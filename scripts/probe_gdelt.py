"""Owner-run probe for #882: one forward week of GDELT GKG 2.1 files.

    uv run python scripts/probe_gdelt.py export-universe     # once, before the week
    uv run python scripts/probe_gdelt.py poll                # every 15 minutes, 7 days
    uv run python scripts/probe_gdelt.py match               # offline, any time
    uv run python scripts/probe_gdelt.py report [--labels FILE]

Measures the three things the owner's gate (#882, 2026-10-05) asks for, plus
universe coverage, against the thresholds pre-registered in
docs/research/2026-10-05-gdelt-probe-protocol.md (the `THRESHOLDS` block below
mirrors that table and must not change once the week starts):

- `poll` fetches the newest GKG 2.1 15-minute file named in GDELT's public
  `lastupdate.txt`, plus any earlier 15-minute file it missed (laptop asleep),
  by its deterministic name. One request per file, a pause between files,
  backoff on errors, never a second fetch of a file it holds. Raw zips are
  kept as fetched under `<root>/raw/`; `known_at` (our fetch time, UTC) and
  the listed size and MD5 go to `<root>/manifest.jsonl`.
- `match` applies ONE fixed rule (`RULE_VERSION`) to the page title (the
  `<PAGE_TITLE>` in V2EXTRASXML) and the URL only, never GDELT's machine-
  extracted organisation or actor fields (ADR 0008; event-data spec req 2 (d)),
  against a frozen universe CSV (columns `ticker,name`, optional `cik`,
  `company_rank`).
- `report` prints and writes coverage, headline share, cache bytes per day and
  projected per year, file completeness and lag, a seeded random sample of
  matches for hand labelling, and, given the labelled sample, the precision
  and the PASS/FAIL verdict.

Nothing touches the store except `export-universe`, which the owner runs once
(one short read-only connection). Spike code (#882), never merged.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import html
import io
import json
import math
import random
import re
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DEFAULT_ROOT = Path.home() / "tradepartner-probes" / "882-gdelt"
LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
FILE_URL = "https://data.gdeltproject.org/gdeltv2/{label}.gkg.csv.zip"
NEW_YORK = ZoneInfo("America/New_York")
INTERVAL = timedelta(minutes=15)
FILES_PER_DAY = 96
USER_AGENT = "tradepartner-probe-882 (personal research; one request per file)"
PAUSE_SECONDS = 1.0  # between file requests
RETRIES = 3  # attempts per request on network errors or HTTP 5xx/429
BACKOFF_SECONDS = 5.0  # first backoff; doubles per attempt
CATCHUP_FILES = 192  # look back at most 48 hours for missed files
MAX_ATTEMPTS = 3  # polls that may try one missed label before it counts as missing
TIMEOUT_SECONDS = 120.0
GKG_COLUMNS = 27
COL_SOURCE_NAME, COL_URL, COL_EXTRAS = 3, 4, 26
RULE_VERSION = "name-title-url+ticker-tag/v1"
SAMPLE_SIZE = 200
SAMPLE_SEED = 882
PROBE_DAYS = 7

# Pre-registered thresholds (protocol doc, "Thresholds"). Frozen for the week.
THRESHOLDS: dict[str, float] = {
    "headline_share_min": 0.90,  # rows with a non-empty PAGE_TITLE, whole week
    "headline_share_day_min": 0.80,  # the same, worst ET day
    "precision_correct_min": 170,  # correct labels out of SAMPLE_SIZE (85%)
    "coverage_week_min": 0.50,  # companies with >= 1 match in the week
    "coverage_day_mean_min": 0.15,  # mean over ET days of the daily share
    "matched_cache_gb_year_max": 5.0,  # matched-rows-only raw form, projected
    "completeness_min": 0.90,  # validity: files held / files expected
}

_LABEL = re.compile(r"^\d{14}$")
_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_SLASH_SEGMENT = re.compile(r"/[A-Za-z]{1,5}/")
_APOSTROPHES = re.compile("['`\u2018\u2019]")
_WORD = re.compile(r"[A-Za-z0-9]+")
_TICKER = r"([A-Z]{1,5}(?:[.\-/][A-Z]{1,2})?)"
_CASHTAG = re.compile(r"(?<![A-Za-z0-9$])\$" + _TICKER + r"(?![A-Za-z0-9])")
_EXCHANGE_TAG = re.compile(
    r"\b(?:NYSE(?:\s+American|\s+Arca|\s+MKT)?|NASDAQ|Nasdaq|AMEX|Cboe|CBOE|BATS)"
    r"\s*:\s*" + _TICKER + r"(?![A-Za-z0-9])"
)
LEGAL_SUFFIXES = frozenset(
    [
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "cos",
        "companies",
        "ltd",
        "limited",
        "plc",
        "llc",
        "lp",
        "llp",
        "sa",
        "nv",
        "ag",
        "se",
        "holdings",
        "holding",
        "group",
        "trust",
        "the",
    ]
)


# --------------------------------------------------------------------------- time


def utc_now() -> datetime:
    """The current instant, tz-aware UTC."""
    return datetime.now(UTC)


def label_time(label: str) -> datetime:
    """A GDELT batch label (YYYYMMDDHHMMSS) as tz-aware UTC."""
    if not _LABEL.match(label):
        raise ValueError(f"not a GDELT batch label: {label!r}")
    return datetime.strptime(label, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def label_of(t: datetime) -> str:
    """The batch label of a tz-aware instant."""
    return t.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def et_day(t: datetime) -> date:
    """The America/New_York calendar date of a tz-aware instant."""
    return t.astimezone(NEW_YORK).date()


# --------------------------------------------------------------------------- poll


@dataclass(frozen=True)
class Listed:
    """One GKG entry of lastupdate.txt."""

    label: str
    size: int
    md5: str
    url: str


def parse_lastupdate(text: str) -> Listed | None:
    """The GKG 2.1 entry of a lastupdate.txt body, or None if absent."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[2].endswith(".gkg.csv.zip"):
            continue
        label = parts[2].rsplit("/", 1)[-1].split(".", 1)[0]
        if _LABEL.match(label):
            return Listed(label, int(parts[0]), parts[1].lower(), parts[2])
    return None


def catchup_labels(
    latest: str,
    held: set[str],
    attempts: Mapping[str, int] | None = None,
    *,
    limit: int = CATCHUP_FILES,
) -> list[str]:
    """Labels on the 15-minute grid before `latest`, not held, oldest first.
    Looks back at most `limit` files and never before the oldest held file (the
    first poll fetches only the newest file). A label already tried
    `MAX_ATTEMPTS` times without success is not tried again."""
    if not held:
        return []
    attempts = attempts or {}
    end = label_time(latest)
    floor = max(end - limit * INTERVAL, min(label_time(h) for h in held))
    out = []
    t = end - INTERVAL
    while t >= floor:
        lab = label_of(t)
        if lab not in held and attempts.get(lab, 0) < MAX_ATTEMPTS:
            out.append(lab)
        t -= INTERVAL
    return sorted(out)


class Fetcher:
    """Polite HTTP GET: one request at a time, a pause between requests,
    exponential backoff on network errors, HTTP 429 and 5xx."""

    def __init__(
        self,
        get: Callable[[str], tuple[int, bytes, Mapping[str, str]]],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._get = get
        self._sleep = sleep
        self._last = 0.0

    def fetch(self, url: str) -> tuple[int, bytes, Mapping[str, str]]:
        """(status, body, headers). Retries transient failures; a 404 is returned
        at once. Raises the last error after `RETRIES` attempts."""
        wait = BACKOFF_SECONDS
        error: Exception | None = None
        for attempt in range(RETRIES):
            gap = PAUSE_SECONDS - (time.monotonic() - self._last)
            if gap > 0:
                self._sleep(gap)
            self._last = time.monotonic()
            try:
                status, body, headers = self._get(url)
            except OSError as exc:  # httpx errors are re-raised as OSError
                error = exc
            else:
                if status == 429 or status >= 500:
                    error = OSError(f"HTTP {status} for {url}")
                else:
                    return status, body, headers
            if attempt < RETRIES - 1:
                self._sleep(wait)
                wait *= 2
        assert error is not None
        raise error


def httpx_get(url: str) -> tuple[int, bytes, Mapping[str, str]]:
    """GET with httpx (redirects followed), errors surfaced as OSError."""
    import httpx

    try:
        r = httpx.get(
            url,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.HTTPError as exc:
        raise OSError(str(exc)) from exc
    return r.status_code, r.content, dict(r.headers)


def read_manifest(root: Path) -> list[dict[str, Any]]:
    """Every manifest record, in write order."""
    path = root / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def held_labels(root: Path) -> set[str]:
    """Labels whose raw zip is on disk and recorded `ok` in the manifest."""
    return {
        r["label"]
        for r in read_manifest(root)
        if r["status"] == "ok" and (root / "raw" / r["file"]).exists()
    }


def _append(root: Path, record: Mapping[str, Any]) -> None:
    with (root / "manifest.jsonl").open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def fetch_file(
    root: Path,
    fetcher: Fetcher,
    label: str,
    *,
    via: str,
    listed: Listed | None = None,
    listed_last_modified: str | None = None,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """Fetch one GKG zip, store it atomically under raw/, append its manifest record."""
    name = f"{label}.gkg.csv.zip"
    url = listed.url.replace("http://", "https://", 1) if listed else FILE_URL.format(label=label)
    started = now()
    record: dict[str, Any] = {
        "label": label,
        "file": name,
        "url": url,
        "via": via,
        "fetch_started": started.isoformat(),
        "listed_size": listed.size if listed else None,
        "listed_md5": listed.md5 if listed else None,
        "listed_last_modified": listed_last_modified,
    }
    try:
        status, body, _headers = fetcher.fetch(url)
    except OSError as exc:
        record.update(status="error", known_at=now().isoformat(), error=str(exc)[:300])
        _append(root, record)
        return record
    known_at = now()
    record["known_at"] = known_at.isoformat()
    record["http_status"] = status
    if status == 404:
        record["status"] = "missing"
    elif status != 200:
        record["status"] = "error"
    else:
        md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
        ok = listed is None or (md5 == listed.md5 and len(body) == listed.size)
        record.update(
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            md5=md5,
            status="ok" if ok else "error",
        )
        if ok:
            raw = root / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            part = raw / (name + ".part")
            part.write_bytes(body)
            part.replace(raw / name)
        else:
            record["error"] = "size or MD5 differs from lastupdate.txt"
    _append(root, record)
    return record


def poll(
    root: Path,
    fetcher: Fetcher,
    *,
    catchup: int = CATCHUP_FILES,
    now: Callable[[], datetime] = utc_now,
) -> list[dict[str, Any]]:
    """One poll: the newest listed file, then missed earlier ones. Idempotent."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "poll.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another poll is running; skipping this one")
            return []
        status, body, headers = fetcher.fetch(LASTUPDATE_URL)
        if status != 200:
            raise OSError(f"lastupdate.txt: HTTP {status}")
        listed = parse_lastupdate(body.decode("utf-8", "replace"))
        if listed is None:
            raise ValueError("lastupdate.txt carries no GKG entry")
        held = held_labels(root)
        done: list[dict[str, Any]] = []
        if listed.label not in held:
            done.append(
                fetch_file(
                    root,
                    fetcher,
                    listed.label,
                    via="lastupdate",
                    listed=listed,
                    listed_last_modified=headers.get("last-modified"),
                    now=now,
                )
            )
        if catchup > 0:
            held = held_labels(root)
            tried: dict[str, int] = defaultdict(int)
            for r in read_manifest(root):
                if r["status"] != "ok":
                    tried[r["label"]] += 1
            for lab in catchup_labels(listed.label, held, tried, limit=catchup):
                done.append(fetch_file(root, fetcher, lab, via="catchup", now=now))
        return done


# --------------------------------------------------------------------------- match


def _tokens(text: str) -> list[str]:
    return _WORD.findall(_APOSTROPHES.sub("", text))


def name_core(name: str) -> tuple[str, ...]:
    """The fixed rule's name key: lowercase word tokens of the registrant name
    with `/XX/` state tags removed, apostrophes dropped, and legal or structural
    suffixes (`LEGAL_SUFFIXES`) stripped from the end (and a leading "the").
    Empty, or shorter than 3 characters in all, means no name match for it."""
    toks = [t.lower() for t in _tokens(_SLASH_SEGMENT.sub(" ", f" {name} "))]
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    if toks and toks[0] == "the":
        toks.pop(0)
    return tuple(toks) if sum(len(t) for t in toks) >= 3 else ()


def norm_ticker(ticker: str) -> str:
    """Class separators unified to '.' (BRK-B, BRK/B, BRK.B -> BRK.B)."""
    return re.sub(r"[\-/]", ".", ticker.strip().upper())


@dataclass(frozen=True)
class Company:
    """One universe company (share classes of one CIK collapse to one)."""

    key: str  # cik when given, else the normalised name
    tickers: tuple[str, ...]
    name: str
    core: tuple[str, ...]


def load_universe(path: Path) -> list[Company]:
    """Companies from a universe CSV (`ticker,name`, optional `cik`)."""
    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("ticker") or not row.get("name"):
                continue
            key = (
                (row.get("cik") or "").strip() or " ".join(name_core(row["name"])) or row["ticker"]
            )
            by_key[key].append(row)
    out = []
    for key, rows in sorted(by_key.items()):
        tickers = tuple(sorted({norm_ticker(r["ticker"]) for r in rows}))
        out.append(Company(key, tickers, rows[0]["name"].strip(), name_core(rows[0]["name"])))
    return out


@dataclass(frozen=True)
class Matcher:
    """The frozen rule over a universe."""

    by_first: Mapping[str, Sequence[tuple[tuple[str, ...], Company]]]
    by_ticker: Mapping[str, Company]

    @classmethod
    def build(cls, companies: Iterable[Company]) -> Matcher:
        """Index name cores by first token and tickers by symbol."""
        by_first: dict[str, list[tuple[tuple[str, ...], Company]]] = defaultdict(list)
        by_ticker: dict[str, Company] = {}
        for c in companies:
            if c.core:
                by_first[c.core[0]].append((c.core, c))
            for t in c.tickers:
                by_ticker[t] = c
        for v in by_first.values():
            v.sort(key=lambda pair: -len(pair[0]))  # longest name first
        return cls(by_first, by_ticker)

    def _names(
        self, toks: Sequence[str], orig: Sequence[str] | None
    ) -> dict[str, tuple[Company, str]]:
        found: dict[str, tuple[Company, str]] = {}
        low = [t.lower() for t in toks]
        for i, tok in enumerate(low):
            for core, comp in self.by_first.get(tok, ()):
                if tuple(low[i : i + len(core)]) != core:
                    continue
                # single-word names in a title must be capitalised (proper noun)
                if orig is not None and len(core) == 1 and not orig[i][:1].isupper():
                    continue
                found.setdefault(comp.key, (comp, " ".join(core)))
        return found

    def match(self, title: str, url: str) -> list[tuple[Company, str, str]]:
        """(company, rule, matched text) per company the rule links to this row.
        Rules, in order: `title_ticker` (cashtag or exchange tag in the title),
        `title_name` (the name key as consecutive title words), `url_name` (the
        name key as consecutive words of the URL path, case ignored)."""
        out: dict[str, tuple[Company, str, str]] = {}
        for rx in (_CASHTAG, _EXCHANGE_TAG):
            for m in rx.finditer(title):
                comp = self.by_ticker.get(norm_ticker(m.group(1)))
                if comp:
                    out.setdefault(comp.key, (comp, "title_ticker", m.group(0)))
        title_toks = _tokens(title)
        for key, (comp, text) in self._names(title_toks, title_toks).items():
            out.setdefault(key, (comp, "title_name", text))
        path = urlsplit(url).path
        for key, (comp, text) in self._names(_tokens(path), None).items():
            out.setdefault(key, (comp, "url_name", text))
        return list(out.values())


def page_title(extras: str) -> str:
    """The `<PAGE_TITLE>` of a V2EXTRASXML cell, HTML-unescaped, or ''."""
    m = _TITLE.search(extras)
    return html.unescape(m.group(1)).strip() if m else ""


def iter_rows(zip_path: Path) -> Iterator[list[str]]:
    """Tab-separated GKG rows of a zip (one CSV member)."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            with zf.open(member) as fh:
                for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="\n"):
                    line = raw.rstrip("\r\n")
                    if line:
                        yield line.split("\t")


def file_hash(path: Path) -> str:
    """SHA-256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


MATCH_FIELDS = [
    "label", "known_at", "et_date", "company_key", "tickers", "name",
    "rule", "matched", "source", "title", "url",
]  # fmt: skip
FILE_FIELDS = [
    "label",
    "known_at",
    "et_date",
    "via",
    "bytes",
    "rows",
    "bad_rows",
    "titled",
    "matched_rows",
]


def match(root: Path, universe: Path) -> dict[str, Any]:
    """Re-derive matches.csv, files.csv and matched_rows.tsv.gz under derived/
    from every held raw zip. Deterministic and rerunnable; network not used."""
    companies = load_universe(universe)
    if not companies:
        raise ValueError(f"no companies in {universe}")
    matcher = Matcher.build(companies)
    records = {r["label"]: r for r in read_manifest(root) if r["status"] == "ok"}
    out = root / "derived"
    out.mkdir(parents=True, exist_ok=True)
    n_matches = 0
    with (
        (out / "matches.csv").open("w", newline="") as mfh,
        (out / "files.csv").open("w", newline="") as ffh,
        gzip.open(out / "matched_rows.tsv.gz", "wt", encoding="utf-8") as gz,
    ):
        mw = csv.DictWriter(mfh, MATCH_FIELDS)
        fw = csv.DictWriter(ffh, FILE_FIELDS)
        mw.writeheader()
        fw.writeheader()
        for label in sorted(records):
            rec = records[label]
            path = root / "raw" / rec["file"]
            if not path.exists():
                continue
            known_at = datetime.fromisoformat(rec["known_at"])
            day = et_day(known_at).isoformat()
            rows = bad = titled = matched_rows = 0
            for cols in iter_rows(path):
                rows += 1
                if len(cols) != GKG_COLUMNS:
                    bad += 1
                    continue
                title = page_title(cols[COL_EXTRAS])
                titled += bool(title)
                hits = matcher.match(title, cols[COL_URL])
                if hits:
                    matched_rows += 1
                    gz.write("\t".join(cols) + "\n")
                for comp, rule, text in hits:
                    n_matches += 1
                    mw.writerow(
                        {
                            "label": label,
                            "known_at": rec["known_at"],
                            "et_date": day,
                            "company_key": comp.key,
                            "tickers": " ".join(comp.tickers),
                            "name": comp.name,
                            "rule": rule,
                            "matched": text,
                            "source": cols[COL_SOURCE_NAME],
                            "title": title,
                            "url": cols[COL_URL],
                        }
                    )
            fw.writerow(
                {
                    "label": label,
                    "known_at": rec["known_at"],
                    "et_date": day,
                    "via": rec["via"],
                    "bytes": rec["bytes"],
                    "rows": rows,
                    "bad_rows": bad,
                    "titled": titled,
                    "matched_rows": matched_rows,
                }
            )
    meta = {
        "rule_version": RULE_VERSION,
        "universe": str(universe),
        "universe_sha256": file_hash(universe),
        "companies": len(companies),
        "companies_with_name_key": sum(1 for c in companies if c.core),
        "files": len(records),
        "matches": n_matches,
        "matched_at": utc_now().isoformat(),
    }
    (out / "match_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


# --------------------------------------------------------------------------- report


def wilson_lower(k: int, n: int, z: float = 1.959964) -> float:
    """Lower bound of the Wilson score interval (95% by default)."""
    if n == 0:
        return 0.0
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def probe_days(file_rows: Sequence[Mapping[str, str]], days: int = PROBE_DAYS) -> list[date]:
    """The first `days` complete ET days: from the day after the first file's
    ET day (the start day is partial), capped at the last day with files."""
    held = sorted({date.fromisoformat(r["et_date"]) for r in file_rows})
    if not held:
        return []
    first = held[0] + timedelta(days=1)
    return [d for d in (first + timedelta(days=i) for i in range(days)) if d <= held[-1]]


def expected_labels(day: date) -> list[str]:
    """Every 15-minute batch label whose instant falls on this ET day."""
    start = datetime(day.year, day.month, day.day, tzinfo=NEW_YORK).astimezone(UTC)
    end = (datetime(day.year, day.month, day.day, tzinfo=NEW_YORK) + timedelta(days=1)).astimezone(
        UTC
    )
    t = start + (-start.minute % 15) * timedelta(minutes=1)
    out = []
    while t < end:
        out.append(label_of(t))
        t += INTERVAL
    return out


def sample_matches(
    matches: Sequence[Mapping[str, str]], n: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED
) -> list[dict[str, str]]:
    """A seeded simple random sample of distinct (url, company) matches,
    taken from the list sorted by (url, company_key) so it is reproducible."""
    unique: dict[tuple[str, str], Mapping[str, str]] = {}
    for m in matches:
        unique.setdefault((m["url"], m["company_key"]), m)
    pool = [unique[k] for k in sorted(unique)]
    picked = random.Random(seed).sample(pool, min(n, len(pool)))
    return [
        {
            "sample_id": str(i + 1),
            "et_date": m["et_date"],
            "tickers": m["tickers"],
            "name": m["name"],
            "rule": m["rule"],
            "matched": m["matched"],
            "title": m["title"],
            "url": m["url"],
            "correct": "",
        }
        for i, m in enumerate(picked)
    ]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def summarise(
    file_rows: Sequence[Mapping[str, str]],
    match_rows: Sequence[Mapping[str, str]],
    manifest: Sequence[Mapping[str, Any]],
    n_companies: int,
    matched_rows_bytes: int,
    labels: Sequence[Mapping[str, str]] | None,
) -> dict[str, Any]:
    """Every probe measure over the probe window, with the threshold verdicts."""
    days = probe_days(file_rows)
    in_window = {d.isoformat() for d in days}
    files = [r for r in file_rows if r["et_date"] in in_window]
    expected = {lab for d in days for lab in expected_labels(d)}
    held = {r["label"] for r in files} & expected
    per_day: dict[str, dict[str, Any]] = {}
    for d in sorted(in_window):
        fs = [r for r in files if r["et_date"] == d]
        rows = sum(int(r["rows"]) - int(r["bad_rows"]) for r in fs)
        titled = sum(int(r["titled"]) for r in fs)
        comps = {m["company_key"] for m in match_rows if m["et_date"] == d}
        per_day[d] = {
            "files": len(fs),
            "rows": rows,
            "titled_share": titled / rows if rows else 0.0,
            "matches": sum(1 for m in match_rows if m["et_date"] == d),
            "companies": len(comps),
            "coverage": len(comps) / n_companies if n_companies else 0.0,
            "raw_bytes": sum(int(r["bytes"]) for r in fs),
        }
    n_days = len(per_day)
    rows_all = sum(v["rows"] for v in per_day.values())
    titled_all = sum(int(r["titled"]) for r in files)
    week_companies = {m["company_key"] for m in match_rows if m["et_date"] in in_window}
    # bytes per held file times 96 files a day, so missed files do not shrink the projection
    raw_day = FILES_PER_DAY * sum(int(r["bytes"]) for r in files) / len(files) if files else 0.0
    matched_day = FILES_PER_DAY * matched_rows_bytes / len(file_rows) if file_rows else 0.0
    lags = [
        (datetime.fromisoformat(r["known_at"]) - label_time(r["label"])).total_seconds() / 60
        for r in manifest
        if r.get("status") == "ok" and r.get("via") == "lastupdate"
    ]
    posted = [
        (
            datetime.strptime(r["listed_last_modified"], "%a, %d %b %Y %H:%M:%S %Z").replace(
                tzinfo=UTC
            )
            - label_time(r["label"])
        ).total_seconds()
        / 60
        for r in manifest
        if r.get("status") == "ok" and r.get("listed_last_modified")
    ]
    gb = 1e9
    s: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "window": [days[0].isoformat(), days[-1].isoformat()] if days else [],
        "days": n_days,
        "files_expected": len(expected),
        "files_held": len(held),
        "completeness": len(held) / len(expected) if expected else 0.0,
        "missing_or_error_records": sum(1 for r in manifest if r.get("status") != "ok"),
        "rows": rows_all,
        "headline_share": titled_all / rows_all if rows_all else 0.0,
        "headline_share_worst_day": min((v["titled_share"] for v in per_day.values()), default=0.0),
        "companies": n_companies,
        "coverage_week": len(week_companies) / n_companies if n_companies else 0.0,
        "coverage_day_mean": statistics.mean(v["coverage"] for v in per_day.values())
        if per_day
        else 0.0,
        "raw_bytes_per_day": raw_day,
        "raw_gb_per_year": raw_day * 365 / gb,
        "matched_bytes_per_day": matched_day,
        "matched_gb_per_year": matched_day * 365 / gb,
        "lag_fetch_minus_label_min": _quantiles(lags),
        "lag_posted_minus_label_min": _quantiles(posted),
        "per_day": per_day,
    }
    if labels is not None:
        filled = [r for r in labels if r.get("correct", "").strip()]
        correct = sum(1 for r in filled if r["correct"].strip() == "1")
        s.update(
            labelled=len(filled),
            correct=correct,
            precision=correct / len(filled) if filled else 0.0,
            precision_wilson_lower=wilson_lower(correct, len(filled)),
            precision_by_rule=_by_rule(filled),
        )
    s["verdicts"] = verdicts(s)
    return s


def _by_rule(filled: Sequence[Mapping[str, str]]) -> dict[str, str]:
    out = {}
    for rule in sorted({r["rule"] for r in filled}):
        mine = [r for r in filled if r["rule"] == rule]
        out[rule] = f"{sum(1 for r in mine if r['correct'].strip() == '1')}/{len(mine)}"
    return out


def _quantiles(xs: Sequence[float]) -> dict[str, float] | None:
    if not xs:
        return None
    ys = sorted(xs)
    return {
        "n": len(ys),
        "min": ys[0],
        "median": statistics.median(ys),
        "p95": ys[min(len(ys) - 1, math.ceil(0.95 * len(ys)) - 1)],
        "max": ys[-1],
    }


def verdicts(s: Mapping[str, Any]) -> dict[str, str]:
    """PASS / FAIL / PENDING per pre-registered threshold, plus run validity."""
    th = THRESHOLDS
    v = {
        "validity (completeness, 7 days)": _pf(
            s["completeness"] >= th["completeness_min"] and s["days"] >= PROBE_DAYS
        ),
        "T1 headline share": _pf(
            s["headline_share"] >= th["headline_share_min"]
            and s["headline_share_worst_day"] >= th["headline_share_day_min"]
        ),
        "T3 coverage": _pf(
            s["coverage_week"] >= th["coverage_week_min"]
            and s["coverage_day_mean"] >= th["coverage_day_mean_min"]
        ),
        "T4 cache (matched-rows form)": _pf(
            s["matched_gb_per_year"] <= th["matched_cache_gb_year_max"]
        ),
    }
    if "correct" not in s:
        v["T2 precision"] = "PENDING (label the sample, rerun with --labels)"
    elif s["labelled"] < SAMPLE_SIZE:
        v["T2 precision"] = f"PENDING ({s['labelled']}/{SAMPLE_SIZE} labelled)"
    else:
        v["T2 precision"] = _pf(s["correct"] >= th["precision_correct_min"])
    return dict(sorted(v.items()))


def _pf(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def render(s: Mapping[str, Any]) -> str:
    """The summary as Markdown, to paste on #882."""
    pct = "{:.1%}".format
    th, v = THRESHOLDS, s["verdicts"]
    if "correct" in s:
        t2 = (
            f"{s['correct']}/{s['labelled']} ({pct(s['precision'])}, Wilson lower "
            f"{pct(s['precision_wilson_lower'])}); by rule {s['precision_by_rule']}"
        )
    else:
        t2 = "not labelled yet"
    window = " to ".join(s["window"]) or "n/a"
    files = f"{s['files_held']}/{s['files_expected']} ({pct(s['completeness'])})"
    t1 = f"{pct(s['headline_share'])} / {pct(s['headline_share_worst_day'])}"
    t1_th = f">= {pct(th['headline_share_min'])} / >= {pct(th['headline_share_day_min'])}"
    t3 = f"{pct(s['coverage_week'])} / {pct(s['coverage_day_mean'])} of {s['companies']}"
    t3_th = f">= {pct(th['coverage_week_min'])} / >= {pct(th['coverage_day_mean_min'])}"
    t4 = f"{s['matched_bytes_per_day'] / 1e6:.1f} MB/day, {s['matched_gb_per_year']:.2f} GB/yr"
    raw = f"{s['raw_bytes_per_day'] / 1e6:.1f} MB/day, {s['raw_gb_per_year']:.1f} GB/yr"
    lines = [
        f"## GDELT probe (#882), {RULE_VERSION}",
        "",
        f"Window (ET): {window} ({s['days']} days); files {files}; "
        f"non-ok manifest records {s['missing_or_error_records']}",
        "",
        "| Measure | Value | Threshold | Verdict |",
        "|---|---|---|---|",
        f"| T1 headline share (week / worst day) | {t1} | {t1_th} | {v['T1 headline share']} |",
        f"| T2 precision | {t2} | >= {int(th['precision_correct_min'])}/{SAMPLE_SIZE} "
        f"| {v['T2 precision']} |",
        f"| T3 coverage (week / mean day) | {t3} | {t3_th} | {v['T3 coverage']} |",
        f"| T4 cache, matched rows gz | {t4} | <= {th['matched_cache_gb_year_max']} GB/yr "
        f"| {v['T4 cache (matched-rows form)']} |",
        f"| (info) whole GKG zips | {raw} | none | info |",
        f"| validity | {s['days']} days, {pct(s['completeness'])} of files "
        f"| 7 days, >= {pct(th['completeness_min'])} | {v['validity (completeness, 7 days)']} |",
        "",
        f"Rows in window: {s['rows']:,}.",
        f"Lag fetch minus label (min): {s['lag_fetch_minus_label_min']}.",
        f"Lag posted (Last-Modified) minus label (min): {s['lag_posted_minus_label_min']}.",
        "",
        "| ET day | files | rows | titled | matches | companies | coverage | raw MB |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d, x in s["per_day"].items():
        lines.append(
            f"| {d} | {x['files']} | {x['rows']:,} | {pct(x['titled_share'])} | {x['matches']:,} | "
            f"{x['companies']} | {pct(x['coverage'])} | {x['raw_bytes'] / 1e6:.1f} |"
        )
    return "\n".join(lines) + "\n"


def report(root: Path, labels: Path | None) -> dict[str, Any]:
    """Summarise derived/ into report.md and report.json; write the sample CSV once."""
    out = root / "derived"
    meta = json.loads((out / "match_meta.json").read_text())
    file_rows = _read_csv(out / "files.csv")
    match_rows = _read_csv(out / "matches.csv")
    window = {d.isoformat() for d in probe_days(file_rows)}
    sample_path = root / "precision_sample.csv"
    if not sample_path.exists():
        sample = sample_matches([m for m in match_rows if m["et_date"] in window])
        with sample_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, list(sample[0]) if sample else ["sample_id"])
            w.writeheader()
            w.writerows(sample)
    s = summarise(
        file_rows,
        match_rows,
        read_manifest(root),
        int(meta["companies"]),
        (out / "matched_rows.tsv.gz").stat().st_size,
        _read_csv(labels) if labels else None,
    )
    s["match_meta"] = meta
    (root / "report.json").write_text(json.dumps(s, indent=2, default=str) + "\n")
    (root / "report.md").write_text(render(s))
    return s


# --------------------------------------------------------------------------- universe


def universe_rows(
    conn: Any, t: datetime, top_n: int = 1000, settings: Any = None
) -> list[dict[str, Any]]:
    """Universe members at `t` with company rank <= top_n, and their registrant
    names as known at `t`, sorted by rank then ticker."""
    from tradepartner.store.master import securities_as_of
    from tradepartner.universe import universe_as_of

    u = universe_as_of(conn, t, settings)
    names = {r["security_id"]: r["name"] for r in securities_as_of(conn, t).iter_rows(named=True)}
    rows = [
        {
            "ticker": m["ticker"],
            "name": names[m["security_id"]],
            "cik": m["cik"],
            "company_rank": m["company_rank"],
        }
        for m in u.members.iter_rows(named=True)
        if m["company_rank"] <= top_n
    ]
    return sorted(rows, key=lambda r: (r["company_rank"], r["ticker"]))


def export_universe(out: Path, top_n: int) -> int:
    """Owner-run: one short read-only connection to the configured store."""
    from tradepartner.config import get_settings
    from tradepartner.store.db import open_read_only

    with open_read_only(get_settings()) as conn:
        rows = universe_rows(conn, utc_now(), top_n)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["ticker", "name", "cik", "company_rank"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------- cli


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = p.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export-universe", help="owner: freeze the top-N universe to a CSV")
    ex.add_argument("--top-n", type=int, default=1000)
    po = sub.add_parser("poll", help="fetch the newest GKG file and any missed ones")
    po.add_argument(
        "--catchup", type=int, default=CATCHUP_FILES, help="files to look back (0: none)"
    )
    ma = sub.add_parser("match", help="apply the frozen rule to every held file")
    ma.add_argument("--universe", type=Path, default=None, help="default: <root>/universe.csv")
    rp = sub.add_parser("report", help="summary, precision sample, verdicts")
    rp.add_argument("--labels", type=Path, default=None, help="the labelled precision_sample CSV")
    args = p.parse_args(argv)
    root: Path = args.root
    if args.cmd == "export-universe":
        n = export_universe(root / "universe.csv", args.top_n)
        print(f"wrote {n} rows to {root / 'universe.csv'}")
    elif args.cmd == "poll":
        for r in poll(root, Fetcher(httpx_get), catchup=args.catchup):
            print(f"{r['label']} {r['via']:<10} {r['status']:<7} {r.get('bytes', '')}")
    elif args.cmd == "match":
        print(json.dumps(match(root, args.universe or root / "universe.csv"), indent=2))
    else:
        print(render(report(root, args.labels)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

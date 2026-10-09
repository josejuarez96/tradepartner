"""The departure-reason corpus fetch (research-labeling spec req 2 "Fetch" as
amendment 2026-10-06 C11 changes it; plan T120).

**Population.** Every `25`, `25-NSE`, `25/A` and `25-NSE/A` row of the
quarterly `form.idx` filed in `[since, until]`. Rows are de-duplicated by
accession: the exchange's copy of a 25-NSE, which the index lists under the
exchange's CIK with the same accession, is counted `exchange_copy`. Each
accession's full-submission `.txt` gives the notification fields; one whose
text holds no XML `notificationOfRemoval` is counted `pre_xml`. The issuer CIK
named in the XML gives the submissions JSON, whose `acceptanceDateTime` is the
only acceptance used; a filing it carries no acceptance for is counted
`unstamped` and never dated from the index. An amendment is attached to the
latest earlier-accepted original of the same issuer CIK and exchange (counted
`amendment_attached`); one with no original is its own listing end, flagged
`orphan_amendment`. So listing ends kept + `pre_xml` + `unstamped` +
`exchange_copy` + `amendment_attached` = index rows seen (`counts.json`).

**Per listing end.** The EX-99.25 notice from the `.txt` (`text` at
`EXHIBIT_MIN_WORDS` alphabetic words or more, else `stub`; `none` when the
submission has no document but the notification), cut at a sentence boundary
at `research.labeling.exhibit_max_chars`; from the issuer's submissions JSON,
the marker filings (`MARKER_FORMS`, filed from `MARKER_BEFORE_DAYS` before the
filing date to the earlier of `marker_after_days` after it and the CIK's next
listing end, never downloaded) and the one 8-K of C2 (among `8-K`, `8-K/A` and
`8-K12B` filed in `[filed - context_before_days, filed + context_after_days]`,
the nearest whose index `items` meet `eightk_items`, else the nearest of any),
whose primary document is downloaded and split on its `Item N.NN` headings
(`eightk_items` order, each cut at `item_max_chars`, all at
`eightk_max_chars`), or its body's head when no listed item segments.

**I/O.** Every request goes through `adapters.edgar_raw` (its paced client at
`edgar.requests_per_second`, its User-Agent and retry policy). Raw responses
are cached under `edgar.cache_dir/corpus/departure-reason/raw/`, which
`_fetch_delisting`'s unlink of `cached_filing_path` never touches, and a rerun
requests only what that cache lacks. Two things go stale and are fetched again:
a quarter's index until `INDEX_SETTLE_DAYS` after the quarter ends, and an
issuer's submissions JSON while a window of one of its listing ends was still
open when it was fetched (so a later amendment, 8-K or marker is seen). A
document EDGAR answers with an HTTP error is recorded under the listing end's
`missing` with its status (a Form 25 not served leaves `exhibit` null), and a
`404` or `410` is remembered so a rerun does not ask again. Output: `corpus.jsonl` (one object
per listing end, sorted by filing date and accession) and `counts.json` beside
`raw/`. This module imports nothing from `tradepartner.research`, never opens
the runtime store, and writes nothing in the research store.

The reference implementation is the sample test's `jev_sample.py`
(`fill_form25`, `fill_exhibit`, `fill_eightk`, `segment_items`,
`truncate_sentence`), rewritten here.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import (
    UnstampedFiling,
    acceptance_times,
    normalize_exchange,
    parse_filing_index,
)
from tradepartner.config import Settings, get_settings

#: The population's first filing date (spec Definitions "Listing end").
POPULATION_START = date(2016, 1, 1)
ORIGINAL_FORMS = frozenset({"25", "25-NSE"})
AMENDMENT_FORMS = frozenset({"25/A", "25-NSE/A"})
LISTING_END_FORMS = ORIGINAL_FORMS | AMENDMENT_FORMS
EIGHTK_FORMS = frozenset({"8-K", "8-K/A", "8-K12B"})
MARKER_FORMS = frozenset({"15-12B", "15-12G", "8-A12B", "8-K12B"})
#: Req 2: the marker window opens 30 calendar days before the filing date.
MARKER_BEFORE_DAYS = 30
#: C2: a notice with fewer alphabetic words than this is a `stub`.
EXHIBIT_MIN_WORDS = 20
#: Rule 12d2-2(d)(1): a removal takes effect 10 days after the Form 25 is filed;
#: the notification XML states no effective date.
EFFECTIVE_DAYS = 10
#: A quarter's `form.idx` is cached as final only this many days after the quarter
#: ends, so a run just after quarter end never freezes an index missing its last day.
INDEX_SETTLE_DAYS = 3
#: Submission documents that are never the notice (binary or uuencoded payloads).
_NON_TEXT_TYPES = frozenset({"GRAPHIC", "PDF", "ZIP", "EXCEL", "XML", "JSON"})
_UUENCODED = re.compile(r"(?m)^begin [0-7]{3} \S")
#: Statuses that mean EDGAR does not have the document, remembered across reruns.
_PERMANENT_STATUSES = frozenset({404, 410})
#: `truncate_sentence` keeps a sentence cut only when it keeps at least this share.
_MIN_SENTENCE_SHARE = 0.5
_TRUNCATION_MARK = " [...]"

_DOCUMENT = re.compile(r"<DOCUMENT>\s*<TYPE>([^\n<]+)\n(.*?)</DOCUMENT>", re.S)
_DOCUMENT_TEXT = re.compile(r"<TEXT>(.*?)</TEXT>", re.S)
_NOTIFICATION = re.compile(r"<notificationOfRemoval\b.*?</notificationOfRemoval>", re.S)
_HTML_HINT = re.compile(r"(?i)<(html|p|div|br|table)\b")
_WORD = re.compile(r"[A-Za-z]{2,}")
_BLOCK = re.compile(r"(?is)<(script|style|head)[^>]*>.*?</\1>")
_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h\d|p|div|tr|li|h\d)\b[^>]*>")
_TAG = re.compile(r"(?s)<[^>]+>")
_ITEM_HEADING = re.compile(r"(?im)^\s*item\s*(\d{1,2}\.\d{2})")
_SUBMISSIONS_PAGE = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")


# --- pure extraction -----------------------------------------------------------


def normalise_provision(raw: str) -> str | None:
    """The notification's `ruleProvision` with whitespace and the `17 CFR 240.`
    prefix dropped (`17 CFR 240.12d2-2(a)(2)` -> `12d2-2(a)(2)`); `None` when blank."""
    compact = re.sub(r"\s+", "", raw)
    compact = re.sub(r"^(17CFR)?(240\.)?", "", compact, flags=re.I)
    compact = re.sub(r"(?i)rule", "", compact)
    return compact or None


def html_to_text(raw: str) -> str:
    """Deterministic HTML to text: scripts, styles and heads dropped, block tags
    to line breaks, entities unescaped, whitespace collapsed."""
    text = _BLOCK.sub(" ", raw)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def truncate_sentence(text: str, limit: int) -> str:
    """`text` cut to `limit` characters at the last sentence end inside the
    limit (when that keeps at least half of it, else at `limit`), marked `[...]`."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind(".\n"))
    kept = cut[: end + 1] if end > limit * _MIN_SENTENCE_SHARE else cut
    return kept + _TRUNCATION_MARK


def segment_items(text: str) -> dict[str, str]:
    """An 8-K's text split on its `Item N.NN` headings, item number to segment
    (`01.01` read as `1.01`); a repeated heading keeps the longest segment, so a
    table-of-contents hit does not win."""
    headings = list(_ITEM_HEADING.finditer(text))
    segments: dict[str, str] = {}
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        number = match.group(1).removeprefix("0")
        segment = text[match.start() : end].strip()
        if len(segment) > len(segments.get(number, "")):
            segments[number] = segment
    return segments


@dataclass(frozen=True)
class Notice:
    """The Form 25's notice exhibit: `status` is `text`, `stub` or `none`;
    `text` is set only for `text`."""

    status: str
    document_type: str | None
    text: str | None


def notice_exhibit(submission_text: str, *, max_chars: int) -> Notice:
    """The longest text document of a full-submission text other than the
    notification itself (the EX-99.25 notice; images, PDFs and uuencoded payloads
    are skipped), classified at `EXHIBIT_MIN_WORDS` and cut at `max_chars`."""
    best_type, best_text = "", ""
    for doc_type, body in _DOCUMENT.findall(submission_text):
        doc_type = doc_type.strip()
        if doc_type.upper().startswith("25"):
            continue  # the notification itself
        found = _DOCUMENT_TEXT.search(body)
        raw = found.group(1) if found else body
        if doc_type.upper() in _NON_TEXT_TYPES or "<PDF>" in raw or _UUENCODED.search(raw):
            continue  # an image, PDF or other encoded payload is never the notice
        if _HTML_HINT.search(raw):
            text = html_to_text(raw)
        else:
            text = re.sub(r"[ \t]+", " ", html.unescape(raw)).strip()
        if not best_type or len(text) > len(best_text):
            best_type, best_text = doc_type, text
    if not best_type:
        return Notice("none", None, None)
    if len(_WORD.findall(best_text)) < EXHIBIT_MIN_WORDS:
        return Notice("stub", best_type, None)
    return Notice("text", best_type, truncate_sentence(best_text, max_chars))


@dataclass(frozen=True)
class Notification:
    """The labelled fields of a `notificationOfRemoval`; any may be absent."""

    issuer_cik: str | None
    issuer: str | None
    exchange_name: str | None
    class_title: str | None
    rule_provision_raw: str | None
    signature_date: str | None


def _tag(xml: str, path: Sequence[str]) -> str | None:
    segment = xml
    for name in path:
        found = re.search(rf"<{name}>(.*?)</{name}>", segment, re.S)
        if found is None:
            return None
        segment = found.group(1)
    value = html.unescape(re.sub(r"\s+", " ", segment)).strip()
    return value or None


def parse_notification(submission_text: str) -> Notification | None:
    """The notification fields of a full-submission text, or `None` when it
    holds no XML `notificationOfRemoval` (a pre-XML filing)."""
    found = _NOTIFICATION.search(submission_text)
    if found is None:
        return None
    xml = found.group(0)
    cik = _tag(xml, ["issuer", "cik"])
    return Notification(
        issuer_cik=f"{int(cik):010d}" if cik and cik.isdigit() else None,
        issuer=_tag(xml, ["issuer", "entityName"]),
        exchange_name=_tag(xml, ["exchange", "entityName"]),
        class_title=_tag(xml, ["descriptionClassSecurity"]),
        rule_provision_raw=_tag(xml, ["ruleProvision"]),
        signature_date=_tag(xml, ["signatureData", "signatureDate"]),
    )


@dataclass(frozen=True)
class EightKPassage:
    """An 8-K's listed items in configured order, or its body's head when none
    of them segments."""

    items: dict[str, str]
    body_head: str | None


def eightk_passage(
    document: str, items: Sequence[str], *, item_max_chars: int, eightk_max_chars: int
) -> EightKPassage:
    """The items of `items` found in the 8-K primary `document`, in that order,
    each cut at `item_max_chars`, stopping once `eightk_max_chars` is spent; the
    head of the body at `item_max_chars` when none segments."""
    text = html_to_text(document)
    segments = segment_items(text)
    kept: dict[str, str] = {}
    budget = eightk_max_chars
    for item in items:
        if item not in segments:
            continue
        piece = truncate_sentence(segments[item], min(item_max_chars, budget))
        kept[item] = piece
        budget -= len(piece)
        if budget <= 0:
            break
    if kept:
        return EightKPassage(kept, None)
    return EightKPassage({}, truncate_sentence(text, min(item_max_chars, eightk_max_chars)))


# --- cached EDGAR access ---------------------------------------------------------


@dataclass(frozen=True)
class _NotServed:
    """A document EDGAR answered with an HTTP error status."""

    url: str
    status: int

    def missing(self, document: str) -> dict[str, Any]:
        return {"document": document, "url": self.url, "status": self.status}


class _CachedEdgar:
    """`edgar_raw` calls behind a raw-response cache under `raw_dir`."""

    def __init__(
        self, settings: Settings, client: httpx.Client | None, raw_dir: Path, now: datetime
    ) -> None:
        self._settings = settings
        self._now = now
        # `download_filing_file` caches under `edgar.cache_dir`; pointing a copy of the
        # settings at `raw_dir` keeps the corpus's documents out of the ingest cache.
        edgar = settings.edgar.model_copy(update={"cache_dir": str(raw_dir)})
        self._raw_settings = settings.model_copy(update={"edgar": edgar})
        self._client = client
        self._raw_dir = raw_dir

    def index_rows(self, year: int, qtr: int, *, complete: bool) -> list[UnstampedFiling]:
        """The quarter's `form.idx` rows of the four listing-end forms."""
        path = self._raw_dir / "form-idx" / f"{year}-QTR{qtr}.idx"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            full = edgar_raw.filing_index_quarter(
                year, qtr, settings=self._settings, client=self._client
            )
            text = "".join(
                line + "\n"
                for line in full.splitlines()
                if line.split(" ", 1)[0] in LISTING_END_FORMS
            )
            if complete:
                edgar_raw.write_atomic(path, text.encode("utf-8"))
        # No acceptance map: every row comes back as an `UnstampedFiling` (cik, name,
        # form, accession, filed_on); acceptance is read from the submissions JSON.
        return list(parse_filing_index(text, {}).unstamped)

    def document(self, cik: str, accession: str, filename: str) -> bytes | _NotServed:
        """One filing file's bytes, from the cache or EDGAR."""
        path = edgar_raw.cached_filing_path(cik, accession, filename, settings=self._raw_settings)
        url = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession.replace('-', '')}/{filename}"
        )
        remembered = self._remembered(path)
        if remembered is not None:
            return remembered
        try:
            downloaded = edgar_raw.download_filing_file(
                cik, accession, filename, settings=self._raw_settings, client=self._client
            )
        except httpx.HTTPStatusError as error:
            return self._not_served(path, url, error)
        return downloaded.read_bytes()

    def submissions(self, cik: str, *, open_until: date) -> Any:
        """The issuer's submissions payload (`_NotServed` on an HTTP error).

        The cached copy is stored with its fetch time and used only when it was
        fetched after `open_until`, the last day of any window of this issuer's
        listing ends; otherwise it is fetched again, so a filing (a new Form 25,
        amendment, 8-K or marker) made since the last fetch is seen on a rerun.
        """
        padded = f"{int(cik):010d}"
        path = self._raw_dir / "submissions" / f"CIK{padded}.json"
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        if path.is_file():
            cached = json.loads(path.read_bytes())
            if datetime.fromisoformat(cached["fetched_at"]).date() > open_until:
                return cached["payload"]
        remembered = self._remembered(path)
        if remembered is not None:
            return remembered
        try:
            payload = edgar_raw.submissions(cik, settings=self._settings, client=self._client)
        except httpx.HTTPStatusError as error:
            return self._not_served(path, url, error)
        stored = {"fetched_at": self._now.isoformat(), "payload": payload}
        edgar_raw.write_atomic(path, json.dumps(stored).encode("utf-8"))
        return payload

    def submissions_page(self, name: str) -> Any:
        """One older submissions page (`_NotServed` on an HTTP error); a page
        covers a closed range of older filings, so its cached copy is final."""
        if not _SUBMISSIONS_PAGE.fullmatch(name):
            raise edgar_raw.InvalidFilingReferenceError(f"bad submissions page name: {name!r}")
        return self._json(
            self._raw_dir / "submissions" / name,
            f"https://data.sec.gov/submissions/{name}",
            lambda: edgar_raw.submissions_page(name, settings=self._settings, client=self._client),
        )

    def _json(self, path: Path, url: str, fetch: Callable[[], Any]) -> Any:
        if path.is_file():
            return json.loads(path.read_bytes())
        remembered = self._remembered(path)
        if remembered is not None:
            return remembered
        try:
            payload = fetch()
        except httpx.HTTPStatusError as error:
            return self._not_served(path, url, error)
        edgar_raw.write_atomic(path, json.dumps(payload).encode("utf-8"))
        return payload

    @staticmethod
    def _marker(path: Path) -> Path:
        return path.with_name(f"{path.name}.not-served.json")

    def _remembered(self, path: Path) -> _NotServed | None:
        marker = self._marker(path)
        if not marker.is_file():
            return None
        data = json.loads(marker.read_bytes())
        return _NotServed(str(data["url"]), int(data["status"]))

    def _not_served(self, path: Path, url: str, error: httpx.HTTPStatusError) -> _NotServed:
        status = error.response.status_code
        if status in _PERMANENT_STATUSES:
            marker = json.dumps({"url": url, "status": status}).encode("utf-8")
            edgar_raw.write_atomic(self._marker(path), marker)
        return _NotServed(url, status)


# --- the fetch ---------------------------------------------------------------------


@dataclass(frozen=True)
class FetchCounts:
    """Req 2's counts: kept + the four exclusion categories = index rows seen."""

    index_rows_seen: int
    kept: int
    orphan_amendment: int
    pre_xml: int
    unstamped: int
    exchange_copy: int
    amendment_attached: int

    def excluded(self) -> int:
        """Rows that are not listing ends of their own."""
        return self.pre_xml + self.unstamped + self.exchange_copy + self.amendment_attached

    def identity_holds(self) -> bool:
        """Whether every index row seen is accounted for exactly once."""
        return self.kept + self.excluded() == self.index_rows_seen

    def as_json(self) -> dict[str, int | bool]:
        """The `counts.json` object."""
        return {
            "index_rows_seen": self.index_rows_seen,
            "kept": self.kept,
            "orphan_amendment": self.orphan_amendment,
            "pre_xml": self.pre_xml,
            "unstamped": self.unstamped,
            "exchange_copy": self.exchange_copy,
            "amendment_attached": self.amendment_attached,
            "identity_holds": self.identity_holds(),
        }


@dataclass(frozen=True)
class FetchResult:
    """Where the fetch wrote and what it counted."""

    corpus_path: Path
    counts_path: Path
    counts: FetchCounts


@dataclass
class _Filing:
    accession: str
    form: str
    filed_on: date
    issuer_cik: str
    raw: bytes | None
    notification: Notification | None
    missing: list[dict[str, Any]]
    accepted_at: datetime | None = None
    amendments: list[dict[str, str]] = field(default_factory=list)
    orphan_amendment: bool = False

    @property
    def exchange(self) -> str | None:
        name = self.notification.exchange_name if self.notification else None
        return normalize_exchange(name) if name else None


@dataclass(frozen=True)
class _Submissions:
    rows: list[dict[str, str]]
    acceptance: dict[str, datetime]


def corpus_dir(settings: Settings) -> Path:
    """`edgar.cache_dir/corpus/departure-reason/`, where the fetch writes."""
    return Path(settings.edgar.cache_dir).resolve() / "corpus" / "departure-reason"


def fetch_departure_corpus(
    *,
    since: date,
    until: date,
    ciks: Sequence[str] = (),
    limit: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> FetchResult:
    """Fetch the departure-reason corpus for Form 25s filed in `[since, until]`
    and write `corpus.jsonl` and `counts.json` (module docstring).

    `ciks` keeps only accessions with an index row under one of those CIKs;
    `limit` keeps the first `limit` accessions by filing date. Both narrow the
    rows seen, so the counts identity holds over what was kept; `counts.json`
    records the range, both filters and `fetched_at` (`now`, default the
    current UTC time), so a narrowed corpus is never mistaken for the full one.
    """
    settings = settings or get_settings()
    fetched_at = (now or datetime.now(UTC)).astimezone(UTC)
    today = fetched_at.date()
    out_dir = corpus_dir(settings)
    edgar = _CachedEdgar(settings, client, out_dir / "raw", fetched_at)

    groups = _accession_groups(edgar, since, until, today, ciks, limit)
    index_rows_seen = sum(len(rows) for rows in groups.values())
    exchange_copy = sum(len(rows) - 1 for rows in groups.values())

    filings: list[_Filing] = []
    pre_xml = 0
    for accession, rows in groups.items():
        head = _index_head(accession, rows)
        raw = edgar.document(head.cik, accession, f"{accession}.txt")
        if isinstance(raw, _NotServed):
            filings.append(
                _Filing(
                    accession,
                    head.form,
                    head.filed_on,
                    head.cik,
                    None,
                    None,
                    [raw.missing("form25")],
                )
            )
            continue
        notification = parse_notification(raw.decode("utf-8", "replace"))
        if notification is None:
            pre_xml += 1
            continue
        issuer = notification.issuer_cik or head.cik
        filings.append(_Filing(accession, head.form, head.filed_on, issuer, raw, notification, []))

    submissions = _load_submissions(edgar, filings, settings)
    for filing in filings:
        table = submissions.get(filing.issuer_cik)
        filing.accepted_at = table.acceptance.get(filing.accession) if table else None
    stamped = [f for f in filings if f.accepted_at is not None]
    unstamped = len(filings) - len(stamped)

    kept, attached = _attach_amendments(stamped)
    kept.sort(key=lambda f: (f.filed_on, f.accession))

    # Every kept filing is stamped, so its issuer's submissions were served.
    records = [
        _record(filing, kept, submissions[filing.issuer_cik], edgar, settings) for filing in kept
    ]
    counts = FetchCounts(
        index_rows_seen=index_rows_seen,
        kept=len(kept),
        orphan_amendment=sum(1 for f in kept if f.orphan_amendment),
        pre_xml=pre_xml,
        unstamped=unstamped,
        exchange_copy=exchange_copy,
        amendment_attached=attached,
    )
    if not counts.identity_holds():  # by construction; a failure is a bug here
        raise RuntimeError(f"departure corpus counts do not add up: {counts.as_json()}")

    corpus_path = out_dir / "corpus.jsonl"
    counts_path = out_dir / "counts.json"
    lines = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    edgar_raw.write_atomic(corpus_path, lines.encode("utf-8"))
    summary = {
        **counts.as_json(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "ciks": sorted(f"{int(cik):010d}" for cik in ciks),
        "limit": limit,
        "fetched_at": fetched_at.isoformat(),
    }
    edgar_raw.write_atomic(counts_path, (json.dumps(summary, indent=2) + "\n").encode())
    return FetchResult(corpus_path, counts_path, counts)


def _quarters(since: date, until: date) -> list[tuple[int, int]]:
    first = since.year * 4 + (since.month - 1) // 3
    last = until.year * 4 + (until.month - 1) // 3
    return [(index // 4, index % 4 + 1) for index in range(first, last + 1)]


def _quarter_end(year: int, qtr: int) -> date:
    next_start = date(year + 1, 1, 1) if qtr == 4 else date(year, qtr * 3 + 1, 1)
    return next_start - timedelta(days=1)


def _accession_groups(
    edgar: _CachedEdgar,
    since: date,
    until: date,
    today: date,
    ciks: Sequence[str],
    limit: int | None,
) -> dict[str, list[UnstampedFiling]]:
    """Index rows in range, grouped by accession in filing-date order."""
    rows: list[UnstampedFiling] = []
    for year, qtr in _quarters(since, until):
        complete = _quarter_end(year, qtr) + timedelta(days=INDEX_SETTLE_DAYS) < today
        quarter_rows = edgar.index_rows(year, qtr, complete=complete)
        rows.extend(row for row in quarter_rows if since <= row.filed_on <= until)
    rows.sort(key=lambda row: (row.filed_on, row.accession))  # stable: index order within
    groups: dict[str, list[UnstampedFiling]] = {}
    for row in rows:
        groups.setdefault(row.accession, []).append(row)
    if ciks:
        wanted = {f"{int(cik):010d}" for cik in ciks}
        groups = {
            accession: group
            for accession, group in groups.items()
            if any(row.cik in wanted for row in group)
        }
    if limit is not None:
        groups = dict(list(groups.items())[:limit])
    return groups


def _index_head(accession: str, rows: Sequence[UnstampedFiling]) -> UnstampedFiling:
    """The row whose CIK the `.txt` is fetched under and that stands in for the
    issuer when the notification names none: not the filer's own row (the
    accession's prefix, the exchange for a 25-NSE) when the index lists another."""
    filer = accession[:10]
    return next((row for row in rows if row.cik != filer), rows[0])


def _filing_rows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """A submissions payload's (or older page's) column lists as rows."""
    filings = payload.get("filings")
    columns = filings["recent"] if isinstance(filings, Mapping) else payload
    keys = ("form", "filingDate", "accessionNumber", "acceptanceDateTime", "primaryDocument")
    count = len(columns["accessionNumber"])
    rows = [{key: str(columns[key][i] or "") for key in keys} for i in range(count)]
    items = columns.get("items") or [""] * count
    for row, value in zip(rows, items, strict=True):
        row["items"] = str(value or "")
    return rows


def _load_submissions(
    edgar: _CachedEdgar, filings: Sequence[_Filing], settings: Settings
) -> dict[str, _Submissions]:
    """Each issuer's submissions rows and acceptance times, with the older pages
    that overlap any of its listing ends' windows."""
    labeling = settings.research.labeling
    before = timedelta(days=max(labeling.context_before_days, MARKER_BEFORE_DAYS))
    after = timedelta(days=max(labeling.context_after_days, labeling.marker_after_days))
    by_issuer: dict[str, list[_Filing]] = defaultdict(list)
    for filing in filings:
        by_issuer[filing.issuer_cik].append(filing)

    tables: dict[str, _Submissions] = {}
    for issuer, issuer_filings in by_issuer.items():
        open_until = max(f.filed_on for f in issuer_filings) + after
        payload = edgar.submissions(issuer, open_until=open_until)
        if isinstance(payload, _NotServed):
            for filing in issuer_filings:
                filing.missing.append(payload.missing("submissions"))
            continue
        windows = [(f.filed_on - before, f.filed_on + after) for f in issuer_filings]
        parts: list[Mapping[str, Any]] = [payload]
        for page in payload.get("filings", {}).get("files", []):
            first, last = (
                date.fromisoformat(page["filingFrom"]),
                date.fromisoformat(page["filingTo"]),
            )
            if not any(last >= lo and first <= hi for lo, hi in windows):
                continue
            older = edgar.submissions_page(str(page["name"]))
            if isinstance(older, _NotServed):
                for filing in issuer_filings:
                    filing.missing.append(older.missing("submissions_page"))
                continue
            parts.append(older)
        rows = [row for part in parts for row in _filing_rows(part)]
        tables[issuer] = _Submissions(rows, acceptance_times(*parts))
    return tables


def _stamp(filing: _Filing) -> datetime:
    if filing.accepted_at is None:
        raise ValueError(f"{filing.accession}: unstamped filing reached amendment matching")
    return filing.accepted_at


def _attach_amendments(stamped: Sequence[_Filing]) -> tuple[list[_Filing], int]:
    """Originals plus orphan amendments, and how many amendments were attached."""
    ordered = sorted(stamped, key=lambda f: (_stamp(f), f.accession))
    originals = [f for f in ordered if f.form in ORIGINAL_FORMS]
    kept = list(originals)
    attached = 0
    for amendment in (f for f in ordered if f.form in AMENDMENT_FORMS):
        earlier = [
            original
            for original in originals
            if original.issuer_cik == amendment.issuer_cik
            and original.exchange == amendment.exchange
            and _stamp(original) < _stamp(amendment)
        ]
        if earlier:
            earlier[-1].amendments.append(
                {"accession": amendment.accession, "accepted_at": _stamp(amendment).isoformat()}
            )
            attached += 1
        else:
            amendment.orphan_amendment = True
            kept.append(amendment)
    return kept, attached


def _items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _accepted(table: _Submissions, accession: str) -> str | None:
    accepted = table.acceptance.get(accession)
    return accepted.isoformat() if accepted else None


def _markers(
    filing: _Filing, kept: Sequence[_Filing], table: _Submissions, settings: Settings
) -> list[dict[str, Any]]:
    """The issuer's marker filings in req 2's window, from the submissions rows."""
    lo = filing.filed_on - timedelta(days=MARKER_BEFORE_DAYS)
    hi = filing.filed_on + timedelta(days=settings.research.labeling.marker_after_days)
    later = [
        other.filed_on
        for other in kept
        if other.issuer_cik == filing.issuer_cik and other.filed_on > filing.filed_on
    ]
    next_end = min(later) if later else None
    markers = []
    for row in table.rows:
        if row["form"] not in MARKER_FORMS or not row["filingDate"]:
            continue
        filed = date.fromisoformat(row["filingDate"])
        if not lo <= filed <= hi or (next_end is not None and filed >= next_end):
            continue
        markers.append(
            {
                "form": row["form"],
                "accession": row["accessionNumber"],
                "filed_on": row["filingDate"],
                "accepted_at": _accepted(table, row["accessionNumber"]),
            }
        )
    return sorted(markers, key=lambda m: (m["filed_on"], m["accession"]))


def _eightk(
    filing: _Filing, table: _Submissions, edgar: _CachedEdgar, settings: Settings
) -> tuple[dict[str, Any] | None, str | None]:
    """C2's one 8-K for the listing end, or `None` with a note."""
    labeling = settings.research.labeling
    lo = filing.filed_on - timedelta(days=labeling.context_before_days)
    hi = filing.filed_on + timedelta(days=labeling.context_after_days)
    wanted = set(labeling.eightk_items)
    candidates = []
    for row in table.rows:
        if row["form"] not in EIGHTK_FORMS or not row["filingDate"]:
            continue
        filed = date.fromisoformat(row["filingDate"])
        if lo <= filed <= hi:
            relevant = bool(set(_items(row["items"])) & wanted)
            distance = abs((filed - filing.filed_on).days)
            candidates.append(
                (not relevant, distance, row["filingDate"], row["accessionNumber"], row)
            )
    if not candidates:
        return None, "no 8-K in window"
    row = min(candidates, key=lambda c: c[:4])[4]
    accession, document = row["accessionNumber"], row["primaryDocument"]
    if not document:
        return None, "8-K has no primary document"
    raw = edgar.document(filing.issuer_cik, accession, document)
    if isinstance(raw, _NotServed):
        filing.missing.append(raw.missing("eightk"))
        return None, "8-K primary document not served"
    passage = eightk_passage(
        raw.decode("utf-8", "replace"),
        labeling.eightk_items,
        item_max_chars=labeling.item_max_chars,
        eightk_max_chars=labeling.eightk_max_chars,
    )
    return (
        {
            "accession": accession,
            "form": row["form"],
            "filed_on": row["filingDate"],
            "accepted_at": _accepted(table, accession),
            "index_items": _items(row["items"]),
            "items": passage.items,
            "body_head": passage.body_head,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        None,
    )


def _record(
    filing: _Filing,
    kept: Sequence[_Filing],
    table: _Submissions,
    edgar: _CachedEdgar,
    settings: Settings,
) -> dict[str, Any]:
    """One `corpus.jsonl` object (amendment C11's fields, plus `issuer`,
    `exchange_name` and `orphan_amendment`)."""
    labeling = settings.research.labeling
    notification = filing.notification
    exhibit: dict[str, Any] | None = None  # the Form 25 was not served: unknown
    if filing.raw is not None:
        notice = notice_exhibit(
            filing.raw.decode("utf-8", "replace"), max_chars=labeling.exhibit_max_chars
        )
        exhibit = {
            "status": notice.status,
            "type": notice.document_type,
            "text": notice.text,
            "sha256": hashlib.sha256(filing.raw).hexdigest(),
        }
    raw_provision = notification.rule_provision_raw if notification else None
    eightk, note = _eightk(filing, table, edgar, settings)
    markers = _markers(filing, kept, table, settings)
    return {
        "listing_end_id": filing.accession,
        "cik": filing.issuer_cik,
        "issuer": notification.issuer if notification else None,
        "exchange": filing.exchange,
        "exchange_name": notification.exchange_name if notification else None,
        "class_title": notification.class_title if notification else None,
        "form": filing.form,
        "form25_accepted_at": filing.accepted_at.isoformat() if filing.accepted_at else None,
        "form25_filed_on": filing.filed_on.isoformat(),
        "signature_date": notification.signature_date if notification else None,
        "effective_on": (filing.filed_on + timedelta(days=EFFECTIVE_DAYS)).isoformat(),
        "rule_provision_raw": raw_provision,
        "rule_provision": normalise_provision(raw_provision) if raw_provision else None,
        "amendments": list(filing.amendments),
        "orphan_amendment": filing.orphan_amendment,
        "exhibit": exhibit,
        "eightk": eightk,
        "eightk_note": note,
        "markers": markers,
        "missing": list(filing.missing),
    }

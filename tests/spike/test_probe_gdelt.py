"""Offline tests for the #882 GDELT probe (spike code, never merged)."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

from tradepartner.config import Settings


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "probe_gdelt.py"
    spec = importlib.util.spec_from_file_location("probe_gdelt", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_gdelt"] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load()

# The live lastupdate.txt body of 2026-10-05 03:44Z (format check).
LASTUPDATE = (
    "29083 ab95d743dd3cf3a03e41c783a0ecfaea "
    "http://data.gdeltproject.org/gdeltv2/20261005034500.export.CSV.zip\n"
    "40287 02ef845625136c71deb2eef7fa79b449 "
    "http://data.gdeltproject.org/gdeltv2/20261005034500.mentions.CSV.zip\n"
    "2002121 769279aa325835c181f3f1c71f28c8fc "
    "http://data.gdeltproject.org/gdeltv2/20261005034500.gkg.csv.zip\n"
)

UNIVERSE = (
    "ticker,name,cik\n"
    "AAPL,Apple Inc.,320193\n"
    "TGT,TARGET CORP /MN/,27419\n"
    "JPM,JPMORGAN CHASE & CO,19617\n"
    "BRK.B,BERKSHIRE HATHAWAY INC,1067983\n"
    "MMM,3M CO,66740\n"
    "GOOGL,Alphabet Inc.,1652044\n"
    "GOOG,Alphabet Inc.,1652044\n"
)


def gkg_row(rec: str, url: str, title: str | None, orgs: str = "") -> str:
    """A 27-column GKG 2.1 row; `orgs` fills V2ORGANIZATIONS (col 14)."""
    cols = [""] * 27
    cols[0], cols[1], cols[2], cols[3], cols[4] = rec, rec[:14], "1", "example.com", url
    cols[13] = orgs
    no_title = "<PAGE_AUTHORS>x</PAGE_AUTHORS>"
    cols[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>" if title is not None else no_title
    return "\t".join(cols)


def gkg_zip(label: str, rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{label}.gkg.csv", "\n".join(rows) + "\n")
    return buf.getvalue()


class FakeWeb:
    """Serves lastupdate.txt for `latest` and GKG zips by label; records URLs."""

    def __init__(self, files: Mapping[str, bytes], latest: str) -> None:
        self.files = dict(files)
        self.latest = latest
        self.urls: list[str] = []
        self.fail_next: list[int] = []

    def lastupdate(self) -> str:
        body = self.files[self.latest]
        md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
        return f"{len(body)} {md5} http://data.gdeltproject.org/gdeltv2/{self.latest}.gkg.csv.zip\n"

    def get(self, url: str) -> tuple[int, bytes, Mapping[str, str]]:
        self.urls.append(url)
        if self.fail_next:
            return self.fail_next.pop(0), b"", {}
        if url.endswith("lastupdate.txt"):
            headers = {"last-modified": "Mon, 05 Oct 2026 03:34:16 GMT"}
            return 200, self.lastupdate().encode(), headers
        label = url.rsplit("/", 1)[-1].split(".", 1)[0]
        if label in self.files:
            return 200, self.files[label], {}
        return 404, b"", {}


def fetcher(web: FakeWeb) -> object:
    return pg.Fetcher(web.get, sleep=lambda _s: None)


def clock(start: datetime):  # type: ignore[no-untyped-def]
    t = [start]

    def now() -> datetime:
        t[0] += timedelta(seconds=1)
        return t[0]

    return now


# --------------------------------------------------------------------------- poll


def test_parse_lastupdate_picks_the_gkg_line() -> None:
    listed = pg.parse_lastupdate(LASTUPDATE)
    assert listed.label == "20261005034500"
    assert listed.size == 2002121
    assert listed.md5 == "769279aa325835c181f3f1c71f28c8fc"
    assert pg.parse_lastupdate("1 x http://h/20261005034500.export.CSV.zip") is None


def test_catchup_first_poll_fetches_only_the_newest() -> None:
    assert pg.catchup_labels("20261005034500", set()) == []


def test_catchup_fills_the_gap_since_the_oldest_held_file() -> None:
    held = {"20261005030000", "20261005031500"}
    assert pg.catchup_labels("20261005040000", held) == ["20261005033000", "20261005034500"]


def test_catchup_stops_after_max_attempts_and_respects_the_limit() -> None:
    held = {"20261005030000"}
    out = pg.catchup_labels("20261005040000", held, {"20261005031500": pg.MAX_ATTEMPTS})
    assert out == ["20261005033000", "20261005034500"]
    assert pg.catchup_labels("20261005040000", held, limit=1) == ["20261005034500"]


def test_fetcher_retries_5xx_and_returns_404_at_once() -> None:
    web = FakeWeb({"20261005034500": b"z"}, "20261005034500")
    sleeps: list[float] = []
    f = pg.Fetcher(web.get, sleep=sleeps.append)
    web.fail_next = [503, 429]
    status, _body, _h = f.fetch("https://x/20261005034500.gkg.csv.zip")
    assert status == 200 and len(web.urls) == 3
    backoffs = [s for s in sleeps if s >= pg.BACKOFF_SECONDS]
    assert backoffs == [pg.BACKOFF_SECONDS, 2 * pg.BACKOFF_SECONDS]
    web.urls.clear()
    assert f.fetch("https://x/20991005034500.gkg.csv.zip")[0] == 404
    assert len(web.urls) == 1
    web.fail_next = [500] * pg.RETRIES
    with pytest.raises(OSError):
        f.fetch("https://x/20261005034500.gkg.csv.zip")


def test_poll_stores_raw_with_fetch_time_and_is_idempotent(tmp_path: Path) -> None:
    body = gkg_zip("20261005034500", [gkg_row("20261005034500-0", "https://a/b", "T")])
    web = FakeWeb({"20261005034500": body}, "20261005034500")
    t0 = datetime(2026, 10, 5, 3, 46, tzinfo=UTC)
    done = pg.poll(tmp_path, fetcher(web), now=clock(t0))
    assert [r["status"] for r in done] == ["ok"]
    rec = pg.read_manifest(tmp_path)[0]
    known_at = datetime.fromisoformat(rec["known_at"])
    assert known_at.tzinfo is not None and known_at > t0  # our fetch time, never the label
    assert rec["listed_last_modified"] == "Mon, 05 Oct 2026 03:34:16 GMT"
    assert (tmp_path / "raw" / "20261005034500.gkg.csv.zip").read_bytes() == body
    assert web.urls[-1].startswith("https://")
    web.urls.clear()
    assert pg.poll(tmp_path, fetcher(web), now=clock(t0)) == []
    assert web.urls == [pg.LASTUPDATE_URL]  # one request, no refetch


def test_poll_catches_up_missed_files_and_records_missing(tmp_path: Path) -> None:
    labels = ["20261005030000", "20261005031500", "20261005033000", "20261005034500"]
    files = {lab: gkg_zip(lab, [gkg_row(lab + "-0", "https://a/b", "T")]) for lab in labels}
    web = FakeWeb({labels[0]: files[labels[0]]}, labels[0])
    now = clock(datetime(2026, 10, 5, 3, 1, tzinfo=UTC))
    pg.poll(tmp_path, fetcher(web), now=now)
    web.files.update({labels[1]: files[labels[1]], labels[3]: files[labels[3]]})
    web.latest = labels[3]
    done = pg.poll(tmp_path, fetcher(web), now=now)
    assert {(r["label"], r["via"], r["status"]) for r in done} == {
        (labels[3], "lastupdate", "ok"),
        (labels[1], "catchup", "ok"),
        (labels[2], "catchup", "missing"),
    }
    assert pg.held_labels(tmp_path) == {labels[0], labels[1], labels[3]}


def test_poll_rejects_a_body_whose_md5_differs(tmp_path: Path) -> None:
    web = FakeWeb({"20261005034500": b"good"}, "20261005034500")
    lastupdate = web.lastupdate()
    web.lastupdate = lambda: lastupdate  # type: ignore[method-assign]
    web.files["20261005034500"] = b"bad!"
    (rec,) = pg.poll(tmp_path, fetcher(web))
    assert rec["status"] == "error"
    assert not (tmp_path / "raw" / "20261005034500.gkg.csv.zip").exists()


# --------------------------------------------------------------------------- match rule


@pytest.mark.parametrize(
    ("name", "core"),
    [
        ("Apple Inc.", ("apple",)),
        ("JPMORGAN CHASE & CO", ("jpmorgan", "chase")),
        ("TARGET CORP /MN/", ("target",)),
        ("The Walt Disney Company", ("walt", "disney")),
        ("MCDONALD'S CORP", ("mcdonalds",)),
        ("3M CO", ()),  # under 3 characters: ticker rule only
    ],
)
def test_name_core(name: str, core: tuple[str, ...]) -> None:
    assert pg.name_core(name) == core


def _matcher(tmp_path: Path) -> object:
    path = tmp_path / "u.csv"
    path.write_text(UNIVERSE)
    return pg.Matcher.build(pg.load_universe(path))


def _hits(m: object, title: str, url: str = "https://x.com/") -> set[tuple[str, str]]:
    return {(c.tickers[0], rule) for c, rule, _t in m.match(title, url)}  # type: ignore[attr-defined]


def test_rule_title_name_needs_a_capitalised_single_word(tmp_path: Path) -> None:
    m = _matcher(tmp_path)
    assert _hits(m, "Apple Makes It Tougher for AI Agents") == {("AAPL", "title_name")}
    assert _hits(m, "an apple a day") == set()
    assert _hits(m, "JPMorgan Chase beats estimates") == {("JPM", "title_name")}


def test_rule_url_slug_and_ticker_tags(tmp_path: Path) -> None:
    m = _matcher(tmp_path)
    assert _hits(m, "", "https://news.com/markets/target-cuts-forecast/") == {("TGT", "url_name")}
    assert _hits(m, "", "https://target.com/deals") == set()  # host is not matched
    assert _hits(m, "Shares of $AAPL slip") == {("AAPL", "title_ticker")}
    assert _hits(m, "Buffett letter (NYSE: BRK-B)") == {("BRK.B", "title_ticker")}
    assert _hits(m, "3M settles (NYSE:MMM)") == {("MMM", "title_ticker")}
    assert _hits(m, "MMM is a ticker without a tag") == set()


def test_share_classes_collapse_to_one_company(tmp_path: Path) -> None:
    m = _matcher(tmp_path)
    (hit,) = m.match("Alphabet earnings", "https://x.com/")  # type: ignore[attr-defined]
    assert hit[0].tickers == ("GOOG", "GOOGL")


def test_match_never_reads_gdelt_org_fields(tmp_path: Path) -> None:
    """ADR 0008 / spec req 2 (d): V2ORGANIZATIONS naming a company links nothing."""
    m = _matcher(tmp_path)
    row = gkg_row("x-0", "https://x.com/weather", "Storm hits coast", orgs="Apple Inc,12")
    cols = row.split("\t")
    assert m.match(pg.page_title(cols[26]), cols[4]) == []  # type: ignore[attr-defined]


def test_page_title_unescapes_html() -> None:
    assert pg.page_title("<PAGE_TITLE>AT&amp;T &#39;wins&#39;</PAGE_TITLE>") == "AT&T 'wins'"
    assert pg.page_title("<PAGE_AUTHORS>x</PAGE_AUTHORS>") == ""


# --------------------------------------------------------------------------- report


def test_expected_labels_cover_an_et_day() -> None:
    assert len(pg.expected_labels(date(2026, 10, 6))) == 96
    assert len(pg.expected_labels(date(2026, 11, 1))) == 100  # DST ends: 25 hours
    assert pg.expected_labels(date(2026, 10, 6))[0] == "20261006040000"


def test_wilson_lower_at_the_precision_threshold() -> None:
    assert pg.wilson_lower(170, 200) == pytest.approx(0.7948, abs=1e-3)


def _probe_week(root: Path) -> None:
    """Nine ET days (a partial start day, seven full, one more), two files a day."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir()
    manifest = []
    start = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)  # 12:00 ET on 10-05
    for d in range(9):
        for k in range(2):
            t = start + timedelta(days=d, minutes=15 * k)
            lab = pg.label_of(t)
            rows = [
                gkg_row(f"{lab}-0", f"https://x.com/{d}/{k}", f"Apple story {d}"),
                gkg_row(f"{lab}-1", f"https://x.com/target-sale-{d}", None),
                gkg_row(f"{lab}-2", "https://x.com/weather", "Rain"),
            ]
            body = gkg_zip(lab, rows)
            name = f"{lab}.gkg.csv.zip"
            (root / "raw" / name).write_bytes(body)
            manifest.append(
                {
                    "label": lab,
                    "file": name,
                    "status": "ok",
                    "via": "lastupdate",
                    "known_at": (t + timedelta(minutes=2)).isoformat(),
                    "bytes": len(body),
                    "listed_last_modified": (t - timedelta(minutes=11)).strftime(
                        "%a, %d %b %Y %H:%M:%S GMT"
                    ),
                }
            )
    (root / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in manifest))
    (root / "universe.csv").write_text(UNIVERSE)


def test_match_and_report_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "probe"
    _probe_week(root)
    meta = pg.match(root, root / "universe.csv")
    assert meta["files"] == 18 and meta["companies"] == 6
    s = pg.report(root, None)
    assert s["window"] == ["2026-10-06", "2026-10-12"] and s["days"] == 7
    assert s["files_held"] == 14 and s["completeness"] == pytest.approx(14 / 672)
    assert s["headline_share"] == pytest.approx(2 / 3)
    assert s["coverage_week"] == pytest.approx(2 / 6)  # Apple (title) and Target (URL)
    assert s["coverage_day_mean"] == pytest.approx(2 / 6)
    assert s["lag_fetch_minus_label_min"]["median"] == pytest.approx(2.0)
    assert s["lag_posted_minus_label_min"]["median"] == pytest.approx(-11.0)
    assert s["verdicts"]["T2 precision"].startswith("PENDING")
    assert s["verdicts"]["validity (completeness, 7 days)"] == "FAIL"
    assert "| T1 headline share" in (root / "report.md").read_text()

    sample = list(csv.DictReader((root / "precision_sample.csv").open()))
    assert len(sample) == 14 + 7  # distinct (url, company) pairs in the window
    assert {r["et_date"] for r in sample} <= {f"2026-10-{d:02d}" for d in range(6, 13)}
    for r in sample:
        r["correct"] = "1" if r["rule"] == "title_name" else "0"
    labelled = tmp_path / "labelled.csv"
    with labelled.open("w", newline="") as fh:
        w = csv.DictWriter(fh, list(sample[0]))
        w.writeheader()
        w.writerows(sample)
    s2 = pg.report(root, labelled)
    assert s2["correct"] == 14 and s2["labelled"] == 21
    assert s2["precision_by_rule"] == {"title_name": "14/14", "url_name": "0/7"}
    assert s2["verdicts"]["T2 precision"].startswith("PENDING (21/200")


def test_sample_is_seeded_and_deduplicated() -> None:
    rows = [
        {"url": f"u{i % 50}", "company_key": "c", "et_date": "d", "tickers": "T", "name": "N",
         "rule": "r", "matched": "m", "title": "t"}
        for i in range(500)
    ]  # fmt: skip
    a, b = pg.sample_matches(rows, n=20), pg.sample_matches(list(reversed(rows)), n=20)
    assert [r["url"] for r in a] == [r["url"] for r in b]
    assert len({r["url"] for r in pg.sample_matches(rows, n=200)}) == 50


def test_verdicts_pass_at_the_thresholds() -> None:
    th = pg.THRESHOLDS
    s = {
        "completeness": th["completeness_min"],
        "days": 7,
        "headline_share": th["headline_share_min"],
        "headline_share_worst_day": th["headline_share_day_min"],
        "coverage_week": th["coverage_week_min"],
        "coverage_day_mean": th["coverage_day_mean_min"],
        "matched_gb_per_year": th["matched_cache_gb_year_max"],
        "labelled": 200,
        "correct": int(th["precision_correct_min"]),
    }
    assert set(pg.verdicts(s).values()) == {"PASS"}
    s["correct"] -= 1
    assert pg.verdicts(s)["T2 precision"] == "FAIL"


def test_thresholds_match_the_protocol_table() -> None:
    research = Path(__file__).resolve().parents[2] / "docs" / "research"
    doc = (research / "2026-10-05-gdelt-probe-protocol.md").read_text()
    for key, value in pg.THRESHOLDS.items():
        assert f"`{key}` = {value:g}" in doc, key


# --------------------------------------------------------------------------- universe export


def test_universe_rows_from_the_fixture_store(fixture_store: duckdb.DuckDBPyConnection) -> None:
    t = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)
    rows = pg.universe_rows(fixture_store, t, top_n=1000, settings=Settings(_env_file=None))
    assert rows, "fixture universe has members at T_LATE"
    assert all(r["name"] and r["ticker"] for r in rows)
    ranks = [r["company_rank"] for r in rows]
    assert ranks == sorted(ranks)
    assert len(pg.universe_rows(fixture_store, t, top_n=1, settings=Settings(_env_file=None))) <= 2

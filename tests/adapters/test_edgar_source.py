"""`EdgarFilingSource` index and snapshot (T11b) over the recorded EDGAR fixtures.

Tests scan the index from 2024 with a fixed clock of 2026-09-25T00:00Z, so
quarters 2024 Q1 to 2026 Q3. The router serves the recorded 2024 Q1 index,
a header-only `form.idx` for every quarter in between, and for 2026 Q3 a
**synthetic** index: the recorded KLX 25-NSE (accepted 2026-09-24, filed by
the exchange under its own accession prefix), the same accession listed under
the exchange's CIK 1354457, and an Apple 8-K whose accession is in no
recorded submissions payload.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from edgar_transport import (
    FIXTURES,
    SUBMISSIONS,
    USER_AGENT,
    EdgarRouter,
    edgar_settings,
    index_header,
    index_line,
    load,
)

from tradepartner.adapters.edgar import acceptance_times
from tradepartner.adapters.edgar_raw import EdgarCredentialsError
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.filings import CoverPage, FilingIndexEntry
from tradepartner.config import Settings
from tradepartner.store.master import build_master

CLOCK = datetime(2026, 9, 25, tzinfo=UTC)
APPLE, ALPHABET, KLX = "0000320193", "0001652044", "0001738827"
KLX_25NSE = "0001354457-26-000904"
MISSING = "0000320193-26-999999"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/"
BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"


def _synthetic(*lines: str) -> str:
    return index_header() + "".join(lines)


KLX_LINE = index_line(
    "25-NSE", "KLX Energy Services Holdings, Inc.", 1738827, "2026-09-24", KLX_25NSE
)
EXCHANGE_LINE = index_line("25-NSE", "Nasdaq Stock Market LLC", 1354457, "2026-09-24", KLX_25NSE)
MISSING_LINE = index_line("8-K", "Apple Inc.", 320193, "2026-09-24", MISSING)


def _router(
    last: tuple[int, int] = (2026, 3), overrides: dict[tuple[int, int], str | int] | None = None
) -> EdgarRouter:
    router = EdgarRouter()
    quarters = [(y, q) for y in range(2024, last[0] + 1) for q in range(1, 5) if (y, q) <= last]
    bodies: dict[tuple[int, int], str | int] = {q: index_header() for q in quarters}
    bodies[(2024, 1)] = (FIXTURES / "filing_index_2024_qtr1.txt").read_text()
    bodies[(2026, 3)] = _synthetic(KLX_LINE, EXCHANGE_LINE, MISSING_LINE)
    bodies.update(overrides or {})
    for (year, qtr), body in bodies.items():
        router.add_index(year, qtr, body)
    return router


def _source(settings: Settings, router: EdgarRouter, clock: datetime = CLOCK) -> EdgarFilingSource:
    return EdgarFilingSource(settings, client=router.client(), clock=lambda: clock)


def _recorded_acceptance() -> dict[str, datetime]:
    return acceptance_times(*(load(name) for files in SUBMISSIONS.values() for name in files))


def _submission_urls(router: EdgarRouter) -> list[str]:
    return [u.removeprefix(SUBMISSIONS_URL) for u in router.urls if u.startswith(SUBMISSIONS_URL)]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return edgar_settings(tmp_path / "cache")


# --- what is kept and how it is stamped -------------------------------------


def test_rows_carry_the_recorded_acceptance_instant_never_the_filing_date(
    settings: Settings,
) -> None:
    entries = _source(settings, _router()).filing_index()
    recorded = _recorded_acceptance()
    assert entries and all(e.accepted_at == recorded[e.accession] for e in entries)
    assert all(e.accepted_at.tzinfo is UTC for e in entries)
    # Apple's 10-Q, accepted 18:03 ET on 2024-02-01, is indexed as filed 2024-02-02.
    [ten_q] = [e for e in entries if e.accession == "0000320193-24-000006"]
    assert ten_q.accepted_at == datetime(2024, 2, 1, 23, 3, 38, tzinfo=UTC)
    [klx] = [e for e in entries if e.accession == KLX_25NSE]
    assert (klx.cik, klx.form) == (KLX, "25-NSE")
    assert klx.accepted_at == datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC)


def test_only_issuer_ciks_are_kept_with_all_their_forms(settings: Settings) -> None:
    source = _source(settings, _router())
    entries = source.filing_index()
    assert {e.cik for e in entries} == {APPLE, ALPHABET, KLX}
    assert {"3", "4", "144", "13F-HR", "SC 13G/A"} <= {e.form for e in entries}
    # 24X National Exchange (Form 1), AC Partners (1-A) and the exchange CIK
    # whose only row is its copy of the KLX 25-NSE.
    assert source.skipped_filers == 3
    assert [e.cik for e in entries if e.accession == KLX_25NSE] == [KLX]


def test_the_exchange_copy_is_dropped_even_when_the_exchange_is_an_issuer(
    settings: Settings,
) -> None:
    """A synthetic 10-K makes the exchange CIK an issuer; its own copy of the
    25-NSE it filed for KLX is still dropped, and KLX keeps the filing."""
    ten_k = "0001354457-24-000100"
    exchange_10k = index_line("10-K", "Nasdaq Stock Market LLC", 1354457, "2024-02-20", ten_k)
    recorded = (FIXTURES / "filing_index_2024_qtr1.txt").read_text()
    router = _router(overrides={(2024, 1): recorded + exchange_10k})
    columns = {
        "accessionNumber": [ten_k, KLX_25NSE],
        "form": ["10-K", "25-NSE"],
        "primaryDocument": ["a.htm", "primary_doc.xml"],
        "isInlineXBRL": [1, 0],
        "acceptanceDateTime": ["2024-02-20T21:00:00.000Z", "2026-09-24T14:08:40.000Z"],
    }
    router.add(
        f"{SUBMISSIONS_URL}CIK0001354457.json",
        {"cik": "1354457", "name": "Nasdaq Stock Market LLC", "filings": {"recent": columns}},
    )
    entries = _source(settings, router).filing_index()
    assert [(e.cik, e.form) for e in entries if e.cik == "0001354457"] == [("0001354457", "10-K")]
    assert [e.cik for e in entries if e.accession == KLX_25NSE] == [KLX]


def test_an_accession_absent_from_the_submissions_is_reported_and_nowhere_else(
    settings: Settings,
) -> None:
    router = _router()
    source = _source(settings, router)
    entries = source.filing_index()
    assert MISSING not in {e.accession for e in entries}
    assert [(u.cik, u.accession, u.form) for u in source.unstamped_filings] == [
        (APPLE, MISSING, "8-K")
    ]
    # Apple's older page is read looking for it; Alphabet's is never needed.
    assert sorted(_submission_urls(router)) == [
        "CIK0000320193-submissions-001.json",
        "CIK0000320193.json",
        "CIK0001652044.json",
        "CIK0001738827.json",
    ]


def test_since_filters_the_result_but_not_the_fetched_urls(tmp_path: Path) -> None:
    since = datetime(2024, 2, 1, tzinfo=UTC)
    full_router, since_router = _router(), _router()
    full = _source(edgar_settings(tmp_path / "a"), full_router).filing_index()
    after = _source(edgar_settings(tmp_path / "b"), since_router).filing_index(since)
    assert after == [e for e in full if e.accepted_at >= since] and len(after) < len(full)
    assert since_router.urls == full_router.urls


def test_requests_counts_every_http_request(settings: Settings) -> None:
    router = _router()
    source = _source(settings, router)
    source.filing_index()
    source.companies_snapshot()
    assert source.requests == len(router.urls) > 0


# --- caching -------------------------------------------------------------------


def _cached_quarters(settings: Settings) -> set[str]:
    return {
        p.name.removesuffix(".idx.gz") for p in (Path(settings.edgar.cache_dir) / "index").iterdir()
    }


def test_a_quarter_is_cached_only_once_settled_and_the_open_quarter_may_404(
    settings: Settings,
) -> None:
    later = datetime(2026, 10, 2, tzinfo=UTC)  # Q3 ended 2026-10-01 ET, settles 2026-10-04
    router = _router(last=(2026, 4), overrides={(2026, 4): 404})
    entries = _source(settings, router, clock=later).filing_index()
    assert KLX_25NSE in {e.accession for e in entries}
    cached = _cached_quarters(settings)
    assert "2026-QTR2" in cached and "2026-QTR3" not in cached and "2026-QTR4" not in cached
    assert len(cached) == 10


def test_a_404_for_a_closed_quarter_raises(settings: Settings) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _source(settings, _router(overrides={(2025, 2): 404})).filing_index()


def test_a_second_call_fetches_only_the_unsettled_quarter_and_restamps_nothing(
    settings: Settings,
) -> None:
    first = _source(settings, _router()).filing_index()
    again_router = _router()
    source = _source(settings, again_router)
    assert source.filing_index() == first
    assert again_router.index_urls() == [
        "https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx"
    ]
    # Only Apple still has an accession to stamp (its quarter is not settled).
    assert _submission_urls(again_router) == [
        "CIK0000320193.json",
        "CIK0000320193-submissions-001.json",
    ]
    # The same instance reuses its memoised submissions: only the open quarter.
    before = len(again_router.urls)
    assert source.filing_index() == first
    assert again_router.urls[before:] == again_router.index_urls()[-1:]


def test_an_accession_missing_after_its_quarter_settled_is_cached_as_unstampable(
    settings: Settings,
) -> None:
    settled = {(2025, 1): _synthetic(MISSING_LINE), (2026, 3): _synthetic(KLX_LINE)}
    first = _source(settings, _router(overrides=settled))
    first.filing_index()
    assert [u.accession for u in first.unstamped_filings] == [MISSING]
    router = _router(overrides=settled)
    again = _source(settings, router)
    again.filing_index()
    assert [u.accession for u in again.unstamped_filings] == [MISSING]
    assert _submission_urls(router) == []


def test_a_truncated_cache_file_is_not_served(settings: Settings) -> None:
    first = _source(settings, _router()).filing_index()
    cache = Path(settings.edgar.cache_dir)
    index = cache / "index" / "2024-QTR1.idx.gz"
    index.write_bytes(index.read_bytes()[:100])
    [stamps] = (cache / "stamps").glob(f"*/{ALPHABET}.json")
    stamps.write_bytes(stamps.read_bytes()[:100])
    router = _router()
    assert _source(settings, router).filing_index() == first
    assert "https://www.sec.gov/Archives/edgar/full-index/2024/QTR1/form.idx" in router.urls
    assert "CIK0001652044.json" in _submission_urls(router)
    assert gzip.decompress(index.read_bytes())  # re-cached whole


# --- the bulk path -------------------------------------------------------------


def test_the_bulk_zip_stamps_and_the_per_cik_top_up_follows(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:  # built before KLX's latest filings
        for cik in (APPLE, ALPHABET):
            main, page = SUBMISSIONS[cik]
            bulk.write(FIXTURES / main, f"CIK{cik}.json")
            bulk.write(FIXTURES / page, f"CIK{cik}-submissions-001.json")
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    settings = edgar_settings(tmp_path / "cache", bulk_stamp_threshold_ciks=2)
    source = _source(settings, router)
    entries = source.filing_index()
    assert entries == _source(edgar_settings(tmp_path / "per-cik"), _router()).filing_index()
    assert router.urls.index(BULK_URL) < len(router.urls) - 3
    # KLX is not in the zip, Apple's 8-K is in neither: both topped up per CIK.
    assert sorted(_submission_urls(router)) == [
        "CIK0000320193-submissions-001.json",
        "CIK0000320193.json",
        "CIK0001738827.json",
    ]
    assert [u.accession for u in source.unstamped_filings] == [MISSING]


# --- the companies snapshot and provenance ---------------------------------------


def test_snapshot_rows_are_known_when_fetched(settings: Settings) -> None:
    entries = _source(settings, _router()).companies_snapshot()
    assert entries and all(e.fetched_at == CLOCK for e in entries)
    assert entries == sorted(entries, key=lambda e: (e.fetched_at, e.ticker, e.cik))
    assert {"AAPL", "GOOGL"} <= {e.ticker for e in entries}


def test_the_master_stamps_filing_and_snapshot_provenance(tmp_path: Path) -> None:
    """With no static columns, snapshot listings are `snapshot`, known from
    the session of the fetch (T3 left every static column on by default)."""

    class NoCovers(EdgarFilingSource):
        def cover_pages(self, cik: str) -> list[CoverPage]:
            return []

    settings = edgar_settings(tmp_path)
    settings = settings.model_copy(
        update={"master": settings.master.model_copy(update={"static_columns": []})}
    )
    source = NoCovers(settings, client=_router().client(), clock=lambda: CLOCK)
    master = build_master(source, settings, ingested_at=CLOCK)
    securities = {r["cik"]: r for r in master.securities if not r["benchmark"]}
    assert {r["provenance"] for r in securities.values()} == {"filing"}
    issuer_forms = settings.master.issuer_forms
    assert securities[APPLE]["known_at"] == min(
        e.accepted_at for e in source.filing_index() if e.cik == APPLE and e.form in issuer_forms
    )
    assert "snapshot" in {r["provenance"] for r in master.listings}


# --- credentials and the T11c methods ----------------------------------------------


def test_a_missing_user_agent_raises_before_any_request(tmp_path: Path) -> None:
    settings = edgar_settings(tmp_path).model_copy(update={"sec_edgar_user_agent": None})
    router = _router()
    source = _source(settings, router)
    with pytest.raises(EdgarCredentialsError):
        source.filing_index()
    assert router.urls == [] and source.requests == 0


def test_an_http_error_names_no_secret(settings: Settings) -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        _source(settings, _router(overrides={(2024, 1): 500})).filing_index()
    assert USER_AGENT not in str(raised.value) and "test@example.com" not in repr(raised.value)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("facts", (APPLE, ["EntityCommonStockSharesOutstanding"])),
        ("filing_headers", (APPLE, ["10-K"])),
        ("cover_pages", (APPLE,)),
        ("delistings", ()),
    ],
)
def test_the_per_cik_methods_are_left_to_t11c(
    settings: Settings, method: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(NotImplementedError, match="T11c"):
        getattr(_source(settings, _router()), method)(*args)


def test_entries_are_filing_index_entries(settings: Settings) -> None:
    entries = _source(settings, _router()).filing_index()
    assert all(isinstance(e, FilingIndexEntry) for e in entries)
    assert entries == sorted(entries, key=lambda e: (e.accepted_at, e.accession, e.cik))


def test_a_corrupt_cache_file_is_fetched_again(settings: Settings) -> None:
    first = _source(settings, _router()).filing_index()
    cache = Path(settings.edgar.cache_dir)
    index = cache / "index" / "2024-QTR1.idx.gz"
    data = bytearray(index.read_bytes())
    data[10] ^= 0xFF  # the first deflate byte: zlib.error, not a gzip OSError
    index.write_bytes(bytes(data))
    [stamps] = (cache / "stamps").glob(f"*/{ALPHABET}.json")
    stamps.write_text(stamps.read_text().replace("+00:00", ""))  # naive instants
    router = _router()
    assert _source(settings, router).filing_index() == first
    assert "https://www.sec.gov/Archives/edgar/full-index/2024/QTR1/form.idx" in router.urls
    assert "CIK0001652044.json" in _submission_urls(router)


def test_quarters_and_settling_follow_eastern_time(settings: Settings) -> None:
    # 2026-10-01T02:00Z is 2026-09-30 22:00 ET: Q3 is still the open quarter,
    # so Q4 is never asked for (an unrouted URL would fail the test).
    _source(settings, _router(), clock=datetime(2026, 10, 1, 2, tzinfo=UTC)).filing_index()
    # 2026-10-04T02:00Z is 2026-10-03 22:00 ET: Q3 settles only at 2026-10-04 00:00 ET.
    router = _router(last=(2026, 4), overrides={(2026, 4): 404})
    _source(settings, router, clock=datetime(2026, 10, 4, 2, tzinfo=UTC)).filing_index()
    assert "2026-QTR3" not in _cached_quarters(settings)
    later = datetime(2026, 10, 4, 4, 1, tzinfo=UTC)
    _source(
        settings, _router(last=(2026, 4), overrides={(2026, 4): 404}), clock=later
    ).filing_index()
    assert "2026-QTR3" in _cached_quarters(settings)

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
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

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

from tradepartner.adapters import edgar_raw
from tradepartner.adapters import edgar_source as edgar_source_module
from tradepartner.adapters.edgar import FsnFiling, UnstampedFiling, acceptance_times
from tradepartner.adapters.edgar_raw import EdgarCredentialsError
from tradepartner.adapters.edgar_source import (
    COVER_VERSION,
    FSN_VERSION,
    STAMPS_LAYOUT,
    EdgarFilingSource,
    SubmissionRecord,
    reduce_submissions,
)
from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingIndexEntry,
)
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


KLX_AT = "2026-09-24T14:08:40"


def _payload(cik: int, *filings: tuple[str, str, str]) -> dict[str, object]:
    """A synthetic submissions payload: (accession, form, acceptance in UTC)."""
    accessions, forms, stamps = zip(*filings, strict=True)
    columns = {
        "accessionNumber": list(accessions),
        "form": list(forms),
        "primaryDocument": ["doc.htm"] * len(filings),
        "isInlineXBRL": [0] * len(filings),
        "acceptanceDateTime": [f"{at}.000Z" for at in stamps],
    }
    return {"cik": str(cik), "name": "Synthetic", "filings": {"recent": columns}}


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


def test_reduce_submissions_skips_a_blank_acceptance_instead_of_raising() -> None:
    # #1138, same bug class as #1055: since #1133 a blank `acceptanceDateTime`
    # leaves its accession out of `acceptance_times`'s result, so the dict
    # comprehension in `reduce_submissions` that indexed `times[accession]`
    # for every accession raised `KeyError`, aborting the whole payload's
    # reduce. The blank accession should simply stay out of `records`,
    # leaving the other accession's record intact.
    payload = _payload(
        int(APPLE),
        ("0000320193-26-000001", "10-Q", KLX_AT),
        ("0000320193-26-000002", "8-K", KLX_AT),
    )
    columns = payload["filings"]["recent"]
    columns["acceptanceDateTime"] = ["", columns["acceptanceDateTime"][1]]

    records, pages = reduce_submissions(payload)

    assert "0000320193-26-000001" not in records
    assert "0000320193-26-000002" in records
    assert pages == []


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
    router.add(
        f"{SUBMISSIONS_URL}CIK0001354457.json",
        _payload(1354457, (ten_k, "10-K", "2024-02-20T21:00:00"), (KLX_25NSE, "25-NSE", KLX_AT)),
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


def _moved_early(name: str) -> bytes:
    """Submissions fixture `name` with every `acceptanceDateTime` moved 5 h
    earlier, the shape #1382 found in SEC's bulk `submissions.zip`."""
    payload = load(name)
    recent = payload["filings"]["recent"] if "filings" in payload else payload
    recent["acceptanceDateTime"] = [
        (datetime.fromisoformat(at.replace("Z", "+00:00")) - timedelta(hours=5))
        .isoformat()
        .replace("+00:00", ".000Z")
        for at in recent["acceptanceDateTime"]
    ]
    return json.dumps(payload).encode("utf-8")


def test_a_moved_bulk_zip_never_writes_a_stamp_by_default(tmp_path: Path) -> None:
    """#1382 guard: SEC's `submissions.zip` serves `acceptanceDateTime` moved
    by New York's UTC offset (early for many CIKs, a look-ahead). With
    `edgar.bulk_stamps_enabled` at its default (`false`), more pending CIKs
    than `edgar.bulk_stamp_threshold_ciks` are still stamped per CIK from the
    API: the zip is not even downloaded, and every stamp equals the per-CIK
    one, never the zip's early value."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        for cik in (APPLE, ALPHABET):
            for number, name in enumerate(SUBMISSIONS[cik]):
                member = f"CIK{cik}.json" if number == 0 else f"CIK{cik}-submissions-001.json"
                bulk.writestr(member, _moved_early(name))
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    settings = edgar_settings(tmp_path / "cache", bulk_stamp_threshold_ciks=2)
    assert settings.edgar.bulk_stamps_enabled is False
    source = _source(settings, router)
    entries = source.filing_index()
    assert BULK_URL not in router.urls
    assert entries == _source(edgar_settings(tmp_path / "per-cik"), _router()).filing_index()
    recorded = _recorded_acceptance()
    assert entries and all(e.accepted_at == recorded[e.accession] for e in entries)


def test_the_bulk_zip_stamps_and_the_per_cik_top_up_follows(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:  # built before KLX's latest filings
        for cik in (APPLE, ALPHABET):
            main, page = SUBMISSIONS[cik]
            bulk.write(FIXTURES / main, f"CIK{cik}.json")
            bulk.write(FIXTURES / page, f"CIK{cik}-submissions-001.json")
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    settings = edgar_settings(
        tmp_path / "cache", bulk_stamp_threshold_ciks=2, bulk_stamps_enabled=True
    )
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


def test_an_empty_submissions_api_payload_leaves_rows_unstamped_not_failed(
    tmp_path: Path,
) -> None:
    """#576: the per-CIK submissions API answering 200 `{}` (a CIK payload or
    an older page) lists nothing: the run goes on, the rows it would have
    stamped are excluded and reported as unstamped, and nothing is cached as
    unstampable, so a later run that gets the real payload stamps them."""
    settings = edgar_settings(tmp_path / "cache")
    router = _router()
    router.add(f"{SUBMISSIONS_URL}CIK{ALPHABET}.json", {})
    router.add(f"{SUBMISSIONS_URL}CIK{APPLE}-submissions-001.json", {})
    source = _source(settings, router)
    entries = source.filing_index()
    full = _source(edgar_settings(tmp_path / "per-cik"), _router()).filing_index()
    assert source.submissions_api_empty == 2
    assert entries == [e for e in entries if e in full]
    assert not any(e.cik == ALPHABET for e in entries)
    unstamped = {u.accession for u in source.unstamped_filings}
    assert {e.accession for e in full if e.cik == ALPHABET} <= unstamped
    assert {e.accession for e in full} - {e.accession for e in entries} <= unstamped
    later = _source(settings, _router())
    assert later.filing_index() == full
    assert later.submissions_api_empty == 0


def test_an_empty_bulk_submissions_member_falls_back_to_the_api(tmp_path: Path) -> None:
    """#566 sweep: an empty `{}` CIK member or older page in `submissions.zip`
    is treated as absent, so the CIK is stamped per CIK, never left
    unstamped or failing the source."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        bulk.write(FIXTURES / SUBMISSIONS[APPLE][0], f"CIK{APPLE}.json")
        bulk.writestr(f"CIK{APPLE}-submissions-001.json", "{}")
        bulk.writestr(f"CIK{ALPHABET}.json", "{}")
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    settings = edgar_settings(
        tmp_path / "cache", bulk_stamp_threshold_ciks=2, bulk_stamps_enabled=True
    )
    source = _source(settings, router)
    entries = source.filing_index()
    assert entries == _source(edgar_settings(tmp_path / "per-cik"), _router()).filing_index()
    assert source.submissions_bulk_empty == 2
    assert {"CIK0001652044.json", "CIK0000320193-submissions-001.json"} <= set(
        _submission_urls(router)
    )
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

        def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
            return []  # the master reads Form 25s for relistings (#820); none here

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


def test_a_self_filed_form_25_stays_under_every_cik_that_lists_it(settings: Settings) -> None:
    """A parent's own Form 25 naming a co-registrant: only a 25-NSE's filer
    copy is dropped (quant-auditor, owner decision on #184)."""
    form_25 = "0001111111-26-000001"
    lines = (
        index_line("10-K", "Parent Corp", 1111111, "2026-08-03", "0001111111-26-000000"),
        index_line("25", "Parent Corp", 1111111, "2026-09-01", form_25),
        index_line("25", "Parent Finance LLC", 2222222, "2026-09-01", form_25),
    )
    router = _router(overrides={(2026, 3): _synthetic(KLX_LINE, *lines)})
    at = "2026-09-01T20:00:00"
    router.add(
        f"{SUBMISSIONS_URL}CIK0001111111.json",
        _payload(
            1111111, ("0001111111-26-000000", "10-K", "2026-08-03T20:00:00"), (form_25, "25", at)
        ),
    )
    router.add(f"{SUBMISSIONS_URL}CIK0002222222.json", _payload(2222222, (form_25, "25", at)))
    entries = _source(settings, router).filing_index()
    assert sorted(e.cik for e in entries if e.accession == form_25) == ["0001111111", "0002222222"]


def test_a_company_seen_only_through_its_delisting_is_kept(settings: Settings) -> None:
    """A CIK with no issuer form but named as the subject of an exchange's
    25-NSE is an issuer; the exchange, as filer, is not."""
    gone = "0001354457-26-000905"
    lines = (
        index_line("25-NSE", "Gone Corp", 3333333, "2026-09-24", gone),
        index_line("25-NSE", "Nasdaq Stock Market LLC", 1354457, "2026-09-24", gone),
    )
    router = _router(overrides={(2026, 3): _synthetic(KLX_LINE, *lines)})
    router.add(f"{SUBMISSIONS_URL}CIK0003333333.json", _payload(3333333, (gone, "25-NSE", KLX_AT)))
    source = _source(settings, router)
    entries = source.filing_index()
    assert [(e.cik, e.form) for e in entries if e.accession == gone] == [("0003333333", "25-NSE")]
    assert "0001354457" not in {e.cik for e in entries}
    assert source.skipped_filers == 3


# --- 8-K items and the stamps layout (T164c, #1358) ----------------------------

SETTLED = {(2025, 1): _synthetic(MISSING_LINE), (2026, 3): _synthetic(KLX_LINE)}
APPLE_202 = "0000320193-24-000005"  # recorded 8-K, items "2.02,9.01"
APPLE_507 = "0001140361-24-010155"  # recorded 8-K, items "5.07"
APPLE_10Q = "0000320193-24-000006"  # recorded 10-Q, items ""


def _stamps_files(settings: Settings) -> dict[str, Path]:
    root = Path(settings.edgar.cache_dir) / "stamps"
    return {p.stem: p for p in root.glob("*/*.json")}


def _to_old_layout(path: Path) -> None:
    """Rewrite a stamps file exactly as the pre-T164c writer did: no `layout`,
    no `items_refreshed`, four elements per record."""
    data = json.loads(path.read_bytes())
    old = {
        "version": data["version"],
        "cik": data["cik"],
        "records": {a: row[:4] for a, row in data["records"].items()},
    }
    path.write_bytes(json.dumps(old).encode("utf-8"))


def _old_cache(settings: Settings) -> dict[str, dict[str, list[object]]]:
    """A settled fixture cache (with MISSING as a `None` sentinel) in the old
    layout; returns each CIK's old records as written."""
    _source(settings, _router(overrides=SETTLED)).filing_index()
    old = {}
    for cik, path in _stamps_files(settings).items():
        _to_old_layout(path)
        old[cik] = json.loads(path.read_bytes())["records"]
    assert old[APPLE][MISSING] == ["8-K", "", False, None]  # the sentinel
    return old


def _file(settings: Settings, cik: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_stamps_files(settings)[cik].read_bytes())
    return data


def _assert_old_stamps_unchanged(
    settings: Settings, old: dict[str, dict[str, list[object]]]
) -> None:
    """Every pre-existing record's form, document, iXBRL flag and acceptance
    (sentinels included) is byte-identical in the new file."""
    for cik, records in old.items():
        new = _file(settings, cik)["records"]
        assert records.keys() <= new.keys()
        for accession, row in records.items():
            assert json.dumps(new[accession][:4]) == json.dumps(row), accession


def test_reduce_keeps_items_verbatim_and_a_page_without_the_column_parses() -> None:
    records, _ = reduce_submissions(load("submissions_plain_issuer.json"))
    assert records[APPLE_202].items == "2.02,9.01"
    assert records[APPLE_507].items == "5.07"
    assert records[APPLE_10Q].items == ""
    assert (
        reduce_submissions(load("submissions_plain_issuer.json"))[0]["0001140361-26-035325"].items
        == "5.02"
    )
    with_items = _payload(int(APPLE), ("0000320193-26-000001", "8-K", KLX_AT))
    with_items["filings"]["recent"]["items"] = [""]  # type: ignore[index]
    assert reduce_submissions(with_items)[0]["0000320193-26-000001"].items == ""
    without = _payload(int(APPLE), ("0000320193-26-000001", "8-K", KLX_AT))
    [record] = reduce_submissions(without)[0].values()
    assert record.items is None
    assert record.accepted_at == datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC)


def test_the_stamps_cache_round_trips_items_under_the_new_layout(settings: Settings) -> None:
    _source(settings, _router()).filing_index()
    data = _file(settings, APPLE)
    assert (data["layout"], data["items_refreshed"]) == (STAMPS_LAYOUT, True)
    assert data["records"][APPLE_202][4] == "2.02,9.01"
    loaded = _source(settings, _router())._load_stamps(APPLE)
    assert loaded[APPLE_202].items == "2.02,9.01"
    assert loaded[APPLE_202].accepted_at == _recorded_acceptance()[APPLE_202]


def test_an_old_layout_cache_is_read_as_it_is_with_no_request(settings: Settings) -> None:
    old = _old_cache(settings)
    before = {cik: p.read_bytes() for cik, p in _stamps_files(settings).items()}
    router = _router(overrides=SETTLED)
    source = _source(settings, router)
    source.filing_index()
    # The stamping path neither refreshes nor rewrites: no submissions request.
    assert _submission_urls(router) == []
    assert {cik: p.read_bytes() for cik, p in _stamps_files(settings).items()} == before
    assert source._load_stamps(APPLE)[APPLE_202].items is None
    assert old


def test_restamping_an_old_layout_cik_keeps_every_old_stamp_byte_identical(
    settings: Settings,
) -> None:
    old = _old_cache(settings)
    path = _stamps_files(settings)[APPLE]
    data = json.loads(path.read_bytes())
    dropped = data["records"].pop(APPLE_202)
    path.write_bytes(json.dumps(data).encode("utf-8"))
    del old[APPLE][APPLE_202]
    router = _router(overrides=SETTLED)
    _source(settings, router).filing_index()
    assert "CIK0000320193.json" in _submission_urls(router)
    new = _file(settings, APPLE)
    assert (new["layout"], new["items_refreshed"]) == (STAMPS_LAYOUT, False)
    assert new["records"][APPLE_202] == [*dropped, "2.02,9.01"]  # the new stamp carries it
    _assert_old_stamps_unchanged(settings, old)
    # The old records keep `items` unknown until the refresh.
    assert all(new["records"][a][4] is None for a in old[APPLE])


def test_the_refresh_writes_items_only_once_per_cik(settings: Settings) -> None:
    old = _old_cache(settings)
    router = _router(overrides=SETTLED)
    source = _source(settings, router)
    events = source.filing_events(APPLE)
    # One read; the older page is not needed (every wanted accession is in the
    # main payload, and the sentinel is never wanted).
    assert _submission_urls(router) == ["CIK0000320193.json"]
    _assert_old_stamps_unchanged(settings, old)
    data = _file(settings, APPLE)
    assert data["items_refreshed"] is True
    assert data["records"][MISSING] == ["8-K", "", False, None, None]  # sentinel untouched
    recorded = _recorded_acceptance()
    by_accession = {e.accession: e for e in events}
    assert by_accession[APPLE_202].items == "2.02,9.01"
    assert by_accession[APPLE_202].accepted_at == recorded[APPLE_202]
    assert by_accession[APPLE_507].items == "5.07"
    assert MISSING not in by_accession
    assert set(by_accession) == {APPLE_202, APPLE_507}
    assert events == sorted(events, key=lambda e: (e.accepted_at, e.accession))
    assert (source.filing_events_unstamped, source.filing_events_items_missing) == (1, 0)
    # Asked again, here or by a new instance: no request, the same answer.
    again_router = _router(overrides=SETTLED)
    assert _source(settings, again_router).filing_events(APPLE) == events
    assert source.filing_events(APPLE) == events
    assert _submission_urls(again_router) == []
    assert (source.filing_events_unstamped, source.filing_events_items_missing) == (1, 0)
    # Untouched CIKs stay in the old layout.
    assert "layout" not in _file(settings, ALPHABET)


def test_an_accession_without_items_after_the_refresh_is_terminal_and_counted(
    settings: Settings,
) -> None:
    old = _old_cache(settings)
    router = _router(overrides=SETTLED)
    for name in ("CIK0000320193.json", "CIK0000320193-submissions-001.json"):
        payload = load(SUBMISSIONS[APPLE][0 if name.endswith("0193.json") else 1])
        columns = payload["filings"]["recent"] if "filings" in payload else payload
        del columns["items"]
        router.add(SUBMISSIONS_URL + name, payload)
    source = _source(settings, router)
    assert source.filing_events(APPLE) == []
    eight_ks = sum(
        1 for row in old[APPLE].values() if row[0] in ("8-K", "8-K/A") and row[3] is not None
    )
    assert source.filing_events_items_missing == eight_ks > 0
    assert _file(settings, APPLE)["items_refreshed"] is True
    _assert_old_stamps_unchanged(settings, old)
    again = _router(overrides=SETTLED)
    assert _source(settings, again).filing_events(APPLE) == []
    assert _submission_urls(again) == []  # never re-fetched


def test_a_failed_refresh_read_marks_nothing_and_is_tried_again(settings: Settings) -> None:
    old = _old_cache(settings)
    router = _router(overrides=SETTLED)
    router.add(SUBMISSIONS_URL + "CIK0000320193.json", {})  # SEC's "nothing here"
    source = _source(settings, router)
    assert source.filing_events(APPLE) == []
    assert source.filing_events_items_missing == 0
    assert "layout" not in _file(settings, APPLE)
    assert json.loads(_stamps_files(settings)[APPLE].read_bytes())["records"] == old[APPLE]
    assert _source(settings, _router(overrides=SETTLED)).filing_events(APPLE)


def test_the_refresh_reads_the_cached_zip_with_no_request_under_reuse_cached(
    settings: Settings,
) -> None:
    old = _old_cache(settings)
    bulk = Path(settings.edgar.cache_dir) / "bulk" / "submissions.zip"
    bulk.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bulk, "w") as archive:
        main, page = SUBMISSIONS[APPLE]
        archive.write(FIXTURES / main, f"CIK{APPLE}.json")
        archive.write(FIXTURES / page, f"CIK{APPLE}-submissions-001.json")
    router = _router(overrides=SETTLED)
    source = EdgarFilingSource(
        settings, client=router.client(), clock=lambda: CLOCK, reuse_cached=True
    )
    events = source.filing_events(APPLE)
    assert router.urls == []
    assert events == _source(settings, _router(overrides=SETTLED)).filing_events(APPLE)
    assert {e.accession: e.items for e in events}[APPLE_202] == "2.02,9.01"
    _assert_old_stamps_unchanged(settings, old)


def test_a_cik_with_no_stamps_file_answers_nothing_and_fetches_nothing(
    settings: Settings,
) -> None:
    router = _router()
    source = _source(settings, router)
    assert source.filing_events(APPLE) == []
    assert router.urls == []


def test_event_forms_are_configurable_and_never_empty(settings: Settings) -> None:
    _source(settings, _router()).filing_index()
    source = EdgarFilingSource(settings, client=_router().client(), event_forms=["10-Q"])
    [event] = [e for e in source.filing_events(APPLE) if e.accession == APPLE_10Q]
    assert (event.form, event.items) == ("10-Q", "")
    assert {e.form for e in source.filing_events(APPLE)} == {"10-Q"}
    with pytest.raises(ValueError, match="event_forms"):
        EdgarFilingSource(settings, client=_router().client(), event_forms=[])


def test_clock_detector_reports_refresh_and_new_stamps_once(settings: Settings) -> None:
    source = _source(settings, _router())
    early = SubmissionRecord("early", "8-K", "doc.htm", False, datetime(2024, 1, 2, 20, tzinfo=UTC))
    late = SubmissionRecord("late", "8-K", "doc.htm", False, datetime(2024, 1, 3, 21, tzinfo=UTC))
    source._items_source = lambda cik, wanted: (  # type: ignore[method-assign]
        {
            "early": SubmissionRecord(
                "early", "8-K", "doc.htm", False, early.accepted_at, "2.02", date(2024, 1, 3)
            ),
            "late": SubmissionRecord(
                "late", "8-K", "doc.htm", False, late.accepted_at, "2.02", date(2024, 1, 2)
            ),
        },
        True,
    )
    source._refresh_items(APPLE, {"early": early, "late": late})
    assert source.filing_events_clock_suspect == 2
    assert source.filing_events_clock_suspect_accessions == ["early", "late"]


def test_per_cik_stamping_tolerates_bad_and_short_filing_dates(settings: Settings) -> None:
    bad, short, early, absent = (
        "0000320193-24-000101",
        "0000320193-24-000102",
        "0000320193-24-000103",
        "0000320193-24-000105",
    )
    payload = _payload(
        int(APPLE),
        (bad, "8-K", "2024-01-02T20:00:00"),
        (short, "8-K", "2024-01-02T20:00:00"),
        (early, "8-K", "2024-01-02T20:00:00"),
        (absent, "8-K", "2024-01-02T20:00:00"),
    )
    payload["filings"]["recent"]["filingDate"] = ["bad", "2024", "2024-01-03"]
    router = _router()
    router.add(f"{SUBMISSIONS_URL}CIK{APPLE}.json", payload)
    source = _source(settings, router)
    kept = {
        accession: (UnstampedFiling(APPLE, "Apple", "8-K", accession, date(2024, 1, 3)), (2024, 1))
        for accession in (bad, short, early, absent)
    }
    stamps = source._stamp({APPLE: kept})[APPLE]
    assert set(stamps) == {bad, short, early, absent}
    assert all(stamps[a].filing_date is None for a in (bad, short, absent))
    assert source.filing_events_clock_suspect_accessions == [early]


def test_bulk_stamping_runs_the_clock_detector(settings: Settings) -> None:
    accession = "0000320193-24-000104"
    payload = _payload(int(APPLE), (accession, "8-K", "2024-01-02T20:00:00"))
    payload["filings"]["recent"]["filingDate"] = ["2024-01-03"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"CIK{APPLE}.json", json.dumps(payload))
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    source = _source(settings, router)
    stamps: dict[str, dict[str, SubmissionRecord]] = {APPLE: {}}
    assert source._stamp_bulk({APPLE: {accession}}, stamps) == set()
    assert accession in stamps[APPLE]
    assert source.filing_events_clock_suspect_accessions == [accession]


def _bulk_zip(settings: Settings, *members: tuple[str, bytes]) -> None:
    bulk = Path(settings.edgar.cache_dir) / "bulk" / "submissions.zip"
    bulk.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bulk, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)


def test_the_refresh_from_a_moved_zip_never_moves_a_stamp(settings: Settings) -> None:
    """#1382 guard on T164c's refresh: a cached zip whose `acceptanceDateTime`
    is moved early fills `items` only; every `accepted_at` stays the stamp
    already in the file, never the zip's early value."""
    old = _old_cache(settings)
    main, page = SUBMISSIONS[APPLE]
    _bulk_zip(
        settings,
        (f"CIK{APPLE}.json", _moved_early(main)),
        (f"CIK{APPLE}-submissions-001.json", _moved_early(page)),
    )
    router = _router(overrides=SETTLED)
    source = EdgarFilingSource(
        settings, client=router.client(), clock=lambda: CLOCK, reuse_cached=True
    )
    events = source.filing_events(APPLE)
    recorded = _recorded_acceptance()
    assert events and all(e.accepted_at == recorded[e.accession] for e in events)
    assert {e.accession: e.items for e in events}[APPLE_202] == "2.02,9.01"
    _assert_old_stamps_unchanged(settings, old)


def test_a_stale_cached_zip_fills_what_it_has_and_leaves_the_cik_unrefreshed(
    settings: Settings,
) -> None:
    old = _old_cache(settings)
    stale = load(SUBMISSIONS[APPLE][0])
    columns = stale["filings"]["recent"]
    keep = [i for i, a in enumerate(columns["accessionNumber"]) if a != APPLE_202]
    for key, values in columns.items():
        columns[key] = [values[i] for i in keep]
    stale["filings"]["files"] = []
    _bulk_zip(settings, (f"CIK{APPLE}.json", json.dumps(stale).encode()))
    router = _router(overrides=SETTLED)
    source = EdgarFilingSource(
        settings, client=router.client(), clock=lambda: CLOCK, reuse_cached=True
    )
    events = source.filing_events(APPLE)
    assert router.urls == []
    assert APPLE_202 not in {e.accession for e in events}
    assert {e.accession for e in events} == {APPLE_507}
    assert source.filing_events_items_missing == 0  # not terminal: not refreshed
    data = _file(settings, APPLE)
    assert data["items_refreshed"] is False
    assert data["records"][APPLE_202][4] is None
    _assert_old_stamps_unchanged(settings, old)
    # A later per-CIK refresh completes it and marks it refreshed.
    later = _source(settings, _router(overrides=SETTLED))
    assert {e.accession for e in later.filing_events(APPLE)} == {APPLE_202, APPLE_507}
    assert _file(settings, APPLE)["items_refreshed"] is True
    _assert_old_stamps_unchanged(settings, old)


def test_an_empty_zip_page_is_counted_not_recorded_and_marks_nothing(settings: Settings) -> None:
    old = _old_cache(settings)
    path = _stamps_files(settings)[APPLE]
    data = json.loads(path.read_bytes())
    data["records"]["0000320193-15-000001"] = ["8-K", "doc.htm", False, "2015-01-27T21:30:00+00:00"]
    path.write_bytes(json.dumps(data).encode("utf-8"))  # wanted only on the older page
    main, _ = SUBMISSIONS[APPLE]
    _bulk_zip(
        settings,
        (f"CIK{APPLE}.json", (FIXTURES / main).read_bytes()),
        (f"CIK{APPLE}-submissions-001.json", b"{}"),
    )
    source = EdgarFilingSource(
        settings, client=_router().client(), clock=lambda: CLOCK, reuse_cached=True
    )
    source.filing_events(APPLE)
    assert source.submissions_bulk_empty == 1
    assert not source.validation_failures
    assert _file(settings, APPLE)["items_refreshed"] is False
    _assert_old_stamps_unchanged(settings, old)


def test_the_missing_count_waits_for_the_refresh_that_succeeds(settings: Settings) -> None:
    _old_cache(settings)
    router = _router(overrides=SETTLED)
    payload = load(SUBMISSIONS[APPLE][0])
    del payload["filings"]["recent"]["items"]
    answers = iter([{}, payload])
    router.add(
        SUBMISSIONS_URL + "CIK0000320193.json",
        lambda request: httpx.Response(200, json=next(answers)),
    )
    source = _source(settings, router)
    assert source.filing_events(APPLE) == []  # `{}`: not refreshed, nothing counted
    assert source.filing_events_items_missing == 0
    source._submissions.clear()  # a later run's fresh memo
    assert source.filing_events(APPLE) == []
    assert source.filing_events_items_missing == 2  # both 8-Ks terminal, counted once


# --- #1541 (T178): `edgar.exchange_axis_listings` reaches both parsers ------------

_ANSYS = "0001013462"
_ANSYS_ACCESSION = "0001013462-19-000023"
_ANSYS_ACCEPTED = datetime(2019, 7, 31, 20, 30, tzinfo=UTC)
#: The spec's ANSYS shape with its member patched to name NYSE while the value
#: says Nasdaq: a listing kept and counted (spec open question 1).
_MISMATCHED_ANSYS = (
    (FIXTURES / "cover_exchange_axis" / "ansys_10q_2019q2.htm")
    .read_bytes()
    .replace(b'">exch:XNGS<', b'">exch:XNYS<')
)


def _axis_fsn_zip() -> bytes:
    from test_edgar_fsn import _dim, _fsn_zip_bytes, _sub, _txt

    adsh, dimh = _ANSYS_ACCESSION, "0x1dc0af108c94fa3ada3018ad13966aeb"
    txt = [
        _txt(adsh, tag, value, dimh=dimh, dimn="1")
        for tag, value in (
            ("Security12bTitle", "Common Stock"),
            ("TradingSymbol", "ANSS"),
            ("SecurityExchangeName", "NYSE"),  # the member names Nasdaq
        )
    ]
    return _fsn_zip_bytes(
        [_sub(adsh, "1013462", "10-Q")], [], txt, [_dim(dimh, "EntityListingsExchange=XNAS;")]
    )


def _axis_source(tmp_path: Path, on: bool) -> tuple[EdgarFilingSource, EdgarRouter]:
    from test_edgar_fsn import FSN_PAGE_URL, _fsn_zip_url

    router = EdgarRouter()
    router.add(FSN_PAGE_URL, f'<html><body><a href="{_fsn_zip_url("2019q3")}">x</a></body></html>')
    router.add(_fsn_zip_url("2019q3"), _axis_fsn_zip())
    nodash = _ANSYS_ACCESSION.replace("-", "")
    router.add(
        f"https://www.sec.gov/Archives/edgar/data/{int(_ANSYS)}/{nodash}/anss-10q.htm",
        _MISMATCHED_ANSYS,
    )
    settings = edgar_settings(tmp_path, fsn_first_year=2019, exchange_axis_listings=on)
    source = EdgarFilingSource(settings, client=router.client())
    source._filing_index_ran = True
    return source, router


@pytest.mark.parametrize("on", [False, True])
def test_the_switch_reaches_the_fsn_extraction_and_its_count(tmp_path: Path, on: bool) -> None:
    source, _ = _axis_source(tmp_path, on)
    source._ensure_fsn()
    [record] = source._load_fsn_cache(_ANSYS).values()
    assert [(i.ticker, i.exchange) for i in record.listings] == ([("ANSS", "NYSE")] if on else [])
    assert source.fsn_exchange_axis_mismatch == (1 if on else 0)


@pytest.mark.parametrize("on", [False, True])
def test_the_switch_reaches_the_per_document_parse_and_its_count(tmp_path: Path, on: bool) -> None:
    source, _ = _axis_source(tmp_path, on)
    parsed = source._fetch_cover_page(_ANSYS, _ANSYS_ACCESSION, "anss-10q.htm", _ANSYS_ACCEPTED)
    assert [(i.ticker, i.exchange) for i in parsed.cover.listings] == (
        [("ANSS", "NASDAQ")] if on else []
    )
    assert source.cover_exchange_axis_mismatch == (1 if on else 0)


# --- the FSN cache round-trips the symbol-only tag (#1624, T184) ------------


def _symbol_only_zip() -> bytes:
    """One FSN period with a complete listing, a symbol that names no
    exchange (T184's symbol-only tag) and a filing-less group."""
    from test_edgar_fsn import _dim, _fsn_zip_bytes, _sub, _txt

    accession = "0001193125-19-000077"
    complete, only = "0x1", "0x2"
    txt = [
        _txt(accession, "Security12bTitle", "Common Stock", dimh=complete, dimn="1"),
        _txt(accession, "TradingSymbol", "ANSS", dimh=complete, dimn="1"),
        _txt(accession, "SecurityExchangeName", "NASDAQ", dimh=complete, dimn="1"),
        _txt(accession, "TradingSymbol", "ZEN", dimh=only, dimn="1"),  # no exchange
    ]
    return _fsn_zip_bytes(
        [_sub(accession, "1013462", "10-Q")],
        [],
        txt,
        [_dim(complete, "ClassOfStock=CommonClassA;"), _dim(only, "ClassOfStock=Units;")],
    )


def test_the_fsn_cache_round_trips_the_symbol_only_tag(tmp_path: Path) -> None:
    """A filing parsed from a cached period must read back the symbol-only
    tag its parse recorded (#1624): without the round-trip a reload finds
    none, and a filename reading that asks for the tag would miss it."""
    from test_edgar_fsn import FSN_PAGE_URL, _fsn_page_html, _fsn_zip_url

    router = EdgarRouter()
    router.add(FSN_PAGE_URL, _fsn_page_html("2019q3"))
    router.add(_fsn_zip_url("2019q3"), _symbol_only_zip())
    settings = edgar_settings(tmp_path, fsn_first_year=2019)
    source = EdgarFilingSource(settings, client=router.client())
    source._filing_index_ran = True
    source._ensure_fsn()
    parsed = source._load_fsn_cache(_ANSYS)
    [record] = parsed.values()
    assert record.symbol_only == ("ZEN",)
    assert [(item.ticker, item.exchange) for item in record.listings] == [("ANSS", "NASDAQ")]

    # A fresh source reads the same period back from the cache.
    reopened = EdgarFilingSource(settings, client=router.client())
    reopened._filing_index_ran = True
    assert reopened._load_fsn_cache(_ANSYS) == parsed


def test_a_cache_entry_written_before_the_field_reads_no_tag(tmp_path: Path) -> None:
    """An entry laid out before #1624 (no `symbol_only` key) reloads with
    none recorded: the cache kept what its own parser wrote, the release's
    re-extraction rewrites it with the tag its filing carries, and no
    reading this cache already served is changed by the field."""
    settings = edgar_settings(tmp_path)
    source = EdgarFilingSource(settings)
    path = source._fsn_cache_path(_ANSYS)
    path.parent.mkdir(parents=True, exist_ok=True)
    accession = "0001193125-19-000077"
    path.write_text(
        json.dumps(
            {
                "version": FSN_VERSION,
                "cik": _ANSYS,
                "records": {
                    accession: {
                        "form": "10-Q",
                        "sic": 3711,
                        "listings": [["Common Stock", "ANSS", "NASDAQ"]],
                        "shares": [["", "12", "2019-06-30"]],
                    }
                },
            }
        )
    )
    [record] = source._load_fsn_cache(_ANSYS).values()
    assert (record.accession, record.form) == (accession, "10-Q")
    assert (record.symbol_only, record.incomplete_listings) == ((), 0)
    assert [(item.ticker, item.exchange) for item in record.listings] == [("ANSS", "NASDAQ")]


@pytest.mark.parametrize("recorded", ["ZEN", {"tag": "ZEN"}, ["ZEN", 7], [["ZEN"]]])
def test_a_cache_entry_whose_symbol_only_is_not_strings_refuses_to_read(
    tmp_path: Path, recorded: Any
) -> None:
    """safety-reviewer (#1624): the cache file is a file on disk. An entry
    whose `symbol_only` is not the list of strings the writer wrote is
    corrupt, and is refused rather than read as the letters of one symbol or
    as symbols that are not strings — the loader's own answer for an
    unreadable file, no records, and a named error when it is strict."""
    settings = edgar_settings(tmp_path)
    source = EdgarFilingSource(settings)
    path = source._fsn_cache_path(_ANSYS)
    path.parent.mkdir(parents=True, exist_ok=True)
    accession = "0001193125-19-000077"
    path.write_text(
        json.dumps(
            {
                "version": FSN_VERSION,
                "cik": _ANSYS,
                "records": {
                    accession: {
                        "form": "10-Q",
                        "sic": 3711,
                        "listings": [["Common Stock", "ANSS", "NASDAQ"]],
                        "shares": [["", "12", "2019-06-30"]],
                        "symbol_only": recorded,
                    }
                },
            }
        )
    )
    assert source._load_fsn_cache(_ANSYS) == {}
    with pytest.raises(ValueError, match="unreadable"):
        source._load_fsn_cache(_ANSYS, strict=True)


# --- the release's cache versions (T181, #1541) -----------------------------


# --- filing-name listings: the fetches and the per-CIK cache (T184b, #1597) ---

_FN_CIK = APPLE
_FN_A, _FN_B, _FN_C = "0000320193-16-000010", "0000320193-17-000020", "0000320193-18-000030"
_FN_BEFORE = "0000320193-14-000005"  # a 10-K before filename_listings_since
_FN_AFTER = "0000320193-19-000050"  # a 10-K after the first cover page
_FN_COVER = "0000320193-19-000040"  # the first cover-page filing (a 10-Q)
_FN_25_NASDAQ, _FN_25_NYSE, _FN_25_COLD = (
    "0001354457-18-000500",
    "0001354457-18-000501",
    "0001354457-18-000502",
)
_FN_AT = {
    _FN_BEFORE: datetime(2014, 10, 1, 20, 0, tzinfo=UTC),
    _FN_A: datetime(2016, 10, 26, 20, 5, tzinfo=UTC),
    _FN_B: datetime(2017, 11, 3, 20, 5, tzinfo=UTC),
    _FN_C: datetime(2018, 11, 5, 21, 5, tzinfo=UTC),
    _FN_25_NASDAQ: datetime(2018, 12, 3, 14, 0, tzinfo=UTC),
    _FN_25_NYSE: datetime(2018, 12, 4, 14, 0, tzinfo=UTC),
    _FN_25_COLD: datetime(2018, 12, 5, 14, 0, tzinfo=UTC),
    _FN_COVER: datetime(2019, 8, 1, 20, 0, tzinfo=UTC),
    _FN_AFTER: datetime(2019, 11, 1, 20, 0, tzinfo=UTC),
}
_FN_FORM = {
    _FN_25_NASDAQ: "25-NSE",
    _FN_25_NYSE: "25-NSE",
    _FN_25_COLD: "25",
    _FN_COVER: "10-Q",
}


def _fn_url(accession: str, name: str, cik: str = _FN_CIK) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{name}"


def _fn_router() -> EdgarRouter:
    """Index and document routes for the synthetic CIK: A reads the plain
    instance and a NASDAQ table, B the `_htm` instance and the two-venue
    table, C has no instance (and so no document route: asking is a failure),
    the first cover filing and the NASDAQ Form 25 their recorded indexes.
    The before/after 10-Ks and the cold Form 25 have no route at all."""
    router = EdgarRouter()
    router.add(
        _fn_url(_FN_A, "index.json"), (FIXTURES / "index_10k_plain_instance.json").read_bytes()
    )
    router.add(
        _fn_url(_FN_A, "doc.htm"),
        gzip.decompress((FIXTURES / "filing_plain_issuer_aapl-20250927.htm.gz").read_bytes()),
    )
    router.add(
        _fn_url(_FN_B, "index.json"), (FIXTURES / "index_10k_htm_instance.json").read_bytes()
    )
    router.add(
        _fn_url(_FN_B, "doc.htm"),
        gzip.decompress((FIXTURES / "filing_dual_exchange_d360924d10ka.htm.gz").read_bytes()),
    )
    router.add(_fn_url(_FN_C, "index.json"), (FIXTURES / "index_10k_no_instance.json").read_bytes())
    router.add(
        _fn_url(_FN_COVER, "index.json"), (FIXTURES / "index_10k_htm_instance.json").read_bytes()
    )
    router.add(
        _fn_url(_FN_25_NASDAQ, "index.json"), (FIXTURES / "index_25nse_nasdaq.json").read_bytes()
    )
    router.add(
        _fn_url(_FN_25_NYSE, "index.json"), (FIXTURES / "index_25nse_nyse.json").read_bytes()
    )
    return router


def _fn_source(
    tmp_path: Path, router: EdgarRouter, *, on: bool = True, **edgar: Any
) -> EdgarFilingSource:
    """A source over the synthetic CIK's stamps, FSN cache and delisting
    cache, written straight into `tmp_path / "cache"` (the fetch pass that
    fills them is other tasks'); a second source over the same directory is a
    re-run."""
    settings = edgar_settings(tmp_path / "cache", filename_listings=on, **edgar)
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: CLOCK)
    source._fsn_ready = True
    source._fsn_loaded_periods = ("2019q3",)
    if source._load_stamps(_FN_CIK):
        return source
    records = {
        accession: SubmissionRecord(
            accession, _FN_FORM.get(accession, "10-K"), "doc.htm", False, accepted_at
        )
        for accession, accepted_at in _FN_AT.items()
    }
    source._save_stamps(_FN_CIK, records)
    own_listing = [["Common Stock", "AAPL", "NASDAQ"]]
    fsn = {
        # A tags its own symbol; B's entry predates #1624 (no symbol_only key).
        _FN_A: {"form": "10-K", "sic": None, "listings": [], "shares": [], "symbol_only": ["AAPL"]},
        _FN_B: {"form": "10-K", "sic": None, "listings": [], "shares": []},
        _FN_COVER: {"form": "10-Q", "sic": None, "listings": own_listing, "shares": []},
    }
    edgar_raw.write_atomic(
        source._fsn_cache_path(_FN_CIK),
        json.dumps(
            {"version": edgar_source_module.FSN_VERSION, "cik": _FN_CIK, "records": fsn}
        ).encode(),
    )
    for accession in (_FN_25_NASDAQ, _FN_25_NYSE):
        source._save_delisting_cache(
            accession,
            DelistingFiling(
                _FN_CIK,
                "25-NSE",
                "Common Stock, $0.01 par value per share",
                "NASDAQ",
                accession,
                _FN_AT[accession],
                date(2018, 12, 13),
            ),
        )
    return source


def _fn_keys(listings: list[Any]) -> list[tuple[str, str, str, str | None, str | None]]:
    return [(e.accession, e.form, e.ticker, e.exchange, e.dei_symbol) for e in listings]


def test_filename_listings_reads_the_window_from_the_extract_and_the_stamps(
    tmp_path: Path,
) -> None:
    router = _fn_router()
    source = _fn_source(tmp_path, router)
    listings = source.filename_listings(_FN_CIK)

    assert _fn_keys(listings) == [
        (_FN_A, "10-K", "aapl", "NASDAQ", "AAPL"),
        (_FN_B, "10-K", "a10-k20199282019", "NASDAQ", None),
        (_FN_25_NASDAQ, "25-NSE", "spdc", "NASDAQ", None),
        (_FN_COVER, "10-Q", "a10-k20199282019", None, None),
    ]
    # accepted_at is the filing's own stamp; valid_from its New York session.
    by_accession = {e.accession: e for e in listings}
    assert by_accession[_FN_A].accepted_at == _FN_AT[_FN_A]
    assert by_accession[_FN_A].valid_from == date(2016, 10, 26)
    assert by_accession[_FN_B].valid_from == date(2017, 11, 3)
    assert by_accession[_FN_A].class_title.startswith("Common Stock")  # type: ignore[union-attr]
    assert by_accession[_FN_25_NASDAQ].class_title == "Common Stock, $0.01 par value per share"
    # The window: the 2014 10-K (before `since`), the one after the first
    # cover page, the NYSE exhibit (no ticker) and the uncached Form 25 are out.
    assert _FN_BEFORE not in by_accession and _FN_AFTER not in by_accession
    assert _FN_25_NYSE not in by_accession and _FN_25_COLD not in by_accession
    assert (
        source.filename_10ks_read,
        source.filename_no_instance,
        source.filename_no_exchange,
        source.filename_form25_read,
        source.filename_form25_no_exhibit,
        source.filename_form25_uncached,
    ) == (3, 1, 0, 1, 1, 1)
    assert (source.filename_index_fetches, source.filename_document_fetches) == (6, 2)
    # One index per accession read, one document per 10-K with an instance.
    urls = [u for u in router.urls if "/Archives/" in u]
    assert sorted(urls) == sorted(
        [
            _fn_url(a, "index.json")
            for a in (_FN_A, _FN_B, _FN_C, _FN_COVER, _FN_25_NASDAQ, _FN_25_NYSE)
        ]
        + [_fn_url(_FN_A, "doc.htm"), _fn_url(_FN_B, "doc.htm")]
    )


def test_filename_cache_holds_only_the_extract_and_a_rerun_makes_no_request(
    tmp_path: Path,
) -> None:
    router = _fn_router()
    first = _fn_source(tmp_path, router).filename_listings(_FN_CIK)
    asked = len(router.urls)

    cache_dir = tmp_path / "cache" / "filename" / "v1"
    assert [p.name for p in cache_dir.iterdir()] == [f"{_FN_CIK}.json"]
    stored = json.loads((cache_dir / f"{_FN_CIK}.json").read_text())
    assert set(stored) == {"version", "cik", "records"}
    assert stored["records"][_FN_A]["name"] == "aapl"
    assert stored["records"][_FN_A]["pairs"][0][1] == "NASDAQ"
    assert stored["records"][_FN_C] == {
        "name": None,
        "pairs": [],
        "document_read": False,
    }  # no instance, still cached
    assert max(len(json.dumps(r)) for r in stored["records"].values()) < 2000  # no document kept
    # No downloaded document anywhere under the cache directory.
    assert {p.suffix for p in (tmp_path / "cache").rglob("*") if p.is_file()} == {".json"}

    again = _fn_source(tmp_path, router)
    assert again.filename_listings(_FN_CIK) == first
    assert len(router.urls) == asked
    assert (again.filename_index_fetches, again.filename_document_fetches) == (0, 0)
    assert again.filename_10ks_read == 3  # the readings are counted, the fetches are not


def test_filename_switch_off_makes_no_request_and_writes_no_cache(tmp_path: Path) -> None:
    router = _fn_router()
    source = _fn_source(tmp_path, router, on=False)
    assert source.filename_listings(_FN_CIK) == []
    assert router.urls == []
    assert not (tmp_path / "cache" / "filename").exists()
    assert source.filename_index_fetches == 0


def test_filename_fsn_entry_without_symbol_only_is_counted_not_read_as_no_tag(
    tmp_path: Path,
) -> None:
    source = _fn_source(tmp_path, _fn_router())
    listings = {e.accession: e for e in source.filename_listings(_FN_CIK)}
    assert listings[_FN_A].dei_symbol == "AAPL"  # recorded and equal to the prefix's case
    assert listings[_FN_B].dei_symbol is None  # B's entry never recorded the field
    assert source.filename_fsn_no_symbol_only == 1  # B, named, never silent
    assert source.filename_fsn_missing == 0


def test_filename_tag_that_names_another_symbol_is_the_filings_dei_symbol(
    tmp_path: Path,
) -> None:
    source = _fn_source(tmp_path, _fn_router())
    path = source._fsn_cache_path(_FN_CIK)
    data = json.loads(path.read_text())
    data["records"][_FN_B]["symbol_only"] = ["ZEN"]
    path.write_text(json.dumps(data))
    listings = {e.accession: e for e in source.filename_listings(_FN_CIK)}
    assert listings[_FN_B].dei_symbol == "ZEN"
    assert source.filename_fsn_no_symbol_only == 0


def test_filename_ten_k_missing_from_fsn_is_counted_apart(tmp_path: Path) -> None:
    source = _fn_source(tmp_path, _fn_router())
    path = source._fsn_cache_path(_FN_CIK)
    data = json.loads(path.read_text())
    del data["records"][_FN_B]
    path.write_text(json.dumps(data))
    source.filename_listings(_FN_CIK)
    assert (source.filename_fsn_missing, source.filename_fsn_no_symbol_only) == (1, 0)


@pytest.mark.parametrize("broken", ["index_404", "index_malformed", "document_404"])
def test_filename_failed_fetch_is_counted_cached_nowhere_and_asked_again(
    tmp_path: Path, broken: str
) -> None:
    router = _fn_router()
    if broken == "index_404":
        router.add(_fn_url(_FN_A, "index.json"), 404)
    elif broken == "index_malformed":
        router.add(_fn_url(_FN_A, "index.json"), b'{"directory": {"item": "oops"}}')
    else:
        router.add(_fn_url(_FN_A, "doc.htm"), 404)
    source = _fn_source(tmp_path, router)
    listings = source.filename_listings(_FN_CIK)

    assert _FN_A not in {e.accession for e in listings}  # never a guessed ticker
    assert {_FN_B, _FN_25_NASDAQ, _FN_COVER} <= {e.accession for e in listings}
    assert source.failed_filings == 1
    stored = json.loads((tmp_path / "cache" / "filename" / "v1" / f"{_FN_CIK}.json").read_text())
    assert _FN_A not in stored["records"]

    router.add(
        _fn_url(_FN_A, "index.json"), (FIXTURES / "index_10k_plain_instance.json").read_bytes()
    )
    router.add(
        _fn_url(_FN_A, "doc.htm"),
        gzip.decompress((FIXTURES / "filing_plain_issuer_aapl-20250927.htm.gz").read_bytes()),
    )
    before = len(router.urls)
    healed = _fn_source(tmp_path, router).filename_listings(_FN_CIK)
    assert _FN_A in {e.accession for e in healed}
    asked = {u for u in router.urls[before:]}
    assert asked == {_fn_url(_FN_A, "index.json"), _fn_url(_FN_A, "doc.htm")}  # only the failed one


def test_filename_dei_symbol_survives_the_real_fsn_cache_writer(tmp_path: Path) -> None:
    """The tag reaches `FilenameListing.dei_symbol` through `_save_fsn_cache`,
    not through a hand-written key: without the writer's `symbol_only` every
    entry reads as "never recorded" and the filing's own tag is lost."""
    source = _fn_source(tmp_path, _fn_router())
    records = {
        accession: FsnFiling(accession, _FN_CIK, "10-K", None, (), (), symbol_only=tags)
        for accession, tags in ((_FN_A, ("AAPL",)), (_FN_B, ("ZEN",)))
    }
    records[_FN_COVER] = FsnFiling(
        _FN_COVER, _FN_CIK, "10-Q", None, (CoverListing("Common Stock", "AAPL", "NASDAQ"),), ()
    )
    source._save_fsn_cache(_FN_CIK, records)
    listings = {e.accession: e for e in source.filename_listings(_FN_CIK)}
    assert (listings[_FN_A].dei_symbol, listings[_FN_B].dei_symbol) == ("AAPL", "ZEN")
    assert source.filename_fsn_no_symbol_only == 0


def test_a_prefix_only_cache_entry_is_a_miss_for_a_ten_k_read(tmp_path: Path) -> None:
    """An accession first cached as the first cover filing (prefix only) and
    later asked as a 10-K is read again, not served with no 12(b) pairs."""
    router = _fn_router()
    source = _fn_source(tmp_path, router)
    source._save_filename_cache(
        _FN_CIK,
        {_FN_A: edgar_source_module._FilenameExtract("aapl", (), False)},
    )
    listing = {e.accession: e for e in source.filename_listings(_FN_CIK)}[_FN_A]
    assert listing.exchange == "NASDAQ"
    assert _fn_url(_FN_A, "doc.htm") in router.urls
    stored = json.loads((tmp_path / "cache" / "filename" / "v1" / f"{_FN_CIK}.json").read_text())
    assert stored["records"][_FN_A]["document_read"] is True


def test_filename_incomplete_ciks_names_a_partial_answer(tmp_path: Path) -> None:
    router = _fn_router()
    # The cold Form 25 has no delisting-cache entry: the answer is partial.
    source = _fn_source(tmp_path, router)
    source.filename_listings(_FN_CIK)
    assert source.filename_incomplete_ciks == {_FN_CIK}

    # The same Form 25 as the exchange's copy of another company's: skipped, complete.
    clean = _fn_source(tmp_path, router)
    clean._save_delisting_cache(
        _FN_25_COLD,
        DelistingFiling(KLX, "25", "Common Stock", "NASDAQ", _FN_25_COLD, _FN_AT[_FN_25_COLD]),
    )
    clean.filename_listings(_FN_CIK)
    assert clean.filename_incomplete_ciks == set()

    # A failed fetch makes it partial again.
    router.add(_fn_url(_FN_B, "index.json"), 404)
    failing = _fn_source(tmp_path / "other", router)
    failing.filename_listings(_FN_CIK)
    assert failing.filename_incomplete_ciks == {_FN_CIK}


class _CountedBody(httpx.SyncByteStream):
    def __init__(self, chunk: bytes, total: int) -> None:
        self._chunk, self._total, self.pulled = chunk, total, 0

    def __iter__(self) -> Any:
        for _ in range(self._total):
            self.pulled += 1
            yield self._chunk


def test_filename_document_read_stops_at_the_configured_cap(tmp_path: Path) -> None:
    router = _fn_router()
    body = _CountedBody(b"<p>front matter with no table</p>" * 30, total=2_000)
    router.add(_fn_url(_FN_A, "doc.htm"), lambda request: httpx.Response(200, stream=body))
    source = _fn_source(tmp_path, router, filename_document_max_bytes=5000)
    listings = {e.accession: e for e in source.filename_listings(_FN_CIK)}
    assert body.pulled <= 6  # ~1 KB chunks against a 5 KB cap, not 100,000
    assert listings[_FN_A].exchange is None  # the table was never reached: no exchange
    assert source.filename_no_exchange >= 1


def test_a_cached_v2_cover_page_is_no_longer_served(settings: Settings) -> None:
    """T181 (#1541): the release bumps `COVER_VERSION` to 3 (and `FSN_VERSION`
    to 3), so a per-document cover page cached under v2, parsed without the
    exchange-axis listings, is never served: it is fetched and parsed again."""
    assert (FSN_VERSION, COVER_VERSION) == (3, 3)
    accession = "0000320193-24-000123"
    old = Path(settings.edgar.cache_dir) / "cover" / "v2" / f"{accession}.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(
        json.dumps(
            {
                "version": 2,
                "accession": accession,
                "cik": "0000320193",
                "entity_cik": "0000320193",
                "listings": [],
                "facts": [],
            }
        )
    )
    source = _source(settings, EdgarRouter())
    assert source._load_cover_cache(accession) is None

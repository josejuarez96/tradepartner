"""The adapter's statement-backfill mode (#2872, #2837).

`EdgarFilingSource(..., reuse_cached=True, statement_backfill=True)` serves
`statement_facts` from local caches only: FSN from the cached manifests (no
listing, `HEAD` or extraction), stamps from the stored `filing_index` plus
the stamps cache (a missing stamp is skipped and counted, never fetched),
and a CIK absent from the cached `companyfacts.zip` whose statement cache
already holds an empty result under the same latest accession is carried
forward with no request. Each case is pinned against the nightly path
(`statement_backfill=False`), whose behaviour does not change.

Every request goes through an `EdgarRouter`, which fails the test on an
unrouted URL; the autouse socket fixture backs it up.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from edgar_transport import EdgarRouter, edgar_settings

from tradepartner.adapters import edgar_raw
from tradepartner.adapters import edgar_source as edgar_source_module
from tradepartner.adapters.edgar_source import (
    FSN_VERSION,
    STATEMENT_VERSION,
    EdgarFilingSource,
    SubmissionRecord,
)
from tradepartner.config import Settings
from tradepartner.store import filing_index as stored_index
from tradepartner.store import schema

CIK = "0000123456"
K2022 = "0000123456-23-000001"
K2022_AT = datetime(2023, 2, 24, 21, 30, tzinfo=UTC)
NEW = "0000123456-23-000050"  # in the index, no cached stamp
NEW_AT = datetime(2023, 5, 1, 20, 0, tzinfo=UTC)
FUTURE = "0000123456-24-000001"  # accepted after the adapter's clock
FUTURE_AT = datetime(2024, 3, 1, 21, 0, tzinfo=UTC)
OTHER = "0000999999-23-000001"  # another filer's accession, in a cached FSN manifest
CLOCK = datetime(2023, 12, 1, tzinfo=UTC)
API = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"


def _settings(tmp_path: Path) -> Settings:
    return edgar_settings(tmp_path / "cache", statement_facts_enabled=True, index_first_year=2020)


def _payload() -> dict[str, Any]:
    entry = {
        "start": "2022-01-01",
        "end": "2022-12-31",
        "val": 300,
        "accn": K2022,
        "fy": 2022,
        "fp": "FY",
        "form": "10-K",
        "filed": "2023-02-24",
    }
    return {
        "cik": int(CIK),
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [entry]}}}},
    }


def _bulk_zip(settings: Settings, members: dict[str, bytes]) -> None:
    path = Path(settings.edgar.cache_dir) / "bulk" / "companyfacts.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _index(*rows: tuple[str, str, datetime]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    listings = [
        stored_index.Listing(CIK, accession, form, "Co", at.date(), at, at.date(), True)
        for accession, form, at in rows
    ]
    stored_index.insert_listings(conn, listings, max(at for _a, _f, at in rows))
    return conn


def _source(
    settings: Settings, router: EdgarRouter, *, backfill: bool, rows: tuple[Any, ...] = ()
) -> EdgarFilingSource:
    """A fresh adapter (no FSN or `filing_index` flag set by hand) with the
    stored index bound and K2022's stamp cached."""
    source = EdgarFilingSource(
        settings,
        client=router.client(),
        clock=lambda: CLOCK,
        reuse_cached=True,
        statement_backfill=backfill,
    )
    source._save_stamps(
        CIK, {K2022: SubmissionRecord(K2022, "10-Q", "doc.htm", True, K2022_AT - timedelta(1))}
    )
    source.use_index(_index((K2022, "10-K", K2022_AT), *rows))
    return source


def _statement_file(settings: Settings) -> Path:
    root = Path(settings.edgar.cache_dir) / "statement_facts" / f"v{STATEMENT_VERSION}"
    return root / f"{CIK}.json"


def _write_statement_cache(settings: Settings, key: str, records: list[Any]) -> None:
    path = _statement_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": STATEMENT_VERSION, "cik": CIK, "key": key, "records": records}
    path.write_text(json.dumps({**data, "conflicts": []}))


def _fsn_periods_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def listed(**_kwargs: Any) -> list[str]:
        calls.append("listed")
        return []

    monkeypatch.setattr(edgar_raw, "fsn_periods", listed)
    return calls


def test_backfill_mode_needs_reuse_cached(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reuse_cached"):
        EdgarFilingSource(_settings(tmp_path), statement_backfill=True)


# --- FSN: cached manifests only ---------------------------------------------------


def test_backfill_loads_fsn_from_cached_manifests_with_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_payload()).encode()})
    fsn_root = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests"
    fsn_root.mkdir(parents=True)
    manifest = {
        "version": FSN_VERSION,
        "period": "2023q1",
        "accessions_extracted": [OTHER],
        "accessions_failed": [{"accession": NEW}],
    }
    (fsn_root / "2023q1.json").write_text(json.dumps(manifest))
    before = sorted(p.name for p in fsn_root.parent.rglob("*"))
    listed = _fsn_periods_spy(monkeypatch)
    router = EdgarRouter()
    source = _source(settings, router, backfill=True)
    records = source.statement_facts(CIK)  # `filing_index()` never ran: no guard trips
    assert [r.accession for r in records] == [K2022]
    assert router.urls == [] and source.requests == 0 and listed == []
    assert source._fsn_loaded_periods == ("2023q1",)
    assert source._fsn_extracted_accessions == frozenset({OTHER})
    assert source._fsn_failed_accessions == frozenset({NEW})
    assert sorted(p.name for p in fsn_root.parent.rglob("*")) == before  # nothing written


def test_nightly_still_lists_fsn_and_needs_filing_index_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin: without the mode, `_ensure_fsn` keeps its guard and its listing."""
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_payload()).encode()})
    listed = _fsn_periods_spy(monkeypatch)
    source = _source(settings, EdgarRouter(), backfill=False)
    with pytest.raises(RuntimeError, match="filing_index"):
        source.statement_facts(CIK)
    source._filing_index_ran = True
    source.statement_facts(CIK)
    assert listed == ["listed"]


# --- stamps: a missing one is skipped and counted ---------------------------------


def test_backfill_skips_and_counts_an_index_accession_with_no_cached_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_payload()).encode()})
    _fsn_periods_spy(monkeypatch)
    router = EdgarRouter()
    source = _source(settings, router, backfill=True, rows=((NEW, "10-Q", NEW_AT),))
    stamps_file = source._stamps_path(CIK)
    before = stamps_file.read_bytes()
    monkeypatch.setattr(source, "_fetch_submissions", lambda *_a: pytest.fail("fetched"))
    monkeypatch.setattr(source, "_sgml_checked", lambda *_a: pytest.fail("sgml"))
    stamps = source._read_stamps(CIK)
    assert set(stamps) == {K2022}  # NEW gets no stamp at all, never a run-time one
    assert stamps[K2022].accepted_at == K2022_AT  # the table's acceptance, not the cache's
    assert stamps[K2022].form == "10-K"
    source.statement_facts(CIK)
    source.statement_facts(CIK)  # a re-read counts nothing twice
    assert source.statement_stamps_missing == 1
    assert stamps_file.read_bytes() == before
    assert router.urls == [] and source.requests == 0


def test_backfill_never_sees_an_index_row_known_after_its_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No look-ahead: a row the table learned after the clock is neither
    stamped nor counted missing; the records are the clock-visible ones."""
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_payload()).encode()})
    _fsn_periods_spy(monkeypatch)
    source = _source(settings, EdgarRouter(), backfill=True, rows=((FUTURE, "10-K", FUTURE_AT),))
    assert set(source._read_stamps(CIK)) == {K2022}
    records = source.statement_facts(CIK)
    assert source.statement_stamps_missing == 0
    assert all(r.accepted_at is not None and r.accepted_at <= CLOCK for r in records)


def test_nightly_still_fetches_a_missing_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin: without the mode, a missing stamp goes to the submissions API."""
    settings = _settings(tmp_path)
    source = _source(settings, EdgarRouter(), backfill=False, rows=((NEW, "10-Q", NEW_AT),))
    asked: list[set[str]] = []

    def fetched(cik: str, wanted: set[str]) -> edgar_source_module._Submissions:
        asked.append(wanted)
        return edgar_source_module._Submissions({}, [], CLOCK)

    monkeypatch.setattr(source, "_fetch_submissions", fetched)
    source._read_stamps(CIK)
    assert asked == [{NEW}]
    assert source.statement_stamps_missing == 0


# --- #2837: a zip-absent CIK's cached empty result is carried forward ---------------


def _absent_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = _settings(tmp_path)
    _bulk_zip(settings, {"CIK0000999999.json": b"{}"})  # no member for CIK
    _fsn_periods_spy(monkeypatch)
    return settings


def test_backfill_carries_forward_a_zip_absent_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _absent_setup(tmp_path, monkeypatch)
    _write_statement_cache(settings, f"{K2022}|0123456789abcdef", [])  # the old tag lists
    router = EdgarRouter()
    source = _source(settings, router, backfill=True)
    assert source.statement_facts(CIK) == []
    assert source.statement_facts(CIK) == []
    assert router.urls == [] and source.requests == 0
    assert source.statement_absent_carried == 1
    data = json.loads(_statement_file(settings).read_text())
    assert data["key"] == source._statement_key(K2022)  # re-keyed under today's lists
    assert data["records"] == [] and data["conflicts"] == []


#: One cached statement row (the cache file's layout).
_ROW = ["revenue", "Revenues", "2022-01-01", "2022-12-31", 1.0, "USD", K2022, "2023-02-24"]


def _api_404(router: EdgarRouter) -> None:
    router.add(API, lambda _request: httpx.Response(404))


@pytest.mark.parametrize(
    ("backfill", "key", "records", "zip_member"),
    [
        pytest.param(False, f"{K2022}|0123456789abcdef", [], False, id="nightly"),
        pytest.param(True, f"{NEW}|0123456789abcdef", [], False, id="another-accession"),
        pytest.param(True, "|0123456789abcdef", [], False, id="unparsed-payload"),
        pytest.param(True, f"{K2022}|0123456789abcdef", [_ROW], False, id="non-empty"),
        pytest.param(True, f"{K2022}|0123456789abcdef", [], True, id="zip-member"),
    ],
)
def test_the_api_is_still_asked_outside_the_carry_forward_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill: bool,
    key: str,
    records: list[Any],
    zip_member: bool,
) -> None:
    """Pins: the nightly (mode off) asks the API as before; in the mode, a
    file under another accession, a non-empty file, or a CIK the zip holds
    (the trailing CIKs, #2837's 916) is asked as before."""
    settings = _settings(tmp_path)
    trailing = {"cik": int(CIK), "facts": {"us-gaap": {}}}  # holds no K2022: trails
    members = {f"CIK{CIK}.json": json.dumps(trailing).encode()} if zip_member else {}
    _bulk_zip(settings, {"CIK0000999999.json": b"{}", **members})
    _fsn_periods_spy(monkeypatch)
    _write_statement_cache(settings, key, records)
    router = EdgarRouter()
    _api_404(router)
    source = _source(settings, router, backfill=backfill)
    source._filing_index_ran = True  # the nightly's FSN guard; the mode needs none
    source.statement_facts(CIK)
    assert router.urls == [API]
    assert source.statement_absent_carried == 0


def test_carried_forward_and_404_results_write_the_same_cache_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The carry-forward writes exactly what a fresh 404 would have written."""
    carried_settings = _absent_setup(tmp_path / "a", monkeypatch)
    _write_statement_cache(carried_settings, f"{K2022}|0123456789abcdef", [])
    _source(carried_settings, EdgarRouter(), backfill=True).statement_facts(CIK)
    asked_settings = _absent_setup(tmp_path / "b", monkeypatch)
    router = EdgarRouter()
    _api_404(router)
    _source(asked_settings, router, backfill=True).statement_facts(CIK)
    assert router.urls == [API]
    carried = json.loads(_statement_file(carried_settings).read_text())
    asked = json.loads(_statement_file(asked_settings).read_text())
    assert carried == asked


def test_records_are_stamped_from_the_table_never_the_filed_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_payload()).encode()})
    _fsn_periods_spy(monkeypatch)
    records = _source(settings, EdgarRouter(), backfill=True).statement_facts(CIK)
    assert records and {r.accepted_at for r in records} == {K2022_AT}
    assert {r.filed for r in records} == {date(2023, 2, 24)}  # filed_on is never the clock

"""The bulk and index inputs record onto the validation collector (#578
part 2): a quarter's `form.idx`, a `submissions.zip` member or older page,
and an FSN period whose extraction or parse fails whole. Each is recorded
once and is absent for the rest of the pass; `ingest._prefetch`'s gate then
fails the run before any store write (the end-to-end test at the bottom,
#806 item 2). `companyfacts.zip` members are tested in
`test_edgar_source_cik.py`, next to the rest of the company-facts path.
"""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from edgar_transport import FIXTURES, SUBMISSIONS, edgar_settings, index_header
from test_edgar_failures import _NoPrices
from test_edgar_fsn import (
    _fsn_zip_bytes,
    _num,
    _one_period_zip,
    _router_with_fsn,
    _source_ready,
    _sub,
    _txt,
)
from test_edgar_fsn import _settings as _fsn_settings
from test_edgar_source import (
    ALPHABET,
    APPLE,
    BULK_URL,
    MISSING,
    _router,
    _source,
    _submission_urls,
    _synthetic,
)

from tradepartner.adapters.edgar_source import FSN_VERSION, EdgarFilingSource
from tradepartner.adapters.edgar_validation import ValidationFailure
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, ingest_session

#: A data row (it names `edgar/data/`) that `parse_filing_index` refuses.
BAD_ROW = "10-K garbage that names edgar/data/ but has no columns\n"


def _failures(source: EdgarFilingSource) -> list[tuple[str, str]]:
    return [(f.input, f.key) for f in source.validation_failures]


# --- form.idx ----------------------------------------------------------------


def test_a_quarter_that_does_not_parse_is_recorded_once_and_skipped(tmp_path: Path) -> None:
    """The bad quarter's rows are absent (here it has none to lose, so the
    entries match a clean run); it is recorded once although `filing_index`
    walks the quarters twice and is called twice; its cached file is kept
    for the owner to inspect, and the next instance records it again."""
    settings = edgar_settings(tmp_path / "cache")
    bad = {(2025, 2): index_header() + BAD_ROW}
    source = _source(settings, _router(overrides=bad))
    entries = source.filing_index()
    assert entries == _source(edgar_settings(tmp_path / "clean"), _router()).filing_index()
    source.filing_index()
    [failure] = source.validation_failures
    assert (failure.input, failure.key) == ("form.idx", "2025-QTR2")
    assert failure.error.startswith("ValueError: form.idx row does not parse")
    cached = Path(settings.edgar.cache_dir) / "index" / "2025-QTR2.idx.gz"
    assert BAD_ROW in gzip.decompress(cached.read_bytes()).decode()
    again = _source(settings, _router())
    again.filing_index()  # served from the bad cached file, recorded again
    assert _failures(again) == [("form.idx", "2025-QTR2")]


def test_rows_of_an_unparsed_quarter_are_absent(tmp_path: Path) -> None:
    """A quarter holding real rows plus one bad row contributes none of them."""
    settings = edgar_settings(tmp_path / "cache")
    recorded = (FIXTURES / "filing_index_2024_qtr1.txt").read_text()
    source = _source(settings, _router(overrides={(2024, 1): recorded + BAD_ROW}))
    entries = source.filing_index()
    assert _failures(source) == [("form.idx", "2024-QTR1")]
    clean = _source(edgar_settings(tmp_path / "clean"), _router()).filing_index()
    assert entries == [e for e in clean if e.accepted_at >= datetime(2024, 4, 1, tzinfo=UTC)]
    assert entries != clean


# --- submissions.zip -----------------------------------------------------------


def test_a_bad_submissions_member_or_page_is_recorded_and_never_topped_up(
    tmp_path: Path,
) -> None:
    """Alphabet's member is not JSON and Apple's older page is not an
    object: both are recorded, neither CIK is asked of the per-CIK API
    (its answer would likely share the shape, #599), so their rows stay
    unstamped and nothing is cached for them; KLX, absent from the zip, is
    stamped per CIK as before."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        main, _page = SUBMISSIONS[APPLE]
        bulk.write(FIXTURES / main, f"CIK{APPLE}.json")
        bulk.writestr(f"CIK{APPLE}-submissions-001.json", "[1]")
        bulk.writestr(f"CIK{ALPHABET}.json", "not json")
    router = _router()
    router.add(BULK_URL, buffer.getvalue())
    settings = edgar_settings(tmp_path / "cache", bulk_stamp_threshold_ciks=2)
    source = _source(settings, router)
    entries = source.filing_index()
    assert sorted(_failures(source)) == [
        ("submissions.zip member", f"CIK{APPLE}-submissions-001.json"),
        ("submissions.zip member", f"CIK{ALPHABET}.json"),
    ]
    assert _submission_urls(router) == ["CIK0001738827.json"]
    assert {e.cik for e in entries} == {"0001738827"}
    unstamped = {u.cik for u in source.unstamped_filings}
    assert {APPLE, ALPHABET} <= unstamped
    assert MISSING in {u.accession for u in source.unstamped_filings}
    stamps = Path(settings.edgar.cache_dir) / "stamps"
    assert not list(stamps.glob(f"*/{APPLE}.json")) and not list(stamps.glob(f"*/{ALPHABET}.json"))
    assert source.submissions_bulk_empty == 0


# --- FSN periods -------------------------------------------------------------


def _missing_member_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sub.tsv", "adsh\tcik\tsic\tform\n")
    return buffer.getvalue()


def _malformed_num_zip() -> bytes:
    """A `num.tsv` line with too few fields: DuckDB fails the whole read
    (#455, #498), and `num` has no free-text column to fold."""
    good = _fsn_zip_bytes(
        [_sub("0000000012-15-000001", "12", "10-K")],
        [_num("0000000012-15-000001", "EntityCommonStockSharesOutstanding", "1", "20150101")],
        [_txt("0000000012-15-000001", "TradingSymbol", "TCK")],
        [],
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(buffer, "w") as archive:
        for name in source.namelist():
            body = source.read(name)
            if name == "num.tsv":
                body += b"0000000012-15-000002\tEntityCommonStockSharesOutstanding\n"
            archive.writestr(name, body)
    return buffer.getvalue()


def test_an_fsn_period_that_fails_whole_is_recorded_and_absent(tmp_path: Path) -> None:
    """A zip missing a member and a member DuckDB cannot read are recorded
    under their periods; neither gets a manifest (the next run extracts it
    again), and the good period is extracted as before."""
    settings = _fsn_settings(tmp_path)
    zips = {
        "2015q1": _one_period_zip("0000000011-15-000001", "11"),
        "2015q2": _missing_member_zip(),
        "2015q3": _malformed_num_zip(),
    }
    source = _source_ready(settings, _router_with_fsn(*zips, zips=zips))
    source._ensure_fsn()
    failures = list(source.validation_failures)
    assert [(f.input, f.key) for f in failures] == [
        ("FSN period", "2015q2"),
        ("FSN period", "2015q3"),
    ]
    assert "missing num.tsv" in failures[0].error
    assert failures[1].error.startswith("InvalidInputException")
    manifests = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests"
    assert sorted(p.stem for p in manifests.glob("*.json")) == ["2015q1"]
    assert source._fsn_loaded_periods == ("2015q1",)
    assert source._fsn_extracted_accessions == {"0000000011-15-000001"}


def test_a_duckdb_io_error_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a malformed line is a parse failure; DuckDB's other errors are
    not recorded (they are not the input's fault)."""

    def disk_error(*args: object, **kwargs: object) -> list[dict[str, str]]:
        raise duckdb.IOException("No space left on device")

    monkeypatch.setattr("tradepartner.adapters.edgar_source._fsn_rows", disk_error)
    settings = _fsn_settings(tmp_path)
    zips = {"2015q1": _one_period_zip("0000000011-15-000001", "11")}
    source = _source_ready(settings, _router_with_fsn(*zips, zips=zips))
    with pytest.raises(duckdb.IOException):
        source._ensure_fsn()
    assert len(source.validation_failures) == 0


# --- end to end: the gate stops a real source (#806 item 2) -------------------


def test_ingest_session_stops_a_real_source_at_the_validation_gate(tmp_path: Path) -> None:
    """A real `EdgarFilingSource` (offline transport) records a bad
    `form.idx` quarter, and `ingest_session`'s `_prefetch` gate finds it
    under its real attribute name and fails the chunk before any store
    write, with the full list on disk. A rename of `validation_failures`
    would let this run succeed."""
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    settings = edgar_settings(tmp_path / "edgar", index_first_year=2026, fsn_first_year=2026)
    router = _router()
    router.add_index(2026, 1, _synthetic(BAD_ROW))
    router.add_index(2026, 2, index_header())
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: now)
    store_settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        edgar=settings.edgar.model_dump(),
        sec_edgar_user_agent=settings.sec_edgar_user_agent,
    )
    [run] = ingest_session(
        store_settings, prices=_NoPrices(), filings=source, source="edgar", clock=lambda: now
    ).runs
    assert run.status == FAILED and run.rows_added == 0
    assert "InputValidationError: EDGAR input validation: 1 input(s)" in run.message
    assert "form.idx 2026-QTR1: ValueError" in run.message
    [listed] = (Path(settings.edgar.cache_dir) / "validation").glob("failures-*.json")
    assert f"full list: {listed.resolve()}" in run.message
    failures = json.loads(listed.read_text())["failures"]
    assert [ValidationFailure(**f).key for f in failures] == ["2026-QTR1"]
    with duckdb.connect(store_settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM securities").fetchone() == (0,)
        assert conn.execute("SELECT status FROM ingestion_runs").fetchall() == [(FAILED,)]

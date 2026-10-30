"""The Form 4 backfill driver over recorded bulk tables and fake SEC calls."""

from __future__ import annotations

import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar_insider_bulk import read_insider_bulk
from tradepartner.adapters.filings import SecFormFiling
from tradepartner.adapters.form4_bulk_route import (
    RequestBudgetStopped,
    load_bulk_quarters,
    order_check_path,
    request_budget,
    run_form4_bulk_route,
)
from tradepartner.adapters.form4_route import Quarter
from tradepartner.config import Settings


def test_second_bulk_read_missing_zip_fails_closed_without_download(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    quarter = Quarter(2026, 1)
    local = settings.model_copy(
        update={"edgar": settings.edgar.model_copy(update={"cache_dir": tmp_path})}
    )
    monkeypatch.setattr(
        edgar_raw,
        "insider_bulk_zip",
        lambda *_args, **_kwargs: pytest.fail("second read requested a download"),
    )
    loaded, digests, counts, bad = load_bulk_quarters(
        {quarter}, settings=local, client=httpx.Client(), revalidate=False
    )
    assert loaded == {quarter: None}
    assert digests == {}
    assert counts["sec_forms_bulk_unreadable"] == 1
    assert bad == {quarter}


def test_accession_rows_in_two_quarters_route_together(tmp_path: Path) -> None:
    accession = "0001577526-26-000036"
    first = Quarter(2026, 1)
    rows = [
        (accession, "0001577526", "4", date(2026, 3, 31)),
        (accession, "0001577526", "4", date(2026, 4, 1)),
    ]
    seen: list[date] = []
    result = run_form4_bulk_route(
        rows,
        quarters={first: _bulk(tmp_path)},
        stamp=lambda _cik, _accession, filed: seen.append(filed) or None,
        xml=lambda *_: pytest.fail("unstamped accession fetched XML"),
        write=lambda *_: pytest.fail("unstamped accession written"),
        now=datetime(2027, 1, 2, tzinfo=UTC),
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=100,
        accession_quarters={accession: first},
    )
    assert seen == [date(2026, 3, 31), date(2026, 4, 1)]
    assert result.remaining == 2


def _bulk(tmp_path: Path):  # type: ignore[no-untyped-def]
    directory = Path(__file__).parents[1] / "fixtures/edgar/sec_forms/insider_bulk_2026q1"
    archive = tmp_path / "2026q1_form345.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS"):
            handle.write(directory / f"{name}.tsv", f"{name}.tsv")
    return read_insider_bulk(archive)


def test_bulk_acceptance_is_the_stamp_and_missing_stamp_is_excluded(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accessions = [
        ("0001577526-26-000036", "0001577526", "4", date(2026, 3, 31)),
        ("0001493152-26-014171", "0000873860", "4", date(2026, 3, 31)),
    ]
    accepted = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)
    written: list[SecFormFiling] = []
    result = run_form4_bulk_route(
        accessions,
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda _cik, accession, _day: accepted if accession == accessions[0][0] else None,
        xml=lambda *_: None,
        write=written.append,
        now=datetime(2026, 4, 2, tzinfo=UTC),
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=100,
    )
    assert len(written) == 1
    assert written[0].accepted_at == accepted
    assert result.counts["sec_forms_unstamped"] == 1


def test_under_threshold_never_writes_bulk_when_xml_fails(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accession = "0001577526-26-000036"
    written: list[SecFormFiling] = []
    result = run_form4_bulk_route(
        [(accession, "0001577526", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: datetime(2026, 4, 1, tzinfo=UTC),
        xml=lambda *_: None,
        write=written.append,
        now=datetime(2026, 4, 2, tzinfo=UTC),
        price_below=10,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=100,
    )
    assert written == []
    assert result.counts["sec_forms_bulk_under_threshold"] == 1
    facts = bulk.accessions[accession]
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    xml_filing = SecFormFiling(
        accession,
        "4",
        accepted,
        date(2026, 3, 31),
        (facts.issuer_cik,),
        facts.filer_ciks,
        facts.transactions,
    )
    from_xml: list[SecFormFiling] = []
    successful = run_form4_bulk_route(
        [(accession, "0001577526", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: xml_filing,
        write=from_xml.append,
        write_bulk=lambda *_: pytest.fail("under-threshold bulk row was written"),
        now=accepted,
        price_below=10,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    assert successful.counts["sec_forms_bulk_under_threshold"] == 1
    assert from_xml == [xml_filing]


def test_order_mismatch_blocks_quarter_and_passed_check_is_remembered(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accession = "0001577526-26-000036"
    facts = bulk.accessions[accession]
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    xml_filing = SecFormFiling(
        accession,
        "4",
        accepted,
        date(2026, 3, 31),
        (facts.issuer_cik,),
        facts.filer_ciks,
        facts.transactions,
    )
    written: list[SecFormFiling] = []
    calls = 0

    def xml(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return replace(
            xml_filing,
            transactions=(
                replace(xml_filing.transactions[0], line_no=99),
                *xml_filing.transactions[1:],
            ),
        )

    args = [(accession, "0001577526", "4", date(2026, 3, 31))]
    result = run_form4_bulk_route(
        args,
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=xml,
        write=written.append,
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
    )
    assert result.counts["sec_forms_bulk_order_mismatch"] == 1
    assert written == []
    assert calls == 1

    checked: set[Quarter] = set()
    passed = run_form4_bulk_route(
        args,
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: xml_filing,
        write=written.append,
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        checked=lambda q: q in checked,
        remember_check=checked.add,
    )
    assert passed.counts["sec_forms_added_accessions"] == 1
    assert checked == {Quarter(2026, 1)}
    repeated = run_form4_bulk_route(
        args,
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: (_ for _ in ()).throw(AssertionError("sample repeated")),
        write=written.append,
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        checked=lambda q: q in checked,
    )
    assert repeated.counts["sec_forms_added_accessions"] == 1


def test_old_missing_quarter_blocks_and_budget_run_resumes(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    first = ("0001577526-26-000036", "0001577526", "4", date(2026, 3, 31))
    second = ("0001493152-26-014171", "0000873860", "4", date(2026, 3, 31))
    written: list[SecFormFiling] = []
    missing = run_form4_bulk_route(
        [first],
        quarters={Quarter(2026, 1): None},
        stamp=lambda *_: accepted,
        xml=lambda *_: None,
        write=written.append,
        now=datetime(2026, 10, 29, tzinfo=UTC),
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    assert missing.counts["sec_forms_bulk_quarter_missing"] == 1
    assert written == []
    first_run = run_form4_bulk_route(
        [first, second],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: None,
        write=written.append,
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=1,
    )
    assert first_run.counts["sec_forms_budget_stopped"] == 1
    assert len(written) == 1
    resumed = run_form4_bulk_route(
        [first, second],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: None,
        write=written.append,
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=1,
        skip_accessions=frozenset({written[0].accession}),
    )
    assert resumed.counts["sec_forms_added_accessions"] == 1
    assert {f.accession for f in written} == {first[0], second[0]}


def test_header_disagreement_goes_only_to_xml(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accession = "0001577526-26-000036"
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    facts = bulk.accessions[accession]
    xml_filing = SecFormFiling(
        accession,
        "4",
        accepted,
        date(2026, 3, 30),
        (facts.issuer_cik,),
        facts.filer_ciks,
        facts.transactions,
    )
    written: list[SecFormFiling] = []
    result = run_form4_bulk_route(
        [(accession, "0001577526", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: (accepted, "4", date(2026, 3, 30)),
        xml=lambda *_: xml_filing,
        write=written.append,
        write_bulk=lambda *_: pytest.fail("bulk route wrote a mismatched header"),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    assert result.counts["sec_forms_bulk_header_disagree"] == 1
    assert len(written) == 1
    assert written[0].filed_date == date(2026, 3, 30)


def test_bulk_issuer_mismatch_goes_only_to_xml(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accession = "0001577526-26-000036"
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    result = run_form4_bulk_route(
        [(accession, "0000873860", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: None,
        write=lambda *_: pytest.fail("mismatched issuer was written"),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    assert result.counts["sec_forms_bulk_identity_bad"] == 1


def test_amendment_and_original_coexist_across_routes(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accepted = datetime(2026, 10, 29, tzinfo=UTC)
    original = ("0001577526-26-000036", "0001577526", "4", date(2026, 3, 31))
    amendment = ("0001977272-26-000004", "0001883313", "4/A", date(2026, 10, 29))
    amended_bulk = bulk.accessions[amendment[0]]
    amended_xml = SecFormFiling(
        amendment[0],
        "4/A",
        accepted,
        amendment[3],
        (amended_bulk.issuer_cik,),
        amended_bulk.filer_ciks,
        amended_bulk.transactions,
    )
    bulk_writes: list[str] = []
    xml_writes: list[str] = []
    result = run_form4_bulk_route(
        [original, amendment],
        quarters={Quarter(2026, 1): bulk, Quarter(2026, 4): None},
        stamp=lambda *_: accepted,
        xml=lambda *_: amended_xml,
        write=lambda filing: xml_writes.append(filing.accession),
        write_bulk=lambda filing, _quarter: bulk_writes.append(filing.accession),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    assert result.counts["sec_forms_added_accessions"] == 2
    assert bulk_writes == [original[0]]
    assert xml_writes == [amendment[0]]


def test_all_sec_requests_use_shared_limiter_and_budget_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    waited: list[float] = []
    monkeypatch.setattr(edgar_raw._LIMITER, "wait", waited.append)
    sent: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, headers={"ETag": "same"})

    count = 0

    def counted(_request: httpx.Request) -> None:
        nonlocal count
        count += 1

    client = httpx.Client(
        transport=httpx.MockTransport(response), event_hooks={"request": [counted]}
    )
    settings = Settings(
        _env_file=None,
        sec_edgar_user_agent="Offline Test test@example.com",
    ).model_copy(
        update={"edgar": Settings(_env_file=None).edgar.model_copy(update={"cache_dir": tmp_path})}
    )
    with request_budget(client, lambda: count, 1):
        assert (
            edgar_raw.insider_bulk_validators("2026q1", settings=settings, client=client)
            is not None
        )
        with pytest.raises(RequestBudgetStopped):
            edgar_raw.insider_bulk_validators("2026q1", settings=settings, client=client)
    assert len(waited) == 2
    assert len(sent) == 1


def test_order_check_repeats_when_zip_or_parser_version_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tradepartner.adapters import edgar_source, form4_bulk_route

    settings = Settings(_env_file=None).model_copy(
        update={"edgar": Settings(_env_file=None).edgar.model_copy(update={"cache_dir": tmp_path})}
    )
    quarter = Quarter(2026, 1)
    passed = order_check_path(settings, quarter, "zip-a")
    passed.parent.mkdir(parents=True)
    passed.write_text("passed")
    assert passed.exists()
    assert not order_check_path(settings, quarter, "zip-b").exists()
    monkeypatch.setattr(
        form4_bulk_route,
        "BULK_READER_VERSION",
        form4_bulk_route.BULK_READER_VERSION + 1,
    )
    assert not order_check_path(settings, quarter, "zip-a").exists()
    monkeypatch.setattr(edgar_source, "PARSER_VERSION", edgar_source.PARSER_VERSION + 1)
    assert not order_check_path(settings, quarter, "zip-a").exists()

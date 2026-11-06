"""The Form 4 backfill driver over recorded bulk tables and fake SEC calls."""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar_insider_bulk import InsiderBulk, read_insider_bulk
from tradepartner.adapters.filings import SecFormFiling
from tradepartner.adapters.form4_bulk_route import (
    RequestBudgetStopped,
    Rule5,
    load_bulk_quarters,
    marker_entries,
    order_check_path,
    request_budget,
    run_form4_bulk_route,
    trusted_filing,
    unresolved_markers,
)
from tradepartner.adapters.form4_compare import Outcome, declaration_from
from tradepartner.adapters.form4_route import Quarter, order_sample
from tradepartner.config import EventsConfig, Settings


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


@pytest.mark.parametrize("multi_owner_xml", [True, False])
def test_multi_owner_route_and_order_sample_switch(tmp_path: Path, multi_owner_xml: bool) -> None:
    bulk = _bulk(tmp_path)
    multi = "0001213900-26-037435"
    single = "0001577526-26-000036"
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    rows = [
        (multi, "0002025396", "4", date(2026, 3, 31)),
        (single, "0001577526", "4", date(2026, 3, 31)),
    ]
    xml_calls: list[str] = []
    xml_writes: list[str] = []
    bulk_writes: list[str] = []

    def xml(_cik: str, accession: str, _day: date) -> SecFormFiling:
        xml_calls.append(accession)
        facts = bulk.accessions[accession]
        return SecFormFiling(
            accession,
            "4",
            accepted,
            date(2026, 3, 31),
            (facts.issuer_cik,),
            facts.filer_ciks,
            facts.transactions,
        )

    result = run_form4_bulk_route(
        rows,
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=xml,
        write=lambda filing: xml_writes.append(filing.accession),
        write_bulk=lambda filing, _quarter: bulk_writes.append(filing.accession),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        multi_owner_xml=multi_owner_xml,
    )
    if multi_owner_xml:
        assert xml_writes == [multi]
        assert bulk_writes == [single]
        assert result.counts["sec_forms_bulk_multi_owner_xml"] == 1
        assert xml_calls.count(multi) == 1  # route only, never order sample
    else:
        assert xml_writes == []
        assert set(bulk_writes) == {multi, single}
        assert result.counts["sec_forms_bulk_multi_owner_xml"] == 0


def test_duplicate_owner_cik_is_one_distinct_owner(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    accession = "0001577526-26-000036"
    facts = bulk.accessions[accession]
    bulk.accessions[accession] = replace(facts, filer_ciks=(facts.filer_ciks[0],) * 2)
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    bulk_writes: list[str] = []
    result = run_form4_bulk_route(
        [(accession, facts.issuer_cik, "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: SecFormFiling(
            accession,
            "4",
            accepted,
            date(2026, 3, 31),
            (facts.issuer_cik,),
            facts.filer_ciks,
            facts.transactions,
        ),
        write=lambda *_: pytest.fail("duplicate owner CIK was routed to XML"),
        write_bulk=lambda filing, _quarter: bulk_writes.append(filing.accession),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        multi_owner_xml=True,
    )
    assert bulk_writes == [accession]
    assert result.counts["sec_forms_bulk_multi_owner_xml"] == 0


def test_multi_owner_swap_does_not_fail_quarter_order_sample(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path)
    multi = "0001213900-26-037435"
    single = "0001577526-26-000036"
    accepted = datetime(2026, 4, 1, tzinfo=UTC)
    xml_writes: list[str] = []
    bulk_writes: list[str] = []
    checked: list[Quarter] = []

    def xml(_cik: str, accession: str, _day: date) -> SecFormFiling:
        facts = bulk.accessions[accession]
        transactions = facts.transactions
        if accession == multi:
            # the #2705 mispin: the XML's roles differ from the bulk join's
            transactions = tuple(
                replace(t, is_director=not t.is_director, is_other=True) for t in transactions
            )
        return SecFormFiling(
            accession,
            "4",
            accepted,
            date(2026, 3, 31),
            (facts.issuer_cik,),
            facts.filer_ciks,
            transactions,
        )

    result = run_form4_bulk_route(
        [
            (multi, "0002025396", "4", date(2026, 3, 31)),
            (single, "0001577526", "4", date(2026, 3, 31)),
        ],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=xml,
        write=lambda filing: xml_writes.append(filing.accession),
        write_bulk=lambda filing, _quarter: bulk_writes.append(filing.accession),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=2,
        budget=10,
        multi_owner_xml=True,
        remember_check=checked.append,
    )
    assert result.counts["sec_forms_bulk_order_mismatch"] == 0
    assert result.counts["sec_forms_bulk_multi_owner_xml"] == 1
    assert xml_writes == [multi]
    assert bulk_writes == [single]
    assert checked == [Quarter(2026, 1)]


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
    switched = settings.model_copy(
        update={"events": settings.events.model_copy(update={"form4_multi_owner_xml": False})}
    )
    assert not order_check_path(switched, quarter, "zip-a").exists()
    monkeypatch.setattr(
        form4_bulk_route,
        "BULK_READER_VERSION",
        form4_bulk_route.BULK_READER_VERSION + 1,
    )
    assert not order_check_path(settings, quarter, "zip-a").exists()
    monkeypatch.setattr(edgar_source, "PARSER_VERSION", edgar_source.PARSER_VERSION + 1)
    assert not order_check_path(settings, quarter, "zip-a").exists()


def test_a_held_accession_makes_no_request_and_no_write(tmp_path: Path) -> None:
    """Rule 4 (#2714 item 5): the store's held set is skipped before any stamp or
    XML request, so a rerun with nothing new asks SEC for nothing."""
    held = ("0001577526-26-000036", "0001577526", "4", date(2026, 3, 31))
    result = run_form4_bulk_route(
        [held],
        quarters={Quarter(2026, 1): _bulk(tmp_path)},
        stamp=lambda *_: pytest.fail("a held accession was stamped"),
        xml=lambda *_: pytest.fail("a held accession fetched XML"),
        write=lambda *_: pytest.fail("a held accession was written"),
        now=datetime(2026, 4, 1, tzinfo=UTC),
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
        skip_accessions=frozenset({held[0]}),
    )
    assert (result.requests, result.remaining) == (0, 0)


def test_insider_zip_fetch_records_its_url_and_utc_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T348: a downloaded insider zip leaves its URL and fetch time beside it, for
    the writer's manifest; a zip cached before that reads as unknown."""
    from tradepartner.adapters.form4_bulk_route import insider_zip_fetch, insider_zip_path

    monkeypatch.setattr(edgar_raw._LIMITER, "wait", lambda _s: None)
    buffer = tmp_path / "source.zip"
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\n")
    sent: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        if "structureddata" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=buffer.read_bytes())

    settings = Settings(_env_file=None, sec_edgar_user_agent="Offline Test test@example.com")
    settings = settings.model_copy(
        update={"edgar": settings.edgar.model_copy(update={"cache_dir": tmp_path / "cache"})}
    )
    quarter = Quarter(2026, 1)
    assert insider_zip_fetch(settings, quarter, "0" * 64) == (None, None)
    before = datetime.now(UTC)
    fetched = edgar_raw.insider_bulk_zip(
        "2026q1", settings=settings, client=httpx.Client(transport=httpx.MockTransport(response))
    )
    assert fetched is not None and fetched[0] == insider_zip_path(settings, quarter)
    digest = hashlib.sha256(buffer.read_bytes()).hexdigest()
    url, at = insider_zip_fetch(settings, quarter, digest)
    assert url == sent[-1] and "datastandardsinnovation" in url
    assert at is not None and at.tzinfo is not None and at >= before
    # A record of other bytes (a stale record beside a replaced zip) is not cited.
    assert insider_zip_fetch(settings, quarter, "f" * 64) == (None, None)
    insider_zip_path(settings, quarter).with_name("2026q1_form345.fetch.json").write_text("{")
    assert insider_zip_fetch(settings, quarter, digest) == (None, None)


_MULTI = "0001213900-26-037435"
_ACCEPTED = datetime(2026, 4, 1, tzinfo=UTC)


def _route_multi(
    tmp_path: Path, xml_filing: Callable[[SecFormFiling], SecFormFiling | None], form: str = "4"
) -> tuple[Counter[str], list[SecFormFiling], int]:
    bulk = _bulk(tmp_path)
    facts = bulk.accessions[_MULTI]
    written: list[SecFormFiling] = []
    base = SecFormFiling(
        _MULTI,
        form,
        _ACCEPTED,
        date(2026, 3, 31),
        (facts.issuer_cik,),
        facts.filer_ciks,
        facts.transactions,
    )
    result = run_form4_bulk_route(
        [(_MULTI, "0002025396", form, date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: (_ACCEPTED, form, date(2026, 3, 31)),
        xml=lambda *_: xml_filing(base),
        write=written.append,
        write_bulk=lambda *_: pytest.fail("multi-owner accession written from bulk"),
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
        multi_owner_xml=True,
    )
    return result.counts, written, result.remaining


def test_distinct_associations_are_kept_from_xml(tmp_path: Path) -> None:
    _counts, written, remaining = _route_multi(tmp_path, lambda f: f)
    assert remaining == 0 and len(written) == 1
    pairs = {(t.filer_cik, t.line_no) for t in written[0].transactions}
    assert len(pairs) == len(written[0].transactions) == 4
    assert {c for c, _ in pairs} == set(written[0].filer_ciks)


def test_bulk_mispin_roles_are_replaced_by_xml_roles(tmp_path: Path) -> None:
    """The #2705 / #2711 fixture shape: the bulk join gives one owner both roles and
    the other none, while the XML splits them (Director, TenPercentOwner)."""
    first, second = _bulk(tmp_path).accessions[_MULTI].filer_ciks

    def xml_roles(filing: SecFormFiling) -> SecFormFiling:
        rows = tuple(
            replace(
                t,
                is_director=t.filer_cik == first,
                is_officer=False,
                is_ten_percent_owner=t.filer_cik == second,
            )
            for t in filing.transactions
        )
        return replace(filing, transactions=rows)

    _counts, written, _ = _route_multi(tmp_path, xml_roles)
    stored = {t.filer_cik: (t.is_director, t.is_ten_percent_owner) for t in written[0].transactions}
    assert stored == {first: (True, False), second: (False, True)}


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda f: replace(f, transactions=f.transactions[:-1]), "sec_forms_multi_owner_ambiguous"),
        (
            lambda f: replace(f, transactions=(*f.transactions, f.transactions[0])),
            "sec_forms_multi_owner_ambiguous",
        ),
        (
            lambda f: replace(f, filer_ciks=(*f.filer_ciks, f.filer_ciks[0])),
            "sec_forms_multi_owner_ambiguous",
        ),
        (
            lambda f: replace(
                f,
                filer_ciks=(*f.filer_ciks, "0000000999"),
            ),
            "sec_forms_multi_owner_set_mismatch",
        ),
    ],
)
def test_ambiguous_xml_owner_attribution_is_refused_and_counted(
    tmp_path: Path, mutate: Callable[[SecFormFiling], SecFormFiling], reason: str
) -> None:
    counts, written, remaining = _route_multi(tmp_path, mutate)
    assert written == [] and remaining == 1
    assert counts[reason] == 1


def test_a_4a_multi_owner_amendment_routes_to_xml_on_its_own(tmp_path: Path) -> None:
    counts, written, remaining = _route_multi(tmp_path, lambda f: f, form="4/A")
    assert remaining == 0 and [w.form for w in written] == ["4/A"]
    assert counts["sec_forms_bulk_multi_owner_xml"] == 1


# --- T351 (#2987): rule 5 comparison in the route ---------------------------------------

_SINGLE = "0001577526-26-000036"


def _rule5(threshold: str | None) -> Rule5:
    events = EventsConfig(form4_compare_threshold=None if threshold is None else Decimal(threshold))
    return Rule5(declaration_from(events))


def _route_rule5(
    tmp_path: Path,
    rule5: Rule5,
    mutate: Callable[[SecFormFiling], SecFormFiling],
    *,
    sample: int = 1,
    accessions: tuple[str, ...] = (_SINGLE,),
) -> tuple[Counter[str], list[SecFormFiling], list[SecFormFiling]]:
    bulk = _bulk(tmp_path)
    xml_writes: list[SecFormFiling] = []
    bulk_writes: list[SecFormFiling] = []

    def xml(_cik: str, accession: str, _day: date) -> SecFormFiling:
        facts = bulk.accessions[accession]
        return mutate(
            SecFormFiling(
                accession,
                "4",
                _ACCEPTED,
                date(2026, 3, 31),
                (facts.issuer_cik,),
                facts.filer_ciks,
                facts.transactions,
            )
        )

    result = run_form4_bulk_route(
        [(a, bulk.accessions[a].issuer_cik, "4", date(2026, 3, 31)) for a in accessions],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: _ACCEPTED,
        xml=xml,
        write=xml_writes.append,
        write_bulk=lambda filing, _quarter: bulk_writes.append(filing),
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=sample,
        budget=20,
        rule5=rule5,
    )
    return result.counts, xml_writes, bulk_writes


def _shares_off(filing: SecFormFiling) -> SecFormFiling:
    first = replace(filing.transactions[0], shares=filing.transactions[0].shares + 1)
    return replace(filing, transactions=(first, *filing.transactions[1:]))


def test_unset_threshold_counts_but_keeps_the_order_sample_refusal(tmp_path: Path) -> None:
    rule5 = _rule5(None)
    counts, xml_writes, bulk_writes = _route_rule5(tmp_path, rule5, _shares_off)
    assert counts["sec_forms_bulk_order_mismatch"] == 1
    assert xml_writes == [] and bulk_writes == []
    assert (rule5.tally.disagreeing, rule5.tally.compared) == (1, 1)


def test_one_sample_mismatch_below_threshold_does_not_block_the_quarter(tmp_path: Path) -> None:
    rule5 = _rule5("0.5")
    other = "0001493152-26-014171"
    # Both accessions have two rows; the sample takes the first by SHA-256 order.
    sampled = order_sample({_SINGLE: 2, other: 2}, 1)[0]
    unsampled = other if sampled == _SINGLE else _SINGLE
    counts, xml_writes, bulk_writes = _route_rule5(
        tmp_path, rule5, _shares_off, accessions=(_SINGLE, other)
    )
    assert counts["sec_forms_bulk_order_mismatch"] == 0
    assert [w.accession for w in xml_writes] == [sampled]  # its trusted XML row
    assert [w.accession for w in bulk_writes] == [unsampled]
    bulk_first = _bulk(tmp_path).accessions[sampled].transactions[0]
    assert xml_writes[0].transactions[0].shares == bulk_first.shares + 1
    assert counts["sec_forms_compare_trusted_row"] == 1
    assert set(rule5.found) == {sampled}
    assert (rule5.tally.disagreeing, rule5.tally.compared) == (1, 1)


def test_zero_eligible_accessions_leave_the_comparison_unchecked(tmp_path: Path) -> None:
    rule5 = _rule5("0.5")
    counts, _xml, bulk_writes = _route_rule5(tmp_path, rule5, _shares_off, sample=0)
    assert counts["sec_forms_bulk_order_unchecked"] == 1
    assert rule5.tally.compared == 0
    assert rule5.tally.outcome(rule5.declaration.threshold) is Outcome.UNCHECKED
    assert [w.accession for w in bulk_writes] == [_SINGLE]


def test_multi_owner_accession_is_not_compared_with_the_bulk_owner_join(tmp_path: Path) -> None:
    """#2705 / #2711 roles: the bulk join mispins roles, the XML is right, and the
    accession is stored from XML without counting SEC's defect as a disagreement."""
    rule5 = _rule5("0.5")
    first, second = _bulk(tmp_path).accessions[_MULTI].filer_ciks

    def xml_roles(filing: SecFormFiling) -> SecFormFiling:
        rows = tuple(
            replace(t, is_director=t.filer_cik == first, is_officer=False,
                    is_ten_percent_owner=t.filer_cik == second)
            for t in filing.transactions
        )  # fmt: skip
        return replace(filing, transactions=rows)

    _counts, xml_writes, bulk_writes = _route_rule5(
        tmp_path, rule5, xml_roles, sample=0, accessions=(_MULTI,)
    )
    assert bulk_writes == [] and len(xml_writes) == 1
    assert rule5.tally.compared == 0 and rule5.found == {}
    stored = {
        t.filer_cik: (t.is_director, t.is_ten_percent_owner) for t in xml_writes[0].transactions
    }
    assert stored == {first: (True, False), second: (False, True)}


def test_xml_routed_single_owner_role_disagreement_is_counted_and_xml_stored(
    tmp_path: Path,
) -> None:
    """A single-owner accession routed to XML (the 4/A header against the 4) whose bulk
    role differs is one disagreement; the XML role is the trusted row."""
    rule5 = _rule5("0.9")
    bulk = _bulk(tmp_path)
    record = bulk.accessions[_SINGLE]
    flipped = tuple(replace(t, is_director=not t.is_director) for t in record.transactions)
    patched = InsiderBulk(
        {**bulk.accessions, _SINGLE: replace(record, transactions=flipped, document_type="4/A")}
    )
    written: list[SecFormFiling] = []
    xml_filing = SecFormFiling(
        _SINGLE,
        "4",
        _ACCEPTED,
        date(2026, 3, 31),
        (record.issuer_cik,),
        record.filer_ciks,
        record.transactions,
    )
    run_form4_bulk_route(
        [(_SINGLE, record.issuer_cik, "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): patched},
        stamp=lambda *_: _ACCEPTED,
        xml=lambda *_: xml_filing,
        write=written.append,
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
        rule5=rule5,
    )  # fmt: skip
    assert [d.field for d in rule5.found[_SINGLE]] == ["is_director", "is_director"]
    assert written[0].transactions == record.transactions  # the XML rows, not the flipped bulk


def test_a_field_declared_to_the_bulk_set_takes_its_value(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path).accessions[_SINGLE]
    events = EventsConfig(form4_compare_trusted={"shares_owned_after": "bulk"})
    xml_filing = SecFormFiling(
        _SINGLE, "4", _ACCEPTED, date(2026, 3, 31), (bulk.issuer_cik,), bulk.filer_ciks,
        tuple(replace(t, shares_owned_after=Decimal(1)) for t in bulk.transactions),
    )  # fmt: skip
    merged = trusted_filing(xml_filing, bulk.transactions, declaration_from(events))
    assert [t.shares_owned_after for t in merged.transactions] == [
        t.shares_owned_after for t in bulk.transactions
    ]
    assert trusted_filing(xml_filing, bulk.transactions, declaration_from(EventsConfig())) == (
        xml_filing
    )


def test_markers_name_both_values_and_only_unresolved_ones_carry() -> None:
    from tradepartner.adapters.form4_compare import Disagreement

    d = Disagreement("shares", ("0000000002", 1), Decimal(5), Decimal(6), "xml")
    (marker,) = marker_entries("a-1", "2026q1", "f" * 64, (d,))
    assert marker["bulk"] == "5" and marker["xml"] == "6" and marker["trusted"] == "xml"
    assert marker["row_key"] == ["0000000002", 1] and marker["zip_sha256"] == "f" * 64
    (resolved,) = marker_entries("a-2", "2026q1", "f" * 64, ())
    manifest = {"entries": [marker, resolved, {"row_source": "sec_form4_xml"}]}
    assert unresolved_markers(manifest) == [marker]
    assert unresolved_markers(None) == []


def test_an_unfetched_order_sample_still_refuses_the_quarter_when_enforcing(
    tmp_path: Path,
) -> None:
    bulk = _bulk(tmp_path)
    record = bulk.accessions[_SINGLE]
    counts = run_form4_bulk_route(
        [(_SINGLE, record.issuer_cik, "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: _ACCEPTED,
        xml=lambda *_: None,
        write=lambda f: pytest.fail("written"),
        write_bulk=lambda f, q: pytest.fail("written"),
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        rule5=_rule5("0.5"),
    ).counts
    assert counts["sec_forms_bulk_order_mismatch"] == 1


def test_a_marked_accession_is_never_stored_from_bulk_rows(tmp_path: Path) -> None:
    rule5 = _rule5("0.5")
    _counts, _xml, bulk_writes = _route_rule5(
        tmp_path, rule5, _shares_off, sample=0, accessions=(_SINGLE,)
    )
    assert [w.accession for w in bulk_writes] == [_SINGLE]  # unmarked: plain bulk
    bulk = _bulk(tmp_path)
    written: list[SecFormFiling] = []
    record = bulk.accessions[_SINGLE]
    run_form4_bulk_route(
        [(_SINGLE, record.issuer_cik, "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: _ACCEPTED,
        xml=lambda _c, a, _d: _shares_off(
            SecFormFiling(a, "4", _ACCEPTED, date(2026, 3, 31), (record.issuer_cik,),
                          record.filer_ciks, record.transactions)
        ),
        write=written.append,
        write_bulk=lambda f, q: pytest.fail("a marked accession was stored from bulk"),
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
        rule5=rule5,
        xml_accessions=frozenset({_SINGLE}),
    )  # fmt: skip
    assert [w.accession for w in written] == [_SINGLE] and _SINGLE in rule5.found


def test_a_tolerated_sample_pass_is_not_remembered(tmp_path: Path) -> None:
    """A sample that passes only on a declared tolerance would be a weaker pass than
    the unset key's, so it is not remembered (it is resampled)."""
    rule5 = Rule5(declaration_from(EventsConfig(
        form4_compare_threshold=Decimal("0.5"), form4_compare_tolerance={"shares": Decimal(1)},
    )))  # fmt: skip
    bulk = _bulk(tmp_path)
    remembered: list[Quarter] = []
    run_form4_bulk_route(
        [(_SINGLE, bulk.accessions[_SINGLE].issuer_cik, "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: _ACCEPTED,
        xml=lambda _c, a, _d: _shares_off(
            SecFormFiling(a, "4", _ACCEPTED, date(2026, 3, 31),
                          (bulk.accessions[a].issuer_cik,), bulk.accessions[a].filer_ciks,
                          bulk.accessions[a].transactions)
        ),
        write=lambda f: None,
        write_bulk=lambda f, q: None,
        now=_ACCEPTED,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=1,
        budget=10,
        rule5=rule5,
        remember_check=remembered.append,
    )  # fmt: skip
    assert rule5.tally.disagreeing == 0 and rule5.tally.compared == 1  # within tolerance
    assert remembered == []


def test_a_sampled_accession_gets_the_owner_refusal_before_it_is_stored(tmp_path: Path) -> None:
    def extra_owner(filing: SecFormFiling) -> SecFormFiling:
        return replace(_shares_off(filing), filer_ciks=(*filing.filer_ciks, "0000000999"))

    counts, xml_writes, bulk_writes = _route_rule5(tmp_path, _rule5("0.9"), extra_owner)
    assert counts["sec_forms_multi_owner_set_mismatch"] == 1
    assert xml_writes == [] and bulk_writes == []


def test_a_bulk_trusted_party_difference_never_aborts_the_run(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path).accessions[_SINGLE]
    other = "0000000999"
    xml_rows = tuple(replace(t, filer_cik=other) for t in bulk.transactions)
    xml_filing = SecFormFiling(
        _SINGLE, "4", _ACCEPTED, date(2026, 3, 31), (bulk.issuer_cik,), (other,), xml_rows
    )
    line_no_bulk = declaration_from(EventsConfig(form4_compare_trusted={"line_no": "bulk"}))
    merged = trusted_filing(xml_filing, bulk.transactions, line_no_bulk)
    assert merged is not None
    assert set(merged.filer_ciks) == {t.filer_cik for t in bulk.transactions}
    issuer_bulk = declaration_from(EventsConfig(form4_compare_trusted={"issuer_cik": "bulk"}))
    changed_issuer = tuple(replace(t, issuer_cik="0000000998") for t in bulk.transactions)
    same_keys = replace(xml_filing, filer_ciks=bulk.filer_ciks, transactions=bulk.transactions)
    assert trusted_filing(same_keys, changed_issuer, issuer_bulk) is None

"""T305b (#2498): the insider bulk data-set reader, its raw fetch and its limiter.

The recorded zip (`insider_bulk_2026q1.zip`) holds the real `SUBMISSION`,
`REPORTINGOWNER` and `NONDERIV_TRANS` rows of four 2026Q1 accessions, and
`bulk_parity_<accession>.xml` is the same accessions' Form 4 XML. Everything else is a
synthetic zip built here. No store, no network.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tradepartner.adapters import edgar_insider_bulk as bulk
from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import Form4Transaction, parse_form4
from tradepartner.config import Settings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar" / "sec_forms"
RECORDED = FIXTURES / "insider_bulk_2026q1.zip"
ACCESSIONS = (
    "0001213900-26-037435",  # joint filing, two owners, blank prices (J, C), indirect
    "0001577526-26-000036",  # P and S with prices; SK order is not file order
    "0001977272-26-000004",  # 4/A with a date of original submission
    "0001493152-26-014171",  # M with a blank price, F with one; Director,Officer
)

SUBMISSION = [
    "ACCESSION_NUMBER",
    "FILING_DATE",
    "DATE_OF_ORIG_SUB",
    "DOCUMENT_TYPE",
    "ISSUERCIK",
]
OWNER = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"]
TRANS = [
    "ACCESSION_NUMBER",
    "NONDERIV_TRANS_SK",
    "SECURITY_TITLE",
    "TRANS_DATE",
    "DEEMED_EXECUTION_DATE",
    "TRANS_CODE",
    "TRANS_ACQUIRED_DISP_CD",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
]

ACC = "0000000000-26-000001"


def _tsv(columns: list[str], rows: list[dict[str, str]]) -> str:
    lines = ["\t".join(columns)] + ["\t".join(r.get(c, "") for c in columns) for r in rows]
    return "\n".join(lines) + "\n"


def _sub(**kw: str) -> dict[str, str]:
    base = {
        "ACCESSION_NUMBER": ACC,
        "FILING_DATE": "31-MAR-2026",
        "DOCUMENT_TYPE": "4",
        "ISSUERCIK": "0000111111",
    }
    return {**base, **kw}


def _own(cik: str = "0000222222", rel: str = "Director", acc: str = ACC) -> dict[str, str]:
    return {"ACCESSION_NUMBER": acc, "RPTOWNERCIK": cik, "RPTOWNER_RELATIONSHIP": rel}


def _tr(sk: str, **kw: str) -> dict[str, str]:
    base = {
        "ACCESSION_NUMBER": ACC,
        "NONDERIV_TRANS_SK": sk,
        "SECURITY_TITLE": "Common Stock",
        "TRANS_DATE": "27-MAR-2026",
        "TRANS_CODE": "P",
        "TRANS_ACQUIRED_DISP_CD": "A",
        "TRANS_SHARES": "100.0",
        "TRANS_PRICEPERSHARE": "12.5",
        "SHRS_OWND_FOLWNG_TRANS": "1100.0",
        "DIRECT_INDIRECT_OWNERSHIP": "D",
    }
    return {**base, **kw}


def _zip(
    *,
    subs: list[dict[str, str]] | None = None,
    owners: list[dict[str, str]] | None = None,
    trans: list[dict[str, str]] | None = None,
    extra: dict[str, bytes] | None = None,
    sub_cols: list[str] | None = None,
    own_cols: list[str] | None = None,
    trans_cols: list[str] | None = None,
    drop: tuple[str, ...] = (),
) -> io.BytesIO:
    members = {
        "SUBMISSION": _tsv(sub_cols or SUBMISSION, subs if subs is not None else [_sub()]),
        "REPORTINGOWNER": _tsv(own_cols or OWNER, owners if owners is not None else [_own()]),
        "NONDERIV_TRANS": _tsv(trans_cols or TRANS, trans if trans is not None else [_tr("1")]),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in members.items():
            if name not in drop:
                z.writestr(f"{name}.tsv", text)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    buffer.seek(0)
    return buffer


def _read(**kw: object) -> bulk.InsiderBulk:
    return bulk.read_insider_bulk(_zip(**kw))  # type: ignore[arg-type]


def _one(**kw: object) -> bulk.BulkAccession:
    return _read(**kw).accessions[ACC]


# --- the recorded zip: every field of the amendment's source table ---------------


def test_recorded_zip_reads_the_four_accessions() -> None:
    result = bulk.read_insider_bulk(RECORDED)
    assert set(result.accessions) == set(ACCESSIONS)


def test_recorded_zip_every_source_field() -> None:
    a = bulk.read_insider_bulk(RECORDED).accessions["0001577526-26-000036"]
    assert (a.document_type, a.filing_date) == ("4", date(2026, 3, 31))
    assert a.issuer_cik == "0001577526"
    assert a.filer_ciks == ("0001849334",)
    assert not a.order_bad and not a.identity_bad and a.skipped == 0
    # SK 8938587 (S) ranks before SK 8938588 (P), though the file lists P first.
    sale, purchase = a.transactions
    assert sale == Form4Transaction(
        issuer_cik="0001577526",
        filer_cik="0001849334",
        line_no=0,
        is_director=True,
        is_officer=False,
        is_ten_percent_owner=False,
        is_other=False,
        transaction_date=date(2026, 3, 27),
        deemed_execution_date=None,
        transaction_code="S",
        acquired_disposed="D",
        shares=Decimal("10000.0"),
        price=Decimal("8.08"),
        shares_owned_after=Decimal("385000.0"),
        ownership_form="D",
        security_title="Class A Common Stock",
        date_of_original_submission=None,
    )
    assert (purchase.line_no, purchase.transaction_code, purchase.price) == (
        1,
        "P",
        Decimal("7.73"),
    )


def test_recorded_zip_amendment_carries_the_original_submission_date() -> None:
    a = bulk.read_insider_bulk(RECORDED).accessions["0001977272-26-000004"]
    assert a.document_type == "4/A"
    (row,) = a.transactions
    assert row.date_of_original_submission == date(2026, 3, 16)
    assert (row.is_officer, row.is_director) == (True, False)


def test_recorded_zip_blank_price_is_null_never_zero() -> None:
    a = bulk.read_insider_bulk(RECORDED).accessions["0001493152-26-014171"]
    by_code = {t.transaction_code: t for t in a.transactions}
    assert by_code["M"].price is None
    assert by_code["F"].price == Decimal("37.54")
    assert by_code["M"].line_no == 0 and by_code["F"].line_no == 1
    assert (by_code["M"].is_director, by_code["M"].is_officer) == (True, True)


def test_recorded_zip_joint_filing_has_a_row_per_owner_and_transaction() -> None:
    a = bulk.read_insider_bulk(RECORDED).accessions["0001213900-26-037435"]
    assert a.filer_ciks == ("0001642434", "0002029854")
    assert [(t.line_no, t.filer_cik) for t in a.transactions] == [
        (0, "0001642434"),
        (0, "0002029854"),
        (1, "0001642434"),
        (1, "0002029854"),
    ]
    petruska, sponsor = a.transactions[0], a.transactions[1]
    assert (sponsor.is_ten_percent_owner, sponsor.is_director, sponsor.is_officer) == (
        True,
        False,
        False,
    )
    assert (petruska.is_director, petruska.is_officer, petruska.is_ten_percent_owner) == (
        True,
        True,
        True,
    )
    assert {t.ownership_form for t in a.transactions} == {"I"}


def _q2(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(Decimal("0.01"))


@pytest.mark.parametrize("accession", ACCESSIONS)
def test_parity_with_parse_form4_on_the_same_accession(accession: str) -> None:
    """Equal rows, in order, with price and shares compared at 2 decimals (the data set
    rounds them)."""
    xml = (FIXTURES / f"bulk_parity_{accession}.xml").read_text(encoding="utf-8")
    expected = parse_form4(xml)
    got = bulk.read_insider_bulk(RECORDED).accessions[accession]
    assert got.skipped == expected.skipped
    # The data set does not keep the XML's owner order: compare by identity, not position.
    assert got.filer_ciks == tuple(sorted(expected.filer_ciks))
    assert len(got.transactions) == len(expected.transactions)
    by_key = {(t.filer_cik, t.line_no): t for t in expected.transactions}
    for bulk_row in got.transactions:
        xml_row = by_key[(bulk_row.filer_cik, bulk_row.line_no)]
        for f in fields(Form4Transaction):
            left, right = getattr(bulk_row, f.name), getattr(xml_row, f.name)
            if f.name in ("price", "shares", "shares_owned_after"):
                left, right = _q2(left), _q2(right)
            assert left == right, (accession, f.name)


# --- the rules --------------------------------------------------------------------


def test_the_relationship_list_gives_the_four_booleans() -> None:
    cases = {
        "Director": (True, False, False, False),
        "Officer": (False, True, False, False),
        "TenPercentOwner": (False, False, True, False),
        "Other": (False, False, False, True),
        "Director,Officer,TenPercentOwner,Other": (True, True, True, True),
        "": (False, False, False, False),
    }
    for rel, flags in cases.items():
        (row,) = _one(owners=[_own(rel=rel)]).transactions
        assert (
            row.is_director,
            row.is_officer,
            row.is_ten_percent_owner,
            row.is_other,
        ) == flags, rel


def test_a_blank_is_null_never_zero() -> None:
    (row,) = _one(
        trans=[
            _tr("1", TRANS_PRICEPERSHARE="", SHRS_OWND_FOLWNG_TRANS="", DEEMED_EXECUTION_DATE="")
        ]
    ).transactions
    assert row.price is None
    assert row.shares_owned_after is None
    assert row.deemed_execution_date is None


def test_a_zero_price_stays_zero() -> None:
    (row,) = _one(trans=[_tr("1", TRANS_CODE="A", TRANS_PRICEPERSHARE="0.0")]).transactions
    assert row.price == Decimal("0.0")


def test_line_no_ranks_every_row_by_key_a_skipped_row_keeping_its_position() -> None:
    a = _one(
        trans=[
            _tr("30", TRANS_CODE="S", TRANS_ACQUIRED_DISP_CD="D"),
            _tr("10"),
            _tr("20", DIRECT_INDIRECT_OWNERSHIP=""),  # skipped, but rank 1
        ]
    )
    assert [(t.line_no, t.transaction_code) for t in a.transactions] == [(0, "P"), (2, "S")]
    assert a.skipped == 1
    assert [(r.line_no, r.skipped) for r in a.rows] == [(0, False), (1, True), (2, False)]


def test_a_blank_ownership_or_a_bad_ad_flag_skips_the_row_and_keeps_its_position() -> None:
    for column, value in (
        ("DIRECT_INDIRECT_OWNERSHIP", ""),
        ("TRANS_ACQUIRED_DISP_CD", "X"),
        ("TRANS_ACQUIRED_DISP_CD", ""),
        ("TRANS_CODE", ""),
        ("TRANS_DATE", ""),
        ("TRANS_SHARES", ""),
        ("TRANS_SHARES", "lots"),
        ("TRANS_DATE", "2026-03-27"),
    ):
        a = _one(trans=[_tr("1", **{column: value}), _tr("2")])
        assert a.skipped == 1, (column, value)
        assert [t.line_no for t in a.transactions] == [1], (column, value)
        assert not a.order_bad


def test_a_duplicate_or_non_integer_key_is_order_bad() -> None:
    for keys in (("1", "1"), ("1", ""), ("1", "x"), ("1", "2.0"), ("1", "-3")):
        a = _one(trans=[_tr(keys[0]), _tr(keys[1])])
        assert a.order_bad, keys
        assert a.transactions == ()
        assert all(r.line_no is None for r in a.rows)
        assert len(a.rows) == 2  # the raw facts are still there


def test_a_clean_accession_is_not_order_bad() -> None:
    assert not _one(trans=[_tr("5"), _tr("9")]).order_bad


def test_row_facts_carry_each_rows_code_and_price() -> None:
    a = _one(
        trans=[
            _tr("1", TRANS_CODE="G", TRANS_PRICEPERSHARE="0.0"),
            _tr("2", TRANS_PRICEPERSHARE=""),
            _tr("3", TRANS_PRICEPERSHARE="0.4", DIRECT_INDIRECT_OWNERSHIP=""),
        ]
    )
    assert [(r.line_no, r.transaction_code, r.price, r.skipped) for r in a.rows] == [
        (0, "G", Decimal("0.0"), False),
        (1, "P", None, False),
        (2, "P", Decimal("0.4"), True),
    ]


def test_a_null_price_is_never_under_the_threshold() -> None:
    """The reader hands T305c a `None`, which no `<` comparison can read as under."""
    threshold = Settings(_env_file=None).events.form4_xml_price_below
    facts = _one(trans=[_tr("1", TRANS_PRICEPERSHARE="")]).rows
    assert facts[0].price is None
    assert not any(r.price is not None and r.price < threshold for r in facts)


def test_identity_bad_cases() -> None:
    cases: dict[str, dict[str, object]] = {
        "blank issuer": {"subs": [_sub(ISSUERCIK="")]},
        "non-numeric issuer": {"subs": [_sub(ISSUERCIK="abc")]},
        "no owner": {"owners": []},
        "blank owner": {"owners": [_own(cik="")]},
        "duplicate owner": {"owners": [_own(), _own(rel="Officer")]},
        "owner of another accession only": {"owners": [_own(acc="0000000000-26-000002")]},
        "duplicate submission": {"subs": [_sub(), _sub()]},
    }
    for label, kw in cases.items():
        a = _one(**kw)
        assert a.identity_bad, label
        assert a.transactions == (), label
        assert len(a.rows) == 1, label


def test_two_different_owners_are_not_identity_bad() -> None:
    a = _one(owners=[_own("0000222222"), _own("0000333333", "Officer")])
    assert not a.identity_bad
    assert len(a.transactions) == 2


def test_only_form_4_and_4a_are_read() -> None:
    other = "0000000000-26-000009"
    result = _read(
        subs=[
            _sub(),
            _sub(ACCESSION_NUMBER=other, DOCUMENT_TYPE="5"),
            _sub(ACCESSION_NUMBER="0000000000-26-000008", DOCUMENT_TYPE="3"),
            _sub(ACCESSION_NUMBER="0000000000-26-000007", DOCUMENT_TYPE="4/A"),
            _sub(ACCESSION_NUMBER="0000000000-26-000006", DOCUMENT_TYPE="5/A"),
            _sub(ACCESSION_NUMBER="0000000000-26-000005", DOCUMENT_TYPE="3/A"),
        ],
        owners=[_own(), _own(acc=other)],
        trans=[_tr("1"), _tr("2", ACCESSION_NUMBER=other)],
    )
    assert set(result.accessions) == {ACC, "0000000000-26-000007"}


def test_derivative_and_holding_tables_are_not_read() -> None:
    junk = {
        "DERIV_TRANS.tsv": b"\xff\xfenot a table",
        "DERIV_HOLDING.tsv": b"",
        "NONDERIV_HOLDING.tsv": b"ACCESSION_NUMBER\nbroken\x00",
        "FOOTNOTES.tsv": b"x",
    }
    a = _one(extra=junk)
    assert len(a.transactions) == 1


def test_header_fields_for_the_submissions_cross_check() -> None:
    a = _one(subs=[_sub(FILING_DATE="02-JAN-2026", DOCUMENT_TYPE="4/A", DATE_OF_ORIG_SUB="")])
    assert (a.filing_date, a.document_type) == (date(2026, 1, 2), "4/A")
    bad = _one(subs=[_sub(FILING_DATE="garbage")])
    assert bad.filing_date is None  # the cross-check then reads it as a disagreement


def test_a_ragged_row_fails_the_quarter_closed() -> None:
    """A tab inside a cell shifts the later cells; reading it would hide a row from the
    under-threshold hold-back, so the quarter is unreadable."""
    for bad in (_tr("1", SECURITY_TITLE="Common\tStock"), _tr("1", TRANS_SHARES="1\t0")):
        with pytest.raises(bulk.InsiderBulkUnreadableError):
            _read(trans=[bad])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("SUBMISSION.tsv", _tsv(SUBMISSION, [_sub()]).replace("\t0000111111", ""))
        z.writestr("REPORTINGOWNER.tsv", _tsv(OWNER, [_own()]))
        z.writestr("NONDERIV_TRANS.tsv", _tsv(TRANS, [_tr("1")]))
    buffer.seek(0)
    with pytest.raises(bulk.InsiderBulkUnreadableError):
        bulk.read_insider_bulk(buffer)


# --- fail closed ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"sub_cols": [c for c in SUBMISSION if c != "ISSUERCIK"]},
        {"sub_cols": [c for c in SUBMISSION if c != "DOCUMENT_TYPE"]},
        {"own_cols": [c for c in OWNER if c != "RPTOWNER_RELATIONSHIP"]},
        {"trans_cols": [c for c in TRANS if c != "NONDERIV_TRANS_SK"]},
        {"trans_cols": [c for c in TRANS if c != "TRANS_PRICEPERSHARE"]},
        {"trans_cols": [c for c in TRANS if c != "DIRECT_INDIRECT_OWNERSHIP"]},
        {"drop": ("NONDERIV_TRANS",)},
        {"drop": ("SUBMISSION",)},
        {"drop": ("REPORTINGOWNER",)},
    ],
)
def test_a_missing_column_or_table_fails_the_quarter_closed(kw: dict[str, object]) -> None:
    with pytest.raises(bulk.InsiderBulkUnreadableError):
        _read(**kw)


def test_an_empty_table_or_a_non_zip_fails_closed() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("SUBMISSION.tsv", "")
        z.writestr("REPORTINGOWNER.tsv", _tsv(OWNER, []))
        z.writestr("NONDERIV_TRANS.tsv", _tsv(TRANS, []))
    buffer.seek(0)
    with pytest.raises(bulk.InsiderBulkUnreadableError):
        bulk.read_insider_bulk(buffer)
    with pytest.raises(bulk.InsiderBulkUnreadableError):
        bulk.read_insider_bulk(io.BytesIO(b"not a zip"))


def test_header_only_tables_read_as_an_empty_quarter() -> None:
    assert _read(subs=[], owners=[], trans=[]).accessions == {}


# --- the raw fetch: 404 is unpublished, and the request passes the shared limiter ----


def _settings(tmp_path: Path) -> Settings:
    base = Settings(
        _env_file=None,
        sec_edgar_user_agent="TradePartner test-agent",
        edgar={"cache_dir": str(tmp_path)},
    )
    fast = base.edgar.model_copy(update={"requests_per_second": 1000.0})
    return base.model_copy(update={"edgar": fast})


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    edgar_raw._LIMITER._last_request_monotonic = None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_insider_bulk_zip_streams_into_the_cache_and_a_404_reads_as_unpublished(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "2026q3" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(
            200, content=RECORDED.read_bytes(), headers={"Last-Modified": "Tue, 07 Apr 2026"}
        )

    settings = _settings(tmp_path)
    client = _client(handler)
    got = edgar_raw.insider_bulk_zip("2026q1", settings=settings, client=client)
    assert got is not None
    path, headers = got
    assert path == tmp_path / "insider" / "2026q1_form345.zip"
    assert headers["Last-Modified"] == "Tue, 07 Apr 2026"
    assert seen[0].url.path.endswith("/insider-transactions-data-sets/2026q1_form345.zip")
    assert seen[0].headers["User-Agent"] == "TradePartner test-agent"
    assert set(bulk.read_insider_bulk(path).accessions) == set(ACCESSIONS)
    assert edgar_raw.insider_bulk_zip("2026q3", settings=settings, client=client) is None
    assert not (tmp_path / "insider" / "2026q3_form345.zip").exists()


def test_a_non_404_failure_is_not_read_as_unpublished(tmp_path: Path) -> None:
    client = _client(lambda request: httpx.Response(403))
    settings = _settings(tmp_path)
    settings = settings.model_copy(
        update={"edgar": settings.edgar.model_copy(update={"rate_limit_wait_seconds": 0.0})}
    )
    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.insider_bulk_zip("2026q1", settings=settings, client=client)


def test_insider_bulk_validators_read_a_404_as_unpublished(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        if "2026q3" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Content-Length": "12", "ETag": '"x"'})

    settings = _settings(tmp_path)
    client = _client(handler)
    headers = edgar_raw.insider_bulk_validators("2026q1", settings=settings, client=client)
    assert headers is not None and headers["ETag"] == '"x"'
    assert edgar_raw.insider_bulk_validators("2026q3", settings=settings, client=client) is None


@pytest.mark.parametrize("quarter", ["2026", "2026q5", "2026_02", "../x", "2026Q1", ""])
def test_a_malformed_quarter_never_reaches_a_url(tmp_path: Path, quarter: str) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200)

    client = _client(handler)
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.insider_bulk_zip(quarter, settings=_settings(tmp_path), client=client)
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.insider_bulk_validators(quarter, settings=_settings(tmp_path), client=client)
    assert calls == []


def test_the_zip_request_passes_the_shared_limiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(edgar_raw._LIMITER, "wait", lambda interval: waits.append(interval))
    client = _client(lambda request: httpx.Response(200, content=RECORDED.read_bytes()))
    settings = _settings(tmp_path)
    edgar_raw.insider_bulk_zip("2026q1", settings=settings, client=client)
    edgar_raw.insider_bulk_validators("2026q1", settings=settings, client=client)
    assert waits == [pytest.approx(1 / 1000.0)] * 2


def test_the_limiter_spaces_back_to_back_zip_requests(tmp_path: Path) -> None:
    """Two requests at 5 per second are at least 0.2 s apart (measured, not mocked)."""
    import time

    stamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        return httpx.Response(200, content=RECORDED.read_bytes())

    settings = _settings(tmp_path)
    settings = settings.model_copy(
        update={"edgar": settings.edgar.model_copy(update={"requests_per_second": 5.0})}
    )
    client = _client(handler)
    edgar_raw.insider_bulk_zip("2026q1", settings=settings, client=client)
    edgar_raw.insider_bulk_zip("2026q2", settings=settings, client=client)
    assert stamps[1] - stamps[0] >= 0.19

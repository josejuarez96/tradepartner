"""Pure Form 4 bulk route rules (plan T305c, #2498)."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import date
from decimal import Decimal

import pytest

from tradepartner.adapters import form4_route as r
from tradepartner.adapters.edgar import Form4Transaction
from tradepartner.adapters.edgar_insider_bulk import BulkAccession, BulkRowFact

FILED = date(2026, 3, 10)
BELOW = Decimal("1.00")
CODES = ("P", "S")


def _acc(rows: list[tuple[str, str | None]] | None = None, **kw: object) -> BulkAccession:
    facts = tuple(
        BulkRowFact(
            line_no=i,
            transaction_code=code,
            price=None if price is None else Decimal(price),
            skipped=False,
        )
        for i, (code, price) in enumerate(rows if rows is not None else [("P", "25.00")])
    )
    base: dict[str, object] = {
        "accession": "0000000000-26-000001",
        "document_type": "4",
        "filing_date": FILED,
        "issuer_cik": "0000000001",
        "filer_ciks": ("0000000002",),
        "transactions": (),
        "skipped": 0,
        "rows": facts,
        "order_bad": False,
        "identity_bad": False,
    }
    base.update(kw)
    return BulkAccession(**base)  # type: ignore[arg-type]


def _route(
    bulk: BulkAccession | None, state: r.QuarterState = r.QuarterState.PUBLISHED, **kw: object
) -> r.AccessionRoute:
    args: dict[str, object] = {
        "state": state,
        "submissions_form": "4",
        "submissions_filing_date": FILED,
        "price_below": BELOW,
        "zero_price_codes": CODES,
    }
    args.update(kw)
    return r.route_accession(bulk, **args)  # type: ignore[arg-type]


# --- quarters -------------------------------------------------------------------


def test_quarter_of_and_previous() -> None:
    assert r.quarter_of(date(2026, 1, 1)) == r.Quarter(2026, 1)
    assert r.quarter_of(date(2026, 12, 31)) == r.Quarter(2026, 4)
    assert r.Quarter(2026, 1).previous() == r.Quarter(2025, 4)
    assert r.Quarter(2026, 3).previous() == r.Quarter(2026, 2)


@pytest.mark.parametrize(
    ("quarter", "downloaded", "expected"),
    [
        (r.Quarter(2026, 4), False, r.QuarterState.UNPUBLISHED),  # the run date's quarter
        (r.Quarter(2026, 3), False, r.QuarterState.UNPUBLISHED),  # the one before
        (r.Quarter(2026, 2), False, r.QuarterState.MISSING),  # one older
        (r.Quarter(2016, 1), False, r.QuarterState.MISSING),
        (r.Quarter(2026, 4), True, r.QuarterState.PUBLISHED),
        (r.Quarter(2020, 1), True, r.QuarterState.PUBLISHED),
    ],
)
def test_quarter_state_from_the_download(
    quarter: r.Quarter, downloaded: bool, expected: r.QuarterState
) -> None:
    assert r.quarter_state(quarter, downloaded=downloaded, run_date=date(2026, 10, 28)) is expected


def test_quarter_state_across_the_year_boundary() -> None:
    run = date(2026, 2, 1)  # Q1 2026, so Q4 2025 is the one before
    unpublished = r.QuarterState.UNPUBLISHED
    assert r.quarter_state(r.Quarter(2026, 1), downloaded=False, run_date=run) is unpublished
    assert r.quarter_state(r.Quarter(2025, 4), downloaded=False, run_date=run) is unpublished
    missing = r.quarter_state(r.Quarter(2025, 3), downloaded=False, run_date=run)
    assert missing is r.QuarterState.MISSING


# --- the route ------------------------------------------------------------------


def test_plain_accession_stays_in_bulk() -> None:
    assert _route(_acc()) == r.AccessionRoute(r.Route.BULK, None)


def test_p_with_zero_price_routes_the_whole_accession_to_xml() -> None:
    got = _route(_acc([("A", "30.00"), ("P", "0.00")]))
    assert got == r.AccessionRoute(r.Route.XML, r.UNDER_THRESHOLD)


def test_any_row_under_one_dollar_routes_to_xml() -> None:
    assert _route(_acc([("A", "30.00"), ("A", "0.99")])).reason == r.UNDER_THRESHOLD
    assert _route(_acc([("P", "0.01")])).route is r.Route.XML


def test_one_dollar_exactly_and_the_rounded_edge_stay_in_bulk() -> None:
    assert _route(_acc([("P", "1.00")])).route is r.Route.BULK  # a true 0.995 reads 1.00


def test_a_with_zero_price_and_a_null_price_stay_in_bulk() -> None:
    assert _route(_acc([("A", "0.00")])).route is r.Route.BULK
    assert _route(_acc([("P", None)])).route is r.Route.BULK
    assert _route(_acc([("G", "0.00"), ("M", "0.00"), ("F", "0.00")])).route is r.Route.BULK


def test_zero_price_codes_come_from_the_argument() -> None:
    assert _route(_acc([("A", "0.00")]), zero_price_codes=("A",)).route is r.Route.XML
    assert _route(_acc([("P", "0.00")]), zero_price_codes=()).route is r.Route.BULK


def test_a_skipped_row_with_a_low_price_still_holds_the_accession_back() -> None:
    skipped = BulkRowFact(line_no=1, transaction_code="P", price=Decimal("0.50"), skipped=True)
    acc = _acc()
    acc = dataclasses.replace(acc, rows=(*acc.rows, skipped))
    assert _route(acc).reason == r.UNDER_THRESHOLD


def test_unpublished_quarter_routes_to_xml_without_a_counter() -> None:
    got = _route(None, r.QuarterState.UNPUBLISHED)
    assert got == r.AccessionRoute(r.Route.XML, None)
    assert _route(_acc(), r.QuarterState.UNPUBLISHED).route is r.Route.XML


def test_an_older_quarters_404_routes_nothing_and_is_counted() -> None:
    for bulk in (None, _acc()):
        assert _route(bulk, r.QuarterState.MISSING) == r.AccessionRoute(
            r.Route.NONE, "sec_forms_bulk_quarter_missing"
        )


def test_bulk_missing_accession_routes_to_xml() -> None:
    assert _route(None) == r.AccessionRoute(r.Route.XML, "sec_forms_bulk_missing")


def test_order_bad_and_identity_bad_route_to_xml() -> None:
    assert _route(_acc(order_bad=True)) == r.AccessionRoute(r.Route.XML, "sec_forms_bulk_order_bad")
    assert _route(_acc(identity_bad=True)) == r.AccessionRoute(
        r.Route.XML, "sec_forms_bulk_identity_bad"
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"document_type": "4/A"},
        {"filing_date": date(2026, 3, 11)},
        {"filing_date": None},
    ],
)
def test_header_disagreement_routes_to_xml(kw: dict[str, object]) -> None:
    assert _route(_acc(**kw)) == r.AccessionRoute(r.Route.XML, "sec_forms_bulk_header_disagree")


def test_a_4a_agrees_with_a_4a_submissions_record() -> None:
    assert _route(_acc(document_type="4/A"), submissions_form="4/A").route is r.Route.BULK


# --- the order sample -----------------------------------------------------------


def _sha(accession: str) -> str:
    return hashlib.sha256(accession.encode()).hexdigest()


def test_order_sample_is_the_first_n_by_ascending_sha256() -> None:
    counts = {f"0000000000-26-{i:06d}": 2 + i % 3 for i in range(50)}
    got = r.order_sample(counts, 5)
    assert got == tuple(sorted(counts, key=_sha)[:5])
    assert [_sha(a) for a in got] == sorted(_sha(a) for a in got)


def test_only_accessions_with_two_or_more_transactions_qualify() -> None:
    counts = {"a": 1, "b": 0, "c": 2, "d": 5}
    assert set(r.order_sample(counts, 10)) == {"c", "d"}
    assert r.order_sample({"a": 1, "b": 1}, 10) == ()


def test_order_sample_does_not_depend_on_input_order() -> None:
    items = [(f"acc-{i}", 2) for i in range(30)]
    assert r.order_sample(dict(items), 7) == r.order_sample(dict(reversed(items)), 7)


def test_order_sample_rejects_a_negative_size() -> None:
    with pytest.raises(ValueError):
        r.order_sample({"a": 2}, -1)


# --- the row comparison ---------------------------------------------------------


def _row(filer: str = "0000000002", line: int = 0, **kw: object) -> Form4Transaction:
    base: dict[str, object] = {
        "issuer_cik": "0000000001",
        "filer_cik": filer,
        "line_no": line,
        "is_director": True,
        "is_officer": False,
        "is_ten_percent_owner": False,
        "is_other": False,
        "transaction_date": date(2026, 3, 9),
        "deemed_execution_date": None,
        "transaction_code": "P",
        "acquired_disposed": "A",
        "shares": Decimal("100"),
        "price": Decimal("25.00"),
        "shares_owned_after": Decimal("1000"),
        "ownership_form": "D",
        "security_title": "Common Stock",
        "date_of_original_submission": None,
    }
    base.update(kw)
    return Form4Transaction(**base)  # type: ignore[arg-type]


def test_equal_rows_agree_and_empty_agree() -> None:
    rows = [_row(line=0), _row(line=1, shares=Decimal("5"))]
    assert r.rows_agree(rows, list(rows))
    assert r.rows_agree([], [])


def test_price_and_shares_compare_at_two_decimals() -> None:
    xml = [
        _row(
            price=Decimal("25.0049"),
            shares=Decimal("100.004"),
            shares_owned_after=Decimal("1000.001"),
        )
    ]
    bulk = [
        _row(
            price=Decimal("25.00"), shares=Decimal("100.00"), shares_owned_after=Decimal("1000.00")
        )
    ]
    assert r.rows_agree(bulk, xml)
    assert not r.rows_agree([_row(price=Decimal("25.01"))], [_row(price=Decimal("25.00"))])


def test_a_null_price_never_equals_a_zero_price() -> None:
    assert not r.rows_agree([_row(price=None)], [_row(price=Decimal("0"))])
    assert r.rows_agree([_row(price=None)], [_row(price=None)])


def test_a_reordered_pair_fails() -> None:
    first = _row(line=0, transaction_code="P", shares=Decimal("100"))
    second = _row(line=1, transaction_code="S", acquired_disposed="D", shares=Decimal("40"))
    swapped = [
        dataclasses.replace(second, line_no=0),
        dataclasses.replace(first, line_no=1),
    ]
    assert not r.rows_agree([first, second], swapped)


def test_owner_position_does_not_matter_only_filer_and_line() -> None:
    a0, a1 = _row("0000000002", 0), _row("0000000002", 1, shares=Decimal("7"))
    b0, b1 = _row("0000000003", 0), _row("0000000003", 1, shares=Decimal("7"))
    bulk = [a0, b0, a1, b1]  # the data set orders by (line_no, filer_cik)
    xml = [a0, a1, b0, b1]  # the XML orders by owner then transaction
    assert r.rows_agree(bulk, xml)


def test_missing_extra_or_repeated_rows_fail() -> None:
    row = _row()
    assert not r.rows_agree([row], [])
    assert not r.rows_agree([row], [row, _row(line=1)])
    assert not r.rows_agree([row, row], [row, row])
    assert not r.rows_agree([_row("0000000002")], [_row("0000000009")])


def test_every_contract_field_is_compared() -> None:
    base = _row()
    changes: dict[str, object] = {
        "issuer_cik": "0000000009",
        "is_director": False,
        "is_officer": True,
        "is_ten_percent_owner": True,
        "is_other": True,
        "transaction_date": date(2026, 3, 8),
        "deemed_execution_date": date(2026, 3, 8),
        "transaction_code": "S",
        "acquired_disposed": "D",
        "shares": Decimal("101"),
        "price": Decimal("26"),
        "shares_owned_after": Decimal("999"),
        "ownership_form": "I",
        "security_title": "Class B",
        "date_of_original_submission": date(2026, 3, 1),
    }
    assert set(changes) == {f.name for f in dataclasses.fields(Form4Transaction)} - {
        "filer_cik",
        "line_no",
    }
    for name, value in changes.items():
        assert not r.rows_agree([base], [dataclasses.replace(base, **{name: value})]), name


@pytest.mark.parametrize("title", ["Common\nStock", " Common   Stock ", "Common\tStock"])
def test_security_title_whitespace_is_equivalent(title: str) -> None:
    base = _row(security_title="Common Stock")
    assert r.rows_agree([base], [dataclasses.replace(base, security_title=title)])


# --- the request plan -----------------------------------------------------------


def _counts(**kw: int) -> r.RequestCounts:
    base = {
        "submissions_first_pages": 100,
        "anchor_headers": 50,
        "xml_fetches": 800,
        "order_samples": 20,
    }
    base.update(kw)
    return r.RequestCounts(**base)


def test_plan_within_budget_is_not_refused() -> None:
    plan = r.plan_requests(_counts(), budget=1000)
    assert plan.planned == 970
    assert not plan.refused and plan.reason is None
    assert "970 of 1000" in plan.message()


def test_plan_over_budget_is_refused_and_counted() -> None:
    plan = r.plan_requests(_counts(xml_fetches=900), budget=1000)
    assert plan.planned == 1070
    assert plan.refused and plan.reason == "sec_forms_budget_refused"
    assert "sec_forms_budget_refused" in plan.message()


def test_plan_at_exactly_the_budget_runs() -> None:
    assert not r.plan_requests(_counts(), budget=970).refused


def test_plan_counts_only_the_remaining_work() -> None:
    full = r.plan_requests(_counts(), budget=1000)
    resumed = r.plan_requests(
        _counts(xml_fetches=0, anchor_headers=0, order_samples=0), budget=1000
    )
    assert resumed.planned == 100 < full.planned
    assert (
        r.plan_requests(
            _counts(submissions_first_pages=0, anchor_headers=0, xml_fetches=0, order_samples=0),
            budget=1,
        ).planned
        == 0
    )


def test_requests_already_spent_count_against_the_budget() -> None:
    assert r.plan_requests(_counts(), budget=1000, already_spent=31).refused
    assert not r.plan_requests(_counts(), budget=1000, already_spent=30).refused


def test_plan_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        r.RequestCounts(-1, 0, 0, 0)
    with pytest.raises(ValueError):
        r.plan_requests(_counts(), budget=0)
    with pytest.raises(ValueError):
        r.plan_requests(_counts(), budget=10, already_spent=-1)


def test_the_run_stops_on_reaching_the_budget() -> None:
    assert not r.budget_reached(999, 1000)
    assert r.budget_reached(1000, 1000)


def test_half_cent_rows_agree_under_the_shared_dera_rounding() -> None:
    assert r.rows_agree([_row(price=Decimal("0.125"))], [_row(price=Decimal("0.13"))])
    assert not r.rows_agree([_row(price=Decimal("0.125"))], [_row(price=Decimal("0.12"))])

"""`execution.lots_reconcile.compare` (Phase 4 plan T90; ADR 0010 amendment
2026-10-04): export rows built directly against a fixture ledger of journal rows.

The fixture ledger, tax year 2026 (one account, as `execution.lots` writes it):

- lot 1: 10 XYZ (CUSIP 98422D105) bought 2026-01-05 for 1,000.00; disposal 1
  sells it 2026-02-02 for 900.00, a 100.00 loss;
- lot 2: 10 XYZ bought 2026-02-10 for 950.00, inside the 30 days, so flag 1
  names it the replacement for disposal 1's whole loss (100.00 disallowed);
  disposal 2 sells it 2026-06-01 for 1,100.00;
- lot 3: 5 ABC (no CUSIP) bought 2026-03-02 for 100.00; disposal 3 sells it
  2026-04-01 for 120.00.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tradepartner.execution.lots_reconcile import (
    COST_BASIS,
    DIFFERENCE,
    DISALLOWED_LOSS,
    EXPECTED,
    MATCH,
    PROCEEDS,
    BrokerLotRow,
    FigureDifference,
    LedgerSetError,
    compare,
)
from tradepartner.store.journal import DisposalRow, LotRow, WashSaleFlagRow

_STAMP = datetime(2026, 12, 31, 22, 0, tzinfo=UTC)
XYZ_CUSIP = "98422D105"


def _lot(
    lot_id: int, symbol: str, cusip: str | None, day: date, qty: float, basis: float
) -> LotRow:
    return LotRow(
        lot_id=lot_id,
        account_id="PA1",
        account_type="paper",
        account_owner="self",
        security_id=f"SEC_{symbol}",
        symbol=symbol,
        cusip=cusip,
        trade_at=datetime(day.year, day.month, day.day, 15, tzinfo=UTC),
        trade_date_local=day,
        quantity=qty,
        cost_basis=basis,
        fill_id=lot_id,
        known_at=_STAMP,
        ingested_at=_STAMP,
    )


def _disposal(
    disposal_id: int, lot_id: int, day: date, qty: float, proceeds: float, pnl: float
) -> DisposalRow:
    return DisposalRow(
        disposal_id=disposal_id,
        lot_id=lot_id,
        account_id="PA1",
        trade_at=datetime(day.year, day.month, day.day, 15, tzinfo=UTC),
        trade_date_local=day,
        quantity=qty,
        proceeds=proceeds,
        realised_pnl=pnl,
        tax_year=day.year,
        fill_id=100 + disposal_id,
        known_at=_STAMP,
        ingested_at=_STAMP,
    )


LOTS = [
    _lot(1, "XYZ", XYZ_CUSIP, date(2026, 1, 5), 10.0, 1000.0),
    _lot(2, "XYZ", XYZ_CUSIP, date(2026, 2, 10), 10.0, 950.0),
    _lot(3, "ABC", None, date(2026, 3, 2), 5.0, 100.0),
]
DISPOSALS = [
    _disposal(1, 1, date(2026, 2, 2), 10.0, 900.0, -100.0),
    _disposal(2, 2, date(2026, 6, 1), 10.0, 1100.0, 150.0),
    _disposal(3, 3, date(2026, 4, 1), 5.0, 120.0, 20.0),
]
FLAGS = [
    WashSaleFlagRow(
        flag_id=1,
        disposal_id=1,
        replacement_lot_id=2,
        matched_quantity=10.0,
        disallowed_amount=100.0,
        scanned_at=_STAMP,
        known_at=_STAMP,
        ingested_at=_STAMP,
    )
]


def _row(
    symbol: str,
    day: date,
    qty: str,
    proceeds: str,
    basis: str,
    box_1g: str = "0",
    cusip: str | None = None,
) -> BrokerLotRow:
    return BrokerLotRow(
        symbol=symbol,
        cusip=cusip,
        trade_date=day,
        quantity=Decimal(qty),
        proceeds=Decimal(proceeds),
        cost_basis=Decimal(basis),
        disallowed_loss=Decimal(box_1g),
    )


def _year(**override: BrokerLotRow) -> list[BrokerLotRow]:
    """The export that agrees with the fixture ledger, one row per disposal,
    with any row replaced by keyword (`r1`, `r2`, `r3`)."""
    rows = {
        "r1": _row("XYZ", date(2026, 2, 2), "10", "900.00", "1000.00", "100.00", XYZ_CUSIP),
        "r2": _row("XYZ", date(2026, 6, 1), "10", "1100.00", "950.00", "0", XYZ_CUSIP),
        "r3": _row("ABC", date(2026, 4, 1), "5", "120.00", "100.00"),
    }
    rows.update(override)
    return list(rows.values())


def test_a_matching_year_is_clean() -> None:
    report = compare(_year(), DISPOSALS, LOTS, FLAGS)

    assert [(m.disposal_id, m.status) for m in report.matched] == [
        (1, MATCH),
        (2, MATCH),
        (3, MATCH),
    ]
    assert report.unmatched_rows == ()
    assert report.unmatched_disposals == ()
    assert report.clean
    assert not report.has_difference


def test_a_basis_difference_is_reported_with_both_figures() -> None:
    rows = _year(r3=_row("ABC", date(2026, 4, 1), "5", "120.00", "101.50"))

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    (abc,) = [m for m in report.matched if m.disposal_id == 3]
    assert abc.status == DIFFERENCE
    assert abc.differences == (FigureDifference(COST_BASIS, Decimal("101.50"), Decimal("100.00")),)
    assert report.has_difference
    assert not report.clean


def test_a_box_1g_difference_is_reported_with_both_figures() -> None:
    rows = _year(r1=_row("XYZ", date(2026, 2, 2), "10", "900.00", "1000.00", "0", XYZ_CUSIP))

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    (xyz,) = [m for m in report.matched if m.disposal_id == 1]
    assert xyz.status == DIFFERENCE
    assert xyz.mismatches == (
        FigureDifference(DISALLOWED_LOSS, Decimal("0.00"), Decimal("100.00")),
    )
    assert not report.clean


def test_a_proceeds_difference_is_reported() -> None:
    rows = _year(r3=_row("ABC", date(2026, 4, 1), "5", "119.99", "100.00"))

    (abc,) = [m for m in compare(rows, DISPOSALS, LOTS, FLAGS).matched if m.disposal_id == 3]

    assert abc.differences == (FigureDifference(PROCEEDS, Decimal("119.99"), Decimal("120.00")),)


def test_an_unmatched_broker_row_is_reported() -> None:
    extra = _row("DEF", date(2026, 5, 4), "3", "30.00", "33.00")

    report = compare([*_year(), extra], DISPOSALS, LOTS, FLAGS)

    assert report.unmatched_rows == (extra,)
    assert len(report.matched) == 3
    assert report.has_difference
    assert not report.clean


def test_an_unmatched_ledger_disposal_is_reported() -> None:
    rows = _year()[:2]  # the export lacks the ABC sale

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    assert [d.disposal_id for d in report.unmatched_disposals] == [3]
    assert report.has_difference
    assert not report.clean


def test_a_replacement_lot_disposal_is_reported_as_expected_not_a_mismatch() -> None:
    # The broker carries disposal 1's disallowed 100.00 onto lot 2's basis.
    rows = _year(r2=_row("XYZ", date(2026, 6, 1), "10", "1100.00", "1050.00", "0", XYZ_CUSIP))

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    (replacement,) = [m for m in report.matched if m.disposal_id == 2]
    assert replacement.replacement_lot
    assert replacement.status == EXPECTED
    assert replacement.expected == (
        FigureDifference(COST_BASIS, Decimal("1050.00"), Decimal("950.00")),
    )
    assert replacement.mismatches == ()
    assert not report.has_difference
    assert report.clean


def test_a_replacement_lot_proceeds_difference_is_still_a_mismatch() -> None:
    rows = _year(r2=_row("XYZ", date(2026, 6, 1), "10", "1090.00", "1050.00", "0", XYZ_CUSIP))

    (replacement,) = [
        m for m in compare(rows, DISPOSALS, LOTS, FLAGS).matched if m.disposal_id == 2
    ]

    assert replacement.status == DIFFERENCE
    assert [d.figure for d in replacement.mismatches] == [PROCEEDS]
    assert [d.figure for d in replacement.expected] == [COST_BASIS]


def test_zero_matched_rows_is_never_clean() -> None:
    assert not compare([], [], LOTS, FLAGS).clean
    report = compare([], DISPOSALS, LOTS, FLAGS)
    assert not report.clean
    assert len(report.unmatched_disposals) == 3


def test_cusip_matches_across_a_symbol_change() -> None:
    rows = _year(r1=_row("XYZN", date(2026, 2, 2), "10", "900.00", "1000.00", "100.00", XYZ_CUSIP))

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    assert report.clean


def test_a_different_cusip_under_the_same_symbol_does_not_match() -> None:
    rows = _year(r1=_row("XYZ", date(2026, 2, 2), "10", "900.00", "1000.00", "100.00", "000000000"))

    report = compare(rows, DISPOSALS, LOTS, FLAGS)

    assert [d.disposal_id for d in report.unmatched_disposals] == [1]
    assert len(report.unmatched_rows) == 1


def test_trade_date_and_quantity_must_match_exactly() -> None:
    wrong_day = _row("ABC", date(2026, 4, 2), "5", "120.00", "100.00")
    wrong_qty = _row("ABC", date(2026, 4, 1), "5.0001", "120.00", "100.00")

    report = compare([wrong_day, wrong_qty], DISPOSALS, LOTS, FLAGS)

    assert report.matched == ()
    assert report.unmatched_rows == (wrong_day, wrong_qty)


def test_two_equal_disposals_of_one_sale_pair_by_agreeing_figures() -> None:
    lots = [
        _lot(1, "ABC", None, date(2026, 1, 5), 5.0, 100.0),
        _lot(2, "ABC", None, date(2026, 1, 6), 5.0, 110.0),
    ]
    disposals = [
        _disposal(1, 1, date(2026, 3, 2), 5.0, 125.0, 25.0),
        _disposal(2, 2, date(2026, 3, 2), 5.0, 125.0, 15.0),
    ]
    rows = [
        _row("ABC", date(2026, 3, 2), "5", "125.00", "110.00"),
        _row("ABC", date(2026, 3, 2), "5", "125.00", "100.00"),
    ]

    report = compare(rows, disposals, lots, [])

    assert [m.disposal_id for m in report.matched] == [2, 1]
    assert report.clean


def test_a_row_agreeing_nowhere_never_takes_another_rows_exact_match() -> None:
    # One sale closes two equal lots: disposal 1 (basis 100) and 2 (basis 110).
    # The first row has disposal 2's basis but proceeds a cent off; the second
    # row agrees exactly with disposal 1, so it keeps it.
    lots = [
        _lot(1, "ABC", None, date(2026, 1, 5), 5.0, 100.0),
        _lot(2, "ABC", None, date(2026, 1, 6), 5.0, 110.0),
    ]
    disposals = [
        _disposal(1, 1, date(2026, 3, 2), 5.0, 125.0, 25.0),
        _disposal(2, 2, date(2026, 3, 2), 5.0, 125.0, 15.0),
    ]
    off_by_a_cent = _row("ABC", date(2026, 3, 2), "5", "125.01", "110.00")
    exact = _row("ABC", date(2026, 3, 2), "5", "125.00", "100.00")

    report = compare([off_by_a_cent, exact], disposals, lots, [])

    assert [(m.row, m.disposal_id) for m in report.matched] == [(off_by_a_cent, 2), (exact, 1)]
    assert report.matched[0].differences == (
        FigureDifference(PROCEEDS, Decimal("125.01"), Decimal("125.00")),
    )
    assert report.matched[1].differences == ()


def test_ledger_figures_round_to_the_cent() -> None:
    lots = [_lot(1, "ABC", None, date(2026, 1, 5), 3.0, 100.0)]
    disposals = [_disposal(1, 1, date(2026, 3, 2), 1.0, 33.335, 0.0016666)]
    rows = [_row("ABC", date(2026, 3, 2), "1", "33.34", "33.33")]

    assert compare(rows, disposals, lots, []).clean


def test_a_disposal_outside_the_lots_set_raises() -> None:
    with pytest.raises(LedgerSetError, match="lot 9"):
        compare([], [_disposal(1, 9, date(2026, 3, 2), 1.0, 1.0, 0.0)], LOTS, [])


@pytest.mark.parametrize(
    "field, value",
    [("quantity", Decimal(0)), ("proceeds", Decimal("NaN")), ("cost_basis", 1.5)],
)
def test_a_broker_row_refuses_bad_figures(field: str, value: object) -> None:
    values: dict[str, object] = {
        "symbol": "ABC",
        "cusip": None,
        "trade_date": date(2026, 3, 2),
        "quantity": Decimal(1),
        "proceeds": Decimal(1),
        "cost_basis": Decimal(1),
        "disallowed_loss": Decimal(0),
    }
    values[field] = value
    with pytest.raises(ValueError):
        BrokerLotRow(**values)  # type: ignore[arg-type]


def test_compare_writes_nothing_and_takes_no_connection() -> None:
    import inspect

    from tradepartner.execution import lots_reconcile

    source = inspect.getsource(lots_reconcile)
    assert "duckdb" not in source
    assert "journal.append" not in source
    assert list(inspect.signature(compare).parameters) == ["rows", "disposals", "lots", "flags"]

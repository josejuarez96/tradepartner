"""Tests for the wash-sale lot ledger `execution.lots` (Phase 4 plan T56, #308).

Spec req 13 and its "Wash-sale ledger" acceptance list, research G7: Pub 550
Example 1 exactly (R12), each replacement share absorbing one loss with losses
in disposal order (R13), same-day loss and gain blocks apart (R14), a December
sale bought back in January (R15), a gain flags nothing, a later acquisition
re-scans an earlier loss, the merged-lot rule for an order with a
`price_implied` fill, trade date (exchange-local) rather than settlement date,
and a superseded fill building no lot through the real `fills_for` accessor.
The no-literal check on `lots.py` is T50's (`tests/test_no_literals.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import count

import duckdb
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.execution import lots
from tradepartner.execution.lots import (
    WASH_SALE_WINDOW_DAYS,
    LedgerAccount,
    LotLedgerError,
    rebuild,
)
from tradepartner.store import schema
from tradepartner.store.journal import (
    FillRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    append,
    fills_for,
)

ACCOUNT = LedgerAccount(account_id="paper-1", account_type="paper", owner="self")
_STAMP = datetime(2030, 1, 1, tzinfo=UTC)
_ids = count(1)


def _at(day: date, hour_utc: int = 20) -> datetime:
    """A fill time on `day`: 20:00 UTC is 15:00 or 16:00 in New York."""
    return datetime(day.year, day.month, day.day, hour_utc, tzinfo=UTC)


def _order(coid: str, side: str, security_id: str = "SEC_X", symbol: str = "XYZ") -> OrderRow:
    return OrderRow(
        client_order_id=coid,
        decision_id=1,
        run_id=1,
        session=date(2024, 1, 2),
        attempt=1,
        phase="buy" if side == "buy" else "sell",
        security_id=security_id,
        symbol=symbol,
        side=side,
        quantity=1.0,
        sells_in_flight_at_submit=False,
        known_at=_STAMP,
        ingested_at=_STAMP,
    )


def _fill(
    order: OrderRow,
    quantity: float,
    price: float,
    at: datetime,
    *,
    implied: bool = False,
) -> OrderedFill:
    fill_id = next(_ids)
    row = FillRow(
        fill_id=fill_id,
        client_order_id=order.client_order_id,
        filled_at=at,
        quantity=quantity,
        price=price,
        price_implied=implied,
        broker_fill_id=("synthetic:" if implied else "bf-") + str(fill_id),
        source="broker_status" if implied else "broker_feed",
        known_at=_STAMP,
        ingested_at=_STAMP,
    )
    return OrderedFill(row, order.side, order.security_id, order.symbol, 1, 1)


class Book:
    """Orders and fills for one test, one order per trade unless told."""

    def __init__(self) -> None:
        self.orders: list[OrderRow] = []
        self.fills: list[OrderedFill] = []

    def trade(
        self,
        side: str,
        quantity: float,
        price: float,
        day: date,
        *,
        security_id: str = "SEC_X",
        hour_utc: int = 20,
    ) -> str:
        order = _order(f"o{len(self.orders) + 1}", side, security_id)
        self.orders.append(order)
        self.fills.append(_fill(order, quantity, price, _at(day, hour_utc)))
        return order.client_order_id

    def run(self) -> tuple[list[lots.Lot], list[lots.Disposal], list[lots.WashSaleFlag]]:
        return rebuild(self.fills, self.orders, {}, ACCOUNT)


def test_the_window_is_the_statutes_thirty_days() -> None:
    assert WASH_SALE_WINDOW_DAYS == 30


def test_pub_550_example_1() -> None:
    """G7 R12: 100 bought 2024-09-20 for $5,000; 50 on 2024-12-13 for $2,750;
    25 on 2024-12-20 for $1,125; the 100 sold 2025-01-03 for $4,000."""
    book = Book()
    book.trade("buy", 100, 50.0, date(2024, 9, 20))
    book.trade("buy", 50, 55.0, date(2024, 12, 13))
    book.trade("buy", 25, 45.0, date(2024, 12, 20))
    book.trade("sell", 100, 40.0, date(2025, 1, 3))

    found_lots, disposals, flags = book.run()

    assert [(lot.quantity, lot.cost_basis) for lot in found_lots] == [
        (100.0, 5000.0),
        (50.0, 2750.0),
        (25.0, 1125.0),
    ]
    [disposal] = disposals
    assert disposal.lot_id == found_lots[0].lot_id  # FIFO: the September lot
    assert (disposal.quantity, disposal.proceeds, disposal.realised_pnl) == (100.0, 4000.0, -1000.0)
    assert disposal.tax_year == 2025
    assert [(f.replacement_lot_id, f.matched_quantity, f.disallowed_amount) for f in flags] == [
        (found_lots[1].lot_id, 50.0, 500.0),
        (found_lots[2].lot_id, 25.0, 250.0),
    ]
    assert sum(f.disallowed_amount for f in flags) == 750.0
    assert disposal.quantity - sum(f.matched_quantity for f in flags) == 25.0  # deductible shares
    assert {f.disposal_id for f in flags} == {disposal.disposal_id}


def test_r15_december_sale_bought_back_in_january() -> None:
    """Pub 550: 300 sold 2024-12-27 at $125 (a $33 loss per share), 250
    identical shares bought 2025-01-10."""
    book = Book()
    book.trade("buy", 300, 158.0, date(2024, 6, 3))
    book.trade("sell", 300, 125.0, date(2024, 12, 27))
    book.trade("buy", 250, 126.0, date(2025, 1, 10))

    found_lots, [disposal], [flag] = book.run()

    assert disposal.tax_year == 2024
    assert disposal.realised_pnl == -9900.0
    assert flag.replacement_lot_id == found_lots[1].lot_id
    assert found_lots[1].trade_date_local.year == 2025
    assert (flag.matched_quantity, flag.disallowed_amount) == (250.0, 8250.0)


def test_a_later_acquisition_rescans_an_earlier_loss() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 3, 3))
    book.trade("sell", 10, 90.0, date(2025, 6, 2))
    assert book.run()[2] == []

    book.trade("buy", 4, 95.0, date(2025, 6, 2) + timedelta(days=WASH_SALE_WINDOW_DAYS))
    _, _, [flag] = book.run()
    assert (flag.matched_quantity, flag.disallowed_amount) == (4.0, 40.0)

    late = Book()
    late.orders, late.fills = book.orders[:2], book.fills[:2]
    late.trade("buy", 4, 95.0, date(2025, 6, 2) + timedelta(days=WASH_SALE_WINDOW_DAYS + 1))
    assert late.run()[2] == []


def test_a_purchase_the_day_before_the_window_opens_is_not_matched() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 1, 2))
    book.trade("buy", 5, 100.0, date(2025, 6, 2) - timedelta(days=WASH_SALE_WINDOW_DAYS + 1))
    book.trade("buy", 5, 100.0, date(2025, 6, 2) - timedelta(days=WASH_SALE_WINDOW_DAYS))
    book.trade("sell", 10, 90.0, date(2025, 6, 2))

    found_lots, _, [flag] = book.run()

    assert flag.replacement_lot_id == found_lots[2].lot_id
    assert flag.matched_quantity == 5.0


def test_r13_a_replacement_share_absorbs_one_loss_in_disposal_order() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 1, 2))
    book.trade("buy", 10, 100.0, date(2025, 1, 3))
    book.trade("sell", 10, 90.0, date(2025, 3, 3))  # loss 1: 10 x 10
    book.trade("sell", 10, 80.0, date(2025, 3, 4))  # loss 2: 10 x 20
    book.trade("buy", 15, 85.0, date(2025, 3, 10))  # 15 replacement shares

    found_lots, disposals, flags = book.run()

    replacement = found_lots[2].lot_id
    assert [(f.disposal_id, f.replacement_lot_id, f.matched_quantity) for f in flags] == [
        (disposals[0].disposal_id, replacement, 10.0),
        (disposals[1].disposal_id, replacement, 5.0),
    ]
    assert [f.disallowed_amount for f in flags] == [100.0, 100.0]


def test_replacement_shares_are_matched_in_acquisition_order() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 1, 2))
    book.trade("sell", 10, 90.0, date(2025, 3, 3))
    book.trade("buy", 6, 91.0, date(2025, 3, 12))
    book.trade("buy", 6, 92.0, date(2025, 3, 5))  # bought earlier, journaled later

    found_lots, _, flags = book.run()

    by_id = {lot.lot_id: lot for lot in found_lots}
    matched = [(by_id[f.replacement_lot_id].trade_date_local, f.matched_quantity) for f in flags]
    assert matched == [(date(2025, 3, 5), 6.0), (date(2025, 3, 12), 4.0)]


def test_r14_same_day_loss_and_gain_blocks_are_kept_apart() -> None:
    """One sale closes a cheap lot at a gain and a dear lot at a loss; the
    loss is flagged in full and never netted against the gain."""
    book = Book()
    book.trade("buy", 10, 50.0, date(2025, 1, 2))
    book.trade("buy", 10, 150.0, date(2025, 1, 3))
    book.trade("sell", 20, 100.0, date(2025, 3, 3))
    book.trade("buy", 10, 100.0, date(2025, 3, 20))

    found_lots, disposals, [flag] = book.run()

    gain, loss = disposals
    assert (gain.realised_pnl, loss.realised_pnl) == (500.0, -500.0)
    assert gain.trade_date_local == loss.trade_date_local
    assert flag.disposal_id == loss.disposal_id
    assert (flag.matched_quantity, flag.disallowed_amount) == (10.0, 500.0)
    assert flag.replacement_lot_id == found_lots[2].lot_id


def test_shares_sold_in_the_same_sale_are_not_their_own_replacement() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 3, 1))
    book.trade("buy", 10, 100.0, date(2025, 3, 3))
    book.trade("sell", 20, 90.0, date(2025, 3, 10))

    _, disposals, flags = book.run()

    assert len(disposals) == 2
    assert flags == []


def test_literal_rule_the_unsold_rest_of_a_partly_sold_lot_is_matched() -> None:
    """Pinned while the owner decides (#308): the statute's literal reading
    counts the 6 unsold shares of the same purchase as replacements."""
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 3, 1))
    book.trade("sell", 4, 90.0, date(2025, 3, 20))

    [lot], _, [flag] = book.run()

    assert (flag.replacement_lot_id, flag.matched_quantity) == (lot.lot_id, 4.0)


def test_literal_rule_another_fill_of_the_same_order_is_matched() -> None:
    """Pinned while the owner decides (#308): a buy filled in two parts is two
    lots, and a loss on the first part is matched to the second."""
    book = Book()
    order = _order("buy1", "buy")
    book.orders.append(order)
    day = date(2025, 3, 3)
    book.fills += [_fill(order, 5, 100.0, _at(day, 14)), _fill(order, 5, 100.0, _at(day, 15))]
    book.trade("sell", 5, 90.0, date(2025, 3, 20))

    found_lots, _, [flag] = book.run()

    assert (flag.replacement_lot_id, flag.matched_quantity) == (found_lots[1].lot_id, 5.0)


def test_the_window_counts_new_york_dates_not_utc_dates() -> None:
    sale_day = date(2025, 3, 3)  # the loss sale at 20:00 UTC, 15:00 in New York
    inside = sale_day + timedelta(days=WASH_SALE_WINDOW_DAYS + 1)  # 02:00 UTC: 30 local days
    outside = sale_day + timedelta(days=WASH_SALE_WINDOW_DAYS + 2)  # 02:00 UTC: 31 local days
    for buy_day, matched in ((inside, True), (outside, False)):
        book = Book()
        book.trade("buy", 10, 100.0, date(2025, 1, 2))
        book.trade("sell", 10, 90.0, sale_day)
        book.trade("buy", 10, 90.0, buy_day, hour_utc=2)
        assert bool(book.run()[2]) is matched, buy_day


def test_a_gain_disposal_flags_nothing() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 1, 2))
    book.trade("sell", 10, 110.0, date(2025, 3, 3))
    book.trade("buy", 10, 105.0, date(2025, 3, 4))

    _, [disposal], flags = book.run()

    assert disposal.realised_pnl == 100.0
    assert flags == []


def test_only_the_same_security_replaces() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, date(2025, 1, 2))
    book.trade("sell", 10, 90.0, date(2025, 3, 3))
    book.trade("buy", 10, 90.0, date(2025, 3, 4), security_id="SEC_OTHER")

    assert book.run()[2] == []


def test_fifo_partial_lots_keep_exact_basis() -> None:
    book = Book()
    book.trade("buy", 0.3, 10.0, date(2025, 1, 2))
    book.trade("buy", 0.6, 20.0, date(2025, 1, 3))
    book.trade("sell", 0.4, 30.0, date(2025, 2, 3))
    book.trade("sell", 0.5, 30.0, date(2025, 2, 4))

    found_lots, disposals, _ = book.run()

    assert [(d.lot_id, d.quantity) for d in disposals] == [
        (found_lots[0].lot_id, 0.3),
        (found_lots[1].lot_id, 0.1),
        (found_lots[1].lot_id, 0.5),
    ]
    assert sum(d.proceeds - d.realised_pnl for d in disposals) == pytest.approx(15.0)
    assert disposals[2].proceeds - disposals[2].realised_pnl == pytest.approx(10.0)


def test_selling_more_than_the_ledger_holds_raises() -> None:
    book = Book()
    book.trade("buy", 1, 10.0, date(2025, 1, 2))
    book.trade("sell", 2, 10.0, date(2025, 1, 3))
    with pytest.raises(LotLedgerError, match="more than"):
        book.run()


def test_lots_carry_trade_date_account_columns_and_cusip() -> None:
    book = Book()
    # 2025-12-31 20:00 UTC is 15:00 in New York: settles in 2026, trades in 2025.
    book.trade("buy", 5, 10.0, date(2025, 12, 31))
    # 2026-01-02 02:00 UTC is still 2026-01-01 21:00 in New York.
    book.trade("sell", 5, 9.0, date(2026, 1, 2), hour_utc=2)
    asset = Asset(tradable=True, fractionable=True, status="active", cusip="123456789")

    [lot], [disposal], _ = rebuild(book.fills, book.orders, {"XYZ": asset}, ACCOUNT)

    assert (lot.trade_at, lot.trade_date_local) == (_at(date(2025, 12, 31)), date(2025, 12, 31))
    assert (lot.account_id, lot.account_type, lot.account_owner) == ("paper-1", "paper", "self")
    assert (lot.cusip, lot.symbol, lot.security_id) == ("123456789", "XYZ", "SEC_X")
    assert lot.fill_id == book.fills[0].fill.fill_id
    assert disposal.trade_date_local == date(2026, 1, 1)
    assert disposal.tax_year == 2026
    assert disposal.account_id == "paper-1"
    assert disposal.fill_id == book.fills[1].fill.fill_id


def test_cusip_is_none_when_assets_lack_it() -> None:
    book = Book()
    book.trade("buy", 5, 10.0, date(2025, 1, 2))
    [lot], _, _ = book.run()
    assert lot.cusip is None


def test_an_order_with_an_implied_fill_merges_into_one_lot_at_its_average() -> None:
    """Two real fills and a synthetic residual at a negative implied price:
    the order is one lot at its average price, basis never below zero."""
    book = Book()
    order = _order("buy1", "buy")
    book.orders.append(order)
    day = date(2025, 4, 1)
    book.fills += [
        _fill(order, 0.5, 100.0, _at(day, 14)),
        _fill(order, 0.3, 100.0, _at(day, 15)),
        _fill(order, 0.2, -10.0, _at(day, 16), implied=True),
    ]
    [lot], _, _ = book.run()
    assert lot.quantity == 1.0
    assert lot.cost_basis == pytest.approx(78.0)
    assert lot.cost_basis >= 0
    assert lot.fill_id is None
    assert lot.trade_at == _at(day, 14)

    sell = _order("sell1", "sell")
    book.orders.append(sell)
    book.fills += [
        _fill(sell, 0.9, 50.0, _at(day + timedelta(days=1))),
        _fill(sell, 0.1, -5.0, _at(day + timedelta(days=1), 21), implied=True),
    ]
    _, [disposal], _ = book.run()
    assert disposal.quantity == 1.0
    assert disposal.proceeds == pytest.approx(44.5)
    assert disposal.fill_id is None


def test_a_fill_without_its_order_raises() -> None:
    book = Book()
    book.trade("buy", 1, 10.0, date(2025, 1, 2))
    with pytest.raises(LotLedgerError, match="no order"):
        rebuild(book.fills, [], {}, ACCOUNT)


def test_a_superseded_fill_passed_in_raises() -> None:
    book = Book()
    book.trade("buy", 1, 10.0, date(2025, 1, 2))
    old = book.fills[0]
    stale = OrderedFill(
        replace(old.fill, superseded_by=99),
        old.side,
        old.security_id,
        old.symbol,
        1,
        1,
    )
    with pytest.raises(LotLedgerError, match="superseded"):
        rebuild([stale], book.orders, {}, ACCOUNT)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def test_a_superseded_fill_builds_no_lot_through_the_accessor(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    append(
        conn,
        PaperRunRow(
            run_id=1,
            window_id=1,
            started_at=_STAMP,
            invoked_by="tty",
            code_version="test",
            known_at=_STAMP,
            ingested_at=_STAMP,
        ),
    )
    order = _order("a", "buy")
    append(conn, order)
    day = date(2025, 4, 1)
    real = _fill(order, 0.6, 100.0, _at(day, 14)).fill
    synthetic = _fill(order, 0.4, 100.0, _at(day, 16), implied=True).fill
    late = _fill(order, 0.4, 100.0, _at(day, 15)).fill
    for row in (real, synthetic):
        append(conn, replace(row, fill_id=None))
    append(conn, replace(late, fill_id=None, superseded_by=2))

    live = fills_for(conn)
    [lot], _, _ = rebuild(live, [order], {}, ACCOUNT)

    assert len(live) == 2
    assert lot.quantity == 1.0  # the superseded 0.4 is not counted again

"""The ledger: positions and cash the journal implies (Phase 4 plan T51).

Every expected number is computed by hand in the test's comments. The numeric
literal check on `execution/ledger.py` is T50's (`tests/test_no_literals.py`).
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.risk import round_down
from tradepartner.store import schema
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import insert_row
from tradepartner.store.journal import (
    AdjustmentRow,
    FillRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    append,
    fills_for,
)

WINDOW = 1
RUN = 1
D1 = date(2026, 10, 1)
D5 = date(2026, 10, 5)
D7 = date(2026, 10, 7)
D8 = date(2026, 10, 8)
D9 = date(2026, 10, 9)
A, B, C = "SEC_A", "SEC_B", "SEC_C"
TOLERANCE = 1e-6  # stands in for the frozen risk.reconcile_quantity_tolerance
_ADJUSTMENT_IDS = itertools.count(1)


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _order(security_id: str, side: str, session: date, attempt: int = 1) -> OrderRow:
    stamp = _utc(session, 13)
    return OrderRow(
        client_order_id=client_order_id("tp", "main", session, security_id, side, attempt),
        decision_id=1,
        run_id=RUN,
        session=session,
        attempt=attempt,
        phase=side,
        security_id=security_id,
        symbol=security_id.removeprefix("SEC_"),
        side=side,
        notional=100.0,
        sells_in_flight_at_submit=False,
        known_at=stamp,
        ingested_at=stamp,
    )


def _fill(
    order: OrderRow,
    quantity: float,
    price: float,
    *,
    filled_at: datetime | None = None,
    known_at: datetime | None = None,
    window_id: int = WINDOW,
    fill_id: int = 1,
) -> OrderedFill:
    filled = filled_at or _utc(order.session, 14)
    known = known_at or _utc(order.session, 21)
    row = FillRow(
        fill_id=fill_id,
        client_order_id=order.client_order_id,
        filled_at=filled,
        quantity=quantity,
        price=price,
        price_implied=False,
        broker_fill_id=f"bf-{fill_id}",
        source="broker_feed",
        known_at=known,
        ingested_at=known,
    )
    return OrderedFill(row, order.side, order.security_id, order.symbol, order.run_id, window_id)


def _adjustment(
    kind: str,
    session: date,
    *,
    security_id: str | None = None,
    quantity: float | None = None,
    cash: float | None = None,
    known_at: datetime | None = None,
    window_id: int = WINDOW,
    adjustment_id: int | None = None,
) -> AdjustmentRow:
    known = known_at or _utc(session, 21)
    return AdjustmentRow(
        adjustment_id=adjustment_id or next(_ADJUSTMENT_IDS),
        window_id=window_id,
        run_id=RUN,
        session=session,
        kind=kind,
        security_id=security_id,
        quantity=quantity,
        cash=cash,
        known_at=known,
        ingested_at=known,
    )


def _reconciliation(
    broker_cash: float | None,
    known_at: datetime,
    *,
    status: str = "ok",
    window_id: int = WINDOW,
) -> ReconciliationRow:
    return ReconciliationRow(
        reconciliation_id=1,
        window_id=window_id,
        run_id=RUN,
        at=known_at,
        status=status,
        broker_cash=broker_cash,
        known_at=known_at,
        ingested_at=known_at,
    )


def _splits(*rows: tuple[str, date, float]) -> pl.DataFrame:
    """A frame shaped like `live_actions_as_of`, holding only split rows."""
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "action_type": ["split"] * len(rows),
            "ex_date": [r[1] for r in rows],
            "ratio_or_amount": [r[2] for r in rows],
        },
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
        },
    )


NO_ACTIONS = _splits()


def _ledger(
    fills: list[OrderedFill],
    orders: list[OrderRow],
    *,
    adjustments: tuple[AdjustmentRow, ...] = (),
    actions: pl.DataFrame = NO_ACTIONS,
    reconciliation: ReconciliationRow | None = None,
    starting_cash: float = 1000.0,
    through: date = D9,
    window_id: int = WINDOW,
) -> Ledger:
    return from_journal(
        fills,
        orders,
        adjustments,
        actions,
        reconciliation,
        starting_cash,
        through,
        window_id=window_id,
        quantity_tolerance=TOLERANCE,
    )


# One trading history used by several tests:
#   D1 buy A 10 @ 50   cash -500
#   D1 buy B  4 @ 25   cash -100
#   D5 sell A 3 @ 60   cash +180
BUY_A = _order(A, "buy", D1)
BUY_B = _order(B, "buy", D1)
SELL_A = _order(A, "sell", D5)
ORDERS = [BUY_A, BUY_B, SELL_A]
FILLS = [
    _fill(BUY_A, 10, 50.0, fill_id=1),
    _fill(BUY_B, 4, 25.0, fill_id=2),
    _fill(SELL_A, 3, 60.0, fill_id=3),
]


def test_positions_and_cash_across_buys_and_sells() -> None:
    ledger = _ledger(FILLS, ORDERS)
    # A: 10 - 3 = 7; B: 4. Cash: 1000 - 500 - 100 + 180 = 580.
    assert ledger.positions == {A: 7.0, B: 4.0}
    assert ledger.cash == pytest.approx(580.0)


def test_partial_fills_of_one_order_add_up() -> None:
    fills = [_fill(BUY_A, 6, 50.0, fill_id=1), _fill(BUY_A, 4, 51.0, fill_id=2)]
    ledger = _ledger(fills, [BUY_A])
    # 6 + 4 = 10 shares; cash 1000 - 300 - 204 = 496.
    assert ledger.positions == {A: 10.0}
    assert ledger.cash == pytest.approx(496.0)


def test_a_name_sold_out_leaves_the_positions() -> None:
    sell_all = _order(A, "sell", D5, attempt=2)
    fills = [_fill(BUY_A, 10, 50.0, fill_id=1), _fill(sell_all, 10, 55.0, fill_id=2)]
    ledger = _ledger(fills, [BUY_A, sell_all])
    assert ledger.positions == {}
    assert ledger.cash == pytest.approx(1000.0 - 500.0 + 550.0)


def test_an_empty_journal_is_starting_cash_and_no_positions() -> None:
    ledger = _ledger([], [], starting_cash=1234.5)
    assert ledger.positions == {}
    assert ledger.cash == 1234.5


# Splits: a pure frame first, then the real as-of read with a known_at cut.


def test_a_split_before_through_multiplies_the_earlier_quantity() -> None:
    # A 2:1 on D8 with through D9: 7 held before D8 becomes 14. B has no split.
    ledger = _ledger(FILLS, ORDERS, actions=_splits((A, D8, 2.0)))
    assert ledger.positions == {A: 14.0, B: 4.0}
    # Splits never move cash.
    assert ledger.cash == pytest.approx(580.0)


def test_a_split_after_through_is_not_applied() -> None:
    ledger = _ledger(FILLS, ORDERS, actions=_splits((A, D8, 2.0)), through=D7)
    assert ledger.positions == {A: 7.0, B: 4.0}


def test_a_fill_on_or_after_the_ex_date_is_already_post_split() -> None:
    buy_on_ex = _order(A, "buy", D8, attempt=2)
    fills = [*FILLS, _fill(buy_on_ex, 2, 30.0, fill_id=4)]
    ledger = _ledger(fills, [*ORDERS, buy_on_ex], actions=_splits((A, D8, 2.0)))
    # 7 before D8 doubles to 14; the 2 bought on D8 stay 2: 16.
    assert ledger.positions[A] == 16.0
    assert ledger.cash == pytest.approx(580.0 - 60.0)


def test_the_ex_date_is_read_in_new_york_time() -> None:
    # 2026-10-08 00:30 UTC is 2026-10-07 20:30 in New York: before the D8 ex-date.
    late = _order(A, "buy", D7, attempt=2)
    fills = [_fill(late, 1, 50.0, filled_at=_utc(D8, 0, 30), known_at=_utc(D8, 1))]
    ledger = _ledger(fills, [late], actions=_splits((A, D8, 2.0)))
    assert ledger.positions == {A: 2.0}


def test_consecutive_splits_compound() -> None:
    ledger = _ledger([_fill(BUY_A, 10, 50.0)], [BUY_A], actions=_splits((A, D5, 2.0), (A, D8, 3.0)))
    # 10 x 2 x 3 = 60.
    assert ledger.positions == {A: 60.0}


def test_a_reverse_split_divides() -> None:
    ledger = _ledger([_fill(BUY_A, 10, 50.0)], [BUY_A], actions=_splits((A, D8, 0.1)))
    assert ledger.positions[A] == pytest.approx(1.0)


def test_non_split_actions_are_ignored() -> None:
    actions = pl.DataFrame(
        {
            "security_id": [A],
            "action_type": ["dividend"],
            "ex_date": [D8],
            "ratio_or_amount": [0.5],
        }
    )
    ledger = _ledger(FILLS, ORDERS, actions=actions)
    assert ledger.positions == {A: 7.0, B: 4.0}
    assert ledger.cash == pytest.approx(580.0)


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _action(
    conn: duckdb.DuckDBPyConnection, security_id: str, ex_date: date, ratio: float, known: datetime
) -> None:
    values: dict[str, Any] = {
        "security_id": security_id,
        "action_type": "split",
        "ex_date": ex_date,
        "ratio_or_amount": ratio,
        "announced_at": known,
        "known_at": known,
        "ingested_at": known + timedelta(minutes=5),
        "source": "alpaca",
        "provenance": "action",
    }
    insert_row(conn, "corporate_actions", values)


def test_a_split_known_after_the_cut_is_not_applied(store: duckdb.DuckDBPyConnection) -> None:
    # S = D9, so the cut is close(D8) = 2026-10-08 20:00 UTC.
    # A 2:1 on D8 was known on D5 (before the cut); B 3:1 on D8 only at 21:00 on D8.
    _action(store, A, D8, 2.0, _utc(D5, 21))
    _action(store, B, D8, 3.0, _utc(D8, 21))
    before_cut = live_actions_as_of(store, session_close(D8))
    ledger = _ledger(FILLS, ORDERS, actions=before_cut)
    assert ledger.positions == {A: 14.0, B: 4.0}
    # The same journal read after B's split is known applies both.
    after = live_actions_as_of(store, _utc(D8, 22))
    assert _ledger(FILLS, ORDERS, actions=after).positions == {A: 14.0, B: 12.0}


# Adjustments (quantity and cash are signed deltas).


def test_each_adjustment_kind() -> None:
    adjustments = (
        # 1.5 shares of A carried into the window on D1, doubled by the D8 split: 3.
        _adjustment("carried_residue", D1, security_id=A, quantity=1.5),
        # 5 shares of spin-off child C received on D9.
        _adjustment("spinoff_receipt", D9, security_id=C, quantity=5.0),
        # B's listing ended: the 4 shares leave and the broker credits 90.
        _adjustment("corporate_action_cash", D9, security_id=B, quantity=-4.0, cash=90.0),
        # An unexpected dividend on A credited 1.2.
        _adjustment("dividend_cash", D9, security_id=A, cash=1.2),
    )
    ledger = _ledger(FILLS, ORDERS, adjustments=adjustments, actions=_splits((A, D8, 2.0)))
    # A: (7 + 1.5) x 2 = 17; B: 4 - 4 = 0 (dropped); C: 5.
    assert ledger.positions == {A: 17.0, C: 5.0}
    # Cash: 580 + 90 + 1.2.
    assert ledger.cash == pytest.approx(671.2)


def test_an_unknown_adjustment_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="kind"):
        _ledger([], [], adjustments=(_adjustment("gift", D1, security_id=A, quantity=1.0),))


def test_an_adjustment_quantity_without_a_security_is_refused() -> None:
    with pytest.raises(ValueError, match="security_id"):
        _ledger([], [], adjustments=(_adjustment("spinoff_receipt", D1, quantity=1.0),))


# Cash: the re-base at the last ok reconciliation.


def test_cash_rebases_on_the_last_ok_reconciliation() -> None:
    buy_later = _order(A, "buy", D8, attempt=2)
    fills = [
        *FILLS,  # known on D1 and D5, before the reconciliation
        _fill(buy_later, 2, 30.0, fill_id=4),  # known D8 21:00, after it
    ]
    adjustments = (
        _adjustment("dividend_cash", D5, security_id=A, cash=5.0, known_at=_utc(D5, 21)),
        _adjustment("dividend_cash", D9, security_id=A, cash=1.2),
    )
    reconciliation = _reconciliation(600.0, _utc(D5, 22))
    ledger = _ledger(
        fills, [*ORDERS, buy_later], adjustments=adjustments, reconciliation=reconciliation
    )
    # Base 600 (the broker's cash at D5 22:00, which already holds every earlier
    # fill and the D5 dividend), then -60 and +1.2: 541.2. starting_cash is unused.
    assert ledger.cash == pytest.approx(541.2)
    # Positions still come from every fill: A 7 + 2 = 9, B 4.
    assert ledger.positions == {A: 9.0, B: 4.0}


def test_a_row_known_at_the_reconciliation_instant_is_inside_it() -> None:
    at = _utc(D5, 21)
    ledger = _ledger(FILLS, ORDERS, reconciliation=_reconciliation(575.0, at))
    # The D5 sell is known at exactly 21:00: part of the base, not added again.
    assert ledger.cash == pytest.approx(575.0)


def test_before_the_first_reconciliation_cash_starts_from_starting_cash() -> None:
    ledger = _ledger(FILLS, ORDERS, reconciliation=None, starting_cash=2000.0)
    assert ledger.cash == pytest.approx(2000.0 - 500.0 - 100.0 + 180.0)


def test_a_reconciliation_that_is_not_ok_is_refused() -> None:
    with pytest.raises(ValueError, match="ok"):
        _ledger(
            FILLS, ORDERS, reconciliation=_reconciliation(600.0, _utc(D5, 22), status="mismatch")
        )


def test_an_ok_reconciliation_without_broker_cash_is_refused() -> None:
    with pytest.raises(ValueError, match="broker_cash"):
        _ledger(FILLS, ORDERS, reconciliation=_reconciliation(None, _utc(D5, 22)))


# Per window and tied to the orders.


def test_rows_of_another_window_are_refused() -> None:
    other = _fill(BUY_A, 1, 50.0, window_id=2)
    with pytest.raises(ValueError, match="window"):
        _ledger([*FILLS, other], ORDERS)
    with pytest.raises(ValueError, match="window"):
        _ledger(
            FILLS, ORDERS, adjustments=(_adjustment("dividend_cash", D9, cash=1.0, window_id=2),)
        )
    with pytest.raises(ValueError, match="window"):
        _ledger(FILLS, ORDERS, reconciliation=_reconciliation(1.0, _utc(D9, 21), window_id=2))


def test_a_fill_without_its_order_is_refused() -> None:
    with pytest.raises(ValueError, match="no orders row"):
        _ledger(FILLS, [BUY_A, BUY_B])


def test_a_fill_disagreeing_with_its_order_is_refused() -> None:
    wrong = OrderedFill(FILLS[0].fill, "sell", A, "A", RUN, WINDOW)
    with pytest.raises(ValueError, match="disagrees"):
        _ledger([wrong], ORDERS)


def test_through_must_be_a_date() -> None:
    with pytest.raises(ValueError, match="through"):
        from_journal(
            FILLS,
            ORDERS,
            (),
            NO_ACTIONS,
            None,
            1000.0,
            _utc(D9, 21),  # type: ignore[arg-type]
            window_id=WINDOW,
            quantity_tolerance=TOLERANCE,
        )


# A superseded fill is counted once, through the accessor.


def test_a_superseded_fill_counts_once_through_the_accessor(
    store: duckdb.DuckDBPyConnection,
) -> None:
    stamp = {"known_at": _utc(D1, 21), "ingested_at": _utc(D1, 21)}
    append(
        store,
        PaperWindowRow(
            hypothesis_id=1,
            first_rebalance_session=D1,
            account_id="PA1",
            starting_cash=1000.0,
            starting_equity=1000.0,
            code_version="abc",
            started_at=_utc(D1, 12),
            frozen_json="{}",
            frozen_sha256="0" * 64,
            **stamp,
        ),
    )
    append(
        store,
        PaperRunRow(
            window_id=WINDOW,
            session=D1,
            kind="rebalance",
            started_at=_utc(D1, 12),
            invoked_by="scheduler",
            code_version="abc",
            **stamp,
        ),
    )
    append(store, BUY_A)
    # The run first books the order's fill from its status (synthetic), then the
    # feed delivers the same trade, journaled as superseded by the status row.
    status = append(
        store,
        FillRow(
            client_order_id=BUY_A.client_order_id,
            filled_at=_utc(D1, 14),
            quantity=10,
            price=50.0,
            price_implied=True,
            broker_fill_id="synthetic:1",
            source="broker_status",
            **stamp,
        ),
    )
    append(
        store,
        FillRow(
            client_order_id=BUY_A.client_order_id,
            filled_at=_utc(D1, 14),
            quantity=10,
            price=50.0,
            price_implied=False,
            broker_fill_id="bf-1",
            source="broker_feed",
            superseded_by=status,
            **stamp,
        ),
    )
    fills = fills_for(store, window_id=WINDOW)
    ledger = _ledger(fills, [BUY_A])
    assert ledger.positions == {A: 10.0}
    assert ledger.cash == pytest.approx(500.0)


# Equity and weights.


def test_equity_and_weights_from_marks() -> None:
    ledger = _ledger(FILLS, ORDERS)
    marks = {A: 40.0, B: 30.0}
    # 580 + 7 x 40 + 4 x 30 = 580 + 280 + 120 = 980.
    assert ledger.equity(marks) == pytest.approx(980.0)
    weights = ledger.weights(marks)
    assert weights == pytest.approx({A: 280.0 / 980.0, B: 120.0 / 980.0})


def test_a_held_name_without_a_mark_is_refused() -> None:
    ledger = _ledger(FILLS, ORDERS)
    with pytest.raises(KeyError, match=B):
        ledger.equity({A: 40.0})


def test_marks_for_names_not_held_are_ignored() -> None:
    ledger = _ledger(FILLS, ORDERS)
    assert ledger.equity({A: 40.0, B: 30.0, C: 1.0}) == pytest.approx(980.0)


def test_weights_refuse_non_positive_equity() -> None:
    ledger = _ledger([], [], starting_cash=0.0)
    with pytest.raises(ValueError, match="equity"):
        ledger.weights({})


# Review fixes: window id, rows after `through`, dust, shorts, bad numbers, repeats.


def test_a_reconciliation_of_another_window_is_refused_with_no_rows() -> None:
    with pytest.raises(ValueError, match="window"):
        _ledger([], [], reconciliation=_reconciliation(900.0, _utc(D5, 22), window_id=2))


def test_every_row_must_be_of_the_named_window() -> None:
    with pytest.raises(ValueError, match="window"):
        _ledger(FILLS, ORDERS, window_id=2)


def test_rows_dated_after_through_are_left_out() -> None:
    buy_d9 = _order(A, "buy", D9, attempt=2)
    fills = [*FILLS, _fill(buy_d9, 5, 40.0, fill_id=4)]
    adjustments = (_adjustment("spinoff_receipt", D9, security_id=C, quantity=5.0, cash=3.0),)
    ledger = _ledger(fills, [*ORDERS, buy_d9], adjustments=adjustments, through=D8)
    # As on D8: the D9 fill and the D9 receipt are not in positions or cash.
    assert ledger.positions == {A: 7.0, B: 4.0}
    assert ledger.cash == pytest.approx(580.0)
    # Stated for D9 they are.
    later = _ledger(fills, [*ORDERS, buy_d9], adjustments=adjustments, through=D9)
    assert later.positions == {A: 12.0, B: 4.0, C: 5.0}
    assert later.cash == pytest.approx(580.0 - 200.0 + 3.0)


def test_a_cash_base_after_through_is_refused() -> None:
    with pytest.raises(ValueError, match="after"):
        _ledger(FILLS, ORDERS, reconciliation=_reconciliation(600.0, _utc(D9, 13)), through=D8)


def test_dust_within_the_tolerance_is_dropped() -> None:
    sell = _order(A, "sell", D5)
    fills = [
        _fill(BUY_A, 0.1, 10.0, fill_id=1),
        _fill(BUY_A, 0.2, 10.0, fill_id=2),
        _fill(sell, 0.3, 10.0, fill_id=3),
    ]
    assert 0.1 + 0.2 - 0.3 != 0  # the float residue this case is about
    ledger = _ledger(fills, [BUY_A, sell])
    assert ledger.positions == {}
    assert ledger.equity({}) == pytest.approx(1000.0)


def test_a_negative_position_is_kept_but_refused_by_equity_and_weights() -> None:
    sell = _order(A, "sell", D5)
    fills = [_fill(BUY_A, 2, 50.0, fill_id=1), _fill(sell, 3, 50.0, fill_id=2)]
    ledger = _ledger(fills, [BUY_A, sell])
    assert ledger.positions == {A: -1.0}
    assert ledger.short_names == (A,)
    with pytest.raises(ValueError, match="negative"):
        ledger.equity({A: 50.0})
    with pytest.raises(ValueError, match="negative"):
        ledger.weights({A: 50.0})


@pytest.mark.parametrize("mark", [math.nan, math.inf, 0.0, -1.0])
def test_a_bad_mark_is_refused(mark: float) -> None:
    ledger = _ledger(FILLS, ORDERS)
    with pytest.raises(ValueError, match="mark"):
        ledger.equity({A: mark, B: 30.0})
    with pytest.raises(ValueError, match="mark"):
        ledger.weights({A: mark, B: 30.0})


def test_non_finite_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="starting_cash"):
        _ledger([], [], starting_cash=math.nan)
    with pytest.raises(ValueError, match="split ratio"):
        _ledger(FILLS, ORDERS, actions=_splits((A, D8, math.nan)))
    with pytest.raises(ValueError, match="broker_cash"):
        _ledger(FILLS, ORDERS, reconciliation=_reconciliation(math.inf, _utc(D5, 22)))
    with pytest.raises(ValueError, match="price"):
        _ledger([_fill(BUY_A, 1, math.nan)], [BUY_A])
    with pytest.raises(ValueError, match="quantity"):
        _ledger(
            [],
            [],
            adjustments=(_adjustment("carried_residue", D1, security_id=A, quantity=math.inf),),
        )
    with pytest.raises(ValueError, match="cash"):
        _ledger([], [], adjustments=(_adjustment("dividend_cash", D1, cash=math.nan),))


def test_a_repeated_fill_or_adjustment_is_refused() -> None:
    with pytest.raises(ValueError, match="repeated"):
        _ledger([*FILLS, FILLS[0]], ORDERS)
    twice = _adjustment("dividend_cash", D1, cash=1.0)
    with pytest.raises(ValueError, match="repeated"):
        _ledger([], [], adjustments=(twice, twice))


def test_a_negative_tolerance_is_refused() -> None:
    with pytest.raises(ValueError, match="quantity_tolerance"):
        from_journal(
            [], [], (), NO_ACTIONS, None, 1.0, D9, window_id=WINDOW, quantity_tolerance=-1.0
        )


@pytest.mark.parametrize(
    ("quantity", "price"), [(0.0, 50.0), (-1.0, 50.0), (1.0, 0.0), (1.0, -5.0)]
)
def test_a_fill_breaking_the_fills_checks_is_refused(quantity: float, price: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        _ledger([_fill(BUY_A, quantity, price)], [BUY_A])


def test_an_implied_residual_price_needs_only_to_be_finite() -> None:
    feed = _fill(BUY_A, 9, 50.0, fill_id=1)
    residual = FillRow(
        fill_id=2,
        client_order_id=BUY_A.client_order_id,
        filled_at=feed.fill.filled_at,
        quantity=1.0,
        price=0.0,
        price_implied=True,
        broker_fill_id=f"synthetic:{BUY_A.client_order_id}",
        source="broker_status",
        known_at=feed.fill.known_at,
        ingested_at=feed.fill.ingested_at,
    )
    implied = OrderedFill(residual, "buy", A, "A", RUN, WINDOW)
    ledger = _ledger([feed, implied], [BUY_A])
    # 9 + 1 shares; cash moves only by the feed fill's 450.
    assert ledger.positions == {A: 10.0}
    assert ledger.cash == pytest.approx(550.0)


# #1296: fills are summed exactly, so a full exit on a 9-decimal grid leaves
# no nano-share behind. Summed as floats, 0.057436865 + 1 - 0.5 is
# 0.5574368649999999, and `round_down(·, 9)` sold one step less than held.
NINE = 9  # the quantity grid set explicitly, not the config default (#1294)


def _exact_ledger(fills: list[OrderedFill], orders: list[OrderRow]) -> Ledger:
    """A ledger with no dust tolerance at all, so any residue stays visible."""
    return from_journal(
        fills, orders, (), NO_ACTIONS, None, 1000.0, D9, window_id=WINDOW, quantity_tolerance=0.0
    )


def test_the_recorded_ko_fills_exit_fully_on_the_nine_decimal_grid() -> None:
    buy_1, buy_2 = _order(A, "buy", D1), _order(A, "buy", D5)
    sell = _order(A, "sell", D7)
    fills = [
        _fill(buy_1, 0.057436865, 60.0, fill_id=1),
        _fill(buy_2, 1, 60.0, fill_id=2),
        _fill(sell, 0.5, 60.0, fill_id=3),
    ]
    assert round_down(0.057436865 + 1 - 0.5, NINE) == 0.557436864  # the float bug
    held = _exact_ledger(fills, [buy_1, buy_2, sell]).positions[A]
    assert held == 0.557436865
    exit_quantity = round_down(held, NINE)
    assert exit_quantity == 0.557436865
    exit_order = _order(A, "sell", D8)
    after = _exact_ledger(
        [*fills, _fill(exit_order, exit_quantity, 60.0, fill_id=4)],
        [buy_1, buy_2, sell, exit_order],
    )
    assert after.positions == {}


def test_random_nine_decimal_fills_exit_exactly_and_never_oversell() -> None:
    # Quantities stay under 1e4 shares, so each 9-decimal sum has at most 13
    # significant digits and round-trips through `float` exactly (under ~4.5e6
    # shares it would still).
    rng = random.Random(1296)
    step = Decimal(1).scaleb(-NINE)
    for _ in range(500):
        count = rng.randint(2, 5)
        quantities = [Decimal(rng.randint(1, 10**13)) * step for _ in range(count)]
        orders = [_order(A, "buy", D1, attempt=i + 1) for i in range(count)]
        fills = [
            _fill(order, float(q), 10.0, fill_id=i + 1)
            for i, (order, q) in enumerate(zip(orders, quantities, strict=True))
        ]
        sell = _order(A, "sell", D5)
        sold = Decimal(rng.randint(0, int(sum(quantities) / step) - 1)) * step
        if sold:
            fills.append(_fill(sell, float(sold), 10.0, fill_id=count + 1))
            orders.append(sell)
        truth = sum(quantities) - sold
        exit_quantity = round_down(_exact_ledger(fills, orders).positions[A], NINE)
        assert Decimal(repr(exit_quantity)) == truth  # the whole holding, on the grid
        exit_order = _order(A, "sell", D7)
        after = _exact_ledger(
            [*fills, _fill(exit_order, exit_quantity, 10.0, fill_id=count + 2)],
            [*orders, exit_order],
        )
        assert after.positions == {}


@pytest.mark.parametrize(
    ("bought", "ratio", "after_split"),
    [(9, 1 / 3, 3), (3, 2 / 3, 2), (0.7, 3.0, 2.1), (0.3, 1.5, 0.45)],
)
def test_a_split_holding_exits_fully(bought: float, ratio: float, after_split: float) -> None:
    sell = _order(A, "sell", D9)
    fills = [_fill(BUY_A, bought, 50.0, fill_id=1)]
    actions = _splits((A, D8, ratio))
    held = from_journal(
        fills, [BUY_A], (), actions, None, 1000.0, D8, window_id=WINDOW, quantity_tolerance=0.0
    ).positions[A]
    exit_quantity = round_down(held, NINE)
    assert exit_quantity == after_split
    after = from_journal(
        [*fills, _fill(sell, exit_quantity, 50.0, fill_id=2)],
        [BUY_A, sell],
        (),
        actions,
        None,
        1000.0,
        D9,
        window_id=WINDOW,
        quantity_tolerance=0.0,
    )
    assert after.positions == {}


def test_a_tiny_split_ratio_is_not_rounded_to_zero() -> None:
    held = _ledger([_fill(BUY_A, 10**8, 50.0)], [BUY_A], actions=_splits((A, D8, 1e-7))).positions
    assert held == {A: pytest.approx(10.0)}

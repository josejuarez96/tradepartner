"""The reserve for our own open buys (ADR 0010 amendment 2026-10-01; plan T54d).

Hand-computed cases: what an open buy reserves, what reserves nothing, the
split adjustment, rounding up to the cent, and the no-look-ahead guards (a
split known after close(S-1) is not applied; an order dated after S raises).
The literal check on `execution/reserve.py` is T60c's (`tests/test_no_literals.py`).
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.reserve import open_buy_reserve
from tradepartner.store.journal import FillRow, OrderedFill, OrderEventRow, OrderRow

S1 = date(2026, 10, 2)
S2 = date(2026, 10, 5)
S = date(2026, 10, 6)  # the run's session
A = "SEC_A"
B = "SEC_B"
FROZEN = RiskConfig()  # whole_share_price_buffer 0.02
_IDS = itertools.count(1)


def price_of(security_id: str) -> float:
    return {A: 50.0, B: 20.0}[security_id]


def _utc(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _order(
    session: date,
    *,
    side: str = "buy",
    notional: float | None = None,
    quantity: float | None = None,
    security_id: str = A,
    attempt: int = 1,
) -> OrderRow:
    stamp = _utc(session, 13)
    return OrderRow(
        client_order_id=client_order_id("tp", "main", session, security_id, side, attempt),
        decision_id=next(_IDS),
        run_id=1,
        session=session,
        attempt=attempt,
        phase=side,
        security_id=security_id,
        symbol=security_id,
        side=side,
        notional=notional,
        quantity=quantity,
        sells_in_flight_at_submit=False,
        known_at=stamp,
        ingested_at=stamp,
    )


def _event(order: OrderRow, status: str) -> OrderEventRow:
    stamp = _utc(order.session, 14)
    return OrderEventRow(
        client_order_id=order.client_order_id, status=status, known_at=stamp, ingested_at=stamp
    )


def _fill(order: OrderRow, quantity: float, price: float) -> OrderedFill:
    stamp = _utc(order.session, 15)
    fill_id = next(_IDS)
    row = FillRow(
        fill_id=fill_id,
        client_order_id=order.client_order_id,
        filled_at=_utc(order.session, 14),
        quantity=quantity,
        price=price,
        price_implied=False,
        broker_fill_id=f"bf-{fill_id}",
        source="broker_feed",
        known_at=stamp,
        ingested_at=stamp,
    )
    return OrderedFill(row, order.side, order.security_id, order.symbol, order.run_id, 1)


def _splits(*rows: tuple[str, date, float, datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "action_type": ["split"] * len(rows),
            "ex_date": [r[1] for r in rows],
            "ratio_or_amount": [r[2] for r in rows],
            "known_at": [r[3] for r in rows],
        },
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
            "known_at": pl.Datetime(time_zone="UTC"),
        },
    )


NO_ACTIONS = _splits()
CUTOFF = session_close(previous_session(S))  # close(S-1)


def _reserve(
    orders: list[OrderRow],
    events: list[OrderEventRow] | None = None,
    fills: list[OrderedFill] | None = None,
    actions: pl.DataFrame = NO_ACTIONS,
) -> Decimal:
    return open_buy_reserve(orders, events or [], fills or [], actions, price_of, FROZEN, session=S)


def test_a_partly_filled_notional_buy_reserves_its_unfilled_notional() -> None:
    order = _order(S1, notional=1000.0)
    fills = [_fill(order, 8.0, 50.0)]  # $400 filled
    assert _reserve([order], [_event(order, "accepted")], fills) == Decimal("600.00")


def test_a_pending_buy_with_no_event_reserves_all_of_it() -> None:
    # journaled before its submit, never acknowledged: still ours and open
    assert _reserve([_order(S, notional=250.0)]) == Decimal("250.00")


def test_a_quantity_buy_reserves_its_unfilled_shares_at_the_buffered_price() -> None:
    order = _order(S1, quantity=10.0)
    fills = [_fill(order, 4.0, 49.0)]
    # 6 shares x $50 x 1.02 = $306
    assert _reserve([order], [_event(order, "accepted")], fills) == Decimal("306.00")


@pytest.mark.parametrize("status", ["filled", "expired", "rejected", "cancelled"])
def test_a_terminal_buy_reserves_nothing(status: str) -> None:
    order = _order(S1, notional=1000.0)
    assert _reserve([order], [_event(order, "accepted"), _event(order, status)]) == Decimal("0")


def test_every_sell_reserves_nothing() -> None:
    sells = [_order(S1, side="sell", quantity=5.0), _order(S, side="sell", notional=300.0)]
    assert _reserve(sells) == Decimal("0")


def test_buys_from_two_sessions_sum() -> None:
    earlier = _order(S1, notional=100.0, security_id=B)
    today = _order(S, quantity=2.0)  # 2 x 50 x 1.02 = 102
    assert _reserve([earlier, today]) == Decimal("202.00")


def test_a_split_after_the_order_adjusts_its_unfilled_quantity() -> None:
    order = _order(S1, quantity=10.0)
    fills = [_fill(order, 4.0, 100.0)]
    # a 2:1 split, ex-date S2 in (S1, S], known by close(S-1): 6 shares become 12
    actions = _splits((A, S2, 2.0, _utc(S1, 22)))
    # 12 x $50 (post-split price) x 1.02 = $612
    assert _reserve([order], [_event(order, "accepted")], fills, actions) == Decimal("612.00")


def test_a_split_on_or_before_the_order_session_is_not_applied_again() -> None:
    order = _order(S2, quantity=10.0)
    actions = _splits((A, S2, 2.0, _utc(S1, 22)))
    assert _reserve([order], actions=actions) == Decimal("510.00")


def test_a_split_known_at_close_s_minus_1_applies_and_a_later_one_raises() -> None:
    order = _order(S1, quantity=10.0)
    on_time = _splits((A, S, 2.0, CUTOFF))
    assert _reserve([order], actions=on_time) == Decimal("1020.00")
    # a frame read after close(S-1): price_of would be post-split while the
    # shares stayed pre-split, an under-reserve, so it raises instead of skipping
    late = _splits((A, S, 2.0, CUTOFF + timedelta(minutes=1)))
    with pytest.raises(ValueError, match="after close"):
        _reserve([order], actions=late)
    other_name = _splits((B, S, 2.0, CUTOFF + timedelta(minutes=1)))
    with pytest.raises(ValueError, match="after close"):
        _reserve([order], actions=other_name)


def test_a_split_after_s_or_of_another_name_is_not_applied() -> None:
    order = _order(S1, quantity=10.0)
    after_s = _splits((A, S + timedelta(days=1), 2.0, _utc(S1, 22)))
    other = _splits((B, S2, 2.0, _utc(S1, 22)))
    assert _reserve([order], actions=after_s) == Decimal("510.00")
    assert _reserve([order], actions=other) == Decimal("510.00")


def test_an_order_dated_after_the_session_raises() -> None:
    with pytest.raises(ValueError, match="after the session"):
        _reserve([_order(S + timedelta(days=1), notional=10.0)])


def test_fractions_of_a_cent_round_up() -> None:
    order = _order(S1, notional=10.0)
    fills = [_fill(order, 0.1, 33.333)]  # 3.3333 filled, 6.6667 unfilled
    assert _reserve([order], fills=fills) == Decimal("6.67")
    tiny = _order(S1, notional=10.0, security_id=B)
    fills = [_fill(tiny, 1.0, 9.999)]  # 0.001 unfilled rounds up to a cent
    assert _reserve([tiny], fills=fills) == Decimal("0.01")


def test_the_reserve_drops_to_zero_once_the_order_is_terminal() -> None:
    order = _order(S1, notional=500.0)
    accepted = [_event(order, "accepted")]
    assert _reserve([order], accepted) == Decimal("500.00")
    assert _reserve([order], [*accepted, _event(order, "expired")]) == Decimal("0")


def test_a_fill_beyond_the_order_reserves_nothing_never_negative() -> None:
    order = _order(S1, notional=100.0)
    assert _reserve([order], fills=[_fill(order, 3.0, 50.0)]) == Decimal("0")


def test_an_order_with_neither_quantity_nor_notional_raises() -> None:
    with pytest.raises(ValueError, match="neither quantity nor notional"):
        _reserve([_order(S1)])


def test_actions_without_known_at_raise() -> None:
    bare = NO_ACTIONS.drop("known_at")
    with pytest.raises(ValueError, match="known_at"):
        _reserve([_order(S1, quantity=1.0)], actions=bare)


def test_the_reserve_is_a_decimal_with_cents() -> None:
    result = _reserve([_order(S1, notional=12.5)])
    assert isinstance(result, Decimal)
    assert result == Decimal("12.50")


def test_a_fill_of_another_side_or_name_raises() -> None:
    order = _order(S1, notional=100.0)
    stray = _fill(order, 1.0, 50.0)
    wrong = OrderedFill(stray.fill, "sell", order.security_id, order.symbol, 1, 1)
    with pytest.raises(ValueError, match="disagrees"):
        _reserve([order], fills=[wrong])


@pytest.mark.parametrize(
    "bad",
    [
        {"notional": -1.0},
        {"notional": float("nan")},
        {"quantity": float("inf")},
    ],
)
def test_a_bad_order_amount_raises(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="finite"):
        _reserve([_order(S1, **bad)])


def test_a_bad_price_or_fill_raises() -> None:
    order = _order(S1, quantity=1.0, security_id=B)
    with pytest.raises(ValueError, match="positive"):
        open_buy_reserve([order], [], [], NO_ACTIONS, lambda _: 0.0, FROZEN, session=S)
    with pytest.raises(ValueError, match="finite"):
        _reserve([order], fills=[_fill(order, 1.0, float("nan"))])


def test_a_split_with_a_bad_ratio_raises() -> None:
    order = _order(S1, quantity=1.0)
    with pytest.raises(ValueError, match="positive"):
        _reserve([order], actions=_splits((A, S2, 0.0, _utc(S1, 22))))


def test_an_actions_row_with_no_known_at_or_ex_date_raises() -> None:
    order = _order(S1, quantity=1.0)
    for column in ("known_at", "ex_date"):
        frame = _splits((A, S2, 2.0, _utc(S1, 22))).with_columns(pl.lit(None).alias(column))
        with pytest.raises(ValueError, match=f"no {column}"):
            _reserve([order], actions=frame)


def test_a_datetime_session_raises() -> None:
    with pytest.raises(ValueError, match="must be a date"):
        open_buy_reserve([], [], [], NO_ACTIONS, price_of, FROZEN, session=_utc(S, 12))  # type: ignore[arg-type]

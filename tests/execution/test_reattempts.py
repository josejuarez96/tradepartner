"""Re-attempt scope and end-of-phase write-offs (Phase 4 plan T60d; spec
Definitions > Decision and req 3 "Re-runs and catch-ups").

Hand-built journal rows; each decision's state comes from the real
`plan.decision_state`, as the wrapper will compute it. The broker-facing
cases are T60e's.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.config import RiskConfig
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.plan import DecisionState, Remainder, State, decision_state
from tradepartner.execution.reattempts import (
    Attempt,
    WrittenOff,
    attempt_scope,
    write_offs,
)
from tradepartner.store.journal import (
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

T0 = date(2026, 10, 1)  # rebalance session
S1 = date(2026, 10, 2)  # its fill session
S2 = date(2026, 10, 5)  # a catch-up session
PRICE = 50.0
FROZEN = RiskConfig()  # min_order_notional 1.0
NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
    }
)
_IDS = itertools.count(1)


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _price(_security_id: str) -> float:
    return PRICE


def _decision(
    decision_id: int,
    side: str | None,
    *,
    decision: str = "trade",
    reason: str | None = None,
    target: float | None = None,
    planned_notional: float | None = None,
    planned_quantity: float | None = None,
    rebalance_session: date | None = T0,
) -> DecisionRow:
    stamp = _utc(T0, 21)
    return DecisionRow(
        decision_id=decision_id,
        run_id=1,
        rebalance_session=rebalance_session,
        security_id=f"SEC_{decision_id}",
        side=side,
        planned_notional=planned_notional if side != "buy" else target,
        planned_quantity=planned_quantity,
        target_notional=target,
        whole_share=False,
        decision=decision,
        reason=reason,
        known_at=stamp,
        ingested_at=stamp,
    )


def _order(
    decision: DecisionRow,
    *,
    notional: float | None = None,
    quantity: float | None = None,
    sells_in_flight: bool = False,
    session: date = S1,
    attempt: int = 1,
) -> OrderRow:
    assert decision.side is not None and decision.decision_id is not None
    stamp = _utc(session, 13)
    return OrderRow(
        client_order_id=client_order_id(
            "tp", session, decision.security_id, decision.side, attempt
        ),
        decision_id=decision.decision_id,
        run_id=1,
        session=session,
        attempt=attempt,
        phase=decision.side,
        security_id=decision.security_id,
        symbol=decision.security_id,
        side=decision.side,
        notional=notional,
        quantity=quantity,
        sells_in_flight_at_submit=sells_in_flight,
        known_at=stamp,
        ingested_at=stamp,
    )


def _events(order: OrderRow, *chain: str | tuple[str, str]) -> list[OrderEventRow]:
    """The order's events in order after `pending` and `accepted`; a tuple is
    (status, reason)."""
    rows = []
    for minute, link in enumerate(("pending", "accepted", *chain)):
        status, reason = link if isinstance(link, tuple) else (link, None)
        stamp = _utc(order.session, 14, minute)
        rows.append(
            OrderEventRow(
                client_order_id=order.client_order_id,
                status=status,
                reason=reason,
                known_at=stamp,
                ingested_at=stamp,
            )
        )
    return rows


def _fill(order: OrderRow, quantity: float, price: float = PRICE) -> OrderedFill:
    fill_id = next(_IDS)
    stamp = _utc(order.session, 15)
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


def _states(
    decisions: Sequence[DecisionRow],
    orders: Sequence[OrderRow] = (),
    events: Sequence[OrderEventRow] = (),
    fills: Sequence[OrderedFill] = (),
    decision_events: Sequence[DecisionEventRow] = (),
    *,
    session: date = S1,
) -> dict[int, DecisionState]:
    states = {}
    for d in decisions:
        assert d.decision_id is not None
        states[d.decision_id] = decision_state(
            d,
            decision_events,
            orders,
            events,
            fills,
            NO_ACTIONS,
            _price,
            FROZEN,
            session=session,
        )
    return states


def _ids(attempts: Sequence[Attempt]) -> list[int | None]:
    return [a.decision.decision_id for a in attempts]


def test_an_open_sell_leaves_buys_open_for_re_attempt_against_the_remaining_buys() -> None:
    sell = _decision(1, "sell", planned_quantity=10.0, reason="left_targets")
    sell_order = _order(sell, quantity=10.0)
    bought = _decision(2, "buy", target=1000.0)
    buy_order = _order(bought, notional=400.0, sells_in_flight=True)
    deferred = _decision(3, "buy", target=500.0)
    decisions = [sell, bought, deferred]
    orders = [sell_order, buy_order]
    events = _events(sell_order) + _events(buy_order, "expired")
    states = _states(decisions, orders, events, [_fill(buy_order, 8.0)])

    scope = attempt_scope(decisions, states, phase="buy")

    # Only the remaining buys are in scope, each for its remainder, so
    # `risk.size_buys` rescales the deferred one against them alone.
    assert _ids(scope.attempts) == [2, 3]
    assert [a.remainder.notional for a in scope.attempts] == [600.0, 500.0]
    assert scope.in_flight == () and not scope.last
    assert write_offs(decisions, states, phase="buy", last=scope.last, halted=False) == []


def test_sells_filled_below_the_reference_price_make_a_last_phase_that_writes_off_the_rest() -> (
    None
):
    # Sells filled 0.5% below the reference price, so the buys cannot all be funded.
    sell = _decision(1, "sell", planned_quantity=20.0, reason="left_targets")
    sell_order = _order(sell, quantity=20.0)
    sell_fill = _fill(sell_order, 20.0, PRICE * (1 - 0.005))
    sell_events = _events(sell_order, "filled")
    short = _decision(2, "buy", target=600.0)
    deferred = _decision(3, "buy", target=400.0)
    decisions = [sell, short, deferred]
    start = _states(decisions, [sell_order], sell_events, [sell_fill])

    scope = attempt_scope(decisions, start, phase="buy")
    assert scope.last and _ids(scope.attempts) == [2, 3]

    # The phase sizes buy 2 to the cash and defers buy 3; buy 2 fills short and
    # expires. Its row says no sell was in flight, because the phase was last.
    buy_order = _order(short, notional=595.0, sells_in_flight=not scope.last)
    orders = [sell_order, buy_order]
    events = sell_events + _events(buy_order, "expired")
    fills = [sell_fill, _fill(buy_order, 11.0)]
    end = _states(decisions, orders, events, fills)

    assert buy_order.sells_in_flight_at_submit is False
    assert write_offs(decisions, end, phase="buy", last=scope.last, halted=False) == [
        WrittenOff(2, 600.0 - 11.0 * PRICE),
        WrittenOff(3, 400.0),
    ]
    # No later attempt: both buys are closed on the next session.
    stamp = _utc(S1, 22)
    rows = [
        DecisionEventRow(
            decision_id=w.decision_id,
            run_id=1,
            status="written_off",
            reason="unfunded",
            unfunded_notional=w.unfunded_notional,
            known_at=stamp,
            ingested_at=stamp,
        )
        for w in write_offs(decisions, end, phase="buy", last=True, halted=False)
    ]
    later = _states(decisions, orders, events, fills, rows, session=S2)
    assert attempt_scope(decisions, later, phase="buy").attempts == ()


def test_a_buy_submitted_while_a_sell_was_in_flight_then_expired_is_re_attempted() -> None:
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0, sells_in_flight=True)
    events = _events(order, "expired")
    states = _states([buy], [order], events, session=S2)

    (attempt,) = attempt_scope([buy], states, phase="buy").attempts
    assert attempt.remainder == Remainder(quantity=1000.0 / PRICE, notional=1000.0)


def test_a_buy_that_expired_with_no_sell_in_flight_is_written_off_not_re_attempted() -> None:
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0, sells_in_flight=False)
    events = _events(order, "expired")
    states = _states([buy], [order], events, session=S2)

    assert attempt_scope([buy], states, phase="buy").attempts == ()
    assert write_offs([buy], states, phase="buy", last=False, halted=False) == [
        WrittenOff(1, 1000.0)
    ]


def test_a_buy_partly_filled_then_cancelled_by_a_halt_is_re_attempted_never_written_off() -> None:
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0, sells_in_flight=False)
    events = _events(order, ("cancel_requested", "halt"), "cancelled")
    states = _states([buy], [order], events, [_fill(order, 4.0)], session=S2)

    (attempt,) = attempt_scope([buy], states, phase="buy").attempts
    assert attempt.remainder.notional == 1000.0 - 4.0 * PRICE
    # The halted phase that cancelled it writes it off neither at its end ...
    assert write_offs([buy], states, phase="buy", last=True, halted=True) == []
    # ... nor at collection, whatever `sells_in_flight_at_submit` says.
    assert write_offs([buy], states, phase="buy", last=False, halted=False) == []


def test_a_buy_the_broker_never_received_is_open_and_never_written_off_at_collection() -> None:
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0)
    events = [e for e in _events(order, ("cancelled", "not_received")) if e.status != "accepted"]
    states = _states([buy], [order], events, session=S2)

    assert states[1].state == State.OPEN
    assert _ids(attempt_scope([buy], states, phase="buy").attempts) == [1]
    assert write_offs([buy], states, phase="buy", last=False, halted=False) == []


def test_a_halted_buy_retried_and_deferred_by_a_last_phase_is_written_off() -> None:
    # After the resume the retry is sized from cash like any buy; a last phase
    # that cannot fund it defers it, and a deferral there is a write-off.
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0)
    events = _events(order, ("cancel_requested", "halt"), "cancelled")
    states = _states([buy], [order], events, [_fill(order, 4.0)], session=S2)

    scope = attempt_scope([buy], states, phase="buy")
    assert scope.last
    assert write_offs([buy], states, phase="buy", last=scope.last, halted=False) == [
        WrittenOff(1, 1000.0 - 4.0 * PRICE)
    ]


def test_a_notional_trim_partly_filled_then_expired_is_re_attempted_for_the_rest() -> None:
    trim = _decision(1, "sell", planned_notional=1000.0)
    order = _order(trim, quantity=1000.0 / PRICE)
    events = _events(order, "expired")
    states = _states([trim], [order], events, [_fill(order, 8.0)], session=S2)

    (attempt,) = attempt_scope([trim], states, phase="sell").attempts
    assert attempt.remainder == Remainder(quantity=600.0 / PRICE, notional=600.0)


def test_a_full_exit_partly_filled_then_cancelled_is_re_attempted_for_the_remaining_shares() -> (
    None
):
    exit_ = _decision(1, "sell", planned_quantity=30.0, reason="left_targets")
    order = _order(exit_, quantity=30.0)
    events = _events(order, "cancelled")
    states = _states([exit_], [order], events, [_fill(order, 10.0)], session=S2)

    (attempt,) = attempt_scope([exit_], states, phase="sell").attempts
    assert attempt.remainder.quantity == 20.0


def test_a_buy_scaled_by_the_cost_reserve_is_settled_once_filled() -> None:
    # The target already holds the cost reserve (T52), so filling it settles the buy.
    buy = _decision(1, "buy", target=995.0)
    order = _order(buy, notional=995.0)
    events = _events(order, "filled")
    states = _states([buy], [order], events, [_fill(order, 995.0 / PRICE)])

    scope = attempt_scope([buy], states, phase="buy")
    assert states[1].state == State.SETTLED
    assert scope.attempts == () and scope.in_flight == ()
    assert write_offs([buy], states, phase="buy", last=True, halted=False) == []


def test_closed_decisions_are_never_in_scope() -> None:
    skipped = _decision(1, "buy", decision="skip_below_minimum", target=0.5)
    keep = _decision(2, None, decision="override")
    written = _decision(3, "buy", target=500.0)
    dust = _decision(4, "sell", decision="dust", planned_quantity=0.01, reason="left_targets")
    stamp = _utc(S1, 22)
    event = DecisionEventRow(
        decision_id=3, run_id=1, status="written_off", known_at=stamp, ingested_at=stamp
    )
    decisions = [skipped, keep, written, dust]
    states = _states(decisions, decision_events=[event])

    for phase in ("sell", "buy"):
        scope = attempt_scope(decisions, states, phase=phase)
        assert scope.attempts == () and scope.in_flight == ()
    assert write_offs(decisions, states, phase="buy", last=True, halted=False) == []


def test_a_decision_with_a_live_order_is_left_to_collection() -> None:
    buy = _decision(1, "buy", target=1000.0)
    order = _order(buy, notional=1000.0)
    states = _states([buy], [order], _events(order))

    scope = attempt_scope([buy], states, phase="buy")
    assert scope.attempts == () and scope.in_flight == (1,)
    assert write_offs([buy], states, phase="buy", last=True, halted=False) == []


def test_a_halted_last_phase_writes_off_only_the_derived_write_offs() -> None:
    short = _decision(1, "buy", target=600.0)
    order = _order(short, notional=600.0, sells_in_flight=False)
    events = _events(order, "expired")
    unreached = _decision(2, "buy", target=400.0)
    decisions = [short, unreached]
    states = _states(decisions, [order], events, [_fill(order, 2.0)])

    assert write_offs(decisions, states, phase="buy", last=True, halted=True) == [
        WrittenOff(1, 600.0 - 2.0 * PRICE)
    ]


def test_forced_exits_join_the_sells_phase_and_never_decide_the_last_phase() -> None:
    forced = _decision(
        1,
        "sell",
        decision="forced_exit",
        reason="delisted",
        planned_quantity=5.0,
        rebalance_session=None,
    )
    buy = _decision(2, "buy", target=100.0)
    states = _states([forced, buy])

    assert _ids(attempt_scope([forced, buy], states, phase="sell").attempts) == [1]
    assert attempt_scope([forced, buy], states, phase="buy").last


def test_a_sells_phase_is_never_last_and_writes_nothing_off() -> None:
    buy = _decision(1, "buy", target=100.0)
    states = _states([buy])
    assert not attempt_scope([buy], states, phase="sell").last
    assert write_offs([buy], states, phase="sell", last=True, halted=False) == []


def test_missing_states_unknown_phases_and_two_rebalances_raise() -> None:
    buy = _decision(1, "buy", target=100.0)
    with pytest.raises(ValueError, match="no derived state for decision 1"):
        attempt_scope([buy], {}, phase="buy")
    with pytest.raises(ValueError, match="no derived state for decision 1"):
        write_offs([buy], {}, phase="buy", last=True, halted=False)
    with pytest.raises(ValueError, match="phase must be one of"):
        attempt_scope([buy], _states([buy]), phase="buys")
    with pytest.raises(ValueError, match="phase must be one of"):
        write_offs([buy], _states([buy]), phase="exit", last=True, halted=False)
    other = _decision(2, "buy", target=100.0, rebalance_session=S2)
    with pytest.raises(ValueError, match="more than one rebalance"):
        attempt_scope([buy, other], _states([buy, other]), phase="buy")
    # A closed buy of another rebalance is not in play and raises nothing.
    old = _decision(3, "buy", decision="skip_below_minimum", target=0.5, rebalance_session=S2)
    assert _ids(attempt_scope([buy, old], _states([buy, old]), phase="buy").attempts) == [1]

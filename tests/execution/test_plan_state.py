"""Decision state, remainders and buy targets (Phase 4 spec Definitions; plan T52).

Hand-computed cases, one per state and remainder kind. The literal check on
`execution/plan.py` is T50's (`tests/test_no_literals.py`).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.config import RiskConfig
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.plan import (
    BuyCosts,
    DecisionState,
    Remainder,
    State,
    decision_state,
    remainder,
    target_notional,
)
from tradepartner.store import schema
from tradepartner.store.journal import (
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    append,
    fills_for,
)

T0 = date(2026, 10, 1)  # rebalance session
S1 = date(2026, 10, 2)
S2 = date(2026, 10, 5)
S3 = date(2026, 10, 6)
A = "SEC_A"
PRICE = 50.0
FROZEN = RiskConfig()  # min_order_notional 1.0, whole_share_price_buffer 0.02
_IDS = itertools.count(1)


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def price_of(security_id: str) -> float:
    return {A: PRICE, "SEC_B": 20.0, "SEC_C": 100.0}[security_id]


def _decision(
    *,
    side: str | None = "sell",
    decision: str = "trade",
    planned_notional: float | None = None,
    planned_quantity: float | None = None,
    target: float | None = None,
    whole_share: bool = False,
    security_id: str = A,
    rebalance_session: date | None = T0,
    decision_id: int = 1,
) -> DecisionRow:
    stamp = _utc(T0, 21)
    return DecisionRow(
        decision_id=decision_id,
        run_id=1,
        rebalance_session=rebalance_session,
        security_id=security_id,
        side=side,
        planned_notional=planned_notional,
        planned_quantity=planned_quantity,
        target_notional=target,
        whole_share=whole_share,
        decision=decision,
        known_at=stamp,
        ingested_at=stamp,
    )


def _order(
    decision: DecisionRow,
    session: date,
    *,
    attempt: int = 1,
    notional: float | None = None,
    quantity: float | None = None,
    sells_in_flight: bool = False,
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
        symbol="A",
        side=decision.side,
        notional=notional,
        quantity=quantity,
        sells_in_flight_at_submit=sells_in_flight,
        known_at=stamp,
        ingested_at=stamp,
    )


def _event(
    order: OrderRow, status: str, *, minute: int = 0, reason: str | None = None
) -> OrderEventRow:
    stamp = _utc(order.session, 14, minute)
    return OrderEventRow(
        client_order_id=order.client_order_id,
        status=status,
        reason=reason,
        known_at=stamp,
        ingested_at=stamp,
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


def _decision_event(
    decision: DecisionRow, status: str, reason: str | None = None
) -> DecisionEventRow:
    assert decision.decision_id is not None
    stamp = _utc(S1, 22)
    return DecisionEventRow(
        decision_id=decision.decision_id,
        run_id=1,
        status=status,
        reason=reason,
        known_at=stamp,
        ingested_at=stamp,
    )


def _splits(*rows: tuple[str, date, float]) -> pl.DataFrame:
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


def _remainder(
    decision: DecisionRow,
    orders: list[OrderRow],
    fills: list[OrderedFill],
    *,
    actions: pl.DataFrame = NO_ACTIONS,
    session: date = S2,
) -> Remainder:
    return remainder(decision, orders, [], fills, actions, price_of, session=session)


def _state(
    decision: DecisionRow,
    orders: list[OrderRow] = (),  # type: ignore[assignment]
    events: list[OrderEventRow] = (),  # type: ignore[assignment]
    fills: list[OrderedFill] = (),  # type: ignore[assignment]
    *,
    decision_events: list[DecisionEventRow] = (),  # type: ignore[assignment]
    actions: pl.DataFrame = NO_ACTIONS,
    session: date = S2,
    frozen: RiskConfig = FROZEN,
) -> DecisionState:
    return decision_state(
        decision,
        list(decision_events),
        list(orders),
        list(events),
        list(fills),
        actions,
        price_of,
        frozen,
        session=session,
    )


# Remainders, one per kind.


def test_quantity_sell_remainder_is_submitted_minus_filled() -> None:
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    r = _remainder(d, [o], [_fill(o, 4, 49.0), _fill(o, 2, 51.0)])
    # 10 - 6 = 4 shares, valued at the reference price 50: 200.
    assert r == Remainder(quantity=4.0, notional=200.0)


def test_quantity_sell_remainder_takes_splits_after_the_order_through_s() -> None:
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    fills = [_fill(o, 6, 50.0)]
    # A 2:1 split with ex-date S2 (in (S1, S2]) doubles the 4 unfilled shares.
    assert _remainder(d, [o], fills, actions=_splits((A, S2, 2.0))).quantity == 8.0
    # One with ex-date S3 (after S = S2) is not applied, though already known.
    assert _remainder(d, [o], fills, actions=_splits((A, S3, 2.0))).quantity == 4.0
    # One on or before the order's own session is already in its quantity.
    assert _remainder(d, [o], fills, actions=_splits((A, S1, 2.0))).quantity == 4.0


def test_quantity_sell_remainder_reads_only_the_latest_order() -> None:
    d = _decision(planned_quantity=10)
    first = _order(d, S1, quantity=10)
    second = _order(d, S2, attempt=1, quantity=4)
    fills = [_fill(first, 6, 50.0), _fill(second, 1, 50.0)]
    # The second attempt was sized for the first's remainder: 4 - 1 = 3.
    assert _remainder(d, [second, first], fills).quantity == 3.0


def test_notional_sell_remainder_is_submitted_notional_minus_filled_value() -> None:
    d = _decision(planned_notional=500.0)
    o = _order(d, S1, notional=500.0)
    r = _remainder(d, [o], [_fill(o, 3, 100.0), _fill(o, 1, 90.0)])
    # 500 - (300 + 90) = 110 notional; 110 / 50 = 2.2 shares at the reference price.
    assert r.notional == pytest.approx(110.0)
    assert r.quantity == pytest.approx(2.2)


def test_buy_remainder_is_target_minus_filled_value_over_every_order() -> None:
    d = _decision(side="buy", planned_notional=1100.0, target=1000.0)
    first = _order(d, S1, notional=1000.0)
    second = _order(d, S2, notional=600.0, sells_in_flight=True)
    fills = [_fill(first, 4, 100.0), _fill(second, 3, 110.0)]
    r = _remainder(d, [first, second], fills)
    # 1000 - 400 - 330 = 270; 270 / 50 = 5.4 shares.
    assert r.notional == pytest.approx(270.0)
    assert r.quantity == pytest.approx(5.4)


def test_remainder_before_any_order_is_the_plan() -> None:
    buy = _decision(side="buy", planned_notional=300.0, target=290.0)
    assert _remainder(buy, [], []).notional == 290.0
    trim = _decision(planned_notional=120.0)
    assert _remainder(trim, [], []).notional == 120.0
    exit_ = _decision(planned_quantity=7)
    # Split-adjusted from the rebalance session T0 through S: 2:1 on S1 gives 14.
    assert _remainder(exit_, [], [], actions=_splits((A, S1, 2.0))).quantity == 14.0


def test_remainder_ignores_other_decisions_rows() -> None:
    d = _decision(planned_quantity=10)
    other = _decision(planned_quantity=5, decision_id=2)
    o = _order(d, S1, quantity=10)
    o2 = _order(other, S1, attempt=2, quantity=5)
    assert _remainder(d, [o, o2], [_fill(o, 6, 50.0), _fill(o2, 5, 50.0)]).quantity == 4.0


def test_a_buy_without_a_target_is_refused() -> None:
    with pytest.raises(ValueError, match="target_notional"):
        _remainder(_decision(side="buy", planned_notional=100.0), [], [])


def test_a_sell_without_a_planned_amount_is_refused() -> None:
    with pytest.raises(ValueError, match="planned"):
        _remainder(_decision(), [], [])


# States.


@pytest.mark.parametrize(
    "kind",
    [
        "skip_below_minimum",
        "skip_untradable",
        "skip_below_one_share",
        "skip_delisted",
        "skip_zero",
        "dust",
    ],
)
def test_skip_and_dust_decisions_are_closed(kind: str) -> None:
    state = _state(_decision(side=None, decision=kind))
    assert (state.state, state.reason) == (State.CLOSED, kind)


def test_a_keep_name_override_is_closed() -> None:
    # `keep_name` is an `override` decision with nothing to trade (no side).
    state = _state(_decision(side=None, decision="override"))
    assert (state.state, state.reason) == (State.CLOSED, "keep_name")


@pytest.mark.parametrize(
    ("status", "reason"), [("skipped", "untradable"), ("written_off", "unfunded")]
)
def test_a_decision_event_closes_the_decision(status: str, reason: str) -> None:
    d = _decision(side="buy", planned_notional=100.0, target=100.0)
    state = _state(d, decision_events=[_decision_event(d, status, reason)])
    assert (state.state, state.reason) == (State.CLOSED, status)
    assert not state.written_off


def test_a_deferred_buy_is_open_with_no_row() -> None:
    d = _decision(side="buy", planned_notional=100.0, target=100.0)
    state = _state(d)
    assert state.state == State.OPEN
    assert state.remainder == Remainder(quantity=2.0, notional=100.0)


@pytest.mark.parametrize(
    "statuses",
    [[], ["pending"], ["pending", "accepted"], ["accepted", "cancel_requested"], ["replay"]],
)
def test_an_order_with_no_terminal_event_is_in_flight(statuses: list[str]) -> None:
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    events = [_event(o, s, minute=i) for i, s in enumerate(statuses)]
    assert _state(d, [o], events).state == State.IN_FLIGHT


def test_an_order_is_terminal_even_when_a_later_event_is_not() -> None:
    # A `cancel_noop` journaled after `expired` is the latest row of a terminal order.
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    events = [
        _event(o, "accepted"),
        _event(o, "expired", minute=1),
        _event(o, "cancel_noop", minute=2),
    ]
    assert _state(d, [o], events).state == State.OPEN


def test_a_fractionable_exit_with_0_8_shares_above_the_minimum_stays_open() -> None:
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    state = _state(d, [o], [_event(o, "expired")], [_fill(o, 9.2, 50.0)])
    # 0.8 x 50 = 40, above min_order_notional 1.0.
    assert state.state == State.OPEN
    assert state.remainder is not None
    assert state.remainder.quantity == pytest.approx(0.8)


def test_a_non_fractionable_sub_share_residue_settles() -> None:
    d = _decision(planned_quantity=10, whole_share=True)
    o = _order(d, S1, quantity=10)
    state = _state(d, [o], [_event(o, "expired")], [_fill(o, 9.5, 50.0)])
    # 0.5 x 50 = 25, below one share at 50 x 1.02 = 51.
    assert state.state == State.SETTLED


def test_a_whole_share_residue_of_one_share_or_more_stays_open() -> None:
    d = _decision(planned_quantity=10, whole_share=True)
    o = _order(d, S1, quantity=10)
    state = _state(d, [o], [_event(o, "expired")], [_fill(o, 8, 50.0)])
    # 2 x 50 = 100, above 51.
    assert state.state == State.OPEN


def test_a_fractionable_remainder_below_the_minimum_settles() -> None:
    d = _decision(side="buy", planned_notional=100.0, target=100.0)
    o = _order(d, S1, notional=100.0)
    state = _state(d, [o], [_event(o, "filled")], [_fill(o, 1.99, 50.0)])
    # 100 - 99.5 = 0.5, below min_order_notional 1.0.
    assert state.state == State.SETTLED


def test_the_minimum_comes_from_the_frozen_values() -> None:
    d = _decision(side="buy", planned_notional=100.0, target=100.0)
    o = _order(d, S1, notional=100.0)
    args = ([o], [_event(o, "filled")], [_fill(o, 1.99, 50.0)])
    raised = RiskConfig(min_order_notional=0.25)
    # The same 0.5 remainder is open, and a buy with no sell in flight is written off.
    state = _state(d, *args, frozen=raised)
    assert (state.state, state.reason, state.written_off) == (State.CLOSED, "written_off", True)


# The write-off rule for buys.


def _terminal_buy(
    *, sells_in_flight: bool = False, extra: tuple[tuple[str, str | None], ...] = ()
) -> tuple[DecisionRow, OrderRow, list[OrderEventRow]]:
    d = _decision(side="buy", planned_notional=500.0, target=500.0)
    o = _order(d, S1, notional=500.0, sells_in_flight=sells_in_flight)
    events = [_event(o, "accepted")]
    events += [_event(o, s, minute=i + 1, reason=r) for i, (s, r) in enumerate(extra)]
    return d, o, events


def test_a_buy_is_written_off_when_no_sell_was_in_flight_at_submit() -> None:
    d, o, events = _terminal_buy(extra=(("expired", None),))
    state = _state(d, [o], events, [_fill(o, 4, 50.0)])
    # 500 - 200 = 300 unfunded.
    assert (state.state, state.reason, state.written_off) == (State.CLOSED, "written_off", True)
    assert state.remainder == Remainder(quantity=6.0, notional=300.0)


def test_a_buy_with_a_sell_in_flight_at_submit_stays_open() -> None:
    d, o, events = _terminal_buy(sells_in_flight=True, extra=(("expired", None),))
    state = _state(d, [o], events)
    assert (state.state, state.written_off) == (State.OPEN, False)


@pytest.mark.parametrize(
    "extra",
    [
        (("cancel_requested", "halt"), ("cancelled", None)),
        (("cancel_requested", "halt"), ("cancel_failed", "halt"), ("expired", None)),
        (("cancel_failed", "halt"), ("cancelled", None)),
        (("cancelled", "not_received"),),
    ],
    ids=["halt-cancel", "halt-cancel-failed", "cancel-failed-only", "not-received"],
)
def test_a_halt_or_a_crash_is_not_a_funding_shortfall(
    extra: tuple[tuple[str, str | None], ...],
) -> None:
    d, o, events = _terminal_buy(extra=extra)
    state = _state(d, [o], events)
    assert (state.state, state.written_off) == (State.OPEN, False)


def test_a_cancel_without_the_halt_reason_does_not_protect_the_buy() -> None:
    d, o, events = _terminal_buy(extra=(("cancel_requested", "owner"), ("cancelled", None)))
    assert _state(d, [o], events).written_off


def test_a_sell_with_a_remainder_is_never_written_off() -> None:
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    state = _state(d, [o], [_event(o, "rejected")])
    assert (state.state, state.written_off) == (State.OPEN, False)


def test_the_write_off_rule_reads_the_latest_order() -> None:
    d = _decision(side="buy", planned_notional=500.0, target=500.0)
    first = _order(d, S1, notional=500.0, sells_in_flight=True)
    second = _order(d, S2, notional=300.0, sells_in_flight=False)
    events = [_event(first, "expired"), _event(second, "expired")]
    fills = [_fill(first, 4, 50.0)]
    state = _state(d, [first, second], events, fills)
    assert state.written_off


# A superseded fill changes nothing: it is hidden by the accessor.


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_a_superseded_fill_changes_no_remainder_or_state(store: duckdb.DuckDBPyConnection) -> None:
    stamp = {"known_at": _utc(T0, 21), "ingested_at": _utc(T0, 21)}
    append(
        store,
        PaperWindowRow(
            hypothesis_id=1,
            first_rebalance_session=T0,
            account_id="PA1",
            starting_cash=1000.0,
            starting_equity=1000.0,
            code_version="abc",
            started_at=_utc(T0, 12),
            frozen_json="{}",
            frozen_sha256="0" * 64,
            **stamp,
        ),
    )
    append(
        store,
        PaperRunRow(
            window_id=1,
            session=T0,
            kind="rebalance",
            started_at=_utc(T0, 12),
            invoked_by="scheduler",
            code_version="abc",
            **stamp,
        ),
    )
    d = _decision(planned_quantity=10)
    o = _order(d, S1, quantity=10)
    append(store, o)
    status = append(
        store,
        FillRow(
            client_order_id=o.client_order_id,
            filled_at=_utc(S1, 14),
            quantity=9.2,
            price=50.0,
            price_implied=True,
            broker_fill_id=f"synthetic:{o.client_order_id}",
            source="broker_status",
            **stamp,
        ),
    )
    append(
        store,
        FillRow(
            client_order_id=o.client_order_id,
            filled_at=_utc(S1, 14),
            quantity=9.2,
            price=50.0,
            price_implied=False,
            broker_fill_id="bf-late",
            source="broker_feed",
            superseded_by=status,
            **stamp,
        ),
    )
    fills = fills_for(store, window_id=1)
    assert len(fills) == 1
    events = [_event(o, "filled")]
    assert _remainder(d, [o], fills).quantity == pytest.approx(0.8)
    assert _state(d, [o], events, fills).state == State.OPEN


# Buy targets.

COSTS = BuyCosts(per_side_bps=15.0, commissions=Commissions(per_share=0.0, per_order=0.0))


def test_target_scales_planned_buys_to_spendable_cash_if_every_sell_fills() -> None:
    buys = [
        _decision(side="buy", planned_notional=300.0, security_id=A, decision_id=10),
        _decision(side="buy", planned_notional=500.0, security_id="SEC_B", decision_id=11),
        _decision(side="buy", planned_notional=200.0, security_id="SEC_C", decision_id=12),
    ]
    sells = [
        _decision(planned_notional=400.0, security_id="SEC_B", decision_id=20),
        _decision(planned_quantity=5, security_id="SEC_C", decision_id=21),  # 5 x 100
        # A forced exit that joins the sells phase is not in the target.
        _decision(
            planned_quantity=10,
            security_id=A,
            decision="forced_exit",
            rebalance_session=None,
            decision_id=22,
        ),
    ]
    # Cash 100 + sells 400 + 500 = 1000, spendable 1000 / 1.0015, planned sum 1000.
    spendable = buy_notional_after_costs(1000.0, 15.0, COSTS.commissions, price=PRICE)
    assert spendable == pytest.approx(1000.0 / 1.0015)
    scale = spendable / 1000.0
    got = [target_notional(b, 100.0, sells, buys, price_of, COSTS) for b in buys]
    assert got == pytest.approx([300.0 * scale, 500.0 * scale, 200.0 * scale])
    assert sum(got) <= spendable + 1e-9


def test_target_never_scales_above_the_plan() -> None:
    buy = _decision(side="buy", planned_notional=300.0, decision_id=10)
    assert target_notional(buy, 10_000.0, [], [buy], price_of, COSTS) == 300.0


def test_target_refuses_a_decision_that_is_not_a_planned_buy() -> None:
    sell = _decision(planned_notional=100.0)
    with pytest.raises(ValueError, match="buy"):
        target_notional(sell, 100.0, [], [sell], price_of, COSTS)
    buy = _decision(side="buy", planned_notional=100.0, decision_id=10)
    with pytest.raises(ValueError, match="planned_buys"):
        target_notional(buy, 100.0, [], [], price_of, COSTS)

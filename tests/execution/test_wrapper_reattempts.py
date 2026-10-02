"""End-to-end re-attempts on the scripted broker book (T60e)."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

import duckdb
import pytest

from tradepartner.adapters.broker import TERMINAL_STATUSES
from tradepartner.adapters.fake_broker import Accept, Expire, FillAt, PartialFill
from tradepartner.execution import switch, wrapper
from tradepartner.execution.collect import WriteOffContext
from tradepartner.execution.plan import RebalanceState, State, rebalance_state
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    ReconciliationRow,
    ResumeInvocationRow,
    rebalance_events_for,
    runs_for,
)

from .test_wrapper_phases import (
    PRICE,
    T_I,
    A,
    B,
    C,
    Env,
    S,
    _append,
    _decision,
    _decision_events,
    _execute,
    _gate,
    _hold,
    _open_buy,
    _override,
    _query,
    _run,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


def _orders(env: Env, decision_id: int) -> list[tuple[str, float | None, float | None]]:
    return _query(
        env.settings,
        "SELECT client_order_id, notional, quantity FROM orders "
        "WHERE decision_id = ? ORDER BY rowid",
        [decision_id],
    )


def _assert_one_live_per_decision(env: Env, decision_id: int) -> None:
    live = [
        coid
        for coid, _, _ in _orders(env, decision_id)
        if env.fake.get_order(coid).status not in TERMINAL_STATUSES
    ]
    assert len(live) <= 1


def _buy_share(env: Env, cash: float, count: int) -> float:
    """One equally sized buy after the configured per-side cost reserve."""
    factor = Decimal(1) + Decimal(str(env.settings.costs.per_side_bps)) / Decimal(10_000)
    return float(
        (Decimal(str(cash)) / factor / Decimal(count)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
    )


def _write_off_amounts(env: Env) -> list[tuple[int, float]]:
    return _query(
        env.settings,
        "SELECT decision_id, unfunded_notional FROM decision_events "
        "WHERE status = 'written_off' ORDER BY rowid",
    )


def _halt_then_resume(env: Env, gate: wrapper.RiskGatedBroker, decisions: list[object]) -> None:
    """Use the wrapper halt, then journal an owner resume and release for a new run."""
    book = gate._read_book(env.run, decisions)  # type: ignore[arg-type]
    context = WriteOffContext(
        window_id=env.window.window_id,  # type: ignore[arg-type]
        actions_as_of=book.actions,
        price_of=book.price_of,
        session=S,
    )
    with pytest.raises(ValueError, match="intentional halt"):
        gate.halt(ValueError("intentional halt"), env.run, write_offs=context)
    seen = _query(
        env.settings,
        "SELECT max(event_id) FROM kill_switch WHERE window_id = ?",
        [env.window.window_id],
    )[0][0]
    env.clock.advance(seconds=1)
    resume_at = env.clock.now
    (resume_id,) = _append(
        env.settings,
        ResumeInvocationRow(
            at=resume_at,
            reason="reviewed fault and broker state",
            accept_broker_fills=False,
            accept_rejections=False,
            known_at=resume_at,
            ingested_at=resume_at,
        ),
    )
    env.clock.advance(seconds=1)
    reconciled_at = env.clock.now
    (reconciliation_id,) = _append(
        env.settings,
        ReconciliationRow(
            window_id=env.window.window_id,  # type: ignore[arg-type]
            at=reconciled_at,
            status="ok",
            broker_cash=env.fake.account().cash,
            known_at=reconciled_at,
            ingested_at=reconciled_at,
        ),
    )
    assert resume_id is not None and reconciliation_id is not None
    env.clock.advance(seconds=1)
    switch.release(
        env.settings,
        env.clock,
        window_id=env.window.window_id,  # type: ignore[arg-type]
        resume_id=resume_id,
        reconciliation_id=reconciliation_id,
        peak_equity=env.fake.account().equity,
        seen_event_id=seen,
    )
    env.clock.advance(seconds=1)
    env.run = _run(env.settings, env.window, S, env.clock.now)


def test_last_phase_expired_buy_is_written_off_and_never_reordered(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())  # sell completes before the buys phase
    env.fake.script(Expire())
    gate = _gate(env, alerter_conn)
    first = _execute(gate, env, [sell, buy])
    assert first.written_off == (buy.decision_id,)
    assert _decision_events(env.settings) == [(buy.decision_id, "written_off", "unfunded")]
    assert _write_off_amounts(env) == [(buy.decision_id, 3000.0)]
    assert _execute(gate, env, [sell, buy]).submitted == ()
    assert len(_orders(env, buy.decision_id)) == 1  # type: ignore[arg-type]


def test_open_sell_shortfall_keeps_buy_open_then_retries_its_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=2500.0)
    _hold(env, A, 10.0)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=1500.0)
    other = _decision(env, "SEC_STATIC_PRE2019", "buy", notional=1500.0)
    before = env.fake.account().cash
    env.fake.script(Accept())
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    gate = _gate(env, alerter_conn)
    first = _execute(gate, env, [sell, buy, other])
    expected_first = _buy_share(env, before, 2)
    assert expected_first < 1500.0
    assert 2 * expected_first <= before
    assert [row[1] for row in _orders(env, buy.decision_id)] == [expected_first]  # type: ignore[arg-type]
    assert [row[1] for row in _orders(env, other.decision_id)] == [expected_first]  # type: ignore[arg-type]
    assert first.written_off == ()
    first_book = gate._read_book(env.run, [sell, buy, other])
    for decision in (buy, other):
        state = first_book.states[decision.decision_id]  # type: ignore[index]
        assert state.state is State.OPEN
        assert state.remainder is not None
        assert state.remainder.notional == pytest.approx(1500.0 - expected_first)
    _assert_one_live_per_decision(env, buy.decision_id)  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, other.decision_id)  # type: ignore[arg-type]
    sell_coid = _orders(env, sell.decision_id)[0][0]  # type: ignore[arg-type]
    env.fake.apply(sell_coid, FillAt())
    after_sell = env.fake.account().cash
    sell_rows = [
        row
        for row in gate._read_book(env.run, [sell, buy, other]).orders
        if row.client_order_id == sell_coid
    ]
    gate._collect(env.run, sell_rows)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    second = _execute(gate, env, [sell, buy, other])
    assert len(second.submitted) == 2
    expected_second = _buy_share(env, after_sell, 2)
    assert expected_second < 1500.0 - expected_first
    assert 2 * expected_second <= after_sell
    for decision in (buy, other):
        assert [row[1] for row in _orders(env, decision.decision_id)] == [  # type: ignore[arg-type]
            expected_first,
            expected_second,
        ]
    assert set(second.written_off) == {buy.decision_id, other.decision_id}
    _assert_one_live_per_decision(env, buy.decision_id)  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, other.decision_id)  # type: ignore[arg-type]


def test_partly_filled_trim_expires_and_sells_only_the_notional_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    fill_price = float(Decimal(str(PRICE)) * Decimal("1.1"))
    env.fake.script(PartialFill(2.0, fill_price))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [trim])
    first = _orders(env, trim.decision_id)[0][0]  # type: ignore[arg-type]
    env.fake.apply(first, Expire())
    gate._collect(env.run, [gate._read_book(env.run, [trim]).orders[-1]])
    env.fake.script(FillAt())
    _execute(gate, env, [trim])
    assert [quantity for _, _, quantity in _orders(env, trim.decision_id)] == [
        500.0 / PRICE,
        (500.0 - 2.0 * fill_price) / PRICE,
    ]  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, trim.decision_id)  # type: ignore[arg-type]


def test_closed_decision_does_not_submit_again(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    buy = _decision(env, A, "buy", notional=3000.0)
    env.fake.script(FillAt())
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [buy])
    before = len(_submits(env.fake))
    assert _execute(gate, env, [buy]).submitted == ()
    assert len(_submits(env.fake)) == before
    assert gate._read_book(env.run, [buy]).states[buy.decision_id].state is State.SETTLED  # type: ignore[index]


def test_expired_buy_submitted_with_sell_in_flight_is_reattempted(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(Accept())
    env.fake.script(Expire())
    gate = _gate(env, alerter_conn)
    first = _execute(gate, env, [sell, buy])
    assert first.written_off == ()
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(True,)]
    env.fake.script(FillAt())
    second = _execute(gate, env, [sell, buy])
    assert len(second.submitted) == 1
    assert len(_orders(env, buy.decision_id)) == 2  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, buy.decision_id)  # type: ignore[arg-type]


def test_sells_fill_half_percent_below_reference_and_last_phase_writes_off_buys(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=1100.0)
    _hold(env, A, 10.0)
    sell = _decision(env, A, "sell", quantity=10.0, reason="left_targets")
    buy = _decision(env, B, "buy", notional=3000.0)
    deferred = _decision(env, "SEC_STATIC_PRE2019", "buy", notional=100.0, whole_share=True)
    cash_before_sell = env.fake.account().cash
    sell_price = PRICE * (1 - 0.005)
    env.fake.script(FillAt(sell_price))
    env.fake.script(Expire())
    gate = _gate(env, alerter_conn)
    result = _execute(gate, env, [sell, buy, deferred])
    assert result.deferred == (deferred.decision_id,)
    assert _orders(env, buy.decision_id)[0][1] == _buy_share(  # type: ignore[arg-type]
        env, cash_before_sell + 10.0 * sell_price, 1
    )
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(False,)]
    assert set(result.written_off) == {buy.decision_id, deferred.decision_id}
    assert _write_off_amounts(env) == [
        (buy.decision_id, 3000.0),
        (deferred.decision_id, 100.0),
    ]
    assert _decision_events(env.settings) == [
        (buy.decision_id, "written_off", "unfunded"),
        (deferred.decision_id, "written_off", "unfunded"),
    ]
    book = gate._read_book(env.run, [sell, buy, deferred])
    assert book.states[buy.decision_id].state is State.CLOSED  # type: ignore[index]
    assert book.states[deferred.decision_id].state is State.CLOSED  # type: ignore[index]
    assert _execute(gate, env, [sell, buy, deferred]).submitted == ()
    with open_for_write(env.settings) as conn:
        runs = [item.run for item in runs_for(conn, env.window.window_id)]  # type: ignore[arg-type]
        events = rebalance_events_for(conn, env.window.window_id)  # type: ignore[arg-type]
    assert (
        rebalance_state(
            T_I,
            env.window,
            runs,
            events,
            [
                (decision, book.states[decision.decision_id])  # type: ignore[index]
                for decision in (sell, buy, deferred)
            ],
            session=S,
        )
        is RebalanceState.EXECUTED
    )


def test_partly_filled_buy_cancelled_for_halt_retries_only_unfilled_target(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    target = 3000.0
    filled_quantity = 10.0
    fill_price = float(Decimal(str(PRICE)) * Decimal("1.1"))
    buy = _decision(env, A, "buy", notional=target)
    env.fake.script(PartialFill(filled_quantity, fill_price))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [buy])
    _halt_then_resume(env, gate, [buy])
    assert gate._read_book(env.run, [buy]).states[buy.decision_id].state is State.OPEN  # type: ignore[index]
    assert _write_off_amounts(env) == []
    env.fake.script(FillAt())
    _execute(_gate(env, alerter_conn), env, [buy])
    assert [notional for _, notional, _ in _orders(env, buy.decision_id)] == [
        target,
        target - filled_quantity * fill_price,
    ]  # type: ignore[arg-type]


def test_full_exit_cancelled_after_partial_fill_sells_reconciled_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    exit_ = _decision(env, A, "sell", quantity=8.0, reason="left_targets")
    env.fake.script(PartialFill(4.0, PRICE))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [exit_])
    _halt_then_resume(env, gate, [exit_])
    env.fake.script(FillAt())
    _execute(_gate(env, alerter_conn), env, [exit_])
    assert [quantity for _, _, quantity in _orders(env, exit_.decision_id)] == [10.0, 6.0]  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, exit_.decision_id)  # type: ignore[arg-type]


def test_last_phase_expired_buy_is_written_off_by_collection(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    buy = _decision(env, A, "buy", notional=3000.0)
    env.fake.script(Expire())
    original = wrapper.collect
    at_collection: list[tuple[int, str, str | None]] = []

    def observed_collect(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        at_collection.extend(_decision_events(env.settings))
        return result

    monkeypatch.setattr(wrapper, "collect", observed_collect)
    _execute(_gate(env, alerter_conn), env, [buy])
    assert at_collection == [(buy.decision_id, "written_off", "unfunded")]


def test_expired_buy_is_written_off_once_not_twice(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The `written_off` row the buys phase's own collection appends (#538)
    must not be appended again by the phase's end-of-phase write-offs."""
    buy = _decision(env, A, "buy", notional=3000.0)
    env.fake.script(Expire())
    outcome = _execute(_gate(env, alerter_conn), env, [buy])
    assert outcome.written_off == (buy.decision_id,)
    rows = _query(
        env.settings,
        "SELECT decision_id, status FROM decision_events "
        "WHERE decision_id = ? AND status = 'written_off'",
        [buy.decision_id],
    )
    assert rows == [(buy.decision_id, "written_off")]
    assert _write_off_amounts(env) == [(buy.decision_id, 3000.0)]


def test_engaged_switch_before_buys_first_submit_writes_off_no_deferred_buy(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The T60b pin (#487): a switch read engaged before the buys phase's
    first submit never collects, so it writes off no deferred buy, even with
    the collection-time write-off path (#538) now wired in."""
    env.new_fake(cash=2000.0)
    _open_buy(env, C, 2000.0)
    buy = _decision(env, A, "buy", notional=3000.0)
    _override(env)
    outcome = _execute(_gate(env, alerter_conn), env, [buy])
    assert outcome.status == "skipped_kill_switch"
    assert outcome.written_off == ()
    assert _decision_events(env.settings) == []

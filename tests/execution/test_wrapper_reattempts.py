"""End-to-end re-attempts on the scripted broker book (T60e)."""

from __future__ import annotations

import duckdb
import pytest

from tradepartner.adapters.broker import TERMINAL_STATUSES
from tradepartner.adapters.fake_broker import Accept, Expire, FillAt, PartialFill
from tradepartner.execution import wrapper
from tradepartner.execution.plan import RebalanceState, State, rebalance_state
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    OrderEventRow,
    rebalance_events_for,
    runs_for,
)

from .test_wrapper_phases import (
    PRICE,
    T_I,
    A,
    B,
    Env,
    S,
    _append,
    _decision,
    _decision_events,
    _execute,
    _gate,
    _hold,
    _query,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


def _orders(env: Env, decision_id: int) -> list[tuple[str, float | None, float | None]]:
    return _query(
        env.settings,
        "SELECT client_order_id, notional, quantity FROM orders WHERE decision_id = ? "
        "AND run_id = ? ORDER BY rowid",
        [decision_id, env.run.run_id],
    )


def _assert_one_live_per_decision(env: Env, decision_id: int) -> None:
    live = [
        coid
        for coid, _, _ in _orders(env, decision_id)
        if env.fake.get_order(coid).status not in TERMINAL_STATUSES
    ]
    assert len(live) <= 1


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
    assert _execute(gate, env, [sell, buy]).submitted == ()
    assert len(_orders(env, buy.decision_id)) == 1  # type: ignore[arg-type]


def test_open_sell_shortfall_keeps_buy_open_then_retries_its_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=2500.0)
    _hold(env, A, 10.0)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(Accept())
    env.fake.script(Expire())
    gate = _gate(env, alerter_conn)
    first = _execute(gate, env, [sell, buy])
    assert first.written_off == ()
    assert len(_orders(env, buy.decision_id)) == 1  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, buy.decision_id)  # type: ignore[arg-type]
    sell_coid = _orders(env, sell.decision_id)[0][0]  # type: ignore[arg-type]
    env.fake.apply(sell_coid, FillAt())
    sell_rows = [
        row
        for row in gate._read_book(env.run, [sell, buy]).orders
        if row.client_order_id == sell_coid
    ]
    gate._collect(env.run, sell_rows)
    env.fake.script(FillAt())
    second = _execute(gate, env, [sell, buy])
    assert len(second.submitted) == 1
    assert len(_orders(env, buy.decision_id)) == 2  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, buy.decision_id)  # type: ignore[arg-type]


def test_partly_filled_trim_expires_and_sells_only_the_notional_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    env.fake.script(PartialFill(2.0, PRICE))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [trim])
    first = _orders(env, trim.decision_id)[0][0]  # type: ignore[arg-type]
    env.fake.apply(first, Expire())
    gate._collect(env.run, [gate._read_book(env.run, [trim]).orders[-1]])
    env.fake.script(FillAt())
    _execute(gate, env, [trim])
    assert [quantity for _, _, quantity in _orders(env, trim.decision_id)] == [5.0, 3.0]  # type: ignore[arg-type]
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
    env.fake.script(FillAt(PRICE * (1 - 0.005)))
    env.fake.script(Expire())
    gate = _gate(env, alerter_conn)
    result = _execute(gate, env, [sell, buy, deferred])
    assert result.deferred == (deferred.decision_id,)
    assert set(result.written_off) == {buy.decision_id, deferred.decision_id}
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
    filled = 1000.0
    buy = _decision(env, A, "buy", notional=target)
    env.fake.script(PartialFill(filled / PRICE, PRICE))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [buy])
    coid = _orders(env, buy.decision_id)[0][0]  # type: ignore[arg-type]
    stamp = env.clock.now
    _append(
        env.settings,
        OrderEventRow(
            client_order_id=coid,
            status="cancel_requested",
            reason="halt",
            known_at=stamp,
            ingested_at=stamp,
        ),
    )
    env.fake.cancel(coid)
    gate._collect(env.run, [gate._read_book(env.run, [buy]).orders[-1]])
    assert gate._read_book(env.run, [buy]).states[buy.decision_id].state is State.OPEN  # type: ignore[index]
    env.fake.script(FillAt())
    _execute(gate, env, [buy])
    assert [notional for _, notional, _ in _orders(env, buy.decision_id)] == [
        target,
        target - filled,
    ]  # type: ignore[arg-type]


def test_full_exit_cancelled_after_partial_fill_sells_reconciled_remainder(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    exit_ = _decision(env, A, "sell", quantity=10.0, reason="left_targets")
    env.fake.script(PartialFill(4.0, PRICE))
    gate = _gate(env, alerter_conn)
    _execute(gate, env, [exit_])
    coid = _orders(env, exit_.decision_id)[0][0]  # type: ignore[arg-type]
    stamp = env.clock.now
    _append(
        env.settings,
        OrderEventRow(
            client_order_id=coid,
            status="cancel_requested",
            reason="halt",
            known_at=stamp,
            ingested_at=stamp,
        ),
    )
    env.fake.cancel(coid)
    gate._collect(env.run, [gate._read_book(env.run, [exit_]).orders[-1]])
    env.fake.script(FillAt())
    _execute(gate, env, [exit_])
    assert [quantity for _, _, quantity in _orders(env, exit_.decision_id)] == [10.0, 6.0]  # type: ignore[arg-type]
    _assert_one_live_per_decision(env, exit_.decision_id)  # type: ignore[arg-type]


@pytest.mark.xfail(strict=True, reason="#538")
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

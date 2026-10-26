"""An owner `paper kill` engaged while a phase is submitting stops that phase's
remaining submits (#2182, owner decision 2026-10-26: the switch is re-read
before every submit; spec req 5, ADR 0010 point 2 amendments)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import FillAt
from tradepartner.calendar import session_open
from tradepartner.execution import switch
from tradepartner.execution.wrapper import RiskGatedBroker

from .test_wrapper_phases import (
    A,
    B,
    C,
    Env,
    S,
    _decision,
    _decision_events,
    _execute,
    _gate,
    _hold,
    _query,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


def _kill_after_first_submit(env: Env) -> list[object]:
    engaged: list[object] = []

    def owner_kills(_request: OrderRequest) -> None:
        # `paper kill --reason ...` from another terminal, after the first submit.
        if not engaged:
            engaged.append(
                switch.engage(
                    env.settings,
                    env.clock,
                    window_id=env.window.window_id,  # type: ignore[arg-type]
                    source="owner",
                    reason="owner: stop now",
                )
            )

    env.fake.on_submit = owner_kills
    return engaged


def test_an_owner_kill_mid_buys_phase_stops_the_remaining_submits(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    buys = [_decision(env, s, "buy", notional=3000.0) for s in (A, B, C)]
    for _ in buys:
        env.fake.script(FillAt())
    engaged = _kill_after_first_submit(env)

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert isinstance(engaged[0], int)
    assert len(_submits(env.fake)) == 1
    assert outcome.status == "skipped_kill_switch"
    assert len(outcome.submitted) == 1
    # The two unsent orders are closed, not left pending; the next run re-attempts.
    closed = _query(
        env.settings,
        "SELECT count(*) FROM order_events WHERE status = 'cancelled' AND reason = 'not_received'",
    )
    assert closed == [(2,)]
    # The sent buy's fill is collected and journaled before the batch ends.
    (sent,) = outcome.submitted
    assert _query(env.settings, "SELECT count(*) FROM fills WHERE client_order_id = ?", [sent]) == [
        (1,)
    ]


def test_an_owner_kill_mid_sells_phase_stops_the_sells_and_the_buys(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _hold(env, B, 10.0)
    sells = [_decision(env, s, "sell", notional=500.0) for s in (A, B)]
    buy = _decision(env, C, "buy", notional=3000.0)
    for _ in range(3):
        env.fake.script(FillAt())
    _kill_after_first_submit(env)

    outcome = _execute(_gate(env, alerter_conn), env, [*sells, buy])

    assert outcome.status == "skipped_kill_switch"
    assert [r.side for r in _submits(env.fake)] == [Side.SELL]
    # The unsent sell is closed, not left pending, and no buy was journaled.
    closed = _query(
        env.settings,
        "SELECT o.side FROM order_events e JOIN orders o USING (client_order_id) "
        "WHERE e.status = 'cancelled' AND e.reason = 'not_received'",
    )
    assert closed == [("sell",)]
    assert _query(
        env.settings,
        "SELECT count(*) FROM orders WHERE side = 'buy' AND run_id = ?",
        [env.run.run_id],
    ) == [(0,)]


def test_a_mid_buys_kill_writes_off_no_deferred_buy(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The buys phase is the last, but a kill mid-phase leaves it incomplete: the
    deferred buy stays open for the run after the release, never written off
    `unfunded` (reattempts' incomplete-phase rule, #487)."""
    a, b = (_decision(env, s, "buy", notional=3000.0) for s in (A, B))
    # Whole shares at $100 a share: $50 buys none, so the buy is deferred.
    tiny = _decision(env, C, "buy", notional=50.0, whole_share=True)
    for _ in range(2):
        env.fake.script(FillAt())
    _kill_after_first_submit(env)

    outcome = _execute(_gate(env, alerter_conn), env, [a, b, tiny])

    assert outcome.status == "skipped_kill_switch"
    assert outcome.deferred == (tiny.decision_id,)
    assert len(_submits(env.fake)) == 1
    assert outcome.written_off == ()
    assert [e for e in _decision_events(env.settings) if e[0] == tiny.decision_id] == []


def test_a_switch_read_that_runs_past_the_window_sends_nothing_more(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window test is the last check before `submit` (#2178 with #2182): a lock
    wait inside the switch read that carries the clock past the window sends
    nothing more, and the unsent orders are closed `not_received`."""
    buys = [_decision(env, s, "buy", notional=3000.0) for s in (A, B, C)]
    for _ in buys:
        env.fake.script(FillAt())
    engaged = RiskGatedBroker._engaged

    def slow_read(self: RiskGatedBroker, *args: Any, **kwargs: Any) -> bool:
        if _submits(env.fake):
            env.clock.now = session_open(S) + timedelta(minutes=31)
        return engaged(self, *args, **kwargs)

    monkeypatch.setattr(RiskGatedBroker, "_engaged", slow_read)

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert len(_submits(env.fake)) == 1
    assert outcome.status == "ok"
    closed = _query(
        env.settings,
        "SELECT count(*) FROM order_events WHERE status = 'cancelled' AND reason = 'not_received'",
    )
    assert closed == [(2,)]


def test_a_run_with_no_kill_submits_every_order(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    buys = [_decision(env, s, "buy", notional=3000.0) for s in (A, B, C)]
    for _ in buys:
        env.fake.script(FillAt())

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert outcome.status == "ok"
    assert len(_submits(env.fake)) == 3

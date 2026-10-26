"""An owner `paper kill` engaged while a phase is submitting stops that phase's
remaining submits (#2182, owner decision 2026-10-26: the switch is re-read
before every submit; spec req 5, ADR 0010 point 2 amendments)."""

from __future__ import annotations

import duckdb

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import FillAt
from tradepartner.execution import switch

from .test_wrapper_phases import (
    A,
    B,
    C,
    Env,
    _decision,
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


def test_a_run_with_no_kill_submits_every_order(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    buys = [_decision(env, s, "buy", notional=3000.0) for s in (A, B, C)]
    for _ in buys:
        env.fake.script(FillAt())

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert outcome.status == "ok"
    assert len(_submits(env.fake)) == 3

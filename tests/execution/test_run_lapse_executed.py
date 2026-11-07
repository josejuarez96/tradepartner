"""A rebalance that traded but lapses with small open remainders is `executed`, not
`missed` (#3141).

Production, book daily, T = 2026-10-29: every one of its 196 orders filled on 10-30
(run 39), but 13 decisions kept remainders of $1-7 (a notional-cap leftover, a
fractional sell) at or above `risk.min_order_notional`. Their orders were submitted
with sells in flight, so they were never written off, the rebalance never reached
`executed`, and at its lapse (11-02) it was journaled `missed` / `catch_up_lapsed`,
so the tracking check skipped a traded period.

The test reproduces that journal on the T63d `Env` (temp copy of the fixture store,
scripted fake): after the F_0 build, one buy's `target_notional` is raised (an open
remainder, broker and ledger still agree), its order is marked as sent with sells in
flight, and the build's `executed` row is removed. The run past the catch-up bound
must journal `executed`. A rebalance with a decision never sent still lapses `missed`
(`test_run_trade.py::test_a_rebalance_skipped_past_its_catch_up_bound_lapses...`).
"""

from __future__ import annotations

from datetime import date

from tradepartner.store.db import open_for_write
from tradepartner.store.journal import PaperWindowRow

from .test_run_trade import T_0, Env, at, bought
from .test_run_trade import _no_env_file as _no_env_file
from .test_run_trade import env as env
from .test_run_trade import window as window

PAST_BOUND = date(2019, 5, 6)  # F_0 + 3 sessions; the fixture's bound is 2


def _leave_an_open_remainder(env: Env) -> None:
    with open_for_write(env.settings) as conn:
        (decision_id,) = conn.execute(
            "SELECT decision_id FROM decisions WHERE rebalance_session = ? AND side = 'buy' "
            "AND security_id = 'SEC_TRANSFER'",
            [T_0],
        ).fetchone()
        conn.execute(
            "UPDATE decisions SET target_notional = target_notional + 500 WHERE decision_id = ?",
            [decision_id],
        )
        conn.execute(
            "UPDATE orders SET sells_in_flight_at_submit = TRUE WHERE decision_id = ?",
            [decision_id],
        )
        conn.execute("DELETE FROM rebalance_events WHERE rebalance_session = ?", [T_0])


def test_a_traded_rebalance_with_open_remainders_lapses_executed_not_missed(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    _leave_an_open_remainder(env)
    assert env.rebalance_events() == []
    later = env.run(at(PAST_BOUND))
    assert later.status == "ok", env.result(later.run_id)
    assert env.rebalance_events() == [(T_0, "executed", None, later.run_id)]
    assert env.alerts("missed_rebalance") == []

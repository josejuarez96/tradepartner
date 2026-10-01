"""The run-level forced exits (Phase 4 spec req 7 step 7, the "Forced exits"
criterion; plan T63d).

On the rebalance window of `test_run_trade.py` (its `Env`, through
`tracking_run` with the scripted fake and a settable clock): the F_0 run buys
DUALB, SPFT and TRNS, then a Form 25 for TRNS, accepted before close(S-1),
ends its listing at close(S-1) for a later session S. A spin-off receipt is an
`adjustments` row with the shares put in the fake's book, which has no
corporate actions of its own.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from execution.test_run_trade import (
    F_0,
    F_0_PLUS_1,
    T_0,
    Env,
    at,
    bought,
    delist,
    env,
    window,
)
from tradepartner.adapters.broker import Asset
from tradepartner.adapters.fake_broker import Expire
from tradepartner.calendar import previous_session, session_close
from tradepartner.store.journal import AdjustmentRow, PaperWindowRow

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests

MAY_3, MAY_6, MAY_7 = date(2019, 5, 3), date(2019, 5, 6), date(2019, 5, 7)
T_1, F_1 = date(2019, 5, 31), date(2019, 6, 3)
UNTRADABLE = Asset(tradable=False, fractionable=True, status="active", cusip=None)
TRADABLE = Asset(tradable=True, fractionable=True, status="active", cusip=None)


def exits(env: Env, security_id: str) -> list[tuple[int, str, str, int]]:
    """(decision_id, reason, side, run_id) of the name's forced exits."""
    return [
        (r[0], r[1], r[2], r[3])
        for r in env.query(
            "SELECT decision_id, reason, side, run_id FROM decisions "
            "WHERE decision = 'forced_exit' AND security_id = ? ORDER BY decision_id",
            [security_id],
        )
    ]


def exit_orders(env: Env, security_id: str) -> list[tuple[str, int, str, float | None, date]]:
    """(client_order_id, decision_id, phase, quantity, session) of the name's
    orders made for a forced exit."""
    return [
        (r[0], r[1], r[2], r[3], r[4])
        for r in env.query(
            "SELECT o.client_order_id, o.decision_id, o.phase, o.quantity, o.session "
            "FROM orders o JOIN decisions d USING (decision_id) "
            "WHERE d.decision = 'forced_exit' AND o.security_id = ? ORDER BY o.known_at",
            [security_id],
        )
    ]


def ended_before(env: Env, session: date, security_id: str = "SEC_TRANSFER") -> None:
    """The name's Form 25, accepted an hour before close(S-1)."""
    delist(env, security_id, session_close(previous_session(session)) - timedelta(hours=1))


def test_a_delisted_name_is_sold_whole_by_the_next_in_window_run(
    env: Env, window: PaperWindowRow
) -> None:
    """A run outside the submit window makes no exit; the next in-window run on
    a non-fill session sells the whole holding as `forced_exit` (`delisted`),
    a batch of its own (phase `exit`), and the name leaves the book."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    early = env.run(at(MAY_3, 11, 0))
    assert early.status == "ok"
    assert early.kind == "mark"
    assert exits(env, "SEC_TRANSFER") == []
    assert any("outside the submit window" in n for n in early.notes)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side, run_id) == ("delisted", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "exit", MAY_3)
    assert quantity == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6
    # The batch held no plan decision: no rebalance event is written for it.
    assert [e for e in env.rebalance_events() if e[3] == outcome.run_id] == []


def test_an_untradable_delisted_name_is_closed_marked_and_re_evaluated(
    env: Env, window: PaperWindowRow
) -> None:
    """Untradable at the run: the `forced_exit` decision is closed at once by a
    `skipped` row with event reason `untradable`, nothing is ordered, the name
    is marked at its last close with `tradable` false, and the next session
    makes a new decision; once tradable again it is sold whole."""
    bought(env)
    ended_before(env, MAY_3)
    env.fake.set_asset("TRNS", UNTRADABLE)
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((closed, reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert reason == "delisted"
    assert env.query(
        "SELECT status, reason, run_id FROM decision_events WHERE decision_id = ?", [closed]
    ) == [("skipped", "untradable", first.run_id)]
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.alerts("skip_cap") == []

    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[3] for e in exits(env, "SEC_TRANSFER")] == [first.run_id, second.run_id]
    (close, mark, tradable) = env.query(
        "SELECT p.close, m.mark_price, m.tradable FROM positions_daily m "
        "JOIN prices_daily p ON p.security_id = m.security_id AND p.session = m.session "
        "WHERE m.security_id = 'SEC_TRANSFER' AND m.session = ?",
        [MAY_3],
    )[0]
    assert mark == pytest.approx(close)
    assert tradable is False
    assert exit_orders(env, "SEC_TRANSFER") == []

    env.fake.set_asset("TRNS", TRADABLE, from_session=MAY_7)
    third = env.run(at(MAY_7))
    assert third.status == "ok", env.result(env.latest_run())
    assert len(exits(env, "SEC_TRANSFER")) == 3
    ((_coid, decision_id, _phase, _quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (decision_id, session) == (exits(env, "SEC_TRANSFER")[-1][0], MAY_7)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_an_expired_exit_is_re_attempted_for_its_remainder(
    env: Env, window: PaperWindowRow
) -> None:
    """The exit's order expires unfilled: the decision stays open, the next
    in-window run re-attempts it (a new order on its own session, the same
    decision) and makes no second forced exit."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    env.fake.script(Expire())
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert env.held()["TRNS"] == pytest.approx(held)
    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [decision_id]
    orders = exit_orders(env, "SEC_TRANSFER")
    assert [(o[1], o[4]) for o in orders] == [(decision_id, MAY_3), (decision_id, MAY_6)]
    assert orders[1][3] == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_spinoff_receipt_is_sold_whole_by_the_next_in_window_run(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    assert window.window_id is not None
    received = at(date(2019, 5, 2), 22, 0)
    env.append(
        AdjustmentRow(
            window_id=window.window_id,
            session=date(2019, 5, 2),
            kind="spinoff_receipt",
            security_id="SEC_SPY",
            quantity=10.0,
            known_at=received,
            ingested_at=received,
        )
    )
    env.fake._net_quantity["SPY"] = env.fake._net_quantity.get("SPY", 0) + 10  # the spin-off
    early = env.run(at(MAY_3, 14, 30))
    assert early.status == "ok", env.result(env.latest_run())
    assert exits(env, "SEC_SPY") == []
    assert env.held()["SPY"] == pytest.approx(10.0)
    outcome = env.run(at(MAY_6))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_SPY")
    assert (reason, side, run_id) == ("untargeted_receipt", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, _session),) = exit_orders(env, "SEC_SPY")
    assert (ordered, phase, quantity) == (decision_id, "exit", pytest.approx(10.0))
    assert env.held().get("SPY", 0.0) < 1e-6


def test_a_plan_target_whose_listing_ended_is_journaled_skip_delisted(
    env: Env, window: PaperWindowRow
) -> None:
    """A target (not held) whose listing ended between close(T_0) and
    close(S-1) is `skip_delisted` on the catch-up: closed, never ordered, and
    no forced exit is made for it."""
    delist(env, "SEC_DUAL_B", at(F_0, 19, 0))
    outcome = env.run(at(F_0_PLUS_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.query(
        "SELECT decision, rebalance_session FROM decisions WHERE security_id = 'SEC_DUAL_B'"
    ) == [("skip_delisted", T_0)]
    assert exits(env, "SEC_DUAL_B") == []
    assert [o for o in env.orders() if o[1] == "SEC_DUAL_B"] == []


def test_on_a_fill_session_exits_join_the_sells_phase(env: Env, window: PaperWindowRow) -> None:
    """A held name whose listing ended at close(F_1 - 1): the F_1 run's forced
    exit is ordered in the rebalance's sells phase (phase `sell`), before its
    buys, in the same batch."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, F_1)
    outcome = env.run(at(F_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "rebalance"
    ((decision_id, reason, _side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, run_id) == ("delisted", outcome.run_id)
    ((coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "sell", F_1)
    assert quantity == pytest.approx(held)
    accepted = dict(
        env.query("SELECT client_order_id, known_at FROM order_events WHERE status = 'accepted'")
    )
    buys = [o[0] for o in env.orders() if o[5] == outcome.run_id and o[2] == "buy"]
    assert buys
    assert all(accepted[coid] <= accepted[buy] for buy in buys)
    assert env.held().get("TRNS", 0.0) < 1e-6

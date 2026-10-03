"""Crash, resume and alert suite (Phase 4 spec reqs 4, 5 and 8; plan T63h).

End to end on the fixture store with the scripted fake, built on
`test_run_trade.py`'s `Env` (a real window on a registered hypothesis) and
`test_run_stop.py`'s id helpers. The alert-kind suite is deferred to a
`T63j` by amendment (plan line, "Near the budget"): this file covers the
crash/resume mechanics, the transport-error case, the "Two phases"
criterion's resume half, the fill-lag bound, the chain criterion and the
cent-rounding tolerance.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from execution.test_run_stop import buy_id
from execution.test_run_trade import (
    F_0,
    F_0_PLUS_1,
    Env,
    at,
    bought,
    env,
    window,
)
from tradepartner.adapters.fake_broker import Reject, TransportFault
from tradepartner.execution import check as check_module
from tradepartner.execution import resume as resume_module
from tradepartner.execution import switch
from tradepartner.execution.resume import RELEASED
from tradepartner.store.db import open_read_only
from tradepartner.store.journal import (
    PaperWindowRow,
    kill_switch_events_for,
    runs_for,
)

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests

MAY_2, MAY_3, MAY_6 = date(2019, 5, 2), date(2019, 5, 3), date(2019, 5, 6)


class Crash(BaseException):
    """A process death: not an `Exception`, so no result row is written and
    the test can drop the process state (nothing further runs on this fake)."""


def _ticking(env: Env) -> object:
    """A clock that moves by a microsecond on every call, so `resume`'s
    release is stamped after the crashed run's close (the real clock does
    this on its own; `env.clock` is settable and otherwise frozen)."""

    def tick() -> datetime:
        env.clock.now += timedelta(microseconds=1)
        return env.clock.now

    return tick


def _resume(env: Env, *, accept_broker_fills: bool = False, accept_rejections: bool = False):
    return resume_module.resume(
        env.settings,
        env.connect,
        env.fake,
        _ticking(env),
        "owner checked after the crash",
        accept_broker_fills,
        accept_rejections=accept_rejections,
    )


def _engaged(env: Env, window: PaperWindowRow) -> bool:
    assert window.window_id is not None
    with open_read_only(env.settings) as conn:
        runs = runs_for(conn, window.window_id)
        rows = kill_switch_events_for(conn, window.window_id)
    state = switch.derive(
        window,
        rows,
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    return state.engaged


def _attempts(env: Env, security_id: str) -> list[tuple[str, int, str | None]]:
    """(client_order_id, attempt, last event status) for `security_id`'s buys,
    oldest first."""
    rows = env.query(
        "SELECT o.client_order_id, o.attempt FROM orders o "
        "WHERE o.security_id = ? AND o.side = 'buy' ORDER BY o.attempt",
        [security_id],
    )
    out = []
    for coid, attempt in rows:
        events = env.query(
            "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at",
            [coid],
        )
        out.append((coid, attempt, events[-1][0] if events else None))
    return out


def _live_orders_per_decision(env: Env) -> dict[int, int]:
    """How many non-terminal orders each decision currently has on the book."""
    terminal = {"filled", "expired", "rejected", "cancelled"}
    rows = env.query("SELECT client_order_id, decision_id FROM orders")
    live: dict[int, int] = {}
    for coid, decision_id in rows:
        statuses = {
            s
            for (s,) in env.query(
                "SELECT status FROM order_events WHERE client_order_id = ?", [coid]
            )
        }
        if not statuses & terminal:
            live[decision_id] = live.get(decision_id, 0) + 1
    return live


# --- the crash ------------------------------------------------------------------------------


def test_a_crash_midway_through_the_buys_phase_leaves_the_switch_engaged(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake raises after accepting some orders and the test drops the
    process state: the run row is `rebalance` with no result, the accepted
    orders' decisions are untouched, and the derived switch is engaged, so
    the next run submits nothing."""
    original_submit = env.fake.submit
    accepted = 0

    def die_on_second(request: object) -> object:
        nonlocal accepted
        if accepted >= 1:
            raise Crash
        accepted += 1
        return original_submit(request)  # type: ignore[arg-type]

    monkeypatch.setattr(env.fake, "submit", die_on_second)
    with pytest.raises(Crash):
        env.run(at(F_0))
    crashed = env.latest_run()
    assert env.query("SELECT count(*) FROM paper_run_results WHERE run_id = ?", [crashed]) == [(0,)]
    assert _engaged(env, window)

    monkeypatch.undo()
    nxt = env.run(at(F_0_PLUS_1))
    assert nxt.status == "skipped_kill_switch"
    assert len(env.submits()) == 1  # the crash's own attempt, nothing from this run


# --- resume settles pending, next run makes one new attempt per open decision ---------------


def test_resume_settles_the_pending_order_and_the_next_run_reattempts_only_it(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`paper resume` settles the crashed run's `pending` order (the fake
    filled it), reconciles and releases; the next in-window run makes a new
    attempt with a new id only for that decision, and the book never holds
    two live orders for it."""
    original_submit = env.fake.submit
    accepted: list[str] = []

    def die_on_third(request: object) -> object:
        if len(accepted) >= 2:
            raise Crash
        accepted.append(request.client_order_id)  # type: ignore[attr-defined]
        return original_submit(request)  # type: ignore[arg-type]

    monkeypatch.setattr(env.fake, "submit", die_on_third)
    with pytest.raises(Crash):
        env.run(at(F_0))
    monkeypatch.undo()
    # the fake fills the two accepted orders while the test is not looking,
    # so `resume` finds the crashed run's third order still open or pending.
    for coid in list(env.fake._orders):
        if env.fake.get_order(coid).status.value == "accepted":
            env.fake.simulate_fill(coid)

    outcome = _resume(env, accept_broker_fills=True)
    assert outcome.status == RELEASED, outcome.reasons
    assert not _engaged(env, window)

    nxt = env.run(at(MAY_2))
    assert nxt.status == "ok", env.result(env.latest_run())
    # every target is now held, the crashed decision's remainder re-attempted once
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    for security_id in ("SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER"):
        attempts = _attempts(env, security_id)
        assert len(attempts) <= 2
    assert all(count <= 1 for count in _live_orders_per_decision(env).values())


# --- the transport error: never received ----------------------------------------------------


def test_a_submit_transport_error_never_received_is_settled_by_resume(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `submit` that raises before the fake records the order halts the run
    with that order `pending` and untouched by the halt's cancels; `paper
    resume` settles it `not_received`, and the next in-window run makes a new
    attempt."""
    env.fake.script(TransportFault(), client_order_id=buy_id(env, F_0, "SEC_TRANSFER"))
    with pytest.raises(Exception, match="transport error"):
        env.run(at(F_0))
    halted = env.latest_run()
    assert env.result(halted)[0] == "halted"
    coid = buy_id(env, F_0, "SEC_TRANSFER")
    last = env.query(
        "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at", [coid]
    )
    assert [s for (s,) in last] == ["pending"]
    assert _engaged(env, window)

    released = _resume(env)
    assert released.status == RELEASED, released.reasons
    last = env.query(
        "SELECT status, reason FROM order_events WHERE client_order_id = ? ORDER BY known_at",
        [coid],
    )
    assert last[-1] == ("cancelled", "not_received")

    nxt = env.run(at(MAY_2))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    attempts = _attempts(env, "SEC_TRANSFER")
    assert len(attempts) == 2
    assert attempts[0][0] == coid


# --- the "Two phases" criterion's resume half -------------------------------------------------


def test_a_buy_terminal_inside_resume_is_written_off_by_the_next_runs_step_3(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buy rejected while its run is stuck `pending` becomes terminal
    inside `paper resume`; the `written_off` row is only written by the next
    run's step 3, state and rows agree, and the resume writes no
    `decision_events` or `rebalance_events` row."""
    original_submit = env.fake.submit
    accepted: list[str] = []

    def die_on_third(request: object) -> object:
        if len(accepted) >= 2:
            raise Crash
        accepted.append(request.client_order_id)  # type: ignore[attr-defined]
        return original_submit(request)  # type: ignore[arg-type]

    monkeypatch.setattr(env.fake, "submit", die_on_third)
    with pytest.raises(Crash):
        env.run(at(F_0))
    monkeypatch.undo()
    rejected_coid, filled_coid = accepted
    # `filled_coid` fills normally (so its decision settles by a real fill);
    # `rejected_coid` the broker rejects while the run is down: it becomes
    # terminal inside `paper resume` itself, not through the crash's halt.
    env.fake.simulate_fill(filled_coid)
    env.fake.apply(rejected_coid, Reject())
    decision_id = env.query(
        "SELECT decision_id FROM orders WHERE client_order_id = ?", [rejected_coid]
    )[0][0]
    before = (
        env.count("decision_events"),
        env.count("rebalance_events"),
    )

    outcome = _resume(env)
    assert outcome.status == RELEASED, outcome.reasons
    after = (env.count("decision_events"), env.count("rebalance_events"))
    assert after == before  # resume writes neither
    assert env.query(
        "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at",
        [rejected_coid],
    )[-1] == ("rejected",)

    assert (
        env.query(
            "SELECT status FROM decision_events WHERE decision_id = ? ORDER BY known_at",
            [decision_id],
        )
        == []
    )  # still open: the write-off has not happened yet

    nxt = env.run(at(MAY_2))
    assert nxt.status == "ok", env.result(env.latest_run())
    statuses = [
        s
        for (s,) in env.query(
            "SELECT status FROM decision_events WHERE decision_id = ? ORDER BY known_at",
            [decision_id],
        )
    ]
    assert statuses[-1] == "written_off"
    writers = {
        r
        for (r,) in env.query(
            "SELECT run_id FROM decision_events WHERE decision_id = ?", [decision_id]
        )
    }
    assert writers == {nxt.run_id}


# --- the fill-lag bound end to end ------------------------------------------------------------


def test_the_fill_lag_bound_halts_and_accept_broker_fills_completes_it(
    env: Env, window: PaperWindowRow
) -> None:
    """A fill the fake never delivers to `fills()` blocks the next fill
    session's plan; the first collection at or after
    `risk.max_fill_lag_sessions` sessions halts with `ReconciliationError`;
    `paper resume --accept-broker-fills` completes it and the position is
    counted once by every reader."""
    env.fill_on_sleep = False
    coid = buy_id(env, F_0, "SEC_TRANSFER")
    env.fake.lag_fills(None)  # every fill from here on is hidden from fills()

    def fill_trns(now: datetime) -> None:
        for order in env.fake.open_orders():
            if order.client_order_id == coid:
                env.fake.simulate_fill(order.client_order_id)
        for order in env.fake.open_orders():
            if order.client_order_id != coid:
                env.fake.simulate_fill(order.client_order_id)

    env.on_sleep.append(fill_trns)
    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    assert sorted(o for o in env.held() if o != "TRNS") == ["DUALB", "SPFT"]
    with pytest.raises(Exception, match=r"ReconciliationError|fills_lagging"):
        env.run(at(MAY_2))
    halted = env.latest_run()
    status, fault, _ = env.result(halted)
    assert status == "halted" and fault == "ReconciliationError"

    outcome = _resume(env, accept_broker_fills=True)
    assert outcome.status == RELEASED, outcome.reasons
    nxt = env.run(at(MAY_3))
    assert nxt.status == "ok", env.result(env.latest_run())
    quantity = env.held().get("TRNS", 0.0)
    assert quantity > 0.0
    # counted once: the fake's own net position matches the journaled fills' sum
    fills = env.query(
        "SELECT sum(f.quantity) FROM fills f JOIN orders o USING (client_order_id) "
        "WHERE o.security_id = 'SEC_TRANSFER' AND o.side = 'buy'"
    )[0][0]
    assert fills == pytest.approx(quantity)


# --- the chain criterion ----------------------------------------------------------------------


def _check_frozen_json(window: PaperWindowRow) -> str:
    """The window's `frozen_json` with the two extra keys `check.check` needs
    for its other lines (not the chain line itself) so `check()` does not
    raise before building it."""
    values = json.loads(window.frozen_json)
    values["paper.min_rebalances"] = 1
    values["paper.min_override_reason_chars"] = 1
    return json.dumps(values)


def test_the_chain_query_is_zero_once_every_outcome_is_due_then_a_deleted_event_reopens_it(
    env: Env, window: PaperWindowRow
) -> None:
    """Every buy fills whole: once its horizon is due, `paper check`'s chain
    query returns zero incomplete chains, each outcome row carries the req 8
    `position_return` kind for a filled buy, and a deliberately deleted
    terminal event makes the query return that order again."""
    assert window.window_id is not None
    with env.connect() as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [_check_frozen_json(window), window.window_id],
        )

    bought(env)  # DUALB, SPFT, TRNS filled in full (position_return due)

    nxt = env.run(at(date(2019, 6, 3)))  # F_1 + a catch-up window well past
    assert nxt.status in ("ok", "skipped_kill_switch")

    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert chain.passed, chain.detail

    coid = buy_id(env, F_0, "SEC_DUAL_B")
    assert env.query("SELECT kind FROM outcomes WHERE client_order_id = ?", [coid]) == [
        ("position_return",)
    ]
    with env.connect() as conn:
        conn.execute(
            "DELETE FROM order_events WHERE client_order_id = ? AND status = 'filled'", [coid]
        )
    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert not chain.passed
    assert coid in chain.detail


# --- cent rounding stays within the reconcile tolerance -------------------------------------


def test_a_cent_rounding_fake_stays_within_the_reconcile_cash_tolerance(
    env: Env, window: PaperWindowRow
) -> None:
    """A fake that rounds each fill to the cent (`round_cash_to_cent=True`)
    stays within `risk.reconcile_cash_tolerance` over the window's frozen
    `paper.min_rebalances` months of fills."""
    env.new_fake(round_cash_to_cent=True)
    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    second = env.run(at(date(2019, 6, 3)))  # the next month's catch-up/rebalance
    assert second.status == "ok", env.result(env.latest_run())
    rows = env.query("SELECT status FROM reconciliations ORDER BY reconciliation_id")
    assert all(status == "ok" for (status,) in rows)

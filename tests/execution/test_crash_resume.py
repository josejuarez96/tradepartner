"""Crash, resume and alert suite (Phase 4 spec reqs 4, 5, 8 and 11; plan T63h).

End to end on the fixture store with the scripted fake, built on
`test_run_trade.py`'s `Env` (a real window on a registered hypothesis),
`test_run_core.py`'s lighter `Env` (a mark-only window, no hypothesis), the
alert fakes from `test_alerts.py`, and `test_run_stop.py`'s id helpers. This
file covers the crash/resume mechanics, the transport-error case, the "Two
phases" criterion's resume half, the fill-lag bound, the chain criterion, the
cent-rounding tolerance, and the alert-kind suite (spec req 11): eleven kinds
with a real trigger, four (`kill_switch`, `reconciliation`, `rejection_cap`,
`skip_cap`) `xfail`ed because no production path writes them yet (#644).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from execution.test_alerts import FakeRunner, FakeSMTP
from execution.test_run_core import (
    FAKE_CASH as CORE_FAKE_CASH,
)
from execution.test_run_core import (
    MON,
    PRICE,
    SPY,
    TUE,
    WED,
)
from execution.test_run_core import Clock as CoreClock
from execution.test_run_core import Env as CoreEnv
from execution.test_run_core import _at as core_at
from execution.test_run_core import _marked as core_marked
from execution.test_run_stop import buy_id
from execution.test_run_trade import (
    F_0,
    F_0_PLUS_1,
    FROZEN,
    TARGETS,
    Clock,
    Env,
    at,
    env,
    window,
)
from tradepartner.adapters.broker import Asset, OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker, Reject, TransportFault
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ReconciliationError, RejectionCapError, SkipCapError, StaleDataError
from tradepartner.execution import alerts as alerts_module
from tradepartner.execution import check as check_module
from tradepartner.execution import resume as resume_module
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.outcomes import NOT_EXECUTED, POSITION_RETURN
from tradepartner.execution.resume import RELEASED
from tradepartner.execution.window import stop as window_stop
from tradepartner.execution.wrapper import WRITE_FAILED_EXIT_CODE
from tradepartner.store.db import open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    PaperWindowRow,
    fills_for,
    kill_switch_events_for,
    runs_for,
)

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests

_ALERT_SECRETS = {
    "alert_smtp_host": "smtp.example.test:587",
    "alert_smtp_user": "owner-login@example.test",
    "alert_smtp_password": "hunter2-very-secret",
    "alert_email_to": "owner-inbox@example.test",
}

MAY_2, MAY_3, MAY_6 = date(2019, 5, 2), date(2019, 5, 3), date(2019, 5, 6)


# --- alert-kind suite helpers -----------------------------------------------------------------


@pytest.fixture
def alert_fakes(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """Wires the real `Alerter`'s default `runner`/`smtp` (the ones every
    production call site builds it with: `_ChunkAlerter(settings, connect,
    clock)`, never overridden) to the `test_alerts.py` fakes, for the
    duration of one test."""
    runner = FakeRunner()
    monkeypatch.setattr(
        alerts_module.Alerter.__init__, "__kwdefaults__", {"runner": runner, "smtp": FakeSMTP}
    )
    FakeSMTP.instances = []
    FakeSMTP.fail_login_with = None
    FakeSMTP.fail_quit_with = None
    return runner


def _alerting_settings(store_path: Path, **overrides: object) -> Settings:
    """A `Settings` with every alert channel live (`store`, `macos`, `email`)
    and the email secrets set, so a kind's delivery exercises all three."""
    base: dict[str, object] = {
        "_env_file": None,
        "store": {"path": str(store_path)},
        "alerts": {"channels": ["store", "macos", "email"]},
        **_ALERT_SECRETS,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _assert_delivered(runner: FakeRunner, message_fragment: str) -> None:
    """Both non-store fakes (`alert_fakes`) received a message naming
    `message_fragment`: one `osascript` call and one SMTP send."""
    assert any(message_fragment in call[2] for call in runner.calls if len(call) > 2)
    assert FakeSMTP.instances, "no SMTP client was built: the email channel was not tried"
    assert any(
        message_fragment in m.get_content() for smtp in FakeSMTP.instances for m in smtp.sent
    )


def _core_env(fixture_store_path: Path) -> CoreEnv:
    """`test_run_core.py`'s `Env` on alert-capable settings (its own `env`
    fixture uses `journal_settings`, which carries no `alerts` section)."""
    settings = _alerting_settings(fixture_store_path)
    clock = CoreClock(core_at(TUE))
    fake = FakeBroker(
        clock=clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        cash=CORE_FAKE_CASH,
        account_id="PA1",
    )
    return CoreEnv(settings, clock, fake)


def _alerting_env(fixture_store_path: Path) -> Env:
    """`test_run_trade.py`'s `Env` (the full rebalance window) on
    alert-capable settings."""
    settings = _alerting_settings(
        fixture_store_path, alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48}
    )
    return Env(settings, Clock(at(F_0)))


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


def _guard_one_live_order_per_decision(env: Env) -> list[str]:
    """Installs `env.fake.on_submit` to assert, at every submit, that no
    non-terminal order already on the fake's book shares the request's
    decision (the fake's own book, not the journal's end state, so a
    regression that puts two live orders on it fails here, not after both
    happen to fill). Returns the list of client_order_ids seen, so the test
    can assert the guard actually fired."""
    seen: list[str] = []

    def guard(request: object) -> None:
        coid = request.client_order_id  # type: ignore[attr-defined]
        # The wrapper journals every decision's `orders` row and `pending`
        # event for the whole batch before its first submit, so this row
        # already exists.
        decision_id = env.query("SELECT decision_id FROM orders WHERE client_order_id = ?", [coid])[
            0
        ][0]
        open_ids = {o.client_order_id for o in env.fake.open_orders()}
        siblings = [
            c
            for (c,) in env.query(
                "SELECT client_order_id FROM orders WHERE decision_id = ? AND client_order_id != ?",
                [decision_id, coid],
            )
            if c in open_ids
        ]
        assert siblings == [], f"decision {decision_id} already has a live order: {siblings}"
        seen.append(coid)

    env.fake.on_submit = guard
    return seen


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
    assert env.count("decision_events") == 0  # the accepted order's decision is untouched
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
    never received it, so resume cancels it `not_received`), reconciles and
    releases; the next in-window run makes a new attempt with a new id only
    for that decision, and the fake's book never holds two live orders for
    it."""
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
    accepted_securities = {
        env.query("SELECT security_id FROM orders WHERE client_order_id = ?", [c])[0][0]
        for c in accepted
    }
    (crashed_security,) = set(TARGETS) - accepted_securities
    first_attempt_quantity = env.query(
        "SELECT quantity FROM orders WHERE security_id = ? AND side = 'buy'", [crashed_security]
    )[0][0]
    # the fake fills the two accepted orders while the test is not looking,
    # so `resume` finds the crashed run's third order still open or pending.
    for coid in list(env.fake._orders):
        if env.fake.get_order(coid).status.value == "accepted":
            env.fake.simulate_fill(coid)

    outcome = _resume(env, accept_broker_fills=True)
    assert outcome.status == RELEASED, outcome.reasons
    assert not _engaged(env, window)

    seen = _guard_one_live_order_per_decision(env)
    nxt = env.run(at(MAY_2))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert seen  # the guard actually fired, so the check is not vacuous
    # every target is now held, and the crashed decision got exactly one new
    # attempt, for its full remainder (nothing of it had filled before).
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    for security_id in TARGETS:
        attempts = _attempts(env, security_id)
        if security_id == crashed_security:
            # a new order (new client_order_id, the catch-up session's own
            # attempt 1) for the same decision, its full remainder (nothing
            # of the first, never-received attempt had filled).
            assert len(attempts) == 2
            assert attempts[0][0] != attempts[1][0]
            decisions = {
                env.query("SELECT decision_id FROM orders WHERE client_order_id = ?", [c])[0][0]
                for c, _, _ in attempts
            }
            assert len(decisions) == 1
            second_quantity = env.query(
                "SELECT quantity FROM orders WHERE client_order_id = ?", [attempts[1][0]]
            )[0][0]
            assert second_quantity == pytest.approx(first_attempt_quantity)
        else:
            assert len(attempts) == 1


# --- the transport error: never received ----------------------------------------------------


def test_a_submit_transport_error_never_received_is_settled_by_resume(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `submit` that raises before the fake records the order halts the run
    with that order `pending` and untouched by the halt's cancels: TRNS is
    submitted last in this batch, so the fault leaves it `pending`, while
    DUALB and SPFT, already `accepted`, are cancelled by the halt's own
    best-effort cancel (reason `halt`) — a different path from TRNS's.
    `paper resume` settles TRNS `not_received`; the next in-window run makes
    a new attempt for every one of the three, since none of them filled."""
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
    for other in (buy_id(env, F_0, "SEC_DUAL_B"), buy_id(env, F_0, "SEC_SPLIT_FUTURE")):
        statuses = [
            s
            for (s,) in env.query(
                "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at",
                [other],
            )
        ]
        assert statuses[1] == "accepted", "the other two were submitted before TRNS's fault"
        assert "cancel_requested" in statuses and statuses[-1] == "cancelled"
    assert _engaged(env, window)

    released = _resume(env)
    assert released.status == RELEASED, released.reasons
    last = env.query(
        "SELECT status, reason FROM order_events WHERE client_order_id = ? ORDER BY known_at",
        [coid],
    )
    assert last[-1] == ("cancelled", "not_received")

    seen = _guard_one_live_order_per_decision(env)
    nxt = env.run(at(MAY_2))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert seen
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    for security_id in TARGETS:
        attempts = _attempts(env, security_id)
        assert len(attempts) == 2
        assert attempts[-1][2] == "filled"
    attempts = _attempts(env, "SEC_TRANSFER")
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
    # state and rows agree: a written-off decision gets no further order (not
    # a duplicate buy), and the journal shows exactly the one rejected order.
    orders_of_decision = env.query(
        "SELECT client_order_id FROM orders WHERE decision_id = ?", [decision_id]
    )
    assert [c for (c,) in orders_of_decision] == [rejected_coid]
    rejected_security = env.query(
        "SELECT security_id FROM orders WHERE client_order_id = ?", [rejected_coid]
    )[0][0]
    assert rejected_security not in env.held()


# --- the fill-lag bound end to end ------------------------------------------------------------


def test_the_fill_lag_bound_halts_and_accept_broker_fills_completes_it(
    env: Env, tmp_path: Path
) -> None:
    """A fill the fake never delivers to `fills()` blocks the next fill
    session's plan (`risk.max_fill_lag_sessions` = 2 here, so a run between
    first listing it lagging and the bound is told apart from the halting
    one); the first collection at or after the bound halts with
    `ReconciliationError`, with no submit made; `paper resume
    --accept-broker-fills` completes it with a synthetic fill, and when the
    real fill the fake was holding back finally surfaces, it is journaled
    `superseded_by` that synthetic one, so every reader (the fake's own net
    position and the journal's live, non-superseded fills) counts the
    position once."""
    env.open_window(
        frozen=FROZEN.model_copy(update={"max_fill_lag_sessions": 2}), tmp_path=tmp_path
    )
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
    submitted_before = len(env.submits())

    # One session lagging (first listed): blocked, not yet halted.
    blocked = env.run(at(MAY_2))
    assert blocked.status == "ok", env.result(env.latest_run())
    assert env.query(
        "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
        [blocked.run_id],
    ) == [("fills_lagging",), ("fills_lagging",)]
    assert len(env.submits()) == submitted_before  # no new plan while lagging

    # A second session lagging reaches the bound: halts, no submit made.
    with pytest.raises(Exception, match=r"ReconciliationError|fills_lagging"):
        env.run(at(MAY_3))
    halted = env.latest_run()
    status, fault, _ = env.result(halted)
    assert status == "halted" and fault == "ReconciliationError"
    assert len(env.submits()) == submitted_before

    outcome = _resume(env, accept_broker_fills=True)
    assert outcome.status == RELEASED, outcome.reasons
    synthetic = env.query(
        "SELECT fill_id, quantity FROM fills "
        "WHERE client_order_id = ? AND source = 'broker_status'",
        [coid],
    )
    assert len(synthetic) == 1
    synthetic_fill_id, synthetic_quantity = synthetic[0]

    # The real fill the fake was holding back now surfaces: it must be
    # superseded by the synthetic one, counted once, not twice.
    env.fake._fill_hidden_reads = [0 for _ in env.fake._fill_hidden_reads]
    nxt = env.run(at(date(2019, 5, 6)))
    assert nxt.status == "ok", env.result(env.latest_run())
    real = env.query(
        "SELECT fill_id, quantity, superseded_by FROM fills "
        "WHERE client_order_id = ? AND source = 'broker_feed'",
        [coid],
    )
    assert len(real) == 1
    _real_fill_id, real_quantity, superseded_by = real[0]
    assert superseded_by == synthetic_fill_id

    quantity = env.held().get("TRNS", 0.0)
    assert quantity == pytest.approx(synthetic_quantity)
    assert quantity != pytest.approx(synthetic_quantity + real_quantity)  # not double counted
    with open_read_only(env.settings) as conn:
        live = fills_for(conn, client_order_ids=[coid])
    assert sum(f.fill.quantity for f in live) == pytest.approx(quantity)  # the reader agrees


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
    """DUALB's F_0 buy is rejected (no fill), SPFT's expires outright (no
    fill), TRNS's fills whole: once each one's horizon is due, `paper
    check`'s chain query returns zero incomplete chains, each outcome row
    carries the req 8 kind that fits its terminal state and side, and a
    deliberately deleted terminal event makes the query return that order
    again."""
    assert window.window_id is not None
    with env.connect() as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [_check_frozen_json(window), window.window_id],
        )

    dualb_coid = buy_id(env, F_0, "SEC_DUAL_B")
    spft_coid = buy_id(env, F_0, "SEC_SPLIT_FUTURE")
    trns_coid = buy_id(env, F_0, "SEC_TRANSFER")
    env.fake.script(Reject(), client_order_id=dualb_coid)
    env.fake.script(Expire(), client_order_id=spft_coid)

    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    assert env.held().get("TRNS", 0.0) > 0.0
    assert "DUALB" not in env.held() and "SPFT" not in env.held()

    nxt = env.run(at(date(2019, 6, 3)))  # well past T_0's due threshold (T_1)
    assert nxt.status == "ok", env.result(env.latest_run())

    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert chain.passed, chain.detail

    def kinds_of(coid: str) -> set[str]:
        return {
            k for (k,) in env.query("SELECT kind FROM outcomes WHERE client_order_id = ?", [coid])
        }

    assert kinds_of(dualb_coid) == {NOT_EXECUTED}  # rejected, zero fill
    assert kinds_of(spft_coid) == {NOT_EXECUTED}  # expired, zero fill
    assert kinds_of(trns_coid) == {POSITION_RETURN}  # filled whole

    with env.connect() as conn:
        conn.execute(
            "DELETE FROM order_events WHERE client_order_id = ? AND status = 'rejected'",
            [dualb_coid],
        )
    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert not chain.passed
    assert dualb_coid in chain.detail


# --- cent rounding stays within the reconcile tolerance -------------------------------------


def test_a_cent_rounding_fake_stays_within_the_reconcile_cash_tolerance(
    env: Env, window: PaperWindowRow
) -> None:
    """A fake that rounds each fill to the cent (`round_cash_to_cent=True`)
    stays within `risk.reconcile_cash_tolerance` over the fixed 3-name
    universe's F_0 rebalance and its next month's catch-up, with the
    rounding actually exercised (at least one fill not an exact cent
    itself). Running the full `paper.min_rebalances` (6) span is not done
    here: `Env.price_at` only prices the fixed `SYMBOLS` set (module
    docstring), and a later month's momentum universe can pick a name
    outside it (observed: `SEC_SPLIT_BETWEEN`/SPBT), which needs a broader
    `Env` than this file builds; flagged as a follow-up, not fixed here."""
    env.new_fake(round_cash_to_cent=True)

    for session in (F_0, date(2019, 6, 3)):
        outcome = env.run(at(session))
        assert outcome.status == "ok", env.result(env.latest_run())

    rows = env.query("SELECT status FROM reconciliations ORDER BY reconciliation_id")
    assert len(rows) >= 2  # not a vacuous pass on an empty table
    assert all(status == "ok" for (status,) in rows)

    fills = env.query("SELECT price, quantity FROM fills")
    assert fills  # fills were actually made
    assert any(
        Decimal(repr(price)) * Decimal(repr(quantity))
        != (Decimal(repr(price)) * Decimal(repr(quantity))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for price, quantity in fills
    )  # at least one fill's exact notional needed rounding to the cent


# =============================================================================================
# The alert-kind suite (spec req 11).
# =============================================================================================


# --- kinds written before the exception propagates (the halt and failure paths) --------------


def test_halted_alert_row_exists_before_the_exception_propagates(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    alerting_env.fake.script(
        TransportFault(), client_order_id=buy_id(alerting_env, F_0, "SEC_TRANSFER")
    )
    with pytest.raises(Exception, match="transport error"):
        alerting_env.run(at(F_0))
    rows = alerting_env.alerts("halted")
    assert len(rows) == 1
    _assert_delivered(alert_fakes, "transport error")


def test_stale_data_alert_row_exists_before_the_exception_propagates(
    alert_fakes: FakeRunner, fixture_store_path: Path
) -> None:
    core_env = _core_env(fixture_store_path)
    core_env.window()
    core_env.ingest(core_at(TUE, 14))  # after the run's own clock reading: no look-ahead
    with pytest.raises(StaleDataError):
        core_env.run(core_at(TUE))
    assert ("stale_data", core_env.latest_run(), TUE) in core_env.alerts()
    _assert_delivered(alert_fakes, "StaleDataError")


def test_run_failed_and_missed_run_alert_rows_exist_before_the_exception_propagates(
    alert_fakes: FakeRunner, fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core_env = _core_env(fixture_store_path)
    core_env.window()
    core_env.ingest(core_at(MON, 21))

    def broken(_context: object) -> None:
        raise RuntimeError("the step failed")

    monkeypatch.setattr(run_module, "exits_step", broken)
    monkeypatch.setattr(run_module, "trade_step", broken)

    with pytest.raises(RuntimeError):
        core_env.run(core_at(TUE))
    run_id = core_env.latest_run()
    assert ("run_failed", run_id, TUE) in core_env.alerts()
    assert ("missed_run", run_id, TUE) in core_env.alerts()
    _assert_delivered(alert_fakes, "RuntimeError")


def test_kill_switch_write_failed_writes_no_store_row_and_calls_the_non_store_fakes(
    alert_fakes: FakeRunner,
    fixture_store_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The halt's own `engage()` write fails: the `deliver_without_store`
    path, so no `alerts` row (the store is what failed), and the non-store
    fakes are still called."""
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    alerting_env.fake.script(
        TransportFault(), client_order_id=buy_id(alerting_env, F_0, "SEC_TRANSFER")
    )

    def raise_write_error(*_args: object, **_kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(switch, "_append", raise_write_error)
    with pytest.raises(SystemExit) as excinfo:
        alerting_env.run(at(F_0))
    assert excinfo.value.code == WRITE_FAILED_EXIT_CODE
    assert alerting_env.count("alerts") == 0
    _assert_delivered(alert_fakes, "could not be written")


# --- kinds that exist when the command returns ------------------------------------------------


def test_missed_rebalance_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    later = alerting_env.run(at(MAY_6))  # no F_0 run: past max_catch_up_sessions, lapsed
    assert later.status == "ok", alerting_env.result(alerting_env.latest_run())
    ((run_id, session, _message),) = alerting_env.alerts("missed_rebalance")
    assert (run_id, session) == (later.run_id, MAY_6)
    _assert_delivered(alert_fakes, "catch_up_lapsed")


def test_drawdown_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner, fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors `test_run_core.py`'s own drawdown test: no run on Tuesday,
    Wednesday's run marks Monday and Tuesday, and Monday's ledger equity is
    below the peak by more than `risk.max_drawdown`."""
    core_env = _core_env(fixture_store_path)
    monkeypatch.setattr(run_module, "exits_step", lambda _context: None)
    shares = 1000.0
    cash = CORE_FAKE_CASH
    closes = dict(
        core_env.query(
            "SELECT session, close FROM prices_daily WHERE security_id = ? AND session IN (?, ?)",
            [SPY, MON, TUE],
        )
    )
    line = cash + shares * (closes[MON] + closes[TUE]) / 2
    peak = line / (1 - RiskConfig().max_drawdown)
    window = core_env.window(started=core_at(MON, 22), starting_equity=peak)
    core_env.ingest(core_at(TUE, 21))
    core_env.fake = FakeBroker(
        clock=core_env.clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        cash=cash + shares * PRICE,
        account_id="PA1",
    )
    core_env.clock.now = core_at(MON)
    core_env.fake.submit(OrderRequest("seed-1", "SPY", Side.BUY, quantity=shares))
    core_env.fake.simulate_fill("seed-1")
    core_env.append(
        AdjustmentRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=MON,
            kind="spinoff_receipt",
            security_id=SPY,
            quantity=shares,
            known_at=core_at(MON, 21),
            ingested_at=core_at(MON, 21),
        )
    )
    outcome = core_env.run(core_at(WED))
    assert outcome.status == "skipped_kill_switch", core_env.results()
    assert ("drawdown", outcome.run_id, WED) in core_env.alerts()
    _assert_delivered(alert_fakes, "max_drawdown")


def test_unspent_cash_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    alerting_env.fake.set_asset(
        "SPFT", Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    outcome = alerting_env.run(at(F_0))
    assert outcome.status == "ok", alerting_env.result(alerting_env.latest_run())
    ((run_id, session, _message),) = alerting_env.alerts("unspent_cash")
    assert (run_id, session) == (outcome.run_id, F_0)
    _assert_delivered(alert_fakes, "max_unspent_cash_fraction")


def test_locked_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner, fixture_store_path: Path
) -> None:
    core_env = _core_env(fixture_store_path)
    core_marked(core_env)
    with run_lock(core_env.settings):
        outcome = core_env.run(core_at(TUE))
    assert outcome.status == "locked"
    assert core_env.alerts() == [("locked", None, TUE)]
    _assert_delivered(alert_fakes, "paper run not started")


def test_no_window_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner, fixture_store_path: Path
) -> None:
    core_env = _core_env(fixture_store_path)
    outcome = core_env.run(core_at(MON, 15))
    assert outcome.status == "no_window"
    assert ("no_window", None, MON) in core_env.alerts()
    _assert_delivered(alert_fakes, "no paper window is open")


# --- paper stop refused while a run holds the lock --------------------------------------------


def test_paper_stop_is_refused_while_a_run_holds_the_lock(fixture_store_path: Path) -> None:
    core_env = _core_env(fixture_store_path)
    core_marked(core_env)
    with run_lock(core_env.settings), pytest.raises(LockHeld):
        window_stop(
            core_env.settings, core_env.connect, core_env.fake, core_env.clock, "owner checked"
        )


# --- the four kinds with no production writer yet (#644) --------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="#644: wrapper.halt() writes 'halted' for every non-stale SystemFaultError, "
    "including a kill-switch engagement; no path writes a distinct 'kill_switch' alert row",
)
def test_kill_switch_alert_kind_is_not_yet_written(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    """The trigger picked (#644): the halt path's own `engage()` call, the
    only kill-switch engagement every halt makes (spec req 4: 'appends the
    kill_switch engaged row ... writes the alert (req 11)')."""
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    alerting_env.fake.script(
        TransportFault(), client_order_id=buy_id(alerting_env, F_0, "SEC_TRANSFER")
    )
    with pytest.raises(Exception, match="transport error"):
        alerting_env.run(at(F_0))
    assert alerting_env.alerts("kill_switch") != []


@pytest.mark.xfail(
    strict=True,
    reason="#644: a ReconciliationError halt writes 'halted', never a distinct "
    "'reconciliation' alert row",
)
def test_reconciliation_alert_kind_is_not_yet_written(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    bought_outcome = alerting_env.run(at(F_0))
    assert bought_outcome.status == "ok"
    alerting_env.fake.submit(OrderRequest("owner-1", "DUALB", Side.BUY, quantity=1.0))
    alerting_env.fake.simulate_fill("owner-1")  # a position the ledger lacks
    with pytest.raises(ReconciliationError):
        alerting_env.run(at(MAY_2))
    assert alerting_env.alerts("reconciliation") != []


@pytest.mark.xfail(
    strict=True,
    reason="#644: a RejectionCapError halt writes 'halted', never a distinct "
    "'rejection_cap' alert row",
)
def test_rejection_cap_alert_kind_is_not_yet_written(
    alert_fakes: FakeRunner, fixture_store_path: Path
) -> None:
    core_env = _core_env(fixture_store_path)
    window = core_marked(core_env, RiskConfig(max_rejections_per_run=1))
    earlier = core_env.past_run(window, core_at(MON))
    for coid in ("tp-a", "tp-b"):
        core_env.order(earlier, coid, core_at(MON))
        core_env.fake.apply(coid, Reject())
    with pytest.raises(RejectionCapError):
        core_env.run(core_at(TUE))
    assert any(kind == "rejection_cap" for kind, _run_id, _session in core_env.alerts())


@pytest.mark.xfail(
    strict=True,
    reason="#644: a SkipCapError halt writes 'halted', never a distinct 'skip_cap' alert row",
)
def test_skip_cap_alert_kind_is_not_yet_written(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(
        frozen=FROZEN.model_copy(update={"max_skips_per_run": 0}), tmp_path=tmp_path
    )
    alerting_env.fake.set_asset(
        "DUALB", Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    with pytest.raises(SkipCapError):
        alerting_env.run(at(F_0))
    assert alerting_env.alerts("skip_cap") != []

"""Crash, resume and alert suite (Phase 4 spec reqs 4, 5, 8 and 11; plan T63h).

End to end on the fixture store with the scripted fake, built on
`test_run_trade.py`'s `Env` (a real window on a registered hypothesis),
`test_run_core.py`'s lighter `Env` (a mark-only window, no hypothesis), the
alert fakes from `test_alerts.py`, and `test_run_stop.py`'s id helpers. This
file covers the crash/resume mechanics, the transport-error case, the "Two
phases" criterion's resume half, the fill-lag bound, the chain criterion, the
cent-rounding tolerance, and the alert-kind suite (spec req 11): eleven of
the plan line's fifteen kinds with a real trigger (`lot_ledger` through the
run's own lot-ledger error path); `reconciliation`, `rejection_cap` and
`skip_cap` became real once #667 fixed #644, and a halt's own engagement
writes one `halted` alert and no `kill_switch` alert (#741, owner decision
(a): `kill_switch` alerts belong to skipped_kill_switch runs, #677/#698).
`paper resume` releasing while the window holds a marked position
(#653, found while building the chain criterion) is fixed by #664 and tested
here. (#650, found the same way,
was regraded to low-priority hardening reproducible only with a frozen test
clock, and is not reproduced as a test here.)
"""

from __future__ import annotations

import json
from dataclasses import replace
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
from execution.test_run_stop import buy_id, request_stop, sell_id
from execution.test_run_trade import (
    F_0,
    F_0_PLUS_1,
    FROZEN,
    T_0,
    TARGETS,
    Clock,
    Env,
    at,
    bought,
    env,
    window,
)
from tradepartner.adapters.broker import Asset, OrderRequest, Side, UnknownOrderError
from tradepartner.adapters.fake_broker import (
    Expire,
    FakeBroker,
    PartialFill,
    Reject,
    TransportFault,
    Vanish,
)
from tradepartner.calendar import (
    last_session_of_month,
    next_session,
    previous_session,
    session_close,
)
from tradepartner.config import PaperConfig, RiskConfig, Settings
from tradepartner.errors import (
    AcknowledgementTimeoutError,
    ClockError,
    ReconciliationError,
    RejectionCapError,
    SkipCapError,
    StaleDataError,
)
from tradepartner.execution import alerts as alerts_module
from tradepartner.execution import check as check_module
from tradepartner.execution import resume as resume_module
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.outcomes import NOT_EXECUTED, POSITION_RETURN, REALISED_PNL
from tradepartner.execution.reconcile_run import reconcile_command
from tradepartner.execution.reserve import open_buy_reserve
from tradepartner.execution.resume import RELEASED
from tradepartner.execution.window import stop as window_stop
from tradepartner.execution.wrapper import WRITE_FAILED_EXIT_CODE
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    OverrideRow,
    PaperWindowRow,
    fills_for,
    kill_switch_events_for,
    order_events_for,
    orders_for,
    runs_for,
)

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`test_run_trade.py`'s own autouse fixture, repeated here: importing
    its `env`/`window` fixtures does not import its autouse ones, and the
    run path (`StoreProvider` -> `get_settings()`) reads the real `.env`
    without this."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


_ALERT_SECRETS = {
    "alert_smtp_host": "smtp.example.test:587",
    "alert_smtp_user": "owner-login@example.test",
    "alert_smtp_password": "hunter2-very-secret",
    "alert_email_to": "owner-inbox@example.test",
}

MAY_2, MAY_3, MAY_6 = date(2019, 5, 2), date(2019, 5, 3), date(2019, 5, 6)
UNTRADABLE = Asset(tradable=False, fractionable=True, status="active", cusip=None)


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
        assets=FakeBroker.active_assets("SPY", "DUALB", "SPFT", "TRNS", "WNDX"),
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

    env.fake.submit = original_submit  # not monkeypatch.undo(): that would also
    # revert conftest's autouse _no_network patch (same MonkeyPatch instance)
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
    env.fake.submit = original_submit  # not monkeypatch.undo(): that would also
    # revert conftest's autouse _no_network patch (same MonkeyPatch instance)
    accepted_securities = {
        env.query("SELECT security_id FROM orders WHERE client_order_id = ?", [c])[0][0]
        for c in accepted
    }
    (crashed_security,) = set(TARGETS) - accepted_securities
    first_attempt_notional = env.query(
        "SELECT notional FROM orders WHERE security_id = ? AND side = 'buy'", [crashed_security]
    )[0][0]
    # the fake fills the two accepted orders while the test is not looking,
    # so `resume` finds the crashed run's third order still open or pending.
    # `open_orders()` is every order not in a terminal status, which (per
    # `broker.py`'s `TERMINAL_STATUSES`) is exactly `accepted`, so this is
    # the public equivalent of the private `_orders` reach it replaces.
    for order in env.fake.open_orders():  # already a new list, not a live view
        env.fake.simulate_fill(order.client_order_id)

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
            second_notional = env.query(
                "SELECT notional FROM orders WHERE client_order_id = ?", [attempts[1][0]]
            )[0][0]
            assert second_notional == pytest.approx(first_attempt_notional)
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


# --- the order shape: a post-submit fault, settled like any other (ADR 0015 seam 3) ---------


def test_resume_settles_a_limit_order_halt_as_after_any_post_submit_fault(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker answers the batch's first submit with a limit order (which
    `FakeBroker` never does): the run halts after that order's `accepted`
    event, the `kill_switch` row naming `refused_order_shape`, the halt's
    cancel settles the limit order and the batch's other orders stay
    `pending`, unsubmitted. `paper resume` settles those `not_received` and
    releases, as after any fault after a submit (T135b)."""
    original_submit = env.fake.submit
    shaped: list[str] = []

    def limit_first(request: OrderRequest) -> object:
        order = original_submit(request)
        if shaped:
            return order
        shaped.append(request.client_order_id)
        return replace(order, order_type="limit", limit_price=1.0)

    monkeypatch.setattr(env.fake, "submit", limit_first)
    with pytest.raises(Exception, match="refused_order_shape"):
        env.run(at(F_0))
    env.fake.submit = original_submit  # not monkeypatch.undo() (see above)
    halted = env.latest_run()
    # A fault after a submit (`SystemFaultError`), not the pre-submit
    # `LimitBreachError`: no `missed` row.
    assert env.result(halted)[:2] == ("halted", "SystemFaultError")
    assert [e for e in env.rebalance_events() if e[1] == "missed"] == []
    [(reason,)] = env.query("SELECT reason FROM kill_switch WHERE run_id = ?", [halted])
    assert "refused_order_shape" in reason
    (coid,) = shaped
    statuses = env.query(
        "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at", [coid]
    )
    assert [s for (s,) in statuses] == ["pending", "accepted", "cancel_requested", "cancelled"]
    others = [c for (c,) in env.query("SELECT client_order_id FROM orders") if c != coid]
    assert others  # the batch's other buys: journaled, never submitted
    assert {r.client_order_id for r in env.submits()} == {coid}
    assert _engaged(env, window)

    released = _resume(env)
    assert released.status == RELEASED, released.reasons
    assert not _engaged(env, window)
    for other in others:
        last = env.query(
            "SELECT status, reason FROM order_events WHERE client_order_id = ? "
            "ORDER BY known_at DESC LIMIT 1",
            [other],
        )
        assert last == [("cancelled", "not_received")]


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
    env.fake.submit = original_submit  # not monkeypatch.undo(): that would also
    # revert conftest's autouse _no_network patch (same MonkeyPatch instance)
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
    """Every fill of the F_0 batch (all three targets) is hidden from
    `fills()` from the moment it is recorded (`lag_fills(None)`, before any
    of them fill): the fake holds them, but the journal never collects
    them, so none of DUALB, SPFT or TRNS is ever "held" from the ledger's
    own point of view, and no mark this test makes ever carries a holding
    (avoiding #653, a separate defect: a mark that does hold something
    blocks every later `paper resume`). `risk.max_fill_lag_sessions` = 2
    here, so a run between first listing the lag and the bound is told
    apart from the halting one; the first collection at or after the bound
    halts with `ReconciliationError`, with no submit made; `paper resume
    --accept-broker-fills` completes all three with a synthetic fill each,
    and when the real fills the fake was holding back finally surface,
    each is journaled `superseded_by` its synthetic one, so every reader
    (the fake's own net position and the journal's live, non-superseded
    fills) counts each position once."""
    env.open_window(
        frozen=FROZEN.model_copy(update={"max_fill_lag_sessions": 2}), tmp_path=tmp_path
    )
    env.fill_on_sleep = False
    coids = {s: buy_id(env, F_0, s) for s in ("SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER")}
    env.fake.lag_fills(None)  # every fill from here on, for every name, is hidden from fills()

    def fill_all(now: datetime) -> None:
        for order in env.fake.open_orders():
            env.fake.simulate_fill(order.client_order_id)

    env.on_sleep.append(fill_all)
    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]  # the fake holds all three
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
    synthetic_ids: dict[str, int] = {}
    synthetic_quantities: dict[str, float] = {}
    for security_id, c in coids.items():
        synthetic = env.query(
            "SELECT fill_id, quantity FROM fills "
            "WHERE client_order_id = ? AND source = 'broker_status'",
            [c],
        )
        assert len(synthetic) == 1, security_id
        synthetic_ids[security_id], synthetic_quantities[security_id] = synthetic[0]

    # The real fills the fake was holding back now surface: each must be
    # superseded by its synthetic one, counted once, not twice.
    env.fake.reveal_hidden_fills()
    nxt = env.run(at(date(2019, 5, 6)))
    assert nxt.status == "ok", env.result(env.latest_run())
    for security_id, c in coids.items():
        real = env.query(
            "SELECT fill_id, quantity, superseded_by FROM fills "
            "WHERE client_order_id = ? AND source = 'broker_feed'",
            [c],
        )
        assert len(real) == 1, security_id
        _real_fill_id, _real_quantity, superseded_by = real[0]
        assert superseded_by == synthetic_ids[security_id], security_id

    # counted once by every reader: the fake's own net position, and the
    # journal's live (non-superseded) fills through `fills_for`, agree with
    # the synthetic quantity alone, never the synthetic plus the real one.
    for symbol, security_id in (
        ("DUALB", "SEC_DUAL_B"),
        ("SPFT", "SEC_SPLIT_FUTURE"),
        ("TRNS", "SEC_TRANSFER"),
    ):
        held_quantity = env.held().get(symbol, 0.0)
        assert held_quantity == pytest.approx(synthetic_quantities[security_id])
        with open_read_only(env.settings) as conn:
            live = fills_for(conn, client_order_ids=[coids[security_id]])
        assert sum(f.fill.quantity for f in live) == pytest.approx(held_quantity)


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
    """F_0: DUALB's buy (submitted first) gets a 2-share partial fill, its
    clock reading moved on afterwards so the fill does not tie step 4's
    reconciliation the way a real clock's gap between separate writes
    already would (#650 is the latent, frozen-clock-only mechanism this
    sidesteps); TRNS's buy (submitted last) is scripted a transport fault
    (never received, settled
    `not_received` by `paper resume`); DUALB's partial-filled order and
    SPFT's plain `accepted` one are cancelled by the halt's own
    best-effort cancel (reason `halt`) — a sixth fate (`cancelled`),
    distinct from `not_received`, and DUALB's keeps its partial fill, so
    it earns both req 8 kinds a partial fill can (`position_return` for
    the fill, `not_executed` for the terminal remainder). `paper resume`
    releases while this crashed run has not completed any mark yet (its
    peak falls back to `switch.drawdown_peak`, which does not read
    `positions_daily`; #653's bug, which needs an *existing* mark with a
    holding, does not apply here). The next run re-attempts all
    three for the same pending rebalance: DUALB's new order is sized to
    its remainder (target minus the 2 already filled), and is rejected;
    SPFT's expires; TRNS's fills whole (`position_return`, held). A stop
    then sells TRNS, filling whole (`realised_pnl`, a sell) — covering
    every fate the plan line lists. Once every outcome is due, `paper
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

    trns_buy_1 = buy_id(env, F_0, "SEC_TRANSFER")
    dualb_buy_1 = buy_id(env, F_0, "SEC_DUAL_B")
    spft_buy_1 = buy_id(env, F_0, "SEC_SPLIT_FUTURE")
    dualb_price = env.query(
        "SELECT close FROM prices_daily WHERE security_id = 'SEC_DUAL_B' AND session = ?",
        [date(2019, 4, 30)],
    )[0][0]
    partial_quantity = 2.0

    def on_submit(request: object) -> None:
        # DUALB submits first; apply its partial fill (and move the clock
        # on) just before the next order (SPFT) submits, so a real clock's
        # gap between separate journal writes is modelled honestly.
        if request.client_order_id == spft_buy_1:  # type: ignore[attr-defined]
            env.clock.now += timedelta(seconds=1)
            env.fake.apply(dualb_buy_1, PartialFill(partial_quantity, float(dualb_price)))
            env.clock.now += timedelta(seconds=1)

    env.fake.on_submit = on_submit
    env.fake.script(TransportFault(), client_order_id=trns_buy_1)
    with pytest.raises(Exception, match="transport error"):
        env.run(at(F_0))
    env.fake.on_submit = None
    # DUALB's and SPFT's buys, submitted before TRNS's, are accepted then
    # cancelled by the halt (reason `halt`); TRNS's never reaches the fake.
    # DUALB's cancel keeps its partial fill.
    for coid in (dualb_buy_1, spft_buy_1):
        statuses = [
            s
            for (s,) in env.query(
                "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at",
                [coid],
            )
        ]
        assert statuses[1] == "accepted" and statuses[-1] == "cancelled"
    assert [
        s
        for (s,) in env.query(
            "SELECT status FROM order_events WHERE client_order_id = ? ORDER BY known_at",
            [trns_buy_1],
        )
    ] == ["pending"]
    (dualb_filled,) = env.query(
        "SELECT sum(quantity) FROM fills WHERE client_order_id = ?", [dualb_buy_1]
    )[0]
    assert dualb_filled == pytest.approx(partial_quantity)
    dualb_decision_id = env.query(
        "SELECT decision_id FROM orders WHERE client_order_id = ?", [dualb_buy_1]
    )[0][0]
    dualb_target_notional = env.query(
        "SELECT target_notional FROM decisions WHERE decision_id = ?", [dualb_decision_id]
    )[0][0]

    released = _resume(env)
    assert released.status == RELEASED, released.reasons
    last = env.query(
        "SELECT status, reason FROM order_events WHERE client_order_id = ? ORDER BY known_at",
        [trns_buy_1],
    )
    assert last[-1] == ("cancelled", "not_received")

    dualb_buy_2 = buy_id(env, MAY_2, "SEC_DUAL_B")
    spft_buy_2 = buy_id(env, MAY_2, "SEC_SPLIT_FUTURE")
    trns_buy_2 = buy_id(env, MAY_2, "SEC_TRANSFER")
    env.fake.script(Reject(), client_order_id=dualb_buy_2)
    env.fake.script(Expire(), client_order_id=spft_buy_2)
    reattempt = env.run(at(MAY_2))
    assert reattempt.status == "ok", env.result(env.latest_run())
    # DUALB still holds its 2-share partial fill (only the remainder's new
    # order was rejected); SPFT holds nothing (expired); TRNS is held whole.
    assert sorted(env.held()) == ["DUALB", "TRNS"]
    assert env.held()["DUALB"] == pytest.approx(partial_quantity)

    # the remainder, not the full target: DUALB's new attempt is sized to
    # what the partial fill left, not what the original order asked for.
    dualb_remainder_notional = env.query(
        "SELECT notional FROM orders WHERE client_order_id = ?", [dualb_buy_2]
    )[0][0]
    expected_remainder = dualb_target_notional - partial_quantity * float(dualb_price)
    assert dualb_remainder_notional == pytest.approx(expected_remainder, abs=0.01)
    assert dualb_remainder_notional != pytest.approx(dualb_target_notional)

    request_stop(env, at(MAY_2, 22, 0))
    trns_sell_1 = sell_id(env, MAY_3, "SEC_TRANSFER")
    stop_outcome = env.run(at(MAY_3))
    assert stop_outcome.status == "ok", env.result(env.latest_run())
    assert env.held().get("TRNS", 0.0) < 1e-6

    nxt = env.run(at(date(2019, 7, 31)))  # well past every order's due threshold
    assert nxt.status == "ok", env.result(env.latest_run())

    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert chain.passed, chain.detail

    def kinds_of(coid: str) -> set[str]:
        return {
            k for (k,) in env.query("SELECT kind FROM outcomes WHERE client_order_id = ?", [coid])
        }

    assert kinds_of(trns_buy_1) == {NOT_EXECUTED}  # cancelled (not_received), zero fill
    # cancelled (halt), with a kept 2-share partial fill: earns both kinds.
    assert kinds_of(dualb_buy_1) == {POSITION_RETURN, NOT_EXECUTED}
    assert kinds_of(spft_buy_1) == {NOT_EXECUTED}  # cancelled (halt), zero fill
    assert kinds_of(dualb_buy_2) == {NOT_EXECUTED}  # rejected, zero fill (the remainder)
    assert kinds_of(spft_buy_2) == {NOT_EXECUTED}  # expired, zero fill
    assert kinds_of(trns_buy_2) == {POSITION_RETURN}  # filled whole, held
    assert kinds_of(trns_sell_1) == {REALISED_PNL}  # filled whole, a sell

    with env.connect() as conn:
        conn.execute(
            "DELETE FROM order_events WHERE client_order_id = ? AND status = 'rejected'",
            [dualb_buy_2],
        )
    with env.connect() as conn:
        lines = check_module.check(conn, env.settings)
    chain = next(line for line in lines if line.name == "chain")
    assert not chain.passed
    assert dualb_buy_2 in chain.detail


# --- cent rounding stays within the reconcile tolerance -------------------------------------


def _price_every_symbol(env: Env, session: date) -> None:
    """`Env.price_at`'s job, but for every ticker with a price that session,
    not just the fixed `SYMBOLS` set: the real momentum universe can pick a
    name outside that set over several real months. Filtered to the one
    `known_at` vintage the run's own ingest would have seen (`Env.run`
    ingests through 21:00 UTC on S-1, the same cutoff used here): a bare
    `session = ?` with no `known_at` bound would pick an arbitrary row once
    a restatement gives `(security_id, session)` more than one `known_at`
    (schema.py's `UNIQUE (security_id, session, known_at)`), which could
    read a later-known price into the fake's fill and silently violate
    point-in-time. `store.asof.prices_as_of` does the same `known_at`
    pick, but over every session at once, as a polars frame with no
    ticker; a raw query stays simpler here, where exactly one session is
    wanted and the result must join to `listings` for the ticker anyway."""
    as_of = at(previous_session(session), 21, 0)
    rows = env.query(
        "SELECT l.ticker, p.close FROM prices_daily p "
        "JOIN listings l ON l.security_id = p.security_id "
        "WHERE p.session = ? AND l.valid_from = ("
        "  SELECT max(l2.valid_from) FROM listings l2 "
        "  WHERE l2.security_id = l.security_id AND l2.valid_from <= p.session"
        ") AND p.known_at = ("
        "  SELECT max(p2.known_at) FROM prices_daily p2 "
        "  WHERE p2.security_id = p.security_id AND p2.session = p.session "
        "  AND p2.known_at <= ?"
        ")",
        [previous_session(session), as_of],
    )
    for ticker, close in rows:
        env.prices[ticker] = float(close)


def _no_drop_rule(env: Env) -> None:
    """`paper.max_universe_drop_fraction=1.0` for a window whose later rebalance loses
    most of the fixture's handful of names (T188, #1550: named in its PR, the one
    settings override the plan allows instead of editing the fixture)."""
    paper = env.settings.paper.model_copy(update={"max_universe_drop_fraction": 1.0})
    env.settings = env.settings.model_copy(update={"paper": paper})


def test_a_cent_rounding_fake_stays_within_the_reconcile_cash_tolerance(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fake that rounds each fill to the cent (`round_cash_to_cent=True`)
    stays within `risk.reconcile_cash_tolerance` over the window's frozen
    `paper.min_rebalances` months of fills, with the rounding actually
    exercised (at least one fill not an exact cent itself), and each
    month's own signed rounding drift (buy and sell cash residues carry
    opposite signs), not just its reconciliation status, is reported and
    checked against the same tolerance. Position limits are loosened to
    1.0 (the other tests' `FROZEN` is tuned for a fixed
    3-name, one-third-each portfolio, not whatever the real momentum
    universe picks each month), and `price_at` is widened to every ticker
    with a price that session, not just the fixed `SYMBOLS` set, so a later
    month's pick is never missing a price."""
    window = env.open_window(
        frozen=FROZEN.model_copy(
            update={"max_position_weight": 1.0, "max_order_notional_fraction": 1.0}
        ),
        tmp_path=tmp_path,
    )
    _no_drop_rule(env)
    monkeypatch.setattr(env, "price_at", lambda session: _price_every_symbol(env, session))
    env.new_fake(round_cash_to_cent=True)
    # WNDX delists partway through the window (last bar 2019-06-24): the
    # broker reports it untradable from the next session, so the marks
    # after its last bar carry its last close (#678) instead of failing.
    env.fake.set_asset("WNDX", UNTRADABLE, from_session=date(2019, 6, 25))

    # The fixture's `frozen_json` (test_run_trade.py) leaves `paper.*` keys
    # out; req 14 freezes `min_rebalances` at `paper start` (config.py's
    # `FROZEN_PAPER_KEYS`), so give this window's frozen value the same key
    # production would, and read the loop bound back from it rather than
    # from `Settings()` (which the owner's environment, not the window,
    # controls). This is done with a direct `UPDATE`, not by having the
    # shared `frozen_json()` helper freeze every `FROZEN_PAPER_KEYS` key
    # from `PaperConfig()` the way `window.py`'s `_frozen_params` does:
    # that helper's `paper.max_catch_up_sessions` is deliberately set to
    # the test constant `MAX_CATCH_UP` (2), not the config default (5), so
    # a blanket default-filling loop there would silently change every
    # other test's catch-up-session behavior.
    assert window.window_id is not None
    frozen_values = json.loads(window.frozen_json)
    frozen_values["paper.min_rebalances"] = PaperConfig().min_rebalances["month_end"]
    with env.connect() as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [json.dumps(frozen_values), window.window_id],
        )
    min_rebalances = frozen_values["paper.min_rebalances"]

    sessions = [F_0]
    year, month = F_0.year, F_0.month
    for _ in range(min_rebalances - 1):
        sessions.append(next_session(last_session_of_month(year, month)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    assert [(s.year, s.month) for s in sessions] == [
        (2019, 5),
        (2019, 6),
        (2019, 7),
        (2019, 8),
        (2019, 9),
        (2019, 10),
    ]  # six consecutive months' first sessions, none skipped

    cash_tolerance = Decimal(repr(FROZEN.reconcile_cash_tolerance))
    any_fill_needed_rounding = False
    for session in sessions:
        outcome = env.run(at(session))
        assert outcome.status == "ok", env.result(env.latest_run())
        # `superseded_by IS NULL`: every journal reader (`journal.fills_for`,
        # `journal.py`'s own "the only reader of `fills`" contract) hides a
        # superseded row. This test's fills are never superseded (no lagging
        # fill, no resume in this test), so the filter changes nothing here
        # today, but a raw query should still match what production reads.
        session_fills = env.query(
            "SELECT f.price, f.quantity, o.side FROM fills f "
            "JOIN orders o ON o.client_order_id = f.client_order_id "
            "WHERE o.run_id = ? AND f.superseded_by IS NULL",
            [outcome.run_id],
        )
        # The month's own cash drift: a buy's rounded cost understates or
        # overstates the exact one by `exact - rounded`; a sell's effect on
        # cash is the opposite sign. Kept per session, not accumulated
        # across sessions: `ledger.py` builds cash from the window's last
        # `ok` reconciliation's `broker_cash` plus only what's known after
        # it, so there is no production invariant that sums this drift
        # across months, only one that bounds it within each one, the same
        # tolerance each reconciliation already enforces.
        session_drift = Decimal("0")
        for price, quantity, side in session_fills:
            exact = Decimal(repr(price)) * Decimal(repr(quantity))
            rounded = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if exact != rounded:
                any_fill_needed_rounding = True
            session_drift += (exact - rounded) if side == "buy" else (rounded - exact)
        assert abs(session_drift) <= cash_tolerance, (
            f"{session.isoformat()}'s rounding drift {session_drift} exceeded "
            f"risk.reconcile_cash_tolerance ({cash_tolerance})"
        )

    rows = env.query("SELECT status FROM reconciliations ORDER BY reconciliation_id")
    assert len(rows) >= min_rebalances  # not a vacuous pass on an empty table
    assert all(status == "ok" for (status,) in rows)
    # vacuous-pass guard: `any_fill_needed_rounding` can only be True if at
    # least one fill was made somewhere in the window, so this alone also
    # proves fills were made, not just that rounding was exercised.
    assert any_fill_needed_rounding  # at least one fill's exact notional needed cent rounding


def test_a_mid_window_delisting_is_marked_at_its_last_close_then_force_exited(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#678 / T86, end to end: WNDX (SEC_WINDOW_DELIST) is bought by the real
    momentum universe, its bars stop after 2019-06-24 and the broker reports
    it untradable from the next session. The July run marks 06-25..06-28 at
    the 06-24 close (`tradable` false) instead of failing; its Form 25,
    accepted 2019-06-28 20:30Z, is after that run's cut (close(06-28)), so the
    August run is the first to read it and reaches step 7's `delisted` forced
    exit, closed at once as `skipped` `untradable`."""
    env.open_window(
        frozen=FROZEN.model_copy(
            update={"max_position_weight": 1.0, "max_order_notional_fraction": 1.0}
        ),
        tmp_path=tmp_path,
    )
    _no_drop_rule(env)
    monkeypatch.setattr(env, "price_at", lambda session: _price_every_symbol(env, session))
    env.new_fake()
    env.fake.set_asset("WNDX", UNTRADABLE, from_session=date(2019, 6, 25))
    last_bar, gap = date(2019, 6, 24), [date(2019, 6, d) for d in (25, 26, 27, 28)]
    june, july, august = date(2019, 6, 3), date(2019, 7, 1), date(2019, 8, 1)

    for session in (F_0, june):
        assert env.run(at(session)).status == "ok", env.result(env.latest_run())
    assert env.held().get("WNDX", 0.0) > 0.0  # the premise: the universe bought it

    july_run = env.run(at(july))
    assert july_run.status == "ok", env.result(env.latest_run())
    ((last_close,),) = env.query(
        "SELECT close FROM prices_daily WHERE security_id = 'SEC_WINDOW_DELIST' "
        "AND session = ? ORDER BY known_at DESC LIMIT 1",
        [last_bar],
    )
    marked = env.query(
        "SELECT session, mark_price, tradable FROM positions_daily "
        "WHERE security_id = 'SEC_WINDOW_DELIST' AND session > ? AND session <= ? "
        "ORDER BY session",
        [last_bar, gap[-1]],
    )
    assert marked == [(day, pytest.approx(last_close), False) for day in gap]
    assert env.query(
        "SELECT count(*) FROM decisions "
        "WHERE decision = 'forced_exit' AND security_id = 'SEC_WINDOW_DELIST'"
    ) == [(0,)]

    august_run = env.run(at(august))
    assert august_run.status == "ok", env.result(env.latest_run())
    exits = env.query(
        "SELECT d.reason, e.status, e.reason, d.run_id FROM decisions d "
        "JOIN decision_events e USING (decision_id) "
        "WHERE d.decision = 'forced_exit' AND d.security_id = 'SEC_WINDOW_DELIST'"
    )
    assert exits == [("delisted", "skipped", "untradable", august_run.run_id)]


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
        assets=FakeBroker.active_assets("SPY", "DUALB", "SPFT", "TRNS", "WNDX"),
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


def test_lot_ledger_alert_row_exists_when_the_command_returns(
    alert_fakes: FakeRunner,
    fixture_store_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lot-ledger rebuild error (#366 Q5) is a `lot_ledger` alert and a note,
    and never fails the run. The real writer runs; only its `on_lot_error`
    callback is also fed one error, the way the writer reports a sale beyond
    the fill-built holding (`test_outcomes.py` covers that trigger itself)."""
    real = run_module.write_outcomes_and_lots

    def failing_lots(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        on_lot_error = kwargs["on_lot_error"]
        assert callable(on_lot_error)
        on_lot_error("SEC_KID: a sale of more than the ledger holds")
        return result

    monkeypatch.setattr(run_module, "write_outcomes_and_lots", failing_lots)
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    outcome = alerting_env.run(at(F_0))
    assert outcome.status == "ok", alerting_env.result(alerting_env.latest_run())
    ((run_id, session, message),) = alerting_env.alerts("lot_ledger")
    assert (run_id, session) == (outcome.run_id, F_0)
    assert "more than the ledger holds" in message
    _assert_delivered(alert_fakes, "lot ledger not rebuilt")


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


# --- the fault-specific halt kinds (#644: fixed by #667; a halt's own engagement, #741) -------


def test_halt_writes_exactly_one_halted_alert_and_no_kill_switch_alert(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    """A halt's own `engage()` writes no `kill_switch` alert (#741, owner
    decision (a)): spec req 4 says a halt writes **one** alert, `halted` for
    a transport fault (#644 Option A), and req 11 scopes `kill_switch` alerts
    to runs that end `skipped_kill_switch` (#644 option 2, #677)."""
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    alerting_env.fake.script(
        TransportFault(), client_order_id=buy_id(alerting_env, F_0, "SEC_TRANSFER")
    )
    with pytest.raises(Exception, match="transport error"):
        alerting_env.run(at(F_0))
    run_id = alerting_env.latest_run()
    assert alerting_env.query("SELECT source, run_id FROM kill_switch WHERE state = 'engaged'") == [
        ("fault", run_id)
    ]  # the halt did engage the switch
    assert alerting_env.query("SELECT kind, run_id FROM alerts ORDER BY alert_id") == [
        ("halted", run_id)
    ]
    assert alerting_env.alerts("kill_switch") == []


def test_reconciliation_alert_row_exists_before_the_exception_propagates(
    alert_fakes: FakeRunner, fixture_store_path: Path, tmp_path: Path
) -> None:
    alerting_env = _alerting_env(fixture_store_path)
    alerting_env.open_window(tmp_path=tmp_path)
    bought_outcome = alerting_env.run(at(F_0))
    if bought_outcome.status != "ok":
        pytest.fail(f"setup: F_0 run was not ok: {alerting_env.result(alerting_env.latest_run())}")
    alerting_env.fake.submit(OrderRequest("owner-1", "DUALB", Side.BUY, quantity=1.0))
    alerting_env.fake.simulate_fill("owner-1")  # a position the ledger lacks
    with pytest.raises(ReconciliationError):
        alerting_env.run(at(MAY_2))
    assert alerting_env.alerts("reconciliation") != []


def test_rejection_cap_alert_row_exists_before_the_exception_propagates(
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


def test_skip_cap_alert_row_exists_before_the_exception_propagates(
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


# --- resume releases while a position is held (#653, fixed by #664) ------------------


def test_resume_releases_while_the_window_holds_a_marked_position(
    env: Env, window: PaperWindowRow
) -> None:
    """A real mark written by a completed run that holds positions (the
    production path, `marks.marks_for`) carries no `security_id IS NULL`
    cash-only row once anything is held; before #664, `resume._mark_equity`
    required one and refused the release (#653). `paper resume` releases
    (equity is cash plus three held names' values)."""
    bought(env)  # DUALB, SPFT, TRNS filled; this run's own mark is cash-only (covers T_0)
    marked = env.run(at(MAY_2))  # a mark run: writes F_0's mark, now with the three positions
    if marked.status != "ok":
        pytest.fail(f"setup: the mark run was not ok: {env.result(env.latest_run())}")
    engaged = switch.engage(
        env.settings, env.clock, window_id=window.window_id, source="owner", reason="test"
    )
    if not isinstance(engaged, int):
        pytest.fail(f"setup: switch.engage did not return an event id: {engaged!r}")

    outcome = _resume(env)
    assert outcome.status == RELEASED, outcome.reasons


# --- an acknowledgement timeout leaves one order `pending_unresolved` (#1841, T202b) ---------
#
# Paper spec amendment 2026-10-18 item 4, end to end. F_0's three buys are submitted
# DUALB, SPFT, TRNS (in that order), each a 33,283.40 notional; the fake fills an
# open order whenever the run sleeps, so only a vanished order stays unfilled.


def _events(env: Env, coid: str) -> list[tuple[str, str | None]]:
    return [
        (status, reason)
        for status, reason in env.query(
            "SELECT status, reason FROM order_events WHERE client_order_id = ? "
            "ORDER BY known_at, rowid",
            [coid],
        )
    ]


def _reconciliations(env: Env, run_id: int | None) -> list[str]:
    return [
        status
        for (status,) in env.query(
            "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
            [run_id],
        )
    ]


def _first_reconciled_at(env: Env, run_id: int | None) -> datetime:
    return env.query("SELECT min(known_at) FROM reconciliations WHERE run_id = ?", [run_id])[0][0]


def _unresolved_buy(env: Env) -> tuple[str, int]:
    """F_0's run with TRNS's buy (the last submit) vanishing at the broker, so it is
    never acknowledged: (its client_order_id, the run's id)."""
    coid = buy_id(env, F_0, "SEC_TRANSFER")
    env.fake.script(Vanish(), client_order_id=coid)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.run_id is not None
    return coid, outcome.run_id


def _no_price(security_id: str) -> float:
    raise AssertionError(f"a notional buy's reserve reads no price ({security_id})")


def test_an_unacknowledged_buy_past_the_longer_poll_ends_the_run_ok_pending_unresolved(
    env: Env, window: PaperWindowRow
) -> None:
    """The poll runs to `paper.accept_wait_max_seconds` (300 s), then one
    `pending_unresolved` event, no reason; the run ends `ok`, its step 8
    reconciliation `pending_unresolved` (no cash re-base), one
    `pending_unresolved` alert naming the order, no switch; the decision stays
    open with its one order, and the order's unfilled 33,283.40 is the next
    session's whole buy reserve (the other two buys filled)."""
    coid = buy_id(env, F_0, "SEC_TRANSFER")
    env.fake.script(Vanish(), client_order_id=coid)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    run_id = outcome.run_id
    assert env.result(run_id)[:2] == ("ok", None)
    stamped = env.query(
        "SELECT status, reason, known_at FROM order_events WHERE client_order_id = ? "
        "ORDER BY known_at, rowid",
        [coid],
    )
    assert [(status, reason) for status, reason, _ in stamped] == [
        ("pending", None),
        ("pending_unresolved", None),
    ]
    waited = stamped[1][2] - stamped[0][2]
    assert waited >= timedelta(seconds=env.settings.paper.accept_wait_max_seconds)
    for security_id in ("SEC_DUAL_B", "SEC_SPLIT_FUTURE"):
        assert _events(env, buy_id(env, F_0, security_id))[-1] == ("filled", None)
    assert env.query("SELECT count(*) FROM fills WHERE client_order_id = ?", [coid]) == [(0,)]
    assert [(a[0], a[2]) for a in _attempts(env, "SEC_TRANSFER")] == [(coid, "pending_unresolved")]
    assert env.query(
        "SELECT count(*) FROM decision_events e JOIN orders o USING (decision_id) "
        "WHERE o.client_order_id = ?",
        [coid],
    ) == [(0,)]
    assert _reconciliations(env, run_id)[-1] == "pending_unresolved"
    [(alert_run, alert_session, message)] = env.alerts("pending_unresolved")
    assert (alert_run, alert_session) == (run_id, F_0)
    assert coid in message
    assert env.alerts("halted") == []
    assert env.count("kill_switch") == 0
    assert not _engaged(env, window)

    (notional,) = env.query("SELECT notional FROM orders WHERE client_order_id = ?", [coid])[0]
    assert notional == pytest.approx(33_283.40)
    with open_read_only(env.settings) as conn:
        orders = orders_for(conn, window_id=None)
        events = order_events_for(conn, window_id=None)
        fills = fills_for(conn)
        actions = live_actions_as_of(conn, session_close(F_0))
    reserve = open_buy_reserve(
        orders, events, fills, actions, _no_price, FROZEN, session=F_0_PLUS_1
    )
    assert reserve == Decimal("33283.40")


def test_the_next_runs_start_cancels_an_order_the_broker_never_got_and_reattempts_it(
    env: Env, window: PaperWindowRow
) -> None:
    """Item 4 (c), unknown: step 3 of the next run (2019-05-02, a catch-up in the
    window) cancels it `not_received` before step 4 reconciles; the decision,
    open again, gets one new attempt on that run, which fills; both of that
    run's reconciliations are `ok`."""
    coid, _ = _unresolved_buy(env)
    nxt = env.run(at(F_0_PLUS_1))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert _events(env, coid) == [
        ("pending", None),
        ("pending_unresolved", None),
        ("cancelled", "not_received"),
    ]
    settled_at = env.query(
        "SELECT known_at FROM order_events WHERE client_order_id = ? AND status = 'cancelled'",
        [coid],
    )[0][0]
    assert settled_at <= _first_reconciled_at(env, nxt.run_id)
    assert _reconciliations(env, nxt.run_id) == ["ok", "ok"]
    attempts = _attempts(env, "SEC_TRANSFER")
    assert [(a[0] == coid, a[2]) for a in attempts] == [(True, "cancelled"), (False, "filled")]
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]


def test_the_next_runs_start_acknowledges_and_collects_an_order_the_broker_did_get(
    env: Env, window: PaperWindowRow
) -> None:
    """Item 4 (c), known: the broker got TRNS's buy late and filled it after the
    first run gave up. The next run's step 3 journals `accepted` (before step 4),
    its one fill and `filled`; no new attempt is made, and both reconciliations
    are `ok`."""
    coid, _ = _unresolved_buy(env)
    (notional,) = env.query("SELECT notional FROM orders WHERE client_order_id = ?", [coid])[0]
    placed = env.fake.submit(OrderRequest(coid, "TRNS", Side.BUY, notional=notional))
    env.fake.simulate_fill(coid)
    submits = len(env.submits())
    nxt = env.run(at(F_0_PLUS_1))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert [status for status, _ in _events(env, coid)] == [
        "pending",
        "pending_unresolved",
        "accepted",
        "filled",
    ]
    accepted_at, broker_order_id = env.query(
        "SELECT known_at, broker_order_id FROM order_events "
        "WHERE client_order_id = ? AND status = 'accepted'",
        [coid],
    )[0]
    assert broker_order_id == placed.broker_order_id
    assert accepted_at <= _first_reconciled_at(env, nxt.run_id)
    assert env.query("SELECT count(*) FROM fills WHERE client_order_id = ?", [coid]) == [(1,)]
    assert len(env.submits()) == submits
    assert _reconciliations(env, nxt.run_id) == ["ok", "ok"]
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]


def test_a_second_acknowledgement_timeout_in_one_run_halts(
    env: Env, window: PaperWindowRow
) -> None:
    """Item 4 (a): at most one per run. SPFT's buy times out first and is left
    `pending_unresolved`; TRNS's, the second, halts with
    `AcknowledgementTimeoutError` as before T202b, its order left `pending`."""
    first, second = buy_id(env, F_0, "SEC_SPLIT_FUTURE"), buy_id(env, F_0, "SEC_TRANSFER")
    env.fake.script(Vanish(), client_order_id=first)
    env.fake.script(Vanish(), client_order_id=second)
    with pytest.raises(AcknowledgementTimeoutError):
        env.run(at(F_0))
    halted = env.latest_run()
    assert env.result(halted)[:2] == ("halted", "AcknowledgementTimeoutError")
    assert _events(env, first) == [("pending", None), ("pending_unresolved", None)]
    assert _events(env, second) == [("pending", None)]
    assert env.query("SELECT source, fault_type, run_id FROM kill_switch") == [
        ("fault", "AcknowledgementTimeoutError", halted)
    ]
    assert len(env.alerts("halted")) == 1


def test_the_clock_check_runs_again_after_the_longer_poll(env: Env, window: PaperWindowRow) -> None:
    """Item 4 (a): after the longer poll the clock pre-check runs again before the
    next submit. DUALB's buy (the first submit) vanishes; during its poll the
    adapter stops sharing the wrapper's clock object (the identity the pre-check
    tests first), so the check halts with `ClockError` before SPFT is submitted.
    Nothing else in the run reads that identity: without the second check the
    run would end `ok`."""
    coid = buy_id(env, F_0, "SEC_DUAL_B")
    env.fake.script(Vanish(), client_order_id=coid)
    shared = env.fake.clock

    def unshare(_now: datetime) -> None:
        env.fake.clock = lambda: shared()

    env.on_sleep.append(unshare)
    with pytest.raises(ClockError, match="one clock object"):
        env.run(at(F_0))
    halted = env.latest_run()
    assert env.result(halted)[:2] == ("halted", "ClockError")
    assert [request.client_order_id for request in env.submits()] == [coid]
    assert _events(env, coid) == [("pending", None), ("pending_unresolved", None)]


def test_after_the_longer_poll_outside_the_submit_window_the_rest_is_never_sent(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    """Item 4 (a): the submit-window check runs again after the longer poll. The
    window closes at the open (13:30 UTC, `submit_window_after_open_minutes` 0);
    the run starts at 13:27 and DUALB's buy vanishes, so its 300 s poll ends at
    13:32. SPFT and TRNS, journaled before the first submit, are never sent: each
    is closed `cancelled` with reason `not_received` (the broker never got it),
    the run ends `ok`, the rebalance stays pending, and the next in-window run
    settles DUALB `not_received` and trades all three."""
    settings = Settings(
        _env_file=None,
        store={"path": str(fixture_store_path)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
        paper={"submit_window_after_open_minutes": 0},
    )
    env = Env(settings, Clock(at(F_0, 13, 27)))
    env.open_window(tmp_path=tmp_path)
    coid = buy_id(env, F_0, "SEC_DUAL_B")
    env.fake.script(Vanish(), client_order_id=coid)
    outcome = env.run(at(F_0, 13, 27))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert [request.client_order_id for request in env.submits()] == [coid]
    assert _events(env, coid) == [("pending", None), ("pending_unresolved", None)]
    for security_id in ("SEC_SPLIT_FUTURE", "SEC_TRANSFER"):
        assert _events(env, buy_id(env, F_0, security_id)) == [
            ("pending", None),
            ("cancelled", "not_received"),
        ]
    assert env.rebalance_events() == []
    assert _reconciliations(env, outcome.run_id)[-1] == "pending_unresolved"
    assert env.count("kill_switch") == 0

    nxt = env.run(at(F_0_PLUS_1))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert _events(env, coid)[-1] == ("cancelled", "not_received")
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    assert _reconciliations(env, nxt.run_id) == ["ok", "ok"]


def test_an_unresolved_sells_proceeds_are_not_spent_and_its_name_stays_held(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4 (a), the sell side. On F_1 an `exclude_name` override sells TRNS
    whole (1,314.510268 shares) and the plan buys 35,155.05 with the proceeds
    (DUALB 1,192.51, SPFT 246.30, WNDX 33,716.24; cash before the run 149.80).
    The broker fills the sell but never acknowledges it in the run (`get_order`
    and the fills feed do not show it): its proceeds are excluded from the
    buys phase's cash, so the buys submitted cost at most the 149.80, and its
    position counts as held (no fill journaled; the ledger keeps TRNS until the
    read-back). The next run's step 3 acknowledges it and journals the fill."""
    bought(env)
    assert window.window_id is not None
    t_1, f_1 = date(2019, 5, 31), date(2019, 6, 3)
    stamp = at(T_0, 22)
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="the owner's reason, long enough",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    sell = sell_id(env, f_1, "SEC_TRANSFER")
    hidden = {sell}
    get_order, fills = env.fake.get_order, env.fake.fills

    def unacknowledged(client_order_id: str) -> object:
        if client_order_id in hidden:
            raise UnknownOrderError(client_order_id)
        return get_order(client_order_id)

    def feed(since: datetime | None = None) -> list[object]:
        return [f for f in fills(since) if f.client_order_id not in hidden]

    monkeypatch.setattr(env.fake, "get_order", unacknowledged)
    monkeypatch.setattr(env.fake, "fills", feed)
    cash_before = env.fake.account().cash
    assert cash_before == pytest.approx(149.80)

    outcome = env.run(at(f_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert _events(env, sell) == [("pending", None), ("pending_unresolved", None)]
    assert env.held().get("TRNS", 0.0) < 1e-6  # the broker did sell it
    assert env.fake.account().cash > cash_before + 30_000  # and credited the proceeds
    buys = env.query(
        "SELECT notional FROM orders WHERE run_id = ? AND side = 'buy'", [outcome.run_id]
    )
    assert sum(notional for (notional,) in buys) <= cash_before
    assert env.query("SELECT count(*) FROM fills WHERE client_order_id = ?", [sell]) == [(0,)]
    assert _reconciliations(env, outcome.run_id)[-1] == "pending_unresolved"
    assert len(env.alerts("pending_unresolved")) == 1

    hidden.clear()
    nxt = env.run(at(date(2019, 6, 4)))
    assert nxt.status == "ok", env.result(env.latest_run())
    assert [status for status, _ in _events(env, sell)][-2:] == ["accepted", "filled"]
    assert env.query("SELECT count(*) FROM fills WHERE client_order_id = ?", [sell]) == [(1,)]
    assert _reconciliations(env, nxt.run_id)[-1] == "ok"


def test_paper_resume_under_the_switch_settles_it_by_the_read_back(
    env: Env, window: PaperWindowRow
) -> None:
    """Item 4 (c): with the switch engaged (the owner's), `paper resume` settles the
    `pending_unresolved` order by the same read-back (the broker got it and filled
    it: `accepted`, its fill, `filled`), submits nothing, reconciles and releases."""
    coid, _ = _unresolved_buy(env)
    assert window.window_id is not None
    engaged = switch.engage(
        env.settings, env.clock, window_id=window.window_id, source="owner", reason="test"
    )
    assert isinstance(engaged, int)
    (notional,) = env.query("SELECT notional FROM orders WHERE client_order_id = ?", [coid])[0]
    env.fake.submit(OrderRequest(coid, "TRNS", Side.BUY, notional=notional))
    env.fake.simulate_fill(coid)
    submits = len(env.submits())
    outcome = _resume(env)
    assert outcome.status == RELEASED, outcome.reasons
    assert [status for status, _ in _events(env, coid)] == [
        "pending",
        "pending_unresolved",
        "accepted",
        "filled",
    ]
    assert len(env.submits()) == submits


def test_paper_reconcile_under_the_switch_settles_it_by_the_read_back(
    env: Env, window: PaperWindowRow
) -> None:
    """Item 4 (c): with the switch engaged, `paper reconcile` settles the order by
    the read-back before it reconciles (the broker never got it: `cancelled`,
    `not_received`), so its reconciliation is `ok`, not `pending_unresolved`; it
    submits nothing and releases nothing."""
    coid, _ = _unresolved_buy(env)
    assert window.window_id is not None
    engaged = switch.engage(
        env.settings, env.clock, window_id=window.window_id, source="owner", reason="test"
    )
    assert isinstance(engaged, int)
    submits = len(env.submits())
    result = reconcile_command(env.settings, env.connect, env.fake, _ticking(env))
    assert result.status == "ok"
    assert _events(env, coid)[-1] == ("cancelled", "not_received")
    assert _engaged(env, window)
    assert len(env.submits()) == submits

"""The tracking run's core: `paper run` on session S (Phase 4 spec req 7; reqs 4,
5, 8 and 11; plan T63).

`tracking_run(settings, connect, broker, clock)` is the whole run, in the req 7
order. Step 6's trading half and the forced exits are dispatched through the
module names `trade_step` and `exits_step` (T63d); the `stop` kind through
`stop_step`, which raises `NotImplementedError` until T63f lands, so a `stop`
run ends `failed` there rather than skip a step silently.

**Entry.** The session is the clock's New York date. The run lock (T59) is
taken first: a second instance writes a `locked` alert with no run id (its
session the one containing the clock reading, or the next one on a non-session
day) and returns `locked`, closing nothing. With no open window (before `paper
start`, after `paper stop`) the run writes a `no_window` alert, deduped on that
session, and returns `no_window`: no broker call, no `paper_runs` row. On a
non-session day with a window open, the run writes its row with no session and
no kind, and the result `no_session`, and does nothing else.

**Step 1.** `switch.engage_from_overrides` engages every unconsumed
`engage_kill_switch` override, so an override takes effect at the next run of
any kind; then the derived switch (T59) is read; then the `paper_runs` row is
written with its kind (`stop` while the window has a stop requested, else
`planning.rebalance_kind`'s, else `mark`) and `invoked_by` (`scheduler` only
when `TRADEPARTNER_INVOKED_BY=scheduler` and stdin is not a TTY), and in the
same chunk every unfinished earlier run of the window is closed `crashed`.
Then `account()`: an `account_id` other than the window's halts with
`ReconciliationError`.

**Step 2. Staleness.** The latest `ok` ingestion run finished at or before the
clock reading must cover S-1 for `ingest.reference_symbol`: that symbol's
security (its listing current at S-1) has a bar for S-1 known by that run's
`finished_at`. Otherwise `StaleDataError`, before any plan read, any
collection and any reconciliation.

**Step 3. Collection** (T58) of every acknowledged non-terminal order of the
window (a `pending` one is `paper resume`'s), with the `written_off`
back-fill for the pending rebalances' buys. A rejection verdict raises
`RejectionCapError` naming the submitting run. An order still
`fills_lagging` at or past the frozen `risk.max_fill_lag_sessions`
(`collect.lag_verdict`) gets a `mismatch` reconciliation row listing it, and
halts with `ReconciliationError` the first time; while the switch is already
engaged the row is the report and the run goes on (spec req 8). Then the
`executed` test: every due rebalance with no `rebalance_events` row whose
decisions are all closed or settled (`plan.rebalance_state`) gets its
`executed` row, before the lapse rule.

**Step 4.** `reconcile_now` (T61), its journal cut (`as_of`, #488) a clock
reading taken just before the call, so step 3's collected rows are in it.
Then one `assets` read for every name the ledger holds at S, kept for the
marks, the lot ledger, the exits and the planning step; the planning step's
`assets_read` answers from it and reads the broker through the same path for
any symbol it lacks.

**Step 5.** `marks.marks_for` rows for every session after the window's last
mark (or from the window's start date) through S-1; the drawdown check (T59)
on each session this run marks (and the last marked session), so a crossing on
a back-filled session is not missed, engaging once with source `drawdown` and
its alert; the `missed_run` alert when S-1
is after the window's start date and has no `paper_runs` row; the lapse rows
from `marks.lapses`, with one `missed_rebalance` alert; the due outcomes and the
lot-ledger write (T62). A lot-ledger error never fails the run: it is a
`lot_ledger` alert (#366 Q5) and a note on the result row.

**Step 6.** Under an engaged switch the run ends here, `skipped_kill_switch`.
Otherwise a `stop` run goes to `stop_step`; a run with a rebalance or catch-up
due plans through `planning.plan_rebalance` (with this run's `assets` read and
the `fills_lagging` state: any order lagging at step 3 or at step 4) and goes
to `trade_step`; any other run goes to `exits_step`.

**The submit window** gates both: a batch starts only while the clock is in
[open(S) - `paper.submit_window_before_open_minutes`, open(S) +
`paper.submit_window_after_open_minutes`]. Outside it nothing is journaled for
the batch and nothing submitted, forced exits included: a pending rebalance
stays pending for the next in-window run, an exit is made by that run. Under
`fills_lagging` the same holds: no plan was made, and no forced exit is sized
off a ledger that is missing a fill.

**Step 7, forced exits** (T63e, `exits.reattempt_exits` and
`exits.forced_exits`), read once inside the window and before this run
journals any exit: the window's decisions with their states, the ledger
stated for S, the own non-terminal sells, the adjustments, the listings
`delisted` at close(S-1) (a transfer is not an end, as the wrapper reads it)
and this run's `assets` read, every store fact as of close(S-1). The new
exits are journaled in one chunk; an untradable one with its `skipped` row
(event reason `untradable`), which closes it, so it is not ordered and the
next session re-evaluates it. The open exits (re-attempted
for their remainder) and the new tradable ones go to the wrapper's `execute`
(T60b) with the plan's decisions on a rebalance or catch-up run, so they join
its sells phase; on any other run they are a batch of their own, made only
when there is an exit. The window is checked again just before `execute`, so
the sells phase starts inside it: an exit journaled by a run that has since
left the window stays open for the next in-window run. Every exception
`execute` raises takes the halt path.

**Step 7b.** After a batch, this run's acknowledged orders are collected (T58)
until each is terminal or `paper.accept_wait_seconds` has passed since the
step began (an absolute deadline: an order still open then is step 3's on the
next run), polling every `paper.poll_interval_seconds`. Then the `executed`
test of step 3 runs again, and for the batch's rebalance reaching `executed`
here the `unspent_cash` alert is written when the cash the buys phase left
(`BatchOutcome.cash_left`) exceeds the frozen `risk.max_unspent_cash_fraction`
of the broker's equity read at that moment. The batch's status
(`skipped_kill_switch` when the wrapper read the switch engaged) is the run's.

**Steps 8 and 9.** `reconcile_now` again, cut the same way, then the result row:
`ok`, or the batch's `skipped_kill_switch`.

**Exits.** Every broker read the run makes itself, the collection, both
reconciliations and the planning step's `assets` reads go through one path: an
exception there takes the halt path, as does any `SystemFaultError` or
`StaleDataError`. Every halt goes through `RiskGatedBroker.halt` (T60), which
writes the `engaged` row (not for `StaleDataError`), the cancels, the alert and
the `halted` or `stale` result row, and re-raises. Any other exception (a
`PlanTrialError`, a stub's `NotImplementedError`, a store error) writes the
`run_failed` alert and the `failed` result row, best-effort, and propagates;
that path never runs after a halt, whose result row stands, so a stale halt
does not engage the switch for the next run.

**Connections.** Writes go through `connect` (a write chunk each,
`store.db.open_for_write`); the run's own reads use short read-only
connections. None is held across a writer or a halt, and none across a broker
call but one: the planning step holds its write chunk, with its transaction
open, across its second `assets_read` (the targets not held), as T63c's
contract requires; the override form waits for that call. Every row the run
writes itself is stamped by the wrapper's checked clock, which also checks that
the clock never goes back.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any, TypeVar, cast
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner import calendar
from tradepartner.adapters.broker import Asset, Broker, canonical_symbol
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import all_sessions, is_session, next_session, previous_session
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import (
    ClockError,
    ReconciliationError,
    RejectionCapError,
    StaleDataError,
    SystemFaultError,
)
from tradepartner.execution import exits, marks, planning, switch
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.collect import (
    Collected,
    Connect,
    WriteOffContext,
    collect,
    lag_verdict,
)
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.lots import LedgerAccount
from tradepartner.execution.outcomes import write_outcomes_and_lots
from tradepartner.execution.plan import (
    DecisionState,
    RebalanceState,
    decision_state,
    rebalance_state,
)
from tradepartner.execution.reconcile import FILLS_LAGGING, MISMATCH, Mismatch, Reconciliation
from tradepartner.execution.reconcile import OK as RECONCILED
from tradepartner.execution.reconcile_run import frozen_risk, reconcile_now
from tradepartner.execution.risk import unfilled_sells
from tradepartner.execution.wrapper import (
    CRASH_EXIT_CODE,
    WRITE_FAILED_EXIT_CODE,
    BatchOutcome,
    RiskGatedBroker,
    Verdict,
    classify,
)
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.asof import listings_as_of, live_actions_as_of, prices_as_of
from tradepartner.store.db import open_read_only, utc_now
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    DecisionRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
    ReconciliationRow,
    adjustments_for,
    append,
    decisions_for,
    fills_for,
    kill_switch_events_for,
    last_marked_session,
    non_terminal_orders,
    open_window,
    order_events_for,
    orders_for,
    pending_orders,
    positions_daily_for,
    rebalance_events_for,
    reconciliations_for,
    runs_for,
    window_stops_for,
)
from tradepartner.timeutil import ensure_tz_aware_utc

__all__ = [
    "INVOKED_BY_ENV",
    "RunOutcome",
    "StepContext",
    "exits_step",
    "invoked_by",
    "stop_step",
    "submit_window",
    "tracking_run",
    "trade_step",
]

#: The environment variable the scheduler's plist sets (spec "Env vars").
INVOKED_BY_ENV = "TRADEPARTNER_INVOKED_BY"
_SCHEDULER = "scheduler"
_TTY = "tty"

OK = "ok"
LOCKED = "locked"
NO_WINDOW = "no_window"
NO_SESSION = "no_session"
SKIPPED_KILL_SWITCH = "skipped_kill_switch"
#: Outcomes that exit 0; every other outcome, and every exception, is non-zero.
_CLEAN_EXITS = frozenset({OK, NO_SESSION, SKIPPED_KILL_SWITCH})
_FAILED = "failed"
_CRASHED = "crashed"
_STOP, _MARK = "stop", "mark"
_REQUESTED = "requested"
_EXECUTED = "executed"
_MISSED = "missed"
_FORCED_EXIT = "forced_exit"
_LAGGING = "lagging"
_UNSPENT_CASH = "unspent_cash"
_RUN_WRITER = "run"
_DRAWDOWN = "drawdown"
_INGEST_OK = "ok"
#: The lot ledger's account (spec req 13: Phase 4 holds one account, paper).
_ACCOUNT_TYPE, _ACCOUNT_OWNER = "paper", "self"
_LAG_BOUND_KIND = "fill_lag_bound"
_NEW_YORK = ZoneInfo("America/New_York")
_T = TypeVar("_T")


@dataclass(frozen=True)
class RunOutcome:
    """What a run that did not raise ended as: `ok`, `skipped_kill_switch`,
    `no_session`, `no_window` or `locked`, with its run (None for the last two),
    session, kind and the notes on its result row."""

    status: str
    run_id: int | None = None
    session: date | None = None
    kind: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        """0 for `ok`, `no_session` and `skipped_kill_switch`, else `CRASH_EXIT_CODE`
        (same code an uncaught exception exits with; this outcome returned rather
        than raised, but is not the halt path's write-failure case, which exits
        `WRITE_FAILED_EXIT_CODE` instead via `SystemExit`, #515)."""
        return 0 if self.status in _CLEAN_EXITS else CRASH_EXIT_CODE


@dataclass(frozen=True)
class StepContext:
    """What the later steps (T63d, T63f) receive from the core: the run's
    wrapper (never the raw broker: a step reaches the broker only through
    `gate` and `assets_read`), the window and its frozen `risk` section, the run row,
    S, this run's `assets` read (symbol to `Asset`) and its reader, the
    planning outcome (None when nothing was planned), the `fills_lagging`
    state, and the run's notes (a step appends to them; they go on the result
    row)."""

    settings: Settings
    connect: Connect
    gate: RiskGatedBroker
    window: PaperWindowRow
    frozen: RiskConfig
    run: PaperRunRow
    session: date
    assets: Mapping[str, Asset]
    assets_read: planning.AssetsRead
    plan: planning.PlanOutcome | None
    lagging: bool
    notes: list[str] = field(default_factory=list)


def trade_step(context: StepContext) -> BatchOutcome | None:
    """Step 6's trading half on a rebalance or catch-up run (module
    docstring): inside the submit window, the plan's decisions and this
    session's forced exits handed to the wrapper as one batch. None when no
    batch was made (outside the window, or no plan under `fills_lagging`)."""
    plan = context.plan
    if plan is None or plan.status == _LAGGING:
        context.notes.append(
            "fills_lagging: nothing planned or traded; the rebalance stays pending"
        )
        return None
    if not _in_submit_window(context):
        return None
    forced = _forced_exits(context, pending_rebalance=plan.rebalance_session)
    if not _in_submit_window(context):  # the sells phase must start inside it
        return None
    return _execute(context, plan.decisions, forced)


def exits_step(context: StepContext) -> BatchOutcome | None:
    """The forced exits of a run with no rebalance due (module docstring): a
    batch of their own inside the submit window, None when there is none."""
    if context.lagging:
        context.notes.append("fills_lagging: no forced exit made")
        return None
    if not _in_submit_window(context):
        return None
    forced = _forced_exits(context, pending_rebalance=None)
    if not forced or not _in_submit_window(context):
        return None
    return _execute(context, (), forced)


def stop_step(context: StepContext) -> BatchOutcome | None:
    """The `stop` kind's steps after step 5 (T63f)."""
    raise NotImplementedError("the stop run lands with T63f")


def submit_window(settings: Settings, session: date) -> tuple[datetime, datetime]:
    """[open(S) - `paper.submit_window_before_open_minutes`, open(S) +
    `paper.submit_window_after_open_minutes`] (spec req 7 step 6)."""
    opening = calendar.session_open(session)
    paper = settings.paper
    return (
        opening - timedelta(minutes=paper.submit_window_before_open_minutes),
        opening + timedelta(minutes=paper.submit_window_after_open_minutes),
    )


def _in_submit_window(context: StepContext) -> bool:
    """Whether the batch may start now; a note on the run when it may not."""
    now = context.gate.read_clock()
    start, end = submit_window(context.settings, context.session)
    if start <= now <= end:
        return True
    context.notes.append(
        f"outside the submit window [{start.isoformat()}, {end.isoformat()}] at "
        f"{now.isoformat()}: nothing submitted"
    )
    return False


def _execute(
    context: StepContext, decisions: Sequence[DecisionRow], forced: Sequence[DecisionRow]
) -> BatchOutcome:
    """The wrapper's two phases; every exception it raises takes the halt path."""
    try:
        return context.gate.execute(context.run, decisions, forced)
    except Exception as exc:
        raise _Halt(exc) from exc


def _forced_exits(context: StepContext, *, pending_rebalance: date | None) -> list[DecisionRow]:
    """Step 7 (module docstring): the open forced exits to re-attempt and the
    new tradable ones, journaled; an untradable new one journaled closed."""
    session = context.session
    window_id = _window_id(context.window)
    cut = calendar.session_close(previous_session(session))
    with open_read_only(context.settings) as conn:
        actions = live_actions_as_of(conn, cut)
        journaled = decisions_for(conn, window_id)
        orders = orders_for(conn, window_id=None)
        events = order_events_for(conn, window_id=None)
        fills = fills_for(conn)
        adjustments = adjustments_for(conn, window_id)
        ok_rows = [
            r
            for r in reconciliations_for(conn, window_id)
            if r.status == RECONCILED and r.at.astimezone(_NEW_YORK).date() <= session
        ]
        ledger = from_journal(
            fills_for(conn, window_id=window_id),
            orders_for(conn, window_id=window_id),
            adjustments,
            actions,
            ok_rows[-1] if ok_rows else None,
            context.window.starting_cash,
            session,
            window_id=window_id,
            quantity_tolerance=context.frozen.reconcile_quantity_tolerance,
        )
        terminal = {e.client_order_id for e in events if e.status in TERMINAL_ORDER_STATUSES}
        names = (
            {d.decision.security_id for d in journaled}
            | set(ledger.positions)
            | {o.security_id for o in orders if o.client_order_id not in terminal}
        )
        prices = planning.reference_prices(conn, session, names, actions)
        held = sorted(name for name, quantity in ledger.positions.items() if quantity > 0)
        ends = (
            _current(
                listing_ends_as_of(conn, cut, context.settings, held), previous_session(session)
            )
            if held
            else {}
        )
        tickers = _current_tickers(listings_as_of(conn, cut, held), session) if held else {}
    price_of = _lookup(prices)
    states: dict[int, DecisionState] = {}
    for journaled_decision in journaled:
        decision = journaled_decision.decision
        assert decision.decision_id is not None
        states[decision.decision_id] = decision_state(
            decision,
            list(journaled_decision.events),
            orders,
            events,
            fills,
            actions,
            price_of,
            context.frozen,
            session=session,
        )
    decisions = [d.decision for d in journaled]
    reattempts = exits.reattempt_exits(decisions, states)
    assets = {
        sid: context.assets[canonical_symbol(ticker)]
        for sid, ticker in tickers.items()
        if canonical_symbol(ticker) in context.assets
    }
    new = exits.forced_exits(
        ledger.positions,
        {sid: row["end_session"] for sid, row in ends.items() if row["status"] == DELISTED},
        assets,
        decisions,
        states,
        unfilled_sells(orders, events, fills, price_of, actions, session=session),
        adjustments,
        session=session,
        pending_rebalance=pending_rebalance,
    )
    handed = list(reattempts)
    if new:
        stamp = context.gate.read_clock()
        with context.connect() as conn:
            for exit_ in new:
                row = exit_.row(run_id=_run_id(context.run), known_at=stamp, ingested_at=stamp)
                decision_id = append(conn, row)
                assert decision_id is not None
                event = exit_.event(
                    decision_id=decision_id,
                    run_id=_run_id(context.run),
                    known_at=stamp,
                    ingested_at=stamp,
                )
                if event is None:
                    handed.append(replace(row, decision_id=decision_id))
                else:
                    append(conn, event)
                    context.notes.append(
                        f"forced exit of {exit_.security_id} ({exit_.reason}) closed: "
                        f"{exit_.skipped_reason}"
                    )
    return handed


def _run_id(run: PaperRunRow) -> int:
    if run.run_id is None:
        raise ValueError("a run row without a run_id")
    return run.run_id


def _lookup(prices: Mapping[str, float]) -> Callable[[str], float]:
    def price_of(security_id: str) -> float:
        if security_id not in prices:
            raise ValueError(f"no reference price read for {security_id}")
        return prices[security_id]

    return price_of


def invoked_by(environ: Mapping[str, str], *, stdin_is_tty: bool) -> str:
    """`scheduler` when `TRADEPARTNER_INVOKED_BY=scheduler` and stdin is not a
    TTY, else `tty` (spec req 7 step 9)."""
    if environ.get(INVOKED_BY_ENV) == _SCHEDULER and not stdin_is_tty:
        return _SCHEDULER
    return _TTY


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class _Halt(Exception):
    """Carries an exception from the broker-facing path to the halt path."""

    def __init__(self, fault: Exception) -> None:
        super().__init__(f"{type(fault).__name__}: {fault}")
        self.fault = fault


class _ChunkAlerter(Alerter):
    """An `Alerter` that opens one write chunk per alert, so no connection is
    held across a run (the wrapper's halt and the run's own alerts use it)."""

    def __init__(self, settings: Settings, connect: Connect, clock: Callable[[], datetime]):
        # The base's connection is never used: `write` builds an Alerter per chunk.
        super().__init__(settings, cast(duckdb.DuckDBPyConnection, None), clock)
        self._chunk = connect
        self._chunk_settings = settings
        self._chunk_clock = clock

    def write(
        self,
        kind: str,
        run_id: int | None,
        session: date,
        message: str,
        *,
        clock_fault: bool = False,
    ) -> int:
        """`Alerter.write` in a write chunk of its own."""
        with self._chunk() as conn:
            alerter = Alerter(self._chunk_settings, conn, self._chunk_clock)
            return alerter.write(kind, run_id, session, message, clock_fault=clock_fault)

    def scrub(self, text: str) -> str:
        """`text` with every configured secret masked: the `Alerter`'s own
        masking, so the run's result rows and notes share it."""
        return self._scrub(text)


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    """One clock reading, tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
    try:
        reading = clock()
        if not isinstance(reading, datetime):
            raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
        return ensure_tz_aware_utc(reading, field_name="clock")
    except Exception as exc:
        raise ClockError(f"clock failed: {type(exc).__name__}") from exc


def _alert_session(now: datetime) -> date:
    """The calendar session containing `now`, or the next one on a non-session
    day: the dedupe session of `locked` and `no_window` (spec req 7)."""
    day = now.astimezone(_NEW_YORK).date()
    return day if is_session(day) else next_session(day)


def _window_id(window: PaperWindowRow) -> int:
    if window.window_id is None:
        raise ValueError("a paper_windows row without a window_id")
    return window.window_id


@contextmanager
def _no_transaction(connect: Connect) -> Iterator[duckdb.DuckDBPyConnection]:
    """A write chunk with its transaction ended, for `planning.plan_rebalance`,
    which opens and commits its own; a fresh one is begun again on the way out
    so the chunk's own commit or rollback has one to end."""
    with connect() as conn:
        conn.commit()
        try:
            yield conn
        finally:
            with suppress(duckdb.TransactionException):
                conn.begin()


def tracking_run(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> RunOutcome:
    """`paper run` (module docstring). `connect` opens a write chunk
    (`lambda: store.db.open_for_write(settings)`); `clock` is the clock the
    broker adapter holds; `sleep` waits between polls, the wrapper's and step
    7b's (tests advance a fake clock). Returns the outcome of a run that ended `ok`,
    `skipped_kill_switch`, `no_session`, `no_window` or `locked`; a halt
    re-raises its fault after the halt path, any other failure propagates
    after the `failed` result row, and a `kill_switch` row that cannot be
    written exits with `SystemExit`."""
    with ExitStack() as stack:
        try:
            stack.enter_context(run_lock(settings))
        except LockHeld as exc:  # only taking the lock: never a LockHeld from the run
            session = _alert_session(_read_clock(clock))
            alerter = _ChunkAlerter(settings, connect, clock)
            alerter.write(LOCKED, None, session, alerter.scrub(f"paper run not started: {exc}"))
            return RunOutcome(LOCKED, session=session)
        return _locked_run(settings, connect, broker, clock, sleep)


def _locked_run(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> RunOutcome:
    now = _read_clock(clock)
    with open_read_only(settings) as conn:
        window = open_window(conn)
    if window is None:
        session = _alert_session(now)
        _ChunkAlerter(settings, connect, clock).write(
            NO_WINDOW, None, session, "paper run: no paper window is open"
        )
        return RunOutcome(NO_WINDOW, session=session)
    window_id = _window_id(window)
    day = now.astimezone(_NEW_YORK).date()
    who = invoked_by(os.environ, stdin_is_tty=_stdin_is_tty())
    version, dirty = registry.code_version()
    if not is_session(day):
        with connect() as conn:
            run_id = append(
                conn,
                PaperRunRow(
                    window_id=window_id,
                    started_at=now,
                    invoked_by=who,
                    code_version=version,
                    code_dirty=dirty,
                    known_at=now,
                    ingested_at=now,
                ),
            )
            assert run_id is not None
            append(
                conn,
                PaperRunResultRow(
                    run_id=run_id,
                    finished_at=now,
                    status=NO_SESSION,
                    message=f"{day.isoformat()} is not a session",
                    clock_fault=False,
                    known_at=now,
                    ingested_at=now,
                ),
            )
        return RunOutcome(NO_SESSION, run_id=run_id)

    frozen = frozen_risk(window)
    alerter = _ChunkAlerter(settings, connect, clock)
    gate = RiskGatedBroker(broker, clock, frozen, settings, connect, calendar, alerter, sleep=sleep)
    engaged = switch.engage_from_overrides(
        settings, gate.read_clock, window_id=window_id, run_id=None
    )
    if isinstance(engaged, switch.WriteFailed):
        alerter.deliver_without_store(
            "kill_switch_write_failed",
            f"paper run on {day.isoformat()}: an engage_kill_switch override could not be "
            f"engaged ({engaged.error})",
        )
        raise SystemExit(WRITE_FAILED_EXIT_CODE)

    with open_read_only(settings) as conn:
        runs = runs_for(conn, window_id)
        rows = kill_switch_events_for(conn, window_id)
        events = rebalance_events_for(conn, window_id)
        stops = window_stops_for(conn, window_id)
    run_rows = [r.run for r in runs]
    state = switch.derive(
        window,
        rows,
        run_rows,
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    due = _rebalance_kind(window, run_rows, events, day)
    kind = _STOP if any(s.state == _REQUESTED for s in stops) else (due or _MARK)

    stamp = gate.read_clock()
    with connect() as conn:
        row = PaperRunRow(
            window_id=window_id,
            session=day,
            kind=kind,
            started_at=stamp,
            invoked_by=who,
            code_version=version,
            code_dirty=dirty,
            known_at=stamp,
            ingested_at=stamp,
        )
        run_id = append(conn, row)
        assert run_id is not None
        for earlier in runs:
            if earlier.result is None and earlier.run.run_id is not None:
                append(
                    conn,
                    PaperRunResultRow(
                        run_id=earlier.run.run_id,
                        finished_at=stamp,
                        status=_CRASHED,
                        message=f"closed by paper run {run_id}",
                        clock_fault=False,
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
    run = replace(row, run_id=run_id)
    return _Run(
        settings, connect, broker, gate, alerter, window, frozen, run, state.engaged, sleep
    ).execute()


class _Run:
    """One run after its `paper_runs` row (module docstring, steps 1 to 9)."""

    def __init__(
        self,
        settings: Settings,
        connect: Connect,
        broker: Broker,
        gate: RiskGatedBroker,
        alerter: _ChunkAlerter,
        window: PaperWindowRow,
        frozen: RiskConfig,
        run: PaperRunRow,
        engaged: bool,
        sleep: Callable[[float], None],
    ) -> None:
        assert run.run_id is not None and run.session is not None and run.kind is not None
        self.settings = settings
        self.connect = connect
        self.broker = broker
        self.gate = gate
        self.alerter = alerter
        self.window = window
        self.window_id = _window_id(window)
        self.frozen = frozen
        self.run = run
        self.run_id: int = run.run_id
        self.session: date = run.session
        self.kind: str = run.kind
        self.engaged = engaged
        self.notes: list[str] = []
        self.write_offs: WriteOffContext | None = None
        self.assets: dict[str, Asset] = {}
        self.sleep = sleep

    # --- exits ---------------------------------------------------------------------

    def execute(self) -> RunOutcome:
        try:
            return self._steps()
        except _Halt as carried:
            self.gate.halt(carried.fault, self.run, write_offs=self.write_offs)
        except (SystemFaultError, StaleDataError) as fault:
            self.gate.halt(fault, self.run, write_offs=self.write_offs)
        except Exception as exc:
            self._fail(exc)
            raise

    def _fail(self, exc: Exception) -> None:
        """The `run_failed` alert, then the `failed` result row, best-effort: a
        failure of either is noted on the exception, which still propagates."""
        clock_fault = isinstance(exc, ClockError)
        message = self.alerter.scrub(f"{type(exc).__name__}: {exc}")
        problems: list[str] = []
        try:
            self.alerter.write(
                "run_failed", self.run_id, self.session, message, clock_fault=clock_fault
            )
        except Exception as alert_error:
            problems.append(f"run_failed alert not written ({type(alert_error).__name__})")
        try:
            finished, clock_fault = self._stamp(clock_fault)
            with self.connect() as conn:
                append(
                    conn,
                    PaperRunResultRow(
                        run_id=self.run_id,
                        finished_at=finished,
                        status=_FAILED,
                        fault_type=type(exc).__name__,
                        message=self.alerter.scrub("; ".join([message, *problems, *self.notes])),
                        clock_fault=clock_fault,
                        known_at=finished,
                        ingested_at=finished,
                    ),
                )
        except Exception as write_error:
            problems.append(f"result row not written ({type(write_error).__name__})")
        if problems:
            exc.add_note(self.alerter.scrub("failed path: " + "; ".join(problems)))

    def _stamp(self, clock_fault: bool) -> tuple[datetime, bool]:
        if not clock_fault:
            try:
                return self.gate.read_clock(), False
            except ClockError:
                pass
        return utc_now(), True

    def _finish(self, status: str) -> RunOutcome:
        finished = self.gate.read_clock()
        notes = tuple(dict.fromkeys(self.notes))
        with self.connect() as conn:
            append(
                conn,
                PaperRunResultRow(
                    run_id=self.run_id,
                    finished_at=finished,
                    status=status,
                    message=self.alerter.scrub("; ".join(notes)) or None,
                    clock_fault=False,
                    known_at=finished,
                    ingested_at=finished,
                ),
            )
        return RunOutcome(status, self.run_id, self.session, self.kind, notes)

    # --- the broker-facing path --------------------------------------------------------

    def _halting(self, step: Callable[[], _T]) -> _T:
        """Run a broker-facing step; any exception it raises takes the halt path
        (every read allowlist is empty, req 4)."""
        try:
            return step()
        except _Halt:
            raise
        except Exception as exc:
            raise _Halt(exc) from exc

    def _broker_call(self, method: str, call: Callable[[], _T]) -> _T:
        """One broker read, its exception classified by the wrapper's allowlist."""
        try:
            return call()
        except Exception as exc:
            if classify(method, exc) is not Verdict.HALT:
                raise  # an allowlisted exception: none exists for a read (req 4)
            raise _Halt(exc) from exc

    def assets_read(self, symbols: Sequence[str]) -> dict[str, Asset]:
        """This run's `assets` read: answered from what the run has read, the rest
        read from the broker through the same classified path."""
        missing = sorted({s for s in symbols if s not in self.assets})
        if missing:
            read = self._broker_call(Broker.assets.__name__, lambda: self.broker.assets(missing))
            self.assets.update(read)
        return {s: self.assets[s] for s in symbols if s in self.assets}

    def _alert(self, kind: str, message: str) -> None:
        self.alerter.write(kind, self.run_id, self.session, self.alerter.scrub(message))

    # --- the steps -----------------------------------------------------------------

    def _steps(self) -> RunOutcome:
        account = self._broker_call(Broker.account.__name__, self.broker.account)
        if account.account_id != self.window.account_id:
            raise ReconciliationError(
                f"account_id: the broker's account {account.account_id!r} is not window "
                f"{self.window_id}'s {self.window.account_id!r}"
            )
        self._staleness()
        actions = self._actions()
        prices = self._pending_prices(actions)
        self.write_offs = WriteOffContext(self.window_id, actions, prices.__getitem__, self.session)
        collected = self._collect()
        self._executed(actions, prices)
        reconciliation = self._reconcile()
        ledger = self._ledger_at(self.session, actions)
        self.assets_read(list(self._symbols(sorted(ledger.positions)).values()))
        self._mark(actions)
        self._lapses()
        self._outcomes(actions)
        if self.engaged:
            return self._finish(SKIPPED_KILL_SWITCH)

        lagging = bool(collected.lagging) or reconciliation.status == FILLS_LAGGING
        context = StepContext(
            settings=self.settings,
            connect=self.connect,
            gate=self.gate,
            window=self.window,
            frozen=self.frozen,
            run=self.run,
            session=self.session,
            assets=self.assets,
            assets_read=self.assets_read,
            plan=None,
            lagging=lagging,
            notes=self.notes,
        )
        rebalance: date | None = None
        if self.kind == _STOP:
            batch = stop_step(context)
        elif self._due() is not None:
            plan = self._plan(lagging)
            rebalance = plan.rebalance_session
            # The halt path's write-off back-fill prices every name now decided.
            self.write_offs = WriteOffContext(
                self.window_id, actions, self._pending_prices(actions).__getitem__, self.session
            )
            batch = trade_step(replace(context, plan=plan))
        else:
            batch = exits_step(context)
        if batch is not None:
            self._collect_own()
            prices = self._pending_prices(actions)
            self.write_offs = WriteOffContext(
                self.window_id, actions, prices.__getitem__, self.session
            )
            executed = self._executed(actions, prices)
            if rebalance is not None and rebalance in executed:
                self._unspent_cash(batch, rebalance)
        self._reconcile()
        return self._finish(OK if batch is None else batch.status)

    def _staleness(self) -> None:
        """Step 2 (module docstring)."""
        now = self.gate.read_clock()
        before = previous_session(self.session)
        symbol = self.settings.ingest.reference_symbol
        with open_read_only(self.settings) as conn:
            row = conn.execute(
                "SELECT max(finished_at) FROM ingestion_runs WHERE status = ? AND finished_at <= ?",
                [_INGEST_OK, now],
            ).fetchone()
            finished = None if row is None or row[0] is None else row[0]
            if finished is None:
                raise StaleDataError(
                    f"no ok ingestion run finished by {now.isoformat()}: {symbol} is not "
                    f"covered for {before.isoformat()}"
                )
            finished = ensure_tz_aware_utc(finished, field_name="finished_at")
            reference = _current_ids(listings_as_of(conn, finished), before, symbol)
            bars = (
                prices_as_of(conn, finished, reference).filter(pl.col("session") == before)
                if reference
                else pl.DataFrame()
            )
        if bars.is_empty():
            raise StaleDataError(
                f"the last ok ingestion run (finished {finished.isoformat()}) does not "
                f"cover {before.isoformat()} for {symbol}"
            )

    def _cut(self) -> datetime:
        return calendar.session_close(previous_session(self.session))

    def _actions(self) -> pl.DataFrame:
        with open_read_only(self.settings) as conn:
            return live_actions_as_of(conn, self._cut())

    def _pending_prices(self, actions: pl.DataFrame) -> dict[str, float]:
        """Reference prices on S for every name decided in a pending rebalance:
        the write-off back-fill's and the `executed` test's `price_of`."""
        with open_read_only(self.settings) as conn:
            settled = {e.rebalance_session for e in rebalance_events_for(conn, self.window_id)}
            names = {
                d.decision.security_id
                for d in decisions_for(conn, self.window_id)
                if d.decision.rebalance_session is not None
                and d.decision.rebalance_session not in settled
            }
            return planning.reference_prices(conn, self.session, names, actions)

    def _collect(self) -> Collected:
        """Step 3's collection, the rejection cap and the lag bound."""
        with open_read_only(self.settings) as conn:
            pending = {o.client_order_id for o in pending_orders(conn, window_id=self.window_id)}
            orders = [
                o
                for o in non_terminal_orders(conn, window_id=self.window_id)
                if o.client_order_id not in pending
            ]
        collected = self._halting(
            lambda: collect(
                self.broker,
                self.connect,
                orders,
                self.gate.read_clock,
                _RUN_WRITER,
                self.run_id,
                self.frozen,
                self.settings,
                write_offs=self.write_offs,
            )
        )
        if collected.rejections:
            raise RejectionCapError("; ".join(b.message for b in collected.rejections))
        self._lag_bound(collected)
        return collected

    def _lag_bound(self, collected: Collected) -> None:
        if not collected.lagging:
            return
        with open_read_only(self.settings) as conn:
            history = reconciliations_for(conn, self.window_id)
        past = [
            reading
            for reading in collected.lagging
            if lag_verdict(reading.order, history, self.session, self.frozen).breached
        ]
        if not past:
            return
        bound = self.frozen.max_fill_lag_sessions
        report = Reconciliation(
            status=MISMATCH,
            broker_cash=None,
            mismatches=tuple(
                Mismatch(
                    _LAG_BOUND_KIND,
                    f"fills_lagging at or past risk.max_fill_lag_sessions ({bound})",
                    security_id=r.order.security_id,
                    symbol=r.order.symbol,
                    client_order_id=r.order.client_order_id,
                )
                for r in past
            ),
            lagging_ids=tuple(sorted(r.order.client_order_id for r in collected.lagging)),
            pending_ids=(),
            adjustments=(),
        )
        stamp = self.gate.read_clock()
        with self.connect() as conn:
            reconciliation_id = append(
                conn,
                ReconciliationRow(
                    window_id=self.window_id,
                    run_id=self.run_id,
                    at=stamp,
                    status=MISMATCH,
                    mismatches_json=report.mismatches_json,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
        ids = ", ".join(r.order.client_order_id for r in past)
        text = (
            f"reconciliation {reconciliation_id}: {ids} still fills_lagging at or past "
            f"{bound} session(s)"
        )
        if self.engaged:
            self.notes.append(f"{text}; reported, the switch already engaged")
            return
        raise ReconciliationError(text)

    def _executed(self, actions: pl.DataFrame, prices: Mapping[str, float]) -> list[date]:
        """The `executed` test after step 3 and after step 7b (module
        docstring); returns the rebalances it wrote `executed` for."""
        if fill_session(self.window.first_rebalance_session) > self.session:
            return []
        with open_read_only(self.settings) as conn:
            run_rows = [r.run for r in runs_for(conn, self.window_id)]
            events = rebalance_events_for(conn, self.window_id)
            decisions = decisions_for(conn, self.window_id)
            orders = orders_for(conn, window_id=self.window_id)
            order_events = order_events_for(conn, window_id=self.window_id)
            fills = fills_for(conn, window_id=self.window_id)
        settled = {e.rebalance_session for e in events}
        executed: list[date] = []
        for t_i in rebalance_sessions(self.window.first_rebalance_session, self.session):
            if fill_session(t_i) > self.session or t_i in settled:
                continue
            mine = [
                d
                for d in decisions
                if d.decision.rebalance_session == t_i and d.decision.decision != _FORCED_EXIT
            ]
            if not mine:
                continue
            states = [
                (
                    d.decision,
                    decision_state(
                        d.decision,
                        list(d.events),
                        orders,
                        order_events,
                        fills,
                        actions,
                        prices.__getitem__,
                        self.frozen,
                        session=self.session,
                    ),
                )
                for d in mine
            ]
            verdict = rebalance_state(
                t_i, self.window, run_rows, events, states, session=self.session
            )
            if verdict is RebalanceState.EXECUTED:
                executed.append(t_i)
        if executed:
            stamp = self.gate.read_clock()
            with self.connect() as conn:
                for t_i in executed:
                    append(
                        conn,
                        RebalanceEventRow(
                            rebalance_session=t_i,
                            run_id=self.run_id,
                            status=_EXECUTED,
                            known_at=stamp,
                            ingested_at=stamp,
                        ),
                    )
        return executed

    def _collect_own(self) -> None:
        """Step 7b's collection (module docstring): this run's acknowledged
        orders until each is terminal or `paper.accept_wait_seconds` has passed
        since the step began."""
        paper = self.settings.paper
        deadline = self.gate.read_clock() + timedelta(seconds=paper.accept_wait_seconds)
        while True:
            with open_read_only(self.settings) as conn:
                pending = {
                    o.client_order_id for o in pending_orders(conn, window_id=self.window_id)
                }
                orders = [
                    o
                    for o in non_terminal_orders(conn, window_id=self.window_id)
                    if o.run_id == self.run_id and o.client_order_id not in pending
                ]
            if not orders:
                return
            collected = self._halting(partial(self._collect_orders, orders))
            if collected.rejections:
                raise RejectionCapError("; ".join(b.message for b in collected.rejections))
            now = self.gate.read_clock()
            if now >= deadline:
                with open_read_only(self.settings) as conn:
                    still = sorted(
                        o.client_order_id
                        for o in non_terminal_orders(conn, window_id=self.window_id)
                        if o.run_id == self.run_id and o.client_order_id not in pending
                    )
                if still:
                    self.notes.append(
                        f"still open after paper.accept_wait_seconds ({paper.accept_wait_seconds}"
                        f"s): {', '.join(still)}"
                    )
                return
            self.sleep(min(paper.poll_interval_seconds, (deadline - now).total_seconds()))

    def _collect_orders(self, orders: Sequence[OrderRow]) -> Collected:
        return collect(
            self.broker,
            self.connect,
            orders,
            self.gate.read_clock,
            _RUN_WRITER,
            self.run_id,
            self.frozen,
            self.settings,
            write_offs=self.write_offs,
        )

    def _unspent_cash(self, batch: BatchOutcome, rebalance: date) -> None:
        """The `unspent_cash` alert at `executed` (module docstring)."""
        if batch.cash_left is None:
            return
        account = self._broker_call(Broker.account.__name__, self.broker.account)
        bound = self.frozen.max_unspent_cash_fraction * account.equity
        if batch.cash_left > bound:
            self._alert(
                _UNSPENT_CASH,
                f"rebalance {rebalance.isoformat()} executed with {batch.cash_left:.2f} cash "
                f"unspent, above risk.max_unspent_cash_fraction "
                f"{self.frozen.max_unspent_cash_fraction} of equity {account.equity:.2f}",
            )

    def _reconcile(self) -> Reconciliation:
        """Steps 4 and 8. The journal cut (`as_of`, #488) is a clock reading
        taken just before the call: step 3 has already journaled this run's
        collected fills and terminal events, stamped after close(S-1), and a
        ledger cut at close(S-1) would drop them while the broker holds them
        (#488's corrected guidance). Store facts stay cut at close(S-1)."""
        as_of = self.gate.read_clock()
        return self._halting(
            lambda: reconcile_now(
                self.settings,
                self.connect,
                self.broker,
                self.window,
                self.session,
                self.gate.read_clock,
                self.connect,
                self.run_id,
                frozen=self.frozen,
                as_of=as_of,
            )
        )

    def _ledger_for(self, actions: pl.DataFrame) -> Callable[[date], Ledger]:
        """The window's ledger stated through a session, from one journal read."""
        with open_read_only(self.settings) as conn:
            fills = fills_for(conn, window_id=self.window_id)
            orders = orders_for(conn, window_id=self.window_id)
            adjustments = adjustments_for(conn, self.window_id)
            ok_rows = [
                r for r in reconciliations_for(conn, self.window_id) if r.status == RECONCILED
            ]

        def ledger(through: date) -> Ledger:
            stated = [r for r in ok_rows if r.at.astimezone(_NEW_YORK).date() <= through]
            return from_journal(
                fills,
                orders,
                adjustments,
                actions,
                stated[-1] if stated else None,
                self.window.starting_cash,
                through,
                window_id=self.window_id,
                quantity_tolerance=self.frozen.reconcile_quantity_tolerance,
            )

        return ledger

    def _ledger_at(self, through: date, actions: pl.DataFrame) -> Ledger:
        return self._ledger_for(actions)(through)

    def _symbols(self, names: Sequence[str]) -> dict[str, str]:
        """Each name's ticker on its listing current at S, known at close(S-1)."""
        if not names:
            return {}
        with open_read_only(self.settings) as conn:
            listings = listings_as_of(conn, self._cut(), list(names))
        return _current_tickers(listings, self.session)

    def _start_day(self) -> date:
        return self.window.started_at.astimezone(_NEW_YORK).date()

    def _mark_sessions(self, last: date | None) -> list[date]:
        sessions = all_sessions()
        first = (
            bisect.bisect_right(sessions, last)
            if last
            else bisect.bisect_left(sessions, self._start_day())
        )
        through = bisect.bisect_right(sessions, previous_session(self.session))
        return list(sessions[first:through])

    def _mark(self, actions: pl.DataFrame) -> None:
        """Step 5's marks, then the drawdown check and the `missed_run` alert."""
        ledger = self._ledger_for(actions)
        with open_read_only(self.settings) as conn:
            sessions = self._mark_sessions(last_marked_session(conn, self.window_id))
            held = {name for day in sessions for name in ledger(day).positions}
            symbols = (
                _current_tickers(listings_as_of(conn, self._cut(), sorted(held)), self.session)
                if held
                else {}
            )
            flags = {
                name: self.assets[symbol].tradable
                for name, symbol in symbols.items()
                if symbol in self.assets
            }
            rows = marks.marks_for(conn, self.window, ledger, sessions, flags)
        if rows:
            stamp = self.gate.read_clock()
            with self.connect() as conn:
                for mark in rows:
                    append(
                        conn,
                        PositionDailyRow(
                            run_id=self.run_id,
                            session=mark.session,
                            security_id=mark.security_id,
                            quantity=mark.quantity,
                            mark_price=mark.mark_price,
                            value=mark.value,
                            cash=mark.cash,
                            tradable=mark.tradable,
                            known_at=stamp,
                            ingested_at=stamp,
                        ),
                    )
        self._drawdown({mark.session for mark in rows})
        with open_read_only(self.settings) as conn:
            run_rows = [r.run for r in runs_for(conn, self.window_id)]
        boundary = previous_session(self.session)
        if boundary > self._start_day() and marks.missed_run(run_rows, self.session):
            self._alert("missed_run", f"no paper run on {boundary.isoformat()}")

    def _drawdown(self, marked: set[date]) -> None:
        """The drawdown check on each session this run `marked` and on the last
        marked session, in session order; the first crossing engages, once."""
        with open_read_only(self.settings) as conn:
            marks_rows = positions_daily_for(conn, self.window_id)
            rows = kill_switch_events_for(conn, self.window_id)
        if not marks_rows:
            return
        peak = switch.drawdown_peak(self.window, rows)
        armed = switch.drawdown_armed(self.window_id, rows)
        crossed: tuple[date, float] | None = None
        for day in sorted(marked | {max(r.session for r in marks_rows)}):
            equity = _mark_equity(marks_rows, day)
            if equity is not None and switch.drawdown_check(
                equity, peak, self.frozen.max_drawdown, armed=armed
            ):
                crossed = day, equity
                break
        if crossed is None:
            return
        day, equity = crossed
        reason = (
            f"ledger equity {equity:.2f} at {day.isoformat()} is below the peak {peak:.2f} "
            f"by more than risk.max_drawdown {self.frozen.max_drawdown}"
        )
        engaged = switch.engage(
            self.settings,
            self.gate.read_clock,
            window_id=self.window_id,
            source=_DRAWDOWN,
            reason=reason,
            run_id=self.run_id,
        )
        if isinstance(engaged, switch.WriteFailed):
            raise SystemFaultError(f"the drawdown engagement could not be written: {engaged.error}")
        self.engaged = True
        self._alert(_DRAWDOWN, reason)

    def _lapses(self) -> None:
        """The lapse rows from `marks.lapses`, with one `missed_rebalance` alert."""
        with open_read_only(self.settings) as conn:
            runs = runs_for(conn, self.window_id)
            events = rebalance_events_for(conn, self.window_id)
            rows = kill_switch_events_for(conn, self.window_id)
        frozen_values: dict[str, Any] = json.loads(self.window.frozen_json)
        missed = marks.lapses(
            self.window,
            [r.run for r in runs],
            events,
            rows,
            [r.result for r in runs if r.result is not None],
            self.session,
            frozen_values,
        )
        if not missed:
            return
        stamp = self.gate.read_clock()
        with self.connect() as conn:
            for lapse in missed:
                append(
                    conn,
                    RebalanceEventRow(
                        rebalance_session=lapse.rebalance_session,
                        run_id=self.run_id,
                        status=_MISSED,
                        reason=lapse.reason,
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
        self._alert(
            "missed_rebalance",
            "; ".join(
                f"rebalance {m.rebalance_session.isoformat()} missed ({m.reason})" for m in missed
            ),
        )

    def _outcomes(self, actions: pl.DataFrame) -> None:
        """The due outcomes and the lot-ledger write (T62). A lot-ledger error is
        a `lot_ledger` alert and a note; it never fails the run."""
        with open_read_only(self.settings) as conn:
            names = sorted({o.security_id for o in orders_for(conn, window_id=self.window_id)})
            bars = prices_as_of(conn, self._cut(), names) if names else pl.DataFrame()
        closes = {
            (row["security_id"], row["session"]): float(row["close"])
            for row in bars.iter_rows(named=True)
        }

        def price(security_id: str, day: date) -> float | None:
            return closes.get((security_id, day))

        errors: list[str] = []
        with self.connect() as conn:
            write_outcomes_and_lots(
                conn,
                self.window_id,
                LedgerAccount(self.window.account_id, _ACCOUNT_TYPE, _ACCOUNT_OWNER),
                self.assets,
                price,
                self.session,
                self.gate.read_clock,
                on_lot_error=errors.append,
                actions=actions,
            )
        if errors:
            message = self.alerter.scrub("lot ledger not rebuilt: " + "; ".join(errors))
            self.notes.append(message)
            self._alert("lot_ledger", message)

    def _due(self) -> planning.RebalanceKind | None:
        with open_read_only(self.settings) as conn:
            run_rows = [r.run for r in runs_for(conn, self.window_id)]
            events = rebalance_events_for(conn, self.window_id)
        return _rebalance_kind(self.window, run_rows, events, self.session)

    def _plan(self, lagging: bool) -> planning.PlanOutcome:
        now = self.gate.read_clock()
        with _no_transaction(self.connect) as conn:
            return planning.plan_rebalance(
                conn,
                store_journal,
                self.window,
                self.run,
                self.session,
                self.settings,
                self.frozen,
                self.assets_read,
                lagging,
                now=now,
            )


def _rebalance_kind(
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    events: Sequence[RebalanceEventRow],
    session: date,
) -> planning.RebalanceKind | None:
    """`planning.rebalance_kind` with the window's frozen catch-up bound; None
    before the window's first rebalance session, a session that function
    refuses (its schedule needs a start on or before the session)."""
    if session < window.first_rebalance_session:
        return None
    return planning.rebalance_kind(
        window, runs, events, session, planning.frozen_max_catch_up_sessions(window)
    )


def _current(listings: pl.DataFrame, day: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from` on or before `day`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        valid_from = row["valid_from"]
        if valid_from is not None and valid_from > day:
            continue
        held = current.get(row["security_id"])
        if held is None or (
            valid_from is not None
            and (held["valid_from"] is None or valid_from > held["valid_from"])
        ):
            current[row["security_id"]] = row
    return current


def _current_tickers(listings: pl.DataFrame, day: date) -> dict[str, str]:
    return {sid: row["ticker"] for sid, row in _current(listings, day).items()}


def _current_ids(listings: pl.DataFrame, day: date, ticker: str) -> list[str]:
    """The securities whose listing current on `day` has `ticker`."""
    return sorted(sid for sid, row in _current(listings, day).items() if row["ticker"] == ticker)


def _mark_equity(rows: Sequence[PositionDailyRow], day: date) -> float | None:
    """Ledger equity at the marked session `day`: its cash row plus every name's
    value, or None when that session's rows cannot give it."""
    today = [r for r in rows if r.session == day]
    cash = {r.cash for r in today if r.cash is not None}
    values = [r.value for r in today if r.security_id is not None]
    if len(cash) != 1 or any(v is None for v in values):
        return None
    equity = cash.pop() + math.fsum(v for v in values if v is not None)
    return equity if math.isfinite(equity) else None

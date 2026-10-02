"""Exit-criteria check (Phase 4 spec req 15; plan T66).

`check(conn, settings) -> list[CheckLine]` runs, over a single read
connection to the real store, the four req 15 queries against the
**latest** `paper_windows` row (`store.journal.latest_window`), open or
closed (a closed window is checked through its last completed rebalance
session strictly before its stop session, `report()`'s own "last completed
T" rule, spec req 15 and req 10). Every threshold it compares against comes
from the window's `frozen_json` (Definitions, "Paper window": "runs and
checks read the frozen values, never live `Settings`"); `settings` is taken
only so a setup failure can name the store it looked in, never for a
threshold. `check` never reads the lot ledger or its wash-sale flags (spec
req 15, last sentence): only the tables `store.journal` and
`execution.report` already read.

The four `CheckLine`s, in order:

1. **`rebalance_count`**: distinct `rebalance_events.rebalance_session` rows
   with `status = 'executed'` whose writing run has `invoked_by = 'scheduler'`,
   counted once however many `executed` rows name it (two `ok` runs on one
   fill session count once); a session whose only `executed` row came from a
   tty-invoked run, and a scheduler run of kind `rebalance` or `catch_up`
   that wrote no `executed` row of its own (it traded nothing), are both
   named in the detail text but never counted. Passes at `paper.min_rebalances`
   or more.
2. **`tracking`**: the req 10 check, replayed from the window's latest
   `paper_reports` row's trial (`report.compare_months`, the same pure
   function `paper report` calls) over at least `paper.min_rebalances`
   compared, non-excluded months; fails as `report stale` unless that row's
   `through_session` is the window's last completed rebalance session.
3. **`chain`**: no order whose chain (signal -> decision -> order ->
   terminal event -> outcome, ADR 0005) is incomplete once its outcome is
   due: at the first run after close(T_{i+1}) for an order of rebalance i,
   or after the order's own session for a forced exit with no rebalance
   (spec req 8's last sentence).
4. **`override_reason`**: no `overrides` row whose trimmed `reason` is
   shorter than `paper.min_override_reason_chars`.

Each `CheckLine.query` names the query in words, so the owner can see what
ran without reading this module.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.calendar import is_session, last_session_of_month, next_session, session_close
from tradepartner.config import Settings
from tradepartner.execution.outcomes import NOT_EXECUTED, POSITION_RETURN, REALISED_PNL
from tradepartner.execution.report import Journal, PriceOf, TrialMonths, compare_months
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import utc_now
from tradepartner.store.journal import PaperWindowRow

_NEW_YORK = ZoneInfo("America/New_York")
_EXECUTED = "executed"
_REQUESTED = "requested"
_SCHEDULER = "scheduler"
_FILLED = "filled"
_BUY = "buy"
_SELL = "sell"
_REBALANCE = "rebalance"
_CATCH_UP = "catch_up"
_EXIT_PHASE = "exit"
_PAPER_MIN_REBALANCES = "paper.min_rebalances"
_PAPER_MIN_OVERRIDE_REASON_CHARS = "paper.min_override_reason_chars"
_EXECUTION_FILL_PRICE = "execution.fill_price"


@dataclass(frozen=True, kw_only=True)
class CheckLine:
    """One req 15 check's result. `query` names, in words, what it ran against
    the store; `detail` carries the counts or the names of whatever it found,
    pass or fail."""

    name: str
    passed: bool
    query: str
    detail: str


def _frozen(window: PaperWindowRow) -> dict[str, Any]:
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    return cast(dict[str, Any], parsed)


def _int_key(window: PaperWindowRow, frozen: dict[str, Any], key: str) -> int:
    value = frozen.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"window {window.window_id} frozen_json has no usable {key}: {value!r}")
    return value


def _frozen_fill_price(window: PaperWindowRow) -> Literal["close", "open"]:
    value = _frozen(window).get(_EXECUTION_FILL_PRICE)
    if value not in ("close", "open"):
        raise ValueError(f"window {window.window_id} {_EXECUTION_FILL_PRICE} is {value!r}")
    return cast(Literal["close", "open"], value)


def _local_date(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _last_completed_rebalance_session(now: datetime) -> date:
    """The latest rebalance session (last XNYS session of a month) whose close
    is at or before `now` (`report._last_completed_rebalance_session`, spec
    req 10's "last completed T", duplicated locally: plan T58's conftest
    docstring reserves shared edits for the task that owns a file, and this
    one is `report.py`'s)."""
    today = _local_date(now)
    year, month = today.year, today.month
    while True:
        candidate = last_session_of_month(year, month)
        if candidate <= today and session_close(candidate) <= now:
            return candidate
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)


def _rebalance_session_before(session: date) -> date:
    """The last rebalance session strictly before `session` (spec req 15: "a
    closed window is checked through its last completed rebalance session
    before the stop session"; `report._before`, duplicated locally)."""
    year, month = session.year, session.month
    while True:
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        candidate = last_session_of_month(year, month)
        if candidate < session:
            return candidate


def _next_rebalance_session(session: date) -> date:
    """The first rebalance session strictly after `session`: T_{i+1} for T_i
    (Definitions)."""
    year, month = session.year, session.month
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return last_session_of_month(year, month)


def _stop_session(at: datetime) -> date:
    """spec req 9: the calendar session containing `at`, or the next session
    when `at` falls on a non-session day (`report._stop_session`)."""
    day = _local_date(at)
    return day if is_session(day) else next_session(day)


def _stop_session_of(stops: Sequence[store_journal.PaperWindowStopRow]) -> date | None:
    """The stop session of the window's `requested` stop, or None when the
    window was never asked to stop (`report._stop_session_of`)."""
    requested = [s for s in stops if s.state == _REQUESTED]
    return _stop_session(min(s.at for s in requested)) if requested else None


def _price_of(
    store: duckdb.DuckDBPyConnection, fill_price_key: Literal["close", "open"]
) -> PriceOf:
    def price(security_id: str, session: date) -> float | None:
        frame = prices_as_of(store, session_close(session), [security_id]).filter(
            pl.col("session") == session
        )
        if frame.is_empty():
            return None
        return float(frame[fill_price_key][0])

    return price


def _trial_months(conn: duckdb.DuckDBPyConnection, trial_id: int) -> TrialMonths:
    """`TrialMonths` for `trial_id`'s base cost level (`report._trial_months`,
    duplicated locally)."""
    found = conn.execute(
        "SELECT hypothesis_id, start_session, end_session FROM trials WHERE trial_id = ?",
        [trial_id],
    ).fetchone()
    if found is None:
        raise ValueError(f"no trial {trial_id}")
    hypothesis_id, start_session, end_session = found
    hypothesis = registry.get_hypothesis_by_id(conn, hypothesis_id)
    base_level = float(hypothesis.params[registry.BASE_COST_KEY])
    sessions = tuple(rebalance_sessions(start_session, end_session))
    equity_rows = conn.execute(
        "SELECT session, equity FROM trial_equity "
        "WHERE trial_id = ? AND series = 'strategy' AND cost_per_side_bps = ?",
        [trial_id, base_level],
    ).fetchall()
    equity = {session: float(value) for session, value in equity_rows}
    cost_rows = conn.execute(
        "SELECT session, cost_paid FROM trial_rebalances "
        "WHERE trial_id = ? AND cost_per_side_bps = ?",
        [trial_id, base_level],
    ).fetchall()
    cost_paid = {session: float(value) for session, value in cost_rows}
    return TrialMonths(
        sessions=sessions,
        equity={session: equity[session] for session in sessions if session in equity},
        cost_paid=cost_paid,
        trial_id=trial_id,
    )


def _journal_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> Journal:
    with_events = store_journal.decisions_for(conn, window_id)
    return Journal(
        positions_daily=store_journal.positions_daily_for(conn, window_id),
        adjustments=store_journal.adjustments_for(conn, window_id),
        decisions=tuple(d.decision for d in with_events),
        decision_events=tuple(e for d in with_events for e in d.events),
        orders=store_journal.orders_for(conn, window_id=window_id),
        fills=store_journal.fills_for(conn, window_id=window_id),
        runs=tuple(rw.run for rw in store_journal.runs_for(conn, window_id)),
        rebalance_events=store_journal.rebalance_events_for(conn, window_id),
        plans=store_journal.plans_for(conn, window_id),
    )


def _rebalance_count_line(
    conn: duckdb.DuckDBPyConnection, window: PaperWindowRow, window_id: int, frozen: dict[str, Any]
) -> CheckLine:
    query = (
        "distinct rebalance_events.rebalance_session with status='executed' "
        "whose writing paper_runs row has invoked_by='scheduler'"
    )
    min_rebalances = _int_key(window, frozen, _PAPER_MIN_REBALANCES)
    events = store_journal.rebalance_events_for(conn, window_id)
    runs = {rw.run.run_id: rw.run for rw in store_journal.runs_for(conn, window_id)}

    executed = [e for e in events if e.status == _EXECUTED]
    scheduler_sessions = {
        e.rebalance_session
        for e in executed
        if e.run_id in runs and runs[e.run_id].invoked_by == _SCHEDULER
    }
    all_executed_sessions = {e.rebalance_session for e in executed}
    tty_only_sessions = sorted(all_executed_sessions - scheduler_sessions)
    executed_run_ids = {e.run_id for e in executed}
    no_trade_runs = sorted(
        run.session
        for run in runs.values()
        if run.session is not None
        and run.invoked_by == _SCHEDULER
        and run.kind in (_REBALANCE, _CATCH_UP)
        and run.run_id not in executed_run_ids
    )

    count = len(scheduler_sessions)
    passed = count >= min_rebalances
    sessions_text = ", ".join(s.isoformat() for s in sorted(scheduler_sessions))
    detail = (
        f"{count} scheduler-executed rebalance session(s) [{sessions_text}], "
        f"need >= {min_rebalances}"
    )
    if tty_only_sessions:
        tty_text = ", ".join(s.isoformat() for s in tty_only_sessions)
        detail += f"; tty-executed, listed not counted: [{tty_text}]"
    if no_trade_runs:
        no_trade_text = ", ".join(s.isoformat() for s in no_trade_runs)
        detail += f"; no-trade fill-session runs, listed not counted: [{no_trade_text}]"
    return CheckLine(name="rebalance_count", passed=passed, query=query, detail=detail)


def _tracking_line(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    window_id: int,
    frozen: dict[str, Any],
    last_t: date,
    stop_session: date | None,
) -> CheckLine:
    query = (
        "req 10 tracking check (report.compare_months) over the window's "
        "latest paper_reports row's trial"
    )
    min_rebalances = _int_key(window, frozen, _PAPER_MIN_REBALANCES)
    reports = store_journal.paper_reports_for(conn, window_id)
    if not reports:
        return CheckLine(
            name="tracking", passed=False, query=query, detail="no paper_reports row yet"
        )
    latest_report = max(reports, key=lambda r: r.run_at)
    if latest_report.through_session != last_t:
        return CheckLine(
            name="tracking",
            passed=False,
            query=query,
            detail=(
                f"report stale: through_session {latest_report.through_session} "
                f"!= the last completed rebalance session {last_t}"
            ),
        )

    fill_price_key = _frozen_fill_price(window)
    trial = _trial_months(conn, latest_report.trial_id)
    journal = _journal_for(conn, window_id)
    actions = conn.execute("SELECT * FROM corporate_actions").pl()
    prices = _price_of(conn, fill_price_key)
    comparison = compare_months(window, trial, journal, actions, prices, stop_session)
    compared = [m for m in comparison.months if not m.excluded]
    enough = len(compared) >= min_rebalances
    passed = comparison.passed and enough
    detail = f"{len(compared)} compared non-excluded month(s), need >= {min_rebalances}; " + (
        "tracking check passed"
        if comparison.passed
        else f"tracking check failed at {comparison.failing_month}"
    )
    return CheckLine(name="tracking", passed=passed, query=query, detail=detail)


def _rebalance_before(session: date) -> date:
    """The last rebalance session (last session of a month) at or before
    `session` (`outcomes._rebalance_before`, duplicated locally)."""
    candidate = last_session_of_month(session.year, session.month)
    if candidate < session:
        return candidate
    year, month = (session.year, session.month - 1) if session.month > 1 else (session.year - 1, 12)
    return last_session_of_month(year, month)


def _order_due_threshold(
    order: store_journal.OrderRow, decision: store_journal.DecisionRow | None
) -> date:
    """The session an order's outcome becomes due strictly after
    (`outcomes._horizon`'s non-stop `base`, duplicated locally): for a `phase
    = exit` order (a forced exit's own phase, spec req 8's "exit session"),
    its own session; otherwise T_{i+1} of its rebalance (the decision's
    `rebalance_session` when it has one, else the rebalance at or before the
    order's own session - `outcomes.py` falls back the same way for an order
    whose decision carries none, a forced exit traded inside a rebalance
    batch with phase `sell`, ADR 0010 amendment 2026-10-01)."""
    if order.phase == _EXIT_PHASE:
        return order.session
    rebalance = (
        decision.rebalance_session
        if decision is not None and decision.rebalance_session is not None
        else _rebalance_before(order.session)
    )
    return _next_rebalance_session(rebalance)


def _chain_line(conn: duckdb.DuckDBPyConnection, window_id: int) -> CheckLine:
    query = (
        "every order's chain (order -> terminal event -> outcome) once its "
        "outcome is due (spec req 8)"
    )
    orders = store_journal.orders_for(conn, window_id=window_id)
    decisions = {
        d.decision.decision_id: d.decision for d in store_journal.decisions_for(conn, window_id)
    }
    run_sessions = sorted(
        r.run.session for r in store_journal.runs_for(conn, window_id) if r.run.session is not None
    )
    open_ids = {
        o.client_order_id for o in store_journal.non_terminal_orders(conn, window_id=window_id)
    }
    terminal: dict[str, str] = {}
    for event in sorted(
        store_journal.order_events_for(conn, window_id=window_id),
        key=lambda e: (e.known_at, e.ingested_at),
    ):
        if event.status in store_journal.TERMINAL_ORDER_STATUSES:
            terminal.setdefault(event.client_order_id, event.status)
    filled: dict[str, float] = {}
    for item in store_journal.fills_for(conn, window_id=window_id):
        coid = item.fill.client_order_id
        filled[coid] = filled.get(coid, 0.0) + item.fill.quantity
    written = {(o.client_order_id, o.kind) for o in store_journal.outcomes_for(conn, window_id)}

    incomplete: list[str] = []
    for order in sorted(orders, key=lambda o: o.client_order_id):
        decision = decisions.get(order.decision_id)
        threshold = _order_due_threshold(order, decision)
        due = any(session > threshold for session in run_sessions)
        if not due:
            continue
        coid = order.client_order_id
        if coid in open_ids or coid not in terminal:
            incomplete.append(f"order {coid} ({order.symbol}) is not terminal")
            continue
        earned: list[str] = []
        if filled.get(coid, 0.0) > 0 and order.side == _BUY:
            earned.append(POSITION_RETURN)
        if filled.get(coid, 0.0) > 0 and order.side == _SELL:
            earned.append(REALISED_PNL)
        if terminal[coid] != _FILLED:
            earned.append(NOT_EXECUTED)
        incomplete += [
            f"order {coid} ({order.symbol}) has no {kind} outcome"
            for kind in earned
            if (coid, kind) not in written
        ]
    passed = not incomplete
    detail = "no incomplete chain once due" if passed else "; ".join(incomplete)
    return CheckLine(name="chain", passed=passed, query=query, detail=detail)


def _override_line(
    conn: duckdb.DuckDBPyConnection, window: PaperWindowRow, window_id: int, frozen: dict[str, Any]
) -> CheckLine:
    query = "overrides.reason, trimmed, against the frozen paper.min_override_reason_chars"
    min_chars = _int_key(window, frozen, _PAPER_MIN_OVERRIDE_REASON_CHARS)
    violations = [
        f"override {o.override.override_id} ({o.override.kind}) reason trimmed to "
        f"{len(o.override.reason.strip())} char(s), need >= {min_chars}"
        for o in store_journal.overrides_for(conn, window_id)
        if len(o.override.reason.strip()) < min_chars
    ]
    passed = not violations
    detail = "every override reason meets the frozen minimum" if passed else "; ".join(violations)
    return CheckLine(name="override_reason", passed=passed, query=query, detail=detail)


def check(conn: duckdb.DuckDBPyConnection, settings: Settings) -> list[CheckLine]:
    """`paper check` (spec req 15): the four exit-criteria `CheckLine`s against
    the **latest** `paper_windows` row, open or closed. Raises `ValueError`
    when no window exists or the window's `frozen_json` lacks a key a query
    needs."""
    window = store_journal.latest_window(conn)
    if window is None:
        raise ValueError(f"no paper window exists in {settings.store.path}")
    window_id = window.window_id
    if window_id is None:
        raise ValueError(f"the window in {settings.store.path} has no window_id")

    stops = store_journal.window_stops_for(conn, window_id)
    stop_session = _stop_session_of(stops)
    last_t = _last_completed_rebalance_session(utc_now())
    while stop_session is not None and last_t >= stop_session:
        last_t = _rebalance_session_before(last_t)

    frozen = _frozen(window)

    return [
        _rebalance_count_line(conn, window, window_id, frozen),
        _tracking_line(conn, window, window_id, frozen, last_t, stop_session),
        _chain_line(conn, window_id),
        _override_line(conn, window, window_id, frozen),
    ]

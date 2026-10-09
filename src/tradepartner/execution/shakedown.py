"""The machine-readiness shakedown (ADR 0017 part E; paper spec req 15 as amended
2026-10-09; paper-trading plan T157b).

`shakedown(conn, settings, now=...) -> Shakedown` runs, over a single read
connection to the real store, the seven criteria E.1 to E.7 over **every book**
and returns one `ShakedownLine` per criterion. It is pure over the connection
(the `check.py` pattern): it only reads, so the command that calls it opens the
store read-only and writes nothing. `paper shakedown` exits 0 only when every
line passes.

**The span.** The newest `owner_decisions` row of kind `shakedown_span`
(`registry.shakedown_span`) opens it and carries the two thresholds, `sessions`
(N) and `order_sessions` (M): they are read from that row, never from
`Settings`, so the bar cannot move after a failing span. The span is every XNYS
session from the session after the row's `made_at` (its New York date) through
the last completed session (`calendar.last_completed_session(now)`). A
`reconciliations` row with `status = 'mismatch'`, or a `paper_run_results` row
with `status = 'halted'`, written after the row restarts the span at the session
after the first `kill_switch` row with `state = 'released'` of the same window
that follows it (or that window's closing stop after it, since a closed window
never trades again); until that row exists the span is **pending** (no sessions) and
starts at the restart event itself, so E.2 and the rest still see it. With
several restart events the latest restart wins. The span's instant lower bound
(`Span.start_at`) is New York midnight of its first session (or the earliest
unreleased restart event while pending): every timestamped row (`reconciliations.at`,
`kill_switch.at`, `alert_deliveries.at`, `paper_run_results.finished_at`) is in
the span when it is at or after it, and every dated row (`paper_runs.session`,
`orders.session`, `alerts.session`) when its date is on or after the span's
first day.

**Which windows count.** A window (a book's) is open on a span session S when its
`started_at` falls on a New York date before S and it has no closing stop
(`closed`, `abandoned`) on a New York date at or before S: the day a window opens
and the day it closes may hold a run or not, so neither is required (ADR 0017:
"a book opened or stopped inside the span counts for the sessions it was open").

**The seven lines** (each names its rows, its query and the thresholds it read):

1. **`E.1 sessions`**: for every span session and every window open on it, a
   scheduler-invoked `paper_runs` row of that window and session whose
   `paper_run_results.status` is `ok`; or `skipped_kill_switch` when the run
   started between an owner `engaged` row and the first `released` row after it in
   that window (write order) and wrote no `orders` row (the E.4 drill: an
   engagement never released excuses nothing); or `stale` with a `shakedown_note`
   naming a `stale_data` alert of that run. A session with no scheduler row passes
   only when the book's `missed_run` alert for it (`alerts.session` is the next
   session: `run.py` alerts S-1 from the run on S) has a `shakedown_note`; the
   count is printed. Across all books, at least M span sessions on which a
   scheduler-invoked run submitted an order with a live fill (`fills_for`, which
   hides superseded rows). The span must hold at least N sessions and not be
   pending.
2. **`E.2 reconciliation`**: no `mismatch` row in the span; each
   `pending_unresolved` or `fills_lagging` row's next reconciliation of the same
   book (by `at`, then id) exists and is `ok`.
3. **`E.3 orders`**: (duplicate) for every decision with an order in the span,
   its orders' live fills sum to at most `planned_quantity` plus the decision
   window's frozen `risk.reconcile_quantity_tolerance` (a decision with a
   `planned_quantity`), else to at most `planned_notional` (quantity times price)
   plus the frozen `risk.reconcile_cash_tolerance`; (mis-sized) every span
   order's live fills sum to at most its `quantity` plus the quantity tolerance,
   else its `notional` plus the cash tolerance; (breach) no span
   `paper_run_results` row with `fault_type = 'LimitBreachError'`.
4. **`E.4 kill-switch drill`**: in at least one window, an owner `engaged`
   `kill_switch` row in the span, then a scheduler-invoked run of kind
   `rebalance` or `catch_up` started before the next `released` row whose result
   is `skipped_kill_switch` and which wrote no `orders` row, then that `released`
   row. A drilled run that wrote an `orders` row does not count and is named.
   Non-owner `engaged` rows in the span are printed as evidence, not required.
5. **`E.5 journal`**: `paper check`'s `chain` and `override_reason` lines
   (`check._chain_line`, `check._override_line`, the same functions) pass for
   every window not closed before the span.
6. **`E.6 alerts`**: an `alert_deliveries` row with `ok = TRUE` in the span for
   every non-store channel in `alerts.channels` (the channel list is the live
   config, the one the alerter delivers to; it is not a threshold).
7. **`E.7 data`**: every `stale_data` alert in the span has a `shakedown_note`,
   and `health.health_report` at `now` is `ok` (`tradepartner health --check`'s
   rule; it reads the live `Settings` exactly as that command does).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import last_completed_session, next_session, previous_session
from tradepartner.config import Settings
from tradepartner.execution.check import _chain_line, _frozen, _override_line
from tradepartner.execution.window import window_cadence
from tradepartner.health import health_report
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.db import utc_now
from tradepartner.store.journal import PaperWindowRow

_NEW_YORK = ZoneInfo("America/New_York")
_SCHEDULER = "scheduler"
_OK = "ok"
_SKIPPED_KILL_SWITCH = "skipped_kill_switch"
_STALE = "stale"
_HALTED = "halted"
_MISMATCH = "mismatch"
_LAGGING_STATUSES = ("pending_unresolved", "fills_lagging")
_ENGAGED = "engaged"
_RELEASED = "released"
_OWNER = "owner"
_DRILL_KINDS = ("rebalance", "catch_up")
_STALE_DATA = "stale_data"
_MISSED_RUN = "missed_run"
_STORE_CHANNEL = "store"
_LIMIT_BREACH = "LimitBreachError"
_QUANTITY_TOLERANCE = "risk.reconcile_quantity_tolerance"
_CASH_TOLERANCE = "risk.reconcile_cash_tolerance"
#: How many failing items a line names before it says "and n more".
_NAMED = 10


@dataclass(frozen=True, kw_only=True)
class ShakedownLine:
    """One criterion's verdict: the rows it read, its query in words, the
    thresholds it compared against and what it found, pass or fail."""

    name: str
    passed: bool
    rows: str
    query: str
    thresholds: str
    detail: str


@dataclass(frozen=True, kw_only=True)
class Span:
    """The span the criteria read (module docstring, "The span")."""

    decision_id: int
    opened_at: datetime
    sessions_needed: int
    order_sessions_needed: int
    start: date | None
    start_at: datetime
    sessions: tuple[date, ...]
    restart: str | None

    @property
    def first_day(self) -> date:
        """The first day dated rows are read from: the first session, or the
        restart event's New York date while the span is pending."""
        return self.start if self.start is not None else _local_date(self.start_at)

    def describe(self) -> str:
        """The span in words, for E.1's detail."""
        opened = f"span from decision {self.decision_id} ({self.opened_at.isoformat()})"
        if self.start is None:
            return f"{opened}: pending, {self.restart}"
        restarted = f", restarted after {self.restart}" if self.restart else ""
        last = self.sessions[-1].isoformat() if self.sessions else "none completed"
        count = len(self.sessions)
        return f"{opened}{restarted}: {self.start.isoformat()}..{last}, {count} session(s)"


@dataclass(frozen=True, kw_only=True)
class Shakedown:
    """`shakedown`'s result: the span and the seven lines, E.1 to E.7 in order."""

    span: Span
    lines: tuple[ShakedownLine, ...]

    @property
    def passed(self) -> bool:
        """True only when every line passes."""
        return all(line.passed for line in self.lines)


@dataclass(frozen=True, kw_only=True)
class _Run:
    run_id: int
    window_id: int
    session: date
    kind: str
    started_at: datetime
    invoked_by: str
    status: str | None
    fault_type: str | None


@dataclass(frozen=True, kw_only=True)
class _Switch:
    event_id: int
    window_id: int
    at: datetime
    state: str
    source: str


@dataclass(frozen=True, kw_only=True)
class _Alert:
    alert_id: int
    run_id: int | None
    session: date
    kind: str
    book_id: str
    at: datetime


def _local_date(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=_NEW_YORK)


def _named(items: Sequence[str]) -> str:
    shown = "; ".join(items[:_NAMED])
    more = len(items) - _NAMED
    return shown + (f"; and {more} more" if more > 0 else "")


def _sessions_between(first: date, last: date) -> tuple[date, ...]:
    out: list[date] = []
    day = first
    while day <= last:
        out.append(day)
        day = next_session(day)
    return tuple(out)


# --- reads ----------------------------------------------------------------------


def _windows(conn: duckdb.DuckDBPyConnection) -> list[PaperWindowRow]:
    rows = conn.execute(
        "SELECT window_id, hypothesis_id, first_rebalance_session, account_id, starting_cash, "
        "starting_equity, code_version, started_at, frozen_json, frozen_sha256, book_id, "
        "known_at, ingested_at FROM paper_windows ORDER BY window_id"
    ).fetchall()
    return [
        PaperWindowRow(
            window_id=r[0],
            hypothesis_id=r[1],
            first_rebalance_session=r[2],
            account_id=r[3],
            starting_cash=r[4],
            starting_equity=r[5],
            code_version=r[6],
            started_at=r[7],
            frozen_json=r[8],
            frozen_sha256=r[9],
            book_id=r[10],
            known_at=r[11],
            ingested_at=r[12],
        )
        for r in rows
    ]


def _closed_at(conn: duckdb.DuckDBPyConnection) -> dict[int, datetime]:
    """Each window's first closing stop (`closed` or `abandoned`)."""
    rows = conn.execute(
        'SELECT window_id, MIN("at") FROM paper_window_stops WHERE list_contains(?, state) '
        "GROUP BY window_id",
        [list(store_journal.CLOSING_STOP_STATES)],
    ).fetchall()
    return {int(w): at for w, at in rows}


def _runs(conn: duckdb.DuckDBPyConnection, first_day: date) -> list[_Run]:
    rows = conn.execute(
        "SELECT r.run_id, r.window_id, r.session, r.kind, r.started_at, r.invoked_by, "
        "res.status, res.fault_type FROM paper_runs r "
        "LEFT JOIN paper_run_results res ON res.run_id = r.run_id "
        "WHERE r.session >= ? ORDER BY r.run_id",
        [first_day],
    ).fetchall()
    return [
        _Run(
            run_id=r[0],
            window_id=r[1],
            session=r[2],
            kind=r[3],
            started_at=r[4],
            invoked_by=r[5],
            status=r[6],
            fault_type=r[7],
        )
        for r in rows
    ]


def _switches(conn: duckdb.DuckDBPyConnection) -> list[_Switch]:
    rows = conn.execute(
        'SELECT event_id, window_id, "at", state, source FROM kill_switch ORDER BY event_id'
    ).fetchall()
    return [_Switch(event_id=r[0], window_id=r[1], at=r[2], state=r[3], source=r[4]) for r in rows]


def _alerts(conn: duckdb.DuckDBPyConnection, first_day: date) -> list[_Alert]:
    rows = conn.execute(
        'SELECT alert_id, run_id, session, kind, book_id, "at" FROM alerts '
        "WHERE session >= ? ORDER BY alert_id",
        [first_day],
    ).fetchall()
    return [
        _Alert(alert_id=r[0], run_id=r[1], session=r[2], kind=r[3], book_id=r[4], at=r[5])
        for r in rows
    ]


def _noted_alerts(conn: duckdb.DuckDBPyConnection) -> set[int]:
    """The alert ids every `shakedown_note` row names."""
    rows = conn.execute(
        "SELECT values_json FROM owner_decisions WHERE kind = ?", [registry.SHAKEDOWN_NOTE_KIND]
    ).fetchall()
    return {int(json.loads(r[0])["alert_id"]) for r in rows}


def _ordered_runs(conn: duckdb.DuckDBPyConnection) -> set[int]:
    """Run ids that wrote at least one `orders` row."""
    return {int(r[0]) for r in conn.execute("SELECT DISTINCT run_id FROM orders").fetchall()}


# --- the span -------------------------------------------------------------------


def _restart_events(
    conn: duckdb.DuckDBPyConnection, after: datetime, now: datetime
) -> list[tuple[int, datetime, str]]:
    """(window_id, at, what) for every `mismatch` reconciliation and `halted` run
    result written after `after` and at or before `now`."""
    mismatches = conn.execute(
        'SELECT window_id, "at", reconciliation_id FROM reconciliations '
        'WHERE status = ? AND "at" > ? AND "at" <= ?',
        [_MISMATCH, after, now],
    ).fetchall()
    halts = conn.execute(
        "SELECT r.window_id, res.finished_at, r.run_id FROM paper_run_results res "
        "JOIN paper_runs r ON r.run_id = res.run_id "
        "WHERE res.status = ? AND res.finished_at > ? AND res.finished_at <= ?",
        [_HALTED, after, now],
    ).fetchall()
    return [
        (int(w), at, f"mismatch reconciliation {rid} ({at.isoformat()})")
        for w, at, rid in mismatches
    ] + [(int(w), at, f"halted run {rid} ({at.isoformat()})") for w, at, rid in halts]


def read_span(conn: duckdb.DuckDBPyConnection, now: datetime) -> Span:
    """The span (module docstring) at `now`. `ValueError` when no `shakedown_span`
    row exists."""
    return _read_span(conn, now, _switches(conn), _closed_at(conn))


def _read_span(
    conn: duckdb.DuckDBPyConnection,
    now: datetime,
    switch_rows: Sequence[_Switch],
    closed: dict[int, datetime],
) -> Span:
    row = registry.shakedown_span(conn)
    if row is None:
        raise ValueError(
            "no shakedown_span decision: open the span with "
            "`tradepartner decision shakedown-span --sessions N --order-sessions M --reason`"
        )
    start = next_session(_local_date(row.made_at))
    restart: str | None = None
    pending: list[tuple[datetime, str]] = []
    for window_id, at, what in _restart_events(conn, row.made_at, now):
        released = min(
            (
                s.at
                for s in switch_rows
                if s.window_id == window_id and s.state == _RELEASED and s.at > at
            ),
            default=None,
        )
        # a window closed or abandoned after the event never trades again: its
        # closing stop ends the restart as a release would
        ended = closed.get(window_id)
        if released is None and ended is not None and ended > at:
            released = ended
        if released is None:
            pending.append((at, what))
            continue
        candidate = next_session(_local_date(released))
        if candidate > start:
            start, restart = candidate, f"{what}, released {released.isoformat()}"
    if pending:
        at, what = min(pending)
        return Span(
            decision_id=row.decision_id,
            opened_at=row.made_at,
            sessions_needed=row.sessions,
            order_sessions_needed=row.order_sessions,
            start=None,
            start_at=at,
            sessions=(),
            restart=f"awaiting the released kill-switch row after {what}",
        )
    last = last_completed_session(now)
    return Span(
        decision_id=row.decision_id,
        opened_at=row.made_at,
        sessions_needed=row.sessions,
        order_sessions_needed=row.order_sessions,
        start=start,
        start_at=_midnight(start),
        sessions=_sessions_between(start, last) if start <= last else (),
        restart=restart,
    )


def _open_on(window: PaperWindowRow, closed: datetime | None, session: date) -> bool:
    if _local_date(window.started_at) >= session:
        return False
    return closed is None or _local_date(closed) > session


# --- E.1 ------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _Bracket:
    """An owner `engaged` row and the first `released` row after it in the same
    window (write order): the only interval a skipped run is a drill in."""

    window_id: int
    engaged: _Switch
    released: _Switch


def _brackets(switches: Iterable[_Switch]) -> list[_Bracket]:
    by_window: dict[int, list[_Switch]] = {}
    for s in switches:
        by_window.setdefault(s.window_id, []).append(s)
    out: list[_Bracket] = []
    for window_id, rows in by_window.items():
        for index, engaged in enumerate(rows):
            if engaged.state != _ENGAGED or engaged.source != _OWNER:
                continue
            released = next((s for s in rows[index + 1 :] if s.state == _RELEASED), None)
            if released is not None:
                out.append(_Bracket(window_id=window_id, engaged=engaged, released=released))
    return out


def _drill_bracket(run: _Run, brackets: Iterable[_Bracket]) -> _Bracket | None:
    """The released owner bracket a `skipped_kill_switch` scheduler run lies in."""
    if run.invoked_by != _SCHEDULER or run.status != _SKIPPED_KILL_SWITCH:
        return None
    return next(
        (
            b
            for b in brackets
            if b.window_id == run.window_id and b.engaged.at < run.started_at < b.released.at
        ),
        None,
    )


def _sessions_line(
    conn: duckdb.DuckDBPyConnection,
    span: Span,
    windows: Sequence[PaperWindowRow],
    closed: dict[int, datetime],
    runs: Sequence[_Run],
    brackets: Sequence[_Bracket],
    ordered: set[int],
    alerts: Sequence[_Alert],
    noted: set[int],
) -> ShakedownLine:
    rows = "paper_runs, paper_run_results, kill_switch, alerts, owner_decisions, orders, fills"
    query = (
        "per span session and open window: a scheduler run whose result is ok, "
        "skipped_kill_switch inside a released owner engagement with no order, or stale "
        "with a noted stale_data alert; no run only with a noted missed_run alert; "
        "and distinct sessions whose scheduler run submitted an order with a live fill"
    )
    thresholds = (
        f"sessions >= {span.sessions_needed}, order sessions >= {span.order_sessions_needed} "
        f"(shakedown_span decision {span.decision_id})"
    )
    failures: list[str] = []
    by_note = 0
    if span.start is None:
        failures.append("span pending")
    elif len(span.sessions) < span.sessions_needed:
        failures.append(f"{len(span.sessions)} span session(s), need >= {span.sessions_needed}")
    in_span = set(span.sessions)
    for window in windows:
        assert window.window_id is not None
        wid = window.window_id
        for session in span.sessions:
            if not _open_on(window, closed.get(wid), session):
                continue
            mine = [
                r
                for r in runs
                if r.window_id == wid and r.session == session and r.invoked_by == _SCHEDULER
            ]
            if any(r.status == _OK for r in mine):
                continue
            if any(
                _drill_bracket(r, brackets) is not None and r.run_id not in ordered for r in mine
            ):
                continue
            if any(
                r.status == _STALE
                and any(
                    a.run_id == r.run_id and a.kind == _STALE_DATA and a.alert_id in noted
                    for a in alerts
                )
                for r in mine
            ):
                by_note += 1
                continue
            if not mine:
                missed = [
                    a
                    for a in alerts
                    if a.kind == _MISSED_RUN
                    and a.book_id == window.book_id
                    and previous_session(a.session) == session
                ]
                if any(a.alert_id in noted for a in missed):
                    by_note += 1
                    continue
                failures.append(
                    f"book {window.book_id} window {wid} {session}: no scheduler run"
                    + ("" if missed else " and no missed_run alert")
                    + " with a shakedown_note"
                )
                continue
            statuses = ", ".join(f"run {r.run_id} {r.status or 'unfinished'}" for r in mine)
            failures.append(f"book {window.book_id} window {wid} {session}: {statuses}")
    filled = {item.fill.client_order_id for item in store_journal.fills_for(conn)}
    filled_runs = {
        int(run_id)
        for run_id, coid in conn.execute("SELECT run_id, client_order_id FROM orders").fetchall()
        if coid in filled
    }
    order_sessions = sorted(
        {
            r.session
            for r in runs
            if r.invoked_by == _SCHEDULER and r.session in in_span and r.run_id in filled_runs
        }
    )
    if len(order_sessions) < span.order_sessions_needed:
        failures.append(
            f"{len(order_sessions)} order session(s), need >= {span.order_sessions_needed}"
        )
    detail = (
        f"{span.describe()}; {len(order_sessions)} order session(s); "
        f"{by_note} session(s) passed by note"
    )
    if failures:
        detail += "; " + _named(failures)
    return ShakedownLine(
        name="E.1 sessions",
        passed=not failures,
        rows=rows,
        query=query,
        thresholds=thresholds,
        detail=detail,
    )


# --- E.2 ------------------------------------------------------------------------


def _reconciliation_line(conn: duckdb.DuckDBPyConnection, span: Span) -> ShakedownLine:
    rows = conn.execute(
        'SELECT reconciliation_id, book_id, "at", status FROM reconciliations '
        'ORDER BY book_id, "at", reconciliation_id'
    ).fetchall()
    failures: list[str] = []
    lagging = 0
    for index, (rid, book, at, status) in enumerate(rows):
        if at < span.start_at:
            continue
        if status == _MISMATCH:
            failures.append(f"mismatch reconciliation {rid} (book {book}, {at.isoformat()})")
        elif status in _LAGGING_STATUSES:
            lagging += 1
            following = next((r for r in rows[index + 1 :] if r[1] == book), None)
            if following is None:
                failures.append(f"{status} reconciliation {rid} (book {book}) has no next one yet")
            elif following[3] != _OK:
                failures.append(
                    f"{status} reconciliation {rid} (book {book}) is followed by "
                    f"{following[3]} reconciliation {following[0]}"
                )
    detail = f"{lagging} pending_unresolved/fills_lagging row(s) in the span"
    if failures:
        detail += "; " + _named(failures)
    return ShakedownLine(
        name="E.2 reconciliation",
        passed=not failures,
        rows="reconciliations",
        query=(
            "reconciliations at or after the span start: no status='mismatch'; each "
            "pending_unresolved or fills_lagging row's next row of its book is 'ok'"
        ),
        thresholds="mismatch rows = 0",
        detail=detail,
    )


# --- E.3 ------------------------------------------------------------------------


def _frozen_tolerance(window: PaperWindowRow, key: str) -> float:
    try:
        frozen: Any = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    value = frozen.get(key) if isinstance(frozen, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError(f"window {window.window_id} frozen_json has no usable {key}: {value!r}")
    return float(value)


def _orders_line(
    conn: duckdb.DuckDBPyConnection, span: Span, windows: dict[int, PaperWindowRow]
) -> ShakedownLine:
    run_window = {
        int(r): int(w)
        for r, w in conn.execute("SELECT run_id, window_id FROM paper_runs").fetchall()
    }
    quantity: dict[str, float] = {}
    notional: dict[str, float] = {}
    for item in store_journal.fills_for(conn):
        coid = item.fill.client_order_id
        quantity[coid] = quantity.get(coid, 0.0) + item.fill.quantity
        notional[coid] = notional.get(coid, 0.0) + item.fill.quantity * item.fill.price
    orders = store_journal.orders_for(conn, window_id=None)
    in_span = [o for o in orders if o.session >= span.first_day]

    def tolerances(run_id: int) -> tuple[float, float]:
        window = windows[run_window[run_id]]
        return _frozen_tolerance(window, _QUANTITY_TOLERANCE), _frozen_tolerance(
            window, _CASH_TOLERANCE
        )

    failures: list[str] = []
    for order in in_span:
        coid = order.client_order_id
        qty_tol, cash_tol = tolerances(order.run_id)
        if order.quantity is not None:
            if quantity.get(coid, 0.0) > order.quantity + qty_tol:
                failures.append(
                    f"order {coid} filled {quantity[coid]:g} shares > quantity {order.quantity:g}"
                )
        elif order.notional is not None and notional.get(coid, 0.0) > order.notional + cash_tol:
            failures.append(
                f"order {coid} filled {notional[coid]:.2f} > notional {order.notional:.2f}"
            )
    decision_ids = sorted({o.decision_id for o in in_span})
    by_decision: dict[int, list[str]] = {}
    for order in orders:
        by_decision.setdefault(order.decision_id, []).append(order.client_order_id)
    planned = {
        int(d): (run, pq, pn)
        for d, run, pq, pn in conn.execute(
            "SELECT decision_id, run_id, planned_quantity, planned_notional FROM decisions "
            "WHERE list_contains(?, decision_id)",
            [decision_ids],
        ).fetchall()
    }
    for decision_id in decision_ids:
        if decision_id not in planned:
            failures.append(f"decision {decision_id} has orders but no decisions row")
            continue
        run_id, planned_quantity, planned_notional = planned[decision_id]
        qty_tol, cash_tol = tolerances(run_id)
        coids = by_decision[decision_id]
        if planned_quantity is not None:
            got = sum(quantity.get(c, 0.0) for c in coids)
            if got > planned_quantity + qty_tol:
                failures.append(
                    f"decision {decision_id} filled {got:g} shares > planned {planned_quantity:g}"
                )
        elif planned_notional is not None:
            got = sum(notional.get(c, 0.0) for c in coids)
            if got > planned_notional + cash_tol:
                failures.append(
                    f"decision {decision_id} filled {got:.2f} > planned {planned_notional:.2f}"
                )
        else:
            got = sum(quantity.get(c, 0.0) for c in coids)
            if got > 0:
                failures.append(f"decision {decision_id} has live fills but no planned size")
    breaches = conn.execute(
        "SELECT run_id FROM paper_run_results WHERE fault_type = ? AND finished_at >= ? "
        "ORDER BY run_id",
        [_LIMIT_BREACH, span.start_at],
    ).fetchall()
    failures += [f"run {r[0]} failed with {_LIMIT_BREACH}" for r in breaches]
    detail = f"{len(in_span)} order(s) and {len(decision_ids)} decision(s) in the span"
    if failures:
        detail += "; " + _named(failures)
    return ShakedownLine(
        name="E.3 orders",
        passed=not failures,
        rows="decisions, orders, fills (live), paper_run_results",
        query=(
            "per span decision: its orders' live fills against planned_quantity or "
            "planned_notional; per span order: its live fills against quantity or notional; "
            "span run results with fault_type='LimitBreachError'"
        ),
        thresholds=f"each window's frozen {_QUANTITY_TOLERANCE} and {_CASH_TOLERANCE}",
        detail=detail,
    )


# --- E.4 ------------------------------------------------------------------------


def _drill_line(
    span: Span,
    windows: Sequence[PaperWindowRow],
    runs: Sequence[_Run],
    switches: Sequence[_Switch],
    brackets: Sequence[_Bracket],
    ordered: set[int],
) -> ShakedownLine:
    drills: list[str] = []
    spoiled: list[str] = []
    in_span = [s for s in switches if s.at >= span.start_at]
    books = {w.window_id: w.book_id for w in windows}
    spans = [b for b in brackets if b.engaged.at >= span.start_at]
    for run in runs:
        if run.kind not in _DRILL_KINDS or _drill_bracket(run, spans) is None:
            continue
        text = f"book {books.get(run.window_id)} run {run.run_id} on {run.session}"
        (spoiled if run.run_id in ordered else drills).append(text)
    halts = [
        f"window {s.window_id} {s.source} at {s.at.isoformat()}"
        for s in in_span
        if s.state == _ENGAGED and s.source != _OWNER
    ]
    detail = f"drill(s): {_named(drills)}" if drills else "no drill sequence in the span"
    if spoiled:
        detail += f"; drilled run(s) that wrote orders, not counted: {_named(spoiled)}"
    if halts:
        detail += f"; real engagements (evidence): {_named(halts)}"
    return ShakedownLine(
        name="E.4 kill-switch drill",
        passed=bool(drills),
        rows="kill_switch, paper_runs, paper_run_results, orders",
        query=(
            "in one window: an owner 'engaged' kill_switch row, then a scheduler "
            "rebalance/catch_up run with result skipped_kill_switch and no orders row, "
            "then a 'released' row"
        ),
        thresholds="drill sequences >= 1",
        detail=detail,
    )


# --- E.5 ------------------------------------------------------------------------


def _journal_line(
    conn: duckdb.DuckDBPyConnection,
    span: Span,
    windows: Sequence[PaperWindowRow],
    closed: dict[int, datetime],
) -> ShakedownLine:
    failures: list[str] = []
    checked = 0
    for window in windows:
        wid = window.window_id
        assert wid is not None
        if wid in closed and closed[wid] < span.start_at:
            continue
        checked += 1
        frozen = _frozen(window)
        for line in (
            _chain_line(conn, wid, window_cadence(conn, window)),
            _override_line(conn, window, wid, frozen),
        ):
            if not line.passed:
                failures.append(f"book {window.book_id} window {wid} {line.name}: {line.detail}")
    detail = f"{checked} window(s) checked"
    if failures:
        detail += "; " + _named(failures)
    return ShakedownLine(
        name="E.5 journal",
        passed=not failures,
        rows="orders, order_events, fills (live), outcomes, decisions, paper_runs, overrides",
        query=(
            "paper check's chain and override_reason lines for every window not closed "
            "before the span"
        ),
        thresholds="each window's frozen paper.min_override_reason_chars",
        detail=detail,
    )


# --- E.6 ------------------------------------------------------------------------


def _alerts_line(conn: duckdb.DuckDBPyConnection, span: Span, settings: Settings) -> ShakedownLine:
    channels = [c for c in settings.alerts.channels if c != _STORE_CHANNEL]
    delivered = {
        str(r[0])
        for r in conn.execute(
            'SELECT DISTINCT channel FROM alert_deliveries WHERE ok AND "at" >= ?',
            [span.start_at],
        ).fetchall()
    }
    missing = [c for c in channels if c not in delivered]
    detail = f"ok deliveries on {', '.join(sorted(delivered & set(channels))) or 'no channel'}" + (
        f"; no ok delivery on {', '.join(missing)}" if missing else ""
    )
    return ShakedownLine(
        name="E.6 alerts",
        passed=not missing,
        rows="alert_deliveries",
        query="distinct channels with an ok=TRUE alert_deliveries row at or after the span start",
        thresholds=f"every non-store channel of alerts.channels: {', '.join(channels)}",
        detail=detail,
    )


# --- E.7 ------------------------------------------------------------------------


def _data_line(
    conn: duckdb.DuckDBPyConnection,
    span: Span,
    settings: Settings,
    now: datetime,
    alerts: Sequence[_Alert],
    noted: set[int],
) -> ShakedownLine:
    unnoted = [
        f"stale_data alert {a.alert_id} (book {a.book_id}, {a.session})"
        for a in alerts
        if a.kind == _STALE_DATA and a.at >= span.start_at and a.alert_id not in noted
    ]
    health = health_report(conn, now, settings)
    failures = list(unnoted)
    if not health.ok:
        failures.append(f"tradepartner health fails: {', '.join(health.failures)}")
    detail = "every stale_data alert noted; health passes" if not failures else _named(failures)
    return ShakedownLine(
        name="E.7 data",
        passed=not failures,
        rows="alerts, owner_decisions, the fact tables (health)",
        query=(
            "stale_data alerts at or after the span start without a shakedown_note; "
            f"health_report at {now.isoformat()}"
        ),
        thresholds="unnoted stale_data alerts = 0; health integrity rules all pass",
        detail=detail,
    )


def shakedown(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> Shakedown:
    """`paper shakedown` (ADR 0017 part E): the span and the seven lines over every
    book, reading only. Raises `ValueError` when no `shakedown_span` row exists or
    a window's `frozen_json` lacks a tolerance key E.3 needs."""
    at = utc_now() if now is None else now
    switches = _switches(conn)
    closed = _closed_at(conn)
    span = _read_span(conn, at, switches, closed)
    brackets = _brackets(switches)
    windows = _windows(conn)
    runs = _runs(conn, span.first_day)
    alerts = _alerts(conn, span.first_day)
    noted = _noted_alerts(conn)
    ordered = _ordered_runs(conn)
    by_id = {w.window_id: w for w in windows if w.window_id is not None}
    return Shakedown(
        span=span,
        lines=(
            _sessions_line(conn, span, windows, closed, runs, brackets, ordered, alerts, noted),
            _reconciliation_line(conn, span),
            _orders_line(conn, span, by_id),
            _drill_line(span, windows, runs, switches, brackets, ordered),
            _journal_line(conn, span, windows, closed),
            _alerts_line(conn, span, settings),
            _data_line(conn, span, settings, at, alerts, noted),
        ),
    )

"""The Book page's view model (plan T279, #2170): everything one book's page shows,
read in one pass and returned as plain dataclasses.

`build(conn, settings, book, now)` reads the journal and registry through `conn`
(read-only: no write, no trial, no run lock taken, no broker) and shapes what the
page draws: the header (status, as-of session, value, since-start return against
SPY), the chart series for since start, 1M and 3M, the best and worst day, the
maximum drawdown and "N of M" (`book_stats`), Today's grouped sentences
(`book_activity`), the holdings summary (`book_holdings`), then the safety part
(reconciliation, kill switch, limits) and the backtest part (shakedown progress,
the latest tracking report). No Streamlit import: the page only draws it.

**No look-ahead.** `now` is the as-of time. Every journal row is cut to
`known_at <= now`, a mark to the last completed session at `now`, so a row
learned of (or dated) after `now` moves nothing. The as-of session is the latest
such marked session.

**"Matches Alpaca to the cent"** is said only when the latest reconciliation row
(by `at`, known by `now`) is `ok`, records no mismatch, lagging or pending order,
and none of the differences it allowed exceeds the window's frozen tolerance:
`risk.reconcile_cash_tolerance` for cash, `risk.reconcile_quantity_tolerance` for a
quantity (both read from the window's `frozen_json` through `frozen_risk`, the
values that reconciliation used). Anything else says in words what differs, never
a match that was not checked.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import (
    is_session,
    last_completed_session,
    previous_session,
    session_close,
)
from tradepartner.config import Settings
from tradepartner.execution import (
    book_activity,
    book_holdings,
    book_stats,
    headroom,
    lock,
    ops,
    plain,
    shakedown,
)
from tradepartner.execution.marks import equity_at
from tradepartner.execution.reconcile_run import frozen_risk
from tradepartner.execution.switch import SwitchState, derive
from tradepartner.store import journal, registry
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.benchmarks import BenchmarkIdentityError
from tradepartner.store.journal import (
    JournalNotInitialised,
    KillSwitchRow,
    PaperReportRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    ReconciliationRow,
)
from tradepartner.timeutil import ensure_tz_aware_utc

EASTERN = ZoneInfo("America/New_York")

#: The frozen `paper.*` key holding the "M" of "N of M" (`config.FROZEN_PAPER_KEYS`).
MIN_REBALANCES_KEY = "paper.min_rebalances"
#: The reconciliation fields the "matches to the cent" claim reads, named for the page.
RECONCILIATION_FIELDS_READ = (
    "reconciliations.status",
    "reconciliations.mismatches_json (mismatches, lagging, pending, allowed)",
    "risk.reconcile_cash_tolerance",
    "risk.reconcile_quantity_tolerance",
)
NO_RECONCILIATION = "No reconciliation with Alpaca yet."
_STATUS_WORDS = {
    "pending_unresolved": "an order's outcome is not known yet, so the books were not compared",
    "fills_lagging": "Alpaca has filled an order the journal has not caught up with",
    "mismatch": "the books do not match Alpaca",
}


@dataclass(frozen=True)
class Header:
    """The page header. `status` is `running` or `stopped` (`switch.derive`);
    `value` is the book's equity at `as_of_session`; the returns are percents over
    the since-start range (None until the book has a mark, or SPY is missing)."""

    status: Literal["running", "stopped"]
    as_of_session: date | None
    value: float | None
    since_start_return_pct: float | None
    spy_return_pct: float | None
    benchmark_error: str | None


@dataclass(frozen=True)
class Performance:
    """Chart ranges and the summary beside them (all `book_stats` types)."""

    ranges: tuple[book_stats.RangeSlice, ...]
    best_day: book_stats.DayStat | None
    worst_day: book_stats.DayStat | None
    max_drawdown: book_stats.MaxDrawdown | None
    tracking: book_stats.TrackingProgress | None


@dataclass(frozen=True)
class ReconciliationView:
    """Whether the book matches Alpaca. `matches` is True only for a checked
    match (module docstring); `text` is the sentence to show; `fields_read` names
    what was read; `at` is the row's time (None with no row)."""

    matches: bool
    text: str
    at: datetime | None
    cash_tolerance: float | None
    fields_read: tuple[str, ...] = RECONCILIATION_FIELDS_READ


@dataclass(frozen=True)
class KillSwitchView:
    """`running`, or stopped since `since` (None when no engaging row dates it)
    for `reason`, in words."""

    running: bool
    since: datetime | None
    reason: str
    text: str


@dataclass(frozen=True)
class LimitLine:
    """One limit in plain words (`headroom.LimitRow`)."""

    key: str
    sentence: str
    status: str


@dataclass(frozen=True)
class Safety:
    """The safety part: reconciliation, the kill switch and the limits."""

    reconciliation: ReconciliationView
    kill_switch: KillSwitchView
    limits: tuple[LimitLine, ...]


@dataclass(frozen=True)
class ShakedownProgress:
    """The open shakedown span's progress in words."""

    sessions_done: int
    sessions_needed: int
    text: str


@dataclass(frozen=True)
class TrialResult:
    """A report's trial as the registry holds it (read-only). `status` is the
    `trial_results` status, or `no result yet`."""

    trial_id: int
    status: str
    message: str | None
    red_flag: bool | None
    text: str


@dataclass(frozen=True)
class ReportLine:
    """The latest `paper_reports` row: the session it runs through and its trial."""

    through_session: date
    trial: TrialResult
    text: str


@dataclass(frozen=True)
class Backtest:
    """The backtest part: shakedown progress (None with no span), the latest
    report (None with none) and the command to run a new one."""

    shakedown: ShakedownProgress | None
    report: ReportLine | None
    command: str


@dataclass(frozen=True)
class BookView:
    """Everything the Book page draws. `window_id` is None for a book with no
    window (or no journal); the other parts are then empty."""

    book: str
    now: datetime
    window_id: int | None
    header: Header | None
    performance: Performance | None
    today: book_activity.TodayActivity | None
    today_session: date | None
    holdings: book_holdings.Holdings | None
    safety: Safety | None
    backtest: Backtest | None


def _known(rows: list[Any], now: datetime) -> list[Any]:
    """The rows the journal knew by `now`."""
    return [r for r in rows if r.known_at <= now]


def _marks(
    conn: duckdb.DuckDBPyConnection, window_id: int, now: datetime
) -> list[PositionDailyRow]:
    """The window's marks known by `now`, dated no later than the last completed session."""
    last = last_completed_session(now)
    return [
        m for m in _known(journal.positions_daily_for(conn, window_id), now) if m.session <= last
    ]


def _equity(marks: list[PositionDailyRow]) -> dict[date, float]:
    by_session: dict[date, list[PositionDailyRow]] = defaultdict(list)
    for mark in marks:
        by_session[mark.session].append(mark)
    return {s: equity_at(rows, s) for s, rows in by_session.items()}


def _switch(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    book: str,
    window: PaperWindowRow,
    now: datetime,
) -> tuple[SwitchState, list[KillSwitchRow], list[PaperRunRow]]:
    assert window.window_id is not None
    ks_rows: list[KillSwitchRow] = _known(
        journal.kill_switch_events_for(conn, window.window_id), now
    )
    runs: list[PaperRunRow] = []
    results: list[PaperRunResultRow] = []
    for pair in journal.runs_for(conn, window.window_id):
        if pair.run.known_at > now:
            continue
        runs.append(pair.run)
        if pair.result is not None and pair.result.known_at <= now:
            results.append(pair.result)
    state = derive(
        window, ks_rows, runs, results, reading_run=None, lock_free=not lock.is_held(settings, book)
    )
    return state, ks_rows, runs


def _kill_switch(state: SwitchState, runs: list[PaperRunRow]) -> KillSwitchView:
    if not state.engaged:
        return KillSwitchView(True, None, "", "Running: the kill switch is off.")
    since: datetime | None = state.engaged_row.at if state.engaged_row is not None else None
    if since is None:
        started = [
            r.started_at
            for r in runs
            if any(c.startswith(f"run {r.run_id} ") for c in state.causes)
        ]
        since = min(started, default=None)
    reason = plain.plain_causes(state.causes)
    if state.engaged_row is not None and state.engaged_row.reason:
        reason = f"{reason}: {state.engaged_row.reason}"
    when = f" since {since.isoformat()}" if since is not None else ""
    return KillSwitchView(False, since, reason, f"Stopped{when}: {reason}.")


def _percent(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _limit_sentence(row: headroom.LimitRow) -> str:
    left = row.headroom_pp
    state = (
        f"{left:.1f} points to spare"
        if row.status == "within"
        else f"{-left:.1f} points over the limit"
    )
    return (
        f"{row.label}: {_percent(row.value)} against a limit of {_percent(row.limit)}, "
        f"{state}. Past it, {row.does}."
    )


def _limits(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    marks: list[PositionDailyRow],
    ks_rows: list[KillSwitchRow],
) -> tuple[LimitLine, ...]:
    """`headroom`'s four rows over the marks and switch rows known by `now`, as
    `headroom.read` builds them (its private `_side` and `_ticker` are reused)."""
    session = max((m.session for m in marks), default=None)
    last = [m for m in marks if m.session == session]
    card = headroom.compute(
        window, last, ks_rows, frozen_risk(window), side=headroom._side(conn, window)
    )
    return tuple(LimitLine(r.key, _limit_sentence(r), r.status) for r in card.rows)


def _json(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _money(amount: float) -> str:
    return f"${abs(amount):,.2f}"


def _reconciliation(
    window: PaperWindowRow, rows: list[ReconciliationRow], now: datetime
) -> ReconciliationView:
    known = [r for r in rows if r.known_at <= now and r.at <= now]
    if not known:
        return ReconciliationView(False, NO_RECONCILIATION, None, None)
    row = max(known, key=lambda r: (r.at, r.known_at, r.reconciliation_id or 0))
    risk = frozen_risk(window)
    cash_tol = float(risk.reconcile_cash_tolerance)
    quantity_tol = float(risk.reconcile_quantity_tolerance)
    stamp = row.at.isoformat()
    payload = _json(row.mismatches_json)
    if row.status != "ok":
        why = _STATUS_WORDS.get(row.status, f"its status is {row.status}")
        detail = ""
        mismatches = payload.get("mismatches") if payload is not None else None
        if isinstance(mismatches, list) and mismatches:
            parts = [
                str(m.get("detail", m.get("kind", "?"))) for m in mismatches if isinstance(m, dict)
            ]
            detail = ": " + "; ".join(parts)
        return ReconciliationView(
            False, f"Does not match Alpaca as of {stamp}: {why}{detail}.", row.at, cash_tol
        )
    if payload is None:
        return ReconciliationView(
            False,
            f"Not confirmed against Alpaca as of {stamp}: the record could not be read.",
            row.at,
            cash_tol,
        )
    if payload.get("mismatches") or payload.get("lagging") or payload.get("pending"):
        return ReconciliationView(
            False,
            f"Not confirmed against Alpaca as of {stamp}: the record lists open differences.",
            row.at,
            cash_tol,
        )
    off: list[str] = []
    for entry in payload.get("allowed") or []:
        if not isinstance(entry, dict):
            continue
        difference = entry.get("difference")
        if isinstance(difference, bool) or not isinstance(difference, int | float):
            off.append("a difference that could not be read")
            continue
        reason = str(entry.get("reason", ""))
        is_quantity = "quantity" in reason or "spin-off" in reason or "listing" in reason
        tolerance = quantity_tol if is_quantity else cash_tol
        if abs(difference) > tolerance:
            amount = f"{abs(difference):g} shares" if is_quantity else _money(float(difference))
            off.append(f"{reason} differs by {amount}, more than the {tolerance:g} allowed")
    if off:
        return ReconciliationView(
            False,
            f"Close but not to the cent as of {stamp}: " + "; ".join(off) + ".",
            row.at,
            cash_tol,
        )
    return ReconciliationView(True, f"Matches Alpaca to the cent as of {stamp}.", row.at, cash_tol)


def _progress(conn: duckdb.DuckDBPyConnection, now: datetime) -> ShakedownProgress | None:
    if registry.shakedown_span(conn) is None:
        return None
    span = shakedown.read_span(conn, now)
    done = len(span.sessions)
    text = f"{done} of {span.sessions_needed} sessions"
    if span.start is None and span.restart:
        text += f", not started: {span.restart}"
    return ShakedownProgress(done, span.sessions_needed, text)


def _trial_result(conn: duckdb.DuckDBPyConnection, trial_id: int, now: datetime) -> TrialResult:
    row = conn.execute(
        "SELECT status, message, red_flag FROM trial_results "
        "WHERE trial_id = ? AND finished_at <= ?",
        [trial_id, now],
    ).fetchone()
    if row is None:
        return TrialResult(
            trial_id, "no result yet", None, None, f"trial {trial_id}: no result yet"
        )
    status, message, red_flag = row
    text = f"trial {trial_id}: {status}" + (f" ({message})" if message else "")
    if red_flag:
        text += ", red flag raised"
    return TrialResult(trial_id, str(status), message, red_flag, text)


def _backtest(
    conn: duckdb.DuckDBPyConnection, window: PaperWindowRow, book: str, now: datetime
) -> Backtest:
    assert window.window_id is not None
    reports: list[PaperReportRow] = _known(journal.paper_reports_for(conn, window.window_id), now)
    latest = max(reports, key=lambda r: (r.known_at, r.trial_id), default=None)
    line: ReportLine | None = None
    if latest is not None:
        trial = _trial_result(conn, latest.trial_id, now)
        line = ReportLine(
            latest.through_session,
            trial,
            f"Latest report runs through {latest.through_session.isoformat()}; {trial.text}.",
        )
    return Backtest(_progress(conn, now), line, f"tradepartner paper report --book {book}")


def _min_rebalances(window: PaperWindowRow) -> int | None:
    payload = _json(window.frozen_json)
    value = payload.get(MIN_REBALANCES_KEY) if payload is not None else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _series(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    window: PaperWindowRow,
    marks: list[PositionDailyRow],
    now: datetime,
) -> tuple[dict[date, float], dict[date, float] | None, str | None]:
    """The book's equity by session (with the base point at B) and SPY's at the
    same sessions, both known by `now`; the third item says why SPY is missing."""
    marked = _equity(marks)
    if not marked:
        return {}, None, None
    base = ops._base_session(window.started_at)
    base_value = marked[base] if min(marked) == base else window.starting_equity
    equity = {**marked, base: base_value}
    try:
        spy = ops._benchmark_series(conn, settings, as_of=now, base=base, sessions=sorted(equity))
    except (BenchmarkIdentityError, ValueError) as exc:
        return equity, None, str(exc)
    return equity, spy, None


def _today_session(now: datetime) -> date:
    day = now.astimezone(EASTERN).date()
    return day if is_session(day) else previous_session(day)


def _holdings(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    marks: list[PositionDailyRow],
    as_of: date,
    now: datetime,
    top_n: int,
) -> book_holdings.Holdings:
    """`book_holdings.read_holdings`'s read with every row cut to `known_at <= now`."""
    fills = [f for f in journal.fills_for(conn, window_id=window_id) if f.fill.known_at <= now]
    orders = _known(journal.orders_for(conn, window_id=window_id), now)
    adjustments = _known(journal.adjustments_for(conn, window_id, through_session=as_of), now)
    marked = sorted({m.session for m in marks if m.session <= as_of})
    actions = (
        live_actions_as_of(conn, session_close(previous_session(marked[-1]))) if marked else None
    )
    return book_holdings.build_holdings(
        fills,
        orders,
        adjustments,
        marks,
        as_of,
        actions=actions,
        tickers=book_holdings.ticker_map(orders),
        top_n=top_n,
    )


def build(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    book: str,
    now: datetime,
    *,
    top_n: int = 5,
) -> BookView:
    """The Book page for `book` as the journal knew it at `now` (tz-aware UTC).

    One read pass; writes nothing. `top_n` is the holdings ranking length the page
    wants. A book with no window (or no journal) comes back with only `book` and
    `now`. `UnreadableMarkError` from an unreadable mark propagates, for the
    page's unreadable panel."""
    now = ensure_tz_aware_utc(now, field_name="now")
    empty = BookView(book, now, None, None, None, None, None, None, None, None)
    try:
        window = journal.latest_window(conn, book)
    except JournalNotInitialised:
        return empty
    if window is None or window.window_id is None:
        return empty
    window_id = window.window_id

    marks = _marks(conn, window_id, now)
    as_of = max((m.session for m in marks), default=None)
    state, ks_rows, runs = _switch(conn, settings, book, window, now)
    equity, spy, spy_error = _series(conn, settings, window, marks, now)

    performance: Performance | None = None
    value: float | None = None
    since_book: float | None = None
    since_spy: float | None = None
    if as_of is not None and equity:
        ranges = tuple(
            book_stats.range_slice(equity, spy, key, as_of) for key in ("since_start", "1M", "3M")
        )
        best_worst = book_stats.best_worst_day(equity, as_of)
        minimum = _min_rebalances(window)
        tracking = (
            book_stats.tracking_progress(ops._tracking_count(conn, window, now), minimum)
            if minimum is not None
            else None
        )
        performance = Performance(
            ranges,
            best_worst[0] if best_worst else None,
            best_worst[1] if best_worst else None,
            book_stats.max_drawdown(equity, as_of),
            tracking,
        )
        value = equity[as_of]
        since_book, since_spy = ranges[0].book_return_pct, ranges[0].spy_return_pct

    session = _today_session(now)
    today = book_activity.today(
        conn, window_id, session, now, limit=settings.dashboard.page_row_limit
    )
    holdings = _holdings(conn, window_id, marks, as_of, now, top_n) if as_of is not None else None

    safety = Safety(
        _reconciliation(window, _known(journal.reconciliations_for(conn, window_id), now), now),
        _kill_switch(state, runs),
        _limits(conn, window, marks, ks_rows),
    )
    return replace(
        empty,
        window_id=window_id,
        header=Header(
            "stopped" if state.engaged else "running",
            as_of,
            value,
            since_book,
            since_spy,
            spy_error,
        ),
        performance=performance,
        today=today,
        today_session=session,
        holdings=holdings,
        safety=safety,
        backtest=_backtest(conn, window, book, now),
    )

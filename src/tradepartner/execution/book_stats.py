"""The book's performance summary (plan T270, #2170): range slices, best and
worst day, maximum drawdown and the "N of M" line; and the forward return exam's
inputs (plan T381a): the monthly net excess series, the benchmark's return over
cash, `window_ended` and `minimums_met_by` (pure), and, at the edge, the exam
line and the budget line `paper status` prints (`exam_line`, `exam_budget_line`).

Pure functions over `ops.PerformanceData`'s series (session -> dollars): no
I/O, no clock, no Streamlit. `as_of` is the last session the summary may use;
a point dated after it is ignored (no look-ahead). Ranges cut on the XNYS
trading calendar, never on weekdays.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

import duckdb
from dateutil.relativedelta import relativedelta

from tradepartner.backtest.metrics import PERCENT_POINTS_PER_UNIT
from tradepartner.backtest.riskfree import published_by_session, session_accruals, to_yield
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.calendar import all_sessions, is_session, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.execution import exam as exam_rules
from tradepartner.execution import exam_ledger, ops, performance
from tradepartner.execution import window as win
from tradepartner.execution.window import window_cadence
from tradepartner.store import journal, registry
from tradepartner.store.asof import rates_as_of
from tradepartner.store.journal import CLOSING_STOP_STATES as CLOSING
from tradepartner.store.journal import PaperWindowRow

Series = Mapping[date, float]
Rows = list[tuple[date, float]]
RangeKey = Literal["since_start", "1M", "3M"]

#: Calendar months back from `as_of` for each bounded range.
_RANGE_MONTHS: dict[str, int] = {"1M": 1, "3M": 3}

TOO_YOUNG_NOTE = "range longer than the book's history"
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RangeSlice:
    """One range of the book and SPY, both rebased to 100 at `start_session`.

    `book` and `spy` cover identical sessions: a session either series lacks is
    dropped from both, never forward-filled. `spy` is empty when no benchmark
    was given. `note` is `TOO_YOUNG_NOTE` when the book's history is shorter
    than the range (the slice then starts at the book's first session); None
    otherwise. All fields are empty/None for a series with no usable point."""

    range_key: str
    start_session: date | None
    book: Rows
    spy: Rows
    book_return_pct: float | None
    spy_return_pct: float | None
    note: str | None


@dataclass(frozen=True)
class DayStat:
    """One session's dollar change and its percent of the prior session's equity."""

    session: date
    dollars: float
    pct: float


@dataclass(frozen=True)
class MaxDrawdown:
    """The largest peak-to-trough fall of the equity series.

    `dollars` and `pct` are non-positive (the trough over the peak, minus one);
    a series that never falls gives 0.0 and 0.0 with both dates at its first
    session."""

    dollars: float
    pct: float
    peak_session: date
    trough_session: date


@dataclass(frozen=True)
class TrackingProgress:
    """`tracking_n` of the frozen `paper.min_rebalances` for the book's cadence."""

    n: int | None
    m: int
    enough: bool
    text: str


def _upto(series: Series, as_of: date) -> dict[date, float]:
    """The series without any point dated after `as_of`."""
    return {s: v for s, v in series.items() if s <= as_of}


def _session_on_or_before(day: date) -> date:
    return day if is_session(day) else previous_session(day)


def range_slice(
    equity: Series,
    benchmark: Series | None,
    range_key: RangeKey,
    as_of: date,
) -> RangeSlice:
    """Cut `equity` (and SPY) to `range_key` ending at `as_of`, rebased together.

    The start session of "1M" / "3M" is the last session on or before the
    calendar date one / three months before `as_of`. A book whose first session
    is later than that starts at its own first session and carries
    `TOO_YOUNG_NOTE`. "since_start" is the whole book up to `as_of`. Only
    sessions present in both series are kept; both are rebased to 100 at the
    first kept session, and the returns are `cumulative_return_pct` on that
    same base."""
    if range_key != "since_start" and range_key not in _RANGE_MONTHS:
        raise ValueError(f"unknown range {range_key!r}")
    book = _upto(equity, as_of)
    spy = _upto(benchmark, as_of) if benchmark is not None else None
    note: str | None = None
    if range_key != "since_start" and book:
        start = _session_on_or_before(as_of - relativedelta(months=_RANGE_MONTHS[range_key]))
        if min(book) > start:
            note = TOO_YOUNG_NOTE
        book = {s: v for s, v in book.items() if s >= start}
    sessions = set(book) if spy is None else set(book) & set(spy)
    if not sessions:
        return RangeSlice(range_key, None, [], [], None, None, note)
    book = {s: book[s] for s in sessions}
    first = min(sessions)
    book_rows = performance.rebase(book, book[first])
    spy_rows: Rows = []
    spy_ret: float | None = None
    if spy is not None:
        spy_slice = {s: spy[s] for s in sessions}
        spy_rows = performance.rebase(spy_slice, spy_slice[first])
        spy_ret = performance.cumulative_return_pct(spy_slice)
    return RangeSlice(
        range_key,
        first,
        book_rows,
        spy_rows,
        performance.cumulative_return_pct(book),
        spy_ret,
        note,
    )


def best_worst_day(equity: Series, as_of: date) -> tuple[DayStat, DayStat] | None:
    """The best and worst session by dollar change (earliest wins a tie).

    None for fewer than two points up to `as_of`. The percent is the day's
    dollars over the prior session's equity."""
    book = _upto(equity, as_of)
    pnl = performance.daily_pnl(book)
    if not pnl:
        return None
    ordered = sorted(book)
    prior = dict(zip(ordered[1:], ordered[:-1], strict=True))
    stats = [DayStat(s, d, 100.0 * d / book[prior[s]]) for s, d in pnl]
    return max(stats, key=lambda x: x.dollars), min(stats, key=lambda x: x.dollars)


def max_drawdown(equity: Series, as_of: date) -> MaxDrawdown | None:
    """The largest peak-to-trough fall of `equity` up to `as_of`; None under two points."""
    book = _upto(equity, as_of)
    if len(book) < 2:
        return None
    ordered = sorted(book)
    peak_s = ordered[0]
    best = MaxDrawdown(0.0, 0.0, peak_s, peak_s)
    for s in ordered:
        if book[s] > book[peak_s]:
            peak_s = s
        peak = book[peak_s]
        if peak <= 0:
            raise ValueError("equity must be positive")
        pct = 100.0 * (book[s] / peak - 1.0)
        if pct < best.pct:
            best = MaxDrawdown(book[s] - peak, pct, peak_s, s)
    return best


def tracking_progress(tracking_n: int | None, min_rebalances: int) -> TrackingProgress:
    """The "N of M" line: `tracking_n` rebalances of the frozen `paper.min_rebalances`.

    "enough to judge" only at N >= M; an unreadable count (None) is never enough."""
    if min_rebalances <= 0:
        raise ValueError("min_rebalances must be positive")
    if tracking_n is None:
        return TrackingProgress(
            None, min_rebalances, False, f"rebalances unknown of {min_rebalances}"
        )
    enough = tracking_n >= min_rebalances
    text = f"{tracking_n} of {min_rebalances} rebalances"
    return TrackingProgress(
        tracking_n, min_rebalances, enough, text + (", enough to judge" if enough else "")
    )


# --- the forward return exam's inputs (plan T381a; ADR 0016, #3125 and #3143) ------


@dataclass(frozen=True)
class ExamSeries:
    """An exam window's monthly series, T381's two inputs (ADR 0016 decision 2).

    `month_ends[0]` is the base: the first month-end session on or after the window's
    first rebalance session (the stub before it is not tested). Month m runs from
    `month_ends[m - 1]` to `month_ends[m]`: `net_excess[m - 1]` is the book's return
    over it minus the benchmark's total return over the same dates, and
    `bench_over_cash[m - 1]` is the benchmark's return over it minus the T-bill
    return (ADR 0020) over the same sessions."""

    month_ends: tuple[date, ...]
    net_excess: tuple[float, ...]
    bench_over_cash: tuple[float, ...]

    @property
    def months(self) -> int:
        """The number of complete months the series holds."""
        return len(self.net_excess)


def exam_month_ends(first_rebalance: date, as_of: date) -> list[date]:
    """Every month-end session from the first on or after `first_rebalance` through
    `as_of` (the base, then one per complete month); empty before the base."""
    if as_of < first_rebalance:
        return []
    return rebalance_sessions(first_rebalance, as_of, "month_end")


def exam_series(
    equity: Series,
    benchmark: Series,
    cash_accruals: Series,
    first_rebalance: date,
    as_of: date,
) -> ExamSeries:
    """The monthly net excess series and the benchmark's return over cash (module
    section; ADR 0016 decision 2), pure.

    `equity` is the book's ledger equity per marked session (every fill, fee and
    dividend the journal books), `benchmark` the frozen benchmark's total-return
    level per session, and `cash_accruals` the T-bill accrual of each session
    (`riskfree.session_accruals`). Only month-ends on or before both `as_of` and the
    last marked session are read, so the series up to month k never changes when
    later sessions are added (no look-ahead). `ValueError` names a month-end the
    equity or the benchmark lacks, or a session inside a month with no accrual: a
    month that cannot be read is never skipped (it would shift every later month)."""
    marked = _upto(equity, as_of)
    if not marked:
        return ExamSeries((), (), ())
    ends = exam_month_ends(first_rebalance, min(as_of, max(marked)))
    for end in ends:
        if end not in marked:
            raise ValueError(f"the exam series has no equity mark at month-end {end}")
        if end not in benchmark:
            raise ValueError(f"the exam series has no benchmark level at month-end {end}")
    net: list[float] = []
    over_cash: list[float] = []
    for start, end in pairwise(ends):
        book = marked[end] / marked[start] - 1.0
        bench = benchmark[end] / benchmark[start] - 1.0
        growth = 1.0
        for session in sessions_between(start, end):
            if session not in cash_accruals:
                raise ValueError(f"the exam series has no T-bill accrual at {session}")
            growth *= 1.0 + cash_accruals[session]
        net.append(book - bench)
        over_cash.append(bench - (growth - 1.0))
    return ExamSeries(tuple(ends), tuple(net), tuple(over_cash))


def sessions_between(start: date, end: date) -> list[date]:
    """The XNYS sessions after `start` and on or before `end`."""
    return [s for s in all_sessions() if start < s <= end]


def window_ended(stopped_at: datetime | None, now: datetime) -> bool:
    """T381's `window_ended`: true only once the window's stop (its `closed` or
    `abandoned` stop row's stamp) is at or before `now`."""
    return stopped_at is not None and stopped_at <= now


def minimums_met_by(
    looks_months: Sequence[int],
    month_ends: Sequence[date],
    rebalances: Sequence[date],
    fills: Sequence[date],
    *,
    min_rebalances: int,
    min_exam_fills: int | None,
) -> dict[int, bool]:
    """T381's `minimums_met_by`: for each look L, whether the window's rebalance
    sessions and live fills (their sessions) on or before month L's month-end reach
    `paper.min_rebalances` and `paper.min_exam_fills` (ADR 0023). A look the series
    has not reached is unmet, and so is every look when `min_exam_fills` is None (a
    window frozen without the key: fail-closed, it can never pass)."""
    met: dict[int, bool] = {}
    for look in looks_months:
        if min_exam_fills is None or look >= len(month_ends):
            met[look] = False
            continue
        cutoff = month_ends[look]
        met[look] = (
            sum(1 for s in rebalances if s <= cutoff) >= min_rebalances
            and sum(1 for s in fills if s <= cutoff) >= min_exam_fills
        )
    return met


# --- the exam line `paper status` prints (plan T381a; ADR 0016 decision 1, #3146) ------

#: Every book without a passed exam (ADR 0016 decision 1, "Labels").
EDGE_UNJUDGED = "machine pass, edge unjudged"
_VERDICT_TEXT = {
    "failed": "forward return exam failed",
    "failed_futility": "forward return exam failed (futility)",
    "failed_void": "forward return exam failed (void)",
}


def _frozen_exam(frozen: Mapping[str, object]) -> exam_rules.ExamFrozen:
    looks = frozen["paper.exam_looks_months"]
    bounds = frozen["paper.exam_bounds"]
    futility = frozen["paper.exam_futility_months"]
    if not isinstance(looks, list) or not isinstance(bounds, list) or not isinstance(futility, int):
        raise ValueError("the window's frozen exam keys are malformed")
    return exam_rules.ExamFrozen(
        looks_months=tuple(int(v) for v in looks),
        bounds=tuple(float(v) for v in bounds),
        futility_months=futility,
    )


def _cash_accruals(
    conn: duckdb.DuckDBPyConnection, settings: Settings, sessions: Sequence[date], now: datetime
) -> dict[date, float]:
    """The T-bill accrual of each session after the first (ADR 0020), from the rates
    known at each session's close, as the backtest reads them."""
    if len(sessions) < 2:
        return {}
    metrics = settings.metrics
    rows = rates_as_of(conn, now, metrics.risk_free_series)
    published = published_by_session(
        (
            (known_at, observed, value / PERCENT_POINTS_PER_UNIT)
            for _series, observed, value, _vintage, known_at in rows.iter_rows()
        ),
        sessions,
        session_close,
    )
    yields = {s: to_yield(r, metrics.risk_free_basis) for s, r in published.items()}
    return session_accruals(sessions, yields, series=metrics.risk_free_series)


def exam_verdict_of(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    window: PaperWindowRow,
    *,
    now: datetime,
) -> exam_rules.ExamVerdict:
    """T381's verdict for an exam window that froze its line, read at the edge: the
    journal's month-end marks, SPY's stored total return, the T-bill rates, the
    window's rebalances and live fills (`fills.superseded_by IS NULL`) and its stop.
    `ValueError` (or `KeyError`) when an input cannot be read."""
    assert window.window_id is not None
    frozen = json.loads(window.frozen_json)
    looks = _frozen_exam(frozen)
    perf = ops.performance_data(conn, settings, window.book_id, now=now)
    if perf is None or perf.window is None or perf.window.window_id != window.window_id:
        raise ValueError("the window is not its book's latest")
    if perf.benchmark_error is not None:
        raise ValueError(perf.benchmark_error)
    equity = dict(perf.equity)
    benchmark = dict(perf.benchmark_index)
    first = window.first_rebalance_session
    as_of = max(equity) if equity else first
    ends = exam_month_ends(first, as_of)
    sessions = [ends[0], *sessions_between(ends[0], ends[-1])] if ends else []
    series = exam_series(
        equity, benchmark, _cash_accruals(conn, settings, sessions, now), first, as_of
    )
    stops = [s.at for s in journal.window_stops_for(conn, window.window_id) if s.state in CLOSING]
    cadence = window_cadence(conn, window)
    last = series.month_ends[-1] if series.month_ends else first
    rebalances = rebalance_sessions(first, last, cadence) if last >= first else []
    fills = [  # live fills only: `fills_for` hides superseded rows
        f.fill.filled_at.astimezone(_NEW_YORK).date()
        for f in journal.fills_for(conn, window_id=window.window_id)
    ]
    min_fills = frozen.get("paper.min_exam_fills")
    met = minimums_met_by(
        looks.looks_months,
        series.month_ends,
        rebalances,
        fills,
        min_rebalances=int(frozen["paper.min_rebalances"]),
        min_exam_fills=None if min_fills is None else int(min_fills),
    )
    return exam_rules.exam_verdict(
        series.net_excess,
        series.bench_over_cash,
        looks,
        window_ended=window_ended(min(stops) if stops else None, now),
        minimums_met_by=met,
    )


def exam_line(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    window: PaperWindowRow,
    ledger: exam_ledger.ExamLedger | None,
    *,
    now: datetime,
) -> str:
    """The exam line `paper status` prints for `window` (ADR 0016 decision 1 and
    #3146 rule 2(b)): "forward return exam passed" only for a window whose entry in
    `ledger` (`origin/main`'s copy; None when it cannot be read) is `passed`; the
    verdict for an exam window; "machine pass, edge unjudged" for every other."""
    try:
        tracking = win.window_exam_class(conn, window) == win.TRACKING_CLASS
    except (ValueError, KeyError, registry.UnknownHypothesis) as exc:
        return f"exam: window class unreadable ({exc}); {EDGE_UNJUDGED}"
    if tracking:
        return f"exam: tracking window; {EDGE_UNJUDGED}"
    if win.is_unexamined(window):
        return f"exam: exam window, unexamined, counted; {EDGE_UNJUDGED}"
    try:
        verdict = exam_verdict_of(conn, settings, window, now=now)
    except (ValueError, KeyError, TypeError) as exc:
        return f"exam: forward return exam unreadable ({exc}); {EDGE_UNJUDGED}"
    months = f"month {verdict.months_read}"
    if verdict.state == "passed":
        entry = next(
            (e for e in (ledger.entries if ledger else ()) if e.window_id == window.window_id),
            None,
        )
        if entry is not None and entry.outcome == exam_ledger.PASSED:
            return f"exam: forward return exam passed at month {verdict.month}"
        return (
            "exam: forward return exam passed by T381, not yet recorded in the ledger; "
            + EDGE_UNJUDGED
        )
    if verdict.state == "running":
        return (
            f"exam: forward return exam running, {months}, next look at month "
            f"{verdict.next_look}; {EDGE_UNJUDGED}"
        )
    at = "" if verdict.month is None else f" at month {verdict.month}"
    return f"exam: {_VERDICT_TEXT[verdict.state]}{at}; {EDGE_UNJUDGED}"


def exam_budget_line(conn: duckdb.DuckDBPyConnection, ledger: exam_ledger.ExamLedger | None) -> str:
    """`paper status`'s budget line (#3146, "Display")."""
    try:
        stored: int | str = len(win.store_exam_windows(conn))
    except (ValueError, KeyError, registry.UnknownHypothesis):
        stored = "unreadable"
    if ledger is None:
        return f"exam budget unreadable (no reviewed ledger), {stored} exam windows in the store"
    count = len(exam_ledger.counted(ledger))
    return f"exam budget {count} of {ledger.budget} (ledger), {stored} exam windows in the store"

"""The book's performance summary (plan T270, #2170): range slices, best and
worst day, maximum drawdown and the "N of M" line.

Pure functions over `ops.PerformanceData`'s series (session -> dollars): no
I/O, no clock, no Streamlit. `as_of` is the last session the summary may use;
a point dated after it is ignored (no look-ahead). Ranges cut on the XNYS
trading calendar, never on weekdays.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from dateutil.relativedelta import relativedelta

from tradepartner.calendar import is_session, previous_session
from tradepartner.execution import performance

Series = Mapping[date, float]
Rows = list[tuple[date, float]]
RangeKey = Literal["since_start", "1M", "3M"]

#: Calendar months back from `as_of` for each bounded range.
_RANGE_MONTHS: dict[str, int] = {"1M": 1, "3M": 3}

TOO_YOUNG_NOTE = "range longer than the book's history"


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

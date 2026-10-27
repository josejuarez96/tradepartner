"""The per-book performance arithmetic (plan T250, paper spec amendment #2006).

Pure functions over explicit inputs: no I/O, no store, no broker, no clock. A
series is a mapping of trading session (`datetime.date`) to a value; outputs
are lists of `(session, value)` ordered by session. The base value is the
value at the base session B; every rebase, return and excess is relative to
a base the caller passes, so the caller (T251) owns how B is chosen.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from itertools import pairwise

Series = Mapping[date, float]
Rows = list[tuple[date, float]]


def base_value(series: Series, base_session: date) -> float:
    """Return the series value at `base_session`; `ValueError` naming it if absent."""
    if base_session not in series:
        raise ValueError(f"no value at base session {base_session.isoformat()}")
    return series[base_session]


def _check_base(base: float) -> None:
    if not base > 0:  # also rejects NaN
        raise ValueError("base value must be positive")


def rebase(series: Series, base: float) -> Rows:
    """Scale the series so `base` maps to 100.00, ordered by session."""
    _check_base(base)
    return [(s, 100.0 * series[s] / base) for s in sorted(series)]


def daily_pnl(series: Series) -> Rows:
    """Value change between consecutive sessions, keyed by the later session."""
    ordered = sorted(series)
    return [(b, series[b] - series[a]) for a, b in pairwise(ordered)]


def cumulative_return_pct(series: Series, base: float | None = None) -> float | None:
    """Last value over `base` (default: the first value), in percent; None if empty."""
    if not series:
        return None
    ordered = sorted(series)
    b = series[ordered[0]] if base is None else base
    _check_base(b)
    return 100.0 * (series[ordered[-1]] / b - 1.0)


def excess_over_benchmark(
    paper: Series,
    benchmark: Series,
    paper_base: float | None = None,
    benchmark_base: float | None = None,
) -> Rows:
    """`100 * (P_t/P_B - S_t/S_B)` in percentage points at each paper session.

    The base is the first paper session unless both bases are given.
    The benchmark is read only at the paper series' sessions (extra benchmark
    sessions are ignored); a paper session with no benchmark value raises
    `ValueError` naming it.
    """
    if (paper_base is None) != (benchmark_base is None):
        raise ValueError("give both bases or neither: one explicit base skews the excess")
    if not paper:
        return []
    ordered = sorted(paper)
    for s in ordered:
        if s not in benchmark:
            raise ValueError(f"no benchmark value at paper session {s.isoformat()}")
    p0 = paper[ordered[0]] if paper_base is None else paper_base
    s0 = benchmark[ordered[0]] if benchmark_base is None else benchmark_base
    _check_base(p0)
    _check_base(s0)
    return [(s, 100.0 * (paper[s] / p0 - benchmark[s] / s0)) for s in ordered]

"""The forward return exam's verdict, pure (ADR 0016 amendments 2026-11-06, #3125,
decision 2, and 2026-11-07, #3143, the group-sequential exam; plan T381).

`exam_verdict(net_excess, bench_over_cash, frozen, *, window_ended, minimums_met_by)`
reads an exam window's monthly net excess series (month m is the book's net return
from one month-end to the next minus the frozen benchmark's total return over the same
dates) and the benchmark's monthly return over the T-bill rate (ADR 0020), both built
at the edge by T381a, and returns one of five states:

- `running`, with its next look;
- `passed`, with the look it passed at;
- `failed`, the last look (36) reached with ADR 0023's minimums met by it and not both
  legs at its bound;
- `failed_futility`, with its month: the sum of months 1 to `futility_months` (6) is
  below 0, or a 12- or 24-month look failed and the sum of months 1 to L is below 0;
- `failed_void`: the window ended before a decision, or the minimums were not met by
  the last look.

**The rule, in time order.** At month 6, futility. At each look L (12, 24, 36), both
legs are computed on months 1 to L: leg (i) the t of the mean (sample standard
deviation, L - 1 degrees of freedom) and leg (ii) the t of the OLS intercept of the
series on the benchmark's return over cash (L - 2 degrees of freedom). The look passes
when both reach that look's frozen bound (the same bound for both legs) and the
minimums were met by it; the exam passes at the first such look. A look whose
minimums are unmet cannot pass, but the futility rule still reads it. A failed last
look is `failed`, or `failed_void` when its minimums were unmet, whatever the legs
show.

**No look-ahead.** A verdict decided at month m reads only months 1 to m: months after
it never change it, and a `running` verdict reads only the months it is given.

**Fail-closed numerics.** A leg whose t is undefined (a zero standard deviation of the
series or of the residuals, or a constant regressor) is NaN, which reaches no bound.

`insample_skew(series)` is the sample skewness (the moment estimator m3 / m2^1.5, the
one `backtest.metrics` reports as `skew_period`) the skew guard reads at `paper start`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tradepartner.config import PaperConfig

ExamState = Literal["running", "passed", "failed", "failed_futility", "failed_void"]


# A sum of squared deviations at or below this fraction of the sum of squares is
# rounding noise around zero (a constant series, an exact fit), not a spread: the t
# it would give is a float artefact, so the leg is NaN (fail-closed).
_DEGENERATE = (64.0 * 2.220446049250313e-16) ** 2


@dataclass(frozen=True, slots=True)
class ExamFrozen:
    """The exam keys a window froze at `paper start` (T381a).

    `looks_months` are strictly increasing, `bounds` has one t bound per look and
    `futility_months` is below the first look. The pinned values are the config
    validator's job (`PaperConfig`); this checks only the shape the verdict needs.
    """

    looks_months: tuple[int, ...]
    bounds: tuple[float, ...]
    futility_months: int

    def __post_init__(self) -> None:
        if not self.looks_months:
            raise ValueError("exam looks must not be empty")
        if len(self.bounds) != len(self.looks_months):
            raise ValueError(
                f"exam bounds {self.bounds} must have one bound per look {self.looks_months}"
            )
        if any(b <= a for a, b in zip(self.looks_months, self.looks_months[1:], strict=False)):
            raise ValueError(f"exam looks must be strictly increasing, got {self.looks_months}")
        if self.looks_months[0] < 3:
            raise ValueError("the first exam look needs at least 3 months for leg (ii)")
        if not all(math.isfinite(b) for b in self.bounds):
            raise ValueError(f"exam bounds must be finite, got {self.bounds}")
        if not 0 < self.futility_months < self.looks_months[0]:
            raise ValueError(
                f"the futility month {self.futility_months} must be in (0, {self.looks_months[0]})"
            )

    @classmethod
    def from_config(cls, paper: PaperConfig) -> ExamFrozen:
        """The exam keys as `paper` holds them (validated against the pins)."""
        return cls(
            looks_months=tuple(paper.exam_looks_months),
            bounds=tuple(paper.exam_bounds),
            futility_months=paper.exam_futility_months,
        )


@dataclass(frozen=True, slots=True)
class LookResult:
    """One look's reading: both legs' t, the bound, the sum of months 1 to `month`,
    whether the minimums were met by it and whether the look passed."""

    month: int
    bound: float
    t_mean: float
    t_intercept: float
    excess_sum: float
    minimums_met: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class ExamVerdict:
    """The exam's state.

    `month` is the deciding month (the look for `passed`, the last look for `failed`,
    the futility month or look for `failed_futility`, the last look for a
    `failed_void` on unmet minimums) and `None` for `running` and for a window that
    ended before a decision. `next_look` is set only while `running`. `months_read` is
    the number of months the verdict read, and `looks` the looks it reached.
    """

    state: ExamState
    month: int | None
    next_look: int | None
    months_read: int
    looks: tuple[LookResult, ...]


def mean_t(series: Sequence[float]) -> float:
    """Leg (i): the t-statistic of the mean, with the sample standard deviation
    (n - 1 degrees of freedom); NaN when it is undefined (n < 2 or zero deviation)."""
    n = len(series)
    if n < 2:
        return math.nan
    mean = math.fsum(series) / n
    ss = math.fsum((v - mean) ** 2 for v in series)
    if ss <= _DEGENERATE * math.fsum(v * v for v in series):
        return math.nan
    sd = math.sqrt(ss / (n - 1))
    return mean / (sd / math.sqrt(n))


def intercept_t(series: Sequence[float], regressor: Sequence[float]) -> float:
    """Leg (ii): the t-statistic of the intercept of the OLS regression of `series` on
    `regressor` (n - 2 degrees of freedom); NaN when it is undefined (n < 3, a constant
    regressor or zero residuals)."""
    n = len(series)
    if n != len(regressor):
        raise ValueError(f"series ({n}) and regressor ({len(regressor)}) lengths differ")
    if n < 3:
        return math.nan
    y_bar = math.fsum(series) / n
    x_bar = math.fsum(regressor) / n
    sxx = math.fsum((x - x_bar) ** 2 for x in regressor)
    if sxx <= _DEGENERATE * math.fsum(x * x for x in regressor):
        return math.nan
    sxy = math.fsum((x - x_bar) * (y - y_bar) for x, y in zip(regressor, series, strict=True))
    slope = sxy / sxx
    intercept = y_bar - slope * x_bar
    ssr = math.fsum(
        (y - intercept - slope * x) ** 2 for x, y in zip(regressor, series, strict=True)
    )
    if ssr <= _DEGENERATE * math.fsum(y * y for y in series):
        return math.nan
    s2 = ssr / (n - 2)
    se = math.sqrt(s2 * (1.0 / n + x_bar * x_bar / sxx))
    return intercept / se


def _reaches(t: float, bound: float) -> bool:
    # NaN reaches no bound (fail-closed).
    return not math.isnan(t) and t >= bound


def exam_verdict(
    net_excess: Sequence[float],
    bench_over_cash: Sequence[float],
    frozen: ExamFrozen,
    *,
    window_ended: bool,
    minimums_met_by: Mapping[int, bool],
) -> ExamVerdict:
    """The forward return exam's verdict over the months given (ADR 0016, #3143).

    `net_excess[m - 1]` and `bench_over_cash[m - 1]` are month m's values; the two
    sequences must be the same length and finite. `window_ended` says the window has
    stopped, so no further month will come. `minimums_met_by[L]` says whether
    `paper.min_rebalances` and `paper.min_exam_fills` were both met by look L's
    month-end; a look missing from the map counts as unmet (fail-closed). Months after
    the last look are not read.
    """
    if len(net_excess) != len(bench_over_cash):
        raise ValueError(
            f"net_excess ({len(net_excess)}) and bench_over_cash ({len(bench_over_cash)}) "
            "must have one value per month"
        )
    if not all(math.isfinite(v) for v in (*net_excess, *bench_over_cash)):
        raise ValueError("the exam series must be finite")
    n = len(net_excess)
    looks: list[LookResult] = []

    def undecided(next_look: int) -> ExamVerdict:
        state: ExamState = "failed_void" if window_ended else "running"
        return ExamVerdict(
            state=state,
            month=None,
            next_look=None if window_ended else next_look,
            months_read=n,
            looks=tuple(looks),
        )

    first_look = frozen.looks_months[0]
    futility = frozen.futility_months
    if n < futility:
        return undecided(first_look)
    if math.fsum(net_excess[:futility]) < 0.0:
        return ExamVerdict("failed_futility", futility, None, futility, ())

    last_index = len(frozen.looks_months) - 1
    for index, (look, bound) in enumerate(zip(frozen.looks_months, frozen.bounds, strict=True)):
        if n < look:
            return undecided(look)
        y = net_excess[:look]
        x = bench_over_cash[:look]
        met = bool(minimums_met_by.get(look, False))
        t_mean = mean_t(y)
        t_int = intercept_t(y, x)
        total = math.fsum(y)
        passed = met and _reaches(t_mean, bound) and _reaches(t_int, bound)
        looks.append(LookResult(look, bound, t_mean, t_int, total, met, passed))
        if passed:
            return ExamVerdict("passed", look, None, look, tuple(looks))
        if index == last_index:
            state: ExamState = "failed" if met else "failed_void"
            return ExamVerdict(state, look, None, look, tuple(looks))
        if total < 0.0:
            return ExamVerdict("failed_futility", look, None, look, tuple(looks))
    raise AssertionError("unreachable: the last look always decides")  # pragma: no cover


def insample_skew(series: Sequence[float]) -> float:
    """The sample skewness m3 / m2^1.5 of `series` (central moments with divisor n),
    the in-sample monthly net excess skewness the skew guard reads (ADR 0016)."""
    n = len(series)
    if n < 3:
        raise ValueError(f"skewness needs at least 3 values, got {n}")
    if not all(math.isfinite(v) for v in series):
        raise ValueError("the in-sample series must be finite")
    mean = math.fsum(series) / n
    m2 = math.fsum((v - mean) ** 2 for v in series) / n
    if m2 <= _DEGENERATE * math.fsum(v * v for v in series) / n:
        raise ValueError("skewness is undefined for a constant series")
    m3 = math.fsum((v - mean) ** 3 for v in series) / n
    return m3 / (m2 * math.sqrt(m2))

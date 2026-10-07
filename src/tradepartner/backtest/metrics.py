"""Performance metrics, probabilistic and deflated Sharpe, red flag (backtest spec reqs 7, 8, 15).

Pure functions over plain sequences of returns and equity values.

- Standard metrics come from `empyrical` (ADR 0004 amendment 2026-09-25): CAGR,
  annualized volatility and tracking error (sample standard deviation), Sharpe, and max
  drawdown (a negative fraction).
- Skew and kurtosis are population moments (kurtosis non-excess, so a normal series
  gives 3), as Bailey & López de Prado use them.
- PSR and DSR are our own code.
- Period returns are the unit (strategy-lab spec "Period", req 8): the return between
  consecutive rebalance sessions at the hypothesis's cadence, annualised with that
  cadence's `periods_per_year` (`schedule.PERIODS_PER_YEAR`, a constant, not config).
  `MONTHS_PER_YEAR` is its `month_end` entry, re-exported here.
- The deflated Sharpe (req 9) is computed at the trial's own period, against V over
  the family's **annualised** Sharpes: SR*_annual from V and N, and the trial's
  SR* = SR*_annual / sqrt(periods_per_year).

Units: every return, CAGR and drawdown is a fraction (0.03 = 3%).
`metrics.risk_free_rate` is an annual rate, compounded to a per-period one.
`metrics.red_flag_excess_cagr_pp` is in percentage points.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

import empyrical
import numpy as np
import numpy.typing as npt

from tradepartner.backtest.schedule import MONTHS_PER_YEAR as MONTHS_PER_YEAR
from tradepartner.config import Settings

# Bailey & López de Prado's gamma in the expected maximum Sharpe (spec req 8).
EULER_GAMMA = np.euler_gamma

# `excess_cagr_spy` is a fraction; the red-flag threshold is in percentage points.
PERCENT_POINTS_PER_UNIT = 100

Series = Literal["strategy", "SPY", "MTUM"]
Basis = Literal["raw", "excess_spy"]

#: The strategy-lab spec's req 8 keys, in its order. The Phase 3 `*_monthly` and
#: `n_months` keys are no longer written; schema version 15 copied them to these.
METRIC_KEYS: tuple[str, ...] = (
    "cagr",
    "vol_annual",
    "sharpe_period",
    "sharpe_annual",
    "sharpe_period_excess_spy",
    "sharpe_annual_excess_spy",
    "max_drawdown",
    "turnover_period",
    "turnover_annual",
    "cost_drag",
    "excess_cagr_spy",
    "excess_cagr_mtum",
    "tracking_error_spy",
    "tracking_error_mtum",
    "skew_period",
    "kurtosis_period",
    "skew_period_excess_spy",
    "kurtosis_period_excess_spy",
    "n_periods",
    "periods_per_year",
)

# Stored as null for the SPY series: its excess over itself is identically zero (req 7).
EXCESS_SPY_KEYS: tuple[str, ...] = (
    "sharpe_period_excess_spy",
    "sharpe_annual_excess_spy",
    "skew_period_excess_spy",
    "kurtosis_period_excess_spy",
)

# The metric keys each DSR basis reads: (Sharpe, skew, kurtosis), all per period.
_BASIS_KEYS: dict[Basis, tuple[str, str, str]] = {
    "raw": ("sharpe_period", "skew_period", "kurtosis_period"),
    "excess_spy": (
        "sharpe_period_excess_spy",
        "skew_period_excess_spy",
        "kurtosis_period_excess_spy",
    ),
}

_NORMAL = NormalDist()

FloatArray = npt.NDArray[np.float64]


def _array(values: Sequence[float]) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _cagr(returns: FloatArray, periods_per_year: int) -> float:
    """Years = n_periods / periods_per_year."""
    return float(empyrical.cagr(returns, annualization=periods_per_year))


def _annualized_std(returns: FloatArray, periods_per_year: int) -> float:
    return float(empyrical.annual_volatility(returns, annualization=periods_per_year))


def _period_sharpe(returns: FloatArray, risk_free_period: float = 0.0) -> float:
    return float(empyrical.sharpe_ratio(returns, risk_free=risk_free_period, annualization=1))


def _skew(x: FloatArray) -> float:
    d = x - x.mean()
    m2 = float(np.mean(d**2))
    return float(np.mean(d * d * d)) / (m2 * math.sqrt(m2))


def _kurtosis(x: FloatArray) -> float:
    d = x - x.mean()
    m2 = float(np.mean(d**2))
    return float(np.mean(d**4)) / (m2 * m2)


def _require_finite(name: str, x: FloatArray) -> None:
    """empyrical skips NaN while the period still counts toward `n_periods`, which
    flatters CAGR; refuse any non-finite value instead."""
    if not bool(np.all(np.isfinite(x))):
        raise ValueError(f"{name} must be finite, got a NaN or infinite value")


def _require_variance(name: str, x: FloatArray) -> None:
    """A constant series has no Sharpe, skew or kurtosis. That includes a series that
    differs from a constant only by rounding noise (its standard deviation is within
    one float epsilon of its scale), whose Sharpe would be arbitrary. Refuse it rather
    than let a NaN, a division by zero or a meaningless Sharpe reach the stored
    metrics and the DSR."""
    scale = max(1.0, abs(float(np.mean(x))))
    if float(np.std(x)) <= float(np.finfo(np.float64).eps) * scale:
        raise ValueError(f"{name} has zero variance: Sharpe, skew and kurtosis are undefined")


def series_metrics(
    series: Series,
    *,
    period_returns: Sequence[float],
    gross_period_returns: Sequence[float],
    daily_equity: Sequence[float],
    turnover: Sequence[float],
    spy_period_returns: Sequence[float],
    mtum_period_returns: Sequence[float],
    periods_per_year: int,
    risk_free_rate: float,
) -> dict[str, float | None]:
    """Every req 8 key for one series at one cost level.

    `period_returns` holds this series' period returns net of costs at this level, one
    per rebalance period at the hypothesis's cadence; `periods_per_year` is that
    cadence's constant (`schedule.periods_per_year`). `gross_period_returns` holds the
    same series at zero cost, for `cost_drag`. `spy_period_returns` and
    `mtum_period_returns` are the benchmarks' returns over the same periods.
    `daily_equity` is the equity on every session, for `max_drawdown`. `turnover` is the
    one-sided turnover per rebalance; an empty sequence gives 0. `risk_free_rate` is
    annual, compounded to the period. The `*_excess_spy` keys are None when `series` is
    `SPY`. Raises `ValueError` when the series (or, except for SPY, its excess over SPY)
    is constant, or `periods_per_year` is not positive.
    """
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    ppy = periods_per_year
    net = _array(period_returns)
    n = len(net)
    for name, other in (
        ("gross_period_returns", gross_period_returns),
        ("spy_period_returns", spy_period_returns),
        ("mtum_period_returns", mtum_period_returns),
    ):
        if len(other) != n:
            raise ValueError(f"{name} has {len(other)} periods, period_returns has {n}")
    if n < 2:
        raise ValueError(f"need at least 2 period returns for a Sharpe ratio, got {n}")
    equity = _array(daily_equity)
    gross = _array(gross_period_returns)
    spy, mtum = _array(spy_period_returns), _array(mtum_period_returns)
    turnover_values = _array(turnover)
    for name, values in (
        ("period_returns", net),
        ("gross_period_returns", gross),
        ("spy_period_returns", spy),
        ("mtum_period_returns", mtum),
        ("daily_equity", equity),
        ("turnover", turnover_values),
    ):
        _require_finite(name, values)
    if len(equity) == 0 or bool(np.any(equity <= 0)):
        raise ValueError("daily_equity must be non-empty and strictly positive")

    ex_spy, ex_mtum = net - spy, net - mtum
    _require_variance("period_returns", net)
    if series != "SPY":
        _require_variance("period_returns minus spy_period_returns", ex_spy)
    rf_period = (1 + risk_free_rate) ** (1 / ppy) - 1
    root_ppy = math.sqrt(ppy)
    cagr = _cagr(net, ppy)
    sharpe_period = _period_sharpe(net, rf_period)
    turnover_period = float(np.mean(turnover_values)) if len(turnover_values) else 0.0
    daily_returns = equity[1:] / equity[:-1] - 1

    out: dict[str, float | None] = {
        "cagr": cagr,
        "vol_annual": _annualized_std(net, ppy),
        "sharpe_period": sharpe_period,
        "sharpe_annual": sharpe_period * root_ppy,
        "sharpe_period_excess_spy": None,
        "sharpe_annual_excess_spy": None,
        "max_drawdown": float(empyrical.max_drawdown(daily_returns)) if len(daily_returns) else 0.0,
        "turnover_period": turnover_period,
        "turnover_annual": turnover_period * ppy,
        "cost_drag": _cagr(gross, ppy) - cagr,
        "excess_cagr_spy": cagr - _cagr(spy, ppy),
        "excess_cagr_mtum": cagr - _cagr(mtum, ppy),
        "tracking_error_spy": _annualized_std(ex_spy, ppy),
        "tracking_error_mtum": _annualized_std(ex_mtum, ppy),
        "skew_period": _skew(net),
        "kurtosis_period": _kurtosis(net),
        "skew_period_excess_spy": None,
        "kurtosis_period_excess_spy": None,
        "n_periods": float(n),
        "periods_per_year": float(ppy),
    }
    if series != "SPY":
        sharpe_excess = _period_sharpe(ex_spy)
        out["sharpe_period_excess_spy"] = sharpe_excess
        out["sharpe_annual_excess_spy"] = sharpe_excess * root_ppy
        out["skew_period_excess_spy"] = _skew(ex_spy)
        out["kurtosis_period_excess_spy"] = _kurtosis(ex_spy)
    return out


def expected_max_sharpe(n_trials: int, variance: float) -> float:
    """SR*: the expected maximum Sharpe of `n_trials` unskilled trials (spec req 8).

    SR* = sqrt(V) * ((1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N*e))).
    Zero for a single trial, where Phi^-1(0) is undefined and there is nothing to deflate.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be at least 1, got {n_trials}")
    if variance < 0:
        raise ValueError(f"variance must be non-negative, got {variance}")
    if n_trials == 1:
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def probabilistic_sharpe(
    sharpe: float, sharpe_benchmark: float, n_periods: int, skew: float, kurtosis: float
) -> float:
    """PSR: the probability that the true per-period Sharpe exceeds `sharpe_benchmark`.

    Phi((SR - SR_b) * sqrt(T - 1) / sqrt(1 - skew*SR + (kurtosis - 1)/4 * SR^2)), with a
    per-period, non-annualized SR, T periods and non-excess kurtosis.
    """
    if n_periods < 2:
        raise ValueError(f"n_periods must be at least 2, got {n_periods}")
    variance_term = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if not variance_term > 0:
        raise ValueError(
            f"PSR variance term 1 - skew*SR + (kurtosis-1)/4*SR^2 = {variance_term} is not "
            f"positive (SR={sharpe}, skew={skew}, kurtosis={kurtosis})"
        )
    z = (sharpe - sharpe_benchmark) * math.sqrt(n_periods - 1) / math.sqrt(variance_term)
    return _NORMAL.cdf(z)


@dataclass(frozen=True)
class DeflatedSharpe:
    """One DSR basis for one trial (strategy-lab spec req 9).

    `sharpe_variance` (V) and `sr_star` (SR*_annual) are in **annual** units, as
    `trial_results` stores them with `sharpe_unit = annual`; `sr_star_period` is the
    trial's own threshold, SR*_annual / sqrt(periods_per_year), which the DSR uses."""

    basis: Basis
    dsr_basis: Literal["dsr", "psr"]
    n_trials: int
    sharpe_variance: float | None
    sr_star: float
    sr_star_period: float
    psr_zero: float
    dsr: float


def deflated_sharpe(
    metrics: Mapping[str, float | None],
    basis: Basis,
    *,
    n_trials: int,
    pair_sharpes: Sequence[float],
    periods_per_year: int,
) -> DeflatedSharpe:
    """DSR of one trial on `basis`, from its base-level `metrics` (req 9).

    `n_trials` is N: the family's `ok`, non-synthetic, in-sample trials, reruns and
    this one included (`results.family_n`). `pair_sharpes` are this basis' **annualised**
    Sharpes of the latest `ok` trial for each distinct (canonical frozen set, window)
    pair; V is their sample variance, SR*_annual = `expected_max_sharpe(N, V)` and the
    trial's SR* = SR*_annual / sqrt(`periods_per_year`), compared with its per-period
    Sharpe over its `n_periods`. With fewer than two pairs, SR* = 0 and the result is
    labelled `psr`. `periods_per_year` is the trial's cadence constant; when `metrics`
    holds a `periods_per_year` row it must agree.
    """
    if n_trials < max(1, len(pair_sharpes)):
        raise ValueError(
            f"n_trials ({n_trials}) must be at least 1 and at least the number of "
            f"pairs ({len(pair_sharpes)}): N counts every trial, reruns included"
        )
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    stored_ppy = metrics.get("periods_per_year")
    if stored_ppy is not None and stored_ppy != periods_per_year:
        raise ValueError(
            f"metrics were computed at {stored_ppy} periods per year, not {periods_per_year}"
        )
    sharpe_key, skew_key, kurtosis_key = _BASIS_KEYS[basis]
    sharpe, skew, kurtosis = metrics[sharpe_key], metrics[skew_key], metrics[kurtosis_key]
    n_periods = metrics["n_periods"]
    if sharpe is None or skew is None or kurtosis is None or n_periods is None:
        raise ValueError(f"basis {basis} needs {sharpe_key}, {skew_key}, {kurtosis_key}")
    _require_finite(f"{basis} basis inputs", _array([sharpe, skew, kurtosis, n_periods]))
    _require_finite("pair_sharpes", _array(pair_sharpes))
    periods = int(n_periods)
    psr_zero = probabilistic_sharpe(sharpe, 0.0, periods, skew, kurtosis)
    if len(pair_sharpes) < 2:
        return DeflatedSharpe(basis, "psr", n_trials, None, 0.0, 0.0, psr_zero, psr_zero)
    variance = float(np.var(_array(pair_sharpes), ddof=1))
    sr_star_annual = expected_max_sharpe(n_trials, variance)
    sr_star_period = sr_star_annual / math.sqrt(periods_per_year)
    dsr = probabilistic_sharpe(sharpe, sr_star_period, periods, skew, kurtosis)
    return DeflatedSharpe(
        basis, "dsr", n_trials, variance, sr_star_annual, sr_star_period, psr_zero, dsr
    )


def red_flag(metrics: Mapping[str, float | None], settings: Settings) -> bool:
    """True when base-level `excess_cagr_spy` is strictly above the configured
    threshold (spec req 15). A prompt for a look-ahead and cost audit, never a gate.

    The threshold is converted to a fraction (divided, not the excess multiplied), so an
    excess exactly at the threshold is not flagged: 7 / 100 is the same double as 0.07,
    while 0.07 * 100 is 7.000000000000001.
    """
    excess = metrics["excess_cagr_spy"]
    if excess is None:
        raise ValueError("excess_cagr_spy is required for the red flag")
    return excess > settings.metrics.red_flag_excess_cagr_pp / PERCENT_POINTS_PER_UNIT

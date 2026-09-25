"""Performance metrics, probabilistic and deflated Sharpe, red flag (backtest spec reqs 7, 8, 15).

Pure functions over plain sequences of returns and equity values.

- Standard metrics come from `empyrical` (ADR 0004 amendment 2026-09-25): CAGR,
  annualized volatility and tracking error (sample standard deviation), Sharpe, and max
  drawdown (a negative fraction).
- Skew and kurtosis are population moments (kurtosis non-excess, so a normal series
  gives 3), as Bailey & López de Prado use them.
- PSR and DSR are our own code.
- Monthly returns are the unit (spec: "Month i return"), and `MONTHS_PER_YEAR` is
  derived from the ADR 0006 monthly cadence, not config.

Units: every return, CAGR and drawdown is a fraction (0.03 = 3%).
`metrics.risk_free_rate` is an annual rate, compounded to a monthly one.
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

from tradepartner.config import Settings

MONTHS_PER_YEAR = 12

# Bailey & López de Prado's gamma in the expected maximum Sharpe (spec req 8).
EULER_GAMMA = np.euler_gamma

# `excess_cagr_spy` is a fraction; the red-flag threshold is in percentage points.
PERCENT_POINTS_PER_UNIT = 100

Series = Literal["strategy", "SPY", "MTUM"]
Basis = Literal["raw", "excess_spy"]

METRIC_KEYS: tuple[str, ...] = (
    "cagr",
    "vol_annual",
    "sharpe_monthly",
    "sharpe_annual",
    "sharpe_monthly_excess_spy",
    "max_drawdown",
    "turnover_monthly",
    "cost_drag",
    "excess_cagr_spy",
    "excess_cagr_mtum",
    "tracking_error_spy",
    "tracking_error_mtum",
    "skew_monthly",
    "kurtosis_monthly",
    "skew_monthly_excess_spy",
    "kurtosis_monthly_excess_spy",
    "n_months",
)

# Stored as null for the SPY series: its excess over itself is identically zero (req 7).
EXCESS_SPY_KEYS: tuple[str, ...] = (
    "sharpe_monthly_excess_spy",
    "skew_monthly_excess_spy",
    "kurtosis_monthly_excess_spy",
)

# The metric keys each DSR basis reads: (Sharpe, skew, kurtosis).
_BASIS_KEYS: dict[Basis, tuple[str, str, str]] = {
    "raw": ("sharpe_monthly", "skew_monthly", "kurtosis_monthly"),
    "excess_spy": (
        "sharpe_monthly_excess_spy",
        "skew_monthly_excess_spy",
        "kurtosis_monthly_excess_spy",
    ),
}

_NORMAL = NormalDist()

FloatArray = npt.NDArray[np.float64]


def _array(values: Sequence[float]) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _cagr(monthly: FloatArray) -> float:
    return float(empyrical.cagr(monthly, annualization=MONTHS_PER_YEAR))


def _annualized_std(monthly: FloatArray) -> float:
    return float(empyrical.annual_volatility(monthly, annualization=MONTHS_PER_YEAR))


def _monthly_sharpe(monthly: FloatArray, risk_free_monthly: float = 0.0) -> float:
    return float(empyrical.sharpe_ratio(monthly, risk_free=risk_free_monthly, annualization=1))


def _skew(x: FloatArray) -> float:
    d = x - x.mean()
    m2 = float(np.mean(d**2))
    return float(np.mean(d * d * d)) / (m2 * math.sqrt(m2))


def _kurtosis(x: FloatArray) -> float:
    d = x - x.mean()
    m2 = float(np.mean(d**2))
    return float(np.mean(d**4)) / (m2 * m2)


def _require_variance(name: str, x: FloatArray) -> None:
    """A constant series has no Sharpe, skew or kurtosis; refuse it rather than let a
    NaN or a division by zero reach the stored metrics and the DSR."""
    if float(np.ptp(x)) == 0:
        raise ValueError(f"{name} has zero variance: Sharpe, skew and kurtosis are undefined")


def series_metrics(
    series: Series,
    *,
    monthly: Sequence[float],
    gross_monthly: Sequence[float],
    daily_equity: Sequence[float],
    turnover: Sequence[float],
    spy_monthly: Sequence[float],
    mtum_monthly: Sequence[float],
    risk_free_rate: float,
) -> dict[str, float | None]:
    """Every req 7 key for one series at one cost level.

    `monthly` holds this series' month returns net of costs at this level.
    `gross_monthly` holds the same series at zero cost, for `cost_drag`. `spy_monthly` and
    `mtum_monthly` are the benchmarks' month returns over the same months. `daily_equity`
    is the equity on every session, for `max_drawdown`. `turnover` is the one-sided
    turnover per rebalance; an empty sequence gives 0. `risk_free_rate` is annual.
    The `*_excess_spy` keys are None when `series` is `SPY`. Raises `ValueError` when
    the series (or, except for SPY, its excess over SPY) is constant.
    """
    net = _array(monthly)
    n = len(net)
    for name, other in (
        ("gross_monthly", gross_monthly),
        ("spy_monthly", spy_monthly),
        ("mtum_monthly", mtum_monthly),
    ):
        if len(other) != n:
            raise ValueError(f"{name} has {len(other)} months, monthly has {n}")
    if n < 2:
        raise ValueError(f"need at least 2 monthly returns for a Sharpe ratio, got {n}")
    equity = _array(daily_equity)
    if len(equity) == 0 or bool(np.any(equity <= 0)):
        raise ValueError("daily_equity must be non-empty and strictly positive")

    gross, spy, mtum = _array(gross_monthly), _array(spy_monthly), _array(mtum_monthly)
    ex_spy, ex_mtum = net - spy, net - mtum
    rf_monthly = (1 + risk_free_rate) ** (1 / MONTHS_PER_YEAR) - 1
    cagr = _cagr(net)
    sharpe_monthly = _monthly_sharpe(net, rf_monthly)
    daily_returns = equity[1:] / equity[:-1] - 1

    _require_variance("monthly", net)
    out: dict[str, float | None] = {
        "cagr": cagr,
        "vol_annual": _annualized_std(net),
        "sharpe_monthly": sharpe_monthly,
        "sharpe_annual": sharpe_monthly * math.sqrt(MONTHS_PER_YEAR),
        "sharpe_monthly_excess_spy": None,
        "max_drawdown": float(empyrical.max_drawdown(daily_returns)) if len(daily_returns) else 0.0,
        "turnover_monthly": float(np.mean(_array(turnover))) if len(turnover) else 0.0,
        "cost_drag": _cagr(gross) - cagr,
        "excess_cagr_spy": cagr - _cagr(spy),
        "excess_cagr_mtum": cagr - _cagr(mtum),
        "tracking_error_spy": _annualized_std(ex_spy),
        "tracking_error_mtum": _annualized_std(ex_mtum),
        "skew_monthly": _skew(net),
        "kurtosis_monthly": _kurtosis(net),
        "skew_monthly_excess_spy": None,
        "kurtosis_monthly_excess_spy": None,
        "n_months": float(n),
    }
    if series != "SPY":
        _require_variance("monthly minus spy_monthly", ex_spy)
        out["sharpe_monthly_excess_spy"] = _monthly_sharpe(ex_spy)
        out["skew_monthly_excess_spy"] = _skew(ex_spy)
        out["kurtosis_monthly_excess_spy"] = _kurtosis(ex_spy)
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
    sharpe: float, sharpe_benchmark: float, n_months: int, skew: float, kurtosis: float
) -> float:
    """PSR: the probability that the true monthly Sharpe exceeds `sharpe_benchmark`.

    Phi((SR - SR_b) * sqrt(T - 1) / sqrt(1 - skew*SR + (kurtosis - 1)/4 * SR^2)), with a
    monthly, non-annualized SR and non-excess kurtosis.
    """
    if n_months < 2:
        raise ValueError(f"n_months must be at least 2, got {n_months}")
    variance_term = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if not variance_term > 0:
        raise ValueError(
            f"PSR variance term 1 - skew*SR + (kurtosis-1)/4*SR^2 = {variance_term} is not "
            f"positive (SR={sharpe}, skew={skew}, kurtosis={kurtosis})"
        )
    z = (sharpe - sharpe_benchmark) * math.sqrt(n_months - 1) / math.sqrt(variance_term)
    return _NORMAL.cdf(z)


@dataclass(frozen=True)
class DeflatedSharpe:
    """One DSR basis for one trial, as stored in `trial_results` (spec req 8)."""

    basis: Basis
    dsr_basis: Literal["dsr", "psr"]
    n_trials: int
    sharpe_variance: float | None
    sr_star: float
    psr_zero: float
    dsr: float


def deflated_sharpe(
    metrics: Mapping[str, float | None],
    basis: Basis,
    *,
    n_trials: int,
    pair_sharpes: Sequence[float],
) -> DeflatedSharpe:
    """DSR of one trial on `basis`, from its base-level `metrics`.

    `n_trials` is N: the family's `ok`, non-synthetic, in-sample trials, reruns and
    this one included. `pair_sharpes` are this basis' monthly Sharpes of the latest
    `ok` trial for each distinct (parameter hash, window) pair; V is their sample
    variance. With fewer than two pairs, SR* = 0 and the result is labelled `psr`.
    """
    if n_trials < max(1, len(pair_sharpes)):
        raise ValueError(
            f"n_trials ({n_trials}) must be at least 1 and at least the number of "
            f"pairs ({len(pair_sharpes)}): N counts every trial, reruns included"
        )
    sharpe_key, skew_key, kurtosis_key = _BASIS_KEYS[basis]
    sharpe, skew, kurtosis = metrics[sharpe_key], metrics[skew_key], metrics[kurtosis_key]
    n_months = metrics["n_months"]
    if sharpe is None or skew is None or kurtosis is None or n_months is None:
        raise ValueError(f"basis {basis} needs {sharpe_key}, {skew_key}, {kurtosis_key}")
    months = int(n_months)
    psr_zero = probabilistic_sharpe(sharpe, 0.0, months, skew, kurtosis)
    if len(pair_sharpes) < 2:
        return DeflatedSharpe(basis, "psr", n_trials, None, 0.0, psr_zero, psr_zero)
    variance = float(np.var(_array(pair_sharpes), ddof=1))
    sr_star = expected_max_sharpe(n_trials, variance)
    dsr = probabilistic_sharpe(sharpe, sr_star, months, skew, kurtosis)
    return DeflatedSharpe(basis, "dsr", n_trials, variance, sr_star, psr_zero, dsr)


def red_flag(metrics: Mapping[str, float | None], settings: Settings) -> bool:
    """True when base-level `excess_cagr_spy` is strictly above the configured
    threshold (spec req 15). A prompt for a look-ahead and cost audit, never a gate."""
    excess = metrics["excess_cagr_spy"]
    if excess is None:
        raise ValueError("excess_cagr_spy is required for the red flag")
    return excess * PERCENT_POINTS_PER_UNIT > settings.metrics.red_flag_excess_cagr_pp

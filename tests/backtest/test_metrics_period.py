"""Metrics at a weekly cadence (strategy-lab spec req 8, plan T97).

Every req 8 key on a synthetic weekly series against values computed here from first
principles in plain Python with `periods_per_year = 52`: `sharpe_annual = sharpe_period
x sqrt(52)`, `turnover_annual = 52 x turnover_period`, `cagr` over `n_periods / 52`
years, the risk-free rate compounded to the week.
"""

from __future__ import annotations

import math

import pytest

from tradepartner.backtest.metrics import METRIC_KEYS, series_metrics
from tradepartner.backtest.schedule import PERIODS_PER_YEAR

PPY = PERIODS_PER_YEAR["week_end"]

NET = [0.004, -0.002, 0.006, 0.001, -0.003, 0.005]
GROSS = [0.0045, -0.0015, 0.0065, 0.0015, -0.0025, 0.0055]
SPY = [0.002, -0.004, 0.004, 0.0, -0.001, 0.003]
MTUM = [0.003, -0.003, 0.005, 0.0004, -0.002, 0.004]
DAILY_EQUITY = [100.0, 100.4, 99.9, 100.6, 100.2, 99.8, 100.9]
TURNOVER = [1.0, 0.1, 0.25, 0.15, 0.3, 0.2]
RF = 0.03


def _mean(x: list[float]) -> float:
    return sum(x) / len(x)


def _sample_std(x: list[float]) -> float:
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def _moment(x: list[float], k: int) -> float:
    m = _mean(x)
    return sum((v - m) ** k for v in x) / len(x)


def _skew(x: list[float]) -> float:
    return _moment(x, 3) / _moment(x, 2) ** 1.5


def _kurt(x: list[float]) -> float:
    return _moment(x, 4) / _moment(x, 2) ** 2


def _cagr(x: list[float]) -> float:
    years = len(x) / 52
    return math.prod(1 + v for v in x) ** (1 / years) - 1


def _diff(a: list[float], b: list[float]) -> list[float]:
    return [u - v for u, v in zip(a, b, strict=True)]


def _weekly(series: str = "strategy") -> dict[str, float | None]:
    return series_metrics(
        series,  # type: ignore[arg-type]
        period_returns=NET,
        gross_period_returns=GROSS,
        daily_equity=DAILY_EQUITY,
        turnover=TURNOVER,
        spy_period_returns=SPY,
        mtum_period_returns=MTUM,
        periods_per_year=PPY,
        risk_free_rate=RF,
    )


def test_weekly_cadence_is_52_periods_per_year() -> None:
    assert PPY == 52


def test_every_key_matches_hand_computed_weekly_values() -> None:
    m = _weekly()
    rf_week = (1 + RF) ** (1 / 52) - 1
    net_rf = [v - rf_week for v in NET]
    ex_spy = _diff(NET, SPY)
    sharpe_period = _mean(net_rf) / _sample_std(net_rf)
    sharpe_excess = _mean(ex_spy) / _sample_std(ex_spy)
    turnover_period = sum(TURNOVER) / len(TURNOVER)
    expected: dict[str, float] = {
        "cagr": _cagr(NET),
        "vol_annual": _sample_std(NET) * math.sqrt(52),
        "sharpe_period": sharpe_period,
        "sharpe_annual": sharpe_period * math.sqrt(52),
        "sharpe_period_excess_spy": sharpe_excess,
        "sharpe_annual_excess_spy": sharpe_excess * math.sqrt(52),
        "max_drawdown": 99.8 / 100.6 - 1,
        "turnover_period": turnover_period,
        "turnover_annual": 52 * turnover_period,
        "cost_drag": _cagr(GROSS) - _cagr(NET),
        "excess_cagr_spy": _cagr(NET) - _cagr(SPY),
        "excess_cagr_mtum": _cagr(NET) - _cagr(MTUM),
        "tracking_error_spy": _sample_std(ex_spy) * math.sqrt(52),
        "tracking_error_mtum": _sample_std(_diff(NET, MTUM)) * math.sqrt(52),
        "skew_period": _skew(NET),
        "kurtosis_period": _kurt(NET),
        "skew_period_excess_spy": _skew(ex_spy),
        "kurtosis_period_excess_spy": _kurt(ex_spy),
        "n_periods": 6.0,
        "periods_per_year": 52.0,
    }
    assert set(expected) == set(METRIC_KEYS) == set(m)
    for key, value in expected.items():
        assert m[key] == pytest.approx(value, rel=1e-12, abs=1e-15), key


def test_weekly_cagr_uses_n_periods_over_52_years() -> None:
    m = _weekly()
    growth = math.prod(1 + v for v in NET)
    assert m["cagr"] == pytest.approx(growth ** (52 / 6) - 1, rel=1e-12)
    assert m["n_periods"] == 6.0


def test_weekly_spy_excess_keys_are_null() -> None:
    spy = _weekly("SPY")
    assert spy["sharpe_period_excess_spy"] is None
    assert spy["sharpe_annual_excess_spy"] is None
    assert spy["periods_per_year"] == 52.0

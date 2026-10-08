"""Tests for `tradepartner.backtest.metrics` (backtest spec reqs 7, 8 and 15, plan T32).

Expected values are computed here from first principles in plain Python (no empyrical,
no numpy), so the module's use of the library is checked, not repeated.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from tradepartner.backtest.metrics import (
    EXCESS_SPY_KEYS,
    METRIC_KEYS,
    MONTHS_PER_YEAR,
    DeflatedSharpe,
    deflated_sharpe,
    expected_max_sharpe,
    metric_keys,
    probabilistic_sharpe,
    red_flag,
    series_metrics,
)
from tradepartner.config import Settings

BACKTEST_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "backtest"
METRICS_PY = BACKTEST_SRC / "metrics.py"
SCHEDULE_PY = BACKTEST_SRC / "schedule.py"

NET = [0.02, -0.01, 0.03, 0.005]
GROSS = [0.021, -0.009, 0.031, 0.006]
SPY = [0.01, -0.02, 0.02, 0.0]
MTUM = [0.015, -0.015, 0.025, 0.002]
DAILY_EQUITY = [100.0, 102.0, 99.0, 101.0, 98.0, 103.0]
TURNOVER = [1.0, 0.2, 0.3, 0.1]


# --- hand arithmetic -------------------------------------------------------------


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
    return math.prod(1 + v for v in x) ** (12 / len(x)) - 1


def _diff(a: list[float], b: list[float]) -> list[float]:
    return [u - v for u, v in zip(a, b, strict=True)]


def _metrics(series: str = "strategy", rf: float = 0.0) -> dict[str, float | None]:
    return series_metrics(
        series,  # type: ignore[arg-type]
        period_returns=NET,
        gross_period_returns=GROSS,
        daily_equity=DAILY_EQUITY,
        turnover=TURNOVER,
        spy_period_returns=SPY,
        mtum_period_returns=MTUM,
        periods_per_year=12,
        risk_free_rate=rf,
    )


# --- req 7: every key, hand-computed ---------------------------------------------


def test_metric_keys_are_exactly_the_spec_list() -> None:
    assert METRIC_KEYS == (
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
    assert not any("monthly" in key or "months" in key for key in METRIC_KEYS)
    assert set(_metrics()) == set(METRIC_KEYS)


def test_every_key_matches_hand_computed_values() -> None:
    m = _metrics()
    ex_spy = _diff(NET, SPY)
    expected = {
        "cagr": _cagr(NET),
        "vol_annual": _sample_std(NET) * math.sqrt(12),
        "sharpe_period": _mean(NET) / _sample_std(NET),
        "sharpe_annual": _mean(NET) / _sample_std(NET) * math.sqrt(12),
        "sharpe_period_excess_spy": _mean(ex_spy) / _sample_std(ex_spy),
        "sharpe_annual_excess_spy": _mean(ex_spy) / _sample_std(ex_spy) * math.sqrt(12),
        "max_drawdown": 98.0 / 102.0 - 1,
        "turnover_period": 0.4,
        "turnover_annual": 0.4 * 12,
        "cost_drag": _cagr(GROSS) - _cagr(NET),
        "excess_cagr_spy": _cagr(NET) - _cagr(SPY),
        "excess_cagr_mtum": _cagr(NET) - _cagr(MTUM),
        "tracking_error_spy": _sample_std(ex_spy) * math.sqrt(12),
        "tracking_error_mtum": _sample_std(_diff(NET, MTUM)) * math.sqrt(12),
        "skew_period": _skew(NET),
        "kurtosis_period": _kurt(NET),
        "skew_period_excess_spy": _skew(ex_spy),
        "kurtosis_period_excess_spy": _kurt(ex_spy),
        "n_periods": 4.0,
        "periods_per_year": 12.0,
    }
    assert set(expected) == set(METRIC_KEYS)
    for key, value in expected.items():
        assert m[key] == pytest.approx(value, rel=1e-12, abs=1e-15), key


def test_sharpe_annual_is_sharpe_period_times_root_twelve_at_month_end() -> None:
    m = _metrics()
    assert m["sharpe_annual"] == pytest.approx(m["sharpe_period"] * math.sqrt(12), rel=1e-15)  # type: ignore[operator]
    assert MONTHS_PER_YEAR == 12


def test_risk_free_rate_is_annual_and_compounded_to_the_period() -> None:
    rf_month = 1.12 ** (1 / 12) - 1
    shifted = [v - rf_month for v in NET]
    m = _metrics(rf=0.12)
    assert m["sharpe_period"] == pytest.approx(_mean(shifted) / _sample_std(shifted), rel=1e-12)
    # The excess-over-SPY Sharpe subtracts SPY, not cash.
    assert m["sharpe_period_excess_spy"] == _metrics()["sharpe_period_excess_spy"]


def test_excess_spy_keys_are_null_for_spy_only() -> None:
    assert set(EXCESS_SPY_KEYS) == {
        "sharpe_period_excess_spy",
        "sharpe_annual_excess_spy",
        "skew_period_excess_spy",
        "kurtosis_period_excess_spy",
    }
    spy = _metrics("SPY")
    assert all(spy[k] is None for k in EXCESS_SPY_KEYS)
    assert all(spy[k] is not None for k in METRIC_KEYS if k not in EXCESS_SPY_KEYS)
    for series in ("strategy", "MTUM"):
        assert all(_metrics(series)[k] is not None for k in EXCESS_SPY_KEYS)


def test_empty_turnover_is_zero() -> None:
    m = series_metrics(
        "SPY",
        period_returns=NET,
        gross_period_returns=GROSS,
        daily_equity=DAILY_EQUITY,
        turnover=[],
        spy_period_returns=SPY,
        mtum_period_returns=MTUM,
        periods_per_year=12,
        risk_free_rate=0.0,
    )
    assert m["turnover_period"] == 0.0
    assert m["turnover_annual"] == 0.0


@pytest.mark.parametrize(
    "override",
    [
        {"gross_period_returns": GROSS[:3]},
        {"spy_period_returns": SPY[:3]},
        {"mtum_period_returns": [*MTUM, 0.0]},
        {
            "period_returns": [0.01],
            "gross_period_returns": [0.01],
            "spy_period_returns": [0.0],
            "mtum_period_returns": [0.0],
        },
        {"periods_per_year": 0},
        {"daily_equity": []},
        {"daily_equity": [100.0, 0.0, 50.0]},
    ],
)
def test_series_metrics_rejects_misaligned_or_degenerate_inputs(
    override: dict[str, list[float]],
) -> None:
    kwargs: dict[str, object] = {
        "period_returns": NET,
        "gross_period_returns": GROSS,
        "daily_equity": DAILY_EQUITY,
        "turnover": TURNOVER,
        "spy_period_returns": SPY,
        "mtum_period_returns": MTUM,
        "periods_per_year": 12,
        "risk_free_rate": 0.0,
    }
    kwargs.update(override)
    with pytest.raises(ValueError):
        series_metrics("strategy", **kwargs)  # type: ignore[arg-type]


# --- req 8: deflated Sharpe ------------------------------------------------------

REF_SR, REF_T, REF_SKEW, REF_KURT = 0.25, 120, -0.5, 4.0
# Sample variance (ddof=1) of [0, 0.1, 0.2] is exactly 0.01, the spec's reference
# V_monthly. Pair Sharpes are annualised now (strategy-lab spec req 9), so the
# reference family is those monthly Sharpes times sqrt(12), whose variance is 0.12.
PAIRS_V_001 = [v * math.sqrt(12) for v in (0.0, 0.1, 0.2)]


def _ref_metrics(
    sr: float = REF_SR,
    skew: float = REF_SKEW,
    kurt: float = REF_KURT,
    t: int = REF_T,
    ppy: int = 12,
) -> dict[str, float | None]:
    return {
        "sharpe_period": sr,
        "skew_period": skew,
        "kurtosis_period": kurt,
        "sharpe_period_excess_spy": sr,
        "skew_period_excess_spy": skew,
        "kurtosis_period_excess_spy": kurt,
        "n_periods": float(t),
        "periods_per_year": float(ppy),
    }


def test_euler_gamma_is_numpy_constant() -> None:
    import tradepartner.backtest.metrics as metrics

    assert metrics.EULER_GAMMA is np.euler_gamma


def test_spec_reference_values_n20() -> None:
    """The Phase 3 reference values hold with V supplied annualised (0.12) and SR*
    divided by sqrt(12) (strategy-lab spec, "Deflated Sharpe")."""
    assert expected_max_sharpe(20, 0.01) == pytest.approx(0.190071, abs=1e-6)
    assert expected_max_sharpe(20, 0.12) == pytest.approx(0.658425, abs=1e-6)
    assert probabilistic_sharpe(REF_SR, 0.0, REF_T, REF_SKEW, REF_KURT) == pytest.approx(
        0.994120, abs=1e-6
    )
    d = _ref_dsr(20, PAIRS_V_001)
    assert d == DeflatedSharpe(
        basis="raw",
        dsr_basis="dsr",
        n_trials=20,
        sharpe_variance=pytest.approx(0.12, rel=1e-12),  # type: ignore[arg-type]
        sr_star=pytest.approx(0.658425, abs=1e-6),  # type: ignore[arg-type]
        sr_star_period=pytest.approx(0.190071, abs=1e-6),  # type: ignore[arg-type]
        psr_zero=pytest.approx(0.994120, abs=1e-6),  # type: ignore[arg-type]
        dsr=pytest.approx(0.727048, abs=1e-6),  # type: ignore[arg-type]
    )
    assert d.sr_star_period == pytest.approx(d.sr_star / math.sqrt(12), rel=1e-15)


def test_spec_reference_values_n100() -> None:
    d = _ref_dsr(100, PAIRS_V_001)
    assert d.sr_star_period == pytest.approx(0.253060, abs=1e-6)
    assert d.dsr == pytest.approx(0.487699, abs=1e-6)


def _ref_dsr(
    n_trials: int, pairs: list[float], *, sr: float = REF_SR, t: int = REF_T, ppy: int = 12
) -> DeflatedSharpe:
    return deflated_sharpe(
        _ref_metrics(sr=sr, t=t, ppy=ppy),
        "raw",
        n_trials=n_trials,
        pair_sharpes=pairs,
        periods_per_year=ppy,
    )


@pytest.mark.parametrize(
    ("ppy", "sharpe_period", "t", "sr_star", "dsr"),
    [
        (52, 0.120096, 520, 0.091307, 0.736890),
        (252, 0.054554, 2_520, 0.041477, 0.741147),
    ],
)
def test_spec_reference_values_weekly_and_daily(
    ppy: int, sharpe_period: float, t: int, sr_star: float, dsr: float
) -> None:
    """A weekly and a daily trial with the reference trial's annual Sharpe (0.25 *
    sqrt(12)), ten years of periods, the same skew and kurtosis and the same family V
    (strategy-lab spec, "Deflated Sharpe"; all to 1e-6)."""
    exact = REF_SR * math.sqrt(12 / ppy)  # the spec prints it rounded to 1e-6
    assert exact == pytest.approx(sharpe_period, abs=1e-6)
    d = _ref_dsr(20, PAIRS_V_001, sr=exact, t=t, ppy=ppy)
    assert d.sr_star == pytest.approx(0.658425, abs=1e-6)
    assert d.sr_star_period == pytest.approx(sr_star, abs=1e-6)
    assert d.dsr == pytest.approx(dsr, abs=1e-6)


def _phase3_dsr(sr: float, t: int, skew: float, kurt: float, n: int, monthly: list[float]) -> float:
    """The Phase 3 computation, before req 9: V over monthly Sharpes, SR* monthly."""
    variance = float(np.var(np.asarray(monthly), ddof=1))
    return probabilistic_sharpe(sr, expected_max_sharpe(n, variance), t, skew, kurt)


@pytest.mark.parametrize(
    ("sr", "t", "skew", "kurt", "n", "monthly"),
    [
        (0.25, 120, -0.5, 4.0, 20, [0.0, 0.1, 0.2]),
        (0.18, 41, 0.3, 3.2, 7, [0.05, 0.31, -0.12, 0.2, 0.18]),
        (-0.04, 84, -1.1, 6.5, 250, [0.11, 0.09, 0.14, 0.07]),
    ],
)
def test_all_monthly_family_matches_the_phase3_dsr(
    sr: float, t: int, skew: float, kurt: float, n: int, monthly: list[float]
) -> None:
    """A family of only `month_end` trials: V over annualised Sharpes and SR* / sqrt(12)
    give every trial the DSR the Phase 3 code gave, to 1e-9."""
    m = _ref_metrics(sr=sr, skew=skew, kurt=kurt, t=t)
    annual = [v * math.sqrt(12) for v in monthly]
    d = deflated_sharpe(m, "raw", n_trials=n, pair_sharpes=annual, periods_per_year=12)
    assert d.dsr == pytest.approx(_phase3_dsr(sr, t, skew, kurt, n, monthly), abs=1e-9)


def test_deflated_sharpe_refuses_a_period_count_the_metrics_disagree_with() -> None:
    with pytest.raises(ValueError, match="periods per year"):
        deflated_sharpe(
            _ref_metrics(ppy=12), "raw", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=52
        )


def test_zero_sharpe_single_trial_gives_one_half() -> None:
    d = deflated_sharpe(
        _ref_metrics(sr=0.0), "raw", n_trials=1, pair_sharpes=[0.0], periods_per_year=12
    )
    assert d.dsr == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize(("n_trials", "pairs"), [(1, [0.3]), (5, [0.3]), (3, [])])
def test_fewer_than_two_pairs_reduces_to_psr(n_trials: int, pairs: list[float]) -> None:
    d = deflated_sharpe(
        _ref_metrics(), "raw", n_trials=n_trials, pair_sharpes=pairs, periods_per_year=12
    )
    assert d.dsr_basis == "psr"
    assert d.sr_star == 0.0
    assert d.sr_star_period == 0.0
    assert d.sharpe_variance is None
    assert d.dsr == pytest.approx(d.psr_zero, abs=1e-15)
    assert d.dsr == pytest.approx(0.994120, abs=1e-6)


def test_expected_max_sharpe_is_zero_for_one_trial() -> None:
    assert expected_max_sharpe(1, 0.01) == 0.0


def test_excess_basis_reads_its_own_inputs() -> None:
    m = _ref_metrics()
    m["sharpe_period_excess_spy"] = 0.1
    m["skew_period_excess_spy"] = 0.0
    m["kurtosis_period_excess_spy"] = 3.0
    raw = deflated_sharpe(m, "raw", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12)
    ex = deflated_sharpe(
        m, "excess_spy", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12
    )
    assert ex.basis == "excess_spy"
    denom = math.sqrt(1 - 0.0 * 0.1 + (3.0 - 1) / 4 * 0.1**2)
    expected = 0.5 * (
        1 + math.erf((0.1 - ex.sr_star_period) * math.sqrt(119) / denom / math.sqrt(2))
    )
    assert ex.dsr == pytest.approx(expected, rel=1e-12)
    assert ex.dsr != raw.dsr


def test_excess_basis_refuses_null_inputs() -> None:
    m = _ref_metrics()
    m["sharpe_period_excess_spy"] = None
    with pytest.raises(ValueError, match="excess_spy"):
        deflated_sharpe(m, "excess_spy", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12)


def test_dsr_falls_as_n_grows() -> None:
    dsrs = [
        deflated_sharpe(
            _ref_metrics(), "raw", n_trials=n, pair_sharpes=PAIRS_V_001, periods_per_year=12
        ).dsr
        for n in (3, 5, 10, 20, 50, 100, 1000)
    ]
    assert dsrs == sorted(dsrs, reverse=True)
    assert len(set(dsrs)) == len(dsrs)


def test_dsr_falls_as_kurtosis_grows() -> None:
    dsrs = [
        deflated_sharpe(
            _ref_metrics(kurt=k), "raw", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12
        ).dsr
        for k in (3.0, 4.0, 6.0, 10.0)
    ]
    assert dsrs == sorted(dsrs, reverse=True)
    assert len(set(dsrs)) == len(dsrs)


def test_dsr_falls_as_skew_grows_more_negative() -> None:
    dsrs = [
        deflated_sharpe(
            _ref_metrics(skew=s), "raw", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12
        ).dsr
        for s in (0.5, 0.0, -0.5, -1.0, -2.0)
    ]
    assert dsrs == sorted(dsrs, reverse=True)
    assert len(set(dsrs)) == len(dsrs)


@pytest.mark.parametrize(("n_trials", "pairs"), [(0, []), (1, [0.1, 0.2]), (2, [0.1, 0.2, 0.3])])
def test_deflated_sharpe_rejects_n_below_the_pair_count(n_trials: int, pairs: list[float]) -> None:
    """N counts every trial including reruns, so it can never be below the pair count."""
    with pytest.raises(ValueError):
        deflated_sharpe(
            _ref_metrics(), "raw", n_trials=n_trials, pair_sharpes=pairs, periods_per_year=12
        )


def test_probabilistic_sharpe_rejects_non_positive_variance_term() -> None:
    """1 - skew*SR + (kurtosis-1)/4*SR^2 <= 0 has no square root; refuse, not NaN."""
    with pytest.raises(ValueError):
        probabilistic_sharpe(2.0, 0.0, 120, 5.0, 1.0)


# --- req 15: red flag -------------------------------------------------------------


@pytest.mark.parametrize(
    ("excess_cagr_spy", "flag"),
    [(0.0299, False), (0.03, False), (0.0301, True), (-0.10, False), (0.2, True)],
)
def test_red_flag_either_side_of_the_threshold(excess_cagr_spy: float, flag: bool) -> None:
    """`excess_cagr_spy` is a fraction; `metrics.red_flag_excess_cagr_pp` is in
    percentage points (3.0 pp = 0.03). Strictly above flags."""
    assert red_flag({"excess_cagr_spy": excess_cagr_spy}, Settings(_env_file=None)) is flag


def test_red_flag_threshold_comes_from_config() -> None:
    s = Settings(_env_file=None, metrics={"red_flag_excess_cagr_pp": 1.0})
    assert red_flag({"excess_cagr_spy": 0.011}, s) is True
    assert red_flag({"excess_cagr_spy": 0.009}, s) is False


# --- spec acceptance: literal check ------------------------------------------------

# The spec's allowed set, plus 100 for percentage points (red flag); see the PR notes.
# 12 left with the lab (strategy-lab spec req 8): the periods-per-year counts are
# allowed in `schedule.py` only.
ALLOWED_LITERALS = {0, 1, -1, 2, 4, 10_000, 100}
PERIODS_PER_YEAR_LITERALS = {12, 52, 252}


def _stray_literals(path: Path, allowed: set[int]) -> list[tuple[int, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float | complex)
        and not isinstance(node.value, bool)
        and node.value not in allowed
    ]


def test_metrics_module_has_no_stray_numeric_literals() -> None:
    assert _stray_literals(METRICS_PY, ALLOWED_LITERALS) == []


def test_schedule_module_allows_only_the_periods_per_year_counts() -> None:
    assert _stray_literals(SCHEDULE_PY, ALLOWED_LITERALS | PERIODS_PER_YEAR_LITERALS) == []


@pytest.mark.parametrize(
    ("series", "monthly", "spy"),
    [
        ("strategy", [0.0, 0.0, 0.0, 0.0], SPY),  # all cash: Sharpe and skew undefined
        ("MTUM", [0.01, -0.02, 0.02, 0.0], [0.01, -0.02, 0.02, 0.0]),  # equals SPY
    ],
)
def test_zero_variance_series_refused_not_nan(
    series: str, monthly: list[float], spy: list[float]
) -> None:
    """A constant series has no Sharpe, skew or kurtosis; a NaN would reach DSR silently."""
    with pytest.raises(ValueError, match="zero variance"):
        series_metrics(
            series,  # type: ignore[arg-type]
            period_returns=monthly,
            gross_period_returns=monthly,
            daily_equity=DAILY_EQUITY,
            turnover=TURNOVER,
            spy_period_returns=spy,
            mtum_period_returns=MTUM,
            periods_per_year=12,
            risk_free_rate=0.0,
        )


def test_spy_series_equal_to_itself_is_fine() -> None:
    """SPY's excess over itself is constant, but those keys are null for SPY anyway."""
    m = series_metrics(
        "SPY",
        period_returns=SPY,
        gross_period_returns=SPY,
        daily_equity=DAILY_EQUITY,
        turnover=[],
        spy_period_returns=SPY,
        mtum_period_returns=MTUM,
        periods_per_year=12,
        risk_free_rate=0.0,
    )
    assert m["tracking_error_spy"] == 0.0
    assert m["sharpe_period_excess_spy"] is None


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize(
    "override",
    [
        {"period_returns": [0.02, NAN, 0.03, 0.005]},
        {"gross_period_returns": [0.021, INF, 0.031, 0.006]},
        {"spy_period_returns": [0.01, NAN, 0.02, 0.0]},
        {"mtum_period_returns": [0.015, -0.015, -INF, 0.002]},
        {"daily_equity": [100.0, NAN, 101.0]},
        {"daily_equity": [100.0, INF, 101.0]},
        {"turnover": [1.0, NAN]},
    ],
)
def test_non_finite_inputs_refused(override: dict[str, list[float]]) -> None:
    """empyrical skips NaN while n_periods still counts the month, which flatters CAGR
    (quant-auditor, PR #128); refuse instead."""
    kwargs: dict[str, object] = {
        "period_returns": NET,
        "gross_period_returns": GROSS,
        "daily_equity": DAILY_EQUITY,
        "turnover": TURNOVER,
        "spy_period_returns": SPY,
        "mtum_period_returns": MTUM,
        "periods_per_year": 12,
        "risk_free_rate": 0.0,
    }
    kwargs.update(override)
    with pytest.raises(ValueError, match="finite"):
        series_metrics("strategy", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("pairs", [[0.1, NAN, 0.2], [0.1, INF]])
def test_non_finite_pair_sharpes_refused(pairs: list[float]) -> None:
    with pytest.raises(ValueError, match="finite"):
        deflated_sharpe(_ref_metrics(), "raw", n_trials=20, pair_sharpes=pairs, periods_per_year=12)


def test_non_finite_basis_inputs_refused() -> None:
    m = _ref_metrics()
    m["skew_period"] = NAN
    with pytest.raises(ValueError, match="finite"):
        deflated_sharpe(m, "raw", n_trials=20, pair_sharpes=PAIRS_V_001, periods_per_year=12)


def test_rounding_noise_excess_series_refused_as_zero_variance() -> None:
    """A strategy equal to SPY through a different arithmetic path leaves ~1e-17 noise
    as its excess; its Sharpe would be arbitrary and could push DSR(excess) toward 1."""
    spy = [0.1 + 0.2, -0.02, 0.02, 0.01]
    net = [0.3, -0.02 + 1e-18, 0.02, 0.01]
    assert spy[0] != net[0]  # 0.1 + 0.2 != 0.3 in binary floating point
    with pytest.raises(ValueError, match="zero variance"):
        series_metrics(
            "strategy",
            period_returns=net,
            gross_period_returns=net,
            daily_equity=DAILY_EQUITY,
            turnover=TURNOVER,
            spy_period_returns=spy,
            mtum_period_returns=MTUM,
            periods_per_year=12,
            risk_free_rate=0.0,
        )


def test_red_flag_exactly_at_the_threshold_is_not_flagged() -> None:
    """0.07 * 100 == 7.000000000000001 in floating point; strictly above means 7 pp
    against a 7 pp threshold must not flag."""
    s = Settings(_env_file=None, metrics={"red_flag_excess_cagr_pp": 7.0})
    assert red_flag({"excess_cagr_spy": 0.07}, s) is False
    assert red_flag({"excess_cagr_spy": 0.0700001}, s) is True


# --- per-family comparison benchmarks (T129) -------------------------------------------

QUAL = [0.012, -0.018, 0.022, 0.001]


def _with_family_benchmark(
    benchmark: tuple[str, list[float]] | None,
) -> dict[str, float | None]:
    return series_metrics(
        "strategy",
        period_returns=NET,
        gross_period_returns=GROSS,
        daily_equity=DAILY_EQUITY,
        turnover=TURNOVER,
        spy_period_returns=SPY,
        mtum_period_returns=MTUM,
        periods_per_year=12,
        risk_free_rate=0.0,
        family_benchmark=benchmark,
    )


def test_no_family_benchmark_changes_nothing() -> None:
    """The H1 shape: keys and values byte-identical to the call without the argument."""
    base = _metrics()
    assert _with_family_benchmark(None) == base
    assert list(_with_family_benchmark(None)) == list(base)
    assert set(base) == set(METRIC_KEYS) == set(metric_keys(None))


def test_family_benchmark_adds_two_keys_beside_the_mtum_keys() -> None:
    base = _metrics()
    out = _with_family_benchmark(("QUAL", QUAL))
    assert set(out) == set(METRIC_KEYS) | {"excess_cagr_qual", "tracking_error_qual"}
    assert set(out) == set(metric_keys("QUAL"))
    assert {k: out[k] for k in base} == base
    assert out["excess_cagr_qual"] == pytest.approx(_cagr(NET) - _cagr(QUAL), rel=1e-12)
    assert out["tracking_error_qual"] == pytest.approx(
        _sample_std(_diff(NET, QUAL)) * math.sqrt(12), rel=1e-12
    )


def test_family_benchmark_must_match_the_period_count_and_be_finite() -> None:
    with pytest.raises(ValueError, match="QUAL"):
        _with_family_benchmark(("QUAL", QUAL[:3]))
    with pytest.raises(ValueError, match="finite"):
        _with_family_benchmark(("QUAL", [0.01, float("nan"), 0.0, 0.0]))

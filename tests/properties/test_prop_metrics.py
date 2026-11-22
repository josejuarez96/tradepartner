"""Property tests for `tradepartner.backtest.metrics` (#3655, plan T445): P1 to P5,
and the check that nothing in the repo shadows the `hypothesis` package.

Ours is `tradepartner.backtest.hypothesis`, reachable only through the package. `tests/`
is on `sys.path`, so a `tests/hypothesis.py` or `tests/hypothesis/` would hide the
library; a property-test file never does `from tradepartner.backtest import hypothesis`.
"""

from __future__ import annotations

import math
from pathlib import Path

import hypothesis
import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from tradepartner.backtest.metrics import (
    expected_max_sharpe,
    probabilistic_sharpe,
    series_metrics,
)

REPO = Path(__file__).resolve().parents[2]

_FINITE = {"allow_nan": False, "allow_infinity": False}
_RETURN = st.floats(-0.5, 0.5, **_FINITE)
_EQUITY = st.floats(1e-3, 1e9, **_FINITE)
_SCALE = st.floats(0.1, 10, **_FINITE)
_NON_FINITE = st.sampled_from([float("nan"), float("inf"), float("-inf")])


def test_nothing_shadows_the_hypothesis_package() -> None:
    """The imported `hypothesis` is the installed library, not a repo file, and no path
    named `hypothesis` or `hypothesis.py` exists under `tests/` or `scripts/`."""
    library = Path(hypothesis.__file__).resolve()
    assert library.name == "__init__.py" and library.parent.name == "hypothesis"
    assert hasattr(hypothesis, "given")
    # The virtualenv lives under the repo root, so "not in the repo" is "in none of its
    # source trees".
    for tree in ("tests", "scripts", "src"):
        assert not library.is_relative_to(REPO / tree), library
    shadows = [
        path
        for tree in ("tests", "scripts")
        for name in ("hypothesis", "hypothesis.py")
        for path in (REPO / tree).rglob(name)
    ]
    assert shadows == []


@st.composite
def _series_inputs(draw: st.DrawFn) -> dict[str, list[float]]:
    """Finite inputs of one length for `series_metrics`: 2 to 60 periods."""
    n = draw(st.integers(2, 60))
    returns = st.lists(_RETURN, min_size=n, max_size=n)
    return {
        "period_returns": draw(returns),
        "gross_period_returns": draw(returns),
        "spy_period_returns": draw(returns),
        "mtum_period_returns": draw(returns),
        "risk_free_period_returns": draw(
            st.lists(st.floats(0, 0.01, **_FINITE), min_size=n, max_size=n)
        ),
        "daily_equity": draw(st.lists(_EQUITY, min_size=1, max_size=120)),
        "turnover": draw(st.lists(st.floats(0, 2, **_FINITE), max_size=n)),
    }


def _metrics(inputs: dict[str, list[float]], series: str = "strategy") -> dict[str, float | None]:
    return series_metrics(series, periods_per_year=12, **inputs)


# --- P1 ---------------------------------------------------------------------------


@given(inputs=_series_inputs(), series=st.sampled_from(["strategy", "SPY", "MTUM"]))
def test_series_metrics_refuses_or_returns_only_finite_values(
    inputs: dict[str, list[float]], series: str
) -> None:
    try:
        out = _metrics(inputs, series)
    except ValueError:
        return
    not_finite = {k: v for k, v in out.items() if v is not None and not math.isfinite(v)}
    assert not_finite == {}


# --- P2 ---------------------------------------------------------------------------


# A return scaled by up to 10 can fall below -100%, where empyrical's CAGR is NaN with
# a warning; this property reads only the three scale-free moments.
@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")
@given(inputs=_series_inputs(), k=_SCALE)
def test_scaling_the_returns_leaves_sharpe_skew_and_kurtosis_unchanged(
    inputs: dict[str, list[float]], k: float
) -> None:
    n = len(inputs["period_returns"])
    base = {**inputs, "risk_free_period_returns": [0.0] * n}
    scaled = {
        **base,
        **{
            key: [value * k for value in base[key]]
            for key in ("period_returns", "spy_period_returns", "mtum_period_returns")
        },
    }
    try:
        before, after = _metrics(base), _metrics(scaled)
    except ValueError:
        # A series (or its excess over SPY) that is constant up to rounding noise at
        # one scale or the other is refused; the property is about the accepted ones.
        assume(False)
    for key in ("sharpe_period", "skew_period", "kurtosis_period"):
        assert after[key] == pytest.approx(before[key], rel=1e-6), key


# --- P3 ---------------------------------------------------------------------------


@given(inputs=_series_inputs(), k=_SCALE)
def test_max_drawdown_is_in_range_and_ignores_the_scale_of_equity(
    inputs: dict[str, list[float]], k: float
) -> None:
    scaled = {**inputs, "daily_equity": [value * k for value in inputs["daily_equity"]]}
    try:
        before, after = _metrics(inputs), _metrics(scaled)
    except ValueError:
        assume(False)
    drawdown = before["max_drawdown"]
    assert drawdown is not None
    assert -1 <= drawdown <= 0
    assert after["max_drawdown"] == pytest.approx(drawdown, rel=1e-9, abs=1e-12)


# --- P4 ---------------------------------------------------------------------------

_SHARPE = st.floats(-3, 3, **_FINITE)
_PERIODS = st.integers(2, 600)
_SKEW = st.floats(-5, 5, **_FINITE)
_KURTOSIS = st.floats(1, 50, **_FINITE)


@given(sharpe=_SHARPE, low=_SHARPE, high=_SHARPE, n=_PERIODS, skew=_SKEW, kurtosis=_KURTOSIS)
def test_probabilistic_sharpe_is_a_probability_that_falls_as_the_benchmark_rises(
    sharpe: float, low: float, high: float, n: int, skew: float, kurtosis: float
) -> None:
    low, high = sorted((low, high))
    try:
        at_low = probabilistic_sharpe(sharpe, low, n, skew, kurtosis)
        at_high = probabilistic_sharpe(sharpe, high, n, skew, kurtosis)
        at_itself = probabilistic_sharpe(sharpe, sharpe, n, skew, kurtosis)
    except ValueError:
        return
    assert 0 <= at_high <= at_low <= 1
    assert at_itself == 0.5


# --- P5 ---------------------------------------------------------------------------

_TRIALS = st.integers(1, 10**6)
_VARIANCE = st.floats(0, 1e6, **_FINITE)


@given(n_low=_TRIALS, n_high=_TRIALS, v_low=_VARIANCE, v_high=_VARIANCE)
def test_expected_max_sharpe_is_non_negative_and_grows_with_trials_and_variance(
    n_low: int, n_high: int, v_low: float, v_high: float
) -> None:
    n_low, n_high = sorted((n_low, n_high))
    v_low, v_high = sorted((v_low, v_high))
    base = expected_max_sharpe(n_low, v_low)
    assert math.isfinite(base)
    assert base >= 0
    assert expected_max_sharpe(n_high, v_low) >= base
    assert expected_max_sharpe(n_low, v_high) >= base


@given(n=_TRIALS, variance=_NON_FINITE)
@example(n=10, variance=float("nan"))
@example(n=10, variance=float("inf"))
def test_expected_max_sharpe_refuses_a_non_finite_variance(n: int, variance: float) -> None:
    with pytest.raises(ValueError, match="variance"):
        expected_max_sharpe(n, variance)

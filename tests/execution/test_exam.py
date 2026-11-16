"""The forward return exam's verdict (ADR 0016 amendments 2026-11-06, #3125, and
2026-11-07, #3143; plan T381)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tradepartner.config import Settings
from tradepartner.execution.exam import (
    ExamFrozen,
    ExamVerdict,
    exam_verdict,
    insample_skew,
    intercept_t,
    mean_t,
)

FROZEN = ExamFrozen(looks_months=(12, 24, 36), bounds=(4.762, 3.367, 2.750), futility_months=6)
ALL_MET = {12: True, 24: True, 36: True}


def _verdict(
    y: list[float],
    x: list[float] | None = None,
    *,
    ended: bool = False,
    met: dict[int, bool] | None = None,
    frozen: ExamFrozen = FROZEN,
) -> ExamVerdict:
    return exam_verdict(
        y,
        x if x is not None else _bench(len(y)),
        frozen,
        window_ended=ended,
        minimums_met_by=ALL_MET if met is None else met,
    )


def _bench(n: int) -> list[float]:
    """A mean-zero regressor orthogonal to `_noise` on every multiple of 4 months."""
    return [0.02 * v for v in ([1.0, 1.0, -1.0, -1.0] * (n // 4 + 1))[:n]]


def _noise(n: int) -> list[float]:
    """Alternating +-1, scaled so months 1 to n have sample sd 1 and mean 0 (n even)."""
    scale = math.sqrt((n - 1) / n)
    return [scale * (1.0 if i % 2 == 0 else -1.0) for i in range(n)]


def _with_mean_t(n: int, target: float) -> list[float]:
    """A series of n months (n a multiple of 4) whose leg (i) t is `target`."""
    c = target / math.sqrt(n)
    return [0.01 * (d + c) for d in _noise(n)]


def _ref_mean_t(y: np.ndarray) -> float:
    return float(y.mean() / (y.std(ddof=1) / math.sqrt(len(y))))


def _ref_intercept_t(y: np.ndarray, x: np.ndarray) -> float:
    design = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    s2 = float(resid @ resid) / (len(y) - 2)
    cov = s2 * np.linalg.inv(design.T @ design)
    return float(beta[0] / math.sqrt(cov[0, 0]))


# --- the legs against a hand-computed reference -----------------------------------


def test_legs_match_a_hand_computed_reference_on_a_tiny_series() -> None:
    # y = [1, 2, 3]: mean 2, sd 1, so t = 2 / (1 / sqrt 3) = 2 sqrt 3.
    assert mean_t([1.0, 2.0, 3.0]) == pytest.approx(2.0 * math.sqrt(3.0), rel=1e-15)
    # y = [1, 3, 2, 4] on x = [0, 1, 0, 1]: slope 2, intercept 1.5, residuals
    # (-0.5, -0.5, 0.5, 0.5), s^2 = 1 / 2, se(a) = sqrt(0.5 x (1/4 + 0.25 / 1)) = 0.5.
    assert intercept_t([1.0, 3.0, 2.0, 4.0], [0.0, 1.0, 0.0, 1.0]) == pytest.approx(3.0, rel=1e-14)


@pytest.mark.parametrize("look", [12, 24, 36])
def test_both_legs_match_the_reference_at_each_look(look: int) -> None:
    rng = np.random.default_rng(381 + look)
    y = rng.normal(0.01, 0.02, 36)
    x = rng.normal(0.005, 0.04, 36)
    verdict = _verdict(list(y), list(x), met={12: False, 24: False, 36: True})
    reading = {r.month: r for r in verdict.looks}[look]
    assert reading.t_mean == pytest.approx(_ref_mean_t(y[:look]), rel=1e-12)
    assert reading.t_intercept == pytest.approx(_ref_intercept_t(y[:look], x[:look]), rel=1e-12)
    assert reading.excess_sum == pytest.approx(float(y[:look].sum()), rel=1e-12)


def test_undefined_legs_are_nan_and_reach_no_bound() -> None:
    assert math.isnan(mean_t([0.01] * 12))
    assert math.isnan(mean_t([0.01]))
    assert math.isnan(intercept_t([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]))
    assert math.isnan(intercept_t([0.01, 0.03, 0.05], [0.0, 1.0, 2.0]))  # exact fit
    # A constant positive series has an infinite t in the limit; it does not pass.
    assert _verdict([0.05] * 12).state == "running"


# --- the bound at each look, on each leg --------------------------------------------


def _binding(look: int, target: float, leg: str) -> tuple[list[float], list[float]]:
    """A `look`-month series and regressor whose `leg` t is `target` and whose other
    leg is clearly above it.

    y = 0.01 (d + c + beta z) with d (`_noise`) and z (the regressor's +-1 pattern,
    scaled to sd 1) orthogonal and mean zero, so the intercept is 0.01 c, the slope
    0.5 beta and the residuals 0.01 d. Leg (i) is c sqrt(n) / sqrt(1 + beta^2) and leg
    (ii) is c sqrt(n) sqrt((n - 2) / (n - 1)): beta = 0 makes leg (ii) the lower leg,
    beta = 1 makes leg (i) the lower one (sqrt 2 x sqrt(10 / 11) > 1).
    """
    x = _bench(look)
    z = [v / 0.02 * math.sqrt((look - 1) / look) for v in x]
    if leg == "ii":
        beta, c = 0.0, target / (math.sqrt(look) * math.sqrt((look - 2) / (look - 1)))
    else:
        beta, c = 1.0, target * math.sqrt(2.0) / math.sqrt(look)
    y = [0.01 * (d + c + beta * zv) for d, zv in zip(_noise(look), z, strict=True)]
    return y, x


@pytest.mark.parametrize(("look", "bound"), [(12, 4.762), (24, 3.367), (36, 2.750)])
@pytest.mark.parametrize("leg", ["i", "ii"])
def test_each_leg_just_below_and_at_the_pinned_bound(look: int, bound: float, leg: str) -> None:
    """With the other leg clear, a hair below the look's pinned bound on one leg does
    not pass and a hair above it does; earlier looks cannot pass (minimums unmet there)
    and futility never fires (every prefix sum is positive)."""
    met = {m: m == look for m in (12, 24, 36)}
    for factor, expect in ((1.0 - 1e-9, False), (1.0 + 1e-9, True)):
        y, x = _binding(look, bound * factor, leg)
        t_i, t_ii = mean_t(y), intercept_t(y, x)
        lower, upper = (t_i, t_ii) if leg == "i" else (t_ii, t_i)
        assert lower == pytest.approx(bound * factor, rel=1e-12)
        assert upper > bound * 1.01
        v = _verdict(y, x, met=met)
        assert v.looks[-1].month == look
        assert (v.state == "passed") is expect
        if expect:
            assert v.month == look


@pytest.mark.parametrize("look", [12, 24, 36])
@pytest.mark.parametrize("leg", ["i", "ii"])
def test_each_leg_exactly_at_its_bound_passes_and_one_ulp_above_does_not(
    look: int, leg: str
) -> None:
    """The rule is `t >= bound` on each leg: a bound equal to the lower leg's exact t
    passes, the next float above it does not."""
    y, x = _binding(look, 4.0, leg)
    binding = mean_t(y) if leg == "i" else intercept_t(y, x)
    met = {m: m == look for m in (12, 24, 36)}
    for edge, expect in ((binding, True), (math.nextafter(binding, math.inf), False)):
        bounds = tuple(edge if m == look else 1e9 for m in (12, 24, 36))
        frozen = ExamFrozen(looks_months=(12, 24, 36), bounds=bounds, futility_months=6)
        assert (_verdict(y, x, met=met, frozen=frozen).state == "passed") is expect


def test_leg_ii_fails_a_seeded_pure_beta_book_that_leg_i_passes() -> None:
    """1.5 x the benchmark's excess plus seeded noise, zero alpha, in a rising sample."""
    rng = np.random.default_rng(3143)
    x = rng.normal(0.03, 0.01, 12)
    y = 1.5 * x + rng.normal(0.0, 0.005, 12)
    verdict = _verdict(list(y), list(x))
    reading = verdict.looks[0]
    assert reading.month == 12
    assert reading.t_mean >= 4.762
    assert reading.t_intercept < 4.762
    assert verdict.state == "running"
    assert verdict.next_look == 24


# --- futility, failed, void ----------------------------------------------------------


def test_month_six_futility_fires_on_a_negative_sum_and_not_on_zero() -> None:
    negative = [0.01, -0.01, 0.02, -0.02, 0.03, -0.0300001]
    v = _verdict(negative)
    assert (v.state, v.month, v.months_read) == ("failed_futility", 6, 6)
    zero = [0.01, -0.01, 0.02, -0.02, 0.03, -0.03]
    assert math.fsum(zero) == 0.0
    v = _verdict(zero)
    assert (v.state, v.next_look) == ("running", 12)
    # Before month 6 nothing is decided, whatever the sum.
    assert _verdict([-0.05] * 5).state == "running"


def test_a_last_look_either_leg_misses_is_failed() -> None:
    y = _with_mean_t(36, 2.0)  # both legs below 2.750, every prefix sum positive
    v = _verdict(y)
    assert (v.state, v.month, v.next_look) == ("failed", 36, None)
    assert [r.month for r in v.looks] == [12, 24, 36]


def test_unmet_minimums_at_the_last_look_are_void_whatever_the_legs_show() -> None:
    """T381a folds `paper.min_rebalances` and `paper.min_exam_fills` into one flag per
    look (either unmet makes it false, tested at its edge in `book_stats`); the verdict
    is void whatever the legs show."""
    met = {12: False, 24: False, 36: False}
    passing = _with_mean_t(36, 10.0)
    failing = _with_mean_t(36, 2.0)
    for y in (passing, failing):
        v = _verdict(y, met=met)
        assert (v.state, v.month) == ("failed_void", 36)


def test_a_failed_twelve_month_look_is_futility_on_a_negative_sum_else_running() -> None:
    six = [0.01] * 6
    negative = six + [-0.02] * 6  # sum of 12 is -0.06, month-6 sum positive
    v = _verdict(negative)
    assert (v.state, v.month) == ("failed_futility", 12)
    zero = six + [-0.01] * 6
    assert math.fsum(zero) == 0.0
    v = _verdict(zero)
    assert (v.state, v.next_look) == ("running", 24)


def test_a_failed_twenty_four_month_look_is_futility_on_a_negative_sum_else_running() -> None:
    first = [0.01, 0.0] * 6  # fails at 12 (low t), sum +0.06
    negative = first + [-0.01] * 12
    v = _verdict(negative)
    assert (v.state, v.month) == ("failed_futility", 24)
    zero = first + [-0.01, 0.0] * 6  # exact: sum of 24 is 0
    assert math.fsum(zero) == 0.0
    v = _verdict(zero)
    assert (v.state, v.next_look) == ("running", 36)


def test_a_pass_at_twelve_stops_the_exam() -> None:
    y = _with_mean_t(12, 10.0)
    v = _verdict(y)
    assert (v.state, v.month, v.months_read) == ("passed", 12, 12)
    later = y + [-0.5] * 24
    assert _verdict(later) == v
    assert _verdict(later, ended=True) == v


def test_fewer_than_thirty_six_months_undecided_is_running_then_void_once_ended() -> None:
    y = _with_mean_t(24, 2.0)[:20]
    v = _verdict(y)
    assert (v.state, v.next_look, v.months_read) == ("running", 24, 20)
    v = _verdict(y, ended=True)
    assert (v.state, v.month, v.next_look) == ("failed_void", None, None)
    assert _verdict([0.01] * 3, ended=True).state == "failed_void"


def test_a_look_whose_minimums_are_unmet_does_not_pass() -> None:
    y = _with_mean_t(24, 10.0)
    v = _verdict(y, met={12: False, 24: True, 36: True})
    assert (v.state, v.month) == ("passed", 24)
    assert v.looks[0].passed is False
    # A look missing from the map counts as unmet.
    v = _verdict(y[:12], met={})
    assert (v.state, v.next_look) == ("running", 24)


def test_futility_reads_a_look_whose_minimums_are_unmet() -> None:
    negative = [0.01] * 6 + [-0.02] * 6
    v = _verdict(negative, met={})
    assert (v.state, v.month) == ("failed_futility", 12)


# --- no look-ahead -------------------------------------------------------------------


def test_the_verdict_at_month_k_is_unchanged_by_later_months() -> None:
    rng = np.random.default_rng(3143)
    for _ in range(200):
        y = list(rng.normal(0.004, 0.01, 40))
        x = list(rng.normal(0.0, 0.04, 40))
        full = _verdict(y, x)
        for k in range(1, 41):
            at_k = _verdict(y[:k], x[:k])
            tail_y = list(rng.normal(0.0, 0.05, 40 - k))
            tail_x = list(rng.normal(0.0, 0.05, 40 - k))
            altered = _verdict(y[:k] + tail_y, x[:k] + tail_x)
            if at_k.state != "running":
                assert altered == at_k
                assert full == at_k
            else:
                assert full.months_read > k or full.state == "running"
        decided = full.months_read
        assert _verdict(y[:decided], x[:decided]) == full or full.state == "running"


# --- seeded nulls (seed 3143, 20,000 36-month series each) --------------------------

N_NULL = 20_000
MONTHLY_TE = 0.08 / math.sqrt(12.0)


def _null_rate(excess: np.ndarray, bench: np.ndarray) -> float:
    passes = 0
    for y, x in zip(excess, bench, strict=True):
        v = exam_verdict(list(y), list(x), FROZEN, window_ended=True, minimums_met_by=ALL_MET)
        passes += v.state == "passed"
    return passes / len(excess)


def _student_t5(rng: np.random.Generator, shape: tuple[int, int]) -> np.ndarray:
    return rng.standard_t(5, size=shape) / math.sqrt(5.0 / 3.0)


def _skew_normal(rng: np.random.Generator, skew: float, shape: tuple[int, int]) -> np.ndarray:
    """delta |Z0| + sqrt(1 - delta^2) Z1 at the delta whose skewness is `skew`,
    standardised to mean 0 and sd 1."""
    target = abs(skew)

    def skew_of(delta: float) -> float:
        mu = delta * math.sqrt(2.0 / math.pi)
        return (4.0 - math.pi) / 2.0 * mu**3 / (1.0 - mu * mu) ** 1.5

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if skew_of(mid) < target else (lo, mid)
    delta = math.copysign((lo + hi) / 2.0, skew)
    z0 = np.abs(rng.standard_normal(shape))
    z1 = rng.standard_normal(shape)
    raw = delta * z0 + math.sqrt(1.0 - delta * delta) * z1
    mean = delta * math.sqrt(2.0 / math.pi)
    sd = math.sqrt(1.0 - mean * mean)
    return (raw - mean) / sd


def test_null_student_t_df5_passes_at_most_0_6_percent() -> None:
    rng = np.random.default_rng(3143)
    excess = MONTHLY_TE * _student_t5(rng, (N_NULL, 36))
    bench = 0.045 * _student_t5(rng, (N_NULL, 36))
    assert _null_rate(excess, bench) <= 0.006


def test_null_skew_normal_at_minus_0_85_passes_at_most_1_5_percent() -> None:
    rng = np.random.default_rng(3143)
    draws = _skew_normal(rng, -0.85, (N_NULL, 36))
    flat = draws.ravel()
    sample = float(((flat - flat.mean()) ** 3).mean() / flat.std() ** 3)
    assert sample == pytest.approx(-0.85, abs=0.03)
    excess = MONTHLY_TE * draws
    bench = 0.045 * _student_t5(rng, (N_NULL, 36))
    assert _null_rate(excess, bench) <= 0.015


# --- the frozen keys and the skew -----------------------------------------------------


def test_frozen_from_config_is_the_pinned_exam() -> None:
    assert ExamFrozen.from_config(Settings(_env_file=None).paper) == FROZEN


@pytest.mark.parametrize(
    "kwargs",
    [
        {"looks_months": (), "bounds": (), "futility_months": 6},
        {"looks_months": (12, 24), "bounds": (4.762,), "futility_months": 6},
        {"looks_months": (24, 12), "bounds": (3.0, 4.0), "futility_months": 6},
        {"looks_months": (12,), "bounds": (math.nan,), "futility_months": 6},
        {"looks_months": (12,), "bounds": (4.0,), "futility_months": 12},
        {"looks_months": (12,), "bounds": (4.0,), "futility_months": 0},
        {"looks_months": (2,), "bounds": (4.0,), "futility_months": 1},
    ],
)
def test_frozen_refuses_a_shape_the_verdict_cannot_read(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ExamFrozen(**kwargs)  # type: ignore[arg-type]


def test_verdict_refuses_misaligned_or_non_finite_series() -> None:
    with pytest.raises(ValueError, match="one value per month"):
        exam_verdict([0.01] * 6, [0.0] * 5, FROZEN, window_ended=False, minimums_met_by=ALL_MET)
    with pytest.raises(ValueError, match="finite"):
        exam_verdict(
            [0.01] * 5 + [math.nan], [0.0] * 6, FROZEN, window_ended=False, minimums_met_by={}
        )


def test_insample_skew_is_the_moment_estimator() -> None:
    # [0, 0, 3]: mean 1, m2 = (1 + 1 + 4) / 3 = 2, m3 = (-1 - 1 + 8) / 3 = 2,
    # so the skewness is 2 / 2^1.5 = 1 / sqrt 2.
    assert insample_skew([0.0, 0.0, 3.0]) == pytest.approx(1.0 / math.sqrt(2.0), rel=1e-15)
    assert insample_skew([0.0, 0.0, -3.0]) == pytest.approx(-1.0 / math.sqrt(2.0), rel=1e-15)
    rng = np.random.default_rng(3143)
    x = rng.normal(0.0, 1.0, 50) ** 3
    d = x - x.mean()
    assert insample_skew(list(x)) == pytest.approx(
        float((d**3).mean() / (d**2).mean() ** 1.5), rel=1e-12
    )
    with pytest.raises(ValueError):
        insample_skew([0.1, 0.2])
    with pytest.raises(ValueError):
        insample_skew([0.1, 0.1, 0.1])
    with pytest.raises(ValueError):
        insample_skew([0.1, math.inf, 0.2])

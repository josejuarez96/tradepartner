"""Null calibration of the overlap standard error (#3679).

Every design simulates a no-effect null: the true mean spread is zero, so a
correctly sized t exceeds 1.645 about 5% of the time and 1.0 about 16%. The
entries are bunched into earnings seasons (30 of every 63 sessions), the shape
that made the flat Hansen-Hodrick kernel noisy and low.

Bands come from the binomial noise of the simulation, not from tuning. With
``R`` replications the standard error of a rejection rate ``p`` is
``sqrt(p (1 - p) / R)``; each band below is the nominal rate (or the size the
Bartlett kernel was measured at over 2,000 replications, quoted in the PR) plus
or minus four such standard errors. The measured limits of the kernel, in
particular the overlap-with-a-common-shock design, are in the PR table and are
not asserted as calibrated here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tradepartner.research.event_study.stats import (
    overlap_cluster_stats,
    overlap_result_interval,
)

SESSIONS = 1200
HOLD = 60
LAG = 69  # 60 - 1 + 2 * 5, E1's frozen lag
SEASON = np.array([s for s in range(SESSIONS - HOLD) if s % 63 < 30])


def _bunched(rng: np.random.Generator, n: int) -> tuple[list[int], list[float]]:
    entries = np.sort(rng.choice(SEASON, size=n))
    return [int(e) for e in entries], [float(v) for v in rng.normal(0.0, 0.12, n)]


def _t(values: list[float], entries: list[int], kernel: str) -> float:
    return overlap_cluster_stats(values, entries, max_lag_sessions=LAG, kernel=kernel)["t"]


def _band(p: float, reps: int) -> tuple[float, float]:
    half = 4 * math.sqrt(p * (1 - p) / reps)
    return max(p - half, 0.0), p + half


def test_default_kernel_is_the_flat_one_byte_for_byte() -> None:
    entries, values = _bunched(np.random.default_rng(1), 400)
    default = overlap_cluster_stats(values, entries, max_lag_sessions=LAG)
    flat = overlap_cluster_stats(values, entries, max_lag_sessions=LAG, kernel="hansen_hodrick")
    assert default == flat
    a = overlap_result_interval(values, entries, max_lag_sessions=LAG)
    b = overlap_result_interval(values, entries, max_lag_sessions=LAG, kernel="hansen_hodrick")
    assert a == b


def test_unknown_kernel_refused() -> None:
    with pytest.raises(ValueError, match="kernel"):
        overlap_cluster_stats([1.0, 2.0, 3.0], [1, 2, 3], max_lag_sessions=5, kernel="parzen")


def test_bartlett_at_zero_lag_is_the_cohort_cluster_se() -> None:
    entries, values = _bunched(np.random.default_rng(2), 300)
    bart = overlap_cluster_stats(values, entries, max_lag_sessions=0, kernel="bartlett")
    flat = overlap_cluster_stats(values, entries, max_lag_sessions=0)
    assert bart["se"] == pytest.approx(flat["se"])


def test_bartlett_variance_never_negative_where_flat_kernel_goes_nan() -> None:
    """Sparse bunched null (60 events): the flat kernel's variance goes negative sometimes."""
    rng = np.random.default_rng(5)
    flat_nan = bart_nan = 0
    for _ in range(3000):
        entries, values = _bunched(rng, 60)
        flat_nan += math.isnan(_t(values, entries, "hansen_hodrick"))
        bart_nan += math.isnan(_t(values, entries, "bartlett"))
    assert flat_nan > 0  # the defect, pinned: old studies keep it
    assert bart_nan == 0


def test_bartlett_bunched_null_rejection_rates_and_spread() -> None:
    """3,000 iid spreads, 300 seeded replications; measured size 0.067 / 0.172 over 2,000."""
    reps = 300
    rng = np.random.default_rng(11)
    flat, bart = [], []
    for _ in range(reps):
        entries, values = _bunched(rng, 3000)
        flat.append(_t(values, entries, "hansen_hodrick"))
        bart.append(_t(values, entries, "bartlett"))
    b = np.array(bart)
    assert not np.isnan(b).any()
    low, high = _band(0.067, reps)
    assert low <= float((b > 1.645).mean()) <= high
    low, high = _band(0.172, reps)
    assert low <= float((b > 1.0).mean()) <= high
    f = np.array([t for t in flat if not math.isnan(t)])
    # t should have unit spread: the flat kernel overshoots, Bartlett stays near it
    assert float(b.std()) < 1.25
    assert float(f.std()) > float(b.std()) + 0.15

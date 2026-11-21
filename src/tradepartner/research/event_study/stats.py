"""Pure inference and overlap measures for event studies."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence
from datetime import date, timedelta
from statistics import NormalDist
from typing import Final

#: `[study].se_method` values (#3679). ``hansen_hodrick`` is the flat-kernel
#: overlap SE every study registered before the amendment ran under, and what an
#: absent key reads as. ``quarter_cluster_t`` is the calendar-quarter-of-entry
#: cluster SE on the pooled mean with the CR1 factor, judged against t(G - 1).
SE_METHODS: Final = ("hansen_hodrick", "quarter_cluster_t")
DEFAULT_SE_METHOD: Final = "hansen_hodrick"
#: A frozen term of ``quarter_cluster_t``: fewer entry quarters than this and
#: the statistic is refused. G - 1 degrees of freedom below 7 leave a t
#: reference too heavy-tailed to mean anything; changing it is a new method name.
QUARTER_CLUSTER_MIN_QUARTERS: Final = 8


_Z_EQ_CAP: Final = 40.0


class TooFewClustersError(ValueError):
    """The study has fewer entry quarters than ``quarter_cluster_t`` can judge."""


def cluster_stats(values: Sequence[float], clusters: Sequence[Hashable]) -> dict[str, float]:
    """Return the probe's uncorrected one-way cluster mean, SE, and t."""
    if len(values) != len(clusters):
        raise ValueError("values and clusters must have equal length")
    n = len(values)
    c = len(set(clusters))
    if n < 2:
        return {"n": n, "mean": math.nan, "se": math.nan, "t": math.nan, "clusters": c}
    if any(not math.isfinite(v) for v in values):
        return {"n": n, "mean": math.nan, "se": math.nan, "t": math.nan, "clusters": c}
    mean = sum(values) / n
    sums: dict[Hashable, float] = defaultdict(float)
    for value, cluster in zip(values, clusters, strict=True):
        sums[cluster] += value - mean
    se = math.sqrt(sum(total * total for total in sums.values())) / n
    return {
        "n": n,
        "mean": mean,
        "se": se,
        "t": mean / se if se > 0 else math.nan,
        "clusters": c,
    }


def kish_clusters(clusters: Sequence[Hashable]) -> float:
    """Effective number of clusters under unequal event counts."""
    if not clusters:
        return math.nan
    sizes = Counter(clusters).values()
    return len(clusters) ** 2 / sum(size * size for size in sizes)


def overlap_cluster_stats(
    values: Sequence[float], ordered_clusters: Sequence[int], *, max_lag_sessions: int
) -> dict[str, float]:
    """Cluster by entry cohort and include covariance from overlapping holds.

    ``max_lag_sessions`` is the largest entry-session distance at which two
    holds can overlap. The flat lag kernel is the Hansen-Hodrick
    estimator for a known holding horizon, with no small-sample correction.
    """
    if len(values) != len(ordered_clusters) or max_lag_sessions < 0:
        raise ValueError("invalid cohort series or overlap lag")
    base = cluster_stats(values, ordered_clusters)
    if len(values) < 2:
        return base
    mean = base["mean"]
    sums: dict[int, float] = defaultdict(float)
    for value, cohort in zip(values, ordered_clusters, strict=True):
        sums[cohort] += value - mean
    ordered = sorted(sums)
    variance = sum(total * total for total in sums.values())
    n_ordered = len(ordered)
    for i in range(n_ordered):
        a = ordered[i]
        for j in range(i + 1, n_ordered):
            b = ordered[j]
            if b - a > max_lag_sessions:
                break
            variance += 2 * sums[a] * sums[b]
    se = math.sqrt(variance) / len(values) if variance > 0 else math.nan
    return {**base, "se": se, "t": mean / se if se > 0 else math.nan}


def result_interval(
    values: Sequence[float], clusters: Sequence[Hashable], *, z: float = 1.96
) -> dict[str, float]:
    """Report effect, two-sided interval, and 80% power minimum detectable effect."""
    if not math.isfinite(z) or z <= 0:
        raise ValueError("z must be finite and positive")
    stats = cluster_stats(values, clusters)
    mean, se = stats["mean"], stats["se"]
    return {
        "effect": mean,
        "se": se,
        "t": stats["t"],
        "ci_low": mean - z * se,
        "ci_high": mean + z * se,
        "mde_80": (z + 0.8416212335729143) * se,
        "n": stats["n"],
        "clusters": stats["clusters"],
    }


def overlap_result_interval(
    values: Sequence[float],
    ordered_clusters: Sequence[int],
    *,
    max_lag_sessions: int,
    z: float = 1.96,
) -> dict[str, float]:
    """Report the same verdict fields with known overlapping-cohort covariance."""
    if not math.isfinite(z) or z <= 0:
        raise ValueError("z must be finite and positive")
    stats = overlap_cluster_stats(values, ordered_clusters, max_lag_sessions=max_lag_sessions)
    mean, se = stats["mean"], stats["se"]
    return {
        "effect": mean,
        "se": se,
        "t": stats["t"],
        "ci_low": mean - z * se,
        "ci_high": mean + z * se,
        "mde_80": (z + 0.8416212335729143) * se,
        "n": stats["n"],
        "clusters": stats["clusters"],
    }


def hold_overlap(holds: Sequence[tuple[str, date, date]]) -> dict[str, float | int]:
    """Count open holds on each date and same-security overlapping entries."""
    if not holds:
        return {"mean_open": math.nan, "max_open": 0, "same_security": 0}
    changes: dict[date, int] = defaultdict(int)
    last_end: dict[str, date] = {}
    same_security = 0
    for security, start, end in sorted(holds, key=lambda hold: (hold[1], hold[0], hold[2])):
        if end < start:
            raise ValueError("hold exit precedes entry")
        if security in last_end and start <= last_end[security]:
            same_security += 1
        last_end[security] = max(end, last_end.get(security, end))
        changes[start] += 1
        changes[end + timedelta(days=1)] -= 1
    active = total = days = max_open = 0
    previous: date | None = None
    for day, change in sorted(changes.items()):
        if previous is not None:
            span = (day - previous).days
            total += active * span
            days += span
        active += change
        max_open = max(max_open, active)
        previous = day
    return {"mean_open": total / days, "max_open": max_open, "same_security": same_security}


def hold_overlap_sessions(holds: Sequence[tuple[str, int, int]]) -> dict[str, float | int]:
    """Count overlapping positions on actual session indices, excluding market closures."""
    if not holds:
        return {"mean_open": math.nan, "max_open": 0, "same_security": 0}
    changes: dict[int, int] = defaultdict(int)
    last_end: dict[str, int] = {}
    same_security = 0
    for security, start, end in sorted(holds, key=lambda hold: (hold[1], hold[0], hold[2])):
        if end < start:
            raise ValueError("hold exit precedes entry")
        if security in last_end and start <= last_end[security]:
            same_security += 1
        last_end[security] = max(end, last_end.get(security, end))
        changes[start] += 1
        changes[end + 1] -= 1
    active = total = sessions = max_open = 0
    previous: int | None = None
    for session, change in sorted(changes.items()):
        if previous is not None:
            span = session - previous
            total += active * span
            sessions += span
        active += change
        max_open = max(max_open, active)
        previous = session
    return {"mean_open": total / sessions, "max_open": max_open, "same_security": same_security}


def net_long(spread: float, cost_bps: float) -> float:
    """Long spread after two traded sides of cost; matched leg is costless."""
    return spread - 2 * cost_bps / 10_000


def net_short(spread: float, cost_bps: float, borrow_bps: float, horizon: int) -> float:
    """Short spread after cost and prorated borrow."""
    return -spread - 2 * cost_bps / 10_000 - borrow_bps / 10_000 * horizon / 252


def entry_quarter(entry: date) -> int:
    """Calendar-quarter index of an entry date: ``year * 4 + (month - 1) // 3``."""
    return entry.year * 4 + (entry.month - 1) // 3


def _betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta ``I_x(a, b)`` by the continued fraction (Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / (c if abs(c) > tiny else tiny)
            h *= d * c
        if abs(d * c - 1.0) < 1e-14:
            break
    return front * h / a


def student_t_cdf(t: float, df: int) -> float:
    """Student t distribution function with ``df`` degrees of freedom."""
    if df < 1:
        raise ValueError("df must be at least 1")
    if math.isnan(t):
        return math.nan
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def student_t_quantile(p: float, df: int) -> float:
    """Inverse of `student_t_cdf` by bisection, for ``0 < p < 1``."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    low, high = -1e3, 1e3
    for _ in range(200):
        mid = (low + high) / 2.0
        if student_t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def quarter_cluster_stats(
    values: Sequence[float], quarters: Sequence[int], *, min_quarters: int
) -> dict[str, float]:
    """Pooled mean with a calendar-quarter cluster SE, CR1 factor and t(G - 1) reference.

    ``quarters`` is the calendar quarter of each event's entry date
    (`entry_quarter`). The SE is the one-way cluster SE of the pooled mean times
    ``sqrt(G / (G - 1))``; ``t`` is the raw ratio; ``z_eq = Phi^-1(F_{t,G-1}(t))``
    is that t mapped to the normal scale, so a kill-line floor written as a
    normal-scale t (1.0, 1.645) keeps its meaning (the same one-sided tail).
    Raises `TooFewClustersError` when 0 < G < ``min_quarters``; never returns it as NaN.
    """
    if len(values) != len(quarters):
        raise ValueError("values and quarters must have equal length")
    n = len(values)
    groups = len(set(quarters))
    if n == 0:  # nothing to judge: undefined, which a kill line counts as a kill (as today)
        return {
            "n": 0,
            "mean": math.nan,
            "se": math.nan,
            "t": math.nan,
            "z_eq": math.nan,
            "clusters": 0,
        }
    if groups < min_quarters or groups < 2:
        raise TooFewClustersError(
            f"quarter_cluster_t needs at least {max(min_quarters, 2)} entry quarters, got {groups}"
        )
    nan = math.nan
    if any(not math.isfinite(v) for v in values):
        return {"n": n, "mean": nan, "se": nan, "t": nan, "z_eq": nan, "clusters": groups}
    mean = sum(values) / n
    sums: dict[int, float] = defaultdict(float)
    for value, quarter in zip(values, quarters, strict=True):
        sums[quarter] += value - mean
    variance = sum(total * total for total in sums.values())
    se = math.sqrt(variance * groups / (groups - 1)) / n
    if not se > 0:
        return {"n": n, "mean": mean, "se": nan, "t": nan, "z_eq": nan, "clusters": groups}
    t = mean / se
    tail = student_t_cdf(-abs(t), groups - 1)
    # Finite even when the tail underflows, so an overwhelming pass is not a "NaN or inf" kill.
    z_eq = math.copysign(-NormalDist().inv_cdf(tail) if tail > 0.0 else _Z_EQ_CAP, t)
    return {"n": n, "mean": mean, "se": se, "t": t, "z_eq": z_eq, "clusters": groups}


def se_result_interval(
    values: Sequence[float],
    entry_indices: Sequence[int],
    quarters: Sequence[int] | None,
    *,
    method: str,
    max_lag_sessions: int,
    z: float,
) -> dict[str, float]:
    """The verdict fields under ``method``; ``hansen_hodrick`` is `overlap_result_interval` exactly.

    Under ``quarter_cluster_t`` the interval uses the t(G - 1) quantile at the
    normal tail ``Phi(z)``, ``t`` is the kill-line statistic on the normal scale
    (``z_eq``), and ``t_raw``, ``z_eq``, ``quarters`` and ``df`` are added.
    ``quarters`` is required for that method only.
    """
    if method not in SE_METHODS:
        raise ValueError(f"unknown se_method {method!r}")
    if method == "hansen_hodrick":
        return overlap_result_interval(
            values, entry_indices, max_lag_sessions=max_lag_sessions, z=z
        )
    if quarters is None:
        raise ValueError("quarter_cluster_t needs the entry quarter of each event")
    if not math.isfinite(z) or z <= 0:
        raise ValueError("z must be finite and positive")
    stats = quarter_cluster_stats(values, quarters, min_quarters=QUARTER_CLUSTER_MIN_QUARTERS)
    df = max(int(stats["clusters"]) - 1, 1)
    mean, se = stats["mean"], stats["se"]
    crit = student_t_quantile(NormalDist().cdf(z), df) if se == se else math.nan
    power = student_t_quantile(0.8, df) if se == se else math.nan
    return {
        "effect": mean,
        "se": se,
        "t": stats["z_eq"],
        "t_raw": stats["t"],
        "z_eq": stats["z_eq"],
        "ci_low": mean - crit * se,
        "ci_high": mean + crit * se,
        "mde_80": (crit + power) * se,
        "n": stats["n"],
        "clusters": stats["clusters"],
        "quarters": stats["clusters"],
        "df": df,
    }

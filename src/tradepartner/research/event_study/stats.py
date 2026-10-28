"""Pure inference and overlap measures for event studies."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence
from datetime import date, timedelta


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
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
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

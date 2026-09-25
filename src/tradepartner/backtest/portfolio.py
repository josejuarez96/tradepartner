"""Portfolio construction and trades (backtest spec reqs 3 and 4).

Pure functions over plain mappings keyed by `security_id`. Long-only: every weight is
non-negative, weights sum to at most 1, and the remainder is cash.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Literal

Weighting = Literal["equal"]


def _selected_count(n_ranked: int, top_fraction: float) -> int:
    """ceil(top_fraction * n), with the fraction read as the decimal it was written as.

    `Fraction(0.1).limit_denominator()` is exactly 1/10, so 0.1 of 30 names is 3 and
    not the 4 that `ceil(0.1 * 30)` gives (0.1 * 30 == 3.0000000000000004).
    """
    exact = Fraction(top_fraction).limit_denominator() * n_ranked
    return math.ceil(exact)


def _equal_weight(k: int) -> float:
    """1/k, stepped down by an ulp if k copies would sum above 1 in any summation order."""
    weight = 1 / k
    while sum([weight] * k) > 1 or math.fsum([weight] * k) > 1:
        weight = math.nextafter(weight, 0)
    return weight


def target_weights(
    scores: Mapping[str, float], top_fraction: float, weighting: Weighting
) -> dict[str, float]:
    """Target weights for the top `top_fraction` of names ranked by score.

    Ranked by score descending, ties by `security_id` ascending. The count selected
    is the ceiling of `top_fraction` times the number of scored names, so any non-empty
    score set selects at least one name. `equal` weighting gives each selected name the
    same weight, summing to at most 1. An empty score set gives no targets (all cash).
    The result is ordered by `security_id`.
    """
    if not (0 < top_fraction <= 1):
        raise ValueError(f"top_fraction must be in (0, 1], got {top_fraction}")
    if weighting != "equal":
        raise ValueError(f"unknown weighting {weighting!r}; only 'equal' is defined")
    for sid, score in scores.items():
        if not math.isfinite(score):
            raise ValueError(f"score for {sid} must be finite, got {score}")
    if not scores:
        return {}
    ranked = sorted(scores, key=lambda sid: (-scores[sid], sid))
    selected = ranked[: _selected_count(len(ranked), top_fraction)]
    weight = _equal_weight(len(selected))
    return {sid: weight for sid in sorted(selected)}


def drifted_weights(position_values: Mapping[str, float], cash: float) -> dict[str, float]:
    """Current weights from dollar position values and cash: value / equity.

    Positions are carried as dollar values (req 2), so the drift over a month is
    already in `position_values`. Raises `ValueError` for a negative or non-finite value
    or cash, or zero equity.
    """
    for name, value in (*position_values.items(), ("cash", cash)):
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{name} must be non-negative and finite, got {value}")
    equity = math.fsum([*position_values.values(), cash])
    if equity <= 0:
        raise ValueError("equity must be positive")
    return {sid: value / equity for sid, value in sorted(position_values.items())}


def trades_from(target: Mapping[str, float], drifted: Mapping[str, float]) -> dict[str, float]:
    """Weight changes, target minus drifted, over the union of names (req 4).

    Positive is a buy and negative a sell; a held name absent from `target` is sold
    out. Exact zeros are dropped. Ordered by `security_id`.
    """
    trades = {
        sid: target.get(sid, 0.0) - drifted.get(sid, 0.0) for sid in sorted({*target, *drifted})
    }
    return {sid: delta for sid, delta in trades.items() if delta != 0}

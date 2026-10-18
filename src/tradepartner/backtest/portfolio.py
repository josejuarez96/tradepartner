"""Portfolio construction and trades (backtest spec reqs 3 and 4).

Pure functions over plain mappings keyed by `security_id`. Long-only: every weight is
non-negative, weights sum to at most 1, and the remainder is cash.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

Weighting = Literal["equal", "hold"]


def _selected_count(n_ranked: int, top_fraction: float) -> int:
    """ceil(top_fraction * n), at least 1, with the fraction read as the decimal it
    was written as.

    `Fraction(0.1).limit_denominator()` is exactly 1/10, so 0.1 of 30 names is 3 and
    not the 4 that `ceil(0.1 * 30)` gives (0.1 * 30 == 3.0000000000000004). When the
    capped fraction is not the same `float` (1e-7 caps to 0, #1446), the fraction is
    its shortest decimal form instead. A positive fraction of a non-empty set selects
    at least one name; an empty set selects none.
    """
    small = Fraction(top_fraction).limit_denominator()
    fraction = small if float(small) == top_fraction else Fraction(repr(top_fraction))
    return max(1, math.ceil(fraction * n_ranked)) if n_ranked else 0


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
        raise ValueError(
            f"target_weights builds only 'equal' weighting, got {weighting!r}; "
            "'hold' is built by hold_membership"
        )
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


def apply_rebalance_band(
    target: Mapping[str, float],
    current: Mapping[str, float],
    band: float,
) -> dict[str, float]:
    """Effective target weights with every drift inside the no-trade band left alone.

    `strategy.rebalance_band` is a fraction of a name's target weight (backtest spec
    amendment #1731): a name whose absolute drift from its target is at most
    `band * target` is not traded and keeps its current weight. A name absent from
    `target` has a target of 0 and therefore a band of 0, so an exit is always traded.
    The names still traded are then reconciled into what is left of the budget, so the
    result never sums above 1 and scaling only ever shrinks: each traded name's target is
    scaled by `min(1, (1 - Σ held-within-band weights) / Σ traded target weights)`, so a
    traded name is never lifted past its target and leftover equity stays cash. Scaling is
    a no-op when every target is traded and the targets already sum to at most 1, and 0
    when the held names fill the budget. A band of 0 is the identity and returns `target`
    unchanged; a band wide enough to cover every drift returns `current` over the shared
    names. Names whose effective weight is 0, and unscaled exact zeros, are dropped, as
    `trades_from` drops zero deltas. `band` must be in [0, 1): a band of 1 or more would
    suppress every new entry. Raises `ValueError` for a band outside that range. Ordered by
    `security_id`.
    """
    if not (math.isfinite(band) and 0 <= band < 1):
        raise ValueError(f"rebalance_band must be in [0, 1), got {band}")
    if band == 0:
        return dict(target)
    held_within: dict[str, float] = {}
    traded: dict[str, float] = {}
    for sid in sorted({*target, *current}):
        desired = target.get(sid, 0.0)
        held = current.get(sid, 0.0)
        if abs(desired - held) <= band * desired:
            held_within[sid] = held
        else:
            traded[sid] = desired
    budget = max(0.0, 1.0 - math.fsum(held_within.values()))
    traded_total = math.fsum(traded.values())
    scale = min(1.0, budget / traded_total) if traded_total > 0 else 0.0
    effective = {sid: weight for sid, weight in held_within.items() if weight != 0}
    for sid, weight in traded.items():
        scaled = weight * scale
        if scaled != 0:
            effective[sid] = scaled
    return effective


@dataclass(frozen=True)
class HoldMembership:
    """The held set at one session under `strategy.weighting = "hold"` (backtest spec
    amendment #1731, "Hold mode and the no-trade band").

    `entering` and `exiting` are the names added to and removed from the held set at the
    session; `held` is the new held set, a live name keeping its place and a new entry
    following in rank order. `entries` is the state to carry to the next session: every
    held name with the session it entered. A name whose hold elapsed at the session is
    eligible to enter again in it (a re-entry restarts its hold), so it can appear in
    both `exiting` and `entering`.
    """

    entering: tuple[str, ...]
    exiting: tuple[str, ...]
    held: tuple[str, ...]
    entries: Mapping[str, int]


def hold_membership(
    ranked: Sequence[str],
    entries: Mapping[str, int],
    session: int,
    hold_sessions: int,
    hold_target_names: int,
    top_fraction: float,
) -> HoldMembership:
    """Hold-mode membership at `session`, given each held name's entry session.

    An entry made at session E is live at sessions E, E+1, …, E + `hold_sessions` - 1 and
    exits at session E + `hold_sessions`; so a name exits on session N and no earlier.
    Live names are all kept. New entries fill the target up to `hold_target_names` (the
    best `ranked` names not already live, in rank order); a non-positive
    `hold_target_names` means the count `top_fraction` selects. A book with fewer live
    names than its target holds the rest in cash, so live names are never force-exited.
    Raises `ValueError` for `hold_sessions` below 1 or a negative `session`.
    """
    if hold_sessions < 1:
        raise ValueError(f"hold_sessions must be at least 1, got {hold_sessions}")
    if session < 0:
        raise ValueError(f"session must be non-negative, got {session}")
    target = (
        hold_target_names if hold_target_names > 0 else _selected_count(len(ranked), top_fraction)
    )
    live: list[str] = []
    exiting: list[str] = []
    for name, entered in entries.items():
        (exiting if session - entered >= hold_sessions else live).append(name)
    live_names = set(live)
    entering = [name for name in dict.fromkeys(ranked) if name not in live_names][
        : max(0, target - len(live))
    ]
    new_entries = {name: entries[name] for name in live}
    new_entries.update({name: session for name in entering})
    return HoldMembership(
        entering=tuple(entering),
        exiting=tuple(exiting),
        held=tuple((*live, *entering)),
        entries=new_entries,
    )

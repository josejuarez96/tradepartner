"""Portfolio construction and trades (backtest spec reqs 3 and 4).

Pure functions over plain mappings keyed by `security_id`. Long-only: every weight is
non-negative, weights sum to at most 1, and the remainder is cash. A signed book
(`portfolio.side = "long_short"`, spec amendment #1872) builds through
`signed_target_weights`.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
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


def signed_target_weights(
    scores: Mapping[str, float],
    top_fraction: float,
    gross: float,
    net: float,
    eligible: Collection[str],
) -> dict[str, float]:
    """Signed target weights for a long/short book (backtest spec amendment #1872,
    item 2).

    `ranked` is the scored names by score descending, ties by `security_id` ascending, as
    `target_weights` ranks them. The **long leg** is the top `k_L` ranked names, `k_L`
    the count `top_fraction` selects from them, each at `(gross + net) / 2 / k_L`. The
    **short leg** is chosen after it: `k_S` is the count `top_fraction` selects from the
    eligible names (`eligible`, the names that pass item 3's price proxy), and the leg is
    the `k_S` lowest-scored eligible names not in the long leg, each at
    `-(gross - net) / 2 / k_S`. An ineligible low scorer is skipped and the next-lowest
    eligible name takes its place; a name is never in both legs; when fewer than `k_S`
    eligible names remain outside the long leg, the short leg takes those that remain and
    `k_S` is that count. The legs are disjoint, each name with a non-zero target has one
    weight, and the result is ordered by `security_id`. When both legs are full the long
    leg sums to `(gross + net) / 2` and the short leg to `-(gross - net) / 2`, so
    Σ |w| = `gross` and Σ w = `net`; a truncated or empty leg scales its own share.

    `gross == net == 1.0` is the `long_only` book (spec item 1 makes `net_exposure` equal
    `gross_exposure` there at the default), and the result is `target_weights`' output
    exactly. A `gross == net` below 1.0 is still a signed book with no short leg: its
    long leg carries the `gross` share, not the whole book. Raises `ValueError` for a
    `top_fraction` outside `(0, 1]`, a non-finite score, `gross` or `net`, a `gross` at
    or below 0, or a `net` outside `[-gross, gross]` (outside that range a "short" leg's
    weight would turn into a long buy).
    """
    if not (0 < top_fraction <= 1):
        raise ValueError(f"top_fraction must be in (0, 1], got {top_fraction}")
    for sid, score in scores.items():
        if not math.isfinite(score):
            raise ValueError(f"score for {sid} must be finite, got {score}")
    for name, value in (("gross", gross), ("net", net)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value}")
    if gross <= 0:
        raise ValueError(f"gross must be positive, got {gross}")
    if not (-gross <= net <= gross):
        raise ValueError(f"net must be in [{-gross}, {gross}], got {net}")
    if gross == net == 1.0:
        return target_weights(scores, top_fraction, "equal")
    if not scores:
        return {}
    ranked = sorted(scores, key=lambda sid: (-scores[sid], sid))
    long_leg = ranked[: _selected_count(len(ranked), top_fraction)]
    in_long = set(long_leg)
    eligible_set = set(eligible)
    eligible_ranked = [sid for sid in ranked if sid in eligible_set]
    k_short = _selected_count(len(eligible_ranked), top_fraction)
    short_leg = [sid for sid in reversed(eligible_ranked) if sid not in in_long][:k_short]
    targets: dict[str, float] = {}
    long_weight = (gross + net) / 2 / len(long_leg)
    if long_weight:
        targets.update({sid: long_weight for sid in long_leg})
    if short_leg:
        short_weight = -(gross - net) / 2 / len(short_leg)
        if short_weight:
            targets.update({sid: short_weight for sid in short_leg})
    return {sid: targets[sid] for sid in sorted(targets)}


def buffered_targets(
    scores: Mapping[str, float],
    previous: Collection[str],
    top_fraction: float,
    hold_top_fraction: float,
) -> tuple[dict[str, float], int]:
    """Targets under the buy/hold buffer, and the number of retained names (backtest
    spec amendment #657, item 2).

    `ranked` is the scored names by score descending, ties by `security_id` ascending;
    n = |ranked|, k = the `top_fraction` count and h = the `hold_top_fraction` count
    (the same count rule, so h >= k). The targets are `ranked[:k]` plus every name of
    `previous` (the previous plan's targets) that is in `ranked[:h]`, each at the
    `equal` weight 1 / |targets|. A previous name that is not scored is never kept.
    `n_retained` is the number of targets outside `ranked[:k]`.

    `hold_top_fraction` 0.0 means no buffer: the result is `target_weights`' output with
    zero retained, and `previous` is not read. Otherwise it must be in
    [`top_fraction`, 1]. Raises `ValueError` for a bad fraction or a non-finite score.
    """
    if hold_top_fraction == 0.0:
        return target_weights(scores, top_fraction, "equal"), 0
    if not (0 < top_fraction <= 1):
        raise ValueError(f"top_fraction must be in (0, 1], got {top_fraction}")
    if not (top_fraction <= hold_top_fraction <= 1):
        raise ValueError(
            f"hold_top_fraction must be 0.0 or in [{top_fraction}, 1], got {hold_top_fraction}"
        )
    for sid, score in scores.items():
        if not math.isfinite(score):
            raise ValueError(f"score for {sid} must be finite, got {score}")
    if not scores:
        return {}, 0
    ranked = sorted(scores, key=lambda sid: (-scores[sid], sid))
    top = set(ranked[: _selected_count(len(ranked), top_fraction)])
    kept = set(ranked[: _selected_count(len(ranked), hold_top_fraction)]) & set(previous)
    targets = top | kept
    weight = _equal_weight(len(targets))
    return {sid: weight for sid in sorted(targets)}, len(targets - top)


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

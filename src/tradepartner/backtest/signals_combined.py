"""B4 `combined`: momentum and gross profitability combined by equal ranks (hypothesis
backlog B4; ADR 0014 point 6, T130).

`combined_rank` averages the cross-sectional ranks the two sub-signals give the names
they both score. The combination is fixed in advance and never fitted on returns
(HO-10): over the common names, each sub-signal ranks by its own score (ties broken by
`security_id` ascending, the rule `portfolio.target_weights` and
`ProfitabilitySignal.ranked` use) and a common name's score is the mean of its two ranks.
Because both rank vectors are taken over the same common set, each sub-signal carries the
same weight, and a higher combined score is a better name.

Exclusions are pairwise disjoint by this precedence: a name momentum excludes carries
momentum's reason (`no_history`); otherwise a name profitability excludes carries its
reason (`sector`, `no_facts`, `stale_facts`, `malformed`); otherwise a name exactly one
sub-signal scores is `one_signal_only`. Every reason is present, possibly empty, and each
reason's names are ordered by `security_id`.

`counts` are both sub-signals' counts plus `n_combined`, the number of names both score.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from tradepartner.backtest.signals import MomentumSignal, ProfitabilitySignal

CombinedExclusion = Literal[
    "no_history",
    "sector",
    "no_facts",
    "stale_facts",
    "malformed",
    "one_signal_only",
]

#: Every exclusion reason `combined_rank` may report, in precedence order.
COMBINED_EXCLUSIONS: tuple[CombinedExclusion, ...] = (
    "no_history",
    "sector",
    "no_facts",
    "stale_facts",
    "malformed",
    "one_signal_only",
)


@dataclass(frozen=True)
class CombinedSignal:
    """Equal-rank combined scores and the exclusions that leave the common set.

    `scores` holds the names both sub-signals score, each the mean of its momentum rank
    and its profitability rank (higher is better), ordered by `security_id`. `excluded`
    maps every declared reason, in precedence order, to its names. `counts` are the
    sub-signals' counts plus `n_combined`.
    """

    scores: dict[str, float]
    excluded: dict[CombinedExclusion, tuple[str, ...]]
    counts: dict[str, int]


def _ranks(scores: Mapping[str, float]) -> dict[str, int]:
    """Each scored name's rank, best first: the highest score gets the largest rank (one
    per name, so ranks are distinct), ties broken by `security_id` ascending."""
    ranked = sorted(scores, key=lambda sid: (-scores[sid], sid))
    count = len(ranked)
    return {sid: count - position for position, sid in enumerate(ranked)}


def combined_rank(momentum: MomentumSignal, profitability: ProfitabilitySignal) -> CombinedSignal:
    """The equal-rank combination of `momentum` and `profitability` (T130).

    Scores only the names both sub-signals score, each the mean of its two ranks over
    that common set, and partitions every other name by the precedence above. Pure: it
    reads the two signal results and nothing else, and fits nothing.
    """
    common = sorted(set(momentum.scores) & set(profitability.scores))
    momentum_ranks = _ranks({sid: momentum.scores[sid] for sid in common})
    profitability_ranks = _ranks({sid: profitability.scores[sid] for sid in common})
    scores = {sid: (momentum_ranks[sid] + profitability_ranks[sid]) / 2 for sid in common}

    reason_of: dict[str, CombinedExclusion] = {
        sid: cast(CombinedExclusion, reason)
        for reason, ids in profitability.excluded.items()
        for sid in ids
    }
    momentum_excluded = set(momentum.excluded)
    names = sorted(
        set(momentum.scores) | momentum_excluded | set(profitability.scores) | set(reason_of)
    )
    excluded: dict[CombinedExclusion, list[str]] = {reason: [] for reason in COMBINED_EXCLUSIONS}
    common_set = set(common)
    for sid in names:
        if sid in common_set:
            continue
        if sid in momentum_excluded:
            excluded["no_history"].append(sid)
        elif sid in reason_of:
            excluded[reason_of[sid]].append(sid)
        elif sid in momentum.scores or sid in profitability.scores:
            excluded["one_signal_only"].append(sid)

    return CombinedSignal(
        scores=scores,
        excluded={reason: tuple(ids) for reason, ids in excluded.items()},
        counts={
            "n_excluded_no_history": momentum.n_excluded,
            **profitability.counts,
            "n_combined": len(scores),
        },
    )

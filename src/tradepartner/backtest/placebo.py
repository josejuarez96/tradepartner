"""Pure placebo generators and the trial-row tag for the kill ladder's rung 5
(ADR 0023; backtest spec amendment #1820, items 2 and 3).

Stdlib only. `seed` is the string `f"{placebo_seed}:{rebalance_session.toordinal()}"`
that `random.Random` receives, so one `placebo_seed` draws afresh at every rebalance and
the plan step carries no generator state. The scored names are sorted by security id
first, so the result never depends on the mapping's order. Exclusions and counts stay
the caller's.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any


def shuffle(scores: Mapping[str, float], seed: str) -> dict[str, float]:
    """Assign the id-sorted score vector back to the id-sorted names after one shuffle.

    The names and the multiset of scores are kept; the pairing is random.
    """
    names = sorted(scores)
    vector = [scores[name] for name in names]
    random.Random(seed).shuffle(vector)
    return dict(zip(names, vector, strict=True))


def random_entry(scores: Mapping[str, float], seed: str) -> dict[str, float]:
    """A random full ranking: the id-sorted names shuffled once, scored n down to 1.

    The input scores are ignored (only the names are used); no two names tie.
    """
    names = sorted(scores)
    random.Random(seed).shuffle(names)
    n = len(names)
    return {name: float(n - i) for i, name in enumerate(names)}


def shock_note(frozen_values: Mapping[str, Any]) -> str | None:
    """The trial-row tag: `placebo=<mode> seed=<n>` when the placebo is not `none` and
    `signal_delay=<d>` when d > 0, space-separated in that order; None at the defaults."""
    parts: list[str] = []
    mode = frozen_values["strategy.placebo"]
    if mode != "none":
        parts.append(f"placebo={mode} seed={frozen_values['strategy.placebo_seed']}")
    delay = frozen_values["strategy.signal_delay_sessions"]
    if delay > 0:
        parts.append(f"signal_delay={delay}")
    return " ".join(parts) or None

"""Window, holdout and gap-gate decisions (backtest spec req 11; plan T36).

Pure: every input is passed in, and the only outside reference is the XNYS
calendar. The run orchestration (T39) opens the trial handle first, then asks
`decide` what to do. The rules, in the order they are checked:

1. **Window.** A window starting before the frozen `in_sample_start`, ending
   after the frozen `holdout.end` (those sessions belong to Phase 4 tracking),
   or ending before it starts is `refused_window`.
2. **Holdout.** A window touching the frozen `[holdout.start, holdout.end]` is
   `refused_holdout` unless `--spend-holdout` comes with a non-blank reason.
   A holdout run is marked `holdout_repeat` when any hypothesis in the family
   already spent the holdout (domain rule 3); a prior spend by the **same**
   hypothesis additionally needs `--holdout-repeat`. Every spend the caller
   passes counts, whatever its outcome (`registry.family_holdout_spends`).
3. **Gap gate** (holdout runs only, ADR 0003 rule 6). The run is
   `refused_gap` when the survivorship-gap count share at any rebalance close
   of the window exceeds the frozen `gap.count_share_threshold`, unless
   `--override-gap` comes with a non-blank reason. A NaN share counts as a
   breach: it cannot show the gap is below the threshold.

Rules 1 and 2 need no provider read. When they pass for a holdout window and
no gap series is given yet, the outcome is `needs_gap`: the caller reads
`survivorship_gap` at `Decision.gap_sessions` only, then calls `decide` again
with the series. A flag given without its reason is refused at its rule, so
a gate is never passed on an unexplained flag.

Rebalance sessions are the last XNYS session of each month (ADR 0006).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from tradepartner.calendar import last_session_of_month
from tradepartner.store.registry import HoldoutSpend, HypothesisRecord

Outcome = Literal["run", "needs_gap", "refused_window", "refused_holdout", "refused_gap"]
RunKind = Literal["in_sample", "holdout"]

GAP_THRESHOLD_KEY = "gap.count_share_threshold"


@dataclass(frozen=True)
class Window:
    """A requested backtest window, both ends inclusive calendar dates."""

    start: date
    end: date


@dataclass(frozen=True)
class Frozen:
    """The frozen values the decisions read, from the registered hypothesis,
    never from live `Settings` (spec req 10)."""

    hypothesis_id: int
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    gap_count_share_threshold: float

    @classmethod
    def from_hypothesis(cls, hypothesis: HypothesisRecord) -> Frozen:
        """Read the decision inputs from a `hypotheses` row and its frozen params."""
        threshold = hypothesis.params.get(GAP_THRESHOLD_KEY)
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            raise ValueError(
                f"hypothesis {hypothesis.slug!r} has no numeric frozen {GAP_THRESHOLD_KEY}"
            )
        return cls(
            hypothesis_id=hypothesis.hypothesis_id,
            in_sample_start=hypothesis.in_sample_start,
            holdout_start=hypothesis.holdout_start,
            holdout_end=hypothesis.holdout_end,
            gap_count_share_threshold=float(threshold),
        )


@dataclass(frozen=True)
class Flags:
    """The owner's CLI flags (spec req 16)."""

    spend_holdout: bool = False
    holdout_repeat: bool = False
    override_gap: bool = False


@dataclass(frozen=True)
class Reasons:
    """The reasons that must accompany `spend_holdout` and `override_gap`."""

    holdout_reason: str | None = None
    gap_reason: str | None = None


@dataclass(frozen=True)
class Decision:
    """What the run does next.

    `kind` is set when the window rules pass (`None` for `refused_window` and
    `refused_holdout`). `holdout_reason` is set only for a holdout run and
    `gap_override_reason` only when the override was needed to pass the gate.
    `gap_sessions` names the sessions to read the gap at (holdout runs only);
    `gap_breaches` lists every `(session, count share)` above the threshold.
    """

    outcome: Outcome
    kind: RunKind | None
    message: str
    holdout_repeat: bool = False
    holdout_reason: str | None = None
    gap_override_reason: str | None = None
    gap_sessions: tuple[date, ...] = ()
    gap_breaches: tuple[tuple[date, float], ...] = ()


def _has_text(reason: str | None) -> bool:
    return reason is not None and reason.strip() != ""


def _next_month(day: date) -> tuple[int, int]:
    return (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)


def _previous_month(day: date) -> tuple[int, int]:
    return (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)


def gap_sessions(window: Window) -> tuple[date, ...]:
    """The rebalance sessions (last XNYS session of each month) inside `window`."""
    sessions: list[date] = []
    year, month = window.start.year, window.start.month
    while (year, month) <= (window.end.year, window.end.month):
        session = last_session_of_month(year, month)
        if window.start <= session <= window.end:
            sessions.append(session)
        year, month = _next_month(date(year, month, 1))
    return tuple(sessions)


def window_touches_holdout(window: Window, frozen: Frozen) -> bool:
    """Whether `window` shares at least one day with the frozen holdout."""
    return window.start <= frozen.holdout_end and window.end >= frozen.holdout_start


def default_in_sample_window(frozen: Frozen) -> Window:
    """`[in_sample_start, last rebalance session strictly before holdout.start]`,
    so an ordinary run cannot drift into the holdout."""
    end = last_session_of_month(frozen.holdout_start.year, frozen.holdout_start.month)
    if end >= frozen.holdout_start:
        end = last_session_of_month(*_previous_month(frozen.holdout_start))
    if end < frozen.in_sample_start:
        raise ValueError(
            f"no rebalance session between in_sample_start {frozen.in_sample_start} "
            f"and holdout.start {frozen.holdout_start}"
        )
    return Window(frozen.in_sample_start, end)


def _window_refusal(window: Window, frozen: Frozen) -> str | None:
    if window.end < window.start:
        return f"window end {window.end} is before its start {window.start}"
    if window.start < frozen.in_sample_start:
        return f"window start {window.start} is before in_sample_start {frozen.in_sample_start}"
    if window.end > frozen.holdout_end:
        return (
            f"window end {window.end} is after holdout.end {frozen.holdout_end}; "
            "later sessions belong to Phase 4 tracking"
        )
    return None


def decide(
    window: Window,
    frozen: Frozen,
    flags: Flags,
    reasons: Reasons,
    gap_series: Mapping[date, float] | None,
    prior_spends: Sequence[HoldoutSpend],
) -> Decision:
    """Apply the window, holdout and gap rules (module docstring) to one run.

    `gap_series` maps rebalance sessions to the survivorship-gap count share
    read at their close, or is `None` before any read. `prior_spends` are the
    family's holdout trials before this one. Raises `ValueError` when a
    series is given but misses a rebalance session of the window.
    """
    refusal = _window_refusal(window, frozen)
    if refusal is not None:
        return Decision("refused_window", None, refusal)

    if not window_touches_holdout(window, frozen):
        return Decision("run", "in_sample", "in-sample run")

    holdout = f"[{frozen.holdout_start}, {frozen.holdout_end}]"
    if not flags.spend_holdout:
        return Decision(
            "refused_holdout",
            None,
            f"window touches the holdout {holdout}; spending it needs --spend-holdout",
        )
    if not _has_text(reasons.holdout_reason):
        return Decision("refused_holdout", None, "--spend-holdout needs a --holdout-reason")
    same_hypothesis = [s for s in prior_spends if s.hypothesis_id == frozen.hypothesis_id]
    if same_hypothesis and not flags.holdout_repeat:
        trials = ", ".join(str(s.trial_id) for s in same_hypothesis)
        return Decision(
            "refused_holdout",
            None,
            f"this hypothesis already spent the holdout (trials {trials}); "
            "a repeat needs --holdout-repeat",
        )
    spend = Decision(
        "run",
        "holdout",
        "holdout run",
        holdout_repeat=bool(prior_spends),
        holdout_reason=reasons.holdout_reason,
    )

    if flags.override_gap and not _has_text(reasons.gap_reason):
        return replace(spend, outcome="refused_gap", message="--override-gap needs a --gap-reason")

    sessions = gap_sessions(window)
    if gap_series is None:
        return replace(
            spend,
            outcome="needs_gap",
            message="read the survivorship gap at the window's rebalance sessions",
            gap_sessions=sessions,
        )
    missing = [s for s in sessions if s not in gap_series]
    if missing:
        raise ValueError(f"gap series has no value for {', '.join(map(str, missing))}")

    threshold = frozen.gap_count_share_threshold
    breaches = tuple(
        (s, gap_series[s])
        for s in sessions
        if math.isnan(gap_series[s]) or gap_series[s] > threshold
    )
    gated = replace(spend, gap_sessions=sessions, gap_breaches=breaches)
    if not breaches:
        return replace(gated, message="holdout run; gap within threshold")
    listed = ", ".join(f"{s} ({share:.4f})" for s, share in breaches)
    if not flags.override_gap:
        return replace(
            gated,
            outcome="refused_gap",
            message=f"survivorship-gap count share above {threshold} at {listed}",
        )
    return replace(
        gated,
        message=f"holdout run; gap above {threshold} at {listed} overridden by the owner",
        gap_override_reason=reasons.gap_reason,
    )

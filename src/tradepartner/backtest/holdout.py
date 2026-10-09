"""Window, holdout and gap-gate decisions (backtest spec req 11; plan T36; at a cadence,
strategy-lab spec req 6, plan T98).

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

**Tracking windows** (Phase 4 spec req 10; plan T53). `decide(..., tracking=True)`
replaces rules 1 to 3 for a `kind=tracking` trial (a plan trial or `paper report`'s
tracking trial): the window must start at or after the first rebalance session
**after** the frozen `holdout.end` (`first_tracking_session`) and end no earlier than
it starts, else `refused_window`; it is never a holdout spend or repeat and has no gap
gate, and the holdout and gap flags are ignored.

Rules 1 and 2 need no provider read. When they pass for a holdout window and
no gap series is given yet, the outcome is `needs_gap`: the caller reads
`survivorship_gap` at `Decision.gap_sessions` only, then calls `decide` again
with the series. A flag given without its reason is refused at its rule, so
a gate is never passed on an unexplained flag.

**Strategy-lab rules** (strategy-lab spec req 5; plan T110). `decide(..., lab=...)`
takes a `LabState` that the caller reads **only when the store is lab-initialised**
(`lab_schema.is_lab_initialised`); with `lab=None`, the default, every rule above
applies exactly as in Phase 3, so a store without the lab tables (the owner's store
at version P, a plain fixture store) decides as it always has. With a `LabState`:

0. **Variant.** A sweep variant is `refused_variant` before any other rule, the
   tracking rule included: variants run only through `sweep run`, which cannot
   spend the holdout (req 5(a)).
2b. **Spend gate.** A holdout spend (rule 2's flag and reason given) by a
   hypothesis that is neither pre-lab (`is_pre_lab`) nor named by a `promotion`
   decision is `refused_holdout` (req 5(b)).
2c. **Family cap.** A spend when the family already has the family rules'
   `max_family_holdout_spends` holdout spends of any outcome (`prior_spends`,
   research spends included) is `refused_holdout`, naming them (req 5(d)); below
   the cap a family repeat is marked `holdout_repeat` as before.

Rebalance sessions are those of the hypothesis's frozen `schedule.rebalance_cadence`
(`schedule.rebalance_sessions`; ADR 0012): `decide`'s "touches the holdout but reaches
none of its rebalance sessions" rule, the gap gate's sessions (`gap_sessions`) and the
default in-sample window's end (`default_in_sample_window`) take it. The caller passes
it from the frozen `Settings` (`hypothesis.load_frozen`, through
`frozen.frozen_values`). The tracking rule's first session (`first_tracking_session`)
is at the same cadence (ADR 0015 seam 4); `paper start` still refuses any cadence but
`month_end` (strategy-lab spec req 11), so a paper window's is a month end.

**The development boundary** (ADR 0016 points 2 and 4; plan T142). `decide` and
`default_in_sample_window` take `boundary`, the owner's newest `development_boundary`
date, which the caller reads from the store (never live config; it is not a frozen key
and enters no fingerprint). With `boundary=None`, the default, every rule above applies
exactly as written. With a boundary:

- The default in-sample window ends at the earlier of the last rebalance session
  strictly before `holdout.start` and the last one on or before the boundary.
- Rule 1 gains two refusals, checked before its others (and so before the holdout and
  gap rules): a window that does not touch the holdout (an in-sample window) and ends
  after the boundary, and a window that touches the holdout (a holdout spend, flag or
  not) but starts before `holdout.start`. No flag overrides either. The sessions
  between the boundary and `holdout.start` are dead months: no window may read them.
- A boundary before the frozen `in_sample_start` raises `ValueError`.

**Forward holdouts** (ADR 0016 point 4). `Frozen.registered_on` is the day the family
was first registered, passed by the caller (`None` when not read); `is_forward` is true
when `holdout.start` is after it. A forward holdout's tracking window may start at the
first rebalance session on or after `holdout.start` (`tracking_start`), because its exam
of record is the paper book inside the holdout; every other tracking window keeps
`first_tracking_session`. The tracking rule does not read the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.config import Cadence
from tradepartner.store.registry import HoldoutSpend, HypothesisRecord

Outcome = Literal[
    "run", "needs_gap", "refused_window", "refused_holdout", "refused_gap", "refused_variant"
]
RunKind = Literal["in_sample", "holdout", "tracking"]

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
    registered_on: date | None = None

    def __post_init__(self) -> None:
        if not self.in_sample_start < self.holdout_start <= self.holdout_end:
            raise ValueError(
                f"frozen dates must satisfy in_sample_start ({self.in_sample_start}) < "
                f"holdout.start ({self.holdout_start}) <= holdout.end ({self.holdout_end})"
            )
        threshold = self.gap_count_share_threshold
        if not (math.isfinite(threshold) and 0 <= threshold < 1):
            raise ValueError(
                f"{GAP_THRESHOLD_KEY} must be a finite share in [0, 1), got {threshold}"
            )

    @classmethod
    def from_hypothesis(
        cls, hypothesis: HypothesisRecord, *, registered_on: date | None = None
    ) -> Frozen:
        """Read the decision inputs from a `hypotheses` row and its frozen params,
        through `frozen.frozen_values` (the one accessor of a registration's values).

        `registered_on` is the day the hypothesis's **family** was first registered
        (ADR 0016 point 4), which one `hypotheses` row cannot know; `None` leaves the
        holdout historical (`is_forward` false), as every caller had it before T142."""
        threshold = frozen_values(hypothesis).get(GAP_THRESHOLD_KEY)
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
            registered_on=registered_on,
        )


def is_forward(frozen: Frozen) -> bool:
    """Whether the holdout is forward: its `holdout.start` is after the day the family
    was first registered, so no session of it existed at registration (ADR 0016 point 4).
    False when `registered_on` is unknown."""
    return frozen.registered_on is not None and frozen.holdout_start > frozen.registered_on


@dataclass(frozen=True)
class LabState:
    """The strategy-lab inputs to `decide` (module docstring, "Strategy-lab rules"),
    read by the caller from a lab-initialised store and never otherwise.

    `is_variant`: the hypothesis is a sweep variant (a `sweep_variants` row).
    `is_pre_lab`: it has a `pre_lab_hypotheses` row. `promoted`: a `promotion`
    decision names it. `max_family_holdout_spends`: the family rules' spend cap.
    """

    is_variant: bool
    is_pre_lab: bool
    promoted: bool
    max_family_holdout_spends: int

    def __post_init__(self) -> None:
        if self.max_family_holdout_spends < 1:
            raise ValueError(
                f"max_family_holdout_spends must be positive, got {self.max_family_holdout_spends}"
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


#: How far past `holdout.end` `first_tracking_session` looks: the slowest cadence,
#: `month_end`, has its next rebalance session within two calendar months.
_TRACKING_SEARCH = timedelta(days=62)


def gap_sessions(window: Window, cadence: Cadence = "month_end") -> tuple[date, ...]:
    """The rebalance sessions at `cadence` inside `window` (none when it ends before it
    starts): where the gap gate reads `survivorship_gap`."""
    if window.end < window.start:
        return ()
    return tuple(rebalance_sessions(window.start, window.end, cadence))


def window_touches_holdout(window: Window, frozen: Frozen) -> bool:
    """Whether `window` shares at least one day with the frozen holdout."""
    return window.start <= frozen.holdout_end and window.end >= frozen.holdout_start


def _first_rebalance_on_or_after(day: date, cadence: Cadence, what: str) -> date:
    sessions = rebalance_sessions(day, day + _TRACKING_SEARCH, cadence)
    if not sessions:
        raise ValueError(
            f"no rebalance session at {cadence} within {_TRACKING_SEARCH.days} days {what}"
        )
    return sessions[0]


def first_tracking_session(frozen: Frozen, cadence: Cadence) -> date:
    """The first rebalance session at `cadence` strictly after the frozen `holdout.end`:
    where a `kind=tracking` window may start (Phase 4 spec req 10; ADR 0015 seam 4).

    `cadence` is the hypothesis's frozen `schedule.rebalance_cadence`; the sessions come
    from `schedule.rebalance_sessions`, so a week end before Good Friday or a session
    after a holiday is found by the calendar, never by weekday arithmetic.
    """
    return _first_rebalance_on_or_after(
        frozen.holdout_end + timedelta(days=1),
        cadence,
        f"after holdout.end {frozen.holdout_end}",
    )


def tracking_start(frozen: Frozen, cadence: Cadence) -> date:
    """Where a `kind=tracking` window may start: for a forward holdout (`is_forward`),
    the first rebalance session at `cadence` on or after `holdout.start` (ADR 0016
    point 4, the paper book inside the holdout); otherwise `first_tracking_session`."""
    if is_forward(frozen):
        return _first_rebalance_on_or_after(
            frozen.holdout_start, cadence, f"from holdout.start {frozen.holdout_start}"
        )
    return first_tracking_session(frozen, cadence)


def _tracking(window: Window, frozen: Frozen, cadence: Cadence) -> Decision:
    first = tracking_start(frozen, cadence)
    if window.end < window.start:
        return Decision(
            "refused_window", None, f"window end {window.end} is before its start {window.start}"
        )
    if window.start < first:
        where = (
            f"on or after holdout.start {frozen.holdout_start} (a forward holdout)"
            if is_forward(frozen)
            else f"after holdout.end {frozen.holdout_end}"
        )
        return Decision(
            "refused_window",
            None,
            f"tracking window start {window.start} is before {first}, the first rebalance "
            f"session {where}",
        )
    return Decision("run", "tracking", "tracking run; holdout and gap flags are not read")


def _check_boundary(frozen: Frozen, boundary: date | None) -> None:
    if boundary is not None and boundary < frozen.in_sample_start:
        raise ValueError(
            f"development boundary {boundary} is before in_sample_start "
            f"{frozen.in_sample_start}: no in-sample session would remain"
        )


def default_in_sample_window(
    frozen: Frozen, cadence: Cadence = "month_end", boundary: date | None = None
) -> Window:
    """`[in_sample_start, last rebalance session at cadence strictly before
    holdout.start]`, so an ordinary run cannot drift into the holdout.

    With a development `boundary` (ADR 0016 point 2) the window ends at the earlier
    of that session and the last rebalance session on or before the boundary. Raises
    `ValueError` when the boundary is before `in_sample_start` or no rebalance session
    remains."""
    _check_boundary(frozen, boundary)
    if boundary is None:
        sessions = rebalance_sessions(
            frozen.in_sample_start, frozen.holdout_start - timedelta(days=1), cadence
        )
        if not sessions:
            raise ValueError(
                f"no rebalance session at {cadence} between in_sample_start "
                f"{frozen.in_sample_start} and holdout.start {frozen.holdout_start}"
            )
        return Window(frozen.in_sample_start, sessions[-1])
    last = min(frozen.holdout_start - timedelta(days=1), boundary)
    sessions = rebalance_sessions(frozen.in_sample_start, last, cadence)
    if not sessions:
        raise ValueError(
            f"no rebalance session at {cadence} between in_sample_start "
            f"{frozen.in_sample_start} and {last} (holdout.start {frozen.holdout_start}, "
            f"development boundary {boundary})"
        )
    return Window(frozen.in_sample_start, sessions[-1])


def _boundary_refusal(window: Window, frozen: Frozen, boundary: date) -> str | None:
    """ADR 0016 point 2: an in-sample window ends on or before the boundary; a window
    touching the holdout (a spend) lies inside it. Its end past `holdout.end` is rule 1's."""
    if window_touches_holdout(window, frozen):
        if window.start < frozen.holdout_start:
            return (
                f"window start {window.start} is before holdout.start {frozen.holdout_start}: "
                f"with the development boundary {boundary}, a window touching the holdout "
                f"lies inside [{frozen.holdout_start}, {frozen.holdout_end}]"
            )
        return None
    if window.end > boundary:
        return (
            f"window end {window.end} is after the development boundary {boundary}; "
            "in-sample runs read no session after it"
        )
    return None


def _window_refusal(window: Window, frozen: Frozen, boundary: date | None = None) -> str | None:
    if boundary is not None:
        refusal = _boundary_refusal(window, frozen, boundary)
        if refusal is not None:
            return refusal
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


def _lab_spend_refusal(lab: LabState, prior_spends: Sequence[HoldoutSpend]) -> str | None:
    """Rules 2b and 2c (module docstring) for a spend, or None when both pass."""
    if not (lab.is_pre_lab or lab.promoted):
        return (
            "only a pre-lab or promoted hypothesis may spend the holdout; this one is "
            "neither (write it as a one-value sweep and promote its argmax)"
        )
    cap = lab.max_family_holdout_spends
    if len(prior_spends) >= cap:
        listed = ", ".join(
            f"{s.slug} ({'research run' if s.source == 'research_run' else 'trial'} "
            f"{s.trial_id}, {s.status})"
            for s in prior_spends
        )
        return f"the family has its cap of {cap} holdout spends: {listed}"
    return None


def decide(
    window: Window,
    frozen: Frozen,
    flags: Flags,
    reasons: Reasons,
    gap_series: Mapping[date, float] | None,
    prior_spends: Sequence[HoldoutSpend],
    *,
    tracking: bool = False,
    cadence: Cadence = "month_end",
    lab: LabState | None = None,
    boundary: date | None = None,
) -> Decision:
    """Apply the window, holdout and gap rules (module docstring) to one run.

    `cadence` is the hypothesis's frozen `schedule.rebalance_cadence`: the rebalance
    sessions the holdout must reach and the gap gate reads at. `gap_series` maps
    rebalance sessions to the survivorship-gap count share
    read at their close, or is `None` before any read. `prior_spends` are the
    family's holdout trials **excluding this one**: a caller that opened this
    trial as `holdout` before reading `family_holdout_spends` drops its own id,
    or every first spend reads as a repeat. Raises `ValueError` when a
    series is given but misses a rebalance session of the window.

    With `tracking=True` only the tracking-window rule applies (module
    docstring): `flags`, `reasons`, `gap_series` and `prior_spends` are not read.

    `lab` is `None` on a store without the lab tables, and then nothing here differs
    from Phase 3; otherwise the variant, spend-gate and family-cap rules apply
    (module docstring, "Strategy-lab rules").

    `boundary` is the development boundary (module docstring); `None` leaves every rule
    as before it existed. Raises `ValueError` when it is before `in_sample_start`.
    """
    _check_boundary(frozen, boundary)
    if lab is not None and lab.is_variant:
        return Decision(
            "refused_variant",
            None,
            "this hypothesis is a sweep variant: variants run only through `sweep run`",
        )
    if tracking:
        return _tracking(window, frozen, cadence)
    refusal = _window_refusal(window, frozen, boundary)
    if refusal is not None:
        return Decision("refused_window", None, refusal)

    if not window_touches_holdout(window, frozen):
        ignored = "; --spend-holdout ignored" if flags.spend_holdout else ""
        return Decision("run", "in_sample", f"in-sample run{ignored}")

    holdout = f"[{frozen.holdout_start}, {frozen.holdout_end}]"
    sessions = gap_sessions(window, cadence)
    if not any(frozen.holdout_start <= s <= frozen.holdout_end for s in sessions):
        return Decision(
            "refused_window",
            None,
            f"window touches the holdout {holdout} but reaches none of its rebalance sessions",
        )
    if not flags.spend_holdout:
        return Decision(
            "refused_holdout",
            None,
            f"window touches the holdout {holdout}; spending it needs --spend-holdout",
        )
    if not _has_text(reasons.holdout_reason):
        return Decision("refused_holdout", None, "--spend-holdout needs a --holdout-reason")
    if lab is not None:
        refusal = _lab_spend_refusal(lab, prior_spends)
        if refusal is not None:
            return Decision("refused_holdout", None, refusal)
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

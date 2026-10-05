"""Gate arithmetic for the research-experiment registry (spec reqs 4 to 8; plan T81a).

Pure: every function takes its inputs explicitly and reads no store, no
clock and no config. `store/research.py` (T81) reads the rows (the
registration, the dataset, the family's holdouts and prior spends, the
amendment chain's counts) and calls these functions to decide what `open_run`,
`register_dataset` and `write_result` do; this module only computes the
decision and names the refusal, mirroring `backtest/holdout.py`'s split
between pure arithmetic and store orchestration.

The gates, in spec order:

1. **Window** (`check_window`, req 4). A bound event span that is not inside
   the registration's `[window.start, window.end]`, or an as-of bound later
   than `close(window.end)`, is `refused_window`.
2. **Split** (`check_split`, req 5 / req 14). A split the registration does
   not list is `refused_split` at open.
3. **Holdout** (`check_holdout`, req 5). A window overlapping a protected
   window of its family, a bound split that is itself sealed, or a bound
   split whose event span overlaps a sealed period of the dataset name, is
   `refused_holdout` unless `--spend-holdout --holdout-reason` is given; a
   spend over a window any prior run has already spent additionally needs
   `--holdout-repeat`.
4. **Budget** (`check_budget`, req 6). Opening a run once the chain's run
   count has reached `budget.runs`, or declaring configurations that would
   push the chain's sum past `budget.configurations`, is `refused_budget`.
5. **Confirmatory basis** (`check_confirmatory`, req 7). A confirmatory
   registration is `predates_dataset` when it precedes the dataset name's
   first version, `sealed_split` when it binds a sealed split or period
   drawn under the registered seed, and otherwise `refused_split` ("dataset
   precedes registration"); a confirmatory `agreement` run additionally
   needs its bound dataset `locked`, else `refused_split` ("label export not
   locked").
6. **Verdict** (`compute_verdict`, req 8). `underpowered` when
   `n_clusters < min_clusters`, regardless of the interval; otherwise `n/a`
   without a threshold, else `pass` when the interval excludes the
   threshold on the registered side and `fail` otherwise. Never the point
   estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from tradepartner.calendar import is_session, previous_session, session_close

WindowOutcome = Literal["ok", "refused_window"]
SplitOutcome = Literal["ok", "refused_split"]
HoldoutOutcome = Literal["ok", "refused_holdout"]
BudgetOutcome = Literal["ok", "refused_budget"]
ConfirmatoryOutcome = Literal["ok", "refused_split"]
ConfirmatoryBasis = Literal["predates_dataset", "sealed_split", "none"]
Direction = Literal["greater", "less"]
Verdict = Literal["pass", "fail", "underpowered", "n/a"]


@dataclass(frozen=True)
class Span:
    """A closed, inclusive date range: a registration window, an event span,
    a hypothesis holdout or a sealed period (reqs 4 and 5 all share this
    shape)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"span end {self.end} is before its start {self.start}")

    def contains(self, other: Span) -> bool:
        """Whether `other` lies entirely inside this span, both ends inclusive."""
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: Span) -> bool:
        """Whether this span shares at least one day with `other`."""
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True)
class Flags:
    """The owner's CLI flags (spec req 14); mirrors `backtest/holdout.py`."""

    spend_holdout: bool = False
    holdout_repeat: bool = False


@dataclass(frozen=True)
class Reasons:
    """The reason that must accompany `spend_holdout`; mirrors `backtest/holdout.py`."""

    holdout_reason: str | None = None


def _has_text(reason: str | None) -> bool:
    return reason is not None and reason.strip() != ""


@dataclass(frozen=True)
class WindowDecision:
    outcome: WindowOutcome
    message: str


def _close_on_or_before(day: date) -> datetime:
    """`close(day)`: the official XNYS close of `day`, or of the last session
    at or before it when `day` itself is not a session (a registration
    window's dates are calendar dates, not necessarily sessions)."""
    session = day if is_session(day) else previous_session(day)
    return session_close(session)


def check_window(bound_span: Span, window: Span, as_of: datetime | None = None) -> WindowDecision:
    """Req 4: the bound dataset's event span must lie inside the
    registration's data window, and an as-of read must not reach later than
    `close(window.end)`. `as_of` is `None` for a kind that never reads
    market or company data (every kind but `economic` and `return`)."""
    if not window.contains(bound_span):
        return WindowDecision(
            "refused_window",
            f"bound event span [{bound_span.start}, {bound_span.end}] is not inside "
            f"the registration window [{window.start}, {window.end}]",
        )
    if as_of is not None:
        bound = _close_on_or_before(window.end)
        if as_of > bound:
            return WindowDecision(
                "refused_window",
                f"as-of bound {as_of} is after close(window.end) {bound}",
            )
    return WindowDecision("ok", "bound event span is inside the registration window")


@dataclass(frozen=True)
class SplitDecision:
    outcome: SplitOutcome
    message: str


def check_split(split: str, allowed_splits: tuple[str, ...]) -> SplitDecision:
    """Req 5 / req 14: a split the registration's `splits` does not list is
    refused at open, before any protected-window check."""
    if split not in allowed_splits:
        return SplitDecision(
            "refused_split",
            f"split {split!r} is not among the registration's splits {allowed_splits}",
        )
    return SplitDecision("ok", "split is among the registration's splits")


@dataclass(frozen=True)
class PriorSpend:
    """A previously recorded spend of a protected window: `label` is
    whatever the caller found it under (a run id, a slug, a backtest trial
    id — for messages only) and `window` is the protected window it spent
    (a family holdout, the bound split's own span when the spend sealed a
    split by name, or a sealed period). The repeat decision below compares
    `window` against what *this* run touches, by `Span.overlaps`, so the
    caller never has to pre-filter by family or dataset name itself."""

    label: str
    window: Span


@dataclass(frozen=True)
class HoldoutDecision:
    outcome: HoldoutOutcome
    message: str
    holdout_spent: bool = False
    holdout_repeat: bool = False
    holdout_reason: str | None = None
    touched: tuple[Span, ...] = ()


def check_holdout(
    window: Span,
    bound_split: str,
    bound_split_span: Span,
    family_holdouts: tuple[Span, ...],
    sealed_splits: tuple[str, ...],
    sealed_periods: tuple[Span, ...],
    flags: Flags,
    reasons: Reasons,
    prior_spends: tuple[PriorSpend, ...],
) -> HoldoutDecision:
    """Req 5: a run whose window overlaps a protected window of its family
    (`family_holdouts`), or that binds a sealed split (`bound_split` is
    `"test"`, which is always sealed, Definitions, Split, or is otherwise in
    `sealed_splits`) or whose bound split's event span overlaps a sealed
    period of the dataset name (`sealed_periods`), is refused unless the
    flags are given. A former `test` row relabelled into another split
    still trips the check, because `bound_split_span` is checked against
    `sealed_periods` whatever the split's current label.

    `decision.touched` names exactly the protected windows this run
    touched (the overlapped family holdouts, the bound split's own span
    when sealed by name, the overlapped sealed periods), so the caller can
    record "the window, split or period spent" (req 5) without redoing the
    overlap arithmetic. The spend is a repeat when any `prior_spends` entry
    overlaps one of `touched` (`prior_spends` is every prior spend the
    caller found: any backtest `holdout` trial of the family, any research
    run of the family, any research run on this dataset name, synthetic
    excluded, this run's own id dropped); a repeat additionally needs
    `--holdout-repeat`."""
    touched_holdouts = tuple(holdout for holdout in family_holdouts if window.overlaps(holdout))
    split_sealed_by_name = bound_split == "test" or bound_split in sealed_splits
    touched_periods = tuple(
        period for period in sealed_periods if bound_split_span.overlaps(period)
    )
    touched = (
        touched_holdouts + ((bound_split_span,) if split_sealed_by_name else ()) + touched_periods
    )
    if not touched:
        return HoldoutDecision("ok", "no protected window touched")
    if not flags.spend_holdout:
        return HoldoutDecision(
            "refused_holdout",
            "touches a protected window; spending it needs --spend-holdout",
            touched=touched,
        )
    if not _has_text(reasons.holdout_reason):
        return HoldoutDecision(
            "refused_holdout", "--spend-holdout needs a --holdout-reason", touched=touched
        )
    repeats = tuple(
        spend for spend in prior_spends if any(spend.window.overlaps(t) for t in touched)
    )
    if repeats and not flags.holdout_repeat:
        spent_by = ", ".join(spend.label for spend in repeats)
        return HoldoutDecision(
            "refused_holdout",
            f"already spent ({spent_by}); a repeat needs --holdout-repeat",
            touched=touched,
        )
    return HoldoutDecision(
        "ok",
        "holdout spend recorded",
        holdout_spent=True,
        holdout_repeat=bool(repeats),
        holdout_reason=reasons.holdout_reason,
        touched=touched,
    )


@dataclass(frozen=True)
class BudgetDecision:
    outcome: BudgetOutcome
    message: str


def check_budget(
    chain_run_count: int,
    budget_runs: int,
    chain_configurations: int,
    configurations_declared: int,
    budget_configurations: int,
) -> BudgetDecision:
    """Req 6: `chain_run_count` and `chain_configurations` are the amendment
    chain's totals so far (every run row, failures and refusals included,
    synthetic excluded), read under the latest registration's budget, not
    counting the run being opened. Opening is refused once that count has
    already reached `budget.runs` (so the `budget.runs`-th run opens and
    the next is refused); declaring configurations that would push the
    chain's sum past `budget.configurations` is refused before any write."""
    if chain_run_count >= budget_runs:
        return BudgetDecision(
            "refused_budget",
            f"chain run count {chain_run_count} has reached budget.runs {budget_runs}",
        )
    total_configurations = chain_configurations + configurations_declared
    if total_configurations > budget_configurations:
        return BudgetDecision(
            "refused_budget",
            f"declaring {configurations_declared} configurations would push the chain's "
            f"sum to {total_configurations}, over budget.configurations {budget_configurations}",
        )
    return BudgetDecision("ok", "within budget")


@dataclass(frozen=True)
class ConfirmatoryDecision:
    outcome: ConfirmatoryOutcome
    message: str
    basis: ConfirmatoryBasis = "none"


def check_confirmatory(
    kind: str,
    confirmatory: bool,
    registration_known_at: datetime,
    dataset_first_known_at: datetime,
    bound_split_sealed: bool,
    dataset_locked: bool,
) -> ConfirmatoryDecision:
    """Req 7: an exploratory registration (`confirmatory=False`) always opens
    with basis `none`. A confirmatory `agreement` run needs its bound
    dataset `locked` (protocol §10 step 5), refused otherwise with "label
    export not locked" whatever its basis would be. Otherwise the basis is
    `predates_dataset` when `registration_known_at` precedes the bound
    dataset name's first `known_at` (re-hashing an export makes a new
    version, never an earlier name), else `sealed_split` when the run binds
    a sealed split or sealed period (`bound_split_sealed`, computed by the
    caller from the dataset's sealed splits/periods and the registered
    seed), else refused with "dataset precedes registration"."""
    if not confirmatory:
        return ConfirmatoryDecision("ok", "exploratory run", basis="none")
    if kind == "agreement" and not dataset_locked:
        return ConfirmatoryDecision("refused_split", "label export not locked")
    if registration_known_at < dataset_first_known_at:
        return ConfirmatoryDecision(
            "ok",
            "registration predates the bound dataset name's first version",
            basis="predates_dataset",
        )
    if bound_split_sealed:
        return ConfirmatoryDecision(
            "ok",
            "run binds a sealed split or period drawn under the registered seed",
            basis="sealed_split",
        )
    return ConfirmatoryDecision("refused_split", "dataset precedes registration")


def compute_verdict(
    n_clusters: int,
    min_clusters: int,
    direction: Direction,
    threshold: float | None,
    ci_low: float,
    ci_high: float,
) -> Verdict:
    """Req 8: computed from the interval, never the point estimate.
    `underpowered` beats every other outcome, including a threshold the
    interval would otherwise clear. `direction = less` passes when the
    interval's upper bound is strictly below the threshold; `greater` passes
    when its lower bound is strictly above. No threshold is always `n/a`
    once the cluster floor is met."""
    if n_clusters < min_clusters:
        return "underpowered"
    if threshold is None:
        return "n/a"
    if direction == "less":
        return "pass" if ci_high < threshold else "fail"
    return "pass" if ci_low > threshold else "fail"

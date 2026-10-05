"""Gate arithmetic (research-registry spec reqs 4 to 8; plan T81a)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradepartner.calendar import previous_session, session_close
from tradepartner.research.gates import (
    BudgetDecision,
    ConfirmatoryDecision,
    Flags,
    HoldoutDecision,
    PriorSpend,
    Reasons,
    Span,
    SplitDecision,
    WindowDecision,
    check_budget,
    check_confirmatory,
    check_holdout,
    check_split,
    check_window,
    compute_verdict,
    is_split_sealed,
)

NO_FLAGS = Flags()
NO_REASONS = Reasons()
SPEND = Flags(spend_holdout=True)
SPEND_REPEAT = Flags(spend_holdout=True, holdout_repeat=True)
SPEND_REASON = Reasons(holdout_reason="scoring the sealed test for E1-H")


# --- Span ----------------------------------------------------------------


def test_span_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="before its start"):
        Span(date(2024, 6, 1), date(2024, 1, 1))


def test_span_contains_is_inclusive_both_ends() -> None:
    outer = Span(date(2024, 1, 1), date(2024, 12, 31))
    assert outer.contains(Span(date(2024, 1, 1), date(2024, 12, 31)))
    assert not outer.contains(Span(date(2023, 12, 31), date(2024, 12, 31)))
    assert not outer.contains(Span(date(2024, 1, 1), date(2025, 1, 1)))


def test_span_overlaps_is_symmetric_and_inclusive() -> None:
    a = Span(date(2024, 1, 1), date(2024, 3, 31))
    b = Span(date(2024, 3, 31), date(2024, 6, 30))
    c = Span(date(2024, 4, 1), date(2024, 6, 30))
    assert a.overlaps(b) and b.overlaps(a)
    assert not a.overlaps(c) and not c.overlaps(a)


# --- check_window (req 4) -------------------------------------------------


# 2023-12-31 is a Sunday: the window ends on the last XNYS session of the year.
WINDOW = Span(date(2017, 1, 1), date(2023, 12, 29))


def test_window_ok_when_event_span_is_inside_and_no_as_of_bound() -> None:
    decision = check_window(Span(date(2018, 1, 1), date(2019, 1, 1)), WINDOW)
    assert decision == WindowDecision("ok", decision.message)
    assert decision.outcome == "ok"


def test_window_refused_when_event_span_starts_before_window() -> None:
    decision = check_window(Span(date(2016, 12, 31), date(2018, 1, 1)), WINDOW)
    assert decision.outcome == "refused_window"
    assert "is not inside" in decision.message


def test_window_refused_when_event_span_ends_after_window() -> None:
    decision = check_window(Span(date(2018, 1, 1), date(2024, 1, 1)), WINDOW)
    assert decision.outcome == "refused_window"


def test_window_ok_when_as_of_bound_is_at_close_of_window_end() -> None:
    bound = session_close(WINDOW.end)
    decision = check_window(Span(date(2018, 1, 1), date(2019, 1, 1)), WINDOW, as_of=bound)
    assert decision.outcome == "ok"


def test_window_refused_when_as_of_bound_is_after_close_of_window_end() -> None:
    bound = session_close(WINDOW.end)
    after = bound + timedelta(seconds=1)
    decision = check_window(Span(date(2018, 1, 1), date(2019, 1, 1)), WINDOW, as_of=after)
    assert decision.outcome == "refused_window"
    assert "after close(window.end)" in decision.message


def test_window_end_on_a_non_session_date_bounds_at_the_prior_session_close() -> None:
    # 2023-12-31 is a Sunday, a legitimate calendar-date registration window end (req 2
    # places no session constraint on window.end); the as-of bound falls back to the
    # close of the last session at or before it rather than raising.
    weekend_window = Span(date(2017, 1, 1), date(2023, 12, 31))
    bound = session_close(previous_session(weekend_window.end))
    ok = check_window(Span(date(2018, 1, 1), date(2019, 1, 1)), weekend_window, as_of=bound)
    assert ok.outcome == "ok"
    refused = check_window(
        Span(date(2018, 1, 1), date(2019, 1, 1)),
        weekend_window,
        as_of=bound + timedelta(seconds=1),
    )
    assert refused.outcome == "refused_window"


# --- check_split (req 5 / req 14) -----------------------------------------


def test_split_ok_when_listed() -> None:
    assert check_split("dev", ("dev", "test")) == SplitDecision(
        "ok", "split is among the registration's splits"
    )


def test_split_refused_when_not_listed() -> None:
    decision = check_split("prospective", ("dev", "test"))
    assert decision.outcome == "refused_split"
    assert "'prospective'" in decision.message


# --- check_holdout (req 5) -------------------------------------------------


FAMILY_HOLDOUT = Span(date(2024, 1, 1), date(2024, 12, 31))
SEALED_PERIOD = Span(date(2024, 1, 1), date(2024, 6, 30))
UNTOUCHED = Span(date(2023, 1, 1), date(2023, 6, 30))


def _holdout(
    window: Span = UNTOUCHED,
    split: str = "dev",
    split_span: Span = UNTOUCHED,
    family_holdouts: tuple[Span, ...] = (),
    sealed_splits: tuple[str, ...] = (),
    sealed_periods: tuple[Span, ...] = (),
    flags: Flags = NO_FLAGS,
    reasons: Reasons = NO_REASONS,
    prior_spends: tuple[PriorSpend, ...] = (),
    sealed_split_already_spent: bool = False,
) -> HoldoutDecision:
    return check_holdout(
        window,
        split,
        split_span,
        family_holdouts,
        sealed_splits,
        sealed_periods,
        flags,
        reasons,
        prior_spends,
        sealed_split_already_spent,
    )


def test_dev_run_outside_sealed_period_opens_without_flags() -> None:
    decision = _holdout(
        split="dev",
        split_span=Span(date(2024, 7, 1), date(2024, 12, 31)),
        sealed_splits=("test",),
        sealed_periods=(SEALED_PERIOD,),
    )
    assert decision == HoldoutDecision("ok", decision.message)
    assert decision.holdout_spent is False


def test_relabelled_test_row_inside_sealed_period_is_a_spend() -> None:
    # Former `test` rows relabelled `dev`, lying inside the sealed period, still trip the
    # check because the bound split's event span (not its label) is checked against the
    # sealed periods.
    decision = _holdout(
        split="dev",
        split_span=Span(date(2024, 3, 1), date(2024, 4, 1)),
        sealed_splits=("test",),
        sealed_periods=(SEALED_PERIOD,),
    )
    assert decision.outcome == "refused_holdout"
    spent = _holdout(
        split="dev",
        split_span=Span(date(2024, 3, 1), date(2024, 4, 1)),
        sealed_splits=("test",),
        sealed_periods=(SEALED_PERIOD,),
        flags=SPEND,
        reasons=SPEND_REASON,
    )
    assert spent.outcome == "ok"
    assert spent.holdout_spent is True
    assert spent.holdout_repeat is False


def test_is_split_sealed_test_is_always_sealed() -> None:
    assert is_split_sealed("test", ()) is True
    assert is_split_sealed("dev", ()) is False
    assert is_split_sealed("dev", ("dev",)) is True


def test_test_split_is_sealed_even_when_sealed_splits_omits_it() -> None:
    # Definitions, Split: "`test` is always sealed" — the dataset row's own sealed-set
    # column need not list it for the gate to protect it.
    decision = _holdout(split="test", sealed_splits=())
    assert decision.outcome == "refused_holdout"
    assert decision.touches_sealed_split is True
    spent = _holdout(split="test", sealed_splits=(), flags=SPEND, reasons=SPEND_REASON)
    assert spent.outcome == "ok"
    assert spent.holdout_spent is True


def test_first_test_scoring_is_the_spend_and_second_is_a_repeat() -> None:
    first = _holdout(split="test", sealed_splits=("test",), flags=SPEND, reasons=SPEND_REASON)
    assert first.outcome == "ok"
    assert first.holdout_spent is True
    assert first.holdout_repeat is False
    assert first.touches_sealed_split is True

    second_unflagged = _holdout(
        split="test",
        sealed_splits=("test",),
        flags=SPEND,
        reasons=SPEND_REASON,
        sealed_split_already_spent=True,
    )
    assert second_unflagged.outcome == "refused_holdout"
    assert "a repeat needs --holdout-repeat" in second_unflagged.message

    second_flagged = _holdout(
        split="test",
        sealed_splits=("test",),
        flags=SPEND_REPEAT,
        reasons=SPEND_REASON,
        sealed_split_already_spent=True,
    )
    assert second_flagged.outcome == "ok"
    assert second_flagged.holdout_spent is True
    assert second_flagged.holdout_repeat is True


def test_test_redrawn_to_new_dates_in_a_later_version_is_still_a_repeat() -> None:
    # Regression (code-review, post quant-auditor pass 2): the sealed split is
    # protected by the dataset NAME (Definitions, Protected window (b): "sealing
    # persists across versions"), not by any one version's dates. A dataset version
    # that redraws which rows carry `test` must not look like a fresh, unspent split
    # just because this run's bound_split_span differs from the version that was
    # scored first. The caller signals this directly with `sealed_split_already_spent`
    # rather than through `prior_spends` (which only matches by date overlap, and a
    # redrawn split need not overlap the old one at all).
    v1_test_span = Span(date(2020, 1, 1), date(2020, 6, 30))
    v2_test_span = Span(date(2021, 1, 1), date(2021, 6, 30))
    assert not v1_test_span.overlaps(v2_test_span)

    repeat = check_holdout(
        window=UNTOUCHED,
        bound_split="test",
        bound_split_span=v2_test_span,
        family_holdouts=(),
        sealed_splits=("test",),
        sealed_periods=(),
        flags=SPEND_REPEAT,
        reasons=SPEND_REASON,
        prior_spends=(PriorSpend("run 1 (v1 test)", v1_test_span),),
        sealed_split_already_spent=True,
    )
    assert repeat.outcome == "ok"
    assert repeat.holdout_repeat is True

    refused_without_repeat_flag = check_holdout(
        window=UNTOUCHED,
        bound_split="test",
        bound_split_span=v2_test_span,
        family_holdouts=(),
        sealed_splits=("test",),
        sealed_periods=(),
        flags=SPEND,
        reasons=SPEND_REASON,
        prior_spends=(PriorSpend("run 1 (v1 test)", v1_test_span),),
        sealed_split_already_spent=True,
    )
    assert refused_without_repeat_flag.outcome == "refused_holdout"


def test_repeat_across_slugs_and_dataset_names_is_still_a_repeat() -> None:
    # `prior_spends` is whatever the caller found: another slug's run, a run on a
    # different dataset name bound to the same family window, or a backtest holdout
    # trial. The gate matches by window overlap, not by identity.
    decision = _holdout(
        window=FAMILY_HOLDOUT,
        family_holdouts=(FAMILY_HOLDOUT,),
        flags=SPEND_REPEAT,
        reasons=SPEND_REASON,
        prior_spends=(
            PriorSpend("other-slug run 4", FAMILY_HOLDOUT),
            PriorSpend("trial 9 (backtest holdout)", FAMILY_HOLDOUT),
        ),
    )
    assert decision.outcome == "ok"
    assert decision.holdout_repeat is True

    refused = _holdout(
        window=FAMILY_HOLDOUT,
        family_holdouts=(FAMILY_HOLDOUT,),
        flags=SPEND,
        reasons=SPEND_REASON,
        prior_spends=(PriorSpend("other-slug run 4", FAMILY_HOLDOUT),),
    )
    assert refused.outcome == "refused_holdout"


def test_repeat_is_decided_per_touched_window_not_by_any_prior_spend() -> None:
    other_family_holdout = Span(date(2021, 1, 1), date(2021, 12, 31))
    # This run touches only `FAMILY_HOLDOUT`; a prior spend of the *other* protected
    # window must not force a repeat here.
    not_a_repeat = _holdout(
        window=FAMILY_HOLDOUT,
        family_holdouts=(FAMILY_HOLDOUT, other_family_holdout),
        flags=SPEND,
        reasons=SPEND_REASON,
        prior_spends=(PriorSpend("unrelated run 2", other_family_holdout),),
    )
    assert not_a_repeat.outcome == "ok"
    assert not_a_repeat.holdout_repeat is False

    is_a_repeat = _holdout(
        window=FAMILY_HOLDOUT,
        family_holdouts=(FAMILY_HOLDOUT, other_family_holdout),
        flags=SPEND_REPEAT,
        reasons=SPEND_REASON,
        prior_spends=(
            PriorSpend("unrelated run 2", other_family_holdout),
            PriorSpend("run 3", FAMILY_HOLDOUT),
        ),
    )
    assert is_a_repeat.outcome == "ok"
    assert is_a_repeat.holdout_repeat is True


def test_window_overlapping_family_holdout_is_refused_without_flags() -> None:
    decision = _holdout(window=FAMILY_HOLDOUT, family_holdouts=(FAMILY_HOLDOUT,))
    assert decision.outcome == "refused_holdout"
    assert "needs --spend-holdout" in decision.message


def test_spend_holdout_without_reason_is_refused() -> None:
    decision = _holdout(window=FAMILY_HOLDOUT, family_holdouts=(FAMILY_HOLDOUT,), flags=SPEND)
    assert decision.outcome == "refused_holdout"
    assert "--holdout-reason" in decision.message


def test_spend_holdout_with_blank_reason_is_refused() -> None:
    decision = _holdout(
        window=FAMILY_HOLDOUT,
        family_holdouts=(FAMILY_HOLDOUT,),
        flags=SPEND,
        reasons=Reasons(holdout_reason="   "),
    )
    assert decision.outcome == "refused_holdout"


def test_window_not_touching_any_protected_window_ignores_flags() -> None:
    decision = _holdout(window=Span(date(2020, 1, 1), date(2020, 6, 30)), flags=SPEND)
    assert decision.outcome == "ok"
    assert decision.holdout_spent is False


def test_touched_is_deduplicated_when_a_sealed_period_equals_a_family_holdout() -> None:
    # A coincidence (the same dates registered as both a family holdout and a sealed
    # period) must not double-record one window.
    shared = Span(date(2024, 2, 1), date(2024, 2, 29))
    decision = check_holdout(
        window=shared,
        bound_split="dev",
        bound_split_span=shared,
        family_holdouts=(shared,),
        sealed_splits=(),
        sealed_periods=(shared,),
        flags=SPEND,
        reasons=SPEND_REASON,
        prior_spends=(),
    )
    assert decision.outcome == "ok"
    assert decision.touched == (shared,)


# --- check_budget (req 6) -------------------------------------------------


def test_budget_ok_below_run_count_and_configurations() -> None:
    decision = check_budget(
        chain_run_count=1,
        budget_runs=3,
        chain_configurations=1,
        configurations_declared=1,
        budget_configurations=5,
    )
    assert decision == BudgetDecision("ok", decision.message)


def test_budget_opens_the_budget_runs_th_run() -> None:
    # One below the limit: the chain so far has run count `budget_runs - 1`, so this
    # run (the `budget.runs`-th) opens; the one after it is refused (next test).
    decision = check_budget(
        chain_run_count=2,
        budget_runs=3,
        chain_configurations=0,
        configurations_declared=1,
        budget_configurations=10,
    )
    assert decision.outcome == "ok"


def test_budget_refused_at_the_run_count() -> None:
    decision = check_budget(
        chain_run_count=3,
        budget_runs=3,
        chain_configurations=0,
        configurations_declared=1,
        budget_configurations=10,
    )
    assert decision.outcome == "refused_budget"
    assert "budget.runs 3" in decision.message


def test_budget_refused_past_the_run_count() -> None:
    decision = check_budget(
        chain_run_count=5,
        budget_runs=3,
        chain_configurations=0,
        configurations_declared=1,
        budget_configurations=10,
    )
    assert decision.outcome == "refused_budget"


def test_budget_refused_when_declared_configurations_would_exceed_budget() -> None:
    decision = check_budget(
        chain_run_count=0,
        budget_runs=3,
        chain_configurations=4,
        configurations_declared=2,
        budget_configurations=5,
    )
    assert decision.outcome == "refused_budget"
    assert "over budget.configurations 5" in decision.message


def test_budget_ok_when_declared_configurations_exactly_meet_budget() -> None:
    decision = check_budget(
        chain_run_count=0,
        budget_runs=3,
        chain_configurations=4,
        configurations_declared=1,
        budget_configurations=5,
    )
    assert decision.outcome == "ok"


# --- check_confirmatory (req 7) -------------------------------------------


REGISTERED = datetime(2024, 1, 1, tzinfo=UTC)
EARLIER = datetime(2023, 1, 1, tzinfo=UTC)
LATER = datetime(2025, 1, 1, tzinfo=UTC)


def test_exploratory_run_is_always_basis_none() -> None:
    decision = check_confirmatory(
        kind="return",
        confirmatory=False,
        registration_known_at=LATER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=False,
        dataset_locked=False,
    )
    assert decision == ConfirmatoryDecision("ok", decision.message, basis="none")


def test_predates_dataset_when_registration_is_first() -> None:
    decision = check_confirmatory(
        kind="economic",
        confirmatory=True,
        registration_known_at=EARLIER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=False,
        dataset_locked=False,
    )
    assert decision.outcome == "ok"
    assert decision.basis == "predates_dataset"


def test_sealed_split_basis_when_registration_is_not_first_but_split_is_sealed() -> None:
    decision = check_confirmatory(
        kind="benchmark",
        confirmatory=True,
        registration_known_at=LATER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=True,
        dataset_locked=False,
    )
    assert decision.outcome == "ok"
    assert decision.basis == "sealed_split"


def test_predates_dataset_preferred_over_sealed_split_when_both_hold() -> None:
    decision = check_confirmatory(
        kind="benchmark",
        confirmatory=True,
        registration_known_at=EARLIER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=True,
        dataset_locked=False,
    )
    assert decision.basis == "predates_dataset"


def test_refused_dataset_precedes_registration_when_neither_holds() -> None:
    decision = check_confirmatory(
        kind="benchmark",
        confirmatory=True,
        registration_known_at=LATER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=False,
        dataset_locked=False,
    )
    assert decision.outcome == "refused_split"
    assert decision.message == "dataset precedes registration"
    assert decision.basis == "none"


def test_label_export_not_locked_refuses_confirmatory_agreement_run() -> None:
    decision = check_confirmatory(
        kind="agreement",
        confirmatory=True,
        registration_known_at=EARLIER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=True,
        dataset_locked=False,
    )
    assert decision.outcome == "refused_split"
    assert decision.message == "label export not locked"


def test_locked_agreement_run_still_needs_predates_or_sealed_split() -> None:
    decision = check_confirmatory(
        kind="agreement",
        confirmatory=True,
        registration_known_at=EARLIER,
        dataset_first_known_at=REGISTERED,
        bound_split_sealed=False,
        dataset_locked=True,
    )
    assert decision.outcome == "ok"
    assert decision.basis == "predates_dataset"


# --- compute_verdict (req 8) ----------------------------------------------


def test_underpowered_beats_an_interval_that_would_otherwise_pass() -> None:
    verdict = compute_verdict(
        n_clusters=10,
        min_clusters=30,
        direction="greater",
        threshold=0.667,
        ci_low=0.9,
        ci_high=0.95,
    )
    assert verdict == "underpowered"


def test_direction_less_passes_when_upper_bound_is_strictly_below_threshold() -> None:
    assert (
        compute_verdict(
            n_clusters=50,
            min_clusters=30,
            direction="less",
            threshold=0.0,
            ci_low=-0.2,
            ci_high=-0.05,
        )
        == "pass"
    )


def test_direction_less_fails_when_upper_bound_meets_or_exceeds_threshold() -> None:
    assert (
        compute_verdict(
            n_clusters=50,
            min_clusters=30,
            direction="less",
            threshold=0.0,
            ci_low=-0.2,
            ci_high=0.0,
        )
        == "fail"
    )
    # Fails even when the point estimate is below the threshold (req 8: never the point
    # estimate).
    assert (
        compute_verdict(
            n_clusters=50,
            min_clusters=30,
            direction="less",
            threshold=0.0,
            ci_low=-0.2,
            ci_high=0.05,
        )
        == "fail"
    )


def test_direction_greater_passes_when_lower_bound_is_strictly_above_threshold() -> None:
    assert (
        compute_verdict(
            n_clusters=150,
            min_clusters=100,
            direction="greater",
            threshold=0.667,
            ci_low=0.70,
            ci_high=0.80,
        )
        == "pass"
    )


def test_direction_greater_fails_when_lower_bound_meets_or_is_below_threshold() -> None:
    assert (
        compute_verdict(
            n_clusters=150,
            min_clusters=100,
            direction="greater",
            threshold=0.667,
            ci_low=0.667,
            ci_high=0.80,
        )
        == "fail"
    )
    assert (
        compute_verdict(
            n_clusters=150,
            min_clusters=100,
            direction="greater",
            threshold=0.667,
            ci_low=0.5,
            ci_high=0.80,
        )
        == "fail"
    )


def test_verdict_is_n_a_without_a_threshold() -> None:
    assert (
        compute_verdict(
            n_clusters=150,
            min_clusters=100,
            direction="greater",
            threshold=None,
            ci_low=0.1,
            ci_high=0.9,
        )
        == "n/a"
    )


def test_verdict_n_a_still_yields_to_underpowered() -> None:
    assert (
        compute_verdict(
            n_clusters=5,
            min_clusters=100,
            direction="greater",
            threshold=None,
            ci_low=0.1,
            ci_high=0.9,
        )
        == "underpowered"
    )

"""Tests for `tradepartner.research.labeling.scoring` (plan T121b).

Research-labeling spec req 10 and its 2026-10-06 amendment (C5, C6, Q6).
Every statistical bound is checked against a value worked independently of
this module's own implementation: the Wilson bound by its closed form (by
hand, in the module's own docstring derivation, reproduced in the test
comments below); the Clopper-Pearson bound by `scipy.stats.beta.ppf`, run
once outside this repository, and by its two closed forms (all-successes,
`alpha ** (1 / n)`; all-failures, the lower bound is always `0.0`). `scipy`
is not a dependency of this project and is not imported here or anywhere in
`src/`; the numbers below are copied in, not computed by a test-time import.
"""

from __future__ import annotations

import math

import pytest

from tradepartner.research.labeling import crosswalk, scoring
from tradepartner.research.labeling.questions import UNRESOLVED


def _item(
    listing_end_id: str,
    gold_label: str | None,
    predicted_option: str | None,
    reason: scoring.Reason,
    **kwargs: object,
) -> scoring.ScoredItem:
    """A `ScoredItem` whose labelled gold rests on `text` unless the test says
    otherwise (#1492: a labelled item needs a basis; the tests written before it
    score the text set)."""
    if gold_label not in (None, UNRESOLVED):
        kwargs.setdefault("basis", "text")
    return scoring.ScoredItem(
        listing_end_id,
        gold_label,
        predicted_option,
        reason,
        **kwargs,  # type: ignore[arg-type]
    )


# --- wilson_lower_bound --------------------------------------------------------


def test_wilson_lower_bound_n_zero_is_zero() -> None:
    assert scoring.wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_rejects_other_confidence_levels() -> None:
    with pytest.raises(ValueError):
        scoring.wilson_lower_bound(10, 20, confidence=0.90)


@pytest.mark.parametrize(
    ("successes", "n", "expected"),
    [
        (102, 120, 0.789),  # req 10's worked example: 0.85 observed, fails the 0.80 bar
        (104, 120, 0.807),  # the smallest passing count
        (108, 120, 0.846),  # 0.90 observed
    ],
)
def test_wilson_lower_bound_req10_worked_examples(successes: int, n: int, expected: float) -> None:
    assert math.isclose(scoring.wilson_lower_bound(successes, n), expected, abs_tol=1e-3)


# --- clopper_pearson_lower_bound -----------------------------------------------


def test_clopper_pearson_zero_successes_or_zero_n_is_zero() -> None:
    assert scoring.clopper_pearson_lower_bound(0, 20) == 0.0
    assert scoring.clopper_pearson_lower_bound(0, 0) == 0.0


@pytest.mark.parametrize(
    ("successes", "n", "expected"),
    [
        # scipy.stats.beta.ppf(0.05, successes, n - successes + 1), run once outside
        # this repository (scipy is not a project dependency).
        (5, 20, 0.1040808359101361),
        (3, 15, 0.05684686759024681),
        (8, 20, 0.21706858937007417),
    ],
)
def test_clopper_pearson_against_the_scipy_oracle(successes: int, n: int, expected: float) -> None:
    assert math.isclose(scoring.clopper_pearson_lower_bound(successes, n), expected, abs_tol=1e-9)


@pytest.mark.parametrize("n", [1, 5, 10, 20, 50])
def test_clopper_pearson_all_successes_matches_the_closed_form(n: int) -> None:
    """When every trial succeeds (`a = n`, `b = 1`), `Beta(n, 1)`'s CDF is
    `p ** n`, so the one-sided 95% lower bound solves `p ** n = 0.05`:
    `p = 0.05 ** (1 / n)`, independent of this module's incomplete-beta
    implementation."""
    expected = 0.05 ** (1 / n)
    assert math.isclose(scoring.clopper_pearson_lower_bound(n, n), expected, abs_tol=1e-9)


def test_betainc_symmetry() -> None:
    """`I_x(a, b) = 1 - I_{1-x}(b, a)` for the regularized incomplete beta
    function, an identity independent of this implementation's own code
    path (the two sides take different branches of `_betainc`)."""
    for a, b, x in [(3.0, 5.0, 0.4), (1.0, 1.0, 0.5), (10.0, 2.0, 0.9), (2.0, 10.0, 0.1)]:
        left = scoring._betainc(a, b, x)
        right = 1.0 - scoring._betainc(b, a, 1 - x)
        assert math.isclose(left, right, abs_tol=1e-9)


# --- class_accuracy -------------------------------------------------------------


def _labelled_items(
    n_correct: int,
    n_wrong_option: int,
    n_timeout: int,
    n_unresolved: int,
    *,
    extra_unlabelled: int = 0,
    extra_gold_unresolved: int = 0,
) -> list[scoring.ScoredItem]:
    """`n_correct` items whose model class matches `gold_label`'s class
    (`bankruptcy`, the `insolvency` class); the rest wrong by three routes:
    a different-class option (`exchange_transfer`), a `timeout` (no
    answer) and a model `unresolved` on a labelled item -- all three must
    score wrong (req 10), never excluded."""
    items = []
    for i in range(n_correct):
        items.append(_item(f"correct-{i}", "bankruptcy", "bankruptcy", "ok"))
    for i in range(n_wrong_option):
        items.append(_item(f"wrong-option-{i}", "bankruptcy", "exchange_transfer", "ok"))
    for i in range(n_timeout):
        items.append(_item(f"timeout-{i}", "bankruptcy", None, "timeout"))
    for i in range(n_unresolved):
        items.append(_item(f"unresolved-{i}", "bankruptcy", UNRESOLVED, "ok"))
    for i in range(extra_unlabelled):
        items.append(_item(f"unlabelled-{i}", None, "bankruptcy", "ok"))
    for i in range(extra_gold_unresolved):
        items.append(_item(f"gold-unresolved-{i}", UNRESOLVED, "bankruptcy", "ok"))
    return items


def test_class_accuracy_102_of_120() -> None:
    items = _labelled_items(n_correct=102, n_wrong_option=10, n_timeout=4, n_unresolved=4)
    result = scoring.class_accuracy(items)
    assert result.n == 120
    assert result.correct == 102
    assert math.isclose(result.point_estimate, 0.85, abs_tol=1e-9)
    assert math.isclose(result.lower_bound, 0.789, abs_tol=1e-3)
    assert result.lower_bound < 0.80  # fails the bar


def test_class_accuracy_104_of_120() -> None:
    items = _labelled_items(n_correct=104, n_wrong_option=10, n_timeout=3, n_unresolved=3)
    result = scoring.class_accuracy(items)
    assert result.n == 120
    assert result.correct == 104
    assert math.isclose(result.lower_bound, 0.807, abs_tol=1e-3)
    assert result.lower_bound >= 0.80  # the smallest passing count


def test_class_accuracy_108_of_120() -> None:
    items = _labelled_items(n_correct=108, n_wrong_option=6, n_timeout=3, n_unresolved=3)
    result = scoring.class_accuracy(items)
    assert result.n == 120
    assert result.correct == 108
    assert math.isclose(result.lower_bound, 0.846, abs_tol=1e-3)
    assert result.lower_bound >= 0.80


def test_class_accuracy_excludes_unlabelled_and_gold_unresolved() -> None:
    scored = _labelled_items(n_correct=104, n_wrong_option=10, n_timeout=3, n_unresolved=3)
    padded = scored + _labelled_items(
        n_correct=0,
        n_wrong_option=0,
        n_timeout=0,
        n_unresolved=0,
        extra_unlabelled=7,
        extra_gold_unresolved=5,
    )
    assert len(padded) == 120 + 12
    result = scoring.class_accuracy(padded)
    assert result.n == 120  # the 12 extras never enter the denominator
    assert result.correct == 104


def test_class_accuracy_against_text_states() -> None:
    item_matches = _item(
        "a", "bankruptcy", "bankruptcy", "ok", text_states=None
    )  # text_states defaults to gold_label (Q6)
    item_differs = _item(
        "b", "bankruptcy", "exchange_transfer", "ok", text_states="compliance_delisting"
    )
    result_gold = scoring.class_accuracy([item_matches, item_differs], against="gold_label")
    result_text = scoring.class_accuracy([item_matches, item_differs], against="text_states")
    assert result_gold.n == 2
    assert result_gold.correct == 1  # item_differs is wrong against gold_label too
    assert result_text.n == 2
    # item_matches: text_states defaults to gold_label, so it's still correct;
    # item_differs: exchange_transfer's class != compliance_delisting's class, wrong.
    assert result_text.correct == 1


def test_worst_case_lower_bound_counts_unlabelled_as_wrong() -> None:
    scored = _labelled_items(n_correct=108, n_wrong_option=6, n_timeout=3, n_unresolved=3)
    unlabelled = [_item(f"u-{i}", None, "bankruptcy", "ok") for i in range(5)]
    items = scored + unlabelled

    ordinary = scoring.class_accuracy(items)
    worst_case = scoring.worst_case_lower_bound(items)

    assert ordinary.n == 120  # unlabelled excluded from the ordinary bar
    assert worst_case == scoring.wilson_lower_bound(108, 125)  # the same 108, denominator +5
    assert worst_case < ordinary.lower_bound


def test_worst_case_lower_bound_excludes_a_seed_not_in_the_random_draw() -> None:
    """The worst-case bound is reported beside `class_accuracy` and the
    `TP-` claim quotes both (quant-auditor SHOULD FIX on PR #1069, pass 2):
    a seed added beside the 150 drawn items must not change either bound's
    denominator."""
    drawn = _labelled_items(n_correct=10, n_wrong_option=0, n_timeout=0, n_unresolved=0)
    without_seed = scoring.worst_case_lower_bound(drawn)

    extra_seed = _item(
        "extra-seed", "bankruptcy", "exchange_transfer", "ok", is_seed=True, in_random_draw=False
    )
    with_seed = scoring.worst_case_lower_bound([*drawn, extra_seed])

    assert with_seed == without_seed


# --- in_random_draw: excluded from the accuracy denominator, not from seeded_recall --


def test_in_random_draw_true_seed_counts_in_both_class_accuracy_and_seeded_recall() -> None:
    """A seed the random draw itself picked keeps `in_random_draw=True`
    (its default) and must count toward both `class_accuracy` and
    `seeded_recall` (req 10's exception: "except a seed the random draw
    itself picked")."""
    row6 = crosswalk.RuleAnswer(status="listed")  # a disagreement by construction
    drawn_seed = _item(
        "drawn-seed", "bankruptcy", "bankruptcy", "ok", is_seed=True, pre_fix_rule_answer=row6
    )
    items = [
        *_labelled_items(n_correct=9, n_wrong_option=0, n_timeout=0, n_unresolved=0),
        drawn_seed,
    ]

    accuracy = scoring.class_accuracy(items)
    assert accuracy.n == 10  # the drawn seed enters the denominator
    assert accuracy.correct == 10

    assert scoring.seeded_recall(items) == 1.0  # row6 disagrees with every non-unresolved option


def test_scored_item_rejects_in_random_draw_false_on_a_non_seed() -> None:
    with pytest.raises(ValueError):
        _item("x", "bankruptcy", "bankruptcy", "ok", in_random_draw=False)


def test_seed_not_in_random_draw_changes_seeded_recall_not_class_accuracy() -> None:
    """req 10: a seed added beside the 150 drawn items (`in_random_draw=
    False`) is excluded from the accuracy denominator but still counts
    toward `seeded_recall` (which filters on `is_seed` alone, unaffected by
    this flag)."""
    row6 = crosswalk.RuleAnswer(status="listed")  # a disagreement by construction
    drawn = _labelled_items(n_correct=10, n_wrong_option=0, n_timeout=0, n_unresolved=0)
    extra_seed = _item(
        "extra-seed",
        "bankruptcy",
        "bankruptcy",
        "ok",
        is_seed=True,
        in_random_draw=False,
        pre_fix_rule_answer=row6,
    )
    items = [*drawn, extra_seed]

    accuracy = scoring.class_accuracy(items)
    assert accuracy.n == 10  # the extra seed never enters the denominator
    assert accuracy.correct == 10

    recall = scoring.seeded_recall(items)
    assert recall == 1.0  # the extra seed is caught (row6 disagrees with every option)


def test_in_random_draw_excludes_from_unresolved_and_unlabelled_share() -> None:
    not_drawn = _item("not-drawn", None, None, "timeout", is_seed=True, in_random_draw=False)
    items = [
        _item("a", "bankruptcy", "bankruptcy", "ok"),
        _item("b", None, "bankruptcy", "ok"),
        not_drawn,
    ]
    assert scoring.unresolved_share(items) == 0.0  # only "a" and "b" counted, neither unresolved
    assert scoring.unlabelled_share(items) == 0.5  # only "b" of the two drawn items


# --- unresolved_share, unlabelled_share -----------------------------------------


def test_unresolved_share() -> None:
    items = [
        _item("a", "bankruptcy", "bankruptcy", "ok"),
        _item("b", "bankruptcy", UNRESOLVED, "ok"),
        _item("c", "bankruptcy", None, "timeout"),
        _item("d", "bankruptcy", None, "refused"),
    ]
    assert scoring.unresolved_share(items) == 0.75
    assert scoring.unresolved_share([]) == 0.0


def test_unlabelled_share() -> None:
    items = [
        _item("a", "bankruptcy", "bankruptcy", "ok"),
        _item("b", None, "bankruptcy", "ok"),
        _item("c", None, None, "timeout"),
        _item("d", UNRESOLVED, "bankruptcy", "ok"),
    ]
    assert scoring.unlabelled_share(items) == 0.5
    assert scoring.unlabelled_share([]) == 0.0


# --- seeded_recall ---------------------------------------------------------------


def test_seeded_recall_with_no_seeds_is_none_not_a_vacuous_one() -> None:
    """#1351: no seed (or only seeds off rows 6 and 7) makes no claim; the old
    `1.0` passed the gate on an empty seed set."""
    assert scoring.seeded_recall([]) is None
    row4 = crosswalk.RuleAnswer(status="delisted", form15_in_window=True)
    seed = _item(
        "seed",
        "redomicile_or_reorganisation",
        "redomicile_or_reorganisation",
        "ok",
        is_seed=True,
        pre_fix_rule_answer=row4,
    )
    assert scoring.seeded_recall([seed]) is None


def test_seeded_recall_gates_rows_6_and_7_and_reports_the_rest() -> None:
    """#1351 option (a): a correct label on a row-4 or row-5 seed agrees with the
    pre-fix rule, so it is not caught; it is reported, never gated."""
    row6 = crosswalk.RuleAnswer(status="listed")
    row7 = crosswalk.RuleAnswer(status="unmatched")
    row4 = crosswalk.RuleAnswer(status="delisted", form15_in_window=True)
    row5 = crosswalk.RuleAnswer(status="delisted")
    items = [
        _item("seed-6", "bankruptcy", "bankruptcy", "ok", is_seed=True, pre_fix_rule_answer=row6),
        _item("seed-7", "bankruptcy", None, "timeout", is_seed=True, pre_fix_rule_answer=row7),
        # Seagate 2021's shape: a correct redomicile label agrees with row 4
        _item(
            "seed-4",
            "redomicile_or_reorganisation",
            "redomicile_or_reorganisation",
            "ok",
            is_seed=True,
            pre_fix_rule_answer=row4,
        ),
        # a row-5 seed the model labels outside the row's set is caught, still ungated
        _item(
            "seed-5",
            "exchange_transfer",
            "exchange_transfer",
            "ok",
            is_seed=True,
            pre_fix_rule_answer=row5,
        ),
        _item("not-a-seed", "bankruptcy", "bankruptcy", "ok", is_seed=False),
    ]
    assert scoring.seeded_recall(items) == 1.0
    assert scoring.seeded_report(items) == (
        scoring.SeedOutcome("seed-4", 4, gated=False, caught=False, basis="text"),
        scoring.SeedOutcome("seed-5", 5, gated=False, caught=True, basis="text"),
        scoring.SeedOutcome("seed-6", 6, gated=True, caught=True, basis="text"),
        scoring.SeedOutcome("seed-7", 7, gated=True, caught=True, basis="text"),
    )


# --- per_option_precision_recall -------------------------------------------------


def test_per_option_precision_recall_against_the_scipy_oracle() -> None:
    recall_items = [_item(f"r-correct-{i}", "bankruptcy", "bankruptcy", "ok") for i in range(5)] + [
        _item(f"r-wrong-{i}", "bankruptcy", "exchange_transfer", "ok") for i in range(15)
    ]
    precision_items = [
        _item(f"p-correct-{i}", "compliance_delisting", "compliance_delisting", "ok")
        for i in range(3)
    ] + [
        _item(f"p-wrong-{i}", "voluntary_withdrawal", "compliance_delisting", "ok")
        for i in range(12)
    ]
    result = scoring.per_option_precision_recall(recall_items + precision_items)

    bankruptcy = result["bankruptcy"]
    assert bankruptcy.precision_n == 5
    assert bankruptcy.precision == 1.0
    assert bankruptcy.precision_lower_bound is not None
    assert math.isclose(bankruptcy.precision_lower_bound, 0.05 ** (1 / 5), abs_tol=1e-9)
    assert bankruptcy.recall_n == 20
    assert bankruptcy.recall == 0.25
    assert bankruptcy.recall_lower_bound is not None
    assert math.isclose(bankruptcy.recall_lower_bound, 0.1040808359101361, abs_tol=1e-9)
    assert bankruptcy.underpowered is False  # recall_n == 20, not under it

    compliance = result["compliance_delisting"]
    assert compliance.precision_n == 15
    assert compliance.precision is not None
    assert math.isclose(compliance.precision, 0.2, abs_tol=1e-9)
    assert compliance.precision_lower_bound is not None
    assert math.isclose(compliance.precision_lower_bound, 0.05684686759024681, abs_tol=1e-9)
    assert compliance.recall_n == 3
    assert compliance.recall == 1.0
    assert compliance.recall_lower_bound is not None
    assert math.isclose(compliance.recall_lower_bound, 0.05 ** (1 / 3), abs_tol=1e-9)
    assert compliance.underpowered is True  # recall_n == 3, under 20


def test_per_option_precision_recall_excludes_non_scorable_gold_from_precision() -> None:
    """A model call on an item the owner left gold-`unresolved` or
    unlabelled makes no claim precision can be scored against (quant-auditor
    SHOULD FIX on PR #1069): it must not lower that option's precision."""
    scorable_true_positive = _item("tp", "bankruptcy", "bankruptcy", "ok")
    non_scorable_unresolved = _item("unresolved-gold", UNRESOLVED, "bankruptcy", "ok")
    non_scorable_unlabelled = _item("unlabelled-gold", None, "bankruptcy", "ok")
    items = [scorable_true_positive, non_scorable_unresolved, non_scorable_unlabelled]

    result = scoring.per_option_precision_recall(items)
    bankruptcy = result["bankruptcy"]
    assert bankruptcy.precision_n == 1  # only the scorable item enters the denominator
    assert bankruptcy.precision == 1.0


def test_per_option_precision_recall_no_support_is_none() -> None:
    result = scoring.per_option_precision_recall([])
    merger = result["merger_or_acquisition"]
    assert merger.precision is None
    assert merger.precision_lower_bound is None
    assert merger.recall is None
    assert merger.recall_lower_bound is None
    assert merger.underpowered is True


# --- rule_provision_arm_score -----------------------------------------------------


def test_rule_provision_arm_score() -> None:
    items = [
        # one-class arm ((a)(1) -> {instrument_retirement}), gold matches: covered and correct
        _item(
            "one-class-correct",
            "instrument_retirement",
            "instrument_retirement",
            "ok",
            rule_provision="12d2-2(a)(1)",
        ),
        # multi-class arm ((a)(3)): covered (bankruptcy's class is among the arm's classes),
        # never counted toward class_accuracy
        _item(
            "multi-class-covered",
            "bankruptcy",
            "bankruptcy",
            "ok",
            rule_provision="12d2-2(a)(3)",
        ),
        # one-class arm again, gold outside it: not covered, counted wrong
        _item(
            "one-class-wrong",
            "bankruptcy",
            "bankruptcy",
            "ok",
            rule_provision="12d2-2(a)(1)",
        ),
    ]
    result = scoring.rule_provision_arm_score(items)
    assert math.isclose(result.coverage, 2 / 3, abs_tol=1e-9)
    assert result.class_accuracy.n == 2  # the two one-class-arm items
    assert result.class_accuracy.correct == 1
    assert math.isclose(result.class_accuracy.point_estimate, 0.5, abs_tol=1e-9)
    assert result.class_accuracy.lower_bound == scoring.wilson_lower_bound(1, 2)


def test_rule_provision_arm_score_empty_is_zero() -> None:
    result = scoring.rule_provision_arm_score([])
    assert result.coverage == 0.0
    assert result.class_accuracy.n == 0


def test_rule_provision_arm_score_excludes_a_seed_not_in_the_random_draw() -> None:
    """The arm is "scored on the same gold items" as the model (req 5):
    a seed added beside the 150 drawn items must not change the arm's
    coverage or accuracy either (quant-auditor SHOULD FIX on PR #1069,
    pass 2)."""
    drawn = [
        _item(
            "one-class-correct",
            "instrument_retirement",
            "instrument_retirement",
            "ok",
            rule_provision="12d2-2(a)(1)",
        )
    ]
    without_seed = scoring.rule_provision_arm_score(drawn)

    extra_seed = _item(
        "extra-seed",
        "bankruptcy",
        "bankruptcy",
        "ok",
        rule_provision="12d2-2(a)(1)",
        is_seed=True,
        in_random_draw=False,
    )
    with_seed = scoring.rule_provision_arm_score([*drawn, extra_seed])

    assert with_seed.coverage == without_seed.coverage
    assert with_seed.class_accuracy.n == without_seed.class_accuracy.n
    assert with_seed.class_accuracy.correct == without_seed.class_accuracy.correct


def test_rule_provision_arm_score_scores_the_arm_against_gold_not_the_model() -> None:
    """The arm's `class_accuracy` must answer "does the arm agree with
    gold?", not "does the model agree with the arm?" (quant-auditor BLOCKER
    on PR #1069): a one-class arm (`(a)(1)` -> `{instrument_retirement}`)
    where the arm is *right* but the model disagrees with it must still
    score correct, and one where the arm is *wrong* but the model happens
    to agree with it must still score wrong."""
    items = [
        # arm right (gold == instrument_retirement), model disagrees with
        # the arm (bankruptcy): must count correct for the arm.
        _item(
            "arm-right-model-wrong",
            "instrument_retirement",
            "bankruptcy",
            "ok",
            rule_provision="12d2-2(a)(1)",
        ),
        # arm wrong (gold == bankruptcy, outside the arm's one class), model
        # happens to agree with the arm (instrument_retirement): must count
        # wrong for the arm.
        _item(
            "arm-wrong-model-agrees-with-arm",
            "bankruptcy",
            "instrument_retirement",
            "ok",
            rule_provision="12d2-2(a)(1)",
        ),
    ]
    result = scoring.rule_provision_arm_score(items)
    assert result.class_accuracy.n == 2
    assert result.class_accuracy.correct == 1  # only the first item: the arm itself is right
    assert math.isclose(result.class_accuracy.point_estimate, 0.5, abs_tol=1e-9)


# --- ece: a hand-built vector set against a worked value ------------------------


def _calibration_items_for_ece() -> list[scoring.CalibrationItem]:
    """15 items, one per equal-mass bin (`n == n_bins`): `p_max = i / 15`,
    correct for `i >= 8`. Hand-worked ECE below."""
    items = []
    for i in range(1, 16):
        p = i / 15
        correct = i >= 8
        gold_class = "insolvency" if correct else "transfer"
        items.append(
            scoring.CalibrationItem(
                probabilities={"bankruptcy": p, "exchange_transfer": 0.0}, gold_class=gold_class
            )
        )
    return items


def test_ece_against_a_hand_worked_value() -> None:
    """With one item per bin, `ece = mean_i |correct_i - p_i|`. For `i` 1 to
    7 (wrong), the term is `i / 15`; for `i` 8 to 15 (right), `(15 - i) /
    15`. Both halves sum to `28 / 15`, so `ece = (56 / 15) / 15 = 56 / 225`.
    """
    items = _calibration_items_for_ece()
    expected = 56 / 225
    assert math.isclose(scoring.ece(items), expected, abs_tol=1e-9)


def test_ece_empty_is_zero() -> None:
    assert scoring.ece([]) == 0.0


def test_ece_aggregates_option_probability_to_class_before_scoring() -> None:
    """`ece` must score confidence and correctness at the *class* level, not
    mix an option-level top probability with a class-level correctness
    check (quant-auditor SHOULD FIX on PR #1069). Two options in the same
    class (`terminal`: `merger_or_acquisition` 0.5, `going_private` 0.45)
    sum to a 0.95 class probability; gold is `terminal`, so the item is
    correct at p = 0.95, not p = 0.5 (the top option alone)."""
    item = scoring.CalibrationItem(
        probabilities={"merger_or_acquisition": 0.5, "going_private": 0.45, "bankruptcy": 0.05},
        gold_class="terminal",
    )
    # one item: the whole mass sits in the last equal-mass bin regardless of
    # n_bins, so ece == |accuracy - confidence| == |1 - 0.95| == 0.05.
    assert math.isclose(scoring.ece([item]), 0.05, abs_tol=1e-9)


def test_ece_and_classwise_reliability_are_permutation_invariant_on_ties() -> None:
    """With tied confidences, `ece` and `classwise_reliability` must not
    depend on the order the caller passes items in (quant-auditor SHOULD
    FIX on PR #1069; E7: ties are the common case, median p = 1.000)."""
    right = [
        scoring.CalibrationItem(probabilities={"bankruptcy": 0.7}, gold_class="insolvency")
        for _ in range(15)
    ]
    wrong = [
        scoring.CalibrationItem(probabilities={"bankruptcy": 0.7}, gold_class="transfer")
        for _ in range(15)
    ]
    right_first = right + wrong
    interleaved = [item for pair in zip(right, wrong, strict=True) for item in pair]

    ece_right_first = scoring.ece(right_first)
    ece_interleaved = scoring.ece(interleaved)
    assert math.isclose(ece_right_first, ece_interleaved, abs_tol=1e-9)

    reliability_right_first = scoring.classwise_reliability(right_first)
    reliability_interleaved = scoring.classwise_reliability(interleaved)
    for class_name in reliability_right_first:
        assert math.isclose(
            reliability_right_first[class_name], reliability_interleaved[class_name], abs_tol=1e-9
        )


def test_max_class_probability_tie_breaks_on_class_name_not_dict_order() -> None:
    """When two classes tie exactly on probability, `_max_class_probability_
    and_class` must pick the same class regardless of the probability
    mapping's key order (quant-auditor follow-up on PR #1069, pass 3):
    `insolvency` (`bankruptcy`) and `transfer` (`exchange_transfer`) tie at
    0.5, and must both resolve to `insolvency` (alphabetically first),
    whichever key is written first. An `ece`-level test cannot distinguish
    this (with one item at a 0.5 tie, `ece` is 0.5 whichever class wins,
    since `|1 - 0.5| == |0 - 0.5|`), so this tests the helper directly."""
    p_max_first, class_first = scoring._max_class_probability_and_class(
        {"bankruptcy": 0.5, "exchange_transfer": 0.5}
    )
    p_max_second, class_second = scoring._max_class_probability_and_class(
        {"exchange_transfer": 0.5, "bankruptcy": 0.5}
    )
    assert class_first == class_second == "insolvency"
    assert p_max_first == p_max_second == 0.5


# --- multiclass_brier and classwise_reliability: a one-item hand-worked case ----


def _single_calibration_item() -> scoring.CalibrationItem:
    return scoring.CalibrationItem(
        probabilities={"bankruptcy": 0.7, "exchange_transfer": 0.3}, gold_class="insolvency"
    )


def test_multiclass_brier_hand_worked() -> None:
    """One item, `p(bankruptcy) = 0.7` (class `insolvency`, the gold class)
    and `p(exchange_transfer) = 0.3` (class `transfer`): Brier =
    `(0.7 - 1)**2 + (0.3 - 0)**2 + 0 + 0 + 0 = 0.09 + 0.09 = 0.18`."""
    assert math.isclose(scoring.multiclass_brier([_single_calibration_item()]), 0.18, abs_tol=1e-9)


def test_multiclass_brier_empty_is_zero() -> None:
    assert scoring.multiclass_brier([]) == 0.0


def test_classwise_reliability_hand_worked() -> None:
    """The same single item: with `n = 1 < n_bins`, only the last equal-
    mass bin holds it, so each class's ECE is `|indicator - class
    probability|`: `insolvency` (the gold class) is `|1 - 0.7| = 0.3`,
    `transfer` is `|0 - 0.3| = 0.3`, the other three classes (no
    probability mass, not the gold class) are `0.0`."""
    result = scoring.classwise_reliability([_single_calibration_item()])
    expected = {
        "continuity": 0.0,
        "insolvency": 0.3,
        "removal": 0.0,
        "terminal": 0.0,
        "transfer": 0.3,
    }
    assert result.keys() == expected.keys()
    for class_name, value in expected.items():
        assert math.isclose(result[class_name], value, abs_tol=1e-9)


def test_classwise_reliability_empty_is_zero_for_every_class() -> None:
    """code-review follow-up on PR #1069: `classwise_reliability([])` must
    return `0.0` for every class, consistent with `ece([])` and
    `multiclass_brier([])`, not merely happen to by falling through an
    empty loop."""
    result = scoring.classwise_reliability([])
    assert result.keys() == set(crosswalk.CLASSES)
    assert all(value == 0.0 for value in result.values())


def test_share_at_or_above_skips_an_empty_probability_vector() -> None:
    items = [
        scoring.CalibrationItem({}, "insolvency"),
        scoring.CalibrationItem({"bankruptcy": 1.0}, "insolvency"),
    ]
    assert scoring.share_at_or_above(items) == 0.5  # only the second item is at or above 0.999


def test_mean_abs_probability_shift_skips_an_empty_probability_vector() -> None:
    comparisons = [
        scoring.DriftComparison(
            "a", "bankruptcy", "bankruptcy", probabilities={}, baseline_probabilities={"x": 1.0}
        )
    ]
    assert scoring.mean_abs_probability_shift(comparisons) == 0.0


# --- share_at_or_above and the agreement-by-bin table ----------------------------


def test_share_at_or_above() -> None:
    items = [
        scoring.CalibrationItem({"bankruptcy": 0.9995}, "insolvency"),
        scoring.CalibrationItem({"bankruptcy": 0.5}, "insolvency"),
        scoring.CalibrationItem({"bankruptcy": 1.0}, "insolvency"),
        scoring.CalibrationItem({"bankruptcy": 0.2}, "insolvency"),
    ]
    assert scoring.share_at_or_above(items) == 0.5
    assert scoring.share_at_or_above([]) == 0.0


def test_agreement_by_probability_bin() -> None:
    confidence_and_agreement = [
        (0.3, True),
        (0.3, False),  # [0, 0.6): 2 items, 1 agrees -> 0.5
        (0.7, True),  # [0.6, 0.9): 1 item, agrees -> 1.0
        (0.95, False),  # [0.9, 0.999): 1 item, 0 agree -> 0.0
        (1.0, True),
        (0.999, True),  # [0.999, 1.0]: 2 items, both agree -> 1.0
    ]
    rows = scoring.agreement_by_probability_bin(confidence_and_agreement)
    by_lower = {row.lower: row for row in rows}
    assert by_lower[0.0].n == 2
    assert by_lower[0.0].agreement_rate == 0.5
    assert by_lower[0.6].n == 1
    assert by_lower[0.6].agreement_rate == 1.0
    assert by_lower[0.9].n == 1
    assert by_lower[0.9].agreement_rate == 0.0
    assert by_lower[0.999].n == 2
    assert by_lower[0.999].agreement_rate == 1.0


# --- the drift probe: flip_rate and mean_abs_probability_shift ------------------


def _drift_comparisons(n_flips: int, n_total: int = 20) -> list[scoring.DriftComparison]:
    comparisons = []
    for i in range(n_total):
        flipped = i < n_flips
        comparisons.append(
            scoring.DriftComparison(
                listing_end_id=f"c-{i}",
                option="exchange_transfer" if flipped else "bankruptcy",
                baseline_option="bankruptcy",
            )
        )
    return comparisons


def test_flip_rate_1_of_20_passes_a_0_10_threshold() -> None:
    rate = scoring.flip_rate(_drift_comparisons(1))
    assert math.isclose(rate, 0.05, abs_tol=1e-9)
    assert rate < 0.10


def test_flip_rate_2_of_20_fails_a_0_10_threshold() -> None:
    rate = scoring.flip_rate(_drift_comparisons(2))
    assert math.isclose(rate, 0.10, abs_tol=1e-9)
    assert not rate < 0.10  # direction "less": equal to the threshold does not pass


def test_flip_rate_empty_is_zero() -> None:
    assert scoring.flip_rate([]) == 0.0


def test_mean_abs_probability_shift() -> None:
    comparisons = [
        scoring.DriftComparison(
            "a",
            "bankruptcy",
            "bankruptcy",
            probabilities={"bankruptcy": 0.95},
            baseline_probabilities={"bankruptcy": 1.0},
        ),
        scoring.DriftComparison(
            "b",
            "bankruptcy",
            "bankruptcy",
            probabilities={"bankruptcy": 0.80},
            baseline_probabilities={"bankruptcy": 0.80},
        ),
        # no probabilities on either side: skipped, not counted
        scoring.DriftComparison("c", "bankruptcy", "bankruptcy"),
    ]
    # |0.95 - 1.0| = 0.05; |0.80 - 0.80| = 0.0; mean over the 2 comparable = 0.025
    assert math.isclose(scoring.mean_abs_probability_shift(comparisons), 0.025, abs_tol=1e-9)


def test_mean_abs_probability_shift_nothing_comparable_is_zero() -> None:
    assert scoring.mean_abs_probability_shift([scoring.DriftComparison("a", "x", "x")]) == 0.0


# --- the split score (#1492, item 3) ---------------------------------------------------


def _split_items() -> list[scoring.ScoredItem]:
    """Ten `text` items (eight right), four `provision` items the model calls
    unresolved (the arm's `(a)(2)` agrees with their gold by construction), three
    `exchange_rule` items (one right), one gold `unresolved`, one unlabelled."""
    items = [
        scoring.ScoredItem(f"t-right-{i}", "bankruptcy", "bankruptcy", "ok", basis="text")
        for i in range(8)
    ]
    items += [
        scoring.ScoredItem(f"t-wrong-{i}", "bankruptcy", "exchange_transfer", "ok", basis="text")
        for i in range(2)
    ]
    items += [
        scoring.ScoredItem(
            f"p-{i}",
            "instrument_retirement",
            UNRESOLVED,
            "ok",
            rule_provision="12d2-2(a)(2)",
            basis="provision",
        )
        for i in range(4)
    ]
    items += [
        scoring.ScoredItem("e-right", "bankruptcy", "bankruptcy", "ok", basis="exchange_rule"),
        scoring.ScoredItem("e-wrong-0", "bankruptcy", UNRESOLVED, "ok", basis="exchange_rule"),
        scoring.ScoredItem("e-wrong-1", "bankruptcy", None, "timeout", basis="exchange_rule"),
        scoring.ScoredItem("gold-unresolved", UNRESOLVED, "bankruptcy", "ok"),
        scoring.ScoredItem("unlabelled", None, "bankruptcy", "ok"),
    ]
    return items


def test_a_provision_item_never_enters_the_headline_or_the_arm() -> None:
    items = _split_items()
    headline = scoring.headline_items(items)
    assert {i.listing_end_id for i in headline} == {
        *(f"t-right-{i}" for i in range(8)),
        "t-wrong-0",
        "t-wrong-1",
        "unlabelled",
    }
    primary = scoring.class_accuracy(headline)
    assert (primary.n, primary.correct) == (10, 8)
    # the worst case adds the one unlabelled item as wrong: 8 of 11
    assert scoring.worst_case_lower_bound(headline) == pytest.approx(
        scoring.wilson_lower_bound(8, 11)
    )
    per_option = scoring.per_option_precision_recall(headline, min_gold=1)
    assert per_option["instrument_retirement"].recall_n == 0
    assert per_option["bankruptcy"].recall_n == 10
    # the four provision items carry the arm's own provision: on the headline set the
    # arm sees none of them (no text item has a rule_provision here)
    arm = scoring.rule_provision_arm_score(headline)
    assert arm.class_accuracy.n == 0 and arm.coverage == 0.0
    # pooled, the same functions would have counted them: the test's own control
    pooled = scoring.rule_provision_arm_score(items)
    assert pooled.class_accuracy.n == 4


def test_exchange_rule_and_provision_each_get_their_own_accuracy() -> None:
    items = _split_items()
    exchange_rule = scoring.class_accuracy(scoring.by_basis(items, "exchange_rule"))
    provision = scoring.class_accuracy(scoring.by_basis(items, "provision"))
    assert (exchange_rule.n, exchange_rule.correct) == (3, 1)
    assert exchange_rule.lower_bound == pytest.approx(scoring.wilson_lower_bound(1, 3))
    # a model unresolved on a provision item counts wrong, as everywhere
    assert (provision.n, provision.correct) == (4, 0)
    assert [i.listing_end_id for i in scoring.by_basis(items, "text")][:1] == ["t-right-0"]


def test_basis_counts_counts_the_random_draw() -> None:
    items = _split_items()
    items.append(
        scoring.ScoredItem(
            "extra-seed",
            "bankruptcy",
            "bankruptcy",
            "ok",
            basis="text",
            is_seed=True,
            in_random_draw=False,
        )
    )
    assert scoring.basis_counts(items) == {
        "text": 10,
        "exchange_rule": 3,
        "provision": 4,
        "unresolved": 1,
        "unlabelled": 1,
    }


@pytest.mark.parametrize("label", ["bankruptcy", "instrument_retirement"])
def test_a_labelled_item_without_a_basis_raises(label: str) -> None:
    with pytest.raises(ValueError, match="needs a basis"):
        scoring.ScoredItem("x", label, label, "ok")


@pytest.mark.parametrize("label", [UNRESOLVED, None])
def test_a_basis_on_an_unresolved_or_unlabelled_item_raises(label: str | None) -> None:
    with pytest.raises(ValueError, match="basis 'text' on a gold"):
        scoring.ScoredItem("x", label, "bankruptcy", "ok", basis="text")


def test_a_basis_outside_the_three_raises() -> None:
    with pytest.raises(ValueError, match="not one of"):
        scoring.ScoredItem("x", "bankruptcy", "bankruptcy", "ok", basis="notice")  # type: ignore[arg-type]


def test_seeded_recall_is_unchanged_by_basis_and_the_report_carries_it() -> None:
    answer = crosswalk.RuleAnswer(
        status="listed", relisted=False, successor_id=None, form15_in_window=False
    )

    def seeds(basis: scoring.Basis) -> list[scoring.ScoredItem]:
        return [
            scoring.ScoredItem(
                "s6",
                "bankruptcy",
                "bankruptcy",
                "ok",
                basis=basis,
                is_seed=True,
                in_random_draw=False,
                pre_fix_rule_answer=answer,
            )
        ]

    by_basis = {basis: scoring.seeded_recall(seeds(basis)) for basis in scoring.BASES}
    assert set(by_basis.values()) == {1.0}
    assert [o.basis for o in scoring.seeded_report(seeds("provision"))] == ["provision"]


# --- the evidence ladder, the hybrid router and McNemar (#1504, T175) -------------

import dataclasses  # noqa: E402


def _call(
    arm: str,
    kind: str,
    option: str | None,
    reason: str = "ok",
    *,
    stop_reason: str | None = None,
    cost: float = 0.01,
    latency: float = 100.0,
    tokens_in: int = 100,
    tokens_out: int = 10,
) -> scoring.ArmCall:
    return scoring.ArmCall(
        arm,  # type: ignore[arg-type]
        kind,  # type: ignore[arg-type]
        option,
        reason,  # type: ignore[arg-type]
        stop_reason,
        tokens_in,
        tokens_out,
        cost,
        latency,
    )


def _jev(kind: str, option: str | None, reason: str = "ok", **kw: float) -> scoring.ArmCall:
    return _call("jev", kind, option, reason, latency=100.0 if kind == "A" else 300.0, **kw)  # type: ignore[arg-type]


def _claude(kind: str, option: str | None, reason: str = "ok", **kw: object) -> scoring.ArmCall:
    return _call("claude", kind, option, reason, cost=0.02, tokens_in=200, **kw)  # type: ignore[arg-type]


BK, WRONG, MA = "bankruptcy", "exchange_transfer", "merger_or_acquisition"


def _ladder_items(*, with_claude: bool = True) -> list[scoring.ScoredItem]:
    """30 text items: 20 plain (gold bankruptcy, no 8-K, provision (a)(3)) and 10 with an
    8-K (gold merger_or_acquisition; K0-3 on (a)(1), the rest on (a)(3)).

    Jev A right on P0-13, K8, K9 (timeout P18, ok-unresolved P19); Jev B right on K0-6
    and K9 (K7 unresolved, K8 wrong). Claude A right on P0-11, P14-17, K0-3 (P18
    `max_tokens`, P19 `refusal`); Claude B right on K0-4, wrong K5-8, none on K9 (B was
    refused before sending)."""
    items = []
    for i in range(20):
        jev_a = (
            _jev("A", BK)
            if i < 14
            else _jev("A", WRONG)
            if i < 18
            else _jev("A", None, "timeout")
            if i == 18
            else _jev("A", UNRESOLVED)
        )
        calls = [jev_a]
        if with_claude:
            if i in (12, 13):
                calls.append(_claude("A", WRONG))
            elif i == 18:
                calls.append(_claude("A", None, "refused", stop_reason="max_tokens"))
            elif i == 19:
                calls.append(_claude("A", None, "refused", stop_reason="refusal"))
            else:
                calls.append(_claude("A", BK))
        items.append(
            _item(f"P{i}", BK, BK, "ok", calls=tuple(calls), rule_provision="12d2-2(a)(3)")
        )
    for i in range(10):
        calls = [_jev("A", MA if i >= 8 else BK)]
        calls.append(
            _jev("B", UNRESOLVED)
            if i == 7
            else _jev("B", MA if i in (0, 1, 2, 3, 4, 5, 6, 9) else BK)
        )
        if with_claude:
            calls.append(_claude("A", MA if i < 4 else BK))
            if i != 9:
                calls.append(_claude("B", MA if i < 5 else BK))
        items.append(
            _item(
                f"K{i}",
                MA,
                BK,
                "ok",
                calls=tuple(calls),
                has_eightk=True,
                rule_provision="12d2-2(a)(1)" if i < 4 else "12d2-2(a)(3)",
            )
        )
    return items


def _row(
    rows: tuple[scoring.LadderRow, ...], arm: str, level: str, set_name: str = "text"
) -> scoring.LadderRow:
    (row,) = [r for r in rows if (r.arm, r.level, r.set_name) == (arm, level, set_name)]
    return row


def test_mcnemar_exact_matches_the_amendments_worked_values() -> None:
    assert scoring.mcnemar_exact(10, 1) == pytest.approx(0.012, abs=1e-3)
    assert scoring.mcnemar_exact(8, 3) == pytest.approx(0.227, abs=1e-3)
    assert scoring.mcnemar_exact(6, 0) == pytest.approx(0.031, abs=1e-3)
    assert scoring.mcnemar_exact(15, 5) == pytest.approx(0.041, abs=1e-3)
    assert scoring.mcnemar_exact(7, 1) == pytest.approx(0.070, abs=1e-3)


def test_mcnemar_exact_is_symmetric_and_degenerate_cases() -> None:
    for b, c in [(10, 1), (8, 3), (0, 6), (4, 4)]:
        assert scoring.mcnemar_exact(b, c) == scoring.mcnemar_exact(c, b)
    assert scoring.mcnemar_exact(0, 0) == 1.0
    assert scoring.mcnemar_exact(4, 4) == 1.0
    with pytest.raises(ValueError, match="non-negative"):
        scoring.mcnemar_exact(-1, 3)


@pytest.mark.parametrize(
    ("m", "k"),
    [(6, 6), (8, 8), (9, 8), (10, 9), (11, 10), (15, 12), (20, 15), (30, 21), (4, None)],
)
def test_min_significant_split_matches_the_amendment(m: int, k: int | None) -> None:
    assert scoring.min_significant_split(m) == k


def test_level_answer_e1_e2_and_cascade() -> None:
    items = {i.listing_end_id: i for i in _ladder_items()}
    # E1 is the A call; E2 is B where there is an 8-K and B was sent, else A
    assert scoring.level_answer(items["K0"], "jev", "E1") == BK
    assert scoring.level_answer(items["K0"], "jev", "E2") == MA
    assert scoring.level_answer(items["P0"], "jev", "E2") == BK  # no 8-K: E2 = A
    # B sent but unresolved: E2 reads unresolved, the cascade falls back to A
    assert scoring.level_answer(items["K7"], "jev", "E2") == UNRESOLVED
    assert scoring.level_answer(items["K7"], "jev", "cascade") == BK
    # B resolved (wrong): the cascade keeps B
    assert scoring.level_answer(items["K8"], "jev", "cascade") == BK
    assert scoring.level_answer(items["K6"], "jev", "cascade") == MA
    # a refused or timeout call reads as unresolved
    assert scoring.level_answer(items["P18"], "jev", "E1") == UNRESOLVED
    assert scoring.level_answer(items["P18"], "claude", "E1") == UNRESOLVED
    # B was never sent (refused by max_packet_tokens): E2 = A
    assert scoring.level_answer(items["K9"], "claude", "E2") == BK
    assert scoring.level_answer(items["K9"], "claude", "E1") == BK


def test_level_answer_edge_cases() -> None:
    item = _item("x", BK, BK, "ok", calls=(_jev("A", BK),))
    assert scoring.level_answer(item, "claude", "E1") is None  # arm made no call
    assert scoring.level_answer(item, "jev", "cascade") == BK
    with pytest.raises(ValueError, match="Jev's registered treatment"):
        scoring.level_answer(item, "claude", "cascade")
    with pytest.raises(ValueError, match="unknown ladder level"):
        scoring.level_answer(item, "jev", "E3")  # type: ignore[arg-type]
    twice = _item("y", BK, BK, "ok", calls=(_jev("A", BK), _jev("A", WRONG)))
    with pytest.raises(ValueError, match="2 jev calls on packet A"):
        scoring.level_answer(twice, "jev", "E1")


def test_arm_call_rejects_an_unknown_arm_or_kind() -> None:
    with pytest.raises(ValueError, match="arm"):
        _call("gpt", "A", BK)
    with pytest.raises(ValueError, match="kind"):
        _call("jev", "C", BK)


def test_ladder_table_counts_bounds_and_sums_on_the_fixture() -> None:
    rows = scoring.ladder_table(_ladder_items())
    e1 = _row(rows, "jev", "E1")
    assert (e1.n_eligible, e1.n, e1.correct) == (30, 30, 16)
    assert e1.point_estimate == pytest.approx(16 / 30)
    assert e1.lower_bound == pytest.approx(scoring.wilson_lower_bound(16, 30))
    assert e1.unresolved_share == pytest.approx(2 / 30)
    assert e1.timeouts == 1 and dict(e1.refused_by_stop_reason) == {}
    assert (e1.input_tokens, e1.output_tokens) == (3000, 300)
    assert e1.cost_usd == pytest.approx(0.30)
    assert e1.cost_per_correct == pytest.approx(0.30 / 16)
    assert (e1.latency_p50_ms, e1.latency_p90_ms) == (100.0, 100.0)
    assert not e1.underpowered

    e2 = _row(rows, "jev", "E2")
    assert (e2.n, e2.correct) == (30, 22)
    assert e2.unresolved_share == pytest.approx(3 / 30)
    assert e2.cost_usd == pytest.approx(0.30)  # 20 A calls + 10 B calls
    assert (e2.latency_p50_ms, e2.latency_p90_ms) == (100.0, 300.0)
    assert e2.b_refused == 0

    cascade = _row(rows, "jev", "cascade")
    assert (cascade.n, cascade.correct) == (30, 22)
    assert cascade.unresolved_share == pytest.approx(2 / 30)
    assert cascade.cost_usd == pytest.approx(0.31)  # K7 paid B and A
    assert cascade.cost_per_correct == pytest.approx(0.31 / 22)
    assert not [r for r in rows if r.arm == "claude" and r.level == "cascade"]


def test_ladder_table_claude_lines_refusals_and_b_refused() -> None:
    rows = scoring.ladder_table(_ladder_items())
    e1 = _row(rows, "claude", "E1")
    assert e1.correct == 20 and e1.unresolved_share == pytest.approx(2 / 30)
    assert dict(e1.refused_by_stop_reason) == {"max_tokens": 1, "refusal": 1}
    assert e1.timeouts == 0
    e2 = _row(rows, "claude", "E2")
    assert e2.correct == 21  # K9 has no B call, so E2 falls back to A (wrong)
    assert e2.b_refused == 1
    assert e2.cost_usd == pytest.approx(0.60)  # 20 + (9 B + 1 A) calls at 0.02
    assert e2.cost_per_correct == pytest.approx(0.60 / 21)


def test_ladder_table_eightk_subset_apart_and_underpowered() -> None:
    rows = scoring.ladder_table(_ladder_items())
    jev_e1 = _row(rows, "jev", "E1", "text_eightk")
    jev_e2 = _row(rows, "jev", "E2", "text_eightk")
    assert (jev_e1.n, jev_e1.correct) == (10, 2)
    assert (jev_e2.n, jev_e2.correct) == (10, 8)
    assert jev_e1.underpowered and jev_e1.lower_bound is None  # under 20
    # the subset is the items with an 8-K AND this arm's B packet sent
    claude_e1 = _row(rows, "claude", "E1", "text_eightk")
    claude_e2 = _row(rows, "claude", "E2", "text_eightk")
    assert (claude_e1.n_eligible, claude_e1.n, claude_e1.correct) == (9, 9, 4)
    assert (claude_e2.n, claude_e2.correct) == (9, 5)


def test_exchange_rule_and_provision_items_are_never_in_the_text_table() -> None:
    items = _ladder_items()
    extra = [
        _item(
            f"X{i}",
            "bankruptcy",
            BK,
            "ok",
            basis="exchange_rule",
            calls=(_jev("A", BK),),
            rule_provision="12d2-2(a)(1)",
        )
        for i in range(3)
    ] + [
        _item(
            f"R{i}",
            MA,
            MA,
            "ok",
            basis="provision",
            calls=(_jev("A", MA),),
            rule_provision="12d2-2(a)(1)",
        )
        for i in range(4)
    ]
    with_extra = scoring.ladder_table(items + extra)
    without = scoring.ladder_table(items)
    for level in ("E1", "E2", "cascade"):
        assert _row(with_extra, "jev", level) == _row(without, "jev", level)
    assert _row(with_extra, "jev", "E1", "exchange_rule").n == 3
    assert _row(with_extra, "jev", "E1", "provision").n == 4
    assert _row(with_extra, "jev", "E1", "provision").underpowered
    assert scoring.e0_report(items + extra) == scoring.e0_report(items)
    for level in ("E1", "E2", "cascade"):
        assert scoring.hybrid_router(items + extra, "jev", level) == scoring.hybrid_router(
            items, "jev", level
        )
    with pytest.raises(ValueError, match="text set"):
        scoring.hybrid_router(items + extra, "jev", "E1", set_name="provision")


def test_a_jev_only_run_has_no_claude_lines() -> None:
    items = _ladder_items(with_claude=False)
    assert {r.arm for r in scoring.ladder_table(items)} == {"jev"}
    comparisons = scoring.paired_comparisons(items)
    assert len(comparisons) == 10  # E1/E2, E2/cascade and 3 hybrids, on two sets
    assert all("claude" not in c.first + c.second for c in comparisons)


def test_is_rule_decisive_only_for_a1_and_a2() -> None:
    assert scoring.is_rule_decisive("12d2-2(a)(1)")
    assert scoring.is_rule_decisive("12d2-2(a)(2)")
    for provision in (
        "12d2-2(a)(3)",  # three classes
        "12d2-2(a)(4)",
        "12d2-2(b)",
        "12d2-2(c)",
        "something-unmapped",  # {unresolved} is never decisive
        "",
        None,
    ):
        assert not scoring.is_rule_decisive(provision)


def test_e0_decisive_share_matches_the_rule_arm_score() -> None:
    e0 = scoring.e0_report(_ladder_items())
    assert (e0.n, e0.decisive) == (30, 4)
    assert e0.decisive_share == pytest.approx(4 / 30)
    assert e0.arm.class_accuracy.n == e0.decisive == 4
    assert e0.arm.class_accuracy.correct == 4  # the rule's terminal matches gold MA


def test_hybrid_router_routes_a1_a2_charges_model_cost_only_and_reports_routed() -> None:
    items = _ladder_items()
    e1 = scoring.hybrid_router(items, "jev", "E1")
    assert e1.n == 30 and e1.routed_to_rule_share == pytest.approx(4 / 30)
    assert e1.correct == 20  # 16 model-right items, none of them routed, + 4 rule-right
    assert e1.unresolved_share == pytest.approx(2 / 30)
    assert e1.cost_usd == pytest.approx(0.26)  # 26 model-routed A calls; the rule is free
    assert e1.cost_per_correct == pytest.approx(0.26 / 20)
    routed = e1.routed
    assert routed.count == 4 and dict(routed.gold_class_mix) == {"terminal": 4}
    assert (routed.rule_correct, routed.model_correct) == (4, 0)
    assert (routed.rule_accuracy, routed.model_accuracy) == (1.0, 0.0)

    e2 = scoring.hybrid_router(items, "jev", "E2")
    assert e2.correct == 22 and e2.routed.model_correct == 4
    assert e2.cost_usd == pytest.approx(0.26)  # 20 A + K4-K9's six B calls

    cascade = scoring.hybrid_router(items, "jev", "cascade")
    assert cascade.cost_usd == pytest.approx(0.27)  # K7 pays B and A


def test_hybrid_router_never_routes_a_provision_basis_item() -> None:
    items = _ladder_items()
    provision = _item(
        "R0", MA, MA, "ok", basis="provision", calls=(_jev("A", MA),), rule_provision="12d2-2(a)(1)"
    )
    assert scoring.hybrid_router([*items, provision], "jev", "E1").n == 30
    assert scoring.hybrid_router([*items, provision], "jev", "E1").routed.count == 4


def test_hybrid_router_on_the_eightk_subset() -> None:
    result = scoring.hybrid_router(_ladder_items(), "claude", "E2", set_name="text_eightk")
    assert result.n == 9 and result.routed.count == 4  # K0-K8 (Claude sent B on those)
    assert result.underpowered and result.lower_bound is None


def test_paired_comparisons_make_twelve_plus_twelve() -> None:
    comparisons = scoring.paired_comparisons(_ladder_items())
    assert len(comparisons) == 24
    assert sum(1 for c in comparisons if c.set_name == "text") == 12
    assert sum(1 for c in comparisons if c.set_name == "text_eightk") == 12
    text = [c for c in comparisons if c.set_name == "text"]
    assert [(c.first, c.second) for c in text][:4] == [
        ("jev:E1", "claude:E1"),
        ("jev:E2", "claude:E2"),
        ("jev:E1:hybrid", "claude:E1:hybrid"),
        ("jev:E2:hybrid", "claude:E2:hybrid"),
    ]
    adjacent = [c for c in text if c.first.split(":")[0] == c.second.split(":")[0]]
    hybrid_pairs = [c for c in adjacent if c.second.endswith(":hybrid")]
    assert len(adjacent) == 8 and len(hybrid_pairs) == 5


def _comparison(first: str, second: str, set_name: str = "text") -> scoring.Comparison:
    (c,) = [
        c
        for c in scoring.paired_comparisons(_ladder_items())
        if (c.first, c.second, c.set_name) == (first, second, set_name)
    ]
    return c


def test_paired_comparison_discordant_counts_and_p() -> None:
    c = _comparison("jev:E1", "claude:E1")
    # Jev right on P0-13, K8, K9; Claude right on P0-11, P14-17, K0-3
    assert (c.n, c.b, c.c) == (30, 4, 8)
    assert c.accuracy_difference == pytest.approx((16 - 20) / 30)
    assert c.p_value == pytest.approx(scoring.mcnemar_exact(4, 8))
    assert c.min_split == scoring.min_significant_split(12)
    assert not c.underpowered

    adjacent = _comparison("jev:E1", "jev:E2")
    assert (adjacent.b, adjacent.c) == (1, 7)  # E1 right and E2 wrong: K8; the reverse: K0-6
    assert adjacent.accuracy_difference == pytest.approx((16 - 22) / 30)

    subset = _comparison("jev:E1", "claude:E1", "text_eightk")
    assert (subset.n, subset.b, subset.c) == (9, 1, 4)  # K0-K8, where both sent B
    assert subset.underpowered


def test_paired_comparison_against_the_hybrid_is_on_the_same_items() -> None:
    c = _comparison("jev:E1", "jev:E1:hybrid")
    # the hybrid fixes K0-K3 (rule right, Jev A wrong) and changes nothing else
    assert (c.n, c.b, c.c) == (30, 0, 4)
    assert c.p_value == pytest.approx(0.125)


def test_an_item_one_arm_never_answered_is_not_paired() -> None:
    items = _ladder_items()
    items.append(_item("only-jev", BK, BK, "ok", calls=(_jev("A", BK),)))
    c = next(
        c for c in scoring.paired_comparisons(items) if (c.first, c.set_name) == ("jev:E1", "text")
    )
    assert c.n == 30


def test_existing_metrics_are_byte_identical_with_and_without_calls() -> None:
    with_calls = _ladder_items()
    without = [dataclasses.replace(i, calls=(), has_eightk=False) for i in with_calls]
    for fn in (
        scoring.class_accuracy,
        scoring.per_option_precision_recall,
        scoring.seeded_recall,
        scoring.unresolved_share,
        scoring.worst_case_lower_bound,
        scoring.rule_provision_arm_score,
        scoring.basis_counts,
    ):
        assert fn(with_calls) == fn(without), fn.__name__  # type: ignore[operator]


def test_a_packet_never_sent_scores_unresolved_not_dropped() -> None:
    """The primary counts an item whose A packet was over the cap as wrong; the ladder
    must keep it in the denominator, so Jev's cascade row equals `class_accuracy`."""
    items = _ladder_items(with_claude=False)
    no_a = dataclasses.replace(
        items[30 - 1],  # K9: keep only its B call
        calls=(items[29].calls[1],),
        predicted_option=MA,
    )
    items[29] = no_a
    assert scoring.level_answer(no_a, "jev", "E1") == UNRESOLVED
    assert scoring.level_answer(no_a, "jev", "cascade") == MA
    rows = scoring.ladder_table(items)
    assert _row(rows, "jev", "E1").n == 30
    # a Jev-only fixture whose primary label is the cascade answer scores identically
    cascade_items = [
        dataclasses.replace(i, predicted_option=scoring.level_answer(i, "jev", "cascade"))
        for i in items
    ]
    acc = scoring.class_accuracy(cascade_items)
    row = _row(scoring.ladder_table(cascade_items), "jev", "cascade")
    assert (row.n, row.correct) == (acc.n, acc.correct) == (30, 22)
    no_calls = _item("no-calls", BK, BK, "ok", calls=())
    assert scoring.level_answer(no_calls, "jev", "cascade") == UNRESOLVED
    assert scoring.level_answer(no_calls, "claude", "E1") is None
    with_none = [*items, no_calls]
    assert _row(scoring.ladder_table(with_none), "jev", "E1").n == 31
    only_b = _item("only-b", BK, BK, "ok", calls=(_jev("B", BK),), has_eightk=True)
    assert scoring.level_answer(only_b, "jev", "E2") == BK
    assert scoring.level_answer(only_b, "jev", "E1") == UNRESOLVED

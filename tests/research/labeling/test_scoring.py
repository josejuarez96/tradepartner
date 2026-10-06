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
        items.append(scoring.ScoredItem(f"correct-{i}", "bankruptcy", "bankruptcy", "ok"))
    for i in range(n_wrong_option):
        items.append(
            scoring.ScoredItem(f"wrong-option-{i}", "bankruptcy", "exchange_transfer", "ok")
        )
    for i in range(n_timeout):
        items.append(scoring.ScoredItem(f"timeout-{i}", "bankruptcy", None, "timeout"))
    for i in range(n_unresolved):
        items.append(scoring.ScoredItem(f"unresolved-{i}", "bankruptcy", UNRESOLVED, "ok"))
    for i in range(extra_unlabelled):
        items.append(scoring.ScoredItem(f"unlabelled-{i}", None, "bankruptcy", "ok"))
    for i in range(extra_gold_unresolved):
        items.append(scoring.ScoredItem(f"gold-unresolved-{i}", UNRESOLVED, "bankruptcy", "ok"))
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
    item_matches = scoring.ScoredItem(
        "a", "bankruptcy", "bankruptcy", "ok", text_states=None
    )  # text_states defaults to gold_label (Q6)
    item_differs = scoring.ScoredItem(
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
    unlabelled = [scoring.ScoredItem(f"u-{i}", None, "bankruptcy", "ok") for i in range(5)]
    items = scored + unlabelled

    ordinary = scoring.class_accuracy(items)
    worst_case = scoring.worst_case_lower_bound(items)

    assert ordinary.n == 120  # unlabelled excluded from the ordinary bar
    assert worst_case == scoring.wilson_lower_bound(108, 125)  # the same 108, denominator +5
    assert worst_case < ordinary.lower_bound


# --- unresolved_share, unlabelled_share -----------------------------------------


def test_unresolved_share() -> None:
    items = [
        scoring.ScoredItem("a", "bankruptcy", "bankruptcy", "ok"),
        scoring.ScoredItem("b", "bankruptcy", UNRESOLVED, "ok"),
        scoring.ScoredItem("c", "bankruptcy", None, "timeout"),
        scoring.ScoredItem("d", "bankruptcy", None, "refused"),
    ]
    assert scoring.unresolved_share(items) == 0.75
    assert scoring.unresolved_share([]) == 0.0


def test_unlabelled_share() -> None:
    items = [
        scoring.ScoredItem("a", "bankruptcy", "bankruptcy", "ok"),
        scoring.ScoredItem("b", None, "bankruptcy", "ok"),
        scoring.ScoredItem("c", None, None, "timeout"),
        scoring.ScoredItem("d", UNRESOLVED, "bankruptcy", "ok"),
    ]
    assert scoring.unlabelled_share(items) == 0.5
    assert scoring.unlabelled_share([]) == 0.0


# --- seeded_recall ---------------------------------------------------------------


def test_seeded_recall_with_no_seeds_is_one() -> None:
    assert scoring.seeded_recall([]) == 1.0


def test_seeded_recall_3_of_4_caught() -> None:
    row6 = crosswalk.RuleAnswer(status="listed")  # a disagreement by construction
    row1 = crosswalk.RuleAnswer(status="transferred")  # consistent only with exchange_transfer
    items = [
        # caught: row 6 disagrees with every non-unresolved option
        scoring.ScoredItem(
            "seed-1", "bankruptcy", "bankruptcy", "ok", is_seed=True, pre_fix_rule_answer=row6
        ),
        # caught: a timeout is always shortlisted
        scoring.ScoredItem(
            "seed-2", "bankruptcy", None, "timeout", is_seed=True, pre_fix_rule_answer=row6
        ),
        # caught: a model unresolved is always shortlisted
        scoring.ScoredItem(
            "seed-3", "bankruptcy", UNRESOLVED, "ok", is_seed=True, pre_fix_rule_answer=row6
        ),
        # not caught: exchange_transfer is consistent with row 1, so it's an agreement
        scoring.ScoredItem(
            "seed-4",
            "exchange_transfer",
            "exchange_transfer",
            "ok",
            is_seed=True,
            pre_fix_rule_answer=row1,
        ),
        # a non-seed item never counts toward the denominator
        scoring.ScoredItem("not-a-seed", "bankruptcy", "bankruptcy", "ok", is_seed=False),
    ]
    assert scoring.seeded_recall(items) == 0.75


# --- per_option_precision_recall -------------------------------------------------


def test_per_option_precision_recall_against_the_scipy_oracle() -> None:
    recall_items = [
        scoring.ScoredItem(f"r-correct-{i}", "bankruptcy", "bankruptcy", "ok") for i in range(5)
    ] + [
        scoring.ScoredItem(f"r-wrong-{i}", "bankruptcy", "exchange_transfer", "ok")
        for i in range(15)
    ]
    precision_items = [
        scoring.ScoredItem(f"p-correct-{i}", "compliance_delisting", "compliance_delisting", "ok")
        for i in range(3)
    ] + [
        scoring.ScoredItem(f"p-wrong-{i}", "voluntary_withdrawal", "compliance_delisting", "ok")
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
        scoring.ScoredItem(
            "one-class-correct",
            "instrument_retirement",
            "instrument_retirement",
            "ok",
            rule_provision="12d2-2(a)(1)",
        ),
        # multi-class arm ((a)(3)): covered (bankruptcy's class is among the arm's classes),
        # never counted toward class_accuracy
        scoring.ScoredItem(
            "multi-class-covered",
            "bankruptcy",
            "bankruptcy",
            "ok",
            rule_provision="12d2-2(a)(3)",
        ),
        # one-class arm again, gold outside it: not covered, counted wrong
        scoring.ScoredItem(
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

"""Scoring: the pilot's bars and the drift probe's comparison, pure.

Research-labeling spec req 10 and its 2026-10-06 amendment (C6, C10's
question 6, the acceptance criteria after the amendment), plus the drift
probe of req 11 / C5. No I/O, no store, no network, no model client: every
function here is a pure reduction over the records the caller already holds
(`job.py` and `review.py` assemble them from the research store and the
model client's answers). Depends on T121's `questions` and `crosswalk`
modules for `UNRESOLVED` and the five classes; never on `packets.py`.

**Statistics.** `wilson_lower_bound` and `clopper_pearson_lower_bound` are
hand-implemented (`math` only: no `numpy`/`scipy` in `pyproject.toml`'s
dependencies, so none is imported here) and tested against independently
worked values (closed-form for Wilson; `scipy.stats.beta.ppf` run once,
outside this module, for Clopper-Pearson -- the number is in the test, not
derived from this module's own output).

**The pilot's primary bar** (`class_accuracy`): the share of gold items whose
model class equals the gold class, `gold_label` as owner decision 6 reads it
(a model `unresolved` or `timeout` on a labelled item scores wrong; a gold
`unresolved` or unlabelled item is excluded from the denominator), scored
with the Wilson one-sided 95% lower bound -- the worked numbers of req 10:
102, 104 and 108 right of 120 give 0.789, 0.807 and 0.846. The same function
scores against `text_states` (reported beside it, Q6).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tradepartner.research.labeling import crosswalk, questions

UNRESOLVED = questions.UNRESOLVED

#: One-sided 95% normal quantile (`z` with `Phi(z) = 0.95`), the Wilson
#: bound's only "magic number" -- fixed here rather than computed, since
#: `math` has no inverse normal CDF.
_Z95 = 1.6448536269514722

Reason = Literal["ok", "timeout", "refused"]


def wilson_lower_bound(successes: int, n: int, *, confidence: float = 0.95) -> float:
    """The Wilson score interval's one-sided lower bound on a binomial
    proportion (req 10's `inference`: "Wilson score one-sided 95% lower
    bound"). `0.0` for `n == 0` (no claim about an empty sample).

    Only `confidence = 0.95` has a closed-form `z` here; another value
    raises, rather than silently using the wrong quantile.
    """
    if n == 0:
        return 0.0
    if confidence != 0.95:
        raise ValueError(
            f"wilson_lower_bound: only confidence=0.95 is implemented, got {confidence}"
        )
    z = _Z95
    phat = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = phat + z2 / (2 * n)
    spread = z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    return (center - spread) / denominator


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction for the regularized incomplete beta function
    (Numerical Recipes §6.4), used only by `_betainc`."""
    max_iterations, eps, tiny = 200, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function `I_x(a, b)` (Numerical
    Recipes §6.4): the CDF of a `Beta(a, b)` distribution at `x`."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_prefix = a * math.log(x) + b * math.log(1 - x) - _log_beta(a, b)
    prefix = math.exp(log_prefix)
    if x < (a + 1.0) / (a + b + 2.0):
        return prefix * _betacf(a, b, x) / a
    return 1.0 - prefix * _betacf(b, a, 1 - x) / b


def clopper_pearson_lower_bound(successes: int, n: int, *, confidence: float = 0.95) -> float:
    """The Clopper-Pearson exact one-sided lower bound on a binomial
    proportion: the `alpha`-quantile of `Beta(successes, n - successes + 1)`
    (`alpha = 1 - confidence`), found by bisection over the incomplete beta
    function above (no `scipy.stats.beta.ppf` here: that import is not in
    `pyproject.toml`). `0.0` for `n == 0` or `successes == 0`.
    """
    if n == 0 or successes == 0:
        return 0.0
    alpha = 1.0 - confidence
    a, b = float(successes), float(n - successes + 1)
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _betainc(a, b, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class ScoredItem:
    """One gold or batch item's scoring inputs (req 10): the owner's labels
    (`gold_label`, the true reason; `text_states`, what the shown text says,
    defaulting to `gold_label` when the owner recorded no difference, Q6;
    both `None` for `unlabelled`, `questions.UNRESOLVED` for a gold
    `unresolved`), the model's final `predicted_option` (`None` for a
    `timeout` or `refused` call, which carries no answer) and `reason`, and,
    for the seeded error set, the pre-fix rule answer it is scored against
    (`is_seed`, `pre_fix_rule_answer`; req 10's "seeded error set")."""

    listing_end_id: str
    gold_label: str | None
    predicted_option: str | None
    reason: Reason
    rule_provision: str | None = None
    text_states: str | None = None
    probabilities: Mapping[str, float] | None = None
    is_seed: bool = False
    pre_fix_rule_answer: crosswalk.RuleAnswer | None = None

    @property
    def text_states_label(self) -> str | None:
        """`text_states` when the owner recorded one, else `gold_label`
        (Q6: "the lock copies `gold_label` into it otherwise")."""
        return self.text_states if self.text_states is not None else self.gold_label


def _predicted_class(item: ScoredItem) -> str | None:
    """The model's class for `item` (`None` on a `timeout`/`refused` call, an
    `unresolved` answer, or an option with no class)."""
    if item.reason != "ok" or item.predicted_option is None:
        return None
    if item.predicted_option == UNRESOLVED:
        return None
    return crosswalk.class_of(item.predicted_option)


@dataclass(frozen=True)
class AccuracyResult:
    """`class_accuracy`'s result: the scored count, how many were right, the
    point estimate, and its Wilson one-sided 95% lower bound (`0.0` for both
    when `n == 0`)."""

    n: int
    correct: int
    point_estimate: float
    lower_bound: float


def _gold_label_of(item: ScoredItem, against: Literal["gold_label", "text_states"]) -> str | None:
    return item.gold_label if against == "gold_label" else item.text_states_label


def class_accuracy(
    items: Sequence[ScoredItem],
    *,
    against: Literal["gold_label", "text_states"] = "gold_label",
) -> AccuracyResult:
    """The primary bar (req 10): the share of `items` whose gold label (by
    `against`) is not `None` (unlabelled) and not `questions.UNRESOLVED`
    (gold `unresolved`) -- both excluded from the denominator -- whose
    predicted class equals the gold class; a `timeout` or a model
    `unresolved` on a scored item counts wrong, never excluded."""
    scored = [item for item in items if _gold_label_of(item, against) not in (None, UNRESOLVED)]
    n = len(scored)
    correct = 0
    for item in scored:
        gold_label = _gold_label_of(item, against)
        assert gold_label is not None
        if _predicted_class(item) == crosswalk.class_of(gold_label):
            correct += 1
    point = correct / n if n else 0.0
    return AccuracyResult(
        n=n, correct=correct, point_estimate=point, lower_bound=wilson_lower_bound(correct, n)
    )


def worst_case_lower_bound(
    items: Sequence[ScoredItem], *, against: Literal["gold_label", "text_states"] = "gold_label"
) -> float:
    """The worst-case bound of req 10: every `unlabelled` item (gold `None`)
    counts as wrong and joins the denominator, beside the ordinary scoring
    rule for every other item (gold `unresolved` stays excluded: the owner
    made no claim to score against)."""
    scored = [item for item in items if _gold_label_of(item, against) != UNRESOLVED]
    n = len(scored)
    correct = 0
    for item in scored:
        label = _gold_label_of(item, against)
        if label is None:
            continue  # unlabelled: counts wrong, i.e. not correct
        if _predicted_class(item) == crosswalk.class_of(label):
            correct += 1
    return wilson_lower_bound(correct, n)


def unresolved_share(items: Sequence[ScoredItem]) -> float:
    """The share of `items` the model left unresolved (req 10's
    `unresolved_share`): a `timeout`, a `refused` call, or an `unresolved`
    answer, over every item passed (the batch's own denominator, not the
    gold-scored one)."""
    if not items:
        return 0.0
    count = sum(1 for item in items if item.reason != "ok" or item.predicted_option == UNRESOLVED)
    return count / len(items)


def unlabelled_share(items: Sequence[ScoredItem]) -> float:
    """The share of `items` the owner skipped twice (req 10's
    `unlabelled_share`: `gold_label is None`)."""
    if not items:
        return 0.0
    return sum(1 for item in items if item.gold_label is None) / len(items)


def seeded_recall(items: Sequence[ScoredItem]) -> float:
    """req 10's `seeded_recall`: the share of the seeded error set that would
    have been shortlisted under its *pre-fix* rule answer -- a disagreement,
    or a model `unresolved`/`timeout` (`crosswalk.is_disagreement`'s own
    rule: unresolved is "neither", but is still always shortlisted, so it
    counts as caught here). `1.0` when there are no seeds (vacuously true,
    never a reason to fail the pilot on an empty seed set)."""
    seeds = [item for item in items if item.is_seed and item.pre_fix_rule_answer is not None]
    if not seeds:
        return 1.0
    caught = sum(1 for item in seeds if _caught_by_pre_fix_rule(item))
    return caught / len(seeds)


def _caught_by_pre_fix_rule(item: ScoredItem) -> bool:
    """Whether `item`'s pre-fix rule answer would have shortlisted it (an
    `unresolved`/`timeout` answer is always shortlisted; otherwise it is
    `crosswalk.is_disagreement`'s call)."""
    rule_answer = item.pre_fix_rule_answer
    assert rule_answer is not None
    option = item.predicted_option
    if item.reason != "ok" or option is None or option == UNRESOLVED:
        return True
    return crosswalk.is_disagreement(rule_answer, option)


@dataclass(frozen=True)
class PrecisionRecall:
    """One option's precision and recall (req 10), each with its Clopper-
    Pearson one-sided 95% lower bound and the gold support it was computed
    on; `underpowered` when that support (the gold items truly of this
    option) is under `min_gold` (default 20, req 10's gate threshold)."""

    option: str
    precision: float | None
    precision_lower_bound: float | None
    precision_n: int
    recall: float | None
    recall_lower_bound: float | None
    recall_n: int
    underpowered: bool


def per_option_precision_recall(
    items: Sequence[ScoredItem], *, min_gold: int = 20
) -> dict[str, PrecisionRecall]:
    """Precision and recall for every non-`unresolved` option (req 10's
    "per-option precision and recall with Clopper-Pearson bounds and
    `underpowered` under 20"), scored against `gold_label` (options with no
    predicted or gold support get `None` scores, `underpowered` either
    way)."""
    result: dict[str, PrecisionRecall] = {}
    for option in sorted(crosswalk.OPTIONS_WITH_A_CLASS):
        predicted_as = [i for i in items if i.reason == "ok" and i.predicted_option == option]
        gold_as = [i for i in items if i.gold_label == option]
        precision_n, recall_n = len(predicted_as), len(gold_as)
        precision = (
            sum(1 for i in predicted_as if i.gold_label == option) / precision_n
            if precision_n
            else None
        )
        recall = (
            sum(1 for i in gold_as if i.reason == "ok" and i.predicted_option == option) / recall_n
            if recall_n
            else None
        )
        result[option] = PrecisionRecall(
            option=option,
            precision=precision,
            precision_lower_bound=(
                clopper_pearson_lower_bound(round(precision * precision_n), precision_n)
                if precision is not None
                else None
            ),
            precision_n=precision_n,
            recall=recall,
            recall_lower_bound=(
                clopper_pearson_lower_bound(round(recall * recall_n), recall_n)
                if recall is not None
                else None
            ),
            recall_n=recall_n,
            underpowered=recall_n < min_gold,
        )
    return result


@dataclass(frozen=True)
class ArmResult:
    """The `rule_provision` arm's score on the same gold items (req 5, C3):
    `coverage` is the share whose gold class is among the arm's mapped
    options' classes; `class_accuracy` scores only the items where the arm's
    set maps to exactly one class (its own `AccuracyResult`, `n = 0` when the
    arm never resolves to one class for any scored item)."""

    coverage: float
    class_accuracy: AccuracyResult


def rule_provision_arm_score(items: Sequence[ScoredItem]) -> ArmResult:
    """req 5 and C3: `coverage` and, where the arm names one class,
    `class_accuracy`, over every item with a usable `gold_label` and a
    `rule_provision`."""
    scored = [
        item for item in items if item.gold_label not in (None, UNRESOLVED) and item.rule_provision
    ]
    if not scored:
        return ArmResult(coverage=0.0, class_accuracy=AccuracyResult(0, 0, 0.0, 0.0))
    covered = 0
    one_class_n, one_class_correct = 0, 0
    for item in scored:
        gold_class = crosswalk.class_of(item.gold_label)  # type: ignore[arg-type]
        arm_options = crosswalk.rule_provision_arm(item.rule_provision)  # type: ignore[arg-type]
        arm_classes = {crosswalk.class_of(o) for o in arm_options if o != UNRESOLVED}
        if gold_class in arm_classes:
            covered += 1
        if len(arm_classes) == 1:
            one_class_n += 1
            if _predicted_class(item) == next(iter(arm_classes)):
                one_class_correct += 1
    arm_accuracy = AccuracyResult(
        n=one_class_n,
        correct=one_class_correct,
        point_estimate=(one_class_correct / one_class_n) if one_class_n else 0.0,
        lower_bound=wilson_lower_bound(one_class_correct, one_class_n),
    )
    return ArmResult(coverage=covered / len(scored), class_accuracy=arm_accuracy)


# --- calibration: reported, never gating (C6) ------------------------------------


@dataclass(frozen=True)
class CalibrationItem:
    """One scored call's probability vector and its gold class (C6: "on the
    probability vector, never on `vendor_confidence`"). Only items with a
    usable `gold_label` belong here; the caller filters (as `class_accuracy`
    does) before building these."""

    probabilities: Mapping[str, float]
    gold_class: str


def _max_probability_and_class(probabilities: Mapping[str, float]) -> tuple[float, str | None]:
    option, p_max = max(probabilities.items(), key=lambda kv: kv[1])
    predicted_class = None if option == UNRESOLVED else crosswalk.class_of(option)
    return p_max, predicted_class


def _equal_mass_bins(n: int, n_bins: int) -> list[tuple[int, int]]:
    """`n_bins` contiguous index ranges over a sequence of length `n`,
    sorted by confidence ascending, each holding `n // n_bins` items, the
    remainder spread over the last bins (15 equal-mass bins, req 10, C6)."""
    return [((b * n) // n_bins, ((b + 1) * n) // n_bins) for b in range(n_bins)]


def ece(items: Sequence[CalibrationItem], *, n_bins: int = 15) -> float:
    """Expected calibration error with `n_bins` equal-mass bins (C6; req 10):
    items sorted by their chosen option's probability, split into
    equal-mass bins, each bin's `|accuracy - mean confidence|` weighted by
    its share of `items`."""
    if not items:
        return 0.0
    pairs = sorted(
        (
            (p_max, predicted_class == item.gold_class)
            for item, (p_max, predicted_class) in (
                (item, _max_probability_and_class(item.probabilities)) for item in items
            )
        ),
        key=lambda pair: pair[0],
    )
    n = len(pairs)
    total = 0.0
    for start, end in _equal_mass_bins(n, n_bins):
        bucket = pairs[start:end]
        if not bucket:
            continue
        confidence = sum(p for p, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        total += (len(bucket) / n) * abs(accuracy - confidence)
    return total


def multiclass_brier(items: Sequence[CalibrationItem]) -> float:
    """Multiclass Brier score, summed over the five classes, not divided by
    `K` (C6), then averaged over `items`: for each item, the squared
    difference between its per-class probability (the sum of every option's
    probability in that class; `unresolved`'s probability joins no class)
    and the 0/1 indicator of the gold class, summed over the five classes."""
    if not items:
        return 0.0
    classes = sorted(crosswalk.CLASSES)
    total = 0.0
    for item in items:
        class_probabilities = dict.fromkeys(classes, 0.0)
        for option, p in item.probabilities.items():
            if option == UNRESOLVED:
                continue
            option_class = crosswalk.class_of(option)
            assert option_class is not None
            class_probabilities[option_class] += p
        total += sum(
            (class_probabilities[c] - (1.0 if c == item.gold_class else 0.0)) ** 2 for c in classes
        )
    return total / len(items)


def classwise_reliability(
    items: Sequence[CalibrationItem], *, n_bins: int = 15
) -> dict[str, float]:
    """One-vs-rest ECE per class (C6's "classwise reliability"): for class
    `c`, the confidence is each item's `c`-probability (the sum of its
    options' probabilities, as in `multiclass_brier`) and the outcome is
    whether `c` is the gold class, binned the same equal-mass way as `ece`."""
    result: dict[str, float] = {}
    for target_class in sorted(crosswalk.CLASSES):
        pairs = []
        for item in items:
            class_probability = sum(
                p
                for option, p in item.probabilities.items()
                if option != UNRESOLVED and crosswalk.class_of(option) == target_class
            )
            pairs.append((class_probability, item.gold_class == target_class))
        pairs.sort(key=lambda pair: pair[0])
        n = len(pairs)
        total = 0.0
        for start, end in _equal_mass_bins(n, n_bins):
            bucket = pairs[start:end]
            if not bucket:
                continue
            confidence = sum(p for p, _ in bucket) / len(bucket)
            accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
            total += (len(bucket) / n) * abs(accuracy - confidence)
        result[target_class] = total
    return result


def share_at_or_above(items: Sequence[CalibrationItem], threshold: float = 0.999) -> float:
    """The share of `items` whose chosen option's probability is at or above
    `threshold` (C6: "the share of records with p(choice) >= 0.999")."""
    if not items:
        return 0.0
    at_or_above = sum(1 for item in items if max(item.probabilities.values()) >= threshold)
    return at_or_above / len(items)


#: E7's four fixed probability bins, the last closed on both ends.
PROBABILITY_BINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.6),
    (0.6, 0.9),
    (0.9, 0.999),
    (0.999, 1.0),
)


@dataclass(frozen=True)
class AgreementBinRow:
    """One row of C6's "agreement-by-bin table": how many items fell in
    `[lower, upper)` (the last bin closed at `1.0`) by their chosen option's
    probability, and what share of them `agrees` (the caller's own
    definition, e.g. A against B, or the model against the rule)."""

    lower: float
    upper: float
    n: int
    agreement_rate: float


def agreement_by_probability_bin(
    confidence_and_agreement: Sequence[tuple[float, bool]],
) -> tuple[AgreementBinRow, ...]:
    """C6's agreement-by-bin table, over `PROBABILITY_BINS`: each item is its
    chosen option's probability paired with whether it agrees (the caller
    decides what "agrees" means)."""
    rows = []
    for lower, upper in PROBABILITY_BINS:
        in_bin = [
            agrees
            for confidence, agrees in confidence_and_agreement
            if lower <= confidence < upper or (upper == 1.0 and confidence == 1.0)
        ]
        n = len(in_bin)
        rows.append(AgreementBinRow(lower, upper, n, (sum(in_bin) / n) if n else 0.0))
    return tuple(rows)


# --- the drift probe's comparison (req 11, C5) ------------------------------------


@dataclass(frozen=True)
class DriftComparison:
    """One drift-probe packet's answer against its baseline record (C5):
    `option` and `baseline_option` for `flip_rate`; the probability vectors,
    when both are present, for `mean_abs_probability_shift`."""

    listing_end_id: str
    option: str | None
    baseline_option: str
    probabilities: Mapping[str, float] | None = None
    baseline_probabilities: Mapping[str, float] | None = None


def flip_rate(comparisons: Sequence[DriftComparison]) -> float:
    """The share of `comparisons` whose answer changed from its baseline
    (C5's `flip_rate`; req 10's worked example: 1/20 = 0.05 passes a 0.10
    threshold, 2/20 = 0.10 does not, since the bar is strict). `0.0` for no
    comparisons."""
    if not comparisons:
        return 0.0
    flips = sum(1 for c in comparisons if c.option != c.baseline_option)
    return flips / len(comparisons)


def mean_abs_probability_shift(comparisons: Sequence[DriftComparison]) -> float:
    """The mean absolute shift in the chosen option's top probability
    between a drift-probe comparison and its baseline (C5, reported, never
    gating); a comparison missing either probability vector is skipped.
    `0.0` when nothing is comparable."""
    shifts = [
        abs(max(c.probabilities.values()) - max(c.baseline_probabilities.values()))
        for c in comparisons
        if c.probabilities is not None and c.baseline_probabilities is not None
    ]
    return sum(shifts) / len(shifts) if shifts else 0.0

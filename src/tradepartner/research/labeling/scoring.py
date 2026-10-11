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

**The split score** (the 2026-10-11 amendment, #1492, item 3): every labelled
gold item carries the `basis` its label rests on (`text`, `exchange_rule`,
`provision`; none on a gold `unresolved` or an unlabelled item), and a
labelled item without one is refused. The headline numbers (`class_accuracy`,
`worst_case_lower_bound`, `per_option_precision_recall`, the calibration and
the `rule_provision` arm's score) are computed by the caller on
`headline_items` (the `text` set plus the unlabelled items the worst case
counts wrong), so a provision-decided label never scores the arm it agrees
with by construction; `by_basis` gives the `exchange_rule` and `provision`
sets, each scored on its own and never pooled.
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

#: The evidence a gold label rests on (the 2026-10-11 amendment, #1492, item 1):
#: the text shown states the reason, a cited exchange rule names one reason, or
#: the Form 25 provision alone has one meaning. The labeller sets it; nothing here
#: derives it.
Basis = Literal["text", "exchange_rule", "provision"]
BASES: tuple[Basis, ...] = ("text", "exchange_rule", "provision")


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


Arm = Literal["jev", "claude"]
ARMS: tuple[Arm, ...] = ("jev", "claude")
PacketKind = Literal["A", "B"]


@dataclass(frozen=True)
class ArmCall:
    """One model call's record as the evidence ladder reads it (the 2026-10-11
    amendment, #1504, item 3): which `arm` made it, on which packet `kind`
    (`A`: the Form 25 fields and notice; `B`: A plus the one 8-K), the `option`
    it returned (`None` on a `timeout` or `refused` call), its `reason`, the
    vendor's `stop_reason` (only meaningful on a `refused` record), and the
    tokens, cost and latency the record keeps."""

    arm: Arm
    kind: PacketKind
    option: str | None
    reason: Reason
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.arm not in ARMS:
            raise ValueError(f"ArmCall: arm {self.arm!r} is not one of {', '.join(ARMS)}")
        if self.kind not in ("A", "B"):
            raise ValueError(f"ArmCall: kind {self.kind!r} is not 'A' or 'B'")

    @property
    def answer(self) -> str:
        """The option this call answers, `unresolved` for a `timeout`, a
        `refused` record or a call with no option (#1504 item 6)."""
        if self.reason != "ok" or self.option is None:
            return UNRESOLVED
        return self.option


@dataclass(frozen=True)
class ScoredItem:
    """One gold or batch item's scoring inputs (req 10): the owner's labels
    (`gold_label`, the true reason; `text_states`, what the shown text says,
    defaulting to `gold_label` when the owner recorded no difference, Q6;
    both `None` for `unlabelled`, `questions.UNRESOLVED` for a gold
    `unresolved`), the model's final `predicted_option` (`None` for a
    `timeout` or `refused` call, which carries no answer) and `reason`, and,
    for the seeded error set, the pre-fix rule answer it is scored against
    (`is_seed`, `pre_fix_rule_answer`; req 10's "seeded error set").
    `in_random_draw` is `False` only for a seed item added beside the 150
    drawn gold items (req 10: seeds are "excluded from the accuracy
    denominator, except a seed the random draw itself picked" -- a seed that
    the random draw happened to select keeps `in_random_draw=True`, its
    default, and is scored normally; `seeded_recall` itself filters on
    `is_seed` alone and is unaffected by this flag). `basis` is the evidence
    the gold label rests on (#1492): required on a labelled item that is not
    `unresolved`, refused on a gold `unresolved` or unlabelled one."""

    listing_end_id: str
    gold_label: str | None
    predicted_option: str | None
    reason: Reason
    rule_provision: str | None = None
    text_states: str | None = None
    probabilities: Mapping[str, float] | None = None
    is_seed: bool = False
    in_random_draw: bool = True
    pre_fix_rule_answer: crosswalk.RuleAnswer | None = None
    basis: Basis | None = None
    calls: tuple[ArmCall, ...] = ()
    has_eightk: bool = False

    def __post_init__(self) -> None:
        if not self.in_random_draw and not self.is_seed:
            raise ValueError(
                f"{self.listing_end_id}: in_random_draw=False is only for a seed item "
                "(is_seed=True); a non-seed item dropped from every accuracy denominator "
                "would go unnoticed"
            )
        labelled = self.gold_label not in (None, UNRESOLVED)
        if self.basis is not None and self.basis not in BASES:
            raise ValueError(
                f"{self.listing_end_id}: basis {self.basis!r} is not one of {', '.join(BASES)}"
            )
        if labelled and self.basis is None:
            raise ValueError(
                f"{self.listing_end_id}: a labelled item needs a basis (#1492); "
                "an export without the column cannot be scored"
            )
        if not labelled and self.basis is not None:
            raise ValueError(
                f"{self.listing_end_id}: basis {self.basis!r} on a gold "
                f"{'unresolved' if self.gold_label == UNRESOLVED else 'unlabelled'} item"
            )

    @property
    def text_states_label(self) -> str | None:
        """`text_states` when the owner recorded one, else `gold_label`
        (Q6: "the lock copies `gold_label` into it otherwise")."""
        return self.text_states if self.text_states is not None else self.gold_label


def by_basis(items: Sequence[ScoredItem], basis: Basis) -> list[ScoredItem]:
    """The items whose gold label rests on `basis` (#1492, item 3), in input
    order: no gold `unresolved` or unlabelled item is in any of the three
    sets, since neither carries a basis."""
    return [item for item in items if item.basis == basis]


def headline_items(items: Sequence[ScoredItem]) -> list[ScoredItem]:
    """The headline set of #1492 item 3, in input order: the `text`-basis
    items plus the unlabelled ones (`gold_label is None`), which
    `worst_case_lower_bound` adds to its denominator as wrong and every other
    headline function already excludes. An `exchange_rule` or `provision`
    item is never in it, nor is a gold `unresolved` one (excluded from every
    headline denominator anyway)."""
    return [item for item in items if item.basis == "text" or item.gold_label is None]


def basis_counts(items: Sequence[ScoredItem]) -> dict[str, int]:
    """The result row's `basis_counts` (#1492 item 3): how many items in the
    random draw (`in_random_draw`, matching every accuracy denominator) rest
    on each basis, plus the gold `unresolved` and the unlabelled counts."""
    drawn = [item for item in items if item.in_random_draw]
    counts: dict[str, int] = {b: sum(1 for i in drawn if i.basis == b) for b in BASES}
    counts[UNRESOLVED] = sum(1 for item in drawn if item.gold_label == UNRESOLVED)
    counts["unlabelled"] = sum(1 for item in drawn if item.gold_label is None)
    return counts


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
    `unresolved` on a scored item counts wrong, never excluded. A seed item
    not in the random draw (`in_random_draw=False`) is excluded from the
    denominator (req 10)."""
    scored = [
        item
        for item in items
        if item.in_random_draw and _gold_label_of(item, against) not in (None, UNRESOLVED)
    ]
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
    made no claim to score against). A seed item not in the random draw
    (`in_random_draw=False`) is excluded, matching `class_accuracy`'s
    denominator."""
    scored = [
        item
        for item in items
        if item.in_random_draw and _gold_label_of(item, against) != UNRESOLVED
    ]
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
    gold-scored one). A seed item not in the random draw
    (`in_random_draw=False`) is excluded, matching `class_accuracy`'s
    denominator."""
    items = [item for item in items if item.in_random_draw]
    if not items:
        return 0.0
    count = sum(1 for item in items if item.reason != "ok" or item.predicted_option == UNRESOLVED)
    return count / len(items)


def unlabelled_share(items: Sequence[ScoredItem]) -> float:
    """The share of `items` the owner skipped twice (req 10's
    `unlabelled_share`: `gold_label is None`). A seed item not in the random
    draw (`in_random_draw=False`) is excluded, matching `class_accuracy`'s
    denominator."""
    items = [item for item in items if item.in_random_draw]
    if not items:
        return 0.0
    return sum(1 for item in items if item.gold_label is None) / len(items)


#: The crosswalk rows whose seeds `seeded_recall` gates (owner decision on #1351,
#: 2026-10-10, option (a)): a pre-fix answer on row 6 or 7 has an empty consistent
#: set, so the seed is on the shortlist whatever the model says. A seed on rows 1 to
#: 5 is reported by `seeded_report`, never gated: a correct label can agree with the
#: pre-fix rule there, so the gate could fail on a right answer.
GATED_SEED_ROWS: frozenset[int] = frozenset({6, 7})


@dataclass(frozen=True)
class SeedOutcome:
    """One seed's line of `seeded_report`: its pre-fix crosswalk row, whether that
    row is gated (`GATED_SEED_ROWS`), and whether the pre-fix rule answer would have
    put it on the shortlist, and the basis of its gold label (#1492; `None` on a
    gold `unresolved` or unlabelled seed)."""

    listing_end_id: str
    pre_fix_row: int
    gated: bool
    caught: bool
    basis: Basis | None = None


def _seeds(items: Sequence[ScoredItem]) -> list[ScoredItem]:
    return [item for item in items if item.is_seed and item.pre_fix_rule_answer is not None]


def seeded_report(items: Sequence[ScoredItem]) -> tuple[SeedOutcome, ...]:
    """Every seed with a pre-fix rule answer, gated or not (#1351 option (a): the
    seeds off rows 6 and 7 are reported here), in `listing_end_id` order."""
    outcomes = []
    for item in sorted(_seeds(items), key=lambda i: i.listing_end_id):
        assert item.pre_fix_rule_answer is not None  # `_seeds` filters on it
        row = crosswalk.crosswalk_row(item.pre_fix_rule_answer)
        outcomes.append(
            SeedOutcome(
                listing_end_id=item.listing_end_id,
                pre_fix_row=row,
                gated=row in GATED_SEED_ROWS,
                caught=_caught_by_pre_fix_rule(item),
                basis=item.basis,
            )
        )
    return tuple(outcomes)


def seeded_recall(items: Sequence[ScoredItem]) -> float | None:
    """req 10's `seeded_recall` as #1351's option (a) amends it: the share of the
    seeds whose pre-fix rule answer is on crosswalk row 6 or 7 (`GATED_SEED_ROWS`)
    that would have been shortlisted under it -- a disagreement, or a model
    `unresolved`/`timeout` (`crosswalk.is_disagreement`'s own rule: unresolved is
    "neither", but is still always shortlisted, so it counts as caught here).
    `None` when no seed is on those rows (no claim, never a vacuous `1.0`); the
    other seeds are in `seeded_report`."""
    gated = [outcome for outcome in seeded_report(items) if outcome.gated]
    if not gated:
        return None
    return sum(1 for outcome in gated if outcome.caught) / len(gated)


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
    way). Precision's denominator is restricted to items with a scorable
    gold label (not `None`/unlabelled, not `questions.UNRESOLVED`): a model
    call on an item the owner left gold-`unresolved` or unlabelled makes no
    claim precision can be scored against, so it is excluded rather than
    counted a false positive, matching `class_accuracy`'s own exclusions.
    A seed item not in the random draw (`in_random_draw=False`) is excluded
    from both precision and recall, matching `class_accuracy`'s
    denominator."""
    result: dict[str, PrecisionRecall] = {}
    for option in sorted(crosswalk.OPTIONS_WITH_A_CLASS):
        predicted_as = [
            i
            for i in items
            if i.in_random_draw
            and i.reason == "ok"
            and i.predicted_option == option
            and i.gold_label not in (None, UNRESOLVED)
        ]
        gold_as = [i for i in items if i.in_random_draw and i.gold_label == option]
        precision_n, recall_n = len(predicted_as), len(gold_as)
        precision_correct = sum(1 for i in predicted_as if i.gold_label == option)
        recall_correct = sum(
            1 for i in gold_as if i.reason == "ok" and i.predicted_option == option
        )
        precision = precision_correct / precision_n if precision_n else None
        recall = recall_correct / recall_n if recall_n else None
        result[option] = PrecisionRecall(
            option=option,
            precision=precision,
            precision_lower_bound=(
                clopper_pearson_lower_bound(precision_correct, precision_n)
                if precision is not None
                else None
            ),
            precision_n=precision_n,
            recall=recall,
            recall_lower_bound=(
                clopper_pearson_lower_bound(recall_correct, recall_n)
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
    `rule_provision`. A seed item not in the random draw
    (`in_random_draw=False`) is excluded, matching `class_accuracy`'s
    denominator."""
    scored = [
        item
        for item in items
        if item.in_random_draw and item.gold_label not in (None, UNRESOLVED) and item.rule_provision
    ]
    if not scored:
        return ArmResult(coverage=0.0, class_accuracy=AccuracyResult(0, 0, 0.0, 0.0))
    covered = 0
    one_class_n, one_class_correct = 0, 0
    for item in scored:
        assert item.gold_label is not None  # excluded above (not None, not UNRESOLVED)
        assert item.rule_provision is not None  # excluded above (truthy)
        gold_class = crosswalk.class_of(item.gold_label)
        arm_options = crosswalk.rule_provision_arm(item.rule_provision)
        arm_classes = {crosswalk.class_of(o) for o in arm_options if o != UNRESOLVED}
        if gold_class in arm_classes:
            covered += 1
        if len(arm_classes) == 1:
            one_class_n += 1
            # the arm's own accuracy against gold, not the model's agreement
            # with the arm (B8's question: does reading the filing add
            # anything to a field code already has?).
            if gold_class == next(iter(arm_classes)):
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


def _class_probabilities(probabilities: Mapping[str, float]) -> dict[str, float]:
    """Each class's probability mass: the sum of its options' probabilities
    (`unresolved`'s probability joins no class), as `multiclass_brier` and
    `classwise_reliability` both compute it."""
    class_probabilities: dict[str, float] = {}
    for option, p in probabilities.items():
        if option == UNRESOLVED:
            continue
        option_class = crosswalk.class_of(option)
        assert option_class is not None
        class_probabilities[option_class] = class_probabilities.get(option_class, 0.0) + p
    return class_probabilities


def _max_class_probability_and_class(
    probabilities: Mapping[str, float],
) -> tuple[float, str | None]:
    """The predicted *class* (argmax over class probabilities, not over
    options) and its probability -- aggregated the same way
    `multiclass_brier` does, so `ece`'s notion of "confidence" and
    "correct" both operate at the class level rather than mixing an
    option-level confidence with a class-level correctness check. A tie
    between two classes' probabilities breaks on the class name
    (alphabetically first), not on probability-mapping key order, so the
    result does not depend on dict insertion order."""
    class_probabilities = _class_probabilities(probabilities)
    if not class_probabilities:
        return 0.0, None
    predicted_class, p_max = min(class_probabilities.items(), key=lambda kv: (-kv[1], kv[0]))
    return p_max, predicted_class


def _equal_mass_bins(values: Sequence[float], n_bins: int) -> list[tuple[int, int]]:
    """`n_bins` contiguous index ranges over `values` (already sorted
    ascending), each holding about `len(values) // n_bins` items, the
    remainder spread over the last bins (15 equal-mass bins, req 10, C6) --
    except that no boundary falls inside a run of equal values: tied
    confidences are never split across bins, so the result does not depend
    on the input order among ties (E7: the median probability is 1.000, so
    ties are the common case, not an edge case)."""
    n = len(values)
    if n == 0:
        return []
    raw_boundaries = [((b + 1) * n) // n_bins for b in range(n_bins)]
    boundaries: list[int] = []
    for b in raw_boundaries[:-1]:
        while 0 < b < n and values[b] == values[b - 1]:
            b += 1
        boundaries.append(b)
    boundaries.append(n)
    ranges = []
    start = 0
    for b in boundaries:
        if b > start:
            ranges.append((start, b))
            start = b
    return ranges


def _binned_calibration_error(pairs: Sequence[tuple[float, bool]], n_bins: int) -> float:
    """The shared reduction behind `ece` and `classwise_reliability`: `pairs`
    (confidence, is-correct) sorted by confidence ascending, split into
    `n_bins` tie-aware equal-mass bins (`_equal_mass_bins`), each bin's
    `|accuracy - mean confidence|` weighted by its share of `pairs`. `0.0`
    for no pairs."""
    n = len(pairs)
    if n == 0:
        return 0.0
    values = [p for p, _ in pairs]
    total = 0.0
    for start, end in _equal_mass_bins(values, n_bins):
        bucket = pairs[start:end]
        if not bucket:
            continue
        confidence = sum(p for p, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        total += (len(bucket) / n) * abs(accuracy - confidence)
    return total


def ece(items: Sequence[CalibrationItem], *, n_bins: int = 15) -> float:
    """Expected calibration error with `n_bins` equal-mass bins (C6; req 10):
    items sorted by their predicted *class*'s aggregated probability
    (ascending), split into equal-mass bins (ties kept in one bin), each
    bin's `|accuracy - mean confidence|` weighted by its share of `items`.
    "Confidence" and "correct" are both at the class level (as
    `multiclass_brier` and `classwise_reliability` already are): an item's
    confidence is its predicted class's total probability, and it is
    correct when that class equals `gold_class`."""
    if not items:
        return 0.0
    pairs = sorted(
        (
            (p_max, predicted_class == item.gold_class)
            for item, (p_max, predicted_class) in (
                (item, _max_class_probability_and_class(item.probabilities)) for item in items
            )
        ),
        key=lambda pair: pair[0],
    )
    return _binned_calibration_error(pairs, n_bins)


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
        class_probabilities = _class_probabilities(item.probabilities)
        total += sum(
            (class_probabilities.get(c, 0.0) - (1.0 if c == item.gold_class else 0.0)) ** 2
            for c in classes
        )
    return total / len(items)


def classwise_reliability(
    items: Sequence[CalibrationItem], *, n_bins: int = 15
) -> dict[str, float]:
    """One-vs-rest ECE per class (C6's "classwise reliability"): for class
    `c`, the confidence is each item's `c`-probability (the sum of its
    options' probabilities, as in `multiclass_brier`) and the outcome is
    whether `c` is the gold class, binned the same tie-aware equal-mass way
    as `ece` (ties kept in one bin, so the result does not depend on the
    input order among ties). `0.0` for every class when `items` is empty."""
    result: dict[str, float] = {}
    for target_class in sorted(crosswalk.CLASSES):
        pairs = sorted(
            (
                (
                    _class_probabilities(item.probabilities).get(target_class, 0.0),
                    item.gold_class == target_class,
                )
                for item in items
            ),
            key=lambda pair: pair[0],
        )
        result[target_class] = _binned_calibration_error(pairs, n_bins)
    return result


def share_at_or_above(items: Sequence[CalibrationItem], threshold: float = 0.999) -> float:
    """The share of `items` whose chosen option's probability is at or above
    `threshold` (C6: "the share of records with p(choice) >= 0.999"). An
    item with an empty probability vector has no "chosen option" and never
    counts (not a crash)."""
    if not items:
        return 0.0
    at_or_above = sum(
        1 for item in items if item.probabilities and max(item.probabilities.values()) >= threshold
    )
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
    gating); a comparison missing either probability vector, or carrying an
    empty one, is skipped. `0.0` when nothing is comparable."""
    shifts = [
        abs(max(c.probabilities.values()) - max(c.baseline_probabilities.values()))
        for c in comparisons
        if c.probabilities and c.baseline_probabilities
    ]
    return sum(shifts) / len(shifts) if shifts else 0.0


# --- the evidence ladder, the hybrid router, paired comparisons (#1504) -------------
#
# Reported, never gating: nothing below enters `class_accuracy`'s denominator, the
# per-option gate, `seeded_recall` or any bar. Every function reads `ScoredItem.calls`
# and `has_eightk` only; the functions above ignore both.

Level = Literal["E1", "E2", "cascade"]
LADDER_LEVELS: tuple[Level, ...] = ("E1", "E2", "cascade")

#: The sets a ladder table is cut on: the headline `text` set, its 8-K subset
#: (`has_eightk` and the arm's B packet sent), and the two label bases reported apart.
LadderSet = Literal["text", "text_eightk", "exchange_rule", "provision"]
LADDER_SETS: tuple[LadderSet, ...] = ("text", "text_eightk", "exchange_rule", "provision")

#: A level (or its hybrid) with fewer scorable items than this is reported without a
#: claim (#1504 item 5).
MIN_LADDER_ITEMS = 20

_ALPHA = 0.05


@dataclass(frozen=True)
class _Resolved:
    """The answer an arm gives at a level on one item, and the calls it paid for."""

    answer: str
    answering: ArmCall
    paid: tuple[ArmCall, ...]
    b_refused: bool


def _call_of(item: ScoredItem, arm: Arm, kind: PacketKind) -> ArmCall | None:
    found = [c for c in item.calls if c.arm == arm and c.kind == kind]
    if len(found) > 1:
        raise ValueError(f"{item.listing_end_id}: {len(found)} {arm} calls on packet {kind}")
    return found[0] if found else None


#: An item the arm reached (it made some call) but whose packet for the level was never
#: sent (over `max_packet_tokens`): it scores `unresolved`, as the primary counts it, never
#: dropped from the denominator.
_NOT_SENT = _Resolved(UNRESOLVED, ArmCall("jev", "A", None, "refused", "not_sent"), (), False)


def _from(call: ArmCall | None, paid: tuple[ArmCall | None, ...], b_refused: bool) -> _Resolved:
    assert call is not None
    return _Resolved(call.answer, call, tuple(c for c in paid if c), b_refused)


def _resolve(item: ScoredItem, arm: Arm, level: Level) -> _Resolved | None:
    """The level's answer for `arm` on `item`, `None` when Claude made no call on the
    item (an item it never reached is not scored for it; Jev's missing item reads
    `unresolved`); a packet the level needs but
    that was never sent reads `unresolved`, as the primary counts it."""
    if level not in LADDER_LEVELS:
        raise ValueError(f"unknown ladder level {level!r}")
    if level == "cascade" and arm != "jev":
        raise ValueError("the cascade is Jev's registered treatment only")
    a_call = _call_of(item, arm, "A")
    b_call = _call_of(item, arm, "B")
    if a_call is None and b_call is None:
        # Jev is the primary arm: an item it has no record on (both packets over the
        # cap) counts wrong in `class_accuracy`, so it reads `unresolved` here too.
        return _NOT_SENT if arm == "jev" else None
    if level == "E1":
        return _from(a_call, (a_call,), False) if a_call else _NOT_SENT
    if level == "E2":
        if item.has_eightk and b_call is not None:
            return _Resolved(b_call.answer, b_call, (b_call,), False)
        if a_call is None:
            return _NOT_SENT
        return _Resolved(a_call.answer, a_call, (a_call,), item.has_eightk)
    # cascade: B where it ran and resolved, else A
    if b_call is not None and b_call.answer != UNRESOLVED:
        return _Resolved(b_call.answer, b_call, (b_call,), False)
    if a_call is None:
        return _Resolved(
            UNRESOLVED, b_call or _NOT_SENT.answering, (b_call,) if b_call else (), False
        )
    paid = (b_call, a_call) if b_call is not None else (a_call,)
    return _Resolved(a_call.answer, a_call, paid, False)


def level_answer(item: ScoredItem, arm: Arm, level: Level) -> str | None:
    """`arm`'s answer at `level` on `item` (#1504 item 6): `E1` the A call; `E2` the B
    call where the item has an 8-K and B was sent, else the A call (the bundle "up to
    one more document" is the Form 25 alone when there is none, or when B was refused
    before sending, which `LadderRow.b_refused` counts; a B call the vendor refused
    reads `unresolved`); `cascade` (Jev only) B where B ran and resolved, else A. A
    `refused` or `timeout` call reads as `unresolved`. `None` only when Claude made no call on the
    item; a level whose packet was never sent reads `unresolved`."""
    resolved = _resolve(item, arm, level)
    return resolved.answer if resolved else None


def _is_correct(item: ScoredItem, answer: str) -> bool:
    assert item.gold_label is not None
    return answer != UNRESOLVED and crosswalk.class_of(answer) == crosswalk.class_of(
        item.gold_label
    )


def _scorable(items: Sequence[ScoredItem], basis: Basis) -> list[ScoredItem]:
    """The items `class_accuracy` would score (in the draw, a usable gold label) whose
    label rests on `basis`, in input order."""
    return [
        item
        for item in items
        if item.in_random_draw and item.gold_label not in (None, UNRESOLVED) and item.basis == basis
    ]


def _in_set(item: ScoredItem, arm: Arm, set_name: LadderSet) -> bool:
    if set_name == "text_eightk":
        return item.has_eightk and _call_of(item, arm, "B") is not None
    return True


def _set_items(items: Sequence[ScoredItem], arm: Arm, set_name: LadderSet) -> list[ScoredItem]:
    if set_name not in LADDER_SETS:
        raise ValueError(f"unknown ladder set {set_name!r}")
    basis: Basis = "text" if set_name == "text_eightk" else set_name
    return [i for i in _scorable(items, basis) if _in_set(i, arm, set_name)]


def _nearest_rank(values: Sequence[float], q: float) -> float | None:
    """The nearest-rank percentile (`q` in (0, 1]); `None` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


@dataclass(frozen=True)
class LadderRow:
    """One arm and level line of a ladder table on one set (#1504 item 6). `n` is the
    scorable items the arm answered at the level out of `n_eligible` in the set;
    `refused_by_stop_reason` and `timeouts` count the answering calls; the token, cost
    and latency figures are over the calls the level paid for (the cascade's A call
    only when B did not resolve); `cost_per_correct` is `None` with no correct item;
    `lower_bound` is `None` when `underpowered` (under `MIN_LADDER_ITEMS`)."""

    arm: Arm
    level: Level
    set_name: LadderSet
    n_eligible: int
    n: int
    correct: int
    point_estimate: float
    lower_bound: float | None
    unresolved_share: float
    refused_by_stop_reason: Mapping[str, int]
    timeouts: int
    b_refused: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_p50_ms: float | None
    latency_p90_ms: float | None
    cost_per_correct: float | None
    underpowered: bool


def _row(
    arm: Arm,
    level: Level,
    set_name: LadderSet,
    n_eligible: int,
    pairs: Sequence[tuple[ScoredItem, _Resolved]],
) -> LadderRow:
    n = len(pairs)
    correct = sum(1 for item, r in pairs if _is_correct(item, r.answer))
    paid = [call for _, r in pairs for call in r.paid]
    refused: dict[str, int] = {}
    for _, r in pairs:
        if r.answering.reason == "refused":
            key = r.answering.stop_reason or "unknown"
            refused[key] = refused.get(key, 0) + 1
    cost = sum(c.cost_usd for c in paid)
    latencies = [c.latency_ms for c in paid]
    underpowered = n < MIN_LADDER_ITEMS
    return LadderRow(
        arm=arm,
        level=level,
        set_name=set_name,
        n_eligible=n_eligible,
        n=n,
        correct=correct,
        point_estimate=correct / n if n else 0.0,
        lower_bound=None if underpowered else wilson_lower_bound(correct, n),
        unresolved_share=(sum(1 for _, r in pairs if r.answer == UNRESOLVED) / n) if n else 0.0,
        refused_by_stop_reason=dict(sorted(refused.items())),
        timeouts=sum(1 for _, r in pairs if r.answering.reason == "timeout"),
        b_refused=sum(1 for _, r in pairs if r.b_refused),
        input_tokens=sum(c.input_tokens for c in paid),
        output_tokens=sum(c.output_tokens for c in paid),
        cost_usd=cost,
        latency_p50_ms=_nearest_rank(latencies, 0.5),
        latency_p90_ms=_nearest_rank(latencies, 0.9),
        cost_per_correct=(cost / correct) if correct else None,
        underpowered=underpowered,
    )


def _arms_present(items: Sequence[ScoredItem]) -> list[Arm]:
    return [arm for arm in ARMS if any(c.arm == arm for i in items for c in i.calls)]


def _levels_for(arm: Arm) -> tuple[Level, ...]:
    return LADDER_LEVELS if arm == "jev" else ("E1", "E2")


def ladder_table(items: Sequence[ScoredItem]) -> tuple[LadderRow, ...]:
    """The ladder table (#1504 item 6): one `LadderRow` per arm and level (the cascade a
    third level for Jev only) on the `text` set, on its 8-K subset, and on the
    `exchange_rule` and `provision` sets reported apart, never pooled. An arm with no
    call on any item has no lines (absent, not zero). Order: set, arm, level."""
    rows: list[LadderRow] = []
    for set_name in LADDER_SETS:
        for arm in _arms_present(items):
            eligible = _set_items(items, arm, set_name)
            for level in _levels_for(arm):
                pairs = [(i, r) for i in eligible if (r := _resolve(i, arm, level)) is not None]
                rows.append(_row(arm, level, set_name, len(eligible), pairs))
    return tuple(rows)


@dataclass(frozen=True)
class E0Result:
    """E0 (#1504 item 6): the `rule_provision` arm's score on the text set (no call)
    and `decisive_share`, the share of text-set scorable items whose arm set holds
    exactly one class (`decisive` of `n`); `arm.class_accuracy.n == decisive`."""

    arm: ArmResult
    n: int
    decisive: int
    decisive_share: float


def e0_report(items: Sequence[ScoredItem]) -> E0Result:
    """E0 beside the ladder: `rule_provision_arm_score` on the `text` set and
    `decisive_share` over the same set."""
    text = _scorable(items, "text")
    decisive = sum(1 for i in text if is_rule_decisive(i.rule_provision))
    return E0Result(
        arm=rule_provision_arm_score(text),
        n=len(text),
        decisive=decisive,
        decisive_share=decisive / len(text) if text else 0.0,
    )


def _rule_class(provision: str | None) -> str | None:
    """The one class the `rule_provision` arm names, `None` unless it names exactly
    one (`unresolved` dropped first, as `rule_provision_arm_score` drops it)."""
    if not provision:
        return None
    classes = {
        crosswalk.class_of(o) for o in crosswalk.rule_provision_arm(provision) if o != UNRESOLVED
    }
    return next(iter(classes)) if len(classes) == 1 else None


def is_rule_decisive(provision: str | None) -> bool:
    """Whether the `rule_provision` arm decides on `provision` (#1504 item 7): its class
    set, `unresolved` dropped first, holds exactly one class. An unmapped provision's
    `{unresolved}` is never decisive; neither is a missing provision."""
    return _rule_class(provision) is not None


@dataclass(frozen=True)
class RoutedApart:
    """The items the router sent to the rule, reported apart (#1504 item 7): their
    count, gold class mix, and the rule's and the model's accuracy on them (`None`
    with no routed item). The rule's accuracy is near 1 almost by construction on
    `text` labels, so the hybrid's gain over the model is bounded by `model_n -
    model_correct`."""

    count: int
    gold_class_mix: Mapping[str, int]
    rule_correct: int
    model_correct: int
    rule_accuracy: float | None
    model_accuracy: float | None


@dataclass(frozen=True)
class RouterResult:
    """The hybrid's line for one arm and level on one set (#1504 item 7). `cost_usd` is
    the model's cost on the routed-to-model items only (the rule is free)."""

    arm: Arm
    level: Level
    set_name: LadderSet
    n: int
    correct: int
    point_estimate: float
    lower_bound: float | None
    unresolved_share: float
    routed_to_rule_share: float
    cost_usd: float
    cost_per_correct: float | None
    routed: RoutedApart
    underpowered: bool


def _hybrid_outcomes(
    items: Sequence[ScoredItem], arm: Arm, level: Level, set_name: LadderSet
) -> list[tuple[ScoredItem, _Resolved, str | None]]:
    """Each answered item in the set with its model resolution and the rule's class
    where the router sends it to the rule (else `None`)."""
    out = []
    for item in _set_items(items, arm, set_name):
        resolved = _resolve(item, arm, level)
        if resolved is not None:
            out.append((item, resolved, _rule_class(item.rule_provision)))
    return out


def _hybrid_correct(item: ScoredItem, resolved: _Resolved, rule_class: str | None) -> bool:
    assert item.gold_label is not None
    if rule_class is not None:
        return rule_class == crosswalk.class_of(item.gold_label)
    return _is_correct(item, resolved.answer)


def hybrid_router(
    items: Sequence[ScoredItem],
    arm: Arm,
    level: Level,
    *,
    set_name: LadderSet = "text",
) -> RouterResult:
    """The rule-first hybrid, computed and never run (#1504 item 7): where
    `is_rule_decisive` the rule's class is the answer, else the level's answer. Only
    `text` and `text_eightk` sets are routed: a `provision`-basis label agrees with the
    rule by construction and never enters this score."""
    if set_name not in ("text", "text_eightk"):
        raise ValueError("the router scores the text set (and its 8-K subset) only")
    rows = _hybrid_outcomes(items, arm, level, set_name)
    n = len(rows)
    correct = sum(1 for item, r, rc in rows if _hybrid_correct(item, r, rc))
    to_model = [(item, r) for item, r, rc in rows if rc is None]
    routed = [(item, r, rc) for item, r, rc in rows if rc is not None]
    unresolved = sum(1 for _, r in to_model if r.answer == UNRESOLVED)
    cost = sum(c.cost_usd for _, r in to_model for c in r.paid)
    mix: dict[str, int] = {}
    for item, _, _ in routed:
        assert item.gold_label is not None
        gold_class = crosswalk.class_of(item.gold_label)
        assert gold_class is not None
        mix[gold_class] = mix.get(gold_class, 0) + 1
    rule_correct = sum(1 for item, _, rc in routed if _hybrid_correct(item, _NO_MODEL, rc))
    model_correct = sum(1 for item, r, _ in routed if _is_correct(item, r.answer))
    underpowered = n < MIN_LADDER_ITEMS
    return RouterResult(
        arm=arm,
        level=level,
        set_name=set_name,
        n=n,
        correct=correct,
        point_estimate=correct / n if n else 0.0,
        lower_bound=None if underpowered else wilson_lower_bound(correct, n),
        unresolved_share=unresolved / n if n else 0.0,
        routed_to_rule_share=len(routed) / n if n else 0.0,
        cost_usd=cost,
        cost_per_correct=(cost / correct) if correct else None,
        routed=RoutedApart(
            count=len(routed),
            gold_class_mix=dict(sorted(mix.items())),
            rule_correct=rule_correct,
            model_correct=model_correct,
            rule_accuracy=rule_correct / len(routed) if routed else None,
            model_accuracy=model_correct / len(routed) if routed else None,
        ),
        underpowered=underpowered,
    )


#: A placeholder resolution for scoring the rule's answer alone (never read: the rule
#: branch of `_hybrid_correct` ignores it).
_NO_MODEL = _Resolved(UNRESOLVED, ArmCall("jev", "A", None, "ok"), (), False)


def mcnemar_exact(b: int, c: int) -> float:
    """McNemar's exact two-sided test on the discordant pairs (#1504 item 8): the
    binomial probability, at p = 0.5, of a split at least as uneven as `(b, c)`; no
    continuity correction, no asymptotic form. `1.0` when `b + c == 0`. Symmetric."""
    if b < 0 or c < 0:
        raise ValueError(f"discordant counts must be non-negative, got ({b}, {c})")
    m = b + c
    if m == 0:
        return 1.0
    tail = sum(math.comb(m, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / (1 << m))


def min_significant_split(m: int, *, alpha: float = _ALPHA) -> int | None:
    """The smallest majority count `k` such that a `k`-to-`(m - k)` split of `m`
    discordant pairs has an exact two-sided p of at most `alpha` (#1504 item 8's
    "minimum split that reaches 0.05": `m = 6` gives 6, `m = 20` gives 15); `None`
    when even `m`-to-0 does not reach it."""
    for k in range(math.ceil(m / 2), m + 1):
        if mcnemar_exact(k, m - k) <= alpha:
            return k
    return None


@dataclass(frozen=True)
class Comparison:
    """One paired comparison (#1504 item 8) between `first` and `second` (labels such
    as `jev:E1` or `claude:E2:hybrid`) over the same items: `b` first right and second
    wrong, `c` first wrong and second right, `n` items paired, the accuracy difference
    (first minus second) and the exact p. `min_split` is the smallest majority count
    that would reach 0.05 at this `b + c` (the claim quotes it beside the p)."""

    first: str
    second: str
    set_name: LadderSet
    n: int
    b: int
    c: int
    accuracy_difference: float
    p_value: float
    min_split: int | None
    underpowered: bool


_ARM_LEVELS: tuple[Level, ...] = ("E1", "E2")
_Spec = tuple[Arm, Level, bool]  # (arm, level, hybrid)


def _label(spec: _Spec) -> str:
    arm, level, hybrid = spec
    return f"{arm}:{level}" + (":hybrid" if hybrid else "")


def _spec_outcomes(
    items: Sequence[ScoredItem], spec: _Spec, set_name: LadderSet
) -> dict[int, bool]:
    """Item index -> whether the spec was right, over the set's answered items."""
    arm, level, hybrid = spec
    index = {id(item): k for k, item in enumerate(items)}
    out: dict[int, bool] = {}
    for item, resolved, rule_class in _hybrid_outcomes(items, arm, level, set_name):
        out[index[id(item)]] = (
            _hybrid_correct(item, resolved, rule_class)
            if hybrid
            else _is_correct(item, resolved.answer)
        )
    return out


def _compare(
    items: Sequence[ScoredItem], first: _Spec, second: _Spec, set_name: LadderSet
) -> Comparison:
    one = _spec_outcomes(items, first, set_name)
    two = _spec_outcomes(items, second, set_name)
    shared = sorted(one.keys() & two.keys())
    b = sum(1 for k in shared if one[k] and not two[k])
    c = sum(1 for k in shared if not one[k] and two[k])
    n = len(shared)
    return Comparison(
        first=_label(first),
        second=_label(second),
        set_name=set_name,
        n=n,
        b=b,
        c=c,
        accuracy_difference=(b - c) / n if n else 0.0,
        p_value=mcnemar_exact(b, c),
        min_split=min_significant_split(b + c),
        underpowered=n < MIN_LADDER_ITEMS,
    )


def paired_comparisons(items: Sequence[ScoredItem]) -> tuple[Comparison, ...]:
    """The twelve comparisons of #1504 item 8 on the `text` set and the same twelve on
    its 8-K subset (so up to twenty-four; no multiplicity correction, none claimed):
    `jev` against `claude` at E1, at E2 and on each level's hybrid (four); E1 against
    E2 within each arm, and Jev's E2 against the cascade (three); each level against
    its own hybrid (five: Jev's three levels, Claude's two). Pairs are over the same
    items (the intersection of what both sides answered). A comparison involving an
    arm with no calls is absent. Order: set, then the order above."""
    arms = _arms_present(items)
    out: list[Comparison] = []
    for set_name in ("text", "text_eightk"):
        pairs: list[tuple[_Spec, _Spec]] = []
        if "jev" in arms and "claude" in arms:
            for hybrid in (False, True):
                for level in _ARM_LEVELS:
                    pairs.append((("jev", level, hybrid), ("claude", level, hybrid)))
        for arm in arms:
            pairs.append(((arm, "E1", False), (arm, "E2", False)))
        if "jev" in arms:
            pairs.append((("jev", "E2", False), ("jev", "cascade", False)))
        for arm in arms:
            for level in _levels_for(arm):
                pairs.append(((arm, level, False), (arm, level, True)))
        out.extend(_compare(items, a, b, set_name) for a, b in pairs)
    return tuple(out)

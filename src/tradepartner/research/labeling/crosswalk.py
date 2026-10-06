"""Classes, the crosswalk and the deterministic `rule_provision` arm, pure.

Research-labeling spec req 5 (docs/specs/research-labeling.md) and its 2026-10-06
amendment C3 (the arm stays unchanged) and C13 (the sentinel columns this module
never reads: it takes only the rule answer's four fields, never a frame row, a gold
column or a model field). Pure: no I/O, no store, no network, no model client.

**Classes** partition the eight non-`unresolved` options (`questions.UNRESOLVED` has
no class) for scoring and display.

**The crosswalk** (`RuleAnswer` -> a `crosswalk_row` 1 to 7 -> `consistent_options`)
is the shortlist's definition of disagreement: a model label outside the row's
consistent set is a disagreement (`is_disagreement`); `unresolved` is neither a
disagreement nor an agreement, by req 5's "Classes, crosswalk and the deterministic
arm" and the human-path req 9's "a label outside the subset is a disagreement,
`unresolved` is neither". Rows 6 and 7 (`rule_status` not `delisted`, or no
`delistings` row joined) have an empty consistent set, so disagree with every label.

**The `rule_provision` arm** (`rule_provision_arm`) maps the Form 25's normalised
provision (`12d2-2(a)(1)` etc., stripped of the `17 CFR 240.` prefix by the corpus
fetch) onto a set of options by the rule's own text (17 CFR 240.12d2-2); an unknown
provision maps to `{"unresolved"}`. It is scored on the gold items as coverage and,
where its set is one class, as class accuracy (req 5, C3).

**`shortlist`** turns a batch's inference outcomes into the human-reviewed shortlist
of req 9: every disagreement, every `unresolved` (a `timeout` record included, since
it carries no answer), and a seeded sample of the agreements, each item carrying its
`stratum` and (for the agreement stratum) its sampling rate; items beyond `max_items`
in acceptance order are `deferred` and counted, never dropped.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

#: `questions.UNRESOLVED` is the same string; kept local so this module has no
#: dependency on `questions.py` (the dependency runs the other way: `questions`
#: validates its option set against this module's classes).
UNRESOLVED = "unresolved"

RuleStatus = Literal["transferred", "delisted", "listed", "unmatched"]
Stratum = Literal["disagreement", "unresolved", "agreement_sample"]

#: The five classes, a true partition of the eight non-`unresolved` options (req 5).
CLASSES: dict[str, frozenset[str]] = {
    "transfer": frozenset({"exchange_transfer"}),
    "continuity": frozenset({"redomicile_or_reorganisation"}),
    "insolvency": frozenset({"bankruptcy"}),
    "terminal": frozenset({"merger_or_acquisition", "going_private", "instrument_retirement"}),
    "removal": frozenset({"compliance_delisting", "voluntary_withdrawal"}),
}

#: Every option a class covers, for `class_of` and the "exactly one class" check.
OPTIONS_WITH_A_CLASS: frozenset[str] = frozenset().union(*CLASSES.values())


def class_of(option: str) -> str | None:
    """The class `option` belongs to, or `None` for `unresolved` (no class).

    Raises `KeyError` for any other name outside the nine options (req 4, req 5):
    a typo should fail loudly rather than silently read as "no class".
    """
    if option == UNRESOLVED:
        return None
    for class_name, options in CLASSES.items():
        if option in options:
            return class_name
    raise KeyError(f"{option!r} is not one of the nine options")


@dataclass(frozen=True)
class RuleAnswer:
    """The rule answer for one listing end, at the frame's `t` (req 5's crosswalk
    columns: `rule_status`, `rule_relisted`, `rule_successor_id`,
    `rule_form15_in_window`). Built by the frame build (T122); read here only.
    """

    status: RuleStatus
    #: The same security relisted after the effective day (#820: CMPR, CG, WELL,
    #: Seagate 2021). Only meaningful when `status == "delisted"`.
    relisted: bool = False
    #: A successor security's id, when new equity was created (#820); `None` when
    #: none was. Only meaningful when `status == "delisted"`.
    successor_id: str | None = None
    #: A Form 15 marker filing fell inside the reorganisation window (the master's
    #: `reorganisation_window_sessions` over the XNYS calendar). Only meaningful
    #: when `status == "delisted"` with no relisting and no successor.
    form15_in_window: bool = False


def crosswalk_row(answer: RuleAnswer) -> int:
    """Which crosswalk row (1 to 7) `answer` falls under (req 5's table)."""
    if answer.status == "transferred":
        return 1
    if answer.status == "listed":
        return 6
    if answer.status == "unmatched":
        return 7
    if answer.status != "delisted":
        raise ValueError(f"unknown rule status {answer.status!r}")
    if answer.relisted:
        return 2
    if answer.successor_id is not None:
        return 3
    if answer.form15_in_window:
        return 4
    return 5


#: Row -> consistent option set (req 5's table). Rows 6 and 7 are empty: a
#: disagreement by construction, whatever the model says.
ROW_CONSISTENT_OPTIONS: dict[int, frozenset[str]] = {
    1: frozenset({"exchange_transfer"}),
    2: frozenset({"redomicile_or_reorganisation"}),
    3: frozenset({"bankruptcy", "redomicile_or_reorganisation"}),
    4: frozenset(
        {
            "merger_or_acquisition",
            "going_private",
            "redomicile_or_reorganisation",
            "instrument_retirement",
        }
    ),
    5: frozenset(
        {
            "merger_or_acquisition",
            "going_private",
            "compliance_delisting",
            "voluntary_withdrawal",
            "instrument_retirement",
            "bankruptcy",
        }
    ),
    6: frozenset(),
    7: frozenset(),
}


def consistent_options(answer: RuleAnswer) -> frozenset[str]:
    """The crosswalk's consistent option set for `answer`'s row (req 5)."""
    return ROW_CONSISTENT_OPTIONS[crosswalk_row(answer)]


def is_disagreement(answer: RuleAnswer, option: str) -> bool:
    """Whether `option` disagrees with `answer`'s row (req 5, req 9).

    `unresolved` is neither an agreement nor a disagreement (req 9: "`unresolved`
    is neither"); rows 6 and 7 disagree with every other option, by construction.
    """
    if option == UNRESOLVED:
        return False
    return option not in consistent_options(answer)


#: The `rule_provision` arm (req 5's "Deterministic comparison arm"; C3: unchanged).
#: Keyed on the corpus fetch's normalised form (the `17 CFR 240.` prefix stripped,
#: e.g. `12d2-2(a)(2)`).
RULE_PROVISION_ARM: dict[str, frozenset[str]] = {
    "12d2-2(a)(1)": frozenset({"instrument_retirement"}),
    "12d2-2(a)(2)": frozenset({"instrument_retirement"}),
    "12d2-2(a)(3)": frozenset(
        {
            "merger_or_acquisition",
            "going_private",
            "redomicile_or_reorganisation",
            "bankruptcy",
        }
    ),
    "12d2-2(a)(4)": frozenset({"instrument_retirement", "bankruptcy"}),
    "12d2-2(b)": frozenset({"compliance_delisting", "bankruptcy"}),
    "12d2-2(c)": frozenset({"exchange_transfer", "voluntary_withdrawal"}),
}


def rule_provision_arm(provision: str) -> frozenset[str]:
    """The arm's option set for a normalised `rule_provision`, or `{"unresolved"}`
    for anything it does not map (req 5)."""
    return RULE_PROVISION_ARM.get(provision, frozenset({UNRESOLVED}))


@dataclass(frozen=True)
class InferenceOutcome:
    """What `shortlist` needs from one listing end's labeling outcome: the rule
    answer, the model's final label (per the packet rule's "last call's answer";
    `None` for a `timeout`, which carries no answer), whether the call resolved
    (`reason`, from the model client: `"ok"`, `"timeout"` or `"refused"`), and the
    listing end's acceptance time for the batch's acceptance order (req 9's
    `max_items` deferral)."""

    listing_end_id: str
    accepted_at: datetime
    rule_answer: RuleAnswer
    selected_option: str | None
    reason: Literal["ok", "timeout", "refused"]


@dataclass(frozen=True)
class ShortlistItem:
    """One shortlisted listing end (req 9): its `stratum`, and, for the agreement
    stratum, the sampling rate it was drawn at (`1.0` for every other stratum,
    since every disagreement and `unresolved` item is always shortlisted)."""

    listing_end_id: str
    stratum: Stratum
    sampling_rate: float
    deferred: bool


@dataclass(frozen=True)
class Shortlist:
    """`shortlist`'s result: the items (active and deferred alike, in acceptance
    order) and the deferred count (req 9's "counted, and carried to the next
    batch's selector" -- the carry-over itself is the job's, not this module's)."""

    items: tuple[ShortlistItem, ...]
    n_deferred: int


def _stratum(outcome: InferenceOutcome) -> Stratum | None:
    """`outcome`'s stratum, or `None` for an agreement (not yet sampled)."""
    option = outcome.selected_option
    if outcome.reason != "ok" or option is None or option == UNRESOLVED:
        return "unresolved"
    if is_disagreement(outcome.rule_answer, option):
        return "disagreement"
    return None


def shortlist(
    outcomes: list[InferenceOutcome],
    *,
    seed: int,
    agreement_sample_size: int = 30,
    max_items: int = 200,
) -> Shortlist:
    """The human-reviewed shortlist over a batch's `outcomes` (req 9).

    Every disagreement and every `unresolved` (a `timeout` included) is
    shortlisted; `agreement_sample_size` of the remaining agreements are drawn
    under `seed` (stable across calls: the candidates are sorted by
    `listing_end_id` before the draw, so the result does not depend on the input
    order). The combined set, in acceptance order (`accepted_at`, ties broken by
    `listing_end_id`), is kept active up to `max_items`; the rest are marked
    `deferred` and counted, never dropped.
    """
    disagreements: list[InferenceOutcome] = []
    unresolved: list[InferenceOutcome] = []
    agreements: list[InferenceOutcome] = []
    for outcome in outcomes:
        stratum = _stratum(outcome)
        if stratum == "disagreement":
            disagreements.append(outcome)
        elif stratum == "unresolved":
            unresolved.append(outcome)
        else:
            agreements.append(outcome)

    n_agreements = len(agreements)
    sample_size = min(agreement_sample_size, n_agreements)
    sampling_rate = (sample_size / n_agreements) if n_agreements else 0.0
    ordered_agreements = sorted(agreements, key=lambda o: o.listing_end_id)
    sampled = random.Random(seed).sample(ordered_agreements, sample_size) if sample_size else []

    entries: list[tuple[InferenceOutcome, Stratum, float]] = [
        (o, "disagreement", 1.0) for o in disagreements
    ]
    entries += [(o, "unresolved", 1.0) for o in unresolved]
    entries += [(o, "agreement_sample", sampling_rate) for o in sampled]

    entries.sort(key=lambda e: (e[0].accepted_at, e[0].listing_end_id))

    items = tuple(
        ShortlistItem(
            listing_end_id=outcome.listing_end_id,
            stratum=stratum,
            sampling_rate=rate,
            deferred=index >= max_items,
        )
        for index, (outcome, stratum, rate) in enumerate(entries)
    )
    n_deferred = sum(1 for item in items if item.deferred)
    return Shortlist(items=items, n_deferred=n_deferred)

"""`crosswalk` (plan T121; spec req 5, docs/specs/research-labeling.md)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradepartner.research.labeling.crosswalk import (
    CLASSES,
    UNRESOLVED,
    InferenceOutcome,
    RuleAnswer,
    class_of,
    consistent_options,
    crosswalk_row,
    is_disagreement,
    rule_provision_arm,
    shortlist,
)


def _at(day: int) -> datetime:
    return datetime(2020, 1, day, tzinfo=UTC)


# --- req 5's seven crosswalk rows, one fixture `RuleAnswer` each -----------------

ROW_1 = RuleAnswer(status="transferred")
ROW_2 = RuleAnswer(status="delisted", relisted=True)
ROW_3 = RuleAnswer(status="delisted", successor_id="sec-successor")
ROW_4 = RuleAnswer(status="delisted", form15_in_window=True)
ROW_5 = RuleAnswer(status="delisted")
ROW_6 = RuleAnswer(status="listed")
ROW_7 = RuleAnswer(status="unmatched")

ROWS = {1: ROW_1, 2: ROW_2, 3: ROW_3, 4: ROW_4, 5: ROW_5, 6: ROW_6, 7: ROW_7}

EXPECTED_CONSISTENT_OPTIONS = {
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


@pytest.mark.parametrize("row_number", sorted(ROWS))
def test_crosswalk_row_and_consistent_options(row_number: int) -> None:
    answer = ROWS[row_number]
    assert crosswalk_row(answer) == row_number
    assert consistent_options(answer) == EXPECTED_CONSISTENT_OPTIONS[row_number]


def test_priority_order_relisted_beats_successor_and_form15() -> None:
    # A row with both a successor and form15_in_window set, plus relisted, is row 2:
    # relisted is checked first (req 5's table lists it that way; #820's relisting
    # rule takes priority over the successor rule in the master too).
    answer = RuleAnswer(
        status="delisted", relisted=True, successor_id="sec-x", form15_in_window=True
    )
    assert crosswalk_row(answer) == 2


def test_successor_beats_form15_in_window() -> None:
    answer = RuleAnswer(status="delisted", successor_id="sec-x", form15_in_window=True)
    assert crosswalk_row(answer) == 3


def test_unknown_status_raises() -> None:
    with pytest.raises(ValueError, match="unknown rule status"):
        crosswalk_row(RuleAnswer(status="bogus"))  # type: ignore[arg-type]


def test_a_label_outside_the_subset_is_a_disagreement() -> None:
    assert is_disagreement(ROW_1, "bankruptcy") is True
    assert is_disagreement(ROW_1, "exchange_transfer") is False


def test_unresolved_is_neither() -> None:
    for answer in ROWS.values():
        assert is_disagreement(answer, UNRESOLVED) is False


@pytest.mark.parametrize("row_number", (6, 7))
def test_rows_6_and_7_disagree_with_every_label(row_number: int) -> None:
    answer = ROWS[row_number]
    for option in (
        CLASSES["terminal"]
        | CLASSES["removal"]
        | CLASSES["insolvency"]
        | CLASSES["continuity"]
        | CLASSES["transfer"]
    ):
        assert is_disagreement(answer, option) is True


def test_class_of_partitions_the_eight_options() -> None:
    all_options = {o for options in CLASSES.values() for o in options}
    assert len(all_options) == 8
    seen = set()
    for class_name, options in CLASSES.items():
        for option in options:
            assert class_of(option) == class_name
            assert option not in seen, f"{option} appears in more than one class"
            seen.add(option)
    assert seen == all_options


def test_class_of_unresolved_is_none() -> None:
    assert class_of(UNRESOLVED) is None


def test_class_of_unknown_option_raises() -> None:
    with pytest.raises(KeyError):
        class_of("not_a_real_option")


# --- the `rule_provision` arm -----------------------------------------------------


@pytest.mark.parametrize(
    ("provision", "expected"),
    [
        ("12d2-2(a)(1)", frozenset({"instrument_retirement"})),
        ("12d2-2(a)(2)", frozenset({"instrument_retirement"})),
        (
            "12d2-2(a)(3)",
            frozenset(
                {
                    "merger_or_acquisition",
                    "going_private",
                    "redomicile_or_reorganisation",
                    "bankruptcy",
                }
            ),
        ),
        ("12d2-2(a)(4)", frozenset({"instrument_retirement", "bankruptcy"})),
        ("12d2-2(b)", frozenset({"compliance_delisting", "bankruptcy"})),
        ("12d2-2(c)", frozenset({"exchange_transfer", "voluntary_withdrawal"})),
    ],
)
def test_rule_provision_arm_known_paragraphs(provision: str, expected: frozenset[str]) -> None:
    assert rule_provision_arm(provision) == expected


def test_rule_provision_arm_a4() -> None:
    assert rule_provision_arm("12d2-2(a)(4)") == frozenset({"instrument_retirement", "bankruptcy"})


def test_rule_provision_arm_unknown_provision() -> None:
    assert rule_provision_arm("not-a-real-provision") == frozenset({UNRESOLVED})
    assert rule_provision_arm("") == frozenset({UNRESOLVED})


# --- `shortlist`: strata, seed stability, deferral --------------------------------


def _outcome(
    listing_end_id: str,
    day: int,
    rule_answer: RuleAnswer,
    selected_option: str | None,
    reason: str = "ok",
) -> InferenceOutcome:
    return InferenceOutcome(
        listing_end_id=listing_end_id,
        accepted_at=_at(day),
        rule_answer=rule_answer,
        selected_option=selected_option,
        reason=reason,  # type: ignore[arg-type]
    )


def test_shortlist_strata() -> None:
    outcomes = [
        _outcome("disagree-1", 1, ROW_1, "bankruptcy"),  # disagreement
        _outcome("agree-1", 2, ROW_1, "exchange_transfer"),  # agreement
        _outcome("agree-2", 3, ROW_1, "exchange_transfer"),  # agreement
        _outcome("unresolved-1", 4, ROW_1, UNRESOLVED),  # unresolved
        _outcome("timeout-1", 5, ROW_1, None, reason="timeout"),  # unresolved (timeout)
    ]
    result = shortlist(outcomes, seed=1, agreement_sample_size=30)
    by_id = {item.listing_end_id: item for item in result.items}
    assert by_id["disagree-1"].stratum == "disagreement"
    assert by_id["disagree-1"].sampling_rate == 1.0
    assert by_id["unresolved-1"].stratum == "unresolved"
    assert by_id["timeout-1"].stratum == "unresolved"
    assert by_id["agree-1"].stratum == "agreement_sample"
    assert by_id["agree-2"].stratum == "agreement_sample"
    assert len(result.items) == 5
    assert result.n_deferred == 0


def test_shortlist_agreement_sample_size_and_rate() -> None:
    agreements = [_outcome(f"agree-{i}", i, ROW_1, "exchange_transfer") for i in range(1, 11)]
    result = shortlist(agreements, seed=42, agreement_sample_size=3)
    sampled = [item for item in result.items if item.stratum == "agreement_sample"]
    assert len(sampled) == 3
    for item in sampled:
        assert item.sampling_rate == pytest.approx(0.3)


def test_shortlist_seed_stability() -> None:
    agreements = [_outcome(f"agree-{i}", i, ROW_1, "exchange_transfer") for i in range(1, 21)]
    first = shortlist(agreements, seed=7, agreement_sample_size=5)
    second = shortlist(agreements, seed=7, agreement_sample_size=5)
    assert [i.listing_end_id for i in first.items] == [i.listing_end_id for i in second.items]

    # The draw does not depend on the input order (candidates are sorted by
    # listing_end_id before the seeded sample is taken).
    shuffled = list(reversed(agreements))
    third = shortlist(shuffled, seed=7, agreement_sample_size=5)
    assert {i.listing_end_id for i in third.items if i.stratum == "agreement_sample"} == {
        i.listing_end_id for i in first.items if i.stratum == "agreement_sample"
    }


def test_shortlist_different_seed_can_change_the_sample() -> None:
    agreements = [_outcome(f"agree-{i}", i, ROW_1, "exchange_transfer") for i in range(1, 21)]
    first = shortlist(agreements, seed=1, agreement_sample_size=5)
    second = shortlist(agreements, seed=2, agreement_sample_size=5)
    first_sample = {i.listing_end_id for i in first.items if i.stratum == "agreement_sample"}
    second_sample = {i.listing_end_id for i in second.items if i.stratum == "agreement_sample"}
    assert first_sample != second_sample


def test_shortlist_deferral_beyond_max_items_in_acceptance_order() -> None:
    outcomes = [
        _outcome(f"item-{i}", i, ROW_1, "bankruptcy") for i in range(1, 6)
    ]  # 5 disagreements
    result = shortlist(outcomes, seed=1, max_items=3)
    assert len(result.items) == 5
    assert result.n_deferred == 2
    active = {i.listing_end_id for i in result.items if not i.deferred}
    assert active == {"item-1", "item-2", "item-3"}
    deferred = {i.listing_end_id for i in result.items if i.deferred}
    assert deferred == {"item-4", "item-5"}


def test_shortlist_agreement_sample_smaller_than_requested() -> None:
    agreements = [_outcome(f"agree-{i}", i, ROW_1, "exchange_transfer") for i in range(1, 4)]
    result = shortlist(agreements, seed=1, agreement_sample_size=30)
    sampled = [item for item in result.items if item.stratum == "agreement_sample"]
    assert len(sampled) == 3
    for item in sampled:
        assert item.sampling_rate == 1.0


def test_shortlist_empty_input() -> None:
    result = shortlist([], seed=1)
    assert result.items == ()
    assert result.n_deferred == 0

"""Tests for tradepartner.store.dividend_reviews (#1613, T186): the reviewed
list of equal-amount cash dividend groups, its loader and the pure rule."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from tradepartner.store.dividend_reviews import (
    COLUMNS,
    ReviewedGroup,
    ReviewsError,
    equal_amount_groups,
    parse_reviews,
    reviewed_groups,
)

HEADER = ",".join(COLUMNS)
GOOD = "0000000001,2021-06-15,0.25,duplicate,id-b,AAA,raw records differ by cusip only"
#: The owner-side raw export the first file was built from (read-only; absent in CI).
RAW_EXPORT = Path.home() / "tradepartner-probes" / "dupdiv" / "raw_groups.json"


def _text(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_the_loader_reads_typed_records() -> None:
    [group] = parse_reviews(_text(GOOD))
    assert group == ReviewedGroup(
        security_id="0000000001",
        ex_date=date(2021, 6, 15),
        amount=0.25,
        verdict="duplicate",
        retract_source_action_id="id-b",
        ticker="AAA",
        evidence="raw records differ by cusip only",
    )
    assert group.key == ("0000000001", date(2021, 6, 15), 0.25)


@pytest.mark.parametrize(
    ("row", "why"),
    [
        ("0000000001,2021-06-15,0.25,dupe,id-b,AAA,x", "verdict 'dupe'"),
        ("0000000001,2021-06-12,0.25,distinct,,AAA,x", "not an XNYS session"),  # a Saturday
        ("0000000001,2021-13-01,0.25,distinct,,AAA,x", "not a date"),
        ("0000000001,2021-06-15,0,distinct,,AAA,x", "not positive"),
        ("0000000001,2021-06-15,-0.1,distinct,,AAA,x", "not positive"),
        ("0000000001,2021-06-15,nan,distinct,,AAA,x", "not positive"),
        ("0000000001,2021-06-15,abc,distinct,,AAA,x", "not a number"),
        ("0000000001,2021-06-15,0.25,distinct,id-b,AAA,x", "distinct row has a retract"),
        ("0000000001,2021-06-15,0.25,unresolved,id-b,AAA,x", "unresolved row has a retract"),
        ("0000000001,2021-06-15,0.25,duplicate,,AAA,x", "needs retract_source_action_id"),
        ("0000000001,2021-06-15,0.25,duplicate,id-b,AAA", "6 fields, not 7"),
        (",2021-06-15,0.25,distinct,,AAA,x", "empty security_id"),
    ],
)
def test_the_loader_refuses_a_malformed_row_naming_it(row: str, why: str) -> None:
    with pytest.raises(ReviewsError, match=rf"line 3: .*{why}"):
        parse_reviews(_text(GOOD.replace("0.25", "0.5"), row))


def test_the_loader_refuses_a_key_listed_twice_and_another_header() -> None:
    other = GOOD.replace("duplicate,id-b", "distinct,")
    with pytest.raises(ReviewsError, match=r"line 3: key .* is listed again \(line 2\)"):
        parse_reviews(_text(GOOD, other))
    with pytest.raises(ReviewsError, match="line 1: header"):
        parse_reviews("security_id,ex_date\n" + GOOD + "\n")
    with pytest.raises(ReviewsError, match="line 1: header"):
        parse_reviews("")


def test_the_amount_is_the_stored_double_exactly() -> None:
    [group] = parse_reviews(_text(GOOD.replace("0.25", "0.1515334")))
    assert group.amount == 0.1515334
    assert group.amount != 0.151533


def test_the_committed_list_pins_its_counts() -> None:
    """183 groups at the amendment (2026-10-12): a later edit is deliberate."""
    groups = reviewed_groups()
    assert Counter(g.verdict for g in groups) == {"duplicate": 169, "distinct": 11, "unresolved": 3}
    assert all(g.evidence for g in groups)
    assert min(g.ex_date for g in groups) == date(2019, 9, 27)
    assert max(g.ex_date for g in groups) == date(2026, 9, 30)


@pytest.mark.parametrize(
    ("ticker", "ex_date", "amount", "verdict"),
    [
        ("AIV", date(2020, 11, 3), 8.2, "duplicate"),
        ("TDG", date(2025, 9, 2), 90.0, "duplicate"),
        ("MKTW", date(2026, 3, 18), 0.2, "duplicate"),
        ("F", date(2025, 2, 18), 0.15, "distinct"),
        ("CHRD", date(2023, 11, 13), 1.25, "distinct"),
        ("MKTW", date(2025, 8, 15), 0.2, "distinct"),
        ("STX", date(2021, 5, 11), 0.67, "unresolved"),
    ],
)
def test_the_issues_named_cases_read_as_listed(
    ticker: str, ex_date: date, amount: float, verdict: str
) -> None:
    [group] = [
        g for g in reviewed_groups() if (g.ticker, g.ex_date, g.amount) == (ticker, ex_date, amount)
    ]
    assert group.verdict == verdict


def test_the_listing_gap_groups_are_unresolved() -> None:
    gaps = [
        g for g in reviewed_groups() if g.security_id == "0001372807" and g.ex_date.year == 2021
    ]
    assert [(g.ex_date, g.verdict, g.ticker) for g in gaps] == [
        (date(2021, 5, 18), "unresolved", ""),
        (date(2021, 8, 16), "unresolved", ""),
    ]


@pytest.mark.skipif(not RAW_EXPORT.exists(), reason="the owner-side raw export is local only")
def test_every_retract_id_is_the_greater_of_its_groups_two_ids() -> None:
    export = json.loads(RAW_EXPORT.read_text())
    ids = {
        (g["security_id"], date.fromisoformat(g["ex_date"]), g["amount"]): g["source_action_ids"]
        for g in export
    }
    duplicates = [g for g in reviewed_groups() if g.verdict == "duplicate"]
    assert len(duplicates) == 169
    for group in duplicates:
        pair = ids[group.key]
        assert len(pair) == 2
        assert group.retract_source_action_id == max(pair)


T1 = datetime(2021, 6, 14, 20, tzinfo=UTC)
T2 = datetime(2021, 6, 15, 20, tzinfo=UTC)
DAY = date(2021, 6, 15)


def _live(*rows: tuple[str, date, float, str, datetime], action_type: str = "dividend") -> Any:
    return pl.DataFrame(
        [
            {
                "security_id": sid,
                "action_type": action_type,
                "ex_date": ex,
                "ratio_or_amount": amount,
                "source_action_id": action_id,
                "known_at": known,
            }
            for sid, ex, amount, action_id, known in rows
        ]
    )


def _listed(sid: str, verdict: str, amount: float = 0.25) -> ReviewedGroup:
    return ReviewedGroup(
        security_id=sid,
        ex_date=DAY,
        amount=amount,
        verdict=verdict,  # type: ignore[arg-type]
        retract_source_action_id="b" if verdict == "duplicate" else "",
        ticker="",
        evidence="test",
    )


def test_equal_amount_groups_classes_every_key() -> None:
    live = _live(
        ("UNL", DAY, 0.25, "a", T1),  # unlisted pair: violation
        ("UNL", DAY, 0.25, "b", T1),
        ("SOLO", DAY, 0.25, "a", T1),  # one identity, unlisted: not a group
        ("DIS", DAY, 0.25, "a", T1),  # distinct pair: accepted
        ("DIS", DAY, 0.25, "b", T1),
        ("UNR", DAY, 0.25, "a", T1),  # unresolved pair: the gap
        ("UNR", DAY, 0.25, "b", T1),
        ("DUP", DAY, 0.25, "a", T1),  # duplicate pair: pending
        ("DUP", DAY, 0.25, "b", T1),
        ("REP", DAY, 0.25, "a", T1),  # duplicate, one left: repaired
        ("THR", DAY, 0.25, "a", T1),  # a third id on a listed key: violation
        ("THR", DAY, 0.25, "b", T1),
        ("THR", DAY, 0.25, "c", T1),
        ("LEG", DAY, 0.25, "a", T1),  # one leg of a distinct pair gone: violation
        ("NEW", DAY, 0.25, "a", T1),  # duplicate reissued under a new id: violation
        ("NEW", DAY, 0.25, "c", T1),
        ("KEPT", DAY, 0.25, "b", T1),  # duplicate whose kept id is gone: violation
        ("AMT", DAY, 0.25, "a", T1),  # another amount is another key
        ("AMT", DAY, 0.26, "b", T1),
    )
    reviewed = [
        _listed("DIS", "distinct"),
        _listed("UNR", "unresolved"),
        _listed("DUP", "duplicate"),
        _listed("REP", "duplicate"),
        _listed("THR", "distinct"),
        _listed("LEG", "distinct"),
        _listed("NEW", "duplicate"),
        _listed("KEPT", "duplicate"),
        _listed("GONE", "duplicate"),  # a store that never held it: not returned
    ]
    groups = equal_amount_groups(live, reviewed)
    assert [(g.security_id, g.group_class, len(g.identities)) for g in groups] == [
        ("DIS", "accepted", 2),
        ("DUP", "pending", 2),
        ("KEPT", "violation", 1),
        ("LEG", "violation", 1),
        ("NEW", "violation", 2),
        ("REP", "repaired", 1),
        ("THR", "violation", 3),
        ("UNL", "violation", 2),
        ("UNR", "unresolved", 2),
    ]
    [unlisted] = [g for g in groups if g.security_id == "UNL"]
    assert unlisted.reviewed is None
    assert [(i.source_action_id, i.known_at) for i in unlisted.identities] == [("a", T1), ("b", T1)]
    assert unlisted.key == ("UNL", DAY, 0.25)


def test_two_known_at_are_one_key_and_other_actions_are_ignored() -> None:
    live = _live(("X", DAY, 0.25, "a", T1), ("X", DAY, 0.25, "b", T2))
    [group] = equal_amount_groups(live, [])
    assert group.group_class == "violation"
    assert [(i.source_action_id, i.known_at) for i in group.identities] == [("a", T1), ("b", T2)]
    splits = _live(("X", DAY, 2.0, "a", T1), ("X", DAY, 2.0, "b", T1), action_type="split")
    assert equal_amount_groups(splits, []) == ()
    assert equal_amount_groups(_live(("X", DAY, 0.25, "a", T1)).clear(), []) == ()

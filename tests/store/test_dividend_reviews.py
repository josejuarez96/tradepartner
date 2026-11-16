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
    parse_recurring_pairs,
    parse_reviews,
    recurring_pairs,
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


def test_the_loader_refuses_a_retract_id_in_two_groups() -> None:
    second = "0000000001,2021-06-16,0.5,duplicate,id-b,AAA,second review"
    with pytest.raises(ReviewsError, match=r"id-b.*2021-06-15.*2021-06-16"):
        parse_reviews(_text(GOOD, second))


def test_the_amount_is_the_stored_double_exactly() -> None:
    [group] = parse_reviews(_text(GOOD.replace("0.25", "0.1515334")))
    assert group.amount == 0.1515334
    assert group.amount != 0.151533


def test_the_committed_list_pins_its_counts() -> None:
    """196 groups (184 at the amendment, 10 by #1666, 2 by #1720): a later edit is
    deliberate. #3054 settled STX 2021-05-11 as a duplicate (was unresolved). #3460
    added PNNT 2026-11-13 as distinct."""
    groups = reviewed_groups()
    assert Counter(g.verdict for g in groups) == {"duplicate": 181, "distinct": 13, "unresolved": 2}
    assert all(g.evidence for g in groups)
    assert min(g.ex_date for g in groups) == date(2019, 6, 27)
    assert max(g.ex_date for g in groups) == date(2026, 11, 13)


@pytest.mark.parametrize(
    ("ticker", "ex_date", "amount", "verdict"),
    [
        ("AIV", date(2020, 11, 3), 8.2, "duplicate"),
        ("TDG", date(2025, 9, 2), 90.0, "duplicate"),
        ("MKTW", date(2026, 3, 18), 0.2, "duplicate"),
        ("PNNT", date(2026, 10, 15), 0.04, "distinct"),
        ("PNNT", date(2026, 11, 13), 0.04, "distinct"),
        ("PFLT", date(2026, 10, 15), 0.0033, "duplicate"),
        ("F", date(2025, 2, 18), 0.15, "distinct"),
        ("CHRD", date(2023, 11, 13), 1.25, "distinct"),
        ("MKTW", date(2025, 8, 15), 0.2, "distinct"),
        ("STX", date(2021, 5, 11), 0.67, "duplicate"),
    ],
)
def test_the_issues_named_cases_read_as_listed(
    ticker: str, ex_date: date, amount: float, verdict: str
) -> None:
    [group] = [
        g for g in reviewed_groups() if (g.ticker, g.ex_date, g.amount) == (ticker, ex_date, amount)
    ]
    assert group.verdict == verdict


def test_stx_2021_05_11_retracts_the_greater_of_its_two_ids() -> None:
    """#3054: an independent dividend history has one $0.67 STX entitlement on
    2021-05-11 (the 2026-11-03 store measurement, #3053 item 5), so the pair
    is one payment stored twice; the greater id goes."""
    [group] = [g for g in reviewed_groups() if g.key == ("0001137789", date(2021, 5, 11), 0.67)]
    assert group.verdict == "duplicate"
    assert group.retract_source_action_id == "d08ea098-e6bd-4775-9bb6-abda5545428d"
    assert group.retract_source_action_id > "41b127bb-bd78-4967-9996-e4f2158e86c3"


def test_the_listing_gap_groups_are_unresolved() -> None:
    gaps = [
        g for g in reviewed_groups() if g.security_id == "0001372807" and g.ex_date.year == 2021
    ]
    assert [(g.ex_date, g.verdict, g.ticker) for g in gaps] == [
        (date(2021, 5, 18), "unresolved", ""),
        (date(2021, 8, 16), "unresolved", ""),
    ]


#: The owner-side raw export's own last ex_date (#1613/#1666 snapshot): a
#: duplicate added after it has no raw pair the local export can check.
RAW_EXPORT_LAST_EX_DATE = date(2026, 10, 1)


@pytest.mark.skipif(not RAW_EXPORT.exists(), reason="the owner-side raw export is local only")
def test_every_retract_id_is_the_greater_of_its_groups_two_ids() -> None:
    export = json.loads(RAW_EXPORT.read_text())
    ids = {
        (g["security_id"], date.fromisoformat(g["ex_date"]), g["amount"]): g["source_action_ids"]
        for g in export
    }
    duplicates = [g for g in reviewed_groups() if g.verdict == "duplicate"]
    assert len(duplicates) == 181
    for group in duplicates:
        pair = ids.get(group.key)
        if pair is None:
            # Only a group added after the export's snapshot (#1720's
            # 2026-10-15 PFLT pair) may lack a raw pair here; the check
            # stays strict for every group the export does cover.
            assert group.ex_date > RAW_EXPORT_LAST_EX_DATE, group.key
            continue
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


def test_a_re_sent_retract_id_under_another_key_beside_a_payment_is_a_violation() -> None:
    """#2980: the vendor re-sends a reviewed retract id with a noisy amount (or
    another ex-date); ingest stores it on its own key, so the exact key no
    longer joins the kept id. Beside another live payment on the reviewed or
    the new ex-date it is one payment counted twice: both keys fail."""
    later = date(2021, 6, 16)
    live = _live(
        ("NOISE", DAY, 0.25, "a", T1),  # kept id on the reviewed key
        ("NOISE", DAY, 0.25 + 1e-12, "b", T2),  # retract id, noisy amount
        ("MOVED", DAY, 0.25, "a", T1),  # kept id on the reviewed key
        ("MOVED", later, 0.25, "b", T2),  # retract id, another ex-date
        ("BOTH", later, 0.25, "a", T2),  # both re-sent on a new ex-date
        ("BOTH", later, 0.25 + 1e-12, "b", T2),
        ("ALONE", later, 0.25 + 1e-12, "b", T2),  # kept id gone: counted once
    )
    reviewed = [_listed(sid, "duplicate") for sid in ("NOISE", "MOVED", "BOTH", "ALONE")]
    groups = equal_amount_groups(live, reviewed)
    assert [
        (
            g.security_id,
            g.ex_date,
            g.amount,
            g.group_class,
            [i.source_action_id for i in g.identities],
        )
        for g in groups
    ] == [
        ("BOTH", later, 0.25, "violation", ["a"]),
        ("BOTH", later, 0.25 + 1e-12, "violation", ["b"]),
        ("MOVED", DAY, 0.25, "violation", ["a"]),
        ("MOVED", later, 0.25, "violation", ["b"]),
        ("NOISE", DAY, 0.25, "violation", ["a"]),
        ("NOISE", DAY, 0.25 + 1e-12, "violation", ["b"]),
    ]
    # The re-sent key names the reviewed row it came from.
    noisy = next(g for g in groups if g.security_id == "NOISE" and g.amount != 0.25)
    assert noisy.reviewed == reviewed[0]


PNNT = "0001383414"
PAIRS_HEADER = "security_id,start_ex_date,group_size,ticker,evidence"
#: A session after the committed hand rows end: the month nobody has listed yet.
NEXT_MONTH = date(2026, 12, 15)
T3 = datetime(2026, 12, 15, 20, tzinfo=UTC)


def _pnnt(ex: date, *ids: str, amount: float = 0.04, sid: str = PNNT, known: datetime = T3) -> Any:
    return _live(*((sid, ex, amount, i, known) for i in ids))


def test_the_next_months_pnnt_pair_is_accepted_by_the_committed_entry() -> None:
    """Fails before #3462: an unlisted pair is a violation until a hand row is added."""
    [group] = equal_amount_groups(_pnnt(NEXT_MONTH, "a", "b"), reviewed_groups())
    assert group.group_class == "accepted"
    assert group.reviewed is not None
    assert group.reviewed.verdict == "distinct"
    assert "Accepted risk" in group.reviewed.evidence
    assert group.reviewed.ticker == "PNNT"


def test_the_committed_entry_starts_at_the_earliest_hand_row() -> None:
    [entry] = recurring_pairs()
    hand = [g.ex_date for g in reviewed_groups() if g.security_id == PNNT]
    assert (entry.security_id, entry.group_size) == (PNNT, 2)
    assert entry.start_ex_date == min(hand)


def test_the_entry_gives_every_committed_pnnt_month_the_hand_rows_class() -> None:
    hand = [g for g in reviewed_groups() if g.security_id == PNNT]
    rows = _live(*((PNNT, g.ex_date, g.amount, i, T3) for g in hand for i in "ab"))
    with_rows = equal_amount_groups(rows, reviewed_groups())
    entry_only = equal_amount_groups(rows, [])
    assert [(g.key, g.group_class) for g in entry_only] == [
        (g.key, g.group_class) for g in with_rows
    ]
    assert {g.group_class for g in entry_only} == {"accepted"}


def test_a_third_row_or_a_differing_amount_on_the_day_still_fails() -> None:
    [three] = equal_amount_groups(_pnnt(NEXT_MONTH, "a", "b", "c"), [])
    assert three.group_class == "violation"
    odd = _live(
        (PNNT, NEXT_MONTH, 0.04, "a", T3),
        (PNNT, NEXT_MONTH, 0.04, "b", T3),
        (PNNT, NEXT_MONTH, 0.05, "c", T3),
    )
    assert [g.group_class for g in equal_amount_groups(odd, [])] == ["violation"]
    four = _pnnt(NEXT_MONTH, "a", "b", "c", "d")
    assert [g.group_class for g in equal_amount_groups(four, [])] == ["violation"]


def test_a_pair_before_the_start_or_of_another_security_fails() -> None:
    before = date(2026, 4, 14)
    [early] = equal_amount_groups(_pnnt(before, "a", "b"), [])
    assert early.group_class == "violation"
    [other] = equal_amount_groups(_pnnt(NEXT_MONTH, "a", "b", sid="0000000002"), [])
    assert other.group_class == "violation"


def test_the_entry_reads_only_the_rows_known_at_t() -> None:
    """No look-ahead: the rows are the as-of frame, so a row not yet known never
    joins the pair, and a later third row turns the same day into a violation."""
    assert equal_amount_groups(_pnnt(NEXT_MONTH, "a"), []) == ()
    assert [g.group_class for g in equal_amount_groups(_pnnt(NEXT_MONTH, "a", "b"), [])] == [
        "accepted"
    ]
    assert [g.group_class for g in equal_amount_groups(_pnnt(NEXT_MONTH, "a", "b", "c"), [])] == [
        "violation"
    ]


def test_the_pair_loader_refuses_malformed_rows_naming_them() -> None:
    good = f"{PNNT},2026-04-15,2,PNNT,why"
    [entry] = parse_recurring_pairs(f"{PAIRS_HEADER}\n{good}\n")
    assert entry.group_size == 2
    bad = [
        (good.replace("2026-04-15", "2026-04-18"), "not an XNYS session"),
        (good.replace(",2,", ",1,"), "below 2"),
        (good.replace(",2,", ",x,"), "not an integer"),
        (good.replace(",why", ","), "empty evidence"),
        (f"{good}\n{good}", "listed again"),
        ("a,b", "2 fields"),
    ]
    for row, why in bad:
        with pytest.raises(ReviewsError, match=why):
            parse_recurring_pairs(f"{PAIRS_HEADER}\n{row}\n")
    with pytest.raises(ReviewsError, match="header"):
        parse_recurring_pairs("x\n")

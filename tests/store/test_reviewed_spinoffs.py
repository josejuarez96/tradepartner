"""Reviewed spin-off actions the feed does not carry (#3392)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tradepartner.adapters.alpaca_prices import (
    AlpacaPriceSource,
    ListingResolver,
    parse_corporate_actions,
)
from tradepartner.adapters.prices import ActionType, spinoff_known_at_floor
from tradepartner.calendar import previous_session, session_open
from tradepartner.config import Settings
from tradepartner.store.reviewed_spinoffs import (
    ReviewedSpinoff,
    parse_spinoffs,
    reviewed_spinoffs,
)

HEADER = "child_security_id,parent_security_id,ex_date,ratio,known_at,accession,evidence,issue\n"
GOOD = (
    "0002132873,0001636519,2026-10-27,0.5,2026-09-30T20:39:29+00:00,"
    "0001193125-26-409143,why,#3392\n"
)
CHILD, PARENT = "0002132873", "0001636519"
EX = date(2026, 10, 27)
ACCEPTED = datetime(2026, 9, 30, 20, 39, 29, tzinfo=UTC)  # 8-K 16:39:29 Eastern, EDGAR index


def test_packaged_row_is_the_msgr_distribution_cited_to_the_oct_27_8k() -> None:
    (row,) = reviewed_spinoffs()
    assert (row.child_security_id, row.parent_security_id) == (CHILD, PARENT)
    assert row.ex_date == EX
    assert row.ratio == 0.5  # one MSGR per two parent shares (8-K of 2026-09-30)
    assert row.known_at == ACCEPTED
    assert row.accession == "0001193125-26-409143"
    assert row.issue == "#3392"
    action = row.action
    assert action.action_type is ActionType.SPINOFF
    assert (action.security_id, action.source_action_id) == (CHILD, PARENT)
    assert (action.ex_date, action.ratio_or_amount, action.known_at) == (EX, 0.5, ACCEPTED)


def test_the_action_is_known_before_the_ex_date_open_and_not_before_the_8k() -> None:
    """The 9-30 8-K announced the ratio and distribution: a backtest at the
    2026-10-27 open must know the action. The writer raises the stamp to the
    spin-off floor (close before the ex-date), still before that open."""
    (row,) = reviewed_spinoffs()
    stamp = row.action.known_at
    floor = spinoff_known_at_floor(EX)
    assert floor is not None
    assert max(stamp, floor) <= session_open(EX)
    assert stamp == datetime(2026, 9, 30, 20, 39, 29, tzinfo=UTC)
    assert stamp > datetime(2026, 9, 30, 20, 39, 28, tzinfo=UTC)  # nothing earlier than acceptance


def test_good_row_parses() -> None:
    (row,) = parse_spinoffs(HEADER + GOOD)
    assert isinstance(row, ReviewedSpinoff)
    assert row.identity == (CHILD, PARENT)


@pytest.mark.parametrize(
    "bad",
    [
        GOOD.replace("2026-10-27,0.5", "2026-10-25,0.5"),  # a Sunday: not a session
        GOOD.replace(",0.5,", ",0,"),
        GOOD.replace(",0.5,", ",nan,"),
        GOOD.replace("20:39:29+00:00", "20:39:29"),  # naive
        GOOD.replace("20:39:29+00:00", "16:39:29-04:00"),  # not UTC
        GOOD.replace("0001193125-26-409143", "bad"),
        GOOD.replace("#3392", "3392"),
        GOOD.replace("0002132873,0001636519", "0002132873,0002132873"),  # self spin-off
        GOOD.replace(",why,", ",,"),
        GOOD + GOOD,  # identity twice
        "0002132873,0001636519,2026-10-27\n",
    ],
)
def test_malformed_rows_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match=r"reviewed_spinoffs\.csv line"):
        parse_spinoffs(HEADER + bad)


def test_wrong_header_is_refused() -> None:
    with pytest.raises(ValueError, match="line 1"):
        parse_spinoffs("a,b\n" + GOOD)


# --- the Alpaca source adds the reviewed row -----------------------------------


def _listing(security_id: str, ticker: str, start: date) -> dict[str, object]:
    return {"security_id": security_id, "ticker": ticker, "valid_from": start, "class_title": None}


def _row() -> ReviewedSpinoff:
    (row,) = parse_spinoffs(HEADER + GOOD)
    return row


def _source(
    payload: dict[str, object] | None = None,
    reviewed: list[ReviewedSpinoff] | None = None,
    listings: list[dict[str, object]] | None = None,
) -> AlpacaPriceSource:
    return AlpacaPriceSource(
        ListingResolver(
            listings
            if listings is not None
            else [_listing(PARENT, "MSGS", date(2020, 5, 8)), _listing(CHILD, "MSGR", EX)]
        ),
        fetch_actions=lambda symbols, start, end: payload or {},
        settings=Settings(_env_file=None),
        reviewed_spinoffs=[_row()] if reviewed is None else reviewed,
    )


def test_source_returns_the_reviewed_spinoff_when_the_feed_has_none() -> None:
    source = _source()
    got = source.corporate_actions([PARENT], date(2026, 10, 1), date(2026, 10, 31))
    assert [(a.security_id, a.source_action_id, a.ratio_or_amount) for a in got] == [
        (CHILD, PARENT, 0.5)
    ]
    assert got[0].known_at == ACCEPTED
    assert source.last_actions_report is not None
    assert source.last_actions_report.reviewed_spinoffs_added == 1


def test_source_returns_it_for_the_child_request_too() -> None:
    got = _source().corporate_actions([CHILD], date(2026, 10, 1), date(2026, 10, 31))
    assert len(got) == 1


def test_source_leaves_it_out_of_a_window_without_the_ex_date_or_the_securities() -> None:
    assert _source().corporate_actions([PARENT], date(2026, 11, 2), date(2026, 11, 13)) == []
    other = _source(
        listings=[
            _listing(PARENT, "MSGS", date(2020, 5, 8)),
            _listing(CHILD, "MSGR", EX),
            _listing("0000000001", "OTHR", date(2020, 5, 8)),
        ]
    )
    assert other.corporate_actions(["0000000001"], date(2026, 10, 1), date(2026, 10, 31)) == []


def test_feed_row_for_the_same_identity_wins_and_nothing_is_added_twice() -> None:
    feed = {
        "spin_offs": [
            {
                "id": "alpaca-msgr",
                "source_symbol": "MSGS",
                "new_symbol": "MSGR.WI",
                "ex_date": "2026-10-27",
                "process_date": "2026-10-28",
                "source_rate": 2,
                "new_rate": 1,
            }
        ]
    }
    source = _source(feed)
    got = source.corporate_actions([PARENT], date(2026, 10, 1), date(2026, 10, 31))
    assert len(got) == 1
    assert got[0].source == "alpaca"
    assert source.last_actions_report is not None
    assert source.last_actions_report.reviewed_spinoffs_added == 0


def test_no_reviewed_rows_changes_nothing() -> None:
    source = _source(reviewed=[])
    assert source.corporate_actions([PARENT], date(2026, 10, 1), date(2026, 10, 31)) == []


def test_default_source_loads_the_packaged_row() -> None:
    source = AlpacaPriceSource(
        ListingResolver([_listing(PARENT, "MSGS", date(2020, 5, 8)), _listing(CHILD, "MSGR", EX)]),
        fetch_actions=lambda symbols, start, end: {},
        settings=Settings(_env_file=None),
    )
    assert len(source.corporate_actions([PARENT], date(2026, 10, 1), date(2026, 10, 31))) == 1


# --- a parent named by its new ticker -----------------------------------------


def _msgk_row() -> dict[str, object]:
    return {
        "id": "alpaca-msgr",
        "source_symbol": "MSGK",
        "new_symbol": "MSGR.WI",
        "ex_date": "2026-10-27",
        "process_date": "2026-10-28",
        "source_rate": 2,
        "new_rate": 1,
    }


def test_a_parent_named_by_its_new_ticker_resolves_on_the_ex_date() -> None:
    """Alpaca names the parent MSGK; the master lists MSGK only from the ex-date
    (the first session after the 10-26 distribution), MSGS before it."""
    resolver = ListingResolver(
        [
            _listing(PARENT, "MSGS", date(2020, 5, 8)),
            _listing(PARENT, "MSGK", EX),
            _listing(CHILD, "MSGR", EX),
        ]
    )
    assert resolver.resolve("MSGK", previous_session(EX)) is None
    parsed = parse_corporate_actions({"spin_offs": [_msgk_row()]}, resolver.resolve)
    [action] = parsed.actions
    assert (action.security_id, action.source_action_id, action.ratio_or_amount) == (
        CHILD,
        PARENT,
        0.5,
    )
    assert parsed.unresolved == ()
    assert parsed.spinoff_parents == ((PARENT, EX),)


def test_the_ex_date_parent_fallback_does_not_resolve_a_symbol_nobody_holds() -> None:
    resolver = ListingResolver([_listing(CHILD, "MSGR", EX)])
    parsed = parse_corporate_actions({"spin_offs": [_msgk_row()]}, resolver.resolve)
    assert parsed.actions == ()
    assert ("MSGK", EX) in parsed.unresolved


def test_a_parent_split_echo_on_a_reviewed_spinoff_ex_date_is_refused() -> None:
    """The feed has the parent's split echo (0.5) but no spin-off row: the
    reviewed spin-off adjusts it, so the split must not adjust it twice."""
    feed = {
        "forward_splits": [
            {
                "id": "echo",
                "symbol": "MSGS",
                "ex_date": "2026-10-27",
                "process_date": "2026-10-28",
                "new_rate": 1,
                "old_rate": 2,
            }
        ]
    }
    source = _source(feed)
    got = source.corporate_actions([PARENT], date(2026, 10, 1), date(2026, 10, 31))
    assert [a.action_type for a in got] == [ActionType.SPINOFF]
    assert source.last_actions_report is not None
    assert source.last_actions_report.spinoff_echo_splits_refused

"""Tests for tradepartner.store.index_members (#1682, T189): the committed S&P 500
answer key, its loader, the interval derivation and the pure matcher."""

from __future__ import annotations

import random
import re
from datetime import date, timedelta
from importlib import resources
from typing import Any

import polars as pl
import pytest

from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED
from tradepartner.store.index_members import (
    COLUMNS,
    COVERING,
    IN_INTERVAL,
    UNRESOLVED,
    IndexInterval,
    IndexMembersError,
    derive_intervals,
    format_index_members,
    load_index_members,
    normalize_ticker,
    parse_index_members,
    resolve,
)

PINNED = "a2430f2af0c79ddf0748e91de11bdeb1616ab5a7"
COMMENTS = (
    "source: https://github.com/fja05680/sp500",
    f"commit: {PINNED} (2026-09-07 update)",
    "through: 2026-08-18",
    "derived: 2026-10-14",
)
D = date


def _text(*rows: str, comments: tuple[str, ...] = COMMENTS) -> str:
    return "\n".join([*(f"# {c}" for c in comments), ",".join(COLUMNS), *rows]) + "\n"


# --- normalize_ticker -------------------------------------------------------


@pytest.mark.parametrize(
    "spelling", ["BRK.B", "BRKB", "brk.b", "BRK-B", "BRK/B", "BRK B", " BRK.B "]
)
def test_normalize_folds_case_dots_slashes_dashes_and_spaces(spelling: str) -> None:
    assert normalize_ticker(spelling) == "BRKB"


def test_normalize_keeps_digits_and_distinct_tickers_apart() -> None:
    assert normalize_ticker("BRK.A") != normalize_ticker("BRK.B")
    assert normalize_ticker("VZ 28A") == "VZ28A"


# --- derive_intervals -------------------------------------------------------


def test_derive_runs_gaps_reentry_and_open_ends() -> None:
    rows = [
        (D(2020, 1, 2), "AAA,BBB"),
        (D(2020, 3, 2), "AAA,BBB,CCC"),
        (D(2020, 6, 1), "BBB,CCC"),  # AAA leaves: last day 2020-05-31
        (D(2021, 1, 4), "AAA,BBB, CCC ,"),  # AAA returns; a stray space and an empty field
    ]
    assert derive_intervals(rows) == (
        IndexInterval("AAA", D(2020, 1, 2), D(2020, 5, 31)),
        IndexInterval("AAA", D(2021, 1, 4), None),
        IndexInterval("BBB", D(2020, 1, 2), None),
        IndexInterval("CCC", D(2020, 3, 2), None),
    )


def test_derive_matches_the_last_row_on_or_before_each_day() -> None:
    """A ticker is inside an interval on day d exactly when the last row
    dated on or before d lists it (how the 2026-10-14 check read the file)."""
    rng = random.Random(1682)
    names = [f"T{i}" for i in range(12)]
    day, rows = D(2019, 1, 2), []
    for _ in range(40):
        rows.append((day, ",".join(n for n in names if rng.random() < 0.6)))
        day += timedelta(days=rng.randint(1, 40))
    intervals = derive_intervals(rows)
    probe = rows[0][0] - timedelta(days=3)
    while probe <= day + timedelta(days=30):
        listed = [set(t.split(",")) - {""} for d, t in rows if d <= probe]
        expected = listed[-1] if listed else set()
        assert {i.ticker for i in intervals if i.covers(probe)} == expected, probe
        probe += timedelta(days=1)


def test_derive_refuses_rows_out_of_order() -> None:
    with pytest.raises(IndexMembersError, match="not in date order"):
        derive_intervals([(D(2020, 2, 3), "A"), (D(2020, 2, 3), "A")])
    with pytest.raises(IndexMembersError, match="not in date order"):
        derive_intervals([(D(2020, 2, 3), "A"), (D(2020, 1, 3), "A")])


# --- the file format and the loader -----------------------------------------


def test_parse_reads_provenance_and_intervals() -> None:
    members = parse_index_members(
        _text("AAA,2020-01-02,2020-05-31", "AAA,2021-01-04,", "BBB,2019-01-02,")
    )
    assert members.commit == PINNED
    assert members.source == "https://github.com/fja05680/sp500"
    assert (members.through, members.derived) == (D(2026, 8, 18), D(2026, 10, 14))
    assert members.intervals == (
        IndexInterval("AAA", D(2020, 1, 2), D(2020, 5, 31)),
        IndexInterval("AAA", D(2021, 1, 4), None),
        IndexInterval("BBB", D(2019, 1, 2), None),
    )


def test_format_round_trips_through_parse() -> None:
    intervals = derive_intervals([(D(2020, 1, 2), "AAA,BRK.B"), (D(2020, 6, 1), "BRK.B")])
    members = parse_index_members(format_index_members(intervals, COMMENTS))
    assert members.intervals == intervals


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (_text("A,2020-01-02,", comments=COMMENTS[:1] + COMMENTS[2:]), "'# commit:' is missing"),
        (
            _text("A,2020-01-02,", comments=("commit: abc123", COMMENTS[0], *COMMENTS[2:])),
            "not a 40-hex SHA",
        ),
        (_text("A,2020-01-02,", comments=COMMENTS[:3]), "'# derived:' is missing"),
        ("\n".join(f"# {c}" for c in COMMENTS) + "\nticker,start,end\n", "header"),
        ("\n".join(f"# {c}" for c in COMMENTS) + "\n", "header None"),
        (_text("A,2020-01-02"), "2 fields"),
        (_text(",2020-01-02,"), "empty ticker"),
        (_text("A,2020-02-30,"), "from '2020-02-30' is not a date"),
        (_text("A,2020-03-02,2020-03-01"), "before from"),
        (_text("A,2020-01-02,2020-06-30", "A,2020-06-30,"), "overlaps"),
        (_text("A,2020-01-02,", "A,2021-01-04,"), "overlaps"),
    ],
)
def test_parse_refuses_a_malformed_file(text: str, why: str) -> None:
    with pytest.raises(IndexMembersError, match=re.escape(why)):
        parse_index_members(text)


def test_parse_names_the_line() -> None:
    with pytest.raises(IndexMembersError, match="line 7: empty ticker"):
        parse_index_members(_text("A,2020-01-02,", ",2020-01-02,"))


def test_load_reads_a_path(tmp_path: Any) -> None:
    path = tmp_path / "key.csv"
    path.write_text(_text("A,2020-01-02,"), encoding="utf-8")
    assert load_index_members(path).intervals == (IndexInterval("A", D(2020, 1, 2), None),)
    with pytest.raises(FileNotFoundError):
        load_index_members(tmp_path / "absent.csv")


def test_the_packaged_file_is_the_pinned_derivation() -> None:
    raw = resources.files("tradepartner.store").joinpath("index_members.csv").read_text("utf-8")
    members = load_index_members()
    assert members.commit == PINNED
    assert members.source.startswith("https://github.com/fja05680/sp500")
    assert members.through == D(2026, 8, 18)
    # The MIT notice is the licence's one condition: it travels with the copy.
    assert "# Copyright (c) 2019-2020 Farrell J. Aultman" in raw
    assert "# The above copyright notice and this permission notice shall be included" in raw
    assert "Trading Evolved" in raw and "Wikipedia" in raw  # the upstream provenance
    assert len(members.intervals) == 1262
    assert sum(i.end is None for i in members.intervals) == 503
    by_ticker = {i.ticker: i for i in members.intervals if i.end is None}
    assert {"BRK.B", "BF.B", "ZBH", "MSFT"} <= set(by_ticker)
    # Three of the 2026-10-14 check's month-ends (its 86 summed to 43,325 member-months).
    month_ends = [D(2019, 8, 30), D(2022, 6, 30), D(2026, 7, 31)]
    assert [sum(i.covers(m) for i in members.intervals) for m in month_ends] == [505, 503, 503]


# --- resolve ----------------------------------------------------------------


def _frame(*rows: dict[str, Any]) -> pl.DataFrame:
    full = [{"status": LISTED, "end_session": None, "exchange": "NYSE", **row} for row in rows]
    return pl.DataFrame(
        full,
        schema={
            "security_id": pl.Utf8,
            "ticker": pl.Utf8,
            "exchange": pl.Utf8,
            "valid_from": pl.Date,
            "status": pl.Utf8,
            "end_session": pl.Date,
        },
    )


def _row(sid: str, ticker: str, start: date, **extra: Any) -> dict[str, Any]:
    return {"security_id": sid, "ticker": ticker, "valid_from": start, **extra}


S = D(2021, 6, 30)


def test_a_row_covering_the_session_resolves() -> None:
    [res] = resolve(
        [IndexInterval("AAA", D(2010, 1, 4), None)], S, _frame(_row("1", "AAA", D(2001, 1, 2)))
    )
    assert (res.outcome, res.security_id, res.listing_ticker) == (COVERING, "1", "AAA")
    assert (res.session, res.interval_start, res.interval_end) == (S, D(2010, 1, 4), None)
    assert res.candidates == ("1",)


def test_only_intervals_covering_the_session_are_resolved() -> None:
    key = [
        IndexInterval("OLD", D(2010, 1, 4), D(2021, 6, 29)),
        IndexInterval("NEW", D(2021, 7, 1), None),
        IndexInterval("ON", D(2021, 6, 30), D(2021, 6, 30)),
    ]
    assert [r.ticker for r in resolve(key, S, _frame())] == ["ON"]


def test_a_normalised_spelling_matches() -> None:
    [res] = resolve(
        [IndexInterval("BRK.B", D(2010, 1, 4), None)],
        S,
        _frame(_row("2:b", "BRK-B", D(2019, 1, 2))),
    )
    assert (res.outcome, res.security_id) == (COVERING, "2:b")


def test_an_exact_spelling_wins_over_a_normalised_one() -> None:
    """ZBH, not Zions' preferred `ZB/H`, which normalises to the same key."""
    frame = _frame(
        _row("0000109380:series-h", "ZB/H", D(2019, 8, 6)),
        _row("0001136869", "ZBH", D(2001, 3, 26)),
    )
    [res] = resolve([IndexInterval("ZBH", D(2001, 8, 7), None)], S, frame)
    assert res.security_id == "0001136869"
    assert res.candidates == ("0001136869", "0000109380:series-h")


def test_the_primary_id_wins_over_a_note_filed_under_its_ticker() -> None:
    frame = _frame(
        _row("0000789019:3-125pct-notes-due-2028", "MSFT", D(2020, 1, 29)),
        _row("0000789019", "MSFT", D(2019, 10, 23)),
    )
    [res] = resolve([IndexInterval("MSFT", D(1996, 1, 2), None)], S, frame)
    assert res.security_id == "0000789019"


def test_a_class_id_resolves_when_it_alone_carries_the_ticker() -> None:
    frame = _frame(
        _row("1067983", "BRK.A", D(2019, 11, 4)), _row("1067983:class-b", "BRK.B", D(2019, 11, 4))
    )
    [res] = resolve([IndexInterval("BRK.B", D(2010, 2, 16), None)], S, frame)
    assert res.security_id == "1067983:class-b"


def test_a_row_starting_inside_the_interval_after_the_session_is_the_fallback() -> None:
    """BRK.B in 2019: the store's first row starts after the session."""
    frame = _frame(_row("1067983:class-b", "BRK.B", D(2021, 11, 4)))
    [res] = resolve([IndexInterval("BRK.B", D(2010, 2, 16), None)], S, frame)
    assert (res.outcome, res.security_id, res.listing_valid_from) == (
        IN_INTERVAL,
        "1067983:class-b",
        D(2021, 11, 4),
    )


def test_a_row_ended_before_the_session_but_inside_the_interval_is_the_fallback() -> None:
    frame = _frame(_row("7", "GONE", D(2015, 1, 2), status=DELISTED, end_session=D(2021, 3, 31)))
    [res] = resolve([IndexInterval("GONE", D(2018, 1, 2), D(2021, 9, 1))], S, frame)
    assert (res.outcome, res.security_id) == (IN_INTERVAL, "7")


def test_the_fallback_takes_the_row_nearest_the_session() -> None:
    frame = _frame(
        _row("far", "AAA", D(2024, 1, 2)),
        _row("near", "AAA", D(2021, 8, 2)),
    )
    [res] = resolve([IndexInterval("AAA", D(2010, 1, 4), None)], S, frame)
    assert (res.outcome, res.security_id, res.candidates) == (IN_INTERVAL, "near", ("near", "far"))


def test_a_reused_tickers_other_holder_is_never_the_name() -> None:
    """The old holder's span ended before the key's interval began, and the
    new holder has no row yet: unresolved, not the old company."""
    old = _row("old", "ECHO", D(2010, 1, 4), status=DELISTED, end_session=D(2021, 5, 19))
    [res] = resolve([IndexInterval("ECHO", D(2021, 6, 24), None)], D(2021, 12, 31), _frame(old))
    assert (res.outcome, res.security_id, res.candidates) == (UNRESOLVED, None, ())
    assert (res.listing_ticker, res.listing_valid_from) == (None, None)


def test_a_renamed_securitys_old_ticker_ends_at_its_next_row() -> None:
    """A listed row's span stops the day before its security's next row
    starts (universe rule 2), so a later holder of the old ticker is not
    matched to the renamed company."""
    frame = _frame(_row("fb", "FB", D(2012, 5, 18)), _row("fb", "META", D(2022, 6, 9)))
    [res] = resolve([IndexInterval("FB", D(2023, 1, 3), None)], D(2023, 6, 30), frame)
    assert res.outcome == UNRESOLVED
    [res] = resolve([IndexInterval("FB", D(2013, 12, 23), D(2022, 6, 8))], S, frame)
    assert (res.outcome, res.security_id) == (COVERING, "fb")


def test_a_transfer_with_no_end_covers_and_a_delisted_row_without_a_bar_does_not() -> None:
    frame = _frame(
        _row("t", "TTT", D(2015, 1, 2), status=TRANSFERRED),
        _row("d", "DDD", D(2021, 6, 1), status=DELISTED),
    )
    key = [IndexInterval("TTT", D(2015, 1, 2), None), IndexInterval("DDD", D(2021, 1, 4), None)]
    by_ticker = {r.ticker: r for r in resolve(key, S, frame)}
    assert by_ticker["TTT"].outcome == COVERING
    # No live day: the fallback still names it by its start inside the interval.
    assert (by_ticker["DDD"].outcome, by_ticker["DDD"].security_id) == (IN_INTERVAL, "d")


def test_a_delisted_row_covers_through_its_end_session() -> None:
    frame = _frame(_row("x", "XXX", D(2015, 1, 2), status=DELISTED, end_session=S))
    [res] = resolve([IndexInterval("XXX", D(2015, 1, 2), None)], S, frame)
    assert res.outcome == COVERING
    [res] = resolve([IndexInterval("XXX", D(2015, 1, 2), None)], S + timedelta(days=1), frame)
    assert res.outcome == IN_INTERVAL


def test_resolve_is_pure_and_sorted_by_ticker() -> None:
    frame = _frame(_row("b", "BBB", D(2015, 1, 2)), _row("a", "AAA", D(2015, 1, 2)))
    before = frame.clone()
    key = [IndexInterval("BBB", D(2015, 1, 2), None), IndexInterval("AAA", D(2015, 1, 2), None)]
    first = resolve(key, S, frame)
    assert [r.ticker for r in first] == ["AAA", "BBB"]
    assert resolve(key, S, frame) == first
    assert frame.equals(before)

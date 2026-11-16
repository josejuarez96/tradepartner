"""A spin-off is not a split (#3058; #3053 item 2).

Alpaca serves some spin-offs as a split row of the parent. Two ingest rules keep
such a row out of the store:

- the echo rule: a split row of a security on the ex-date of a `spin_offs` row
  whose parent is that security, in the same answer, is the spin-off's echo;
- the reviewed list (`store.spinoff_splits`): an identity judged a spin-off is
  refused, so a backfill never stores it again after the repair deleted it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, ListingResolver
from tradepartner.adapters.prices import CorporateAction
from tradepartner.config import Settings
from tradepartner.store.spinoff_splits import (
    SpinoffSplit,
    SpinoffSplitsError,
    parse_spinoff_splits,
    spinoff_splits,
)

PAR, KID, OTHER = "SEC_PAR", "SEC_KID", "SEC_OTHER"
EX = date(2018, 10, 1)  # a Monday session
LATER = date(2018, 10, 29)


def _listing(security_id: str, ticker: str, valid_from: date) -> dict[str, object]:
    return {
        "security_id": security_id,
        "ticker": ticker,
        "valid_from": valid_from,
        "class_title": None,
    }


def _split(
    symbol: str = "PAR",
    ex_date: date = EX,
    *,
    new_rate: float = 1.011,
    old_rate: float = 1,
    action_id: str = "split-id",
    category: str = "forward_splits",
) -> tuple[str, dict[str, Any]]:
    return category, {
        "id": action_id,
        "symbol": symbol,
        "ex_date": ex_date.isoformat(),
        "process_date": ex_date.isoformat(),
        "old_rate": old_rate,
        "new_rate": new_rate,
    }


def _spin(
    ex_date: date = EX, *, process_date: date | None = None, child: str = "KID"
) -> dict[str, Any]:
    return {
        "id": "spin-id",
        "source_symbol": "PAR",
        "new_symbol": child,
        "ex_date": ex_date.isoformat(),
        "process_date": (process_date or ex_date).isoformat(),
        "source_rate": 1,
        "new_rate": 0.1,
    }


def _source(
    *rows: tuple[str, dict[str, Any]],
    reviewed: tuple[SpinoffSplit, ...] = (),
    child_listed: bool = True,
) -> AlpacaPriceSource:
    payload: dict[str, list[dict[str, Any]]] = {}
    for category, row in rows:
        payload.setdefault(category, []).append(row)
    listings = [_listing(PAR, "PAR", date(2016, 1, 4)), _listing(OTHER, "OTH", date(2016, 1, 4))]
    if child_listed:
        listings.append(_listing(KID, "KID", date(2018, 9, 17)))
    return AlpacaPriceSource(
        ListingResolver(listings),
        fetch_actions=lambda symbols, start, end: {
            category: [
                row
                for row in category_rows
                if start <= date.fromisoformat(row["process_date"]) <= end
            ]
            for category, category_rows in payload.items()
        },
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        spinoff_splits=reviewed,
    )


def _kinds(actions: list[CorporateAction]) -> list[tuple[str, str, date]]:
    return [(a.security_id, a.action_type.value, a.ex_date) for a in actions]


# --- the echo rule ------------------------------------------------------------------


def test_a_split_row_on_the_parents_spin_off_ex_date_is_not_a_split() -> None:
    source = _source(_split(), ("spin_offs", _spin()))
    got = source.corporate_actions([PAR, KID], EX, EX)
    assert _kinds(got) == [(KID, "spinoff", EX)]
    report = source.last_actions_report
    assert report is not None
    assert report.spinoff_echo_splits_refused == (f"{PAR} {EX} 1.011",)
    assert f"1 split row(s) refused as the parent's same-day spin-off: {PAR} {EX} 1.011" in (
        source.resolution_summary()
    )


@pytest.mark.parametrize("category", ["forward_splits", "reverse_splits"])
def test_either_split_category_is_an_echo(category: str) -> None:
    split = _split(new_rate=1, old_rate=4, category=category)
    source = _source(split, ("spin_offs", _spin()))
    assert _kinds(source.corporate_actions([PAR], EX, EX)) == [(KID, "spinoff", EX)]


def test_a_stock_dividend_row_on_the_spin_off_ex_date_is_an_echo() -> None:
    stock = (
        "stock_dividends",
        {
            "id": "stock-id",
            "symbol": "PAR",
            "ex_date": EX.isoformat(),
            "process_date": EX.isoformat(),
            "rate": 1.081,
        },
    )
    source = _source(stock, ("spin_offs", _spin()))
    assert _kinds(source.corporate_actions([PAR], EX, EX)) == [(KID, "spinoff", EX)]
    assert source.last_actions_report is not None
    assert source.last_actions_report.spinoff_echo_splits_refused == (f"{PAR} {EX} 1.081",)


def test_the_echo_holds_when_the_child_does_not_resolve() -> None:
    """The parent and the day make it a spin-off; an unknown child does not
    make the split row true."""
    source = _source(_split(), ("spin_offs", _spin()), child_listed=False)
    assert source.corporate_actions([PAR], EX, EX) == []
    report = source.last_actions_report
    assert report is not None
    assert report.spinoff_echo_splits_refused == (f"{PAR} {EX} 1.011",)
    assert ("KID", EX) in report.unresolved


def test_a_split_on_another_day_or_of_another_security_is_kept() -> None:
    source = _source(
        _split(ex_date=LATER, action_id="later"),
        _split("OTH", action_id="other"),
        ("spin_offs", _spin()),
    )
    got = source.corporate_actions([PAR, OTHER, KID], EX, LATER)
    assert set(_kinds(got)) == {(OTHER, "split", EX), (PAR, "split", LATER), (KID, "spinoff", EX)}
    assert source.last_actions_report is not None
    assert source.last_actions_report.spinoff_echo_splits_refused == ()


def test_a_spin_off_this_run_does_not_keep_never_refuses_a_split() -> None:
    """No look-ahead: a `spin_offs` row processed after the run's process
    range is not read, so it cannot take the split away; a later run that
    reads it can."""
    late = EX + timedelta(days=200)
    source = _source(_split(), ("spin_offs", _spin(process_date=late)))
    assert _kinds(source.corporate_actions([PAR, KID], EX, EX)) == [(PAR, "split", EX)]
    assert source.last_actions_report is not None
    assert source.last_actions_report.spinoff_echo_splits_refused == ()
    end = late - timedelta(days=60)  # the 90-day process lag now reaches `late`
    got = source.corporate_actions([PAR, KID], EX, end)
    assert _kinds(got) == [(KID, "spinoff", EX)]


def test_a_malformed_spin_off_category_refuses_no_split() -> None:
    bad = {**_spin(), "new_rate": "x"}
    source = _source(_split(), ("spin_offs", bad))
    assert _kinds(source.corporate_actions([PAR], EX, EX)) == [(PAR, "split", EX)]
    report = source.last_actions_report
    assert report is not None
    assert report.malformed_categories == ("spin_offs",)
    assert report.spinoff_echo_splits_refused == ()


# --- the reviewed list ----------------------------------------------------------------


def _reviewed(ratio: float = 1.011, ex_date: date = EX) -> SpinoffSplit:
    return SpinoffSplit(PAR, "split-id", ex_date, ratio, "PAR", "Kid Co", "test")


def test_a_reviewed_spin_off_split_is_refused() -> None:
    source = _source(_split(), reviewed=(_reviewed(),))
    assert source.corporate_actions([PAR], EX, EX) == []
    report = source.last_actions_report
    assert report is not None
    assert report.reviewed_spinoff_splits_refused == 1
    assert "1 reviewed spin-off split row(s) refused" in source.resolution_summary()


@pytest.mark.parametrize(
    "reviewed", [_reviewed(ratio=1.25), _reviewed(ex_date=LATER)], ids=["ratio", "ex_date"]
)
def test_a_reviewed_id_the_source_changed_is_stored_again(reviewed: SpinoffSplit) -> None:
    """Only the reviewed values are refused: a changed event is stored, so the
    repair's dry run names it stale for a new review."""
    source = _source(_split(), reviewed=(reviewed,))
    assert _kinds(source.corporate_actions([PAR], EX, EX)) == [(PAR, "split", EX)]
    assert source.last_actions_report is not None
    assert source.last_actions_report.reviewed_spinoff_splits_refused == 0


def test_an_unlisted_split_is_kept() -> None:
    source = _source(_split(action_id="another"), reviewed=(_reviewed(),))
    assert _kinds(source.corporate_actions([PAR], EX, EX)) == [(PAR, "split", EX)]


def test_the_source_reads_the_packaged_list_by_default() -> None:
    packaged = spinoff_splits()
    assert packaged, "the packaged list is empty"
    hon = [row for row in packaged if row.ticker == "HON"]
    assert {row.ex_date for row in hon} == {
        date(2016, 10, 3),
        date(2018, 10, 1),
        date(2018, 10, 29),
    }
    [first] = packaged[:1]
    category, row = _split(
        ex_date=first.ex_date, new_rate=first.ratio, action_id=first.source_action_id
    )
    source = AlpacaPriceSource(
        ListingResolver([_listing(first.security_id, "PAR", date(2016, 1, 4))]),
        fetch_actions=lambda symbols, start, end: {category: [row]},
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
    )
    assert source.corporate_actions([first.security_id], first.ex_date, first.ex_date) == []


# --- the file -------------------------------------------------------------------------

HEADER = "security_id,source_action_id,ex_date,ratio,ticker,child,evidence"
ROW = "0000773840,abc,2018-10-29,1.032,HON,Resideo,Form 10 of Resideo"


def test_the_list_parses_and_refuses_a_malformed_file() -> None:
    [row] = parse_spinoff_splits(f"{HEADER}\n{ROW}\n")
    assert row == SpinoffSplit(
        "0000773840", "abc", date(2018, 10, 29), 1.032, "HON", "Resideo", "Form 10 of Resideo"
    )
    assert row.identity == ("0000773840", "abc")
    for text, error in (
        ("a,b\n", "header"),
        (f"{HEADER}\n{ROW}\n{ROW}\n", "line 3: identity .* is listed again"),
        (f"{HEADER}\n{ROW.replace('abc', '')}\n", "empty source_action_id"),
        (f"{HEADER}\n{ROW.replace('2018-10-29', '2018-10-28')}\n", "not an XNYS session"),
        (f"{HEADER}\n{ROW.replace('1.032', '0')}\n", "not positive"),
        (f"{HEADER}\n{ROW.replace('Form 10 of Resideo', '')}\n", "empty evidence"),
        (f"{HEADER}\n{ROW},extra\n", "8 fields"),
    ):
        with pytest.raises(SpinoffSplitsError, match=error):
            parse_spinoff_splits(text)


def test_the_packaged_list_holds_no_split_type_mismatch() -> None:
    """Every packaged row names its child and its evidence."""
    for row in spinoff_splits():
        assert row.child and row.evidence and row.ticker, row

"""Per-book buy timing and the pure arm function (T369)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from tradepartner.calendar import all_sessions
from tradepartner.config import BookExecution, Settings
from tradepartner.execution.buy_timing import allowed_time_in_force, arm_for, execution_for

SESSIONS = all_sessions()


def _alt(start: date) -> BookExecution:
    return BookExecution(buy_timing="alternate", alternate_start=start)


def test_at_open_is_v1_and_after_sells_v0_everywhere() -> None:
    for s in SESSIONS[100:110]:
        assert arm_for(BookExecution(buy_timing="at_open"), s, SESSIONS) == "V1"
        assert arm_for(BookExecution(), s, SESSIONS) == "V0"


def test_alternate_counts_sessions_across_a_holiday() -> None:
    # 2026-11-23 Mon .. 2026-11-27 Fri spans Thanksgiving (11-26 closed).
    start = date(2026, 11, 23)
    ex = _alt(start)
    got = [
        arm_for(ex, d, SESSIONS)
        for d in (
            date(2026, 11, 23),
            date(2026, 11, 24),
            date(2026, 11, 25),
            date(2026, 11, 27),
            date(2026, 11, 30),
        )
    ]
    assert got == ["V1", "V0", "V1", "V0", "V1"]  # 11-27 is ordinal 3, not 4 weekdays


def test_alternate_before_start_is_v0() -> None:
    assert arm_for(_alt(date(2026, 11, 23)), date(2026, 11, 20), SESSIONS) == "V0"


def test_alternate_non_session_day_refused() -> None:
    with pytest.raises(ValueError, match="not in the session list"):
        arm_for(_alt(date(2026, 11, 23)), date(2026, 11, 26), SESSIONS)


def test_absent_book_gets_defaults() -> None:
    ex = execution_for(Settings(), "main")
    assert ex.buy_timing == "after_sells"
    assert ex.cash_sleeve_fraction == 0
    assert ex.auction == "none"
    assert allowed_time_in_force(ex) == frozenset({"day"})


def test_book_in_map_is_returned() -> None:
    s = Settings(paper={"execution": {"daily": {"buy_timing": "at_open"}}})  # type: ignore[arg-type]
    assert execution_for(s, "daily").buy_timing == "at_open"
    assert execution_for(s, "other").buy_timing == "after_sells"


def test_bad_book_token_refused() -> None:
    with pytest.raises(ValueError, match="book_id"):
        execution_for(Settings(), "bad-id")


def test_opening_auction_refused_naming_t376() -> None:
    with pytest.raises(ValidationError, match="T376"):
        BookExecution(auction="opening")


def test_closing_auction_refused() -> None:
    with pytest.raises(ValidationError):
        BookExecution(auction="closing")  # type: ignore[arg-type]


def test_allowed_tif_with_opening_includes_opg() -> None:
    ex = BookExecution.model_construct(auction="opening")
    assert allowed_time_in_force(ex) == frozenset({"day", "opg"})

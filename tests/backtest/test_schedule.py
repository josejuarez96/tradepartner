"""Rebalance and fill sessions from the XNYS calendar (backtest spec definitions, T37)."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.calendar import is_half_day

SCHEDULE_PY = Path(__file__).parents[2] / "src" / "tradepartner" / "backtest" / "schedule.py"


def test_rebalance_sessions_are_the_last_session_of_each_month() -> None:
    assert rebalance_sessions(date(2024, 1, 1), date(2024, 6, 30)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 28),  # Good Friday 2024-03-29 is a holiday: a Thursday month end
        date(2024, 4, 30),
        date(2024, 5, 31),
        date(2024, 6, 28),  # 2024-06-29/30 fall on a weekend
    ]


def test_rebalance_sessions_bounds_are_inclusive_and_partial_months_count_only_if_inside() -> None:
    # The window ends before May's last session, so May is not a rebalance month.
    assert rebalance_sessions(date(2024, 3, 28), date(2024, 5, 30)) == [
        date(2024, 3, 28),
        date(2024, 4, 30),
    ]
    assert rebalance_sessions(date(2024, 3, 29), date(2024, 4, 30)) == [date(2024, 4, 30)]


def test_rebalance_sessions_empty_and_reversed_windows() -> None:
    assert rebalance_sessions(date(2024, 4, 1), date(2024, 4, 29)) == []
    with pytest.raises(ValueError, match="after"):
        rebalance_sessions(date(2024, 5, 1), date(2024, 4, 1))


def test_rebalance_sessions_reject_datetimes() -> None:
    with pytest.raises(TypeError):
        rebalance_sessions(datetime(2024, 1, 1, tzinfo=UTC), date(2024, 2, 1))


def test_fill_session_across_a_month_end_before_a_holiday() -> None:
    # 2024-08-30 is August's last session; Monday 2024-09-02 is Labor Day.
    assert fill_session(date(2024, 8, 30)) == date(2024, 9, 3)
    # Good Friday: March 2024 ends on Thursday 03-28, the fill is Monday 04-01.
    assert fill_session(date(2024, 3, 28)) == date(2024, 4, 1)


def test_fill_session_on_a_half_day() -> None:
    # June 2023 ends Friday 06-30; the next session, Monday 2023-07-03, closes early.
    assert fill_session(date(2023, 6, 30)) == date(2023, 7, 3)
    assert is_half_day(date(2023, 7, 3))


def test_rebalance_session_on_a_half_day_reads_at_the_early_close() -> None:
    # 2024-11-29 (day after Thanksgiving) closes at 13:00 New York, 18:00 UTC.
    assert rebalance_sessions(date(2024, 11, 1), date(2024, 11, 30)) == [date(2024, 11, 29)]
    assert read_time(date(2024, 11, 29)) == datetime(2024, 11, 29, 18, 0, tzinfo=UTC)
    assert fill_session(date(2024, 11, 29)) == date(2024, 12, 2)


def test_read_time_is_the_close_in_utc() -> None:
    t = read_time(date(2024, 1, 31))
    assert t == datetime(2024, 1, 31, 21, 0, tzinfo=UTC)
    assert t.utcoffset() == timedelta(0)


@pytest.mark.parametrize("func", [fill_session, read_time])
def test_non_rebalance_sessions_are_refused(func: object) -> None:
    assert callable(func)
    with pytest.raises(ValueError, match="rebalance session"):
        func(date(2024, 1, 30))  # a session, but not the month's last
    with pytest.raises(ValueError, match="rebalance session"):
        func(date(2024, 3, 31))  # a Sunday


def test_schedule_uses_no_weekday_logic() -> None:
    """A rebalance date is a calendar output, never a weekday rule (spec req 1)."""
    tree = ast.parse(SCHEDULE_PY.read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"weekday", "isoweekday", "BDay", "BusinessDay", "bdate_range"}

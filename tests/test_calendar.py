"""Tests for tradepartner.calendar (XNYS wrapper).

Every function must derive its answer from `exchange_calendars`, never from
`.weekday()` or other "weekdays" arithmetic (CLAUDE.md code standards).
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, date, datetime

import pytest

from tradepartner import calendar as tp_calendar


def test_no_weekday_logic_in_source() -> None:
    """Grep (via AST) the module for `.weekday()` / `isoweekday()` calls.

    (The word "weekday" is allowed in prose/docstrings explaining *why* we
    avoid it; what must never appear is an actual call to a weekday method.)
    """
    source = inspect.getsource(tp_calendar)
    assert ".weekday(" not in source
    assert ".isoweekday(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"weekday", "isoweekday"}


def test_is_session_on_a_normal_trading_day() -> None:
    assert tp_calendar.is_session(date(2025, 7, 7)) is True


def test_is_session_false_on_a_holiday() -> None:
    # 2025-07-04: Independence Day, not an XNYS session.
    assert tp_calendar.is_session(date(2025, 7, 4)) is False


def test_is_session_false_on_a_weekend() -> None:
    assert tp_calendar.is_session(date(2025, 7, 5)) is False


def test_next_session_skips_holiday() -> None:
    assert tp_calendar.next_session(date(2025, 7, 3)) == date(2025, 7, 7)


def test_previous_session_skips_holiday() -> None:
    assert tp_calendar.previous_session(date(2025, 7, 7)) == date(2025, 7, 3)


def test_session_open_close_on_a_normal_day() -> None:
    open_ts = tp_calendar.session_open(date(2025, 7, 7))
    close_ts = tp_calendar.session_close(date(2025, 7, 7))
    assert open_ts == datetime(2025, 7, 7, 13, 30, tzinfo=UTC)
    assert close_ts == datetime(2025, 7, 7, 20, 0, tzinfo=UTC)
    assert open_ts.tzinfo is not None
    assert close_ts.tzinfo is not None


def test_half_day_close_is_18_utc() -> None:
    # 2025-11-28 (day after Thanksgiving) is an XNYS half day: 1pm ET close.
    close_ts = tp_calendar.session_close(date(2025, 11, 28))
    assert close_ts == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)


def test_is_half_day_true_on_a_half_day() -> None:
    assert tp_calendar.is_half_day(date(2025, 11, 28)) is True


def test_is_half_day_false_on_a_normal_day() -> None:
    assert tp_calendar.is_half_day(date(2025, 7, 7)) is False


def test_holiday_is_not_a_session_via_is_session() -> None:
    assert tp_calendar.is_session(date(2025, 7, 4)) is False


def test_last_session_of_month() -> None:
    assert tp_calendar.last_session_of_month(2025, 7) == date(2025, 7, 31)


def test_last_completed_session_before_close_same_day() -> None:
    # Mid-session on 2025-07-07 (before its 20:00 UTC close): the last
    # *completed* session is the previous one, 2025-07-03 (07-04 is a holiday).
    as_of = datetime(2025, 7, 7, 15, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_after_close_same_day() -> None:
    as_of = datetime(2025, 7, 7, 21, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 7)


def test_last_completed_session_at_close_exactly() -> None:
    as_of = datetime(2025, 7, 7, 20, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 7)


def test_last_completed_session_on_a_weekend() -> None:
    as_of = datetime(2025, 7, 5, 12, 0, tzinfo=UTC)  # Saturday
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_on_a_holiday() -> None:
    as_of = datetime(2025, 7, 4, 12, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_requires_tz_aware() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        tp_calendar.last_completed_session(datetime(2025, 7, 7, 15, 0))  # noqa: DTZ001


def test_sessions_in_month_window_12_months_including_end() -> None:
    sessions = tp_calendar.sessions_in_month_window(date(2025, 7, 31), months=12)
    assert sessions[0] == date(2024, 8, 1)
    assert sessions[-1] == date(2025, 7, 31)
    assert len(sessions) == 250
    # Every session in range actually is one, and the list is sorted/unique.
    assert sessions == sorted(set(sessions))
    assert all(tp_calendar.is_session(s) for s in sessions)


def test_sessions_in_month_window_default_is_12_months() -> None:
    default_window = tp_calendar.sessions_in_month_window(date(2025, 7, 31))
    explicit_window = tp_calendar.sessions_in_month_window(date(2025, 7, 31), months=12)
    assert default_window == explicit_window

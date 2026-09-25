"""Tests for tradepartner.calendar (XNYS wrapper).

Every function must derive its answer from `exchange_calendars`, never from
`.weekday()` or other "weekdays" arithmetic (CLAUDE.md code standards).
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner import calendar as tp_calendar
from tradepartner.config import Settings


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


# --- calendar bounds are pinned from config, not exchange_calendars' default
# sliding (today - 20y .. today + 1y) window ------------------------------


def test_pre_2006_session_resolves() -> None:
    """A date well before exchange_calendars' unpinned 20-years-back default."""
    assert tp_calendar.is_session(date(1995, 1, 3)) is True
    open_ts = tp_calendar.session_open(date(1995, 1, 3))
    assert open_ts == datetime(1995, 1, 3, 14, 30, tzinfo=UTC)


def test_calendar_bounds_do_not_depend_on_today() -> None:
    """A fixed, config-pinned range: results for a historical date are stable
    however this test happens to be run relative to "today".
    """
    assert tp_calendar.last_session_of_month(1995, 1) == date(1995, 1, 31)


def test_last_session_of_month_outside_pinned_range_raises_value_error() -> None:
    with pytest.raises(ValueError, match="outside the configured calendar range"):
        tp_calendar.last_session_of_month(2036, 1)


# --- basic session queries -------------------------------------------------


def test_is_session_on_a_normal_trading_day() -> None:
    assert tp_calendar.is_session(date(2025, 7, 7)) is True


def test_is_session_false_on_a_holiday() -> None:
    # 2025-07-04: Independence Day, not an XNYS session.
    assert tp_calendar.is_session(date(2025, 7, 4)) is False


def test_is_session_false_on_a_weekend() -> None:
    assert tp_calendar.is_session(date(2025, 7, 5)) is False


# --- next_session / previous_session: any date, never raise ---------------


def test_next_session_skips_holiday() -> None:
    assert tp_calendar.next_session(date(2025, 7, 3)) == date(2025, 7, 7)


def test_previous_session_skips_holiday() -> None:
    assert tp_calendar.previous_session(date(2025, 7, 7)) == date(2025, 7, 3)


def test_next_session_from_a_holiday_itself() -> None:
    # 2025-07-04 is not a session; the docstring promises this works anyway.
    assert tp_calendar.next_session(date(2025, 7, 4)) == date(2025, 7, 7)


def test_next_session_from_a_weekend() -> None:
    assert tp_calendar.next_session(date(2025, 7, 5)) == date(2025, 7, 7)  # Saturday


def test_previous_session_from_a_holiday_itself() -> None:
    assert tp_calendar.previous_session(date(2025, 7, 4)) == date(2025, 7, 3)


def test_previous_session_from_a_weekend() -> None:
    assert tp_calendar.previous_session(date(2025, 7, 5)) == date(2025, 7, 3)  # Saturday


@pytest.mark.parametrize(
    "func",
    [tp_calendar.next_session, tp_calendar.previous_session],
)
def test_next_previous_session_reject_datetime(func: object) -> None:
    with pytest.raises(TypeError, match="expects a date, not a datetime"):
        func(datetime(2025, 7, 7, 12, 0, tzinfo=UTC))  # type: ignore[operator]


# --- session_open / session_close ------------------------------------------


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


def test_summer_half_day_close_is_17_utc() -> None:
    # 2025-07-03 (day before Independence Day, EDT): 1pm ET = 17:00 UTC.
    close_ts = tp_calendar.session_close(date(2025, 7, 3))
    assert close_ts == datetime(2025, 7, 3, 17, 0, tzinfo=UTC)


def test_session_open_close_after_spring_dst_change() -> None:
    # 2025-03-10: first XNYS session under EDT (DST began 2025-03-09).
    assert tp_calendar.session_open(date(2025, 3, 10)) == datetime(2025, 3, 10, 13, 30, tzinfo=UTC)


def test_session_open_close_after_fall_dst_change() -> None:
    # 2025-11-03: first XNYS session under EST (DST ended 2025-11-02).
    assert tp_calendar.session_open(date(2025, 11, 3)) == datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
    assert tp_calendar.session_close(date(2025, 11, 3)) == datetime(2025, 11, 3, 21, 0, tzinfo=UTC)


def test_session_open_raises_value_error_on_weekend() -> None:
    with pytest.raises(ValueError, match="not an XNYS session"):
        tp_calendar.session_open(date(2025, 7, 5))


def test_session_close_raises_value_error_on_holiday() -> None:
    with pytest.raises(ValueError, match="not an XNYS session"):
        tp_calendar.session_close(date(2025, 7, 4))


@pytest.mark.parametrize(
    "func",
    [tp_calendar.session_open, tp_calendar.session_close],
)
def test_session_open_close_reject_datetime(func: object) -> None:
    with pytest.raises(TypeError, match="expects a date, not a datetime"):
        func(datetime(2025, 7, 7, 12, 0, tzinfo=UTC))  # type: ignore[operator]


# --- half days ---------------------------------------------------------


def test_is_half_day_true_on_a_half_day() -> None:
    assert tp_calendar.is_half_day(date(2025, 11, 28)) is True


def test_is_half_day_false_on_a_normal_day() -> None:
    assert tp_calendar.is_half_day(date(2025, 7, 7)) is False


# --- last_session_of_month --------------------------------------------


def test_last_session_of_month() -> None:
    assert tp_calendar.last_session_of_month(2025, 7) == date(2025, 7, 31)


def test_last_session_of_month_weekend_end() -> None:
    # 2025-08-31 is a Sunday.
    assert tp_calendar.last_session_of_month(2025, 8) == date(2025, 8, 29)


def test_last_session_of_month_holiday_end() -> None:
    # 2024-03-29 is Good Friday.
    assert tp_calendar.last_session_of_month(2024, 3) == date(2024, 3, 28)


def test_last_session_of_month_half_day_end() -> None:
    # 2025-11-30 is a Sunday; 2025-11-28 (the prior Friday) is also a half day.
    assert tp_calendar.last_session_of_month(2025, 11) == date(2025, 11, 28)


# --- last_completed_session ----------------------------------------------


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


def test_last_completed_session_one_second_before_close() -> None:
    as_of = datetime(2025, 7, 7, 19, 59, 59, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_on_a_weekend() -> None:
    as_of = datetime(2025, 7, 5, 12, 0, tzinfo=UTC)  # Saturday
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_on_a_holiday() -> None:
    as_of = datetime(2025, 7, 4, 12, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(as_of) == date(2025, 7, 3)


def test_last_completed_session_half_day_close_boundary() -> None:
    just_before = datetime(2025, 11, 28, 17, 59, 59, tzinfo=UTC)
    at_close = datetime(2025, 11, 28, 18, 0, 0, tzinfo=UTC)
    assert tp_calendar.last_completed_session(just_before) == date(2025, 11, 26)
    assert tp_calendar.last_completed_session(at_close) == date(2025, 11, 28)


def test_last_completed_session_requires_tz_aware() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        tp_calendar.last_completed_session(datetime(2025, 7, 7, 15, 0))  # noqa: DTZ001


# --- sessions_in_month_window ---------------------------------------------


def test_sessions_in_month_window_precise_definition() -> None:
    """`end_session - relativedelta(months=months) < s <= end_session`.

    2025-08-31 - 12 months = 2024-08-31, a Saturday (not a session), so the
    window's first session is the next one that *is* a session: 2024-09-03
    (2024-08-31/09-01 are a weekend, 2024-09-02 is Labor Day).
    """
    sessions = tp_calendar.sessions_in_month_window(date(2025, 8, 31), months=12)
    assert sessions[0] == date(2024, 9, 3)
    assert sessions[-1] == date(2025, 8, 29)
    assert len(sessions) == 249
    assert sessions == sorted(set(sessions))
    assert all(tp_calendar.is_session(s) for s in sessions)


def test_sessions_in_month_window_one_month() -> None:
    sessions = tp_calendar.sessions_in_month_window(date(2025, 7, 7), months=1)
    assert sessions[0] == date(2025, 6, 9)
    assert sessions[-1] == date(2025, 7, 7)
    assert len(sessions) == 19


def test_sessions_in_month_window_months_below_one_raises() -> None:
    with pytest.raises(ValueError, match="months must be >= 1"):
        tp_calendar.sessions_in_month_window(date(2025, 7, 31), months=0)


def test_all_sessions_skips_holidays_and_spans_the_pinned_range() -> None:
    sessions = tp_calendar.all_sessions()
    assert sessions[0] == date(1990, 1, 2)  # first session in the pinned range
    assert sessions[-1] == tp_calendar.previous_session(date(2036, 1, 1))
    assert date(2021, 1, 18) not in sessions  # MLK Day
    assert date(2021, 1, 15) in sessions
    assert list(sessions) == sorted(set(sessions))
    assert tp_calendar.all_sessions() is sessions


# --- the calendar range is read from config once per config state (#154) ----


def _count_settings_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    reads = [0]
    real = tp_calendar.get_settings

    def counting() -> Settings:
        reads[0] += 1
        return real()

    monkeypatch.setattr(tp_calendar, "get_settings", counting)
    return reads


def test_session_helpers_do_not_rebuild_settings_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_settings()` costs ~5 ms (env + dotenv parse); the helpers are called
    thousands of times per backtest, so an unchanged config is read once."""
    reads = _count_settings_reads(monkeypatch)
    tp_calendar._calendar_bounds.cache_clear()
    day = date(2024, 1, 2)
    for _ in range(50):
        tp_calendar.is_session(day)
        tp_calendar.next_session(day)
        tp_calendar.previous_session(day)
        tp_calendar.last_session_of_month(2024, 1)
        tp_calendar.all_sessions()
    assert reads[0] == 1


def test_environment_change_to_the_range_takes_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tp_calendar.all_sessions()[0] < date(2000, 1, 1)
    monkeypatch.setenv("CALENDAR__START", "2000-01-01")
    assert tp_calendar.all_sessions()[0] == date(2000, 1, 3)
    monkeypatch.delenv("CALENDAR__START")
    assert tp_calendar.all_sessions()[0] < date(2000, 1, 1)


def test_env_file_change_to_the_range_takes_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "calendar.env"
    env_file.write_text("CALENDAR__END=2030-12-31\n")
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(env_file))
    assert tp_calendar.all_sessions()[-1] == date(2030, 12, 31)
    env_file.write_text("CALENDAR__END=2031-12-31\n# edited\n")
    assert tp_calendar.all_sessions()[-1] == date(2031, 12, 31)
    env_file.unlink()
    assert tp_calendar.all_sessions()[-1] > date(2031, 12, 31)

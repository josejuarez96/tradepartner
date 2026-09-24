"""XNYS trading-calendar wrapper.

Everything about "which day is a trading day" and "when did it open or
close" goes through `exchange_calendars` here, never through weekday-number
arithmetic (CLAUDE.md code standards): holidays and half days do not follow
a Monday-through-Friday pattern.

A **session** (a trading day) is represented as `datetime.date`: it denotes
a calendar day, not an instant. An **instant** (a session's open or close)
is a tz-aware UTC `datetime.datetime`, per CLAUDE.md's "datetimes are always
timezone-aware UTC" rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

_CALENDAR_NAME = "XNYS"


@lru_cache(maxsize=1)
def _calendar() -> xcals.ExchangeCalendar:
    """Build (once) and cache the XNYS calendar."""
    return xcals.get_calendar(_CALENDAR_NAME)


def _to_utc_datetime(ts: pd.Timestamp) -> datetime:
    localized = ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)
    result = localized.to_pydatetime()
    assert isinstance(result, datetime)
    return result


def _to_date(ts: pd.Timestamp) -> date:
    result = ts.date()
    assert isinstance(result, date)
    return result


def is_session(day: date) -> bool:
    """True if `day` is an XNYS trading session."""
    return bool(_calendar().is_session(pd.Timestamp(day)))


def next_session(day: date) -> date:
    """The first XNYS session strictly after `day`."""
    return _to_date(_calendar().next_session(pd.Timestamp(day)))


def previous_session(day: date) -> date:
    """The last XNYS session strictly before `day`."""
    return _to_date(_calendar().previous_session(pd.Timestamp(day)))


def session_open(day: date) -> datetime:
    """The tz-aware UTC official open of session `day`."""
    return _to_utc_datetime(_calendar().session_open(pd.Timestamp(day)))


def session_close(day: date) -> datetime:
    """The tz-aware UTC official close of session `day`."""
    return _to_utc_datetime(_calendar().session_close(pd.Timestamp(day)))


def is_half_day(day: date) -> bool:
    """True if session `day` is an XNYS early-close (half) day."""
    return bool(pd.Timestamp(day) in _calendar().early_closes)


def last_session_of_month(year: int, month: int) -> date:
    """The last XNYS session in the given calendar month."""
    cal = _calendar()
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    sessions = cal.sessions_in_range(start, end)
    if len(sessions) == 0:
        raise ValueError(f"no XNYS sessions in {year:04d}-{month:02d}")
    return _to_date(sessions[-1])


def last_completed_session(as_of: datetime) -> date:
    """The most recent session whose close is at or before `as_of`.

    `as_of` must be tz-aware. Works uniformly whether `as_of` falls mid-session,
    exactly at a close, after a close, on a weekend, or on a holiday.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be tz-aware")
    ts = pd.Timestamp(as_of).tz_convert(UTC)
    # `minute_to_past_session` returns the session that closed before `ts`
    # unless `ts` is itself a trading minute, in which case it returns the
    # session preceding the one in progress -- exactly "last completed
    # session" semantics, verified against the close boundary itself.
    return _to_date(_calendar().minute_to_past_session(ts))


def sessions_in_month_window(end_session: date, months: int = 12) -> list[date]:
    """All XNYS sessions in the `months` calendar months up to and including
    the month of `end_session`, truncated at `end_session` itself.
    """
    cal = _calendar()
    end_month_start = pd.Timestamp(year=end_session.year, month=end_session.month, day=1)
    start = end_month_start - pd.DateOffset(months=months - 1)
    sessions = cal.sessions_in_range(start, pd.Timestamp(end_session))
    return [_to_date(s) for s in sessions]

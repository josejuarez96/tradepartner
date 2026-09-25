"""XNYS trading-calendar wrapper.

Everything about "which day is a trading day" and "when did it open or
close" goes through `exchange_calendars` here, never through weekday-number
arithmetic (CLAUDE.md code standards): holidays and half days do not follow
a Monday-through-Friday pattern.

A **session** (a trading day) is represented as `datetime.date`: it denotes
a calendar day, not an instant. An **instant** (a session's open or close)
is a tz-aware UTC `datetime.datetime`, per CLAUDE.md's "datetimes are always
timezone-aware UTC" rule.

The calendar's valid date range is pinned from config
(`calendar.start`/`calendar.end`), not `exchange_calendars`' default sliding
window (today - 20y .. today + 1y): an unpinned range rejects any date
older than ~20 years and drifts with today's date, which is not
reproducible.

The range is read through `get_settings()`, which parses the environment and
`.env` afresh on every call (~5 ms; deliberately uncached, see `config.py`).
The session helpers run thousands of times per backtest, so the range is
cached per **config state** instead (#154): a fingerprint of everything
`Settings` can read for `calendar`, namely the whole environment (`.env` lines
may expand `${VAR}` from it) and the `.env` file's path, inode, size, mtime and
ctime (ctime cannot be reset by `utime`, and an atomic replace changes the
inode). Changing any of them re-reads the range.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd
from dateutil.relativedelta import relativedelta
from exchange_calendars.errors import DateOutOfBounds, NotSessionError

from tradepartner.config import _default_env_file, get_settings

_CALENDAR_NAME = "XNYS"


@lru_cache(maxsize=1)
def _calendar(start: date, end: date) -> xcals.ExchangeCalendar:
    """Build (once per distinct `(start, end)`) and cache the XNYS calendar."""
    return xcals.get_calendar(_CALENDAR_NAME, start=pd.Timestamp(start), end=pd.Timestamp(end))


def _config_fingerprint() -> tuple[object, ...]:
    """What `Settings` can read for `calendar`, cheaply: the whole environment
    and the `.env` file's state (a scan of `os.environ` and one `stat`)."""
    env = frozenset(os.environ.items())
    path = _default_env_file()
    try:
        stat = path.stat()
    except OSError:
        return env, str(path), None
    return env, str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=8)
def _calendar_bounds(fingerprint: tuple[object, ...]) -> tuple[date, date]:
    """`(calendar.start, calendar.end)` for one config state (`fingerprint` is
    the cache key only)."""
    cfg = get_settings().calendar
    return cfg.start, cfg.end


def _bounds() -> tuple[date, date]:
    return _calendar_bounds(_config_fingerprint())


def _get_calendar() -> xcals.ExchangeCalendar:
    """The XNYS calendar, bounded by the current `calendar.start`/`.end` config."""
    return _calendar(*_bounds())


def _reject_datetime(day: date, *, func: str) -> None:
    """`datetime` is a subclass of `date`; reject it explicitly so a caller
    can't accidentally pass an instant where a session (a calendar day) is
    meant — `pd.Timestamp` would otherwise silently drop the time-of-day.
    """
    if isinstance(day, datetime):
        raise TypeError(f"{func}() expects a date, not a datetime: {day!r}")


def _to_utc_datetime(ts: pd.Timestamp) -> datetime:
    return ts.tz_convert(UTC).to_pydatetime()


def _to_date(ts: pd.Timestamp) -> date:
    return ts.date()


def is_session(day: date) -> bool:
    """True if `day` is an XNYS trading session."""
    return bool(_get_calendar().is_session(pd.Timestamp(day)))


def next_session(day: date) -> date:
    """The first XNYS session strictly after `day` (`day` need not itself be a session)."""
    _reject_datetime(day, func="next_session")
    following_day = pd.Timestamp(day) + pd.Timedelta(days=1)
    return _to_date(_get_calendar().date_to_session(following_day, "next"))


def previous_session(day: date) -> date:
    """The last XNYS session strictly before `day` (`day` need not itself be a session)."""
    _reject_datetime(day, func="previous_session")
    preceding_day = pd.Timestamp(day) - pd.Timedelta(days=1)
    return _to_date(_get_calendar().date_to_session(preceding_day, "previous"))


def session_open(day: date) -> datetime:
    """The tz-aware UTC official open of session `day`.

    Raises `ValueError` if `day` is not itself an XNYS session.
    """
    _reject_datetime(day, func="session_open")
    try:
        ts = _get_calendar().session_open(pd.Timestamp(day))
    except NotSessionError as exc:
        raise ValueError(f"not an XNYS session: {day.isoformat()}") from exc
    return _to_utc_datetime(ts)


def session_close(day: date) -> datetime:
    """The tz-aware UTC official close of session `day`.

    Raises `ValueError` if `day` is not itself an XNYS session.
    """
    _reject_datetime(day, func="session_close")
    try:
        ts = _get_calendar().session_close(pd.Timestamp(day))
    except NotSessionError as exc:
        raise ValueError(f"not an XNYS session: {day.isoformat()}") from exc
    return _to_utc_datetime(ts)


def is_half_day(day: date) -> bool:
    """True if session `day` is an XNYS early-close (half) day."""
    return bool(pd.Timestamp(day) in _get_calendar().early_closes)


@lru_cache(maxsize=1)
def _all_sessions(start: date, end: date) -> tuple[date, ...]:
    """Every session of the `(start, end)` calendar, ascending, built once."""
    return tuple(_to_date(s) for s in _calendar(start, end).sessions)


def all_sessions() -> tuple[date, ...]:
    """Every XNYS session in the configured `calendar.start`..`calendar.end`
    range, ascending. Cached per range; the same tuple object is returned
    while the range is unchanged."""
    return _all_sessions(*_bounds())


def last_session_of_month(year: int, month: int) -> date:
    """The last XNYS session in the given calendar month."""
    cal = _get_calendar()
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    try:
        sessions = cal.sessions_in_range(start, end)
    except DateOutOfBounds as exc:
        raise ValueError(
            f"{year:04d}-{month:02d} is outside the configured calendar range "
            f"({cal.first_session.date()}..{cal.last_session.date()})"
        ) from exc
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
    return _to_date(_get_calendar().minute_to_past_session(ts))


def sessions_in_month_window(end_session: date, months: int) -> list[date]:
    """Sessions `s` with `end_session - relativedelta(months=months) < s <= end_session`.

    `months` has no default: callers pass it explicitly (e.g.
    `settings.universe.min_history_months`) so the window length is always a
    visible config value, never a silently-assumed constant.
    """
    if months < 1:
        raise ValueError(f"months must be >= 1, got {months}")
    cal = _get_calendar()
    start_exclusive = end_session - relativedelta(months=months)
    start_ts = pd.Timestamp(start_exclusive)
    sessions = cal.sessions_in_range(start_ts, pd.Timestamp(end_session))
    return [_to_date(s) for s in sessions if s > start_ts]

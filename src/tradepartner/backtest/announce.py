"""Announcement sessions, pure (plan T415; backtest spec amendment 2026-11-16 (#3504), item 5).

`reaction_session` maps an 8-K's acceptance instant to s0, the first XNYS session whose
close the filing precedes. `dropped_sessions` is the window left out of a name's score:
s0 and the next `post_sessions` XNYS sessions. Both take the trading calendar as an
argument (the `tradepartner.calendar` module satisfies `SessionCalendar`); no EDGAR
calendar, store or clock is read, and no session count or hour is a literal here: every
one is an argument (an AST check pins it).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")


class SessionCalendar(Protocol):
    """The XNYS lookups this module needs (`tradepartner.calendar` provides them)."""

    def is_session(self, day: date) -> bool:
        """True if `day` is a trading session."""
        ...

    def next_session(self, day: date) -> date:
        """The first session strictly after `day`."""
        ...

    def session_close(self, day: date) -> datetime:
        """The tz-aware UTC close of session `day` (half days included)."""
        ...


def reaction_session(accepted_at: datetime, calendar: SessionCalendar) -> date:
    """s0 for an acceptance at `accepted_at` (tz-aware; a naive value raises `ValueError`).

    With D the New York date of `accepted_at`: if D is a session and `accepted_at` is
    strictly before its close, s0 = D; otherwise (after the close, a weekend, a holiday)
    s0 = the next session after D. The EDGAR `filed` date is never used.
    """
    instant = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
    day = instant.astimezone(_NEW_YORK).date()
    if calendar.is_session(day) and instant < calendar.session_close(day):
        return day
    return calendar.next_session(day)


def dropped_sessions(s0: date, post_sessions: int, calendar: SessionCalendar) -> tuple[date, ...]:
    """The sessions left out for an event: `s0` and the next `post_sessions` XNYS sessions.

    `s0` must be a session (else `ValueError`) and `post_sessions` non-negative. Holidays
    and weekends are skipped by the calendar, never counted.
    """
    if isinstance(s0, datetime):
        raise TypeError(f"s0 must be a date, not a datetime: {s0!r}")
    if len(range(post_sessions)) != post_sessions:  # a negative count has an empty range
        raise ValueError(f"post_sessions must be non-negative, got {post_sessions}")
    if not calendar.is_session(s0):
        raise ValueError(f"s0 is not an XNYS session: {s0.isoformat()}")
    window = [s0]
    latest = s0
    for _ in range(post_sessions):
        latest = calendar.next_session(latest)
        window.append(latest)
    return tuple(window)

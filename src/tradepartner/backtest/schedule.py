"""Rebalance and fill sessions (backtest spec definitions; ADR 0006 cadence).

A rebalance session T_i is the last XNYS session of a calendar month; the read time of
a step is close(T_i), tz-aware UTC; the fill session F_i is the session after T_i. All
three come from `tradepartner.calendar`, never from weekday arithmetic: month ends fall
on Thursdays before Good Friday, and fill sessions land after holidays or on half days.
"""

from __future__ import annotations

from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from tradepartner.calendar import last_session_of_month, next_session, session_close


def _reject_datetime(day: date, *, name: str) -> None:
    if isinstance(day, datetime):
        raise TypeError(f"{name} expects a date (a session), not a datetime: {day!r}")


def _require_rebalance_session(day: date, *, name: str) -> None:
    _reject_datetime(day, name=name)
    if day != last_session_of_month(day.year, day.month):
        raise ValueError(
            f"{day.isoformat()} is not a rebalance session (last session of its month)"
        )


def rebalance_sessions(start: date, end: date) -> list[date]:
    """Every rebalance session T with `start <= T <= end`, ascending.

    A month counts only if its last session falls inside the window, so a window ending
    before a month's last session does not rebalance in that month. Raises `ValueError`
    if `start` is after `end`, `TypeError` for a datetime.
    """
    _reject_datetime(start, name="start")
    _reject_datetime(end, name="end")
    if start > end:
        raise ValueError(f"start {start.isoformat()} is after end {end.isoformat()}")
    sessions: list[date] = []
    month = start.replace(day=1)
    while month <= end:
        session = last_session_of_month(month.year, month.month)
        if start <= session <= end:
            sessions.append(session)
        month += relativedelta(months=1)
    return sessions


def fill_session(rebalance_session: date) -> date:
    """F_i = the XNYS session after rebalance session T_i."""
    _require_rebalance_session(rebalance_session, name="fill_session")
    return next_session(rebalance_session)


def read_time(rebalance_session: date) -> datetime:
    """close(T_i), tz-aware UTC: the `t` of every provider read at that rebalance."""
    _require_rebalance_session(rebalance_session, name="read_time")
    return session_close(rebalance_session)

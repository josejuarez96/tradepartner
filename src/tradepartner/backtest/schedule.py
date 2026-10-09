"""Rebalance and fill sessions at a cadence (backtest spec definitions; strategy-lab
spec "Cadence"; ADR 0012, superseding ADR 0006's cadence).

A rebalance session T_i is the last XNYS session of each calendar month (`month_end`,
the Phase 3 rule and the default), of each ISO week (`week_end`), or every session
(`daily`); the read time of a step is close(T_i), tz-aware UTC; the fill session F_i is
the session after T_i (at `daily`, F_i = T_{i+1}). All three come from
`tradepartner.calendar`, never from weekday arithmetic: month and week ends fall on
Thursdays before Good Friday, and fill sessions land after holidays or on half days.

`PERIODS_PER_YEAR` is the one place the annualisation counts 12, 52 and 252 live: a
constant derived from the cadence, not config.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

from tradepartner.calendar import (
    is_session,
    last_session_of_month,
    last_session_of_week,
    next_session,
    rebalance_sessions_between,
    session_close,
)
from tradepartner.config import Cadence

__all__ = [
    "MONTHS_PER_YEAR",
    "PERIODS_PER_YEAR",
    "Cadence",
    "fill_session",
    "periods_per_year",
    "read_time",
    "rebalance_sessions",
]

PERIODS_PER_YEAR: Final[dict[Cadence, int]] = {"month_end": 12, "week_end": 52, "daily": 252}
MONTHS_PER_YEAR: Final[int] = PERIODS_PER_YEAR["month_end"]


def periods_per_year(cadence: Cadence) -> int:
    """Rebalance periods per year at `cadence` (strategy-lab spec, "Period")."""
    return PERIODS_PER_YEAR[cadence]


def _reject_datetime(day: date, *, name: str) -> None:
    if isinstance(day, datetime):
        raise TypeError(f"{name} expects a date (a session), not a datetime: {day!r}")


def _is_rebalance_session(day: date, cadence: Cadence) -> bool:
    if cadence == "month_end":
        return day == last_session_of_month(day.year, day.month)
    if cadence == "week_end":
        iso_year, iso_week, _ = day.isocalendar()
        try:
            return day == last_session_of_week(iso_year, iso_week)
        except ValueError:
            return False
    return is_session(day)


def _require_rebalance_session(day: date, cadence: Cadence, *, name: str) -> None:
    _reject_datetime(day, name=name)
    if not _is_rebalance_session(day, cadence):
        raise ValueError(f"{day.isoformat()} is not a rebalance session at cadence {cadence}")


def rebalance_sessions(start: date, end: date, cadence: Cadence = "month_end") -> list[date]:
    """Every rebalance session T at `cadence` with `start <= T <= end`, ascending.

    A month (or ISO week) counts only if its last session falls inside the window, so a
    window ending before that session rebalances last at the previous one. Raises
    `ValueError` if `start` is after `end`, `TypeError` for a datetime.
    """
    _reject_datetime(start, name="start")
    _reject_datetime(end, name="end")
    return rebalance_sessions_between(start, end, cadence)


def fill_session(rebalance_session: date, cadence: Cadence = "month_end") -> date:
    """F_i = the XNYS session after rebalance session T_i (T_{i+1} at `daily`)."""
    _require_rebalance_session(rebalance_session, cadence, name="fill_session")
    return next_session(rebalance_session)


def read_time(rebalance_session: date, cadence: Cadence = "month_end") -> datetime:
    """close(T_i), tz-aware UTC: the `t` of every provider read at that rebalance."""
    _require_rebalance_session(rebalance_session, cadence, name="read_time")
    return session_close(rebalance_session)

"""Rebalance sessions at every cadence (strategy-lab spec "Cadence and signal anchor",
first criterion; T94)."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

import pytest
from dateutil.relativedelta import relativedelta

from tradepartner import calendar as tp_calendar
from tradepartner.backtest.schedule import (
    MONTHS_PER_YEAR,
    PERIODS_PER_YEAR,
    fill_session,
    periods_per_year,
    read_time,
    rebalance_sessions,
)
from tradepartner.calendar import all_sessions, last_session_of_month, session_close

# The fixture store's range: its first session through the end of H1's holdout.
START = date(2016, 1, 4)
END = date(2026, 9, 30)


def _phase3_month_ends(start: date, end: date) -> list[date]:
    """The Phase 3 `rebalance_sessions(start, end)` loop, as written before T94."""
    sessions: list[date] = []
    month = start.replace(day=1)
    while month <= end:
        session = last_session_of_month(month.year, month.month)
        if start <= session <= end:
            sessions.append(session)
        month += relativedelta(months=1)
    return sessions


def test_periods_per_year_table() -> None:
    assert PERIODS_PER_YEAR == {"month_end": 12, "week_end": 52, "daily": 252}
    assert MONTHS_PER_YEAR == 12
    assert [periods_per_year(c) for c in ("month_end", "week_end", "daily")] == [12, 52, 252]


def test_month_end_equals_the_phase3_function() -> None:
    assert rebalance_sessions(START, END, "month_end") == _phase3_month_ends(START, END)
    assert rebalance_sessions(START, END) == _phase3_month_ends(START, END)
    mid = (date(2024, 1, 15), date(2024, 6, 27))  # both ends mid-month
    assert rebalance_sessions(*mid, "month_end") == _phase3_month_ends(*mid)


def test_week_end_is_the_last_session_of_each_iso_week() -> None:
    weekly = rebalance_sessions(START, END, "week_end")
    weeks = [s.isocalendar()[:2] for s in weekly]
    assert len(weeks) == len(set(weeks))
    by_week: dict[tuple[int, int], date] = {}
    for s in all_sessions():
        by_week[s.isocalendar()[:2]] = s
    assert weekly == sorted(s for s in by_week.values() if START <= s <= END)
    # END (Wednesday 2026-09-30) cuts its week short: that week does not rebalance.
    assert weekly[-1] == date(2026, 9, 25)
    assert date(2024, 3, 28) in weekly  # Good Friday week
    assert date(2024, 3, 29) not in weekly
    assert date(2025, 7, 3) in weekly  # Independence Day week
    assert date(2025, 7, 4) not in weekly


def test_daily_equals_all_sessions() -> None:
    assert rebalance_sessions(START, END, "daily") == [
        s for s in all_sessions() if START <= s <= END
    ]


def test_window_ending_mid_week_rebalances_at_the_previous_week_end() -> None:
    sessions = rebalance_sessions(date(2024, 6, 3), date(2024, 6, 19), "week_end")
    assert sessions == [date(2024, 6, 7), date(2024, 6, 14)]  # 2024-06-19 is a Wednesday


def test_fill_session_is_the_next_session_at_every_cadence() -> None:
    assert fill_session(date(2024, 3, 28), "week_end") == date(2024, 4, 1)
    assert fill_session(date(2024, 3, 28), "month_end") == date(2024, 4, 1)
    daily = rebalance_sessions(date(2024, 6, 3), date(2024, 7, 10), "daily")
    for t_i, t_next in pairwise(daily):
        assert fill_session(t_i, "daily") == t_next
        assert read_time(t_i, "daily") == session_close(t_i)


@pytest.mark.parametrize(
    ("day", "cadence"),
    [
        (date(2024, 3, 27), "week_end"),  # a session, but not its week's last
        (date(2024, 3, 29), "week_end"),  # Good Friday, a holiday
        (date(2024, 6, 14), "month_end"),  # a week end, not a month end
        (date(2024, 6, 15), "daily"),  # a Saturday
    ],
)
def test_non_rebalance_sessions_are_refused_at_their_cadence(day: date, cadence: str) -> None:
    for func in (fill_session, read_time):
        with pytest.raises(ValueError, match="rebalance session"):
            func(day, cadence)  # type: ignore[arg-type]


def test_a_removed_friday_moves_the_weeks_rebalance_to_thursday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    friday = date(2024, 6, 14)
    synthetic = tuple(s for s in all_sessions() if s != friday)
    monkeypatch.setattr(tp_calendar, "all_sessions", lambda: synthetic)
    sessions = rebalance_sessions(date(2024, 6, 3), date(2024, 6, 21), "week_end")
    assert sessions == [date(2024, 6, 7), date(2024, 6, 13), date(2024, 6, 21)]

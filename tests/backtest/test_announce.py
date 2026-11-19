"""Announcement sessions, pure (plan T415; backtest spec amendment 2026-11-16 (#3504),
item 5 "Session mapping" and "The dropped sessions", acceptance "Event timing").

Every instant below is given in UTC; the comment beside it is its New York wall time.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradepartner import calendar as xnys
from tradepartner.backtest import announce
from tradepartner.backtest.announce import dropped_sessions, reaction_session

POST = 2  # the default `irrx.announce_post_sessions`; a test argument, not module code


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_preopen_acceptance_is_that_session() -> None:
    # Tue 2025-07-15 07:00 EDT
    assert reaction_session(_utc(2025, 7, 15, 11, 0), xnys) == date(2025, 7, 15)


def test_intraday_acceptance_is_that_session() -> None:
    # Tue 2025-07-15 15:30 EDT
    assert reaction_session(_utc(2025, 7, 15, 19, 30), xnys) == date(2025, 7, 15)


def test_1630_filed_date_trap_is_next_session() -> None:
    # Tue 2025-07-15 16:30 EDT: `filed` = 07-15 would put s0 a session early
    assert reaction_session(_utc(2025, 7, 15, 20, 30), xnys) == date(2025, 7, 16)


def test_2030_is_next_session() -> None:
    # Tue 2025-07-15 20:30 EDT = 00:30Z on 07-16: the UTC date is not the New York date
    assert reaction_session(_utc(2025, 7, 16, 0, 30), xnys) == date(2025, 7, 16)


def test_same_utc_clock_either_side_of_dst_maps_differently() -> None:
    summer = reaction_session(_utc(2025, 7, 15, 20, 30), xnys)  # 16:30 EDT, after the close
    winter = reaction_session(_utc(2026, 1, 13, 20, 30), xnys)  # 15:30 EST, intraday
    assert summer == date(2025, 7, 16)
    assert winter == date(2026, 1, 13)


def test_acceptance_at_the_close_instant_is_next_session() -> None:
    close = xnys.session_close(date(2025, 7, 15))
    assert reaction_session(close - timedelta(microseconds=1), xnys) == date(2025, 7, 15)
    assert reaction_session(close, xnys) == date(2025, 7, 16)


def test_saturday_acceptance_is_next_session() -> None:
    # Sat 2025-07-12 10:00 EDT
    assert reaction_session(_utc(2025, 7, 12, 14, 0), xnys) == date(2025, 7, 14)


def test_holiday_acceptance_is_next_session() -> None:
    # Thanksgiving Thu 2025-11-27 10:00 EST; the next session is the half day
    assert reaction_session(_utc(2025, 11, 27, 15, 0), xnys) == date(2025, 11, 28)


def test_half_day_1230_is_that_day_and_1400_is_next() -> None:
    # Fri 2025-11-28 closes 13:00 EST
    assert reaction_session(_utc(2025, 11, 28, 17, 30), xnys) == date(2025, 11, 28)  # 12:30
    assert reaction_session(_utc(2025, 11, 28, 19, 0), xnys) == date(2025, 12, 1)  # 14:00


def test_non_utc_offset_is_read_as_the_same_instant() -> None:
    plus9 = timezone(timedelta(hours=9))
    # 2025-07-16 05:30 +09:00 is Tue 2025-07-15 16:30 EDT
    at = datetime(2025, 7, 16, 5, 30, tzinfo=plus9)
    assert reaction_session(at, xnys) == date(2025, 7, 16)


def test_naive_datetime_raises() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        reaction_session(datetime(2025, 7, 15, 11, 0), xnys)  # noqa: DTZ001


def test_window_is_s0_plus_post_sessions() -> None:
    assert dropped_sessions(date(2025, 7, 15), POST, xnys) == (
        date(2025, 7, 15),
        date(2025, 7, 16),
        date(2025, 7, 17),
    )


def test_window_skips_a_weekend() -> None:
    assert dropped_sessions(date(2025, 7, 17), POST, xnys) == (
        date(2025, 7, 17),
        date(2025, 7, 18),
        date(2025, 7, 21),
    )


def test_window_skips_a_holiday() -> None:
    # Christmas Thu 2025-12-25 is closed
    assert dropped_sessions(date(2025, 12, 24), POST, xnys) == (
        date(2025, 12, 24),
        date(2025, 12, 26),
        date(2025, 12, 29),
    )


def test_window_with_zero_post_sessions_is_s0_alone() -> None:
    assert dropped_sessions(date(2025, 7, 15), len(()), xnys) == (date(2025, 7, 15),)


def test_window_start_must_be_a_session() -> None:
    with pytest.raises(ValueError, match="not an XNYS session"):
        dropped_sessions(date(2025, 7, 12), POST, xnys)


def test_window_rejects_a_datetime_and_a_negative_count() -> None:
    with pytest.raises(TypeError):
        dropped_sessions(_utc(2025, 7, 15, 0, 0), POST, xnys)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="post_sessions"):
        dropped_sessions(date(2025, 7, 15), -POST, xnys)


def test_only_the_injected_calendar_is_used() -> None:
    """A recording calendar sees every lookup: nothing else (no EDGAR calendar) is read."""
    calls: list[str] = []

    class Recorder:
        def is_session(self, day: date) -> bool:
            calls.append("is_session")
            return xnys.is_session(day)

        def next_session(self, day: date) -> date:
            calls.append("next_session")
            return xnys.next_session(day)

        def session_close(self, day: date) -> datetime:
            calls.append("session_close")
            return xnys.session_close(day)

    rec = Recorder()
    assert reaction_session(_utc(2025, 7, 15, 20, 30), rec) == date(2025, 7, 16)
    assert dropped_sessions(date(2025, 7, 16), POST, rec)
    assert calls
    assert set(calls) <= {"is_session", "next_session", "session_close"}


def test_module_has_no_numeric_literal_and_no_edgar_import() -> None:
    """Spec item 6's AST check: a session count or an hour is always an argument."""
    path = Path(announce.__file__)
    tree = ast.parse(path.read_text())
    numbers = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float | complex)
        and not isinstance(n.value, bool)
    ]
    assert numbers == []
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not {m for m in modules if "edgar" in m or "store" in m}

"""Per-book buy timing and the pure arm function (T369)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from tradepartner.calendar import all_sessions, session_close, session_open
from tradepartner.config import BookExecution, Settings
from tradepartner.execution.buy_timing import (
    allowed_time_in_force,
    arm_for,
    execution_for,
    latest_batch_start,
    submit_window_for,
)

SESSIONS = all_sessions()


def _alt(start: date) -> BookExecution:
    return BookExecution(buy_timing="alternate", alternate_start=start)


def test_at_open_is_v1_and_after_sells_v0_everywhere() -> None:
    for s in SESSIONS[100:110]:
        assert arm_for(BookExecution(buy_timing="at_open"), s, SESSIONS) == "V1"
        assert arm_for(BookExecution(), s, SESSIONS) == "V0"


def test_alternate_counts_sessions_across_a_holiday() -> None:
    # 2026-11-23 Mon .. 2026-11-27 Fri spans Thanksgiving (11-26 closed).
    start = date(2026, 11, 23)
    ex = _alt(start)
    got = [
        arm_for(ex, d, SESSIONS)
        for d in (
            date(2026, 11, 23),
            date(2026, 11, 24),
            date(2026, 11, 25),
            date(2026, 11, 27),
            date(2026, 11, 30),
        )
    ]
    assert got == ["V1", "V0", "V1", "V0", "V1"]  # 11-27 is ordinal 3, not 4 weekdays


def test_alternate_before_start_is_v0() -> None:
    assert arm_for(_alt(date(2026, 11, 23)), date(2026, 11, 20), SESSIONS) == "V0"


def test_alternate_non_session_day_refused() -> None:
    with pytest.raises(ValueError, match="not in the session list"):
        arm_for(_alt(date(2026, 11, 23)), date(2026, 11, 26), SESSIONS)


def test_absent_book_gets_defaults() -> None:
    ex = execution_for(Settings(), "main")
    assert ex.buy_timing == "after_sells"
    assert ex.cash_sleeve_fraction == 0
    assert ex.auction == "none"
    assert allowed_time_in_force(ex) == frozenset({"day"})


def test_book_in_map_is_returned() -> None:
    s = Settings(paper={"execution": {"daily": {"buy_timing": "at_open"}}})  # type: ignore[arg-type]
    assert execution_for(s, "daily").buy_timing == "at_open"
    assert execution_for(s, "other").buy_timing == "after_sells"


def test_bad_book_token_refused() -> None:
    with pytest.raises(ValueError, match="book_id"):
        execution_for(Settings(), "bad-id")


def test_opening_auction_refused_naming_t376() -> None:
    with pytest.raises(ValidationError, match="T376"):
        BookExecution(auction="opening")


def test_closing_auction_refused() -> None:
    with pytest.raises(ValidationError):
        BookExecution(auction="closing")  # type: ignore[arg-type]


def test_allowed_tif_with_opening_includes_opg() -> None:
    ex = BookExecution.model_construct(auction="opening")
    assert allowed_time_in_force(ex) == frozenset({"day", "opg"})


def test_non_session_alternate_start_refused_by_execution_for() -> None:
    s = Settings(
        paper={  # type: ignore[arg-type]
            "execution": {"daily": {"buy_timing": "alternate", "alternate_start": "2026-11-26"}}
        }
    )
    with pytest.raises(ValueError, match="not an XNYS session"):
        execution_for(s, "daily")


def test_get_settings_with_alternate_from_env_does_not_recurse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradepartner.config import get_settings

    monkeypatch.setenv(
        "PAPER__EXECUTION",
        '{"daily": {"buy_timing": "alternate", "alternate_start": "2026-11-23"}}',
    )
    assert get_settings().paper.execution["daily"].buy_timing == "alternate"


# --- the per-book submit window (paper spec amendment 2026-11-03 items 1 and 9, T377) ---

_MID = {"daily": {"submit_at": "midsession"}}


def _utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


@pytest.mark.parametrize(
    "entry", [None, {}, {"submit_at": "open"}], ids=["absent", "empty", "open"]
)
def test_a_book_absent_or_at_open_keeps_todays_window(entry: dict[str, object] | None) -> None:
    paper: dict[str, object] = {"submit_window_before_open_minutes": 90}
    if entry is not None:
        paper["execution"] = {"daily": entry}
    s = Settings(_env_file=None, paper=paper)  # type: ignore[arg-type]
    opening = session_open(date(2026, 10, 30))
    assert submit_window_for(s, "daily", opening) == (
        opening - timedelta(minutes=90),
        opening + timedelta(minutes=30),
    )
    assert latest_batch_start(s, "daily", opening) is None


@pytest.mark.parametrize(
    ("session", "start", "end"),
    [
        # Friday before the DST change: EDT, the open 13:30 UTC.
        pytest.param(
            date(2026, 10, 30), _utc(2026, 10, 30, 14, 30), _utc(2026, 10, 30, 15, 30), id="edt"
        ),
        # Monday after it: EST, the open 14:30 UTC; the window moves with the open.
        pytest.param(
            date(2026, 11, 2), _utc(2026, 11, 2, 15, 30), _utc(2026, 11, 2, 16, 30), id="est"
        ),
        # The early-close day after Thanksgiving: the open is 09:30 ET as on a full day.
        pytest.param(
            date(2026, 11, 27),
            _utc(2026, 11, 27, 15, 30),
            _utc(2026, 11, 27, 16, 30),
            id="early-close",
        ),
    ],
)
def test_a_midsession_book_gets_open_plus_start_to_open_plus_start_plus_length(
    session: date, start: datetime, end: datetime
) -> None:
    s = Settings(_env_file=None, paper={"execution": _MID})  # type: ignore[arg-type]
    opening = session_open(session)
    assert submit_window_for(s, "daily", opening) == (start, end)
    # 10:30 to 11:30 ET, latest batch start 11:05 ET (end - 900 s - 10 min).
    assert latest_batch_start(s, "daily", opening) == end - timedelta(minutes=25)
    assert end < session_close(session) - timedelta(minutes=30)


def test_the_window_reads_the_books_own_keys_and_the_sell_wait() -> None:
    entry = {
        "submit_at": "midsession",
        "midsession_start_minutes_after_open": 90,
        "midsession_window_minutes": 45,
    }
    s = Settings(_env_file=None, paper={"sell_wait_seconds": 600, "execution": {"daily": entry}})  # type: ignore[arg-type]
    opening = session_open(date(2026, 10, 30))
    start, end = submit_window_for(s, "daily", opening)
    assert (start, end) == (opening + timedelta(minutes=90), opening + timedelta(minutes=135))
    assert latest_batch_start(s, "daily", opening) == end - timedelta(minutes=20)
    # A second book with no entry keeps today's window.
    assert submit_window_for(s, "main", opening)[0] == opening - timedelta(minutes=90)


def test_the_latest_start_interval_is_never_empty_at_the_floor() -> None:
    """At the length floor (sell wait / 60 + 25) the latest start is 15 minutes in."""
    entry = {"submit_at": "midsession", "midsession_window_minutes": 40}
    s = Settings(_env_file=None, paper={"execution": {"daily": entry}})  # type: ignore[arg-type]
    opening = session_open(date(2026, 10, 30))
    start, _end = submit_window_for(s, "daily", opening)
    latest = latest_batch_start(s, "daily", opening)
    assert latest is not None and latest - start == timedelta(minutes=15)


def test_a_naive_opening_is_refused() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        submit_window_for(Settings(_env_file=None), "main", datetime(2026, 10, 30, 13, 30))  # noqa: DTZ001

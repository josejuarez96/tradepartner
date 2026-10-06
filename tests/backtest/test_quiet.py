"""Quiet intervals, pure (strategy-lab spec "Quiet interval" and req 2; the "Quiet
intervals" acceptance criterion as far as the scheduling arithmetic reaches; T106).

Every test passes its clock explicitly (`now`), so the "fake clock" of the criterion is
the argument itself. Defaults: `lab.quiet_intervals = [16:00, 21:00]` America/New_York
Monday to Friday; the paper interval is [open - 90 min - 30 min, open + 30 min + 900 s],
07:30 to 10:15 New York time on a regular session.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tradepartner import calendar as tp_calendar
from tradepartner.backtest.quiet import (
    QuietInterval,
    configured_intervals,
    in_quiet_interval,
    next_quiet_interval,
    paper_interval,
    predicted_group_seconds,
    quiet_intervals_around,
    seconds_per_variant,
    start_decision,
    system_timezone_matches,
)
from tradepartner.config import Settings

NY = ZoneInfo("America/New_York")

MONDAY = date(2026, 10, 5)  # an XNYS session
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
THANKSGIVING = date(2026, 11, 26)  # a Thursday, XNYS closed


def _settings(**sections: Any) -> Settings:
    return Settings(_env_file=None, **sections)


def _ny(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(UTC)


def _session_open(day: date) -> datetime | None:
    return tp_calendar.session_open(day) if tp_calendar.is_session(day) else None


def _around(now: datetime, settings: Settings, *, paper: bool) -> list[QuietInterval]:
    return quiet_intervals_around(
        now, settings, paper_window_open=paper, session_open=_session_open
    )


# ── configured intervals ────────────────────────────────────────────────────────


def test_configured_interval_on_a_quiet_weekday_is_local_time_in_utc() -> None:
    (interval,) = configured_intervals(MONDAY, _settings())
    assert interval.start == _ny(MONDAY, 16)
    assert interval.end == _ny(MONDAY, 21)
    assert interval.start.tzinfo is UTC
    assert interval.end.tzinfo is UTC


def test_configured_interval_follows_daylight_saving() -> None:
    (winter,) = configured_intervals(date(2026, 1, 5), _settings())
    (summer,) = configured_intervals(date(2026, 3, 9), _settings())
    assert winter.start == datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
    assert summer.start == datetime(2026, 3, 9, 20, 0, tzinfo=UTC)


def test_a_day_outside_quiet_weekdays_carries_none() -> None:
    assert configured_intervals(SATURDAY, _settings()) == []


def test_a_weekday_holiday_carries_the_configured_intervals() -> None:
    assert not tp_calendar.is_session(THANKSGIVING)
    (interval,) = configured_intervals(THANKSGIVING, _settings())
    assert interval.start == _ny(THANKSGIVING, 16)


def test_weekend_carries_intervals_when_configured() -> None:
    settings = _settings(lab={"quiet_weekdays": [0, 1, 2, 3, 4, 5, 6]})
    assert len(configured_intervals(SATURDAY, settings)) == 1


def test_several_configured_intervals_in_order() -> None:
    settings = _settings(lab={"quiet_intervals": [["16:00", "21:00"], ["02:00", "03:30"]]})
    starts = [i.start for i in configured_intervals(MONDAY, settings)]
    assert starts == [_ny(MONDAY, 2), _ny(MONDAY, 16)]


@pytest.mark.parametrize(
    "pair", [["21:00", "16:00"], ["16:00", "16:00"], ["4pm", "9pm"], ["25:00", "26:00"]]
)
def test_malformed_configured_interval_is_refused(pair: list[str]) -> None:
    with pytest.raises(ValueError, match=r"lab\.quiet_intervals"):
        configured_intervals(MONDAY, _settings(lab={"quiet_intervals": [pair]}))


# ── the paper interval ──────────────────────────────────────────────────────────


def test_paper_interval_on_a_session() -> None:
    interval = paper_interval(MONDAY, _settings(), True, _session_open(MONDAY))
    assert interval is not None
    assert interval.start == _ny(MONDAY, 7, 30)
    assert interval.end == _ny(MONDAY, 10, 15)


def test_paper_interval_moves_with_the_paper_keys() -> None:
    settings = _settings(
        paper={
            "submit_window_before_open_minutes": 60,
            "submit_window_after_open_minutes": 45,
            "sell_wait_seconds": 1800.0,
        },
        lab={"paper_run_lead_minutes": 15},
    )
    interval = paper_interval(MONDAY, settings, True, _session_open(MONDAY))
    assert interval is not None
    assert interval.start == _ny(MONDAY, 8, 15)  # 09:30 - 60 - 15
    assert interval.end == _ny(MONDAY, 10, 45)  # 09:30 + 45 + 30


def test_paper_interval_on_a_weekday_holiday_uses_the_regular_open() -> None:
    interval = paper_interval(THANKSGIVING, _settings(), True, None)
    assert interval is not None
    assert interval.start == _ny(THANKSGIVING, 7, 30)
    assert interval.end == _ny(THANKSGIVING, 10, 15)


def test_no_paper_interval_without_an_open_window_or_off_the_quiet_weekdays() -> None:
    assert paper_interval(MONDAY, _settings(), False, _session_open(MONDAY)) is None
    assert paper_interval(SATURDAY, _settings(), True, None) is None


def test_paper_interval_refuses_a_naive_open_or_one_on_another_day() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        paper_interval(MONDAY, _settings(), True, datetime(2026, 10, 5, 13, 30))  # noqa: DTZ001
    with pytest.raises(ValueError, match="not on"):
        paper_interval(MONDAY, _settings(), True, _session_open(FRIDAY))


# ── the merged timeline, next and in ────────────────────────────────────────────


def test_intervals_around_are_sorted_merged_and_cover_a_week() -> None:
    now = _ny(MONDAY, 12)
    intervals = _around(now, _settings(), paper=True)
    assert intervals == sorted(intervals, key=lambda i: i.start)
    for earlier, later in pairwise(intervals):
        assert earlier.end < later.start
    assert intervals[-1].end - now > timedelta(days=7)


def test_overlapping_intervals_merge() -> None:
    settings = _settings(lab={"quiet_intervals": [["07:00", "08:00"]]})
    intervals = _around(_ny(MONDAY, 0, 30), settings, paper=True)
    monday = [i for i in intervals if i.start.astimezone(NY).date() == MONDAY]
    assert len(monday) == 1
    assert monday[0].start == _ny(MONDAY, 7)
    assert monday[0].end == _ny(MONDAY, 10, 15)


def test_next_quiet_interval_and_in_quiet_interval() -> None:
    settings = _settings()
    kwargs: dict[str, Any] = {"paper_window_open": False, "session_open": _session_open}

    inside = next_quiet_interval(_ny(MONDAY, 17), settings, **kwargs)
    assert inside is not None and inside.start == _ny(MONDAY, 16)
    after = next_quiet_interval(_ny(MONDAY, 22), settings, **kwargs)
    assert after is not None and after.start == _ny(MONDAY + timedelta(days=1), 16)
    weekend = next_quiet_interval(_ny(FRIDAY, 22), settings, **kwargs)
    assert weekend is not None and weekend.start == _ny(FRIDAY + timedelta(days=3), 16)

    assert in_quiet_interval(_ny(MONDAY, 17), settings, **kwargs)
    assert in_quiet_interval(_ny(MONDAY, 16), settings, **kwargs)
    assert not in_quiet_interval(_ny(MONDAY, 21), settings, **kwargs)  # end exclusive
    assert not in_quiet_interval(_ny(MONDAY, 15), settings, **kwargs)
    assert not in_quiet_interval(_ny(SATURDAY, 17), settings, **kwargs)


def test_paper_window_brings_the_morning_interval() -> None:
    nxt = next_quiet_interval(
        _ny(MONDAY, 22), _settings(), paper_window_open=True, session_open=_session_open
    )
    assert nxt is not None and nxt.start == _ny(MONDAY + timedelta(days=1), 7, 30)


def test_no_quiet_days_means_no_interval() -> None:
    settings = _settings(lab={"quiet_weekdays": []})
    now = _ny(MONDAY, 12)
    assert (
        next_quiet_interval(now, settings, paper_window_open=True, session_open=_session_open)
        is None
    )
    assert _around(now, settings, paper=True) == []


def test_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        next_quiet_interval(
            datetime(2026, 10, 5, 12),  # noqa: DTZ001
            _settings(),
            paper_window_open=False,
            session_open=_session_open,
        )


# ── duration prediction ─────────────────────────────────────────────────────────


def test_seconds_per_variant_uses_the_measured_figure_at_the_cadence() -> None:
    settings = _settings()
    measured = {"month_end": 2.0, "week_end": None, "daily": 50.0}
    assert seconds_per_variant(measured, "month_end", settings) == 2.0
    assert seconds_per_variant(measured, "daily", settings) == 50.0
    # No measurement at this cadence: the config default for it, never another cadence's.
    assert seconds_per_variant(measured, "week_end", settings) == 4.0
    assert seconds_per_variant({}, "daily", settings) == 20.0


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_seconds_per_variant_refuses_a_nonsense_measurement(bad: float) -> None:
    with pytest.raises(ValueError, match="seconds per variant"):
        seconds_per_variant({"daily": bad}, "daily", _settings())


def test_predicted_group_seconds() -> None:
    assert predicted_group_seconds(4, 2.5) == 10.0
    assert predicted_group_seconds(0, 2.5) == 0.0
    with pytest.raises(ValueError):
        predicted_group_seconds(-1, 2.5)
    with pytest.raises(ValueError):
        predicted_group_seconds(1, -2.5)


# ── the start decision ──────────────────────────────────────────────────────────


def test_group_that_ends_before_the_next_interval_starts() -> None:
    now = _ny(MONDAY, 14)
    intervals = _around(now, _settings(), paper=False)
    assert start_decision(now, 3600.0, intervals) == "start"
    assert start_decision(now, 7200.0, intervals) == "start"  # ends exactly at 16:00


def test_group_that_would_end_inside_the_next_interval_waits() -> None:
    now = _ny(MONDAY, 14)
    intervals = _around(now, _settings(), paper=False)
    assert start_decision(now, 3 * 3600.0, intervals) == "wait"


def test_group_longer_than_the_longest_gap_starts_at_once() -> None:
    now = _ny(MONDAY, 14)
    intervals = _around(now, _settings(), paper=False)
    # The longest gap is the weekend: Friday 21:00 to Monday 16:00, 67 hours.
    assert start_decision(now, 66 * 3600.0, intervals) == "wait"
    assert start_decision(now, 68 * 3600.0, intervals) == "start"


def test_paper_interval_shortens_the_longest_gap() -> None:
    now = _ny(MONDAY, 14)
    intervals = _around(now, _settings(), paper=True)
    # Friday 21:00 to Monday 07:30 is 58.5 hours.
    assert start_decision(now, 59 * 3600.0, intervals) == "start"
    assert start_decision(now, 58 * 3600.0, intervals) == "wait"


def test_daily_group_after_month_end_group_predicts_from_the_daily_figure() -> None:
    settings = _settings()
    now = _ny(MONDAY, 14)
    intervals = _around(now, settings, paper=False)
    measured = {"month_end": 60.0, "week_end": None, "daily": 2400.0}
    month_end = predicted_group_seconds(4, seconds_per_variant(measured, "month_end", settings))
    daily = predicted_group_seconds(4, seconds_per_variant(measured, "daily", settings))
    assert start_decision(now, month_end, intervals) == "start"  # 4 min
    assert start_decision(now, daily, intervals) == "wait"  # 2 h 40 min ends 16:40


def test_nothing_starts_inside_an_interval() -> None:
    now = _ny(MONDAY, 17)
    intervals = _around(now, _settings(), paper=False)
    assert start_decision(now, 1.0, intervals) == "wait"
    assert start_decision(now, 1e9, intervals) == "wait"


def test_no_intervals_always_starts() -> None:
    assert start_decision(_ny(MONDAY, 17), 1e9, []) == "start"


def test_start_decision_refuses_a_naive_clock_or_negative_prediction() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        start_decision(datetime(2026, 10, 5, 12), 1.0, [])  # noqa: DTZ001
    with pytest.raises(ValueError):
        start_decision(_ny(MONDAY, 12), -1.0, [])


# ── the system time zone ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("zone", "matches"),
    [
        ("America/New_York", True),
        ("US/Eastern", True),  # an alias with the same rules
        ("UTC", False),
        ("America/Chicago", False),
        ("America/Toronto", True),  # same offsets over the sampled years
    ],
)
def test_system_timezone_matches(zone: str, matches: bool) -> None:
    assert (
        system_timezone_matches(_settings(), system_tz=ZoneInfo(zone), around=_ny(MONDAY, 12))
        is matches
    )


def test_system_timezone_mismatch_with_a_configured_zone() -> None:
    settings = _settings(lab={"quiet_timezone": "Europe/London"})
    assert system_timezone_matches(
        settings, system_tz=ZoneInfo("Europe/London"), around=_ny(MONDAY, 12)
    )
    assert not system_timezone_matches(
        settings, system_tz=ZoneInfo("America/New_York"), around=_ny(MONDAY, 12)
    )


@pytest.mark.parametrize(("tz", "matches"), [("America/New_York", True), ("UTC", False)])
def test_system_timezone_read_from_the_process(
    monkeypatch: pytest.MonkeyPatch, tz: str, matches: bool
) -> None:
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        assert system_timezone_matches(_settings(), around=_ny(MONDAY, 12)) is matches
    finally:
        monkeypatch.undo()
        time.tzset()


# ── review fixes: sparse weekdays, DST edges, overflow, eager validation ────────


@pytest.mark.parametrize("paper", [False, True])
@pytest.mark.parametrize("weekday_offset", range(7))
def test_longest_gap_is_the_weekly_one_with_a_single_quiet_weekday(
    weekday_offset: int, paper: bool
) -> None:
    settings = _settings(lab={"quiet_weekdays": [0]})
    now = _ny(MONDAY + timedelta(days=weekday_offset), 12)
    intervals = _around(now, settings, paper=paper)
    gaps = [b.start - a.end for a, b in pairwise(intervals)]
    # Monday 21:00 to the next Monday's first interval (16:00, or 07:30 with paper).
    weekly = timedelta(days=6, hours=19) if not paper else timedelta(days=6, hours=10, minutes=30)
    assert max(gaps) == weekly


def test_a_sparse_week_group_that_would_end_inside_monday_waits() -> None:
    settings = _settings(lab={"quiet_weekdays": [0]})
    now = _ny(FRIDAY, 12)
    intervals = _around(now, settings, paper=True)
    assert start_decision(now, 4 * 86400.0, intervals) == "wait"
    assert start_decision(now, 7 * 86400.0, intervals) == "start"


def test_dst_skipped_and_repeated_local_times_take_the_widest_reading() -> None:
    every_day = {"quiet_weekdays": [0, 1, 2, 3, 4, 5, 6]}
    spring = date(2027, 3, 14)  # a Sunday; 02:00-03:00 does not exist in New York
    (skipped,) = configured_intervals(
        spring, _settings(lab={**every_day, "quiet_intervals": [["02:30", "03:00"]]})
    )
    assert skipped.start < skipped.end
    fall = date(2026, 11, 1)  # a Sunday; 01:00-02:00 happens twice
    (repeated,) = configured_intervals(
        fall, _settings(lab={**every_day, "quiet_intervals": [["01:15", "01:45"]]})
    )
    assert repeated.start == datetime(2026, 11, 1, 5, 15, tzinfo=UTC)  # first 01:15 (EDT)
    assert repeated.end == datetime(2026, 11, 1, 6, 45, tzinfo=UTC)  # second 01:45 (EST)


def test_malformed_interval_is_refused_on_a_non_quiet_day_too() -> None:
    with pytest.raises(ValueError, match=r"lab\.quiet_intervals"):
        configured_intervals(SATURDAY, _settings(lab={"quiet_intervals": [["4pm", "9pm"]]}))


def test_a_huge_prediction_starts_without_overflow() -> None:
    now = _ny(MONDAY, 14)
    assert start_decision(now, 1e15, _around(now, _settings(), paper=False)) == "start"


def test_paper_interval_gate_reads_the_open_in_the_quiet_zone() -> None:
    # In Auckland, New York's Monday 07:30 fire time is already Tuesday 00:30.
    settings = _settings(lab={"quiet_timezone": "Pacific/Auckland", "quiet_weekdays": [1]})
    interval = paper_interval(MONDAY, settings, True, _session_open(MONDAY))
    assert interval is not None and interval.start == _ny(MONDAY, 7, 30)
    assert paper_interval(FRIDAY, settings, True, _session_open(FRIDAY)) is None


def test_system_timezone_sample_follows_the_clock() -> None:
    # Sao Paulo kept DST until 2019 and Bahia did not: the zones differ around 2017
    # and agree around 2026, so the sample must track the clock, not fixed years.
    settings = _settings(lab={"quiet_timezone": "America/Sao_Paulo"})
    bahia = ZoneInfo("America/Bahia")
    assert system_timezone_matches(settings, system_tz=bahia, around=_ny(MONDAY, 12))
    assert not system_timezone_matches(
        settings, system_tz=bahia, around=datetime(2017, 6, 1, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="tz-aware"):
        system_timezone_matches(settings, system_tz=bahia, around=datetime(2026, 1, 1))  # noqa: DTZ001


def test_paper_interval_gate_reads_the_fire_time_not_the_open() -> None:
    # Noumea (UTC+11, no DST): NY Friday's 07:30 EDT fire time is Friday 22:30 local,
    # while its 09:30 open is already Saturday 00:30. The gate follows the fire time.
    def gated(weekdays: list[int]) -> QuietInterval | None:
        settings = _settings(lab={"quiet_timezone": "Pacific/Noumea", "quiet_weekdays": weekdays})
        return paper_interval(FRIDAY, settings, True, _session_open(FRIDAY))

    assert gated([4]) is not None
    assert gated([5]) is None

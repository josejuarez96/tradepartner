"""Quiet intervals: when the sweep runner may start a read group (strategy-lab spec
"Quiet interval" definition and req 2; plan task T106).

A quiet interval is a span inside which the sweep runner opens no trial, holds no
connection and writes nothing, so a sweep never contends for the store with `ingest` or
`paper run`. Two sources, both applied on each day in `lab.quiet_weekdays` (the days
launchd fires, XNYS session or not):

- the **configured** intervals, `lab.quiet_intervals` as local `HH:MM` pairs in
  `lab.quiet_timezone`;
- the **paper** interval while a paper window is open, [open -
  `paper.submit_window_before_open_minutes` - `lab.paper_run_lead_minutes`, open +
  `paper.submit_window_after_open_minutes` + `paper.sell_wait_seconds`], with "open" the
  session open on a session and XNYS's regular open time on a holiday.

A group starts when its predicted duration (variants x seconds per variant at the
group's cadence) ends before the next interval, or when that duration exceeds the
longest gap between intervals (it would never fit, so it starts at once and pauses
through each interval). Everything here is pure over explicit inputs: the clock is the
`now` argument, the session opens come from a caller-supplied function, and the
measured seconds per variant are a mapping the caller reads from the registry (T107).
All instants are tz-aware UTC.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from itertools import pairwise
from typing import Final, Literal
from zoneinfo import ZoneInfo

from exchange_calendars.exchange_calendar_xnys import XNYSExchangeCalendar

from tradepartner.config import Cadence, Settings

__all__ = [
    "QuietInterval",
    "SessionOpen",
    "StartDecision",
    "configured_intervals",
    "in_quiet_interval",
    "next_quiet_interval",
    "paper_interval",
    "predicted_group_seconds",
    "quiet_intervals_around",
    "seconds_per_variant",
    "start_decision",
    "system_timezone_matches",
]

StartDecision = Literal["start", "wait"]

# The session open for a calendar day, tz-aware UTC, or None when the day is not an
# XNYS session (the caller passes `tradepartner.calendar`'s reads, a test a fake).
SessionOpen = Callable[[date], datetime | None]

# XNYS's regular open, from the trading calendar rather than a literal: the paper
# interval on a weekday holiday (no session, so no session open) sits at this time.
_EXCHANGE_TZ: Final = ZoneInfo(str(XNYSExchangeCalendar.tz))
_REGULAR_OPEN: Final = XNYSExchangeCalendar.open_times[-1][1]

# How far the timeline reaches: from the day before `now` (an interval that began
# yesterday may still hold) to this many days after, so it holds every quiet weekday
# at least twice and the longest gap, even with a single quiet weekday, is the true
# weekly one.
_HORIZON_DAYS: Final = 14

_HHMM = re.compile(r"\A([01]\d|2[0-3]):([0-5]\d)\Z")

# Instants `system_timezone_matches` compares UTC offsets at: every six hours from a
# year before the check to two years after it, so both DST transitions of each year,
# and the rules in force now and next, are covered.
_TZ_SAMPLE_BEFORE: Final = timedelta(days=366)
_TZ_SAMPLE_STEP: Final = timedelta(hours=6)
_TZ_SAMPLE_COUNT: Final = 3 * 366 * 4


@dataclass(frozen=True)
class QuietInterval:
    """A half-open span [start, end) of tz-aware UTC instants the runner keeps out of.

    `sources` names where it came from (`configured`, `paper`); a merged interval
    carries every source it absorbed.
    """

    start: datetime
    end: datetime
    sources: tuple[str, ...]

    def contains(self, now: datetime) -> bool:
        """True when `now` lies inside the interval (start inclusive, end exclusive)."""
        return self.start <= now < self.end


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be tz-aware, got naive {value!r}")


def _parse_hhmm(text: str) -> tuple[int, int]:
    match = _HHMM.match(text)
    if match is None:
        raise ValueError(f"lab.quiet_intervals time {text!r} is not HH:MM (00:00 to 23:59)")
    return int(match.group(1)), int(match.group(2))


def _local_instant(day: date, hour: int, minute: int, zone: tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(UTC)


def _local_bounds(day: date, hour: int, minute: int, zone: tzinfo) -> tuple[datetime, datetime]:
    """The earliest and latest UTC instants a local wall time names on `day`: one
    instant normally, two an hour apart when DST makes the time ambiguous (fall back)
    or skipped (spring forward). Interval bounds take the widest reading."""
    readings = [
        datetime(day.year, day.month, day.day, hour, minute, fold=fold, tzinfo=zone).astimezone(UTC)
        for fold in (0, 1)
    ]
    return min(readings), max(readings)


def _configured_pairs(settings: Settings) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """`lab.quiet_intervals` parsed and checked as same-day local wall-clock pairs."""
    pairs = []
    for start_text, end_text in settings.lab.quiet_intervals:
        start, end = _parse_hhmm(start_text), _parse_hhmm(end_text)
        if end <= start:
            raise ValueError(
                f"lab.quiet_intervals pair [{start_text!r}, {end_text!r}] must end after it "
                "starts on the same day"
            )
        pairs.append((start, end))
    return pairs


def configured_intervals(day: date, settings: Settings) -> list[QuietInterval]:
    """The `lab.quiet_intervals` on local calendar day `day`, in start order.

    `day` is a date in `lab.quiet_timezone`; a day whose weekday is not in
    `lab.quiet_weekdays` carries none, and a weekday holiday carries them all (launchd
    fires whether or not XNYS is open). Raises `ValueError` on a time that is not
    `HH:MM` or a pair whose end is not after its start.
    """
    lab = settings.lab
    pairs = _configured_pairs(settings)  # checked on every day, quiet or not
    if day.weekday() not in lab.quiet_weekdays:
        return []
    zone = ZoneInfo(lab.quiet_timezone)
    intervals = []
    for (start_h, start_m), (end_h, end_m) in pairs:
        start = _local_bounds(day, start_h, start_m, zone)[0]
        end = _local_bounds(day, end_h, end_m, zone)[1]
        intervals.append(QuietInterval(start, end, ("configured",)))
    return sorted(intervals, key=lambda i: i.start)


def paper_interval(
    day: date,
    settings: Settings,
    paper_window_open: bool,
    session_open: datetime | None,
) -> QuietInterval | None:
    """The paper interval on exchange day `day`, or None when no window is open or the
    plist's fire time does not fall on a quiet weekday.

    `day` is an XNYS (New York) calendar date; the quiet-weekday gate reads the weekday
    of the interval's start (when the paper plist fires) in `lab.quiet_timezone`, the
    zone launchd fires in, which is `day` itself whenever that zone is the exchange's.
    `session_open` is the day's XNYS open (tz-aware) or None on a non-session day, when
    the regular open time stands in (the paper plist fires on weekday holidays too and
    writes a `no_session` row). The bounds move with the `paper.*` timing keys and
    `lab.paper_run_lead_minutes`, so no edit here follows a T70 timing change.
    """
    if not paper_window_open:
        return None
    if session_open is None:
        open_at = _local_instant(day, _REGULAR_OPEN.hour, _REGULAR_OPEN.minute, _EXCHANGE_TZ)
    else:
        _require_aware(session_open, "session_open")
        if session_open.astimezone(_EXCHANGE_TZ).date() != day:
            raise ValueError(f"session_open {session_open.isoformat()} is not on {day}")
        open_at = session_open.astimezone(UTC)
    paper = settings.paper
    before = timedelta(
        minutes=paper.submit_window_before_open_minutes + settings.lab.paper_run_lead_minutes
    )
    after = timedelta(
        minutes=paper.submit_window_after_open_minutes, seconds=paper.sell_wait_seconds
    )
    start = open_at - before
    quiet_zone = ZoneInfo(settings.lab.quiet_timezone)
    if start.astimezone(quiet_zone).weekday() not in settings.lab.quiet_weekdays:
        return None
    return QuietInterval(start, open_at + after, ("paper",))


def _merge(intervals: Sequence[QuietInterval]) -> list[QuietInterval]:
    merged: list[QuietInterval] = []
    for interval in sorted(intervals, key=lambda i: (i.start, i.end)):
        if merged and interval.start <= merged[-1].end:
            last = merged[-1]
            sources = last.sources + tuple(s for s in interval.sources if s not in last.sources)
            merged[-1] = QuietInterval(last.start, max(last.end, interval.end), sources)
        else:
            merged.append(interval)
    return merged


def quiet_intervals_around(
    now: datetime,
    settings: Settings,
    *,
    paper_window_open: bool,
    session_open: SessionOpen,
) -> list[QuietInterval]:
    """Every quiet interval from the day before `now` to `_HORIZON_DAYS` after it.

    Configured intervals per `lab.quiet_timezone` day and paper intervals per XNYS day,
    sorted, with overlapping or touching ones merged, so consecutive entries are
    separated by a real gap. The span holds every weekday at least twice, which
    `start_decision` needs to see the longest gap.
    """
    _require_aware(now, "now")
    local_today = now.astimezone(ZoneInfo(settings.lab.quiet_timezone)).date()
    exchange_today = now.astimezone(_EXCHANGE_TZ).date()
    intervals: list[QuietInterval] = []
    for offset in range(-1, _HORIZON_DAYS + 1):
        intervals.extend(configured_intervals(local_today + timedelta(days=offset), settings))
        if paper_window_open:
            day = exchange_today + timedelta(days=offset)
            paper = paper_interval(day, settings, paper_window_open, session_open(day))
            if paper is not None:
                intervals.append(paper)
    return _merge(intervals)


def next_quiet_interval(
    now: datetime,
    settings: Settings,
    *,
    paper_window_open: bool,
    session_open: SessionOpen,
) -> QuietInterval | None:
    """The interval holding `now`, else the first one starting after it; None when no
    interval falls within the horizon (no quiet weekdays configured)."""
    intervals = quiet_intervals_around(
        now, settings, paper_window_open=paper_window_open, session_open=session_open
    )
    return next((i for i in intervals if now < i.end), None)


def in_quiet_interval(
    now: datetime,
    settings: Settings,
    *,
    paper_window_open: bool,
    session_open: SessionOpen,
) -> bool:
    """True when `now` lies inside a quiet interval."""
    nxt = next_quiet_interval(
        now, settings, paper_window_open=paper_window_open, session_open=session_open
    )
    return nxt is not None and nxt.contains(now)


def predicted_group_seconds(n_variants: int, seconds_per_variant: float) -> float:
    """A read group's predicted duration: its variant count times the seconds per
    variant at the group's cadence."""
    if n_variants < 0:
        raise ValueError(f"n_variants must be >= 0, got {n_variants}")
    if not math.isfinite(seconds_per_variant) or seconds_per_variant < 0:
        raise ValueError(f"seconds per variant must be finite and >= 0, got {seconds_per_variant}")
    return n_variants * seconds_per_variant


def seconds_per_variant(
    measured: Mapping[Cadence, float | None], cadence: Cadence, settings: Settings
) -> float:
    """Seconds one variant takes at `cadence`: the measured figure (from `sweep_trials`
    joined to the variants' cadence) when one exists, else `lab.seconds_per_variant_default`
    for that cadence. A measurement at another cadence is never used. Raises
    `ValueError` on a measured figure that is not finite and positive."""
    value = measured.get(cadence)
    if value is None:
        return settings.lab.seconds_per_variant_default[cadence]
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"measured seconds per variant at {cadence} must be > 0, got {value}")
    return value


def _longest_gap(intervals: Sequence[QuietInterval]) -> timedelta | None:
    ordered = sorted(intervals, key=lambda i: i.start)
    gaps = [later.start - earlier.end for earlier, later in pairwise(ordered)]
    return max(gaps) if gaps else None


def start_decision(
    now: datetime, predicted_seconds: float, intervals: Sequence[QuietInterval]
) -> StartDecision:
    """Whether the runner starts a read group at `now`.

    `wait` inside an interval (no trial opens there); otherwise `start` when the group
    ends at or before the next interval begins, or when its duration exceeds the
    longest gap between consecutive intervals (it can never fit, so it starts at once
    and pauses through each interval), and `wait` otherwise. With no interval ahead the
    group starts. `intervals` is the timeline from `quiet_intervals_around`, which holds
    every quiet weekday at least twice so the longest gap is the true weekly one; a
    shorter caller-built timeline can understate it.
    """
    _require_aware(now, "now")
    if not math.isfinite(predicted_seconds) or predicted_seconds < 0:
        raise ValueError(f"predicted_seconds must be finite and >= 0, got {predicted_seconds}")
    merged = _merge(intervals)
    if any(i.contains(now) for i in merged):
        return "wait"
    upcoming = [i for i in merged if i.start > now]
    if not upcoming:
        return "start"
    # Compared in float seconds, so a huge prediction cannot overflow a datetime.
    if predicted_seconds <= (upcoming[0].start - now).total_seconds():
        return "start"
    longest = _longest_gap(merged)
    if longest is not None and predicted_seconds > longest.total_seconds():
        return "start"
    return "wait"


def _system_offset(instant: datetime) -> timedelta:
    return timedelta(seconds=time.localtime(instant.timestamp()).tm_gmtoff)


def system_timezone_matches(
    settings: Settings, *, around: datetime, system_tz: tzinfo | None = None
) -> bool:
    """True when `lab.quiet_timezone` keeps the same UTC offsets as the system zone.

    launchd fires the plists in the machine's zone, so the intervals mean what they say
    only when the two agree; `lab status` warns when they do not. Zones are compared by
    their offsets at instants every six hours from a year before `around` to two years
    after it (both DST transitions each year, the rules in force now and next), so an
    alias such as `US/Eastern` matches `America/New_York`. `system_tz` stands in for
    the machine's zone (a test passes one); when omitted the process's local time rules
    are read. `around` is the clock, passed in like `now` elsewhere here.
    """
    zone = ZoneInfo(settings.lab.quiet_timezone)
    _require_aware(around, "around")
    first = around.astimezone(UTC) - _TZ_SAMPLE_BEFORE
    for step in range(_TZ_SAMPLE_COUNT):
        instant = first + step * _TZ_SAMPLE_STEP
        configured = instant.astimezone(zone).utcoffset()
        system = (
            instant.astimezone(system_tz).utcoffset()
            if system_tz is not None
            else _system_offset(instant)
        )
        if configured != system:
            return False
    return True

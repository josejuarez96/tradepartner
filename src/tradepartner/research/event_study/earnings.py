"""E1 event-builder core: 8-K Item 2.02 releases at their causal session (#3503).

The point-in-time rules of the earnings design
(docs/research/2026-11-02-earnings-event-family-design.md) section 2 (rules 1, 2, 6, 7, 8, 9),
pinned by the E1 design file (docs/research/2026-11-16-e1-earnings-reaction-design.md).
This reads filings only: no price, no return. The reaction `R`, its causal
breakpoints (rules 3 and 4) and the one-class-per-CIK choice are the frame
step's, run only after the frozen kill line.
`power_counts` is stage 4a item (c) on the events this module emits.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from tradepartner.calendar import all_sessions, is_session, next_session, session_close
from tradepartner.store.asof import filing_events_as_of
from tradepartner.store.filing_index import first_known

_NEW_YORK = ZoneInfo("America/New_York")
#: EDGAR's dissemination cut: a filing accepted after 17:30 New York is
#: disseminated the next EDGAR business day (a rule of the source).
_EDGAR_CUT = time(17, 30)


def day_zero(accepted_at: datetime) -> date:
    """Rule 2: the first XNYS session whose close follows the acceptance."""
    if accepted_at.tzinfo is None:
        raise ValueError("accepted_at must be tz-aware")
    local = accepted_at.astimezone(_NEW_YORK).date()
    if is_session(local) and accepted_at < session_close(local):
        return local
    return next_session(local)


@lru_cache(maxsize=64)
def _federal_holidays(year: int) -> frozenset[date]:
    days = USFederalHolidayCalendar().holidays(pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31))
    return frozenset(day.date() for day in days)


def edgar_business_day(day: date) -> bool:
    """A weekday that is not a US federal holiday (EDGAR is closed on those)."""
    return day.weekday() < 5 and day not in _federal_holidays(day.year)


def disseminated_on(accepted_at: datetime) -> date:
    """The New York date EDGAR makes the filing public (rule 9)."""
    local = accepted_at.astimezone(_NEW_YORK)
    day = local.date()
    if edgar_business_day(day) and local.time() <= _EDGAR_CUT:
        return day
    day += timedelta(days=1)
    while not edgar_business_day(day):
        day += timedelta(days=1)
    return day


def session_events(
    conn: duckdb.DuckDBPyConnection, session: date, members: Collection[str]
) -> dict[str, Any]:
    """The 2.02 8-Ks whose day 0 is `session`, read at `session_close(session)`.

    `members` is `universe_as_of(close)`'s membership (rule 5). Rows are one per
    `(cik, accession)`, the CIK's member classes in `security_ids` (rule 7's class
    choice is the frame's). Dropped and counted: a stamp that disagrees with
    `filing_index`'s first-known acceptance (rule 8), a filing EDGAR made public
    after `session` (rule 9). 8-K/A rows are counted, never events (rule 6).
    """
    t = session_close(session)
    rows = filing_events_as_of(conn, t, forms=["8-K"], items=["2.02"])
    rows = rows.filter(rows["security_id"].is_in(list(members)))
    releases: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows.iter_rows(named=True):
        if day_zero(row["accepted_at"]) != session:
            continue
        key = (row["cik"], row["accession"])
        release = releases.setdefault(key, {**row, "s0": session, "security_ids": []})
        release["security_ids"].append(row["security_id"])
    index = {
        (f.cik, f.accession): f.accepted_at
        for f in first_known(conn, t, ciks={cik for cik, _ in releases}, form="8-K")
    }
    events, mismatch, late = [], 0, 0
    for key, release in sorted(releases.items(), key=lambda kv: (kv[1]["accepted_at"], kv[0])):
        if index.get(key) != release["accepted_at"]:
            mismatch += 1
        elif disseminated_on(release["accepted_at"]) > session:
            late += 1
        else:
            events.append(release)
    amendments = filing_events_as_of(conn, t, forms=["8-K/A"], items=["2.02"])
    n_amend = len(
        {
            a["accession"]
            for a in amendments.iter_rows(named=True)
            if day_zero(a["accepted_at"]) == session
        }
    )
    return {
        "events": events,
        "n_stamp_mismatch": mismatch,
        "n_edgar_holiday": late,
        "n_8ka": n_amend,
    }


def dedupe_releases(
    events: Iterable[Mapping[str, Any]], *, gap_sessions: int
) -> tuple[list[Mapping[str, Any]], int]:
    """Rule 7: a CIK's release within `gap_sessions` sessions of its last kept one
    is not an event. `events` must be in `(s0, accepted_at)` order."""
    sessions = all_sessions()
    kept: list[Mapping[str, Any]] = []
    last: dict[str, int] = {}
    dropped = 0
    for event in events:
        position = bisect_left(sessions, event["s0"])
        previous = last.get(event["cik"])
        if previous is not None and position - previous <= gap_sessions:
            dropped += 1
            continue
        last[event["cik"]] = position
        kept.append(event)
    return kept, dropped


def kish(counts: Iterable[int]) -> float:
    """Kish effective cluster count, `(sum n)^2 / sum n^2` (0 for no events)."""
    values = list(counts)
    squares = sum(n * n for n in values)
    return sum(values) ** 2 / squares if squares else 0.0


def power_counts(
    entries: Sequence[date],
    entry_sessions: Sequence[date],
    *,
    tail_share: float,
    overlap_factor: float,
    needed_effective: float,
) -> dict[str, Any]:
    """Stage 4a item (c) from entry dates only (no return): per thesis
    `n_eff = tail_share * N / (overlap_factor * B)`, `B` the season bunching
    (window sessions over the summed per-quarter Kish counts of entry dates)."""
    by_quarter: dict[tuple[int, int], Counter[date]] = {}
    for day in entries:
        by_quarter.setdefault((day.year, (day.month - 1) // 3), Counter())[day] += 1
    kish_sum = sum(kish(c.values()) for c in by_quarter.values())
    bunching = len(entry_sessions) / kish_sum if kish_sum else float("inf")
    n_thesis = tail_share * len(entries)
    n_eff = n_thesis / (overlap_factor * bunching)
    return {
        "n_events": len(entries),
        "n_thesis": n_thesis,
        "kish_dates": kish_sum,
        "bunching": bunching,
        "n_eff": n_eff,
        "months_with_events": len({(d.year, d.month) for d in entries}),
        "pass": n_eff >= needed_effective,
    }

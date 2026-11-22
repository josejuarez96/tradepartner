"""EDGAR's filing-date rule and the clock detector built on it (#1358).

A rule of the source, kept in a leaf module so the EDGAR adapter and the
health checks share one definition without the adapter importing the
orchestrator-level `health` (#3644). It imports only the trading calendar
and the UTC helper.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from tradepartner.calendar import is_session
from tradepartner.timeutil import ensure_tz_aware_utc

#: EDGAR's filing-date rule: a submission accepted on a business day after
#: 17:30 New York is dated the next business day, one before it that day
#: (#1358, "The clock"; T164's count found the cutoff holds for 8-Ks). A
#: rule of the source, not a tunable threshold.
EDGAR_FILING_DATE_CUTOFF = time(17, 30)
_NEW_YORK = ZoneInfo("America/New_York")


def clock_suspect(accepted_at: datetime, filed: date) -> bool:
    """The clock detector (#1358, "The clock"): whether a stamp fits only a
    moved clock, given the submissions payload's filing date. Early: accepted
    on a session before `EDGAR_FILING_DATE_CUTOFF` New York and dated a later
    day. Late: accepted on a New York date after its filing date. A health
    finding, never a repair; it catches only the moves that cross the
    filing-date rule."""
    local = ensure_tz_aware_utc(accepted_at, field_name="accepted_at").astimezone(_NEW_YORK)
    day = local.date()
    if day > filed:
        return True
    return is_session(day) and local.time() < EDGAR_FILING_DATE_CUTOFF and filed > day

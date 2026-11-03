"""Audit stored `known_at` stamps against the SGML acceptance (#2936).

#1382 and #1494 moved EDGAR acceptance stamps by New York's UTC offset (an
Eastern wall clock stored as UTC). A stamp that is **early** reads a filing
as known before it was accepted: a look-ahead, and a one-session one when the
filing came in after the 16:00 ET close of a day whose close it is then
visible at. `audit_stamps` is the pure judge: stored stamps in, the counts out.
The SGML reads and the store reads stay in `scripts/repair_stamps.py`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from tradepartner.adapters.stamp_repair import shift_stamp

_NEW_YORK = ZoneInfo("America/New_York")
_CLOSE = time(16, 0)


@dataclass(frozen=True)
class StoredStamp:
    """One stored row's stamp: the table, the CIK, the accession, the `known_at`."""

    table: str
    cik: str
    accession: str
    known_at: datetime


@dataclass
class TableAudit:
    """The counts for one table's audited stamps."""

    checked: int = 0
    exact: int = 0
    early: int = 0
    late: int = 0
    early_by_ny_offset: int = 0
    session_early: int = 0

    @property
    def look_ahead(self) -> bool:
        """True when any audited stamp is earlier than its acceptance."""
        return self.early > 0


def visible_a_session_early(stored: datetime, accepted: datetime) -> bool:
    """True when ``stored`` makes a filing visible at a close it missed.

    That is: the stamp is on or before the 16:00 New York close of its own
    New York date, while the true acceptance is after that close.
    """
    stored_ny = stored.astimezone(_NEW_YORK)
    close = datetime.combine(stored_ny.date(), _CLOSE, tzinfo=_NEW_YORK)
    return stored_ny <= close < accepted.astimezone(_NEW_YORK)


def audit_stamps(
    stored: Iterable[StoredStamp], accepted: Mapping[str, datetime]
) -> dict[str, TableAudit]:
    """Judge each stored stamp against its SGML acceptance, per table.

    A stamp whose accession has no entry in ``accepted`` is skipped (not
    counted as checked). ``early_by_ny_offset`` counts the early stamps that
    the repair's own `shift_stamp` maps exactly onto the acceptance, i.e. the
    #1382 class.
    """
    out: dict[str, TableAudit] = defaultdict(TableAudit)
    for s in stored:
        truth = accepted.get(s.accession)
        if truth is None:
            continue
        known = s.known_at.astimezone(UTC)
        truth = truth.astimezone(UTC)
        row = out[s.table]
        row.checked += 1
        if known == truth:
            row.exact += 1
        elif known > truth:
            row.late += 1
        else:
            row.early += 1
            row.early_by_ny_offset += shift_stamp(known) == truth
            row.session_early += visible_a_session_early(known, truth)
    return dict(out)

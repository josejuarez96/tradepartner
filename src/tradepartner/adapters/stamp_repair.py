"""The stamps repair rules for #1382, a pure module (plan task T166).

The 2026-10-01 bulk run wrote some CIKs' acceptance stamps as New York wall
clock labelled UTC, so those stamps are early by New York's UTC offset (4 h in
EDT, 5 h in EST). This module holds the rules the repair applies, over explicit
inputs and with no I/O:

- `ny_utc_offset`: New York's UTC offset on a date.
- `shift_stamp`: the shift, a stamp plus the offset on its date.
- `is_provably_early`: the filing-date rule.
- `classify_cik`: a CIK's class (`early`, `true` or `mixed`) from SGML probes.
- `audit_sample`: the seeded audit sample of shifted, store-backing stamps.
- `audit_decision`: the audit stop rule.
- `ManifestRow`: one row of the repair manifest, the contract between the
  cache driver (T166a, `scripts/repair_stamps.py`) and the store restamp
  (T166b, `ingest --restamp-from`).

The cache driver and the store restamp import these names; neither re-derives
a rule.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")

FILING_DAY_CUTOFF = time(17, 30)
"""EDGAR's filing-date cutoff, New York time (Regulation S-T Rule 13(a)(4)).

A filing accepted before it on a business day takes that day as its filing
date; Section 16 forms have a later cutoff (22:00), so a stamp before 17:30
dates to its own day for every form.
"""

AUDIT_FRACTION = 0.01
"""Default audit sample fraction and stop-rule threshold (T166 plan line)."""

CikClass = Literal["early", "true", "mixed"]
"""A CIK's class from its SGML probes."""

RepairMethod = Literal["shift", "sgml", "kept", "unrepaired"]
"""How a manifest row's new stamp was found."""

REPAIR_METHODS: tuple[RepairMethod, ...] = get_args(RepairMethod)

MANIFEST_COLUMNS: tuple[str, ...] = (
    "cik",
    "accession",
    "old_stamp",
    "new_stamp",
    "method",
    "evidence",
)
"""The manifest's column order, for the CSV the cache driver writes."""


def ny_utc_offset(day: date) -> timedelta:
    """Return New York's UTC offset on ``day`` as a positive timedelta.

    The offset is taken at noon New York time, so a DST change day (which
    switches at 02:00) gets the offset in force for the filing day: 4 h on
    EDT dates and 5 h on EST dates. Positive means UTC = New York + offset.
    """
    noon = datetime.combine(day, time(12), tzinfo=_NEW_YORK)
    offset = noon.utcoffset()
    if offset is None:  # pragma: no cover - ZoneInfo always answers
        raise ValueError(f"no UTC offset for {day}")
    return -offset


def shift_stamp(stamp: datetime) -> datetime:
    """Return the repaired stamp for an early stamp: ``stamp`` plus the offset.

    An early stamp is New York wall clock labelled UTC, so the repair reads
    its wall clock as New York time and converts it to UTC. That is the stamp
    plus New York's offset at that wall-clock time, which equals
    ``ny_utc_offset`` on the stamp's date except in the 00:00-02:00 hours of a
    DST-change Sunday (EDGAR is closed then); a wall time in the skipped
    spring-forward hour takes the pre-change (EST) offset.
    """
    utc = ensure_tz_aware_utc(stamp, field_name="stamp")
    wall = utc.replace(tzinfo=_NEW_YORK, fold=0)
    return wall.astimezone(UTC)


def is_provably_early(
    stamp: datetime,
    index_date: date,
    *,
    holidays: Iterable[date] = (),
    cutoff: time = FILING_DAY_CUTOFF,
) -> bool:
    """Return True when the filing-date rule proves ``stamp`` early.

    A stamp before ``cutoff`` New York time on a business day (a weekday not
    in ``holidays``) dates the filing to that same day. ``holidays`` are the
    days EDGAR is closed (federal holidays and announced closures), never the
    NYSE calendar: EDGAR is open on Good Friday, so passing exchange holidays
    would hide early stamps from that day. If the index (filing)
    date is a later day, the true acceptance came later, so the stamp is early.
    A stamp on or after the cutoff, or on a non-business day, proves nothing.
    """
    local = ensure_tz_aware_utc(stamp, field_name="stamp").astimezone(_NEW_YORK)
    local_day = local.date()
    if local_day.weekday() >= 5 or local_day in set(holidays):
        return False
    return local.time() < cutoff and index_date > local_day


def classify_cik(probes: Sequence[tuple[datetime, datetime]]) -> CikClass:
    """Return a CIK's class from its SGML probes.

    Each probe is ``(stamp, sgml)``: the cached stamp and the acceptance time
    read from that filing's SGML header. The class is ``early`` when every
    probe's SGML equals ``shift_stamp(stamp)``, ``true`` when every probe's
    SGML equals the stamp, otherwise ``mixed``. Raises ``ValueError`` on no
    probes, since an unprobed CIK has no class.
    """
    if not probes:
        raise ValueError("classify_cik needs at least one probe")
    pairs = [
        (
            ensure_tz_aware_utc(s, field_name="probe stamp"),
            ensure_tz_aware_utc(g, field_name="probe sgml"),
        )
        for s, g in probes
    ]
    if all(sgml == stamp for stamp, sgml in pairs):
        return "true"
    if all(sgml == shift_stamp(stamp) for stamp, sgml in pairs):
        return "early"
    return "mixed"


def audit_sample(
    shifted: Mapping[str, Sequence[str]],
    *,
    seed: int,
    fraction: float = AUDIT_FRACTION,
) -> list[tuple[str, str]]:
    """Return the seeded audit sample as sorted ``(cik, accession)`` pairs.

    ``shifted`` maps each store-backing CIK to the accessions of its shifted
    stamps that back stored rows. The sample holds one accession per CIK
    (every CIK with at least one accession is covered) plus
    ``ceil(fraction * total)`` more drawn from the rest. The draw depends only
    on ``seed`` and the inputs' contents, not their order.
    """
    if not 0 <= fraction <= 1:
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    rng = random.Random(seed)
    picked: list[tuple[str, str]] = []
    rest: list[tuple[str, str]] = []
    total = 0
    for cik in sorted(shifted):
        accessions = sorted(set(shifted[cik]))
        if not accessions:
            continue
        total += len(accessions)
        first = rng.choice(accessions)
        picked.append((cik, first))
        rest.extend((cik, a) for a in accessions if a != first)
    extra = min(len(rest), math.ceil(fraction * total))
    picked.extend(rng.sample(rest, extra))
    return sorted(picked)


@dataclass(frozen=True)
class AuditDecision:
    """The audit stop rule's outcome.

    ``per_filing_ciks`` are the CIKs with any mismatch: their filings are
    re-stamped filing by filing from SGML. ``stop`` is True when more than the
    threshold share of audited CIKs mismatched: the run stops and writes
    nothing.
    """

    per_filing_ciks: tuple[str, ...]
    audited_ciks: int
    mismatched_ciks: int
    stop: bool


def audit_decision(
    mismatches: Mapping[str, int],
    *,
    threshold: float = AUDIT_FRACTION,
) -> AuditDecision:
    """Apply the audit stop rule to per-CIK mismatch counts.

    ``mismatches`` maps every audited CIK to its number of audited filings
    whose SGML differs from the shifted stamp (0 for a clean CIK). A CIK with
    any mismatch goes filing by filing. More than ``threshold`` of the audited
    CIKs mismatching (strictly more) stops the run.
    """
    if any(n < 0 for n in mismatches.values()):
        raise ValueError("mismatch counts must be non-negative")
    bad = tuple(sorted(cik for cik, n in mismatches.items() if n > 0))
    audited = len(mismatches)
    stop = audited > 0 and len(bad) > threshold * audited
    return AuditDecision(
        per_filing_ciks=bad,
        audited_ciks=audited,
        mismatched_ciks=len(bad),
        stop=stop,
    )


@dataclass(frozen=True)
class ManifestRow:
    """One repair manifest row: one stamp, before and after.

    ``method`` is ``shift`` (moved by the offset), ``sgml`` (taken from the
    SGML header), ``kept`` (checked, unchanged) or ``unrepaired`` (an
    undetermined CIK backing no stored row, left as is). For ``kept`` and
    ``unrepaired`` the new stamp equals the old one. ``evidence`` is free text
    saying why (the CIK's class, the probe or audit that decided it).
    Stamps are tz-aware UTC.
    """

    cik: str
    accession: str
    old_stamp: datetime
    new_stamp: datetime
    method: RepairMethod
    evidence: str

    def __post_init__(self) -> None:
        """Normalise stamps to UTC and check the method's invariants."""
        object.__setattr__(
            self, "old_stamp", ensure_tz_aware_utc(self.old_stamp, field_name="old_stamp")
        )
        object.__setattr__(
            self, "new_stamp", ensure_tz_aware_utc(self.new_stamp, field_name="new_stamp")
        )
        if self.method not in REPAIR_METHODS:
            raise ValueError(f"unknown repair method {self.method!r}")
        if self.method in ("kept", "unrepaired") and self.new_stamp != self.old_stamp:
            raise ValueError(f"a {self.method} row keeps its stamp: {self.accession}")
        if self.method == "shift" and self.new_stamp != shift_stamp(self.old_stamp):
            raise ValueError(f"a shift row moves by the offset: {self.accession}")

    @property
    def changed(self) -> bool:
        """True when the row moves the stamp."""
        return self.new_stamp != self.old_stamp

    def to_record(self) -> dict[str, str]:
        """Return the row as strings keyed by `MANIFEST_COLUMNS` (ISO stamps)."""
        return {
            "cik": self.cik,
            "accession": self.accession,
            "old_stamp": self.old_stamp.isoformat(),
            "new_stamp": self.new_stamp.isoformat(),
            "method": self.method,
            "evidence": self.evidence,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, str]) -> ManifestRow:
        """Parse a row written by `to_record` (for example a CSV DictReader row)."""
        method = record["method"]
        if method not in REPAIR_METHODS:
            raise ValueError(f"unknown repair method {method!r}")
        return cls(
            cik=record["cik"],
            accession=record["accession"],
            old_stamp=datetime.fromisoformat(record["old_stamp"]),
            new_stamp=datetime.fromisoformat(record["new_stamp"]),
            method=method,
            evidence=record["evidence"],
        )


__all__ = [
    "AUDIT_FRACTION",
    "FILING_DAY_CUTOFF",
    "MANIFEST_COLUMNS",
    "REPAIR_METHODS",
    "AuditDecision",
    "CikClass",
    "ManifestRow",
    "RepairMethod",
    "audit_decision",
    "audit_sample",
    "classify_cik",
    "is_provably_early",
    "ny_utc_offset",
    "shift_stamp",
]

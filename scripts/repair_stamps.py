"""The stamps repair cache driver for #1382 (plan task T166a).

Repairs the EDGAR stamps cache (`<edgar.cache_dir>/stamps/v<PARSER_VERSION>/`)
with T166's rules (`tradepartner.adapters.stamp_repair`), in the order the
#1382 recommendation gives, and writes the manifest T166b's
`ingest --restamp-from` reads.

Two subcommands:

- ``export``: the one-time, read-only export of the ``--bulk-written`` input
  (owner decision on #1382, 2026-10-10, option (b)). It lists every record of
  the stamps files whose modification date (New York) is still 2026-10-01, as
  ``cik,accession,stamp``. A record in a file touched after 10-01 is left out:
  it counts as not bulk-written, is never shifted and falls to the SGML steps.
  The CSV is written once and frozen; ``run`` never looks at file mtimes.
- ``run``: the repair. Dry run by default: no request, no write, a JSON report
  of the counts and the SGML requests each step will make. ``--apply`` makes
  the requests and writes.
- ``restamp-unrepaired`` (#1553): ``--from`` an earlier release's
  ``<manifest>.unrepaired.csv.gz``; every listed record is re-read from its
  own SGML header through step 4's per-filing path (`restamp_filing`), so its
  stamp becomes the header's ``ACCEPTANCE-DATETIME`` (``sgml``, or ``kept``
  when equal), never offset-adjusted. A header with no acceptance (pre-2003)
  or that does not read, and a listed record with no stamp in its file, keep
  the old stamp and are listed in this release's unrepaired file with the
  reason. The record's current stamp in its file is the manifest's
  ``old_stamp``. Dry run by default (the request count, no request, no
  write); ``--apply`` refuses above ``--max-requests`` and otherwise works as
  ``run`` does: the backup, the request journal, ``--resume``, the window
  and read-instant refusals, the manifest and sidecars under ``--release``.

**Inputs of ``run``, all frozen files.** ``--bulk-written`` (the export above),
``--classes`` (stampcheck's per-CIK classes, ``cik`` plus ``class`` or
``cls``), ``--store-ciks`` (``cik,accession``: the CIKs that back stored rows
and their stored accessions, from T166c's read-only query), ``--window-start``
(the first session of any open paper window). Filing (index) dates come from
the cache's quarterly ``form.idx`` files, which do not change once cached.

**Phases** (owner, #1382, 2026-10-10). ``--phase universe`` repairs only the
CIKs listed in ``--universe-ciks``; ``--phase rest`` repairs every other CIK
in the cache; ``--phase all`` (default) repairs the whole cache. Each phase is
its own named release (``--release``). Every step below is limited to the
phase's CIKs.

**Rewritten CIKs** (amendment 2026-10-11, (b)). A CIK of the phase with no
record in ``--bulk-written`` is ``rewritten``: an ingest after 10-01 rewrote
its file, keeping its older records. Steps 1, 3 and 4 skip it (its class from
``--classes`` is not used). Step 3b probes it instead.

**Steps.** SGML is read only through ``edgar_raw.filing_sgml_header``, at the
configured throttle, and every answer is journalled before it is used.

1. Early-class CIKs: shift every record whose current stamp is identical to
   its ``--bulk-written`` stamp. No requests.
2. Every other stamp that ``is_provably_early`` finds early is re-stamped
   from its SGML header. Above ``--max-step2-requests`` (default 10,000) an
   ``--apply`` run stops before any request, for the owner.
3. Undetermined store-backing CIKs: two SGML probes each (bulk-written
   records, stored ones first, latest stamps first), then ``classify_cik``.
   Early ones are shifted as in step 1; mixed ones join step 4.
3b. Rewritten CIKs: two SGML probes each from its records dated (New York)
   before 2026-10-01 and not in step 2, stored ones first, newest first, then
   ``classify_cik``; an unreadable probe is no probe, and no readable probe is
   ``mixed``. ``true`` lists the CIK in ``<manifest>.rewritten_ciks.txt`` and
   none of its records is ``unrepaired``; ``early`` or ``mixed`` sends its
   stored accessions to step 4 and its other records dated before 10-01 to
   ``unrepaired``. The audit stop rule does not apply to these probes.
4. Mixed store-backing CIKs: their stored accessions are re-stamped from
   SGML. So are the stored accessions missing from ``--bulk-written`` (files
   touched after 10-01) of every store-backing CIK not classed ``true``.
4b. Late API stamps (amendment 2026-10-11, (c)), with ``--late-api-from`` and
   ``--late-api-to`` (New York dates, inclusive): every record of the phase
   whose stamp's New York date is in the range and has no SGML row yet is
   re-stamped from SGML, whatever its class or export status.
5. Audit: ``audit_sample`` over the shifted stamps that back stored rows, one
   SGML read each; ``audit_decision`` sends a mismatching CIK filing by filing
   (its stored accessions re-stamped from SGML, its other shifts dropped to
   ``unrepaired``) or stops the whole run, which then writes no stamp.

A record gets at most one manifest row, and an SGML answer always wins over a
shift. Every remaining stamp of a CIK that is not classed ``true`` (after
step 3) is ``unrepaired``; those rows go to ``<manifest>.unrepaired.csv.gz``
(the same columns), with their CIKs in ``<manifest>.unrepaired_ciks.txt``, so
the manifest itself holds only the rows T166b acts on (plan amendment of
2026-10-10).

**The count** (amendment 2026-10-11, (a)). The dry run reports
``requests_floor`` (steps 2, 3, 3b, 4 known and 4b, plus the audit as if no
step-3 CIK comes out early), ``requests_ceiling`` (the floor plus the stored
accessions of step 3's CIKs as if every one came out mixed, with the audit as
if every one came out early) and ``rewritten_if_all_mixed`` (step 3b's
possible filing-by-filing load, not in the ceiling). ``--apply`` refuses when
the floor exceeds ``--max-requests``. The cap also holds during the run:
before each step's requests, the release's journal lines (resumed runs
included) plus the requests the step still has to make must fit under it, or
the run stops with the journal kept and nothing written; ``--resume`` under a
higher cap repeats no request.

**Writes and refusals.** ``--apply`` first copies the stamps directory to
``stamps/v<PARSER_VERSION>.<release>.bak``; it refuses when that name exists
(unless ``--resume``) or when the disk lacks room for the copy. The plan is
then computed from the backup, so a resumed run sees the same input. The
manifest, the request journal and the unrepaired files are written beside the
backup. Any change whose old or new stamp falls on or after ``--window-start``
(New York date) aborts the run before any stamps file is written, listing the
changes. With ``--window-earlier-ok <precheck.json>`` (amendment 2026-10-11,
(d); T166f's pre-check file, which must say pass for this ``--release`` and
``--window-start`` and be made on the run's New York date) an in-window change
that ``stamp_repair.in_window_change_allowed`` passes (an ``sgml`` move earlier
by at most New York's offset) is allowed; and any change, in the window or
not, that crosses one of the file's read instants ``t`` (``new <= t < old``
or ``old <= t < new``) aborts the run. Each changed file is
rewritten atomically in ``STAMPS_LAYOUT``; every
unchanged record, ``items`` and ``items_refreshed`` stay byte-identical.
``--resume`` reuses the backup and the request journal, so no request repeats.
The backup is copied under a temporary name and renamed when whole, so a
half-made copy is never resumed. Before writing, each live file to rewrite
must still be byte-identical to its backup (or already rewritten): a file an
ingest touched meanwhile stops the run, with nothing written.

**Other stops**, all before any request: a stored accession with no record in
its CIK's stamps file (``--store-ciks`` keyed by the wrong CIK); a request
floor above ``--max-requests`` (default 10,000, per phase); a step-1 shift in
the window. A 404/410 or a header with
no acceptance is journalled, never retried, and its stamp listed
``unrepaired``; an audit read that fails is not counted as a mismatch. The
SGML acceptance is read for any form (`sgml_acceptance`), since a Form 4
header names no FILER.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import shutil
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import parse_filing_index, parse_sgml_header
from tradepartner.adapters.edgar_source import PARSER_VERSION, STAMPS_LAYOUT
from tradepartner.adapters.stamp_repair import (
    MANIFEST_COLUMNS,
    CikClass,
    ManifestRow,
    audit_decision,
    audit_sample,
    classify_cik,
    in_window_change_allowed,
    is_provably_early,
    shift_stamp,
)
from tradepartner.store import restamp_precheck

_NEW_YORK = ZoneInfo("America/New_York")
_EASTERN = _NEW_YORK
_RELEASE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CIK = re.compile(r"^\d{10}$")
_ACCEPTANCE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")
_ACCESSION = re.compile(r"^ACCESSION NUMBER:\s*(\S+)\s*$", re.M)
_FREE_SPACE_MARGIN = 1.1  # the backup copy plus 10% headroom
_OLD_STAMPS_LAYOUT = 1
DEFAULT_MAX_STEP2_REQUESTS = 10_000
DEFAULT_MAX_REQUESTS = 10_000  # the request cap, per phase (release)
DEFAULT_SEED = 1382
BULK_WRITTEN_DAY = date(2026, 10, 1)
_PROBES_PER_CIK = 2
_GONE = (404, 410)
_JOURNAL_COLUMNS = ("cik", "accession", "sgml_stamp", "error")

Key = tuple[str, str]
Fetch = Callable[[str, str], datetime]


class RepairStop(Exception):
    """The run stops before writing any stamp; the message says why."""


# --- inputs -----------------------------------------------------------------


def _parse_stamp(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError(f"stamp {text!r} has no timezone")
    return value.astimezone(UTC)


def _norm_cik(text: str) -> str:
    return text.strip().zfill(10)


def load_bulk_written(path: Path) -> dict[Key, datetime]:
    """``(cik, accession) -> stamp`` from the frozen ``export`` CSV."""
    with path.open(newline="") as f:
        return {
            (_norm_cik(r["cik"]), r["accession"]): _parse_stamp(r["stamp"])
            for r in csv.DictReader(f)
        }


def load_classes(path: Path) -> dict[str, str]:
    """``cik -> class`` from stampcheck's CSV (column ``class`` or ``cls``)."""
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        column = "class" if "class" in fields else "cls"
        if column not in fields:
            raise ValueError(f"{path}: no class column")
        return {_norm_cik(r["cik"]): r[column].strip() for r in reader}


def load_store_ciks(path: Path) -> dict[str, set[str]]:
    """``cik -> stored accessions`` from T166c's read-only query."""
    out: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            out[_norm_cik(r["cik"])].add(r["accession"])
    return dict(out)


def load_cik_list(path: Path) -> set[str]:
    """CIKs from a file: a CSV with a ``cik`` column, or one CIK per line."""
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if lines and "cik" in lines[0].split(","):
        with path.open(newline="") as f:
            return {_norm_cik(r["cik"]) for r in csv.DictReader(f)}
    return {_norm_cik(line) for line in lines}


def load_index_dates(index_dir: Path, wanted: set[str]) -> tuple[dict[str, date], list[str]]:
    """``accession -> filing date`` from the cached quarterly ``form.idx``
    files, for the accessions in ``wanted`` (the first quarter listing one
    wins, as in the cache's own reads), and the quarters skipped because they
    do not parse (#578). A quarter the cache has not kept yet (younger than
    `edgar.index_settle_days`) gives its filings no date: the report counts
    them, since step 2 cannot judge them."""
    out: dict[str, date] = {}
    skipped: list[str] = []
    if not index_dir.is_dir():
        return out, skipped
    for path in sorted(index_dir.glob("*.idx.gz")):
        try:
            text = gzip.decompress(path.read_bytes()).decode("utf-8")
            rows = parse_filing_index(text, {}).unstamped
        except (OSError, ValueError, EOFError) as exc:
            skipped.append(f"{path.name}: {exc}")
            continue
        for row in rows:
            if row.accession in wanted:
                out.setdefault(row.accession, row.filed_on)
    return out, skipped


def federal_holidays(start_year: int, end_year: int) -> frozenset[date]:
    """US federal holidays, EDGAR's closed days besides weekends. A missed
    closure only makes `is_provably_early` ask for one more SGML header,
    whose answer is the truth either way."""
    from pandas.tseries.holiday import USFederalHolidayCalendar

    start = datetime(start_year, 1, 1)  # noqa: DTZ001 - calendar days, no instant
    end = datetime(end_year, 12, 31)  # noqa: DTZ001
    days = USFederalHolidayCalendar().holidays(start, end)
    return frozenset(d.date() for d in days)


@dataclass
class StampsFile:
    """One CIK's stamps file as read: the raw JSON and its parsed stamps."""

    cik: str
    raw: bytes
    data: dict[str, Any]
    stamps: dict[str, datetime | None]


def read_stamps_file(path: Path) -> StampsFile | None:
    """Parse one stamps file; ``None`` for one the cache itself would ignore."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if data["version"] != PARSER_VERSION or data["cik"] != path.stem:
            return None
        stamps: dict[str, datetime | None] = {}
        for accession, row in data["records"].items():
            at = row[3]
            stamps[accession] = _parse_stamp(at) if at else None
        return StampsFile(str(data["cik"]), raw, data, stamps)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def stamps_dir(cache_dir: Path) -> Path:
    """The live stamps directory."""
    return cache_dir / "stamps" / f"v{PARSER_VERSION}"


def backup_dir(cache_dir: Path, release: str) -> Path:
    """The release's backup of the stamps directory."""
    return cache_dir / "stamps" / f"v{PARSER_VERSION}.{release}.bak"


def manifest_path(cache_dir: Path, release: str) -> Path:
    """The release's manifest, beside its backup."""
    return cache_dir / "stamps" / f"v{PARSER_VERSION}.{release}.manifest.csv"


def _sidecar(manifest: Path, suffix: str) -> Path:
    return manifest.with_name(manifest.name.removesuffix(".csv") + suffix)


# --- export -----------------------------------------------------------------


def export_bulk_written(cache_dir: Path, out: Path, *, day: date = BULK_WRITTEN_DAY) -> int:
    """Write the frozen ``--bulk-written`` CSV: every stamped record of the
    stamps files whose New York modification date is ``day``. Read-only on
    the cache. Returns the number of records written."""
    if out.resolve().is_relative_to((cache_dir / "stamps").resolve()):
        raise RepairStop(f"write the frozen export outside {cache_dir / 'stamps'}")
    rows: list[tuple[str, str, str]] = []
    for path in sorted(stamps_dir(cache_dir).glob("*.json")):
        modified = datetime.fromtimestamp(path.stat().st_mtime, _NEW_YORK).date()
        if modified != day:
            continue
        parsed = read_stamps_file(path)
        if parsed is None:
            continue
        for accession, stamp in sorted(parsed.stamps.items()):
            if stamp is not None:
                rows.append((parsed.cik, accession, stamp.isoformat()))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("cik", "accession", "stamp"))
        writer.writerows(rows)
    return len(rows)


# --- the plan (no requests) -------------------------------------------------


@dataclass
class Inputs:
    """Everything ``run`` decides from, already loaded."""

    files: dict[str, StampsFile]
    bulk_written: Mapping[Key, datetime]
    classes: Mapping[str, str]
    store: Mapping[str, set[str]]
    index_dates: Mapping[str, date]
    holidays: frozenset[date]
    window_start: date
    seed: int = DEFAULT_SEED
    skipped_quarters: list[str] = field(default_factory=list)
    stored_without_stamp: list[str] = field(default_factory=list)
    late_api: tuple[date, date] | None = None


def _is_bulk(inputs: Inputs, cik: str, accession: str, stamp: datetime | None) -> bool:
    written = inputs.bulk_written.get((cik, accession))
    return stamp is not None and written is not None and written == stamp


def _ny_date(stamp: datetime) -> date:
    return stamp.astimezone(_NEW_YORK).date()


@dataclass
class Plan:
    """What the run will do before any request (the dry run's report)."""

    step1: dict[Key, ManifestRow] = field(default_factory=dict)
    step2: list[Key] = field(default_factory=list)
    step3: dict[str, list[str]] = field(default_factory=dict)
    step4_known: dict[str, list[str]] = field(default_factory=dict)
    rewritten: list[str] = field(default_factory=list)
    rewritten_probes: dict[str, list[str]] = field(default_factory=dict)
    step4b: list[Key] = field(default_factory=list)
    audit_floor: int = 0
    audit_ceiling: int = 0
    carry_ceiling: int = 0
    rewritten_if_all_mixed: int = 0

    def known_keys(self) -> set[Key]:
        """Every record the plan reads from SGML whatever the probes find."""
        keys = set(self.step2) | set(self.step4b)
        for source in (self.step3, self.step4_known, self.rewritten_probes):
            keys.update((cik, a) for cik, accessions in source.items() for a in accessions)
        return keys

    def report(self) -> dict[str, int]:
        """The counts of each step's work and requests."""
        floor = len(self.known_keys()) + self.audit_floor
        return {
            "step1_shifts": len(self.step1),
            "step2_requests": len(self.step2),
            "step3_ciks": len(self.step3),
            "step3_requests": sum(len(v) for v in self.step3.values()),
            "rewritten_ciks": len(self.rewritten),
            "rewritten_probe_requests": sum(len(v) for v in self.rewritten_probes.values()),
            "step4_known_ciks": len(self.step4_known),
            "step4_known_requests": sum(len(v) for v in self.step4_known.values()),
            "step4b_requests": len(self.step4b),
            "audit_floor": self.audit_floor,
            "requests_floor": floor,
            "requests_ceiling": floor - self.audit_floor + self.carry_ceiling + self.audit_ceiling,
            "rewritten_if_all_mixed": self.rewritten_if_all_mixed,
        }


def _shift_rows(inputs: Inputs, cik: str, evidence: str) -> dict[Key, ManifestRow]:
    rows: dict[Key, ManifestRow] = {}
    for accession, stamp in inputs.files[cik].stamps.items():
        if stamp is not None and _is_bulk(inputs, cik, accession, stamp):
            rows[(cik, accession)] = ManifestRow(
                cik, accession, stamp, shift_stamp(stamp), "shift", evidence
            )
    return rows


def _step4_accessions(inputs: Inputs, cik: str, *, mixed: bool) -> list[str]:
    """Stored accessions of ``cik`` to re-stamp from SGML: all of them for a
    mixed CIK; otherwise those missing from ``--bulk-written``."""
    stamps = inputs.files[cik].stamps
    out = []
    for accession in sorted(inputs.store.get(cik, ())):
        stamp = stamps.get(accession)
        if stamp is None:
            continue
        if mixed or (cik, accession) not in inputs.bulk_written:
            out.append(accession)
    return out


def rewritten_ciks(inputs: Inputs) -> set[str]:
    """The phase's CIKs with no record in ``--bulk-written`` (amendment (b))."""
    bulk_ciks = {cik for cik, _ in inputs.bulk_written}
    return {cik for cik in inputs.files if cik not in bulk_ciks}


def _in_late_range(inputs: Inputs, stamp: datetime | None) -> bool:
    if inputs.late_api is None or stamp is None:
        return False
    start, end = inputs.late_api
    return start <= _ny_date(stamp) <= end


def late_api_keys(inputs: Inputs, done: set[Key]) -> list[Key]:
    """Step 4b: the records whose stamp's New York date is in the late-API
    range, less ``done`` (records already read from SGML)."""
    return [
        (cik, accession)
        for cik in sorted(inputs.files)
        for accession, stamp in sorted(inputs.files[cik].stamps.items())
        if _in_late_range(inputs, stamp) and (cik, accession) not in done
    ]


def make_plan(inputs: Inputs) -> Plan:
    """Steps 1 and 2 in full, step 3's and 3b's probes, step 4's known part,
    step 4b, and the count's floor and ceiling (no requests)."""
    plan = Plan()
    rewritten = rewritten_ciks(inputs)
    plan.rewritten = sorted(rewritten)
    for cik in sorted(inputs.files):
        if inputs.classes.get(cik) == "early" and cik not in rewritten:
            plan.step1.update(_shift_rows(inputs, cik, "class early (stampcheck)"))
    for cik in sorted(inputs.files):
        for accession, stamp in sorted(inputs.files[cik].stamps.items()):
            filed = inputs.index_dates.get(accession)
            if stamp is None or filed is None or (cik, accession) in plan.step1:
                continue
            if is_provably_early(stamp, filed, holidays=inputs.holidays):
                plan.step2.append((cik, accession))
    step2 = set(plan.step2)
    for cik in sorted(inputs.store):
        if cik not in inputs.files or cik in rewritten:
            continue
        cls = inputs.classes.get(cik)
        if cls == "mixed":
            plan.step4_known[cik] = _step4_accessions(inputs, cik, mixed=True)
        elif cls not in ("early", "true"):
            plan.step3[cik] = _probe_picks(inputs, cik, step2)
        elif cls == "early":
            missing = _step4_accessions(inputs, cik, mixed=False)
            if missing:
                plan.step4_known[cik] = missing
    for cik, accessions in list(plan.step4_known.items()):
        plan.step4_known[cik] = [a for a in accessions if (cik, a) not in step2]
    for cik in plan.rewritten:
        plan.rewritten_probes[cik] = _rewritten_picks(inputs, cik, step2)
    plan.step4b = late_api_keys(inputs, plan.known_keys())
    known = plan.known_keys()

    # The audit: over the step-1 shifts that back stored rows and that no
    # SGML read replaces; the ceiling adds every step-3 CIK as if early.
    shifted: dict[str, list[str]] = defaultdict(list)
    for cik, accession in plan.step1:
        if accession in inputs.store.get(cik, ()) and (cik, accession) not in known:
            shifted[cik].append(accession)
    plan.audit_floor = len(audit_sample(shifted, seed=inputs.seed))
    for cik in plan.step3:
        for _, accession in _shift_rows(inputs, cik, ""):
            if accession in inputs.store.get(cik, ()) and (cik, accession) not in known:
                shifted[cik].append(accession)
    plan.audit_ceiling = len(audit_sample(shifted, seed=inputs.seed))
    plan.carry_ceiling = sum(
        1
        for cik in plan.step3
        for a in _step4_accessions(inputs, cik, mixed=True)
        if (cik, a) not in known
    )
    plan.rewritten_if_all_mixed = sum(
        1
        for cik in plan.rewritten
        for a in _step4_accessions(inputs, cik, mixed=True)
        if (cik, a) not in known
    )
    return plan


def _probe_picks(inputs: Inputs, cik: str, step2: set[Key]) -> list[str]:
    stamps = inputs.files[cik].stamps
    stored = inputs.store.get(cik, set())
    candidates = [
        (accession in stored, stamp, accession)
        for accession, stamp in stamps.items()
        if stamp is not None
        and _is_bulk(inputs, cik, accession, stamp)
        and (cik, accession) not in step2
    ]
    candidates.sort(reverse=True)
    return [accession for _, _, accession in candidates[:_PROBES_PER_CIK]]


def _rewritten_picks(inputs: Inputs, cik: str, step2: set[Key]) -> list[str]:
    """Step 3b's probes: records dated before 2026-10-01 (New York) and not
    in step 2, stored ones first, newest first."""
    stamps = inputs.files[cik].stamps
    stored = inputs.store.get(cik, set())
    candidates = [
        (accession in stored, stamp, accession)
        for accession, stamp in stamps.items()
        if stamp is not None
        and _ny_date(stamp) < BULK_WRITTEN_DAY
        and (cik, accession) not in step2
    ]
    candidates.sort(reverse=True)
    return [accession for _, _, accession in candidates[:_PROBES_PER_CIK]]


# --- requests ---------------------------------------------------------------


class Journal:
    """SGML answers by ``(cik, accession)``, appended to a CSV as they come,
    so a resumed run repeats no request."""

    def __init__(self, path: Path | None, fetch: Fetch, *, cap: int | None = None) -> None:
        self._path = path
        self._fetch = fetch
        self.cap = cap
        self.answers: dict[Key, datetime | str] = {}
        self.requests = 0
        self.lines = 0  # every request of the release, resumed runs included
        if path is not None and path.exists():
            with path.open(newline="") as f:
                for r in csv.DictReader(f):
                    self.lines += 1
                    try:  # a line torn by a crash is asked again
                        key = (r["cik"], r["accession"])
                        answer = _parse_stamp(r["sgml_stamp"]) if r["sgml_stamp"] else r["error"]
                    except (KeyError, TypeError, ValueError):
                        continue
                    if answer is not None:
                        self.answers[key] = answer

    def budget(self, keys: Iterable[Key], step: str) -> None:
        """Stop the run (`RepairStop`) unless the release's requests so far plus
        the ones ``keys`` still need fit under the cap (amendment (a))."""
        new = {k for k in keys if k not in self.answers}
        if self.cap is not None and self.lines + len(new) > self.cap:
            raise RepairStop(
                f"{step}: {self.lines} request(s) made in this release plus {len(new)} still "
                f"to make exceed --max-requests {self.cap}; the journal is kept, nothing is "
                "written; resume with --resume under a higher cap"
            )

    def get(self, cik: str, accession: str) -> datetime | str:
        """The SGML acceptance stamp, or the error text when the header did not
        give one; fetched once, ever."""
        key = (cik, accession)
        if key in self.answers:
            return self.answers[key]
        self.budget([key], "request")
        self.requests += 1
        self.lines += 1
        answer: datetime | str
        try:
            answer = self._fetch(cik, accession)
        except ValueError as exc:  # a header that does not parse or match
            answer = f"{type(exc).__name__}: {exc}"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _GONE:
                raise  # transient or blocked: stop; --resume picks up here
            answer = f"HTTP {exc.response.status_code}"
        self.answers[key] = answer
        if self._path is not None:
            new = not self._path.exists()
            with self._path.open("a", newline="") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(_JOURNAL_COLUMNS)
                if isinstance(answer, datetime):
                    writer.writerow((cik, accession, answer.isoformat(), ""))
                else:
                    writer.writerow((cik, accession, "", answer))
        return answer


def sgml_acceptance(text: str, accession: str) -> datetime:
    """A header's ``ACCEPTANCE-DATETIME`` as UTC, for any form.

    `parse_sgml_header` also resolves the issuer, which a Form 3/4/5 header
    (ISSUER and REPORTING-OWNER blocks) does not name the way it expects; the
    acceptance does not depend on the issuer. When the issuer does resolve,
    its acceptance is used, so both paths share one Eastern-time rule; the
    fallback applies that same rule (Eastern wall clock, ``fold=1``).
    Raises ``ValueError`` for a truncated header or another accession."""
    header, closed, _ = text.partition("</SEC-HEADER>")
    if not closed:
        raise ValueError("SGML header is truncated: no </SEC-HEADER>")
    named = _ACCESSION.search(header)
    if named is None or named.group(1) != accession:
        raise ValueError(f"SGML header for {accession} names {named.group(1) if named else None}")
    try:
        return parse_sgml_header(text).accepted_at
    except ValueError:
        stamp = _ACCEPTANCE.search(header)
        if stamp is None:
            raise
        naive = datetime.strptime(stamp.group(1), "%Y%m%d%H%M%S")  # noqa: DTZ007
        return naive.replace(tzinfo=_EASTERN, fold=1).astimezone(UTC)


def sgml_fetch(settings: Any = None) -> Fetch:
    """The live fetch: ``edgar_raw.filing_sgml_header`` (throttled), parsed."""

    def fetch(cik: str, accession: str) -> datetime:
        return sgml_acceptance(
            edgar_raw.filing_sgml_header(cik, accession, settings=settings), accession
        )

    return fetch


# --- execution --------------------------------------------------------------


@dataclass
class Outcome:
    """The run's result: the manifest rows and the counts per step."""

    rows: dict[Key, ManifestRow]
    unrepaired: list[ManifestRow]
    unrepaired_ciks: list[str]
    counts: dict[str, int]
    sgml_errors: list[str]
    rewritten_true: list[str] = field(default_factory=list)


def _sgml_row(
    journal: Journal, cik: str, accession: str, old: datetime, why: str
) -> ManifestRow | None:
    answer = journal.get(cik, accession)
    if not isinstance(answer, datetime):
        return None
    if answer == old:
        return ManifestRow(cik, accession, old, old, "kept", f"{why}; SGML equals stamp")
    return ManifestRow(cik, accession, old, answer, "sgml", f"{why}; SGML {answer.isoformat()}")


def restamp_filing(
    files: Mapping[str, StampsFile],
    journal: Journal,
    rows: dict[Key, ManifestRow],
    errors: dict[Key, str],
    key: Key,
    why: str,
) -> None:
    """The per-filing path (step 4's, and ``restamp-unrepaired``'s): the
    record's stamp becomes its own SGML acceptance, never offset-adjusted
    (``rows`` gets an ``sgml`` or ``kept`` row); a header with no acceptance
    puts its error in ``errors`` and drops any earlier row of the record."""
    cik, accession = key
    old = files[cik].stamps[accession]
    assert old is not None
    row = _sgml_row(journal, cik, accession, old, why)
    if row is None:
        errors[key] = str(journal.get(cik, accession))
        rows.pop(key, None)
    else:
        rows[key] = row


def execute(inputs: Inputs, plan: Plan, journal: Journal) -> Outcome:
    """Steps 1 to 5 with requests through ``journal``. Raises `RepairStop`
    when the audit stop rule fires or a step does not fit under the cap."""
    rows: dict[Key, ManifestRow] = dict(plan.step1)
    errors: dict[Key, str] = {}

    def restamp(cik: str, accession: str, why: str) -> None:
        restamp_filing(inputs.files, journal, rows, errors, (cik, accession), why)

    journal.budget(plan.step2, "step 2")
    for cik, accession in plan.step2:
        restamp(cik, accession, "step 2: provably early by filing date")

    rewritten = set(plan.rewritten)
    true_ciks = {c for c, cls in inputs.classes.items() if cls == "true"}
    early_ciks = {c for c, cls in inputs.classes.items() if cls == "early"}
    mixed_ciks = {c for c, cls in inputs.classes.items() if cls == "mixed"}

    def probe(cik: str, picks: list[str], why: str) -> CikClass:
        probes = []
        for accession in picks:
            restamp(cik, accession, why)
            answer = journal.get(cik, accession)
            old = inputs.files[cik].stamps[accession]
            if isinstance(answer, datetime) and old is not None:
                probes.append((old, answer))
        return classify_cik(probes) if probes else "mixed"

    journal.budget(((c, a) for c, p in plan.step3.items() for a in p), "step 3")
    step3_classes: dict[str, str] = {}
    for cik, picks in plan.step3.items():
        cls = probe(cik, picks, "step 3: class probe")
        step3_classes[cik] = cls
        if cls == "early":
            early_ciks.add(cik)
            for key, row in _shift_rows(inputs, cik, "class early (step 3 probes)").items():
                rows.setdefault(key, row)
        elif cls == "true":
            true_ciks.add(cik)
        else:
            mixed_ciks.add(cik)

    journal.budget(((c, a) for c, p in plan.rewritten_probes.items() for a in p), "step 3b")
    rewritten_classes: dict[str, str] = {}
    for cik, picks in plan.rewritten_probes.items():
        rewritten_classes[cik] = probe(cik, picks, "step 3b: rewritten CIK probe")

    step4: dict[str, list[str]] = dict(plan.step4_known)
    for cik, found in step3_classes.items():
        if found == "true":
            continue
        accessions = _step4_accessions(inputs, cik, mixed=found == "mixed")
        if accessions:
            step4[cik] = accessions
    for cik, found in rewritten_classes.items():
        if found != "true":
            accessions = _step4_accessions(inputs, cik, mixed=True)
            if accessions:
                step4[cik] = accessions

    def needs_sgml(key: Key) -> bool:
        row = rows.get(key)
        return key not in errors and (row is None or row.method == "shift")

    step4_keys = [(c, a) for c, accs in sorted(step4.items()) for a in accs if needs_sgml((c, a))]
    journal.budget(step4_keys, "step 4")
    for cik, accession in step4_keys:
        restamp(cik, accession, "step 4: mixed or not bulk-written, filing by filing")

    step4b_keys = [k for k in late_api_keys(inputs, set()) if needs_sgml(k)]
    journal.budget(step4b_keys, "step 4b")
    for cik, accession in step4b_keys:
        restamp(cik, accession, "step 4b: late API stamp")

    shifted: dict[str, list[str]] = defaultdict(list)
    for (cik, accession), row in rows.items():
        if row.method == "shift" and accession in inputs.store.get(cik, ()):
            shifted[cik].append(accession)
    sample = audit_sample(shifted, seed=inputs.seed)
    journal.budget(sample, "step 5 audit")
    mismatches: dict[str, int] = {}
    audit_errors = 0
    for cik, accession in sample:
        answer = journal.get(cik, accession)
        if not isinstance(answer, datetime):
            # No header is no evidence either way: not counted as audited.
            errors[(cik, accession)] = answer
            audit_errors += 1
            continue
        expected = rows[(cik, accession)].new_stamp
        mismatches[cik] = mismatches.get(cik, 0) + (answer != expected)
    decision = audit_decision(mismatches)
    if decision.stop:
        raise RepairStop(
            f"audit stop rule: {decision.mismatched_ciks} of {decision.audited_ciks} "
            f"audited CIKs mismatch: {', '.join(decision.per_filing_ciks)}"
        )
    per_filing = [
        (cik, accession)
        for cik in decision.per_filing_ciks
        for accession in sorted(inputs.store.get(cik, ()))
        if inputs.files[cik].stamps.get(accession) is not None
    ]
    journal.budget(per_filing, "step 5 filing by filing")
    for cik in decision.per_filing_ciks:
        early_ciks.discard(cik)
        mixed_ciks.add(cik)
        for key in [k for k, r in rows.items() if k[0] == cik and r.method == "shift"]:
            del rows[key]
    for cik, accession in per_filing:
        restamp(cik, accession, "step 5: audit mismatch, filing by filing")

    unrepaired: list[ManifestRow] = []
    unrepaired_ciks: set[str] = set()
    for cik in sorted(inputs.files):
        for accession, stamp in sorted(inputs.files[cik].stamps.items()):
            key = (cik, accession)
            if stamp is None or key in rows:
                continue
            if key in errors:
                why = f"SGML gave no stamp: {errors[key]}"
            elif cik in rewritten:
                found = rewritten_classes.get(cik, "mixed")
                if found == "true" or _ny_date(stamp) >= BULK_WRITTEN_DAY:
                    continue
                why = f"rewritten CIK, probes {found}, not repaired"
            elif cik in true_ciks:
                continue
            elif cik in early_ciks and key not in inputs.bulk_written:
                why = "early CIK, record not bulk-written"
            elif cik in early_ciks:
                why = "early CIK, stamp differs from --bulk-written"
            else:
                why = f"class {inputs.classes.get(cik, 'undetermined')}, not repaired"
            unrepaired.append(ManifestRow(cik, accession, stamp, stamp, "unrepaired", why))
            unrepaired_ciks.add(cik)

    method_counts: dict[str, int] = defaultdict(int)
    for row in rows.values():
        method_counts[row.method] += 1
    counts = {
        **plan.report(),
        "step3_early": sum(1 for c in step3_classes.values() if c == "early"),
        "step3_true": sum(1 for c in step3_classes.values() if c == "true"),
        "step3_mixed": sum(1 for c in step3_classes.values() if c == "mixed"),
        "rewritten_true": sum(1 for c in rewritten_classes.values() if c == "true"),
        "rewritten_early": sum(1 for c in rewritten_classes.values() if c == "early"),
        "rewritten_mixed": sum(1 for c in rewritten_classes.values() if c == "mixed"),
        "step4_requests": len(step4_keys),
        "step4b_reads": len(step4b_keys),
        "audit_sample": len(sample),
        "audit_ciks": decision.audited_ciks,
        "audit_mismatched_ciks": decision.mismatched_ciks,
        "shift": method_counts["shift"],
        "sgml": method_counts["sgml"],
        "kept": method_counts["kept"],
        "unrepaired": len(unrepaired),
        "unrepaired_ciks": len(unrepaired_ciks),
        "sgml_errors": len(errors),
        "audit_errors": audit_errors,
        "requests_made": journal.requests,
        "requests_release": journal.lines,
    }
    listed = [f"{c} {a}: {e}" for (c, a), e in sorted(errors.items())]
    rewritten_true = sorted(c for c, found in rewritten_classes.items() if found == "true")
    return Outcome(rows, unrepaired, sorted(unrepaired_ciks), counts, listed, rewritten_true)


WindowRule = Callable[[ManifestRow, date], bool]


def in_window(row: ManifestRow, window_start: date) -> bool:
    """True when the row changes a stamp whose old or new New York date is on
    or after ``window_start``."""
    return row.changed and (
        _ny_date(row.old_stamp) >= window_start or _ny_date(row.new_stamp) >= window_start
    )


def window_violations(
    rows: Iterable[ManifestRow], window_start: date, allowed: WindowRule | None = None
) -> list[ManifestRow]:
    """Changes whose old or new stamp falls on or after ``window_start``
    (New York date): any one aborts the run (data-releases runbook, rule 5).
    With ``allowed`` (T166f's ``in_window_change_allowed``, under
    ``--window-earlier-ok``), an in-window change it passes is not one."""
    return [
        r
        for r in rows
        if in_window(r, window_start) and (allowed is None or not allowed(r, window_start))
    ]


def _refuse_window(
    rows: Iterable[ManifestRow], window_start: date, allowed: WindowRule | None = None
) -> None:
    bad = window_violations(rows, window_start, allowed)
    if bad:
        raise RepairStop(f"{len(bad)} change(s) on or after {window_start}: {_list_rows(bad)}")


def _list_rows(rows: Iterable[ManifestRow]) -> str:
    return "; ".join(
        f"{r.cik} {r.accession} {r.old_stamp.isoformat()} -> {r.new_stamp.isoformat()}"
        for r in rows
    )


@dataclass(frozen=True)
class Precheck:
    """The parts of T166f's ``precheck.<release>.json`` the driver uses."""

    path: Path
    instants: tuple[datetime, ...]
    sha256: str


def load_precheck(path: Path, *, release: str, window_start: date, now: datetime) -> Precheck:
    """Read ``--window-earlier-ok``'s file with T166f's loader; `RepairStop`
    unless it says pass for this release and window start and was made on
    ``now``'s New York date (the driver has no store, so the day is its
    staleness rule; amendment (d))."""
    try:
        data = restamp_precheck.load_precheck(path)
    except (OSError, ValueError) as exc:
        raise RepairStop(f"--window-earlier-ok {path}: unreadable pre-check file: {exc}") from exc
    if not data.passed:
        raise RepairStop(f"--window-earlier-ok {path}: the pre-check did not pass")
    if data.release != release:
        raise RepairStop(
            f"--window-earlier-ok {path}: made for release {data.release!r}, not {release!r}"
        )
    if data.window_first_session != window_start:
        raise RepairStop(
            f"--window-earlier-ok {path}: made for window start {data.window_first_session}, "
            f"not {window_start.isoformat()}"
        )
    if _ny_date(data.made_at) != _ny_date(now):
        raise RepairStop(
            f"--window-earlier-ok {path}: made {data.made_at.isoformat()}, not on the run's "
            f"New York date {_ny_date(now).isoformat()}; run the pre-check again"
        )
    return Precheck(path, tuple(sorted(data.instants)), data.sha256)


def instant_violations(
    rows: Iterable[ManifestRow], instants: Sequence[datetime]
) -> list[ManifestRow]:
    """Changes that cross a read instant ``t`` (amendment (d), the exact
    check, T166f's `crossed_instants`): ``new <= t < old`` for an earlier
    move and, read the other way, ``old <= t < new`` for a later one. Every
    changed row is checked, not only in-window ones: a correction dated the
    day before the window's first session can still cross ``close(S-1)``."""
    return [
        r
        for r in rows
        if r.changed
        and (
            restamp_precheck.crossed_instants(r.old_stamp, r.new_stamp, instants)
            or restamp_precheck.crossed_instants(r.new_stamp, r.old_stamp, instants)
        )
    ]


def _refuse_instants(rows: Iterable[ManifestRow], precheck: Precheck) -> None:
    read = instant_violations(rows, precheck.instants)
    if read:
        raise RepairStop(
            f"{len(read)} change(s) cross a read instant of {precheck.path} "
            f"(new <= t < old or old <= t < new): {_list_rows(read)}"
        )


def rewrite_file(stamps_file: StampsFile, changes: Mapping[str, datetime]) -> bytes:
    """``stamps_file`` with the stamps in ``changes`` replaced, in
    ``STAMPS_LAYOUT``. Every other byte of a ``STAMPS_LAYOUT`` file is kept;
    an old-layout file is converted as the cache's own write would."""
    data = json.loads(stamps_file.raw)
    if json.dumps(data).encode("utf-8") != stamps_file.raw:
        raise RepairStop(f"{stamps_file.cik}: stamps file is not in the cache's own encoding")
    layout = data.get("layout", _OLD_STAMPS_LAYOUT)
    if layout == _OLD_STAMPS_LAYOUT:
        data = {
            "version": data["version"],
            "layout": STAMPS_LAYOUT,
            "cik": data["cik"],
            "items_refreshed": False,
            "records": {a: [*row, None] for a, row in data["records"].items()},
        }
    elif layout != STAMPS_LAYOUT:
        raise RepairStop(f"{stamps_file.cik}: unknown stamps layout {layout!r}")
    for accession, new in changes.items():
        data["records"][accession][3] = new.astimezone(UTC).isoformat()
    return json.dumps(data).encode("utf-8")


def _write_rows(path: Path, rows: Iterable[ManifestRow], *, compress: bool = False) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_record())
    data = buffer.getvalue().encode("utf-8")
    edgar_raw.write_atomic(path, gzip.compress(data, mtime=0) if compress else data)


# --- run --------------------------------------------------------------------


def _read_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _plan_report(inputs: Inputs, plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    """The dry run's report: counts, known requests, and what would stop."""
    out: dict[str, Any] = dict(plan.report(), ciks=len(inputs.files))
    out["step2_over_limit"] = len(plan.step2) > args.max_step2_requests
    out["requests_over_limit"] = out["requests_floor"] > args.max_requests
    out["window_changes_known"] = len(window_violations(plan.step1.values(), inputs.window_start))
    out["late_api_range"] = (
        [d.isoformat() for d in inputs.late_api] if inputs.late_api is not None else None
    )
    # Candidates already dated in the window: a later move there aborts the run.
    for name, keys in (("step2", plan.step2), ("step4b", plan.step4b)):
        dated = [
            (c, a)
            for c, a in keys
            if (st := inputs.files[c].stamps[a]) is not None and _ny_date(st) >= inputs.window_start
        ]
        out[f"{name}_in_window"] = len(dated)
        out[f"{name}_in_window_list"] = [f"{c} {a}" for c, a in dated]
    out["stamps_without_index_date"] = sum(
        1
        for f in inputs.files.values()
        for a, st in f.stamps.items()
        if st is not None and a not in inputs.index_dates
    )
    out["skipped_quarters"] = inputs.skipped_quarters
    out["stored_without_stamp"] = len(inputs.stored_without_stamp)
    return out


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _scope(all_ciks: Iterable[str], phase: str, universe: set[str]) -> set[str]:
    ciks = set(all_ciks)
    if phase == "universe":
        return ciks & universe
    if phase == "rest":
        return ciks - universe
    return ciks


def load_inputs(
    source: Path,
    args: argparse.Namespace,
    universe: set[str],
) -> Inputs:
    """Read the stamps files of the phase's CIKs from ``source`` and the
    frozen input files."""
    names = {p.stem: p for p in source.glob("*.json") if _CIK.match(p.stem)}
    files: dict[str, StampsFile] = {}
    for cik in sorted(_scope(names, args.phase, universe)):
        parsed = read_stamps_file(names[cik])
        if parsed is not None:
            files[cik] = parsed
    scope = _scope(names, args.phase, universe)
    store_all = load_store_ciks(args.store_ciks)
    # `--store-ciks` is keyed by the CIK of the stamps file holding the
    # accession (a 25-NSE sits under the exchange's CIK). A stored accession
    # missing from its file means the keys are wrong: listed, and --apply stops.
    missing = sorted(
        f"{c} {a}"
        for c, accessions in store_all.items()
        if c in scope
        for a in accessions
        if c not in files or a not in files[c].stamps
    )
    store = {c: a for c, a in store_all.items() if c in files}
    wanted = {a for f in files.values() for a, s in f.stamps.items() if s is not None}
    index_dates, skipped = load_index_dates(args.cache_dir / "index", wanted)
    years = [d.year for d in index_dates.values()] or [2000]
    return Inputs(
        files=files,
        bulk_written=load_bulk_written(args.bulk_written),
        classes=load_classes(args.classes),
        store=store,
        index_dates=index_dates,
        holidays=federal_holidays(min(years) - 1, max(years) + 1),
        window_start=args.window_start,
        seed=args.seed,
        skipped_quarters=skipped,
        stored_without_stamp=missing,
        late_api=_late_api(args),
    )


def _late_api(args: argparse.Namespace) -> tuple[date, date] | None:
    start, end = args.late_api_from, args.late_api_to
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise RepairStop("--late-api-from and --late-api-to go together")
    if end < start:
        raise RepairStop(f"--late-api-to {end} is before --late-api-from {start}")
    return (start, end)


@dataclass
class Release:
    """One named release's paths and window rule, as ``run`` and
    ``restamp-unrepaired`` share them."""

    live: Path
    backup: Path
    manifest: Path
    precheck: Precheck | None
    allowed: WindowRule | None


def _open_release(args: argparse.Namespace, report: dict[str, Any]) -> Release:
    """Check the release name, the backup against ``--apply``/``--resume``
    and ``--window-earlier-ok``'s file; nothing is written."""
    if not _RELEASE.match(args.release):
        raise RepairStop(f"release name {args.release!r} must match {_RELEASE.pattern}")
    backup = backup_dir(args.cache_dir, args.release)
    if args.apply:
        if backup.exists() and not args.resume:
            raise RepairStop(f"backup {backup} exists; pick another --release or pass --resume")
        if args.resume and not backup.exists():
            raise RepairStop(f"--resume needs the backup {backup}")
    precheck: Precheck | None = None
    allowed: WindowRule | None = None
    if args.window_earlier_ok is not None:
        precheck = load_precheck(
            args.window_earlier_ok,
            release=args.release,
            window_start=args.window_start,
            now=datetime.now(UTC),
        )
        allowed = in_window_change_allowed
        report["window_earlier_ok"] = str(precheck.path)
        report["window_earlier_ok_sha256"] = precheck.sha256
    return Release(
        stamps_dir(args.cache_dir),
        backup,
        manifest_path(args.cache_dir, args.release),
        precheck,
        allowed,
    )


def _make_backup(release: Release, args: argparse.Namespace) -> None:
    """Copy the live stamps directory to the release's backup, unless
    resuming; refuses without disk room."""
    if args.resume:
        return
    live, backup = release.live, release.backup
    need = _dir_bytes(live)
    free = shutil.disk_usage(live).free
    if free < need * _FREE_SPACE_MARGIN:
        raise RepairStop(f"disk has {free} bytes free, the backup needs {need} plus 10%")
    partial = backup.with_name(f".{backup.name}.partial")
    if partial.exists():
        shutil.rmtree(partial)  # an earlier copy that never finished
    shutil.copytree(live, partial)
    partial.rename(backup)  # the backup name exists only once the copy is whole


def _journal(release: Release, args: argparse.Namespace, fetch: Fetch | None) -> Journal:
    return Journal(
        _sidecar(release.manifest, ".requests.csv"), fetch or sgml_fetch(), cap=args.max_requests
    )


def _write_release(
    release: Release,
    args: argparse.Namespace,
    files: Mapping[str, StampsFile],
    outcome: Outcome,
    report: dict[str, Any],
) -> None:
    """The window and read-instant refusals, then the stamps files, the
    manifest and its sidecars."""
    _refuse_window(outcome.rows.values(), args.window_start, release.allowed)
    if release.precheck is not None:
        _refuse_instants(outcome.rows.values(), release.precheck)
        # A long run may pass New York midnight: the file must still be the
        # run's day when the moves are written (amendment (d), staleness).
        load_precheck(
            release.precheck.path,
            release=args.release,
            window_start=args.window_start,
            now=datetime.now(UTC),
        )

    live, manifest = release.live, release.manifest
    changes: dict[str, dict[str, datetime]] = defaultdict(dict)
    for (cik, accession), row in outcome.rows.items():
        if row.changed:
            changes[cik][accession] = row.new_stamp
    new_files = {cik: rewrite_file(files[cik], c) for cik, c in changes.items()}
    moved = [
        cik
        for cik in sorted(new_files)
        if _read_or_none(live / f"{cik}.json") not in (files[cik].raw, new_files[cik])
    ]
    if moved:
        raise RepairStop(
            f"{len(moved)} live stamps file(s) changed since the backup (an ingest ran?): "
            f"{', '.join(moved[:20])}; rerun under a new --release"
        )
    for cik, data in sorted(new_files.items()):
        edgar_raw.write_atomic(live / f"{cik}.json", data)
    _write_rows(manifest, (outcome.rows[k] for k in sorted(outcome.rows)))
    _write_rows(_sidecar(manifest, ".unrepaired.csv.gz"), outcome.unrepaired, compress=True)
    edgar_raw.write_atomic(
        _sidecar(manifest, ".unrepaired_ciks.txt"),
        "".join(f"{c}\n" for c in outcome.unrepaired_ciks).encode(),
    )
    edgar_raw.write_atomic(
        _sidecar(manifest, ".rewritten_ciks.txt"),
        "".join(f"{c}\n" for c in outcome.rewritten_true).encode(),
    )
    report.update(outcome.counts, files_rewritten=len(new_files), manifest=str(manifest))
    report["sgml_error_list"] = outcome.sgml_errors


def run(args: argparse.Namespace, fetch: Fetch | None = None) -> dict[str, Any]:
    """The ``run`` subcommand; returns the JSON report."""
    report: dict[str, Any] = {"release": args.release, "phase": args.phase, "apply": args.apply}
    universe = load_cik_list(args.universe_ciks) if args.universe_ciks else set()
    if args.phase != "all" and not universe:
        raise RepairStop(f"--phase {args.phase} needs --universe-ciks")
    release = _open_release(args, report)
    inputs = load_inputs(
        release.backup if args.apply and args.resume else release.live, args, universe
    )
    plan = make_plan(inputs)
    report.update(_plan_report(inputs, plan, args))
    if not args.apply:
        return report
    if inputs.stored_without_stamp:
        raise RepairStop(
            f"{len(inputs.stored_without_stamp)} stored accession(s) have no record in their "
            f"CIK's stamps file: {'; '.join(inputs.stored_without_stamp[:20])}"
        )
    if report["step2_over_limit"]:
        raise RepairStop(f"step 2 needs more than {args.max_step2_requests} requests")
    if report["requests_over_limit"]:
        raise RepairStop(
            f"the request floor ({report['requests_floor']}) exceeds --max-requests "
            f"{args.max_requests}"
        )
    _refuse_window(plan.step1.values(), inputs.window_start, release.allowed)
    if release.precheck is not None:
        _refuse_instants(plan.step1.values(), release.precheck)
    _make_backup(release, args)
    outcome = execute(inputs, plan, _journal(release, args, fetch))
    _write_release(release, args, inputs.files, outcome, report)
    return report


# --- restamp-unrepaired (#1553) ---------------------------------------------


def load_unrepaired(path: Path) -> list[ManifestRow]:
    """The rows of an earlier release's ``<manifest>.unrepaired.csv.gz`` (or
    a plain CSV), every one of method ``unrepaired``; `RepairStop` on any
    other file (a manifest given by mistake)."""
    try:
        raw = path.read_bytes()
        text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        missing = [c for c in MANIFEST_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"lacks column(s) {', '.join(missing)}")
        rows = [ManifestRow.from_record(r) for r in reader]
    except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
        raise RepairStop(f"--from {path}: not an unrepaired list: {exc}") from exc
    other = sorted({r.method for r in rows} - {"unrepaired"})
    if other:
        raise RepairStop(
            f"--from {path}: rows of method {', '.join(other)}; not an unrepaired list"
        )
    return rows


@dataclass
class UnrepairedPlan:
    """``restamp-unrepaired``'s inputs and requests, before any request."""

    files: dict[str, StampsFile]
    keys: list[Key]
    missing: list[ManifestRow]
    stamp_differs: int

    def report(self, window_start: date) -> dict[str, Any]:
        """The dry run's counts; ``requests`` is the most the run makes."""
        dated = [
            f"{c} {a}"
            for c, a in self.keys
            if (st := self.files[c].stamps[a]) is not None and _ny_date(st) >= window_start
        ]
        return {
            "listed": len(self.keys) + len(self.missing),
            "listed_ciks": len({c for c, _ in self.keys} | {r.cik for r in self.missing}),
            "requests": len(self.keys),
            "requests_floor": len(self.keys),
            "listed_without_stamp": len(self.missing),
            "stamp_differs_from_list": self.stamp_differs,
            "listed_in_window": len(dated),
            "listed_in_window_list": dated,
        }


def plan_unrepaired(source: Path, listed: Sequence[ManifestRow]) -> UnrepairedPlan:
    """Read the listed CIKs' stamps files from ``source``. A listed record
    re-reads from the stamp its file holds now; one with no stamped record
    there is kept as unrepaired."""
    files: dict[str, StampsFile] = {}
    for cik in sorted({r.cik for r in listed}):
        parsed = read_stamps_file(source / f"{cik}.json") if _CIK.match(cik) else None
        if parsed is not None:
            files[cik] = parsed
    keys: list[Key] = []
    missing: list[ManifestRow] = []
    differs = 0
    seen: set[Key] = set()
    for row in sorted(listed, key=lambda r: (r.cik, r.accession)):
        key = (row.cik, row.accession)
        if key in seen:
            continue
        seen.add(key)
        stamp = files[row.cik].stamps.get(row.accession) if row.cik in files else None
        if stamp is None:
            missing.append(row)
            continue
        differs += stamp != row.old_stamp
        keys.append(key)
    return UnrepairedPlan(files, keys, missing, differs)


def execute_unrepaired(plan: UnrepairedPlan, journal: Journal) -> Outcome:
    """Each listed record through step 4's per-filing path; a header with no
    acceptance (pre-2003) or that does not read keeps its stamp and is listed
    again with its reason."""
    rows: dict[Key, ManifestRow] = {}
    errors: dict[Key, str] = {}
    journal.budget(plan.keys, "restamp-unrepaired")
    for key in plan.keys:
        restamp_filing(plan.files, journal, rows, errors, key, "unrepaired list, filing by filing")
    unrepaired = [
        ManifestRow(r.cik, r.accession, r.old_stamp, r.old_stamp, "unrepaired", "no stamped record")
        for r in plan.missing
    ]
    for cik, accession in sorted(errors):
        stamp = plan.files[cik].stamps[accession]
        assert stamp is not None
        why = f"SGML gave no stamp: {errors[(cik, accession)]}"
        unrepaired.append(ManifestRow(cik, accession, stamp, stamp, "unrepaired", why))
    unrepaired.sort(key=lambda r: (r.cik, r.accession))
    method_counts: dict[str, int] = defaultdict(int)
    for row in rows.values():
        method_counts[row.method] += 1
    counts = {
        "sgml": method_counts["sgml"],
        "kept": method_counts["kept"],
        "unrepaired": len(unrepaired),
        "unrepaired_ciks": len({r.cik for r in unrepaired}),
        "sgml_errors": len(errors),
        "requests_made": journal.requests,
        "requests_release": journal.lines,
    }
    listed = [f"{c} {a}: {e}" for (c, a), e in sorted(errors.items())]
    return Outcome(rows, unrepaired, sorted({r.cik for r in unrepaired}), counts, listed)


def run_unrepaired(args: argparse.Namespace, fetch: Fetch | None = None) -> dict[str, Any]:
    """The ``restamp-unrepaired`` subcommand (#1553): every record of an
    earlier release's unrepaired list takes its own SGML acceptance; returns
    the JSON report. Dry run unless ``--apply``."""
    report: dict[str, Any] = {
        "release": args.release,
        "mode": "restamp-unrepaired",
        "from": str(args.source),
        "apply": args.apply,
    }
    release = _open_release(args, report)
    listed = load_unrepaired(args.source)
    plan = plan_unrepaired(release.backup if args.apply and args.resume else release.live, listed)
    report.update(plan.report(args.window_start))
    report["requests_over_limit"] = len(plan.keys) > args.max_requests
    if not args.apply:
        return report
    if report["requests_over_limit"]:
        raise RepairStop(f"the {len(plan.keys)} requests exceed --max-requests {args.max_requests}")
    _make_backup(release, args)
    outcome = execute_unrepaired(plan, _journal(release, args, fetch))
    _write_release(release, args, plan.files, outcome, report)
    return report


def _date(text: str) -> date:
    return date.fromisoformat(text)


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="write the frozen --bulk-written CSV (read-only)")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--day", type=_date, default=BULK_WRITTEN_DAY)
    r = sub.add_parser("run", help="the repair (dry run unless --apply)")
    r.add_argument("--bulk-written", type=Path, required=True)
    r.add_argument("--classes", type=Path, required=True)
    r.add_argument("--store-ciks", type=Path, required=True)
    r.add_argument("--window-start", type=_date, required=True)
    r.add_argument("--release", required=True)
    r.add_argument("--phase", choices=("universe", "rest", "all"), default="all")
    r.add_argument("--universe-ciks", type=Path, default=None)
    r.add_argument("--max-step2-requests", type=int, default=DEFAULT_MAX_STEP2_REQUESTS)
    r.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--late-api-from", type=_date, default=None)
    r.add_argument("--late-api-to", type=_date, default=None)
    r.add_argument("--window-earlier-ok", type=Path, default=None, metavar="PRECHECK_JSON")
    r.add_argument("--apply", action="store_true")
    r.add_argument("--resume", action="store_true")
    u = sub.add_parser(
        "restamp-unrepaired",
        help="re-stamp an unrepaired list from SGML, filing by filing (dry run unless --apply)",
    )
    u.add_argument("--from", dest="source", type=Path, required=True, metavar="UNREPAIRED_CSV")
    u.add_argument("--window-start", type=_date, required=True)
    u.add_argument("--release", required=True)
    u.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    u.add_argument("--window-earlier-ok", type=Path, default=None, metavar="PRECHECK_JSON")
    u.add_argument("--apply", action="store_true")
    u.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, fetch: Fetch | None = None) -> int:
    """Entry point: prints the JSON report; exit 1 on a stop."""
    args = build_parser().parse_args(argv)
    if args.cache_dir is None:
        from tradepartner.config import get_settings

        args.cache_dir = Path(get_settings().edgar.cache_dir)
    try:
        if args.command == "export":
            n = export_bulk_written(args.cache_dir, args.out, day=args.day)
            print(json.dumps({"exported": n, "out": str(args.out)}))
            return 0
        command = run_unrepaired if args.command == "restamp-unrepaired" else run
        print(json.dumps(command(args, fetch), indent=2, sort_keys=True))
    except RepairStop as stop:
        print(f"repair_stamps: stopped: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

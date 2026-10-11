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
4. Mixed store-backing CIKs: their stored accessions are re-stamped from
   SGML. So are the stored accessions missing from ``--bulk-written`` (files
   touched after 10-01) of every store-backing CIK not classed ``true``.
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

**Writes and refusals.** ``--apply`` first copies the stamps directory to
``stamps/v<PARSER_VERSION>.<release>.bak``; it refuses when that name exists
(unless ``--resume``) or when the disk lacks room for the copy. The plan is
then computed from the backup, so a resumed run sees the same input. The
manifest, the request journal and the unrepaired files are written beside the
backup. Any change whose old or new stamp falls on or after ``--window-start``
(New York date) aborts the run before any stamps file is written, listing the
changes. Each changed file is rewritten atomically in ``STAMPS_LAYOUT``; every
unchanged record, ``items`` and ``items_refreshed`` stay byte-identical.
``--resume`` reuses the backup and the request journal, so no request repeats.
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
    ManifestRow,
    audit_decision,
    audit_sample,
    classify_cik,
    is_provably_early,
    shift_stamp,
)

_NEW_YORK = ZoneInfo("America/New_York")
_RELEASE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_OLD_STAMPS_LAYOUT = 1
DEFAULT_MAX_STEP2_REQUESTS = 10_000
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


def load_index_dates(index_dir: Path, wanted: set[str]) -> dict[str, date]:
    """``accession -> filing date`` from the cached quarterly ``form.idx``
    files, for the accessions in ``wanted`` (the first quarter listing one
    wins, as in the cache's own reads)."""
    out: dict[str, date] = {}
    if not index_dir.is_dir():
        return out
    for path in sorted(index_dir.glob("*.idx.gz")):
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
        for row in parse_filing_index(text, {}).unstamped:
            if row.accession in wanted:
                out.setdefault(row.accession, row.filed_on)
    return out


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
        if data["version"] != PARSER_VERSION:
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


def _is_bulk(inputs: Inputs, cik: str, accession: str, stamp: datetime | None) -> bool:
    written = inputs.bulk_written.get((cik, accession))
    return stamp is not None and written is not None and written == stamp


@dataclass
class Plan:
    """What the run will do before any request (the dry run's report)."""

    step1: dict[Key, ManifestRow] = field(default_factory=dict)
    step2: list[Key] = field(default_factory=list)
    step3: dict[str, list[str]] = field(default_factory=dict)
    step4_known: dict[str, list[str]] = field(default_factory=dict)

    def report(self) -> dict[str, int]:
        """The counts of each step's work and requests."""
        return {
            "step1_shifts": len(self.step1),
            "step2_requests": len(self.step2),
            "step3_ciks": len(self.step3),
            "step3_requests": sum(len(v) for v in self.step3.values()),
            "step4_known_ciks": len(self.step4_known),
            "step4_known_requests": sum(len(v) for v in self.step4_known.values()),
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


def make_plan(inputs: Inputs) -> Plan:
    """Steps 1 and 2 in full, step 3's probes and step 4's known part."""
    plan = Plan()
    for cik in sorted(inputs.files):
        if inputs.classes.get(cik) == "early":
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
        if cik not in inputs.files:
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


# --- requests ---------------------------------------------------------------


class Journal:
    """SGML answers by ``(cik, accession)``, appended to a CSV as they come,
    so a resumed run repeats no request."""

    def __init__(self, path: Path | None, fetch: Fetch) -> None:
        self._path = path
        self._fetch = fetch
        self.answers: dict[Key, datetime | str] = {}
        self.requests = 0
        if path is not None and path.exists():
            with path.open(newline="") as f:
                for r in csv.DictReader(f):
                    key = (r["cik"], r["accession"])
                    self.answers[key] = (
                        _parse_stamp(r["sgml_stamp"]) if r["sgml_stamp"] else r["error"]
                    )

    def get(self, cik: str, accession: str) -> datetime | str:
        """The SGML acceptance stamp, or the error text when the header did not
        give one; fetched once, ever."""
        key = (cik, accession)
        if key in self.answers:
            return self.answers[key]
        self.requests += 1
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


def sgml_fetch(settings: Any = None) -> Fetch:
    """The live fetch: ``edgar_raw.filing_sgml_header`` (throttled), parsed."""

    def fetch(cik: str, accession: str) -> datetime:
        text = edgar_raw.filing_sgml_header(cik, accession, settings=settings)
        header = parse_sgml_header(text, cik=cik)
        if header.accession != accession:
            raise ValueError(f"SGML header for {accession} names {header.accession}")
        return header.accepted_at

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


def _sgml_row(
    journal: Journal, cik: str, accession: str, old: datetime, why: str
) -> ManifestRow | None:
    answer = journal.get(cik, accession)
    if not isinstance(answer, datetime):
        return None
    if answer == old:
        return ManifestRow(cik, accession, old, old, "kept", f"{why}; SGML equals stamp")
    return ManifestRow(cik, accession, old, answer, "sgml", f"{why}; SGML {answer.isoformat()}")


def execute(inputs: Inputs, plan: Plan, journal: Journal) -> Outcome:
    """Steps 1 to 5 with requests through ``journal``. Raises `RepairStop`
    when the audit stop rule fires."""
    rows: dict[Key, ManifestRow] = dict(plan.step1)
    errors: list[str] = []

    def restamp(cik: str, accession: str, why: str) -> None:
        old = inputs.files[cik].stamps[accession]
        assert old is not None
        row = _sgml_row(journal, cik, accession, old, why)
        if row is None:
            errors.append(f"{cik} {accession}: {journal.get(cik, accession)}")
            rows.pop((cik, accession), None)
        else:
            rows[(cik, accession)] = row

    for cik, accession in plan.step2:
        restamp(cik, accession, "step 2: provably early by filing date")

    true_ciks = {c for c, cls in inputs.classes.items() if cls == "true"}
    early_ciks = {c for c, cls in inputs.classes.items() if cls == "early"}
    mixed_ciks = {c for c, cls in inputs.classes.items() if cls == "mixed"}
    step3_classes: dict[str, str] = {}
    for cik, picks in plan.step3.items():
        probes = []
        for accession in picks:
            restamp(cik, accession, "step 3: class probe")
            answer = journal.get(cik, accession)
            old = inputs.files[cik].stamps[accession]
            if isinstance(answer, datetime) and old is not None:
                probes.append((old, answer))
        cls = classify_cik(probes) if probes else "mixed"
        step3_classes[cik] = cls
        if cls == "early":
            early_ciks.add(cik)
            for key, row in _shift_rows(inputs, cik, "class early (step 3 probes)").items():
                rows.setdefault(key, row)
        elif cls == "true":
            true_ciks.add(cik)
        else:
            mixed_ciks.add(cik)

    step4: dict[str, list[str]] = dict(plan.step4_known)
    for cik, found in step3_classes.items():
        accessions = _step4_accessions(inputs, cik, mixed=found == "mixed")
        if accessions:
            step4[cik] = accessions
    step4_count = 0
    for cik, accessions in sorted(step4.items()):
        for accession in accessions:
            if rows.get((cik, accession)) is not None and rows[(cik, accession)].method != "shift":
                continue
            step4_count += 1
            restamp(cik, accession, "step 4: mixed or not bulk-written, filing by filing")

    shifted: dict[str, list[str]] = defaultdict(list)
    for (cik, accession), row in rows.items():
        if row.method == "shift" and accession in inputs.store.get(cik, ()):
            shifted[cik].append(accession)
    sample = audit_sample(shifted, seed=inputs.seed)
    mismatches: dict[str, int] = {}
    for cik, accession in sample:
        answer = journal.get(cik, accession)
        expected = rows[(cik, accession)].new_stamp
        mismatches[cik] = mismatches.get(cik, 0) + (answer != expected)
    decision = audit_decision(mismatches)
    if decision.stop:
        raise RepairStop(
            f"audit stop rule: {decision.mismatched_ciks} of {decision.audited_ciks} "
            f"audited CIKs mismatch: {', '.join(decision.per_filing_ciks)}"
        )
    for cik in decision.per_filing_ciks:
        early_ciks.discard(cik)
        mixed_ciks.add(cik)
        for key in [k for k, r in rows.items() if k[0] == cik and r.method == "shift"]:
            del rows[key]
        for accession in sorted(inputs.store.get(cik, ())):
            if inputs.files[cik].stamps.get(accession) is not None:
                restamp(cik, accession, "step 5: audit mismatch, filing by filing")

    unrepaired: list[ManifestRow] = []
    unrepaired_ciks: set[str] = set()
    for cik in sorted(inputs.files):
        if cik in true_ciks:
            continue
        for accession, stamp in sorted(inputs.files[cik].stamps.items()):
            if stamp is None or (cik, accession) in rows:
                continue
            if cik in early_ciks and (cik, accession) not in inputs.bulk_written:
                why = "early CIK, record not bulk-written"
            elif cik in early_ciks:
                continue  # bulk-written but already moved since 10-01: not shifted
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
        "step4_requests": step4_count,
        "audit_sample": len(sample),
        "audit_ciks": decision.audited_ciks,
        "audit_mismatched_ciks": decision.mismatched_ciks,
        "shift": method_counts["shift"],
        "sgml": method_counts["sgml"],
        "kept": method_counts["kept"],
        "unrepaired": len(unrepaired),
        "unrepaired_ciks": len(unrepaired_ciks),
        "sgml_errors": len(errors),
        "requests_made": journal.requests,
    }
    return Outcome(rows, unrepaired, sorted(unrepaired_ciks), counts, errors)


def window_violations(rows: Iterable[ManifestRow], window_start: date) -> list[ManifestRow]:
    """Changes whose old or new stamp falls on or after ``window_start``
    (New York date): any one aborts the run (data-releases runbook, rule 5)."""
    return [
        r
        for r in rows
        if r.changed
        and (
            r.old_stamp.astimezone(_NEW_YORK).date() >= window_start
            or r.new_stamp.astimezone(_NEW_YORK).date() >= window_start
        )
    ]


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
    names = {_norm_cik(p.stem): p for p in source.glob("*.json")}
    files: dict[str, StampsFile] = {}
    for cik in sorted(_scope(names, args.phase, universe)):
        parsed = read_stamps_file(names[cik])
        if parsed is not None:
            files[cik] = parsed
    store = {c: a for c, a in load_store_ciks(args.store_ciks).items() if c in files}
    wanted = {a for f in files.values() for a, s in f.stamps.items() if s is not None}
    index_dates = load_index_dates(args.cache_dir / "index", wanted)
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
    )


def run(args: argparse.Namespace, fetch: Fetch | None = None) -> dict[str, Any]:
    """The ``run`` subcommand; returns the JSON report."""
    if not _RELEASE.match(args.release):
        raise RepairStop(f"release name {args.release!r} must match {_RELEASE.pattern}")
    universe = load_cik_list(args.universe_ciks) if args.universe_ciks else set()
    if args.phase != "all" and not universe:
        raise RepairStop(f"--phase {args.phase} needs --universe-ciks")
    live = stamps_dir(args.cache_dir)
    backup = backup_dir(args.cache_dir, args.release)
    manifest = manifest_path(args.cache_dir, args.release)
    report: dict[str, Any] = {"release": args.release, "phase": args.phase, "apply": args.apply}

    if not args.apply:
        inputs = load_inputs(live, args, universe)
        plan = make_plan(inputs)
        report.update(plan.report(), ciks=len(inputs.files))
        report["step2_over_limit"] = len(plan.step2) > args.max_step2_requests
        report["requests_known"] = (
            report["step2_requests"] + report["step3_requests"] + report["step4_known_requests"]
        )
        return report

    if backup.exists() and not args.resume:
        raise RepairStop(f"backup {backup} exists; pick another --release or pass --resume")
    if args.resume and not backup.exists():
        raise RepairStop(f"--resume needs the backup {backup}")
    if not backup.exists():
        need = _dir_bytes(live)
        free = shutil.disk_usage(live).free
        if free <= need:
            raise RepairStop(f"disk has {free} bytes free, the backup needs {need}")
        # Plan before copying, so the step 2 limit refuses without a backup.
        if len(make_plan(load_inputs(live, args, universe)).step2) > args.max_step2_requests:
            raise RepairStop(f"step 2 needs more than {args.max_step2_requests} requests")
        shutil.copytree(live, backup)
    inputs = load_inputs(backup, args, universe)
    plan = make_plan(inputs)
    if len(plan.step2) > args.max_step2_requests:
        raise RepairStop(f"step 2 needs more than {args.max_step2_requests} requests")
    journal = Journal(_sidecar(manifest, ".requests.csv"), fetch or sgml_fetch())
    outcome = execute(inputs, plan, journal)
    bad = window_violations(outcome.rows.values(), inputs.window_start)
    if bad:
        listed = "; ".join(
            f"{r.cik} {r.accession} {r.old_stamp.isoformat()} -> {r.new_stamp.isoformat()}"
            for r in bad
        )
        raise RepairStop(f"{len(bad)} change(s) on or after {inputs.window_start}: {listed}")

    changes: dict[str, dict[str, datetime]] = defaultdict(dict)
    for (cik, accession), row in outcome.rows.items():
        if row.changed:
            changes[cik][accession] = row.new_stamp
    rewritten = {cik: rewrite_file(inputs.files[cik], c) for cik, c in changes.items()}
    for cik, data in sorted(rewritten.items()):
        edgar_raw.write_atomic(live / f"{cik}.json", data)
    _write_rows(manifest, (outcome.rows[k] for k in sorted(outcome.rows)))
    _write_rows(_sidecar(manifest, ".unrepaired.csv.gz"), outcome.unrepaired, compress=True)
    edgar_raw.write_atomic(
        _sidecar(manifest, ".unrepaired_ciks.txt"),
        "".join(f"{c}\n" for c in outcome.unrepaired_ciks).encode(),
    )
    report.update(outcome.counts, files_rewritten=len(rewritten), manifest=str(manifest))
    report["sgml_error_list"] = outcome.sgml_errors
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
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--apply", action="store_true")
    r.add_argument("--resume", action="store_true")
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
        print(json.dumps(run(args, fetch), indent=2, sort_keys=True))
    except RepairStop as stop:
        print(f"repair_stamps: stopped: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The gold session: the draw, the exclusion, the session files, the records, the lock.

Research-labeling spec C10 (docs/specs/research-labeling.md) as the 2026-10-07
amendment (#1121) edits it, and req 10's gold sample; plan task T131, the gold half
of T123b split out on #1177 under C13's split clause. No Streamlit import: the
review page (T123c) draws only what this module hands it and writes only through
it. Everything here is the owner's blind gold labelling, made **before any model
output exists for the gold items** (ADR 0013 point 5, owner decision 7).

**The draw** (`build_gold_session`). It reads the registered frame export by the
path the caller gives and refuses (`FrameChanged`) when its SHA-256 differs from
the row's. It removes every listing end of the issuers named in the exclusion file
(ADR 0013 "Owner decisions 2026-10-06 (#1026)" decision 2): a row matched on
`listing_end_id` or on any `documents.amendments[].accession`, and every other row
whose CIK is a matched row's CIK or a CIK in the file's `cik` column, compared in
the ten-digit zero-padded form. An excluded accession that matches no row is
listed, and the build refuses when the file gives it no CIK. It then draws **one
listing end per CIK per period** under the seed: `n_dev` from the `dev` period
(the earliest acceptance span holding `dev_span_min_rows` frame rows) and the rest
from the `pilot` period after it. Only `listing_end_id`, `cik` and
`form25_accepted_at` take part, never a `rule_*` column, `rule_provision` or a
model output, so the draw selects on nothing the gold measures. The seeded error
set sits beside the draw, flagged `seed_case`, whatever its CIK.

**Blindness** (`InferenceFilesPresent`). The build and every resume refuse when
any regular file exists anywhere under the research store's `inferences/` (a
recursive walk; empty directories do not count; not `datafiles.inference_paths`'s
run-id filter, so a stray name cannot hide model output; #1042, #1121). Nothing
here opens an inference file.

**The working file** (`record_label`, `record_skip`, `record_undo`). Each appends
one line to `gold/departure-reason/working.jsonl` through `datafiles.append_jsonl`
before returning; the last line per `listing_end_id` wins. `record_label` and
`record_skip` refuse a case whose final line is an answer (or an `unlabelled`
skip), with no bypass parameter; `record_undo` appends an undo line that reopens
the case (#1029 F5). A case skipped a second time once no unseen case is left is
recorded `unlabelled`, never replaced (replacing after reading would be
selection).

**The lock** (`lock_gold`). A partial session is never locked. The export is the
sampled frame rows, every frame column (`documents` and `form25_accepted_at`
included), joined to the final working line per case, plus `gold_class`,
`seed_case`, `in_random_draw` and `unlabelled`; content-addressed under
`datafiles.gold_path`, with its split file, and registered `locked` under
`departure-reason-gold` with the sealed `pilot` period through `store.research`,
the module's one write outside the research directory. A `StoreLockedError` from
the caller's connection is returned as the retryable `busy` state: the working
file is untouched and the session stays unlocked.

This module opens no connection itself: the lock's registry write uses the
connection the caller's `connect` opens.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import polars as pl

from tradepartner.research import datafiles
from tradepartner.research.labeling.crosswalk import class_of
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET, OptionSet
from tradepartner.store.db import StoreLockedError
from tradepartner.store.research import register_dataset

if TYPE_CHECKING:
    import duckdb

    from tradepartner.config import Settings

#: The registered name of the locked gold export (C7).
DATASET_NAME = "departure-reason-gold"
#: Req 10: 30 `dev` items, the rest of `n` from `pilot`.
N_DEV = 30
#: Req 10: `dev` is the earliest acceptance span holding at least this many rows.
DEV_SPAN_MIN_ROWS = 300
#: C10: `seconds_spent` is capped here and flagged `idle` beyond it.
SECONDS_CAP = 600.0
#: C10's "relied on" choice: the notice, an 8-K item, or outside the shown text.
_ITEM_REF = re.compile(r"8-K item \d+\.\d{2}")
CONFIDENCE = frozenset({"high", "low"})
EDGAR_INDEX_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
    "&type=&dateb={dateb}&owner=include&count=100"
)

Split = Literal["dev", "pilot"]
Status = Literal["open", "skipped", "answered", "unlabelled"]


class GoldRefused(ValueError):
    """A gold-session step refused (the message says why and what to fix)."""


class InferenceFilesPresent(GoldRefused):
    """A regular file exists under the research store's `inferences/` (C10 blindness)."""


class FrameChanged(GoldRefused):
    """The frame export's SHA-256 differs from its registered row."""


class SessionMismatch(GoldRefused):
    """Resume flags differ from `session.json`."""


class AlreadyAnswered(GoldRefused):
    """The case's final working line is an answer; `record_undo` reopens it."""


class SessionPartial(GoldRefused):
    """A case has no final answer; a partial session is never locked."""


class SessionLocked(GoldRefused):
    """The session is locked; it only displays."""


@dataclass(frozen=True)
class FrameExport:
    """The registered frame the caller names: its dataset id, the export's path as
    given on the command line, and the row's SHA-256."""

    dataset_id: int
    path: Path
    sha256: str


@dataclass(frozen=True)
class ExclusionResult:
    """The exclusion's three counts (C10), the accessions that matched no row, and
    the exclusion file's SHA-256."""

    sha256: str
    accessions_matched: int
    ciks: int
    rows_removed: int
    unmatched: tuple[str, ...]


@dataclass(frozen=True)
class GoldCase:
    """One case of the session: `in_random_draw` is false only for a seed the draw
    did not pick (outside the accuracy denominator, req 10)."""

    listing_end_id: str
    split: Split
    seed_case: bool
    in_random_draw: bool


@dataclass(frozen=True)
class GoldFlags:
    """What a resume was given on the command line; `None` means not given."""

    frame_dataset_id: int | None = None
    seed: int | None = None
    n: int | None = None
    exclude: Path | None = None


@dataclass(frozen=True)
class GoldSession:
    """An open gold session: `session.json`'s content plus the sampled frame rows."""

    path: Path
    settings: Settings
    frame: FrameExport
    seed: int
    n: int
    exclusion_sha256: str
    dev_boundary: date
    pilot_period: tuple[date, date]
    cases: tuple[GoldCase, ...]
    rows: Mapping[str, Mapping[str, Any]] = field(repr=False)
    option_set: OptionSet = DEFAULT_OPTION_SET

    @property
    def working_path(self) -> Path:
        """The working file beside `session.json`."""
        return datafiles.gold_working_path(self.settings)

    @property
    def lock_path(self) -> Path:
        """Written once the lock's registration has committed."""
        return self.path.with_name("lock.json")


@dataclass(frozen=True)
class BuildResult:
    """`build_gold_session`'s session, exclusion counts and seeds the frame lacks."""

    session: GoldSession
    exclusion: ExclusionResult
    seed_cases_missing: tuple[str, ...]


@dataclass(frozen=True)
class EightKView:
    """The chosen 8-K, its items in `research.labeling.eightk_items` order."""

    form: str
    accession: str
    filed_on: str | None
    items: tuple[tuple[str, str], ...]
    body_head: str | None


@dataclass(frozen=True)
class CaseView:
    """What the page draws for one case (C10): never a `rule_*` value, a model
    field, a seed flag or anything from an inference file."""

    index: int
    total: int
    listing_end_id: str
    split: Split
    issuer: str | None
    exchange: str | None
    class_title: str | None
    filed_on: str | None
    effective_on: str | None
    provision: str | None
    notice: str | None
    eightk: EightKView | None
    index_url: str
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LockResult:
    """`locked` with the dataset id, or the retryable `busy` (store locked)."""

    state: Literal["locked", "busy"]
    sha256: str
    scorable_pilot: int
    dataset_id: int | None = None
    message: str | None = None


# --- paths, blindness, the frame --------------------------------------------------


def session_path(settings: Settings) -> Path:
    """`gold/departure-reason/session.json`, beside the working file."""
    return datafiles.gold_working_path(settings).with_name("session.json")


def refuse_if_inference_files(settings: Settings) -> None:
    """Raise `InferenceFilesPresent` when any regular file exists under
    the research store's `inferences/` (a recursive walk; no file is opened)."""
    root = datafiles.data_dir(settings) / "inferences"
    if not root.is_dir():
        return
    found = sorted(p for p in root.rglob("*") if p.is_file())
    if found:
        names = ", ".join(str(p.relative_to(root)) for p in found[:5])
        raise InferenceFilesPresent(
            f"gold is made before any model output: {len(found)} file(s) under {root} "
            f"({names}); the gold session cannot start while they exist"
        )


def _read_frame(frame: FrameExport) -> pl.DataFrame:
    data = frame.path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != frame.sha256:
        raise FrameChanged(
            f"frame {frame.path} has SHA-256 {actual}, dataset {frame.dataset_id}'s row "
            f"has {frame.sha256}"
        )
    return pl.read_parquet(io.BytesIO(data))


def _cik10(value: object) -> str:
    return f"{int(str(value).strip()):010d}"


def _documents(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row["documents"]
    return dict(json.loads(raw) if isinstance(raw, str) else raw)


def _accepted_day(value: datetime) -> date:
    return value.astimezone(UTC).date()


# --- the exclusion and the draw ---------------------------------------------------


def _read_exclusion(path: Path) -> tuple[bytes, dict[str, str | None], set[str]]:
    """The file's bytes, accession -> CIK (or None), and every CIK it names."""
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames is None or "accession" not in reader.fieldnames:
        raise GoldRefused(f"exclusion file {path} needs an `accession` column")
    accessions: dict[str, str | None] = {}
    ciks: set[str] = set()
    for record in reader:
        accession = (record.get("accession") or "").strip()
        cik_text = (record.get("cik") or "").strip()
        cik = _cik10(cik_text) if cik_text else None
        if cik is not None:
            ciks.add(cik)
        if accession:
            accessions[accession] = accessions.get(accession) or cik
    return data, accessions, ciks


def apply_exclusion(
    rows: Sequence[Mapping[str, Any]], exclude: Path
) -> tuple[set[str], ExclusionResult]:
    """The `listing_end_id`s the exclusion file removes and its counts (C10, #1121).
    Refuses when an accession matches no row and the file gives it no CIK."""
    data, accessions, file_ciks = _read_exclusion(exclude)
    excluded = set(accessions)
    matched_rows: set[str] = set()
    matched_accessions: set[str] = set()
    for row in rows:
        own = {row["listing_end_id"]}
        own |= {a["accession"] for a in _documents(row).get("amendments") or []}
        hits = own & excluded
        if hits:
            matched_rows.add(row["listing_end_id"])
            matched_accessions |= hits
    unmatched = tuple(sorted(excluded - matched_accessions))
    without_cik = [a for a in unmatched if accessions[a] is None]
    if without_cik:
        raise GoldRefused(
            f"excluded accession(s) match no frame row and the file gives no CIK, so the "
            f"issuer's other listing ends cannot be removed: {', '.join(without_cik)}"
        )
    ciks = {_cik10(r["cik"]) for r in rows if r["listing_end_id"] in matched_rows} | file_ciks
    removed = {
        r["listing_end_id"]
        for r in rows
        if r["listing_end_id"] in matched_rows or _cik10(r["cik"]) in ciks
    }
    return removed, ExclusionResult(
        sha256=hashlib.sha256(data).hexdigest(),
        accessions_matched=len(matched_accessions),
        ciks=len(ciks),
        rows_removed=len(removed),
        unmatched=unmatched,
    )


def _dev_boundary(rows: Sequence[Mapping[str, Any]], min_rows: int) -> date:
    """The last day of the earliest acceptance span holding `min_rows` frame rows."""
    if len(rows) < min_rows:
        raise GoldRefused(f"the frame has {len(rows)} rows; the dev span needs {min_rows}")
    times = sorted(r["form25_accepted_at"] for r in rows)
    return _accepted_day(times[min_rows - 1])


def _draw(
    candidates: Sequence[Mapping[str, Any]], count: int, rng: random.Random, split: str
) -> list[str]:
    """One listing end per CIK: permute under `rng`, keep each new CIK's first."""
    ordered = sorted(candidates, key=lambda r: r["listing_end_id"])
    rng.shuffle(ordered)
    drawn: list[str] = []
    seen: set[str] = set()
    for row in ordered:
        if len(drawn) == count:
            break
        cik = _cik10(row["cik"])
        if cik not in seen:
            seen.add(cik)
            drawn.append(row["listing_end_id"])
    if len(drawn) < count:
        raise GoldRefused(f"the {split} period has {len(drawn)} CIKs to draw from; {count} asked")
    return drawn


def build_gold_session(
    frame: FrameExport,
    seed: int,
    n: int,
    exclude: Path,
    *,
    settings: Settings,
    seed_cases: Sequence[str] = (),
    n_dev: int = N_DEV,
    dev_span_min_rows: int = DEV_SPAN_MIN_ROWS,
) -> BuildResult:
    """Draw the gold sample (module docstring) and write `session.json`.

    Refuses when inference files exist, when a session already exists (resume it
    with `open_gold_session`), when the frame's hash differs from its row, and on
    the exclusion's unmatched accession with no CIK."""
    refuse_if_inference_files(settings)
    path = session_path(settings)
    if path.exists():
        raise GoldRefused(f"a gold session already exists at {path}; resume it instead")
    if not 0 < n_dev < n:
        raise GoldRefused(f"n = {n} must exceed n_dev = {n_dev} > 0")
    table = _read_frame(frame)
    rows = table.select("listing_end_id", "cik", "form25_accepted_at", "documents").to_dicts()
    removed, exclusion = apply_exclusion(rows, exclude)
    boundary = _dev_boundary(rows, dev_span_min_rows)
    last_day = max(_accepted_day(r["form25_accepted_at"]) for r in rows)
    pilot_period = (boundary + timedelta(days=1), last_day)

    def split_of(row: Mapping[str, Any]) -> Split:
        return "dev" if _accepted_day(row["form25_accepted_at"]) <= boundary else "pilot"

    kept = [r for r in rows if r["listing_end_id"] not in removed]
    rng = random.Random(seed)
    drawn: dict[Split, list[str]] = {
        "dev": _draw([r for r in kept if split_of(r) == "dev"], n_dev, rng, "dev"),
        "pilot": _draw([r for r in kept if split_of(r) == "pilot"], n - n_dev, rng, "pilot"),
    }
    by_id = {r["listing_end_id"]: r for r in rows}
    seeds = set(seed_cases)
    missing = tuple(sorted(seeds - set(by_id)))
    groups: dict[Split, list[GoldCase]] = {}
    for split in ("dev", "pilot"):
        in_draw = set(drawn[split])
        group = [GoldCase(i, split, i in seeds, True) for i in drawn[split]]
        group += [
            GoldCase(i, split, True, False)
            for i in sorted(seeds & set(by_id))
            if split_of(by_id[i]) == split and i not in in_draw
        ]
        rng.shuffle(group)
        groups[split] = group
    cases = tuple(groups["dev"] + groups["pilot"])
    doc = {
        "frame": {
            "dataset_id": frame.dataset_id,
            "path": str(frame.path),
            "sha256": frame.sha256,
        },
        "seed": seed,
        "n": n,
        "n_dev": n_dev,
        "exclusion": {
            "sha256": exclusion.sha256,
            "accessions_matched": exclusion.accessions_matched,
            "ciks": exclusion.ciks,
            "rows_removed": exclusion.rows_removed,
            "unmatched": list(exclusion.unmatched),
        },
        "dev_boundary": boundary.isoformat(),
        "pilot_period": [d.isoformat() for d in pilot_period],
        "option_set": {"version": DEFAULT_OPTION_SET.version, "hash": DEFAULT_OPTION_SET.hash},
        "seed_cases_missing": list(missing),
        "cases": [
            {
                "listing_end_id": c.listing_end_id,
                "split": c.split,
                "seed_case": c.seed_case,
                "in_random_draw": c.in_random_draw,
            }
            for c in cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    session = _session_from(path, doc, settings, table)
    return BuildResult(session=session, exclusion=exclusion, seed_cases_missing=missing)


def _session_from(
    path: Path, doc: Mapping[str, Any], settings: Settings, table: pl.DataFrame
) -> GoldSession:
    cases = tuple(
        GoldCase(c["listing_end_id"], c["split"], c["seed_case"], c["in_random_draw"])
        for c in doc["cases"]
    )
    ids = {c.listing_end_id for c in cases}
    rows = {
        r["listing_end_id"]: r
        for r in table.filter(pl.col("listing_end_id").is_in(sorted(ids))).to_dicts()
    }
    frame = FrameExport(
        doc["frame"]["dataset_id"], Path(doc["frame"]["path"]), doc["frame"]["sha256"]
    )
    start, end = (date.fromisoformat(d) for d in doc["pilot_period"])
    return GoldSession(
        path=path,
        settings=settings,
        frame=frame,
        seed=doc["seed"],
        n=doc["n"],
        exclusion_sha256=doc["exclusion"]["sha256"],
        dev_boundary=date.fromisoformat(doc["dev_boundary"]),
        pilot_period=(start, end),
        cases=cases,
        rows=rows,
    )


def open_gold_session(path: Path, flags: GoldFlags, *, settings: Settings) -> GoldSession:
    """Resume the session at `path`: the inference-file refusal again, then
    `SessionMismatch` for any given flag that differs from `session.json`, then
    `FrameChanged` if the frame export moved."""
    refuse_if_inference_files(settings)
    doc = json.loads(path.read_text(encoding="utf-8"))
    differ: list[str] = []
    if flags.frame_dataset_id is not None and flags.frame_dataset_id != doc["frame"]["dataset_id"]:
        differ.append(f"--frame {flags.frame_dataset_id} (session: {doc['frame']['dataset_id']})")
    if flags.seed is not None and flags.seed != doc["seed"]:
        differ.append(f"--seed {flags.seed} (session: {doc['seed']})")
    if flags.n is not None and flags.n != doc["n"]:
        differ.append(f"--n {flags.n} (session: {doc['n']})")
    if flags.exclude is not None:
        given = hashlib.sha256(flags.exclude.read_bytes()).hexdigest()
        if given != doc["exclusion"]["sha256"]:
            differ.append(f"--exclude {flags.exclude} (a different file from the session's)")
    if differ:
        raise SessionMismatch("flags differ from the gold session: " + "; ".join(differ))
    frame = FrameExport(
        doc["frame"]["dataset_id"], Path(doc["frame"]["path"]), doc["frame"]["sha256"]
    )
    return _session_from(path, doc, settings, _read_frame(frame))


# --- what the page draws ----------------------------------------------------------


def _ordered_items(items: Mapping[str, str], order: Sequence[str]) -> tuple[tuple[str, str], ...]:
    ranked = [k for k in order if k in items] + [k for k in items if k not in order]
    return tuple((k, items[k]) for k in ranked)


def case_view(session: GoldSession, i: int) -> CaseView:
    """Case `i` as the page draws it (module docstring): the Form 25 fields, the
    notice when it is text, the 8-K items in `eightk_items` order, the fallback
    EDGAR index URL and the nine options with their descriptions."""
    case = session.cases[i]
    docs = _documents(session.rows[case.listing_end_id])
    labeling = session.settings.research.labeling
    exhibit = docs.get("exhibit") or {}
    notice = exhibit.get("text") if exhibit.get("status") == "text" else None
    raw_8k = docs.get("eightk")
    eightk = None
    if raw_8k:
        eightk = EightKView(
            form=raw_8k.get("form") or "8-K",
            accession=raw_8k.get("accession") or "",
            filed_on=raw_8k.get("filed_on"),
            items=_ordered_items(raw_8k.get("items") or {}, labeling.eightk_items),
            body_head=raw_8k.get("body_head"),
        )
    filed_on = str(docs["form25_filed_on"])
    until = date.fromisoformat(filed_on) + timedelta(days=labeling.context_after_days)
    return CaseView(
        index=i,
        total=len(session.cases),
        listing_end_id=case.listing_end_id,
        split=case.split,
        issuer=docs.get("issuer"),
        exchange=docs.get("exchange_name") or docs.get("exchange"),
        class_title=docs.get("class_title"),
        filed_on=filed_on,
        effective_on=docs.get("effective_on"),
        provision=docs.get("rule_provision_raw") or docs.get("rule_provision"),
        notice=notice,
        eightk=eightk,
        index_url=EDGAR_INDEX_URL.format(cik=docs["cik"], dateb=f"{until:%Y%m%d}"),
        options=session.option_set.options,
    )


# --- the working file -------------------------------------------------------------


def _lines(session: GoldSession) -> list[dict[str, Any]]:
    path = session.working_path
    return datafiles.read_jsonl(path) if path.exists() else []


def _states(session: GoldSession) -> tuple[dict[str, Status], dict[str, int], dict[str, int]]:
    """Per case: its status, its skips since the last answer or undo, and the
    line position of its last skip (for the end-of-queue order)."""
    status: dict[str, Status] = {c.listing_end_id: "open" for c in session.cases}
    skips = dict.fromkeys(status, 0)
    last_skip = dict.fromkeys(status, -1)
    for position, line in enumerate(_lines(session)):
        lid = line.get("listing_end_id")
        if lid not in status:
            raise GoldRefused(f"working file names {lid!r}, which is not a case of this session")
        if line.get("undo"):
            status[lid], skips[lid] = "open", 0
        elif line.get("skipped"):
            status[lid] = "unlabelled" if line.get("unlabelled") else "skipped"
            skips[lid] += 1
            last_skip[lid] = position
        else:
            status[lid], skips[lid] = "answered", 0
    return status, skips, last_skip


def _writable(session: GoldSession, listing_end_id: str) -> dict[str, Status]:
    if session.lock_path.exists():
        raise SessionLocked(f"the gold session {session.path} is locked")
    status, _, _ = _states(session)
    if listing_end_id not in status:
        raise GoldRefused(f"{listing_end_id!r} is not a case of this session")
    return status


def _seconds(value: float) -> tuple[float, bool]:
    if value < 0:
        raise GoldRefused(f"seconds_spent must be >= 0, got {value}")
    return min(value, SECONDS_CAP), value > SECONDS_CAP


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append(session: GoldSession, line: Mapping[str, Any]) -> None:
    datafiles.append_jsonl(session.working_path, [line])


def _check_label(session: GoldSession, line: Mapping[str, Any]) -> None:
    """The checks `record_label` makes and `lock_gold` repeats on the file."""
    names = {name for name, _ in session.option_set.options}
    lid = line.get("listing_end_id")
    if line.get("gold_label") not in names:
        raise GoldRefused(f"{lid}: label {line.get('gold_label')!r} is not one of the options")
    if line.get("text_states") is not None and line["text_states"] not in names:
        raise GoldRefused(f"{lid}: text_states {line['text_states']!r} is not one of the options")
    relied_on = line.get("relied_on")
    if relied_on not in ("notice", "outside") and not _ITEM_REF.fullmatch(str(relied_on)):
        raise GoldRefused(
            f"{lid}: relied_on {relied_on!r} must be `notice`, `8-K item N.NN` or `outside`"
        )
    if relied_on == "outside" and not (line.get("passage_ref") or "").strip():
        raise GoldRefused(f"{lid}: an `outside` label needs the filing's accession or URL")
    if line.get("confidence") not in CONFIDENCE:
        raise GoldRefused(f"{lid}: confidence {line.get('confidence')!r} is not high or low")


def record_label(
    session: GoldSession,
    listing_end_id: str,
    *,
    label: str,
    relied_on: str,
    seconds_spent: float,
    passage_ref: str | None = None,
    text_states: str | None = None,
    confidence: str = "high",
) -> None:
    """Append one answer line. Refuses a case whose final line is an answer or an
    `unlabelled` skip (`AlreadyAnswered`; `record_undo` reopens it), a label
    outside the option set, and an `outside` label without `passage_ref`."""
    status = _writable(session, listing_end_id)
    if status[listing_end_id] in ("answered", "unlabelled"):
        raise AlreadyAnswered(f"{listing_end_id} is already answered; undo it first")
    seconds, idle = _seconds(seconds_spent)
    line = {
        "listing_end_id": listing_end_id,
        "gold_label": label,
        "text_states": text_states,
        "relied_on": relied_on,
        "passage_ref": passage_ref,
        "confidence": confidence,
        "seconds_spent": seconds,
        "idle": idle,
        "labeled_at": _now(),
    }
    _check_label(session, line)
    _append(session, line)


def record_skip(session: GoldSession, listing_end_id: str, *, seconds_spent: float = 0.0) -> None:
    """Append one skip line. A second skip once no open case is left records the
    case `unlabelled` (C10: counted, never replaced). Refuses an answered case."""
    status = _writable(session, listing_end_id)
    if status[listing_end_id] in ("answered", "unlabelled"):
        raise AlreadyAnswered(f"{listing_end_id} is already answered; undo it first")
    _, skips, _ = _states(session)
    others_open = any(s == "open" for lid, s in status.items() if lid != listing_end_id)
    seconds, idle = _seconds(seconds_spent)
    _append(
        session,
        {
            "listing_end_id": listing_end_id,
            "skipped": True,
            "unlabelled": skips[listing_end_id] >= 1 and not others_open,
            "seconds_spent": seconds,
            "idle": idle,
            "labeled_at": _now(),
        },
    )


def record_undo(session: GoldSession, listing_end_id: str) -> None:
    """Append an undo line (`undo = true`, no answer) that reopens the case; the
    next `record_label` is accepted and, as the last line, wins at the lock."""
    status = _writable(session, listing_end_id)
    if status[listing_end_id] == "open":
        raise GoldRefused(f"{listing_end_id} has nothing to undo")
    _append(session, {"listing_end_id": listing_end_id, "undo": True, "labeled_at": _now()})


def next_case(session: GoldSession) -> int | None:
    """The first case with no final line (an undo leaves none), else the skipped
    cases in the order they were skipped; `None` when every case is final."""
    status, _, last_skip = _states(session)
    for i, case in enumerate(session.cases):
        if status[case.listing_end_id] == "open":
            return i
    skipped = [
        (last_skip[c.listing_end_id], i)
        for i, c in enumerate(session.cases)
        if status[c.listing_end_id] == "skipped"
    ]
    return min(skipped)[1] if skipped else None


# --- the lock ---------------------------------------------------------------------


def _parquet_bytes(table: pl.DataFrame, metadata: Mapping[str, str]) -> bytes:
    buffer = io.BytesIO()
    table.write_parquet(buffer, metadata=dict(metadata))
    return buffer.getvalue()


def _span(days: Iterable[date]) -> tuple[date, date]:
    ordered = sorted(days)
    return ordered[0], ordered[-1]


def _write_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != data:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def lock_gold(
    session: GoldSession,
    connect: Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]],
) -> LockResult:
    """Lock a complete session (module docstring): write the export and its split
    file, register them through `store.research` on the connection `connect`
    opens, then mark the session locked. A locked session returns its lock again.
    `SessionPartial` on a case with no final answer; `busy` on `StoreLockedError`."""
    if session.lock_path.exists():
        done = json.loads(session.lock_path.read_text(encoding="utf-8"))
        return LockResult("locked", done["sha256"], done["scorable_pilot"], done["dataset_id"])
    status, _, _ = _states(session)
    partial = sorted(lid for lid, s in status.items() if s in ("open", "skipped"))
    if partial:
        raise SessionPartial(f"{len(partial)} case(s) have no final answer: {partial[:5]}")
    finals: dict[str, dict[str, Any]] = {}
    for line in _lines(session):
        finals[line["listing_end_id"]] = line
    out: list[dict[str, Any]] = []
    splits: list[str] = []
    scorable = 0
    for case in sorted(session.cases, key=lambda c: c.listing_end_id):
        final = finals[case.listing_end_id]
        unlabelled = bool(final.get("skipped"))
        label: str | None = None
        if not unlabelled:
            _check_label(session, final)
            label = final["gold_label"]
        row = dict(session.rows[case.listing_end_id])
        row.update(
            gold_label=label,
            text_states=(final.get("text_states") or label) if label else None,
            gold_class=class_of(label) if label else None,
            relied_on=final.get("relied_on"),
            passage_ref=final.get("passage_ref"),
            confidence=final.get("confidence"),
            seconds_spent=final.get("seconds_spent"),
            idle=final.get("idle"),
            labeled_at=final.get("labeled_at"),
            seed_case=case.seed_case,
            in_random_draw=case.in_random_draw,
            unlabelled=unlabelled,
        )
        out.append(row)
        splits.append(case.split)
        if case.split == "pilot" and case.in_random_draw and label not in (None, "unresolved"):
            scorable += 1
    metadata = {
        "exclusion_sha256": session.exclusion_sha256,
        "frame_dataset_id": str(session.frame.dataset_id),
        "frame_sha256": session.frame.sha256,
        "seed": str(session.seed),
    }
    export = _parquet_bytes(pl.DataFrame(out), metadata)
    sha = hashlib.sha256(export).hexdigest()
    split_bytes = (json.dumps({"splits": splits}) + "\n").encode("utf-8")
    export_path = datafiles.gold_path(session.settings, sha)
    split_path = datafiles.gold_splits_path(session.settings, sha)
    _write_once(export_path, export)
    _write_once(split_path, split_bytes)
    days = {
        s: [
            _accepted_day(r["form25_accepted_at"])
            for r, x in zip(out, splits, strict=True)
            if x == s
        ]
        for s in ("dev", "pilot")
    }
    every = [d for v in days.values() for d in v]
    try:
        with connect() as conn:
            record = register_dataset(
                conn,
                name=DATASET_NAME,
                version=sha[:12],
                path=str(export_path),
                sha256=sha,
                event_start=min(every),
                event_end=max(every),
                n_rows=len(out),
                event_column="form25_accepted_at",
                split_path=str(split_path),
                split_sha256=hashlib.sha256(split_bytes).hexdigest(),
                split_spans={s: _span(v) for s, v in days.items() if v},
                sealed_splits=("pilot",),
                sealed_periods=(session.pilot_period,),
                locked=True,
                seed=session.seed,
                note=f"exclusion sha256 {session.exclusion_sha256}",
            )
    except StoreLockedError as exc:
        return LockResult("busy", sha, scorable, message=f"store busy: {exc}")
    lock = {
        "dataset_id": record.dataset_id,
        "sha256": sha,
        "scorable_pilot": scorable,
        "locked_at": _now(),
    }
    session.lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return LockResult("locked", sha, scorable, record.dataset_id)

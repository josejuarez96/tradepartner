"""The desk-read exposure backfill (plan T359, #3116; research-registry spec amendment
2026-10-31, "Backfill").

A reviewed TOML manifest lists each past desk-read launch that read returns outside
the backtester, with the evidence behind every declared value (issue, kill-line
comment, file hashes, the date it was seen). This module reads it, pins every file it
names by SHA-256 before anything is written, and records each entry through
`research.record_exposure`, the only writer, so every refusal and every computed column
(`kill_line_basis`, `probe_verified`, `overlaps_holdout`, `post_boundary`, `synthetic`)
is the writer's own. Nothing here asserts a computed value.

* **Dry run.** `plan_backfill` runs `research.preview_exposure` for each entry on any
  connection, read-only included, and writes nothing: it lists the rows a write would
  add, the existing rows a write would return, and what each family's N would gain.
* **Write.** `apply_backfill` refuses unless the release the manifest names is the
  store's open data release (runbook `docs/runbooks/data-releases.md`, rule 1), plans
  the whole batch first, so any refusal writes no row, and then records every entry
  inside one transaction: all or nothing.
* **Idempotent.** A launch already recorded (same `candidate_ref`, probe hash and
  `read_at`) returns its row and adds none, so a second run adds no row and no N.
* **No back-stamp.** Each record's `known_at` is the write time; `read_at` is the
  declared past launch. A stored backtest result keeps the N it was written with.

Each record's `note` carries the entry's own note, how its `read_at` is known
(`measured`, `bound`, or `unverified`, which the note spells UNVERIFIED as the spec
asks) and the manifest's name, hash and release.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import duckdb

from tradepartner.config import Settings
from tradepartner.store import research
from tradepartner.store.registry import open_release

MANIFEST_SCHEMA: Final = 1
READ_AT_BASES: Final = ("measured", "bound", "unverified")
_RELEASE_NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")

_HEADER_FIELDS: Final = frozenset({"schema", "release", "title", "reviewed_in", "note"})
_REQUIRED: Final = (
    "key",
    "candidate_ref",
    "source",
    "issue_url",
    "kill_line_url",
    "kill_line_file",
    "kill_line_sha256",
    "kill_line_posted_at",
    "read_at",
    "read_at_basis",
    "read_at_evidence",
    "window_start",
    "window_end",
    "n_configurations",
    "verdict",
    "desk_read_path",
    "date_seen",
    "evidence",
)
_OPTIONAL: Final = (
    "family",
    "probe_path",
    "probe_sha256",
    "probe_unavailable_reason",
    "inputs",
    "pins",
    "audit_url",
    "holdout_reason",
    "note",
)
_FIELDS: Final = frozenset(_REQUIRED + _OPTIONAL)


class BackfillRefused(research.ResearchError):
    """The manifest, a file it pins, or the store refuses the backfill; nothing written."""


@dataclass(frozen=True)
class RawManifest:
    """The manifest as text: its header and each entry's fields, checked for shape only
    (no file read), so a test can check the committed manifest off the owner's Mac."""

    source_file: Path
    sha256: str
    release: str
    title: str
    entries: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class BackfillEntry:
    """One launch: the writer's input plus the evidence the manifest gives for it."""

    key: str
    spec: research.ExposureInput
    issue_url: str
    date_seen: date
    read_at_basis: str
    read_at_evidence: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class BackfillManifest:
    """A manifest whose every named file hashed to its pinned digest when loaded."""

    source_file: Path
    sha256: str
    release: str
    title: str
    entries: tuple[BackfillEntry, ...]


@dataclass(frozen=True)
class PlannedEntry:
    """An entry and the row the writer would insert (`row`), or the row it already has
    (`existing`)."""

    entry: BackfillEntry
    row: Mapping[str, Any] | None
    existing: research.ExposureRecord | None


@dataclass(frozen=True)
class BackfillPlan:
    """What a write would do: `new` entries with their computed rows, `existing` ones
    with their rows, and per family the N a write would add (non-synthetic `store`
    rows that spend no holdout, as `research.family_exposure_count` sums)."""

    new: tuple[PlannedEntry, ...]
    existing: tuple[PlannedEntry, ...]
    n_added: Mapping[str, int]


@dataclass(frozen=True)
class BackfillResult:
    """The records a write added and the ones it found already there."""

    written: tuple[research.ExposureRecord, ...]
    existing: tuple[research.ExposureRecord, ...]


def _refuse(where: str, message: str) -> BackfillRefused:
    return BackfillRefused(f"{where}: {message}")


def read_manifest_text(path: Path) -> RawManifest:
    """Parse `path` and check its header and every entry's field names and required
    fields, reading no other file. Raises `BackfillRefused`."""
    try:
        data = path.read_bytes()
        doc = tomllib.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise BackfillRefused(f"manifest {str(path)!r} cannot be read: {exc}") from exc
    header = doc.get("manifest")
    if not isinstance(header, dict):
        raise BackfillRefused("manifest: no [manifest] table")
    if unknown := set(header) - _HEADER_FIELDS:
        raise _refuse("manifest", f"unknown field {sorted(unknown)}")
    if header.get("schema") != MANIFEST_SCHEMA:
        raise _refuse("manifest", f"schema {header.get('schema')!r} is not {MANIFEST_SCHEMA}")
    release = header.get("release")
    if not isinstance(release, str) or not _RELEASE_NAME.match(release):
        raise _refuse("manifest", f"release {release!r} is not a release name")
    title = header.get("title")
    if not isinstance(title, str) or not title.strip():
        raise _refuse("manifest", "needs a title")
    if unknown_tables := set(doc) - {"manifest", "exposure"}:
        raise _refuse("manifest", f"unknown table {sorted(unknown_tables)}")
    raw = doc.get("exposure")
    if not isinstance(raw, list) or not raw:
        raise _refuse("manifest", "lists no [[exposure]]")
    entries: list[Mapping[str, Any]] = []
    keys: set[str] = set()
    for i, entry in enumerate(raw):
        where = f"exposure {entry.get('key', i)!r}"
        if unknown := set(entry) - _FIELDS:
            raise _refuse(where, f"unknown field {sorted(unknown)}")
        if missing := [f for f in _REQUIRED if f not in entry]:
            raise _refuse(where, f"missing {missing}")
        if entry["key"] in keys:
            raise _refuse(where, "the key repeats an earlier entry")
        keys.add(entry["key"])
        entries.append(entry)
    return RawManifest(
        source_file=path,
        sha256=sha256(data).hexdigest(),
        release=release,
        title=title,
        entries=tuple(entries),
    )


def _path(base: Path, value: Any, where: str, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise _refuse(where, f"{field} is not a path")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _sha(value: Any, where: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.match(value):
        raise _refuse(where, f"{field} {value!r} is not a lowercase SHA-256 hex digest")
    return value


def _pinned(path: Path, digest: str, where: str, what: str) -> None:
    try:
        actual = sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _refuse(where, f"{what} {str(path)!r} cannot be read: {exc}") from exc
    if actual != digest:
        raise _refuse(where, f"{what} {str(path)!r} hashes to {actual}, not the pinned {digest}")


def _when(value: Any, where: str, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise _refuse(where, f"{field} {value!r} is not an ISO timestamp") from exc
    if not isinstance(value, datetime):
        raise _refuse(where, f"{field} {value!r} is not a timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise _refuse(where, f"{field} {value.isoformat()} is naive; give it a UTC offset")
    return value.astimezone(UTC)


def _day(value: Any, where: str, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _refuse(where, f"{field} {value!r} is not a YYYY-MM-DD date")


def _text(value: Any, where: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _refuse(where, f"{field} needs non-blank text")
    return value.strip()


def _optional_text(entry: Mapping[str, Any], field: str, where: str) -> str | None:
    return _text(entry[field], where, field) if field in entry else None


def _entry(base: Path, entry: Mapping[str, Any]) -> BackfillEntry:
    key = _text(entry["key"], "exposure", "key")
    where = f"exposure {key!r}"
    basis = entry["read_at_basis"]
    if basis not in READ_AT_BASES:
        raise _refuse(where, f"read_at_basis {basis!r} is not one of {READ_AT_BASES}")
    evidence = entry["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise _refuse(where, "evidence lists nothing")
    kill_line = _path(base, entry["kill_line_file"], where, "kill_line_file")
    kill_sha = _sha(entry["kill_line_sha256"], where, "kill_line_sha256")
    _pinned(kill_line, kill_sha, where, "kill-line copy")
    probe: Path | None = None
    probe_sha: str | None = None
    if "probe_path" in entry or "probe_sha256" in entry:
        probe = _path(base, entry.get("probe_path"), where, "probe_path")
        probe_sha = _sha(entry.get("probe_sha256"), where, "probe_sha256")
        _pinned(probe, probe_sha, where, "probe")
    inputs: list[Path] = []
    for item in entry.get("inputs", []):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise _refuse(where, f"input {item!r} needs exactly a path and a sha256")
        path = _path(base, item["path"], where, "input path")
        _pinned(path, _sha(item["sha256"], where, "input sha256"), where, "input")
        inputs.append(path)
    pins = entry.get("pins", {})
    if not isinstance(pins, dict):
        raise _refuse(where, "pins is not a table")
    configurations = entry["n_configurations"]
    if not isinstance(configurations, int) or isinstance(configurations, bool):
        raise _refuse(where, f"n_configurations {configurations!r} is not an integer")
    spec = research.ExposureInput(
        candidate_ref=_text(entry["candidate_ref"], where, "candidate_ref"),
        source=_text(entry["source"], where, "source"),
        kill_line_url=_text(entry["kill_line_url"], where, "kill_line_url"),
        kill_line_file=kill_line,
        kill_line_posted_at=_when(entry["kill_line_posted_at"], where, "kill_line_posted_at"),
        read_at=_when(entry["read_at"], where, "read_at"),
        window_start=_day(entry["window_start"], where, "window_start"),
        window_end=_day(entry["window_end"], where, "window_end"),
        n_configurations=configurations,
        verdict=_text(entry["verdict"], where, "verdict"),
        desk_read_path=_text(entry["desk_read_path"], where, "desk_read_path"),
        family=_optional_text(entry, "family", where),
        probe_path=probe,
        probe_sha256=probe_sha,
        probe_unavailable_reason=_optional_text(entry, "probe_unavailable_reason", where),
        inputs=tuple(inputs),
        pins={str(k): str(v) for k, v in pins.items()},
        audit_url=_optional_text(entry, "audit_url", where),
        holdout_reason=_optional_text(entry, "holdout_reason", where),
        note=_optional_text(entry, "note", where),
    )
    return BackfillEntry(
        key=key,
        spec=spec,
        issue_url=_text(entry["issue_url"], where, "issue_url"),
        date_seen=_day(entry["date_seen"], where, "date_seen"),
        read_at_basis=basis,
        read_at_evidence=_text(entry["read_at_evidence"], where, "read_at_evidence"),
        evidence=tuple(_text(e, where, "evidence") for e in evidence),
    )


def load_manifest(path: Path) -> BackfillManifest:
    """Read `path` (relative file paths resolve against its directory, `~` against the
    home directory) and hash every kill-line copy, probe and input it names against its
    pinned digest. Refuses a repeated key or launch. Raises `BackfillRefused`."""
    raw = read_manifest_text(path)
    entries = tuple(_entry(path.parent, e) for e in raw.entries)
    launches: set[tuple[str, str, datetime]] = set()
    for entry in entries:
        launch = (entry.spec.candidate_ref, entry.spec.probe_sha256 or "", entry.spec.read_at)
        if launch in launches:
            raise _refuse(f"exposure {entry.key!r}", "the launch repeats an earlier entry")
        launches.add(launch)
    return BackfillManifest(
        source_file=path, sha256=raw.sha256, release=raw.release, title=raw.title, entries=entries
    )


def _with_provenance(manifest: BackfillManifest, entry: BackfillEntry) -> research.ExposureInput:
    basis = "UNVERIFIED" if entry.read_at_basis == "unverified" else entry.read_at_basis
    parts = [
        *([entry.spec.note] if entry.spec.note else []),
        f"read_at {basis}: {entry.read_at_evidence}",
        f"backfill {manifest.source_file.name} sha256 {manifest.sha256[:12]}, "
        f"release {manifest.release}, entry {entry.key}",
    ]
    return replace(entry.spec, note="; ".join(parts))


def _counts(row: Mapping[str, Any]) -> bool:
    return row["source"] == "store" and not row["synthetic"] and not row["overlaps_holdout"]


def plan_backfill(
    conn: duckdb.DuckDBPyConnection,
    manifest: BackfillManifest,
    *,
    settings: Settings | None = None,
    repo_dir: Path | None = None,
    recorded_by: str = "owner",
) -> BackfillPlan:
    """The dry run: every entry through `research.preview_exposure`, writing nothing.
    Raises `BackfillRefused` naming the first entry the writer would refuse."""
    new: list[PlannedEntry] = []
    existing: list[PlannedEntry] = []
    added: dict[str, int] = {}
    for entry in manifest.entries:
        try:
            preview = research.preview_exposure(
                conn,
                _with_provenance(manifest, entry),
                recorded_by,
                settings=settings,
                repo_dir=repo_dir,
            )
        except research.ResearchError as exc:
            raise _refuse(f"exposure {entry.key!r}", str(exc)) from exc
        if preview.existing is not None:
            existing.append(PlannedEntry(entry, None, preview.existing))
            continue
        assert preview.row is not None
        row = dict(preview.row)
        row["exposure_id"] = int(row["exposure_id"]) + len(new)  # ids the batch would take
        new.append(PlannedEntry(entry, row, None))
        if _counts(row):
            family = str(row["family"])
            added[family] = added.get(family, 0) + int(row["n_configurations"])
    return BackfillPlan(new=tuple(new), existing=tuple(existing), n_added=added)


def release_refusal(conn: duckdb.DuckDBPyConnection, release: str) -> str | None:
    """Why the backfill may not write under `release`, or None when it is the store's
    open data release."""
    current = open_release(conn)
    if current is not None and current.name == release:
        return None
    found = "no release is open" if current is None else f"release {current.name!r} is open"
    return (
        f"no open data release named {release!r} ({found}); open it first with "
        "`tradepartner decision data-release open`"
    )


def apply_backfill(
    conn: duckdb.DuckDBPyConnection,
    manifest: BackfillManifest,
    *,
    release: str,
    settings: Settings | None = None,
    recorded_by: str,
    repo_dir: Path | None = None,
    own_transaction: bool = True,
) -> BackfillResult:
    """Record every entry inside one transaction, under `release`, which must be the
    release the manifest names and the store's open one. Plans first, so any refusal
    writes nothing; a failure part-way rolls the batch back: in its own transaction,
    or, with `own_transaction=False`, in the caller's (`store.db.open_for_write` holds
    one for its whole scope and rolls it back when the error leaves it).
    Raises `BackfillRefused`."""
    if release != manifest.release:
        raise BackfillRefused(f"the manifest names release {manifest.release!r}, not {release!r}")
    if (refusal := release_refusal(conn, release)) is not None:
        raise BackfillRefused(refusal)
    plan_backfill(conn, manifest, settings=settings, repo_dir=repo_dir, recorded_by=recorded_by)
    written: list[research.ExposureRecord] = []
    existing: list[research.ExposureRecord] = []
    own = own_transaction
    if own:
        conn.execute("BEGIN TRANSACTION")
    try:
        for entry in manifest.entries:
            before = _row_count(conn)
            record = research.record_exposure(
                conn,
                _with_provenance(manifest, entry),
                recorded_by,
                settings=settings,
                repo_dir=repo_dir,
            )
            (written if _row_count(conn) > before else existing).append(record)
        if own:
            conn.execute("COMMIT")
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise
    return BackfillResult(written=tuple(written), existing=tuple(existing))


def _row_count(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(*) FROM research_exposures").fetchone()
    assert row is not None
    return int(row[0])


def plan_lines(plan: BackfillPlan) -> Sequence[str]:
    """The dry run's report, one line per entry, then the N each family would gain."""
    lines = []
    for item in plan.new:
        row = item.row
        assert row is not None
        lines.append(
            f"new  {item.entry.key}  {row['candidate_ref']}  {row['source']}  "
            f"{row['family'] or '-'}  read_at {row['read_at'].isoformat()}  "
            f"configs {row['n_configurations']}  {row['verdict']}  {row['kill_line_basis']}  "
            f"probe {'verified' if row['probe_verified'] else 'unverified'}  "
            f"holdout {'overlap' if row['overlaps_holdout'] else 'no'}  "
            f"{'synthetic' if row['synthetic'] else 'real'}"
        )
    for item in plan.existing:
        assert item.existing is not None
        lines.append(f"have {item.entry.key}  exposure {item.existing.exposure_id}")
    added = ", ".join(f"{f} +{n}" for f, n in sorted(plan.n_added.items())) or "none"
    lines.append(f"{len(plan.new)} new, {len(plan.existing)} already recorded; N added: {added}")
    return lines

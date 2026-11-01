"""The existing-cache migration runner, move and backup classes (plan T361a, #2765,
and T361b, #2775; data-foundation spec amendment 2026-10-31, #2743, items 2, 3, 4,
6 and 7). Restore is a later slice (T361c).

`apply_migration` moves the files T360's planner classes as `move` out of
`edgar.cache_dir` into the raw archive by content hash, with no SEC request and
no store row. Per file: re-hash the source, append an **intent** line to the run's
fsynced journal, rename it under its hash (`raw_archive.adopt_file`), then append a
**settled** line. After the moves the run asks T346 to evict the confirmed
uploads, writes the content-addressed **migration manifest**, appends one line per
moved path to the **tombstone index** `_archived/index.jsonl`, and closes the
journal. Journal, manifest and index stay on the laptop and are never evicted.

**Backups (T361b).** After the moves and their evictions, each backup-class file
(`stamps/v1/`, its restamp manifests and backups) is copied, never moved: read,
hashed, an intent line journaled, written through `archive_body` (floor-checked
against the file's bytes, atomic, deduplicated by hash), then the source is
re-hashed. A source rewritten meanwhile is re-read and retried once, then
reported (`changed`); the superseded copy stays, a valid body for its own hash,
since no archive body is ever deleted. The copies are then confirmed and evicted
like the moves (`evicted`, `pending_upload`, or `backed_up` when eviction failed).
Below the floor `RawArchiveFloorError` stops the run with no partial file and a
rerun resumes at that file. A file whose hash the archive already holds is only
confirmed. The manifest lists every backup file with its hash, so each run is a
complete snapshot. A run that moves nothing and meets no new backup hash changes
nothing. No backup source is moved or unlinked.

**Refusals.** The archive root and the cache must share a filesystem; otherwise
the run refuses before any change (`MigrationRefusedError`). The move phase never
checks the floor (a rename adds no bytes). The manifest is a small floor-checked
archive write: below the floor it raises `RawArchiveFloorError` after the moves
and evictions, and a rerun completes it.

**Duplicates.** A source whose hash the archive already holds is unlinked only if
the archived body is local and re-hashes equal now and the source re-hashes equal
again (`duplicate`); otherwise it stays local and is reported (`duplicate_kept`).
No archived body is ever unlinked.

**Resume.** The journal is `_archived/journal.jsonl` until the run closes, then
`journal-<run>.jsonl`. A rerun with an open journal continues that run: an intent
with its source present resumes at the re-hash; one with the source gone and the
body archived is marked moved; one with neither stops the run loudly
(`MigrationStoppedError`). A changed source is skipped and reported. The manifest
is built from the journal alone, so it is the same digest however often the run
was cut. A run with nothing left to move changes nothing but a closed journal.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradepartner import diskfloor
from tradepartner.config import TOMBSTONE_DIR, Settings
from tradepartner.store.raw_archive import (
    ICloudItems,
    adopt_file,
    archive_body,
    archive_manifest,
    evict_uploaded,
    is_archived,
    local_body_matches,
    same_filesystem,
)
from tradepartner.store.raw_archive_config import migration_config
from tradepartner.store.raw_archive_plan import (
    CacheFile,
    MigrationClass,
    PlannedFile,
    current_versions,
    plan_migration,
)

JOURNAL = "journal.jsonl"
INDEX = "index.jsonl"
_CHUNK = 1 << 20


class MigrationRefusedError(RuntimeError):
    """The run refuses before any change (a cross-filesystem root)."""


class MigrationStoppedError(RuntimeError):
    """The journal names a file that is neither in the cache nor in the archive."""


@dataclass(frozen=True)
class MigrationResult:
    """One `apply_migration` run. `states` maps each moved-class path to
    `evicted`, `pending_upload` (local and counted), `moved` (archived, eviction
    failed), `duplicate`, `duplicate_kept` or `changed`."""

    run_id: str
    manifest_sha256: str
    states: dict[str, str] = field(default_factory=dict)
    backup_states: dict[str, str] = field(default_factory=dict)

    def count(self, state: str) -> int:
        """How many moved-class paths ended in `state`."""
        return sum(1 for s in self.states.values() if s == state)

    def backup_count(self, state: str) -> int:
        """How many backup-class paths ended in `state` (`evicted`,
        `pending_upload`, `backed_up` or `changed`)."""
        return sum(1 for s in self.backup_states.values() if s == state)


def _step(_name: str) -> None:
    """A no-op at each step boundary; tests replace it to cut a run there."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """The records of an append-only JSON-lines file; a torn line is skipped."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn line (a cut write); the lines after it still count
    return out


def _last_byte(path: Path) -> bytes:
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        if not size:
            return b""
        handle.seek(size - 1)
        return handle.read(1)


def _append(path: Path, *records: dict[str, Any]) -> None:
    """Append and fsync the records (and the directory, when the file is new)."""
    fresh = not path.exists()
    with path.open("a", encoding="utf-8") as handle:
        if not fresh and _last_byte(path) not in (b"", b"\n"):
            handle.write("\n")  # end a torn line so the next record starts clean
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if fresh:
        _fsync_dir(path.parent)


def _listing(cache_dir: Path) -> Iterator[CacheFile]:
    for dirpath, dirnames, filenames in os.walk(cache_dir):
        if Path(dirpath) == cache_dir:
            dirnames[:] = [d for d in dirnames if d != TOMBSTONE_DIR]
        for name in filenames:
            path = Path(dirpath) / name
            info = os.lstat(path)
            if stat.S_ISREG(info.st_mode):
                yield CacheFile(
                    path=path.relative_to(cache_dir).as_posix(),
                    size_bytes=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                )


def _settle(
    settings: Settings,
    cache_dir: Path,
    icloud: ICloudItems,
    intent: dict[str, Any],
    verified: str | None,
) -> str:
    """Carry one intent to its end state (module docstring: Duplicates, Resume)."""
    source = cache_dir / intent["path"]
    sha = intent["sha256"]
    if not os.path.lexists(source):
        if is_archived(settings, sha):
            return "moved"
        raise MigrationStoppedError(
            f"{intent['path']}: in the journal, but neither in the cache nor in the archive"
        )
    if (verified or _sha256_file(source)) != sha:
        return "changed"
    try:
        adopted = adopt_file(settings, source, sha, size=intent["bytes"])
    except OSError:
        return "changed"
    if adopted.created:
        source.unlink(missing_ok=True)
        _step("rename")
        return "moved"
    _step("rename")
    if local_body_matches(settings, sha, icloud) and _sha256_file(source) == sha:
        source.unlink()
        return "duplicate"
    return "duplicate_kept"


def apply_migration(
    settings: Settings,
    cache_dir: Path,
    icloud: ICloudItems,
    *,
    run_id: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> MigrationResult:
    """Move the cache's move-class files into the archive (module docstring).
    Raises `MigrationRefusedError`, `MigrationStoppedError`, T346's archive errors
    and T360's `MigrationPlanRefusedError`; each leaves the journal resumable."""
    if not same_filesystem(settings, cache_dir):
        raise MigrationRefusedError(
            "raw archive root and edgar.cache_dir are on different filesystems; nothing was changed"
        )
    plan = plan_migration(
        _listing(cache_dir),
        versions=current_versions(),
        config=migration_config(settings),
        known_hashes=(),
        free_bytes=diskfloor.free_bytes(cache_dir),
    )
    keep_dir = cache_dir / TOMBSTONE_DIR
    keep_dir.mkdir(exist_ok=True)
    journal = keep_dir / JOURNAL
    records = _read_lines(journal)
    start = next((r for r in records if r["op"] == "start"), None)
    if start is None:
        start = {"op": "start", "run": run_id or uuid.uuid4().hex, "at": now().isoformat()}
        _append(journal, start)
        records = [start]
    run = start["run"]
    intents = {r["path"]: r for r in records if r["op"] == "intent"}
    states = {r["path"]: r["state"] for r in records if r["op"] == "settled"}

    def settled(path: str, state: str) -> None:
        _append(journal, {"op": "settled", "path": path, "state": state})
        states[path] = state

    for path, intent in list(intents.items()):  # an interrupted run's open intents
        if path not in states:
            settled(path, _settle(settings, cache_dir, icloud, intent, None))
    for planned in plan.files:
        if planned.klass is MigrationClass.MOVE and planned.path not in intents:
            sha = _sha256_file(cache_dir / planned.path)
            intent = _intent(planned, sha)
            _append(journal, intent)
            intents[planned.path] = intent
            _step("intent")
            settled(planned.path, _settle(settings, cache_dir, icloud, intent, sha))

    backups = [f for f in plan.files if f.klass is MigrationClass.BACKUP]
    if intents and not any(r["op"] == "evicted" for r in _read_lines(journal)):
        hashes = list(
            dict.fromkeys(
                intents[p]["sha256"] for p, st in states.items() if st in {"moved", "duplicate"}
            )
        )
        report = evict_uploaded(settings, hashes, icloud) if hashes else None
        if report is not None:
            for path in [p for p, st in states.items() if st in {"moved", "duplicate"}]:
                sha = intents[path]["sha256"]
                if sha in report.evicted or sha in report.already_evicted:
                    states[path] = "evicted"
                elif sha in report.pending_upload:
                    states[path] = "pending_upload"
        _append(journal, {"op": "evicted", "states": states})
    elif intents:
        states = next(r for r in _read_lines(journal) if r["op"] == "evicted")["states"]
    _step("evict")

    bsettled = _backup_phase(settings, cache_dir, journal, backups)
    fresh = any(r["new"] for r in _read_lines(journal) if r["op"] == "bsettled")
    if not intents and not fresh:  # nothing to move or copy: leave no trace
        if any(r["op"] not in {"start", "bintent", "bsettled"} for r in _read_lines(journal)):
            raise MigrationStoppedError("the journal holds records but names no file")
        journal.unlink()
        return MigrationResult(run_id=run, manifest_sha256="")

    bstates = {p: r["state"] for p, r in bsettled.items()}
    bevicted = next((r for r in _read_lines(journal) if r["op"] == "bevicted"), None)
    if bevicted is None:
        held = {p: r["sha256"] for p, r in bsettled.items() if r["state"] == "backed_up"}
        report = (
            evict_uploaded(settings, list(dict.fromkeys(held.values())), icloud) if held else None
        )
        if report is not None:
            for path, sha in held.items():
                if sha in report.evicted or sha in report.already_evicted:
                    bstates[path] = "evicted"
                elif sha in report.pending_upload:
                    bstates[path] = "pending_upload"
        _append(journal, {"op": "bevicted", "states": bstates})
    else:
        bstates = bevicted["states"]
    _step("backup_evict")

    records = _read_lines(journal)
    finish = next((r for r in records if r["op"] == "finish"), None)
    if finish is None:
        finish = {"op": "finish", "at": now().isoformat()}
        _append(journal, finish)
    manifest = next((r for r in records if r["op"] == "manifest"), None)
    if manifest is None:
        body = _manifest_body(
            run, start["at"], finish["at"], intents, states, bsettled, bstates, backups
        )
        digest = archive_manifest(settings, body).sha256
        manifest = {"op": "manifest", "digest": digest}
        _append(journal, manifest)
    _step("manifest")

    index = keep_dir / INDEX
    done = {r["path"] for r in _read_lines(index) if r.get("run") == run}
    lines = [
        {
            "path": p,
            "bytes": i["bytes"],
            "sha256": i["sha256"],
            "mtime_ns": i["mtime_ns"],
            "run": run,
            "manifest": manifest["digest"],
        }
        for p, i in intents.items()
        if p not in done and states[p] not in {"changed", "duplicate_kept"}
    ]
    if lines:
        _append(index, *lines)
    _append(journal, {"op": "done"})
    os.replace(journal, keep_dir / f"journal-{run}.jsonl")
    _fsync_dir(keep_dir)
    return MigrationResult(
        run_id=run, manifest_sha256=manifest["digest"], states=states, backup_states=bstates
    )


def _manifest_body(
    run: str,
    started: str,
    finished: str,
    intents: dict[str, dict[str, Any]],
    states: dict[str, str],
    bsettled: dict[str, dict[str, Any]],
    bstates: dict[str, str],
    backups: list[PlannedFile],
) -> bytes:
    files = [
        {
            "path": p,
            "bytes": i["bytes"],
            "sha256": i["sha256"],
            "mtime_ns": i["mtime_ns"],
            "class": "move",
            "state": states[p],
        }
        for p, i in intents.items()
    ]
    files += [
        {
            "path": f.path,
            "bytes": bsettled[f.path]["bytes"] if f.path in bsettled else f.size_bytes,
            "sha256": bsettled[f.path]["sha256"] if f.path in bsettled else None,
            "mtime_ns": f.mtime_ns,
            "class": "backup",
            "state": bstates.get(f.path, "backup_pending"),
        }
        for f in backups
    ]
    doc = {"run_id": run, "started_at": started, "finished_at": finished, "files": files}
    return json.dumps(doc, sort_keys=True).encode()


def _backup_one(
    settings: Settings, journal: Path, path: str, source: Path, prior: set[tuple[str, str]]
) -> dict[str, Any]:
    """Copy one backup file into the archive; the settled record (module docstring:
    Backups). A source that is gone, or still changing after one retry, is `changed`."""
    for _attempt in range(2):
        try:
            body = source.read_bytes()
        except FileNotFoundError:
            break
        sha = hashlib.sha256(body).hexdigest()
        # new: this run wrote the body (also when a cut run already did, see `prior`)
        fresh = (path, sha) in prior or not is_archived(settings, sha)
        _append(
            journal,
            {"op": "bintent", "path": path, "sha256": sha, "bytes": len(body), "new": fresh},
        )
        _step("backup_intent")
        if fresh:
            archive_body(settings, body)  # the floor error stops the run, resumable
        _step("backup_copy")
        try:
            stable = _sha256_file(source) == sha
        except FileNotFoundError:
            break
        if stable:
            return {"state": "backed_up", "sha256": sha, "bytes": len(body), "new": fresh}
    return {"state": "changed", "sha256": None, "bytes": 0, "new": False}


def _backup_phase(
    settings: Settings, cache_dir: Path, journal: Path, backups: list[PlannedFile]
) -> dict[str, dict[str, Any]]:
    """Copy every backup file the journal has not settled yet; return all settled."""
    records = _read_lines(journal)
    done = {r["path"]: r for r in records if r["op"] == "bsettled"}
    prior = {(r["path"], r["sha256"]) for r in records if r["op"] == "bintent" and r["new"]}
    for planned in backups:
        if planned.path in done:
            continue
        record = _backup_one(settings, journal, planned.path, cache_dir / planned.path, prior)
        record = {"op": "bsettled", "path": planned.path, **record}
        _append(journal, record)
        done[planned.path] = record
    return done


def _intent(planned: PlannedFile, sha: str) -> dict[str, Any]:
    return {
        "op": "intent",
        "path": planned.path,
        "bytes": planned.size_bytes,
        "mtime_ns": planned.mtime_ns,
        "sha256": sha,
    }

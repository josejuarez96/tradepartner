"""Owner store backup: a verified, durable copy of the DuckDB file beside the store.

`backup_store` refuses while another process has the store open for writing and
holds a read-only connection for the whole copy (so no writer can start
meanwhile). It first writes `<copy>.manifest.json.partial`, which says what the
files of this run are (`"complete": false`, and the `reason` if any); copies the
file and its `.wal` (if any) to temporary names and fsyncs them; checks each copy
against its source by SHA-256; renames them to `<store>.bak-YYYYMMDD-HHMM` (UTC);
opens the copy read-only once and runs one query; then writes
`<copy>.manifest.json` with the git commit, the schema version, the sizes and
the SHA-256 of each copied file. Every written file and the directory are
fsynced before it returns. It never overwrites.

The retention sweep may remove only (a) old copies whose manifest says
`reason: "pre_migration"`, together with their WAL and manifest, and (b) what a
crashed pre-migration run left: the files beside a partial manifest that says
`reason: "pre_migration"` and is older than `store.orphan_backup_min_age_hours`.
It never removes an unmarked owner copy, nor a `.partial` file with no such
partial manifest beside it. A failed copy removes only its own files.
Scheduling is not part of this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import duckdb

from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import StoreLockedError, open_read_only

STAMP_FORMAT = "%Y%m%d-%H%M"
_CHUNK = 1 << 20
PRE_MIGRATION: Literal["pre_migration"] = "pre_migration"


class BackupError(RuntimeError):
    """The backup was refused or failed; nothing was left half-made."""


@dataclass(frozen=True)
class BackupResult:
    """Where the copy went and what was recorded about it."""

    copy_path: Path
    wal_copy_path: Path | None
    manifest_path: Path
    git_commit: str
    schema_version: int | None
    store_bytes: int
    wal_bytes: int | None
    store_sha256: str
    wal_sha256: str | None


def _schema_version(conn: duckdb.DuckDBPyConnection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except duckdb.Error:
        return None
    return int(row[0]) if row is not None and row[0] is not None else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_synced(path: Path, record: dict[str, object]) -> None:
    with path.open("w") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _copy_checked(source: Path, temp: Path) -> str:
    """Copy `source` to `temp`, fsync it, and return the SHA-256 both share."""
    shutil.copyfile(source, temp)
    _fsync_file(temp)
    sha = _sha256_file(temp)
    if _sha256_file(source) != sha:
        raise BackupError(f"the copy of {source} does not match the original")
    return sha


def _test_open(copy: Path) -> None:
    """Open `copy` read-only and run one query: byte equality with the source does
    not prove DuckDB can open it with its WAL."""
    try:
        conn = duckdb.connect(str(copy), read_only=True)
        try:
            conn.execute("SELECT count(*) FROM duckdb_tables()").fetchone()
        finally:
            conn.close()
    except duckdb.Error as exc:
        raise BackupError(f"the copy cannot be opened: {exc}") from exc


def backup_store(
    settings: Settings,
    *,
    reason: Literal["pre_migration"] | None = None,
    now: datetime | None = None,
    git_commit: str | None = None,
    code_dirty: bool | None = None,
) -> BackupResult:
    """Copy the store and WAL beside it, verify, test-open and fsync the copy.

    Callers wanting the dirty flag from `registry.code_version()` pass neither
    `git_commit` nor `code_dirty`. Supplying only `git_commit` records a null
    dirty flag, as before.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    if git_commit is None:
        git_commit, code_dirty = registry.code_version()
    store = Path(settings.store.path)
    if not store.is_file():
        raise BackupError(f"no store at {store}")
    stamp = now.astimezone(UTC).strftime(STAMP_FORMAT)
    wal = store.with_name(store.name + ".wal")
    target = store.with_name(f"{store.name}.bak-{stamp}")
    wal_target = Path(f"{target}.wal")
    manifest = store.with_name(f"{target.name}.manifest.json")
    for path in (target, wal_target, manifest):
        if path.exists():
            raise BackupError(f"{path} already exists; wait a minute and run again")
    temps = [Path(f"{p}.partial") for p in (target, wal_target, manifest)]
    for temp in temps:
        if temp.exists():
            raise BackupError(f"{temp} is left over from an earlier run; read it, then remove it")
    if reason == PRE_MIGRATION:
        check_backup_disk_space(settings)

    # Every file this run has made so far; a failure removes exactly these.
    made: list[Path] = []
    try:
        with open_read_only(settings) as conn:
            schema_version = _schema_version(conn)
            record: dict[str, object] = {
                "created_at_utc": now.astimezone(UTC).isoformat(),
                "store_file": store.name,
                "complete": False,
            }
            if reason is not None:
                record["reason"] = reason
            # First, so every later file of this run has a sibling that says what it is.
            made.append(temps[2])
            _write_synced(temps[2], record)
            has_wal = wal.is_file()
            made.append(temps[0])
            store_sha256 = _copy_checked(store, temps[0])
            wal_sha256: str | None = None
            if has_wal:
                made.append(temps[1])
                wal_sha256 = _copy_checked(wal, temps[1])
            store_bytes = store.stat().st_size
            wal_bytes = wal.stat().st_size if has_wal else None
            made.append(target)
            temps[0].rename(target)
            if has_wal:
                made.append(wal_target)
                temps[1].rename(wal_target)
            _fsync_dir(store.parent)
            # After the renames, so DuckDB finds `<copy>.wal`; the source is still held.
            _test_open(target)
            record = {
                "created_at_utc": record["created_at_utc"],
                "git_commit": git_commit,
                "code_dirty": code_dirty,
                "schema_version": schema_version,
                "store_file": store.name,
                "store_bytes": store_bytes,
                "wal_bytes": wal_bytes,
                "store_sha256": store_sha256,
                "wal_sha256": wal_sha256,
                "opened_read_only": True,
                "complete": True,
            }
            if reason is not None:
                record["reason"] = reason
            _write_synced(temps[2], record)
            made.append(manifest)
            temps[2].rename(manifest)
            _fsync_dir(store.parent)
    except StoreLockedError as exc:
        raise BackupError(
            f"the store is in use ({exc}); stop the jobs and close other sessions first"
        ) from exc
    except BaseException:
        for path in made:
            path.unlink(missing_ok=True)
        raise

    return BackupResult(
        copy_path=target,
        wal_copy_path=wal_target if has_wal else None,
        manifest_path=manifest,
        git_commit=git_commit,
        schema_version=schema_version,
        store_bytes=store_bytes,
        wal_bytes=wal_bytes,
        store_sha256=store_sha256,
        wal_sha256=wal_sha256,
    )


def check_backup_disk_space(settings: Settings) -> None:
    """Refuse unless free space covers the store, WAL and configured margin."""
    store = Path(settings.store.path)
    if not store.is_file():
        raise BackupError(f"no store at {store}")
    wal = Path(f"{store}.wal")
    needed = store.stat().st_size + settings.store.backup_disk_margin_bytes
    if wal.is_file():
        needed += wal.stat().st_size
    free = shutil.disk_usage(store.parent).free
    if free < needed:
        raise BackupError(f"not enough free disk for backup: need {needed} bytes, have {free}")


def sweep_pre_migration_backups(
    settings: Settings, *, keep: Path, now: datetime | None = None
) -> None:
    """Keep `keep` and the newest other marked copies, up to the configured N, and
    remove what a crashed pre-migration run left once it is older than
    `store.orphan_backup_min_age_hours` at `now` (tz-aware; default the present)."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    store = Path(settings.store.path)
    _sweep_crashed(settings, store, keep=keep, now=now)
    pattern = re.compile(rf"{re.escape(store.name)}\.bak-\d{{8}}-\d{{4}}\.manifest\.json")
    marked: list[tuple[str, Path, Path]] = []
    for manifest in store.parent.glob(f"{store.name}.bak-*.manifest.json"):
        if pattern.fullmatch(manifest.name) is None or manifest.is_symlink():
            continue
        try:
            record = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("reason") != PRE_MIGRATION
            or record.get("store_file") != store.name
        ):
            continue
        copy = manifest.with_name(manifest.name.removesuffix(".manifest.json"))
        wal = Path(f"{copy}.wal")
        if copy == keep or copy.is_symlink() or not copy.is_file() or wal.is_symlink():
            continue
        marked.append((copy.name, copy, manifest))
    marked.sort(reverse=True)
    for _, copy, manifest in marked[settings.store.pre_migration_backups_to_keep - 1 :]:
        copy.unlink(missing_ok=True)
        Path(f"{copy}.wal").unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)


def _sweep_crashed(settings: Settings, store: Path, *, keep: Path, now: datetime) -> None:
    """Remove the files of each unfinished pre-migration run: those beside a
    `<copy>.manifest.json.partial` that names this store and `reason:
    "pre_migration"` and was last written more than the configured age ago. A copy
    with no partial manifest is never touched here (it may be the owner's), nor is
    one that also has a finished manifest (the count rule owns those)."""
    pattern = re.compile(rf"{re.escape(store.name)}\.bak-\d{{8}}-\d{{4}}\.manifest\.json\.partial")
    cutoff = now.astimezone(UTC) - timedelta(hours=settings.store.orphan_backup_min_age_hours)
    for partial in store.parent.glob(f"{store.name}.bak-*.manifest.json.partial"):
        if pattern.fullmatch(partial.name) is None or partial.is_symlink():
            continue
        try:
            record = json.loads(partial.read_text())
            written = datetime.fromtimestamp(partial.stat().st_mtime, tz=UTC)
        except (OSError, ValueError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("reason") != PRE_MIGRATION
            or record.get("store_file") != store.name
            or written >= cutoff
        ):
            continue
        copy = partial.with_name(partial.name.removesuffix(".manifest.json.partial"))
        wal = Path(f"{copy}.wal")
        pieces = [copy, Path(f"{copy}.partial"), wal, Path(f"{wal}.partial")]
        finished = Path(f"{copy}.manifest.json")
        if copy == keep or finished.exists() or any(piece.is_symlink() for piece in pieces):
            continue
        for piece in (*pieces, partial):
            piece.unlink(missing_ok=True)

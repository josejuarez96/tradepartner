"""Owner store backup: a verified copy of the DuckDB file beside the store.

`backup_store` refuses while another process has the store open for writing,
holds a read-only connection for the whole copy (so no writer can start
meanwhile), copies the file and its `.wal` (if any) to temporary names,
checks each copy byte for byte, renames them to
`<store>.bak-YYYYMMDD-HHMM` (UTC), and writes `<copy>.manifest.json` with the
git commit, the schema version and the sizes. It never overwrites. The
retention sweep may remove only old copies whose manifest says
`reason: "pre_migration"`, together with their WAL and manifest; it never
removes an unmarked owner copy or a `.partial` file. A failed copy removes
only its own half-made temporary files. Scheduling is not part of this module.
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb

from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import StoreLockedError, open_read_only

STAMP_FORMAT = "%Y%m%d-%H%M"
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


def _schema_version(conn: duckdb.DuckDBPyConnection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except duckdb.Error:
        return None
    return int(row[0]) if row is not None and row[0] is not None else None


def _copy_checked(source: Path, temp: Path) -> None:
    shutil.copyfile(source, temp)
    if not filecmp.cmp(source, temp, shallow=False):
        raise BackupError(f"the copy of {source} does not match the original")


def backup_store(
    settings: Settings,
    *,
    reason: Literal["pre_migration"] | None = None,
    now: datetime | None = None,
    git_commit: str | None = None,
    code_dirty: bool | None = None,
) -> BackupResult:
    """Copy and verify the store and WAL beside it.

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

    try:
        with open_read_only(settings) as conn:
            schema_version = _schema_version(conn)
            has_wal = wal.is_file()
            _copy_checked(store, temps[0])
            if has_wal:
                _copy_checked(wal, temps[1])
            record = {
                "created_at_utc": now.astimezone(UTC).isoformat(),
                "git_commit": git_commit,
                "code_dirty": code_dirty,
                "schema_version": schema_version,
                "store_file": store.name,
                "store_bytes": store.stat().st_size,
                "wal_bytes": wal.stat().st_size if has_wal else None,
            }
            if reason is not None:
                record["reason"] = reason
            temps[2].write_text(json.dumps(record, indent=2) + "\n")
    except StoreLockedError as exc:
        raise BackupError(
            f"the store is in use ({exc}); stop the jobs and close other sessions first"
        ) from exc
    except BaseException:
        for temp in temps:
            temp.unlink(missing_ok=True)
        raise

    temps[0].rename(target)
    if record["wal_bytes"] is not None:
        temps[1].rename(wal_target)
    temps[2].rename(manifest)
    return BackupResult(
        copy_path=target,
        wal_copy_path=wal_target if record["wal_bytes"] is not None else None,
        manifest_path=manifest,
        git_commit=git_commit,
        schema_version=schema_version,
        store_bytes=int(str(record["store_bytes"])),
        wal_bytes=None if record["wal_bytes"] is None else int(str(record["wal_bytes"])),
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


def sweep_pre_migration_backups(settings: Settings, *, keep: Path) -> None:
    """Keep `keep` and the newest other marked copies, up to the configured N."""
    store = Path(settings.store.path)
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

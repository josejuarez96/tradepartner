"""Owner store backup: a verified copy of the DuckDB file beside the store.

`backup_store` refuses while another process has the store open for writing,
holds a read-only connection for the whole copy (so no writer can start
meanwhile), copies the file and its `.wal` (if any) to temporary names,
checks each copy byte for byte, renames them to
`<store>.bak-YYYYMMDD-HHMM` (UTC), and writes `<copy>.manifest.json` with the
git commit, the schema version and the sizes. It never overwrites and never
deletes an existing file; the only files it removes are its own half-made
temporary copies after a failed check. Scheduling is not part of this module.
"""

from __future__ import annotations

import filecmp
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from tradepartner.config import Settings
from tradepartner.store.db import StoreLockedError, open_read_only

STAMP_FORMAT = "%Y%m%d-%H%M"


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
    settings: Settings, *, now: datetime, git_commit: str, code_dirty: bool | None = None
) -> BackupResult:
    """Copy the store (and its `.wal`) to `<store>.bak-<UTC stamp>` and verify it."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
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

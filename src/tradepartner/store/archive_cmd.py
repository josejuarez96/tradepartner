"""What `tradepartner archive migrate-cache` and `archive restore` do (plan T362,
#2743), kept out of `cli.py` so the CLI names no raw-archive module (the look-ahead
boundary scan, `tests/lookahead/test_raw_archive_boundary.py`).

`migrate_cache` is a dry run unless `apply` is set: it lists `edgar.cache_dir`,
hashes the move and backup classes, asks the archive which hashes it holds and
prints T360's plan, one line per class and folder, with the free-space projection
against the floor. It writes nothing, not even the tombstone folder. `apply` first
needs the store's write lock free (a read-only connection is held for the run, so a
nightly cannot start mid-move), then runs `apply_migration`. Output names cache
paths and counts only, never the archive root or an account.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store import raw_archive_migrate as migrate
from tradepartner.store.db import StoreLockedError, open_read_only
from tradepartner.store.raw_archive import (
    ICloudItems,
    MacICloud,
    RawArchiveICloudError,
    RawArchiveIntegrityError,
    RawArchiveMissingError,
    RawArchiveOfflineError,
    is_archived,
)
from tradepartner.store.raw_archive_config import (
    RawArchiveFloorError,
    RawArchiveUnavailableError,
    migration_config,
    require_archive_root,
)
from tradepartner.store.raw_archive_plan import (
    BACKUP_TREE,
    VERSION_TREES,
    MigrationClass,
    MigrationPlanRefusedError,
    current_versions,
    plan_migration,
)

IcloudFactory = Callable[[Settings], ICloudItems]

#: Every refusal the two commands turn into a plain message and a non-zero exit.
REFUSALS: tuple[type[BaseException], ...] = (
    migrate.MigrationRefusedError,
    migrate.MigrationStoppedError,
    migrate.RestoreRefusedError,
    MigrationPlanRefusedError,
    RawArchiveICloudError,
    RawArchiveIntegrityError,
    RawArchiveMissingError,
    RawArchiveOfflineError,
    RawArchiveFloorError,
    RawArchiveUnavailableError,
    StoreLockedError,
    OSError,
)


def default_icloud(settings: Settings) -> ICloudItems:
    """The real macOS calls, capped at `raw_archive.command_timeout_s`."""
    return MacICloud.from_settings(settings)


def cache_dir_of(settings: Settings) -> Path:
    """`edgar.cache_dir`, resolved as the corpus fetch resolves it."""
    return Path(settings.edgar.cache_dir).resolve()


def _folder(path: str) -> str:
    parts = path.split("/")
    trees = {tree for tree, _m, _n in VERSION_TREES} | {BACKUP_TREE}
    depth = 3 if parts[0] == "corpus" else 2 if parts[0] in trees else 1
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _dry_run(settings: Settings, cache_dir: Path) -> list[str]:
    require_archive_root(settings)
    config, versions = migration_config(settings), current_versions()
    listing = list(migrate._listing(cache_dir))
    first = plan_migration(listing, versions=versions, config=config, known_hashes=(), free_bytes=0)
    # Hash only what can be moved or copied; a kept file's hash is never needed.
    wanted = {f.path for f in first.files if f.klass is not MigrationClass.KEEP}
    listing = [
        replace(f, sha256=_sha256(cache_dir / f.path)) if f.path in wanted else f for f in listing
    ]
    known = {f.sha256 for f in listing if f.sha256 and is_archived(settings, f.sha256)}
    plan = plan_migration(
        listing,
        versions=versions,
        config=config,
        known_hashes=known,
        free_bytes=diskfloor.free_bytes(cache_dir),
    )
    rows: dict[tuple[MigrationClass, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for f in plan.files:
        row = rows[(f.klass, _folder(f.path))]
        row[0] += 1
        row[1] += f.size_bytes
        row[2] += f.duplicate
        row[3] += f.copy
    out = ["migrate-cache dry run: nothing was changed (add --apply to run it)"]
    for (klass, folder), (files, size, dups, copies) in sorted(rows.items()):
        extra = {
            MigrationClass.MOVE: f"  duplicates {dups}",
            MigrationClass.BACKUP: f"  copies {copies}",
            MigrationClass.KEEP: "",
        }[klass]
        out.append(f"{klass.value:<6} {folder}  files {files}  bytes {size}{extra}")
    out.append(
        f"duplicates {len(plan.duplicates)} ({plan.duplicate_bytes} bytes); "
        f"bytes to free {plan.bytes_to_free}; "
        f"transient backup bytes {plan.transient_backup_bytes}"
    )
    for phase in plan.phases:
        note = (
            "no floor on a move"
            if not phase.floor_applies
            else "BELOW THE FLOOR"
            if phase.below_floor
            else "above the floor"
        )
        out.append(f"projected free after {phase.name}: {phase.free_bytes} bytes ({note})")
    out.append(f"floor: {plan.floor_bytes} bytes")
    if plan.backup_refused_at is not None:
        out.append(f"the backup copies stop at {plan.backup_refused_at} (below the floor)")
    return out


@contextmanager
def _store_write_lock_free(settings: Settings) -> Iterator[None]:
    """Refuse (`StoreLockedError`) while another process holds the store's write
    lock; otherwise hold a read-only connection so none starts meanwhile."""
    if not Path(settings.store.path).exists():
        yield
        return
    with open_read_only(settings):
        yield


def migrate_cache(settings: Settings, icloud: ICloudItems, *, apply: bool) -> list[str]:
    """The dry-run plan, or the run's summary with `apply` (module docstring)."""
    cache_dir = cache_dir_of(settings)
    if not apply:
        return _dry_run(settings, cache_dir)
    require_archive_root(settings)
    with _store_write_lock_free(settings):
        result = migrate.apply_migration(settings, cache_dir, icloud)
    moved = ", ".join(
        f"{state} {result.count(state)}"
        for state in (
            "evicted",
            "pending_upload",
            "moved",
            "duplicate",
            "duplicate_kept",
            "changed",
        )
    )
    backed = ", ".join(
        f"{state} {result.backup_count(state)}"
        for state in ("evicted", "pending_upload", "backed_up", "changed")
    )
    return [
        f"migrate-cache applied: run {result.run_id or '-'}",
        f"moved files: {moved}",
        f"backup files: {backed}",
        f"pending_upload (still local, retry later): "
        f"{result.count('pending_upload') + result.backup_count('pending_upload')}",
    ]


def restore(settings: Settings, icloud: ICloudItems, *, path: str, run: str | None) -> list[str]:
    """Copy archived files back to the cache (`raw_archive_migrate.restore_files`)."""
    require_archive_root(settings)
    cache_dir = cache_dir_of(settings)
    result = migrate.restore_files(settings, cache_dir, icloud, path=path, run=run)
    return [f"restored {len(result.restored)} file(s) under {path!r}; the archive keeps its copies"]

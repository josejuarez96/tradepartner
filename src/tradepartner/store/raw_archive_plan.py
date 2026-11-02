"""The existing-cache migration planner (plan T360, #2758; data-foundation spec
amendment 2026-10-31, #2743, items 1, 2, 3 and 6, with the owner's answers).

Pure: `plan_migration` takes a listing of the EDGAR cache's files (relative path,
bytes, mtime and, where the caller hashed it, SHA-256), the current version of
each pinned tree, the migration settings, the archive's known body hashes and the
disk's free bytes. It opens no file, reads no clock and no environment. The
driver (T361) hashes, renames, copies and evicts; the dry run (T362) prints this
plan.

**Classes** (amendment item 1), first rule that matches wins:

1. `_archived/` (the tombstone index) is **kept**, always.
2. Everything under `stamps/` is **backed up** and stays local: `stamps/v1/`,
   the `stamps/v1.restamp-*` manifests and any file beside them. `stamps/` is in
   no version table, so neither a `PARSER_VERSION` bump nor `STAMPS_LAYOUT`
   ever moves it, and an owner-listed path never overrides it.
3. A directory named exactly `v<digits>` directly under a tree of
   `VERSION_TREES` is **moved** when its number differs from that tree's
   constant, read at run time by `current_versions` (`dead_version`).
4. `corpus/departure-reason/raw/` is **moved** (`corpus_raw`).
5. A path equal to or under an entry of the archive's `migrate_extra_paths` key
   is **moved** (`owner_listed`). While `edgar.bulk_stamps_enabled` is on, an
   entry covering `bulk/submissions.zip` refuses the whole plan (owner's
   answer 3, 2026-10-31): that zip is then a nightly input.
6. Anything else is **kept**: the current versions (`current_version`) and every
   path no rule names (`not_named`). An empty per-CIK directory holds no file and
   is not in a listing.

**Duplicates and dedup.** The move phase runs first. A moved file whose hash is
already known (in the archive or earlier in this plan, in path order) is a
duplicate: the driver unlinks it only once the archived body is local and
re-hashes equal (owner's answer 1). A backup file whose hash is known is not
copied again. A file with no hash is never a duplicate and is always copied.

**Space** (items 2 and 3). A rename adds no bytes, so the move phase ignores
the floor. Its projection, `start + bytes_to_free`, assumes every move is
evicted or unlinked; the free space comes back only minutes after an eviction
(T354), so it is an upper bound and the driver checks the measured free space
before every backup copy. Backup copies run in path order, each checked after
its write (free minus its bytes at or above the floor, as the archive's write
guard does); the first that would go below stops the run, so it and every later
copy are `floor_refused`. The backup projection is the low point before the
backup copies are evicted.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from tradepartner.config import TOMBSTONE_DIR, check_cache_relative_path

#: The pinned tree-to-constant table (amendment item 1 (a)): tree, module, name.
VERSION_TREES: Final[tuple[tuple[str, str, str], ...]] = (
    ("fsn", "tradepartner.adapters.edgar_source", "FSN_VERSION"),
    ("cover", "tradepartner.adapters.edgar_source", "COVER_VERSION"),
    ("header", "tradepartner.adapters.edgar_source", "HEADER_VERSION"),
    ("delisting", "tradepartner.adapters.edgar_source", "DELISTING_VERSION"),
    ("filename", "tradepartner.adapters.edgar_source", "FILENAME_VERSION"),
    ("statement_facts", "tradepartner.adapters.edgar_source", "STATEMENT_VERSION"),
    ("facts", "tradepartner.adapters.edgar_source", "PARSER_VERSION"),
    ("sec_forms_identity", "tradepartner.adapters.edgar", "SEC_FORMS_PARSER_VERSION"),
    ("sec_forms_parties", "tradepartner.adapters.edgar", "SEC_FORMS_PARSER_VERSION"),
)
#: The backed-up tree: copied, never moved.
BACKUP_TREE: Final = "stamps"
#: The departure-reason corpus's raw responses, read only by `corpus fetch`.
CORPUS_RAW: Final = "corpus/departure-reason/raw"
#: The bulk zip the owner may list, refused while bulk stamps are on.
SUBMISSIONS_ZIP: Final = "bulk/submissions.zip"

_VERSION_DIR = re.compile(r"v([0-9]+)")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class MigrationPlanRefusedError(RuntimeError):
    """The settings forbid planning a listed path."""


class MigrationClass(StrEnum):
    """What the migration does with a cache file."""

    MOVE = "move"
    BACKUP = "backup"
    KEEP = "keep"


@dataclass(frozen=True)
class CacheFile:
    """One file of the cache listing: a path relative to `edgar.cache_dir`, its
    size, its mtime in nanoseconds, and its SHA-256 when the caller hashed it."""

    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str | None = None


@dataclass(frozen=True)
class MigrationConfig:
    """The settings the planner reads, built by the archive config module's
    `migration_config`: the owner's extra paths, the archive floor in bytes and
    `edgar.bulk_stamps_enabled`."""

    extra_paths: tuple[str, ...]
    floor_bytes: int
    bulk_stamps_enabled: bool


@dataclass(frozen=True)
class PlannedFile:
    """A listed file with its class. `reason` names the rule that classed it;
    `duplicate` (move class) means its hash is already archived; `copy` (backup
    class) means its hash is new, so a copy is written; `floor_refused` means the
    backup copy is projected to stop the run at the floor."""

    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str | None
    klass: MigrationClass
    reason: str
    duplicate: bool = False
    copy: bool = False
    floor_refused: bool = False


@dataclass(frozen=True)
class PhaseProjection:
    """Projected free bytes after a phase; `floor_applies` is false for the move
    phase, which the floor does not stop."""

    name: str
    free_bytes: int
    below_floor: bool
    floor_applies: bool


@dataclass(frozen=True)
class MigrationPlan:
    """Every listed file's class, in path order, and the space projection."""

    files: tuple[PlannedFile, ...]
    floor_bytes: int
    phases: tuple[PhaseProjection, ...]

    @property
    def bytes_to_free(self) -> int:
        """Bytes the move class frees once moved files are evicted and duplicates
        unlinked."""
        return sum(f.size_bytes for f in self.files if f.klass is MigrationClass.MOVE)

    @property
    def duplicates(self) -> tuple[str, ...]:
        """Paths of moved files whose hash is already archived."""
        return tuple(f.path for f in self.files if f.duplicate)

    @property
    def duplicate_bytes(self) -> int:
        """Bytes of the duplicates."""
        return sum(f.size_bytes for f in self.files if f.duplicate)

    @property
    def transient_backup_bytes(self) -> int:
        """Bytes the backup copies need before their archive copies are evicted,
        refused copies included."""
        return sum(f.size_bytes for f in self.files if f.copy)

    @property
    def backup_refused_at(self) -> str | None:
        """The first backup copy projected to stop the run at the floor."""
        return next((f.path for f in self.files if f.floor_refused), None)

    def totals(self) -> dict[MigrationClass, tuple[int, int]]:
        """Files and bytes per class."""
        out = {c: (0, 0) for c in MigrationClass}
        for f in self.files:
            count, size = out[f.klass]
            out[f.klass] = (count + 1, size + f.size_bytes)
        return out


def current_versions() -> dict[str, int]:
    """Each pinned tree's current version, read from its constant at call time."""
    # Imported here, statically, so this pure module loads without the adapters.
    from tradepartner.adapters import edgar, edgar_source

    modules = {edgar.__name__: edgar, edgar_source.__name__: edgar_source}
    return {tree: int(getattr(modules[module], name)) for tree, module, name in VERSION_TREES}


def _covers(entry: str, path: str) -> bool:
    return path == entry or path.startswith(entry + "/")


def _classify(
    path: str, versions: Mapping[str, int], extra: tuple[str, ...]
) -> tuple[MigrationClass, str]:
    parts = path.split("/")
    if parts[0] == TOMBSTONE_DIR:
        return MigrationClass.KEEP, "tombstone_index"
    if parts[0] == BACKUP_TREE:
        return MigrationClass.BACKUP, "stamps"
    current = False
    if len(parts) >= 3 and parts[0] in versions:
        match = _VERSION_DIR.fullmatch(parts[1])
        if match is not None:
            if int(match.group(1)) != versions[parts[0]]:
                return MigrationClass.MOVE, "dead_version"
            current = True
    if _covers(CORPUS_RAW, path):
        return MigrationClass.MOVE, "corpus_raw"
    if any(_covers(entry, path) for entry in extra):
        return MigrationClass.MOVE, "owner_listed"
    return MigrationClass.KEEP, "current_version" if current else "not_named"


def _check_sha(value: str, what: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{what}: not a lowercase hex SHA-256")


def plan_migration(
    listing: Iterable[CacheFile],
    *,
    versions: Mapping[str, int],
    config: MigrationConfig,
    known_hashes: Collection[str],
    free_bytes: int,
) -> MigrationPlan:
    """Class every listed file and project the free space per phase (module
    docstring). Raises `MigrationPlanRefusedError` when an extra path covers
    `bulk/submissions.zip` while bulk stamps are on, and `ValueError` on a listing
    path that is not clean and relative, a repeated path, a negative size, a
    malformed hash, or `versions` that do not name exactly the pinned trees."""
    pinned = {tree for tree, _module, _name in VERSION_TREES}
    if set(versions) != pinned:
        raise ValueError("versions must name exactly the pinned trees")
    if config.bulk_stamps_enabled and any(_covers(e, SUBMISSIONS_ZIP) for e in config.extra_paths):
        raise MigrationPlanRefusedError(
            f"refusing to plan {SUBMISSIONS_ZIP} while edgar.bulk_stamps_enabled is on: "
            "it is then read by EDGAR runs; turn the key off or drop the path"
        )
    for sha in known_hashes:
        _check_sha(sha, "a known archive hash")
    files = sorted(listing, key=lambda f: f.path)
    seen_paths: set[str] = set()
    for f in files:
        check_cache_relative_path(f.path, "cache listing")
        if f.path in seen_paths:
            raise ValueError(f"cache listing names {f.path!r} twice")
        seen_paths.add(f.path)
        if f.size_bytes < 0:
            raise ValueError(f"cache listing: {f.path!r} has a negative size")
        if f.sha256 is not None:
            _check_sha(f.sha256, f"cache listing: {f.path!r}")

    classed = [(f, *_classify(f.path, versions, config.extra_paths)) for f in files]
    held = set(known_hashes)
    planned: dict[str, PlannedFile] = {}
    for f, klass, reason in classed:  # the move phase first
        if klass is MigrationClass.MOVE:
            duplicate = f.sha256 is not None and f.sha256 in held
            if f.sha256 is not None:
                held.add(f.sha256)
            planned[f.path] = _planned(f, klass, reason, duplicate=duplicate)

    after_move = free_bytes + sum(
        p.size_bytes for p in planned.values() if p.klass is MigrationClass.MOVE
    )
    running, stopped = after_move, False
    for f, klass, reason in classed:
        if klass is MigrationClass.BACKUP:
            copy = f.sha256 is None or f.sha256 not in held
            if f.sha256 is not None:
                held.add(f.sha256)
            refused = False
            if copy:
                stopped = stopped or running - f.size_bytes < config.floor_bytes
                refused = stopped
                if not refused:
                    running -= f.size_bytes
            planned[f.path] = _planned(f, klass, reason, copy=copy, refused=refused)
        elif klass is MigrationClass.KEEP:
            planned[f.path] = _planned(f, klass, reason)

    floor = config.floor_bytes
    phases = (
        PhaseProjection("start", free_bytes, free_bytes < floor, floor_applies=False),
        PhaseProjection("move", after_move, after_move < floor, floor_applies=False),
        PhaseProjection("backup", running, running < floor, floor_applies=True),
    )
    return MigrationPlan(
        files=tuple(planned[f.path] for f in files), floor_bytes=floor, phases=phases
    )


def _planned(
    f: CacheFile,
    klass: MigrationClass,
    reason: str,
    *,
    duplicate: bool = False,
    copy: bool = False,
    refused: bool = False,
) -> PlannedFile:
    return PlannedFile(
        path=f.path,
        size_bytes=f.size_bytes,
        mtime_ns=f.mtime_ns,
        sha256=f.sha256,
        klass=klass,
        reason=reason,
        duplicate=duplicate,
        copy=copy,
        floor_refused=refused,
    )

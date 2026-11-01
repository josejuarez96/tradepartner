"""The local raw archive's path and its disk-floor guard (plan T345, #2744).

ADR 0028 keeps every fetched raw body in a local archive (an iCloud Drive folder
on the owner's Mac) before it is parsed. Two rules live here, for T346's archive
helper to call:

- **Path** (spec amendment #2714 item 2). The root is `TRADEPARTNER_RAW_ARCHIVE`,
  held in `.env` only, never a committed path or account name. Unset or blank
  means the archive is off (`archive_root` returns `None`). Where an archive is
  needed, `require_archive_root` refuses an unset, relative, too short, unresolved
  (`~`, symlink, `.`, `..`, trailing slash), missing, non-directory or unwritable
  root. It never creates the directory, so a typo cannot start a second archive.
  T347 and T348 call it where retention needs the archive.
- **Floor** (ADR 0028 Upload; amendment item 3). `check_archive_write` refuses a
  write that would leave less than `raw_archive.min_free_disk_gb` free on the
  archive's disk, counting the file's own size: free bytes minus the file's bytes
  must stay at or above the floor. An unreadable free-space figure is refused too
  (fail closed). A refusal is raised, never a silent skip: the caller fails that
  source's run before any derived row is written.

No message here names the path: it carries the macOS account name. Messages name
the variable and the config key instead. The path is also a `SecretStr` on
`Settings`, so `config.clean_message` redacts it from any stored text.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store.raw_archive_plan import MigrationConfig

#: The environment variable that holds the archive root.
ARCHIVE_ENV_NAME = "TRADEPARTNER_RAW_ARCHIVE"
#: The config key that holds the write floor.
FLOOR_KEY = "raw_archive.min_free_disk_gb"
#: The fewest parts below `/` a root may have: a root of `/` or `/Users` would make
#: every scrubber redact every such prefix in stored text.
MIN_ROOT_PARTS = 2


class RawArchiveUnavailableError(RuntimeError):
    """The archive root is unset, relative, missing, not a directory or not writable."""


class RawArchiveFloorError(RuntimeError):
    """An archive write would leave the disk below `raw_archive.min_free_disk_gb`."""


def archive_root(settings: Settings) -> Path | None:
    """The configured archive root exactly as set (stripped, `~` not expanded), or
    `None` when it is unset or blank: the archive is off. Reads the setting only; it
    does not touch the disk."""
    text = _archive_text(settings)
    return None if text is None else Path(text)


def _archive_text(settings: Settings) -> str | None:
    """The stripped setting, as `config.secret_values` holds it, or `None` if blank."""
    secret = settings.tradepartner_raw_archive
    if secret is None:
        return None
    return secret.get_secret_value().strip() or None


def require_archive_root(settings: Settings) -> Path:
    """The archive root once it is checked to be an existing, writable directory.
    Raises `RawArchiveUnavailableError` otherwise; the message names
    `TRADEPARTNER_RAW_ARCHIVE`, never the path.

    The value must already be its own resolved, absolute form: no `~`, no symlink,
    no `.`, `..` or trailing slash, and at least `MIN_ROOT_PARTS` parts below `/`.
    So the returned path, and any `OSError` text naming a file under it, is the very
    string `config.secret_values` redacts; an expanded or resolved form would carry
    the account name past every scrubber (#2746 safety review)."""
    text = _archive_text(settings)
    if text is None:
        raise RawArchiveUnavailableError(f"raw archive is off: {ARCHIVE_ENV_NAME} is not set")
    root = Path(text)
    if not root.is_absolute():
        raise RawArchiveUnavailableError(
            f"{ARCHIVE_ENV_NAME} must be an absolute path (a relative one follows the "
            "working directory)"
        )
    if len(root.parts) - 1 < MIN_ROOT_PARTS:
        raise RawArchiveUnavailableError(
            f"{ARCHIVE_ENV_NAME} is too short: name a directory at least "
            f"{MIN_ROOT_PARTS} levels below /"
        )
    try:
        if str(root.resolve()) != text:
            raise RawArchiveUnavailableError(
                f"{ARCHIVE_ENV_NAME} must be written as its resolved path (no symlink, "
                "`.`, `..` or trailing slash)"
            )
        is_dir = root.is_dir()
        exists = is_dir or root.exists()
    except OSError as exc:
        raise RawArchiveUnavailableError(
            f"{ARCHIVE_ENV_NAME} could not be read ({type(exc).__name__})"
        ) from None
    if not exists:
        raise RawArchiveUnavailableError(
            f"{ARCHIVE_ENV_NAME} names a directory that does not exist; create it first"
        )
    if not is_dir:
        raise RawArchiveUnavailableError(f"{ARCHIVE_ENV_NAME} names a file, not a directory")
    if not os.access(root, os.W_OK | os.X_OK):
        raise RawArchiveUnavailableError(
            f"{ARCHIVE_ENV_NAME} names a directory that is not writable"
        )
    return root


def check_archive_write(settings: Settings, root: Path, size_bytes: int) -> None:
    """Refuse an archive write of `size_bytes` under `root` that would leave the
    disk below `raw_archive.min_free_disk_gb`, counting the file itself: free bytes
    minus `size_bytes` must be at or above the floor. Raises `RawArchiveFloorError`,
    also when the free space cannot be read; the message never names `root`."""
    if size_bytes < 0:
        raise ValueError(f"archive write size must not be negative, got {size_bytes}")
    floor_gb = settings.raw_archive.min_free_disk_gb
    try:
        free = diskfloor.free_bytes(root)
    except OSError as exc:
        raise RawArchiveFloorError(
            f"raw archive write refused: free space could not be read ({type(exc).__name__})"
        ) from None
    left = free - size_bytes
    if left < floor_gb * diskfloor.GB:
        raise RawArchiveFloorError(
            f"raw archive write refused: {size_bytes} bytes would leave {left} bytes "
            f"free, below {FLOOR_KEY} ({floor_gb:g} GB); "
            "free up disk space and rerun"
        )


def migration_config(settings: Settings) -> MigrationConfig:
    """The existing-cache migration planner's settings (plan T360, #2758; spec
    amendment #2743): `raw_archive.migrate_extra_paths`, the write floor in whole
    bytes (rounded up, so the plan never projects a copy the guard would refuse)
    and `edgar.bulk_stamps_enabled`. Reads settings only."""
    archive = settings.raw_archive
    return MigrationConfig(
        extra_paths=tuple(archive.migrate_extra_paths),
        floor_bytes=math.ceil(archive.min_free_disk_gb * diskfloor.GB),
        bulk_stamps_enabled=settings.edgar.bulk_stamps_enabled,
    )

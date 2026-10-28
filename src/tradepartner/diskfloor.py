"""Free-disk checks for the store's disk (#2454).

Two thresholds, both read from config and both checked at one moment only:
`ingest.min_free_disk_gb` is the hard floor an ingest refuses to start below
(never mid-write: the check runs once, before the first store write), and
`health.warn_free_disk_gb` is the early warning `health` prints well above it.
A value of 0 turns that check off. The paper run has its own floor
(`paper.min_free_disk_gb`, #1758) in `execution/run.py`; this module does not
touch it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tradepartner.config import Settings

GB = 1e9


def free_bytes(path: Path) -> int:
    """Free bytes on the disk holding `path`, or its nearest existing parent.
    The one seam tests replace; nothing else here reads the disk."""
    probe = path.resolve()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def store_free_gb(settings: Settings) -> float:
    """Free GB on the store's disk."""
    return free_bytes(Path(settings.store.path)) / GB


def ingest_floor_refusal(settings: Settings) -> str | None:
    """The refusal text when the store's disk is below `ingest.min_free_disk_gb`,
    else `None`. A floor of 0 skips the check (and the disk read)."""
    floor = settings.ingest.min_free_disk_gb
    if floor <= 0:
        return None
    free = store_free_gb(settings)
    if free < floor:
        return (
            f"ingest not started: {free:.1f} GB free is below ingest.min_free_disk_gb "
            f"({floor:g} GB); free up disk space and rerun"
        )
    return None


def low_disk_warning(settings: Settings) -> str | None:
    """The early-warning text when free space is below `health.warn_free_disk_gb`,
    else `None`. A threshold of 0 skips the check."""
    warn = settings.health.warn_free_disk_gb
    if warn <= 0:
        return None
    try:
        free = store_free_gb(settings)
    except OSError as exc:
        return f"low disk: could not read free space ({type(exc).__name__})"
    if free < warn:
        return (
            f"low disk: {free:.1f} GB free is below health.warn_free_disk_gb ({warn:g} GB); "
            f"ingest refuses below ingest.min_free_disk_gb "
            f"({settings.ingest.min_free_disk_gb:g} GB) and paper run below "
            f"paper.min_free_disk_gb ({settings.paper.min_free_disk_gb:g} GB)"
        )
    return None

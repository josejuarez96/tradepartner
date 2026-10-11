"""Write the in-window restamp pre-check for a named data release (T166f).

Data-foundation plan, amendment 2026-10-11 (#1497), decision (d), and the
data-releases runbook's rule 5 exception: read-only on the store, this lists
the open paper windows, every instant a paper run read the store at, the
journal high-water mark and the in-window candidate rows, and says whether a
correction could change what any paper decision read. It writes
``<out>/precheck.<release>.json`` and prints the file's path, its pass
verdict and its SHA-256 (the hash the release's ``before`` record carries).

Usage::

    uv run python scripts/restamp_precheck.py --release restamp-1382a2 \\
        --out data/releases/restamp-1382a2

Exit 0 when the pre-check passes, 1 when it fails (the file is written
either way), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tradepartner.store.restamp_precheck import (
    compute_precheck,
    file_sha256,
    precheck_file_name,
)

if TYPE_CHECKING:
    from tradepartner.config import Settings

_RELEASE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--release", required=True, help="the named data release")
    parser.add_argument(
        "--out", required=True, type=Path, help="directory for precheck.<release>.json"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Entry point: writes the file, prints its path, verdict and hash.

    ``now`` and ``settings`` are for tests; by default the wall clock and the
    configured store."""
    args = build_parser().parse_args(argv)
    if not _RELEASE.match(args.release):
        print(f"bad release name {args.release!r}", file=sys.stderr)
        return 2
    from tradepartner.config import get_settings
    from tradepartner.store.db import open_read_only

    path = args.out / precheck_file_name(args.release)
    if path.exists():
        print(f"{path} exists; a pre-check file is never overwritten", file=sys.stderr)
        return 2
    made_at = now if now is not None else datetime.now(UTC)
    with open_read_only(settings if settings is not None else get_settings()) as conn:
        result = compute_precheck(conn, release=args.release, made_at=made_at)
    args.out.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.to_bytes())
    verdict = "pass" if result.passed else "FAIL"
    first = result.window_first_session
    print(f"{path}")
    print(
        f"{verdict}: window_first_session {first.isoformat() if first else 'none'}, "
        f"{len(result.instants)} instants, {len(result.in_reach)} in-reach groups"
    )
    print(f"sha256 {file_sha256(path)}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())

"""The research store's files: the one resolver of `research.data_dir`.

Research-labeling spec Definitions ("Research store") and Data / interfaces (the
layout), as the amendment of 2026-10-06 reads them (C5 drops the drift set). Every
path under the research store is built here, so nothing else names the directory
(ADR 0013 point 3 (c); besides `tradepartner.research.load_dataset`, which reads a
registered export by its recorded path). Nothing under the store is a fact: it holds
frames, gold labels, inference records and review records, never a runtime table.

Layout (`<task>` is `departure-reason`, the one pilot):

- `frames/<task>/<sha256>/frame.parquet` and `counts.json`
- `gold/<task>/working.jsonl` (the session file) and
  `gold/<task>/<sha256>/gold.parquet` and `splits.json` (the locked export)
- `inferences/<task>/<run_id>.jsonl`
- `reviews/<task>/<run_id>.jsonl`

JSONL files are append-only (`append_jsonl`): a write opens the file for append and
never rewrites what is there, refuses to add to a file whose last line is torn, and
serialises every record before writing any, so a record that cannot be written
leaves the file as it was.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tradepartner.config import Settings

#: The one pilot this store holds (spec "Data / interfaces").
TASK = "departure-reason"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def data_dir(settings: Settings) -> Path:
    """The research store's root, `research.data_dir`."""
    return Path(settings.research.data_dir)


def _content_address(sha256: str) -> str:
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError(f"a content address must be a lowercase SHA-256, got {sha256!r}")
    return sha256


def _run_file(run_id: int) -> str:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError(f"a run id must be a positive integer, got {run_id!r}")
    return f"{run_id}.jsonl"


def frame_path(settings: Settings, sha256: str) -> Path:
    """The frame export with content hash `sha256`."""
    return data_dir(settings) / "frames" / TASK / _content_address(sha256) / "frame.parquet"


def frame_counts_path(settings: Settings, sha256: str) -> Path:
    """The count identity file beside the frame `sha256`."""
    return data_dir(settings) / "frames" / TASK / _content_address(sha256) / "counts.json"


def gold_working_path(settings: Settings) -> Path:
    """The gold session's working file (append-only JSONL)."""
    return data_dir(settings) / "gold" / TASK / "working.jsonl"


def gold_path(settings: Settings, sha256: str) -> Path:
    """The locked gold export with content hash `sha256`."""
    return data_dir(settings) / "gold" / TASK / _content_address(sha256) / "gold.parquet"


def gold_splits_path(settings: Settings, sha256: str) -> Path:
    """The split assignment beside the gold export `sha256`."""
    return data_dir(settings) / "gold" / TASK / _content_address(sha256) / "splits.json"


def inference_path(settings: Settings, run_id: int) -> Path:
    """Run `run_id`'s inference records (append-only JSONL)."""
    return data_dir(settings) / "inferences" / TASK / _run_file(run_id)


def inference_paths(settings: Settings) -> list[Path]:
    """Every inference records file in the store, by run id (the spend check sums
    over all of them, C4)."""
    folder = data_dir(settings) / "inferences" / TASK
    if not folder.is_dir():
        return []
    found = [p for p in folder.glob("*.jsonl") if p.stem.isdigit()]
    return sorted(found, key=lambda p: int(p.stem))


def review_path(settings: Settings, run_id: int) -> Path:
    """Run `run_id`'s review records (append-only JSONL)."""
    return data_dir(settings) / "reviews" / TASK / _run_file(run_id)


def append_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Append `records` to `path` as JSON lines and return how many were written.

    Every record is serialised first (`ValueError` on NaN or infinity, `TypeError` on
    a value JSON cannot hold), so a bad record writes nothing. A file whose last byte
    is not a newline (a torn earlier write) is refused, never extended. The file is
    opened for append only and synced before returning.
    """
    lines = [json.dumps(dict(r), ensure_ascii=False, allow_nan=False) + "\n" for r in records]
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        if handle.tell() > 0:
            with path.open("rb") as existing:
                existing.seek(-1, os.SEEK_END)
                if existing.read(1) != b"\n":
                    raise ValueError(f"{path} does not end with a newline (a torn write?)")
        handle.write("".join(lines).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    return len(lines)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every record in the JSONL file `path`; a line that is not a JSON object raises
    `ValueError` naming its line number."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except ValueError:
                raise ValueError(f"{path} line {number} is not valid JSON") from None
            if not isinstance(record, dict):
                raise ValueError(f"{path} line {number} is not a JSON object")
            records.append(record)
    return records

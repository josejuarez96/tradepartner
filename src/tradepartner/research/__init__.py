"""Research-experiment registry (docs/specs/research-registry.md): the run handle and
the one dataset reader (req 3; plan T81).

**`RunHandle`** is the proof that a `research_runs` row exists. Only
`store.research.open_run` and `store.research.attach_run` build one (ADR 0005: no
id, no run); its constructor raises. A handle remembers the store file it was
issued on and its bound dataset row (dataset rows are append-only, so the copy is
the row). A run refused at open still gets a handle, so the caller can report its
id, but `handle.refusal` names the outcome and `require_handle` refuses it: a
refused run reads no data.

**`require_handle`** is the check every analysis entry point under
`tradepartner.research` calls first, before reading any data: anything that is
not an open `RunHandle` (a plain integer, a refused run) raises `NoRunHandle`.

**`load_dataset`** is the only function that resolves a registered export's path
(`DatasetRecord.path`; req 3, req 13's AST test). It reads the export's and the
split file's bytes, checks each SHA-256 against the bound row before a byte is
parsed, and returns the bound split's rows only (`full` and `none`: every row).
`verify_only=True` does the hash check and parses nothing; `store.research
.write_result` uses it to record `dataset changed during run` (req 8) without a
second reader of the path.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

import polars as pl

from tradepartner.research.experiment import SPLITS, hash_directory

if TYPE_CHECKING:
    from tradepartner.store.research import DatasetRecord

#: Splits that bind every row of the export (req 3).
EVERY_ROW_SPLITS = frozenset({"full", "none"})


class NoRunHandle(TypeError):
    """An analysis entry point was called without an open `RunHandle`."""


class DatasetChanged(RuntimeError):
    """The export or its split file no longer has the bound row's SHA-256."""


class RunHandle:
    """Proof that a `research_runs` row exists. Built only by
    `store.research.open_run` and `store.research.attach_run`."""

    __slots__ = (
        "database",
        "dataset",
        "family",
        "kind",
        "known_at",
        "message",
        "n_configurations_declared",
        "refusal",
        "registration_id",
        "run_id",
        "slug",
        "split",
        "store_max_ingested_at",
        "synthetic",
        "touches_returns",
    )

    run_id: int
    registration_id: int
    slug: str
    kind: str
    family: str | None
    touches_returns: bool
    dataset: DatasetRecord
    split: str
    n_configurations_declared: int
    synthetic: bool
    known_at: datetime
    store_max_ingested_at: datetime | None
    database: str | None
    refusal: str | None
    message: str | None

    def __init__(self) -> None:
        raise TypeError("a RunHandle is issued only by store.research.open_run or attach_run")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a RunHandle is immutable")

    def __repr__(self) -> str:
        return (
            f"RunHandle(run_id={self.run_id}, slug={self.slug!r}, split={self.split!r}, "
            f"refusal={self.refusal!r})"
        )


def _issue_run_handle(**values: Any) -> RunHandle:
    """Build a handle; called by `store.research` only."""
    handle = object.__new__(RunHandle)
    for name, value in values.items():
        object.__setattr__(handle, name, value)
    return handle


def require_handle(handle: object) -> RunHandle:
    """Return `handle` if it is an open `RunHandle`, else raise `NoRunHandle`.
    Every analysis entry point calls this before reading any data (req 3)."""
    if not isinstance(handle, RunHandle):
        raise NoRunHandle(
            "an analysis needs a RunHandle from store.research.open_run or attach_run, "
            f"not {type(handle).__name__}"
        )
    if handle.refusal is not None:
        raise NoRunHandle(f"run {handle.run_id} was refused at open ({handle.refusal})")
    return handle


def _verified_bytes(path: Path, expected: str | None, what: str) -> bytes:
    """`path`'s bytes, refused with `DatasetChanged` unless their SHA-256 is
    `expected`. The bytes are hashed as read, then parsed from memory, so the
    file cannot change between the check and the parse."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DatasetChanged(f"{what} {path} cannot be read: {exc}") from exc
    actual = sha256(data).hexdigest()
    if actual != expected:
        raise DatasetChanged(f"{what} {path} has SHA-256 {actual}, the bound row has {expected}")
    return data


def _parse_tabular(data: bytes, suffix: str) -> pl.DataFrame:
    """A tabular export from verified bytes (the formats `dataset register` reads)."""
    if suffix == ".parquet":
        return pl.read_parquet(io.BytesIO(data))
    if suffix in (".csv", ".tsv"):
        separator = "\t" if suffix == ".tsv" else ","
        return pl.read_csv(io.BytesIO(data), separator=separator, try_parse_dates=True)
    raise ValueError(f"unsupported tabular export format {suffix!r} (expected .csv/.tsv/.parquet)")


def _parse_split_assignment(data: bytes) -> list[str]:
    """The per-row split names of a `--split-json` file, from verified bytes."""
    doc = json.loads(data)
    splits = doc.get("splits") if isinstance(doc, dict) else None
    if not isinstance(splits, list) or not all(s in SPLITS for s in splits):
        raise ValueError('a split file is {"splits": [<split>, ...]}, one per row')
    return splits


@overload
def load_dataset(handle: object, *, verify_only: Literal[False] = False) -> pl.DataFrame: ...
@overload
def load_dataset(handle: object, *, verify_only: Literal[True]) -> None: ...
def load_dataset(handle: object, *, verify_only: bool = False) -> pl.DataFrame | None:
    """The bound split's rows of the run's dataset (module docstring).

    Raises `NoRunHandle` without an open handle and `DatasetChanged` when the
    export or the split file differs from the bound row, before parsing either.
    A directory export has no rows to return (it cannot carry a split, req 11),
    so only `verify_only` accepts one."""
    run = require_handle(handle)
    record = run.dataset
    export = Path(record.path)
    if export.is_dir():
        actual = hash_directory(export)
        if actual != record.sha256:
            raise DatasetChanged(
                f"export {export} has SHA-256 {actual}, the bound row has {record.sha256}"
            )
        if verify_only:
            return None
        raise ValueError(f"export {export} is a directory; load_dataset returns tabular rows")
    data = _verified_bytes(export, record.sha256, "export")
    split_data = (
        _verified_bytes(Path(record.split_path), record.split_sha256, "split file")
        if record.split_path is not None
        else None
    )
    if verify_only:
        return None
    frame = _parse_tabular(data, export.suffix)
    if run.split in EVERY_ROW_SPLITS:
        return frame
    if split_data is None:
        return frame.clear()
    assignment = _parse_split_assignment(split_data)
    if len(assignment) != frame.height:
        raise ValueError(
            f"split file has {len(assignment)} entries, the export has {frame.height} rows"
        )
    return frame.filter(pl.Series([s == run.split for s in assignment]))

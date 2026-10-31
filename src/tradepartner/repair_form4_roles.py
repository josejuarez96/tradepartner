"""Manifest-gated correction of never-true SEC bulk Form 4 owner roles (#2697)."""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from tradepartner.adapters.edgar_insider_bulk import (
    _OWNER_COLUMNS,
    DEFAULT_MAX_MEMBER_BYTES,
    _relationship,
    _table,
)
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _write_run
from tradepartner.repair import REPAIR, REPAIRED, release_refusal
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc

_FLAGS = ("is_director", "is_officer", "is_ten_percent_owner", "is_other")


class RoleRepairRefused(ValueError):
    """The manifest, cached source, stored row, count, or release did not match."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _owners(path: Path) -> dict[tuple[str, str], str]:
    owners: dict[tuple[str, str], str] = {}
    with zipfile.ZipFile(path) as archive:
        for row in _table(archive, "REPORTINGOWNER", _OWNER_COLUMNS, DEFAULT_MAX_MEMBER_BYTES):
            key = (row["ACCESSION_NUMBER"].strip(), row["RPTOWNERCIK"].strip().zfill(10))
            if key in owners:
                raise RoleRepairRefused(f"duplicate owner {key} in {path}")
            owners[key] = row["RPTOWNER_RELATIONSHIP"]
    return owners


def _old(raw: str) -> tuple[bool, bool, bool, bool]:
    parts = {part.strip().lower() for part in raw.split(",")}
    return tuple(role in parts for role in ("director", "officer", "tenpercentowner", "other"))  # type: ignore[return-value]


def build_manifest(settings: Settings, zips: Iterable[Path], output: Path) -> int:
    """Read store and cached zips to write the proposed changed-row manifest."""
    owners: dict[tuple[str, str], tuple[str, str, str]] = {}
    found_zip = False
    for path in zips:
        found_zip = True
        resolved = path.resolve()
        sha = _sha(resolved)
        for key, raw in _owners(resolved).items():
            if key in owners:
                raise RoleRepairRefused(f"owner {key} occurs in multiple zips")
            owners[key] = (str(resolved), sha, raw)
    if not found_zip:
        raise RoleRepairRefused("no cached insider zips found")
    rows: list[dict[str, Any]] = []
    unmatched = 0
    with open_read_only(settings) as conn:
        cursor = conn.execute(
            "SELECT accession, filer_cik, line_no, is_director, is_officer, "
            "is_ten_percent_owner, is_other FROM sec_form4_transactions "
            "WHERE source = 'sec_insider_bulk'"
        )
        while batch := cursor.fetchmany(10_000):
            for accession, filer, line, *stored in batch:
                source = owners.get((accession, filer))
                if source is None:
                    unmatched += 1
                    continue
                zip_name, sha, raw = source
                new = _relationship(raw)
                if new is None:
                    raise RoleRepairRefused(f"unreadable relationship for {(accession, filer)}")
                old = _old(raw)
                if tuple(stored) == old and old != new:
                    rows.append(
                        {
                            "key": [accession, filer, line],
                            "zip": zip_name,
                            "sha256": sha,
                            "raw": raw,
                            "old_flags": list(old),
                            "new_flags": list(new),
                        }
                    )
                elif tuple(stored) != new and tuple(stored) != old:
                    raise RoleRepairRefused(f"stale stored flags for {(accession, filer, line)}")
    if unmatched:
        raise RoleRepairRefused(
            f"{unmatched} bulk rows have no cached owner match; manifest not written"
        )
    rows.sort(key=lambda row: row["key"])
    output.write_text(json.dumps({"version": 1, "rows": rows}, indent=2) + "\n")
    return len(rows)


def repair_form4_roles(
    settings: Settings,
    manifest: Path,
    release: str,
    expect_rows: int,
    *,
    dry_run: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """Validate all manifest entries, then update only changed flags and ingested_at."""
    if expect_rows < 0:
        raise RoleRepairRefused("--expect-rows must be nonnegative")
    try:
        document = json.loads(manifest.read_text())
        if document["version"] != 1 or not isinstance(document["rows"], list):
            raise ValueError("unsupported manifest")
        entries = document["rows"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RoleRepairRefused(f"invalid manifest: {exc}") from exc
    zip_data: dict[str, dict[tuple[str, str], str]] = {}
    zip_hashes: dict[str, str] = {}
    for entry in entries:
        try:
            path = str(Path(entry["zip"]).resolve())
            if path not in zip_hashes:
                zip_hashes[path] = _sha(Path(path))
            if zip_hashes[path] != entry["sha256"]:
                raise RoleRepairRefused(f"zip sha256 differs: {path}")
            if path not in zip_data:
                zip_data[path] = _owners(Path(path))
            accession, filer, line = entry["key"]
            raw = zip_data[path].get((accession, filer))
            if raw is None or raw != entry["raw"]:
                raise RoleRepairRefused(f"raw relationship differs for {entry['key']}")
            new = _relationship(raw)
            old = _old(raw)
            if (
                new is None
                or list(new) != entry["new_flags"]
                or list(old) != entry["old_flags"]
                or old == new
            ):
                raise RoleRepairRefused(f"role decode differs for {entry['key']}")
            if not isinstance(line, int) or line < 0:
                raise RoleRepairRefused(f"invalid line number for {entry['key']}")
        except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            if isinstance(exc, RoleRepairRefused):
                raise
            raise RoleRepairRefused(f"invalid manifest entry: {exc}") from exc
    started = ensure_tz_aware_utc(clock(), field_name="clock") if not dry_run else None
    context = open_read_only(settings) if dry_run else open_for_write(settings)
    with context as conn:
        refusal = release_refusal(conn, release)
        if refusal is not None:
            raise RoleRepairRefused(refusal)
        seen: set[tuple[str, str, int]] = set()
        changes: list[tuple[list[bool], tuple[str, str, int]]] = []
        for entry in entries:
            key = tuple(entry["key"])
            if key in seen:
                raise RoleRepairRefused(f"duplicate manifest key {key}")
            seen.add(key)
            row = conn.execute(
                "SELECT source, is_director, is_officer, is_ten_percent_owner, is_other "
                "FROM sec_form4_transactions WHERE accession = ? AND filer_cik = ? AND line_no = ?",
                list(key),
            ).fetchone()
            if row is None or row[0] != "sec_insider_bulk":
                raise RoleRepairRefused(f"missing or non-bulk row {key}")
            stored = list(row[1:])
            if stored == entry["new_flags"]:
                continue
            if stored != entry["old_flags"]:
                raise RoleRepairRefused(f"stale stored flags for {key}")
            changes.append((entry["new_flags"], key))
        if len(changes) != expect_rows:
            raise RoleRepairRefused(f"found {len(changes)} rows; expected {expect_rows}")
        if not dry_run and changes:
            at = ensure_tz_aware_utc(clock(), field_name="clock")
            conn.execute(
                "CREATE TEMP TABLE _form4_role_changes ("
                "accession VARCHAR, filer_cik VARCHAR, line_no INTEGER, "
                "is_director BOOLEAN, is_officer BOOLEAN, "
                "is_ten_percent_owner BOOLEAN, is_other BOOLEAN)"
            )
            conn.executemany(
                "INSERT INTO _form4_role_changes VALUES (?, ?, ?, ?, ?, ?, ?)",
                [[*key, *flags] for flags, key in changes],
            )
            updated = conn.execute(
                "UPDATE sec_form4_transactions AS stored SET "
                "is_director = changed.is_director, is_officer = changed.is_officer, "
                "is_ten_percent_owner = changed.is_ten_percent_owner, "
                "is_other = changed.is_other, ingested_at = ? "
                "FROM _form4_role_changes AS changed "
                "WHERE stored.accession = changed.accession "
                "AND stored.filer_cik = changed.filer_cik AND stored.line_no = changed.line_no",
                [at],
            ).fetchone()
            if updated is None or int(updated[0]) != len(changes):
                raise RuntimeError(
                    f"form4 roles updated {updated} rows where it counted {len(changes)}; "
                    "rolled back"
                )
            assert started is not None
            run = SourceRun(
                "edgar", REPAIRED, 0, "", f"form4 roles: {len(changes)} rows; release {release}"
            )
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
        return len(changes)

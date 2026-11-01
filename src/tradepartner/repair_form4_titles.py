"""Manifest-gated correction of CSV-quoted SEC bulk Form 4 titles (#2708)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from tradepartner.adapters.edgar_insider_bulk import read_insider_bulk
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _write_run
from tradepartner.repair import REPAIR, REPAIRED, release_refusal
from tradepartner.repair_form4_roles import _sha
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc


class TitleRepairRefused(ValueError):
    """The cached source, manifest, release, or stored title did not match."""


def manifest_sha256(path: Path) -> str:
    """Return the SHA-256 of the reviewed manifest's exact bytes."""
    return _sha(path)


def _quoted(title: str) -> str:
    return '"' + title.replace('"', '""') + '"'


def _source_rows(path: Path) -> dict[tuple[str, str, int], str]:
    rows: dict[tuple[str, str, int], str] = {}
    for accession, filing in read_insider_bulk(path).accessions.items():
        for transaction in filing.transactions:
            key = (accession, transaction.filer_cik, transaction.line_no)
            if key in rows:
                raise TitleRepairRefused(f"duplicate raw transaction {key} in {path}")
            rows[key] = transaction.security_title
    return rows


def build_manifest(settings: Settings, zips: Iterable[Path], output: Path) -> int:
    """Compare every stored bulk transaction with the fixed parser's source title."""
    source: dict[tuple[str, str, int], tuple[str, str, str]] = {}
    found = False
    for path in zips:
        found = True
        resolved = path.resolve()
        sha = _sha(resolved)
        for key, title in _source_rows(resolved).items():
            if key in source:
                raise TitleRepairRefused(f"transaction {key} occurs in multiple zips")
            source[key] = (str(resolved), sha, title)
    if not found:
        raise TitleRepairRefused("no cached insider zips found")
    rows: list[dict[str, Any]] = []
    unmatched = 0
    other_differences = 0
    with open_read_only(settings) as conn:
        cursor = conn.execute(
            "SELECT accession, filer_cik, line_no, security_title "
            "FROM sec_form4_transactions WHERE source = 'sec_insider_bulk'"
        )
        while batch := cursor.fetchmany(10_000):
            for accession, filer, line, old in batch:
                key = (accession, filer, line)
                candidate = source.get(key)
                if candidate is None:
                    unmatched += 1
                    continue
                zip_name, sha, new = candidate
                if old == new:
                    continue
                if '"' not in new or old != _quoted(new):
                    other_differences += 1
                    continue
                rows.append(
                    {
                        "key": list(key),
                        "old_title": old,
                        "new_title": new,
                        "zip": zip_name,
                        "sha256": sha,
                    }
                )
    if unmatched or other_differences:
        raise TitleRepairRefused(
            f"{unmatched} bulk rows have no cached transaction match; "
            f"{other_differences} have non-quoting title differences; manifest not written"
        )
    rows.sort(key=lambda row: row["key"])
    output.write_text(json.dumps({"version": 1, "rows": rows}, indent=2) + "\n")
    return len(rows)


def repair_form4_titles(
    settings: Settings,
    manifest: Path,
    release: str,
    expect_rows: int,
    *,
    dry_run: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """Validate the manifest and update only security_title in one transaction."""
    if expect_rows < 0:
        raise TitleRepairRefused("--expect-rows must be nonnegative")
    try:
        document = json.loads(manifest.read_text())
        if document["version"] != 1 or not isinstance(document["rows"], list):
            raise ValueError("unsupported manifest")
        entries = document["rows"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TitleRepairRefused(f"invalid manifest: {exc}") from exc
    hashes: dict[str, str] = {}
    sources: dict[str, dict[tuple[str, str, int], str]] = {}
    seen: set[tuple[str, str, int]] = set()
    for entry in entries:
        try:
            key = tuple(entry["key"])
            if len(key) != 3 or not isinstance(key[2], int) or key[2] < 0:
                raise TitleRepairRefused(f"invalid key {key}")
            if key in seen:
                raise TitleRepairRefused(f"duplicate manifest key {key}")
            seen.add(key)
            path = str(Path(entry["zip"]).resolve())
            if path not in hashes:
                hashes[path] = _sha(Path(path))
            if hashes[path] != entry["sha256"]:
                raise TitleRepairRefused(f"zip sha256 differs: {path}")
            if path not in sources:
                sources[path] = _source_rows(Path(path))
            new = sources[path].get(key)
            if (
                new is None
                or new != entry["new_title"]
                or '"' not in new
                or entry["old_title"] != _quoted(new)
            ):
                raise TitleRepairRefused(f"title decode differs for {key}")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TitleRepairRefused):
                raise
            raise TitleRepairRefused(f"invalid manifest entry: {exc}") from exc
    started = ensure_tz_aware_utc(clock(), field_name="clock") if not dry_run else None
    context = open_read_only(settings) if dry_run else open_for_write(settings)
    with context as conn:
        refusal = release_refusal(conn, release)
        if refusal is not None:
            raise TitleRepairRefused(refusal)
        changes: list[tuple[str, tuple[str, str, int]]] = []
        for entry in entries:
            key = tuple(entry["key"])
            row = conn.execute(
                "SELECT source, security_title FROM sec_form4_transactions "
                "WHERE accession = ? AND filer_cik = ? AND line_no = ?",
                list(key),
            ).fetchone()
            if row is None or row[0] != "sec_insider_bulk":
                raise TitleRepairRefused(f"missing or non-bulk row {key}")
            if row[1] == entry["new_title"]:
                continue
            if row[1] != entry["old_title"]:
                raise TitleRepairRefused(f"stale stored title for {key}")
            changes.append((entry["new_title"], key))
        if len(changes) != expect_rows:
            raise TitleRepairRefused(f"found {len(changes)} rows; expected {expect_rows}")
        if not dry_run and changes:
            conn.execute(
                "CREATE TEMP TABLE _form4_title_changes ("
                "accession VARCHAR, filer_cik VARCHAR, line_no INTEGER, security_title VARCHAR)"
            )
            conn.executemany(
                "INSERT INTO _form4_title_changes VALUES (?, ?, ?, ?)",
                [[*key, title] for title, key in changes],
            )
            updated = conn.execute(
                "UPDATE sec_form4_transactions AS stored SET "
                "security_title = changed.security_title FROM _form4_title_changes AS changed "
                "WHERE stored.accession = changed.accession "
                "AND stored.filer_cik = changed.filer_cik AND stored.line_no = changed.line_no"
            ).fetchone()
            if updated is None or int(updated[0]) != len(changes):
                raise TitleRepairRefused(
                    f"form4 titles updated {updated} rows where it counted "
                    f"{len(changes)}; rolled back"
                )
            assert started is not None
            run = SourceRun(
                "edgar",
                REPAIRED,
                0,
                "",
                f"form4 titles: {len(changes)} rows; release {release}; "
                f"manifest sha256 {manifest_sha256(manifest)}",
            )
            _write_run(
                conn,
                uuid.uuid4().hex,
                started,
                ensure_tz_aware_utc(clock(), field_name="finished_at"),
                run,
                REPAIR,
            )
        return len(changes)

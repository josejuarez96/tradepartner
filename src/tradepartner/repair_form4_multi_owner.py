"""Recheck stored bulk multi-owner Form 4 rows against SEC XML (#2720)."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import Form4Transaction, parse_form4
from tradepartner.adapters.form4_bulk_route import request_budget
from tradepartner.adapters.form4_route import quarter_of, rows_agree
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _write_run
from tradepartner.repair import REPAIR, REPAIRED, release_refusal
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc

_FLAGS = ("is_director", "is_officer", "is_ten_percent_owner", "is_other")
_FIELDS = tuple(field.name for field in fields(Form4Transaction))
_QUARTER = re.compile(r"^(\d{4})q([1-4])$")
_NEW_YORK = ZoneInfo("America/New_York")


class MultiOwnerRepairRefused(ValueError):
    """The candidate, manifest, release, or stored row failed validation."""


def manifest_digest(path: Path) -> str:
    """Return the SHA-256 of the exact reviewed manifest bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object, *, sorted_keys: bool = False) -> None:
    edgar_raw.write_atomic(
        path, (json.dumps(value, sort_keys=sorted_keys, indent=2) + "\n").encode("utf-8")
    )


def _quarter(value: str | None) -> str | None:
    if value is not None and _QUARTER.fullmatch(value) is None:
        raise MultiOwnerRepairRefused(f"invalid quarter: {value}")
    return value


def _selected(
    settings: Settings, first: str | None, last: str | None
) -> list[tuple[str, str, str]]:
    first, last = _quarter(first), _quarter(last)
    if first is not None and last is not None and first > last:
        raise MultiOwnerRepairRefused("from-quarter follows to-quarter")
    with open_read_only(settings) as conn:
        raw = conn.execute(
            "SELECT accession, min(issuer_cik) FILTER (WHERE source = 'sec_insider_bulk'), "
            "min(accepted_at) FILTER (WHERE source = 'sec_insider_bulk') "
            "FROM sec_form4_transactions GROUP BY accession "
            "HAVING count(DISTINCT filer_cik) "
            "FILTER (WHERE source = 'sec_insider_bulk') > 1 "
            "ORDER BY accession"
        ).fetchall()
    result = []
    for accession, issuer, accepted in raw:
        filing_quarter = quarter_of(accepted.astimezone(_NEW_YORK).date())
        quarter = f"{filing_quarter.year}q{filing_quarter.number}"
        if (first is None or quarter >= first) and (last is None or quarter <= last):
            result.append((accession, issuer, quarter))
    return result


def count_candidates(
    settings: Settings, *, from_quarter: str | None = None, to_quarter: str | None = None
) -> dict[str, int]:
    """Count selected accessions by filing quarter without SEC requests."""
    return dict(
        sorted(Counter(q for _, _, q in _selected(settings, from_quarter, to_quarter)).items())
    )


def _stored_conn(conn: duckdb.DuckDBPyConnection, accession: str) -> tuple[Form4Transaction, ...]:
    columns = ", ".join(_FIELDS)
    rows = conn.execute(
        f"SELECT {columns} FROM sec_form4_transactions WHERE accession = ? "
        "AND source = 'sec_insider_bulk' ORDER BY filer_cik, line_no",
        [accession],
    ).fetchall()
    return tuple(Form4Transaction(*row) for row in rows)


def _stored(settings: Settings, accession: str) -> tuple[Form4Transaction, ...]:
    with open_read_only(settings) as conn:
        return _stored_conn(conn, accession)


def _classify(
    accession: str, stored: tuple[Form4Transaction, ...], xml: str
) -> tuple[str, list[dict[str, Any]]]:
    parsed = parse_form4(xml)
    if {r.filer_cik for r in stored} != set(parsed.filer_ciks):
        return "other_difference", []
    if rows_agree(stored, parsed.transactions):
        return "agrees", []
    by_key = {(r.filer_cik, r.line_no): r for r in parsed.transactions}
    if len(by_key) != len(parsed.transactions) or len(stored) != len(by_key):
        return "other_difference", []
    aligned = []
    changes = []
    digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    for old in stored:
        new = by_key.get((old.filer_cik, old.line_no))
        if new is None:
            return "other_difference", []
        aligned.append(replace(old, **{flag: getattr(new, flag) for flag in _FLAGS}))
        old_flags = [getattr(old, flag) for flag in _FLAGS]
        new_flags = [getattr(new, flag) for flag in _FLAGS]
        if old_flags != new_flags:
            changes.append(
                {
                    "key": [accession, old.filer_cik, old.line_no],
                    "old_flags": old_flags,
                    "new_flags": new_flags,
                    "xml_sha256": digest,
                }
            )
    if not rows_agree(aligned, parsed.transactions):
        return "other_difference", []
    return "roles_differ", changes


def _fetch_xml(settings: Settings, client: httpx.Client, issuer: str, accession: str) -> str:
    names = edgar_raw.filing_index(issuer, accession, settings=settings, client=client)
    candidates = [
        name
        for name in names
        if name.lower().endswith(".xml") and not name.lower().startswith("xsl")
    ]
    if len(candidates) != 1:
        raise ValueError(f"{accession}: expected one root XML, found {len(candidates)}")
    path = edgar_raw.download_filing_file(
        issuer, accession, candidates[0], settings=settings, client=client
    )
    return path.read_text(encoding="utf-8")


def recheck_form4_multi_owner(
    settings: Settings,
    output: Path,
    *,
    max_requests: int,
    from_quarter: str | None = None,
    to_quarter: str | None = None,
    resume: Path | None = None,
    client: httpx.Client | None = None,
    fetch_xml: Callable[[Settings, httpx.Client, str, str], str] = _fetch_xml,
) -> dict[str, Any]:
    """Fetch and compare candidates, saving a resumable manifest and review summary."""
    selected = _selected(settings, from_quarter, to_quarter)
    completed: dict[str, dict[str, Any]] = {}
    if resume is not None:
        document = json.loads(resume.read_text())
        if document.get("version") != 1 or not isinstance(document.get("results"), dict):
            raise MultiOwnerRepairRefused("invalid resume file")
        completed = {
            accession: result
            for accession, result in document["results"].items()
            if result["class"] != "retry"
        }
        if not set(completed).issubset({row[0] for row in selected}):
            raise MultiOwnerRepairRefused("resume contains accessions outside selection")
    pending = [row for row in selected if row[0] not in completed]
    needed = 2 * len(pending)
    if max_requests < needed or max_requests > settings.events.sec_forms_max_requests:
        raise MultiOwnerRepairRefused(
            f"need at least {needed} requests for {len(pending)} accessions, excluding retries; "
            f"--max-requests is {max_requests}, configured sec_forms_max_requests "
            f"is {settings.events.sec_forms_max_requests}"
        )
    owned = client is None
    active = client or httpx.Client()
    requests = 0

    def spent() -> int:
        return requests

    def count_request(_request: httpx.Request) -> None:
        nonlocal requests
        requests += 1

    hooks = active.event_hooks
    active.event_hooks = {**hooks, "request": [count_request, *hooks.get("request", [])]}
    try:
        with request_budget(active, spent, max_requests):
            for accession, issuer, _quarter_label in pending:
                try:
                    xml = fetch_xml(settings, active, issuer, accession)
                    classification, rows = _classify(accession, _stored(settings, accession), xml)
                    cache_path = (
                        Path(settings.edgar.cache_dir) / "form4_multi_owner" / f"{accession}.xml"
                    )
                    edgar_raw.write_atomic(cache_path, xml.encode("utf-8"))
                    for row in rows:
                        row["xml_path"] = str(cache_path.resolve())
                except httpx.HTTPStatusError as exc:
                    classification, rows = (
                        ("unreadable" if exc.response.status_code == 404 else "retry"),
                        [],
                    )
                    reason = str(exc)
                except (OSError, httpx.HTTPError) as exc:
                    classification, rows = "retry", []
                    reason = str(exc)
                except ValueError as exc:
                    classification, rows = "unreadable", []
                    reason = str(exc)
                else:
                    reason = ""
                completed[accession] = {"class": classification, "rows": rows, "reason": reason}
                _write_json(output, {"version": 1, "results": completed}, sorted_keys=True)
    finally:
        active.event_hooks = hooks
        if owned:
            active.close()
    manifest_rows = sorted(
        (
            row
            for result in completed.values()
            if result["class"] == "roles_differ"
            for row in result["rows"]
        ),
        key=lambda row: row["key"],
    )
    manifest = {"version": 1, "rows": manifest_rows, "results": dict(sorted(completed.items()))}
    _write_json(output, manifest, sorted_keys=True)
    class_counts = Counter(v["class"] for v in completed.values())
    summary = {
        "counts": {
            name: class_counts[name]
            for name in ("agrees", "roles_differ", "other_difference", "unreadable", "retry")
        },
        "other_difference": sorted(
            k for k, v in completed.items() if v["class"] == "other_difference"
        ),
        "unreadable": sorted(k for k, v in completed.items() if v["class"] == "unreadable"),
        "retry": sorted(k for k, v in completed.items() if v["class"] == "retry"),
        "cache_dir": str(Path(settings.edgar.cache_dir).resolve()),
        "requests": requests,
    }
    _write_json(output.with_suffix(".summary.json"), summary)
    return summary


def repair_form4_multi_owner(
    settings: Settings,
    manifest: Path,
    release: str,
    expect_rows: int,
    *,
    dry_run: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """Apply only reviewed XML role changes under the named open release."""
    if expect_rows < 0:
        raise MultiOwnerRepairRefused("--expect-rows must be nonnegative")
    try:
        document = json.loads(manifest.read_text())
        if document["version"] != 1 or not isinstance(document["rows"], list):
            raise ValueError("unsupported manifest")
        entries = document["rows"]
        if any(result["class"] == "retry" for result in document.get("results", {}).values()):
            raise ValueError("transient fetch failures remain; resume the recheck")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MultiOwnerRepairRefused(f"invalid manifest: {exc}") from exc
    seen: set[tuple[str, str, int]] = set()
    for entry in entries:
        try:
            key = tuple(entry["key"])
            if len(key) != 3 or not isinstance(key[2], int) or key[2] < 0 or key in seen:
                raise ValueError("invalid or duplicate key")
            seen.add(key)
            if (
                not isinstance(entry["xml_sha256"], str)
                or len(entry["xml_sha256"]) != 64
                or any(type(x) is not bool for x in entry["old_flags"] + entry["new_flags"])
                or len(entry["old_flags"]) != 4
                or len(entry["new_flags"]) != 4
                or entry["old_flags"] == entry["new_flags"]
            ):
                raise ValueError("invalid flags or XML hash")
            xml = Path(entry["xml_path"]).read_bytes()
            if hashlib.sha256(xml).hexdigest() != entry["xml_sha256"]:
                raise ValueError("cached XML sha256 differs")
            parsed = parse_form4(xml.decode("utf-8"))
            matched = [r for r in parsed.transactions if (r.filer_cik, r.line_no) == tuple(key[1:])]
            if (
                len(matched) != 1
                or [getattr(matched[0], flag) for flag in _FLAGS] != entry["new_flags"]
            ):
                raise ValueError("XML roles differ")
        except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            raise MultiOwnerRepairRefused(f"invalid manifest entry: {exc}") from exc
    started = ensure_tz_aware_utc(clock(), field_name="clock") if not dry_run else None
    with open_read_only(settings) if dry_run else open_for_write(settings) as conn:
        refusal = release_refusal(conn, release)
        if refusal is not None:
            raise MultiOwnerRepairRefused(refusal)
        by_accession: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            by_accession.setdefault(entry["key"][0], []).append(entry)
        for accession, accession_entries in by_accession.items():
            paths = {entry["xml_path"] for entry in accession_entries}
            if len(paths) != 1:
                raise MultiOwnerRepairRefused(f"multiple XML paths for {accession}")
            xml_text = Path(next(iter(paths))).read_text(encoding="utf-8")
            if any(
                hashlib.sha256(xml_text.encode("utf-8")).hexdigest() != entry["xml_sha256"]
                for entry in accession_entries
            ):
                raise MultiOwnerRepairRefused(f"cached XML changed for {accession}")
            try:
                classification, computed = _classify(
                    accession, _stored_conn(conn, accession), xml_text
                )
            except ValueError as exc:
                raise MultiOwnerRepairRefused(f"XML recheck failed for {accession}: {exc}") from exc
            if classification not in ("agrees", "roles_differ"):
                raise MultiOwnerRepairRefused(f"stored non-role fields differ for {accession}")
            reviewed = {tuple(entry["key"]): entry for entry in accession_entries}
            if any(
                tuple(row["key"]) not in reviewed
                or row["new_flags"] != reviewed[tuple(row["key"])]["new_flags"]
                for row in computed
            ):
                raise MultiOwnerRepairRefused(f"manifest omits XML role changes for {accession}")
        changes = []
        for entry in entries:
            key = entry["key"]
            row = conn.execute(
                "SELECT source, is_director, is_officer, is_ten_percent_owner, is_other "
                "FROM sec_form4_transactions WHERE accession = ? AND filer_cik = ? AND line_no = ?",
                key,
            ).fetchone()
            if row is None or row[0] != "sec_insider_bulk":
                raise MultiOwnerRepairRefused(f"missing or non-bulk row {key}")
            stored = list(row[1:])
            if stored == entry["new_flags"]:
                continue
            if stored != entry["old_flags"]:
                raise MultiOwnerRepairRefused(f"stale stored flags for {key}")
            changes.append(entry)
        if len(changes) != expect_rows:
            raise MultiOwnerRepairRefused(f"found {len(changes)} rows; expected {expect_rows}")
        if not dry_run and changes:
            at = ensure_tz_aware_utc(clock(), field_name="clock")
            conn.execute(
                "CREATE TEMP TABLE _form4_multi_changes ("
                "accession VARCHAR, filer_cik VARCHAR, line_no INTEGER, "
                "is_director BOOLEAN, is_officer BOOLEAN, "
                "is_ten_percent_owner BOOLEAN, is_other BOOLEAN)"
            )
            conn.executemany(
                "INSERT INTO _form4_multi_changes VALUES (?, ?, ?, ?, ?, ?, ?)",
                [[*entry["key"], *entry["new_flags"]] for entry in changes],
            )
            updated = conn.execute(
                "UPDATE sec_form4_transactions AS stored SET "
                "is_director = changed.is_director, is_officer = changed.is_officer, "
                "is_ten_percent_owner = changed.is_ten_percent_owner, "
                "is_other = changed.is_other, ingested_at = ? "
                "FROM _form4_multi_changes AS changed WHERE stored.accession = changed.accession "
                "AND stored.filer_cik = changed.filer_cik AND stored.line_no = changed.line_no",
                [at],
            ).fetchone()
            if updated is None or int(updated[0]) != len(changes):
                raise MultiOwnerRepairRefused(
                    f"form4 multi-owner updated {updated} rows where it counted "
                    f"{len(changes)}; rolled back"
                )
            assert started is not None
            _write_run(
                conn,
                uuid.uuid4().hex,
                started,
                ensure_tz_aware_utc(clock(), field_name="finished_at"),
                SourceRun(
                    "edgar",
                    REPAIRED,
                    0,
                    "",
                    f"form4 multi-owner: {len(changes)} rows; release {release}",
                ),
                REPAIR,
            )
        return len(changes)

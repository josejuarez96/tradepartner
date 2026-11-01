"""Pure Form 4 provenance decisions (plan T348, #2755; ADR 0028 rules 1, 2 and 4;
data-foundation spec amendment #2714 items 1 and 5).

The ingest driver keeps each fetched Form 4 XML body and each new insider bulk zip
by content hash before parsing, and writes one provenance manifest per enclosing
`edgar` or `sec_forms` writer run. This module holds the parts that are plain
functions of their inputs, so they are pinned by tests and shared with a later
re-derive: no store, no network, no file system.

- **Row digest** (`canonical_row`, `row_digest`): every column of a written row
  except `ingested_at` (the commit stamp, which a re-derive cannot reproduce),
  names sorted, compact JSON, ASCII. Datetimes must be tz-aware and are written
  as UTC ISO text; dates as ISO; decimals normalized to plain text (`12.50` and
  `12.5` agree, as the store may change the scale); booleans, integers, strings
  and nulls as JSON. Any other value is refused, so the serialization cannot
  drift silently.
- **Manifest** (`build_manifest`, `parse_manifest`): a versioned JSON document
  with the writer's run ID, run source and mode, one entry per accession input
  (row source, quarter, raw SHA-256, URL, UTC fetch time, parser version, what
  happened, and the table key and digest of each row actually written) and the
  insider zips read. The bytes are canonical, so the same run content always has
  the same hash.
- **Run messages** (`quarter_message`, `message_digest`): `checked_sha256` first,
  `provenance_sha256` second, then `zip_sha256` and the rest, so both digests
  fit inside the 200-character `ingest.max_message_chars` test cap (163
  characters).
- **Rule 4** (`zip_changed`, `recheck_verdict`, `prior_xml_hashes`): a held
  accession makes no XML request unless its quarter's zip hash changed from a
  recorded check; a recheck compares the new XML hash with the hash in the
  manifest whose entry wrote the stored row.
- **Match** (`find_entry`): a stored row maps to exactly one manifest entry by
  row source, mode, accession, table key and row digest, never by an
  `ingested_at` window; none or several is an error.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

#: The run-message key that names a writer run's manifest.
PROVENANCE_KEY = "provenance_sha256"
MANIFEST_FORMAT = "tradepartner.form4_provenance"
MANIFEST_VERSION = 1
#: The enclosing writer runs that own a Form 4 manifest.
WRITER_SOURCES = frozenset({"edgar", "sec_forms"})
XML_SOURCE = "sec_form4_xml"
BULK_SOURCE = "sec_insider_bulk"
ROW_SOURCES = frozenset({XML_SOURCE, BULK_SOURCE})
#: Columns left out of the row digest: the commit stamp only.
DIGEST_EXCLUDED = frozenset({"ingested_at"})
#: Each table's unique key (`store/schema.py`), in column order.
TABLE_KEYS: Mapping[str, tuple[str, ...]] = {
    "sec_filing_events": ("accession", "subject_cik", "filer_cik"),
    "sec_form4_transactions": ("accession", "filer_cik", "line_no"),
}
#: What happened to one parsed input. Only `added` wrote rows.
OUTCOMES = frozenset(
    {"added", "unchanged", "changed_refused", "not_yet_known", "recheck_same", "not_written"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MESSAGE_DIGEST = re.compile(rf"(?:^|;)\s*{PROVENANCE_KEY}=([0-9a-f]{{64}})(?![0-9a-f])")


class ProvenanceError(ValueError):
    """A value, manifest or match that the provenance rules refuse."""


@dataclass(frozen=True)
class RawInput:
    """One kept raw input: its SHA-256, URL and UTC fetch time (`None` when the
    input predates provenance, such as a zip cached before T348) and the version
    of the parser that read it."""

    sha256: str
    url: str | None
    fetched_at: datetime | None
    parser_version: str


def _value(value: object) -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ProvenanceError("a row datetime must be tz-aware to be digested")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ProvenanceError("a row decimal must be finite to be digested")
        text = format(value.normalize(), "f")
        return "0" if text in ("-0", "0") else text
    raise ProvenanceError(f"a row value of type {type(value).__name__} has no pinned form")


def _utc_text(value: datetime | None) -> str | None:
    return None if value is None else str(_value(value))


def canonical_row(row: Mapping[str, object]) -> str:
    """The pinned text a row digest hashes; see the module docstring."""
    fields = {name: _value(row[name]) for name in sorted(row) if name not in DIGEST_EXCLUDED}
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=True)


def row_digest(row: Mapping[str, object]) -> str:
    """The stable SHA-256 of one written row."""
    return hashlib.sha256(canonical_row(row).encode("ascii")).hexdigest()


def written_rows(rows: Mapping[str, Sequence[Mapping[str, object]]]) -> list[dict[str, object]]:
    """Each written row's table, unique key and digest, in a fixed order."""
    out: list[dict[str, object]] = []
    for table, table_rows in rows.items():
        if table not in TABLE_KEYS:
            raise ProvenanceError(f"{table} has no pinned key")
        for row in table_rows:
            key = [_value(row[column]) for column in TABLE_KEYS[table]]
            out.append({"table": table, "key": key, "digest": row_digest(row)})
    return sorted(out, key=lambda item: (item["table"], json.dumps(item["key"]), item["digest"]))


def manifest_entry(
    *,
    accession: str,
    row_source: str,
    quarter: str | None,
    raw: RawInput,
    outcome: str,
    rows: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    """One accession input's manifest entry; only an `added` outcome lists rows."""
    if row_source not in ROW_SOURCES:
        raise ProvenanceError(f"unknown Form 4 row source {row_source!r}")
    if outcome not in OUTCOMES:
        raise ProvenanceError(f"unknown outcome {outcome!r}")
    if outcome != "added" and any(rows.values()):
        raise ProvenanceError(f"a {outcome} entry wrote no row")
    if not _SHA256.fullmatch(raw.sha256):
        raise ProvenanceError("a raw input needs a lowercase hex SHA-256")
    return {
        "accession": accession,
        "row_source": row_source,
        "quarter": quarter,
        "raw_sha256": raw.sha256,
        "url": raw.url,
        "fetched_at": _utc_text(raw.fetched_at),
        "parser_version": raw.parser_version,
        "outcome": outcome,
        "rows": written_rows(rows),
    }


def build_manifest(
    *,
    run_id: str,
    source: str,
    mode: str,
    entries: Iterable[Mapping[str, object]],
    zips: Iterable[Mapping[str, object]],
) -> bytes:
    """The canonical manifest bytes of one writer run."""
    if source not in WRITER_SOURCES:
        raise ProvenanceError(f"{source!r} is not a Form 4 writer run source")
    if not run_id or not mode:
        raise ProvenanceError("a manifest names its writer run ID and mode")
    document = {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "writer": {"run_id": run_id, "source": source, "mode": mode},
        "entries": sorted(
            (dict(entry) for entry in entries), key=lambda e: json.dumps(e, sort_keys=True)
        ),
        "zips": sorted((dict(item) for item in zips), key=lambda z: json.dumps(z, sort_keys=True)),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def parse_manifest(body: bytes) -> dict[str, Any]:
    """A manifest's document, refusing another format or version."""
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise ProvenanceError("a Form 4 manifest is not JSON") from exc
    if (
        not isinstance(document, dict)
        or document.get("format") != MANIFEST_FORMAT
        or document.get("version") != MANIFEST_VERSION
        or not isinstance(document.get("writer"), dict)
        or not isinstance(document.get("entries"), list)
    ):
        raise ProvenanceError("not a version-1 Form 4 provenance manifest")
    return document


def quarter_message(*, checked: str, provenance: str | None, zip_sha256: str, rest: str) -> str:
    """A `sec_insider_bulk` quarter row's message, digests first in the pinned order."""
    keys = [f"checked_sha256={checked}"]
    if provenance is not None:
        keys.append(f"{PROVENANCE_KEY}={provenance}")
    keys.append(f"zip_sha256={zip_sha256}")
    return "; ".join([*keys, rest]) if rest else "; ".join(keys)


def message_digest(message: str | None) -> str | None:
    """The whole `provenance_sha256` a run message names, or `None`."""
    found = _MESSAGE_DIGEST.search(message or "")
    return found.group(1) if found else None


def zip_changed(checked: str | None, current: str | None) -> bool:
    """Whether a quarter's zip hash changed from a recorded check: the explicit event
    that rechecks the quarter's held accessions once."""
    return checked not in (None, "none") and current is not None and checked != current


def recheck_verdict(prior: str | None, fetched: str) -> str:
    """`same` when the refetched XML hash is the held one, `changed` when it
    differs, `unknown` when no manifest names the held hash."""
    if prior is None:
        return "unknown"
    return "same" if prior == fetched else "changed"


def prior_xml_hashes(
    manifests: Iterable[Mapping[str, Any]], wanted: Collection[str]
) -> dict[str, str]:
    """Each wanted accession's XML hash from the entry that wrote its stored row."""
    found: dict[str, str] = {}
    for manifest in manifests:
        for entry in manifest["entries"]:
            accession = entry.get("accession")
            if (
                accession in wanted
                and accession not in found
                and entry.get("row_source") == XML_SOURCE
                and entry.get("outcome") == "added"
            ):
                found[accession] = entry["raw_sha256"]
    return found


def find_entry(
    manifests: Iterable[Mapping[str, Any]],
    *,
    row_source: str,
    mode: str,
    accession: str,
    table: str,
    key: Sequence[object],
    digest: str,
) -> tuple[str, Mapping[str, Any]]:
    """The one writer run ID and entry that wrote this stored row."""
    wanted_key = list(key)
    matches = [
        (manifest["writer"]["run_id"], entry)
        for manifest in manifests
        if manifest["writer"].get("mode") == mode
        and manifest["writer"].get("source") in WRITER_SOURCES
        for entry in manifest["entries"]
        if entry.get("row_source") == row_source
        and entry.get("accession") == accession
        and any(
            row["table"] == table and row["key"] == wanted_key and row["digest"] == digest
            for row in entry.get("rows", ())
        )
    ]
    if not matches:
        raise ProvenanceError(f"no manifest entry wrote {table} row {wanted_key} of {accession}")
    if len(matches) > 1:
        raise ProvenanceError(f"more than one manifest entry wrote {table} row {wanted_key}")
    return matches[0]

"""Pure SEC daily-index listing, store-cursor comparison and the daily files
provenance manifest (plan T347)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from tradepartner.adapters.edgar import UnstampedFiling, parse_filing_index
from tradepartner.adapters.form4_archive import ProvenanceError, RawInput, row_digest

DAILY_FORM_NAME = re.compile(r"form\.(\d{4})(\d{2})(\d{2})\.idx")


@dataclass(frozen=True)
class DailyIndexFile:
    """One form index file named by SEC's quarter directory listing."""

    path: str
    name: str
    last_modified: str
    size: str


def parse_daily_listing(
    payload: Mapping[str, Any], year: int, quarter: int
) -> tuple[DailyIndexFile, ...]:
    """Extract only dated form files belonging to the requested quarter.

    File names come exclusively from the listing, never a guessed calendar.
    The listing's size and last-modified text are opaque cursor values. SEC's
    last-modified text is Eastern wall time without a zone; it must not be
    parsed as UTC or used as `listed_on` by later tasks.
    """
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    items = payload["directory"]["item"]
    if not isinstance(items, list):
        raise ValueError("daily index listing has no item list")
    found: dict[str, DailyIndexFile] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("daily index listing has a malformed item")
        name = item["name"]
        match = DAILY_FORM_NAME.fullmatch(name)
        if match is None:
            continue
        listed_year, month, _day = map(int, match.groups())
        if listed_year != year or (month - 1) // 3 + 1 != quarter:
            continue
        modified, size = item.get("last-modified"), item.get("size")
        if not isinstance(modified, str) or not isinstance(size, str):
            raise ValueError(f"daily index listing lacks cursor fields: {name}")
        if name in found:
            raise ValueError(f"daily index listing repeats {name}")
        path = f"daily-index/{year}/QTR{quarter}/{name}"
        found[name] = DailyIndexFile(path, name, modified, size)
    return tuple(found[name] for name in sorted(found))


_DAILY_FILED = re.compile(r"\b(\d{4})(\d{2})(\d{2})(?=\s+edgar/data/)")


def parse_daily_body(body: bytes) -> tuple[UnstampedFiling, ...]:
    """The rows of one daily `form.<yyyymmdd>.idx` body: UTF-8 with universal
    newlines, its `YYYYMMDD` filed dates read as ISO dates, then
    `parse_filing_index` with no acceptance times. The incremental reader and the
    raw re-derive (T352) share it, so a re-parse reads a body as ingest did."""
    text = body.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return parse_filing_index(_DAILY_FILED.sub(r"\1-\2-\3", text), {}).unstamped


def daily_files_to_read(
    listing: tuple[DailyIndexFile, ...], stored: Mapping[str, tuple[str, str]]
) -> tuple[DailyIndexFile, ...]:
    """Select absent or re-posted files by path, listed modification and size."""
    return tuple(
        file for file in listing if stored.get(file.path) != (file.last_modified, file.size)
    )


# --- provenance (plan T347, #2714 item 1) ---------------------------------------
#
# The `edgar_index` writer keeps each daily `form.<yyyymmdd>.idx` body it parses in
# the raw archive by SHA-256 and writes one manifest per writer run. Each entry is
# keyed by the file's date: path, raw hash, URL, UTC fetch time, parser version,
# whether this run parsed it, and the `filing_index` rows it wrote (table key and
# stable row digest, `form4_archive.row_digest`'s pinned form). A stored row maps
# to its writer by mode, file date (the row's `listed_on`), key and digest, never
# by an `ingested_at` window. A quarterly `form.idx` is not entered: it stays
# under ADR 0028's snapshot exception.

#: The writer run source that owns a daily index manifest.
INDEX_WRITER = "edgar_index"
DAILY_SOURCE = "daily_index"
#: Bump when `parse_filing_index`'s reading of a daily file changes.
DAILY_PARSER_VERSION = "daily_index_parser=1"
INDEX_MANIFEST_FORMAT = "tradepartner.daily_index_provenance"
INDEX_MANIFEST_VERSION = 1
INDEX_TABLE = "filing_index"
#: `filing_index`'s unique key (`store/schema.py`).
INDEX_KEY = ("cik", "accession", "known_at")
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/"


def file_date(name: str) -> date:
    """The date a daily form file's name carries."""
    match = DAILY_FORM_NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"not a daily form file name: {name!r}")
    return date(*map(int, match.groups()))


def daily_path(day: date) -> str:
    """The listing path of the daily form file for `day`."""
    return f"daily-index/{day.year}/QTR{(day.month - 1) // 3 + 1}/form.{day:%Y%m%d}.idx"


def _utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ProvenanceError("a manifest time must be tz-aware")
    return value.astimezone(UTC).isoformat()


def index_key(row: Mapping[str, object]) -> list[object]:
    """A `filing_index` row's key in the manifest: CIK, accession, UTC `known_at`."""
    known = row["known_at"]
    if not isinstance(known, datetime):
        raise ProvenanceError("a filing_index row's known_at must be a datetime")
    return [row["cik"], row["accession"], _utc_text(known)]


def index_entry(
    *, path: str, raw: RawInput, parsed: bool, rows: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """One daily file's manifest entry; `rows` are the stored rows it wrote."""
    written = sorted(
        ({"table": INDEX_TABLE, "key": index_key(r), "digest": row_digest(r)} for r in rows),
        key=lambda item: (json.dumps(item["key"]), item["digest"]),
    )
    return {
        "file_date": file_date(path.rsplit("/", 1)[-1]).isoformat(),
        "path": path,
        "row_source": DAILY_SOURCE,
        "raw_sha256": raw.sha256,
        "url": raw.url,
        "fetched_at": None if raw.fetched_at is None else _utc_text(raw.fetched_at),
        "parser_version": raw.parser_version,
        "parsed": parsed,
        "outcome": "added" if written else "not_written",
        "rows": written,
    }


def build_index_manifest(
    *, run_id: str, mode: str, entries: Iterable[Mapping[str, object]]
) -> bytes:
    """The canonical manifest bytes of one `edgar_index` writer run."""
    if not run_id or not mode:
        raise ProvenanceError("a manifest names its writer run ID and mode")
    document = {
        "format": INDEX_MANIFEST_FORMAT,
        "version": INDEX_MANIFEST_VERSION,
        "writer": {"run_id": run_id, "source": INDEX_WRITER, "mode": mode},
        "entries": sorted((dict(e) for e in entries), key=lambda e: str(e["path"])),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def parse_index_manifest(body: bytes) -> dict[str, Any]:
    """A daily index manifest's document, refusing another format or version."""
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise ProvenanceError("a daily index manifest is not JSON") from exc
    if (
        not isinstance(document, dict)
        or document.get("format") != INDEX_MANIFEST_FORMAT
        or document.get("version") != INDEX_MANIFEST_VERSION
        or not isinstance(document.get("writer"), dict)
        or not isinstance(document.get("entries"), list)
    ):
        raise ProvenanceError("not a version-1 daily index provenance manifest")
    return document


def find_index_entry(
    manifests: Iterable[Mapping[str, Any]],
    *,
    mode: str,
    listed_on: date,
    key: Sequence[object],
    digest: str,
) -> tuple[str, Mapping[str, Any]]:
    """The one writer run ID and daily file entry that wrote this stored row."""
    wanted = list(key)
    matches = [
        (manifest["writer"]["run_id"], entry)
        for manifest in manifests
        if manifest["writer"].get("source") == INDEX_WRITER
        and manifest["writer"].get("mode") == mode
        for entry in manifest["entries"]
        if entry.get("file_date") == listed_on.isoformat()
        and any(r["key"] == wanted and r["digest"] == digest for r in entry.get("rows", ()))
    ]
    if len(matches) != 1:
        found = "no" if not matches else "more than one"
        raise ProvenanceError(f"{found} manifest entry wrote filing_index row {wanted}")
    return matches[0]

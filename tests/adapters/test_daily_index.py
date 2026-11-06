"""Cursor selection from SEC's recorded daily listing."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner.adapters.daily_index import daily_files_to_read, parse_daily_listing

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/edgar/daily_index/index.json"


def test_listing_selects_only_named_files_and_detects_reposts() -> None:
    files = parse_daily_listing(json.loads(FIXTURE.read_text()), 2026, 2)
    names = {file.name for file in files}
    assert "form.20260401.idx" in names
    assert "form.20260402.idx" in names
    assert "form.20260403.idx" in names
    assert "form.20260404.idx" not in names
    first = next(file for file in files if file.name == "form.20260401.idx")
    second = next(file for file in files if file.name == "form.20260402.idx")
    stored = {first.path: (first.last_modified, first.size), second.path: ("old", second.size)}
    chosen = daily_files_to_read(files, stored)
    assert first not in chosen
    assert second in chosen
    assert len(chosen) == len(files) - 1
    assert second in daily_files_to_read(files, {second.path: (second.last_modified, "old")})


def test_cursor_selection_is_invariant_to_later_listing_rows() -> None:
    """A later file cannot change the selection for an earlier listed file."""
    payload = json.loads(FIXTURE.read_text())
    complete = parse_daily_listing(payload, 2026, 2)
    first = complete[0]
    form_item = next(item for item in payload["directory"]["item"] if item["name"] == first.name)
    prefix = {"directory": {"item": [form_item]}}
    assert daily_files_to_read(parse_daily_listing(prefix, 2026, 2), {}) == (first,)
    assert daily_files_to_read(complete, {})[0] == first


def _stored_row(known_at: datetime, ingested_at: datetime) -> dict[str, object]:
    return {
        "cik": "0000000301",
        "accession": "0000000301-26-000001",
        "form": "10-K",
        "company_name": "Lambda Corp",
        "filed_on": date(2026, 3, 2),
        "accepted_at": datetime(2026, 3, 2, 22, tzinfo=UTC),
        "listed_on": date(2026, 3, 2),
        "list_position": 0,
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "edgar",
        "provenance": "filing",
    }


def test_index_manifest_maps_a_stored_row_by_mode_file_date_key_and_digest() -> None:
    """Plan T347: a row maps to exactly one writer entry by mode, file date, key
    and digest; the commit stamp (`ingested_at`) is not part of the match."""
    from tradepartner.adapters import daily_index as di
    from tradepartner.adapters.form4_archive import ProvenanceError, RawInput, row_digest

    known = datetime(2026, 3, 2, 22, tzinfo=UTC)
    row = _stored_row(known, datetime(2026, 3, 3, 4, 30, tzinfo=UTC))
    path = "daily-index/2026/QTR1/form.20260302.idx"
    assert di.daily_path(date(2026, 3, 2)) == path
    raw = RawInput("a" * 64, di.SEC_ARCHIVES + path, known, di.DAILY_PARSER_VERSION)
    entry = di.index_entry(path=path, raw=raw, parsed=True, rows=[row])
    unused = di.index_entry(path=path, raw=raw, parsed=True, rows=[])
    assert (entry["outcome"], unused["outcome"]) == ("added", "not_written")
    manifests = [
        di.parse_index_manifest(di.build_index_manifest(run_id=run, mode=mode, entries=entries))
        for run, mode, entries in (
            ("seed", "rebuild_index", [entry]),
            ("night", "session", [unused]),
        )
    ]
    # A later commit stamp leaves the digest, so the row still maps.
    restamped = _stored_row(known, datetime(2026, 11, 2, tzinfo=UTC))
    key, digest = di.index_key(restamped), row_digest(restamped)
    run, found = di.find_index_entry(
        manifests, mode="rebuild_index", listed_on=date(2026, 3, 2), key=key, digest=digest
    )
    assert (run, found["raw_sha256"]) == ("seed", "a" * 64)
    for mode, day in (("session", date(2026, 3, 2)), ("rebuild_index", date(2026, 3, 3))):
        with pytest.raises(ProvenanceError, match="no manifest entry"):
            di.find_index_entry(manifests, mode=mode, listed_on=day, key=key, digest=digest)
    with pytest.raises(ProvenanceError, match="more than one"):
        di.find_index_entry(
            [*manifests, manifests[0]],
            mode="rebuild_index",
            listed_on=date(2026, 3, 2),
            key=key,
            digest=digest,
        )
    with pytest.raises(ProvenanceError):
        di.parse_index_manifest(b'{"format": "tradepartner.form4_provenance", "version": 1}')

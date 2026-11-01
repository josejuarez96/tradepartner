"""Cursor selection from SEC's recorded daily listing."""

import json
from pathlib import Path

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

"""Pure SEC daily-index listing and store-cursor comparison."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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


def daily_files_to_read(
    listing: tuple[DailyIndexFile, ...], stored: Mapping[str, tuple[str, str]]
) -> tuple[DailyIndexFile, ...]:
    """Select absent or re-posted files by path, listed modification and size."""
    return tuple(
        file for file in listing if stored.get(file.path) != (file.last_modified, file.size)
    )

"""Reviewed successor-chain identities for the health alarm (#3142)."""

from __future__ import annotations

import csv
import io
from datetime import date
from importlib import resources

from tradepartner.calendar import is_session

_FILE = "successor_reviews.csv"
_COLUMNS = ("predecessor_id", "successor_id", "effective_session", "reason", "issue")
type ChainKey = tuple[str, str, date]


def parse_reviews(text: str) -> frozenset[ChainKey]:
    """Parse the reviewed CSV, refusing malformed, duplicate, or undocumented rows."""
    reader = csv.reader(io.StringIO(text))
    if tuple(next(reader, ())) != _COLUMNS:
        raise ValueError(f"{_FILE} line 1: expected {_COLUMNS}")
    keys: set[ChainKey] = set()
    for row in reader:
        if not row:
            continue
        line = reader.line_num
        if len(row) != len(_COLUMNS) or any(not value.strip() for value in row):
            raise ValueError(f"{_FILE} line {line}: five nonempty fields required")
        predecessor, successor, session, _reason, issue = (value.strip() for value in row)
        try:
            effective_session = date.fromisoformat(session)
        except ValueError:
            raise ValueError(
                f"{_FILE} line {line}: invalid effective_session {session!r}"
            ) from None
        if (
            not is_session(effective_session)
            or predecessor == successor
            or not issue.startswith("#")
        ):
            raise ValueError(f"{_FILE} line {line}: invalid chain identity or issue")
        key = (predecessor, successor, effective_session)
        if key in keys:
            raise ValueError(f"{_FILE} line {line}: duplicate chain {key}")
        keys.add(key)
    return frozenset(keys)


def reviewed_chains() -> frozenset[ChainKey]:
    """Load the packaged reviewed list; a missing file fails closed."""
    return parse_reviews(resources.files(__package__).joinpath(_FILE).read_text(encoding="utf-8"))

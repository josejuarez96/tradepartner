"""Point-in-time choice among a security's listing rows."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from typing import Any

from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED


def prefer_current_listing(
    row: Mapping[str, Any],
    held: Mapping[str, Any] | None,
    session: date,
    exchanges: Collection[str] = (),
) -> bool:
    """Choose the latest effective row; keep a real delisting visible on a tie.

    The caller supplies only rows known at its read cutoff. An undated row never
    displaces a dated row. A delisted row beats a possibly stale listed sibling;
    otherwise a live line beats a withdrawn one. Equal ranks retain frame order.
    """
    if held is None:
        return True
    valid_from, old_from = row.get("valid_from"), held.get("valid_from")
    if valid_from != old_from:
        return valid_from is not None and (old_from is None or valid_from > old_from)

    row_delisted, held_delisted = row.get("status") == DELISTED, held.get("status") == DELISTED
    if row_delisted != held_delisted:
        return row_delisted

    def live(candidate: Mapping[str, Any]) -> bool:
        if candidate.get("status") == LISTED:
            return True
        return (
            candidate.get("status") == TRANSFERRED
            and candidate.get("end_session") is not None
            and candidate["end_session"] >= session
        )

    row_live, held_live = live(row), live(held)
    if row_live != held_live:
        return row_live
    # When both lines have ended, the later end is the security's last line.
    # This preserves a real delisting after an earlier line withdrawal.
    if not row_live:
        end, old_end = row.get("end_session"), held.get("end_session")
        if end != old_end and (end is not None or old_end is not None):
            return end is not None and (old_end is None or end > old_end)
    return row.get("exchange") in exchanges and held.get("exchange") not in exchanges

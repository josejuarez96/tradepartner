"""Reviewed and config entries whose security id left the store (#3387).

A rebuild can change a successor's `<cik>@<date>` id (#3381, #3362), and an
entry keyed by the old id then goes stale without any error: the APA
`dividend_reviews.csv` row (#3263) kept matching nothing. `stale_keys` lists
every such entry, read-only, from five sources:

- `dividend_reviews.csv`, `listing_reviews.csv` and `spinoff_splits.csv`
  (the packaged, reviewed files);
- `alpaca.accepted_relistings` and `master.keep_successors` (config).

An id exists when its latest `securities` revision is not retracted (a store
below version 11 has no `retracted` column and holds none). Pure over the
connection and the entry lists: it writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import duckdb

from tradepartner.store import dividend_reviews, listing_reviews, spinoff_splits
from tradepartner.store.schema import has_retracted

DIVIDEND_REVIEWS: Final = "dividend_reviews.csv"
LISTING_REVIEWS: Final = "listing_reviews.csv"
SPINOFF_SPLITS: Final = "spinoff_splits.csv"
ACCEPTED_RELISTINGS: Final = "alpaca.accepted_relistings"
KEEP_SUCCESSORS: Final = "master.keep_successors"


@dataclass(frozen=True)
class StaleKey:
    """One entry whose `security_id` is not in the store."""

    source: str
    security_id: str
    #: The entry, for the reader to find it (ticker and date, or the id again).
    detail: str

    @property
    def line(self) -> str:
        """`<source>: <security_id> (<detail>)`."""
        return f"{self.source}: {self.security_id} ({self.detail})"


def live_security_ids(conn: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Ids whose latest `securities` revision is live (not retracted)."""
    live = "NOT retracted" if has_retracted(conn, "securities") else "TRUE"
    rows = conn.execute(
        f"""
        SELECT security_id FROM securities
        QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY known_at DESC) = 1
            AND {live}
        """
    ).fetchall()
    return frozenset(sid for (sid,) in rows)


def stale_keys(
    conn: duckdb.DuckDBPyConnection,
    *,
    accepted_relistings: Iterable[str],
    keep_successors: Iterable[str],
    dividends: Iterable[dividend_reviews.ReviewedGroup] | None = None,
    listings: Iterable[listing_reviews.ReviewedListing] | None = None,
    spinoffs: Iterable[spinoff_splits.SpinoffSplit] | None = None,
) -> tuple[StaleKey, ...]:
    """Every entry whose id is not in the store, sources in a fixed order.

    The three file sources default to the packaged files; a test passes its
    own rows.
    """
    live = live_security_ids(conn)
    found: list[StaleKey] = []

    def check(source: str, security_id: str, detail: str) -> None:
        if security_id not in live:
            found.append(StaleKey(source, security_id, detail))

    for group in dividend_reviews.reviewed_groups() if dividends is None else dividends:
        check(DIVIDEND_REVIEWS, group.security_id, f"{group.ticker} ex {group.ex_date}")
    for listing in listing_reviews.reviewed_listings() if listings is None else listings:
        check(
            LISTING_REVIEWS,
            listing.security_id,
            f"{listing.ticker} {listing.exchange} from {listing.valid_from}",
        )
    for split in spinoff_splits.spinoff_splits() if spinoffs is None else spinoffs:
        check(SPINOFF_SPLITS, split.security_id, f"{split.ticker} ex {split.ex_date}")
    for entry in accepted_relistings:
        check(ACCEPTED_RELISTINGS, entry, entry)
    for entry in keep_successors:
        check(KEEP_SUCCESSORS, entry, entry)
        # `master-retract` never retracts a kept id, so after a rebuild re-derives the
        # successor under a new date the old id stays live: flag it when its CIK holds
        # another live `<cik>@<date>` id.
        if entry in live and "@" in entry:
            cik = entry.split("@", 1)[0]
            others = sorted(i for i in live if i.startswith(f"{cik}@") and i != entry)
            if others:
                found.append(
                    StaleKey(KEEP_SUCCESSORS, entry, f"superseded: {cik} also holds {others}")
                )
    return tuple(found)

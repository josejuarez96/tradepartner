"""Reviewed and config entries whose security id left the store (#3387).

A rebuild can change a successor's `<cik>@<date>` id (#3381, #3362), and an
entry keyed by the old id then goes stale without any error: the APA
`dividend_reviews.csv` row (#3263) kept matching nothing. `stale_keys` lists
every such entry, read-only, from five sources:

- `dividend_reviews.csv`, `listing_reviews.csv` and `spinoff_splits.csv`
  (the packaged, reviewed files);
- `alpaca.accepted_relistings` and `master.keep_successors` (config).

An id exists when its latest `securities` revision is not retracted (a store
below version 11 has no `retracted` column and holds none). A kept successor
is also flagged if another live successor shares its listing span. The check
uses observed bars to bound a listing without a Form 25. It writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

import duckdb

from tradepartner.config import Settings
from tradepartner.store import delistings, dividend_reviews, listing_reviews, spinoff_splits
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
    settings: Settings,
    reading: datetime,
    accepted_relistings: Iterable[str],
    keep_successors: Iterable[str],
    dividends: Iterable[dividend_reviews.ReviewedGroup] | None = None,
    listings: Iterable[listing_reviews.ReviewedListing] | None = None,
    spinoffs: Iterable[spinoff_splits.SpinoffSplit] | None = None,
) -> tuple[StaleKey, ...]:
    """Missing ids and replaced kept successors, sources in a fixed order.

    The three file sources default to the packaged files; a test passes its
    own rows.
    """
    live = live_security_ids(conn)
    found: list[StaleKey] = []
    kept = tuple(keep_successors)
    ciks = {entry.split("@", 1)[0] for entry in kept if "@" in entry}
    relevant = sorted(i for i in live if any(i.startswith(f"{cik}@") for cik in ciks))
    ended_listings = delistings.listing_ends_as_of(conn, reading, settings, relevant)
    bar_live = "NOT retracted" if has_retracted(conn, "prices_daily") else "TRUE"
    bar_rows = conn.execute(
        f"""
        SELECT security_id, session FROM prices_daily
        WHERE security_id = ANY(?) AND known_at <= ?
        QUALIFY row_number() OVER (
            PARTITION BY security_id, session ORDER BY known_at DESC
        ) = 1 AND {bar_live}
        """,
        [relevant, reading],
    ).fetchall()
    bars: dict[str, list[date]] = {}
    for sid, session in bar_rows:
        bars.setdefault(sid, []).append(session)
    listing_rows = ended_listings.to_dicts()
    starts: dict[tuple[str, str | None], set[date]] = {}
    for row in listing_rows:
        starts.setdefault((row["security_id"], row["class_title"]), set()).add(row["valid_from"])
    spans: dict[str, set[tuple[str, str, str | None, date, date | None]]] = {}
    for row in listing_rows:
        sid = row["security_id"]
        if sid in live:
            end = row["end_session"] or row["effective_on"]
            if end is None:
                later_starts = [
                    d for d in starts[(sid, row["class_title"])] if d > row["valid_from"]
                ]
                next_start = min(later_starts) if later_starts else date.max
                within = [day for day in bars.get(sid, []) if row["valid_from"] <= day < next_start]
                end = max(within, default=None)
            spans.setdefault(sid, set()).add(
                (row["ticker"], row["exchange"], row["class_title"], row["valid_from"], end)
            )

    def shared_span(left: str, right: str) -> bool:
        for ticker, exchange, title, start, end in spans.get(left, set()):
            for other_ticker, other_exchange, other_title, other_start, other_end in spans.get(
                right, set()
            ):
                if not (
                    (ticker == other_ticker or (title is not None and title == other_title))
                    and exchange == other_exchange
                ):
                    continue
                # An emptied kept row can still have a re-dated replacement,
                # but an earlier id must carry bars or a derived end through
                # the kept row's start to prove that replacement.
                if end is None:
                    if other_start <= start and other_end is not None and start <= other_end:
                        return True
                    continue
                if other_end is None and other_start < start:
                    continue
                if other_start <= end and (other_end is None or start <= other_end):
                    return True
        return False

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
    for entry in kept:
        check(KEEP_SUCCESSORS, entry, entry)
        # A later relisting of the same CIK is a separate span. Only a different
        # id holding this kept id's listing span can show a live replacement.
        if entry in live and "@" in entry:
            cik = entry.split("@", 1)[0]
            others = sorted(
                i for i in live if i.startswith(f"{cik}@") and i != entry and shared_span(entry, i)
            )
            if others:
                found.append(
                    StaleKey(
                        KEEP_SUCCESSORS,
                        entry,
                        f"superseded: same listing span on {others} (owner review)",
                    )
                )
    return tuple(found)

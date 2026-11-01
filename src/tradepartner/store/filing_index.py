"""Stored EDGAR index rows and their point-in-time filed-date revisions.

The read side is intentionally separate from strategy and universe reads. A
listing's first acceptance stamp is permanent; only ``filed_on`` may revise.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.store.schema import atomic


@dataclass(frozen=True)
class Listing:
    """One SEC index listing, with its file's effective listing date."""

    cik: str
    accession: str
    form: str
    company_name: str
    filed_on: date
    accepted_at: datetime
    listed_on: date
    is_daily: bool
    #: 0-based position in its source index file (#2813); None when unknown.
    list_position: int | None = None


@dataclass(frozen=True)
class FilingRow:
    """A stored index revision."""

    cik: str
    accession: str
    form: str
    company_name: str
    filed_on: date
    accepted_at: datetime
    listed_on: date
    known_at: datetime
    ingested_at: datetime
    #: The first listing's 0-based position in its SEC index file; NULL on
    #: rows written before schema 46 (#2813). Revisions keep the first row's.
    list_position: int | None = None


@dataclass(frozen=True)
class InsertCounts:
    """Counts of inserted, revised, refused, and stale listings."""

    added: int = 0
    revised: int = 0
    changed: int = 0
    stale: int = 0


def quarterly_listed_on(last_modified: datetime) -> date:
    """A settled quarterly file covers at most the preceding Eastern date."""
    _utc(last_modified)
    return last_modified.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: list[object]) -> list[FilingRow]:
    return [FilingRow(*row) for row in conn.execute(sql, params).fetchall()]


_SELECT = (
    "cik, accession, form, company_name, filed_on, accepted_at, listed_on, known_at, ingested_at, "
    "list_position"
)
_LATEST_CIK = (
    "WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY cik, accession "
    "ORDER BY known_at DESC) AS rn FROM filing_index WHERE known_at <= ? AND cik = ?) "
    f"SELECT {_SELECT} FROM ranked WHERE rn = 1"
)
_LATEST_FORM = (
    "WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY cik, accession "
    "ORDER BY known_at DESC) AS rn FROM filing_index WHERE known_at <= ? AND form = ?) "
    f"SELECT {_SELECT} FROM ranked WHERE rn = 1"
)
_ORDER = " ORDER BY accepted_at, accession, cik"


def by_cik(conn: duckdb.DuckDBPyConnection, cik: str, t: datetime) -> list[FilingRow]:
    """Latest visible revision of each accession for one CIK."""
    return _rows(conn, _LATEST_CIK + _ORDER, [_utc(t), cik])


def by_form(conn: duckdb.DuckDBPyConnection, form: str, t: datetime) -> list[FilingRow]:
    """Latest visible revision of each accession of one form."""
    return _rows(conn, _LATEST_FORM + _ORDER, [_utc(t), form])


def by_quarter(
    conn: duckdb.DuckDBPyConnection, year: int, quarter: int, t: datetime
) -> list[FilingRow]:
    """Latest visible rows whose revised filing date falls in the quarter."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1 through 4")
    start = date(year, 3 * quarter - 2, 1)
    end = date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)
    sql = (
        f"SELECT {', '.join('f.' + col for col in _SELECT.split(', '))} "
        "FROM filing_index f WHERE f.filed_on >= ? AND f.filed_on < ? "
        "AND f.known_at <= ? AND NOT EXISTS ("
        "SELECT 1 FROM filing_index later WHERE later.cik = f.cik "
        "AND later.accession = f.accession AND later.known_at <= ? "
        "AND later.known_at > f.known_at)" + _ORDER
    )
    cutoff = _utc(t)
    return _rows(conn, sql, [start, end, cutoff, cutoff])


def first_known(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    *,
    cik: str | None = None,
    form: str | None = None,
) -> list[FilingRow]:
    """First visible revision per accession, for immutable backfill cutoffs.

    Filters are pushed before ranking so a form or issuer read does not load
    the full index into memory.
    """
    predicates = ["known_at <= ?"]
    params: list[object] = [_utc(t)]
    if cik is not None:
        predicates.append("cik = ?")
        params.append(cik)
    if form is not None:
        predicates.append("form = ?")
        params.append(form)
    sql = (
        "WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY cik, accession "
        "ORDER BY known_at ASC) AS rn FROM filing_index WHERE "
        + " AND ".join(predicates)
        + ") "
        + f"SELECT {_SELECT} FROM ranked WHERE rn = 1"
        + _ORDER
    )
    return _rows(conn, sql, params)


def accession_listing_order(
    conn: duckdb.DuckDBPyConnection, accession: str, t: datetime
) -> list[FilingRow]:
    """One accession's first-known row per CIK, in SEC listing order (#2813).

    Orders by the first listing's ``(listed_on, list_position)``, then CIK; a
    NULL position (a row written before schema 46) sorts after a known one.
    Never by physical row order, which a rebuild or restamp can change. The
    first element is "the first row SEC lists" for the accession.
    """
    sql = (
        "WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY cik "
        "ORDER BY known_at ASC) AS rn FROM filing_index WHERE known_at <= ? AND accession = ?) "
        f"SELECT {_SELECT} FROM ranked WHERE rn = 1 "
        "ORDER BY listed_on, list_position NULLS LAST, cik"
    )
    return _rows(conn, sql, [_utc(t), accession])


def snapshot_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Last committed EDGAR index writer run id for an optimistic snapshot."""
    row = conn.execute(
        "SELECT run_id FROM ingestion_runs WHERE source = 'edgar_index' "
        "ORDER BY started_at DESC, finished_at DESC NULLS LAST LIMIT 1"
    ).fetchone()
    return None if row is None else str(row[0])


@contextmanager
def listing_run(
    conn: duckdb.DuckDBPyConnection, ingested_at: datetime
) -> Iterator[Callable[[list[Listing]], None]]:
    """Collect per-file listings and commit their winners once at run end."""
    listings: list[Listing] = []

    def add(file_listings: list[Listing]) -> None:
        listings.extend(file_listings)

    yield add
    insert_listings(conn, listings, ingested_at)


def insert_listings(
    conn: duckdb.DuckDBPyConnection, listings: list[Listing], ingested_at: datetime
) -> InsertCounts:
    """Insert first rows or one eligible filed-date revision per accession.

    The first listing fixes identity and first-known date. Later listings
    compete by ``listed_on``; a daily file wins a tie. Identity disagreements
    are counted and never written.
    """
    clock = _utc(ingested_at)
    grouped: dict[tuple[str, str], list[Listing]] = {}
    for listing in listings:
        _utc(listing.accepted_at)
        if listing.listed_on > clock.date():
            raise ValueError("listing is after ingestion clock")
        key = (listing.cik, listing.accession)
        grouped.setdefault(key, []).append(listing)
    added = revised = changed = stale = 0
    with atomic(conn):
        for (cik, accession), candidates in grouped.items():
            counts = _insert_accession(conn, cik, accession, candidates, clock)
            added += counts.added
            revised += counts.revised
            changed += counts.changed
            stale += counts.stale
    return InsertCounts(added, revised, changed, stale)


def _insert_accession(
    conn: duckdb.DuckDBPyConnection,
    cik: str,
    accession: str,
    candidates: list[Listing],
    clock: datetime,
) -> InsertCounts:
    added = revised = changed = stale = 0
    latest = conn.execute(
        f"SELECT {_SELECT} FROM filing_index WHERE cik = ? AND accession = ? "
        "ORDER BY known_at DESC LIMIT 1",
        [cik, accession],
    ).fetchone()
    old = FilingRow(*latest) if latest is not None else None
    if old is None:
        first = min(
            enumerate(candidates),
            key=lambda item: (item[1].listed_on, item[1].is_daily, item[0]),
        )[1]
        if first.accepted_at > clock:
            raise ValueError("acceptance is after ingestion clock")
        conn.execute(
            "INSERT INTO filing_index (cik, accession, form, company_name, filed_on, "
            "accepted_at, listed_on, known_at, ingested_at, list_position, source, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'edgar', 'filing')",
            [
                first.cik,
                first.accession,
                first.form,
                first.company_name,
                first.filed_on,
                first.accepted_at,
                first.listed_on,
                first.accepted_at,
                clock,
                first.list_position,
            ],
        )
        added = 1
        old = FilingRow(
            first.cik,
            first.accession,
            first.form,
            first.company_name,
            first.filed_on,
            first.accepted_at,
            first.listed_on,
            first.accepted_at,
            clock,
            first.list_position,
        )
        candidates = [candidate for candidate in candidates if candidate is not first]
    valid = [
        listing
        for listing in candidates
        if (listing.form, listing.company_name, listing.accepted_at)
        == (old.form, old.company_name, old.accepted_at)
    ]
    changed = len(candidates) - len(valid)
    if not valid:
        return InsertCounts(added, revised, changed, stale)
    listing = max(
        enumerate(valid),
        key=lambda item: (item[1].listed_on, item[1].is_daily, item[0]),
    )[1]
    if listing.listed_on < old.listed_on:
        stale = 1
    elif listing.filed_on == old.filed_on:
        pass
    elif listing.listed_on == old.listed_on and not listing.is_daily:
        stale = 1
    else:
        if clock <= old.known_at:
            raise ValueError(
                "revision clock must follow the previous known_at; collect one run first"
            )
        conn.execute(
            "INSERT INTO filing_index (cik, accession, form, company_name, filed_on, "
            "accepted_at, listed_on, known_at, ingested_at, list_position, source, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'edgar', 'filing')",
            [
                listing.cik,
                listing.accession,
                listing.form,
                listing.company_name,
                listing.filed_on,
                listing.accepted_at,
                listing.listed_on,
                clock,
                clock,
                old.list_position,
            ],
        )
        revised = 1
    return InsertCounts(added, revised, changed, stale)

"""Stored EDGAR index rows and their point-in-time filed-date revisions.

The read side is intentionally separate from strategy and universe reads. A
listing's first acceptance stamp is permanent; only ``filed_on`` may revise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb


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
    "cik, accession, form, company_name, filed_on, accepted_at, listed_on, known_at, ingested_at"
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


def snapshot_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Last committed EDGAR index writer run id for an optimistic snapshot."""
    row = conn.execute(
        "SELECT run_id FROM ingestion_runs WHERE source = 'edgar_index' "
        "ORDER BY started_at DESC, finished_at DESC NULLS LAST LIMIT 1"
    ).fetchone()
    return None if row is None else str(row[0])


def insert_listings(
    conn: duckdb.DuckDBPyConnection, listings: list[Listing], ingested_at: datetime
) -> InsertCounts:
    """Insert first rows or one eligible filed-date revision per accession.

    A batch is one writer run. Among listings of an accession, the greatest
    ``listed_on`` wins and a daily file wins a tie. Identity disagreements
    are counted and never written.
    """
    clock = _utc(ingested_at)
    grouped: dict[tuple[str, str], list[Listing]] = {}
    for listing in listings:
        _utc(listing.accepted_at)
        key = (listing.cik, listing.accession)
        grouped.setdefault(key, []).append(listing)
    added = revised = changed = stale = 0
    for (cik, accession), candidates in grouped.items():
        latest = conn.execute(
            f"SELECT {_SELECT} FROM filing_index WHERE cik = ? AND accession = ? "
            "ORDER BY known_at DESC LIMIT 1",
            [cik, accession],
        ).fetchone()
        old = FilingRow(*latest) if latest is not None else None
        if old is not None:
            valid = [
                listing
                for listing in candidates
                if (listing.form, listing.company_name, listing.accepted_at)
                == (old.form, old.company_name, old.accepted_at)
            ]
            changed += len(candidates) - len(valid)
            if not valid:
                continue
            candidates = valid
        listing = max(
            enumerate(candidates),
            key=lambda item: (item[1].listed_on, item[1].is_daily, item[0]),
        )[1]
        if latest is None:
            if listing.accepted_at > clock:
                raise ValueError("acceptance is after ingestion clock")
            conn.execute(
                "INSERT INTO filing_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'edgar', 'filing')",
                [
                    listing.cik,
                    listing.accession,
                    listing.form,
                    listing.company_name,
                    listing.filed_on,
                    listing.accepted_at,
                    listing.listed_on,
                    listing.accepted_at,
                    clock,
                ],
            )
            added += 1
            continue
        assert old is not None
        if listing.listed_on < old.listed_on:
            stale += 1
        elif listing.filed_on == old.filed_on:
            continue
        elif listing.listed_on == old.listed_on and not listing.is_daily:
            stale += 1
        else:
            if clock <= old.known_at:
                raise ValueError("revision clock must follow the previous known_at")
            conn.execute(
                "INSERT INTO filing_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'edgar', 'filing')",
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
                ],
            )
            revised += 1
    return InsertCounts(added, revised, changed, stale)

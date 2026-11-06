"""#2921: the incremental-index reads scan `filing_index` once per run, not once
per accession chunk, CIK or accession, and return the rows the old reads did."""

from __future__ import annotations

from dataclasses import astuple
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb

from tradepartner import ingest as ingest_module
from tradepartner.config import Settings
from tradepartner.store import filing_index, schema

ACCEPTED = datetime(2026, 3, 2, 21, tzinfo=UTC)
RUN = datetime(2026, 3, 5, 23, tzinfo=UTC)
NOW = RUN + timedelta(days=1)
COLUMNS = (
    "cik, accession, form, company_name, filed_on, accepted_at, listed_on, "
    "known_at, ingested_at, list_position"
)


class _Counting:
    """A connection whose `filing_index` scans are counted (pending excluded)."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.scans = 0

    def execute(self, sql: str, params: Any = None) -> Any:
        if "FROM filing_index " in sql or sql.rstrip().endswith("FROM filing_index"):
            self.scans += 1
        return self._conn.execute(sql, params or [])


def _store(ciks: int = 40, accessions_per_cik: int = 30) -> duckdb.DuckDBPyConnection:
    """Many CIKs and accessions, shared copies, a revision and a row known after NOW."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    listings = []
    for c in range(ciks):
        cik = f"{c:010d}"
        for a in range(accessions_per_cik):
            listings.append(
                filing_index.Listing(
                    cik,
                    f"{c:010d}-26-{a:06d}",
                    "10-K" if a % 3 else "8-K",
                    f"Issuer {c}",
                    date(2026, 3, 2),
                    ACCEPTED,
                    date(2026, 3, 3),
                    True,
                    a,
                )
            )
        # A 25-NSE copy filed by the exchange under this CIK's accession.
        listings.append(
            filing_index.Listing(
                "0000876661",
                f"{c:010d}-26-000000",
                "25-NSE",
                "Exchange",
                date(2026, 3, 2),
                ACCEPTED,
                date(2026, 3, 3),
                True,
                999,
            )
        )
    filing_index.insert_listings(conn, listings, RUN)
    revised = [
        filing_index.Listing(
            row.cik,
            row.accession,
            row.form,
            row.company_name,
            date(2026, 3, 3),
            row.accepted_at,
            date(2026, 3, 4),
            True,
            row.list_position,
        )
        for row in listings[::7]
    ]
    filing_index.insert_listings(conn, revised, RUN + timedelta(hours=1))
    late = [
        filing_index.Listing(
            "0000000001",
            "0000000001-26-900000",
            "10-Q",
            "Issuer 1",
            date(2026, 3, 9),
            NOW + timedelta(days=3),
            date(2026, 3, 9),
            True,
            0,
        )
    ]
    filing_index.insert_listings(conn, late, NOW + timedelta(days=3))
    return conn


def _old_candidate_rows(
    conn: duckdb.DuckDBPyConnection, accessions: frozenset[str], ciks: frozenset[str]
) -> list[tuple[Any, ...]]:
    """`_index_state`'s candidate read before #2921: a query per 500-accession
    chunk, then one per CIK, de-duplicated by (cik, accession, known_at)."""
    raw: list[tuple[Any, ...]] = []
    ordered = sorted(accessions)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        marks = ", ".join("?" for _ in chunk)
        raw.extend(
            conn.execute(
                f"SELECT {COLUMNS} FROM filing_index WHERE accession IN ({marks}) "
                "AND known_at <= ?",
                [*chunk, NOW],
            ).fetchall()
        )
    for cik in sorted(ciks):
        raw.extend(
            conn.execute(
                f"SELECT {COLUMNS} FROM filing_index WHERE cik = ? AND known_at <= ?",
                [cik, NOW],
            ).fetchall()
        )
    return list({(row[0], row[1], row[7]): row for row in raw}.values())


def _key(row: Any) -> tuple[Any, ...]:
    return tuple(row)


def test_candidate_read_equals_the_chunked_reads_in_one_scan(settings: Settings) -> None:
    conn = _store()
    all_accessions = [
        a
        for (a,) in conn.execute(
            "SELECT DISTINCT accession FROM filing_index ORDER BY accession"
        ).fetchall()
    ]
    # >500 accessions (three old chunks), CIKs overlapping them, an unknown
    # accession and CIK, and the late accession (not visible at NOW).
    accessions = frozenset([*all_accessions[:1100], "0009999999-26-000000", "0000000001-26-900000"])
    ciks = frozenset({"0000000001", "0000000003", "0000000039", "0000876661", "0009999999"})
    counting = _Counting(conn)
    state = ingest_module._index_state(
        counting,  # type: ignore[arg-type]
        settings,
        NOW,
        (),
        {},
        {},
        candidate_accessions=accessions,
        candidate_ciks=ciks,
    )
    expected = sorted(_old_candidate_rows(conn, accessions, ciks), key=_key)
    assert len(expected) > 1100
    assert sorted((astuple(row) for row in state.stored), key=_key) == expected
    assert all(row.known_at <= NOW for row in state.stored)  # no look-ahead
    assert "0000000001-26-900000" not in {row.accession for row in state.stored}
    # The candidate rows and the issuer set: two scans, whatever the counts.
    # The old read made 3 chunk queries and 5 CIK queries here.
    assert counting.scans == 2


def test_candidate_read_with_no_candidates_reads_nothing(settings: Settings) -> None:
    conn = _store(ciks=3, accessions_per_cik=2)
    state = ingest_module._index_state(
        conn, settings, NOW, (), {}, {}, candidate_accessions=frozenset()
    )
    assert state.stored == ()


def test_touch_state_ciks_equal_one_read_per_accession() -> None:
    conn = _store()
    accessions = {
        "0000000001-26-000000",  # shared with the exchange's 25-NSE copy
        "0000000002-26-000004",  # revised: one CIK, two rows
        "0000000001-26-900000",  # known only later: the old read had no cutoff either
        "0009999999-26-000000",  # absent: kept as an empty key
        *(f"{c:010d}-26-000010" for c in range(40)),
    }
    old = {
        accession: {
            cik
            for (cik,) in conn.execute(
                "SELECT DISTINCT cik FROM filing_index WHERE accession = ?", [accession]
            ).fetchall()
        }
        for accession in accessions
    }
    counting = _Counting(conn)
    found = ingest_module._stored_ciks_of(counting, accessions)  # type: ignore[arg-type]
    assert found == old
    assert found["0000000001-26-000000"] == {"0000000001", "0000876661"}
    assert found["0009999999-26-000000"] == set()
    assert counting.scans == 1
    assert ingest_module._stored_ciks_of(counting, set()) == {}  # type: ignore[arg-type]
    assert counting.scans == 1

"""The OpenFIGI identity cross-check (plan T399, #3437): every kind is reported
with both values, an agreement is not, the store is read as it stood at the
observation's exact `known_at`, and nothing is written."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import figi_identity
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.figi_identity import (
    FIGI_SHARED_ACROSS_CIKS,
    SHARE_CLASS_SAME_CIK,
    SUCCESSOR_SHARE_CLASS_DIFFERS,
    UNMAPPED_WHILE_LISTED,
    figi_identity_rows,
)
from tradepartner.store.schema import init_schema

LONG_AGO = datetime(2019, 1, 2, 21, 0, tzinfo=UTC)
OBSERVED = datetime(2020, 6, 29, 21, 0, tzinfo=UTC)
T = datetime(2020, 6, 30, 21, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    db = duckdb.connect(":memory:")
    configure_connection(db)
    init_schema(db)
    yield db
    db.close()


def _security(
    conn: duckdb.DuckDBPyConnection, sid: str, cik: str, known: datetime = LONG_AGO
) -> None:
    insert_row(
        conn,
        "securities",
        {
            "security_id": sid,
            "cik": cik,
            "name": sid,
            "benchmark": False,
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _listing(
    conn: duckdb.DuckDBPyConnection, sid: str, ticker: str, known: datetime = LONG_AGO
) -> None:
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "valid_from": date(2019, 1, 3),
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _figi(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    *,
    figi: str | None = None,
    share_class: str | None = None,
    mapped: bool = True,
    known: datetime = OBSERVED,
) -> None:
    insert_row(
        conn,
        "openfigi_types",
        {
            "security_id": sid,
            "ticker": sid,
            "figi": (figi or f"BBG-{sid}") if mapped else "",
            "composite_figi": (figi or f"BBG-{sid}") if mapped else "",
            "share_class_figi": (share_class or f"BBGSC-{sid}") if mapped else "",
            "security_type": "Common Stock" if mapped else "",
            "security_type2": "Common Stock" if mapped else "",
            "market_sector": "Equity" if mapped else "",
            "name": sid if mapped else "",
            "exch_code": "US" if mapped else "",
            "mapped": mapped,
            "known_at": known,
            "ingested_at": known,
            "provenance": "openfigi",
            "source": "openfigi",
        },
    )


def _chain(conn: duckdb.DuckDBPyConnection, predecessor: str, successor: str) -> None:
    insert_row(
        conn,
        "successor_chains",
        {
            "predecessor_id": predecessor,
            "successor_id": successor,
            "evidence_accession": f"{successor}-1",
            "evidence_form": "8-K12B",
            "known_at": LONG_AGO,
            "ingested_at": LONG_AGO,
            "source": "edgar",
            "provenance": "filing",
            "retracted": False,
        },
    )


def _rows(conn: duckdb.DuckDBPyConnection, t: datetime = T) -> list[dict[str, Any]]:
    return figi_identity_rows(conn, t, SETTINGS)[1].to_dicts()


def test_an_agreeing_security_is_not_reported(conn: duckdb.DuckDBPyConnection) -> None:
    for sid, cik in (("A", "C1"), ("B", "C2")):
        _security(conn, sid, cik)
        _listing(conn, sid, sid)
        _figi(conn, sid)
    observations, frame = figi_identity_rows(conn, T, SETTINGS)
    assert observations == 2
    assert frame.is_empty()


def test_two_ids_of_one_cik_in_one_share_class(conn: duckdb.DuckDBPyConnection) -> None:
    for sid in ("A", "B", "C"):
        _security(conn, sid, "C1")
        _listing(conn, sid, sid)
    _figi(conn, "A", share_class="SC1")
    _figi(conn, "B", share_class="SC1")
    _figi(conn, "C", share_class="SC2")  # a real second class: agrees
    assert _rows(conn) == [
        {
            "kind": SHARE_CLASS_SAME_CIK,
            "security_id": "A",
            "other_id": "B",
            "session": date(2020, 6, 29),
            "field": "share_class_figi",
            "value": "SC1",
            "other_value": "SC1",
            "cik": "C1",
            "other_cik": "C1",
            "ticker": "",
            "exchange": "",
            "known_at": OBSERVED,
        }
    ]


def test_one_share_class_under_two_ciks_is_not_a_duplicate(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    for sid, cik in (("A", "C1"), ("B", "C2")):
        _security(conn, sid, cik)
        _listing(conn, sid, sid)
        _figi(conn, sid, share_class="SC1", figi=f"F-{sid}")
    assert _rows(conn) == []


def test_successors_in_different_share_classes(conn: duckdb.DuckDBPyConnection) -> None:
    for sid, cik in (("OLD", "C1"), ("NEW", "C2"), ("OLD2", "C3"), ("NEW2", "C4")):
        _security(conn, sid, cik)
        _listing(conn, sid, sid)
    _chain(conn, "OLD", "NEW")
    _chain(conn, "OLD2", "NEW2")
    _figi(conn, "OLD", share_class="SC1")
    _figi(conn, "NEW", share_class="SC2", known=OBSERVED + timedelta(hours=1))
    _figi(conn, "OLD2", share_class="SC3")
    _figi(conn, "NEW2", share_class="SC3")  # agrees
    [row] = _rows(conn)
    assert (row["kind"], row["security_id"], row["other_id"]) == (
        SUCCESSOR_SHARE_CLASS_DIFFERS,
        "NEW",
        "OLD",
    )
    assert (row["value"], row["other_value"]) == ("SC2", "SC1")
    assert row["known_at"] == OBSERVED + timedelta(hours=1)


def test_a_live_but_unmapped_id_is_listed_with_ticker_and_exchange(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _security(conn, "A", "C1")
    _listing(conn, "A", "AAA")
    _figi(conn, "A", mapped=False)
    [row] = _rows(conn)
    assert row["kind"] == UNMAPPED_WHILE_LISTED
    assert (row["security_id"], row["ticker"], row["exchange"]) == ("A", "AAA", "NYSE")
    assert (row["field"], row["value"], row["known_at"]) == ("mapped", "FALSE", OBSERVED)
    assert row["session"] == date(2020, 6, 29)


def test_an_unmapped_id_with_no_live_listing_is_not_listed(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _security(conn, "A", "C1")
    _figi(conn, "A", mapped=False)
    assert _rows(conn) == []


def test_one_figi_across_two_ciks(conn: duckdb.DuckDBPyConnection) -> None:
    for sid, cik in (("A", "C1"), ("B", "C2")):
        _security(conn, sid, cik)
        _listing(conn, sid, sid)
        _figi(conn, sid, figi="BBGSAME")
    kinds = [(r["kind"], r["field"], r["cik"], r["other_cik"]) for r in _rows(conn)]
    assert kinds == [
        (FIGI_SHARED_ACROSS_CIKS, "composite_figi", "C1", "C2"),
        (FIGI_SHARED_ACROSS_CIKS, "figi", "C1", "C2"),
    ]


def test_an_observation_after_t_is_not_seen(conn: duckdb.DuckDBPyConnection) -> None:
    _security(conn, "A", "C1")
    _listing(conn, "A", "AAA")
    _figi(conn, "A", mapped=False)
    observations, frame = figi_identity_rows(conn, OBSERVED - timedelta(seconds=1), SETTINGS)
    assert (observations, frame.height) == (0, 0)


def test_a_listing_revised_later_the_same_day_is_not_seen(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The observation is compared with the listings as known at its exact
    `known_at`, not at the end of that day: a listing stamped one minute later
    does not make the id 'live' at the observation."""
    _security(conn, "A", "C1")
    _listing(conn, "A", "AAA", known=OBSERVED + timedelta(minutes=1))
    _figi(conn, "A", mapped=False)
    assert _rows(conn) == []
    # At the exact instant it is seen.
    other = duckdb.connect(":memory:")
    configure_connection(other)
    init_schema(other)
    _security(other, "A", "C1")
    _listing(other, "A", "AAA", known=OBSERVED)
    _figi(other, "A", mapped=False)
    assert len(_rows(other)) == 1
    other.close()


def test_a_cik_revised_later_the_same_day_is_not_seen(conn: duckdb.DuckDBPyConnection) -> None:
    """B is under C2 when observed; a revision moving it to C1 one minute later
    must not make 'A' and 'B' a same-CIK share-class duplicate."""
    _security(conn, "A", "C1")
    _security(conn, "B", "C2")
    _security(conn, "B", "C1", known=OBSERVED + timedelta(minutes=1))
    for sid in ("A", "B"):
        _listing(conn, sid, sid)
        _figi(conn, sid, share_class="SC1", figi=f"F-{sid}")
    assert _rows(conn) == []


def test_a_chain_written_later_the_same_day_is_not_seen(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    for sid, cik in (("OLD", "C1"), ("NEW", "C2")):
        _security(conn, sid, cik)
        _listing(conn, sid, sid)
    insert_row(
        conn,
        "successor_chains",
        {
            "predecessor_id": "OLD",
            "successor_id": "NEW",
            "evidence_accession": "NEW-1",
            "evidence_form": "8-K12B",
            "known_at": OBSERVED + timedelta(minutes=1),
            "ingested_at": OBSERVED + timedelta(minutes=1),
            "source": "edgar",
            "provenance": "filing",
            "retracted": False,
        },
    )
    _figi(conn, "OLD", share_class="SC1")
    _figi(conn, "NEW", share_class="SC2")
    assert _rows(conn) == []


def test_no_table_is_no_observation(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DROP TABLE openfigi_types")
    assert figi_identity_rows(conn, T, SETTINGS)[0] == 0


def _hash(conn: duckdb.DuckDBPyConnection) -> str:
    digest = hashlib.sha256()
    for table in ("listings", "securities", "delistings", "retracted_rows"):
        if conn.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone() == (0,):
            continue
        digest.update(repr(conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()).encode())
    return digest.hexdigest()


def test_the_check_opens_read_only_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    writer = duckdb.connect(str(path))
    configure_connection(writer)
    init_schema(writer)
    _security(writer, "A", "C1")
    _listing(writer, "A", "AAA")
    _figi(writer, "A", mapped=False)
    writer.close()
    reader = duckdb.connect(str(path), read_only=True)
    try:
        before = _hash(reader)
        assert len(_rows(reader)) == 1
        assert _hash(reader) == before
    finally:
        reader.close()


def test_the_module_states_what_it_cannot_check() -> None:
    text = " ".join(figi_identity.CANNOT_CHECK)
    assert "query echo" in text
    assert "always US" in text

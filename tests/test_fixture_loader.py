"""Tests for tests/conftest.py's `load_universe_fixtures` (T4 review fix):
CSV columns bind by header name, not position, and TIMESTAMPTZ cells must
carry an explicit offset.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures

from tradepartner.store import schema
from tradepartner.store.db import configure_connection


@pytest.fixture
def store_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


def test_missing_fixtures_dir_is_a_no_op(
    tmp_path: Path, store_conn: duckdb.DuckDBPyConnection
) -> None:
    load_universe_fixtures(store_conn, tmp_path / "does-not-exist")
    (count,) = store_conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert count == 0


def test_columns_bind_by_header_name_not_order(
    tmp_path: Path, store_conn: duckdb.DuckDBPyConnection
) -> None:
    """The CSV's header order is deliberately not the table's declaration
    order, to prove binding is by name (`BY NAME`), not position."""
    fixtures_dir = tmp_path / "universe"
    fixtures_dir.mkdir()
    csv_path = fixtures_dir / "prices_daily.csv"
    csv_path.write_text(
        "provenance,source,ingested_at,known_at,volume,close,low,high,open,session,security_id\n"
        "bar,test,2020-01-02T21:00:00+00:00,2020-01-02T21:00:00+00:00,1000,10.5,9.5,11.0,10.0,"
        "2020-01-02,S1\n"
    )

    load_universe_fixtures(store_conn, fixtures_dir)

    row = store_conn.execute(
        "SELECT security_id, session, open, close, volume, provenance FROM prices_daily"
    ).fetchone()
    assert row == ("S1", date(2020, 1, 2), 10.0, 10.5, 1000, "bar")


def test_unknown_table_name_raises(tmp_path: Path, store_conn: duckdb.DuckDBPyConnection) -> None:
    fixtures_dir = tmp_path / "universe"
    fixtures_dir.mkdir()
    (fixtures_dir / "not_a_real_table.csv").write_text("a,b\n1,2\n")

    with pytest.raises(ValueError, match="does not match any store table"):
        load_universe_fixtures(store_conn, fixtures_dir)


def test_timestamptz_cell_without_offset_raises(
    tmp_path: Path, store_conn: duckdb.DuckDBPyConnection
) -> None:
    fixtures_dir = tmp_path / "universe"
    fixtures_dir.mkdir()
    csv_path = fixtures_dir / "prices_daily.csv"
    csv_path.write_text(
        "security_id,session,open,high,low,close,volume,known_at,ingested_at,source,provenance\n"
        "S1,2020-01-02,10.0,11.0,9.5,10.5,1000,2020-01-02T21:00:00,2020-01-02T21:00:00+00:00,"
        "test,bar\n"
    )

    with pytest.raises(ValueError, match="no explicit UTC offset"):
        load_universe_fixtures(store_conn, fixtures_dir)

    (count,) = store_conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert count == 0


def test_timestamptz_cell_with_z_suffix_accepted(
    tmp_path: Path, store_conn: duckdb.DuckDBPyConnection
) -> None:
    fixtures_dir = tmp_path / "universe"
    fixtures_dir.mkdir()
    csv_path = fixtures_dir / "prices_daily.csv"
    csv_path.write_text(
        "security_id,session,open,high,low,close,volume,known_at,ingested_at,source,provenance\n"
        "S1,2020-01-02,10.0,11.0,9.5,10.5,1000,2020-01-02T21:00:00Z,2020-01-02T21:00:00Z,"
        "test,bar\n"
    )

    load_universe_fixtures(store_conn, fixtures_dir)

    (count,) = store_conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert count == 1

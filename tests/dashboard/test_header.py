"""Tests for the page header's freshness line (dashboard standard principle 5, #185)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures
from streamlit.testing.v1 import AppTest

from tradepartner.calendar import session_close
from tradepartner.dashboard import header
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
T = session_close(date(2020, 6, 30)) + timedelta(hours=3)


def _run(conn: duckdb.DuckDBPyConnection, run_id: int, status: str, finished: datetime) -> None:
    insert_row(
        conn,
        "ingestion_runs",
        {
            "run_id": run_id,
            "started_at": finished - timedelta(minutes=5),
            "finished_at": finished,
            "status": status,
            "source": "alpaca",
            "mode": "daily",
            "rows_added": 0,
            "chunk_cursor": None,
            "message": None,
        },
    )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


def test_empty_store_is_never_updated(conn: duckdb.DuckDBPyConnection) -> None:
    assert header.store_freshness(conn, T) == header.Freshness(as_of=None, last_updated=None)
    assert header.when(None) == "never"


def test_as_of_is_the_latest_known_at_at_or_before_t(conn: duckdb.DuckDBPyConnection) -> None:
    load_universe_fixtures(conn, UNIVERSE_DIR)
    everything = header.store_freshness(conn, T).as_of
    earlier = header.store_freshness(conn, session_close(date(2019, 1, 2))).as_of
    assert everything is not None and earlier is not None
    assert earlier <= session_close(date(2019, 1, 2)) < everything <= T


def test_last_updated_is_the_latest_ok_run_finished_by_t(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    ok = datetime(2020, 6, 30, 22, tzinfo=UTC)
    _run(conn, 1, "ok", ok)
    _run(conn, 2, "failed", ok + timedelta(minutes=30))
    _run(conn, 3, "ok", T + timedelta(hours=1))  # finished after t
    assert header.store_freshness(conn, T).last_updated == ok


def test_when_formats_utc() -> None:
    value = datetime(2020, 6, 30, 22, 5, tzinfo=UTC)
    assert header.when(value) == "2020-06-30 22:05 UTC"


def test_render_writes_one_caption() -> None:
    at = AppTest.from_string(
        "from tradepartner.dashboard import header\n"
        "header.render_freshness(header.Freshness(None, None))\n",
        default_timeout=30,
    )
    at.run()
    assert [c.value for c in at.caption] == ["as of never · last updated never"]


def test_a_store_without_the_tables_has_no_freshness() -> None:
    conn = duckdb.connect(":memory:")
    assert header.store_freshness(conn, T) == header.Freshness(None, None)

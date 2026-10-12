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
from tradepartner.store.asof import ASSETS_SOURCE
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
T = session_close(date(2020, 6, 30)) + timedelta(hours=3)


def _run(conn: duckdb.DuckDBPyConnection, run_id: int, status: str, finished: datetime) -> None:
    _run_row(conn, run_id, status, finished, source="alpaca")


def _run_row(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    status: str,
    finished: datetime,
    *,
    source: str,
) -> None:
    _assets_run_row(
        conn,
        run_id,
        status,
        finished,
        source=source,
        cursor=None,
    )


def _assets_run_row(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    status: str,
    finished: datetime,
    *,
    source: str,
    cursor: datetime | None,
) -> None:
    insert_row(
        conn,
        "ingestion_runs",
        {
            "run_id": run_id,
            "started_at": finished - timedelta(minutes=5),
            "finished_at": finished,
            "status": status,
            "source": source,
            "mode": "daily",
            "rows_added": 0,
            "chunk_cursor": None if cursor is None else cursor.isoformat(),
            "message": None,
        },
    )


def _borrow_row(conn: duckdb.DuckDBPyConnection, known_at: datetime) -> None:
    """One `borrow_status` row in the fixture's shape (T171b): the daily
    snapshot's fetch time as `known_at`, an hour after the last bar."""
    insert_row(
        conn,
        "borrow_status",
        {
            "asset_id": "b0rr0w00-0000-4b0d-8000-0000000000ff",
            "symbol": "PINNED",
            "exchange": "NYSE",
            "status": "active",
            "tradable": True,
            "marginable": True,
            "shortable": True,
            "easy_to_borrow": True,
            "borrow_status": "easy_to_borrow",
            "fractionable": True,
            "present": True,
            "known_at": known_at,
            "ingested_at": known_at + timedelta(minutes=10),
            "source": "alpaca",
            "provenance": "snapshot",
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


def test_a_borrow_snapshot_leaves_both_reads_on_the_bars(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The borrow snapshot (#1506, T171b) is fetched after every other
    source and is read by nothing these two numbers describe, so neither
    its table nor its run row may make the store look fresher than its
    bars: "as of" stays the last bar's `known_at` even at a `t` an hour
    later, and "last updated" stays the bars' run."""
    load_universe_fixtures(conn, UNIVERSE_DIR)
    snapshot = datetime(2020, 6, 30, 21, tzinfo=UTC)  # close 20:00 plus an hour
    _borrow_row(conn, snapshot)
    _assets_run_row(conn, 9, "ok", snapshot, source=ASSETS_SOURCE, cursor=snapshot)
    before = header.store_freshness(conn, T)
    assert before.as_of == session_close(date(2020, 6, 30))
    assert before.last_updated is None
    # With a bars run at the same close, the later snapshot run still does
    # not become "last updated".
    _run(conn, 1, "ok", snapshot - timedelta(minutes=1))
    assert header.store_freshness(conn, T).last_updated == snapshot - timedelta(minutes=1)
    assert header.store_freshness(conn, T).as_of == session_close(date(2020, 6, 30))


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

"""Tests for tradepartner.store.schema and .db (T4).

Covers the "Timing and store" acceptance criteria in
docs/specs/data-foundation.md: common fact-table columns, tz-aware
`known_at` round-trip, the `known_at <= ingested_at` and provenance `CHECK`
constraints, uniqueness, `init_schema` idempotency, the read-only/write
connection helpers, and lock retry/rollback behavior.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import (
    StoreLockedError,
    ensure_tz_aware,
    insert_row,
    open_for_write,
    open_read_only,
    utc_now,
)

# Every fact table (spec "Data / interfaces" > Tables), i.e. every table
# except ingestion_runs and schema_version, which are not fact tables.
FACT_TABLES: tuple[str, ...] = (
    "securities",
    "listings",
    "classifications",
    "delistings",
    "prices_daily",
    "corporate_actions",
    "facts",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _minimal_row(table: str, *, known_at: datetime, ingested_at: datetime) -> dict[str, object]:
    """The smallest valid row for `table`, for constraint-focused tests
    that don't care about the table's own business columns."""
    common = {
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "test",
        "provenance": "bar",
    }
    business: dict[str, object]
    if table == "securities":
        business = {"security_id": "S1", "cik": "0000000001", "name": "Test Co", "benchmark": False}
    elif table == "listings":
        business = {
            "security_id": "S1",
            "ticker": "TST",
            "exchange": "NASDAQ",
            "class_title": None,
            "valid_from": "2020-01-02",
        }
    elif table == "classifications":
        business = {
            "security_id": "S1",
            "sic": 7372,
            "security_type": "common",
            "rule": "test-rule",
        }
    elif table == "delistings":
        business = {
            "security_id": "S1",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": known_at,
            "effective_on": "2020-01-12",
        }
    elif table == "prices_daily":
        business = {
            "security_id": "S1",
            "session": "2020-01-02",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        }
    elif table == "corporate_actions":
        business = {
            "security_id": "S1",
            "action_type": "split",
            "ex_date": "2020-01-02",
            "ratio_or_amount": 2.0,
        }
    elif table == "facts":
        business = {
            "security_id": "S1",
            "fact_name": "EntityCommonStockSharesOutstanding",
            "as_of_date": "2020-01-02",
            "class_member": None,
            "value": 1_000_000.0,
            "filing_accession": "0000000001-20-000001",
        }
    else:
        raise ValueError(f"no minimal row defined for {table!r}")
    return {**business, **common}


# --- schema shape -----------------------------------------------------


@pytest.mark.parametrize("table", FACT_TABLES)
def test_fact_table_has_common_columns(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    info = fixture_store.execute(f"PRAGMA table_info('{table}')").fetchall()
    columns = {row[1]: row for row in info}
    for col in ("known_at", "ingested_at", "source", "provenance"):
        assert col in columns, f"{table} is missing common column {col!r}"
        _, _, _col_type, not_null, _, _ = columns[col]
        assert not_null, f"{table}.{col} must be NOT NULL"
    assert columns["known_at"][2].upper().startswith("TIMESTAMP")
    assert columns["ingested_at"][2].upper().startswith("TIMESTAMP")


def test_ingestion_runs_is_not_a_fact_table(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """`ingestion_runs` has the spec's exact columns and none of the four
    common fact-table columns (it is not a fact table)."""
    info = fixture_store.execute("PRAGMA table_info('ingestion_runs')").fetchall()
    columns = {row[1] for row in info}
    assert columns == {
        "run_id",
        "started_at",
        "finished_at",
        "status",
        "source",
        "mode",
        "rows_added",
        "chunk_cursor",
        "message",
    }
    assert "known_at" not in columns
    assert "ingested_at" not in columns
    assert "provenance" not in columns


def test_all_table_names_present(fixture_store: duckdb.DuckDBPyConnection) -> None:
    tables = {
        row[0]
        for row in fixture_store.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert set(schema.TABLE_NAMES) <= tables


def test_init_schema_twice_is_a_no_op(fixture_store: duckdb.DuckDBPyConnection) -> None:
    schema.init_schema(fixture_store)
    schema.init_schema(fixture_store)
    (count,) = fixture_store.execute("SELECT COUNT(*) FROM schema_version").fetchone()  # type: ignore[misc]
    assert count == 1


# --- tz-aware known_at round trip and validation -----------------------


def test_known_at_round_trips_as_utc(fixture_store: duckdb.DuckDBPyConnection) -> None:
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    (known_at,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT known_at FROM prices_daily"
    ).fetchone()
    assert known_at.tzinfo is not None
    assert known_at.utcoffset() == timedelta(0)
    assert known_at == now


def test_ensure_tz_aware_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        ensure_tz_aware(datetime(2020, 1, 1), field="known_at")  # noqa: DTZ001


def test_ensure_tz_aware_accepts_aware_datetime() -> None:
    now = _now()
    assert ensure_tz_aware(now, field="known_at") == now


def test_insert_row_rejects_naive_known_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())
    with pytest.raises(ValueError, match="known_at"):
        insert_row(fixture_store, "prices_daily", row)
    (count,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert count == 0


def test_insert_row_rejects_naive_ingested_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=naive)
    with pytest.raises(ValueError, match="ingested_at"):
        insert_row(fixture_store, "prices_daily", row)


# --- CHECK / UNIQUE constraints ----------------------------------------


@pytest.mark.parametrize("table", FACT_TABLES)
def test_known_at_after_ingested_at_rejected(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now - timedelta(seconds=1))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_bad_provenance_rejected(fixture_store: duckdb.DuckDBPyConnection, table: str) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    row["provenance"] = "not-a-real-provenance"
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


def test_duplicate_bar_rejected(fixture_store: duckdb.DuckDBPyConnection) -> None:
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "prices_daily", dict(row))


def test_bar_with_later_known_at_is_a_new_row_not_rejected(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A revision (later `known_at`, same session) is a *new* row, per spec
    "Definitions" > Revision — the unique key includes `known_at`."""
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    revised = dict(row)
    revised["known_at"] = now + timedelta(days=1)
    revised["ingested_at"] = now + timedelta(days=1)
    revised["close"] = 99.0
    insert_row(fixture_store, "prices_daily", revised)
    (count,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert count == 2


# --- read-only / write connections --------------------------------------


def test_open_read_only_cannot_write(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    reader = open_read_only(settings)
    try:
        with pytest.raises(duckdb.Error):
            reader.execute("CREATE TABLE should_not_exist (a INTEGER)")
    finally:
        reader.close()


def test_open_for_write_commits_on_normal_exit(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "r1",
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "status": "ok",
                "source": "test",
                "mode": "backfill",
            },
        )

    with open_for_write(settings) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
    assert count == 1


def test_open_for_write_rolls_back_on_exception(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), open_for_write(settings) as conn:
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "r1",
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "status": "ok",
                "source": "test",
                "mode": "backfill",
            },
        )
        raise _Boom("simulated failure mid-chunk")

    with open_for_write(settings) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
    assert count == 0


def test_open_for_write_retries_then_raises_store_locked_error(tmp_path: Path) -> None:
    db_path = tmp_path / "locked.duckdb"
    duckdb.connect(database=str(db_path)).close()

    holder_code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={str(db_path)!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(5)
        conn.close()
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD"

        settings = Settings(_env_file=None, store={"path": str(db_path), "lock_retry_seconds": 1})
        start = time.monotonic()
        with pytest.raises(StoreLockedError), open_for_write(settings):
            pass  # pragma: no cover - lock acquisition should never succeed
        elapsed = time.monotonic() - start
        assert elapsed >= 1.0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_open_for_write_succeeds_once_lock_is_released(tmp_path: Path) -> None:
    db_path = tmp_path / "released.duckdb"
    duckdb.connect(database=str(db_path)).close()

    holder_code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={str(db_path)!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(0.5)
        conn.close()
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD"

        settings = Settings(_env_file=None, store={"path": str(db_path), "lock_retry_seconds": 5})
        with open_for_write(settings) as conn:
            schema.init_schema(conn)
    finally:
        proc.wait(timeout=10)

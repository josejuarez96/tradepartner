"""Tests for tradepartner.store.schema and .db (T4).

Covers the "Timing and store" acceptance criteria in
docs/specs/data-foundation.md: common fact-table columns, tz-aware
`known_at` round-trip, per-column-type validation, the `known_at <=
ingested_at` and per-table provenance `CHECK` constraints, uniqueness,
`init_schema` idempotency and version checking, the read-only/write
connection helpers (including same-process and cross-process locking),
and the connection's extension auto-install/auto-load settings.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import (
    StoreLockedError,
    _is_lock_error,
    configure_connection,
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
    that don't care about the table's own business columns.

    `provenance` defaults to the first value `table` allows (spec "Data /
    interfaces" > master column sources; `schema.TABLE_PROVENANCE_VALUES`)
    rather than a single hardcoded value, since the allowed set differs
    per table.
    """
    common = {
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "test",
        "provenance": schema.TABLE_PROVENANCE_VALUES[table][0],
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
            "valid_from": date(2020, 1, 2),
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
            "effective_on": date(2020, 1, 12),
        }
    elif table == "prices_daily":
        business = {
            "security_id": "S1",
            "session": date(2020, 1, 2),
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
            "ex_date": date(2020, 1, 2),
            "ratio_or_amount": 2.0,
        }
    elif table == "facts":
        business = {
            "security_id": "S1",
            "fact_name": "EntityCommonStockSharesOutstanding",
            "as_of_date": date(2020, 1, 2),
            "class_member": "",  # '' = no class dimension; see schema.py
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


def test_corporate_actions_has_a_nullable_announced_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Issue #83: the source's announcement time is stored next to
    `known_at`, so a first-seen stamp earlier than the proxy is checkable."""
    info = fixture_store.execute("PRAGMA table_info('corporate_actions')").fetchall()
    columns = {row[1]: row for row in info}
    assert "announced_at" in columns
    _, _, col_type, not_null, _, _ = columns["announced_at"]
    assert col_type.upper().startswith("TIMESTAMP")
    assert not not_null


def test_schema_version_is_2() -> None:
    """Version 2 adds `corporate_actions.announced_at` (issue #83)."""
    assert schema.CURRENT_SCHEMA_VERSION == 2


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


def test_init_schema_raises_on_schema_version_mismatch(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute(
        "UPDATE schema_version SET version = ?", [schema.CURRENT_SCHEMA_VERSION + 1]
    )
    with pytest.raises(schema.SchemaVersionError, match=str(schema.CURRENT_SCHEMA_VERSION)):
        schema.init_schema(fixture_store)


def test_configure_connection_disables_extension_autoinstall_and_autoload(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """DuckDB's own HTTP client (extension auto-install/auto-load) does
    not go through Python's `socket` module, so the no-network test
    fixture cannot block it directly; these settings do instead."""
    (autoinstall,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT current_setting('autoinstall_known_extensions')"
    ).fetchone()
    (autoload,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT current_setting('autoload_known_extensions')"
    ).fetchone()
    assert autoinstall is False
    assert autoload is False


# --- tz-aware known_at round trip and per-column-type validation -------


def test_known_at_round_trips_as_utc(fixture_store: duckdb.DuckDBPyConnection) -> None:
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    # `fixture_store` may already carry other securities' bars (T5's fixture
    # universe), so select this row by its own key rather than the whole
    # table.
    (known_at,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT known_at FROM prices_daily WHERE security_id = ? AND session = ? AND known_at = ?",
        [row["security_id"], row["session"], now],
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


def test_ensure_tz_aware_normalizes_non_utc_aware_datetime_to_utc() -> None:
    non_utc = datetime(2020, 1, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    result = ensure_tz_aware(non_utc, field="known_at")
    assert result == non_utc
    assert result.tzinfo is UTC


def test_insert_row_rejects_naive_known_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())
    with pytest.raises(ValueError, match="known_at"):
        insert_row(fixture_store, "prices_daily", row)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before


def test_insert_row_stores_none_as_null_in_a_nullable_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`announced_at` is nullable (#83): `None` binds as NULL."""
    row = {
        **_minimal_row("corporate_actions", known_at=_now(), ingested_at=_now()),
        "security_id": "S_NULL",
        "announced_at": None,
    }
    insert_row(fixture_store, "corporate_actions", row)
    (announced,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT announced_at FROM corporate_actions WHERE security_id = 'S_NULL'"
    ).fetchone()
    assert announced is None


def test_insert_row_none_in_a_not_null_timestamptz_column_is_refused(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    row = _minimal_row("prices_daily", known_at=None, ingested_at=_now())  # type: ignore[arg-type]
    with pytest.raises(duckdb.ConstraintException, match="known_at"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_naive_ingested_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=naive)
    with pytest.raises(ValueError, match="ingested_at"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_bare_date_for_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["known_at"] = date(2020, 1, 2)  # a session date, not an instant
    with pytest.raises(TypeError, match="TIMESTAMPTZ"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_naive_string_for_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A `str` is never accepted for a `TIMESTAMPTZ` column, whatever it
    contains — here, a naive-looking ISO string with no UTC offset, the
    kind DuckDB itself would silently interpret in the session `TimeZone`
    rather than reject (see the module docstring's caveat)."""
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["known_at"] = "2020-01-02T21:00:00"
    with pytest.raises(TypeError, match="TIMESTAMPTZ"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_datetime_for_date_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`datetime` is a `date` subclass; a DATE column must still reject
    it — a session is a calendar day, not an instant in some timezone."""
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["session"] = datetime(2020, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    with pytest.raises(TypeError, match="DATE"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_accepts_valid_date_for_date_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    insert_row(fixture_store, "prices_daily", row)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before + 1


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


@pytest.mark.parametrize("table", FACT_TABLES)
def test_provenance_restricted_to_table_specific_set(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    """A provenance value that's valid *somewhere* in the store but not
    for this particular table is still rejected (spec "Data /
    interfaces": each table's allowed provenance set is a strict subset
    of the five overall values)."""
    allowed = set(schema.TABLE_PROVENANCE_VALUES[table])
    disallowed = next(p for p in schema.PROVENANCE_VALUES if p not in allowed)
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    row["provenance"] = disallowed
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_duplicate_row_rejected(fixture_store: duckdb.DuckDBPyConnection, table: str) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    insert_row(fixture_store, table, row)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, dict(row))


def test_duplicate_undimensioned_fact_rejected(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """`facts.class_member` defaults to `''` (not NULL) specifically so
    that two undimensioned facts for the same key collide on the UNIQUE
    constraint — DuckDB treats NULL as distinct from NULL, which would
    otherwise let the same undimensioned fact be inserted twice."""
    now = _now()
    row = _minimal_row("facts", known_at=now, ingested_at=now)
    assert row["class_member"] == ""
    insert_row(fixture_store, "facts", row)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "facts", dict(row))


def test_bar_with_later_known_at_is_a_new_row_not_rejected(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A revision (later `known_at`, same session) is a *new* row, per spec
    "Definitions" > Revision — the unique key includes `known_at`."""
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    revised = dict(row)
    revised["known_at"] = now + timedelta(days=1)
    revised["ingested_at"] = now + timedelta(days=1)
    revised["close"] = 99.0
    insert_row(fixture_store, "prices_daily", revised)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before + 2


# --- lock-error detection -----------------------------------------------


def test_is_lock_error_detects_cross_process_lock_messages() -> None:
    assert _is_lock_error(duckdb.IOException('IO Error: Could not set lock on file "x"')) is True
    assert _is_lock_error(duckdb.IOException("IO Error: Conflicting lock is held in ...")) is True


def test_is_lock_error_rejects_unrelated_io_errors() -> None:
    assert (
        _is_lock_error(
            duckdb.IOException('IO Error: Cannot open file "x": No such file or directory')
        )
        is False
    )


# --- read-only / write connections --------------------------------------


def test_open_read_only_cannot_write(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    with open_read_only(settings) as reader, pytest.raises(duckdb.Error):
        reader.execute("CREATE TABLE should_not_exist (a INTEGER)")


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


def test_open_for_write_creates_missing_parent_directory(tmp_path: Path) -> None:
    nested_path = tmp_path / "a" / "b" / "store.duckdb"
    assert not nested_path.parent.exists()
    settings = Settings(_env_file=None, store={"path": str(nested_path)})

    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    assert nested_path.exists()


def test_open_for_write_does_not_retry_non_lock_io_errors(tmp_path: Path) -> None:
    """A real, non-lock `IOException` (here: `store.path` names an
    existing directory, not a file) must propagate immediately, not spin
    out the full `lock_retry_seconds` window before misreporting itself
    as `StoreLockedError` (probed against the pre-fix behavior)."""
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    settings = Settings(_env_file=None, store={"path": str(a_directory), "lock_retry_seconds": 5})

    start = time.monotonic()
    with pytest.raises(duckdb.IOException), open_for_write(settings):
        pass  # pragma: no cover - connect() itself raises
    elapsed = time.monotonic() - start

    assert elapsed < 1.0


def test_open_for_write_raises_store_locked_error_for_same_process_reader(
    settings: Settings,
) -> None:
    """DuckDB shares one database instance per path per process: a
    read-only connection already open in *this* process makes a write
    connection to the same path raise `duckdb.ConnectionException`, not a
    file-lock `IOException` — `open_for_write` converts that to
    `StoreLockedError` immediately rather than retrying (retrying can
    never help; this process itself is the blocker)."""
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    reader = duckdb.connect(database=settings.store.path, read_only=True)
    try:
        start = time.monotonic()
        with pytest.raises(StoreLockedError, match="different mode"), open_for_write(settings):
            pass  # pragma: no cover - connect() itself raises
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
    finally:
        reader.close()


def test_open_read_only_raises_store_locked_error_for_same_process_writer(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    writer = duckdb.connect(database=settings.store.path, read_only=False)
    try:
        with pytest.raises(StoreLockedError, match="different mode"), open_read_only(settings):
            pass  # pragma: no cover - connect() itself raises
    finally:
        writer.close()


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


# --- config: lock-retry backoff (T4 review fix) --------------------------


def test_store_config_lock_retry_backoff_defaults() -> None:
    """`store.lock_retry_initial_delay_seconds`/`.lock_retry_max_delay_seconds`
    (added to `StoreConfig` for this fix; see config.py's `StoreConfig`
    docstring for why T4 touches a T1 file here) back `open_for_write`'s
    retry backoff so it is config, not a hardcoded constant."""
    s = Settings(_env_file=None)
    assert s.store.lock_retry_initial_delay_seconds == pytest.approx(0.05)
    assert s.store.lock_retry_max_delay_seconds == pytest.approx(1.0)


def test_store_config_rejects_zero_initial_delay() -> None:
    """A zero delay is not a backoff (`Field(gt=0)`, third review pass)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_initial_delay_seconds": 0})


def test_store_config_rejects_zero_max_delay() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_max_delay_seconds": 0})


def test_store_config_rejects_negative_lock_retry_seconds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_seconds": -1})


def test_store_config_allows_zero_lock_retry_seconds() -> None:
    """Zero means "fail immediately, no retry" -- a legitimate choice,
    unlike a zero backoff delay."""
    s = Settings(_env_file=None, store={"lock_retry_seconds": 0})
    assert s.store.lock_retry_seconds == 0


def test_store_config_rejects_initial_delay_greater_than_max_delay() -> None:
    with pytest.raises(ValidationError, match="must be <="):
        Settings(
            _env_file=None,
            store={
                "lock_retry_initial_delay_seconds": 2.0,
                "lock_retry_max_delay_seconds": 1.0,
            },
        )


# --- column-type cache correctness (T4 review, third pass) --------------


def test_insert_row_before_init_schema_raises_clear_error() -> None:
    """Before `init_schema`, `prices_daily` has no columns at all --
    `insert_row` must say so plainly, not cache an empty result that would
    silently skip every type check forever (`_column_types` / third
    review pass)."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())

    with pytest.raises(ValueError, match="has no columns; run init_schema first"):
        insert_row(conn, "prices_daily", row)

    schema.init_schema(conn)

    # The pre-init lookup must not have poisoned the cache with an empty
    # result: a naive known_at is still rejected after init_schema runs.
    with pytest.raises(ValueError, match="known_at"):
        insert_row(conn, "prices_daily", row)


# --- canonical UTC binding (issue #43) -----------------------------------


def test_insert_row_binds_normalized_utc_value(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-UTC aware `known_at` is normalized to UTC before it is bound
    to the `INSERT`, not passed through as the caller's original tzinfo —
    one canonical form (issue #43). Verified two ways: the caller's `row`
    dict is left untouched, and the actual value handed to
    `DuckDBPyConnection.execute` has `tzinfo is UTC`."""
    eastern = timezone(timedelta(hours=-5))
    non_utc_known_at = datetime(2020, 1, 1, 7, 0, 0, tzinfo=eastern)
    non_utc_ingested_at = _now().astimezone(ZoneInfo("Asia/Tokyo"))
    row = _minimal_row("prices_daily", known_at=non_utc_known_at, ingested_at=non_utc_ingested_at)
    original_known_at = row["known_at"]

    captured: dict[str, list[object]] = {}
    connection_cls = type(fixture_store)
    real_execute = connection_cls.execute

    def spy_execute(self: duckdb.DuckDBPyConnection, sql: str, params: object = None) -> object:
        if params is not None and sql.startswith("INSERT INTO prices_daily"):
            captured["columns"] = list(row.keys())
            captured["params"] = list(params)  # type: ignore[arg-type]
        return real_execute(self, sql, params) if params is not None else real_execute(self, sql)

    monkeypatch.setattr(connection_cls, "execute", spy_execute)

    insert_row(fixture_store, "prices_daily", row)

    # The caller's Mapping must not be mutated in place.
    assert row["known_at"] is original_known_at
    assert row["known_at"].tzinfo == eastern  # type: ignore[union-attr]

    assert "params" in captured
    idx = captured["columns"].index("known_at")
    bound_known_at = captured["params"][idx]
    assert isinstance(bound_known_at, datetime)
    assert bound_known_at.tzinfo is UTC
    assert bound_known_at == non_utc_known_at  # same instant
    bound_ingested_at = captured["params"][captured["columns"].index("ingested_at")]
    assert isinstance(bound_ingested_at, datetime)
    assert bound_ingested_at.tzinfo is UTC
    assert bound_ingested_at == non_utc_ingested_at

    # And the instant actually stored matches the original instant.
    (stored,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT known_at FROM prices_daily WHERE security_id = ? AND session = ?",
        [row["security_id"], row["session"]],
    ).fetchone()
    assert stored == non_utc_known_at


def test_insert_row_raises_value_error_not_overflow_error_on_utc_overflow(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """An overflowing `TIMESTAMPTZ` value (e.g. a far-future sentinel with
    a non-zero offset) must surface as `ValueError` naming `table.field`,
    not the underlying `OverflowError` (issue #43)."""
    overflowing = datetime.max.replace(tzinfo=timezone(timedelta(hours=-5)))
    row = _minimal_row("prices_daily", known_at=overflowing, ingested_at=_now())
    with pytest.raises(ValueError, match=r"prices_daily\.known_at") as exc_info:
        insert_row(fixture_store, "prices_daily", row)
    assert not isinstance(exc_info.value, OverflowError)


def test_column_types_ignore_attached_database_with_same_named_table() -> None:
    """`_column_types` filters on `current_database()`/`current_schema()`
    so an `ATTACH`ed database's same-named table with a differently-typed
    same-named column cannot override the real column type (probed: an
    unfiltered query returns rows from both catalogs, and collapsing them
    into a dict silently picks whichever came last)."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)

    conn.execute("ATTACH ':memory:' AS other")
    conn.execute("CREATE TABLE other.prices_daily (known_at VARCHAR)")

    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())
    with pytest.raises(ValueError, match="known_at"):
        insert_row(conn, "prices_daily", row)

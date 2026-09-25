"""Tests for the schema-version-2 trial registry tables (Phase 3 T31).

Spec `docs/specs/backtest.md` req 9 and the "Registry and holdout"
acceptance line: the eight registry tables exist after `init_schema` on a
fresh store and after a write connection opens a version-1 store; the
version row is appended; no fact table changes; `REGISTRY_TABLE_NAMES` is
disjoint from `TABLE_NAMES`; a read-only open of a version-1 store raises
`RegistryNotInitialised`. The look-ahead harness
(`tests/lookahead/test_asof_invariance.py`) is left untouched and must
still pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import load_universe_fixtures

from tradepartner.store import schema
from tradepartner.store.db import configure_connection

_FIXTURES_UNIVERSE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "universe"

_EXPECTED_REGISTRY_TABLES = {
    "hypotheses",
    "trials",
    "trial_results",
    "trial_metrics",
    "trial_rebalances",
    "trial_equity",
    "trial_weights",
    "owner_decisions",
}

#: Every table that existed at version 1 except `schema_version`, which
#: the migration appends to by design.
_V1_TABLES_UNCHANGED = tuple(name for name in schema.TABLE_NAMES if name != "schema_version")

_V1_APPLIED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _make_version_1_store(path: Path) -> None:
    """Write a store file shaped as `init_schema` left it at version 1:
    the fact-table DDL, the fixture universe loaded, one `ingestion_runs`
    row and a single `schema_version` row for version 1."""
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        for ddl in schema._TABLE_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            [1, _V1_APPLIED_AT],
        )
        load_universe_fixtures(conn, _FIXTURES_UNIVERSE_DIR)
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, status, source, mode) "
            "VALUES ('run-1', ?, 'ok', 'fixture', 'backfill')",
            [_V1_APPLIED_AT],
        )
    finally:
        conn.close()


def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database()"
    ).fetchall()
    return {row[0] for row in rows}


def _versions(conn: duckdb.DuckDBPyConnection) -> list[tuple[int, datetime]]:
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT version, applied_at FROM schema_version ORDER BY version"
        ).fetchall()
    ]


def _fact_table_snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """DDL text, constraints and every row of each version-1 table."""
    snapshot: dict[str, Any] = {}
    for table in _V1_TABLES_UNCHANGED:
        (ddl,) = conn.execute(  # type: ignore[misc]
            "SELECT sql FROM duckdb_tables() "
            "WHERE database_name = current_database() AND table_name = ?",
            [table],
        ).fetchone()
        constraints = conn.execute(
            "SELECT constraint_type, constraint_text FROM duckdb_constraints() "
            "WHERE database_name = current_database() AND table_name = ? "
            "ORDER BY constraint_type, constraint_text",
            [table],
        ).fetchall()
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        snapshot[table] = (ddl, constraints, rows)
    return snapshot


@pytest.fixture
def version_1_store(tmp_path: Path) -> Path:
    path = tmp_path / "store_v1.duckdb"
    _make_version_1_store(path)
    return path


def test_registry_table_names_are_the_eight_spec_tables() -> None:
    assert set(schema.REGISTRY_TABLE_NAMES) == _EXPECTED_REGISTRY_TABLES
    assert len(schema.REGISTRY_TABLE_NAMES) == len(_EXPECTED_REGISTRY_TABLES)


def test_registry_table_names_disjoint_from_fact_table_names() -> None:
    assert set(schema.REGISTRY_TABLE_NAMES) & set(schema.TABLE_NAMES) == set()


def test_current_schema_version_is_2() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 2


def test_fresh_init_creates_every_table_at_version_2() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    assert _table_names(conn) == set(schema.TABLE_NAMES) | set(schema.REGISTRY_TABLE_NAMES)
    assert [version for version, _ in _versions(conn)] == [2]


def test_fresh_init_twice_keeps_one_version_row() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    schema.init_schema(conn)
    assert [version for version, _ in _versions(conn)] == [2]


def test_registry_tables_carry_no_fact_columns() -> None:
    """Like `ingestion_runs`: registry rows are not point-in-time facts."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    for table in schema.REGISTRY_TABLE_NAMES:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        assert columns, table
        assert columns.isdisjoint({"known_at", "ingested_at", "provenance"}), table


def test_write_open_of_version_1_store_migrates_to_version_2(version_1_store: Path) -> None:
    conn = duckdb.connect(str(version_1_store))
    try:
        assert _table_names(conn).isdisjoint(schema.REGISTRY_TABLE_NAMES)
        schema.init_schema(conn)
        assert set(schema.REGISTRY_TABLE_NAMES) <= _table_names(conn)
        versions = _versions(conn)
    finally:
        conn.close()
    assert [version for version, _ in versions] == [1, 2]
    assert versions[0][1] == _V1_APPLIED_AT
    assert versions[1][1] > _V1_APPLIED_AT


def test_migration_leaves_fact_tables_byte_identical(version_1_store: Path) -> None:
    conn = duckdb.connect(str(version_1_store))
    try:
        before = _fact_table_snapshot(conn)
        assert before["prices_daily"][2], "fixture universe should load price rows"
        schema.init_schema(conn)
        after = _fact_table_snapshot(conn)
    finally:
        conn.close()
    assert after == before


def test_migrated_store_reopens_without_a_further_version_row(version_1_store: Path) -> None:
    for _ in range(2):
        conn = duckdb.connect(str(version_1_store))
        try:
            schema.init_schema(conn)
        finally:
            conn.close()
    conn = duckdb.connect(str(version_1_store), read_only=True)
    try:
        assert [version for version, _ in _versions(conn)] == [1, 2]
    finally:
        conn.close()


def test_read_only_open_of_version_1_store_raises_and_changes_nothing(
    version_1_store: Path,
) -> None:
    conn = duckdb.connect(str(version_1_store), read_only=True)
    try:
        with pytest.raises(schema.RegistryNotInitialised, match="version 1"):
            schema.init_schema(conn)
    finally:
        conn.close()
    conn = duckdb.connect(str(version_1_store), read_only=True)
    try:
        assert _table_names(conn).isdisjoint(schema.REGISTRY_TABLE_NAMES)
        assert [version for version, _ in _versions(conn)] == [1]
    finally:
        conn.close()


def test_read_only_open_of_version_2_store_passes(tmp_path: Path) -> None:
    path = tmp_path / "store_v2.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        schema.init_schema(conn)
        assert [version for version, _ in _versions(conn)] == [2]
    finally:
        conn.close()


def test_read_only_open_of_uninitialised_store_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        with pytest.raises(schema.RegistryNotInitialised):
            schema.init_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_unknown_later_version_raises_schema_version_error(tmp_path: Path, read_only: bool) -> None:
    path = tmp_path / "store_v3.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.execute("INSERT INTO schema_version VALUES (3, ?)", [datetime.now(UTC)])
    conn.close()
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        with pytest.raises(schema.SchemaVersionError, match="3"):
            schema.init_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("table", "column", "bad_value"),
    [
        ("trials", "kind", "oos"),
        ("trial_results", "status", "done"),
        ("owner_decisions", "kind", "holdout_peek"),
    ],
)
def test_registry_enumerated_columns_reject_values_outside_the_spec_set(
    table: str, column: str, bad_value: str
) -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    row = dict(_VALID_ROWS[table])
    row[column] = bad_value
    with pytest.raises(duckdb.ConstraintException):
        _insert(conn, table, row)


@pytest.mark.parametrize("table", ["trials", "trial_results", "owner_decisions"])
def test_registry_valid_rows_insert(table: str) -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    _insert(conn, table, _VALID_ROWS[table])


def test_trial_results_holds_one_row_per_trial() -> None:
    """The schema backstop for "closing a trial twice raises" (T31b)."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    _insert(conn, "trial_results", _VALID_ROWS["trial_results"])
    with pytest.raises(duckdb.ConstraintException):
        _insert(conn, "trial_results", _VALID_ROWS["trial_results"])


_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

_VALID_ROWS: dict[str, dict[str, Any]] = {
    "trials": {
        "trial_id": 1,
        "hypothesis_id": 1,
        "kind": "in_sample",
        "started_at": _NOW,
        "start_session": "2018-01-02",
        "end_session": "2023-12-29",
        "data_cutoff": _NOW,
        "code_version": "abc123",
        "code_dirty": False,
        "synthetic": True,
        "holdout_repeat": False,
        "run_by": "test",
    },
    "trial_results": {
        "trial_id": 1,
        "finished_at": _NOW,
        "status": "ok",
    },
    "owner_decisions": {
        "decision_id": 1,
        "made_at": _NOW,
        "kind": "gap_signoff",
        "values_json": "{}",
        "reason": "test",
    },
}


def _insert(conn: duckdb.DuckDBPyConnection, table: str, row: dict[str, Any]) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(row.values()))

"""Tests for the trial registry tables (Phase 3 T31) and the schema
versions around them.

Spec `docs/specs/backtest.md` req 9 and the "Registry and holdout"
acceptance line, written when the registry was to be version 2. #83 took
version 2 first (`corporate_actions.announced_at`), so the registry is
version 3, and #108 (action identity) is version 4: the eight registry
tables exist after `init_schema` on a fresh store and after a write
connection opens a version-2 or version-3 store; each version row passed
through is appended; no fact table other than `corporate_actions` changes,
and that one keeps every row (identity unchanged); `REGISTRY_TABLE_NAMES`
is disjoint from `TABLE_NAMES`; a read-only open of an older store raises
and changes nothing. The look-ahead harness
(`tests/lookahead/test_asof_invariance.py`) is left untouched and must
still pass.
"""

from __future__ import annotations

import hashlib
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

#: Every fact table the version-4 migration leaves alone: all but
#: `corporate_actions` (rebuilt) and `schema_version` (appended to).
_UNTOUCHED_TABLES = tuple(
    name for name in schema.TABLE_NAMES if name not in ("corporate_actions", "schema_version")
)

_OLD_APPLIED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

#: `corporate_actions` as versions 2 and 3 created it (#83), frozen here:
#: the stores the version-4 migration reads were built from this text.
_V3_CORPORATE_ACTIONS_DDL = """
CREATE TABLE corporate_actions (
    security_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    announced_at TIMESTAMPTZ,
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    source VARCHAR NOT NULL,
    provenance VARCHAR NOT NULL,
    CHECK (provenance IN ('action')),
    CHECK (known_at <= ingested_at),
    UNIQUE (security_id, action_type, ex_date, known_at)
)
"""

_V3_ACTION_COLUMNS = (
    "security_id, action_type, ex_date, ratio_or_amount, announced_at, "
    "known_at, ingested_at, source, provenance"
)


def _make_old_store(path: Path, version: int) -> None:
    """Write a store file shaped as `init_schema` left it at `version`
    (1 and 2: fact tables only; 3: plus the registry, with one hypothesis
    row), with the fixture universe loaded, one `ingestion_runs` row and a
    single `schema_version` row. `corporate_actions` has its version-3
    shape and holds the fixture rows that shape can hold: those with no
    source id and not cancelled."""
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        for ddl in schema._TABLE_DDL:
            conn.execute(ddl)
        if version >= 3:
            for ddl in schema._REGISTRY_TABLE_DDL:
                conn.execute(ddl)
            _insert(conn, "hypotheses", _VALID_ROWS["hypotheses"])
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            [version, _OLD_APPLIED_AT],
        )
        load_universe_fixtures(conn, _FIXTURES_UNIVERSE_DIR)
        conn.execute("DROP INDEX corporate_actions_identity")
        conn.execute("ALTER TABLE corporate_actions RENAME TO corporate_actions_v4")
        conn.execute(_V3_CORPORATE_ACTIONS_DDL)
        conn.execute(
            f"INSERT INTO corporate_actions SELECT {_V3_ACTION_COLUMNS} "
            "FROM corporate_actions_v4 WHERE source_action_id = '' AND NOT cancelled"
        )
        conn.execute("DROP TABLE corporate_actions_v4")
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, status, source, mode) "
            "VALUES ('run-1', ?, 'ok', 'fixture', 'backfill')",
            [_OLD_APPLIED_AT],
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


def _table_snapshot(conn: duckdb.DuckDBPyConnection, tables: tuple[str, ...]) -> dict[str, Any]:
    """DDL text, constraints, indexes and every row of each table."""
    snapshot: dict[str, Any] = {}
    for table in tables:
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
        indexes = conn.execute(
            "SELECT index_name, sql FROM duckdb_indexes() "
            "WHERE database_name = current_database() AND table_name = ? ORDER BY index_name",
            [table],
        ).fetchall()
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        snapshot[table] = (ddl, constraints, indexes, rows)
    return snapshot


#: SHA-256 of `"".join(schema._TABLE_DDL)` at version 4 (#108's
#: `corporate_actions` on top of #83's fact tables).
_V4_DDL_SHA256 = "0815559c58066e74829be4956dea3f252eeddb03ab612cb3485b588c6f44b54a"


def test_version_4_fact_ddl_is_pinned() -> None:
    """Stores on disk are built from this DDL. A fact-table change goes to
    schema version 5 with its own migration, never an edit here (spec req 9)."""
    digest = hashlib.sha256("".join(schema._TABLE_DDL).encode()).hexdigest()
    assert digest == _V4_DDL_SHA256, (
        "version-4 fact-table DDL changed: bump to schema version 5 and add a "
        "migration instead of editing the fact tables"
    )


@pytest.fixture
def version_2_store(tmp_path: Path) -> Path:
    path = tmp_path / "store_v2.duckdb"
    _make_old_store(path, version=2)
    return path


@pytest.fixture
def version_3_store(tmp_path: Path) -> Path:
    path = tmp_path / "store_v3.duckdb"
    _make_old_store(path, version=3)
    return path


def test_registry_table_names_are_the_eight_spec_tables() -> None:
    assert set(schema.REGISTRY_TABLE_NAMES) == _EXPECTED_REGISTRY_TABLES
    assert len(schema.REGISTRY_TABLE_NAMES) == len(_EXPECTED_REGISTRY_TABLES)


def test_registry_table_names_disjoint_from_fact_table_names() -> None:
    assert set(schema.REGISTRY_TABLE_NAMES) & set(schema.TABLE_NAMES) == set()


def test_current_schema_version_is_4() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 4


def test_fresh_init_creates_every_table_at_version_4() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    assert _table_names(conn) == set(schema.TABLE_NAMES) | set(schema.REGISTRY_TABLE_NAMES)
    assert [version for version, _ in _versions(conn)] == [4]


def test_fresh_init_twice_keeps_one_version_row() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    schema.init_schema(conn)
    assert [version for version, _ in _versions(conn)] == [4]


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


def test_write_open_of_version_2_store_migrates_to_version_4(version_2_store: Path) -> None:
    conn = duckdb.connect(str(version_2_store))
    try:
        assert _table_names(conn).isdisjoint(schema.REGISTRY_TABLE_NAMES)
        schema.init_schema(conn)
        assert set(schema.REGISTRY_TABLE_NAMES) <= _table_names(conn)
        versions = _versions(conn)
    finally:
        conn.close()
    assert [version for version, _ in versions] == [2, 3, 4]
    assert versions[0][1] == _OLD_APPLIED_AT
    assert versions[1][1] > _OLD_APPLIED_AT
    assert versions[2][1] == versions[1][1]


def test_write_open_of_version_3_store_migrates_to_version_4(version_3_store: Path) -> None:
    conn = duckdb.connect(str(version_3_store))
    try:
        before = _table_snapshot(conn, schema.REGISTRY_TABLE_NAMES)
        assert before["hypotheses"][3], "the version-3 store should hold a registry row"
        schema.init_schema(conn)
        after = _table_snapshot(conn, schema.REGISTRY_TABLE_NAMES)
        versions = _versions(conn)
    finally:
        conn.close()
    assert after == before
    assert [version for version, _ in versions] == [3, 4]
    assert versions[0][1] == _OLD_APPLIED_AT
    assert versions[1][1] > _OLD_APPLIED_AT


@pytest.mark.parametrize("version", [2, 3])
def test_migration_leaves_other_fact_tables_byte_identical(tmp_path: Path, version: int) -> None:
    path = tmp_path / "store.duckdb"
    _make_old_store(path, version=version)
    conn = duckdb.connect(str(path))
    try:
        before = _table_snapshot(conn, _UNTOUCHED_TABLES)
        assert before["prices_daily"][3], "fixture universe should load price rows"
        schema.init_schema(conn)
        after = _table_snapshot(conn, _UNTOUCHED_TABLES)
    finally:
        conn.close()
    assert after == before


@pytest.mark.parametrize("version", [2, 3])
def test_migration_keeps_every_action_row_as_an_id_less_live_row(
    tmp_path: Path, version: int
) -> None:
    """Each version-3 row keeps its values and gets `source_action_id = ''`,
    `cancelled = FALSE`: its identity stays `(security_id, action_type,
    ex_date)`, exactly what it was before #108."""
    path = tmp_path / "store.duckdb"
    _make_old_store(path, version=version)
    conn = duckdb.connect(str(path))
    try:
        before = conn.execute(
            f"SELECT {_V3_ACTION_COLUMNS} FROM corporate_actions ORDER BY ALL"
        ).fetchall()
        assert before, "fixture universe should load action rows"
        schema.init_schema(conn)
        after = conn.execute(
            f"SELECT {_V3_ACTION_COLUMNS}, source_action_id, cancelled "
            "FROM corporate_actions ORDER BY ALL"
        ).fetchall()
    finally:
        conn.close()
    assert after == [(*row, "", False) for row in before]


@pytest.mark.parametrize("version", [2, 3])
def test_migrated_corporate_actions_matches_a_fresh_store(tmp_path: Path, version: int) -> None:
    """Same DDL text, constraints and identity index as `init_schema`
    creates on a fresh store, so a migrated store refuses what a fresh
    one refuses."""
    path = tmp_path / "store.duckdb"
    _make_old_store(path, version=version)
    conn = duckdb.connect(str(path))
    try:
        schema.init_schema(conn)
        migrated = _table_snapshot(conn, ("corporate_actions",))["corporate_actions"][:3]
    finally:
        conn.close()
    fresh_conn = duckdb.connect(":memory:")
    schema.init_schema(fresh_conn)
    fresh = _table_snapshot(fresh_conn, ("corporate_actions",))["corporate_actions"][:3]
    assert migrated == fresh


def test_migration_inside_a_caller_transaction_commits_with_it(version_3_store: Path) -> None:
    """`open_for_write` calls `init_schema` inside its transaction; the
    migration joins it rather than committing on its own, so a rollback
    leaves the version-3 store untouched."""
    conn = duckdb.connect(str(version_3_store))
    try:
        before = _table_snapshot(conn, ("corporate_actions", "schema_version"))
        conn.execute("BEGIN TRANSACTION")
        schema.init_schema(conn)
        conn.execute("ROLLBACK")
        after = _table_snapshot(conn, ("corporate_actions", "schema_version"))
    finally:
        conn.close()
    assert after == before


def test_failed_migration_leaves_the_store_unchanged(
    version_3_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On an autocommit connection the migration runs in its own
    transaction: a failure after the rebuild (here, stamping the version
    row) rolls the rebuild back too."""

    def broken_clock() -> datetime:
        raise RuntimeError("clock failed")

    conn = duckdb.connect(str(version_3_store))
    try:
        before = _table_snapshot(conn, ("corporate_actions", "schema_version"))
        monkeypatch.setattr(schema, "utc_now", broken_clock)
        with pytest.raises(RuntimeError, match="clock failed"):
            schema.init_schema(conn)
        after = _table_snapshot(conn, ("corporate_actions", "schema_version"))
    finally:
        conn.close()
    assert after == before


@pytest.mark.parametrize("version", [2, 3])
def test_migrated_store_reopens_without_a_further_version_row(tmp_path: Path, version: int) -> None:
    path = tmp_path / "store.duckdb"
    _make_old_store(path, version=version)
    for _ in range(2):
        conn = duckdb.connect(str(path))
        try:
            schema.init_schema(conn)
        finally:
            conn.close()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        schema.init_schema(conn)
        assert [version for version, _ in _versions(conn)] == list(range(version, 5))
    finally:
        conn.close()


def test_read_only_open_of_version_2_store_raises_and_changes_nothing(
    version_2_store: Path,
) -> None:
    conn = duckdb.connect(str(version_2_store), read_only=True)
    try:
        with pytest.raises(schema.RegistryNotInitialised, match="version 2"):
            schema.init_schema(conn)
    finally:
        conn.close()
    conn = duckdb.connect(str(version_2_store), read_only=True)
    try:
        assert _table_names(conn).isdisjoint(schema.REGISTRY_TABLE_NAMES)
        assert [version for version, _ in _versions(conn)] == [2]
    finally:
        conn.close()


def test_read_only_open_of_version_3_store_raises_and_changes_nothing(
    version_3_store: Path,
) -> None:
    """A read-only connection never migrates, and the as-of reads need the
    version-4 columns, so a version-3 store is refused with the fix named."""
    conn = duckdb.connect(str(version_3_store), read_only=True)
    try:
        with pytest.raises(schema.SchemaVersionError, match=r"version 3.*migrate"):
            schema.init_schema(conn)
        assert [version for version, _ in _versions(conn)] == [3]
    finally:
        conn.close()


def test_read_only_open_of_version_4_store_passes(tmp_path: Path) -> None:
    path = tmp_path / "store_v4.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        schema.init_schema(conn)
        assert [version for version, _ in _versions(conn)] == [4]
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
    path = tmp_path / "store_v5.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.execute("INSERT INTO schema_version VALUES (5, ?)", [datetime.now(UTC)])
    conn.close()
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        with pytest.raises(schema.SchemaVersionError, match="5"):
            schema.init_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_version_1_store_raises_schema_version_error(tmp_path: Path, read_only: bool) -> None:
    """#83 gave version 1 no migration (no store had data); the registry
    keeps that: a version-1 store is rebuilt, never migrated or read."""
    path = tmp_path / "store_v1.duckdb"
    _make_old_store(path, version=1)
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        with pytest.raises(schema.SchemaVersionError, match="1"):
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


def test_trials_row_without_a_resolvable_cutoff_inserts() -> None:
    """A refused run whose requested end is off the trading calendar is still
    a trial: its row must insert with no `data_cutoff`."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    row = dict(_VALID_ROWS["trials"])
    row["end_session"] = "2099-12-31"
    row["data_cutoff"] = None
    _insert(conn, "trials", row)


def test_trial_results_holds_one_row_per_trial() -> None:
    """The schema backstop for "closing a trial twice raises" (T31b)."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    _insert(conn, "trial_results", _VALID_ROWS["trial_results"])
    with pytest.raises(duckdb.ConstraintException):
        _insert(conn, "trial_results", _VALID_ROWS["trial_results"])


_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

_VALID_ROWS: dict[str, dict[str, Any]] = {
    "hypotheses": {
        "hypothesis_id": 1,
        "slug": "h1-momentum-12-1",
        "family": "momentum",
        "title": "12-1 momentum",
        "doc_path": "docs/hypotheses/h1-momentum-12-1.md",
        "doc_sha256": "0" * 64,
        "params_json": "{}",
        "params_sha256": "0" * 64,
        "in_sample_start": "2017-01-31",
        "holdout_start": "2024-01-01",
        "holdout_end": "2026-09-30",
        "registered_at": _NOW,
        "registered_by": "owner",
    },
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

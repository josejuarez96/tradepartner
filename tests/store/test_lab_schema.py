"""Tests for the strategy-lab DDL as functions (#1142, strategy-lab plan T101,
choice 2; spec "Data / interfaces" > Tables): `apply_lab_schema`, the two
staging rebuilds that widen `trial_results.status` and `owner_decisions.kind`,
`is_lab_initialised` by table presence, `LabNotInitialised`, the fixture
marker, and the `lab_store` fixture."""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, get_args

import duckdb
import pytest
from conftest import mark_pre_lab

from tradepartner.backtest import sweep
from tradepartner.store import lab_schema, schema
from tradepartner.store.db import configure_connection, insert_row

REPO = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

_PHASE3_STATUSES = ("ok", "failed", "refused_window", "refused_holdout", "refused_gap")
_PHASE3_DECISION_KINDS = ("gap_signoff", "gap_override", "holdout_spend")


def _fresh_store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


def _tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database() "
            "AND schema_name = current_schema()"
        ).fetchall()
    }


def _rows_in_order(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[Any, ...]]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[Any, ...]]:
    return conn.execute(f"PRAGMA table_info('{table}')").fetchall()


def _checksum(conn: duckdb.DuckDBPyConnection, table: str) -> tuple[int, str]:
    rows = sorted(repr(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall())
    return len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[Any, ...]]:
    """Every table's DDL, columns and rows in insertion order."""
    ddl = dict(
        conn.execute(
            "SELECT table_name, sql FROM duckdb_tables() WHERE database_name = "
            "current_database() AND schema_name = current_schema()"
        ).fetchall()
    )
    return {
        table: (sql, _columns(conn, table), _rows_in_order(conn, table))
        for table, sql in ddl.items()
    }


def _with_phase3_rows(conn: duckdb.DuckDBPyConnection) -> None:
    """One hypothesis, one trial per Phase 3 status (with `n_research` set on
    some), every registry table holding rows, and one decision per kind."""
    insert_row(
        conn,
        "hypotheses",
        {
            "hypothesis_id": 1,
            "slug": "h1-momentum",
            "family": "momentum",
            "title": "H1",
            "doc_path": "docs/hypotheses/h1.md",
            "doc_sha256": "a" * 64,
            "params_json": '{"strategy.formation_months": 12}',
            "params_sha256": "b" * 64,
            "in_sample_start": date(2020, 8, 31),
            "holdout_start": date(2024, 1, 1),
            "holdout_end": date(2026, 9, 30),
            "registered_at": AT,
            "registered_by": "jose",
        },
    )
    for trial_id, status in enumerate(_PHASE3_STATUSES, start=1):
        insert_row(
            conn,
            "trials",
            {
                "trial_id": trial_id,
                "hypothesis_id": 1,
                "kind": "in_sample",
                "started_at": AT,
                "start_session": date(2020, 8, 31),
                "end_session": date(2023, 12, 29),
                "code_version": "abc123",
                "synthetic": False,
                "run_by": "jose",
            },
        )
        ok = status == "ok"
        insert_row(
            conn,
            "trial_results",
            {
                "trial_id": trial_id,
                "finished_at": AT,
                "status": status,
                "message": None if ok else f"{status} because",
                "n_trials": trial_id if ok else None,
                "sharpe_variance": 0.0123456789 if ok else None,
                "dsr": 0.987654321 if ok else None,
                "dsr_excess": -0.1 if ok else None,
                "dsr_basis": "excess" if ok else None,
                "red_flag": False if ok else None,
                "n_research": trial_id if trial_id % 2 else None,
            },
        )
    conn.execute(
        "INSERT INTO trial_metrics VALUES (1, 'strategy', 15.0, 'sharpe', 0.42), "
        "(1, 'spy', 15.0, 'sharpe', NULL)"
    )
    conn.execute(
        "INSERT INTO trial_equity VALUES (1, 'strategy', 15.0, DATE '2020-09-30', 1.01, 0.0)"
    )
    conn.execute("INSERT INTO trial_weights VALUES (1, DATE '2020-10-01', 'S1', 0.5, 10.0, 5.0)")
    for decision_id, kind in enumerate(_PHASE3_DECISION_KINDS, start=1):
        insert_row(
            conn,
            "owner_decisions",
            {
                "decision_id": decision_id,
                "made_at": AT,
                "kind": kind,
                "hypothesis_id": 1,
                "trial_id": None if kind == "holdout_spend" else 1,
                "values_json": '{"x": 1}',
                "reason": f"owner said {kind}",
            },
        )


# --- apply_lab_schema ---------------------------------------------------------


def test_every_lab_table_exists_after_apply_on_a_fresh_store() -> None:
    conn = _fresh_store()
    assert not set(lab_schema.LAB_TABLE_NAMES) & _tables(conn)
    lab_schema.apply_lab_schema(conn)
    assert set(lab_schema.LAB_TABLE_NAMES) <= _tables(conn)
    assert lab_schema.is_lab_initialised(conn)


def test_lab_table_names_are_the_eight_spec_tables() -> None:
    assert set(lab_schema.LAB_TABLE_NAMES) == {
        "family_rules",
        "sweeps",
        "sweep_variants",
        "hypothesis_fingerprints",
        "pre_lab_hypotheses",
        "sweep_runs",
        "sweep_trials",
        "store_markers",
    }
    assert len(set(lab_schema.LAB_TABLE_NAMES)) == len(lab_schema.LAB_TABLE_NAMES)


def test_lab_tables_carry_the_spec_columns() -> None:
    """Column names and order as the spec's Data / interfaces lists them."""
    conn = _fresh_store()
    lab_schema.apply_lab_schema(conn)
    expected = {
        "family_rules": "family, first_hypothesis_id, parent_family, holdout_start, "
        "holdout_end, in_sample_start, fixed_params_json, fixed_params_sha256, "
        "max_family_holdout_spends, max_family_promotions, min_sharpe_variance_annual, "
        "axis_lattice_json, sr_star_seed_annual, registered_at",
        "sweeps": "sweep_id, slug, family, title, doc_path, doc_sha256, grid_json, "
        "grid_sha256, n_variants, selection_statistic, expected_excess_cagr_spy_pp, "
        "expected_range_lo_pp, expected_range_hi_pp, promote_at_least, retire_below, "
        "max_promotions, min_dsr_floor, max_failures_per_variant, axis_lattice_json, "
        "in_sample_start, holdout_start, holdout_end, registered_at, registered_by",
        "sweep_variants": "sweep_id, variant_index, hypothesis_id, fingerprint, "
        "variant_params_json",
        "hypothesis_fingerprints": "hypothesis_id, fingerprint",
        "pre_lab_hypotheses": "hypothesis_id, marked_at",
        "sweep_runs": "sweep_run_id, sweep_id, started_at, finished_at, "
        "time_budget_minutes, n_declared, n_planned, n_ok, n_failed, n_terminal_failed, "
        "seconds, code_tree_sha256, code_version, code_dirty, n_trials_at_end, "
        "sr_star_annual_at_end, completed, run_by, note",
        "sweep_trials": "sweep_run_id, trial_id, read_group_index, seconds",
        "store_markers": "kind, written_at, written_by",
    }
    for table, columns in expected.items():
        assert [row[1] for row in _columns(conn, table)] == columns.split(", "), table


def test_apply_twice_changes_nothing() -> None:
    conn = _fresh_store()
    _with_phase3_rows(conn)
    lab_schema.apply_lab_schema(conn)
    first = _snapshot(conn)
    lab_schema.apply_lab_schema(conn)
    assert _snapshot(conn) == first


def test_rebuild_keeps_every_phase3_row_byte_identical() -> None:
    """Every pre-rebuild row of `trial_results` and `owner_decisions` (the two
    rebuilt tables) is identical afterwards, in insertion order and with every
    column (`n_research` included); every other registry table's row count and
    checksum is unchanged."""
    conn = _fresh_store()
    _with_phase3_rows(conn)
    rebuilt = ("trial_results", "owner_decisions")
    before_rows = {table: _rows_in_order(conn, table) for table in rebuilt}
    before_columns = {table: _columns(conn, table) for table in rebuilt}
    others = [t for t in schema.REGISTRY_TABLE_NAMES if t not in rebuilt]
    before_others = {table: _checksum(conn, table) for table in others}
    assert all(before_rows.values())
    assert all(before_others[t][0] > 0 for t in others if t != "trial_rebalances")

    lab_schema.apply_lab_schema(conn)

    for table in rebuilt:
        assert _rows_in_order(conn, table) == before_rows[table], table
        assert _columns(conn, table) == before_columns[table], table
    assert {table: _checksum(conn, table) for table in others} == before_others


def test_rebuild_widens_the_two_enumerations() -> None:
    conn = _fresh_store()
    _with_phase3_rows(conn)
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "INSERT INTO trial_results (trial_id, finished_at, status) VALUES (99, ?, ?)",
            [AT, "refused_variant"],
        )
    lab_schema.apply_lab_schema(conn)
    conn.execute(
        "INSERT INTO trial_results (trial_id, finished_at, status) VALUES (99, ?, ?)",
        [AT, "refused_variant"],
    )
    for decision_id, kind in enumerate(("promotion", "sweep_retired"), start=10):
        conn.execute(
            "INSERT INTO owner_decisions (decision_id, made_at, kind, values_json, reason) "
            "VALUES (?, ?, ?, '{}', 'r')",
            [decision_id, AT, kind],
        )
    for table, column, value in (
        ("trial_results", "status", "made_up"),
        ("owner_decisions", "kind", "made_up"),
    ):
        key = "trial_id" if table == "trial_results" else "decision_id"
        when = "finished_at" if table == "trial_results" else "made_at"
        extra = "" if table == "trial_results" else ", values_json, reason"
        extra_values = "" if table == "trial_results" else ", '{}', 'r'"
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                f"INSERT INTO {table} ({key}, {when}, {column}{extra}) "
                f"VALUES (100, ?, ?{extra_values})",
                [AT, value],
            )
    # The primary key survives the rebuild.
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "INSERT INTO trial_results (trial_id, finished_at, status) VALUES (1, ?, 'ok')",
            [AT],
        )


def test_rebuild_keeps_a_value_another_version_added() -> None:
    """A store whose enumeration already holds a value the lab does not know
    (a later version's widening) keeps it: the lab appends, never replaces."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    conn.execute(
        "CREATE TABLE trial_results (trial_id BIGINT PRIMARY KEY, finished_at TIMESTAMPTZ "
        "NOT NULL, status VARCHAR NOT NULL, CHECK (status IN ('ok', 'other_plan')))"
    )
    conn.execute(
        "CREATE TABLE owner_decisions (decision_id BIGINT PRIMARY KEY, kind VARCHAR NOT "
        "NULL, CHECK (kind IN ('gap_signoff', 'promotion', 'sweep_retired')))"
    )
    conn.execute("INSERT INTO trial_results VALUES (1, ?, 'other_plan')", [AT])
    owner_ddl = conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = 'owner_decisions'"
    ).fetchone()
    lab_schema.apply_lab_schema(conn)
    conn.execute("INSERT INTO trial_results VALUES (2, ?, 'refused_variant')", [AT])
    conn.execute("INSERT INTO trial_results VALUES (3, ?, 'other_plan')", [AT])
    assert _rows_in_order(conn, "trial_results")[0] == (1, AT, "other_plan")
    # Already widened: not rebuilt.
    assert (
        conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE table_name = 'owner_decisions'"
        ).fetchone()
        == owner_ddl
    )


def test_rebuild_leaves_another_columns_check_alone() -> None:
    """A later version's `CHECK` on another column (T97's `sharpe_unit`, say)
    neither blocks the widening nor is lost by it."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    conn.execute(
        "CREATE TABLE trial_results (trial_id BIGINT PRIMARY KEY, status VARCHAR NOT NULL, "
        "sharpe_unit VARCHAR, CHECK (status IN ('ok')), "
        "CHECK (sharpe_unit IN ('monthly', 'annual')))"
    )
    conn.execute("CREATE TABLE owner_decisions (decision_id BIGINT, kind VARCHAR)")
    conn.execute("ALTER TABLE owner_decisions ADD COLUMN x INTEGER")
    with pytest.raises(schema.SchemaVersionError):
        lab_schema.apply_lab_schema(conn)
    conn.execute("DROP TABLE owner_decisions")
    conn.execute(
        "CREATE TABLE owner_decisions (decision_id BIGINT, kind VARCHAR, "
        "CHECK (kind IN ('gap_signoff')))"
    )
    conn.execute("INSERT INTO trial_results VALUES (1, 'ok', 'annual')")
    lab_schema.apply_lab_schema(conn)
    conn.execute("INSERT INTO trial_results VALUES (2, 'refused_variant', 'monthly')")
    with pytest.raises(duckdb.ConstraintException):
        conn.execute("INSERT INTO trial_results VALUES (3, 'ok', 'weekly')")
    assert _rows_in_order(conn, "trial_results")[0] == (1, "ok", "annual")


def test_rebuild_refuses_an_unknown_check_shape() -> None:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    conn.execute(
        "CREATE TABLE trial_results (trial_id BIGINT, status VARCHAR, CHECK (status <> 'x'))"
    )
    conn.execute("CREATE TABLE owner_decisions (decision_id BIGINT, kind VARCHAR)")
    with pytest.raises(schema.SchemaVersionError):
        lab_schema.apply_lab_schema(conn)
    # One transaction: nothing was created.
    assert not set(lab_schema.LAB_TABLE_NAMES) & _tables(conn)


def test_lab_enumeration_checks() -> None:
    conn = _fresh_store()
    lab_schema.apply_lab_schema(conn)
    with pytest.raises(duckdb.ConstraintException):
        conn.execute("INSERT INTO store_markers VALUES ('real', ?, 'me')", [AT])
    assert set(lab_schema.SELECTION_STATISTICS) == set(get_args(sweep.SelectionStatistic))
    with pytest.raises(duckdb.ConstraintException, match="selection_statistic"):
        conn.execute(
            "INSERT INTO sweeps VALUES (1, 's', 'momentum', 't', 'p', 'h', '{}', 'g', 4, "
            "'best_guess', 1.0, 0.0, 2.0, 0.5, 0.0, 1, 0.5, 2, '{}', DATE '2020-08-31', "
            "DATE '2024-01-01', DATE '2026-09-30', ?, 'me')",
            [AT],
        )


# --- is_lab_initialised, LabNotInitialised -------------------------------------


def test_init_schema_does_not_initialise_the_lab() -> None:
    conn = _fresh_store()
    assert not lab_schema.is_lab_initialised(conn)


def test_is_lab_initialised_false_on_a_plain_fixture_store(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert "store_markers" in _tables(fixture_store)
    assert not lab_schema.is_lab_initialised(fixture_store)


def test_is_lab_initialised_true_on_lab_store(lab_store: duckdb.DuckDBPyConnection) -> None:
    assert lab_schema.is_lab_initialised(lab_store)
    lab_schema.require_lab(lab_store)


def test_is_lab_initialised_false_when_one_table_is_missing() -> None:
    conn = _fresh_store()
    lab_schema.apply_lab_schema(conn)
    conn.execute("DROP TABLE sweep_trials")
    assert not lab_schema.is_lab_initialised(conn)


def test_lab_read_on_a_plain_fixture_store_raises(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(lab_schema.LabNotInitialised):
        lab_schema.require_lab(fixture_store)
    with pytest.raises(lab_schema.LabNotInitialised):
        mark_pre_lab(fixture_store, 1)


def test_mark_pre_lab_writes_the_row(lab_store: duckdb.DuckDBPyConnection) -> None:
    mark_pre_lab(lab_store, 7)
    assert lab_store.execute("SELECT hypothesis_id FROM pre_lab_hypotheses").fetchall() == [(7,)]


def test_lab_table_names_disjoint_from_every_other_table_list() -> None:
    lab = set(lab_schema.LAB_TABLE_NAMES)
    for names in (
        schema.TABLE_NAMES,
        schema.REGISTRY_TABLE_NAMES,
        schema.JOURNAL_TABLE_NAMES,
        schema.LATER_JOURNAL_TABLE_NAMES,
        schema.MASTER_CHECK_TABLE_NAMES,
        schema.RESEARCH_TABLE_NAMES,
    ):
        assert not lab & set(names)


# --- fixture marker ------------------------------------------------------------


def test_fixture_marker_on_both_fixtures(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert lab_schema.has_fixture_marker(fixture_store)
    (row,) = fixture_store.execute("SELECT kind, written_by FROM store_markers").fetchall()
    assert row == ("fixture", "tests/conftest.py:load_universe_fixtures")


def test_fixture_marker_on_lab_store(lab_store: duckdb.DuckDBPyConnection) -> None:
    assert lab_schema.has_fixture_marker(lab_store)
    assert lab_store.execute("SELECT COUNT(*) FROM store_markers").fetchone() == (1,)


def test_fixture_marker_on_fixture_store_path(fixture_store_path: Path) -> None:
    conn = duckdb.connect(str(fixture_store_path), read_only=True)
    try:
        assert lab_schema.has_fixture_marker(conn)
    finally:
        conn.close()


def test_no_fixture_marker_on_a_copy_with_the_row_deleted(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "copy.duckdb"
    shutil.copy(fixture_store_path, copy)
    conn = duckdb.connect(str(copy))
    try:
        conn.execute("DELETE FROM store_markers")
        assert not lab_schema.has_fixture_marker(conn)
    finally:
        conn.close()


def test_no_fixture_marker_without_the_table() -> None:
    conn = _fresh_store()
    assert "store_markers" not in _tables(conn)
    assert not lab_schema.has_fixture_marker(conn)


def test_write_fixture_marker_is_idempotent() -> None:
    conn = _fresh_store()
    lab_schema.create_store_markers(conn)
    lab_schema.create_store_markers(conn)
    lab_schema.write_fixture_marker(conn, "a")
    lab_schema.write_fixture_marker(conn, "b")
    assert conn.execute("SELECT kind, written_by FROM store_markers").fetchall() == [
        ("fixture", "a")
    ]


# --- text checks ---------------------------------------------------------------

_WRITE_STORE_MARKERS = re.compile(
    r"\b(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE|COPY)\s+"
    r"store_markers\b",
    re.IGNORECASE,
)


def _python_files() -> list[Path]:
    roots = (REPO / "src", REPO / "tests", REPO / "scripts")
    return sorted(p for root in roots for p in root.rglob("*.py"))


def test_store_markers_written_only_by_write_fixture_marker() -> None:
    this = Path(__file__).resolve()
    writers = {
        path.relative_to(REPO).as_posix()
        for path in _python_files()
        if path.resolve() != this and _WRITE_STORE_MARKERS.search(path.read_text())
    }
    assert writers == {"src/tradepartner/store/lab_schema.py"}
    source = (REPO / "src/tradepartner/store/lab_schema.py").read_text()
    tree = ast.parse(source)
    writing_functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and _WRITE_STORE_MARKERS.search(ast.get_source_segment(source, node) or "")
    }
    assert writing_functions == {"write_fixture_marker"}


def test_write_fixture_marker_called_only_from_load_universe_fixtures() -> None:
    this = Path(__file__).resolve()
    callers: set[tuple[str, str]] = set()
    for path in _python_files():
        if path.resolve() == this:
            continue
        source = path.read_text()
        if "write_fixture_marker" not in source:
            continue
        tree = ast.parse(source)
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute | ast.Name)
                    and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
                    == "write_fixture_marker"
                ):
                    callers.add((path.relative_to(REPO).as_posix(), func.name))
    assert callers == {("tests/conftest.py", "load_universe_fixtures")}


def test_schema_py_does_not_call_the_lab_ddl() -> None:
    """No call from `init_schema` and no edit to `schema.py` until T113."""
    source = (REPO / "src/tradepartner/store/schema.py").read_text()
    assert "lab_schema" not in source
    assert "apply_lab_schema" not in source

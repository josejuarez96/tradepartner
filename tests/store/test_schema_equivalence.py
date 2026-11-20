"""#3624: a store created fresh by `init_schema` has the same shape as one
migrated from an old version: every column (name, order, type, nullability,
default), every constraint (CHECK text included) and every index (SQL
included). Today's differences are pinned as a baseline in
`KNOWN_DIFFERENCES`, so a new difference fails and a fixed one fails too.

The old stores are built from today's DDL constants (no stored old-version
file exists under `tests/`), so this proves the migration path converges, not
that a real 2026-09 store file does."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from conftest import version_4_store
from test_registry_schema import _make_old_store

from tradepartner.store import lab_schema, schema

Row = tuple[object, ...]
Shape = dict[str, list[Row]]
Diff = dict[str, dict[str, list[Row]]]

_SHAPE_QUERIES: dict[str, str] = {
    "columns": (
        "SELECT table_name, column_index, column_name, data_type, is_nullable, "
        "column_default FROM duckdb_columns() "
        "WHERE database_name = current_database() AND schema_name = 'main' "
        "ORDER BY 1, 2"
    ),
    "constraints": (
        "SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints() "
        "WHERE database_name = current_database() AND schema_name = 'main' "
        "ORDER BY ALL"
    ),
    "indexes": (
        "SELECT table_name, index_name, is_unique, sql FROM duckdb_indexes() "
        "WHERE database_name = current_database() AND schema_name = 'main' "
        "ORDER BY ALL"
    ),
}

# A fresh store never gets the eight lab tables (#FOLLOWUP-1): `init_schema` calls
# `_migrate_lab` only for a store that recorded a pre-lab version.

_LAB_TABLE_COLUMNS: list[Row] = [
    ("family_rules", 1, "family", "VARCHAR", False, None),
    ("family_rules", 2, "first_hypothesis_id", "BIGINT", False, None),
    ("family_rules", 3, "parent_family", "VARCHAR", True, None),
    ("family_rules", 4, "holdout_start", "DATE", False, None),
    ("family_rules", 5, "holdout_end", "DATE", False, None),
    ("family_rules", 6, "in_sample_start", "DATE", False, None),
    ("family_rules", 7, "fixed_params_json", "VARCHAR", False, None),
    ("family_rules", 8, "fixed_params_sha256", "VARCHAR", False, None),
    ("family_rules", 9, "max_family_holdout_spends", "INTEGER", False, None),
    ("family_rules", 10, "max_family_promotions", "INTEGER", False, None),
    ("family_rules", 11, "min_sharpe_variance_annual", "DOUBLE", False, None),
    ("family_rules", 12, "axis_lattice_json", "VARCHAR", False, None),
    ("family_rules", 13, "sr_star_seed_annual", "DOUBLE", True, None),
    ("family_rules", 14, "registered_at", "TIMESTAMP WITH TIME ZONE", False, None),
    ("hypothesis_fingerprints", 1, "hypothesis_id", "BIGINT", False, None),
    ("hypothesis_fingerprints", 2, "fingerprint", "VARCHAR", False, None),
    ("pre_lab_hypotheses", 1, "hypothesis_id", "BIGINT", False, None),
    ("pre_lab_hypotheses", 2, "marked_at", "TIMESTAMP WITH TIME ZONE", False, None),
    ("store_markers", 1, "kind", "VARCHAR", False, None),
    ("store_markers", 2, "written_at", "TIMESTAMP WITH TIME ZONE", False, None),
    ("store_markers", 3, "written_by", "VARCHAR", False, None),
    ("sweep_runs", 1, "sweep_run_id", "BIGINT", False, None),
    ("sweep_runs", 2, "sweep_id", "BIGINT", False, None),
    ("sweep_runs", 3, "started_at", "TIMESTAMP WITH TIME ZONE", False, None),
    ("sweep_runs", 4, "finished_at", "TIMESTAMP WITH TIME ZONE", True, None),
    ("sweep_runs", 5, "time_budget_minutes", "INTEGER", False, None),
    ("sweep_runs", 6, "n_declared", "INTEGER", False, None),
    ("sweep_runs", 7, "n_planned", "INTEGER", False, None),
    ("sweep_runs", 8, "n_ok", "INTEGER", True, None),
    ("sweep_runs", 9, "n_failed", "INTEGER", True, None),
    ("sweep_runs", 10, "n_terminal_failed", "INTEGER", True, None),
    ("sweep_runs", 11, "seconds", "DOUBLE", True, None),
    ("sweep_runs", 12, "code_tree_sha256", "VARCHAR", False, None),
    ("sweep_runs", 13, "code_version", "VARCHAR", False, None),
    ("sweep_runs", 14, "code_dirty", "BOOLEAN", True, None),
    ("sweep_runs", 15, "n_trials_at_end", "INTEGER", True, None),
    ("sweep_runs", 16, "sr_star_annual_at_end", "DOUBLE", True, None),
    ("sweep_runs", 17, "completed", "BOOLEAN", True, None),
    ("sweep_runs", 18, "run_by", "VARCHAR", False, None),
    ("sweep_runs", 19, "note", "VARCHAR", True, None),
    ("sweep_trials", 1, "sweep_run_id", "BIGINT", False, None),
    ("sweep_trials", 2, "trial_id", "BIGINT", False, None),
    ("sweep_trials", 3, "read_group_index", "INTEGER", False, None),
    ("sweep_trials", 4, "seconds", "DOUBLE", False, None),
    ("sweep_variants", 1, "sweep_id", "BIGINT", False, None),
    ("sweep_variants", 2, "variant_index", "INTEGER", False, None),
    ("sweep_variants", 3, "hypothesis_id", "BIGINT", False, None),
    ("sweep_variants", 4, "fingerprint", "VARCHAR", False, None),
    ("sweep_variants", 5, "variant_params_json", "VARCHAR", False, None),
    ("sweeps", 1, "sweep_id", "BIGINT", False, None),
    ("sweeps", 2, "slug", "VARCHAR", False, None),
    ("sweeps", 3, "family", "VARCHAR", False, None),
    ("sweeps", 4, "title", "VARCHAR", False, None),
    ("sweeps", 5, "doc_path", "VARCHAR", False, None),
    ("sweeps", 6, "doc_sha256", "VARCHAR", False, None),
    ("sweeps", 7, "grid_json", "VARCHAR", False, None),
    ("sweeps", 8, "grid_sha256", "VARCHAR", False, None),
    ("sweeps", 9, "n_variants", "INTEGER", False, None),
    ("sweeps", 10, "selection_statistic", "VARCHAR", False, None),
    ("sweeps", 11, "expected_excess_cagr_spy_pp", "DOUBLE", False, None),
    ("sweeps", 12, "expected_range_lo_pp", "DOUBLE", False, None),
    ("sweeps", 13, "expected_range_hi_pp", "DOUBLE", False, None),
    ("sweeps", 14, "promote_at_least", "DOUBLE", False, None),
    ("sweeps", 15, "retire_below", "DOUBLE", False, None),
    ("sweeps", 16, "max_promotions", "INTEGER", False, None),
    ("sweeps", 17, "min_dsr_floor", "DOUBLE", False, None),
    ("sweeps", 18, "max_failures_per_variant", "INTEGER", False, None),
    ("sweeps", 19, "axis_lattice_json", "VARCHAR", False, None),
    ("sweeps", 20, "in_sample_start", "DATE", False, None),
    ("sweeps", 21, "holdout_start", "DATE", False, None),
    ("sweeps", 22, "holdout_end", "DATE", False, None),
    ("sweeps", 23, "registered_at", "TIMESTAMP WITH TIME ZONE", False, None),
    ("sweeps", 24, "registered_by", "VARCHAR", False, None),
]

_LAB_TABLE_CONSTRAINTS: list[Row] = [
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "NOT NULL", "NOT NULL"),
    ("family_rules", "PRIMARY KEY", 'PRIMARY KEY("family")'),
    ("hypothesis_fingerprints", "NOT NULL", "NOT NULL"),
    ("hypothesis_fingerprints", "NOT NULL", "NOT NULL"),
    ("hypothesis_fingerprints", "PRIMARY KEY", "PRIMARY KEY(hypothesis_id)"),
    ("pre_lab_hypotheses", "NOT NULL", "NOT NULL"),
    ("pre_lab_hypotheses", "NOT NULL", "NOT NULL"),
    ("pre_lab_hypotheses", "PRIMARY KEY", "PRIMARY KEY(hypothesis_id)"),
    ("store_markers", "CHECK", "CHECK((kind IN ('fixture')))"),
    ("store_markers", "NOT NULL", "NOT NULL"),
    ("store_markers", "NOT NULL", "NOT NULL"),
    ("store_markers", "NOT NULL", "NOT NULL"),
    ("store_markers", "PRIMARY KEY", "PRIMARY KEY(kind)"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "NOT NULL", "NOT NULL"),
    ("sweep_runs", "PRIMARY KEY", "PRIMARY KEY(sweep_run_id)"),
    ("sweep_trials", "NOT NULL", "NOT NULL"),
    ("sweep_trials", "NOT NULL", "NOT NULL"),
    ("sweep_trials", "NOT NULL", "NOT NULL"),
    ("sweep_trials", "NOT NULL", "NOT NULL"),
    ("sweep_trials", "PRIMARY KEY", "PRIMARY KEY(trial_id)"),
    ("sweep_variants", "CHECK", "CHECK((variant_index >= 1))"),
    ("sweep_variants", "NOT NULL", "NOT NULL"),
    ("sweep_variants", "NOT NULL", "NOT NULL"),
    ("sweep_variants", "NOT NULL", "NOT NULL"),
    ("sweep_variants", "NOT NULL", "NOT NULL"),
    ("sweep_variants", "NOT NULL", "NOT NULL"),
    ("sweep_variants", "PRIMARY KEY", "PRIMARY KEY(sweep_id, variant_index)"),
    ("sweeps", "CHECK", "CHECK((n_variants >= 1))"),
    (
        "sweeps",
        "CHECK",
        "CHECK((selection_statistic IN ('dsr_excess', 'sharpe_annual_excess_spy', "
        "'excess_cagr_spy')))",
    ),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "NOT NULL", "NOT NULL"),
    ("sweeps", "PRIMARY KEY", "PRIMARY KEY(sweep_id)"),
]

# Same cause (#FOLLOWUP-1): without the lab migration `trial_results.status` lacks
# `refused_variant` and `owner_decisions.kind` lacks `promotion` and `sweep_retired`.

_PRE_LAB_ENUM_CHECKS: list[Row] = [
    (
        "owner_decisions",
        "CHECK",
        "CHECK((kind IN ('gap_signoff', 'gap_override', 'holdout_spend', 'data_release', "
        "'development_boundary', 'shakedown_span', 'shakedown_note', 'operations_book')))",
    ),
    (
        "trial_results",
        "CHECK",
        "CHECK((status IN ('ok', 'failed', 'refused_window', 'refused_holdout', 'refused_gap')))",
    ),
]

_LAB_ENUM_CHECKS: list[Row] = [
    (
        "owner_decisions",
        "CHECK",
        "CHECK((kind IN ('gap_signoff', 'gap_override', 'holdout_spend', 'promotion', "
        "'sweep_retired', 'data_release', 'development_boundary', 'shakedown_span', "
        "'shakedown_note', 'operations_book')))",
    ),
    (
        "trial_results",
        "CHECK",
        "CHECK((status IN ('ok', 'failed', 'refused_window', 'refused_holdout', 'refused_gap', "
        "'refused_variant')))",
    ),
]

# With the lab schema applied to the fresh store, the `owner_decisions.kind` CHECK
# lists the same values in a different order (#FOLLOWUP-2): same set, different text.

_CHECK_ORDER_FRESH: list[Row] = [
    (
        "owner_decisions",
        "CHECK",
        "CHECK((kind IN ('gap_signoff', 'gap_override', 'holdout_spend', 'data_release', "
        "'development_boundary', 'shakedown_span', 'shakedown_note', 'operations_book', "
        "'promotion', 'sweep_retired')))",
    ),
]

_CHECK_ORDER_MIGRATED: list[Row] = [
    (
        "owner_decisions",
        "CHECK",
        "CHECK((kind IN ('gap_signoff', 'gap_override', 'holdout_spend', 'promotion', "
        "'sweep_retired', 'data_release', 'development_boundary', 'shakedown_span', "
        "'shakedown_note', 'operations_book')))",
    ),
]


KNOWN_DIFFERENCES: Diff = {
    "columns": {"fresh_only": [], "migrated_only": sorted(_LAB_TABLE_COLUMNS, key=repr)},
    "constraints": {
        "fresh_only": sorted(_PRE_LAB_ENUM_CHECKS, key=repr),
        "migrated_only": sorted(_LAB_TABLE_CONSTRAINTS + _LAB_ENUM_CHECKS, key=repr),
    },
}

KNOWN_DIFFERENCES_WITH_LAB: Diff = {
    "constraints": {"fresh_only": _CHECK_ORDER_FRESH, "migrated_only": _CHECK_ORDER_MIGRATED},
}


def _shape(conn: duckdb.DuckDBPyConnection) -> Shape:
    """Every column, constraint and index of the connection's `main` schema."""
    return {key: conn.execute(sql).fetchall() for key, sql in _SHAPE_QUERIES.items()}


def _only_in(rows: list[Row], other: list[Row]) -> list[Row]:
    """The rows of `rows` that `other` lacks, in order and counted: `NOT NULL`
    constraint rows carry no column name, so a table has several equal rows."""
    remaining = Counter(other)
    out: list[Row] = []
    for row in rows:
        if remaining[row] > 0:
            remaining[row] -= 1
        else:
            out.append(row)
    return out


def _diff(fresh: Shape, migrated: Shape) -> Diff:
    """The rows only one of the two shapes has, per kind; kinds that agree are
    left out, so two equal shapes give `{}`. Rows are sorted by `repr`."""
    out: Diff = {}
    for key in _SHAPE_QUERIES:
        fresh_only = _only_in(fresh[key], migrated[key])
        migrated_only = _only_in(migrated[key], fresh[key])
        if fresh_only or migrated_only:
            out[key] = {
                "fresh_only": sorted(fresh_only, key=repr),
                "migrated_only": sorted(migrated_only, key=repr),
            }
    return out


def _v2(path: Path) -> None:
    _make_old_store(path, 2)


def _v3(path: Path) -> None:
    _make_old_store(path, 3)


def _v4(path: Path) -> None:
    version_4_store(path)


_BUILDERS: dict[str, Callable[[Path], None]] = {"v2": _v2, "v3": _v3, "v4": _v4}


def _fresh() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    return conn


def _migrated(tmp_path: Path, builder: str) -> duckdb.DuckDBPyConnection:
    """An old store built by `builder` under `tmp_path`, then migrated by
    `init_schema` (the autouse `_legacy_schema_migration` fixture allows it)."""
    path = tmp_path / f"{builder}.duckdb"
    _BUILDERS[builder](path)
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    return conn


@pytest.mark.parametrize("builder", list(_BUILDERS))
def test_a_fresh_store_has_the_shape_of_a_migrated_one(tmp_path: Path, builder: str) -> None:
    fresh = _fresh()
    migrated = _migrated(tmp_path, builder)
    try:
        assert _diff(_shape(fresh), _shape(migrated)) == KNOWN_DIFFERENCES
    finally:
        fresh.close()
        migrated.close()


def _in_list_values(constraint_text: object) -> set[str]:
    """The quoted values of the `IN (...)` list in a CHECK's text."""
    assert isinstance(constraint_text, str)
    match = re.search(r"IN \((.*?)\)", constraint_text)
    assert match is not None, constraint_text
    return set(re.findall(r"'([^']*)'", match.group(1)))


@pytest.mark.parametrize("builder", list(_BUILDERS))
def test_with_the_lab_schema_applied_only_the_check_order_differs(
    tmp_path: Path, builder: str
) -> None:
    fresh = _fresh()
    lab_schema.apply_lab_schema(fresh)
    migrated = _migrated(tmp_path, builder)
    try:
        diff = _diff(_shape(fresh), _shape(migrated))
    finally:
        fresh.close()
        migrated.close()
    assert diff == KNOWN_DIFFERENCES_WITH_LAB
    (fresh_row,) = diff["constraints"]["fresh_only"]
    (migrated_row,) = diff["constraints"]["migrated_only"]
    assert fresh_row[:2] == migrated_row[:2] == ("owner_decisions", "CHECK")
    assert _in_list_values(fresh_row[2]) == _in_list_values(migrated_row[2])


def test_the_comparison_sees_a_planted_difference(tmp_path: Path) -> None:
    fresh = _fresh()
    migrated = _migrated(tmp_path, "v4")
    try:
        migrated.execute("ALTER TABLE rates ADD COLUMN planted INTEGER")
        diff = _diff(_shape(fresh), _shape(migrated))
    finally:
        fresh.close()
        migrated.close()
    assert diff != KNOWN_DIFFERENCES
    planted = [row for row in diff["columns"]["migrated_only"] if row[0] == "rates"]
    assert [row[2:] for row in planted] == [("planted", "INTEGER", True, None)]


def test_the_comparison_sees_a_planted_check_change(tmp_path: Path) -> None:
    fresh = _fresh()
    migrated = _migrated(tmp_path, "v4")
    try:
        fresh.execute("CREATE TABLE planted (x INTEGER CHECK (x > 0))")
        migrated.execute("CREATE TABLE planted (x INTEGER CHECK (x > 1))")
        diff = _diff(_shape(fresh), _shape(migrated))
    finally:
        fresh.close()
        migrated.close()
    assert ("planted", "CHECK", "CHECK((x > 0))") in diff["constraints"]["fresh_only"]
    assert ("planted", "CHECK", "CHECK((x > 1))") in diff["constraints"]["migrated_only"]


def test_a_removed_baseline_row_no_longer_matches(tmp_path: Path) -> None:
    """A stale baseline fails the main test: with any one row dropped from
    `KNOWN_DIFFERENCES`, today's diff no longer equals it."""
    fresh = _fresh()
    migrated = _migrated(tmp_path, "v4")
    try:
        diff = _diff(_shape(fresh), _shape(migrated))
    finally:
        fresh.close()
        migrated.close()
    for kind, sides in KNOWN_DIFFERENCES.items():
        for side, rows in sides.items():
            for index in range(len(rows)):
                pruned = {k: dict(v) for k, v in KNOWN_DIFFERENCES.items()}
                pruned[kind][side] = rows[:index] + rows[index + 1 :]
                assert diff != pruned, (kind, side, rows[index])

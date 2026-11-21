"""#3624: a store created fresh by `init_schema` has the same shape as one
migrated from an old version: every column (name, order, type, nullability,
default), every constraint (CHECK text included) and every index (SQL
included). Since #3672 nothing differs (`KNOWN_DIFFERENCES` is empty). A
store created without the lab at version 16 to 49 and repaired by version 50
still differs in one CHECK's text, pinned in `KNOWN_DIFFERENCES_WITH_LAB`, so
a new difference fails and a fixed one fails too.

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
from conftest import strip_lab, version_4_store
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

# A store created fresh at schema version 16 to 49 had no lab schema (#3672);
# version 50 gives it one on migration. Its `owner_decisions.kind` CHECK then
# lists the same values as a store that migrated through version 16, in a
# different order (#3673): same set, different text.

_CHECK_ORDER_REPAIRED: list[Row] = [
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

#: Fresh against migrated: nothing differs since #3672 (a fresh store gets the
#: lab schema at the step a migrating one does).
KNOWN_DIFFERENCES: Diff = {}

#: A version-49 store created without the lab and repaired by version 50
#: (`fresh_only` is the repaired store's side) against a migrated one.
KNOWN_DIFFERENCES_WITH_LAB: Diff = {
    "constraints": {"fresh_only": _CHECK_ORDER_REPAIRED, "migrated_only": _CHECK_ORDER_MIGRATED},
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


def _repaired() -> duckdb.DuckDBPyConnection:
    """A store as `init_schema` created it fresh at version 49 (no lab schema,
    `strip_lab`), then migrated to the current version, which repairs it."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    strip_lab(conn)
    assert not lab_schema.is_lab_initialised(conn)
    conn.execute("UPDATE schema_version SET version = ?", [schema._PRE_FRESH_LAB_VERSION])
    schema.init_schema(conn)
    assert lab_schema.is_lab_initialised(conn)
    return conn


@pytest.mark.parametrize("builder", list(_BUILDERS))
def test_a_repaired_store_differs_from_a_migrated_one_only_in_check_order(
    tmp_path: Path, builder: str
) -> None:
    repaired = _repaired()
    migrated = _migrated(tmp_path, builder)
    try:
        diff = _diff(_shape(repaired), _shape(migrated))
    finally:
        repaired.close()
        migrated.close()
    assert diff == KNOWN_DIFFERENCES_WITH_LAB
    (repaired_row,) = diff["constraints"]["fresh_only"]
    (migrated_row,) = diff["constraints"]["migrated_only"]
    assert repaired_row[:2] == migrated_row[:2] == ("owner_decisions", "CHECK")
    assert _in_list_values(repaired_row[2]) == _in_list_values(migrated_row[2])


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
    """A stale baseline fails its test: with any one row dropped from
    `KNOWN_DIFFERENCES_WITH_LAB`, today's diff no longer equals it."""
    repaired = _repaired()
    migrated = _migrated(tmp_path, "v4")
    try:
        diff = _diff(_shape(repaired), _shape(migrated))
    finally:
        repaired.close()
        migrated.close()
    for kind, sides in KNOWN_DIFFERENCES_WITH_LAB.items():
        for side, rows in sides.items():
            for index in range(len(rows)):
                pruned = {k: dict(v) for k, v in KNOWN_DIFFERENCES_WITH_LAB.items()}
                pruned[kind][side] = rows[:index] + rows[index + 1 :]
                assert diff != pruned, (kind, side, rows[index])

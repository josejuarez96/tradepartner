"""Tests for `store.journal` (Phase 4 plan T49b): row types, `append`, the fills
accessor and `JournalNotInitialised`.

Module-text checks: no `UPDATE` or `DELETE`, and `fills` is selected only inside
`fills_for` and `all_fill_ids`. Behaviour: every row type matches its table and
appends; ids increase; a naive or reversed timestamp pair is refused by every
writer; a superseded fill is hidden by the accessor and counted by `all_fill_ids`;
the accessor raises `JournalNotInitialised` on a version-4 store; `known_at` is
stored as given, never taken from a broker field.
"""

from __future__ import annotations

import ast
import re
import typing
from collections.abc import Iterator
from dataclasses import MISSING, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.store import journal, schema
from tradepartner.store.journal import (
    ROW_TYPES,
    DecisionRow,
    FillRow,
    JournalIntegrityError,
    JournalNotInitialised,
    OrderRow,
    PaperRunRow,
    all_fill_ids,
    append,
    fills_for,
)

MODULE = Path(journal.__file__)
_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
_SESSION = date(2026, 10, 1)
#: Nullable columns a valid sample row still needs (a position row names its security).
_SAMPLE_EXTRAS: dict[str, dict[str, Any]] = {"positions_daily": {"security_id": "SEC_A"}}
_PYTHON_TYPES = {
    "BIGINT": "int",
    "INTEGER": "int",
    "DOUBLE": "float",
    "VARCHAR": "str",
    "BOOLEAN": "bool",
    "DATE": "date",
    "TIMESTAMP WITH TIME ZONE": "datetime",
}
_TYPES = {"int": 1, "float": 1.0, "str": "x", "bool": False, "date": _SESSION, "datetime": _NOW}


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _field_type(row_type: type[Any], name: str) -> str:
    hint = typing.get_type_hints(row_type)[name]
    args = [a for a in typing.get_args(hint) if a is not type(None)] or [hint]
    return str(args[0].__name__)


def _sample(row_type: type[Any], **values: Any) -> Any:
    """A valid row of `row_type`: the first allowed value for an enumerated column,
    a placeholder of the field's type otherwise; NULL-able columns left None."""
    kwargs: dict[str, Any] = {}
    for f in fields(row_type):
        if f.default is not MISSING:
            continue
        enum = schema.JOURNAL_ENUMS.get((row_type.TABLE, f.name))
        kwargs[f.name] = enum[0] if enum else _TYPES[_field_type(row_type, f.name)]
    return row_type(**{**kwargs, **_SAMPLE_EXTRAS.get(row_type.TABLE, {}), **values})


# --- module text --------------------------------------------------------------------


def test_the_module_never_updates_or_deletes() -> None:
    """No SQL string in the module updates, deletes or replaces a row (docstrings,
    which say it never does, are prose and skipped)."""
    tree = ast.parse(MODULE.read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }
    sql = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    pattern = re.compile(r"\b(UPDATE|DELETE|REPLACE|TRUNCATE|DROP|ALTER)\b|ON\s+CONFLICT", re.I)
    assert [text for text in sql if pattern.search(text)] == []


def test_fills_is_selected_only_inside_the_accessor_and_all_fill_ids() -> None:
    tree = ast.parse(MODULE.read_text())
    readers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.search(r"\b(FROM|JOIN)\s+fills\b", node.value, re.IGNORECASE)
            ):
                readers.add(function.name)
    top_level = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Expr | ast.Assign)
        and isinstance(getattr(node, "value", None), ast.Constant)
    ]
    assert readers == {"fills_for", "all_fill_ids"}
    assert not any(
        re.search(r"\b(FROM|JOIN)\s+fills\b", str(getattr(c, "value", ""))) for c in top_level
    )


def test_known_at_is_never_assigned_from_a_broker_field() -> None:
    """No `known_at=<...filled_at|event_at|at>` or `known_at = ...` from a broker
    instant anywhere in the module. This module builds no rows, so the check guards
    against a future change here; the real guard is in the collectors (T58), which
    stamp `known_at` from the clock."""
    tree = ast.parse(MODULE.read_text())
    broker = {"filled_at", "event_at", "at", "finished_at"}
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.keyword) and node.arg == "known_at":
            value = node.value
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name | ast.Attribute)
            and getattr(t, "id", getattr(t, "attr", None)) == "known_at"
            for t in node.targets
        ):
            value = node.value
        if value is not None:
            names = {getattr(n, "attr", getattr(n, "id", None)) for n in ast.walk(value)}
            assert not names & broker, ast.unparse(node)


# --- row types ------------------------------------------------------------------------


def test_one_row_type_per_journal_table() -> None:
    assert tuple(ROW_TYPES) == schema.JOURNAL_TABLE_NAMES


@pytest.mark.parametrize("table", schema.JOURNAL_TABLE_NAMES)
def test_row_type_fields_are_the_table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> None:
    """Same names in the same order; a field defaults to None exactly when its
    column may be NULL (or is the table's own id, which `append` assigns)."""
    columns = conn.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    row_type = ROW_TYPES[table]
    assert [f.name for f in fields(row_type)] == [name for name, _ in columns]
    types = dict(
        conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
            [table],
        ).fetchall()
    )
    for f, (name, nullable) in zip(fields(row_type), columns, strict=True):
        optional = f.default is None
        assert optional == (nullable == "YES" or name == row_type.ID_COLUMN), name
        assert _field_type(row_type, name) == _PYTHON_TYPES[types[name]], name
    assert table == row_type.TABLE


@pytest.mark.parametrize("table", schema.JOURNAL_TABLE_NAMES)
def test_every_row_type_appends(conn: duckdb.DuckDBPyConnection, table: str) -> None:
    row_type = ROW_TYPES[table]
    returned = append(conn, _sample(row_type))
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)
    assert (returned is None) == (row_type.ID_COLUMN is None)


# --- append -------------------------------------------------------------------------------


def test_ids_increase_and_an_explicit_id_is_kept(conn: duckdb.DuckDBPyConnection) -> None:
    ids = [append(conn, _sample(DecisionRow)) for _ in range(3)]
    assert ids == [1, 2, 3]
    assert append(conn, _sample(DecisionRow, decision_id=10)) == 10
    assert append(conn, _sample(DecisionRow)) == 11


@pytest.mark.parametrize("table", schema.JOURNAL_TABLE_NAMES)
@pytest.mark.parametrize(
    "stamps",
    [
        {"known_at": _NOW.replace(tzinfo=None)},
        {"ingested_at": _NOW.replace(tzinfo=None)},
        {"known_at": _NOW + timedelta(microseconds=1)},
    ],
    ids=["naive-known_at", "naive-ingested_at", "known_at-after-ingested_at"],
)
def test_every_writer_refuses_a_naive_or_reversed_pair(
    conn: duckdb.DuckDBPyConnection, table: str, stamps: dict[str, datetime]
) -> None:
    with pytest.raises(ValueError):
        append(conn, _sample(ROW_TYPES[table], **stamps))
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


def test_known_at_is_stored_as_given_whatever_the_broker_says(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The broker's `filled_at` is its own column; `known_at` is the clock's."""
    learned = _NOW + timedelta(minutes=5)
    append(
        conn,
        _sample(
            FillRow, filled_at=_NOW - timedelta(hours=1), known_at=learned, ingested_at=learned
        ),
    )
    assert conn.execute("SELECT filled_at, known_at FROM fills").fetchone() == (
        _NOW - timedelta(hours=1),
        learned,
    )


def test_append_refuses_what_is_not_a_row_type(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TypeError):
        append(conn, object())  # type: ignore[arg-type]


# --- fills accessor -----------------------------------------------------------------------


def _order(conn: duckdb.DuckDBPyConnection, coid: str, *, run_id: int, side: str) -> None:
    append(
        conn,
        _sample(
            OrderRow,
            client_order_id=coid,
            run_id=run_id,
            side=side,
            security_id=f"SEC_{coid}",
            symbol=coid.upper(),
        ),
    )


def _fill(conn: duckdb.DuckDBPyConnection, coid: str, broker_id: str, **values: Any) -> int:
    fill_id = append(
        conn, _sample(FillRow, client_order_id=coid, broker_fill_id=broker_id, **values)
    )
    assert fill_id is not None
    return fill_id


@pytest.fixture
def seeded(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Two windows' runs; orders a (buy) and b (sell) in window 1, c in window 2."""
    for run_id, window_id in ((1, 1), (2, 2)):
        append(conn, _sample(PaperRunRow, run_id=run_id, window_id=window_id))
    _order(conn, "a", run_id=1, side="buy")
    _order(conn, "b", run_id=1, side="sell")
    _order(conn, "c", run_id=2, side="buy")
    return conn


def test_a_superseded_fill_is_hidden_by_the_accessor_and_counted_by_all_fill_ids(
    seeded: duckdb.DuckDBPyConnection,
) -> None:
    synthetic = _fill(seeded, "a", "synthetic:a", source="broker_status", price_implied=True)
    real = _fill(seeded, "a", "bf-a", superseded_by=None)
    _fill(seeded, "a", "bf-a-late", superseded_by=synthetic)
    live = fills_for(seeded, client_order_ids=["a"])
    assert [f.fill.fill_id for f in live] == [synthetic, real]
    assert all_fill_ids(seeded) == {"synthetic:a", "bf-a", "bf-a-late"}


def test_the_accessor_joins_side_and_security_and_filters(
    seeded: duckdb.DuckDBPyConnection,
) -> None:
    for coid in ("a", "b", "c"):
        _fill(seeded, coid, f"bf-{coid}")
    every = fills_for(seeded)
    assert [
        (f.fill.client_order_id, f.side, f.security_id, f.symbol, f.window_id) for f in every
    ] == [
        ("a", "buy", "SEC_a", "A", 1),
        ("b", "sell", "SEC_b", "B", 1),
        ("c", "buy", "SEC_c", "C", 2),
    ]
    assert [f.fill.client_order_id for f in fills_for(seeded, window_id=1)] == ["a", "b"]
    assert [f.fill.client_order_id for f in fills_for(seeded, window_id=2)] == ["c"]
    assert [f.fill.client_order_id for f in fills_for(seeded, client_order_ids=["c", "b"])] == [
        "b",
        "c",
    ]
    assert fills_for(seeded, client_order_ids=[]) == []


@pytest.mark.parametrize("window_id", [None, 1])
def test_a_live_fill_without_its_order_raises(
    seeded: duckdb.DuckDBPyConnection, window_id: int | None
) -> None:
    _fill(seeded, "nobody", "bf-orphan")
    with pytest.raises(JournalIntegrityError, match=r"nobody.*orders row"):
        fills_for(seeded, window_id=window_id)


@pytest.mark.parametrize("window_id", [None, 1])
def test_a_fill_whose_order_has_no_run_raises(
    seeded: duckdb.DuckDBPyConnection, window_id: int | None
) -> None:
    _order(seeded, "lost", run_id=99, side="buy")
    _fill(seeded, "lost", "bf-lost")
    with pytest.raises(JournalIntegrityError, match="paper_runs row for run 99"):
        fills_for(seeded, window_id=window_id)


@pytest.mark.parametrize(
    "shape",
    ["dangling", "feed-target", "other-order", "chained", "self"],
)
def test_a_bad_superseded_pointer_raises(seeded: duckdb.DuckDBPyConnection, shape: str) -> None:
    synthetic = _fill(seeded, "a", "synthetic:a", source="broker_status", price_implied=True)
    feed = _fill(seeded, "a", "bf-a")
    other = _fill(seeded, "b", "synthetic:b", source="broker_status", price_implied=True)
    target = {
        "dangling": 999,
        "feed-target": feed,
        "other-order": other,
        "chained": _fill(seeded, "a", "bf-a-2", superseded_by=synthetic),
        "self": None,
    }[shape]
    if shape == "self":
        target = 10
        _fill(seeded, "a", "bf-self", fill_id=10, superseded_by=10)
    else:
        _fill(seeded, "a", f"bf-{shape}", superseded_by=target)
    if shape == "chained":  # a pointer at a row that is itself superseded
        _fill(seeded, "a", "bf-a-3", superseded_by=target)
    with pytest.raises(JournalIntegrityError, match=f"-> {target}"):
        fills_for(seeded)


def test_one_string_is_not_a_list_of_order_ids(seeded: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TypeError):
        fills_for(seeded, client_order_ids="abc")


def test_row_types_cannot_be_changed_by_callers() -> None:
    with pytest.raises(TypeError):
        ROW_TYPES["fills"] = DecisionRow  # type: ignore[index]


# --- version-4 store ----------------------------------------------------------------------


def test_journal_calls_raise_journal_not_initialised_on_a_version_4_store(
    tmp_path: Path,
) -> None:
    path = version_4_store(tmp_path / "store_v4.duckdb")
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)  # a version-4 store passes the read-only check
        with pytest.raises(JournalNotInitialised):
            fills_for(conn)
        with pytest.raises(JournalNotInitialised):
            all_fill_ids(conn)
    with (
        duckdb.connect(str(path)) as conn,  # write, but without init_schema
        pytest.raises(JournalNotInitialised),
    ):
        append(conn, _sample(DecisionRow))

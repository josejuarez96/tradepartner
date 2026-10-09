"""Tests for the closed `decisions.reason` set (#377, schema version 7).

A **full exit** is defined from (decision, reason) (spec Definitions; #368), and
`plan.decision_state` treats a forced exit's untradable residue by its reason, so a
writer that misspelt `left_targets` or `window_stop` would silently turn a full exit
into a trim or drop a residue. Version 7 makes the column a nullable closed set built
from shared constants that `plan` uses too, the spec's list says exactly what the
`CHECK` enforces, and the migration from version 6 rebuilds `decisions` with every
row kept, refusing (and changing nothing) if a stored reason is outside the set. A
read-only connection still opens a version-6 store, since the `CHECK` only guards
writes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.execution import plan
from tradepartner.store import schema

_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
_SPEC = Path(__file__).resolve().parents[2] / "docs" / "specs" / "paper-trading.md"

#: `decisions` as version 6 created it: the same DDL without the reason `CHECK`.
_V6_DECISIONS_DDL = schema._CREATE_DECISIONS.replace(
    f",\n    {schema._check('decisions', 'reason')}", ""
)
#: `order_events` as version 5 created it: the same DDL without the reason `CHECK`.
_V5_ORDER_EVENTS_DDL = schema._CREATE_ORDER_EVENTS.replace(
    f",\n    {schema._check('order_events', 'reason')}", ""
)


#: `resume_invocations` as versions 5 to 7 created it: no `accept_rejections` (#472).
_V7_RESUME_INVOCATIONS_DDL = schema._CREATE_RESUME_INVOCATIONS.replace(
    "    accept_rejections BOOLEAN NOT NULL,\n", ""
)


@pytest.fixture
def journal() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _decision(
    conn: duckdb.DuckDBPyConnection,
    reason: str | None,
    decision_id: int = 1,
    decision: str = "trade",
) -> None:
    conn.execute(
        "INSERT INTO decisions (decision_id, run_id, security_id, whole_share, decision, "
        "reason, known_at, ingested_at) VALUES (?, 1, 'S1', FALSE, ?, ?, ?, ?)",
        [decision_id, decision, reason, _NOW, _NOW],
    )


def _old_store(path: Path, version: int, reasons: tuple[str | None, ...] = ()) -> Path:
    """A store shaped as `version` (5 or 6) left it: no `decisions.reason` `CHECK`,
    and at version 5 no `order_events.reason` `CHECK` either; one `decisions` row
    per entry of `reasons`."""
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute("DROP TABLE decisions")
        conn.execute(_V6_DECISIONS_DDL)
        conn.execute("DROP TABLE resume_acceptances")
        conn.execute("DROP TABLE resume_invocations")
        conn.execute(_V7_RESUME_INVOCATIONS_DDL)
        if version == 5:
            conn.execute("DROP TABLE order_events")
            conn.execute(_V5_ORDER_EVENTS_DDL)
        conn.execute("UPDATE schema_version SET version = ?", [version])
        for index, reason in enumerate(reasons):
            _decision(conn, reason, decision_id=index)
    return path


def _versions(conn: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY 1").fetchall()
    ]


def _shape(conn: duckdb.DuckDBPyConnection, table: str) -> tuple[Any, ...]:
    """Columns and constraints of `table`, for comparing two stores."""
    columns = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    constraints = conn.execute(
        "SELECT constraint_type, constraint_text FROM duckdb_constraints() "
        "WHERE database_name = current_database() AND table_name = ? ORDER BY ALL",
        [table],
    ).fetchall()
    return (columns, constraints)


# --- the shared constants -----------------------------------------------------------


def test_the_reasons_are_spelt_as_the_spec_names_them() -> None:
    """#368's full exit is defined on these exact spellings."""
    assert schema.LEFT_TARGETS_REASON == "left_targets"
    assert schema.LEFT_UNIVERSE_REASON == "left_universe"
    assert schema.EXCLUDE_NAME_REASON == "exclude_name"
    assert schema.KEEP_NAME_REASON == "keep_name"
    assert schema.DELISTED_REASON == "delisted"
    assert schema.UNTARGETED_RECEIPT_REASON == "untargeted_receipt"
    assert schema.WINDOW_STOP_REASON == "window_stop"
    assert schema.DECISION_REASONS == (
        schema.LEFT_TARGETS_REASON,
        schema.LEFT_UNIVERSE_REASON,
        schema.EXCLUDE_NAME_REASON,
        schema.KEEP_NAME_REASON,
        schema.DELISTED_REASON,
        schema.UNTARGETED_RECEIPT_REASON,
        schema.WINDOW_STOP_REASON,
    )


def test_the_spec_lists_exactly_the_checked_reasons() -> None:
    """Spec "Data / interfaces" > Tables: the `decisions.reason` list is closed (no
    "…") and names the `CHECK`'s values and null."""
    text = _SPEC.read_text(encoding="utf-8")
    match = re.search(r"`decisions\(decision_id[^`]*?reason ∈ \{([^}]*)\}", text)
    assert match is not None
    listed = [value.strip() for value in match.group(1).split(",")]
    assert "…" not in listed
    assert sorted(listed) == sorted(["null", *schema.DECISION_REASONS])


def test_the_check_is_built_from_the_shared_constants() -> None:
    assert schema.JOURNAL_ENUMS["decisions", "reason"] == schema.DECISION_REASONS
    assert ("decisions", "reason") in schema.NULLABLE_JOURNAL_ENUMS


def test_the_name_override_kinds_are_the_shared_constants() -> None:
    """`decisions_from` writes an `exclude_name` or `keep_name` override's kind as
    the decision's reason, so the two sets must agree."""
    kinds = schema.JOURNAL_ENUMS["overrides", "kind"]
    assert schema.EXCLUDE_NAME_REASON in kinds
    assert schema.KEEP_NAME_REASON in kinds


def test_the_kill_switch_kind_is_the_shared_constant() -> None:
    """#636: the dashboard's duplicate guard exemption must name the same kind the
    `overrides.kind` enum uses, not a hardcoded literal that could drift from it."""
    assert schema.ENGAGE_KILL_SWITCH_KIND == "engage_kill_switch"
    assert schema.ENGAGE_KILL_SWITCH_KIND in schema.JOURNAL_ENUMS["overrides", "kind"]


def test_plan_reads_and_writes_the_shared_constants() -> None:
    """`decisions_from` writes these and `decision_state` matches on them."""
    assert plan._LEFT_TARGETS is schema.LEFT_TARGETS_REASON
    assert plan._LEFT_UNIVERSE is schema.LEFT_UNIVERSE_REASON
    assert plan._EXCLUDE_NAME is schema.EXCLUDE_NAME_REASON
    assert plan._KEEP_NAME is schema.KEEP_NAME_REASON
    assert plan._WINDOW_STOP is schema.WINDOW_STOP_REASON
    assert {
        schema.WINDOW_STOP_REASON,
        schema.DELISTED_REASON,
        schema.UNTARGETED_RECEIPT_REASON,
    } == plan._UNTRADABLE_EXIT_REASONS
    assert set(schema.DECISION_REASONS) >= plan._NAME_OVERRIDES


# --- the CHECK ----------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, *schema.DECISION_REASONS])
def test_every_listed_reason_and_null_is_accepted(
    journal: duckdb.DuckDBPyConnection, reason: str | None
) -> None:
    _decision(journal, reason)
    assert journal.execute("SELECT reason FROM decisions").fetchall() == [(reason,)]


@pytest.mark.parametrize(
    "reason",
    ["left_target", "Left_Targets", "left universe", "leftuniverse", "window-stop", "", "dust"],
)
def test_a_misspelt_reason_is_refused(journal: duckdb.DuckDBPyConnection, reason: str) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _decision(journal, reason)


# --- version 7 and the migrations from versions 6 and 5 ----------------------------------


def test_current_schema_version_is_20() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 20


def test_fresh_init_records_version_17(journal: duckdb.DuckDBPyConnection) -> None:
    assert _versions(journal) == [20]


@pytest.mark.parametrize("version", [5, 6])
def test_write_open_adds_the_check_and_keeps_every_row(
    tmp_path: Path, journal: duckdb.DuckDBPyConnection, version: int
) -> None:
    reasons = (None, *schema.DECISION_REASONS)
    path = _old_store(tmp_path / f"v{version}.duckdb", version, reasons)
    rebuilt = {"decisions", "resume_invocations", "resume_acceptances"} | (
        {"order_events"} if version == 5 else set()
    )
    with duckdb.connect(str(path)) as conn:
        before = conn.execute("SELECT * FROM decisions ORDER BY ALL").fetchall()
        kept = [table for table in schema.JOURNAL_TABLE_NAMES if table not in rebuilt]
        others_before = {table: _shape(conn, table) for table in kept}
        schema.init_schema(conn)
        after = conn.execute("SELECT * FROM decisions ORDER BY ALL").fetchall()
        shapes = {table: _shape(conn, table) for table in schema.JOURNAL_TABLE_NAMES}
        versions = _versions(conn)
        with pytest.raises(duckdb.ConstraintException):
            _decision(conn, "left_target", decision_id=99)
    assert after == before
    assert len(after) == len(reasons)
    assert versions == list(range(version, 21))
    assert shapes == {table: _shape(journal, table) for table in schema.JOURNAL_TABLE_NAMES}
    assert {t: s for t, s in shapes.items() if t not in rebuilt} == others_before


def test_the_migration_keeps_the_insertion_order(tmp_path: Path) -> None:
    """The rebuild copies rows in rowid order, so a reader that breaks `known_at` ties
    on rowid sees the same order after the migration."""
    path = _old_store(tmp_path / "v6.duckdb", 6)
    ids = [5, 3, 9, 1, 7]
    reader = "SELECT decision_id FROM decisions ORDER BY known_at, rowid"
    with duckdb.connect(str(path)) as conn:
        for decision_id in ids:
            _decision(conn, schema.LEFT_TARGETS_REASON, decision_id=decision_id)
        before = [row[0] for row in conn.execute(reader).fetchall()]
        schema.init_schema(conn)
        after = [row[0] for row in conn.execute(reader).fetchall()]
    assert before == ids
    assert after == ids


def test_a_migrated_store_reopens_without_another_version_row(tmp_path: Path) -> None:
    path = _old_store(tmp_path / "v6.duckdb", 6)
    for _ in range(2):
        with duckdb.connect(str(path)) as conn:
            schema.init_schema(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


@pytest.mark.parametrize("version", [5, 6])
def test_a_stored_reason_outside_the_set_refuses_the_migration_and_changes_nothing(
    tmp_path: Path, version: int
) -> None:
    path = _old_store(
        tmp_path / f"v{version}.duckdb", version, (schema.LEFT_TARGETS_REASON, "left_target")
    )
    with duckdb.connect(str(path)) as conn:
        with pytest.raises(schema.SchemaVersionError, match="left_target'"):
            schema.init_schema(conn)
        assert _versions(conn) == [version]
        stored = sorted(r for (r,) in conn.execute("SELECT reason FROM decisions").fetchall())
        assert stored == ["left_target", "left_targets"]
        _decision(conn, "still unchecked", decision_id=99)  # the old table is untouched
        if version == 5:  # nor was order_events rebuilt
            conn.execute(
                "INSERT INTO order_events (client_order_id, status, reason, known_at, "
                "ingested_at) VALUES ('c1', 'cancelled', 'still unchecked', ?, ?)",
                [_NOW, _NOW],
            )


@pytest.mark.parametrize("version", [5, 6])
def test_read_only_open_of_an_older_journal_store_passes(tmp_path: Path, version: int) -> None:
    """Reads do not depend on the `CHECK`, so the dashboard keeps working on a store
    no write has touched since this version was pulled."""
    path = _old_store(tmp_path / f"v{version}.duckdb", version, (schema.WINDOW_STOP_REASON,))
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [version]
        assert conn.execute("SELECT reason FROM decisions").fetchall() == [("window_stop",)]

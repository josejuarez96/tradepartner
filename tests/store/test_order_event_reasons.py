"""Tests for the closed `order_events.reason` set (#332, schema version 6; #571,
version 9).

`plan.decision_state` keeps a halted or never-received buy open by matching
`order_events.reason` against `halt` and `not_received`; a writer that spelt either
differently would silently write such a buy off as unfunded. Version 6 makes the
column a nullable closed set built from the shared constants, and the migration
from version 5 rebuilds `order_events` with every row kept, refusing (and changing
nothing) if a stored reason is outside the set. A read-only connection still opens
a version-5 store, since the `CHECK` only guards writes.

Version 9 (#571, spec req 17) adds `owner_settled_unknown`, the reason of the
`cancelled` event `paper settle` journals, and rebuilds `order_events` and
`overrides` (the `settle_order` kind and its `client_order_id`) from version 8 with
every row kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.execution import plan
from tradepartner.store import schema

_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)

#: `order_events` as version 5 created it: the same DDL without the reason `CHECK`.
_V5_ORDER_EVENTS_DDL = schema._CREATE_ORDER_EVENTS.replace(
    f",\n    {schema._check('order_events', 'reason')}", ""
)


@pytest.fixture
def journal() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _event(conn: duckdb.DuckDBPyConnection, reason: str | None, coid: str = "c1") -> None:
    conn.execute(
        "INSERT INTO order_events (client_order_id, status, reason, known_at, ingested_at) "
        "VALUES (?, 'cancelled', ?, ?, ?)",
        [coid, reason, _NOW, _NOW],
    )


#: `resume_invocations` as versions 5 to 7 created it: no `accept_rejections` (#472).
_V7_RESUME_INVOCATIONS_DDL = schema._CREATE_RESUME_INVOCATIONS.replace(
    "    accept_rejections BOOLEAN NOT NULL,\n", ""
)


def _version_5_store(path: Path, reasons: tuple[str | None, ...] = ()) -> Path:
    """A store shaped as version 5 left it (no reason `CHECK`), holding one
    `order_events` row per entry of `reasons`."""
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute("DROP TABLE order_events")
        conn.execute(_V5_ORDER_EVENTS_DDL)
        conn.execute("DROP TABLE resume_acceptances")
        conn.execute("DROP TABLE resume_invocations")
        conn.execute(_V7_RESUME_INVOCATIONS_DDL)
        conn.execute("UPDATE schema_version SET version = 5")
        for index, reason in enumerate(reasons):
            _event(conn, reason, coid=f"c{index}")
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
    """Spec "Data / interfaces" > Tables: `reason ∈ {null, not_received, halt}`."""
    assert schema.HALT_REASON == "halt"
    assert schema.NOT_RECEIVED_REASON == "not_received"
    assert schema.OWNER_SETTLED_UNKNOWN_REASON == "owner_settled_unknown"
    assert schema.ORDER_EVENT_REASONS == (
        schema.HALT_REASON,
        schema.NOT_RECEIVED_REASON,
        schema.OWNER_SETTLED_UNKNOWN_REASON,
    )


def test_the_check_is_built_from_the_shared_constants() -> None:
    assert schema.JOURNAL_ENUMS["order_events", "reason"] == schema.ORDER_EVENT_REASONS
    assert ("order_events", "reason") in schema.NULLABLE_JOURNAL_ENUMS


def test_decision_state_matches_on_the_shared_constants() -> None:
    """The reader whose protection the `CHECK` backs uses the same spelling."""
    assert plan._HALT is schema.HALT_REASON
    assert plan._NOT_RECEIVED is schema.NOT_RECEIVED_REASON
    assert plan._OWNER_SETTLED_UNKNOWN is schema.OWNER_SETTLED_UNKNOWN_REASON


# --- the CHECK ----------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, *schema.ORDER_EVENT_REASONS])
def test_every_listed_reason_and_null_is_accepted(
    journal: duckdb.DuckDBPyConnection, reason: str | None
) -> None:
    _event(journal, reason)
    assert journal.execute("SELECT reason FROM order_events").fetchall() == [(reason,)]


@pytest.mark.parametrize(
    "reason",
    ["halted", "Halt", "not received", "notreceived", "", "owner_settled", "settled_unknown"],
)
def test_a_misspelt_reason_is_refused(journal: duckdb.DuckDBPyConnection, reason: str) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _event(journal, reason)


# --- version 6 and the migration from version 5 ---------------------------------------


def test_fresh_init_records_the_current_version(journal: duckdb.DuckDBPyConnection) -> None:
    assert _versions(journal) == [schema.CURRENT_SCHEMA_VERSION]


#: The tables the migration from version 5 rebuilds or creates (versions 6 to
#: 9; version 10, #660, adds `statement_facts` but rebuilds nothing, so the
#: set stops growing here).
_REBUILT = {"order_events", "decisions", "resume_invocations", "resume_acceptances", "overrides"}


def test_write_open_of_a_version_5_store_adds_the_check_and_keeps_every_row(
    tmp_path: Path, journal: duckdb.DuckDBPyConnection
) -> None:
    reasons = (None, schema.HALT_REASON, schema.NOT_RECEIVED_REASON)
    path = _version_5_store(tmp_path / "v5.duckdb", reasons)
    with duckdb.connect(str(path)) as conn:
        before = conn.execute("SELECT * FROM order_events ORDER BY ALL").fetchall()
        others_before = {
            table: _shape(conn, table)
            for table in schema.JOURNAL_TABLE_NAMES
            if table not in _REBUILT
        }
        schema.init_schema(conn)
        after = conn.execute("SELECT * FROM order_events ORDER BY ALL").fetchall()
        shapes = {table: _shape(conn, table) for table in schema.JOURNAL_TABLE_NAMES}
        versions = _versions(conn)
        with pytest.raises(duckdb.ConstraintException):
            _event(conn, "halted")
    assert after == before
    assert len(after) == len(reasons)
    assert versions == [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    assert shapes == {table: _shape(journal, table) for table in schema.JOURNAL_TABLE_NAMES}
    assert {t: s for t, s in shapes.items() if t not in _REBUILT} == others_before


def test_the_migration_keeps_the_reader_order_of_tied_events(tmp_path: Path) -> None:
    """Readers order an order's events by `known_at`, then rowid; the halt path's
    `cancel_requested` and `cancel_noop` share a stamp, so the rebuild must not
    reorder them."""
    path = _version_5_store(tmp_path / "v5.duckdb")
    statuses = ["pending", "accepted", "cancel_requested", "cancel_noop", "filled"]
    reader = "SELECT status FROM order_events ORDER BY known_at, rowid"
    with duckdb.connect(str(path)) as conn:
        for status in statuses:
            conn.execute(
                "INSERT INTO order_events (client_order_id, status, reason, known_at, "
                "ingested_at) VALUES ('c1', ?, ?, ?, ?)",
                [status, schema.HALT_REASON if status == "cancel_requested" else None, _NOW, _NOW],
            )
        before = [status for (status,) in conn.execute(reader).fetchall()]
        schema.init_schema(conn)
        after = [status for (status,) in conn.execute(reader).fetchall()]
    assert before == statuses
    assert after == statuses


def test_a_migrated_store_reopens_without_another_version_row(tmp_path: Path) -> None:
    path = _version_5_store(tmp_path / "v5.duckdb")
    for _ in range(2):
        with duckdb.connect(str(path)) as conn:
            schema.init_schema(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]


def test_a_stored_reason_outside_the_set_refuses_the_migration_and_changes_nothing(
    tmp_path: Path,
) -> None:
    path = _version_5_store(tmp_path / "v5.duckdb", (schema.HALT_REASON, "halted"))
    with duckdb.connect(str(path)) as conn:
        with pytest.raises(schema.SchemaVersionError, match="halted"):
            schema.init_schema(conn)
        assert _versions(conn) == [5]
        assert sorted(r for (r,) in conn.execute("SELECT reason FROM order_events").fetchall()) == [
            "halt",
            "halted",
        ]
        _event(conn, "still unchecked")  # the version-5 table is untouched


def test_read_only_open_of_a_version_5_store_passes(tmp_path: Path) -> None:
    """Reads do not depend on the `CHECK`, so the dashboard keeps working on a
    store no write has touched since this version was pulled."""
    path = _version_5_store(tmp_path / "v5.duckdb", (schema.HALT_REASON,))
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [5]
        assert conn.execute("SELECT reason FROM order_events").fetchall() == [("halt",)]


# --- version 9 and the migration from version 8 (#571) -------------------------------

#: `order_events` as versions 6 to 8 created it: the reason set without
#: `owner_settled_unknown`.
_V8_ORDER_EVENTS_DDL = schema._CREATE_ORDER_EVENTS.replace(
    schema._check("order_events", "reason"),
    "CHECK (reason IS NULL OR reason IN ('halt', 'not_received'))",
)
#: `overrides` as versions 5 to 8 created it: no `client_order_id`, no
#: `settle_order`.
_V8_OVERRIDES_DDL = (
    schema._CREATE_OVERRIDES.replace("    client_order_id VARCHAR,\n", "")
    .replace(",\n    CHECK ((kind = 'settle_order') = (client_order_id IS NOT NULL))", "")
    .replace(
        schema._check("overrides", "kind"),
        "CHECK (kind IN ('exclude_name', 'keep_name', 'engage_kill_switch'))",
    )
)


def _version_8_store(path: Path, reasons: tuple[str | None, ...] = ()) -> Path:
    """A store shaped as version 8 left it, holding one `order_events` row per entry
    of `reasons` and one `overrides` row of each pre-9 kind, in that order."""
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute("DROP TABLE order_events")
        conn.execute(_V8_ORDER_EVENTS_DDL)
        conn.execute("DROP TABLE overrides")
        conn.execute(_V8_OVERRIDES_DDL)
        conn.execute("UPDATE schema_version SET version = 8")
        for index, reason in enumerate(reasons):
            _event(conn, reason, coid=f"c{index}")
        for override_id, kind in ((3, "keep_name"), (1, "engage_kill_switch"), (2, "exclude_name")):
            conn.execute(
                "INSERT INTO overrides (override_id, window_id, made_at, kind, reason, "
                "known_at, ingested_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                [override_id, _NOW, kind, f"reason {override_id}", _NOW, _NOW],
            )
    return path


def test_the_version_8_shapes_are_what_version_8_created(tmp_path: Path) -> None:
    """The helper's tables really lack version 9's set, column and `CHECK`."""
    path = _version_8_store(tmp_path / "v8.duckdb")
    with duckdb.connect(str(path)) as conn:
        columns = [
            name
            for (name,) in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'overrides'"
            ).fetchall()
        ]
        assert "client_order_id" not in columns
        with pytest.raises(duckdb.ConstraintException):
            _event(conn, schema.OWNER_SETTLED_UNKNOWN_REASON)


def test_write_open_of_a_version_8_store_migrates_to_9_and_keeps_every_row(
    tmp_path: Path, journal: duckdb.DuckDBPyConnection
) -> None:
    reasons = (None, schema.HALT_REASON, schema.NOT_RECEIVED_REASON)
    path = _version_8_store(tmp_path / "v8.duckdb", reasons)
    events_reader = "SELECT * FROM order_events ORDER BY rowid"
    overrides_reader = (
        "SELECT override_id, window_id, made_at, rebalance_session, security_id, kind, "
        "reason, known_at, ingested_at FROM overrides ORDER BY rowid"
    )
    rebuilt = {"order_events", "overrides"}
    with duckdb.connect(str(path)) as conn:
        events_before = conn.execute(events_reader).fetchall()
        overrides_before = conn.execute(overrides_reader).fetchall()
        others_before = {
            table: _shape(conn, table)
            for table in schema.JOURNAL_TABLE_NAMES
            if table not in rebuilt
        }
        schema.init_schema(conn)
        events_after = conn.execute(events_reader).fetchall()
        overrides_after = conn.execute(overrides_reader).fetchall()
        order_ids = conn.execute("SELECT client_order_id FROM overrides").fetchall()
        shapes = {table: _shape(conn, table) for table in schema.JOURNAL_TABLE_NAMES}
        versions = _versions(conn)
        _event(conn, schema.OWNER_SETTLED_UNKNOWN_REASON, coid="settled")
        conn.execute(
            "INSERT INTO overrides (override_id, window_id, made_at, client_order_id, kind, "
            "reason, known_at, ingested_at) VALUES (9, 1, ?, 'tp-1', 'settle_order', 'r', ?, ?)",
            [_NOW, _NOW, _NOW],
        )
        with pytest.raises(duckdb.ConstraintException):
            _event(conn, "halted")
    assert events_after == events_before
    assert len(events_after) == len(reasons)
    assert overrides_after == overrides_before
    assert [row[0] for row in overrides_after] == [3, 1, 2]
    assert order_ids == [(None,)] * 3
    assert versions == [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    assert shapes == {table: _shape(journal, table) for table in schema.JOURNAL_TABLE_NAMES}
    assert {t: s for t, s in shapes.items() if t not in rebuilt} == others_before


def test_a_version_8_store_reopens_at_current_without_another_version_row(tmp_path: Path) -> None:
    path = _version_8_store(tmp_path / "v8.duckdb")
    for _ in range(2):
        with duckdb.connect(str(path)) as conn:
            schema.init_schema(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]


def test_read_only_open_of_a_version_8_store_passes(tmp_path: Path) -> None:
    """Every read but `overrides` works on a store no write has migrated yet."""
    path = _version_8_store(tmp_path / "v8.duckdb", (schema.NOT_RECEIVED_REASON,))
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [8]
        assert conn.execute("SELECT reason FROM order_events").fetchall() == [("not_received",)]

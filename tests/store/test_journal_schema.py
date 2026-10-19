"""Tests for the paper-trading journal tables (Phase 4 plan T49, schema version 5).

The journal tables exist after `init_schema` on a fresh store and after a write
connection opens a version-4 store, which gets appended version-5 to version-10 rows and no
other change: fact and registry DDL are pinned by hash and every fact and registry
table is byte-identical after the migration. A read-only open of a version-4 store
(`conftest.version_4_store`) still passes the schema check and serves fact and
registry reads. The three table-name tuples are pairwise disjoint, every journal
table carries `known_at` and `ingested_at`, and the spec's constraints hold:
`fills.broker_fill_id` unique, `fills.superseded_by` nullable, `overrides.reason`
non-blank, `paper_window_stops.state` including `abandoned`, and a `CHECK` on every
closed enumeration. `tests/lookahead/test_asof_invariance.py` is untouched.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.store import lab_schema, registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import insert_row
from tradepartner.store.journal import (
    _EXPANSION_SEAM_TABLES,
    AlertDeliveryRow,
    AlertRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    alerts_for,
    append,
    fills_for,
    latest_window,
    open_window,
)

_EXPECTED_JOURNAL_TABLES = {
    "paper_windows",
    "paper_window_stops",
    "paper_runs",
    "paper_run_results",
    "paper_plans",
    "rebalance_events",
    "paper_reports",
    "signals",
    "decisions",
    "decision_events",
    "orders",
    "order_events",
    "fills",
    "fill_cursors",
    "resume_invocations",
    "resume_acceptances",
    "outcomes",
    "positions_daily",
    "adjustments",
    "reconciliations",
    "kill_switch",
    "overrides",
    "alerts",
    "alert_deliveries",
    "lots",
    "disposals",
    "wash_sale_flags",
}

#: SHA-256 of `"".join(schema._TABLE_DDL)` and `"".join(schema._REGISTRY_TABLE_DDL)`
#: at version 4, which version 5 must leave as they are -- frozen forever by
#: design (quant-auditor review of #660/T76, PR #729): `statement_facts`
#: (version 10) is additive to the *store*, not to this tuple, which is why it
#: lives in its own `_STATEMENT_FACTS_TABLE_DDL` with its own pin
#: (`test_schema.py`) instead of changing the hash below.
_V4_FACT_DDL_SHA256 = "0815559c58066e74829be4956dea3f252eeddb03ab612cb3485b588c6f44b54a"
_V4_REGISTRY_DDL_SHA256 = "d8152e9e8bd289b606b7de2e976da4ff9b679648a28f4735d54216bca50cd1db"

_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)


def _sha256(parts: tuple[str, ...]) -> str:
    return hashlib.sha256("".join(parts).encode()).hexdigest()


def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database()"
    ).fetchall()
    return {row[0] for row in rows}


def _versions(conn: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY 1").fetchall()
    ]


def _snapshot(conn: duckdb.DuckDBPyConnection, tables: tuple[str, ...]) -> dict[str, Any]:
    """DDL text, constraints, indexes and every row of each table."""
    snapshot: dict[str, Any] = {}
    for table in tables:
        ddl = conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE database_name = current_database() "
            "AND table_name = ?",
            [table],
        ).fetchall()
        constraints = conn.execute(
            "SELECT constraint_type, constraint_text FROM duckdb_constraints() "
            "WHERE database_name = current_database() AND table_name = ? ORDER BY ALL",
            [table],
        ).fetchall()
        indexes = conn.execute(
            "SELECT index_name, sql FROM duckdb_indexes() "
            "WHERE database_name = current_database() AND table_name = ? ORDER BY ALL",
            [table],
        ).fetchall()
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        snapshot[table] = (ddl, constraints, indexes, rows)
    return snapshot


@pytest.fixture
def v4_path(tmp_path: Path) -> Path:
    return version_4_store(tmp_path / "store_v4.duckdb")


@pytest.fixture
def journal() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


# --- names, version, DDL pins -------------------------------------------------------


def test_journal_table_names_are_the_spec_tables() -> None:
    assert set(schema.JOURNAL_TABLE_NAMES) == _EXPECTED_JOURNAL_TABLES
    assert len(schema.JOURNAL_TABLE_NAMES) == len(_EXPECTED_JOURNAL_TABLES)


def test_the_three_name_tuples_are_pairwise_disjoint() -> None:
    facts, registry_, journal_ = (
        set(schema.TABLE_NAMES),
        set(schema.REGISTRY_TABLE_NAMES),
        set(schema.JOURNAL_TABLE_NAMES),
    )
    assert facts & registry_ == set()
    assert facts & journal_ == set()
    assert registry_ & journal_ == set()


def test_current_schema_version_is_30() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 30
    assert schema._PRE_FEE_ACTIVITIES_VERSION == 24
    assert schema._PRE_FILING_NAME_VERSION == 25
    assert schema._PRE_CASH_IN_LIEU_VERSION == 26
    assert schema._PRE_EXPANSION_SEAMS_VERSION == 16


def test_the_version_25_fee_activity_columns_exist_and_are_nullable(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    """#1583, T183: `adjustments.broker_activity_id` and
    `paper_windows.activities_seen_json` are the two version-25 columns, both
    nullable, and `adjustments.kind`'s `CHECK` holds `fee_cash`."""
    assert "broker_activity_id" in _column_order(journal, "adjustments")
    assert "activities_seen_json" in _column_order(journal, "paper_windows")
    nullable = {
        row[0]: row[1]
        for row in journal.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name IN ('adjustments', 'paper_windows')"
        ).fetchall()
    }
    assert nullable["broker_activity_id"] == "YES"
    assert nullable["activities_seen_json"] == "YES"
    ddl = journal.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = 'adjustments'"
    ).fetchone()[0]
    assert "fee_cash" in ddl


def test_broker_activity_id_is_unique_and_nullable_like_broker_fill_id(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    """A broker fee fact is booked once by its id: `broker_activity_id` is
    `UNIQUE` (like `fills.broker_fill_id`) but nullable, so every non-fee
    adjustment (and an unbooked one) carries NULL without colliding."""
    journal.execute(
        "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, account_id, "
        "starting_cash, starting_equity, code_version, started_at, frozen_json, frozen_sha256, "
        "known_at, ingested_at) VALUES (1, 1, ?, 'acct', 100.0, 100.0, 'v1', ?, '{}', 'sha', ?, ?)",
        [_NOW.date(), _NOW, _NOW, _NOW],
    )

    def _add(adjustment_id: int, activity_id: str | None) -> None:
        journal.execute(
            "INSERT INTO adjustments (adjustment_id, window_id, session, kind, cash, "
            "broker_activity_id, known_at, ingested_at) "
            "VALUES (?, 1, ?, 'fee_cash', -0.01, ?, ?, ?)",
            [adjustment_id, _NOW.date(), activity_id, _NOW, _NOW],
        )

    _add(1, None)  # a non-fee or unbooked row: no activity id, never collides
    _add(2, None)
    _add(3, "act-1")
    journal.execute(
        "INSERT INTO adjustments (adjustment_id, window_id, session, kind, cash, known_at, "
        "ingested_at) VALUES (4, 1, ?, 'dividend_cash', 4.5, ?, ?)",
        [_NOW.date(), _NOW, _NOW],
    )
    with pytest.raises(duckdb.ConstraintException):
        _add(5, "act-1")  # the same broker fact again


def _version_24_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store as version 24 left it: every table, one `adjustments` row and one
    `paper_windows` row, without the two version-25 columns, with a version-24
    `schema_version` row (the version-25 delta is removed from the current
    tables, as `_version_16_store` does)."""
    schema.init_schema(conn)
    conn.execute(
        "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, account_id, "
        "starting_cash, starting_equity, code_version, started_at, frozen_json, frozen_sha256, "
        "known_at, ingested_at) VALUES (1, 1, ?, 'acct', 100.0, 100.0, 'v1', ?, '{}', 'sha', ?, ?)",
        [_NOW.date(), _NOW, _NOW, _NOW],
    )
    conn.execute(
        "INSERT INTO adjustments (adjustment_id, window_id, session, kind, origin, security_id, "
        "quantity, cash, explanation_json, known_at, ingested_at) "
        "VALUES (1, 1, ?, 'dividend_cash', NULL, 'SEC_A', NULL, 4.5, '{\"x\": 1}', ?, ?)",
        [_NOW.date(), _NOW, _NOW],
    )
    conn.execute("UPDATE schema_version SET version = 24")
    # `broker_activity_id`'s UNIQUE constraint blocks an in-place drop of the
    # column before it, so `adjustments` is rebuilt without it.
    _rebuild_without(conn, "adjustments", ["broker_activity_id"])
    conn.execute("ALTER TABLE paper_windows DROP COLUMN activities_seen_json")


def test_migrating_a_version_24_store_books_the_fee_columns_once_and_keeps_rows() -> None:
    """The spec's "Migration identity" for version 25 (#1583, T183): the two
    columns are added, the `kind` `CHECK` rebuilt with `fee_cash`, every pre-25
    row kept in insertion order and byte-identical but for the new NULL
    `broker_activity_id`, and one version-25 row appended."""
    conn = duckdb.connect(":memory:")
    try:
        _version_24_store(conn)
        assert "broker_activity_id" not in _column_order(conn, "adjustments")
        assert "activities_seen_json" not in _column_order(conn, "paper_windows")
        before = conn.execute(
            "SELECT adjustment_id, window_id, session, kind, origin, security_id, quantity, "
            "cash, explanation_json, book_id, known_at, ingested_at FROM adjustments ORDER BY rowid"
        ).fetchall()
        schema.init_schema(conn)
        assert "broker_activity_id" in _column_order(conn, "adjustments")
        assert "activities_seen_json" in _column_order(conn, "paper_windows")
        after = conn.execute(
            "SELECT adjustment_id, window_id, session, kind, origin, security_id, quantity, "
            "cash, explanation_json, book_id, known_at, ingested_at, broker_activity_id "
            "FROM adjustments ORDER BY rowid"
        ).fetchall()
        assert [row[:-1] for row in after] == before
        assert all(row[-1] is None for row in after)  # the new column, NULL on every old row
        assert conn.execute("SELECT activities_seen_json FROM paper_windows").fetchall() == [
            (None,)
        ]
        ddl = conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE table_name = 'adjustments'"
        ).fetchone()[0]
        assert "'fee_cash'" in ddl
        assert "'cash_in_lieu'" in ddl  # the same init pass runs the version-27 widen
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 30
    finally:
        conn.close()


def _version_26_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store as version 26 left it (main, T184d/#1597): every table at the
    current shape but with `adjustments`' `kind` `CHECK` without `cash_in_lieu`
    (the version-27 delta not yet applied), one `adjustments` row, and every
    `schema_version` row set to 26. DuckDB cannot add a `CHECK` in place, so the
    table is rebuilt through a staging copy that uses the live `CREATE TABLE`
    with `cash_in_lieu` removed from the `kind` `CHECK` — the same widen this
    version's migration reverses."""
    schema.init_schema(conn)
    conn.execute(
        "INSERT INTO adjustments (adjustment_id, window_id, session, kind, security_id, "
        "quantity, cash, broker_activity_id, explanation_json, known_at, ingested_at) "
        "VALUES (1, 1, ?, 'fee_cash', NULL, NULL, -0.01, 'act-9', '{}', ?, ?)",
        [_NOW.date(), _NOW, _NOW],
    )
    ddl = conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = 'adjustments'"
    ).fetchone()[0]
    v26 = ddl.replace("CREATE TABLE adjustments(", "CREATE TABLE adj_v26(", 1).replace(
        ", 'cash_in_lieu'", ""
    )
    conn.execute(v26)
    conn.execute("INSERT INTO adj_v26 BY NAME SELECT * FROM adjustments ORDER BY rowid")
    conn.execute("DROP TABLE adjustments")
    conn.execute("ALTER TABLE adj_v26 RENAME TO adjustments")
    conn.execute("UPDATE schema_version SET version = 26")


def test_migrating_a_version_26_store_widens_the_kind_check_once_and_keeps_rows() -> None:
    """The spec's "Migration identity" for version 27 (#1627): the `adjustments`
    `kind` `CHECK` widens with `cash_in_lieu` and one version-27 row is appended,
    while every pre-27 row is kept in insertion order and byte-identical. This is
    the real production path (main is at version 26, T184d/#1597, with `fee_cash`
    already booked); it must fail if `_migrate_cash_in_lieu_kind`'s body is a
    no-op."""
    conn = duckdb.connect(":memory:")
    try:
        _version_26_store(conn)
        before = conn.execute("SELECT * FROM adjustments ORDER BY rowid").fetchall()

        schema.init_schema(conn)

        assert conn.execute("SELECT * FROM adjustments ORDER BY rowid").fetchall() == before
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 30
        ddl = conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE table_name = 'adjustments'"
        ).fetchone()[0]
        assert "'cash_in_lieu'" in ddl
        # A `cash_in_lieu` row is accepted now, and a duplicate broker id refused.
        conn.execute(
            "INSERT INTO adjustments (adjustment_id, window_id, session, kind, security_id, "
            "quantity, cash, broker_activity_id, explanation_json, known_at, ingested_at) "
            "VALUES (2, 1, ?, 'cash_in_lieu', 'SEC_A', -0.5, 50.0, 'act-cil', '{}', ?, ?)",
            [_NOW.date(), _NOW, _NOW],
        )
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                "INSERT INTO adjustments (adjustment_id, window_id, session, kind, cash, "
                "broker_activity_id, known_at, ingested_at) "
                "VALUES (3, 1, ?, 'fee_cash', -0.01, 'act-9', ?, ?)",
                [_NOW.date(), _NOW, _NOW],
            )
    finally:
        conn.close()


#: The reason `CHECK` of each table as version 28 left it, in DuckDB's stored
#: form (#1808, T202a): `decision_events` without `skip_asset_unknown` and
#: `skip_not_fractionable`, `order_events` without the three skip reasons.
_V28_REASONS = {
    "decision_events": (
        "skip_below_one_share",
        "skip_untradable",
        "skip_below_minimum",
        "skip_delisted",
        "dust",
        "untradable",
        "unfunded",
    ),
    "order_events": ("halt", "not_received", "owner_settled_unknown"),
}
#: The values version 29 adds to each table's reason `CHECK` (spec amendment
#: item 2, #1808).
_V29_NEW_REASONS = {
    "decision_events": ("skip_asset_unknown", "skip_not_fractionable"),
    "order_events": ("skip_asset_unknown", "skip_not_fractionable", "skip_untradable"),
}
_REASON_CHECK = re.compile(r"CHECK\(\(\(reason IS NULL\) OR \(reason IN \([^)]*\)\)\)\)")
_LATER = _NOW + timedelta(minutes=1)


def _rebuild_with_reasons(
    conn: duckdb.DuckDBPyConnection, table: str, reasons: tuple[str, ...]
) -> None:
    """Rebuild `table` from its live `CREATE TABLE` with the reason `CHECK` set to
    exactly `reasons`, every row kept in insertion order (DuckDB cannot alter a
    `CHECK` in place, as `_version_26_store` notes)."""
    ddl = conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE database_name = current_database() "
        "AND table_name = ?",
        [table],
    ).fetchone()[0]
    allowed = ", ".join(f"'{reason}'" for reason in reasons)
    narrowed, count = _REASON_CHECK.subn(
        f"CHECK(((reason IS NULL) OR (reason IN ({allowed}))))", ddl
    )
    assert count == 1, ddl
    staging = f"{table}_v28"
    conn.execute(narrowed.replace(f"CREATE TABLE {table}(", f"CREATE TABLE {staging}(", 1))
    conn.execute(f"INSERT INTO {staging} BY NAME SELECT * FROM {table} ORDER BY rowid")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")


def _version_28_store(conn: duckdb.DuckDBPyConnection, stray: dict[str, str] | None = None) -> None:
    """A store as version 28 left it (main before T202a): every table at the
    current shape but `decision_events` and `order_events` with their version-28
    reason `CHECK`s, every `schema_version` row set to 28, and four rows in each
    table inserted out of key order with tied `known_at` stamps (so a copy that
    sorted by anything but rowid would reorder them). `stray` maps a table to one
    reason outside its version-29 set, which its `CHECK` is widened to admit and
    which is then stored, as a hand-edited store could hold."""
    schema.init_schema(conn)
    for table, reasons in _V28_REASONS.items():
        extra = (stray[table],) if stray and table in stray else ()
        _rebuild_with_reasons(conn, table, reasons + extra)
    conn.execute("UPDATE schema_version SET version = 28")
    decision_rows: list[tuple[object, ...]] = [
        (7, 2, "skipped", "skip_untradable", None, _NOW, _NOW),
        (3, 2, "written_off", "unfunded", 1234.5678, _NOW, _NOW),
        (9, 1, "skipped", None, None, _NOW, _LATER),
        (1, 2, "skipped", "skip_below_one_share", None, _LATER, _LATER),
    ]
    for row in decision_rows:
        conn.execute(
            "INSERT INTO decision_events (decision_id, run_id, status, reason, "
            "unfunded_notional, known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(row),
        )
    order_rows: list[tuple[object, ...]] = [
        ("tp-9", _NOW, "pending", None, None, None, None, None, _NOW, _NOW),
        ("tp-9", _NOW, "cancel_requested", "halt", "b-9", None, None, '{"a": 1}', _NOW, _NOW),
        ("tp-9", _NOW, "cancel_noop", None, "b-9", 0.25, 101.125, '{"b": 2}', _NOW, _NOW),
        ("tp-2", None, "cancelled", "not_received", None, None, None, None, _NOW, _LATER),
    ]
    for row in order_rows:
        conn.execute(
            "INSERT INTO order_events (client_order_id, event_at, status, reason, "
            "broker_order_id, filled_quantity, filled_avg_price, raw_json, known_at, "
            "ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(row),
        )
    if stray:
        for table, reason in stray.items():
            if table == "decision_events":
                conn.execute(
                    "INSERT INTO decision_events (decision_id, run_id, status, reason, "
                    "known_at, ingested_at) VALUES (5, 2, 'skipped', ?, ?, ?)",
                    [reason, _LATER, _LATER],
                )
            else:
                conn.execute(
                    "INSERT INTO order_events (client_order_id, status, reason, known_at, "
                    "ingested_at) VALUES ('tp-5', 'rejected', ?, ?, ?)",
                    [reason, _LATER, _LATER],
                )


def _insert_reason(conn: duckdb.DuckDBPyConnection, table: str, reason: str | None) -> None:
    if table == "decision_events":
        conn.execute(
            "INSERT INTO decision_events (decision_id, run_id, status, reason, known_at, "
            "ingested_at) VALUES (99, 3, 'skipped', ?, ?, ?)",
            [reason, _LATER, _LATER],
        )
    else:
        conn.execute(
            "INSERT INTO order_events (client_order_id, status, reason, known_at, "
            "ingested_at) VALUES ('tp-99', 'rejected', ?, ?, ?)",
            [reason, _LATER, _LATER],
        )


def _rows_in_insertion_order(conn: duckdb.DuckDBPyConnection, table: str) -> list[Any]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def _ddl(conn: duckdb.DuckDBPyConnection, table: str) -> str:
    return conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE database_name = current_database() "
        "AND table_name = ?",
        [table],
    ).fetchone()[0]


@pytest.mark.parametrize("table", sorted(_V28_REASONS))
def test_the_version_28_helper_refuses_every_version_29_reason(table: str) -> None:
    """The helper's tables really are version 28's: each new reason is refused."""
    conn = duckdb.connect(":memory:")
    try:
        _version_28_store(conn)
        for reason in _V29_NEW_REASONS[table]:
            with pytest.raises(duckdb.ConstraintException):
                _insert_reason(conn, table, reason)
    finally:
        conn.close()


def test_migrating_a_version_28_store_widens_both_reason_checks_once_and_keeps_rows(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    """Version 29 (#1808, T202a; spec amendment item 2): a write open of a
    version-28 store rebuilds `decision_events` and `order_events` with the
    widened reason `CHECK`s, every row kept in insertion order and byte-identical,
    every other table untouched, and one version-29 row appended. It must fail if
    the migration is a no-op or reorders rows."""
    conn = duckdb.connect(":memory:")
    try:
        _version_28_store(conn)
        rebuilt = tuple(sorted(_V28_REASONS))
        # `schema_version` gains the version-29 row, asserted below.
        others = tuple(sorted(_table_names(conn) - {*rebuilt, "schema_version"}))
        rows_before = {table: _rows_in_insertion_order(conn, table) for table in rebuilt}
        others_before = _snapshot(conn, others)

        schema.init_schema(conn)

        assert {table: _rows_in_insertion_order(conn, table) for table in rebuilt} == rows_before
        assert [row[0] for row in rows_before["decision_events"]] == [7, 3, 9, 1]
        assert [row[2] for row in rows_before["order_events"]] == [
            "pending",
            "cancel_requested",
            "cancel_noop",
            "cancelled",
        ]
        assert _snapshot(conn, others) == others_before
        assert _versions(conn) == [28, 29, 30]
        for table in rebuilt:
            assert _ddl(conn, table) == _ddl(journal, table)
            for reason in (*_V28_REASONS[table], *_V29_NEW_REASONS[table], None):
                _insert_reason(conn, table, reason)
            with pytest.raises(duckdb.ConstraintException):
                _insert_reason(conn, table, "skip_asset_unkown")
    finally:
        conn.close()


def test_a_version_28_store_migrates_once_and_reopens_without_another_version_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v28.duckdb"
    with duckdb.connect(str(path)) as conn:
        _version_28_store(conn)
    for _ in range(2):
        with duckdb.connect(str(path)) as conn:
            schema.init_schema(conn)
            assert _versions(conn) == [28, 29, 30]
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [28, 29, 30]


@pytest.mark.parametrize(
    ("table", "stray"),
    [
        ("decision_events", "skip_asset_unkown"),
        ("decision_events", "not_fractionable"),
        ("order_events", "skip_not_fractional"),
        ("order_events", "halted"),
    ],
)
def test_a_stored_reason_outside_the_version_29_set_refuses_the_migration_and_changes_nothing(
    table: str, stray: str
) -> None:
    """Spec amendment item 2, as versions 6 and 7 did: a stored reason outside the
    widened set needs the owner's look, so the write open raises
    `SchemaVersionError` naming it, and neither table, nor the version, changes."""
    conn = duckdb.connect(":memory:")
    try:
        _version_28_store(conn, stray={table: stray})
        both = tuple(sorted(_V28_REASONS))
        before = _snapshot(conn, both)
        with pytest.raises(schema.SchemaVersionError, match=f"'{stray}'"):
            schema.init_schema(conn)
        assert _versions(conn) == [28]
        assert _snapshot(conn, both) == before
    finally:
        conn.close()


def test_read_only_open_of_a_version_28_store_passes(tmp_path: Path) -> None:
    """No read depends on the reason `CHECK`s, and no new reason can exist at 28, so
    a read-only open (the dashboard's) of a store no write has migrated yet passes
    and reads both tables."""
    path = tmp_path / "v28.duckdb"
    with duckdb.connect(str(path)) as conn:
        _version_28_store(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [28]
        assert [
            row[0] for row in conn.execute("SELECT decision_id FROM decision_events").fetchall()
        ] == [7, 3, 9, 1]
        assert conn.execute("SELECT count(*) FROM order_events").fetchone()[0] == 4


def test_the_expansion_seam_table_lists_agree() -> None:
    """`journal.require_journal`'s guard and `schema._migrate_expansion_seams`
    must name the same eight tables (plan T132)."""
    assert tuple(schema._EXPANSION_SEAM_TABLE_DDL) == _EXPANSION_SEAM_TABLES


def test_fact_and_registry_ddl_are_pinned_at_version_4() -> None:
    """Version 5 adds journal tables only. `statement_facts` (version 10, #660,
    T76) is a new fact table but lives outside `_TABLE_DDL`
    (`_STATEMENT_FACTS_TABLE_DDL`, pinned separately in `test_schema.py`), so
    this pin never has to move for it either. Any other fact or registry
    change goes to the next version with its own migration, never an edit of
    this DDL."""
    assert _sha256(schema._TABLE_DDL) == _V4_FACT_DDL_SHA256
    assert _sha256(schema._REGISTRY_TABLE_DDL) == _V4_REGISTRY_DDL_SHA256


# --- fresh store and migration --------------------------------------------------------


def test_fresh_init_creates_the_journal_at_version_17(journal: duckdb.DuckDBPyConnection) -> None:
    assert set(schema.JOURNAL_TABLE_NAMES) <= _table_names(journal)
    assert _versions(journal) == [30]


def test_write_open_of_a_version_4_store_adds_the_journal_and_nothing_else(
    v4_path: Path,
) -> None:
    kept = schema.TABLE_NAMES + schema.REGISTRY_TABLE_NAMES
    # Version 11 (#859) rebuilds the retractable master tables, every row kept
    # (`tests/store/test_retraction_schema.py`); version 12 (#926) adds
    # `trial_results.n_research` (`tests/store/test_research_schema.py`);
    # version 13 (#720, #1033, T85d) adds `trial_rebalances`'s six
    # `PROFITABILITY_REBALANCE_COLUMNS` (`tests/store/test_schema.py`);
    # version 14 (#1153, T127) makes its `n_excluded_no_history` nullable and
    # adds `trial_rebalance_counts`; version 15 (#1179, T97) adds `trials`'
    # vintage and detail columns (`tests/store/test_schema_period.py`);
    # version 16 (#1195, T113) creates the lab tables and rebuilds
    # `owner_decisions` with every row kept (`tests/store/test_lab_migration.py`);
    # version 17 (#1258, T132) rebuilds the eight expanded journal tables with
    # their new defaulted columns (every row kept, `_version_16_store` below).
    kept_without_versions = tuple(
        name
        for name in kept
        if name
        not in ("schema_version", "trial_results", "trial_rebalances", "trials", "owner_decisions")
        and name not in schema.RETRACTABLE_TABLES
        and name not in lab_schema.LAB_TABLE_NAMES
    )
    conn = duckdb.connect(str(v4_path))
    try:
        assert _table_names(conn).isdisjoint(schema.JOURNAL_TABLE_NAMES)
        before = _snapshot(conn, kept_without_versions)
        assert before["prices_daily"][3], "the fixture universe should load price rows"
        applied_before = conn.execute("SELECT * FROM schema_version").fetchall()
        schema.init_schema(conn)
        after = _snapshot(conn, kept_without_versions)
        versions = conn.execute("SELECT * FROM schema_version ORDER BY version").fetchall()
        tables = _table_names(conn)
    finally:
        conn.close()
    assert after == before
    assert versions[:1] == applied_before
    assert [row[0] for row in versions] == [
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
    ]
    # `store_markers`: the fixture loader's marker table (strategy-lab plan T101).
    assert tables == set(kept) | set(schema.JOURNAL_TABLE_NAMES) | set(
        schema.MASTER_CHECK_TABLE_NAMES
    ) | set(schema.RESEARCH_TABLE_NAMES) | {schema.REBALANCE_COUNTS_TABLE_NAME, "store_markers"}


def test_migrated_journal_matches_a_fresh_store(
    v4_path: Path, journal: duckdb.DuckDBPyConnection
) -> None:
    conn = duckdb.connect(str(v4_path))
    try:
        schema.init_schema(conn)
        migrated = _snapshot(conn, schema.JOURNAL_TABLE_NAMES)
    finally:
        conn.close()
    assert migrated == _snapshot(journal, schema.JOURNAL_TABLE_NAMES)


def test_a_migrated_store_reopens_without_another_version_row(v4_path: Path) -> None:
    for _ in range(2):
        conn = duckdb.connect(str(v4_path))
        schema.init_schema(conn)
        conn.close()
    with duckdb.connect(str(v4_path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            25,
            26,
            27,
            28,
            29,
            30,
        ]


# --- version 17 (#1258, T132): the ADR 0015 expansion seams --------------------------


#: The columns schema version 17 adds, by table (ADR 0015 seams 1 to 3, plan
#: T132): `_version_16_store`'s table is the version-17 one without them.
_V17_ADDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "paper_windows": ("book_id",),
    "decisions": ("position_side", "book_id"),
    "orders": (
        "position_side",
        "order_type",
        "time_in_force",
        "limit_price",
        "stop_price",
        "asset_class",
        "order_class",
        "multiplier",
        "parent_order_id",
        "book_id",
    ),
    "positions_daily": ("position_side", "book_id"),
    "lots": ("position_side", "book_id"),
    "disposals": ("position_side", "book_id"),
    "adjustments": ("book_id",),
    "reconciliations": ("book_id",),
}


def _version_16_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store as version 16 left it: the eight tables ADR 0015 seams 1 to 3
    expand, each holding one row, without the version-17 columns and with a
    version-16 `schema_version` row (`tests/store/test_lab_migration.py`'s
    `_version_15_store`, adapted: the version-17 delta is *removed from* the
    current tables rather than never created, because version 17 deletes
    nothing and every other table is byte-identical either way)."""
    schema.init_schema(conn)
    session = _NOW.date()
    conn.execute(
        "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, "
        "account_id, starting_cash, starting_equity, code_version, started_at, frozen_json, "
        "frozen_sha256, known_at, ingested_at) VALUES (1, 1, ?, 'acct', 100.0, 100.0, "
        "'v1', ?, '{}', 'sha', ?, ?)",
        [session, _NOW, _NOW, _NOW],
    )
    conn.execute(
        "INSERT INTO decisions (decision_id, run_id, security_id, whole_share, decision, "
        "known_at, ingested_at) VALUES (3, 1, 'SEC_A', true, 'trade', ?, ?)",
        [_NOW.replace(second=3), _NOW.replace(second=3)],
    )
    conn.execute(
        "INSERT INTO decisions (decision_id, run_id, security_id, whole_share, decision, "
        "known_at, ingested_at) VALUES (1, 1, 'SEC_A', true, 'trade', ?, ?)",
        [_NOW.replace(second=1), _NOW.replace(second=1)],
    )
    conn.execute("DELETE FROM decisions WHERE decision_id = 1")
    conn.execute(
        "INSERT INTO decisions (decision_id, run_id, security_id, whole_share, decision, "
        "known_at, ingested_at) VALUES (2, 1, 'SEC_A', true, 'trade', ?, ?)",
        [_NOW.replace(second=2), _NOW.replace(second=2)],
    )
    for client_order_id, second in (("tp-3", 3), ("tp-1", 1)):
        conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
            "security_id, symbol, side, sells_in_flight_at_submit, known_at, ingested_at) "
            "VALUES (?, 1, 1, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', true, ?, ?)",
            [client_order_id, session, _NOW.replace(second=second), _NOW.replace(second=second)],
        )
    conn.execute("DELETE FROM orders WHERE client_order_id = 'tp-1'")
    conn.execute(
        "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
        "security_id, symbol, side, sells_in_flight_at_submit, known_at, ingested_at) "
        "VALUES ('tp-2', 1, 1, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', true, ?, ?)",
        [session, _NOW.replace(second=2), _NOW.replace(second=2)],
    )
    conn.execute(
        "INSERT INTO positions_daily (run_id, session, security_id, quantity, known_at, "
        "ingested_at) VALUES (1, ?, 'SEC_A', 2.0, ?, ?)",
        [session, _NOW, _NOW],
    )
    conn.execute(
        "INSERT INTO lots (lot_id, account_id, account_type, account_owner, security_id, "
        "symbol, trade_at, trade_date_local, quantity, cost_basis, known_at, ingested_at) "
        "VALUES (1, 'acct', 'individual', 'owner', 'SEC_A', 'SEC_A', ?, ?, 2.0, 10.0, ?, ?)",
        [_NOW, session, _NOW, _NOW],
    )
    conn.execute(
        "INSERT INTO disposals (disposal_id, lot_id, account_id, trade_at, trade_date_local, "
        "quantity, proceeds, realised_pnl, tax_year, known_at, ingested_at) "
        "VALUES (1, 1, 'acct', ?, ?, 1.0, 12.0, 2.0, 2026, ?, ?)",
        [_NOW, session, _NOW, _NOW],
    )
    conn.execute(
        "INSERT INTO adjustments (adjustment_id, window_id, session, kind, known_at, "
        "ingested_at) VALUES (1, 1, ?, 'dividend_cash', ?, ?)",
        [session, _NOW, _NOW],
    )
    conn.execute(
        'INSERT INTO reconciliations (reconciliation_id, window_id, "at", status, known_at, '
        "ingested_at) VALUES (1, 1, ?, 'ok', ?, ?)",
        [_NOW, _NOW, _NOW],
    )
    conn.execute("UPDATE schema_version SET version = 16")
    for table, columns in _V17_ADDED_COLUMNS.items():
        if table == "adjustments":
            # Version 25's UNIQUE `broker_activity_id` sits after `book_id`, so
            # DuckDB blocks an in-place `DROP COLUMN book_id`: rebuild instead.
            _rebuild_without(conn, table, columns)
            continue
        for column in columns:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


@pytest.fixture
def v16_path(tmp_path: Path) -> Path:
    path = tmp_path / "store_v16.duckdb"
    conn = duckdb.connect(str(path))
    try:
        _version_16_store(conn)
    finally:
        conn.close()
    return path


def test_migrating_a_version_16_store_keeps_every_row_and_fills_the_new_columns() -> None:
    """The spec's "Migration identity" for version 17: every row of the eight
    expanded tables is kept in insertion order (not key order: `decisions` and
    `orders` hold two rows inserted out of key order, with one deleted in
    between), each pre-version-17 column — `known_at` and `ingested_at`
    included — unchanged, and its new columns at their defaults
    (`book_id = 'main'`, `position_side = 'long'`, the `orders` shape from
    `ORDER_SHAPE_DEFAULTS`)."""
    conn = duckdb.connect(":memory:")
    try:
        _version_16_store(conn)
        old_columns = {table: _column_order(conn, table) for table in _V17_ADDED_COLUMNS}
        for table in _V17_ADDED_COLUMNS:
            assert "book_id" not in old_columns[table]
        before = {
            table: conn.execute(
                f"SELECT {', '.join(f'"{c}"' for c in columns)} FROM {table} ORDER BY rowid"
            ).fetchall()
            for table, columns in old_columns.items()
        }
        schema.init_schema(conn)
        assert _versions(conn) == [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
        assert conn.execute("SELECT window_id, book_id FROM paper_windows").fetchall() == [
            (1, "main")
        ]
        assert conn.execute(
            "SELECT decision_id, position_side, book_id FROM decisions ORDER BY rowid"
        ).fetchall() == [(3, "long", "main"), (2, "long", "main")]
        assert conn.execute(
            "SELECT client_order_id, position_side, order_type, time_in_force, limit_price, "
            "stop_price, asset_class, order_class, multiplier, parent_order_id, book_id "
            "FROM orders ORDER BY rowid"
        ).fetchall() == [
            ("tp-3", "long", "market", "day", None, None, "us_equity", "simple", 1.0, None, "main"),
            ("tp-2", "long", "market", "day", None, None, "us_equity", "simple", 1.0, None, "main"),
        ]
        assert conn.execute(
            "SELECT run_id, position_side, book_id FROM positions_daily"
        ).fetchall() == [(1, "long", "main")]
        assert conn.execute("SELECT lot_id, position_side, book_id FROM lots").fetchall() == [
            (1, "long", "main")
        ]
        assert conn.execute(
            "SELECT disposal_id, position_side, book_id FROM disposals"
        ).fetchall() == [(1, "long", "main")]
        assert conn.execute("SELECT adjustment_id, book_id FROM adjustments").fetchall() == [
            (1, "main")
        ]
        assert conn.execute(
            "SELECT reconciliation_id, book_id FROM reconciliations"
        ).fetchall() == [(1, "main")]
        # Every pre-version-17 column of every row, `known_at` and `ingested_at`
        # included, is identical in rowid order: the rebuild neither restamps
        # nor reorders (quant-auditor finding on pass 1).
        for table, columns in old_columns.items():
            after = conn.execute(
                f"SELECT {', '.join(f'"{c}"' for c in columns)} FROM {table} ORDER BY rowid"
            ).fetchall()
            assert after == before[table], table
            assert _column_order(conn, table)[-2:] == ["known_at", "ingested_at"], table
        assert [row[0] for row in before["decisions"]] == [3, 2]
        assert [row[0] for row in before["orders"]] == ["tp-3", "tp-2"]
    finally:
        conn.close()


def test_the_version_16_migration_leaves_every_other_table_byte_identical() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _version_16_store(conn)
        others = tuple(
            table
            for table in sorted(_table_names(conn))
            if table not in _V17_ADDED_COLUMNS and table != "schema_version"
        )
        before = _snapshot(conn, others)
        schema.init_schema(conn)
        assert _snapshot(conn, others) == before
    finally:
        conn.close()


def test_a_version_16_store_gets_no_new_version_row_on_a_second_open() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _version_16_store(conn)
        schema.init_schema(conn)
        first = _snapshot(conn, tuple(sorted(_table_names(conn))))
        schema.init_schema(conn)
        assert _snapshot(conn, tuple(sorted(_table_names(conn)))) == first
        assert _versions(conn) == [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    finally:
        conn.close()


def test_a_version_4_store_gets_the_version_17_journal_directly(v4_path: Path) -> None:
    conn = duckdb.connect(str(v4_path))
    try:
        schema.init_schema(conn)
        assert "book_id" in _columns(conn, "orders")
        assert "position_side" in _columns(conn, "positions_daily")
        assert "multiplier" in _columns(conn, "orders")
    finally:
        conn.close()


def test_read_only_open_of_a_version_16_store_serves_fact_and_registry_reads(
    v16_path: Path,
) -> None:
    with duckdb.connect(str(v16_path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [16]
        assert registry.list_trials(conn) == []
        assert prices_as_of(conn, datetime(2019, 6, 28, 20, 0, tzinfo=UTC)).height == 0


def test_read_only_open_of_a_version_16_store_raises_schema_version_error_for_journal_reads(
    v16_path: Path,
) -> None:
    """#1261: a read-only view of a version-16 store renders the fix, not a
    traceback. Every read of the eight expanded tables goes through
    `require_journal`, whose message names the fix (open it for writing once)."""
    with duckdb.connect(str(v16_path), read_only=True) as conn:
        schema.init_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone() is not None
        with pytest.raises(schema.SchemaVersionError, match="book_id"):
            fills_for(conn)
        with pytest.raises(schema.SchemaVersionError, match="open it for writing once"):
            latest_window(conn)


# --- version 19 (#1370, T154; ADR 0017 B.6): `alerts.book_id` -------------------------


def _version_18_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store as version 18 left it, shaped like the owner's store while H1's
    window is open: book `main`'s window 1, open (no stop row), with a run and its
    result, and `alerts` without `book_id` holding a run-scoped alert, the two
    session-scoped kinds (`no_window` from before the window, `locked`) and their
    delivery rows, inserted out of key order with one deleted in between, so
    insertion order is not key order. The version-19 delta is removed from the
    current table, as `_version_16_store` does for version 17."""
    schema.init_schema(conn)
    session = _NOW.date()
    append(
        conn,
        PaperWindowRow(
            window_id=1,
            hypothesis_id=1,
            first_rebalance_session=session,
            account_id="PA_H1",
            starting_cash=100_000.0,
            starting_equity=100_000.0,
            code_version="v1",
            started_at=_NOW,
            frozen_json='{"cadence": "month_end"}',
            frozen_sha256="0" * 64,
            known_at=_NOW,
            ingested_at=_NOW,
        ),
    )
    append(
        conn,
        PaperRunRow(
            run_id=1,
            window_id=1,
            session=session,
            kind="rebalance",
            started_at=_NOW,
            invoked_by="scheduler",
            code_version="v1",
            known_at=_NOW,
            ingested_at=_NOW,
        ),
    )
    append(
        conn,
        PaperRunResultRow(
            run_id=1,
            finished_at=_NOW,
            status="ok",
            clock_fault=False,
            known_at=_NOW,
            ingested_at=_NOW,
        ),
    )
    for alert_id, run_id, kind, second in (
        (3, 1, "stale_data", 3),
        (1, None, "no_window", 1),
        (2, None, "locked", 2),
        (4, None, "locked", 4),
    ):
        stamp = _NOW.replace(second=second)
        append(
            conn,
            AlertRow(
                alert_id=alert_id,
                run_id=run_id,
                session=session,
                kind=kind,
                message=f"{kind} {alert_id}",
                at=stamp,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
        append(
            conn,
            AlertDeliveryRow(
                alert_id=alert_id,
                channel="store",
                at=stamp,
                ok=True,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
    conn.execute("DELETE FROM alerts WHERE alert_id = 2")
    conn.execute("DELETE FROM alert_deliveries WHERE alert_id = 2")
    conn.execute("UPDATE schema_version SET version = 18")
    conn.execute("ALTER TABLE alerts DROP COLUMN book_id")


@pytest.fixture
def v18_path(tmp_path: Path) -> Path:
    path = tmp_path / "store_v18.duckdb"
    conn = duckdb.connect(str(path))
    try:
        _version_18_store(conn)
    finally:
        conn.close()
    return path


def test_migrating_a_version_18_store_keeps_every_alert_and_books_it_main() -> None:
    """Every `alerts` row is kept in insertion order, each pre-version-19 column,
    `known_at` and `ingested_at` included, unchanged, and `book_id = 'main'`; the
    column sits before the two timestamps, as on a fresh store."""
    conn = duckdb.connect(":memory:")
    try:
        _version_18_store(conn)
        old_columns = _column_order(conn, "alerts")
        assert "book_id" not in old_columns
        select = f"SELECT {', '.join(f'"{c}"' for c in old_columns)} FROM alerts ORDER BY rowid"
        before = conn.execute(select).fetchall()
        assert [row[0] for row in before] == [3, 1, 4]
        schema.init_schema(conn)
        assert _versions(conn) == [18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
        assert conn.execute(select).fetchall() == before
        assert conn.execute("SELECT alert_id, book_id FROM alerts ORDER BY rowid").fetchall() == [
            (3, "main"),
            (1, "main"),
            (4, "main"),
        ]
        fresh = duckdb.connect(":memory:")
        schema.init_schema(fresh)
        assert _column_order(conn, "alerts") == _column_order(fresh, "alerts")
        assert _column_order(conn, "alerts")[-3:] == ["book_id", "known_at", "ingested_at"]
        fresh.close()
        with pytest.raises(duckdb.ConstraintException):  # the primary key survives
            conn.execute(
                'INSERT INTO alerts (alert_id, session, kind, message, "at", known_at, '
                "ingested_at) VALUES (1, ?, 'locked', 'm', ?, ?, ?)",
                [_NOW.date(), _NOW, _NOW, _NOW],
            )
    finally:
        conn.close()


def test_the_version_19_migration_leaves_mains_open_window_and_other_tables_unchanged() -> None:
    """H1's window is open on the owner's store when version 19 lands: the
    migration changes no row outside `alerts`, `main`'s open window reads the same
    before and after, and the alert dedupe for `main` finds the same rows."""
    conn = duckdb.connect(":memory:")
    try:
        _version_18_store(conn)
        others = tuple(
            table
            for table in sorted(_table_names(conn))
            if table not in ("alerts", "schema_version")
        )
        before = _snapshot(conn, others)
        window_before = open_window(conn, "main")
        assert window_before is not None and window_before.window_id == 1
        schema.init_schema(conn)
        assert _snapshot(conn, others) == before
        assert open_window(conn, "main") == window_before == open_window(conn)
        assert latest_window(conn, "main") == window_before
        assert open_window(conn, "b") is None
        assert [a.alert_id for a in alerts_for(conn, kind="locked", session=_NOW.date())] == [4]
        assert alerts_for(conn, kind="locked", session=_NOW.date(), book_id="b") == []
    finally:
        conn.close()


def test_a_version_18_store_gets_no_new_version_row_on_a_second_open() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _version_18_store(conn)
        schema.init_schema(conn)
        first = _snapshot(conn, tuple(sorted(_table_names(conn))))
        schema.init_schema(conn)
        assert _snapshot(conn, tuple(sorted(_table_names(conn)))) == first
        assert _versions(conn) == [18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    finally:
        conn.close()


def test_a_failed_version_19_migration_leaves_the_store_at_18(
    v18_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rebuild = schema._migrate_alert_books

    def boom(conn: duckdb.DuckDBPyConnection) -> None:
        rebuild(conn)  # the rebuild happens, then the migration fails
        raise RuntimeError("injected")

    with duckdb.connect(str(v18_path)) as conn:
        everything = tuple(sorted(_table_names(conn)))
        before = _snapshot(conn, everything)
        monkeypatch.setattr(schema, "_migrate_alert_books", boom)
        with pytest.raises(RuntimeError, match="injected"):
            schema.init_schema(conn)
        assert _snapshot(conn, everything) == before
        assert _versions(conn) == [18]
        assert "book_id" not in _column_order(conn, "alerts")


def test_read_only_open_of_a_version_18_store_serves_the_window_reads(v18_path: Path) -> None:
    """A read-only view (the ops page, `paper status`) of the owner's version-18
    store before any writer has migrated it: the version check passes, nothing
    changes, and `main`'s window reads as before."""
    with duckdb.connect(str(v18_path), read_only=True) as conn:
        everything = tuple(sorted(_table_names(conn)))
        before = _snapshot(conn, everything)
        schema.init_schema(conn)
        assert _versions(conn) == [18]
        assert (window := open_window(conn, "main")) is not None and window.window_id == 1
        assert latest_window(conn) == window
        assert _snapshot(conn, everything) == before


def test_a_version_4_store_gets_alerts_book_id_directly(v4_path: Path) -> None:
    with duckdb.connect(str(v4_path)) as conn:
        schema.init_schema(conn)
        assert _columns(conn, "alerts")["book_id"] is False  # NOT NULL
        assert _versions(conn)[-1] == 30


def test_default_book_id_is_a_nonempty_word() -> None:
    assert re.fullmatch(r"[A-Za-z0-9]+", schema.DEFAULT_BOOK_ID)


@pytest.mark.parametrize(
    "table",
    sorted(table for table, columns in _V17_ADDED_COLUMNS.items() if "position_side" in columns),
)
def test_a_position_side_outside_the_set_is_refused_on_each_expanded_table(
    journal: duckdb.DuckDBPyConnection, table: str
) -> None:
    """ADR 0015 seam 2: `position_side` is a closed set on the five tables that
    carry it."""
    assert schema.JOURNAL_ENUMS[table, "position_side"] == schema.POSITION_SIDES
    assert schema.LONG in schema.POSITION_SIDES
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, table, {**_minimal_v17_row(table), "position_side": "sideways"})


def _minimal_v17_row(table: str) -> dict[str, object]:
    """A minimal valid row for each table that carries `position_side` (its old
    columns, since a fresh version-17 store fills the new ones by default)."""
    session = _NOW.date()
    base: dict[str, dict[str, object]] = {
        "decisions": {
            "decision_id": 1,
            "run_id": 1,
            "security_id": "SEC_A",
            "whole_share": True,
            "decision": "trade",
        },
        "orders": {
            "client_order_id": "tp-1",
            "decision_id": 1,
            "run_id": 1,
            "session": session,
            "attempt": 1,
            "phase": "buy",
            "security_id": "SEC_A",
            "symbol": "SEC_A",
            "side": "buy",
            "sells_in_flight_at_submit": True,
        },
        "positions_daily": {
            "run_id": 1,
            "session": session,
            "security_id": "SEC_A",
            "quantity": 2.0,
        },
        "lots": {
            "lot_id": 1,
            "account_id": "acct",
            "account_type": "individual",
            "account_owner": "owner",
            "security_id": "SEC_A",
            "symbol": "SEC_A",
            "trade_at": _NOW,
            "trade_date_local": session,
            "quantity": 2.0,
            "cost_basis": 10.0,
        },
        "disposals": {
            "disposal_id": 1,
            "lot_id": 1,
            "account_id": "acct",
            "trade_at": _NOW,
            "trade_date_local": session,
            "quantity": 1.0,
            "proceeds": 12.0,
            "realised_pnl": 2.0,
            "tax_year": 2026,
        },
    }
    return {**base[table], "known_at": _NOW, "ingested_at": _NOW}


def test_an_unknown_later_version_is_refused(tmp_path: Path) -> None:
    later = schema.CURRENT_SCHEMA_VERSION + 1
    path = tmp_path / f"store_v{later}.duckdb"
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute("INSERT INTO schema_version VALUES (?, ?)", [later, _NOW])
    for read_only in (False, True):
        with (
            duckdb.connect(str(path), read_only=read_only) as conn,
            pytest.raises(schema.SchemaVersionError, match=str(later)),
        ):
            schema.init_schema(conn)


# --- read-only on a version-4 store -------------------------------------------------


def test_read_only_open_of_a_version_4_store_serves_fact_and_registry_reads(
    v4_path: Path,
) -> None:
    with duckdb.connect(str(v4_path)) as conn:  # a registry row, without migrating
        conn.execute(
            "INSERT INTO hypotheses VALUES (1, 'h', 'momentum', 't', 'p', ?, '{}', ?, "
            "DATE '2018-01-31', DATE '2019-06-03', DATE '2020-06-30', ?, 'test')",
            ["0" * 64, "0" * 64, _NOW],
        )
    with duckdb.connect(str(v4_path), read_only=True) as conn:
        schema.init_schema(conn)  # a version check only: passes, migrates nothing
        (bars,) = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
        hypothesis = registry.get_hypothesis(conn, "h")
        assert registry.list_trials(conn) == []
        assert _versions(conn) == [4]
        assert _table_names(conn).isdisjoint(schema.JOURNAL_TABLE_NAMES)
    assert bars > 0
    assert hypothesis.family == "momentum"


def test_asof_reads_work_read_only_on_a_version_4_store(v4_path: Path) -> None:
    with duckdb.connect(str(v4_path), read_only=True) as conn:
        schema.init_schema(conn)
        frame = prices_as_of(conn, datetime(2019, 6, 28, 20, 0, tzinfo=UTC))
    assert frame.height > 0


# --- columns and constraints -------------------------------------------------------------


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, bool]:
    """Column name -> nullable."""
    rows = conn.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return {name: nullable == "YES" for name, nullable in rows}


def _column_order(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """The table's column names in ordinal order."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]


def _rebuild_without(
    conn: duckdb.DuckDBPyConnection, table: str, columns: tuple[str, ...] | list[str]
) -> None:
    """Rebuild `table` through a staging copy that omits `columns`, keeping every
    remaining column and its rows in `rowid` order. This is how a column whose
    drop DuckDB blocks (an earlier column before a later `UNIQUE`, e.g. version
    16's `book_id` before version 25's `broker_activity_id`, or that `UNIQUE`
    `broker_activity_id` itself) is removed from an older-version simulation; the
    in-place migration rebuilds the table from its DDL afterwards."""
    drop = set(columns)
    keep = [name for name in _column_order(conn, table) if name not in drop]
    quoted = ", ".join(f'"{name}"' for name in keep)
    staging = f"{table}_older"
    conn.execute(f"CREATE TABLE {staging} AS SELECT {quoted} FROM {table} ORDER BY rowid")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")


@pytest.mark.parametrize("table", sorted(_EXPECTED_JOURNAL_TABLES))
def test_every_journal_table_carries_both_timestamps_not_null(
    journal: duckdb.DuckDBPyConnection, table: str
) -> None:
    columns = _columns(journal, table)
    assert columns["known_at"] is False
    assert columns["ingested_at"] is False
    assert "provenance" not in columns  # not a fact table (`source` is a spec column on two)


def _row(table: str, **values: object) -> dict[str, object]:
    """A minimal valid row for the constraint probes below."""
    base: dict[str, dict[str, object]] = {
        "fills": {
            "fill_id": 1,
            "client_order_id": "tp-20261001-SEC_A-buy-1",
            "filled_at": _NOW,
            "quantity": 1.5,
            "price": 10.0,
            "price_implied": False,
            "broker_fill_id": "bf-1",
            "source": "broker_feed",
        },
        "overrides": {
            "override_id": 1,
            "window_id": 1,
            "made_at": _NOW,
            "kind": "exclude_name",
            "reason": "owner excludes this name for a reason",
        },
        "paper_window_stops": {"window_id": 1, "at": _NOW, "state": "requested"},
        "paper_runs": {
            "run_id": 1,
            "window_id": 1,
            "session": _NOW.date(),
            "kind": "mark",
            "started_at": _NOW,
            "invoked_by": "scheduler",
            "code_version": "abc",
        },
        "positions_daily": {
            "run_id": 1,
            "session": _NOW.date(),
            "security_id": "SEC_A",
            "quantity": 2.0,
            "cash": 100.0,
        },
        "alerts": {
            "alert_id": 1,
            "session": _NOW.date(),
            "kind": "no_window",
            "message": "m",
            "at": _NOW,
        },
        "signals": {
            "run_id": 1,
            "rebalance_session": _NOW.date(),
            "security_id": "SEC_A",
            "reason": "selected",
        },
    }
    return {**base[table], "known_at": _NOW, "ingested_at": _NOW, **values}


def _insert(conn: duckdb.DuckDBPyConnection, table: str, row: dict[str, object]) -> None:
    columns = ", ".join(f'"{name}"' for name in row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))


def test_known_at_after_ingested_at_is_refused(journal: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "fills", _row("fills", known_at=_NOW + timedelta(seconds=1)))


@pytest.mark.parametrize("table", sorted(_EXPECTED_JOURNAL_TABLES))
def test_every_journal_table_checks_known_at_not_after_ingested_at(
    journal: duckdb.DuckDBPyConnection, table: str
) -> None:
    checks = journal.execute(
        "SELECT constraint_text FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'CHECK'",
        [table],
    ).fetchall()
    assert ("CHECK((known_at <= ingested_at))",) in checks


def test_known_at_is_required(journal: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "fills", _row("fills", known_at=None))


def test_broker_fill_id_is_unique_and_superseded_by_nullable(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    _insert(journal, "fills", _row("fills"))
    _insert(journal, "fills", _row("fills", fill_id=2, broker_fill_id="bf-2", superseded_by=1))
    assert journal.execute("SELECT superseded_by FROM fills ORDER BY fill_id").fetchall() == [
        (None,),
        (1,),
    ]
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "fills", _row("fills", fill_id=3))  # bf-1 again


def test_an_implied_fill_is_exactly_a_broker_status_row_at_any_price(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    implied = _row(
        "fills",
        fill_id=2,
        broker_fill_id="synthetic:tp-20261001-SEC_A-buy-1",
        source="broker_status",
        price=-0.5,
        price_implied=True,
    )
    _insert(journal, "fills", implied)
    for bad in (
        _row("fills", fill_id=3, broker_fill_id="bf-3", price=0.0),  # feed price must be > 0
        _row("fills", fill_id=4, broker_fill_id="bf-4", price_implied=True),
        {**implied, "fill_id": 5, "broker_fill_id": "synthetic:x", "price_implied": False},
        _row("fills", fill_id=6, broker_fill_id="bf-6", quantity=0.0),
    ):
        with pytest.raises(duckdb.ConstraintException):
            _insert(journal, "fills", bad)


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("fills", "price_implied"),
        ("decisions", "whole_share"),
        ("paper_run_results", "clock_fault"),
    ],
)
def test_decided_flags_have_no_default(
    journal: duckdb.DuckDBPyConnection, table: str, column: str
) -> None:
    """A writer must state these; a silent FALSE would mislead the lot ledger, the
    remainder rule and the halt record."""
    [(default, nullable)] = journal.execute(
        "SELECT column_default, is_nullable FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchall()
    assert (default, nullable) == (None, "NO")


def test_a_non_session_run_has_neither_session_nor_kind(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    _insert(journal, "paper_runs", _row("paper_runs", session=None, kind=None))
    for run_id, values in ((2, {"session": None}), (3, {"kind": None})):
        with pytest.raises(duckdb.ConstraintException):
            _insert(journal, "paper_runs", _row("paper_runs", run_id=run_id, **values))


def test_a_flat_session_is_marked_by_a_cash_only_row(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    _insert(journal, "positions_daily", _row("positions_daily", security_id=None, quantity=0.0))
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "positions_daily", _row("positions_daily", security_id=None))


def test_an_alert_always_has_its_dedupe_session(journal: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "alerts", _row("alerts", session=None))


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_override_reason_is_refused(
    journal: duckdb.DuckDBPyConnection, reason: str
) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "overrides", _row("overrides", reason=reason))


def test_window_stop_states_include_abandoned(journal: duckdb.DuckDBPyConnection) -> None:
    assert schema.JOURNAL_ENUMS["paper_window_stops", "state"] == (
        "requested",
        "closed",
        "abandoned",
    )
    for state in ("requested", "closed", "abandoned"):
        _insert(journal, "paper_window_stops", _row("paper_window_stops", state=state))
    with pytest.raises(duckdb.ConstraintException):
        _insert(journal, "paper_window_stops", _row("paper_window_stops", state="paused"))


def test_signal_reasons_are_checked_by_prefix_not_by_closed_set(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    """`signals.reason` is no closed enum since version 14 (#1153, T127; ADR 0014
    point 3): its own `CHECK` accepts `selected`, `below_cut` and any
    `excluded_<reason>`, refuses anything else and NULL."""
    assert ("signals", "reason") not in schema.JOURNAL_ENUMS
    assert schema.SIGNAL_REASONS == ("selected", "below_cut")
    for reason in ("selected", "below_cut", "excluded_no_history", "excluded_sector"):
        _insert(journal, "signals", _row("signals", reason=reason))
    for reason in ("foo", "excluded", "Excluded_sector", None):
        with pytest.raises(duckdb.ConstraintException):
            _insert(journal, "signals", _row("signals", reason=reason))


@pytest.mark.parametrize(("table", "column"), sorted(schema.JOURNAL_ENUMS))
def test_every_enumerated_column_has_its_check(
    journal: duckdb.DuckDBPyConnection, table: str, column: str
) -> None:
    """The DDL carries each closed set exactly: the `CHECK` names every value and
    `NULL` is allowed only for the columns the spec lists `null` for."""
    [check] = [
        text
        for (text,) in journal.execute(
            "SELECT constraint_text FROM duckdb_constraints() "
            "WHERE table_name = ? AND constraint_type = 'CHECK'",
            [table],
        ).fetchall()
        if re.search(rf'\(\(?"?{column}"? IN \(', text)
    ]
    for value in schema.JOURNAL_ENUMS[table, column]:
        assert f"'{value}'" in check
    assert ("IS NULL" in check) == ((table, column) in schema.NULLABLE_JOURNAL_ENUMS)
    assert _columns(journal, table)[column] == ((table, column) in schema.NULLABLE_JOURNAL_ENUMS)


def test_journal_session_columns_are_dates(journal: duckdb.DuckDBPyConnection) -> None:
    """A session is a calendar day, never an instant (calendar.py)."""
    rows = journal.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE column_name LIKE '%session%' AND table_name IN "
        f"({', '.join(repr(t) for t in schema.JOURNAL_TABLE_NAMES)})"
    ).fetchall()
    assert rows
    assert {data_type for _, _, data_type in rows} == {"DATE"}


def test_insert_row_writes_a_column_named_at(journal: duckdb.DuckDBPyConnection) -> None:
    """`at` is a DuckDB keyword; `store.db.insert_row`, which T49b's writer uses,
    quotes column names so the spec's column name works as it is."""
    insert_row(
        journal,
        "alerts",
        {
            "alert_id": 1,
            "session": _NOW.date(),
            "kind": "locked",
            "message": "m",
            "at": _NOW,
            "known_at": _NOW,
            "ingested_at": _NOW,
        },
    )
    assert journal.execute('SELECT "at" FROM alerts').fetchall() == [(_NOW,)]

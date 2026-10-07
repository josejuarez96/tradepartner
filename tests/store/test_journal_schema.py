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

from tradepartner.store import registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import insert_row

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


def test_current_schema_version_is_15() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 15


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


def test_fresh_init_creates_the_journal_at_version_15(journal: duckdb.DuckDBPyConnection) -> None:
    assert set(schema.JOURNAL_TABLE_NAMES) <= _table_names(journal)
    assert _versions(journal) == [15]


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
    # vintage and detail columns (`tests/store/test_schema_period.py`).
    kept_without_versions = tuple(
        name
        for name in kept
        if name not in ("schema_version", "trial_results", "trial_rebalances", "trials")
        and name not in schema.RETRACTABLE_TABLES
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
    assert [row[0] for row in versions] == [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
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
        assert _versions(conn) == [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]


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

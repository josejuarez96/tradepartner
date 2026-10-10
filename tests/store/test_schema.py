"""Tests for tradepartner.store.schema and .db (T4).

Covers the "Timing and store" acceptance criteria in
docs/specs/data-foundation.md: common fact-table columns, tz-aware
`known_at` round-trip, per-column-type validation, the `known_at <=
ingested_at` and per-table provenance `CHECK` constraints, uniqueness,
`init_schema` idempotency and version checking, the read-only/write
connection helpers (including same-process and cross-process locking),
and the connection's extension auto-install/auto-load settings.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import (
    StoreLockedError,
    _is_lock_error,
    configure_connection,
    ensure_tz_aware,
    insert_row,
    open_for_write,
    open_read_only,
    utc_now,
)

# Every fact table (spec "Data / interfaces" > Tables), i.e. every table
# except ingestion_runs and schema_version, which are not fact tables.
FACT_TABLES: tuple[str, ...] = (
    "securities",
    "listings",
    "classifications",
    "delistings",
    "prices_daily",
    "corporate_actions",
    "facts",
    "statement_facts",
    "filing_events",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _minimal_row(table: str, *, known_at: datetime, ingested_at: datetime) -> dict[str, object]:
    """The smallest valid row for `table`, for constraint-focused tests
    that don't care about the table's own business columns.

    `provenance` defaults to the first value `table` allows (spec "Data /
    interfaces" > master column sources; `schema.TABLE_PROVENANCE_VALUES`)
    rather than a single hardcoded value, since the allowed set differs
    per table.
    """
    common = {
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "test",
        "provenance": schema.TABLE_PROVENANCE_VALUES[table][0],
    }
    business: dict[str, object]
    if table == "securities":
        business = {"security_id": "S1", "cik": "0000000001", "name": "Test Co", "benchmark": False}
    elif table == "listings":
        business = {
            "security_id": "S1",
            "ticker": "TST",
            "exchange": "NASDAQ",
            "class_title": None,
            "valid_from": date(2020, 1, 2),
        }
    elif table == "classifications":
        business = {
            "security_id": "S1",
            "sic": 7372,
            "security_type": "common",
            "rule": "test-rule",
        }
    elif table == "delistings":
        business = {
            "security_id": "S1",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": known_at,
            "effective_on": date(2020, 1, 12),
        }
    elif table == "prices_daily":
        business = {
            "security_id": "S1",
            "session": date(2020, 1, 2),
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        }
    elif table == "corporate_actions":
        business = {
            "security_id": "S1",
            "action_type": "split",
            "ex_date": date(2020, 1, 2),
            "ratio_or_amount": 2.0,
        }
    elif table == "facts":
        business = {
            "security_id": "S1",
            "fact_name": "EntityCommonStockSharesOutstanding",
            "as_of_date": date(2020, 1, 2),
            "class_member": "",  # '' = no class dimension; see schema.py
            "value": 1_000_000.0,
            "filing_accession": "0000000001-20-000001",
        }
    elif table == "statement_facts":
        business = {
            "cik": "0000000001",
            "fact_name": "revenue",
            "xbrl_tag": "us-gaap:Revenues",
            "period_start": date(2019, 1, 1),
            "period_end": date(2019, 12, 31),
            "period_days": 364,
            "value": 1_000_000.0,
            "unit": "USD",
            "form": "10-K",
            "filing_accession": "0000000001-20-000001",
            "basis": "reported",
            "comparative": False,
        }
    elif table == "filing_events":
        business = {
            "cik": "0000000001",
            "accession": "0000000001-20-000001",
            "form": "8-K",
            "items": "2.02,9.01",
            "accepted_at": known_at,
        }
    else:
        raise ValueError(f"no minimal row defined for {table!r}")
    return {**business, **common}


# --- schema shape -----------------------------------------------------


@pytest.mark.parametrize("table", FACT_TABLES)
def test_fact_table_has_common_columns(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    info = fixture_store.execute(f"PRAGMA table_info('{table}')").fetchall()
    columns = {row[1]: row for row in info}
    for col in ("known_at", "ingested_at", "source", "provenance"):
        assert col in columns, f"{table} is missing common column {col!r}"
        _, _, _col_type, not_null, _, _ = columns[col]
        assert not_null, f"{table}.{col} must be NOT NULL"
    assert columns["known_at"][2].upper().startswith("TIMESTAMP")
    assert columns["ingested_at"][2].upper().startswith("TIMESTAMP")


def test_corporate_actions_has_a_nullable_announced_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Issue #83: the source's announcement time is stored next to
    `known_at`, so a first-seen stamp earlier than the proxy is checkable."""
    info = fixture_store.execute("PRAGMA table_info('corporate_actions')").fetchall()
    columns = {row[1]: row for row in info}
    assert "announced_at" in columns
    _, _, col_type, not_null, _, _ = columns["announced_at"]
    assert col_type.upper().startswith("TIMESTAMP")
    assert not not_null


def test_announced_at_arrived_at_schema_version_2() -> None:
    """Version 2 adds `corporate_actions.announced_at` (issue #83); the
    registry (version 3, #117) migrates only from it."""
    assert schema._PRE_REGISTRY_VERSION == 2


def test_ingestion_runs_is_not_a_fact_table(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """`ingestion_runs` has the spec's exact columns and none of the four
    common fact-table columns (it is not a fact table)."""
    info = fixture_store.execute("PRAGMA table_info('ingestion_runs')").fetchall()
    columns = {row[1] for row in info}
    assert columns == {
        "run_id",
        "started_at",
        "finished_at",
        "status",
        "source",
        "mode",
        "rows_added",
        "chunk_cursor",
        "message",
    }
    assert "known_at" not in columns
    assert "ingested_at" not in columns
    assert "provenance" not in columns


def test_all_table_names_present(fixture_store: duckdb.DuckDBPyConnection) -> None:
    tables = {
        row[0]
        for row in fixture_store.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert set(schema.TABLE_NAMES) <= tables


def test_init_schema_twice_is_a_no_op(fixture_store: duckdb.DuckDBPyConnection) -> None:
    schema.init_schema(fixture_store)
    schema.init_schema(fixture_store)
    (count,) = fixture_store.execute("SELECT COUNT(*) FROM schema_version").fetchone()  # type: ignore[misc]
    assert count == 1


def test_init_schema_raises_on_schema_version_mismatch(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute(
        "UPDATE schema_version SET version = ?", [schema.CURRENT_SCHEMA_VERSION + 1]
    )
    with pytest.raises(schema.SchemaVersionError, match=str(schema.CURRENT_SCHEMA_VERSION)):
        schema.init_schema(fixture_store)


def test_configure_connection_disables_extension_autoinstall_and_autoload(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """DuckDB's own HTTP client (extension auto-install/auto-load) does
    not go through Python's `socket` module, so the no-network test
    fixture cannot block it directly; these settings do instead."""
    (autoinstall,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT current_setting('autoinstall_known_extensions')"
    ).fetchone()
    (autoload,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT current_setting('autoload_known_extensions')"
    ).fetchone()
    assert autoinstall is False
    assert autoload is False


# --- tz-aware known_at round trip and per-column-type validation -------


def test_known_at_round_trips_as_utc(fixture_store: duckdb.DuckDBPyConnection) -> None:
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    # `fixture_store` may already carry other securities' bars (T5's fixture
    # universe), so select this row by its own key rather than the whole
    # table.
    (known_at,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT known_at FROM prices_daily WHERE security_id = ? AND session = ? AND known_at = ?",
        [row["security_id"], row["session"], now],
    ).fetchone()
    assert known_at.tzinfo is not None
    assert known_at.utcoffset() == timedelta(0)
    assert known_at == now


def test_ensure_tz_aware_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        ensure_tz_aware(datetime(2020, 1, 1), field="known_at")  # noqa: DTZ001


def test_ensure_tz_aware_accepts_aware_datetime() -> None:
    now = _now()
    assert ensure_tz_aware(now, field="known_at") == now


def test_ensure_tz_aware_normalizes_non_utc_aware_datetime_to_utc() -> None:
    non_utc = datetime(2020, 1, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    result = ensure_tz_aware(non_utc, field="known_at")
    assert result == non_utc
    assert result.tzinfo is UTC


def test_insert_row_rejects_naive_known_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())
    with pytest.raises(ValueError, match="known_at"):
        insert_row(fixture_store, "prices_daily", row)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before


def test_insert_row_stores_none_as_null_in_a_nullable_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`announced_at` is nullable (#83): `None` binds as NULL."""
    row = {
        **_minimal_row("corporate_actions", known_at=_now(), ingested_at=_now()),
        "security_id": "S_NULL",
        "announced_at": None,
    }
    insert_row(fixture_store, "corporate_actions", row)
    (announced,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT announced_at FROM corporate_actions WHERE security_id = 'S_NULL'"
    ).fetchone()
    assert announced is None


def test_insert_row_none_in_a_not_null_timestamptz_column_is_refused(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    row = _minimal_row("prices_daily", known_at=None, ingested_at=_now())  # type: ignore[arg-type]
    with pytest.raises(duckdb.ConstraintException, match="known_at"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_naive_ingested_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=naive)
    with pytest.raises(ValueError, match="ingested_at"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_bare_date_for_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["known_at"] = date(2020, 1, 2)  # a session date, not an instant
    with pytest.raises(TypeError, match="TIMESTAMPTZ"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_naive_string_for_timestamptz_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A `str` is never accepted for a `TIMESTAMPTZ` column, whatever it
    contains — here, a naive-looking ISO string with no UTC offset, the
    kind DuckDB itself would silently interpret in the session `TimeZone`
    rather than reject (see the module docstring's caveat)."""
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["known_at"] = "2020-01-02T21:00:00"
    with pytest.raises(TypeError, match="TIMESTAMPTZ"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_rejects_datetime_for_date_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`datetime` is a `date` subclass; a DATE column must still reject
    it — a session is a calendar day, not an instant in some timezone."""
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    row["session"] = datetime(2020, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    with pytest.raises(TypeError, match="DATE"):
        insert_row(fixture_store, "prices_daily", row)


def test_insert_row_accepts_valid_date_for_date_column(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    row = _minimal_row("prices_daily", known_at=_now(), ingested_at=_now())
    insert_row(fixture_store, "prices_daily", row)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before + 1


# --- CHECK / UNIQUE constraints ----------------------------------------


@pytest.mark.parametrize("table", FACT_TABLES)
def test_known_at_after_ingested_at_rejected(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now - timedelta(seconds=1))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_bad_provenance_rejected(fixture_store: duckdb.DuckDBPyConnection, table: str) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    row["provenance"] = "not-a-real-provenance"
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_provenance_restricted_to_table_specific_set(
    fixture_store: duckdb.DuckDBPyConnection, table: str
) -> None:
    """A provenance value that's valid *somewhere* in the store but not
    for this particular table is still rejected (spec "Data /
    interfaces": each table's allowed provenance set is a strict subset
    of the five overall values)."""
    allowed = set(schema.TABLE_PROVENANCE_VALUES[table])
    disallowed = next(p for p in schema.PROVENANCE_VALUES if p not in allowed)
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    row["provenance"] = disallowed
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, row)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_duplicate_row_rejected(fixture_store: duckdb.DuckDBPyConnection, table: str) -> None:
    now = _now()
    row = _minimal_row(table, known_at=now, ingested_at=now)
    insert_row(fixture_store, table, row)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, table, dict(row))


def test_duplicate_undimensioned_fact_rejected(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """`facts.class_member` defaults to `''` (not NULL) specifically so
    that two undimensioned facts for the same key collide on the UNIQUE
    constraint — DuckDB treats NULL as distinct from NULL, which would
    otherwise let the same undimensioned fact be inserted twice."""
    now = _now()
    row = _minimal_row("facts", known_at=now, ingested_at=now)
    assert row["class_member"] == ""
    insert_row(fixture_store, "facts", row)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "facts", dict(row))


def test_bar_with_later_known_at_is_a_new_row_not_rejected(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A revision (later `known_at`, same session) is a *new* row, per spec
    "Definitions" > Revision — the unique key includes `known_at`."""
    (before,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    now = _now()
    row = _minimal_row("prices_daily", known_at=now, ingested_at=now)
    insert_row(fixture_store, "prices_daily", row)
    revised = dict(row)
    revised["known_at"] = now + timedelta(days=1)
    revised["ingested_at"] = now + timedelta(days=1)
    revised["close"] = 99.0
    insert_row(fixture_store, "prices_daily", revised)
    (after,) = fixture_store.execute("SELECT COUNT(*) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert after == before + 2


# --- statement_facts (#660, T76) ---------------------------------------


def test_statement_fact_with_later_known_at_still_raises(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Unlike `prices_daily` above, `statement_facts`' UNIQUE key excludes
    `known_at`: a second row for an already-stored key is rejected even
    with a later `known_at` — one vintage per key for ever (spec
    "First vintage"), the deliberate exception to the "Definitions"
    revision rule."""
    now = _now()
    row = _minimal_row("statement_facts", known_at=now, ingested_at=now)
    insert_row(fixture_store, "statement_facts", row)
    revised = dict(row)
    revised["known_at"] = now + timedelta(days=1)
    revised["ingested_at"] = now + timedelta(days=1)
    revised["value"] = 999.0
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "statement_facts", revised)


@pytest.mark.parametrize(
    ("period_start", "period_days"),
    [
        (date(2019, 1, 2), 0),  # non-NULL period_start but period_days = 0
        (None, 364),  # NULL period_start but period_days != 0
    ],
)
def test_period_start_null_iff_period_days_is_zero(
    fixture_store: duckdb.DuckDBPyConnection, period_start: date | None, period_days: int
) -> None:
    """Spec "Statement facts" > Schema: `period_start` is NULL exactly
    when `period_days = 0` (an instant fact); the mismatched combinations
    above are refused by the table's own `CHECK`."""
    now = _now()
    row = _minimal_row("statement_facts", known_at=now, ingested_at=now)
    row["period_start"] = period_start
    row["period_days"] = period_days
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "statement_facts", row)


def test_statement_fact_basis_restricted_to_reported_or_derived(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    now = _now()
    row = _minimal_row("statement_facts", known_at=now, ingested_at=now)
    row["basis"] = "not-a-real-basis"
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "statement_facts", row)


def test_statement_fact_basis_values_are_reported_and_derived() -> None:
    assert schema.STATEMENT_FACT_BASIS_VALUES == ("reported", "derived")


def test_statement_facts_keyed_by_cik_not_security_id(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`statement_facts` has no `security_id` column at all (spec decision
    (c): a statement is the issuer's, not a share class's)."""
    info = fixture_store.execute("PRAGMA table_info('statement_facts')").fetchall()
    columns = {row[1] for row in info}
    assert "security_id" not in columns
    assert "cik" in columns


#: SHA-256 of `"".join(schema._STATEMENT_FACTS_TABLE_DDL)` at version 10
#: (#660, T76; renumbered from 9 at ready time, T84/#714 landed version 9
#: first). Deliberately a tuple of its own, never folded into
#: `_TABLE_DDL` (whose version-4 pin in `test_journal_schema.py` and
#: `test_registry_schema.py` must never move again, quant-auditor review
#: of PR #729): a later edit of this table's DDL goes to the next schema
#: version with its own migration, never a silent change here.
_V10_STATEMENT_FACTS_DDL_SHA256 = "7b68b590db31163266c3ec13d227be93381ff113e30c57cc094a76c5f0342416"


def test_statement_facts_ddl_is_pinned_at_version_10() -> None:
    digest = hashlib.sha256("".join(schema._STATEMENT_FACTS_TABLE_DDL).encode()).hexdigest()
    assert digest == _V10_STATEMENT_FACTS_DDL_SHA256, (
        "statement_facts DDL changed: bump the schema version and add a "
        "migration instead of editing the table in place"
    )


def test_migrating_a_genuine_pre_version_10_store_creates_statement_facts() -> None:
    """Unlike `conftest.version_4_store` and
    `test_registry_schema._make_old_store` (which both pre-create
    `statement_facts` so `load_universe_fixtures` can load
    `statement_facts.csv` into them -- code-review of PR #729: that makes
    their own before/after snapshots of this table vacuously equal), this
    builds a store with none of version 10's DDL at all, so migrating it
    (through T84/#714's version-9 settle-order step on the way) is the
    only way the table can appear."""
    conn = duckdb.connect(":memory:")
    try:
        configure_connection(conn)
        for ddl in schema._TABLE_DDL + schema._REGISTRY_TABLE_DDL + schema._JOURNAL_TABLE_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (8, TIMESTAMPTZ "
            "'2026-09-26 12:00:00+00')"
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = 'statement_facts'"
            ).fetchone()[0]  # type: ignore[index]
            == 0
        )
        schema.init_schema(conn)
        info = conn.execute("PRAGMA table_info('statement_facts')").fetchall()
        columns = {row[1] for row in info}
        assert columns == {
            "cik",
            "fact_name",
            "xbrl_tag",
            "period_start",
            "period_end",
            "period_days",
            "value",
            "unit",
            "form",
            "filing_accession",
            "basis",
            "comparative",
            "known_at",
            "ingested_at",
            "source",
            "provenance",
        }
        constraints = conn.execute(
            "SELECT constraint_type, constraint_column_names FROM duckdb_constraints() "
            "WHERE table_name = 'statement_facts' AND constraint_type = 'UNIQUE'"
        ).fetchall()
        assert len(constraints) == 1
        assert set(constraints[0][1]) == {"cik", "fact_name", "period_end", "period_days"}
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone() == (22,)
    finally:
        conn.close()


# --- filing_events (#1358, T164b; schema version 21) -------------------

_V21_FILING_EVENTS_DDL_SHA256 = "aff98d8eecb42c4ffb6cf47d75d6e1976ff8d0ceb01f26becb1dee0de3f8730d"


def test_filing_events_ddl_is_pinned_at_version_22() -> None:
    digest = hashlib.sha256("".join(schema._FILING_EVENTS_TABLE_DDL).encode()).hexdigest()
    assert digest == _V21_FILING_EVENTS_DDL_SHA256, (
        "filing_events DDL changed: bump the schema version and add a "
        "migration instead of editing the table in place"
    )


def test_filing_events_is_a_filing_fact_table_keyed_by_cik_and_accession(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The spec's columns, `provenance = filing` only, UNIQUE `(cik,
    accession)` without `known_at` (no revisions by construction), and in
    `TABLE_NAMES` so the look-ahead harness truncates it."""
    assert "filing_events" in schema.TABLE_NAMES
    assert schema.TABLE_PROVENANCE_VALUES["filing_events"] == ("filing",)
    info = fixture_store.execute("PRAGMA table_info('filing_events')").fetchall()
    assert [(row[1], row[2], bool(row[3])) for row in info] == [
        ("cik", "VARCHAR", True),
        ("accession", "VARCHAR", True),
        ("form", "VARCHAR", True),
        ("items", "VARCHAR", True),
        ("accepted_at", "TIMESTAMP WITH TIME ZONE", True),
        ("known_at", "TIMESTAMP WITH TIME ZONE", True),
        ("ingested_at", "TIMESTAMP WITH TIME ZONE", True),
        ("source", "VARCHAR", True),
        ("provenance", "VARCHAR", True),
    ]
    constraints = fixture_store.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = 'filing_events' AND constraint_type = 'UNIQUE'"
    ).fetchall()
    assert [set(c[0]) for c in constraints] == [{"cik", "accession"}]


def test_a_second_row_for_an_accession_raises_even_with_a_later_known_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    now = _now()
    insert_row(
        fixture_store, "filing_events", _minimal_row("filing_events", known_at=now, ingested_at=now)
    )
    later = now + timedelta(days=1)
    again = _minimal_row("filing_events", known_at=later, ingested_at=later)
    again["items"] = "5.02"
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "filing_events", again)


def test_filing_events_known_at_must_equal_accepted_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`known_at = accepted_at`, never the filing date: a row stamped at any
    other instant is refused by the table itself."""
    now = _now()
    row = _minimal_row("filing_events", known_at=now, ingested_at=now)
    row["accepted_at"] = now + timedelta(hours=1)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "filing_events", row)
    row["accepted_at"] = now - timedelta(hours=1)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "filing_events", row)


def test_filing_events_items_is_never_null_but_may_be_empty(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    now = _now()
    row = _minimal_row("filing_events", known_at=now, ingested_at=now)
    row["items"] = None
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "filing_events", row)
    row["items"] = ""
    insert_row(fixture_store, "filing_events", row)


def test_the_fixture_store_holds_the_three_filing_events(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """One `SEC_SPLIT_PLAIN` issuer: the 16:05 New York `2.02,9.01` (filed on
    its own session), the 20:30 one (filed the next day) and a `5.02`."""
    rows = fixture_store.execute(
        "SELECT f.cik, form, items, timezone('America/New_York', accepted_at), "
        "f.known_at = accepted_at, s.security_id FROM filing_events f "
        "JOIN securities s USING (cik) ORDER BY accepted_at"
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3].strftime("%H:%M"), r[4], r[5]) for r in rows] == [
        ("CIK0001000011", "8-K", "2.02,9.01", "16:05", True, "SEC_SPLIT_PLAIN"),
        ("CIK0001000011", "8-K", "2.02,9.01", "20:30", True, "SEC_SPLIT_PLAIN"),
        ("CIK0001000011", "8-K", "5.02", "09:00", True, "SEC_SPLIT_PLAIN"),
    ]


def _version_20_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store as version 20 left it: today's schema without `filing_events`
    and a single version-20 row, holding a `prices_daily` and a
    `statement_facts` row."""
    configure_connection(conn)
    schema.init_schema(conn)
    conn.execute(
        "INSERT INTO prices_daily VALUES ('S1', DATE '2020-01-02', 1, 1, 1, 1, 1, "
        "TIMESTAMPTZ '2020-01-02 21:00:00+00', TIMESTAMPTZ '2020-01-02 21:10:00+00', "
        "'test', 'bar')"
    )
    conn.execute(
        "INSERT INTO statement_facts VALUES ('0000000001', 'revenue', 'us-gaap:Revenues', "
        "DATE '2019-01-01', DATE '2019-12-31', 364, 1.0, 'USD', '10-K', 'a1', 'reported', "
        "FALSE, TIMESTAMPTZ '2020-02-03 21:00:00+00', TIMESTAMPTZ '2020-02-03 21:10:00+00', "
        "'test', 'filing')"
    )
    conn.execute("DROP TABLE filing_events")
    conn.execute("DELETE FROM schema_version")
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (20, TIMESTAMPTZ "
        "'2026-10-09 12:00:00+00')"
    )


def _shape(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(conn.execute("SELECT table_name, sql FROM duckdb_tables() ORDER BY 1").fetchall())


def _all_rows(conn: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, list[object]]:
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables}


def test_the_version_21_migration_adds_filing_events_and_nothing_else() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _version_20_store(conn)
        shape_before = _shape(conn)
        rows_before = _all_rows(conn, sorted(set(shape_before) - {"schema_version"}))
        assert "filing_events" not in shape_before
        schema.init_schema(conn)
        shape_after = _shape(conn)
        assert set(shape_after) - set(shape_before) == {"filing_events"}
        assert {t: shape_after[t] for t in shape_before} == shape_before
        assert _all_rows(conn, sorted(set(shape_before) - {"schema_version"})) == rows_before
        assert conn.execute("SELECT count(*) FROM filing_events").fetchone() == (0,)
        assert _versions(conn) == [20, 21, 22]
        schema.init_schema(conn)
        assert _versions(conn) == [20, 21, 22]
        # The migrated table is the fresh store's table.
        fresh = duckdb.connect(":memory:")
        schema.init_schema(fresh)
        assert shape_after["filing_events"] == _shape(fresh)["filing_events"]
        fresh.close()
    finally:
        conn.close()


def test_read_only_open_of_a_version_20_store_passes_and_a_filing_events_read_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v20.duckdb"
    conn = duckdb.connect(str(path))
    _version_20_store(conn)
    conn.close()
    with duckdb.connect(str(path), read_only=True) as ro:
        schema.init_schema(ro)
        assert _versions(ro) == [20]
        assert ro.execute("SELECT count(*) FROM prices_daily").fetchone() == (1,)
        with pytest.raises(duckdb.CatalogException):
            ro.execute("SELECT * FROM filing_events")


#: Every package a trial or a paper run reads data through: the engine and its
#: signals (`backtest/`, `signals.py` and `signals_combined.py` included), the
#: research lab and the paper path.
_TRIAL_PATH_PACKAGES = ("backtest", "research", "execution")


def test_no_trial_path_reads_filing_events() -> None:
    """Until #1382 (the stamps clock) is closed, no trial reads the table
    (spec, Amendment 2026-10-09, "The clock"; open question 11): nothing
    under `backtest/` (the signals included), `research/` or `execution/`
    names it."""
    src = Path(schema.__file__).resolve().parents[1]
    readers = [
        str(path.relative_to(src))
        for package in _TRIAL_PATH_PACKAGES
        for path in sorted((src / package).rglob("*.py"))
        if "filing_events" in path.read_text()
    ]
    assert all((src / package).is_dir() for package in _TRIAL_PATH_PACKAGES)
    assert (src / "backtest" / "signals.py").is_file()
    assert readers == []


# --- version 13 (#720, #1033, T85d): profitability rebalance columns ----


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, tuple[str, bool]]:
    """`{column: (dtype, notnull)}`, as `tests/store/test_research_schema.py`'s
    helper of the same name does."""
    return {
        name: (dtype, notnull)
        for _, name, dtype, notnull, _, _ in conn.execute(
            f"PRAGMA table_info('{table}')"
        ).fetchall()
    }


def _version_12_store(conn: duckdb.DuckDBPyConnection, *, with_rebalance_row: bool = False) -> None:
    """Build a store on `conn` shaped exactly as version 12 left it: every
    DDL version 13 (`_migrate_profitability_rebalance_counts`) itself does
    not touch, and a version-12 `schema_version` row. With
    `with_rebalance_row`, one `momentum`-shaped `trial_rebalances` row
    (every column version 13 adds is absent, so there is nothing to fill
    for it)."""
    configure_connection(conn)
    for ddl in (
        schema._TABLE_DDL
        + schema._REGISTRY_TABLE_DDL
        + schema._JOURNAL_TABLE_DDL
        + schema._STATEMENT_FACTS_TABLE_DDL
        + schema._MASTER_UNDERIVED_TABLE_DDL
        + schema._RESEARCH_TABLE_DDL
    ):
        conn.execute(ddl)
    if with_rebalance_row:
        conn.execute(
            "INSERT INTO trial_rebalances (trial_id, cost_per_side_bps, session, "
            "fill_session, n_universe, n_static_listings, n_targets, turnover, "
            "cost_paid, gap_count_share, gap_size_share, n_missing_fill, "
            "n_delisting_exits, n_stale_exits, n_excluded_no_history, "
            "n_dropped_dividends, n_late_dividends) VALUES "
            "(1, 15.0, '2018-01-31', '2018-02-01', 10, 1, 2, 0.5, 0.001, 0.01, "
            "0.001, 0, 0, 0, 1, 0, 0)"
        )
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (12, TIMESTAMPTZ "
        "'2026-10-05 12:00:00+00')"
    )


def test_profitability_rebalance_columns_ddl_is_pinned() -> None:
    """`PROFITABILITY_REBALANCE_COLUMNS` arrive by `ALTER TABLE`
    (`_migrate_profitability_rebalance_counts`), never by editing
    `_CREATE_TRIAL_REBALANCES`/`_REGISTRY_TABLE_DDL` in place, so a later
    registry DDL change still goes to the next schema version."""
    assert "n_ranked" not in schema._CREATE_TRIAL_REBALANCES
    assert schema.PROFITABILITY_REBALANCE_COLUMNS == (
        "n_ranked",
        "n_excluded_no_facts",
        "n_excluded_stale_facts",
        "n_excluded_sector",
        "n_excluded_malformed",
        "n_derived",
    )


def test_migrating_a_genuine_version_12_store_adds_the_six_columns_and_keeps_every_row() -> None:
    """A store shaped exactly as version 12 left it (no
    `PROFITABILITY_REBALANCE_COLUMNS`, a `momentum`-shaped `trial_rebalances`
    row already in it) gains the six nullable columns, NULL on that
    existing row, and a version-13 row, with nothing else about the row
    changed."""
    conn = duckdb.connect(":memory:")
    try:
        _version_12_store(conn, with_rebalance_row=True)
        assert set(_columns(conn, "trial_rebalances")).isdisjoint(
            schema.PROFITABILITY_REBALANCE_COLUMNS
        )
        schema.init_schema(conn)
        assert set(schema.PROFITABILITY_REBALANCE_COLUMNS) <= set(
            _columns(conn, "trial_rebalances")
        )
        rows = conn.execute(
            "SELECT trial_id, cost_per_side_bps, n_universe, n_ranked, "
            "n_excluded_no_facts, n_excluded_stale_facts, n_excluded_sector, "
            "n_excluded_malformed, n_derived FROM trial_rebalances"
        ).fetchall()
        assert rows == [(1, 15.0, 10, None, None, None, None, None, None)]
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone() == (22,)
    finally:
        conn.close()


def test_a_fresh_store_has_the_six_columns_too() -> None:
    """`_migrate_profitability_rebalance_counts` runs unconditionally after
    the DDL pass, so a fresh store gets the columns the same way a
    migrated one does, exactly as `_migrate_n_research` does for
    `trial_results`."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    columns = _columns(conn, "trial_rebalances")
    assert set(schema.PROFITABILITY_REBALANCE_COLUMNS) <= set(columns)
    for column in schema.PROFITABILITY_REBALANCE_COLUMNS:
        dtype, notnull = columns[column]
        assert dtype == "INTEGER"
        assert not notnull


def test_read_only_open_of_a_genuine_version_12_store_passes(tmp_path: Path) -> None:
    """A read-only connection never migrates (quant-auditor finding on PR
    #1076): a store still shaped exactly as version 12 left it — no
    `PROFITABILITY_REBALANCE_COLUMNS` on `trial_rebalances` — is accepted,
    `schema_version` stays at `[12]`, and every other read keeps working."""
    path = tmp_path / "v12.duckdb"
    conn = duckdb.connect(str(path))
    try:
        _version_12_store(conn)
    finally:
        conn.close()
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert conn.execute("SELECT version FROM schema_version ORDER BY 1").fetchall() == [(12,)]
        assert conn.execute("SELECT COUNT(*) FROM trial_rebalances").fetchone() == (0,)
        assert set(_columns(conn, "trial_rebalances")).isdisjoint(
            schema.PROFITABILITY_REBALANCE_COLUMNS
        )


# --- version 14 (#1153, T127): generic rebalance counts, signals prefix CHECK ----

#: `signals.reason`'s closed `CHECK` before version 14.
_V13_SIGNALS_CHECK = "CHECK (reason IN ('selected', 'below_cut', 'excluded_no_history'))"

#: `trial_rebalances` columns before `PROFITABILITY_REBALANCE_COLUMNS`, in order.
_V12_REBALANCE_COLUMNS = (
    "trial_id, cost_per_side_bps, session, fill_session, n_universe, n_static_listings, "
    "n_targets, turnover, cost_paid, gap_count_share, gap_size_share, n_missing_fill, "
    "n_delisting_exits, n_stale_exits, n_excluded_no_history, n_dropped_dividends, "
    "n_late_dividends"
)

#: (trial, cost, session, n_excluded_no_history, the six profitability counts): a
#: `momentum` trial 1 at two levels, and a `profitability` trial 3 at three levels
#: (written with `n_excluded_no_history = 0`, as the engine did before version
#: 14), whose 2018-02-28 `n_derived` is NULL.
_V13_REBALANCES: tuple[tuple[object, ...], ...] = (
    (1, 0.0, "2018-01-31", 2, None, None, None, None, None, None),
    (1, 15.0, "2018-01-31", 2, None, None, None, None, None, None),
    (3, 0.0, "2018-01-31", 0, 58, 12, 3, 2, 1, 4),
    (3, 15.0, "2018-01-31", 0, 58, 12, 3, 2, 1, 4),
    (3, 30.0, "2018-01-31", 0, 58, 12, 3, 2, 1, 4),
    (3, 0.0, "2018-02-28", 0, 60, 10, 0, 2, 0, None),
    (3, 15.0, "2018-02-28", 0, 60, 10, 0, 2, 0, None),
)

_V13_SIGNALS = (
    (1, "SEC_A", 1.5, 1, "selected"),
    (1, "SEC_B", 0.5, 2, "below_cut"),
    (1, "SEC_C", None, None, "excluded_no_history"),
)


def _version_13_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store shaped as version 13 left it: no `trial_rebalance_counts`, a NOT
    NULL `n_excluded_no_history`, `signals.reason`'s closed `CHECK`, the
    `_V13_REBALANCES` rows and three `signals` rows, and a version-13 row."""
    _version_12_store(conn)
    conn.execute("DROP TABLE signals")
    conn.execute(schema._CREATE_SIGNALS.replace(schema._SIGNALS_REASON_CHECK, _V13_SIGNALS_CHECK))
    schema._migrate_profitability_rebalance_counts(conn)
    six = ", ".join(schema.PROFITABILITY_REBALANCE_COLUMNS)
    for trial, cost, session, no_history, *counts in _V13_REBALANCES:
        conn.execute(
            f"INSERT INTO trial_rebalances ({_V12_REBALANCE_COLUMNS}, {six}) VALUES "
            "(?, ?, ?, '2018-02-01', 10, 1, 2, 0.5, 0.001, 0.01, 0.001, 0, 0, 0, ?, 0, 0, "
            "?, ?, ?, ?, ?, ?)",
            [trial, cost, session, no_history, *counts],
        )
    for run_id, security_id, score, rank, reason in _V13_SIGNALS:
        conn.execute(
            "INSERT INTO signals (run_id, rebalance_session, security_id, score, rank, reason, "
            "known_at, ingested_at) VALUES (?, '2018-01-31', ?, ?, ?, ?, ?, ?)",
            [run_id, security_id, score, rank, reason, _now(), _now()],
        )
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (13, TIMESTAMPTZ "
        "'2026-10-06 12:00:00+00')"
    )


def _signals(conn: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT run_id, security_id, score, rank, reason FROM signals ORDER BY rowid"
    ).fetchall()


def _versions(conn: duckdb.DuckDBPyConnection) -> list[int]:
    return [v for (v,) in conn.execute("SELECT version FROM schema_version ORDER BY 1").fetchall()]


def test_migrating_a_version_13_store_copies_the_counts_once_per_rebalance() -> None:
    """The counts table holds, once per `(trial, session)`, every existing
    row's `n_excluded_no_history` and each non-NULL profitability count (the
    owner's B3 trial included); a NULL gives no row; `trial_rebalances` keeps
    every row and value, its `n_excluded_no_history` now nullable."""
    conn = duckdb.connect(":memory:")
    try:
        _version_13_store(conn)
        before = conn.execute("SELECT * FROM trial_rebalances ORDER BY ALL").fetchall()
        schema.init_schema(conn)
        assert _versions(conn) == [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
        assert conn.execute("SELECT * FROM trial_rebalances ORDER BY ALL").fetchall() == before
        assert not _columns(conn, "trial_rebalances")["n_excluded_no_history"][1]
        rows = conn.execute(
            "SELECT trial_id, session, name, value FROM trial_rebalance_counts ORDER BY ALL"
        ).fetchall()
        jan, feb = date(2018, 1, 31), date(2018, 2, 28)
        jan_six = dict(
            zip(schema.PROFITABILITY_REBALANCE_COLUMNS, (58, 12, 3, 2, 1, 4), strict=True)
        )
        feb_five = dict(
            zip(schema.PROFITABILITY_REBALANCE_COLUMNS[:5], (60, 10, 0, 2, 0), strict=True)
        )
        assert rows == sorted(
            [
                (1, jan, "n_excluded_no_history", 2),
                (3, jan, "n_excluded_no_history", 0),
                *((3, jan, name, value) for name, value in jan_six.items()),
                (3, feb, "n_excluded_no_history", 0),
                *((3, feb, name, value) for name, value in feb_five.items()),
            ]
        )
    finally:
        conn.close()


def test_migrating_a_version_13_store_keeps_every_signal_and_widens_the_check() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _version_13_store(conn)
        with pytest.raises(duckdb.ConstraintException):  # the version-13 closed set
            conn.execute(
                "INSERT INTO signals SELECT * REPLACE ('excluded_sector' AS reason) "
                "FROM signals LIMIT 1"
            )
        schema.init_schema(conn)
        assert _signals(conn) == list(_V13_SIGNALS)
        conn.execute(
            "INSERT INTO signals SELECT * REPLACE ('excluded_sector' AS reason) "
            "FROM signals LIMIT 1"
        )
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                "INSERT INTO signals SELECT * REPLACE ('foo' AS reason) FROM signals LIMIT 1"
            )
    finally:
        conn.close()


def test_a_failed_signals_rebuild_leaves_the_store_at_version_13(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rebuild runs in `init_schema`'s one transaction: made to fail (its
    staging table's name is taken), nothing of version 14 is left behind."""
    conn = duckdb.connect(":memory:")
    try:
        _version_13_store(conn)
        before = conn.execute("SELECT * FROM trial_rebalances ORDER BY ALL").fetchall()
        monkeypatch.setattr(schema, "_SIGNALS_STAGING_TABLE", "decisions")
        with pytest.raises(duckdb.CatalogException):
            schema.init_schema(conn)
        assert _versions(conn) == [12, 13]
        assert _signals(conn) == list(_V13_SIGNALS)
        assert conn.execute("SELECT * FROM trial_rebalances ORDER BY ALL").fetchall() == before
        assert _columns(conn, "trial_rebalances")["n_excluded_no_history"][1]
        tables = {t for (t,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        assert schema.REBALANCE_COUNTS_TABLE_NAME not in tables
    finally:
        conn.close()


def test_read_only_open_of_a_version_13_store_passes(tmp_path: Path) -> None:
    path = tmp_path / "v13.duckdb"
    conn = duckdb.connect(str(path))
    try:
        _version_13_store(conn)
    finally:
        conn.close()
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [12, 13]
        assert _signals(conn) == list(_V13_SIGNALS)


def test_a_fresh_store_has_the_counts_table_and_a_nullable_no_history_count() -> None:
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        assert _columns(conn, schema.REBALANCE_COUNTS_TABLE_NAME) == {
            "trial_id": ("BIGINT", True),
            "session": ("DATE", True),
            "name": ("VARCHAR", True),
            "value": ("INTEGER", True),
        }
        assert not _columns(conn, "trial_rebalances")["n_excluded_no_history"][1]
        want = ("n_excluded_no_history", *schema.PROFITABILITY_REBALANCE_COLUMNS)
        assert want == schema.REBALANCE_COUNT_COLUMNS
    finally:
        conn.close()


# --- lock-error detection -----------------------------------------------


def test_is_lock_error_detects_cross_process_lock_messages() -> None:
    assert _is_lock_error(duckdb.IOException('IO Error: Could not set lock on file "x"')) is True
    assert _is_lock_error(duckdb.IOException("IO Error: Conflicting lock is held in ...")) is True


def test_is_lock_error_rejects_unrelated_io_errors() -> None:
    assert (
        _is_lock_error(
            duckdb.IOException('IO Error: Cannot open file "x": No such file or directory')
        )
        is False
    )


# --- read-only / write connections --------------------------------------


def test_open_read_only_cannot_write(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    with open_read_only(settings) as reader, pytest.raises(duckdb.Error):
        reader.execute("CREATE TABLE should_not_exist (a INTEGER)")


def test_open_for_write_commits_on_normal_exit(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "r1",
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "status": "ok",
                "source": "test",
                "mode": "backfill",
            },
        )

    with open_for_write(settings) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
    assert count == 1


def test_open_for_write_rolls_back_on_exception(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), open_for_write(settings) as conn:
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "r1",
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "status": "ok",
                "source": "test",
                "mode": "backfill",
            },
        )
        raise _Boom("simulated failure mid-chunk")

    with open_for_write(settings) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
    assert count == 0


def test_open_for_write_creates_missing_parent_directory(tmp_path: Path) -> None:
    nested_path = tmp_path / "a" / "b" / "store.duckdb"
    assert not nested_path.parent.exists()
    settings = Settings(_env_file=None, store={"path": str(nested_path)})

    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    assert nested_path.exists()


def test_open_for_write_does_not_retry_non_lock_io_errors(tmp_path: Path) -> None:
    """A real, non-lock `IOException` (here: `store.path` names an
    existing directory, not a file) must propagate immediately, not spin
    out the full `lock_retry_seconds` window before misreporting itself
    as `StoreLockedError` (probed against the pre-fix behavior)."""
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    settings = Settings(_env_file=None, store={"path": str(a_directory), "lock_retry_seconds": 5})

    start = time.monotonic()
    with pytest.raises(duckdb.IOException), open_for_write(settings):
        pass  # pragma: no cover - connect() itself raises
    elapsed = time.monotonic() - start

    assert elapsed < 1.0


def test_open_for_write_raises_store_locked_error_for_same_process_reader(
    settings: Settings,
) -> None:
    """DuckDB shares one database instance per path per process: a
    read-only connection already open in *this* process makes a write
    connection to the same path raise `duckdb.ConnectionException`, not a
    file-lock `IOException` — `open_for_write` converts that to
    `StoreLockedError` immediately rather than retrying (retrying can
    never help; this process itself is the blocker)."""
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    reader = duckdb.connect(database=settings.store.path, read_only=True)
    try:
        start = time.monotonic()
        with pytest.raises(StoreLockedError, match="different mode"), open_for_write(settings):
            pass  # pragma: no cover - connect() itself raises
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
    finally:
        reader.close()


def test_open_read_only_raises_store_locked_error_for_same_process_writer(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    writer = duckdb.connect(database=settings.store.path, read_only=False)
    try:
        with pytest.raises(StoreLockedError, match="different mode"), open_read_only(settings):
            pass  # pragma: no cover - connect() itself raises
    finally:
        writer.close()


def test_open_for_write_retries_then_raises_store_locked_error(tmp_path: Path) -> None:
    db_path = tmp_path / "locked.duckdb"
    duckdb.connect(database=str(db_path)).close()

    holder_code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={str(db_path)!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(5)
        conn.close()
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD"

        settings = Settings(_env_file=None, store={"path": str(db_path), "lock_retry_seconds": 1})
        start = time.monotonic()
        with pytest.raises(StoreLockedError), open_for_write(settings):
            pass  # pragma: no cover - lock acquisition should never succeed
        elapsed = time.monotonic() - start
        assert elapsed >= 1.0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_open_for_write_succeeds_once_lock_is_released(tmp_path: Path) -> None:
    db_path = tmp_path / "released.duckdb"
    duckdb.connect(database=str(db_path)).close()

    holder_code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={str(db_path)!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(0.5)
        conn.close()
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD"

        settings = Settings(_env_file=None, store={"path": str(db_path), "lock_retry_seconds": 5})
        with open_for_write(settings) as conn:
            schema.init_schema(conn)
    finally:
        proc.wait(timeout=10)


# --- config: lock-retry backoff (T4 review fix) --------------------------


def test_store_config_lock_retry_backoff_defaults() -> None:
    """`store.lock_retry_initial_delay_seconds`/`.lock_retry_max_delay_seconds`
    (added to `StoreConfig` for this fix; see config.py's `StoreConfig`
    docstring for why T4 touches a T1 file here) back `open_for_write`'s
    retry backoff so it is config, not a hardcoded constant."""
    s = Settings(_env_file=None)
    assert s.store.lock_retry_initial_delay_seconds == pytest.approx(0.05)
    assert s.store.lock_retry_max_delay_seconds == pytest.approx(1.0)


def test_store_config_rejects_zero_initial_delay() -> None:
    """A zero delay is not a backoff (`Field(gt=0)`, third review pass)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_initial_delay_seconds": 0})


def test_store_config_rejects_zero_max_delay() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_max_delay_seconds": 0})


def test_store_config_rejects_negative_lock_retry_seconds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store={"lock_retry_seconds": -1})


def test_store_config_allows_zero_lock_retry_seconds() -> None:
    """Zero means "fail immediately, no retry" -- a legitimate choice,
    unlike a zero backoff delay."""
    s = Settings(_env_file=None, store={"lock_retry_seconds": 0})
    assert s.store.lock_retry_seconds == 0


def test_store_config_rejects_initial_delay_greater_than_max_delay() -> None:
    with pytest.raises(ValidationError, match="must be <="):
        Settings(
            _env_file=None,
            store={
                "lock_retry_initial_delay_seconds": 2.0,
                "lock_retry_max_delay_seconds": 1.0,
            },
        )


# --- column-type cache correctness (T4 review, third pass) --------------


def test_insert_row_before_init_schema_raises_clear_error() -> None:
    """Before `init_schema`, `prices_daily` has no columns at all --
    `insert_row` must say so plainly, not cache an empty result that would
    silently skip every type check forever (`_column_types` / third
    review pass)."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())

    with pytest.raises(ValueError, match="has no columns; run init_schema first"):
        insert_row(conn, "prices_daily", row)

    schema.init_schema(conn)

    # The pre-init lookup must not have poisoned the cache with an empty
    # result: a naive known_at is still rejected after init_schema runs.
    with pytest.raises(ValueError, match="known_at"):
        insert_row(conn, "prices_daily", row)


# --- canonical UTC binding (issue #43) -----------------------------------


def test_insert_row_binds_normalized_utc_value(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-UTC aware `known_at` is normalized to UTC before it is bound
    to the `INSERT`, not passed through as the caller's original tzinfo —
    one canonical form (issue #43). Verified two ways: the caller's `row`
    dict is left untouched, and the actual value handed to
    `DuckDBPyConnection.execute` has `tzinfo is UTC`."""
    eastern = timezone(timedelta(hours=-5))
    non_utc_known_at = datetime(2020, 1, 1, 7, 0, 0, tzinfo=eastern)
    non_utc_ingested_at = _now().astimezone(ZoneInfo("Asia/Tokyo"))
    row = _minimal_row("prices_daily", known_at=non_utc_known_at, ingested_at=non_utc_ingested_at)
    original_known_at = row["known_at"]

    captured: dict[str, list[object]] = {}
    connection_cls = type(fixture_store)
    real_execute = connection_cls.execute

    def spy_execute(self: duckdb.DuckDBPyConnection, sql: str, params: object = None) -> object:
        if params is not None and sql.startswith("INSERT INTO prices_daily"):
            captured["columns"] = list(row.keys())
            captured["params"] = list(params)  # type: ignore[arg-type]
        return real_execute(self, sql, params) if params is not None else real_execute(self, sql)

    monkeypatch.setattr(connection_cls, "execute", spy_execute)

    insert_row(fixture_store, "prices_daily", row)

    # The caller's Mapping must not be mutated in place.
    assert row["known_at"] is original_known_at
    assert row["known_at"].tzinfo == eastern  # type: ignore[union-attr]

    assert "params" in captured
    idx = captured["columns"].index("known_at")
    bound_known_at = captured["params"][idx]
    assert isinstance(bound_known_at, datetime)
    assert bound_known_at.tzinfo is UTC
    assert bound_known_at == non_utc_known_at  # same instant
    bound_ingested_at = captured["params"][captured["columns"].index("ingested_at")]
    assert isinstance(bound_ingested_at, datetime)
    assert bound_ingested_at.tzinfo is UTC
    assert bound_ingested_at == non_utc_ingested_at

    # And the instant actually stored matches the original instant.
    (stored,) = fixture_store.execute(  # type: ignore[misc]
        "SELECT known_at FROM prices_daily WHERE security_id = ? AND session = ?",
        [row["security_id"], row["session"]],
    ).fetchone()
    assert stored == non_utc_known_at


def test_insert_row_raises_value_error_not_overflow_error_on_utc_overflow(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """An overflowing `TIMESTAMPTZ` value (e.g. a far-future sentinel with
    a non-zero offset) must surface as `ValueError` naming `table.field`,
    not the underlying `OverflowError` (issue #43)."""
    overflowing = datetime.max.replace(tzinfo=timezone(timedelta(hours=-5)))
    row = _minimal_row("prices_daily", known_at=overflowing, ingested_at=_now())
    with pytest.raises(ValueError, match=r"prices_daily\.known_at") as exc_info:
        insert_row(fixture_store, "prices_daily", row)
    assert not isinstance(exc_info.value, OverflowError)


def test_column_types_ignore_attached_database_with_same_named_table() -> None:
    """`_column_types` filters on `current_database()`/`current_schema()`
    so an `ATTACH`ed database's same-named table with a differently-typed
    same-named column cannot override the real column type (probed: an
    unfiltered query returns rows from both catalogs, and collapsing them
    into a dict silently picks whichever came last)."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)

    conn.execute("ATTACH ':memory:' AS other")
    conn.execute("CREATE TABLE other.prices_daily (known_at VARCHAR)")

    naive = datetime(2020, 1, 1)  # noqa: DTZ001
    row = _minimal_row("prices_daily", known_at=naive, ingested_at=_now())
    with pytest.raises(ValueError, match="known_at"):
        insert_row(conn, "prices_daily", row)


# --- corporate action identity (#108) ---------------------------------


def test_corporate_actions_carry_source_action_id_and_cancelled(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """`source_action_id` uses `''` for "no id" (the `class_member`
    convention, so UNIQUE still bites for id-less rows) and `cancelled`
    defaults to false."""
    info = fixture_store.execute("PRAGMA table_info('corporate_actions')").fetchall()
    columns = {row[1]: row for row in info}
    _, _, id_type, id_not_null, id_default, _ = columns["source_action_id"]
    assert id_type == "VARCHAR" and id_not_null and id_default == "''"
    _, _, cancelled_type, cancelled_not_null, _, _ = columns["cancelled"]
    assert cancelled_type == "BOOLEAN" and cancelled_not_null

    now = _now()
    insert_row(
        fixture_store,
        "corporate_actions",
        _minimal_row("corporate_actions", known_at=now, ingested_at=now),
    )
    assert fixture_store.execute(
        "SELECT source_action_id, cancelled FROM corporate_actions WHERE security_id = 'S1'"
    ).fetchall() == [("", False)]


def test_schema_version_is_bumped_past_action_identity() -> None:
    """#108 took version 4; the Phase 4 journal (T49) is version 5; #332's
    `order_events.reason` CHECK is version 6; #377's `decisions.reason` CHECK
    is version 7; #472's `resume_invocations.accept_rejections` is version 8;
    #571's owner settlement (`settle_order`, `owner_settled_unknown`,
    `overrides.client_order_id`) is version 9; #660's `statement_facts`
    (T76) is version 10 (renumbered from 9 at ready time, T84/#714 landed
    version 9 first); #859's retraction is version 11; the research registry
    (#926, T80) is version 12; the profitability rebalance columns (#720,
    #1033, T85d) is version 13; the generic rebalance counts and the
    `signals.reason` prefix `CHECK` (#1153, T127) is version 14; the period keys
    (#1179, T97) are version 15; the lab migration (#1195, T113) is version 16;
    the ADR 0015 expansion seams (#1258, T132) are version 17."""
    assert schema.CURRENT_SCHEMA_VERSION == 22


# --- version 9 (#571, spec req 17): the `settle_order` override ----------------------

_OVERRIDE_AT = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)


def _override(
    conn: duckdb.DuckDBPyConnection, override_id: int, kind: str, client_order_id: str | None
) -> None:
    conn.execute(
        "INSERT INTO overrides (override_id, window_id, made_at, security_id, "
        "client_order_id, kind, reason, known_at, ingested_at) "
        "VALUES (?, 1, ?, 'S1', ?, ?, 'a reason long enough', ?, ?)",
        [override_id, _OVERRIDE_AT, client_order_id, kind, _OVERRIDE_AT, _OVERRIDE_AT],
    )


def test_settle_order_is_an_override_kind_spelt_as_the_spec_names_it() -> None:
    """Spec "Data / interfaces" > Tables: `kind ∈ {exclude_name, keep_name,
    engage_kill_switch, settle_order}`."""
    assert schema.SETTLE_ORDER_KIND == "settle_order"
    assert schema.JOURNAL_ENUMS["overrides", "kind"] == (
        "exclude_name",
        "keep_name",
        "engage_kill_switch",
        schema.SETTLE_ORDER_KIND,
    )


def test_a_settle_order_override_with_its_order_is_accepted() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    _override(conn, 1, schema.SETTLE_ORDER_KIND, "tp-1")
    assert conn.execute("SELECT kind, client_order_id FROM overrides").fetchall() == [
        ("settle_order", "tp-1")
    ]


def test_a_settle_order_override_without_an_order_is_refused() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    with pytest.raises(duckdb.ConstraintException):
        _override(conn, 1, schema.SETTLE_ORDER_KIND, None)


@pytest.mark.parametrize("kind", ["exclude_name", "keep_name", "engage_kill_switch"])
def test_only_a_settle_order_override_may_carry_an_order(kind: str) -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    with pytest.raises(duckdb.ConstraintException):
        _override(conn, 1, kind, "tp-1")
    _override(conn, 2, kind, None)
    assert conn.execute("SELECT client_order_id FROM overrides").fetchall() == [(None,)]


def test_two_source_ids_may_share_an_ex_date_and_known_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A regular and a special dividend on one ex-date are two events."""
    now = _now()
    for source_action_id in ("D1", "D2"):
        row = _minimal_row("corporate_actions", known_at=now, ingested_at=now)
        insert_row(
            fixture_store, "corporate_actions", {**row, "source_action_id": source_action_id}
        )
    row = _minimal_row("corporate_actions", known_at=now, ingested_at=now)
    with pytest.raises(duckdb.ConstraintException):
        insert_row(fixture_store, "corporate_actions", {**row, "source_action_id": "D1"})


def test_one_id_has_one_row_per_known_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """Audit finding 3 on PR #111: under an id identity the store itself
    refuses two revisions at one `known_at`, whatever their ex-dates."""
    now = _now()
    row = _minimal_row("corporate_actions", known_at=now, ingested_at=now)
    insert_row(fixture_store, "corporate_actions", {**row, "source_action_id": "A1"})
    with pytest.raises(duckdb.ConstraintException):
        insert_row(
            fixture_store,
            "corporate_actions",
            {**row, "source_action_id": "A1", "ex_date": date(2020, 1, 9)},
        )

"""Tests for schema version 12 (#926, research-registry plan T80; spec req 1 and
"Data / interfaces" > Tables): the five research-registry tables, their
enumeration `CHECK`s, `trial_results.n_research`, the additive migration from
version 11, and the read-only path on a version-11 store."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from lookahead.harness import _FACT_TABLES

from tradepartner.store import lab_schema, schema
from tradepartner.store.db import configure_connection, insert_row

KNOWN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

#: SHA-256 of each pre-version-12 DDL blob as version 11 left it (origin/main at
#: 0c63d05). Version 12 is additive: none of these may change, and
#: `trial_results` gains `n_research` by `ALTER TABLE`, not by a DDL edit.
_V11_DDL_SHA256 = {
    "_TABLE_DDL": "0815559c58066e74829be4956dea3f252eeddb03ab612cb3485b588c6f44b54a",
    "_REGISTRY_TABLE_DDL": "d8152e9e8bd289b606b7de2e976da4ff9b679648a28f4735d54216bca50cd1db",
    "_JOURNAL_TABLE_DDL": "2446512cb972e2dd04b553f8bf022d96ed6dec5af5b9d1f3e6169565ed6fc38d",
    "_STATEMENT_FACTS_TABLE_DDL": (
        "7b68b590db31163266c3ec13d227be93381ff113e30c57cc094a76c5f0342416"
    ),
    "_MASTER_UNDERIVED_TABLE_DDL": (
        "fbbd9f7cb4ebb13b9a20bda2be5e55e70182813d53bde1b73bcf4f31c7d93de5"
    ),
}
#: `signals.reason`'s `CHECK` text in `_JOURNAL_TABLE_DDL` before version 14 (#1153,
#: T127), which widened it and moved nothing else of the blob.
_V13_SIGNALS_CHECK = "CHECK (reason IN ('selected', 'below_cut', 'excluded_no_history'))"
_V11_RETRACTION_DDL_SHA256 = "d976e81c40b6b212eab80165bb570ca5c23a835b7dc0efcf9a87d7167dd4a4eb"


def _row(table: str, **overrides: Any) -> dict[str, Any]:
    """A minimal valid row for each research table (every NOT NULL column)."""
    rows: dict[str, dict[str, Any]] = {
        "research_registrations": {
            "registration_id": 1,
            "slug": "pilot-agreement-d1-d3",
            "kind": "agreement",
            "stage": 2,
            "title": "Annotation pilot",
            "confirmatory": True,
            "provenance": "human",
            "touches_returns": False,
            "claims_json": '["TX-1"]',
            "dataset_name": "annotation-pilot-labels",
            "window_start": date(2017, 1, 1),
            "window_end": date(2023, 12, 31),
            "splits_json": '["pilot"]',
            "primary_metric": "alpha_J_min_over_dimension_mode",
            "primary_direction": "greater",
            "primary_ci_level": 0.95,
            "primary_min_clusters": 100,
            "primary_inference": "issuer-cluster bootstrap",
            "secondary_json": "[]",
            "comparison_set": "annotators A, B",
            "multiplicity_method": "none",
            "budget_runs": 6,
            "budget_configurations": 1,
            "stop_rule": "words",
            "expected_effect": "words",
            "seed": 20261003,
            "doc_path": "docs/experiments/pilot-agreement-d1-d3.md",
            "doc_sha256": "0" * 64,
            "params_json": "{}",
            "params_sha256": "0" * 64,
            "registered_by": "owner",
            "known_at": KNOWN,
        },
        "research_datasets": {
            "dataset_id": 1,
            "name": "annotation-pilot-labels",
            "version": "1",
            "path": "data/research/abc",
            "sha256": "0" * 64,
            "event_start": date(2017, 1, 3),
            "event_end": date(2023, 12, 29),
            "split_spans_json": "{}",
            "sealed_splits_json": "[]",
            "sealed_periods_json": "[]",
            "locked": True,
            "code_version": "abc123",
            "known_at": KNOWN,
        },
        "research_runs": {
            "run_id": 1,
            "registration_id": 1,
            "dataset_id": 1,
            "dataset_sha256": "0" * 64,
            "split": "pilot",
            "config_json": "{}",
            "config_sha256": "0" * 64,
            "n_configurations_declared": 1,
            "confirmatory": True,
            "confirmatory_basis": "predates_dataset",
            "code_version": "abc123",
            "synthetic": True,
            "holdout_spent": False,
            "holdout_repeat": False,
            "run_by": "owner",
            "known_at": KNOWN,
        },
        "research_results": {
            "run_id": 1,
            "outcome": "ok",
            "n_configurations": 1,
            "verdict": "pass",
            "known_at": KNOWN,
        },
        "research_decisions": {
            "decision_id": 1,
            "kind": "holdout_spend",
            "run_id": 1,
            "values_json": "{}",
            "reason": "the one look",
            "made_by": "owner",
            "known_at": KNOWN,
        },
    }
    return rows[table] | overrides


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    return c


def _tables(c: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in c.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database()"
        ).fetchall()
    }


def _ddl(c: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Every table's stored DDL text, by name."""
    return dict(
        c.execute(
            "SELECT table_name, sql FROM duckdb_tables() WHERE database_name = current_database()"
        ).fetchall()
    )


def _columns(c: duckdb.DuckDBPyConnection, table: str) -> dict[str, tuple[str, bool]]:
    return {
        name: (dtype, notnull)
        for _, name, dtype, notnull, _, _ in c.execute(f"PRAGMA table_info('{table}')").fetchall()
    }


def _versions(c: duckdb.DuckDBPyConnection) -> list[int]:
    return [row[0] for row in c.execute("SELECT version FROM schema_version ORDER BY 1").fetchall()]


def _version_11_store(path: Path) -> None:
    """A store as version 11 left it: every pre-version-12 table (the master
    tables rebuilt with `retracted`), no research table, no `n_research`, and
    rows in `trial_results`, `trials`, `securities` and a journal table."""
    with duckdb.connect(str(path)) as c:
        configure_connection(c)
        for ddl in (
            schema._TABLE_DDL
            + schema._REGISTRY_TABLE_DDL
            + schema._JOURNAL_TABLE_DDL
            + schema._STATEMENT_FACTS_TABLE_DDL
            + schema._MASTER_UNDERIVED_TABLE_DDL
        ):
            c.execute(ddl)
        schema._migrate_retracted(c)
        c.execute("INSERT INTO schema_version VALUES (10, ?), (11, ?)", [KNOWN, KNOWN])
        c.execute(
            "INSERT INTO securities (security_id, cik, name, benchmark, known_at, ingested_at, "
            "source, provenance) VALUES ('S1', '0000000001', 'Acme', FALSE, ?, ?, 'edgar', "
            "'filing')",
            [KNOWN, KNOWN],
        )
        c.execute(
            "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
            "end_session, code_version, synthetic, run_by) VALUES "
            "(1, 1, 'in_sample', ?, '2020-01-02', '2020-12-31', 'abc', TRUE, 'owner'), "
            "(2, 1, 'in_sample', ?, '2020-01-02', '2020-12-31', 'abc', TRUE, 'owner')",
            [KNOWN, KNOWN],
        )
        c.execute(
            "INSERT INTO trial_results (trial_id, finished_at, status, n_trials) VALUES "
            "(1, ?, 'ok', 1), (2, ?, 'failed', NULL)",
            [KNOWN, KNOWN],
        )
        c.execute(
            'INSERT INTO kill_switch (event_id, window_id, "at", state, source, reason, '
            "known_at, ingested_at) VALUES (1, 1, ?, 'engaged', 'owner', 'test', ?, ?)",
            [KNOWN, KNOWN, KNOWN],
        )


#: The columns version 15 (#1179, T97) adds to `trials`.
_TRIALS_V15 = ("detail_level", "data_vintage", "code_tree_sha256")


def _snapshot(c: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[Any, ...]]]:
    """Every pre-version-12 table's rows in insertion order (`trial_results`
    without `n_research` and `sharpe_unit`, `trials` without the version-15
    columns, when they have them)."""
    out = {}
    later = {
        *schema.RESEARCH_TABLE_NAMES,
        schema.REBALANCE_COUNTS_TABLE_NAME,
        *lab_schema.LAB_TABLE_NAMES,
    }
    for table in _tables(c) - later:
        if table == "schema_version":
            continue
        later_columns = [
            column
            for column in ("n_research", "sharpe_unit", *_TRIALS_V15)
            if column in _columns(c, table)
        ]
        exclude = f" EXCLUDE ({', '.join(later_columns)})" if later_columns else ""
        out[table] = c.execute(f"SELECT *{exclude} FROM {table} ORDER BY rowid").fetchall()
    return out


# --- constants and names -----------------------------------------------------------


def test_current_schema_version_is_16() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 16


def test_research_table_names_are_the_spec_five() -> None:
    assert schema.RESEARCH_TABLE_NAMES == (
        "research_registrations",
        "research_datasets",
        "research_runs",
        "research_results",
        "research_decisions",
    )


def test_research_table_names_disjoint_from_every_other_set() -> None:
    research = set(schema.RESEARCH_TABLE_NAMES)
    assert research & set(schema.TABLE_NAMES) == set()
    assert research & set(schema.REGISTRY_TABLE_NAMES) == set()
    assert research & set(schema.JOURNAL_TABLE_NAMES) == set()
    assert research & set(schema.MASTER_CHECK_TABLE_NAMES) == set()


def test_the_look_ahead_harness_never_sees_a_research_table() -> None:
    """The harness truncates `TABLE_NAMES` fact tables only; its own suite
    (`tests/lookahead/`) runs unchanged on version 12."""
    assert set(_FACT_TABLES) & set(schema.RESEARCH_TABLE_NAMES) == set()


def test_the_enumerations_are_the_specs_sets() -> None:
    """Code constants pinned by a test: a new value is a spec amendment
    (research-registry spec "Config keys")."""
    assert schema.RESEARCH_KINDS == ("agreement", "benchmark", "robustness", "economic", "return")
    assert schema.RESEARCH_STAGES == (2, 3, 4, 5, 6, 7)
    assert schema.RESEARCH_PROVENANCES == (
        "human",
        "deterministic",
        "classical",
        "model_historical",
        "model_prospective",
        "pit_model",
    )
    assert schema.RESEARCH_DIRECTIONS == ("greater", "less")
    assert schema.RESEARCH_MULTIPLICITY_METHODS == ("holm", "fixed_sequence", "bh", "none")
    assert schema.RESEARCH_SPLITS == (
        "dev",
        "cal",
        "test",
        "prospective",
        "pilot",
        "full",
        "none",
    )
    assert schema.RESEARCH_CONFIRMATORY_BASES == ("predates_dataset", "sealed_split", "none")
    assert schema.RESEARCH_OUTCOMES == (
        "ok",
        "failed",
        "refused_window",
        "refused_holdout",
        "refused_split",
        "refused_budget",
        "abandoned",
    )
    assert schema.RESEARCH_VERDICTS == ("pass", "fail", "underpowered", "n/a")
    assert schema.RESEARCH_DECISION_KINDS == ("holdout_spend", "budget_amend")


def test_every_pre_version_12_ddl_blob_is_unchanged() -> None:
    """Version 12 is additive: no fact, registry, journal, statement-facts or
    master DDL text moves (`n_research` arrives by `ALTER TABLE`)."""
    for name, digest in _V11_DDL_SHA256.items():
        blob = "".join(getattr(schema, name))
        if name == "_JOURNAL_TABLE_DDL":
            blob = blob.replace(schema._SIGNALS_REASON_CHECK, _V13_SIGNALS_CHECK, 1)
        assert hashlib.sha256(blob.encode()).hexdigest() == digest, name
    retraction = "".join(schema._RETRACTION_TABLE_DDL[t] for t in ("securities", "listings"))
    assert hashlib.sha256(retraction.encode()).hexdigest() == _V11_RETRACTION_DDL_SHA256


# --- fresh store ---------------------------------------------------------------------


def test_a_fresh_store_has_every_research_table_at_version_16(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    assert set(schema.RESEARCH_TABLE_NAMES) <= _tables(conn)
    assert _versions(conn) == [16]
    assert _columns(conn, "trial_results")["n_research"] == ("INTEGER", False)
    schema.require_research(conn)


def test_init_schema_is_idempotent_on_version_16(conn: duckdb.DuckDBPyConnection) -> None:
    before = _ddl(conn)
    schema.init_schema(conn)
    assert _ddl(conn) == before
    assert _versions(conn) == [16]


@pytest.mark.parametrize("table", schema.RESEARCH_TABLE_NAMES)
def test_every_research_table_has_known_at_timestamptz_not_null(
    conn: duckdb.DuckDBPyConnection, table: str
) -> None:
    columns = _columns(conn, table)
    assert columns["known_at"] == ("TIMESTAMP WITH TIME ZONE", True)
    # Not a fact table: no `ingested_at` or `source` (the registrations'
    # `provenance` is the treatment's, an enumeration, not ADR 0003's column).
    assert "ingested_at" not in columns
    assert "source" not in columns


def test_the_spec_columns_exactly(conn: duckdb.DuckDBPyConnection) -> None:
    """The column lists of the spec's Data / interfaces, in order, and none of
    the dropped import columns (req 12, #810)."""
    expected = {
        "research_registrations": [
            "registration_id",
            "slug",
            "kind",
            "stage",
            "title",
            "confirmatory",
            "provenance",
            "touches_returns",
            "family",
            "claims_json",
            "hypothesis_ref",
            "dataset_name",
            "dataset_sha256_pin",
            "window_start",
            "window_end",
            "splits_json",
            "primary_metric",
            "primary_direction",
            "primary_threshold",
            "primary_ci_level",
            "primary_min_clusters",
            "primary_inference",
            "secondary_json",
            "comparison_set",
            "multiplicity_method",
            "multiplicity_family_id",
            "multiplicity_family_size",
            "budget_runs",
            "budget_configurations",
            "stop_rule",
            "expected_effect",
            "seed",
            "doc_path",
            "doc_sha256",
            "params_json",
            "params_sha256",
            "amends_registration_id",
            "registered_by",
            "known_at",
        ],
        "research_datasets": [
            "dataset_id",
            "name",
            "version",
            "path",
            "sha256",
            "n_rows",
            "event_start",
            "event_end",
            "event_column",
            "split_path",
            "split_sha256",
            "split_spans_json",
            "sealed_splits_json",
            "sealed_periods_json",
            "locked",
            "seed",
            "code_version",
            "code_dirty",
            "note",
            "known_at",
        ],
        "research_runs": [
            "run_id",
            "registration_id",
            "dataset_id",
            "dataset_sha256",
            "split",
            "config_json",
            "config_sha256",
            "n_configurations_declared",
            "confirmatory",
            "confirmatory_basis",
            "code_version",
            "code_dirty",
            "store_max_ingested_at",
            "synthetic",
            "holdout_spent",
            "holdout_repeat",
            "holdout_reason",
            "run_by",
            "note",
            "known_at",
        ],
        "research_results": [
            "run_id",
            "outcome",
            "message",
            "primary_value",
            "primary_ci_low",
            "primary_ci_high",
            "n_observations",
            "n_clusters",
            "n_configurations",
            "secondary_json",
            "exploratory_json",
            "verdict",
            "artifact_sha256",
            "artifact_path",
            "known_at",
        ],
        "research_decisions": [
            "decision_id",
            "kind",
            "registration_id",
            "run_id",
            "values_json",
            "reason",
            "made_by",
            "known_at",
        ],
    }
    for table, columns in expected.items():
        assert list(_columns(conn, table)) == columns, table


def test_no_import_column_exists_anywhere(conn: duckdb.DuckDBPyConnection) -> None:
    """Req 12 dropped the interim-log import with its two columns (#810)."""
    found = conn.execute(
        "SELECT table_name, column_name FROM duckdb_columns() WHERE column_name IN "
        "('imported_from_sha256', 'source_logged_at')"
    ).fetchall()
    assert found == []


# --- CHECKs and keys -----------------------------------------------------------------


@pytest.mark.parametrize("table", schema.RESEARCH_TABLE_NAMES)
def test_a_minimal_row_is_accepted_through_insert_row(
    conn: duckdb.DuckDBPyConnection, table: str
) -> None:
    insert_row(conn, table, _row(table))
    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)


@pytest.mark.parametrize(("table", "column"), sorted(schema.RESEARCH_ENUMS))
def test_every_enumeration_check_accepts_its_set_and_refuses_anything_else(
    conn: duckdb.DuckDBPyConnection, table: str, column: str
) -> None:
    key = {
        "research_registrations": "registration_id",
        "research_runs": "run_id",
        "research_results": "run_id",
        "research_decisions": "decision_id",
    }[table]
    for i, value in enumerate(schema.RESEARCH_ENUMS[table, column], start=1):
        extra: dict[str, Any] = {key: i, column: value}
        if (table, column, value) == ("research_registrations", "kind", "return"):
            extra |= {"touches_returns": True, "family": "momentum"}
        if table == "research_results":
            # `verdict` is set exactly on an `ok` result.
            if column == "outcome":
                extra["verdict"] = "pass" if value == "ok" else None
            else:
                extra["outcome"] = "ok"
        insert_row(conn, table, _row(table, **extra))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(conn, table, _row(table, **{key: 999, column: "import"}))


def test_stage_is_two_to_seven(conn: duckdb.DuckDBPyConnection) -> None:
    for stage in schema.RESEARCH_STAGES:
        insert_row(
            conn,
            "research_registrations",
            _row("research_registrations", registration_id=stage, stage=stage),
        )
    for stage in (1, 8):
        with pytest.raises(duckdb.ConstraintException):
            insert_row(
                conn,
                "research_registrations",
                _row("research_registrations", registration_id=100 + stage, stage=stage),
            )


def test_verdict_is_set_exactly_on_an_ok_result(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        insert_row(conn, "research_results", _row("research_results", verdict=None))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(
            conn,
            "research_results",
            _row("research_results", run_id=2, outcome="failed", verdict="fail"),
        )
    insert_row(
        conn,
        "research_results",
        _row("research_results", run_id=3, outcome="refused_holdout", verdict=None),
    )


@pytest.mark.parametrize("n_configurations", [None, 0, -1])
def test_an_ok_result_reports_at_least_one_configuration(
    conn: duckdb.DuckDBPyConnection, n_configurations: int | None
) -> None:
    """`family_run_count` sums `n_configurations` over `ok` runs into N (req 9):
    an `ok` row without a positive count would undercount N (quant audit of
    PR #940). A row with no statistics needs none."""
    with pytest.raises(duckdb.ConstraintException):
        insert_row(
            conn,
            "research_results",
            _row("research_results", n_configurations=n_configurations),
        )
    insert_row(
        conn,
        "research_results",
        _row("research_results", outcome="failed", verdict=None, n_configurations=None),
    )


@pytest.mark.parametrize("declared", [0, -1])
def test_a_run_declares_at_least_one_configuration(
    conn: duckdb.DuckDBPyConnection, declared: int
) -> None:
    with pytest.raises(duckdb.ConstraintException):
        insert_row(conn, "research_runs", _row("research_runs", n_configurations_declared=declared))


def test_a_return_registration_touches_returns_and_names_a_family(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Req 2's two rules N depends on, backstopped below the parser."""
    reg = "research_registrations"
    with pytest.raises(duckdb.ConstraintException):
        insert_row(conn, reg, _row(reg, kind="return", touches_returns=False, family="momentum"))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(conn, reg, _row(reg, kind="economic", touches_returns=True, family=None))
    insert_row(conn, reg, _row(reg, kind="return", touches_returns=True, family="momentum"))


def test_a_second_result_row_for_a_run_raises(conn: duckdb.DuckDBPyConnection) -> None:
    insert_row(conn, "research_results", _row("research_results"))
    with pytest.raises(duckdb.ConstraintException):
        insert_row(
            conn,
            "research_results",
            _row("research_results", outcome="abandoned", verdict=None),
        )


@pytest.mark.parametrize("table", schema.RESEARCH_TABLE_NAMES)
def test_known_at_must_be_tz_aware(conn: duckdb.DuckDBPyConnection, table: str) -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        insert_row(conn, table, _row(table, known_at=KNOWN.replace(tzinfo=None)))


# --- migration from version 11 -------------------------------------------------------


def test_the_migration_from_version_11_is_additive(tmp_path: Path) -> None:
    path = tmp_path / "v11.duckdb"
    _version_11_store(path)
    with duckdb.connect(str(path)) as c:
        ddl_before = _ddl(c)
        rows_before = _snapshot(c)
        schema.init_schema(c)
        ddl_after = _ddl(c)
        rows_after = _snapshot(c)
        assert _versions(c) == [10, 11, 12, 13, 14, 15, 16]
        assert set(schema.RESEARCH_TABLE_NAMES) <= set(ddl_after)
        n_research = c.execute("SELECT trial_id, n_research FROM trial_results ORDER BY 1")
        assert n_research.fetchall() == [(1, None), (2, None)]
        schema.require_research(c)
    # No pre-existing table's DDL text changed but `trial_results` (gained
    # `n_research`, version 12) and `trial_rebalances` (gained
    # `PROFITABILITY_REBALANCE_COLUMNS`, version 13) and `trials` (gained its
    # vintage and detail columns, version 15) and `owner_decisions` and
    # `trial_results` (their `CHECK` widened by the lab migration, version 16,
    # `tests/store/test_lab_migration.py`); no row of any table changed.
    _moved = {"trial_results", "trial_rebalances", "trials", "owner_decisions"}
    assert {t: s for t, s in ddl_after.items() if t in ddl_before and t not in _moved} == {
        t: s for t, s in ddl_before.items() if t not in _moved
    }
    lab_status = ", " + ", ".join(f"'{v}'" for v in lab_schema.LAB_TRIAL_STATUSES)
    assert ddl_after["trial_results"].replace(lab_status, "") == ddl_before[
        "trial_results"
    ].replace(
        "red_flag BOOLEAN, gap_max_count_share DOUBLE, gap_max_size_share DOUBLE, ",
        "red_flag BOOLEAN, gap_max_count_share DOUBLE, gap_max_size_share DOUBLE, "
        "n_research INTEGER, sharpe_unit VARCHAR, ",
    )
    assert all(f"{column} " in ddl_after["trials"] for column in _TRIALS_V15)
    assert ddl_after["trial_results"] != ddl_before["trial_results"]
    assert ddl_after["trial_rebalances"] != ddl_before["trial_rebalances"]
    assert all(
        c not in ddl_before["trial_rebalances"] for c in schema.PROFITABILITY_REBALANCE_COLUMNS
    )
    assert all(c in ddl_after["trial_rebalances"] for c in schema.PROFITABILITY_REBALANCE_COLUMNS)
    assert rows_after == rows_before
    # And the migrated store is shaped exactly as a fresh one.
    fresh = duckdb.connect(":memory:")
    schema.init_schema(fresh)
    lab_schema.apply_lab_schema(fresh)  # a fresh store has no lab table
    assert ddl_after == _ddl(fresh)


def test_a_failed_migration_leaves_the_version_11_store_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "v11.duckdb"
    _version_11_store(path)

    def boom(conn: duckdb.DuckDBPyConnection) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(schema, "_migrate_n_research", boom)
    with duckdb.connect(str(path)) as c:
        before = _ddl(c)
        with pytest.raises(RuntimeError, match="boom"):
            schema.init_schema(c)
        assert _ddl(c) == before
        assert _versions(c) == [10, 11]


# --- read-only ---------------------------------------------------------------------


def test_a_read_only_open_of_a_version_11_store_serves_every_other_read(
    tmp_path: Path,
) -> None:
    """A read-only connection never migrates: `init_schema` returns, every
    non-research read works, and a research read raises
    `ResearchNotInitialised` (the research readers call `require_research`)."""
    path = tmp_path / "v11.duckdb"
    _version_11_store(path)
    with duckdb.connect(str(path), read_only=True) as c:
        schema.init_schema(c)
        assert c.execute("SELECT count(*) FROM trial_results").fetchone() == (2,)
        assert c.execute("SELECT count(*) FROM kill_switch").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM securities").fetchone() == (1,)
        with pytest.raises(schema.ResearchNotInitialised, match="not initialised"):
            schema.require_research(c)
        assert _versions(c) == [10, 11]


def test_a_read_only_open_of_a_version_15_store_passes(tmp_path: Path) -> None:
    path = tmp_path / "v15.duckdb"
    with duckdb.connect(str(path)) as c:
        schema.init_schema(c)
    with duckdb.connect(str(path), read_only=True) as c:
        schema.init_schema(c)
        schema.require_research(c)

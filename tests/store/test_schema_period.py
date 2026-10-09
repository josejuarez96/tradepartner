"""Schema version 15, "P" (#1179, strategy-lab plan T97; spec reqs 8 and 9).

The migration from version 14 on a store holding a Phase 3 trial: it inserts the
`*_period`, `n_periods`, `periods_per_year = 12`, `turnover_annual` and
`sharpe_annual_excess_spy` rows for every existing trial, leaves the `*_monthly` rows,
sets `trials.detail_level = full`, leaves `trial_results.sharpe_unit` NULL (read as
`monthly`), and leaves every other table byte-identical.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.backtest.metrics import METRIC_KEYS
from tradepartner.config import Settings
from tradepartner.store import lab_schema, registry, schema

_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_CUTOFF = datetime(2024, 9, 30, 20, 0, tzinfo=UTC)
_LEVELS = (0.0, 15.0)

#: The Phase 3 keys (`metrics.METRIC_KEYS` before version 15), with a value each.
_PHASE3: dict[str, float] = {
    "cagr": 0.08,
    "vol_annual": 0.15,
    "sharpe_monthly": 0.2,
    "sharpe_annual": 0.2 * math.sqrt(12),
    "sharpe_monthly_excess_spy": 0.05,
    "max_drawdown": -0.2,
    "turnover_monthly": 0.3,
    "cost_drag": 0.002,
    "excess_cagr_spy": 0.01,
    "excess_cagr_mtum": -0.005,
    "tracking_error_spy": 0.05,
    "tracking_error_mtum": 0.04,
    "skew_monthly": -0.3,
    "kurtosis_monthly": 3.5,
    "skew_monthly_excess_spy": 0.1,
    "kurtosis_monthly_excess_spy": 3.2,
    "n_months": 40.0,
}
_EXCESS = ("sharpe_monthly_excess_spy", "skew_monthly_excess_spy", "kurtosis_monthly_excess_spy")
_TRIALS_V15 = ("detail_level", "data_vintage", "code_tree_sha256")
#: The migration runs on to version 16 (#1195, T113), which rebuilds
#: `owner_decisions` (every row kept) and adds the lab tables
#: (`tests/store/test_lab_migration.py`).
_CHANGED = {"schema_version", "trials", "trial_results", "trial_metrics", "owner_decisions"}
_LAB = set(lab_schema.LAB_TABLE_NAMES)


def _phase3_value(series: str, level: float, key: str) -> float | None:
    if series == "SPY" and key in _EXCESS:
        return None
    return _PHASE3[key] + level / 1000 + (0.0 if series == "strategy" else 0.5)


def _version_14_store(conn: duckdb.DuckDBPyConnection) -> None:
    """A store shaped as version 14 left it, holding one Phase 3 trial (an `ok`
    in-sample run of a registered momentum hypothesis with every Phase 3 metric row
    for three series at two levels), a refused trial, a price row and a journal
    row."""
    schema.init_schema(conn)
    # `development_boundary` is version 18's (#1319, T140b), dropped too.
    trials = (*_TRIALS_V15, "development_boundary")
    for table, columns in (("trials", trials), ("trial_results", ("sharpe_unit",))):
        for column in columns:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (14, ?)", [_AT])
    record = registry.register_hypothesis(
        conn,
        slug="h1",
        family="momentum",
        title="h1",
        doc_path="docs/hypotheses/h1.md",
        doc_sha256="d" * 64,
        params={"costs.per_side_bps": 15.0, "strategy.top_fraction": 0.1},
        in_sample_start=date(2020, 8, 31),
        holdout_start=date(2024, 1, 2),
        holdout_end=date(2026, 9, 30),
        registered_by="owner",
        settings=Settings(_env_file=None, store={"path": "/nonexistent/real.duckdb"}),
    )
    for trial_id, status in ((1, "ok"), (2, "refused_window")):
        conn.execute(
            "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
            "end_session, data_cutoff, code_version, code_dirty, synthetic, run_by) VALUES "
            "(?, ?, 'in_sample', ?, '2020-08-31', '2023-12-29', ?, 'abc', false, false, 'owner')",
            [trial_id, record.hypothesis_id, _AT, _CUTOFF],
        )
        conn.execute(
            "INSERT INTO trial_results (trial_id, finished_at, status, n_trials, "
            "sharpe_variance, sr_star, dsr, dsr_basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trial_id,
                _AT,
                status,
                *((1, None, 0.0, 0.93, "psr") if status == "ok" else [None] * 5),
            ],
        )
    conn.executemany(
        "INSERT INTO trial_metrics VALUES (1, ?, ?, ?, ?)",
        [
            [series, level, key, _phase3_value(series, level, key)]
            for series in ("strategy", "SPY", "MTUM")
            for level in _LEVELS
            for key in _PHASE3
        ],
    )
    conn.execute(
        "INSERT INTO prices_daily VALUES "
        "('SEC_A', '2024-09-30', 1, 1, 1, 1, 1, ?, ?, 'test', 'bar')",
        [_CUTOFF, _AT],
    )


def _tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    return sorted(t for (t,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall())


def _snapshot(conn: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, Any]:
    """Each table's DDL and rows in insertion order."""
    out = {}
    for table in tables:
        (ddl,) = conn.execute(  # type: ignore[misc]
            "SELECT sql FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone()
        out[table] = (ddl, conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall())
    return out


def _metric_rows(conn: duckdb.DuckDBPyConnection) -> set[tuple[Any, ...]]:
    return set(conn.execute("SELECT * FROM trial_metrics").fetchall())


@pytest.fixture
def v14() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    try:
        _version_14_store(conn)
        yield conn
    finally:
        conn.close()


def test_current_schema_version_is_20() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 20


def test_the_rename_table_covers_every_phase3_period_key() -> None:
    assert set(schema.PERIOD_KEY_RENAMES) == {
        k for k in _PHASE3 if "monthly" in k or k == "n_months"
    }
    assert set(schema.PERIOD_KEY_RENAMES.values()) <= set(METRIC_KEYS)
    added = {"periods_per_year", "turnover_annual", "sharpe_annual_excess_spy"}
    kept = {k for k in _PHASE3 if k not in schema.PERIOD_KEY_RENAMES}
    assert kept | set(schema.PERIOD_KEY_RENAMES.values()) | added == set(METRIC_KEYS)


def test_the_migration_inserts_the_period_rows_and_keeps_the_monthly_ones(
    v14: duckdb.DuckDBPyConnection,
) -> None:
    before = _metric_rows(v14)
    schema.init_schema(v14)
    after = _metric_rows(v14)
    assert before <= after  # every `*_monthly` and `n_months` row stays
    expected: set[tuple[Any, ...]] = set()
    for series in ("strategy", "SPY", "MTUM"):
        for level in _LEVELS:
            for old, new in schema.PERIOD_KEY_RENAMES.items():
                expected.add((1, series, level, new, _phase3_value(series, level, old)))
            expected.add((1, series, level, "periods_per_year", 12.0))
            turnover = _phase3_value(series, level, "turnover_monthly")
            assert turnover is not None
            expected.add((1, series, level, "turnover_annual", 12 * turnover))
            excess = _phase3_value(series, level, "sharpe_monthly_excess_spy")
            annual = None if excess is None else math.sqrt(12) * excess
            expected.add((1, series, level, "sharpe_annual_excess_spy", annual))
    inserted = after - before
    assert {row[:4] for row in inserted} == {row[:4] for row in expected}
    by_key = {row[:4]: row[4] for row in inserted}
    for *key, value in expected:
        got = by_key[tuple(key)]
        assert (got is None) == (value is None), key
        if value is not None:
            assert got == pytest.approx(value, rel=1e-15), key
    # Every Phase 3 trial now has every req 8 key at every series and level.
    for series in ("strategy", "SPY", "MTUM"):
        for level in _LEVELS:
            keys = {row[3] for row in after if row[1] == series and row[2] == level}
            assert set(METRIC_KEYS) <= keys


def test_the_migration_sets_detail_level_full_and_leaves_the_rest_null(
    v14: duckdb.DuckDBPyConnection,
) -> None:
    schema.init_schema(v14)
    assert v14.execute(
        "SELECT trial_id, detail_level, data_vintage, code_tree_sha256 FROM trials ORDER BY 1"
    ).fetchall() == [(1, "full", None, None), (2, "full", None, None)]
    assert v14.execute("SELECT trial_id, sharpe_unit FROM trial_results ORDER BY 1").fetchall() == [
        (1, None),
        (2, None),
    ]
    assert v14.execute("SELECT version FROM schema_version ORDER BY 1").fetchall() == [
        (14,),
        (15,),
        (16,),
        (17,),
        (18,),
        (19,),
        (20,),
    ]


def test_every_other_table_is_byte_identical(v14: duckdb.DuckDBPyConnection) -> None:
    others = [t for t in _tables(v14) if t not in _CHANGED]
    before = _snapshot(v14, others)
    trials = v14.execute("SELECT * FROM trials ORDER BY rowid").fetchall()
    results = v14.execute("SELECT * FROM trial_results ORDER BY rowid").fetchall()
    schema.init_schema(v14)
    assert [t for t in _tables(v14) if t not in _CHANGED | _LAB] == others
    assert _snapshot(v14, others) == before
    v15 = ", ".join((*_TRIALS_V15, "development_boundary"))
    assert v14.execute(f"SELECT * EXCLUDE ({v15}) FROM trials ORDER BY rowid").fetchall() == trials
    assert (
        v14.execute("SELECT * EXCLUDE (sharpe_unit) FROM trial_results ORDER BY rowid").fetchall()
        == results
    )


def test_the_migrated_store_is_shaped_as_a_fresh_one(v14: duckdb.DuckDBPyConnection) -> None:
    schema.init_schema(v14)
    fresh = duckdb.connect(":memory:")
    try:
        schema.init_schema(fresh)
        lab_schema.apply_lab_schema(fresh)  # a fresh store has no lab table
        ddl = "SELECT table_name, sql FROM duckdb_tables() ORDER BY 1"
        assert v14.execute(ddl).fetchall() == fresh.execute(ddl).fetchall()
    finally:
        fresh.close()


def test_a_second_open_inserts_nothing_more(v14: duckdb.DuckDBPyConnection) -> None:
    schema.init_schema(v14)
    rows = _metric_rows(v14)
    schema.init_schema(v14)
    assert _metric_rows(v14) == rows
    assert v14.execute("SELECT count(*) FROM schema_version").fetchone() == (7,)


def test_new_trials_after_the_migration_carry_no_detail_default(
    v14: duckdb.DuckDBPyConnection,
) -> None:
    """The `'full'` default only fills the existing rows; a row inserted without the
    column reads NULL, so every writer sets it (`registry.open_trial` does)."""
    schema.init_schema(v14)
    v14.execute(
        "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
        "end_session, code_version, synthetic, run_by) VALUES "
        "(3, 1, 'in_sample', ?, '2020-08-31', '2023-12-29', 'abc', false, 'owner')",
        [_AT],
    )
    assert v14.execute("SELECT detail_level FROM trials WHERE trial_id = 3").fetchone() == (None,)


def test_family_sharpes_reads_the_migrated_trial_in_annual_units(
    v14: duckdb.DuckDBPyConnection,
) -> None:
    """After the migration a Phase 3 trial is read through the new keys only: its
    base-level period Sharpe times sqrt(12)."""
    schema.init_schema(v14)
    result = registry.family_sharpes(v14, "momentum")
    assert result.n_trials == 1
    assert result.raw == pytest.approx(((_PHASE3["sharpe_monthly"] + 0.015) * math.sqrt(12),))
    assert result.excess_spy == pytest.approx(
        ((_PHASE3["sharpe_monthly_excess_spy"] + 0.015) * math.sqrt(12),)
    )


def test_a_failed_migration_leaves_the_version_14_store_as_it_was(
    v14: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _snapshot(v14, _tables(v14))

    def boom(conn: duckdb.DuckDBPyConnection) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(schema, "_migrate_period_metrics", boom)
    with pytest.raises(RuntimeError, match="boom"):
        schema.init_schema(v14)
    assert _snapshot(v14, _tables(v14)) == before


def test_a_read_only_open_of_a_version_14_store_passes(tmp_path: Any) -> None:
    path = tmp_path / "v14.duckdb"
    with duckdb.connect(str(path)) as conn:
        _version_14_store(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone() == (14,)

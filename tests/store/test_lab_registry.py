"""Tests for the strategy-lab registry (plan T103, `store/lab_registry.py`).

Every function on hand-inserted rows on the `lab_store` fixture: the earliest
fingerprint returned; the family rules copied once, a later `Settings` change
changing nothing; the SR* high-water mark as the maximum of the runs, the
parent's seed and today's value with the V floor; `family_ready_for_sweep`
false before and true after the twin's first `ok` trial; `is_pre_lab` by the
marker only; `promotion_for` by decision row; every function raising
`LabNotInitialised` on a plain `fixture_store`; the append-only text check and
the AST check that only `open_sweep_run` inserts into `sweep_runs`.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import mark_pre_lab

from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.config import Settings
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import insert_row

IN_SAMPLE_START = date(2020, 8, 31)
HOLDOUT_START = date(2024, 1, 2)
HOLDOUT_END = date(2026, 9, 30)
#: The default in-sample window at `month_end`: the last month-end session
#: strictly before `HOLDOUT_START`.
DEFAULT_END = date(2023, 12, 29)
_CUTOFF = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
_FIXED = {
    "costs.per_side_bps": 15.0,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}


def _params(**extra: Any) -> dict[str, Any]:
    return {
        "costs.per_side_bps": 15.0,
        "execution.fill_price": "close",
        "universe.top_n_by_cap": 500,
        "gap.count_share_threshold": 0.02,
        "strategy.top_fraction": 0.1,
        **extra,
    }


def _register(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str = "h1",
    *,
    family: str = "momentum",
    params: dict[str, Any] | None = None,
    doc_sha256: str = "d" * 64,
    holdout_start: date = HOLDOUT_START,
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family=family,
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256=doc_sha256,
        params=params if params is not None else _params(),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=holdout_start,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _trial(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    hypothesis_id: int,
    *,
    status: str = "ok",
    kind: registry.TrialKind = "in_sample",
    window: tuple[date, date] = (IN_SAMPLE_START, DEFAULT_END),
    synthetic: bool = False,
) -> int:
    handle = registry.open_trial(
        conn,
        hypothesis_id=hypothesis_id,
        kind=kind,
        start_session=window[0],
        end_session=window[1],
        data_cutoff=_CUTOFF,
        synthetic=synthetic,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )
    if status == "ok":
        assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"
    else:
        registry.close_trial(conn, handle, status, "test")
    return handle.trial_id


def _rules(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    first_hypothesis_id: int,
    *,
    family: str = "momentum",
    seed: float | None = None,
    parent_family: str | None = None,
) -> lab_registry.FamilyRules:
    return lab_registry.write_family_rules(
        conn,
        family=family,
        first_hypothesis_id=first_hypothesis_id,
        parent_family=parent_family,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        in_sample_start=IN_SAMPLE_START,
        fixed_params=_FIXED,
        sr_star_seed_annual=seed,
        settings=settings,
    )


def _sweep(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str = "mom-grid",
    *,
    family: str = "momentum",
    n_variants: int = 2,
) -> lab_registry.SweepRecord:
    return lab_registry.register_sweep(
        conn,
        slug=slug,
        family=family,
        title="grid",
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.1, 0.2]},
        n_variants=n_variants,
        selection_statistic="dsr_excess",
        expected_excess_cagr_spy_pp=2.0,
        expected_range_pp=(0.0, 4.0),
        promote_at_least=0.6,
        retire_below=0.1,
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _variant(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    sweep: lab_registry.SweepRecord,
    index: int,
    fingerprint: str,
) -> registry.HypothesisRecord:
    record = _register(
        conn,
        settings,
        f"{sweep.slug}--r{sweep.sweep_id}-v{index}",
        family=sweep.family,
        params=_params(**{"strategy.top_fraction": 0.1 * index}),
        doc_sha256=sweep.doc_sha256,
    )
    lab_registry.write_sweep_variant(
        conn,
        sweep_id=sweep.sweep_id,
        variant_index=index,
        hypothesis_id=record.hypothesis_id,
        fingerprint=fingerprint,
        variant_params={"strategy.top_fraction": 0.1 * index},
    )
    lab_registry.write_fingerprint(conn, record.hypothesis_id, fingerprint)
    return record


def _run(
    conn: duckdb.DuckDBPyConnection, sweep_id: int, tmp_path: Path, sr_star: float | None
) -> int:
    run_id = lab_registry.open_sweep_run(
        conn,
        sweep_id=sweep_id,
        time_budget_minutes=480,
        n_declared=2,
        n_planned=2,
        code_tree_sha256="c" * 64,
        run_by="test",
        repo_dir=tmp_path,
    )
    if sr_star is not None:
        lab_registry.close_sweep_run(
            conn,
            run_id,
            n_ok=2,
            n_failed=0,
            n_terminal_failed=0,
            seconds=12.5,
            n_trials_at_end=2,
            sr_star_annual_at_end=sr_star,
            completed=True,
        )
    return run_id


def _decision(
    conn: duckdb.DuckDBPyConnection, kind: str, hypothesis_id: int, values: dict[str, Any]
) -> int:
    decision_id = registry._next_id(conn, "owner_decisions", "decision_id")
    insert_row(
        conn,
        "owner_decisions",
        {
            "decision_id": decision_id,
            "made_at": datetime(2026, 10, 7, tzinfo=UTC),
            "kind": kind,
            "hypothesis_id": hypothesis_id,
            "trial_id": None,
            "values_json": registry.canonical_params_json(values),
            "reason": "test",
        },
    )
    return decision_id


# --- fingerprints --------------------------------------------------------------


def test_fingerprint_registered_returns_the_earliest(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _register(lab_store, settings, "h1")
    second = _register(lab_store, settings, "h1", doc_sha256="e" * 64)
    lab_registry.write_fingerprint(lab_store, second.hypothesis_id, "f" * 64)
    lab_registry.write_fingerprint(lab_store, first.hypothesis_id, "f" * 64)
    found = lab_registry.fingerprint_registered(lab_store, "f" * 64)
    assert found == first
    assert lab_registry.fingerprint_registered(lab_store, "0" * 64) is None


def test_write_fingerprint_refuses_a_second_row_and_an_unknown_hypothesis(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(lab_store, settings)
    lab_registry.write_fingerprint(lab_store, record.hypothesis_id, "f" * 64)
    with pytest.raises(lab_registry.LabRegistryError, match="already has its fingerprint"):
        lab_registry.write_fingerprint(lab_store, record.hypothesis_id, "a" * 64)
    with pytest.raises(registry.UnknownHypothesis):
        lab_registry.write_fingerprint(lab_store, 999, "a" * 64)


# --- family rules --------------------------------------------------------------


def test_family_rules_are_copied_once_and_a_later_settings_change_changes_nothing(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    assert lab_registry.family_rules(lab_store, "momentum") is None
    h1 = _register(lab_store, settings)
    written = _rules(lab_store, settings, h1.hypothesis_id)
    assert written.max_family_holdout_spends == settings.lab.max_family_holdout_spends
    assert written.max_family_promotions == settings.lab.max_family_promotions
    assert written.min_sharpe_variance_annual == settings.lab.min_sharpe_variance_annual
    assert written.axis_lattice == settings.lab.axis_lattice
    assert written.fixed_params == _FIXED
    assert written.fixed_params_sha256 == registry.params_sha256(_FIXED)
    assert written.parent_family is None
    assert written.sr_star_seed_annual is None
    changed = Settings(
        _env_file=None,
        store={"path": settings.store.path},
        lab={
            "max_family_holdout_spends": 9,
            "max_family_promotions": 9,
            "min_sharpe_variance_annual": 0.5,
            "axis_lattice": {"strategy.top_fraction": 0.05},
        },
    )
    with pytest.raises(lab_registry.LabRegistryError, match="already has its family rules"):
        _rules(lab_store, changed, h1.hypothesis_id)
    assert lab_registry.family_rules(lab_store, "momentum") == written


# --- sweeps --------------------------------------------------------------------


def test_register_sweep_copies_the_lab_values_and_sweep_by_slug_takes_the_latest(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _sweep(lab_store, settings)
    assert first.sweep_id == 1
    assert first.max_promotions == settings.lab.max_promotions_per_sweep
    assert first.min_dsr_floor == settings.lab.promotion_min_dsr_excess
    assert first.max_failures_per_variant == settings.lab.max_failures_per_variant
    assert first.axis_lattice == settings.lab.axis_lattice
    assert first.grid == {"strategy.top_fraction": [0.1, 0.2]}
    assert first.grid_sha256 == registry.params_sha256(first.grid)
    assert (first.expected_range_lo_pp, first.expected_range_hi_pp) == (0.0, 4.0)
    _sweep(lab_store, settings, "other")
    second = _sweep(lab_store, settings)
    assert second.sweep_id == 3
    assert lab_registry.sweep_by_slug(lab_store, "mom-grid") == second
    assert lab_registry.sweep_by_slug(lab_store, "absent") is None


def test_sweep_variants_in_canonical_order(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings)
    second = _variant(lab_store, settings, sweep, 2, "b" * 64)
    first = _variant(lab_store, settings, sweep, 1, "a" * 64)
    variants = lab_registry.sweep_variants(lab_store, sweep.sweep_id)
    assert [(v.variant_index, v.hypothesis_id, v.fingerprint) for v in variants] == [
        (1, first.hypothesis_id, "a" * 64),
        (2, second.hypothesis_id, "b" * 64),
    ]
    assert variants[0].variant_params == {"strategy.top_fraction": 0.1}
    assert lab_registry.sweep_variants(lab_store, 99) == []


def test_write_sweep_variant_refuses_an_index_outside_the_sweep(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings)
    record = _register(lab_store, settings)
    for index in (0, 3):
        with pytest.raises(lab_registry.LabRegistryError, match=r"outside 1\.\.2"):
            lab_registry.write_sweep_variant(
                lab_store,
                sweep_id=sweep.sweep_id,
                variant_index=index,
                hypothesis_id=record.hypothesis_id,
                fingerprint="a" * 64,
                variant_params={},
            )
    with pytest.raises(lab_registry.LabRegistryError, match="no sweep"):
        lab_registry.write_sweep_variant(
            lab_store,
            sweep_id=99,
            variant_index=1,
            hypothesis_id=record.hypothesis_id,
            fingerprint="a" * 64,
            variant_params={},
        )


# --- sweep runs ----------------------------------------------------------------


def test_open_and_close_sweep_run(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings)
    run_id = _run(lab_store, sweep.sweep_id, tmp_path, None)
    opened = lab_store.execute(
        "SELECT sweep_id, finished_at, n_ok, code_version, code_dirty, n_trials_at_end "
        "FROM sweep_runs WHERE sweep_run_id = ?",
        [run_id],
    ).fetchone()
    assert opened == (sweep.sweep_id, None, None, "unknown", None, None)
    lab_registry.close_sweep_run(
        lab_store,
        run_id,
        n_ok=1,
        n_failed=1,
        n_terminal_failed=0,
        seconds=3.5,
        n_trials_at_end=7,
        sr_star_annual_at_end=0.42,
        completed=False,
    )
    closed = lab_store.execute(
        "SELECT finished_at IS NOT NULL, n_ok, n_failed, n_terminal_failed, seconds, "
        "n_trials_at_end, sr_star_annual_at_end, completed FROM sweep_runs "
        "WHERE sweep_run_id = ?",
        [run_id],
    ).fetchone()
    assert closed == (True, 1, 1, 0, 3.5, 7, 0.42, False)
    with pytest.raises(lab_registry.LabRegistryError, match="already closed"):
        lab_registry.close_sweep_run(
            lab_store,
            run_id,
            n_ok=9,
            n_failed=0,
            n_terminal_failed=0,
            seconds=1.0,
            n_trials_at_end=9,
            sr_star_annual_at_end=9.0,
            completed=True,
        )
    assert lab_store.execute("SELECT n_ok FROM sweep_runs").fetchall() == [(1,)]
    with pytest.raises(lab_registry.LabRegistryError, match="no sweep has id"):
        _run(lab_store, 99, tmp_path, None)


def test_write_sweep_trial_needs_an_open_run_and_a_trial(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings)
    variant = _variant(lab_store, settings, sweep, 1, "a" * 64)
    trial_id = _trial(lab_store, settings, tmp_path, variant.hypothesis_id)
    run_id = _run(lab_store, sweep.sweep_id, tmp_path, None)
    lab_registry.write_sweep_trial(
        lab_store, sweep_run_id=run_id, trial_id=trial_id, read_group_index=0, seconds=2.0
    )
    assert lab_store.execute("SELECT * FROM sweep_trials").fetchall() == [
        (run_id, trial_id, 0, 2.0)
    ]
    with pytest.raises(lab_registry.LabRegistryError, match="no trial"):
        lab_registry.write_sweep_trial(
            lab_store, sweep_run_id=run_id, trial_id=999, read_group_index=0, seconds=2.0
        )
    closed = _run(lab_store, sweep.sweep_id, tmp_path, 0.3)
    with pytest.raises(lab_registry.LabRegistryError, match="already closed"):
        lab_registry.write_sweep_trial(
            lab_store, sweep_run_id=closed, trial_id=trial_id, read_group_index=0, seconds=2.0
        )


# --- registration-time reads ---------------------------------------------------


def test_first_session_is_the_minimum_bar_session(
    lab_store: duckdb.DuckDBPyConnection,
) -> None:
    (expected,) = lab_store.execute("SELECT MIN(session) FROM prices_daily").fetchone()  # type: ignore[misc]
    assert expected is not None
    assert lab_registry.first_session(lab_store) == expected
    lab_store.execute(
        "INSERT INTO prices_daily SELECT security_id, DATE '2016-01-04', open, high, low, "
        "close, volume, known_at, ingested_at, source, provenance FROM prices_daily LIMIT 1"
    )
    assert lab_registry.first_session(lab_store) == date(2016, 1, 4)


def test_first_session_is_none_without_bars() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    lab_schema.apply_lab_schema(conn)
    assert lab_registry.first_session(conn) is None


def test_high_water_mark_is_the_maximum_of_runs_parent_and_today_with_the_v_floor(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _register(lab_store, settings)
    floor = settings.lab.min_sharpe_variance_annual
    _rules(lab_store, settings, h1.hypothesis_id, seed=0.25)
    mark = lab_registry.family_sr_star_high_water_mark
    # Today's value with V below the floor is computed at the floor.
    today = expected_max_sharpe(50, floor)
    assert today > 0.25
    assert mark(lab_store, "momentum", n_trials_today=50, sharpe_variance_annual_today=0.0001) == (
        pytest.approx(today)
    )
    assert mark(lab_store, "momentum", n_trials_today=50, sharpe_variance_annual_today=None) == (
        pytest.approx(today)
    )
    # Above the floor, today's V is used as given.
    assert mark(lab_store, "momentum", n_trials_today=50, sharpe_variance_annual_today=1.0) == (
        pytest.approx(expected_max_sharpe(50, 1.0))
    )
    # The parent's seed wins over a small today.
    assert mark(lab_store, "momentum", n_trials_today=2, sharpe_variance_annual_today=None) == 0.25
    assert mark(lab_store, "momentum", n_trials_today=0, sharpe_variance_annual_today=None) == 0.25
    # Every sweep_runs row of every sweep in the family; open rows and other families ignored.
    first = _sweep(lab_store, settings, "a")
    second = _sweep(lab_store, settings, "b")
    _run(lab_store, first.sweep_id, tmp_path, 0.9)
    _run(lab_store, second.sweep_id, tmp_path, 1.7)
    _run(lab_store, second.sweep_id, tmp_path, 1.1)
    _run(lab_store, second.sweep_id, tmp_path, None)
    other = _register(lab_store, settings, "b3", family="profitability")
    _rules(lab_store, settings, other.hypothesis_id, family="profitability")
    other_sweep = _sweep(lab_store, settings, "c", family="profitability")
    _run(lab_store, other_sweep.sweep_id, tmp_path, 5.0)
    assert mark(lab_store, "momentum", n_trials_today=2, sharpe_variance_annual_today=None) == 1.7
    for bad in (float("nan"), float("inf")):
        with pytest.raises(lab_registry.LabRegistryError, match="not finite"):
            mark(lab_store, "momentum", n_trials_today=2, sharpe_variance_annual_today=bad)
    with pytest.raises(lab_registry.LabRegistryError, match="no family rules"):
        mark(lab_store, "oracle", n_trials_today=2, sharpe_variance_annual_today=None)


def test_family_holdout_spent_or_capped(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _register(lab_store, settings)
    assert not lab_registry.family_holdout_spent_or_capped(lab_store, "momentum")
    _rules(lab_store, settings, h1.hypothesis_id)
    assert not lab_registry.family_holdout_spent_or_capped(lab_store, "momentum")
    _trial(
        lab_store,
        settings,
        tmp_path,
        h1.hypothesis_id,
        status="refused_holdout",
        kind="holdout",
        window=(HOLDOUT_START, HOLDOUT_END),
    )
    assert lab_registry.family_holdout_spent_or_capped(lab_store, "momentum")
    assert not lab_registry.family_holdout_spent_or_capped(lab_store, "profitability")


def test_family_declared_count_counts_distinct_fingerprints_of_the_family(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    assert lab_registry.family_declared_count(lab_store, "momentum") == 0
    first = _sweep(lab_store, settings, "a")
    _variant(lab_store, settings, first, 1, "a" * 64)
    _variant(lab_store, settings, first, 2, "b" * 64)
    second = _sweep(lab_store, settings, "b")
    _variant(lab_store, settings, second, 1, "b" * 64)
    _variant(lab_store, settings, second, 2, "c" * 64)
    other = _sweep(lab_store, settings, "c", family="profitability")
    lab_store.execute(
        "INSERT INTO sweep_variants VALUES (?, 1, 1, ?, '{}')", [other.sweep_id, "d" * 64]
    )
    assert lab_registry.family_declared_count(lab_store, "momentum") == 3
    assert lab_registry.family_declared_count(lab_store, "profitability") == 1


def test_family_ready_for_sweep_false_before_and_true_after_the_twins_first_ok_trial(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")
    twin = _register(lab_store, settings)
    mark_pre_lab(lab_store, twin.hypothesis_id)
    # A variant of a sweep is not standalone and never blocks.
    sweep = _sweep(lab_store, settings)
    _variant(lab_store, settings, sweep, 1, "a" * 64)
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    # None of these is the twin's ok, non-synthetic, in-sample trial over its default window.
    _trial(lab_store, settings, tmp_path, twin.hypothesis_id, status="failed")
    _trial(lab_store, settings, tmp_path, twin.hypothesis_id, synthetic=True)
    for window in ((date(2021, 1, 29), DEFAULT_END), (IN_SAMPLE_START, date(2023, 11, 30))):
        _trial(lab_store, settings, tmp_path, twin.hypothesis_id, window=window)
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    _trial(lab_store, settings, tmp_path, twin.hypothesis_id)
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")
    # An older registration of a re-registered slug is not read: only the latest row
    # (the one `backtest h1` runs) needs its first ok trial.
    newer = _register(lab_store, settings, "h1", doc_sha256="e" * 64)
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    _trial(lab_store, settings, tmp_path, newer.hypothesis_id)
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")
    # A second standalone (a promoted file) blocks again until its own first ok trial.
    promoted = _register(lab_store, settings, "promoted")
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    _trial(lab_store, settings, tmp_path, promoted.hypothesis_id)
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")


def test_family_ready_for_sweep_reads_only_the_latest_registration_of_a_slug(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(lab_store, settings)
    newer = _register(lab_store, settings, "h1", doc_sha256="e" * 64)
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    _trial(lab_store, settings, tmp_path, newer.hypothesis_id)
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")


def test_family_ready_for_sweep_reads_the_default_window_under_the_boundary(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """ADR 0016 point 2 (plan T142b): the twin's default window ends at the development
    boundary, read from the store unless the caller passes it."""
    twin = _register(lab_store, settings)
    _trial(lab_store, settings, tmp_path, twin.hypothesis_id)
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")
    moved = date(2022, 12, 30)
    registry.write_development_boundary(lab_store, boundary=moved, reason="test")
    assert not lab_registry.family_ready_for_sweep(lab_store, "momentum")
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum", None)
    _trial(lab_store, settings, tmp_path, twin.hypothesis_id, window=(IN_SAMPLE_START, moved))
    assert lab_registry.family_ready_for_sweep(lab_store, "momentum")


def test_is_pre_lab_by_the_marker_only(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    marked = _register(lab_store, settings, "h1")
    # Same params, earlier registration order or not: only the marker row decides.
    unmarked = _register(lab_store, settings, "h1", doc_sha256="e" * 64)
    mark_pre_lab(lab_store, marked.hypothesis_id)
    assert lab_registry.is_pre_lab(lab_store, marked.hypothesis_id)
    assert not lab_registry.is_pre_lab(lab_store, unmarked.hypothesis_id)
    assert not lab_registry.is_pre_lab(lab_store, 999)


def test_promotion_for_by_decision_row(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    promoted = _register(lab_store, settings, "promoted")
    other = _register(lab_store, settings, "other")
    assert lab_registry.promotion_for(lab_store, promoted.hypothesis_id) is None
    _decision(lab_store, "holdout_spend", promoted.hypothesis_id, {})
    _decision(lab_store, "sweep_retired", promoted.hypothesis_id, {})
    assert lab_registry.promotion_for(lab_store, promoted.hypothesis_id) is None
    decision_id = _decision(lab_store, "promotion", promoted.hypothesis_id, {"sweep_id": 1})
    _decision(lab_store, "promotion", promoted.hypothesis_id, {"sweep_id": 2})
    found = lab_registry.promotion_for(lab_store, promoted.hypothesis_id)
    assert found is not None
    assert (found.decision_id, found.hypothesis_id, found.values) == (
        decision_id,
        promoted.hypothesis_id,
        {"sweep_id": 1},
    )
    assert lab_registry.promotion_for(lab_store, other.hypothesis_id) is None


def test_grandfathered_fingerprints_lists_pre_lab_duplicates_only(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _register(lab_store, settings, "h1")
    second = _register(lab_store, settings, "h1", doc_sha256="e" * 64)
    single = _register(lab_store, settings, "single")
    promoted = _register(lab_store, settings, "promoted")
    for record, fingerprint in (
        (first, "f" * 64),
        (second, "f" * 64),
        (single, "a" * 64),
        (promoted, "a" * 64),
    ):
        lab_registry.write_fingerprint(lab_store, record.hypothesis_id, fingerprint)
    for record in (first, second, single):
        mark_pre_lab(lab_store, record.hypothesis_id)
    assert lab_registry.grandfathered_fingerprints(lab_store) == {
        "f" * 64: (first.hypothesis_id, second.hypothesis_id)
    }


def test_grandfathered_members_lists_pre_lab_members_off_the_rules(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    h1 = _register(lab_store, settings, "h1")
    _rules(lab_store, settings, h1.hypothesis_id)
    cost, top_n = "costs.per_side_bps", "universe.top_n_by_cap"
    dearer = _register(lab_store, settings, "dearer", params=_params(**{cost: 20.0}))
    cheaper = _register(lab_store, settings, "cheaper", params=_params(**{cost: 10.0}))
    wider = _register(lab_store, settings, "wider", params=_params(**{top_n: 1000}))
    moved = _register(lab_store, settings, "moved", holdout_start=date(2024, 2, 1))
    after_lab = _register(lab_store, settings, "after", params=_params(**{top_n: 9}))
    capital = "backtest.initial_capital"
    extra = _register(lab_store, settings, "extra", params=_params(**{capital: 1.0}))
    for record in (h1, dearer, cheaper, wider, moved, extra):
        mark_pre_lab(lab_store, record.hypothesis_id)
    members = lab_registry.grandfathered_members(lab_store)
    assert [(m.slug, m.differences) for m in members] == [
        ("cheaper", ("costs.per_side_bps",)),
        ("wider", ("universe.top_n_by_cap",)),
        ("moved", ("holdout_start",)),
        ("extra", ("backtest.initial_capital",)),
    ]
    assert after_lab.hypothesis_id not in {m.hypothesis_id for m in members}


def test_registry_size_bytes_is_the_store_file_and_its_wal(tmp_path: Path) -> None:
    path = tmp_path / "lab.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    lab_schema.apply_lab_schema(conn)
    conn.execute("CHECKPOINT")
    wal = Path(f"{path}.wal")
    expected = path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    assert lab_registry.registry_size_bytes(conn) == expected > 0
    conn.close()


def test_registry_size_bytes_on_an_in_memory_store(lab_store: duckdb.DuckDBPyConnection) -> None:
    assert lab_registry.registry_size_bytes(lab_store) >= 0


# --- the lab is not initialised -------------------------------------------------

_DAY = date(2024, 1, 2)
_CALLS: dict[str, Callable[[duckdb.DuckDBPyConnection], object]] = {
    "fingerprint_registered": lambda c: lab_registry.fingerprint_registered(c, "f"),
    "write_fingerprint": lambda c: lab_registry.write_fingerprint(c, 1, "f"),
    "family_rules": lambda c: lab_registry.family_rules(c, "momentum"),
    "write_family_rules": lambda c: lab_registry.write_family_rules(
        c,
        family="momentum",
        first_hypothesis_id=1,
        parent_family=None,
        holdout_start=_DAY,
        holdout_end=_DAY,
        in_sample_start=_DAY,
        fixed_params={},
        sr_star_seed_annual=None,
    ),
    "register_sweep": lambda c: lab_registry.register_sweep(
        c,
        slug="s",
        family="momentum",
        title="t",
        doc_path="p",
        doc_sha256="d",
        grid={},
        n_variants=1,
        selection_statistic="dsr_excess",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(0.0, 1.0),
        promote_at_least=0.6,
        retire_below=0.1,
        in_sample_start=_DAY,
        holdout_start=_DAY,
        holdout_end=_DAY,
        registered_by="o",
    ),
    "write_sweep_variant": lambda c: lab_registry.write_sweep_variant(
        c, sweep_id=1, variant_index=1, hypothesis_id=1, fingerprint="f", variant_params={}
    ),
    "sweep_by_slug": lambda c: lab_registry.sweep_by_slug(c, "s"),
    "sweep_variants": lambda c: lab_registry.sweep_variants(c, 1),
    "open_sweep_run": lambda c: lab_registry.open_sweep_run(
        c,
        sweep_id=1,
        time_budget_minutes=1,
        n_declared=1,
        n_planned=1,
        code_tree_sha256="c",
        run_by="r",
    ),
    "close_sweep_run": lambda c: lab_registry.close_sweep_run(
        c,
        1,
        n_ok=0,
        n_failed=0,
        n_terminal_failed=0,
        seconds=0.0,
        n_trials_at_end=0,
        sr_star_annual_at_end=0.0,
        completed=False,
    ),
    "write_sweep_trial": lambda c: lab_registry.write_sweep_trial(
        c, sweep_run_id=1, trial_id=1, read_group_index=0, seconds=0.0
    ),
    "first_session": lab_registry.first_session,
    "family_sr_star_high_water_mark": lambda c: lab_registry.family_sr_star_high_water_mark(
        c, "momentum", n_trials_today=1, sharpe_variance_annual_today=None
    ),
    "family_holdout_spent_or_capped": lambda c: lab_registry.family_holdout_spent_or_capped(
        c, "momentum"
    ),
    "family_declared_count": lambda c: lab_registry.family_declared_count(c, "momentum"),
    "family_ready_for_sweep": lambda c: lab_registry.family_ready_for_sweep(c, "momentum"),
    "is_pre_lab": lambda c: lab_registry.is_pre_lab(c, 1),
    "promotion_for": lambda c: lab_registry.promotion_for(c, 1),
    "grandfathered_fingerprints": lab_registry.grandfathered_fingerprints,
    "grandfathered_members": lab_registry.grandfathered_members,
    "registry_size_bytes": lab_registry.registry_size_bytes,
}


def _public_functions() -> set[str]:
    return {
        name
        for name, value in vars(lab_registry).items()
        if inspect.isfunction(value)
        and value.__module__ == lab_registry.__name__
        and not name.startswith("_")
    }


def test_every_public_function_is_covered_by_the_not_initialised_case() -> None:
    assert set(_CALLS) == _public_functions()


@pytest.mark.parametrize("name", sorted(_CALLS))
def test_every_function_raises_lab_not_initialised_on_a_plain_fixture_store(
    fixture_store: duckdb.DuckDBPyConnection, name: str
) -> None:
    with pytest.raises(lab_schema.LabNotInitialised):
        _CALLS[name](fixture_store)


# --- module text ----------------------------------------------------------------


def test_module_text_is_append_only() -> None:
    """No `DROP`, `DELETE`, `TRUNCATE`, `VACUUM` or `ALTER`; the one `UPDATE`
    fills a `sweep_runs` row still open (`finished_at IS NULL`), inside
    `close_sweep_run`."""
    text = inspect.getsource(lab_registry)
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|VACUUM|ALTER)\b", text)
    updates = re.findall(r"\bUPDATE\b[^\"]*", text, flags=re.IGNORECASE)
    assert updates == ["UPDATE sweep_runs SET finished_at = ?, n_ok = ?, n_failed = ?, "]
    assert "WHERE sweep_run_id = ? AND finished_at IS NULL" in inspect.getsource(
        lab_registry.close_sweep_run
    )
    assert "UPDATE" not in "".join(
        inspect.getsource(getattr(lab_registry, name))
        for name in _public_functions() - {"close_sweep_run"}
    )


def _writers_of(table: str) -> set[str]:
    """Functions of the module that insert into `table`: an `insert_row(conn,
    "<table>", ...)` call or an `INSERT INTO <table>` string."""
    tree = ast.parse(inspect.getsource(lab_registry))
    insert = re.compile(rf"\bINSERT\s+INTO\s+{table}\b", flags=re.IGNORECASE)
    writers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            named = (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "insert_row"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == table
            )
            literal = (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and insert.search(node.value) is not None
            )
            if named or literal:
                writers.add(function.name)
    return writers


def test_only_open_sweep_run_inserts_into_sweep_runs() -> None:
    assert _writers_of("sweep_runs") == {"open_sweep_run"}
    # The scan sees the module's other writers, so an empty answer would fail.
    assert _writers_of("sweep_trials") == {"write_sweep_trial"}
    assert _writers_of("family_rules") == {"write_family_rules"}

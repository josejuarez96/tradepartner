"""Tests for the sweep-run readers (plan T291, `store/lab_registry.py`):
`sweep_runs_after` (runs above a baseline, one registration only), `open_sweep_runs`
(every run with no `finished_at`) and `sweep_run_trials` (a run's trials with their
stored outcome). Hand-inserted rows on the `lab_store` fixture; every reader is
read-only and raises `LabNotInitialised` on a store without the lab tables.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import lab_registry, registry
from tradepartner.store.lab_schema import LabNotInitialised

_FIXED = {
    "costs.per_side_bps": 15.0,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}


def _sweep(conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str) -> Any:
    return lab_registry.register_sweep(
        conn,
        slug=slug,
        family="momentum",
        title=slug,
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.1, 0.2]},
        n_variants=2,
        selection_statistic="sharpe_annual_excess_spy",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(-2.0, 2.0),
        promote_at_least=0.5,
        retire_below=0.0,
        in_sample_start=date(2020, 8, 31),
        holdout_start=date(2024, 1, 2),
        holdout_end=date(2026, 9, 30),
        registered_by="owner",
        settings=settings,
    )


def _run(conn: duckdb.DuckDBPyConnection, sweep_id: int, tmp_path: Path) -> int:
    return lab_registry.open_sweep_run(
        conn,
        sweep_id=sweep_id,
        time_budget_minutes=60,
        n_declared=2,
        n_planned=2,
        code_tree_sha256="c" * 64,
        run_by="test",
        repo_dir=tmp_path,
    )


def _close(conn: duckdb.DuckDBPyConnection, run_id: int) -> None:
    lab_registry.close_sweep_run(
        conn,
        run_id,
        n_ok=2,
        n_failed=0,
        n_terminal_failed=0,
        seconds=1.0,
        n_trials_at_end=2,
        sr_star_annual_at_end=0.1,
        completed=True,
    )


def test_sweep_runs_after_returns_only_newer_runs_of_that_registration(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    a = _sweep(lab_store, settings, "grid-a")
    b = _sweep(lab_store, settings, "grid-b")
    first = _run(lab_store, a.sweep_id, tmp_path)
    other = _run(lab_store, b.sweep_id, tmp_path)
    second = _run(lab_store, a.sweep_id, tmp_path)
    _close(lab_store, first)

    assert [r.sweep_run_id for r in lab_registry.sweep_runs_after(lab_store, a.sweep_id, 0)] == [
        first,
        second,
    ]
    assert [
        r.sweep_run_id for r in lab_registry.sweep_runs_after(lab_store, a.sweep_id, first)
    ] == [second]
    assert lab_registry.sweep_runs_after(lab_store, a.sweep_id, second) == []
    assert [r.sweep_run_id for r in lab_registry.sweep_runs_after(lab_store, b.sweep_id, 0)] == [
        other
    ]
    row = lab_registry.sweep_runs_after(lab_store, a.sweep_id, 0)[0]
    assert (row.n_planned, row.n_ok, row.completed, row.run_by) == (2, 2, True, "test")


def test_open_sweep_runs_lists_only_runs_without_an_end(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    a = _sweep(lab_store, settings, "grid-a")
    closed = _run(lab_store, a.sweep_id, tmp_path)
    open_ = _run(lab_store, a.sweep_id, tmp_path)
    _close(lab_store, closed)

    rows = lab_registry.open_sweep_runs(lab_store)

    assert [r.sweep_run_id for r in rows] == [open_]
    assert rows[0].finished_at is None and rows[0].n_ok is None


def test_readers_write_nothing(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    a = _sweep(lab_store, settings, "grid-a")
    _run(lab_store, a.sweep_id, tmp_path)
    before = lab_store.execute("SELECT COUNT(*) FROM sweep_runs").fetchone()
    lab_registry.sweep_runs_after(lab_store, a.sweep_id, 0)
    lab_registry.open_sweep_runs(lab_store)
    lab_registry.sweep_run_trials(lab_store, 1)
    assert lab_store.execute("SELECT COUNT(*) FROM sweep_runs").fetchone() == before


def test_sweep_run_trials_carry_the_stored_outcome(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, "grid-a")
    run_id = _run(lab_store, sweep.sweep_id, tmp_path)
    handles = []
    for index, fraction in ((1, 0.1), (2, 0.2)):
        record = registry.register_hypothesis(
            lab_store,
            slug=f"grid-a--v{index}",
            family="momentum",
            title="v",
            doc_path="docs/sweeps/grid-a.md",
            doc_sha256="d" * 64,
            params={**_FIXED, "strategy.top_fraction": fraction},
            in_sample_start=date(2020, 8, 31),
            holdout_start=date(2024, 1, 2),
            holdout_end=date(2026, 9, 30),
            registered_by="owner",
            settings=settings,
        )
        lab_registry.write_sweep_variant(
            lab_store,
            sweep_id=sweep.sweep_id,
            variant_index=index,
            hypothesis_id=record.hypothesis_id,
            fingerprint=f"{index:064x}",
            variant_params={"strategy.top_fraction": fraction},
        )
        handle = registry.open_trial(
            lab_store,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            start_session=date(2020, 8, 31),
            end_session=date(2023, 12, 29),
            data_cutoff=None,
            synthetic=False,
            run_by="test",
            settings=settings,
            repo_dir=tmp_path,
        )
        lab_registry.write_sweep_trial(
            lab_store,
            sweep_run_id=run_id,
            trial_id=handle.trial_id,
            read_group_index=1,
            seconds=1.0,
        )
        handles.append(handle)
    registry.close_trial(lab_store, handles[0], "failed", "boom")

    got = lab_registry.sweep_run_trials(lab_store, run_id)

    assert [(t.variant_index, t.slug, t.status, t.message) for t in got] == [
        (1, "grid-a--v1", "failed", "boom"),
        (2, "grid-a--v2", "unfinished", None),
    ]


def test_every_reader_raises_on_a_store_without_the_lab_tables(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    for call in (
        lambda: lab_registry.sweep_runs_after(fixture_store, 1, 0),
        lambda: lab_registry.open_sweep_runs(fixture_store),
        lambda: lab_registry.sweep_run_trials(fixture_store, 1),
    ):
        with pytest.raises(LabNotInitialised):
            call()

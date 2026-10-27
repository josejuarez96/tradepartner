"""Tests for `results.next_trial_cost` (plan T292; ADR 0018 amendment B, #2304).

Hand-inserted trials on the `lab_store` fixture, each its own (parameter set, window)
pair, so N and V are set exactly. The SR* are checked against `expected_max_sharpe`
called directly and against `run_sweep`'s close arithmetic recorded on `sweep_runs`.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.backtest import metrics, results
from tradepartner.config import Settings
from tradepartner.store import lab_registry, registry
from tradepartner.store.db import insert_row, utc_now

IN_SAMPLE_START = date(2020, 8, 31)
HOLDOUT_START = date(2024, 1, 2)
HOLDOUT_END = date(2026, 9, 30)
CUTOFF = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
BASE = 15.0
PPY = 12.0
FIXED = {
    "costs.per_side_bps": BASE,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}
FLOOR = 0.04


def _trial(conn: duckdb.DuckDBPyConnection, settings: Settings, index: int, annual: float) -> None:
    """One `ok` in-sample trial of `momentum` with annual excess/raw Sharpe `annual`."""
    record = registry.register_hypothesis(
        conn,
        slug=f"t{index}",
        family="momentum",
        title=f"t{index}",
        doc_path=f"docs/hypotheses/t{index}.md",
        doc_sha256="d" * 64,
        params={**FIXED, "strategy.top_fraction": 0.1 + index / 100},
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )
    (trial_id,) = conn.execute("SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials").fetchone()  # type: ignore[misc]
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": record.hypothesis_id,
            "kind": "in_sample",
            "started_at": utc_now(),
            "start_session": IN_SAMPLE_START,
            "end_session": date(2023, 12, 29),
            "data_cutoff": CUTOFF,
            "store_max_ingested_at": None,
            "code_version": "abc",
            "code_dirty": False,
            "synthetic": False,
            "holdout_repeat": False,
            "run_by": "test",
            "detail_level": "summary",
            "data_vintage": registry.data_vintage(conn, CUTOFF),
            "code_tree_sha256": "c" * 64,
        },
    )
    insert_row(
        conn,
        "trial_results",
        {"trial_id": trial_id, "finished_at": utc_now(), "status": "ok", "sharpe_unit": "annual"},
    )
    row: dict[str, Any] = {"series": "strategy", "cost_per_side_bps": BASE, "trial_id": trial_id}
    for metric, value in (
        ("sharpe_period", annual / math.sqrt(PPY)),
        ("sharpe_period_excess_spy", annual / math.sqrt(PPY) * 0.5),
        ("periods_per_year", PPY),
    ):
        insert_row(conn, "trial_metrics", {**row, "metric": metric, "value": value})


def _family(conn: duckdb.DuckDBPyConnection, settings: Settings, sharpes: list[float]) -> None:
    for index, annual in enumerate(sharpes, start=1):
        _trial(conn, settings, index, annual)


def test_unfloored_equals_expected_max_sharpe_called_directly_per_basis(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sharpes = [0.2, 0.9, 0.5, 1.4]
    _family(lab_store, settings, sharpes)
    for basis, scale in (("raw", 1.0), ("excess_spy", 0.5)):
        v = statistics.variance([s * scale for s in sharpes])
        cost = results.next_trial_cost(lab_store, "momentum", basis=basis)  # type: ignore[arg-type]
        assert (cost.n_now, cost.n_next, cost.basis, cost.floored) == (4, 5, basis, False)
        assert cost.sr_star_now == pytest.approx(metrics.expected_max_sharpe(4, v))
        assert cost.sr_star_next == pytest.approx(metrics.expected_max_sharpe(5, v))
        assert cost.sr_star_next > cost.sr_star_now
        assert cost.note is None


def test_added_moves_n_next_and_the_next_sr_star(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _family(lab_store, settings, [0.2, 0.9, 0.5])
    v = statistics.variance([0.1, 0.45, 0.25])
    cost = results.next_trial_cost(lab_store, "momentum", basis="excess_spy", added=7)
    assert (cost.n_now, cost.n_next) == (3, 10)
    assert cost.sr_star_next == pytest.approx(metrics.expected_max_sharpe(10, v))


def test_floor_lifts_a_small_v_as_the_sweep_close_does(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _family(lab_store, settings, [0.5, 0.52, 0.51])
    v = statistics.variance([0.25, 0.26, 0.255])
    assert v < FLOOR
    cost = results.next_trial_cost(lab_store, "momentum", basis="excess_spy", floor=FLOOR)
    assert cost.floored is True
    assert cost.variance_used == FLOOR
    assert cost.sr_star_now == pytest.approx(metrics.expected_max_sharpe(3, FLOOR))
    # A floor below V changes nothing and is not reported as applied.
    low = results.next_trial_cost(lab_store, "momentum", basis="excess_spy", floor=v / 10)
    assert low.floored is False
    assert low.sr_star_now == pytest.approx(metrics.expected_max_sharpe(3, v))


def test_sr_star_now_with_family_floor_equals_last_closed_sweep_run_at_todays_n(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _family(lab_store, settings, [0.5, 0.52, 0.51])
    first = lab_store.execute("SELECT MIN(hypothesis_id) FROM hypotheses").fetchone()[0]  # type: ignore[index]
    rules = lab_registry.write_family_rules(
        lab_store,
        family="momentum",
        first_hypothesis_id=first,
        parent_family=None,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        in_sample_start=IN_SAMPLE_START,
        fixed_params=FIXED,
        sr_star_seed_annual=None,
        settings=settings,
    )
    # What lab._close_run records at today's N (3) with V floored.
    recorded = metrics.expected_max_sharpe(3, rules.min_sharpe_variance_annual)
    sweep = lab_registry.register_sweep(
        lab_store,
        slug="g",
        family="momentum",
        title="g",
        doc_path="docs/sweeps/g.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.15]},
        n_variants=1,
        selection_statistic="sharpe_annual_excess_spy",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(-2.0, 2.0),
        promote_at_least=0.5,
        retire_below=0.0,
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )
    run_id = lab_registry.open_sweep_run(
        lab_store,
        sweep_id=sweep.sweep_id,
        time_budget_minutes=480,
        n_declared=1,
        n_planned=1,
        code_tree_sha256="c" * 64,
        run_by="test",
    )
    lab_registry.close_sweep_run(
        lab_store,
        run_id,
        n_ok=1,
        n_failed=0,
        n_terminal_failed=0,
        seconds=1.0,
        n_trials_at_end=3,
        sr_star_annual_at_end=recorded,
        completed=True,
    )
    (stored,) = lab_store.execute("SELECT sr_star_annual_at_end FROM sweep_runs").fetchone()  # type: ignore[misc]
    cost = results.next_trial_cost(
        lab_store, "momentum", basis="excess_spy", floor=rules.min_sharpe_variance_annual
    )
    assert cost.n_now == 3
    assert cost.sr_star_now == pytest.approx(stored)


def test_unknown_v_without_a_floor_is_zero_and_says_so(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _family(lab_store, settings, [0.7])
    cost = results.next_trial_cost(lab_store, "momentum", basis="raw")
    assert (cost.n_now, cost.n_next) == (1, 2)
    assert (cost.sr_star_now, cost.sr_star_next) == (0.0, 0.0)
    assert cost.variance_used is None
    assert cost.note is not None and "unknown" in cost.note
    # With a floor, the floor is V and SR* of two trials is above zero.
    floored = results.next_trial_cost(lab_store, "momentum", basis="raw", floor=FLOOR)
    assert floored.floored is True
    assert floored.sr_star_now == 0.0
    assert floored.sr_star_next == pytest.approx(metrics.expected_max_sharpe(2, FLOOR))


def test_a_family_with_no_counted_trial_never_calls_expected_max_sharpe_below_one(
    lab_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    real = metrics.expected_max_sharpe

    def spy(n: int, v: float) -> float:
        seen.append(n)
        return real(n, v)

    monkeypatch.setattr(results, "expected_max_sharpe", spy)
    cost = results.next_trial_cost(lab_store, "momentum", basis="excess_spy", floor=FLOOR)
    assert (cost.n_now, cost.n_next, cost.sr_star_now) == (0, 1, 0.0)
    assert cost.sr_star_next == 0.0  # one trial: nothing to deflate
    assert seen and min(seen) >= 1


def test_negative_added_or_floor_refused(lab_store: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="added"):
        results.next_trial_cost(lab_store, "momentum", basis="raw", added=-1)
    with pytest.raises(ValueError, match="floor"):
        results.next_trial_cost(lab_store, "momentum", basis="raw", floor=-0.1)
    with pytest.raises(ValueError, match="floor"):
        results.next_trial_cost(lab_store, "momentum", basis="raw", floor=float("nan"))


def test_read_only(lab_store: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    _family(lab_store, settings, [0.2, 0.9])
    tables = ("trials", "trial_results", "trial_metrics", "hypotheses", "sweep_runs")

    def counts() -> list[int]:
        return [lab_store.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables]  # type: ignore[index]

    before = counts()
    results.next_trial_cost(lab_store, "momentum", basis="raw")
    assert counts() == before

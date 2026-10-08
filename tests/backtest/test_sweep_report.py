"""Tests for the sweep report and `lab status` (strategy-lab plan T108,
`backtest/sweep_report.py`; spec req 3, the `sweep report` and `lab status` criteria).

Every case runs on hand-inserted rows on the `lab_store` fixture: hypotheses, family
rules, a sweep and its variants through `registry`/`lab_registry`, and trials, results
and base-level metrics inserted as rows, so each variant's state (counted, stale,
terminal-failed, failed, unrun) is set exactly. The checkout's code vintage is passed
in as `CODE`.
"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pytest
from conftest import mark_pre_lab

from tradepartner.backtest import results, sweep_report
from tradepartner.backtest.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
)
from tradepartner.config import Settings
from tradepartner.store import lab_queries, lab_registry, registry
from tradepartner.store.db import insert_row, utc_now
from tradepartner.store.lab_schema import LabNotInitialised
from tradepartner.store.schema import REGISTRY_TABLE_NAMES

IN_SAMPLE_START = date(2020, 8, 31)
HOLDOUT_START = date(2024, 1, 2)
HOLDOUT_END = date(2026, 9, 30)
#: The default in-sample window's end at `month_end`.
DEFAULT_END = date(2023, 12, 29)
CUTOFF = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
CODE = "c" * 64
OTHER_CODE = "9" * 64
BASE = 15.0
FIXED = {
    "costs.per_side_bps": BASE,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}
#: The wall-clock bound on the 10,000-trial report (seconds), with ample headroom.
REPORT_SECONDS_LIMIT = 10.0
#: A holdout trial's statistic, which must never appear in the report.
HOLDOUT_SENTINEL = 0.777777


def _params(top_fraction: float) -> dict[str, Any]:
    return {**FIXED, "strategy.top_fraction": top_fraction}


def _register(
    conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str, top_fraction: float
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=slug,
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params=_params(top_fraction),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _metrics(
    *,
    excess_cagr: float,
    sharpe_annual_excess: float,
    sharpe_period_excess: float | None = None,
    sharpe_period: float = 0.2,
) -> dict[str, float]:
    period_excess = (
        sharpe_period_excess
        if sharpe_period_excess is not None
        else sharpe_annual_excess / math.sqrt(12)
    )
    return {
        "excess_cagr_spy": excess_cagr,
        "sharpe_annual_excess_spy": sharpe_annual_excess,
        "sharpe_period_excess_spy": period_excess,
        "skew_period_excess_spy": -0.1,
        "kurtosis_period_excess_spy": 3.5,
        "sharpe_period": sharpe_period,
        "cost_drag": 0.004,
        "turnover_annual": 5.5,
        "max_drawdown": -0.21,
        "n_periods": 40.0,
        "periods_per_year": 12.0,
    }


class Store:
    """Hand-inserted trial rows on a `lab_store`."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn
        self.vintage = registry.data_vintage(conn, CUTOFF)

    def trial(
        self,
        hypothesis_id: int,
        *,
        status: str = "ok",
        message: str | None = None,
        metrics: dict[str, float] | None = None,
        stale: bool = False,
        code: str = CODE,
        dirty: bool = False,
        kind: str = "in_sample",
        red_flag: bool = False,
        stored_dsr_excess: float | None = None,
        run_id: int | None = None,
        seconds: float = 1.0,
    ) -> int:
        conn = self.conn
        (trial_id,) = conn.execute(  # type: ignore[misc]
            "SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials"
        ).fetchone()
        vintage = self.vintage
        if stale:
            vintage = datetime(2000, 1, 1, tzinfo=UTC)
        insert_row(
            conn,
            "trials",
            {
                "trial_id": trial_id,
                "hypothesis_id": hypothesis_id,
                "kind": kind,
                "started_at": utc_now(),
                "start_session": IN_SAMPLE_START if kind == "in_sample" else HOLDOUT_START,
                "end_session": DEFAULT_END if kind == "in_sample" else HOLDOUT_END,
                "data_cutoff": CUTOFF,
                "store_max_ingested_at": None,
                "code_version": "abc",
                "code_dirty": dirty,
                "synthetic": False,
                "holdout_repeat": False,
                "run_by": "test",
                "detail_level": "summary",
                "data_vintage": vintage,
                "code_tree_sha256": code,
            },
        )
        insert_row(
            conn,
            "trial_results",
            {
                "trial_id": trial_id,
                "finished_at": utc_now(),
                "status": status,
                "message": message,
                "red_flag": red_flag if status == "ok" else None,
                "dsr_excess": stored_dsr_excess,
                "sharpe_unit": "annual" if status == "ok" else None,
            },
        )
        for metric, value in (metrics or {}).items():
            insert_row(
                conn,
                "trial_metrics",
                {
                    "trial_id": trial_id,
                    "series": "strategy",
                    "cost_per_side_bps": BASE,
                    "metric": metric,
                    "value": value,
                },
            )
        if run_id is not None:
            lab_registry.write_sweep_trial(
                conn, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=seconds
            )
        return int(trial_id)

    def run(self, sweep: lab_registry.SweepRecord) -> int:
        """An open `sweep_runs` row of `sweep`: a failure counts toward terminal
        failure only as a trial of one of its registration's runs (#1221)."""
        return lab_registry.open_sweep_run(
            self.conn,
            sweep_id=sweep.sweep_id,
            time_budget_minutes=480,
            n_declared=sweep.n_variants,
            n_planned=sweep.n_variants,
            code_tree_sha256=CODE,
            run_by="test",
        )


def _rules(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    first_id: int,
    *,
    parent_family: str | None = None,
) -> lab_registry.FamilyRules:
    return lab_registry.write_family_rules(
        conn,
        family="momentum",
        first_hypothesis_id=first_id,
        parent_family=parent_family,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        in_sample_start=IN_SAMPLE_START,
        fixed_params=FIXED,
        sr_star_seed_annual=None,
        settings=settings,
    )


def _sweep(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    values: list[float],
    *,
    slug: str = "mom-grid",
    statistic: str = "sharpe_annual_excess_spy",
    first_index_fingerprint: int = 0,
    expected_range_pp: tuple[float, float] = (-2.0, 2.0),
) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
    sweep = lab_registry.register_sweep(
        conn,
        slug=slug,
        family="momentum",
        title="top fraction grid",
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": values},
        n_variants=len(values),
        selection_statistic=statistic,
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=expected_range_pp,
        promote_at_least=0.5,
        retire_below=0.0,
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )
    records = []
    for index, value in enumerate(values, start=1):
        record = _register(conn, settings, f"{slug}--r{sweep.sweep_id}-v{index}", value)
        fingerprint = f"{first_index_fingerprint + index:064x}"
        lab_registry.write_sweep_variant(
            conn,
            sweep_id=sweep.sweep_id,
            variant_index=index,
            hypothesis_id=record.hypothesis_id,
            fingerprint=fingerprint,
            variant_params={"strategy.top_fraction": value},
        )
        lab_registry.write_fingerprint(conn, record.hypothesis_id, fingerprint)
        records.append(record)
    return sweep, records


@pytest.fixture
def world(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> tuple[Store, registry.HypothesisRecord]:
    """H1's twin registered with the family rules, one `ok` in-sample trial and one
    holdout trial carrying `HOLDOUT_SENTINEL`."""
    store = Store(lab_store)
    twin = _register(lab_store, settings, "h1", 0.1)
    _rules(lab_store, settings, twin.hypothesis_id, parent_family="profitability")
    store.trial(twin.hypothesis_id, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.3))
    store.trial(
        twin.hypothesis_id,
        kind="holdout",
        metrics=_metrics(excess_cagr=HOLDOUT_SENTINEL, sharpe_annual_excess=HOLDOUT_SENTINEL),
    )
    return store, twin


# The four complete-sweep variants: (excess_cagr_spy, sharpe_annual_excess_spy, red flag).
# Selection statistic sharpe_annual_excess_spy: v2 and v4 tie at the maximum, so the
# argmax is v2 by canonical index. Inside [-2, 2] pp: v1 and v3, a share of 0.5.
COMPLETE = [
    (0.01, 0.4, False),
    (0.025, 0.9, False),
    (-0.015, 0.2, False),
    (0.5, 0.9, True),
]


def _complete_sweep(
    store: Store, settings: Settings
) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
    sweep, records = _sweep(store.conn, settings, [0.15, 0.2, 0.25, 0.3])
    for record, (excess, sharpe, flag) in zip(records, COMPLETE, strict=True):
        store.trial(
            record.hypothesis_id,
            metrics=_metrics(excess_cagr=excess, sharpe_annual_excess=sharpe),
            red_flag=flag,
            stored_dsr_excess=0.999,
        )
    return sweep, records


# --- the incomplete sweep -----------------------------------------------------------


def test_incomplete_sweep_prints_counts_rows_n_and_no_argmax_or_verdict(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    conn = store.conn
    _sweep_record, records = _sweep(conn, settings, [0.15, 0.2, 0.25, 0.3])
    v1, v2, v3, _v4 = records
    store.trial(v1.hypothesis_id, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.4))
    store.trial(v2.hypothesis_id, metrics=_metrics(excess_cagr=0.02, sharpe_annual_excess=0.6))
    store.trial(
        v3.hypothesis_id,
        metrics=_metrics(excess_cagr=0.03, sharpe_annual_excess=0.5),
        stale=True,
    )

    report = sweep_report.sweep_report(conn, "mom-grid", code_vintage=CODE)

    assert report.state == "incomplete (stale)"
    assert not report.complete
    assert (report.n_declared, report.n_run, report.n_counted) == (4, 3, 2)
    assert report.stale == (v3.slug,)
    assert report.terminal_failed == ()
    assert [r.status for r in report.rows] == ["counted", "counted", "stale", "unrun"]
    assert [r.trial_id is not None for r in report.rows] == [True, True, True, False]
    n = results.family_n(conn, "momentum")
    assert report.family_n == n == 4  # the twin's, v1's, v2's and the stale v3's
    sharpes = registry.family_sharpes(conn, "momentum")
    variance = sharpes.variance("excess_spy")
    assert variance is not None
    assert report.sharpe_variance_annual_excess == pytest.approx(variance)
    assert report.sr_star_annual == pytest.approx(expected_max_sharpe(n, variance))
    assert report.declared_count == 4
    # v3 (stale) and v4 (unrun) have no counted trial: N_declared = N + 2.
    assert report.n_at_declared_count == n + 2
    assert report.sr_star_annual_at_declared_count == pytest.approx(
        expected_max_sharpe(n + 2, variance)
    )
    assert report.parent_family == "profitability"
    assert report.parent_n == results.family_n(conn, "profitability") == 0
    assert report.verdicts is None

    text = sweep_report.format_report(report)
    assert "argmax" not in text
    assert "promote_at_least" not in text
    assert "retire_below" not in text
    assert "declared count n 4" in text
    assert f"N at declared count {n + 2}" in text
    assert "parent family profitability: N 0" in text
    assert "state: incomplete (stale)" in text
    assert f"stale (awaiting a rerun): {v3.slug}" in text
    for record in records:
        assert record.slug in text


def test_unrun_sweep_reads_incomplete_unrun_with_an_empty_distribution(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    _sweep(store.conn, settings, [0.15, 0.2])
    report = sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=CODE)
    assert report.state == "incomplete (unrun)"
    assert report.n_run == 0
    assert report.distribution is None
    assert report.dsr_excess_share_above is None
    assert report.verdicts is None


# --- the complete sweep ---------------------------------------------------------------


def test_complete_sweep_prints_argmax_recomputed_dsr_quartiles_shares_and_verdicts(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, twin = world
    conn = store.conn
    sweep, records = _complete_sweep(store, settings)
    # N changes between the run and the report: two more twin trials.
    store.trial(twin.hypothesis_id, metrics=_metrics(excess_cagr=0.0, sharpe_annual_excess=0.1))
    store.trial(twin.hypothesis_id, metrics=_metrics(excess_cagr=0.0, sharpe_annual_excess=0.2))

    report = sweep_report.sweep_report(conn, "mom-grid", code_vintage=CODE)

    assert report.complete
    n = results.family_n(conn, "momentum")
    pairs = registry.family_sharpes(conn, "momentum").excess_spy
    for row, (excess, sharpe, _flag) in zip(report.rows, COMPLETE, strict=True):
        fresh = deflated_sharpe(
            _metrics(excess_cagr=excess, sharpe_annual_excess=sharpe),
            "excess_spy",
            n_trials=n,
            pair_sharpes=pairs,
            periods_per_year=12,
        ).dsr
        assert row.dsr_excess == pytest.approx(fresh)
        assert row.dsr_excess != pytest.approx(0.999)  # never the stored value
        assert (row.cadence, row.signal_anchor) == ("month_end", "month_end")
    statistic = [sharpe for _, sharpe, _ in COMPLETE]
    q = np.percentile(statistic, [0, 25, 50, 75, 100])
    d = report.distribution
    assert d is not None
    assert (d.minimum, d.q1, d.median, d.q3, d.maximum) == pytest.approx(tuple(q))
    above = sum(1 for r in report.rows if r.dsr_excess is not None and r.dsr_excess > 0.5)
    assert report.dsr_excess_share_above == pytest.approx(above / 4)
    assert report.expected_range_share == pytest.approx(0.5)
    assert report.red_flag_count == 1

    verdicts = report.verdicts
    assert verdicts is not None
    assert verdicts.argmax.slug == records[1].slug  # v2 ties v4 and wins by index
    variance = registry.family_sharpes(conn, "momentum").variance("excess_spy")
    mark = lab_registry.family_sr_star_high_water_mark(
        conn, "momentum", n_trials_today=n, sharpe_variance_annual_today=variance
    )
    metrics = _metrics(excess_cagr=0.025, sharpe_annual_excess=0.9)
    expected_dsr = probabilistic_sharpe(
        metrics["sharpe_period_excess_spy"],
        mark / math.sqrt(12),
        40,
        metrics["skew_period_excess_spy"],
        metrics["kurtosis_period_excess_spy"],
    )
    assert verdicts.sr_star_high_water_annual == pytest.approx(mark)
    assert verdicts.dsr_excess_at_high_water == pytest.approx(expected_dsr)
    assert verdicts.promote_at_least_met is (expected_dsr >= sweep.promote_at_least)
    assert verdicts.retire_below_met is False  # 0.9 is not below 0.0

    text = sweep_report.format_report(report)
    assert f"argmax: {records[1].slug} (index 2" in text
    assert "promote_at_least 0.5: " in text
    assert "retire_below 0.0: not met" in text
    assert "red flags: 1" in text
    assert "share with excess_cagr_spy inside expected_range_pp [-2.0, 2.0]: 0.50" in text
    assert "fill close" in text
    assert text.count("month_end  month_end") == 4  # cadence and anchor on every row


def test_retire_below_met_and_promotion_against_the_high_water_mark(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    conn = store.conn
    _sweep_record, records = _sweep(conn, settings, [0.15, 0.2])
    for record in records:
        store.trial(
            record.hypothesis_id,
            metrics=_metrics(excess_cagr=-0.02, sharpe_annual_excess=-0.3),
        )
    # A recorded run with a high SR* mark: today's V alone could not set it.
    run_id = lab_registry.open_sweep_run(
        conn,
        sweep_id=1,
        time_budget_minutes=480,
        n_declared=2,
        n_planned=2,
        code_tree_sha256=CODE,
        run_by="test",
    )
    lab_registry.close_sweep_run(
        conn,
        run_id,
        n_ok=2,
        n_failed=0,
        n_terminal_failed=0,
        seconds=10.0,
        n_trials_at_end=3,
        sr_star_annual_at_end=5.0,
        completed=True,
    )
    report = sweep_report.sweep_report(conn, "mom-grid", code_vintage=CODE)
    verdicts = report.verdicts
    assert verdicts is not None
    assert verdicts.sr_star_high_water_annual == pytest.approx(5.0)
    assert verdicts.retire_below_met is True
    assert verdicts.promote_at_least_met is False
    assert "retire_below 0.0: met" in sweep_report.format_report(report)


def test_an_excess_exactly_at_the_range_bound_is_inside(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    # 0.07 * 100 is 7.000000000000001; the bound is converted to a fraction instead.
    store, _twin = world
    _sweep_record, records = _sweep(store.conn, settings, [0.15], expected_range_pp=(0.0, 7.0))
    store.trial(
        records[0].hypothesis_id, metrics=_metrics(excess_cagr=0.07, sharpe_annual_excess=0.4)
    )
    report = sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=CODE)
    assert report.expected_range_share == 1.0


# --- variant states ---------------------------------------------------------------------


def test_terminal_failed_variants_complete_the_sweep_and_never_select(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    conn = store.conn
    sweep_record, records = _sweep(conn, settings, [0.15, 0.2, 0.25, 0.3, 0.35])
    v1, v2, v3, v4, v5 = (r.hypothesis_id for r in records)
    run = store.run(sweep_record)
    store.trial(v1, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.2), run_id=run)
    # Two identical clean current failures: terminal.
    store.trial(v2, status="failed", message="boom", run_id=run)
    store.trial(v2, status="failed", message="boom", run_id=run)
    # Excluded kinds, a dirty one and another code vintage: not terminal.
    store.trial(v3, status="failed", message=registry.STORE_CHANGED_MESSAGE, run_id=run)
    store.trial(v3, status="failed", message=registry.STORE_CHANGED_MESSAGE, run_id=run)
    store.trial(v3, status="failed", message=lab_queries.SHARED_READ_FAILED, run_id=run)
    store.trial(v4, status="failed", message="boom", dirty=True, run_id=run)
    store.trial(v4, status="failed", message="boom", code=OTHER_CODE, run_id=run)
    # A current ok after two failures: counted.
    store.trial(v5, status="failed", message="boom", run_id=run)
    store.trial(v5, status="failed", message="boom", run_id=run)
    store.trial(v5, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.7), run_id=run)

    report = sweep_report.sweep_report(conn, "mom-grid", code_vintage=CODE)
    assert [r.status for r in report.rows] == [
        "counted",
        "terminal_failed",
        "failed",
        "failed",
        "counted",
    ]
    assert report.terminal_failed == (records[1].slug,)
    assert report.state == "incomplete (unrun)"
    assert "terminal_failed (boom)" in sweep_report.format_report(report)


_REPORT_STATUS = {
    "current": "counted",
    "terminal_failed": "terminal_failed",
    "stale": "stale",
    "unrun": "unrun",
}


@pytest.mark.parametrize("code", [CODE, OTHER_CODE])
def test_report_states_equal_the_run_planners_one_classifier(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings, code: str
) -> None:
    """#1221: the report reads variant state from `lab_queries.variant_states`, so
    its states and the sweep's state equal what the runner plans from, including
    the four cases on which the report's own copy of the rule used to differ."""
    store, _twin = world
    conn = store.conn
    sweep_record, records = _sweep(conn, settings, [0.15, 0.2, 0.25, 0.3, 0.35, 0.4])
    v1, v2, v3, v4, v5, _v6 = (r.hypothesis_id for r in records)
    run = store.run(sweep_record)
    store.trial(v1, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.2), run_id=run)
    store.trial(v2, status="failed", message="boom", run_id=run)
    store.trial(v2, status="failed", message="boom", run_id=run)
    # Two identical failures outside any run of this registration: not terminal.
    store.trial(v3, status="failed", message="boom")
    store.trial(v3, status="failed", message="boom")
    # Two NULL-message failures in its runs: one (empty) message, terminal.
    store.trial(v4, status="failed", message=None, run_id=run)
    store.trial(v4, status="failed", message=None, run_id=run)
    store.trial(v5, metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.3), stale=True)

    report = sweep_report.sweep_report(conn, "mom-grid", code_vintage=code)
    states = lab_queries.variant_states(conn, sweep_record.sweep_id, code_vintage=code)
    planner = lab_queries.sweep_state(conn, sweep_record.sweep_id, code_vintage=code)
    assert report.state == planner.state
    for row, state in zip(report.rows, states, strict=True):
        expected = _REPORT_STATUS[state.state]
        if expected == "unrun" and row.status == "failed":
            continue  # the report's one display state: unrun with failures
        assert row.status == expected, row.slug
    if code == CODE:
        assert [r.status for r in report.rows] == [
            "counted",
            "terminal_failed",
            "failed",
            "terminal_failed",
            "stale",
            "unrun",
        ]


def test_a_code_change_makes_every_trial_stale(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    _complete_sweep(store, settings)
    report = sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=OTHER_CODE)
    assert report.state == "incomplete (stale)"
    assert report.n_counted == 0
    assert report.verdicts is None


def test_all_terminal_failed_is_complete_with_no_argmax(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    sweep_record, records = _sweep(store.conn, settings, [0.15])
    run = store.run(sweep_record)
    store.trial(records[0].hypothesis_id, status="failed", message="boom", run_id=run)
    store.trial(records[0].hypothesis_id, status="failed", message="boom", run_id=run)
    report = sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=CODE)
    assert report.complete
    assert report.verdicts is None
    assert "argmax" not in sweep_report.format_report(report)


def test_the_latest_registration_is_reported(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    _sweep(store.conn, settings, [0.15, 0.2])
    second, _records = _sweep(store.conn, settings, [0.4, 0.45, 0.5], first_index_fingerprint=10)
    report = sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=CODE)
    assert report.sweep_id == second.sweep_id
    assert report.n_declared == 3
    assert report.declared_count == 5  # distinct fingerprints across both registrations


# --- no holdout value ----------------------------------------------------------------------


def test_no_holdout_value_appears_in_the_report(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    _complete_sweep(store, settings)
    text = sweep_report.format_report(
        sweep_report.sweep_report(store.conn, "mom-grid", code_vintage=CODE)
    )
    assert "holdout" not in text.lower()
    assert HOLDOUT_START.isoformat() not in text
    assert HOLDOUT_END.isoformat() not in text
    assert "0.7778" not in text
    assert "77.78" not in text


# --- refusals ---------------------------------------------------------------------------


def test_both_raise_lab_not_initialised_on_a_store_without_the_tables(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    with pytest.raises(LabNotInitialised):
        sweep_report.sweep_report(fixture_store, "mom-grid", code_vintage=CODE)
    with pytest.raises(LabNotInitialised):
        sweep_report.lab_status(fixture_store, settings, code_vintage=CODE)


def test_an_unknown_slug_is_refused(lab_store: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="no sweep is registered as 'absent'"):
        sweep_report.sweep_report(lab_store, "absent", code_vintage=CODE)


# --- lab status --------------------------------------------------------------------------


def test_lab_status_lists_everything_named(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, twin = world
    conn = store.conn
    sweep_record, records = _sweep(conn, settings, [0.15, 0.2])
    run_id = store.run(sweep_record)
    store.trial(
        records[0].hypothesis_id,
        metrics=_metrics(excess_cagr=0.01, sharpe_annual_excess=0.4),
        stale=True,
        run_id=run_id,
        seconds=15.0,
    )
    # A failed trial's near-zero seconds are not a run time (as `lab_queries` reads them).
    store.trial(
        records[1].hypothesis_id, status="failed", message="boom", run_id=run_id, seconds=0.001
    )
    # A grandfathered pair: two pre-lab registrations with one fingerprint, the second
    # with a different in-sample start (a grandfathered member).
    old = registry.register_hypothesis(
        conn,
        slug="h1-old",
        family="momentum",
        title="old",
        doc_path="docs/hypotheses/h1-old.md",
        doc_sha256="e" * 64,
        params=_params(0.1),
        in_sample_start=date(2019, 11, 29),
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )
    lab_registry.write_fingerprint(conn, twin.hypothesis_id, "f" * 64)
    lab_registry.write_fingerprint(conn, old.hypothesis_id, "f" * 64)
    mark_pre_lab(conn, twin.hypothesis_id)
    mark_pre_lab(conn, old.hypothesis_id)
    lab_registry.close_sweep_run(
        conn,
        run_id,
        n_ok=1,
        n_failed=1,
        n_terminal_failed=0,
        seconds=30.0,
        n_trials_at_end=3,
        sr_star_annual_at_end=0.4,
        completed=False,
    )

    status = sweep_report.lab_status(
        conn,
        settings,
        now=datetime(2026, 10, 7, tzinfo=UTC),
        system_tz=ZoneInfo("America/New_York"),
        code_vintage=CODE,
    )

    assert status.store_size_bytes == lab_registry.registry_size_bytes(conn)
    assert set(status.table_rows) == set(REGISTRY_TABLE_NAMES)
    for table in REGISTRY_TABLE_NAMES:
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
        assert status.table_rows[table] == count
    (momentum,) = [f for f in status.families if f.family == "momentum"]
    assert momentum.n == results.family_n(conn, "momentum")
    sharpes = registry.family_sharpes(conn, "momentum")
    assert momentum.sharpe_variance_annual_excess == sharpes.variance("excess_spy")
    assert momentum.sharpe_variance_annual_raw == sharpes.variance("raw")
    assert momentum.declared_count == 2
    assert momentum.rules == lab_registry.family_rules(conn, "momentum")
    assert status.grandfathered_pairs == {"f" * 64: (twin.hypothesis_id, old.hypothesis_id)}
    assert [m.slug for m in status.grandfathered_members] == ["h1-old"]
    (open_sweep,) = status.open_sweeps
    assert (open_sweep.slug, open_sweep.n_declared, open_sweep.n_counted) == ("mom-grid", 2, 0)
    assert (open_sweep.n_stale, open_sweep.state) == (1, "incomplete (stale)")
    (run,) = status.last_runs
    assert run.seconds_per_variant == pytest.approx(15.0)
    assert status.timezone_warning is None

    text = sweep_report.format_lab_status(status)
    for table in REGISTRY_TABLE_NAMES:
        assert f"  {table}: " in text
    assert "momentum: N " in text
    assert "rules: parent profitability" in text
    assert "hypotheses " in text
    assert "h1-old (momentum" in text
    assert "mom-grid (registration 1, momentum): 0 of 2 counted" in text
    assert "seconds per variant 15.0" in text
    assert "warning" not in text


def test_lab_status_warns_when_the_quiet_timezone_is_not_the_system_zone(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    status = sweep_report.lab_status(
        lab_store,
        settings,
        now=datetime(2026, 10, 7, tzinfo=UTC),
        system_tz=ZoneInfo("Europe/London"),
        code_vintage=CODE,
    )
    assert status.timezone_warning is not None
    assert "America/New_York" in status.timezone_warning
    assert "warning: lab.quiet_timezone" in sweep_report.format_lab_status(status)


def test_lab_status_lists_the_last_ten_runs_newest_first(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, _twin = world
    conn = store.conn
    _sweep(conn, settings, [0.15])
    for _ in range(12):
        lab_registry.open_sweep_run(
            conn,
            sweep_id=1,
            time_budget_minutes=480,
            n_declared=1,
            n_planned=1,
            code_tree_sha256=CODE,
            run_by="test",
        )
    status = sweep_report.lab_status(
        conn, settings, system_tz=ZoneInfo("America/New_York"), code_vintage=CODE
    )
    assert [r.sweep_run_id for r in status.last_runs] == list(range(12, 2, -1))
    assert all(r.seconds_per_variant is None for r in status.last_runs)


# --- scale ----------------------------------------------------------------------------------


class _Recording:
    """A connection that records every statement it executes."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(sql)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_report_over_ten_thousand_trials_reads_trial_metrics_a_fixed_number_of_times(
    world: tuple[Store, registry.HypothesisRecord], settings: Settings
) -> None:
    store, twin = world
    conn = store.conn
    _sweep_record, records = _complete_sweep(store, settings)
    ids = [twin.hypothesis_id] + [r.hypothesis_id for r in records]
    (start,) = conn.execute("SELECT MAX(trial_id) + 1 FROM trials").fetchone()  # type: ignore[misc]
    # 10,000 more current `ok` trials spread over the twin and the four variants: every
    # one counts in N, and the newest of each variant becomes its counted trial.
    conn.execute(
        "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
        "end_session, data_cutoff, code_version, code_dirty, synthetic, holdout_repeat, "
        "run_by, detail_level, data_vintage, code_tree_sha256) "
        "SELECT $start + i, list_extract($ids, (i % 5) + 1), 'in_sample', now(), $s, $e, "
        "$cutoff, 'abc', false, false, false, 'test', 'summary', $vintage, $code "
        "FROM range(10000) r(i)",
        {
            "start": start,
            "ids": ids,
            "s": IN_SAMPLE_START,
            "e": DEFAULT_END,
            "cutoff": CUTOFF,
            "vintage": store.vintage,
            "code": CODE,
        },
    )
    conn.execute(
        "INSERT INTO trial_results (trial_id, finished_at, status, red_flag, sharpe_unit) "
        "SELECT $start + i, now(), 'ok', false, 'annual' FROM range(10000) r(i)",
        {"start": start},
    )
    base_metrics = _metrics(excess_cagr=0.01, sharpe_annual_excess=0.5)
    conn.execute(
        "INSERT INTO trial_metrics (trial_id, series, cost_per_side_bps, metric, value) "
        "SELECT $start + i, 'strategy', $base, m.metric, "
        "m.value + CASE WHEN m.metric LIKE 'sharpe%' THEN (i % 97) / 1000.0 ELSE 0 END "
        "FROM range(10000) r(i), "
        "(SELECT UNNEST($names::VARCHAR[]) AS metric, UNNEST($values::DOUBLE[]) AS value) m",
        {
            "start": start,
            "base": BASE,
            "names": list(base_metrics),
            "values": list(base_metrics.values()),
        },
    )
    recording = _Recording(conn)

    began = time.perf_counter()
    report = sweep_report.sweep_report(
        recording,  # type: ignore[arg-type]
        "mom-grid",
        code_vintage=CODE,
    )
    elapsed = time.perf_counter() - began

    assert report.family_n == results.family_n(conn, "momentum") >= 10_000
    assert report.complete
    # `family_sharpes`' one query per basis, plus the report's one read of the counted
    # trials' base-level metrics: independent of the 10,000 trials.
    metric_reads = [s for s in recording.statements if "trial_metrics" in s]
    assert len(metric_reads) == 3
    assert elapsed < REPORT_SECONDS_LIMIT


def test_report_module_has_no_destructive_statement() -> None:
    text = Path(sweep_report.__file__).read_text()
    assert re.search(r"\b(DROP|DELETE|TRUNCATE|VACUUM)\b", text, re.IGNORECASE) is None

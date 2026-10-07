"""Tests for the strategy-lab run-planning reads (plan T103b,
`store/lab_queries.py`) on the `lab_store` fixture with hand-inserted trials.

Covers every branch the plan names: `counted_trial` is the latest `ok`,
non-synthetic, `in_sample` trial over the default window; `is_current` /
`is_stale` by the data vintage at the trial's own cutoff and the checkout's
code vintage; req 2's counting rule branch by branch (two identical clean
current failures terminal, three excluded-kind failures not, two dirty or
other-vintage failures not, an `ok` after two failures counted, a stale `ok`
neither counts nor blocks, different messages not); `sweep_state`'s complete,
incomplete (stale) and incomplete (unrun); `plan_run`'s stale and unrun
variants, its read-group order and its `--rerun` refusal; and the measured
seconds per variant by cadence. Every function raises `LabNotInitialised` on a
plain `fixture_store`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import lab_queries, lab_registry, lab_schema, registry
from tradepartner.store.db import insert_row

IN_SAMPLE_START = date(2020, 8, 31)
HOLDOUT_START = date(2024, 1, 2)
HOLDOUT_END = date(2026, 9, 30)
#: The default in-sample window at `month_end`: the last month-end session
#: strictly before `HOLDOUT_START`.
DEFAULT_END = date(2023, 12, 29)
_CUTOFF = datetime(2100, 1, 1, tzinfo=UTC)
_OLD_VINTAGE = datetime(2019, 1, 1, tzinfo=UTC)
_FIXED = {
    "costs.per_side_bps": 15.0,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}


@lru_cache(maxsize=1)
def _checkout() -> str:
    """The checkout's code vintage, the value a current trial records."""
    value = registry.code_tree_sha256()
    assert value is not None
    return value


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
    slug: str,
    *,
    family: str = "momentum",
    params: dict[str, Any] | None = None,
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family=family,
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params=params if params is not None else _params(),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _sweep(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    slug: str = "mom-grid",
    family: str = "momentum",
    n_variants: int = 1,
) -> lab_registry.SweepRecord:
    return lab_registry.register_sweep(
        conn,
        slug=slug,
        family=family,
        title="grid",
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.1, 0.2, 0.3, 0.4]},
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
    params: dict[str, Any] | None = None,
) -> registry.HypothesisRecord:
    record = _register(
        conn,
        settings,
        f"{sweep.slug}--r{sweep.sweep_id}-v{index}",
        family=sweep.family,
        params=params if params is not None else _params(**{"strategy.top_fraction": 0.1 * index}),
    )
    fingerprint = f"{index:064x}"
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


def _one_variant(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> tuple[lab_registry.SweepRecord, registry.HypothesisRecord]:
    sweep = _sweep(conn, settings)
    return sweep, _variant(conn, settings, sweep, 1)


def _next_trial_id(conn: duckdb.DuckDBPyConnection) -> int:
    trials = conn.execute("SELECT COALESCE(MAX(trial_id), 0) FROM trials").fetchone()
    results = conn.execute("SELECT COALESCE(MAX(trial_id), 0) FROM trial_results").fetchone()
    assert trials is not None and results is not None
    return max(int(trials[0]), int(results[0])) + 1


def _insert_trial(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    *,
    status: str = "ok",
    message: str | None = None,
    window: tuple[date, date] = (IN_SAMPLE_START, DEFAULT_END),
    code_tree_sha256_value: str | None = None,
    data_vintage_value: datetime | None = None,
    code_dirty: bool = False,
    synthetic: bool = False,
    kind: str = "in_sample",
    data_cutoff: datetime | None = _CUTOFF,
) -> int:
    """Hand-insert one `trials` row and its `trial_results` row, returning the id.

    Defaults: a current `ok` trial (the checkout's code vintage, the data
    vintage computed now at `data_cutoff`). Pass `data_vintage_value` or
    `code_tree_sha256_value` to make it stale, and `status`/`message` for the
    counting-rule branches.
    """
    trial_id = _next_trial_id(conn)
    if code_tree_sha256_value is None:
        code_tree_sha256_value = _checkout()
    if data_vintage_value is None and data_cutoff is not None:
        data_vintage_value = registry.data_vintage(conn, data_cutoff)
    started = datetime(2026, 10, 7, tzinfo=UTC)
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": hypothesis_id,
            "kind": kind,
            "started_at": started,
            "start_session": window[0],
            "end_session": window[1],
            "data_cutoff": data_cutoff,
            "store_max_ingested_at": None,
            "code_version": "test",
            "code_dirty": code_dirty,
            "synthetic": synthetic,
            "holdout_repeat": False,
            "holdout_reason": None,
            "gap_override_reason": None,
            "run_by": "test",
            "note": None,
            "detail_level": "full",
            "data_vintage": data_vintage_value,
            "code_tree_sha256": code_tree_sha256_value,
        },
    )
    insert_row(
        conn,
        "trial_results",
        {"trial_id": trial_id, "finished_at": started, "status": status, "message": message},
    )
    return trial_id


# --- counted_trial -------------------------------------------------------------


def test_counted_trial_is_the_latest_ok_trial_over_the_default_window(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id)
    latest = _insert_trial(lab_store, record.hypothesis_id)
    found = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert found is not None
    assert found.trial_id == latest
    assert found.status == "ok"


def test_counted_trial_ignores_synthetic_failed_holdout_and_off_window(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, synthetic=True)
    _insert_trial(lab_store, record.hypothesis_id, status="failed", message="boom")
    _insert_trial(lab_store, record.hypothesis_id, kind="holdout")
    _insert_trial(lab_store, record.hypothesis_id, window=(IN_SAMPLE_START, date(2023, 11, 30)))
    assert lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id) is None
    counted = _insert_trial(lab_store, record.hypothesis_id)
    found = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert found is not None and found.trial_id == counted


# --- is_current / is_stale -----------------------------------------------------


def test_a_current_ok_trial_is_current_and_not_stale(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id)
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None
    assert lab_queries.is_current(lab_store, trial)
    assert not lab_queries.is_stale(lab_store, trial)


def test_an_older_data_vintage_is_stale(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None
    assert not lab_queries.is_current(lab_store, trial)
    assert lab_queries.is_stale(lab_store, trial)


def test_another_code_vintage_is_not_current(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, code_tree_sha256_value="0" * 64)
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None
    assert not lab_queries.is_current(lab_store, trial)
    assert lab_queries.is_stale(lab_store, trial)


def test_a_failed_trial_is_never_stale(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(
        lab_store,
        record.hypothesis_id,
        status="failed",
        message="boom",
        data_vintage_value=_OLD_VINTAGE,
    )
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is None
    assert not lab_queries.is_stale(
        lab_store,
        lab_queries.TrialRow(
            trial_id=1,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            synthetic=False,
            start_session=IN_SAMPLE_START,
            end_session=DEFAULT_END,
            data_cutoff=_CUTOFF,
            data_vintage=_OLD_VINTAGE,
            code_version="test",
            code_dirty=False,
            code_tree_sha256=_checkout(),
            status="failed",
            message="boom",
        ),
    )


# --- the counting rule, branch by branch ---------------------------------------


def test_two_identical_clean_current_failures_are_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for _ in range(2):
        _insert_trial(lab_store, record.hypothesis_id, status="failed", message="boom")
    assert lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_three_excluded_kind_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for message in ("store changed during run", "shared read failed", "store changed during run"):
        _insert_trial(lab_store, record.hypothesis_id, status="failed", message=message)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_dirty_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for _ in range(2):
        _insert_trial(
            lab_store, record.hypothesis_id, status="failed", message="boom", code_dirty=True
        )
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_other_vintage_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for _ in range(2):
        _insert_trial(
            lab_store,
            record.hypothesis_id,
            status="failed",
            message="boom",
            code_tree_sha256_value="0" * 64,
        )
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_different_messages_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, status="failed", message="boom")
    _insert_trial(lab_store, record.hypothesis_id, status="failed", message="bang")
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_an_ok_trial_after_two_failures_is_counted_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for _ in range(2):
        _insert_trial(lab_store, record.hypothesis_id, status="failed", message="boom")
    _insert_trial(lab_store, record.hypothesis_id)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())
    counted = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert counted is not None and lab_queries.is_current(lab_store, counted)


def test_a_stale_ok_after_two_failures_neither_counts_nor_blocks(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    for _ in range(2):
        _insert_trial(lab_store, record.hypothesis_id, status="failed", message="boom")
    stale = _insert_trial(lab_store, record.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    counted = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert counted is not None and counted.trial_id == stale
    assert lab_queries.is_stale(lab_store, counted)
    assert lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_no_failures_is_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


# --- sweep_state ---------------------------------------------------------------


def test_sweep_state_complete_when_every_variant_is_current(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    _insert_trial(lab_store, second.hypothesis_id)
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "complete" and state.complete
    assert state.stale == () and state.unrun == () and state.terminal_failed == ()


def test_sweep_state_incomplete_stale_lists_the_stale_variant(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    _insert_trial(lab_store, second.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "incomplete (stale)" and not state.complete
    assert [variant.variant_index for variant in state.stale] == [2]
    assert state.unrun == ()


def test_sweep_state_incomplete_unrun_lists_the_unrun_variant(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "incomplete (unrun)" and not state.complete
    assert [variant.variant_index for variant in state.unrun] == [2]
    assert state.stale == ()


def test_terminal_failed_counts_as_complete_and_is_listed(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    for _ in range(2):
        _insert_trial(lab_store, second.hypothesis_id, status="failed", message="boom")
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "complete" and state.complete
    assert [variant.variant_index for variant in state.terminal_failed] == [2]


def test_sweep_state_stale_takes_precedence_over_unrun(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "incomplete (stale)"
    assert [variant.variant_index for variant in state.stale] == [1]
    assert [variant.variant_index for variant in state.unrun] == [2]


# --- plan_run ------------------------------------------------------------------


def test_plan_run_lists_the_stale_and_unrun_variants_in_read_group_order(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=4)
    first = _variant(lab_store, settings, sweep, 1, _params(**{"strategy.top_fraction": 0.1}))
    second = _variant(lab_store, settings, sweep, 2, _params(**{"strategy.top_fraction": 0.2}))
    _variant(lab_store, settings, sweep, 3, _params(**{"strategy.top_fraction": 0.3}))
    _variant(
        lab_store,
        settings,
        sweep,
        4,
        _params(**{"strategy.top_fraction": 0.4, "universe.top_n_by_cap": 100}),
    )
    _insert_trial(lab_store, first.hypothesis_id)
    _insert_trial(lab_store, second.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    plan = lab_queries.plan_run(lab_store, sweep.sweep_id)
    assert [planned.variant.variant_index for planned in plan.variants] == [2, 3, 4]
    assert [planned.read_group_index for planned in plan.variants] == [1, 1, 2]
    groups = plan.read_groups()
    assert [[planned.variant.variant_index for planned in group] for group in groups] == [
        [2, 3],
        [4],
    ]
    assert [group[0].cadence for group in groups] == ["month_end", "month_end"]


def test_plan_run_rerun_lists_every_variant_when_complete(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    for _ in range(2):
        _insert_trial(lab_store, second.hypothesis_id, status="failed", message="boom")
    plan = lab_queries.plan_run(lab_store, sweep.sweep_id, rerun=True)
    assert plan.rerun
    assert [planned.variant.variant_index for planned in plan.variants] == [1, 2]


def test_plan_run_refuses_rerun_on_an_incomplete_sweep(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    with pytest.raises(lab_queries.SweepNotCompleteError):
        lab_queries.plan_run(lab_store, sweep.sweep_id, rerun=True)


# --- seconds per variant by cadence --------------------------------------------


def test_seconds_per_variant_by_cadence_averages_the_sweeps_trials(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Any
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=3)
    month = _variant(
        lab_store,
        settings,
        sweep,
        1,
        _params(**{"strategy.top_fraction": 0.1, "schedule.rebalance_cadence": "month_end"}),
    )
    week = _variant(
        lab_store,
        settings,
        sweep,
        2,
        _params(**{"strategy.top_fraction": 0.2, "schedule.rebalance_cadence": "week_end"}),
    )
    daily = _variant(
        lab_store,
        settings,
        sweep,
        3,
        _params(**{"strategy.top_fraction": 0.3, "schedule.rebalance_cadence": "daily"}),
    )
    run_id = lab_registry.open_sweep_run(
        lab_store,
        sweep_id=sweep.sweep_id,
        time_budget_minutes=480,
        n_declared=3,
        n_planned=3,
        code_tree_sha256=_checkout(),
        run_by="test",
        repo_dir=tmp_path,
    )
    for variant, seconds in ((month, 10.0), (week, 4.0), (week, 6.0), (daily, 20.0)):
        trial_id = _insert_trial(lab_store, variant.hypothesis_id)
        lab_registry.write_sweep_trial(
            lab_store, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=seconds
        )
    measured = lab_queries.seconds_per_variant_by_cadence(lab_store, sweep.sweep_id)
    assert measured == {"month_end": 10.0, "week_end": 5.0, "daily": 20.0}


# --- the lab gate --------------------------------------------------------------


def test_every_function_raises_without_the_lab_tables(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(lab_schema.LabNotInitialised):
        lab_queries.counted_trial(fixture_store, 1, 1)
    with pytest.raises(lab_schema.LabNotInitialised):
        lab_queries.plan_run(fixture_store, 1)
    with pytest.raises(lab_schema.LabNotInitialised):
        lab_queries.sweep_state(fixture_store, 1)
    with pytest.raises(lab_schema.LabNotInitialised):
        lab_queries.seconds_per_variant_by_cadence(fixture_store, 1)

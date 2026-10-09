"""Tests for the strategy-lab run-planning reads (plan T103b,
`store/lab_queries.py`) on the `lab_store` fixture with hand-inserted trials.

Covers every branch the plan names: `counted_trial` is the latest current
`ok`, non-synthetic, `in_sample` trial over the default window (and the latest
stale `ok` only when none is current); `is_current` / `is_stale` by the data
vintage at the trial's own cutoff and the checkout's code vintage; req 2's
counting rule branch by branch (two identical clean current failures terminal,
three excluded-kind failures not, two dirty or other-vintage failures not, an
`ok` after two failures counted, a stale `ok` neither counts nor blocks,
different messages not, synthetic or other-window failures not, another
registration's failures not); `sweep_state`'s complete, incomplete (stale) and
incomplete (unrun); `plan_run`'s stale and unrun variants, its read-group order
and its `--rerun` refusal; the measured seconds per variant by cadence from
`ok` trials only; and vintage at the trial's own cutoff with a realistic fact.
The rerun epoch (#1218): a `--rerun` the budget stopped is finished by a plain
run, a plain first run opens none, terminal-failure counts start fresh under a
rerun; and a `store_path` run's planning reads its synthetic trials only.
Every function raises `LabNotInitialised` on a plain `fixture_store`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
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
#: A realistic cutoff: the close of `DEFAULT_END`.
_CUTOFF = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
_OLD_VINTAGE = datetime(2019, 1, 1, tzinfo=UTC)


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


def _write_variant(
    conn: duckdb.DuckDBPyConnection,
    sweep: lab_registry.SweepRecord,
    index: int,
    record: registry.HypothesisRecord,
    *,
    fingerprint: str | None = None,
    variant_params: dict[str, Any] | None = None,
) -> str:
    """Link `record` to `sweep` as variant `index` and return the fingerprint
    (the caller writes the fingerprint row once, since one registration writes
    it once even when the same variant is linked to a second sweep)."""
    used = fingerprint if fingerprint is not None else f"{index:064x}"
    lab_registry.write_sweep_variant(
        conn,
        sweep_id=sweep.sweep_id,
        variant_index=index,
        hypothesis_id=record.hypothesis_id,
        fingerprint=used,
        variant_params=variant_params
        if variant_params is not None
        else {"strategy.top_fraction": 0.1 * index},
    )
    return used


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
    fingerprint = _write_variant(conn, sweep, index, record)
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


def _open_run(
    conn: duckdb.DuckDBPyConnection, sweep: lab_registry.SweepRecord, tmp_path: Path
) -> int:
    """An open `sweep_runs` row of `sweep`, so a trial can be linked to it as
    the runner links one (one `sweep_trials` row per trial)."""
    return lab_registry.open_sweep_run(
        conn,
        sweep_id=sweep.sweep_id,
        time_budget_minutes=480,
        n_declared=sweep.n_variants,
        n_planned=sweep.n_variants,
        code_tree_sha256=_checkout(),
        run_by="test",
        repo_dir=tmp_path,
    )


def _record_failure(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    run_id: int,
    *,
    message: str = "boom",
    **kwargs: Any,
) -> int:
    """Insert a `failed` trial and its `sweep_trials` row, as the runner does."""
    trial_id = _insert_trial(conn, hypothesis_id, status="failed", message=message, **kwargs)
    lab_registry.write_sweep_trial(
        conn, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=1.0
    )
    return trial_id


def _insert_price_fact(
    conn: duckdb.DuckDBPyConnection,
    *,
    security_id: str,
    session: date,
    known_at: datetime,
    ingested_at: datetime,
) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": security_id,
            "session": session,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
            "known_at": known_at,
            "ingested_at": ingested_at,
            "source": "test",
            "provenance": "bar",
        },
    )


# --- counted_trial -------------------------------------------------------------


def test_counted_trial_is_the_latest_current_ok_trial(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id)
    latest = _insert_trial(lab_store, record.hypothesis_id)
    found = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert found is not None
    assert found.trial_id == latest
    assert found.status == "ok"
    assert lab_queries.is_current(lab_store, found)


def test_counted_trial_prefers_a_current_older_ok_over_a_newer_stale_one(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """The counted trial is "the latest `ok` ... that is current": an older
    current `ok` beats a newer stale one (e.g. the checkout went back to the
    older code vintage), so the variant is counted, not stale."""
    sweep, record = _one_variant(lab_store, settings)
    older = _insert_trial(lab_store, record.hypothesis_id)
    _insert_trial(lab_store, record.hypothesis_id, code_tree_sha256_value="0" * 64)
    found = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert found is not None and found.trial_id == older
    assert lab_queries.is_current(lab_store, found)
    assert not lab_queries.is_stale(lab_store, found)


def test_counted_trial_returns_the_latest_ok_when_none_is_current(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    latest = _insert_trial(lab_store, record.hypothesis_id, data_vintage_value=_OLD_VINTAGE)
    found = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert found is not None and found.trial_id == latest
    assert lab_queries.is_stale(lab_store, found)


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


def test_vintage_is_measured_at_the_trials_own_cutoff(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """`_is_current` must read the vintage at the trial's own cutoff, not the
    store-wide one: a fact known after the cutoff leaves the trial current,
    while a late fact inside the window changes the vintage and makes it
    stale."""
    sweep, record = _one_variant(lab_store, settings)
    vintage = registry.data_vintage(lab_store, _CUTOFF)
    assert vintage is not None
    _insert_trial(
        lab_store,
        record.hypothesis_id,
        data_cutoff=_CUTOFF,
        data_vintage_value=vintage,
    )
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None and lab_queries.is_current(lab_store, trial)
    # A fact known after the cutoff does not move the vintage at the cutoff,
    # even though its ingested_at (later than the current max) raises the
    # store-wide vintage: only an own-cutoff comparison stays current here.
    _insert_price_fact(
        lab_store,
        security_id="SEC_AFTER",
        session=date(2024, 1, 2),
        known_at=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        ingested_at=vintage + timedelta(days=1),
    )
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None and lab_queries.is_current(lab_store, trial)
    # A late fact for an in-window session moves the vintage at the cutoff: stale.
    _insert_price_fact(
        lab_store,
        security_id="SEC_LATE",
        session=date(2020, 6, 30),
        known_at=datetime(2020, 6, 30, 20, 0, tzinfo=UTC),
        ingested_at=vintage + timedelta(days=2),
    )
    trial = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert trial is not None and lab_queries.is_stale(lab_store, trial)
    assert lab_queries.sweep_state(lab_store, sweep.sweep_id).state == "incomplete (stale)"


# --- the counting rule, branch by branch ---------------------------------------


def test_two_identical_clean_current_failures_are_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id)
    assert lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_three_excluded_kind_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for message in ("store changed during run", "shared read failed", "store changed during run"):
        _record_failure(lab_store, record.hypothesis_id, run_id, message=message)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_dirty_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id, code_dirty=True)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_other_vintage_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id, code_tree_sha256_value="0" * 64)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_two_different_messages_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    _record_failure(lab_store, record.hypothesis_id, run_id, message="boom")
    _record_failure(lab_store, record.hypothesis_id, run_id, message="bang")
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_synthetic_or_other_window_failures_are_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id, synthetic=True)
    for _ in range(2):
        _record_failure(
            lab_store,
            record.hypothesis_id,
            run_id,
            window=(IN_SAMPLE_START, date(2023, 11, 30)),
        )
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())


def test_another_registrations_failures_do_not_count(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """A hypothesis linked to two sweep registrations does not take the other
    registration's failures."""
    first = _sweep(lab_store, settings, slug="sweep-a")
    second = _sweep(lab_store, settings, slug="sweep-b")
    record = _register(lab_store, settings, "shared-variant")
    fingerprint = _write_variant(lab_store, first, 1, record)
    lab_registry.write_fingerprint(lab_store, record.hypothesis_id, fingerprint)
    _write_variant(lab_store, second, 1, record, fingerprint=fingerprint)
    run_id = _open_run(lab_store, first, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id)
    assert lab_queries.terminal_failed(lab_store, record.hypothesis_id, first, _checkout())
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, second, _checkout())


def test_an_ok_trial_after_two_failures_is_counted_not_terminal(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id)
    _insert_trial(lab_store, record.hypothesis_id)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, _checkout())
    counted = lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert counted is not None and lab_queries.is_current(lab_store, counted)


def test_a_stale_ok_after_two_failures_neither_counts_nor_blocks(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id)
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


def test_terminal_failed_reads_the_counted_ok_at_the_passed_code_vintage(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """`terminal_failed`'s counted `ok` is judged at the caller's code vintage:
    an older `ok` at vintage X clears the state even though a newer `ok` is
    current only at the checkout's vintage."""
    sweep, record = _one_variant(lab_store, settings)
    vintage = "x" * 64
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, record.hypothesis_id, run_id, code_tree_sha256_value=vintage)
    _insert_trial(lab_store, record.hypothesis_id, code_tree_sha256_value=vintage)
    _insert_trial(lab_store, record.hypothesis_id)
    assert not lab_queries.terminal_failed(lab_store, record.hypothesis_id, sweep, vintage)


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
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, second.hypothesis_id, run_id)
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


# --- the development boundary (ADR 0016 points 2 and 6; plan T142b) --------------

#: A moved boundary: the default window then ends at its last month end.
_MOVED = date(2022, 12, 30)


def _boundary(conn: duckdb.DuckDBPyConnection, day: date) -> None:
    registry.write_development_boundary(conn, boundary=day, reason="test")


def test_the_default_window_ends_at_the_boundary(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep, record = _one_variant(lab_store, settings)
    window = lab_queries.default_window(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert (window.start, window.end) == (IN_SAMPLE_START, DEFAULT_END)
    _boundary(lab_store, date(2023, 6, 15))
    window = lab_queries.default_window(lab_store, record.hypothesis_id, sweep.sweep_id)
    assert (window.start, window.end) == (IN_SAMPLE_START, date(2023, 5, 31))


def test_the_first_boundary_at_the_practice_end_stales_nothing(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """#1333: writing 2023-12-29 leaves every default window as it was, so a trial with
    a NULL recorded boundary stays current and nothing is replanned."""
    sweep, record = _one_variant(lab_store, settings)
    trial = _insert_trial(lab_store, record.hypothesis_id)
    _boundary(lab_store, DEFAULT_END)
    (recorded,) = lab_store.execute(  # type: ignore[misc]
        "SELECT development_boundary FROM trials WHERE trial_id = ?", [trial]
    ).fetchone()
    assert recorded is None
    (state,) = lab_queries.variant_states(lab_store, sweep.sweep_id)
    assert (state.state, state.trial.trial_id if state.trial else None) == ("current", trial)
    assert lab_queries.sweep_state(lab_store, sweep.sweep_id).complete
    assert lab_queries.plan_run(lab_store, sweep.sweep_id).variants == ()


def test_a_moved_boundary_stales_the_trial_over_the_old_window(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """The window test: a trial whose window is not the default window under the
    current boundary is stale (counted in N, never selected) and replanned; a trial
    over the new default window is current again."""
    sweep, record = _one_variant(lab_store, settings)
    old = _insert_trial(lab_store, record.hypothesis_id)
    _boundary(lab_store, _MOVED)
    (state,) = lab_queries.variant_states(lab_store, sweep.sweep_id)
    assert state.state == "stale"
    assert state.trial is not None and state.trial.trial_id == old
    assert lab_queries.counted_trial(lab_store, record.hypothesis_id, sweep.sweep_id) is None
    assert lab_queries.sweep_state(lab_store, sweep.sweep_id).state == "incomplete (stale)"
    assert [
        p.variant.hypothesis_id for p in lab_queries.plan_run(lab_store, sweep.sweep_id).variants
    ] == [record.hypothesis_id]
    new = _insert_trial(lab_store, record.hypothesis_id, window=(IN_SAMPLE_START, _MOVED))
    (state,) = lab_queries.variant_states(lab_store, sweep.sweep_id)
    assert state.state == "current"
    assert state.trial is not None and state.trial.trial_id == new


def test_without_a_boundary_another_window_is_not_stale(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """With no boundary row the window test is not applied: an `ok` trial over another
    window leaves the variant unrun, as before T142b."""
    sweep, record = _one_variant(lab_store, settings)
    _insert_trial(lab_store, record.hypothesis_id, window=(IN_SAMPLE_START, _MOVED))
    (state,) = lab_queries.variant_states(lab_store, sweep.sweep_id)
    assert (state.state, state.trial) == ("unrun", None)


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
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, second.hypothesis_id, run_id)
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


# --- the rerun epoch and synthetic runs (#1218) --------------------------------


def _closed_run(
    conn: duckdb.DuckDBPyConnection,
    sweep: lab_registry.SweepRecord,
    tmp_path: Path,
    *,
    n_planned: int,
    completed: bool | None,
    trials: tuple[int, ...] = (),
) -> int:
    """A `sweep_runs` row planning `n_planned` variants, linked to `trials` and
    closed with `completed` (left open with None), as the runner (T107) writes
    one."""
    run_id = lab_registry.open_sweep_run(
        conn,
        sweep_id=sweep.sweep_id,
        time_budget_minutes=480,
        n_declared=sweep.n_variants,
        n_planned=n_planned,
        code_tree_sha256=_checkout(),
        run_by="test",
        repo_dir=tmp_path,
    )
    for trial_id in trials:
        lab_registry.write_sweep_trial(
            conn, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=1.0
        )
    if completed is None:
        return run_id
    lab_registry.close_sweep_run(
        conn,
        run_id,
        n_ok=len(trials),
        n_failed=0,
        n_terminal_failed=0,
        seconds=1.0,
        n_trials_at_end=0,
        sr_star_annual_at_end=0.0,
        completed=completed,
    )
    return run_id


def test_a_rerun_stopped_by_the_budget_is_finished_by_a_plain_run(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    before = (_insert_trial(lab_store, first.hypothesis_id),)
    before += (_insert_trial(lab_store, second.hypothesis_id),)
    _closed_run(lab_store, sweep, tmp_path, n_planned=2, completed=True, trials=before)
    assert lab_queries.sweep_state(lab_store, sweep.sweep_id).complete
    assert lab_queries.rerun_epoch(lab_store, sweep.sweep_id) is None
    # A --rerun plans both; the budget stops it after variant 1.
    rerun_trial = _insert_trial(lab_store, first.hypothesis_id)
    epoch = _closed_run(
        lab_store, sweep, tmp_path, n_planned=2, completed=False, trials=(rerun_trial,)
    )
    assert lab_queries.rerun_epoch(lab_store, sweep.sweep_id) == epoch
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.state == "incomplete (stale)"
    assert [v.variant_index for v in state.stale] == [2]
    plan = lab_queries.plan_run(lab_store, sweep.sweep_id)
    assert [planned.variant.variant_index for planned in plan.variants] == [2]
    with pytest.raises(lab_queries.SweepNotCompleteError):
        lab_queries.plan_run(lab_store, sweep.sweep_id, rerun=True)
    # The next plain run (one variant planned) finishes it.
    finishing = _insert_trial(lab_store, second.hypothesis_id)
    _closed_run(lab_store, sweep, tmp_path, n_planned=1, completed=True, trials=(finishing,))
    assert lab_queries.sweep_state(lab_store, sweep.sweep_id).complete
    assert lab_queries.plan_run(lab_store, sweep.sweep_id).variants == ()


def test_a_plain_first_run_opens_no_epoch(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    _variant(lab_store, settings, sweep, 2)
    trial = _insert_trial(lab_store, first.hypothesis_id)
    _closed_run(lab_store, sweep, tmp_path, n_planned=2, completed=False, trials=(trial,))
    assert lab_queries.rerun_epoch(lab_store, sweep.sweep_id) is None
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert [v.variant_index for v in state.unrun] == [2]


def test_terminal_failure_counts_start_fresh_under_a_rerun(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    ok = _insert_trial(lab_store, first.hypothesis_id)
    run_id = _open_run(lab_store, sweep, tmp_path)
    for _ in range(2):
        _record_failure(lab_store, second.hypothesis_id, run_id)
    lab_registry.write_sweep_trial(
        lab_store, sweep_run_id=run_id, trial_id=ok, read_group_index=1, seconds=1.0
    )
    lab_registry.close_sweep_run(
        lab_store,
        run_id,
        n_ok=1,
        n_failed=2,
        n_terminal_failed=1,
        seconds=1.0,
        n_trials_at_end=0,
        sr_star_annual_at_end=0.0,
        completed=True,
    )
    assert lab_queries.terminal_failed(lab_store, second.hypothesis_id, sweep, _checkout())
    # A --rerun fails variant 2 once with the same message: a fresh count of one.
    rerun_ok = _insert_trial(lab_store, first.hypothesis_id)
    rerun = _closed_run(lab_store, sweep, tmp_path, n_planned=2, completed=None)
    _record_failure(lab_store, second.hypothesis_id, rerun)
    lab_registry.write_sweep_trial(
        lab_store, sweep_run_id=rerun, trial_id=rerun_ok, read_group_index=1, seconds=1.0
    )
    assert not lab_queries.terminal_failed(lab_store, second.hypothesis_id, sweep, _checkout())
    state = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert state.terminal_failed == ()
    assert [v.variant_index for v in state.unrun] == [2]
    # A second identical failure inside the epoch makes it terminal again.
    _record_failure(lab_store, second.hypothesis_id, rerun)
    assert lab_queries.terminal_failed(lab_store, second.hypothesis_id, sweep, _checkout())


def test_synthetic_planning_reads_only_synthetic_trials(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    sweep = _sweep(lab_store, settings, n_variants=2)
    first = _variant(lab_store, settings, sweep, 1)
    second = _variant(lab_store, settings, sweep, 2)
    _insert_trial(lab_store, first.hypothesis_id, synthetic=True)
    _insert_trial(lab_store, second.hypothesis_id, synthetic=False)
    synthetic = lab_queries.sweep_state(lab_store, sweep.sweep_id, synthetic=True)
    assert [v.variant_index for v in synthetic.unrun] == [2]
    real = lab_queries.sweep_state(lab_store, sweep.sweep_id)
    assert [v.variant_index for v in real.unrun] == [1]
    plan = lab_queries.plan_run(lab_store, sweep.sweep_id, synthetic=True)
    assert [planned.variant.variant_index for planned in plan.variants] == [2]


# --- seconds per variant by cadence --------------------------------------------


def test_seconds_per_variant_by_cadence_averages_the_sweeps_ok_trials(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
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
    run_id = _open_run(lab_store, sweep, tmp_path)
    for variant, seconds in ((month, 10.0), (week, 4.0), (week, 6.0), (daily, 20.0)):
        trial_id = _insert_trial(lab_store, variant.hypothesis_id)
        lab_registry.write_sweep_trial(
            lab_store, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=seconds
        )
    # A failed trial's near-zero seconds are not a run time and must not move it.
    failure = _insert_trial(lab_store, month.hypothesis_id, status="failed", message="boom")
    lab_registry.write_sweep_trial(
        lab_store, sweep_run_id=run_id, trial_id=failure, read_group_index=1, seconds=0.001
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

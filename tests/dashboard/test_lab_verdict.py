"""Tests for the Lab's verdict and in-flight reads (plan T291, `dashboard/lab_verdict.py`).

Every case on hand-inserted registry rows on the `lab_store` fixture. The verdict is
the registry's: a hypothesis run reads the first trial above the baseline, a sweep run
its new `sweep_runs` row; the exit code speaks only when no new row exists and never
overrides one. `in_flight` separates a live open row from an orphan older than
`ui.lab_orphan_after_hours`.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.backtest import results
from tradepartner.config import Settings
from tradepartner.dashboard import lab_verdict
from tradepartner.store import lab_registry, registry
from tradepartner.store.db import insert_row, utc_now
from tradepartner.store.lab_schema import LabNotInitialised

IN_SAMPLE_START = date(2020, 8, 31)
HOLDOUT_START = date(2024, 1, 2)
HOLDOUT_END = date(2026, 9, 30)
DEFAULT_END = date(2023, 12, 29)
CODE = "c" * 64
BASE = 15.0
FIXED = {
    "costs.per_side_bps": BASE,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


def _hyp(
    conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str, fraction: float = 0.1
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=slug,
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params={**FIXED, "strategy.top_fraction": fraction},
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _open(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    hypothesis_id: int,
    *,
    synthetic: bool = False,
) -> registry.TrialHandle:
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis_id,
        kind="in_sample",
        start_session=IN_SAMPLE_START,
        end_session=DEFAULT_END,
        data_cutoff=datetime(2023, 12, 29, 21, 0, tzinfo=UTC),
        synthetic=synthetic,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )


def _trial(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    hypothesis_id: int,
    status: str | None,
    message: str | None = None,
) -> int:
    """A trial closed as `status` (`ok` writes empty statistics); None leaves it open."""
    handle = _open(conn, settings, tmp_path, hypothesis_id)
    if status == "ok":
        registry.write_result(conn, handle, registry.ResultStatistics())
    elif status is not None:
        registry.close_trial(conn, handle, status, message)
    return handle.trial_id


def _baseline(conn: duckdb.DuckDBPyConnection) -> int:
    (top,) = conn.execute("SELECT COALESCE(MAX(trial_id), 0) FROM trials").fetchone()  # type: ignore[misc]
    return int(top)


def _verdict(conn: duckdb.DuckDBPyConnection, **kw: Any) -> lab_verdict.LabVerdict:
    args: dict[str, Any] = {
        "kind": "backtest",
        "slug": "h1",
        "baseline_id": 0,
        "exit_code": 0,
        "output": "",
        "now": NOW,
    }
    args.update(kw)
    return lab_verdict.verdict(conn, **args)


# --- a hypothesis run ----------------------------------------------------------------


def test_hypothesis_verdict_is_the_first_trial_above_the_baseline_not_an_earlier_desk_run(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _hyp(lab_store, settings, "h1")
    _trial(lab_store, settings, tmp_path, h1.hypothesis_id, "failed", "desk run broke")
    baseline = _baseline(lab_store)
    mine = _trial(lab_store, settings, tmp_path, h1.hypothesis_id, "ok")
    _trial(lab_store, settings, tmp_path, h1.hypothesis_id, "failed", "a later desk run")

    got = _verdict(lab_store, baseline_id=baseline)

    assert (got.outcome, got.trial_id, got.from_registry) == ("ok", mine, True)
    assert got.message is None


def test_hypothesis_verdict_ignores_a_synthetic_trial_above_the_baseline(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _hyp(lab_store, settings, "h1")
    baseline = _baseline(lab_store)
    handle = _open(lab_store, settings, tmp_path, h1.hypothesis_id, synthetic=True)
    registry.close_trial(lab_store, handle, "failed", "synthetic")

    got = _verdict(lab_store, baseline_id=baseline, exit_code=2, output="usage text")

    assert got.outcome == "refused" and got.from_registry is False


def test_refused_window_with_exit_2_shows_the_stored_status_and_message_and_keeps_n(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _hyp(lab_store, settings, "h1")
    baseline = _baseline(lab_store)
    _trial(
        lab_store,
        settings,
        tmp_path,
        h1.hypothesis_id,
        "refused_window",
        "window ends in the holdout",
    )
    n_before = results.family_n(lab_store, "momentum")

    got = _verdict(lab_store, baseline_id=baseline, exit_code=2, output="error: usage error")

    assert got.outcome == "refused_window"
    assert got.message == "window ends in the holdout"
    assert "window ends in the holdout" in got.text
    assert "usage error" not in got.text
    assert results.family_n(lab_store, "momentum") == n_before == 0


@pytest.mark.parametrize("code", [0, 1, 2, 3, None])
def test_the_exit_code_never_overrides_a_row(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path, code: int | None
) -> None:
    h1 = _hyp(lab_store, settings, "h1")
    _trial(lab_store, settings, tmp_path, h1.hypothesis_id, "failed", "engine error")

    got = _verdict(lab_store, exit_code=code, output="store busy: x")

    assert (got.outcome, got.message, got.from_registry) == ("failed", "engine error", True)


def test_an_unfinished_trial_is_running_without_an_exit_code_and_unfinished_with_one(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    h1 = _hyp(lab_store, settings, "h1")
    _trial(lab_store, settings, tmp_path, h1.hypothesis_id, None)

    assert _verdict(lab_store, exit_code=None).outcome == "running"
    assert _verdict(lab_store, exit_code=-9).outcome == "unfinished"


@pytest.mark.parametrize(
    ("code", "output", "outcome"),
    [
        (2, "error: no hypothesis file", "refused"),
        (1, "store busy: the store is locked", "store_busy"),
        (1, "  store busy: the store is locked", "store_busy"),
        (1, "something else broke", "failed"),
        (3, "", "failed"),
        (0, "", "failed"),
        (-15, "killed", "failed"),
        (None, "", "no_trial"),
    ],
)
def test_with_no_new_row_the_exit_code_decides(
    lab_store: duckdb.DuckDBPyConnection,
    settings: Settings,
    code: int | None,
    output: str,
    outcome: str,
) -> None:
    _hyp(lab_store, settings, "h1")

    got = _verdict(lab_store, exit_code=code, output=output)

    assert (got.outcome, got.from_registry, got.trial_id) == (outcome, False, None)
    if outcome in ("refused", "store_busy"):
        assert got.text == output
    if outcome == "no_trial":
        assert got.text == "ended with no trial recorded"


# --- a sweep run ---------------------------------------------------------------------


def _sweep(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    values: list[float],
    slug: str = "mom-grid",
) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
    lab_registry.write_family_rules(
        conn,
        family="momentum",
        first_hypothesis_id=_hyp(conn, settings, "twin").hypothesis_id,
        parent_family=None,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        in_sample_start=IN_SAMPLE_START,
        fixed_params=FIXED,
        sr_star_seed_annual=None,
        settings=settings,
    )
    sweep = lab_registry.register_sweep(
        conn,
        slug=slug,
        family="momentum",
        title="grid",
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": values},
        n_variants=len(values),
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
    records = []
    for index, value in enumerate(values, start=1):
        record = _hyp(conn, settings, f"{slug}--v{index}", value)
        fingerprint = f"{index:064x}"
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


def _run(
    conn: duckdb.DuckDBPyConnection, sweep: lab_registry.SweepRecord, tmp_path: Path, n: int = 2
) -> int:
    return lab_registry.open_sweep_run(
        conn,
        sweep_id=sweep.sweep_id,
        time_budget_minutes=60,
        n_declared=n,
        n_planned=n,
        code_tree_sha256=CODE,
        run_by="test",
        repo_dir=tmp_path,
    )


def _close(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    *,
    n_ok: int,
    n_failed: int = 0,
    completed: bool,
) -> None:
    lab_registry.close_sweep_run(
        conn,
        run_id,
        n_ok=n_ok,
        n_failed=n_failed,
        n_terminal_failed=0,
        seconds=2.0,
        n_trials_at_end=n_ok,
        sr_star_annual_at_end=0.1,
        completed=completed,
    )


def _run_trial(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    run_id: int,
    record: registry.HypothesisRecord,
    status: str | None,
    message: str | None = None,
) -> int:
    trial_id = _trial(conn, settings, tmp_path, record.hypothesis_id, status, message)
    lab_registry.write_sweep_trial(
        conn, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=1.0
    )
    return trial_id


def _sweep_verdict(conn: duckdb.DuckDBPyConnection, **kw: Any) -> lab_verdict.LabVerdict:
    return _verdict(conn, kind="sweep_run", slug="mom-grid", **kw)


def test_a_sweep_verdict_is_the_new_run_row_and_each_variants_outcome(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, records = _sweep(lab_store, settings, [0.1, 0.2])
    earlier = _run(lab_store, sweep, tmp_path)
    _close(lab_store, earlier, n_ok=0, n_failed=2, completed=False)
    run_id = _run(lab_store, sweep, tmp_path)
    _run_trial(lab_store, settings, tmp_path, run_id, records[0], "ok")
    _run_trial(lab_store, settings, tmp_path, run_id, records[1], "failed", "engine broke")
    _close(lab_store, run_id, n_ok=1, n_failed=1, completed=False)

    got = _sweep_verdict(lab_store, baseline_id=earlier, exit_code=0)

    assert got.sweep_run_id == run_id and got.from_registry
    assert (got.n_planned, got.n_ok, got.n_failed) == (2, 1, 1)
    assert got.outcome == "failed"  # every planned variant ran, one failed
    assert [(v.variant_index, v.status, v.message) for v in got.variants] == [
        (1, "ok", None),
        (2, "failed", "engine broke"),
    ]
    assert got.verdicts is None  # not a complete sweep


def test_a_sweep_that_stopped_early_says_so_and_carries_the_log_tail(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, records = _sweep(lab_store, settings, [0.1, 0.2])
    run_id = _run(lab_store, sweep, tmp_path)
    _run_trial(lab_store, settings, tmp_path, run_id, records[0], "ok")
    _close(lab_store, run_id, n_ok=1, completed=False)

    got = _sweep_verdict(lab_store, exit_code=0, output="time budget reached")

    assert got.outcome == "ended_early"
    assert "ended before every planned variant ran" in got.text
    assert got.message == "time budget reached" and got.log_tail == "time budget reached"


def test_an_open_row_is_running_until_the_child_has_gone_then_ended_early(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, _ = _sweep(lab_store, settings, [0.1, 0.2])
    _run(lab_store, sweep, tmp_path)

    assert _sweep_verdict(lab_store, exit_code=None).outcome == "running"
    died = _sweep_verdict(lab_store, exit_code=1, output="Traceback: boom")
    assert died.outcome == "ended_early" and died.message == "Traceback: boom"


def test_a_sweep_exit_code_never_overrides_the_run_row(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, records = _sweep(lab_store, settings, [0.1])
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    _run_trial(lab_store, settings, tmp_path, run_id, records[0], "ok")
    _close(lab_store, run_id, n_ok=1, completed=False)

    for code in (None, 0, 1, 2, 3):
        assert _sweep_verdict(lab_store, exit_code=code, output="store busy: x").outcome == "ok"


@pytest.mark.parametrize(
    ("code", "output", "outcome"),
    [
        (2, "error: bad", "refused"),
        (1, "store busy: x", "store_busy"),
        (1, "boom", "failed"),
        (None, "", "no_trial"),
    ],
)
def test_a_sweep_with_no_new_row_reads_the_exit_code(
    lab_store: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    code: int | None,
    output: str,
    outcome: str,
) -> None:
    sweep, _ = _sweep(lab_store, settings, [0.1])
    old = _run(lab_store, sweep, tmp_path, n=1)
    _close(lab_store, old, n_ok=1, completed=True)

    got = _sweep_verdict(lab_store, baseline_id=old, exit_code=code, output=output)

    assert (got.outcome, got.from_registry) == (outcome, False)


def _metrics(sharpe: float) -> dict[str, float]:
    return {
        "excess_cagr_spy": 0.01,
        "sharpe_annual_excess_spy": sharpe,
        "sharpe_period_excess_spy": sharpe / math.sqrt(12),
        "skew_period_excess_spy": -0.1,
        "kurtosis_period_excess_spy": 3.5,
        "sharpe_period": 0.2,
        "cost_drag": 0.004,
        "turnover_annual": 5.5,
        "max_drawdown": -0.21,
        "n_periods": 40.0,
        "periods_per_year": 12.0,
    }


def _counted_trial(
    conn: duckdb.DuckDBPyConnection, record: registry.HypothesisRecord, sharpe: float
) -> int:
    (trial_id,) = conn.execute("SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials").fetchone()  # type: ignore[misc]
    cutoff = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": record.hypothesis_id,
            "kind": "in_sample",
            "started_at": utc_now(),
            "start_session": IN_SAMPLE_START,
            "end_session": DEFAULT_END,
            "data_cutoff": cutoff,
            "store_max_ingested_at": None,
            "code_version": "abc",
            "code_dirty": False,
            "synthetic": False,
            "holdout_repeat": False,
            "run_by": "test",
            "detail_level": "summary",
            "data_vintage": registry.data_vintage(conn, cutoff),
            "code_tree_sha256": CODE,
        },
    )
    insert_row(
        conn,
        "trial_results",
        {
            "trial_id": trial_id,
            "finished_at": utc_now(),
            "status": "ok",
            "message": None,
            "red_flag": False,
            "dsr_excess": 0.9,
            "sharpe_unit": "annual",
        },
    )
    for metric, value in _metrics(sharpe).items():
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
    return int(trial_id)


def test_a_complete_sweep_carries_the_reports_two_verdicts(
    lab_store: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "code_tree_sha256", lambda: CODE)
    sweep, records = _sweep(lab_store, settings, [0.1, 0.2])
    run_id = _run(lab_store, sweep, tmp_path)
    for record, sharpe in zip(records, (0.4, 0.9), strict=True):
        trial_id = _counted_trial(lab_store, record, sharpe)
        lab_registry.write_sweep_trial(
            lab_store, sweep_run_id=run_id, trial_id=trial_id, read_group_index=1, seconds=1.0
        )
    _close(lab_store, run_id, n_ok=2, completed=True)

    got = _sweep_verdict(lab_store, exit_code=0)

    assert got.outcome == "completed"
    assert got.verdicts is not None
    assert got.verdicts.argmax_slug == records[1].slug
    assert (got.verdicts.promote_at_least, got.verdicts.retire_below) == (0.5, 0.0)
    assert got.verdicts.retire_below_met is False  # 0.9 is not below 0.0
    assert isinstance(got.verdicts.promote_at_least_met, bool)
    assert [v.status for v in got.variants] == ["ok", "ok"]


def test_verdict_is_read_only(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, records = _sweep(lab_store, settings, [0.1])
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    _run_trial(lab_store, settings, tmp_path, run_id, records[0], "ok")
    counts = (
        "SELECT (SELECT COUNT(*) FROM trials), (SELECT COUNT(*) FROM trial_results), "
        "(SELECT COUNT(*) FROM sweep_runs)"
    )
    before = lab_store.execute(counts).fetchone()
    _sweep_verdict(lab_store)
    _verdict(lab_store, slug="twin")
    lab_verdict.in_flight(lab_store, NOW, settings)
    assert lab_store.execute(counts).fetchone() == before


def test_a_naive_now_is_refused(lab_store: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    naive = datetime(2026, 10, 28)  # noqa: DTZ001
    with pytest.raises(ValueError, match="tz-aware"):
        _verdict(lab_store, now=naive)
    with pytest.raises(ValueError, match="tz-aware"):
        lab_verdict.in_flight(lab_store, naive, settings)


def test_a_store_without_the_lab_tables_is_refused(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    with pytest.raises(LabNotInitialised):
        _sweep_verdict(fixture_store)
    with pytest.raises(LabNotInitialised):
        lab_verdict.in_flight(fixture_store, NOW, settings)


# --- in flight -----------------------------------------------------------------------


def _age(
    conn: duckdb.DuckDBPyConnection, table: str, column: str, row_id: int, hours: float
) -> None:
    conn.execute(
        f"UPDATE {table} SET started_at = ? WHERE {column} = ?",
        [NOW - timedelta(hours=hours), row_id],
    )


def test_in_flight_lists_a_recent_open_run_and_an_unfinished_trial_as_running(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, _ = _sweep(lab_store, settings, [0.1])
    desk = _hyp(lab_store, settings, "desk-h")  # not a sweep variant
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    trial_id = _trial(lab_store, settings, tmp_path, desk.hypothesis_id, None)
    _age(lab_store, "sweep_runs", "sweep_run_id", run_id, 1)
    _age(lab_store, "trials", "trial_id", trial_id, 2)

    got = lab_verdict.in_flight(lab_store, NOW, settings)

    assert got.busy and got.orphans == ()
    assert {(i.kind, i.row_id, i.slug, i.state) for i in got.running} == {
        ("sweep_run", run_id, "mom-grid", "running"),
        ("trial", trial_id, desk.slug, "running"),
    }
    assert all(i.remedy is None for i in got.running)


def test_a_historic_orphan_is_listed_apart_and_does_not_block(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, _ = _sweep(lab_store, settings, [0.1])
    desk = _hyp(lab_store, settings, "desk-h")  # not a sweep variant
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    trial_id = _trial(lab_store, settings, tmp_path, desk.hypothesis_id, None)
    hours = settings.ui.lab_orphan_after_hours + 1
    _age(lab_store, "sweep_runs", "sweep_run_id", run_id, hours)
    _age(lab_store, "trials", "trial_id", trial_id, hours)

    got = lab_verdict.in_flight(lab_store, NOW, settings)

    assert not got.busy and got.running == ()
    assert {(i.kind, i.state) for i in got.orphans} == {
        ("sweep_run", "orphan"),
        ("trial", "orphan"),
    }
    assert all(i.remedy == lab_verdict.ORPHAN_REMEDY for i in got.orphans)


def test_the_orphan_boundary_comes_from_config(
    lab_store: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    short = Settings(_env_file=None, ui={"lab_orphan_after_hours": 2})
    sweep, _ = _sweep(lab_store, short, [0.1])
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    _age(lab_store, "sweep_runs", "sweep_run_id", run_id, 3)

    assert lab_verdict.in_flight(lab_store, NOW, short).orphans[0].row_id == run_id
    assert lab_verdict.in_flight(lab_store, NOW, Settings(_env_file=None)).busy


def test_closed_runs_finished_trials_and_synthetic_trials_are_not_in_flight(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    sweep, records = _sweep(lab_store, settings, [0.1])
    run_id = _run(lab_store, sweep, tmp_path, n=1)
    _close(lab_store, run_id, n_ok=1, completed=True)
    _trial(lab_store, settings, tmp_path, records[0].hypothesis_id, "ok")
    _open(lab_store, settings, tmp_path, records[0].hypothesis_id, synthetic=True)

    got = lab_verdict.in_flight(lab_store, NOW, settings)

    assert (got.running, got.orphans, got.busy) == ((), (), False)


def test_an_unfinished_variant_trial_without_a_link_follows_its_sweeps_run(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """The runner links a trial to its run only when it closes the variant
    (`backtest/lab.py`, `_write_sweep_trial`), so an interrupt leaves an unfinished
    trial with no `sweep_trials` row: the state built here."""
    sweep, records = _sweep(lab_store, settings, [0.1, 0.2])
    run_id = _run(lab_store, sweep, tmp_path)
    for record in records:
        _trial(lab_store, settings, tmp_path, record.hypothesis_id, None)  # unlinked
    assert lab_store.execute("SELECT COUNT(*) FROM sweep_trials").fetchone() == (0,)
    _age(lab_store, "sweep_runs", "sweep_run_id", run_id, 1)

    open_run = lab_verdict.in_flight(lab_store, NOW, settings)
    assert [(i.kind, i.row_id) for i in open_run.running] == [("sweep_run", run_id)]

    _close(lab_store, run_id, n_ok=0, n_failed=0, completed=False)  # the interrupt closed it
    closed = lab_verdict.in_flight(lab_store, NOW, settings)
    assert not closed.busy and closed.running == ()
    assert {i.kind for i in closed.orphans} == {"trial"} and len(closed.orphans) == 2

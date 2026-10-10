"""Target comparison and `paper report` (Phase 4 spec req 10, last paragraph;
plan T65b).

`compare_targets` needs a real connection (`trial_weights` has no registry
reader, and the late-data check scans the raw fact tables directly), so these
tests build an in-memory schema-initialised store rather than following
`test_report_months.py`'s "no store" idiom. `report()` itself is covered by
one end-to-end test on the shared fixture store, following
`tests/backtest/test_run.py`'s pattern for a `kind="tracking"` run.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path

import duckdb
import pytest

from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.schedule import fill_session
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.execution.report import Journal, TrialPeriods, compare_targets, report
from tradepartner.store import registry
from tradepartner.store.db import configure_connection, open_for_write, open_read_only
from tradepartner.store.journal import DecisionRow, PaperPlanRow, PaperWindowRow, append
from tradepartner.store.schema import init_schema

WINDOW_ID = 9
A = "SEC_A"
T0 = date(2026, 9, 30)
T1 = date(2026, 10, 30)  # trial.sessions[-1]: never compared (nothing planned past it)
F0 = fill_session(T0)
_IDS = itertools.count(200)


def _utc(day: date, hour: int = 20) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _window() -> PaperWindowRow:
    frozen = {
        "paper.tracking_k": 2.0,
        "paper.tracking_rule": "raw",
        "execution.fill_price": "close",
    }
    return PaperWindowRow(
        window_id=WINDOW_ID,
        hypothesis_id=1,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=_utc(T0),
        frozen_json=json.dumps(frozen),
        frozen_sha256="0" * 64,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )


def _trial(trial_id: int = 1) -> TrialPeriods:
    # Two sessions so T0 is not `sessions[-1]` (never compared: nothing is
    # planned past a trial's last session).
    return TrialPeriods(sessions=(T0, T1), equity={}, cost_paid={}, trial_id=trial_id)


def _plan(*, store_max_ingested_at: datetime, run_id: int = 1) -> PaperPlanRow:
    return PaperPlanRow(
        run_id=run_id,
        plan_trial_id=1,
        rebalance_session=T0,
        store_max_ingested_at=store_max_ingested_at,
        n_universe=1,
        n_targets=1,
        n_orders_below_min_at_live_capital=0,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )


def _decision(target_weight: float, *, decision: str = "trade", run_id: int = 1) -> DecisionRow:
    return DecisionRow(
        decision_id=next(_IDS),
        run_id=run_id,
        rebalance_session=T0,
        security_id=A,
        target_weight=target_weight,
        whole_share=False,
        decision=decision,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )


def _insert_trial_weight(
    store: duckdb.DuckDBPyConnection, trial_id: int, fill_session_: date, weight: float
) -> None:
    store.execute(
        "INSERT INTO trial_weights (trial_id, fill_session, security_id, target_weight) "
        "VALUES (?, ?, ?, ?)",
        [trial_id, fill_session_, A, weight],
    )


def _insert_late_bar(
    store: duckdb.DuckDBPyConnection, *, known_at: datetime, ingested_at: datetime
) -> None:
    store.execute(
        "INSERT INTO prices_daily "
        "(security_id, session, open, high, low, close, volume, known_at, ingested_at, "
        "source, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [A, T0, 1.0, 1.0, 1.0, 1.0, 1, known_at, ingested_at, "alpaca", "bar"],
    )


def test_equal_weights_pass(store: duckdb.DuckDBPyConnection) -> None:
    _insert_trial_weight(store, 1, F0, 0.5)
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.5),),
        plans=(_plan(store_max_ingested_at=_utc(T0)),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert result.rows == ()


def test_weight_moved_by_hand_is_bug_naming_session_and_name(
    store: duckdb.DuckDBPyConnection,
) -> None:
    _insert_trial_weight(store, 1, F0, 0.5)
    plan_time = _utc(T0)
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.6),),  # moved by hand: trial says 0.5
        plans=(_plan(store_max_ingested_at=plan_time),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert (row.rebalance_session, row.security_id, row.difference) == (T0, A, "bug")
    assert (row.paper_weight, row.trial_weight) == (0.6, 0.5)


def test_overridden_name_is_override(store: duckdb.DuckDBPyConnection) -> None:
    _insert_trial_weight(store, 1, F0, 0.5)
    plan_time = _utc(T0)
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.6, decision="override"),),
        plans=(_plan(store_max_ingested_at=plan_time),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert len(result.rows) == 1
    assert result.rows[0].difference == "override"


def test_late_ingested_bar_is_late_data(store: duckdb.DuckDBPyConnection) -> None:
    _insert_trial_weight(store, 1, F0, 0.5)
    plan_time = _utc(T0)  # the plan's store_max_ingested_at
    # A bar known at or before close(T0) but ingested strictly after the plan's
    # cutoff: the tracking trial saw it, the paper plan could not.
    _insert_late_bar(store, known_at=session_close(T0), ingested_at=_utc(T0, hour=23))
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.6),),
        plans=(_plan(store_max_ingested_at=plan_time),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert len(result.rows) == 1
    assert result.rows[0].difference == "late_data"


def test_bar_ingested_at_exactly_the_plan_cutoff_is_not_late_data(
    store: duckdb.DuckDBPyConnection,
) -> None:
    # `ingested_at` equal to (not strictly after) the plan's `store_max_ingested_at`:
    # the plan could have seen this very row, so it is not a late-ingested one.
    _insert_trial_weight(store, 1, F0, 0.5)
    plan_time = _utc(T0)
    _insert_late_bar(store, known_at=session_close(T0), ingested_at=plan_time)
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.6),),
        plans=(_plan(store_max_ingested_at=plan_time),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert len(result.rows) == 1
    assert result.rows[0].difference == "bug"


def test_bar_known_after_the_cutoff_is_not_late_data(store: duckdb.DuckDBPyConnection) -> None:
    # Known strictly AFTER close(T0): neither paper's plan nor the tracking
    # trial could have seen this row by T0's close, so it cannot explain a
    # difference reported at T0 -- `bug`, not `late_data` (a no-look-ahead
    # boundary; quant-auditor finding on PR #525).
    _insert_trial_weight(store, 1, F0, 0.5)
    plan_time = _utc(T0)
    _insert_late_bar(
        store, known_at=session_close(T0) + timedelta(seconds=1), ingested_at=_utc(T0, hour=23)
    )
    journal = Journal(
        positions_daily=(),
        adjustments=(),
        decisions=(_decision(0.6),),
        plans=(_plan(store_max_ingested_at=plan_time),),
    )
    result = compare_targets(_window(), _trial(), journal, store)
    assert len(result.rows) == 1
    assert result.rows[0].difference == "bug"


def test_no_trial_id_raises(store: duckdb.DuckDBPyConnection) -> None:
    journal = Journal(positions_daily=(), adjustments=(), decisions=())
    trial = TrialPeriods(sessions=(T0,), equity={}, cost_paid={})  # trial_id=None
    with pytest.raises(ValueError, match="trial_id"):
        compare_targets(_window(), trial, journal, store)


# --- `report()` end to end (plan T65b) ----------------------------------------------

TRACKING_SLUG = "h-report"
IN_SAMPLE_START = date(2018, 1, 31)
HOLDOUT_START = date(2019, 6, 3)
HOLDOUT_END = date(2019, 6, 28)
TRACKING_START = date(2019, 7, 31)
TRACKING_END = date(2019, 9, 30)
REPORT_NOW = datetime(2019, 10, 15, 12, tzinfo=UTC)


def _frozen() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT_START, "end": HOLDOUT_END},
        gap={"count_share_threshold": 0.05},
    )


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _seed_tracking_window(
    conn: duckdb.DuckDBPyConnection, *, slug: str = TRACKING_SLUG, book_id: str = "main"
) -> int:
    """Register `slug` and open a window on it for `book_id`, with one flat cash
    mark and one plan per rebalance session, each through its own `paper_runs`
    row (the window readers' `_RUN_IN_WINDOW` join needs one), so
    `compare_periods`'s equity read never raises and every fill session
    `compare_targets` finds a difference for has a plan to read
    `store_max_ingested_at` from. Returns the window's id."""
    frozen = _frozen()
    hypothesis = registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title="paper report",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="2" * 64,
        params=frozen_params_of(frozen, family="momentum"),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="test",
        settings=frozen,
    )
    window_frozen = {
        "paper.tracking_k": 2.0,
        "paper.tracking_rule": "raw",
        "execution.fill_price": "close",
    }
    window_id = append(
        conn,
        PaperWindowRow(
            hypothesis_id=hypothesis.hypothesis_id,
            first_rebalance_session=TRACKING_START,
            account_id=f"PA-{book_id}",
            starting_cash=100_000.0,
            starting_equity=100_000.0,
            code_version="test",
            started_at=_utc(TRACKING_START),
            frozen_json=json.dumps(window_frozen),
            frozen_sha256="0" * 64,
            book_id=book_id,
            known_at=_utc(TRACKING_START),
            ingested_at=_utc(TRACKING_START),
        ),
    )
    assert window_id is not None
    found = conn.execute("SELECT coalesce(max(run_id), 0) + 1 FROM paper_runs").fetchone()
    assert found is not None
    first_run = int(found[0])
    for run_id, session in enumerate(
        (TRACKING_START, date(2019, 8, 30), TRACKING_END), start=first_run
    ):
        conn.execute(
            "INSERT INTO paper_runs "
            "(run_id, window_id, session, kind, started_at, invoked_by, code_version, "
            "known_at, ingested_at) "
            "VALUES (?, ?, ?, 'rebalance', ?, 'scheduler', 'test', ?, ?)",
            [run_id, window_id, session, _utc(session), _utc(session), _utc(session)],
        )
        conn.execute(
            "INSERT INTO positions_daily "
            "(run_id, session, security_id, quantity, cash, book_id, known_at, ingested_at) "
            "VALUES (?, ?, NULL, 0.0, ?, ?, ?, ?)",
            [run_id, session, 100_000.0, book_id, _utc(session), _utc(session)],
        )
        conn.execute(
            "INSERT INTO paper_plans "
            "(run_id, plan_trial_id, rebalance_session, store_max_ingested_at, "
            "n_universe, n_targets, n_orders_below_min_at_live_capital, "
            "known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                1,
                session,
                _utc(session, hour=23),
                1,
                1,
                0,
                _utc(session),
                _utc(session),
            ],
        )
    return window_id


def test_report_appends_a_paper_reports_row_naming_the_trial_and_through_session(
    fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_settings = Settings(_env_file=None, store={"path": str(fixture_store_path)})
    with open_for_write(report_settings) as conn:
        window_id = _seed_tracking_window(conn)

    import tradepartner.execution.report as report_module

    monkeypatch.setattr(report_module, "utc_now", lambda: REPORT_NOW)

    outcome = report(report_settings, partial(open_read_only, report_settings))

    assert outcome.paper_report.window_id == window_id
    assert outcome.paper_report.through_session == TRACKING_END
    with open_read_only(report_settings) as conn:
        rows = conn.execute(
            "SELECT window_id, trial_id, through_session FROM paper_reports"
        ).fetchall()
    assert len(rows) == 1
    stored_window_id, stored_trial_id, stored_through = rows[0]
    assert (stored_window_id, stored_through) == (window_id, TRACKING_END)
    assert stored_trial_id == outcome.paper_report.trial_id


def test_report_for_a_book_runs_that_books_hypothesis_never_mains(
    fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0017 B.7 (plan T156): `paper report --book b` compares `b`'s window
    against the tracking trial of `b`'s hypothesis, and the `paper_reports` row
    names `b`'s window; `main`'s window, though older, is never read. With no
    book it reads `paper.book_id`'s (`main`), though `b`'s window is newer."""
    report_settings = Settings(_env_file=None, store={"path": str(fixture_store_path)})
    with open_for_write(report_settings) as conn:
        main_window = _seed_tracking_window(conn, slug=TRACKING_SLUG, book_id="main")
        b_window = _seed_tracking_window(conn, slug="h-report-b", book_id="b")

    import tradepartner.execution.report as report_module

    monkeypatch.setattr(report_module, "utc_now", lambda: REPORT_NOW)
    slugs: list[str] = []
    real_run = report_module.run_hypothesis

    def recording_run(slug: str, *args: object, **kwargs: object) -> object:
        slugs.append(slug)
        return real_run(slug, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "run_hypothesis", recording_run)

    for_b = report(report_settings, partial(open_read_only, report_settings), "b")
    by_default = report(report_settings, partial(open_read_only, report_settings))

    assert slugs == ["h-report-b", TRACKING_SLUG]
    assert for_b.paper_report.window_id == b_window
    assert by_default.paper_report.window_id == main_window
    with open_read_only(report_settings) as conn:
        trial_hypotheses = dict(
            conn.execute(
                "SELECT r.window_id, h.slug FROM paper_reports r "
                "JOIN trials t ON t.trial_id = r.trial_id "
                "JOIN hypotheses h ON h.hypothesis_id = t.hypothesis_id"
            ).fetchall()
        )
    assert trial_hypotheses == {b_window: "h-report-b", main_window: TRACKING_SLUG}


def test_report_refuses_a_bad_book_token_before_any_trial(
    fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tradepartner.execution.report as report_module

    def no_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("no trial may run for a bad book token")

    monkeypatch.setattr(report_module, "run_hypothesis", no_run)
    report_settings = Settings(_env_file=None, store={"path": str(fixture_store_path)})
    with pytest.raises(ValueError, match="book_id must match"):
        report(report_settings, partial(open_read_only, report_settings), "b-1")

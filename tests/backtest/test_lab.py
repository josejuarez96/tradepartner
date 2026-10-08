"""The sweep runner (strategy-lab spec req 2; plan task T107; #1218).

Every case runs `run_sweep` on a temp-file copy of one lab store built once per
module: the fixture universe with the lab tables, H1's fixture twin registered
pre-lab with its family rules and run once (an `ok` trial, so the family is ready
for a sweep), and `tests/fixtures/sweeps/fixture-sweep.md` registered (four
variants: `strategy.top_fraction` x `schedule.rebalance_cadence`, so two read
groups of two, one per cadence). The twin and the sweep share a window that
fits the fixture bars (2017-01-03 to 2020-06-30): in-sample from 2018-01-31,
holdout 2019-05-01 to 2020-06-30, so the default window ends at close(2019-04-30)
at `month_end` and close(2019-04-26) at `week_end` (cutoffs differ by cadence).

The live `settings.store.path` is the copy (a non-synthetic run, as the CLI's)
unless a case passes `store_path`. The clock is a fake one starting on a
Saturday, when no configured quiet interval applies, unless a case is about
quiet intervals; the checkout reads clean (`code_version` patched), so failures
count toward terminal failure as on a clean checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import load_universe_fixtures, mark_pre_lab

from tradepartner.backtest import engine, frozen, hypothesis, lab, sweep
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.results import family_n
from tradepartner.backtest.run import run_hypothesis
from tradepartner.config import Settings
from tradepartner.store import lab_queries, lab_registry, lab_schema, registry, schema
from tradepartner.store.db import configure_connection, insert_row, open_for_write, utc_now

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SWEEP_SOURCE = FIXTURES / "sweeps" / "fixture-sweep.md"
TWIN_SOURCE = FIXTURES / "hypotheses" / "fixture-momentum.md"
SLUG = "fixture-sweep"
EDITS = (
    ("in_sample_start = 2017-01-31", "in_sample_start = 2018-01-31"),
    ("start = 2023-01-03", "start = 2019-05-01"),
    ("end = 2025-12-31", "end = 2020-06-30"),
)
#: The default window's last rebalance session per cadence (the cutoff's session).
LAST_SESSION = {"month_end": date(2019, 4, 30), "week_end": date(2019, 4, 26)}
#: A Saturday: no configured quiet interval (weekdays only) and none within a day.
SATURDAY = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
#: The registry tables a refused run must leave unchanged.
REGISTRY_TABLES = (
    "trials",
    "trial_results",
    "trial_metrics",
    "trial_equity",
    "trial_rebalances",
    "sweep_runs",
    "sweep_trials",
)
_CLEAN: tuple[str, bool] = ("test-clean", False)


class FakeClock:
    """A clock that moves only when told to or when the runner sleeps. `on_sleep`
    runs before each sleep advances it."""

    def __init__(self, start: datetime = SATURDAY) -> None:
        self.t = start
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[float], None] | None = None

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(seconds)
        self.t += timedelta(seconds=seconds)

    def advance(self, **delta: float) -> None:
        self.t += timedelta(**delta)


def _copy_file(directory: Path, source: Path) -> Path:
    text = source.read_text()
    for old, new in EDITS:
        assert old in text, old
        text = text.replace(old, new, 1)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / source.name
    path.write_text(text)
    return path


@contextmanager
def _env(path: Path, tmp: Path) -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TRADEPARTNER_ENV_FILE", str(tmp / "none.env"))
        mp.setenv("STORE__PATH", str(path))
        yield mp


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The lab store every case copies (module docstring)."""
    directory = tmp_path_factory.mktemp("lab-template")
    path = directory / "lab.duckdb"
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, FIXTURES / "universe")
        lab_schema.apply_lab_schema(conn)
    finally:
        conn.close()
    settings = Settings(_env_file=None)
    with _env(path, directory):
        with open_for_write(Settings()) as conn:
            # Straight through the registry, as H1 was registered before the lab:
            # `hypothesis.register` refuses a plain standalone file on a lab store
            # (T104c).
            twin_file = _copy_file(directory / "files", TWIN_SOURCE)
            parsed = hypothesis.parse_file(twin_file)
            twin = registry.register_hypothesis(
                conn,
                slug=parsed.slug,
                family=parsed.family,
                title=parsed.title,
                doc_path=twin_file.as_posix(),
                doc_sha256=parsed.doc_sha256,
                params=hypothesis.frozen_params(parsed, settings),
                in_sample_start=parsed.in_sample_start,
                holdout_start=parsed.holdout_start,
                holdout_end=parsed.holdout_end,
                registered_by="test",
                settings=settings,
            )
            mark_pre_lab(conn, twin.hypothesis_id)
            values = frozen.frozen_values(twin)
            lab_registry.write_fingerprint(
                conn,
                twin.hypothesis_id,
                frozen.fingerprint(twin.family, values, twin.in_sample_start),
            )
            lab_registry.write_family_rules(
                conn,
                family=twin.family,
                first_hypothesis_id=twin.hypothesis_id,
                parent_family=None,
                holdout_start=twin.holdout_start,
                holdout_end=twin.holdout_end,
                in_sample_start=twin.in_sample_start,
                fixed_params=sweep.family_rule_params(values),
                sr_star_seed_annual=None,
                settings=settings,
            )
        assert run_hypothesis(twin.slug, None, None, Flags(), run_by="test").status == "ok"
        with open_for_write(Settings()) as conn:
            sweep.register(
                conn,
                _copy_file(directory / "files", SWEEP_SOURCE),
                settings,
                registered_by="test",
            )
    return path


@pytest.fixture
def store(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh copy of the template, the live store, read as a clean checkout."""
    path = tmp_path / "lab.duckdb"
    shutil.copy(template, path)
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setattr(registry, "code_version", lambda repo_dir=None: _CLEAN)
    monkeypatch.setattr(lab_registry, "code_version", lambda repo_dir=None: _CLEAN)
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@contextmanager
def _read(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def _sweep_id(path: Path) -> int:
    with _read(path) as conn:
        record = lab_registry.sweep_by_slug(conn, SLUG)
        assert record is not None
        return record.sweep_id


def _variants(path: Path) -> list[tuple[int, int, str, float]]:
    """(variant index, hypothesis id, cadence, top_fraction) in canonical order."""
    with _read(path) as conn:
        rows = []
        for variant in lab_registry.sweep_variants(conn, _sweep_id(path)):
            values = frozen.frozen_values(
                registry.get_hypothesis_by_id(conn, variant.hypothesis_id)
            )
            rows.append(
                (
                    variant.variant_index,
                    variant.hypothesis_id,
                    values["schedule.rebalance_cadence"],
                    values["strategy.top_fraction"],
                )
            )
        return rows


def _counts(path: Path) -> dict[str, int]:
    with _read(path) as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # type: ignore[index]
            for table in REGISTRY_TABLES
        }


def _n(path: Path) -> int:
    with _read(path) as conn:
        return family_n(conn, "momentum")


def _state(path: Path) -> lab_queries.SweepState:
    with _read(path) as conn:
        return lab_queries.sweep_state(conn, _sweep_id(path))


def _insert_bar(path: Path, session: date, known_at: datetime) -> None:
    """One bar for `session`, known at `known_at`, ingested now, of a security no
    variant reads: it moves the data vintage at every cutoff at or after
    `known_at` and nothing else."""
    with open_for_write(Settings(_env_file=None, store={"path": str(path)})) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "SEC_LAB_TEST",
                "session": session,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": known_at,
                "ingested_at": utc_now(),
                "source": "alpaca",
                "provenance": "bar",
            },
        )


def _close(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 20, 0, tzinfo=UTC)


def _by_group(outcome: lab.SweepRunOutcome) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for trial in outcome.trials:
        groups.setdefault(trial.read_group_index, []).append(trial.variant_index)
    return groups


# --- sweep run and accounting ---------------------------------------------------


def test_a_plain_run_runs_every_variant_then_nothing_then_rerun_runs_all(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB__REGISTRY_SIZE_WARN_GB", "0.000001")
    events: list[tuple[str, int]] = []
    real_open = registry.open_trial

    def recording_open(*args: Any, **kwargs: Any) -> registry.TrialHandle:
        handle = real_open(*args, **kwargs)
        events.append(("open", handle.trial_id))
        return handle

    real_run_many = engine.run_many

    def recording_run_many(variants: Any, *args: Any, **kwargs: Any) -> Any:
        events.append(("run", len(variants)))
        return real_run_many(variants, *args, **kwargs)

    monkeypatch.setattr(registry, "open_trial", recording_open)
    monkeypatch.setattr(engine, "run_many", recording_run_many)
    variants = _variants(store)
    n_before = _n(store)

    first = lab.run_sweep(SLUG, clock=clock)

    assert (first.n_declared, first.n_planned, first.n_ok, first.n_failed) == (4, 4, 4, 0)
    assert first.completed and not first.stopped_by_budget and not first.synthetic
    assert first.warnings and "registry_size_warn_gb" in first.warnings[0]
    # Read groups by cadence, ordered by their lowest canonical index, canonical within.
    cadence = {index: c for index, _, c, _ in variants}
    groups = _by_group(first)
    assert [sorted(members) for members in groups.values()] == list(groups.values())
    assert all(len({cadence[i] for i in members}) == 1 for members in groups.values())
    assert [min(members) for members in groups.values()] == sorted(
        min(members) for members in groups.values()
    )
    # Every variant's handle is opened before its group's first provider call.
    assert [kind for kind, _ in events] == ["open", "open", "run", "open", "open", "run"]
    assert _n(store) == n_before + 4
    with _read(store) as conn:
        trials = conn.execute(
            "SELECT t.trial_id, t.synthetic, t.kind, t.data_vintage, t.code_tree_sha256, "
            "t.data_cutoff, r.status FROM trials t JOIN trial_results r USING (trial_id) "
            "WHERE t.trial_id IN (SELECT trial_id FROM sweep_trials)"
        ).fetchall()
        assert len(trials) == 4
        assert all(not t[1] and t[2] == "in_sample" and t[6] == "ok" for t in trials)
        assert all(t[3] is not None and t[4] is not None for t in trials)
        run = conn.execute(
            "SELECT n_declared, n_planned, n_ok, n_failed, n_terminal_failed, seconds, "
            "n_trials_at_end, sr_star_annual_at_end, completed, finished_at "
            "FROM sweep_runs WHERE sweep_run_id = ?",
            [first.sweep_run_id],
        ).fetchone()
        assert run is not None
        assert run[:5] == (4, 4, 4, 0, 0)
        assert run[5] is not None and run[6] == n_before + 4 and run[7] is not None
        assert run[8] is True and run[9] is not None
        sweep_trials = conn.execute(
            "SELECT COUNT(*), MIN(seconds) FROM sweep_trials WHERE sweep_run_id = ?",
            [first.sweep_run_id],
        ).fetchone()
        assert sweep_trials is not None and sweep_trials[0] == 4 and sweep_trials[1] is not None
        sharpes = registry.family_sharpes(conn, "momentum")
        assert len(sharpes.excess_spy) == 5  # the twin's pair and the four variants'

    second = lab.run_sweep(SLUG, clock=clock)
    assert (second.n_planned, second.n_ok, second.trials) == (0, 0, ())
    assert second.completed
    assert _n(store) == n_before + 4

    third = lab.run_sweep(SLUG, rerun=True, clock=clock)
    assert (third.n_planned, third.n_ok) == (4, 4)
    assert third.completed
    assert _n(store) == n_before + 8
    with _read(store) as conn:
        assert len(registry.family_sharpes(conn, "momentum").excess_spy) == 5


def test_a_store_path_run_is_synthetic_leaves_n_unchanged_and_completes(
    store: Path, clock: FakeClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    n_before = _n(store)
    outcome = lab.run_sweep(SLUG, store_path=store, clock=clock)
    assert outcome.synthetic and outcome.n_ok == 4 and outcome.completed
    with _read(store) as conn:
        flags = conn.execute(
            "SELECT DISTINCT synthetic FROM trials "
            "WHERE trial_id IN (SELECT trial_id FROM sweep_trials)"
        ).fetchall()
    assert flags == [(True,)]
    assert _n(store) == n_before
    # #1218: the synthetic trials count for the store_path run's own planning.
    again = lab.run_sweep(SLUG, store_path=store, clock=clock)
    assert again.n_planned == 0 and again.completed


def test_an_unmarked_store_path_is_refused_before_any_trial(
    store: Path, clock: FakeClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A lab store with no `store_markers` fixture row: built without the fixture
    # loader (the only writer of the row), as a copy of the real store would be.
    unmarked = tmp_path / "unmarked.duckdb"
    conn = duckdb.connect(str(unmarked))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        lab_schema.apply_lab_schema(conn)
    finally:
        conn.close()
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    before = _counts(unmarked)
    with pytest.raises(registry.UnmarkedStoreRefused):
        lab.run_sweep(SLUG, store_path=unmarked, clock=clock)
    assert _counts(unmarked) == before


def test_a_plain_fixture_store_raises_lab_not_initialised(
    fixture_store_path: Path, clock: FakeClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(fixture_store_path))
    with pytest.raises(lab_schema.LabNotInitialised):
        lab.run_sweep(SLUG, clock=clock)


def test_the_size_guard_refuses_with_every_table_unchanged(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB__REGISTRY_SIZE_REFUSE_GB", "0.000001")
    before = _counts(store)
    with pytest.raises(lab.RegistryTooLarge):
        lab.run_sweep(SLUG, clock=clock)
    assert _counts(store) == before


# --- failure isolation and terminal failure ----------------------------------------


def test_a_variant_failure_is_isolated_and_becomes_terminal_then_rerun_counts_fresh(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, _, cadence, top = next(v for v in _variants(store) if v[0] == 3)
    real_plan = engine._plan

    def failing_plan(view: Any, settings: Settings, *args: Any, **kwargs: Any) -> Any:
        if settings.schedule.rebalance_cadence == cadence and settings.strategy.top_fraction == top:
            raise RuntimeError("injected variant error")
        return real_plan(view, settings, *args, **kwargs)

    monkeypatch.setattr(engine, "_plan", failing_plan)
    first = lab.run_sweep(SLUG, clock=clock)
    status = {t.variant_index: (t.status, t.message) for t in first.trials}
    assert status[index] == ("failed", "RuntimeError: injected variant error")
    assert all(s == ("ok", None) for i, s in status.items() if i != index)
    assert not first.completed and first.n_terminal_failed == 0

    second = lab.run_sweep(SLUG, clock=clock)
    assert [t.variant_index for t in second.trials] == [index]
    assert second.n_terminal_failed == 1 and second.completed
    state = _state(store)
    assert [v.variant_index for v in state.terminal_failed] == [index]
    assert lab.run_sweep(SLUG, clock=clock).n_planned == 0

    # --rerun gives the terminal-failed variant a fresh count; it now succeeds.
    monkeypatch.setattr(engine, "_plan", real_plan)
    rerun = lab.run_sweep(SLUG, rerun=True, clock=clock)
    assert rerun.n_ok == 4 and rerun.completed and rerun.n_terminal_failed == 0
    assert _state(store).terminal_failed == ()


def test_a_shared_read_failure_fails_every_open_variant_and_never_turns_terminal(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected shared read error")

    monkeypatch.setattr(lab._PausingProvider, "listing_ends", failing)
    for _ in range(3):
        outcome = lab.run_sweep(SLUG, clock=clock)
        assert outcome.n_planned == 4
        assert {(t.status, t.message) for t in outcome.trials} == {
            ("failed", lab_queries.SHARED_READ_FAILED)
        }
        assert all("injected shared read error" in error for error in outcome.errors.values())
    state = _state(store)
    assert state.terminal_failed == () and state.state == "incomplete (unrun)"


def test_an_in_window_fact_mid_group_fails_that_group_and_the_next_run_resumes(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = lab._PausingProvider.listing_ends
    inserted: list[bool] = []

    def inserting(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not inserted:
            inserted.append(True)
            self.end_step()
            _insert_bar(store, date(2018, 6, 1), _close(date(2018, 6, 1)))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(lab._PausingProvider, "listing_ends", inserting)
    first = lab.run_sweep(SLUG, clock=clock)
    groups = _by_group(first)
    status = {t.variant_index: (t.status, t.message) for t in first.trials}
    first_group, second_group = list(groups.values())
    assert {status[i] for i in first_group} == {("failed", registry.STORE_CHANGED_MESSAGE)}
    assert {status[i] for i in second_group} == {("ok", None)}

    monkeypatch.setattr(lab._PausingProvider, "listing_ends", real)
    second = lab.run_sweep(SLUG, clock=clock)
    assert sorted(t.variant_index for t in second.trials) == sorted(first_group)
    assert second.n_ok == 2 and second.completed


def test_a_variants_own_failure_on_changed_data_is_store_changed_not_its_error(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, cadence, top = next(v for v in _variants(store) if v[0] == 1)
    real_plan = engine._plan
    real = lab._PausingProvider.listing_ends
    inserted: list[bool] = []

    def failing_plan(view: Any, settings: Settings, *args: Any, **kwargs: Any) -> Any:
        if settings.schedule.rebalance_cadence == cadence and settings.strategy.top_fraction == top:
            raise RuntimeError("injected variant error")
        return real_plan(view, settings, *args, **kwargs)

    def inserting(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not inserted:
            inserted.append(True)
            self.end_step()
            _insert_bar(store, date(2018, 6, 1), _close(date(2018, 6, 1)))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(engine, "_plan", failing_plan)
    monkeypatch.setattr(lab._PausingProvider, "listing_ends", inserting)
    outcome = lab.run_sweep(SLUG, clock=clock)
    status = {t.variant_index: t.message for t in outcome.trials}
    assert status[1] == registry.STORE_CHANGED_MESSAGE


def test_an_interrupt_mid_group_closes_the_run_and_leaves_its_trials_unfinished(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "run_many", interrupted)
    with pytest.raises(KeyboardInterrupt):
        lab.run_sweep(SLUG, clock=clock)
    with _read(store) as conn:
        row = conn.execute(
            "SELECT finished_at IS NOT NULL, completed FROM sweep_runs "
            "ORDER BY sweep_run_id DESC LIMIT 1"
        ).fetchone()
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM trials WHERE trial_id NOT IN (SELECT trial_id FROM trial_results)"
        ).fetchone()
    assert row == (True, False)
    assert unfinished == (2,)
    monkeypatch.undo()
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store.parent / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(store))
    monkeypatch.setattr(registry, "code_version", lambda repo_dir=None: _CLEAN)
    monkeypatch.setattr(lab_registry, "code_version", lambda repo_dir=None: _CLEAN)
    assert lab.run_sweep(SLUG, clock=clock).n_planned == 4


@pytest.mark.xfail(
    strict=True,
    reason="#1232: registry.write_result still fails a run on any ingest (Phase 3 rule)",
)
def test_a_row_after_the_cutoff_inserted_mid_group_fails_nothing(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = lab._PausingProvider.listing_ends
    inserted: list[bool] = []

    def inserting(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not inserted:
            inserted.append(True)
            self.end_step()
            _insert_bar(store, date(2020, 7, 1), _close(date(2020, 7, 1)))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(lab._PausingProvider, "listing_ends", inserting)
    outcome = lab.run_sweep(SLUG, clock=clock)
    assert outcome.n_ok == 4


def test_a_one_group_budget_stops_at_the_group_boundary(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run_many = engine.run_many

    def slow_run_many(*args: Any, **kwargs: Any) -> Any:
        clock.advance(minutes=60)
        return real_run_many(*args, **kwargs)

    monkeypatch.setattr(engine, "run_many", slow_run_many)
    outcome = lab.run_sweep(SLUG, time_budget_minutes=30, clock=clock)
    assert outcome.stopped_by_budget and not outcome.completed
    assert outcome.n_planned == 4 and outcome.n_ok == 2
    with _read(store) as conn:
        row = conn.execute(
            "SELECT n_ok, completed FROM sweep_runs WHERE sweep_run_id = ?",
            [outcome.sweep_run_id],
        ).fetchone()
    assert row == (2, False)
    with pytest.raises(lab_queries.SweepNotCompleteError):
        lab.run_sweep(SLUG, rerun=True, clock=clock)


# --- vintage and resumption -----------------------------------------------------


def test_the_stale_and_rerun_cycle(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    n_before = _n(store)
    real_ready = lab._ready_to_start
    calls: list[int] = []

    def insert_between_groups(*args: Any, **kwargs: Any) -> bool:
        calls.append(1)
        if len(calls) == 2:
            _insert_bar(store, date(2018, 6, 1), _close(date(2018, 6, 1)))
        return real_ready(*args, **kwargs)

    monkeypatch.setattr(lab, "_ready_to_start", insert_between_groups)
    first = lab.run_sweep(SLUG, clock=clock)
    assert first.n_ok == 4 and not first.completed
    first_group = next(iter(_by_group(first).values()))
    state = _state(store)
    assert state.state == "incomplete (stale)"
    assert sorted(v.variant_index for v in state.stale) == sorted(first_group)
    monkeypatch.setattr(lab, "_ready_to_start", real_ready)

    second = lab.run_sweep(SLUG, clock=clock)
    assert sorted(t.variant_index for t in second.trials) == sorted(first_group)
    assert second.completed
    assert _n(store) == n_before + 6  # the stale trials still count in N

    # A --rerun stopped by the budget after one group is finished by a plain run.
    real_run_many = engine.run_many

    def slow_run_many(*args: Any, **kwargs: Any) -> Any:
        clock.advance(minutes=60)
        return real_run_many(*args, **kwargs)

    monkeypatch.setattr(engine, "run_many", slow_run_many)
    stopped = lab.run_sweep(SLUG, rerun=True, time_budget_minutes=30, clock=clock)
    assert stopped.stopped_by_budget and stopped.n_ok == 2 and not stopped.completed
    rerun_group = {t.variant_index for t in stopped.trials}
    awaiting = _state(store)
    assert awaiting.state == "incomplete (stale)"
    assert {v.variant_index for v in awaiting.stale} == {1, 2, 3, 4} - rerun_group
    monkeypatch.setattr(engine, "run_many", real_run_many)
    finishing = lab.run_sweep(SLUG, clock=clock)
    assert {t.variant_index for t in finishing.trials} == {1, 2, 3, 4} - rerun_group
    assert finishing.completed


def test_a_cadence_axis_sweep_is_current_at_each_variants_own_cutoff(
    store: Path, clock: FakeClock
) -> None:
    outcome = lab.run_sweep(SLUG, clock=clock)
    assert outcome.completed
    cadence = {index: c for index, _, c, _ in _variants(store)}
    with _read(store) as conn:
        for trial in outcome.trials:
            (cutoff,) = conn.execute(  # type: ignore[misc]
                "SELECT data_cutoff FROM trials WHERE trial_id = ?", [trial.trial_id]
            ).fetchone()
            assert cutoff.date() == LAST_SESSION[cadence[trial.variant_index]]
    # A fact known between the two cutoffs moves only the later (month_end) cutoff.
    _insert_bar(store, date(2019, 4, 29), _close(date(2019, 4, 29)))
    state = _state(store)
    stale = {v.variant_index for v in state.stale}
    assert stale == {i for i, c in cadence.items() if c == "month_end"}
    rerun = lab.run_sweep(SLUG, clock=clock)
    assert {t.variant_index for t in rerun.trials} == stale and rerun.completed


def test_a_code_vintage_change_reruns_every_variant(
    store: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert lab.run_sweep(SLUG, clock=clock).completed
    moved = "f" * 64
    monkeypatch.setattr(registry, "code_tree_sha256", lambda repo_dir=None: moved)
    monkeypatch.setattr(lab_queries, "code_tree_sha256", lambda repo_dir=None: moved)
    assert len(_state(store).stale) == 4
    outcome = lab.run_sweep(SLUG, clock=clock)
    assert outcome.n_ok == 4 and outcome.completed


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_code_tree_sha256_moves_with_source_and_lock_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "tradepartner").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "tradepartner" / "mod.py").write_text("x = 1\n")
    (repo / "docs" / "note.md").write_text("doc\n")
    (repo / "tests" / "test_mod.py").write_text("def test(): pass\n")
    (repo / "uv.lock").write_text("lock 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    base = registry.code_tree_sha256(repo)
    assert base is not None
    (repo / "docs" / "note.md").write_text("doc changed\n")
    (repo / "tests" / "test_mod.py").write_text("def test(): assert True\n")
    cache = repo / "src" / "tradepartner" / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
    assert registry.code_tree_sha256(repo) == base
    (repo / "src" / "tradepartner" / "mod.py").write_text("x = 2\n")
    source_moved = registry.code_tree_sha256(repo)
    assert source_moved != base
    (repo / "src" / "tradepartner" / "mod.py").write_text("x = 1\n")
    (repo / "uv.lock").write_text("lock 2\n")
    assert registry.code_tree_sha256(repo) not in (base, source_moved)


# --- quiet intervals --------------------------------------------------------------


def _quiet(monkeypatch: pytest.MonkeyPatch, seconds_per_variant: float) -> None:
    monkeypatch.setenv("LAB__QUIET_INTERVALS", '[["16:00", "21:00"]]')
    monkeypatch.setenv("LAB__QUIET_WEEKDAYS", "[0, 1, 2, 3, 4]")
    monkeypatch.setenv("LAB__QUIET_TIMEZONE", "America/New_York")
    value = str(seconds_per_variant)
    monkeypatch.setenv(
        "LAB__SECONDS_PER_VARIANT_DEFAULT",
        f'{{"month_end": {value}, "week_end": {value}, "daily": {value}}}',
    )


#: Wednesday 2026-10-07, 15:00 in New York (EDT): an hour before the interval.
WEDNESDAY_1500_NY = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)
INTERVAL_END = datetime(2026, 10, 8, 1, 0, tzinfo=UTC)  # 21:00 EDT


class _Connections:
    """A recording connection factory: how many read-only connections are open."""

    def __init__(self) -> None:
        self.open = 0
        self.opened = 0


def _recording_connections(monkeypatch: pytest.MonkeyPatch) -> _Connections:
    record = _Connections()
    real = lab.open_read_only

    @contextmanager
    def recording(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
        record.open += 1
        record.opened += 1
        try:
            with real(settings) as conn:
                yield conn
        finally:
            record.open -= 1

    monkeypatch.setattr(lab, "open_read_only", recording)
    return record


def test_a_group_running_into_an_interval_pauses_with_no_connection_or_row(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch, seconds_per_variant=60.0)
    clock = FakeClock(WEDNESDAY_1500_NY)
    connections = _recording_connections(monkeypatch)
    real_at = lab.StoreProvider._at

    paused: list[tuple[int, dict[str, int]]] = []
    resumed: list[dict[str, int]] = []

    def stepping(self: Any, t: datetime) -> Any:
        if self._conn is None or self._step_t != t:
            if len(resumed) < len(paused):
                resumed.append(_counts(store))  # the first step after a pause
            clock.advance(minutes=2)  # each step takes two minutes
        return real_at(self, t)

    monkeypatch.setattr(lab.StoreProvider, "_at", stepping)

    def on_sleep(seconds: float) -> None:
        paused.append((connections.open, _counts(store)))

    clock.on_sleep = on_sleep
    outcome = lab.run_sweep(SLUG, clock=clock)
    assert outcome.n_ok == 4 and outcome.completed
    assert paused, "the run never paused"
    assert len(paused) == len(resumed) == 1
    open_connections, at_pause = paused[0]
    assert open_connections == 0
    assert resumed[0] == at_pause  # nothing was written while paused
    # The pause fell inside the second group: its trials open, no result written.
    assert at_pause["trials"] - at_pause["trial_results"] == 2
    assert clock.now() >= INTERVAL_END


def test_a_group_that_would_end_inside_an_interval_waits_for_it_to_end(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch, seconds_per_variant=45 * 60.0)  # 90 minutes per group of two
    clock = FakeClock(WEDNESDAY_1500_NY)
    opened_at: list[datetime] = []
    real_open = registry.open_trial

    def recording_open(*args: Any, **kwargs: Any) -> registry.TrialHandle:
        opened_at.append(clock.now())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(registry, "open_trial", recording_open)
    outcome = lab.run_sweep(SLUG, clock=clock)
    assert outcome.n_ok == 4
    assert opened_at and min(opened_at) >= INTERVAL_END


def test_a_group_longer_than_the_longest_gap_starts_at_once(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch, seconds_per_variant=10 * 24 * 3600.0)
    clock = FakeClock(WEDNESDAY_1500_NY)
    opened_at: list[datetime] = []
    real_open = registry.open_trial

    def recording_open(*args: Any, **kwargs: Any) -> registry.TrialHandle:
        opened_at.append(clock.now())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(registry, "open_trial", recording_open)
    outcome = lab.run_sweep(SLUG, time_budget_minutes=60 * 24 * 30, clock=clock)
    assert outcome.n_ok == 4
    assert opened_at[0] == WEDNESDAY_1500_NY


# --- text check -------------------------------------------------------------------

#: The SQL verbs as SQL writes them (upper case, a whole word); prose such as "drops"
#: or "deletes" in a docstring is not SQL.
_DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|TRUNCATE|VACUUM)\b")
_NO_DESTRUCTIVE_SQL = (
    "backtest/lab.py",
    "backtest/sweep_report.py",
    "backtest/promotion.py",
    "backtest/quiet.py",
    "store/lab_registry.py",
    "store/lab_queries.py",
    "store/registry.py",
)


@pytest.mark.parametrize("relative", _NO_DESTRUCTIVE_SQL)
def test_no_lab_module_drops_deletes_truncates_or_vacuums(relative: str) -> None:
    path = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / relative
    if not path.exists():
        pytest.skip(f"{relative} is not built yet")
    found = _DESTRUCTIVE.findall(path.read_text())
    assert found == [], f"{relative} contains {found}"

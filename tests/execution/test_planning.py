"""The planning step (Phase 4 spec req 7 step 6, Definitions > Plan trial; plan T63c).

On a temp-file copy of the fixture store with a registered hypothesis, one
open window and a seeded journal, never a broker: the `assets` read is a
plain callable. T_i is 2019-04-30 (six members, three targets on the fixture
universe with `strategy.top_fraction = 0.5`), the first rebalance after the
frozen `holdout.end` of 2019-03-29; its fill session F_i is 2019-05-01. The
ledger holds `SEC_SPY` (never a member: a `left_universe` exit) and
`SEC_DUAL_A` (a member outside the targets: `left_targets`) through two
`spinoff_receipt` adjustments.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.backtest import engine
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.schedule import fill_session, read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import next_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, LimitBreachError, StaleDataError
from tradepartner.execution import planning, reconcile_run, wrapper
from tradepartner.execution import window as window_module
from tradepartner.execution.planning import (
    PlanTrialError,
    due_rebalance,
    frozen_max_catch_up_sessions,
    plan_rebalance,
    rebalance_kind,
)
from tradepartner.store import journal, registry
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    JournalRow,
    OrderEventRow,
    OrderRow,
    OverrideRow,
    PaperPlanRow,
    PaperRunRow,
    PaperWindowRow,
    RebalanceEventRow,
)

PLANNING_PY = (
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "execution" / "planning.py"
)

T_I = date(2019, 4, 30)
F_I = fill_session(T_I)  # 2019-05-01
T_NEXT = date(2019, 5, 31)
HOLDOUT_START, HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)
IN_SAMPLE_START = date(2017, 1, 3)
MAX_CATCH_UP = 2
STARTING_CASH = 1000.0
LIVE_CAPITAL = 50.0
MIN_ORDER = 10.0
FROZEN = RiskConfig(min_order_notional=MIN_ORDER)
HELD = {"SEC_SPY": 2.0, "SEC_DUAL_A": 1.0}
PLAN_ROWS = ("trials", "trial_results", "signals", "decisions", "paper_plans")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _utc(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _frozen_settings() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT_START.isoformat(), "end": HOLDOUT_END.isoformat()},
    )


def _frozen_json() -> str:
    values: dict[str, Any] = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
    values["paper.max_catch_up_sessions"] = MAX_CATCH_UP
    return json.dumps(values, sort_keys=True)


def _asset(fractionable: bool = True) -> Asset:
    return Asset(tradable=True, fractionable=fractionable, status="active", cusip=None)


class AssetsRead:
    """A recording `assets` read answering every symbol."""

    def __init__(self, *, whole_share: Sequence[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self.whole_share = set(whole_share)

    def __call__(self, symbols: Sequence[str]) -> dict[str, Asset]:
        self.calls.append(list(symbols))
        return {s: _asset(s not in self.whole_share) for s in symbols}


@dataclass
class Env:
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    params: Settings
    window: PaperWindowRow
    run: PaperRunRow
    hypothesis_id: int

    def run_on(self, session: date) -> PaperRunRow:
        stamp = _utc(session, 12)
        row = PaperRunRow(
            window_id=self.window.window_id,  # type: ignore[arg-type]
            session=session,
            kind="catch_up",
            started_at=stamp,
            invoked_by="tty",
            code_version="test",
            known_at=stamp,
            ingested_at=stamp,
        )
        run_id = journal.append(self.conn, row)
        return replace(row, run_id=run_id)

    def plan(
        self,
        session: date = F_I,
        *,
        run: PaperRunRow | None = None,
        assets_read: Callable[[Sequence[str]], Mapping[str, Asset]] | None = None,
        lagging: bool = False,
        journal_: planning.Journal = journal,
    ) -> planning.PlanOutcome:
        return plan_rebalance(
            self.conn,
            journal_,
            self.window,
            run or self.run,
            session,
            self.settings,
            FROZEN,
            assets_read or AssetsRead(),
            lagging,
            now=_utc(session, 12),
        )

    def counts(self) -> dict[str, int]:
        return {
            table: self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # type: ignore[index]
            for table in PLAN_ROWS
        }


@pytest.fixture
def env(fixture_store_path: Path, tmp_path: Path) -> Iterator[Env]:
    conn = duckdb.connect(str(fixture_store_path))
    configure_connection(conn)
    live = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "not-the-fixture.duckdb")},
        paper={"live_capital_reference": LIVE_CAPITAL},
    )
    params = _frozen_settings()
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-paper-plan",
        family="momentum",
        title="planning step test",
        doc_path="docs/hypotheses/h-paper-plan.md",
        doc_sha256="0" * 64,
        params=frozen_params_of(params),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="test",
        settings=live,
    )
    start = _utc(T_I, 12)
    window = PaperWindowRow(
        hypothesis_id=hypothesis.hypothesis_id,
        first_rebalance_session=T_I,
        account_id="PA1",
        starting_cash=STARTING_CASH,
        starting_equity=STARTING_CASH,
        code_version="test",
        started_at=start,
        frozen_json=_frozen_json(),
        frozen_sha256="0" * 64,
        known_at=start,
        ingested_at=start,
    )
    window = replace(window, window_id=journal.append(conn, window))
    stamp = _utc(F_I, 12)
    run = PaperRunRow(
        window_id=window.window_id,  # type: ignore[arg-type]
        session=F_I,
        kind="rebalance",
        started_at=stamp,
        invoked_by="tty",
        code_version="test",
        known_at=stamp,
        ingested_at=stamp,
    )
    run = replace(run, run_id=journal.append(conn, run))
    for sid, quantity in HELD.items():
        journal.append(
            conn,
            AdjustmentRow(
                window_id=window.window_id,  # type: ignore[arg-type]
                session=T_I,
                kind="spinoff_receipt",
                security_id=sid,
                quantity=quantity,
                known_at=start,
                ingested_at=start,
            ),
        )
    try:
        yield Env(conn, live, params, window, run, hypothesis.hypothesis_id)
    finally:
        conn.close()


def _decisions(outcome: planning.PlanOutcome) -> dict[str, DecisionRow]:
    return {d.security_id: d for d in outcome.decisions}


def _plan_on_store(env: Env) -> engine.Plan:
    """`engine.plan` at T_i under a trial of its own, on a rolled-back transaction."""
    env.conn.begin()
    try:
        handle = registry.open_trial(
            env.conn,
            hypothesis_id=env.hypothesis_id,
            kind="tracking",
            start_session=T_I,
            end_session=T_I,
            data_cutoff=read_time(T_I),
            synthetic=False,
            run_by="test",
            settings=env.settings,
        )
        with StoreProvider(lambda: _Lend(env.conn), handle, env.params) as provider:
            return engine.plan(provider, env.params, T_I)
    finally:
        env.conn.rollback()


class _Lend:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        return self.conn

    def __exit__(self, *exc: object) -> None:
        return None


# --- the planning step ---------------------------------------------------------------


def test_a_plan_journals_signals_decisions_and_the_plan_row(env: Env) -> None:
    before = env.counts()
    outcome = env.plan()
    assert (outcome.status, outcome.rebalance_session) == ("planned", T_I)
    after = env.counts()
    assert after["trials"] == before["trials"] + 1
    assert after["trial_results"] == before["trial_results"] + 1
    plan = _plan_on_store(env)
    signals = journal.signals_for(env.conn, env.run.run_id)  # type: ignore[arg-type]
    assert sorted(s.security_id for s in signals) == sorted(plan.members)
    assert len(signals) == len(plan.members) == 6
    decided = _decisions(outcome)
    assert set(decided) == set(plan.targets) | set(HELD)
    assert all(d.decision_id is not None and d.run_id == env.run.run_id for d in decided.values())
    spy, dual_a = decided["SEC_SPY"], decided["SEC_DUAL_A"]
    assert (spy.decision, spy.side, spy.reason) == ("trade", "sell", "left_universe")
    assert spy.planned_quantity == HELD["SEC_SPY"]
    assert (dual_a.decision, dual_a.side, dual_a.reason) == ("trade", "sell", "left_targets")
    for sid in plan.targets:
        assert (decided[sid].side, decided[sid].decision) == ("buy", "trade")
        assert decided[sid].target_notional is not None
    journaled = journal.decisions_for(env.conn, env.window.window_id, rebalance_session=T_I)  # type: ignore[arg-type]
    assert [d.decision for d in journaled] == list(outcome.decisions)


def test_the_plan_trial_is_tracking_closed_ok_with_no_metrics(env: Env) -> None:
    sharpes = registry.family_sharpes(env.conn, "momentum")
    outcome = env.plan()
    trial_id = outcome.plan_trial_id
    kind, start, end, cutoff = env.conn.execute(
        "SELECT kind, start_session, end_session, data_cutoff FROM trials WHERE trial_id = ?",
        [trial_id],
    ).fetchone()  # type: ignore[misc]
    assert (kind, start, end) == ("tracking", T_I, T_I)
    assert cutoff == read_time(T_I)
    status, n_trials, dsr = env.conn.execute(
        "SELECT status, n_trials, dsr FROM trial_results WHERE trial_id = ?", [trial_id]
    ).fetchone()  # type: ignore[misc]
    assert (status, n_trials, dsr) == ("ok", None, None)
    for table in ("trial_metrics", "trial_equity", "trial_weights", "trial_rebalances"):
        count = env.conn.execute(
            f"SELECT count(*) FROM {table} WHERE trial_id = ?", [trial_id]
        ).fetchone()
        assert count == (0,), table
    assert registry.family_sharpes(env.conn, "momentum") == sharpes
    [plan_row] = journal.plans_for(env.conn, env.window.window_id)  # type: ignore[arg-type]
    assert plan_row.plan_trial_id == trial_id


def test_the_paper_targets_equal_the_backtest_targets(env: Env) -> None:
    """The oracle: the plan's target weights are `engine.run`'s `targets[F_i]` on the
    same store and frozen settings."""
    outcome = env.plan()
    paper = {d.security_id: d.target_weight for d in outcome.decisions if d.target_weight}
    env.conn.begin()
    try:
        handle = registry.open_trial(
            env.conn,
            hypothesis_id=env.hypothesis_id,
            kind="tracking",
            start_session=T_I,
            end_session=T_NEXT,
            data_cutoff=read_time(T_NEXT),
            synthetic=True,
            run_by="test",
            settings=env.settings,
        )
        with StoreProvider(lambda: _Lend(env.conn), handle, env.params) as provider:
            results = engine.run(env.params, provider, T_I, T_NEXT, handle, (0.0,))
    finally:
        env.conn.rollback()
    backtest = dict(results[0.0].targets[F_I])
    assert paper == pytest.approx(backtest)
    assert len(paper) == 3


def test_the_live_capital_count_matches_a_hand_count(env: Env) -> None:
    """Equity is the starting cash plus the held names at their reference prices;
    every order's notional scaled to the live capital is counted below the minimum."""
    outcome = env.plan()
    held_prices = planning.reference_prices(env.conn, F_I, set(HELD), journal_actions(env))
    equity = STARTING_CASH + sum(HELD[sid] * held_prices[sid] for sid in HELD)
    hand = 0
    for d in outcome.decisions:
        if d.side is None:
            continue
        notional = (
            d.planned_notional
            if d.planned_notional is not None
            else (d.planned_quantity or 0.0) * held_prices[d.security_id]
        )
        hand += notional * LIVE_CAPITAL / equity < MIN_ORDER
    assert hand >= 1
    [plan_row] = journal.plans_for(env.conn, env.window.window_id)  # type: ignore[arg-type]
    assert plan_row.n_orders_below_min_at_live_capital == hand


def journal_actions(env: Env) -> Any:
    from tradepartner.store.asof import live_actions_as_of

    return live_actions_as_of(env.conn, session_close(T_I))


def test_store_max_ingested_at_is_recorded(env: Env) -> None:
    want = registry.store_max_ingested_at(env.conn)
    env.plan()
    [plan_row] = journal.plans_for(env.conn, env.window.window_id)  # type: ignore[arg-type]
    assert plan_row.store_max_ingested_at == want
    assert (plan_row.n_universe, plan_row.n_targets) == (6, 3)


def test_whole_share_comes_from_the_assets_reads(env: Env) -> None:
    reads = AssetsRead(whole_share=("SPY", "DUALB"))
    decided = _decisions(env.plan(assets_read=reads))
    assert decided["SEC_SPY"].whole_share is True
    assert decided["SEC_DUAL_B"].whole_share is True
    assert decided["SEC_TRANSFER"].whole_share is False
    # Two reads: the held names first, then the targets not held.
    assert reads.calls == [["DUALA", "SPY"], ["DUALB", "SPFT", "TRNS"]]


# --- no look-ahead ---------------------------------------------------------------------


class RecordingProvider(StoreProvider):
    """A `StoreProvider` recording the read time of every engine read."""

    reads: ClassVar[list[tuple[str, datetime]]] = []

    def universe(self, t: datetime) -> Any:
        self.reads.append(("universe", t))
        return super().universe(t)

    def adjusted_prices(self, t: datetime, *args: Any, **kwargs: Any) -> Any:
        self.reads.append(("adjusted_prices", t))
        return super().adjusted_prices(t, *args, **kwargs)

    def static_listing_count(self, t: datetime, *args: Any, **kwargs: Any) -> Any:
        self.reads.append(("static_listing_count", t))
        return super().static_listing_count(t, *args, **kwargs)

    def survivorship_gap(self, t: datetime) -> Any:
        self.reads.append(("survivorship_gap", t))
        return super().survivorship_gap(t)


@pytest.mark.parametrize("sessions_late", [0, MAX_CATCH_UP])
def test_every_provider_read_is_at_close_t_i_on_time_and_on_a_catch_up(
    env: Env, monkeypatch: pytest.MonkeyPatch, sessions_late: int
) -> None:
    RecordingProvider.reads = []
    monkeypatch.setattr(planning, "StoreProvider", RecordingProvider)
    session = F_I
    for _ in range(sessions_late):
        session = next_session(session)
    run = env.run if sessions_late == 0 else env.run_on(session)
    outcome = env.plan(session, run=run)
    assert outcome.rebalance_session == T_I
    methods = {name for name, _ in RecordingProvider.reads}
    assert methods == {"universe", "adjusted_prices", "static_listing_count", "survivorship_gap"}
    assert {t for _, t in RecordingProvider.reads} == {read_time(T_I)}


def _view(outcome: planning.PlanOutcome) -> dict[str, tuple[Any, ...]]:
    return {
        d.security_id: (d.side, d.planned_quantity, d.planned_notional, d.target_notional)
        for d in outcome.decisions
    }


def _replan(env: Env) -> planning.PlanOutcome:
    for table in ("paper_plans", "decisions", "signals"):
        env.conn.execute(f"DELETE FROM {table}")
    return env.plan(run=env.run_on(F_I))


def test_the_ledger_and_prices_read_nothing_known_after_close_s_minus_1(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held name's S-1 bar revised (close x 10) one minute after close(S-1)
    changes no decision. Teeth: with the cut moved past the revision, equity moves and so
    do the buys' notionals."""
    base = _view(env.plan())
    late = session_close(T_I) + timedelta(minutes=1)  # just after close(S-1)
    env.conn.execute(
        "INSERT INTO prices_daily SELECT * REPLACE (close * 10 AS close, ? AS known_at, "
        "? AS ingested_at) FROM prices_daily WHERE security_id = 'SEC_SPY' AND session = ?",
        [late, late, T_I],
    )
    assert _view(_replan(env)) == base
    monkeypatch.setattr(planning, "_cut", lambda session: late)
    assert _view(_replan(env)) != base


# --- re-use and lagging -----------------------------------------------------------------


def test_a_re_run_reuses_the_journaled_decisions_and_never_re_plans(env: Env) -> None:
    first = env.plan()
    counts = env.counts()
    catch_up = next_session(F_I)
    again = env.plan(catch_up, run=env.run_on(catch_up), assets_read=_raising(RuntimeError))
    assert again.status == "reused"
    assert again.decisions == first.decisions
    assert again.plan_trial_id is None
    assert env.counts() == counts


def test_a_lagging_fill_leaves_the_rebalance_pending_with_no_plan(env: Env) -> None:
    counts = env.counts()
    outcome = env.plan(lagging=True, assets_read=_raising(RuntimeError))
    assert (outcome.status, outcome.decisions) == ("lagging", ())
    assert env.counts() == counts


# --- exits --------------------------------------------------------------------------------


def _raising(kind: type[BaseException]) -> Callable[..., Any]:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise kind("injected")

    return boom


def _raising_after_held(kind: type[BaseException]) -> Callable[[Sequence[str]], Any]:
    """An `assets` read that answers the held names and raises on the targets."""
    reads = AssetsRead()

    def read(symbols: Sequence[str]) -> Any:
        if reads.calls:
            raise kind("injected")
        return reads(symbols)

    return read


def test_a_failing_engine_plan_raises_plan_trial_error_with_no_row(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = env.counts()
    monkeypatch.setattr(planning.engine, "plan", _raising(ValueError))
    with pytest.raises(PlanTrialError) as caught:
        env.plan()
    assert not isinstance(caught.value, ClockError)
    assert isinstance(caught.value.__cause__, ValueError)
    assert env.counts() == counts


def test_a_failing_registry_open_raises_plan_trial_error_with_no_row(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = env.counts()
    monkeypatch.setattr(planning.registry, "open_trial", _raising(registry.RegistryError))
    with pytest.raises(PlanTrialError):
        env.plan()
    assert env.counts() == counts


def test_a_failing_trial_result_raises_plan_trial_error_with_no_row(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = env.counts()
    monkeypatch.setattr(planning.registry, "write_result", lambda *a, **k: "failed")
    with pytest.raises(PlanTrialError):
        env.plan()
    assert env.counts() == counts


@pytest.mark.parametrize("fault", [ClockError, StaleDataError, LimitBreachError])
def test_a_fault_inside_engine_plan_propagates_unchanged(
    env: Env, monkeypatch: pytest.MonkeyPatch, fault: type[Exception]
) -> None:
    counts = env.counts()
    monkeypatch.setattr(planning.engine, "plan", _raising(fault))
    with pytest.raises(fault) as caught:
        env.plan()
    assert type(caught.value) is fault
    assert env.counts() == counts


@pytest.mark.parametrize("kind", [LimitBreachError, RuntimeError, KeyError])
def test_an_assets_read_error_propagates_unchanged_with_no_row(
    env: Env, kind: type[Exception]
) -> None:
    counts = env.counts()
    with pytest.raises(kind) as caught:
        env.plan(assets_read=_raising_after_held(kind))
    assert type(caught.value) is kind
    assert env.counts() == counts


def test_an_assets_read_error_on_the_held_names_propagates_with_no_row(env: Env) -> None:
    counts = env.counts()
    with pytest.raises(LimitBreachError):
        env.plan(assets_read=_raising(LimitBreachError))
    assert env.counts() == counts


class FailingJournal:
    """`store.journal.append`, raising on the first row of one type."""

    def __init__(self, row_type: type) -> None:
        self.row_type = row_type

    def append(self, conn: duckdb.DuckDBPyConnection, row: JournalRow) -> int | None:
        if isinstance(row, self.row_type):
            raise duckdb.IOException("disk full, injected")
        return journal.append(conn, row)


@pytest.mark.parametrize("row_type", [DecisionRow, PaperPlanRow])
def test_a_write_failing_midway_rolls_back_all_four_kinds_of_row(env: Env, row_type: type) -> None:
    counts = env.counts()
    with pytest.raises(duckdb.IOException):
        env.plan(journal_=FailingJournal(row_type))
    assert env.counts() == counts
    # The rolled-back step leaves the rebalance plannable.
    assert env.plan(run=env.run_on(F_I)).status == "planned"


def test_a_re_registered_hypothesis_is_a_plan_trial_error(env: Env) -> None:
    changed = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.4},
        holdout={"start": HOLDOUT_START.isoformat(), "end": HOLDOUT_END.isoformat()},
    )
    registry.register_hypothesis(
        env.conn,
        slug="h-paper-plan",
        family="momentum",
        title="planning step test, changed",
        doc_path="docs/hypotheses/h-paper-plan.md",
        doc_sha256="1" * 64,
        params=frozen_params_of(changed),
        in_sample_start=IN_SAMPLE_START,
        holdout_start=HOLDOUT_START,
        holdout_end=HOLDOUT_END,
        registered_by="test",
        settings=env.settings,
    )
    counts = env.counts()
    with pytest.raises(PlanTrialError, match="re-registered"):
        env.plan()
    assert env.counts() == counts


def test_profitability_is_refused_by_the_paper_family_gate(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Profitability is engine-ready but cannot open a paper plan or provider."""
    env.conn.execute("UPDATE hypotheses SET family = 'profitability'")
    counts = env.counts()

    def provider_used(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider must not open for a non-paper family")

    monkeypatch.setattr(planning, "StoreProvider", provider_used)
    with pytest.raises(PlanTrialError, match="cannot run yet"):
        env.plan()
    assert env.counts() == counts


def test_momentum_plan_uses_the_windows_stored_family(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_plan = engine.plan
    seen: list[str] = []

    def record(*args: Any, **kwargs: Any) -> engine.Plan:
        seen.append(kwargs["family"])
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(engine, "plan", record)
    assert env.plan().status == "planned"
    assert seen == ["momentum"]


def test_a_run_of_another_window_is_refused(env: Env) -> None:
    with pytest.raises(ValueError, match="not of window"):
        env.plan(run=replace(env.run, window_id=999))


# --- the kind ------------------------------------------------------------------------------


def _event(env: Env, status: str) -> RebalanceEventRow:
    stamp = _utc(F_I, 20)
    return RebalanceEventRow(
        rebalance_session=T_I,
        run_id=env.run.run_id,  # type: ignore[arg-type]
        status=status,
        known_at=stamp,
        ingested_at=stamp,
    )


def _sessions_after(day: date, count: int) -> date:
    for _ in range(count):
        day = next_session(day)
    return day


def test_each_kind_at_its_boundary_session(env: Env) -> None:
    runs = [env.run]
    kind = rebalance_kind
    assert kind(env.window, runs, [], T_I, MAX_CATCH_UP) is None
    assert kind(env.window, runs, [], F_I, MAX_CATCH_UP) == "rebalance"
    assert kind(env.window, runs, [], _sessions_after(F_I, 1), MAX_CATCH_UP) == "catch_up"
    last = _sessions_after(F_I, MAX_CATCH_UP)
    assert kind(env.window, runs, [], last, MAX_CATCH_UP) == "catch_up"
    assert kind(env.window, runs, [], next_session(last), MAX_CATCH_UP) is None
    assert kind(env.window, runs, [], _sessions_after(F_I, 1), 0) is None
    assert due_rebalance(env.window, runs, [], last, MAX_CATCH_UP) == ("catch_up", T_I)
    # The next month's fill session is a rebalance again.
    assert kind(env.window, runs, [], fill_session(T_NEXT), MAX_CATCH_UP) == "rebalance"


@pytest.mark.parametrize("status", ["executed", "missed"])
def test_an_executed_or_missed_rebalance_is_not_caught_up(env: Env, status: str) -> None:
    runs = [env.run]
    events = [_event(env, status)]
    catch_up = _sessions_after(F_I, 1)
    assert rebalance_kind(env.window, runs, events, catch_up, MAX_CATCH_UP) is None
    # Not on its own fill session either: a same-session re-run trades nothing.
    assert rebalance_kind(env.window, runs, events, F_I, MAX_CATCH_UP) is None


def test_an_event_of_another_window_does_not_count(env: Env) -> None:
    other = replace(env.run, run_id=999, window_id=999)
    event = replace(_event(env, "executed"), run_id=999)
    catch_up = _sessions_after(F_I, 1)
    kind = rebalance_kind(env.window, [env.run, other], [event], catch_up, MAX_CATCH_UP)
    assert kind == "catch_up"


def test_an_event_of_an_unknown_run_raises(env: Env) -> None:
    event = replace(_event(env, "executed"), run_id=12345)
    with pytest.raises(ValueError, match="not among the runs"):
        rebalance_kind(env.window, [env.run], [event], next_session(F_I), MAX_CATCH_UP)


def test_the_frozen_catch_up_window_is_read_from_the_window(env: Env) -> None:
    assert frozen_max_catch_up_sessions(env.window) == MAX_CATCH_UP
    with pytest.raises(ValueError, match="max_catch_up_sessions"):
        frozen_max_catch_up_sessions(replace(env.window, frozen_json="{}"))


# --- the boundary --------------------------------------------------------------------------


def test_the_plan_path_imports_no_adapter() -> None:
    tree = ast.parse(PLANNING_PY.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    assert imported
    assert not [m for m in imported if m.startswith("tradepartner.adapters")], imported
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"AlpacaBroker", "FakeBroker", "utc_now", "now_utc"}


# --- the reference price ---------------------------------------------------------------------


def _splits(*rows: tuple[str, date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"security_id": s, "action_type": "split", "ex_date": d, "ratio_or_amount": r}
            for s, d, r in rows
        ],
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
        },
    )


def _copy_bar(env: Env, new_id: str, session: date) -> float:
    env.conn.execute(
        "INSERT INTO prices_daily SELECT * REPLACE (? AS security_id) FROM prices_daily "
        "WHERE security_id = 'SEC_SPY' AND session = ?",
        [new_id, session],
    )
    row = env.conn.execute(
        "SELECT close FROM prices_daily WHERE security_id = ? AND session = ?", [new_id, session]
    ).fetchone()
    assert row is not None
    return float(row[0])


def test_the_reference_price_divides_by_the_splits_in_s_minus_1_to_s(env: Env) -> None:
    close = _copy_bar(env, "SEC_X", T_I)
    splits = _splits(("SEC_X", T_I, 5.0), ("SEC_X", F_I, 2.0), ("SEC_X", T_NEXT, 7.0))
    prices = planning.reference_prices(env.conn, F_I, {"SEC_X"}, splits)
    assert prices["SEC_X"] == pytest.approx(close / 2.0)


def test_an_older_bar_is_divided_by_every_split_after_its_own_session(env: Env) -> None:
    """The latest bar is 2019-04-26: a split ex 2019-04-29 (inside the gap before
    S-1) and one ex S both apply; one ex on the bar's own session does not."""
    bar = date(2019, 4, 26)
    close = _copy_bar(env, "SEC_X", bar)
    splits = _splits(("SEC_X", bar, 5.0), ("SEC_X", date(2019, 4, 29), 2.0), ("SEC_X", F_I, 3.0))
    prices = planning.reference_prices(env.conn, F_I, {"SEC_X"}, splits)
    assert prices["SEC_X"] == pytest.approx(close / 6.0)


def test_a_name_with_no_bar_by_s_minus_1_raises(env: Env) -> None:
    with pytest.raises(ValueError, match="SEC_NONE"):
        planning.reference_prices(env.conn, F_I, {"SEC_NONE"}, _splits())


# --- review fixes (safety-reviewer and quant-auditor on #431) ---------------------------


def test_a_fault_after_the_transaction_ended_still_propagates_unchanged(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing rollback (here the transaction is already gone) never replaces
    the exception that caused it: a `StaleDataError` still takes its own path."""

    def ends_then_raises(*_args: Any, **_kwargs: Any) -> Any:
        env.conn.rollback()
        raise StaleDataError("injected")

    counts = env.counts()
    monkeypatch.setattr(planning.engine, "plan", ends_then_raises)
    with pytest.raises(StaleDataError):
        env.plan()
    assert env.counts() == counts


def _forced_exit(env: Env, sid: str) -> DecisionRow:
    stamp = _utc(F_I, 11)
    row = DecisionRow(
        run_id=env.run.run_id,  # type: ignore[arg-type]
        security_id=sid,
        side="sell",
        planned_quantity=HELD[sid],
        whole_share=False,
        decision="forced_exit",
        reason="delisted",
        known_at=stamp,
        ingested_at=stamp,
    )
    return replace(row, decision_id=journal.append(env.conn, row))


@pytest.mark.parametrize("state", ["open", "in_flight", "closed"])
def test_a_name_with_an_open_or_in_flight_forced_exit_gets_no_decision(
    env: Env, state: str
) -> None:
    exit_ = _forced_exit(env, "SEC_DUAL_A")
    stamp = _utc(F_I, 11)
    if state == "in_flight":
        order = OrderRow(
            client_order_id="tp-20190501-SEC_DUAL_A-sell-1",
            decision_id=exit_.decision_id,  # type: ignore[arg-type]
            run_id=env.run.run_id,  # type: ignore[arg-type]
            session=F_I,
            attempt=1,
            phase="exit",
            security_id="SEC_DUAL_A",
            symbol="DUALA",
            side="sell",
            quantity=HELD["SEC_DUAL_A"],
            sells_in_flight_at_submit=False,
            known_at=stamp,
            ingested_at=stamp,
        )
        journal.append(env.conn, order)
        journal.append(
            env.conn,
            OrderEventRow(
                client_order_id=order.client_order_id,
                status="accepted",
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
    if state == "closed":
        journal.append(
            env.conn,
            DecisionEventRow(
                decision_id=exit_.decision_id,  # type: ignore[arg-type]
                run_id=env.run.run_id,  # type: ignore[arg-type]
                status="skipped",
                reason="untradable",
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
    decided = _decisions(env.plan())
    if state == "closed":
        assert decided["SEC_DUAL_A"].reason == "left_targets"
    else:
        assert "SEC_DUAL_A" not in decided
    assert decided["SEC_SPY"].reason == "left_universe"


def test_frozen_risk_must_be_the_windows(env: Env) -> None:
    with pytest.raises(ValueError, match="frozen risk"):
        plan_rebalance(
            env.conn,
            journal,
            env.window,
            env.run,
            F_I,
            env.settings,
            RiskConfig(),
            AssetsRead(),
            False,
            now=_utc(F_I, 12),
        )


def test_a_naive_now_is_refused(env: Env) -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        plan_rebalance(
            env.conn,
            journal,
            env.window,
            env.run,
            F_I,
            env.settings,
            FROZEN,
            AssetsRead(),
            False,
            now=datetime(2019, 5, 1, 12),  # noqa: DTZ001 - naive on purpose
        )


def test_a_plan_row_with_no_decision_is_reused_too(env: Env) -> None:
    stamp = _utc(F_I, 12)
    journal.append(
        env.conn,
        PaperPlanRow(
            run_id=env.run.run_id,  # type: ignore[arg-type]
            plan_trial_id=0,
            rebalance_session=T_I,
            n_universe=0,
            n_targets=0,
            n_orders_below_min_at_live_capital=0,
            known_at=stamp,
            ingested_at=stamp,
        ),
    )
    counts = env.counts()
    outcome = env.plan(assets_read=_raising(RuntimeError))
    assert (outcome.status, outcome.decisions) == ("reused", ())
    assert env.counts() == counts


def test_only_the_windows_overrides_naming_t_i_apply(env: Env) -> None:
    stamp = _utc(F_I, 9)

    def override(rebalance: date, window_id: int) -> None:
        journal.append(
            env.conn,
            OverrideRow(
                window_id=window_id,
                made_at=stamp,
                rebalance_session=rebalance,
                security_id="SEC_DUAL_B",
                kind="exclude_name",
                reason="the owner's reason, long enough",
                known_at=stamp,
                ingested_at=stamp,
            ),
        )

    override(T_NEXT, env.window.window_id)  # type: ignore[arg-type]
    override(T_I, 999)
    assert _decisions(env.plan())["SEC_DUAL_B"].decision == "trade"
    for table in ("paper_plans", "decisions", "signals"):
        env.conn.execute(f"DELETE FROM {table}")
    override(T_I, env.window.window_id)  # type: ignore[arg-type]
    dual_b = _decisions(env.plan(run=env.run_on(F_I)))["SEC_DUAL_B"]
    assert (dual_b.decision, dual_b.side, dual_b.reason) == ("override", None, "exclude_name")


# --- the shared listing tie rule (#534) ---------------------------------------------------


def test_current_listings_keeps_the_first_row_on_a_valid_from_tie() -> None:
    """Two rows of one name with the same `valid_from`: the first in frame
    order wins; a later `valid_from` on or before the day beats both, and one
    after the day is not read. The wrapper reads tickers through this same
    function, so the tie rule cannot diverge."""
    day = date(2026, 9, 30)
    frame = pl.DataFrame(
        {
            "security_id": ["X", "X", "Y", "Y", "Y"],
            "ticker": ["FIRST", "SECOND", "OLD", "NEW", "FUTURE"],
            "valid_from": [
                date(2026, 1, 2),
                date(2026, 1, 2),
                date(2025, 1, 2),
                date(2026, 3, 2),
                date(2026, 10, 1),
            ],
        }
    )

    current = planning.current_listings(frame, day)

    assert {sid: row["ticker"] for sid, row in current.items()} == {"X": "FIRST", "Y": "NEW"}
    flipped = planning.current_listings(frame.slice(1, 1).vstack(frame.slice(0, 1)), day)
    assert flipped["X"]["ticker"] == "SECOND"
    assert wrapper.current_listings is planning.current_listings
    assert not hasattr(wrapper, "_current")
    # reconcile_run and the window's flatness check read the same rule (#705).
    assert reconcile_run.current_listings is planning.current_listings
    assert window_module.current_listings is planning.current_listings
    assert not hasattr(reconcile_run, "_current_listings")
    assert not hasattr(window_module, "_current_tickers")

"""No plan reads later than its own rebalance (backtest spec req 13; plan T40b, #201).

T40's truncation and prefix invariance cannot see an engine that fills at F_k from
targets planned with a read at close(T_{k+1}): every read stays inside both cuts. This
file compares each rebalance's **plan** on a store cut at read_time(T_k) with the same
plan on the full store:

- For every rebalance T_k with a next rebalance, T_0 included, the two-rebalance window
  `run(start=T_k, end=T_{k+1})` covers `engine.run`'s `_plan` call before the loop; for
  k >= 1 the three-rebalance window `run(start=T_{k-1}, end=T_{k+1})` covers the call
  inside it. Both are compared with the full store's two-rebalance window: the plan is
  state-free, so a window's plan at T_k is a full run's.
- Compared: `targets[fill_session(T_k)]` and the T_k `RebalanceRow`'s plan fields
  (`PLAN_FIELDS`), at every cost level. Fills, exits and valuation after T_k are not:
  the cut has no strategy bar after T_k, so most fills on F_k are missing, by design.
- **Benchmark-exempt cut** (owner decision on #201): a window starting at T_k (k >= 12,
  once SPY and MTUM are known) buys the benchmarks on F_k, which a plain cut has no bar
  for. `BenchmarkExemptStore` keeps benchmark bars past the cut. That is safe only
  because no plan field reads a benchmark, which `test_benchmarks_are_never_plan_inputs`
  checks at every compared read.
- **Teeth**, at `T_TEETH`: (a) `_plan` reading at the next rebalance fails the check on
  both windows while T40's truncation comparison still passes; (b) a copy of `_plan`
  whose signal frame alone is read at the next rebalance fails it on a store with a
  revised T_{k-1} month-end bar, and the unpatched engine passes on that same store.

Helpers are copied from `test_backtest_invariance.py` (T40) rather than imported, so
T40's file and `harness.py` stay unchanged. The store is the plain fixture universe:
this check needs no seeded revisions.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import _FACT_TABLES, _SOURCE_SCHEMA, TruncatedStore
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.provider import DataProvider
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.signals import momentum_12_1
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"

#: The fixture's bars run 2017-01-03 through 2020-06-30 (fixture README).
FIXTURE_START = date(2017, 1, 3)
FIXTURE_END = date(2020, 6, 30)
COST_LEVELS = (0.0, 15.0)
#: The teeth case's T_k: a 2019 rebalance with several targets (as in T40).
T_TEETH = date(2019, 1, 31)
#: When a synthetic revision becomes known: an hour after close(T), before the next close.
REVISION_DELAY = timedelta(hours=1)
#: The `RebalanceRow` fields the plan at T_k decides.
PLAN_FIELDS = (
    "n_universe",
    "n_targets",
    "n_static_listings",
    "n_excluded_no_history",
    "gap_count_share",
    "gap_size_share",
)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Results = Mapping[float, BacktestResult]
PlanView = dict[float, tuple[dict[str, float], tuple[Any, ...]]]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live
    # ones; keep the live ones free of any `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _frozen() -> Settings:
    return Settings(_env_file=None, strategy={"top_fraction": 0.5})


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    return conn


def _open_trial(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.TrialHandle:
    """A synthetic trial on the in-memory store (never the real store)."""
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-plan-timing",
        family="momentum",
        title="plan-read timing check",
        doc_path="docs/hypotheses/h-plan-timing.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": COST_LEVELS[-1]},
        in_sample_start=FIXTURE_START,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=FIXTURE_START,
        end_session=FIXTURE_END,
        data_cutoff=read_time(rebalance_sessions(FIXTURE_START, FIXTURE_END)[-1]),
        synthetic=True,
        run_by="test",
        settings=settings,
    )


def _factory(conn: duckdb.DuckDBPyConnection) -> Connect:
    """A connection factory that lends `conn` without closing it."""

    @contextmanager
    def _lend() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return _lend


def _revise_bar(
    conn: duckdb.DuckDBPyConnection, sid: str, session: date, known_at: datetime, factor: float
) -> None:
    """A second `prices_daily` row for (sid, session), every price times `factor`."""
    [row] = (
        prices_as_of(conn, session_close(session), [sid])
        .filter(pl.col("session") == session)
        .iter_rows(named=True)
    )
    for column in ("open", "high", "low", "close"):
        row[column] = row[column] * factor
    row.update(known_at=known_at, ingested_at=known_at, provenance="bar")
    insert_row(conn, "prices_daily", row)


class BenchmarkExemptStore(TruncatedStore):
    """`TruncatedStore` whose `main.prices_daily` keeps benchmark bars past the cut.

    Built with every harness view except `prices_daily`, then that one view is
    created once, on the connection `.at` returns, over the untruncated source: rows
    known by the probe time, or of a security flagged `benchmark` in the source's
    `securities` (benchmarks never change, so the untruncated flag is the right key).
    """

    def __init__(self, source: duckdb.DuckDBPyConnection) -> None:
        super().__init__(source, tables=[t for t in _FACT_TABLES if t != "prices_daily"])
        self.at(datetime(1970, 1, 1, tzinfo=UTC)).execute(
            f"CREATE VIEW main.prices_daily AS SELECT * FROM {_SOURCE_SCHEMA}.prices_daily "
            "WHERE known_at <= (SELECT t FROM probe_t) OR security_id IN "
            f"(SELECT security_id FROM {_SOURCE_SCHEMA}.securities WHERE benchmark)"
        )


@dataclass(frozen=True)
class Fixture:
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    sessions: tuple[date, ...]

    def provider(self, conn: duckdb.DuckDBPyConnection) -> StoreProvider:
        return StoreProvider(
            _factory(conn), self.handle, self.settings, registry_connect=_factory(self.conn)
        )

    def window(self, start: date, end: date, conn: duckdb.DuckDBPyConnection) -> Results:
        """`engine.run` over `[start, end]` reading `conn`, the handle checked here."""
        with self.provider(conn) as provider:
            return run(self.settings, provider, start, end, self.handle, COST_LEVELS)

    def after(self, session: date) -> date:
        return self.sessions[self.sessions.index(session) + 1]


@pytest.fixture(scope="module")
def fixture() -> Iterator[Fixture]:
    conn = _store()
    settings = _frozen()
    try:
        yield Fixture(
            conn=conn,
            settings=settings,
            handle=_open_trial(conn, settings),
            sessions=tuple(rebalance_sessions(FIXTURE_START, FIXTURE_END)),
        )
    finally:
        conn.close()


@pytest.fixture(scope="module")
def baseline(fixture: Fixture) -> dict[date, PlanView]:
    """The full store's plan at every T_k with a next rebalance (two-rebalance window)."""
    return {
        t_k: _plan_view(fixture.window(t_k, fixture.after(t_k), fixture.conn), t_k)
        for t_k in fixture.sessions[:-1]
    }


def _plan_view(results: Results, t_k: date) -> PlanView:
    """What the plan at T_k decided, per cost level: its targets and plan fields."""
    view: PlanView = {}
    for level, result in results.items():
        [row] = [r for r in result.rebalances if r.session == t_k]
        targets = dict(result.targets[fill_session(t_k)])
        view[level] = (targets, tuple(getattr(row, name) for name in PLAN_FIELDS))
    return view


def _fields(view: PlanView, name: str) -> set[Any]:
    return {fields[PLAN_FIELDS.index(name)] for _, fields in view.values()}


# --- the check -------------------------------------------------------------------


def test_benchmarks_are_never_plan_inputs(fixture: Fixture) -> None:
    """The exemption is safe only if no benchmark is a universe member (so neither the
    signal frame nor the static-listing count reads one; the gap drops them by flag)."""
    seen = 0
    with fixture.provider(fixture.conn) as provider:
        for t_k in fixture.sessions[:-1]:
            t = read_time(t_k)
            benchmarks = set(provider.benchmark_ids(t).values())
            members = set(provider.universe(t).members["security_id"].to_list())
            assert not benchmarks & members, f"benchmark in the universe at {t_k}"
            seen += bool(benchmarks)
    assert seen >= 20  # SPY and MTUM are known from 2018: the exemption is exercised


def test_every_plan_is_unchanged_on_a_store_cut_at_its_own_read(
    fixture: Fixture, baseline: dict[date, PlanView]
) -> None:
    exempt = BenchmarkExemptStore(fixture.conn)
    try:
        for k, t_k in enumerate(fixture.sessions[:-1]):
            cut = exempt.at(read_time(t_k))
            t_next = fixture.after(t_k)
            want = baseline[t_k]
            got = _plan_view(fixture.window(t_k, t_next, cut), t_k)
            assert got == want, f"plan at {t_k} (before the loop) differs on the cut store"
            if k >= 1:
                start = fixture.sessions[k - 1]
                got = _plan_view(fixture.window(start, t_next, cut), t_k)
                assert got == want, f"plan at {t_k} (inside the loop) differs on the cut store"
    finally:
        exempt.close()
    with_targets = [t_k for t_k, view in baseline.items() if max(_fields(view, "n_targets")) >= 1]
    assert len(with_targets) >= 20


# --- teeth -----------------------------------------------------------------------


def _late(fixture: Fixture, read: Callable[..., engine._Plan]) -> Callable[..., engine._Plan]:
    """`read` at the next rebalance, its result relabelled as the plan at `session`."""

    def plan(provider: DataProvider, params: Settings, session: date) -> engine._Plan:
        late = read(provider, params, fixture.after(session))
        return dataclasses.replace(late, session=session, fill_session=fill_session(session))

    return plan


def test_a_plan_read_at_the_next_rebalance_fails_the_check_but_not_truncation(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "_plan", _late(fixture, engine._plan))
    t_k = T_TEETH
    k = fixture.sessions.index(t_k)
    exempt = BenchmarkExemptStore(fixture.conn)
    try:
        for start in (t_k, fixture.sessions[k - 1]):  # before the loop, inside it
            full = _plan_view(fixture.window(start, fixture.after(t_k), fixture.conn), t_k)
            cut = _plan_view(
                fixture.window(start, fixture.after(t_k), exempt.at(read_time(t_k))), t_k
            )
            assert _fields(full, "gap_count_share") != _fields(cut, "gap_count_share"), start
    finally:
        exempt.close()
    # T40's cut at close(T_TEETH) does not see it: every late read stays inside it.
    plain = TruncatedStore(fixture.conn)
    try:
        start = fixture.sessions[0]
        full_run = fixture.window(start, t_k, fixture.conn)
        cut_run = fixture.window(start, t_k, plain.at(read_time(t_k)))
    finally:
        plain.close()
    for level, result in full_run.items():
        assert cut_run[level].equity == result.equity
        assert cut_run[level].rebalances == result.rebalances
        assert cut_run[level].targets == result.targets


def _late_signal_plan(fixture: Fixture) -> Callable[..., engine._Plan]:
    """A copy of `engine._plan` whose signal frame alone is read at the next rebalance."""

    def plan(provider: DataProvider, params: Settings, session: date) -> engine._Plan:
        t, t_late = read_time(session), read_time(fixture.after(session))
        members = sorted(provider.universe(t).members["security_id"].to_list())
        strategy = params.strategy
        frame = provider.adjusted_prices(t_late, members, strategy.signal_total_return)
        signal = momentum_12_1(
            frame, session, strategy.formation_months, strategy.skip_months, security_ids=members
        )
        return engine._Plan(
            session=session,
            fill_session=fill_session(session),
            targets=engine.target_weights(signal.scores, strategy.top_fraction, strategy.weighting),
            n_universe=len(members),
            n_static_listings=provider.static_listing_count(t, members),
            n_excluded_no_history=signal.n_excluded,
            gap=provider.survivorship_gap(t),
        )

    return plan


def test_a_late_signal_read_fails_the_check_and_the_engine_passes_it(
    fixture: Fixture, baseline: dict[date, PlanView], monkeypatch: pytest.MonkeyPatch
) -> None:
    t_k = T_TEETH
    k = fixture.sessions.index(t_k)
    anchor = fixture.sessions[k - 1]  # the 12-1 skip anchor at T_k: the previous month-end
    t_next = fixture.after(t_k)
    targets = baseline[t_k][COST_LEVELS[-1]][0]
    # A scored member outside the targets, lifted far across the top-fraction boundary
    # by a revision of its anchor bar known after read_time(T_k), before read_time(T_{k+1}).
    with fixture.provider(fixture.conn) as provider:
        t = read_time(t_k)
        members = sorted(provider.universe(t).members["security_id"].to_list())
        strategy = fixture.settings.strategy
        frame = provider.adjusted_prices(t, members, strategy.signal_total_return)
        signal = momentum_12_1(
            frame, t_k, strategy.formation_months, strategy.skip_months, security_ids=members
        )
    outside = sorted(set(signal.scores) - set(targets))
    assert outside and targets, f"no scored non-target at {t_k}"
    revised_at = read_time(t_k) + REVISION_DELAY
    assert revised_at < read_time(t_next)
    revised = _store()
    _revise_bar(revised, outside[0], anchor, revised_at, factor=10.0)
    exempt = BenchmarkExemptStore(revised)
    try:
        cut = exempt.at(read_time(t_k))

        def views() -> list[tuple[PlanView, PlanView]]:
            return [
                (
                    _plan_view(fixture.window(start, t_next, revised), t_k),
                    _plan_view(fixture.window(start, t_next, cut), t_k),
                )
                for start in (t_k, anchor)
            ]

        # Control: the engine as built reads the signal on time and passes.
        for full, on_cut in views():
            assert full == on_cut
        monkeypatch.setattr(engine, "_plan", _late_signal_plan(fixture))
        for full, on_cut in views():
            assert full[COST_LEVELS[-1]][0] != on_cut[COST_LEVELS[-1]][0]
            assert outside[0] in full[COST_LEVELS[-1]][0]
    finally:
        exempt.close()
        revised.close()

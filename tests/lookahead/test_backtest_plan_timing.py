"""No plan reads later than its own rebalance (backtest spec req 13; plan T40b, #201), at
every rebalance cadence (strategy-lab spec req 6; plan T98b).

**Cadences.** Every test runs once per `Case` in `CASES`, one per cadence (`month_end`,
`week_end`, `daily`), with `schedule.rebalance_cadence` frozen to it. `month_end` walks
the whole fixture range as T40b did; `week_end` and `daily` walk the same windows of
about thirty rebalances as `test_backtest_invariance.py`. A later axis (the strategy
family, T85e) extends `Case` and `CASES`, not the tests.

**Families** (T85e). The cadence cases run `family="momentum"`; the `profitability` case
runs that family at `month_end` from 2018, so the plans read `statement_facts` and
`classifications` and the compared fields include the family's six counts. Teeth (b)
revises a momentum anchor bar, so it runs on the momentum cases; the profitability
case's teeth are the fixture's acceptance cases (`Accepted`, fixture README "Statement
facts"): on a store without that case's rows, the plan at the close before acceptance
is unchanged and the plan at the first close after it is not. A plan read at the fill
session's close, or strictly before `session_close(T)`, fails at least one of them. A
provider read past `t` alone is masked by the signal's rule 0 (`known_at <= t`); with
that filter gone too, the 10-K case fails.

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
  (`PLAN_FIELDS`), at every cost level, and the plan's own `PLAN_READS` (the universe
  members, the signal scores and the names excluded for no history, which Phase 4's
  `decisions_from` reads; T53b), recorded around `engine._plan` during the run. Fills,
  exits and valuation after T_k are not: the cut has no strategy bar after T_k, so most
  fills on F_k are missing, by design.
- **Benchmark-exempt cut** (owner decision on #201): a window starting at T_k (k >= 12,
  once SPY and MTUM have bars; earlier windows run with no benchmarks, `BENCHMARK_START`)
  buys the benchmarks on F_k, which a plain cut has no bar
  for. `BenchmarkExemptStore` keeps benchmark bars past the cut. That is safe only
  because no plan field reads a benchmark, which `test_benchmarks_are_never_plan_inputs`
  checks at every compared read.
- **Teeth**, at the case's `teeth` rebalance: (a) `_plan` reading at the next rebalance
  fails the check on both windows while T40's truncation comparison still passes; (b) a
  copy of `_plan` whose signal frame alone is read at the next rebalance fails it on a
  store with a revised skip-anchor bar (A_skip of T_k: the previous month end at the
  frozen `month_end` anchor, whatever the cadence), and the unpatched engine passes on
  that same store.

**What the main check sees on the plain fixture store.** A late read changes a compared
field only if the data it reaches differs: a late universe or gap read is caught (the
cut store has no bars after T_k); a late signal-frame read is caught only where an
anchor bar is revised after the read, i.e. by teeth (b); a late
`static_listing_count` read goes unseen, because the fixture's static-listing count
never changes between rebalances. The spec asks for exactly this ("detects one that
changes a compared plan field"; quant-auditor on PR #215).

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
from typing import Any, get_args

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import _FACT_TABLES, _SOURCE_SCHEMA, TruncatedStore
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.provider import DataProvider
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.signals import MomentumSignal, anchor_sessions, momentum
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import session_close
from tradepartner.config import Cadence, HypothesisFamily, Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"

#: The fixture's bars run 2017-01-03 through 2020-06-30 (fixture README).
FIXTURE_START = date(2017, 1, 3)
FIXTURE_END = date(2020, 6, 30)
#: The fixture benchmarks' (SPY, MTUM) first bar. Benchmarks are read by symbol (#840),
#: so a window starting before it is run with `benchmarks=[]`: the engine refuses a
#: benchmark with no bar at F_0, and no plan field reads a benchmark.
BENCHMARK_START = date(2018, 1, 2)
COST_LEVELS = (0.0, 15.0)
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
    "n_ranked",
    "n_excluded_no_facts",
    "n_excluded_stale_facts",
    "n_excluded_sector",
    "n_excluded_malformed",
    "n_derived",
)


@dataclass(frozen=True)
class Accepted:
    """A fixture statement-fact case: `cik`'s rows accepted at `known_at`, unseen by the
    plan at `before` and first read by the plan at `first`."""

    name: str
    cik: str
    known_at: datetime
    before: date
    first: date


@dataclass(frozen=True)
class Case:
    """One cadence's walk: the window `[start, end]` and the teeth case's T_k (a 2019
    rebalance with several targets, as in T40)."""

    cadence: Cadence
    start: date
    end: date
    teeth: date
    family: HypothesisFamily = "momentum"
    accepted: tuple[Accepted, ...] = ()

    @property
    def id(self) -> str:
        """The pytest id: the cadence for a momentum case (ids unchanged), else the family."""
        return self.cadence if self.family == "momentum" else self.family


#: The fixture's statement-fact acceptance cases on universe members (README; times UTC,
#: close 21:00 on these sessions): a 10-K at 16:30 New York after close(2019-02-28); the
#: stamps 15:00, 16:00 (= session_close) and 17:30 at 2019-01-31; and a total_assets
#: (10-K/A, 2020-01-15) accepted after its gross_profit, leaving a no_facts name at
#: 2019-12-31.
ACCEPTED = (
    Accepted(
        "10-K after close",
        "CIK0001000007",
        datetime(2019, 2, 28, 21, 30, tzinfo=UTC),
        date(2019, 2, 28),
        date(2019, 3, 29),
    ),
    Accepted(
        "15:00",
        "CIK0001000002",
        datetime(2019, 1, 31, 20, tzinfo=UTC),
        date(2018, 12, 31),
        date(2019, 1, 31),
    ),
    Accepted(
        "16:00 = close",
        "CIK0001000013",
        datetime(2019, 1, 31, 21, tzinfo=UTC),
        date(2018, 12, 31),
        date(2019, 1, 31),
    ),
    Accepted(
        "17:30",
        "CIK0001000006",
        datetime(2019, 1, 31, 22, 30, tzinfo=UTC),
        date(2019, 1, 31),
        date(2019, 2, 28),
    ),
    Accepted(
        "total_assets later",
        "CIK0001000016",
        datetime(2020, 1, 15, 20, 30, tzinfo=UTC),
        date(2019, 12, 31),
        date(2020, 1, 31),
    ),
)


#: One momentum case per cadence (`month_end` is T40b's walk unchanged, the others are
#: `test_backtest_invariance.py`'s windows), then one per other family.
CASES: dict[str, Case] = {
    case.id: case
    for case in (
        Case("month_end", FIXTURE_START, FIXTURE_END, teeth=date(2019, 1, 31)),
        Case("week_end", date(2018, 10, 12), date(2019, 5, 3), teeth=date(2019, 2, 1)),
        Case("daily", date(2019, 1, 2), date(2019, 2, 15), teeth=date(2019, 1, 31)),
        Case(
            "month_end",
            date(2018, 1, 2),
            FIXTURE_END,
            teeth=date(2019, 1, 31),
            family="profitability",
            accepted=ACCEPTED,
        ),
    )
}
assert tuple(c.cadence for c in CASES.values() if c.family == "momentum") == get_args(Cadence)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Results = Mapping[float, BacktestResult]
PlanView = dict[float, tuple[dict[str, float], tuple[Any, ...]]]
Planned = tuple[Results, Mapping[date, engine.Plan]]
#: The `Plan` fields `decisions_from` reads (T53b), compared like the row's plan fields.
PLAN_READS = ("members", "scores", "excluded_no_history")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live
    # ones; keep the live ones free of any `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _frozen(cadence: Cadence, family: HypothesisFamily) -> Settings:
    extra = {"profitability": {"top_fraction": 0.5}} if family == "profitability" else {}
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        schedule={"rebalance_cadence": cadence},
        **extra,
    )


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    return conn


def _open_trial(
    conn: duckdb.DuckDBPyConnection, settings: Settings, case: Case
) -> registry.TrialHandle:
    """A synthetic trial on the in-memory store (never the real store)."""
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-plan-timing",
        family=case.family,
        title="plan-read timing check",
        doc_path="docs/hypotheses/h-plan-timing.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": COST_LEVELS[-1]},
        in_sample_start=case.start,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=case.start,
        end_session=case.end,
        data_cutoff=read_time(
            rebalance_sessions(case.start, case.end, case.cadence)[-1], case.cadence
        ),
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
    case: Case
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    sessions: tuple[date, ...]

    def read_time(self, session: date) -> datetime:
        return read_time(session, self.case.cadence)

    def provider(self, conn: duckdb.DuckDBPyConnection) -> StoreProvider:
        return StoreProvider(
            _factory(conn), self.handle, self.settings, registry_connect=_factory(self.conn)
        )

    def window(self, start: date, end: date, conn: duckdb.DuckDBPyConnection) -> Results:
        """`engine.run` over `[start, end]` reading `conn`, the handle checked here."""
        settings = self.settings
        if start < BENCHMARK_START:
            settings = settings.model_copy(update={"benchmarks": []})
        with StoreProvider(
            _factory(conn), self.handle, settings, registry_connect=_factory(self.conn)
        ) as provider:
            return run(
                settings, provider, start, end, self.handle, COST_LEVELS, family=self.case.family
            )

    def planned(self, start: date, end: date, conn: duckdb.DuckDBPyConnection) -> Planned:
        """`window`, with every plan the run made (by session), recorded around whatever
        `engine._plan` is at the call (a test's patched copy included)."""
        plans: dict[date, engine.Plan] = {}
        planner = engine._plan

        def recording(
            provider: DataProvider, params: Settings, session: date, family: HypothesisFamily
        ) -> engine.Plan:
            plans[session] = planner(provider, params, session, family)
            return plans[session]

        engine._plan = recording
        try:
            return self.window(start, end, conn), plans
        finally:
            engine._plan = planner

    def after(self, session: date) -> date:
        return self.sessions[self.sessions.index(session) + 1]


@pytest.fixture(scope="module", params=list(CASES.values()), ids=list(CASES))
def fixture(request: pytest.FixtureRequest) -> Iterator[Fixture]:
    case: Case = request.param
    conn = _store()
    settings = _frozen(case.cadence, case.family)
    try:
        yield Fixture(
            case=case,
            conn=conn,
            settings=settings,
            handle=_open_trial(conn, settings, case),
            sessions=tuple(rebalance_sessions(case.start, case.end, case.cadence)),
        )
    finally:
        conn.close()


@pytest.fixture(scope="module")
def baseline(fixture: Fixture) -> dict[date, PlanView]:
    """The full store's plan at every T_k with a next rebalance (two-rebalance window)."""
    return {
        t_k: _plan_view(fixture.planned(t_k, fixture.after(t_k), fixture.conn), t_k)
        for t_k in fixture.sessions[:-1]
    }


def _plan_view(planned: Planned, t_k: date) -> PlanView:
    """What the plan at T_k decided, per cost level: its targets, the row's plan fields,
    then the plan's `PLAN_READS` (the same at every level)."""
    results, plans = planned
    reads = tuple(getattr(plans[t_k], name) for name in PLAN_READS)
    view: PlanView = {}
    for level, result in results.items():
        [row] = [r for r in result.rebalances if r.session == t_k]
        targets = dict(result.targets[plans[t_k].fill_session])
        view[level] = (targets, tuple(getattr(row, name) for name in PLAN_FIELDS) + reads)
    return view


def _fields(view: PlanView, name: str) -> set[Any]:
    return {fields[PLAN_FIELDS.index(name)] for _, fields in view.values()}


# --- the check -------------------------------------------------------------------


def test_benchmarks_are_never_plan_inputs(fixture: Fixture) -> None:
    """The exemption is safe only if no exempted security is a universe member (so
    neither the signal frame nor the static-listing count reads one; the gap drops them
    by flag). Checked against the view's own key: every security flagged `benchmark`
    in the untruncated store, whether or not it is known yet or named in settings."""
    query = "SELECT security_id FROM securities WHERE benchmark"
    exempted = {sid for (sid,) in fixture.conn.execute(query).fetchall()}
    assert exempted
    seen = 0
    with fixture.provider(fixture.conn) as provider:
        for t_k in fixture.sessions[:-1]:
            t = fixture.read_time(t_k)
            members = set(provider.universe(t).members["security_id"].to_list())
            assert not exempted & members, f"exempted security in the universe at {t_k}"
            seen += bool(provider.benchmark_ids(t))
    assert seen >= 20  # SPY and MTUM are known from 2018: the exemption is exercised


def test_every_plan_is_unchanged_on_a_store_cut_at_its_own_read(
    fixture: Fixture, baseline: dict[date, PlanView]
) -> None:
    exempt = BenchmarkExemptStore(fixture.conn)
    try:
        for k, t_k in enumerate(fixture.sessions[:-1]):
            cut = exempt.at(fixture.read_time(t_k))
            t_next = fixture.after(t_k)
            want = baseline[t_k]
            got = _plan_view(fixture.planned(t_k, t_next, cut), t_k)
            assert got == want, f"plan at {t_k} (before the loop) differs on the cut store"
            if k >= 1:
                start = fixture.sessions[k - 1]
                got = _plan_view(fixture.planned(start, t_next, cut), t_k)
                assert got == want, f"plan at {t_k} (inside the loop) differs on the cut store"
    finally:
        exempt.close()
    with_targets = [t_k for t_k, view in baseline.items() if max(_fields(view, "n_targets")) >= 1]
    assert len(with_targets) >= 20


# --- teeth -----------------------------------------------------------------------


def _late(fixture: Fixture, read: Callable[..., engine._Plan]) -> Callable[..., engine._Plan]:
    """`read` at the next rebalance, its result relabelled as the plan at `session`."""

    def plan(
        provider: DataProvider, params: Settings, session: date, family: HypothesisFamily
    ) -> engine._Plan:
        late = read(provider, params, fixture.after(session), family)
        fill = fill_session(session, fixture.case.cadence)
        return dataclasses.replace(late, session=session, fill_session=fill)

    return plan


def test_a_plan_read_at_the_next_rebalance_fails_the_check_but_not_truncation(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "_plan", _late(fixture, engine._plan))
    t_k = fixture.case.teeth
    k = fixture.sessions.index(t_k)
    exempt = BenchmarkExemptStore(fixture.conn)
    try:
        for start in (t_k, fixture.sessions[k - 1]):  # before the loop, inside it
            full = _plan_view(fixture.planned(start, fixture.after(t_k), fixture.conn), t_k)
            cut = _plan_view(
                fixture.planned(start, fixture.after(t_k), exempt.at(fixture.read_time(t_k))), t_k
            )
            assert _fields(full, "gap_count_share") != _fields(cut, "gap_count_share"), start
    finally:
        exempt.close()
    # T40's cut at close(T_k) does not see it: every late read stays inside it.
    plain = TruncatedStore(fixture.conn)
    try:
        start = fixture.sessions[0]
        full_run = fixture.window(start, t_k, fixture.conn)
        cut_run = fixture.window(start, t_k, plain.at(fixture.read_time(t_k)))
    finally:
        plain.close()
    for level, result in full_run.items():
        assert cut_run[level].equity == result.equity
        assert cut_run[level].rebalances == result.rebalances
        assert cut_run[level].targets == result.targets


def _signal(
    provider: DataProvider, params: Settings, session: date, t: datetime, members: list[str]
) -> MomentumSignal:
    """`engine._signal`'s read and score at rebalance `session`, its frame read at `t`."""
    strategy, schedule = params.strategy, params.schedule
    a_form, _ = anchor_sessions(
        session, strategy.formation_months, strategy.skip_months, schedule.signal_anchor
    )
    frame = provider.adjusted_prices(t, members, strategy.signal_total_return, sessions_from=a_form)
    return momentum(
        frame,
        session,
        strategy.formation_months,
        strategy.skip_months,
        schedule.signal_anchor,
        schedule.rebalance_cadence,
        security_ids=members,
    )


def _late_signal_plan(fixture: Fixture) -> Callable[..., engine._Plan]:
    """A copy of `engine._plan` whose signal frame alone is read at the next rebalance."""

    def plan(
        provider: DataProvider, params: Settings, session: date, family: HypothesisFamily
    ) -> engine._Plan:
        t, t_late = fixture.read_time(session), fixture.read_time(fixture.after(session))
        members = sorted(provider.universe(t).members["security_id"].to_list())
        strategy = params.strategy
        signal = _signal(provider, params, session, t_late, members)
        return engine._Plan(
            session=session,
            fill_session=fill_session(session, fixture.case.cadence),
            targets=engine.target_weights(signal.scores, strategy.top_fraction, strategy.weighting),
            n_universe=len(members),
            n_static_listings=provider.static_listing_count(t, members),
            n_excluded_no_history=signal.n_excluded,
            gap=provider.survivorship_gap(t),
            members=tuple(members),
            scores=signal.scores,
            excluded_no_history=signal.excluded,
        )

    return plan


def test_a_late_signal_read_fails_the_check_and_the_engine_passes_it(
    fixture: Fixture, baseline: dict[date, PlanView], monkeypatch: pytest.MonkeyPatch
) -> None:
    if fixture.case.family != "momentum":
        pytest.skip("revises a momentum anchor bar; the profitability teeth are `Accepted`")
    t_k = fixture.case.teeth
    k = fixture.sessions.index(t_k)
    previous = fixture.sessions[k - 1]  # the window that plans T_k inside the loop
    strategy = fixture.settings.strategy
    _, anchor = anchor_sessions(  # A_skip of T_k: the bar the revision lifts
        t_k,
        strategy.formation_months,
        strategy.skip_months,
        fixture.settings.schedule.signal_anchor,
    )
    t_next = fixture.after(t_k)
    targets = baseline[t_k][COST_LEVELS[-1]][0]
    # A scored member outside the targets, lifted far across the top-fraction boundary
    # by a revision of its anchor bar known after read_time(T_k), before read_time(T_{k+1}).
    with fixture.provider(fixture.conn) as provider:
        t = fixture.read_time(t_k)
        members = sorted(provider.universe(t).members["security_id"].to_list())
        signal = _signal(provider, fixture.settings, t_k, t, members)
    outside = sorted(set(signal.scores) - set(targets))
    assert outside and targets, f"no scored non-target at {t_k}"
    revised_at = fixture.read_time(t_k) + REVISION_DELAY
    assert revised_at < fixture.read_time(t_next)
    revised = _store()
    _revise_bar(revised, outside[0], anchor, revised_at, factor=10.0)
    exempt = BenchmarkExemptStore(revised)
    try:
        cut = exempt.at(fixture.read_time(t_k))

        def views() -> list[tuple[PlanView, PlanView]]:
            return [
                (
                    _plan_view(fixture.planned(start, t_next, revised), t_k),
                    _plan_view(fixture.planned(start, t_next, cut), t_k),
                )
                for start in (t_k, previous)
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


def test_accepted_facts_first_reach_the_plan_at_the_first_close_after_acceptance(
    fixture: Fixture,
) -> None:
    if not fixture.case.accepted:
        pytest.skip("the family reads no statement facts")
    for case in fixture.case.accepted:
        assert fixture.after(case.before) == case.first, case.name
        assert fixture.read_time(case.before) < case.known_at <= fixture.read_time(case.first)
        without = _store()
        try:
            query = "DELETE FROM statement_facts WHERE cik = ? AND known_at = ?"
            [(deleted,)] = without.execute(query, [case.cik, case.known_at]).fetchall()
            assert deleted >= 1, case.name
            for t_k, unchanged in ((case.before, True), (case.first, False)):
                full = _plan_view(fixture.planned(t_k, fixture.after(t_k), fixture.conn), t_k)
                cut = _plan_view(fixture.planned(t_k, fixture.after(t_k), without), t_k)
                assert (full == cut) is unchanged, f"{case.name}: plan at {t_k}"
        finally:
            without.close()

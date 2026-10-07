"""No look-ahead for the full backtest run (backtest spec req 13, "Look-ahead"; plan T40),
at every rebalance cadence (strategy-lab spec req 6; plan T98b).

**Cadences.** Every test runs once per `Case` in `CASES`, one per cadence (`month_end`,
`week_end`, `daily`), with `schedule.rebalance_cadence` frozen to it. `month_end` walks
the whole fixture range as T40 did; `week_end` and `daily` walk a window of about thirty
rebalances around the teeth case (`Case`), because the walk runs `run(end=T_i)` from the
window's first rebalance for every T_i (quadratic in the rebalance count) and CI runs it
once per worker that draws a truncation or prefix test. A later axis
(the strategy family, T85e) extends `Case` and `CASES`, not the tests.

**Families** (T85e). The three cadence cases run `family="momentum"`; the
`profitability` case runs that family at `month_end` from 2018 (the fixture universe is
empty before), so the truncated set includes `statement_facts` and `classifications`,
whose fixture rows carry point-in-time cases on universe members (fixture README,
"Statement facts"). Its own teeth: the FY2018 10-K of SEC_TRANSFER (CIK0001000007),
accepted at 16:30 New York after close(2019-02-28), is not read by the plan at that
close, so it leaves `run(end=2019-03-29)` (whose last plan is at 2019-02-28) unchanged,
and changes `run(end=2019-04-30)`. A plan read at the fill session's close (2019-03-01)
would see it.

`engine.run` over a `StoreProvider` on the fixture universe, at every rebalance
session T_i of the case's window:

- **Truncation invariance**: `run(end=T_i)` on the full store equals `run(end=T_i)`
  with the data connection factory yielding `TruncatedStore.at(close(T_i))` and the
  registry connection the untruncated store (the truncated one has no registry
  tables). Equity, weights, rebalances, position values, marking frames and targets
  are compared whole, at every cost level.
- **Prefix invariance**: `run(end=T_i)` equals the prefix through T_i of
  `run(end=T_n)`.
- **Teeth**: on a synthetic copy of the store, three revisions each known an hour
  after close(T_i) leave `run(end=T_i)` unchanged and change `run(end=T_{i+1})`: a
  bar revision at T_i on a held name, a dividend revision with ex-date T_i on a name
  held across it (only the `known_at` filter keeps it out of the earlier run; the
  ex-date filter does not), and a dividend revision with ex-date inside step i. Each
  is checked alone, so any one reaching the earlier run fails. Inside step i means the
  session after the fill F_i, on a name filled there; at `daily` the step is the one
  session F_i = T_{i+1}, so it is that session, on a name held across T_i and still a
  target (the carry to the fill earns it).

**Seeded revisions.** The fixture's own revisions (SEC_SPLIT_BACKFILLED's bar,
SEC_DIV_REVISED's dividend) are on names the strategy never holds. So the store every
test runs on (`_store`) adds, at the case's seeded sessions (`Case.seeded`) and for each
of `SEEDED_IDS` (every name the run holds in 2018-2019), a bar revision at T_k, a
dividend first seen before its ex-date and restated after close(T_k), and a dividend
first seen only after close(T_k) (late), all known an hour after close(T_k); a seeded
dividend never lands on a fixture action's ex-date (`_store`, #1099) or on another
seeded one's (at `daily`, so only every third rebalance is seeded). The
truncation walk then crosses revision-type facts (a row dated at or before T but known
after it) on held names, which `test_the_run_is_not_vacuous` checks.

The frozen settings are the defaults except `strategy.top_fraction = 0.5`, so the
fixture's small universe (at most six names) yields several targets per rebalance
instead of one, the case's `schedule.rebalance_cadence`, and no benchmarks (`_frozen`);
nothing else about the run changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import get_args

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import TruncatedStore
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.config import Cadence, HypothesisFamily, Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"

#: The fixture's bars run 2017-01-03 through 2020-06-30 (fixture README).
FIXTURE_START = date(2017, 1, 3)
FIXTURE_END = date(2020, 6, 30)
COST_LEVELS = (0.0, 15.0)

#: When a synthetic revision becomes known: an hour after close(T), before the next close.
REVISION_DELAY = timedelta(hours=1)
#: The seeded dividends' ex-dates, in sessions before their T_k (restated, late).
RESTATED_EX_SESSIONS = 5
LATE_EX_SESSIONS = 3

#: Names seeded with revisions: every name the run holds in 2018-2019.
SEEDED_IDS = ("SEC_DUAL_A", "SEC_DUAL_B", "SEC_SPLIT_BETWEEN", "SEC_SPLIT_FUTURE", "SEC_TRANSFER")


@dataclass(frozen=True)
class Case:
    """One cadence's walk: the run window `[start, end]`, the teeth case's T_i (a 2019
    rebalance where the run holds three names) and the range seeded with revisions, every
    `seed_step`-th rebalance of it."""

    cadence: Cadence
    start: date
    end: date
    teeth: date
    seed_from: date
    seed_to: date
    seed_step: int = 1
    family: HypothesisFamily = "momentum"

    @property
    def id(self) -> str:
        """The pytest id: the cadence for a momentum case (ids unchanged), else the family."""
        return self.cadence if self.family == "momentum" else self.family

    @property
    def sessions(self) -> tuple[date, ...]:
        return tuple(rebalance_sessions(self.start, self.end, self.cadence))

    @property
    def teeth_next(self) -> date:
        return self.sessions[self.sessions.index(self.teeth) + 1]

    @property
    def seeded(self) -> tuple[date, ...]:
        """Rebalances in the seeded range, less those at or after the teeth case's T_i
        whose seeded ex-dates reach its step (T_i and T_{i+1} at `month_end`), so the
        teeth case's own revisions are the only ones on its dates."""
        return tuple(
            t_k
            for t_k in rebalance_sessions(self.seed_from, self.seed_to, self.cadence)[
                :: self.seed_step
            ]
            if not (
                self.teeth <= t_k and _sessions_before(t_k, RESTATED_EX_SESSIONS) <= self.teeth_next
            )
        )


#: One momentum case per cadence (`month_end` is T40's walk unchanged), then one per
#: other family.
CASES: dict[str, Case] = {
    case.id: case
    for case in (
        Case(
            "month_end",
            FIXTURE_START,
            FIXTURE_END,
            teeth=date(2019, 1, 31),
            seed_from=date(2018, 6, 1),
            seed_to=date(2019, 11, 30),
        ),
        Case(
            "week_end",
            date(2018, 10, 12),
            date(2019, 5, 3),
            teeth=date(2019, 2, 1),
            seed_from=date(2018, 10, 26),
            seed_to=date(2019, 5, 3),
        ),
        # Seeded from the sixth rebalance on, so a restated dividend's ex-date (five
        # sessions before its T_k) falls after the first fill; every third rebalance, so
        # no T_k's late ex-date (three sessions back) is another's restated one (five
        # back), which would make the late dividend a revision of that one (`_store`).
        Case(
            "daily",
            date(2019, 1, 2),
            date(2019, 2, 15),
            teeth=date(2019, 1, 31),
            seed_from=date(2019, 1, 9),
            seed_to=date(2019, 2, 15),
            seed_step=3,
        ),
        Case(
            "month_end",
            date(2018, 1, 2),
            FIXTURE_END,
            teeth=date(2019, 1, 31),
            seed_from=date(2018, 6, 1),
            seed_to=date(2019, 11, 30),
            family="profitability",
        ),
    )
}
assert tuple(c.cadence for c in CASES.values() if c.family == "momentum") == get_args(Cadence)

#: SEC_TRANSFER's FY2018 10-K (fixture README): its cik, acceptance and the close before.
LATE_10K = ("CIK0001000007", datetime(2019, 2, 28, 21, 30, tzinfo=UTC), date(2019, 2, 28))

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Results = Mapping[float, BacktestResult]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live
    # ones; keep the live ones free of any `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _frozen(cadence: Cadence, family: HypothesisFamily) -> Settings:
    # The `month_end` runs start in 2017, before the fixture benchmarks' first bar
    # (2018-01-02). Benchmarks are read by symbol (#840) and the engine refuses one with
    # no bar at F_0, so these runs name none, as they effectively did before (master
    # rows unknown); the other cadences follow suit, so only the cadence differs. A
    # profitability case takes the same top fraction from its own section.
    extra = {"profitability": {"top_fraction": 0.5}} if family == "profitability" else {}
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        schedule={"rebalance_cadence": cadence},
        benchmarks=[],
        **extra,
    )


def _store(case: Case) -> duckdb.DuckDBPyConnection:
    """The fixture universe plus `case`'s seeded revisions (module docstring)."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    # A seeded dividend has no source id, so the as-of read keys it by (type, ex-date):
    # two seeded on one ex-date would be revisions of one action, and a late dividend
    # would read as a restated one. Every seeded ex-date is distinct.
    ex_dates = [
        _sessions_before(t_k, n)
        for t_k in case.seeded
        for n in (RESTATED_EX_SESSIONS, LATE_EX_SESSIONS)
    ]
    assert len(set(ex_dates)) == len(ex_dates), f"seeded dividends share an ex-date: {case}"
    for t_k in case.seeded:
        revised_at = read_time(t_k, case.cadence) + REVISION_DELAY
        restated_ex = _sessions_before(t_k, RESTATED_EX_SESSIONS)
        late_ex = _sessions_before(t_k, LATE_EX_SESSIONS)
        for sid in SEEDED_IDS:
            _revise_bar(conn, sid, t_k, revised_at, factor=1.02)
            # Seeded dividends may share an ex-date with a fixture split. The
            # full and truncated stores must still yield bit-identical frames.
            _dividend(conn, sid, restated_ex, 0.2, session_close(previous_session(restated_ex)))
            _dividend(conn, sid, restated_ex, 0.3, revised_at)
            _dividend(conn, sid, late_ex, 0.25, revised_at)
    return conn


def _sessions_before(day: date, n: int) -> date:
    for _ in range(n):
        day = previous_session(day)
    return day


def _open_trial(
    conn: duckdb.DuckDBPyConnection, settings: Settings, case: Case
) -> registry.TrialHandle:
    """A synthetic trial on the in-memory store (never the real store)."""
    sessions = case.sessions
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-lookahead",
        family=case.family,
        title="look-ahead suite",
        doc_path="docs/hypotheses/h-lookahead.md",
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
        data_cutoff=read_time(sessions[-1], case.cadence),
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


@dataclass(frozen=True)
class Fixture:
    case: Case
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    sessions: tuple[date, ...]

    def read_time(self, session: date) -> datetime:
        return read_time(session, self.case.cadence)

    def run(
        self,
        end: date,
        connect: Connect | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
    ) -> Results:
        """`engine.run` from the first rebalance to `end`, reading through `connect`
        (default: `conn`, default the full store), the handle checked on this store."""
        data = connect or _factory(conn if conn is not None else self.conn)
        with StoreProvider(
            data, self.handle, self.settings, registry_connect=_factory(self.conn)
        ) as provider:
            return run(
                self.settings,
                provider,
                self.sessions[0],
                end,
                self.handle,
                COST_LEVELS,
                family=self.case.family,
            )


@pytest.fixture(scope="module", params=list(CASES.values()), ids=list(CASES))
def fixture(request: pytest.FixtureRequest) -> Iterator[Fixture]:
    case: Case = request.param
    conn = _store(case)
    settings = _frozen(case.cadence, case.family)
    try:
        yield Fixture(
            case=case,
            conn=conn,
            settings=settings,
            handle=_open_trial(conn, settings, case),
            sessions=case.sessions,
        )
    finally:
        conn.close()


@pytest.fixture(scope="module")
def full_runs(fixture: Fixture) -> dict[date, Results]:
    """`run(end=T_i)` on the full store for every T_i after the first: the walk's cost,
    read only by the truncation and prefix tests (under xdist each worker that runs one
    of them builds it again)."""
    return {end: fixture.run(end) for end in fixture.sessions[1:]}


@pytest.fixture(scope="module")
def longest(fixture: Fixture) -> Results:
    """`run(end=T_n)` on the full store, for the tests that need only that run."""
    return fixture.run(fixture.sessions[-1])


# --- comparison ------------------------------------------------------------------


def _assert_same(got: Results, want: Results, context: str) -> None:
    assert got.keys() == want.keys(), context
    for level, result in want.items():
        other = got[level]
        where = f"{context}, cost level {level}"
        assert other.equity == result.equity, f"equity differs: {where}"
        assert other.weights == result.weights, f"weights differ: {where}"
        assert other.rebalances == result.rebalances, f"rebalances differ: {where}"
        assert other.position_values.equals(result.position_values), where
        assert other.targets == result.targets, f"targets differ: {where}"
        assert len(other.marking_frames) == len(result.marking_frames), where
        for a, b in zip(other.marking_frames, result.marking_frames, strict=True):
            assert (a.start, a.end) == (b.start, b.end), where
            assert a.frame.equals(b.frame), f"marking frame {a.start}..{a.end}: {where}"


def _prefix(result: BacktestResult, end: date) -> BacktestResult:
    """The part of a longer run that a run ending at rebalance `end` produces."""
    return BacktestResult(
        cost_per_side_bps=result.cost_per_side_bps,
        equity=tuple(row for row in result.equity if row.session <= end),
        rebalances=tuple(row for row in result.rebalances if row.session < end),
        weights=tuple(row for row in result.weights if row.fill_session <= end),
        position_values=result.position_values.filter(pl.col("session") <= end),
        marking_frames=tuple(f for f in result.marking_frames if f.end <= end),
        targets={fill: t for fill, t in result.targets.items() if fill <= end},
    )


# --- the suite -------------------------------------------------------------------


def test_the_run_is_not_vacuous(fixture: Fixture, longest: Results) -> None:
    result = longest[COST_LEVELS[-1]]
    seeded = fixture.case.seeded
    assert len(fixture.sessions) >= 30
    # The frozen cadence reached the engine: it rebalanced on exactly the case's sessions.
    assert [row.session for row in result.rebalances] == list(fixture.sessions[:-1])
    assert max(row.n_targets for row in result.rebalances) >= 3
    assert sum(row.turnover > 0 for row in result.rebalances) >= 10
    # The walk crosses seeded revisions on held names: at most seeded T_k a seeded
    # name is held at the close whose bar is revised, and late dividends are counted.
    revised_and_held = [t_k for t_k in seeded if set(_held_at_close(result, t_k)) & set(SEEDED_IDS)]
    assert len(revised_and_held) >= len(seeded) - 2
    assert sum(row.n_late_dividends for row in result.rebalances) >= 5
    # One step per seeded T_k counts its late dividends (none merged into a restatement).
    assert sum(row.n_late_dividends > 0 for row in result.rebalances) >= len(seeded) - 2
    # And a restated seeded dividend is held across its ex-date.
    entitled = [
        t_k
        for t_k in seeded
        if set(
            _held_at_close(result, previous_session(_sessions_before(t_k, RESTATED_EX_SESSIONS)))
        )
        & set(SEEDED_IDS)
    ]
    assert len(entitled) >= len(seeded) - 2


def test_truncation_invariance_at_every_rebalance(
    fixture: Fixture, full_runs: dict[date, Results]
) -> None:
    truncated = TruncatedStore(fixture.conn)
    try:
        for end, full in full_runs.items():
            cut_conn = truncated.at(fixture.read_time(end))
            cut = fixture.run(end, connect=_factory(cut_conn))
            _assert_same(cut, full, f"run(end={end}) on the store truncated to close({end})")
    finally:
        truncated.close()


def test_prefix_invariance_at_every_rebalance(
    fixture: Fixture, full_runs: dict[date, Results]
) -> None:
    longest = full_runs[fixture.sessions[-1]]
    for end, shorter in full_runs.items():
        prefix = {level: _prefix(result, end) for level, result in longest.items()}
        _assert_same(shorter, prefix, f"run(end={end}) against the prefix of the full run")


# --- teeth -----------------------------------------------------------------------


def _held_at_close(result: BacktestResult, session: date) -> list[str]:
    values = result.position_values.filter((pl.col("session") == session) & (pl.col("value") > 0))
    return sorted(values["security_id"].to_list())


def _revise_bar(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    session: date,
    known_at: datetime,
    factor: float = 1.1,
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


def _dividend(
    conn: duckdb.DuckDBPyConnection, sid: str, ex_date: date, amount: float, known_at: datetime
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": "dividend",
            "ex_date": ex_date,
            "ratio_or_amount": amount,
            "announced_at": None,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def _step_frame(result: BacktestResult, start: date) -> pl.DataFrame:
    [frame] = [f.frame for f in result.marking_frames if f.start == start]
    return frame


def test_revisions_known_after_t_i_leave_run_to_t_i_unchanged(fixture: Fixture) -> None:
    case = fixture.case
    t_i, t_next = case.teeth, case.teeth_next
    revised_at = fixture.read_time(t_i) + REVISION_DELAY
    assert revised_at < fixture.read_time(t_next)

    base = fixture.run(t_i)[COST_LEVELS[-1]]
    # The bar revision hits a name held at close(T_i): carrying it to F_i reads that close.
    held = _held_at_close(base, t_i)
    assert held, f"nothing held at close({t_i})"
    # The ex-T_i dividend hits a name held at the close before T_i and at T_i. At
    # close(T_i) its ex-date filter admits it, so only `known_at` keeps the revision out.
    entitled = sorted(set(held) & set(_held_at_close(base, previous_session(t_i))))
    assert entitled, f"nothing held across {t_i}"
    # The in-step dividend hits a name held through step i, ex-date inside (F_i, T_{i+1}];
    # at `daily` that interval is empty, so ex-date F_i = T_{i+1} on a target held across
    # T_i, which the carry to the fill pays.
    fill = fill_session(t_i, case.cadence)
    targets = sorted(fixture.run(t_next)[COST_LEVELS[-1]].targets[fill])
    assert targets, f"no targets filled on {fill}"
    in_step_ex = next_session(fill)
    if in_step_ex > t_next:
        assert fill == t_next
        in_step_ex = fill
        targets = sorted(set(targets) & set(held))
        assert targets, f"no target filled on {fill} is held across {t_i}"
    first_seen = session_close(previous_session(t_i)) - REVISION_DELAY

    def store(revision: str | None) -> duckdb.DuckDBPyConnection:
        conn = _store(case)
        # First seen before T_i in every variant, so only the revision differs.
        _dividend(conn, entitled[0], t_i, 0.5, first_seen)
        _dividend(conn, targets[0], in_step_ex, 0.5, first_seen)
        if revision == "bar at T_i":
            _revise_bar(conn, held[0], t_i, revised_at)
        elif revision == "dividend ex T_i":
            _dividend(conn, entitled[0], t_i, 2.5, revised_at)
        elif revision == "dividend in step i":
            _dividend(conn, targets[0], in_step_ex, 2.5, revised_at)
        return conn

    plain = store(None)
    try:
        want_i = fixture.run(t_i, conn=plain)
        want_next = fixture.run(t_next, conn=plain)
    finally:
        plain.close()
    for revision in ("bar at T_i", "dividend ex T_i", "dividend in step i"):
        conn = store(revision)
        try:
            _assert_same(fixture.run(t_i, conn=conn), want_i, f"{revision}, run to {t_i}")
            got_next = fixture.run(t_next, conn=conn)
        finally:
            conn.close()
        for level in COST_LEVELS:
            got, want = got_next[level], want_next[level]
            if revision == "dividend ex T_i":
                # Ex-date before the step's carry: only pre-ex adjusted levels move.
                changed = not _step_frame(got, t_i).equals(_step_frame(want, t_i))
            else:
                changed = got.equity != want.equity
            assert changed, f"{revision} known at {revised_at} did not change run(end={t_next})"


def test_a_10k_accepted_after_close_t_i_reaches_only_runs_planning_after_t_i(
    fixture: Fixture,
) -> None:
    if fixture.case.family != "profitability":
        pytest.skip("only the profitability family reads statement facts")
    cik, accepted, t_i = LATE_10K
    i = fixture.sessions.index(t_i)
    t_next, t_after = fixture.sessions[i + 1], fixture.sessions[i + 2]
    # After the plan's read at T_i, before the fill session's close and the next plan's read.
    fill_close = session_close(fill_session(t_i, fixture.case.cadence))
    assert fixture.read_time(t_i) < accepted < fill_close < fixture.read_time(t_next)
    without = _store(fixture.case)
    try:
        query = "DELETE FROM statement_facts WHERE cik = ? AND known_at = ?"
        assert without.execute(query, [cik, accepted]).fetchone() == (2,)
        _assert_same(fixture.run(t_next, conn=without), fixture.run(t_next), f"run to {t_next}")
        got, want = fixture.run(t_after, conn=without), fixture.run(t_after)
    finally:
        without.close()
    for level in COST_LEVELS:
        assert got[level].targets != want[level].targets, f"run to {t_after}, level {level}"

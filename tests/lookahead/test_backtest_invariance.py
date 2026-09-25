"""No look-ahead for the full backtest run (backtest spec req 13, "Look-ahead"; plan T40).

`engine.run` over a `StoreProvider` on the fixture universe, at every rebalance
session T_i of the fixture range:

- **Truncation invariance**: `run(end=T_i)` on the full store equals `run(end=T_i)`
  with the data connection factory yielding `TruncatedStore.at(close(T_i))` and the
  registry connection the untruncated store (the truncated one has no registry
  tables). Equity, weights, rebalances, position values, marking frames and targets
  are compared whole, at every cost level.
- **Prefix invariance**: `run(end=T_i)` equals the prefix through T_i of
  `run(end=T_n)`.
- **Teeth**: on a synthetic copy of the store, a bar revision and a dividend
  revision, each known after close(T_i), leave `run(end=T_i)` unchanged and change
  `run(end=T_{i+1})`. Each revision is checked alone, so either one reaching the
  earlier run would fail.

The frozen settings are the defaults except `strategy.top_fraction = 0.5`, so the
fixture's small universe (at most six names) yields several targets per rebalance
instead of one; nothing else about the run changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import TruncatedStore
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import next_session
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"

#: The fixture's bars run 2017-01-03 through 2020-06-30 (fixture README).
FIXTURE_START = date(2017, 1, 3)
FIXTURE_END = date(2020, 6, 30)
COST_LEVELS = (0.0, 15.0)

#: The teeth case's T_i: a 2019 rebalance where the run holds three names.
T_TEETH = date(2019, 1, 31)
#: When the synthetic revisions become known: after close(T_TEETH), before the next close.
REVISION_DELAY = timedelta(hours=1)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Results = Mapping[float, BacktestResult]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live
    # ones; keep the live ones free of any `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _frozen() -> Settings:
    return Settings(_env_file=None, strategy={"top_fraction": 0.5})


def _fixture_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    return conn


def _open_trial(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.TrialHandle:
    """A synthetic trial on the in-memory store (never the real store)."""
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-lookahead",
        family="momentum",
        title="look-ahead suite",
        doc_path="docs/hypotheses/h-lookahead.md",
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


@dataclass(frozen=True)
class Fixture:
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    sessions: tuple[date, ...]

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
            return run(self.settings, provider, self.sessions[0], end, self.handle, COST_LEVELS)


@pytest.fixture(scope="module")
def fixture() -> Iterator[Fixture]:
    conn = _fixture_conn()
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
def full_runs(fixture: Fixture) -> dict[date, Results]:
    """`run(end=T_i)` on the full store for every T_i after the first."""
    return {end: fixture.run(end) for end in fixture.sessions[1:]}


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


def test_the_run_is_not_vacuous(fixture: Fixture, full_runs: dict[date, Results]) -> None:
    result = full_runs[fixture.sessions[-1]][COST_LEVELS[-1]]
    assert len(fixture.sessions) > 40
    assert max(row.n_targets for row in result.rebalances) >= 3
    assert sum(row.turnover > 0 for row in result.rebalances) >= 10
    # A name with a fill-session bar missing (SEC_WINDOW_DELIST after its last bar).
    assert any(row.n_missing_fill for row in result.rebalances)


def test_truncation_invariance_at_every_rebalance(
    fixture: Fixture, full_runs: dict[date, Results]
) -> None:
    truncated = TruncatedStore(fixture.conn)
    try:
        for end, full in full_runs.items():
            cut_conn = truncated.at(read_time(end))
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
    conn: duckdb.DuckDBPyConnection, sid: str, session: date, known_at: datetime
) -> None:
    """A second `prices_daily` row for (sid, session), every price 10% higher."""
    [row] = (
        prices_as_of(conn, read_time(session), [sid])
        .filter(pl.col("session") == session)
        .iter_rows(named=True)
    )
    for column in ("open", "high", "low", "close"):
        row[column] = row[column] * 1.1
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


def test_revisions_known_after_t_i_leave_run_to_t_i_unchanged(
    fixture: Fixture, full_runs: dict[date, Results]
) -> None:
    sessions = fixture.sessions
    i = sessions.index(T_TEETH)
    t_i, t_next = sessions[i], sessions[i + 1]
    revised_at = read_time(t_i) + REVISION_DELAY
    assert revised_at < read_time(t_next)

    base = full_runs[t_i][COST_LEVELS[-1]]
    # The bar revision hits a name held at close(T_i): carrying it to F_i reads that close.
    held = _held_at_close(base, t_i)
    assert held, f"nothing held at close({t_i})"
    # The dividend hits a name held through step i, ex-date inside (F_i, T_{i+1}].
    fill = fill_session(t_i)
    targets = sorted(full_runs[t_next][COST_LEVELS[-1]].targets[fill])
    assert targets, f"no targets filled on {fill}"
    ex_date = next_session(fill)
    assert ex_date <= t_next

    def store(*, revise_bar: bool, revise_dividend: bool) -> duckdb.DuckDBPyConnection:
        conn = _fixture_conn()
        # First seen before T_i in every variant, so only the revision differs.
        _dividend(conn, targets[0], ex_date, 0.5, read_time(t_i) - timedelta(days=1))
        if revise_bar:
            _revise_bar(conn, held[0], t_i, revised_at)
        if revise_dividend:
            _dividend(conn, targets[0], ex_date, 2.5, revised_at)
        return conn

    plain = store(revise_bar=False, revise_dividend=False)
    try:
        want_i = fixture.run(t_i, conn=plain)
        want_next = fixture.run(t_next, conn=plain)
    finally:
        plain.close()
    for revision in ("bar", "dividend"):
        conn = store(revise_bar=revision == "bar", revise_dividend=revision == "dividend")
        try:
            _assert_same(fixture.run(t_i, conn=conn), want_i, f"{revision} revision, run to {t_i}")
            got_next = fixture.run(t_next, conn=conn)
        finally:
            conn.close()
        for level in COST_LEVELS:
            assert got_next[level].equity != want_next[level].equity, (
                f"the {revision} revision known at {revised_at} did not change run(end={t_next})"
            )

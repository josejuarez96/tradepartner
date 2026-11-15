"""Behavioral look-ahead check (#3427): a backtest cut at date D decides what a longer
run decided up to D, and does not depend on anything known after D.

The suite beside this one (`test_backtest_invariance.py`) walks every rebalance of a
revision-seeded fixture. This file asks the plainer question a reviewer asks of any
engine ("run it to D, run it to D + N, compare the part they share") with the fixture
events a real window contains, at the three cadences:

- a **corporate action**: SEC_SPLIT_BETWEEN's 3-for-1 split (ex-date 2019-01-11) and
  SEC_SPLIT_FUTURE's 4-for-1 (ex-date 2019-02-14), both held across their ex-dates;
- a **delisting**: SEC_WINDOW_DELIST's last bar is 2019-06-24 and its Form 25 is filed
  2019-06-28 (known_at 20:30 UTC), so the run books a delisting exit.

Each case runs the engine to D and to D + `EXTRA_REBALANCES` rebalances, on the fixture
store and on that store truncated to `known_at <= read_time(D)`. Everything the shorter
run produced (decisions: targets and rebalance rows; orders and fills: weight rows;
positions: per-session values; cash and equity; delisting exits) must equal the part of
the longer run that falls at or before D. The longer run's rows after D are read only to
prove the window is not vacuous. Scope: no benchmarks are frozen and the risk-free rates
read is off in these runs, and the plan made at D itself (filled after D) is left out of
"up to D" by design. Fixture stores only; the real store is never opened.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from lookahead.harness import FIXTURE_UNIVERSE, FixtureTemplate, TruncatedStore
from lookahead.test_backtest_invariance import _dividend, _revise_bar
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import previous_session
from tradepartner.config import Cadence, Settings
from tradepartner.store import registry

COST_LEVELS = (0.0, 15.0)
EXTRA_REBALANCES = 5
WINDOW_START = date(2018, 12, 31)

DELISTED = "SEC_WINDOW_DELIST"
#: (security, ex-date) of the splits in the window that the run holds across.
SPLITS = (("SEC_SPLIT_BETWEEN", date(2019, 1, 11)), ("SEC_SPLIT_FUTURE", date(2019, 2, 14)))


@dataclass(frozen=True)
class Case:
    """One cadence's window: it starts at `WINDOW_START` and the cut `cut` is the first
    rebalance at which the delisting exit is booked (so both events lie inside)."""

    cadence: Cadence
    cut: date


CASES = (
    Case("month_end", date(2019, 7, 31)),
    Case("daily", date(2019, 7, 12)),
    Case("week_end", date(2019, 7, 26)),
)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> FixtureTemplate:
    return FixtureTemplate(tmp_path_factory.mktemp("cut-date-template"), FIXTURE_UNIVERSE)


def _settings(cadence: Cadence) -> Settings:
    # No benchmarks (the window starts before the fixture's benchmark bars in other
    # suites' ranges); top_fraction 0.5 so the small universe yields several targets.
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        schedule={"rebalance_cadence": cadence},
        benchmarks=[],
    )


def _handle(
    conn: duckdb.DuckDBPyConnection, settings: Settings, case: Case, end: date
) -> registry.TrialHandle:
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-cut-date",
        family="momentum",
        title="cut-date look-ahead",
        doc_path="docs/hypotheses/h-cut-date.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": COST_LEVELS[-1]},
        in_sample_start=WINDOW_START,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=WINDOW_START,
        end_session=end,
        data_cutoff=read_time(end, case.cadence),
        synthetic=True,
        run_by="test",
        settings=settings,
    )


@contextmanager
def _lend(conn: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    yield conn


@dataclass(frozen=True)
class Runs:
    case: Case
    conn: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    sessions: tuple[date, ...]  # rebalances, WINDOW_START .. D + N
    cut: date
    long_end: date
    longer: dict[float, BacktestResult]

    def run(
        self, end: date, data: duckdb.DuckDBPyConnection | None = None
    ) -> dict[float, BacktestResult]:
        """`engine.run` from the window start to `end`, reading `data` (default: the
        full fixture store); the trial registry is always the full store."""
        source = data if data is not None else self.conn
        with StoreProvider(
            lambda: _lend(source),
            self.handle,
            self.settings,
            registry_connect=lambda: _lend(self.conn),
        ) as provider:
            return run(
                self.settings,
                provider,
                self.sessions[0],
                end,
                self.handle,
                COST_LEVELS,
                family="momentum",
            )


@pytest.fixture(scope="module", params=CASES, ids=[c.cadence for c in CASES])
def runs(request: pytest.FixtureRequest, template: FixtureTemplate) -> Iterator[Runs]:
    case: Case = request.param
    sessions = rebalance_sessions(WINDOW_START, date(2019, 12, 31), case.cadence)
    i = sessions.index(case.cut)
    window = tuple(sessions[: i + EXTRA_REBALANCES + 1])
    conn = template.copy()
    settings = _settings(case.cadence)
    try:
        handle = _handle(conn, settings, case, window[-1])
        shell = Runs(case, conn, settings, handle, window, case.cut, window[-1], {})
        longer = shell.run(window[-1])
        yield Runs(case, conn, settings, handle, window, case.cut, window[-1], longer)
    finally:
        conn.close()


# --- what "up to D" means ---------------------------------------------------------


@dataclass(frozen=True)
class UpToCut:
    """The parts of a run that are decided at or before the cut rebalance D."""

    targets: dict[date, dict[str, float]]  # decisions, keyed by fill session
    rebalances: tuple[object, ...]  # decisions: the per-rebalance counts, turnover, cost
    orders: tuple[object, ...]  # fills: price and shares per (fill session, security)
    positions: pl.DataFrame  # value of every position at every close
    cash: tuple[tuple[str, float, date, float | None], ...]  # series, level, session, cash
    equity: tuple[tuple[str, float, date, float], ...]
    delisting_exits: tuple[object, ...]


def _up_to(result: BacktestResult, cut: date, cadence: Cadence) -> UpToCut:
    """Rows of `result` that a run ending at the rebalance `cut` produces: equity and
    positions through the cut's close, rebalances planned before it, fills on or before
    it, and exits decided by a read at or before the cut's read."""
    when = read_time(cut, cadence)
    equity = [row for row in result.equity if row.session <= cut]
    return UpToCut(
        targets={fill: dict(t) for fill, t in result.targets.items() if fill <= cut},
        rebalances=tuple(row for row in result.rebalances if row.session < cut),
        orders=tuple(row for row in result.weights if row.fill_session <= cut),
        positions=result.position_values.filter(pl.col("session") <= cut),
        cash=tuple((r.series, r.cost_per_side_bps, r.session, r.cash) for r in equity),
        equity=tuple((r.series, r.cost_per_side_bps, r.session, r.equity) for r in equity),
        delisting_exits=tuple(e for e in result.delisting_exits if e.read_time <= when),
    )


def _assert_same_up_to_cut(got: UpToCut, want: UpToCut, where: str) -> None:
    assert got.targets == want.targets, f"decisions (targets) differ: {where}"
    assert got.rebalances == want.rebalances, f"rebalance rows differ: {where}"
    assert got.orders == want.orders, f"orders/fills differ: {where}"
    assert got.positions.equals(want.positions), f"positions differ: {where}"
    assert got.cash == want.cash, f"cash differs: {where}"
    assert got.equity == want.equity, f"equity differs: {where}"
    assert got.delisting_exits == want.delisting_exits, f"delisting exits differ: {where}"


# --- the suite ----------------------------------------------------------------------


def test_the_window_holds_a_corporate_action_and_a_delisting(runs: Runs) -> None:
    """Non-vacuity: both events lie inside the compared part, and the longer run goes on
    past the cut."""
    cut = runs.cut
    assert runs.sessions[-1] > cut
    assert len(runs.sessions) - runs.sessions.index(cut) - 1 == EXTRA_REBALANCES
    result = runs.longer[COST_LEVELS[-1]]
    held = result.position_values.filter(pl.col("value") > 0)
    across = 0
    for sid, ex_date in SPLITS:
        assert runs.sessions[0] < ex_date < cut
        before = held.filter(
            (pl.col("security_id") == sid) & (pl.col("session") == previous_session(ex_date))
        )
        on = held.filter((pl.col("security_id") == sid) & (pl.col("session") == ex_date))
        across += before.height == 1 and on.height == 1
    # At least one split is held across its ex-date (which names are held depends on the
    # cadence's rebalance dates).
    assert across >= 1, f"no split in {SPLITS} is held across its ex-date"
    # The delisting exit is booked by a read at or before the cut, and the name is held
    # until it ends (last bar 2019-06-24 < the cut).
    exits = [e for e in result.delisting_exits if e.security_id == DELISTED]
    assert [e.end_session for e in exits] == [date(2019, 6, 24)]
    assert exits[0].read_time <= read_time(cut, runs.case.cadence)
    assert held.filter(pl.col("security_id") == DELISTED).height > 0
    # Something happens after the cut for the comparison to be able to differ.
    assert any(row.session >= cut for row in result.rebalances)
    assert any(fill > cut for fill in result.targets)


def test_a_run_cut_at_d_equals_the_longer_run_up_to_d(runs: Runs) -> None:
    shorter = runs.run(runs.cut)
    assert shorter.keys() == runs.longer.keys()
    for level, result in shorter.items():
        _assert_same_up_to_cut(
            _up_to(result, runs.cut, runs.case.cadence),
            _up_to(runs.longer[level], runs.cut, runs.case.cadence),
            f"{runs.case.cadence}: run(end={runs.cut}) vs run(end={runs.long_end}), level {level}",
        )


def test_a_run_cut_at_d_reads_nothing_known_after_d(runs: Runs) -> None:
    """`run(end=D)` on a store holding only rows known by close(D) (the report time of
    the cut rebalance) equals the longer run up to D."""
    when = read_time(runs.cut, runs.case.cadence)
    later = runs.conn.execute(
        "SELECT (SELECT count(*) FROM prices_daily WHERE known_at > ?)"
        " + (SELECT count(*) FROM corporate_actions WHERE known_at > ?)"
        " + (SELECT count(*) FROM delistings WHERE known_at > ?)",
        [when, when, when],
    ).fetchone()
    assert later is not None and later[0] > 0, "the store has nothing known after the cut"
    truncated = TruncatedStore(runs.conn)
    try:
        cut_conn = truncated.at(when)
        cut = runs.run(runs.cut, data=cut_conn)
    finally:
        truncated.close()
    for level, result in cut.items():
        _assert_same_up_to_cut(
            _up_to(result, runs.cut, runs.case.cadence),
            _up_to(runs.longer[level], runs.cut, runs.case.cadence),
            f"{runs.case.cadence}: run(end={runs.cut}) on the store truncated to {when}",
        )


def test_a_bar_or_dividend_known_after_d_leaves_the_run_to_d_unchanged(
    runs: Runs, template: FixtureTemplate
) -> None:
    """Teeth for the price and adjustment reads: a revised bar at D and a dividend with
    ex-date D, on a name held at D's close, both first known an hour after read_time(D),
    change the longer run but not the run to D (a read time shifted past them would)."""
    cut, cadence = runs.cut, runs.case.cadence
    held = (
        runs.longer[COST_LEVELS[-1]]
        .position_values.filter((pl.col("session") == cut) & (pl.col("value") > 0))["security_id"]
        .to_list()
    )
    assert held, f"nothing held at close({cut})"
    known = read_time(cut, cadence) + timedelta(hours=1)
    conn = template.copy()
    try:
        _revise_bar(conn, held[0], cut, known)
        _dividend(conn, held[0], cut, 5.0, known)
        handle = _handle(conn, runs.settings, runs.case, runs.long_end)
        revised = Runs(
            runs.case, conn, runs.settings, handle, runs.sessions, cut, runs.long_end, {}
        )
        got = revised.run(cut)
        longer = revised.run(runs.long_end)
    finally:
        conn.close()
    for level, result in got.items():
        _assert_same_up_to_cut(
            _up_to(result, cut, cadence),
            _up_to(runs.longer[level], cut, cadence),
            f"{cadence}: run(end={cut}) with a bar and a dividend known after read_time({cut})",
        )
    assert longer[COST_LEVELS[-1]].equity != runs.longer[COST_LEVELS[-1]].equity, (
        "the late revisions changed nothing even in the longer run"
    )

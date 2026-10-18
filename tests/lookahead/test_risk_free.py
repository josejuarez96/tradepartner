"""No look-ahead for the risk-free series (backtest spec, amendment 2026-10-11 #1498,
"Look-ahead"; plan T170c).

A run ending at T is a function of the `rates` rows with `known_at <= close(T)`. Three
cases, over a `store`-source momentum run on the fixture universe (its `rates.csv` is a
generated DTB3-shaped series, one observation per session, stamped at the close of the
session after the vintage):

- **Truncation invariance**: at every rebalance T_i, the metric rows of `run(end=T_i)` on
  the full store equal those of the same run reading the store truncated to close(T_i)
  (the truncated set includes `rates`; the registry connection is the untruncated store).
- **Teeth**: a synthetic observation (a quote of 40%, on a weekend date after T_i, so no
  fixture row shares its identity) first known an hour after close(T_i) changes no
  metric of `run(end=T_i)` and changes `run(end=T_{i+1})`; the same row known at close(T_i)
  changes `run(end=T_i)`.
- **Per-session filter**: the rate the metrics use at each session s of the window is the
  last observation of `rates_as_of(close(s))`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import TruncatedStore
from tradepartner.backtest import results as results_module
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.results import metric_rows
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import all_sessions, is_session, session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import rates_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
START, END = date(2019, 1, 2), date(2019, 9, 30)
LEVELS = (0.0, 15.0)
SERIES = "DTB3"
SYNTHETIC_QUOTE = 40.0
TEETH_DELAY = timedelta(hours=1)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Rows = dict[tuple[str, float, str], float | None]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        metrics={"risk_free_source": "store"},
    )


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    return conn


def _factory(conn: duckdb.DuckDBPyConnection) -> Connect:
    @contextmanager
    def _lend() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return _lend


def _handle(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.TrialHandle:
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-risk-free",
        family="momentum",
        title="risk-free look-ahead",
        doc_path="docs/hypotheses/h-risk-free.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": LEVELS[-1]},
        in_sample_start=START,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=START,
        end_session=END,
        data_cutoff=read_time(rebalance_sessions(START, END)[-1]),
        synthetic=True,
        run_by="test",
        settings=settings,
    )


class Walk:
    """The fixture store, its trial and the runs over it."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn
        self.settings = _settings()
        self.handle = _handle(conn, self.settings)
        self.sessions = rebalance_sessions(START, END)

    def run(self, end: date, connect: Connect | None = None) -> Mapping[float, BacktestResult]:
        data = connect or _factory(self.conn)
        with StoreProvider(
            data, self.handle, self.settings, registry_connect=_factory(self.conn)
        ) as provider:
            return run(
                self.settings, provider, self.sessions[0], end, self.handle, LEVELS,
                family="momentum",
            )  # fmt: skip

    def metrics(self, end: date, connect: Connect | None = None) -> Rows:
        """Every metric row of `run(end=end)` as `(series, level, key) -> value`."""
        rows = metric_rows(
            self.run(end, connect), self.settings, cadence="month_end", family="momentum"
        )
        return {(r.series, r.cost_per_side_bps, r.metric): r.value for r in rows}


@pytest.fixture
def walk() -> Iterator[Walk]:
    conn = _store()
    try:
        yield Walk(conn)
    finally:
        conn.close()


def _weekend_after(session: date) -> date:
    day = session + timedelta(days=1)
    while is_session(day):
        day += timedelta(days=1)
    return day


def _add_observation(
    conn: duckdb.DuckDBPyConnection, observed: date, known_at: datetime, value: float
) -> None:
    insert_row(
        conn,
        "rates",
        {
            "series": SERIES,
            "observation_date": observed,
            "value": value,
            "vintage_date": observed,
            "known_at": known_at,
            "ingested_at": known_at + timedelta(minutes=10),
            "source": "fred",
            "provenance": "release",
        },
    )


def test_the_run_is_not_vacuous(walk: Walk) -> None:
    rows = walk.metrics(walk.sessions[-1])
    annual = rows[("strategy", LEVELS[-1], "risk_free_annual")]
    assert annual is not None and 0.0 < annual < 0.05
    assert len(walk.sessions) >= 8


def test_truncation_invariance_at_every_rebalance(walk: Walk) -> None:
    truncated = TruncatedStore(walk.conn)
    try:
        for end in walk.sessions[2:]:
            full = walk.metrics(end)
            cut = walk.metrics(end, _factory(truncated.at(read_time(end))))
            assert cut == full, f"run(end={end}) on the store truncated to close({end})"
    finally:
        truncated.close()


def test_a_rate_known_after_close_changes_no_metric_and_one_known_by_it_does(walk: Walk) -> None:
    index = 4
    t_i, t_next = walk.sessions[index], walk.sessions[index + 1]
    observed = _weekend_after(t_i)
    assert observed < t_next
    before_to_i, before_next = walk.metrics(t_i), walk.metrics(t_next)

    _add_observation(walk.conn, observed, session_close(t_i) + TEETH_DELAY, SYNTHETIC_QUOTE)
    assert walk.metrics(t_i) == before_to_i
    changed = walk.metrics(t_next)
    assert changed != before_next
    key = ("strategy", LEVELS[-1], "risk_free_annual")
    assert changed[key] > before_next[key]  # type: ignore[operator]

    walk.conn.execute("DELETE FROM rates WHERE observation_date = ?", [observed])
    _add_observation(walk.conn, observed, session_close(t_i), SYNTHETIC_QUOTE)
    known = walk.metrics(t_i)
    assert known != before_to_i
    assert known[key] > before_to_i[key]  # type: ignore[operator]


def test_the_session_filter_is_the_as_of_read_at_each_close(walk: Walk) -> None:
    end = walk.sessions[-1]
    result = walk.run(end)[LEVELS[-1]]
    sessions = [s for s in all_sessions() if walk.sessions[0] <= s <= end]
    assert len(sessions) > 100
    used = results_module._rates_by_session(result.rates, SERIES, sessions)
    assert set(used) == set(sessions[1:])
    for session in sessions[1:]:
        known = rates_as_of(walk.conn, session_close(session), SERIES)
        assert known.height > 0
        latest = known.sort("observation_date").row(-1, named=True)
        assert used[session] == pytest.approx(latest["value"] / 100, abs=0.0)
        assert latest["known_at"] <= session_close(session)

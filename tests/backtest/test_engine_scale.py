"""The scale harness (backtest spec amendment 2026-10-18, "the scale budget"; T207, #1742).

A `daily` trial over a synthetic 1,000-name, 10-year fixture must finish within
`BACKTEST_SCALE_BUDGET_SECONDS` on the test machine. The fixture is deterministic (a
seeded generator, no I/O); the test is marked `slow`, skipped unless
`RUN_SLOW_TESTS=1` (it takes hours until T208 bounds the per-step marking read), and
prints its wall clock whether it meets the budget or not. A small smoke run of the same
generator and engine path stays in the default suite.
"""

from __future__ import annotations

import os
import time
from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from tradepartner.backtest.engine import run
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.calendar import all_sessions, last_completed_session, session_close
from tradepartner.config import Settings
from tradepartner.store import registry

#: Wall-clock budget, in seconds, for one 10-year, 1,000-name `daily` trial on the test
#: machine (spec amendment 2026-10-18; open question 20 has the 1,800 s alternative).
BACKTEST_SCALE_BUDGET_SECONDS = 600

SCALE_NAMES = 1_000
SCALE_SEED = 20_261_018
#: The run window: ten calendar years of `daily` rebalances, after a year of history for
#: the 12-1 momentum lookback.
SCALE_RUN_START = date(2014, 1, 2)
SCALE_RUN_END = date(2023, 12, 29)
SCALE_HISTORY_START = date(2012, 12, 3)
REENTRY_NAME = "N0004"  # top-decile momentum at both sides of the fixture's gap
REENTRY_GAP_START = date(2016, 1, 4)
REENTRY_RETURN = date(2022, 1, 3)


def scale_prices(
    names: int, history_start: date, end: date, *, seed: int = SCALE_SEED
) -> pl.DataFrame:
    """Deterministic synthetic bars: `names` securities on every XNYS session from
    `history_start` through `end`, a seeded log-normal walk per name, an open between
    the previous close and the close, `known_at` the session's close (UTC)."""
    sessions = [s for s in all_sessions() if history_start <= s <= end]
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0003, 0.0002, size=(1, names))
    shocks = rng.normal(0.0, 0.02, size=(len(sessions), names)) + drift
    closes = 100.0 * np.exp(np.cumsum(shocks, axis=0))
    previous = np.vstack([np.full((1, names), 100.0), closes[:-1]])
    opens = previous * (1.0 + rng.uniform(-0.005, 0.005, size=(len(sessions), names)))
    rows = np.repeat(np.arange(len(sessions)), names)
    session_col = pl.Series("session", sessions, dtype=pl.Date)
    known_col = pl.Series("known_at", [session_close(s) for s in sessions])
    return pl.DataFrame(
        {
            "security_id": np.tile([f"N{i:04d}" for i in range(names)], len(sessions)),
            "session": session_col.gather(rows),
            "open": opens.ravel(),
            "close": closes.ravel(),
            "known_at": known_col.gather(rows).cast(pl.Datetime("us", "UTC")),
        }
    )


class _ScaleProvider(FakeProvider):
    """The fixture has one immutable bar per name and session.

    Serve its contiguous session blocks directly. The general FakeProvider
    sorts all 2.7 million bars on every read to resolve revisions that this
    fixture does not contain; that setup cost would hide the engine budget.
    """

    def __init__(self, prices: pl.DataFrame, members: dict[date, list[str]]) -> None:
        super().__init__(prices=prices, members=members, benchmarks={})
        self._sessions = prices["session"].unique(maintain_order=True).to_list()
        self._names = len(members[next(iter(members))])
        self._name_index = {
            sid: index for index, sid in enumerate(sorted(members[next(iter(members))]))
        }
        self._raw_by_id = prices.sort("security_id", "session")

    def _bars(
        self, t: datetime, ids: list[str] | tuple[str, ...], sessions_from: date
    ) -> pl.DataFrame:
        first = bisect_left(self._sessions, sessions_from)
        last = bisect_right(self._sessions, last_completed_session(t))
        bars = self.prices.slice(first * self._names, (last - first) * self._names)
        if len(ids) != self._names:
            bars = bars.filter(pl.col("security_id").is_in(ids))
        return bars.sort("security_id", "session")

    def adjusted_prices(
        self,
        t: datetime,
        ids: list[str] | tuple[str, ...],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        self._record(
            "adjusted_prices",
            t,
            ids=ids,
            include_dividends=include_dividends,
            sessions_from=sessions_from,
        )
        return self._bars(t, ids, sessions_from or self._sessions[0])

    def raw_prices(self, t: datetime, ids: list[str] | tuple[str, ...]) -> pl.DataFrame:
        self._record("raw_prices", t, ids=ids)
        through = bisect_right(self._sessions, last_completed_session(t))
        n_sessions = len(self._sessions)
        return (
            pl.concat(
                [
                    self._raw_by_id.slice(self._name_index[sid] * n_sessions, through)
                    for sid in sorted(ids)
                ]
            )
            if ids
            else self._raw_by_id.slice(0, 0)
        )

    def marking_changed_ids(
        self, previous_t: datetime, t: datetime, ids: list[str] | tuple[str, ...]
    ) -> tuple[str, ...]:
        return ()


def _scale_provider(
    names: int, start: date, end: date, history_start: date, *, reentry: bool = False
) -> FakeProvider:
    ids = [f"N{i:04d}" for i in range(names)]
    members = {
        s: [sid for sid in ids if sid != REENTRY_NAME]
        if reentry and REENTRY_GAP_START <= s < REENTRY_RETURN
        else ids
        for s in rebalance_sessions(start, end, "daily")
    }
    prices = scale_prices(names, history_start, end)
    return _ScaleProvider(prices, members)


def _handle() -> registry.TrialHandle:
    return registry._issue_handle(
        trial_id=1,
        hypothesis_id=1,
        family="momentum",
        params_sha256="0" * 64,
        kind="in_sample",
        synthetic=True,
        started_at=datetime(2026, 10, 18, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
    )


def _daily_params() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.1},
        schedule={"rebalance_cadence": "daily"},
        execution={"fill_price": "open"},
        costs={"per_side_bps": 15.0, "sensitivity_per_side_bps": [0.0, 30.0]},
    )


def test_the_generator_is_deterministic_and_shaped() -> None:
    one = scale_prices(5, date(2024, 1, 2), date(2024, 1, 12))
    two = scale_prices(5, date(2024, 1, 2), date(2024, 1, 12))
    assert one.equals(two)
    assert one.height == 5 * 9  # nine XNYS sessions
    assert not one.select(pl.struct("security_id", "session").is_duplicated().any()).item()
    assert one.select((pl.col("close") > 0).all() & (pl.col("open") > 0).all()).item()
    assert one["known_at"].dtype == pl.Datetime("us", "UTC")
    assert not one.equals(scale_prices(5, date(2024, 1, 2), date(2024, 1, 12), seed=1))


def test_a_small_daily_trial_runs_on_the_scale_fixture() -> None:
    """The scale path at 30 names and ten days: the harness itself stays green."""
    start, end = date(2024, 2, 1), date(2024, 2, 15)
    provider = _scale_provider(30, start, end, date(2022, 12, 1))
    result = run(_daily_params(), provider, start, end, _handle(), (15.0,), family="momentum")[15.0]
    ordinary = FakeProvider(prices=provider.prices, members=provider.members, benchmarks={})
    baseline = run(_daily_params(), ordinary, start, end, _handle(), (15.0,), family="momentum")[
        15.0
    ]
    assert len(result.rebalances) == len(rebalance_sessions(start, end, "daily")) - 1
    assert result.equity[-1].equity > 0
    assert result.equity == baseline.equity
    assert result.rebalances == baseline.rebalances
    assert result.weights == baseline.weights
    assert result.targets == baseline.targets
    assert result.position_values.equals(baseline.position_values)


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("RUN_SLOW_TESTS") != "1",
    reason="scale test (hours until T208); set RUN_SLOW_TESTS=1 to run",
)
def test_a_ten_year_thousand_name_daily_trial_fits_the_budget() -> None:
    """The break: 1,000 names, 10 years, `daily`. Prints the wall clock either way."""
    provider = _scale_provider(
        SCALE_NAMES, SCALE_RUN_START, SCALE_RUN_END, SCALE_HISTORY_START, reentry=True
    )
    started = time.perf_counter()
    result = run(
        _daily_params(),
        provider,
        SCALE_RUN_START,
        SCALE_RUN_END,
        _handle(),
        (15.0,),
        family="momentum",
        keep_marking_frames=False,
    )[15.0]
    elapsed = time.perf_counter() - started
    print(
        f"\nscale trial: {SCALE_NAMES} names, {len(result.rebalances)} daily rebalances, "
        f"{elapsed:.1f} s wall clock (budget {BACKTEST_SCALE_BUDGET_SECONDS} s)"
    )
    assert any(
        REENTRY_NAME in targets
        for session, targets in result.targets.items()
        if session < REENTRY_GAP_START
    )
    assert any(
        REENTRY_NAME in targets
        for session, targets in result.targets.items()
        if session >= REENTRY_RETURN
    )
    assert elapsed <= BACKTEST_SCALE_BUDGET_SECONDS

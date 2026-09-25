"""Engine exits and benchmarks on the fake provider (backtest spec reqs 3 and 5, T37c).

A and X are held at half weight each from F0. Each exit case is compared with a twin
run whose only difference is that X does not exit (no listing rows, and a stale rule
too long to fire): up to the exit session the two agree, and from it on the exiting run
is worse by exactly the exit's cost, with the proceeds in cash.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.provider import GapReading
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.calendar import all_sessions, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.store import delistings, registry

T0, T1, T2, T3 = date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28), date(2024, 4, 30)
F0, F1, F2 = fill_session(T0), fill_session(T1), fill_session(T2)
HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 5, 31)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
GROWTH = {"A": 0.002, "X": 0.001}
LEVEL = 15.0
#: Long enough that no stale exit fires in the window: the twin runs use it.
NO_STALE = 10_000
KNOWN = pl.Datetime("us", "UTC")


def _handle() -> registry.TrialHandle:
    return registry._issue_handle(
        trial_id=1,
        hypothesis_id=1,
        family="momentum",
        params_sha256="0" * 64,
        kind="in_sample",
        synthetic=True,
        started_at=datetime(2024, 6, 1, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
    )


def _params(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "strategy": {"top_fraction": 1.0},
        "costs": {"per_side_bps": LEVEL},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _no_stale(**overrides: Any) -> Settings:
    return _params(backtest={"stale_exit_sessions": NO_STALE}, **overrides)


def _bars(
    growth: Mapping[str, float],
    *,
    skip: Iterable[tuple[str, date]] = (),
    known_late: Mapping[tuple[str, date], date] | None = None,
) -> pl.DataFrame:
    skipped, late = set(skip), dict(known_late or {})
    rows = []
    for sid, rate in growth.items():
        close = 100.0
        for session in SESSIONS:
            previous = close
            close *= 1 + rate
            if (sid, session) in skipped:
                continue
            known = session_close(late.get((sid, session), session))
            rows.append((sid, session, (previous + close) / 2, close, known))
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": KNOWN,
        },
        orient="row",
    )


def _listing_rows(rows: Sequence[tuple[str, date, str, date | None, date]]) -> pl.DataFrame:
    """`(security_id, valid_from, status, end_session, known on)` listing rows."""
    return pl.DataFrame(
        [(sid, vf, status, end, session_close(known)) for sid, vf, status, end, known in rows],
        schema={
            "security_id": pl.Utf8,
            "valid_from": pl.Date,
            "status": pl.Utf8,
            "end_session": pl.Date,
            "known_at": KNOWN,
        },
        orient="row",
    )


def _provider(
    *,
    skip: Iterable[tuple[str, date]] = (),
    known_late: Mapping[tuple[str, date], date] | None = None,
    x_until: date = HISTORY_END,
    listing_rows: Sequence[tuple[str, date, str, date | None, date]] = (),
) -> FakeProvider:
    members = {
        session: ["A", "X"] if session <= x_until else ["A"]
        for session in rebalance_sessions(T0, HISTORY_END)
    }
    provider = FakeProvider(
        prices=_bars(GROWTH, skip=skip, known_late=known_late), members=members, benchmarks={}
    )
    if listing_rows:
        provider.listing_ends_rows = _listing_rows(listing_rows)
    return provider


def _run(
    provider: FakeProvider,
    params: Settings,
    *,
    end: date = T2,
    levels: Sequence[float] = (LEVEL,),
) -> dict[float, BacktestResult]:
    return run(params, provider, T0, end, _handle(), levels)


def _strategy(result: BacktestResult) -> dict[date, tuple[float, float | None]]:
    return {
        row.session: (row.equity, row.cash)
        for row in result.equity
        if row.series == engine.STRATEGY_SERIES
    }


def _row(result: BacktestResult, session: date) -> Any:
    (row,) = [row for row in result.rebalances if row.session == session]
    return row


def _value(result: BacktestResult, sid: str, session: date) -> float:
    values = result.position_values.filter(
        (pl.col("security_id") == sid) & (pl.col("session") == session)
    )
    return float(values["value"].sum())


def _steps(first: date, last: date) -> list[date]:
    return [s for s in SESSIONS if first <= s <= last]


def _assert_exit(
    exiting: BacktestResult, twin: BacktestResult, sid: str, exit_on: date, through: date
) -> float:
    """Up to `exit_on` the runs agree; from it through `through` the exiting run holds
    `sid`'s value at `exit_on` in cash less the exit cost. Returns that cost."""
    ours, theirs = _strategy(exiting), _strategy(twin)
    value = _value(twin, sid, exit_on)
    cost = value * LEVEL / 10_000
    assert value > 0
    for session in _steps(T0, previous_session(exit_on)):
        assert ours[session] == pytest.approx(theirs[session], rel=1e-12)
    for session in _steps(exit_on, through):
        equity, cash = ours[session]
        twin_equity, twin_cash = theirs[session]
        assert equity == pytest.approx(twin_equity - cost, rel=1e-12)
        assert cash is not None and twin_cash is not None
        assert cash == pytest.approx(twin_cash + value - cost, rel=1e-12)
        assert _value(exiting, sid, session) == 0.0
    return cost


class TestDelistingExit:
    END = date(2024, 2, 15)

    def _pair(self, known: date) -> tuple[BacktestResult, BacktestResult]:
        skip = [("X", s) for s in SESSIONS if s > self.END]
        listing = [("X", date(2010, 1, 4), delistings.DELISTED, self.END, known)]
        exiting = _provider(skip=skip, x_until=T0, listing_rows=listing)
        twin = _provider(skip=skip, x_until=T0)
        # Stale exits off in both, so X's trailing gap is not what ends it here.
        return _run(exiting, _no_stale())[LEVEL], _run(twin, _no_stale())[LEVEL]

    def test_exits_at_its_last_close_counted_and_charged(self) -> None:
        exiting, twin = self._pair(known=date(2024, 2, 20))
        cost = _assert_exit(exiting, twin, "X", self.END, T1)
        row, twin_row = _row(exiting, T0), _row(twin, T0)
        assert (row.n_delisting_exits, row.n_stale_exits) == (1, 0)
        assert row.cost_paid == pytest.approx(twin_row.cost_paid + cost, rel=1e-12)
        assert row.turnover > twin_row.turnover
        assert (_row(exiting, T1).n_delisting_exits, _row(exiting, T1).n_missing_fill) == (0, 0)
        # The twin still holds X frozen at T1 and misses its sale at F1.
        assert _row(twin, T1).n_missing_fill == 1

    def test_a_delisting_known_after_the_read_exits_at_the_next_fill(self) -> None:
        exiting, twin = self._pair(known=date(2024, 3, 5))
        assert _row(exiting, T0).n_delisting_exits == 0
        assert _row(exiting, T1).n_delisting_exits == 1
        assert _value(exiting, "X", T1) == pytest.approx(_value(twin, "X", self.END))
        # The F1 fill is the same in both runs (the exit is decided at the T2 read); the
        # frozen value turns into cash at F1, the first session of the step that read it.
        _assert_exit(exiting, twin, "X", F1, T2)

    def test_zero_cost_exit_leaves_equity_unchanged(self) -> None:
        skip = [("X", s) for s in SESSIONS if s > self.END]
        listing = [("X", date(2010, 1, 4), delistings.DELISTED, self.END, date(2024, 2, 20))]
        exiting = _run(
            _provider(skip=skip, x_until=T0, listing_rows=listing), _params(), levels=[0.0]
        )[0.0]
        twin = _run(_provider(skip=skip, x_until=T0), _no_stale(), levels=[0.0])[0.0]
        for session in _steps(T0, T1):
            assert _strategy(exiting)[session][0] == pytest.approx(_strategy(twin)[session][0])

    def test_status_constant_matches_the_store(self) -> None:
        assert engine._LISTED == delistings.LISTED

    def test_after_a_ticker_change_the_current_listing_decides(self) -> None:
        # The pre-change listing is never ended by the filing and stays `listed`.
        skip = [("X", s) for s in SESSIONS if s > self.END]
        listing = [
            ("X", date(2015, 1, 2), delistings.LISTED, None, date(2015, 1, 2)),
            ("X", date(2018, 6, 1), delistings.DELISTED, self.END, date(2024, 2, 20)),
        ]
        exiting = _run(_provider(skip=skip, x_until=T0, listing_rows=listing), _no_stale())
        twin = _run(_provider(skip=skip, x_until=T0), _no_stale())
        _assert_exit(exiting[LEVEL], twin[LEVEL], "X", self.END, T1)
        row = _row(exiting[LEVEL], T0)
        assert (row.n_delisting_exits, row.n_stale_exits) == (1, 0)

    def test_bars_after_the_final_session_do_not_delay_the_exit(self) -> None:
        # An OTC tail: the vendor keeps printing bars after the listing ended.
        listing = [("X", date(2010, 1, 4), delistings.DELISTED, self.END, date(2024, 2, 20))]
        exiting = _run(_provider(x_until=T0, listing_rows=listing), _params())[LEVEL]
        twin = _run(_provider(x_until=T0), _no_stale())[LEVEL]
        assert _row(exiting, T0).n_delisting_exits == 1
        # From the final session on, X is the cash its close there raised, not its tail.
        value = _value(twin, "X", self.END)
        proceeds = value * (1 - LEVEL / 10_000)
        ours, theirs = _strategy(exiting), _strategy(twin)
        for session in _steps(self.END, T1):
            twin_equity, twin_cash = theirs[session]
            assert twin_cash is not None
            assert ours[session][1] == pytest.approx(twin_cash + proceeds, rel=1e-12)
            assert ours[session][0] == pytest.approx(
                twin_equity - _value(twin, "X", session) + proceeds, rel=1e-12
            )
            assert _value(exiting, "X", session) == 0.0


class TestTransfer:
    OLD, NEW = date(2010, 1, 4), date(2024, 2, 16)

    def test_a_known_transfer_is_held_through(self) -> None:
        listing = [
            ("X", self.OLD, delistings.TRANSFERRED, date(2024, 2, 15), date(2024, 2, 20)),
            ("X", self.NEW, delistings.LISTED, None, date(2024, 2, 20)),
        ]
        moved = _run(_provider(listing_rows=listing), _params())[LEVEL]
        plain = _run(_provider(), _params())[LEVEL]
        assert _strategy(moved) == _strategy(plain)
        assert [r.n_delisting_exits for r in moved.rebalances] == [0, 0]

    def test_before_the_new_listing_is_known_it_exits_then_is_bought_again(self) -> None:
        # At the T1 read only the filing is known: delisted, its end the last bar (T1).
        listing = [
            ("X", self.OLD, delistings.DELISTED, T1, date(2024, 2, 20)),
            ("X", self.OLD, delistings.TRANSFERRED, date(2024, 2, 15), date(2024, 3, 5)),
            ("X", self.NEW, delistings.LISTED, None, date(2024, 3, 5)),
        ]
        moved = _run(_provider(listing_rows=listing), _params())[LEVEL]
        plain = _run(_provider(), _no_stale())[LEVEL]
        _assert_exit(moved, plain, "X", T1, T1)
        assert [r.n_delisting_exits for r in moved.rebalances] == [1, 0]
        bought = [w for w in moved.weights if w.fill_session == F1 and w.security_id == "X"]
        assert bought and bought[0].shares is not None and bought[0].shares > 0
        assert _value(moved, "X", T2) > 0


class TestStaleExit:
    @staticmethod
    def _gap(first: date, last: date) -> list[tuple[str, date]]:
        return [("X", s) for s in _steps(first, last)]

    def _pair(
        self,
        skip: Iterable[tuple[str, date]] = (),
        known_late: Mapping[tuple[str, date], date] | None = None,
    ) -> tuple[BacktestResult, BacktestResult]:
        skip = list(skip)
        stale = _run(_provider(skip=skip, known_late=known_late), _params())[LEVEL]
        twin = _run(_provider(skip=skip, known_late=known_late), _no_stale())[LEVEL]
        return stale, twin

    def test_a_trailing_gap_exits_at_the_last_close(self) -> None:
        stale, twin = self._pair(self._gap(date(2024, 2, 22), T1))
        _assert_exit(stale, twin, "X", date(2024, 2, 21), T1)
        assert (_row(stale, T0).n_stale_exits, _row(stale, T0).n_delisting_exits) == (1, 0)

    @pytest.mark.parametrize(("first", "exits"), [(date(2024, 2, 26), 0), (date(2024, 2, 23), 1)])
    def test_the_gap_must_reach_stale_exit_sessions(self, first: date, exits: int) -> None:
        stale, _ = self._pair(self._gap(first, T1))
        assert len(_steps(first, T1)) == 4 + exits
        assert _row(stale, T0).n_stale_exits == exits

    def test_a_gap_that_closes_before_the_rebalance_does_not_exit(self) -> None:
        stale, twin = self._pair(self._gap(date(2024, 2, 12), date(2024, 2, 21)))
        assert _strategy(stale) == _strategy(twin)
        assert [r.n_stale_exits for r in stale.rebalances] == [0, 0]

    def test_the_gap_is_judged_on_bars_known_at_the_read(self) -> None:
        late = {("X", s): date(2024, 3, 4) for s in _steps(date(2024, 2, 22), T1)}
        stale, twin = self._pair(known_late=late)
        assert _row(stale, T0).n_stale_exits == 1
        _assert_exit(stale, twin, "X", date(2024, 2, 21), T1)

    def test_a_delisted_name_is_not_also_a_stale_exit(self) -> None:
        end = date(2024, 2, 15)
        skip = [("X", s) for s in SESSIONS if s > end]
        listing = [("X", date(2010, 1, 4), delistings.DELISTED, end, date(2024, 2, 20))]
        result = _run(_provider(skip=skip, x_until=T0, listing_rows=listing), _params())[LEVEL]
        assert (_row(result, T0).n_delisting_exits, _row(result, T0).n_stale_exits) == (1, 0)


class TestBenchmarks:
    """SPY (S) pays $1 ex 2024-02-15, known 2024-02-20; MTUM (M) pays nothing."""

    EX, DIVIDEND, KNOWN_ON = date(2024, 2, 15), 1.0, date(2024, 2, 20)
    BENCHMARKS: ClassVar[dict[str, str]] = {"SPY": "S", "MTUM": "M"}

    def _raw(self) -> pl.DataFrame:
        return _bars({"A": 0.002, "S": 0.0005, "M": 0.001})

    def _provider(self) -> FakeProvider:
        raw = self._raw()
        prior = raw.filter((pl.col("security_id") == "S") & (pl.col("session") < self.EX))
        factor = 1 - self.DIVIDEND / prior["close"][-1]
        restated = prior.with_columns(
            pl.col("open") * factor,
            pl.col("close") * factor,
            pl.lit(session_close(self.KNOWN_ON)).cast(KNOWN).alias("known_at"),
        )
        adjusted = pl.concat([raw, restated])
        members = {s: ["A"] for s in rebalance_sessions(T0, HISTORY_END)}
        return FakeProvider(
            prices=adjusted,
            dividend_prices=adjusted,
            raw=raw,
            members=members,
            benchmarks=self.BENCHMARKS,
        )

    def _hand(self, sid: str, fill_price: str, level: float) -> dict[date, float]:
        """Buy-and-hold by share count on raw prices, the dividend reinvested at the
        close before its ex-date, one buy cost at F0."""
        raw = self._raw().filter(pl.col("security_id") == sid)
        bars = {row["session"]: row for row in raw.iter_rows(named=True)}
        capital = _params().backtest.initial_capital
        notional = capital / (1 + level / 10_000)
        leftover = capital - notional - notional * level / 10_000
        shares = notional / bars[F0][fill_price]
        equity = {T0: capital}
        for session in _steps(F0, T3):
            if sid == "S" and session == self.EX:
                shares /= 1 - self.DIVIDEND / bars[previous_session(session)]["close"]
            equity[session] = shares * bars[session]["close"] + leftover
        return equity

    @pytest.mark.parametrize("fill_price", ["close", "open"])
    def test_equal_hand_computed_total_return(self, fill_price: str) -> None:
        params = _params(execution={"fill_price": fill_price})
        results = run(params, self._provider(), T0, T3, _handle(), [0.0, LEVEL])
        for level, result in results.items():
            for series, sid in self.BENCHMARKS.items():
                rows = [row for row in result.equity if row.series == series]
                assert all(row.cash is None for row in rows)
                got = {row.session: row.equity for row in rows}
                hand = self._hand(sid, fill_price, level)
                assert set(got) == set(hand)
                for session, value in hand.items():
                    assert got[session] == pytest.approx(value, rel=1e-9), (series, session)

    def test_one_initial_cost_only(self) -> None:
        results = run(_params(), self._provider(), T0, T3, _handle(), [0.0, LEVEL])
        for series in self.BENCHMARKS:
            free = {r.session: r.equity for r in results[0.0].equity if r.series == series}
            paid = {r.session: r.equity for r in results[LEVEL].equity if r.series == series}
            ratios = {paid[s] / free[s] for s in free if s != T0}
            assert max(ratios) == pytest.approx(min(ratios), rel=1e-12)
            assert min(ratios) == pytest.approx(1 / (1 + LEVEL / 10_000), rel=1e-12)

    def test_benchmarks_are_read_once_and_marked_with_the_held_names(self) -> None:
        provider = self._provider()
        result = run(_params(), provider, T0, T3, _handle(), [LEVEL])[LEVEL]
        reads = [call for call in provider.calls if call.method == "benchmark_ids"]
        assert [call.t for call in reads] == [read_time(T0)]
        stitched = result.stitched_returns()
        assert {"S", "M"} <= set(stitched["security_id"].unique())

    def test_a_benchmark_without_a_fill_bar_is_refused(self) -> None:
        provider = self._provider()
        provider.dividend_prices = provider.prices = provider.prices.filter(
            ~((pl.col("security_id") == "M") & (pl.col("session") == F0))
        )
        with pytest.raises(ValueError, match="MTUM"):
            run(_params(), provider, T0, T3, _handle(), [LEVEL])

    def test_prefix_invariance_holds_with_benchmarks(self) -> None:
        short = run(_params(), self._provider(), T0, T2, _handle(), [LEVEL])[LEVEL]
        long = run(_params(), self._provider(), T0, T3, _handle(), [LEVEL])[LEVEL]
        assert short.equity == tuple(row for row in long.equity if row.session <= T2)


class TestRebalanceCounters:
    def test_static_listings_and_gap_reach_every_row(self) -> None:
        provider = _provider()
        provider.static_listings = {"X"}
        provider.gaps = {read_time(T1): GapReading(count_share=0.02, size_share=0.01)}
        result = _run(provider, _params())[LEVEL]
        assert [r.n_static_listings for r in result.rebalances] == [1, 1]
        assert [(r.gap_count_share, r.gap_size_share) for r in result.rebalances] == [
            (0.0, 0.0),
            (0.02, 0.01),
        ]

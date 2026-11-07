"""Engine exits and benchmarks on the fake provider (backtest spec reqs 3 and 5, T37c).

A and X are held at half weight each from F0. Each exit case is compared with a twin
run whose only difference is that X does not exit (no listing rows, and a stale rule
too long to fire): up to the exit session the two agree, and from it on the exiting run
is worse by exactly the exit's cost, with the proceeds in cash.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
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
    return run(params, provider, T0, end, _handle(), levels, family="momentum")


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

    def test_a_transfer_known_ahead_of_its_effective_date_is_held_through(self) -> None:
        # The successor listing is already known (filed/announced) before the T1 read,
        # but its own `valid_from` is still in the future, so the current row stays the
        # old exchange's: status `transferred`, not yet `listed` (issue #557, same bug
        # #555 fixed in planning; owner decision 2026-10-02: a transfer never ends a
        # listing, known ahead or not).
        far_future = date(2024, 6, 1)
        listing = [
            ("X", self.OLD, delistings.TRANSFERRED, date(2024, 2, 15), date(2024, 2, 20)),
            ("X", far_future, delistings.LISTED, None, date(2024, 2, 20)),
        ]
        moved = _run(_provider(listing_rows=listing), _no_stale())[LEVEL]
        plain = _run(_provider(), _no_stale())[LEVEL]
        assert _strategy(moved) == _strategy(plain)
        assert [r.n_delisting_exits for r in moved.rebalances] == [0, 0]

    def test_an_advance_transfer_known_only_after_the_decision_date_has_no_effect_at_that_date(
        self,
    ) -> None:
        # Filed and known only after the T0 step's read (close T1): at that read
        # nothing is known yet, so it must match plain exactly (no look-ahead). Once
        # known, before the T1 step's read (close T2), the transfer is held through
        # like any other, with no retroactive exit charged for the window it was
        # unknown.
        far_future = date(2024, 6, 1)
        known_on = date(2024, 3, 5)  # after close(T1), before close(T2)
        listing = [
            ("X", self.OLD, delistings.TRANSFERRED, date(2024, 2, 15), known_on),
            ("X", far_future, delistings.LISTED, None, known_on),
        ]
        moved = _run(_provider(listing_rows=listing), _no_stale())[LEVEL]
        plain = _run(_provider(), _no_stale())[LEVEL]
        assert _strategy(moved) == _strategy(plain)
        assert [r.n_delisting_exits for r in moved.rebalances] == [0, 0]


class TestEndedDirect:
    """Direct, unit-level coverage of `engine._ended` (mirrors #559's planning tests),
    independent of the full run harness above."""

    OLD, NEW = date(2010, 1, 4), date(2024, 2, 16)

    def test_a_transfer_known_ahead_of_its_effective_date_does_not_end_the_listing(self) -> None:
        far_future = date(2024, 6, 1)
        frame = _listing_rows(
            [
                ("X", self.OLD, delistings.TRANSFERRED, date(2024, 2, 15), date(2024, 2, 20)),
                ("X", far_future, delistings.LISTED, None, date(2024, 2, 20)),
            ]
        )
        # Sanity: `far_future` has not arrived at T1, so the current row is still the
        # old, `transferred` one -- otherwise this would not exercise the bug.
        assert engine._ended(frame, T1) == {}

    def test_a_genuine_delisting_with_no_successor_still_ends(self) -> None:
        end = date(2024, 2, 15)
        frame = _listing_rows([("X", self.OLD, delistings.DELISTED, end, date(2024, 2, 20))])
        assert engine._ended(frame, T1) == {"X": end}


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
        results = run(params, self._provider(), T0, T3, _handle(), [0.0, LEVEL], family="momentum")
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
        results = run(
            _params(), self._provider(), T0, T3, _handle(), [0.0, LEVEL], family="momentum"
        )
        for series in self.BENCHMARKS:
            free = {r.session: r.equity for r in results[0.0].equity if r.series == series}
            paid = {r.session: r.equity for r in results[LEVEL].equity if r.series == series}
            ratios = {paid[s] / free[s] for s in free if s != T0}
            assert max(ratios) == pytest.approx(min(ratios), rel=1e-12)
            assert min(ratios) == pytest.approx(1 / (1 + LEVEL / 10_000), rel=1e-12)

    def test_benchmarks_are_read_once_and_marked_with_the_held_names(self) -> None:
        provider = self._provider()
        result = run(_params(), provider, T0, T3, _handle(), [LEVEL], family="momentum")[LEVEL]
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
            run(_params(), provider, T0, T3, _handle(), [LEVEL], family="momentum")

    def test_prefix_invariance_holds_with_benchmarks(self) -> None:
        short = run(_params(), self._provider(), T0, T2, _handle(), [LEVEL], family="momentum")[
            LEVEL
        ]
        long = run(_params(), self._provider(), T0, T3, _handle(), [LEVEL], family="momentum")[
            LEVEL
        ]
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


# --- the delisting failure-return policy (spec amendment #2670, req 2; plan T328c) ------

#: X's Form 25 episode: the filing that ends its listing, on `EXCHANGE` by default.
FILED = datetime(2024, 2, 16, 21, 0, tzinfo=UTC)
FORM = "25-NSE"
POLICY_COUNTS = set(engine.POLICY_COUNT_NAMES)


class TestDelistingPolicy:
    """X delists on `END` (known before the T1 read); the run's data cutoff is
    close(T2). Each policy run is compared with the same run at `none`: up to `END`
    they agree, and from it on the policy run's cash differs by exactly the change in
    the sale value, net of the per-side cost on it."""

    END = date(2024, 2, 15)
    CUTOFF = read_time(T2, "month_end")
    DECIDING = read_time(T1, "month_end")

    def _provider(
        self,
        *,
        exchange: str = "NYSE",
        reasons: Sequence[tuple[str, datetime]] = (),
        mergers: Sequence[dict[str, Any]] = (),
        status: str = delistings.DELISTED,
    ) -> FakeProvider:
        skip = [("X", s) for s in SESSIONS if s > self.END]
        provider = _provider(skip=skip, x_until=T0)
        provider.listing_ends_rows = pl.DataFrame(
            [
                {
                    "security_id": "X",
                    "valid_from": date(2010, 1, 4),
                    "exchange": exchange,
                    "status": status,
                    "end_session": self.END,
                    "delisting_form": FORM,
                    "delisting_filed_at": FILED,
                    "known_at": session_close(date(2024, 2, 20)),
                }
            ],
            schema={
                "security_id": pl.Utf8,
                "valid_from": pl.Date,
                "exchange": pl.Utf8,
                "status": pl.Utf8,
                "end_session": pl.Date,
                "delisting_form": pl.Utf8,
                "delisting_filed_at": KNOWN,
                "known_at": KNOWN,
            },
        )
        provider.delisting_reason_rows = pl.DataFrame(
            [
                {
                    "security_id": "X",
                    "delisting_form": FORM,
                    "delisting_class_title": f"Class {i}",
                    "delisting_exchange": exchange,
                    "delisting_filed_at": FILED,
                    "reason": reason,
                    "reason_source": "eightk_item",
                    "known_at": known_at,
                }
                for i, (reason, known_at) in enumerate(reasons)
            ],
            schema=provider.delisting_reason_rows.schema,
        )
        provider.merger_exit_rows = pl.DataFrame(
            [
                {
                    "security_id": "X",
                    "source_action_id": f"m{i}",
                    "kind": "cash",
                    "cash_per_share": None,
                    "share_ratio": None,
                    "effective_date": self.END,
                    "acquirer_security_id": None,
                    "acquirer_raw_close": None,
                    "known_at": session_close(date(2024, 2, 20)),
                    **row,
                }
                for i, row in enumerate(mergers)
            ],
            schema=provider.merger_exit_rows.schema,
        )
        return provider

    def _run(self, provider: FakeProvider, policy: str) -> BacktestResult:
        params = _params(
            backtest={"stale_exit_sessions": NO_STALE, "delisting_failure_return": policy}
        )
        return _run(provider, params)[LEVEL]

    def _pair(self, policy: str, **kwargs: Any) -> tuple[BacktestResult, BacktestResult]:
        return self._run(self._provider(**kwargs), policy), self._run(
            self._provider(**kwargs), "none"
        )

    def _sold_for(self, ours: BacktestResult, base: BacktestResult) -> float:
        """X's sale value in `ours`, from the cash difference to the `none` run (whose
        sale is X's marked value at `END`), the per-side cost backed out; the two runs
        must agree before `END` and differ by one constant from it through T1 (the next
        fill reinvests the cash)."""
        mine, theirs = _strategy(ours), _strategy(base)
        for session in _steps(T0, previous_session(self.END)):
            assert mine[session] == pytest.approx(theirs[session], rel=1e-12)
        gaps = {
            session: cast_cash(mine[session]) - cast_cash(theirs[session])
            for session in _steps(self.END, T1)
        }
        first = gaps[self.END]
        assert all(gap == pytest.approx(first, abs=1e-6) for gap in gaps.values())
        return self._marked(base) + first / (1 - LEVEL / 10_000)

    def _marked(self, base: BacktestResult) -> float:
        """X's marked value at `END`: the `none` run's sale value, read from the twin
        that never exits it (the exiting runs zero it from `END` on)."""
        skip = [("X", s) for s in SESSIONS if s > self.END]
        twin = _run(_provider(skip=skip, x_until=T0), _no_stale())[LEVEL]
        marked = _value(twin, "X", self.END)
        assert marked > 0
        return marked

    def _x_close(self) -> float:
        bars = _bars(GROWTH).filter(
            (pl.col("security_id") == "X") & (pl.col("session") == self.END)
        )
        return float(bars["close"].item())

    @pytest.mark.parametrize(
        ("policy", "exchange", "reason", "shock"),
        [
            ("failure_v1", "NYSE", "performance_failure", -0.30),
            ("failure_v1", "NYSE_AMERICAN", "bankruptcy", -0.30),
            ("failure_v1", "NASDAQ", "performance_failure", -0.55),
            ("severe_v1", "NYSE", "bankruptcy", -0.55),
            ("severe_v1", "NYSE_AMERICAN", "performance_failure", -0.55),
            ("severe_v1", "NASDAQ", "bankruptcy", -1.00),
        ],
    )
    def test_a_confirmed_failure_takes_its_exchange_shock(
        self, policy: str, exchange: str, reason: str, shock: float
    ) -> None:
        ours, base = self._pair(
            policy, exchange=exchange, reasons=[(reason, session_close(date(2024, 2, 20)))]
        )
        assert self._sold_for(ours, base) == pytest.approx(
            self._marked(base) * (1 + shock), rel=1e-9
        )
        counts = _row(ours, T0).counts
        assert {k: counts[k] for k in POLICY_COUNTS} == {
            **dict.fromkeys(POLICY_COUNTS, 0),
            "n_failure_shocks": 1,
        }
        assert _row(ours, T0).n_delisting_exits == 1
        # The ordinary cost is on the shocked value: cost_paid falls by the shock's share.
        assert _row(ours, T0).cost_paid == pytest.approx(
            _row(base, T0).cost_paid + self._marked(base) * shock * LEVEL / 10_000, rel=1e-9
        )

    @pytest.mark.parametrize(
        ("policy", "exchange", "shock", "unknown"),
        [
            ("failure_v1", "NASDAQ", 0.0, 1),
            ("severe_v1", "NYSE", -0.55, 1),
            ("severe_v1", "NASDAQ", -1.00, 1),
        ],
    )
    def test_an_unknown_exit_is_shocked_only_under_severe(
        self, policy: str, exchange: str, shock: float, unknown: int
    ) -> None:
        ours, base = self._pair(policy, exchange=exchange)
        assert self._sold_for(ours, base) == pytest.approx(
            self._marked(base) * (1 + shock), rel=1e-9, abs=1e-9
        )
        counts = _row(ours, T0).counts
        assert counts["n_unknown_exits"] == unknown
        assert counts["n_failure_shocks"] == (shock != 0.0)

    @pytest.mark.parametrize("policy", ["failure_v1", "severe_v1"])
    def test_a_voluntary_exit_is_never_shocked(self, policy: str) -> None:
        ours, base = self._pair(
            policy,
            exchange="NASDAQ",
            reasons=[("voluntary_other", session_close(date(2024, 2, 20)))],
        )
        assert self._sold_for(ours, base) == pytest.approx(self._marked(base), rel=1e-9)
        assert _row(ours, T0).counts["n_failure_shocks"] == 0

    def test_an_unmapped_exchange_fails_the_trial_naming_it(self) -> None:
        provider = self._provider(
            exchange="NYSE_ARCA", reasons=[("bankruptcy", session_close(date(2024, 2, 20)))]
        )
        with pytest.raises(ValueError, match=r"X: listing exchange 'NYSE_ARCA' is outside"):
            self._run(provider, "failure_v1")

    def test_an_unknown_unmapped_exit_under_severe_fails_too(self) -> None:
        with pytest.raises(ValueError, match=r"'NYSE_ARCA' is outside"):
            self._run(self._provider(exchange="NYSE_ARCA"), "severe_v1")

    def test_merger_consideration_reads_the_raw_acquirer_close(self) -> None:
        # The acquirer A is held and marked in the frame at another (adjusted) price; the
        # consideration must use the row's raw close (80), not A's frame close.
        merger = {
            "kind": "stock_and_cash",
            "cash_per_share": 10.0,
            "share_ratio": 0.5,
            "acquirer_security_id": "A",
            "acquirer_raw_close": 80.0,
        }
        reasons = [("merger_acquisition", session_close(date(2024, 2, 20)))]
        ours, base = self._pair("severe_v1", exchange="NASDAQ", reasons=reasons, mergers=[merger])
        shares = self._marked(base) / self._x_close()
        assert self._sold_for(ours, base) == pytest.approx(shares * (10.0 + 0.5 * 80.0), rel=1e-9)
        a_close = float(
            _bars(GROWTH)
            .filter((pl.col("security_id") == "A") & (pl.col("session") == self.END))["close"]
            .item()
        )
        assert a_close != pytest.approx(80.0)
        assert self._sold_for(ours, base) != pytest.approx(shares * (10.0 + 0.5 * a_close))
        counts = _row(ours, T0).counts
        assert (counts["n_merger_consideration"], counts["n_failure_shocks"]) == (1, 0)

    @pytest.mark.parametrize(
        ("kind", "cash", "ratio", "value"),
        [("cash", 120.0, None, 120.0), ("stock", None, 2.0, 2.0 * 55.0)],
    )
    def test_a_one_component_deal_uses_its_component(
        self, kind: str, cash: float | None, ratio: float | None, value: float
    ) -> None:
        merger = {
            "kind": kind,
            "cash_per_share": cash,
            "share_ratio": ratio,
            "acquirer_security_id": None if ratio is None else "A",
            "acquirer_raw_close": None if ratio is None else 55.0,
        }
        reasons = [("merger_acquisition", session_close(date(2024, 2, 20)))]
        ours, base = self._pair("failure_v1", reasons=reasons, mergers=[merger])
        shares = self._marked(base) / self._x_close()
        assert self._sold_for(ours, base) == pytest.approx(shares * value, rel=1e-9)

    @pytest.mark.parametrize(
        ("mergers", "fallbacks", "conflicts"),
        [
            ([], 1, 0),
            (
                [
                    {"cash_per_share": 120.0},
                    {"cash_per_share": 130.0, "effective_date": date(2024, 2, 20)},
                ],
                1,
                1,
            ),
            (
                [
                    {
                        "kind": "stock",
                        "share_ratio": 2.0,
                        "acquirer_security_id": "A",
                        "acquirer_raw_close": None,
                    }
                ],
                1,
                0,
            ),
            # Outside the window: before `end_session`, and 11 sessions after it.
            ([{"cash_per_share": 120.0, "effective_date": date(2024, 2, 14)}], 1, 0),
            ([{"cash_per_share": 120.0, "effective_date": date(2024, 3, 4)}], 1, 0),
        ],
    )
    def test_a_merger_without_one_usable_row_falls_back_to_the_last_close_counted(
        self, mergers: list[dict[str, Any]], fallbacks: int, conflicts: int
    ) -> None:
        reasons = [("merger_acquisition", session_close(date(2024, 2, 20)))]
        ours, base = self._pair("severe_v1", exchange="NASDAQ", reasons=reasons, mergers=mergers)
        assert self._sold_for(ours, base) == pytest.approx(self._marked(base), rel=1e-9)
        counts = _row(ours, T0).counts
        assert (counts["n_merger_fallbacks"], counts["n_merger_conflicts"]) == (
            fallbacks,
            conflicts,
        )
        assert (counts["n_merger_consideration"], counts["n_failure_shocks"]) == (0, 0)

    def test_the_window_end_is_ten_trading_sessions_after_end_session(self) -> None:
        # 2024-02-15 + 10 sessions (Presidents' Day 02-19 closed) is 2024-03-01.
        assert engine._window_end(self.END, 10) == date(2024, 3, 1)
        merger = {"cash_per_share": 120.0, "effective_date": date(2024, 3, 1)}
        reasons = [("merger_acquisition", session_close(date(2024, 2, 20)))]
        ours, base = self._pair("failure_v1", reasons=reasons, mergers=[merger])
        shares = self._marked(base) / self._x_close()
        assert self._sold_for(ours, base) == pytest.approx(shares * 120.0, rel=1e-9)

    def test_two_matching_reasons_are_unknown_and_counted(self) -> None:
        known = session_close(date(2024, 2, 20))
        reasons = [("bankruptcy", known), ("merger_acquisition", known)]
        ours, base = self._pair("failure_v1", exchange="NASDAQ", reasons=reasons)
        assert self._sold_for(ours, base) == pytest.approx(self._marked(base), rel=1e-9)
        counts = _row(ours, T0).counts
        assert (counts["n_reason_conflicts"], counts["n_unknown_exits"]) == (1, 1)

    def test_a_reason_for_another_episode_does_not_match(self) -> None:
        provider = self._provider(
            exchange="NASDAQ", reasons=[("bankruptcy", session_close(date(2024, 2, 20)))]
        )
        provider.delisting_reason_rows = provider.delisting_reason_rows.with_columns(
            pl.lit("NYSE").alias("delisting_exchange")
        )
        ours = self._run(provider, "failure_v1")
        assert _row(ours, T0).counts["n_unknown_exits"] == 1
        assert _row(ours, T0).counts["n_failure_shocks"] == 0

    def test_evidence_known_after_the_cutoff_leaves_the_last_close(self) -> None:
        late = self.CUTOFF + timedelta(microseconds=1)
        merger = {"cash_per_share": 120.0, "known_at": late}
        for reasons, mergers in (
            ([("bankruptcy", late)], []),
            ([("merger_acquisition", session_close(date(2024, 2, 20)))], [merger]),
        ):
            ours, base = self._pair(
                "failure_v1", exchange="NASDAQ", reasons=reasons, mergers=mergers
            )
            assert self._sold_for(ours, base) == pytest.approx(self._marked(base), rel=1e-9)
            assert _row(ours, T0).counts["n_failure_shocks"] == 0
            assert _row(ours, T0).counts["n_merger_consideration"] == 0

    def test_evidence_after_the_deciding_read_but_by_the_cutoff_applies(self) -> None:
        # Owner 2026-10-31: the data cutoff alone decides; the deciding read (close T1)
        # only decides that X exits.
        at_cutoff = self.CUTOFF
        assert at_cutoff > self.DECIDING
        ours, base = self._pair(
            "failure_v1", exchange="NASDAQ", reasons=[("bankruptcy", at_cutoff)]
        )
        assert self._sold_for(ours, base) == pytest.approx(self._marked(base) * 0.45, rel=1e-9)
        merger = {"cash_per_share": 120.0, "known_at": at_cutoff}
        reasons = [("merger_acquisition", at_cutoff)]
        ours, base = self._pair("failure_v1", reasons=reasons, mergers=[merger])
        shares = self._marked(base) / self._x_close()
        assert self._sold_for(ours, base) == pytest.approx(shares * 120.0, rel=1e-9)

    def test_the_evidence_is_read_once_at_the_cutoff_for_the_ended_names(self) -> None:
        provider = self._provider(reasons=[("bankruptcy", session_close(date(2024, 2, 20)))])
        self._run(provider, "severe_v1")
        calls = [c for c in provider.calls if c.method in {"delisting_reasons", "merger_exits"}]
        assert [(c.method, c.t, c.ids) for c in calls] == [
            ("delisting_reasons", self.CUTOFF, ("X",)),
            ("merger_exits", self.CUTOFF, ("X",)),
        ]

    def test_a_short_delisting_exit_is_refused(self) -> None:
        context = engine._ExitContext(
            read_time=self.DECIDING,
            listings={"X": {"exchange": "NYSE"}},
            policy="failure_v1",
            terms=engine.DELISTING_POLICIES["failure_v1"],
        )
        counts = dict.fromkeys(engine.POLICY_COUNT_NAMES, 0)
        with pytest.raises(ValueError, match="refused_delisting_short"):
            engine._policy_value("X", -100.0, 1.0, self.END, context, counts)

    def test_a_transfer_is_never_shocked(self) -> None:
        provider = self._provider(
            exchange="NASDAQ",
            status=delistings.TRANSFERRED,
            reasons=[("bankruptcy", session_close(date(2024, 2, 20)))],
        )
        # Kept trading on its new exchange: no trailing gap.
        provider.prices = _bars(GROWTH)
        ours = self._run(provider, "severe_v1")
        assert [r.n_delisting_exits for r in ours.rebalances] == [0, 0]
        assert all(r.counts["n_failure_shocks"] == 0 for r in ours.rebalances)
        assert ours.delisting_exits == ()
        assert "delisting_reasons" not in {c.method for c in provider.calls}

    def test_none_makes_no_evidence_read_and_ignores_the_evidence(self) -> None:
        known = session_close(date(2024, 2, 20))
        merger = {"cash_per_share": 120.0}
        with_rows = self._provider(
            exchange="NASDAQ", reasons=[("bankruptcy", known)], mergers=[merger]
        )
        plain = self._provider(exchange="NASDAQ")
        ours, theirs = self._run(with_rows, "none"), self._run(plain, "none")
        methods = {c.method for c in with_rows.calls}
        assert not methods & {"delisting_reasons", "merger_exits"}
        assert _strategy(ours) == _strategy(theirs)
        assert ours.rebalances == theirs.rebalances
        assert not POLICY_COUNTS & set(_row(ours, T0).counts)

    @pytest.mark.parametrize("policy", ["none", "failure_v1"])
    def test_every_policy_records_the_delisting_exit(self, policy: str) -> None:
        ours = self._run(self._provider(exchange="NASDAQ"), policy)
        assert ours.delisting_exits == (
            engine.DelistingExit(
                security_id="X",
                read_time=self.DECIDING,
                end_session=self.END,
                exchange="NASDAQ",
                delisting_form=FORM,
                delisting_filed_at=FILED,
            ),
        )


def cast_cash(point: tuple[float, float | None]) -> float:
    cash = point[1]
    assert cash is not None
    return cash

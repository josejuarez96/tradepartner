"""Engine loop, fills, costs and cash on the fake provider (backtest spec reqs 1-6, T37b)."""

from __future__ import annotations

import ast
import dataclasses
import itertools
import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures
from polars.testing import assert_frame_equal

from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.costs import Commissions, trade_cost
from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult, run
from tradepartner.backtest.fills import apply_trades
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.signals import momentum_12_1
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import all_sessions, last_completed_session, session_close
from tradepartner.config import Cadence, HypothesisFamily, Settings, SignalAnchor
from tradepartner.store import registry, schema
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row

SRC = Path(__file__).parents[2] / "src" / "tradepartner" / "backtest"

T0, T1, T2, T3 = date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28), date(2024, 4, 30)
T4 = date(2024, 5, 31)
F0, F1, F2 = fill_session(T0), fill_session(T1), fill_session(T2)
HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 5, 31)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]

#: Per-session growth. C triples on 2024-01-10: excluded from the signal at T0 (inside
#: the skip month), so the targets are A and B at F0; from T1 on C leads and B is sold.
GROWTH = {"A": 0.004, "B": 0.003, "C": 0.001, "D": -0.001}
C_JUMP = date(2024, 1, 10)


def _handle(trial_id: int = 1, *, family: HypothesisFamily = "momentum") -> registry.TrialHandle:
    return registry._issue_handle(
        trial_id=trial_id,
        hypothesis_id=1,
        family=family,
        params_sha256="0" * 64,
        kind="in_sample",
        synthetic=True,
        started_at=datetime(2024, 6, 1, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
    )


def _params(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "strategy": {"top_fraction": 0.5},
        "costs": {"per_side_bps": 15.0, "sensitivity_per_side_bps": [0.0, 30.0, 100.0]},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _price_rows(skip: Iterable[tuple[str, date]] = ()) -> pl.DataFrame:
    skipped = set(skip)
    rows = []
    for sid, growth in GROWTH.items():
        close = 100.0
        for session in SESSIONS:
            previous = close
            close *= 1 + growth
            if sid == "C" and session == C_JUMP:
                close *= 3
            if (sid, session) in skipped:
                continue
            # Open between the previous close and today's, so open and close fills differ.
            rows.append((sid, session, (previous + close) / 2, close, session_close(session)))
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


def _provider(skip: Iterable[tuple[str, date]] = ()) -> FakeProvider:
    members = {session: sorted(GROWTH) for session in rebalance_sessions(T0, HISTORY_END)}
    return FakeProvider(prices=_price_rows(skip), members=members, benchmarks={})


def _run(
    provider: FakeProvider,
    params: Settings | None = None,
    *,
    end: date = T3,
    levels: Sequence[float] = (15.0,),
) -> dict[float, BacktestResult]:
    return run(params or _params(), provider, T0, end, _handle(), levels, family="momentum")


def _equity(result: BacktestResult) -> dict[date, float]:
    return {row.session: row.equity for row in result.equity}


def _strategy_equity(result: BacktestResult) -> dict[date, float]:
    """The strategy series' equity by session: a `StoreProvider` run also carries the store's
    benchmark series, whose rows share every session and would overwrite it in `_equity`."""
    return {row.session: row.equity for row in result.equity if row.series == STRATEGY_SERIES}


def _rows(result: BacktestResult, session: date) -> Any:
    (row,) = [row for row in result.rebalances if row.session == session]
    return row


class TestReads:
    def test_only_rebalance_closes_are_read(self) -> None:
        provider = _provider()
        _run(provider)
        allowed = {read_time(t) for t in rebalance_sessions(T0, T3)}
        assert provider.read_times() <= allowed
        assert max(provider.read_times()) == read_time(T3)

    def test_one_read_set_serves_every_cost_level(self) -> None:
        one, many = _provider(), _provider()
        _run(one, levels=[15.0])
        results = _run(many, levels=[0.0, 15.0, 30.0, 100.0])
        assert many.calls == one.calls
        assert sorted(results) == [0.0, 15.0, 30.0, 100.0]

    def test_marking_reads_include_dividends(self) -> None:
        provider = _provider()
        _run(provider)
        marking = [
            call
            for call in provider.calls
            if call.method == "adjusted_prices" and call.include_dividends
        ]
        # The signal frame also includes dividends by default; one marking read per step.
        steps = len(rebalance_sessions(T0, T3)) - 1
        assert len(marking) >= steps


def _rate_frame(series: str, value: float = 5.0) -> pl.DataFrame:
    rows = [(series, s, value, s, session_close(s)) for s in SESSIONS]
    return pl.DataFrame(
        rows,
        schema={
            "series": pl.Utf8,
            "observation_date": pl.Date,
            "value": pl.Float64,
            "vintage_date": pl.Date,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


class TestRiskFreeRead:
    """The engine's one risk-free read (backtest spec amendment 2026-10-11, T170c)."""

    def test_constant_never_calls_rates(self) -> None:
        provider = _provider()
        results = _run(provider, _params(metrics={"risk_free_rate": 0.02}), levels=(0.0, 15.0))
        assert [c for c in provider.calls if c.method == "rates"] == []
        assert all(result.rates is None for result in results.values())

    def test_store_reads_once_at_the_cutoff_and_every_level_shares_the_frame(self) -> None:
        provider = dataclasses.replace(_provider(), rate_rows=_rate_frame("DTB3"))
        params = _params(metrics={"risk_free_source": "store"})
        results = _run(provider, params, levels=(0.0, 15.0, 30.0))
        reads = [c for c in provider.calls if c.method == "rates"]
        assert [(c.t, c.series) for c in reads] == [(read_time(T3), "DTB3")]
        frames = [result.rates for result in results.values()]
        assert all(frame is not None and frame.equals(frames[0]) for frame in frames)
        assert frames[0] is not None and frames[0]["known_at"].max() <= read_time(T3)

    def test_the_series_key_is_the_one_read(self) -> None:
        frame = pl.concat([_rate_frame("DTB3", 5.0), _rate_frame("OTHER", 1.0)])
        provider = dataclasses.replace(_provider(), rate_rows=frame)
        params = _params(metrics={"risk_free_source": "store", "risk_free_series": "OTHER"})
        (result,) = _run(provider, params).values()
        assert result.rates is not None
        assert set(result.rates["series"]) == {"OTHER"}
        assert set(result.rates["value"]) == {1.0}

    def test_a_missing_series_is_an_empty_frame_with_the_columns(self) -> None:
        provider = _provider()
        params = _params(metrics={"risk_free_source": "store"})
        (result,) = _run(provider, params).values()
        assert result.rates is not None and result.rates.is_empty()
        assert result.rates.columns == [
            "series",
            "observation_date",
            "value",
            "vintage_date",
            "known_at",
        ]

    def test_the_declaring_pass_reads_nothing_and_returns_the_columns(self) -> None:
        requests = engine._Requests(engine._SharedReads(_provider(), covering=True))
        frame = requests.rates(read_time(T0), "DTB3")
        assert frame.is_empty() and frame.columns == list(engine._RATES_SCHEMA)
        assert requests.asked == {}

    def test_group_variants_each_read_their_own_series_once(self) -> None:
        frame = pl.concat([_rate_frame("DTB3", 5.0), _rate_frame("OTHER", 1.0)])
        provider = dataclasses.replace(_provider(), rate_rows=frame)
        variants = [
            (_params(metrics={"risk_free_source": "store"}), _handle()),
            (_params(), _handle(2)),
            (
                _params(metrics={"risk_free_source": "store", "risk_free_series": "OTHER"}),
                _handle(3),
            ),
        ]
        out = engine.run_many(variants, provider, T0, T3, (15.0,), family="momentum")
        reads = sorted(c.series for c in provider.calls if c.method == "rates")
        assert reads == ["DTB3", "OTHER"]
        assert out[1][15.0].rates is not None and set(out[1][15.0].rates["series"]) == {"DTB3"}
        assert out[2][15.0].rates is None
        assert out[3][15.0].rates is not None and set(out[3][15.0].rates["series"]) == {"OTHER"}


class TestHandle:
    @pytest.mark.parametrize("handle", [None, 1, "1"])
    def test_no_handle_raises_before_any_call(self, handle: object) -> None:
        provider = _provider()
        with pytest.raises(TypeError, match="TrialHandle"):
            run(_params(), provider, T0, T3, handle, [15.0], family="momentum")  # type: ignore[arg-type]
        assert provider.calls == []

    def test_a_window_with_one_rebalance_is_refused_before_any_call(self) -> None:
        provider = _provider()
        with pytest.raises(ValueError, match="two rebalance sessions"):
            run(_params(), provider, T0, date(2024, 2, 28), _handle(), [15.0], family="momentum")
        assert provider.calls == []

    def test_a_benchmark_in_the_universe_is_refused(self) -> None:
        """#840: a benchmark read by symbol is a reference series, never a member."""
        members = {session: sorted(GROWTH) for session in rebalance_sessions(T0, HISTORY_END)}
        provider = FakeProvider(prices=_price_rows(), members=members, benchmarks={"SPY": "A"})
        with pytest.raises(ValueError, match="benchmark SPY is a universe member"):
            _run(provider)

    def test_the_benchmarks_are_read_over_the_run_window(self) -> None:
        seen: list[date | None] = []
        provider = _provider()
        original = provider.benchmark_ids

        def spy(t: datetime, through: date | None = None) -> Mapping[str, str]:
            seen.append(through)
            return original(t, through)

        provider.benchmark_ids = spy  # type: ignore[method-assign]
        _run(provider)
        assert seen == [T3]

    def test_non_zero_cash_rate_is_refused(self) -> None:
        provider = _provider()
        with pytest.raises(ValueError, match="cash_rate"):
            _run(provider, _params(backtest={"cash_rate": 0.01}))
        assert provider.calls == []

    @pytest.mark.parametrize("levels", [[], [-1.0], [math.nan]])
    def test_bad_cost_levels_are_refused(self, levels: list[float]) -> None:
        with pytest.raises(ValueError, match="cost level"):
            _run(_provider(), levels=levels)


class TestTargetsAndFills:
    def test_targets_follow_the_signal(self) -> None:
        result = _run(_provider())[15.0]
        assert set(result.targets[F0]) == {"A", "B"}
        assert set(result.targets[F1]) == {"A", "C"}
        assert set(result.targets[F2]) == {"A", "C"}
        assert all(math.isclose(w, 0.5) for w in result.targets[F1].values())

    def test_close_and_open_fills_share_targets_and_differ_in_price(self) -> None:
        closes = _run(_provider(), _params(execution={"fill_price": "close"}))[15.0]
        opens = _run(_provider(), _params(execution={"fill_price": "open"}))[15.0]
        assert closes.targets == opens.targets
        by_key = {(w.fill_session, w.security_id): w for w in closes.weights}
        for weight in opens.weights:
            twin = by_key[(weight.fill_session, weight.security_id)]
            assert weight.target_weight == twin.target_weight
            assert weight.fill_price is not None and twin.fill_price is not None
            assert weight.fill_price < twin.fill_price  # rising prices: open below close
        prices = _price_rows().filter(pl.col("session") == F0)
        close_a = prices.filter(pl.col("security_id") == "A")["close"].item()
        open_a = prices.filter(pl.col("security_id") == "A")["open"].item()
        assert by_key[(F0, "A")].fill_price == pytest.approx(close_a)
        assert {(w.fill_session, w.security_id): w for w in opens.weights}[
            (F0, "A")
        ].fill_price == pytest.approx(open_a)

    @pytest.mark.parametrize("fill_price", ["close", "open"])
    def test_weights_report_shares_at_the_raw_fill_price(self, fill_price: str) -> None:
        """Shares are the dollar value at the fill over the raw fill price (req 2)."""
        raw = _price_rows().with_columns(pl.col("open") * 2, pl.col("close") * 2)
        provider = FakeProvider(
            prices=_price_rows(),
            raw=raw,
            members={s: sorted(GROWTH) for s in rebalance_sessions(T0, HISTORY_END)},
            benchmarks={},
        )
        result = _run(provider, _params(execution={"fill_price": fill_price}))[15.0]
        weight = {(w.fill_session, w.security_id): w for w in result.weights}[(F0, "A")]
        bar = _price_rows().filter((pl.col("security_id") == "A") & (pl.col("session") == F0))
        value_at_close = result.position_values.filter(
            (pl.col("security_id") == "A") & (pl.col("session") == F0)
        )["value"].item()
        value_at_fill = value_at_close * bar[fill_price].item() / bar["close"].item()
        assert weight.fill_price == pytest.approx(2 * bar[fill_price].item())
        assert weight.shares == pytest.approx(value_at_fill / weight.fill_price)

    def test_missing_fills_for_a_buy_and_a_sell(self) -> None:
        # At F1 the plan buys C and sells B; neither has a bar that session.
        provider = _provider(skip=[("C", F1), ("B", F1)])
        result = _run(provider)[15.0]
        assert _rows(result, T1).n_missing_fill == 2
        assert _rows(result, T0).n_missing_fill == 0
        values = result.position_values
        c_at_f1 = values.filter((pl.col("security_id") == "C") & (pl.col("session") == F1))
        assert c_at_f1.is_empty()  # not bought
        b = dict(values.filter(pl.col("security_id") == "B").select("session", "value").iter_rows())
        # B keeps its last mark through F1, then moves with its bars until the next fill.
        assert b[F1] == pytest.approx(b[T1])
        next_session = all_sessions()[all_sessions().index(F1) + 1]
        assert b[next_session] == pytest.approx(b[T1] * (1 + GROWTH["B"]) ** 2)
        weight = {(w.fill_session, w.security_id): w for w in result.weights}[(F1, "C")]
        assert weight.fill_price is None and weight.shares is None
        assert "B" not in {
            sid for (session, sid) in values.select("session", "security_id").rows() if session > F2
        }

    def test_a_held_name_without_a_bar_keeps_its_last_mark(self) -> None:
        gap_day = date(2024, 2, 14)
        result = _run(_provider(skip=[("A", gap_day)]))[15.0]
        a = dict(
            result.position_values.filter(pl.col("security_id") == "A")
            .select("session", "value")
            .iter_rows()
        )
        previous = all_sessions()[all_sessions().index(gap_day) - 1]
        assert a[gap_day] == a[previous]


class TestCash:
    @pytest.mark.parametrize("fill_price", ["close", "open"])
    def test_cash_identity_and_non_negative_cash_at_every_level(self, fill_price: str) -> None:
        params = _params(
            execution={"fill_price": fill_price},
            costs={
                "per_side_bps": 15.0,
                "sensitivity_per_side_bps": [0.0, 30.0, 100.0],
                "commission_per_share": 0.005,
                "commission_per_order": 1.0,
            },
        )
        results = _run(_provider(), params, levels=[0.0, 15.0, 30.0, 100.0])
        for level, result in results.items():
            sums = dict(
                result.position_values.group_by("session").agg(pl.col("value").sum()).iter_rows()
            )
            for row in result.equity:
                assert row.cash is not None
                assert row.cash >= 0, (level, row.session)
                assert row.equity == pytest.approx(row.cash + sums.get(row.session, 0.0), abs=1e-6)

    def test_cash_changes_only_on_fill_sessions(self) -> None:
        result = _run(_provider())[15.0]
        fills = {F0, F1, F2}
        rows = sorted(result.equity, key=lambda r: r.session)
        for before, after in itertools.pairwise(rows):
            if after.session not in fills:
                assert after.cash == before.cash, after.session

    def test_equity_carries_across_fills(self) -> None:
        """Equity is continuous from one step to the next: the fill moves value between
        cash and positions and pays costs, never creates money."""
        result = _run(_provider(), levels=[0.0])[0.0]
        equity = _equity(result)
        for rebalance, fill in ((T0, F0), (T1, F1), (T2, F2)):
            moves = _price_rows().filter(pl.col("session") == fill)
            assert moves.height  # the fill session has bars
            assert equity[fill] <= equity[rebalance] * (1 + max(GROWTH.values())) ** 2

    def test_equity_starts_at_initial_capital_and_covers_every_session(self) -> None:
        params = _params(backtest={"initial_capital": 50_000.0})
        result = _run(_provider(), params)[15.0]
        sessions = [row.session for row in result.equity]
        expected = [T0, *[s for s in all_sessions() if F0 <= s <= T3]]
        assert sessions == expected
        assert result.equity[0].equity == 50_000.0 and result.equity[0].cash == 50_000.0
        assert {row.series for row in result.equity} == {"strategy"}

    def test_costs_are_charged_and_never_raise_equity(self) -> None:
        results = _run(_provider(), levels=[0.0, 15.0, 100.0])
        finals = [_equity(results[level])[T3] for level in (0.0, 15.0, 100.0)]
        assert finals[0] > finals[1] > finals[2]
        assert _rows(results[0.0], T0).cost_paid == 0.0
        assert _rows(results[15.0], T0).cost_paid > 0.0
        assert _rows(results[15.0], T0).turnover == pytest.approx(0.5, rel=1e-2)

    def test_rebalance_rows_carry_the_signal_reads(self) -> None:
        result = _run(_provider())[15.0]
        assert [row.session for row in result.rebalances] == [T0, T1, T2]
        assert [row.fill_session for row in result.rebalances] == [F0, F1, F2]
        row = _rows(result, T0)
        assert (row.n_universe, row.n_targets) == (4, 2)
        assert row.counts == {"n_excluded_no_history": 0}
        assert row.cost_per_side_bps == 15.0


class TestDividends:
    """A dividend ex 2024-02-15 on a flat $100 name: raw drops to 99 with $1 paid."""

    EX = date(2024, 2, 15)

    def _provider(self, known: date, ex: date, *, dropped: bool) -> FakeProvider:
        rows, adjusted = [], []
        for session in SESSIONS:
            close = 99.0 if session >= ex else 100.0
            at = session_close(session)
            rows.append(("A", session, close, close, at))
            if session < ex:  # the restatement the dividend brings
                adjusted.append(("A", session, 99.0, 99.0, session_close(known)))
        schema = {
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        }
        raw = pl.DataFrame(rows, schema=schema, orient="row")
        with_dividend = pl.concat([raw, pl.DataFrame(adjusted, schema=schema, orient="row")])
        dividends = pl.DataFrame(
            {
                "security_id": ["A"],
                "ex_date": [ex],
                "ratio_or_amount": [1.0],
                "known_at": [session_close(known)],
            },
            schema_overrides={"known_at": pl.Datetime("us", "UTC")},
        )
        members = {s: ["A"] for s in rebalance_sessions(T0, HISTORY_END)}
        provider = FakeProvider(
            prices=with_dividend,
            dividend_prices=with_dividend,
            raw=raw,
            members=members,
            benchmarks={},
            dividends=dividends,
        )
        if dropped:
            provider.dropped = dividends
        return provider

    def _result(
        self, known: date, *, ex: date = EX, end: date = T3, dropped: bool = False
    ) -> BacktestResult:
        params = _params(strategy={"top_fraction": 1.0}, costs={"per_side_bps": 0.0})
        provider = self._provider(known, ex, dropped=dropped)
        return _run(provider, params, end=end, levels=[0.0])[0.0]

    def test_known_before_the_read_is_reinvested_in_that_step(self) -> None:
        result = self._result(known=date(2024, 2, 20))
        equity = _equity(result)
        assert equity[T1] == pytest.approx(equity[F0])
        assert equity[date(2024, 2, 14)] == pytest.approx(equity[F0])
        assert [row.n_late_dividends for row in result.rebalances] == [0, 0, 0]
        assert [row.n_dropped_dividends for row in result.rebalances] == [0, 0, 0]

    @pytest.mark.parametrize(
        ("known", "row_session"),
        [(date(2024, 3, 5), T1), (date(2024, 4, 3), T2), (date(2024, 5, 3), T3)],
        ids=["one-month", "two-months", "three-months"],
    )
    def test_known_after_the_read_is_lost_and_counted_late(
        self, known: date, row_session: date
    ) -> None:
        result = self._result(known=known, end=T4)
        equity = _equity(result)
        assert equity[T1] == pytest.approx(equity[F0] * 0.99)
        assert equity[T4] == pytest.approx(equity[F0] * 0.99)
        counts = {row.session: row.n_late_dividends for row in result.rebalances}
        assert counts == {s: int(s == row_session) for s in (T0, T1, T2, T3)}

    def test_a_late_dividend_on_a_name_not_held_on_its_ex_date_is_not_counted(self) -> None:
        # Ex 2024-01-22, before the first fill on F0: A was not held at the close before.
        result = self._result(known=date(2024, 3, 5), ex=date(2024, 1, 22))
        assert [row.n_late_dividends for row in result.rebalances] == [0, 0, 0]

    def test_a_dropped_dividend_is_counted_in_the_month_of_its_ex_date(self) -> None:
        result = self._result(known=date(2024, 2, 20), dropped=True)
        assert [row.n_dropped_dividends for row in result.rebalances] == [1, 0, 0]


class TestSpinOffs:
    """`adjust.apply_spin_offs` (backtest spec amendment #1894, T211e): the engine reads
    the two spin-off counts from the provider only under the opt-in key, a valued
    spin-off is a dividend in kind (item 2), and a late one matches a late dividend
    (item 4).

    The store's own frames are the hand fixture of `tests/lookahead/test_spin_off.py`: the
    held parent A flat at 100, a child C whose ex-date close is 20, a ratio of 0.5, so
    D = r x C = 10 and the factor is 0.9. `test_a_valued_spin_off_equals_the_dividend_in_kind`
    reads them through `adjusted_prices_as_of`, so the engine's equity over the two is
    the store's frame equality, not a stand-in's."""

    EX = date(2024, 2, 15)
    D, P, RATIO = 10.0, 100.0, 0.5

    @staticmethod
    def _params(*, on: bool) -> Settings:
        return _params(
            strategy={"top_fraction": 1.0},
            costs={"per_side_bps": 0.0},
            adjust={"apply_spin_offs": on},
        )

    @staticmethod
    def _members() -> dict[date, list[str]]:
        return {session: ["A"] for session in rebalance_sessions(T0, HISTORY_END)}

    @staticmethod
    def _bar(sid: str, session: date, close: float) -> dict[str, object]:
        stamp = session_close(session)
        return {
            "security_id": sid,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": stamp,
            "ingested_at": stamp,
            "source": "alpaca",
            "provenance": "bar",
        }

    def _base(self, *, drop: bool) -> pl.DataFrame:
        """Name A flat at P, dropping to P - D at the ex-date when `drop`."""
        schema = {
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        }
        rows = [
            (
                "A",
                session,
                self.P,
                self.P - self.D if drop and session >= self.EX else self.P,
                session_close(session),
            )
            for session in SESSIONS
        ]
        return pl.DataFrame(rows, schema=schema, orient="row")

    def _store_frames(self) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """`(spin_off, unadjusted, dividend)`: the store's frames for the held name A over
        the module sessions, once with the `spinoff` row (child C at 20, ratio 0.5, ex
        2024-02-15), once without it, and once with the dividend of r x C = 10 in its
        place. The parent is A, the member the engine actually holds."""
        conn = duckdb.connect(":memory:")
        configure_connection(conn)
        schema.init_schema(conn)
        for session in SESSIONS:
            conn_bar = self.P - self.D if session >= self.EX else self.P
            insert_row(conn, "prices_daily", self._bar("A", session, conn_bar))
        for session in SESSIONS:
            if session >= self.EX:
                insert_row(conn, "prices_daily", self._bar("C", session, self.D / self.RATIO))
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": "C",
                "action_type": "spinoff",
                "ex_date": self.EX,
                "ratio_or_amount": self.RATIO,
                "announced_at": None,
                "source_action_id": "A",
                "cancelled": False,
                "known_at": session_close(self.EX),
                "ingested_at": session_close(self.EX),
                "source": "alpaca",
                "provenance": "action",
            },
        )
        settings = Settings(_env_file=None)
        t = session_close(T2)
        spin = adjusted_prices_as_of(
            conn, t, ["A"], include_dividends=True, include_spin_offs=True, settings=settings
        )
        unadjusted = adjusted_prices_as_of(
            conn, t, ["A"], include_dividends=True, settings=settings
        )
        conn.execute("DELETE FROM corporate_actions")
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": "A",
                "action_type": "dividend",
                "ex_date": self.EX,
                "ratio_or_amount": self.D,
                "announced_at": None,
                "source_action_id": "",
                "cancelled": False,
                "known_at": session_close(self.EX),
                "ingested_at": session_close(self.EX),
                "source": "alpaca",
                "provenance": "action",
            },
        )
        dividend = adjusted_prices_as_of(conn, t, ["A"], include_dividends=True, settings=settings)
        conn.close()
        return spin, unadjusted, dividend

    def _provider(
        self,
        *,
        unvalued: bool = False,
        late: bool = False,
        unadjusted: pl.DataFrame | None = None,
        adjusted: pl.DataFrame | None = None,
    ) -> FakeProvider:
        """One always-held name A; the frames default to the flat dividend-in-kind frame,
        the declared rows are the test's (a row `ex` 2024-02-15, in the T0 step's window)."""
        base = unadjusted if unadjusted is not None else self._base(drop=True)
        frame = adjusted if adjusted is not None else self._base(drop=True)
        unvalued_rows = (
            pl.DataFrame(
                {
                    "security_id": ["A"],
                    "child": ["C"],
                    "ex_date": [self.EX],
                    "reason": ["no_child_close"],
                    "known_at": [session_close(date(2024, 2, 20))],
                },
                schema_overrides={"known_at": pl.Datetime("us", "UTC")},
            )
            if unvalued
            else pl.DataFrame(
                schema={
                    "security_id": pl.Utf8,
                    "child": pl.Utf8,
                    "ex_date": pl.Date,
                    "reason": pl.Utf8,
                    "known_at": pl.Datetime("us", "UTC"),
                }
            )
        )
        late_rows = (
            pl.DataFrame(
                {
                    "security_id": ["A"],
                    "ex_date": [self.EX],
                    "ratio_or_amount": [self.RATIO],
                    "known_at": [session_close(date(2024, 3, 5))],
                },
                schema_overrides={"known_at": pl.Datetime("us", "UTC")},
            )
            if late
            else pl.DataFrame(
                schema={
                    "security_id": pl.Utf8,
                    "ex_date": pl.Date,
                    "ratio_or_amount": pl.Float64,
                    "known_at": pl.Datetime("us", "UTC"),
                }
            )
        )
        dividends = pl.DataFrame(
            schema={
                "security_id": pl.Utf8,
                "ex_date": pl.Date,
                "ratio_or_amount": pl.Float64,
                "known_at": pl.Datetime("us", "UTC"),
            }
        )
        return FakeProvider(
            prices=base,
            dividend_prices=frame,
            raw=base,
            members=self._members(),
            benchmarks={},
            dividends=dividends,
            unvalued_spin_offs_rows=unvalued_rows,
            late_spin_offs_rows=late_rows,
        )

    def test_the_two_counts_are_written_only_under_the_opt_in(self) -> None:
        provider = self._provider(unvalued=True, late=True)
        on = _run(provider, self._params(on=True), levels=[0.0])[0.0]
        assert [row.counts.get("n_spin_offs_unvalued") for row in on.rebalances] == [1, 0, 0]
        assert [row.counts.get("n_late_spin_offs") for row in on.rebalances] == [0, 1, 0]
        assert {"unvalued_spin_offs", "late_spin_offs"} <= {call.method for call in provider.calls}
        # The same rows at the default: no count name, no read, byte-identical.
        off_provider = self._provider(unvalued=True, late=True)
        off = _run(off_provider, self._params(on=False), levels=[0.0])[0.0]
        plain = _run(self._provider(), self._params(on=False), levels=[0.0])[0.0]
        assert _run_digest(off) == _run_digest(plain)
        assert [row.counts for row in off.rebalances] == [row.counts for row in plain.rebalances]
        assert not {"unvalued_spin_offs", "late_spin_offs"} & {
            call.method for call in off_provider.calls
        }

    def test_a_mixed_opt_in_read_group_is_refused(self) -> None:
        """One provider holds one frozen `Settings`, so its `adjust.apply_spin_offs`
        decides the group's frames while each variant's own decides its counts: a group
        mixing the flag would count spin-offs no read served (item 3)."""
        variants = [
            (self._params(on=True), _handle()),
            (self._params(on=False), _handle(2)),
        ]
        provider = self._provider()
        with pytest.raises(ValueError, match="apply_spin_offs"):
            engine.run_many(variants, provider, T0, T3, (0.0,), family="momentum")
        assert provider.calls == []

    def test_a_provider_flag_that_differs_from_the_variants_is_refused(self) -> None:
        """The provider's own frozen `Settings` decide its frames (#1894, item 3), so a
        provider built from other settings would serve frames the variant's counts do not
        match: `run_many` refuses it before any read, like a mixed-flag group."""
        from tradepartner.backtest.provider import DataProvider

        class _Provider:
            settings = _params(adjust={"apply_spin_offs": False})

        variants = [(_params(adjust={"apply_spin_offs": True}), _handle())]
        with pytest.raises(ValueError, match=r"provider's adjust\.apply_spin_offs"):
            engine.run_many(
                variants,
                cast(DataProvider, _Provider()),
                T0,
                T3,
                (0.0,),
                family="momentum",
            )

    def test_a_valued_spin_off_equals_the_dividend_in_kind(self) -> None:
        """The store's `spinoff` frame equals its dividend of r x C to 1e-12, and the
        engine's equity over each is equal: a valued row (no unvalued or late listing)
        earns exactly the dividend, and the true trial's counts are zero (items 2, 3, 5)."""
        spin, unadjusted, dividend = self._store_frames()
        assert_frame_equal(spin, dividend, check_exact=False, abs_tol=1e-12)
        # Teeth: the factor is real, so a provider that dropped the opt-in read this frame.
        assert not spin.equals(unadjusted)
        on = _run(
            self._provider(unadjusted=unadjusted, adjusted=spin),
            self._params(on=True),
            levels=[0.0],
        )[0.0]
        off = _run(
            self._provider(unadjusted=unadjusted, adjusted=dividend),
            self._params(on=False),
            levels=[0.0],
        )[0.0]
        assert _equity(on) == pytest.approx(_equity(off), rel=1e-12)
        assert all(row.n_targets == 1 for row in on.rebalances)  # A is held, not cash
        assert all(row.counts.get("n_spin_offs_unvalued") == 0 for row in on.rebalances)
        assert all(row.counts.get("n_late_spin_offs") == 0 for row in on.rebalances)
        assert all("n_spin_offs_unvalued" not in row.counts for row in off.rebalances)

    #: The fixture universe's spin-off material for the store-driven late case (T211e):
    #: `SEC_SPLIT_FUTURE` is a member at every rebalance here and `SEC_SPLIT_PLAIN` has a
    #: bar on every session, so the child's E close and the parent's prior close are real
    #: stored bars, not a fake's frames.
    LATE_PARENT, LATE_CHILD = "SEC_SPLIT_FUTURE", "SEC_SPLIT_PLAIN"
    LATE_E = date(2018, 12, 13)
    LATE_KNOWN = datetime(2018, 12, 14, 21, 0, tzinfo=UTC)
    LATE_RATIO = 0.25
    LATE_START, LATE_END = date(2018, 11, 30), date(2018, 12, 31)

    @staticmethod
    def _plant(
        conn: duckdb.DuckDBPyConnection,
        *,
        security_id: str,
        action_type: str,
        amount: float,
        source_action_id: str,
        at: datetime,
    ) -> None:
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": security_id,
                "action_type": action_type,
                "ex_date": TestSpinOffs.LATE_E,
                "ratio_or_amount": amount,
                "announced_at": None,
                "source_action_id": source_action_id,
                "cancelled": False,
                "known_at": at,
                "ingested_at": at,
                "source": "alpaca",
                "provenance": "action",
            },
        )

    @staticmethod
    def _late_trial(conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str) -> Any:
        hypothesis = registry.register_hypothesis(
            conn,
            slug=slug,
            family="momentum",
            title=slug,
            doc_path=f"docs/hypotheses/{slug}.md",
            doc_sha256="0" * 64,
            params={"costs.per_side_bps": 0.0},
            in_sample_start=TestSpinOffs.LATE_START,
            holdout_start=date(2020, 1, 1),
            holdout_end=date(2021, 12, 31),
            registered_by="test",
            settings=settings,
        )
        return registry.open_trial(
            conn,
            hypothesis_id=hypothesis.hypothesis_id,
            kind="in_sample",
            start_session=TestSpinOffs.LATE_START,
            end_session=TestSpinOffs.LATE_END,
            data_cutoff=session_close(TestSpinOffs.LATE_END),
            synthetic=True,
            run_by="test",
            settings=settings,
        )

    def _late_run(self, conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str) -> Any:
        handle = self._late_trial(conn, settings, slug)
        connect = _lend(conn)
        with StoreProvider(connect, handle, settings, registry_connect=connect) as provider:
            return run(
                settings,
                provider,
                self.LATE_START,
                self.LATE_END,
                handle,
                (0.0,),
                family="momentum",
            )[0.0]

    @pytest.mark.usefixtures("_no_env_file")
    def test_a_late_spin_off_matches_a_late_dividend(self) -> None:
        """A `spinoff` row first known after the step that owned its ex-date is counted late
        on a held parent and never restates the earlier steps (item 4, T211e). Run on a real
        store through `StoreProvider`: the same row known in time scales the parent's pre-E
        marks and moves equity away from the row-free store (the adjustment is real, so the
        equivalence has teeth), while the late row's `true` book equals a late parent
        dividend of r x C, to 1e-12, and the row-free store at every session (no
        restatement). The step that first knows the late row counts it once."""
        base = Settings(
            _env_file=None,
            strategy={"top_fraction": 1.0},
            costs={"per_side_bps": 0.0},
            schedule={"rebalance_cadence": "daily"},
        )
        on = Settings(
            _env_file=None,
            strategy={"top_fraction": 1.0},
            costs={"per_side_bps": 0.0},
            schedule={"rebalance_cadence": "daily"},
            adjust={"apply_spin_offs": True},
        )
        plain = _fixture_conn()
        (child_close,) = (
            prices_as_of(plain, session_close(self.LATE_E), [self.LATE_CHILD])
            .filter(pl.col("session") == self.LATE_E)["close"]
            .to_list()
        )
        amount = self.LATE_RATIO * child_close
        in_time = session_close(date(2018, 12, 12))  # the session before E, known by its step

        def spin_row(at: datetime) -> duckdb.DuckDBPyConnection:
            conn = _fixture_conn()
            self._plant(
                conn,
                security_id=self.LATE_CHILD,
                action_type="spinoff",
                amount=self.LATE_RATIO,
                source_action_id=self.LATE_PARENT,
                at=at,
            )
            return conn

        def div_row(at: datetime) -> duckdb.DuckDBPyConnection:
            conn = _fixture_conn()
            self._plant(
                conn,
                security_id=self.LATE_PARENT,
                action_type="dividend",
                amount=amount,
                source_action_id="",
                at=at,
            )
            return conn

        none = self._late_run(plain, base, "h-spin-none")
        spin = self._late_run(spin_row(self.LATE_KNOWN), on, "h-spin-late")
        div = self._late_run(div_row(self.LATE_KNOWN), base, "h-div-late")
        timed = self._late_run(spin_row(in_time), on, "h-spin-timed")
        timed_div = self._late_run(div_row(in_time), base, "h-div-timed")
        # The store seeds benchmarks (SPY, MTUM), whose equity rows share every session: keep
        # the strategy series alone, as `_equity` would drop it for a benchmark's.
        et, etd = _strategy_equity(timed), _strategy_equity(timed_div)
        es, ed, en = _strategy_equity(spin), _strategy_equity(div), _strategy_equity(none)
        assert all(row.n_targets > 0 for row in spin.rebalances)  # the parent is held, not cash
        # In time, the factor is real: the spin-off's book equals the dividend's and moves away
        # from the row-free store, so the late equality below is not a flat-cash tautology.
        assert et == pytest.approx(etd, rel=1e-12)
        assert et[self.LATE_E] != pytest.approx(en[self.LATE_E], rel=1e-9)
        # Late: the `true` book equals the late dividend's and the row-free store everywhere
        # (earlier steps' equity is never restated when the row arrives).
        assert set(es) == set(ed) == set(en)
        for session in es:
            assert es[session] == pytest.approx(ed[session], rel=1e-12)
            assert es[session] == pytest.approx(en[session], rel=1e-12)
        late_spin = {row.session: row.counts.get("n_late_spin_offs") for row in spin.rebalances}
        assert late_spin[self.LATE_E] == 1
        assert sum(count or 0 for count in late_spin.values()) == 1
        assert all(row.counts.get("n_spin_offs_unvalued") == 0 for row in spin.rebalances)
        assert all(row.counts.get("n_late_spin_offs") == 0 for row in timed.rebalances)
        assert [row.n_late_dividends for row in div.rebalances if row.session == self.LATE_E] == [1]


class TestInvariance:
    def test_a_shorter_run_is_a_prefix_of_a_longer_one(self) -> None:
        short = _run(_provider(), end=T2, levels=[0.0, 15.0])
        long = _run(_provider(), end=T3, levels=[0.0, 15.0])
        for level in (0.0, 15.0):
            s, full = short[level], long[level]
            assert s.equity == tuple(row for row in full.equity if row.session <= T2)
            assert s.rebalances == tuple(row for row in full.rebalances if row.session < T2)
            assert s.weights == tuple(w for w in full.weights if w.fill_session <= F1)

    def test_stitched_returns_come_from_the_marking_frames(self) -> None:
        result = _run(_provider())[15.0]
        assert [(f.start, f.end) for f in result.marking_frames] == [(T0, T1), (T1, T2), (T2, T3)]
        stitched = result.stitched_returns()
        assert set(stitched["security_id"].unique()) >= {"A", "B", "C"}


class TestNegativeValueGuard:
    """ADR 0015 TE5: a negative position value raises; zero is still dropped."""

    @staticmethod
    def _patched(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
        real = engine.value_positions

        def patched(*args: Any, **kwargs: Any) -> pl.DataFrame:
            frame = real(*args, **kwargs)
            first = frame["security_id"][0]
            return frame.with_columns(
                pl.when(pl.col("security_id") == first)
                .then(value)
                .otherwise(pl.col("value"))
                .alias("value")
            )

        monkeypatch.setattr(engine, "value_positions", patched)

    def test_a_negative_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patched(monkeypatch, -1.0)
        with pytest.raises(ValueError, match="negative position value"):
            _run(_provider())

    def test_a_zero_value_is_still_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patched(monkeypatch, 0.0)
        assert _run(_provider())[15.0].equity


class TestApplyTrades:
    COMMISSIONS = Commissions(per_share=0.01, per_order=1.0)
    FRAME = pl.DataFrame(
        {
            "security_id": ["A", "B", "C"],
            "session": [F0, F0, F0],
            "open": [10.0, 20.0, 40.0],
            "close": [11.0, 22.0, 44.0],
        }
    )
    #: Raw prices double the adjusted ones (a 2:1 split later in the month).
    RAW = FRAME.with_columns(pl.col("open") * 2, pl.col("close") * 2)

    def test_sells_fund_buys_and_costs_leave_cash_non_negative(self) -> None:
        result = apply_trades(
            {"A": 600.0, "B": 400.0},
            0.0,
            {"C": 0.5, "A": 0.5},
            self.FRAME,
            self.RAW,
            F0,
            fill_price="close",
            per_side_bps=100.0,
            commissions=self.COMMISSIONS,
        )
        sell_a = trade_cost(100.0, 100.0 / 22.0, 100.0, self.COMMISSIONS)
        sell_b = trade_cost(400.0, 400.0 / 44.0, 100.0, self.COMMISSIONS)
        assert result.cash >= 0.0
        assert result.positions["A"] == pytest.approx(500.0)
        assert "B" not in result.positions
        bought = result.positions["C"]
        buy_c = trade_cost(bought, bought / 88.0, 100.0, self.COMMISSIONS)
        assert result.cost_paid == pytest.approx(sell_a + sell_b + buy_c)
        assert result.cash == pytest.approx(500.0 - sell_a - sell_b - bought - buy_c)
        assert result.turnover == pytest.approx((100.0 + 400.0 + bought) / 2 / 1000.0)
        assert result.missing == ()

    def test_names_without_a_bar_are_not_traded(self) -> None:
        frame = self.FRAME.filter(pl.col("security_id") != "B")
        result = apply_trades(
            {"B": 500.0},
            500.0,
            {"C": 1.0},
            frame,
            self.RAW,
            F0,
            fill_price="open",
            per_side_bps=0.0,
            commissions=Commissions(per_share=0.0, per_order=0.0),
        )
        assert result.positions["B"] == 500.0  # unsold, last mark
        assert result.positions["C"] == pytest.approx(500.0)
        assert result.missing == ("B",)

    def test_a_trim_smaller_than_its_cost_is_skipped_not_fatal(self) -> None:
        """Cash near zero after a full rebalance and a $1 order fee: a $0.40 drift trim
        would cost more than it raises, so it is not an order (quant-auditor, PR #159)."""
        frame = self.FRAME.filter(pl.col("security_id") != "C")
        result = apply_trades(
            {"A": 5000.4, "B": 4999.6},
            0.0,
            {"A": 0.5, "B": 0.5},
            frame,
            frame,
            F0,
            fill_price="close",
            per_side_bps=15.0,
            commissions=Commissions(per_share=0.0, per_order=1.0),
        )
        assert result.positions == {"A": 5000.4, "B": 4999.6}
        assert result.cash == 0.0
        assert result.trades == ()

    def test_an_exit_costing_more_than_it_raises_is_funded_by_other_sells(self) -> None:
        result = apply_trades(
            {"A": 0.5, "B": 1000.0},
            0.0,
            {"B": 0.5},
            self.FRAME,
            self.RAW,
            F0,
            fill_price="close",
            per_side_bps=0.0,
            commissions=Commissions(per_share=0.0, per_order=1.0),
        )
        assert "A" not in result.positions
        assert result.cash >= 0.0

    def test_a_buy_that_costs_more_than_its_notional_is_skipped(self) -> None:
        """$1.01 left and a $1 order fee: buying $0.01 would pay $1 for it."""
        result = apply_trades(
            {},
            1.01,
            {"B": 1.0},
            self.FRAME,
            self.RAW,
            F0,
            fill_price="close",
            per_side_bps=0.0,
            commissions=Commissions(per_share=0.0, per_order=1.0),
        )
        assert result.trades == ()
        assert result.cash == 1.01

    def test_a_buy_is_sized_after_costs(self) -> None:
        result = apply_trades(
            {},
            1000.0,
            {"A": 1.0},
            self.FRAME,
            self.RAW,
            F0,
            fill_price="close",
            per_side_bps=100.0,
            commissions=Commissions(per_share=0.0, per_order=0.0),
        )
        assert result.positions["A"] == pytest.approx(1000.0 / 1.01)
        assert result.cash >= 0.0


# Spec acceptance (Costs and metrics): no numeric literals in these modules other than these.
ALLOWED_LITERALS = {0, 1, -1, 2, 4, 12, 10_000}


@pytest.mark.parametrize("module", ["fills.py", "engine.py"])
def test_no_stray_numeric_literals(module: str) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    stray = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in ALLOWED_LITERALS
    ]
    assert stray == []


class TestPublicPlan:
    """Phase 4 plans with the engine's own function (plan T53): the public `plan`
    is the one the loop calls, and returns the same plan at every rebalance."""

    def test_the_public_plan_equals_the_loops_plan_at_every_rebalance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[engine.Plan] = []
        original = engine._plan

        def recording(provider: Any, params: Settings, session: date, family: str) -> engine.Plan:
            result = original(provider, params, session, family)
            recorded.append(result)
            return result

        monkeypatch.setattr(engine, "_plan", recording)
        _run(_provider(), end=T4)
        monkeypatch.setattr(engine, "_plan", original)  # the check below calls the real one
        # Every rebalance but the last is planned, in order, once.
        assert [p.session for p in recorded] == [T0, T1, T2, T3]
        for loop_plan in recorded:
            assert (
                engine.plan(_provider(), _params(), loop_plan.session, family="momentum")
                == loop_plan
            )

    def test_a_plan_carries_its_targets_and_reads(self) -> None:
        public = engine.plan(_provider(), _params(), T0, family="momentum")
        assert isinstance(public, engine.Plan)
        assert (public.session, public.fill_session) == (T0, F0)
        assert set(public.targets) == {"A", "B"}
        assert public.n_universe == 4

    def test_the_plan_carries_the_loops_universe_scores_and_exclusions(self) -> None:
        """T53b: the universe members, the signal scores and the names excluded for no
        history are exactly what the plan read at close(T0) (the momentum signal over
        the members' frame), and agree with the counts the rebalance row records."""
        provider = _provider(skip=[("D", date(2023, 12, 29))])  # D's skip-month anchor
        params = _params()
        public = engine.plan(provider, params, T0, family="momentum")
        t = read_time(T0)
        members = sorted(provider.universe(t).members["security_id"].to_list())
        strategy = params.strategy
        frame = provider.adjusted_prices(t, members, strategy.signal_total_return)
        signal = momentum_12_1(
            frame, T0, strategy.formation_months, strategy.skip_months, security_ids=members
        )
        assert public.members == tuple(members) == ("A", "B", "C", "D")
        assert public.scores == signal.scores
        assert set(public.scores) == {"A", "B", "C"}
        assert public.excluded_no_history == signal.excluded == ("D",)
        assert public.n_universe == len(public.members)
        assert public.n_excluded_no_history == len(public.excluded_no_history)
        assert set(public.targets) <= set(public.scores)
        # The generic fields (#1153, T127) agree with the momentum-named ones.
        # Every declared reason has an entry (#1358: `no_turnover` is empty and its counts
        # unreported at the default `strategy.turnover_top_fraction = 1.0`).
        assert public.exclusions == {
            "no_history": public.excluded_no_history,
            "no_turnover": (),
            "no_t0_bar": (),
        }
        assert public.counts == {"n_excluded_no_history": public.n_excluded_no_history}

    def test_the_new_fields_leave_the_run_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No behaviour change: a run whose plans drop the new fields' contents gives
        the same result (the loop reads only the fields it read before T53b)."""
        provider_skip = [("D", date(2023, 12, 29))]
        want = _run(_provider(provider_skip), end=T4)
        original = engine._plan

        def emptied(provider: Any, params: Settings, session: date, family: str) -> engine.Plan:
            plan = original(provider, params, session, family)
            return dataclasses.replace(plan, members=(), scores={}, excluded_no_history=())

        monkeypatch.setattr(engine, "_plan", emptied)
        got = _run(_provider(provider_skip), end=T4)
        for level, result in want.items():
            assert got[level].equity == result.equity
            assert got[level].rebalances == result.rebalances
            assert got[level].targets == result.targets


# --- cadence (strategy-lab spec req 6, plan T98) ------------------------------------

#: E stops trading on this session but stays in the universe: at every rebalance below,
#: its last bar precedes the signal frame's bound A_form.
E_LAST_BAR = date(2023, 1, 20)


def _cadence_provider(
    cadence: Cadence, start: date, end: date, *, unbounded: bool = False
) -> FakeProvider:
    """`_provider`'s names plus the halted E, members at every rebalance at `cadence`."""
    # E trades like A until it halts.
    e_rows = (
        _price_rows()
        .filter((pl.col("security_id") == "A") & (pl.col("session") <= E_LAST_BAR))
        .with_columns(pl.lit("E").alias("security_id"))
    )
    prices = pl.concat([_price_rows(), e_rows])
    # The fake's adjusted rows encode late corrections to older bars, including
    # the sort of restatement an action would cause in the store provider.
    corrections = prices.filter(
        ((pl.col("security_id") == "A") & (pl.col("session") == date(2024, 2, 1)))
        | ((pl.col("security_id") == "B") & (pl.col("session") == date(2024, 2, 2)))
    ).with_columns(
        pl.col("close") * 0.98,
        pl.col("open") * 0.98,
        pl.when(pl.col("security_id") == "A")
        .then(session_close(date(2024, 2, 6)))
        .otherwise(session_close(date(2024, 2, 9)))
        .alias("known_at"),
    )
    adjusted = pl.concat([prices, corrections])
    names = [*sorted(GROWTH), "E"]
    members = {session: names for session in rebalance_sessions(start, end, cadence)}
    kind = _UnboundedProvider if unbounded else _CachedProvider
    return kind(prices=adjusted, members=members, benchmarks={})


class _CachedProvider(FakeProvider):
    """The fake's as-of revision rows provide a real marking change probe."""

    change_checks = 0
    changed_names: set[str]

    def marking_changed_ids(
        self, previous_t: datetime, t: datetime, ids: Sequence[str]
    ) -> tuple[str, ...]:
        self.change_checks += 1
        changed = (
            self.prices.filter(
                pl.col("security_id").is_in(ids)
                & (pl.col("known_at") > previous_t)
                & (pl.col("known_at") <= t)
                & (pl.col("session") <= last_completed_session(previous_t))
            )["security_id"]
            .unique()
            .sort()
            .to_list()
        )
        self.changed_names = getattr(self, "changed_names", set()) | set(changed)
        return tuple(changed)


class _UnboundedProvider(FakeProvider):
    """The engine as it read before T98: every signal frame unbounded."""

    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        return super().adjusted_prices(t, ids, include_dividends)


def _assert_same_results(
    got: Mapping[float, BacktestResult], want: Mapping[float, BacktestResult]
) -> None:
    assert sorted(got) == sorted(want)
    for level, result in want.items():
        other = got[level]
        assert other.equity == result.equity
        assert other.rebalances == result.rebalances
        assert other.weights == result.weights
        assert other.targets == result.targets
        assert other.position_values.equals(result.position_values)
        assert len(other.marking_frames) == len(result.marking_frames)
        for a, b in zip(other.marking_frames, result.marking_frames, strict=True):
            assert (a.start, a.end) == (b.start, b.end)
            if a.frame.is_empty():
                assert b.frame.is_empty()
            else:
                assert a.frame.equals(b.frame.filter(pl.col("session") >= a.frame["session"].min()))
            # Stitching and valuation need each name's latest bar at or
            # before this step's start, even if its first new bar is later.
            anchors = (
                b.frame.filter(pl.col("session") <= a.start)
                .group_by("security_id")
                .agg(pl.col("session").max())
            )
            for sid, anchor_session in anchors.iter_rows():
                assert (
                    a.frame.filter(
                        (pl.col("security_id") == sid) & (pl.col("session") == anchor_session)
                    ).height
                    == 1
                )


class TestSignalFrameBound:
    """The signal frame read from A_form on gives the unbounded engine's results row
    for row (plan T98)."""

    @pytest.mark.parametrize(
        ("cadence", "anchor", "start", "end"),
        [
            ("week_end", "month_end", date(2024, 1, 5), date(2024, 4, 26)),
            # T = 2024-02-05: T - 12 months is Sunday 2023-02-05, so A_form is 2023-02-03.
            ("daily", "offset", date(2024, 1, 29), date(2024, 2, 29)),
        ],
    )
    def test_bounded_equals_unbounded_row_for_row(
        self, cadence: Cadence, anchor: SignalAnchor, start: date, end: date
    ) -> None:
        params = _params(schedule={"rebalance_cadence": cadence, "signal_anchor": anchor})
        levels = (0.0, 15.0)
        bounded = _cadence_provider(cadence, start, end)
        got = run(params, bounded, start, end, _handle(), levels, family="momentum")
        want = run(
            params,
            _cadence_provider(cadence, start, end, unbounded=True),
            start,
            end,
            _handle(),
            levels,
            family="momentum",
        )
        _assert_same_results(got, want)
        assert isinstance(bounded, _CachedProvider)
        assert bounded.change_checks > 0
        assert bounded.changed_names == {"A", "B"}

        signal_reads = [
            call
            for call in bounded.calls
            if call.method == "adjusted_prices" and len(call.ids or ()) == len(GROWTH) + 1
        ]
        sessions = rebalance_sessions(start, end, cadence)
        assert len(signal_reads) == len(sessions) - 1  # one per plan, none at T_n
        # The bound cut the halted name out of every signal frame, and E is never scored.
        assert all(call.sessions_from > E_LAST_BAR for call in signal_reads)
        assert all(row.counts["n_excluded_no_history"] >= 1 for row in got[0.0].rebalances)
        if anchor == "offset":
            assert date(2024, 2, 5) in sessions
            assert date(2023, 2, 5).weekday() == 6  # the weekend T - k
            assert any(call.sessions_from == date(2023, 2, 3) for call in signal_reads)

    def test_the_marking_read_is_bounded(self) -> None:
        provider = _provider()
        _run(provider)
        marking = [
            call
            for call in provider.calls
            if call.method == "adjusted_prices" and len(call.ids or ()) < len(GROWTH)
        ]
        assert len(marking) == len(rebalance_sessions(T0, T3)) - 1
        # T0's 12-month formation anchor is 2023-01-31; A remains held,
        # so it is earlier than each step's five-session stale window.
        assert [(call.t, call.sessions_from) for call in marking] == [
            (read_time(T1), date(2023, 1, 31)),
            (read_time(T2), date(2023, 1, 31)),
            (read_time(T3), date(2023, 1, 31)),
        ]

    def test_stale_exit_window_sets_an_oracle_marking_bound(self) -> None:
        start, end = date(2024, 1, 5), date(2024, 1, 19)
        sessions = rebalance_sessions(start, end, "week_end")
        provider = FakeProvider(
            prices=_price_rows(),
            members={session: ["A"] for session in sessions},
            benchmarks={},
        )
        run(
            _params(
                strategy={"top_fraction": 1.0},
                schedule={"rebalance_cadence": "week_end"},
                backtest={"stale_exit_sessions": 10},
            ),
            provider,
            start,
            end,
            _handle(),
            (0.0,),
            family="oracle",
        )
        marking = [
            call
            for call in provider.calls
            if call.method == "adjusted_prices"
            and call.include_dividends
            and call.ids == ("A",)
            and call.sessions_from is not None
            and call.sessions_from >= date(2023, 12, 1)
        ]
        assert [(call.t, call.sessions_from) for call in marking] == [
            (read_time(date(2024, 1, 12), "week_end"), date(2023, 12, 28)),
            (read_time(date(2024, 1, 19), "week_end"), date(2024, 1, 4)),
        ]

    def test_reentry_keeps_the_last_stitched_session(self) -> None:
        start, end = date(2024, 1, 5), date(2024, 3, 1)
        sessions = rebalance_sessions(start, end, "week_end")
        order = ["A", "C", "D", "B", "A"] + ["A"] * 10
        assert sum(sessions[0] < s < sessions[4] for s in all_sessions()) > 5
        members = {session: [order[i]] for i, session in enumerate(sessions)}
        provider = FakeProvider(prices=_price_rows(), members=members, benchmarks={})
        result = run(
            _params(
                strategy={"top_fraction": 1.0},
                schedule={"rebalance_cadence": "week_end"},
                costs={"per_side_bps": 0.0, "sensitivity_per_side_bps": []},
            ),
            provider,
            start,
            end,
            _handle(family="oracle"),
            (0.0,),
            family="oracle",
        )[0.0]
        assert not result.stitched_returns().is_empty()
        last_stitched: dict[str, date] = {}
        saw_reentry = False
        for step in result.marking_frames:
            inside = (
                step.frame.filter(
                    (pl.col("session") > step.start) & (pl.col("session") <= step.end)
                )
                .group_by("security_id")
                .agg(pl.col("session").max())
            )
            for sid, session in inside.iter_rows():
                if sid in last_stitched:
                    assert (
                        step.frame.filter(
                            (pl.col("security_id") == sid)
                            & (pl.col("session") == last_stitched[sid])
                        ).height
                        == 1
                    )
                    if sid == "A" and step.end >= date(2024, 2, 9):
                        saw_reentry = True
                last_stitched[sid] = session
        assert saw_reentry


class TestCombinationsAndLag:
    """The spec's "Combinations and lag" criterion on a synthetic two-name case at
    `daily`, where F_i = T_{i+1}: both names held at equal weight, close fills, no
    costs, so every rebalance trades only the drift of the session before it."""

    START, END = date(2024, 1, 29), date(2024, 2, 9)

    def _result(self) -> BacktestResult:
        prices = _price_rows().filter(pl.col("security_id").is_in(["A", "B"]))
        members = {s: ["A", "B"] for s in rebalance_sessions(self.START, self.END, "daily")}
        provider = FakeProvider(prices=prices, members=members, benchmarks={})
        params = _params(
            strategy={"top_fraction": 1.0, "weighting": "equal"},
            schedule={"rebalance_cadence": "daily"},
            execution={"fill_price": "close"},
            costs={"per_side_bps": 0.0, "sensitivity_per_side_bps": []},
        )
        return run(params, provider, self.START, self.END, _handle(), (0.0,), family="momentum")[
            0.0
        ]

    def _close(self, sid: str) -> dict[date, float]:
        prices = _price_rows().filter(pl.col("security_id") == sid)
        return dict(prices.select("session", "close").iter_rows())

    def test_the_next_rebalance_trades_from_the_fill_at_f_i(self) -> None:
        """The plan read at close(T_{i+1}) = close(F_i) trades from the positions the
        fill at F_i bought, drifted one session: its turnover is that drift alone (were
        the plan read before the fill, the trade would start from cash: turnover 1/2)."""
        result = self._result()
        a, b = self._close("A"), self._close("B")
        sessions = rebalance_sessions(self.START, self.END, "daily")
        rows = result.rebalances
        assert [row.fill_session for row in rows] == sessions[1:]  # F_i = T_{i+1}
        assert math.isclose(rows[0].turnover, 0.5)  # the first buy, from cash
        for previous, row in itertools.pairwise(rows):
            f_prev, f = previous.fill_session, row.fill_session
            growth_a, growth_b = a[f] / a[f_prev], b[f] / b[f_prev]
            drift = abs(growth_a - growth_b) / (2 * (growth_a + growth_b))
            assert math.isclose(row.turnover, drift, rel_tol=1e-9)
            assert 0 < row.turnover < rows[0].turnover

    def test_the_filled_position_earns_the_full_next_period(self) -> None:
        """Bought at close(F_i), held at equal weight to close(F_{i+1}): equity grows by
        the mean of the two names' close-to-close returns over that period, and holds
        cash until close(F_0) -- a one-session lag (hand-computed equity)."""
        result = self._result()
        capital = _params().backtest.initial_capital
        a, b = self._close("A"), self._close("B")
        fills = [row.fill_session for row in result.rebalances]
        expected = {self.START: capital, fills[0]: capital}
        for f_prev, f in itertools.pairwise(fills):
            expected[f] = expected[f_prev] * (a[f] / a[f_prev] + b[f] / b[f_prev]) / 2
        equity = _equity(result)
        assert set(equity) == set(expected)
        for session, value in expected.items():
            assert math.isclose(equity[session], value, rel_tol=1e-12)


@pytest.fixture
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live ones.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _lend(conn: duckdb.DuckDBPyConnection) -> Callable[[], Any]:
    @contextmanager
    def _connect() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return _connect


#: The fixture universe, for the store-driven spin-off test (T211e).
FIXTURE_UNIVERSE = Path(__file__).resolve().parents[1] / "fixtures" / "universe"


def _fixture_conn() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory store with the fixture universe loaded, as `fixture_store` builds."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, FIXTURE_UNIVERSE)
    return conn


@pytest.mark.usefixtures("_no_env_file")
@pytest.mark.parametrize(
    ("cadence", "start", "end"),
    [
        ("week_end", date(2019, 1, 4), date(2019, 3, 29)),
        ("daily", date(2019, 1, 2), date(2019, 2, 28)),
    ],
)
def test_a_run_at_cadence_on_the_fixture_store_completes(
    fixture_store: duckdb.DuckDBPyConnection, cadence: Cadence, start: date, end: date
) -> None:
    settings = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        schedule={"rebalance_cadence": cadence},
    )
    hypothesis = registry.register_hypothesis(
        fixture_store,
        slug=f"h-{cadence}",
        family="momentum",
        title=f"engine at {cadence}",
        doc_path=f"docs/hypotheses/h-{cadence}.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": 15.0, "schedule.rebalance_cadence": cadence},
        in_sample_start=start,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    sessions = rebalance_sessions(start, end, cadence)
    handle = registry.open_trial(
        fixture_store,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=start,
        end_session=end,
        data_cutoff=read_time(sessions[-1], cadence),
        synthetic=True,
        run_by="test",
        settings=settings,
    )
    connect = _lend(fixture_store)
    with StoreProvider(connect, handle, settings, registry_connect=connect) as provider:
        results = run(settings, provider, start, end, handle, (0.0, 15.0), family="momentum")
    for result in results.values():
        assert [row.session for row in result.rebalances] == sessions[:-1]
        assert [row.fill_session for row in result.rebalances] == [
            fill_session(t, cadence) for t in sessions[:-1]
        ]
        assert all(row.n_targets > 0 for row in result.rebalances)
        strategy = sorted(r.session for r in result.equity if r.series == STRATEGY_SERIES)
        assert strategy == [s for s in all_sessions() if sessions[0] <= s <= sessions[-1]]
        assert all(math.isfinite(r.equity) and r.equity > 0 for r in result.equity)
    if cadence == "daily":
        assert all(
            row.fill_session == t_next
            for row, t_next in zip(results[0.0].rebalances, sessions[1:], strict=True)
        )


# --- literal pins of the H1 twin's run, taken on `main`'s code (#1358, T165) ----------

TWIN_FILE = Path(__file__).parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"


def _twin_settings() -> Settings:
    """The momentum fixture twin's (H1's) signal, cost and gap keys over the defaults,
    as its registration freezes them; the holdout is the defaults', outside this run."""
    from tradepartner.backtest import hypothesis

    parsed = hypothesis.parse_file(TWIN_FILE)
    nested: dict[str, dict[str, Any]] = {}
    for key, value in parsed.file_params.items():
        section, _, name = key.partition(".")
        if section != "holdout":
            nested.setdefault(section, {})[name] = value
    return Settings(_env_file=None, **nested)  # type: ignore[arg-type]


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _rounded(v) for k, v in sorted(value.items())}
    if isinstance(value, tuple | list):
        return [_rounded(v) for v in value]
    return value


def _run_digest(result: BacktestResult) -> str:
    import hashlib
    import json

    payload = {
        "equity": [_rounded(dataclasses.astuple(row)) for row in result.equity],
        "rebalances": [
            _rounded({**row.columns(), "counts": row.counts}) for row in result.rebalances
        ],
        "weights": [_rounded(dataclasses.astuple(row)) for row in result.weights],
        "targets": _rounded({d.isoformat(): t for d, t in result.targets.items()}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _call_log(provider: FakeProvider) -> list[tuple[Any, ...]]:
    return [
        (
            call.method,
            call.t.isoformat(),
            call.ids,
            call.include_dividends,
            None if call.t_prev is None else call.t_prev.isoformat(),
            None if call.sessions_from is None else call.sessions_from.isoformat(),
        )
        for call in provider.calls
    ]


def test_h1_twin_run_on_the_fake_provider_is_pinned() -> None:
    """The H1 twin's fake-provider call log and `in_sample` run, by literal on `main`'s
    code before `strategy.turnover_top_fraction` existed (T165): the key at its default
    1.0 must read nothing new and change nothing (T165c keeps this green)."""
    import hashlib
    import json

    settings = _twin_settings()
    provider = _provider()
    levels = tuple(settings.costs.sensitivity_per_side_bps)
    results = run(settings, provider, T0, T4, _handle(), levels, family="momentum")
    log = _call_log(provider)
    assert {call.method for call in provider.calls} == TWIN_CALL_METHODS
    assert len(log) == TWIN_N_CALLS
    assert hashlib.sha256(json.dumps(log).encode()).hexdigest() == TWIN_CALL_LOG_SHA256
    assert {level: _run_digest(result) for level, result in results.items()} == TWIN_RUN_DIGESTS
    finals = {level: result.equity[-1].equity for level, result in results.items()}
    assert finals == pytest.approx(TWIN_FINAL_EQUITY, rel=1e-12)


def test_the_twin_pin_fails_if_the_screen_reads_or_runs_at_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin above has teeth (T165c): with the screen forced on at 1.0, the twin's run
    makes a turnover read and reports the screen's counts, so its log and digests move."""
    import hashlib
    import json

    from tradepartner.backtest import strategies

    monkeypatch.setattr(strategies, "_screens", lambda params: True)
    settings = _twin_settings()
    assert settings.strategy.turnover_top_fraction == 1.0
    provider = _provider()
    provider.raw = provider.prices.with_columns(pl.lit(1000.0).alias("volume"))
    results = run(settings, provider, T0, T4, _handle(), (0.0,), family="momentum")
    assert "turnover_inputs" in {call.method for call in provider.calls}
    log = _call_log(provider)
    assert hashlib.sha256(json.dumps(log).encode()).hexdigest() != TWIN_CALL_LOG_SHA256
    assert _run_digest(results[0.0]) != TWIN_RUN_DIGESTS[0.0]


#: The provider methods the twin's run calls: no turnover read. `chains` (#821,
#: T187b) is the engine's once-per-step live successor-chain read; it is empty on
#: the fake and changes no run digest, only the call log.
TWIN_CALL_METHODS = {
    "universe",
    "chains",
    "adjusted_prices",
    "raw_prices",
    "static_listing_count",
    "survivorship_gap",
    "benchmark_ids",
    "listing_ends",
    "dropped_dividends",
    "late_dividends",
}
TWIN_N_CALLS = 41
TWIN_CALL_LOG_SHA256 = "84941e2098cbff6122a610b8b2a0b2b63f13a7a8c6a23378fb25116ac867f25a"
#: Per cost level: a SHA-256 over the equity, rebalance (counts included), weight and
#: target rows, floats rounded to nine places.
TWIN_RUN_DIGESTS = {
    0.0: "cc8ef92465dd2de9751bb299f14ec3c8bfb091a9b787f7f26ed7da80fd228eeb",
    30.0: "8580101aaa32339c8321c4bede06f244dbfe84d16d11423abfb77871b8ea3212",
    60.0: "284267f52861c0bc8254a6c66b29cc8b0bbaac615c61e35105f669c4ac249883",
    100.0: "6444e18ce51e309a3a27691a639c36eac4917a627f4d320335b80fea667c3c7e",
}
TWIN_FINAL_EQUITY = {
    0.0: 115350.9398460223,
    30.0: 114317.95046215717,
    60.0: 113295.2130230013,
    100.0: 111947.28992016679,
}


class TestSuccessorChains:
    """The engine's rename of a chained predecessor (#821, plan T187b)."""

    def _variant(self) -> engine._Variant:
        from tradepartner.backtest.provider import GapReading

        variant = engine._Variant(settings=_params(), handle=_handle())
        variant.plan = engine.Plan(
            session=T0,
            fill_session=T1,
            targets={"PRE": 1.0},
            n_universe=1,
            n_static_listings=0,
            n_excluded_no_history=0,
            gap=GapReading(count_share=0.0, size_share=0.0),
            members=("PRE",),
            scores={"PRE": 1.0},
            excluded_no_history=(),
        )
        variant.books = [engine._Book(level=0.0, cash=0.0)]
        book = variant.books[0]
        book.positions = {"PRE": 1234.0}
        book.marked_at = {"PRE": T0}
        book.held_since = {"PRE": T0}
        book.entries = {"PRE": 1}
        book.held_on = {"PRE": {T0}}
        variant.last_stitched = {"PRE": T0}
        variant.targets = {T1: {"PRE": 1.0}}
        return variant

    def test_a_held_predecessor_is_renamed_to_its_successor(self) -> None:
        variant = self._variant()
        engine._rename_chained(variant, (("PRE", "POST"),))
        book = variant.books[0]
        assert book.positions == {"POST": 1234.0}  # one share for one share
        assert book.marked_at == {"POST": T0}
        assert book.held_since == {"POST": T0}
        assert book.entries == {"POST": 1}
        assert book.held_on == {"POST": {T0}}
        assert variant.plan is not None and variant.plan.targets == {"POST": 1.0}
        assert variant.targets == {T1: {"POST": 1.0}}
        assert variant.last_stitched == {"POST": T0}
        assert book.chained_positions == 1

    def test_the_count_lands_on_the_rebalance_row_then_resets(self) -> None:
        variant = self._variant()
        book = variant.books[0]
        engine._rename_chained(variant, (("PRE", "POST"),))
        plan = cast(engine.Plan, variant.plan)
        counts = engine._rebalance_counts(plan, book)
        assert counts["n_chained_positions"] == 1
        assert book.chained_positions == 0
        # A step with no chain writes the plan's counts unchanged (no extra row).
        assert engine._rebalance_counts(plan, book) is plan.counts

    def test_the_map_is_idempotent(self) -> None:
        variant = self._variant()
        engine._rename_chained(variant, (("PRE", "POST"),))
        engine._rename_chained(variant, (("PRE", "POST"),))
        assert variant.books[0].positions == {"POST": 1234.0}
        assert variant.books[0].chained_positions == 1  # not counted twice

    def test_a_collision_sums_the_values(self) -> None:
        variant = self._variant()
        variant.books[0].positions = {"PRE": 100.0, "POST": 25.0}
        engine._rename_chained(variant, (("PRE", "POST"),))
        assert variant.books[0].positions == {"POST": 125.0}

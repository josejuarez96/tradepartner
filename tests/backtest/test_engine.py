"""Engine loop, fills, costs and cash on the fake provider (backtest spec reqs 1-6, T37b)."""

from __future__ import annotations

import ast
import itertools
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from tradepartner.backtest.costs import Commissions, trade_cost
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.fills import apply_trades
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.store import registry

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
    return run(params or _params(), provider, T0, end, _handle(), levels)


def _equity(result: BacktestResult) -> dict[date, float]:
    return {row.session: row.equity for row in result.equity}


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


class TestHandle:
    @pytest.mark.parametrize("handle", [None, 1, "1"])
    def test_no_handle_raises_before_any_call(self, handle: object) -> None:
        provider = _provider()
        with pytest.raises(TypeError, match="TrialHandle"):
            run(_params(), provider, T0, T3, handle, [15.0])  # type: ignore[arg-type]
        assert provider.calls == []

    def test_a_window_with_one_rebalance_is_refused_before_any_call(self) -> None:
        provider = _provider()
        with pytest.raises(ValueError, match="two rebalance sessions"):
            run(_params(), provider, T0, date(2024, 2, 28), _handle(), [15.0])
        assert provider.calls == []

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
        assert (row.n_universe, row.n_targets, row.n_excluded_no_history) == (4, 2, 0)
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

"""Hand-computed long/short parity cases through the backtest engine (issue #2166, on T223).

Two or three names and a few sessions each. Every expected number is written out by hand
in the comment above it; the zero-cost, constant-price fixtures make the arithmetic exact.
Plans are supplied by hand (`test_engine_short._install_plan`). Zero costs and flat price
paths between the named steps keep each ledger a handful of products.

Calendar facts the hand numbers lean on: T0 = 2024-01-31 fills at F0 = 2024-02-01; the
February 2024 sessions are 1, 2, 5-9, 12-16, 20-23 and 26-29 (Presidents' Day is closed),
so `F0..T1` is 20 sessions, the last 18 of them from Monday 2024-02-05.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from backtest import test_engine, test_engine_short
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.config import Settings

T0, T1, T2 = test_engine_short.T0, test_engine_short.T1, test_engine_short.T2
F0, F1 = test_engine_short.F0, test_engine_short.F1
H = test_engine_short.HISTORY_START
FEB2, FEB5, FEB6 = date(2024, 2, 2), date(2024, 2, 5), date(2024, 2, 6)
CAPITAL = test_engine_short.CAPITAL


def _params(
    *,
    borrow: float = 0.0,
    sensitivity: tuple[float, ...] = (),
    cover: float = 1.0,
    stale: int | None = None,
) -> Settings:
    """Zero-cost signed settings; `cover` is the drift cover's short share of equity."""
    costs = {
        "per_side_bps": 0.0,
        "sensitivity_per_side_bps": [0.0],
        "borrow_bps_per_year": borrow,
        "borrow_sensitivity_bps": list(sensitivity),
    }
    base = test_engine_short._signed(costs=costs)
    update: dict[str, Any] = {
        "portfolio": base.portfolio.model_copy(update={"short_cover_weight": cover})
    }
    if stale is not None:
        update["backtest"] = base.backtest.model_copy(update={"stale_exit_sessions": stale})
    return base.model_copy(update=update)


def _go(
    monkeypatch: pytest.MonkeyPatch,
    targets: dict[date, dict[str, float]],
    bars: pl.DataFrame,
    params: Settings,
    end: date,
) -> BacktestResult:
    test_engine_short._install_plan(monkeypatch, targets)
    return run(
        params,
        test_engine_short._provider(bars),
        T0,
        end,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]


def _series(result: BacktestResult, name: str) -> dict[date, float]:
    return {r.session: r.equity for r in result.equity if r.series == name}


# --- signed fills and marks, and the gross/net exposure rows -----------------------------


def test_three_name_signed_fills_marks_and_exposure_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long 40 %, B short 30 %, C short 30 % of 100,000 at F0, no costs.

    Prices: A 100 -> 110 and B 50 -> 55 on Feb 2; C 20 -> 18 on Feb 5.
    Shares: A 400 (40,000 / 100), B 600 (30,000 / 50), C 1,500 (30,000 / 20).
    Cash at F0: 100,000 - 40,000 + 30,000 + 30,000 = 120,000 (shorts add their proceeds).
    F0:   values A +40,000, B -30,000, C -30,000, equity 100,000, gross 1.0, net -0.2.
    Feb 2: A +44,000, B -33,000, C -30,000; equity 120,000 + 44,000 - 33,000 - 30,000 =
           101,000; gross (44 + 33 + 30) / 101 = 107/101; net (44 - 33 - 30) / 101 = -19/101.
    Feb 5: C -27,000; equity 120,000 + 44,000 - 33,000 - 27,000 = 104,000; gross
           (44 + 33 + 27) / 104 = 1.0; net (44 - 33 - 27) / 104 = -16/104. Flat to T1.
    The summary rows are the T0 seed (0, 0), F0, Feb 2 and 18 sessions from Feb 5 (21 in all).
    """
    bars = test_engine_short._bars(
        {
            "A": [(H, 100.0), (FEB2, 110.0)],
            "B": [(H, 50.0), (FEB2, 55.0)],
            "C": [(H, 20.0), (FEB5, 18.0)],
        }
    )
    result = _go(monkeypatch, {T0: {"A": 0.4, "B": -0.3, "C": -0.3}}, bars, _params(), T1)
    value, cash = test_engine_short._value, test_engine_short._cash(result)
    assert (value(result, "A", F0), value(result, "B", F0), value(result, "C", F0)) == (
        pytest.approx(40_000.0),
        pytest.approx(-30_000.0),
        pytest.approx(-30_000.0),
    )
    assert cash[F0] == pytest.approx(120_000.0)
    assert (value(result, "A", FEB2), value(result, "B", FEB2)) == (
        pytest.approx(44_000.0),
        pytest.approx(-33_000.0),
    )
    assert value(result, "C", FEB2) == pytest.approx(-30_000.0)
    assert value(result, "C", FEB5) == pytest.approx(-27_000.0)
    equity = test_engine_short._equity(result)
    assert equity[F0] == pytest.approx(100_000.0)
    assert equity[FEB2] == pytest.approx(101_000.0)
    assert equity[FEB5] == pytest.approx(104_000.0)
    assert equity[T1] == pytest.approx(104_000.0)
    summary = result.exposure_summary
    assert summary is not None
    assert len(test_engine_short._steps(F0, T1)) == 20
    gross_rows = [1.0, 107 / 101, *[1.0] * 18]
    net_rows = [-0.2, -19 / 101, *[-16 / 104] * 18]
    assert summary.gross_max == pytest.approx(107 / 101)
    assert summary.net_min == pytest.approx(-0.2)  # F0 is below Feb 2's -19/101 = -0.188
    assert summary.net_max == 0.0  # the T0 seed row
    # Means divide by 21: the seeded (0, 0) T0 row is one of the rows.
    assert summary.gross_mean == pytest.approx(sum(gross_rows) / 21)
    assert summary.net_mean == pytest.approx(sum(net_rows) / 21)
    assert summary.borrow_paid == 0.0


def test_zero_exposure_row_at_the_first_rebalance_enters_the_means(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One flat short of 50 % (the #2152 quant-auditor nit): the book seeds (0, 0) at T0.

    S short 50,000: cash 150,000, equity 100,000, gross 0.5, net -0.5 on each of 20 sessions.
    gross_mean = 0.5 * 20 / 21, net_mean = -0.5 * 20 / 21, net_max = 0 (the T0 row).
    """
    bars = test_engine_short._bars({"S": [(H, 100.0)]})
    result = _go(monkeypatch, {T0: {"S": -0.5}}, bars, _params(), T1)
    summary = result.exposure_summary
    assert summary is not None
    assert summary.gross_max == pytest.approx(0.5)
    assert summary.net_min == pytest.approx(-0.5)
    assert summary.net_max == 0.0
    assert summary.gross_mean == pytest.approx(0.5 * 20 / 21)
    assert summary.net_mean == pytest.approx(-0.5 * 20 / 21)


# --- borrow cost at 0 / 30 / 300 bp -----------------------------------------------------


def test_borrow_accrual_at_0_30_and_300_bp(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long 50 %, B short 50 %, flat prices, no costs, F0 = Feb 1 to T1 = Feb 29.

    B is short 50,000 from the F0 close and nothing was held at close(T0), so the fee runs
    over the calendar days from Feb 1 to Feb 29 = 28 days on 50,000 (the short is flat).
    Fee = 50,000 * bp / 10,000 / 360 * 28:
      0 bp   -> 0
      30 bp  -> 50,000 * 0.003 / 360 * 28 = 150 * 28 / 360 = 11.666...
      300 bp -> 50,000 * 0.030 / 360 * 28 = 1,500 * 28 / 360 = 116.666...
    Equity at T1 is 100,000 less the fee (flat prices, zero trade costs).
    """
    bars = test_engine_short._bars({"A": [(H, 100.0)], "B": [(H, 100.0)]})
    result = _go(
        monkeypatch,
        {T0: {"A": 0.5, "B": -0.5}},
        bars,
        _params(borrow=0.0, sensitivity=(0.0, 30.0, 300.0)),
        T1,
    )
    for rate, fee in ((0.0, 0.0), (30.0, 150 * 28 / 360), (300.0, 1_500 * 28 / 360)):
        books = _series(result, f"strategy_borrow_{rate:g}bp")
        assert books[F0] == pytest.approx(CAPITAL)
        assert books[T1] == pytest.approx(CAPITAL - fee, abs=1e-9), rate
    headline = run(
        _params(borrow=300.0),
        test_engine_short._provider(bars),
        T0,
        T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert headline.exposure_summary is not None
    assert headline.exposure_summary.borrow_paid == pytest.approx(1_500 * 28 / 360, abs=1e-9)
    assert test_engine_short._equity(headline)[T1] == pytest.approx(
        CAPITAL - 1_500 * 28 / 360, abs=1e-9
    )


# --- a long-to-short reversal on one name ------------------------------------------------


def test_long_to_short_reversal_on_one_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """S is long 50 % at T0 and short 50 % at T1, zero costs.

    S 100 until Feb 15, 110 from Feb 15, 99 from Mar 4.
    F0: buy 500 shares (50,000 / 100): cash 50,000, S +50,000, equity 100,000.
    Feb 15 on: S 500 * 110 = +55,000, equity 105,000 (cash 50,000).
    F1 (Mar 1, price 110): equity 105,000, target -52,500. Sell the 500 shares for 55,000
      (cash 105,000), short 52,500 / 110 = 477.27... shares for 52,500 (cash 157,500).
      S is -52,500, equity 105,000: the flip goes through zero in one fill.
    Mar 4: S 99 / 110 = 0.9 of the short's value: -52,500 * 0.9 = -47,250;
      equity 157,500 - 47,250 = 110,250 (the short gained 5,250). Flat to T2.
    """
    bars = test_engine_short._bars(
        {"S": [(H, 100.0), (date(2024, 2, 15), 110.0), (date(2024, 3, 4), 99.0)]}
    )
    result = _go(monkeypatch, {T0: {"S": 0.5}, T1: {"S": -0.5}}, bars, _params(), T2)
    value, cash = test_engine_short._value, test_engine_short._cash(result)
    equity = test_engine_short._equity(result)
    assert value(result, "S", F0) == pytest.approx(50_000.0)
    assert value(result, "S", T1) == pytest.approx(55_000.0)
    assert equity[T1] == pytest.approx(105_000.0)
    assert value(result, "S", F1) == pytest.approx(-52_500.0)
    assert cash[F1] == pytest.approx(157_500.0)
    assert equity[F1] == pytest.approx(105_000.0)
    assert value(result, "S", date(2024, 3, 4)) == pytest.approx(-47_250.0)
    assert equity[date(2024, 3, 4)] == pytest.approx(110_250.0)
    assert equity[T2] == pytest.approx(110_250.0)


# --- a drift cover, and a stale (forced) cover --------------------------------------------


def test_drift_cover_at_next_session_with_hand_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long 50 %, B short 50 %, cover weight 0.6; B 100 -> 120 on Feb 2 -> 125 on Feb 5.

    F0: cash 100,000 - 50,000 (A) + 50,000 (B proceeds) = 100,000; A +50,000, B -50,000;
      equity 100,000, gross 1.0, net 0.
    Feb 2: B -50,000 * 1.2 = -60,000; equity 100,000 + 50,000 - 60,000 = 90,000; the short is
      60,000 / 90,000 = 0.667 > 0.6, so it is covered at the next session's fill price.
    Feb 5: B covered at 125: -62,500 leaves the book, cash 100,000 - 62,500 = 37,500, equity
      37,500 + 50,000 = 87,500; B is 0, A +50,000 only: gross = net = 50 / 87.5.
    Exposure rows: T0 seed 0; F0 gross 1, net 0; Feb 2 gross 110/90, net -10/90; then the
    18 sessions Feb 5 .. Feb 29 at 50/87.5 (21 rows).
    """
    bars = test_engine_short._bars(
        {"A": [(H, 100.0)], "B": [(H, 100.0), (FEB2, 120.0), (FEB5, 125.0)]}
    )
    result = _go(monkeypatch, {T0: {"A": 0.5, "B": -0.5}}, bars, _params(cover=0.6), T1)
    equity = test_engine_short._equity(result)
    assert equity[F0] == pytest.approx(100_000.0)
    assert equity[FEB2] == pytest.approx(90_000.0)
    assert test_engine_short._value(result, "B", FEB2) == pytest.approx(-60_000.0)
    assert test_engine_short._value(result, "B", FEB5) == 0.0
    assert test_engine_short._cash(result)[FEB5] == pytest.approx(37_500.0)
    assert equity[FEB5] == pytest.approx(87_500.0)
    assert equity[T1] == pytest.approx(87_500.0)
    assert result.rebalances[0].counts["n_drift_covers"] == 1
    summary = result.exposure_summary
    assert summary is not None
    assert summary.gross_max == pytest.approx(110 / 90)
    assert summary.net_min == pytest.approx(-10 / 90)
    assert summary.net_max == pytest.approx(50 / 87.5)
    assert summary.gross_mean == pytest.approx((0 + 1 + 110 / 90 + 18 * 50 / 87.5) / 21)
    assert summary.net_mean == pytest.approx((0 + 0 - 10 / 90 + 18 * 50 / 87.5) / 21)


def test_stale_forced_cover_at_last_mark_with_borrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long 50 %, B short 50 %, borrow 360 bp (so $1 per 10,000 per day), cover weight 1.

    B 100, then 120 from Feb 5; its last bar is Feb 6, so the six sessions after it exceed
    `stale_exit_sessions` (5) and the short is covered at its last mark, Feb 6, at 120.
    Fees on the previous close's short: Feb 2 1 day * 50,000 = 5; Feb 5 3 days * 50,000
    (Feb 2 close) = 15; Feb 6 1 day * 60,000 (Feb 5 close) = 6; total 26.
    Feb 5: cash 100,000 - 5 - 15 = 99,980, B -60,000, A +50,000: equity 89,980,
      gross 110,000 / 89,980.
    Feb 6: cash 99,980 - 6 - 60,000 (the cover) = 39,974; equity 39,974 + 50,000 = 89,974.
    """
    bars = test_engine_short._bars({"A": [(H, 100.0)], "B": [(H, 100.0), (FEB5, 120.0)]}).filter(
        ~((pl.col("security_id") == "B") & (pl.col("session") > FEB6))
    )
    result = _go(monkeypatch, {T0: {"A": 0.5, "B": -0.5}}, bars, _params(borrow=360.0), T1)
    equity = test_engine_short._equity(result)
    assert equity[FEB2] == pytest.approx(99_995.0)
    assert equity[FEB5] == pytest.approx(89_980.0)
    assert test_engine_short._value(result, "B", FEB5) == pytest.approx(-60_000.0)
    assert test_engine_short._value(result, "B", FEB6) == 0.0
    assert equity[FEB6] == pytest.approx(89_974.0)
    assert equity[T1] == pytest.approx(89_974.0)
    assert result.rebalances[0].n_stale_exits == 1
    summary = result.exposure_summary
    assert summary is not None
    assert summary.borrow_paid == pytest.approx(26.0)
    assert summary.gross_max == pytest.approx(110_000 / 89_980)


# --- #2152 quant-auditor follow-up: a halt that resumes after the step ends -----------------


def test_halted_short_cover_waits_across_the_step_and_fills_at_the_resume_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long 50 %, B short 50 %, cover weight 0.6, stale exit raised to 40 sessions.

    B 100, 160 on Feb 2, then no bars until it resumes at 140 on Mar 5 (after T1 = Feb 29
    and F1 = Mar 1). Both plans after T0 are empty.
    Feb 2: B -80,000, equity 100,000 + 50,000 - 80,000 = 70,000; 80 / 70 > 0.6 -> a cover
      is pending. No bar follows, so B keeps its last mark -80,000 through the step end
      (equity 70,000) and the cover carries out of the step.
    F1 (Mar 1): A is sold for 50,000 (cash 150,000); B has no bar, so it cannot fill and
      still marks -80,000; equity 70,000 through Mar 4.
    Mar 5: B resumes at 140, so the cover fills at 140 / 160 of the carried value:
      -80,000 * 140 / 160 = -70,000; cash 150,000 - 70,000 = 80,000; equity 80,000.
    """
    resume = date(2024, 3, 5)
    bars = test_engine_short._bars(
        {"A": [(H, 100.0)], "B": [(H, 100.0), (FEB2, 160.0), (resume, 140.0)]}
    ).filter(
        ~(
            (pl.col("security_id") == "B")
            & (pl.col("session") > FEB2)
            & (pl.col("session") < resume)
        )
    )
    result = _go(
        monkeypatch,
        {T0: {"A": 0.5, "B": -0.5}, T1: {}, T2: {}},
        bars,
        _params(cover=0.6, stale=40),
        T2,
    )
    equity = test_engine_short._equity(result)
    assert equity[FEB2] == pytest.approx(70_000.0)
    assert equity[T1] == pytest.approx(70_000.0)
    assert test_engine_short._value(result, "B", T1) == pytest.approx(-80_000.0)
    assert result.rebalances[0].counts["n_drift_covers"] == 0
    assert test_engine_short._value(result, "B", date(2024, 3, 4)) == pytest.approx(-80_000.0)
    assert equity[date(2024, 3, 4)] == pytest.approx(70_000.0)
    assert test_engine_short._value(result, "B", resume) == 0.0
    assert test_engine_short._cash(result)[resume] == pytest.approx(80_000.0)
    assert equity[resume] == pytest.approx(80_000.0)
    assert equity[T2] == pytest.approx(80_000.0)
    assert result.rebalances[1].counts["n_drift_covers"] == 1

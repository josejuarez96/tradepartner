"""Tests for `tradepartner.backtest.fills.apply_trades` with the cost terms (plan T210;
backtest spec amendments 2026-10-18 #1731 "Cost terms" and #1792).

`apply_trades` takes `spread_bps`, `impact_coeff` (keyword-only, default 0.0) and `adv`
(a mapping of `security_id` to ADV in dollars, default empty) and threads them to its
four `trade_cost` / `buy_notional_after_costs` calls: the sells, the trim check, the
buy sizing and the skipped-buy check. At the zero defaults the result is today's to the
last bit, so every `engine.py` call stays valid untouched (the engine wiring is T210c).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.fills import FillResult, Trade, apply_trades

F = date(2024, 3, 1)  # the fill session (a Friday, an XNYS session)
NO_COMMISSIONS = Commissions(per_share=0.0, per_order=0.0)


def _frame(prices: dict[str, float]) -> pl.DataFrame:
    """One `close` bar per name on the fill session."""
    return pl.DataFrame(
        {
            "security_id": list(prices),
            "session": [F] * len(prices),
            "close": list(prices.values()),
        },
        schema={"security_id": pl.Utf8, "session": pl.Date, "close": pl.Float64},
    )


def _fill(
    positions: dict[str, float],
    cash: float,
    targets: dict[str, float],
    prices: dict[str, float],
    *,
    per_side_bps: float = 15.0,
    commissions: Commissions = NO_COMMISSIONS,
    raw: dict[str, float] | None = None,
    **terms: Any,
) -> FillResult:
    return apply_trades(
        positions,
        cash,
        targets,
        _frame(prices),
        _frame(raw if raw is not None else prices),
        F,
        fill_price="close",
        per_side_bps=per_side_bps,
        commissions=commissions,
        **terms,
    )


def test_signed_short_open_rise_cover_and_flip_hand_ledger() -> None:
    opened = _fill(
        {}, 100.0, {"S": -0.5, "L": 0.5}, {"S": 10.0, "L": 10.0}, per_side_bps=0, signed=True
    )
    assert opened.positions == pytest.approx({"S": -50.0, "L": 50.0}, abs=1e-9)
    assert opened.cash == pytest.approx(100.0, abs=1e-9)
    assert [t.notional for t in opened.trades] == pytest.approx([-50.0, 50.0], abs=1e-9)
    # S rises 10%: short value -55, cash 100, long value 50, equity 95.
    covered = _fill(
        {"S": -55.0, "L": 50.0},
        100.0,
        {"L": 1.0},
        {"S": 11.0, "L": 10.0},
        per_side_bps=0,
        signed=True,
    )
    assert covered.positions == pytest.approx({"L": 95.0}, abs=1e-9)
    assert covered.cash == pytest.approx(0.0, abs=1e-9)
    assert [t.notional for t in covered.trades] == pytest.approx([55.0, 45.0], abs=1e-9)
    flipped = _fill({"S": -55.0}, 150.0, {"S": 0.5}, {"S": 11.0}, per_side_bps=0, signed=True)
    assert flipped.positions == pytest.approx({"S": 47.5}, abs=1e-9)
    assert flipped.cash == pytest.approx(47.5, abs=1e-9)
    assert [t.notional for t in flipped.trades] == pytest.approx([55.0, 47.5], abs=1e-9)


def test_signed_long_buy_uses_only_free_cash_after_missed_sell() -> None:
    result = _fill(
        {"M": 90.0},
        10.0,
        {"S": -0.5, "L": 0.5},
        {"S": 10.0, "L": 10.0},
        per_side_bps=0,
        signed=True,
    )
    assert result.missing == ("M",)
    assert result.positions == pytest.approx({"M": 90.0, "S": -50.0, "L": 10.0}, abs=1e-9)
    assert result.cash == pytest.approx(50.0, abs=1e-9)
    assert result.cash - abs(result.positions["S"]) == pytest.approx(0.0, abs=1e-9)


def test_signed_long_to_short_flip_has_two_trades() -> None:
    result = _fill({"S": 50.0}, 50.0, {"S": -0.5}, {"S": 10.0}, per_side_bps=0, signed=True)
    assert result.positions == pytest.approx({"S": -50.0}, abs=1e-9)
    assert result.cash == pytest.approx(150.0, abs=1e-9)
    assert [t.notional for t in result.trades] == pytest.approx([-50.0, -50.0], abs=1e-9)


def test_signed_short_open_and_cover_each_pay_cost() -> None:
    opened = _fill({}, 100.0, {"S": -0.5}, {"S": 10.0}, per_side_bps=100, signed=True)
    assert opened.positions == pytest.approx({"S": -50.0}, abs=1e-9)
    assert opened.cash == pytest.approx(149.5, abs=1e-9)
    assert opened.cost_paid == pytest.approx(0.5, abs=1e-9)
    covered = _fill(opened.positions, opened.cash, {}, {"S": 10.0}, per_side_bps=100, signed=True)
    assert covered.positions == {}
    assert covered.cash == pytest.approx(99.0, abs=1e-9)
    assert covered.cost_paid == pytest.approx(0.5, abs=1e-9)


def test_signed_cover_refuses_debit_balance_after_missed_sell() -> None:
    # Equity is 100, but M has no fill bar and covering S needs 150 from cash of 50.
    with pytest.raises(ValueError, match="covers on 2024-03-01"):
        _fill(
            {"M": 200.0, "S": -150.0},
            50.0,
            {"L": 1.0},
            {"S": 10.0, "L": 10.0},
            per_side_bps=0,
            signed=True,
        )


# --- zero terms are today's fill to the last bit -----------------------------------

#: A sell (A leaves the targets, sold whole), a trim (B from 40% to 30%), two buys (C, D)
#: and a skipped buy (E: $0.50 of target under a $1 per-order commission).
_POSITIONS = {"A": 6_000.0, "B": 4_000.0}
_TARGETS = {"B": 0.3, "C": 0.4, "D": 0.2, "E": 0.00005}
_PRICES = {"A": 60.0, "B": 40.0, "C": 25.0, "D": 125.0, "E": 10.0}
_RAW = {"A": 120.0, "B": 40.0, "C": 50.0, "D": 125.0, "E": 10.0}
_ADV = {"A": 2e5, "B": 1e5, "C": 3e5, "D": 5e4, "E": 1e3}


@pytest.mark.parametrize("per_side_bps", [0.0, 15.0, 100.0])
@pytest.mark.parametrize(
    "commissions",
    [NO_COMMISSIONS, Commissions(per_share=0.005, per_order=1.0)],
    ids=["no-commissions", "commissions"],
)
@pytest.mark.parametrize("cash", [0.0, 1_234.5])
def test_zero_terms_are_todays_fill_to_the_last_bit(
    per_side_bps: float, commissions: Commissions, cash: float
) -> None:
    """`==`, not approx, on the whole `FillResult`: positions, cash, cost, turnover,
    every trade and the missing names, whatever ADV map is passed (or none)."""
    common: dict[str, Any] = {"per_side_bps": per_side_bps, "commissions": commissions}
    plain = _fill(_POSITIONS, cash, _TARGETS, _PRICES, raw=_RAW, **common)
    named = _fill(
        _POSITIONS,
        cash,
        _TARGETS,
        _PRICES,
        raw=_RAW,
        spread_bps=0.0,
        impact_coeff=0.0,
        adv=_ADV,
        **common,
    )
    assert named == plain
    no_adv = _fill(
        _POSITIONS, cash, _TARGETS, _PRICES, raw=_RAW, spread_bps=0.0, impact_coeff=0.0, **common
    )
    assert no_adv == plain
    # The fixture exercises what it claims: a whole sell, a trim, two buys, E skipped.
    sold = {t.security_id for t in plain.trades if t.notional < 0}
    bought = {t.security_id for t in plain.trades if t.notional > 0}
    assert sold == {"A", "B"}
    assert {"C", "D"} <= bought
    if commissions.per_order > 0:
        assert "E" not in bought


# --- the terms by hand --------------------------------------------------------------


def test_spread_and_impact_on_a_sell_and_a_buy_by_hand() -> None:
    """15 bp + 5 bp spread, impact_coeff 0.1, ADV $900_000 for both names.

    Sell A whole: 9_000 * 20 / 10_000 = 18, impact 9_000 * 0.1 * sqrt(9_000 / 900_000)
    = 90, cost 108; cash 216 + 9_000 - 108 = 9_108. Buy B: target 100% of equity 9_216,
    capped at cash 9_108, sized so N + 0.002 N + 0.1 N sqrt(N / 900_000) = 9_108, so
    N = 9_000 (18 + 90 = 108 again); cash 0. Turnover 18_000 / (2 * 9_216)."""
    result = _fill(
        {"A": 9_000.0},
        216.0,
        {"B": 1.0},
        {"A": 90.0, "B": 45.0},
        spread_bps=5.0,
        impact_coeff=0.1,
        adv={"A": 9e5, "B": 9e5},
    )
    sell, buy = result.trades
    assert (sell.security_id, sell.notional, sell.fill_price) == ("A", -9_000.0, 90.0)
    assert sell.cost == pytest.approx(108.0, rel=1e-12)
    assert buy.security_id == "B"
    assert buy.notional == pytest.approx(9_000.0, rel=1e-9)
    assert buy.cost == pytest.approx(108.0, rel=1e-9)
    assert result.cost_paid == pytest.approx(216.0, rel=1e-9)
    assert result.cash >= 0.0
    assert result.cash == pytest.approx(0.0, abs=1e-6)
    assert list(result.positions) == ["B"]
    assert result.positions["B"] == pytest.approx(9_000.0, rel=1e-9)
    assert result.turnover == pytest.approx(18_000.0 / (2 * 9_216.0), rel=1e-9)


def test_the_spread_alone_is_a_higher_per_side_level() -> None:
    """At 15 bp + 5 bp spread every trade costs what it costs at 20 bp without it."""
    spread = _fill(_POSITIONS, 0.0, _TARGETS, _PRICES, raw=_RAW, spread_bps=5.0)
    shifted = _fill(_POSITIONS, 0.0, _TARGETS, _PRICES, raw=_RAW, per_side_bps=20.0)
    assert [t.security_id for t in spread.trades] == [t.security_id for t in shifted.trades]
    for ours, theirs in zip(spread.trades, shifted.trades, strict=True):
        assert ours.notional == pytest.approx(theirs.notional, rel=1e-12)
        assert ours.cost == pytest.approx(theirs.cost, rel=1e-12)
    assert spread.cash == pytest.approx(shifted.cash, rel=1e-12, abs=1e-9)


def test_impact_reaches_the_trim_check() -> None:
    """A $1_000 trim of A against an ADV of $0.10: impact 1_000 * 0.1 * sqrt(10_000) =
    10_000, at least what it raises, so the trim is not an order (and B's buy has no
    cash). At impact 0 the same trim is made."""
    positions, targets = {"A": 5_000.0, "B": 5_000.0}, {"A": 0.4, "B": 0.6}
    prices = {"A": 50.0, "B": 50.0}
    plain = _fill(positions, 0.0, targets, prices)
    assert plain.trades[0].security_id == "A"
    assert plain.trades[0].notional == pytest.approx(-1_000.0)
    impacted = _fill(positions, 0.0, targets, prices, impact_coeff=0.1, adv={"A": 0.1, "B": 1e9})
    assert impacted.trades == ()
    assert impacted.positions == positions
    assert impacted.cash == 0.0


@pytest.mark.parametrize("coeff", [0.0, 0.01, 0.1, 0.5])
def test_cash_never_goes_negative_under_impact(coeff: float) -> None:
    result = _fill(
        _POSITIONS,
        500.0,
        _TARGETS,
        _PRICES,
        raw=_RAW,
        commissions=Commissions(per_share=0.005, per_order=0.35),
        spread_bps=5.0,
        impact_coeff=coeff,
        adv=_ADV,
    )
    assert result.cash >= 0.0
    assert all(isinstance(t, Trade) and t.cost >= 0.0 for t in result.trades)


@pytest.mark.parametrize(
    ("adv", "name"),
    [({"A": 9e5}, "B"), ({"B": 9e5}, "A"), ({}, "A")],
    ids=["buy-without-adv", "sell-without-adv", "empty-map"],
)
def test_a_traded_name_without_adv_raises_under_a_non_zero_impact(
    adv: dict[str, float], name: str
) -> None:
    """No ADV is never a silent zero impact (spec amendment #1792, item 3)."""
    with pytest.raises(ValueError, match=rf"\b{name}\b"):
        _fill({"A": 9_000.0}, 216.0, {"B": 1.0}, {"A": 90.0, "B": 45.0}, impact_coeff=0.1, adv=adv)


def test_without_adv_a_zero_impact_fills_as_today() -> None:
    plain = _fill({"A": 9_000.0}, 216.0, {"B": 1.0}, {"A": 90.0, "B": 45.0})
    assert _fill({"A": 9_000.0}, 216.0, {"B": 1.0}, {"A": 90.0, "B": 45.0}, adv={}) == plain

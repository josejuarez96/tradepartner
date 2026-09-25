"""Tests for `tradepartner.backtest.costs` (spec reqs 4 and 6, ADR 0004, plan T33).

The three-trade fixture is checked against values computed by hand, one cost
component at a time and then all together, as ADR 0004 requires: `bt` models costs
differently, so the oracle runs at zero cost and the cost model is tested here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tradepartner.backtest.costs import (
    Commissions,
    buy_notional_after_costs,
    sensitivity_levels,
    trade_cost,
)
from tradepartner.config import Settings

COSTS_PY = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "backtest" / "costs.py"

# (notional, shares): a whole-share buy, a whole-share sell, a fractional buy.
THREE_TRADES = [(10_000.0, 100.0), (2_500.0, 50.0), (333.33, 3.3333)]

# Hand-computed per trade: notional * bps / 10_000, shares * per_share, per_order.
BPS_ONLY = [15.0, 3.75, 0.499995]  # at 15 bp
PER_SHARE_ONLY = [0.5, 0.25, 0.0166665]  # at $0.005 per share
PER_ORDER_ONLY = [1.0, 1.0, 1.0]  # at $1 per order
ALL_THREE = [16.5, 5.0, 1.5166615]


def _settings(**costs: object) -> Settings:
    return Settings(_env_file=None, costs=costs)


@pytest.mark.parametrize(
    ("per_side_bps", "commissions", "expected"),
    [
        (15.0, Commissions(per_share=0.0, per_order=0.0), BPS_ONLY),
        (0.0, Commissions(per_share=0.005, per_order=0.0), PER_SHARE_ONLY),
        (0.0, Commissions(per_share=0.0, per_order=1.0), PER_ORDER_ONLY),
        (15.0, Commissions(per_share=0.005, per_order=1.0), ALL_THREE),
    ],
    ids=["bps", "per_share", "per_order", "all"],
)
def test_three_trade_fixture_matches_hand_computed_costs(
    per_side_bps: float, commissions: Commissions, expected: list[float]
) -> None:
    got = [trade_cost(n, s, per_side_bps, commissions) for n, s in THREE_TRADES]
    assert got == pytest.approx(expected, rel=1e-12)
    assert sum(got) == pytest.approx(sum(expected), rel=1e-12)


def test_zero_cost_is_the_identity() -> None:
    zero = Commissions(per_share=0.0, per_order=0.0)
    assert [trade_cost(n, s, 0.0, zero) for n, s in THREE_TRADES] == [0.0, 0.0, 0.0]
    assert buy_notional_after_costs(12_345.67, 0.0, zero, price=41.0) == 12_345.67


def test_no_trade_costs_nothing_even_with_a_per_order_commission() -> None:
    """A zero trade is not an order, so no per-order commission is charged."""
    assert trade_cost(0.0, 0.0, 15.0, Commissions(per_share=0.005, per_order=1.0)) == 0.0


@pytest.mark.parametrize(("notional", "shares"), [(-1.0, 1.0), (1.0, -1.0)])
def test_trade_cost_rejects_negative_sizes(notional: float, shares: float) -> None:
    """Callers pass traded magnitudes; a sign here would turn a cost into a credit."""
    with pytest.raises(ValueError, match="non-negative"):
        trade_cost(notional, shares, 15.0, Commissions(per_share=0.0, per_order=0.0))


def test_buy_notional_scaled_by_one_over_one_plus_rate() -> None:
    """Spec req 4: with no commissions, notional = cash / (1 + per-side rate)."""
    zero = Commissions(per_share=0.0, per_order=0.0)
    got = buy_notional_after_costs(10_015.0, 15.0, zero, price=50.0)
    assert got == pytest.approx(10_000.0, rel=1e-12)


def test_buy_notional_with_commissions_hand_computed() -> None:
    """N + N*0.0015 + (N/50)*0.005 + 1 = 10_000  =>  N = 9_999 / 1.0016."""
    c = Commissions(per_share=0.005, per_order=1.0)
    got = buy_notional_after_costs(10_000.0, 15.0, c, price=50.0)
    assert got == pytest.approx(9_999.0 / 1.0016, rel=1e-12)


@pytest.mark.parametrize("cash", [0.0, 0.5, 1.0])
def test_buy_notional_zero_when_cash_does_not_cover_the_order_fee(cash: float) -> None:
    c = Commissions(per_share=0.0, per_order=1.0)
    assert buy_notional_after_costs(cash, 15.0, c, price=50.0) == 0.0


@pytest.mark.parametrize("cash", [1e-6, 0.1, 1.0, 999.99, 12_345.678901, 1e5, 3.3e7])
@pytest.mark.parametrize("price", [0.37, 5.0, 123.456, 4_321.0])
@pytest.mark.parametrize(
    "commissions",
    [Commissions(per_share=0.0, per_order=0.0), Commissions(per_share=0.005, per_order=0.35)],
)
def test_post_cost_cash_never_negative_at_the_ladder_maximum(
    cash: float, price: float, commissions: Commissions
) -> None:
    top = max(sensitivity_levels(Settings(_env_file=None)))
    notional = buy_notional_after_costs(cash, top, commissions, price=price)
    assert notional >= 0.0
    remaining = cash - notional - trade_cost(notional, notional / price, top, commissions)
    assert remaining >= 0.0 or notional == 0.0


def test_buy_notional_rejects_bad_inputs() -> None:
    zero = Commissions(per_share=0.0, per_order=0.0)
    with pytest.raises(ValueError):
        buy_notional_after_costs(-1.0, 15.0, zero, price=10.0)
    with pytest.raises(ValueError):
        buy_notional_after_costs(100.0, 15.0, zero, price=0.0)


def test_levels_sorted_and_include_the_base() -> None:
    assert sensitivity_levels(Settings(_env_file=None)) == [0.0, 15.0, 30.0, 60.0, 100.0]


def test_levels_deduplicate_a_base_already_on_the_ladder() -> None:
    s = _settings(per_side_bps=30.0, sensitivity_per_side_bps=[100.0, 0.0, 30.0])
    assert sensitivity_levels(s) == [0.0, 30.0, 100.0]


def test_commissions_from_config() -> None:
    s = _settings(commission_per_share=0.004, commission_per_order=0.25)
    assert Commissions.from_config(s.costs) == Commissions(per_share=0.004, per_order=0.25)


# Spec acceptance (Costs and metrics): no numeric literals in costs.py other than these.
ALLOWED_LITERALS = {0, 1, -1, 2, 4, 12, 10_000}


def test_costs_module_has_no_stray_numeric_literals() -> None:
    tree = ast.parse(COSTS_PY.read_text(encoding="utf-8"))
    stray = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in ALLOWED_LITERALS
    ]
    assert stray == []

"""Property tests for `tradepartner.backtest.costs` (#3655, plan T445): P6 to P8."""

from __future__ import annotations

import math

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from tradepartner.backtest.costs import (
    Commissions,
    borrow_fee,
    buy_notional_after_costs,
    trade_cost,
)

_FINITE = {"allow_nan": False, "allow_infinity": False}
_MONEY = st.floats(0, 1e9, **_FINITE)
_BPS = st.floats(0, 500, **_FINITE)
_PER_SHARE = st.floats(0, 1, **_FINITE)
_PER_ORDER = st.floats(0, 10, **_FINITE)
_PRICE = st.floats(0.01, 1e5, **_FINITE)
_NON_FINITE = st.sampled_from([float("nan"), float("inf"), float("-inf")])


# --- P6: trade_cost ---------------------------------------------------------------


@given(
    notional=st.floats(),
    shares=st.floats(),
    bps=st.floats(0, 500),
    per_share=st.floats(0, 1),
    per_order=st.floats(0, 10),
)
@example(notional=float("nan"), shares=1.0, bps=10.0, per_share=0.0, per_order=0.0)
@example(notional=float("inf"), shares=1.0, bps=10.0, per_share=0.0, per_order=0.0)
@example(notional=1.0, shares=float("nan"), bps=10.0, per_share=0.01, per_order=0.0)
def test_trade_cost_refuses_or_returns_a_finite_non_negative_cost(
    notional: float, shares: float, bps: float, per_share: float, per_order: float
) -> None:
    try:
        cost = trade_cost(notional, shares, bps, Commissions(per_share, per_order))
    except ValueError:
        return
    assert math.isfinite(cost)
    assert cost >= 0


@pytest.mark.xfail(
    strict=True,
    reason="#3655 follow-up, not filed yet: a finite notional above about 1e304 overflows "
    "to an infinite cost; found by the explore profile, outside the non-finite-input "
    "refusal this issue makes",
)
def test_trade_cost_of_a_huge_finite_notional_is_refused_or_finite() -> None:
    """The shrunk example the `explore` profile found for the property above (the `ci`
    budget does not reach it): `notional * bps` overflows before the division."""
    try:
        cost = trade_cost(8.98846567431158e307, 0.0, 2.0, Commissions(0.0, 0.0))
    except ValueError:
        return
    assert math.isfinite(cost)


@given(
    low=_MONEY,
    high=_MONEY,
    fixed=_MONEY,
    bps=_BPS,
    per_share=_PER_SHARE,
    per_order=_PER_ORDER,
)
def test_trade_cost_does_not_decrease_when_notional_or_shares_grows(
    low: float, high: float, fixed: float, bps: float, per_share: float, per_order: float
) -> None:
    low, high = sorted((low, high))
    commissions = Commissions(per_share, per_order)
    # Notional grows, shares fixed.
    assert trade_cost(low, fixed, bps, commissions) <= trade_cost(high, fixed, bps, commissions)
    # Shares grow, notional fixed.
    assert trade_cost(fixed, low, bps, commissions) <= trade_cost(fixed, high, bps, commissions)


# --- P7: buy_notional_after_costs -------------------------------------------------


@given(cash=_MONEY, bps=_BPS, per_share=_PER_SHARE, per_order=_PER_ORDER, price=_PRICE)
def test_buy_notional_fits_cash_in_either_debit_order(
    cash: float, bps: float, per_share: float, per_order: float, price: float
) -> None:
    commissions = Commissions(per_share, per_order)
    notional = buy_notional_after_costs(cash, bps, commissions, price=price)
    assert math.isfinite(notional)
    assert 0 <= notional <= cash
    cost = trade_cost(notional, notional / price, bps, commissions)
    # Exact floats, as the docstring promises: whichever order the caller debits in.
    assert notional + cost <= cash
    assert cash - cost - notional >= 0
    assert cash - notional - cost >= 0


@given(cash=_NON_FINITE, bps=_BPS, per_share=_PER_SHARE, per_order=_PER_ORDER, price=_PRICE)
@example(cash=float("nan"), bps=10.0, per_share=0.0, per_order=0.0, price=10.0)
def test_buy_notional_refuses_a_non_finite_cash(
    cash: float, bps: float, per_share: float, per_order: float, price: float
) -> None:
    with pytest.raises(ValueError, match="cash"):
        buy_notional_after_costs(cash, bps, Commissions(per_share, per_order), price=price)


@given(cash=_MONEY, bps=_BPS, per_share=_PER_SHARE, per_order=_PER_ORDER, price=_NON_FINITE)
@example(cash=100.0, bps=10.0, per_share=0.0, per_order=0.0, price=float("inf"))
def test_buy_notional_refuses_a_non_finite_price(
    cash: float, bps: float, per_share: float, per_order: float, price: float
) -> None:
    with pytest.raises(ValueError, match="price"):
        buy_notional_after_costs(cash, bps, Commissions(per_share, per_order), price=price)


# --- P8: borrow_fee ---------------------------------------------------------------

_SHORTS = st.dictionaries(
    st.text("ABCDEFGH", min_size=1, max_size=3), st.floats(-1e9, 0, **_FINITE), max_size=8
)
_BORROW_BPS = st.floats(0, 5000, **_FINITE)
_DAYS = st.integers(0, 3650)
#: Far below a cent: the fee is money, and a product of tiny values underflows.
_FEE_ABS = 1e-9


@given(shorts=_SHORTS, bps=_BORROW_BPS, days=_DAYS)
def test_borrow_fee_is_non_negative_and_proportional_to_days(
    shorts: dict[str, float], bps: float, days: int
) -> None:
    fee = borrow_fee(shorts, bps, days)
    assert math.isfinite(fee)
    assert fee >= 0
    assert borrow_fee(shorts, bps, 0) == 0
    assert fee == pytest.approx(borrow_fee(shorts, bps, 1) * days, rel=1e-12, abs=_FEE_ABS)


@given(shorts=_SHORTS, bps=_BORROW_BPS, days=_DAYS, data=st.data())
def test_borrow_fee_is_additive_over_a_split_of_the_book(
    shorts: dict[str, float], bps: float, days: int, data: st.DataObject
) -> None:
    left_names = data.draw(
        st.lists(st.sampled_from(sorted(shorts)), unique=True) if shorts else st.just([])
    )
    left = {name: value for name, value in shorts.items() if name in left_names}
    right = {name: value for name, value in shorts.items() if name not in left_names}
    whole = borrow_fee(shorts, bps, days)
    parts = borrow_fee(left, bps, days) + borrow_fee(right, bps, days)
    assert whole == pytest.approx(parts, rel=1e-12, abs=_FEE_ABS)

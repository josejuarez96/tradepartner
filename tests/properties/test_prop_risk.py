"""Property tests for `tradepartner.execution.risk` (#3655, plan T445): P9 and P10
(`round_down`, `open_sold`, `OpenSell`)."""

from __future__ import annotations

import decimal
import sys
from decimal import Decimal

from hypothesis import example, given
from hypothesis import strategies as st

from tradepartner.execution.risk import OpenSell, open_sold, round_down

_FINITE = {"allow_nan": False, "allow_infinity": False}
_FLOAT_EPSILON = Decimal(sys.float_info.epsilon)


# --- P9 ---------------------------------------------------------------------------


@given(quantity=st.floats(0, 1e30, **_FINITE), decimals=st.integers(0, 12))
@example(quantity=1e27, decimals=2)
@example(quantity=0.30000000000000004, decimals=12)
@example(quantity=0.57, decimals=2)
def test_round_down_refuses_with_value_error_or_rounds_toward_zero_onto_the_grid(
    quantity: float, decimals: int
) -> None:
    try:
        rounded = round_down(quantity, decimals)
    except ValueError:
        return  # the only refusal: never `decimal.InvalidOperation`
    assert 0 <= rounded <= quantity
    with decimal.localcontext() as context:
        context.prec = 400
        assert Decimal(repr(quantity)) - Decimal(repr(rounded)) < Decimal(1).scaleb(-decimals)
    assert round_down(rounded, decimals) == rounded


# --- P10 --------------------------------------------------------------------------

_SELLS = st.lists(
    st.builds(
        OpenSell,
        security_id=st.sampled_from(["A", "B", "C"]),
        unfilled_quantity=st.floats(0, 1e9, **_FINITE),
        split_ratios=st.integers(0, 3),
    ),
    max_size=12,
)


@given(sells=_SELLS, decimals=st.integers(0, 9))
def test_open_sold_is_on_the_grid_and_never_under_counts_beyond_float_noise(
    sells: list[OpenSell], decimals: int
) -> None:
    totals = open_sold(sells, decimals)
    with decimal.localcontext() as context:
        context.prec = 400  # exact sums of the quantities' shortest decimal forms
        step = Decimal(1).scaleb(-decimals)
        for name in {sell.security_id for sell in sells}:
            mine = [sell for sell in sells if sell.security_id == name]
            exact = sum((Decimal(repr(sell.unfilled_quantity)) for sell in mine), Decimal(0))
            noise = sum(
                (
                    _FLOAT_EPSILON
                    * max(Decimal(repr(sell.unfilled_quantity)), Decimal(1))
                    * (sell.split_ratios + 2)
                    for sell in mine
                ),
                Decimal(0),
            )
            total = totals[name]
            assert total % step == 0
            assert total >= exact - noise
            assert total <= exact + step * len(mine)
    assert set(totals) == {sell.security_id for sell in sells}

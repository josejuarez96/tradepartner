"""Tests for `execution.risk` (Phase 4 plan T54; ADR 0010 point 1; spec req 3).

Each limit, overridden one at a time in the frozen section, turns a passing
phase into a violation naming its rule, an over-cash batch included; the
phase-time skips and the skip cap; the sell-sum rule with a partly filled open
sell counting only its unfilled part and a second full exit for one name as a
violation; rounding never up; a `Settings` override changes nothing, only
`frozen` and the explicit `quantity_decimals` do; the three-name sizing case
with the cost deducted exactly once and every notional buy scaled alike;
whole-share buys last and within the buffered cash; deferral. The literal
check on the module is T50's (`tests/test_no_literals.py`).

T54c (ADR 0010 amendment 2026-09-30): full exits exempt from the per-order
limit; the post-phase weight of every name bought; one order per (name, side);
a strict cash rule in `Decimal` that every `size_buys` batch passes; every sell
order by quantity.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import polars as pl
import pytest

from tradepartner.adapters.broker import Account, Asset
from tradepartner.backtest.costs import Commissions, buy_notional_after_costs, trade_cost
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, Remainder
from tradepartner.execution.plan import remainder as plan_remainder
from tradepartner.execution.risk import (
    BuyToSize,
    OpenSell,
    PhaseOrder,
    Skips,
    Violations,
    _spendable,
    check_phase,
    open_sold,
    round_down,
    size_buys,
    unfilled_sells,
)
from tradepartner.store.journal import DecisionRow, OrderedFill, OrderEventRow, OrderRow

_NOW = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
_S = date(2026, 10, 1)
_PRICES = {"A": 10.0, "B": 20.0, "C": 50.0, "D": 100.0}
_NO_COSTS = BuyCosts(per_side_bps=0.0, commissions=Commissions(per_share=0.0, per_order=0.0))
_COSTS = BuyCosts(per_side_bps=15.0, commissions=Commissions(per_share=0.0, per_order=0.0))


_NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
    }
)


def _split(ex_date: date, ratio: float = 2.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": ["A"],
            "action_type": ["split"],
            "ex_date": [ex_date],
            "ratio_or_amount": [ratio],
        }
    )


def _price_of(security_id: str) -> float:
    return _PRICES[security_id]


def _ledger(**positions: float) -> Ledger:
    return Ledger(positions=positions or {"A": 10.0, "B": 5.0}, cash=1000.0, through=_S)


def _account(cash: float = 1000.0) -> Account:
    return Account(account_id="PA1", cash=cash, buying_power=5 * cash, equity=1200.0, as_of=_NOW)


def _asset(tradable: bool = True, fractionable: bool = True) -> Asset:
    return Asset(tradable=tradable, fractionable=fractionable, status="active", cusip=None)


_ASSETS = {name: _asset() for name in _PRICES}


def _order(security_id: str, side: str, decision_id: int = 1, **kw: Any) -> PhaseOrder:
    base: dict[str, Any] = {
        "decision_id": decision_id,
        "security_id": security_id,
        "symbol": security_id,
        "side": side,
        "decision": "trade",
        "price": _PRICES[security_id],
    }
    return PhaseOrder(**{**base, **kw})


def _passing_phase() -> list[PhaseOrder]:
    """Equity 1200 (cash 1000 + A 100 + B 100): a 2-share sell of A ($20) and a
    $50 buy of C at target weight 0.04 pass every default limit."""
    return [
        _order("A", "sell", 1, quantity=2.0),
        _order("C", "buy", 2, notional=50.0, target_weight=0.04),
    ]


def _check(
    orders: list[PhaseOrder], frozen: RiskConfig | None = None, **kw: Any
) -> Violations | Skips:
    args: dict[str, Any] = {
        "ledger": _ledger(),
        "account": _account(),
        "assets": _ASSETS,
        "frozen": frozen or RiskConfig(),
        "quantity_decimals": 6,
        "price_of": _price_of,
        "costs": _NO_COSTS,
    }
    args.update(kw)
    return check_phase(orders, **args)


#: Per-order notional loosened, for sell cases larger than 5 % of equity.
_LOOSE = RiskConfig(max_order_notional_fraction=1.0)


def _rules(result: Violations | Skips) -> set[str]:
    assert isinstance(result, Violations), result
    return {v.rule for v in result.violations}


# --- the batch limits ------------------------------------------------------------------------


def test_the_passing_phase_passes() -> None:
    result = _check(_passing_phase())
    assert isinstance(result, Skips)
    assert result.skips == ()
    assert [o.security_id for o in result.orders] == ["A", "C"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_position_weight", 0.03),  # the C buy targets 0.04
        ("max_order_notional_fraction", 0.02),  # $24 per order; the C buy is $50
        ("max_gross_exposure", 0.1),  # after the batch: A 80 + B 100 + C 50 = 230 / 1200
        ("max_orders_per_run", 1),  # two orders
    ],
)
def test_each_limit_turns_the_passing_phase_into_a_violation_naming_it(
    key: str, value: float
) -> None:
    result = _check(_passing_phase(), RiskConfig(**{key: value}))
    assert _rules(result) == {key}
    (violation,) = result.violations  # type: ignore[union-attr]
    assert violation.kind == "limit_breach"


def test_orders_submitted_earlier_in_the_run_count_toward_the_order_cap() -> None:
    assert isinstance(_check(_passing_phase(), RiskConfig(max_orders_per_run=3)), Skips)
    result = _check(_passing_phase(), RiskConfig(max_orders_per_run=3), prior_orders=2)
    assert _rules(result) == {"max_orders_per_run"}


def test_an_over_cash_batch_is_a_violation() -> None:
    result = _check(_passing_phase(), account=_account(cash=40.0))
    assert _rules(result) == {"buys_within_cash"}


def test_buys_are_checked_against_cash_with_their_cost() -> None:
    """$50 at 15 bp costs $50.075: $50.05 of cash is short, $50.075 fits."""
    orders = [_order("C", "buy", 2, notional=50.0, target_weight=0.04)]
    short = _check(orders, account=_account(cash=50.05), costs=_COSTS)
    assert _rules(short) == {"buys_within_cash"}
    assert isinstance(_check(orders, account=_account(cash=50.075), costs=_COSTS), Skips)


def test_whole_share_buys_are_checked_at_the_buffered_price() -> None:
    """One share of D at $100 needs $102 with the default 2 % buffer."""
    orders = [_order("D", "buy", 2, quantity=1.0, whole_share=True, target_weight=0.04)]
    assets = {**_ASSETS, "D": _asset(fractionable=False)}
    frozen = RiskConfig(max_order_notional_fraction=1.0, max_position_weight=1.0)
    assert _rules(_check(orders, frozen, account=_account(101.0), assets=assets)) == {
        "buys_within_cash"
    }
    assert isinstance(_check(orders, frozen, account=_account(102.0), assets=assets), Skips)


def test_a_buy_without_a_target_weight_fails_closed() -> None:
    orders = [_order("C", "buy", 2, notional=50.0)]
    assert _rules(_check(orders)) == {"max_position_weight"}


def test_a_missing_asset_fails_closed() -> None:
    assets = {k: v for k, v in _ASSETS.items() if k != "C"}
    assert _rules(_check(_passing_phase(), assets=assets)) == {"asset_missing"}


def test_a_fractional_order_on_a_non_fractionable_name_is_a_violation() -> None:
    assets = {**_ASSETS, "A": _asset(fractionable=False)}
    assert isinstance(_check(_passing_phase()[:1], assets=assets), Skips)  # 2 whole shares
    fractional = [_order("A", "sell", 1, quantity=2.5)]
    assert _rules(_check(fractional, assets=assets)) == {"whole_shares"}
    by_notional = [_order("C", "buy", 2, notional=50.0, target_weight=0.04)]
    assert _rules(_check(by_notional, assets={**_ASSETS, "C": _asset(fractionable=False)})) == {
        "whole_shares"
    }


def test_a_settings_override_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only `frozen` and the explicit `quantity_decimals` are read."""
    monkeypatch.setenv("RISK__MAX_ORDERS_PER_RUN", "1")
    monkeypatch.setenv("RISK__MAX_POSITION_WEIGHT", "0.001")
    monkeypatch.setenv("ALPACA__QUANTITY_DECIMALS", "0")
    ledger = _ledger(A=10.0, B=0.5)
    orders = [*_passing_phase(), _order("B", "sell", 3, quantity=0.5)]
    assert isinstance(_check(orders, ledger=ledger), Skips)
    assert _rules(_check(orders, ledger=ledger, quantity_decimals=0)) == {
        "sell_within_holding",
        "sell_sum_within_holding",
    }


# --- sells -----------------------------------------------------------------------------------


def test_a_sell_is_at_most_the_holding_rounded_down() -> None:
    ledger = _ledger(A=1.23456)
    assert isinstance(
        _check([_order("A", "sell", quantity=1.23)], ledger=ledger, quantity_decimals=2), Skips
    )
    over = _check([_order("A", "sell", quantity=1.234)], ledger=ledger, quantity_decimals=2)
    assert _rules(over) == {"sell_within_holding", "sell_sum_within_holding"}
    assert isinstance(
        _check([_order("A", "sell", quantity=1.234)], ledger=ledger, quantity_decimals=3), Skips
    )


def test_a_sell_of_a_name_not_held_is_a_violation() -> None:
    assert _rules(_check([_order("C", "sell", quantity=1.0)])) == {
        "sell_within_holding",
        "sell_sum_within_holding",
    }


def test_a_sell_by_notional_is_refused() -> None:
    """Every sell order is by quantity (ADR 0010 amendment 2026-09-30): a
    notional sell is a wrapper fault, refused before any skip or submit."""
    assert _rules(_check([_order("A", "sell", notional=100.0)], _LOOSE)) == {"sell_by_quantity"}
    assert _rules(_check([_order("A", "sell", notional=10.005)], _LOOSE)) == {"sell_by_quantity"}
    tiny = _order("A", "sell", notional=0.5, full_exit=True)  # would be dust by quantity
    assert _rules(_check([tiny], _LOOSE)) == {"sell_by_quantity"}


def test_a_full_exit_beside_an_earlier_open_sell_over_the_holding_is_a_violation() -> None:
    orders = [_order("A", "sell", 1, quantity=10.0, full_exit=True)]
    result = _check(orders, _LOOSE, open_sells=[OpenSell("A", 1.0)])
    assert _rules(result) == {"sell_sum_within_holding"}


def test_open_sells_count_only_their_unfilled_part() -> None:
    order = OrderRow(
        client_order_id="tp-A-1",
        decision_id=9,
        run_id=1,
        session=date(2026, 9, 30),
        attempt=1,
        phase="sell",
        security_id="A",
        symbol="A",
        side="sell",
        quantity=6.0,
        sells_in_flight_at_submit=False,
        known_at=_NOW,
        ingested_at=_NOW,
    )
    accepted = OrderEventRow(
        client_order_id="tp-A-1", status="accepted", known_at=_NOW, ingested_at=_NOW
    )
    fill = OrderedFill(
        fill=_fill("tp-A-1", 4.0), side="sell", security_id="A", symbol="A", run_id=1, window_id=1
    )
    (open_sell,) = unfilled_sells([order], [accepted], [fill], _price_of, _NO_ACTIONS, session=_S)
    assert open_sell == OpenSell("A", 2.0)
    # 8 + the 2 unfilled = 10 = the holding: passes; 8.5 + 2 does not
    assert isinstance(
        _check([_order("A", "sell", quantity=8.0)], _LOOSE, open_sells=[open_sell]), Skips
    )
    over = _check([_order("A", "sell", quantity=8.5)], _LOOSE, open_sells=[open_sell])
    assert _rules(over) == {"sell_sum_within_holding"}
    # A terminal sell holds nothing back
    filled = replace(accepted, status="expired")
    assert (
        unfilled_sells([order], [accepted, filled], [fill], _price_of, _NO_ACTIONS, session=_S)
        == []
    )
    # A 2:1 split between the order's session and S doubles the unfilled part:
    # the holding is on S's basis, so counting 2 would let a batch oversell.
    (split_sell,) = unfilled_sells([order], [accepted], [fill], _price_of, _split(_S), session=_S)
    assert split_sell == OpenSell("A", 4.0, split_ratios=1)
    before = unfilled_sells(
        [order], [accepted], [fill], _price_of, _split(date(2026, 9, 30)), session=_S
    )
    assert before == [OpenSell("A", 2.0)]  # ex-date on the order's own session: already in


def _open_sell_row(quantity: float | None = None, notional: float | None = None) -> OrderRow:
    return OrderRow(
        client_order_id="tp-A-1",
        decision_id=9,
        run_id=1,
        session=date(2026, 9, 30),
        attempt=1,
        phase="sell",
        security_id="A",
        symbol="A",
        side="sell",
        quantity=quantity,
        notional=notional,
        sells_in_flight_at_submit=False,
        known_at=_NOW,
        ingested_at=_NOW,
    )


def _sell_fills(*quantities: float, price: float = 10.0) -> list[OrderedFill]:
    return [
        OrderedFill(
            fill=replace(_fill("tp-A-1", q), price=price),
            side="sell",
            security_id="A",
            symbol="A",
            run_id=1,
            window_id=1,
        )
        for q in quantities
    ]


_ACCEPTED = OrderEventRow(
    client_order_id="tp-A-1", status="accepted", known_at=_NOW, ingested_at=_NOW
)


def test_an_open_sells_unfilled_part_is_exact_not_float_noise() -> None:
    """#719 item 1: the unfilled part is submitted minus filled in `Decimal`,
    so a large partly filled sell leaves its exact grid quantity (`float`
    leaves 0.30000000000001137 for 500 - 499.7) and a fully filled one with no
    terminal event yet leaves nothing (`float` leaves about 1e-16)."""
    order = _open_sell_row(quantity=500.0)
    (partly,) = unfilled_sells(
        [order], [_ACCEPTED], _sell_fills(499.7), _price_of, _NO_ACTIONS, session=_S
    )
    assert partly == OpenSell("A", 0.3)
    whole = _open_sell_row(quantity=1.0)
    fills = _sell_fills(0.7, 0.2, 0.1)
    assert unfilled_sells([whole], [_ACCEPTED], fills, _price_of, _NO_ACTIONS, session=_S) == []


def test_an_off_grid_open_sell_after_a_one_third_split_counts_up_to_the_grid() -> None:
    """#719 item 1: a 1-for-3 split leaves 10 unfilled shares at 3.333...; the
    nearest 9-dp step (3.333333333) would under-count it by a third of a step,
    so an off-grid estimate rounds **up** (3.333333334). 9 shares land on 3
    up to `float` noise, which still snaps to the nearest step."""
    split = _split(_S, 1 / 3)
    ten = unfilled_sells(
        [_open_sell_row(quantity=10.0)], [_ACCEPTED], [], _price_of, split, session=_S
    )
    assert open_sold(ten, 9) == {"A": Decimal("3.333333334")}
    nine = unfilled_sells(
        [_open_sell_row(quantity=9.0)], [_ACCEPTED], [], _price_of, split, session=_S
    )
    assert open_sold(nine, 9) == {"A": Decimal(3)}


def test_an_open_notional_sell_estimate_counts_up_to_the_grid() -> None:
    """#719 item 1: a notional sell's unfilled value over the reference price
    is an estimate, never on the grid by construction: it rounds up."""
    order = _open_sell_row(notional=100.0)  # $100 at $10: fills 3 shares, $70 left
    (left,) = unfilled_sells(
        [order], [_ACCEPTED], _sell_fills(3.0), lambda _s: 30.0, _NO_ACTIONS, session=_S
    )
    assert open_sold([left], 9) == {"A": Decimal("2.333333334")}
    assert open_sold([left], 2) == {"A": Decimal("2.34")}


def _splits(*ratios: float) -> pl.DataFrame:
    """Splits of A with ex-dates in (the open sell's session 2026-09-30, S]."""
    return pl.DataFrame(
        {
            "security_id": ["A"] * len(ratios),
            "action_type": ["split"] * len(ratios),
            "ex_date": [_S] * len(ratios),
            "ratio_or_amount": list(ratios),
        }
    )


@pytest.mark.parametrize(
    ("quantity", "filled", "ratios", "decimals", "expected"),
    [
        (60.0, (), (3 / 2, 1 / 10), 0, "9"),  # float product: 9.000000000000002
        (81.0, (), (1 / 3, 5 / 3), 0, "45"),  # 45.00000000000001
        (65.0, (1.7,), (1 / 15,), 9, "4.22"),  # 4.220000000000001 once stored
        (10.0, (), (1 / 3,), 9, "3.333333334"),  # truly off the grid: up
    ],
)
def test_a_split_adjusted_open_sell_on_the_grid_is_not_counted_a_step_up(
    quantity: float,
    filled: tuple[float, ...],
    ratios: tuple[float, ...],
    decimals: int,
    expected: str,
) -> None:
    """#719 item 1, /code-review on PR #745: an open sell whose true unfilled
    quantity is on the grid after one or more splits carries the `float`
    rounding of each ratio, of their product and of its own storage; that
    noise widens with the split count and still snaps to the nearest step,
    so a whole-share trim beside it never loses a share."""
    (left,) = unfilled_sells(
        [_open_sell_row(quantity=quantity)],
        [_ACCEPTED],
        _sell_fills(*filled),
        _price_of,
        _splits(*ratios),
        session=_S,
    )
    assert left.split_ratios == len(ratios)
    assert open_sold([left], decimals) == {"A": Decimal(expected)}


@pytest.mark.parametrize(
    ("unfilled", "decimals", "expected"),
    [
        (1.0 - (0.7 + 0.2 + 0.1), 9, "0"),  # float noise of a filled sell
        (1.0 - 0.7, 9, "0.3"),  # 0.30000000000000004
        (9 * (1 / 3), 9, "3"),  # 2.9999999999999996 or so
        (0.615, 2, "0.62"),  # off the 2-dp grid by half a step: up
        (0.611, 2, "0.62"),  # off the grid below the midpoint: still up
        (0.62, 2, "0.62"),
    ],
)
def test_open_sold_snaps_only_float_noise_and_rounds_any_other_off_grid_part_up(
    unfilled: float, decimals: int, expected: str
) -> None:
    """#719 item 1: nearest step only within `float` noise of it, else up."""
    assert open_sold([OpenSell("A", unfilled)], decimals) == {"A": Decimal(expected)}


def _fill(client_order_id: str, quantity: float) -> Any:
    from tradepartner.store.journal import FillRow

    return FillRow(
        fill_id=1,
        client_order_id=client_order_id,
        filled_at=_NOW,
        quantity=quantity,
        price=10.0,
        price_implied=False,
        broker_fill_id="bf-1",
        source="broker_feed",
        known_at=_NOW,
        ingested_at=_NOW,
    )


@pytest.mark.parametrize(
    ("quantity", "decimals", "expected"),
    [
        (1.239, 2, 1.23),
        (0.57, 2, 0.57),
        (1.0, 0, 1.0),
        (2.9999, 0, 2.0),
        (0.1 + 0.2, 1, 0.3),
        (5.0, 3, 5.0),
    ],
)
def test_rounding_is_never_up(quantity: float, decimals: int, expected: float) -> None:
    assert round_down(quantity, decimals) == expected
    assert round_down(quantity, decimals) <= quantity


def test_round_down_refuses_negative_decimals() -> None:
    with pytest.raises(ValueError, match="decimals"):
        round_down(1.0, -1)


# --- phase-time skips ---------------------------------------------------------------------------


def _skip_reasons(result: Violations | Skips) -> dict[int, str]:
    assert isinstance(result, Skips), result
    return {s.decision_id: s.reason for s in result.skips}


def test_phase_time_skips_and_their_reasons() -> None:
    assets = {**_ASSETS, "B": _asset(tradable=False), "D": _asset(fractionable=False)}
    orders = [
        _order("B", "sell", 1, quantity=1.0),  # trim of an untradable name
        _order("B", "sell", 2, quantity=5.0, decision="forced_exit", full_exit=True),
        _order("D", "buy", 3, quantity=0.0, whole_share=True, target_weight=0.01),
        _order("D", "sell", 4, quantity=0.0, whole_share=True, full_exit=True),
        _order("C", "buy", 5, notional=0.5, target_weight=0.01),
        _order("A", "sell", 6, quantity=0.04, full_exit=True),
        _order("A", "sell", 7, quantity=1.0, listing_ended=True),
        _order("A", "sell", 8, quantity=1.0),
    ]
    result = _check(orders, assets=assets, ledger=_ledger(A=10.0, B=5.0, D=0.4))
    assert _skip_reasons(result) == {
        1: "skip_untradable",
        2: "untradable",
        3: "skip_below_one_share",
        4: "dust",
        5: "skip_below_minimum",
        6: "dust",
        7: "skip_delisted",
    }
    counted = {s.decision_id for s in result.skips if s.counts_toward_cap}  # type: ignore[union-attr]
    assert counted == {1, 3, 5, 7}
    assert [o.decision_id for o in result.orders] == [8]  # type: ignore[union-attr]


def test_the_skip_cap_counts_neither_dust_nor_untradable() -> None:
    assets = {**_ASSETS, "B": _asset(tradable=False)}
    orders = [
        _order("C", "buy", 1, notional=0.5, target_weight=0.01),  # counts
        _order("B", "sell", 2, quantity=5.0, decision="forced_exit", full_exit=True),  # untradable
        _order("A", "sell", 3, quantity=0.04, full_exit=True),  # dust
    ]
    assert isinstance(_check(orders, RiskConfig(max_skips_per_run=1), assets=assets), Skips)
    result = _check(orders, RiskConfig(max_skips_per_run=1), assets=assets, prior_skips=1)
    assert _rules(result) == {"max_skips_per_run"}
    (violation,) = result.violations  # type: ignore[union-attr]
    assert violation.kind == "skip_cap"


def test_skipped_orders_are_not_checked_against_the_limits() -> None:
    """An untradable buy far over the per-order limit is a skip, not a breach."""
    assets = {**_ASSETS, "C": _asset(tradable=False)}
    orders = [_order("C", "buy", 1, notional=900.0, target_weight=0.9)]
    assert _skip_reasons(_check(orders, assets=assets)) == {1: "skip_untradable"}


# --- buy sizing ------------------------------------------------------------------------------


def _buy(
    decision_id: int, security_id: str, remainder: float, *, whole_share: bool = False
) -> BuyToSize:
    decision = DecisionRow(
        decision_id=decision_id,
        run_id=1,
        rebalance_session=_S,
        security_id=security_id,
        side="buy",
        planned_notional=remainder,
        target_notional=remainder,
        whole_share=whole_share,
        decision="trade",
        known_at=_NOW,
        ingested_at=_NOW,
    )
    price = _PRICES[security_id]
    return BuyToSize(decision, Remainder(quantity=remainder / price, notional=remainder))


def test_three_names_are_scaled_alike_with_the_cost_deducted_once() -> None:
    buys = [_buy(1, "A", 400.0), _buy(2, "B", 300.0), _buy(3, "C", 300.0)]
    sizings = size_buys(buys, 1000.0, _price_of, RiskConfig(), _COSTS)
    spendable = buy_notional_after_costs(1000.0, 15.0, _COSTS.commissions, price=10.0)
    scale = spendable / 1000.0
    assert [s.decision_id for s in sizings] == [1, 2, 3]
    for sizing, remainder in zip(sizings, (400.0, 300.0, 300.0), strict=True):
        assert sizing.notional is not None  # floored to the cent
        assert remainder * scale - 0.01 < sizing.notional <= remainder * scale
        assert sizing.quantity is None and not sizing.deferred
    notionals = [s.notional or 0.0 for s in sizings]
    total = sum(
        n + trade_cost(n, n / _PRICES[s.security_id], 15.0, _COSTS.commissions)
        for n, s in zip(notionals, sizings, strict=True)
    )
    assert total <= 1000.0 + 1e-9
    # with cash to spare the remainders are bought whole: no second cost deduction
    roomy = size_buys(buys, 2000.0, _price_of, RiskConfig(), _COSTS)
    assert [s.notional for s in roomy] == [400.0, 300.0, 300.0]


def test_whole_share_buys_go_last_by_floor_at_the_buffered_price() -> None:
    buys = [_buy(1, "D", 250.0, whole_share=True), _buy(2, "A", 100.0)]
    sizings = size_buys(buys, 1000.0, _price_of, RiskConfig(), _NO_COSTS)
    assert [s.decision_id for s in sizings] == [2, 1]
    whole = sizings[1]
    assert whole.quantity == 2.0 and whole.notional is None  # floor(250 / 102)
    assert not whole.deferred


def test_a_whole_share_buy_that_does_not_fit_the_cash_left_is_deferred() -> None:
    """Remainders 300 (A) and 250 (D, whole-share). At $500: scale 500/550, A
    takes $272.73, D floors 227.27 / 102 to 2 shares ($204), which fit the
    $227.27 left. At $200: scale 200/550, A takes $109.09 and D floors
    90.91 / 102 to 0 shares: deferred."""
    buys = [_buy(1, "A", 300.0), _buy(2, "D", 250.0, whole_share=True)]
    fits = size_buys(buys, 500.0, _price_of, RiskConfig(), _NO_COSTS)
    assert [(s.decision_id, s.quantity, s.deferred) for s in fits][1] == (2, 2.0, False)
    short = size_buys(buys, 200.0, _price_of, RiskConfig(), _NO_COSTS)
    assert [(s.decision_id, s.deferred) for s in short] == [(1, False), (2, True)]
    assert short[1].quantity is None and short[1].notional is None
    # D, flooring to no share, is deferred before A is sized: A is rescaled alone
    assert short[0].notional == pytest.approx(200.0)


@pytest.mark.parametrize(
    ("a_remainder", "b_remainder", "kept"),
    [(600.0, 500.0, "A"), (500.0, 500.0, "B")],
    ids=["smallest-remainder", "security-id-tie-break"],
)
def test_below_minimum_whole_share_buys_are_deferred_one_at_a_time(
    a_remainder: float, b_remainder: float, kept: str
) -> None:
    """At the shared scale neither $400 name buys a share; dropping just the
    smallest remainder (or the lower security_id on a tie) lets the other buy one."""
    buys = [
        _buy(2, "B", b_remainder, whole_share=True),
        _buy(1, "A", a_remainder, whole_share=True),
    ]
    sizings = size_buys(buys, 700.0, {"A": 400.0, "B": 400.0}.__getitem__, RiskConfig(), _NO_COSTS)
    assert [(s.security_id, s.quantity, s.deferred) for s in sizings] == [
        (name, 1.0 if name == kept else None, name != kept) for name in ("B", "A")
    ]


def test_whole_share_buy_impossible_at_full_scale_is_deferred_first() -> None:
    """B cannot buy a buffered share even at full scale; A can after B defers."""
    buys = [
        _buy(1, "A", 500.0, whole_share=True),
        _buy(2, "B", 1000.0, whole_share=True),
    ]
    sizings = size_buys(
        buys, 1000.0, {"A": 400.0, "B": 1200.0}.__getitem__, RiskConfig(), _NO_COSTS
    )
    assert [(s.security_id, s.quantity, s.deferred) for s in sizings] == [
        ("A", 1.0, False),
        ("B", None, True),
    ]


def test_a_scaled_attempt_below_the_minimum_is_deferred_and_the_rest_rescaled() -> None:
    """$10 of cash for remainders 18 (A) and 2 (B): scale 0.5 puts B at $1,
    below a $1.5 minimum, so B defers and A is rescaled alone: min(1, 10/18)."""
    buys = [_buy(1, "A", 18.0), _buy(2, "B", 2.0)]
    sizings = size_buys(buys, 10.0, _price_of, RiskConfig(min_order_notional=1.5), _NO_COSTS)
    assert sizings[0].notional == pytest.approx(10.0)
    assert (sizings[1].deferred, sizings[1].notional) == (True, None)


def test_sizing_refuses_bad_inputs() -> None:
    with pytest.raises(ValueError, match="cash"):
        size_buys([_buy(1, "A", 10.0)], math.nan, _price_of, RiskConfig(), _NO_COSTS)
    sell = _buy(1, "A", 10.0)
    with pytest.raises(ValueError, match="buy"):
        size_buys(
            [replace(sell, decision=replace(sell.decision, side="sell"))],
            10.0,
            _price_of,
            RiskConfig(),
            _NO_COSTS,
        )
    assert size_buys([], 10.0, _price_of, RiskConfig(), _NO_COSTS) == []


def test_a_sized_batch_passes_the_cash_check_with_commissions() -> None:
    """Sizing reserves each buy's per-order commission and the per-share one at
    the lowest price, so its output never trips `buys_within_cash`."""
    costs = BuyCosts(per_side_bps=15.0, commissions=Commissions(per_share=0.01, per_order=1.0))
    buys = [
        _buy(1, "A", 400.0),
        _buy(2, "B", 300.0),
        _buy(3, "C", 300.0),
        _buy(4, "D", 250.0, whole_share=True),
    ]
    sizings = size_buys(buys, 1000.0, _price_of, RiskConfig(), costs)
    assert not any(s.deferred for s in sizings)
    by_id = {b.decision.decision_id: b for b in buys}
    orders = [
        _order(
            s.security_id,
            "buy",
            s.decision_id,
            notional=s.notional,
            quantity=s.quantity,
            whole_share=by_id[s.decision_id].by_whole_shares,
            target_weight=0.01,
        )
        for s in sizings
    ]
    loose = RiskConfig(
        max_order_notional_fraction=1.0, max_gross_exposure=1.0, max_position_weight=1.0
    )
    ledger = Ledger(positions={}, cash=1000.0, through=_S)
    result = _check(orders, loose, ledger=ledger, account=_account(1000.0), costs=costs)
    assert isinstance(result, Skips), result
    spent = 0.0
    for order in orders:
        buffered = order.price * (1 + RiskConfig().whole_share_price_buffer)
        notional = (
            buffered * order.quantity if order.whole_share and order.quantity else order.value()
        )
        spent += notional + trade_cost(notional, notional / order.price, 15.0, costs.commissions)
    assert 900.0 < spent <= 1000.0
    short = _check(orders, loose, ledger=ledger, account=_account(spent - 0.02), costs=costs)
    assert _rules(short) == {"buys_within_cash"}


def test_a_whole_share_buy_worth_less_than_the_minimum_is_deferred() -> None:
    """One share at $0.50 is below a $1 minimum: deferred, not left for the
    phase check to skip against the cap."""
    prices = {**_PRICES, "E": 0.5}
    buy = _buy(1, "A", 10.0)
    small = _buy(2, "A", 0.6)  # one share of E at the buffered $0.51
    cheap = replace(small, decision=replace(small.decision, security_id="E", whole_share=True))
    sizings = size_buys([buy, cheap], 100.0, prices.__getitem__, RiskConfig(), _NO_COSTS)
    assert [(s.decision_id, s.deferred) for s in sizings] == [(1, False), (2, True)]


@pytest.mark.parametrize("weight", [math.nan, math.inf, -0.01])
def test_a_non_finite_or_negative_target_weight_fails_closed(weight: float) -> None:
    orders = [_order("C", "buy", 2, notional=50.0, target_weight=weight)]
    assert _rules(_check(orders)) == {"max_position_weight"}


def test_a_non_finite_price_for_a_name_not_held_fails_closed() -> None:
    prices = {**_PRICES, "C": math.nan}
    orders = [_order("C", "buy", 2, notional=50.0, target_weight=0.04)]
    with pytest.raises(ValueError, match="price of C"):
        _check(orders, price_of=prices.__getitem__)


def test_a_buy_marked_as_an_exit_is_refused() -> None:
    """Else a sub-minimum buy would pass as cap-exempt `dust`."""
    for flags in ({"full_exit": True}, {"decision": "forced_exit"}):
        with pytest.raises(ValueError, match="exit"):
            _check([_order("C", "buy", 2, notional=0.5, target_weight=0.01, **flags)])


def test_a_zero_size_order_is_skipped_even_with_no_minimum() -> None:
    orders = [_order("A", "sell", 1, quantity=0.0)]
    assert _skip_reasons(_check(orders, RiskConfig(min_order_notional=0.0))) == {
        1: "skip_below_minimum"
    }


def test_a_delisted_name_the_broker_no_longer_lists_is_skipped_not_a_breach() -> None:
    assets = {k: v for k, v in _ASSETS.items() if k != "A"}
    orders = [_order("A", "sell", 7, quantity=1.0, listing_ended=True)]
    assert _skip_reasons(_check(orders, assets=assets)) == {7: "skip_delisted"}


def test_the_sell_sum_uses_the_holding_rounded_down() -> None:
    ledger = _ledger(A=1.239)
    orders = [_order("A", "sell", 1, quantity=0.62)]
    result = _check(orders, ledger=ledger, quantity_decimals=2, open_sells=[OpenSell("A", 0.615)])
    assert _rules(result) == {"sell_sum_within_holding"}  # 1.235 over 1.23, under 1.239


# --- ADR 0010 amendment 2026-09-30 (T54c) ----------------------------------------------------


def test_full_exits_are_exempt_from_the_per_order_limit_and_trims_are_not() -> None:
    """Equity 1200: the per-order limit is $60. Selling all 10 shares of A
    ($100) passes as a plan or `window_stop` full exit; the same sell as a trim
    breaches."""
    plan_exit = _order("A", "sell", 1, quantity=10.0, full_exit=True)
    stop_exit = _order("A", "sell", 1, quantity=10.0, decision="forced_exit", full_exit=True)
    for exit_order in (plan_exit, stop_exit):
        assert isinstance(_check([exit_order]), Skips)
    trim = _order("A", "sell", 1, quantity=10.0)
    assert _rules(_check([trim])) == {"max_order_notional_fraction"}


def test_two_buys_of_one_name_breach_the_post_phase_weight() -> None:
    """Two $40 buys of C at target 0.03 each: $80 after the phase over the $60
    that 0.05 of equity 1200 allows."""
    orders = [
        _order("C", "buy", 1, notional=40.0, target_weight=0.03),
        _order("C", "buy", 2, notional=40.0, target_weight=0.03),
    ]
    assert _rules(_check(orders)) == {"max_position_weight", "one_order_per_name_side"}


def test_a_buy_on_a_drifted_holding_breaches_and_a_drifted_name_not_bought_does_not() -> None:
    """B is held at $100 (0.083 of equity 1200), above 0.05: a $10 buy of it at
    target 0.04 ends at $110; the passing phase, which does not buy B, passes."""
    buy_b = [_order("B", "buy", 1, notional=10.0, target_weight=0.04)]
    assert _rules(_check(buy_b)) == {"max_position_weight"}
    assert isinstance(_check(_passing_phase()), Skips)


def test_a_whole_share_buy_counts_at_the_buffered_price_after_the_phase() -> None:
    """One share of D: $100 is under 0.0835 x 1200 = $100.2, the buffered $102
    is not."""
    orders = [_order("D", "buy", 1, quantity=1.0, whole_share=True, target_weight=0.04)]
    assets = {**_ASSETS, "D": _asset(fractionable=False)}
    frozen = RiskConfig(max_position_weight=0.0835, max_order_notional_fraction=1.0)
    assert _rules(_check(orders, frozen, assets=assets)) == {"max_position_weight"}
    roomy = RiskConfig(max_position_weight=0.086, max_order_notional_fraction=1.0)
    assert isinstance(_check(orders, roomy, assets=assets), Skips)


def test_two_orders_on_one_name_and_side_breach() -> None:
    orders = [_order("A", "sell", 1, quantity=1.0), _order("A", "sell", 2, quantity=1.0)]
    assert _rules(_check(orders)) == {"one_order_per_name_side"}
    # a buy and a sell of one name are two sides
    both = [
        _order("A", "sell", 1, quantity=1.0),
        _order("A", "buy", 2, notional=5.0, target_weight=0.01),
    ]
    assert isinstance(_check(both, ledger=_ledger(A=2.0)), Skips)


def test_cash_is_compared_strictly_at_the_cent() -> None:
    """$50 at 15 bp needs $50.075: that cash passes, one cent less breaches; a
    notional not in whole cents, or a fractionable buy by quantity, is refused."""
    orders = [_order("C", "buy", 2, notional=50.0, target_weight=0.04)]
    assert isinstance(_check(orders, account=_account(50.075), costs=_COSTS), Skips)
    assert _rules(_check(orders, account=_account(50.065), costs=_COSTS)) == {"buys_within_cash"}
    tolerant = RiskConfig(reconcile_cash_tolerance=1.0)
    assert _rules(_check(orders, tolerant, account=_account(50.065), costs=_COSTS)) == {
        "buys_within_cash"
    }
    with pytest.raises(ValueError, match="whole cents"):  # never approved unmeasured
        _check([replace(orders[0], notional=50.009)], account=_account(50.075), costs=_COSTS)
    with pytest.raises(ValueError, match="not whole-share"):
        _check([replace(orders[0], notional=None, quantity=1.0)], costs=_COSTS)


def test_a_trim_is_a_quantity_sell_so_a_gap_down_cannot_oversell() -> None:
    """10 shares of D at $100: a $950 trim sells 9.5 shares and a $300 trim 3,
    whatever price the open brings."""
    ledger = _ledger(D=10.0)
    for planned, shares in ((950.0, 9.5), (300.0, 3.0)):
        trim = DecisionRow(
            decision_id=1,
            run_id=1,
            rebalance_session=_S,
            security_id="D",
            side="sell",
            planned_notional=planned,
            whole_share=False,
            decision="trade",
            known_at=_NOW,
            ingested_at=_NOW,
        )
        left = plan_remainder(trim, [], [], [], _NO_ACTIONS, _price_of, session=_S)
        quantity = round_down(left.quantity, 6)
        assert quantity == shares
        order = _order("D", "sell", 1, quantity=quantity)
        assert isinstance(_check([order], _LOOSE, ledger=ledger), Skips)


def test_a_whole_share_full_exit_of_one_share_is_submitted_not_dust() -> None:
    assets = {**_ASSETS, "D": _asset(fractionable=False)}
    one = _order("D", "sell", 1, quantity=1.0, whole_share=True, full_exit=True)
    result = _check([one], ledger=_ledger(D=1.0), assets=assets)
    assert isinstance(result, Skips) and result.skips == ()


def test_every_sized_batch_passes_the_cash_rule() -> None:
    """Property: over random cash, cost rates, commissions and 1 to
    `risk.max_orders_per_run` buys (whole-share names included), the batch
    `size_buys` returns never breaches `buys_within_cash`."""
    rng = random.Random(54)
    frozen = RiskConfig()
    loose = RiskConfig(max_order_notional_fraction=1.0)
    names = list(_PRICES)
    for _ in range(200):
        n = rng.randint(1, frozen.max_orders_per_run)
        buys = [
            _buy(
                i + 1,
                rng.choice(names),
                round(rng.uniform(0.5, 5000.0), 2),
                whole_share=rng.random() < 0.3,
            )
            for i in range(n)
        ]
        cash = round(rng.uniform(0.0, 20000.0), 2)
        costs = BuyCosts(
            per_side_bps=rng.choice([0.0, 15.0, rng.uniform(0.0, 100.0)]),
            commissions=Commissions(
                per_share=rng.choice([0.0, 0.005]), per_order=rng.choice([0.0, 1.0])
            ),
        )
        sizings = size_buys(buys, cash, _price_of, frozen, costs)
        low = min(_PRICES[b.decision.security_id] for b in buys)
        reserved = (n - 1) * costs.commissions.per_order
        expected = buy_notional_after_costs(
            max(cash - reserved, 0.0), costs.per_side_bps, costs.commissions, price=low
        )
        spendable = float(_spendable(Decimal(repr(cash)), n, low, costs))
        assert spendable == pytest.approx(expected, rel=1e-12, abs=1e-9)  # one formula
        by_id = {b.decision.decision_id: b for b in buys}
        orders = [
            _order(
                s.security_id,
                "buy",
                s.decision_id,
                notional=s.notional,
                quantity=s.quantity,
                whole_share=by_id[s.decision_id].by_whole_shares,
                target_weight=0.01,
            )
            for s in sizings
            if not s.deferred
        ]
        ledger = Ledger(positions={}, cash=1e9, through=_S)  # no weight or exposure breach
        assets = {k: _asset(fractionable=True) for k in _PRICES}
        result = _check(
            orders, loose, ledger=ledger, account=_account(cash), costs=costs, assets=assets
        )
        assert "buys_within_cash" not in (
            _rules(result) if isinstance(result, Violations) else set()
        ), (cash, costs, [(b.decision.security_id, b.remainder.notional) for b in buys])


def test_a_rounding_overshoot_comes_off_the_largest_notional_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced: spendable a cent above what $100 holds, so two $60 remainders
    floor to $49.93 each and need $100.0098. The overshoot, rounded up to a
    cent, comes off one buy and the batch fits; a cut below the minimum, or an
    overshoot above a cent (not rounding), raises instead."""
    import tradepartner.execution.risk as risk

    exact = risk._spendable
    monkeypatch.setattr(risk, "_spendable", lambda *a: exact(*a) + Decimal("0.01"))
    even = [_buy(1, "A", 60.0), _buy(2, "B", 60.0)]
    sizings = size_buys(even, 100.0, _price_of, RiskConfig(), _COSTS)
    assert [s.notional for s in sizings] == [49.92, 49.93]
    orders = [
        _order(s.security_id, "buy", s.decision_id, notional=s.notional, target_weight=0.01)
        for s in sizings
    ]
    loose = RiskConfig(max_order_notional_fraction=1.0)
    ledger = Ledger(positions={}, cash=1e6, through=_S)
    assert isinstance(
        _check(orders, loose, ledger=ledger, costs=_COSTS, account=_account(100.0)), Skips
    )
    with pytest.raises(ValueError, match="overshoot"):
        size_buys(even, 100.0, _price_of, RiskConfig(min_order_notional=49.93), _COSTS)
    monkeypatch.setattr(risk, "_spendable", lambda *a: exact(*a) + Decimal("0.05"))
    with pytest.raises(ValueError, match="overshoot"):
        size_buys(even, 100.0, _price_of, RiskConfig(), _COSTS)


# --- a name that lost `fractionable` at phase time (#395, owner answer (b)) -------------------


def test_a_full_exit_of_a_name_that_lost_fractionable_sells_the_whole_holding() -> None:
    """#395: the decision was fractionable; the asset no longer is. A full exit
    still sells all 10.5 shares (by the decision's flag) and never halts on
    `whole_shares`; the same name's trim goes whole-share, 9.5 floored to 9,
    and a fractional trim is refused."""
    assets = {**_ASSETS, "A": _asset(fractionable=False)}
    ledger = _ledger(A=10.5)
    for decision in ("trade", "forced_exit"):
        exit_order = _order("A", "sell", 1, quantity=10.5, decision=decision, full_exit=True)
        result = _check([exit_order], _LOOSE, ledger=ledger, assets=assets)
        assert isinstance(result, Skips), result
        assert [(o.quantity, o.whole_share) for o in result.orders] == [(10.5, False)]
    trim = _order("A", "sell", 1, quantity=9.0)
    passed = _check([trim], _LOOSE, ledger=ledger, assets=assets)
    assert isinstance(passed, Skips)
    assert [o.whole_share for o in passed.orders] == [True]
    fractional_trim = _order("A", "sell", 1, quantity=9.5)
    assert _rules(_check([fractional_trim], _LOOSE, ledger=ledger, assets=assets)) == {
        "whole_shares"
    }


def test_a_full_exit_below_one_share_of_a_name_that_lost_fractionable_is_sold() -> None:
    """0.5 share worth $5, above the $1 minimum: sold, not `dust`; a 0.5-share
    trim of it is `skip_below_one_share`."""
    assets = {**_ASSETS, "A": _asset(fractionable=False)}
    ledger = _ledger(A=0.5)
    exit_order = _order("A", "sell", 1, quantity=0.5, full_exit=True)
    result = _check([exit_order], ledger=ledger, assets=assets)
    assert isinstance(result, Skips) and result.skips == ()
    trim = _order("A", "sell", 1, quantity=0.5)
    assert _skip_reasons(_check([trim], ledger=ledger, assets=assets)) == {
        1: "skip_below_one_share"
    }


def test_a_whole_share_decisions_full_exit_still_goes_by_whole_shares() -> None:
    """A decision journaled `whole_share` sells its whole-share floor (spec
    req 2); a fractional full exit on it is still a `whole_shares` breach."""
    assets = {**_ASSETS, "D": _asset(fractionable=False)}
    ledger = _ledger(D=2.4)
    floor = _order("D", "sell", 1, quantity=2.0, whole_share=True, full_exit=True)
    assert isinstance(_check([floor], _LOOSE, ledger=ledger, assets=assets), Skips)
    fractional = replace(floor, quantity=2.4)
    assert _rules(_check([fractional], _LOOSE, ledger=ledger, assets=assets)) == {"whole_shares"}


def test_a_buy_of_a_name_that_lost_fractionable_flooring_to_zero_is_deferred() -> None:
    """#395: journaled `whole_share = false`, sized by whole shares since the
    name lost `fractionable`: $50 of D at the buffered $102 floors to no
    share, so it is deferred (no order, no skip), not skipped."""
    lost = replace(_buy(1, "D", 50.0), whole_share=True)
    assert lost.decision.whole_share is False and lost.by_whole_shares
    (sizing,) = size_buys([lost], 1000.0, _price_of, RiskConfig(), _NO_COSTS)
    assert sizing.deferred

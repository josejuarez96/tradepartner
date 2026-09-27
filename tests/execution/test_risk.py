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
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from tradepartner.adapters.broker import Account, Asset
from tradepartner.backtest.costs import Commissions, buy_notional_after_costs, trade_cost
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, Remainder
from tradepartner.execution.risk import (
    BuyToSize,
    OpenSell,
    PhaseOrder,
    Skips,
    Violations,
    check_phase,
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
    """$50 at 15 bp costs $50.075: $50.05 of cash is short by more than the
    cash tolerance, $50.075 fits."""
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
    assert _rules(_check(orders, ledger=ledger, quantity_decimals=0)) == {"sell_within_holding"}


# --- sells -----------------------------------------------------------------------------------


def test_a_sell_is_at_most_the_holding_rounded_down() -> None:
    ledger = _ledger(A=1.23456)
    assert isinstance(
        _check([_order("A", "sell", quantity=1.23)], ledger=ledger, quantity_decimals=2), Skips
    )
    over = _check([_order("A", "sell", quantity=1.234)], ledger=ledger, quantity_decimals=2)
    assert _rules(over) == {"sell_within_holding"}
    assert isinstance(
        _check([_order("A", "sell", quantity=1.234)], ledger=ledger, quantity_decimals=3), Skips
    )


def test_a_sell_of_a_name_not_held_is_a_violation() -> None:
    assert _rules(_check([_order("C", "sell", quantity=1.0)])) == {
        "sell_within_holding",
        "sell_sum_within_holding",
    }


def test_a_notional_sell_is_measured_in_shares_at_the_reference_price() -> None:
    assert isinstance(_check([_order("A", "sell", notional=100.0)], _LOOSE), Skips)  # 10 shares
    assert _rules(_check([_order("A", "sell", notional=101.0)], _LOOSE)) == {
        "sell_within_holding",
        "sell_sum_within_holding",
    }


def test_a_second_full_exit_for_one_name_is_a_violation() -> None:
    orders = [_order("A", "sell", 1, quantity=10.0), _order("A", "sell", 2, quantity=10.0)]
    assert _rules(_check(orders, _LOOSE)) == {"sell_sum_within_holding"}


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
    (open_sell,) = unfilled_sells([order], [accepted], [fill], _price_of)
    assert open_sell == OpenSell("A", 2.0)
    # 8 + the 2 unfilled = 10 = the holding: passes; 8.5 + 2 does not
    assert isinstance(
        _check([_order("A", "sell", quantity=8.0)], _LOOSE, open_sells=[open_sell]), Skips
    )
    over = _check([_order("A", "sell", quantity=8.5)], _LOOSE, open_sells=[open_sell])
    assert _rules(over) == {"sell_sum_within_holding"}
    # A terminal sell holds nothing back
    filled = replace(accepted, status="expired")
    assert unfilled_sells([order], [accepted, filled], [fill], _price_of) == []


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
        _order("A", "sell", 6, notional=0.5, full_exit=True),
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
        _order("A", "sell", 3, notional=0.5, full_exit=True),  # dust
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
        assert sizing.notional == pytest.approx(remainder * scale, rel=1e-12)
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

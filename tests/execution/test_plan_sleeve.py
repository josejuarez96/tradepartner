"""The cash sleeve in the plan's buy targets (paper spec amendment 2026-11-01 #2842,
item 3; plan T370).

With c > 0 the long buys' spendable is the cash before the sells plus the plan's
sells, less c x the plan's ledger equity at the plan's marks, floored at zero, so no
target asks for the sleeve; with c = 0 the decisions are today's. The sleeve only ever
shrinks the buys' targets: every sell and every planned notional is unchanged.

Every price is $10 and the 0 bp, no-commission costs make a buy's spendable exactly the
cash plus the plan's sells, so the targets can be checked by hand.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

import polars as pl
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.provider import GapReading
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, Decisions, decisions_from

T = date(2026, 9, 30)  # rebalance session T_i
S = date(2026, 10, 1)  # the run's session S
PRICE = 10.0
SETTINGS = Settings(_env_file=None)
FROZEN = RiskConfig(min_order_notional=0.5)
COSTS = BuyCosts(per_side_bps=0.0, commissions=Commissions(per_share=0.0, per_order=0.0))
ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime("us", "UTC"),
    }
)


def _price_of(security_id: str) -> float:
    return PRICE


def _plan(targets: Mapping[str, float]) -> Plan:
    members = tuple(sorted({*targets, "A", "B", "C"}))
    return Plan(
        session=T,
        fill_session=S,
        targets=dict(targets),
        n_universe=len(members),
        n_static_listings=0,
        n_excluded_no_history=0,
        gap=GapReading(count_share=0.0, size_share=0.0),
        members=members,
        scores={sid: 1.0 for sid in members},
        excluded_no_history=(),
        exclusions={"no_history": ()},
    )


def _decide(plan: Plan, ledger: Ledger, **kwargs: Any) -> Decisions:
    assets = {
        sid: Asset(tradable=True, fractionable=True, status="active", cusip=None) for sid in "ABC"
    }
    return decisions_from(
        plan,
        ledger,
        (),
        assets,
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=_price_of,
        actions_as_of=ACTIONS,
        costs=COSTS,
        **kwargs,
    )


def _by_name(decided: Decisions) -> dict[str, Any]:
    return {d.security_id: d for d in decided.decisions}


#: Cash 100, A 10 shares ($100), B 5 shares ($50): equity 250. A leaves the targets
#: (a $100 sell), B buys 0.3 x 250 = 75 and C 0.5 x 250 = 125: the buys' 200 is exactly
#: the cash plus the sell, so with c = 0 every target is its planned notional.
LEDGER = Ledger(positions={"A": 10.0, "B": 5.0}, cash=100.0, through=S)
PLAN = _plan({"B": 0.5, "C": 0.5})
EQUITY = 250.0


def test_c_zero_is_todays_decisions() -> None:
    assert _decide(PLAN, LEDGER, sleeve_fraction=0.0) == _decide(PLAN, LEDGER)
    today = _by_name(_decide(PLAN, LEDGER))
    assert today["B"].target_notional == pytest.approx(75.0)
    assert today["C"].target_notional == pytest.approx(125.0)


def test_targets_leave_c_times_equity() -> None:
    c = 0.10
    decided = _by_name(_decide(PLAN, LEDGER, sleeve_fraction=c))
    spendable = 100.0 + 100.0 - c * EQUITY  # 175
    assert decided["B"].target_notional == pytest.approx(75.0 * spendable / 200.0)
    assert decided["C"].target_notional == pytest.approx(125.0 * spendable / 200.0)
    total = decided["B"].target_notional + decided["C"].target_notional
    assert total == pytest.approx(spendable)
    assert 100.0 + 100.0 - total == pytest.approx(c * EQUITY)


def test_sleeve_shrinks_buys_never_a_sell() -> None:
    today = _by_name(_decide(PLAN, LEDGER))
    sleeved = _by_name(_decide(PLAN, LEDGER, sleeve_fraction=0.10))
    # The sell is the same decision: no field moves.
    assert sleeved["A"] == today["A"]
    assert sleeved["A"].side == "sell"
    for sid in ("B", "C"):
        assert sleeved[sid].planned_notional == today[sid].planned_notional
        assert sleeved[sid].decision == today[sid].decision
        assert sleeved[sid].target_notional < today[sid].target_notional


def test_sleeve_larger_than_spendable_floors_targets_at_zero() -> None:
    # Cash 10, B 24 shares ($240): equity 250, sleeve 0.10 x 250 = 25 > the 10 the buy
    # could spend, so the buy's target is 0, never negative and never a refusal.
    ledger = Ledger(positions={"B": 24.0}, cash=10.0, through=S)
    plan = _plan({"B": 0.96, "C": 0.04})
    decided = _by_name(_decide(plan, ledger, sleeve_fraction=0.10))
    assert decided["C"].side == "buy"
    assert decided["C"].planned_notional == pytest.approx(10.0)
    assert decided["C"].target_notional == 0.0


def test_a_sleeve_inside_the_slack_leaves_targets_whole() -> None:
    # Cash 150, equity 300: B buys 0.3 x 300 - 50 = 40 and C 90; the spendable 250 less
    # a 0.10 sleeve (30) is 220, still above the buys' 130.
    ledger = Ledger(positions={"A": 10.0, "B": 5.0}, cash=150.0, through=S)
    decided = _by_name(_decide(_plan({"B": 0.3, "C": 0.3}), ledger, sleeve_fraction=0.10))
    for sid in ("B", "C"):
        assert decided[sid].target_notional == decided[sid].planned_notional


@pytest.mark.parametrize("bad", [-0.01, 1.5, math.nan, math.inf])
def test_sleeve_fraction_out_of_range_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="sleeve_fraction"):
        _decide(PLAN, LEDGER, sleeve_fraction=bad)

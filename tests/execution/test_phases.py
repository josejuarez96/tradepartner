"""Phase orders, pure: sells and buys (Phase 4 plan T60c; spec req 3).

Hand-built decisions, ledgers and journal rows; each decision's state comes
from the real `plan.decision_state`, as the wrapper will compute it. The
broker-facing cases are T60b's and T60e's.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import polars as pl
import pytest

from tradepartner.adapters.broker import Account, Asset, Side
from tradepartner.backtest.costs import Commissions
from tradepartner.config import RiskConfig
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.phases import (
    PhaseOrder,
    PhaseOrders,
    buy_orders,
    requests_for,
    sell_orders,
)
from tradepartner.execution.plan import BuyCosts, DecisionState, PriceOf, State, decision_state
from tradepartner.execution.risk import OpenSell, Skips, check_phase, open_sold
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

T0 = date(2026, 10, 1)  # rebalance session
S = date(2026, 10, 2)  # its fill session, the run's session
CLOSE_T0 = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)  # close(S-1)
PRICE = 100.0
FROZEN = RiskConfig()  # min_order_notional 1.0, whole_share_price_buffer 0.02
DECIMALS = 9
NO_COSTS = BuyCosts(0.0, Commissions(0.0, 0.0))
COSTS = BuyCosts(10.0, Commissions(0.0, 0.0))  # 10 bps a side
TRADABLE = Asset(tradable=True, fractionable=True, status="active", cusip=None)
WHOLE = Asset(tradable=True, fractionable=False, status="active", cusip=None)
HALTED = Asset(tradable=False, fractionable=True, status="active", cusip=None)
ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime(time_zone="UTC"),
    }
)
STAMP = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
NO_SELLS = PhaseOrders((), ())


def _price(_sid: str) -> float:
    return PRICE


def _d(
    decision_id: int,
    side: str,
    *,
    sid: str | None = None,
    decision: str = "trade",
    reason: str | None = None,
    notional: float | None = None,
    quantity: float | None = None,
    target: float | None = None,
    whole_share: bool = False,
) -> DecisionRow:
    forced = decision == "forced_exit"
    return DecisionRow(
        decision_id=decision_id,
        run_id=1,
        rebalance_session=None if forced else T0,
        security_id=sid or f"SEC_{decision_id}",
        target_weight=0.04 if side == "buy" else 0.0,
        side=side,
        planned_notional=target if side == "buy" else notional,
        planned_quantity=quantity,
        target_notional=target,
        whole_share=whole_share,
        decision=decision,
        reason=reason,
        known_at=STAMP if not forced else datetime(2026, 10, 2, 13, 0, tzinfo=UTC),
        ingested_at=STAMP,
    )


def _states(
    decisions: Sequence[DecisionRow],
    orders: Sequence[OrderRow] = (),
    events: Sequence[OrderEventRow] = (),
    fills: Sequence[OrderedFill] = (),
    price_of: PriceOf = _price,
) -> dict[int, DecisionState]:
    out = {}
    for d in decisions:
        assert d.decision_id is not None
        out[d.decision_id] = decision_state(
            d, (), orders, events, fills, ACTIONS, price_of, FROZEN, session=S
        )
    return out


def _ledger(positions: dict[str, float], cash: float = 0.0) -> Ledger:
    return Ledger(positions=positions, cash=cash, through=S)


def _sells(
    decisions: Sequence[DecisionRow],
    held: dict[str, float],
    *,
    forced: Sequence[DecisionRow] = (),
    residues: dict[str, float] | None = None,
    assets: dict[str, Asset] | None = None,
    frozen: RiskConfig = FROZEN,
    actions: pl.DataFrame = ACTIONS,
    price_of: PriceOf = _price,
    open_sells: Sequence[OpenSell] = (),
) -> PhaseOrders:
    rows = [*decisions, *forced]
    return sell_orders(
        decisions,
        _states(rows, price_of=price_of),
        _ledger(held),
        residues or {},
        forced,
        actions,
        price_of,
        assets or {d.security_id: TRADABLE for d in rows},
        frozen,
        session=S,
        quantity_decimals=DECIMALS,
        open_sells=open_sells,
    )


def _one(result: PhaseOrders) -> PhaseOrder:
    assert len(result.orders) == 1, result
    return result.orders[0]


def _reasons(result: PhaseOrders) -> dict[str, str]:
    return {s.security_id: s.reason for s in result.skips}


# --- sells ------------------------------------------------------------------------


def test_trims_sell_their_remainder_as_a_quantity_at_the_reference_price() -> None:
    # 10 shares at $100: a $950 trim is 9.5 shares, a $300 one 3, never a notional.
    a, b = _d(1, "sell", notional=950.0), _d(2, "sell", notional=300.0)
    result = _sells([a, b], {"SEC_1": 10.0, "SEC_2": 10.0})
    assert [(o.quantity, o.notional, o.full_exit) for o in result.orders] == [
        (9.5, None, False),
        (3.0, None, False),
    ]


def test_full_exits_sell_the_holding_less_the_residue_except_delisted_and_receipts() -> None:
    plan = _d(1, "sell", reason="left_targets", quantity=10.0)
    stop = _d(2, "sell", decision="forced_exit", reason="window_stop", quantity=4.0)
    delisted = _d(3, "sell", decision="forced_exit", reason="delisted", quantity=7.0)
    receipt = _d(4, "sell", decision="forced_exit", reason="untargeted_receipt", quantity=3.0)
    held = {"SEC_1": 10.0, "SEC_2": 5.0, "SEC_3": 7.0, "SEC_4": 3.0}
    residues = {"SEC_1": 0.5, "SEC_2": 1.0, "SEC_3": 2.0, "SEC_4": 1.0}
    result = _sells([plan], held, forced=[stop, delisted, receipt], residues=residues)
    assert [(o.security_id, o.quantity, o.full_exit) for o in result.orders] == [
        ("SEC_1", 9.5, True),
        ("SEC_2", 4.0, True),
        ("SEC_3", 7.0, True),
        ("SEC_4", 3.0, True),
    ]


def test_whole_share_full_exits_floor_by_the_rows_flag_and_a_sub_share_one_is_dust() -> None:
    small, big = (
        _d(
            i,
            "sell",
            decision="forced_exit",
            reason="untargeted_receipt",
            quantity=q,
            whole_share=True,
        )
        for i, q in ((1, 0.4), (2, 2.4))
    )
    # The asset is fractionable now: the journaled flag decides.
    result = _sells([], {"SEC_1": 0.4, "SEC_2": 2.4}, forced=[small, big])
    order = _one(result)
    assert (order.security_id, order.quantity, order.whole_share) == ("SEC_2", 2.0, True)
    assert _reasons(result) == {"SEC_1": "dust"}
    assert not result.skips[0].counts_toward_cap


def test_a_full_exit_of_a_name_that_lost_fractionable_sells_its_fraction() -> None:
    plan = _d(1, "sell", reason="left_universe", quantity=10.5)
    order = _one(_sells([plan], {"SEC_1": 10.5}, assets={"SEC_1": WHOLE}))
    assert (order.quantity, order.whole_share) == (10.5, False)


def test_a_trim_of_a_name_that_lost_fractionable_floors_and_a_sub_share_one_skips() -> None:
    a, b = _d(1, "sell", notional=950.0), _d(2, "sell", notional=50.0)
    result = _sells([a, b], {"SEC_1": 10.0, "SEC_2": 10.0}, assets={"SEC_1": WHOLE, "SEC_2": WHOLE})
    order = _one(result)
    assert (order.security_id, order.quantity, order.whole_share) == ("SEC_1", 9.0, True)
    assert _reasons(result) == {"SEC_2": "skip_below_one_share"}
    assert result.skips[0].counts_toward_cap


def test_untradable_names_skip_and_a_forced_exit_is_exempt_from_the_cap() -> None:
    trim = _d(1, "sell", notional=300.0)
    exit_ = _d(2, "sell", decision="forced_exit", reason="delisted", quantity=5.0)
    result = _sells(
        [trim],
        {"SEC_1": 10.0, "SEC_2": 5.0},
        forced=[exit_],
        assets={"SEC_1": HALTED, "SEC_2": HALTED},
    )
    assert result.orders == ()
    assert _reasons(result) == {"SEC_1": "skip_untradable", "SEC_2": "untradable"}
    assert [s.counts_toward_cap for s in result.skips] == [True, False]


def test_an_untradable_name_with_no_price_skips_without_halting_the_phase() -> None:
    """#518 item 1: the tradable skip comes before the price read, so a halted
    name with no reference price on S skips and the rest of the phase sells."""
    halted = _d(1, "sell", reason="left_targets", quantity=5.0)
    exit_ = _d(2, "sell", decision="forced_exit", reason="delisted", quantity=5.0)
    trim = _d(3, "sell", notional=300.0)

    def price_of(sid: str) -> float:
        if sid in ("SEC_1", "SEC_2"):
            raise ValueError(f"no reference price for {sid}")
        return PRICE

    result = sell_orders(
        [halted, trim],
        _states([halted, trim, exit_]),
        _ledger({"SEC_1": 5.0, "SEC_2": 5.0, "SEC_3": 10.0}),
        {},
        [exit_],
        ACTIONS,
        price_of,
        {"SEC_1": HALTED, "SEC_2": HALTED, "SEC_3": TRADABLE},
        FROZEN,
        session=S,
        quantity_decimals=DECIMALS,
    )
    assert (_one(result).security_id, _one(result).quantity) == ("SEC_3", 3.0)
    assert _reasons(result) == {"SEC_1": "skip_untradable", "SEC_2": "untradable"}


@pytest.mark.parametrize(("residue", "capped"), [(0.0, 10.0), (0.5, 9.5)])
def test_a_trim_after_a_price_drop_is_capped_at_the_holding_less_residue(
    residue: float, capped: float
) -> None:
    """#518 item 7 (owner decision): a $950 trim planned at $100 is 19 shares at
    $50 on S, above the 10 held. It sells the holding less the residue and
    passes `check_phase`, instead of breaching `sell_within_holding`."""
    trim = _d(1, "sell", notional=950.0)
    held = {"SEC_1": 10.0}

    def dropped(_sid: str) -> float:
        return PRICE / 2

    result = _sells([trim], held, residues={"SEC_1": residue}, price_of=dropped)
    order = _one(result)
    assert (order.quantity, order.full_exit) == (capped, False)
    checked = check_phase(
        [order.to_risk("AAA", listing_ended=False)],
        _ledger(held, 100_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_1": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=dropped,
        costs=NO_COSTS,
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 1


def test_a_trim_is_also_capped_by_the_names_open_sells_from_an_earlier_session() -> None:
    """#605 owner decision: a trim's cap also subtracts the name's open
    (non-terminal) sells from earlier sessions, so the sell sum stays within
    the holding and `check_phase`'s `sell_sum_within_holding` (which adds
    those open sells back in) passes it."""
    trim = _d(1, "sell", notional=950.0)  # 9.5 shares at $100 on a 10-share holding
    held = {"SEC_1": 10.0}
    # An earlier session's forced exit left 4 shares open at the broker.
    result = _sells([trim], held, open_sells=[OpenSell("SEC_1", 4.0)])
    order = _one(result)
    assert (order.quantity, order.full_exit) == (6.0, False)  # 10 - 4, not 9.5
    checked = check_phase(
        [order.to_risk("AAA", listing_ended=False)],
        _ledger(held, 100_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_1": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
        open_sells=[OpenSell("SEC_1", 4.0)],
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 1


@pytest.mark.parametrize(
    ("held_qty", "open_qty", "notional", "whole"),
    [
        (44.138, 11.2, 3300.0, False),  # reviewer's reproduction: float sum is 1 ulp over
        (62.1089, 8.59, 5400.0, False),
        (490.58, 201.58, 29_000.0, True),  # a whole-share trim, same float-sum failure
    ],
)
def test_a_fractional_trim_capped_by_open_sells_always_passes_check_phase(
    held_qty: float, open_qty: float, notional: float, whole: bool
) -> None:
    """#605 pass 1 SHOULD FIX: the old `float` cap (`held - residue - open`)
    could land a rounding ulp over the holding once `check_phase` added the
    open sells back in `float` too, halting the batch on
    `sell_sum_within_holding` for the exact case #605 was meant to fix. The
    cap is now exact in `Decimal` on both sides (`_trim_cap` here,
    `sell_sum_within_holding`'s own `Decimal` sum in `risk.py`), so this can
    never happen."""
    trim = _d(1, "sell", notional=notional, whole_share=whole)
    held = {"SEC_1": held_qty}
    result = _sells([trim], held, open_sells=[OpenSell("SEC_1", open_qty)])
    order = _one(result)
    checked = check_phase(
        [order.to_risk("AAA", listing_ended=False)],
        _ledger(held, 10_000_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_1": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
        open_sells=[OpenSell("SEC_1", open_qty)],
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 1


def test_a_trim_capped_by_two_off_grid_open_sells_passes_check_phase() -> None:
    """#605 review pass 2: the name's open sells are summed exactly too. Two
    off-grid unfilled quantities summed in `float` land under their exact sum,
    so the cap came out a rounding ulp high and `sell_sum_within_holding`
    halted the batch."""
    trim = _d(1, "sell", notional=10_000_000.0)
    held = {"SEC_1": 437.78}
    opens = [OpenSell("SEC_1", 54.62566982250126), OpenSell("SEC_1", 120.92920606749875)]
    order = _one(_sells([trim], held, open_sells=opens))
    checked = check_phase(
        [order.to_risk("AAA", listing_ended=False)],
        _ledger(held, 10_000_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_1": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
        open_sells=opens,
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 1


def test_a_trim_cut_to_zero_by_open_sells_alone_is_held_not_skipped() -> None:
    """#605 pass 1 SHOULD FIX: the open-sells subtraction alone (cap would be
    negative, floored at 0) wipes out the trim; a skip here would close the
    decision for good (`plan._CLOSING_EVENTS`), so instead it is held — no
    order, no skip — and the next run re-attempts the same remainder once the
    open sell terminates (the owner's "heals itself" decision)."""
    trim = _d(1, "sell", notional=950.0)
    result = _sells([trim], {"SEC_1": 10.0}, open_sells=[OpenSell("SEC_1", 50.0)])
    assert result.orders == () and result.skips == ()


def test_a_trim_below_the_minimum_even_without_open_sells_still_skips() -> None:
    """A skip the residue cap alone would have produced (not the open-sells
    subtraction) keeps today's behavior: journaled and closed."""
    trim = _d(1, "sell", notional=950.0)
    result = _sells(
        [trim],
        {"SEC_1": 10.0},
        open_sells=[OpenSell("SEC_1", 50.0)],
        frozen=RiskConfig(min_order_notional=2000.0),
    )
    assert result.orders == ()
    assert _reasons(result) == {"SEC_1": "skip_below_minimum"}
    assert result.skips[0].counts_toward_cap


def test_a_full_exit_is_unaffected_by_the_names_open_sells() -> None:
    """The #605 owner decision is about trims; a full exit still sells the
    whole holding less residue regardless of open sells."""
    plan = _d(1, "sell", reason="left_targets", quantity=10.0)
    order = _one(_sells([plan], {"SEC_1": 10.0}, open_sells=[OpenSell("SEC_1", 4.0)]))
    assert (order.quantity, order.full_exit) == (10.0, True)


#: What `unfilled_sells` leaves for a sell of 1.0 filled 0.7 + 0.2 + 0.1 with
#: no terminal event journaled yet: float noise, not an open sell.
NOISE = 1.0 - (0.7 + 0.2 + 0.1)


def test_open_sold_snaps_float_noise_to_the_quantity_grid() -> None:
    """#605 /code-review: the exact sum must not count `unfilled_sells`'
    float noise, on either side of a grid step."""
    sums = open_sold(
        [OpenSell("SEC_1", NOISE), OpenSell("SEC_2", 1.0 - 0.7), OpenSell("SEC_2", 0.2)],
        DECIMALS,
    )
    assert NOISE > 0
    assert sums == {"SEC_1": Decimal(0), "SEC_2": Decimal("0.5")}


def test_a_full_exit_beside_a_noise_open_sell_passes_check_phase() -> None:
    """#605 /code-review: the exact `sell_sum_within_holding` sum must not halt
    a full exit on the ~1e-16 a fully filled sell leaves before its terminal
    event (the old `float` sum ignored it)."""
    plan = _d(1, "sell", reason="left_targets", quantity=10.0)
    held = {"SEC_1": 10.0}
    opens = [OpenSell("SEC_1", NOISE)]
    order = _one(_sells([plan], held, open_sells=opens))
    checked = check_phase(
        [order.to_risk("AAA", listing_ended=False)],
        _ledger(held, 10_000_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_1": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
        open_sells=opens,
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 1


def test_a_whole_share_trim_keeps_its_share_beside_a_noise_open_sell() -> None:
    trim = _d(1, "sell", notional=10_000_000.0, whole_share=True)
    order = _one(_sells([trim], {"SEC_1": 10.0}, open_sells=[OpenSell("SEC_1", NOISE)]))
    assert order.quantity == 10.0


def test_a_trim_capped_on_a_whole_share_basis_floors_the_cap() -> None:
    trim = _d(1, "sell", notional=950.0, whole_share=True)
    order = _one(
        _sells([trim], {"SEC_1": 10.0}, residues={"SEC_1": 0.5}, price_of=lambda _s: PRICE / 2)
    )
    assert (order.quantity, order.whole_share) == (9.0, True)


def test_a_full_exit_below_the_minimum_is_dust() -> None:
    plan = _d(1, "sell", reason="left_targets", quantity=0.2)
    result = _sells([plan], {"SEC_1": 0.2}, frozen=RiskConfig(min_order_notional=30.0))
    assert result.orders == ()
    assert _reasons(result) == {"SEC_1": "dust"}


def test_a_second_sell_for_one_name_is_never_produced() -> None:
    plan = _d(1, "sell", sid="X", reason="left_targets", quantity=10.0)
    exit_ = _d(2, "sell", sid="X", decision="forced_exit", reason="delisted", quantity=10.0)
    with pytest.raises(ValueError, match="two sell decisions for \\['X'\\]"):
        _sells([plan], {"X": 10.0}, forced=[exit_])


def test_a_name_with_a_sell_in_flight_gets_no_second_sell() -> None:
    plan = _d(1, "sell", sid="X", reason="left_targets", quantity=10.0)
    exit_ = _d(2, "sell", sid="X", decision="forced_exit", reason="delisted", quantity=10.0)
    order = _order(plan, quantity=10.0)  # no event: in flight
    states = _states([plan, exit_], [order])
    with pytest.raises(ValueError, match="two sell decisions"):
        sell_orders(
            [plan],
            states,
            _ledger({"X": 10.0}),
            {},
            [exit_],
            ACTIONS,
            _price,
            {"X": TRADABLE},
            FROZEN,
            session=S,
            quantity_decimals=DECIMALS,
        )


def test_a_split_known_after_close_s_minus_1_is_refused() -> None:
    trim = _d(1, "sell", notional=300.0)
    split = {
        "security_id": ["SEC_1"],
        "action_type": ["split"],
        "ex_date": [S],
        "ratio_or_amount": [2.0],
    }
    on_time = pl.DataFrame({**split, "known_at": [CLOSE_T0]}, schema=ACTIONS.schema)
    assert _one(_sells([trim], {"SEC_1": 10.0}, actions=on_time)).quantity == 3.0
    late = pl.DataFrame(
        {**split, "known_at": [datetime(2026, 10, 2, 12, 0, tzinfo=UTC)]}, schema=ACTIONS.schema
    )
    with pytest.raises(ValueError, match="not known by close"):
        _sells([trim], {"SEC_1": 10.0}, actions=late)


def test_the_sells_pass_the_risk_check_and_the_sell_sum_rule() -> None:
    trim = _d(1, "sell", notional=950.0)
    exit_ = _d(2, "sell", decision="forced_exit", reason="delisted", quantity=1 / 3)
    held = {"SEC_1": 10.0, "SEC_2": 1 / 3}
    result = _sells([trim], held, forced=[exit_])
    batch = [o.to_risk(o.security_id, listing_ended=False) for o in result.orders]
    account = Account("PA1", 0.0, 0.0, 0.0, STAMP)
    checked = check_phase(
        batch,
        _ledger(held, 100_000.0),
        account,
        {k: TRADABLE for k in held},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
    )
    assert isinstance(checked, Skips) and len(checked.orders) == 2


# --- buys -------------------------------------------------------------------------


def _buys(
    decisions: Sequence[DecisionRow],
    cash: float,
    *,
    costs: BuyCosts = COSTS,
    assets: dict[str, Asset] | None = None,
    sells: PhaseOrders = NO_SELLS,
    frozen: RiskConfig = FROZEN,
    states: dict[int, DecisionState] | None = None,
    ended: Collection[str] = (),
) -> PhaseOrders:
    prices = {"SEC_1": 100.0, "SEC_2": 40.0, "SEC_3": 50.0}
    return buy_orders(
        decisions,
        states or _states(decisions),
        cash,
        sells,
        lambda sid: prices.get(sid, PRICE),
        costs,
        assets or {d.security_id: TRADABLE for d in decisions},
        frozen,
        session=S,
        ended=ended,
    )


THREE = [
    _d(1, "buy", target=1000.0),
    _d(2, "buy", target=500.0),
    _d(3, "buy", target=300.0, whole_share=True),
]


def test_the_three_name_formula_scales_alike_and_buys_whole_shares_last() -> None:
    result = _buys(THREE, 1500.0)
    # spendable = 1500 / 1.001; scale = spendable / 1800 (cost deducted once).
    scale = (1500 / 1.001) / 1800
    assert [(o.security_id, o.notional, o.quantity) for o in result.orders] == [
        ("SEC_1", math.floor(1000 * scale * 100) / 100, None),
        ("SEC_2", math.floor(500 * scale * 100) / 100, None),
        ("SEC_3", None, math.floor(300 * scale / (50 * 1.02))),  # 4 shares, buffered
    ]
    spent = (832.50 + 416.25) * 1.001 + 4 * 50 * 1.02 * 1.001
    assert result.cash_left == pytest.approx(1500 - spent)
    assert result.cash_left is not None and result.cash_left >= 0
    assert result.skips == () and result.deferred == ()


def test_forced_exit_proceeds_in_cash_never_raise_a_buy_above_its_target() -> None:
    exit_ = _d(9, "sell", decision="forced_exit", reason="delisted", quantity=50.0)
    sells = _sells([], {"SEC_9": 50.0}, forced=[exit_])
    assert _one(sells).quantity == 50.0  # $5,000 of proceeds now in cash
    result = _buys(THREE, 1500.0 + 5000.0, sells=sells)
    assert [(o.notional, o.quantity) for o in result.orders] == [
        (1000.0, None),
        (500.0, None),
        (None, 5.0),  # floor(300 / 51)
    ]


def test_a_buy_scaled_below_the_minimum_is_deferred_with_no_skip() -> None:
    small = _d(4, "buy", sid="SEC_4", target=20.0)
    frozen = RiskConfig(min_order_notional=15.0)
    result = _buys([*THREE[:2], small], 760.0, costs=NO_COSTS, frozen=frozen)
    assert result.deferred == (4,)
    assert result.skips == ()
    assert [o.decision_id for o in result.orders] == [1, 2]


def test_a_whole_share_buy_that_does_not_fit_is_deferred_and_cash_left_is_not_negative() -> None:
    result = _buys([THREE[2]], 40.0, costs=NO_COSTS)
    assert (result.orders, result.deferred, result.cash_left) == ((), (3,), 40.0)
    assert _buys(THREE, 0.0).cash_left == 0.0


def test_an_untradable_buy_skips_and_a_lost_fractionable_one_goes_by_whole_shares() -> None:
    assets = {"SEC_1": HALTED, "SEC_2": WHOLE, "SEC_3": TRADABLE}
    result = _buys(THREE[:2], 10_000.0, costs=NO_COSTS, assets=assets)
    assert _reasons(result) == {"SEC_1": "skip_untradable"}
    order = _one(result)
    assert (order.quantity, order.notional, order.whole_share) == (12.0, None, True)


def test_an_ended_name_skips_before_sizing_and_leaves_the_other_buys_unchanged() -> None:
    """#604: a buy of a name whose listing ended is dropped before `size_buys`
    runs, so it takes no share of the other buys' sizing (unlike leaving it to
    `check_phase`, which would size it in, then skip it, shrinking the rest)."""
    without_ended = _buys(THREE[:2], 1500.0, ended=())
    with_ended = _buys(THREE, 1500.0, ended={"SEC_3"})
    assert [(o.security_id, o.notional) for o in with_ended.orders] == [
        (o.security_id, o.notional) for o in without_ended.orders
    ]
    assert _reasons(with_ended) == {"SEC_3": "skip_delisted"}
    assert with_ended.skips[0].counts_toward_cap


def test_an_ended_names_skip_reason_and_cap_count_match_check_phase_today() -> None:
    """A wrapper-level equivalent: `check_phase` itself would skip an ended
    buy as `skip_delisted`, counted toward the cap exactly once; `buy_orders`
    now does the same before sizing, so the counted total is unchanged."""
    buy = THREE[2]
    built = _buys([buy], 1000.0, ended={"SEC_3"})
    assert _reasons(built) == {"SEC_3": "skip_delisted"}
    assert sum(s.counts_toward_cap for s in built.skips) == 1

    candidate = PhaseOrder(
        buy.decision_id or 0,
        "SEC_3",
        "buy",
        "trade",
        PRICE,
        notional=300.0,
        target_weight=buy.target_weight,
    )
    checked = check_phase(
        [candidate.to_risk("CCC", listing_ended=True)],
        _ledger({}, 100_000.0),
        Account("PA1", 0.0, 0.0, 0.0, STAMP),
        {"SEC_3": TRADABLE},
        FROZEN,
        DECIMALS,
        price_of=_price,
        costs=NO_COSTS,
    )
    assert isinstance(checked, Skips)
    assert _reasons(PhaseOrders((), checked.skips)) == {"SEC_3": "skip_delisted"}
    assert sum(s.counts_toward_cap for s in checked.skips) == 1


def test_a_whole_share_buy_whose_floor_residue_is_below_one_buffered_share_settles() -> None:
    buy = THREE[2]
    order = _order(buy, quantity=5.0, side="buy")
    events = _events(order, "filled")
    fills = [_fill(order, 5.0, 50.0)]  # 250 of 300: a 50 residue, under 51
    prices = {"SEC_3": 50.0}
    state = decision_state(
        buy, (), [order], events, fills, ACTIONS, prices.__getitem__, FROZEN, session=S
    )
    assert state.state is State.SETTLED
    assert _buys([buy], 1000.0, states={3: state}).orders == ()


def test_a_buy_of_a_name_the_phase_sells_is_refused() -> None:
    trim = _d(1, "sell", sid="SEC_2", notional=300.0)
    sells = _sells([trim], {"SEC_2": 10.0})
    with pytest.raises(ValueError, match="both sells and buys"):
        _buys(THREE[:2], 1000.0, sells=sells)


def test_a_buy_of_a_name_the_sells_phase_skipped_is_refused() -> None:
    """#518 item 3: a sells-phase skip of the name counts as selling it."""
    trim = _d(9, "sell", sid="SEC_2", notional=300.0)
    sells = _sells([trim], {"SEC_2": 10.0}, assets={"SEC_2": HALTED})
    assert sells.orders == () and _reasons(sells) == {"SEC_2": "skip_untradable"}
    with pytest.raises(ValueError, match="both sells and buys \\['SEC_2'\\]"):
        _buys(THREE[:2], 1000.0, sells=sells)


def test_a_buy_of_a_name_with_a_sell_in_flight_is_refused() -> None:
    """#518 item 3: an in-flight sell decision in `decisions` (an earlier
    attempt still open at the broker) counts as selling the name."""
    trim = _d(9, "sell", sid="SEC_2", notional=300.0)
    order = _order(trim, quantity=3.0)  # no event: in flight
    rows = [*THREE[:2], trim]
    with pytest.raises(ValueError, match="both sells and buys \\['SEC_2'\\]"):
        _buys(rows, 1000.0, states=_states(rows, [order]))


# --- requests ---------------------------------------------------------------------


def _order(
    decision: DecisionRow, *, quantity: float, side: str = "sell", attempt: int = 1
) -> OrderRow:
    assert decision.decision_id is not None
    return OrderRow(
        client_order_id=client_order_id("tp", S, decision.security_id, side, attempt),
        decision_id=decision.decision_id,
        run_id=1,
        session=S,
        attempt=attempt,
        phase=side,
        security_id=decision.security_id,
        symbol=decision.security_id,
        side=side,
        quantity=quantity,
        sells_in_flight_at_submit=False,
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _events(order: OrderRow, *chain: str) -> list[OrderEventRow]:
    return [
        OrderEventRow(
            client_order_id=order.client_order_id, status=status, known_at=STAMP, ingested_at=STAMP
        )
        for status in ("pending", "accepted", *chain)
    ]


def _fill(order: OrderRow, quantity: float, price: float) -> OrderedFill:
    row = FillRow(
        fill_id=1,
        client_order_id=order.client_order_id,
        filled_at=STAMP,
        quantity=quantity,
        price=price,
        price_implied=False,
        broker_fill_id="bf-1",
        source="broker_feed",
        known_at=STAMP,
        ingested_at=STAMP,
    )
    return OrderedFill(row, order.side, order.security_id, order.symbol, 1, 1)


SELL = PhaseOrder(1, "SEC_1", "sell", "trade", PRICE, quantity=3.0)
BUY = PhaseOrder(2, "SEC_2", "buy", "trade", PRICE, notional=500.0, target_weight=0.04)
LISTINGS = {"SEC_1": "AAA", "SEC_2": "BBB"}


def test_requests_carry_the_listing_symbol_and_ids_counted_per_attempt() -> None:
    earlier = _order(_d(1, "sell"), quantity=1.0)  # an expired attempt earlier on S
    requests = requests_for(
        PhaseOrders((SELL, BUY), (), session=S), LISTINGS, "tp", [earlier], None, session=S
    )
    assert [(r.client_order_id, r.symbol, r.side, r.quantity, r.notional) for r in requests] == [
        ("tp-20261002-SEC_1-sell-2", "AAA", Side.SELL, 3.0, None),
        ("tp-20261002-SEC_2-buy-1", "BBB", Side.BUY, None, 500.0),
    ]


def test_a_name_unknown_to_the_master_raises_before_any_request() -> None:
    with pytest.raises(ValueError, match="no listing known at close\\(S-1\\) for \\['SEC_2'\\]"):
        requests_for(
            PhaseOrders((SELL, BUY), (), session=S), {"SEC_1": "AAA"}, "tp", [], None, session=S
        )


def test_an_over_long_id_is_refused_when_max_length_is_set_and_accepted_when_none() -> None:
    batch = PhaseOrders((SELL,), (), session=S)
    length = len("tp-20261002-SEC_1-sell-1")
    assert len(requests_for(batch, LISTINGS, "tp", [], length, session=S)) == 1
    assert len(requests_for(batch, LISTINGS, "tp", [], None, session=S)) == 1
    with pytest.raises(ValueError, match="longer than"):
        requests_for(batch, LISTINGS, "tp", [], length - 1, session=S)


def test_a_sell_carrying_a_notional_and_a_stray_session_row_are_refused() -> None:
    notional_sell = PhaseOrder(1, "SEC_1", "sell", "trade", PRICE, notional=300.0)
    with pytest.raises(ValueError, match="carries a notional"):
        requests_for(
            PhaseOrders((notional_sell,), (), session=S), LISTINGS, "tp", [], None, session=S
        )
    stray = OrderRow(**{**_order(_d(1, "sell"), quantity=1.0).__dict__, "session": T0})
    with pytest.raises(ValueError, match="not 2026-10-02"):
        requests_for(PhaseOrders((SELL,), (), session=S), LISTINGS, "tp", [stray], None, session=S)


def test_orders_built_for_another_session_are_refused() -> None:
    """#518 item 4: `requests_for`'s session is the one its orders were built
    for; orders of T0, or with no session, never get S's client order ids."""
    for built_for in (T0, None):
        with pytest.raises(ValueError, match="built for"):
            requests_for(
                PhaseOrders((SELL,), (), session=built_for), LISTINGS, "tp", [], None, session=S
            )
    assert _sells([_d(1, "sell", notional=300.0)], {"SEC_1": 10.0}).session == S
    assert _buys(THREE[:1], 1000.0).session == S


def test_buys_refuse_a_sells_phase_of_another_session() -> None:
    trim = _d(9, "sell", sid="SEC_5", notional=300.0)
    sells = _sells([trim], {"SEC_5": 10.0})
    with pytest.raises(ValueError, match="sells phase of"):
        _buys(THREE[:1], 1000.0, sells=PhaseOrders(sells.orders, (), session=T0))

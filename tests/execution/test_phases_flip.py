"""A flip's close sells the residue (#1959) and the buy phase re-checks
`skip_short_price` (#1969), both owner decisions of 2026-10-19, follow-ups to
plan T230 (paper spec amendment #1872, items 4 and 6, item 14 (d) and (e)).

Locked tests (Opus locks, Sonnet builds; `src/` untouched by the lock).

**#1959, option (a).** A long-to-short flip's close (`sell` + `long`, a plan
full exit with reason `left_targets`, beside a `sell` + `short` **trade** of the
same name among the pending rebalance's `decisions`) sells the **whole**
holding, residue included, like a `delisted` forced exit: else the residue is
still long when the open is checked and `risk.check_phase`'s
`one_side_per_name` halts the batch (item 4: a name is never long and short at
once). The close still nets the name's own open sells. A plain `left_targets`
close, and a close whose short open the planner skipped (a `skip_*` decision
with no side, so no short ever follows), keep the residue as today (req 3).

**#1969, yes.** `buy_orders(..., short_min_price: float | None = None)`: the
window's frozen `portfolio.short_min_price`. A short open whose reference price
on S (`price_of`, close(S-1)) is below it is skipped `skip_short_price`
(counted in the skip cap), the planner's item 14 (e) order: after
`skip_not_borrowable`, before `skip_below_one_share`. A price equal to the floor
is ordered ("at least", item 6). A long buy never reads it. `None` (the
default, until T235's wrapper passes the frozen value) checks nothing. A
negative or non-finite floor raises `ValueError`.

Every expected value is computed by hand in the comments.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.phases import PhaseOrders, buy_orders, sell_orders
from tradepartner.execution.plan import DecisionState, Remainder, State, decision_state
from tradepartner.execution.risk import OpenSell
from tradepartner.store.journal import DecisionRow

from .test_phases_short import (
    ACTIONS,
    BORROWABLE,
    DECIMALS,
    FROZEN,
    LONG,
    NO_COSTS,
    NOT_ETB,
    SHORT,
    STAMP,
    T0,
    S,
    _asset,
    _close_order_phase,
    _long_buy,
    _long_sell,
    _price,
    _reasons,
    _rows,
    _short_open,
    _states,
)

# --- #1959: a flip's close sells the residue ----------------------------------------


def _sells(
    decisions: Sequence[DecisionRow],
    longs: Mapping[str, float],
    residues: Mapping[str, float],
    *,
    open_sells: Sequence[OpenSell] = (),
) -> PhaseOrders:
    return sell_orders(
        decisions,
        _states(decisions),
        Ledger(positions=dict(longs), cash=0.0, through=S, short_holdings={}),
        residues,
        (),
        ACTIONS,
        _price,
        {d.security_id: BORROWABLE for d in decisions},
        FROZEN,
        session=S,
        quantity_decimals=DECIMALS,
        open_sells=open_sells,
    )


def _planner_skipped_open(i: int, sid: str, kind: str) -> DecisionRow:
    """A short open the planner skipped (item 14 (e)): the skip kind, no side,
    `position_side` short, weight left in cash."""
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=-0.1,
        drifted_weight=0.0,
        whole_share=True,
        decision=kind,
        position_side=SHORT,
        known_at=STAMP,
        ingested_at=STAMP,
    )


def test_a_flips_close_sells_the_whole_holding_residue_included() -> None:
    # Each name holds 6 long, 2 of them residue (`plan.residue`).
    flip_close = _long_sell(1, "SEC_F", quantity=6.0)
    flip_open = _short_open(2, "SEC_F", 1000.0)
    plain_close = _long_sell(3, "SEC_P", quantity=6.0)  # left_targets, no open
    whole_close = replace(_long_sell(4, "SEC_W", quantity=6.0), whole_share=True)
    whole_open = _short_open(5, "SEC_W", 1000.0)
    result = _sells(
        [flip_close, flip_open, plain_close, whole_close, whole_open],
        longs={"SEC_F": 6.0, "SEC_P": 6.0, "SEC_W": 6.0},
        residues={"SEC_F": 2.0, "SEC_P": 2.0, "SEC_W": 2.0},
    )
    # The flips' closes sell 6 (the residue too); the plain close 6 - 2 = 4 (req 3).
    assert _rows(result) == [
        (1, "SEC_F", "sell", LONG, 6.0, None),
        (3, "SEC_P", "sell", LONG, 4.0, None),
        (4, "SEC_W", "sell", LONG, 6.0, None),
    ]
    assert [o.full_exit for o in result.orders] == [True, True, True]
    assert result.skips == () and result.held == ()


def test_a_flips_close_of_a_holding_that_is_all_residue_sells_it() -> None:
    # 0.4 held, all of it residue, fractionable. The flip's close sells 0.4
    # (0.4 x 100 = 40 >= the 1.0 minimum), never `dust`: a `dust` close is not
    # SETTLED, so its open would wait for ever (#1904). The plain close of the
    # same holding sells 0.4 - 0.4 = 0 and is `dust`, as today.
    flip_close = _long_sell(1, "SEC_F", quantity=0.4)
    flip_open = _short_open(2, "SEC_F", 1000.0)
    plain_close = _long_sell(3, "SEC_P", quantity=0.4)
    result = _sells(
        [flip_close, flip_open, plain_close],
        longs={"SEC_F": 0.4, "SEC_P": 0.4},
        residues={"SEC_F": 0.4, "SEC_P": 0.4},
    )
    assert _rows(result) == [(1, "SEC_F", "sell", LONG, 0.4, None)]
    assert result.orders[0].full_exit is True
    assert _reasons(result) == {"SEC_P": "dust"}
    assert result.held == ()


def test_a_flips_close_still_nets_its_open_sells() -> None:
    # 6 held, 2 residue, 1 already in an open own sell (#647 item 5): the
    # flip's close sells 6 - 0 - 1 = 5 (today: 6 - 2 - 1 = 3).
    flip_close = _long_sell(1, "SEC_F", quantity=6.0)
    flip_open = _short_open(2, "SEC_F", 1000.0)
    result = _sells(
        [flip_close, flip_open],
        longs={"SEC_F": 6.0},
        residues={"SEC_F": 2.0},
        open_sells=[OpenSell("SEC_F", 1.0)],
    )
    assert _rows(result) == [(1, "SEC_F", "sell", LONG, 5.0, None)]
    assert result.held == ()


@pytest.mark.parametrize(
    "kind", ["skip_not_borrowable", "skip_short_price", "skip_below_one_share"]
)
def test_a_close_whose_short_open_the_planner_skipped_keeps_the_residue(kind: str) -> None:
    # No short follows (the open is a closed skip with no side), so this close is
    # a plain `left_targets` exit: 6 held less 2 residue = 4 (req 3, unchanged).
    close = _long_sell(1, "SEC_F", quantity=6.0)
    skipped = _planner_skipped_open(2, "SEC_F", kind)
    result = _sells([close, skipped], longs={"SEC_F": 6.0}, residues={"SEC_F": 2.0})
    assert _rows(result) == [(1, "SEC_F", "sell", LONG, 4.0, None)]


# --- #1969: the buy phase re-checks skip_short_price --------------------------------

FLOOR = 5.0  # the window's frozen portfolio.short_min_price


def _prices(prices: Mapping[str, float]) -> Callable[[str], float]:
    def price_of(sid: str) -> float:
        return prices[sid]

    return price_of


def _states_at(
    decisions: Sequence[DecisionRow], price_of: Callable[[str], float]
) -> dict[int, DecisionState]:
    """The real `plan.decision_state` on S at these reference prices."""
    out: dict[int, DecisionState] = {}
    for d in decisions:
        assert d.decision_id is not None
        out[d.decision_id] = decision_state(d, (), (), (), (), ACTIONS, price_of, FROZEN, session=S)
    return out


def _buys_at(
    decisions: Sequence[DecisionRow],
    prices: Mapping[str, float],
    *,
    states: Mapping[int, DecisionState] | None = None,
    assets: Mapping[str, Asset] | None = None,
    sells: PhaseOrders | None = None,
    ended: Sequence[str] = (),
    **floor: float,
) -> PhaseOrders:
    """`buy_orders` at `prices` (close(S-1) on a catch-up run, after the
    planner's close(T_i)); `floor` is `short_min_price=...` or nothing."""
    price_of = _prices(prices)
    return buy_orders(
        decisions,
        states if states is not None else _states_at(decisions, price_of),
        10_000.0,
        sells if sells is not None else PhaseOrders((), ()),
        price_of,
        NO_COSTS,
        assets if assets is not None else {d.security_id: BORROWABLE for d in decisions},
        FROZEN,
        session=S,
        ended=ended,
        **floor,
    )


def test_the_buy_phase_re_checks_the_short_price_floor() -> None:
    """The planner passed all three opens at close(T_i); at close(S-1) SEC_1
    has fallen to 4.0, under the 5.0 floor."""
    below = _short_open(1, "SEC_1", 1000.0)
    at_floor = _short_open(2, "SEC_2", 1000.0)  # 1000 / 5 = 200 shares
    above = _short_open(3, "SEC_3", 1000.0)  # 1000 / 100 = 10 shares
    cheap_long = _long_buy(4, "SEC_4", 500.0)  # a long buy never reads the floor
    prices = {"SEC_1": 4.0, "SEC_2": 5.0, "SEC_3": 100.0, "SEC_4": 4.0}
    result = _buys_at([below, at_floor, above, cheap_long], prices, short_min_price=FLOOR)
    assert _reasons(result) == {"SEC_1": "skip_short_price"}
    assert [s.decision_id for s in result.skips] == [1]
    assert all(s.counts_toward_cap for s in result.skips)
    assert _rows(result) == [
        (2, "SEC_2", "sell", SHORT, 200.0, None),
        (3, "SEC_3", "sell", SHORT, 10.0, None),
        (4, "SEC_4", "buy", LONG, None, 500.0),
    ]
    assert result.deferred == ()


def test_the_short_price_check_follows_item_14e_order() -> None:
    """Borrow before the price floor; the floor before one share. A name not
    `tradable` skips before its reference price is read (its price is NaN
    here, which `_price` would refuse), and an ended listing is `skip_delisted`."""
    not_etb = _short_open(1, "SEC_1", 1000.0)  # 4.0 and not ETB: skip_not_borrowable
    tiny = _short_open(2, "SEC_2", 3.0)  # 3 / 4 = 0.75 share and 4.0 < 5: skip_short_price
    halted = _short_open(3, "SEC_3", 1000.0)  # untradable, no price: skip_untradable
    gone = _short_open(4, "SEC_4", 1000.0)  # listing ended, 4.0: skip_delisted
    prices = {"SEC_1": 4.0, "SEC_2": 4.0, "SEC_3": math.nan, "SEC_4": 4.0}
    untradable = replace(_asset(), tradable=False)
    assets = {"SEC_1": NOT_ETB, "SEC_2": BORROWABLE, "SEC_3": untradable, "SEC_4": BORROWABLE}
    states = {
        1: DecisionState(State.OPEN, remainder=Remainder(quantity=250.0, notional=1000.0)),
        2: DecisionState(State.OPEN, remainder=Remainder(quantity=0.75, notional=3.0)),
        3: DecisionState(State.OPEN, remainder=Remainder(quantity=10.0, notional=1000.0)),
        4: DecisionState(State.OPEN, remainder=Remainder(quantity=250.0, notional=1000.0)),
    }
    result = _buys_at(
        [not_etb, tiny, halted, gone],
        prices,
        states=states,
        assets=assets,
        ended=["SEC_4"],
        short_min_price=FLOOR,
    )
    assert result.orders == ()
    assert _reasons(result) == {
        "SEC_1": "skip_not_borrowable",
        "SEC_2": "skip_short_price",
        "SEC_3": "skip_untradable",
        "SEC_4": "skip_delisted",
    }


def test_a_flips_open_waiting_on_its_close_is_not_price_checked_yet() -> None:
    # Item 14 (d): while the close is in flight the open is neither an order nor
    # a skip, whatever its price. Once the close is SETTLED, the 4.0 < 5.0 open
    # is skipped `skip_short_price`.
    close = _long_sell(1, "SEC_F", quantity=4.0)
    flip_open = _short_open(2, "SEC_F", 1000.0)
    open_state = DecisionState(State.OPEN, remainder=Remainder(quantity=250.0, notional=1000.0))
    prices = {"SEC_F": 4.0}
    waiting = _buys_at(
        [close, flip_open],
        prices,
        states={1: DecisionState(State.IN_FLIGHT), 2: open_state},
        sells=_close_order_phase(close, 4.0),
        short_min_price=FLOOR,
    )
    assert (waiting.orders, waiting.skips, waiting.deferred) == ((), (), ())
    settled = DecisionState(State.SETTLED, remainder=Remainder(quantity=0.0, notional=0.0))
    filled = _buys_at(
        [close, flip_open],
        prices,
        states={1: settled, 2: open_state},
        sells=_close_order_phase(close, 4.0),
        short_min_price=FLOOR,
    )
    assert filled.orders == ()
    assert _reasons(filled) == {"SEC_F": "skip_short_price"}


def test_no_short_min_price_checks_no_floor() -> None:
    # The default (None) keeps T230's call: 1000 / 4 = 250 shares, ordered.
    result = _buys_at([_short_open(1, "SEC_1", 1000.0)], {"SEC_1": 4.0})
    assert _rows(result) == [(1, "SEC_1", "sell", SHORT, 250.0, None)]
    assert result.skips == ()


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf], ids=["negative", "nan", "inf"])
def test_a_malformed_short_min_price_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="short_min_price"):
        _buys_at([_short_open(1, "SEC_1", 1000.0)], {"SEC_1": 100.0}, short_min_price=bad)

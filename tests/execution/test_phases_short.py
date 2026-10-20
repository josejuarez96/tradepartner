"""The four intents in the two phases (plan T230; paper spec amendment #1872,
items 3, 4 and 6, and item 14 (d) and (e), #1904).

Locked tests (Opus locks, Sonnet builds). An order's intent is (`side`,
`position_side`) (item 3): `sell` + `long` is `sell_to_close`, `buy` + `short`
is `buy_to_close`, `sell` + `short` is `sell_to_open`, `buy` + `long` is
`buy_to_open`. The **sell phase** orders the reducing intents, `sell_to_close`
then `buy_to_close`; the **buy phase** the opening ones, `sell_to_open` then
`buy_to_open` (item 4). A held short is the ledger's unsigned
`short_holdings` (T232), never a negative `positions` quantity.

Interface pinned here (beyond today's signatures):
- `sell_orders(..., open_covers: Sequence[risk.OpenSell] = ())`: the name's
  non-terminal own covers, netted from a cover's cap as `open_sells` is from a
  long sell's.
- `buy_orders(..., held_shorts: Mapping[str, float] = {})`: the held short
  shares per name (`Ledger.short_holdings`), valued at `price_of` and deducted
  from `cash` before the long buys are sized (ADR 0010's restated cash rule).
- `buy_orders`' `assets` values carry `shortable` and `easy_to_borrow`
  (`adapters.broker.Asset` does).

Every expected value is computed by hand in the comments; none is read back
from the code under test. The long book's behavior is pinned by the existing
`test_phases.py`, unedited.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.adapters.broker import Asset, PositionIntent, Side
from tradepartner.backtest.costs import Commissions
from tradepartner.config import RiskConfig
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.phases import (
    HeldSell,
    PhaseOrder,
    PhaseOrders,
    buy_orders,
    requests_for,
    sell_orders,
)
from tradepartner.execution.plan import (
    BuyCosts,
    DecisionState,
    Remainder,
    State,
    decision_state,
)
from tradepartner.execution.risk import OpenSell
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

T0 = date(2026, 10, 1)  # rebalance session
S = date(2026, 10, 2)  # its fill session, the run's session
STAMP = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
PRICE = 100.0  # every reference price on S
FROZEN = RiskConfig()  # min_order_notional 1.0, whole_share_price_buffer 0.02
DECIMALS = 9
NO_COSTS = BuyCosts(0.0, Commissions(0.0, 0.0))
LONG = "long"
SHORT = "short"
ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime(time_zone="UTC"),
    }
)


def _asset(*, shortable: bool = True, easy_to_borrow: bool = True) -> Asset:
    # Fractionable on purpose: no short order may be fractional even so (item 3).
    return Asset(
        tradable=True,
        fractionable=True,
        status="active",
        cusip=None,
        shortable=shortable,
        easy_to_borrow=easy_to_borrow,
    )


BORROWABLE = _asset()
NOT_SHORTABLE = _asset(shortable=False)
NOT_ETB = _asset(easy_to_borrow=False)


def _price(_sid: str) -> float:
    return PRICE


# --- decisions ----------------------------------------------------------------------


def _long_sell(
    i: int, sid: str, *, quantity: float | None = None, notional: float | None = None
) -> DecisionRow:
    """A long close (`left_targets`, by quantity) or a long trim (by notional)."""
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=0.0 if quantity is not None else 0.02,
        drifted_weight=0.04,
        side="sell",
        planned_notional=notional,
        planned_quantity=quantity,
        whole_share=False,
        decision="trade",
        reason="left_targets" if quantity is not None else None,
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _long_buy(i: int, sid: str, target: float, *, whole_share: bool = False) -> DecisionRow:
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=0.1,
        drifted_weight=0.0,
        side="buy",
        planned_notional=target,
        target_notional=target,
        whole_share=whole_share,
        decision="trade",
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _short_open(i: int, sid: str, planned: float) -> DecisionRow:
    """A `sell` + `short` decision as T229 writes it: unsigned planned
    notional, signed target notional and weight, whole-share."""
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=-0.1,
        drifted_weight=0.0,
        side="sell",
        planned_notional=planned,
        target_notional=-planned,
        whole_share=True,
        decision="trade",
        position_side=SHORT,
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _partial_cover(i: int, sid: str, planned: float) -> DecisionRow:
    """A `buy` + `short` trim by notional: target notional + its planned notional."""
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=-0.02,
        drifted_weight=-0.05,
        side="buy",
        planned_notional=planned,
        target_notional=planned,
        whole_share=True,
        decision="trade",
        position_side=SHORT,
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _whole_cover(i: int, sid: str, quantity: float) -> DecisionRow:
    """A `buy` + `short` full exit by quantity (`left_targets`), no target notional."""
    return DecisionRow(
        decision_id=i,
        run_id=1,
        rebalance_session=T0,
        security_id=sid,
        target_weight=0.0,
        drifted_weight=-0.03,
        side="buy",
        planned_quantity=quantity,
        whole_share=True,
        decision="trade",
        reason="left_targets",
        position_side=SHORT,
        known_at=STAMP,
        ingested_at=STAMP,
    )


# --- journal rows and states --------------------------------------------------------


#: (side, position_side) -> the id token (T228), spelled out here, not read from `ids`.
_TOKENS = {
    ("sell", LONG): "sell",
    ("buy", LONG): "buy",
    ("sell", SHORT): "short",
    ("buy", SHORT): "cover",
}


def _order(decision: DecisionRow, quantity: float, *, attempt: int = 1) -> OrderRow:
    assert decision.decision_id is not None and decision.side is not None
    token = _TOKENS[(decision.side, decision.position_side)]
    return OrderRow(
        client_order_id=client_order_id("tp", "main", S, decision.security_id, token, attempt),
        decision_id=decision.decision_id,
        run_id=1,
        session=S,
        attempt=attempt,
        phase=decision.side,
        security_id=decision.security_id,
        symbol=decision.security_id,
        side=decision.side,
        quantity=quantity,
        position_side=decision.position_side,
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
        broker_fill_id=f"bf-{order.client_order_id}",
        source="broker_feed",
        known_at=STAMP,
        ingested_at=STAMP,
    )
    return OrderedFill(row, order.side, order.security_id, order.symbol, 1, 1)


def _state(
    decision: DecisionRow,
    orders: Sequence[OrderRow] = (),
    events: Sequence[OrderEventRow] = (),
    fills: Sequence[OrderedFill] = (),
) -> DecisionState:
    """The real `plan.decision_state` on S."""
    return decision_state(decision, (), orders, events, fills, ACTIONS, _price, FROZEN, session=S)


def _open_whole_cover(decision: DecisionRow) -> DecisionState:
    """A whole cover's open state, built by hand: `plan.remainder` does not yet
    state a `buy` + `short` full exit (it reads `target_notional`), which is
    outside T230's files. The phase sizes a whole cover from the ledger."""
    assert decision.planned_quantity is not None
    q = decision.planned_quantity
    return DecisionState(State.OPEN, remainder=Remainder(quantity=q, notional=q * PRICE))


def _states(decisions: Sequence[DecisionRow]) -> dict[int, DecisionState]:
    out: dict[int, DecisionState] = {}
    for d in decisions:
        assert d.decision_id is not None
        whole_cover = d.side == "buy" and d.position_side == SHORT and d.target_notional is None
        out[d.decision_id] = _open_whole_cover(d) if whole_cover else _state(d)
    return out


def _ledger(longs: Mapping[str, float], shorts: Mapping[str, float]) -> Ledger:
    return Ledger(positions=dict(longs), cash=0.0, through=S, short_holdings=dict(shorts))


# --- phase calls --------------------------------------------------------------------


def _sells(
    decisions: Sequence[DecisionRow],
    longs: Mapping[str, float],
    shorts: Mapping[str, float],
    *,
    states: Mapping[int, DecisionState] | None = None,
    open_covers: Sequence[OpenSell] = (),
    assets: Mapping[str, Asset] | None = None,
) -> PhaseOrders:
    return sell_orders(
        decisions,
        states if states is not None else _states(decisions),
        _ledger(longs, shorts),
        {},
        (),
        ACTIONS,
        _price,
        assets if assets is not None else {d.security_id: BORROWABLE for d in decisions},
        FROZEN,
        session=S,
        quantity_decimals=DECIMALS,
        open_covers=open_covers,
    )


def _buys(
    decisions: Sequence[DecisionRow],
    cash: float,
    *,
    states: Mapping[int, DecisionState] | None = None,
    assets: Mapping[str, Asset] | None = None,
    sells: PhaseOrders | None = None,
    held_shorts: Mapping[str, float] | None = None,
    ended: Collection[str] = (),
) -> PhaseOrders:
    return buy_orders(
        decisions,
        states if states is not None else _states(decisions),
        cash,
        sells if sells is not None else PhaseOrders((), ()),
        _price,
        NO_COSTS,
        assets if assets is not None else {d.security_id: BORROWABLE for d in decisions},
        FROZEN,
        session=S,
        ended=ended,
        held_shorts=held_shorts or {},
    )


def _rows(result: PhaseOrders) -> list[tuple[int, str, str, str, float | None, float | None]]:
    """(decision_id, security_id, side, position_side, quantity, notional) per order."""
    return [
        (o.decision_id, o.security_id, o.side, o.position_side, o.quantity, o.notional)
        for o in result.orders
    ]


def _reasons(result: PhaseOrders) -> dict[str, str]:
    return {s.security_id: s.reason for s in result.skips}


def _assert_whole_share_shorts(result: PhaseOrders) -> None:
    """Item 3: no short order is notional or fractional, whatever `fractionable` says."""
    for order in result.orders:
        if order.position_side == SHORT:
            assert order.notional is None, order
            assert order.quantity is not None and float(order.quantity).is_integer(), order
            assert order.whole_share is True, order


# --- the sell phase: sell_to_close, then buy_to_close -------------------------------


def test_the_sell_phase_orders_long_sells_then_covers_and_no_short_open() -> None:
    whole_cover = _whole_cover(1, "SEC_1", 3.0)  # held short 3
    trim = _long_sell(2, "SEC_2", notional=300.0)  # 300 / 100 = 3 shares of the 10 held
    partial = _partial_cover(3, "SEC_3", 250.0)  # 250 / 100 = 2.5, floored to 2
    close = _long_sell(4, "SEC_4", quantity=2.0)  # the whole long holding of 2
    short_open = _short_open(5, "SEC_5", 1000.0)  # the buy phase's, never here
    result = _sells(
        [whole_cover, trim, partial, close, short_open],
        longs={"SEC_2": 10.0, "SEC_4": 2.0},
        shorts={"SEC_1": 3.0, "SEC_3": 10.0},
    )
    # sell_to_close in decision-id order, then buy_to_close in decision-id order.
    assert _rows(result) == [
        (2, "SEC_2", "sell", LONG, 3.0, None),
        (4, "SEC_4", "sell", LONG, 2.0, None),
        (1, "SEC_1", "buy", SHORT, 3.0, None),
        (3, "SEC_3", "buy", SHORT, 2.0, None),
    ]
    assert [o.full_exit for o in result.orders] == [False, True, True, False]
    assert [o.target_weight for o in result.orders] == [0.02, 0.0, 0.0, -0.02]
    assert result.skips == () and result.held == ()
    _assert_whole_share_shorts(result)


def test_a_cover_never_exceeds_the_held_short() -> None:
    # The plan said 5 shares; the ledger holds 3 short: a whole cover buys the
    # reconciled short holding, like a long full exit sells the holding.
    whole_cover = _whole_cover(1, "SEC_1", 5.0)
    # 1250 / 100 = 12.5 shares, over the 10 held short: capped at 10.
    partial = _partial_cover(2, "SEC_2", 1250.0)
    result = _sells([whole_cover, partial], longs={}, shorts={"SEC_1": 3.0, "SEC_2": 10.0})
    assert _rows(result) == [
        (1, "SEC_1", "buy", SHORT, 3.0, None),
        (2, "SEC_2", "buy", SHORT, 10.0, None),
    ]
    _assert_whole_share_shorts(result)


def test_a_cover_is_capped_by_the_held_short_less_its_open_covers() -> None:
    # Held short 10 on each name, 4 of each already being covered by an open
    # own cover: a whole cover buys 10 - 4 = 6, and a 900 / 100 = 9-share
    # partial cover is capped at the same 6, so cover + open covers <= 10
    # (`risk.check_phase`'s `cover_sum_within_holding`).
    whole_cover = _whole_cover(1, "SEC_1", 10.0)
    partial = _partial_cover(2, "SEC_2", 900.0)
    result = _sells(
        [whole_cover, partial],
        longs={},
        shorts={"SEC_1": 10.0, "SEC_2": 10.0},
        open_covers=[OpenSell("SEC_1", 4.0), OpenSell("SEC_2", 4.0)],
    )
    assert _rows(result) == [
        (1, "SEC_1", "buy", SHORT, 6.0, None),
        (2, "SEC_2", "buy", SHORT, 6.0, None),
    ]


def test_a_cover_never_reads_the_borrow_flags() -> None:
    # Item 6 checks borrow on a `sell_to_open` only: a short that turned hard to
    # borrow (or not shortable) is still covered, never skipped `skip_not_borrowable`.
    whole_cover = _whole_cover(1, "SEC_1", 3.0)
    partial = _partial_cover(2, "SEC_2", 250.0)
    assets = {"SEC_1": NOT_ETB, "SEC_2": _asset(shortable=False, easy_to_borrow=False)}
    result = _sells(
        [whole_cover, partial], longs={}, shorts={"SEC_1": 3.0, "SEC_2": 10.0}, assets=assets
    )
    assert _rows(result) == [
        (1, "SEC_1", "buy", SHORT, 3.0, None),
        (2, "SEC_2", "buy", SHORT, 2.0, None),
    ]
    assert result.skips == ()


def test_a_partial_cover_under_one_share_is_skip_below_one_share() -> None:
    # Item 3, every cover whole-share: 50 / 100 = 0.5 floors to 0 shares, on a
    # fractionable name, so the cover is skipped, not sent as 0.5.
    partial = _partial_cover(1, "SEC_1", 50.0)
    result = _sells([partial], longs={}, shorts={"SEC_1": 10.0})
    assert result.orders == ()
    assert _reasons(result) == {"SEC_1": "skip_below_one_share"}


# --- a flip: a close in the sell phase, an open in the buy phase ---------------------


def test_a_long_to_short_flip_is_a_close_in_the_sell_phase_only() -> None:
    # Two `sell` decisions on one name are no longer "two sells in one phase":
    # the close is `sell_to_close` (sell phase), the open `sell_to_open` (buy phase).
    close = _long_sell(1, "SEC_F", quantity=4.0)
    short_open = _short_open(2, "SEC_F", 1000.0)
    result = _sells([close, short_open], longs={"SEC_F": 4.0}, shorts={})
    assert _rows(result) == [(1, "SEC_F", "sell", LONG, 4.0, None)]
    assert result.orders[0].full_exit is True
    assert result.skips == () and result.held == ()


def test_a_short_to_long_flip_is_a_cover_in_the_sell_phase_only() -> None:
    cover = _whole_cover(1, "SEC_G", 3.0)
    buy = _long_buy(2, "SEC_G", 500.0)
    result = _sells([cover, buy], longs={}, shorts={"SEC_G": 3.0})
    assert _rows(result) == [(1, "SEC_G", "buy", SHORT, 3.0, None)]
    assert result.orders[0].full_exit is True


def _close_order_phase(close: DecisionRow, quantity: float) -> PhaseOrders:
    """The sells phase this run built for the flip's close (what the wrapper
    hands `buy_orders` as `planned_sells`)."""
    assert close.decision_id is not None and close.side is not None
    order = PhaseOrder(
        close.decision_id,
        close.security_id,
        close.side,
        close.decision,
        PRICE,
        quantity=quantity,
        full_exit=True,
        whole_share=close.whole_share,
        target_weight=close.target_weight,
        position_side=close.position_side,
    )
    return PhaseOrders((order,), (), session=S)


def test_a_flips_open_is_ordered_in_the_buy_phase_once_its_close_has_filled() -> None:
    close = _long_sell(1, "SEC_F", quantity=4.0)
    short_open = _short_open(2, "SEC_F", 1000.0)
    order = _order(close, 4.0)
    filled = _state(close, [order], _events(order, "filled"), [_fill(order, 4.0, PRICE)])
    assert filled.state is State.SETTLED
    result = _buys(
        [close, short_open],
        10_000.0,
        states={1: filled, 2: _state(short_open)},
        sells=_close_order_phase(close, 4.0),
    )
    # 1000 / 100 = 10 shares, sell_to_open.
    assert _rows(result) == [(2, "SEC_F", "sell", SHORT, 10.0, None)]
    assert result.skips == () and result.deferred == ()
    _assert_whole_share_shorts(result)


@pytest.mark.parametrize(
    "close_filled",
    [
        "in_flight_this_run",
        "in_flight_from_an_earlier_run",
        "partly_filled_and_expired",
        "held_this_run",
    ],
)
def test_a_flips_open_is_not_ordered_while_its_close_has_not_fully_filled(
    close_filled: str,
) -> None:
    """Item 14 (d), #1904: the open is not ordered on this run and its decision
    stays open (no order, no skip row, not deferred), so a name is never long
    and short at once. Nothing raises "both sells and buys" for a flip, whether
    this run's sell phase ordered the close, held it, or never saw it (in
    flight from an earlier run)."""
    close = _long_sell(1, "SEC_F", quantity=4.0)
    short_open = _short_open(2, "SEC_F", 1000.0)
    order = _order(close, 4.0)
    sells = _close_order_phase(close, 4.0)
    if close_filled.startswith("in_flight"):
        state = _state(close, [order], _events(order))  # accepted, nothing terminal
        assert state.state is State.IN_FLIGHT
        if close_filled == "in_flight_from_an_earlier_run":
            sells = PhaseOrders((), ())  # this run's sell phase had nothing to attempt
    elif close_filled == "partly_filled_and_expired":
        # 2 of 4 filled, then expired: 2 shares (200 >= the 1.0 minimum) left.
        state = _state(close, [order], _events(order, "expired"), [_fill(order, 2.0, PRICE)])
        assert state.state is State.OPEN
    else:
        # The sell phase held the close (#719): no order, no skip, still open.
        state = _state(close)
        assert state.state is State.OPEN
        sells = PhaseOrders((), (), session=S, held=(HeldSell(1, "SEC_F", 4.0, 0.0, "dust"),))
    open_state = _state(short_open)
    assert open_state.state is State.OPEN
    result = _buys([close, short_open], 10_000.0, states={1: state, 2: open_state}, sells=sells)
    assert result.orders == ()
    assert result.skips == ()
    assert result.deferred == ()


def test_a_short_to_long_flips_buy_waits_for_its_cover_to_fill() -> None:
    cover = _whole_cover(1, "SEC_G", 3.0)
    buy = _long_buy(2, "SEC_G", 500.0)
    sells = _close_order_phase(cover, 3.0)
    buy_state = _state(buy)
    in_flight = _buys(
        [cover, buy],
        10_000.0,
        states={1: DecisionState(State.IN_FLIGHT), 2: buy_state},
        sells=sells,
    )
    assert (in_flight.orders, in_flight.skips, in_flight.deferred) == ((), (), ())
    settled = DecisionState(State.SETTLED, remainder=Remainder(quantity=0.0, notional=0.0))
    filled = _buys([cover, buy], 10_000.0, states={1: settled, 2: buy_state}, sells=sells)
    assert _rows(filled) == [(2, "SEC_G", "buy", LONG, None, 500.0)]


# --- the buy phase: sell_to_open, then buy_to_open ----------------------------------


def test_the_buy_phase_orders_short_opens_then_long_buys_and_no_cover() -> None:
    long_buy = _long_buy(1, "SEC_1", 1000.0)
    short_a = _short_open(2, "SEC_2", 1050.0)  # 10.5 shares, floored to 10
    whole_buy = _long_buy(3, "SEC_3", 300.0, whole_share=True)  # floor(300 / 102) = 2
    short_b = _short_open(4, "SEC_4", 480.0)  # 4.8 shares, floored to 4
    cover = _partial_cover(5, "SEC_5", 250.0)  # the sell phase's, never here
    result = _buys([long_buy, short_a, whole_buy, short_b, cover], 10_000.0)
    # sell_to_open in decision-id order, then the long notional buys, then the
    # long whole-share buys; 1300 of long targets under 10,000 of cash: scale 1.
    assert _rows(result) == [
        (2, "SEC_2", "sell", SHORT, 10.0, None),
        (4, "SEC_4", "sell", SHORT, 4.0, None),
        (1, "SEC_1", "buy", LONG, None, 1000.0),
        (3, "SEC_3", "buy", LONG, 2.0, None),
    ]
    assert [o.target_weight for o in result.orders[:2]] == [-0.1, -0.1]
    assert [o.full_exit for o in result.orders[:2]] == [False, False]
    assert result.skips == () and result.deferred == ()
    _assert_whole_share_shorts(result)


def test_a_short_open_takes_no_cash_and_is_never_deferred() -> None:
    # Item 4: buys are sized from free cash and do not wait on short proceeds;
    # a short open is not cash-sized. With no cash at all the short open is
    # still ordered (1000 / 100 = 10) and the long buy is deferred.
    short_open = _short_open(1, "SEC_1", 1000.0)
    long_buy = _long_buy(2, "SEC_2", 500.0)
    none = _buys([short_open, long_buy], 0.0)
    assert _rows(none) == [(1, "SEC_1", "sell", SHORT, 10.0, None)]
    assert none.deferred == (2,)
    # With exactly the long target in cash, the long buy is bought whole: the
    # short open's 1000 takes no share of the scale (scale = 500 / 500 = 1).
    exact = _buys([short_open, long_buy], 500.0)
    assert _rows(exact) == [
        (1, "SEC_1", "sell", SHORT, 10.0, None),
        (2, "SEC_2", "buy", LONG, None, 500.0),
    ]


def test_the_long_buys_are_sized_within_free_cash() -> None:
    # Cash 10,000 less a held short of 20 at 100 = 8,000 free. A 10,000 long
    # target scales to 10,000 x (8,000 / 10,000) = 8,000.00.
    long_buy = _long_buy(1, "SEC_1", 10_000.0)
    result = _buys([long_buy], 10_000.0, held_shorts={"SEC_9": 20.0})
    assert _rows(result) == [(1, "SEC_1", "buy", LONG, None, 8000.0)]
    # Cash 1,000 under the 2,000 held short: no free cash, never negative, so
    # the long buy is deferred rather than refused.
    starved = _buys([long_buy], 1000.0, held_shorts={"SEC_9": 20.0})
    assert (starved.orders, starved.deferred) == ((), (1,))


def test_a_short_open_under_one_share_is_skip_below_one_share() -> None:
    # Item 3: 80 / 100 = 0.8 floors to 0 shares on a fractionable name.
    result = _buys([_short_open(1, "SEC_1", 80.0)], 10_000.0)
    assert result.orders == () and result.deferred == ()
    assert _reasons(result) == {"SEC_1": "skip_below_one_share"}


def test_a_short_open_re_attempt_floors_its_remainder() -> None:
    # A 1050 short open sold 4 at 100 (400) then expired: remainder 650,
    # 6.5 shares, floored to 6; never the plan's 10 again.
    short_open = _short_open(1, "SEC_1", 1050.0)
    order = _order(short_open, 10.0)
    state = _state(short_open, [order], _events(order, "expired"), [_fill(order, 4.0, PRICE)])
    assert state.state is State.OPEN
    result = _buys([short_open], 10_000.0, states={1: state})
    assert _rows(result) == [(1, "SEC_1", "sell", SHORT, 6.0, None)]


def test_a_short_open_the_buy_phases_assets_read_cannot_borrow_is_skip_not_borrowable() -> None:
    """Item 14 (e) and item 6, #1904: the planner passed all three (each is a
    `trade`), but the buy phase's own `assets` read must say `shortable` and
    `easy_to_borrow`; else `skip_not_borrowable`, counted in the skip cap."""
    a = _short_open(1, "SEC_1", 1000.0)
    b = _short_open(2, "SEC_2", 1000.0)
    c = _short_open(3, "SEC_3", 1000.0)
    long_buy = _long_buy(4, "SEC_4", 500.0)
    assets = {
        "SEC_1": NOT_SHORTABLE,
        "SEC_2": NOT_ETB,
        "SEC_3": BORROWABLE,
        # A long buy never reads the borrow flags.
        "SEC_4": _asset(shortable=False, easy_to_borrow=False),
    }
    result = _buys([a, b, c, long_buy], 10_000.0, assets=assets)
    assert _reasons(result) == {"SEC_1": "skip_not_borrowable", "SEC_2": "skip_not_borrowable"}
    assert all(s.counts_toward_cap for s in result.skips)
    assert [s.decision_id for s in result.skips] == [1, 2]
    assert _rows(result) == [
        (3, "SEC_3", "sell", SHORT, 10.0, None),
        (4, "SEC_4", "buy", LONG, None, 500.0),
    ]


# --- requests: position_intent and the T228 token ------------------------------------

LISTINGS = {"SEC_1": "AAA", "SEC_2": "BBB", "SEC_3": "CCC", "SEC_4": "DDD"}


def _po(i: int, sid: str, side: str, position_side: str, **size: float) -> PhaseOrder:
    whole = position_side == SHORT
    return PhaseOrder(
        i, sid, side, "trade", PRICE, whole_share=whole, position_side=position_side, **size
    )


def test_requests_carry_each_intent_and_its_id_token() -> None:
    orders = (
        _po(1, "SEC_1", "sell", LONG, quantity=3.0),
        _po(2, "SEC_2", "buy", SHORT, quantity=2.0),
        _po(3, "SEC_3", "sell", SHORT, quantity=10.0),
        _po(4, "SEC_4", "buy", LONG, notional=500.0),
    )
    requests = requests_for(
        PhaseOrders(orders, (), session=S), LISTINGS, "tp", "main", [], None, session=S
    )
    assert [
        (r.client_order_id, r.symbol, r.side, r.position_intent, r.quantity, r.notional)
        for r in requests
    ] == [
        (
            "tp-main-20261002-SEC_1-sell-1",
            "AAA",
            Side.SELL,
            PositionIntent.SELL_TO_CLOSE,
            3.0,
            None,
        ),
        ("tp-main-20261002-SEC_2-cover-1", "BBB", Side.BUY, PositionIntent.BUY_TO_CLOSE, 2.0, None),
        (
            "tp-main-20261002-SEC_3-short-1",
            "CCC",
            Side.SELL,
            PositionIntent.SELL_TO_OPEN,
            10.0,
            None,
        ),
        ("tp-main-20261002-SEC_4-buy-1", "DDD", Side.BUY, PositionIntent.BUY_TO_OPEN, None, 500.0),
    ]


def test_a_flips_open_counts_its_attempt_by_its_own_token() -> None:
    # The flip's close went out in this run's sell phase as SEC_1-sell-1; the
    # open is SEC_1-short-1, its own (security, token) count (item 5), not sell-2.
    close = _long_sell(1, "SEC_1", quantity=4.0)
    on_session = [_order(close, 4.0)]
    batch = PhaseOrders((_po(2, "SEC_1", "sell", SHORT, quantity=10.0),), (), session=S)
    (request,) = requests_for(batch, LISTINGS, "tp", "main", on_session, None, session=S)
    assert request.client_order_id == "tp-main-20261002-SEC_1-short-1"
    assert request.position_intent is PositionIntent.SELL_TO_OPEN
    # A second short open attempt on S counts only the short rows: short-2.
    earlier_open = _order(_short_open(2, "SEC_1", 1000.0), 10.0)
    (again,) = requests_for(
        batch, LISTINGS, "tp", "main", [*on_session, earlier_open], None, session=S
    )
    assert again.client_order_id == "tp-main-20261002-SEC_1-short-2"


@pytest.mark.parametrize(
    "order",
    [
        _po(1, "SEC_1", "sell", SHORT, notional=1000.0),  # a notional short open
        _po(1, "SEC_1", "sell", SHORT, quantity=2.5),  # a fractional short open
        _po(1, "SEC_1", "buy", SHORT, notional=250.0),  # a notional cover
        _po(1, "SEC_1", "buy", SHORT, quantity=0.5),  # a fractional cover
    ],
    ids=["notional_open", "fractional_open", "notional_cover", "fractional_cover"],
)
def test_no_short_request_is_notional_or_fractional(order: PhaseOrder) -> None:
    # Item 3: refused before any request is returned (Alpaca refuses a
    # fractional or notional short; the adapter would not).
    batch = PhaseOrders((_po(9, "SEC_2", "sell", LONG, quantity=1.0), order), (), session=S)
    with pytest.raises(ValueError):
        requests_for(batch, LISTINGS, "tp", "main", [], None, session=S)

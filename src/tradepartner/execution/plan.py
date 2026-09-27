"""Decision state, remainders and buy targets (Phase 4 spec, Definitions >
Decision; plan T52).

Every decision is in exactly one state, derived from the journal and never
stored: **closed** (nothing more is ordered for it in this rebalance),
**in flight** (it has a non-terminal order), **settled** (its latest order is
terminal and its remainder is below the trading minimum) or **open** (never
ordered, or its latest order terminal with a remainder still worth trading).
A re-run or catch-up trades only open decisions, and only for the remainder.

Pure functions over rows the caller has read; rows of other decisions are
ignored, so the caller may pass a whole rebalance's orders, events and fills.
Fills come from `store.journal.fills_for`, so a superseded fill is never
counted. `actions_as_of` is a `store.asof.live_actions_as_of` frame read at
close(S-1), and `session` is S: a split applies to a quantity when its
ex-date is after the date the quantity was stated for and on or before S.

How decisions are read (T53 writes them this way):

- every `skip_*` kind and `dust` are closed with that reason; an `override`
  decision with no side is a `keep_name` and closed; an `override` with a side
  (`exclude_name`) trades like any other decision.
- A sell with `planned_quantity` is a quantity sell, one with
  `planned_notional` a notional sell; once ordered, the latest order's own
  field decides. A buy's remainder is measured against its `target_notional`.
- A decision's quantity before any order is stated for its
  `rebalance_session`, or, for a forced exit (no rebalance session), for the
  New York date of its `known_at`.

An order is terminal when **any** of its events is terminal (a `cancel_noop`
may follow `expired`), and in flight otherwise, an order with no event at all
included: that fails safe, since an in-flight decision is never re-ordered.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.config import RiskConfig
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

_NEW_YORK = ZoneInfo("America/New_York")
_SPLIT = "split"
_BUY = "buy"
_SELL = "sell"
_OVERRIDE = "override"
_FORCED_EXIT = "forced_exit"
_KEEP_NAME = "keep_name"
_WRITTEN_OFF = "written_off"
_CLOSING_EVENTS = frozenset({"skipped", _WRITTEN_OFF})
_SKIP_PREFIX = "skip_"
_DUST = "dust"
_TRADE = "trade"
#: Decision kinds whose sells are the plan's own (their proceeds fund the buys).
_PLAN_TRADE_KINDS = frozenset({_TRADE, _OVERRIDE})
_HALT = "halt"
_HALT_CANCEL_STATUSES = frozenset({"cancel_requested", "cancel_failed"})
_NOT_RECEIVED = "not_received"
_CANCELLED = "cancelled"


class State(StrEnum):
    """A decision's derived state."""

    CLOSED = "closed"
    IN_FLIGHT = "in_flight"
    SETTLED = "settled"
    OPEN = "open"


@dataclass(frozen=True)
class Remainder:
    """What is left to trade: shares, and their value at the reference price
    (a notional remainder is converted to shares at that price)."""

    quantity: float
    notional: float


@dataclass(frozen=True)
class DecisionState:
    """A decision's state, why, and its remainder when it has one.

    `written_off` is set when the write-off rule applies to a buy (state
    `closed`, reason `written_off`) although no `written_off` decision event
    exists yet: the run step that sees it appends that row with the remainder's
    notional as `unfunded_notional`.
    """

    state: State
    reason: str | None = None
    remainder: Remainder | None = None
    written_off: bool = False


@dataclass(frozen=True)
class BuyCosts:
    """The cost inputs of buy sizing: the frozen per-side rate and commissions."""

    per_side_bps: float
    commissions: Commissions


PriceOf = Callable[[str], float]


def _decision_id(decision: DecisionRow) -> int:
    if decision.decision_id is None:
        raise ValueError("decision has no decision_id")
    return decision.decision_id


def _orders_of(decision: DecisionRow, orders: Iterable[OrderRow]) -> list[OrderRow]:
    """The decision's orders, oldest attempt first."""
    decision_id = _decision_id(decision)
    mine = [o for o in orders if o.decision_id == decision_id]
    return sorted(mine, key=lambda o: (o.session, o.attempt))


def _filled(order: OrderRow, fills: Iterable[OrderedFill]) -> tuple[float, float]:
    """Filled shares and filled value of one order."""
    quantity = value = 0.0
    for fill in fills:
        if fill.fill.client_order_id == order.client_order_id:
            quantity += fill.fill.quantity
            value += fill.fill.quantity * fill.fill.price
    return quantity, value


def _split_factor(
    actions_as_of: pl.DataFrame, security_id: str, stated_on: date, session: date
) -> float:
    """The product of the ratios of splits with ex-date in (`stated_on`, `session`]."""
    factor = 1.0
    for row in actions_as_of.iter_rows(named=True):
        if (
            row["security_id"] == security_id
            and row["action_type"] == _SPLIT
            and stated_on < row["ex_date"] <= session
        ):
            factor *= float(row["ratio_or_amount"])
    return factor


def _stated_on(decision: DecisionRow) -> date:
    if decision.rebalance_session is not None:
        return decision.rebalance_session
    return decision.known_at.astimezone(_NEW_YORK).date()


def _price(price_of: PriceOf, security_id: str) -> float:
    price = price_of(security_id)
    if not price > 0:
        raise ValueError(f"reference price of {security_id} is {price}, must be positive")
    return price


def _check_session(session: date) -> None:
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")


def remainder(
    decision: DecisionRow,
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    actions_as_of: pl.DataFrame,
    price_of: PriceOf,
    *,
    session: date,
) -> Remainder:
    """What is left of `decision` on session `session` (spec Definitions):

    - a quantity sell: the latest order's submitted quantity minus its filled
      quantity, adjusted by the splits with ex-date in (the order's session, S];
    - a notional sell: the latest order's submitted notional minus its filled
      value, converted to shares at the reference price;
    - a buy: its `target_notional` minus the filled value over all its orders.

    Before any order it is the plan: the buy's target, or the sell's planned
    notional or split-adjusted planned quantity. `order_events` are not read (a
    remainder counts fills, whatever the order's status) and are accepted so
    every derivation takes the same rows.
    """
    del order_events
    _check_session(session)
    price = _price(price_of, decision.security_id)
    mine = _orders_of(decision, orders)
    if decision.side == _BUY:
        if decision.target_notional is None:
            raise ValueError(f"buy decision {decision.decision_id} has no target_notional")
        spent = sum(_filled(order, fills)[1] for order in mine)
        notional = decision.target_notional - spent
        return Remainder(quantity=notional / price, notional=notional)
    if decision.side != _SELL:
        raise ValueError(f"decision {decision.decision_id} has side {decision.side!r}")
    if mine:
        latest = mine[-1]
        filled_quantity, filled_value = _filled(latest, fills)
        if latest.quantity is not None:
            factor = _split_factor(actions_as_of, decision.security_id, latest.session, session)
            quantity = (latest.quantity - filled_quantity) * factor
            return Remainder(quantity=quantity, notional=quantity * price)
        if latest.notional is None:
            raise ValueError(f"order {latest.client_order_id!r} has neither quantity nor notional")
        notional = latest.notional - filled_value
        return Remainder(quantity=notional / price, notional=notional)
    if decision.planned_quantity is not None:
        factor = _split_factor(actions_as_of, decision.security_id, _stated_on(decision), session)
        quantity = decision.planned_quantity * factor
        return Remainder(quantity=quantity, notional=quantity * price)
    if decision.planned_notional is not None:
        notional = decision.planned_notional
        return Remainder(quantity=notional / price, notional=notional)
    raise ValueError(f"sell decision {decision.decision_id} has no planned quantity or notional")


def target_notional(
    decision: DecisionRow,
    cash_before: float,
    planned_sells: Sequence[DecisionRow],
    planned_buys: Sequence[DecisionRow],
    price_of: PriceOf,
    costs: BuyCosts,
) -> float:
    """A buy's target, fixed at plan time: `planned_notional` x min(1, spendable
    / the planned buys' notional sum). Spendable is
    `costs.buy_notional_after_costs` on `cash_before` plus the proceeds of every
    sell the plan made (`trade` or `override` sells) at the reference price (a
    planned notional, or a planned quantity x price). A `forced_exit` sell is
    not the plan's, so its proceeds stay out: they are cash until the next
    rebalance. A plan sell or a buy with no planned amount raises `ValueError`.
    `planned_buys` is the rebalance's buy decisions, `decision` among them.
    """
    if decision.side != _BUY or decision.planned_notional is None:
        raise ValueError(f"decision {decision.decision_id} is not a buy with a planned notional")
    decision_id = _decision_id(decision)
    if decision_id not in {b.decision_id for b in planned_buys}:
        raise ValueError(f"decision {decision_id} is not among planned_buys")
    proceeds = 0.0
    for sell in planned_sells:
        if sell.decision not in _PLAN_TRADE_KINDS or sell.side != _SELL:
            continue  # a forced exit, a skip or dust is not a sell the plan made
        if sell.planned_notional is not None:
            proceeds += sell.planned_notional
        elif sell.planned_quantity is not None:
            proceeds += sell.planned_quantity * _price(price_of, sell.security_id)
        else:
            raise ValueError(f"planned sell {sell.decision_id} has no planned quantity or notional")
    missing = [b.decision_id for b in planned_buys if b.planned_notional is None]
    if missing:
        raise ValueError(f"planned buys {missing} have no planned_notional")
    total = sum(b.planned_notional or 0.0 for b in planned_buys)
    if not total > 0:
        raise ValueError("planned_buys have no positive planned notional")
    spendable = buy_notional_after_costs(
        cash_before + proceeds,
        costs.per_side_bps,
        costs.commissions,
        price=_price(price_of, decision.security_id),
    )
    return decision.planned_notional * min(1.0, spendable / total)


def _is_terminal(events: Iterable[OrderEventRow]) -> bool:
    return any(e.status in TERMINAL_ORDER_STATUSES for e in events)


def _protected_from_write_off(events: Sequence[OrderEventRow]) -> bool:
    """A halt cancel or a `not_received` cancel: not a funding shortfall."""
    for event in events:
        if event.status in _HALT_CANCEL_STATUSES and event.reason == _HALT:
            return True
        if event.status == _CANCELLED and event.reason == _NOT_RECEIVED:
            return True
    return False


def decision_state(
    decision: DecisionRow,
    decision_events: Sequence[DecisionEventRow],
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    actions_as_of: pl.DataFrame,
    price_of: PriceOf,
    frozen: RiskConfig,
    *,
    session: date,
) -> DecisionState:
    """The decision's state on session `session` (module docstring).

    `frozen` is the window's frozen `risk.*` section: the trading minimum is
    `risk.min_order_notional`, or for a `whole_share` decision (read from the
    row, never the live asset) one share at the reference price x
    (1 + `risk.whole_share_price_buffer`). A buy whose latest order is terminal
    with a remainder at or above the minimum is written off when that order
    was submitted with no sell of its rebalance in flight, has no
    `cancel_requested`/`cancel_failed` event with reason `halt`, and did not end
    `cancelled` with reason `not_received`; otherwise it stays open.
    """
    _check_session(session)
    decision_id = _decision_id(decision)
    if decision.decision.startswith(_SKIP_PREFIX) or decision.decision == _DUST:
        return DecisionState(State.CLOSED, decision.decision)
    if decision.decision == _OVERRIDE and decision.side is None:
        return DecisionState(State.CLOSED, _KEEP_NAME)
    mine_events = [e for e in decision_events if e.decision_id == decision_id]
    if mine_events:
        latest_event = max(enumerate(mine_events), key=lambda p: (p[1].known_at, p[0]))[1]
        if latest_event.status in _CLOSING_EVENTS:
            return DecisionState(State.CLOSED, latest_event.status)

    mine = _orders_of(decision, orders)
    events_by_order: dict[str, list[OrderEventRow]] = {o.client_order_id: [] for o in mine}
    for event in order_events:
        if event.client_order_id in events_by_order:
            events_by_order[event.client_order_id].append(event)
    if any(not _is_terminal(events_by_order[o.client_order_id]) for o in mine):
        return DecisionState(State.IN_FLIGHT)

    left = remainder(
        decision, orders, order_events, fills, actions_as_of, price_of, session=session
    )
    if not mine:
        return DecisionState(State.OPEN, remainder=left)

    price = _price(price_of, decision.security_id)
    minimum = (
        price * (1 + frozen.whole_share_price_buffer)
        if decision.whole_share
        else frozen.min_order_notional
    )
    if left.notional < minimum:
        return DecisionState(State.SETTLED, remainder=left)

    latest = mine[-1]
    if (
        decision.side == _BUY
        and not latest.sells_in_flight_at_submit
        and not _protected_from_write_off(events_by_order[latest.client_order_id])
    ):
        return DecisionState(State.CLOSED, _WRITTEN_OFF, left, written_off=True)
    return DecisionState(State.OPEN, remainder=left)

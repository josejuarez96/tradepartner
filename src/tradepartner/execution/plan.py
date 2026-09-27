"""Decision state, remainders and buy targets (Phase 4 spec, Definitions >
Decision; plan T52), residues and rebalance state (spec req 14 and
Definitions > Rebalance state; plan T52b).

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

`price_of(security_id)` is the reference price on the same split basis as the
quantities: the close read at close(S-1), divided by the splits with ex-date
after that close's session and on or before S (so on an ex-date the price is
in post-split shares). The spec does not say this; T53 and T54 build
`price_of` and follow it.

Every amount read is checked finite, and amounts that cannot be negative are
checked too; a remainder is clamped to [0, the amount it is measured against]
(a buy's target, the latest order's quantity or notional), so no fill price,
implied or not, can make it larger than the plan. Rows of one decision whose
side or security disagree with it raise `ValueError`.

**Residues** (`residue`, spec req 14) are a quantity per name: the carried
part (the window's `carried_residue` adjustments, split-adjusted from their
session through the ledger's session, capped at the holding, and for an
`origin = untradable` row counted only while the name's latest
`positions_daily.tradable` flag is false), the dust part (the holding, when the
name's latest decision is a `window_stop` forced exit whose latest event is
`skipped` with reason `dust`) and the untradable part (the holding, when the
name's latest decision is a `window_stop`, `delisted` or `untargeted_receipt`
forced exit whose latest event is `skipped` with reason `untradable`, while the
latest flag is false); their sum capped at the holding. "Latest" is by
`known_at`, the id breaking a tie; the latest flag is the name's latest row by
session then `known_at` that carries one (a row with no flag is not a flag, and
no flag at all is not false). The caller passes one window's rows.

**Rebalance state** (`rebalance_state`) is derived: a journaled `executed` or
`missed` event of the window's runs is the state; otherwise the rebalance is
`executed` when it has at least one decision and none is open or in flight
(forced exits, which carry no rebalance session, never enter the test), and
`pending` otherwise, planned or not.

An order is terminal when **any** of its events is terminal (a `cancel_noop`
may follow `expired`), and in flight otherwise, an order with no event at all
included: that fails safe, since an in-flight decision is never re-ordered.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.backtest.schedule import fill_session
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
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
_SKIPPED = "skipped"
_CARRIED_RESIDUE = "carried_residue"
_UNTRADABLE = "untradable"
_WINDOW_STOP = "window_stop"
#: Forced-exit reasons whose `untradable` skip leaves an untradable residue.
_UNTRADABLE_EXIT_REASONS = frozenset({_WINDOW_STOP, "delisted", "untargeted_receipt"})


class RebalanceState(StrEnum):
    """A rebalance's derived state."""

    PENDING = "pending"
    EXECUTED = "executed"
    MISSED = "missed"


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


def _finite(value: float, what: str, *, non_negative: bool = False) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{what} is {value}, not a finite number")
    if non_negative and value < 0:
        raise ValueError(f"{what} is {value}, must not be negative")
    return value


def _clamp(value: float, upper: float) -> float:
    return min(max(value, 0.0), upper)


def _orders_of(decision: DecisionRow, orders: Iterable[OrderRow]) -> list[OrderRow]:
    """The decision's orders, oldest attempt first; one whose side or security
    disagrees with the decision raises."""
    decision_id = _decision_id(decision)
    mine = [o for o in orders if o.decision_id == decision_id]
    for order in mine:
        if (order.side, order.security_id) != (decision.side, decision.security_id):
            raise ValueError(
                f"order {order.client_order_id!r} ({order.side} {order.security_id}) disagrees "
                f"with decision {decision_id} ({decision.side} {decision.security_id})"
            )
        for amount, name in ((order.quantity, "quantity"), (order.notional, "notional")):
            if amount is not None:
                _finite(amount, f"order {order.client_order_id!r} {name}", non_negative=True)
    return sorted(mine, key=lambda o: (o.session, o.attempt))


def _filled(order: OrderRow, fills: Iterable[OrderedFill]) -> tuple[float, float]:
    """Filled shares and filled value of one order."""
    quantity = value = 0.0
    for fill in fills:
        row = fill.fill
        if row.client_order_id != order.client_order_id:
            continue
        if (fill.side, fill.security_id) != (order.side, order.security_id):
            raise ValueError(f"fill {row.fill_id} disagrees with order {order.client_order_id!r}")
        quantity += _finite(row.quantity, f"fill {row.fill_id} quantity", non_negative=True)
        value += row.quantity * _finite(row.price, f"fill {row.fill_id} price")
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
            ratio = _finite(float(row["ratio_or_amount"]), f"split ratio of {security_id}")
            if ratio <= 0:
                raise ValueError(f"split of {security_id} has ratio {ratio}")
            factor *= ratio
    return factor


def _stated_on(decision: DecisionRow) -> date:
    if decision.rebalance_session is not None:
        return decision.rebalance_session
    return decision.known_at.astimezone(_NEW_YORK).date()


def _price(price_of: PriceOf, security_id: str) -> float:
    price = price_of(security_id)
    if not (math.isfinite(price) and price > 0):
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
        target = _finite(
            decision.target_notional, f"decision {decision.decision_id} target", non_negative=True
        )
        spent = sum(_filled(order, fills)[1] for order in mine)
        notional = _clamp(target - spent, target)
        return Remainder(quantity=notional / price, notional=notional)
    if decision.side != _SELL:
        raise ValueError(f"decision {decision.decision_id} has side {decision.side!r}")
    if mine:
        latest = mine[-1]
        filled_quantity, filled_value = _filled(latest, fills)
        if latest.quantity is not None:
            factor = _split_factor(actions_as_of, decision.security_id, latest.session, session)
            quantity = _clamp(
                (latest.quantity - filled_quantity) * factor, latest.quantity * factor
            )
            return Remainder(quantity=quantity, notional=quantity * price)
        if latest.notional is None:
            raise ValueError(f"order {latest.client_order_id!r} has neither quantity nor notional")
        notional = _clamp(latest.notional - filled_value, latest.notional)
        return Remainder(quantity=notional / price, notional=notional)
    what = f"decision {decision.decision_id}"
    if decision.planned_quantity is not None:
        planned = _finite(decision.planned_quantity, f"{what} quantity", non_negative=True)
        factor = _split_factor(actions_as_of, decision.security_id, _stated_on(decision), session)
        quantity = planned * factor
        return Remainder(quantity=quantity, notional=quantity * price)
    if decision.planned_notional is not None:
        notional = _finite(decision.planned_notional, f"{what} notional", non_negative=True)
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
    Its `price_of` is the plan's own: the close at close(T_i), with no later
    split applied, since planned quantities are stated for T_i (not the run
    session's price the other functions take).
    """
    if decision.side != _BUY or decision.planned_notional is None:
        raise ValueError(f"decision {decision.decision_id} is not a buy with a planned notional")
    decision_id = _decision_id(decision)
    rebalance = decision.rebalance_session
    if rebalance is None:
        raise ValueError(f"buy decision {decision_id} has no rebalance session")
    ids = [row.decision_id for row in (*planned_sells, *planned_buys)]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("planned_sells and planned_buys hold a missing or repeated decision_id")
    for row in (*planned_sells, *planned_buys):
        if (row.rebalance_session, row.run_id) != (rebalance, decision.run_id) and (
            row.decision != _FORCED_EXIT
        ):
            raise ValueError(
                f"decision {row.decision_id} is not of rebalance {rebalance} run {decision.run_id}"
            )
    if any(b.side != _BUY for b in planned_buys):
        raise ValueError("planned_buys holds a decision that is not a buy")
    if decision_id not in ids[len(planned_sells) :]:
        raise ValueError(f"decision {decision_id} is not among planned_buys")
    _finite(cash_before, "cash_before", non_negative=True)
    _finite(costs.per_side_bps, "costs.per_side_bps", non_negative=True)
    proceeds = 0.0
    for sell in planned_sells:
        if sell.decision not in _PLAN_TRADE_KINDS or sell.side != _SELL:
            continue  # a forced exit, a skip or dust is not a sell the plan made
        what = f"planned sell {sell.decision_id}"
        if sell.planned_notional is not None:
            proceeds += _finite(sell.planned_notional, what, non_negative=True)
        elif sell.planned_quantity is not None:
            quantity = _finite(sell.planned_quantity, what, non_negative=True)
            proceeds += quantity * _price(price_of, sell.security_id)
        else:
            raise ValueError(f"{what} has no planned quantity or notional")
    missing = [b.decision_id for b in planned_buys if b.planned_notional is None]
    if missing:
        raise ValueError(f"planned buys {missing} have no planned_notional")
    total = sum(
        _finite(b.planned_notional or 0.0, f"planned buy {b.decision_id}", non_negative=True)
        for b in planned_buys
    )
    if not total > 0:
        raise ValueError("planned_buys have no positive planned notional")
    spendable = buy_notional_after_costs(
        cash_before + proceeds,
        costs.per_side_bps,
        costs.commissions,
        price=_price(price_of, decision.security_id),
    )
    _finite(spendable, "spendable", non_negative=True)
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
    if left.quantity <= 0 or left.notional < minimum:
        return DecisionState(State.SETTLED, remainder=left)

    latest = mine[-1]
    if (
        decision.side == _BUY
        and not latest.sells_in_flight_at_submit
        and not _protected_from_write_off(events_by_order[latest.client_order_id])
    ):
        return DecisionState(State.CLOSED, _WRITTEN_OFF, left, written_off=True)
    return DecisionState(State.OPEN, remainder=left)


# --- residues and rebalance state (T52b) -------------------------------------------


def _latest_flag(security_id: str, positions_daily: Iterable[PositionDailyRow]) -> bool | None:
    """The name's latest `tradable` flag: its latest row carrying one."""
    flagged = [
        row
        for row in positions_daily
        if row.security_id == security_id and row.tradable is not None
    ]
    if not flagged:
        return None
    return max(flagged, key=lambda row: (row.session, row.known_at)).tradable


def _latest_decision(security_id: str, decisions: Iterable[DecisionRow]) -> DecisionRow | None:
    mine = [d for d in decisions if d.security_id == security_id]
    if not mine:
        return None
    return max(mine, key=lambda d: (d.known_at, _decision_id(d)))


def _latest_event(
    decision: DecisionRow, decision_events: Iterable[DecisionEventRow]
) -> DecisionEventRow | None:
    decision_id = _decision_id(decision)
    mine = [e for e in decision_events if e.decision_id == decision_id]
    if not mine:
        return None
    return max(enumerate(mine), key=lambda p: (p[1].known_at, p[0]))[1]


def _carried(
    security_id: str,
    adjustments: Iterable[AdjustmentRow],
    actions_as_of: pl.DataFrame,
    session: date,
    flag_false: bool,
) -> float:
    """The window's carried residue of the name, split-adjusted through `session`;
    an `origin = untradable` row counts only while the latest flag is false."""
    rows = [
        row
        for row in adjustments
        if row.kind == _CARRIED_RESIDUE and row.security_id == security_id
    ]
    if len({row.window_id for row in rows}) > 1:
        raise ValueError(f"carried residues of {security_id} come from more than one window")
    total = 0.0
    for row in rows:
        if row.quantity is None:
            raise ValueError(f"carried residue {row.adjustment_id} has no quantity")
        quantity = _finite(
            row.quantity, f"carried residue {row.adjustment_id} quantity", non_negative=True
        )
        if row.origin == _UNTRADABLE and not flag_false:
            continue
        total += quantity * _split_factor(actions_as_of, security_id, row.session, session)
    return total


def residue(
    security_id: str,
    adjustments: Sequence[AdjustmentRow],
    decisions: Sequence[DecisionRow],
    decision_events: Sequence[DecisionEventRow],
    positions_daily: Sequence[PositionDailyRow],
    ledger: Ledger,
    actions_as_of: pl.DataFrame,
) -> float:
    """The name's residue quantity on the ledger's session (module docstring;
    spec req 14): carried + dust + untradable, capped at the holding.

    All rows are one window's; `ledger.through` is S, the session the holding
    and the split-adjusted carried quantity are stated for, and `actions_as_of`
    the actions read at close(S-1). A name not held has no residue.
    """
    _check_session(ledger.through)
    held = _finite(ledger.positions.get(security_id, 0.0), f"holding of {security_id}")
    if held <= 0:
        return 0.0
    flag_false = _latest_flag(security_id, positions_daily) is False
    carried = min(
        _carried(security_id, adjustments, actions_as_of, ledger.through, flag_false), held
    )
    dust = untradable = 0.0
    latest = _latest_decision(security_id, decisions)
    if latest is not None and latest.decision == _FORCED_EXIT:
        event = _latest_event(latest, decision_events)
        if event is not None and event.status == _SKIPPED:
            if event.reason == _DUST and latest.reason == _WINDOW_STOP:
                dust = held
            elif (
                event.reason == _UNTRADABLE
                and latest.reason in _UNTRADABLE_EXIT_REASONS
                and flag_false
            ):
                untradable = held
    return min(carried + dust + untradable, held)


def rebalance_state(
    rebalance_session: date,
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    rebalance_events: Sequence[RebalanceEventRow],
    decision_states: Sequence[tuple[DecisionRow, DecisionState]],
    *,
    session: date,
) -> RebalanceState:
    """The state of rebalance T_i = `rebalance_session` of the open `window` on
    session S = `session` (module docstring; spec Definitions > Rebalance state).

    `runs` ties events and decisions to the window (`run_id` ->
    `paper_runs.window_id`); rows of other windows or rebalances are ignored.
    `decision_states` pairs each decision with its `decision_state`. Raises
    `ValueError` for a T_i before the window's `first_rebalance_session`, one
    whose fill session is after S (not due yet), a date that is not a rebalance
    session, and a rebalance journaled both `executed` and `missed`.
    """
    _check_session(session)
    if rebalance_session < window.first_rebalance_session:
        raise ValueError(
            f"rebalance {rebalance_session} is before the window's first rebalance "
            f"{window.first_rebalance_session}"
        )
    if fill_session(rebalance_session) > session:
        raise ValueError(f"rebalance {rebalance_session} is not due on {session}")
    run_ids = {run.run_id for run in runs if run.window_id == window.window_id}
    statuses = {
        event.status
        for event in rebalance_events
        if event.rebalance_session == rebalance_session and event.run_id in run_ids
    }
    if len(statuses) > 1:
        raise ValueError(f"rebalance {rebalance_session} is journaled both {sorted(statuses)}")
    if statuses:
        return RebalanceState(statuses.pop())
    mine = [
        state
        for decision, state in decision_states
        if decision.rebalance_session == rebalance_session
        and decision.decision != _FORCED_EXIT
        and decision.run_id in run_ids
    ]
    if mine and all(state.state in (State.CLOSED, State.SETTLED) for state in mine):
        return RebalanceState.EXECUTED
    return RebalanceState.PENDING

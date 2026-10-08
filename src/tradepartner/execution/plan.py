"""Decision state, remainders and buy targets (Phase 4 spec, Definitions >
Decision; plan T52), residues and rebalance state (spec req 14 and
Definitions > Rebalance state; plan T52b), and the decisions a plan makes
(`decisions_from`, spec reqs 3, 7 step 6 and 9; plan T53b).

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
  decision with no side is closed with its own kind as the reason (every
  `keep_name`, and an `exclude_name` of a name not held, #450); an `override`
  with a side (always `exclude_name`) trades like any other decision.
- A sell with `planned_notional` is a **trim**, measured against that planned
  notional over all its orders, whatever field they carry (every sell order is
  by quantity, ADR 0010 amendment 2026-09-30); a sell with `planned_quantity`
  is measured by its latest order's own field. A buy's remainder is measured
  against its `target_notional`.
- A **full exit** (`is_full_exit`, the spec's Definitions > Full exit) is a
  sell that is a `forced_exit`, an `override` (with a side, always
  `exclude_name`), or a `trade` with reason `left_targets` or `left_universe`;
  a `trade` sell with no reason is a trim. A sideless override is never one.
- A decision's quantity before any order is stated for its
  `rebalance_session`, or, for a forced exit (no rebalance session), for the
  New York date of its `known_at`.

`price_of(security_id)` is the reference price on the same split basis as the
quantities (spec Definitions > Reference price): the close read at close(S-1),
divided by the splits with ex-date after that close's session and on or before
S (so on an ex-date the price is in post-split shares).

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
`known_at` (a decision's id, then input order, breaking a tie); the latest flag
is the name's latest row by session then `known_at` that carries one (a row
with no flag is not a flag, and no flag at all is not false). Rows are placed
in the window through the window's runs, and rows dated after S are cut.

**Rebalance state** (`rebalance_state`) is derived: a journaled `executed` or
`missed` event of the window's runs is the state; otherwise the rebalance is
`executed` when it has at least one decision and none is open or in flight
(forced exits, which carry no rebalance session, never enter the test), and
`pending` otherwise, planned or not.

The **stop session** (`stop_session`, spec reqs 9 and 14) of a window's
`requested` stop is the calendar session containing the row's `at` (its New
York date), or the next session when that date is not one. It lives here, the
one module `run`, `report` and `check` all import without a cycle, so the three
share one rule.

An order is terminal when **any** of its events is terminal (a `cancel_noop`
may follow `expired`), and in flight otherwise, an order with no event at all
included: that fails safe, since an in-flight decision is never re-ordered.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.schedule import fill_session
from tradepartner.calendar import is_session, next_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution.ledger import Ledger
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    OverrideRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
    SignalRow,
)
from tradepartner.store.schema import (
    DELISTED_REASON,
    EXCLUDE_NAME_REASON,
    EXCLUDED_REASON_PREFIX,
    HALT_REASON,
    KEEP_NAME_REASON,
    LEFT_TARGETS_REASON,
    LEFT_UNIVERSE_REASON,
    NOT_RECEIVED_REASON,
    OWNER_SETTLED_UNKNOWN_REASON,
    UNTARGETED_RECEIPT_REASON,
    WINDOW_STOP_REASON,
)
from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")
_SPLIT = "split"
_BUY = "buy"
_SELL = "sell"
_OVERRIDE = "override"
#: The `decisions.decision` kind for a forced exit; public so other
#: execution modules (e.g. `reattempts.py`) share one name (#497).
FORCED_EXIT = "forced_exit"
_KEEP_NAME = KEEP_NAME_REASON
_WRITTEN_OFF = "written_off"
_CLOSING_EVENTS = frozenset({"skipped", _WRITTEN_OFF})
_SKIP_PREFIX = "skip_"
_DUST = "dust"
_TRADE = "trade"
#: Decision kinds whose sells are the plan's own (their proceeds fund the buys).
_PLAN_TRADE_KINDS = frozenset({_TRADE, _OVERRIDE})
_HALT = HALT_REASON
_HALT_CANCEL_STATUSES = frozenset({"cancel_requested", "cancel_failed"})
_NOT_RECEIVED = NOT_RECEIVED_REASON
_OWNER_SETTLED_UNKNOWN = OWNER_SETTLED_UNKNOWN_REASON
#: Reasons of a terminal `cancelled` event that is not a funding shortfall: the
#: broker never received the order (req 4), or the owner settled it without a
#: fill (`paper settle`, req 17, #571).
_PROTECTED_CANCEL_REASONS = frozenset({_NOT_RECEIVED, _OWNER_SETTLED_UNKNOWN})
_CANCELLED = "cancelled"
_SKIPPED = "skipped"
_CARRIED_RESIDUE = "carried_residue"
_UNTRADABLE = "untradable"
_WINDOW_STOP = WINDOW_STOP_REASON
#: Forced-exit reasons whose `untradable` skip leaves an untradable residue.
_UNTRADABLE_EXIT_REASONS = frozenset({_WINDOW_STOP, DELISTED_REASON, UNTARGETED_RECEIPT_REASON})
#: A carried residue's origin (spec req 14: `paper start` copies it from the stop row).
_RESIDUE_ORIGINS = frozenset({_DUST, _UNTRADABLE})
#: Full-exit reasons `decisions_from` writes (spec req 3; Definitions > Full exit).
_LEFT_TARGETS = LEFT_TARGETS_REASON
_LEFT_UNIVERSE = LEFT_UNIVERSE_REASON
_EXCLUDE_NAME = EXCLUDE_NAME_REASON
#: `trade` sell reasons that sell the whole holding (spec Definitions > Full exit).
_FULL_EXIT_TRADE_REASONS = frozenset({_LEFT_TARGETS, _LEFT_UNIVERSE})
_NAME_OVERRIDES = frozenset({_EXCLUDE_NAME, _KEEP_NAME})
_SKIP_DELISTED = "skip_delisted"
_SKIP_BELOW_MINIMUM = "skip_below_minimum"
_SKIP_ZERO = "skip_zero"
#: `signals.reason` values.
_SELECTED = "selected"
_BELOW_CUT = "below_cut"


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

    `event_reason` is the reason of the `decision_events` row that closed the
    state, when a closing event is what closed it (e.g. `dust` or
    `untradable`); it is `None` when the state is closed for any other
    reason (a `skip_*`/`dust` decision kind, a no-side `override`, a buy
    write-off, or a state that is not closed at all).
    """

    state: State
    reason: str | None = None
    remainder: Remainder | None = None
    written_off: bool = False
    event_reason: str | None = None


@dataclass(frozen=True)
class BuyCosts:
    """The cost inputs of buy sizing: the frozen per-side rate and commissions."""

    per_side_bps: float
    commissions: Commissions


PriceOf = Callable[[str], float]


def is_full_exit(decision: DecisionRow) -> bool:
    """Whether `decision` sells the name's whole remaining holding (spec
    Definitions > Full exit): a sell that is a `forced_exit` (any reason), an
    `override`, or a `trade` with reason `left_targets` or `left_universe`. A
    `trade` sell with no reason is a trim; any other sell raises `ValueError`.
    """
    if decision.side != _SELL:
        return False
    if decision.decision in (FORCED_EXIT, _OVERRIDE):
        return True
    if decision.decision == _TRADE:
        if decision.reason in _FULL_EXIT_TRADE_REASONS:
            return True
        if decision.reason is None:
            return False
    raise ValueError(
        f"sell decision {decision.decision_id} ({decision.decision!r}, reason "
        f"{decision.reason!r}) is neither a full exit nor a trim"
    )


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

    - a trim (`planned_notional`): the planned notional minus the filled value
      over all its orders, converted to shares at the reference price;
    - any other sell: the latest order's submitted quantity minus its filled
      quantity, adjusted by the splits with ex-date in (the order's session, S]
      (a notional order: its notional minus its filled value);
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
    what = f"decision {decision.decision_id}"
    if decision.planned_notional is not None:
        planned = _finite(decision.planned_notional, f"{what} notional", non_negative=True)
        sold = sum(_filled(order, fills)[1] for order in mine)
        notional = _clamp(planned - sold, planned)
        return Remainder(quantity=notional / price, notional=notional)
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
    if decision.planned_quantity is not None:
        planned = _finite(decision.planned_quantity, f"{what} quantity", non_negative=True)
        factor = _split_factor(actions_as_of, decision.security_id, _stated_on(decision), session)
        quantity = planned * factor
        return Remainder(quantity=quantity, notional=quantity * price)
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
            row.decision != FORCED_EXIT
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
    """A halt cancel, a `not_received` cancel or an `owner_settled_unknown` cancel
    (req 17, #571): not a funding shortfall."""
    for event in events:
        if event.status in _HALT_CANCEL_STATUSES and event.reason == _HALT:
            return True
        if event.status == _CANCELLED and event.reason in _PROTECTED_CANCEL_REASONS:
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
    (1 + `risk.whole_share_price_buffer`), except that a `whole_share` full
    exit (`is_full_exit`) settles only below one share or below
    `risk.min_order_notional`, so its last share is sold (#366 Q3). A buy
    whose latest order is terminal with a remainder at or above the minimum
    is written off when that order was submitted with no sell of its
    rebalance in flight, has no `cancel_requested`/`cancel_failed` event with
    reason `halt`, and did not end `cancelled` with reason `not_received` or
    `owner_settled_unknown` (`paper settle`, req 17); otherwise it stays open.
    """
    _check_session(session)
    decision_id = _decision_id(decision)
    if decision.decision.startswith(_SKIP_PREFIX) or decision.decision == _DUST:
        return DecisionState(State.CLOSED, decision.decision)
    if decision.decision == _OVERRIDE and decision.side is None:
        return DecisionState(State.CLOSED, decision.reason or _OVERRIDE)
    mine_events = [e for e in decision_events if e.decision_id == decision_id]
    if mine_events:
        latest_event = max(enumerate(mine_events), key=lambda p: (p[1].known_at, p[0]))[1]
        if latest_event.status in _CLOSING_EVENTS:
            return DecisionState(
                State.CLOSED, latest_event.status, event_reason=latest_event.reason
            )

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

    if decision.whole_share and is_full_exit(decision):
        below = left.quantity < 1 or left.notional < frozen.min_order_notional
    elif decision.whole_share:
        price = _price(price_of, decision.security_id)
        below = left.notional < price * (1 + frozen.whole_share_price_buffer)
    else:
        below = left.notional < frozen.min_order_notional
    if left.quantity <= 0 or below:
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


def _latest[R](rows: Sequence[R], key: Callable[[R], tuple[object, ...]]) -> R:
    """The latest of `rows` by `key`; on a tie, the one later in the input."""
    return max(enumerate(rows), key=lambda p: (*key(p[1]), p[0]))[1]


def _run_windows(runs: Iterable[PaperRunRow]) -> dict[int, int]:
    windows: dict[int, int] = {}
    for run in runs:
        if run.run_id is None:
            raise ValueError("a paper run has no run_id")
        windows[run.run_id] = run.window_id
    return windows


def _in_window(run_id: int | None, runs: Mapping[int, int], window_id: int, what: str) -> bool:
    """Whether a row of run `run_id` belongs to the window; a run not among
    `runs` at all raises, since its row cannot be placed."""
    if run_id is None or run_id not in runs:
        raise ValueError(f"{what} names run {run_id}, which is not among the runs given")
    return runs[run_id] == window_id


def _latest_flag(security_id: str, positions_daily: Sequence[PositionDailyRow]) -> bool | None:
    """The name's latest `tradable` flag: its latest row carrying one."""
    flagged = [
        row
        for row in positions_daily
        if row.security_id == security_id and row.tradable is not None
    ]
    if not flagged:
        return None
    return _latest(flagged, lambda row: (row.session, row.known_at)).tradable


def _latest_decision(security_id: str, decisions: Sequence[DecisionRow]) -> DecisionRow | None:
    mine = [d for d in decisions if d.security_id == security_id]
    if not mine:
        return None
    return _latest(mine, lambda d: (d.known_at, _decision_id(d)))


def _latest_event(
    decision: DecisionRow, decision_events: Sequence[DecisionEventRow]
) -> DecisionEventRow | None:
    decision_id = _decision_id(decision)
    mine = [e for e in decision_events if e.decision_id == decision_id]
    if not mine:
        return None
    return _latest(mine, lambda e: (e.known_at,))


def _carried(
    security_id: str,
    adjustments: Iterable[AdjustmentRow],
    actions_as_of: pl.DataFrame,
    session: date,
    flag_false: bool,
) -> float:
    """The carried residue of the name, split-adjusted through `session`; an
    `origin = untradable` row counts only while the latest flag is false."""
    total = 0.0
    for row in adjustments:
        if row.kind != _CARRIED_RESIDUE or row.security_id != security_id:
            continue
        if row.origin not in _RESIDUE_ORIGINS:
            raise ValueError(
                f"carried residue {row.adjustment_id} has origin {row.origin!r}, "
                f"not one of {sorted(_RESIDUE_ORIGINS)}"
            )
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
    *,
    window_id: int,
    runs: Sequence[PaperRunRow],
) -> float:
    """The name's residue quantity on the ledger's session (module docstring;
    spec req 14): carried + dust + untradable, capped at the holding.

    `ledger.through` is S, the session the holding and the split-adjusted
    carried quantity are stated for; `actions_as_of` is read at close(S-1).
    Rows are placed in the window `window_id` through `runs` (`run_id` ->
    `paper_runs.window_id`) and an adjustment's own `window_id`: rows of another
    window are ignored, a row of a run not among `runs` raises. Adjustments and
    marks dated after S are left out, as the ledger leaves them out. `decisions`
    and `decision_events` are those known to the run on S, the run's own
    included (its `untradable` exit counts at once). A name not held has no
    residue; a negative holding raises (the system is long-only).
    """
    _check_session(ledger.through)
    through = ledger.through
    windows = _run_windows(runs)
    held = _finite(ledger.positions.get(security_id, 0.0), f"holding of {security_id}")
    if held < 0:
        raise ValueError(f"holding of {security_id} is {held}: the ledger is short")
    carried_rows = [
        row for row in adjustments if row.window_id == window_id and row.session <= through
    ]
    decisions = [
        d
        for d in decisions
        if _in_window(d.run_id, windows, window_id, f"decision {d.decision_id}")
    ]
    decision_events = [
        e
        for e in decision_events
        if _in_window(e.run_id, windows, window_id, f"event of decision {e.decision_id}")
    ]
    marks = [
        m
        for m in positions_daily
        if _in_window(m.run_id, windows, window_id, f"mark of {m.security_id} on {m.session}")
        and m.session <= through
    ]
    if held == 0:
        return 0.0
    flag_false = _latest_flag(security_id, marks) is False
    carried = min(_carried(security_id, carried_rows, actions_as_of, through, flag_false), held)
    dust = untradable = 0.0
    latest = _latest_decision(security_id, decisions)
    if latest is not None and latest.decision == FORCED_EXIT:
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

    `runs` places events and decisions in the window (`run_id` ->
    `paper_runs.window_id`): rows of another window or rebalance are ignored,
    and a row of a run not among `runs` raises, so an incomplete run list can
    never hide a `missed` event or an open decision. `decision_states` pairs
    each decision with its `decision_state`. A rebalance with no decision stays
    `pending` (and lapses to `missed` on schedule): an unplanned rebalance is
    never `executed`. Raises `ValueError` for a T_i before the window's
    `first_rebalance_session`, one whose fill session is after S (not due yet), a
    date that is not a rebalance session, a rebalance journaled both `executed`
    and `missed`, and a decision of the window with no rebalance session that is
    not a forced exit.
    """
    _check_session(session)
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    if rebalance_session < window.first_rebalance_session:
        raise ValueError(
            f"rebalance {rebalance_session} is before the window's first rebalance "
            f"{window.first_rebalance_session}"
        )
    if fill_session(rebalance_session) > session:
        raise ValueError(f"rebalance {rebalance_session} is not due on {session}")
    windows = _run_windows(runs)
    statuses = {
        event.status
        for event in rebalance_events
        if _in_window(event.run_id, windows, window.window_id, f"rebalance event {event}")
        and event.rebalance_session == rebalance_session
    }
    if len(statuses) > 1:
        raise ValueError(f"rebalance {rebalance_session} is journaled both {sorted(statuses)}")
    if statuses:
        return RebalanceState(statuses.pop())
    mine: list[DecisionState] = []
    for decision, state in decision_states:
        what = f"decision {decision.decision_id}"
        if not _in_window(decision.run_id, windows, window.window_id, what):
            continue
        if decision.decision == FORCED_EXIT:
            continue
        if decision.rebalance_session is None:
            raise ValueError(f"{what} is not a forced exit and has no rebalance session")
        if decision.rebalance_session == rebalance_session:
            mine.append(state)
    if mine and all(state.state in (State.CLOSED, State.SETTLED) for state in mine):
        return RebalanceState.EXECUTED
    return RebalanceState.PENDING


def stop_session(requested_at: datetime) -> date:
    """The stop session (spec reqs 9 and 14): the calendar session containing
    the `requested` row's `at` (its New York date), or the next session when
    that date is not a session. A naive `requested_at` raises `ValueError`."""
    day = ensure_tz_aware_utc(requested_at, field_name="requested_at").astimezone(_NEW_YORK).date()
    return day if is_session(day) else next_session(day)


# --- decisions from a plan (T53b) --------------------------------------------------


class AssetFlags(Protocol):
    """What `decisions_from` reads from the broker's `assets` answer for a name
    (`adapters.broker.Asset` fits; this module imports no adapter)."""

    @property
    def fractionable(self) -> bool:
        """Whether the broker trades the name in fractional shares."""
        ...


@dataclass(frozen=True)
class Decision:
    """One planned `decisions` row before the run journals it (`row`)."""

    security_id: str
    rebalance_session: date
    decision: str
    whole_share: bool
    target_weight: float
    drifted_weight: float
    side: str | None = None
    planned_notional: float | None = None
    planned_quantity: float | None = None
    target_notional: float | None = None
    reason: str | None = None
    override_id: int | None = None

    def row(
        self,
        *,
        run_id: int,
        known_at: datetime,
        ingested_at: datetime,
        book_id: str,
    ) -> DecisionRow:
        """The `decisions` row for run `run_id`; its id is assigned on insert.
        `book_id` is the window's book (ADR 0015 seam 1, plan T133)."""
        return DecisionRow(
            run_id=run_id,
            rebalance_session=self.rebalance_session,
            security_id=self.security_id,
            target_weight=self.target_weight,
            drifted_weight=self.drifted_weight,
            side=self.side,
            planned_notional=self.planned_notional,
            planned_quantity=self.planned_quantity,
            target_notional=self.target_notional,
            whole_share=self.whole_share,
            decision=self.decision,
            reason=self.reason,
            override_id=self.override_id,
            book_id=book_id,
            known_at=known_at,
            ingested_at=ingested_at,
        )


@dataclass(frozen=True)
class Signal:
    """One planned `signals` row: a universe member's score, rank and reason."""

    security_id: str
    rebalance_session: date
    reason: str
    score: float | None = None
    rank: int | None = None

    def row(self, *, run_id: int, known_at: datetime, ingested_at: datetime) -> SignalRow:
        """The `signals` row for run `run_id`."""
        return SignalRow(
            run_id=run_id,
            rebalance_session=self.rebalance_session,
            security_id=self.security_id,
            score=self.score,
            rank=self.rank,
            reason=self.reason,
            known_at=known_at,
            ingested_at=ingested_at,
        )


@dataclass(frozen=True)
class Decisions:
    """`decisions_from`'s answer: the decisions (by `security_id`), the signals
    (by rank, the excluded last) and the `paper_plans` live-capital count."""

    decisions: tuple[Decision, ...]
    signals: tuple[Signal, ...]
    n_orders_below_min_at_live_capital: int


@dataclass(frozen=True)
class _Intent:
    """A name's trade before the minimum: side, notional, and for a full exit its
    shares at S."""

    side: str
    notional: float
    full_exit_reason: str | None = None
    held: float = 0.0


def _visible_splits(actions_as_of: pl.DataFrame, cutoff: datetime) -> pl.DataFrame:
    """The frame's splits, refusing any row known after `cutoff` (close(S-1))."""
    if "known_at" not in actions_as_of.columns:
        raise ValueError("actions_as_of has no known_at column to check against close(S-1)")
    splits = actions_as_of.filter(pl.col("action_type") == _SPLIT)
    late = splits.filter(pl.col("known_at") > cutoff)
    if late.height:
        names = sorted(set(late["security_id"].to_list()))
        raise ValueError(f"actions_as_of holds splits of {names} known after close(S-1) ({cutoff})")
    return splits


def _plan_overrides(overrides: Iterable[OverrideRow], rebalance: date) -> dict[str, OverrideRow]:
    """The `exclude_name` and `keep_name` overrides naming `rebalance`, by name."""
    mine: dict[str, OverrideRow] = {}
    for row in overrides:
        if row.kind not in _NAME_OVERRIDES or row.rebalance_session != rebalance:
            continue
        if row.security_id is None:
            raise ValueError(f"override {row.override_id} ({row.kind}) names no security")
        if row.override_id is None:
            raise ValueError(f"override of {row.security_id} has no override_id")
        if row.security_id in mine:
            raise ValueError(
                f"overrides {mine[row.security_id].override_id} and {row.override_id} both "
                f"name {row.security_id} for rebalance {rebalance}"
            )
        mine[row.security_id] = row
    return mine


def _ranked(scores: Mapping[str, float]) -> dict[str, int]:
    """Rank 1 for the highest score; equal scores by `security_id`."""
    order = sorted(scores, key=lambda sid: (-scores[sid], sid))
    return {sid: rank for rank, sid in enumerate(order, start=1)}


#: A `plan.exclusions` key's shape (`_signals`): lowercase letters, digits and
#: underscores, not starting with the `excluded_` prefix the database itself adds.
#: `Plan.exclusions` is built only by `backtest.engine._plan`, which already refuses
#: a signal's exclusion key the family's registry does not declare (`engine.py:202-204`,
#: against `config.FAMILIES[family].exclusion_reasons`); `_signals` has no `family` to
#: re-check that set against (`Plan` does not carry one), so this is the cheaper, local
#: half of that defense -- a malformed key (empty, double-prefixed, or holding
#: whitespace/punctuation the `signals.reason` `CHECK` would still accept because it
#: tests only the `excluded_` prefix) is refused here instead of reaching the journal.
_EXCLUSION_REASON_SHAPE = re.compile(r"[a-z][a-z0-9_]*")


def _signals(plan: Plan) -> tuple[Signal, ...]:
    """One `Signal` per universe member: `selected` or `below_cut` for a scored
    member, `f"{EXCLUDED_REASON_PREFIX}{reason}"` for each reason `plan.exclusions`
    declares (ADR 0014 point 3, #1153; T127b, #1209) -- `excluded_no_history` for
    momentum, unchanged. `plan.exclusions` holds every reason the plan's family
    declares (enforced by the one builder, `backtest.engine._plan`), so its keys
    are trusted as the family's declared set; a key of the wrong shape
    (`_EXCLUSION_REASON_SHAPE`) or starting with `EXCLUDED_REASON_PREFIX` already
    raises here instead, and `excluded_no_history` (kept on `Plan` for Phase 4's
    `decisions_from`) must agree with `exclusions.get("no_history", ())`, else it
    raises rather than silently trusting the legacy field. The database checks
    only that a `signals` reason starts with `excluded_` (`EXCLUDED_REASON_PREFIX`);
    `_signals` is the one place that checks the suffix is shaped like a real
    reason and that the legacy momentum field agrees with it."""
    for sid, score in plan.scores.items():
        _finite(score, f"score of {sid}")
    ranks = _ranked(plan.scores)
    members = set(plan.members)
    declared = set(plan.exclusions)
    malformed = sorted(
        reason
        for reason in declared
        if not _EXCLUSION_REASON_SHAPE.fullmatch(reason)
        or reason.startswith(EXCLUDED_REASON_PREFIX)
    )
    if malformed:
        raise ValueError(f"plan.exclusions has malformed reason key(s) {malformed}")
    no_history_ids = set(plan.exclusions.get("no_history", ()))
    if set(plan.excluded_no_history) != no_history_ids:
        if "no_history" not in declared:
            raise ValueError(
                f"excluded_no_history is non-empty but the plan's declared exclusion "
                f"reasons {sorted(declared)} do not include no_history"
            )
        raise ValueError(
            f"excluded_no_history {sorted(plan.excluded_no_history)} disagrees with "
            f"exclusions['no_history'] {sorted(no_history_ids)}"
        )
    reason_of: dict[str, str] = {}
    for reason, ids in plan.exclusions.items():
        for sid in ids:
            if sid in reason_of:
                raise ValueError(f"{sid} is excluded for both {reason_of[sid]!r} and {reason!r}")
            reason_of[sid] = reason
    excluded = set(reason_of)
    if set(plan.scores) | excluded != members or set(plan.scores) & excluded:
        raise ValueError("the plan's scores and exclusions do not partition its members")
    if not set(plan.targets) <= set(plan.scores):
        raise ValueError("the plan has targets that are not scored members")
    scored = [
        Signal(
            security_id=sid,
            rebalance_session=plan.session,
            reason=_SELECTED if sid in plan.targets else _BELOW_CUT,
            score=plan.scores[sid],
            rank=ranks[sid],
        )
        for sid in sorted(ranks, key=ranks.__getitem__)
    ]
    unscored = [
        Signal(
            security_id=sid,
            rebalance_session=plan.session,
            reason=f"{EXCLUDED_REASON_PREFIX}{reason_of[sid]}",
        )
        for sid in sorted(excluded)
    ]
    return (*scored, *unscored)


def _buy_targets(
    intents: Mapping[str, _Intent],
    cash_before: float,
    price_of: PriceOf,
    costs: BuyCosts,
) -> dict[str, float]:
    """Each buy's target (`target_notional`'s formula, spec Definitions): its
    notional x min(1, spendable / the buys' sum), spendable after costs on cash
    plus every plan sell's value at the reference price."""
    proceeds = sum(i.notional for i in intents.values() if i.side == _SELL)
    buys = {sid: i.notional for sid, i in intents.items() if i.side == _BUY}
    total = sum(buys.values())
    _finite(costs.per_side_bps, "costs.per_side_bps", non_negative=True)
    targets: dict[str, float] = {}
    for sid, notional in buys.items():
        spendable = buy_notional_after_costs(
            cash_before + proceeds,
            costs.per_side_bps,
            costs.commissions,
            price=_price(price_of, sid),
        )
        targets[sid] = notional * min(1.0, _finite(spendable, "spendable") / total)
    return targets


def decisions_from(
    plan: Plan,
    ledger: Ledger,
    overrides: Sequence[OverrideRow],
    assets: Mapping[str, AssetFlags],
    listings_at: Mapping[str, date | None],
    open_forced_exits: Collection[str],
    frozen: RiskConfig,
    settings: Settings,
    *,
    price_of: PriceOf,
    actions_as_of: pl.DataFrame,
    costs: BuyCosts,
) -> Decisions:
    """The decisions of rebalance T_i = `plan.session` on the run's session
    S = `ledger.through` (spec Definitions > Decision, reqs 7 step 6 and 9).

    One decision per held-or-target name, by `security_id`:

    - no decision for a name in `open_forced_exits` (its open or in-flight
      `forced_exit` decision handles it, req 3);
    - `skip_delisted` for a name in `listings_at` whose listing ended at
      close(S-1) (its end session on or before S-1, or unknown);
    - an `exclude_name` override (`overrides` naming T_i) sells a held name
      whole (`override`, side `sell`, reason `exclude_name`) and buys nothing
      for an unheld target (`override`, no side, reason `exclude_name`), its weight left in cash; a
      `keep_name` override trades nothing (`override`, no side, reason
      `keep_name`); either carries its `override_id`;
    - a held name outside the universe is sold whole by quantity (`trade`,
      reason `left_universe`), a held member outside the targets likewise
      (`left_targets`);
    - any other name trades notional: target weight minus drifted ledger weight,
      times equity (a buy, or a trim sold by notional);
    - a full exit worth less than `risk.min_order_notional`, or a whole-share one
      below one share, is plan-time `dust` (never by the buffered-share minimum,
      ADR 0010 amendment 2026-09-30); a trade below the minimum is
      `skip_below_minimum`, and exactly zero `skip_zero`.

    Weights and notionals are at S: the ledger's quantities and `price_of`, the
    reference price on S (the close(S-1) close in post-split shares, as
    `decision_state` reads it). A planned quantity is stated in T_i's units, as
    `remainder` reads it: the shares at S divided by the splits with ex-date in
    (T_i, S]. `actions_as_of` is read at close(S-1); a split row known after it
    raises, as the run cannot have read it. A buy's `target_notional` is the
    Definitions formula (`target_notional`): its planned notional x min(1,
    spendable / the planned buys' sum), spendable from
    `costs.buy_notional_after_costs` on `ledger.cash` plus every sell the plan
    made at the reference price (a forced exit's proceeds stay out).

    `whole_share` is the name's `fractionable` flag negated, from `assets`
    (keyed by `security_id`; the caller maps symbols); a name that would trade
    and is missing from it raises. The plan side (targets, signals) reads only
    `plan`, the engine's read at close(T_i). `n_orders_below_min_at_live_capital`
    counts every name with an order to place (a trade, an `exclude_name` sell,
    and the `skip_below_minimum` and `dust` ones) whose notional scaled to
    `paper.live_capital_reference` / equity is below `risk.min_order_notional`.
    """
    session = ledger.through
    _check_session(session)
    if session <= plan.session:
        raise ValueError(
            f"the ledger is stated for {session}, which is not after the rebalance {plan.session}"
        )
    if ledger.short_names:
        raise ValueError(f"the ledger is short {list(ledger.short_names)}")
    previous = previous_session(session)
    splits = _visible_splits(actions_as_of, session_close(previous))
    marks = {sid: _price(price_of, sid) for sid in ledger.positions}
    equity = ledger.equity(marks)
    if not (math.isfinite(equity) and equity > 0):
        raise ValueError(f"equity is {equity}, the plan needs a positive equity")
    drifted = ledger.weights(marks)
    for sid, weight in plan.targets.items():
        _finite(weight, f"target weight of {sid}", non_negative=True)
    signals = _signals(plan)
    named = _plan_overrides(overrides, plan.session)
    universe = set(plan.members)
    held = {sid for sid, quantity in ledger.positions.items() if quantity > 0}
    targets = {sid for sid, weight in plan.targets.items() if weight > 0}

    def ended(sid: str) -> bool:
        if sid not in listings_at:
            return False
        end = listings_at[sid]
        return end is None or end <= previous

    def whole_share(sid: str, *, required: bool) -> bool:
        if sid not in assets:
            if required:
                raise ValueError(f"{sid} is missing from the assets read")
            return False
        return not assets[sid].fractionable

    def base(sid: str) -> dict[str, Any]:
        return {
            "security_id": sid,
            "rebalance_session": plan.session,
            "target_weight": plan.targets.get(sid, 0.0),
            "drifted_weight": drifted.get(sid, 0.0),
        }

    decided: dict[str, Decision] = {}
    intents: dict[str, _Intent] = {}  # names with an order to place, before the minimum
    for sid in sorted(held | targets):
        if sid in open_forced_exits:
            continue
        if ended(sid):
            decided[sid] = Decision(
                **base(sid), decision=_SKIP_DELISTED, whole_share=whole_share(sid, required=False)
            )
            continue
        override = named.get(sid)
        if override is not None and (override.kind == _KEEP_NAME or sid not in held):
            decided[sid] = Decision(
                **base(sid),
                decision=_OVERRIDE,
                whole_share=whole_share(sid, required=False),
                reason=override.kind,
                override_id=override.override_id,
            )
            continue
        value = ledger.positions.get(sid, 0.0) * marks.get(sid, 0.0)
        if override is not None:
            intent = _Intent(_SELL, value, _EXCLUDE_NAME, ledger.positions[sid])
        elif sid in held and sid not in universe:
            intent = _Intent(_SELL, value, _LEFT_UNIVERSE, ledger.positions[sid])
        elif sid in held and sid not in targets:
            intent = _Intent(_SELL, value, _LEFT_TARGETS, ledger.positions[sid])
        else:
            delta = (plan.targets.get(sid, 0.0) - drifted.get(sid, 0.0)) * equity
            intent = _Intent(_BUY if delta > 0 else _SELL, abs(delta))
        intents[sid] = intent

    minimum = frozen.min_order_notional
    for sid, intent in list(intents.items()):
        whole = whole_share(sid, required=True)
        kind = _OVERRIDE if intent.full_exit_reason == _EXCLUDE_NAME else _TRADE
        override_id = named[sid].override_id if sid in named else None
        if intent.full_exit_reason is not None:
            if intent.notional < minimum or (whole and intent.held < 1):
                decided[sid] = Decision(
                    **base(sid),
                    decision=_DUST,
                    whole_share=whole,
                    reason=intent.full_exit_reason,
                    override_id=override_id,
                )
                continue
            factor = _split_factor(splits, sid, plan.session, session)
            decided[sid] = Decision(
                **base(sid),
                decision=kind,
                whole_share=whole,
                side=_SELL,
                planned_quantity=intent.held / factor,
                reason=intent.full_exit_reason,
                override_id=override_id,
            )
            continue
        if intent.notional == 0 or intent.notional < minimum:
            decided[sid] = Decision(
                **base(sid),
                decision=_SKIP_ZERO if intent.notional == 0 else _SKIP_BELOW_MINIMUM,
                whole_share=whole,
            )
            if intent.notional == 0:
                del intents[sid]
            continue
        decided[sid] = Decision(
            **base(sid),
            decision=_TRADE,
            whole_share=whole,
            side=intent.side,
            planned_notional=intent.notional,
        )

    placed = {sid: i for sid, i in intents.items() if decided[sid].side is not None}
    for sid, target in _buy_targets(placed, ledger.cash, price_of, costs).items():
        decided[sid] = replace(decided[sid], target_notional=target)

    live_scale = settings.paper.live_capital_reference / equity
    below = sum(1 for i in intents.values() if i.notional * live_scale < minimum)
    return Decisions(
        decisions=tuple(decided[sid] for sid in sorted(decided)),
        signals=signals,
        n_orders_below_min_at_live_capital=below,
    )


def current_listings(listings: pl.DataFrame, day: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from` on or before `day`
    (the first in frame order on a tie, as `universe_as_of` reads it). The one
    tie rule planning, the wrapper, the run, reconcile_run and the window's
    flatness check share (#534, #705). A row with no `valid_from` is always
    current but never beats a dated row."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        valid_from = row["valid_from"]
        if valid_from is not None and valid_from > day:
            continue
        held = current.get(row["security_id"])
        if held is None or (
            valid_from is not None
            and (held["valid_from"] is None or valid_from > held["valid_from"])
        ):
            current[row["security_id"]] = row
    return current

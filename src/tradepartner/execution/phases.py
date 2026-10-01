"""Phase orders, pure: sells and buys (Phase 4 spec req 3; plan T60c).

Three functions build what one wrapper phase (T60b) checks and submits; no
broker, no journal write, no clock.

**`sell_orders`** takes the pending rebalance's decisions and the forced exits
to hand to the sells phase, each with its `plan.decision_state` on S, and
orders the **open** sells (`reattempts.attempt_scope`), each by **quantity**,
never by notional (ADR 0010 amendment 2026-09-30):

- a **full exit** (`plan.is_full_exit`) sells the reconciled holding on S
  (`ledger`), less the name's residue (`plan.residue`, T52b) for a plan full
  exit (`left_targets`, `left_universe`, `exclude_name`) or a `window_stop`
  exit, and the **whole** holding for a `delisted` or `untargeted_receipt`
  exit. A re-attempt after a partial fill sells the holding that is left, so
  the first attempt and every later one follow one rule. Its whole-share basis
  is the decision's journaled `whole_share` flag alone (#395);
- a **trim** sells its remainder (`DecisionState.remainder`, the trim's notional
  left converted at the reference price; a planned quantity there is already
  adjusted by the splits in (T_i, S]), by whole shares when the decision's flag
  is set or the name is no longer `fractionable`.

Every quantity is rounded down to `quantity_decimals` (`alpaca.quantity_decimals`)
and, on a whole-share basis, floored, so no sell exceeds the holding the risk
check rounds the same way. The per-name skips of the phase, each a
`risk.Skip` with its `decision_events` reason: `skip_untradable` for a trim or
a plan full exit of a name not `tradable`, `untradable` for a forced exit;
`dust` for a full exit below `risk.min_order_notional` or whose whole-share
floor is zero (a 0.4-share receipt); `skip_below_one_share` for a whole-share
trim that floors to zero; `skip_below_minimum` for a trim below the minimum.
At most one sell per name: two sell decisions for one name, attempted or in
flight, raise `ValueError` (never two sells for one name). Forced exits sell in
the same list; their proceeds reach the buys only as the cash read after the
sells, never as a larger target (`buy_orders`).

**`buy_orders`** sizes the open buys from `cash` (the account's cash after
the sells, less the open-buy reserve, which the caller computes) through
`risk.size_buys`: notional buys first, whole-share buys last. A buy whose
attempt falls below the minimum is **deferred** (its id in `deferred`, no skip
row, no cap count); a name not `tradable` is `skip_untradable`. The scale is
capped at 1 against the buys' remainders, so cash beyond the targets (a forced
exit's proceeds) is never spent. `cash_left` is the cash minus every order's
modelled cost (`risk._buy_cash`), never negative.

**`requests_for`** turns a phase's orders into `OrderRequest`s, validated
before any is returned: the symbol from the master's listing at close(S-1),
the id from `ids.client_order_id` off the run's session with the attempt
counted from `orders_on_session` (and from this batch), and each id checked
against `max_length` when it is set.

No numeric literal other than 0, 1, 2 and -1 appears here
(`tests/test_no_literals.py`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import polars as pl

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution import risk
from tradepartner.execution.exits import ExitAsset
from tradepartner.execution.ids import client_order_id, next_attempt
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, DecisionState, PriceOf, is_full_exit
from tradepartner.execution.reattempts import attempt_scope
from tradepartner.execution.risk import BuyToSize, Skip, round_down, size_buys
from tradepartner.store.journal import DecisionRow, OrderRow
from tradepartner.store.schema import DELISTED_REASON, UNTARGETED_RECEIPT_REASON

__all__ = ["PhaseOrder", "PhaseOrders", "buy_orders", "requests_for", "sell_orders"]

_BUY = "buy"
_SELL = "sell"
_FORCED_EXIT = "forced_exit"
#: Forced-exit reasons that sell the whole holding, residue included (spec req 3).
_WHOLE_HOLDING_REASONS = frozenset({DELISTED_REASON, UNTARGETED_RECEIPT_REASON})
_DUST = "dust"
_UNTRADABLE = "untradable"
_SKIP_UNTRADABLE = "skip_untradable"
_SKIP_BELOW_ONE_SHARE = "skip_below_one_share"
_SKIP_BELOW_MINIMUM = "skip_below_minimum"


@dataclass(frozen=True)
class PhaseOrder:
    """One order of a phase before the risk check: exactly one of `notional`
    (a buy) and `quantity`. `decision` is the decision's kind; `full_exit` is
    `plan.is_full_exit`; `whole_share` the order's basis; `price` the reference
    price on S; `target_weight` the decision's."""

    decision_id: int
    security_id: str
    side: str
    decision: str
    price: float
    notional: float | None = None
    quantity: float | None = None
    full_exit: bool = False
    whole_share: bool = False
    target_weight: float | None = None

    def to_risk(self, symbol: str, *, listing_ended: bool) -> risk.PhaseOrder:
        """The `risk.check_phase` candidate for this order, with the name's
        `symbol` and whether its listing ended at close(S-1)."""
        return risk.PhaseOrder(
            decision_id=self.decision_id,
            security_id=self.security_id,
            symbol=symbol,
            side=self.side,
            decision=self.decision,
            price=self.price,
            notional=self.notional,
            quantity=self.quantity,
            full_exit=self.full_exit,
            whole_share=self.whole_share,
            target_weight=self.target_weight,
            listing_ended=listing_ended,
        )


@dataclass(frozen=True)
class PhaseOrders:
    """A phase's orders and per-name skips; for a buys phase also the ids its
    sizing deferred and the cash its orders leave (`None` for a sells phase)."""

    orders: tuple[PhaseOrder, ...]
    skips: tuple[Skip, ...]
    deferred: tuple[int, ...] = ()
    cash_left: float | None = None


def _check_session(session: date) -> None:
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")


def _check_actions(actions_as_of: pl.DataFrame, session: date) -> None:
    """No row of `actions_as_of` known after close(S-1) (no look-ahead)."""
    if "known_at" not in actions_as_of.columns:
        raise ValueError("actions_as_of has no known_at column; read it with live_actions_as_of")
    cutoff = session_close(previous_session(session))
    late = actions_as_of.filter(pl.col("known_at").is_null() | (pl.col("known_at") > cutoff))
    if late.height:
        names = sorted(set(late["security_id"].to_list()))
        raise ValueError(f"actions_as_of holds rows of {names} not known by close(S-1) {cutoff}")


def _finite(value: float, what: str) -> float:
    if not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{what} is {value}, must be a finite non-negative number")
    return value


def _price(price_of: PriceOf, security_id: str) -> float:
    price = price_of(security_id)
    if not (math.isfinite(price) and price > 0):
        raise ValueError(f"reference price of {security_id} is {price}, must be positive")
    return price


def _asset(security_id: str, assets: Mapping[str, ExitAsset]) -> ExitAsset:
    if security_id not in assets:
        raise ValueError(f"{security_id} is missing from the assets read")
    return assets[security_id]


def _id(decision: DecisionRow) -> int:
    if decision.decision_id is None:
        raise ValueError(f"decision of {decision.security_id} has no decision_id")
    return decision.decision_id


def _full_exit_quantity(
    decision: DecisionRow, ledger: Ledger, residues: Mapping[str, float]
) -> float:
    """The holding on S, less the residue unless a `delisted` or
    `untargeted_receipt` exit sells it whole."""
    sid = decision.security_id
    held = _finite(ledger.positions.get(sid, 0.0), f"holding of {sid}")
    if decision.decision == _FORCED_EXIT and decision.reason in _WHOLE_HOLDING_REASONS:
        return held
    left = _finite(residues.get(sid, 0.0), f"residue of {sid}")
    if left > held:
        raise ValueError(f"residue of {sid} is {left}, above the holding {held}")
    return held - left


def _sell_skip(
    decision: DecisionRow,
    full_exit: bool,
    asset: ExitAsset,
    whole: bool,
    quantity: float,
    price: float,
    frozen: RiskConfig,
) -> str | None:
    if not asset.tradable:
        return _UNTRADABLE if decision.decision == _FORCED_EXIT else _SKIP_UNTRADABLE
    if whole and quantity < 1:
        return _DUST if full_exit else _SKIP_BELOW_ONE_SHARE
    if quantity <= 0 or quantity * price < frozen.min_order_notional:
        return _DUST if full_exit else _SKIP_BELOW_MINIMUM
    return None


def sell_orders(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    ledger: Ledger,
    residues: Mapping[str, float],
    forced_exits: Sequence[DecisionRow],
    actions_as_of: pl.DataFrame,
    price_of: PriceOf,
    assets: Mapping[str, ExitAsset],
    frozen: RiskConfig,
    *,
    session: date,
    quantity_decimals: int,
) -> PhaseOrders:
    """The sells phase's orders and per-name skips on S = `session` (module
    docstring), in decision-id order.

    `decisions` are the pending rebalance's decisions, both sides, and
    `forced_exits` the journaled forced exits handed to this phase (the open
    ones to re-attempt and this run's new ones), each once; `states` maps every
    id to its `plan.decision_state` on S. `ledger` is stated for S, `residues`
    is `plan.residue` per name, `actions_as_of` is read at close(S-1) (a row
    known later raises), `price_of` is the reference price on S, `assets` the
    phase's `assets` read by `security_id` (a name missing raises), `frozen`
    the window's frozen `risk.*` section and `quantity_decimals` the broker's
    quantity precision. Raises `ValueError` for a forced exit among
    `decisions`, a non-forced-exit or non-sell among `forced_exits`, two sells
    for one name, and every malformed input `attempt_scope` refuses.
    """
    _check_session(session)
    if ledger.through != session:
        raise ValueError(f"the ledger is stated for {ledger.through}, not the session {session}")
    _check_actions(actions_as_of, session)
    if isinstance(quantity_decimals, bool) or not (
        isinstance(quantity_decimals, int) and quantity_decimals >= 0
    ):
        raise ValueError(f"quantity_decimals is {quantity_decimals!r}")
    if any(d.decision == _FORCED_EXIT for d in decisions):
        raise ValueError("pass forced exits through forced_exits, not decisions")
    if any(d.decision != _FORCED_EXIT or d.side != _SELL for d in forced_exits):
        raise ValueError("forced_exits holds a decision that is not a forced-exit sell")
    rows = [*decisions, *forced_exits]
    scope = attempt_scope(rows, states, phase=_SELL)
    by_id = {_id(d): d for d in rows}
    names = Counter(
        [a.decision.security_id for a in scope.attempts]
        + [by_id[i].security_id for i in scope.in_flight]
    )
    twice = sorted(name for name, count in names.items() if count > 1)
    if twice:
        raise ValueError(f"two sell decisions for {twice} in one phase")

    orders: list[PhaseOrder] = []
    skips: list[Skip] = []
    for attempt in scope.attempts:
        decision = attempt.decision
        sid = decision.security_id
        asset = _asset(sid, assets)
        price = _price(price_of, sid)
        full_exit = is_full_exit(decision)
        if full_exit:
            quantity = _full_exit_quantity(decision, ledger, residues)
            whole = decision.whole_share
        else:
            quantity = _finite(attempt.remainder.quantity, f"remainder of {sid}")
            whole = decision.whole_share or not asset.fractionable
        quantity = round_down(quantity, quantity_decimals)
        if whole:
            quantity = float(math.floor(quantity))
        reason = _sell_skip(decision, full_exit, asset, whole, quantity, price, frozen)
        if reason is not None:
            skips.append(Skip(_id(decision), sid, reason))
            continue
        orders.append(
            PhaseOrder(
                decision_id=_id(decision),
                security_id=sid,
                side=_SELL,
                decision=decision.decision,
                price=price,
                quantity=quantity,
                full_exit=full_exit,
                whole_share=whole,
                target_weight=decision.target_weight,
            )
        )
    return PhaseOrders(tuple(orders), tuple(skips))


def buy_orders(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    cash: float,
    planned_sells: PhaseOrders,
    price_of: PriceOf,
    costs: BuyCosts,
    assets: Mapping[str, ExitAsset],
    frozen: RiskConfig,
) -> PhaseOrders:
    """The buys phase's orders, skips, deferred ids and cash left (module
    docstring).

    `decisions` are the pending rebalance's decisions, both sides (and any
    forced exits), with their `plan.decision_state` on S in `states`; `cash`
    is the account's cash read after the sells less the open-buy reserve, at
    least 0; `planned_sells` is this run's sells phase (`sell_orders`), whose
    proceeds are in `cash` already and whose names are never bought (a buy of
    one raises `ValueError`). Orders are notional buys in decision-id order,
    then whole-share buys; a buy of a name no longer `fractionable` goes by
    whole shares.
    """
    _finite(cash, "cash")
    scope = attempt_scope(decisions, states, phase=_BUY)
    sold = {order.security_id for order in planned_sells.orders}
    both = sorted(sold & {a.decision.security_id for a in scope.attempts})
    if both:
        raise ValueError(f"the phase both sells and buys {both}")

    skips: list[Skip] = []
    to_size: list[BuyToSize] = []
    for attempt in scope.attempts:
        decision = attempt.decision
        asset = _asset(decision.security_id, assets)
        if not asset.tradable:
            skips.append(Skip(_id(decision), decision.security_id, _SKIP_UNTRADABLE))
            continue
        lost = True if not asset.fractionable else None
        to_size.append(BuyToSize(decision, attempt.remainder, whole_share=lost))

    by_id = {_id(b.decision): b for b in to_size}
    orders: list[PhaseOrder] = []
    deferred: list[int] = []
    spent = Decimal(0)
    for sizing in size_buys(to_size, cash, price_of, frozen, costs):
        decision = by_id[sizing.decision_id].decision
        if sizing.deferred:
            deferred.append(sizing.decision_id)
            continue
        price = _price(price_of, decision.security_id)
        whole = sizing.quantity is not None
        spent += risk._buy_cash(sizing.notional, sizing.quantity, price, whole, frozen, costs)
        orders.append(
            PhaseOrder(
                decision_id=sizing.decision_id,
                security_id=decision.security_id,
                side=_BUY,
                decision=decision.decision,
                price=price,
                notional=sizing.notional,
                quantity=sizing.quantity,
                whole_share=whole,
                target_weight=decision.target_weight,
            )
        )
    left = Decimal(repr(float(cash))) - spent
    if left < 0:
        raise ValueError(f"the buys need {spent}, more than the cash {cash}")
    return PhaseOrders(tuple(orders), tuple(skips), tuple(sorted(deferred)), float(left))


def requests_for(
    phase_orders: PhaseOrders,
    listings_at: Mapping[str, str | None],
    prefix: str,
    orders_on_session: Sequence[OrderRow],
    max_length: int | None,
    *,
    session: date,
) -> list[OrderRequest]:
    """One `OrderRequest` per order of `phase_orders`, in order, every one
    built and validated before any is returned (spec req 3 (b), (e)).

    `listings_at` maps a `security_id` to its ticker in the master's listing
    at close(S-1) (`None` or absent: none, a `ValueError` before any request is
    built); `prefix` is `paper.order_id_prefix`; `orders_on_session` every
    `orders` row journaled on S = `session`, every decision and phase (a row of
    another session raises); `max_length` is `alpaca.client_order_id_max_length`
    (`None` until T48b records it skips the check: T48c refuses to construct the
    real adapter while it is unset). A sell carrying a notional raises.
    """
    _check_session(session)
    if max_length is not None and (
        isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1
    ):
        raise ValueError(f"max_length is {max_length!r}")
    stray = sorted({str(row.session) for row in orders_on_session if row.session != session})
    if stray:
        raise ValueError(f"orders_on_session holds rows of {stray}, not {session}")
    orders = phase_orders.orders
    unknown = sorted({o.security_id for o in orders if not listings_at.get(o.security_id)})
    if unknown:
        raise ValueError(f"no listing known at close(S-1) for {unknown}")
    for order in orders:
        if order.side == _SELL and order.notional is not None:
            raise ValueError(f"sell of decision {order.decision_id} carries a notional")

    built: Counter[tuple[str, str]] = Counter()
    requests: list[OrderRequest] = []
    for order in orders:
        key = (order.security_id, order.side)
        attempt = next_attempt(orders_on_session, *key) + built[key]
        built[key] += 1
        coid = client_order_id(prefix, session, order.security_id, order.side, attempt)
        if max_length is not None and len(coid) > max_length:
            raise ValueError(f"client order id {coid!r} is longer than {max_length}")
        symbol = listings_at[order.security_id]
        assert symbol is not None
        requests.append(
            OrderRequest(
                client_order_id=coid,
                symbol=symbol,
                side=Side(order.side),
                notional=order.notional,
                quantity=order.quantity,
            )
        )
    return requests

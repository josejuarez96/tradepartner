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
  exit, **less the name's own open (non-terminal) sells already journaled**
  (`open_sells`, same as a trim's cap, below; #647 item 5 owner decision: a
  full exit nets them out exactly like a trim, computed with the same
  `_trim_cap`, and never halts `check_phase`'s `sell_sum_within_holding` on
  its own open sells). The netting is the trim's arithmetic, but the
  under-sell heals differently: a full exit's remainder is its latest
  order's quantity less its fills, so once the netted order fills the
  decision is settled, and if the other open sell then expires unfilled the
  name is still held with no open decision (never a short). A `delisted`
  holding heals on the next run (`exits.py` makes a new forced exit once no
  own sell is open); an `untargeted_receipt` (its settled exit spends the
  receipt) and a plan full exit (`left_targets`, `left_universe`,
  `exclude_name`) heal only at the next rebalance's `plan.decisions_from`;
  a `window_stop` exit is never made beside an open own sell (#647 review).
  A re-attempt after a partial fill sells the holding that is left, so the
  first attempt and every later one follow one rule. Its whole-share basis
  is the decision's journaled `whole_share` flag alone (#395);
- a **trim** sells its remainder (`DecisionState.remainder`, the trim's notional
  left converted at the reference price; a planned quantity there is already
  adjusted by the splits in (T_i, S]), capped at the holding on S less the
  name's residue and less the name's open (non-terminal) sells already
  journaled (`open_sells`, `risk.unfilled_sells`, read before this phase's own
  orders are journaled, so every one is from an earlier session or an earlier
  batch of S; the cap never goes below 0, exact in `Decimal` on the
  `quantity_decimals` grid so it agrees with `check_phase`'s
  `sell_sum_within_holding`, which sums the same numbers the same way) (#518
  owner decision: after a price drop the converted remainder can exceed the
  holding, and the cap sells what is held instead of halting the phase on
  `sell_within_holding`; #605 owner decision: the same cap also subtracts
  those open sells, so the trim passes `sell_sum_within_holding`, which adds
  them back onto the name's sells — an under-trim this leaves heals itself
  next session if the open sell later expires, while a halt does not), by
  whole shares when the decision's flag is set or the name is no longer
  `fractionable`.

Both a full exit and a trim are **held** — neither an order nor a skip, so
the decision stays open and the next run re-attempts it, the same way an
under-minimum buy is `deferred` — exactly when the **capped** quantity (the
holding cap and/or the open-sells subtraction) is a skip but the
**uncapped** one is not (#647 items 5 and 7 owner decisions, 2026-10-03,
superseding #605's narrower open-sells-only hold): for a full exit, "capped"
is the holding-and-residue cap plus the open-sells subtraction, and
"uncapped" is that same cap *without* the open-sells subtraction, so only an
open-sells-caused skip is held, not one the holding cap alone (residue
included) already produces — a full exit that is `dust` even without open
sells still skips `dust`; for a trim, "capped" is the full cap (holding and
open sells together) applied to its remainder, and "uncapped" is the
remainder itself, un-capped by anything — so a trim the holding cap alone
turns into a skip (e.g. a price drop that leaves it already sold down to its
residue) is held too, not just one the open sells alone would have caused.
A trim whose own uncapped remainder is already below the minimum (or below
one whole share) still skips as before. Every hold is reported in
`PhaseOrders.held` (`HeldSell`, both quantities and the skip the capped one
would have been): the wrapper logs one line per hold, and `buy_orders`
counts a held name as sold, like an ordered or skipped one (#719 items 2
and 4).

Every quantity is rounded down to `quantity_decimals` (`alpaca.quantity_decimals`)
and, on a whole-share basis, floored, so no sell exceeds the holding the risk
check rounds the same way. A name not `tradable` skips before its reference
price is read, so a halted name with no price never halts the phase (#518).
The per-name skips of the phase, each a
`risk.Skip` with its `decision_events` reason: `skip_untradable` for a trim or
a plan full exit of a name not `tradable`, `untradable` for a forced exit;
`dust` for a full exit below `risk.min_order_notional` or whose whole-share
floor is zero (a 0.4-share receipt; unless the capping alone caused it, held
instead); `skip_below_one_share` for a whole-share trim that floors to zero
(same exception); `skip_below_minimum` for a trim below the minimum (same
exception). At most one sell per name: two sell decisions for one name,
attempted or in flight, raise `ValueError` (never two sells for one name).
Forced exits sell in the same list; their proceeds reach the buys only as the
cash read after the sells, never as a larger target (`buy_orders`).

**`buy_orders`** sizes the open buys from `cash` (the account's cash after
the sells, less the open-buy reserve, which the caller computes) through
`risk.size_buys`: notional buys first, whole-share buys last. A name whose
listing ended at close(S-1) (`ended`) is skipped as `skip_delisted` before
sizing, so it takes no share of the other buys' scaling, instead of being
sized in and only then dropped by `risk.check_phase` (#604). A buy whose
attempt falls below the minimum is **deferred** (its id in `deferred`, no skip
row, no cap count); a name not `tradable` is `skip_untradable`. A buy of a
name the sells phase ordered or skipped, or whose sell decision is in flight,
raises `ValueError` (#518). The scale is
capped at 1 against the buys' remainders, so cash beyond the targets (a forced
exit's proceeds) is never spent. `cash_left` is the cash minus every order's
modelled cost (`risk._buy_cash`), never negative.

**`requests_for`** turns a phase's orders into `OrderRequest`s, validated
before any is returned: the symbol from the master's listing at close(S-1),
the id from `ids.client_order_id` off the run's session with the attempt
counted from `orders_on_session` (and from this batch), and each id checked
against `max_length` when it is set. Orders built for another session (their
`PhaseOrders.session`) raise (#518).

No numeric literal other than 0, 1, 2 and -1 appears here
(`tests/test_no_literals.py`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

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
from tradepartner.store.schema import DELISTED_REASON, LONG, UNTARGETED_RECEIPT_REASON

__all__ = ["HeldSell", "PhaseOrder", "PhaseOrders", "buy_orders", "requests_for", "sell_orders"]

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
_SKIP_DELISTED = "skip_delisted"


@dataclass(frozen=True)
class PhaseOrder:
    """One order of a phase before the risk check: exactly one of `notional`
    (a buy) and `quantity`. `decision` is the decision's kind; `full_exit` is
    `plan.is_full_exit`; `whole_share` the order's basis; `price` the reference
    price on S; `target_weight` and `position_side` the decision's."""

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
    position_side: str = LONG

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
            position_side=self.position_side,
        )


@dataclass(frozen=True)
class HeldSell:
    """A sell `sell_orders` **held** (module docstring): no order and no skip
    this run, the decision left open. `uncapped_quantity` and
    `capped_quantity` are the two quantities the hold rule compared, and
    `capped_skip` the skip reason the capped one would have been (#719 items
    2 and 4: the wrapper logs one line per hold, and `buy_orders` counts the
    name as sold)."""

    decision_id: int
    security_id: str
    uncapped_quantity: float
    capped_quantity: float
    capped_skip: str


@dataclass(frozen=True)
class PhaseOrders:
    """A phase's orders and per-name skips; for a buys phase also the ids its
    sizing deferred and the cash its orders leave (`None` for a sells phase).
    `session` is the session S the orders were built for (`None` for an empty
    placeholder); `requests_for` refuses any other. `held` is a sells phase's
    held sells (`HeldSell`), empty for a buys phase."""

    orders: tuple[PhaseOrder, ...]
    skips: tuple[Skip, ...]
    deferred: tuple[int, ...] = ()
    cash_left: float | None = None
    session: date | None = None
    held: tuple[HeldSell, ...] = ()


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


def _held_and_residue(
    decision: DecisionRow, ledger: Ledger, residues: Mapping[str, float]
) -> tuple[float, float]:
    """The holding on S and the residue effective for this sell: 0 for a
    `delisted` or `untargeted_receipt` forced exit (`_WHOLE_HOLDING_REASONS`),
    else `plan.residue`. Raises if the residue is above the holding."""
    sid = decision.security_id
    held = _finite(ledger.positions.get(sid, 0.0), f"holding of {sid}")
    if decision.decision == _FORCED_EXIT and decision.reason in _WHOLE_HOLDING_REASONS:
        return held, 0.0
    residue = _finite(residues.get(sid, 0.0), f"residue of {sid}")
    if residue > held:
        raise ValueError(f"residue of {sid} is {residue}, above the holding {held}")
    return held, residue


def _trim_cap(held: float, residue: float, open_sold: Decimal, quantity_decimals: int) -> float:
    """A sell's quantity cap, exact in `Decimal` on the `quantity_decimals`
    grid: the holding rounded down first, less the residue, less the name's
    open sells, never below 0. Rounding the holding down *before*
    subtracting (all in `Decimal`, never `float`) is what lets `check_phase`'s
    `sell_sum_within_holding` — which sums a sell's quantity and its open
    sells the same exact way and compares with the holding rounded down the
    same way — never see a total a rounding ulp over the holding (#605, pass
    1's SHOULD FIX: the original `float` cap could still trip that rule on a
    fractional holding or open sell). Used for a trim's cap and, since #647
    item 5, a full exit's quantity too, with `residue` already 0 for a
    `delisted` or `untargeted_receipt` exit (`_held_and_residue`)."""
    step = Decimal(1).scaleb(-quantity_decimals)
    rounded_holding = Decimal(repr(round_down(held, quantity_decimals)))
    cap = rounded_holding - risk._dec(residue) - open_sold
    return float(max(cap, Decimal(0)).quantize(step, rounding=ROUND_DOWN))


def _floor_if_whole(quantity: float, whole: bool) -> float:
    """`quantity` floored to a whole share when `whole`, else unchanged."""
    return float(math.floor(quantity)) if whole else quantity


def _trim_quantity(
    remainder_quantity: float, cap: float, whole: bool, quantity_decimals: int
) -> float:
    """The trim's final quantity: its remainder, capped, rounded down to
    `quantity_decimals` and, on a whole-share basis, floored."""
    quantity = round_down(min(remainder_quantity, cap), quantity_decimals)
    return _floor_if_whole(quantity, whole)


def _sell_skip(
    full_exit: bool, whole: bool, quantity: float, price: float, frozen: RiskConfig
) -> str | None:
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
    open_sells: Sequence[risk.OpenSell] = (),
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
    quantity precision. `open_sells` is the name's non-terminal own sells
    already journaled (`risk.unfilled_sells`), read before this phase's own
    orders are journaled, so every one of them is from an earlier session or
    an earlier batch of S (#605); both a trim's cap and, since #647 item 5, a
    full exit's quantity subtract them, exact in `Decimal` (never below 0,
    `_trim_cap`).

    When the capped quantity is a skip but the uncapped one is not, the
    decision is **held** instead: no order and no skip for it this run
    (`_sell_skip` on the uncapped quantity is checked too, and only a skip the
    capping alone causes is swallowed), so it stays open and the next run
    re-attempts it (#647 items 5 and 7 owner decisions). For a full exit,
    "uncapped" means the holding-and-residue cap *without* the open-sells
    subtraction, so only an open-sells-caused skip is held; a full exit that
    is `dust` even without open sells still skips `dust`. For a trim,
    "uncapped" means its remainder with no cap at all, so a trim the holding
    cap alone (residue included, before any open sells) turns into a skip is
    held too -- this subsumes #605's narrower open-sells-only hold. A trim
    whose own uncapped remainder is already below the minimum, or below one
    whole share, still skips as before. Raises `ValueError` for a forced exit
    among `decisions`, a non-forced-exit or non-sell among `forced_exits`,
    two sells for one name, and every malformed input `attempt_scope`
    refuses.
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

    open_sold = risk.open_sold(open_sells, quantity_decimals)
    orders: list[PhaseOrder] = []
    skips: list[Skip] = []
    held_sells: list[HeldSell] = []
    for attempt in scope.attempts:
        decision = attempt.decision
        sid = decision.security_id
        asset = _asset(sid, assets)
        if not asset.tradable:
            untradable = _UNTRADABLE if decision.decision == _FORCED_EXIT else _SKIP_UNTRADABLE
            skips.append(Skip(_id(decision), sid, untradable))
            continue
        price = _price(price_of, sid)
        full_exit = is_full_exit(decision)
        held, residue = _held_and_residue(decision, ledger, residues)
        sold = open_sold.get(sid, Decimal(0))
        if full_exit:
            whole = decision.whole_share
            # #647 item 5: the open-sells subtraction is netted out of the
            # full exit's quantity exactly like a trim's cap (`_trim_cap`);
            # "before" is the holding-and-residue cap alone, so a skip only
            # the open sells cause (not the holding cap itself) is held.
            cap_before = _trim_cap(held, residue, Decimal(0), quantity_decimals)
            cap_after = _trim_cap(held, residue, sold, quantity_decimals)
            quantity_before = _floor_if_whole(cap_before, whole)
            quantity = _floor_if_whole(cap_after, whole)
        else:
            remainder_q = _finite(attempt.remainder.quantity, f"remainder of {sid}")
            whole = decision.whole_share or not asset.fractionable
            # #647 item 7: "before" is the trim's remainder with no cap at
            # all (not even the holding cap), so a skip the holding cap
            # and/or the open sells cause, that the uncapped remainder would
            # not have been, is held rather than journaled (subsumes #605's
            # narrower open-sells-only hold).
            cap_after = _trim_cap(held, residue, sold, quantity_decimals)
            quantity_before = _floor_if_whole(round_down(remainder_q, quantity_decimals), whole)
            quantity = _trim_quantity(remainder_q, cap_after, whole, quantity_decimals)
        reason_before = _sell_skip(full_exit, whole, quantity_before, price, frozen)
        reason = _sell_skip(full_exit, whole, quantity, price, frozen)
        if reason is not None and reason_before is None:
            # Held: the capping alone caused it, not a real skip.
            held_sells.append(HeldSell(_id(decision), sid, quantity_before, quantity, reason))
            continue
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
                position_side=decision.position_side,
            )
        )
    return PhaseOrders(tuple(orders), tuple(skips), session=session, held=tuple(held_sells))


def buy_orders(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    cash: float,
    planned_sells: PhaseOrders,
    price_of: PriceOf,
    costs: BuyCosts,
    assets: Mapping[str, ExitAsset],
    frozen: RiskConfig,
    *,
    session: date,
    ended: Collection[str] = (),
) -> PhaseOrders:
    """The buys phase's orders, skips, deferred ids and cash left on S =
    `session` (module docstring).

    `decisions` are the pending rebalance's decisions, both sides (and any
    forced exits), with their `plan.decision_state` on S in `states`; `cash`
    is the account's cash read after the sells less the open-buy reserve, at
    least 0; `planned_sells` is this run's sells phase (`sell_orders`), whose
    proceeds are in `cash` already and whose names, ordered, skipped or held
    (#719 item 4), are never bought (a buy of one raises `ValueError`, as does a buy of a name
    whose sell decision in `decisions` is in flight, and a non-empty
    `planned_sells` built for another session). `ended` is the names whose
    listing ended at close(S-1) (`book.ended`): such a buy is skipped as
    `skip_delisted` before sizing runs, so it takes no share of the other
    buys' scaling (#604), the same way `risk.check_phase` would skip it, just
    earlier. Orders are notional buys in decision-id order,
    then whole-share buys; a buy of a name no longer `fractionable` goes by
    whole shares.
    """
    _check_session(session)
    _finite(cash, "cash")
    if (
        planned_sells.orders or planned_sells.skips or planned_sells.held
    ) and planned_sells.session != session:
        raise ValueError(
            f"planned_sells is a sells phase of {planned_sells.session}, not {session}"
        )
    scope = attempt_scope(decisions, states, phase=_BUY)
    selling = attempt_scope(decisions, states, phase=_SELL)
    by_id = {_id(d): d for d in decisions}
    sold = (
        {order.security_id for order in planned_sells.orders}
        | {skip.security_id for skip in planned_sells.skips}
        | {held.security_id for held in planned_sells.held}
        | {by_id[i].security_id for i in selling.in_flight}
    )
    both = sorted(sold & {a.decision.security_id for a in scope.attempts})
    if both:
        raise ValueError(f"the phase both sells and buys {both}")

    skips: list[Skip] = []
    to_size: list[BuyToSize] = []
    for attempt in scope.attempts:
        decision = attempt.decision
        sid = decision.security_id
        if sid in ended:
            # Matches `risk.check_phase`'s precedence (listing-ended before
            # tradable): skipped before sizing, not after, so no other buy
            # takes a smaller share for this one's sake (#604).
            skips.append(Skip(_id(decision), sid, _SKIP_DELISTED))
            continue
        asset = _asset(sid, assets)
        if not asset.tradable:
            skips.append(Skip(_id(decision), sid, _SKIP_UNTRADABLE))
            continue
        lost = True if not asset.fractionable else None
        to_size.append(BuyToSize(decision, attempt.remainder, whole_share=lost))

    sizing_of = {_id(b.decision): b for b in to_size}
    orders: list[PhaseOrder] = []
    deferred: list[int] = []
    spent = Decimal(0)
    for sizing in size_buys(to_size, cash, price_of, frozen, costs):
        decision = sizing_of[sizing.decision_id].decision
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
                position_side=decision.position_side,
            )
        )
    left = Decimal(repr(float(cash))) - spent
    if left < 0:
        raise ValueError(f"the buys need {spent}, more than the cash {cash}")
    return PhaseOrders(
        tuple(orders), tuple(skips), tuple(sorted(deferred)), float(left), session=session
    )


def requests_for(
    phase_orders: PhaseOrders,
    listings_at: Mapping[str, str | None],
    prefix: str,
    book: str,
    orders_on_session: Sequence[OrderRow],
    max_length: int | None,
    *,
    session: date,
) -> list[OrderRequest]:
    """One `OrderRequest` per order of `phase_orders`, in order, every one
    built and validated before any is returned (spec req 3 (b), (e)).

    `listings_at` maps a `security_id` to its ticker in the master's listing
    at close(S-1) (`None` or absent: none, a `ValueError` before any request is
    built); `prefix` is `paper.order_id_prefix`; `book` is the window's
    `book_id` (`ids.client_order_id`'s token, ADR 0015 seam 1);
    `orders_on_session` every `orders` row journaled on S = `session`, every
    decision and phase (a row of another session raises); `max_length` is
    `alpaca.client_order_id_max_length` (`None` until T48b records it skips the
    check: T48c refuses to construct the real adapter while it is unset). A
    sell carrying a notional raises.
    """
    _check_session(session)
    if phase_orders.session != session:
        raise ValueError(f"the orders were built for {phase_orders.session}, not {session}")
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
        coid = client_order_id(prefix, book, session, order.security_id, order.side, attempt)
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

"""Risk checks and buy sizing for one wrapper phase (Phase 4 spec req 3 (c);
ADR 0010 point 1; plan T54).

Pure functions. Every limit is read from `frozen`, the window's frozen `risk.*`
section, never from live `Settings`; the only run-time input is
`quantity_decimals` (`alpaca.quantity_decimals`, a broker fact), passed in
explicitly. No numeric literal other than 0, 1, 2 and -1 appears here
(`tests/test_no_literals.py`).

`check_phase` takes the phase's candidate orders, already built by the wrapper
(a sell as a quantity or a notional, a whole-share order by whole-share
quantity), and returns either `Violations` (the batch halts before any submit)
or `Skips` (the per-name skips and the orders left to submit). In order:

1. **Phase-time skips**, per name, each with its journal reason (a zero-size
   order is always below the minimum):
   `skip_delisted` (the listing ended at close(S-1); a `forced_exit` is never
   skipped for it), `skip_untradable` (not `tradable`; `untradable` instead for
   a `forced_exit`, exempt from the cap like `dust`), `skip_below_one_share` (a
   whole-share order that rounds to zero; `dust` for a whole-share full exit),
   `skip_below_minimum` (notional below `risk.min_order_notional`; `dust` for a
   full exit). A deferred buy is not a skip: `size_buys` leaves it out.
2. **The skip cap**: skips other than `dust` and `untradable`, plus the run's
   earlier ones (`prior_skips`), above `risk.max_skips_per_run` is a violation of
   kind `skip_cap` (the wrapper raises `SkipCapError`).
3. **The batch limits** on the orders left, each a violation of kind
   `limit_breach` (`LimitBreachError`) naming its rule:
   - `max_position_weight`: a buy's decision target weight (a buy without one,
     or with a non-finite or negative one, fails closed);
   - `max_order_notional_fraction`: each order's notional against equity;
   - `max_gross_exposure`: held value after the batch over equity (a
     non-finite price for any name fails closed);
   - `max_orders_per_run`: this phase's orders plus the run's earlier ones;
   - `sell_within_holding`: each sell at most the reconciled holding rounded
     down to `quantity_decimals` (no short, ever);
   - `sell_sum_within_holding`: a name's sells plus the unfilled quantity of its
     non-terminal own sells from any session at most the holding, rounded down
     as above;
   - `buys_within_cash`: the buys with their modelled cost, a whole-share buy
     at the reference price x (1 + `risk.whole_share_price_buffer`), within
     `account().cash` (never `buying_power`), checked here independently of the
     sizing; within `risk.reconcile_cash_tolerance`, float noise, the check
     passes;
   - `asset_missing` and `whole_shares`: an order whose name the broker's
     `assets` read lacks, or a fractional order on a name that is not
     `fractionable` (the wrapper builds it by whole shares), fail closed.

Equity is the ledger's at `price_of` (the reference price, close(S-1)); a
notional sell is converted to shares at its order's reference price.

`size_buys` sizes the buys phase from cash after the sells (spec req 3):
spendable = `costs.buy_notional_after_costs(cash, ...)`; scale = min(1,
spendable / the buys' remainders), so the cost reserve a first attempt's target
already carries is not deducted twice; a notional buy whose scaled attempt falls
below `risk.min_order_notional`, or a whole-share buy that floors to no share
(or to less than the minimum), is deferred and the others rescaled without it;
each active buy's per-order commission is reserved before scaling; whole-share
buys come last, by floor at the buffered price, while that fits the cash left,
else deferred. Notionals are not rounded to cents here: the wrapper rounds them
down (`round_down`), which only spends less.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_DOWN, Decimal

import polars as pl

from tradepartner.adapters.broker import Account, Asset
from tradepartner.backtest.costs import buy_notional_after_costs, trade_cost
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, Remainder, _split_factor
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    DecisionRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

__all__ = [
    "BuyToSize",
    "OpenSell",
    "PhaseOrder",
    "Sizing",
    "Skip",
    "Skips",
    "Violation",
    "Violations",
    "check_phase",
    "round_down",
    "size_buys",
    "unfilled_sells",
]

_BUY = "buy"
_SELL = "sell"
_FORCED_EXIT = "forced_exit"
_LIMIT_BREACH = "limit_breach"
_SKIP_CAP = "skip_cap"
#: Skips exempt from `risk.max_skips_per_run` (spec req 3).
_CAP_EXEMPT = frozenset({"dust", "untradable"})

PriceOf = Callable[[str], float]


@dataclass(frozen=True)
class PhaseOrder:
    """One candidate order of a phase, as the wrapper built it: exactly one of
    `notional` and `quantity`. `decision` is the decision's kind (`trade`,
    `override`, `forced_exit`); `full_exit` marks a sell of the whole remaining
    holding; `whole_share` is the order's basis (the decision's flag, or the
    name's lost `fractionable`); `price` is the reference price at close(S-1);
    `target_weight` is a buy decision's; `listing_ended` is true when the
    name's listing ended at close(S-1)."""

    decision_id: int
    security_id: str
    symbol: str
    side: str
    decision: str
    price: float
    notional: float | None = None
    quantity: float | None = None
    full_exit: bool = False
    whole_share: bool = False
    target_weight: float | None = None
    listing_ended: bool = False

    def shares(self) -> float:
        """The order in shares (a notional at its reference price)."""
        if self.quantity is not None:
            return self.quantity
        assert self.notional is not None
        return self.notional / self.price

    def value(self) -> float:
        """The order in dollars at its reference price."""
        if self.notional is not None:
            return self.notional
        assert self.quantity is not None
        return self.quantity * self.price


@dataclass(frozen=True)
class OpenSell:
    """The unfilled quantity of one non-terminal own sell, any session."""

    security_id: str
    unfilled_quantity: float


@dataclass(frozen=True)
class Violation:
    """A batch rule broken: `kind` is `limit_breach` or `skip_cap`, `rule` the
    `risk.*` key or structural rule's name."""

    kind: str
    rule: str
    detail: str


@dataclass(frozen=True)
class Skip:
    """A phase-time skip: the decision's `decision_events` reason."""

    decision_id: int
    security_id: str
    reason: str

    @property
    def counts_toward_cap(self) -> bool:
        """False for `dust` and `untradable` (spec req 3)."""
        return self.reason not in _CAP_EXEMPT


@dataclass(frozen=True)
class Violations:
    """The batch halts: every rule it breaks."""

    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class Skips:
    """The batch passes: its skips and the orders left to submit, in input order,
    each with `whole_share` set when its name is not `fractionable`."""

    skips: tuple[Skip, ...]
    orders: tuple[PhaseOrder, ...]


@dataclass(frozen=True)
class BuyToSize:
    """An open buy decision and its remainder (`plan.remainder`). `whole_share`
    defaults to the decision's flag; the wrapper sets it when the name has lost
    `fractionable` since."""

    decision: DecisionRow
    remainder: Remainder
    whole_share: bool | None = field(default=None)

    @property
    def by_whole_shares(self) -> bool:
        """Whether this buy is sized by whole-share quantity."""
        return self.decision.whole_share if self.whole_share is None else self.whole_share


@dataclass(frozen=True)
class Sizing:
    """One buy's attempt: a notional or a whole-share quantity, or deferred
    (neither set)."""

    decision_id: int
    security_id: str
    notional: float | None = None
    quantity: float | None = None

    @property
    def deferred(self) -> bool:
        """True when the buy gets no order this phase."""
        return self.notional is None and self.quantity is None


def _finite(value: float, what: str, *, positive: bool = False) -> float:
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        bound = "positive" if positive else "non-negative"
        raise ValueError(f"{what} is {value}, must be a finite {bound} number")
    return value


def round_down(quantity: float, decimals: int) -> float:
    """`quantity` rounded toward zero to `decimals` places, from its shortest
    decimal form (so 0.57 stays 0.57): never up."""
    if decimals < 0:
        raise ValueError(f"decimals must be non-negative, got {decimals}")
    _finite(quantity, "quantity")
    step = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(quantity)).quantize(step, rounding=ROUND_DOWN))


def unfilled_sells(
    orders: Iterable[OrderRow],
    order_events: Iterable[OrderEventRow],
    fills: Iterable[OrderedFill],
    price_of: PriceOf,
    actions_as_of: pl.DataFrame,
    *,
    session: date,
) -> list[OpenSell]:
    """The unfilled part of every non-terminal own sell in `orders`, in shares
    on session `session`'s basis, as `plan.remainder` measures it: a quantity
    sell's submitted minus filled quantity, adjusted by the splits in
    `actions_as_of` with ex-date in (the order's session, S]; a notional sell's
    submitted notional minus its filled value, at the reference price. Never
    below zero. An order with no event counts as open."""
    terminal = {e.client_order_id for e in order_events if e.status in TERMINAL_ORDER_STATUSES}
    filled_quantity: dict[str, float] = defaultdict(float)
    filled_value: dict[str, float] = defaultdict(float)
    for fill in fills:
        row = fill.fill
        filled_quantity[row.client_order_id] += row.quantity
        filled_value[row.client_order_id] += row.quantity * row.price
    result = []
    for order in orders:
        if order.side != _SELL or order.client_order_id in terminal:
            continue
        coid = order.client_order_id
        if order.quantity is not None:
            factor = _split_factor(actions_as_of, order.security_id, order.session, session)
            left = (order.quantity - filled_quantity[coid]) * factor
        elif order.notional is not None:
            price = _finite(price_of(order.security_id), "price", positive=True)
            left = (order.notional - filled_value[coid]) / price
        else:
            raise ValueError(f"order {coid!r} has neither quantity nor notional")
        if left > 0:
            result.append(OpenSell(order.security_id, left))
    return result


def _skip_reason(
    order: PhaseOrder, asset: Asset, frozen: RiskConfig, whole_share: bool
) -> str | None:
    forced = order.decision == _FORCED_EXIT
    if not asset.tradable:
        return "untradable" if forced else "skip_untradable"
    if whole_share and order.quantity is not None and order.quantity < 1:
        return "dust" if order.full_exit else "skip_below_one_share"
    if order.value() <= 0 or order.value() < frozen.min_order_notional:
        return "dust" if order.full_exit else "skip_below_minimum"
    return None


def check_phase(
    orders: Sequence[PhaseOrder],
    ledger: Ledger,
    account: Account,
    assets: Mapping[str, Asset],
    frozen: RiskConfig,
    quantity_decimals: int,
    *,
    price_of: PriceOf,
    costs: BuyCosts,
    open_sells: Sequence[OpenSell] = (),
    prior_orders: int = 0,
    prior_skips: int = 0,
) -> Violations | Skips:
    """Check one phase's batch (module docstring). `assets` is keyed by
    `security_id`; `open_sells` are the non-terminal own sells from any session
    (`unfilled_sells`); `prior_orders` and `prior_skips` are this run's earlier
    phases' counts."""
    for order in orders:
        if order.side not in (_BUY, _SELL):
            raise ValueError(f"order of decision {order.decision_id} has side {order.side!r}")
        if (order.notional is None) == (order.quantity is None):
            raise ValueError(f"order of decision {order.decision_id} needs exactly one size")
        _finite(order.price, f"price of {order.security_id}", positive=True)
        _finite(order.value(), f"size of decision {order.decision_id}")
        if order.side == _BUY and (order.full_exit or order.decision == _FORCED_EXIT):
            raise ValueError(f"buy of decision {order.decision_id} marked as an exit")

    violations: list[Violation] = []

    def breach(rule: str, detail: str, kind: str = _LIMIT_BREACH) -> None:
        violations.append(Violation(kind, rule, detail))

    skips: list[Skip] = []
    left: list[PhaseOrder] = []
    for order in orders:
        if order.listing_ended and order.decision != _FORCED_EXIT:
            skips.append(Skip(order.decision_id, order.security_id, "skip_delisted"))
            continue
        asset = assets.get(order.security_id)
        if asset is None:
            breach("asset_missing", f"no asset read for {order.security_id}")
            continue
        whole = order.whole_share or not asset.fractionable
        reason = _skip_reason(order, asset, frozen, whole)
        if reason is not None:
            skips.append(Skip(order.decision_id, order.security_id, reason))
            continue
        if whole and (order.quantity is None or order.quantity != math.floor(order.quantity)):
            breach("whole_shares", f"{order.security_id} must be ordered by whole shares")
            continue
        if whole != order.whole_share:
            order = replace(order, whole_share=True)
        left.append(order)

    counted = prior_skips + sum(s.counts_toward_cap for s in skips)
    if counted > frozen.max_skips_per_run:
        breach(
            "max_skips_per_run",
            f"{counted} skips counted against the cap of {frozen.max_skips_per_run}",
            _SKIP_CAP,
        )

    marks = {name: price_of(name) for name in ledger.positions}
    equity = ledger.equity(marks)
    if not equity > 0:
        breach("max_order_notional_fraction", f"equity is {equity}, not positive")
        return Violations(tuple(violations))

    if prior_orders + len(left) > frozen.max_orders_per_run:
        breach(
            "max_orders_per_run",
            f"{prior_orders + len(left)} orders against the cap of {frozen.max_orders_per_run}",
        )

    after: dict[str, float] = defaultdict(float, ledger.positions)
    sold: dict[str, float] = defaultdict(float)
    buy_cash = 0.0
    for order in left:
        if order.value() > frozen.max_order_notional_fraction * equity:
            breach(
                "max_order_notional_fraction",
                f"{order.security_id} order of {order.value():.2f} over "
                f"{frozen.max_order_notional_fraction} of equity {equity:.2f}",
            )
        if order.side == _BUY:
            weight = order.target_weight
            if (
                weight is None
                or not math.isfinite(weight)
                or weight < 0
                or weight > frozen.max_position_weight
            ):
                breach(
                    "max_position_weight",
                    f"{order.security_id} target weight {weight} over {frozen.max_position_weight}",
                )
            after[order.security_id] += order.shares()
            if order.whole_share:
                assert order.quantity is not None
                price = order.price * (1 + frozen.whole_share_price_buffer)
                notional, shares = order.quantity * price, order.quantity
            else:
                notional, shares = order.value(), order.shares()
            buy_cash += notional + trade_cost(
                notional, shares, costs.per_side_bps, costs.commissions
            )
        else:
            holding = ledger.positions.get(order.security_id, 0.0)
            allowed = round_down(max(holding, 0.0), quantity_decimals)
            if order.shares() > allowed:
                breach(
                    "sell_within_holding",
                    f"{order.security_id} sell of {order.shares()} over the holding {allowed}",
                )
            sold[order.security_id] += order.shares()
            after[order.security_id] -= order.shares()

    for sell in open_sells:
        if sell.security_id in sold:
            sold[sell.security_id] += sell.unfilled_quantity
    for name, total in sold.items():
        holding = round_down(max(ledger.positions.get(name, 0.0), 0.0), quantity_decimals)
        if total > holding:
            breach(
                "sell_sum_within_holding",
                f"{name} sells and open sells of {total} over the holding {holding}",
            )

    exposure = (
        sum(
            max(q, 0.0) * _finite(price_of(name), f"price of {name}", positive=True)
            for name, q in after.items()
        )
        / equity
    )
    if not math.isfinite(exposure) or exposure > frozen.max_gross_exposure:
        breach(
            "max_gross_exposure",
            f"gross exposure after the batch {exposure:.4f} over {frozen.max_gross_exposure}",
        )

    if buy_cash > account.cash + frozen.reconcile_cash_tolerance:
        breach("buys_within_cash", f"buys need {buy_cash:.2f}, cash is {account.cash:.2f}")

    if violations:
        return Violations(tuple(violations))
    return Skips(tuple(skips), tuple(left))


def size_buys(
    decisions: Sequence[BuyToSize],
    cash: float,
    price_of: PriceOf,
    frozen: RiskConfig,
    costs: BuyCosts,
) -> list[Sizing]:
    """Size the buys phase from `cash` (the account's, after the sells; module
    docstring). Returns one `Sizing` per buy: notional buys first in input
    order, then whole-share buys in input order."""
    _finite(cash, "cash")
    if not decisions:
        return []
    for buy in decisions:
        if buy.decision.side != _BUY or buy.decision.decision_id is None:
            raise ValueError(f"decision {buy.decision.decision_id} is not a buy with an id")
        _finite(buy.remainder.notional, f"remainder of decision {buy.decision.decision_id}")
    prices = {
        b.decision.security_id: _finite(price_of(b.decision.security_id), "price", positive=True)
        for b in decisions
    }
    buffer = 1 + frozen.whole_share_price_buffer

    def whole_shares(buy: BuyToSize, scale: float) -> float:
        buffered = prices[buy.decision.security_id] * buffer
        return float(math.floor(buy.remainder.notional * scale / buffered))

    def below_minimum(buy: BuyToSize, scale: float) -> bool:
        if not buy.by_whole_shares:
            attempt = buy.remainder.notional * scale
            return attempt <= 0 or attempt < frozen.min_order_notional
        quantity = whole_shares(buy, scale)
        value = quantity * prices[buy.decision.security_id]
        return quantity < 1 or value < frozen.min_order_notional

    active = [b for b in decisions if b.remainder.notional > 0]
    deferred: set[int] = {id(b) for b in decisions if b.remainder.notional <= 0}
    while True:
        # Each buy pays its own per-order commission, and the lowest price buys
        # the most shares (the worst case for a per-share one); Alpaca charges
        # neither.
        reserved = max(len(active) - 1, 0) * costs.commissions.per_order
        spendable = buy_notional_after_costs(
            max(cash - reserved, 0.0),
            costs.per_side_bps,
            costs.commissions,
            price=min((prices[b.decision.security_id] for b in active), default=1.0),
        )
        total = sum(b.remainder.notional for b in active)
        scale = min(1.0, spendable / total) if total > 0 else 0.0
        low = [b for b in active if below_minimum(b, scale)]
        if not low:
            break
        deferred.update(id(b) for b in low)
        active = [b for b in active if id(b) not in deferred]

    notional_sizings: list[Sizing] = []
    cash_left = cash
    for buy in decisions:
        if buy.by_whole_shares:
            continue
        if id(buy) in deferred:
            notional_sizings.append(_sizing(buy))
            continue
        notional = buy.remainder.notional * scale
        shares = notional / prices[buy.decision.security_id]
        cash_left -= notional + trade_cost(notional, shares, costs.per_side_bps, costs.commissions)
        notional_sizings.append(_sizing(buy, notional=notional))

    whole_sizings: list[Sizing] = []
    for buy in decisions:
        if not buy.by_whole_shares:
            continue
        price = prices[buy.decision.security_id] * buffer
        quantity = 0.0 if id(buy) in deferred else whole_shares(buy, scale)
        cost = trade_cost(quantity * price, quantity, costs.per_side_bps, costs.commissions)
        if quantity >= 1 and quantity * price + cost <= cash_left:
            cash_left -= quantity * price + cost
            whole_sizings.append(_sizing(buy, quantity=quantity))
        else:
            whole_sizings.append(_sizing(buy))
    return notional_sizings + whole_sizings


def _sizing(
    buy: BuyToSize, *, notional: float | None = None, quantity: float | None = None
) -> Sizing:
    assert buy.decision.decision_id is not None
    return Sizing(buy.decision.decision_id, buy.decision.security_id, notional, quantity)

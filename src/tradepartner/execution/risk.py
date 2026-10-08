"""Risk checks and buy sizing for one wrapper phase (Phase 4 spec req 3 (c);
ADR 0010 point 1 and its amendment of 2026-09-30; plans T54 and T54c).

Pure functions. Every limit is read from `frozen`, the window's frozen `risk.*`
section, never from live `Settings`; the only run-time input is
`quantity_decimals` (`alpaca.quantity_decimals`, a broker fact), passed in
explicitly. No numeric literal other than 0, 1, 2 and -1 appears here
(`tests/test_no_literals.py`).

`check_phase` takes the phase's candidate orders, already built by the wrapper
(every sell by quantity, a whole-share order by whole-share quantity, and
`full_exit` set from `plan.is_full_exit`), and returns either `Violations`
(the batch halts before any submit) or `Skips` (the per-name skips and the
orders left to submit). In order:

1. **Phase-time skips**, per name (a sell carrying a notional is refused first,
   `sell_by_quantity`, before any skip), each with its journal reason (a zero-size
   order is always below the minimum):
   `skip_delisted` (the listing ended at close(S-1); a `forced_exit` is never
   skipped for it), `skip_untradable` (not `tradable`; `untradable` instead for
   a `forced_exit`, exempt from the cap like `dust`), `skip_below_one_share` (a
   whole-share order that rounds to zero; `dust` for a whole-share full exit,
   so a full exit of one share is still sold),
   `skip_below_minimum` (notional below `risk.min_order_notional`; `dust` for a
   full exit). A deferred buy is not a skip: `size_buys` leaves it out.
2. **The skip cap**: skips other than `dust` and `untradable`, plus the run's
   earlier ones (`prior_skips`), above `risk.max_skips_per_run` is a violation of
   kind `skip_cap` (the wrapper raises `SkipCapError`).
3. **The batch limits** on the orders left, each a violation of kind
   `limit_breach` (`LimitBreachError`) naming its rule:
   - `max_position_weight`: a buy's decision target weight (a buy without one,
     or with a non-finite or negative one, fails closed), and every bought
     name's held value after the phase (its ledger quantity at the reference
     price plus the buy, a whole-share buy at the buffered price) over equity;
     names the phase does not buy are not checked, so drift alone never halts;
   - `one_order_per_name_side`: at most one order per (name, side);
   - `max_order_notional_fraction`: each buy's and trim's notional against
     equity; a full exit (every forced exit included) is exempt;
   - `max_gross_exposure`: held value after the batch over equity (a
     non-finite price for any name fails closed);
   - `max_orders_per_run`: this phase's orders plus the run's earlier ones;
   - `sell_within_holding`: each sell at most the reconciled holding rounded
     down to `quantity_decimals` (no short, ever);
   - `sell_sum_within_holding`: a name's sells plus the unfilled quantity of its
     non-terminal own sells from any session (`open_sold`: each put on the
     `quantity_decimals` grid, the nearest step only within `float` noise of
     it and otherwise **up**, summed exactly in `Decimal`) at most the
     holding, rounded down as above;
   - `buys_within_cash`: the buys with their modelled cost, a whole-share buy
     at the reference price x (1 + `risk.whole_share_price_buffer`), within
     `account().cash` (never `buying_power`), strictly and in `Decimal`
     (`_buy_cash`, which `size_buys` uses too); a buy notional not in whole
     cents is refused (`ValueError`: the wrapper floors to the cent first);
   - `asset_missing` and `whole_shares`: an order whose name the broker's
     `assets` read lacks, or a fractional order by whole shares, fail closed. A
     buy or trim of a name no longer `fractionable` goes by whole shares (the
     wrapper floors it); a full exit goes by its decision's `whole_share` flag
     alone, so a name that lost `fractionable` is still sold whole (#395).
   - `refused_position_side`: an order of the phase, or a row of
     `rows_on_session` (the session's `decisions` and `orders` rows the
     wrapper read), whose `position_side` is not `schema.LONG`, one violation
     per such order or row. Structural, like the two above: no short, ever,
     until the shorting ADR (ADR 0015 seam 2, amending ADR 0010 point 1).
   - `refused_order_shape`: an `orders` row of `rows_on_session` that
     `order_shape_violation` refuses, one violation per such row. Structural,
     like `refused_position_side` (ADR 0015 seam 3).

`order_shape_violation` is the one order-shape predicate (ADR 0015 seam 3,
plan T135b): an `Order` the broker returned, or an `orders` row, whose
(`order_type`, `time_in_force`, `asset_class`, `order_class`, `multiplier`) is
not `schema.ORDER_SHAPE_DEFAULTS`' (`market`, `day`, `us_equity`, `simple`,
`1`), or whose `limit_price`, `stop_price` or `parent_order_id` is set, or whose
`legs` is non-empty, is a `limit_breach` `Violation` named
`refused_order_shape`. `multiplier` and `parent_order_id` are read on rows
only and `legs` on `Order`s only: neither type has the other's fields. The
wrapper applies it to every `Order` from `submit` and `get_order` after
journaling it (a post-submit fault), and `reconcile.compare` to every own open
order.

Equity is the ledger's at `price_of` (the reference price, close(S-1)).

`size_buys` sizes the buys phase from cash after the sells (spec req 3), in
`Decimal`: spendable is the cash less one per-order commission per active buy,
over 1 + the per-side rate + the per-share commission at the lowest price;
scale = min(1, spendable / the buys' remainders), so the cost reserve a first
attempt's target already carries is not deducted twice; notionals are floored
to the cent and, should `Decimal` rounding leave the floored batch needing more
than the cash under `_buy_cash`, the overshoot rounded up to the cent comes off
the largest notional once (a `ValueError` if that takes it below the minimum),
so every sized batch passes `check_phase`'s cash rule. A notional buy whose
scaled attempt falls below `risk.min_order_notional`, or a whole-share buy that
floors to no share (or to less than the minimum), is deferred and the others
rescaled without it. Buys that cannot meet the minimum even at full scale are
set aside first; then one below-minimum buy is deferred per pass, smallest
remainder first, with ties by `security_id` ascending. Whole-share buys come
last, by floor at the buffered price, while that fits the
cash left, else deferred.
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, Decimal

import polars as pl

from tradepartner.adapters.broker import Account, Asset, Order
from tradepartner.backtest.costs import BPS_PER_UNIT
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import _SPLIT as _SPLIT_ACTION
from tradepartner.execution.plan import BuyCosts, Remainder, _split_factor
from tradepartner.store import schema
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
    "open_sold",
    "order_shape_violation",
    "round_down",
    "size_buys",
    "unfilled_sells",
]

_BUY = "buy"
_SELL = "sell"
_FORCED_EXIT = "forced_exit"
_LIMIT_BREACH = "limit_breach"
_SKIP_CAP = "skip_cap"
#: The structural rule refusing any position side but long (ADR 0015 seam 2).
_REFUSED_POSITION_SIDE = "refused_position_side"
#: The structural rule refusing any order but a market day order of one share
#: class (ADR 0015 seam 3).
_REFUSED_ORDER_SHAPE = "refused_order_shape"
#: Cash is compared in cents (spec req 3 (c)).
_CENT = Decimal(1).scaleb(-2)
#: Skips exempt from `risk.max_skips_per_run` (spec req 3).
_CAP_EXEMPT = frozenset({"dust", "untradable"})

PriceOf = Callable[[str], float]


@dataclass(frozen=True)
class PhaseOrder:
    """One candidate order of a phase, as the wrapper built it: exactly one of
    `notional` and `quantity`. `decision` is the decision's kind (`trade`,
    `override`, `forced_exit`); `full_exit` marks a sell of the whole remaining
    holding; `whole_share` is the order's basis (the decision's flag, or, for
    a buy or trim, the name's lost `fractionable`); `price` is the reference price at close(S-1);
    `target_weight` is a buy decision's; `listing_ended` is true when the
    name's listing ended at close(S-1); `position_side` is the decision's
    (`check_phase` refuses anything but `schema.LONG`)."""

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
    position_side: str = schema.LONG

    def shares(self) -> float:
        """The order in shares (a notional buy at its reference price)."""
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
    """The unfilled quantity of one non-terminal own sell, any session.
    `split_ratios` is how many `float` split ratios `unfilled_sells`
    multiplied into it: each one widens the `float` noise `open_sold` snaps
    away (#719 item 1 /code-review: chained splits)."""

    security_id: str
    unfilled_quantity: float
    split_ratios: int = 0


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
    each buy and trim with `whole_share` set when its name is not
    `fractionable`."""

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


def open_sold(open_sells: Iterable[OpenSell], quantity_decimals: int) -> dict[str, Decimal]:
    """Each name's open sells (`unfilled_sells`), summed exactly in `Decimal`
    by `security_id`, every unfilled quantity first put on the
    `quantity_decimals` grid (`_on_grid`): the nearest step when it is within
    `float` noise of one (a fully filled sell with no terminal event yet can
    leave about 1e-16, which must not count), otherwise the step **above**,
    so an off-grid estimate (a split factor such as 1/3, a notional sell's
    unfilled value over the reference price) is never under-counted (#719
    item 1: the nearest step could miss up to half a step, letting a trim
    plus its open sells exceed the holding by that much). `check_phase`'s
    `sell_sum_within_holding` and `phases.sell_orders`' sell cap both read
    this one sum (#605)."""
    if quantity_decimals < 0:
        raise ValueError(f"decimals must be non-negative, got {quantity_decimals}")
    step = Decimal(1).scaleb(-quantity_decimals)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for sell in open_sells:
        left = _dec(_finite(sell.unfilled_quantity, f"open sell of {sell.security_id}"))
        if sell.split_ratios < 0:
            raise ValueError(f"open sell of {sell.security_id} has {sell.split_ratios} splits")
        totals[sell.security_id] += _on_grid(left, step, sell.split_ratios)
    return totals


#: The relative rounding error of one `float` operation (machine epsilon).
_FLOAT_EPSILON = Decimal(sys.float_info.epsilon)


def _on_grid(left: Decimal, step: Decimal, split_ratios: int) -> Decimal:
    """`left` (non-negative) on the grid of `step`: the nearest step when the
    distance to it is at most `float` noise, else rounded **up**. The noise
    bound is machine epsilon times the larger of `left` and one share, times
    `split_ratios` + 2: `unfilled_sells` subtracts exactly, so what is left
    is each split ratio's own rounding and their `float` product (under one
    epsilon each), plus storing the result as a `float` and reading it back
    through `repr` (under two together)."""
    nearest = left.quantize(step, rounding=ROUND_HALF_EVEN)
    noise = _FLOAT_EPSILON * max(left, Decimal(1)) * (split_ratios + 2)
    if abs(left - nearest) <= noise:
        return nearest
    return left.quantize(step, rounding=ROUND_UP)


def round_down(quantity: float, decimals: int) -> float:
    """`quantity` rounded toward zero to `decimals` places, from its shortest
    decimal form (so 0.57 stays 0.57): never up."""
    if decimals < 0:
        raise ValueError(f"decimals must be non-negative, got {decimals}")
    _finite(quantity, "quantity")
    step = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(quantity)).quantize(step, rounding=ROUND_DOWN))


def _dec(value: float) -> Decimal:
    return Decimal(repr(value))


def _buy_cash(
    notional: float | None,
    quantity: float | None,
    price: float,
    whole_share: bool,
    frozen: RiskConfig,
    costs: BuyCosts,
) -> Decimal:
    """What one buy takes from cash, in `Decimal`: a notional (floored to the
    cent; `check_phase` refuses any other), or a whole-share quantity at the
    buffered reference price, plus its modelled cost (`backtest.costs.
    trade_cost`'s formula; nothing for a buy of nothing). `check_phase` and
    `size_buys` both use it."""
    if whole_share:
        assert quantity is not None
        shares = _dec(quantity)
        amount = shares * _dec(price) * (1 + _dec(frozen.whole_share_price_buffer))
    else:
        assert notional is not None
        amount = _dec(notional).quantize(_CENT, rounding=ROUND_DOWN)
        shares = amount / _dec(price)
    if amount == 0 and shares == 0:
        return Decimal(0)
    return (
        amount
        + amount * _dec(costs.per_side_bps) / BPS_PER_UNIT
        + shares * _dec(costs.commissions.per_share)
        + _dec(costs.commissions.per_order)
    )


def _spendable(cash: Decimal, buys: int, low_price: float, costs: BuyCosts) -> Decimal:
    """The largest notional `buys` buys can share out of `cash`: cash less one
    per-order commission per buy, over 1 + the per-side rate + the per-share
    commission at the lowest price (the most shares per dollar).
    `costs.buy_notional_after_costs`'s formula in `Decimal` (a test pins the
    two equal); never below zero."""
    available = cash - buys * _dec(costs.commissions.per_order)
    if available <= 0:
        return Decimal(0)
    rate = (
        1
        + _dec(costs.per_side_bps) / BPS_PER_UNIT
        + _dec(costs.commissions.per_share) / _dec(low_price)
    )
    return available / rate


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
    below zero. An order with no event counts as open. The subtraction is
    exact in `Decimal` (#719 item 1), so a fully filled sell with no terminal
    event yet leaves nothing and a partly filled one leaves its grid quantity;
    only a split factor or a notional over the price can leave it off-grid."""
    terminal = {e.client_order_id for e in order_events if e.status in TERMINAL_ORDER_STATUSES}
    filled_quantity: dict[str, Decimal] = defaultdict(Decimal)
    filled_value: dict[str, Decimal] = defaultdict(Decimal)
    for fill in fills:
        row = fill.fill
        filled_quantity[row.client_order_id] += _dec(row.quantity)
        filled_value[row.client_order_id] += _dec(row.quantity) * _dec(row.price)
    result = []
    for order in orders:
        if order.side != _SELL or order.client_order_id in terminal:
            continue
        coid = order.client_order_id
        ratios = 0
        if order.quantity is not None:
            factor = _split_factor(actions_as_of, order.security_id, order.session, session)
            ratios = _split_count(actions_as_of, order.security_id, order.session, session)
            left = (_dec(order.quantity) - filled_quantity[coid]) * _dec(factor)
        elif order.notional is not None:
            price = _finite(price_of(order.security_id), "price", positive=True)
            left = (_dec(order.notional) - filled_value[coid]) / _dec(price)
        else:
            raise ValueError(f"order {coid!r} has neither quantity nor notional")
        if left > 0:
            result.append(OpenSell(order.security_id, float(left), ratios))
    return result


def _split_count(
    actions_as_of: pl.DataFrame, security_id: str, stated_on: date, session: date
) -> int:
    """How many split ratios `plan._split_factor` multiplies for the same
    arguments: the name's `split` rows with ex-date in (`stated_on`,
    `session`]."""
    return sum(
        1
        for row in actions_as_of.iter_rows(named=True)
        if row["security_id"] == security_id
        and row["action_type"] == _SPLIT_ACTION
        and stated_on < row["ex_date"] <= session
    )


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


def order_shape_violation(order_or_row: Order | OrderRow) -> Violation | None:
    """`refused_order_shape` when `order_or_row` is not a market, day,
    `us_equity`, `simple`, multiplier-1 order with no limit price, stop price,
    parent or legs (module docstring); `None` when it is. Every offending field
    is named in the detail."""
    shape = schema.ORDER_SHAPE_DEFAULTS
    offending = [
        f"{name} {value!r}"
        for name, value, default in (
            ("order_type", order_or_row.order_type, shape["order_type"]),
            ("time_in_force", order_or_row.time_in_force, shape["time_in_force"]),
            ("asset_class", order_or_row.asset_class, shape["asset_class"]),
            ("order_class", order_or_row.order_class, shape["order_class"]),
        )
        if value != default
    ]
    offending += [
        f"{name} {value!r}"
        for name, value in (
            ("limit_price", order_or_row.limit_price),
            ("stop_price", order_or_row.stop_price),
        )
        if value is not None
    ]
    if isinstance(order_or_row, OrderRow):
        # `!=`, never `>`: a NaN multiplier is refused too.
        if order_or_row.multiplier != shape["multiplier"]:
            offending.append(f"multiplier {order_or_row.multiplier!r}")
        if order_or_row.parent_order_id is not None:
            offending.append(f"parent_order_id {order_or_row.parent_order_id!r}")
    elif order_or_row.legs:
        offending.append(f"{len(order_or_row.legs)} legs")
    if not offending:
        return None
    return Violation(
        _LIMIT_BREACH,
        _REFUSED_ORDER_SHAPE,
        f"order {order_or_row.client_order_id} for {order_or_row.symbol} has "
        + ", ".join(offending),
    )


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
    rows_on_session: Sequence[DecisionRow | OrderRow] = (),
) -> Violations | Skips:
    """Check one phase's batch (module docstring). `assets` is keyed by
    `security_id`; `open_sells` are the non-terminal own sells from any session
    (`unfilled_sells`); `prior_orders` and `prior_skips` are this run's earlier
    phases' counts; `rows_on_session` are the session's `decisions` and
    `orders` rows, read only for `refused_position_side` and (its `orders`
    rows) `refused_order_shape`."""
    for order in orders:
        if order.side not in (_BUY, _SELL):
            raise ValueError(f"order of decision {order.decision_id} has side {order.side!r}")
        if (order.notional is None) == (order.quantity is None):
            raise ValueError(f"order of decision {order.decision_id} needs exactly one size")
        _finite(order.price, f"price of {order.security_id}", positive=True)
        _finite(order.value(), f"size of decision {order.decision_id}")
        if order.side == _BUY and (order.full_exit or order.decision == _FORCED_EXIT):
            raise ValueError(f"buy of decision {order.decision_id} marked as an exit")
        if order.side == _BUY and order.quantity is not None and not order.whole_share:
            raise ValueError(f"buy of decision {order.decision_id} by quantity is not whole-share")
        if (
            order.side == _BUY
            and order.notional is not None
            and _dec(order.notional) != _dec(order.notional).quantize(_CENT)
        ):
            raise ValueError(f"buy of decision {order.decision_id} is not in whole cents")

    violations: list[Violation] = []

    def breach(rule: str, detail: str, kind: str = _LIMIT_BREACH) -> None:
        violations.append(Violation(kind, rule, detail))

    for order in orders:
        if order.position_side != schema.LONG:
            breach(
                _REFUSED_POSITION_SIDE,
                f"order of decision {order.decision_id} for {order.security_id} "
                f"has position_side {order.position_side!r}",
            )
    for row in rows_on_session:
        if row.position_side != schema.LONG:
            what = (
                f"order {row.client_order_id}"
                if isinstance(row, OrderRow)
                else f"decision {row.decision_id}"
            )
            breach(
                _REFUSED_POSITION_SIDE,
                f"{what} for {row.security_id} has position_side {row.position_side!r}",
            )
        if isinstance(row, OrderRow):
            shape = order_shape_violation(row)
            if shape is not None:
                violations.append(shape)

    skips: list[Skip] = []
    left: list[PhaseOrder] = []
    for order in orders:
        if order.side == _SELL and order.quantity is None:
            breach("sell_by_quantity", f"{order.security_id} sell carries a notional")
            continue
        if order.listing_ended and order.decision != _FORCED_EXIT:
            skips.append(Skip(order.decision_id, order.security_id, "skip_delisted"))
            continue
        asset = assets.get(order.security_id)
        if asset is None:
            breach("asset_missing", f"no asset read for {order.security_id}")
            continue
        # A full exit keeps its decision's basis and sells the whole holding (#395).
        whole = order.whole_share or (not asset.fractionable and not order.full_exit)
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

    per_side: dict[tuple[str, str], int] = defaultdict(int)
    for order in left:
        per_side[order.security_id, order.side] += 1
    for (name, side), count in per_side.items():
        if count > 1:
            breach("one_order_per_name_side", f"{count} {side} orders for {name}")

    after: dict[str, float] = defaultdict(float, ledger.positions)
    sold: dict[str, float] = defaultdict(float)
    bought: dict[str, tuple[float, float]] = {}
    over_weight: dict[str, str] = {}
    buy_cash = Decimal(0)
    for order in left:
        if not order.full_exit and order.value() > frozen.max_order_notional_fraction * equity:
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
                over_weight.setdefault(
                    order.security_id,
                    f"{order.security_id} target weight {weight} over {frozen.max_position_weight}",
                )
            after[order.security_id] += order.shares()
            buffer = 1 + frozen.whole_share_price_buffer if order.whole_share else 1
            _, value = bought.get(order.security_id, (order.price, 0.0))
            bought[order.security_id] = (order.price, value + order.value() * buffer)
            buy_cash += _buy_cash(
                order.notional, order.quantity, order.price, order.whole_share, frozen, costs
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

    for name, (price, value) in bought.items():
        held = max(ledger.positions.get(name, 0.0), 0.0) * price + value
        if held > frozen.max_position_weight * equity:
            over_weight.setdefault(
                name,
                f"{name} held {held:.2f} after the phase over "
                f"{frozen.max_position_weight} of equity {equity:.2f}",
            )
    for detail in over_weight.values():
        breach("max_position_weight", detail)

    # Exact in `Decimal`, never `float`, with the open sells summed by the one
    # helper `phases.sell_orders` caps a trim with (`open_sold`), so a trim
    # capped there never breaches here on float drift alone (#605).
    open_by_name = open_sold(open_sells, quantity_decimals)
    for name, total in sold.items():
        holding = round_down(max(ledger.positions.get(name, 0.0), 0.0), quantity_decimals)
        total_exact = _dec(total) + open_by_name.get(name, Decimal(0))
        if total_exact > _dec(holding):
            breach(
                "sell_sum_within_holding",
                f"{name} sells and open sells of {total_exact} over the holding {holding}",
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

    if buy_cash > _dec(account.cash):
        breach("buys_within_cash", f"buys need {buy_cash:.4f}, cash is {account.cash:.2f}")

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
    buffer = 1 + _dec(frozen.whole_share_price_buffer)
    cash_d = _dec(cash)

    def whole_shares(buy: BuyToSize, scale: Decimal) -> float:
        buffered = _dec(prices[buy.decision.security_id]) * buffer
        return float(
            (_dec(buy.remainder.notional) * scale / buffered).to_integral_value(ROUND_DOWN)
        )

    def attempt(buy: BuyToSize, scale: Decimal) -> float:
        return float((_dec(buy.remainder.notional) * scale).quantize(_CENT, rounding=ROUND_DOWN))

    def need(buy: BuyToSize, scale: Decimal) -> Decimal:
        price = prices[buy.decision.security_id]
        if buy.by_whole_shares:
            return _buy_cash(None, whole_shares(buy, scale), price, True, frozen, costs)
        return _buy_cash(attempt(buy, scale), None, price, False, frozen, costs)

    def scale_for(active: list[BuyToSize]) -> Decimal:
        total = sum((_dec(b.remainder.notional) for b in active), Decimal(0))
        if not active or total <= 0:
            return Decimal(0)
        # Each buy pays its own per-order commission, and the lowest price buys
        # the most shares (the worst case for a per-share one); Alpaca charges
        # neither.
        low_price = min(prices[b.decision.security_id] for b in active)
        return min(Decimal(1), _spendable(cash_d, len(active), low_price, costs) / total)

    def below_minimum(buy: BuyToSize, scale: Decimal) -> bool:
        if not buy.by_whole_shares:
            notional = attempt(buy, scale)
            return notional <= 0 or notional < frozen.min_order_notional
        quantity = whole_shares(buy, scale)
        value = quantity * prices[buy.decision.security_id]
        return quantity < 1 or value < frozen.min_order_notional

    active = [b for b in decisions if b.remainder.notional > 0]
    deferred: set[int] = {id(b) for b in decisions if b.remainder.notional <= 0}
    never_fit = {id(b) for b in active if below_minimum(b, Decimal(1))}
    deferred.update(never_fit)
    active = [b for b in active if id(b) not in never_fit]
    while True:
        scale = scale_for(active)
        low = [b for b in active if below_minimum(b, scale)]
        if not low:
            break
        # Ties by security_id ascending: stable and always present; the owner's #409 said symbol.
        dropped = min(low, key=lambda b: (b.remainder.notional, b.decision.security_id))
        deferred.add(id(dropped))
        active = [b for b in active if id(b) != id(dropped)]

    notionals = {
        id(b): _dec(attempt(b, scale))
        for b in decisions
        if not b.by_whole_shares and id(b) not in deferred
    }
    by_id = {id(b): b for b in decisions}

    def notional_need(key: int) -> Decimal:
        buy = by_id[key]
        price = prices[buy.decision.security_id]
        return _buy_cash(float(notionals[key]), None, price, False, frozen, costs)

    # Exact arithmetic keeps the floored batch within cash; `Decimal` rounding
    # can overshoot by a few units in its last place. One cut, the overshoot
    # rounded up to the cent, off the largest notional removes at least the
    # overshoot (a dollar less needs at least a dollar less), so no loop; an
    # overshoot above a cent is not rounding, and raises.
    over = sum((notional_need(k) for k in notionals), Decimal(0)) - cash_d
    if over > 0:
        largest = max(notionals, key=lambda k: notionals[k])
        cut = over.quantize(_CENT, rounding=ROUND_UP)
        if cut > _CENT or notionals[largest] - cut < _dec(frozen.min_order_notional):
            raise ValueError(f"buys of {cash} cash overshoot it by {over} and cannot be cut")
        notionals[largest] -= cut

    notional_sizings: list[Sizing] = []
    cash_left = cash_d
    for buy in decisions:
        if buy.by_whole_shares:
            continue
        if id(buy) in deferred:
            notional_sizings.append(_sizing(buy))
            continue
        cash_left -= notional_need(id(buy))
        notional_sizings.append(_sizing(buy, notional=float(notionals[id(buy)])))

    whole_sizings: list[Sizing] = []
    for buy in decisions:
        if not buy.by_whole_shares:
            continue
        quantity = 0.0 if id(buy) in deferred else whole_shares(buy, scale)
        cost = need(buy, scale) if quantity else Decimal(0)
        if quantity >= 1 and cost <= cash_left:
            cash_left -= cost
            whole_sizings.append(_sizing(buy, quantity=quantity))
        else:
            whole_sizings.append(_sizing(buy))
    return notional_sizings + whole_sizings


def _sizing(
    buy: BuyToSize, *, notional: float | None = None, quantity: float | None = None
) -> Sizing:
    assert buy.decision.decision_id is not None
    return Sizing(buy.decision.decision_id, buy.decision.security_id, notional, quantity)

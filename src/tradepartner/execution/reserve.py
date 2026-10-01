"""The reserve for our own open buys (ADR 0010 amendment 2026-10-01, spec req 3;
plan T54d).

`account().cash` does not hold back the notional of our own buys that are still
open at the broker, so a later run could size its buys against the full cash and,
if both filled, borrow. The buys phase therefore reads its cash as
max(0, `account().cash` - `open_buy_reserve(...)`); the wrapper (T60b) makes that
one subtraction and passes the result to both `phases.buy_orders` and
`risk.check_phase`.

The reserve is the sum, over our own **non-terminal buys from any session**, of
the unfilled notional:

- a notional buy: its submitted notional minus its journaled filled value;
- a quantity buy: its unfilled quantity (submitted minus journaled filled),
  split-adjusted by the splits with ex-date in (its order's session, S], as
  `plan.remainder` does, times the reference price times
  (1 + `risk.whole_share_price_buffer`).

A buy with no event yet (journaled before its submit) is open. Terminal buys and
every sell reserve nothing. Each order's unfilled amount is clamped to
[0, what it was submitted for], so an over-fill never makes the reserve negative.
The sum is computed in `Decimal` from each float's shortest repr and rounded
**up** to the cent, so the reserve never under-reserves.

No look-ahead: `actions_as_of` is read at close(S-1) and only rows with
`known_at` <= close(S-1) are applied; an order dated after S raises. The
journal's own rows are not cut at close(S-1): a buy submitted earlier on S (a
same-session re-run) is exactly what the reserve must count. Pure: no broker, no
clock, no store.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal

import polars as pl

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.plan import PriceOf
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

_BUY = "buy"
_SPLIT = "split"
_CENT = Decimal("0.01")
_ZERO = Decimal(0)
_ONE = Decimal(1)


def _dec(value: float, what: str, *, positive: bool = False) -> Decimal:
    """`value` as a `Decimal` from its shortest repr; finite and non-negative
    (strictly positive with `positive`), or `ValueError`."""
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        need = "positive" if positive else "non-negative"
        raise ValueError(f"{what} is {value}, must be a finite {need} number")
    return Decimal(repr(float(value)))


def _filled(order: OrderRow, fills: Iterable[OrderedFill]) -> tuple[Decimal, Decimal]:
    """Filled shares and filled value of one order."""
    quantity = value = _ZERO
    for fill in fills:
        row = fill.fill
        if row.client_order_id != order.client_order_id:
            continue
        if (fill.side, fill.security_id) != (order.side, order.security_id):
            raise ValueError(f"fill {row.fill_id} disagrees with order {order.client_order_id!r}")
        shares = _dec(row.quantity, f"fill {row.fill_id} quantity")
        quantity += shares
        value += shares * _dec(row.price, f"fill {row.fill_id} price")
    return quantity, value


def _split_factor(
    actions_as_of: pl.DataFrame, security_id: str, stated_on: date, session: date
) -> Decimal:
    """The product of the ratios of splits with ex-date in (`stated_on`, `session`]
    known at close(S-1)."""
    cutoff = session_close(previous_session(session))
    factor = _ONE
    for row in actions_as_of.iter_rows(named=True):
        if (
            row["security_id"] == security_id
            and row["action_type"] == _SPLIT
            and stated_on < row["ex_date"] <= session
            and row["known_at"] <= cutoff
        ):
            factor *= _dec(
                float(row["ratio_or_amount"]), f"split ratio of {security_id}", positive=True
            )
    return factor


def _unfilled_notional(
    order: OrderRow,
    fills: Sequence[OrderedFill],
    actions_as_of: pl.DataFrame,
    price_of: PriceOf,
    buffer: Decimal,
    session: date,
) -> Decimal:
    shares, value = _filled(order, fills)
    if order.quantity is not None:
        submitted = _dec(order.quantity, f"order {order.client_order_id!r} quantity")
        factor = _split_factor(actions_as_of, order.security_id, order.session, session)
        unfilled = min(max(submitted - shares, _ZERO), submitted) * factor
        price = _dec(price_of(order.security_id), f"price of {order.security_id}", positive=True)
        return unfilled * price * (_ONE + buffer)
    if order.notional is not None:
        submitted = _dec(order.notional, f"order {order.client_order_id!r} notional")
        return min(max(submitted - value, _ZERO), submitted)
    raise ValueError(f"order {order.client_order_id!r} has neither quantity nor notional")


def open_buy_reserve(
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    actions_as_of: pl.DataFrame,
    price_of: PriceOf,
    frozen: RiskConfig,
    *,
    session: date,
) -> Decimal:
    """The unfilled notional of every own non-terminal buy (module docstring),
    rounded up to the cent. `orders`, `order_events` and `fills` are the
    window's journal rows (`fills` from `store.journal.fills_for`, so a
    superseded fill is never counted); `actions_as_of` a
    `store.asof.live_actions_as_of` frame read at close(S-1); `price_of` the
    reference price on S; `frozen` the window's `risk` section; `session` is S.
    """
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")
    if "known_at" not in actions_as_of.columns:
        raise ValueError("actions_as_of has no known_at column; read it with live_actions_as_of")
    buffer = _dec(frozen.whole_share_price_buffer, "risk.whole_share_price_buffer")
    terminal = {e.client_order_id for e in order_events if e.status in TERMINAL_ORDER_STATUSES}
    total = _ZERO
    for order in orders:
        if order.session > session:
            raise ValueError(
                f"order {order.client_order_id!r} is dated {order.session}, after the session "
                f"{session}"
            )
        if order.side != _BUY or order.client_order_id in terminal:
            continue
        total += _unfilled_notional(order, fills, actions_as_of, price_of, buffer, session)
    return total.quantize(_CENT, rounding=ROUND_CEILING)

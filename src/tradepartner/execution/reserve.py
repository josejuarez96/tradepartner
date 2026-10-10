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
- a quantity buy: its unfilled quantity in post-split shares, times the
  reference price times (1 + `risk.whole_share_price_buffer`). The submitted
  quantity is split-adjusted by the splits with ex-date in (its order's
  session, S], as `plan.remainder` does; each journaled fill by the splits with
  ex-date in (that fill's day, S], where a fill's day is the New York date of
  its `filled_at` (never before its order's session), so a fill on or after an
  ex-date is already in post-split shares and is not adjusted again (#1445).

A buy with no event yet (journaled before its submit) is open. Terminal buys and
every sell reserve nothing. Each order's unfilled amount is clamped to
[0, what it was submitted for (split-adjusted)], so an over-fill never makes the reserve negative.
The sum is computed in `Decimal` from each float's shortest repr and rounded
**up** to the cent, so the reserve never under-reserves.

No look-ahead: `actions_as_of` must be read at close(S-1); a row with `known_at`
after close(S-1) raises instead of being skipped, because `price_of` would then be
post-split while the share count stayed pre-split, an under-reserve. An order
dated after S raises too. The
journal's own rows are not cut at close(S-1): a buy submitted earlier on S (a
same-session re-run) is exactly what the reserve must count. Pure: no broker, no
clock, no store.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

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
_NEW_YORK = ZoneInfo("America/New_York")


def _dec(value: float, what: str, *, positive: bool = False) -> Decimal:
    """`value` as a `Decimal` from its shortest repr; finite and non-negative
    (strictly positive with `positive`), or `ValueError`."""
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        need = "positive" if positive else "non-negative"
        raise ValueError(f"{what} is {value}, must be a finite {need} number")
    return Decimal(repr(float(value)))


def _filled(
    order: OrderRow, fills: Iterable[OrderedFill]
) -> tuple[list[tuple[Decimal, date]], Decimal]:
    """Each fill of one order as (shares, the New York date of its `filled_at`),
    and the order's filled value."""
    shares: list[tuple[Decimal, date]] = []
    value = _ZERO
    for fill in fills:
        row = fill.fill
        if row.client_order_id != order.client_order_id:
            continue
        if (fill.side, fill.security_id) != (order.side, order.security_id):
            raise ValueError(f"fill {row.fill_id} disagrees with order {order.client_order_id!r}")
        quantity = _dec(row.quantity, f"fill {row.fill_id} quantity")
        shares.append((quantity, row.filled_at.astimezone(_NEW_YORK).date()))
        value += quantity * _dec(row.price, f"fill {row.fill_id} price")
    return shares, value


def _check_actions(actions_as_of: pl.DataFrame, session: date) -> None:
    """`actions_as_of` carries `known_at` and `ex_date` on every row and no row
    known after close(S-1), or `ValueError`."""
    for column in ("known_at", "ex_date"):
        if column not in actions_as_of.columns:
            raise ValueError(
                f"actions_as_of has no {column} column; read it with live_actions_as_of"
            )
        if actions_as_of[column].null_count():
            raise ValueError(f"actions_as_of has a row with no {column}")
    cutoff = session_close(previous_session(session))
    for row in actions_as_of.iter_rows(named=True):
        if row["known_at"] > cutoff:
            raise ValueError(
                f"actions_as_of holds a {row['action_type']} of {row['security_id']} known at "
                f"{row['known_at']}, after close(S-1) {cutoff}: read it at close(S-1)"
            )


def _split_factor(
    actions_as_of: pl.DataFrame, security_id: str, stated_on: date, session: date
) -> Decimal:
    """The product of the ratios of splits with ex-date in (`stated_on`, `session`]."""
    factor = _ONE
    for row in actions_as_of.iter_rows(named=True):
        if (
            row["security_id"] == security_id
            and row["action_type"] == _SPLIT
            and stated_on < row["ex_date"] <= session
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
        # each fill in post-split shares: a fill's day is never taken before its
        # order's session, so a bad stamp adjusts it by fewer splits, not more
        filled = sum(
            (
                quantity
                * _split_factor(actions_as_of, order.security_id, max(day, order.session), session)
                for quantity, day in shares
            ),
            _ZERO,
        )
        unfilled = min(max(submitted * factor - filled, _ZERO), submitted * factor)
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
    rounded up to the cent. `orders`, `order_events` and `fills` are our own
    journal rows from every session and every window (a buy left open by an
    earlier window is still ours and still open; `fills` from
    `store.journal.fills_for`, so a superseded fill is never counted); `actions_as_of` a
    `store.asof.live_actions_as_of` frame read at close(S-1); `price_of` the
    reference price on S; `frozen` the window's `risk` section; `session` is S.
    """
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")
    _check_actions(actions_as_of, session)
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

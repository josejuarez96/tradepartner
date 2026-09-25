"""In-memory fake `Broker` (spec req 7).

Deterministic and offline: nothing here reads the wall clock or the
network. Every timestamp comes from the caller — `Order.submitted_at` at
submission time, and the `session_open_ts`/`session_close_ts` a caller
passes to `mark_session_open`/`mark_session_close` to simulate a trading
session.

**DAY limit-order fill rule (owner decision, T20 review round 2; see
`broker.py`'s module docstring for the canonical statement).** A DAY
order is evaluated only once, at its session's open: a market order
fills unconditionally at the given open price; a limit order fills at
that price only if a buy's limit price is >= the open price or a sell's
limit price is <= the open price. Otherwise it stays `"accepted"` until
`mark_session_close` expires it. **An order never fills intraday** — the
only two events that can change an order's status are one
`mark_session_open` call and one `mark_session_close` call per session.
The Phase 3 backtester and the `bt` oracle must apply the identical rule.

**No look-ahead.** An order is eligible for a session only if
`order.submitted_at` is strictly before that session's open timestamp:
`mark_session_open` fills only eligible accepted orders, and
`mark_session_close` expires only accepted orders that were eligible for
the session it is closing (tracked via the open timestamp last passed to
`mark_session_open`, not the order's own status) — an order submitted
between one session's open and its close is not filled at that open (it
already happened) and is not expired at that close either (it was never
eligible for that session), so it survives, unfilled, into the next
session. `mark_session_open`/`mark_session_close` also require their
timestamps to be non-decreasing across calls (`ValueError` otherwise), so
a caller cannot accidentally replay time backwards.

**No risk logic.** Per ADR 0003 rule 7 and `broker.py`'s module
docstring, `FakeBroker` accepts and fills whatever valid `Order` it is
given, including a sell for more shares than are currently held — the
resulting position simply goes negative (short). Stopping that is the
risk-gated wrapper's job (Phase 4), not this adapter's.

`FakeBroker` never produces `"partially_filled"` or `"rejected"`
(`OrderStatus` also covers what a real adapter can report); an order
here is filled in full or not at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from tradepartner.adapters.broker import (
    Broker,
    DuplicateOrderError,
    Fill,
    Order,
    OrderNotCancellableError,
    OrderNotFoundError,
    OrderStatus,
    Position,
    require_tz_aware,
)

__all__ = [
    "DuplicateOrderError",
    "FakeBroker",
    "OrderNotCancellableError",
    "OrderNotFoundError",
]


def _validate_prices(prices: Mapping[str, Decimal]) -> None:
    """Validate every price in `prices` before `mark_session_open` touches
    any order or position state, so a single bad price cannot leave some
    orders filled and others not (T20 review round 2, safety-reviewer
    MUST FIX: atomicity)."""
    for security_id, price in prices.items():
        if not isinstance(price, Decimal):
            raise TypeError(
                f"price for {security_id!r} must be a Decimal, got {type(price).__name__}"
            )
        if not price.is_finite():
            raise ValueError(f"price for {security_id!r} must be finite, got {price!r}")
        if price <= 0:
            raise ValueError(f"price for {security_id!r} must be > 0, got {price!r}")


@dataclass(frozen=True)
class _OrderRecord:
    """An order's immutable snapshot: the submitted `Order` plus its
    current `status`. A status change replaces the dict entry with a new
    `_OrderRecord` (`dataclasses.replace`) rather than mutating one in
    place, mirroring the store's "revision is a new row" rule.
    `FakeBroker.orders` exposes these for inspection as a read-only
    `MappingProxyType`."""

    order: Order
    status: OrderStatus


@dataclass
class _PositionState:
    """Running position for one security, updated fill by fill. `qty` is
    signed (positive long, negative short); `cost` is the matching signed
    cost basis (`qty * avg_price`), from which `avg_price` is derived
    on demand in `FakeBroker.positions` rather than stored redundantly."""

    symbol: str
    qty: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)


def _apply_fill(state: _PositionState, order: Order, fill: Fill) -> None:
    """Update `state` in place for one fill, using standard average-cost
    accounting with signed quantities (buys positive, sells negative) and
    a signed cost basis.

    Three cases, by how the fill's signed delta relates to the existing
    position:
    - **Same direction as the existing position (or opening one from
      flat):** the fill adds to the position; cost basis accumulates
      (`cost += delta_qty * price`), which is equivalent to a
      quantity-weighted average price.
    - **Opposite direction, reducing magnitude without crossing zero
      (including closing to exactly flat):** a partial (or full) close;
      cost basis shrinks proportionally (`cost *= new_qty / old_qty`),
      which leaves `avg_price = cost / qty` unchanged for whatever
      quantity remains.
    - **Opposite direction, crossing zero (selling more than held, or the
      reverse):** the old position's cost basis is fully closed and a new
      one opens on the other side at the fill price (`cost = new_qty *
      price`). No risk logic stops this — see `broker.Position`'s
      docstring.

    `state.symbol` is refreshed to `order.symbol` on every fill (not just
    when opening from flat), so it tracks the most recently traded symbol
    for this `security_id` (e.g. after a same-company ticker change).
    """
    delta_qty = fill.qty if order.side == "buy" else -fill.qty
    old_qty = state.qty
    new_qty = old_qty + delta_qty

    same_direction_or_opening = old_qty == 0 or (old_qty > 0) == (delta_qty > 0)
    crossed_zero = not same_direction_or_opening and new_qty != 0 and (new_qty > 0) != (old_qty > 0)

    if same_direction_or_opening:
        state.cost += delta_qty * fill.price
    elif crossed_zero:
        state.cost = new_qty * fill.price
    else:
        # Reducing magnitude, possibly to exactly flat, without flipping
        # sign: old_qty is non-zero here (the `same_direction_or_opening`
        # branch above already covers old_qty == 0).
        state.cost = state.cost * new_qty / old_qty

    state.qty = new_qty
    state.symbol = order.symbol


class FakeBroker(Broker):
    """In-memory `Broker` for tests and backtesting scaffolding. See the
    module docstring for fill/expiry/look-ahead semantics."""

    def __init__(self) -> None:
        self._orders: dict[str, _OrderRecord] = {}
        self._open_ids: set[str] = set()
        self._fills: list[Fill] = []
        self._positions: dict[str, _PositionState] = {}
        # The timestamp last passed to mark_session_open: the eligibility
        # threshold mark_session_close uses to decide which still-accepted
        # orders belong to the session it is closing (no-look-ahead fix,
        # review round 2).
        self._last_session_open_ts: datetime | None = None
        # The timestamp last passed to either mark_session_open or
        # mark_session_close, for the monotonic (non-decreasing) check.
        self._last_session_ts: datetime | None = None

    @property
    def orders(self) -> Mapping[str, _OrderRecord]:
        """Read-only view of every order ever submitted and its current
        status. Mutating it (e.g. `broker.orders[x] = ...`) raises
        `TypeError`, since this is a `MappingProxyType` over the
        broker's own private dict, never a copy a caller could edit
        without effect."""
        return MappingProxyType(self._orders)

    def submit(self, order: Order) -> OrderStatus:
        if order.client_order_id in self._orders:
            raise DuplicateOrderError(
                f"client_order_id {order.client_order_id!r} was already submitted"
            )
        self._orders[order.client_order_id] = _OrderRecord(order=order, status="accepted")
        self._open_ids.add(order.client_order_id)
        return "accepted"

    def cancel(self, client_order_id: str) -> OrderStatus:
        record = self._get_record(client_order_id)
        if record.status != "accepted":
            raise OrderNotCancellableError(
                f"order {client_order_id!r} is {record.status!r}, not cancellable"
            )
        self._set_status(client_order_id, "cancelled")
        self._open_ids.discard(client_order_id)
        return "cancelled"

    def positions(self) -> list[Position]:
        result: list[Position] = []
        for security_id, state in self._positions.items():
            if state.qty == 0:
                continue
            result.append(
                Position(
                    security_id=security_id,
                    symbol=state.symbol,
                    qty=state.qty,
                    avg_price=state.cost / state.qty,
                )
            )
        return result

    def fills(self, since: datetime) -> list[Fill]:
        since = require_tz_aware(since)
        return [fill for fill in self._fills if fill.filled_at >= since]

    def order_status(self, client_order_id: str) -> OrderStatus:
        return self._get_record(client_order_id).status

    def mark_session_open(self, session_open_ts: datetime, prices: Mapping[str, Decimal]) -> None:
        """Fill every eligible `"accepted"` DAY order against `prices`
        (keyed by `security_id`): market orders fill unconditionally at
        the given price; limit orders fill at that price only if it
        satisfies the limit (buy: price <= limit_price; sell: price >=
        limit_price). An order is eligible only if `order.submitted_at <
        session_open_ts` (module docstring: "No look-ahead"). An
        ineligible order, an order for a security missing from `prices`,
        or a limit order whose limit is not satisfied, stays `"accepted"`
        until `mark_session_close` expires it.

        Every price in `prices` is validated (a `Decimal`, finite, > 0)
        before any order or position state is touched: an invalid price
        anywhere in `prices` raises and leaves every order and position
        exactly as it was.

        Raises `ValueError` if `session_open_ts` is naive, or is earlier
        than the last timestamp passed to this method or
        `mark_session_close` (session timestamps must be non-decreasing).
        """
        session_open_ts = require_tz_aware(session_open_ts)
        _validate_prices(prices)
        self._advance_session_clock(session_open_ts)

        for client_order_id in list(self._open_ids):
            record = self._orders[client_order_id]
            order = record.order
            if not order.submitted_at < session_open_ts:
                continue
            price = prices.get(order.security_id)
            if price is None:
                continue
            if order.order_type == "limit" and not self._limit_satisfied(order, price):
                continue
            self._fill(client_order_id, order, price, session_open_ts)

        self._last_session_open_ts = session_open_ts

    def mark_session_close(self, session_close_ts: datetime) -> None:
        """Expire every still-`"accepted"` order that was eligible for the
        session being closed — i.e. `order.submitted_at` is strictly
        before the timestamp last passed to `mark_session_open`. An order
        submitted after that open was never eligible to fill this
        session either, so it is left `"accepted"` for the next session
        rather than expired here (module docstring: "No look-ahead"). If
        `mark_session_close` is called before any `mark_session_open`,
        there is no known session-open threshold, so nothing is expired.

        Raises `ValueError` if `session_close_ts` is naive, or is earlier
        than the last timestamp passed to this method or
        `mark_session_open`.
        """
        session_close_ts = require_tz_aware(session_close_ts)
        self._advance_session_clock(session_close_ts)

        open_ts = self._last_session_open_ts
        if open_ts is None:
            return
        for client_order_id in list(self._open_ids):
            if self._orders[client_order_id].order.submitted_at < open_ts:
                self._expire(client_order_id)

    def _advance_session_clock(self, ts: datetime) -> None:
        """Enforce that timestamps passed across `mark_session_open`/
        `mark_session_close` calls are non-decreasing, then record `ts`."""
        if self._last_session_ts is not None and ts < self._last_session_ts:
            raise ValueError(
                "session timestamps passed to mark_session_open/mark_session_close "
                f"must be non-decreasing: {ts!r} is before the last one seen, "
                f"{self._last_session_ts!r}"
            )
        self._last_session_ts = ts

    def _get_record(self, client_order_id: str) -> _OrderRecord:
        try:
            return self._orders[client_order_id]
        except KeyError:
            raise OrderNotFoundError(f"no such order: {client_order_id!r}") from None

    def _set_status(self, client_order_id: str, status: OrderStatus) -> None:
        self._orders[client_order_id] = replace(self._orders[client_order_id], status=status)

    @staticmethod
    def _limit_satisfied(order: Order, price: Decimal) -> bool:
        assert order.limit_price is not None  # enforced by Order's validator
        if order.side == "buy":
            return price <= order.limit_price
        return price >= order.limit_price

    def _fill(
        self, client_order_id: str, order: Order, price: Decimal, filled_at: datetime
    ) -> None:
        record = self._orders[client_order_id]
        assert record.status == "accepted", (
            f"order {client_order_id!r} already has status {record.status!r}; "
            "an order must never be filled twice"
        )
        assert filled_at > order.submitted_at, "a fill must not precede its order's submission"

        fill = Fill(
            client_order_id=client_order_id,
            qty=order.qty,
            price=price,
            filled_at=filled_at,
        )
        self._fills.append(fill)
        self._set_status(client_order_id, "filled")
        self._open_ids.discard(client_order_id)

        state = self._positions.setdefault(order.security_id, _PositionState(symbol=order.symbol))
        _apply_fill(state, order, fill)

    def _expire(self, client_order_id: str) -> None:
        self._set_status(client_order_id, "expired")
        self._open_ids.discard(client_order_id)

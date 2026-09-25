"""In-memory fake `Broker` (spec req 7).

Deterministic and offline: nothing here reads the wall clock or the
network. Every timestamp comes from the caller — `Order.submitted_at` at
submission time, and the `session_open_ts`/`session_close_ts` a test
passes to `mark_session_open`/`mark_session_close` to simulate a trading
session. This mirrors ADR 0006's cadence (DAY orders placed for the T+1
open): an order sits `accepted` until the next call to
`mark_session_open` fills it (market orders unconditionally at the given
open price; limit orders only if the limit is satisfied), or until
`mark_session_close` expires whatever is still `accepted` (a DAY order
that never filled).

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

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradepartner.adapters.broker import Broker, Fill, Order, OrderStatus, Position


def _require_tz_aware(value: datetime) -> datetime:
    """Same check as `broker._require_tz_aware`, duplicated locally rather
    than imported (that helper is private to `broker.py`)."""
    if value.tzinfo is None:
        raise ValueError(f"must be tz-aware, got a naive datetime: {value!r}")
    return value


class DuplicateOrderError(RuntimeError):
    """Raised by `FakeBroker.submit` when `client_order_id` was already
    submitted. The original order and its status are left unchanged."""


class OrderNotFoundError(LookupError):
    """Raised when `client_order_id` names no order this `FakeBroker` has
    ever seen."""


class OrderNotCancellableError(RuntimeError):
    """Raised by `FakeBroker.cancel` when the order named is not in the
    `"accepted"` state (e.g. already filled, cancelled or expired)."""


@dataclass
class _OrderRecord:
    """One order's current state, as tracked internally. `fake.orders`
    exposes these directly (spec: "Expose fake.orders for inspection")."""

    order: Order
    status: OrderStatus


@dataclass
class _PositionState:
    """Running average-cost position for one security, updated fill by
    fill. `qty` is signed: positive is long, negative is short."""

    symbol: str
    qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)


def _apply_fill(state: _PositionState, order: Order, fill: Fill) -> None:
    """Update `state` in place for one fill, using standard average-cost
    accounting with signed quantities (buys positive, sells negative).

    Three cases, by how the fill's signed delta relates to the existing
    position:
    - **Same direction as the existing position (or opening one from
      flat):** the fill adds to the position; `avg_price` becomes the
      quantity-weighted average of the old position and the fill.
    - **Opposite direction, reducing magnitude without crossing zero:**
      a partial close; `avg_price` is unchanged (this fake tracks no
      realized P&L), only `qty` shrinks.
    - **Opposite direction, crossing zero (selling more than held, or the
      reverse):** the old position is fully closed and a new one opens on
      the other side at the fill price. No risk logic stops this — see
      `broker.Position`'s docstring.
    """
    delta = fill.qty if order.side == "buy" else -fill.qty
    old_qty = state.qty
    new_qty = old_qty + delta

    if old_qty == 0 or (old_qty > 0) == (delta > 0):
        old_notional = old_qty * state.avg_price
        added_notional = delta * fill.price
        state.avg_price = (old_notional + added_notional) / new_qty if new_qty != 0 else Decimal(0)
    elif new_qty == 0:
        state.avg_price = Decimal(0)
    elif (new_qty > 0) != (old_qty > 0):
        # Crossed through zero: the excess beyond closing the old
        # position opens a fresh one at the fill price.
        state.avg_price = fill.price
    # else: reducing magnitude without crossing zero -> avg_price unchanged.

    state.qty = new_qty


class FakeBroker(Broker):
    """In-memory `Broker` for tests and backtesting scaffolding. See the
    module docstring for fill/expiry semantics."""

    def __init__(self) -> None:
        self.orders: dict[str, _OrderRecord] = {}
        self._fills: list[Fill] = []
        self._positions: dict[str, _PositionState] = {}

    def submit(self, order: Order) -> OrderStatus:
        if order.client_order_id in self.orders:
            raise DuplicateOrderError(
                f"client_order_id {order.client_order_id!r} was already submitted"
            )
        self.orders[order.client_order_id] = _OrderRecord(order=order, status="accepted")
        return "accepted"

    def cancel(self, client_order_id: str) -> OrderStatus:
        record = self._record(client_order_id)
        if record.status != "accepted":
            raise OrderNotCancellableError(
                f"order {client_order_id!r} is {record.status!r}, not cancellable"
            )
        record.status = "cancelled"
        return "cancelled"

    def positions(self) -> list[Position]:
        return [
            Position(
                security_id=security_id,
                symbol=state.symbol,
                qty=state.qty,
                avg_price=state.avg_price,
            )
            for security_id, state in self._positions.items()
            if state.qty != 0
        ]

    def fills(self, since: datetime) -> list[Fill]:
        _require_tz_aware(since)
        return [fill for fill in self._fills if fill.filled_at >= since]

    def order_status(self, client_order_id: str) -> OrderStatus:
        return self._record(client_order_id).status

    def mark_session_open(self, session_open_ts: datetime, prices: dict[str, Decimal]) -> None:
        """Fill every `"accepted"` DAY order against `prices` (keyed by
        `security_id`): market orders fill unconditionally at the given
        price; limit orders fill at that price only if it satisfies the
        limit (buy: price <= limit_price; sell: price >= limit_price).
        An order for a security missing from `prices`, or a limit order
        whose limit is not satisfied, stays `"accepted"` until
        `mark_session_close` expires it.
        """
        _require_tz_aware(session_open_ts)
        for record in self.orders.values():
            if record.status != "accepted":
                continue
            order = record.order
            price = prices.get(order.security_id)
            if price is None:
                continue
            if order.order_type == "limit" and not self._limit_satisfied(order, price):
                continue
            self._fill(record, order, price, session_open_ts)

    def mark_session_close(self, session_close_ts: datetime) -> None:
        """Expire every order still `"accepted"` (a DAY order that never
        filled during the session)."""
        _require_tz_aware(session_close_ts)
        for record in self.orders.values():
            if record.status == "accepted":
                record.status = "expired"

    def _record(self, client_order_id: str) -> _OrderRecord:
        try:
            return self.orders[client_order_id]
        except KeyError:
            raise OrderNotFoundError(f"no such order: {client_order_id!r}") from None

    @staticmethod
    def _limit_satisfied(order: Order, price: Decimal) -> bool:
        assert order.limit_price is not None  # enforced by Order's validator
        if order.side == "buy":
            return price <= order.limit_price
        return price >= order.limit_price

    def _fill(
        self, record: _OrderRecord, order: Order, price: Decimal, filled_at: datetime
    ) -> None:
        fill = Fill(
            client_order_id=order.client_order_id,
            qty=order.qty,
            price=price,
            filled_at=filled_at,
        )
        self._fills.append(fill)
        record.status = "filled"
        state = self._positions.setdefault(order.security_id, _PositionState(symbol=order.symbol))
        _apply_fill(state, order, fill)

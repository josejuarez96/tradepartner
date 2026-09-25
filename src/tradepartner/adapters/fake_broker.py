"""In-memory fake `Broker` (spec req 7 "fake broker"; T20).

**No risk logic lives here.** No position limits, no kill switch, no order
sizing, no reconciliation against a real account — those are Phase 4
(`docs/roadmap.md`; spec "Out of scope": "Risk-gated broker wrapper, Alpaca
paper adapter, alerts (Phase 4)"). `FakeBroker` exists only so the rest of
the system has something to submit orders to before a real broker adapter
exists; it does no I/O, no network calls, and spawns no threads. It models
**net** positions only: a sell with no prior long is accepted and books a
negative (short) quantity — nothing here prevents or limits that (Phase 4).

**Fill mechanism (documented choice).** `OrderRequest.price` is the price
the order fills at — there is no market simulation, no slippage and no
costs modeled (those are Phase 3/4 concerns; spec "Risks & domain checks":
"Costs: none. Order path: fake broker only"). By default (`auto_fill=True`,
the constructor default) `submit` fills the order immediately, in the same
call, at `request.price`. Passing `auto_fill=False` instead leaves the
order `OPEN` after `submit` so a caller can exercise `cancel` on it (as the
acceptance tests do); such an order is filled explicitly by calling
`simulate_fill(client_order_id)`, which fills the order's full remaining
quantity at its original `request.price` — there is no partial fill.
`simulate_fill` is a `FakeBroker`-only escape hatch, not part of the
`Broker` interface (which is exactly `submit`/`cancel`/`positions`/`fills`).

**Clock.** All timestamps come from an injectable `clock: Callable[[],
datetime]` supplied at construction, never from `datetime.now()` called
internally, so tests are deterministic (CLAUDE.md: "Datetimes are always
timezone-aware UTC"). A clock that returns a naive `datetime` causes the
same `ValueError` as any other naive timestamp, raised when the value is
used to construct an `Order`/`Fill`.

**Position netting.** Net quantity per symbol is accumulated internally as
`decimal.Decimal` (via `Decimal(repr(fill.quantity))`) rather than `float`,
so a sequence like +0.1, +0.2, -0.3 nets to exactly zero instead of a
`float` rounding artifact (~5e-17) that would leave a phantom position
behind; the running total is converted back to `float` only when building
the `Position` returned to callers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from tradepartner.adapters.broker import (
    Broker,
    DuplicateClientOrderIdError,
    Fill,
    Order,
    OrderNotOpenError,
    OrderRequest,
    OrderStatus,
    Position,
    Side,
    UnknownOrderError,
)


class FakeBroker(Broker):
    """In-memory `Broker` for tests and local research runs. See the
    module docstring for the fill mechanism, position-netting precision,
    and the explicit absence of risk logic (no position limits, no kill
    switch, no order sizing — Phase 4)."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        auto_fill: bool = True,
    ) -> None:
        self._clock = clock
        self._auto_fill = auto_fill
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._net_quantity: dict[str, Decimal] = {}

    def submit(self, request: OrderRequest) -> Order:
        if request.client_order_id in self._orders:
            raise DuplicateClientOrderIdError(
                f"client_order_id already submitted: {request.client_order_id!r}"
            )

        submitted_at = self._clock()
        order = Order(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            status=OrderStatus.OPEN,
            submitted_at=submitted_at,
        )
        self._orders[request.client_order_id] = order

        if self._auto_fill:
            return self._simulate_fill(
                client_order_id=request.client_order_id, filled_at=submitted_at
            )
        return order

    def cancel(self, client_order_id: str) -> Order:
        order = self._require_order(client_order_id)
        if order.status is not OrderStatus.OPEN:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {order.status.value}, cannot cancel"
            )
        cancelled = replace(order, status=OrderStatus.CANCELLED)
        self._orders[client_order_id] = cancelled
        return cancelled

    def simulate_fill(self, client_order_id: str) -> Order:
        """Fill an open order's full remaining quantity at its original
        `OrderRequest.price`, timestamped by the injected clock. Not part
        of the `Broker` interface — a `FakeBroker`-specific escape hatch
        for tests that submit with `auto_fill=False` (see module
        docstring)."""
        return self._simulate_fill(client_order_id=client_order_id, filled_at=self._clock())

    def _simulate_fill(self, *, client_order_id: str, filled_at: datetime) -> Order:
        order = self._require_order(client_order_id)
        if order.status is not OrderStatus.OPEN:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {order.status.value}, cannot fill"
            )

        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            filled_at=filled_at,
        )
        self._fills.append(fill)
        self._apply_fill_to_net(fill)

        filled_order = replace(order, status=OrderStatus.FILLED)
        self._orders[client_order_id] = filled_order
        return filled_order

    def positions(self) -> dict[str, Position]:
        return {
            symbol: Position(symbol=symbol, quantity=float(net))
            for symbol, net in self._net_quantity.items()
            if net != 0
        }

    def fills(self) -> list[Fill]:
        return list(self._fills)

    def _apply_fill_to_net(self, fill: Fill) -> None:
        delta = Decimal(repr(fill.quantity))
        if fill.side is Side.BUY:
            signed_delta = delta
        elif fill.side is Side.SELL:
            signed_delta = -delta
        else:  # pragma: no cover - Side is coerced/validated to one of these two
            raise ValueError(f"unexpected side: {fill.side!r}")
        self._net_quantity[fill.symbol] = (
            self._net_quantity.get(fill.symbol, Decimal(0)) + signed_delta
        )

    def _require_order(self, client_order_id: str) -> Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise UnknownOrderError(f"unknown client_order_id: {client_order_id!r}")
        return order

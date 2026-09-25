"""In-memory fake `Broker` (spec req 7 "fake broker"; T20).

**No risk logic lives here.** No position limits, no kill switch, no order
sizing, no reconciliation against a real account — those are Phase 4
(`docs/roadmap.md`; spec "Out of scope": "Risk-gated broker wrapper, Alpaca
paper adapter, alerts (Phase 4)"). `FakeBroker` exists only so the rest of
the system has something to submit orders to before a real broker adapter
exists; it does no I/O, no network calls, and spawns no threads.

**Fill mechanism (documented choice).** `OrderRequest.price` is the price
the order fills at — there is no market simulation, no slippage and no
costs modeled (those are Phase 3/4 concerns; spec "Risks & domain checks":
"Costs: none. Order path: fake broker only"). By default (`auto_fill=True`,
the constructor default) `submit` fills the order immediately, in the same
call, at `request.price`. Passing `auto_fill=False` instead leaves the
order `OPEN` after `submit` so a caller can exercise `cancel` on it (as the
acceptance tests do); such an order is filled explicitly by calling
`fill(client_order_id)`, which fills the order's full remaining quantity
at its original `request.price` — there is no partial fill.

**Clock.** All timestamps come from an injectable `clock: Callable[[],
datetime]` supplied at construction, never from `datetime.now()` called
internally, so tests are deterministic (CLAUDE.md: "Datetimes are always
timezone-aware UTC"). A clock that returns a naive `datetime` causes the
same `ValueError` as any other naive timestamp, raised when the value is
used to construct an `Order`/`Fill`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

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


@dataclass
class _OrderState:
    order: Order
    request: OrderRequest


class FakeBroker(Broker):
    """In-memory `Broker` for tests and local research runs. See the
    module docstring for the fill mechanism and the explicit absence of
    risk logic (no position limits, no kill switch, no order sizing —
    Phase 4)."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        auto_fill: bool = True,
    ) -> None:
        self._clock = clock
        self._auto_fill = auto_fill
        self._orders: dict[str, _OrderState] = {}
        self._fills: list[Fill] = []
        self._submission_order: list[str] = []

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
        self._orders[request.client_order_id] = _OrderState(order=order, request=request)
        self._submission_order.append(request.client_order_id)

        if self._auto_fill:
            return self._fill(client_order_id=request.client_order_id, filled_at=submitted_at)
        return order

    def cancel(self, client_order_id: str) -> Order:
        state = self._require_order(client_order_id)
        if state.order.status is not OrderStatus.OPEN:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {state.order.status.value}, cannot cancel"
            )
        state.order = replace(state.order, status=OrderStatus.CANCELLED)
        return state.order

    def fill(self, client_order_id: str) -> Order:
        """Fill an open order's full remaining quantity at its original
        `OrderRequest.price`, timestamped by the injected clock. Not part
        of the `Broker` interface — a `FakeBroker`-specific escape hatch
        for tests that submit with `auto_fill=False` (see module
        docstring)."""
        return self._fill(client_order_id=client_order_id, filled_at=self._clock())

    def _fill(self, *, client_order_id: str, filled_at: datetime) -> Order:
        state = self._require_order(client_order_id)
        if state.order.status is not OrderStatus.OPEN:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {state.order.status.value}, cannot fill"
            )

        fill = Fill(
            client_order_id=state.order.client_order_id,
            symbol=state.order.symbol,
            side=state.order.side,
            quantity=state.order.quantity,
            price=state.order.price,
            filled_at=filled_at,
        )
        self._fills.append(fill)
        state.order = replace(state.order, status=OrderStatus.FILLED)
        return state.order

    def positions(self) -> dict[str, Position]:
        net: dict[str, float] = {}
        for fill in self._fills:
            sign = 1.0 if fill.side is Side.BUY else -1.0
            net[fill.symbol] = net.get(fill.symbol, 0.0) + sign * fill.quantity
        return {
            symbol: Position(symbol=symbol, quantity=quantity)
            for symbol, quantity in net.items()
            if quantity != 0
        }

    def fills(self) -> list[Fill]:
        return list(self._fills)

    def _require_order(self, client_order_id: str) -> _OrderState:
        state = self._orders.get(client_order_id)
        if state is None:
            raise UnknownOrderError(f"unknown client_order_id: {client_order_id!r}")
        return state

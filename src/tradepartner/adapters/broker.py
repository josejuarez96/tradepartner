"""`Broker` interface: the one seam order placement goes through (spec req 7
"Broker interface"; Interfaces paragraph `Broker.submit/cancel/positions/fills`).

This module defines the abstract interface and the small typed value
objects it exchanges. It has no implementation: `adapters/fake_broker.py`
(T20) is the only implementation in Phase 2, and it holds explicitly **no**
risk logic — position limits, a kill switch and reconciliation are Phase 4
(spec "Out of scope": "Risk-gated broker wrapper, Alpaca paper adapter,
alerts (Phase 4)"; CLAUDE.md non-negotiable 5: "LLM output never reaches
order placement without passing deterministic risk rules").

Every timestamp field on these value objects is tz-aware UTC
(CLAUDE.md: "Datetimes are always timezone-aware UTC"); a naive `datetime`
raises `ValueError` at construction, matching the convention in
`store/db.ensure_tz_aware` rather than inventing a new one.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def _ensure_tz_aware(value: datetime, *, field_name: str) -> datetime:
    """Return `value` unchanged, or raise `ValueError` if it is naive.

    Mirrors `store.db.ensure_tz_aware`: this module has no store
    dependency, so the same one-line check is repeated here rather than
    importing across layers for a single guard.
    """
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be tz-aware, got a naive datetime: {value!r}")
    return value


class Side(StrEnum):
    """The explicit two-value side of an order (acceptance criterion 5:
    "side is an explicit enum")."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """An order's lifecycle state. `FakeBroker` only ever produces these
    three; there is no partial-fill state (see `fake_broker.py` docstring)."""

    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"


class DuplicateClientOrderIdError(Exception):
    """Raised when `submit` is called with a `client_order_id` that has
    already been submitted. The original order is left unchanged and no
    second order is created (acceptance criterion 2)."""


class UnknownOrderError(Exception):
    """Raised by `cancel` when no order with the given `client_order_id`
    has ever been submitted (acceptance criterion 3)."""


class OrderNotOpenError(Exception):
    """Raised by `cancel` when the order exists but is already `FILLED` or
    `CANCELLED` (acceptance criterion 3: "not silently ignored")."""


@dataclass(frozen=True)
class OrderRequest:
    """What a caller passes to `Broker.submit`.

    `price` is the price this order fills at once it fills. Real brokers
    would infer this from order type (market/limit); `FakeBroker` does no
    market simulation (spec, Risks & domain checks: "Costs: none. Order
    path: fake broker only"), so the caller supplies it directly (see
    `fake_broker.py` docstring for the full fill-mechanism note).
    """

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float

    def __post_init__(self) -> None:
        if not self.client_order_id:
            raise ValueError("client_order_id must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity!r}")
        if self.price <= 0:
            raise ValueError(f"price must be positive, got {self.price!r}")


@dataclass(frozen=True)
class Order:
    """The broker's record of a submitted order, as returned by `submit`
    and `cancel` and tracked internally between them."""

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    status: OrderStatus
    submitted_at: datetime

    def __post_init__(self) -> None:
        _ensure_tz_aware(self.submitted_at, field_name="submitted_at")


@dataclass(frozen=True)
class Fill:
    """One execution record. `FakeBroker` produces exactly one `Fill` per
    filled order — no partial fills (acceptance criterion 4)."""

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    filled_at: datetime

    def __post_init__(self) -> None:
        if not self.client_order_id:
            raise ValueError("client_order_id must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity!r}")
        _ensure_tz_aware(self.filled_at, field_name="filled_at")


@dataclass(frozen=True)
class Position:
    """Net quantity held in one symbol, aggregated from fills (buy adds,
    sell subtracts). `Broker.positions()` never reports a zero-quantity
    symbol — see `fake_broker.py` for where that's enforced."""

    symbol: str
    quantity: float = field(default=0.0)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")


class Broker(abc.ABC):
    """Abstract order-placement interface (spec "Interfaces":
    `Broker.submit/cancel/positions/fills`).

    Instantiating this class directly raises `TypeError` (it declares
    abstract methods); every concrete broker, including `FakeBroker`,
    implements all four methods.
    """

    @abc.abstractmethod
    def submit(self, request: OrderRequest) -> Order:
        """Submit an order. Raises `DuplicateClientOrderIdError` if
        `request.client_order_id` has already been submitted; the
        pre-existing order is left unchanged."""

    @abc.abstractmethod
    def cancel(self, client_order_id: str) -> Order:
        """Cancel an open order. Raises `UnknownOrderError` if the id was
        never submitted, or `OrderNotOpenError` if it is already `FILLED`
        or `CANCELLED`."""

    @abc.abstractmethod
    def positions(self) -> dict[str, Position]:
        """Net position per symbol, aggregated from fills. A symbol whose
        net quantity is zero is absent from the result."""

    @abc.abstractmethod
    def fills(self) -> list[Fill]:
        """Every fill produced so far, in submission order."""

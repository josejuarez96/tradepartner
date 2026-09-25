"""`Broker` interface (spec req 7; ADR 0003 rule 7).

Defines the order/fill/position data model and the abstract `Broker`
that every broker adapter (the in-memory `FakeBroker` here, Alpaca paper
in Phase 4, Alpaca live in Phase 6) implements. **No risk logic lives in
this module or in any adapter that implements `Broker`.** Per ADR 0003
rule 7 ("Risk rules sit outside every broker"), the core sends orders only
through a risk-gated wrapper around `Broker` (position limits, kill
switch, idempotency beyond the duplicate-`client_order_id` guard below);
that wrapper is Phase 4 work and is the *only* intended caller of
`submit`. An adapter that implements `Broker` must accept and execute
whatever valid `Order` it is given — it is not the place to reject an
order for exceeding a position limit or tripping a kill switch.

ADR 0006 cadence: every order is a **DAY** order placed for the T+1 open
(no other `time_in_force` is modelled), so `Order.time_in_force` is
restricted to the single literal `"day"`.

Every datetime here (`Order.submitted_at`, `Fill.filled_at`, and the
`since` parameter `Broker.fills` takes) is tz-aware UTC; a naive datetime
raises `ValueError` (CLAUDE.md: "Datetimes are always timezone-aware
UTC"). `Order`, `Fill` and `Position` are frozen (immutable) so a caller
holding a reference to a submitted order or a reported fill/position can
never see it mutate out from under them — an adapter that needs a new
state (e.g. filling an order) constructs a new object rather than editing
one in place, mirroring the store's "revision is a new row" rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Every status an order can carry. `FakeBroker` never produces
#: `"partially_filled"` (it fills DAY orders fully or not at all; see
#: `fake_broker.py`) or `"rejected"` (nothing in this module validates an
#: order beyond field-level shape) — both are part of the shared type
#: because a real adapter (Alpaca) can report them.
OrderStatus = Literal[
    "accepted",
    "filled",
    "partially_filled",
    "cancelled",
    "expired",
    "rejected",
]


def _require_tz_aware(value: datetime) -> datetime:
    """Return `value` unchanged, or raise if it is naive.

    Mirrors `tradepartner.store.db.ensure_tz_aware`'s check, kept local to
    this module (rather than imported from `store`) so an adapter package
    never has to import the store to satisfy its own interface — adapters
    are the boundary (ADR 0003), not a consumer of store internals.
    """
    if value.tzinfo is None:
        raise ValueError(f"must be tz-aware, got a naive datetime: {value!r}")
    return value


class Order(BaseModel):
    """An order as submitted to a `Broker`.

    `client_order_id` is the idempotency key: `Broker.submit` must reject
    a duplicate rather than resubmit or silently no-op (spec req 7).
    `qty` is a `Decimal` and fractional (Alpaca supports fractional
    shares); `limit_price` is required for `order_type="limit"` and must
    be absent for `order_type="market"`, enforced below.
    """

    model_config = ConfigDict(frozen=True)

    client_order_id: str
    security_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal = Field(gt=0)
    order_type: Literal["market", "limit"]
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["day"] = "day"
    submitted_at: datetime

    @field_validator("submitted_at")
    @classmethod
    def _validate_submitted_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)

    @model_validator(mode="after")
    def _validate_limit_price_matches_order_type(self) -> Order:
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required when order_type='limit'")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("limit_price must be omitted when order_type='market'")
        return self


class Fill(BaseModel):
    """One execution against a previously submitted order."""

    model_config = ConfigDict(frozen=True)

    client_order_id: str
    qty: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def _validate_filled_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class Position(BaseModel):
    """A broker-reported position in one security.

    `qty` may be negative: this module contains no risk logic, so nothing
    here stops a `Broker` from filling a sell order for more shares than
    are held, and the resulting position is simply short (see
    `fake_broker.py`'s `FakeBroker` docstring and the "sell more than
    held" test in `tests/adapters/test_fake_broker.py` for where that
    gets stopped — the risk-gated wrapper, not here).
    """

    model_config = ConfigDict(frozen=True)

    security_id: str
    symbol: str
    qty: Decimal
    avg_price: Decimal


class Broker(ABC):
    """Abstract broker interface. See module docstring: no implementation
    of this class may contain risk logic; the risk-gated wrapper (Phase 4)
    is the only intended caller of `submit`."""

    @abstractmethod
    def submit(self, order: Order) -> OrderStatus:
        """Submit `order`. Raises on a duplicate `client_order_id`."""

    @abstractmethod
    def cancel(self, client_order_id: str) -> OrderStatus:
        """Cancel the order named by `client_order_id`. Raises if it is no
        longer cancellable (e.g. already filled)."""

    @abstractmethod
    def positions(self) -> list[Position]:
        """Current positions, aggregated across all fills."""

    @abstractmethod
    def fills(self, since: datetime) -> list[Fill]:
        """Fills with `filled_at >= since`. `since` must be tz-aware."""

    @abstractmethod
    def order_status(self, client_order_id: str) -> OrderStatus:
        """The current status of the order named by `client_order_id`."""

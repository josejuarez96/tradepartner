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

**DAY limit-order fill rule (owner decision, T20 review round 2).** A DAY
limit order is evaluated only once, at its session's official open: it
fills at the open price if a buy's limit price is >= the open price, or a
sell's limit price is <= the open price; otherwise it expires unfilled at
that same session's close. **It never fills intraday.** `FakeBroker`
(`fake_broker.py`) implements exactly this rule via
`mark_session_open`/`.mark_session_close`, and the Phase 3 backtester and
the `bt` oracle must use the identical rule, so fills stay comparable
across the fake broker, the backtester, and eventually Alpaca paper/live.

Every datetime here (`Order.submitted_at`, `Fill.filled_at`, and the
`since` parameter `Broker.fills` takes) must be tz-aware; a naive
datetime raises `ValueError` (CLAUDE.md: "Datetimes are always
timezone-aware UTC"). A tz-aware but non-UTC value is accepted and
normalised to UTC via `require_tz_aware` below, rather than rejected —
see its docstring. `Order`, `Fill` and `Position` are frozen (immutable)
so a caller holding a reference to a submitted order or a reported
fill/position can never see it mutate out from under them — an adapter
that needs a new state (e.g. filling an order) constructs a new object
rather than editing one in place, mirroring the store's "revision is a
new row" rule.

`DuplicateOrderError`, `OrderNotFoundError` and `OrderNotCancellableError`
live here, not in a specific adapter: every `Broker` implementation must
raise them under the same conditions (see each abstract method's
docstring below), because the Phase 4 risk-gated wrapper's idempotency
and reconciliation logic depends on that contract surviving a swap from
`FakeBroker` to a real adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

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


class DuplicateOrderError(RuntimeError):
    """Raised by `Broker.submit` when `order.client_order_id` was already
    submitted to this broker. The original order and its status are left
    unchanged. Every `Broker` implementation must raise this on a
    duplicate id rather than resubmitting or silently no-op'ing."""


class OrderNotFoundError(LookupError):
    """Raised by `Broker.cancel`/`.order_status` when `client_order_id`
    names no order this broker has ever seen."""


class OrderNotCancellableError(RuntimeError):
    """Raised by `Broker.cancel` when the named order is no longer in a
    cancellable state (e.g. already filled, cancelled, expired or
    rejected)."""


def require_tz_aware(value: datetime) -> datetime:
    """Return `value` normalised to UTC, or raise `ValueError` if it has
    no well-defined UTC offset.

    Two things are rejected, both meaning "this instant is not
    unambiguously defined": a naive datetime (`tzinfo is None`), and the
    rarer case of a `tzinfo` that is present but whose `utcoffset()`
    itself returns `None` (a technically-valid but incomplete `tzinfo`
    implementation). A tz-aware datetime in a *different* zone (e.g.
    `America/New_York`) is not an error — it names a real instant — so
    it is normalised via `.astimezone(UTC)` rather than rejected,
    matching CLAUDE.md's "datetimes are always timezone-aware UTC" by
    construction instead of by refusing anything not already UTC.

    This is the single tz-aware check shared by `Order.submitted_at`,
    `Fill.filled_at`, `Broker.fills`'s `since`, and `FakeBroker`'s
    `mark_session_open`/`.mark_session_close` timestamps, so every
    adapter enforces the rule the same way.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"must be tz-aware, got a naive datetime: {value!r}")
    return value.astimezone(UTC)


def _reject_float(value: Any) -> Any:
    """Reject a raw `float` before pydantic's `Decimal` coercion would
    otherwise build a `Decimal` from the float's imprecise binary
    representation (e.g. `Decimal(0.1)` has 50+ spurious digits, unlike
    `Decimal("0.1")`) silently. Callers must pass a `Decimal`, `int`, or
    a decimal-literal `str` instead. `None` passes through unchanged (an
    absent optional field, e.g. `Order.limit_price` on a market order)."""
    if isinstance(value, float):
        # ValueError, not TypeError: pydantic only converts ValueError (and
        # AssertionError) raised inside a validator into its own
        # ValidationError; a TypeError would propagate raw past callers
        # expecting ValidationError from a failed Order/Fill/Position
        # construction.
        raise ValueError(
            f"must be a Decimal (or int/str), not a float ({value!r}): "
            "pass Decimal(...) or a string to avoid silent precision loss"
        )
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

    client_order_id: str = Field(min_length=1, max_length=128)
    security_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    qty: Decimal = Field(gt=0)
    order_type: Literal["market", "limit"]
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["day"] = "day"
    submitted_at: datetime

    @field_validator("qty", "limit_price", mode="before")
    @classmethod
    def _validate_no_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @field_validator("submitted_at")
    @classmethod
    def _validate_submitted_at(cls, value: datetime) -> datetime:
        return require_tz_aware(value)

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

    client_order_id: str = Field(min_length=1, max_length=128)
    qty: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    filled_at: datetime

    @field_validator("qty", "price", mode="before")
    @classmethod
    def _validate_no_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @field_validator("filled_at")
    @classmethod
    def _validate_filled_at(cls, value: datetime) -> datetime:
        return require_tz_aware(value)


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

    security_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    qty: Decimal
    avg_price: Decimal

    @field_validator("qty", "avg_price", mode="before")
    @classmethod
    def _validate_no_float(cls, value: Any) -> Any:
        return _reject_float(value)


class Broker(ABC):
    """Abstract broker interface. See module docstring: no implementation
    of this class may contain risk logic; the risk-gated wrapper (Phase 4)
    is the only intended caller of `submit`."""

    @abstractmethod
    def submit(self, order: Order) -> OrderStatus:
        """Submit `order`. Raises `DuplicateOrderError` if
        `order.client_order_id` was already submitted."""

    @abstractmethod
    def cancel(self, client_order_id: str) -> OrderStatus:
        """Cancel the order named by `client_order_id`. Raises
        `OrderNotFoundError` if no such order exists, or
        `OrderNotCancellableError` if it is no longer cancellable (e.g.
        already filled)."""

    @abstractmethod
    def positions(self) -> list[Position]:
        """Current positions, aggregated across all fills."""

    @abstractmethod
    def fills(self, since: datetime) -> list[Fill]:
        """Fills with `filled_at >= since`. `since` must be tz-aware (a
        naive datetime raises `ValueError`); a non-UTC tz-aware value is
        normalised to UTC (`require_tz_aware`)."""

    @abstractmethod
    def order_status(self, client_order_id: str) -> OrderStatus:
        """The current status of the order named by `client_order_id`.
        Raises `OrderNotFoundError` if no such order exists."""

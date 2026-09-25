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
raises `ValueError` at construction, and any tz-aware value that isn't
already UTC is normalized to UTC. This uses the shared
`tradepartner.timeutil.ensure_tz_aware_utc`, the single enforcement point
for that rule, so this module and `store.db` cannot drift out of sync on
what counts as valid (issue #30).

Every `symbol` field is canonicalized to upper-case ASCII at construction
(`_canonical_symbol`), so netting and reconciliation compare one case
form (issue #38). Any structure keyed by symbol (risk limits, a
reconciler) must key on the same canonical form. Case only: separator
variants such as `BRK.B` / `BRK-B` are not mapped here.
"""

from __future__ import annotations

import abc
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradepartner.timeutil import ensure_tz_aware_utc


def _validate_identifier(value: str, *, field_name: str) -> str:
    """Raise `ValueError` if `value` isn't a `str`, is empty, or differs
    from its own `.strip()` — a padded id like `"co-1 "` must not silently
    defeat dedupe or symbol matching. A non-`str` (e.g. an `int`) is checked
    explicitly, matching the other validators in this module, rather than
    left to raise `AttributeError` from `.strip()`.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{field_name} must be non-empty with no leading/trailing whitespace, got {value!r}"
        )
    return value


def _canonical_symbol(value: str) -> str:
    """Validate `value` as an identifier and return its canonical form:
    ASCII, upper case (issue #38). Without this, `"aapl"` and `"AAPL"`
    net as two positions and would not match the broker's own records in
    reconciliation. Non-ASCII is rejected because upper-casing is not a
    safe canonical form for it (`"ß".upper()` is `"SS"`; full-width
    `"\\uff21\\uff21\\uff30\\uff2c"` renders like `"AAPL"` but compares unequal).
    """
    value = _validate_identifier(value, field_name="symbol")
    if not value.isascii():
        raise ValueError(f"symbol must be ASCII, got {value!r}")
    return value.upper()


def _validate_positive_finite(value: float, *, field_name: str) -> float:
    """Raise `ValueError` unless `value` is a finite, positive, non-bool
    real number. `nan <= 0` and `inf <= 0` are both `False`, so a plain
    `value <= 0` check alone lets NaN/infinity through; `bool` is a
    subclass of `int` and must be rejected explicitly too. An `int` too
    large to convert to `float` (e.g. `10**400`) makes `math.isfinite`
    raise `OverflowError` rather than return `False`; that's caught and
    raised as the same `ValueError` as any other non-finite value.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a real number, got {value!r}")
    try:
        is_finite = math.isfinite(value)
    except OverflowError:
        is_finite = False
    if not is_finite or value <= 0:
        raise ValueError(f"{field_name} must be a positive, finite number, got {value!r}")
    return float(value)


def _coerce_side(value: Side) -> Side:
    """Return `value` unchanged if it's already a `Side`; otherwise try to
    coerce it (e.g. the plain string `"buy"`) into one, raising
    `ValueError` if it doesn't match any member. Without this, a caller
    that passes a bare string satisfies the dataclass constructor (Python
    does not enforce type hints at runtime) and `fill.side is Side.BUY`
    silently evaluates to `False` for every fill — a buy would book as a
    short sell in `positions()`.
    """
    if isinstance(value, Side):
        return value
    try:
        return Side(value)
    except ValueError as exc:
        allowed = [member.value for member in Side]
        raise ValueError(f"side must be one of {allowed}, got {value!r}") from exc


def _validate_order_fields(
    *, client_order_id: str, symbol: str, side: Side, quantity: float, price: float
) -> tuple[str, str, Side, float, float]:
    """Shared validation for the fields common to `OrderRequest`, `Order`
    and `Fill`, so the three value objects cannot drift out of sync on
    what counts as a valid quantity/price/side/identifier.
    """
    client_order_id = _validate_identifier(client_order_id, field_name="client_order_id")
    symbol = _canonical_symbol(symbol)
    side = _coerce_side(side)
    quantity = _validate_positive_finite(quantity, field_name="quantity")
    price = _validate_positive_finite(price, field_name="price")
    return client_order_id, symbol, side, quantity, price


class Side(StrEnum):
    """The side of an order, as an explicit two-value enum rather than a
    free-form string."""

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
    second order is created."""


class UnknownOrderError(Exception):
    """Raised by `cancel` (or `FakeBroker.simulate_fill`) when no order
    with the given `client_order_id` has ever been submitted."""


class OrderNotOpenError(Exception):
    """Raised by `cancel` (or `FakeBroker.simulate_fill`) when the order
    exists but is already `FILLED` or `CANCELLED` — the caller is never
    silently ignored."""


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
        client_order_id, symbol, side, quantity, price = _validate_order_fields(
            client_order_id=self.client_order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            price=self.price,
        )
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)


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
        client_order_id, symbol, side, quantity, price = _validate_order_fields(
            client_order_id=self.client_order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            price=self.price,
        )
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(
            self,
            "submitted_at",
            ensure_tz_aware_utc(self.submitted_at, field_name="submitted_at"),
        )


@dataclass(frozen=True)
class Fill:
    """One execution record. `FakeBroker` produces exactly one `Fill` per
    filled order — no partial fills."""

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    filled_at: datetime

    def __post_init__(self) -> None:
        client_order_id, symbol, side, quantity, price = _validate_order_fields(
            client_order_id=self.client_order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            price=self.price,
        )
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(
            self, "filled_at", ensure_tz_aware_utc(self.filled_at, field_name="filled_at")
        )


@dataclass(frozen=True)
class Position:
    """Net quantity held in one symbol, aggregated from fills (buy adds,
    sell subtracts). `Broker.positions()` never reports a zero-quantity
    symbol — a symbol that nets to zero is absent from the result rather
    than present with `quantity=0` (see `fake_broker.py` for where that's
    enforced). This is a net-position model only: a sell with no prior
    long books a negative (short) quantity, since no risk logic here
    prevents it (Phase 4 concern).
    """

    symbol: str
    quantity: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _canonical_symbol(self.symbol))


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
        pre-existing order is left unchanged.

        A real (non-fake) implementation must dedupe `client_order_id`
        against the broker's own persisted order state (e.g. by querying
        the broker for an existing order with that id), not just against
        this process's in-memory state — `FakeBroker` dedupes in memory
        only, which is sufficient for tests but not a model for a real
        adapter's crash-recovery behavior.
        """

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
        """Every fill produced so far, in fill order."""

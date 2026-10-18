"""`Broker` interface: the one seam order placement goes through (data spec
req 7; completed for Phase 4 by paper-trading spec req 1, #33, plan T46b).

This module defines the abstract interface and the small typed value
objects it exchanges. It has no implementation: `adapters/fake_broker.py`
and the Alpaca adapter (T48c) implement it, and neither holds risk logic:
position limits, the kill switch and reconciliation live in the risk-gated
wrapper (ADR 0003 rule 7; CLAUDE.md non-negotiable 5: "LLM output never
reaches order placement without passing deterministic risk rules").

**Orders.** An `OrderRequest` is a market DAY order sized by `notional` or
`quantity`, exactly one; it carries no price. The broker's `Order` status
is one of `OrderStatus` (`ACCEPTED`, `FILLED`, `EXPIRED`, `REJECTED`,
`CANCELLED`); `TERMINAL_STATUSES` is every status but `ACCEPTED`. A partial
fill is `ACCEPTED` with `filled_quantity` and `filled_avg_price` set. The
journal-only state `pending` lives in `order_events.status`, never here.
`cancel` is a request and returns `None`; its outcome is read back through
`get_order`. The ABC exposes no clock (ADR 0007 point 5): the wrapper and
the adapter are handed the same clock callable at composition time.

**Read-side reservations (ADR 0015 seam 3, plan T135).** `Order`, `Fill`,
`Account` and `Asset` carry fields the adapter reads back from the broker —
`order_type`, `time_in_force`, `limit_price`, `stop_price`, `asset_class`,
`order_class`, `legs`, `fee`, `short_market_value`, `maintenance_margin`,
`daytrade_count`, `shortable`, `easy_to_borrow`, `marginable` — that nothing
in this system sends: `OrderRequest` is unchanged, so no request can carry a
price, an order type or a leg (ADR 0015 seam 3). They are reserved so the
Alpaca adapter maps a broker's response once; the risk wrapper does not yet
refuse a non-default shape — that refusal (`refused_order_shape`) is T135b,
not this task.

**Non-trade cash activities (paper-trading spec req 1, the amendment of
2026-10-12, #1583, plan T183b).** `Broker.cash_activities(since)` is the read
of the broker's non-trade account activities (`FEE` a regulatory fee, `DIV` a
dividend, `SSP` a corporate action, and any type this system has not
classified). A `CashActivity` carries the broker's own activity `id` — its
identity, the only key by which an activity is counted once — a `date` with
**no time** (a fee row has only a date; inventing a time for it would be a
guess about when the broker posted it) and a signed `net_amount` (`None` for a
row that moves no cash). It never returns a fill, and it returns every row
the broker lists from `since`'s window on whatever its type — nothing is
dropped by date, a row dated before `since` included, because the caller
applies its own bound: a type nothing here has classified must reach the
reconciliation as `unexplained_activity`, not be filtered away.

Every timestamp field on these value objects is tz-aware UTC
(CLAUDE.md: "Datetimes are always timezone-aware UTC"); a naive `datetime`
raises `ValueError` at construction, and any tz-aware value that isn't
already UTC is normalized to UTC. This uses the shared
`tradepartner.timeutil.ensure_tz_aware_utc`, the single enforcement point
for that rule, so this module and `store.db` cannot drift out of sync on
what counts as valid (issue #30).

Every `symbol` field is canonicalized to upper-case ASCII at construction
(`canonical_symbol`), so netting and reconciliation compare one case
form (issue #38). Any structure keyed by symbol (risk limits, a
reconciler) must key on the same canonical form. Case only: separator
variants such as `BRK.B` / `BRK-B` are not mapped here.
"""

from __future__ import annotations

import abc
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
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


def canonical_symbol(value: str) -> str:
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


def validate_positive_finite(value: float, *, field_name: str) -> float:
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


def _validate_optional_positive(value: float | None, *, field_name: str) -> float | None:
    return None if value is None else validate_positive_finite(value, field_name=field_name)


def _validate_finite(value: float, *, field_name: str) -> float:
    """Raise `ValueError` unless `value` is a finite, non-bool real number
    (any sign: account cash can be negative)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a real number, got {value!r}")
    try:
        is_finite = math.isfinite(value)
    except OverflowError:
        is_finite = False
    if not is_finite:
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _validate_bool(value: bool, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool, got {value!r}")
    return value


def _validate_optional_finite(value: float | None, *, field_name: str) -> float | None:
    return None if value is None else _validate_finite(value, field_name=field_name)


def _validate_optional_non_negative_finite(value: float | None, *, field_name: str) -> float | None:
    """`_validate_optional_finite` that also refuses a negative value (a
    broker fee is never negative)."""
    if value is None:
        return None
    value = _validate_finite(value, field_name=field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value!r}")
    return value


def _validate_optional_count(value: int | None, *, field_name: str) -> int | None:
    """`None` or a non-negative `int` (never a `bool`, an `int` subclass)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int or None, got {value!r}")
    return value


def _validate_optional_identifier(value: str | None, *, field_name: str) -> str | None:
    """`_validate_identifier` for a nullable field: `None` stays `None`."""
    return None if value is None else _validate_identifier(value, field_name=field_name)


def _validate_optional_text(value: str | None, *, field_name: str) -> str | None:
    """`None` or a `str`, with no rule about its content (a broker's
    `description` is free text, punctuation and spaces included)."""
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field_name} must be a str or None, got {value!r}")
    return value


def _validate_date(value: date, *, field_name: str) -> date:
    """`value` must be a calendar `date`. A `datetime` is refused explicitly
    (it subclasses `date`, so `isinstance` alone would pass it): a fee row has
    no time, and a time supplied here would be an invented one."""
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{field_name} must be a date, got {value!r}")
    return value


def _validate_legs(value: tuple[Order, ...], *, field_name: str) -> tuple[Order, ...]:
    """A tuple of `Order` legs. Refuses any non-tuple (including a list),
    so the frozen field stays hashable. Each leg was already validated when
    it was built; this only pins the shape."""
    if not isinstance(value, tuple) or not all(isinstance(leg, Order) for leg in value):
        raise ValueError(f"{field_name} must be a tuple of Order, got {value!r}")
    return value


def _validate_size(
    notional: float | None, quantity: float | None
) -> tuple[float | None, float | None]:
    """Exactly one of `notional` and `quantity`, each positive and finite."""
    if (notional is None) == (quantity is None):
        raise ValueError(
            f"exactly one of notional and quantity is required, got "
            f"notional={notional!r}, quantity={quantity!r}"
        )
    return (
        _validate_optional_positive(notional, field_name="notional"),
        _validate_optional_positive(quantity, field_name="quantity"),
    )


def _coerce_status(value: OrderStatus) -> OrderStatus:
    """`_coerce_side`'s rule for `OrderStatus`."""
    if isinstance(value, OrderStatus):
        return value
    try:
        return OrderStatus(value)
    except ValueError as exc:
        allowed = [member.value for member in OrderStatus]
        raise ValueError(f"status must be one of {allowed}, got {value!r}") from exc


class Side(StrEnum):
    """The side of an order, as an explicit two-value enum rather than a
    free-form string."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """The broker's view of an order (spec req 1). A partial fill is
    `ACCEPTED` with `filled_quantity > 0`."""

    ACCEPTED = "accepted"
    FILLED = "filled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


#: Every status an order never leaves.
TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
)


class DuplicateClientOrderIdError(Exception):
    """Raised when `submit` is called with a `client_order_id` that has
    already been submitted. The original order is left unchanged and no
    second order is created."""


class UnknownOrderError(Exception):
    """Raised by `get_order` and `cancel` (and `FakeBroker.simulate_fill`)
    when the broker has no order with the given `client_order_id`."""


class OrderNotOpenError(Exception):
    """Raised by `cancel` (or `FakeBroker.simulate_fill`) when the order
    exists but is already in a terminal status; the caller is never
    silently ignored."""


@dataclass(frozen=True)
class OrderRequest:
    """What a caller passes to `Broker.submit`: a market DAY order for
    `notional` dollars or `quantity` shares, exactly one."""

    client_order_id: str
    symbol: str
    side: Side
    notional: float | None = None
    quantity: float | None = None

    def __post_init__(self) -> None:
        notional, quantity = _validate_size(self.notional, self.quantity)
        object.__setattr__(
            self,
            "client_order_id",
            _validate_identifier(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "side", _coerce_side(self.side))
        object.__setattr__(self, "notional", notional)
        object.__setattr__(self, "quantity", quantity)


@dataclass(frozen=True)
class Order:
    """The broker's record of a submitted order, as returned by `submit`
    and `get_order`. `broker_order_id`, `filled_quantity`,
    `filled_avg_price` and `filled_at` are `None` until the broker sets
    them; the two fill amounts come together, and a `FILLED` order carries
    all three fill fields. **Nothing filled is `None`, never zero:** an
    adapter maps a broker's `filled_qty = 0` (Alpaca reports it on every
    unfilled order) to `filled_quantity = filled_avg_price = None`, so
    `filled_quantity`, when set, is always positive."""

    client_order_id: str
    symbol: str
    side: Side
    notional: float | None
    quantity: float | None
    status: OrderStatus
    submitted_at: datetime
    broker_order_id: str | None = None
    filled_quantity: float | None = None
    filled_avg_price: float | None = None
    filled_at: datetime | None = None
    order_type: str = "market"
    time_in_force: str = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    asset_class: str = "us_equity"
    order_class: str = "simple"
    legs: tuple[Order, ...] = ()

    def __post_init__(self) -> None:
        notional, quantity = _validate_size(self.notional, self.quantity)
        status = _coerce_status(self.status)
        filled_quantity = _validate_optional_positive(
            self.filled_quantity, field_name="filled_quantity"
        )
        filled_avg_price = _validate_optional_positive(
            self.filled_avg_price, field_name="filled_avg_price"
        )
        if (filled_quantity is None) != (filled_avg_price is None):
            raise ValueError("filled_quantity and filled_avg_price are set together or not at all")
        filled_at = (
            None
            if self.filled_at is None
            else ensure_tz_aware_utc(self.filled_at, field_name="filled_at")
        )
        if status is OrderStatus.FILLED and (filled_quantity is None or filled_at is None):
            raise ValueError("a FILLED order needs filled_quantity, filled_avg_price and filled_at")
        broker_order_id = (
            None
            if self.broker_order_id is None
            else _validate_identifier(self.broker_order_id, field_name="broker_order_id")
        )
        order_type = _validate_identifier(self.order_type, field_name="order_type")
        time_in_force = _validate_identifier(self.time_in_force, field_name="time_in_force")
        limit_price = _validate_optional_positive(self.limit_price, field_name="limit_price")
        stop_price = _validate_optional_positive(self.stop_price, field_name="stop_price")
        asset_class = _validate_identifier(self.asset_class, field_name="asset_class")
        order_class = _validate_identifier(self.order_class, field_name="order_class")
        legs = _validate_legs(self.legs, field_name="legs")
        object.__setattr__(
            self,
            "client_order_id",
            _validate_identifier(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "side", _coerce_side(self.side))
        object.__setattr__(self, "notional", notional)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "submitted_at",
            ensure_tz_aware_utc(self.submitted_at, field_name="submitted_at"),
        )
        object.__setattr__(self, "broker_order_id", broker_order_id)
        object.__setattr__(self, "filled_quantity", filled_quantity)
        object.__setattr__(self, "filled_avg_price", filled_avg_price)
        object.__setattr__(self, "filled_at", filled_at)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "time_in_force", time_in_force)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "stop_price", stop_price)
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "order_class", order_class)
        object.__setattr__(self, "legs", legs)


@dataclass(frozen=True)
class Fill:
    """One execution record, identified by the broker's `broker_fill_id`.
    `fee` is `None` until an adapter derives it from Alpaca's account
    activities (ADR 0015 seam 3); it is not a field of the fill itself."""

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    filled_at: datetime
    broker_fill_id: str
    fee: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_order_id",
            _validate_identifier(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "side", _coerce_side(self.side))
        object.__setattr__(
            self, "quantity", validate_positive_finite(self.quantity, field_name="quantity")
        )
        object.__setattr__(self, "price", validate_positive_finite(self.price, field_name="price"))
        object.__setattr__(
            self, "filled_at", ensure_tz_aware_utc(self.filled_at, field_name="filled_at")
        )
        object.__setattr__(
            self,
            "broker_fill_id",
            _validate_identifier(self.broker_fill_id, field_name="broker_fill_id"),
        )
        object.__setattr__(
            self, "fee", _validate_optional_non_negative_finite(self.fee, field_name="fee")
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
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))


@dataclass(frozen=True)
class Account:
    """The broker account at `as_of`: cash (may be negative), buying
    power and equity, in dollars. `short_market_value`,
    `maintenance_margin` and `daytrade_count` are read-side reservations
    (ADR 0015 seam 3): `None` until an adapter maps them, finite when set."""

    account_id: str
    cash: float
    buying_power: float
    equity: float
    as_of: datetime
    short_market_value: float | None = None
    maintenance_margin: float | None = None
    daytrade_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_id", _validate_identifier(self.account_id, field_name="account_id")
        )
        for name in ("cash", "buying_power", "equity"):
            object.__setattr__(self, name, _validate_finite(getattr(self, name), field_name=name))
        object.__setattr__(self, "as_of", ensure_tz_aware_utc(self.as_of, field_name="as_of"))
        object.__setattr__(
            self,
            "short_market_value",
            _validate_optional_finite(self.short_market_value, field_name="short_market_value"),
        )
        object.__setattr__(
            self,
            "maintenance_margin",
            _validate_optional_finite(self.maintenance_margin, field_name="maintenance_margin"),
        )
        object.__setattr__(
            self,
            "daytrade_count",
            _validate_optional_count(self.daytrade_count, field_name="daytrade_count"),
        )


@dataclass(frozen=True)
class CashActivity:
    """One non-trade account activity the broker lists (paper-trading spec
    req 1, its amendment of 2026-10-12 item 1, #1583, plan T183b).

    `activity_id` is the broker's own activity id and the row's **identity**:
    an activity is counted once by it, never by its date or its time, because
    the evidence says nothing about how long after its trade date a fee posts.
    `activity_type` is the broker's type string, passed through as reported
    (`FEE`, `DIV`, `SSP`, `INT`, `JNLC`, …): **nothing here classifies a
    type**, so a type this system has not seen still reaches the caller, which
    is the point of the read. `date` is the activity's New York **date**, with
    no time of any kind — a `FEE` row has only a date (spec item 1), and an
    `event_at` field does not exist.

    `status` is the broker's own (`executed` for a posted fee) and is
    **required**: a rule judges a row by it, so a row built without a status
    must not be constructible. `net_amount` is the broker's signed amount
    (`None` for a row that moves no cash, such as a split's `SSP`), `symbol`
    the row's symbol when it names one (canonicalized like every other symbol
    here) and `description` the broker's free text. Neither `symbol` nor
    `description` is interpreted: a rule that needed them would be guessing
    from a string (spec item 2).
    """

    activity_id: str
    activity_type: str
    date: date
    status: str
    net_amount: float | None = None
    symbol: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "activity_id", _validate_identifier(self.activity_id, field_name="activity_id")
        )
        object.__setattr__(
            self,
            "activity_type",
            _validate_identifier(self.activity_type, field_name="activity_type"),
        )
        object.__setattr__(self, "date", _validate_date(self.date, field_name="date"))
        object.__setattr__(self, "status", _validate_identifier(self.status, field_name="status"))
        object.__setattr__(
            self, "net_amount", _validate_optional_finite(self.net_amount, field_name="net_amount")
        )
        object.__setattr__(
            self, "symbol", None if self.symbol is None else canonical_symbol(self.symbol)
        )
        object.__setattr__(
            self, "description", _validate_optional_text(self.description, field_name="description")
        )


@dataclass(frozen=True)
class Asset:
    """What the broker says about one symbol. `cusip` is `None` when the
    broker does not report one. `shortable`, `easy_to_borrow` and
    `marginable` are read-side reservations (ADR 0015 seam 3), `False`
    until an adapter maps them."""

    tradable: bool
    fractionable: bool
    status: str
    cusip: str | None
    shortable: bool = False
    easy_to_borrow: bool = False
    marginable: bool = False

    def __post_init__(self) -> None:
        _validate_bool(self.tradable, field_name="tradable")
        _validate_bool(self.fractionable, field_name="fractionable")
        _validate_identifier(self.status, field_name="status")
        if self.cusip is not None:
            _validate_identifier(self.cusip, field_name="cusip")
        _validate_bool(self.shortable, field_name="shortable")
        _validate_bool(self.easy_to_borrow, field_name="easy_to_borrow")
        _validate_bool(self.marginable, field_name="marginable")


#: The `Asset` of a symbol the broker does not know, or of a listing whose
#: ticker has no broker form (#1729): never tradable, so a buy of it is a
#: `skip_untradable` before any order and the run goes on, never a halt.
UNRESOLVED_ASSET = Asset(tradable=False, fractionable=False, status="unresolved", cusip=None)


class Broker(abc.ABC):
    """Abstract order-placement interface (paper-trading spec req 1).

    Instantiating this class directly raises `TypeError` (it declares
    abstract methods). It has no clock member (ADR 0007 point 5).
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
    def cancel(self, client_order_id: str) -> None:
        """Request cancellation of a non-terminal order; read the outcome
        back through `get_order`. Raises `UnknownOrderError` if the broker
        has no such order, or `OrderNotOpenError` if it is terminal."""

    @abc.abstractmethod
    def get_order(self, client_order_id: str) -> Order:
        """The broker's current record of the order. Raises
        `UnknownOrderError` when the broker has no such order (ADR 0007
        point 3's fetch)."""

    @abc.abstractmethod
    def open_orders(self) -> list[Order]:
        """Every order whose status is not in `TERMINAL_STATUSES`."""

    @abc.abstractmethod
    def fills(self, since: datetime | None = None) -> list[Fill]:
        """Every fill with `filled_at >= since` (all when `None`), in fill
        order."""

    @abc.abstractmethod
    def cash_activities(self, since: date) -> list[CashActivity]:
        """Every **non-trade** activity the broker lists, oldest first, and
        never a fill (paper-trading spec req 1, its amendment of 2026-10-12
        item 1, #1583, plan T183b).

        The broker is asked from the New York midnight that begins `since -
        1 day` (an adapter's `after` is a strict timestamp comparison and how
        it orders a date-only row is unknown), so the read's window starts a
        day early and a row dated **before** `since` can come back. Nothing is
        dropped by date or by type: a row dated exactly `since` is included, a
        row dated before it is returned as the broker listed it, and **the
        caller applies its own bound**. A type the caller has not classified
        is returned like any other, so the caller can halt on it rather than
        silently ignore a cash movement.
        """

    @abc.abstractmethod
    def positions(self) -> dict[str, Position]:
        """Net position per symbol, aggregated from fills. A symbol whose
        net quantity is zero is absent from the result."""

    @abc.abstractmethod
    def account(self) -> Account:
        """The account's cash, buying power and equity now."""

    @abc.abstractmethod
    def assets(self, symbols: Sequence[str]) -> dict[str, Asset]:
        """The broker's `Asset` per requested symbol, keyed by canonical
        symbol."""

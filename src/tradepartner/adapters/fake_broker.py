"""In-memory fake `Broker` (data spec req 7; the Phase 4 types from
paper-trading spec req 1, plan T46b).

**No risk logic lives here.** No position limits, no kill switch, no order
sizing, no reconciliation: those live in the risk-gated wrapper (ADR 0003
rule 7). `FakeBroker` exists so the rest of the system has something to
submit orders to without a real broker; it does no I/O, no network calls,
and spawns no threads. It models **net** positions only: a sell with no
prior long is accepted and books a negative (short) quantity; nothing here
prevents or limits that.

**Fill mechanism (documented choice).** An order fills at `price_of(symbol)`,
the price function injected at construction, read when the order fills;
there is no market simulation, no slippage and no costs. A `notional` order
fills `notional / price` shares. By default (`auto_fill=True`, kept for
existing callers) `submit` fills the order immediately, in the same call.
With `auto_fill=False` the order stays `ACCEPTED` after `submit`, so a
caller can `cancel` it; `simulate_fill(client_order_id)` fills its full
quantity. There is no partial fill here (T46c scripts those).
`simulate_fill` is a `FakeBroker`-only escape hatch, not part of the
`Broker` interface. The fill's price is read, and validated by building the
`Fill`, before anything is recorded, so a bad price changes no state.

**Account.** `cash` starts at the constructor's value and moves by
`quantity * price` per fill (a buy debits, a sell credits), exact in
`Decimal`; `buying_power` equals `cash`; `equity` is cash plus every
position marked at `price_of`. `assets` answers every symbol with the
constructor's `Asset` for it, or a tradable, fractionable, `active` asset
with no CUSIP.

**Clock.** All timestamps come from an injectable `clock: Callable[[],
datetime]` supplied at construction and exposed as `.clock` (the wrapper's
identity test, ADR 0007 point 5), never from `datetime.now()` called
internally, so tests are deterministic (CLAUDE.md: "Datetimes are always
timezone-aware UTC"). Every clock call is wrapped (ADR 0007 point 4): the
reading goes straight through `ensure_tz_aware_utc(..., field_name="clock")`,
and any exception from the call or the validation (the clock raising, a
non-`datetime`, a naive value, a UTC overflow) becomes `ClockError` chained
from the original, before any `Order` or `Fill` is built and before any state
changes. A `BaseException` such as `KeyboardInterrupt` propagates untouched.
`ClockError` is a `SystemFaultError`, never a `ValueError`, so the Phase 4
wrapper cannot mistake a broken clock for a rejected order.

**Position netting.** Net quantity per symbol is accumulated internally as
`decimal.Decimal` (via `Decimal(repr(fill.quantity))`) rather than `float`,
so a sequence like +0.1, +0.2, -0.3 nets to exactly zero instead of a
`float` rounding artifact (~5e-17) that would leave a phantom position
behind; the running total is converted back to `float` only when building
the `Position` returned to callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    Account,
    Asset,
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
    canonical_symbol,
)
from tradepartner.errors import ClockError
from tradepartner.timeutil import ensure_tz_aware_utc

_DEFAULT_ASSET = Asset(tradable=True, fractionable=True, status="active", cusip=None)


class FakeBroker(Broker):
    """In-memory `Broker` for tests and local research runs. See the
    module docstring for the fill mechanism, the account, position-netting
    precision, and the explicit absence of risk logic."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        price_of: Callable[[str], float],
        auto_fill: bool = True,
        cash: float = 100_000.0,
        account_id: str = "fake-account",
        assets: Mapping[str, Asset] | None = None,
    ) -> None:
        self.clock = clock
        self._price_of = price_of
        self._auto_fill = auto_fill
        self._cash = Decimal(repr(float(cash)))
        self._account_id = account_id
        self._assets = {canonical_symbol(s): a for s, a in (assets or {}).items()}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._net_quantity: dict[str, Decimal] = {}
        self._orders_submitted = 0

    def submit(self, request: OrderRequest) -> Order:
        if request.client_order_id in self._orders:
            raise DuplicateClientOrderIdError(
                f"client_order_id already submitted: {request.client_order_id!r}"
            )

        submitted_at = self._now()
        order = Order(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            notional=request.notional,
            quantity=request.quantity,
            status=OrderStatus.ACCEPTED,
            submitted_at=submitted_at,
            broker_order_id=f"fake-order-{self._orders_submitted + 1}",
        )
        if self._auto_fill:
            filled, fill = self._fill_of(order, submitted_at)
            self._orders_submitted += 1
            self._record_fill(filled, fill)
            return filled
        self._orders_submitted += 1
        self._orders[request.client_order_id] = order
        return order

    def cancel(self, client_order_id: str) -> None:
        order = self._require_order(client_order_id)
        if order.status in TERMINAL_STATUSES:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {order.status.value}, cannot cancel"
            )
        self._orders[client_order_id] = replace(order, status=OrderStatus.CANCELLED)

    def get_order(self, client_order_id: str) -> Order:
        return self._require_order(client_order_id)

    def open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status not in TERMINAL_STATUSES]

    def simulate_fill(self, client_order_id: str) -> Order:
        """Fill a non-terminal order's full quantity at `price_of(symbol)`,
        timestamped by the injected clock. Not part of the `Broker`
        interface: a `FakeBroker`-specific escape hatch for tests that
        submit with `auto_fill=False` (see module docstring)."""
        filled_at = self._now()
        order = self._require_order(client_order_id)
        if order.status in TERMINAL_STATUSES:
            raise OrderNotOpenError(
                f"order {client_order_id!r} is {order.status.value}, cannot fill"
            )
        filled, fill = self._fill_of(order, filled_at)
        self._record_fill(filled, fill)
        return filled

    def _now(self) -> datetime:
        """One clock reading, tz-aware UTC, or `ClockError` (module docstring)."""
        try:
            reading = self.clock()
            if not isinstance(reading, datetime):
                # `ensure_tz_aware_utc` duck-types; an object with `tzinfo`,
                # `utcoffset` and `astimezone` would pass it.
                raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
            return ensure_tz_aware_utc(reading, field_name="clock")
        except Exception as exc:
            # The type only: the chained cause keeps the detail, and a
            # broker-sourced clock's message could carry request details.
            raise ClockError(f"clock failed: {type(exc).__name__}") from exc

    def _fill_of(self, order: Order, filled_at: datetime) -> tuple[Order, Fill]:
        """The filled order and its fill, built (and so validated) without
        touching any state. A non-positive or non-finite price fails the
        `Fill`'s own check."""
        price = self._price_of(order.symbol)
        if order.quantity is not None:
            quantity = order.quantity
        else:
            assert order.notional is not None  # `Order` holds exactly one
            quantity = order.notional / price if price > 0 else price
        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            filled_at=filled_at,
            broker_fill_id=f"fake-fill-{len(self._fills) + 1}",
        )
        filled = replace(
            order,
            status=OrderStatus.FILLED,
            filled_quantity=fill.quantity,
            filled_avg_price=fill.price,
            filled_at=filled_at,
        )
        return filled, fill

    def _record_fill(self, filled: Order, fill: Fill) -> None:
        self._orders[filled.client_order_id] = filled
        self._fills.append(fill)
        signed = _signed(fill.side, Decimal(repr(fill.quantity)))
        self._net_quantity[fill.symbol] = self._net_quantity.get(fill.symbol, Decimal(0)) + signed
        self._cash -= signed * Decimal(repr(fill.price))

    def fills(self, since: datetime | None = None) -> list[Fill]:
        if since is None:
            return list(self._fills)
        since = ensure_tz_aware_utc(since, field_name="since")
        return [fill for fill in self._fills if fill.filled_at >= since]

    def positions(self) -> dict[str, Position]:
        return {
            symbol: Position(symbol=symbol, quantity=float(net))
            for symbol, net in self._net_quantity.items()
            if net != 0
        }

    def account(self) -> Account:
        as_of = self._now()
        marked = sum(
            (
                net * Decimal(repr(float(self._price_of(symbol))))
                for symbol, net in self._net_quantity.items()
                if net != 0
            ),
            Decimal(0),
        )
        cash = float(self._cash)
        return Account(
            account_id=self._account_id,
            cash=cash,
            buying_power=cash,
            equity=float(self._cash + marked),
            as_of=as_of,
        )

    def assets(self, symbols: Sequence[str]) -> dict[str, Asset]:
        canonical = [canonical_symbol(symbol) for symbol in symbols]
        return {symbol: self._assets.get(symbol, _DEFAULT_ASSET) for symbol in canonical}

    def _require_order(self, client_order_id: str) -> Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise UnknownOrderError(f"unknown client_order_id: {client_order_id!r}")
        return order


def _signed(side: Side, amount: Decimal) -> Decimal:
    """`amount` for a buy, `-amount` for a sell."""
    if side is Side.BUY:
        return amount
    if side is Side.SELL:
        return -amount
    raise ValueError(f"unexpected side: {side!r}")  # pragma: no cover - Side is validated

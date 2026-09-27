"""In-memory fake `Broker` (data spec req 7; the Phase 4 types from
paper-trading spec req 1, plan T46b; scripting for the wrapper tests, T46c).

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
quantity. `simulate_fill` is a `FakeBroker`-only escape hatch, not part of
the `Broker` interface. The fill's price is read, and validated by building the
`Fill`, before anything is recorded, so a bad price changes no state.

**Scripting (T46c).** A test tells the fake what happens to an order,
either for a given `client_order_id` or for the next `submit` (FIFO, one
`script` call per future submit; an id's script wins over the queue):
`Accept`, `FillAt(price)`, `PartialFill(quantity, avg_price)`, `Expire`,
`Reject`, `Vanish` (`submit` returns the accepted order, then the book
forgets it: `get_order` and `cancel` raise `UnknownOrderError`, and the id
is free again), `TransportFault` (raises before the book records the order,
or after it with `after_record=True`), and `HoldCancel` (a `cancel` leaves
the order `ACCEPTED`, Alpaca's `pending_cancel`, optionally after a partial
fill, until `complete_cancel`). With no script the constructor's default
applies (`auto_fill`). `apply(client_order_id, instruction)` does the same
to an order already on the book (scripting an id already on the book
raises: use `apply`). Partial fills accumulate: the order's
`filled_quantity` is their exact sum and `filled_avg_price` the
quantity-weighted average; `filled_at` stays `None` until the order is
`FILLED`, as on Alpaca. A partial fill that reaches a `quantity` order's
size fills it and one beyond it raises; a `notional` order's partials are
not checked against its size. A filled order cannot `Vanish` (a broker
cannot forget an order it filled); an unfilled one that vanishes frees its
id. A script is consumed only when the `submit` it governs gets past the
clock read and builds its order, so a `ClockError` or a bad price leaves
it queued; once a fault consumes an id's script, a retry of that id takes
the next-submit queue's head, like any unscripted id. Scripting calls are
not `Broker` methods: they are not logged and read no clock unless they
fill.

**Account.** `cash` starts at the constructor's value and moves by
`quantity * price` per fill (a buy debits, a sell credits), exact in
`Decimal`, or rounded half-up to the cent per fill with
`round_cash_to_cent=True`; `buying_power` equals `cash` unless set apart
(`buying_power=` or `set_buying_power`), when it is that fixed value;
`equity` is cash plus every position marked at `price_of`. `assets` answers
every symbol with its `Asset` for the session (the clock's New York date,
read only for a symbol with a dated schedule, `set_asset(...,
from_session=)`), or a tradable, fractionable, `active` asset with no CUSIP.

**Lagging fills, hook and log.** After `lag_fills(n)`, each fill recorded is
missing from the next `n` calls of `fills()` and delivered from the one
after (`None`: never delivered), while `get_order` already shows it.
`on_submit(request)` runs at the start of every `submit`, before anything is
checked or recorded, so a test can assert the store state at submit time.
`calls` is every `Broker` method called, in order, with its arguments as
passed.

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

import copy
import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

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
    validate_positive_finite,
)
from tradepartner.errors import ClockError
from tradepartner.timeutil import ensure_tz_aware_utc

_DEFAULT_ASSET = Asset(tradable=True, fractionable=True, status="active", cusip=None)
_NEW_YORK = ZoneInfo("America/New_York")
_CENT = Decimal("0.01")


class FakeTransportError(ConnectionError):
    """The error a scripted `TransportFault` raises unless given another."""


@dataclass(frozen=True)
class Accept:
    """The order stays `ACCEPTED`."""


@dataclass(frozen=True)
class FillAt:
    """Fill the order's remainder at `price` (`price_of(symbol)` when `None`)."""

    price: float | None = None

    def __post_init__(self) -> None:
        if self.price is not None:
            validate_positive_finite(self.price, field_name="price")


@dataclass(frozen=True)
class PartialFill:
    """One fill of `quantity` at `avg_price`; the order stays `ACCEPTED`."""

    quantity: float
    avg_price: float

    def __post_init__(self) -> None:
        validate_positive_finite(self.quantity, field_name="quantity")
        validate_positive_finite(self.avg_price, field_name="avg_price")


@dataclass(frozen=True)
class Expire:
    """The order becomes `EXPIRED`, keeping any fills."""


@dataclass(frozen=True)
class Reject:
    """The order becomes `REJECTED`, keeping any fills."""


@dataclass(frozen=True)
class Vanish:
    """The book forgets the order (after `submit` returns it)."""


@dataclass(frozen=True)
class TransportFault:
    """`submit` raises `error` (a `FakeTransportError` when `None`), before
    the book records the order or, with `after_record`, after it records it
    `ACCEPTED`."""

    after_record: bool = False
    error: Exception | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.after_record, bool):
            raise ValueError(f"after_record must be a bool, got {self.after_record!r}")


@dataclass(frozen=True)
class HoldCancel:
    """A `cancel` leaves the order `ACCEPTED` (`pending_cancel`), after
    applying `fill` if given, until `complete_cancel`."""

    fill: PartialFill | None = None


SubmitOutcome = Accept | FillAt | PartialFill | Expire | Reject | Vanish | TransportFault
BookInstruction = FillAt | PartialFill | Expire | Reject | Vanish | HoldCancel
Instruction = SubmitOutcome | HoldCancel
_SUBMIT_OUTCOMES = (Accept, FillAt, PartialFill, Expire, Reject, Vanish, TransportFault)
_BOOK_INSTRUCTIONS = (FillAt, PartialFill, Expire, Reject, Vanish, HoldCancel)


@dataclass(frozen=True)
class BrokerCall:
    """One `Broker` method call as the fake received it."""

    method: str
    args: tuple[object, ...]


@dataclass(frozen=True)
class _Script:
    outcome: SubmitOutcome | None
    hold: HoldCancel | None


class FakeBroker(Broker):
    """In-memory `Broker` for tests and local research runs. See the
    module docstring for the fill mechanism, scripting, the account,
    position-netting precision, and the explicit absence of risk logic."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        price_of: Callable[[str], float],
        auto_fill: bool = True,
        cash: float = 100_000.0,
        account_id: str = "fake-account",
        assets: Mapping[str, Asset] | None = None,
        buying_power: float | None = None,
        round_cash_to_cent: bool = False,
        on_submit: Callable[[OrderRequest], None] | None = None,
    ) -> None:
        self.clock = clock
        self.on_submit = on_submit
        self._price_of = price_of
        self._auto_fill = auto_fill
        self._cash = Decimal(repr(float(cash)))
        self._account_id = account_id
        self._round_cash = round_cash_to_cent
        self._buying_power: float | None = None
        self.set_buying_power(buying_power)
        self._assets: dict[str, list[tuple[date, Asset]]] = {}
        for symbol, asset in (assets or {}).items():
            self.set_asset(symbol, asset)
        self._orders: dict[str, Order] = {}
        self._filled: dict[str, tuple[Decimal, Decimal]] = {}  # (quantity, notional)
        self._fills: list[Fill] = []
        self._fill_hidden_reads: list[int | None] = []
        self._fill_lag: int | None = 0
        self._net_quantity: dict[str, Decimal] = {}
        self._orders_submitted = 0
        self._scripted_by_id: dict[str, _Script] = {}
        self._scripted_next: deque[_Script] = deque()
        self._cancel_holds: dict[str, HoldCancel] = {}
        self._cancel_pending: set[str] = set()
        self._calls: list[BrokerCall] = []

    # --- scripting (not `Broker` methods) --------------------------------

    def script(self, *instructions: Instruction, client_order_id: str | None = None) -> None:
        """Script one order: at most one submit outcome and at most one
        `HoldCancel`, for `client_order_id` or else for the next unscripted
        `submit` (module docstring)."""
        outcomes = [i for i in instructions if isinstance(i, _SUBMIT_OUTCOMES)]
        holds = [i for i in instructions if isinstance(i, HoldCancel)]
        if not instructions or len(outcomes) + len(holds) != len(instructions):
            raise ValueError(f"script needs instructions, got {instructions!r}")
        if len(outcomes) > 1 or len(holds) > 1:
            raise ValueError("script takes at most one outcome and one HoldCancel per order")
        entry = _Script(outcome=outcomes[0] if outcomes else None, hold=holds[0] if holds else None)
        if client_order_id is None:
            self._scripted_next.append(entry)
        elif client_order_id in self._orders:
            raise ValueError(f"{client_order_id!r} is on the book already: use apply")
        else:
            self._scripted_by_id[client_order_id] = entry

    def apply(self, client_order_id: str, instruction: BookInstruction) -> None:
        """Apply `instruction` to an order already on the book. Raises
        `UnknownOrderError` for an unknown id and `OrderNotOpenError` for a
        terminal order (`Vanish` excepted)."""
        if not isinstance(instruction, _BOOK_INSTRUCTIONS):
            raise ValueError(f"not an instruction for an order on the book: {instruction!r}")
        order = self._require_order(client_order_id)
        if isinstance(instruction, Vanish):
            if client_order_id in self._filled:
                raise ValueError(f"{client_order_id!r} has fills: a broker cannot forget it")
            self._forget(client_order_id)
            return
        self._require_open(order, "change")
        if isinstance(instruction, HoldCancel):
            self._cancel_holds[client_order_id] = instruction
        elif isinstance(instruction, Expire | Reject):
            status = (
                OrderStatus.EXPIRED if isinstance(instruction, Expire) else OrderStatus.REJECTED
            )
            self._set_order(replace(order, status=status))
        else:
            self._fill(order, instruction, self._now())

    def complete_cancel(self, client_order_id: str) -> None:
        """Complete a held cancel: the order becomes `CANCELLED`, keeping
        its fills. Raises `ValueError` when no cancel is pending."""
        order = self._require_order(client_order_id)
        self._require_open(order, "cancel")
        if client_order_id not in self._cancel_pending:
            raise ValueError(f"no cancel pending for {client_order_id!r}")
        self._set_order(replace(order, status=OrderStatus.CANCELLED))

    def set_buying_power(self, value: float | None) -> None:
        """Report `value` as buying power from now on (`None`: equal to cash)."""
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"buying_power must be a finite number, got {value!r}")
        self._buying_power = None if value is None else float(value)

    def lag_fills(self, reads: int | None) -> None:
        """Hide each fill recorded from now on from the next `reads` calls
        of `fills()` (`None`: never delivered)."""
        if reads is not None and (
            isinstance(reads, bool) or not isinstance(reads, int) or reads < 0
        ):
            raise ValueError(f"reads must be a non-negative int or None, got {reads!r}")
        self._fill_lag = reads

    def set_asset(self, symbol: str, asset: Asset, *, from_session: date | None = None) -> None:
        """Answer `asset` for `symbol` from `from_session` on (a New York
        date), or for every session when `None`."""
        if not isinstance(asset, Asset):
            raise TypeError(f"assets values must be Asset, got {type(asset).__name__}")
        key = canonical_symbol(symbol)
        if from_session is None:
            self._assets[key] = [(date.min, asset)]
            return
        if isinstance(from_session, datetime) or not isinstance(from_session, date):
            raise TypeError(f"from_session must be a date, got {from_session!r}")
        current = self._assets.get(key, [(date.min, _DEFAULT_ASSET)])
        schedule = [e for e in current if e[0] != from_session] + [(from_session, asset)]
        self._assets[key] = sorted(schedule, key=lambda entry: entry[0])

    @property
    def calls(self) -> tuple[BrokerCall, ...]:
        """Every `Broker` method called so far, in order."""
        return tuple(self._calls)

    # --- `Broker` ---------------------------------------------------------

    def submit(self, request: OrderRequest) -> Order:
        self._log("submit", request)
        if self.on_submit is not None:
            self.on_submit(request)
        cid = request.client_order_id
        if cid in self._orders:
            raise DuplicateClientOrderIdError(f"client_order_id already submitted: {cid!r}")

        submitted_at = self._now()
        script = self._scripted_by_id.get(cid) or (
            self._scripted_next[0] if self._scripted_next else None
        )
        outcome = script.outcome if script is not None else None
        if outcome is None:
            outcome = FillAt() if self._auto_fill else Accept()
        if isinstance(outcome, TransportFault) and not outcome.after_record:
            self._consume(cid, script)
            raise _transport_error(outcome, f"transport error submitting {cid!r}")

        order = Order(
            client_order_id=cid,
            symbol=request.symbol,
            side=request.side,
            notional=request.notional,
            quantity=request.quantity,
            status=OrderStatus.ACCEPTED,
            submitted_at=submitted_at,
            broker_order_id=f"fake-order-{self._orders_submitted + 1}",
        )
        fill: tuple[Order, Fill] | None = None
        if isinstance(outcome, FillAt | PartialFill):
            fill = self._fill_of(order, outcome, submitted_at)
        elif isinstance(outcome, Expire):
            order = replace(order, status=OrderStatus.EXPIRED)
        elif isinstance(outcome, Reject):
            order = replace(order, status=OrderStatus.REJECTED)

        # Everything is built and validated: from here on, state changes.
        self._consume(cid, script)
        self._orders_submitted += 1
        if isinstance(outcome, Vanish):
            return order
        if script is not None and script.hold is not None:
            self._cancel_holds[cid] = script.hold
        if fill is None:
            self._set_order(order)
        else:
            order = fill[0]
            self._record_fill(*fill)
        if isinstance(outcome, TransportFault):
            raise _transport_error(outcome, f"transport error after recording {cid!r}")
        return order

    def cancel(self, client_order_id: str) -> None:
        self._log("cancel", client_order_id)
        order = self._require_order(client_order_id)
        self._require_open(order, "cancel")
        hold = self._cancel_holds.get(client_order_id)
        if hold is None:
            self._set_order(replace(order, status=OrderStatus.CANCELLED))
            return
        if client_order_id in self._cancel_pending:
            return
        if hold.fill is not None:
            self._fill(order, hold.fill, self._now())
        self._cancel_pending.add(client_order_id)

    def get_order(self, client_order_id: str) -> Order:
        self._log("get_order", client_order_id)
        return self._require_order(client_order_id)

    def open_orders(self) -> list[Order]:
        self._log("open_orders")
        return [o for o in self._orders.values() if o.status not in TERMINAL_STATUSES]

    def simulate_fill(self, client_order_id: str) -> Order:
        """Fill a non-terminal order's remainder at `price_of(symbol)`,
        timestamped by the injected clock. Not part of the `Broker`
        interface: a `FakeBroker`-specific escape hatch for tests that
        submit with `auto_fill=False` (see module docstring)."""
        filled_at = self._now()
        order = self._require_order(client_order_id)
        self._require_open(order, "fill")
        return self._fill(order, FillAt(), filled_at)

    def fills(self, since: datetime | None = None) -> list[Fill]:
        self._log(self.fills.__name__, since)
        if since is not None:
            since = ensure_tz_aware_utc(since, field_name="since")
        visible = [
            f for f, hidden in zip(self._fills, self._fill_hidden_reads, strict=True) if hidden == 0
        ]
        self._fill_hidden_reads = [
            hidden - 1 if hidden else hidden for hidden in self._fill_hidden_reads
        ]
        if since is None:
            return visible
        return [fill for fill in visible if fill.filled_at >= since]

    def positions(self) -> dict[str, Position]:
        self._log("positions")
        return {
            symbol: Position(symbol=symbol, quantity=float(net))
            for symbol, net in self._net_quantity.items()
            if net != 0
        }

    def account(self) -> Account:
        self._log("account")
        as_of = self._now()
        marked = sum(
            (
                net
                * Decimal(
                    repr(validate_positive_finite(self._price_of(symbol), field_name="price"))
                )
                for symbol, net in self._net_quantity.items()
                if net != 0
            ),
            Decimal(0),
        )
        cash = float(self._cash)
        return Account(
            account_id=self._account_id,
            cash=cash,
            buying_power=cash if self._buying_power is None else self._buying_power,
            equity=float(self._cash + marked),
            as_of=as_of,
        )

    def assets(self, symbols: Sequence[str]) -> dict[str, Asset]:
        self._log("assets", tuple(symbols))
        result: dict[str, Asset] = {}
        session: date | None = None
        for symbol in (canonical_symbol(s) for s in symbols):
            schedule = self._assets.get(symbol, [(date.min, _DEFAULT_ASSET)])
            if len(schedule) > 1 and session is None:
                session = self._now().astimezone(_NEW_YORK).date()
            result[symbol] = [a for start, a in schedule if session is None or start <= session][-1]
        return result

    # --- internals --------------------------------------------------------

    def _log(self, method: str, *args: object) -> None:
        self._calls.append(BrokerCall(method, args))

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

    def _consume(self, client_order_id: str, script: _Script | None) -> None:
        if script is None:
            return
        if self._scripted_by_id.get(client_order_id) is script:
            del self._scripted_by_id[client_order_id]
        else:
            self._scripted_next.popleft()

    def _fill(self, order: Order, how: FillAt | PartialFill, filled_at: datetime) -> Order:
        filled, fill = self._fill_of(order, how, filled_at)
        self._record_fill(filled, fill)
        return filled

    def _fill_of(
        self, order: Order, how: FillAt | PartialFill, filled_at: datetime
    ) -> tuple[Order, Fill]:
        """The order after one more fill, and that fill, built (and so
        validated) without touching any state. A non-positive or
        non-finite price or quantity fails the `Fill`'s own check."""
        done_qty, done_notional = self._filled.get(order.client_order_id, (Decimal(0), Decimal(0)))
        if isinstance(how, PartialFill):
            quantity, price = how.quantity, how.avg_price
        else:
            price = self._price_of(order.symbol) if how.price is None else how.price
            if order.quantity is not None:
                quantity = float(Decimal(repr(order.quantity)) - done_qty)
            else:
                assert order.notional is not None  # `Order` holds exactly one
                remaining = float(Decimal(repr(order.notional)) - done_notional)
                quantity = remaining / price if price > 0 else price
        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            filled_at=filled_at,
            broker_fill_id=f"fake-fill-{len(self._fills) + 1}",
        )
        total_qty = done_qty + Decimal(repr(fill.quantity))
        total_notional = done_notional + Decimal(repr(fill.quantity)) * Decimal(repr(fill.price))
        complete = isinstance(how, FillAt)
        if isinstance(how, PartialFill) and order.quantity is not None:
            ordered = Decimal(repr(order.quantity))
            if total_qty > ordered:
                raise ValueError(
                    f"partial fill takes {order.client_order_id!r} to {total_qty}, "
                    f"above its quantity {ordered}"
                )
            complete = total_qty == ordered
        filled = replace(
            order,
            status=OrderStatus.FILLED if complete else order.status,
            filled_quantity=float(total_qty),
            filled_avg_price=fill.price if done_qty == 0 else float(total_notional / total_qty),
            # Alpaca sets `filled_at` only once the order is filled.
            filled_at=filled_at if complete else order.filled_at,
        )
        return filled, fill

    def _record_fill(self, filled: Order, fill: Fill) -> None:
        quantity = Decimal(repr(fill.quantity))
        done_qty, done_notional = self._filled.get(fill.client_order_id, (Decimal(0), Decimal(0)))
        self._filled[fill.client_order_id] = (
            done_qty + quantity,
            done_notional + quantity * Decimal(repr(fill.price)),
        )
        self._set_order(filled)
        self._fills.append(fill)
        self._fill_hidden_reads.append(self._fill_lag)
        signed = _signed(fill.side, quantity)
        self._net_quantity[fill.symbol] = self._net_quantity.get(fill.symbol, Decimal(0)) + signed
        amount = quantity * Decimal(repr(fill.price))
        if self._round_cash:
            amount = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
        self._cash -= amount if fill.side is Side.BUY else -amount

    def _set_order(self, order: Order) -> None:
        self._orders[order.client_order_id] = order
        if order.status in TERMINAL_STATUSES:
            self._cancel_holds.pop(order.client_order_id, None)
            self._cancel_pending.discard(order.client_order_id)

    def _forget(self, client_order_id: str) -> None:
        del self._orders[client_order_id]
        self._filled.pop(client_order_id, None)
        self._cancel_holds.pop(client_order_id, None)
        self._cancel_pending.discard(client_order_id)

    def _require_order(self, client_order_id: str) -> Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise UnknownOrderError(f"unknown client_order_id: {client_order_id!r}")
        return order

    @staticmethod
    def _require_open(order: Order, action: str) -> None:
        if order.status in TERMINAL_STATUSES:
            raise OrderNotOpenError(
                f"order {order.client_order_id!r} is {order.status.value}, cannot {action}"
            )


def _transport_error(fault: TransportFault, message: str) -> Exception:
    """A fresh exception per raise, so one scripted fault object raised
    twice never carries the first raise's traceback."""
    return FakeTransportError(message) if fault.error is None else copy.copy(fault.error)


def _signed(side: Side, amount: Decimal) -> Decimal:
    """`amount` for a buy, `-amount` for a sell."""
    if side is Side.BUY:
        return amount
    if side is Side.SELL:
        return -amount
    raise ValueError(f"unexpected side: {side!r}")  # pragma: no cover - Side is validated

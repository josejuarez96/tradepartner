"""`AlpacaBroker`: the `Broker` interface over Alpaca's **paper** trading API
(paper-trading spec req 2, plan T48c).

**Paper only.** Every request goes through T48's raw client
(`adapters/alpaca_trading_raw.py`), which builds its `TradingClient` with the
literal `paper=True`, refuses to start unless `alpaca.paper` is `True`, and reads
only the adapter's book's own paper pair (`ALPACA_PAPER_API_KEY` / `..._SECRET` for
`main`, `ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY` / `..._API_SECRET` for every other
book; ADR 0017 B.1, T153). This adapter checks the
guard again and refuses to construct while `alpaca.quantity_decimals` or
`alpaca.client_order_id_max_length` is unset (T48b's broker facts).

**No risk logic** (ADR 0003 rule 7): no limit, sizing, kill switch or
reconciliation lives here, and the module imports nothing from
`tradepartner.execution`. It sends one order shape, a market DAY order by
notional or quantity (`OrderRequest` is unchanged, ADR 0015 seam 3).

**Status table** (`STATUS_TABLE`, req 2): `new`, `accepted`, `pending_new`,
`pending_cancel` and `partially_filled` map to `ACCEPTED` (a partial fill keeps
`filled_quantity` and `filled_avg_price`); `filled`, `expired`, `rejected` and
`canceled` to their terminal statuses. Any status absent from the table raises
`SystemFaultError`, never a guess. A payload this module cannot map (a missing
field, a value the value objects refuse) raises `SystemFaultError` too.

**Read-side fields** (ADR 0015 seam 3, the 2026-10-08 amendment): `Order` takes
Alpaca's `type`, `time_in_force`, `limit_price`, `stop_price`, `asset_class`,
`order_class` (Alpaca writes a simple order's class as `""`, mapped to
`"simple"`, #1298) and `legs`; `Account` takes `short_market_value`,
`maintenance_margin` and `daytrade_count` (`None` when the response has none);
`Asset` takes `shortable`, `easy_to_borrow` and `marginable`. `Fill.fee` stays
`None`: account activities other than fills are not fetched.

**Dedupe.** `submit` reads the broker for the `client_order_id` first
(`get_order`) and raises `DuplicateClientOrderIdError` before any submit when
the broker has it; the broker's own `client_order_id must be unique` refusal
(HTTP 422, code 40010001, `duplicate_client_order_id.json`) raises the same.

**Cancel** is a request: it returns `None`, and the outcome is read back through
`get_order`. A cancel the broker refuses (404 or 422) is read again: an order
that finished in between raises `OrderNotOpenError`, anything else the refusal.

**Fills** come from `GET /v2/account/activities/FILL`, whose rows carry only the
broker's `order_id`; each distinct id of a fill kept by `since` is resolved once
per call to its
`client_order_id` through `get_order_by_id`, and an id that cannot be resolved
raises `SystemFaultError` (#1298). `since=None` reads from the Unix epoch; the
request asks from `since` less `_AFTER_MARGIN` (Alpaca's `after` is strict) and
the result is filtered to `filled_at >= since`. No time is parsed from an
activity id (its prefix is New York local time, T48b fact 13).

**Clock** (ADR 0007 point 4): the injected `clock` is exposed as `.clock`, and
every public method reads it first, wrapped, so a failing or invalid reading
raises `ClockError` before any request. Broker timestamps (`submitted_at`,
`filled_at`, a fill's `transaction_time`) stay in their own fields; the clock
reading is used only for `Account.as_of`, which Alpaca does not report.
Alpaca's nanosecond timestamps are truncated to microseconds.

**Symbols.** `symbol_for` and `ticker_for` are the one pair for class-share
separators: `symbol_for` is the prices adapter's `master_symbol` rule (with
`alpaca.class_symbols`), `ticker_for` its inverse over the master rows it is
given; an unmapped or ambiguous symbol raises `SystemFaultError`. The adapter
sends `OrderRequest.symbol` as given: the wrapper maps it (T60c's
`phases.requests_for`), because the adapter has neither the master nor the
session.

**No secret in any exception**: errors from the raw client hold only a status
code and a response body, and this module's own messages name no setting value.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tradepartner.adapters.alpaca_prices import master_symbol
from tradepartner.adapters.alpaca_trading_raw import (
    AlpacaPaperGuardError,
    AlpacaTradingError,
    AlpacaTradingRaw,
    market_day_order,
)
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
from tradepartner.config import Settings
from tradepartner.errors import ClockError, SystemFaultError
from tradepartner.timeutil import ensure_tz_aware_utc

#: Every Alpaca order status this adapter accepts (spec req 2); any other raises.
STATUS_TABLE: Mapping[str, OrderStatus] = {
    "new": OrderStatus.ACCEPTED,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.ACCEPTED,
    "pending_cancel": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.ACCEPTED,
    "filled": OrderStatus.FILLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
    "canceled": OrderStatus.CANCELLED,
}

#: Alpaca's empty `order_class` on a simple order (#1298).
_SIMPLE_ORDER_CLASS = "simple"
#: HTTP status and Alpaca code of the broker's duplicate-id refusal (T48b).
_NOT_FOUND = 404
_DUPLICATE_STATUS = 422
_DUPLICATE_CODE = 40010001
#: A cancel refused because the order is no longer cancelable, or gone.
_CANCEL_REFUSALS = frozenset({_NOT_FOUND, _DUPLICATE_STATUS})
#: `fills(None)` reads from here.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
#: Alpaca's activities `after` is strict; ask from a second earlier, then filter.
_AFTER_MARGIN = timedelta(seconds=1)
_TIMESTAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)")
_MICROSECOND_DIGITS = 6


class AlpacaBrokerConfigError(RuntimeError):
    """A broker fact the adapter needs (`alpaca.quantity_decimals`,
    `alpaca.client_order_id_max_length`) is unset."""


def symbol_for(
    ticker: str, *, exchange: str | None, class_symbols: Mapping[str, str] | None
) -> str:
    """The Alpaca symbol of a master listing's `ticker` on `exchange`: the prices
    adapter's `master_symbol` rule, with `alpaca.class_symbols` as
    `class_symbols` (`BRK-B` -> `BRK.B`; NYSE `BFB` -> `BF.B`). A ticker with no
    Alpaca form raises `SystemFaultError`."""
    symbol = master_symbol({"ticker": ticker, "exchange": exchange}, class_symbols)
    if symbol is None:
        raise SystemFaultError(f"ticker {ticker!r} has no Alpaca symbol")
    return symbol


def ticker_for(
    symbol: str,
    listings: Iterable[Mapping[str, Any]],
    *,
    class_symbols: Mapping[str, str] | None,
) -> str:
    """The master ticker of the one row of `listings` (each with `ticker` and
    `exchange`) whose `symbol_for` is `symbol`: the inverse of the same rule,
    never a rewrite of the symbol. No such row, or rows with two different
    tickers, raise `SystemFaultError`."""
    wanted = canonical_symbol(symbol)
    tickers = sorted(
        {str(row["ticker"]) for row in listings if master_symbol(row, class_symbols) == wanted}
    )
    if len(tickers) != 1:
        found = "no master ticker" if not tickers else f"ambiguous master tickers {tickers}"
        raise SystemFaultError(f"symbol {wanted!r}: {found}")
    return tickers[0]


def _instant(text: Any) -> datetime:
    """An Alpaca RFC 3339 timestamp, fractions beyond microseconds truncated."""
    match = _TIMESTAMP.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"not an Alpaca timestamp: {text!r}")
    whole, fraction, zone = match.groups()
    micro = (fraction or "")[:_MICROSECOND_DIGITS].ljust(_MICROSECOND_DIGITS, "0")
    parsed = datetime.fromisoformat(f"{whole}.{micro}{'+00:00' if zone == 'Z' else zone}")
    return ensure_tz_aware_utc(parsed, field_name="broker timestamp")


def _optional_instant(text: Any) -> datetime | None:
    return None if text is None else _instant(text)


def _number(text: Any) -> float:
    """An Alpaca decimal string (or number) as `float`; never a `bool`."""
    if isinstance(text, bool) or not isinstance(text, str | int | float):
        raise ValueError(f"not a number: {text!r}")
    return float(text)


def _optional_number(text: Any) -> float | None:
    return None if text is None else _number(text)


def _status(text: Any) -> OrderStatus:
    status = STATUS_TABLE.get(text) if isinstance(text, str) else None
    if status is None:
        raise SystemFaultError(f"alpaca order status {text!r} is not in the status table")
    return status


def _order(payload: Mapping[str, Any]) -> Order:
    """One Alpaca order payload as an `Order` (`STATUS_TABLE`, read-side fields)."""
    status = _status(payload["status"])
    filled = _number(payload["filled_qty"])
    if filled < 0:
        raise ValueError(f"filled_qty {filled!r} is negative")
    order_class = payload["order_class"]
    legs = payload["legs"]
    return Order(
        client_order_id=payload["client_order_id"],
        symbol=payload["symbol"],
        side=Side(payload["side"]),
        notional=_optional_number(payload["notional"]),
        quantity=_optional_number(payload["qty"]),
        status=status,
        submitted_at=_instant(payload["submitted_at"]),
        broker_order_id=payload["id"],
        filled_quantity=filled or None,
        filled_avg_price=_optional_number(payload["filled_avg_price"]) if filled else None,
        filled_at=_optional_instant(payload["filled_at"]),
        order_type=payload["type"],
        time_in_force=payload["time_in_force"],
        limit_price=_optional_number(payload["limit_price"]),
        stop_price=_optional_number(payload["stop_price"]),
        asset_class=payload["asset_class"],
        order_class=_SIMPLE_ORDER_CLASS if order_class == "" else order_class,
        legs=() if legs is None else tuple(_order(leg) for leg in legs),
    )


def _mapped[A, T](what: str, build: Callable[[A], T], payload: A) -> T:
    """`build(payload)`, with a payload it cannot map raised as `SystemFaultError`."""
    try:
        return build(payload)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise SystemFaultError(f"malformed alpaca {what}: {type(exc).__name__}: {exc}") from exc


def _error_code(error: AlpacaTradingError) -> object:
    """Alpaca's `code` in a refusal's JSON body, or `None`."""
    try:
        body = json.loads(error.body)
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def _decimals(value: float) -> int:
    exponent = Decimal(repr(value)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


class AlpacaBroker(Broker):
    """`Broker` over Alpaca's paper trading API (module docstring)."""

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], datetime],
        client: AlpacaTradingRaw | None = None,
        *,
        book_id: str,
    ) -> None:
        """Refuse unless `alpaca.paper` is `True` and both broker facts are set,
        then use `client` (T48's raw client) or build one from `settings` on
        `book_id`'s own paper pair (ADR 0017 B.2, plan T153). An injected `client`
        built for another book is refused, so a book never trades on another's pair."""
        alpaca = settings.alpaca
        if alpaca.paper is not True:
            raise AlpacaPaperGuardError("alpaca.paper is not true; the adapter is paper only")
        unset = [
            f"alpaca.{name}"
            for name in ("quantity_decimals", "client_order_id_max_length")
            if getattr(alpaca, name) is None
        ]
        if unset:
            raise AlpacaBrokerConfigError(
                f"{' and '.join(unset)} must be set (T48b's broker facts) before the "
                "Alpaca adapter can be built"
            )
        assert alpaca.quantity_decimals is not None
        assert alpaca.client_order_id_max_length is not None
        self._quantity_decimals: int = alpaca.quantity_decimals
        self._max_id_length: int = alpaca.client_order_id_max_length
        if client is not None and client.book_id != book_id:
            raise AlpacaPaperGuardError(
                f"the injected raw client is book {client.book_id!r}'s, not book {book_id!r}'s"
            )
        self.clock = clock
        self.book_id = book_id
        self._raw = client if client is not None else AlpacaTradingRaw(settings, book_id=book_id)

    # --- Broker -----------------------------------------------------------

    def submit(self, request: OrderRequest) -> Order:
        """Submit a market DAY order, after the broker shows no order with its
        `client_order_id` (else `DuplicateClientOrderIdError`, no submit)."""
        self._now()
        coid = request.client_order_id
        if len(coid) > self._max_id_length:
            raise ValueError(
                f"client_order_id is {len(coid)} characters, above {self._max_id_length}"
            )
        if request.quantity is not None and (_decimals(request.quantity) > self._quantity_decimals):
            raise ValueError(
                f"quantity {request.quantity!r} has more than {self._quantity_decimals} decimals"
            )
        try:
            self.get_order(coid)
        except UnknownOrderError:
            pass
        else:
            raise DuplicateClientOrderIdError(f"the broker already has order {coid!r}")
        order_data = market_day_order(
            request.symbol,
            request.side.value,
            coid,
            notional=request.notional,
            qty=request.quantity,
        )
        try:
            payload = self._raw.submit_order(order_data)
        except AlpacaTradingError as error:
            if error.status_code == _DUPLICATE_STATUS and _error_code(error) == _DUPLICATE_CODE:
                raise DuplicateClientOrderIdError(
                    f"the broker refused order {coid!r} as a duplicate"
                ) from None
            raise
        order = _mapped("order", _order, payload)
        if order.client_order_id != coid:
            raise SystemFaultError(f"submit of {coid!r} returned order {order.client_order_id!r}")
        return order

    def cancel(self, client_order_id: str) -> None:
        """Request cancellation; the outcome is read back through `get_order`."""
        order = self.get_order(client_order_id)
        if order.status in TERMINAL_STATUSES:
            raise OrderNotOpenError(f"order {client_order_id!r} is {order.status.value}")
        if order.broker_order_id is None:
            raise SystemFaultError(f"order {client_order_id!r} has no broker order id")
        try:
            self._raw.cancel_order(order.broker_order_id)
        except AlpacaTradingError as error:
            if error.status_code not in _CANCEL_REFUSALS:
                raise
            # Refused: the order may have finished between the read and the
            # request. Read it again; a terminal order is `OrderNotOpenError`.
            again = self.get_order(client_order_id)
            if again.status in TERMINAL_STATUSES:
                raise OrderNotOpenError(
                    f"order {client_order_id!r} is {again.status.value}"
                ) from None
            raise

    def get_order(self, client_order_id: str) -> Order:
        """The broker's order with `client_order_id`, or `UnknownOrderError`."""
        self._now()
        try:
            payload = self._raw.get_order_by_client_id(client_order_id)
        except AlpacaTradingError as error:
            if error.status_code == _NOT_FOUND:
                raise UnknownOrderError(f"the broker has no order {client_order_id!r}") from None
            raise
        return _mapped("order", _order, payload)

    def open_orders(self) -> list[Order]:
        """Every order Alpaca lists as open."""
        self._now()
        payloads = self._raw.list_open_orders()
        return [_mapped("order", _order, p) for p in payloads]

    def fills(self, since: datetime | None = None) -> list[Fill]:
        """Every fill activity with `filled_at >= since`, oldest first."""
        self._now()
        start = _EPOCH if since is None else ensure_tz_aware_utc(since, field_name="since")
        activities = self._raw.list_fill_activities(max(_EPOCH, start - _AFTER_MARGIN))
        owners: dict[str, str] = {}
        fills: list[Fill] = []
        for activity in activities:
            # Filter first, so no order is resolved for a fill the caller did not ask for.
            if (
                _mapped("fill activity", lambda a: _instant(a["transaction_time"]), activity)
                < start
            ):
                continue
            fills.append(_mapped("fill activity", lambda a: self._fill(a, owners), activity))
        return fills

    def positions(self) -> dict[str, Position]:
        """Alpaca's positions, keyed by canonical symbol; a zero quantity is absent."""
        self._now()
        positions: dict[str, Position] = {}
        for payload in self._raw.list_positions():
            position = _mapped("position", _position, payload)
            if position.quantity:
                positions[position.symbol] = position
        return positions

    def account(self) -> Account:
        """Alpaca's account, as of the clock's reading."""
        now = self._now()
        payload = self._raw.get_account()
        return _mapped("account", lambda p: _account(p, now), payload)

    def assets(self, symbols: Sequence[str]) -> dict[str, Asset]:
        """Alpaca's `Asset` per requested symbol, keyed by canonical symbol."""
        self._now()
        wanted = [canonical_symbol(symbol) for symbol in symbols]
        payloads = self._raw.get_assets(wanted)
        if len(payloads) != len(wanted):
            raise SystemFaultError(f"asset read answered {len(payloads)} of {len(wanted)} symbols")
        assets: dict[str, Asset] = {}
        for symbol, payload in zip(wanted, payloads, strict=True):
            if _mapped("asset", lambda p: canonical_symbol(p["symbol"]), payload) != symbol:
                raise SystemFaultError(f"asset read for {symbol!r} answered another symbol")
            assets[symbol] = _mapped("asset", _asset, payload)
        return assets

    # --- internals --------------------------------------------------------

    def _now(self) -> datetime:
        """One clock reading, tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
        try:
            reading = self.clock()
            if not isinstance(reading, datetime):
                raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
            return ensure_tz_aware_utc(reading, field_name="clock")
        except Exception as exc:
            raise ClockError(f"clock failed: {type(exc).__name__}") from exc

    def _fill(self, activity: Mapping[str, Any], owners: dict[str, str]) -> Fill:
        broker_order_id = activity["order_id"]
        if broker_order_id not in owners:
            owners[broker_order_id] = self._owner(broker_order_id)
        return Fill(
            client_order_id=owners[broker_order_id],
            symbol=activity["symbol"],
            side=Side(activity["side"]),
            quantity=_number(activity["qty"]),
            price=_number(activity["price"]),
            filled_at=_instant(activity["transaction_time"]),
            broker_fill_id=activity["id"],
        )

    def _owner(self, broker_order_id: Any) -> str:
        """The `client_order_id` of the broker's order `broker_order_id`."""
        if not isinstance(broker_order_id, str) or not broker_order_id:
            raise SystemFaultError(f"fill activity order id {broker_order_id!r}")
        try:
            payload = self._raw.get_order_by_id(broker_order_id)
        except AlpacaTradingError as error:
            if error.status_code == _NOT_FOUND:
                raise SystemFaultError(
                    f"fill of broker order {broker_order_id!r}: the broker has no such order"
                ) from None
            raise
        coid = payload.get("client_order_id") if isinstance(payload, Mapping) else None
        if not isinstance(coid, str) or not coid or payload.get("id") != broker_order_id:
            raise SystemFaultError(
                f"fill of broker order {broker_order_id!r}: no client_order_id resolved"
            )
        return coid


def _position(payload: Mapping[str, Any]) -> Position:
    quantity = _number(payload["qty"])
    side = payload["side"]
    if quantity and side != ("long" if quantity > 0 else "short"):
        raise ValueError(f"position side {side!r} disagrees with quantity {quantity!r}")
    return Position(symbol=payload["symbol"], quantity=quantity)


def _account(payload: Mapping[str, Any], as_of: datetime) -> Account:
    daytrades = payload.get("daytrade_count")
    return Account(
        account_id=payload["id"],
        cash=_number(payload["cash"]),
        buying_power=_number(payload["buying_power"]),
        equity=_number(payload["equity"]),
        as_of=as_of,
        short_market_value=_optional_number(payload.get("short_market_value")),
        maintenance_margin=_optional_number(payload.get("maintenance_margin")),
        daytrade_count=daytrades,
    )


def _asset(payload: Mapping[str, Any]) -> Asset:
    return Asset(
        tradable=payload["tradable"],
        fractionable=payload["fractionable"],
        status=payload["status"],
        cusip=payload.get("cusip") or None,
        shortable=payload["shortable"],
        easy_to_borrow=payload["easy_to_borrow"],
        marginable=payload["marginable"],
    )

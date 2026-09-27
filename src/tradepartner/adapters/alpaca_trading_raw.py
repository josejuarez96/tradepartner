"""Thin raw client over `alpaca-py`'s trading API, paper endpoint only (T48).

Every call returns the SDK's raw JSON (`raw_data=True`), never a model object, and
does no interpretation: the status table, symbol mapping and deduplication live in
`adapters/alpaca_broker.py` (T48c). Only that adapter and the owner-run recorder
(`cli_record paper`) may import this module (T50's boundary test).

Paper only, on every path (spec req 2): the `TradingClient` is built with the literal
`paper=True`, never `settings.alpaca.paper` forwarded, because `model_copy` and
`model_construct` bypass that field's guard; construction also refuses to start when
`settings.alpaca.paper is not True`. Credentials come from `ALPACA_PAPER_API_KEY` /
`ALPACA_PAPER_API_SECRET` only, never the data keys, so a live-capable key is never in
the order path. No exception raised here carries a secret.

The EDGAR client pattern: requests are paced at `alpaca.trading_requests_per_minute`,
time out after `alpaca.trading_request_timeout_seconds`, and a timeout (or a 429/504,
the SDK's own retry codes, whose internal retry is switched off) is retried
`alpaca.trading_max_retries` times, then raised. A retried submit is safe because the
broker refuses a second order with the same `client_order_id`, so `submit_order`
requires one.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest, OrderRequest
from pydantic import SecretStr
from requests.adapters import HTTPAdapter

from tradepartner.config import Settings, get_settings
from tradepartner.timeutil import ensure_tz_aware_utc

# API maxima, not tunables: `GET /v2/orders` returns at most 500 orders and
# `GET /v2/account/activities` at most 100 per page (Alpaca API reference).
OPEN_ORDERS_LIMIT = 500
ACTIVITIES_PAGE_SIZE = 100
_RETRY_STATUS_CODES = frozenset({429, 504})


class AlpacaPaperCredentialsError(RuntimeError):
    """`ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET` missing or blank."""


class AlpacaPaperGuardError(RuntimeError):
    """`settings.alpaca.paper` is not `True` (the guard was bypassed)."""


class PacingClock(Protocol):
    """What pacing needs from a clock: a monotonic reading and a sleep."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemPacingClock:
    """The real `time.monotonic` / `time.sleep`."""

    def monotonic(self) -> float:
        """`time.monotonic()`."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """`time.sleep(seconds)`."""
        time.sleep(seconds)


class TradingClientLike(Protocol):
    """The subset of `alpaca.trading.client.TradingClient` this module calls."""

    def submit_order(self, order_data: OrderRequest) -> Any: ...

    def cancel_order_by_id(self, order_id: str) -> Any: ...

    def get_order_by_client_id(self, client_id: str) -> Any: ...

    def get_orders(self, filter: GetOrdersRequest | None = None) -> Any: ...

    def get_all_positions(self) -> Any: ...

    def get_account(self) -> Any: ...

    def get_asset(self, symbol_or_asset_id: str) -> Any: ...

    def get(self, path: str, data: dict[str, Any] | str | None = None) -> Any: ...


class _TimeoutAdapter(HTTPAdapter):
    """An `HTTPAdapter` that applies `timeout` to every request sent without one;
    the SDK's session passes none, so without this a request could hang forever."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.timeout = timeout

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        """Send with the configured timeout unless the caller set one."""
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def _non_blank(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value if value.strip() else None


def _paper_credentials(settings: Settings) -> tuple[str, str]:
    key = _non_blank(settings.alpaca_paper_api_key)
    secret = _non_blank(settings.alpaca_paper_api_secret)
    missing = [
        name
        for name, value in (("ALPACA_PAPER_API_KEY", key), ("ALPACA_PAPER_API_SECRET", secret))
        if value is None
    ]
    if key is None or secret is None:
        raise AlpacaPaperCredentialsError(
            f"{' and '.join(missing)} must be set (see .env.example) before any paper "
            "trading call; the data keys are never used for trading."
        )
    return key, secret


def _build_client(key: str, secret: str, timeout_seconds: float) -> TradingClient:
    client = TradingClient(api_key=key, secret_key=secret, paper=True, raw_data=True)
    # Private SDK attributes, pinned by a test so an upgrade that renames them fails
    # loudly: our loop owns retries (paced, counted), and the session gets a timeout.
    client._retry = 0
    client._session.mount("https://", _TimeoutAdapter(timeout_seconds))
    return client


def _retryable(error: BaseException) -> bool:
    if isinstance(error, requests.Timeout):
        return True
    return isinstance(error, APIError) and error.status_code in _RETRY_STATUS_CODES


class AlpacaTradingRaw:
    """Paced, retried raw calls to the Alpaca **paper** trading API."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: TradingClientLike | None = None,
        clock: PacingClock | None = None,
    ) -> None:
        """Refuse unless `alpaca.paper` is `True` and both paper keys are set, then
        build the paper `TradingClient` (or use the injected `client`)."""
        settings = settings or get_settings()
        if settings.alpaca.paper is not True:
            raise AlpacaPaperGuardError(
                "alpaca.paper is not true; the trading client runs against paper only "
                "(guarded setting, changed only by ADR)."
            )
        key, secret = _paper_credentials(settings)
        alpaca = settings.alpaca
        self._client: TradingClientLike = (
            client
            if client is not None
            else _build_client(key, secret, alpaca.trading_request_timeout_seconds)
        )
        self.clock: PacingClock = clock or SystemPacingClock()
        self._interval = 60.0 / alpaca.trading_requests_per_minute
        self._max_retries = alpaca.trading_max_retries
        self._last: float | None = None
        self._lock = threading.Lock()

    def _pace(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self._interval - (self.clock.monotonic() - self._last)
                if remaining > 0:
                    self.clock.sleep(remaining)
            self._last = self.clock.monotonic()

    def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        for attempt in range(self._max_retries + 1):
            self._pace()
            try:
                return fn(*args)
            except (requests.Timeout, APIError) as error:
                if not _retryable(error) or attempt == self._max_retries:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def submit_order(self, request: OrderRequest) -> Any:
        """`POST /v2/orders`; `request` must carry a `client_order_id`."""
        if not request.client_order_id:
            raise ValueError("submit_order needs a client_order_id (retries rely on it)")
        return self._call(self._client.submit_order, request)

    def cancel_order(self, broker_order_id: str) -> None:
        """`DELETE /v2/orders/{id}`: a request; read the outcome back by id."""
        self._call(self._client.cancel_order_by_id, broker_order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        """`GET /v2/orders:by_client_order_id`."""
        return self._call(self._client.get_order_by_client_id, client_order_id)

    def list_open_orders(self) -> list[Any]:
        """Every open order; raises rather than return a list the API may have cut."""
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=OPEN_ORDERS_LIMIT)
        orders = list(self._call(self._client.get_orders, request))
        if len(orders) >= OPEN_ORDERS_LIMIT:
            raise RuntimeError(f"open orders reached the API limit {OPEN_ORDERS_LIMIT}: truncated")
        return orders

    def list_positions(self) -> list[Any]:
        """`GET /v2/positions`."""
        return list(self._call(self._client.get_all_positions))

    def get_account(self) -> Any:
        """`GET /v2/account`."""
        return self._call(self._client.get_account)

    def list_fill_activities(self, after: datetime) -> list[Any]:
        """Every `FILL` activity after `after` (tz-aware UTC), oldest first, all pages."""
        after = ensure_tz_aware_utc(after, field_name="after")
        params: dict[str, Any] = {
            "after": after.isoformat(),
            "direction": "asc",
            "page_size": ACTIVITIES_PAGE_SIZE,
        }
        activities: list[Any] = []
        while True:
            page = list(self._call(self._client.get, "/account/activities/FILL", dict(params)))
            activities.extend(page)
            if len(page) < ACTIVITIES_PAGE_SIZE:
                return activities
            params["page_token"] = page[-1]["id"]

    def get_assets(self, symbols: Sequence[str]) -> list[Any]:
        """`GET /v2/assets/{symbol}` for each of `symbols`, in order."""
        return [self._call(self._client.get_asset, symbol) for symbol in symbols]

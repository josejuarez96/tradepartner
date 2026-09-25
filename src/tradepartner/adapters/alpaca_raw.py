"""Thin raw-fetch client over `alpaca-py`.

Wraps `StockHistoricalDataClient`, `CorporateActionsClient` and
`TradingClient`, all constructed with `raw_data=True` so every call returns
the SDK's underlying JSON (plain `dict`/`list`), never an SDK model object
(spec "Interfaces": raw clients return raw payloads; JSON-serializable, so
recordings round-trip through `json.dump`/`json.load` unchanged).

No parsing happens here (spec "Raw payload" definition: "fetch functions
return raw payloads; parsers turn raw payloads into records"). Parsing,
`known_at` stamping and security-master resolution live in
`adapters/alpaca_prices.py` (T12).

Credentials come from `Settings.alpaca_api_key` / `.alpaca_api_secret` via
`.get_secret_value()`; `AlpacaCredentialsError` is raised with a clear
message if either is missing, before any network call.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from pydantic import SecretStr

from tradepartner.config import Settings, get_settings

# Alpaca's free market-data plan is believed IEX-only (ADR 0003 "Verify
# before the Phase 2 plan"); T3 confirms this against the owner's real
# account and may promote it to a `Settings` field. Until then, callers can
# override `feed=` explicitly, and every raw-bars payload records the feed
# actually used so a recording or a caller never has to guess.
DEFAULT_FEED = DataFeed.IEX


class AlpacaCredentialsError(RuntimeError):
    """Raised when `ALPACA_API_KEY` / `ALPACA_API_SECRET` are not configured."""


def _non_blank_secret(secret: SecretStr | None) -> str | None:
    """`secret`'s value, or `None` if it's unset or blank/whitespace-only.

    A `.env` line like `ALPACA_API_KEY=` sets the value to `""`, which is
    "configured" as far as `SecretStr | None` is concerned but not usable;
    treat it the same as missing (T2 review round 2, safety-reviewer).
    """
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value if value.strip() else None


def _credentials(settings: Settings) -> tuple[str, str]:
    api_key = _non_blank_secret(settings.alpaca_api_key)
    api_secret = _non_blank_secret(settings.alpaca_api_secret)
    if api_key is None or api_secret is None:
        raise AlpacaCredentialsError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set (see .env.example) "
            "before fetching Alpaca data."
        )
    return api_key, api_secret


def _stock_data_client(settings: Settings) -> StockHistoricalDataClient:
    api_key, secret_key = _credentials(settings)
    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key, raw_data=True)


def _corporate_actions_client(settings: Settings) -> CorporateActionsClient:
    api_key, secret_key = _credentials(settings)
    return CorporateActionsClient(api_key=api_key, secret_key=secret_key, raw_data=True)


def _trading_client(settings: Settings) -> TradingClient:
    api_key, secret_key = _credentials(settings)
    # `paper=True` selects the paper-trading base URL for the *trading*
    # endpoints; the assets snapshot is account-agnostic reference data, so
    # this has no bearing on which market data is returned.
    return TradingClient(api_key=api_key, secret_key=secret_key, raw_data=True, paper=True)


def _session_bounds_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """`[start, end]` session dates as an inclusive tz-aware UTC instant range.

    `alpaca-py` requests take instants, not dates; callers of this module
    pass session dates, matching `calendar.py`'s convention (a session is a
    `date`, an instant is a tz-aware UTC `datetime`).
    """
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end, time.max, tzinfo=UTC),
    )


def daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    feed: DataFeed = DEFAULT_FEED,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Raw, unadjusted daily bars for `symbols` over `[start, end]`.

    Returns `{"feed": <feed used>, "bars": <raw payload>}` so the feed that
    produced the bars travels with them (spec: "expose the feed used").
    Always `adjustment=raw` per ADR 0003 rule 1 (adjustment happens at read
    time in the store, never at the source).
    """
    settings = settings or get_settings()
    client = _stock_data_client(settings)
    start_utc, end_utc = _session_bounds_utc(start, end)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        start=start_utc,
        end=end_utc,
        timeframe=TimeFrame.Day,
        adjustment=Adjustment.RAW,
        feed=feed,
    )
    raw_bars = client.get_stock_bars(request)
    return {"feed": feed.value, "bars": raw_bars}


def corporate_actions(
    symbols: list[str],
    start: date,
    end: date,
    *,
    settings: Settings | None = None,
) -> Any:
    """Raw corporate-actions payload (splits, dividends, ...) for `symbols`.

    `CorporateActionsRequest.limit` defaults to `1000` **as a total cap
    across every page**, not a per-page size: `alpaca-py`'s `_get_marketdata`
    paging loop shrinks its per-request `limit` as pages accumulate and
    stops once `1000` results have been returned in total, silently
    truncating any range with more corporate actions than that (verified
    against the installed `alpaca-py` 0.44.0 `RESTClient._get_marketdata`
    source; T2 review round 2, safety-reviewer MUST FIX). Passing
    `limit=None` here removes the request's `limit` field entirely
    (`CorporateActionsRequest.to_request_fields()` omits `None` fields), so
    paging is governed only by `get_corporate_actions`'s own fixed
    `page_size=1000`/`page_limit=1000` per-page arguments and continues
    until the API stops returning a `next_page_token` — i.e. it always
    fetches everything.
    """
    settings = settings or get_settings()
    client = _corporate_actions_client(settings)
    request = CorporateActionsRequest(symbols=symbols, start=start, end=end, limit=None)
    return client.get_corporate_actions(request)


def assets_snapshot(
    symbols: list[str],
    *,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Raw assets-endpoint snapshot for `symbols` (current-only; one call per symbol).

    `TradingClient.get_all_assets` has no per-symbol filter (only status,
    class, exchange, attributes), so the assets snapshot is fetched one
    symbol at a time via `GET /v2/assets/{symbol}`.
    """
    settings = settings or get_settings()
    client = _trading_client(settings)
    snapshot: list[dict[str, Any]] = []
    for symbol in symbols:
        raw_asset = client.get_asset(symbol)
        snapshot.append(dict(raw_asset))
    return snapshot

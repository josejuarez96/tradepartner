"""Raw, paged FRED observations client. URLs and credentials never enter errors or logs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from threading import RLock
from typing import Any

import httpx

from tradepartner.config import Settings, get_settings

_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
_LIMIT = 100000
_LOG_LOCK = RLock()


class FredCredentialsError(RuntimeError):
    """FRED_API_KEY is absent or blank."""


class FredRequestError(RuntimeError):
    """A FRED observations request failed; the URL is intentionally omitted."""


@contextmanager
def _quiet_http_logs() -> Iterator[None]:
    """Hide httpx/httpcore request URLs, including DEBUG output, during a request."""
    with _LOG_LOCK:
        names = {"httpx", "httpcore"}
        names.update(
            name
            for name in logging.root.manager.loggerDict
            if name.startswith(("httpx.", "httpcore."))
        )
        loggers = [logging.getLogger(name) for name in names]
        levels = [logger.level for logger in loggers]
        try:
            for logger in loggers:
                logger.setLevel(logging.WARNING)
            yield
        finally:
            for logger, level in zip(loggers, levels, strict=True):
                logger.setLevel(level)


def observations(
    series: str,
    realtime_start: date,
    observation_start: date,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return all output_type=1 pages as one raw payload.

    Authentication is checked before a client is built. Transport and status
    failures are wrapped without a URL or chained exception.
    """
    settings = settings or get_settings()
    key = settings.fred_api_key
    secret = key.get_secret_value() if key is not None else ""
    if not secret.strip():
        raise FredCredentialsError("FRED_API_KEY must be set before fetching FRED rates")
    params: dict[str, str | int] = {
        "series_id": series,
        "api_key": secret,
        "file_type": "json",
        "output_type": 1,
        "limit": _LIMIT,
        "realtime_start": realtime_start.isoformat(),
        "realtime_end": "9999-12-31",
        "observation_start": observation_start.isoformat(),
    }
    owned = client is None
    if client is None:
        client = httpx.Client()
    try:
        rows: list[Any] = []
        offset = 0
        count: int | None = None
        while count is None or offset < count:
            with _quiet_http_logs():
                try:
                    response = client.get(_ENDPOINT, params=params | {"offset": offset})
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    raise FredRequestError(
                        f"FRED observations endpoint returned HTTP {exc.response.status_code}"
                    ) from None
                except (httpx.RequestError, ValueError):
                    raise FredRequestError("FRED observations endpoint request failed") from None
            if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
                raise ValueError("FRED observations response lacks observations")
            if not isinstance(payload.get("count"), int) or payload["count"] < 0:
                raise ValueError("FRED observations response has invalid count")
            count = payload["count"]
            rows.extend(payload["observations"])
            offset += _LIMIT
        return {"count": count, "observations": rows}
    finally:
        if owned:
            client.close()

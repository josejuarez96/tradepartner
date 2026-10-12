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
                failure: str | None = None
                try:
                    response = client.get(_ENDPOINT, params=params | {"offset": offset})
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    failure = f"FRED observations endpoint returned HTTP {exc.response.status_code}"
                except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                    failure = "FRED observations endpoint request failed"
                # Raise outside the except: the original httpx exception may hold
                # the full URL, and `from None` leaves it in __context__.
                if failure is not None:
                    raise FredRequestError(failure)
            if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
                raise ValueError("FRED observations response lacks observations")
            if not isinstance(payload.get("count"), int) or payload["count"] < 0:
                raise ValueError("FRED observations response has invalid count")
            page_count = payload["count"]
            if count is not None and page_count != count:
                raise ValueError("FRED observations count changed during paging")
            count = page_count
            page = payload["observations"]
            if not page and offset < count:
                raise ValueError("FRED observations returned an empty page before count")
            rows.extend(page)
            offset += len(page)
            if offset > count:
                raise ValueError("FRED observations exceeded reported count")
        if offset != count:
            raise ValueError("FRED observations total differs from count")
        return {"count": count, "observations": rows}
    finally:
        if owned:
            client.close()

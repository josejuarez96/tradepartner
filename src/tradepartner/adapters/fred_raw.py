"""Raw, paged FRED observations client. URLs and credentials never enter errors or logs."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any

import httpx

from tradepartner.config import Settings, get_settings

_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
_LIMIT = 100000
_LOG_LOCK = RLock()
_sleep = time.sleep
_OPEN_END = date(9999, 12, 31)
_MAX_ERROR_CHARS = 200


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


def _vintage_windows(realtime_start: date, days: int, today: date) -> list[tuple[date, date]]:
    """Consecutive real-time windows, each `days` long; the last runs to 9999-12-31.

    FRED refuses a query spanning more than 2000 vintage dates (HTTP 400), so one
    wide request is replaced by windows holding few vintage dates each.
    """
    windows: list[tuple[date, date]] = []
    start = realtime_start
    while True:
        try:
            nxt = start + timedelta(days=days)
        except OverflowError:
            nxt = date.max
        if nxt > today:
            windows.append((start, _OPEN_END))
            return windows
        windows.append((start, nxt - timedelta(days=1)))
        start = nxt


def _merge_windows(parts: list[tuple[date, date, list[Any]]]) -> list[Any]:
    """Concatenate per-window rows, rejoining rows FRED clipped at a window edge.

    A row valid across a boundary appears in both windows with clipped periods
    (earlier: `realtime_end` = window end; later: `realtime_start` = window start).
    Same observation date and value on both sides of the edge is one row: keep the
    earlier true `realtime_start` and the later `realtime_end`. A different value
    starting at the edge is a real revision and stays separate.
    """
    merged: list[Any] = []
    carry: dict[tuple[Any, Any], int] = {}
    for index, (start, end, rows) in enumerate(parts):
        next_carry: dict[tuple[Any, Any], int] = {}
        for row in rows:
            joined = False
            if (
                index
                and isinstance(row, dict)
                and row.get("realtime_start") == start.isoformat()
                and (row.get("date"), row.get("value")) in carry
            ):
                target = merged[carry[(row.get("date"), row.get("value"))]]
                target["realtime_end"] = row.get("realtime_end")
                row = target
                joined = True
            if not joined:
                merged.append(dict(row) if isinstance(row, dict) else row)
                row = merged[-1]
            if isinstance(row, dict) and row.get("realtime_end") == end.isoformat():
                next_carry[(row.get("date"), row.get("value"))] = (
                    carry[(row.get("date"), row.get("value"))] if joined else len(merged) - 1
                )
        carry = next_carry
    if len(parts) > 1:
        merged.sort(
            key=lambda r: (
                (r.get("date", ""), r.get("realtime_start", ""))
                if isinstance(r, dict)
                else ("", "")
            )
        )
    return merged


def _error_message(response: httpx.Response, secret: str) -> str:
    """FRED's own `error_message` (no URL), capped and scrubbed of the key."""
    try:
        text = response.json().get("error_message")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(text, str):
        return ""
    if secret:
        text = text.replace(secret, "***")
    return " ".join(text.split())[:_MAX_ERROR_CHARS]


def _get_page(client: httpx.Client, params: dict[str, str | int], retry: tuple[int, float]) -> Any:
    """GET one page, retrying timeouts and 5xx with exponential backoff; never 4xx."""
    attempts, backoff = retry
    for attempt in range(1, attempts + 1):
        failure = "FRED observations endpoint request failed"
        retryable = False
        with _quiet_http_logs():
            try:
                response = client.get(_ENDPOINT, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                failure = f"FRED observations endpoint returned HTTP {code}"
                detail = _error_message(exc.response, str(params.get("api_key", "")))
                if detail:
                    failure += f": {detail}"
                retryable = code >= 500
            except httpx.TimeoutException:
                retryable = True
            except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                pass
        # Raise outside the except: the original httpx exception may hold the full
        # URL, and `from None` leaves it in __context__.
        if retryable and attempt < attempts:
            _sleep(backoff * 2 ** (attempt - 1))
            continue
        raise FredRequestError(failure)
    raise FredRequestError("FRED observations endpoint request failed")  # pragma: no cover


def _fetch_window(
    client: httpx.Client, params: dict[str, str | int], retry: tuple[int, float]
) -> tuple[int, list[Any]]:
    """Page one query to completion and validate its count."""
    rows: list[Any] = []
    offset = 0
    count: int | None = None
    while count is None or offset < count:
        payload = _get_page(client, params | {"offset": offset}, retry)
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
    return offset, rows


def observations(
    series: str,
    realtime_start: date,
    observation_start: date,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return all output_type=1 pages as one raw payload.

    Authentication is checked before a client is built. The request is split into
    `rates.fred_vintage_window_days` real-time windows, each paged and retried
    (timeouts and 5xx only) per `rates.fred_max_attempts`. Transport and status
    failures are wrapped without a URL or chained exception.
    """
    settings = settings or get_settings()
    key = settings.fred_api_key
    secret = key.get_secret_value() if key is not None else ""
    if not secret.strip():
        raise FredCredentialsError("FRED_API_KEY must be set before fetching FRED rates")
    cfg = settings.rates
    params: dict[str, str | int] = {
        "series_id": series,
        "api_key": secret,
        "file_type": "json",
        "output_type": 1,
        "limit": _LIMIT,
        "observation_start": observation_start.isoformat(),
    }
    retry = (cfg.fred_max_attempts, cfg.fred_backoff_seconds)
    owned = client is None
    if client is None:
        client = httpx.Client(timeout=cfg.fred_timeout_seconds)
    try:
        parts: list[tuple[date, date, list[Any]]] = []
        today = datetime.now(UTC).date()
        for start, end in _vintage_windows(realtime_start, cfg.fred_vintage_window_days, today):
            window = params | {
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
            }
            _, part = _fetch_window(client, window, retry)
            parts.append((start, end, part))
        rows = _merge_windows(parts)
        return {"count": len(rows), "observations": rows}
    finally:
        if owned:
            client.close()

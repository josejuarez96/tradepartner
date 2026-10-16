"""Batched, paced OpenFIGI V3 mapping; errors never include the API key."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tradepartner.cli_record import SCRUBBED, scrub_json
from tradepartner.config import OpenfigiConfig

_ENDPOINT = "https://api.openfigi.com/v3/mapping"


def _api_key() -> str:
    """The key sent on the wire, from the process environment only."""
    return os.environ.get("OPENFIGI_API_KEY", "").strip()


def map_tickers(
    tickers: Sequence[str],
    config: OpenfigiConfig,
    *,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[list[Any], datetime, datetime]]:
    """Return (response, sent, received) for each batch in request order."""
    if not config.enabled:
        raise ValueError("OpenFIGI client is disabled")
    key = _api_key()
    requests, window, jobs = config.limits(keyed=bool(key))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    owned = client is None
    client = client or httpx.Client(timeout=30)
    recent: deque[float] = deque()
    batches: list[tuple[list[Any], datetime, datetime]] = []
    try:
        for offset in range(0, len(tickers), jobs):
            body = [
                {"idType": "TICKER", "idValue": ticker, "exchCode": "US"}
                for ticker in tickers[offset : offset + jobs]
            ]
            for attempt in range(2):
                now = monotonic()
                while recent and now - recent[0] >= window:
                    recent.popleft()
                if len(recent) >= requests:
                    sleep(max(0, window - (now - recent[0])))
                    now = monotonic()
                    while recent and now - recent[0] >= window:
                        recent.popleft()
                recent.append(now)
                sent = clock()
                try:
                    response = client.post(_ENDPOINT, json=body, headers=headers)
                    if response.status_code == 429 and attempt == 0:
                        sleep(window)
                        continue
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    raise RuntimeError("OpenFIGI mapping request failed") from None
                received = clock()
                break
            if not isinstance(payload, list) or len(payload) != len(body):
                raise ValueError("OpenFIGI mapping response has wrong length")
            batches.append((payload, sent, received))
    finally:
        if owned:
            client.close()
    return batches


def record_mapping_fixture(path: Path, tickers: Sequence[str], responses: list[Any]) -> None:
    """Write a scrubbed mapping fixture; never serialize the environment key."""
    raw_key = os.environ.get("OPENFIGI_API_KEY", "")
    key = _api_key()
    document = {
        "tickers": list(tickers),
        "responses": responses,
        "headers": {"X-OPENFIGI-APIKEY": SCRUBBED} if key else {},
    }
    clean, _ = scrub_json(document, secrets=[value for value in (raw_key, key) if value])
    path.write_text(json.dumps(clean, indent=2) + "\n")

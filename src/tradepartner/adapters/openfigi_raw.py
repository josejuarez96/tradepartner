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

from tradepartner.cli_record import scrub_json
from tradepartner.config import OpenfigiConfig

_ENDPOINT = "https://api.openfigi.com/v3/mapping"


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
    key = os.environ.get("OPENFIGI_API_KEY", "").strip()
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
            now = monotonic()
            while recent and now - recent[0] >= window:
                recent.popleft()
            if len(recent) >= requests:
                sleep(max(0, window - (now - recent[0])))
                now = monotonic()
                while recent and now - recent[0] >= window:
                    recent.popleft()
            recent.append(now)
            body = [
                {"idType": "TICKER", "idValue": ticker, "exchCode": "US"}
                for ticker in tickers[offset : offset + jobs]
            ]
            sent = clock()
            try:
                response = client.post(_ENDPOINT, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                raise RuntimeError("OpenFIGI mapping request failed") from None
            received = clock()
            if not isinstance(payload, list) or len(payload) != len(body):
                raise ValueError("OpenFIGI mapping response has wrong length")
            batches.append((payload, sent, received))
    finally:
        if owned:
            client.close()
    return batches


def record_mapping_fixture(path: Path, tickers: Sequence[str], responses: list[Any]) -> None:
    """Write a scrubbed mapping fixture; never serialize the environment key."""
    key = os.environ.get("OPENFIGI_API_KEY", "")
    document = {
        "tickers": list(tickers),
        "responses": responses,
        "headers": {"X-OPENFIGI-APIKEY": key} if key else {},
    }
    clean, _ = scrub_json(document, secrets=[key] if key else [])
    path.write_text(json.dumps(clean, indent=2) + "\n")

"""Network smoke test for `adapters.alpaca_raw` against the real Alpaca API.

Skipped unless `RUN_NETWORK_TESTS=1` is set alongside real
`ALPACA_API_KEY`/`ALPACA_API_SECRET` in the environment/`.env`. This is not
a parser test (T12 owns those on recorded fixtures) — it only proves the
raw client talks to the real API and returns JSON-serializable plain
dicts/lists, never SDK model objects. For offline coverage (mocked SDK
call, no real network) of the `corporate_actions` paging-limit fix and
credential errors, see `test_alpaca_raw_offline.py`.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from tradepartner.adapters import alpaca_raw

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("RUN_NETWORK_TESTS") != "1",
        reason="network test; set RUN_NETWORK_TESTS=1 to run",
    ),
]


def test_daily_bars_returns_json_serializable_raw_payload() -> None:
    payload = alpaca_raw.daily_bars(["SPY"], date(2024, 1, 2), date(2024, 1, 5))

    assert payload["feed"] == alpaca_raw.DEFAULT_FEED.value
    assert isinstance(payload["bars"], dict)
    json.dumps(payload)  # raises if any SDK model object leaked through


def test_corporate_actions_returns_json_serializable_raw_payload() -> None:
    payload = alpaca_raw.corporate_actions(["SPY"], date(2024, 1, 1), date(2024, 3, 31))
    json.dumps(payload)


def test_assets_snapshot_returns_json_serializable_raw_payload() -> None:
    payload = alpaca_raw.assets_snapshot(["SPY"])

    assert isinstance(payload, list)
    assert len(payload) == 1
    assert isinstance(payload[0], dict)
    json.dumps(payload)

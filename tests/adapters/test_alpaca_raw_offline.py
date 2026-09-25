"""Offline tests for `adapters.alpaca_raw`: no real network access.

`alpaca-py` isn't built on `httpx`, so these monkeypatch the SDK client
methods directly rather than injecting a mock transport -- the point is to
capture exactly what request object reaches the SDK, before any HTTP call
would happen (T2 review round 2, safety-reviewer MUST FIX/tests).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest

from tradepartner.adapters import alpaca_raw
from tradepartner.config import Settings


def _settings(
    *, api_key: str | None = "PKFAKE1234567890ABCD", api_secret: str | None = "SKFAKE1234567890ABCD"
) -> Settings:
    return Settings(_env_file=None, alpaca_api_key=api_key, alpaca_api_secret=api_secret)


def test_corporate_actions_request_carries_no_total_limit_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CorporateActionsRequest.limit` defaults to a 1000-item *total* cap across
    every page (verified against the installed alpaca-py source); the raw
    client must pass `limit=None` so a range with more than 1000 actions
    isn't silently truncated."""
    captured: dict[str, CorporateActionsRequest] = {}

    def fake_get_corporate_actions(
        self: CorporateActionsClient, request_params: CorporateActionsRequest
    ) -> dict[str, Any]:
        captured["request"] = request_params
        return {"corporate_actions": {}}

    monkeypatch.setattr(CorporateActionsClient, "get_corporate_actions", fake_get_corporate_actions)

    alpaca_raw.corporate_actions(
        ["SPY"], date(2020, 1, 1), date(2020, 12, 31), settings=_settings()
    )

    assert captured["request"].limit is None
    assert "limit" not in captured["request"].to_request_fields()


def test_alpaca_credentials_error_when_api_key_missing() -> None:
    with pytest.raises(alpaca_raw.AlpacaCredentialsError):
        alpaca_raw.assets_snapshot(["SPY"], settings=_settings(api_key=None))


def test_alpaca_credentials_error_when_api_secret_blank() -> None:
    with pytest.raises(alpaca_raw.AlpacaCredentialsError):
        alpaca_raw.assets_snapshot(["SPY"], settings=_settings(api_secret="   "))

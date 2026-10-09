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
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest

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


# --- #789: symbol batching (an unbatched 9,500-symbol GET returned HTTP 414) ---


def _batched_settings(per_request: int) -> Settings:
    return Settings(
        _env_file=None,
        alpaca_api_key="PKFAKE1234567890ABCD",
        alpaca_api_secret="SKFAKE1234567890ABCD",
        alpaca={"symbols_per_request": per_request},
    )


def test_symbols_per_request_defaults_to_1000() -> None:
    assert Settings(_env_file=None).alpaca.symbols_per_request == 1000


def test_daily_bars_batches_symbols_and_merges_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[str]] = []

    def fake_get_stock_bars(
        self: StockHistoricalDataClient, request_params: StockBarsRequest
    ) -> dict[str, Any]:
        symbols = list(request_params.symbol_or_symbols)
        sent.append(symbols)
        # Two bars per symbol, as the SDK's paging loop would merge them.
        return {s: [{"t": "2026-09-30", "c": 1.0}, {"t": "2026-10-01", "c": 2.0}] for s in symbols}

    monkeypatch.setattr(StockHistoricalDataClient, "get_stock_bars", fake_get_stock_bars)
    symbols = ["A", "B", "C", "D", "E"]

    out = alpaca_raw.daily_bars(
        symbols, date(2026, 9, 30), date(2026, 10, 1), settings=_batched_settings(2)
    )

    assert sent == [["A", "B"], ["C", "D"], ["E"]]
    assert list(out["bars"]) == symbols
    assert sum(len(v) for v in out["bars"].values()) == 10
    assert out["feed"] == "sip"


def test_daily_bars_one_failing_batch_fails_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_get_stock_bars(
        self: StockHistoricalDataClient, request_params: StockBarsRequest
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("HTTP 414 Request-URI Too Large")
        return {s: [{"c": 1.0}] for s in request_params.symbol_or_symbols}

    monkeypatch.setattr(StockHistoricalDataClient, "get_stock_bars", fake_get_stock_bars)

    with pytest.raises(RuntimeError, match="414"):
        alpaca_raw.daily_bars(
            ["A", "B", "C", "D", "E"],
            date(2026, 9, 30),
            date(2026, 9, 30),
            settings=_batched_settings(2),
        )


def test_corporate_actions_batches_symbols_and_merges_per_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[list[str]] = []

    def fake_get_corporate_actions(
        self: CorporateActionsClient, request_params: CorporateActionsRequest
    ) -> dict[str, Any]:
        symbols = list(request_params.symbols or [])
        sent.append(symbols)
        assert request_params.limit is None
        return {
            "cash_dividends": [{"symbol": s} for s in symbols],
            "forward_splits": [{"symbol": s} for s in symbols if s == "C"],
        }

    monkeypatch.setattr(CorporateActionsClient, "get_corporate_actions", fake_get_corporate_actions)

    out = alpaca_raw.corporate_actions(
        ["A", "B", "C"], date(2026, 1, 1), date(2026, 3, 31), settings=_batched_settings(2)
    )

    assert sent == [["A", "B"], ["C"]]
    assert out == {
        "cash_dividends": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}],
        "forward_splits": [{"symbol": "C"}],
    }


def test_corporate_actions_one_failing_batch_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_corporate_actions(
        self: CorporateActionsClient, request_params: CorporateActionsRequest
    ) -> dict[str, Any]:
        if "C" in (request_params.symbols or []):
            raise RuntimeError("HTTP 500")
        return {"cash_dividends": [{"symbol": s} for s in request_params.symbols or []]}

    monkeypatch.setattr(CorporateActionsClient, "get_corporate_actions", fake_get_corporate_actions)

    with pytest.raises(RuntimeError, match="500"):
        alpaca_raw.corporate_actions(
            ["A", "B", "C"], date(2026, 1, 1), date(2026, 3, 31), settings=_batched_settings(2)
        )


def test_daily_bars_sends_a_repeated_symbol_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[str]] = []

    def fake_get_stock_bars(
        self: StockHistoricalDataClient, request_params: StockBarsRequest
    ) -> dict[str, Any]:
        symbols = list(request_params.symbol_or_symbols)
        sent.append(symbols)
        return {s: [{"c": 1.0}] for s in symbols}

    monkeypatch.setattr(StockHistoricalDataClient, "get_stock_bars", fake_get_stock_bars)

    out = alpaca_raw.daily_bars(
        ["A", "B", "A"], date(2026, 9, 30), date(2026, 9, 30), settings=_batched_settings(2)
    )

    assert sent == [["A", "B"]]
    assert sum(len(v) for v in out["bars"].values()) == 2


# --- #816: an action naming two symbols comes back in both symbols' batches ---

# The live record that stopped the owner's backfill at 2026-03: Alpaca returns
# it to the batch holding `symbol` (GMGI) and to the batch holding `new_symbol`
# (MRDN), byte-identical both times.
_GMGI_REVERSE_SPLIT: dict[str, Any] = {
    "ex_date": "2026-03-03",
    "id": "b1b6b98c-3e47-4a29-b2a3-be259155ddc3",
    "new_cusip": "381098409",
    "new_rate": 1,
    "new_symbol": "MRDN",
    "old_cusip": "381098300",
    "old_rate": 12,
    "payable_date": "2026-03-03",
    "process_date": "2026-03-03",
    "record_date": "2026-03-03",
    "symbol": "GMGI",
}


def _actions_by_batch(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, dict[str, Any]]
) -> list[list[str]]:
    """Patch the SDK call to answer each batch with `responses[<first symbol>]`."""
    sent: list[list[str]] = []

    def fake_get_corporate_actions(
        self: CorporateActionsClient, request_params: CorporateActionsRequest
    ) -> dict[str, Any]:
        symbols = list(request_params.symbols or [])
        sent.append(symbols)
        return responses[symbols[0]]

    monkeypatch.setattr(CorporateActionsClient, "get_corporate_actions", fake_get_corporate_actions)
    return sent


def test_corporate_actions_drops_an_identical_action_repeated_across_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dividend = {"id": "d-1", "symbol": "AAPL", "rate": 0.26, "ex_date": "2026-03-09"}
    sent = _actions_by_batch(
        monkeypatch,
        {
            "GMGI": {"reverse_splits": [dict(_GMGI_REVERSE_SPLIT)], "cash_dividends": [dividend]},
            "MRDN": {"reverse_splits": [dict(_GMGI_REVERSE_SPLIT)], "cash_dividends": []},
        },
    )

    out = alpaca_raw.corporate_actions(
        ["GMGI", "AAPL", "MRDN"],
        date(2026, 2, 15),
        date(2026, 5, 15),
        settings=_batched_settings(2),
    )

    assert sent == [["GMGI", "AAPL"], ["MRDN"]]
    assert out == {"reverse_splits": [_GMGI_REVERSE_SPLIT], "cash_dividends": [dividend]}


def test_corporate_actions_fails_on_differing_records_with_one_id_across_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revised = {**_GMGI_REVERSE_SPLIT, "old_rate": 10}
    _actions_by_batch(
        monkeypatch,
        {
            "GMGI": {"reverse_splits": [dict(_GMGI_REVERSE_SPLIT)]},
            "MRDN": {"reverse_splits": [revised]},
        },
    )

    with pytest.raises(ValueError, match="b1b6b98c-3e47-4a29-b2a3-be259155ddc3"):
        alpaca_raw.corporate_actions(
            ["GMGI", "MRDN"], date(2026, 2, 15), date(2026, 5, 15), settings=_batched_settings(1)
        )


def test_corporate_actions_keeps_a_repeat_within_one_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only repeats across batches are a batching artefact; a repeat inside one
    # Alpaca response passes through for the parser to refuse.
    _actions_by_batch(
        monkeypatch,
        {"GMGI": {"reverse_splits": [dict(_GMGI_REVERSE_SPLIT), dict(_GMGI_REVERSE_SPLIT)]}},
    )

    out = alpaca_raw.corporate_actions(
        ["GMGI", "MRDN"], date(2026, 2, 15), date(2026, 5, 15), settings=_batched_settings(2)
    )

    assert out == {"reverse_splits": [_GMGI_REVERSE_SPLIT, _GMGI_REVERSE_SPLIT]}


# --- #1314: asof names the company that held a reused ticker that day ---


@pytest.mark.parametrize(("asof", "sent_asof"), [(None, None), (date(2017, 6, 1), "2017-06-01")])
def test_daily_bars_sends_asof_on_every_batch(
    monkeypatch: pytest.MonkeyPatch, asof: date | None, sent_asof: str | None
) -> None:
    sent: list[str | None] = []

    def fake_get_stock_bars(
        self: StockHistoricalDataClient, request_params: StockBarsRequest
    ) -> dict[str, Any]:
        sent.append(request_params.asof)
        return {}

    monkeypatch.setattr(StockHistoricalDataClient, "get_stock_bars", fake_get_stock_bars)
    alpaca_raw.daily_bars(
        ["VAL", "AAPL", "KO"],
        date(2017, 2, 1),
        date(2017, 2, 3),
        asof=asof,
        settings=_batched_settings(2),
    )
    assert sent == [sent_asof, sent_asof]

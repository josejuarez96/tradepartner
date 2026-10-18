"""The broker form of a listing's ticker on the order path (#1729)."""

from __future__ import annotations

import pytest

from tradepartner.execution.symbols import broker_symbol

CLASS_SYMBOLS = {"BFB": "BF.B"}


@pytest.mark.parametrize(
    ("ticker", "exchange", "expected"),
    [
        ("GEF-B", "NYSE", "GEF.B"),
        ("BRK-B", "NYSE", "BRK.B"),
        ("BRK/B", "NYSE", "BRK.B"),
        ("bf-b", "NYSE", "BF.B"),
        ("BFB", "NYSE", "BF.B"),
        ("HEI-A", "NYSE", "HEI.A"),
        ("BRK.B", "NYSE", "BRK.B"),
        ("AAPL", "NASDAQ", "AAPL"),
        ("SENEB", "NASDAQ", "SENEB"),
    ],
)
def test_a_listing_maps_to_alpacas_symbol(ticker: str, exchange: str, expected: str) -> None:
    assert broker_symbol({"ticker": ticker, "exchange": exchange}, CLASS_SYMBOLS) == expected


@pytest.mark.parametrize("ticker", ["BRK B", "BAC-PL", "XYZ-WS"])
def test_a_ticker_with_no_broker_form_is_none(ticker: str) -> None:
    assert broker_symbol({"ticker": ticker, "exchange": "NYSE"}, CLASS_SYMBOLS) is None

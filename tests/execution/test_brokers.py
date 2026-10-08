"""`execution.brokers.build_broker` (plan T48c): production-only, the Alpaca
paper adapter and nothing else, with no selector."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from typing import Any

from alpaca.common.enums import BaseURL

from tradepartner.adapters.alpaca_broker import AlpacaBroker
from tradepartner.config import Settings
from tradepartner.execution import brokers
from tradepartner.execution.brokers import build_broker

PAPER_KEY = "PKPAPERFAKE1234567890"  # gitleaks:allow
PAPER_SECRET = "paperSecretFake1234567890abcdefghij"  # gitleaks:allow


def _clock() -> datetime:
    return datetime(2026, 10, 8, 15, 11, tzinfo=UTC)


def test_the_factory_returns_the_alpaca_adapter_on_the_paper_endpoint() -> None:
    settings = Settings(
        _env_file=None, alpaca_paper_api_key=PAPER_KEY, alpaca_paper_api_secret=PAPER_SECRET
    )
    broker = build_broker(settings, _clock)

    assert type(broker) is AlpacaBroker
    assert broker.clock is _clock
    client: Any = broker._raw._client
    assert client._base_url == BaseURL.TRADING_PAPER


def test_the_factory_accepts_no_other_selector() -> None:
    params = inspect.signature(build_broker).parameters
    assert list(params) == ["settings", "clock"]
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_the_factory_module_names_no_other_broker() -> None:
    tree = ast.parse(inspect.getsource(brokers))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names
    }
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "FakeBroker" not in names and "tradepartner.adapters.fake_broker" not in modules
    assert "AlpacaBroker" in names

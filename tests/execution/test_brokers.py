"""`execution.brokers.build_broker` (plan T48c): production-only, the Alpaca
paper adapter and nothing else, with no selector but the book, which picks only
that book's own paper key pair (ADR 0017 B.2, plan T153)."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from typing import Any

import pytest
from alpaca.common.enums import BaseURL

from tradepartner.adapters import alpaca_trading_raw as raw_mod
from tradepartner.adapters.alpaca_broker import AlpacaBroker
from tradepartner.adapters.alpaca_trading_raw import (
    AlpacaPaperCredentialsError,
    AlpacaPaperGuardError,
)
from tradepartner.config import Settings
from tradepartner.execution import brokers
from tradepartner.execution.brokers import build_broker

PAPER_KEY = "PKPAPERFAKE1234567890"  # gitleaks:allow
PAPER_SECRET = "paperSecretFake1234567890abcdefghij"  # gitleaks:allow
B_KEY = "PKBOOKBFAKE0987654321"  # gitleaks:allow
B_SECRET = "bookBSecretFake0987654321zyxwvutsrq"  # gitleaks:allow
DATA_KEY = "AKDATAFAKE1234567890"  # gitleaks:allow
DATA_SECRET = "dataSecretFake1234567890abcdef"  # gitleaks:allow


def _settings(**books: dict[str, str]) -> Settings:
    return Settings(
        _env_file=None,
        alpaca_paper_api_key=PAPER_KEY,
        alpaca_paper_api_secret=PAPER_SECRET,
        alpaca_api_key=DATA_KEY,
        alpaca_api_secret=DATA_SECRET,
        alpaca_paper_books=books,
    )


def _clock() -> datetime:
    return datetime(2026, 10, 8, 15, 11, tzinfo=UTC)


def test_the_factory_returns_the_alpaca_adapter_on_the_paper_endpoint() -> None:
    settings = Settings(
        _env_file=None, alpaca_paper_api_key=PAPER_KEY, alpaca_paper_api_secret=PAPER_SECRET
    )
    broker = build_broker(settings, _clock, "main")

    assert type(broker) is AlpacaBroker
    assert broker.clock is _clock
    client: Any = broker._raw._client
    assert client._base_url == BaseURL.TRADING_PAPER


def test_the_factory_accepts_no_other_selector() -> None:
    params = inspect.signature(build_broker).parameters
    assert list(params) == ["settings", "clock", "book_id"]
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_the_factory_module_names_no_other_broker() -> None:
    tree = ast.parse(inspect.getsource(brokers))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names
    }
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "FakeBroker" not in names and "tradepartner.adapters.fake_broker" not in modules
    assert "AlpacaBroker" in names


def test_the_factory_for_book_b_hands_the_client_bs_pair_and_never_mains() -> None:
    """ADR 0017 B.2 (T153): distinct fake secrets per book; `b`'s raw client is
    built on `b`'s pair, `main`'s on `main`'s, both on the paper endpoint."""
    settings = _settings(b={"api_key": B_KEY, "api_secret": B_SECRET})
    built: dict[str, Any] = {book: build_broker(settings, _clock, book) for book in ("b", "main")}
    b_client: Any = built["b"]._raw._client
    main_client: Any = built["main"]._raw._client
    assert (b_client._api_key, b_client._secret_key) == (B_KEY, B_SECRET)
    assert (main_client._api_key, main_client._secret_key) == (PAPER_KEY, PAPER_SECRET)
    assert b_client._base_url == main_client._base_url == BaseURL.TRADING_PAPER


def test_the_factory_refuses_a_book_with_no_pair_before_any_client_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []
    monkeypatch.setattr(raw_mod, "TradingClient", lambda **kw: built.append(kw))
    with pytest.raises(AlpacaPaperCredentialsError) as err:
        build_broker(_settings(), _clock, "c")
    text = str(err.value)
    assert "ALPACA_PAPER_BOOKS__C__API_KEY" in text
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text
    assert built == []


def test_the_book_never_lifts_the_paper_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ALPACA__PAPER=false` is still refused at load, and a bypassed guard still
    refuses the adapter whatever the book."""
    monkeypatch.setenv("ALPACA__PAPER", "false")
    with pytest.raises(ValueError):
        _settings(b={"api_key": B_KEY, "api_secret": B_SECRET})
    monkeypatch.delenv("ALPACA__PAPER")
    settings = _settings(b={"api_key": B_KEY, "api_secret": B_SECRET})
    bypassed = settings.model_copy(
        update={"alpaca": settings.alpaca.model_copy(update={"paper": False})}
    )
    for book in ("b", "main"):
        with pytest.raises(AlpacaPaperGuardError):
            build_broker(bypassed, _clock, book)


def test_the_cli_default_factory_builds_main_whatever_paper_book_id_says() -> None:
    """Until `--book` (T155b), the `paper` commands build book `main`'s broker on
    `main`'s pair, exactly as before T153, even when the live `paper.book_id` names
    another book (code-review on #1373: a config edit must not move `main`'s window
    onto another account)."""
    from tradepartner import cli

    settings = _settings(b={"api_key": B_KEY, "api_secret": B_SECRET})
    as_b = settings.model_copy(update={"paper": settings.paper.model_copy(update={"book_id": "b"})})
    for s in (settings, as_b):
        broker: Any = cli._main_book_broker(s, _clock)
        assert broker.book_id == "main"
        assert broker._raw._client._api_key == PAPER_KEY

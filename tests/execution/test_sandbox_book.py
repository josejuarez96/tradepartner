"""Book `sandbox` (owner decision 2026-10-28, docs/runbooks/sandbox.md): the regression
book on the third Alpaca paper account. It runs only on a temp COPY of the store, so it
is invisible to the live store's checks; every other book is unaffected."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradepartner.config import SANDBOX_BOOK_ID, Settings, paper_key_variable_names
from tradepartner.execution import lock
from tradepartner.execution.brokers import build_broker
from tradepartner.execution.lock import lock_path, resolve_book, sandbox_store_refusal


def _live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings whose store is NOT under any temp root (the live store's situation)."""
    monkeypatch.setattr(lock, "_temp_roots", lambda: {tmp_path / "elsewhere"})
    return Settings(_env_file=None, store={"path": str(tmp_path / "data" / "live.duckdb")})


def test_the_sandbox_keys_are_the_ordinary_book_variables() -> None:
    assert paper_key_variable_names(SANDBOX_BOOK_ID) == (
        "ALPACA_PAPER_BOOKS__SANDBOX__API_KEY",
        "ALPACA_PAPER_BOOKS__SANDBOX__API_SECRET",
    )


def test_sandbox_is_refused_on_a_store_outside_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _live(tmp_path, monkeypatch)
    assert sandbox_store_refusal(s, "sandbox") is not None
    with pytest.raises(ValueError, match="runs only on a temp copy"):
        resolve_book(s, "sandbox")
    with pytest.raises(ValueError, match="runs only on a temp copy"):
        lock_path(s, "sandbox")


def test_the_default_book_cannot_be_turned_into_the_sandbox_on_the_live_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "live.duckdb")},
        paper={"book_id": "sandbox"},
    )
    monkeypatch.setattr(lock, "_temp_roots", lambda: set())
    with pytest.raises(ValueError, match="temp copy"):
        resolve_book(s, None)


def test_build_broker_refuses_before_any_client_or_key_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _live(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="temp copy") as err:
        build_broker(s, lambda: None, "sandbox")  # type: ignore[arg-type,return-value]
    assert "ALPACA" not in str(err.value)


def test_a_symlink_into_temp_does_not_launder_the_live_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = tmp_path / "data"
    live.mkdir()
    temp = tmp_path / "tmp"
    temp.mkdir()
    (temp / "link").symlink_to(live)
    monkeypatch.setattr(lock, "_temp_roots", lambda: {temp.resolve()})
    s = Settings(_env_file=None, store={"path": str(temp / "link" / "tradepartner.duckdb")})
    assert sandbox_store_refusal(s, "sandbox") is not None


def test_sandbox_is_allowed_on_a_temp_copy_and_other_books_are_never_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lock, "_temp_roots", lambda: {tmp_path.resolve()})
    s = Settings(_env_file=None, store={"path": str(tmp_path / "copy.duckdb")})
    assert resolve_book(s, "sandbox") == "sandbox"
    live = _live(tmp_path, monkeypatch)
    for book in ("main", "daily", "sandbox2"):
        assert resolve_book(live, book) == book

"""Backtest test fixtures.

Handles a test fabricates with `registry._issue_handle` carry no real trial, so they bind
to no window or params hash (their `params_sha256` is `FABRICATED_HASH`); the engine's
handle binding (#3533) is skipped for those only. A handle from `registry.open_trial` is
always checked, and `test_engine_handle_binding` covers the refusals.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import date
from typing import Any

import pytest

from tradepartner.backtest import engine

FABRICATED_HASH = "0" * 64


@pytest.fixture(autouse=True)
def _handle_binding(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real: Callable[..., None] = engine._check_handle_binding

    def check(variants: Sequence[tuple[Any, Any]], start: date, end: date) -> None:
        real([v for v in variants if v[1].params_sha256 != FABRICATED_HASH], start, end)

    monkeypatch.setattr(engine, "_check_handle_binding", check)
    engine._CONSUMED_HANDLES.clear()
    yield
    engine._CONSUMED_HANDLES.clear()

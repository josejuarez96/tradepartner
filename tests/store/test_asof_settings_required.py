"""#3645: four `store/asof.py` readers take `settings` from the caller. They
have no default and never load configuration themselves, so a test that
forgets `settings=` fails loudly instead of reading the process environment."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.store import asof

_T = datetime(2024, 6, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    ("reader", "args"),
    [
        (asof.price_jumps_as_of, ()),
        (asof.sec_filing_events_as_of, (["4"],)),
        (asof.sec_form4_transactions_as_of, ()),
        (asof.unvalued_spin_offs_as_of, ()),
    ],
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_the_four_readers_refuse_a_call_without_settings(
    fixture_store: duckdb.DuckDBPyConnection,
    reader: Callable[..., Any],
    args: tuple[object, ...],
) -> None:
    parameter = inspect.signature(reader).parameters["settings"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="settings"):
        reader(fixture_store, _T, *args)

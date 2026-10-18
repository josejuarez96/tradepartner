"""The broker form of a master listing's ticker (#1729).

The master stores a class share as its filer writes it (`GEF-B`, `BRK/B`, NYSE
`BFB`); Alpaca's trading API knows it as `GEF.B`, `BRK.B`, `BF.B`. Every
symbol the order path sends to the broker, or matches against the broker's
positions and orders, goes through `broker_symbol`: the prices adapter's
`master_symbol` rule with `alpaca.class_symbols`, the same rule the daily bars
are fetched under. A ticker with no broker form (a space, a warrant or
preferred suffix) is `None`: the name is never sent, and a buy of it is skipped
as untradable before any order.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tradepartner.adapters.alpaca_prices import master_symbol


def broker_symbol(row: Mapping[str, Any], class_symbols: Mapping[str, str] | None) -> str | None:
    """The broker symbol of a listing `row` (with `ticker` and `exchange`), or
    `None` when its ticker has no broker form (module docstring)."""
    return master_symbol(row, class_symbols)

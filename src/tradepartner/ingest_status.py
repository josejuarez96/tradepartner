"""Ingest run-status constants, kept in a stdlib-only leaf module (#3595).

The risk-gated order wrapper and the operations reader need only these strings
(`ingestion_runs.status`, `.mode`, `.source`). Reading them from here keeps the
order path's import closure free of the large `tradepartner.ingest` module, so an
import-time fault there cannot break order placement. `ingest` re-exports them.
"""

from __future__ import annotations

from typing import Final

OK: Final[str] = "ok"
STALE: Final[str] = "stale"
FAILED: Final[str] = "failed"
LOCKED: Final[str] = "locked"
MODE: Final[str] = "session"
#: The price side's `ingestion_runs.source`. Its `MODE` runs alone count as
#: the paper run's fresh prices (staleness and the clock floor, #1440 Q3).
PRICE_SOURCE: Final[str] = "alpaca"

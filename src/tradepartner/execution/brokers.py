"""The broker factory (plan T48c; ADR 0003 rule 7).

`build_broker` is the one place production code gets a `Broker`: `cli.py`
imports no adapter module (T50's boundary test), so it can reach an
implementation only through here. It is production-only: it returns the Alpaca
**paper** adapter and nothing else, never a fake and never a live endpoint
(`AlpacaBroker` runs over T48's raw client, built with the literal
`paper=True`). No setting selects a broker; tests inject a `FakeBroker` by
parameter instead of through this function. `book_id` selects only which paper
account's key pair the adapter reads (ADR 0017 B.2, plan T153), never an endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from tradepartner.adapters.alpaca_broker import AlpacaBroker
from tradepartner.adapters.broker import Broker
from tradepartner.config import Settings

__all__ = ["build_broker"]


def build_broker(settings: Settings, clock: Callable[[], datetime], book_id: str) -> Broker:
    """The production broker: `AlpacaBroker` on the paper endpoint, over
    `settings` (pacing, broker facts), `book_id`'s own paper key pair (never
    another book's, never the data keys; a book with none is refused before any
    client is built) and the shared `clock`."""
    return AlpacaBroker(settings, clock, book_id=book_id)

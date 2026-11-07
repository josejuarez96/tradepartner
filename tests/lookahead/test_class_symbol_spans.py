"""#3082: a NYSE class listing written without the dot (`BIOb`, Bio-Rad's
class B) resolves under its `alpaca.class_symbols` symbol (`BIO.B`) only
from the listing's `known_at` and only on sessions from its `valid_from`:
the map renames a ticker, it never dates one."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb

from tradepartner.config import Settings
from tradepartner.repair import store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

BIO_B = "0000012208:class-b-common-stock"
LISTED = date(2019, 8, 6)
FILED = datetime(2019, 8, 6, 21, tzinfo=UTC)


def _store(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "listings",
            {
                "security_id": BIO_B,
                "ticker": "BIOb",
                "exchange": "NYSE",
                "class_title": "Class B Common Stock, Par Value $0.0001 per share",
                "valid_from": LISTED,
                "known_at": FILED,
                "ingested_at": FILED,
                "source": "edgar",
                "provenance": "filing",
            },
        )


def test_the_mapped_symbol_resolves_only_once_its_listing_is_known(settings: Settings) -> None:
    _store(settings)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        early = store_resolver(conn, FILED - timedelta(seconds=1), settings)
        known = store_resolver(conn, FILED, settings)
    assert early.resolve("BIO.B", LISTED) is None  # the cover page is not filed yet
    assert known.resolve("BIO.B", LISTED) == BIO_B
    assert known.resolve("BIO.B", LISTED - timedelta(days=1)) is None  # never before valid_from
    assert known.resolve("BIOB", LISTED) is None  # the undotted spelling is no symbol

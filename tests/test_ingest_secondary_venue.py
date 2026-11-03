"""The bar fetch's names follow the universe's venue rule (#2937).

A newer listing row on a secondary quote venue (`NYSE_CHICAGO`, `NYSETX`) used
to become the security's current listing, and `_fetched` / `_counted` then saw
an exchange off `universe.exchanges`: IBM, ABT, MMM and ~20 more live names
stopped getting bars. The fetch now passes a secondary row while the security
has a live row on the list (universe rule 2, T196), in the daily ingest
(`_price_names`) and in the backfill windows (`_window_names`), and still
refuses OTC / NONE / a name with no row on the list.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from tradepartner.adapters.alpaca_prices import ListingResolver
from tradepartner.backfill import _window_names
from tradepartner.config import Settings
from tradepartner.ingest import _price_names
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import init_schema

NOW = datetime(2021, 6, 30, 21, tzinfo=UTC)
SESSION = date(2021, 6, 29)
RUN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
COMMON = {"ingested_at": RUN, "source": "edgar", "provenance": "filing"}
FILED = datetime(2015, 3, 1, 21, tzinfo=UTC)
OLD = (date(2019, 1, 2), datetime(2019, 1, 2, 21, tzinfo=UTC))
NEW = (date(2020, 6, 1), datetime(2020, 6, 1, 21, tzinfo=UTC))
LATE_KNOWN = datetime(2021, 9, 1, 21, tzinfo=UTC)

#: security -> its listing rows (exchange, valid_from, known_at).
CASES: dict[str, list[tuple[str, date, datetime]]] = {
    "CHI": [("NYSE", *OLD), ("NYSE_CHICAGO", *NEW)],
    "TX": [("NASDAQ", *OLD), ("NYSETX", *NEW)],
    "OTC": [("NYSE", *OLD), ("OTC", *NEW)],
    "NONE": [("NYSE", *OLD), ("NONE", *NEW)],
    "OTCQX": [("NYSE", *OLD), ("OTCQX", *NEW)],
    "ENDED": [("NYSE", *OLD), ("NYSE_CHICAGO", *NEW)],
    "ONLYCHI": [("NYSE_CHICAGO", *OLD)],
    "ARCA": [("NYSE_ARCA", *OLD), ("NYSE_CHICAGO", *NEW)],
    "PLAIN": [("NYSE", *OLD)],
    # The list row is known only after T: at T the name has one Chicago row.
    "LATEOLD": [("NYSE", OLD[0], LATE_KNOWN), ("NYSE_CHICAGO", *NEW)],
}


def _build(
    conn: duckdb.DuckDBPyConnection, cases: dict[str, list[tuple[str, date, datetime]]]
) -> None:
    for sid, rows in cases.items():
        insert_row(
            conn,
            "securities",
            {"security_id": sid, "cik": sid, "name": sid, "known_at": FILED, **COMMON},
        )
        insert_row(
            conn,
            "classifications",
            {
                "security_id": sid,
                "sic": 3570,
                "security_type": "common",
                "rule": "common_default",
                "known_at": FILED,
                **COMMON,
            },
        )
        for exchange, valid_from, known_at in rows:
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": sid,
                    "exchange": exchange,
                    "class_title": "Common Stock",
                    "valid_from": valid_from,
                    "known_at": known_at,
                    **COMMON,
                },
            )
    # ENDED: a Form 25 for its NYSE class long before the Chicago row.
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "ENDED",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": datetime(2020, 2, 3, 21, tzinfo=UTC),
            "effective_on": date(2020, 2, 13),
            "known_at": datetime(2020, 2, 3, 21, tzinfo=UTC),
            **COMMON,
        },
    )


FETCHED = {"CHI", "TX", "PLAIN"}
REFUSED = {"OTC", "NONE", "OTCQX", "ONLYCHI", "ARCA", "ENDED"}


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    configure_connection(c)
    init_schema(c)
    _build(c, CASES)
    yield c
    c.close()


def test_the_daily_fetch_keeps_a_name_whose_newest_row_is_a_secondary_venue(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    fetch, listed, *_ = _price_names(conn, NOW, SESSION, Settings(_env_file=None))
    assert fetch >= FETCHED
    assert listed >= FETCHED  # and the staleness denominator counts them
    assert not REFUSED & fetch
    assert not REFUSED & listed


def test_a_backfill_window_fetches_such_a_name_too(conn: duckdb.DuckDBPyConnection) -> None:
    window = (date(2021, 6, 1), date(2021, 6, 29))
    ids, listed, *_ = _window_names(
        conn, NOW, window, Settings(_env_file=None), ListingResolver([])
    )
    assert set(ids) >= FETCHED
    assert set(listed) >= FETCHED
    assert not REFUSED & set(ids)


def test_a_row_known_after_t_moves_nothing(conn: duckdb.DuckDBPyConnection) -> None:
    """LATEOLD's list row is known only after T, so at T it has just a Chicago
    row and is not fetched; once known it is."""
    settings = Settings(_env_file=None)
    assert "LATEOLD" not in _price_names(conn, NOW, SESSION, settings)[0]
    later = datetime(2021, 10, 1, 21, tzinfo=UTC)
    assert "LATEOLD" in _price_names(conn, later, date(2021, 9, 30), settings)[0]

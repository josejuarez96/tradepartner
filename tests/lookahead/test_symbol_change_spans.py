"""#1696 (T192): a stored symbol change bounds the resolver's spans only
from its `known_at`; one known after `t` bounds nothing at `t`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.repair import store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

PKI = "0000031791"
RENAME = date(2023, 5, 16)
KNOWN = session_close(date(2023, 5, 17))  # Alpaca processed it a day late
FILED = datetime(2019, 8, 6, 21, tzinfo=UTC)


def _store(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "listings",
            {
                "security_id": PKI,
                "ticker": "PKI",
                "exchange": "NYSE",
                "class_title": "Common stock",
                "valid_from": date(2019, 8, 6),
                "known_at": FILED,
                "ingested_at": FILED,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": PKI,
                "old_symbol": "PKI",
                "new_symbol": "RVTY",
                "old_cusip": "714046109",
                "new_cusip": "714046109",
                "process_date": RENAME,
                "source_action_id": "pki-rvty",
                "known_at": KNOWN,
                "ingested_at": KNOWN,
                "source": "alpaca",
                "provenance": "action",
            },
        )


def test_a_change_known_after_t_bounds_nothing_at_t(settings: Settings) -> None:
    _store(settings)
    day = date(2023, 5, 16)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        early = store_resolver(conn, KNOWN - timedelta(seconds=1), settings)
        known = store_resolver(conn, KNOWN, settings)
    assert early.resolve("PKI", day) == PKI  # the change is not known yet
    assert early.resolve("RVTY", day) is None
    assert early.report.symbol_changes_followed == 0
    assert known.resolve("PKI", day) is None
    assert known.resolve("RVTY", day) == PKI
    assert known.report.symbol_changes_followed == 1

"""#3054: a stored name change gives a renamed security its pre-rename symbol's
corporate actions only from the change's `known_at`; at a `t` before it the
old symbol resolves to no one, as before the change was stored."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.repair import store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

AUB = "0000883948"
RENAME = date(2019, 5, 20)  # UBSH -> AUB
KNOWN = session_close(date(2020, 7, 1))  # the change scanned long after it happened
SNAPSHOT = datetime(2019, 1, 2, 21, tzinfo=UTC)


def _store(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "listings",
            {
                "security_id": AUB,
                "ticker": "AUB",
                "exchange": "NASDAQ",
                "class_title": "Common stock",
                "valid_from": date(2016, 1, 4),
                "known_at": SNAPSHOT,
                "ingested_at": SNAPSHOT,
                "source": "edgar",
                "provenance": "snapshot_static",
            },
        )
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": AUB,
                "old_symbol": "UBSH",
                "new_symbol": "AUB",
                "old_cusip": "90539J109",
                "new_cusip": "04911A107",
                "process_date": RENAME,
                "source_action_id": "ubsh-aub",
                "known_at": KNOWN,
                "ingested_at": KNOWN,
                "source": "alpaca",
                "provenance": "action",
            },
        )


def test_the_alias_is_read_only_from_the_changes_known_at(settings: Settings) -> None:
    _store(settings)
    day = date(2016, 2, 9)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        early = store_resolver(conn, KNOWN - timedelta(seconds=1), settings)
        known = store_resolver(conn, KNOWN, settings)
    assert early.action_alias("UBSH", day) is None
    assert early.alias_symbols(AUB, day, day) == []
    assert known.action_alias("UBSH", day) == AUB
    assert known.alias_symbols(AUB, day, day) == ["UBSH"]
    # Bars never take the alias: the old symbol still resolves to no one.
    assert known.resolve("UBSH", day) is None

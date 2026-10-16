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


def _listing_row(sid: str, ticker: str, valid_from: date, known: datetime) -> dict[str, object]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": "Class B Common Stock",
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


def test_a_later_rename_never_changes_an_earlier_read(settings: Settings) -> None:
    """#1696: a rename into a ticker known only later (Banzai's BNZI -> PARA,
    2026) never changes how the resolver read the ticker's earlier, ended
    span (Paramount's PARA) before that rename was known, nor after."""
    paramount, banzai = "0000813828:class-b-common-stock", "0001826011:class-a-common-stock"
    early = datetime(2025, 9, 2, 21, tzinfo=UTC)
    late = datetime(2026, 8, 14, 21, tzinfo=UTC)
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _listing_row(
                paramount, "VIAC", date(2020, 2, 20), datetime(2020, 2, 20, 21, tzinfo=UTC)
            ),
            _listing_row(paramount, "PARA", date(2022, 5, 3), datetime(2022, 5, 3, 21, tzinfo=UTC)),
            _listing_row(paramount, "N/A", date(2025, 8, 17), early),
            _listing_row(banzai, "BNZI", date(2024, 4, 1), datetime(2024, 4, 1, 21, tzinfo=UTC)),
            _listing_row(banzai, "PARA", date(2026, 8, 7), late),
        ):
            insert_row(conn, "listings", row)
    days = [date(2022, 5, 3), date(2023, 6, 1), date(2025, 8, 15)]
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        before = store_resolver(conn, late - timedelta(seconds=1), settings)
        after = store_resolver(conn, late, settings)
    assert after.resolve("PARA", date(2026, 9, 1)) == banzai  # the rename is known
    assert before.resolve("PARA", date(2026, 9, 1)) is None
    for day in days:
        assert before.resolve("PARA", day) == paramount
        assert after.resolve("PARA", day) == before.resolve("PARA", day)

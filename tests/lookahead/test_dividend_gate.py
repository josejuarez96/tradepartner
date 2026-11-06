"""No look-ahead in the reviewed-list dividend gate (#3015).

The gate classes only the dividend rows known at `t`: a second identity of an
equal-amount payment known after `t` cannot leave the first unapplied at `t`. The gated
reads on the full store equal the same reads on the store truncated to `known_at <= t`
at every probe, and the direct assertion pins both sides of the second id's `known_at`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.store import schema
from tradepartner.store.asof import (
    UNEXPLAINED_DUPLICATE,
    adjusted_prices_as_of,
    dropped_dividends_as_of,
)
from tradepartner.store.db import configure_connection, insert_row

SID = "SEC_DUP_LATE"
SESSIONS = [date(2021, 1, d) for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15)]
EX = date(2021, 1, 11)
FIRST_KNOWN = datetime(2021, 1, 8, 21, tzinfo=UTC)
#: The vendor's second record of the same payment arrives after the ex-date.
SECOND_KNOWN = datetime(2021, 1, 13, 21, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for session in SESSIONS:
        known_at = datetime(session.year, session.month, session.day, 21, tzinfo=UTC)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": SID,
                "session": session,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "test",
                "provenance": "bar",
            },
        )
    for action_id, known_at in (("a", FIRST_KNOWN), ("b", SECOND_KNOWN)):
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": SID,
                "action_type": "dividend",
                "ex_date": EX,
                "ratio_or_amount": 1.0,
                "source_action_id": action_id,
                "cancelled": False,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "test",
                "provenance": "action",
            },
        )
    try:
        yield conn
    finally:
        conn.close()


def test_gated_reads_invariant_under_truncation(store: duckdb.DuckDBPyConnection) -> None:
    truncated = TruncatedStore(store)
    try:
        for t in probe_timestamps(store):
            cut = truncated.at(t)
            full = adjusted_prices_as_of(store, t, include_dividends=True, dividend_reviews=())
            got = adjusted_prices_as_of(cut, t, include_dividends=True, dividend_reviews=())
            assert full.equals(got), f"adjusted disagreed at T={t!r}"
            full_dropped = dropped_dividends_as_of(store, t, dividend_reviews=())
            got_dropped = dropped_dividends_as_of(cut, t, dividend_reviews=())
            assert full_dropped.equals(got_dropped), f"dropped disagreed at T={t!r}"
    finally:
        truncated.close()


def test_a_second_record_known_later_gates_only_from_its_known_at(
    store: duckdb.DuckDBPyConnection,
) -> None:
    def close_before_ex(t: datetime) -> float:
        frame = adjusted_prices_as_of(store, t, include_dividends=True, dividend_reviews=())
        return float(frame.filter(pl.col("session") == date(2021, 1, 8))["close"].item())

    before = datetime(2021, 1, 12, 22, tzinfo=UTC)
    after = datetime(2021, 1, 14, 22, tzinfo=UTC)
    assert close_before_ex(before) == pytest.approx(99.0)
    assert dropped_dividends_as_of(store, before, dividend_reviews=()).is_empty()
    assert close_before_ex(after) == pytest.approx(100.0)
    reasons = dropped_dividends_as_of(store, after, dividend_reviews=())["reason"].to_list()
    assert reasons == [UNEXPLAINED_DUPLICATE, UNEXPLAINED_DUPLICATE]

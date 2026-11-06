"""No look-ahead in the paper card's gated SPY total-return read (#3026).

`ops._benchmark_series` gates SPY's dividends on the reviewed list and fails closed on
an unexplained equal-amount group. Only the rows known at `as_of` are classed: a second
record of a payment known after `as_of` cannot fail the line at `as_of`. The read on the
full store equals the read on the store truncated to `known_at <= t` at every probe of
the bar and action stamps (the identity rows are known years before), and the direct
assertion pins both sides of the second record's `known_at`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.config import Settings
from tradepartner.execution import ops
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row

SPY = "BENCH:SPY"
SESSIONS = [date(2021, 1, d) for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15)]
EX = date(2021, 1, 11)
IDENTITY_KNOWN = datetime(2016, 1, 4, 21, tzinfo=UTC)
FIRST_KNOWN = datetime(2021, 1, 8, 21, tzinfo=UTC)
#: The vendor's second record of the same payment arrives after the ex-date.
SECOND_KNOWN = datetime(2021, 1, 13, 21, tzinfo=UTC)
_COMMON = {"source": "test"}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[duckdb.DuckDBPyConnection]:
    monkeypatch.setattr(ops, "packaged_dividend_reviews", lambda: ())
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    identity = {"known_at": IDENTITY_KNOWN, "ingested_at": IDENTITY_KNOWN}
    insert_row(
        conn,
        "securities",
        {"security_id": SPY, "cik": "0000000001", "name": "SPY", "benchmark": True}
        | identity
        | {"source": "config", "provenance": "snapshot_static"},
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": SPY,
            "ticker": "SPY",
            "exchange": "NYSE",
            "class_title": None,
            "valid_from": date(2016, 1, 4),
        }
        | identity
        | {"source": "config", "provenance": "snapshot_static"},
    )
    for session in SESSIONS:
        known_at = datetime(session.year, session.month, session.day, 21, tzinfo=UTC)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": SPY,
                "session": session,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
                "known_at": known_at,
                "ingested_at": known_at,
                "provenance": "bar",
            }
            | _COMMON,
        )
    for action_id, known_at in (("a", FIRST_KNOWN), ("b", SECOND_KNOWN)):
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": SPY,
                "action_type": "dividend",
                "ex_date": EX,
                "ratio_or_amount": 1.0,
                "source_action_id": action_id,
                "cancelled": False,
                "known_at": known_at,
                "ingested_at": known_at,
                "provenance": "action",
            }
            | _COMMON,
        )
    try:
        yield conn
    finally:
        conn.close()


def _outcome(conn: duckdb.DuckDBPyConnection, t: datetime) -> dict[date, float] | str:
    """The SPY line at `t` over the sessions whose bar is known by `t`, or its error."""
    known = [s for s in SESSIONS if datetime(s.year, s.month, s.day, 21, tzinfo=UTC) <= t]
    if not known:
        return "no session"
    try:
        return ops._benchmark_series(
            conn, Settings(_env_file=None), as_of=t, base=SESSIONS[0], sessions=known
        )
    except ValueError as exc:
        return str(exc)


def test_gated_benchmark_read_invariant_under_truncation(
    store: duckdb.DuckDBPyConnection,
) -> None:
    truncated = TruncatedStore(store)
    try:
        for t in probe_timestamps(store, ["prices_daily", "corporate_actions"]):
            assert _outcome(store, t) == _outcome(truncated.at(t), t), f"disagreed at T={t!r}"
    finally:
        truncated.close()


def test_a_second_record_known_later_fails_the_line_only_from_its_known_at(
    store: duckdb.DuckDBPyConnection,
) -> None:
    before = _outcome(store, datetime(2021, 1, 12, 22, tzinfo=UTC))
    assert isinstance(before, dict)
    assert before[date(2021, 1, 8)] == pytest.approx(99.0)
    after = _outcome(store, datetime(2021, 1, 14, 22, tzinfo=UTC))
    assert after == "benchmark has an unexplained duplicate dividend: ex 2021-01-11 1"

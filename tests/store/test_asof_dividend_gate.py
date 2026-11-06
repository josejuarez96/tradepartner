"""The reviewed-list gate on the dividend-including as-of reads (#3015).

`adjusted_prices_as_of(..., dividend_reviews=...)` and `dropped_dividends_as_of(...,
dividend_reviews=...)` class the read's live dividend rows at `t` with
`store.dividend_reviews.equal_amount_groups`, as health's `duplicate_dividends` and the
paper reconciliation do: a listed `duplicate` not yet repaired applies once (its retract
id is skipped), a listed `distinct` or `unresolved` pair applies as stored, and every
identity of a `violation` group is left unapplied and reported as
`unexplained_duplicate`, never summed. Without the keyword every live row applies, as
before. Each scenario runs on a schema-only store so its numbers are exact.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.store import schema
from tradepartner.store.asof import (
    UNEXPLAINED_DUPLICATE,
    adjusted_prices_as_of,
    dropped_dividends_as_of,
)
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.dividend_reviews import ReviewedGroup, Verdict

SID = "SEC_X"
CLEAN = "SEC_CLEAN"
#: 2021-01-04 .. 2021-01-15 are all XNYS sessions (no holiday in that span).
SESSIONS = [date(2021, 1, d) for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15)]
EX = date(2021, 1, 11)
KNOWN = datetime(2021, 1, 8, 21, tzinfo=UTC)
T = datetime(2021, 1, 15, 22, tzinfo=UTC)
#: A bar before the ex-date: 100 raw, times one 1.0-on-100 factor per applied row.
BEFORE = date(2021, 1, 8)


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for sid in (SID, CLEAN):
        for session in SESSIONS:
            known_at = datetime(session.year, session.month, session.day, 21, tzinfo=UTC)
            insert_row(
                conn,
                "prices_daily",
                {
                    "security_id": sid,
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
    try:
        yield conn
    finally:
        conn.close()


def _dividend(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    *,
    sid: str = SID,
    amount: float = 1.0,
    known_at: datetime = KNOWN,
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": "dividend",
            "ex_date": EX,
            "ratio_or_amount": amount,
            "source_action_id": action_id,
            "cancelled": False,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "action",
        },
    )


def _listed(verdict: Verdict, retract: str = "") -> ReviewedGroup:
    return ReviewedGroup(
        security_id=SID,
        ex_date=EX,
        amount=1.0,
        verdict=verdict,
        retract_source_action_id=retract,
        ticker="X",
        evidence="test",
    )


def _close(
    conn: duckdb.DuckDBPyConnection,
    reviews: tuple[ReviewedGroup, ...] | None,
    sid: str = SID,
    *,
    spin_offs: bool = False,
) -> float:
    frame = adjusted_prices_as_of(
        conn,
        T,
        [SID, CLEAN],
        include_dividends=True,
        include_spin_offs=spin_offs,
        dividend_reviews=reviews,
    )
    row = frame.filter((pl.col("security_id") == sid) & (pl.col("session") == BEFORE))
    return float(row["close"].item())


def _dropped(
    conn: duckdb.DuckDBPyConnection, reviews: tuple[ReviewedGroup, ...] | None
) -> list[tuple[str, date, float, str]]:
    frame = dropped_dividends_as_of(conn, T, [SID, CLEAN], dividend_reviews=reviews)
    return [
        (r["security_id"], r["ex_date"], r["ratio_or_amount"], r["reason"])
        for r in frame.iter_rows(named=True)
    ]


def test_without_the_keyword_every_live_row_applies(store: duckdb.DuckDBPyConnection) -> None:
    """The ungated read is unchanged: an unexplained pair is summed (the #3015 hole)."""
    _dividend(store, "a")
    _dividend(store, "b")
    assert _close(store, None) == pytest.approx(100.0 * 0.99 * 0.99)
    assert _dropped(store, None) == []


def test_an_unexplained_pair_is_left_unapplied_and_reported(
    store: duckdb.DuckDBPyConnection,
) -> None:
    _dividend(store, "a")
    _dividend(store, "b")
    assert _close(store, ()) == pytest.approx(100.0)
    assert _dropped(store, ()) == [
        (SID, EX, 1.0, UNEXPLAINED_DUPLICATE),
        (SID, EX, 1.0, UNEXPLAINED_DUPLICATE),
    ]


def test_a_pending_duplicate_applies_once(store: duckdb.DuckDBPyConnection) -> None:
    """A listed `duplicate` still holding both ids counts once, as reconcile counts it."""
    _dividend(store, "a")
    _dividend(store, "b")
    reviews = (_listed("duplicate", retract="b"),)
    assert _close(store, reviews) == pytest.approx(99.0)
    assert _dropped(store, reviews) == []


@pytest.mark.parametrize("verdict", ["distinct", "unresolved"])
def test_a_listed_distinct_or_unresolved_pair_applies_as_stored(
    store: duckdb.DuckDBPyConnection, verdict: Verdict
) -> None:
    _dividend(store, "a")
    _dividend(store, "b")
    reviews = (_listed(verdict),)
    assert _close(store, reviews) == pytest.approx(100.0 * 0.99 * 0.99)
    assert _dropped(store, reviews) == []


def test_a_repaired_duplicate_applies_once(store: duckdb.DuckDBPyConnection) -> None:
    _dividend(store, "a")
    reviews = (_listed("duplicate", retract="b"),)
    assert _close(store, reviews) == pytest.approx(99.0)
    assert _dropped(store, reviews) == []


def test_a_third_record_on_a_listed_duplicate_is_unapplied(
    store: duckdb.DuckDBPyConnection,
) -> None:
    for action_id in ("a", "b", "c"):
        _dividend(store, action_id)
    reviews = (_listed("duplicate", retract="b"),)
    assert _close(store, reviews) == pytest.approx(100.0)
    assert [r[3] for r in _dropped(store, reviews)] == [UNEXPLAINED_DUPLICATE] * 3


def test_the_gate_is_per_name(store: duckdb.DuckDBPyConnection) -> None:
    """Another name's unexplained pair in the same read leaves a clean name's dividend
    applied, so one bad group never refuses or skews the rest of the read."""
    _dividend(store, "a")
    _dividend(store, "b")
    _dividend(store, "c", sid=CLEAN)
    assert _close(store, (), sid=CLEAN) == pytest.approx(99.0)
    assert [r[0] for r in _dropped(store, ())] == [SID, SID]


def test_a_different_amount_on_the_same_day_is_not_a_group(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Only exact equal-amount keys are classed (the reviewed rule), so a base plus a
    different-amount variable on one ex-date both apply."""
    _dividend(store, "a")
    _dividend(store, "b", amount=2.0)
    assert _close(store, ()) == pytest.approx(100.0 * 0.99 * 0.98)
    assert _dropped(store, ()) == []


def test_the_spin_off_read_is_gated_too(store: duckdb.DuckDBPyConnection) -> None:
    _dividend(store, "a")
    _dividend(store, "b")
    assert _close(store, (), spin_offs=True) == pytest.approx(100.0)
    pending = (_listed("duplicate", retract="b"),)
    assert _close(store, pending, spin_offs=True) == pytest.approx(99.0)

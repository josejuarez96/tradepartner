"""#2190: the lot ledger's "latest set" is per book, not global.

`outcomes._latest_set` / `_append_ledger` and `cli._LATEST_SET` all read the
current lot-ledger set as `lots.known_at = (SELECT max(known_at) FROM lots)`,
with no book or window filter, while `write_outcomes_and_lots` rebuilds ONE
window's set. With two books holding lots at once (ADR 0017 fast paper: the
daily book holds 202 names now; main buys at its first rebalance, 2026-10-30)
each book's run compares its own set against the OTHER book's set, finds a
difference and appends a whole new set; the "current" set is whichever book
ran last. These tests state the per-book invariants and FAIL on today's code.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from tradepartner.execution.lots import LedgerAccount
from tradepartner.execution.outcomes import write_outcomes_and_lots
from tradepartner.store import schema
from tradepartner.store.journal import (
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    append,
)

STAMP = datetime(2026, 10, 18, 12, tzinfo=UTC)
F = date(2026, 10, 20)  # fill session
S = date(2026, 10, 26)  # run session


def _utc(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _book(conn: duckdb.DuckDBPyConnection, window_id: int, book: str, security_id: str) -> None:
    """One window in `book` with one filled buy of `security_id` on F."""
    append(
        conn,
        PaperWindowRow(
            window_id=window_id,
            hypothesis_id=window_id,
            first_rebalance_session=date(2026, 10, 19),
            account_id=f"acct-{book}",
            starting_cash=10_000.0,
            starting_equity=10_000.0,
            code_version="t",
            started_at=STAMP,
            frozen_json="{}",
            frozen_sha256="0" * 64,
            book_id=book,
            known_at=STAMP,
            ingested_at=STAMP,
        ),
    )
    append(
        conn,
        PaperRunRow(
            run_id=window_id,
            window_id=window_id,
            started_at=STAMP,
            invoked_by="tty",
            code_version="t",
            known_at=STAMP,
            ingested_at=STAMP,
        ),
    )
    coid = f"tp-{book}-{F:%Y%m%d}-{security_id}-buy-1"
    append(
        conn,
        OrderRow(
            client_order_id=coid,
            decision_id=window_id,
            run_id=window_id,
            session=F,
            attempt=1,
            phase="buy",
            security_id=security_id,
            symbol=security_id,
            side="buy",
            quantity=10.0,
            book_id=book,
            sells_in_flight_at_submit=False,
            known_at=_utc(F, 13),
            ingested_at=_utc(F, 13),
        ),
    )
    for status in ("pending", "accepted", "filled"):
        append(
            conn,
            OrderEventRow(
                client_order_id=coid, status=status, known_at=_utc(F, 20), ingested_at=_utc(F, 20)
            ),
        )
    append(
        conn,
        FillRow(
            client_order_id=coid,
            filled_at=_utc(F, 14),
            quantity=10.0,
            price=50.0,
            price_implied=False,
            broker_fill_id=f"bf-{book}",
            source="broker_feed",
            known_at=_utc(F, 20) + timedelta(seconds=window_id),
            ingested_at=_utc(F, 20) + timedelta(seconds=window_id),
        ),
    )


class _Clock:
    def __init__(self) -> None:
        self.now = _utc(S, 12)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _run(conn: duckdb.DuckDBPyConnection, window_id: int, clock: _Clock) -> int:
    errors: list[str] = []
    result = write_outcomes_and_lots(
        conn,
        window_id,
        LedgerAccount(f"acct-{window_id}", "paper", "self"),
        {},
        lambda _s, _d: 51.0,
        S,
        clock,
        on_lot_error=errors.append,
        cadence="daily",
    )
    assert errors == []
    return result.lot_rows


def test_an_unchanged_book_appends_no_new_lot_set_when_another_book_ran_between(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Module docstring of `execution.outcomes`: "a rebuild that changes nothing
    appends nothing". Two books, nothing changes between runs: after the first
    write of each, further runs must append zero rows."""
    _book(conn, 1, "daily", "SEC_D")
    _book(conn, 2, "main", "SEC_M")
    clock = _Clock()
    assert _run(conn, 1, clock) == 1  # daily's first set
    assert _run(conn, 2, clock) == 1  # main's first set
    # Nothing changed for either book: these should append nothing.
    later = [_run(conn, 1, clock), _run(conn, 2, clock), _run(conn, 1, clock)]
    assert later == [0, 0, 0], f"rows appended by unchanged rebuilds: {later}"


def _book_set(conn: duckdb.DuckDBPyConnection, book: str) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT security_id, known_at FROM lots WHERE book_id = ? "
        "AND known_at = (SELECT max(known_at) FROM lots WHERE book_id = ?)",
        [book, book],
    ).fetchall()


def test_each_books_current_set_is_its_own_and_an_unchanged_rebuild_appends_nothing(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _book(conn, 1, "daily", "SEC_D")
    _book(conn, 2, "main", "SEC_M")
    clock = _Clock()
    _run(conn, 1, clock)
    _run(conn, 2, clock)
    daily, main = _book_set(conn, "daily"), _book_set(conn, "main")
    assert [r[0] for r in daily] == ["SEC_D"]
    assert [r[0] for r in main] == ["SEC_M"]
    assert [_run(conn, 2, clock), _run(conn, 1, clock)] == [0, 0]
    assert _book_set(conn, "daily") == daily
    assert _book_set(conn, "main") == main
    assert conn.execute("SELECT count(*) FROM lots").fetchone() == (2,)
    # main's set changing leaves daily's current set alone.
    conn.execute("UPDATE fills SET quantity = 12.0 WHERE broker_fill_id = 'bf-main'")
    assert _run(conn, 2, clock) == 1
    assert _book_set(conn, "daily") == daily
    assert _run(conn, 1, clock) == 0


def test_lots_reconcile_reads_its_own_books_set_not_the_last_writers(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    from tradepartner import cli

    _book(conn, 1, "daily", "SEC_D")
    _book(conn, 2, "main", "SEC_M")
    clock = _Clock()
    _run(conn, 2, clock)
    _run(conn, 1, clock)  # daily wrote last
    main = cli._read_ledger_set(conn, 2026, "main")
    assert main.window_id == 2
    assert [lot.security_id for lot in main.lots] == ["SEC_M"]
    daily = cli._read_ledger_set(conn, 2026, "daily")
    assert daily.window_id == 1
    assert [lot.security_id for lot in daily.lots] == ["SEC_D"]

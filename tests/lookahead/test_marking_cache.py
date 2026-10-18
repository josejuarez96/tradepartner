"""The incremental marking cache equals a fresh point-in-time adjusted read."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from tradepartner.backtest.engine import _SharedReads
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, marking_changed_ids_as_of
from tradepartner.store.db import configure_connection, insert_row

SESSIONS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
)


def _bar(
    sid: str, session: date, close: float, *, known_at: datetime | None = None
) -> dict[str, object]:
    stamp = known_at or session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "known_at": stamp,
        "ingested_at": stamp,
        "source": "alpaca",
        "provenance": "bar",
    }


def _action(kind: str, amount: float, *, known_at: datetime) -> dict[str, object]:
    return {
        "security_id": "A",
        "action_type": kind,
        "ex_date": date(2024, 1, 5),
        "ratio_or_amount": amount,
        "announced_at": None,
        "known_at": known_at,
        "ingested_at": known_at,
        "source": "alpaca",
        "provenance": "action",
    }


@pytest.fixture
def price_store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for sid, base in (("A", 100.0), ("B", 50.0)):
        for index, session in enumerate(SESSIONS):
            insert_row(conn, "prices_daily", _bar(sid, session, base + index))
    # An unusually early known_at on a future-session bar must not be
    # duplicated when the cache appends intervening sessions.
    insert_row(
        conn,
        "prices_daily",
        _bar("A", date(2024, 1, 11), 110.0, known_at=session_close(SESSIONS[1])),
    )
    # Both events are known before ex-date; merely crossing ex-date must
    # invalidate old adjusted bars. Later corrections must invalidate again.
    for kind, amount in (("split", 2.0), ("dividend", 1.0)):
        insert_row(
            conn,
            "corporate_actions",
            _action(kind, amount, known_at=session_close(SESSIONS[1])),
        )
    insert_row(
        conn,
        "prices_daily",
        _bar("A", SESSIONS[1], 120.0, known_at=session_close(SESSIONS[4])),
    )
    insert_row(
        conn,
        "corporate_actions",
        _action("dividend", 1.5, known_at=session_close(SESSIONS[5])),
    )
    insert_row(
        conn,
        "corporate_actions",
        _action("split", 3.0, known_at=session_close(SESSIONS[6])),
    )
    try:
        yield conn
    finally:
        conn.close()


class _Prices:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn
        self.settings = Settings(_env_file=None)
        self.bounds: list[tuple[datetime, tuple[str, ...], date]] = []

    def adjusted_prices(
        self,
        t: datetime,
        ids: list[str] | tuple[str, ...],
        include_dividends: bool,
        *,
        sessions_from: date,
    ) -> pl.DataFrame:
        assert include_dividends
        self.bounds.append((t, tuple(ids), sessions_from))
        return adjusted_prices_as_of(
            self.conn,
            t,
            ids,
            include_dividends=True,
            settings=self.settings,
            traded_only=True,
            sessions_from=sessions_from,
        )

    def marking_changed_ids(
        self, previous_t: datetime, t: datetime, ids: list[str] | tuple[str, ...]
    ) -> tuple[str, ...]:
        return marking_changed_ids_as_of(self.conn, previous_t, t, ids)


def test_cache_matches_fresh_at_every_step_with_revisions_and_reentry(
    price_store: duckdb.DuckDBPyConnection,
) -> None:
    provider = _Prices(price_store)
    view = _SharedReads(provider, covering=False)  # type: ignore[arg-type]
    requests = [
        (SESSIONS[1], ("A", "B"), SESSIONS[0]),
        (SESSIONS[2], ("A",), SESSIONS[1]),  # B leaves the book
        (SESSIONS[3], ("A",), SESSIONS[1]),  # same-day split and dividend become effective
        (SESSIONS[4], ("A",), SESSIONS[2]),  # old bar correction arrives
        (SESSIONS[5], ("A", "B"), SESSIONS[2]),  # B re-enters; dividend restated
        (SESSIONS[6], ("A", "B"), SESSIONS[1]),  # earlier bound; split restated
    ]
    for session, ids, bound in requests:
        t = session_close(session)
        view.new_step()
        got = view.marking_prices(t, ids, bound)
        fresh = adjusted_prices_as_of(
            price_store,
            t,
            ids,
            include_dividends=True,
            settings=provider.settings,
            traded_only=True,
            sessions_from=bound,
        )
        assert got.equals(fresh), (session, ids, bound)
    # A quiet step adds only its new session. The ex-date and both revision
    # steps read old sessions again; B's re-entry reads its own history.
    assert provider.bounds[1][2] == SESSIONS[2]
    assert any(ids == ("A",) and bound == SESSIONS[1] for _, ids, bound in provider.bounds)
    assert any("B" in ids and bound == SESSIONS[2] for _, ids, bound in provider.bounds)


def test_future_revision_does_not_change_a_cached_earlier_step(
    price_store: duckdb.DuckDBPyConnection,
) -> None:
    provider = _Prices(price_store)
    view = _SharedReads(provider, covering=False)  # type: ignore[arg-type]
    first_t = session_close(SESSIONS[1])
    view.new_step()
    before = view.marking_prices(first_t, ("A",), SESSIONS[0])
    later = adjusted_prices_as_of(
        price_store,
        first_t + timedelta(days=5),
        ["A"],
        include_dividends=True,
        settings=provider.settings,
        traded_only=True,
        sessions_from=SESSIONS[0],
    )
    assert not before.equals(later)
    assert before.equals(
        adjusted_prices_as_of(
            price_store,
            first_t,
            ["A"],
            include_dividends=True,
            settings=provider.settings,
            traded_only=True,
            sessions_from=SESSIONS[0],
        )
    )


def test_advancing_bound_with_a_multi_session_gap_keeps_no_extra_rows(
    price_store: duckdb.DuckDBPyConnection,
) -> None:
    provider = _Prices(price_store)
    view = _SharedReads(provider, covering=False)  # type: ignore[arg-type]
    view.new_step()
    view.marking_prices(session_close(SESSIONS[1]), ("B",), SESSIONS[0])
    view.new_step()
    t, bound = session_close(SESSIONS[4]), SESSIONS[3]
    got = view.marking_prices(t, ("B",), bound)
    fresh = adjusted_prices_as_of(
        price_store,
        t,
        ["B"],
        include_dividends=True,
        settings=provider.settings,
        traded_only=True,
        sessions_from=bound,
    )
    assert got.equals(fresh)
    assert provider.bounds[-1][2] == bound

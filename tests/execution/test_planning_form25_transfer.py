"""Planning agrees with the exits that a Form 25 **transfer** does not end a
listing (issue #555; owner decision 2026-10-02).

`execution.exits` (via its callers in `run.py`/`wrapper.py`) only treats a
name whose current listing status is `delisted` as ended; `transferred` is
not an end, since the business keeps trading, just on another exchange.
`planning._ended` used to flag *any* non-`listed` status, `transferred`
included, which could make the plan journal `skip_delisted` for a name the
exits would still hold and trade normally.

Both sides read `tradepartner.store.delistings.listing_ends_as_of` and pick
the name's current row (highest `valid_from` on or before close(S-1)) the
same way; they differ only in which status counts as "ended". These tests
build that disagreement directly: a successor listing can be *known* before
its own `valid_from` (an exchange transfer announced ahead of its effective
date), so on a session between the filing and the successor's `valid_from`,
the current row's status is already `transferred`, not yet superseded by the
successor row itself.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.execution import planning
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.delistings import DELISTED, listing_ends_as_of

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SESSION = date(2019, 4, 26)  # previous_session(SESSION) = 2019-04-25


def _at(year: int, month: int, day: int, hour: int = 15) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _listing(
    sid: str, ticker: str, exchange: str, valid_from: date, known_at: datetime
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": exchange,
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "known_at": known_at,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _delisting(sid: str, exchange: str, filed_at: datetime) -> dict[str, Any]:
    return {
        "security_id": sid,
        "form": "25",
        "class_title": "Common Stock",
        "exchange": exchange,
        "filed_at": filed_at,
        "effective_on": filed_at.date(),
        "known_at": filed_at,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    configure_connection(c)
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _ended_for_exits(
    conn: duckdb.DuckDBPyConnection, session: date, names: Sequence[str], settings: Settings
) -> set[str]:
    """The exits' rule (run.py's `_forced_exits`, wrapper.py's `_listings`):
    a name's current listing at close(S-1) has ended only when its status is
    `delisted`, never `transferred`. Reproduced here (not imported) because
    it is inlined at each caller; both read the same `listing_ends_as_of` and
    pick the current row by the same `valid_from <= day` rule as
    `planning._ended`."""
    cut = session_close(previous_session(session))
    day = previous_session(session)
    frame = listing_ends_as_of(conn, cut, settings, list(names))
    current = planning._current(frame, day)
    return {sid for sid, row in current.items() if row["status"] == DELISTED}


def test_a_transfer_announced_ahead_of_its_effective_date_does_not_end_the_listing(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    sid = "SEC_ADVANCE_XFER"
    insert_row(conn, "listings", _listing(sid, "ADVX", "NYSE", date(2019, 1, 2), _at(2018, 12, 3)))
    insert_row(conn, "delistings", _delisting(sid, "NYSE", _at(2019, 4, 22)))
    # The NASDAQ relisting is already known (announced) before close(S-1),
    # even though it does not take effect until well after SESSION.
    insert_row(
        conn, "listings", _listing(sid, "ADVX", "NASDAQ", date(2019, 5, 15), _at(2019, 4, 23))
    )
    settings = _settings(master={"transfer_window_sessions": 20})

    # Sanity check: the current row (NYSE, since NASDAQ's valid_from is still
    # in the future relative to close(S-1)) is indeed "transferred", not
    # "listed" -- otherwise this test would not exercise the bug at all.
    day = previous_session(SESSION)
    current = planning._current(listing_ends_as_of(conn, session_close(day), settings, [sid]), day)
    assert current[sid]["status"] == "transferred"

    assert planning._ended(conn, SESSION, [sid], settings) == {}
    assert _ended_for_exits(conn, SESSION, [sid], settings) == set()


def test_a_genuine_delisting_with_no_successor_ends_the_listing_in_both(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    sid = "SEC_GENUINE_DELIST"
    insert_row(conn, "listings", _listing(sid, "GDL", "NYSE", date(2019, 1, 2), _at(2018, 12, 3)))
    insert_row(conn, "delistings", _delisting(sid, "NYSE", _at(2019, 4, 22)))
    settings = _settings()

    assert sid in planning._ended(conn, SESSION, [sid], settings)
    assert sid in _ended_for_exits(conn, SESSION, [sid], settings)

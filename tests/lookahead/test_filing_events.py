"""Look-ahead cases for `filing_events_as_of` (#1358, data-foundation plan
T164d; spec "Data / interfaces" > Amendment 2026-10-09, "As-of read (T164d)",
and backtest spec req 13 as #720 extended it).

The fixture's three 8-K rows for `CIK0001000011` (`SEC_SPLIT_PLAIN`, T164b)
carry their acceptance as `known_at`; the submissions record's `filed` date is
not stored, so `_FILED` below copies it from `tests/fixtures/universe/README.md`
to build the read a `filed`-keyed table would give (`_filed_keyed_read`). Each
case is one checker run against the real read; where the plan line says the
case has teeth, the same checker is run against the `filed`-keyed read and
must fail, so the test proves the store's acceptance key is what passes it.

- An 8-K accepted after `close(T)` is invisible at `close(T)` and visible at
  the next read (the next session's open).
- The `filed`-date trap with teeth: the 16:05 New York row, `filed` on its
  own session T, is invisible at T's open and at `close(T)`, and its first
  visible session is T + 1; a `filed`-keyed read fails both.
- The 20:30 row as the lag case (`filed` = T + 1): the real read passes, and
  so does a `filed`-keyed read, by accident; asserted here so nobody mistakes
  it for a test of the key.
- Truncation invariance with `filing_events` in the truncated set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.calendar import next_session, session_close, session_open
from tradepartner.store.asof import filing_events_as_of

Read = Callable[[duckdb.DuckDBPyConnection, datetime], pl.DataFrame]

_NEW_YORK = ZoneInfo("America/New_York")

#: The fixture's three accessions (tests/fixtures/universe/README.md).
_TEETH = "0001000011-20-000101"
_LAG = "0001000011-20-000102"
_OTHER = "0001000011-20-000103"

#: The submissions record's `filed` date per accession, from the fixture
#: README: not a column, so only a test can build a `filed`-keyed read.
_FILED: dict[str, date] = {
    _TEETH: date(2020, 4, 30),
    _LAG: date(2020, 5, 6),
    _OTHER: date(2020, 5, 12),
}

#: The session on which each row is accepted (New York date of `accepted_at`).
_TEETH_SESSION = date(2020, 4, 30)  # accepted 16:05 New York
_LAG_SESSION = date(2020, 5, 5)  # accepted 20:30 New York


def _store_read(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
    """The real read."""
    return filing_events_as_of(conn, t)


def _filed_keyed_read(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
    """What a table keyed on the index's `filed` date would answer: a row is
    visible from its `filed` date on (New York calendar), whatever its
    acceptance. The wrong key, built only to show the cases below fail on it."""
    everything = filing_events_as_of(conn, datetime(9999, 1, 1, tzinfo=UTC))
    today = t.astimezone(_NEW_YORK).date()
    visible = [_FILED[a] <= today for a in everything["accession"]]
    return everything.filter(pl.Series(visible, dtype=pl.Boolean))


def _visible(read: Read, conn: duckdb.DuckDBPyConnection, t: datetime, accession: str) -> bool:
    return accession in read(conn, t)["accession"].to_list()


def _first_visible_session(
    read: Read, conn: duckdb.DuckDBPyConnection, accession: str, start: date
) -> date:
    """The first session from `start` on whose open sees `accession`."""
    session = start
    for _ in range(10):
        if _visible(read, conn, session_open(session), accession):
            return session
        session = next_session(session)
    raise AssertionError(f"{accession} not visible within ten sessions of {start}")


def _check_after_close_row(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """An 8-K accepted after `close(T)`: invisible at `close(T)`, visible at
    the next read (the next session's open)."""
    for accession, session in ((_TEETH, _TEETH_SESSION), (_LAG, _LAG_SESSION)):
        assert not _visible(read, conn, session_close(session), accession), accession
        assert _visible(read, conn, session_open(next_session(session)), accession), accession


def _check_filed_date_trap(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """The teeth row (accepted 16:05 New York on T, `filed` = T): invisible
    at T's open and at `close(T)`."""
    assert not _visible(read, conn, session_open(_TEETH_SESSION), _TEETH)
    assert not _visible(read, conn, session_close(_TEETH_SESSION), _TEETH)


def _check_teeth_first_visible_session(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """The teeth row's first visible session is T + 1."""
    first = _first_visible_session(read, conn, _TEETH, _TEETH_SESSION)
    assert first == next_session(_TEETH_SESSION)


def _check_lag_row(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """The 20:30 row (`filed` = T + 1): invisible at `close(T)`, first
    visible session T + 1."""
    assert not _visible(read, conn, session_close(_LAG_SESSION), _LAG)
    first = _first_visible_session(read, conn, _LAG, _LAG_SESSION)
    assert first == next_session(_LAG_SESSION)


def test_fixture_rows_are_the_ones_these_cases_assume(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Guards the constants above against a regenerated fixture: each row's
    acceptance falls where its case needs it."""
    rows = fixture_store.execute(
        "SELECT accession, accepted_at FROM filing_events ORDER BY accession"
    ).fetchall()
    accepted = {accession: at for accession, at in rows}
    assert set(accepted) == set(_FILED)
    teeth = accepted[_TEETH]
    assert session_close(_TEETH_SESSION) < teeth
    assert teeth.astimezone(_NEW_YORK).date() == _TEETH_SESSION == _FILED[_TEETH]
    lag = accepted[_LAG]
    assert lag.astimezone(_NEW_YORK).date() == _LAG_SESSION
    assert _FILED[_LAG] == next_session(_LAG_SESSION)


def test_after_close_8k_is_invisible_at_close_and_visible_at_the_next_read(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _check_after_close_row(_store_read, fixture_store)


def test_filed_date_trap_row_is_invisible_at_open_and_close_of_its_session(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _check_filed_date_trap(_store_read, fixture_store)


def test_filed_date_trap_row_is_first_visible_the_next_session(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _check_teeth_first_visible_session(_store_read, fixture_store)


@pytest.mark.parametrize(
    "check", [_check_filed_date_trap, _check_teeth_first_visible_session], ids=["T", "T+1"]
)
def test_a_filed_keyed_read_fails_the_trap(
    fixture_store: duckdb.DuckDBPyConnection,
    check: Callable[[Read, duckdb.DuckDBPyConnection], None],
) -> None:
    """The teeth: the same checks against a `filed`-keyed read fail, so the
    passing tests above pass because the store keys on acceptance."""
    with pytest.raises(AssertionError):
        check(_filed_keyed_read, fixture_store)


def test_lag_row_passes_on_both_keys_and_so_proves_nothing_alone(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The 20:30 row is `filed` the day after its session, so a `filed`-keyed
    read gets it right by accident: this case alone cannot tell the keys apart."""
    _check_lag_row(_store_read, fixture_store)
    _check_lag_row(_filed_keyed_read, fixture_store)


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize(
    ("forms", "items"),
    [(None, None), (["8-K", "8-K/A"], ["2.02"]), (None, ["5.02"])],
    ids=["unfiltered", "8-K 2.02", "5.02"],
)
def test_filing_events_as_of_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection,
    truncated: TruncatedStore,
    forms: list[str] | None,
    items: list[str] | None,
) -> None:
    """f(T) on the full store equals f(T) on the store truncated to
    `known_at <= T`, with `filing_events` (and `securities`, which the read
    joins) in the truncated set, at every probe around every `known_at`."""
    assert {"filing_events", "securities"} <= set(truncated.tables)
    probes = probe_timestamps(fixture_store)
    for t in probes:
        full = filing_events_as_of(fixture_store, t, forms=forms, items=items)
        result = filing_events_as_of(truncated.at(t), t, forms=forms, items=items)
        assert full.equals(result), f"disagreed at T={t!r}"
    # The probes straddle every filing-event row, and the rows do appear.
    assert filing_events_as_of(fixture_store, probes[-1], forms=forms, items=items).height > 0

"""Look-ahead cases for `borrow_status_as_of` (#1506, data-foundation plan
T171b; spec "Data / interfaces" > Amendment 2026-10-11, "As-of read
(T171b)").

The fixture's three snapshots are fetched an hour after each of the last
three sessions' closes (`tests/fixtures/universe/borrow_status.csv`), so a
session's close reads exactly the snapshots fetched strictly before it: that
day's own snapshot is invisible until the next session's open. Each case
below is run against the real read, and the cases whose boundary the fetch
time decides are run again against a read with the `known_at <= t` filter
dropped and must fail there, so a passing case passes because of the filter.

- Nothing is read at the first session's close, before any snapshot.
- The flipped asset reads ETB after its first snapshot and HTB after its
  second.
- The vanished asset reads after its first snapshot and not after the second.
- The renamed asset reads under its old symbol after the first snapshot and
  under its new symbol only after the second.
- The reappeared asset is absent after its second snapshot and back after
  the third.
- Truncation invariance with `borrow_status` in the truncated set: the
  harness reads `TABLE_NAMES`, which lists the table, and it copies no
  `ingestion_runs`, so `observed_at` is NULL on both sides and the table's
  own truncation is what is checked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.calendar import session_close, session_open
from tradepartner.store.asof import borrow_status_as_of

#: A read this module's cases are written against: `t`, then the symbols the
#: case filters on.
Read = Callable[[duckdb.DuckDBPyConnection, datetime, Sequence[str] | None], pl.DataFrame]

#: The fixture's three snapshots: the close plus an hour, a day apart.
_FIRST = datetime(2020, 6, 26, 21, tzinfo=UTC)
_SECOND = datetime(2020, 6, 29, 21, tzinfo=UTC)
_THIRD = datetime(2020, 6, 30, 21, tzinfo=UTC)

#: The sessions the snapshots belong to, and the one after the last.
_SESSIONS = (date(2020, 6, 26), date(2020, 6, 29), date(2020, 6, 30))
_AFTER = date(2020, 7, 1)


def _store_read(
    conn: duckdb.DuckDBPyConnection, t: datetime, symbols: Sequence[str] | None = None
) -> pl.DataFrame:
    """The real read."""
    return borrow_status_as_of(conn, t, symbols)


def _unfiltered_read(
    conn: duckdb.DuckDBPyConnection, t: datetime, symbols: Sequence[str] | None = None
) -> pl.DataFrame:
    """What a read that ignored `known_at <= t` would answer: the store's
    latest row per `asset_id`, whenever it was fetched, with the symbols
    filter kept so a case failing on it fails because of `t` and not because
    of the filter. Written out here rather than imported: there is
    deliberately nothing in `store.asof` to import it from, and it is only
    ever run to be shown wrong."""
    params: list[Any] = []
    symbol_filter = ""
    if symbols is not None:
        symbol_filter = " AND list_contains(?::VARCHAR[], l.symbol)"
        params.append(list(symbols))
    sql = f"""
        SELECT * EXCLUDE (present, ingested_at, source, provenance) FROM (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY asset_id ORDER BY known_at DESC
                ) AS _rn
                FROM borrow_status
            )
            WHERE _rn = 1 AND present
        ) AS l
        WHERE TRUE{symbol_filter}
        ORDER BY l.symbol, l.asset_id
    """
    return conn.execute(sql, params).pl()


def _rows(read: Read, conn: duckdb.DuckDBPyConnection, t: datetime, symbol: str) -> pl.DataFrame:
    """The rows `read` returns for one symbol at `t`."""
    rows = read(conn, t, [symbol])
    assert rows["symbol"].unique().to_list() in ([symbol], [])
    return rows


def _flags(read: Read, conn: duckdb.DuckDBPyConnection, t: datetime, symbol: str) -> list[bool]:
    return _rows(read, conn, t, symbol)["easy_to_borrow"].to_list()


def _check_nothing_before_the_first_snapshot(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """At the first session's close its own snapshot is an hour away, so no
    asset reads yet."""
    assert read(conn, session_close(_SESSIONS[0]), None).height == 0


def _check_flipped_asset(read: Read, conn: duckdb.DuckDBPyPyConnection) -> None:  # type: ignore[valid-type]
    """SPLT: nothing at the first session's close, ETB after its first
    snapshot, HTB after its second."""
    assert _flags(read, conn, session_close(_SESSIONS[0]), "SPLT") == []
    assert _flags(read, conn, session_close(_SESSIONS[1]), "SPLT") == [True]
    assert _flags(read, conn, session_close(_SESSIONS[2]), "SPLT") == [False]


def _check_flip_is_invisible_until_its_fetch(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """The 2020-06-29 snapshot's flip is not visible at that session's
    close, an hour before the fetch, and is at the next session's open."""
    assert _flags(read, conn, _SECOND - timedelta(seconds=1), "SPLT") == [True]
    assert _flags(read, conn, session_open(_SESSIONS[2]), "SPLT") == [False]


def _check_vanished_asset(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """DUALA reads at the close after its first snapshot and not at the
    close after the snapshot that lacked it."""
    assert _rows(read, conn, session_close(_SESSIONS[1]), "DUALA").height == 1
    assert _rows(read, conn, session_close(_SESSIONS[2]), "DUALA").height == 0


def _check_renamed_asset(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """TCKA reads under its old symbol after the first snapshot; under its
    new symbol only after the second, the old symbol never again."""
    assert _rows(read, conn, session_close(_SESSIONS[1]), "TCKA").height == 1
    after = session_close(_AFTER)
    assert _rows(read, conn, after, "TCKA").height == 0
    assert _rows(read, conn, after, "TCKB").height == 1
    assert _rows(read, conn, after, "TCKB")["borrow_status"].to_list() == ["easy_to_borrow"]
    assert _store_read(conn, after).height == 5


def _check_reappeared_asset(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """RDAT vanishes at the second snapshot and is back at the third:
    absent at the close after the second, present after the third."""
    assert _rows(read, conn, session_close(_SESSIONS[1]), "RDAT").height == 1
    assert _rows(read, conn, session_close(_SESSIONS[2]), "RDAT").height == 0
    assert _rows(read, conn, session_open(_AFTER), "RDAT").height == 1


def _check_unchanged_asset(read: Read, conn: duckdb.DuckDBPyConnection) -> None:
    """BNDX never changes, and reads its one row at every later close with
    the fetch time that wrote it."""
    for session in _SESSIONS[1:]:
        rows = _rows(read, conn, session_close(session), "BNDX")
        assert rows.height == 1
        assert rows["known_at"].to_list() == [_FIRST]


#: Every case, with whether its boundary is the fetch time's: a case that is
#: must fail on `_unfiltered_read`, which is what proves the filter is what
#: passes it.
_CASES: tuple[tuple[str, Read, bool], ...] = (
    ("nothing_before_the_first_snapshot", _check_nothing_before_the_first_snapshot, True),
    ("flipped_asset", _check_flipped_asset, True),
    ("vanished_asset", _check_vanished_asset, False),
    ("renamed_asset", _check_renamed_asset, True),
    ("reappeared_asset", _check_reappeared_asset, True),
    ("unchanged_asset", _check_unchanged_asset, False),
)
_NAMES = [name for name, _, _ in _CASES]


@pytest.mark.parametrize(("name", "check"), [(n, c) for n, c, _ in _CASES], ids=_NAMES)
def test_the_case_holds_on_the_real_read(
    fixture_store: duckdb.DuckDBPyConnection, name: str, check: Read
) -> None:
    check(_store_read, fixture_store)


@pytest.mark.parametrize(
    ("name", "check"),
    [(n, c) for n, c, teeth in _CASES if teeth],
    ids=[n for n, _, teeth in _CASES if teeth],
)
def test_the_case_fails_on_a_read_that_ignores_known_at(
    fixture_store: duckdb.DuckDBPyConnection, name: str, check: Read
) -> None:
    """The teeth: the same case against a read that ignores `known_at <= t`
    fails, so what passes above passes because of the filter. A case whose
    boundary the filter does not decide is not run here -- it would pass on
    the wrong read too and prove nothing."""
    with pytest.raises(AssertionError):
        check(_unfiltered_read, fixture_store)


def test_the_flip_is_invisible_at_the_close_before_its_fetch(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _check_flip_is_invisible_until_its_fetch(_store_read, fixture_store)
    with pytest.raises(AssertionError):
        _check_flip_is_invisible_until_its_fetch(_unfiltered_read, fixture_store)


def test_fixture_snapshots_are_where_these_cases_assume(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Guards the module's constants against a regenerated fixture: each
    snapshot falls an hour after its session's close, which is what makes
    that session's close exclude it."""
    known_ats = fixture_store.execute("SELECT DISTINCT known_at FROM borrow_status").fetchall()
    fetched = sorted(known for (known,) in known_ats)
    assert fetched == [_FIRST, _SECOND, _THIRD]
    for fetched_at, session in zip(fetched, _SESSIONS, strict=True):
        assert fetched_at - session_close(session) == timedelta(hours=1)


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize("symbols", [None, ["SPLT"], ["SPLT", "TCKB"]], ids=["none", "one", "two"])
def test_borrow_status_as_of_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection,
    truncated: TruncatedStore,
    symbols: list[str] | None,
) -> None:
    """f(T) on the full store equals f(T) on the store truncated to
    `known_at <= T`, with `borrow_status` in the truncated set (the harness
    reads `TABLE_NAMES`), at every probe around every `known_at` in the
    fixture. `ingestion_runs` is not copied by the harness and no read here
    reads it, so `observed_at` is NULL on both sides and the table's own
    truncation is what this checks."""
    assert {"borrow_status"} <= set(truncated.tables)
    probes = probe_timestamps(fixture_store)
    for t in probes:
        full = borrow_status_as_of(fixture_store, t, symbols)
        result = borrow_status_as_of(truncated.at(t), t, symbols)
        assert full.equals(result), f"disagreed at T={t!r}"
    # The probes straddle every borrow row, and rows do appear.
    last = borrow_status_as_of(fixture_store, probes[-1], symbols)
    assert last.height > 0
    assert last.height == (5 if symbols is None else len(symbols))

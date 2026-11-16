"""#3474: from a repair successor's first listing day, a same-CIK, same-class
old id's bars are the successor's (the ownership `health`'s
`old_id_bars_after_successor` reads). The resolver stops assigning them to the
old id, and `repair_resolution` retracts the ones the successor already holds
and moves the ones it lacks, so the company loses no session.

The shape is the rehearsal's VELO: the old id's last own ticker is a symbol
change (VLD -> VLDXD) that nothing else ends, and its later VELO row was
withdrawn to the successor, so only the symbol-change span kept the bars."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.alpaca_prices import ListingResolver, _successor_cuts
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.health import OLD_ID_BARS_AFTER_SUCCESSOR, integrity_checks
from tradepartner.repair import repair_resolution, store_resolver
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.release_coverage import release_coverage
from tradepartner.store.schema import init_schema

RUN = datetime(2019, 3, 8, 23, tzinfo=UTC)
REL = "t3474"
OLD = "0001825079:common-stock"
NEW = "0001825079@2019-03-01"
START = date(2019, 3, 1)
BEFORE = (date(2019, 2, 27), date(2019, 2, 28))
SHARED = (date(2019, 3, 1), date(2019, 3, 4))  # the successor holds the same bar
LACKED = date(2019, 3, 5)  # only the old id has it


def _listing(
    sid: str, ticker: str, valid_from: date, *, known: datetime | None = None, title: str = ""
) -> dict[str, Any]:
    stamp = known or datetime(valid_from.year, valid_from.month, valid_from.day, 21, tzinfo=UTC)
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": title or "Common Stock, $0.00001 par value per share",
        "valid_from": valid_from,
        "known_at": stamp,
        "ingested_at": max(stamp, RUN),
        "source": "edgar",
        "provenance": "filing",
    }


def _bar(
    sid: str, session: date, *, close: float = 10.0, known: datetime | None = None
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "known_at": known or session_close(session),
        "ingested_at": RUN,
        "source": "alpaca_sip",
        "provenance": "bar",
    }


def _build(settings: Settings, *, successor_known: datetime | None = None) -> Settings:
    known = datetime(2019, 1, 31, 21, tzinfo=UTC)
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "listings", _listing(OLD, "VLD", date(2019, 1, 2)))
        insert_row(conn, "listings", _listing(NEW, "VELO", START, known=successor_known))
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": OLD,
                "old_symbol": "VLD",
                "new_symbol": "VLDXD",
                "old_cusip": "92259N203",
                "new_cusip": "92259N302",
                "process_date": date(2019, 1, 31),
                "source_action_id": "VLD-VLDXD",
                "known_at": known,
                "ingested_at": known,
                "source": "alpaca",
                "provenance": "action",
            },
        )
        for day in (*BEFORE, *SHARED):
            insert_row(conn, "prices_daily", _bar(OLD, day))
        insert_row(conn, "prices_daily", _bar(OLD, LACKED, close=11.0))
        # A later revision of the lacked bar: both revisions move.
        insert_row(
            conn,
            "prices_daily",
            _bar(OLD, LACKED, close=11.5, known=datetime(2019, 3, 6, 23, tzinfo=UTC)),
        )
        for day in SHARED:
            insert_row(conn, "prices_daily", _bar(NEW, day))
        registry.open_data_release(conn, conn, name=REL, backup_path="b.duckdb", reason="#3474")
    return settings


@pytest.fixture
def store(settings: Settings) -> Settings:
    return _build(settings)


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(sql).fetchall()


def test_successor_cuts_pair_same_class_ids_only() -> None:
    rows = [
        _listing(OLD, "VLD", date(2019, 1, 2)),
        _listing(NEW, "VELO", START),
        _listing("0001825079:preferred", "VLDP", date(2019, 1, 2), title="Preferred Stock"),
        _listing("0001825079@2019-04-01", "VELW", date(2019, 4, 1), title="Warrants"),
        _listing("0009999999", "OTHR", date(2018, 1, 2)),
    ]
    assert _successor_cuts(rows) == {OLD: (START, NEW)}


def test_an_older_successor_is_cut_by_a_later_one() -> None:
    later = "0001825079@2020-01-02"
    rows = [
        _listing(OLD, "VLD", date(2019, 1, 2)),
        _listing(NEW, "VELO", START),
        _listing(later, "VELO", date(2020, 1, 2)),
    ]
    assert _successor_cuts(rows) == {OLD: (START, NEW), NEW: (date(2020, 1, 2), later)}


def test_the_resolver_ends_the_old_ids_spans_at_the_successors_start() -> None:
    resolver = ListingResolver(
        [_listing(OLD, "VLD", date(2019, 1, 2)), _listing(NEW, "VELO", START)],
        symbol_changes=[
            {
                "security_id": OLD,
                "old_symbol": "VLD",
                "new_symbol": "VLDXD",
                "process_date": date(2019, 1, 31),
            }
        ],
    )
    assert resolver.holds(OLD, BEFORE[1])
    assert resolver.resolve("VLDXD", BEFORE[1]) == OLD
    for day in (*SHARED, LACKED):
        assert not resolver.holds(OLD, day)
        assert resolver.resolve("VLDXD", day) is None  # no new bar lands on the old id
        assert resolver.resolve("VELO", day) == NEW
        assert resolver.successor_from(OLD, day) == NEW
    assert resolver.successor_from(OLD, BEFORE[1]) is None
    assert resolver.report.successor_cut_spans == 1


def test_a_preferred_is_not_cut_by_the_commons_successor() -> None:
    pref = "0001825079:preferred"
    resolver = ListingResolver(
        [
            _listing(OLD, "VLD", date(2019, 1, 2)),
            _listing(pref, "VLDP", date(2019, 1, 2), title="Series A Preferred Stock"),
            _listing(NEW, "VELO", START),
        ]
    )
    assert resolver.successor_from(pref, LACKED) is None
    assert resolver.resolve("VLDP", LACKED) == resolver.resolve("VLDP", BEFORE[1])


def test_the_dry_run_retracts_the_duplicates_and_moves_the_rest(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    keys = {(OLD, day) for day in (*SHARED, LACKED)}
    assert set(result.found.bars) == keys
    assert result.bar_rows == 4  # the lacked key holds two revisions
    assert result.orphan_keys == 0
    assert dict(result.duplicates) == {(OLD, day): NEW for day in SHARED}
    assert dict(result.moves) == {(OLD, LACKED): NEW}
    assert f"  MOVE {OLD} (?): 1 bar keys 2019-03-05..2019-03-05 to {NEW}, which lacks them" in (
        result.lines()
    )
    assert "1 bar keys to move to a repair successor that lacks them" in result.summary()


def test_the_apply_moves_every_revision_and_the_rule_passes(
    store: Settings, tmp_path: Path
) -> None:
    backup = tmp_path / "backup.duckdb"
    shutil.copy(store.store.path, backup)
    with open_for_write(store) as conn:
        assert not next(
            c for c in integrity_checks(conn, RUN, store) if c.rule == OLD_ID_BARS_AFTER_SUCCESSOR
        ).passed
    result = repair_resolution(store, clock=lambda: RUN, expect=(4, 0), release=REL)
    assert dict(result.moves) == {(OLD, LACKED): NEW}
    assert _rows(
        store,
        "SELECT security_id, session, close, known_at, ingested_at FROM prices_daily "
        "ORDER BY security_id, session, known_at",
    ) == [
        (OLD, BEFORE[0], 10.0, session_close(BEFORE[0]), RUN),
        (OLD, BEFORE[1], 10.0, session_close(BEFORE[1]), RUN),
        (NEW, SHARED[0], 10.0, session_close(SHARED[0]), RUN),
        (NEW, SHARED[1], 10.0, session_close(SHARED[1]), RUN),
        (NEW, LACKED, 11.0, session_close(LACKED), RUN),
        (NEW, LACKED, 11.5, datetime(2019, 3, 6, 23, tzinfo=UTC), RUN),
    ]
    [(rule,)] = _rows(
        store,
        "SELECT DISTINCT rule FROM retracted_rows WHERE table_name = 'prices_daily'",
    )
    assert f"1 of these sessions copied first to {NEW}, which lacked them (#3474)" in rule
    with open_for_write(store) as conn:
        assert next(
            c for c in integrity_checks(conn, RUN, store) if c.rule == OLD_ID_BARS_AFTER_SUCCESSOR
        ).passed
    with duckdb.connect(store.store.path, read_only=True) as conn:
        conn.execute(f"ATTACH '{backup}' AS bk (READ_ONLY)")
        losses = release_coverage(conn, backup_alias="bk", retraction_release=REL)
    assert [(loss.security_id, loss.lost, loss.unmoved) for loss in losses] == [(OLD, 3, 0)]


def test_a_successor_not_yet_known_cuts_nothing(settings: Settings) -> None:
    """No look-ahead: the successor's row known only after the run leaves the
    old id's bars where they are (the resolver reads the listings known at
    the run)."""
    store = _build(settings, successor_known=datetime(2019, 3, 11, 21, tzinfo=UTC))
    with open_for_write(store) as conn:
        resolver = store_resolver(conn, RUN, store)
    assert resolver.successor_from(OLD, LACKED) is None
    assert resolver.holds(OLD, LACKED)
    result = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    assert not [key for key in result.found.bars if key[0] == OLD]
    assert not result.moves

"""#3466: a symbol change stored on a lost id carries to its receiver.

Atlantic International (CIK 1605888): the receiver `@2024-12-09` lists only
`ATLN`; Alpaca's `ATLN -> CIRC` of 2026-07-02 sits on the lost id
`@2025-03-27`, whose listing master-retract withdrew and whose `CIRC` bars of
2026-10-08..11-13 a repair retracted. fill-holes planned the receiver, asked
Alpaca for `ATLN` (served no bar after the change) and wrote nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.alpaca_prices import ListingResolver
from tradepartner.adapters.prices import bar_known_at
from tradepartner.backfill import FILLED, fill_holes
from tradepartner.calendar import is_session
from tradepartner.cli import StorePriceSource
from tradepartner.config import Settings
from tradepartner.ingest import _read
from tradepartner.repair import _held_tickers, store_resolver
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.retracted_rows import retract
from tradepartner.store.schema import init_schema

BARE = "0001605888"
RECEIVER = "0001605888@2024-12-09"
LOST = "0001605888@2025-03-27"
SPY = "0000884394"
CHANGED = date(2026, 7, 2)
LOST_BARS = (date(2026, 10, 8), date(2026, 11, 13))
T = datetime(2026, 11, 15, 18, tzinfo=UTC)
SINCE = date(2026, 8, 1)


def _at(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _sessions(first: date, last: date) -> list[date]:
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return [day for day in days if is_session(day)]


def _listing(
    conn: Any, sid: str, ticker: str, valid_from: date, known: datetime, *, retracted: bool = False
) -> None:
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "NYSE ARCA" if ticker == "SPY" else "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": valid_from,
            "retracted": retracted,
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _bar(conn: Any, sid: str, day: date, close: float) -> None:
    known = bar_known_at(day)
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": day,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": known,
            "ingested_at": known + timedelta(hours=1),
            "source": "alpaca_sip",
            "provenance": "bar",
        },
    )


def _change(conn: Any, sid: str, known: datetime) -> None:
    insert_row(
        conn,
        "symbol_changes",
        {
            "security_id": sid,
            "old_symbol": "ATLN",
            "new_symbol": "CIRC",
            "old_cusip": "048592109",
            "new_cusip": "048592109",
            "process_date": CHANGED,
            "source_action_id": "a2212212",
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "action",
        },
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_missing_share": 1.0},
    )


CHANGE_KNOWN = datetime(2026, 7, 6, 20, tzinfo=UTC)


def _store(settings: Settings, *, change_known: datetime = CHANGE_KNOWN) -> None:
    """The rehearsal copy's ATLN family as of the fill (answers to #3466)."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for sid, cik, benchmark in [
            (BARE, BARE, False),
            (RECEIVER, BARE, False),
            (LOST, BARE, False),
            (SPY, SPY, True),
        ]:
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": cik,
                    "name": sid,
                    "benchmark": benchmark,
                    "known_at": _at(date(2021, 1, 4)),
                    "ingested_at": _at(date(2021, 1, 4)),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        listed = _at(date(2025, 3, 28))
        _listing(conn, SPY, "SPY", date(1993, 1, 29), _at(date(2021, 1, 4)))
        _listing(conn, BARE, "SQL", date(2021, 11, 15), _at(date(2021, 11, 15), 22))
        _listing(conn, BARE, "ATLN", date(2025, 3, 27), listed)
        _listing(conn, BARE, "ATLN", date(2025, 3, 27), _at(date(2026, 10, 13), 13), retracted=True)
        _listing(conn, LOST, "ATLN", date(2025, 3, 27), listed)
        _listing(conn, LOST, "ATLN", date(2025, 3, 27), _at(date(2026, 11, 15), 11), retracted=True)
        _listing(conn, RECEIVER, "ATLN", date(2024, 12, 9), listed)
        _change(conn, LOST, change_known)
        for day in _sessions(date(2026, 6, 1), LOST_BARS[1]):
            _bar(conn, SPY, day, 500.0)
        for day in _sessions(date(2026, 6, 1), date(2026, 7, 1)):
            _bar(conn, RECEIVER, day, 10.0)
        for day in _sessions(*LOST_BARS):
            _bar(conn, LOST, day, 12.0)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "backfill-1",
                "started_at": _at(date(2026, 10, 20)),
                "finished_at": _at(date(2026, 10, 20)),
                "status": "ok",
                "source": "alpaca",
                "mode": "backfill",
                "rows_added": 0,
                "chunk_cursor": f"since={SINCE.isoformat()};through=2026-10-14",
                "message": "",
            },
        )
    with open_for_write(settings) as conn:
        lost = [(LOST, day) for day in _sessions(*LOST_BARS)]
        retract(
            conn, "prices_daily", lost, "lost id", "rederive", "r", at=_at(date(2026, 11, 15), 12)
        )
        registry.open_data_release(conn, conn, name="rederive", backup_path="b.duckdb", reason="x")


class _Alpaca:
    """Alpaca's answer: `ATLN` through 2026-07-01, `CIRC` from the change on."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []

    def __call__(
        self, symbols: list[str], start: date, end: date, *, asof: date | None = None
    ) -> dict[str, Any]:
        self.asked.append(tuple(symbols))
        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            days = _sessions(start, end)
            if symbol == "ATLN":
                days = [d for d in days if d < CHANGED]
            elif symbol == "CIRC":
                days = [d for d in days if d >= CHANGED]
            bars[symbol] = [
                {
                    "t": _at(d, 12).isoformat().replace("+00:00", "Z"),
                    "o": 12.0,
                    "h": 12.0,
                    "l": 12.0,
                    "c": 12.0,
                    "v": 100,
                    "n": 5,
                    "vw": 12.0,
                }
                for d in days
            ]
        return {"feed": "sip", "bars": bars}


def _source(settings: Settings, alpaca: _Alpaca) -> StorePriceSource:
    return StorePriceSource(
        settings,
        clock=lambda: T,
        fetch_bars=alpaca,
        fetch_actions=lambda *_: {},
        fill_handovers=True,
    )


def _receiver_sessions(settings: Settings) -> list[date]:
    with _read(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT session FROM prices_daily WHERE security_id = ? AND session >= ? "
            "ORDER BY 1",
            [RECEIVER, LOST_BARS[0]],
        ).fetchall()
    return [row[0] for row in rows]


def test_the_receiver_gets_the_lost_ids_sessions_under_its_new_symbol(settings: Settings) -> None:
    _store(settings)
    plan = fill_holes(
        settings, prices=_source(settings, _Alpaca()), since=SINCE, clock=lambda: T, dry_run=True
    )
    months = {hole.window[0].replace(day=1) for hole in plan.holes if hole.security_id == RECEIVER}
    assert {date(2026, 10, 1), date(2026, 11, 1)} <= months
    alpaca = _Alpaca()
    result = fill_holes(settings, prices=_source(settings, alpaca), since=SINCE, clock=lambda: T)
    assert result.runs and all(run.status == FILLED for run in result.runs)
    assert ("CIRC", "SPY") in alpaca.asked
    assert _receiver_sessions(settings) == _sessions(*LOST_BARS)


def test_a_change_known_after_the_clock_is_not_carried(settings: Settings) -> None:
    """No look-ahead: the receiver keeps `ATLN` until the change is known."""
    _store(settings, change_known=_at(date(2026, 11, 16)))
    with _read(settings) as conn:
        resolver = store_resolver(conn, T, settings)
        held = _held_tickers(conn, T)
    assert resolver.symbols(RECEIVER, date(2026, 10, 1), date(2026, 10, 31)) == ["ATLN"]
    assert held(LOST, date(2026, 10, 8)) == ["ATLN"]


def test_the_lost_ids_held_tickers_follow_its_own_change(settings: Settings) -> None:
    _store(settings)
    with _read(settings) as conn:
        held = _held_tickers(conn, T)
    assert held(LOST, date(2026, 7, 1)) == ["ATLN"]
    assert held(LOST, date(2026, 10, 8)) == ["ATLN", "CIRC"]


def _row(sid: str, ticker: str, valid_from: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "provenance": "filing",
    }


def _change_row(sid: str) -> dict[str, Any]:
    return {"security_id": sid, "process_date": CHANGED, "old_symbol": "ATLN", "new_symbol": "CIRC"}


OCT = (date(2026, 10, 1), date(2026, 10, 31))


def test_an_orphan_change_carries_to_its_issuers_one_heir() -> None:
    resolver = ListingResolver(
        [_row(RECEIVER, "ATLN", date(2024, 12, 9))], symbol_changes=[_change_row(LOST)]
    )
    assert resolver.symbols(RECEIVER, *OCT) == ["CIRC"]
    assert resolver.resolve("CIRC", date(2026, 10, 8)) == RECEIVER


def test_an_orphan_change_never_carries_to_another_issuer() -> None:
    other = "0000000777"
    resolver = ListingResolver(
        [_row(other, "ATLN", date(2024, 12, 9))], symbol_changes=[_change_row(LOST)]
    )
    assert resolver.symbols(other, *OCT) == ["ATLN"]


def test_an_orphan_change_with_two_heirs_stays_put() -> None:
    twin = "0001605888@2025-01-02"
    resolver = ListingResolver(
        [_row(RECEIVER, "ATLN", date(2024, 12, 9)), _row(twin, "ATLN", date(2025, 1, 2))],
        symbol_changes=[_change_row(LOST)],
    )
    assert "CIRC" not in resolver.symbols(RECEIVER, *OCT) + resolver.symbols(twin, *OCT)


def test_a_change_of_a_listed_id_is_never_carried() -> None:
    """The lost id still has a listing: the change is its own, as before.
    (Its row names another class: a same-class later successor would own the
    receiver's bars from its start, #3474.)"""
    lost_row = {**_row(LOST, "SQL", date(2025, 3, 27)), "class_title": "Class B Common Stock"}
    resolver = ListingResolver(
        [_row(RECEIVER, "ATLN", date(2024, 12, 9)), lost_row],
        symbol_changes=[_change_row(LOST)],
    )
    assert resolver.symbols(RECEIVER, *OCT) == ["ATLN"]

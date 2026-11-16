"""#3471: a symbol change refused for a sibling class still ends the old ticker.

Cartesian Growth / AlTi Global (CIK 1838615), as the real store holds it on
2026-11-15: the bare id lists the SPAC's `GLBL` from 2022-03-17 with no end,
its Class A id lists `ALTI` from 2023-04-17, and Alpaca's `GLBL -> ALTI` of
2023-01-04 sits on the bare id. The change is refused (the class id keeps
`ALTI`), and before #3471 the bare id kept `GLBL` open-ended, so a later
reuser's `GLBL` bars (2024-09-17 on, avg close 24.96 against ALTI's 4.69) were
stored on it and read as its own bar.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.prices import bar_known_at
from tradepartner.calendar import is_session
from tradepartner.config import Settings
from tradepartner.repair import repair_resolution
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

BASE = "0001838615"
CLASS = "0001838615:class-a-common-stock"
SPY = "0000884394"
CHANGED = date(2023, 1, 4)
FOREIGN = (date(2024, 9, 17), date(2024, 10, 31))
RUN = datetime(2026, 11, 15, 18, tzinfo=UTC)


def _at(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _sessions(first: date, last: date) -> list[date]:
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return [day for day in days if is_session(day)]


def _listing(conn: Any, sid: str, ticker: str, valid_from: date, title: str) -> None:
    known = _at(valid_from, 21)
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "NYSE ARCA" if ticker == "SPY" else "NASDAQ",
            "class_title": title,
            "valid_from": valid_from,
            "retracted": False,
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


@pytest.fixture
def store(tmp_path: Path) -> Settings:
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_missing_share": 1.0},
    )
    with open_for_write(settings) as conn:
        init_schema(conn)
        for sid, known in [
            (BASE, date(2021, 2, 5)),
            (CLASS, date(2023, 4, 17)),
            (SPY, date(2021, 1, 4)),
        ]:
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": sid[:10],
                    "name": sid,
                    "benchmark": sid == SPY,
                    "known_at": _at(known, 21),
                    "ingested_at": _at(known, 21),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        _listing(conn, SPY, "SPY", date(1993, 1, 29), "Common Stock")
        _listing(conn, BASE, "GLBL", date(2022, 3, 17), "Class A ordinary shares")
        _listing(conn, CLASS, "ALTI", date(2023, 4, 17), "Class A Common stock")
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": BASE,
                "old_symbol": "GLBL",
                "new_symbol": "ALTI",
                "old_cusip": "02157E106",
                "new_cusip": "02157E106",
                "process_date": CHANGED,
                "source_action_id": "0aa52cca",
                "known_at": datetime(2023, 1, 5, 21, tzinfo=UTC),
                "ingested_at": datetime(2026, 10, 16, 9, tzinfo=UTC),
                "source": "alpaca",
                "provenance": "action",
            },
        )
        for day in _sessions(date(2022, 11, 1), FOREIGN[1]):
            _bar(conn, SPY, day, 500.0)
        for day in _sessions(date(2022, 11, 1), date(2023, 1, 3)):
            _bar(conn, BASE, day, 9.86)  # the SPAC
        for day in _sessions(CHANGED, date(2023, 4, 14)):
            _bar(conn, BASE, day, 10.5)  # AlTi as ALTI before its class id's row
        for day in _sessions(*FOREIGN):
            _bar(conn, BASE, day, 24.96)  # a later reuser of GLBL
        for day in _sessions(date(2023, 4, 17), FOREIGN[1]):
            _bar(conn, CLASS, day, 4.69)  # the real ALTI
    return settings


def test_the_reusers_bars_on_the_bare_id_are_found_and_no_alti_bar_is(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    assert set(result.found.bars) == {(BASE, day) for day in _sessions(*FOREIGN)}
    assert not [key for key in (*result.found.bars, *result.orphans) if key[0] == CLASS]
    assert not result.duplicates
    # The SPAC's own GLBL bars before the change, and its ALTI bars up to the
    # class id's row, stay.
    assert all(day >= FOREIGN[0] for _, day in result.found.bars)


MP_BASE = "0001801368"
MP_CLASS = "0001801368:common-stock"
MP_CHANGED = date(2020, 11, 18)
MP_LISTED = date(2021, 3, 22)


@pytest.fixture
def mp_store(tmp_path: Path) -> Settings:
    """Fortress Value -> MP Materials (#3471 cross-check): FVAC -> MP on
    2020-11-18 on the bare id, MP's class id listed from 2021-03-22; MP's
    bars of the window sit on the bare id."""
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "mp.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_missing_share": 1.0},
    )
    with open_for_write(settings) as conn:
        init_schema(conn)
        for sid, known in [
            (MP_BASE, date(2020, 3, 6)),
            (MP_CLASS, MP_LISTED),
            (SPY, date(2020, 1, 2)),
        ]:
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": sid[:10],
                    "name": sid,
                    "benchmark": sid == SPY,
                    "known_at": _at(known, 21),
                    "ingested_at": _at(known, 21),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        _listing(conn, SPY, "SPY", date(1993, 1, 29), "Common Stock")
        _listing(conn, MP_BASE, "FVAC", date(2020, 6, 9), "Class A common stock")
        _listing(conn, MP_CLASS, "MP", MP_LISTED, "Common Stock")
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": MP_BASE,
                "old_symbol": "FVAC",
                "new_symbol": "MP",
                "old_cusip": "x",
                "new_cusip": "y",
                "process_date": MP_CHANGED,
                "source_action_id": "mp1",
                "known_at": _at(MP_CHANGED, 23),
                "ingested_at": datetime(2026, 10, 16, 9, tzinfo=UTC),
                "source": "alpaca",
                "provenance": "action",
            },
        )
        for day in _sessions(date(2020, 10, 1), date(2021, 5, 28)):
            _bar(conn, SPY, day, 350.0)
        for day in _sessions(date(2020, 10, 1), date(2021, 3, 19)):
            _bar(conn, MP_BASE, day, 10.0 if day < MP_CHANGED else 20.0)
        for day in _sessions(MP_LISTED, date(2021, 5, 28)):
            _bar(conn, MP_CLASS, day, 30.0)
    return settings


def test_the_window_before_the_siblings_row_orphans_nothing(mp_store: Settings) -> None:
    result = repair_resolution(mp_store, clock=lambda: RUN, dry_run=True)
    assert not result.found.bars
    assert not result.orphans

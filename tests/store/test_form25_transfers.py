"""A Form 25 for a security that keeps trading is a transfer (#3242).

Fixtures are the rows of the 11-09 sitting re-rehearsal, after step 9's EDGAR
write (`tests/fixtures/form25_transfers`: `listings.csv`, `delistings.csv`,
and the bars the step-9 checkpoint held, from 25 days before each company's
first Form 25 effective date). Sixteen CIKs, SPAC mergers, renames and
exchange transfers: every Form 25 was read as a terminal delisting while bars
carried on under the next listing.

**Reading (owner, 2026-11-10).** A Form 25 ends its listing as a *transfer*,
not a delisting, when the security keeps trading after `effective_on`,
judged only on bars with `known_at <= T`: a bar within
`master.transfer_window_sessions` of `effective_on`, or any bar between
`effective_on` and the next listing of the security on another exchange. A
terminal delisting (no such bar) still ends at its last bar.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.health import (
    NO_BARS_AFTER_DELISTING,
    NON_OVERLAPPING_LISTINGS,
    integrity_checks,
)
from tradepartner.repair import misattributed, store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.delistings import (
    DELISTED,
    TRANSFERRED,
    delistings_as_of,
    derive_listing_ends,
    listing_ends_as_of,
)
from tradepartner.store.schema import init_schema

FIXTURES = Path(__file__).parent.parent / "fixtures" / "form25_transfers"
T = datetime(2026, 11, 10, 12, tzinfo=UTC)
CIKS = [
    "0000101538",
    "0000732026",
    "0001104485",
    "0001130713",
    "0001309082",
    "0001410098",
    "0001500881",
    "0001595974",
    "0001789029",
    "0001823524",
    "0001823587",
    "0001825079",
    "0001830029",
    "0001847874",
    "0001871745",
    "0002043699",
]


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("+0000", "+00:00"))


def _csv(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _load(conn: duckdb.DuckDBPyConnection, *, bars: bool = True) -> None:
    init_schema(conn)
    for r in _csv("listings.csv"):
        insert_row(
            conn,
            "listings",
            {
                "security_id": r["security_id"],
                "ticker": r["ticker"],
                "exchange": r["exchange"],
                "class_title": r["class_title"] or None,
                "valid_from": date.fromisoformat(r["valid_from"]),
                "known_at": _utc(r["known_at"]),
                "ingested_at": _utc(r["ingested_at"]),
                "source": r["source"],
                "provenance": r["provenance"],
                "retracted": r["retracted"] == "true",
            },
        )
    for r in _csv("delistings.csv"):
        insert_row(
            conn,
            "delistings",
            {
                "security_id": r["security_id"],
                "form": r["form"],
                "class_title": r["class_title"],
                "exchange": r["exchange"],
                "filed_at": _utc(r["filed_at"]),
                "effective_on": date.fromisoformat(r["effective_on"]),
                "known_at": _utc(r["known_at"]),
                "ingested_at": _utc(r["ingested_at"]),
                "source": r["source"],
                "provenance": r["provenance"],
            },
        )
    if bars:
        for r in _csv("bars.csv"):
            close = float(r["close"])
            session = date.fromisoformat(r["session"])
            insert_row(
                conn,
                "prices_daily",
                {
                    "security_id": r["security_id"],
                    "session": session,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000,
                    "known_at": session_close(session),
                    "ingested_at": session_close(session),
                    "source": "alpaca_sip",
                    "provenance": "bar",
                },
            )


def _synthetic_bars(conn: duckdb.DuckDBPyConnection) -> None:
    """The dump holds bars for six companies only. For the ten others, a bar
    on every session from 10 before to 40 after each Form 25's effective day:
    the trading the owner's reading rests on (the issue's Form 25s are
    transfers, renames and SPAC mergers, none a stop), written under the
    Form 25's own id. Without any bar a Form 25's end is the conservative
    state, which is not the question here."""
    from tradepartner.calendar import next_session, previous_session

    have = {r["security_id"].split(":")[0].split("@")[0] for r in _csv("bars.csv")}
    for r in _csv("delistings.csv"):
        sid = r["security_id"].split("@")[0]
        if sid.split(":")[0] in have:
            continue
        day = date.fromisoformat(r["effective_on"])
        start = day
        for _ in range(10):
            start = previous_session(start)
        for _ in range(51):
            if not conn.execute(
                "SELECT 1 FROM prices_daily WHERE security_id = ? AND session = ?", [sid, start]
            ).fetchone():
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": start,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1000,
                        "known_at": session_close(start),
                        "ingested_at": session_close(start),
                        "source": "alpaca_sip",
                        "provenance": "bar",
                    },
                )
            start = next_session(start)


@pytest.fixture
def store_settings(tmp_path: Path) -> Settings:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "f25.duckdb")})
    with open_for_write(settings) as conn:
        _load(conn)
        _synthetic_bars(conn)
    return settings


@pytest.fixture
def conn(store_settings: Settings) -> Any:
    with duckdb.connect(store_settings.store.path, read_only=True) as handle:
        yield handle


def _end(
    ends: pl.DataFrame, sid: str, ticker: str, exchange: str, valid_from: str
) -> dict[str, Any]:
    [row] = ends.filter(
        (pl.col("security_id") == sid)
        & (pl.col("ticker") == ticker)
        & (pl.col("exchange") == exchange)
        & (pl.col("valid_from") == date.fromisoformat(valid_from))
    ).to_dicts()
    return row


def test_the_form25_of_a_security_that_trades_on_is_a_transfer_to_its_next_listing(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    ends = listing_ends_as_of(conn, T, store_settings)
    # Continuous bars across the Form 25's effective day; the next listing
    # starts months later, from a cover page.
    rubi = _end(ends, "0001595974", "RUBI", "NYSE", "2019-07-31")
    assert (rubi["status"], rubi["end_session"]) == (TRANSFERRED, date(2020, 8, 7))
    ostk = _end(ends, "0001130713", "OSTK", "NASDAQ", "2020-08-06")
    assert (ostk["status"], ostk["end_session"]) == (TRANSFERRED, date(2024, 2, 22))
    ysac = _end(ends, "0001823587", "YSAC", "NASDAQ", "2021-08-06")
    assert (ysac["status"], ysac["end_session"]) == (TRANSFERRED, date(2022, 3, 25))
    # Bars resume after a store hole; the next listing is on another exchange.
    bbby = _end(ends, "0001130713", "BBBY", "NYSE", "2025-10-27")
    assert (bbby["status"], bbby["end_session"]) == (TRANSFERRED, date(2026, 11, 4))
    # A late Form 25 for a line whose successor started long before it.
    crmd = _end(ends, "0001410098", "CRMD", "NYSE_AMERICAN", "2020-08-10")
    assert (crmd["status"], crmd["end_session"]) == (TRANSFERRED, date(2020, 11, 4))
    # No next listing is known yet for NHIC (its bars go on): a reused
    # ticker's bars look the same, so nothing is moved to yet.
    nhic = _end(ends, "0002043699", "NHIC", "NASDAQ", "2025-05-12")
    assert nhic["status"] == DELISTED


def test_a_terminal_delisting_with_no_bars_after_it_still_ends_the_listing(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    # VLD: the NYSE line stopped (bars end 2024-09-10, effective 2024-10-06);
    # the NASDAQ relisting 13 months later is another id, `@2025-11-12`.
    ends = listing_ends_as_of(conn, T, store_settings)
    vld = _end(ends, "0001825079:common-stock", "VLD", "NYSE", "2021-11-16")
    assert vld["status"] == DELISTED
    adex = _end(ends, "0001830029", "ADEX", "NYSE_AMERICAN", "2023-03-28")
    assert adex["status"] == DELISTED


def test_the_transfer_reading_uses_only_bars_known_at_t(tmp_path: Path) -> None:
    """No look-ahead. "Bars known at T" is the evidence: the same store reads
    as a delisting at a T before the first bar after the effective day is
    known (the conservative state), and as a transfer from the close of that
    bar's session, though the store holds the bar throughout."""
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "la.duckdb")})
    filed = datetime(2018, 3, 1, 21, tzinfo=UTC)
    with open_for_write(settings) as writer:
        init_schema(writer)
        # The NASDAQ listing is known in advance (a filing in January).
        insert_row(writer, "listings", _listing("SEC_LA", "AAA", "NYSE", date(2018, 1, 2)))
        insert_row(
            writer,
            "listings",
            {
                **_listing("SEC_LA", "BBB", "NASDAQ", date(2018, 9, 4)),
                "known_at": session_close(date(2018, 1, 3)),
            },
        )
        insert_row(
            writer,
            "delistings",
            {
                "security_id": "SEC_LA",
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": filed,
                "effective_on": date(2018, 3, 12),
                "known_at": filed,
                "ingested_at": filed,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for session in (date(2018, 3, 9), date(2018, 3, 13)):
            _bar(writer, "SEC_LA", session)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        before = listing_ends_as_of(conn, session_close(date(2018, 3, 12)), settings)
        at = listing_ends_as_of(conn, session_close(date(2018, 3, 13)), settings)
        unfiled = listing_ends_as_of(conn, session_close(date(2018, 2, 28)), settings)
    key = ("SEC_LA", "AAA", "NYSE", "2018-01-02")
    assert _end(unfiled, *key)["status"] == "listed"
    assert _end(before, *key)["status"] == DELISTED
    assert _end(at, *key)["status"] == TRANSFERRED
    assert _end(at, *key)["end_session"] == date(2018, 8, 31)


def test_derive_is_pure_on_the_bars_it_is_given(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    listings = listing_ends_as_of(conn, T, store_settings).select(
        "security_id",
        "ticker",
        "exchange",
        "class_title",
        "valid_from",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    )
    delistings = delistings_as_of(conn, T)
    none = pl.DataFrame([], schema={"security_id": pl.Utf8(), "session": pl.Date()})
    ends = derive_listing_ends(
        listings,
        delistings,
        none,
        transfer_window_sessions=store_settings.master.transfer_window_sessions,
    )
    # With no bars at all nothing trades on: a Form 25 is a delisting.
    rubi = _end(ends, "0001595974", "RUBI", "NYSE", "2019-07-31")
    assert rubi["status"] == DELISTED


def _cik_rows(violations: pl.DataFrame) -> list[str]:
    return [
        sid
        for sid in violations["security_id"].to_list()
        if sid.split(":")[0].split("@")[0] in CIKS
    ]


def test_the_sixteen_ciks_pass_both_health_rules(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    checks = {c.rule: c for c in integrity_checks(conn, T, store_settings)}
    # NHIC (0002043699) has no next listing yet: its 22 bars stay flagged until
    # the cover page naming the new ticker is read (reported on #3242).
    bars = [s for s in _cik_rows(checks[NO_BARS_AFTER_DELISTING].violations) if s != "0002043699"]
    overlaps = _cik_rows(checks[NON_OVERLAPPING_LISTINGS].violations)
    assert bars == []
    assert overlaps == []


def test_repair_resolution_keeps_the_bars_after_a_transfer(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    resolver = store_resolver(conn, T, store_settings)
    found = misattributed(resolver, _all_bars(conn), ()).bars
    kept = {"0001130713", "0001595974", "0001823587"}
    assert [key for key in found if key[0] in kept and key[1] <= date(2026, 11, 3)] == []


def _all_bars(conn: duckdb.DuckDBPyConnection) -> Any:
    from tradepartner.adapters.alpaca_prices import BAR_SOURCES
    from tradepartner.repair import _Counted

    return _Counted(conn, "prices_daily", "session", sorted(BAR_SOURCES))


def _listing(sid: str, ticker: str, exchange: str, valid_from: date) -> dict[str, Any]:
    known = session_close(valid_from)
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": exchange,
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
            "known_at": session_close(session),
            "ingested_at": session_close(session),
            "source": "alpaca_sip",
            "provenance": "bar",
        },
    )


def test_a_late_form25_with_no_bars_after_it_still_fails_the_overlap_rule(tmp_path: Path) -> None:
    # Control: no bar after the effective day, so no transfer. The NYSE line
    # started eight months before the filing that ends the NYSE_AMERICAN one.
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "c.duckdb")})
    filed = datetime(2019, 1, 10, 21, tzinfo=UTC)
    with open_for_write(settings) as writer:
        init_schema(writer)
        for row in (
            _listing("SEC_LATE", "LATE", "NYSE_AMERICAN", date(2018, 1, 2)),
            _listing("SEC_LATE", "LATER", "NYSE", date(2018, 5, 1)),
        ):
            insert_row(writer, "listings", row)
        insert_row(
            writer,
            "delistings",
            {
                "security_id": "SEC_LATE",
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NYSE_AMERICAN",
                "filed_at": filed,
                "effective_on": date(2019, 1, 20),
                "known_at": filed,
                "ingested_at": filed,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for session in (date(2018, 12, 31), date(2019, 1, 2)):
            insert_row(
                writer,
                "prices_daily",
                {
                    "security_id": "SEC_LATE",
                    "session": session,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1,
                    "known_at": session_close(session),
                    "ingested_at": session_close(session),
                    "source": "alpaca_sip",
                    "provenance": "bar",
                },
            )
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        checks = {c.rule: c for c in integrity_checks(conn, T, settings)}
    assert "SEC_LATE" in checks[NON_OVERLAPPING_LISTINGS].violations["security_id"].to_list()


def test_one_form25_ends_both_same_day_rows_of_its_line() -> None:
    # AEVA and IPV: two cover-page tickers of one NYSE line from one day.
    listings = pl.DataFrame(
        [
            _listing("SEC_TIE", "AEVA", "NYSE", date(2021, 6, 2)),
            _listing("SEC_TIE", "IPV", "NYSE", date(2021, 6, 2)),
        ]
    )
    filed = datetime(2025, 1, 27, 21, tzinfo=UTC)
    delistings = pl.DataFrame(
        [
            {
                "security_id": "SEC_TIE",
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": filed,
                "effective_on": date(2025, 2, 6),
                "known_at": filed,
                "ingested_at": filed,
                "source": "edgar",
                "provenance": "filing",
            }
        ]
    )
    bars = pl.DataFrame([], schema={"security_id": pl.Utf8(), "session": pl.Date()})
    ends = derive_listing_ends(listings, delistings, bars, transfer_window_sessions=5)
    assert set(ends["status"].to_list()) == {DELISTED}
    assert set(ends["delisting_filed_at"].to_list()) == {filed}

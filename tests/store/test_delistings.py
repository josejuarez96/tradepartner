"""Tests for tradepartner.store.delistings (T8b).

Plan T8b tests: preferred-class Form 25 leaves the common listed; 25-NSE
ends the listing; a transfer shows once the new listing is known; the
security is delisted at a T between the filing and the new listing's
`known_at`; a clean merger ends on the session before the filing. Plus
resolution of filings to a class (`build_delistings`) and the look-ahead
checks: `listing_ends_as_of` is invariant under truncation of the fixture
store.

Fixture cases are documented in `tests/fixtures/universe/README.md`;
synthetic stores state their own rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps

from tradepartner.adapters.filings import DelistingFiling
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.delistings import (
    DelistingBuild,
    build_delistings,
    delistings_as_of,
    listing_ends_as_of,
    write_delistings,
)
from tradepartner.store.master import MasterBuild

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LATE = datetime(2021, 1, 4, 21, 0, tzinfo=UTC)  # after every fixture event


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _at(year: int, month: int, day: int, hour: int = 20, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _status(df: pl.DataFrame, security_id: str, exchange: str) -> dict[str, Any]:
    rows = df.filter(
        (pl.col("security_id") == security_id) & (pl.col("exchange") == exchange)
    ).to_dicts()
    assert len(rows) == 1, rows
    return rows[0]


# ---------------------------------------------------------------- fixture store


class TestFixtureCases:
    def test_preferred_class_form_25_leaves_common_listed(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        df = listing_ends_as_of(fixture_store, LATE, _settings())
        pfd = _status(df, "SEC_DUAL_PFD", "NYSE")
        assert pfd["status"] == "delisted"
        assert pfd["delisting_form"] == "25"
        assert pfd["end_session"] == date(2018, 9, 21)
        for common in ("SEC_DUAL_A", "SEC_DUAL_B"):
            row = _status(df, common, "NYSE")
            assert row["status"] == "listed"
            assert row["end_session"] is None

    def test_25_nse_ends_the_listing(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        row = _status(
            listing_ends_as_of(fixture_store, LATE, _settings()), "SEC_25NSE", "NYSE_AMERICAN"
        )
        assert row["status"] == "delisted"
        assert row["delisting_form"] == "25-NSE"
        assert row["end_session"] == date(2018, 8, 22)
        assert row["effective_on"] == date(2018, 9, 2)

    def test_clean_merger_ends_on_session_before_filing(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        row = _status(
            listing_ends_as_of(fixture_store, LATE, _settings()), "SEC_CLEAN_MERGER", "NYSE"
        )
        assert row["status"] == "delisted"
        assert row["end_session"] == date(2018, 7, 24)

    def test_listed_until_the_filing_is_known(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        filed = _at(2018, 7, 25)
        before = _status(
            listing_ends_as_of(fixture_store, filed - PROBE_EPSILON, _settings()),
            "SEC_CLEAN_MERGER",
            "NYSE",
        )
        assert before["status"] == "listed"
        assert before["end_session"] is None
        assert before["delisting_form"] is None
        after = _status(
            listing_ends_as_of(fixture_store, filed, _settings()), "SEC_CLEAN_MERGER", "NYSE"
        )
        assert after["status"] == "delisted"

    def test_delisted_between_filing_and_new_listing_known(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        probe = _at(2018, 10, 25, 20, 0)  # README probe T for SEC_TRANSFER
        df = listing_ends_as_of(fixture_store, probe, _settings())
        nyse = _status(df, "SEC_TRANSFER", "NYSE")
        assert nyse["status"] == "delisted"
        assert nyse["end_session"] == date(2018, 10, 25)  # last bar known at T
        assert df.filter(
            (pl.col("security_id") == "SEC_TRANSFER") & (pl.col("exchange") == "NASDAQ")
        ).is_empty()

    def test_transfer_once_new_listing_known(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        new_known = _at(2018, 11, 1)
        df = listing_ends_as_of(fixture_store, new_known, _settings())
        nyse = _status(df, "SEC_TRANSFER", "NYSE")
        assert nyse["status"] == "transferred"
        assert nyse["end_session"] == date(2018, 10, 25)  # session before NASDAQ valid_from
        nasdaq = _status(df, "SEC_TRANSFER", "NASDAQ")
        assert nasdaq["status"] == "listed"
        assert nasdaq["end_session"] is None

    def test_transfer_window_comes_from_config(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # NASDAQ valid_from 2018-10-26 is 3 sessions after the 2018-10-23 filing.
        narrow = _settings(master={"transfer_window_sessions": 2})
        row = _status(listing_ends_as_of(fixture_store, LATE, narrow), "SEC_TRANSFER", "NYSE")
        assert row["status"] == "delisted"

    def test_every_fixture_delisting_ends_a_listing(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        df = listing_ends_as_of(fixture_store, LATE, _settings())
        ended = set(df.filter(pl.col("status") != "listed")["security_id"].to_list())
        filed = set(delistings_as_of(fixture_store, LATE)["security_id"].to_list())
        assert ended == filed

    def test_security_filter(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        df = listing_ends_as_of(fixture_store, LATE, _settings(), security_ids=["SEC_25NSE"])
        assert set(df["security_id"].to_list()) == {"SEC_25NSE"}
        empty = listing_ends_as_of(fixture_store, LATE, _settings(), security_ids=[])
        assert empty.is_empty()
        assert empty.columns == df.columns

    def test_bare_date_and_naive_t_rejected(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            listing_ends_as_of(fixture_store, date(2019, 1, 2), _settings())  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            listing_ends_as_of(fixture_store, datetime(2019, 1, 2), _settings())  # noqa: DTZ001


# -------------------------------------------------------------- synthetic store


def _new_store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


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
        "effective_on": filed_at.date() + timedelta(days=10),
        "known_at": filed_at,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _bar(sid: str, session: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "session": session,
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "volume": 1000,
        "known_at": session_close(session),
        "ingested_at": INGESTED_AT,
        "source": "alpaca",
        "provenance": "bar",
    }


@pytest.fixture
def synthetic() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = _new_store()
    try:
        yield conn
    finally:
        conn.close()


class TestSynthetic:
    def test_filing_after_close_uses_new_york_date(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # 2019-03-06 01:30 UTC is 2019-03-05 20:30 in New York: filing session
        # 03-05, so a window of 1 reaches back to 03-04 (UTC's 03-06 would not).
        insert_row(
            synthetic, "listings", _listing("S", "A", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(
            synthetic,
            "delistings",
            _delisting("S", "NYSE", datetime(2019, 3, 6, 1, 30, tzinfo=UTC)),
        )
        insert_row(
            synthetic, "listings", _listing("S", "A", "NASDAQ", date(2019, 3, 4), _at(2019, 3, 6))
        )
        one = _settings(master={"transfer_window_sessions": 1})
        assert (
            _status(listing_ends_as_of(synthetic, LATE, one), "S", "NYSE")["status"]
            == "transferred"
        )

    def test_weekend_filing_rolls_to_next_session(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Saturday 2019-03-09 filing: session Monday 03-11, window of 1 reaches
        # 03-12 (Friday 03-08 as the filing session would stop at 03-11).
        insert_row(
            synthetic, "listings", _listing("S", "A", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 9, 15, 0)))
        insert_row(
            synthetic, "listings", _listing("S", "A", "NASDAQ", date(2019, 3, 12), _at(2019, 3, 12))
        )
        one = _settings(master={"transfer_window_sessions": 1})
        assert (
            _status(listing_ends_as_of(synthetic, LATE, one), "S", "NYSE")["status"]
            == "transferred"
        )

    def test_new_listing_first_seen_after_window_is_delisted_then_relisted(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # The master's shape: valid_from is the session of the cover page that
        # first shows the new exchange. Seen 3 weeks after the Form 25, it is
        # outside the window: delisted, then a new listing (no transfer).
        insert_row(
            synthetic, "listings", _listing("S", "A", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        for day in (date(2019, 3, 14), date(2019, 3, 15), date(2019, 3, 26), date(2019, 3, 27)):
            insert_row(synthetic, "prices_daily", _bar("S", day))
        insert_row(
            synthetic, "listings", _listing("S", "A", "NASDAQ", date(2019, 3, 26), _at(2019, 3, 26))
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        nyse = _status(df, "S", "NYSE")
        assert nyse["status"] == "delisted"
        assert nyse["end_session"] == date(2019, 3, 15)  # bars from the new listing excluded
        assert _status(df, "S", "NASDAQ")["status"] == "listed"

    def test_relisting_outside_window_is_not_a_transfer(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(
            synthetic, "listings", _listing("S", "OLD", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        for day in (date(2019, 3, 1), date(2019, 3, 4), date(2019, 3, 5)):
            insert_row(synthetic, "prices_daily", _bar("S", day))
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        # Relisted on NASDAQ two months later: far outside 5 sessions.
        insert_row(
            synthetic,
            "listings",
            _listing("S", "OLD", "NASDAQ", date(2019, 5, 1), _at(2019, 4, 25)),
        )
        insert_row(synthetic, "prices_daily", _bar("S", date(2019, 5, 1)))
        df = listing_ends_as_of(synthetic, LATE, _settings())
        nyse = _status(df, "S", "NYSE")
        assert nyse["status"] == "delisted"
        # The relisting's bar is not the NYSE listing's end.
        assert nyse["end_session"] == date(2019, 3, 5)
        assert _status(df, "S", "NASDAQ")["status"] == "listed"

    def test_same_exchange_later_listing_is_not_a_transfer(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(
            synthetic, "listings", _listing("S", "AAA", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        insert_row(
            synthetic, "listings", _listing("S", "BBB", "NYSE", date(2019, 3, 7), _at(2019, 3, 6))
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        aaa = df.filter(pl.col("ticker") == "AAA").to_dicts()[0]
        assert aaa["status"] == "delisted"

    def test_form_25_ends_only_the_latest_listing_on_its_exchange(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Ticker change on NYSE, then a Form 25: only the current ticker ends.
        insert_row(
            synthetic, "listings", _listing("S", "AAA", "NYSE", date(2018, 1, 2), _at(2017, 12, 1))
        )
        insert_row(
            synthetic, "listings", _listing("S", "BBB", "NYSE", date(2018, 6, 1), _at(2018, 5, 30))
        )
        insert_row(synthetic, "prices_daily", _bar("S", date(2019, 3, 1)))
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        df = listing_ends_as_of(synthetic, LATE, _settings()).sort("valid_from")
        assert df["status"].to_list() == ["listed", "delisted"]

    def test_delisting_with_no_bar_has_no_end_session(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(
            synthetic, "listings", _listing("S", "AAA", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        row = _status(listing_ends_as_of(synthetic, LATE, _settings()), "S", "NYSE")
        assert row["status"] == "delisted"
        assert row["end_session"] is None

    def test_filing_on_another_exchange_leaves_listing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(
            synthetic, "listings", _listing("S", "AAA", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NASDAQ", _at(2019, 3, 5)))
        row = _status(listing_ends_as_of(synthetic, LATE, _settings()), "S", "NYSE")
        assert row["status"] == "listed"

    def test_end_session_grows_with_bars_known_at_t(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Filed before the last trading day: the end is the last bar known at T.
        insert_row(
            synthetic, "listings", _listing("S", "AAA", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 1)))
        for day in (date(2019, 3, 1), date(2019, 3, 4), date(2019, 3, 5)):
            insert_row(synthetic, "prices_daily", _bar("S", day))
        mid = listing_ends_as_of(synthetic, session_close(date(2019, 3, 4)), _settings())
        assert _status(mid, "S", "NYSE")["end_session"] == date(2019, 3, 4)
        late = listing_ends_as_of(synthetic, LATE, _settings())
        assert _status(late, "S", "NYSE")["end_session"] == date(2019, 3, 5)


# ------------------------------------------- #818: real cases from the 10-04 store


def _ny(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """A New York wall-clock acceptance (EDGAR's stamps), as UTC."""
    offset = 4 if 3 < month < 11 else 5  # EDT / EST, close enough for these dates
    return datetime(year, month, day, hour + offset, minute, tzinfo=UTC)


def _row(
    sid: str,
    ticker: str,
    exchange: str,
    valid_from: date,
    class_title: str | None = "Common Stock",
) -> dict[str, Any]:
    return {
        **_listing(
            sid,
            ticker,
            exchange,
            valid_from,
            _ny(valid_from.year, valid_from.month, valid_from.day, 8, 0),
        ),
        "class_title": class_title,
        "provenance": "filing" if class_title is not None else "snapshot_static",
    }


def _form_25(sid: str, exchange: str, filed_at: datetime, class_title: str) -> dict[str, Any]:
    return {**_delisting(sid, exchange, filed_at), "form": "25-NSE", "class_title": class_title}


class TestExchangeTagFlip:
    """#818 part 1: the filer re-tags its exchange on a later cover page
    (NYSE_AMERICAN <-> NYSE) and the Form 25 names the old tag. The filing
    ends the security's latest listing with the same ticker."""

    def test_asxc_form_25_on_the_old_tag_ends_the_retagged_listing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Store 2026-10-04, security 0000876378: ASXC flipped five times; the
        # 25-NSE (NYSE_AMERICAN) came five months after the last NYSE cover page.
        sid = "0000876378"
        for exchange, start in (
            ("NYSE", date(2022, 5, 4)),
            ("NYSE_AMERICAN", date(2023, 5, 11)),
            ("NYSE", date(2024, 3, 21)),
        ):
            insert_row(synthetic, "listings", _row(sid, "ASXC", exchange, start))
        for day in (date(2024, 3, 20), date(2024, 3, 21), date(2024, 8, 21)):
            insert_row(synthetic, "prices_daily", _bar(sid, day))
        insert_row(
            synthetic,
            "delistings",
            _form_25(sid, "NYSE_AMERICAN", _ny(2024, 8, 22, 7, 17), "Common Stock"),
        )
        df = listing_ends_as_of(synthetic, _at(2024, 9, 30), _settings()).sort("valid_from")
        assert df["status"].to_list() == ["listed", "listed", "delisted"]
        latest = df.row(2, named=True)
        assert (latest["exchange"], latest["end_session"]) == ("NYSE", date(2024, 8, 21))
        assert latest["delisting_form"] == "25-NSE"

    def test_sccb_note_with_no_bars_is_delisted_on_its_retagged_listing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "0001682220:7-125pct-notes-due-2024"
        title = "7.125% Notes due 2024"
        insert_row(
            synthetic, "listings", _row(sid, "SCCB", "NYSE_AMERICAN", date(2022, 5, 27), title)
        )
        insert_row(synthetic, "listings", _row(sid, "SCCB", "NYSE", date(2022, 11, 10), title))
        insert_row(
            synthetic, "delistings", _form_25(sid, "NYSE_AMERICAN", _ny(2024, 7, 1, 10, 27), title)
        )
        df = listing_ends_as_of(synthetic, _at(2024, 9, 30), _settings())
        nyse = _status(df, sid, "NYSE")
        assert (nyse["status"], nyse["end_session"]) == ("delisted", None)
        assert _status(df, sid, "NYSE_AMERICAN")["status"] == "listed"

    def test_aone_units_flip_back_to_nasdaq_is_the_one_ended(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "0001816613:units"
        title = "Units, each consisting of one Class A ordinary share"
        insert_row(synthetic, "listings", _row(sid, "AONE.U", "NASDAQ", date(2021, 3, 29), title))
        insert_row(synthetic, "listings", _row(sid, "AONE.U", "NYSE", date(2021, 5, 14), title))
        insert_row(synthetic, "listings", _row(sid, "AONE.U", "NASDAQ", date(2021, 5, 24), title))
        insert_row(synthetic, "delistings", _form_25(sid, "NYSE", _ny(2021, 7, 15, 4, 40), title))
        df = listing_ends_as_of(synthetic, _at(2021, 9, 30), _settings()).sort("valid_from")
        assert df["status"].to_list() == ["listed", "listed", "delisted"]

    def test_same_ticker_listing_inside_the_window_stays_a_transfer(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Spec req 4: a new listing on another exchange within the window is
        # the transfer's destination, never the listing the filing ends.
        insert_row(synthetic, "listings", _row("S", "A", "NASDAQ", date(2019, 1, 2)))
        insert_row(synthetic, "listings", _row("S", "A", "NYSE", date(2019, 3, 4)))
        insert_row(
            synthetic, "delistings", _form_25("S", "NASDAQ", _at(2019, 3, 6), "Common Stock")
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        assert _status(df, "S", "NASDAQ")["status"] == "transferred"
        assert _status(df, "S", "NYSE")["status"] == "listed"

    def test_a_move_off_exchange_is_not_a_retag(self, synthetic: duckdb.DuckDBPyConnection) -> None:
        # SCON (0000895665): the cover page said NONE before the NASDAQ 25-NSE.
        # NONE is no exchange, so the filing still ends the NASDAQ listing.
        sid = "0000895665"
        insert_row(synthetic, "listings", _row(sid, "SCON", "NASDAQ", date(2019, 8, 13)))
        insert_row(synthetic, "listings", _row(sid, "SCON", "NONE", date(2020, 11, 10)))
        insert_row(synthetic, "prices_daily", _bar(sid, date(2020, 9, 29)))
        insert_row(
            synthetic, "delistings", _form_25(sid, "NASDAQ", _ny(2021, 2, 2, 4, 55), "Common Stock")
        )
        df = listing_ends_as_of(synthetic, _at(2021, 3, 31), _settings())
        nasdaq = _status(df, sid, "NASDAQ")
        assert (nasdaq["status"], nasdaq["end_session"]) == ("delisted", date(2020, 9, 29))
        assert _status(df, sid, "NONE")["status"] == "listed"

    def test_another_ticker_on_another_exchange_is_not_a_retag(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(synthetic, "listings", _row("S", "AAA", "NYSE_AMERICAN", date(2019, 1, 2)))
        insert_row(synthetic, "listings", _row("S", "BBB", "NYSE", date(2019, 2, 1)))
        insert_row(
            synthetic, "delistings", _form_25("S", "NYSE_AMERICAN", _at(2019, 6, 3), "Common Stock")
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        assert _status(df, "S", "NYSE_AMERICAN")["status"] == "delisted"
        assert _status(df, "S", "NYSE")["status"] == "listed"

    def test_dual_listing_still_trading_after_effective_on_is_not_a_retag(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # BKFL shape (test_health): a NASDAQ line seven months before a NYSE
        # Form 25. Bars after `effective_on` show the security still trades,
        # so the filing withdrew the NYSE line, not the NASDAQ one. Before
        # such a bar is known, the conservative re-tag holds.
        insert_row(synthetic, "listings", _row("S", "BKFL", "NYSE", date(2018, 1, 2)))
        insert_row(synthetic, "listings", _row("S", "BKFL", "NASDAQ", date(2018, 6, 1)))
        filed = _at(2019, 1, 10)  # effective_on 2019-01-20
        insert_row(synthetic, "delistings", _form_25("S", "NYSE", filed, "Common Stock"))
        for day in (date(2019, 1, 10), date(2019, 1, 18), date(2019, 1, 22), date(2019, 1, 23)):
            insert_row(synthetic, "prices_daily", _bar("S", day))
        before = listing_ends_as_of(synthetic, session_close(date(2019, 1, 18)), _settings())
        assert _status(before, "S", "NASDAQ")["status"] == "delisted"
        assert _status(before, "S", "NYSE")["status"] == "listed"
        after = listing_ends_as_of(synthetic, session_close(date(2019, 1, 22)), _settings())
        assert _status(after, "S", "NYSE")["status"] == "delisted"
        assert _status(after, "S", "NASDAQ")["status"] == "listed"

    def test_late_amendment_never_ends_the_transfer_destination(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(synthetic, "listings", _row("S", "A", "NASDAQ", date(2019, 1, 2)))
        insert_row(synthetic, "listings", _row("S", "A", "NYSE", date(2019, 3, 4)))
        insert_row(
            synthetic, "delistings", _form_25("S", "NASDAQ", _at(2019, 3, 6), "Common Stock")
        )
        insert_row(
            synthetic,
            "delistings",
            {**_form_25("S", "NASDAQ", _at(2019, 4, 15), "Common Stock"), "form": "25-NSE/A"},
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        nasdaq = _status(df, "S", "NASDAQ")
        assert (nasdaq["status"], nasdaq["delisting_form"]) == ("transferred", "25-NSE")
        assert _status(df, "S", "NYSE")["status"] == "listed"


class TestOtherClassOnSnapshotListing:
    """#818 part 2: a Form 25 for a differently titled class (Class B,
    Special, (Old), T-DECS, any non-plain title) never ends an untitled
    `snapshot_static` listing. Titles and dates are the store's."""

    @pytest.mark.parametrize(
        ("sid", "ticker", "exchange", "valid_from", "filed_at", "title"),
        [
            (
                "0001058090",
                "CMG",
                "NYSE",
                date(2005, 10, 24),
                _ny(2009, 12, 22, 13, 51),
                "Class B Common tock",
            ),
            (
                "0000831001",
                "C",
                "NYSE",
                date(1994, 1, 13),
                _ny(2012, 12, 18, 15, 17),
                "Tangible Dividend Enhanced Common Stock (T-DECS)",
            ),
            (
                "0001051512",
                "TDS",
                "NYSE",
                date(1998, 5, 22),
                _ny(2012, 1, 26, 15, 2),
                "Special Common Shares",
            ),
            (
                "0001166691",
                "CMCSA",
                "NASDAQ",
                date(2002, 10, 30),
                _ny(2015, 12, 11, 16, 23),
                "Class A Special Common Stock",
            ),
            (
                "0001020569",
                "IRM",
                "NYSE",
                date(1996, 11, 13),
                _ny(2015, 1, 23, 10, 47),
                "Common Stock (Old)",
            ),
            (
                "0001051470",
                "CCI",
                "NYSE",
                date(1998, 5, 6),
                _ny(2014, 12, 16, 16, 50),
                "Common Stock (OLD)",
            ),
            (
                "0001350593",
                "MWA",
                "NYSE",
                date(2006, 2, 3),
                _ny(2009, 4, 13, 11, 5),
                "Series B Common Stock",
            ),
            (
                "0001053507",
                "AMT",
                "NYSE",
                date(1998, 3, 20),
                _ny(2012, 1, 4, 11, 33),
                "Class A Common Stock",
            ),
            (
                "0000070858",
                "BAC",
                "NYSE",
                date(1994, 3, 30),
                _ny(2010, 2, 25, 14, 46),
                "Common Equivalent Securities, Consisting of Depositary Shares",
            ),
        ],
    )
    def test_other_class_filing_leaves_snapshot_listing_listed(
        self,
        synthetic: duckdb.DuckDBPyConnection,
        sid: str,
        ticker: str,
        exchange: str,
        valid_from: date,
        filed_at: datetime,
        title: str,
    ) -> None:
        insert_row(synthetic, "listings", _row(sid, ticker, exchange, valid_from, None))
        insert_row(synthetic, "prices_daily", _bar(sid, date(2016, 1, 4)))
        insert_row(synthetic, "delistings", _form_25(sid, exchange, filed_at, title))
        row = _status(listing_ends_as_of(synthetic, _at(2016, 1, 5), _settings()), sid, exchange)
        assert (row["status"], row["end_session"]) == ("listed", None)

    @pytest.mark.parametrize(
        ("title", "filed_at"),
        [
            ("Common Stock", _ny(2006, 11, 20, 16, 2)),  # HCA, taken private
            ("Common Stock of Pentair, Inc.", _ny(2012, 12, 19, 14, 36)),  # PNR
            ("Common stock, par value $0.01 per share (United States)", _ny(2015, 7, 20, 14, 13)),
            ("Ordinary Shares", _ny(2009, 12, 3, 10, 41)),
        ],
    )
    def test_plain_common_filing_still_ends_snapshot_listing(
        self, synthetic: duckdb.DuckDBPyConnection, title: str, filed_at: datetime
    ) -> None:
        insert_row(synthetic, "listings", _row("S", "HCA", "NYSE", date(1994, 2, 10), None))
        insert_row(synthetic, "delistings", _form_25("S", "NYSE", filed_at, title))
        row = _status(listing_ends_as_of(synthetic, _at(2016, 1, 5), _settings()), "S", "NYSE")
        assert row["status"] == "delisted"

    @pytest.mark.parametrize(
        "title",
        [
            "Non-Voting Common Stock",
            "Redeemable Common Stock",
            "Exchangeable Common Shares",
            "Common Stock When Issued",
        ],
    )
    def test_qualified_common_title_leaves_snapshot_listing_listed(
        self, synthetic: duckdb.DuckDBPyConnection, title: str
    ) -> None:
        insert_row(synthetic, "listings", _row("S", "X", "NYSE", date(2010, 1, 4), None))
        insert_row(synthetic, "delistings", _form_25("S", "NYSE", _at(2012, 6, 1), title))
        row = _status(listing_ends_as_of(synthetic, LATE, _settings()), "S", "NYSE")
        assert row["status"] == "listed"

    def test_other_class_filing_still_ends_its_titled_listing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        title = "Class B Common Stock"
        insert_row(synthetic, "listings", _row("S:class-b", "XB", "NYSE", date(2019, 1, 2), title))
        insert_row(synthetic, "delistings", _form_25("S:class-b", "NYSE", _at(2019, 6, 3), title))
        row = _status(listing_ends_as_of(synthetic, LATE, _settings()), "S:class-b", "NYSE")
        assert row["status"] == "delisted"


# ---------------------------------------------------------------- build / write

CIK_DUAL = "0000000007"
CIK_SOLO = "0000000008"
CIK_SNAP = "0000000009"
CIK_WORD = "0000000010"  # cover page says "Common Shares"
CIK_TWO = "0000000011"  # two common classes on one exchange


def _sec(sid: str, cik: str) -> dict[str, Any]:
    return {
        "security_id": sid,
        "cik": cik,
        "name": "X",
        "benchmark": False,
        "known_at": _at(2015, 1, 5),
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _master() -> MasterBuild:
    listings = [
        {
            **_listing(CIK_DUAL, "DUA", "NYSE", date(2015, 1, 6), _at(2015, 1, 5)),
            "class_title": "Class A Common Stock, par value $0.01",
        },
        {
            **_listing(
                f"{CIK_DUAL}:6-preferred-stock", "DUP", "NYSE", date(2016, 1, 5), _at(2016, 1, 4)
            ),
            "class_title": "6% Preferred Stock",
        },
        {
            **_listing(CIK_SOLO, "SOL", "NASDAQ", date(2015, 1, 6), _at(2015, 1, 5)),
            "class_title": "Common Stock",
        },
        {
            **_listing(CIK_SNAP, "SNP", "NYSE", date(2015, 1, 6), _at(2020, 1, 15)),
            "class_title": None,
            "provenance": "snapshot_static",
        },
        {
            **_listing(CIK_WORD, "WRD", "NYSE", date(2015, 1, 6), _at(2015, 1, 5)),
            "class_title": "Common Shares, no par value",
        },
        {
            **_listing(CIK_TWO, "TWA", "NYSE", date(2015, 1, 6), _at(2015, 1, 5)),
            "class_title": "Class A Common Stock",
        },
        {
            **_listing(f"{CIK_TWO}:b", "TWB", "NYSE", date(2015, 1, 6), _at(2015, 1, 5)),
            "class_title": "Class B Common Stock",
        },
    ]
    return MasterBuild(
        securities=(
            _sec(CIK_DUAL, CIK_DUAL),
            _sec(f"{CIK_DUAL}:6-preferred-stock", CIK_DUAL),
            _sec(CIK_SOLO, CIK_SOLO),
            _sec(CIK_SNAP, CIK_SNAP),
            _sec(CIK_WORD, CIK_WORD),
            _sec(CIK_TWO, CIK_TWO),
            _sec(f"{CIK_TWO}:b", CIK_TWO),
        ),
        listings=tuple(listings),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )


def _filing(
    cik: str,
    title: str,
    exchange: str,
    accepted_at: datetime,
    form: str = "25",
    effective_on: date | None = None,
) -> DelistingFiling:
    return DelistingFiling(
        cik=cik,
        form=form,
        class_title=title,
        exchange=exchange,
        accession=f"{cik}-{accepted_at:%Y%m%d}",
        accepted_at=accepted_at,
        effective_on=effective_on,
    )


class TestBuild:
    def test_preferred_filing_resolves_to_preferred_class(self) -> None:
        build = build_delistings(
            [
                _filing(
                    CIK_DUAL,
                    "6% Preferred Stock, $25 liquidation preference",
                    "NYSE",
                    _at(2019, 3, 5),
                )
            ],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert [r["security_id"] for r in build.delistings] == [f"{CIK_DUAL}:6-preferred-stock"]
        assert build.unmatched == ()

    def test_common_filing_resolves_by_title_up_to_comma(self) -> None:
        build = build_delistings(
            [_filing(CIK_DUAL, "Class A Common Stock", "NYSE", _at(2019, 3, 5))],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert [r["security_id"] for r in build.delistings] == [CIK_DUAL]

    def test_unknown_title_on_multi_class_issuer_is_unmatched(self) -> None:
        filing = _filing(CIK_DUAL, "Warrants", "NYSE", _at(2019, 3, 5))
        build = build_delistings([filing], _master(), ingested_at=INGESTED_AT)
        assert build.delistings == ()
        assert build.unmatched == (filing,)

    def test_wrong_exchange_is_unmatched(self) -> None:
        filing = _filing(CIK_SOLO, "Common Stock", "NYSE", _at(2019, 3, 5))
        build = build_delistings([filing], _master(), ingested_at=INGESTED_AT)
        assert build.unmatched == (filing,)

    def test_unknown_cik_is_unmatched(self) -> None:
        filing = _filing("0000000099", "Common Stock", "NYSE", _at(2019, 3, 5))
        assert build_delistings([filing], _master(), ingested_at=INGESTED_AT).unmatched == (filing,)

    def test_warrant_filing_never_ends_an_untitled_common_listing(self) -> None:
        filing = _filing(CIK_SNAP, "Warrants to purchase Common Stock", "NYSE", _at(2018, 3, 5))
        build = build_delistings([filing], _master(), ingested_at=INGESTED_AT)
        assert build.delistings == ()
        assert build.unmatched == (filing,)

    def test_common_filing_finds_single_common_class_despite_wording(self) -> None:
        build = build_delistings(
            [_filing(CIK_WORD, "Common Stock", "NYSE", _at(2019, 3, 5))],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert [r["security_id"] for r in build.delistings] == [CIK_WORD]

    def test_common_filing_with_two_common_classes_is_unmatched(self) -> None:
        filing = _filing(CIK_TWO, "Common Stock", "NYSE", _at(2019, 3, 5))
        assert build_delistings([filing], _master(), ingested_at=INGESTED_AT).unmatched == (filing,)

    def test_resolution_through_later_listing_ends_nothing_before_it_is_known(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # The 2018 filing resolves through a listing known only in 2020: the
        # row keeps its 2018 known_at and ends nothing until the listing is known.
        master = _master()
        filed = _at(2018, 3, 5)
        build = build_delistings(
            [_filing(CIK_SNAP, "Common Shares", "NYSE", filed)], master, ingested_at=INGESTED_AT
        )
        assert build.delistings[0]["known_at"] == filed
        write_delistings(synthetic, build)
        for row in master.listings:
            insert_row(synthetic, "listings", row)
        between = _at(2019, 6, 3)
        assert delistings_as_of(synthetic, between)["security_id"].to_list() == [CIK_SNAP]
        early = listing_ends_as_of(synthetic, between, _settings(), security_ids=[CIK_SNAP])
        assert early.is_empty()
        later = listing_ends_as_of(synthetic, LATE, _settings(), security_ids=[CIK_SNAP])
        assert later["status"].to_list() == ["delisted"]

    def test_untitled_listing_of_single_class_issuer_matches(self) -> None:
        build = build_delistings(
            [_filing(CIK_SNAP, "Common Shares", "NYSE", _at(2018, 3, 5))],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert [r["security_id"] for r in build.delistings] == [CIK_SNAP]

    def test_row_columns_and_default_effective_on(self) -> None:
        # 2019-03-06 01:30 UTC is 2019-03-05 in New York: effective 10 days after that.
        accepted = datetime(2019, 3, 6, 1, 30, tzinfo=UTC)
        build = build_delistings(
            [_filing(CIK_SOLO, "Common Stock", "NASDAQ", accepted, form="25-NSE")],
            _master(),
            ingested_at=INGESTED_AT,
        )
        (row,) = build.delistings
        assert row == {
            "security_id": CIK_SOLO,
            "form": "25-NSE",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": accepted,
            "effective_on": date(2019, 3, 15),
            "known_at": accepted,
            "ingested_at": INGESTED_AT,
            "source": "edgar",
            "provenance": "filing",
        }

    def test_stated_effective_on_kept(self) -> None:
        build = build_delistings(
            [
                _filing(
                    CIK_SOLO,
                    "Common Stock",
                    "NASDAQ",
                    _at(2019, 3, 5),
                    effective_on=date(2019, 3, 20),
                )
            ],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert build.delistings[0]["effective_on"] == date(2019, 3, 20)

    @pytest.mark.parametrize("form", ["25/A", "25-NSE/A"])
    def test_amendments_are_delisting_forms(self, form: str) -> None:
        """EDGAR's full history has amended Form 25s (owner decision
        2026-09-26, #262): the builder accepts them instead of failing the
        whole EDGAR chunk on the first one."""
        build = build_delistings(
            [_filing(CIK_SOLO, "Common Stock", "NASDAQ", _at(2019, 3, 5), form=form)],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert [r["security_id"] for r in build.delistings] == [CIK_SOLO]

    def test_an_amendment_never_moves_the_listing_end_later_or_earlier(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        """The earliest filing ends the listing: a 25/A filed after its
        original changes nothing."""
        master = _master()
        original = _filing(CIK_SOLO, "Common Stock", "NASDAQ", _at(2019, 3, 5))
        amended = DelistingFiling(
            cik=CIK_SOLO,
            form="25/A",
            class_title="Common Stock",
            exchange="NASDAQ",
            accession=f"{CIK_SOLO}-amended",
            accepted_at=_at(2019, 4, 1),
            effective_on=None,
        )
        for row in master.listings:
            insert_row(synthetic, "listings", row)
        alone = build_delistings([original], master, ingested_at=INGESTED_AT)
        write_delistings(synthetic, alone)
        before = listing_ends_as_of(synthetic, LATE, _settings(), security_ids=[CIK_SOLO])
        # A later run ingests the amendment on its own.
        write_delistings(synthetic, build_delistings([amended], master, ingested_at=INGESTED_AT))
        after = listing_ends_as_of(synthetic, LATE, _settings(), security_ids=[CIK_SOLO])
        assert after.to_dicts() == before.to_dicts()

    def test_other_forms_rejected(self) -> None:
        with pytest.raises(ValueError, match="10-K"):
            build_delistings(
                [_filing(CIK_SOLO, "Common Stock", "NASDAQ", _at(2019, 3, 5), form="10-K")],
                _master(),
                ingested_at=INGESTED_AT,
            )

    def test_known_after_ingest_rejected(self) -> None:
        with pytest.raises(ValueError, match="ingested_at"):
            build_delistings(
                [_filing(CIK_SOLO, "Common Stock", "NASDAQ", _at(2027, 3, 5))],
                _master(),
                ingested_at=INGESTED_AT,
            )

    def test_write_then_read(self, synthetic: duckdb.DuckDBPyConnection) -> None:
        build = build_delistings(
            [_filing(CIK_SOLO, "Common Stock", "NASDAQ", _at(2019, 3, 5))],
            _master(),
            ingested_at=INGESTED_AT,
        )
        assert isinstance(build, DelistingBuild)
        assert write_delistings(synthetic, build) == 1
        assert delistings_as_of(synthetic, _at(2019, 3, 5) - PROBE_EPSILON).is_empty()
        read = delistings_as_of(synthetic, _at(2019, 3, 5))
        assert read["security_id"].to_list() == [CIK_SOLO]


class TestCompoundTitles:
    """#1163 (C): a Form 25 naming several classes in one title ("Common
    stock and warrants", a SPAC's "Class A Common Stock; Units, each
    consisting of ...") resolves through the common classes it names; a
    clause describing another class ("each consisting of one share of
    ...") never names one, and nothing is guessed."""

    def _ids(self, title: str, cik: str, exchange: str = "NYSE") -> list[str]:
        build = build_delistings(
            [_filing(cik, title, exchange, _at(2019, 3, 5))], _master(), ingested_at=INGESTED_AT
        )
        return [r["security_id"] for r in build.delistings]

    @pytest.mark.parametrize(
        "title",
        [
            "Common stock and warrants",
            "Common Stock & Warrant",
            "Units and Common Stock",
            "Units, Common Stock, and Warrants",
            "Common Stock; Rights, each exchangeable into one-twentieth of a share of Common Stock",
        ],
    )
    def test_common_and_other_classes_resolve_to_the_single_common_class(self, title: str) -> None:
        assert self._ids(title, CIK_SOLO, "NASDAQ") == [CIK_SOLO]

    def test_semicolon_spac_title_resolves_by_its_common_title(self) -> None:
        title = (
            "Class A Common Stock; Units, each consisting of one share of Class A common "
            "stock and one-half of one redeemable warrant"
        )
        assert self._ids(title, CIK_DUAL) == [CIK_DUAL]

    def test_two_named_common_classes_each_get_a_row(self) -> None:
        title = "Class A Common Stock and Class B Common Stock"
        build = build_delistings(
            [_filing(CIK_TWO, title, "NYSE", _at(2019, 3, 5))], _master(), ingested_at=INGESTED_AT
        )
        assert sorted(r["security_id"] for r in build.delistings) == [CIK_TWO, f"{CIK_TWO}:b"]
        assert {r["class_title"] for r in build.delistings} == {title}
        assert build.unmatched == ()

    @pytest.mark.parametrize(
        "title",
        [
            "Units, each consisting of one share of Common Stock and one Warrant",
            "Warrants, exercisable for one-half of one share of Common Stock",
            "Rights to purchase Common Stock and Units",
        ],
    )
    def test_a_described_common_share_names_no_class(self, title: str) -> None:
        assert self._ids(title, CIK_SOLO, "NASDAQ") == []

    def test_one_common_part_never_guesses_between_two_common_classes(self) -> None:
        assert self._ids("Common Stock and Warrants", CIK_TWO) == []

    def test_a_named_class_that_matches_nothing_never_guesses(self) -> None:
        # Class C is not listed: the filing is unmatched, not read as A alone.
        assert self._ids("Class A Common Stock and Class C Common Stock", CIK_TWO) == []

    def test_compound_row_is_known_only_from_the_acceptance(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        master = _master()
        filed = _at(2019, 3, 5)
        build = build_delistings(
            [_filing(CIK_SOLO, "Common stock and warrants", "NASDAQ", filed)],
            master,
            ingested_at=INGESTED_AT,
        )
        assert build.delistings[0]["known_at"] == filed
        write_delistings(synthetic, build)
        for row in master.listings:
            insert_row(synthetic, "listings", row)
        ids = [CIK_SOLO]
        before = listing_ends_as_of(synthetic, filed - PROBE_EPSILON, _settings(), ids)
        assert before["status"].to_list() == ["listed"]
        after = listing_ends_as_of(synthetic, filed, _settings(), ids)
        assert after["status"].to_list() == ["delisted"]
        truncated = TruncatedStore(synthetic, tables=("listings", "delistings", "prices_daily"))
        try:
            for t in probe_timestamps(synthetic, ("listings", "delistings")):
                full = listing_ends_as_of(synthetic, t, _settings())
                assert full.equals(listing_ends_as_of(truncated.at(t), t, _settings())), t
        finally:
            truncated.close()


class TestFormTwentyFiveCloseOverRelisting:
    """#1163 (A): a later cover page re-opens a listing a Form 25 ended
    (Maxim, Rudolph, Nielsen: an acquired company's last 10-K still names
    the pair). Unless a bar known at T falls after the Form 25's effective
    day, that filing closes the re-opened row too; a real relisting (#820:
    CMPR, CG, WELL keep trading) stays listed."""

    FILED = _at(2021, 8, 26)  # effective 2021-09-05

    def _store(self, conn: duckdb.DuckDBPyConnection, *bars: date) -> None:
        insert_row(
            conn, "listings", _listing("S", "MX", "NASDAQ", date(2019, 10, 30), _at(2019, 10, 30))
        )
        insert_row(conn, "delistings", _delisting("S", "NASDAQ", self.FILED))
        insert_row(
            conn, "listings", _listing("S", "MX", "NASDAQ", date(2021, 9, 7), _at(2021, 9, 7))
        )
        for day in (date(2021, 8, 24), date(2021, 8, 25), *bars):
            insert_row(conn, "prices_daily", _bar("S", day))

    def test_reopened_row_without_a_later_bar_is_closed_by_the_form_25(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        self._store(synthetic)
        df = listing_ends_as_of(synthetic, _at(2023, 10, 31), _settings()).sort("valid_from")
        assert df["status"].to_list() == ["delisted", "delisted"]
        reopened = df.row(1, named=True)
        assert reopened["delisting_filed_at"] == self.FILED
        assert reopened["effective_on"] == date(2021, 9, 5)
        assert reopened["end_session"] is None
        assert df.row(0, named=True)["end_session"] == date(2021, 8, 25)

    def test_a_bar_after_the_effective_day_keeps_the_relisting(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        self._store(synthetic, date(2021, 9, 8))
        df = listing_ends_as_of(synthetic, _at(2023, 10, 31), _settings()).sort("valid_from")
        assert df["status"].to_list() == ["delisted", "listed"]

    def test_the_relisting_reads_live_from_its_first_later_bar_known(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Between the cover page and the first bar after the effective day
        # the conservative read holds, as for the #818 re-tag.
        self._store(synthetic, date(2021, 9, 8))
        bar_known = session_close(date(2021, 9, 8))
        early = listing_ends_as_of(synthetic, bar_known - PROBE_EPSILON, _settings())
        late = listing_ends_as_of(synthetic, bar_known, _settings())
        assert early.sort("valid_from")["status"].to_list() == ["delisted", "delisted"]
        assert late.sort("valid_from")["status"].to_list() == ["delisted", "listed"]

    def test_a_form_25_that_ended_nothing_closes_nothing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        # Maxim's 2007 25-NSE predates every listing row: it ends none, so
        # it never closes the 2019 row either.
        insert_row(
            synthetic,
            "listings",
            _listing("S", "MX", "NASDAQ", date(2019, 10, 30), _at(2019, 10, 30)),
        )
        insert_row(synthetic, "delistings", _delisting("S", "NASDAQ", _at(2007, 10, 17)))
        df = listing_ends_as_of(synthetic, _at(2019, 12, 2), _settings())
        assert df["status"].to_list() == ["listed"]

    def test_a_form_25_on_another_exchange_closes_nothing(
        self, synthetic: duckdb.DuckDBPyConnection
    ) -> None:
        insert_row(
            synthetic, "listings", _listing("S", "A", "NYSE", date(2019, 1, 2), _at(2018, 12, 3))
        )
        insert_row(synthetic, "delistings", _delisting("S", "NYSE", _at(2019, 3, 5)))
        insert_row(
            synthetic, "listings", _listing("S", "A", "NASDAQ", date(2019, 5, 1), _at(2019, 4, 25))
        )
        df = listing_ends_as_of(synthetic, LATE, _settings())
        assert _status(df, "S", "NASDAQ")["status"] == "listed"

    def test_no_look_ahead(self, synthetic: duckdb.DuckDBPyConnection) -> None:
        self._store(synthetic, date(2021, 9, 8), date(2021, 9, 9))
        truncated = TruncatedStore(synthetic, tables=("listings", "delistings", "prices_daily"))
        probes = set(probe_timestamps(synthetic, ("listings", "delistings", "prices_daily")))
        settings = _settings()
        try:
            for t in sorted(probes):
                full = listing_ends_as_of(synthetic, t, settings)
                assert full.equals(listing_ends_as_of(truncated.at(t), t, settings)), f"T={t!r}"
        finally:
            truncated.close()


# ------------------------------------------------------------------ look-ahead


class TestNoLookAhead:
    def test_listing_ends_invariant_on_fixture_store(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        tables = ("listings", "delistings", "prices_daily")
        truncated = TruncatedStore(fixture_store, tables=tables)
        # Probes around every listing and delisting known_at, plus every
        # session close in the week or so around each filing (bars decide ends).
        probes = set(probe_timestamps(fixture_store, ("listings", "delistings")))
        # Every bar known_at of a delisted name within 60 days of its filing.
        bar_known_ats = fixture_store.execute(
            """
            SELECT DISTINCT p.known_at FROM prices_daily p JOIN delistings d USING (security_id)
            WHERE p.session BETWEEN CAST(d.filed_at AS DATE) - 60 AND CAST(d.filed_at AS DATE) + 60
            """
        ).fetchall()
        for (known_at,) in bar_known_ats:
            probes.update({known_at - PROBE_EPSILON, known_at})
        for (filed,) in fixture_store.execute("SELECT filed_at FROM delistings").fetchall():
            day = filed.date()
            for offset in range(-8, 9):
                d = day + timedelta(days=offset)
                try:
                    close = session_close(d)
                except ValueError:
                    continue
                probes.update({close - PROBE_EPSILON, close})
        settings = _settings()
        try:
            for t in sorted(probes):
                full = listing_ends_as_of(fixture_store, t, settings)
                assert full.equals(listing_ends_as_of(truncated.at(t), t, settings)), f"T={t!r}"
        finally:
            truncated.close()

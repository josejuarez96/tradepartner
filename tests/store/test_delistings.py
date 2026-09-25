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


# ---------------------------------------------------------------- build / write

CIK_DUAL = "0000000007"
CIK_SOLO = "0000000008"
CIK_SNAP = "0000000009"


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
    ]
    return MasterBuild(
        securities=(
            _sec(CIK_DUAL, CIK_DUAL),
            _sec(f"{CIK_DUAL}:6-preferred-stock", CIK_DUAL),
            _sec(CIK_SOLO, CIK_SOLO),
            _sec(CIK_SNAP, CIK_SNAP),
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

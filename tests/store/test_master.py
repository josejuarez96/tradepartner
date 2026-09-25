"""Tests for tradepartner.store.master (T8).

Plan T8 tests: earliest-filing rule; issuer-forms filter excludes a Form
4-only filer; static reliance; reused ticker; same-company ticker change;
dual-class listings; benchmarks seeded. Plus the look-ahead checks the
master needs: `securities_as_of` is invariant under truncation of the
fixture store, and a store **built** from a source truncated to what was
knowable at T agrees with one built from the full source at every T.

Scenarios use `FixtureFilingSource` with synthetic records, so each test
states exactly which filings exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pytest
from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection
from tradepartner.store.master import (
    MasterBuild,
    build_master,
    primary_security_id,
    securities_as_of,
    write_master,
)

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FETCHED_AT = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)

ACME = "0000000001"  # 10-K filer from 2005; a Form 4 names it as subject earlier
PERSON = "0000000002"  # reporting owner: Form 4 only
OLDCO = "0000000003"  # filed 2003-2004, delisted 2006, never had a cover page
TICK = "0000000004"  # ticker change TCKA -> TCKB on cover pages
REUSE_1 = "0000000005"  # REUSE until 2020
REUSE_2 = "0000000006"  # REUSE again from 2022, a different company
DUAL = "0000000007"  # Class A + Class C, preferred added later
PRE = "0000000008"  # pre-2019 filer, no cover pages, in the snapshot
STEADY = "0000000009"  # 10-K from 2012, cover pages from 2019, same ticker
SPY_TRUST = "0000884394"
ISHARES = "0001100663"


def _at(year: int, month: int, day: int, hour: int = 20, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


_counter = iter(range(1, 1_000_000))


def _filing(cik: str, name: str, form: str, accepted_at: datetime) -> FilingIndexEntry:
    return FilingIndexEntry(cik, name, form, f"{cik}-{next(_counter):06d}", accepted_at)


def _cover(cik: str, accepted_at: datetime, *listings: tuple[str, str, str]) -> CoverPage:
    return CoverPage(
        cik,
        f"{cik}-{next(_counter):06d}",
        accepted_at,
        tuple(CoverListing(title, ticker, exchange) for title, ticker, exchange in listings),
    )


def _snap(cik: str, name: str, ticker: str, exchange: str) -> CompanySnapshotEntry:
    return CompanySnapshotEntry(cik, name, ticker, exchange, FETCHED_AT)


def _source() -> FixtureFilingSource:
    return FixtureFilingSource(
        index=[
            _filing(ACME, "Acme Corp", "4", _at(2004, 6, 1)),
            _filing(ACME, "Acme Corp", "10-K", _at(2005, 3, 1)),
            _filing(ACME, "Acme Corp", "10-Q", _at(2005, 5, 2)),
            _filing(PERSON, "Doe John", "4", _at(2006, 1, 5)),
            _filing(PERSON, "Doe John", "4", _at(2007, 1, 5)),
            _filing(OLDCO, "Old Co", "10-K", _at(2003, 3, 3)),
            _filing(OLDCO, "Old Co", "10-K", _at(2004, 3, 3)),
            _filing(OLDCO, "Old Co", "25", _at(2006, 2, 1)),
            _filing(TICK, "Tick Co", "10-K", _at(2018, 3, 1)),
            _filing(REUSE_1, "First Reuse Inc", "10-K", _at(2018, 3, 5)),
            _filing(REUSE_2, "Second Reuse Inc", "S-1", _at(2021, 11, 1)),
            _filing(DUAL, "Dual Holdings", "10-K", _at(2015, 2, 2)),
            _filing(PRE, "Pre Static Inc", "10-K", _at(2010, 3, 1)),
            _filing(STEADY, "Steady Inc", "10-K", _at(2012, 3, 1)),
        ],
        cover_pages=[
            _cover(TICK, _at(2019, 3, 1), ("Common Stock, par value $0.01", "TCKA", "NYSE")),
            _cover(TICK, _at(2019, 5, 1), ("Common Stock, par value $0.01", "TCKA", "NYSE")),
            _cover(TICK, _at(2020, 3, 2), ("Common Stock, $0.01 par value", "TCKB", "NYSE")),
            _cover(REUSE_1, _at(2019, 3, 5), ("Common Stock", "REUSE", "NYSE")),
            _cover(REUSE_2, _at(2022, 3, 1), ("Common Stock", "REUSE", "NASDAQ")),
            _cover(
                DUAL,
                _at(2019, 2, 4),
                ("Class A Common Stock, $0.001 par value", "DUA", "NASDAQ"),
                ("Class C Capital Stock, $0.001 par value", "DUC", "NASDAQ"),
            ),
            _cover(
                DUAL,
                _at(2020, 2, 3),
                ("Class A Common Stock, $0.001 par value", "DUA", "NASDAQ"),
                ("Class C Capital Stock, $0.001 par value", "DUC", "NASDAQ"),
                ("6% Preferred Stock", "DUP", "NYSE"),
            ),
            _cover(STEADY, _at(2019, 3, 4), ("Common Stock", "STDY", "NYSE")),
        ],
        snapshot=[
            _snap(ACME, "ACME CORP", "ACME", "NYSE"),
            _snap(TICK, "TICK CO", "TCKB", "NYSE"),
            _snap(DUAL, "DUAL HOLDINGS", "DUA", "NASDAQ"),
            _snap(PRE, "PRE STATIC INC", "PRE", "NYSE"),
            _snap(STEADY, "STEADY INC", "STDY", "NYSE"),
            _snap(SPY_TRUST, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE"),
            _snap(ISHARES, "iShares Trust", "MTUM", "NYSE"),
        ],
    )


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _new_store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


@pytest.fixture
def built() -> MasterBuild:
    return build_master(_source(), _settings(), ingested_at=INGESTED_AT)


@pytest.fixture
def store(built: MasterBuild) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = _new_store()
    write_master(conn, built)
    try:
        yield conn
    finally:
        conn.close()


def _ids(df: pl.DataFrame) -> set[str]:
    return set(df["security_id"].to_list())


def _listings(store: duckdb.DuckDBPyConnection, security_id: str) -> list[dict[str, object]]:
    df = listings_as_of(store, INGESTED_AT, security_ids=[security_id]).sort("valid_from")
    return df.select("ticker", "exchange", "class_title", "valid_from", "provenance").to_dicts()


class TestEarliestFilingRule:
    def test_row_known_at_first_issuer_filing(self, store: duckdb.DuckDBPyConnection) -> None:
        row = securities_as_of(store, INGESTED_AT, [ACME]).row(0, named=True)
        assert row["known_at"] == _at(2005, 3, 1)
        assert row["name"] == "Acme Corp"
        assert row["provenance"] == "filing"
        assert row["benchmark"] is False

    def test_absent_before_first_issuer_filing(self, store: duckdb.DuckDBPyConnection) -> None:
        before = _at(2005, 3, 1) - PROBE_EPSILON
        assert ACME not in _ids(securities_as_of(store, before))
        assert ACME in _ids(securities_as_of(store, _at(2005, 3, 1)))

    def test_delisted_historical_company_exists(self, store: duckdb.DuckDBPyConnection) -> None:
        row = securities_as_of(store, INGESTED_AT, [OLDCO]).row(0, named=True)
        assert row["known_at"] == _at(2003, 3, 3)
        assert _ids(securities_as_of(store, _at(2003, 3, 3) - PROBE_EPSILON)) == set()

    def test_index_scanned_over_full_history(self) -> None:
        calls: list[datetime | None] = []

        class Spy(FixtureFilingSource):
            def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
                calls.append(since)
                return super().filing_index(since)

        source = _source()
        spy = Spy(index=source.filing_index())
        build_master(spy, _settings(), ingested_at=INGESTED_AT)
        assert calls == [None]


class TestIssuerFormsFilter:
    def test_form_4_only_filer_has_no_row(self, store: duckdb.DuckDBPyConnection) -> None:
        assert PERSON not in _ids(securities_as_of(store, INGESTED_AT))

    def test_forms_come_from_config(self) -> None:
        settings = _settings(master={"issuer_forms": ["4"]})
        build = build_master(_source(), settings, ingested_at=INGESTED_AT)
        ids = {row["security_id"] for row in build.securities}
        assert PERSON in ids
        acme = next(row for row in build.securities if row["security_id"] == ACME)
        assert acme["known_at"] == _at(2004, 6, 1)


class TestTickers:
    def test_same_company_ticker_change(self, store: duckdb.DuckDBPyConnection) -> None:
        tick = primary_security_id(TICK)
        rows = _listings(store, tick)
        assert [(r["ticker"], r["provenance"]) for r in rows] == [
            ("TCKA", "filing"),
            ("TCKB", "filing"),
        ]
        assert rows[0]["valid_from"] == date(2019, 3, 1)
        assert rows[1]["valid_from"] == date(2020, 3, 2)
        assert _ids(securities_as_of(store, INGESTED_AT, [tick])) == {tick}

    def test_repeated_cover_page_adds_no_row(self, store: duckdb.DuckDBPyConnection) -> None:
        assert len(_listings(store, primary_security_id(TICK))) == 2

    def test_reused_ticker_is_two_securities(self, store: duckdb.DuckDBPyConnection) -> None:
        reuse = listings_as_of(store, INGESTED_AT).filter(pl.col("ticker") == "REUSE")
        assert _ids(reuse) == {primary_security_id(REUSE_1), primary_security_id(REUSE_2)}

    def test_listing_known_at_is_cover_acceptance(self, store: duckdb.DuckDBPyConnection) -> None:
        tcka = listings_as_of(store, _at(2019, 3, 1), [primary_security_id(TICK)])
        assert tcka["known_at"].to_list() == [_at(2019, 3, 1)]
        before = _at(2019, 3, 1) - PROBE_EPSILON
        assert listings_as_of(store, before, [primary_security_id(TICK)]).height == 0


class TestDualClass:
    def test_each_class_is_a_security_sharing_the_cik(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = securities_as_of(store, INGESTED_AT).filter(pl.col("cik") == DUAL)
        assert rows.height == 3
        assert set(rows["name"].to_list()) == {"Dual Holdings"}
        assert primary_security_id(DUAL) in _ids(rows)

    def test_first_listed_class_is_the_primary(self, store: duckdb.DuckDBPyConnection) -> None:
        primary = _listings(store, primary_security_id(DUAL))
        assert [(r["ticker"], r["provenance"]) for r in primary] == [
            ("DUA", "snapshot_static"),
            ("DUA", "filing"),
        ]
        assert primary[1]["class_title"] == "Class A Common Stock, $0.001 par value"

    def test_later_class_known_from_its_cover_page(self, store: duckdb.DuckDBPyConnection) -> None:
        dup = listings_as_of(store, INGESTED_AT).filter(pl.col("ticker") == "DUP")
        assert dup.height == 1
        dup_id = dup["security_id"][0]
        assert dup["class_title"][0] == "6% Preferred Stock"
        known = securities_as_of(store, INGESTED_AT, [dup_id])["known_at"][0]
        assert known == _at(2020, 2, 3)
        assert securities_as_of(store, _at(2020, 2, 3) - PROBE_EPSILON, [dup_id]).height == 0

    def test_class_ids_are_distinct(self, store: duckdb.DuckDBPyConnection) -> None:
        tickers = listings_as_of(store, INGESTED_AT).filter(pl.col("ticker").is_in(["DUA", "DUC"]))
        assert tickers["security_id"].n_unique() == 2


class TestStaticReliance:
    def test_snapshot_only_listing_is_static_from_first_filing(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        (row,) = _listings(store, primary_security_id(PRE))
        assert row["provenance"] == "snapshot_static"
        assert row["valid_from"] == date(2010, 3, 1)
        known = listings_as_of(store, INGESTED_AT, [PRE])["known_at"][0]
        assert known == FETCHED_AT

    def test_not_static_when_config_disallows(self) -> None:
        settings = _settings(master={"static_columns": ["name"]})
        conn = _new_store()
        write_master(conn, build_master(_source(), settings, ingested_at=INGESTED_AT))
        (row,) = _listings(conn, primary_security_id(PRE))
        assert row["provenance"] == "snapshot"
        assert row["valid_from"] == date(2026, 9, 24)
        # A class already listed on cover pages gets no snapshot row at all.
        assert [r["provenance"] for r in _listings(conn, STEADY)] == ["filing"]

    def test_static_fills_the_gap_before_the_first_cover_page(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = _listings(store, primary_security_id(STEADY))
        assert [(r["ticker"], r["provenance"], r["valid_from"]) for r in rows] == [
            ("STDY", "snapshot_static", date(2012, 3, 1)),
            ("STDY", "filing", date(2019, 3, 4)),
        ]

    def test_ticker_changed_since_first_cover_is_not_guessed(self, built: MasterBuild) -> None:
        # TCKB is the current ticker, but the first cover page says TCKA, so
        # the pre-2019 ticker is unknown: reported, not written.
        assert [e.ticker for e in built.unmatched_snapshot] == ["TCKB"]
        tick_static = [
            row
            for row in built.listings
            if row["security_id"] == TICK and row["provenance"] != "filing"
        ]
        assert tick_static == []

    def test_strict_known_at_hides_static_row_before_fetch(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # Issue #35 is open; T8 follows the rule merged in T6 (#69): no row
        # is visible before its own known_at, snapshot_static included.
        assert listings_as_of(store, FETCHED_AT - PROBE_EPSILON, [PRE]).height == 0


class TestBenchmarks:
    def test_seeded_from_config(self, store: duckdb.DuckDBPyConnection) -> None:
        rows = securities_as_of(store, INGESTED_AT).filter(pl.col("benchmark"))
        assert sorted(rows["security_id"].to_list()) == ["BENCH:MTUM", "BENCH:SPY"]
        assert set(rows["source"].to_list()) == {"config"}
        spy = rows.filter(pl.col("security_id") == "BENCH:SPY").row(0, named=True)
        assert spy["cik"] == SPY_TRUST
        assert spy["known_at"] == FETCHED_AT

    def test_benchmark_listing(self, store: duckdb.DuckDBPyConnection) -> None:
        (row,) = _listings(store, "BENCH:MTUM")
        assert row["ticker"] == "MTUM"
        assert row["provenance"] == "snapshot_static"

    def test_missing_benchmark_is_reported(self) -> None:
        settings = _settings(benchmarks=["SPY", "NOPE"])
        build = build_master(_source(), settings, ingested_at=INGESTED_AT)
        assert build.missing_benchmarks == ("NOPE",)

    def test_benchmark_trust_is_not_an_issuer_security(self, built: MasterBuild) -> None:
        ids = {row["security_id"] for row in built.securities}
        assert SPY_TRUST not in ids
        assert ISHARES not in ids


class TestWriteAndRead:
    def test_known_at_after_ingested_at_raises(self) -> None:
        with pytest.raises(ValueError, match="ingested_at"):
            build_master(_source(), _settings(), ingested_at=FETCHED_AT - timedelta(days=1))

    def test_write_returns_row_count(self, built: MasterBuild) -> None:
        conn = _new_store()
        assert write_master(conn, built) == len(built.securities) + len(built.listings)

    def test_bare_date_raises(self, store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            securities_as_of(store, date(2020, 1, 1))  # type: ignore[arg-type]

    def test_build_is_deterministic(self) -> None:
        first = build_master(_source(), _settings(), ingested_at=INGESTED_AT)
        second = build_master(_source(), _settings(), ingested_at=INGESTED_AT)
        assert first == second


class TestNoLookAhead:
    def test_securities_as_of_invariant_on_fixture_store(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Only `securities` is truncated and probed: it is the one table
        # `securities_as_of` reads, and the others are absent from the
        # truncated store, so reading one would fail loudly.
        truncated = TruncatedStore(fixture_store, tables=("securities",))
        try:
            for t in probe_timestamps(fixture_store, ("securities",)):
                full = securities_as_of(fixture_store, t)
                assert full.equals(securities_as_of(truncated.at(t), t)), f"T={t!r}"
        finally:
            truncated.close()

    def test_store_built_from_source_known_at_t_agrees(self) -> None:
        source = _source()
        settings = _settings()
        full = _new_store()
        write_master(full, build_master(source, settings, ingested_at=INGESTED_AT))
        probes = sorted(
            {k + d for k in source.known_ats() for d in (-PROBE_EPSILON, PROBE_EPSILON)}
        )
        for t in probes:
            partial = _new_store()
            partial_build = build_master(source.known_by(t), settings, ingested_at=INGESTED_AT)
            write_master(partial, partial_build)
            for read in (securities_as_of, listings_as_of):
                assert read(full, t).equals(read(partial, t)), f"{read.__name__} T={t!r}"
            partial.close()

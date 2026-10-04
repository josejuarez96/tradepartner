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

from tradepartner.adapters.alpaca_prices import ListingResolver, registrant_evidence
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection
from tradepartner.store.delistings import DELISTED, LISTED, build_delistings, derive_listing_ends
from tradepartner.store.master import (
    MasterBuild,
    Succession,
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
SWAP = "0000000010"  # two classes swap tickers between cover pages
PREF = "0000000011"  # earliest cover page lists the preferred before the common
MULTI = "0000000012"  # one class listed on two exchanges
EARLY = "0000000013"  # snapshot fetched before a cover page that changes the ticker
BANK = "0000000014"  # two depositary series with titles equal up to the comma
REORG = "0000000015"  # Form 25 on a redomicile, then the same pair on a later cover page
EARLY_FETCH = datetime(2018, 6, 1, 14, 0, tzinfo=UTC)
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


def _form25(
    cik: str, title: str, exchange: str, accepted_at: datetime, form: str = "25-NSE"
) -> DelistingFiling:
    return DelistingFiling(cik, form, title, exchange, f"{cik}-{next(_counter):06d}", accepted_at)


def _snap(
    cik: str, name: str, ticker: str, exchange: str, fetched_at: datetime = FETCHED_AT
) -> CompanySnapshotEntry:
    return CompanySnapshotEntry(cik, name, ticker, exchange, fetched_at)


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
            _filing(SWAP, "Swap Corp", "10-K", _at(2018, 2, 1)),
            _filing(PREF, "Pref Corp", "10-K", _at(2018, 2, 2)),
            _filing(MULTI, "Multi Corp", "10-K", _at(2018, 2, 5)),
            _filing(EARLY, "Early Corp", "10-K", _at(2017, 3, 1)),
            _filing(BANK, "Bank Corp", "10-K", _at(2018, 2, 6)),
            _filing(REORG, "Reorg Ltd", "10-K", _at(2018, 2, 7)),
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
            _cover(
                SWAP,
                _at(2019, 2, 1),
                ("Class A Common Stock", "ZZ", "NYSE"),
                ("Class B Common Stock", "ZB", "NYSE"),
            ),
            _cover(
                SWAP,
                _at(2022, 2, 1),
                ("Class A Common Stock", "ZA", "NYSE"),
                ("Class B Common Stock", "ZZ", "NYSE"),
            ),
            _cover(
                PREF,
                _at(2019, 2, 4),
                ("Depositary Shares, each 1/1000th of a Series A Preferred", "PFA", "NYSE"),
                ("Common Stock", "PFC", "NYSE"),
            ),
            _cover(
                MULTI,
                _at(2019, 2, 5),
                ("Common Stock", "MLT", "NASDAQ"),
                ("Common Stock", "MLT", "NYSE"),
            ),
            _cover(EARLY, _at(2019, 3, 1), ("Common Stock", "ERLB", "NYSE")),
            _cover(
                BANK,
                _at(2019, 2, 6),
                ("Common Stock", "BK", "NYSE"),
                ("Depositary Shares, each 1/1000th of a Series A Preferred", "BK-PA", "NYSE"),
                ("Depositary Shares, each 1/1000th of a Series B Preferred", "BK-PB", "NYSE"),
            ),
            _cover(
                BANK,
                _at(2020, 2, 6),
                ("Common Stock", "BK", "NYSE"),
                ("Depositary Shares, each 1/1000th of a Series B Preferred", "BK-PB", "NYSE"),
                ("Depositary Shares, each 1/1000th of a Series A Preferred", "BK-PA", "NYSE"),
            ),
            _cover(REORG, _at(2019, 8, 9), ("Ordinary Shares, par value 0.01", "RORG", "NASDAQ")),
            _cover(
                REORG, _at(2019, 12, 16), ("Ordinary Shares, nominal value 0.01", "RORG", "NASDAQ")
            ),
        ],
        delistings=[_form25(REORG, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3))],
        snapshot=[
            _snap(ACME, "ACME CORP", "ACME", "NYSE"),
            _snap(TICK, "TICK CO", "TCKB", "NYSE"),
            _snap(DUAL, "DUAL HOLDINGS", "DUA", "NASDAQ"),
            _snap(PRE, "PRE STATIC INC", "PRE", "NYSE"),
            _snap(STEADY, "STEADY INC", "STDY", "NYSE"),
            _snap(EARLY, "EARLY CORP", "ERLA", "NYSE", EARLY_FETCH),
            _snap(EARLY, "EARLY CORP", "ERLB", "NYSE"),
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

    def test_swapped_tickers_stay_with_their_classes(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # Auditor SHOULD FIX 1: ZZ moves from Class A to Class B. Matching by
        # ticker first would hand B's ZZ to A and splice two price series.
        class_a = _listings(store, primary_security_id(SWAP))
        assert [r["ticker"] for r in class_a] == ["ZZ", "ZA"]
        zz_2022 = listings_as_of(store, INGESTED_AT).filter(
            (pl.col("ticker") == "ZZ") & (pl.col("valid_from") == date(2022, 2, 1))
        )
        (class_b,) = zz_2022["security_id"].to_list()
        assert class_b != primary_security_id(SWAP)
        assert [r["ticker"] for r in _listings(store, class_b)] == ["ZB", "ZZ"]

    def test_reordered_same_title_series_keep_their_ids(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # Audit round 2: both series normalize to "depositary shares"; a
        # reordered later cover page must not swap their security_ids.
        rows = listings_as_of(store, INGESTED_AT).filter(pl.col("ticker").str.starts_with("BK-"))
        by_id = rows.group_by("security_id").agg(pl.col("ticker").unique())
        assert sorted(sorted(t) for t in by_id["ticker"].to_list()) == [["BK-PA"], ["BK-PB"]]

    def test_primary_is_the_common_class(self, store: duckdb.DuckDBPyConnection) -> None:
        # Auditor SHOULD FIX 3: the preferred is listed first on the page.
        (row,) = _listings(store, primary_security_id(PREF))
        assert row["ticker"] == "PFC"

    def test_one_class_on_two_exchanges(self, store: duckdb.DuckDBPyConnection) -> None:
        rows = securities_as_of(store, INGESTED_AT).filter(pl.col("cik") == MULTI)
        assert rows.height == 1
        exchanges = {r["exchange"] for r in _listings(store, primary_security_id(MULTI))}
        assert exchanges == {"NASDAQ", "NYSE"}

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
        # ERLB: a later fetch whose ticker differs from the static span the
        # earliest fetch wrote (ERLA) is reported rather than dropped.
        assert [e.ticker for e in built.unmatched_snapshot] == ["TCKB", "ERLB"]
        tick_static = [
            row
            for row in built.listings
            if row["security_id"] == TICK and row["provenance"] != "filing"
        ]
        assert tick_static == []

    def test_snapshot_matched_as_of_its_fetch(self, store: duckdb.DuckDBPyConnection) -> None:
        # Auditor NIT 4: the 2018 fetch saw ERLA before any cover page; the
        # 2019 cover page's ERLB must not change what that fetch wrote, and
        # the later ERLB fetch adds no second static span.
        rows = _listings(store, primary_security_id(EARLY))
        assert [(r["ticker"], r["provenance"], r["valid_from"]) for r in rows] == [
            ("ERLA", "snapshot_static", date(2017, 3, 1)),
            ("ERLB", "filing", date(2019, 3, 1)),
        ]

    def test_unlisted_securities_are_reported(self, built: MasterBuild) -> None:
        # Auditor SHOULD FIX 2: delisted before the snapshot and before
        # cover pages, so no listing; reported for the survivorship gap.
        assert built.unlisted_securities == (primary_security_id(OLDCO),)

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


class TestDuplicatePairPerPage:
    """#687: a cover page that lists one class's (ticker, exchange) pair
    under two titles (a filer's duplicate) must build and insert exactly
    one `listings` row for that pair, not two rows that collide on the
    store's UNIQUE (security_id, ticker, exchange, valid_from, known_at)
    key. Real shapes: Honda (CIK 0000864270) 10-Q accepted 2021-11-09, two
    0.750% medium-term notes both tagged HMC/26A; Moatable (CIK 0001509223)
    10-Q accepted 2023-08-14, Class A ordinary shares and their ADS both
    retickered to MTBL."""

    NOTES_CIK = "0000900001"
    ADS_CIK = "0000900002"

    def _keys(self, build: MasterBuild) -> list[tuple[object, ...]]:
        return [
            (row["security_id"], row["ticker"], row["exchange"], row["valid_from"], row["known_at"])
            for row in build.listings
        ]

    def test_two_notes_typo_the_same_ticker_on_one_page(self) -> None:
        # The 0.750% Nov-2026 note is new on the bug page (first time this
        # class shows any pair at all): its pair is not yet in `cls.pairs`,
        # so the typo'd Jan-2024 note matching the same class and the same
        # (ticker, exchange) is the within-page duplicate #687 is about.
        cik = self.NOTES_CIK
        source = FixtureFilingSource(
            index=[_filing(cik, "Honda-like Co", "10-K", _at(2015, 3, 1))],
            cover_pages=[
                _cover(
                    cik,
                    _at(2021, 3, 1),
                    ("Common Stock, par value $0.50 per share", "HMC", "NYSE"),
                ),
                _cover(
                    cik,
                    datetime(2021, 11, 9, 17, 59, 52, tzinfo=UTC),
                    ("Common Stock, par value $0.50 per share", "HMC", "NYSE"),
                    (
                        "0.750% Medium-Term Notes, Series ADue November 25, 2026",
                        "HMC/26A",
                        "NYSE",
                    ),
                    (
                        "0.750% Medium-Term Notes, Series ADue January 17, 2024",
                        "HMC/26A",
                        "NYSE",
                    ),
                    (
                        "1.100% Medium-Term Notes, Series BDue October 1, 2025",
                        "HMC/25B",
                        "NYSE",
                    ),
                ),
            ],
        )
        ingested_at = datetime(2021, 11, 10, tzinfo=UTC)
        build = build_master(source, _settings(), ingested_at=ingested_at)
        keys = self._keys(build)
        assert len(keys) == len(set(keys))
        conn = _new_store()
        write_master(conn, build)  # must not raise duckdb.ConstraintException
        conn.close()

    def test_ads_and_underlying_typo_the_same_ticker_on_one_page(self) -> None:
        cik = self.ADS_CIK
        ads_title = "American depositary shares, each representing 45 Class A ordinary shares"
        class_a_title = "Class A ordinary shares, par value $0.001 per share*"
        source = FixtureFilingSource(
            index=[_filing(cik, "Moatable-like Inc", "10-K", _at(2015, 3, 1))],
            cover_pages=[
                _cover(cik, _at(2020, 3, 1), (ads_title, "RENN", "NYSE")),
                _cover(
                    cik,
                    _at(2023, 3, 31),
                    (class_a_title, "RENN", "NYSE"),
                    (ads_title, "RENN", "NYSE"),
                ),
                _cover(
                    cik,
                    datetime(2023, 8, 14, 20, 56, 14, tzinfo=UTC),
                    (class_a_title, "MTBL", "NYSE"),
                    (ads_title, "MTBL", "NYSE"),
                ),
            ],
        )
        ingested_at = datetime(2023, 8, 15, tzinfo=UTC)
        build = build_master(source, _settings(), ingested_at=ingested_at)
        keys = self._keys(build)
        assert len(keys) == len(set(keys))
        mtbl_rows = [row for row in build.listings if row["ticker"] == "MTBL"]
        assert len(mtbl_rows) == 1
        assert mtbl_rows[0]["class_title"] == class_a_title
        assert mtbl_rows[0]["security_id"] == primary_security_id(cik)
        conn = _new_store()
        write_master(conn, build)  # must not raise duckdb.ConstraintException
        conn.close()


class TestRelistingAfterForm25:
    """#820 (spec req 4): a cover page accepted after a Form 25 that still
    names the same (ticker, exchange) for the class opens a new listing row
    from its own session, `known_at` = that cover page's acceptance. Real
    shapes: CMPR redomicile (Form 25 2019-12-03), KIM holding-company
    reorganisation (2023-01-03), CRC new equity after bankruptcy (Form 25
    2020-07-31, trading again 2020-10-28)."""

    CIK = "0000900003"
    ORD = "Ordinary Shares, par value 0.01"

    def _build(
        self,
        covers: list[CoverPage],
        filings: list[DelistingFiling],
        *,
        cik: str = CIK,
        registered: tuple[datetime, ...] = (),
    ) -> MasterBuild:
        source = FixtureFilingSource(
            index=[
                _filing(cik, "Relist Co", "10-K", _at(2018, 3, 1)),
                *(_filing(cik, "Relist Co", "8-A12B", at) for at in registered),
            ],
            cover_pages=covers,
            delistings=filings,
        )
        return build_master(source, _settings(), ingested_at=INGESTED_AT)

    def _rows(self, build: MasterBuild) -> list[tuple[object, ...]]:
        return sorted(
            (row["security_id"], row["ticker"], row["exchange"], row["valid_from"], row["known_at"])
            for row in build.listings
        )

    def test_redomicile_opens_a_row_at_the_next_cover_page(self) -> None:
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 16), ("Ordinary Shares, nominal value", "RLC", "NASDAQ")),
            ],
            [_form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3))],
        )
        assert self._rows(build) == [
            (cik, "RLC", "NASDAQ", date(2019, 8, 9), _at(2019, 8, 9)),
            (cik, "RLC", "NASDAQ", date(2019, 12, 16), _at(2019, 12, 16)),
        ]
        assert [row["provenance"] for row in build.listings] == ["filing", "filing"]

    def test_without_a_form_25_a_repeated_pair_adds_no_row(self) -> None:
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 16), (self.ORD, "RLC", "NASDAQ")),
            ],
            [],
        )
        assert len(build.listings) == 1

    def test_a_cover_page_before_the_form_25_adds_no_row(self) -> None:
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 2), (self.ORD, "RLC", "NASDAQ")),
            ],
            [_form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3))],
        )
        assert len(build.listings) == 1

    def test_a_cover_page_before_the_delisting_takes_effect_relists_nothing(self) -> None:
        # code-review on #826: an acquisition target's 10-K filed between its
        # Form 25 and the effective day still names the pair; the shares are
        # still trading, so it opens no row, and the Form 25 ends the listing.
        cik = self.CIK
        filing = _form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3))
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 6), (self.ORD, "RLC", "NASDAQ")),
            ],
            [filing],
        )
        assert [row["valid_from"] for row in build.listings] == [date(2019, 8, 9)]
        delistings = build_delistings([filing], build, ingested_at=INGESTED_AT)
        ends = derive_listing_ends(
            pl.DataFrame(list(build.listings)),
            pl.DataFrame(list(delistings.delistings)),
            pl.DataFrame({"security_id": [cik], "session": [date(2019, 12, 12)]}),
            transfer_window_sessions=_settings().master.transfer_window_sessions,
        )
        assert ends.select("status", "end_session").rows() == [(DELISTED, date(2019, 12, 12))]

    def test_the_first_cover_page_after_the_effective_day_relists(self) -> None:
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 6), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2020, 1, 29), (self.ORD, "RLC", "NASDAQ")),
            ],
            [_form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3))],
        )
        assert self._rows(build)[-1] == (cik, "RLC", "NASDAQ", date(2020, 1, 29), _at(2020, 1, 29))

    def test_form_25_on_the_preferred_leaves_the_common_alone(self) -> None:
        cik = self.CIK
        pref = ("6% Series A Preferred Stock", "RLC-PA", "NYSE")
        common = ("Common Stock", "RLC", "NYSE")
        build = self._build(
            [
                _cover(cik, _at(2019, 3, 1), common, pref),
                _cover(cik, _at(2020, 3, 2), common),
            ],
            [_form25(cik, "6% Series A Preferred Stock", "NYSE", _at(2019, 12, 3))],
        )
        assert [row["ticker"] for row in build.listings] == ["RLC", "RLC-PA"]

    def test_plain_common_form_25_reaches_a_differently_worded_class(self) -> None:
        # KIM: "(OLD) Kimco Realty Corporation Common Stock, 5.125% Class L
        # Preferred ..." names no class title; it is plain common up to the
        # comma, and the common is the one common-only class on the exchange.
        cik = self.CIK
        common = ("Common Stock", "RLC", "NYSE")
        pref = ("5.125% Class L Cumulative Redeemable, Preferred Stock", "RLCprL", "NYSE")
        build = self._build(
            [
                _cover(cik, _at(2019, 7, 26), common, pref),
                _cover(cik, _at(2023, 2, 2), common, pref),
            ],
            [
                _form25(
                    cik,
                    "(OLD) Relist Co Common Stock, 5.125% Class L Preferred Stock",
                    "NYSE",
                    _at(2023, 1, 3),
                )
            ],
        )
        relisted = [row for row in build.listings if row["known_at"] == _at(2023, 2, 2)]
        assert [(row["security_id"], row["ticker"]) for row in relisted] == [(cik, "RLC")]

    def test_an_amendment_after_the_relisting_opens_no_second_row(self) -> None:
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 9), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2019, 12, 16), (self.ORD, "RLC", "NASDAQ")),
                _cover(cik, _at(2020, 3, 2), (self.ORD, "RLC", "NASDAQ")),
            ],
            [
                _form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3)),
                _form25(cik, "Ordinary Shares", "NASDAQ", _at(2020, 1, 6), form="25-NSE/A"),
            ],
        )
        assert [row["valid_from"] for row in build.listings] == [
            date(2019, 8, 9),
            date(2019, 12, 16),
        ]
        # Read side (code-review on #826): the late amendment never ends the
        # relisting row it postdates.
        filings = [
            _form25(cik, "Ordinary Shares", "NASDAQ", _at(2019, 12, 3)),
            _form25(cik, "Ordinary Shares", "NASDAQ", _at(2020, 1, 6), form="25-NSE/A"),
        ]
        delistings = build_delistings(filings, build, ingested_at=INGESTED_AT)
        ends = derive_listing_ends(
            pl.DataFrame(list(build.listings)),
            pl.DataFrame(list(delistings.delistings)),
            pl.DataFrame({"security_id": [cik], "session": [date(2019, 12, 13)]}),
            transfer_window_sessions=_settings().master.transfer_window_sessions,
        )
        assert ends.select("valid_from", "status").rows() == [
            (date(2019, 8, 9), DELISTED),
            (date(2019, 12, 16), LISTED),
        ]

    def test_an_amendment_alone_still_counts(self) -> None:
        # CG's only stored filing is a 25-NSE/A (original missing).
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 7, 31), ("Common units", "RLC", "NASDAQ")),
                _cover(cik, _at(2020, 2, 3), ("Common Stock", "RLC", "NASDAQ")),
            ],
            [_form25(cik, "Common units", "NASDAQ", _at(2020, 1, 3), form="25-NSE/A")],
        )
        assert [row["valid_from"] for row in build.listings] == [
            date(2019, 7, 31),
            date(2020, 2, 3),
        ]

    def test_relisting_after_a_gap_without_a_registration_keeps_the_security(self) -> None:
        # A Form 25, a cover page with nothing listed, then the same ticker
        # again with no 8-A12B: a relisting of the same security.
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 1), ("Common Stock", "RLC", "NYSE")),
                _cover(cik, _at(2020, 8, 10)),
                _cover(cik, _at(2020, 11, 5), ("Common Stock, par value $0.01", "RLC", "NYSE")),
            ],
            [_form25(cik, "Common Stock", "NYSE", _at(2020, 7, 31))],
        )
        assert self._rows(build) == [
            (cik, "RLC", "NYSE", date(2019, 8, 1), _at(2019, 8, 1)),
            (cik, "RLC", "NYSE", date(2020, 11, 5), _at(2020, 11, 5)),
        ]
        # Read side (spec req 4): the old listing is delisted and ends at its
        # last bar before the gap; the new one is listed.
        filings = [_form25(cik, "Common Stock", "NYSE", _at(2020, 7, 31))]
        delistings = build_delistings(filings, build, ingested_at=INGESTED_AT)
        bars = pl.DataFrame(
            {
                "security_id": [cik] * 4,
                "session": [
                    date(2020, 7, 29),
                    date(2020, 7, 30),
                    date(2020, 11, 5),
                    date(2020, 11, 6),
                ],
            }
        )
        ends = derive_listing_ends(
            pl.DataFrame(list(build.listings)).sort("valid_from"),
            pl.DataFrame(list(delistings.delistings)),
            bars,
            transfer_window_sessions=_settings().master.transfer_window_sessions,
        )
        assert ends.select("valid_from", "status", "end_session").rows() == [
            (date(2019, 8, 1), DELISTED, date(2020, 7, 30)),
            (date(2020, 11, 5), LISTED, None),
        ]

    def test_relisted_row_exists_only_from_its_cover_page(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        relisted_at = _at(2019, 12, 16)
        before = listings_as_of(store, relisted_at - PROBE_EPSILON, security_ids=[REORG])
        after = listings_as_of(store, relisted_at, security_ids=[REORG])
        assert before["valid_from"].to_list() == [date(2019, 8, 9)]
        assert sorted(after["valid_from"].to_list()) == [date(2019, 8, 9), date(2019, 12, 16)]


class TestNewEquityAfterForm25:
    """Owner decision on #820: post-bankruptcy equity under the same CIK and
    ticker is a new security, so no return spans the gap. Marker: an 8-A12B
    from `master.transfer_window_sessions` sessions before the Form 25 up to
    the relisting cover page (CRC, OAS, DBD, GPOR, MNK, WW and WOLF filed
    one; CMPR, CG, WELL, FCFS and KIM did not)."""

    CIK = "0000900004"

    def _source(self, *, registered: datetime | None) -> FixtureFilingSource:
        cik = self.CIK
        return FixtureFilingSource(
            index=[
                _filing(cik, "Crc Co", "10-K", _at(2018, 3, 1)),
                *([_filing(cik, "Crc Co", "8-A12B", registered)] if registered else []),
            ],
            cover_pages=[
                _cover(cik, _at(2019, 8, 1), ("Common Stock", "CRC", "NYSE")),
                _cover(cik, _at(2020, 8, 6)),  # in bankruptcy: nothing listed
                _cover(cik, _at(2020, 11, 5), ("Common Stock, par value $0.01", "CRC", "NYSE")),
                _cover(cik, _at(2021, 3, 1), ("Common Stock, par value $0.01", "CRC", "NYSE")),
            ],
            delistings=[self._form25()],
        )

    def _form25(self) -> DelistingFiling:
        return DelistingFiling(
            self.CIK,
            "25-NSE",
            "Common Stock",
            "NYSE",
            "25-crc",
            _at(2020, 7, 31),
            date(2020, 8, 10),
        )

    def test_a_registration_makes_the_relisted_shares_a_new_security(self) -> None:
        cik = self.CIK
        build = build_master(
            self._source(registered=_at(2020, 10, 27)), _settings(), ingested_at=INGESTED_AT
        )
        successor = f"{cik}@2020-11-05"
        assert build.successions == (Succession(cik, successor, _at(2020, 11, 5)),)
        assert [(r["security_id"], r["known_at"]) for r in build.securities] == [
            (cik, _at(2018, 3, 1)),
            (successor, _at(2020, 11, 5)),
        ]
        assert [(r["security_id"], r["valid_from"]) for r in build.listings] == [
            (cik, date(2019, 8, 1)),
            (successor, date(2020, 11, 5)),
        ]
        # The Form 25 still ends the old security: a successor known after
        # the filing is never its candidate.
        delistings = build_delistings([self._form25()], build, ingested_at=INGESTED_AT)
        assert [r["security_id"] for r in delistings.delistings] == [cik]

    def test_no_return_spans_the_bankruptcy(self) -> None:
        cik = self.CIK
        build = build_master(
            self._source(registered=_at(2020, 10, 27)), _settings(), ingested_at=INGESTED_AT
        )
        resolver = ListingResolver(build.listings)
        assert resolver.resolve("CRC", date(2020, 7, 30)) == cik
        assert resolver.resolve("CRC", date(2020, 11, 5)) == f"{cik}@2020-11-05"
        assert resolver.resolve("CRC", date(2021, 6, 1)) == f"{cik}@2020-11-05"
        # Each security's bars sit on one side of the gap only, so a return
        # (close over the security's previous close) never spans it.
        sessions = [date(2020, 7, 29), date(2020, 7, 30), date(2020, 11, 5), date(2020, 11, 6)]
        owners = [resolver.resolve("CRC", day) for day in sessions]
        assert owners == [cik, cik, f"{cik}@2020-11-05", f"{cik}@2020-11-05"]

    def test_a_registration_just_before_the_form_25_counts(self) -> None:
        # WOLF: 8-A12B 2025-09-26, Form 25 2025-09-29 effective 2025-10-09,
        # a cover page the next day; the successor starts after the old
        # shares' last day.
        cik = self.CIK
        source = FixtureFilingSource(
            index=[
                _filing(cik, "Wolf Co", "10-K", _at(2018, 3, 1)),
                _filing(cik, "Wolf Co", "8-A12B", _at(2025, 9, 26)),
            ],
            cover_pages=[
                _cover(cik, _at(2025, 8, 26), ("Common Stock", "WLF", "NYSE")),
                _cover(cik, _at(2025, 9, 30), ("Common Stock", "WLF", "NYSE")),
            ],
            delistings=[
                DelistingFiling(
                    cik,
                    "25-NSE",
                    "Common Stock",
                    "NYSE",
                    "25-wlf",
                    _at(2025, 9, 29),
                    date(2025, 10, 9),
                )
            ],
        )
        build = build_master(source, _settings(), ingested_at=INGESTED_AT)
        assert [(r["security_id"], r["valid_from"], r["known_at"]) for r in build.listings] == [
            (cik, date(2025, 8, 26), _at(2025, 8, 26)),
            (f"{cik}@2025-10-10", date(2025, 10, 10), _at(2025, 9, 30)),
        ]

    def test_a_registration_outside_the_window_keeps_the_security(self) -> None:
        cik = self.CIK
        build = build_master(
            self._source(registered=_at(2020, 5, 1)), _settings(), ingested_at=INGESTED_AT
        )
        assert build.successions == ()
        assert [(r["security_id"], r["valid_from"]) for r in build.listings] == [
            (cik, date(2019, 8, 1)),
            (cik, date(2020, 11, 5)),
        ]

    def test_the_successor_is_known_only_from_its_cover_page(self) -> None:
        source = self._source(registered=_at(2020, 10, 27))
        settings = _settings()
        full = _new_store()
        write_master(full, build_master(source, settings, ingested_at=INGESTED_AT))
        probes = sorted(
            {k + d for k in source.known_ats() for d in (-PROBE_EPSILON, PROBE_EPSILON)}
        )
        for t in probes:
            partial = _new_store()
            write_master(
                partial, build_master(source.known_by(t), settings, ingested_at=INGESTED_AT)
            )
            for read in (securities_as_of, listings_as_of):
                assert read(full, t).equals(read(partial, t)), f"{read.__name__} T={t!r}"
            partial.close()
        before = securities_as_of(full, _at(2020, 11, 5) - PROBE_EPSILON)
        assert f"{self.CIK}@2020-11-05" not in _ids(before)
        full.close()

    def test_a_delisted_successor_is_ended_by_its_own_form_25(self) -> None:
        # quant-auditor on #826: the successor's later Form 25 names the same
        # title as the old class; it must resolve to the successor.
        cik = self.CIK
        first = self._form25()
        second = DelistingFiling(
            cik, "25-NSE", "Common Stock", "NYSE", "25-crc-2", _at(2023, 6, 1), date(2023, 6, 12)
        )
        source = self._source(registered=_at(2020, 10, 27))
        source = FixtureFilingSource(
            index=source.filing_index(),
            cover_pages=source.cover_pages(cik),
            delistings=[first, second],
        )
        build = build_master(source, _settings(), ingested_at=INGESTED_AT)
        delistings = build_delistings([first, second], build, ingested_at=INGESTED_AT)
        assert [r["security_id"] for r in delistings.delistings] == [cik, f"{cik}@2020-11-05"]
        assert delistings.unmatched == ()

    @pytest.mark.parametrize("registered_back", [True, False])
    def test_a_transfer_and_a_move_back_make_no_successor(self, registered_back: bool) -> None:
        # quant-auditor on #826: NYSE -> NASDAQ (Form 25 on NYSE, 8-A12B for
        # NASDAQ), later NASDAQ -> NYSE. A move, never new equity.
        cik = self.CIK
        index = [
            _filing(cik, "Move Co", "10-K", _at(2018, 3, 1)),
            _filing(cik, "Move Co", "8-A12B", _at(2020, 2, 27)),
        ]
        if registered_back:
            index.append(_filing(cik, "Move Co", "8-A12B", _at(2023, 2, 27)))
        source = FixtureFilingSource(
            index=index,
            cover_pages=[
                _cover(cik, _at(2019, 8, 1), ("Common Stock", "XYZ", "NYSE")),
                _cover(cik, _at(2020, 5, 1), ("Common Stock", "XYZ", "NASDAQ")),
                _cover(cik, _at(2023, 5, 1), ("Common Stock", "XYZ", "NYSE")),
            ],
            delistings=[
                _form25(cik, "Common Stock", "NYSE", _at(2020, 3, 2)),
                _form25(cik, "Common Stock", "NASDAQ", _at(2023, 3, 1)),
            ],
        )
        build = build_master(source, _settings(), ingested_at=INGESTED_AT)
        assert build.successions == ()
        assert [(r["security_id"], r["exchange"], r["valid_from"]) for r in build.listings] == [
            (cik, "NYSE", date(2019, 8, 1)),
            (cik, "NASDAQ", date(2020, 5, 1)),
            (cik, "NYSE", date(2023, 5, 1)),
        ]

    def test_a_late_amendment_never_delists_the_successor(self) -> None:
        # quant-auditor pass 2 on #826: a 25-NSE/A amending the old class's
        # Form 25, filed after the successor is known, amends the old class.
        cik = self.CIK
        amendment = DelistingFiling(
            cik, "25-NSE/A", "Common Stock", "NYSE", "25-crc-a", _at(2020, 12, 1), date(2020, 8, 10)
        )
        build = build_master(
            self._source(registered=_at(2020, 10, 27)), _settings(), ingested_at=INGESTED_AT
        )
        delistings = build_delistings([self._form25(), amendment], build, ingested_at=INGESTED_AT)
        assert [r["security_id"] for r in delistings.delistings] == [cik, cik]
        ends = derive_listing_ends(
            pl.DataFrame(list(build.listings)),
            pl.DataFrame(list(delistings.delistings)),
            pl.DataFrame({"security_id": [cik], "session": [date(2020, 7, 30)]}),
            transfer_window_sessions=_settings().master.transfer_window_sessions,
        )
        assert ends.select("security_id", "status").rows() == [
            (cik, DELISTED),
            (f"{cik}@2020-11-05", LISTED),
        ]


class TestListingEvidenceAfterForm25:
    """#834: reorganised securities that #826 still left delisted on the
    owner's store. Real shapes: WSC (merger: 8-A12B and 15-12B, the same
    shares reclassified), FRT (8-K12B the day of the Form 25, then cover
    pages that parse with no listing), OKE (8-K12B before the Form 25, no
    cover page since), SA (no filing; the companies snapshot still lists
    it), and GORO (acquired: 15-12G, no ticker left) which stays delisted."""

    CIK = "0000900005"
    INGESTED = datetime(2026, 10, 5, tzinfo=UTC)

    def _build(
        self,
        covers: list[CoverPage],
        filing: DelistingFiling,
        *,
        forms: tuple[tuple[str, datetime], ...] = (),
        snapshot: tuple[CompanySnapshotEntry, ...] = (),
    ) -> MasterBuild:
        return build_master(
            self._source(covers, filing, forms, snapshot), _settings(), ingested_at=self.INGESTED
        )

    def _source(
        self,
        covers: list[CoverPage],
        filing: DelistingFiling,
        forms: tuple[tuple[str, datetime], ...],
        snapshot: tuple[CompanySnapshotEntry, ...],
    ) -> FixtureFilingSource:
        cik = self.CIK
        return FixtureFilingSource(
            index=[
                _filing(cik, "Reorg Co", "10-K", _at(2018, 3, 1)),
                *(_filing(cik, "Reorg Co", form, at) for form, at in forms),
            ],
            cover_pages=covers,
            delistings=[filing],
            snapshot=snapshot,
        )

    def _rows(self, build: MasterBuild) -> list[tuple[object, ...]]:
        return [
            (r["security_id"], r["ticker"], r["valid_from"], r["known_at"], r["provenance"])
            for r in build.listings
            if r["provenance"] != "snapshot_static"
        ]

    def test_a_merger_with_a_form_15_is_not_new_equity(self) -> None:
        # WSC: 8-A12B 2020-07-01, Form 25 the same day, 15-12B 2020-07-13.
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2020, 5, 6), ("Class A common stock, par value", "WSC", "NASDAQ")),
                _cover(cik, _at(2020, 8, 10), ("Common Stock, par value $0.0001", "WSC", "NASDAQ")),
            ],
            DelistingFiling(
                cik, "25-NSE", "Class A Common Stock", "NASDAQ", "25-wsc", _at(2020, 7, 1, 15, 0)
            ),
            forms=(("8-A12B", _at(2020, 7, 1, 14, 0)), ("15-12B", _at(2020, 7, 13))),
        )
        assert build.successions == ()
        assert self._rows(build) == [
            (cik, "WSC", date(2020, 5, 6), _at(2020, 5, 6), "filing"),
            (cik, "WSC", date(2020, 8, 10), _at(2020, 8, 10), "filing"),
        ]

    def test_an_8k12b_relists_without_a_cover_page(self) -> None:
        # FRT: 8-K12B and Form 25 on 2022-01-03 (effective 2022-01-13); every
        # later cover page parses with no listing.
        cik = self.CIK
        build = self._build(
            [
                _cover(
                    cik, _at(2021, 11, 4), ("Common Shares of Beneficial Interest", "FRT", "NYSE")
                ),
                _cover(cik, _at(2022, 2, 10)),
                _cover(cik, _at(2022, 5, 5)),
            ],
            DelistingFiling(
                cik,
                "25-NSE",
                "Common Shares of Beneficial Interest",
                "NYSE",
                "25-frt",
                _at(2022, 1, 3, 21, 0),
                date(2022, 1, 13),
            ),
            forms=(("8-K12B", _at(2022, 1, 3, 13, 30)),),
        )
        assert self._rows(build) == [
            (cik, "FRT", date(2021, 11, 4), _at(2021, 11, 4), "filing"),
            (cik, "FRT", date(2022, 1, 14), _at(2022, 1, 3, 21, 0), "filing"),
        ]

    def _oke(self) -> FixtureFilingSource:
        cik = self.CIK
        return self._source(
            [_cover(cik, _at(2026, 8, 4), ("Common stock, par value of $0.01", "OKE", "NYSE"))],
            DelistingFiling(
                cik,
                "25-NSE",
                "Common Stock",
                "NYSE",
                "25-oke",
                _at(2026, 9, 18, 13, 35),
                date(2026, 9, 28),
            ),
            (("8-K12B", _at(2026, 9, 10, 20, 47)), ("15-12G", _at(2026, 9, 28, 21, 9))),
            (),
        )

    def test_an_8k12b_before_the_form_25_relists_at_the_form_25(self) -> None:
        cik = self.CIK
        build = build_master(self._oke(), _settings(), ingested_at=self.INGESTED)
        assert self._rows(build) == [
            (cik, "OKE", date(2026, 8, 4), _at(2026, 8, 4), "filing"),
            (cik, "OKE", date(2026, 9, 29), _at(2026, 9, 18, 13, 35), "filing"),
        ]

    def test_the_resolver_keeps_a_relisted_security_trading(self) -> None:
        # End to end with #830's own-delisting rule: OKE's bars after the
        # Form 25's effective day stay on OKE.
        cik = self.CIK
        source = self._oke()
        build = build_master(source, _settings(), ingested_at=self.INGESTED)
        delistings = build_delistings(source.delistings(), build, ingested_at=self.INGESTED)
        bars = [date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 2)]
        ends = derive_listing_ends(
            pl.DataFrame(list(build.listings)),
            pl.DataFrame(list(delistings.delistings)),
            pl.DataFrame({"security_id": [cik] * len(bars), "session": bars}),
            transfer_window_sessions=_settings().master.transfer_window_sessions,
        )
        evidence = registrant_evidence(
            [], ends.to_dicts(), as_of=date(2026, 10, 4), quiet_after_days=180
        )
        resolver = ListingResolver(build.listings, evidence)
        assert [resolver.resolve("OKE", day) for day in bars] == [cik] * len(bars)

    def _sa(self, *, fetched_at: datetime | None) -> FixtureFilingSource:
        cik = self.CIK
        snapshot = (
            () if fetched_at is None else (_snap(cik, "SEABRIDGE", "SA", "NYSE", fetched_at),)
        )
        return self._source(
            [_cover(cik, _at(2026, 3, 27), ("Common Shares", "SA", "NYSE"))],
            DelistingFiling(
                cik,
                "25-NSE",
                "Common Shares",
                "NYSE",
                "25-sa",
                _at(2026, 6, 5, 16, 47),
                date(2026, 6, 15),
            ),
            (),
            snapshot,
        )

    def test_a_snapshot_after_the_form_25_relists(self) -> None:
        cik = self.CIK
        build = build_master(
            self._sa(fetched_at=_at(2026, 10, 4)), _settings(), ingested_at=self.INGESTED
        )
        assert self._rows(build)[-1] == (cik, "SA", date(2026, 6, 16), _at(2026, 10, 4), "snapshot")

    def test_without_evidence_the_form_25_stands(self) -> None:
        build = build_master(self._sa(fetched_at=None), _settings(), ingested_at=self.INGESTED)
        assert [r["valid_from"] for r in build.listings] == [date(2026, 3, 27)]

    def test_a_snapshot_before_the_form_25_relists_nothing(self) -> None:
        build = build_master(
            self._sa(fetched_at=_at(2026, 6, 1)), _settings(), ingested_at=self.INGESTED
        )
        assert [
            r["provenance"] for r in build.listings if r["valid_from"] > date(2026, 3, 27)
        ] == []

    def test_an_acquired_company_stays_delisted(self) -> None:
        # GORO: Form 25, 8-K (2.01), 15-12G; the ticker left the CIK.
        cik = self.CIK
        build = self._build(
            [_cover(cik, _at(2026, 5, 7), ("Common Stock", "GORO", "NYSE_AMERICAN"))],
            DelistingFiling(
                cik, "25-NSE", "Common stock", "NYSE_AMERICAN", "25-goro", _at(2026, 7, 20, 14, 56)
            ),
            forms=(("15-12G", _at(2026, 7, 30, 20, 1)),),
        )
        assert [r["valid_from"] for r in build.listings] == [date(2026, 5, 7)]

    @pytest.mark.parametrize("case", ["oke", "sa"])
    def test_the_relisting_is_known_only_from_its_evidence(self, case: str) -> None:
        source = self._oke() if case == "oke" else self._sa(fetched_at=_at(2026, 10, 4))
        settings = _settings()
        full = _new_store()
        write_master(full, build_master(source, settings, ingested_at=self.INGESTED))
        probes = sorted(
            {k + d for k in source.known_ats() for d in (-PROBE_EPSILON, PROBE_EPSILON)}
        )
        for t in probes:
            partial = _new_store()
            write_master(
                partial, build_master(source.known_by(t), settings, ingested_at=self.INGESTED)
            )
            assert listings_as_of(full, t).equals(listings_as_of(partial, t)), f"T={t!r}"
            partial.close()
        full.close()

    def test_an_8k12b_with_a_transfer_waits_for_a_cover_page(self) -> None:
        # CTO (REIT conversion and NYSE American -> NYSE, 2021): 8-A12B
        # 2021-01-28 (the NYSE registration), 25-NSE and 8-K12B 2021-02-01,
        # 15-12B 2021-02-12, a 10-K cover page on NYSE 2021-03-05. The 8-K12B
        # must not relist the old exchange (nor make new equity): a transfer.
        cik = self.CIK
        build = self._build(
            [
                _cover(
                    cik, _at(2020, 11, 5), ("COMMON STOCK, $1 PAR VALUE", "CTO", "NYSE_AMERICAN")
                ),
                _cover(
                    cik, _at(2021, 3, 5), ("Common Stock, $0.01 par value per share", "CTO", "NYSE")
                ),
            ],
            DelistingFiling(
                cik,
                "25-NSE",
                "Common Stock",
                "NYSE_AMERICAN",
                "25-cto",
                _at(2021, 2, 1, 18, 10),
                date(2021, 2, 11),
            ),
            forms=(
                ("8-A12B", _at(2021, 1, 28)),
                ("8-K12B", _at(2021, 2, 1, 21, 0)),
                ("15-12B", _at(2021, 2, 12)),
            ),
        )
        assert build.successions == ()
        assert self._rows(build) == [
            (cik, "CTO", date(2020, 11, 5), _at(2020, 11, 5), "filing"),
            (cik, "CTO", date(2021, 3, 5), _at(2021, 3, 5), "filing"),
        ]

    @pytest.mark.parametrize(
        "fetched_at",
        [_at(2026, 6, 10), _at(2026, 6, 15), _at(2026, 6, 16), _at(2026, 7, 14)],
        ids=["before-effective", "effective-day", "day-after-effective", "inside-lag"],
    )
    def test_a_snapshot_inside_the_lag_relists_nothing(self, fetched_at: datetime) -> None:
        # safety-reviewer and quant-auditor on #835: a daily fetch before SEC
        # drops a delisted ticker must not relist it (effective 2026-06-15,
        # lag 30 days: first eligible fetch 2026-07-15).
        build = build_master(
            self._sa(fetched_at=fetched_at), _settings(), ingested_at=self.INGESTED
        )
        assert [r[2] for r in self._rows(build)] == [date(2026, 3, 27)]

    def test_a_snapshot_after_a_form_15_relists_nothing(self) -> None:
        # An acquired company deregisters; a fetch that still shows its
        # ticker is no listing evidence.
        cik = self.CIK
        build = self._build(
            [_cover(cik, _at(2026, 5, 7), ("Common Stock", "GORO", "NYSE_AMERICAN"))],
            DelistingFiling(
                cik, "25-NSE", "Common stock", "NYSE_AMERICAN", "25-goro", _at(2026, 7, 20, 14, 56)
            ),
            forms=(("15-12G", _at(2026, 7, 30, 20, 1)),),
            snapshot=(_snap(cik, "GOLD RESOURCE", "GORO", "NYSE_AMERICAN", _at(2026, 10, 4)),),
        )
        assert [r[2] for r in self._rows(build)] == [date(2026, 5, 7)]

    def test_a_snapshot_after_a_late_form_15_relists_nothing(self) -> None:
        # Pass-2 fix on #835: a Form 15 filed after the reorganisation window
        # but before the fetch still vetoes the snapshot (the company left
        # the registry; a stale ticker file is no listing evidence).
        cik = self.CIK
        build = self._build(
            [_cover(cik, _at(2026, 5, 7), ("Common Stock", "GORO", "NYSE_AMERICAN"))],
            DelistingFiling(
                cik, "25-NSE", "Common stock", "NYSE_AMERICAN", "25-goro", _at(2026, 7, 20, 14, 56)
            ),
            forms=(("15-12G", _at(2026, 8, 31, 20, 1)),),
            snapshot=(_snap(cik, "GOLD RESOURCE", "GORO", "NYSE_AMERICAN", _at(2026, 10, 4)),),
        )
        assert [r[2] for r in self._rows(build)] == [date(2026, 5, 7)]

    def test_an_8k12b_years_after_the_form_25_relists_nothing(self) -> None:
        # safety-reviewer and quant-auditor on #835: an unrelated 8-K12B long
        # after an old Form 25 (a redeemed class) must not revive it.
        cik = self.CIK
        build = self._build(
            [_cover(cik, _at(2015, 3, 2), ("Common Stock", "OLD", "NYSE"))],
            DelistingFiling(cik, "25-NSE", "Common Stock", "NYSE", "25-old", _at(2015, 6, 1)),
            forms=(("8-K12B", _at(2020, 6, 1)),),
        )
        assert [r["valid_from"] for r in build.listings] == [date(2015, 3, 2)]

    def test_a_late_form_15_never_vetoes_new_equity(self) -> None:
        # quant-auditor on #835: a bankruptcy whose Form 15 comes months after
        # the Form 25 (outside the reorganisation window) is still new equity.
        cik = self.CIK
        build = self._build(
            [
                _cover(cik, _at(2019, 8, 1), ("Common Stock", "CRC", "NYSE")),
                _cover(cik, _at(2020, 11, 5), ("Common Stock", "CRC", "NYSE")),
            ],
            DelistingFiling(
                cik, "25-NSE", "Common Stock", "NYSE", "25-crc", _at(2020, 7, 31), date(2020, 8, 10)
            ),
            forms=(("15-12B", _at(2020, 10, 1)), ("8-A12B", _at(2020, 10, 27))),
        )
        assert [s.security_id for s in build.successions] == [f"{cik}@2020-11-05"]

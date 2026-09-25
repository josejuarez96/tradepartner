"""Tests for tradepartner.store.classify (T9).

Plan T9 tests: one test per rule, F-6 included; the unclassifiable bucket;
every row carries its rule and the `known_at` of the filing used or the
fetch time. Plus the look-ahead checks: a classification built from a
source truncated to what was knowable at T agrees with one built from the
full source at every T, and `classifications_as_of` is invariant under
truncation of the store.

Scenarios use `FixtureFilingSource` with synthetic records, as in
`test_master.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest
from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.classify import (
    ClassificationBuild,
    build_classifications,
    classifications_as_of,
    write_classifications,
)
from tradepartner.store.db import configure_connection
from tradepartner.store.master import build_master, primary_security_id, write_master

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FETCHED_AT = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)

PLAIN = "0000000101"  # 10-K from 2015, a common cover page from 2019
SPAC = "0000000102"  # SIC 6770 until a 2021 header shows an operating SIC
FOREIGN = "0000000103"  # 20-F filer that later files a 10-K
ADR = "0000000104"  # 20-F filer, then an F-6 for its depositary shares
BDC = "0000000105"  # 10-K filer that later files an N-2
CLASSES = "0000000106"  # common plus preferred, warrant, unit, right and trust classes
SNAPW = "0000000107"  # pre-2019 filer whose only listing is a snapshot warrant ticker
SNAPC = "0000000108"  # pre-2019 filer whose only listing is a plain snapshot ticker
BARE = "0000000109"  # only an 8-K: no status form, no title, no ticker
SPY_TRUST = "0000884394"
ISHARES = "0001100663"


def _at(year: int, month: int, day: int, hour: int = 20, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


_counter = iter(range(1, 1_000_000))


def _accession(cik: str) -> str:
    return f"{cik}-{next(_counter):06d}"


def _filing(cik: str, form: str, accepted_at: datetime, name: str = "Co") -> FilingIndexEntry:
    return FilingIndexEntry(cik, name, form, _accession(cik), accepted_at)


def _header(cik: str, form: str, sic: int | None, accepted_at: datetime) -> FilingHeader:
    return FilingHeader(cik, _accession(cik), form, sic, accepted_at)


def _cover(cik: str, accepted_at: datetime, *listings: tuple[str, str, str]) -> CoverPage:
    return CoverPage(
        cik,
        _accession(cik),
        accepted_at,
        tuple(CoverListing(title, ticker, exchange) for title, ticker, exchange in listings),
    )


def _snap(cik: str, ticker: str, fetched_at: datetime = FETCHED_AT) -> CompanySnapshotEntry:
    return CompanySnapshotEntry(cik, "CO", ticker, "NYSE", fetched_at)


def _source() -> FixtureFilingSource:
    return FixtureFilingSource(
        index=[
            _filing(PLAIN, "10-K", _at(2015, 3, 2)),
            _filing(SPAC, "S-1", _at(2018, 1, 5)),
            _filing(SPAC, "10-K", _at(2019, 3, 1)),
            _filing(SPAC, "10-K", _at(2021, 3, 1)),
            _filing(FOREIGN, "20-F", _at(2016, 4, 1)),
            _filing(FOREIGN, "10-K", _at(2022, 3, 1)),
            _filing(ADR, "20-F", _at(2016, 4, 5)),
            _filing(ADR, "F-6", _at(2017, 6, 1)),
            _filing(BDC, "10-K", _at(2016, 3, 1)),
            _filing(BDC, "N-2", _at(2017, 5, 1)),
            _filing(CLASSES, "10-K", _at(2018, 3, 1)),
            _filing(SNAPW, "10-K", _at(2012, 3, 1)),
            _filing(SNAPC, "10-K", _at(2012, 3, 2)),
            _filing(BARE, "8-K", _at(2017, 1, 5)),
        ],
        headers=[
            _header(PLAIN, "10-K", 7372, _at(2015, 3, 2)),
            _header(SPAC, "S-1", 6770, _at(2018, 1, 5)),
            _header(SPAC, "10-K", 6770, _at(2019, 3, 1)),
            _header(SPAC, "10-K", 7372, _at(2021, 3, 1)),
        ],
        cover_pages=[
            _cover(PLAIN, _at(2019, 3, 4), ("Common Stock, par value $0.01", "PLN", "NYSE")),
            _cover(SPAC, _at(2021, 3, 1), ("Class A Common Stock", "SPC", "NASDAQ")),
            _cover(FOREIGN, _at(2020, 4, 1), ("Ordinary Shares", "FRN", "NYSE")),
            _cover(
                CLASSES,
                _at(2019, 2, 4),
                ("Common Stock", "CLS", "NYSE"),
                ("6.5% Series A Preferred Stock", "CLS-PA", "NYSE"),
                ("Warrants, each exercisable for one share of Common Stock", "CLS-WS", "NYSE"),
                ("Units, each of one share of Common Stock and one Warrant", "CLS-U", "NYSE"),
                ("Rights to receive one-tenth of one share", "CLS-R", "NYSE"),
                ("Shares of Beneficial Interest", "CLSB", "NYSE"),
            ),
        ],
        snapshot=[
            _snap(SNAPW, "SNW-WS"),
            _snap(SNAPC, "SNC"),
            CompanySnapshotEntry(SPY_TRUST, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE", FETCHED_AT),
            CompanySnapshotEntry(ISHARES, "iShares Trust", "MTUM", "NYSE", FETCHED_AT),
        ],
    )


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _build(source: FixtureFilingSource | None = None) -> ClassificationBuild:
    source = source or _source()
    settings = _settings()
    master = build_master(source, settings, ingested_at=INGESTED_AT)
    return build_classifications(source, master, settings, ingested_at=INGESTED_AT)


def _new_store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


@pytest.fixture
def built() -> ClassificationBuild:
    return _build()


@pytest.fixture
def store(built: ClassificationBuild) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = _new_store()
    write_classifications(conn, built)
    try:
        yield conn
    finally:
        conn.close()


def _history(built: ClassificationBuild, security_id: str) -> list[tuple[str, str, datetime]]:
    """(security_type, rule, known_at) rows of one security, oldest first."""
    rows = [r for r in built.classifications if r["security_id"] == security_id]
    return [(r["security_type"], r["rule"], r["known_at"]) for r in rows]


def _latest(built: ClassificationBuild, security_id: str) -> dict[str, object]:
    rows = [r for r in built.classifications if r["security_id"] == security_id]
    assert rows, f"no classification for {security_id}"
    return max(rows, key=lambda r: r["known_at"])


def _class_id(cik: str, slug: str) -> str:
    return f"{cik}:{slug}"


class TestRules:
    def test_common_title_from_cover_page(self, built: ClassificationBuild) -> None:
        # Before the cover page the 10-K alone makes it a domestic filer.
        assert _history(built, primary_security_id(PLAIN)) == [
            ("common", "common_default", _at(2015, 3, 2)),
            ("common", "title_common", _at(2019, 3, 4)),
        ]

    def test_sic_6770_is_spac_until_the_sic_changes(self, built: ClassificationBuild) -> None:
        assert _history(built, primary_security_id(SPAC)) == [
            ("spac", "sic_6770", _at(2018, 1, 5)),
            ("common", "title_common", _at(2021, 3, 1)),
        ]

    def test_sic_is_carried_on_every_row(self, built: ClassificationBuild) -> None:
        rows = [r for r in built.classifications if r["security_id"] == primary_security_id(SPAC)]
        assert [r["sic"] for r in rows] == [6770, 7372]

    def test_20f_is_foreign_until_a_10k(self, built: ClassificationBuild) -> None:
        # The 2020 cover page changes nothing while the issuer files 20-F.
        assert _history(built, primary_security_id(FOREIGN)) == [
            ("foreign", "foreign_form", _at(2016, 4, 1)),
            ("common", "title_common", _at(2022, 3, 1)),
        ]

    @pytest.mark.parametrize("form", ["20-F", "40-F", "20-F/A"])
    def test_foreign_forms(self, form: str) -> None:
        # The 8-K creates the security (an amendment is not an issuer form).
        source = FixtureFilingSource(
            index=[
                _filing(FOREIGN, "8-K", _at(2016, 1, 4)),
                _filing(FOREIGN, form, _at(2016, 4, 1)),
            ]
        )
        assert _latest(_build(source), primary_security_id(FOREIGN))["security_type"] == "foreign"

    def test_f6_makes_depositary(self, built: ClassificationBuild) -> None:
        assert _history(built, primary_security_id(ADR)) == [
            ("foreign", "foreign_form", _at(2016, 4, 5)),
            ("depositary", "f6_depositary", _at(2017, 6, 1)),
        ]

    @pytest.mark.parametrize("form", ["F-6", "F-6EF", "F-6/A"])
    def test_f6_forms(self, form: str) -> None:
        source = FixtureFilingSource(
            index=[_filing(ADR, "20-F", _at(2016, 4, 5)), _filing(ADR, form, _at(2017, 6, 1))]
        )
        latest = _latest(_build(source), primary_security_id(ADR))
        assert (latest["security_type"], latest["rule"]) == ("depositary", "f6_depositary")

    def test_investment_company_form_makes_fund(self, built: ClassificationBuild) -> None:
        assert _history(built, primary_security_id(BDC)) == [
            ("common", "common_default", _at(2016, 3, 1)),
            ("fund", "fund_form", _at(2017, 5, 1)),
        ]

    @pytest.mark.parametrize("form", ["N-CSR", "N-CSRS", "NPORT-P", "N-PORT", "485BPOS", "N-2"])
    def test_fund_forms(self, form: str) -> None:
        source = FixtureFilingSource(
            index=[_filing(BDC, "10-K", _at(2016, 3, 1)), _filing(BDC, form, _at(2017, 5, 1))]
        )
        assert _latest(_build(source), primary_security_id(BDC))["rule"] == "fund_form"

    @pytest.mark.parametrize(
        ("slug", "security_type", "rule"),
        [
            ("6-5pct-series-a-preferred-stock", "preferred", "title_preferred"),
            ("warrants", "warrant", "title_warrant"),
            ("units", "unit", "title_unit"),
            ("rights-to-receive-one-tenth-of-one-share", "right", "title_right"),
            ("shares-of-beneficial-interest", "unclassifiable", "title_unrecognized"),
        ],
    )
    def test_class_title_rules(
        self, built: ClassificationBuild, slug: str, security_type: str, rule: str
    ) -> None:
        security_id = _class_id(CLASSES, slug)
        assert _history(built, security_id) == [(security_type, rule, _at(2019, 2, 4))]

    def test_common_class_of_a_multi_class_issuer(self, built: ClassificationBuild) -> None:
        latest = _latest(built, primary_security_id(CLASSES))
        assert (latest["security_type"], latest["rule"]) == ("common", "title_common")

    def test_snapshot_ticker_suffix(self, built: ClassificationBuild) -> None:
        # Known as a warrant only from the fetch that supplied the ticker.
        assert _history(built, primary_security_id(SNAPW)) == [
            ("common", "common_default", _at(2012, 3, 1)),
            ("warrant", "suffix_warrant", FETCHED_AT),
        ]
        assert _latest(built, primary_security_id(SNAPW))["provenance"] == "snapshot_static"

    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("ABC.WS", "warrant"),
            ("ABC-WT", "warrant"),
            ("ABCDW", "warrant"),
            ("ABC.U", "unit"),
            ("ABC-UN", "unit"),
            ("ABCDU", "unit"),
            ("ABC-RT", "right"),
            ("ABCDR", "right"),
            ("ABC-P", "preferred"),
            ("ABC-PA", "preferred"),
            ("ABC.PRB", "preferred"),
            ("ABCDY", "depositary"),
            ("BRK.B", "common"),
            ("ABCD", "common"),
        ],
    )
    def test_suffix_conventions(self, ticker: str, expected: str) -> None:
        source = FixtureFilingSource(
            index=[_filing(SNAPW, "10-K", _at(2012, 3, 1))], snapshot=[_snap(SNAPW, ticker)]
        )
        assert _latest(_build(source), primary_security_id(SNAPW))["security_type"] == expected

    def test_plain_snapshot_ticker_keeps_common_default(self, built: ClassificationBuild) -> None:
        # A ticker with no suffix changes nothing, so the fetch adds no row.
        assert _history(built, primary_security_id(SNAPC)) == [
            ("common", "common_default", _at(2012, 3, 2)),
        ]

    def test_benchmarks_are_etfs_from_config(self, built: ClassificationBuild) -> None:
        for ticker in ("SPY", "MTUM"):
            row = _latest(built, f"BENCH:{ticker}")
            assert (row["security_type"], row["rule"], row["source"]) == (
                "etf",
                "benchmark_config",
                "config",
            )
            assert row["known_at"] == FETCHED_AT


class TestUnclassifiable:
    def test_no_rule_matched(self, built: ClassificationBuild) -> None:
        assert _history(built, primary_security_id(BARE)) == [
            ("unclassifiable", "no_rule_matched", _at(2017, 1, 5)),
        ]

    def test_every_security_has_a_row(self, built: ClassificationBuild) -> None:
        master = build_master(_source(), _settings(), ingested_at=INGESTED_AT)
        classified = {r["security_id"] for r in built.classifications}
        assert classified == {r["security_id"] for r in master.securities}


class TestRows:
    def test_each_row_carries_rule_known_at_and_provenance(
        self, built: ClassificationBuild
    ) -> None:
        for row in built.classifications:
            assert row["rule"]
            assert row["known_at"].tzinfo is not None
            assert row["known_at"] <= row["ingested_at"] == INGESTED_AT
            assert row["provenance"] in schema.TABLE_PROVENANCE_VALUES["classifications"]

    def test_no_row_before_the_security_exists(self, built: ClassificationBuild) -> None:
        master = build_master(_source(), _settings(), ingested_at=INGESTED_AT)
        first = {r["security_id"]: r["known_at"] for r in master.securities}
        for row in built.classifications:
            assert row["known_at"] >= first[row["security_id"]]

    def test_unchanged_evidence_writes_no_row(self, built: ClassificationBuild) -> None:
        # A second identical cover page would add nothing; the 2020 FOREIGN
        # cover page is the in-scenario case (checked in TestRules too).
        keys = [(r["security_id"], r["known_at"]) for r in built.classifications]
        assert len(keys) == len(set(keys))

    def test_evidence_after_ingested_at_raises(self) -> None:
        source = FixtureFilingSource(index=[_filing(PLAIN, "10-K", _at(2015, 3, 2))])
        settings = _settings()
        master = build_master(source, settings, ingested_at=INGESTED_AT)
        late = FixtureFilingSource(
            index=[_filing(PLAIN, "10-K", _at(2015, 3, 2)), _filing(PLAIN, "N-2", _at(2027, 1, 4))]
        )
        with pytest.raises(ValueError, match="ingested_at"):
            build_classifications(late, master, settings, ingested_at=INGESTED_AT)


class TestAsOf:
    def test_latest_row_as_of_t(self, store: duckdb.DuckDBPyConnection) -> None:
        security_id = primary_security_id(SPAC)
        before = classifications_as_of(store, _at(2020, 1, 1), [security_id])
        after = classifications_as_of(store, _at(2021, 3, 1), [security_id])
        assert before["security_type"].to_list() == ["spac"]
        assert after["security_type"].to_list() == ["common"]

    def test_nothing_before_the_first_row(self, store: duckdb.DuckDBPyConnection) -> None:
        early = classifications_as_of(store, _at(2011, 1, 1))
        assert early.is_empty()

    def test_bare_date_raises(self, store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            classifications_as_of(store, date(2020, 1, 1))  # type: ignore[arg-type]

    def test_naive_datetime_raises(self, store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(ValueError):
            classifications_as_of(store, datetime(2020, 1, 1))  # noqa: DTZ001


class TestNoLookAhead:
    def test_invariant_under_truncation(self, store: duckdb.DuckDBPyConnection) -> None:
        truncated = TruncatedStore(store, tables=("classifications",))
        try:
            for t in probe_timestamps(store, ("classifications",)):
                full = classifications_as_of(store, t)
                assert full.equals(classifications_as_of(truncated.at(t), t)), f"T={t!r}"
        finally:
            truncated.close()

    def test_build_from_source_known_at_t_agrees(self, store: duckdb.DuckDBPyConnection) -> None:
        source = _source()
        settings = _settings()
        probes = sorted(
            {k + d for k in source.known_ats() for d in (-PROBE_EPSILON, PROBE_EPSILON)}
        )
        for t in probes:
            partial_source = source.known_by(t)
            master = build_master(partial_source, settings, ingested_at=INGESTED_AT)
            partial = _new_store()
            write_master(partial, master)
            write_classifications(
                partial,
                build_classifications(partial_source, master, settings, ingested_at=INGESTED_AT),
            )
            full = classifications_as_of(store, t)
            assert full.equals(classifications_as_of(partial, t)), f"T={t!r}"
            partial.close()

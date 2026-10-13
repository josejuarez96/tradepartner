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

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import ClassVar

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
    EQUITY,
    ClassificationBuild,
    _title_type,
    build_classifications,
    classifications_as_of,
    is_placeholder_title,
    listing_kind,
    write_classifications,
)
from tradepartner.store.db import configure_connection
from tradepartner.store.master import MasterBuild, build_master, primary_security_id, write_master

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

    def test_header_without_sic_keeps_the_spac(self) -> None:
        source = FixtureFilingSource(
            index=[_filing(SPAC, "S-1", _at(2020, 1, 6)), _filing(SPAC, "8-K", _at(2020, 6, 1))],
            headers=[
                _header(SPAC, "S-1", 6770, _at(2020, 1, 6)),
                _header(SPAC, "8-K", None, _at(2020, 6, 1)),
            ],
            cover_pages=[_cover(SPAC, _at(2020, 3, 2), ("Class A Common Stock", "SPC", "NYSE"))],
        )
        latest = _latest(_build(source), primary_security_id(SPAC))
        assert (latest["security_type"], latest["sic"]) == ("spac", 6770)

    def test_the_call_site_reads_edgar_header_forms(self) -> None:
        """T11d: `_evidence` asks for `settings.edgar.header_forms`, not
        `settings.master.issuer_forms` (the pre-T11d call site)."""

        class Capturing(FixtureFilingSource):
            calls: ClassVar[list[tuple[str, tuple[str, ...]]]] = []

            def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
                type(self).calls.append((cik, tuple(forms)))
                return super().filing_headers(cik, forms)

        source = Capturing(index=[_filing(PLAIN, "10-K", _at(2015, 3, 2))])
        settings = _settings()
        master = build_master(source, settings, ingested_at=INGESTED_AT)
        build_classifications(source, master, settings, ingested_at=INGESTED_AT)
        assert Capturing.calls
        assert all(forms == tuple(settings.edgar.header_forms) for _, forms in Capturing.calls)
        assert tuple(settings.edgar.header_forms) != tuple(settings.master.issuer_forms)

    def test_an_override_of_edgar_header_forms_changes_which_headers_feed_sic(self) -> None:
        """With `edgar.header_forms` limited to `10-K`, the SPAC's S-1 header
        (SIC 6770, filed 2018-01-05) is never asked for, so rule 5 fires only
        once the 10-K header arrives (2019-03-01), later than the default
        `_source()` history (`test_sic_6770_is_spac_until_the_sic_changes`)."""
        limited = _settings(edgar={"header_forms": ["10-K"]})
        master = build_master(_source(), limited, ingested_at=INGESTED_AT)
        built = build_classifications(_source(), master, limited, ingested_at=INGESTED_AT)
        spac_rows = [r for r in _history(built, primary_security_id(SPAC)) if r[0] == "spac"]
        assert spac_rows and spac_rows[0][2] == _at(2019, 3, 1)

    def test_rule_5_fires_only_once_the_sic_is_known(self) -> None:
        """Rule 5 (sic_6770) reads only header evidence known at or before T:
        a header stamped after T never fires it early, and one stamped
        before T does."""
        source = FixtureFilingSource(
            # The security exists from 2019-03-01, so there is a "before" row
            # to test; the only SIC evidence is the 8-K header of 2020-01-06.
            index=[_filing(SPAC, "10-K", _at(2019, 3, 1)), _filing(SPAC, "8-K", _at(2020, 1, 6))],
            headers=[_header(SPAC, "8-K", 6770, _at(2020, 1, 6))],
        )
        history = _history(_build(source), primary_security_id(SPAC))
        before = [r for r in history if r[2] < _at(2020, 1, 6)]
        after = [r for r in history if r[2] >= _at(2020, 1, 6)]
        assert before, history  # not vacuous: a row exists before the SIC is known
        assert not any(r[0] == "spac" for r in before)
        assert after and after[0][0] == "spac" and after[0][2] == _at(2020, 1, 6)

    @pytest.mark.parametrize("form", ["10-KSB", "10-QSB", "10-K405"])
    def test_older_domestic_forms(self, form: str) -> None:
        source = FixtureFilingSource(
            index=[_filing(BARE, "8-K", _at(2004, 1, 5)), _filing(BARE, form, _at(2004, 3, 1))]
        )
        assert _latest(_build(source), primary_security_id(BARE))["rule"] == "common_default"

    def test_same_instant_status_forms_ordered_by_accession(self) -> None:
        stamp = _at(2016, 4, 1)
        source = FixtureFilingSource(
            index=[
                FilingIndexEntry(FOREIGN, "Co", "20-F", f"{FOREIGN}-000001", stamp),
                FilingIndexEntry(FOREIGN, "Co", "10-K", f"{FOREIGN}-000002", stamp),
            ]
        )
        assert _latest(_build(source), primary_security_id(FOREIGN))["rule"] == "common_default"

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


class TestFilingNameRows:
    """Rule 7 on a `filing_name` listing row (spec "Amendment 2026-10-12
    (#1597)", plan T184d): the row's ticker is read from a filing's own file
    names, never from a tagged fact, and that row alone names itself; every
    other provenance on the suffix row behaves as it did on main."""

    CIK = "0000000110"  # a pre-2019 filer with one 10-K and no cover page
    KNOWN_AT = datetime(2016, 3, 1, 21, 30, tzinfo=UTC)  # that 10-K's acceptance

    def _master(self, provenance: str) -> MasterBuild:
        """The master build a T184e write path would produce for the one
        untitled listing row (ticker `FNW-WS`, `class_title` NULL) carrying
        `provenance`, built by hand because no fixture source carries
        filename listings yet."""
        security_id = primary_security_id(self.CIK)
        return MasterBuild(
            securities=(
                {
                    "security_id": security_id,
                    "cik": self.CIK,
                    "name": "Filing Name Co",
                    "benchmark": False,
                    "known_at": _at(2015, 3, 2),
                    "ingested_at": INGESTED_AT,
                    "source": "edgar",
                    "provenance": "filing",
                },
            ),
            listings=(
                {
                    "security_id": security_id,
                    "ticker": "FNW-WS",
                    "exchange": "NYSE",
                    "class_title": None,
                    "valid_from": date(2016, 3, 1),
                    "known_at": self.KNOWN_AT,
                    "ingested_at": INGESTED_AT,
                    "source": "edgar",
                    "provenance": provenance,
                },
            ),
            unmatched_snapshot=(),
            missing_benchmarks=(),
        )

    def _built(self, provenance: str) -> ClassificationBuild:
        source = FixtureFilingSource(
            index=[_filing(self.CIK, "10-K", _at(2015, 3, 2))],
            headers=[_header(self.CIK, "10-K", 7372, _at(2015, 3, 2))],
        )
        return build_classifications(
            source, self._master(provenance), _settings(), ingested_at=INGESTED_AT
        )

    def test_a_filing_name_row_names_itself(self) -> None:
        # Before the 10-K the CIK is a domestic filer with no listing; from
        # the row's own `known_at` the suffix decides, as a snapshot ticker
        # does, and the row written names that listing's provenance.
        security_id = primary_security_id(self.CIK)
        assert _history(self._built("filing_name"), security_id) == [
            ("common", "common_default", _at(2015, 3, 2)),
            ("warrant", "suffix_warrant", self.KNOWN_AT),
        ]
        latest = _latest(self._built("filing_name"), security_id)
        assert latest["provenance"] == "filing_name"
        assert latest["known_at"] == self.KNOWN_AT

    @pytest.mark.parametrize("provenance", ["filing", "snapshot", "snapshot_static"])
    def test_every_other_provenance_keeps_main_s_row(self, provenance: str) -> None:
        """A suffix row over any other listing provenance is what main
        wrote: `snapshot_static`, at the same `known_at` (#1597 names only
        the `filing_name` row)."""
        latest = _latest(self._built(provenance), primary_security_id(self.CIK))
        assert (latest["security_type"], latest["rule"]) == ("warrant", "suffix_warrant")
        assert latest["provenance"] == "snapshot_static"
        assert latest["known_at"] == self.KNOWN_AT


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


# --- listing_kind: the price resolver's equity rule (#735) --------------------


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Common Stock, par value $0.01 per share", EQUITY),
        ("Class A ordinary shares included as part of the units", EQUITY),
        ("American Depositary Shares, each representing one ordinary share", EQUITY),
        ("Global Depositary Shares, each representing ten shares of Common Stock", EQUITY),
        ("Common Units Representing Limited Partner Interests", EQUITY),
        (
            "Depositary Units of Icahn Enterprises L.P. Representing Limited Partner Interests",
            EQUITY,
        ),
        ("Shares of beneficial interest without par value", EQUITY),
        ("N/A", EQUITY),
        ("0.650% Notes due May 2024", "coupon"),
        ("6.375% Series A Cumulative Redeemable", "coupon"),
        ("Floating Rate Notes due 2020", "debt"),
        ("Medium-Term Notes", "debt"),
        ("Preferred Stock Purchase Rights", "preferred"),
        ("Series A Preferred Units", "preferred"),
        ("Depositary Shares, each representing a 1/1,000th interest in a share", "preferred"),
        ("Warrants, each whole warrant exercisable for one Class A ordinary share", "warrant"),
        ("Redeemable warrants included as part of the units", "warrant"),
        ("Common Stock Purchase Warrants", "warrant"),
        ("Rights, each right entitling the holder to one-tenth of one share", "right"),
        ("Common Stock Purchase Rights", "right"),
        ("Units, each consisting of one Class A ordinary share and one right", "unit"),
        ("Units", "unit"),
        ("Common Shares (including Rights under Shareholder Rights Plan), no par value", EQUITY),
        ("Common Stock, $0.01 par value, Preferred Stock Purchase Rights, 8.875% Series B", EQUITY),
        ("Common Stock $0.0001 par value per share Preferred Share Purchase Rights", EQUITY),
        ("Common shares (including common share purchase rights)", EQUITY),
        ("Common Stock and associated Preferred Stock Purchase Rights", EQUITY),
        ("Class A Common Stock and one Redeemable Warrant", "warrant"),
        ("Depository Shares", "preferred"),
        ("6.75% Series C Cumulative Redeemable PreferredShares of Beneficial Interest", "coupon"),
        ("Preferred Stock, Par Value $25 Per Share, 4%, Noncumulative", "coupon"),
        ("Dep Shr, 1/1000th int. per shr of 5.85% Fix-to-Float Non-Cum. Perpetual", "coupon"),
        ("Guarantee of Aon plc, 3.500% Senior Notes due 2024", "coupon"),
        ("Class A Common Stock and one Right", "right"),
        ("Ordinary Shares and one Right to receive one-tenth of a share", "right"),
        ("Common Stock and Warrants to purchase Common Stock and Rights", "warrant"),
        ("Common stock and contingent value rights", "right"),
    ],
)
def test_listing_kind_reads_the_class_title(title: str, kind: str) -> None:
    assert listing_kind("ANY", title) == kind


@pytest.mark.parametrize(
    ("ticker", "kind"),
    [
        ("ACME", EQUITY),
        ("ACME.WS", "warrant"),
        ("ACMEU", "unit"),
        ("ACME-P", "preferred"),
        ("ACMEY", EQUITY),
        ("BRK.B", EQUITY),
    ],
)
def test_an_untitled_listing_takes_its_ticker_suffix(ticker: str, kind: str) -> None:
    assert listing_kind(ticker, None) == kind
    assert listing_kind(ticker, " ") == kind


# --- T179 (#1541): the title fallback ----------------------------------------

# Real titles the spec amendment quotes, as `(title, off, on)` where each side is
# `(security_type, rule)` of `_title_type` with the switch off and on.
_UNREC = ("unclassifiable", "title_unrecognized")
_COMMON = ("common", "title_common")
_TITLE_CASES = [
    ("ShockWave Medical Inc., common stock, par value $0.001 per share", _UNREC, _COMMON),
    ("HD Supply Holdings, Inc. common stock, par value $0.01 per share", _UNREC, _COMMON),
    ("BioLife Solutions, Inc. Common Shares", _UNREC, _COMMON),
    ("Class A Shares", _UNREC, _COMMON),
    ("United Therapeutics Corporation, Common Stock", ("unit", "title_unit"), _COMMON),
    (
        "Depository Shares, each representing 1/1000th of a share of Preferred Stock",
        _UNREC,
        ("preferred", "title_preferred"),
    ),
    ("Depository Shares", _UNREC, ("depositary", "title_depositary")),
    ("Acme Corp., 5.5% Notes due 2030", _UNREC, _UNREC),
    # a `%` title with "Common Stock" after the comma is no common
    ("Acme Corp 6% Debt, convertible into Common Stock", _UNREC, _UNREC),
    ("Shares of Beneficial Interest", _UNREC, _UNREC),
    ("Commorn Stock", _UNREC, _UNREC),
    # class-letter shares with a non-equity word anywhere stay out
    ("Class A Shares, representing limited partner interests", _UNREC, _UNREC),
    ("Class A Shares, 6% Trust Preferred", _UNREC, _UNREC),
    ("Class A Shares, Trust Preferred", _UNREC, ("preferred", "title_preferred")),
    ("Class A Shares, beneficial interest in the Trust", _UNREC, _UNREC),
    # unchanged either way
    ("Common Stock, par value $0.01", _COMMON, _COMMON),
    ("Class A Common Stock", _COMMON, _COMMON),
    (
        "6.5% Series A Preferred Stock",
        ("preferred", "title_preferred"),
        ("preferred", "title_preferred"),
    ),
]


@pytest.mark.parametrize(("title", "off", "on"), _TITLE_CASES)
def test_title_type_with_the_fallback_off_and_on(
    title: str, off: tuple[str, str], on: tuple[str, str]
) -> None:
    assert _title_type(title) == off
    assert _title_type(title, True) == on


@pytest.mark.parametrize(
    ("title", "placeholder"),
    [
        (None, True),
        ("", True),
        ("  ", True),
        ("NONE", True),
        ("N/A", True),
        ("NA", True),
        ("NO", True),
        ("NONW", True),
        ("0.01", True),
        ("None, par value", True),
        ("Common Stock", False),
        ("Nonesuch Common Stock", False),
        ("Class A Shares", False),
    ],
)
def test_placeholder_titles(title: str | None, placeholder: bool) -> None:
    assert is_placeholder_title(title) is placeholder


FB = "0000000201"  # a placeholder title with a warrant ticker, then common
FB_LATER = "0000000202"  # a real title, then a placeholder cover page
FB_NOTE = "0000000203"  # a placeholder-titled note beside a titled common
FB_ONLY = "0000000204"  # only placeholder titles
FB_TIME = "0000000205"  # placeholder-titled class, common named by a later page


def _fallback_source() -> FixtureFilingSource:
    return FixtureFilingSource(
        index=[_filing(cik, "10-K", _at(2018, 3, 1)) for cik in (FB, FB_LATER, FB_NOTE, FB_ONLY)]
        + [_filing(FB_TIME, "10-K", _at(2018, 3, 1))],
        cover_pages=[
            _cover(FB, _at(2019, 3, 1), ("NONE", "FB-WS", "NYSE")),
            _cover(FB_LATER, _at(2019, 3, 1), ("Common Stock", "FBL", "NYSE")),
            _cover(FB_LATER, _at(2020, 3, 1), ("N/A", "FBL", "NASDAQ")),
            _cover(
                FB_NOTE,
                _at(2019, 3, 1),
                ("Common Stock", "FBN", "NYSE"),
                ("NONE", "FBN1", "NYSE"),
            ),
            _cover(FB_ONLY, _at(2019, 3, 1), ("N/A", "FBO", "NYSE")),
            _cover(FB_TIME, _at(2019, 3, 1), ("NONE", "FBT1", "NYSE")),
            _cover(
                FB_TIME,
                _at(2021, 3, 1),
                ("NONE", "FBT1", "NYSE"),
                ("Common Stock", "FBT", "NYSE"),
            ),
        ],
    )


def _fallback_build(
    on: bool, source: FixtureFilingSource | None = None
) -> tuple[ClassificationBuild, MasterBuild]:
    source = source or _fallback_source()
    settings = _settings(edgar={"title_fallback": on})
    master = build_master(source, settings, ingested_at=INGESTED_AT)
    return build_classifications(source, master, settings, ingested_at=INGESTED_AT), master


def _latest_type(built: ClassificationBuild, security_id: str) -> tuple[str, str]:
    row = _latest(built, security_id)
    return (str(row["security_type"]), str(row["rule"]))


def _ids(master: MasterBuild, cik: str) -> list[str]:
    return sorted(r["security_id"] for r in master.securities if r["cik"] == cik)


class TestTitleFallback:
    def test_a_placeholder_title_with_a_warrant_ticker_is_a_warrant_by_rule_7(self) -> None:
        source = _fallback_source()
        on, master = _fallback_build(True, source)
        off, _ = _fallback_build(False, source)
        (sid,) = _ids(master, FB)
        assert _latest_type(on, sid) == ("warrant", "suffix_warrant")
        assert _latest_type(off, sid) == ("unclassifiable", "title_unrecognized")

    def test_a_placeholder_class_with_no_real_title_defaults_to_common(self) -> None:
        on, master = _fallback_build(True)
        off, _ = _fallback_build(False)
        (sid,) = _ids(master, FB_ONLY)
        assert _latest_type(on, sid) == ("common", "common_default")
        assert _latest_type(off, sid) == ("unclassifiable", "title_unrecognized")

    def test_a_later_placeholder_never_masks_the_real_title(self) -> None:
        on, master = _fallback_build(True)
        off, _ = _fallback_build(False)
        (sid,) = _ids(master, FB_LATER)
        assert _history(on, sid)[-1][:2] == ("common", "title_common")
        assert _latest_type(off, sid) == ("unclassifiable", "title_unrecognized")

    def test_a_placeholder_note_beside_a_titled_common_stays_unclassifiable(self) -> None:
        on, master = _fallback_build(True)
        off, _ = _fallback_build(False)
        ids = _ids(master, FB_NOTE)
        assert len(ids) == 2
        assert sorted(_latest_type(on, sid) for sid in ids) == [
            ("common", "title_common"),
            ("unclassifiable", "no_rule_matched"),
        ]
        assert ("unclassifiable", "title_unrecognized") in {_latest_type(off, s) for s in ids}

    def test_a_sibling_title_arriving_later_stops_rule_8_from_then(self) -> None:
        on, master = _fallback_build(True)
        ids = _ids(master, FB_TIME)
        assert len(ids) == 2
        placeholder = next(s for s in ids if _history(on, s)[0][1] == "common_default")
        history = _history(on, placeholder)
        assert history[0] == ("common", "common_default", history[0][2])
        assert history[-1][:2] == ("unclassifiable", "no_rule_matched")
        assert history[-1][2] == _at(2021, 3, 1)

    def test_switch_off_is_the_same_as_the_default(self) -> None:
        explicit, _ = _fallback_build(False)
        source = _fallback_source()
        settings = _settings()
        master = build_master(source, settings, ingested_at=INGESTED_AT)
        default = build_classifications(source, master, settings, ingested_at=INGESTED_AT)
        assert explicit.classifications == default.classifications

    def test_the_fixture_universe_is_unchanged_with_the_switch_off(self) -> None:
        source = _source()
        off = _settings(edgar={"title_fallback": False})
        master = build_master(source, off, ingested_at=INGESTED_AT)
        assert (
            build_classifications(source, master, off, ingested_at=INGESTED_AT).classifications
            == _build().classifications
        )


SHOCK = "0000000301"


def test_shares_of_a_shockwave_shaped_issuer_continue_after_its_titled_listing() -> None:
    """Shares facts are keyed by class member, never by title: a 10-K filer
    whose first cover page carries an issuer-name-first title is common again
    from that page under the fallback, and its shares facts land before and
    after it (off, the facts after the page are unmatched)."""
    from tradepartner.adapters.filings import FactRecord
    from tradepartner.ingest import fact_rows

    source = FixtureFilingSource(
        index=[_filing(SHOCK, "10-K", _at(2018, 3, 1))],
        cover_pages=[
            _cover(
                SHOCK,
                _at(2019, 3, 1),
                (
                    "ShockWave Medical Inc., common stock, par value $0.001 per share",
                    "SWAV",
                    "NASDAQ",
                ),
            )
        ],
    )
    records = [
        FactRecord(
            SHOCK,
            "EntityCommonStockSharesOutstanding",
            date(2018, 2, 15),
            "",
            1.0,
            "a1",
            _at(2018, 3, 1),
        ),
        FactRecord(
            SHOCK,
            "EntityCommonStockSharesOutstanding",
            date(2019, 2, 15),
            "",
            2.0,
            "a2",
            _at(2019, 3, 1, 21),
        ),
        FactRecord(
            SHOCK,
            "EntityCommonStockSharesOutstanding",
            date(2020, 2, 15),
            "",
            3.0,
            "a3",
            _at(2020, 3, 1),
        ),
    ]
    landed = {}
    for on in (False, True):
        settings = _settings(edgar={"title_fallback": on})
        master = build_master(source, settings, ingested_at=INGESTED_AT)
        classes = build_classifications(source, master, settings, ingested_at=INGESTED_AT)
        rows, unmatched = fact_rows(records, master, classes, ingested_at=INGESTED_AT)
        landed[on] = ([r["value"] for r in rows], [u.accession for u in unmatched])
    assert landed[False] == ([1.0], ["a2", "a3"])
    assert landed[True] == ([1.0, 2.0, 3.0], [])

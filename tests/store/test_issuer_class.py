"""Issuer-class tags and the pre-SIC SPAC-shell rule (#3076, #3053 item 6).

Each `classifications` row carries an `issuer_class`: `spac`, `bdc`, `mlp`,
`foreign` or `None`, read from the issuer's filing evidence known at the row's
`known_at` (module docstring of `store.classify`, "Issuer class"). And a
domestic class is `common` only once its issuer's filings name a SIC or it
has filed a periodic report, so a blank check whose first S-1 carries no SIC
yet is never a stock before SIC 6770 shows (#3053 item 6, decision b).

Scenarios use `FixtureFilingSource`, as in `test_classify.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb

from tradepartner.adapters.filings import (
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
from tradepartner.store.master import build_master, primary_security_id

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

SHELL = "0000000201"  # S-1 without a SIC, then an 8-K header with SIC 6770
IPO = "0000000202"  # an operating company's S-1 with its SIC
TRUST = "0000000203"  # a royalty trust: 10-Ks, never a header SIC
BDC = "0000000204"  # 10-K filer that elects BDC status (N-54A), later withdraws
BDC2 = "0000000205"  # 10-K and N-2 filer with no N-54A on EDGAR
CEF = "0000000206"  # a closed-end fund: N-2 and N-CSR, no 10-K
MLP = "0000000207"  # a partnership that converts to a corporation
UNITS = "0000000208"  # a partnership known by its "Common Units" title only
FPI = "0000000209"  # a 20-F filer that later files a 10-K

_counter = iter(range(1, 1_000_000))


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 20, 30, tzinfo=UTC)


def _accession(cik: str) -> str:
    return f"{cik}-{next(_counter):06d}"


def _filing(cik: str, form: str, accepted_at: datetime, name: str = "Co Inc") -> FilingIndexEntry:
    return FilingIndexEntry(cik, name, form, _accession(cik), accepted_at)


def _header(cik: str, form: str, sic: int | None, accepted_at: datetime) -> FilingHeader:
    return FilingHeader(cik, _accession(cik), form, sic, accepted_at)


def _cover(cik: str, accepted_at: datetime, title: str, ticker: str) -> CoverPage:
    return CoverPage(cik, _accession(cik), accepted_at, (CoverListing(title, ticker, "NYSE"),))


def _source() -> FixtureFilingSource:
    return FixtureFilingSource(
        index=[
            _filing(SHELL, "S-1", _at(2021, 3, 1), "NewCo Acquisition Corp"),
            _filing(SHELL, "8-K", _at(2021, 6, 1), "NewCo Acquisition Corp"),
            _filing(SHELL, "10-Q", _at(2021, 8, 1), "NewCo Acquisition Corp"),
            _filing(IPO, "S-1", _at(2021, 3, 1)),
            _filing(TRUST, "10-K", _at(2016, 3, 1), "Basin Royalty Trust"),
            _filing(BDC, "10-K", _at(2016, 3, 1), "Lending Corp"),
            _filing(BDC, "N-54A", _at(2017, 5, 1), "Lending Corp"),
            _filing(BDC, "N-54C", _at(2020, 5, 1), "Lending Corp"),
            _filing(BDC2, "10-K", _at(2016, 3, 1), "Capital Corp"),
            _filing(BDC2, "N-2", _at(2018, 5, 1), "Capital Corp"),
            _filing(CEF, "N-2", _at(2016, 3, 1), "Income Fund"),
            _filing(CEF, "N-CSR", _at(2017, 3, 1), "Income Fund"),
            _filing(MLP, "10-K", _at(2016, 3, 1), "Midstream Partners L.P."),
            _filing(MLP, "10-K", _at(2019, 3, 1), "Midstream Partners L.P."),
            _filing(MLP, "10-K", _at(2020, 3, 1), "Midstream Inc"),
            _filing(UNITS, "10-K", _at(2016, 3, 1), "Pipeline Co"),
            _filing(FPI, "20-F", _at(2016, 4, 1), "Overseas Ltd"),
            _filing(FPI, "10-K", _at(2022, 3, 1), "Overseas Ltd"),
        ],
        headers=[
            _header(SHELL, "S-1", None, _at(2021, 3, 1)),
            _header(SHELL, "8-K", 6770, _at(2021, 6, 1)),
            _header(SHELL, "10-Q", 6770, _at(2021, 8, 1)),
            _header(IPO, "S-1", 7372, _at(2021, 3, 1)),
            _header(BDC, "10-K", 6799, _at(2016, 3, 1)),
            _header(BDC2, "10-K", 6799, _at(2016, 3, 1)),
            _header(MLP, "10-K", 4922, _at(2016, 3, 1)),
            _header(UNITS, "10-K", 4922, _at(2016, 3, 1)),
        ],
        cover_pages=[
            _cover(
                UNITS,
                _at(2019, 3, 4),
                "Common Units representing limited partner interests",
                "PPL",
            ),
        ],
    )


def _build(source: FixtureFilingSource | None = None) -> ClassificationBuild:
    source = source or _source()
    settings = Settings(_env_file=None)
    master = build_master(source, settings, ingested_at=INGESTED_AT)
    return build_classifications(source, master, settings, ingested_at=INGESTED_AT)


def _history(built: ClassificationBuild, cik: str) -> list[tuple[str, str, str | None, datetime]]:
    """(security_type, rule, issuer_class, known_at) of the CIK's primary class."""
    sid = primary_security_id(cik)
    return [
        (r["security_type"], r["rule"], r["issuer_class"], r["known_at"])
        for r in built.classifications
        if r["security_id"] == sid
    ]


def _at_t(built: ClassificationBuild, cik: str, t: datetime) -> tuple[str, str | None]:
    """(security_type, issuer_class) of the CIK's primary class as known at `t`."""
    sid = primary_security_id(cik)
    rows = [r for r in built.classifications if r["security_id"] == sid and r["known_at"] <= t]
    assert rows, f"{cik}: nothing known at {t}"
    latest = max(rows, key=lambda r: r["known_at"])
    return latest["security_type"], latest["issuer_class"]


class TestSpacShell:
    def test_a_shell_is_no_stock_before_its_sic_shows(self) -> None:
        """Decision (b): before SIC 6770 is known, a class whose issuer has filed
        no periodic report is not `common`."""
        assert _history(_build(), SHELL) == [
            ("unclassifiable", "no_sic_or_periodic", None, _at(2021, 3, 1)),
            ("spac", "sic_6770", "spac", _at(2021, 6, 1)),
        ]

    def test_an_ipo_with_its_sic_is_common_at_once(self) -> None:
        assert _history(_build(), IPO) == [("common", "common_default", None, _at(2021, 3, 1))]

    def test_a_periodic_filer_with_no_header_sic_stays_common(self) -> None:
        """A royalty trust filing 10-Ks whose headers never name a SIC keeps
        today's `common_default`: the rule only waits for the first SIC or
        periodic report."""
        assert _history(_build(), TRUST) == [("common", "common_default", None, _at(2016, 3, 1))]


class TestBdc:
    def test_an_n54a_election_tags_bdc_from_its_acceptance_until_withdrawn(self) -> None:
        assert _history(_build(), BDC) == [
            ("common", "common_default", None, _at(2016, 3, 1)),
            ("common", "common_default", "bdc", _at(2017, 5, 1)),
            ("common", "common_default", None, _at(2020, 5, 1)),
        ]

    def test_an_n2_filer_on_domestic_forms_is_a_bdc(self) -> None:
        """No N-54A on EDGAR (an election filed before 1996): an N-2 filer that
        reports on 10-K and never on N-CSR is a BDC; its type stays `fund`."""
        assert _history(_build(), BDC2) == [
            ("common", "common_default", None, _at(2016, 3, 1)),
            ("fund", "fund_form", "bdc", _at(2018, 5, 1)),
        ]

    def test_a_closed_end_fund_is_no_bdc(self) -> None:
        assert [row[2] for row in _history(_build(), CEF)] == [None] * len(_history(_build(), CEF))


class TestMlp:
    def test_a_partnership_name_tags_mlp_until_the_conversion(self) -> None:
        """The tag follows the name on each filing: the 2020 10-K under a
        corporate name clears it, and it never reaches back before 2016's."""
        assert _history(_build(), MLP) == [
            ("common", "common_default", "mlp", _at(2016, 3, 1)),
            ("common", "common_default", None, _at(2020, 3, 1)),
        ]

    def test_a_common_units_title_tags_mlp_from_its_cover_page(self) -> None:
        built = _build()
        assert _at_t(built, UNITS, _at(2019, 3, 1)) == ("common", None)
        assert _at_t(built, UNITS, _at(2019, 3, 5)) == ("unit", "mlp")


class TestForeign:
    def test_a_20f_filer_is_foreign_until_its_first_10k(self) -> None:
        assert [(r[0], r[2], r[3]) for r in _history(_build(), FPI)] == [
            ("foreign", "foreign", _at(2016, 4, 1)),
            ("common", None, _at(2022, 3, 1)),
        ]


class TestNoLookAhead:
    """A tag known only from a later filing never applies earlier: the build
    from a source truncated at T agrees, at T, with the build from the full
    source, for every CIK and every evidence instant (#3076)."""

    def _truncated(self, t: datetime) -> FixtureFilingSource:
        full = _source()
        return FixtureFilingSource(
            index=[e for e in full.filing_index() if e.accepted_at <= t],
            headers=[h for h in full._headers if h.accepted_at <= t],
            cover_pages=[c for c in full._cover_pages if c.accepted_at <= t],
        )

    def test_truncated_and_full_builds_agree_at_every_instant(self) -> None:
        full = _build()
        instants = sorted({r["known_at"] for r in full.classifications})
        for t in instants:
            for probe in (t - timedelta(seconds=1), t):
                truncated = _build(self._truncated(probe))
                for cik in (SHELL, IPO, TRUST, BDC, BDC2, CEF, MLP, UNITS, FPI):
                    sid = primary_security_id(cik)
                    have = [
                        r
                        for r in truncated.classifications
                        if r["security_id"] == sid and r["known_at"] <= probe
                    ]
                    want = [
                        r
                        for r in full.classifications
                        if r["security_id"] == sid and r["known_at"] <= probe
                    ]
                    strip = ("ingested_at",)
                    assert [{k: v for k, v in r.items() if k not in strip} for r in have] == [
                        {k: v for k, v in r.items() if k not in strip} for r in want
                    ], (cik, probe)


class TestStore:
    def test_the_tag_is_written_and_read_back_as_of_t(self) -> None:
        conn = duckdb.connect(":memory:")
        configure_connection(conn)
        schema.init_schema(conn)
        write_classifications(conn, _build())
        sid = primary_security_id(BDC)
        before = classifications_as_of(conn, _at(2017, 4, 30), [sid])
        after = classifications_as_of(conn, _at(2017, 5, 2), [sid])
        assert before["issuer_class"].to_list() == [None]
        assert after["issuer_class"].to_list() == ["bdc"]

    def test_version_47_store_migrates_with_a_null_tag(self) -> None:
        conn = duckdb.connect(":memory:")
        configure_connection(conn)
        schema.init_schema(conn)
        conn.execute("ALTER TABLE classifications DROP COLUMN issuer_class")
        conn.execute("DELETE FROM schema_version WHERE version = 48")
        conn.execute(
            "INSERT INTO classifications (security_id, sic, security_type, rule, known_at, "
            "ingested_at, source, provenance) VALUES ('X', 7372, 'common', 'title_common', "
            "?, ?, 'edgar', 'filing')",
            [_at(2020, 1, 2), INGESTED_AT],
        )
        schema.init_schema(conn)
        assert conn.execute("SELECT issuer_class FROM classifications").fetchall() == [(None,)]
        assert schema.CURRENT_SCHEMA_VERSION == schema._PRE_RETRACTED_ROWS_VERSION + 1

    def test_an_incremental_ingest_stamps_a_new_tag_at_its_run_never_earlier(self) -> None:
        """Without `--rebuild-classifications`, a tag that changes behind stored
        history gets one restatement at the run's `ingested_at`: the tag is a
        value column (`ingest._TABLES`), and no stored history is back-dated."""
        from tradepartner import ingest

        conn = duckdb.connect(":memory:")
        configure_connection(conn)
        schema.init_schema(conn)
        first = {
            "security_id": "X",
            "sic": 6799,
            "security_type": "common",
            "rule": "common_default",
            "issuer_class": None,
            "known_at": _at(2016, 3, 1),
            "ingested_at": _at(2018, 3, 2),
            "source": "edgar",
            "provenance": "filing",
        }
        titled = {**first, "rule": "title_common", "known_at": _at(2018, 3, 1)}
        ingest._add_rows(
            conn, "classifications", [first, titled], ingested_at=_at(2018, 3, 2), current=False
        )
        run = _at(2026, 11, 6)
        built = [
            {**row, "ingested_at": run}
            for row in (
                first,
                {**first, "issuer_class": "bdc", "known_at": _at(2017, 5, 1)},
                {**titled, "issuer_class": "bdc"},
            )
        ]
        assert ingest._add_rows(conn, "classifications", built, ingested_at=run, current=False) == 1
        assert ingest._add_rows(conn, "classifications", built, ingested_at=run, current=False) == 0
        assert conn.execute(
            "SELECT issuer_class, known_at FROM classifications ORDER BY known_at"
        ).fetchall() == [(None, _at(2016, 3, 1)), (None, _at(2018, 3, 1)), ("bdc", run)]

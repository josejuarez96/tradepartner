"""Tests for tradepartner.adapters.edgar (T11): parsers over T3's recorded
EDGAR payloads (`tests/fixtures/edgar/`).

Plan T11: provenance and `known_at` per the spec's master table. Filing
records carry the SEC acceptance instant (`accepted_at`), taken from the
submissions payload (UTC) or the SGML header (Eastern), never from a
filing *date*; snapshot records carry the recorded fetch time.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.edgar import (
    acceptance_times,
    normalize_class_member,
    normalize_exchange,
    parse_company_facts,
    parse_company_tickers,
    parse_cover_page,
    parse_delisting,
    parse_filing_index,
    parse_sgml_header,
    parse_submissions,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
RECORDED_AT = json.loads((FIXTURES.parent / "recorded_at.json").read_text())

APPLE = "0000320193"
ALPHABET = "0001652044"
KLX = "0001738827"
SHARES = "EntityCommonStockSharesOutstanding"


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _gz(name: str) -> bytes:
    with gzip.open(FIXTURES / name, "rb") as handle:
        return handle.read()


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


@pytest.fixture(scope="module")
def acceptance() -> dict[str, datetime]:
    return acceptance_times(
        _json("submissions_plain_issuer.json"),
        _json("submissions_plain_issuer_001.json"),
        _json("submissions_dual_class.json"),
        _json("submissions_dual_class_001.json"),
        _json("submissions_delisted_25nse.json"),
    )


class TestExchange:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("NASDAQ", "NASDAQ"),
            ("Nasdaq", "NASDAQ"),
            ("Nasdaq Stock Market LLC", "NASDAQ"),
            ("NYSE", "NYSE"),
            ("New York Stock Exchange LLC", "NYSE"),
            ("NYSEAMER", "NYSE_AMERICAN"),
            ("NYSE American LLC", "NYSE_AMERICAN"),
            ("NYSE MKT LLC", "NYSE_AMERICAN"),
            ("American Stock Exchange", "NYSE_AMERICAN"),
            ("NYSE Alternext US LLC", "NYSE_AMERICAN"),
            ("NYSEArca", "NYSE_ARCA"),
            ("NYSE Arca, Inc.", "NYSE_ARCA"),
            # Other venues never collapse onto NYSE or NASDAQ (review findings).
            ("NYSENAT", "NYSE_NATIONAL"),
            ("NYSE National, Inc.", "NYSE_NATIONAL"),
            ("CHX", "NYSE_CHICAGO"),
            ("NYSE Chicago, Inc.", "NYSE_CHICAGO"),
            ("NYSETEXAS", "NYSETEXAS"),
            ("Phlx", "NASDAQ_PHLX"),
            ("Nasdaq PHLX LLC", "NASDAQ_PHLX"),
            ("BX", "NASDAQ_BX"),
            ("NASDAQ OMX BX, Inc.", "NASDAQ_BX"),
            ("CboeBZX", "CBOE_BZX"),
            ("Cboe BZX Exchange, Inc.", "CBOE_BZX"),
            ("CBOE", "CBOE"),
            ("OTC", "OTC"),
            ("Some Other Venue", "SOME_OTHER_VENUE"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_exchange(raw) == expected


class TestAcceptance:
    def test_submissions_times_are_utc(self, acceptance: dict[str, datetime]) -> None:
        # Apple's 10-Q was accepted 18:03 ET on 2024-02-01 and dated 2024-02-02.
        assert acceptance["0000320193-24-000006"] == datetime(2024, 2, 1, 23, 3, 38, tzinfo=UTC)

    def test_older_pages_are_read(self, acceptance: dict[str, datetime]) -> None:
        page = _json("submissions_dual_class_001.json")
        assert acceptance[page["accessionNumber"][0]] == _utc(page["acceptanceDateTime"][0])

    def test_submissions_entries(self) -> None:
        payload = _json("submissions_delisted_25nse.json")
        entries = parse_submissions(payload)
        first = entries[-1]
        assert first.cik == KLX
        assert first.company_name == payload["name"]
        assert [e.accepted_at for e in entries] == sorted(e.accepted_at for e in entries)
        nse = next(e for e in entries if e.accession == "0001354457-26-000904")
        assert (nse.form, nse.accepted_at) == (
            "25-NSE",
            datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC),
        )

    def test_submissions_with_pages(self) -> None:
        recent = parse_submissions(_json("submissions_dual_class.json"))
        full = parse_submissions(
            _json("submissions_dual_class.json"), [_json("submissions_dual_class_001.json")]
        )
        assert len(full) == len(recent) + len(_json("submissions_dual_class_001.json")["form"])


class TestFilingIndex:
    def test_entries_take_acceptance_from_submissions(
        self, acceptance: dict[str, datetime]
    ) -> None:
        parsed = parse_filing_index(_text("filing_index_2024_qtr1.txt"), acceptance)
        tenk = next(e for e in parsed.entries if e.accession == "0001652044-24-000022")
        assert (tenk.cik, tenk.form, tenk.company_name) == (ALPHABET, "10-K", "Alphabet Inc.")
        # Accepted 21:43 ET on 2024-01-30; the index dates it 2024-01-31.
        assert tenk.accepted_at == datetime(2024, 1, 31, 2, 43, 43, tzinfo=UTC)

    def test_rows_without_an_acceptance_time_are_not_stamped(
        self, acceptance: dict[str, datetime]
    ) -> None:
        parsed = parse_filing_index(_text("filing_index_2024_qtr1.txt"), acceptance)
        stamped = {e.accession for e in parsed.entries}
        unstamped = {row.accession for row in parsed.unstamped}
        assert stamped and unstamped
        assert not stamped & unstamped
        assert len(stamped | unstamped) == _text("filing_index_2024_qtr1.txt").count("edgar/data/")
        row = next(r for r in parsed.unstamped if r.accession == "0001683168-24-000531")
        assert (row.cik, row.form, row.filed_on) == ("0001133116", "1-A", date(2024, 1, 30))

    def test_blank_company_name_row_parses_with_an_empty_name(self) -> None:
        # The owner's first real backfill hit this 1997 row in a quarterly index (#358).
        blank = (
            "SC 13D" + " " * 75 + "1036125     1997-03-24  "
            "edgar/data/1036125/0000950134-97-002093.txt\n"
        )
        parsed = parse_filing_index(_text("filing_index_2024_qtr1.txt") + blank, {})
        row = next(r for r in parsed.unstamped if r.accession == "0000950134-97-002093")
        assert (row.cik, row.company_name, row.form, row.filed_on) == (
            "0001036125",
            "",
            "SC 13D",
            date(1997, 3, 24),
        )
        # a normal row still keeps its name whole
        named = next(r for r in parsed.unstamped if r.accession != "0000950134-97-002093")
        assert named.company_name and not named.company_name[0].isspace()
        # a one-character form followed by a name never loses a CIK digit to the name
        four = next(r for r in parsed.unstamped if r.form == "4")
        assert four.cik == "0001652044" and four.company_name == "Alphabet Inc."

    def test_malformed_data_row_raises(self) -> None:
        text = _text("filing_index_2024_qtr1.txt") + "10-K garbled edgar/data/1/x.txt\n"
        with pytest.raises(ValueError, match="does not parse"):
            parse_filing_index(text, {})

    def test_form_and_name_separated_by_one_space_still_raises(self) -> None:
        # the blank-name pattern must not swallow a mis-columned row as a long form (#358)
        row = (
            "10-K Acme Corp      1652044     2024-01-31  "
            "edgar/data/1652044/0001652044-24-000099.txt\n"
        )
        with pytest.raises(ValueError, match="does not parse"):
            parse_filing_index(_text("filing_index_2024_qtr1.txt") + row, {})
        # a 16-character form, one space and a short name padded to its column (safety-reviewer)
        wide = (
            "SEC STAFF ACTION Acme Corp" + " " * 53 + "1652044     2024-01-31  "
            "edgar/data/1652044/0001652044-24-000098.txt\n"
        )
        with pytest.raises(ValueError, match="does not parse"):
            parse_filing_index(_text("filing_index_2024_qtr1.txt") + wide, {})

    def test_every_row_is_parsed(self) -> None:
        parsed = parse_filing_index(_text("filing_index_2024_qtr1.txt"), {})
        assert not parsed.entries
        forms = {row.form for row in parsed.unstamped}
        assert {"10-K", "10-Q", "25-NSE", "144"} <= forms


class TestCompanyTickers:
    def test_snapshot_entries(self) -> None:
        fetched_at = _utc(RECORDED_AT["edgar/company_tickers.json"])
        entries = parse_company_tickers(_json("company_tickers.json"), fetched_at)
        apple = next(e for e in entries if e.ticker == "AAPL")
        assert (apple.cik, apple.name, apple.exchange) == (APPLE, "Apple Inc.", "NASDAQ")
        assert all(e.fetched_at == fetched_at for e in entries)
        assert len(entries) == len(_json("company_tickers.json")["data"])

    def test_naive_fetch_time_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_company_tickers(_json("company_tickers.json"), datetime(2026, 9, 25))  # noqa: DTZ001


class TestCompanyFacts:
    def test_facts_stamped_from_acceptance(self, acceptance: dict[str, datetime]) -> None:
        parsed = parse_company_facts(_json("company_facts_plain_issuer.json"), [SHARES], acceptance)
        raw = _json("company_facts_plain_issuer.json")["facts"]["dei"][SHARES]["units"]["shares"]
        assert len(parsed.facts) == len(raw) and not parsed.unstamped
        first = next(f for f in parsed.facts if f.accession == raw[0]["accn"])
        assert first.cik == APPLE
        assert first.fact_name == SHARES
        assert first.as_of_date == date.fromisoformat(raw[0]["end"])
        assert first.class_member == ""
        assert first.value == raw[0]["val"]
        assert first.accepted_at == acceptance[raw[0]["accn"]]

    def test_fact_without_acceptance_is_not_stamped_from_filed(self) -> None:
        payload = _json("company_facts_plain_issuer.json")
        parsed = parse_company_facts(payload, [SHARES], {})
        assert not parsed.facts
        assert len(parsed.unstamped) == len(payload["facts"]["dei"][SHARES]["units"]["shares"])

    def test_other_names_are_ignored(self, acceptance: dict[str, datetime]) -> None:
        parsed = parse_company_facts(_json("company_facts_plain_issuer.json"), [], acceptance)
        assert not parsed.facts and not parsed.unstamped

    def test_us_gaap_names_are_read(self, acceptance: dict[str, datetime]) -> None:
        parsed = parse_company_facts(
            _json("company_facts_plain_issuer.json"), ["CommonStockSharesOutstanding"], acceptance
        )
        assert parsed.facts
        assert {f.fact_name for f in parsed.facts} == {"CommonStockSharesOutstanding"}


class TestSgmlHeader:
    def test_filer_header(self, acceptance: dict[str, datetime]) -> None:
        header = parse_sgml_header(_text("sgml_header_plain_issuer.txt"))
        assert (header.cik, header.form, header.sic) == (APPLE, "10-K", 3571)
        assert header.accession == "0000320193-25-000079"
        # 06:01:26 Eastern (EDT) is 10:01:26 UTC, as the submissions payload says.
        assert header.accepted_at == datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC)
        assert header.accepted_at == acceptance[header.accession]

    def test_subject_company_header(self, acceptance: dict[str, datetime]) -> None:
        # A 25-NSE is filed by the exchange; the issuer is the subject company.
        header = parse_sgml_header(_text("sgml_header_delisted_25nse.txt"))
        assert (header.cik, header.form, header.sic) == (KLX, "25-NSE", 1389)
        assert header.accepted_at == acceptance[header.accession]

    def test_winter_time_is_est(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "<ACCEPTANCE-DATETIME>20251031060126", "<ACCEPTANCE-DATETIME>20240115163000"
        )
        assert parse_sgml_header(text).accepted_at == datetime(2024, 1, 15, 21, 30, tzinfo=UTC)

    def test_missing_sic_is_none(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tELECTRONIC COMPUTERS [3571]\n", ""
        )
        assert parse_sgml_header(text).sic is None

    def test_document_text_after_the_header_is_ignored(self) -> None:
        # The fetched range runs into the filer's documents; a forged block
        # there must not replace the header's issuer.
        text = _text("sgml_header_plain_issuer.txt") + (
            "\nSUBJECT COMPANY:\n\tCENTRAL INDEX KEY:\t0000999999\n"
        )
        assert parse_sgml_header(text).cik == APPLE

    def test_truncated_header_raises(self) -> None:
        text = _text("sgml_header_plain_issuer.txt")[:700]
        with pytest.raises(ValueError, match="truncated"):
            parse_sgml_header(text)

    def test_several_issuers_need_a_cik(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "</SEC-HEADER>",
            "FILER:\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY:\t\t0000000042\n"
            "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tREAL ESTATE [6798]\n</SEC-HEADER>",
        )
        with pytest.raises(ValueError, match="2 issuers"):
            parse_sgml_header(text)
        assert parse_sgml_header(text, cik="42").sic == 6798
        assert parse_sgml_header(text, cik=APPLE).sic == 3571
        with pytest.raises(ValueError, match="no issuer"):
            parse_sgml_header(text, cik="7")

    def test_repeated_fall_back_hour_takes_the_later_instant(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "<ACCEPTANCE-DATETIME>20251031060126", "<ACCEPTANCE-DATETIME>20251102013000"
        )
        assert parse_sgml_header(text).accepted_at == datetime(2025, 11, 2, 6, 30, tzinfo=UTC)

    def test_missing_acceptance_raises(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "<ACCEPTANCE-DATETIME>20251031060126\n", ""
        )
        with pytest.raises(ValueError, match="ACCEPTANCE-DATETIME"):
            parse_sgml_header(text)


class TestNormalizeClassMember:
    def test_drops_prefix_and_trailing_member(self) -> None:
        assert normalize_class_member("us-gaap:CommonClassAMember") == "CommonClassA"

    def test_fsn_member_already_stripped_is_unchanged(self) -> None:
        assert normalize_class_member("CommonClassA") == "CommonClassA"

    def test_filer_custom_member(self) -> None:
        assert (
            normalize_class_member("goog:ClassACommonStockParValue00001PerShareCustomMember")
            == "ClassACommonStockParValue00001PerShareCustom"
        )

    def test_empty_stays_empty(self) -> None:
        assert normalize_class_member("") == ""


class TestCoverPage:
    def test_dual_class_listings_and_shares(self, acceptance: dict[str, datetime]) -> None:
        accession = "0001652044-26-000018"
        accepted_at = acceptance[accession]
        assert accepted_at == datetime(2026, 2, 5, 2, 56, 3, tzinfo=UTC)
        parsed = parse_cover_page(
            _gz("filing_dual_class_goog-20251231.htm.gz"),
            accession=accession,
            accepted_at=accepted_at,
        )
        cover = parsed.cover
        assert (cover.cik, cover.accepted_at) == (ALPHABET, accepted_at)
        # Notes with no trading symbol are not listings.
        assert [(item.ticker, item.exchange) for item in cover.listings] == [
            ("GOOGL", "NASDAQ"),
            ("GOOG", "NASDAQ"),
        ]
        assert cover.listings[0].title == "Class A Common Stock, $0.001 par value"
        shares = {f.class_member: f.value for f in parsed.facts}
        assert shares == {
            "CommonClassA": 5_822_000_000,
            "CommonClassB": 837_000_000,
            "CapitalClassC": 5_438_000_000,
        }
        assert all(f.as_of_date == date(2026, 1, 28) for f in parsed.facts)
        assert all(f.accepted_at == accepted_at for f in parsed.facts)
        assert all(f.accession == accession for f in parsed.facts)

    def test_single_class_undimensioned_shares(self, acceptance: dict[str, datetime]) -> None:
        accession = "0000320193-25-000079"
        parsed = parse_cover_page(
            _gz("filing_plain_issuer_aapl-20250927.htm.gz"),
            accession=accession,
            accepted_at=acceptance[accession],
        )
        assert [(i.title, i.ticker, i.exchange) for i in parsed.cover.listings] == [
            ("Common Stock, $0.00001 par value per share", "AAPL", "NASDAQ")
        ]
        [fact] = parsed.facts
        assert (fact.class_member, fact.value, fact.as_of_date) == (
            "",
            14_776_353_000,
            date(2025, 10, 17),
        )
        assert fact.fact_name == SHARES

    def test_no_network(self) -> None:
        # The autouse no-network fixture already makes connect raise; parsing
        # a recorded document must not need it.
        parsed = parse_cover_page(
            _gz("filing_plain_issuer_aapl-20250927.htm.gz"),
            accession="0000320193-25-000079",
            accepted_at=datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC),
        )
        assert parsed.cover.listings


def _context(context_id: str, dims: dict[str, str] | None = None, *, instant: bool = False) -> str:
    segment = ""
    if dims:
        members = "".join(
            f'<xbrldi:explicitMember dimension="{d}">{m}</xbrldi:explicitMember>'
            for d, m in dims.items()
        )
        segment = f"<xbrli:segment>{members}</xbrli:segment>"
    period = (
        "<xbrli:instant>2024-01-31</xbrli:instant>"
        if instant
        else "<xbrli:startDate>2023-01-01</xbrli:startDate>"
        "<xbrli:endDate>2023-12-31</xbrli:endDate>"
    )
    return (
        f'<xbrli:context id="{context_id}"><xbrli:entity>'
        f'<xbrli:identifier scheme="http://www.sec.gov/CIK">0000092122</xbrli:identifier>'
        f"{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
    )


_CLASS = {"us-gaap:StatementClassOfStockAxis": "us-gaap:CommonStockMember"}


def _ixbrl(contexts: str, facts: str) -> bytes:
    return (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body><div style="display:none">'
        f"<ix:header><ix:resources>{contexts}</ix:resources></ix:header></div>{facts}</body></html>"
    ).encode()


def _nn(name: str, context: str, value: str, extra: str = "") -> str:
    return f'<ix:nonNumeric name="dei:{name}" contextRef="{context}"{extra}>{value}</ix:nonNumeric>'


def _shares(context: str, value: str, extra: str = "") -> str:
    return (
        f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="{context}" '
        f'unitRef="shares" decimals="INF"{extra}>{value}</ix:nonFraction>'
    )


def _cover(document: bytes) -> Any:
    return parse_cover_page(
        document,
        accession="0000092122-24-000001",
        accepted_at=datetime(2024, 2, 1, 21, 0, tzinfo=UTC),
    )


class TestCoverPageFailClosed:
    def test_empty_document_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _cover(b"")

    def test_wrong_shape_facts_raise_value_error(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            parse_company_facts({"cik": 1, "facts": []}, [SHARES], {})

    def test_co_registrant_contexts_are_not_the_filers(self) -> None:
        document = _ixbrl(
            _context("c2", _CLASS)
            + _context("s1", instant=True)
            + _context("s2", {"dei:LegalEntityAxis": "so:SubsidiaryMember"}, instant=True)
            + _context("x2", {**_CLASS, "dei:LegalEntityAxis": "so:SubsidiaryMember"}),
            _nn("Security12bTitle", "c2", "Common Stock")
            + _nn("TradingSymbol", "c2", "SO")
            + _nn("SecurityExchangeName", "c2", "NYSE")
            + _nn("Security12bTitle", "x2", "Series A Notes")
            + _nn("TradingSymbol", "x2", "SUBX")
            + _nn("SecurityExchangeName", "x2", "NYSE")
            + _shares("s1", "1090000000")
            + _shares("s2", "30537500"),
        )
        parsed = _cover(document)
        assert [f.value for f in parsed.facts] == [1_090_000_000]
        assert [i.ticker for i in parsed.cover.listings] == ["SO"]
        assert parsed.other_contexts == ("s2", "x2")

    def test_two_classes_in_one_context_raise(self) -> None:
        document = _ixbrl(
            _context("c1"),
            _nn("Security12bTitle", "c1", "Class A")
            + _nn("TradingSymbol", "c1", "AAA")
            + _nn("SecurityExchangeName", "c1", "NYSE")
            + _nn("Security12bTitle", "c1", "Class B")
            + _nn("TradingSymbol", "c1", "BBB"),
        )
        with pytest.raises(ValueError, match="two values"):
            _cover(document)

    def test_repeated_identical_fact_is_kept_once(self) -> None:
        facts = _shares("s1", "5000") + _shares("s1", "5000")
        parsed = _cover(_ixbrl(_context("s1", instant=True), facts))
        assert [f.value for f in parsed.facts] == [5000]

    @pytest.mark.parametrize(
        "missing",
        [
            ("SecurityExchangeName",),  # #609 C1: NOBH, an OTC name (0001193125-26-391553)
            ("Security12bTitle",),  # #609 C1: BB (0001070235-26-000115)
            ("Security12bTitle", "SecurityExchangeName"),  # #609 C1: TMGI (0001683168-26-007174)
        ],
    )
    def test_symbol_without_title_or_exchange_is_skipped_and_counted(
        self, missing: tuple[str, ...]
    ) -> None:
        """Owner decision #224, as the FSN path: an incomplete listing is
        dropped and counted, and the filing keeps its shares and complete
        listings (it used to fail the whole accession, #609 C1)."""
        facts = {
            "Security12bTitle": _nn("Security12bTitle", "c1", "Common Stock"),
            "TradingSymbol": _nn("TradingSymbol", "c1", "NOBH"),
            "SecurityExchangeName": _nn("SecurityExchangeName", "c1", "NYSE"),
        }
        for name in missing:
            del facts[name]
        complete = (
            _nn("Security12bTitle", "c2", "Warrants")
            + _nn("TradingSymbol", "c2", "NOBHW")
            + _nn("SecurityExchangeName", "c2", "NYSE")
        )
        document = _ixbrl(
            _context("c1") + _context("c2", _CLASS) + _context("s1", instant=True),
            "".join(facts.values()) + complete + _shares("s1", "1000"),
        )
        parsed = _cover(document)
        assert [item.ticker for item in parsed.cover.listings] == ["NOBHW"]
        assert parsed.incomplete_listings == 1
        assert [f.value for f in parsed.facts] == [1000]

    def test_a_complete_cover_counts_no_incomplete_listing(self) -> None:
        document = _ixbrl(
            _context("c1"),
            _nn("Security12bTitle", "c1", "Common Stock")
            + _nn("TradingSymbol", "c1", "SO")
            + _nn("SecurityExchangeName", "c1", "NYSE"),
        )
        assert _cover(document).incomplete_listings == 0

    def test_nil_share_facts_are_skipped(self) -> None:
        """#609 C2 (0001398344-26-014697): two of four unit classes report
        `xs:nil="true"` share facts with no text; the other two are kept."""

        def units(context: str, member: str) -> str:
            return _context(
                context, {"us-gaap:StatementClassOfStockAxis": f"custom:{member}"}, instant=True
            )

        def nil(context: str, fact_id: str) -> str:
            return (
                f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" '
                f'contextRef="{context}" id="{fact_id}" unitRef="Shares" xs:nil="true">'
                "</ix:nonFraction>"
            )

        def number(context: str, fact_id: str, value: str) -> str:
            return (
                f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" '
                f'contextRef="{context}" id="{fact_id}" format="ixt:numdotdecimal" '
                f'decimals="INF" unitRef="Shares">{value}</ix:nonFraction>'
            )

        document = _ixbrl(
            units("A", "ClassAUnitsMember")
            + units("S", "ClassSUnitsMember")
            + units("I", "ClassIUnitsMember")
            + units("M", "ClassMUnitsMember"),
            nil("A", "xdx2ixbrl0037")
            + nil("S", "xdx2ixbrl0038")
            + number("I", "Fact000035", "211,076,548")
            + number("M", "Fact000036", "377,418"),
        )
        parsed = _cover(document)
        assert {f.class_member: f.value for f in parsed.facts} == {
            "ClassIUnits": 211_076_548.0,
            "ClassMUnits": 377_418.0,
        }

    @pytest.mark.parametrize(
        "fact",
        [
            _shares("s1", "1,000", ' xsi:nil="true"'),
            _nn("TradingSymbol", "c1", "SO", ' xsi:nil="true"'),
            _nn("Security12bTitle", "c1", "<span>Common Stock</span>", ' xsi:nil="true"'),
        ],
    )
    def test_a_nil_fact_that_also_has_text_raises(self, fact: str) -> None:
        """#615: `xsi:nil="true"` on an element with text is an XBRL
        inconsistency; fail closed rather than skip a fact that may be real."""
        document = _ixbrl(_context("c1") + _context("s1", instant=True), fact)
        with pytest.raises(ValueError, match="nil but has text"):
            _cover(document)

    def test_a_nil_fact_with_only_whitespace_is_still_skipped(self) -> None:
        document = _ixbrl(
            _context("s1", instant=True),
            _shares("s1", " \n ", ' xsi:nil="true"') + _shares("s1", "1000"),
        )
        assert [f.value for f in _cover(document).facts] == [1000]

    def test_a_blank_share_fact_that_is_not_nil_still_raises(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            _cover(_ixbrl(_context("s1", instant=True), _shares("s1", "")))

    def test_a_cover_with_no_listing_or_shares_is_empty_for_its_cik(self) -> None:
        """#609 C3 (0002124122-26-000017, a TRIC 10-Q): the cover carries no
        listing and no shares fact, only other dei facts such as
        `EntityCentralIndexKey`. It is an empty parse for that CIK."""
        document = _ixbrl(
            _context("From2026-01-01to2026-06-30").replace("0000092122", "0002124122"),
            _nn("AmendmentFlag", "From2026-01-01to2026-06-30", "false")
            + _nn("EntityCentralIndexKey", "From2026-01-01to2026-06-30", "0002124122")
            + _nn("EntityRegistrantName", "From2026-01-01to2026-06-30", "Tric"),
        )
        parsed = parse_cover_page(
            document,
            accession="0002124122-26-000017",
            accepted_at=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        )
        assert parsed.cover.cik == "0002124122"
        assert parsed.cover.accession == "0002124122-26-000017"
        assert (parsed.cover.listings, parsed.facts, parsed.incomplete_listings) == ((), (), 0)

    def test_a_cover_with_no_cover_facts_and_no_cik_still_raises(self) -> None:
        document = _ixbrl(_context("d1"), _nn("EntityRegistrantName", "d1", "Tric"))
        with pytest.raises(ValueError, match="names 0 entities"):
            _cover(document)

    @pytest.mark.parametrize("cik", ["0002124122x", "\uff11\uff12\uff13", "12345678901"])
    def test_a_cover_with_no_cover_facts_and_a_malformed_cik_raises(self, cik: str) -> None:
        """The C3 CIK names cache files downstream: ASCII digits only, 1 to 10."""
        document = _ixbrl(_context("d1"), _nn("EntityCentralIndexKey", "d1", cik))
        with pytest.raises(ValueError, match="EntityCentralIndexKey is not a CIK"):
            _cover(document)

    def test_a_cover_with_no_cover_facts_and_two_ciks_raises(self) -> None:
        document = _ixbrl(
            _context("d1") + _context("d2"),
            _nn("EntityCentralIndexKey", "d1", "0002124122")
            + _nn("EntityCentralIndexKey", "d2", "0000092122"),
        )
        with pytest.raises(ValueError, match="names 0 entities"):
            _cover(document)

    def test_a_nil_trading_symbol_is_no_listing(self) -> None:
        """A nil `TradingSymbol` beside a title and an exchange used to give a
        listing with an empty ticker; skipped as nil, the class has no
        symbol, so it is not a listing (COVER_VERSION 2 re-parses such
        cached covers)."""
        document = _ixbrl(
            _context("c1") + _context("s1", instant=True),
            _nn("Security12bTitle", "c1", "Common Stock")
            + _nn("TradingSymbol", "c1", "", ' xsi:nil="true"')
            + _nn("SecurityExchangeName", "c1", "NYSE")
            + _shares("s1", "1000"),
        )
        parsed = _cover(document)
        assert parsed.cover.listings == ()
        assert parsed.incomplete_listings == 0
        assert [f.value for f in parsed.facts] == [1000]

    @pytest.mark.parametrize("missing", ["Security12bTitle", "SecurityExchangeName"])
    def test_a_nil_title_or_exchange_is_an_incomplete_listing(self, missing: str) -> None:
        facts = {
            "Security12bTitle": _nn("Security12bTitle", "c1", "Common Stock"),
            "TradingSymbol": _nn("TradingSymbol", "c1", "SO"),
            "SecurityExchangeName": _nn("SecurityExchangeName", "c1", "NYSE"),
        }
        facts[missing] = _nn(missing, "c1", "", ' xsi:nil="true"')
        parsed = _cover(_ixbrl(_context("c1"), "".join(facts.values())))
        assert (parsed.cover.listings, parsed.incomplete_listings) == ((), 1)

    def test_failed_format_raises(self) -> None:
        document = _ixbrl(
            _context("s1", instant=True), _shares("s1", "5.822", ' format="ixt:bogus" scale="6"')
        )
        with pytest.raises(ValueError, match="EntityCommonStockSharesOutstanding"):
            _cover(document)

    def test_non_cik_entity_raises(self) -> None:
        document = _ixbrl(
            _context("s1", instant=True).replace("http://www.sec.gov/CIK", "urn:other"),
            _shares("s1", "5000"),
        )
        with pytest.raises(ValueError, match="not a CIK"):
            _cover(document)


class TestDelisting:
    def test_25nse_class_and_exchange(self, acceptance: dict[str, datetime]) -> None:
        accession = "0001354457-26-000904"
        filing = parse_delisting(
            _gz("filing_delisted_25nse_primary_doc.xml.gz").decode(),
            form="25-NSE",
            accession=accession,
            accepted_at=acceptance[accession],
        )
        assert (filing.cik, filing.form, filing.class_title, filing.exchange) == (
            KLX,
            "25-NSE",
            "rights",
            "NASDAQ",
        )
        assert filing.accepted_at == datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC)
        assert filing.effective_on is None

    @pytest.mark.parametrize(
        "description",
        [
            "",
            "<descriptionClassSecurity/>",
            "<descriptionClassSecurity> </descriptionClassSecurity>",
        ],
    )
    def test_25nse_with_no_class_stays_a_failure_with_a_clear_message(
        self, description: str
    ) -> None:
        """#609 D1 (ACCO Brands, 0000876661-15-000379): a 25-NSE naming no
        class. Owner 2026-10-02: it stays a failure, the class is never
        guessed, and the message says so."""
        xml = (
            "<notificationOfRemoval><issuer><cik>0000712034</cik></issuer>"
            f"{description}<exchange><entityName>New York Stock Exchange</entityName></exchange>"
            "</notificationOfRemoval>"
        )
        with pytest.raises(ValueError) as caught:
            parse_delisting(
                xml,
                form="25-NSE",
                accession="0000876661-15-000379",
                accepted_at=datetime(2015, 9, 1, 14, 0, tzinfo=UTC),
            )
        message = str(caught.value)
        assert message.startswith("0000876661-15-000379: 25-NSE names no class of security")
        assert "descriptionClassSecurity" in message
        assert "not guessed" in message

    def test_malformed_xml_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            parse_delisting(
                "<notificationOfRemoval><issuer>",
                form="25",
                accession="0001354457-26-000904",
                accepted_at=datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC),
            )

    def test_other_form_raises(self) -> None:
        with pytest.raises(ValueError, match="10-K"):
            parse_delisting(
                _gz("filing_delisted_25nse_primary_doc.xml.gz").decode(),
                form="10-K",
                accession="0001354457-26-000904",
                accepted_at=datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC),
            )

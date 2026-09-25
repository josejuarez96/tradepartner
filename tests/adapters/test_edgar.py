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
            ("NYSEArca", "NYSE_ARCA"),
            ("NYSE Arca, Inc.", "NYSE_ARCA"),
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

    def test_missing_acceptance_raises(self) -> None:
        text = _text("sgml_header_plain_issuer.txt").replace(
            "<ACCEPTANCE-DATETIME>20251031060126\n", ""
        )
        with pytest.raises(ValueError, match="ACCEPTANCE-DATETIME"):
            parse_sgml_header(text)


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
            "us-gaap:CommonClassAMember": 5_822_000_000,
            "us-gaap:CommonClassBMember": 837_000_000,
            "goog:CapitalClassCMember": 5_438_000_000,
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

    def test_other_form_raises(self) -> None:
        with pytest.raises(ValueError, match="10-K"):
            parse_delisting(
                _gz("filing_delisted_25nse_primary_doc.xml.gz").decode(),
                form="10-K",
                accession="0001354457-26-000904",
                accepted_at=datetime(2026, 9, 24, 14, 8, 40, tzinfo=UTC),
            )

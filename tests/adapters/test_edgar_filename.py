"""Tests for the filing-name listing parsers (T184, #1597): the pure readings
of a filing's own file names and of its primary document's Section 12(b)
table, over the recorded payloads under `tests/fixtures/edgar/`.

Every case here comes from a real filing: the three `index.json` listings
(Apple's FY2017 10-K with a plain `<prefix>-YYYYMMDD.xml` instance, Apple's
FY2019 10-K whose instance is the iXBRL primary document's own
`<prefix>_htm.xml`, Medallion Financial's 10-K with no instance at all), the
two Form 25-NSE listings (Speed Commerce on Nasdaq, whose exhibit carries
the ticker; Arch Coal on the NYSE, whose exhibit carries none) and the four
Section 12(b) text shapes. Only the deep-front-matter case is synthetic: no
filing of the record writes its 12(b) table 100 KB in, so the fixture is
Amtrust Financial Services' FY2017 10-K/A shape with front matter long
enough to exercise `filename_document_max_bytes` (the
`cover_exchange_axis` convention).
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from tradepartner.adapters.edgar import cover_12b_exchanges, form25_exhibit_ticker, instance_prefix

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


def _listing(name: str) -> list[str]:
    """A recorded `index.json` listing's file names, as EDGAR returns them."""
    return [item["name"] for item in json.loads((FIXTURES / name).read_text())["directory"]["item"]]


def _document(name: str) -> str:
    """A recorded primary document, gzipped under its own name."""
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as handle:
        return handle.read()


class TestInstancePrefix:
    def test_a_plain_instance_reads_the_ticker_of_the_time(self) -> None:
        """Apple's FY2017 10-K instance is `aapl-20170930.xml` beside its
        four linkbases, whose `_cal`/`_def`/`_lab`/`_pre` names are never an
        instance."""
        names = _listing("index_10k_plain_instance.json")
        assert "aapl-20170930_cal.xml" in names
        assert instance_prefix(names) == "aapl"

    def test_a_htm_instance_reads_the_primary_documents_own_name(self) -> None:
        """Apple's FY2019 10-K is tagged inline, so its instance is the
        primary document's own `a10-k20199282019_htm.xml` and the prefix is
        that document's stem: a reading, whatever #844's symbol rule then
        makes of it."""
        names = _listing("index_10k_htm_instance.json")
        assert "a10-k20199282019_htm.xml" in names
        assert instance_prefix(names) == "a10-k20199282019"

    def test_no_instance_reads_none(self) -> None:
        assert instance_prefix(_listing("index_10k_no_instance.json")) is None

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("R2.htm", None),
            ("FilingSummary.xml", None),
            ("aapl-20160930_cal.xml", None),
            ("aapl-20160930_def.xml", None),
            ("aapl-20160930_lab.xml", None),
            ("aapl-20160930_pre.xml", None),
            ("brk-a-20171231.xml", "brk-a"),
            ("abc-20171231_htm.xml", "abc"),
            ("jci-20160630.xml", "jci"),
        ],
    )
    def test_only_an_instance_name_reads_a_prefix(self, name: str, expected: str | None) -> None:
        """A rendering, a FilingSummary, a linkbase and any other `.xml` of
        a submission are not an instance; a lettered ticker keeps its dash."""
        assert instance_prefix([name]) == expected

    def test_two_names_of_one_prefix_are_one_reading(self) -> None:
        """A filing that carries both spellings of its instance, or repeats
        a name in the listing, is one prefix, never a disagreement."""
        assert (
            instance_prefix(["abc-20171231.xml", "abc-20171231_htm.xml", "abc-20171231_cal.xml"])
            == "abc"
        )
        assert instance_prefix(["jci-20160630.xml", "jci-20160630.xml"]) == "jci"

    def test_two_prefixes_read_none_never_a_guess(self) -> None:
        """A combined filing names two instances: which registrant's prefix
        a listing belongs to is never guessed, so no reading is taken."""
        assert instance_prefix(["jci-20160630.xml", "tyc-20160630.xml"]) is None


class TestForm25ExhibitTicker:
    def test_a_nasdaq_notices_exhibits_carry_the_ticker(self) -> None:
        """Speed Commerce's 25-NSE files `spdcdelistreason.txt`, one of the
        two Nasdaq conventions (464 of 467)."""
        names = _listing("index_25nse_nasdaq.json")
        assert "spdcdelistreason.txt" in names
        assert form25_exhibit_ticker(names) == "spdc"

    def test_an_nyse_notice_carries_none(self) -> None:
        """Arch Coal's 25-NSE writes `ruleprovisionnotice.htm`: no NYSE-family
        notice carries a ticker (0 of 312)."""
        names = _listing("index_25nse_nyse.json")
        assert "ruleprovisionnotice.htm" in names
        assert form25_exhibit_ticker(names) is None

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ovti-form25.txt", "ovti"),
            ("fsys-form25.txt", "fsys"),
            ("ovtidelistreason.txt", "ovti"),
            ("spdcdelistreason.txt", "spdc"),
            ("ruleprovisionnotice.htm", None),
            ("primary_doc.xml", None),
            ("form25.txt", None),
            ("delistreason.txt", None),
        ],
    )
    def test_only_a_nasdaq_exhibit_reads_a_ticker(self, name: str, expected: str | None) -> None:
        assert form25_exhibit_ticker([name]) == expected

    def test_two_exhibits_of_one_ticker_are_one_reading(self) -> None:
        assert form25_exhibit_ticker(["ovti-form25.txt", "ovtidelistreason.txt"]) == "ovti"

    def test_two_exhibit_tickers_read_none(self) -> None:
        """Two disagreeing tickers in one listing name neither."""
        assert form25_exhibit_ticker(["ovti-form25.txt", "fsysdelistreason.txt"]) is None


class TestCover12bExchanges:
    def test_the_recorded_two_venue_table_reads_both_classes(self) -> None:
        """PennantPark's 10-K/A names its common stock on Nasdaq and its
        senior notes on the New York Stock Exchange, each as its own
        filer's spelling: `The NASDAQ Stock Market LLC` and `The New York
        Stock Exchange`, the article and all. Both read. Which line a row
        takes is the caller's rule (T184b); the reader gives both, the
        common class's first."""
        text = _document("filing_dual_exchange_d360924d10ka.htm.gz")
        assert cover_12b_exchanges(text, max_bytes=4_000_000) == (
            ("Common Stock, par value $0.001 per share", "NASDAQ"),
            ("6.25% Senior Notes due 2025", "NYSE"),
        )

    def test_two_classes_on_two_exchanges_read_both_lines(self) -> None:
        """Amtrust's FY2017 10-K/A shape (synthetic fixture): the common
        shares on Nasdaq, every preferred class on the New York Stock
        Exchange, both read as `normalize_exchange` tags in document order.
        Which line a row takes is the caller's rule (T184b); the reader
        gives both, the common class's first."""
        text = (FIXTURES / "cover_12b_two_exchanges.htm").read_text()
        assert cover_12b_exchanges(text, max_bytes=4_000_000) == (
            ("Common Shares, $0.01 par value per share", "NASDAQ"),
            ("Series A Preferred Stock, $0.01 par value per share", "NYSE"),
            ("Series B Preferred Stock, $0.01 par value per share", "NYSE"),
        )

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            # The spellings a filer writes itself, which `_EXCHANGES`'
            # exact-name table does not carry (quant-auditor, T184): the
            # article the dei codes and the Form 25 notices drop, and
            # Nasdaq's own tier names for its one listing venue.
            ("The New York Stock Exchange", "NYSE"),
            ("New York Stock Exchange LLC", "NYSE"),
            ("THE NASDAQ STOCK MARKET", "NASDAQ"),
            ("The NASDAQ Stock Market LLC", "NASDAQ"),
            ("The NASDAQ Global Select Market", "NASDAQ"),
            ("NASDAQ Global Select Market", "NASDAQ"),
            ("The NASDAQ Global Market", "NASDAQ"),
            ("NASDAQ Capital Market", "NASDAQ"),
            ("NASDAQ National Market", "NASDAQ"),
            ("The NASDAQ Stock Market LLC (NASDAQ Global Select Market)", "NASDAQ"),
            ("NYSE MKT LLC", "NYSE_AMERICAN"),
            # The cover page and the Form 25 spelling is already exact.
            ("New York Stock Exchange", "NYSE"),
            ("Nasdaq Stock Market LLC", "NASDAQ"),
            # A header cell, a none cell and a venue no code carries.
            ("Name of each exchange on which registered", None),
            ("None", None),
            ("N/A", None),
            ("Not applicable", None),
            ("The Tokyo Stock Exchange", None),
        ],
    )
    def test_a_tables_venue_spelling_reads_its_code(
        self, spelling: str, expected: str | None
    ) -> None:
        """The exchange is read from the table as the filer spells it, so
        `The New York Stock Exchange` and `The NASDAQ Global Select Market`
        read what a Form 25 names, not a slug."""
        text = (
            "Securities registered pursuant to Section 12(b) of the Act:\n"
            "<table><tr><td>Common Stock, $0.01 par value</td><td></td>"
            f"<td>{spelling}</td></tr></table>"
        )
        pairs = cover_12b_exchanges(text, max_bytes=4_000_000)
        assert [code for _, code in pairs] == ([expected] if expected else [])

    def test_a_table_after_front_matter_reads_up_to_the_cap(self) -> None:
        """The 12(b) table sits 115 KB into the document: inside
        `filename_document_max_bytes` it reads, at a cap that cuts before it
        nothing does, and a cap that holds the whole document reads exactly
        what a wider one does — the read stops at the table or at the cap."""
        text = (FIXTURES / "cover_12b_two_exchanges.htm").read_text()
        document_bytes = len(text.encode("utf-8"))
        assert document_bytes > 115_000
        assert cover_12b_exchanges(text, max_bytes=200_000)[0][1] == "NASDAQ"
        assert cover_12b_exchanges(text, max_bytes=100_000) == ()
        assert cover_12b_exchanges(text, max_bytes=document_bytes) == cover_12b_exchanges(
            text, max_bytes=4_000_000
        )
        assert cover_12b_exchanges(text, max_bytes=4_000_000) == cover_12b_exchanges(
            text, max_bytes=document_bytes * 2
        )

    def test_a_12g_only_document_reads_no_exchange(self) -> None:
        """Chineseinvestors' 10-K/A registers under Section 12(g): there is
        no 12(b) heading at all, so no row is read (counted `no_exchange`
        by T184b)."""
        text = _document("filing_12g_only_chineseinvestors_10ka-053116.htm.gz")
        assert cover_12b_exchanges(text, max_bytes=4_000_000) == ()

    def test_a_heading_with_no_table_reads_no_exchange(self) -> None:
        """Royale Energy's 10-K/A writes the 12(b) heading and then a 12(g)
        block, with no table at all: the block ends at the next registration
        section and nothing is read."""
        assert (
            cover_12b_exchanges(
                _document("filing_no_table_re04041710ka.htm.gz"), max_bytes=4_000_000
            )
            == ()
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # A table whose exchange cell names none: `None` and `N/A` name
            # no venue, so the row reads nothing (Las Vegas Railway Express's
            # 10-K/A shape).
            (
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "<table><tr><td>Common Stock, $0.0001 par value</td><td></td>"
                "<td>None</td></tr></table>",
                (),
            ),
            # A bare (title, exchange) row, no symbol column.
            (
                "Section 12(b) of the Act:\n<table><tr><td>Common Stock</td>"
                "<td>Nasdaq Stock Market LLC</td></tr></table>",
                (("Common Stock", "NASDAQ"),),
            ),
            # The same row written without any table, its columns after runs
            # of spaces.
            (
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Common Stock, $0.01 par value    Nasdaq Stock Market LLC",
                (("Common Stock, $0.01 par value", "NASDAQ"),),
            ),
            # A header row names no venue, and the 12(g) heading ends the
            # block, so the exchange line after it is never read.
            (
                "Securities registered pursuant to Section 12(g) of the Act:\n"
                "Common Stock, $0.01 par value    Nasdaq Stock Market LLC",
                (),
            ),
            # The read stops at the table: a second table naming another
            # exchange, written after the 12(b) block, is never reached.
            (
                "Section 12(b) of the Act:\n"
                "<table><tr><td>Common Stock</td><td>Nasdaq Stock Market LLC</td></tr></table>"
                "<table><tr><td>Common Stock</td><td>New York Stock Exchange</td></tr></table>",
                (("Common Stock", "NASDAQ"),),
            ),
            # No table follows the heading, so the block ends at the 12(g)
            # heading and reads nothing (a filer writing its 12(g) block
            # with no 12(b) one at all).
            (
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Securities registered pursuant to Section 12(g) of the Act:\n"
                "Common Stock    Nasdaq Stock Market LLC",
                (),
            ),
            # No table and no 12(g) block: the block ends at the blank line,
            # so the exchange line after it is never read either.
            (
                "Securities registered pursuant to Section 12(b) of the Act:\n\n\n"
                "Common Stock    Nasdaq Stock Market LLC",
                (),
            ),
            # A heading written inside the table's own first row, on one
            # source line (minified HTML): the block starts at the end of
            # that row, so the table's own row reads and the table after the
            # 12(g) block never does.
            (
                "<table><tr><td>Securities registered pursuant to "
                "Section 12(b) of the Act:</td></tr>"
                "<tr><td>Common Stock</td><td>New York Stock Exchange</td></tr></table>"
                "<p>Securities registered pursuant to Section 12(g) of the Act: None</p>"
                "<table><tr><td>x</td><td>Nasdaq</td></tr></table>",
                (("Common Stock", "NYSE"),),
            ),
            # The 12(g) section's own table names a venue: it is the 12(g)
            # block's, never this heading's, so nothing is read. Removing the
            # 12(g) bound makes this read the row of that later table.
            (
                "Securities registered pursuant to Section 12(b) of the Act:\n\n\n"
                "Securities registered pursuant to Section 12(g) of the Act:\n"
                "<table><tr><td>Common Stock</td><td>Nasdaq Stock Market LLC</td></tr></table>",
                (),
            ),
            # A cover page that registers nothing under 12(b) says so on the
            # heading's own line ("Section 12(b) of the Act: None"): its block
            # ends where it says none, and the table below is another
            # section's, however close or far it sits.
            (
                "Securities registered pursuant to Section 12(b) of the Act: None\n"
                "Indicate by check mark whether the registrant is a large accelerated filer\n"
                "<table><tr><td>Indicate by check mark</td>"
                "<td>Nasdaq Stock Market LLC</td></tr></table>",
                (),
            ),
            # The same statement at the end of a two-line heading, with the
            # 12(g) block after it.
            (
                "Securities registered pursuant to Section 12(b)\n"
                "of the Act: None\n"
                "Securities registered pursuant to Section 12(g) of the Act:\n"
                "<table><tr><td>Common Stock</td><td>Nasdaq Stock Market LLC</td></tr></table>",
                (),
            ),
        ],
    )
    def test_the_four_text_shapes_the_reader_stops_at(
        self, text: str, expected: tuple[tuple[str, str], ...]
    ) -> None:
        assert cover_12b_exchanges(text, max_bytes=4_000_000) == expected

    def test_the_read_cap_is_honoured_when_it_is_set(self) -> None:
        """`max_bytes` bounds the read itself: the deep table reads at the
        cap's default size, and a cap that cuts before it reads nothing."""
        text = (FIXTURES / "cover_12b_two_exchanges.htm").read_text()
        assert cover_12b_exchanges(text, max_bytes=4_000_000) != ()
        assert cover_12b_exchanges(text, max_bytes=0) == ()

    def test_there_is_no_uncapped_read(self) -> None:
        """#1624 (quant-auditor nit): the cap is required and is an int, so
        no call site can read a primary document unbounded — not even by
        passing `None`, which the reader used to take as "the whole
        document". A `bool` is an `int`, so `True` (one byte) is a mistake,
        not a cap."""
        with pytest.raises(TypeError):
            cover_12b_exchanges("Section 12(b) of the Act:", max_bytes=None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            cover_12b_exchanges("Section 12(b) of the Act:")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            cover_12b_exchanges("Section 12(b) of the Act:", max_bytes=True)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            cover_12b_exchanges("Section 12(b) of the Act:", max_bytes=-1)

    def test_the_read_stays_linear_on_unclosed_tags(self) -> None:
        """safety-reviewer (T184): the text is filer-written, so a pattern
        that scans to the end of the document on every `<` it does not close
        makes a document at the cap quadratic in its own length. One million
        unclosed tags at the cap's default size must read in well under a
        second, and a document that is one long tag must not read a venue."""
        crafted = "<" * 1_000_000
        started = time.monotonic()
        assert cover_12b_exchanges(crafted, max_bytes=4_000_000) == ()
        assert time.monotonic() - started < 10.0
        for broken in ("</ td </td", "<br " * 1_000):
            assert cover_12b_exchanges(broken, max_bytes=4_000_000) == ()

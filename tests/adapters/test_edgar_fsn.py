"""Tests for the SEC Financial Statement and Notes (FSN) data sets (T11c,
plan amendment #216).

`parse_fsn` is exercised over synthetic rows built in this file (dual-class
issuer, a `coreg` row, a filer-custom member, a non-`ClassOfStock` axis, a
non-issuer CIK, and one accession whose rows do not parse), and over the
owner-recorded periods under `tests/fixtures/edgar/fsn/` (`2025_10` Apple,
`2026_02` Alphabet; PR #232), which are compared with `parse_cover_page` on
the same filings. The recorded tests are never skipped: missing fixtures
must fail, not pass silently.

`EdgarFilingSource._ensure_fsn` is tested against a synthetic FSN page and
in-memory zips served by an `httpx.MockTransport`, reusing
`edgar_transport.EdgarRouter`. `filing_index()` is not run for real in these
tests (it is exercised in `test_edgar_source.py`); `_ensure_fsn` only needs
it to have run once, so the tests set the private flag directly, the same
way this suite already reaches into other adapter internals
(`edgar_raw._LIMITER`).
"""

from __future__ import annotations

import dataclasses
import io
import json
import zipfile
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from edgar_transport import EdgarRouter, edgar_settings

from tradepartner import ingest
from tradepartner.adapters.edgar import (
    FsnFailure,
    FsnFiling,
    FsnShare,
    parse_fsn,
    restore_class_letter_space,
)
from tradepartner.adapters.edgar_source import (
    FSN_VERSION,
    EdgarFilingSource,
    _fsn_normalize_member,
    _fsn_rows,
)
from tradepartner.adapters.filings import CoverListing
from tradepartner.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "fsn"
FSN_PAGE_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets"
)


def _fsn_zip_url(period: str) -> str:
    return (
        "https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/"
        f"{period}_notes.zip"
    )


def _fsn_page_html(*periods: str) -> str:
    links = "\n".join(f'<a href="{_fsn_zip_url(p)}">{p}</a>' for p in periods)
    return f"<html><body>{links}</body></html>"


def _tsv(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    header = list(rows[0].keys())
    lines = ["\t".join(header)]
    for row in rows:
        lines.append("\t".join(str(row.get(h, "")) for h in header))
    return "\n".join(lines) + "\n"


def _fsn_zip_bytes(
    sub: list[dict[str, str]],
    num: list[dict[str, str]],
    txt: list[dict[str, str]],
    dim: list[dict[str, str]],
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sub.tsv", _tsv(sub))
        archive.writestr("num.tsv", _tsv(num))
        archive.writestr("txt.tsv", _tsv(txt))
        archive.writestr("dim.tsv", _tsv(dim))
    return buffer.getvalue()


# --- parse_fsn: synthetic rows (module docstring) ---------------------------


def _sub(adsh: str, cik: str, form: str, sic: str = "3711") -> dict[str, str]:
    return {"adsh": adsh, "cik": cik, "sic": sic, "form": form}


def _txt(
    adsh: str,
    tag: str,
    value: str,
    *,
    dimh: str = "",
    dimn: str = "0",
    coreg: str = "",
) -> dict[str, str]:
    return {"adsh": adsh, "tag": tag, "dimh": dimh, "dimn": dimn, "coreg": coreg, "value": value}


def _num(
    adsh: str,
    tag: str,
    value: str,
    ddate: str,
    *,
    dimh: str = "",
    dimn: str = "0",
    coreg: str = "",
) -> dict[str, str]:
    return {
        "adsh": adsh,
        "tag": tag,
        "ddate": ddate,
        "dimh": dimh,
        "dimn": dimn,
        "coreg": coreg,
        "value": value,
    }


def _dim(dimhash: str, segments: str) -> dict[str, str]:
    return {"dimhash": dimhash, "segments": segments}


SHARES_TAG = "EntityCommonStockSharesOutstanding"


def _null_num(adsh: str, ddate: str, value: str | None, **kwargs: str) -> dict[str, Any]:
    """A share row whose `value` DuckDB read as NULL (`None`) or blank (#609 F3)."""
    return {**_num(adsh, SHARES_TAG, "", ddate, **kwargs), "value": value}


class TestParseFsn:
    def test_plain_issuer_undimensioned_listing_and_shares(self) -> None:
        sub = [_sub("0000320193-25-000079", "320193", "10-K")]
        txt = [
            _txt("0000320193-25-000079", "Security12bTitle", "Common Stock, $0.00001 par value"),
            _txt("0000320193-25-000079", "TradingSymbol", "AAPL"),
            _txt("0000320193-25-000079", "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ]
        num = [
            _num(
                "0000320193-25-000079",
                "EntityCommonStockSharesOutstanding",
                "14776353000",
                "20251017",
            )
        ]
        parsed = parse_fsn(sub, num, txt, [])
        assert parsed.failures == ()
        [record] = parsed.records
        assert record == FsnFiling(
            accession="0000320193-25-000079",
            cik="0000320193",
            form="10-K",
            sic=3711,
            listings=(CoverListing("Common Stock, $0.00001 par value", "AAPL", "NASDAQ"),),
            shares=(FsnShare("", 14_776_353_000.0, date(2025, 10, 17)),),
        )

    def test_dual_class_and_filer_custom_member(self) -> None:
        accession = "0001652044-26-000018"
        sub = [_sub(accession, "1652044", "10-K")]
        dim = [
            _dim("hA", "ClassOfStock=CommonClassA;"),
            _dim("hB", "ClassOfStock=CommonClassB;"),
            _dim("hC", "ClassOfStock=ClassACommonStockParValue00001PerShareCustom;"),
        ]
        txt = [
            _txt(accession, "Security12bTitle", "Class A Common Stock", dimh="hA", dimn="1"),
            _txt(accession, "TradingSymbol", "GOOGL", dimh="hA", dimn="1"),
            _txt(accession, "SecurityExchangeName", "Nasdaq Stock Market LLC", dimh="hA", dimn="1"),
            _txt(accession, "Security12bTitle", "Class C Capital Stock", dimh="hC", dimn="1"),
            _txt(accession, "TradingSymbol", "GOOG", dimh="hC", dimn="1"),
            _txt(accession, "SecurityExchangeName", "Nasdaq Stock Market LLC", dimh="hC", dimn="1"),
        ]
        num = [
            _num(
                accession,
                "EntityCommonStockSharesOutstanding",
                "5822000000",
                "20260128",
                dimh="hA",
                dimn="1",
            ),
            _num(
                accession,
                "EntityCommonStockSharesOutstanding",
                "837000000",
                "20260128",
                dimh="hB",
                dimn="1",
            ),
            _num(
                accession,
                "EntityCommonStockSharesOutstanding",
                "5438000000",
                "20260128",
                dimh="hC",
                dimn="1",
            ),
        ]
        parsed = parse_fsn(sub, num, txt, dim)
        assert parsed.failures == ()
        [record] = parsed.records
        shares = {s.class_member: s.value for s in record.shares}
        assert shares == {
            "CommonClassA": 5_822_000_000.0,
            "CommonClassB": 837_000_000.0,
            "ClassACommonStockParValue00001PerShareCustom": 5_438_000_000.0,
        }
        assert {(item.ticker, item.exchange) for item in record.listings} == {
            ("GOOGL", "NASDAQ"),
            ("GOOG", "NASDAQ"),
        }

    def test_coreg_row_excluded(self) -> None:
        accession = "0000000001-25-000001"
        sub = [_sub(accession, "1", "10-K")]
        num = [
            _num(accession, "EntityCommonStockSharesOutstanding", "100", "20250101", coreg="SUB1"),
        ]
        parsed = parse_fsn(sub, num, [], [])
        [record] = parsed.records
        assert record.shares == ()

    def test_non_class_of_stock_axis_excluded(self) -> None:
        accession = "0000000002-25-000001"
        sub = [_sub(accession, "2", "10-K")]
        dim = [_dim("hOther", "SomeOtherAxis=Foo;")]
        num = [
            _num(
                accession,
                "EntityCommonStockSharesOutstanding",
                "100",
                "20250101",
                dimh="hOther",
                dimn="1",
            ),
        ]
        parsed = parse_fsn(sub, num, [], dim)
        [record] = parsed.records
        assert record.shares == ()

    def test_non_issuer_cik_still_yields_a_record(self) -> None:
        """The amendment: the issuer filter is applied at read time (T11d),
        never inside parse_fsn -- a CIK with no other issuer evidence still
        gets a record here."""
        accession = "0000099999-25-000001"
        sub = [_sub(accession, "99999", "10-Q")]
        parsed = parse_fsn(sub, [], [], [])
        assert [r.cik for r in parsed.records] == ["0000099999"]

    def test_unparsable_accession_is_one_failure_others_still_parse(self) -> None:
        good = "0000000003-25-000001"
        bad = "0000000004-25-000001"
        sub = [_sub(good, "3", "10-K"), _sub(bad, "4", "10-K")]
        num = [
            _num(good, "EntityCommonStockSharesOutstanding", "100", "20250101"),
            _num(bad, "EntityCommonStockSharesOutstanding", "not-a-number", "20250101"),
        ]
        parsed = parse_fsn(sub, num, [], [])
        assert [r.accession for r in parsed.records] == [good]
        assert len(parsed.failures) == 1
        [failure] = parsed.failures
        assert failure.accession == bad
        assert isinstance(failure, FsnFailure)

    @pytest.mark.parametrize("cik", ["1" * 400, "-5", "12a"])
    def test_a_malformed_cik_fails_that_accession_only(self, cik: str) -> None:
        """A CIK that is not 1 to 10 digits would make a bad cache file name;
        it is one failure, not a run-wide error."""
        good, bad = "0000000003-25-000001", "0000000004-25-000001"
        parsed = parse_fsn([_sub(good, "3", "10-K"), _sub(bad, cik, "10-K")], [], [], [])
        assert [r.accession for r in parsed.records] == [good]
        assert [f.accession for f in parsed.failures] == [bad]

    def test_symbol_with_no_title_or_exchange_is_skipped_and_counted(self) -> None:
        """Owner decision 2026-09-26 (#224): the incomplete listing is dropped
        and counted, and the filing keeps its SIC, shares and complete listings."""
        accession = "0000000005-25-000001"
        sub = [_sub(accession, "5", "10-K", sic="7372")]
        num = [_num(accession, "EntityCommonStockSharesOutstanding", "100", "20250131")]
        txt = [
            _txt(accession, "Security12bTitle", "Common Stock", dimh="0xa", dimn="1"),
            _txt(accession, "TradingSymbol", "GOOD", dimh="0xa", dimn="1"),
            _txt(accession, "SecurityExchangeName", "NASDAQ", dimh="0xa", dimn="1"),
            _txt(accession, "TradingSymbol", "BAD", dimh="0xb", dimn="1"),  # no title/exchange
        ]
        dim = [_dim("0xa", "ClassOfStock=CommonClassA;"), _dim("0xb", "ClassOfStock=Units;")]
        parsed = parse_fsn(sub, num, txt, dim)
        [record] = parsed.records
        assert parsed.failures == ()
        assert [item.ticker for item in record.listings] == ["GOOD"]
        assert (record.incomplete_listings, record.sic, len(record.shares)) == (1, 7372, 1)

    def test_a_symbol_with_no_exchange_is_kept_as_a_symbol_only_tag(self) -> None:
        """#1597 (spec amendment 2026-10-12): the same filing keeps its
        `dei:TradingSymbol` values that name no exchange as symbol-only tags,
        for a filename-listing reading to confirm against. The listing and
        the incomplete count are unchanged: such a tag never becomes a
        `filing` listing row."""
        accession = "0000000006-25-000001"
        sub = [_sub(accession, "6", "10-K")]
        txt = [
            _txt(accession, "Security12bTitle", "Common Stock", dimh="0xa", dimn="1"),
            _txt(accession, "TradingSymbol", "ONLY", dimh="0xa", dimn="1"),
            _txt(accession, "Security12bTitle", "Common Stock, no par", dimh="0xc", dimn="1"),
            _txt(accession, "TradingSymbol", "ALSO", dimh="0xc", dimn="1"),
            _txt(accession, "Security12bTitle", "Common Stock, no par", dimh="0xd", dimn="1"),
            _txt(accession, "TradingSymbol", "ALSO", dimh="0xd", dimn="1"),
            _txt(accession, "SecurityExchangeName", "NYSE", dimh="0xd", dimn="1"),
        ]
        dim = [
            _dim("0xa", "ClassOfStock=CommonClassA;"),
            _dim("0xc", "ClassOfStock=CommonClassB;"),
            _dim("0xd", "ClassOfStock=CommonClassC;"),
        ]
        parsed = parse_fsn(sub, [], txt, dim)
        [record] = parsed.records
        assert parsed.failures == ()
        # Only the group with an exchange is a listing; the two without one
        # are counted and kept as their symbols, sorted and deduplicated.
        assert [item.ticker for item in record.listings] == ["ALSO"]
        assert (record.incomplete_listings, record.symbol_only) == (2, ("ALSO", "ONLY"))

    def test_a_filing_without_a_symbol_only_tag_reads_none(self) -> None:
        """A filing whose listings are all complete has no symbol-only tag,
        and one with no symbol at all (notes only) has none either."""
        accession = "0000000007-25-000001"
        sub = [_sub(accession, "7", "10-K")]
        complete = [
            _txt(accession, "Security12bTitle", "Common Stock"),
            _txt(accession, "TradingSymbol", "FULL"),
            _txt(accession, "SecurityExchangeName", "NASDAQ"),
        ]
        notes_only = [_txt(accession, "Security12bTitle", "1.000% Notes due 2027")]
        for txt in (complete, notes_only):
            [record] = parse_fsn(sub, [], txt, []).records
            assert record.symbol_only == ()

    def test_a_tagged_symbol_with_an_exchange_but_no_title_is_not_symbol_only(self) -> None:
        """A group with a symbol and an exchange but no title is an incomplete
        listing like any other, and its symbol is no symbol-only tag: the tag
        is a symbol that names no exchange (#1597)."""
        accession = "0000000008-25-000001"
        sub = [_sub(accession, "8", "10-K")]
        txt = [
            _txt(accession, "TradingSymbol", "FULL", dimh="0xa", dimn="1"),
            _txt(accession, "SecurityExchangeName", "NASDAQ", dimh="0xa", dimn="1"),
        ]
        dim = [_dim("0xa", "ClassOfStock=CommonClassA;")]
        parsed = parse_fsn(sub, [], txt, dim)
        [record] = parsed.records
        assert (record.incomplete_listings, record.symbol_only, record.listings) == (1, (), ())

    def test_two_different_values_for_one_fact_fail_that_accession(self) -> None:
        """Fail closed like `parse_cover_page`: never keep whichever came last."""
        good, bad = "0000000008-25-000001", "0000000009-25-000001"
        sub = [_sub(good, "8", "10-K"), _sub(bad, "9", "10-K")]
        txt = [
            _txt(good, "TradingSymbol", "SAME"),
            _txt(good, "TradingSymbol", "SAME"),  # a repeat with the same value is fine
            _txt(bad, "TradingSymbol", "ONE"),
            _txt(bad, "TradingSymbol", "TWO"),
        ]
        parsed = parse_fsn(sub, [], txt, [])
        assert [r.accession for r in parsed.records] == [good]
        assert [f.accession for f in parsed.failures] == [bad]

    @pytest.mark.parametrize("tag", ["TradingSymbol", "Security12bTitle", "SecurityExchangeName"])
    def test_a_replaced_byte_in_a_listing_value_fails_that_accession(self, tag: str) -> None:
        """#455: `_fsn_rows` turns an invalid byte into U+FFFD; a listing
        built from it would be a phantom ticker, so the accession fails."""
        good, bad = "0000000012-25-000001", "0000000013-25-000001"
        sub = [_sub(good, "12", "10-K"), _sub(bad, "13", "10-K")]
        values = {"Security12bTitle": "Common Stock", "TradingSymbol": "ABC"}
        values["SecurityExchangeName"] = "NYSE"
        txt = [_txt(good, t, v) for t, v in values.items()]
        txt += [_txt(bad, t, v + "\ufffd" if t == tag else v) for t, v in values.items()]
        parsed = parse_fsn(sub, [], txt, [])
        assert [r.accession for r in parsed.records] == [good]
        assert [f.accession for f in parsed.failures] == [bad]

    def test_two_different_share_values_for_one_class_fail_that_accession(self) -> None:
        good, bad = "0000000010-25-000001", "0000000011-25-000001"
        sub = [_sub(good, "10", "10-K"), _sub(bad, "11", "10-K")]
        num = [
            _num(good, "EntityCommonStockSharesOutstanding", "5", "20250131"),
            _num(good, "EntityCommonStockSharesOutstanding", "5", "20250131"),  # same value: fine
            _num(bad, "EntityCommonStockSharesOutstanding", "5", "20250131"),
            _num(bad, "EntityCommonStockSharesOutstanding", "6", "20250131"),
        ]
        parsed = parse_fsn(sub, num, [], [])
        assert [(r.accession, len(r.shares)) for r in parsed.records] == [(good, 1)]
        assert [f.accession for f in parsed.failures] == [bad]

    # --- #609: the 2026-10-02 backfill's FSN failures, from their exact row shapes

    @pytest.mark.parametrize("reverse", [False, True])
    def test_shares_at_several_ddates_keep_the_latest_per_member(self, reverse: bool) -> None:
        """#609 F1 (0001193125-26-029314): one undimensioned share fact per
        year-end, four ddates. The latest is kept, whatever the row order."""
        accession = "0001193125-26-029314"
        num = [
            _num(accession, SHARES_TAG, "39347991.0000", "20211231", dimh="0x00000000"),
            _num(accession, SHARES_TAG, "40547379.0000", "20221231", dimh="0x00000000"),
            _num(accession, SHARES_TAG, "58334115.0000", "20231231", dimh="0x00000000"),
            _num(accession, SHARES_TAG, "60203214.0000", "20241231", dimh="0x00000000"),
        ]
        parsed = parse_fsn([_sub(accession, "1", "10-K")], num[::-1] if reverse else num, [], [])
        assert parsed.failures == ()
        [record] = parsed.records
        assert record.shares == (FsnShare("", 60_203_214.0, date(2024, 12, 31)),)

    def test_equal_shares_at_two_ddates_keep_the_latest(self) -> None:
        """#609 F1 (0001104659-26-008700): the same value at two ddates."""
        accession = "0001104659-26-008700"
        num = [
            _num(accession, SHARES_TAG, "11924349.0000", "20250930", dimh="0x00000000"),
            _num(accession, SHARES_TAG, "11924349.0000", "20260131", dimh="0x00000000"),
        ]
        [record] = parse_fsn([_sub(accession, "1", "10-Q")], num, [], []).records
        assert record.shares == (FsnShare("", 11_924_349.0, date(2026, 1, 31)),)

    def test_the_latest_ddate_is_kept_per_class_member(self) -> None:
        accession = "0000000020-25-000001"
        dim = [_dim("hA", "ClassOfStock=CommonClassA;"), _dim("hB", "ClassOfStock=CommonClassB;")]
        num = [
            _num(accession, SHARES_TAG, "10", "20250131", dimh="hA", dimn="1"),
            _num(accession, SHARES_TAG, "11", "20241231", dimh="hA", dimn="1"),
            _num(accession, SHARES_TAG, "20", "20241130", dimh="hB", dimn="1"),
            _num(accession, SHARES_TAG, "21", "20241231", dimh="hB", dimn="1"),
        ]
        [record] = parse_fsn([_sub(accession, "20", "10-K")], num, [], dim).records
        assert set(record.shares) == {
            FsnShare("CommonClassA", 10.0, date(2025, 1, 31)),
            FsnShare("CommonClassB", 21.0, date(2024, 12, 31)),
        }

    def test_two_values_on_one_ddate_still_fail_that_accession(self) -> None:
        """#609 F2 (0001156039-15-000006): two values for one member on the
        same ddate. The rows carry nothing that picks one, so it fails."""
        good, bad = "0000000021-25-000001", "0001156039-15-000006"
        num = [
            _num(good, SHARES_TAG, "5", "20150331", dimh="0x00000000"),
            _num(bad, SHARES_TAG, "264905598.0000", "20150331", dimh="0x00000000"),
            _num(bad, SHARES_TAG, "264536271.0000", "20150331", dimh="0x00000000"),
        ]
        parsed = parse_fsn([_sub(good, "21", "10-Q"), _sub(bad, "1156039", "10-Q")], num, [], [])
        assert [r.accession for r in parsed.records] == [good]
        [failure] = parsed.failures
        assert failure.accession == bad
        assert "two share values for no class on 2015-03-31" in failure.error

    def test_a_same_ddate_conflict_on_an_older_ddate_still_fails(self) -> None:
        """Keeping the latest ddate never hides a same-ddate conflict on another."""
        accession = "0000000022-25-000001"
        num = [
            _num(accession, SHARES_TAG, "1", "20241231"),
            _num(accession, SHARES_TAG, "2", "20241231"),
            _num(accession, SHARES_TAG, "3", "20250131"),
        ]
        parsed = parse_fsn([_sub(accession, "22", "10-K")], num, [], [])
        assert parsed.records == ()
        assert [f.accession for f in parsed.failures] == [accession]

    def test_null_share_values_are_skipped(self) -> None:
        """#609 F3 (0001398344-26-005654): FSN's NULL (an xsi:nil fact) for two
        of four classes; DuckDB reads it as `None`."""
        accession = "0001398344-26-005654"
        hashes = {
            "0x2b31592a40793786882dc7aad7a31ed4": ("ClassAUnits", None),
            "0x31e4f22fb0955ced7f582087aae3e9b0": ("ClassMUnits", "48421.0000"),
            "0x5a55c399179290be6e7ef30689e0c3da": ("ClassSUnits", None),
            "0x84da7b0964c06bc1415d4381ab5eca6b": ("ClassIUnits", "181682064.0000"),
        }
        dim = [_dim(h, f"ClassOfStock={member};") for h, (member, _) in hashes.items()]
        num = [
            _null_num(accession, "20260228", v, dimh=h, dimn="1") for h, (_, v) in hashes.items()
        ]
        parsed = parse_fsn([_sub(accession, "1938649", "10-Q")], num, [], dim)
        assert parsed.failures == ()
        [record] = parsed.records
        assert set(record.shares) == {
            FsnShare("ClassMUnits", 48_421.0, date(2026, 2, 28)),
            FsnShare("ClassIUnits", 181_682_064.0, date(2026, 2, 28)),
        }

    @pytest.mark.parametrize("blank", [None, "", "  "])
    def test_a_null_older_row_beside_a_later_value_keeps_the_value(self, blank: str | None) -> None:
        """#609 F3 (0000833444-15-000032): NULL at 2015-03-31, a value at 2015-04-30."""
        accession = "0000833444-15-000032"
        num = [
            _null_num(accession, "20150331", blank, dimh="0x00000000"),
            _null_num(accession, "20150430", "421032334.0000", dimh="0x00000000"),
        ]
        [record] = parse_fsn([_sub(accession, "833444", "10-Q")], num, [], []).records
        assert record.shares == (FsnShare("", 421_032_334.0, date(2015, 4, 30)),)

    def test_only_null_share_values_give_no_shares(self) -> None:
        """#609 F3 (0001572910-15-000033): every share row NULL; the listing stays."""
        accession = "0001572910-15-000033"
        common = "0x171fa7700d0367d34c779c2b2e5f381e"
        subordinated = "0x93a3d34f885a6316c43698c0db6b2984"
        dim = [
            _dim(common, "ClassOfStock=CommonUnits;"),
            _dim(subordinated, "ClassOfStock=SubordinatedUnits;"),
        ]
        num = [
            _null_num(accession, "20150331", None, dimh=h, dimn="1") for h in (common, subordinated)
        ]
        txt = [
            _txt(accession, "Security12bTitle", "Common Units"),
            _txt(accession, "TradingSymbol", "PSXP"),
            _txt(accession, "SecurityExchangeName", "NYSE"),
        ]
        parsed = parse_fsn([_sub(accession, "1572910", "10-Q")], num, txt, dim)
        assert parsed.failures == ()
        [record] = parsed.records
        assert record.shares == ()
        assert [item.ticker for item in record.listings] == ["PSXP"]

    def test_titles_differing_only_by_a_lost_class_letter_space_are_one_title(self) -> None:
        """#609 F4 (0001193125-26-126955): FSN keeps two copies of a title, one
        with the NBSP dropped ("ClassA"). The spaced variant is kept."""
        accession = "0001193125-26-126955"
        units, common = "0x177ad7e2c2df26ff9e10a1111859f872", "0x64aab115e615b3ad72099cbe43ef2567"
        warrants = "0x8b4fd6841da661431963d15ddbf7a643"
        dim = [
            _dim(units, "ClassOfStock=CapitalUnits;"),
            _dim(common, "ClassOfStock=CommonClassA;"),
            _dim(warrants, "ClassOfStock=Warrant;"),
        ]
        unit_title = (
            "Units, each consisting of one share of Class A common stock and one-half of one "
            "redeemable warrant"
        )
        warrant_title = (
            "Warrants, each whole warrant exercisable for one share of Class A common stock, "
            "each at an exercise price of $11.50 per share"
        )

        def row(tag: str, value: str, dimh: str) -> dict[str, str]:
            return _txt(accession, tag, value, dimh=dimh, dimn="1")

        txt = [
            row("Security12bTitle", unit_title, units),
            row("Security12bTitle", "ClassA common stock, par value $0.0001 per share", common),
            row("Security12bTitle", warrant_title, warrants),
            row("Security12bTitle", unit_title.replace("Class A", "ClassA"), units),
            row("Security12bTitle", warrant_title.replace("Class A", "ClassA"), warrants),
            row("SecurityExchangeName", "NONE", units),
            row("SecurityExchangeName", "NONE", common),
            row("SecurityExchangeName", "NONE", warrants),
            row("TradingSymbol", "AEAEU", units),
            row("TradingSymbol", "AEAE", common),
            row("TradingSymbol", "AEAEW", warrants),
        ]
        parsed = parse_fsn([_sub(accession, "1", "10-K")], [], txt, dim)
        assert parsed.failures == ()
        [record] = parsed.records
        assert {item.ticker: item.title for item in record.listings} == {
            "AEAEU": unit_title,
            "AEAE": "Class A common stock, par value $0.0001 per share",
            "AEAEW": warrant_title,
        }

    @pytest.mark.parametrize("reverse", [False, True])
    def test_titles_differing_only_by_whitespace_keep_the_spaced_variant(
        self, reverse: bool
    ) -> None:
        """#609 F4 (0001688757-21-000028): "No ParValue" vs "No Par Value"."""
        accession = "0001688757-21-000028"
        titles = [
            _txt(accession, "Security12bTitle", "Common Shares, No ParValue"),
            _txt(accession, "Security12bTitle", "Common Shares, No Par Value"),
        ]
        txt = [
            _txt(accession, "SecurityExchangeName", "NASDAQ"),
            _txt(accession, "SecurityExchangeName", "NASDAQ"),
            _txt(accession, "TradingSymbol", "ESTA"),
            *(titles[::-1] if reverse else titles),
        ]
        num = [_num(accession, SHARES_TAG, "23547075.0000", "20210228", dimh="0x00000000")]
        parsed = parse_fsn([_sub(accession, "1688757", "10-K")], num, txt, [])
        assert parsed.failures == ()
        [record] = parsed.records
        assert record.listings == (CoverListing("Common Shares, No Par Value", "ESTA", "NASDAQ"),)

    def test_two_different_titles_still_fail_that_accession(self) -> None:
        accession = "0000000023-25-000001"
        txt = [
            _txt(accession, "Security12bTitle", "Common Stock"),
            _txt(accession, "Security12bTitle", "Preferred Stock"),
            _txt(accession, "TradingSymbol", "ABC"),
            _txt(accession, "SecurityExchangeName", "NYSE"),
        ]
        parsed = parse_fsn([_sub(accession, "23", "10-K")], [], txt, [])
        assert parsed.records == ()
        [failure] = parsed.failures
        assert "two values for Security12bTitle" in failure.error

    def test_symbols_are_still_compared_exactly(self) -> None:
        """Only titles are compared without whitespace: "BRK A" is not "BRKA"."""
        accession = "0000000024-25-000001"
        txt = [_txt(accession, "TradingSymbol", "BRK A"), _txt(accession, "TradingSymbol", "BRKA")]
        parsed = parse_fsn([_sub(accession, "24", "10-K")], [], txt, [])
        assert [f.accession for f in parsed.failures] == [accession]

    def test_title_with_no_symbol_is_not_a_listing(self) -> None:
        accession = "0000000007-25-000001"
        sub = [_sub(accession, "7", "10-K")]
        txt = [_txt(accession, "Security12bTitle", "Notes due 2030")]
        parsed = parse_fsn(sub, [], txt, [])
        [record] = parsed.records
        assert record.listings == ()

    def test_blank_sic_is_none(self) -> None:
        accession = "0000000008-25-000001"
        sub = [_sub(accession, "8", "8-K", sic="")]
        parsed = parse_fsn(sub, [], [], [])
        [record] = parsed.records
        assert record.sic is None


# --- recorded-fixture parity (skipped until the owner records FSN fixtures)
#
# `python -m tradepartner.cli_record fsn` writes each recorded period as
# `tests/fixtures/edgar/fsn/<period>/{sub,num,txt,dim}.tsv`. These two tests
# parse those files with `parse_fsn` and check the result against the
# already-recorded cover-page fixture for the *same* accession (T11's
# `filing_<label>_*.gz`) rather than hardcoding the real share counts again:
# the spec requires the two parses to agree on listings and per-class shares
# for a filing FSN and the cover page both cover.

EDGAR_FIXTURES = FIXTURES.parent


def _cover_page_parse(name: str, accession: str) -> object:
    from tradepartner.adapters.edgar import acceptance_times, parse_cover_page

    accepted = acceptance_times(
        json.loads((EDGAR_FIXTURES / "submissions_plain_issuer.json").read_text()),
        json.loads((EDGAR_FIXTURES / "submissions_dual_class.json").read_text()),
    )[accession]
    import gzip

    with gzip.open(EDGAR_FIXTURES / name, "rb") as handle:
        document = handle.read()
    return parse_cover_page(document, accession=accession, accepted_at=accepted)


def _read_period_tsvs(period_dir: Path) -> tuple[list[dict[str, str]], ...]:
    def rows(name: str) -> list[dict[str, str]]:
        # FSN members are unquoted TSV: split on tabs only, never csv quoting.
        lines = (period_dir / name).read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"), strict=False)) for line in lines[1:]]

    return rows("sub.tsv"), rows("num.tsv"), rows("txt.tsv"), rows("dim.tsv")


def test_plain_issuer_fsn_matches_cover_page_2025_10() -> None:
    accession = "0000320193-25-000079"
    sub, num, txt, dim = _read_period_tsvs(FIXTURES / "2025_10")
    parsed = parse_fsn(sub, num, txt, dim)
    [record] = [r for r in parsed.records if r.accession == accession]

    cover = _cover_page_parse("filing_plain_issuer_aapl-20250927.htm.gz", accession)
    assert {(item.ticker, item.exchange) for item in record.listings} == {
        (item.ticker, item.exchange)
        for item in cover.cover.listings  # type: ignore[attr-defined]
    }
    assert {s.class_member: s.value for s in record.shares} == {
        f.class_member: f.value
        for f in cover.facts  # type: ignore[attr-defined]
    }
    # FSN's ddate is the cover date rounded to month end, never the cover's own
    # date (#224, quant-auditor): pinned so T11e cannot assume otherwise.
    [cover_date] = {f.as_of_date for f in cover.facts}  # type: ignore[attr-defined]
    assert {s.as_of_date for s in record.shares} == {date(2025, 10, 31)}
    assert cover_date == date(2025, 10, 17) and cover_date != date(2025, 10, 31)


def test_dual_class_fsn_matches_cover_page_2026_02() -> None:
    accession = "0001652044-26-000018"
    sub, num, txt, dim = _read_period_tsvs(FIXTURES / "2026_02")
    parsed = parse_fsn(sub, num, txt, dim)
    [record] = [r for r in parsed.records if r.accession == accession]

    cover = _cover_page_parse("filing_dual_class_goog-20251231.htm.gz", accession)
    assert {(item.ticker, item.exchange) for item in record.listings} == {
        (item.ticker, item.exchange)
        for item in cover.cover.listings  # type: ignore[attr-defined]
    }
    assert {s.class_member: s.value for s in record.shares} == {
        f.class_member: f.value
        for f in cover.facts  # type: ignore[attr-defined]
    }
    # FSN's ddate is the cover date rounded to month end (see the 2025_10 test).
    [cover_date] = {f.as_of_date for f in cover.facts}  # type: ignore[attr-defined]
    assert {s.as_of_date for s in record.shares} == {date(2026, 1, 31)}
    assert cover_date == date(2026, 1, 28) and cover_date != date(2026, 1, 31)
    # FSN drops the filing's non-breaking space (`Class&#160;A` arrives as
    # `ClassA`); parse_fsn restores it, so titles match the filing's and
    # ingest's class-letter rule still finds the class (#224, recorded 2026_02).
    assert {item.title for item in record.listings} == {
        " ".join(item.title.replace("\xa0", " ").split())
        for item in cover.cover.listings  # type: ignore[attr-defined]
    }
    [googl] = [item for item in record.listings if item.ticker == "GOOGL"]
    match = ingest._TITLE_CLASS.search(googl.title.lower())
    assert match is not None and match.group(1) == "a"


def test_fsn_records_and_caches_carry_no_timestamp(tmp_path: Path) -> None:
    """No look-ahead (plan T11c): Alphabet's recorded 0001652044-26-000018 has
    FSN `accepted` 2026-02-04 21:56 (Eastern), `filed` 20260205 and `period`
    20251231, while the submissions acceptance is 2026-02-05T02:56:03Z. None
    of FSN's own dates may reach a record or the per-CIK cache: stamping is
    T11d's, from the submissions acceptance, at read time."""
    accession = "0001652044-26-000018"
    sub, num, txt, dim = _read_period_tsvs(FIXTURES / "2026_02")
    [sub_row] = [row for row in sub if row["adsh"] == accession]
    assert (sub_row["accepted"], sub_row["filed"]) == ("2026-02-04 21:56:00.0", "20260205")

    parsed = parse_fsn(sub, num, txt, dim)
    [record] = [r for r in parsed.records if r.accession == accession]
    for item in (record, *record.shares):
        for field in dataclasses.fields(item):
            assert not isinstance(getattr(item, field.name), datetime), field.name

    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as archive:
        for member in ("sub.tsv", "num.tsv", "txt.tsv", "dim.tsv"):
            archive.write(FIXTURES / "2026_02" / member, member)
    settings = _settings(tmp_path)
    router = _router_with_fsn("2026_02", zips={"2026_02": zip_bytes.getvalue()})
    _source_ready(settings, router)._ensure_fsn()
    cache = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "0001652044.json"
    text = cache.read_text()
    for fsn_date in ("2026-02-04", "21:56", "20260205", "20251231", "2025-12-31"):
        assert fsn_date not in text, fsn_date


@pytest.mark.parametrize(
    ("raw", "restored"),
    [
        ("ClassA Common Stock, $0.001 par value", "Class A Common Stock, $0.001 par value"),
        ("ClassB common stock", "Class B common stock"),
        ("Class C Capital Stock", "Class C Capital Stock"),  # already spaced
        ("Classic Common Stock", "Classic Common Stock"),  # not a class letter
        ("SubclassA Units", "SubclassA Units"),  # not at a word start
        ("ClassAB Units", "ClassAB Units"),  # two letters: not a class letter
    ],
)
def test_fsn_titles_get_the_class_letter_space_back(raw: str, restored: str) -> None:
    """Synthetic: FSN strips the NBSP between `Class` and its letter."""
    assert restore_class_letter_space(raw) == restored


# --- EdgarFilingSource._ensure_fsn ------------------------------------------


def _settings(tmp_path: Path, **edgar: object) -> Settings:
    return edgar_settings(tmp_path, fsn_first_year=2015, **edgar)


def _router_with_fsn(*periods: str, zips: Mapping[str, bytes] | None = None) -> EdgarRouter:
    router = EdgarRouter()
    router.add(FSN_PAGE_URL, _fsn_page_html(*periods))
    for period, body in (zips or {}).items():
        router.add(_fsn_zip_url(period), body)
    return router


def _source_ready(settings: Settings, router: EdgarRouter) -> EdgarFilingSource:
    """A source whose `filing_index` has "run" (T11c only needs the flag;
    the index scan itself is `test_edgar_source.py`'s job)."""
    source = EdgarFilingSource(settings, client=router.client())
    source._filing_index_ran = True
    return source


def _one_period_zip(accession: str, cik: str, form: str = "10-K") -> bytes:
    return _fsn_zip_bytes(
        [_sub(accession, cik, form)],
        [_num(accession, "EntityCommonStockSharesOutstanding", "100", "20250101")],
        [
            _txt(accession, "Security12bTitle", "Common Stock"),
            _txt(accession, "TradingSymbol", "TCK"),
            _txt(accession, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )


class TestEnsureFsn:
    def test_raises_before_filing_index_has_run(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        router = _router_with_fsn("2015q1")
        source = EdgarFilingSource(settings, client=router.client())
        with pytest.raises(RuntimeError, match="filing_index"):
            source._ensure_fsn()

    def test_fsn_first_year_bounds_the_periods_fetched(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        zips = {
            "2014q4": _one_period_zip("0000000010-14-000001", "10"),
            "2015q1": _one_period_zip("0000000011-15-000001", "11"),
        }
        router = _router_with_fsn("2014q4", "2015q1", zips=zips)
        source = _source_ready(settings, router)
        source._ensure_fsn()
        fetched = {u for u in router.urls if u.endswith("_notes.zip")}
        assert fetched == {_fsn_zip_url("2015q1")}

    def test_builds_a_per_cik_cache_and_a_committed_false_manifest(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        accession, cik = "0000000011-15-000001", "0000000011"
        router = _router_with_fsn("2015q1", zips={"2015q1": _one_period_zip(accession, "11")})
        source = _source_ready(settings, router)
        source._ensure_fsn()

        cache_path = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / f"{cik}.json"
        data = json.loads(cache_path.read_text())
        assert data["records"][accession]["listings"] == [["Common Stock", "TCK", "NASDAQ"]]

        manifest_path = (
            Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests" / "2015q1.json"
        )
        manifest = json.loads(manifest_path.read_text())
        assert manifest["accessions_extracted"] == [accession]
        assert manifest["accessions_failed"] == []
        assert manifest["committed"] is False

    def test_a_cached_period_makes_no_second_get(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        methods: list[str] = []
        zip_bytes = _one_period_zip("0000000011-15-000001", "11")

        def zip_route(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return httpx.Response(200, content=zip_bytes, headers={"ETag": '"same"'})

        router = _router_with_fsn("2015q1")
        router.add(_fsn_zip_url("2015q1"), zip_route)
        source = _source_ready(settings, router)
        source._ensure_fsn()
        assert methods == ["GET"]

        source._fsn_ready = False  # simulate a fresh instance on the next run
        source._ensure_fsn()
        assert "GET" not in methods[1:]  # the period's zip is not re-fetched, only re-HEAD-ed

    def test_duplicate_accession_across_periods_keeps_the_first(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        accession, cik = "0000000011-15-000001", "11"
        zips = {
            "2015q1": _one_period_zip(accession, cik),
            "2015q2": _one_period_zip(accession, cik),
        }
        router = _router_with_fsn("2015q1", "2015q2", zips=zips)
        source = _source_ready(settings, router)
        source._ensure_fsn()
        assert source.fsn_duplicates == 1

    def test_a_header_form_is_cached_for_its_sic(self, tmp_path: Path) -> None:
        """An 8-K is not a cover-page form but is in `edgar.header_forms`, so
        T11d can serve its SIC without a ranged header request."""
        settings = _settings(tmp_path)
        accession, cik = "0000000014-15-000001", "0000000014"
        zips = {"2015q1": _one_period_zip(accession, "14", form="8-K")}
        source = _source_ready(settings, _router_with_fsn("2015q1", zips=zips))
        source._ensure_fsn()
        cache_path = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / f"{cik}.json"
        assert accession in cache_path.read_text()

    def test_form_in_neither_list_is_extracted_but_not_cached(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        accession, cik = "0000000012-15-000001", "0000000012"
        zip_bytes = _one_period_zip(accession, "12", form="DEF 14A")
        router = _router_with_fsn("2015q1", zips={"2015q1": zip_bytes})
        source = _source_ready(settings, router)
        source._ensure_fsn()
        cache_path = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / f"{cik}.json"
        assert not cache_path.exists()
        manifest_path = (
            Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests" / "2015q1.json"
        )
        manifest = json.loads(manifest_path.read_text())
        assert manifest["accessions_extracted"] == [accession]

    def test_a_404_for_a_listed_period_propagates(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        router = _router_with_fsn("2015q1")
        router.add(_fsn_zip_url("2015q1"), 404)
        source = _source_ready(settings, router)
        with pytest.raises(httpx.HTTPStatusError):
            source._ensure_fsn()

    def test_the_zip_is_deleted_after_extraction(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        zips = {"2015q1": _one_period_zip("0000000011-15-000001", "11")}
        router = _router_with_fsn("2015q1", zips=zips)
        source = _source_ready(settings, router)
        source._ensure_fsn()
        assert not (Path(settings.edgar.cache_dir) / "fsn" / "2015q1_notes.zip").exists()

    def test_a_reissued_period_is_reported_not_reextracted(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        accession, cik = "0000000011-15-000001", "11"
        methods: list[str] = []
        zip_bytes = _one_period_zip(accession, cik)

        def zip_route(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            headers = {"ETag": '"new"'} if len(methods) > 1 else {"ETag": '"old"'}
            return httpx.Response(200, content=zip_bytes, headers=headers)

        router = _router_with_fsn("2015q1")
        router.add(_fsn_zip_url("2015q1"), zip_route)
        source = _source_ready(settings, router)
        source._ensure_fsn()
        assert methods == ["GET"]

        # A fresh instance sees the same, still-listed period with a changed
        # `ETag` (a re-issued file): one `HEAD` detects it, no new `GET`.
        source2 = _source_ready(settings, router)
        source2._ensure_fsn()
        assert methods == ["GET", "HEAD"]
        assert source2.fsn_reissued == 1

    def test_a_cached_period_no_longer_listed_counts_as_rolled_up(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        accession, cik = "0000000011-15-000001", "11"
        router = _router_with_fsn("2015q1", zips={"2015q1": _one_period_zip(accession, cik)})
        source = _source_ready(settings, router)
        source._ensure_fsn()

        # Second run: the page no longer lists 2015q1 (rolled into a quarter).
        rolled_router = _router_with_fsn(
            "2015q2", zips={"2015q2": _one_period_zip("0000000013-15-000001", "13")}
        )
        rolled_settings = _settings(tmp_path)
        source2 = _source_ready(rolled_settings, rolled_router)
        source2._ensure_fsn()
        assert source2.fsn_reissued == 1

    @pytest.mark.parametrize(
        "head",
        [
            httpx.Response(404),
            httpx.Response(200, headers={"Content-Length": "10"}),
            httpx.ConnectError("connection reset"),
        ],
        ids=["head-fails", "no-etag-or-last-modified", "head-transport-error"],
    )
    def test_a_period_whose_reissue_cannot_be_checked_is_counted(
        self, tmp_path: Path, head: httpx.Response | Exception
    ) -> None:
        """No usable validator (a failed `HEAD` at the HTTP or network level,
        or neither `ETag` nor `Last-Modified`) is counted on
        `.fsn_reissue_undetected` so the run message says re-issues went
        unchecked; it never aborts the run and is never a re-issue. A tiny
        `retry_backoff_seconds` keeps the transport-error case's retries
        (#554) from sleeping for real."""
        settings = _settings(tmp_path, retry_backoff_seconds=0.001)
        zip_bytes = _one_period_zip("0000000011-15-000001", "11")

        def zip_route(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                if isinstance(head, Exception):
                    raise head
                return head
            return httpx.Response(200, content=zip_bytes, headers={"ETag": '"v1"'})

        router = _router_with_fsn("2015q1")
        router.add(_fsn_zip_url("2015q1"), zip_route)
        _source_ready(settings, router)._ensure_fsn()
        source2 = _source_ready(settings, router)
        source2._ensure_fsn()
        assert (source2.fsn_reissue_undetected, source2.fsn_reissued) == (1, 0)

    def test_manifest_failures_are_the_served_forms_only(self, tmp_path: Path) -> None:
        """T11f's failure policy counts accessions this adapter serves: a failing
        6-K (never cached) stays out of the manifest, a failing 10-K is in it."""
        settings = _settings(tmp_path)
        kept, unserved = "0000000021-15-000001", "0000000022-15-000001"
        zip_bytes = _fsn_zip_bytes(
            [_sub(kept, "21", "10-K"), _sub(unserved, "22", "6-K")],
            [
                _num(kept, "EntityCommonStockSharesOutstanding", "not-a-number", "20150131"),
                _num(unserved, "EntityCommonStockSharesOutstanding", "bad", "20150131"),
            ],
            [],
            [],
        )
        source = _source_ready(settings, _router_with_fsn("2015q1", zips={"2015q1": zip_bytes}))
        source._ensure_fsn()
        manifest_path = (
            Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests" / "2015q1.json"
        )
        failed = json.loads(manifest_path.read_text())["accessions_failed"]
        assert [(f["accession"], f["base_form"]) for f in failed] == [(kept, "10-K")]

    def test_manifest_records_served_accessions_and_incomplete_listings(
        self, tmp_path: Path
    ) -> None:
        """T11f's failure share needs served-form accessions as its denominator,
        and the incomplete-listing count must outlive the run that extracted it."""
        settings = _settings(tmp_path)
        served, unserved = "0000000031-15-000001", "0000000032-15-000001"
        zip_bytes = _fsn_zip_bytes(
            [_sub(served, "31", "10-K"), _sub(unserved, "32", "6-K")],
            [],
            [_txt(served, "TradingSymbol", "NOEX"), _txt(unserved, "TradingSymbol", "X")],
            [],
        )
        source = _source_ready(settings, _router_with_fsn("2015q1", zips={"2015q1": zip_bytes}))
        source._ensure_fsn()
        manifest_path = (
            Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests" / "2015q1.json"
        )
        manifest = json.loads(manifest_path.read_text())
        assert manifest["accessions_served"] == [served]
        assert manifest["incomplete_listings"] == 1  # the served 10-K only
        assert source.fsn_incomplete_listings == 1

    def test_an_unreadable_cik_cache_stops_extraction(self, tmp_path: Path) -> None:
        """A per-CIK cache that exists but does not load must not be silently
        replaced: its earlier periods have manifests and would never be
        re-extracted, so that CIK's older records would be lost for good."""
        settings = _settings(tmp_path)
        root = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}"
        root.mkdir(parents=True)
        (root / "0000000011.json").write_text("{truncated")
        zips = {"2015q1": _one_period_zip("0000000011-15-000001", "11")}
        source = _source_ready(settings, _router_with_fsn("2015q1", zips=zips))
        with pytest.raises(ValueError, match=r"0000000011\.json"):
            source._ensure_fsn()
        assert (root / "0000000011.json").read_text() == "{truncated"

    @pytest.mark.parametrize("stray", ["notes.json", "2015q1.json"])
    def test_a_stray_or_foreign_manifest_is_ignored(self, tmp_path: Path, stray: str) -> None:
        """A file in the manifests directory that is not a period's manifest
        (a stray name, or valid JSON that is not an object) neither aborts
        the run nor counts as extracted: the period is extracted again."""
        settings = _settings(tmp_path)
        manifests = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}" / "manifests"
        manifests.mkdir(parents=True)
        (manifests / stray).write_text("[]")
        zips = {"2015q1": _one_period_zip("0000000011-15-000001", "11")}
        source = _source_ready(settings, _router_with_fsn("2015q1", zips=zips))
        source._ensure_fsn()
        manifest = json.loads((manifests / "2015q1.json").read_text())
        assert manifest["accessions_extracted"] == ["0000000011-15-000001"]


# --- #455: FSN members are not always valid UTF-8 ----------------------------

_TXT_HEADER = b"adsh\ttag\tdimh\tdimn\tcoreg\tvalue\n"


def _write_txt_tsv(path: Path, *rows: bytes) -> Path:
    # Enough valid rows ahead of the bad one that DuckDB's sniffer sample
    # does not see it, as in the real 2015 txt.tsv (line 6850).
    filler = b"".join(b"0000000001-15-%06d\tOther\t0x00\t0\t\tok\n" % i for i in range(5000))
    path.write_bytes(_TXT_HEADER + filler + b"".join(rows))
    return path


def test_fsn_rows_replaces_a_non_utf8_byte_instead_of_failing(tmp_path: Path) -> None:
    # 0x92 is a Windows-1252 apostrophe, as in SandRidge's 10-K/A note.
    path = _write_txt_tsv(
        tmp_path / "txt.tsv",
        b"0001349436-15-000028\tAmendmentDescription\t0x00\t0\t\tthe Company\x92s note\n",
        b"0001349436-15-000029\tSecurity12bTitle\t0x00\t0\t\tCommon Stock\x92\n",
    )
    rows = _fsn_rows(
        path,
        ("adsh", "tag", "value"),
        where="tag IN ('Security12bTitle', 'AmendmentDescription')",
    )
    assert [row["value"] for row in rows] == ["the Company\ufffds note", "Common Stock\ufffd"]


def test_fsn_rows_keeps_valid_utf8_unchanged(tmp_path: Path) -> None:
    value = "Soci\u00e9t\u00e9 G\u00e9n\u00e9rale \u2019A\u2019 Shares \u2014 \u20ac1"
    path = _write_txt_tsv(
        tmp_path / "txt.tsv",
        b"0000000002-15-000001\tSecurity12bTitle\t0x00\t0\t\t" + value.encode() + b"\n",
    )
    rows = _fsn_rows(path, ("adsh", "value"), where="tag = 'Security12bTitle'")
    assert rows == [{"adsh": "0000000002-15-000001", "value": value}]


# --- #498: FSN txt.tsv values can carry unquoted tabs -------------------------


def test_fsn_rows_folds_a_values_embedded_tabs_into_the_last_column(tmp_path: Path) -> None:
    # As SandRidge's 2015q1 note: 29 tabs on a 20-column line, all in `value`.
    path = _write_txt_tsv(
        tmp_path / "txt.tsv",
        b"0001349436-15-000028\tAmendmentDescription\t0x00\t0\t\tNOTE\t\t\tSandRidge\tInc.\n",
        b"0001349436-15-000029\tTradingSymbol\t0x00\t0\t\tSD\n",
    )
    rows = _fsn_rows(
        path,
        ("adsh", "tag", "value"),
        where="tag IN ('AmendmentDescription', 'TradingSymbol')",
        free_text="value",
    )
    assert [row["value"] for row in rows] == [
        "NOTE\ufffd\ufffd\ufffdSandRidge\ufffdInc.",
        "SD",
    ]


def test_a_listing_value_with_an_embedded_tab_fails_that_accession(tmp_path: Path) -> None:
    good, bad = "0000000014-15-000001", "0000000015-15-000001"
    path = _write_txt_tsv(
        tmp_path / "txt.tsv",
        *(
            f"{adsh}\t{tag}\t0x00\t0\t\t{value}\n".encode()
            for adsh in (good, bad)
            for tag, value in (
                ("Security12bTitle", "Common Stock"),
                ("TradingSymbol", "AB\tC" if adsh == bad else "ABC"),
                ("SecurityExchangeName", "NYSE"),
            )
        ),
    )
    txt = _fsn_rows(
        path,
        ("adsh", "tag", "dimh", "dimn", "coreg", "value"),
        where="tag IN ('Security12bTitle', 'TradingSymbol', 'SecurityExchangeName')",
        free_text="value",
    )
    sub = [_sub(good, "14", "10-K"), _sub(bad, "15", "10-K")]
    parsed = parse_fsn(sub, [], txt, [])
    assert [r.accession for r in parsed.records] == [good]
    assert [f.accession for f in parsed.failures] == [bad]
    [record] = parsed.records
    assert [listing.ticker for listing in record.listings] == ["ABC"]


def test_fsn_rows_leaves_well_formed_lines_byte_for_byte(tmp_path: Path) -> None:
    """The fold is a no-op without surplus fields: CRLF and LF endings, an
    empty last field and a bare quote all survive the rewrite unchanged."""
    body = (
        _TXT_HEADER
        + b"0000000016-15-000001\tTradingSymbol\t0x00\t0\t\tABC\r\n"
        + b"0000000016-15-000001\tSecurity12bTitle\t0x00\t0\t\t\n"
        + b'0000000016-15-000001\tOther\t0x00\t0\t\tsays "hi\n'
    )
    path = tmp_path / "txt.tsv"
    path.write_bytes(body)
    _fsn_normalize_member(path, free_text="value")  # DuckDB cannot sniff mixed endings
    assert path.read_bytes() == body


def test_fsn_rows_still_fails_a_line_with_too_few_fields(tmp_path: Path) -> None:
    path = _write_txt_tsv(tmp_path / "txt.tsv", b"0000000017-15-000001\tTradingSymbol\tABC\n")
    with pytest.raises(duckdb.Error):
        _fsn_rows(path, ("adsh", "value"), free_text="value")


def test_fsn_rows_does_not_fold_a_member_whose_last_column_is_not_free_text(
    tmp_path: Path,
) -> None:
    """Surplus fields in `sub`/`num`/`dim` would shift into a column that is
    not free text, so they still fail the read loudly."""
    path = _write_txt_tsv(
        tmp_path / "num.tsv", b"0000000018-15-000001\tShares\t0x00\t0\t\t100\t5\n"
    )
    with pytest.raises(duckdb.Error):
        _fsn_rows(path, ("adsh", "value"))


def test_fsn_rows_refuses_to_fold_into_a_column_the_header_lacks(tmp_path: Path) -> None:
    path = tmp_path / "txt.tsv"
    path.write_bytes(b"adsh\ttext\tfootnote\n0000000019-15-000001\tABC\t\n")
    with pytest.raises(ValueError, match="no 'value' column to fold tabs into"):
        _fsn_rows(path, ("adsh", "text"), free_text="value")


def test_ensure_fsn_extracts_a_period_whose_txt_note_carries_tabs(tmp_path: Path) -> None:
    """End to end, as the 2015q1 backfill: a SandRidge-like note with literal
    tabs no longer fails the period, and a listing value with one fails only
    its own accession (counted in the manifest, never cached)."""
    good, noted, bad = "0000000041-15-000001", "0001349436-15-000028", "0000000043-15-000001"
    txt = [
        _txt(noted, "AmendmentDescription", "EXPLANATORY NOTE\t\t\tSandRidge\tEnergy"),
        *(
            _txt(adsh, tag, value)
            for adsh, symbol in ((good, "TCK"), (noted, "SD"), (bad, "AB\tC"))
            for tag, value in (
                ("Security12bTitle", "Common Stock"),
                ("TradingSymbol", symbol),
                ("SecurityExchangeName", "New York Stock Exchange"),
            )
        ),
    ]
    zip_bytes = _fsn_zip_bytes(
        [_sub(good, "41", "10-K"), _sub(noted, "1349436", "10-K/A"), _sub(bad, "43", "10-K")],
        [_num(good, "EntityCommonStockSharesOutstanding", "100", "20141231")],
        txt,
        [],
    )
    settings = _settings(tmp_path)
    source = _source_ready(settings, _router_with_fsn("2015q1", zips={"2015q1": zip_bytes}))
    source._ensure_fsn()
    root = Path(settings.edgar.cache_dir) / "fsn" / f"v{FSN_VERSION}"
    manifest = json.loads((root / "manifests" / "2015q1.json").read_text())
    assert manifest["accessions_extracted"] == [good, noted]
    assert [f["accession"] for f in manifest["accessions_failed"]] == [bad]
    cached = json.loads((root / "0001349436.json").read_text())
    assert cached["records"][noted]["listings"] == [["Common Stock", "SD", "NYSE"]]
    assert not (root / "0000000043.json").exists()


def test_fsn_rows_folds_dim_tabs_into_segments_not_the_last_column(tmp_path: Path) -> None:
    # As 2025_07 dim.tsv: an InvestmentIdentifier segment carries a tab and
    # `segt`, the last column, is a flag.
    path = tmp_path / "dim.tsv"
    filler = b"".join(b"0x%032x\tClassOfStock=Common;\t0\n" % i for i in range(5000))
    path.write_bytes(
        b"dimhash\tsegments\tsegt\n"
        + filler
        + b'0xc61f\t"InvestmentIdentifier=Debt, Inc.,\tSenior Secured;"\t0\n'
    )
    rows = _fsn_rows(
        path, ("dimhash", "segments", "segt"), where="dimhash = '0xc61f'", free_text="segments"
    )
    assert rows == [
        {
            "dimhash": "0xc61f",
            "segments": '"InvestmentIdentifier=Debt, Inc.,\ufffdSenior Secured;"',
            "segt": "0",
        }
    ]


@pytest.mark.parametrize(
    "segments",
    [
        "ClassOfStock=Common\ufffdClassA;",
        # quant-auditor on #504: a tab beside the axis key would otherwise
        # read as another axis and drop the class's shares silently.
        "ClassOfStock=CommonClassA;\ufffd",
        "\ufffdClassOfStock=CommonClassA;",
    ],
)
def test_a_class_member_with_a_folded_tab_fails_that_accession(segments: str) -> None:
    good, bad = "0000000016-15-000001", "0000000017-15-000001"
    sub = [_sub(good, "16", "10-K"), _sub(bad, "17", "10-K")]
    num = [
        _num(good, "EntityCommonStockSharesOutstanding", "5", "20150131", dimh="0xaa", dimn="1"),
        _num(bad, "EntityCommonStockSharesOutstanding", "5", "20150131", dimh="0xbb", dimn="1"),
    ]
    dim = [
        _dim("0xaa", "ClassOfStock=CommonClassA;"),
        _dim("0xbb", segments),
    ]
    parsed = parse_fsn(sub, num, [], dim)
    assert [r.accession for r in parsed.records] == [good]
    assert [f.accession for f in parsed.failures] == [bad]


# --- #1541 (T178): listings under the exchange axis ---------------------------

#: One real `dim.tsv` row, verbatim (FSN period 2025_10, read 2026-10-11): FSN
#: spells `dei:EntityListingsExchangeAxis` as `EntityListingsExchange`, the
#: MIC member unprefixed.
REAL_EXCHANGE_DIM = ("0x1dc0af108c94fa3ada3018ad13966aeb", "EntityListingsExchange=XNAS;")
#: A real 2025_10 row with both axes, the class segment first.
REAL_BOTH_DIM = (
    "0x082f9f4ab49f2c8aa8b2e1305746ee37",
    "ClassOfStock=CommonStock;EntityListingsExchange=XNYS;",
)


def _listing_rows(adsh: str, dimh: str, title: str, symbol: str, exchange: str) -> list[Any]:
    dimn = "1" if dimh else "0"
    return [
        _txt(adsh, "Security12bTitle", title, dimh=dimh, dimn=dimn),
        _txt(adsh, "TradingSymbol", symbol, dimh=dimh, dimn=dimn),
        _txt(adsh, "SecurityExchangeName", exchange, dimh=dimh, dimn=dimn),
    ]


class TestParseFsnExchangeAxis:
    """The spec amendment's "Parsers" acceptance, FSN rows."""

    adsh = "0001013462-19-000023"

    def _parse(
        self, txt: list[Any], dim: list[Any], num: list[Any] | None = None, *, on: bool
    ) -> Any:
        sub = [_sub(self.adsh, "1013462", "10-Q")]
        return parse_fsn(sub, num or [], txt, dim, exchange_axis=on)

    def test_the_exchange_axis_alone_yields_the_listing(self) -> None:
        dimh, segments = REAL_EXCHANGE_DIM
        txt = _listing_rows(self.adsh, dimh, "Common Stock", "ANSS", "NASDAQ")
        [record] = self._parse(txt, [_dim(dimh, segments)], on=True).records
        assert record.listings == (CoverListing("Common Stock", "ANSS", "NASDAQ"),)
        assert record.exchange_axis_mismatch == 0
        [off] = self._parse(txt, [_dim(dimh, segments)], on=False).records
        assert off.listings == ()  # today's parse: the axis drops the listing

    def test_the_exchange_axis_beside_class_of_stock_keeps_the_class_member(self) -> None:
        dimh, segments = REAL_BOTH_DIM
        txt = _listing_rows(self.adsh, dimh, "Common Stock", "IBM", "NYSE")
        num = [_num(self.adsh, SHARES_TAG, "885941232", "20190630", dimh=dimh, dimn="2")]
        [record] = self._parse(txt, [_dim(dimh, segments)], num, on=True).records
        assert record.listings == (CoverListing("Common Stock", "IBM", "NYSE"),)
        assert record.shares == (FsnShare("CommonStock", 885_941_232, date(2019, 6, 30)),)
        [off] = self._parse(txt, [_dim(dimh, segments)], num, on=False).records
        assert (off.listings, off.shares) == ((), ())

    def test_the_group_key_separates_two_exchanges_of_one_class(self) -> None:
        nyse, chicago = "0xa1", "0xa2"
        dim = [
            _dim(nyse, "ClassOfStock=CommonStock;EntityListingsExchange=XNYS;"),
            _dim(chicago, "ClassOfStock=CommonStock;EntityListingsExchange=XCHI;"),
        ]
        txt = _listing_rows(self.adsh, nyse, "Common Stock", "ABBV", "NYSE")
        txt += _listing_rows(self.adsh, chicago, "Common Stock", "ABBV", "CHX")
        parsed = self._parse(txt, dim, on=True)
        assert parsed.failures == ()  # never "two values for SecurityExchangeName"
        [record] = parsed.records
        assert [(i.ticker, i.exchange) for i in record.listings] == [
            ("ABBV", "NYSE"),
            ("ABBV", "NYSE_CHICAGO"),
        ]

    def test_an_undimensioned_and_an_exchange_axis_share_count_collapse_when_equal(
        self,
    ) -> None:
        dimh, segments = REAL_EXCHANGE_DIM
        num = [
            _num(self.adsh, SHARES_TAG, "84100767", "20190731"),
            _num(self.adsh, SHARES_TAG, "84100767", "20190731", dimh=dimh, dimn="1"),
        ]
        [record] = self._parse([], [_dim(dimh, segments)], num, on=True).records
        assert record.shares == (FsnShare("", 84_100_767, date(2019, 7, 31)),)
        num[1] = _num(self.adsh, SHARES_TAG, "84100768", "20190731", dimh=dimh, dimn="1")
        parsed = self._parse([], [_dim(dimh, segments)], num, on=True)
        assert [f.accession for f in parsed.failures] == [self.adsh]

    @pytest.mark.parametrize(
        "segments",
        ["EntityListingsExchange=XN�AS;", "EntityListingsExchange=XNAS;�"],
    )
    def test_an_undecodable_byte_in_an_exchange_segment_fails_closed(self, segments: str) -> None:
        txt = _listing_rows(self.adsh, "0xbb", "Common Stock", "ANSS", "NASDAQ")
        parsed = self._parse(txt, [_dim("0xbb", segments)], on=True)
        assert [f.accession for f in parsed.failures] == [self.adsh]
        assert "undecodable byte" in parsed.failures[0].error
        # today's parse drops the row instead, and keeps the accession
        assert [
            r.listings for r in self._parse(txt, [_dim("0xbb", segments)], on=False).records
        ] == [()]

    def test_a_member_naming_another_exchange_is_counted_and_the_value_wins(self) -> None:
        dim = [
            _dim("0xc1", "EntityListingsExchange=XNYS;"),
            _dim("0xc2", "EntityListingsExchange=NYSE;"),
        ]
        txt = _listing_rows(self.adsh, "0xc1", "Common Stock", "SO", "NASDAQ")
        txt += _listing_rows(self.adsh, "0xc2", "Preferred Stock", "SOP", "NASDAQ")  # no MIC
        [record] = self._parse(txt, dim, on=True).records
        assert [(i.ticker, i.exchange) for i in record.listings] == [
            ("SO", "NASDAQ"),
            ("SOP", "NASDAQ"),
        ]
        assert record.exchange_axis_mismatch == 1

    def test_another_axis_beside_the_exchange_axis_is_still_excluded(self) -> None:
        dim = [_dim("0xd1", "EntityListingsExchange=XNYS;LegalEntity=RZC;")]
        txt = _listing_rows(self.adsh, "0xd1", "Notes", "RZC", "NYSE")
        [record] = self._parse(txt, dim, on=True).records
        assert record.listings == ()


@pytest.mark.parametrize("period", ["2025_10", "2026_02"])
def test_recorded_periods_parse_the_same_with_the_switch_on(period: str) -> None:
    """The recorded filings tag no exchange axis: the switch changes nothing."""
    sub, num, txt, dim = _read_period_tsvs(FIXTURES / period)
    assert parse_fsn(sub, num, txt, dim, exchange_axis=True) == parse_fsn(sub, num, txt, dim)


def test_fsn_and_the_cover_page_agree_on_the_ansys_shape() -> None:
    """With the switch on, both parsers read the spec's ANSYS shape alike."""
    from tradepartner.adapters.edgar import parse_cover_page

    document = (EDGAR_FIXTURES / "cover_exchange_axis" / "ansys_10q_2019q2.htm").read_bytes()
    cover = parse_cover_page(
        document,
        accession="0001013462-19-000023",
        accepted_at=datetime(2019, 7, 31, 20, tzinfo=UTC),
        exchange_axis=True,
    )
    dimh, segments = REAL_EXCHANGE_DIM
    adsh = "0001013462-19-000023"
    txt = _listing_rows(adsh, dimh, "Common Stock, $0.01 par value per share", "ANSS", "NASDAQ")
    [record] = parse_fsn(
        [_sub(adsh, "1013462", "10-Q")], [], txt, [_dim(dimh, segments)], exchange_axis=True
    ).records
    assert record.listings == cover.cover.listings


# --- edgar.cover_split_contexts in parse_fsn (#1660, #1651 follow-up) ----------------

_FIFTH_THIRD_PREF = (
    "6.625% Fixed-to-Floating Rate Non-Cumulative Perpetual Preferred Stock, Series I"
)
#: (fixture, [(tag, value, dim.segments)]): the FSN rows each real filing's
#: tagging produces (segments as FSN spells them: prefix and `Axis` dropped).
_SPLIT_PATTERNS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "fifth_third": (
        "fifth_third_10q_2023q3.htm",
        [
            (
                "Security12bTitle",
                "Common Stock, Without Par Value",
                "CapitalUnitsByClass=CommonStockWithoutParValueMember;",
            ),
            ("TradingSymbol", "FITB", "CapitalUnitsByClass=CommonStockWithoutParValueMember;"),
            (
                "SecurityExchangeName",
                "NASDAQ",
                "CapitalUnitsByClass=CommonStockWithoutParValueMember;",
            ),
            (
                "Security12bTitle",
                _FIFTH_THIRD_PREF,
                "CapitalUnitsByClass=SeriesIPreferredStockMember;",
            ),
            ("TradingSymbol", "FITBI", "CapitalUnitsByClass=SeriesIPreferredStockMember;"),
            ("SecurityExchangeName", "NASDAQ", "CapitalUnitsByClass=SeriesIPreferredStockMember;"),
        ],
    ),
    "abbott": (
        "abbott_10q_2020q3.htm",
        [
            ("Security12bTitle", "Common Shares, Without Par Value", ""),
            ("TradingSymbol", "ABT", "EntityListingsExchange=XNYS;"),
            ("SecurityExchangeName", "New York Stock Exchange", "EntityListingsExchange=XNYS;"),
            (
                "SecurityExchangeName",
                "Chicago Stock Exchange, Inc.",
                "EntityListingsExchange=XCHI;",
            ),
        ],
    ),
    "pg": (
        "pg_10q_2023q3.htm",
        [
            (
                "Security12bTitle",
                "Common Stock, without Par Value",
                "ClassOfStock=CommonStockMember;",
            ),
            ("TradingSymbol", "PG", "ClassOfStock=CommonStockMember;"),
            (
                "SecurityExchangeName",
                "NYSE",
                "ClassOfStock=CommonStockMember;EntityListingsExchange=XNYS;",
            ),
            ("Security12bTitle", "1.125% Notes due 2023", "ClassOfStock=A1.125due2023Domain;"),
            ("TradingSymbol", "PG23A", "ClassOfStock=A1.125due2023Domain;"),
            ("SecurityExchangeName", "NYSE", "ClassOfStock=A1.125due2023Domain;"),
        ],
    ),
}


def _split_fsn(pattern: str, *, on: bool, rows: list[tuple[str, str, str]] | None = None) -> Any:
    adsh = "0000000001-23-000001"
    dims: dict[str, str] = {}
    txt = []
    for tag, value, segments in rows if rows is not None else _SPLIT_PATTERNS[pattern][1]:
        dimh = f"0x{len(dims) + 1:x}" if segments and segments not in dims.values() else ""
        if segments:
            dimh = next((h for h, s in dims.items() if s == segments), dimh)
            dims[dimh] = segments
        txt.append(
            _txt(adsh, tag, value, dimh=dimh, dimn=str(segments.count("=")) if segments else "0")
        )
    return parse_fsn(
        [_sub(adsh, "1", "10-Q")],
        [],
        txt,
        [_dim(h, s) for h, s in dims.items()],
        exchange_axis=True,
        split_contexts=on,
    )


class TestParseFsnSplitContexts:
    def test_nisource_common_and_equity_units_keep_their_own_symbols(self) -> None:
        rows = [
            (
                "Security12bTitle",
                "Common Stock, par value $0.01 per share",
                "StatementEquityComponents=CommonStock;",
            ),
            ("TradingSymbol", "NI", "StatementEquityComponents=CommonStock;"),
            ("SecurityExchangeName", "NYSE", "StatementEquityComponents=CommonStock;"),
            (
                "Security12bTitle",
                "Series A Corporate Units",
                "CapitalUnitsByClass=SeriesACorporateUnits;",
            ),
            ("TradingSymbol", "NIMC", "CapitalUnitsByClass=SeriesACorporateUnits;"),
            ("SecurityExchangeName", "NYSE", "CapitalUnitsByClass=SeriesACorporateUnits;"),
        ]
        parsed = _split_fsn("", on=True, rows=rows)
        assert parsed.failures == ()
        [record] = parsed.records
        assert [(i.title, i.ticker, i.exchange) for i in record.listings] == [
            ("Common Stock, par value $0.01 per share", "NI", "NYSE"),
            ("Series A Corporate Units", "NIMC", "NYSE"),
        ]

    def test_same_member_name_on_two_axes_does_not_merge(self) -> None:
        rows = [
            ("Security12bTitle", "Common Stock", "StatementEquityComponents=CommonStock;"),
            ("TradingSymbol", "NIMC", "CapitalUnitsByClass=CommonStock;"),
            ("SecurityExchangeName", "NYSE", "CapitalUnitsByClass=CommonStock;"),
        ]
        [record] = _split_fsn("", on=True, rows=rows).records
        assert record.listings == ()
        assert record.incomplete_listings == 1

    def test_surrounding_symbol_quotes_are_removed(self) -> None:
        rows = [
            ("Security12bTitle", "Common Stock", ""),
            ("TradingSymbol", '"""CNA"""', ""),
            ("SecurityExchangeName", "NYSE", ""),
        ]
        [record] = _split_fsn("", on=True, rows=rows).records
        assert [i.ticker for i in record.listings] == ["CNA"]

    @pytest.mark.parametrize("pattern", sorted(_SPLIT_PATTERNS))
    def test_the_fsn_rows_give_the_per_document_listings(self, pattern: str) -> None:
        from tradepartner.adapters.edgar import parse_cover_page

        name = _SPLIT_PATTERNS[pattern][0]
        cover = parse_cover_page(
            (EDGAR_FIXTURES / "cover_exchange_axis" / name).read_bytes(),
            accession="0000000001-23-000001",
            accepted_at=datetime(2023, 7, 30, 20, tzinfo=UTC),
            exchange_axis=True,
            split_contexts=True,
        )
        parsed = _split_fsn(pattern, on=True)
        assert parsed.failures == ()
        [record] = parsed.records
        assert record.listings == cover.cover.listings
        assert record.incomplete_listings == cover.incomplete_listings == 0
        assert record.exchange_axis_mismatch == cover.exchange_axis_mismatch

    def test_the_three_filings_name_their_tickers(self) -> None:
        got = {
            p: [(i.ticker, i.exchange) for i in _split_fsn(p, on=True).records[0].listings]
            for p in _SPLIT_PATTERNS
        }
        assert got == {
            "fifth_third": [("FITB", "NASDAQ"), ("FITBI", "NASDAQ")],
            "abbott": [("ABT", "NYSE"), ("ABT", "NYSE_CHICAGO")],
            "pg": [("PG", "NYSE"), ("PG23A", "NYSE")],
        }

    @pytest.mark.parametrize(
        ("pattern", "listed", "incomplete"),
        [("fifth_third", [], 0), ("abbott", [], 1), ("pg", ["PG23A"], 1)],
    )
    def test_switch_off_is_todays_output(
        self, pattern: str, listed: list[str], incomplete: int
    ) -> None:
        [record] = _split_fsn(pattern, on=False).records
        assert [i.ticker for i in record.listings] == listed
        assert record.incomplete_listings == incomplete
        adsh = "0000000001-23-000001"
        # the default (no keyword) is the same parse as the explicit off
        assert _split_fsn(pattern, on=False).records[0] == _parse_default(pattern, adsh)

    def test_capital_units_shares_stay_unread(self) -> None:
        adsh = "0000000001-23-000001"
        num = [
            _num(adsh, SHARES_TAG, "5", "20230630", dimh="0x1", dimn="1"),
            _num(adsh, SHARES_TAG, "7", "20230630"),
        ]
        dim = [_dim("0x1", "CapitalUnitsByClass=CommonStockWithoutParValueMember;")]
        parsed = parse_fsn(
            [_sub(adsh, "1", "10-Q")], num, [], dim, exchange_axis=True, split_contexts=True
        )
        [record] = parsed.records
        assert record.shares == (FsnShare("", 7, date(2023, 6, 30)),)

    def test_two_symbols_in_a_class_fall_back_to_one_listing_per_group(self) -> None:
        rows = [
            ("Security12bTitle", "Common", "ClassOfStock=CommonStockMember;"),
            ("TradingSymbol", "AAA", "ClassOfStock=CommonStockMember;EntityListingsExchange=XNYS;"),
            (
                "SecurityExchangeName",
                "NYSE",
                "ClassOfStock=CommonStockMember;EntityListingsExchange=XNYS;",
            ),
            ("TradingSymbol", "BBB", "ClassOfStock=CommonStockMember;EntityListingsExchange=XNAS;"),
            (
                "SecurityExchangeName",
                "NASDAQ",
                "ClassOfStock=CommonStockMember;EntityListingsExchange=XNAS;",
            ),
        ]
        [record] = _split_fsn("", on=True, rows=rows).records
        assert record.listings == ()  # no symbol is carried onto a title it was not tagged with
        assert record.incomplete_listings == 2

    def test_both_class_axes_in_one_segment_are_excluded(self) -> None:
        rows = [
            ("Security12bTitle", "Common", "ClassOfStock=A;CapitalUnitsByClass=B;"),
            ("TradingSymbol", "AAA", "ClassOfStock=A;CapitalUnitsByClass=B;"),
            ("SecurityExchangeName", "NYSE", "ClassOfStock=A;CapitalUnitsByClass=B;"),
        ]
        [record] = _split_fsn("", on=True, rows=rows).records
        assert record.listings == ()


def _parse_default(pattern: str, adsh: str) -> Any:
    dims: dict[str, str] = {}
    txt = []
    for tag, value, segments in _SPLIT_PATTERNS[pattern][1]:
        if segments and segments not in dims.values():
            dims[f"0x{len(dims) + 1:x}"] = segments
        dimh = next((h for h, s in dims.items() if s == segments), "")
        txt.append(
            _txt(adsh, tag, value, dimh=dimh, dimn=str(segments.count("=")) if segments else "0")
        )
    return parse_fsn(
        [_sub(adsh, "1", "10-Q")],
        [],
        txt,
        [_dim(h, s) for h, s in dims.items()],
        exchange_axis=True,
    ).records[0]


def test_an_undecodable_byte_in_a_capital_units_segment_fails_closed_only_with_the_switch() -> None:
    adsh = "0000000001-23-000001"
    dim = [_dim("0x1", "CapitalUnitsByClass=Common\ufffdMember;")]
    txt = _listing_rows(adsh, "0x1", "Common", "AAA", "NYSE")
    args = ([_sub(adsh, "1", "10-Q")], [], txt, dim)
    parsed = parse_fsn(*args, split_contexts=True)
    assert [f.accession for f in parsed.failures] == [adsh]
    assert "undecodable byte" in parsed.failures[0].error
    assert parse_fsn(*args).failures == ()  # today's parse drops the row

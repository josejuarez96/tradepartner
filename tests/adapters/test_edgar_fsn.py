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
from datetime import date, datetime
from pathlib import Path

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
from tradepartner.adapters.edgar_source import FSN_VERSION, EdgarFilingSource, _fsn_rows
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
        txt += [_txt(bad, t, v + "�" if t == tag else v) for t, v in values.items()]
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
        unchecked; it never aborts the run and is never a re-issue."""
        settings = _settings(tmp_path)
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

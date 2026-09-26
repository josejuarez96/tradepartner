"""Tests for the SEC Financial Statement and Notes (FSN) data sets (T11c,
plan amendment #216).

`parse_fsn` is exercised over synthetic rows built in this file (dual-class
issuer, a `coreg` row, a filer-custom member, a non-`ClassOfStock` axis, a
non-issuer CIK, and one accession whose rows do not parse) because
`tests/fixtures/edgar/fsn/` has not been recorded yet: the owner records it
with `python -m tradepartner.cli_record fsn`. The two tests keyed to real
recorded periods (`2025_10`, `2026_02`) are skipped until then, so nothing
here goes untested in the meantime -- the synthetic rows cover the same
behaviour parse_fsn must have on the real files.

`EdgarFilingSource._ensure_fsn` is tested against a synthetic FSN page and
in-memory zips served by an `httpx.MockTransport`, reusing
`edgar_transport.EdgarRouter`. `filing_index()` is not run for real in these
tests (it is exercised in `test_edgar_source.py`); `_ensure_fsn` only needs
it to have run once, so the tests set the private flag directly, the same
way this suite already reaches into other adapter internals
(`edgar_raw._LIMITER`).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from datetime import date
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
from tradepartner.adapters.edgar_source import FSN_VERSION, EdgarFilingSource
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

    def test_symbol_with_no_title_or_exchange_fails_that_accession_only(self) -> None:
        good = "0000000005-25-000001"
        bad = "0000000006-25-000001"
        sub = [_sub(good, "5", "10-K"), _sub(bad, "6", "10-K")]
        txt = [
            _txt(good, "Security12bTitle", "Common Stock"),
            _txt(good, "TradingSymbol", "GOOD"),
            _txt(good, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
            _txt(bad, "TradingSymbol", "BAD"),  # no title, no exchange
        ]
        parsed = parse_fsn(sub, [], txt, [])
        assert [r.accession for r in parsed.records] == [good]
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
    import csv

    def rows(name: str) -> list[dict[str, str]]:
        with (period_dir / name).open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]

    return rows("sub.tsv"), rows("num.tsv"), rows("txt.tsv"), rows("dim.tsv")


@pytest.mark.skipif(
    not (FIXTURES / "2025_10" / "sub.tsv").exists(),
    reason="owner records FSN fixtures: python -m tradepartner.cli_record fsn",
)
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


@pytest.mark.skipif(
    not (FIXTURES / "2026_02" / "sub.tsv").exists(),
    reason="owner records FSN fixtures: python -m tradepartner.cli_record fsn",
)
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
        ],
        ids=["head-fails", "no-etag-or-last-modified"],
    )
    def test_a_period_whose_reissue_cannot_be_checked_is_counted(
        self, tmp_path: Path, head: httpx.Response
    ) -> None:
        """No usable validator (a failed `HEAD`, or neither `ETag` nor
        `Last-Modified`) is counted on `.fsn_reissue_undetected` so the run
        message says re-issues went unchecked; it is never a re-issue."""
        settings = _settings(tmp_path)
        zip_bytes = _one_period_zip("0000000011-15-000001", "11")

        def zip_route(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return head
            return httpx.Response(200, content=zip_bytes, headers={"ETag": '"v1"'})

        router = _router_with_fsn("2015q1")
        router.add(_fsn_zip_url("2015q1"), zip_route)
        _source_ready(settings, router)._ensure_fsn()
        source2 = _source_ready(settings, router)
        source2._ensure_fsn()
        assert (source2.fsn_reissue_undetected, source2.fsn_reissued) == (1, 0)

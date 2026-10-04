"""`EdgarFilingSource.cover_pages` and `.filing_headers` over FSN (T11d,
plan amendment #216).

`filing_index()` is not run for real here (T11b's own job, covered by
`test_edgar_source.py`): a source's `_filing_index_ran` flag is set and its
per-CIK stamps are written directly with `_save_stamps`, the same shortcut
`test_edgar_fsn.py` already takes for `_ensure_fsn`. FSN periods are served
from real recorded zips (`2025_10` Apple, `2026_02` Alphabet) built the same
way `test_edgar_fsn.py` builds them, plus one synthetic period ("2026_03")
carrying a blank-SIC 8-K so the lag window has a fixed boundary: with
`edgar.fsn_first_year=2025`, the newest cached period is `2026_03`, so the
lag window starts 2026-03-01 (Eastern).

Per-document cover pages and headers are exercised against a real recorded
document (`filing_plain_issuer_aapl-20250927.htm.gz`, `sgml_header_plain_issuer.txt`)
re-served under synthetic accessions absent from FSN, so `parse_cover_page`
and `parse_sgml_header` run for real rather than being stubbed.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from edgar_transport import FIXTURES, EdgarRouter, edgar_settings, index_header, index_line, load
from test_edgar_fsn import FSN_PAGE_URL, _fsn_zip_bytes, _fsn_zip_url, _num, _sub, _txt
from test_edgar_source import EXCHANGE_LINE, KLX, KLX_25NSE, KLX_LINE, MISSING_LINE, SUBMISSIONS_URL
from test_edgar_source import _payload as _index_payload

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar_source import (
    COVER_VERSION,
    DELISTING_VERSION,
    HEADER_VERSION,
    PARSER_VERSION,
    EdgarFilingSource,
    SubmissionRecord,
)
from tradepartner.adapters.filings import CoverPage, FactRecord, FilingHeader
from tradepartner.config import Settings

APPLE = "0000320193"
ALPHABET = "0001652044"
APPLE_ACCESSION = "0000320193-25-000079"
ALPHABET_ACCESSION = "0001652044-26-000018"
APPLE_ACCEPTED = datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC)
ALPHABET_ACCEPTED = datetime(2026, 2, 5, 2, 56, 3, tzinfo=UTC)

# Real recorded documents, reused under synthetic accessions absent from FSN.
_COVER_DOCUMENT = gzip.decompress(
    (FIXTURES / "filing_plain_issuer_aapl-20250927.htm.gz").read_bytes()
)
_HEADER_TEXT = (FIXTURES / "sgml_header_plain_issuer.txt").read_text()


def _synthetic_header(
    accession: str, *, form: str = "10-K", sic: int | None = 3571, accepted: str = "20260601060126"
) -> str:
    """The recorded Apple SGML header, patched for a synthetic accession."""
    text = _HEADER_TEXT.replace(APPLE_ACCESSION, accession)
    text = text.replace("<ACCEPTANCE-DATETIME>20251031060126", f"<ACCEPTANCE-DATETIME>{accepted}")
    text = text.replace("CONFORMED SUBMISSION TYPE:\t10-K", f"CONFORMED SUBMISSION TYPE:\t{form}")
    if sic is None:
        text = text.replace(
            "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tELECTRONIC COMPUTERS [3571]\n", ""
        )
    else:
        text = text.replace("[3571]", f"[{sic}]")
    return text


def _download_url(cik: str, accession: str, filename: str) -> str:
    accession_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{filename}"


def _header_url(cik: str, accession: str) -> str:
    accession_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{accession}.txt"


def _real_period_zip(period: str) -> bytes:
    directory = FIXTURES / "fsn" / period
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member in ("sub.tsv", "num.tsv", "txt.tsv", "dim.tsv"):
            archive.write(directory / member, member)
    return buffer.getvalue()


# Synthetic third period: newest cached, so the lag window starts 2026-03-01
# (Eastern). Carries a blank-SIC 8-K for Apple (#174: FSN's blanks are 8-Ks).
BLANK_SIC_ACCESSION = "0000320193-26-000900"


def _blank_sic_period_zip() -> bytes:
    return _fsn_zip_bytes(
        [_sub(BLANK_SIC_ACCESSION, "320193", "8-K", sic="")],
        [],
        [
            _txt(BLANK_SIC_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(BLANK_SIC_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(BLANK_SIC_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )


def _settings(tmp_path: Path, **edgar: object) -> Settings:
    return edgar_settings(tmp_path, fsn_first_year=2025, **edgar)


def _router() -> EdgarRouter:
    router = EdgarRouter()
    periods = ["2025_10", "2026_02", "2026_03"]
    router.add(
        FSN_PAGE_URL,
        "<html><body>"
        + "\n".join(f'<a href="{_fsn_zip_url(p)}">{p}</a>' for p in periods)
        + "</body></html>",
    )
    router.add(_fsn_zip_url("2025_10"), _real_period_zip("2025_10"))
    router.add(_fsn_zip_url("2026_02"), _real_period_zip("2026_02"))
    router.add(_fsn_zip_url("2026_03"), _blank_sic_period_zip())
    return router


def _source(settings: Settings, router: EdgarRouter) -> EdgarFilingSource:
    source = EdgarFilingSource(settings, client=router.client())
    source._filing_index_ran = True
    return source


def _record(
    accession: str,
    form: str,
    accepted_at: datetime | None,
    *,
    inline_xbrl: bool = True,
    primary_document: str = "doc.htm",
) -> SubmissionRecord:
    return SubmissionRecord(accession, form, primary_document, inline_xbrl, accepted_at)


def _seed_stamps(source: EdgarFilingSource, cik: str, records: dict[str, SubmissionRecord]) -> None:
    source._save_stamps(cik, records)


LAG_START = datetime(2026, 3, 1, 5, 0, tzinfo=UTC)  # 2026-03-01 EST (approx.; well inside DST-free)
BEFORE_LAG = datetime(2020, 1, 1, tzinfo=UTC)
INSIDE_LAG = datetime(2026, 6, 1, tzinfo=UTC)


# --- cover pages: FSN precedence and stamping -------------------------------


def test_fsn_cover_pages_are_stamped_from_submissions_never_fsn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(
        source,
        APPLE,
        {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)},
    )
    [page] = source.cover_pages(APPLE)
    assert page.accession == APPLE_ACCESSION
    assert page.accepted_at == APPLE_ACCEPTED  # the submissions stamp, not FSN's own dates
    assert page.accepted_at.tzinfo is UTC
    assert {(item.ticker, item.exchange) for item in page.listings} == {("AAPL", "NASDAQ")}


def test_an_fsn_accession_with_no_stamp_is_absent_then_present(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(source, APPLE, {})  # no stamp at all yet
    assert source.cover_pages(APPLE) == []
    assert source.fsn_missing == 0  # never counted: the index already reports it

    _seed_stamps(
        source,
        APPLE,
        {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)},
    )
    [page] = source.cover_pages(APPLE)
    assert page.accession == APPLE_ACCESSION
    assert source.fsn_missing == 0  # still no double count


def test_an_unstampable_record_counts_as_no_stamp(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", None)})
    assert source.cover_pages(APPLE) == []
    assert source.fsn_missing == 0


# --- cover pages: the lag-window per-document fallback ----------------------


def test_a_lag_window_filing_absent_from_fsn_is_fetched_once_and_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000050"
    router.add(_download_url(APPLE, accession, "lagwin.htm"), _COVER_DOCUMENT)
    source = _source(settings, router)
    _seed_stamps(
        source,
        APPLE,
        {accession: _record(accession, "10-K", INSIDE_LAG, primary_document="lagwin.htm")},
    )
    [page] = source.cover_pages(APPLE)
    assert page.accession == accession and page.accepted_at == INSIDE_LAG
    assert {(item.ticker, item.exchange) for item in page.listings} == {("AAPL", "NASDAQ")}
    assert source.fsn_missing == 0
    cache_path = (
        Path(settings.edgar.cache_dir) / "cover" / f"v{COVER_VERSION}" / f"{accession}.json"
    )
    assert cache_path.exists()
    document_url = _download_url(APPLE, accession, "lagwin.htm")
    fetched_once = [u for u in router.urls if u == document_url]
    assert len(fetched_once) == 1

    # a second call makes no further request: served from the per-document cache
    before = len(router.urls)
    [again] = source.cover_pages(APPLE)
    assert again == page
    assert router.urls[before:] == []


def test_an_older_accession_absent_from_fsn_is_not_fetched_and_is_counted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-19-000001"
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "10-K", BEFORE_LAG)})
    assert source.cover_pages(APPLE) == []
    assert source.fsn_missing == 1
    assert not any(
        "Archives/edgar/data" in u and accession.replace("-", "") in u for u in router.urls
    )


def test_a_non_inline_xbrl_accession_absent_from_fsn_is_never_fetched(tmp_path: Path) -> None:
    """Not inline XBRL: `parse_cover_page` cannot read it either way."""
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    accession = "0000320193-26-000051"
    _seed_stamps(
        source, APPLE, {accession: _record(accession, "10-K", INSIDE_LAG, inline_xbrl=False)}
    )
    assert source.cover_pages(APPLE) == []
    assert source.fsn_missing == 0  # only iXBRL cover-form accessions are counted


def test_an_fsn_failed_accession_inside_the_lag_window_is_fetched_not_counted(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    router = _router()
    failed_accession = "0000000099-15-000001"
    # A synthetic FSN period whose one accession fails to extract (bad share value).
    bad_zip = _fsn_zip_bytes(
        [_sub(failed_accession, "99", "10-K")],
        [_num(failed_accession, "EntityCommonStockSharesOutstanding", "not-a-number", "20250101")],
        [],
        [],
    )
    router.add(_fsn_zip_url("2015q1"), bad_zip)
    # Rebuild the page to also list 2015q1 alongside the usual three periods.
    periods = ["2015q1", "2025_10", "2026_02", "2026_03"]
    router.add(
        FSN_PAGE_URL,
        "<html><body>"
        + "\n".join(f'<a href="{_fsn_zip_url(p)}">{p}</a>' for p in periods)
        + "</body></html>",
    )
    # Apple's recorded cover, re-labelled as CIK 99's own (synthetic): the
    # per-document page is served only to the entity it names.
    document = _COVER_DOCUMENT.replace(b"0000320193", b"0000000099")
    router.add(_download_url("0000000099", failed_accession, "fail.htm"), document)
    source = _source(settings, router)
    _seed_stamps(
        source,
        "0000000099",
        {
            failed_accession: _record(
                failed_accession, "10-K", INSIDE_LAG, primary_document="fail.htm"
            )
        },
    )
    pages = source.cover_pages("0000000099")
    assert [p.accession for p in pages] == [failed_accession]
    assert source.fsn_missing == 0


def test_an_fsn_failed_accession_older_than_lag_window_is_not_fetched_or_counted(
    tmp_path: Path,
) -> None:
    """The failing accession must sit in a period `fsn_first_year` actually
    extracts (so it gets a manifest entry at all): mixed into `2026_03`
    alongside the blank-SIC 8-K, rather than a separate older period, which
    `edgar.fsn_first_year=2025` would skip extracting entirely."""
    settings = _settings(tmp_path)
    router = _router()
    failed_accession = "0000000098-15-000001"
    combined_zip = _fsn_zip_bytes(
        [
            _sub(BLANK_SIC_ACCESSION, "320193", "8-K", sic=""),
            _sub(failed_accession, "98", "10-K"),
        ],
        [
            _num(
                failed_accession, "EntityCommonStockSharesOutstanding", "not-a-number", "20250101"
            ),
        ],
        [
            _txt(BLANK_SIC_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(BLANK_SIC_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(BLANK_SIC_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )
    router.add(_fsn_zip_url("2026_03"), combined_zip)
    source = _source(settings, router)
    _seed_stamps(
        source, "0000000098", {failed_accession: _record(failed_accession, "10-K", BEFORE_LAG)}
    )
    assert source.cover_pages("0000000098") == []
    assert source.fsn_missing == 0  # T11f's .failed_filings already counts it
    assert not any(
        "Archives/edgar/data" in u and failed_accession.replace("-", "") in u for u in router.urls
    )


def test_a_per_document_cover_cache_wins_over_fsn_until_a_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)})
    # A stale per-document cache entry for the FSN-covered accession.
    stale = {
        "version": COVER_VERSION,
        "accession": APPLE_ACCESSION,
        "cik": APPLE,
        "entity_cik": APPLE,
        "listings": [["Stale Title", "STALE", "NYSE"]],
        "facts": [],
    }

    cache_path = (
        Path(settings.edgar.cache_dir) / "cover" / f"v{COVER_VERSION}" / f"{APPLE_ACCESSION}.json"
    )
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(stale))

    [page] = source.cover_pages(APPLE)
    assert [item.ticker for item in page.listings] == ["STALE"]

    monkeypatch.setattr("tradepartner.adapters.edgar_source.COVER_VERSION", COVER_VERSION + 1)
    source2 = _source(settings, _router())
    _seed_stamps(
        source2, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)}
    )
    [page2] = source2.cover_pages(APPLE)
    assert {item.ticker for item in page2.listings} == {"AAPL"}


# --- filing_headers ----------------------------------------------------------


def test_headers_come_from_fsn_with_no_request_filtered_by_forms(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)})
    source._ensure_fsn()  # the one-time FSN setup, not part of "no request" below
    before = len(router.urls)
    [header] = source.filing_headers(APPLE, ["10-K"])
    assert (header.accession, header.sic, header.accepted_at) == (
        APPLE_ACCESSION,
        3571,
        APPLE_ACCEPTED,
    )
    assert router.urls[before:] == []
    assert source.filing_headers(APPLE, ["8-K"]) == []  # filtered by forms


def test_a_ranged_header_is_never_requested_for_an_older_8k(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-15-000002"
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "8-K", BEFORE_LAG)})
    assert source.filing_headers(APPLE, ["8-K"]) == []
    assert not any(u.endswith(f"{accession}.txt") for u in router.urls)


def test_a_ranged_header_is_requested_for_a_lag_window_8k_absent_from_fsn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000060"
    router.add(_header_url(APPLE, accession), _synthetic_header(accession, form="8-K", sic=7372))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "8-K", INSIDE_LAG)})
    [header] = source.filing_headers(APPLE, ["8-K"])
    assert (header.sic, header.accepted_at) == (7372, INSIDE_LAG)  # served at the submissions stamp


def test_a_registration_form_gets_a_ranged_header_from_header_start_year(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-25-000500"
    accepted = datetime(2025, 6, 1, tzinfo=UTC)
    router.add(_header_url(APPLE, accession), _synthetic_header(accession, form="S-1", sic=6770))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "S-1", accepted)})
    [header] = source.filing_headers(APPLE, ["S-1"])
    assert header.sic == 6770


def test_a_registration_form_before_header_start_year_gets_no_ranged_header(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-24-000500"
    accepted = datetime(2024, 6, 1, tzinfo=UTC)  # before header_start_year (2025 override)
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "S-1", accepted)})
    assert source.filing_headers(APPLE, ["S-1"]) == []
    assert not any(u.endswith(f"{accession}.txt") for u in router.urls)


def test_a_lag_window_fsn_8k_with_a_blank_sic_gets_one_ranged_header(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accepted = datetime(2026, 6, 15, tzinfo=UTC)
    router.add(
        _header_url(APPLE, BLANK_SIC_ACCESSION),
        _synthetic_header(BLANK_SIC_ACCESSION, form="8-K", sic=7372, accepted="20260615120000"),
    )
    source = _source(settings, router)
    _seed_stamps(
        source, APPLE, {BLANK_SIC_ACCESSION: _record(BLANK_SIC_ACCESSION, "8-K", accepted)}
    )
    [header] = source.filing_headers(APPLE, ["8-K"])
    assert header.sic == 7372  # arrived via the ranged header, not FSN's blank


def test_an_sgml_header_is_served_at_the_submissions_acceptance(tmp_path: Path) -> None:
    """The header's own ACCEPTANCE-DATETIME (06:01:26 Eastern -> 10:01:26 UTC
    per the recorded fixture) differs from a forged submissions stamp; T11d
    always serves the submissions one."""
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000070"
    forged_stamp = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    router.add(_header_url(APPLE, accession), _synthetic_header(accession, sic=1234))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "10-K", forged_stamp)})
    [header] = source.filing_headers(APPLE, ["10-K"])
    assert header.accepted_at == forged_stamp


def test_a_header_naming_another_accession_skips_and_records(tmp_path: Path) -> None:
    """T11h: `_ranged_header`'s own accession-mismatch `ValueError` is one of
    the failure policy's skipped-not-raised kinds, recorded on
    `.failed_filings`, the run left `ok`."""
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000080"
    router.add(_header_url(APPLE, accession), _synthetic_header("0000320193-26-999999"))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "10-K", INSIDE_LAG)})
    headers = source.filing_headers(APPLE, ["10-K"])
    assert accession not in {h.accession for h in headers}
    assert source.failed_filings == 1


def test_an_http_error_on_a_ranged_header_propagates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000090"
    router.add(_header_url(APPLE, accession), 500)
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "10-K", INSIDE_LAG)})
    with pytest.raises(httpx.HTTPStatusError):
        source.filing_headers(APPLE, ["10-K"])


def test_an_http_error_on_a_cover_page_document_propagates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000091"
    router.add(_download_url(APPLE, accession, "fail.htm"), 500)
    source = _source(settings, router)
    _seed_stamps(
        source,
        APPLE,
        {accession: _record(accession, "10-K", INSIDE_LAG, primary_document="fail.htm")},
    )
    with pytest.raises(httpx.HTTPStatusError):
        source.cover_pages(APPLE)


def test_a_per_document_header_cache_wins_over_fsn_until_a_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)})

    cache_path = (
        Path(settings.edgar.cache_dir)
        / "header"
        / f"v{HEADER_VERSION}"
        / f"{int(APPLE)}"
        / f"{APPLE_ACCESSION}.json"
    )
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {"version": HEADER_VERSION, "accession": APPLE_ACCESSION, "cik": APPLE, "sic": 9999}
        )
    )

    [header] = source.filing_headers(APPLE, ["10-K"])
    assert header.sic == 9999  # the per-document cache, not FSN's 3571

    monkeypatch.setattr("tradepartner.adapters.edgar_source.HEADER_VERSION", HEADER_VERSION + 1)
    source2 = _source(settings, _router())
    _seed_stamps(
        source2, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)}
    )
    [header2] = source2.filing_headers(APPLE, ["10-K"])
    assert header2.sic == 3571  # switched back to FSN


def test_second_calls_make_no_request_for_an_unchanged_cik(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    source = _source(settings, router)
    _seed_stamps(
        source,
        APPLE,
        {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)},
    )
    source.cover_pages(APPLE)
    source.filing_headers(APPLE, ["10-K"])
    before = len(router.urls)
    source.cover_pages(APPLE)
    source.filing_headers(APPLE, ["10-K"])
    assert router.urls[before:] == []


def test_alphabet_dual_class_cover_page_matches_fsn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, _router())
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    [page] = source.cover_pages(ALPHABET)
    assert page.accepted_at == ALPHABET_ACCEPTED
    assert {item.ticker for item in page.listings} == {"GOOGL", "GOOG"}


# --- safety-reviewer fixes (#249) --------------------------------------------


def test_a_combined_filing_gives_each_co_registrant_its_own_sic(tmp_path: Path) -> None:
    """One accession filed by two CIKs (a parent and a subsidiary): each header
    is parsed from that CIK's FILER block and cached per CIK, so neither CIK is
    ever served the other's SIC."""
    settings = _settings(tmp_path)
    router = _router()
    accession, other = "0000320193-26-000060", "0000999999"
    router.add(_header_url(APPLE, accession), _synthetic_header(accession, form="8-K", sic=3571))
    other_text = _synthetic_header(accession, form="8-K", sic=4911).replace(
        "CENTRAL INDEX KEY:\t\t\t0000320193", f"CENTRAL INDEX KEY:\t\t\t{other}"
    )
    router.add(_header_url(other, accession), other_text)
    source = _source(settings, router)
    for cik in (APPLE, other):
        _seed_stamps(source, cik, {accession: _record(accession, "8-K", INSIDE_LAG)})

    [apple] = source.filing_headers(APPLE, ["8-K"])
    [sub] = source.filing_headers(other, ["8-K"])
    assert (apple.sic, sub.sic) == (3571, 4911)
    before = len(router.urls)
    assert [h.sic for h in source.filing_headers(APPLE, ["8-K"])] == [3571]  # cached, per CIK
    assert router.urls[before:] == []


def test_no_cached_fsn_period_in_range_fails_closed_with_no_request(tmp_path: Path) -> None:
    """`edgar.fsn_first_year` past every listed period leaves no lag-window
    start: fail closed, never treat all of history as the lag window (that
    would request a header for every 8-K since 1993)."""
    settings = edgar_settings(tmp_path, fsn_first_year=2030)
    router = _router()
    source = _source(settings, router)
    old = "0000320193-20-000001"
    _seed_stamps(source, APPLE, {old: _record(old, "8-K", BEFORE_LAG)})
    with pytest.raises(RuntimeError, match="fsn_first_year"):
        source.filing_headers(APPLE, ["8-K"])
    with pytest.raises(RuntimeError, match="fsn_first_year"):
        source.cover_pages(APPLE)
    assert not [u for u in router.urls if "/Archives/" in u]


def test_the_root_copy_is_fetched_never_an_xsl_rendering(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000070"
    router.add(_download_url(APPLE, accession, "root.htm"), _COVER_DOCUMENT)
    source = _source(settings, router)
    _seed_stamps(
        source,
        APPLE,
        {accession: _record(accession, "10-K", INSIDE_LAG, primary_document="xslFormX01/root.htm")},
    )
    [page] = source.cover_pages(APPLE)
    assert page.accession == accession
    assert _download_url(APPLE, accession, "root.htm") in router.urls
    assert not [u for u in router.urls if "xsl" in u]


@pytest.mark.parametrize(
    "document", [_COVER_DOCUMENT, b"<html>not a cover page</html>"], ids=["parsed", "parse-error"]
)
def test_the_downloaded_document_is_deleted_after_parsing(tmp_path: Path, document: bytes) -> None:
    """Deleted after a good parse and after a parse error alike."""
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000080"
    router.add(_download_url(APPLE, accession, "del.htm"), document)
    source = _source(settings, router)
    record = _record(accession, "10-K", INSIDE_LAG, primary_document="del.htm")
    _seed_stamps(source, APPLE, {accession: record})
    with contextlib.suppress(ValueError):  # the parse-error case: still deleted
        source.cover_pages(APPLE)
    assert not edgar_raw.cached_filing_path(APPLE, accession, "del.htm", settings=settings).exists()


# --- quant-auditor fixes (#249) ----------------------------------------------

OTHER = "0000999999"  # a co-registrant of Apple's filings, synthetic


def test_a_co_registrant_gets_no_cover_page_from_an_fsn_filing_held_by_the_filer(
    tmp_path: Path,
) -> None:
    """FSN keys a combined filing to its primary filer only. The co-registrant
    must not be served the filer's listings, nor fetch, nor count it missing."""
    settings = _settings(tmp_path)
    router = _router()
    source = _source(settings, router)
    _seed_stamps(source, OTHER, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", BEFORE_LAG)})
    before = len(router.urls)
    assert source.cover_pages(OTHER) == []
    assert source.fsn_missing == 0
    assert not [u for u in router.urls[before:] if "/Archives/" in u]


def test_a_per_document_cover_page_is_served_only_to_its_own_entity(tmp_path: Path) -> None:
    """A lag-window combined filing parsed per document names its entity (the
    Apple cover here); a co-registrant gets no listings and no second fetch."""
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000090"
    router.add(_download_url(APPLE, accession, "joint.htm"), _COVER_DOCUMENT)
    router.add(_download_url(OTHER, accession, "joint.htm"), _COVER_DOCUMENT)
    source = _source(settings, router)
    record = _record(accession, "10-K", INSIDE_LAG, primary_document="joint.htm")
    _seed_stamps(source, OTHER, {accession: record})
    _seed_stamps(source, APPLE, {accession: record})
    assert source.cover_pages(OTHER) == []
    [page] = source.cover_pages(APPLE)
    assert page.accession == accession
    fetches = [u for u in router.urls if u.endswith("/joint.htm")]
    assert len(fetches) == 1  # the co-registrant's parse is cached with its entity
    assert source.fsn_missing == 0


@pytest.mark.parametrize(
    ("accepted", "inside"),
    [
        (datetime(2026, 3, 1, 5, 30, tzinfo=UTC), True),  # 2026-03-01 00:30 EST
        (datetime(2026, 3, 1, 4, 30, tzinfo=UTC), False),  # 2026-02-28 23:30 EST
    ],
    ids=["just-inside", "just-outside"],
)
def test_the_lag_window_starts_at_eastern_midnight_of_the_newest_period(
    tmp_path: Path, accepted: datetime, inside: bool
) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000095"
    router.add(_download_url(APPLE, accession, "edge.htm"), _COVER_DOCUMENT)
    source = _source(settings, router)
    record = _record(accession, "10-K", accepted, primary_document="edge.htm")
    _seed_stamps(source, APPLE, {accession: record})
    pages = source.cover_pages(APPLE)
    assert (len(pages), source.fsn_missing) == ((1, 0) if inside else (0, 1))


def test_an_unreadable_manifest_does_not_move_the_lag_window(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    source = _source(settings, router)
    source._ensure_fsn()
    manifests = Path(settings.edgar.cache_dir) / "fsn"
    [manifest_dir] = list(manifests.glob("v*/manifests"))
    (manifest_dir / "2026_09.json").write_text("{truncated")
    fresh = _source(settings, router)
    fresh._ensure_fsn()
    assert fresh._lag_window_start() == datetime(2026, 3, 1, 5, 0, tzinfo=UTC)


def test_a_cached_ranged_header_makes_no_second_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000096"
    router.add(_header_url(APPLE, accession), _synthetic_header(accession, form="8-K"))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "8-K", INSIDE_LAG)})
    [first] = source.filing_headers(APPLE, ["8-K"])
    before = len(router.urls)
    fresh = _source(settings, router)
    [second] = fresh.filing_headers(APPLE, ["8-K"])
    assert second == first
    assert not [u for u in router.urls[before:] if "/Archives/" in u]


def test_cover_pages_and_headers_after_a_real_filing_index(tmp_path: Path) -> None:
    """End to end (quant-auditor, #249): no hand-written stamps. The real
    `filing_index()` scans synthetic index lines for the two recorded
    accessions, stamps them from the recorded submissions, and `cover_pages`
    and `filing_headers` serve FSN rows at exactly that stamp, never at FSN's
    own dates. A second source on the same cache rebuilds the same records
    without fetching submissions or FSN again."""
    from edgar_transport import index_header, index_line

    settings = edgar_settings(tmp_path, index_first_year=2025, fsn_first_year=2025)
    router = _router()
    lines = {
        (2025, 4): index_line("10-K", "Apple Inc.", 320193, "2025-10-31", APPLE_ACCESSION),
        (2026, 1): index_line("10-K", "Alphabet Inc.", 1652044, "2026-02-05", ALPHABET_ACCESSION),
    }
    for year, qtr in [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
        router.add_index(year, qtr, index_header() + lines.get((year, qtr), ""))
    clock = datetime(2026, 4, 15, tzinfo=UTC)

    def run() -> tuple[dict[str, datetime], list[CoverPage], list[CoverPage], list[FilingHeader]]:
        source = EdgarFilingSource(settings, client=router.client(), clock=lambda: clock)
        entries = {e.accession: e.accepted_at for e in source.filing_index()}
        return (
            entries,
            source.cover_pages(APPLE),
            source.cover_pages(ALPHABET),
            source.filing_headers(APPLE, settings.edgar.header_forms),
        )

    entries, apple, alphabet, headers = run()
    assert entries[APPLE_ACCESSION] == APPLE_ACCEPTED
    assert entries[ALPHABET_ACCESSION] == ALPHABET_ACCEPTED
    [apple_page] = [p for p in apple if p.accession == APPLE_ACCESSION]
    [alphabet_page] = [p for p in alphabet if p.accession == ALPHABET_ACCESSION]
    assert apple_page.accepted_at == APPLE_ACCEPTED and apple_page.accepted_at.tzinfo is UTC
    # FSN has Alphabet accepted 2026-02-04 21:56 Eastern and filed 20260205;
    # the served stamp is the submissions' 2026-02-05T02:56:03Z.
    assert alphabet_page.accepted_at == ALPHABET_ACCEPTED
    [apple_header] = [h for h in headers if h.accession == APPLE_ACCESSION]
    assert (apple_header.sic, apple_header.accepted_at) == (3571, APPLE_ACCEPTED)

    before = len(router.urls)
    again = run()
    assert again[1:] == (apple, alphabet, headers)
    later = router.urls[before:]
    assert not [u for u in later if "data.sec.gov/submissions" in u]
    # FSN zip URLs appear once per cached period: the HEAD re-issue check,
    # not a re-download (the router records URLs, not methods).
    assert len([u for u in later if "_notes.zip" in u]) == 3


# --- delistings (T11f) -------------------------------------------------------
#
# `delistings()` scans the quarterly index for real, unlike `cover_pages`/
# `filing_headers` above, which shortcut `filing_index()` with `_seed_stamps`.
# Every quarter but 2026 Q3 (which carries the recorded KLX 25-NSE synthetic
# index line, borrowed from `test_edgar_source.py`) is header-only: unlike
# that module's own `_router`, this one never serves the *real* 2024 Q1
# index, which carries many real Form 25/25-NSE rows for CIKs this test's
# router has no submissions fixture for.

_DELISTING_CLOCK = datetime(2026, 9, 25, tzinfo=UTC)
_DELISTING_DOCUMENT = gzip.decompress(
    (FIXTURES / "filing_delisted_25nse_primary_doc.xml.gz").read_bytes()
)
#: The recorded KLX filing's own `primaryDocument`: an XSL-rendered view.
_KLX_PRIMARY_DOCUMENT = "xslF25X02/primary_doc.xml"
#: Its root copy (T11d review parity: never the `xsl.../` rendering).
_KLX_ROOT_DOCUMENT = "primary_doc.xml"


def _delisting_router(*extra_lines: str) -> EdgarRouter:
    router = EdgarRouter()
    quarters = [(y, q) for y in range(2024, 2027) for q in range(1, 5) if (y, q) <= (2026, 3)]
    for year, qtr in quarters:
        router.add_index(year, qtr, index_header())
    router.add_index(
        2026, 3, index_header() + "".join((KLX_LINE, EXCHANGE_LINE, MISSING_LINE, *extra_lines))
    )
    return router


def _delisting_source(router: EdgarRouter, tmp_path: Path) -> EdgarFilingSource:
    settings = edgar_settings(tmp_path)
    return EdgarFilingSource(settings, client=router.client(), clock=lambda: _DELISTING_CLOCK)


def _add_klx_document(router: EdgarRouter) -> None:
    router.add(_download_url(KLX, KLX_25NSE, _KLX_ROOT_DOCUMENT), _DELISTING_DOCUMENT)


def _xml_payload(cik: int, accession: str, accepted: str, *, form: str = "25") -> dict[str, object]:
    """As `test_edgar_source._payload`, but with an `.xml` `primaryDocument`
    (`_payload` always serves `doc.htm`, for the pre-XML-skip test)."""
    return {
        "cik": str(cik),
        "name": "Synthetic",
        "filings": {
            "recent": {
                "accessionNumber": [accession],
                "form": [form],
                "primaryDocument": ["primary_doc.xml"],
                "isInlineXBRL": [0],
                "acceptanceDateTime": [f"{accepted}.000Z"],
            }
        },
    }


def test_the_recorded_klx_25nse_yields_its_class_title_and_exchange(tmp_path: Path) -> None:
    router = _delisting_router()
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    [delisting] = source.delistings()
    assert delisting.cik == KLX
    assert delisting.accession == KLX_25NSE
    assert delisting.form == "25-NSE"
    assert delisting.class_title == "rights"
    assert delisting.exchange == "NASDAQ"
    # the root copy, never the recorded filing's own `xsl.../` rendering
    assert not [u for u in router.urls if "xsl" in u]


def test_delistings_since_filters_on_accepted_at(tmp_path: Path) -> None:
    router = _delisting_router()
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    before = datetime(2026, 9, 24, tzinfo=UTC)
    after = datetime(2026, 9, 25, tzinfo=UTC)
    assert [d.accession for d in source.delistings(since=before)] == [KLX_25NSE]
    assert source.delistings(since=after) == []


def test_a_cached_delisting_makes_no_request(tmp_path: Path) -> None:
    router = _delisting_router()
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    [first] = source.delistings()
    document_url = _download_url(KLX, KLX_25NSE, _KLX_ROOT_DOCUMENT)
    assert len([u for u in router.urls if u == document_url]) == 1

    cache_path = (
        Path(source._settings.edgar.cache_dir)
        / "delisting"
        / f"v{DELISTING_VERSION}"
        / f"{KLX_25NSE}.json"
    )
    assert cache_path.exists()

    before = len(router.urls)
    [again] = source.delistings()
    assert again == first
    assert router.urls[before:] == []


def test_a_pre_xml_form_25_is_not_downloaded_and_is_counted(tmp_path: Path) -> None:
    """`_index_payload`'s recorded documents are all `doc.htm`: not XML, so
    `parse_delisting` (XML only) is never even called for it."""
    form_25 = "0005555555-26-000001"
    line = index_line("25", "Old Corp", 5555555, "2026-09-01", form_25)
    router = _delisting_router(line)
    router.add(
        f"{SUBMISSIONS_URL}CIK0005555555.json",
        _index_payload(5555555, (form_25, "25", "2026-09-01T20:00:00")),
    )
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    results = source.delistings()
    assert form_25 not in {d.accession for d in results}
    assert KLX_25NSE in {d.accession for d in results}
    assert source.pre_xml_delistings == 1
    # submissions are fetched (to know the primary document is `doc.htm`),
    # but no `Archives/edgar/data/...` document request for it
    assert not any("Archives/edgar/data/5555555" in u for u in router.urls)


def test_an_http_error_on_a_delisting_document_propagates(tmp_path: Path) -> None:
    accession = "0005555556-26-000001"
    line = index_line("25", "Bad Corp", 5555556, "2026-09-01", accession)
    router = _delisting_router(line)
    _add_klx_document(router)
    router.add(
        f"{SUBMISSIONS_URL}CIK0005555556.json",
        _xml_payload(5555556, accession, "2026-09-01T20:00:00"),
    )
    router.add(_download_url("0005555556", accession, "primary_doc.xml"), 500)
    source = _delisting_source(router, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        source.delistings()


def test_a_delisting_parse_error_skips_and_records(tmp_path: Path) -> None:
    """T11h: a `parse_delisting` `ValueError` skips that accession, records
    it and leaves the run `ok`, instead of raising."""
    accession = "0005555557-26-000001"
    line = index_line("25", "Bad Corp 2", 5555557, "2026-09-01", accession)
    router = _delisting_router(line)
    _add_klx_document(router)
    router.add(
        f"{SUBMISSIONS_URL}CIK0005555557.json",
        _xml_payload(5555557, accession, "2026-09-01T20:00:00"),
    )
    router.add(_download_url("0005555557", accession, "primary_doc.xml"), b"<not-a-delisting/>")
    source = _delisting_source(router, tmp_path)
    results = source.delistings()
    assert accession not in {d.accession for d in results}
    assert source.failed_filings == 1


@pytest.mark.parametrize(
    "document", [_DELISTING_DOCUMENT, b"<not-a-delisting/>"], ids=["parsed", "parse-error"]
)
def test_the_downloaded_delisting_document_is_deleted_after_parsing(
    tmp_path: Path, document: bytes
) -> None:
    """Deleted after a good parse and after a parse error alike."""
    accession = "0005555558-26-000001"
    line = index_line("25", "Doc Corp", 5555558, "2026-09-01", accession)
    router = _delisting_router(line)
    _add_klx_document(router)
    router.add(
        f"{SUBMISSIONS_URL}CIK0005555558.json",
        _xml_payload(5555558, accession, "2026-09-01T20:00:00"),
    )
    router.add(_download_url("0005555558", accession, "primary_doc.xml"), document)
    source = _delisting_source(router, tmp_path)
    with contextlib.suppress(ValueError):  # the parse-error case: still deleted
        source.delistings()
    assert not edgar_raw.cached_filing_path(
        "0005555558", accession, "primary_doc.xml", settings=source._settings
    ).exists()


def test_a_delisting_stamp_comes_from_submissions_never_the_document(tmp_path: Path) -> None:
    """The KLX document itself carries no acceptance date; T11f always
    serves the submissions one, as T11d does for cover pages and headers."""
    router = _delisting_router()
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    forged_stamp = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    source._save_stamps(
        KLX,
        {
            KLX_25NSE: SubmissionRecord(
                KLX_25NSE, "25-NSE", _KLX_PRIMARY_DOCUMENT, False, forged_stamp
            )
        },
    )
    [delisting] = source.delistings()
    assert delisting.accepted_at == forged_stamp
    # the pre-seeded stamp made the submissions fetch unnecessary
    assert not any(u.startswith(SUBMISSIONS_URL) and "1738827" in u for u in router.urls)


# --- facts (T11e) ---------------------------------------------------------------

SHARES = "EntityCommonStockSharesOutstanding"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
BULK_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
APPLE_10Q = "0000320193-26-000006"  # in the recorded company facts, absent from FSN
APPLE_10Q_ACCEPTED = datetime(2026, 1, 30, 21, 30, tzinfo=UTC)


def _facts_router(**payloads: object) -> EdgarRouter:
    """`_router()` plus the recorded company facts (overridable per CIK)."""
    router = _router()
    files = {APPLE: "company_facts_plain_issuer.json", ALPHABET: "company_facts_dual_class.json"}
    for cik, name in files.items():
        body = payloads.get(cik)
        router.add(COMPANY_FACTS_URL.format(cik=cik), body if body is not None else load(name))
    return router


def _apple_entries(*entries: dict[str, object]) -> dict[str, object]:
    """A company-facts payload for Apple holding exactly `entries` for `SHARES`."""
    return {
        "cik": 320193,
        "facts": {"dei": {SHARES: {"units": {"shares": list(entries)}}}},
    }


def _entry(accession: str, end: str, value: float) -> dict[str, object]:
    return {"accn": accession, "end": end, "val": value, "filed": "2026-01-01", "form": "10-K"}


def _shares(source: EdgarFilingSource, cik: str) -> list[FactRecord]:
    return source.facts(cik, [SHARES])


def _seed_apple(source: EdgarFilingSource, *extra: SubmissionRecord) -> None:
    records = {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", APPLE_ACCEPTED)}
    records.update({r.accession: r for r in extra})
    _seed_stamps(source, APPLE, records)


def test_plain_issuer_shares_take_company_facts_end_not_fsn_month_end(tmp_path: Path) -> None:
    """0000320193-25-000079 is in both the recorded company facts (end 2025-10-17)
    and its FSN cover page (ddate 2025-10-31): one record, dated 2025-10-17."""
    source = _source(_settings(tmp_path), _facts_router())
    _seed_apple(source)
    [record] = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert record.as_of_date == date(2025, 10, 17)
    assert record.value == 14_776_353_000.0 and record.class_member == ""
    assert record.accepted_at == APPLE_ACCEPTED and record.accepted_at.tzinfo is UTC


def test_facts_are_stamped_from_submissions_never_fsn(tmp_path: Path) -> None:
    source = _source(_settings(tmp_path), _facts_router())
    # A stamp that differs from FSN's own `accepted`/`filed` for the same accession.
    stamped = datetime(2025, 11, 2, 3, 4, 5, tzinfo=UTC)
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", stamped)})
    records = _shares(source, APPLE)
    assert records and all(f.accepted_at == stamped for f in records)
    assert records == sorted(records, key=lambda f: (f.accepted_at, f.accession, f.class_member))


def test_dual_class_shares_come_per_class_from_fsn_capped_at_month_end(tmp_path: Path) -> None:
    """Company facts drop dimensioned facts (the recorded Alphabet payload has
    no `EntityCommonStockSharesOutstanding`), so the per-class shares come
    from FSN, dated min(ddate 2026-01-31, Eastern acceptance 2026-02-04)."""
    source = _source(_settings(tmp_path), _facts_router())
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    records = _shares(source, ALPHABET)
    assert {f.class_member: f.value for f in records} == {
        "CommonClassA": 5_822_000_000.0,
        "CommonClassB": 837_000_000.0,
        "CapitalClassC": 5_438_000_000.0,
    }
    assert {f.as_of_date for f in records} == {date(2026, 1, 31)}
    assert {f.accepted_at for f in records} == {ALPHABET_ACCEPTED}


def test_dual_class_shares_take_the_company_facts_end_of_the_same_accession(
    tmp_path: Path,
) -> None:
    """With an undimensioned company-facts entry for the accession (end
    2026-01-28, the real cover date), every class takes that date."""
    payload = {
        "cik": 1652044,
        "facts": {
            "dei": {
                SHARES: {
                    "units": {"shares": [_entry(ALPHABET_ACCESSION, "2026-01-28", 12_097_000_000)]}
                }
            }
        },
    }
    source = _source(_settings(tmp_path), _facts_router(**{ALPHABET: payload}))
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    records = _shares(source, ALPHABET)
    assert {f.as_of_date for f in records} == {date(2026, 1, 28)}
    assert {f.class_member for f in records} == {
        "",
        "CommonClassA",
        "CommonClassB",
        "CapitalClassC",
    }


def _lag_window_zip_with_synthetic_10q(accession: str, ddate: str) -> bytes:
    """The synthetic newest period, plus an FSN-only 10-Q for Apple whose
    `ddate` (a month end) can fall after the filing's acceptance."""
    return _fsn_zip_bytes(
        [_sub(BLANK_SIC_ACCESSION, "320193", "8-K", sic=""), _sub(accession, "320193", "10-Q")],
        [_num(accession, SHARES, "14000000000.0000", ddate)],
        [
            _txt(BLANK_SIC_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(BLANK_SIC_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(BLANK_SIC_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
            _txt(accession, "Security12bTitle", "Common Stock"),
            _txt(accession, "TradingSymbol", "AAPL"),
            _txt(accession, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )


def test_an_fsn_only_month_end_after_acceptance_is_capped_at_the_eastern_date(
    tmp_path: Path,
) -> None:
    accession = "0000320193-26-000300"
    router = _facts_router()
    router.add(_fsn_zip_url("2026_03"), _lag_window_zip_with_synthetic_10q(accession, "20260331"))
    source = _source(_settings(tmp_path), router)
    # Accepted 2026-03-21 00:30 UTC, which is 2026-03-20 in New York.
    accepted = datetime(2026, 3, 21, 0, 30, tzinfo=UTC)
    _seed_apple(source, _record(accession, "10-Q", accepted))
    [record] = [f for f in _shares(source, APPLE) if f.accession == accession]
    assert record.as_of_date == date(2026, 3, 20)
    assert record.value == 14_000_000_000.0 and record.accepted_at == accepted


@pytest.mark.parametrize("reverse", [False, True])
def test_shares_at_several_ddates_serve_the_latest_never_after_acceptance(
    tmp_path: Path, reverse: bool
) -> None:
    """#609 F1, no look-ahead: an FSN-only 10-Q reporting the share count at
    two ddates (the shape of 0001104659-26-008700) is served once, with the
    latest ddate's value, dated no later than the filing's acceptance in New
    York even though that month end (2026-03-31) falls after it."""
    accession = "0000320193-26-000300"
    rows = [
        _num(accession, SHARES, "13900000000.0000", "20251231"),
        _num(accession, SHARES, "14000000000.0000", "20260331"),
    ]
    zip_bytes = _fsn_zip_bytes(
        [_sub(BLANK_SIC_ACCESSION, "320193", "8-K", sic=""), _sub(accession, "320193", "10-Q")],
        rows[::-1] if reverse else rows,
        [
            _txt(BLANK_SIC_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(BLANK_SIC_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(BLANK_SIC_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )
    router = _facts_router()
    router.add(_fsn_zip_url("2026_03"), zip_bytes)
    source = _source(_settings(tmp_path), router)
    accepted = datetime(2026, 3, 21, 0, 30, tzinfo=UTC)  # 2026-03-20 in New York
    _seed_apple(source, _record(accession, "10-Q", accepted))
    [record] = [f for f in _shares(source, APPLE) if f.accession == accession]
    assert record.value == 14_000_000_000.0
    assert record.as_of_date <= accepted.astimezone(ZoneInfo("America/New_York")).date()
    assert record.as_of_date == date(2026, 3, 20)


def test_a_lag_window_accession_keeps_its_own_cover_date(tmp_path: Path) -> None:
    """An accession absent from FSN inside the lag window: its shares come from
    the per-document parse (the recorded Apple document, cover date
    2025-10-17), never re-dated, and the document is fetched once."""
    accession = "0000320193-26-000050"
    router = _facts_router()
    router.add(_download_url(APPLE, accession, "lagwin.htm"), _COVER_DOCUMENT)
    source = _source(_settings(tmp_path), router)
    _seed_apple(source, _record(accession, "10-K", INSIDE_LAG, primary_document="lagwin.htm"))
    [record] = [f for f in _shares(source, APPLE) if f.accession == accession]
    assert (record.as_of_date, record.value) == (date(2025, 10, 17), 14_776_353_000.0)
    assert record.accepted_at == INSIDE_LAG
    before = len(router.urls)
    assert _shares(source, APPLE) == _shares(source, APPLE)
    assert router.urls[before:] == []
    document_url = _download_url(APPLE, accession, "lagwin.htm")
    assert len([u for u in router.urls if u == document_url]) == 1


def test_two_same_source_records_with_different_dates_keep_their_own_keys(
    tmp_path: Path,
) -> None:
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000),
        _entry(APPLE_ACCESSION, "2025-09-27", 14_800_000_000),
    )
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source)
    records = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert {(f.as_of_date, f.value) for f in records} == {
        (date(2025, 10, 17), 14_776_353_000.0),
        (date(2025, 9, 27), 14_800_000_000.0),
    }


def test_a_date_that_changes_between_runs_is_served_under_the_new_date(tmp_path: Path) -> None:
    """Run 1: company facts do not yet hold the accession, so its FSN shares
    take the capped month end. Run 2: a newer cover-form accession is stamped
    (the cache key changes), company facts now hold it, and the same accession
    and class are served under the company-facts date. The store then resolves
    the pair to the latest-ingested row (`tests/store/test_asof.py`)."""
    settings = _settings(tmp_path)
    source = _source(settings, _facts_router(**{APPLE: _apple_entries()}))
    _seed_apple(source)
    [first] = _shares(source, APPLE)
    assert first.as_of_date == date(2025, 10, 31)  # min(ddate, acceptance 2025-10-31 ET)

    source2 = _source(settings, _facts_router())
    _seed_apple(source2, _record(APPLE_10Q, "10-Q", APPLE_10Q_ACCEPTED))
    [second] = [f for f in _shares(source2, APPLE) if f.accession == APPLE_ACCESSION]
    assert second.as_of_date == date(2025, 10, 17)
    assert (second.class_member, second.value) == (first.class_member, first.value)


def test_an_unstamped_fsn_accession_shares_are_absent_then_present(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _facts_router(**{APPLE: _apple_entries()})
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {})
    assert _shares(source, APPLE) == []
    _seed_apple(source)
    [record] = _shares(source, APPLE)
    assert record.accession == APPLE_ACCESSION and record.accepted_at == APPLE_ACCEPTED


def test_a_forged_collision_skips_that_key_and_records(tmp_path: Path) -> None:
    """T11h: a per-document cache entry for the FSN-covered accession whose
    share count differs from company facts withholds that key (skip, not
    raise), records it and leaves the run `ok`."""
    settings = _settings(tmp_path)
    source = _source(settings, _facts_router())
    _seed_apple(source)
    forged = {
        "version": COVER_VERSION,
        "accession": APPLE_ACCESSION,
        "cik": APPLE,
        "entity_cik": APPLE,
        "listings": [["Common Stock", "AAPL", "NASDAQ"]],
        "facts": [[SHARES, "2025-10-17", "", 1.0]],
    }
    cache_path = (
        Path(settings.edgar.cache_dir) / "cover" / f"v{COVER_VERSION}" / f"{APPLE_ACCESSION}.json"
    )
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(forged))
    assert _shares(source, APPLE) == []
    assert source.failed_filings == 1


def test_a_forged_collision_withholds_only_that_class_serves_the_rest(tmp_path: Path) -> None:
    """T11h: a collision on one (accession, fact name, class member) key
    withholds only that key; the accession's other classes are still
    served."""
    settings = _settings(tmp_path)
    source = _source(settings, _facts_router())
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    forged = {
        "version": COVER_VERSION,
        "accession": ALPHABET_ACCESSION,
        "cik": ALPHABET,
        "entity_cik": ALPHABET,
        "listings": [],
        "facts": [
            # the same source, same date, two values for CommonClassA: a collision
            [SHARES, "2026-01-31", "CommonClassA", 1.0],
            [SHARES, "2026-01-31", "CommonClassA", 2.0],
            [SHARES, "2026-01-31", "CommonClassB", 837_000_000.0],
            [SHARES, "2026-01-31", "CapitalClassC", 5_438_000_000.0],
        ],
    }
    cache_path = (
        Path(settings.edgar.cache_dir)
        / "cover"
        / f"v{COVER_VERSION}"
        / f"{ALPHABET_ACCESSION}.json"
    )
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(forged))
    records = _shares(source, ALPHABET)
    assert {f.class_member: f.value for f in records} == {
        "CommonClassB": 837_000_000.0,
        "CapitalClassC": 5_438_000_000.0,
    }
    assert source.failed_filings == 1


def test_a_company_facts_404_counts_missing_not_a_failure(tmp_path: Path) -> None:
    """T11h/T11e's leftover: a company-facts 404 (an issuer with no XBRL
    facts) is counted on `.facts_missing`, never `.failed_filings` or
    `failed_filings.json`; the FSN share still stands (only the company-facts
    source is missing), and the empty company-facts result is cached under
    the facts key so a new cover-form accession refetches it."""
    settings = _settings(tmp_path)
    router = _facts_router(**{APPLE: 404})
    source = _source(settings, router)
    _seed_apple(source)
    records = _shares(source, APPLE)
    assert [r.accession for r in records] == [APPLE_ACCESSION]  # FSN's row still served
    assert source.facts_missing == 1
    assert source.failed_filings == 0
    assert not (Path(settings.edgar.cache_dir) / "failed_filings.json").exists()
    # cached: a second facts() call for the same CIK makes no new request
    before = router.urls.count(COMPANY_FACTS_URL.format(cik=APPLE))
    assert _shares(source, APPLE) == records
    assert router.urls.count(COMPANY_FACTS_URL.format(cik=APPLE)) == before
    # a new cover-form accession changes the facts-cache key: refetches
    _seed_apple(source, _record(APPLE_10Q, "10-Q", APPLE_10Q_ACCEPTED))
    _shares(source, APPLE)
    assert router.urls.count(COMPANY_FACTS_URL.format(cik=APPLE)) == before + 1
    assert source.facts_missing == 2


def test_a_second_facts_call_for_an_unchanged_cik_makes_no_request(tmp_path: Path) -> None:
    router = _facts_router()
    source = _source(_settings(tmp_path), router)
    _seed_apple(source)
    first = _shares(source, APPLE)
    before = len(router.urls)
    assert _shares(source, APPLE) == first
    assert router.urls[before:] == []
    # and a fresh source on the same cache does not re-fetch company facts
    again = _source(_settings(tmp_path), router)
    _seed_apple(again)
    assert _shares(again, APPLE) == first
    assert COMPANY_FACTS_URL.format(cik=APPLE) not in router.urls[before:]


def _facts_cache_key(settings: Settings, cik: str) -> str | None:
    path = Path(settings.edgar.cache_dir) / "facts" / f"v{PARSER_VERSION}" / f"{cik}.json"
    data = json.loads(path.read_text())
    assert data["version"] == PARSER_VERSION and data["cik"] == cik
    accession, names = str(data["key"]).split("|", 1)
    assert names == SHARES
    return accession


def test_company_facts_are_cached_by_the_latest_cover_form_accession(tmp_path: Path) -> None:
    """Per-CIK path: the cache key is the CIK's latest stamped cover-form
    accession; a newer stamped 10-K/A invalidates it, an 8-K does not."""
    settings = _settings(tmp_path)
    router = _facts_router()
    source = _source(settings, router)
    _seed_apple(source)
    _shares(source, APPLE)
    assert _facts_cache_key(settings, APPLE) == APPLE_ACCESSION
    url = COMPANY_FACTS_URL.format(cik=APPLE)
    assert router.urls.count(url) == 1
    eight_k = "0000320193-26-000700"
    _seed_apple(source, _record(eight_k, "8-K", INSIDE_LAG, inline_xbrl=False))
    _shares(source, APPLE)
    assert router.urls.count(url) == 1 and _facts_cache_key(settings, APPLE) == APPLE_ACCESSION
    # A newer cover-form filing the payload already holds: re-fetched and re-keyed.
    ten_q = _record(APPLE_10Q, "10-Q", APPLE_10Q_ACCEPTED, inline_xbrl=False)
    _seed_apple(source, ten_q)
    _shares(source, APPLE)
    assert router.urls.count(url) == 2 and _facts_cache_key(settings, APPLE) == APPLE_10Q
    # A 10-K/A the payload does not hold yet: re-fetched, served, but the cache
    # keeps the last complete key so the next run fetches again.
    amendment = "0000320193-26-000701"
    _seed_apple(source, ten_q, _record(amendment, "10-K/A", INSIDE_LAG, inline_xbrl=False))
    _shares(source, APPLE)
    assert router.urls.count(url) == 3 and _facts_cache_key(settings, APPLE) == APPLE_10Q


def test_the_bulk_company_facts_path_caches_by_the_same_key(tmp_path: Path) -> None:
    """Above `edgar.bulk_stamp_threshold_ciks` stale CIKs, one `companyfacts.zip`
    serves them all with no per-CIK request; a CIK absent from the zip falls
    back to its per-CIK payload; the cache key is the same as the per-CIK path's."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        bulk.write(FIXTURES / "company_facts_plain_issuer.json", f"CIK{APPLE}.json")
    settings = _settings(tmp_path, bulk_stamp_threshold_ciks=1)
    router = _facts_router()
    router.add(BULK_FACTS_URL, buffer.getvalue())
    source = _source(settings, router)
    _seed_apple(source)
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    apple = _shares(source, APPLE)
    alphabet = _shares(source, ALPHABET)
    assert [f.accession for f in apple] == [APPLE_ACCESSION] and len(alphabet) == 3
    assert router.urls.count(BULK_FACTS_URL) == 1
    assert COMPANY_FACTS_URL.format(cik=APPLE) not in router.urls
    assert router.urls.count(COMPANY_FACTS_URL.format(cik=ALPHABET)) == 1  # absent from the zip
    assert _facts_cache_key(settings, APPLE) == APPLE_ACCESSION
    assert _facts_cache_key(settings, ALPHABET) == ALPHABET_ACCESSION
    per_cik = _source(_settings(tmp_path / "per-cik"), _facts_router())
    _seed_apple(per_cik)
    assert _shares(per_cik, APPLE) == apple


def test_facts_after_a_real_filing_index(tmp_path: Path) -> None:
    """End to end (the pattern the auditors asked for on T11d): no hand-written
    stamps. `filing_index()` stamps the two recorded accessions from the
    recorded submissions; `facts` serves the plain issuer's one shares record
    at the company-facts date and the dual-class per-class shares, all at the
    submissions stamp, and a second source rebuilds them with no new fetch."""
    from edgar_transport import index_header, index_line

    settings = edgar_settings(tmp_path, index_first_year=2025, fsn_first_year=2025)
    router = _facts_router()
    lines = {
        (2025, 4): index_line("10-K", "Apple Inc.", 320193, "2025-10-31", APPLE_ACCESSION),
        (2026, 1): index_line("10-K", "Alphabet Inc.", 1652044, "2026-02-05", ALPHABET_ACCESSION),
    }
    for year, qtr in [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
        router.add_index(year, qtr, index_header() + lines.get((year, qtr), ""))
    clock = datetime(2026, 4, 15, tzinfo=UTC)

    def run() -> tuple[list[FactRecord], list[FactRecord]]:
        source = EdgarFilingSource(settings, client=router.client(), clock=lambda: clock)
        source.filing_index()
        return _shares(source, APPLE), _shares(source, ALPHABET)

    apple, alphabet = run()
    [apple_record] = [f for f in apple if f.accession == APPLE_ACCESSION]
    assert (apple_record.as_of_date, apple_record.accepted_at) == (
        date(2025, 10, 17),
        APPLE_ACCEPTED,
    )
    assert apple_record.accepted_at.tzinfo is UTC
    per_class = {
        f.class_member: f.as_of_date for f in alphabet if f.accession == ALPHABET_ACCESSION
    }
    assert per_class == {
        m: date(2026, 1, 31) for m in ("CommonClassA", "CommonClassB", "CapitalClassC")
    }
    assert {f.accepted_at for f in alphabet} == {ALPHABET_ACCEPTED}
    before = len(router.urls)
    assert run() == (apple, alphabet)
    later = router.urls[before:]
    assert not [u for u in later if "companyfacts" in u or "data.sec.gov/submissions" in u]


# --- facts: reviewer fixes (#253) ---------------------------------------------


def test_a_co_registrant_gets_no_company_facts_from_the_filers_accession(tmp_path: Path) -> None:
    """Apple's company facts (synthetic) carry Alphabet's combined-filing
    accession, stamped under Apple too: FSN holds it under Alphabet, so Apple
    is never served the filer's share count (bitfly's handoff on #252)."""
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000),
        _entry(ALPHABET_ACCESSION, "2026-01-28", 12_097_000_000),
    )
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source, _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED))
    assert {f.accession for f in _shares(source, APPLE)} == {APPLE_ACCESSION}


def test_a_payload_trailing_the_latest_filing_is_not_cached(tmp_path: Path) -> None:
    """The cache key is the latest stamped cover-form accession; a payload
    that does not hold it yet (the API trails acceptance) is served but not
    cached, so the next run re-fetches and re-dates with no new accession."""
    settings = _settings(tmp_path)
    router = _facts_router(**{APPLE: _apple_entries()})
    source = _source(settings, router)
    _seed_apple(source)
    [first] = _shares(source, APPLE)
    assert first.as_of_date == date(2025, 10, 31)
    cache = Path(settings.edgar.cache_dir) / "facts" / f"v{PARSER_VERSION}" / f"{APPLE}.json"
    assert not cache.exists()

    source2 = _source(settings, _facts_router())
    _seed_apple(source2)
    [second] = _shares(source2, APPLE)
    assert second.as_of_date == date(2025, 10, 17) and cache.exists()


def test_the_bulk_payload_trailing_the_latest_filing_falls_back_to_the_api(
    tmp_path: Path,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:  # rebuilt nightly: lacks today's filing
        bulk.writestr(f"CIK{APPLE}.json", json.dumps(_apple_entries()))
    settings = _settings(tmp_path, bulk_stamp_threshold_ciks=1)
    router = _facts_router()
    router.add(BULK_FACTS_URL, buffer.getvalue())
    source = _source(settings, router)
    _seed_apple(source)
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    [record] = _shares(source, APPLE)
    assert record.as_of_date == date(2025, 10, 17)
    assert router.urls.count(BULK_FACTS_URL) == 1
    assert router.urls.count(COMPANY_FACTS_URL.format(cik=APPLE)) == 1


def test_a_lag_window_cover_date_wins_over_a_differing_company_facts_end(tmp_path: Path) -> None:
    accession = "0000320193-26-000050"
    payload = _apple_entries(_entry(accession, "2025-12-31", 14_776_353_000))
    router = _facts_router(**{APPLE: payload})
    router.add(_download_url(APPLE, accession, "lagwin.htm"), _COVER_DOCUMENT)
    source = _source(_settings(tmp_path), router)
    _seed_apple(source, _record(accession, "10-K", INSIDE_LAG, primary_document="lagwin.htm"))
    records = [f for f in _shares(source, APPLE) if f.accession == accession]
    assert [(f.as_of_date, f.value) for f in records] == [(date(2025, 10, 17), 14_776_353_000.0)]


def test_a_same_date_disagreement_skips_that_key_even_when_another_value_matches(
    tmp_path: Path,
) -> None:
    """T11h: company facts {V1 on D1, V9 on D2} against FSN's V1 dated D2 (the
    FSN share takes the latest company-facts end): the values on D2 differ,
    so an overlapping value elsewhere does not excuse the clash; the key is
    skipped and recorded instead of raising."""
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-09-27", 14_776_353_000),
        _entry(APPLE_ACCESSION, "2025-10-17", 99),
    )
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source)
    assert _shares(source, APPLE) == []
    assert source.failed_filings == 1


def test_a_company_facts_end_after_acceptance_is_capped(tmp_path: Path) -> None:
    payload = _apple_entries(_entry(APPLE_ACCESSION, "2035-10-17", 14_776_353_000))
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source)
    assert {f.as_of_date for f in _shares(source, APPLE)} == {date(2025, 10, 31)}


def test_a_new_name_re_fetches_company_facts(tmp_path: Path) -> None:
    router = _facts_router()
    source = _source(_settings(tmp_path), router)
    _seed_apple(source)
    _shares(source, APPLE)
    url = COMPANY_FACTS_URL.format(cik=APPLE)
    assert router.urls.count(url) == 1
    floats = source.facts(APPLE, [SHARES, "EntityPublicFloat"])
    assert router.urls.count(url) == 2
    assert {f.fact_name for f in floats if f.accession == APPLE_ACCESSION} == {
        SHARES,
        "EntityPublicFloat",
    }


def test_a_malformed_cik_is_refused_before_any_io(tmp_path: Path) -> None:
    router = _facts_router()
    source = _source(_settings(tmp_path), router)
    with pytest.raises(ValueError, match="10-digit"):
        source.facts("320193", [SHARES])
    with pytest.raises(ValueError, match="10-digit"):
        source.facts("../0000320193", [SHARES])
    assert router.urls == []


def test_a_bulk_payload_for_another_cik_raises(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        bulk.write(FIXTURES / "company_facts_dual_class.json", f"CIK{APPLE}.json")
    settings = _settings(tmp_path, bulk_stamp_threshold_ciks=1)
    router = _facts_router()
    router.add(BULK_FACTS_URL, buffer.getvalue())
    source = _source(settings, router)
    _seed_apple(source)
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    with pytest.raises(ValueError, match="served for"):
        _shares(source, APPLE)


# --- an empty or keyless companyfacts.zip member (#566) -------------------------


def _bulk_zip_members(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        for cik, body in members.items():
            bulk.writestr(f"CIK{cik}.json", body)
    return buffer.getvalue()


def _bulk_facts_source(
    tmp_path: Path, zip_bytes: bytes, **api: object
) -> tuple[EdgarFilingSource, EdgarRouter]:
    """A source on the bulk path (threshold 1, two stale CIKs) with the
    per-CIK API overridable per CIK (`_facts_router`)."""
    settings = _settings(tmp_path, bulk_stamp_threshold_ciks=1)
    router = _facts_router(**api)
    router.add(BULK_FACTS_URL, zip_bytes)
    source = _source(settings, router)
    _seed_apple(source)
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    return source, router


def test_an_empty_bulk_member_falls_back_to_the_api_and_a_404_is_missing(tmp_path: Path) -> None:
    """#566: SEC's zip has `{}` members. One is treated as absent from the zip:
    the per-CIK API is asked, and its 404 is the ordinary `facts_missing`
    path, never "no facts" without asking."""
    zip_bytes = _bulk_zip_members({APPLE: b"{}"})
    source, router = _bulk_facts_source(tmp_path, zip_bytes, **{APPLE: 404})
    records = _shares(source, APPLE)
    assert COMPANY_FACTS_URL.format(cik=APPLE) in router.urls
    assert (source.facts_bulk_empty, source.facts_missing, source.failed_filings) == (1, 1, 0)
    assert source.facts_api_empty == 0  # #576: a 404 is not an empty 200
    # nothing from company facts: FSN's share alone, dated min(ddate, acceptance)
    assert [r.as_of_date for r in records if r.accession == APPLE_ACCESSION] == [date(2025, 10, 31)]


def test_an_empty_bulk_member_falls_back_to_the_api_and_a_200_is_used(tmp_path: Path) -> None:
    """#566: the API's payload is served exactly as for a CIK the zip lacks."""
    source, router = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: b"{}"}))
    records = _shares(source, APPLE)
    assert COMPANY_FACTS_URL.format(cik=APPLE) in router.urls
    assert (source.facts_bulk_empty, source.facts_missing) == (1, 0)
    absent, _ = _bulk_facts_source(tmp_path / "absent", _bulk_zip_members({}))
    assert records == _shares(absent, APPLE)
    # company facts came from the API: dated by their `end`, not FSN's month end
    assert [r.as_of_date for r in records if r.accession == APPLE_ACCESSION] == [date(2025, 10, 17)]


def test_a_good_bulk_member_is_unchanged_by_an_empty_neighbour(tmp_path: Path) -> None:
    """#566, no look-ahead: a CIK with a good member is served from the zip
    exactly as before, with no API call, whatever other members hold."""
    good = json.dumps(_apple_entries(_entry(APPLE_ACCESSION, "2025-10-17", 5.0))).encode()
    alone, alone_router = _bulk_facts_source(tmp_path / "alone", _bulk_zip_members({APPLE: good}))
    mixed, mixed_router = _bulk_facts_source(
        tmp_path / "mixed", _bulk_zip_members({APPLE: good, ALPHABET: b"{}"})
    )
    assert _shares(mixed, APPLE) == _shares(alone, APPLE)
    api = COMPANY_FACTS_URL.format(cik=APPLE)
    assert api not in mixed_router.urls and api not in alone_router.urls
    assert mixed.facts_bulk_empty == 0  # counted only when that CIK is asked for


def test_a_bulk_member_that_is_not_json_still_raises(tmp_path: Path) -> None:
    """Fail closed: only an empty or `cik`-less object counts as absent; a
    member that is not JSON still fails the source."""
    source, _ = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: b"not json"}))
    with pytest.raises(ValueError):
        _shares(source, APPLE)


# --- a per-CIK companyfacts API 200 `{}` (#576) --------------------------------


@pytest.mark.parametrize("bulk", [True, False], ids=["bulk-empty-member", "per-cik"])
def test_an_empty_api_payload_is_missing_like_a_404_and_cached(tmp_path: Path, bulk: bool) -> None:
    """#576: SEC's API answers 200 `{}` for the CIKs whose zip member is `{}`.
    That is SEC's "no XBRL facts", handled exactly like a 404: missing,
    counted, its empty result cached, and the run goes on."""
    if bulk:
        source, router = _bulk_facts_source(
            tmp_path, _bulk_zip_members({APPLE: b"{}"}), **{APPLE: {}}
        )
        expected_bulk_empty = 1
    else:
        settings = _settings(tmp_path)
        router = _facts_router(**{APPLE: {}})
        source = _source(settings, router)
        _seed_apple(source)
        expected_bulk_empty = 0
    records = _shares(source, APPLE)
    counts = (source.facts_bulk_empty, source.facts_api_empty, source.facts_missing)
    assert counts == (expected_bulk_empty, 1, 1)
    assert source.failed_filings == 0
    # exactly what a 404 gives: FSN's share alone, dated min(ddate, acceptance)
    assert [r.as_of_date for r in records if r.accession == APPLE_ACCESSION] == [date(2025, 10, 31)]
    # cached: a later run on the same cache does not ask the API again
    again = _source(source._settings, router)
    assert _shares(again, APPLE) == records
    assert router.urls.count(COMPANY_FACTS_URL.format(cik=APPLE)) == 1


def test_a_non_empty_api_payload_without_facts_still_fails(tmp_path: Path) -> None:
    """Fail closed (#576, #599): only an empty object is "no facts", and only
    a payload with `facts` is identified by its requested CIK; a non-empty
    payload missing `facts` still fails the source."""
    payload = {"cik": 320193}
    source, _ = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: b"{}"}), **{APPLE: payload})
    with pytest.raises(KeyError):
        _shares(source, APPLE)
    assert source.facts_api_empty == 0


def test_an_api_payload_that_is_not_an_object_still_fails(tmp_path: Path) -> None:
    """Fail closed (#576): an empty list is not an empty object."""
    source, _ = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: b"{}"}), **{APPLE: b"[]"})
    with pytest.raises(TypeError):
        _shares(source, APPLE)
    assert source.facts_api_empty == 0


def test_an_api_payload_for_another_cik_still_raises(tmp_path: Path) -> None:
    """#576 leaves the API's CIK check as it was: another CIK's facts raise."""
    settings = _settings(tmp_path)
    router = _facts_router(**{APPLE: load("company_facts_dual_class.json")})
    source = _source(settings, router)
    _seed_apple(source)
    with pytest.raises(ValueError, match="served for"):
        _shares(source, APPLE)


# --- a keyless companyfacts payload with facts (#599) --------------------------


def _keyless_apple_entries(*entries: dict[str, object]) -> dict[str, object]:
    """`_apple_entries`, with no `cik` field (SEC ships this shape for a
    handful of CIKs, identically from the zip and the API, #599)."""
    return {"entityName": "x", "facts": {"dei": {SHARES: {"units": {"shares": list(entries)}}}}}


def test_a_keyless_bulk_member_with_facts_is_used_for_its_cik(tmp_path: Path) -> None:
    """#599: a zip member with `entityName`/`facts` and no `cik` is served
    under the CIK it was requested under (the zip member name), counted on
    `facts_bulk_keyless`, and never asked of the API since it already holds
    the latest accession."""
    member = json.dumps(
        _keyless_apple_entries(_entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000))
    ).encode()
    source, router = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: member}))
    [record] = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert (record.cik, record.value, record.as_of_date) == (
        APPLE,
        14_776_353_000.0,
        date(2025, 10, 17),
    )
    assert source.facts_bulk_keyless == 1
    assert source.facts_bulk_empty == 0
    assert COMPANY_FACTS_URL.format(cik=APPLE) not in router.urls


def test_a_keyless_api_payload_with_facts_is_used_for_its_cik(tmp_path: Path) -> None:
    """#599: the same shape from the per-CIK API (the zip has no member for
    the CIK), identified by the API URL it was requested under and counted
    on `facts_api_keyless`."""
    payload = _keyless_apple_entries(_entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000))
    source, router = _bulk_facts_source(tmp_path, _bulk_zip_members({}), **{APPLE: payload})
    [record] = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert (record.cik, record.value, record.as_of_date) == (
        APPLE,
        14_776_353_000.0,
        date(2025, 10, 17),
    )
    assert source.facts_api_keyless == 1
    assert source.facts_api_empty == 0
    assert COMPANY_FACTS_URL.format(cik=APPLE) in router.urls


def test_a_keyless_bulk_member_incomplete_falls_back_to_the_api(tmp_path: Path) -> None:
    """A keyless bulk member that does not yet hold the latest accession (the
    zip trails the day's filings, as for a normal member) still falls back to
    the per-CIK API, which here answers with the same keyless shape."""
    bulk_member = json.dumps(_keyless_apple_entries()).encode()  # holds nothing yet
    payload = _keyless_apple_entries(_entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000))
    source, router = _bulk_facts_source(
        tmp_path, _bulk_zip_members({APPLE: bulk_member}), **{APPLE: payload}
    )
    [record] = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert record.cik == APPLE
    assert (source.facts_bulk_keyless, source.facts_api_keyless) == (1, 1)
    assert COMPANY_FACTS_URL.format(cik=APPLE) in router.urls


def test_a_keyless_payload_is_cached_under_the_requested_cik(tmp_path: Path) -> None:
    """The facts cache written for a keyless bulk payload is keyed and
    readable by the requested CIK: a second source over the same cache
    directory serves the same records without reading the zip again."""
    member = json.dumps(
        _keyless_apple_entries(_entry(APPLE_ACCESSION, "2025-10-17", 14_776_353_000))
    ).encode()
    source, router = _bulk_facts_source(tmp_path, _bulk_zip_members({APPLE: member}))
    records = _shares(source, APPLE)
    again = _source(source._settings, _facts_router())
    assert _shares(again, APPLE) == records
    assert router.urls.count(BULK_FACTS_URL) == 1


# --- review fixes (#262) -----------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    ["{truncated", '{"version": 999}', '{"version": 1, "accession": "0000000000-00-000000"}'],
    ids=["truncated", "other-version", "other-accession"],
)
def test_an_unreadable_delisting_cache_entry_is_fetched_again(tmp_path: Path, stored: str) -> None:
    """A corrupt or foreign cache entry reads as absent, never fails the
    chunk: the document is fetched again and parsed correctly."""
    router = _delisting_router()
    _add_klx_document(router)
    source = _delisting_source(router, tmp_path)
    [expected] = source.delistings()
    cache_path = (
        Path(source._settings.edgar.cache_dir)
        / "delisting"
        / f"v{DELISTING_VERSION}"
        / f"{KLX_25NSE}.json"
    )
    cache_path.write_text(stored)
    fresh = _delisting_source(router, tmp_path)
    before = len(router.urls)
    [again] = fresh.delistings()
    assert again == expected
    document_url = _download_url(KLX, KLX_25NSE, _KLX_ROOT_DOCUMENT)
    assert router.urls[before:].count(document_url) == 1


def test_a_form_25_listed_under_two_ciks_is_returned_once(tmp_path: Path) -> None:
    """De-duplicated by accession (plan T11f): a delisting kept under a
    co-registrant CIK as well as the subject company yields one record, so
    the delistings builder never writes the same row twice."""
    co_registrant = 5555599
    line = index_line("25-NSE", "KLX Subsidiary LLC", co_registrant, "2026-09-24", KLX_25NSE)
    router = _delisting_router(line)
    _add_klx_document(router)
    router.add(
        f"{SUBMISSIONS_URL}CIK{co_registrant:010d}.json",
        _xml_payload(co_registrant, KLX_25NSE, "2026-09-24T14:08:40", form="25-NSE"),
    )
    source = _delisting_source(router, tmp_path)
    results = source.delistings()
    assert [d.accession for d in results].count(KLX_25NSE) == 1


def test_an_unstamped_delisting_is_counted_not_lost(tmp_path: Path) -> None:
    """A 25-NSE indexed only under the exchange's CIK (a row `filing_index`
    never keeps, so its `.unstamped_filings` never sees it) that the
    submissions do not list is excluded and counted on
    `.unstamped_delistings`, never silently dropped."""
    exchange, accession = 1354457, "0001354457-26-000950"
    line = index_line("25-NSE", "Nasdaq Stock Market LLC", exchange, "2026-09-10", accession)
    router = _delisting_router(line)
    _add_klx_document(router)
    router.add(
        f"{SUBMISSIONS_URL}CIK{exchange:010d}.json",
        _xml_payload(exchange, "0001354457-26-000001", "2026-01-05T15:00:00", form="25-NSE"),
    )
    source = _delisting_source(router, tmp_path)
    assert accession not in {d.accession for d in source.delistings()}
    assert source.unstamped_delistings == 1


def test_a_pre_xml_form_25_under_two_ciks_is_counted_once(tmp_path: Path) -> None:
    """`.pre_xml_delistings` counts accessions, not (CIK, accession) pairs."""
    form_25 = "0005555570-26-000001"
    lines = (
        index_line("25", "Old Co", 5555570, "2026-09-01", form_25),
        index_line("25", "Old Co Parent", 5555571, "2026-09-01", form_25),
    )
    router = _delisting_router(*lines)
    _add_klx_document(router)
    for cik in (5555570, 5555571):
        router.add(
            f"{SUBMISSIONS_URL}CIK{cik:010d}.json",
            _index_payload(cik, (form_25, "25", "2026-09-01T20:00:00")),  # doc.htm: pre-XML
        )
    source = _delisting_source(router, tmp_path)
    source.delistings()
    assert source.pre_xml_delistings == 1


def _bulk_zip(*ciks: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bulk:
        for cik in ciks:
            bulk.writestr(f"CIK{cik}.json", json.dumps(_apple_entries()))
    return buffer.getvalue()


def _bulk_source(tmp_path: Path, zip_bytes: bytes) -> tuple[EdgarFilingSource, EdgarRouter]:
    settings = _settings(tmp_path, bulk_stamp_threshold_ciks=1)
    router = _facts_router(**{APPLE: 404})
    router.add(BULK_FACTS_URL, zip_bytes)
    source = _source(settings, router)
    _seed_apple(source)
    _seed_stamps(
        source,
        ALPHABET,
        {ALPHABET_ACCESSION: _record(ALPHABET_ACCESSION, "10-K", ALPHABET_ACCEPTED)},
    )
    return source, router


def test_a_cik_absent_from_the_bulk_zip_and_404_counts_missing(tmp_path: Path) -> None:
    """Plan T11h: a CIK absent from `companyfacts.zip` (then 404 from the API)
    is counted on `.facts_missing`, not a filing failure."""
    source, _router = _bulk_source(tmp_path, _bulk_zip(ALPHABET))
    _shares(source, APPLE)
    assert (source.facts_missing, source.failed_filings) == (1, 0)


def test_a_bulk_payload_is_kept_when_the_api_404s(tmp_path: Path) -> None:
    """The zip holds the CIK but trails its latest filing and the API 404s:
    the zip's facts are served, not replaced by an empty cached result, and
    the result is not cached as complete, so the next run asks again."""
    source, router = _bulk_source(tmp_path, _bulk_zip(APPLE))
    first = _shares(source, APPLE)
    assert source.facts_missing == 0
    assert any(f.accession == APPLE_ACCESSION for f in first)
    api = COMPANY_FACTS_URL.format(cik=APPLE)
    before = router.urls.count(api)
    fresh = _source(source._settings, router)
    _shares(fresh, APPLE)
    assert router.urls.count(api) == before + 1  # not cached as complete

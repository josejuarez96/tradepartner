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
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from edgar_transport import FIXTURES, EdgarRouter, edgar_settings
from test_edgar_fsn import FSN_PAGE_URL, _fsn_zip_bytes, _fsn_zip_url, _num, _sub, _txt

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar_source import (
    COVER_VERSION,
    HEADER_VERSION,
    EdgarFilingSource,
    SubmissionRecord,
)
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
    router.add(_download_url("0000000099", failed_accession, "fail.htm"), _COVER_DOCUMENT)
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


def test_a_header_naming_another_accession_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    accession = "0000320193-26-000080"
    router.add(_header_url(APPLE, accession), _synthetic_header("0000320193-26-999999"))
    source = _source(settings, router)
    _seed_stamps(source, APPLE, {accession: _record(accession, "10-K", INSIDE_LAG)})
    with pytest.raises(ValueError, match="names accession"):
        source.filing_headers(APPLE, ["10-K"])


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

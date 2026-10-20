"""#1494: new per-CIK stamps are checked against the SGML header before they are cached.

From 2026-10 SEC's per-CIK submissions API served `acceptanceDateTime` moved
LATE by New York's UTC offset (4 h in EDT, 5 h in EST) for every row of a
payload, on and off between fetches: CIK 1811210's 8-K 0001628280-26-065525
reads `2026-10-10T00:03:23.000Z` in the API but `<ACCEPTANCE-DATETIME>
20261009160323` (16:03:23 ET, 20:03:23Z) in its SGML header. The header is the
master acceptance. These tests drive `EdgarFilingSource._stamp` over a fake
transport: a synthetic payload, and synthetic SGML headers routed by URL.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from edgar_transport import EdgarRouter, edgar_settings

from tradepartner.adapters.edgar import UnstampedFiling, sgml_acceptance
from tradepartner.adapters.edgar_source import EdgarFilingSource, SubmissionRecord, _fallback_stamp
from tradepartner.config import Settings
from tradepartner.ingest import _source_counts

CIK = "0001811210"
CLOCK = datetime(2026, 10, 11, 12, tzinfo=UTC)
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
#: (accession, form, true acceptance in UTC). The first is the recorded case;
#: the third is in EST, where the API's shift is five hours.
FILINGS = (
    ("0001628280-26-065525", "8-K", datetime(2026, 10, 9, 20, 3, 23, tzinfo=UTC)),
    ("0001628280-26-065038", "4", datetime(2026, 10, 5, 21, 9, 8, tzinfo=UTC)),
    ("0001628280-26-000101", "10-K", datetime(2026, 2, 20, 21, 30, 0, tzinfo=UTC)),
)
EDT, EST = timedelta(hours=4), timedelta(hours=5)
_ET = ZoneInfo("America/New_York")


def _offset(at: datetime) -> timedelta:
    return EST if at.month in (1, 2, 3, 11, 12) else EDT


def _api(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _payload(shift: dict[str, timedelta]) -> dict[str, object]:
    """The API payload; each accession's stamp is its true acceptance plus `shift`."""
    return {
        "cik": CIK,
        "name": "Synthetic",
        "filings": {
            "recent": {
                "accessionNumber": [a for a, _, _ in FILINGS],
                "form": [f for _, f, _ in FILINGS],
                "primaryDocument": ["doc.htm"] * len(FILINGS),
                "isInlineXBRL": [0] * len(FILINGS),
                "acceptanceDateTime": [
                    _api(at + shift.get(a, timedelta())) for a, _, at in FILINGS
                ],
                "filingDate": [at.date().isoformat() for _, _, at in FILINGS],
            }
        },
    }


def _header(accession: str, form: str, at: datetime | None) -> str:
    """A Form-4-like SGML header (no FILER block, so `parse_sgml_header`'s
    issuer rule does not apply), acceptance in Eastern wall-clock time."""
    eastern = at.astimezone(_ET) if at is not None else None
    stamp = f"<ACCEPTANCE-DATETIME>{eastern:%Y%m%d%H%M%S}\n" if eastern is not None else ""
    return (
        f"<SEC-DOCUMENT>{accession}.txt : 20261009\n<SEC-HEADER>{accession}.hdr.sgml : 20261009\n"
        f"{stamp}ACCESSION NUMBER:\t\t{accession}\nCONFORMED SUBMISSION TYPE:\t{form}\n"
        "ISSUER:\n\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY:\t\t\t0001811210\n"
        "REPORTING-OWNER:\n\n\tOWNER DATA:\n\t\tCENTRAL INDEX KEY:\t\t\t0009999999\n"
        "</SEC-HEADER>\n<DOCUMENT>\n"
    )


def _header_url(accession: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/"
        f"{accession.replace('-', '')}/{accession}.txt"
    )


def _router(
    shift: dict[str, timedelta], headers: dict[str, str | int] | None = None
) -> EdgarRouter:
    router = EdgarRouter()
    router.add(SUBMISSIONS_URL, _payload(shift))
    for accession, form, at in FILINGS:
        body = (headers or {}).get(accession, _header(accession, form, at))
        router.add(_header_url(accession), body)
    return router


def _kept() -> dict[str, dict[str, tuple[UnstampedFiling, tuple[int, int]]]]:
    rows = {
        a: (UnstampedFiling(CIK, "Synthetic", form, a, at.date()), (at.year, (at.month + 2) // 3))
        for a, form, at in FILINGS
    }
    return {CIK: rows}


def _stamp(
    settings: Settings, router: EdgarRouter
) -> tuple[EdgarFilingSource, dict[str, SubmissionRecord]]:
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: CLOCK)
    return source, source._stamp(_kept())[CIK]


def _cached(settings: Settings) -> dict[str, list[object]]:
    [path] = (Path(settings.edgar.cache_dir) / "stamps").glob(f"*/{CIK}.json")
    records: dict[str, list[object]] = json.loads(path.read_text())["records"]
    return records


def _header_requests(router: EdgarRouter) -> list[str]:
    return [u for u in router.urls if u.startswith("https://www.sec.gov/Archives/edgar/data/")]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return edgar_settings(tmp_path / "cache", stamp_sgml_check=True)


SHIFTED = {a: _offset(at) for a, _, at in FILINGS}  # the API as served 2026-10-09..11


def test_a_payload_moved_late_by_the_new_york_offset_is_stamped_from_sgml(
    settings: Settings,
) -> None:
    """The bug: every stamp was cached as the API served it, 4 h (5 h in EST) late."""
    router = _router(SHIFTED)
    source, stamps = _stamp(settings, router)
    assert {a: stamps[a].accepted_at for a, _, _ in FILINGS} == {a: at for a, _, at in FILINGS}
    assert {a: row[3] for a, row in _cached(settings).items()} == {
        a: at.isoformat() for a, _, at in FILINGS
    }
    assert source.stamp_check_mismatched_ciks == 1
    assert source.stamps_from_sgml == len(FILINGS)
    # Form, document and items are the payload's; only the stamp is replaced.
    assert stamps[FILINGS[1][0]].form == "4"


def test_a_payload_that_matches_its_anchor_costs_one_header(settings: Settings) -> None:
    router = _router({})
    source, stamps = _stamp(settings, router)
    assert {a: stamps[a].accepted_at for a, _, _ in FILINGS} == {a: at for a, _, at in FILINGS}
    assert _header_requests(router) == [_header_url(FILINGS[0][0])]  # the newest only
    assert (source.stamp_check_mismatched_ciks, source.stamps_from_sgml) == (0, 0)


def test_no_lookahead_a_payload_moved_early_is_never_cached_early(settings: Settings) -> None:
    """The zip's #1382 direction: an early API stamp would be a look-ahead.
    No stamp may be earlier than the SGML acceptance."""
    early = {a: -_offset(at) for a, _, at in FILINGS}
    _, stamps = _stamp(settings, _router(early))
    for accession, _, at in FILINGS:
        accepted_at = stamps[accession].accepted_at
        assert accepted_at is not None and accepted_at >= at
        assert accepted_at == at


def test_a_stamp_whose_header_does_not_read_is_left_out_not_unstampable(
    settings: Settings,
) -> None:
    """Moved payload, one header 404s: that accession is not cached at all
    (neither the moved stamp nor the unstampable sentinel, though its
    quarter has settled), so the next run asks again."""
    missing = FILINGS[2][0]  # 2026 Q1: settled long before the clock
    router = _router(SHIFTED, {missing: 404})
    source, stamps = _stamp(settings, router)
    assert missing not in stamps
    assert missing not in _cached(settings)
    assert source.stamps_sgml_unread == 1
    assert source.stamps_sgml_overlong == 0
    assert {a: stamps[a].accepted_at for a, _, _ in FILINGS[:2]} == {
        a: at for a, _, at in FILINGS[:2]
    }


def test_the_next_anchor_decides_when_the_newest_header_does_not_read(
    settings: Settings,
) -> None:
    newest, _, _ = FILINGS[0]
    router = _router(SHIFTED, {newest: 404})
    source, stamps = _stamp(settings, router)
    assert newest not in stamps  # unread, so left out of a moved payload
    assert {a: stamps[a].accepted_at for a, _, _ in FILINGS[1:]} == {
        a: at for a, _, at in FILINGS[1:]
    }
    assert source.stamps_sgml_unread == 1


def test_a_payload_with_no_readable_anchor_is_kept_and_counted(settings: Settings) -> None:
    """Pre-2001 headers carry no acceptance: nothing can be checked, so the
    API's stamps are kept (as before #1494) and the CIK is counted."""
    no_stamp = {a: _header(a, form, None) for a, form, _ in FILINGS}
    router = _router({}, dict(no_stamp))
    source, stamps = _stamp(settings, router)
    assert {a: stamps[a].accepted_at for a, _, _ in FILINGS} == {a: at for a, _, at in FILINGS}
    assert source.stamp_check_unread_ciks == 1
    assert len(_header_requests(router)) == 3


def test_the_check_off_asks_no_header(tmp_path: Path) -> None:
    settings = edgar_settings(tmp_path / "cache")
    router = _router(SHIFTED)
    _, stamps = _stamp(settings, router)
    assert _header_requests(router) == []
    assert stamps[FILINGS[0][0]].accepted_at == FILINGS[0][2] + EDT


def test_the_check_is_on_by_default() -> None:
    assert Settings(_env_file=None).edgar.stamp_sgml_check is True


# --- the pure header read --------------------------------------------------------


def test_sgml_acceptance_reads_eastern_time_for_any_form() -> None:
    accession, form, at = FILINGS[0]
    assert sgml_acceptance(_header(accession, form, at), accession) == at
    # EST: 16:30 ET on 2026-02-20 is 21:30Z.
    accession, form, at = FILINGS[2]
    assert sgml_acceptance(_header(accession, form, at), accession) == at


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_header(FILINGS[1][0], "4", FILINGS[1][2]), "names"),
        (_header(FILINGS[0][0], "8-K", FILINGS[0][2]).split("</SEC-HEADER>")[0], "truncated"),
        (_header(FILINGS[0][0], "8-K", None), "no ACCEPTANCE-DATETIME"),
    ],
)
def test_sgml_acceptance_refuses_a_wrong_truncated_or_unstamped_header(
    text: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        sgml_acceptance(text, FILINGS[0][0])


def test_no_anchor_header_read_leaves_every_stamp_out_this_run(settings: Settings) -> None:
    """Every anchor 404s: the payload is unproven, so nothing is cached (the
    late stamps this check exists to stop) and the next run asks again."""
    router = _router(SHIFTED, {a: 404 for a, _, _ in FILINGS})
    source, stamps = _stamp(settings, router)
    assert stamps == {}
    assert source.stamps_sgml_unread == len(FILINGS)
    assert source.stamp_check_unread_ciks == 0


@pytest.mark.parametrize("status", [400, 500])
def test_a_header_error_other_than_gone_propagates(settings: Settings, status: int) -> None:
    """A 400 or a 500 (and SEC's 403 block, once waited out) stops the run, as on every
    EDGAR path, instead of being read as a missing header."""
    router = _router(SHIFTED, {FILINGS[0][0]: status})
    with pytest.raises(httpx.HTTPStatusError):
        _stamp(settings, router)


def test_a_header_cut_by_the_first_range_is_asked_again_longer(settings: Settings) -> None:
    accession, form, at = FILINGS[0]
    full = _header(accession, form, at)

    def ranged(request: httpx.Request) -> httpx.Response:
        last = int(request.headers["Range"].removeprefix("bytes=0-"))
        cut = full if last + 1 >= settings.edgar.stamp_header_bytes else full[:60]
        return httpx.Response(206, content=cut.encode())

    router = _router(SHIFTED)
    router.add(_header_url(accession), ranged)
    _, stamps = _stamp(settings, router)
    assert stamps[accession].accepted_at == at
    assert _header_requests(router).count(_header_url(accession)) == 2


def test_a_header_longer_than_the_stamp_cap_has_its_own_counter(settings: Settings) -> None:
    """A header beyond both configured ranges is not classified as a 404."""
    edgar = settings.edgar.model_copy(update={"header_bytes": 128, "stamp_header_bytes": 256})
    settings = settings.model_copy(update={"edgar": edgar})
    accession, form, at = FILINGS[0]
    full = _header(accession, form, at).replace("</SEC-HEADER>", "X" * 300 + "</SEC-HEADER>")

    def ranged(request: httpx.Request) -> httpx.Response:
        last = int(request.headers["Range"].removeprefix("bytes=0-"))
        return httpx.Response(206, content=full[: last + 1].encode())

    router = _router(SHIFTED)
    router.add(_header_url(accession), ranged)
    source, stamps = _stamp(settings, router)
    assert accession not in stamps
    assert source.stamps_sgml_overlong == 1
    assert source.stamps_sgml_unread == 0
    assert "stamps with overlong SGML headers: 1" in _source_counts(source)
    assert _header_requests(router).count(_header_url(accession)) == 2


def test_a_short_unclosed_header_is_unread_not_overlong(settings: Settings) -> None:
    accession, _, _ = FILINGS[0]
    router = _router(SHIFTED, {accession: "<SEC-HEADER>unfinished"})
    source, stamps = _stamp(settings, router)
    assert accession not in stamps
    assert source.stamps_sgml_unread == 1
    assert source.stamps_sgml_overlong == 0


# --- #1686: the API-stamp fallback after N unread runs ------------------------------


def _all_404() -> dict[str, int]:
    return {a: 404 for a, _, _ in FILINGS}


def _fallback_settings(tmp_path: Path, runs: int = 3) -> Settings:
    return edgar_settings(tmp_path / "cache", stamp_sgml_check=True, stamp_sgml_fallback_runs=runs)


def _run_once(settings: Settings, router: EdgarRouter) -> EdgarFilingSource:
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: CLOCK)
    source._stamp(_kept())
    return source


def test_the_fallback_run_count_is_config_and_defaults_above_one() -> None:
    assert Settings(_env_file=None).edgar.stamp_sgml_fallback_runs >= 2


def test_stamps_are_left_out_until_the_nth_consecutive_unread_run(tmp_path: Path) -> None:
    settings = _fallback_settings(tmp_path, runs=3)
    router = _router(SHIFTED, _all_404())
    for _ in range(2):
        source = _run_once(settings, router)  # a fresh source each run: the count persists
        assert source.stamps_api_fallback == 0
        assert not (Path(settings.edgar.cache_dir) / "stamps").exists() or not list(
            (Path(settings.edgar.cache_dir) / "stamps").glob(f"*/{CIK}.json")
        )
    source = _run_once(settings, router)
    assert source.stamps_api_fallback == len(FILINGS)
    assert source.stamp_fallback_ciks == 1
    assert {a: row[3] for a, row in _cached(settings).items()} == {
        a: (at + _offset(at)).isoformat() for a, _, at in FILINGS
    }
    assert "stamps from API fallback: 3" in _source_counts(source)
    assert "CIKs on API fallback: 1" in _source_counts(source)


def test_no_lookahead_a_fallback_stamp_is_never_earlier_than_the_true_acceptance(
    tmp_path: Path,
) -> None:
    """The API serves stamps late by New York's offset (4 h EDT, 5 h EST) or
    exact, never early, so the fallback can only delay a row."""
    settings = _fallback_settings(tmp_path, runs=1)
    _run_once(settings, _router(SHIFTED, _all_404()))
    cached = _cached(settings)
    for accession, _, true_at in FILINGS:
        stamped = datetime.fromisoformat(str(cached[accession][3]))
        assert stamped >= true_at
        assert stamped - true_at == _offset(true_at)


def test_a_readable_anchor_resets_the_unread_count(tmp_path: Path) -> None:
    settings = _fallback_settings(tmp_path, runs=2)
    _run_once(settings, _router(SHIFTED, _all_404()))  # 1 unread run
    _run_once(settings, _router(SHIFTED))  # readable: proven stamps cached, count reset
    for path in (Path(settings.edgar.cache_dir) / "stamps").glob(f"*/{CIK}.json"):
        path.unlink()  # the same accessions are unstamped again
    source = _run_once(settings, _router(SHIFTED, _all_404()))  # 1 unread run again
    assert source.stamps_api_fallback == 0
    assert not list((Path(settings.edgar.cache_dir) / "stamps").glob(f"*/{CIK}.json"))


def test_no_acceptance_headers_are_not_an_unread_run(tmp_path: Path) -> None:
    """Pre-2001 headers are kept unchecked as before; they never touch the count."""
    settings = _fallback_settings(tmp_path, runs=1)
    no_stamp = {a: _header(a, form, None) for a, form, _ in FILINGS}
    source = _run_once(settings, _router({}, dict(no_stamp)))
    assert source.stamp_check_unread_ciks == 1
    assert source.stamps_api_fallback == 0


def test_one_run_counts_a_cik_once_even_if_stamped_twice(tmp_path: Path) -> None:
    settings = _fallback_settings(tmp_path, runs=2)
    router = _router(SHIFTED, _all_404())
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: CLOCK)
    source._stamp(_kept())
    source._stamp(_kept())
    assert source.stamps_api_fallback == 0  # one run, so still below N=2


def test_no_lookahead_a_provably_early_api_stamp_is_moved_by_the_offset() -> None:
    """The API served early (the zip's #1382 direction) and no header can prove
    it. A filing accepted 18:00 ET (22:00Z, EDT) is dated the next day; stamped
    14:00 ET it fits only a clock moved early, so the fallback adds New York's
    offset and lands on the true acceptance, never before it."""
    true_at = datetime(2026, 10, 9, 22, tzinfo=UTC)
    early = SubmissionRecord(
        "0001628280-26-065999", "8-K", "d.htm", False, true_at - EDT, None, date(2026, 10, 10)
    )
    assert _fallback_stamp(early).accepted_at == true_at
    # EST: 18:00 ET Feb 20 is 23:00Z
    true_est = datetime(2026, 2, 20, 23, tzinfo=UTC)
    early_est = replace(early, accepted_at=true_est - EST, filing_date=date(2026, 2, 21))
    assert _fallback_stamp(early_est).accepted_at == true_est


def test_an_api_stamp_that_fits_the_filing_date_is_kept_as_served() -> None:
    late = SubmissionRecord(
        "0001628280-26-065998",
        "8-K",
        "d.htm",
        False,
        datetime(2026, 10, 9, 20, 3, tzinfo=UTC) + EDT,
        None,
        date(2026, 10, 9),
    )
    assert _fallback_stamp(late).accepted_at == late.accepted_at
    assert _fallback_stamp(replace(late, accepted_at=None)).accepted_at is None
    assert _fallback_stamp(replace(late, filing_date=None)).accepted_at == late.accepted_at

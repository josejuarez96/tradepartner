"""Offline tests for `adapters.edgar_raw`: no real network access.

Every HTTP call is served by an `httpx.MockTransport`-backed client
injected via the `client=` parameter every public function accepts, so
these run in CI (unlike `test_edgar_raw.py`'s real-API smoke tests, which
need `RUN_NETWORK_TESTS=1`). Covers the throttle, the retry/backoff
behavior, the declared `User-Agent`, credential errors, and the
path-safety checks on `download_filing_file` (T2 review round 2,
safety-reviewer MUST FIX).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.config import Settings

_Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """The module-level `_LIMITER` is a process-wide singleton; start each
    test with no memory of a previous call, so timing assertions are
    deterministic regardless of test order."""
    edgar_raw._LIMITER._last_request_monotonic = None


def _settings(
    *,
    user_agent: str | None = "TradePartner test-agent",
    cache_dir: Path | None = None,
    **edgar_kwargs: object,
) -> Settings:
    edgar_overrides: dict[str, object] = {"requests_per_second": 1000.0, **edgar_kwargs}
    if cache_dir is not None:
        edgar_overrides["cache_dir"] = str(cache_dir)
    return Settings(_env_file=None, sec_edgar_user_agent=user_agent, edgar=edgar_overrides)


def _mock_client(handler: _Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _status_sequence_handler(statuses: list[int]) -> tuple[_Handler, list[int]]:
    """A handler returning each of `statuses` in turn (repeating the last); the
    returned list records the status served on every call, for assertions."""
    served: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses[min(len(served), len(statuses) - 1)]
        served.append(status)
        return httpx.Response(status, json={"status": status})

    return handler, served


# --- User-Agent --------------------------------------------------------


def test_user_agent_sent_on_company_tickers() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json={"ok": True})

    edgar_raw.company_tickers(settings=_settings(), client=_mock_client(handler))

    assert seen_headers[0]["User-Agent"] == "TradePartner test-agent"


def test_user_agent_sent_on_download_filing_file(tmp_path: Path) -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, content=b"<xml>filing</xml>")

    edgar_raw.download_filing_file(
        "320193",
        "0000320193-24-000123",
        "primary_doc.xml",
        settings=_settings(cache_dir=tmp_path),
        client=_mock_client(handler),
    )

    assert seen_headers[0]["User-Agent"] == "TradePartner test-agent"


def test_user_agent_sent_on_the_range_request_for_sgml_header() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(206, text="SEC-HEADER\n")

    edgar_raw.filing_sgml_header(
        "320193", "0000320193-24-000123", settings=_settings(), client=_mock_client(handler)
    )

    assert seen_requests[0].headers["User-Agent"] == "TradePartner test-agent"
    assert seen_requests[0].headers["Range"] == "bytes=0-4095"


# --- throttle ------------------------------------------------------------


def test_rate_limiter_spaces_two_calls_at_least_the_configured_interval_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(edgar_raw.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(edgar_raw.time, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    settings = _settings(requests_per_second=2.0)  # 0.5s floor between requests
    client = _mock_client(handler)

    edgar_raw.company_tickers(settings=settings, client=client)
    edgar_raw.company_tickers(settings=settings, client=client)

    # First call: no prior request, no sleep. Second call: the fake clock
    # didn't advance during the (instant) mock request, so the limiter must
    # sleep off the full floor interval.
    assert sleeps == [pytest.approx(0.5)]


# --- retry / backoff -------------------------------------------------------


@pytest.mark.parametrize("first_status", [403, 429, 503])
def test_retryable_status_then_success_retries_once(
    monkeypatch: pytest.MonkeyPatch, first_status: int
) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    handler, served = _status_sequence_handler([first_status, 200])

    payload = edgar_raw.company_tickers(settings=_settings(), client=_mock_client(handler))

    assert payload == {"status": 200}
    assert served == [first_status, 200]


def test_503_then_503_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    handler, served = _status_sequence_handler([503, 503])

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(settings=_settings(), client=_mock_client(handler))

    assert served == [503, 503]


def test_retry_after_header_honored_over_configured_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    edgar_raw.company_tickers(
        settings=_settings(retry_backoff_seconds=1.0), client=_mock_client(handler)
    )

    # `time.sleep` also picks up the rate limiter's own (sub-millisecond,
    # real-clock) waits between the two requests; only the retry backoff
    # can plausibly be anywhere near the 7s `Retry-After` value.
    backoff_sleeps = [s for s in slept if s > 1.0]
    assert backoff_sleeps == [pytest.approx(7.0)]


@pytest.mark.parametrize("retry_after", ["nan", "inf", "1e9"])
def test_retry_after_rejects_non_finite_or_too_large_values(
    monkeypatch: pytest.MonkeyPatch, retry_after: str
) -> None:
    """A `nan`/`inf` `Retry-After` (non-finite) or one exceeding
    `edgar.max_retry_after_seconds` (`1e9` seconds is over 31 years) must
    raise instead of sleeping for it."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": retry_after})

    with pytest.raises(edgar_raw.RetryAfterTooLargeError):
        edgar_raw.company_tickers(settings=_settings(), client=_mock_client(handler))


def test_retry_after_within_the_cap_is_still_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "100"})
        return httpx.Response(200, json={"ok": True})

    edgar_raw.company_tickers(
        settings=_settings(max_retry_after_seconds=120.0), client=_mock_client(handler)
    )

    backoff_sleeps = [s for s in slept if s > 1.0]
    assert backoff_sleeps == [pytest.approx(100.0)]


def test_retry_after_unparseable_falls_back_to_configured_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # RFC 9110's HTTP-date form: valid per spec, just not a number.
            return httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return httpx.Response(200, json={"ok": True})

    edgar_raw.company_tickers(
        settings=_settings(retry_backoff_seconds=2.5), client=_mock_client(handler)
    )

    backoff_sleeps = [s for s in slept if s > 1.0]
    assert backoff_sleeps == [pytest.approx(2.5)]


# --- credentials -------------------------------------------------------


def test_edgar_credentials_error_when_user_agent_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without a User-Agent")

    with pytest.raises(edgar_raw.EdgarCredentialsError):
        edgar_raw.company_tickers(settings=_settings(user_agent=None), client=_mock_client(handler))


def test_edgar_credentials_error_when_user_agent_blank() -> None:
    with pytest.raises(edgar_raw.EdgarCredentialsError):
        edgar_raw.company_tickers(settings=_settings(user_agent="   "))


# --- path safety ---------------------------------------------------------


def test_download_filing_file_rejects_path_traversal(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an unsafe filename")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            "0000320193-24-000123",
            "../../../../etc/passwd",
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(handler),
        )


def test_download_filing_file_rejects_absolute_filename(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an unsafe filename")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            "0000320193-24-000123",
            "/etc/passwd",
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(handler),
        )


def test_download_filing_file_rejects_invalid_accession(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an invalid accession")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            "not-a-real-accession",
            "primary_doc.xml",
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(handler),
        )


def test_filing_sgml_header_rejects_invalid_accession() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an invalid accession")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.filing_sgml_header(
            "320193", "not-a-real-accession", settings=_settings(), client=_mock_client(handler)
        )


def test_download_filing_file_creates_nested_subdirectory(tmp_path: Path) -> None:
    """EDGAR's `primaryDocument` can be nested (e.g. an XSL-rendered view path)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<xml>rendered</xml>")

    dest = edgar_raw.download_filing_file(
        "320193",
        "0000320193-24-000123",
        "xslF25X02/primary_doc.xml",
        settings=_settings(cache_dir=tmp_path),
        client=_mock_client(handler),
    )

    expected = (
        tmp_path / "320193" / "000032019324000123" / "xslF25X02" / "primary_doc.xml"
    ).resolve()
    assert dest == expected
    assert dest.read_bytes() == b"<xml>rendered</xml>"


@pytest.mark.parametrize(
    "accession",
    [
        "not-a-real-accession",
        "0000320193-24-0001234",  # one digit too many in the last group
        "0000320193-24-000123\n0000320193-24-000123",  # embedded newline
        "0000320193-24-000123 ",  # trailing space
    ],
)
def test_download_filing_file_rejects_accession_via_fullmatch(
    tmp_path: Path, accession: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an invalid accession")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            accession,
            "primary_doc.xml",
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(handler),
        )


@pytest.mark.parametrize(
    "filename",
    [
        "",  # empty
        "/",  # no real segment
        "a/.",  # final segment is "."
        ".",  # final (only) segment is "."
        "a/../b",  # ".." segment
        "primary?doc.xml",  # forbidden char: ?
        "primary#doc.xml",  # forbidden char: #
        "a\\b",  # forbidden char: backslash
    ],
)
def test_download_filing_file_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an unsafe filename")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            "0000320193-24-000123",
            filename,
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(handler),
        )


# --- the file cache (T11b) --------------------------------------------------


def test_a_traversal_filename_raises_even_when_a_file_sits_at_its_target(tmp_path: Path) -> None:
    """Validation runs before the present-file early return."""
    cache = tmp_path / "cache"
    (tmp_path / "secret.txt").write_text("not for you")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an unsafe filename")

    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.download_filing_file(
            "320193",
            "0000320193-24-000123",
            "../../../secret.txt",
            settings=_settings(cache_dir=cache),
            client=_mock_client(handler),
        )


def test_a_present_file_is_returned_without_a_request(tmp_path: Path) -> None:
    settings = _settings(cache_dir=tmp_path)
    path = edgar_raw.cached_filing_path(
        "320193", "0000320193-24-000123", "a.htm", settings=settings
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"cached")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a cached filing file must not be fetched again")

    got = edgar_raw.download_filing_file(
        "320193", "0000320193-24-000123", "a.htm", settings=settings, client=_mock_client(handler)
    )
    assert got == path and got.read_bytes() == b"cached"


def test_cached_filing_path_is_pure_and_validates(tmp_path: Path) -> None:
    settings = _settings(cache_dir=tmp_path)
    path = edgar_raw.cached_filing_path(
        "320193", "0000320193-24-000123", "a.htm", settings=settings
    )
    assert path == (tmp_path / "320193" / "000032019324000123" / "a.htm").resolve()
    assert not path.parent.exists()
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.cached_filing_path("320193", "bad", "a.htm", settings=settings)


def test_a_download_replaces_a_temp_file_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replaced: list[tuple[Path, Path]] = []
    real_replace = edgar_raw.os.replace

    def spy(src: str, dst: Path) -> None:
        assert Path(src).exists() and not Path(dst).exists()
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(edgar_raw.os, "replace", spy)
    dest = edgar_raw.download_filing_file(
        "320193",
        "0000320193-24-000123",
        "a.htm",
        settings=_settings(cache_dir=tmp_path),
        client=_mock_client(lambda request: httpx.Response(200, content=b"body")),
    )
    [(src, dst)] = replaced
    assert dst == dest and src.parent == dest.parent and src != dest
    assert dest.read_bytes() == b"body"
    assert list(dest.parent.iterdir()) == [dest]


@pytest.mark.parametrize(
    ("fetch", "url_tail", "name"),
    [
        (edgar_raw.bulk_submissions, "bulkdata/submissions.zip", "submissions.zip"),
        (edgar_raw.bulk_company_facts, "xbrl/companyfacts.zip", "companyfacts.zip"),
    ],
)
def test_bulk_zips_stream_into_the_cache_dir_after_one_retry(
    tmp_path: Path, fetch: Callable[..., Path], url_tail: str, name: str
) -> None:
    served: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(str(request.url))
        assert request.headers["User-Agent"] == "TradePartner test-agent"
        if len(served) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"PK zip bytes")

    path = fetch(
        settings=_settings(cache_dir=tmp_path, retry_backoff_seconds=0.001),
        client=_mock_client(handler),
    )
    assert path == tmp_path / "bulk" / name and path.read_bytes() == b"PK zip bytes"
    assert len(served) == 2 and served[0].endswith(url_tail)
    assert [p.name for p in path.parent.iterdir()] == [name]


def test_a_failed_bulk_download_leaves_no_file(tmp_path: Path) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(lambda request: httpx.Response(404)),
        )
    assert list((tmp_path / "bulk").iterdir()) == []


class _BrokenStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"PK first chunk"
        raise httpx.ReadError("connection reset mid-stream")


def test_a_bulk_download_failing_mid_stream_keeps_the_previous_zip(tmp_path: Path) -> None:
    previous = tmp_path / "bulk" / "submissions.zip"
    previous.parent.mkdir()
    previous.write_bytes(b"yesterday's zip")
    with pytest.raises(httpx.ReadError):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(lambda request: httpx.Response(200, stream=_BrokenStream())),
        )
    assert previous.read_bytes() == b"yesterday's zip"
    assert list(previous.parent.iterdir()) == [previous]


# --- FSN data sets (T11c) -----------------------------------------------

_FSN_PAGE_HTML = """
<html><body>
<a href="/files/dera/data/financial-statement-notes-data-sets/2015q1_notes.zip">2015q1</a>
<a href="/files/dera/data/financial-statement-notes-data-sets/2026_02_notes.zip">2026_02</a>
<a href="/files/dera/data/financial-statement-notes-data-sets/2025_10_notes.zip">2025_10</a>
</body></html>
"""


def test_fsn_periods_oldest_first() -> None:
    periods = edgar_raw.fsn_periods(
        settings=_settings(),
        client=_mock_client(lambda request: httpx.Response(200, content=_FSN_PAGE_HTML)),
    )
    assert periods == ["2015q1", "2025_10", "2026_02"]


def test_fsn_periods_raises_when_page_has_no_matches() -> None:
    with pytest.raises(ValueError, match="no FSN periods"):
        edgar_raw.fsn_periods(
            settings=_settings(),
            client=_mock_client(lambda request: httpx.Response(200, content="<html></html>")),
        )


def test_fsn_zip_streams_into_the_fsn_cache_dir_and_returns_headers(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("2025_10_notes.zip")
        assert request.headers["User-Agent"] == "TradePartner test-agent"
        return httpx.Response(
            200, content=b"PK fsn zip bytes", headers={"ETag": '"abc"', "Content-Length": "17"}
        )

    path, headers = edgar_raw.fsn_zip(
        "2025_10", settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )
    assert path == tmp_path / "fsn" / "2025_10_notes.zip"
    assert path.read_bytes() == b"PK fsn zip bytes"
    assert headers["ETag"] == '"abc"'


def test_fsn_periods_break_ties_deterministically() -> None:
    """During an SEC roll-up the page can list a quarter and its months
    together: the quarter sorts at its first month, before its months, so
    "first extracted" never depends on set order."""
    page = "".join(
        f'<a href="/files/dera/data/financial-statement-notes-data-sets/{p}_notes.zip">x</a>'
        for p in ("2015_03", "2015_01", "2015q1", "2014q4", "2015_13", "2015_00")
    )
    periods = edgar_raw.fsn_periods(
        settings=_settings(),
        client=_mock_client(lambda request: httpx.Response(200, content=page)),
    )
    assert periods == ["2014q4", "2015q1", "2015_01", "2015_03"]  # months 00 and 13 are not periods


@pytest.mark.parametrize("period", ["../../x", "2025_10/../../x", "2025-10", "2025_13", ""])
def test_fsn_fetches_refuse_a_malformed_period(tmp_path: Path, period: str) -> None:
    client = _mock_client(lambda request: pytest.fail("no request for a bad period"))
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.fsn_zip(period, settings=_settings(cache_dir=tmp_path), client=client)
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.fsn_validators(period, settings=_settings(), client=client)
    assert not list(tmp_path.rglob("*"))


def test_fsn_zip_404_propagates(tmp_path: Path) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.fsn_zip(
            "2099_01",
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(lambda request: httpx.Response(404)),
        )


def test_fsn_validators_sends_a_plain_head() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        assert request.headers["User-Agent"] == "TradePartner test-agent"
        return httpx.Response(200, headers={"Last-Modified": "Wed, 01 Oct 2025 00:00:00 GMT"})

    headers = edgar_raw.fsn_validators(
        "2025_10", settings=_settings(), client=_mock_client(handler)
    )
    assert seen == ["HEAD"]
    assert headers["Last-Modified"] == "Wed, 01 Oct 2025 00:00:00 GMT"


def test_fsn_validators_retries_once_on_a_rate_limit_status() -> None:
    served: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(1)
        if len(served) == 1:
            return httpx.Response(503)
        return httpx.Response(200, headers={"ETag": '"x"'})

    headers = edgar_raw.fsn_validators(
        "2025_10",
        settings=_settings(retry_backoff_seconds=0.001),
        client=_mock_client(handler),
    )
    assert len(served) == 2
    assert headers["ETag"] == '"x"'

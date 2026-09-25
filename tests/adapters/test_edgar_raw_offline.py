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

from collections.abc import Callable
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

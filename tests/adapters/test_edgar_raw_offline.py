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

import io
import random
import zipfile
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.config import Settings

_Handler = Callable[[httpx.Request], httpx.Response]


def _valid_zip_bytes(member: str = "a.txt", content: bytes = b"hi") -> bytes:
    """A real, openable zip (unlike a bare `b"PK ..."` placeholder), so tests
    that aren't about zip-corruption detection don't trip it by accident."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    return buffer.getvalue()


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
    edgar_overrides: dict[str, object] = dict(edgar_kwargs)
    if cache_dir is not None:
        edgar_overrides["cache_dir"] = str(cache_dir)
    settings = Settings(_env_file=None, sec_edgar_user_agent=user_agent, edgar=edgar_overrides)
    # 1000 req/s (no real sleeps) is past the config's `le=10` (#1108): set it past
    # validation unless the test asks for its own rate.
    if "requests_per_second" in edgar_kwargs:
        return settings
    fast = settings.edgar.model_copy(update={"requests_per_second": 1000.0})
    return settings.model_copy(update={"edgar": fast})


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


def test_503_then_503_raises_once_retry_max_attempts_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    handler, served = _status_sequence_handler([503, 503])

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(
            settings=_settings(retry_max_attempts=2), client=_mock_client(handler)
        )

    assert served == [503, 503]


@pytest.mark.parametrize("status", [429, 503])
def test_capped_exponential_backoff_across_several_attempts(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Each retry's sleep doubles (`retry_backoff_seconds * 2 ** attempt`)
    until `retry_backoff_cap_seconds` caps it, and the request is retried up
    to `retry_max_attempts` times in total before the status is raised."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([status, status, status, status, 200])

    payload = edgar_raw.company_tickers(
        settings=_settings(
            retry_backoff_seconds=1.0,
            retry_backoff_cap_seconds=3.0,
            retry_max_attempts=5,
        ),
        client=_mock_client(handler),
    )

    assert payload == {"status": 200}
    assert served == [status, status, status, status, 200]
    # `time.sleep` also picks up the rate limiter's own (sub-millisecond,
    # real-clock) waits between requests; only the backoff sleeps matter here.
    backoff_sleeps = [s for s in slept if s > 0.5]
    assert backoff_sleeps == [
        pytest.approx(1.0),
        pytest.approx(2.0),
        pytest.approx(3.0),
        pytest.approx(3.0),
    ]


def test_503_fails_after_retry_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    handler, served = _status_sequence_handler([503] * 10)

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(
            settings=_settings(retry_max_attempts=3), client=_mock_client(handler)
        )

    assert served == [503, 503, 503]


def test_403_waits_the_configured_rate_limit_wait_then_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([403, 200])

    payload = edgar_raw.company_tickers(
        settings=_settings(rate_limit_wait_seconds=600.0), client=_mock_client(handler)
    )

    assert payload == {"status": 200}
    assert served == [403, 200]
    backoff_sleeps = [s for s in slept if s > 0.5]
    assert backoff_sleeps == [pytest.approx(600.0)]


def test_403_then_403_fails_without_a_second_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second `403` after the one wait-and-retry fails outright: SEC's
    block lifts only after the rate has stayed below the threshold for a
    while, so a second immediate retry cannot succeed and would only extend
    the block (research #572 P2)."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([403, 403, 200])

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(
            settings=_settings(rate_limit_wait_seconds=600.0, retry_max_attempts=10),
            client=_mock_client(handler),
        )

    assert served == [403, 403]
    backoff_sleeps = [s for s in slept if s > 0.5]
    assert backoff_sleeps == [pytest.approx(600.0)]


def test_a_transport_error_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped connection (or any other `httpx.TransportError`, e.g. a
    timeout) is retried through the same capped-exponential-backoff policy
    as `429`/`503` (research #572 P1)."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"status": 200})

    payload = edgar_raw.company_tickers(settings=_settings(), client=_mock_client(handler))

    assert payload == {"status": 200}
    assert calls["n"] == 2


def test_a_transport_error_raises_after_retry_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(httpx.ReadTimeout):
        edgar_raw.company_tickers(
            settings=_settings(retry_max_attempts=3), client=_mock_client(handler)
        )

    assert calls["n"] == 3


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
    zip_bytes = _valid_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(str(request.url))
        assert request.headers["User-Agent"] == "TradePartner test-agent"
        if len(served) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=zip_bytes)

    path = fetch(
        settings=_settings(cache_dir=tmp_path, retry_backoff_seconds=0.001),
        client=_mock_client(handler),
    )
    assert path == tmp_path / "bulk" / name and path.read_bytes() == zip_bytes
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
    """`retry_max_attempts=1` disables the (unrelated) transport-error
    retry, so this stays a test of atomicity: a failure mid-write must
    never touch the previously cached zip."""
    previous = tmp_path / "bulk" / "submissions.zip"
    previous.parent.mkdir()
    previous.write_bytes(b"yesterday's zip")
    with pytest.raises(httpx.ReadError):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path, retry_max_attempts=1),
            client=_mock_client(lambda request: httpx.Response(200, stream=_BrokenStream())),
        )
    assert previous.read_bytes() == b"yesterday's zip"
    assert list(previous.parent.iterdir()) == [previous]


def test_a_corrupt_zip_is_redownloaded_once_then_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated/corrupt zip (one that fails to open) is re-downloaded
    once automatically, without the caller seeing an error (research #572
    P10)."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    served: list[bytes] = []
    good_zip = _valid_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        body = b"not actually a zip" if len(served) == 0 else good_zip
        served.append(body)
        return httpx.Response(200, content=body)

    path = edgar_raw.bulk_submissions(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )

    assert len(served) == 2
    assert path.read_bytes() == good_zip


def test_a_zip_still_corrupt_after_one_redownload_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second corrupt download is not retried again, and is not handed
    back to the caller either: it raises `BadZipFile` naming the file
    (research #572 P10, "re-download once, then fail"), rather than this
    client looping forever against a source that keeps failing or a later
    caller consuming a damaged file (#554 quant-auditor NIT)."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    served: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = b"not a zip, attempt 1" if len(served) == 0 else b"not a zip, attempt 2"
        served.append(body)
        return httpx.Response(200, content=body)

    with pytest.raises(zipfile.BadZipFile, match="still corrupt after one re-download"):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
        )

    assert len(served) == 2


_MEMBER = "a.txt"
# `ZipFile.writestr` writes a 30-byte local header plus the member's name
# before its data (no extra field), so the member's data starts here.
_MEMBER_DATA_OFFSET = 30 + len(_MEMBER)


def _deflated_zip_with_damaged_compressed_data() -> bytes:
    """A real `ZIP_DEFLATED` zip (as SEC's bulk and FSN zips are) with an
    intact central directory but 40 flipped bytes in the middle of its
    member's compressed data: opening succeeds, and reading the member
    raises `zlib.error` (not `BadZipFile`), which `testzip()` does not
    catch (#554 quant-auditor pass 1, SHOULD FIX)."""
    rng = random.Random(0)
    content = " ".join(f"w{rng.randint(0, 500)}" for _ in range(4000)).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_MEMBER, content)
    damaged = bytearray(buffer.getvalue())
    compress_size = zipfile.ZipFile(io.BytesIO(bytes(damaged))).getinfo(_MEMBER).compress_size
    middle = _MEMBER_DATA_OFFSET + compress_size // 2
    for i in range(middle, middle + 40):
        damaged[i] ^= 0xFF
    return bytes(damaged)


def _stored_zip_failing_its_crc() -> bytes:
    """A `ZIP_STORED` zip whose member's bytes were changed after writing
    (same length, central directory intact): it opens and reads, and only
    the CRC check (`testzip()` naming the member) catches it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(_MEMBER, b"hello world" * 10)
    damaged = bytearray(buffer.getvalue())
    damaged[_MEMBER_DATA_OFFSET + 3] ^= 0xFF
    return bytes(damaged)


def test_damaged_deflate_data_raises_zlib_error_not_bad_zip_file() -> None:
    """Guards the fixture: the deflate case must reach the `zlib.error`
    branch, not the `BadZipFile` one the other corrupt-zip tests cover."""
    archive = zipfile.ZipFile(io.BytesIO(_deflated_zip_with_damaged_compressed_data()))
    with pytest.raises(zlib.error):
        archive.testzip()
    stored = zipfile.ZipFile(io.BytesIO(_stored_zip_failing_its_crc()))
    assert stored.testzip() == _MEMBER


@pytest.mark.parametrize(
    "corrupt_body",
    [_deflated_zip_with_damaged_compressed_data(), _stored_zip_failing_its_crc()],
    ids=["deflate-stream-damaged", "stored-member-crc-mismatch"],
)
def test_a_zip_damaged_mid_body_is_redownloaded_once_then_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt_body: bytes
) -> None:
    """A zip that opens but whose member data is damaged (a deflate error
    or a CRC mismatch) is re-downloaded once, like one that fails to open."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    served: list[bytes] = []
    good_zip = _valid_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        body = corrupt_body if len(served) == 0 else good_zip
        served.append(body)
        return httpx.Response(200, content=body)

    path = edgar_raw.bulk_submissions(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )

    assert len(served) == 2
    assert path.read_bytes() == good_zip


# --- the stream path's retry policy (#554 quant-auditor pass 1) ----------


def test_a_bulk_download_403_then_403_waits_once_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([403, 403, 200])

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.bulk_submissions(
            settings=_settings(
                cache_dir=tmp_path, rate_limit_wait_seconds=600.0, retry_max_attempts=10
            ),
            client=_mock_client(handler),
        )

    assert served == [403, 403]
    assert [s for s in slept if s > 0.5] == [pytest.approx(600.0)]
    assert list((tmp_path / "bulk").iterdir()) == []


def test_a_bulk_download_503_raises_after_retry_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    handler, served = _status_sequence_handler([503])

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path, retry_max_attempts=3),
            client=_mock_client(handler),
        )

    assert served == [503, 503, 503]


def test_a_bulk_download_dropped_mid_stream_is_retried_and_replaces_the_previous_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `ReadError` mid-body is retried; the previous zip is replaced only
    by the complete download, and no temp file is left behind."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    previous = tmp_path / "bulk" / "submissions.zip"
    previous.parent.mkdir()
    previous.write_bytes(b"yesterday's zip")
    good_zip = _valid_zip_bytes()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, stream=_BrokenStream())
        return httpx.Response(200, content=good_zip)

    path = edgar_raw.bulk_submissions(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )

    assert calls["n"] == 2
    assert path == previous and path.read_bytes() == good_zip
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
    zip_bytes = _valid_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("2025_10_notes.zip")
        assert request.headers["User-Agent"] == "TradePartner test-agent"
        return httpx.Response(
            200, content=zip_bytes, headers={"ETag": '"abc"', "Content-Length": str(len(zip_bytes))}
        )

    path, headers = edgar_raw.fsn_zip(
        "2025_10", settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )
    assert path == tmp_path / "fsn" / "2025_10_notes.zip"
    assert path.read_bytes() == zip_bytes
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


# --- #761: zip checked before it replaces the cache; per-run block; 429 block ---


def test_a_zip_still_corrupt_after_one_redownload_keeps_the_previous_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each streamed zip is checked in its temp file, before `os.replace`:
    two corrupt downloads raise `BadZipFile` and leave the previous good
    zip in place, with no temp file behind (#761 item 1)."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    previous = tmp_path / "bulk" / "submissions.zip"
    previous.parent.mkdir()
    yesterdays_zip = _valid_zip_bytes(content=b"yesterday")
    previous.write_bytes(yesterdays_zip)

    with pytest.raises(zipfile.BadZipFile, match="still corrupt after one re-download"):
        edgar_raw.bulk_submissions(
            settings=_settings(cache_dir=tmp_path),
            client=_mock_client(lambda request: httpx.Response(200, content=b"not a zip")),
        )

    assert previous.read_bytes() == yesterdays_zip
    assert list(previous.parent.iterdir()) == [previous]


def test_a_corrupt_zip_never_replaces_the_previous_zip_even_briefly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the good second download is moved over `dest`: the corrupt first
    one is never `os.replace`d onto it (#761 item 1)."""
    monkeypatch.setattr(edgar_raw.time, "sleep", lambda _seconds: None)
    replaced_with: list[bytes] = []
    real_replace = edgar_raw.os.replace

    def recording_replace(src: str | Path, dst: str | Path) -> None:
        replaced_with.append(Path(src).read_bytes())
        real_replace(src, dst)

    monkeypatch.setattr(edgar_raw.os, "replace", recording_replace)
    good_zip = _valid_zip_bytes()
    served: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(1)
        return httpx.Response(200, content=b"not a zip" if len(served) == 1 else good_zip)

    path = edgar_raw.bulk_submissions(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )

    assert replaced_with == [good_zip]
    assert path.read_bytes() == good_zip
    assert list(path.parent.iterdir()) == [path]


def test_a_persistent_403_is_waited_out_once_per_run_not_once_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `403` that outlasts its one wait (e.g. a refused User-Agent) makes
    every later `403` in the process fail at once, without another
    `edgar.rate_limit_wait_seconds` wait: `fsn_validators` over many cached
    periods would otherwise wait 10 minutes per period (#761 item 2)."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([403])
    settings = _settings(rate_limit_wait_seconds=600.0)
    client = _mock_client(handler)

    for period in ("2025_08", "2025_09", "2025_10"):
        with pytest.raises(httpx.HTTPStatusError):
            edgar_raw.fsn_validators(period, settings=settings, client=client)

    assert served == [403, 403, 403, 403]
    assert [s for s in slept if s > 0.5] == [pytest.approx(600.0)]


def test_a_success_ends_the_block_so_a_later_403_is_waited_out_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-fast lasts only while the block does: once any request
    succeeds, a later `403` gets its own wait again (#761 item 2)."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([403, 403, 200, 403, 200])
    settings = _settings(rate_limit_wait_seconds=600.0)
    client = _mock_client(handler)

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(settings=settings, client=client)
    assert edgar_raw.company_tickers(settings=settings, client=client) == {"status": 200}
    assert edgar_raw.company_tickers(settings=settings, client=client) == {"status": 200}

    assert served == [403, 403, 200, 403, 200]
    assert [s for s in slept if s > 0.5] == [pytest.approx(600.0), pytest.approx(600.0)]


def test_a_429_past_its_backoff_is_waited_out_as_a_block_then_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC may signal its block with a `429` (research #572 P2): once the
    backoff attempts are used up, a `429` gets the same one
    `edgar.rate_limit_wait_seconds` wait and one retry as a `403` (#761
    item 3)."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([429, 429, 429, 200])

    payload = edgar_raw.company_tickers(
        settings=_settings(
            retry_max_attempts=3, retry_backoff_seconds=1.0, rate_limit_wait_seconds=600.0
        ),
        client=_mock_client(handler),
    )

    assert payload == {"status": 200}
    assert served == [429, 429, 429, 200]
    assert [s for s in slept if s > 0.5] == [
        pytest.approx(1.0),
        pytest.approx(2.0),
        pytest.approx(600.0),
    ]


def test_a_429_that_outlasts_the_block_wait_fails_without_a_second_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    handler, served = _status_sequence_handler([429] * 10)

    with pytest.raises(httpx.HTTPStatusError):
        edgar_raw.company_tickers(
            settings=_settings(
                retry_max_attempts=3, retry_backoff_seconds=1.0, rate_limit_wait_seconds=600.0
            ),
            client=_mock_client(handler),
        )

    assert served == [429, 429, 429, 429]
    assert [s for s in slept if s > 0.5] == [
        pytest.approx(1.0),
        pytest.approx(2.0),
        pytest.approx(600.0),
    ]


# --- reuse_cached (#660, T77a) ------------------------------------------------


def _refuse(request: httpx.Request) -> httpx.Response:
    pytest.fail(f"no request expected, got {request.url}")


def test_reuse_cached_returns_the_cached_companyfacts_zip_with_no_request(
    tmp_path: Path,
) -> None:
    cached = tmp_path / "bulk" / "companyfacts.zip"
    cached.parent.mkdir()
    cached.write_bytes(_valid_zip_bytes("CIK0000000001.json", b"{}"))
    path = edgar_raw.bulk_company_facts(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(_refuse), reuse_cached=True
    )
    assert path == cached and path.read_bytes() == _valid_zip_bytes("CIK0000000001.json", b"{}")


@pytest.mark.parametrize("body", [b"not a zip", None])
def test_reuse_cached_raises_on_a_file_that_does_not_open_as_a_zip(
    tmp_path: Path, body: bytes | None
) -> None:
    """A damaged file, or none at all, raises: never a silent download."""
    if body is not None:
        (tmp_path / "bulk").mkdir()
        (tmp_path / "bulk" / "companyfacts.zip").write_bytes(body)
    with pytest.raises(zipfile.BadZipFile, match="reuse_cached"):
        edgar_raw.bulk_company_facts(
            settings=_settings(cache_dir=tmp_path), client=_mock_client(_refuse), reuse_cached=True
        )


def test_without_reuse_cached_the_bulk_file_is_requested_over_a_cached_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "bulk").mkdir()
    (tmp_path / "bulk" / "companyfacts.zip").write_bytes(_valid_zip_bytes("old.json"))
    served: list[str] = []
    fresh = _valid_zip_bytes("new.json")

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(str(request.url))
        return httpx.Response(200, content=fresh)

    path = edgar_raw.bulk_company_facts(
        settings=_settings(cache_dir=tmp_path), client=_mock_client(handler)
    )
    assert len(served) == 1 and path.read_bytes() == fresh

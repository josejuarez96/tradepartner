"""Thin `httpx` client over SEC EDGAR.

Every function here returns a raw payload — parsed JSON, plain text (SGML
header, full-index text), or a path to a downloaded file — and does no
interpretation of it (spec "Raw payload" definition). Parsing lives in
`adapters/edgar.py` (T11); `edgartools` is used there only for cover-page
iXBRL, never here.

SEC requires every request to declare a `User-Agent` with a name and
contact ("Verify before the Phase 2 plan", ADR 0003) and rate-limits to
roughly `edgar.requests_per_second` (default 10) requests/second.
`_RateLimiter` enforces that floor between requests made through this
module's shared client (a simple token/timestamp limiter, `threading.Lock`
-guarded: it remembers the last request time and sleeps off the remainder
of the interval).

Retry policy (#554, research #572 pitfalls P1/P2/P10), all through
`_execute_with_retry`, which every request (`_get`, `fsn_validators`'s
`HEAD`, `_stream_to_with_headers`'s `GET`) goes through, each retry still
behind `_RateLimiter`:
- **`429`/`503`, and a transport error** (a dropped connection, a timeout,
  or any other `httpx.TransportError`): capped exponential backoff,
  `edgar.retry_backoff_seconds * 2 ** attempt` up to
  `edgar.retry_backoff_cap_seconds`, up to `edgar.retry_max_attempts` tries
  in total. A `429`/`503` response's own `Retry-After` is honoured instead
  of the computed backoff when the response sends one and it's a finite,
  non-negative number no larger than `edgar.max_retry_after_seconds` --
  a response naming `nan`, `inf`, or an absurdly large value raises
  instead of sleeping for it.
- **`403`** (SEC uses it for its rate-limit block too, alongside the more
  standard `429`/`503`; the block lifts only once the request rate has
  stayed under the threshold for a while): one wait of
  `edgar.rate_limit_wait_seconds` (default 10 minutes), then one retry;
  a second `403` fails outright rather than waiting again.
- **A corrupt or truncated bulk zip** (`BadZipFile`, or `testzip()` naming
  a bad member): `_stream_to_with_headers` re-downloads it once more; a
  zip still corrupt after that is returned as-is and fails later, when
  its caller opens it.

Every public function takes an optional `client: httpx.Client` so tests
can inject an `httpx.MockTransport`-backed client without any real
network access (T2 review round 2, safety-reviewer MUST FIX); real
callers omit it and get the module's shared, lazily-built client.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from tradepartner.config import Settings, get_settings

#: `429`/`503`: SEC's standard "back off" statuses, retried with capped
#: exponential backoff (see module docstring).
_TRANSIENT_STATUS_CODES = frozenset({429, 503})
#: SEC's rate-limit block, which it also signals with a plain `403`: waited
#: out once (`edgar.rate_limit_wait_seconds`), never retried a second time.
_RATE_LIMIT_STATUS_CODE = 403
# `.fullmatch()`, so no anchors needed: a partial/embedded match (or a
# trailing-newline edge case `^...$` can admit) can never slip through.
_ACCESSION_PATTERN = re.compile(r"\d{10}-\d{2}-\d{6}")
# Characters SEC's own URLs and common filesystems treat specially and
# that have no business in a `primaryDocument`/downloaded filename.
_FORBIDDEN_FILENAME_CHARS = frozenset("?#\\")


class EdgarCredentialsError(RuntimeError):
    """Raised when `SEC_EDGAR_USER_AGENT` is not configured."""


class InvalidFilingReferenceError(ValueError):
    """Raised when an accession number or filename fails path-safety validation."""


class RetryAfterTooLargeError(RuntimeError):
    """Raised when a `Retry-After` response header exceeds `edgar.max_retry_after_seconds`."""


def _non_blank_secret(secret: SecretStr | None) -> str | None:
    """`secret`'s value, or `None` if it's unset or blank/whitespace-only.

    A `.env` line like `SEC_EDGAR_USER_AGENT=` sets the value to `""`,
    which is "configured" as far as `SecretStr | None` is concerned but not
    usable; treat it the same as missing (T2 review round 2,
    safety-reviewer).
    """
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value if value.strip() else None


class _RateLimiter:
    """Sleeps as needed so calls through it never run faster than a given interval.

    `threading.Lock`-guarded: the compute-sleep-update sequence is a single
    critical section, so two threads calling `wait()` concurrently can't
    both observe a stale `_last_request_monotonic` and skip the sleep
    (T2 review round 2, safety-reviewer SHOULD FIX). This is
    process-local only — it does not coordinate a rate ceiling across
    multiple processes.
    """

    def __init__(self) -> None:
        self._last_request_monotonic: float | None = None
        self._lock = threading.Lock()

    def wait(self, min_interval_seconds: float) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_request_monotonic is not None:
                elapsed = now - self._last_request_monotonic
                remaining = min_interval_seconds - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_monotonic = time.monotonic()


# Module-level and process-wide on purpose: the requests/second ceiling is
# SEC's limit on the whole client, not per function.
_LIMITER = _RateLimiter()

_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None


def _default_client(timeout_seconds: float) -> httpx.Client:
    """The module's one shared `httpx.Client`, built lazily on first use.

    Built once per process and reused (connection pooling) rather than a
    fresh `httpx.Client`/`httpx.get` per call (T2 review round 2,
    safety-reviewer SHOULD FIX). `timeout_seconds` only takes effect on the
    very first call in a process; later calls with a different
    `edgar.request_timeout_seconds` keep using the client built for the
    first one. Tests that need a different transport (e.g.
    `httpx.MockTransport`) pass their own `client=` instead of relying on
    this singleton.
    """
    global _shared_client
    with _client_lock:
        if _shared_client is None:
            _shared_client = httpx.Client(timeout=timeout_seconds)
        return _shared_client


def _user_agent(settings: Settings) -> str:
    value = _non_blank_secret(settings.sec_edgar_user_agent)
    if value is None:
        raise EdgarCredentialsError(
            "SEC_EDGAR_USER_AGENT must be set (see .env.example): SEC requires a "
            "declared name and contact email on every request."
        )
    return value


def _retry_after_seconds(response: httpx.Response, settings: Settings) -> float | None:
    """`Retry-After`, parsed and validated, or `None` if the response sent
    none or it doesn't parse as a number (e.g. RFC 9110's HTTP-date form):
    the caller then falls back to its own computed backoff.

    Raises `RetryAfterTooLargeError` -- rather than returning it for the
    caller to sleep on -- if the header names a non-finite value
    (`nan`/`inf`; `math.isfinite`) or a finite one larger than
    `edgar.max_retry_after_seconds`: an SEC response is not a trustworthy
    source for "sleep for however long it says", and a value like `1e9`
    would otherwise hang this process for over 31 years.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None

    try:
        seconds = float(retry_after)
    except ValueError:
        return None

    if not math.isfinite(seconds):
        raise RetryAfterTooLargeError(
            f"Retry-After={retry_after!r} is not a finite number of seconds"
        )
    if seconds > settings.edgar.max_retry_after_seconds:
        raise RetryAfterTooLargeError(
            f"Retry-After={seconds}s exceeds edgar.max_retry_after_seconds="
            f"{settings.edgar.max_retry_after_seconds}s"
        )
    return max(seconds, 0.0)


def _backoff_seconds(
    attempt_index: int, settings: Settings, response: httpx.Response | None
) -> float:
    """How long to sleep before the next attempt: `response`'s own
    `Retry-After` when it sends a usable one, else capped exponential
    backoff, `edgar.retry_backoff_seconds * 2 ** attempt_index` up to
    `edgar.retry_backoff_cap_seconds`. `response` is `None` for a transport
    error (a dropped connection, a timeout, ...), which never carries a
    `Retry-After`.
    """
    if response is not None:
        retry_after = _retry_after_seconds(response, settings)
        if retry_after is not None:
            return retry_after
    return min(
        settings.edgar.retry_backoff_seconds * (2.0**attempt_index),
        settings.edgar.retry_backoff_cap_seconds,
    )


def _execute_with_retry(
    make_request: Callable[[], httpx.Response],
    *,
    settings: Settings,
    min_interval_seconds: float,
) -> httpx.Response:
    """Call `make_request()` (behind `_LIMITER`, which also spaces out every
    retry) until it succeeds or the retry policy gives up; see the module
    docstring for the policy. Raises `httpx.TransportError` (a transport
    error on the last allowed attempt) or `httpx.HTTPStatusError`
    (`response.raise_for_status()`, including a second `403`, or any status
    this function doesn't retry) on final failure.
    """
    attempt = 0
    rate_limit_waited = False
    while True:
        _LIMITER.wait(min_interval_seconds)
        try:
            response = make_request()
        except httpx.TransportError:
            if attempt + 1 >= settings.edgar.retry_max_attempts:
                raise
            time.sleep(_backoff_seconds(attempt, settings, None))
            attempt += 1
            continue

        if response.status_code == _RATE_LIMIT_STATUS_CODE:
            if rate_limit_waited:
                response.raise_for_status()
            rate_limit_waited = True
            time.sleep(settings.edgar.rate_limit_wait_seconds)
            continue

        if response.status_code in _TRANSIENT_STATUS_CODES:
            if attempt + 1 >= settings.edgar.retry_max_attempts:
                response.raise_for_status()
            time.sleep(_backoff_seconds(attempt, settings, response))
            attempt += 1
            continue

        response.raise_for_status()
        return response


def _get(
    url: str,
    *,
    settings: Settings,
    extra_headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """`GET url` behind the throttle, with the declared `User-Agent` and the
    module's retry policy (see module docstring)."""
    headers = {"User-Agent": _user_agent(settings), **(extra_headers or {})}
    http_client = (
        client if client is not None else _default_client(settings.edgar.request_timeout_seconds)
    )
    timeout = settings.edgar.request_timeout_seconds
    min_interval_seconds = 1.0 / settings.edgar.requests_per_second

    return _execute_with_retry(
        lambda: http_client.get(url, headers=headers, timeout=timeout),
        settings=settings,
        min_interval_seconds=min_interval_seconds,
    )


def _padded_cik(cik: str) -> str:
    return cik.strip().zfill(10)


def validate_accession(accession: str) -> None:
    """Public form of `_validate_accession`, for callers that build cache
    paths from an accession (T11d's cover and header caches)."""
    _validate_accession(accession)


def _validate_accession(accession: str) -> None:
    """`accession` must be a real EDGAR accession number, e.g. `0000320193-24-000123`.

    Guards against it flowing into a URL/filesystem path unvalidated (T2
    review round 2, safety-reviewer MUST FIX). `.fullmatch()` (round 3):
    the pattern itself carries no `^`/`$` anchors, since `fullmatch`
    already requires the whole string to match -- `$` alone can admit a
    trailing newline, which `fullmatch` cannot.
    """
    if not _ACCESSION_PATTERN.fullmatch(accession):
        raise InvalidFilingReferenceError(
            f"accession must look like 0000320193-24-000123, got {accession!r}"
        )


def _validate_relative_filename(filename: str) -> None:
    """`filename` must be a safe relative path.

    EDGAR's `primaryDocument` can be nested (e.g. `xslF25X02/primary_doc.xml`
    for an XBRL-only form like 25-NSE), so a bare "no slashes" rule is too
    strict; what must never happen is escaping the destination directory,
    resolving to nothing, or containing a character SEC's own URLs and
    common filesystems treat specially. Rejects (round 3, safety-reviewer
    MUST FIX): an empty filename; one with no real segment (e.g. `"/"` or
    `""`, split on `/`); one whose final segment is exactly `.` (worth
    rejecting explicitly -- `pathlib` would otherwise silently normalize
    `"a/."` to `"a"`, masking a malformed input rather than refusing it);
    a `..` segment; an absolute path; or any of `? # \\` anywhere in it.
    """
    if not filename:
        raise InvalidFilingReferenceError("filing filename must not be empty")
    if any(char in filename for char in _FORBIDDEN_FILENAME_CHARS):
        raise InvalidFilingReferenceError(f"unsafe filing filename: {filename!r}")
    if filename.startswith("/"):
        raise InvalidFilingReferenceError(f"unsafe filing filename: {filename!r}")

    segments = filename.split("/")
    if not any(segments):
        raise InvalidFilingReferenceError(f"unsafe filing filename: {filename!r}")
    if segments[-1] == "." or ".." in segments:
        raise InvalidFilingReferenceError(f"unsafe filing filename: {filename!r}")


def company_tickers(*, settings: Settings | None = None, client: httpx.Client | None = None) -> Any:
    """Raw company/ticker/exchange snapshot (current-only; `provenance=snapshot`)."""
    settings = settings or get_settings()
    response = _get(
        "https://www.sec.gov/files/company_tickers_exchange.json",
        settings=settings,
        client=client,
    )
    return response.json()


def submissions(
    cik: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Any:
    """Raw filing-submissions history for `cik` (accepts any digit string; zero-padded)."""
    settings = settings or get_settings()
    response = _get(
        f"https://data.sec.gov/submissions/CIK{_padded_cik(cik)}.json",
        settings=settings,
        client=client,
    )
    return response.json()


_SUBMISSIONS_PAGE_PATTERN = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")


def submissions_page(
    name: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Any:
    """One older submissions page, named in `submissions()["filings"]["files"][i]["name"]`.

    The main payload holds only the most recent ~1,000 filings; older ones, with their
    `acceptanceDateTime`, live in these pages (T3, #84). `name` is validated so it cannot
    carry a path into the URL.
    """
    if not _SUBMISSIONS_PAGE_PATTERN.fullmatch(name):
        raise InvalidFilingReferenceError(
            f"submissions page must look like CIK0000320193-submissions-001.json, got {name!r}"
        )
    settings = settings or get_settings()
    response = _get(f"https://data.sec.gov/submissions/{name}", settings=settings, client=client)
    return response.json()


def company_facts(
    cik: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Any:
    """Raw XBRL company-facts payload for `cik`."""
    settings = settings or get_settings()
    response = _get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_padded_cik(cik)}.json",
        settings=settings,
        client=client,
    )
    return response.json()


def filing_index_quarter(
    year: int,
    qtr: int,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Raw full-index `form.idx` text for one calendar quarter."""
    if qtr not in (1, 2, 3, 4):
        raise ValueError(f"qtr must be 1-4, got {qtr}")
    settings = settings or get_settings()
    response = _get(
        f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx",
        settings=settings,
        client=client,
    )
    return response.text


def filing_sgml_header(
    cik: str,
    accession: str,
    *,
    header_bytes: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> str:
    """The first `header_bytes` (default `edgar.header_bytes`) of a filing's full
    submission `.txt` (the SGML header).

    `accession` is the dashed form (`0000320193-24-000123`); the header
    lives at the top of `.../{accession-nodash}/{accession}.txt`.
    """
    _validate_accession(accession)
    settings = settings or get_settings()
    effective_header_bytes = (
        header_bytes if header_bytes is not None else settings.edgar.header_bytes
    )
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{accession}.txt"
    response = _get(
        url,
        settings=settings,
        extra_headers={"Range": f"bytes=0-{effective_header_bytes - 1}"},
        client=client,
    )
    return response.text


def write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a temp file in the same directory and
    `os.replace`, so a reader never sees a half-written cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cached_filing_path(
    cik: str, accession: str, filename: str, *, settings: Settings | None = None
) -> Path:
    """Where `download_filing_file` keeps one filing file under `edgar.cache_dir`.

    Pure: validates `accession` and `filename` and checks the resolved path
    stays inside the cache directory; touches neither disk nor network.
    """
    _validate_accession(accession)
    _validate_relative_filename(filename)
    settings = settings or get_settings()
    cache_dir = Path(settings.edgar.cache_dir).resolve()
    dest = (cache_dir / str(int(cik)) / accession.replace("-", "") / filename).resolve()
    if not dest.is_relative_to(cache_dir):
        raise InvalidFilingReferenceError(f"resolved path escapes edgar.cache_dir: {dest}")
    return dest


def download_filing_file(
    cik: str,
    accession: str,
    filename: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Download one file from a filing's index into `edgar.cache_dir`; return its path.

    Used by `edgartools` (T11) to parse a downloaded cover page's iXBRL;
    this function only fetches and caches the bytes, never parses them.
    `accession` and `filename` are validated first (`cached_filing_path`),
    before anything else, so a file already sitting at an unsafe target is
    never returned. A file already present is returned without a request (a
    filing never changes); a new one is written atomically.
    """
    settings = settings or get_settings()
    dest = cached_filing_path(cik, accession, filename, settings=settings)
    if dest.is_file():
        return dest
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{filename}"
    response = _get(url, settings=settings, client=client)
    write_atomic(dest, response.content)
    return dest


def _stream_to(url: str, dest: Path, *, settings: Settings, client: httpx.Client | None) -> Path:
    """Stream `url` to `dest` behind the throttle and `User-Agent`, the
    module's retry policy, and one re-download if it arrives corrupt, through
    a temp file and `os.replace`."""
    return _stream_to_with_headers(url, dest, settings=settings, client=client)[0]


def _write_stream_atomic(dest: Path, chunks: Iterable[bytes]) -> None:
    """Write `chunks` to `dest` through a temp file in the same directory and
    `os.replace`, so a reader (or a mid-stream failure) never sees a
    half-written file, and a previous `dest` is left untouched until the new
    one is fully written."""
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            for chunk in chunks:
                file.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _zip_is_corrupt(path: Path) -> bool:
    """Whether `path` fails to open as a zip, or its own CRC check names a
    bad member: a truncated or corrupted download (research #572 P10)."""
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is not None
    except zipfile.BadZipFile:
        return True


def _stream_to_with_headers(
    url: str, dest: Path, *, settings: Settings, client: httpx.Client | None
) -> tuple[Path, httpx.Headers]:
    """As `_stream_to`, but also returns the response headers (T11c: `Last-
    Modified`, `ETag` and `Content-Length` go into the FSN period manifest).

    Every destination streamed through this function is a zip
    (`bulk_submissions`, `bulk_company_facts`, `fsn_zip`): once a response
    streams cleanly to `dest`, `dest` is opened and CRC-checked, and a
    corrupt or truncated result is re-downloaded once more (a second corrupt
    result is returned as-is, and fails later when its caller opens it).
    """
    headers = {"User-Agent": _user_agent(settings)}
    http_client = (
        client if client is not None else _default_client(settings.edgar.request_timeout_seconds)
    )
    timeout = settings.edgar.request_timeout_seconds
    min_interval_seconds = 1.0 / settings.edgar.requests_per_second
    dest.parent.mkdir(parents=True, exist_ok=True)

    attempt = 0
    rate_limit_waited = False
    zip_redownloaded = False
    while True:
        _LIMITER.wait(min_interval_seconds)
        try:
            with http_client.stream("GET", url, headers=headers, timeout=timeout) as response:
                if response.status_code == _RATE_LIMIT_STATUS_CODE:
                    if rate_limit_waited:
                        response.raise_for_status()
                    rate_limit_waited = True
                    time.sleep(settings.edgar.rate_limit_wait_seconds)
                    continue
                if response.status_code in _TRANSIENT_STATUS_CODES:
                    if attempt + 1 >= settings.edgar.retry_max_attempts:
                        response.raise_for_status()
                    time.sleep(_backoff_seconds(attempt, settings, response))
                    attempt += 1
                    continue
                response.raise_for_status()
                response_headers = response.headers
                _write_stream_atomic(dest, response.iter_bytes())
        except httpx.TransportError:
            if attempt + 1 >= settings.edgar.retry_max_attempts:
                raise
            time.sleep(_backoff_seconds(attempt, settings, None))
            attempt += 1
            continue

        if not zip_redownloaded and _zip_is_corrupt(dest):
            zip_redownloaded = True
            continue
        return dest, response_headers


# --- SEC Financial Statement and Notes data sets (T11c) ---------------------

_FSN_PAGE_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets"
)
_FSN_ZIP_URL = (
    "https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/{period}_notes.zip"
)
# The data-set page's href to one period's zip. Never a hardcoded
# quarter-to-month boundary: SEC rolls months into quarters after the fact
# (#174 F1, F10), so both `\d{4}q[1-4]` and `\d{4}_\d{2}` period spellings
# are matched, and the caller (`fsn_periods`) sorts them, not this pattern.
_FSN_PERIOD = r"\d{4}q[1-4]|\d{4}_(?:0[1-9]|1[0-2])"
_FSN_PERIOD_RE = re.compile(_FSN_PERIOD)
_FSN_PERIOD_PATTERN = re.compile(
    rf"/files/dera/data/financial-statement-notes-data-sets/({_FSN_PERIOD})_notes\.zip"
)


def is_fsn_period(period: str) -> bool:
    """Whether `period` is spelled `YYYYqN` or `YYYY_MM` (month 01 to 12)."""
    return _FSN_PERIOD_RE.fullmatch(period) is not None


def _validate_fsn_period(period: str) -> None:
    """`period` must be `YYYYqN` or `YYYY_MM` before it reaches a URL or a
    path under `edgar.cache_dir` (as `_validate_accession` guards accessions)."""
    if not _FSN_PERIOD_RE.fullmatch(period):
        raise InvalidFilingReferenceError(f"not an FSN period: {period!r}")


def _fsn_period_sort_key(period: str) -> tuple[int, int, int]:
    """`(year, month, rank)` for a period spelled `YYYYqN` or `YYYY_MM`, so
    mixed quarterly/monthly periods sort oldest first and deterministically:
    a quarter sorts at its first month, ahead of the months it later split
    into (rank 0 before 1), never by the order the page happened to list."""
    if "q" in period:
        year, qtr = period.split("q")
        return int(year), int(qtr) * 3 - 2, 0
    year, month = period.split("_")
    return int(year), int(month), 1


def fsn_period_year(period: str) -> int:
    """The calendar year of an FSN period spelled `YYYYqN` or `YYYY_MM`."""
    return _fsn_period_sort_key(period)[0]


def fsn_page_html(*, settings: Settings | None = None, client: httpx.Client | None = None) -> str:
    """The raw FSN data-set page (the recorder writes this verbatim as
    `tests/fixtures/edgar/fsn/page.html`; `fsn_periods` parses it)."""
    settings = settings or get_settings()
    return _get(_FSN_PAGE_URL, settings=settings, client=client).text


def fsn_periods(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[str]:
    """Every FSN period (`"2015q1"`, `"2026_02"`, ...) on the data-set page,
    oldest first. Raises `ValueError` if the page names none."""
    text = fsn_page_html(settings=settings, client=client)
    periods = sorted(set(_FSN_PERIOD_PATTERN.findall(text)), key=_fsn_period_sort_key)
    if not periods:
        raise ValueError("fsn_periods: no FSN periods found on the data-set page")
    return periods


def fsn_zip(
    period: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> tuple[Path, httpx.Headers]:
    """Stream one FSN period's zip into `edgar.cache_dir/fsn/`; returns its
    path and response headers (`_stream_to_with_headers`). A 404 (an
    unlisted or malformed period) propagates as `httpx.HTTPStatusError`."""
    _validate_fsn_period(period)
    settings = settings or get_settings()
    dest = Path(settings.edgar.cache_dir) / "fsn" / f"{period}_notes.zip"
    return _stream_to_with_headers(
        _FSN_ZIP_URL.format(period=period), dest, settings=settings, client=client
    )


def fsn_validators(
    period: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> httpx.Headers:
    """A plain, unconditional `HEAD` of one FSN period's zip, behind the same
    throttle, retry and `User-Agent` as every other request here; returns
    the response headers (`Last-Modified`, `ETag`, `Content-Length`) so the
    caller can compare them with the period's manifest."""
    _validate_fsn_period(period)
    settings = settings or get_settings()
    url = _FSN_ZIP_URL.format(period=period)
    headers = {"User-Agent": _user_agent(settings)}
    http_client = (
        client if client is not None else _default_client(settings.edgar.request_timeout_seconds)
    )
    min_interval_seconds = 1.0 / settings.edgar.requests_per_second
    timeout = settings.edgar.request_timeout_seconds

    response = _execute_with_retry(
        lambda: http_client.request("HEAD", url, headers=headers, timeout=timeout),
        settings=settings,
        min_interval_seconds=min_interval_seconds,
    )
    return response.headers


def bulk_submissions(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Path:
    """The nightly `submissions.zip` (every CIK's submissions and older pages,
    rebuilt about 03:00 ET), streamed to `edgar.cache_dir/bulk/submissions.zip`."""
    settings = settings or get_settings()
    return _stream_to(
        "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
        Path(settings.edgar.cache_dir) / "bulk" / "submissions.zip",
        settings=settings,
        client=client,
    )


def bulk_company_facts(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Path:
    """The nightly `companyfacts.zip`, streamed to `edgar.cache_dir/bulk/companyfacts.zip`."""
    settings = settings or get_settings()
    return _stream_to(
        "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
        Path(settings.edgar.cache_dir) / "bulk" / "companyfacts.zip",
        settings=settings,
        client=client,
    )

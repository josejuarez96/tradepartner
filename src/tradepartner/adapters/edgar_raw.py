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
of the interval); `_get` retries once, after a backoff (`Retry-After` if
the response sends one and it's a finite, non-negative number no larger
than `edgar.max_retry_after_seconds` -- else `edgar.retry_backoff_seconds`
-- a response naming `nan`, `inf`, or an absurdly large value raises
instead of sleeping for it), on `403`/`429`/`503` (SEC uses `403` for
rate limiting too, alongside the more standard `429`/`503`).

Every public function takes an optional `client: httpx.Client` so tests
can inject an `httpx.MockTransport`-backed client without any real
network access (T2 review round 2, safety-reviewer MUST FIX); real
callers omit it and get the module's shared, lazily-built client.
"""

from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from tradepartner.config import Settings, get_settings

_RETRY_STATUS_CODES = frozenset({403, 429, 503})
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


def _retry_backoff_seconds(response: httpx.Response, settings: Settings) -> float:
    """`Retry-After` if the response sent one (seconds, integer per RFC 9110's
    common case), else `edgar.retry_backoff_seconds`.

    Raises `RetryAfterTooLargeError` -- rather than sleeping for it -- if
    the header names a non-finite value (`nan`/`inf`; `math.isfinite`) or
    a finite one larger than `edgar.max_retry_after_seconds`: an SEC
    response is not a trustworthy source for "sleep for however long it
    says", and a value like `1e9` would otherwise hang this process for
    over 31 years. A header that doesn't parse as a number at all (e.g.
    the RFC 9110 HTTP-date form) is treated as absent, falling back to
    `edgar.retry_backoff_seconds`.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return settings.edgar.retry_backoff_seconds

    try:
        seconds = float(retry_after)
    except ValueError:
        return settings.edgar.retry_backoff_seconds

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


def _get(
    url: str,
    *,
    settings: Settings,
    extra_headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """`GET url` behind the throttle, with the declared `User-Agent` and one retry."""
    headers = {"User-Agent": _user_agent(settings), **(extra_headers or {})}
    http_client = (
        client if client is not None else _default_client(settings.edgar.request_timeout_seconds)
    )
    min_interval_seconds = 1.0 / settings.edgar.requests_per_second

    _LIMITER.wait(min_interval_seconds)
    response = http_client.get(url, headers=headers, timeout=settings.edgar.request_timeout_seconds)
    if response.status_code in _RETRY_STATUS_CODES:
        time.sleep(_retry_backoff_seconds(response, settings))
        _LIMITER.wait(min_interval_seconds)
        response = http_client.get(
            url, headers=headers, timeout=settings.edgar.request_timeout_seconds
        )
    response.raise_for_status()
    return response


def _padded_cik(cik: str) -> str:
    return cik.strip().zfill(10)


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
    `accession` and `filename` are validated first (see
    `_validate_accession`/`_validate_relative_filename`), and the resolved
    destination is checked to still be inside `edgar.cache_dir` before
    anything is written.
    """
    _validate_accession(accession)
    _validate_relative_filename(filename)
    settings = settings or get_settings()
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{filename}"
    response = _get(url, settings=settings, client=client)

    cache_dir = Path(settings.edgar.cache_dir).resolve()
    dest = (cache_dir / str(int(cik)) / accession_nodash / filename).resolve()
    if not dest.is_relative_to(cache_dir):
        raise InvalidFilingReferenceError(f"resolved path escapes edgar.cache_dir: {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)
    return dest

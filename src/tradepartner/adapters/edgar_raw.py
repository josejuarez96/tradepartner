"""Thin `httpx` client over SEC EDGAR.

Every function here returns a raw payload — parsed JSON, plain text (SGML
header, full-index text), or a path to a downloaded file — and does no
interpretation of it (spec "Raw payload" definition). Parsing lives in
`adapters/edgar.py` (T11); `edgartools` is used there only for cover-page
iXBRL, never here.

SEC requires every request to declare a `User-Agent` with a name and
contact ("Verify before the Phase 2 plan", ADR 0003) and rate-limits to
roughly 10 requests/second. `_RateLimiter` enforces a floor of 1/10s
between requests made through this module (a simple token/timestamp
limiter: it remembers the last request time and sleeps off the remainder
of the interval); `_get` retries once, after a short backoff, on
`429`/`503`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from tradepartner.config import Settings, get_settings

_REQUESTS_PER_SECOND = 10.0
_MIN_INTERVAL_SECONDS = 1.0 / _REQUESTS_PER_SECOND
_RETRY_STATUS_CODES = frozenset({429, 503})
_RETRY_BACKOFF_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 30.0
_HEADER_BYTES = 4096  # first N KB of a filing's SGML header (spec req 6)


class EdgarCredentialsError(RuntimeError):
    """Raised when `SEC_EDGAR_USER_AGENT` is not configured."""


class _RateLimiter:
    """Sleeps as needed so calls through it never run faster than the floor interval."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._last_request_monotonic: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_request_monotonic is not None:
            elapsed = now - self._last_request_monotonic
            remaining = self._min_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_monotonic = time.monotonic()


# Module-level and process-wide on purpose: the ~10 req/s ceiling is SEC's
# limit on the whole client, not per function.
_LIMITER = _RateLimiter(_MIN_INTERVAL_SECONDS)


def _user_agent(settings: Settings) -> str:
    if settings.sec_edgar_user_agent is None:
        raise EdgarCredentialsError(
            "SEC_EDGAR_USER_AGENT must be set (see .env.example): SEC requires a "
            "declared name and contact email on every request."
        )
    return settings.sec_edgar_user_agent.get_secret_value()


def _get(
    url: str,
    *,
    settings: Settings,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """`GET url` behind the throttle, with the declared `User-Agent` and one retry."""
    headers = {"User-Agent": _user_agent(settings), **(extra_headers or {})}
    _LIMITER.wait()
    response = httpx.get(url, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    if response.status_code in _RETRY_STATUS_CODES:
        time.sleep(_RETRY_BACKOFF_SECONDS)
        _LIMITER.wait()
        response = httpx.get(url, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def _padded_cik(cik: str) -> str:
    return cik.strip().zfill(10)


def company_tickers(*, settings: Settings | None = None) -> Any:
    """Raw company/ticker/exchange snapshot (current-only; `provenance=snapshot`)."""
    settings = settings or get_settings()
    response = _get("https://www.sec.gov/files/company_tickers_exchange.json", settings=settings)
    return response.json()


def submissions(cik: str, *, settings: Settings | None = None) -> Any:
    """Raw filing-submissions history for `cik` (accepts any digit string; zero-padded)."""
    settings = settings or get_settings()
    response = _get(
        f"https://data.sec.gov/submissions/CIK{_padded_cik(cik)}.json", settings=settings
    )
    return response.json()


def company_facts(cik: str, *, settings: Settings | None = None) -> Any:
    """Raw XBRL company-facts payload for `cik`."""
    settings = settings or get_settings()
    response = _get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_padded_cik(cik)}.json",
        settings=settings,
    )
    return response.json()


def filing_index_quarter(year: int, qtr: int, *, settings: Settings | None = None) -> str:
    """Raw full-index `form.idx` text for one calendar quarter."""
    if qtr not in (1, 2, 3, 4):
        raise ValueError(f"qtr must be 1-4, got {qtr}")
    settings = settings or get_settings()
    response = _get(
        f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx",
        settings=settings,
    )
    return response.text


def filing_sgml_header(
    cik: str,
    accession: str,
    *,
    header_bytes: int = _HEADER_BYTES,
    settings: Settings | None = None,
) -> str:
    """The first `header_bytes` of a filing's full submission `.txt` (the SGML header).

    `accession` is the dashed form (`0000320193-24-000123`); the header
    lives at the top of `.../{accession-nodash}/{accession}.txt`.
    """
    settings = settings or get_settings()
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{accession}.txt"
    response = _get(
        url,
        settings=settings,
        extra_headers={"Range": f"bytes=0-{header_bytes - 1}"},
    )
    return response.text


def download_filing_file(
    cik: str,
    accession: str,
    filename: str,
    *,
    settings: Settings | None = None,
) -> Path:
    """Download one file from a filing's index into `edgar.cache_dir`; return its path.

    Used by `edgartools` (T11) to parse a downloaded cover page's iXBRL;
    this function only fetches and caches the bytes, never parses them.
    """
    settings = settings or get_settings()
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{filename}"
    response = _get(url, settings=settings)
    dest_dir = Path(settings.edgar.cache_dir) / str(int(cik)) / accession_nodash
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(response.content)
    return dest

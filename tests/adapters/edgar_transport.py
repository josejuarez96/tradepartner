"""A `MockTransport` router over the recorded EDGAR fixtures (T11b).

`EdgarRouter` serves `tests/fixtures/edgar/` by URL. A test adds its own
routes (a quarter's index, a synthetic line, a zip built in the test) with
`add`. An unmatched URL fails the test: the router raises pytest's
`Failed`, a `BaseException` no adapter code catches.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from tradepartner.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
USER_AGENT = "TradePartner test-agent test@example.com"

#: CIK -> recorded submissions files (main payload, older pages).
SUBMISSIONS = {
    "0000320193": ("submissions_plain_issuer.json", "submissions_plain_issuer_001.json"),
    "0001652044": ("submissions_dual_class.json", "submissions_dual_class_001.json"),
    "0001738827": ("submissions_delisted_25nse.json",),
}

_INDEX = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx"
_Route = Callable[[httpx.Request], httpx.Response]


def edgar_settings(cache_dir: Path, **edgar: Any) -> Settings:
    """Test settings: index from 2024, throttle negligible, cache in `cache_dir`."""
    overrides: dict[str, Any] = {
        "index_first_year": 2024,
        "requests_per_second": 1e6,
        "cache_dir": str(cache_dir),
        **edgar,
    }
    return Settings(_env_file=None, sec_edgar_user_agent=USER_AGENT, edgar=overrides)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def index_header() -> str:
    """The recorded 2024 Q1 `form.idx` with every data row removed."""
    lines = (FIXTURES / "filing_index_2024_qtr1.txt").read_text().splitlines(keepends=True)
    return "".join(line for line in lines if "edgar/data/" not in line)


def index_line(form: str, name: str, cik: int, filed: str, accession: str) -> str:
    """One `form.idx` data row in the recorded file's fixed-width layout."""
    return f"{form:<17}{name:<62}{cik:<12}{filed:<12}edgar/data/{cik}/{accession}.txt\n"


class EdgarRouter:
    """Serves recorded payloads by URL and records every URL asked for."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self._routes: dict[str, _Route] = {}
        self.add(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            (FIXTURES / "company_tickers.json").read_bytes(),
        )
        for cik, (main, *pages) in SUBMISSIONS.items():
            self.add(f"https://data.sec.gov/submissions/CIK{cik}.json", load(main))
            for n, page in enumerate(pages, start=1):
                name = f"CIK{cik}-submissions-{n:03d}.json"
                self.add(f"https://data.sec.gov/submissions/{name}", load(page))

    def add(self, url: str, body: bytes | str | dict[str, Any] | int | _Route) -> None:
        """Route `url` to a body, a JSON object, a bare status code or a handler."""
        if callable(body):
            self._routes[url] = body
        elif isinstance(body, int):
            self._routes[url] = lambda request, status=body: httpx.Response(status)
        elif isinstance(body, dict):
            self._routes[url] = lambda request, data=body: httpx.Response(200, json=data)
        else:
            content = body.encode("utf-8") if isinstance(body, str) else body
            self._routes[url] = lambda request, data=content: httpx.Response(200, content=data)

    def add_index(self, year: int, qtr: int, body: str | int) -> None:
        self.add(_INDEX.format(year=year, qtr=qtr), body)

    def index_urls(self) -> list[str]:
        return [u for u in self.urls if "/full-index/" in u]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        assert request.headers["User-Agent"] == USER_AGENT
        route = self._routes.get(url)
        if route is None:
            pytest.fail(f"unrouted EDGAR request: {url}")
        return route(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

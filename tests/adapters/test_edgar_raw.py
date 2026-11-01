"""Offline incremental EDGAR raw request and cache behavior."""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr

from tradepartner.adapters import edgar_raw
from tradepartner.config import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/edgar/daily_index"


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(sec_edgar_user_agent=SecretStr("Test test@example.com"))
    settings.edgar.cache_dir = tmp_path
    return settings


def test_daily_listing_and_cache_use_limiter(tmp_path: Path, monkeypatch: object) -> None:
    calls: list[str] = []
    waits: list[float] = []
    monkeypatch.setattr(edgar_raw._LIMITER, "wait", waits.append)  # type: ignore[attr-defined]

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("index.json"):
            return httpx.Response(200, content=(FIXTURE / "index.json").read_bytes())
        return httpx.Response(200, content=b"daily body")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        listing = edgar_raw.daily_index_listing(2026, 2, settings=settings, client=client)
        assert isinstance(listing["directory"]["item"], list)
        path = edgar_raw.daily_index_file(
            2026, 2, "form.20260401.idx", settings=settings, client=client
        )
        assert path.read_bytes() == b"daily body"
        assert (
            edgar_raw.daily_index_file(
                2026, 2, "form.20260401.idx", settings=settings, client=client
            )
            == path
        )
    assert len(calls) == len(waits) == 2


def test_quarterly_conditional_get_keeps_304_and_snapshots_changed_200(
    tmp_path: Path, monkeypatch: object
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(edgar_raw._LIMITER, "wait", waits.append)  # type: ignore[attr-defined]
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 2:
            assert request.headers["if-none-match"] == '"one"'
            return httpx.Response(304)
        body = b"first" if len(seen) == 1 else b"second"
        return httpx.Response(
            200, content=body, headers={"ETag": '"one"' if len(seen) == 1 else '"two"'}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        first, changed = edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
        assert changed and first.read_bytes() == b"first"
        stat = first.stat().st_mtime_ns
        again, changed = edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
        assert not changed and again == first and first.stat().st_mtime_ns == stat
        latest, changed = edgar_raw.cached_quarterly_index(
            2026, 2, settings=settings, client=client
        )
        assert changed and latest.read_bytes() == b"second"
        assert next(first.parent.glob("form.idx.*.snapshot")).read_bytes() == b"first"
    assert len(waits) == len(seen) == 3


def test_quarterly_same_body_refreshes_validator_without_rewriting_cache(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"same", headers={"ETag": f'"v{len(seen)}"'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        path, _ = edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
        stamp = path.stat().st_mtime_ns
        _, changed = edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
        assert not changed and path.stat().st_mtime_ns == stamp
        edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
    assert seen[2].headers["if-none-match"] == '"v2"'

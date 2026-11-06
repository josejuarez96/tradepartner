"""Offline incremental EDGAR raw request and cache behavior."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.daily_index import parse_daily_listing
from tradepartner.config import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/edgar/daily_index"
DAILY_BODY = gzip.decompress((FIXTURE / "form.20260401.idx.gz").read_bytes())
LISTED = parse_daily_listing(json.loads((FIXTURE / "index.json").read_text()), 2026, 2)[0]


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
        return httpx.Response(200, content=DAILY_BODY)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        listing = edgar_raw.daily_index_listing(2026, 2, settings=settings, client=client)
        assert isinstance(listing["directory"]["item"], list)
        path = edgar_raw.daily_index_file(2026, 2, LISTED, settings=settings, client=client)
        assert path.read_bytes() == DAILY_BODY
        assert edgar_raw.daily_index_file(2026, 2, LISTED, settings=settings, client=client) == path
    assert len(calls) == len(waits) == 2


def test_reposted_daily_file_is_refetched_and_old_body_is_snapshotted(tmp_path: Path) -> None:
    from dataclasses import replace

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=DAILY_BODY if len(seen) == 1 else b"corrected")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        path = edgar_raw.daily_index_file(2026, 2, LISTED, settings=settings, client=client)
        assert edgar_raw.daily_index_file(2026, 2, LISTED, settings=settings, client=client) == path
        reposted = replace(LISTED, last_modified="later", size="larger")
        assert (
            edgar_raw.daily_index_file(2026, 2, reposted, settings=settings, client=client) == path
        )
        assert path.read_bytes() == b"corrected"
        assert next(path.parent.glob(f"{path.name}.*.snapshot")).read_bytes() == DAILY_BODY
    assert len(seen) == 2


def test_daily_refresh_snapshots_changed_body_even_with_same_listing(tmp_path: Path) -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=DAILY_BODY if calls == 1 else b"corrected")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = _settings(tmp_path)
        path = edgar_raw.daily_index_file(2026, 2, LISTED, settings=settings, client=client)
        edgar_raw.daily_index_file(2026, 2, LISTED, refresh=True, settings=settings, client=client)
        assert path.read_bytes() == b"corrected"
        assert next(path.parent.glob(f"{path.name}.*.snapshot")).read_bytes() == DAILY_BODY
    assert calls == 2


def test_daily_file_refuses_a_name_not_at_its_listed_path(tmp_path: Path) -> None:
    from dataclasses import replace

    import pytest

    settings = _settings(tmp_path)
    with pytest.raises(edgar_raw.InvalidFilingReferenceError):
        edgar_raw.daily_index_file(
            2026,
            2,
            replace(LISTED, path="daily-index/2026/QTR2/form.20260404.idx"),
            settings=settings,
        )


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
        assert changed and gzip.decompress(first.read_bytes()) == b"first"
        stat = first.stat().st_mtime_ns
        again, changed = edgar_raw.cached_quarterly_index(2026, 2, settings=settings, client=client)
        assert not changed and again == first and first.stat().st_mtime_ns == stat
        latest, changed = edgar_raw.cached_quarterly_index(
            2026, 2, settings=settings, client=client
        )
        assert changed and gzip.decompress(latest.read_bytes()) == b"second"
        assert first == tmp_path / "index/2026-QTR2.idx.gz"
        assert gzip.decompress(first.read_bytes()) == b"second"
        assert (
            gzip.decompress(next(first.parent.glob("2026-QTR2.idx.gz.*.snapshot")).read_bytes())
            == b"first"
        )
        from tradepartner.adapters.edgar_source import EdgarFilingSource

        source = EdgarFilingSource(settings, client=client)
        assert source._index_text((2026, 2), (2026, 4)) == "second"
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

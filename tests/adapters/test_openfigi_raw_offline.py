"""No live API calls: verify mapping batches, pace, key handling and clocks."""

import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from tradepartner.adapters.openfigi_raw import map_tickers
from tradepartner.config import OpenfigiConfig


def test_keyless_batches_and_paces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    elapsed = [0.0]
    sizes = []
    sent_times = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "X-OPENFIGI-APIKEY" not in request.headers
        assert all(job["idType"] == "TICKER" and job["exchCode"] == "US" for job in body)
        sizes.append(len(body))
        sent_times.append(elapsed[0])
        return httpx.Response(200, json=[{"warning": "No identifier found."} for _ in body])

    def tick() -> datetime:
        value = datetime(2026, 10, 16, tzinfo=UTC) + timedelta(seconds=elapsed[0])
        elapsed[0] += 0.001
        return value

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = map_tickers(
            [f"T{i}" for i in range(126)],
            OpenfigiConfig(enabled=True),
            client=client,
            clock=tick,
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        )
    assert max(sizes) == 5
    assert len(sizes) == 26
    assert sent_times[25] - sent_times[0] >= 60
    assert all(sent < received for _, sent, received in result)


def test_keyed_limits_and_secret_free_error(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "synthetic-short-key"
    monkeypatch.setenv("OPENFIGI_API_KEY", secret)
    assert OpenfigiConfig().limits(keyed=True) == (25, 6, 100)
    assert OpenfigiConfig().limits(keyed=False) == (25, 60, 5)

    def fail(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-OPENFIGI-APIKEY"] == secret
        assert len(json.loads(request.content)) == 100
        raise httpx.ConnectError(f"transport leaked {secret}")

    with (
        httpx.Client(transport=httpx.MockTransport(fail)) as client,
        pytest.raises(RuntimeError) as caught,
    ):
        map_tickers([f"T{i}" for i in range(100)], OpenfigiConfig(enabled=True), client=client)
    assert secret not in str(caught.value)


def test_keyed_request_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENFIGI_API_KEY", "synthetic-key")
    now = [0.0]
    starts = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert len(body) <= 100
        starts.append(now[0])
        return httpx.Response(200, json=[{} for _ in body])

    def tick() -> datetime:
        value = datetime(2026, 10, 16, tzinfo=UTC) + timedelta(seconds=now[0])
        now[0] += 0.001
        return value

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        map_tickers(
            [f"T{i}" for i in range(2501)],
            OpenfigiConfig(enabled=True),
            client=client,
            clock=tick,
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
    assert len(starts) == 26
    assert starts[25] - starts[0] >= 6


def test_disabled_client_refuses_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        map_tickers(["VZ"], OpenfigiConfig())


def test_429_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    calls = []
    slept = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json=[{}])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = map_tickers(
            ["VZ"], OpenfigiConfig(enabled=True), client=client, sleep=slept.append
        )
    assert len(result) == 1 and len(calls) == 2
    assert slept == [60]


@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 10, 16),  # noqa: DTZ001 - deliberately naive
        datetime(2026, 10, 16, tzinfo=timezone(timedelta(hours=-4))),
    ],
)
def test_clock_must_be_utc_aware(bad: datetime) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("request sent"))
        ) as client,
        pytest.raises(ValueError, match="UTC-aware"),
    ):
        map_tickers(["VZ"], OpenfigiConfig(enabled=True), client=client, clock=lambda: bad)

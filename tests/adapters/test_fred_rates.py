"""Constructed FRED payloads and a fake transport; no live requests."""

from __future__ import annotations

import logging
import traceback
from datetime import date

import httpx
import pytest

from tradepartner.adapters import fred_raw
from tradepartner.adapters.fred_rates import FredRateSource, parse_observations
from tradepartner.adapters.fred_raw import FredCredentialsError, FredRequestError, observations
from tradepartner.config import Settings


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(fred_raw, "_sleep", sleeps.append)
    return sleeps


def _settings(key: str = "dummy", **rates: object) -> Settings:
    """Settings whose default chunking is one window unless `rates` says otherwise."""
    return Settings(
        _env_file=None, fred_api_key=key, rates={"fred_vintage_window_days": 36500, **rates}
    )


def test_parser_keeps_vintages_and_counts_missing() -> None:
    payload = {
        "observations": [
            {"date": "2020-03-16", "realtime_start": "2020-03-17", "value": "0.65"},
            {"date": "2020-03-16", "realtime_start": "2020-03-19", "value": "0.64"},
            {"date": "2020-03-17", "realtime_start": "2020-03-18", "value": "."},
        ]
    }
    rows, missing = parse_observations(payload, "DTB3")
    assert [(r.observation_date, r.value, r.vintage_date) for r in rows] == [
        (date(2020, 3, 16), 0.65, date(2020, 3, 17)),
        (date(2020, 3, 16), 0.64, date(2020, 3, 19)),
    ]
    assert missing == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"observations": [{"date": "bad", "realtime_start": "2020-03-17", "value": "1"}]},
        {"observations": [{"date": "2020-03-16", "realtime_start": "bad", "value": "1"}]},
        {"observations": [{"date": "2020-03-16", "realtime_start": "2020-03-17", "value": "oops"}]},
    ],
)
def test_parser_refuses_schema_drift(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_observations(payload, "DTB3")


def test_raw_client_pages_and_sends_required_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fred_raw, "_LIMIT", 2)
    offsets: list[int] = []
    records = [
        {"date": f"2020-01-0{i}", "realtime_start": "2020-01-06", "value": "1"} for i in (2, 3, 4)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fred/series/observations"
        params = request.url.params
        assert params["api_key"] == "dummy-secret"
        assert params["file_type"] == "json"
        assert params["output_type"] == "1"
        assert params["limit"] == "2"
        assert params["realtime_start"] == "2020-01-01"
        assert params["realtime_end"] == "9999-12-31"
        offset = int(params["offset"])
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "count": 3,
                "limit": 2,
                "observations": records[:1] if offset == 0 else records[offset : offset + 2],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        payload = observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=_settings(key="dummy-secret"),
            client=client,
        )
    assert offsets == [0, 1]
    assert payload["observations"] == records


@pytest.mark.parametrize("drift", [False, True])
def test_raw_client_refuses_empty_or_count_drift(
    monkeypatch: pytest.MonkeyPatch, drift: bool
) -> None:
    monkeypatch.setattr(fred_raw, "_LIMIT", 2)

    def respond(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "count": 4 if drift and offset else 3,
                "observations": [{"date": "2020-01-02"}] if offset == 0 else [],
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ValueError, match=r"count|empty"),
    ):
        observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=_settings(),
            client=client,
        )


def test_no_key_refuses_before_a_socket_request() -> None:
    with pytest.raises(FredCredentialsError, match="FRED_API_KEY"):
        observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=Settings(_env_file=None, fred_api_key=" "),
        )


@pytest.mark.parametrize("failure", ["connect", "status", "json", "invalid_url"])
def test_key_absent_from_debug_logs_and_chained_traceback(
    caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    key = "known-dummy-fred-key"
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")

    def fail(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("fake failure " + str(request.url))
        if failure == "invalid_url":
            raise httpx.InvalidURL("fake invalid URL " + str(request.url))
        if failure == "status":
            return httpx.Response(500, text="server failed")
        return httpx.Response(200, text="not-json")

    with (
        httpx.Client(transport=httpx.MockTransport(fail)) as client,
        pytest.raises(FredRequestError) as exc,
    ):
        observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=Settings(_env_file=None, fred_api_key=key),
            client=client,
        )
    assert key not in caplog.text
    assert key not in str(exc.value)
    assert key not in repr(exc.value)
    assert key not in "".join(traceback.format_exception(exc.value))
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None


def test_adapter_uses_raw_payload_and_counts_missing() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "count": 2,
                "observations": [
                    {"date": "2020-01-02", "realtime_start": "2020-01-03", "value": "1.2"},
                    {"date": "2020-01-03", "realtime_start": "2020-01-06", "value": "."},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        source = FredRateSource(settings=_settings(), client=client)
        rows = source.observations("DTB3", date(2020, 1, 1), date(2020, 1, 1))
    assert len(rows) == 1
    assert source.missing_values == 1


def test_adapter_sorts_unsorted_vintages() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "count": 3,
                "observations": [
                    {"date": "2020-01-03", "realtime_start": "2020-01-07", "value": "1.2"},
                    {"date": "2020-01-02", "realtime_start": "2020-01-06", "value": "1.1"},
                    {"date": "2020-01-03", "realtime_start": "2020-01-06", "value": "1.0"},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        source = FredRateSource(settings=_settings(), client=client)
        rows = source.observations("DTB3", date(2020, 1, 1), date(2020, 1, 1))
    assert [(row.observation_date, row.vintage_date) for row in rows] == [
        (date(2020, 1, 2), date(2020, 1, 6)),
        (date(2020, 1, 3), date(2020, 1, 6)),
        (date(2020, 1, 3), date(2020, 1, 7)),
    ]


def _ok(rows: list[dict[str, str]]) -> httpx.Response:
    return httpx.Response(200, json={"count": len(rows), "observations": rows})


def _row(obs: str, vintage: str = "2020-01-06") -> dict[str, str]:
    return {"date": obs, "realtime_start": vintage, "value": "1"}


def _fetch(handler: object, **rates: object) -> dict[str, object]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:  # type: ignore[arg-type]
        return observations(
            "DTB3", date(2020, 1, 1), date(2020, 1, 1), settings=_settings(**rates), client=client
        )


@pytest.mark.parametrize("failure", ["timeout", "502"])
def test_transient_failure_then_success_is_retried_with_backoff(
    failure: str, _no_sleep: list[float]
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout("slow " + str(request.url))
            return httpx.Response(502)
        return _ok([_row("2020-01-02")])

    payload = _fetch(respond, fred_max_attempts=3, fred_backoff_seconds=1.5)
    assert calls == 3
    assert payload["count"] == 1
    assert _no_sleep == [1.5, 3.0]


def test_client_error_is_not_retried(_no_sleep: list[float]) -> None:
    calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400)

    with pytest.raises(FredRequestError, match="HTTP 400"):
        _fetch(respond)
    assert calls == 1
    assert _no_sleep == []


def test_exhausted_attempts_raise_without_url_or_key(_no_sleep: list[float]) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow " + str(request.url))

    with pytest.raises(FredRequestError) as exc:
        _fetch(respond, fred_max_attempts=3)
    assert calls == 3
    assert len(_no_sleep) == 2
    text = "".join(traceback.format_exception(exc.value))
    assert "dummy" not in text
    assert "api.stlouisfed.org" not in text
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None


def test_owned_client_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    real = httpx.Client

    def build(**kwargs: object) -> httpx.Client:
        seen.append(kwargs.get("timeout"))
        return real(transport=httpx.MockTransport(lambda _: _ok([])))

    monkeypatch.setattr(fred_raw.httpx, "Client", build)
    observations(
        "DTB3", date(2020, 1, 1), date(2020, 1, 1), settings=_settings(fred_timeout_seconds=77.0)
    )
    assert seen == [77.0]


def test_vintage_windows_are_consecutive_and_end_open() -> None:
    assert fred_raw._vintage_windows(date(2020, 1, 1), 10, date(2020, 1, 25)) == [
        (date(2020, 1, 1), date(2020, 1, 10)),
        (date(2020, 1, 11), date(2020, 1, 20)),
        (date(2020, 1, 21), date(9999, 12, 31)),
    ]
    assert fred_raw._vintage_windows(date(2020, 1, 1), 10, date(2020, 1, 5)) == [
        (date(2020, 1, 1), date(9999, 12, 31))
    ]


def _true_periods() -> list[dict[str, str]]:
    """What one unbounded query returns: (date, value) with its true real-time period."""
    forever = "9999-12-31"

    def row(d: str, v: str, rs: str, re: str) -> dict[str, str]:
        return {"date": d, "value": v, "realtime_start": rs, "realtime_end": re}

    return [
        row("2020-01-02", "1.0", "2020-01-03", forever),  # spans every edge
        row("2020-01-03", "2.0", "2020-01-04", "2020-01-11"),  # ends exactly at an edge
        row("2020-01-03", "2.5", "2020-01-12", forever),  # new value starting at the edge
        row("2020-01-06", "3.0", "2020-01-08", "2020-01-14"),  # revised mid-window
        row("2020-01-06", "3.1", "2020-01-15", "2020-01-16"),
        row("2020-01-06", "3.0", "2020-01-17", forever),  # value returns: not a join
    ]


def _clipping_server(truth: list[dict[str, str]], seen: list[tuple[str, str]]) -> object:
    def respond(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        lo, hi = params["realtime_start"], params["realtime_end"]
        seen.append((lo, hi))
        out = []
        for r in truth:
            if r["realtime_start"] <= hi and r["realtime_end"] >= lo:
                out.append(
                    r
                    | {
                        "realtime_start": max(r["realtime_start"], lo),
                        "realtime_end": min(r["realtime_end"], hi),
                    }
                )
        out.sort(key=lambda r: (r["date"], r["realtime_start"]))
        return _ok(out)  # type: ignore[arg-type]

    return respond


def test_vintage_windowed_fetch_equals_unbounded_fetch() -> None:
    truth = _true_periods()
    seen: list[tuple[str, str]] = []
    server = _clipping_server(truth, seen)

    def fetch(**rates: object) -> dict[str, object]:
        with httpx.Client(transport=httpx.MockTransport(server)) as client:  # type: ignore[arg-type]
            return observations(
                "DTB3",
                date(2020, 1, 1),
                date(2020, 1, 1),
                settings=_settings(**rates),
                client=client,
            )

    windowed = fetch(fred_vintage_window_days=4)
    assert len(seen) > 3
    assert seen[0] == ("2020-01-01", "2020-01-04")
    assert seen[1] == ("2020-01-05", "2020-01-08")
    seen.clear()
    unbounded = fetch()
    assert len(seen) == 1
    assert windowed == unbounded
    assert unbounded["observations"] == truth
    assert unbounded["count"] == len(truth)


def test_failed_request_surfaces_fred_error_message_but_not_the_key_or_url() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error_code": 400, "error_message": "There are 2667 vintage dates dummy-k"}
        )

    with pytest.raises(FredRequestError) as exc:
        _fetch(respond)
    assert "HTTP 400: There are 2667 vintage dates" in str(exc.value)
    assert "api.stlouisfed.org" not in str(exc.value)


def test_error_message_is_scrubbed_of_the_key_and_capped() -> None:
    key = "known-dummy-fred-key"

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_message": f"bad {key} " + "x" * 500})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(FredRequestError) as exc,
    ):
        observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=_settings(key=key),
            client=client,
        )
    assert key not in str(exc.value)
    assert "bad ***" in str(exc.value)
    assert len(str(exc.value)) < 300

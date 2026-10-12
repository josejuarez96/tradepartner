"""Constructed FRED payloads and a fake transport; no live requests."""

from __future__ import annotations

import logging
import traceback
from datetime import date

import httpx
import pytest

from tradepartner.adapters.fred_rates import FredRateSource, parse_observations
from tradepartner.adapters.fred_raw import FredCredentialsError, FredRequestError, observations
from tradepartner.config import Settings


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


def test_raw_client_pages_and_sends_required_parameters() -> None:
    offsets: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fred/series/observations"
        params = request.url.params
        assert params["api_key"] == "dummy-secret"
        assert params["file_type"] == "json"
        assert params["output_type"] == "1"
        assert params["limit"] == "100000"
        assert params["realtime_start"] == "2020-01-01"
        assert params["realtime_end"] == "9999-12-31"
        offsets.append(int(params["offset"]))
        return httpx.Response(
            200,
            json={
                "count": 100001,
                "limit": 100000,
                "observations": [
                    {"date": "2020-01-02", "realtime_start": "2020-01-03", "value": "1"}
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        payload = observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=Settings(_env_file=None, fred_api_key="dummy-secret"),
            client=client,
        )
    assert offsets == [0, 100000]
    assert len(payload["observations"]) == 2


def test_no_key_refuses_before_a_socket_request() -> None:
    with pytest.raises(FredCredentialsError, match="FRED_API_KEY"):
        observations(
            "DTB3",
            date(2020, 1, 1),
            date(2020, 1, 1),
            settings=Settings(_env_file=None, fred_api_key=" "),
        )


def test_key_absent_from_debug_logs_and_chained_traceback(caplog: pytest.LogCaptureFixture) -> None:
    key = "known-dummy-fred-key"
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fake failure " + str(request.url))

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
    assert key not in "".join(traceback.format_exception(exc.value))


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
        source = FredRateSource(
            settings=Settings(_env_file=None, fred_api_key="dummy"), client=client
        )
        rows = source.observations("DTB3", date(2020, 1, 1), date(2020, 1, 1))
    assert len(rows) == 1
    assert source.missing_values == 1

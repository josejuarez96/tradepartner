"""The off-Mac heartbeat (#2155): ping shapes, the unset no-op, and that no failure
of the ping ever reaches the run or the logs with the URL in them."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from tradepartner import cli, heartbeat
from tradepartner.cli_record import _configured_secrets
from tradepartner.config import Settings
from tradepartner.ingest import OK, STALE, IngestResult, SourceRun

URL = "https://hc-ping.example.com/0a1b2c3d-secret-uuid"
NOW = datetime(2020, 7, 1, 23, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 0},
        edgar={"cache_dir": str(tmp_path / "edgar-cache")},
        sec_edgar_user_agent="Test Owner test-owner-ua@example.com",
        alpaca_api_key="test-key-id",
        alpaca_api_secret="test-key-secret",
        **overrides,
    )


class _Net:
    """Stands in for `heartbeat._get`, recording (url, timeout) per call."""

    def __init__(self, behaviour: Any = None) -> None:
        self.calls: list[tuple[str, float]] = []
        self.behaviour = behaviour

    def __call__(self, url: str, timeout: float) -> httpx.Response:
        self.calls.append((url, timeout))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return httpx.Response(self.behaviour or 200, request=httpx.Request("GET", url))


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> _Net:
    fake = _Net()
    monkeypatch.setattr(heartbeat, "_get", fake)
    return fake


def test_success_pings_the_plain_url_with_the_configured_timeout(tmp_path: Path, net: _Net) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_paper=URL, alerts={"heartbeat_timeout_seconds": 3})
    assert heartbeat.ping(s, "paper", ok=True) is True
    assert net.calls == [(URL, 3.0)]


def test_failure_pings_url_slash_fail(tmp_path: Path, net: _Net) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_ingest=URL + "/")
    assert heartbeat.ping(s, "ingest", ok=False) is True
    assert [u for u, _ in net.calls] == [URL + "/fail"]


def test_each_job_uses_its_own_url(tmp_path: Path, net: _Net) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_paper=URL + "-p")
    assert heartbeat.ping(s, "ingest", ok=True) is False
    assert net.calls == []
    assert heartbeat.ping(s, "paper", ok=True) is True


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_makes_no_call(tmp_path: Path, net: _Net, value: str | None) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_paper=value)
    assert heartbeat.ping(s, "paper", ok=True) is False
    assert net.calls == []


@pytest.mark.parametrize(
    "boom",
    [httpx.ConnectTimeout(URL), httpx.ConnectError(f"cannot reach {URL}"), RuntimeError(URL)],
)
def test_a_failing_ping_never_raises_and_never_logs_the_url(
    tmp_path: Path, net: _Net, boom: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    net.behaviour = boom
    s = _settings(tmp_path, alert_heartbeat_url_paper=URL)
    with caplog.at_level("DEBUG"):
        assert heartbeat.ping(s, "paper", ok=True) is False
    assert "heartbeat ping for paper failed" in caplog.text
    assert "secret-uuid" not in caplog.text and "hc-ping" not in caplog.text


def test_a_non_2xx_answer_is_a_warning_not_an_error(
    tmp_path: Path, net: _Net, caplog: pytest.LogCaptureFixture
) -> None:
    net.behaviour = 404
    s = _settings(tmp_path, alert_heartbeat_url_paper=URL)
    assert heartbeat.ping(s, "paper", ok=True) is False
    assert "status 404" in caplog.text and "secret-uuid" not in caplog.text


def test_a_non_http_url_is_refused_without_a_call(tmp_path: Path, net: _Net) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_paper="file:///etc/passwd")
    assert heartbeat.ping(s, "paper", ok=True) is False
    assert net.calls == []


def test_the_url_is_scrubbed_like_every_other_secret(tmp_path: Path) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_paper=URL, alert_heartbeat_url_ingest=URL + "-i")
    assert URL not in repr(s) and URL not in str(s)
    assert URL in _configured_secrets(s) and URL + "-i" in _configured_secrets(s)


def test_the_timeout_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _settings(tmp_path, alerts={"heartbeat_timeout_seconds": 0})


# CLI wiring: ingest ----------------------------------------------------------------


def _ingest(monkeypatch: pytest.MonkeyPatch, s: Settings, result: IngestResult | Exception) -> Any:
    def fake(_s: Settings, **_kw: Any) -> IngestResult:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli, "ingest_session", fake)
    app = cli.make_app(settings=lambda: s, clock=lambda: NOW)
    return CliRunner().invoke(app, ["ingest"])


def test_ingest_ok_pings_the_plain_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, net: _Net
) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_ingest=URL)
    result = _ingest(monkeypatch, s, IngestResult((SourceRun("edgar", OK, 1, "x", "fine"),)))
    assert result.exit_code == 0, result.output
    assert [u for u, _ in net.calls] == [URL]


def test_ingest_stale_pings_fail_and_keeps_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, net: _Net
) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_ingest=URL)
    result = _ingest(monkeypatch, s, IngestResult((SourceRun("alpaca", STALE, 0, "x", "late"),)))
    assert result.exit_code == 1
    assert [u for u, _ in net.calls] == [URL + "/fail"]


def test_ingest_crash_pings_fail_and_still_crashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, net: _Net
) -> None:
    s = _settings(tmp_path, alert_heartbeat_url_ingest=URL)
    result = _ingest(monkeypatch, s, RuntimeError("boom"))
    assert isinstance(result.exception, RuntimeError)
    assert [u for u, _ in net.calls] == [URL + "/fail"]


def test_ingest_unset_makes_no_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, net: _Net
) -> None:
    result = _ingest(
        monkeypatch, _settings(tmp_path), IngestResult((SourceRun("e", OK, 1, "x", "f"),))
    )
    assert result.exit_code == 0
    assert net.calls == []


def test_ingest_exit_code_survives_a_ping_that_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, net: _Net
) -> None:
    net.behaviour = httpx.ConnectTimeout("t")
    s = _settings(tmp_path, alert_heartbeat_url_ingest=URL)
    result = _ingest(monkeypatch, s, IngestResult((SourceRun("e", OK, 1, "x", "f"),)))
    assert result.exit_code == 0 and net.calls
    assert URL not in result.output

"""An early disk warning in `health` and a hard floor before `ingest` (#2454).

The disk is faked through `diskfloor.free_bytes`; no test reads the real one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from test_cli import T_END, _invoke, _settings

from tradepartner import diskfloor, heartbeat
from tradepartner.config import Settings
from tradepartner.store.db import open_for_write
from tradepartner.store.schema import init_schema

GB = 10**9
HEARTBEAT_URL = "https://hc-ping.example/ingest-check"


def _free(monkeypatch: pytest.MonkeyPatch, gb: float) -> list[Path]:
    probes: list[Path] = []

    def fake(path: Path) -> int:
        probes.append(path)
        return int(gb * GB)

    monkeypatch.setattr(diskfloor, "free_bytes", fake)
    return probes


def _pings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("ALERT_HEARTBEAT_URL_INGEST", HEARTBEAT_URL)
    calls: list[str] = []

    def fake(url: str, timeout: float) -> Any:
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(heartbeat, "_get", fake)
    return calls


def _keys(tmp_path: Path, **overrides: Any) -> Settings:
    return _settings(
        tmp_path,
        sec_edgar_user_agent="Test Owner test-owner-ua@example.com",
        alpaca_api_key="test-key-id",
        alpaca_api_secret="test-key-secret",
        **overrides,
    )


def test_defaults_put_the_warning_above_both_floors() -> None:
    s = Settings(_env_file=None)
    assert s.ingest.min_free_disk_gb == 5.0
    assert s.health.warn_free_disk_gb > s.ingest.min_free_disk_gb
    assert s.health.warn_free_disk_gb > s.paper.min_free_disk_gb


def test_floor_helper_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _free(monkeypatch, 4.9)
    msg = diskfloor.ingest_floor_refusal(s)
    assert msg is not None and "ingest.min_free_disk_gb" in msg and "4.9 GB" in msg
    _free(monkeypatch, 5.0)
    assert diskfloor.ingest_floor_refusal(s) is None
    _free(monkeypatch, 5.1)
    assert diskfloor.ingest_floor_refusal(s) is None


def test_floor_zero_turns_the_check_off_without_reading_the_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probes = _free(monkeypatch, 0.1)
    s = _settings(tmp_path, ingest={"min_free_disk_gb": 0})
    assert diskfloor.ingest_floor_refusal(s) is None
    assert probes == []


def test_ingest_refuses_below_the_floor_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _free(monkeypatch, 2.0)
    calls = _pings(monkeypatch)
    settings = _keys(tmp_path)
    result = _invoke(settings, ["ingest"])
    assert result.exit_code == 1, result.output
    assert "ingest.min_free_disk_gb" in result.output
    assert not Path(settings.store.path).exists(), "no store write"
    assert calls == [HEARTBEAT_URL + "/fail"]


def test_an_unscheduled_shape_refuses_without_pinging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _free(monkeypatch, 2.0)
    calls = _pings(monkeypatch)
    result = _invoke(_keys(tmp_path), ["ingest", "--source", "edgar", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert calls == []


def test_ingest_starts_above_the_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _free(monkeypatch, 6.0)
    result = _invoke(_keys(tmp_path), ["ingest"])
    assert "ingest.min_free_disk_gb" not in result.output


def _store(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)


def test_health_warns_below_the_early_threshold_and_not_above(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    _store(settings)
    _free(monkeypatch, 10.0)  # above the 5 GB floor, below the 15 GB warning
    low = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert "warning: low disk: 10.0 GB free" in low.output
    assert "1 warnings" in low.output
    _free(monkeypatch, 20.0)
    ok = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert "low disk" not in ok.output


def test_health_warning_off_at_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, health={"warn_free_disk_gb": 0})
    _store(settings)
    _free(monkeypatch, 1.0)
    out = _invoke(settings, ["health"], clock=lambda: T_END)
    assert "low disk" not in out.output

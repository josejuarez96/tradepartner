"""Tests for tradepartner.config.

Every default asserted here mirrors the "Config keys" list in
docs/specs/data-foundation.md. `store.path` and `edgar.cache_dir` are not
given explicit defaults in the spec; this task picks conservative ones under
the gitignored `data/` directory (see the PR's Open questions).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradepartner.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the real shell environment."""
    for key in (
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "SEC_EDGAR_USER_AGENT",
        "TRADEPARTNER_ENV_FILE",
        "UNIVERSE__EXCLUDE_SIC_RANGES",
    ):
        monkeypatch.delenv(key, raising=False)


def _settings() -> Settings:
    # Ignore any real .env on disk so defaults are what's actually asserted;
    # the "no .env present" acceptance criterion has its own dedicated test.
    return Settings(_env_file=None)


def test_settings_construct_with_no_env_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import and load succeed with no `.env` present (acceptance criterion)."""
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings is not None
    assert settings.store.path == "data/tradepartner.duckdb"


def test_store_defaults() -> None:
    s = _settings()
    assert s.store.path == "data/tradepartner.duckdb"
    assert s.store.lock_retry_seconds == 60


def test_calendar_defaults() -> None:
    s = _settings()
    assert s.calendar.start == date(1990, 1, 1)
    assert s.calendar.end == date(2035, 12, 31)


def test_ingest_defaults() -> None:
    s = _settings()
    assert s.ingest.settle_delay_minutes == 60
    assert s.ingest.reference_symbol == "SPY"
    assert s.ingest.max_missing_share == pytest.approx(0.05)


def test_edgar_defaults() -> None:
    s = _settings()
    # Anchored to the project root (T2 review round 2), not a bare relative
    # path: must be absolute and end with data/edgar_cache regardless of CWD.
    assert Path(s.edgar.cache_dir).is_absolute()
    assert Path(s.edgar.cache_dir) == Path(__file__).resolve().parents[1] / "data" / "edgar_cache"
    assert s.edgar.requests_per_second == pytest.approx(10.0)
    assert s.edgar.retry_backoff_seconds == pytest.approx(1.0)
    assert s.edgar.request_timeout_seconds == pytest.approx(30.0)
    assert s.edgar.header_bytes == 4096
    assert s.edgar.max_retry_after_seconds == pytest.approx(120.0)


def test_edgar_cache_dir_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert Path(_settings().edgar.cache_dir).is_absolute()


@pytest.mark.parametrize(
    "field",
    [
        "requests_per_second",
        "retry_backoff_seconds",
        "request_timeout_seconds",
        "header_bytes",
        "max_retry_after_seconds",
    ],
)
def test_edgar_thresholds_reject_zero(field: str) -> None:
    """Every `edgar.*` throttle/timeout/backoff threshold is `gt=0`: zero or
    negative would either hang or hot-loop `adapters.edgar_raw`."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, edgar={field: 0})


def test_master_defaults() -> None:
    s = _settings()
    assert s.master.transfer_window_sessions == 5
    assert s.master.issuer_forms == [
        "10-K",
        "10-Q",
        "8-K",
        "20-F",
        "40-F",
        "S-1",
        "F-1",
        "10-12B",
        "25",
        "25-NSE",
    ]
    assert s.master.static_columns == ["name", "ticker", "exchange"]


def test_benchmarks_default() -> None:
    assert _settings().benchmarks == ["SPY", "MTUM"]


def test_execution_fill_price_default_close() -> None:
    assert _settings().execution.fill_price == "close"


def test_execution_fill_price_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, execution={"fill_price": "colse"})


def test_universe_defaults() -> None:
    u = _settings().universe
    assert u.security_types == ["common"]
    assert u.exchanges == ["NYSE", "NASDAQ", "NYSE_AMERICAN"]
    assert u.min_price == 5
    assert u.liquidity_rule_enabled is True
    assert u.min_median_dollar_volume == 5_000_000
    assert u.liquidity_window == 20
    assert u.min_history_months == 12
    assert u.max_shares_age_days == 400
    assert u.top_n_by_cap == 1000


def test_guarded_sic_exclusion_pinned_to_charter_range() -> None:
    """Guarded per ADR 0006: full SIC 4900-4999 division, no carve-outs.

    Changing this default is a charter amendment, not a routine code change.
    """
    assert _settings().universe.exclude_sic_ranges == ((4900, 4999),)


def test_guarded_sic_exclusion_rejects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A env override can't silently loosen the guarded SIC exclusion."""
    monkeypatch.setenv("UNIVERSE__EXCLUDE_SIC_RANGES", "[]")
    with pytest.raises(ValidationError, match="guarded setting; change only by charter amendment"):
        Settings(_env_file=None)


def test_liquidity_rule_enabled_since_t3() -> None:
    """T3 (#84) turned it on: SIP history makes dollar volume a real measure."""
    assert _settings().universe.liquidity_rule_enabled is True


def test_alpaca_historical_feed_defaults_to_sip_and_rejects_others() -> None:
    assert _settings().alpaca.historical_feed == "sip"
    assert (
        Settings(_env_file=None, alpaca={"historical_feed": "iex"}).alpaca.historical_feed == "iex"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpaca={"historical_feed": "otc"})


def test_gap_defaults() -> None:
    s = _settings()
    assert s.gap.missing_tail_sessions == 5
    assert s.gap.count_share_threshold == pytest.approx(0.05)


def test_adjust_defaults() -> None:
    assert _settings().adjust.max_prior_close_gap_sessions == 5


@pytest.mark.parametrize("value", [0, -1])
def test_adjust_max_prior_close_gap_sessions_rejects_non_positive(value: int) -> None:
    """Zero sessions would reject every prior close, even the session right
    before the ex-date, silently dropping every dividend (#72)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, adjust={"max_prior_close_gap_sessions": value})


def test_secrets_default_to_none() -> None:
    s = _settings()
    assert s.alpaca_api_key is None
    assert s.alpaca_api_secret is None
    assert s.sec_edgar_user_agent is None


def test_secrets_absent_from_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "sk-live-abc123")
    monkeypatch.setenv("ALPACA_API_SECRET", "sk-live-secret456")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Jose Juarez jose@example.com")
    s = _settings()

    assert s.alpaca_api_key is not None
    assert s.alpaca_api_key.get_secret_value() == "sk-live-abc123"

    for blob in (repr(s), str(s)):
        assert "sk-live-abc123" not in blob
        assert "sk-live-secret456" not in blob
        assert "jose@example.com" not in blob


# --- .env resolution: anchored to the project root, not the CWD -----------


def test_env_file_anchored_to_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decoy `.env` in an unrelated CWD (e.g. `$HOME` for a launchd job)
    must never be picked up.
    """
    decoy = tmp_path / ".env"
    decoy.write_text("ALPACA_API_KEY=decoy-should-not-load\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.alpaca_api_key is None or (
        settings.alpaca_api_key.get_secret_value() != "decoy-should-not-load"
    )


def test_env_file_override_via_tradepartner_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_env = tmp_path / "custom.env"
    custom_env.write_text("ALPACA_API_KEY=custom-key-999\n")
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(custom_env))

    settings = Settings()

    assert settings.alpaca_api_key is not None
    assert settings.alpaca_api_key.get_secret_value() == "custom-key-999"

"""Tests for tradepartner.config.

Every default asserted here mirrors the "Config keys" lists in
docs/specs/data-foundation.md and docs/specs/backtest.md (Phase 3, T30).
`store.path` and `edgar.cache_dir` are not given explicit defaults in the
spec; T1 picked conservative ones under the gitignored `data/` directory.
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
        "HOLDOUT__START",
        "HOLDOUT__END",
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


# --- Phase 3 keys (docs/specs/backtest.md "Config keys", T30) ---


def test_hypotheses_families_default() -> None:
    assert _settings().hypotheses.families == ["momentum", "oracle"]


def test_hypotheses_family_outside_the_list_rejected() -> None:
    """Families are enumerated so N cannot be reset by renaming a family."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, hypotheses={"families": ["momentum", "value"]})


def test_strategy_defaults() -> None:
    s = _settings().strategy
    assert s.formation_months == 12
    assert s.skip_months == 1
    assert s.top_fraction == pytest.approx(0.10)
    assert s.weighting == "equal"
    assert s.signal_total_return is True


@pytest.mark.parametrize(
    "override",
    [
        {"weighting": "cap"},
        {"top_fraction": 0.0},
        {"top_fraction": 1.5},
        {"skip_months": -1},
        {"formation_months": 1, "skip_months": 1},
    ],
)
def test_strategy_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, strategy=override)


def test_costs_defaults() -> None:
    c = _settings().costs
    assert c.per_side_bps == pytest.approx(15.0)
    assert c.commission_per_share == pytest.approx(0.0)
    assert c.commission_per_order == pytest.approx(0.0)
    assert c.sensitivity_per_side_bps == [0.0, 30.0, 60.0, 100.0]


def test_costs_negative_sensitivity_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={"sensitivity_per_side_bps": [0, -5, 30]})


@pytest.mark.parametrize("field", ["per_side_bps", "commission_per_share", "commission_per_order"])
def test_costs_negative_component_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={field: -1})


def test_holdout_null_by_default() -> None:
    """No defaults: every hypothesis file names both dates (spec open question 2)."""
    h = _settings().holdout
    assert h.start is None
    assert h.end is None


def test_holdout_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            holdout={"start": date(2024, 1, 1), "end": date(2023, 12, 31)},
        )


def test_holdout_accepts_ordered_window() -> None:
    s = Settings(_env_file=None, holdout={"start": date(2024, 1, 1), "end": date(2026, 9, 30)})
    assert s.holdout.start == date(2024, 1, 1)
    assert s.holdout.end == date(2026, 9, 30)


def test_backtest_defaults() -> None:
    b = _settings().backtest
    assert b.initial_capital == pytest.approx(100_000.0)
    assert b.cash_rate == pytest.approx(0.0)
    assert b.delisting_exit == "last_close"
    assert b.stale_exit_sessions == 5


@pytest.mark.parametrize(
    "override",
    [
        {"initial_capital": 0},
        {"delisting_exit": "zero"},
        {"stale_exit_sessions": 0},
    ],
)
def test_backtest_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, backtest=override)


def test_metrics_defaults() -> None:
    m = _settings().metrics
    assert m.risk_free_rate == pytest.approx(0.0)
    assert m.red_flag_excess_cagr_pp == pytest.approx(3.0)


def test_metrics_has_no_periods_per_year() -> None:
    """`MONTHS_PER_YEAR = 12` is a derived constant (ADR 0006), not a key."""
    assert "periods_per_year" not in type(_settings().metrics).model_fields


def test_env_example_names_no_holdout_key() -> None:
    """`holdout.*` is never listed in `.env.example` (spec, Config keys)."""
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert "HOLDOUT__" not in env_example.read_text(encoding="utf-8").upper()


@pytest.mark.parametrize("level", [float("nan"), float("inf")])
def test_costs_non_finite_sensitivity_rejected(level: float) -> None:
    """A NaN level would silently turn that level's results into NaN."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={"sensitivity_per_side_bps": [0, level]})


@pytest.mark.parametrize("field", ["per_side_bps", "commission_per_share", "commission_per_order"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_costs_non_finite_component_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={field: value})


@pytest.mark.parametrize("families", [[], ["momentum", "momentum"]])
def test_hypotheses_families_empty_or_duplicate_rejected(families: list[str]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, hypotheses={"families": families})


@pytest.mark.parametrize(
    ("section", "override"),
    [
        ("hypotheses", {"familes": ["momentum"]}),
        ("strategy", {"top_fracton": 0.2}),
        ("costs", {"per_side_bp": 0}),
        ("holdout", {"stat": "2024-01-01"}),
        ("backtest", {"initial_capitol": 1.0}),
        ("metrics", {"red_flag": 1.0}),
    ],
)
def test_phase3_sections_reject_unknown_keys(section: str, override: dict[str, object]) -> None:
    """A typo in a pinned key must fail, not fall back to the default (spec req 10)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{section: override})


@pytest.mark.parametrize(
    ("section", "override"),
    [
        ("metrics", {"red_flag_excess_cagr_pp": -1.0}),
        ("metrics", {"red_flag_excess_cagr_pp": float("nan")}),
        ("metrics", {"risk_free_rate": float("nan")}),
        ("backtest", {"cash_rate": float("inf")}),
        ("backtest", {"initial_capital": float("inf")}),
        ("strategy", {"top_fraction": float("nan")}),
    ],
)
def test_phase3_rates_and_thresholds_reject_bad_values(
    section: str, override: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{section: override})


def test_misspelt_phase3_env_var_fails_loudly_naming_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COSTS__PER_SIDE_BP` (typo) must fail closed with the key in the error, not load
    the 15 bp default. Loading `Settings` then fails for every job, secrets included,
    so the message has to point straight at the bad key."""
    monkeypatch.setenv("COSTS__PER_SIDE_BP", "0")
    with pytest.raises(ValidationError, match="per_side_bp"):
        Settings(_env_file=None)

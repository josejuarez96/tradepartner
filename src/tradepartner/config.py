"""Application configuration.

A single `pydantic-settings` `Settings` object, read from environment
variables and an optional `.env` file (never required; `uv run pytest` must
pass with no `.env` present). Every threshold named in the data-foundation
spec's "Config keys" list and ADR 0006 is a field here with the documented
default — never hardcoded elsewhere (CLAUDE.md code standards).

`universe.exclude_sic_ranges` is **guarded**: it is a compliance exclusion
(ADR 0006), not a tunable parameter, and changes only through a charter
amendment.

Secrets (`ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`) are
`SecretStr` so their values never appear in `repr()`/`str()` of `Settings`,
including `SEC_EDGAR_USER_AGENT`, which by SEC convention embeds a personal
contact email.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class StoreConfig(BaseModel):
    """Point-in-time DuckDB store location and single-writer locking."""

    path: str = "data/tradepartner.duckdb"
    lock_retry_seconds: int = 60


class IngestConfig(BaseModel):
    """Daily ingest staleness and reference-symbol settings."""

    settle_delay_minutes: int = 60
    reference_symbol: str = "SPY"
    max_missing_share: float = 0.05


class EdgarConfig(BaseModel):
    """SEC EDGAR access, incl. `edgartools`' local cache."""

    cache_dir: str = "data/edgar_cache"


class MasterConfig(BaseModel):
    """Security-master construction rules."""

    transfer_window_sessions: int = 5
    issuer_forms: list[str] = Field(
        default_factory=lambda: [
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
    )
    static_columns: list[str] = Field(default_factory=lambda: ["name", "ticker", "exchange"])


class ExecutionConfig(BaseModel):
    """Backtest/paper fill assumptions."""

    fill_price: str = "close"


class UniverseConfig(BaseModel):
    """ADR 0006 universe-construction thresholds, rules 1-8, in order."""

    security_types: list[str] = Field(default_factory=lambda: ["common"])
    exchanges: list[str] = Field(default_factory=lambda: ["NYSE", "NASDAQ", "NYSE_AMERICAN"])
    # Guarded (ADR 0006): the full SIC 4900-4999 division (electric, gas,
    # water, sanitary services), no carve-outs. A compliance rule, not a
    # tunable parameter; changes only through a charter amendment.
    exclude_sic_ranges: list[list[int]] = Field(default_factory=lambda: [[4900, 4999]])
    min_price: float = 5
    liquidity_rule_enabled: bool = False
    min_median_dollar_volume: float = 5_000_000
    liquidity_window: int = 20
    min_history_months: int = 12
    max_shares_age_days: int = 400
    top_n_by_cap: int = 1000


class GapConfig(BaseModel):
    """Survivorship-gap reporting thresholds."""

    missing_tail_sessions: int = 5
    count_share_threshold: float = 0.05


class Settings(BaseSettings):
    """Root application settings, loaded from env vars and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    store: StoreConfig = Field(default_factory=StoreConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    edgar: EdgarConfig = Field(default_factory=EdgarConfig)
    master: MasterConfig = Field(default_factory=MasterConfig)
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY", "MTUM"])
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    gap: GapConfig = Field(default_factory=GapConfig)

    alpaca_api_key: SecretStr | None = Field(default=None)
    alpaca_api_secret: SecretStr | None = Field(default=None)
    sec_edgar_user_agent: SecretStr | None = Field(default=None)


def get_settings() -> Settings:
    """Load `Settings` fresh from the environment (no process-wide caching)."""
    return Settings()

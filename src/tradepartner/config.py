"""Application configuration.

A single `pydantic-settings` `Settings` object, read from environment
variables and an optional `.env` file (never required; `uv run pytest` must
pass with no `.env` present). Every threshold named in the data-foundation
spec's "Config keys" list and ADR 0006 is a field here with the documented
default — never hardcoded elsewhere (CLAUDE.md code standards).

`universe.exclude_sic_ranges` is **guarded**: it is a compliance exclusion
(ADR 0006), not a tunable parameter. A validator rejects any value other
than the charter default, including an environment override, so it cannot
be silently loosened; changing it is a charter amendment, not a config edit.

Secrets (`ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`) are
`SecretStr` so their values never appear in `repr()`/`str()` of `Settings`,
including `SEC_EDGAR_USER_AGENT`, which by SEC convention embeds a personal
contact email.

The `.env` file is anchored to the project root
(`Path(__file__).resolve().parents[2] / ".env"`), not the process's current
working directory: a scheduled job (e.g. launchd, run from `$HOME`) must
still find real secrets. `TRADEPARTNER_ENV_FILE` overrides the path
explicitly, e.g. for an alternate environment or a test fixture.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only value `universe.exclude_sic_ranges` may take (ADR 0006): the full
# SIC 4900-4999 division (electric, gas, water, sanitary services), no
# carve-outs. Changing it is a charter amendment, not a code or config change.
_GUARDED_EXCLUDE_SIC_RANGES: tuple[tuple[int, int], ...] = ((4900, 4999),)


def _default_env_file() -> Path:
    """Resolve the `.env` path fresh on every `Settings()` construction.

    Anchored to the project root so a process launched from an unrelated
    working directory (e.g. a launchd job run from `$HOME`) still finds it,
    unless `TRADEPARTNER_ENV_FILE` names a different path explicitly.
    """
    override = os.environ.get("TRADEPARTNER_ENV_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".env"


def _default_edgar_cache_dir() -> str:
    """`data/edgar_cache`, anchored to the project root the same way `.env` is.

    A relative default would resolve against the process's current working
    directory, which breaks the moment `cli_record`/ingest run from a
    different directory (e.g. a scheduled job run from `$HOME`); T2 review
    round 2 (safety-reviewer) flagged this after `download_filing_file`
    started writing there.
    """
    return str(Path(__file__).resolve().parents[2] / "data" / "edgar_cache")


class CalendarConfig(BaseModel):
    """XNYS calendar bounds.

    Pinned explicitly rather than left to `exchange_calendars`' default
    sliding window (today - 20y .. today + 1y): an unpinned range rejects
    dates before ~2006 and drifts with the current date, breaking
    reproducibility for backtests over older history.
    """

    start: date = date(1990, 1, 1)
    end: date = date(2035, 12, 31)


class StoreConfig(BaseModel):
    """Point-in-time DuckDB store location and single-writer locking.

    `lock_retry_initial_delay_seconds`/`.lock_retry_max_delay_seconds` are
    not in the spec's "Config keys" list; added here (T4, PR #20 review)
    so `store.db.open_for_write`'s lock-acquisition backoff is a config
    value rather than a hardcoded constant, per CLAUDE.md's "Thresholds
    and limits come from config, never hardcoded." A minimal, deliberate
    exception to T4 touching only its own files (`docs/plans/data-
    foundation.md` T1 lists `config.py` as a T1 file) — see that PR's
    "Notes for reviewer" for why it was made here instead of deferred.

    `lock_retry_initial_delay_seconds` and `.lock_retry_max_delay_seconds`
    must each be strictly positive (a zero or negative delay is not a
    backoff) and `initial <= max`, or `open_for_write`'s backoff would
    never actually grow, or would grow from nothing.
    `lock_retry_seconds` may be zero (a caller that wants "fail
    immediately, no retry" is a legitimate choice) but not negative.
    """

    path: str = "data/tradepartner.duckdb"
    lock_retry_seconds: int = Field(default=60, ge=0)
    lock_retry_initial_delay_seconds: float = Field(default=0.05, gt=0)
    lock_retry_max_delay_seconds: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _validate_lock_retry_backoff_bounds(self) -> StoreConfig:
        if self.lock_retry_initial_delay_seconds > self.lock_retry_max_delay_seconds:
            raise ValueError(
                "lock_retry_initial_delay_seconds "
                f"({self.lock_retry_initial_delay_seconds}) must be <= "
                f"lock_retry_max_delay_seconds ({self.lock_retry_max_delay_seconds})"
            )
        return self


class IngestConfig(BaseModel):
    """Daily ingest staleness and reference-symbol settings."""

    settle_delay_minutes: int = 60
    reference_symbol: str = "SPY"
    max_missing_share: float = 0.05


class EdgarConfig(BaseModel):
    """SEC EDGAR access, incl. `edgartools`' local cache.

    `requests_per_second`/`retry_backoff_seconds`/`request_timeout_seconds`/
    `header_bytes` were added in T2 review round 2 (safety-reviewer SHOULD
    FIX): `adapters/edgar_raw.py`'s throttle, retry backoff, HTTP timeout
    and SGML-header slice size were hardcoded module constants; CLAUDE.md
    requires thresholds to come from config.
    """

    cache_dir: str = Field(default_factory=_default_edgar_cache_dir)
    requests_per_second: float = 10.0
    retry_backoff_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    header_bytes: int = 4096


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

    fill_price: Literal["close", "open"] = "close"


class UniverseConfig(BaseModel):
    """ADR 0006 universe-construction thresholds, rules 1-8, in order."""

    security_types: list[str] = Field(default_factory=lambda: ["common"])
    exchanges: list[str] = Field(default_factory=lambda: ["NYSE", "NASDAQ", "NYSE_AMERICAN"])
    # Guarded (ADR 0006): see `_GUARDED_EXCLUDE_SIC_RANGES` and the validator
    # below. Not a tunable parameter; changes only through a charter amendment.
    exclude_sic_ranges: tuple[tuple[int, int], ...] = _GUARDED_EXCLUDE_SIC_RANGES
    min_price: float = 5
    liquidity_rule_enabled: bool = False
    min_median_dollar_volume: float = 5_000_000
    liquidity_window: int = 20
    min_history_months: int = 12
    max_shares_age_days: int = 400
    top_n_by_cap: int = 1000

    @field_validator("exclude_sic_ranges")
    @classmethod
    def _guard_exclude_sic_ranges(
        cls, value: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[int, int], ...]:
        if value != _GUARDED_EXCLUDE_SIC_RANGES:
            raise ValueError("guarded setting; change only by charter amendment")
        return value


class GapConfig(BaseModel):
    """Survivorship-gap reporting thresholds."""

    missing_tail_sessions: int = 5
    count_share_threshold: float = 0.05


class Settings(BaseSettings):
    """Root application settings, loaded from env vars and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
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

    def __init__(self, **kwargs: Any) -> None:
        # A per-instance default (not a class-level `model_config` value) so
        # `TRADEPARTNER_ENV_FILE` is re-read on every construction, e.g. after
        # `monkeypatch.setenv` in a test. An explicit `_env_file=...` kwarg
        # (used in tests to disable dotenv loading entirely) still wins.
        kwargs.setdefault("_env_file", _default_env_file())
        super().__init__(**kwargs)


def get_settings() -> Settings:
    """Load `Settings` fresh from the environment (no process-wide caching)."""
    return Settings()

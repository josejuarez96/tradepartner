"""Data-foundation config sections: calendar, store, ingest and rates (R1, issue #2512).

Moved verbatim from `tradepartner.config`, which re-exports every name here. This module
imports only `config_base`, never `tradepartner.config`.
"""

from __future__ import annotations

import math
import re
from datetime import date

from pydantic import Field, field_validator, model_validator

from tradepartner.config_base import _ClosedConfig


class CalendarConfig(_ClosedConfig):
    """XNYS calendar bounds.

    Pinned explicitly rather than left to `exchange_calendars`' default
    sliding window (today - 20y .. today + 1y): an unpinned range rejects
    dates before ~2006 and drifts with the current date, breaking
    reproducibility for backtests over older history.
    """

    start: date = date(1990, 1, 1)
    end: date = date(2035, 12, 31)


# A DuckDB memory size (#1418): a number and one of the units DuckDB's
# `memory_limit` parser accepts (1000^i or 1024^i), any case.
_DUCKDB_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B)", re.IGNORECASE)


class StoreConfig(_ClosedConfig):
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

    `writer_lock_retry_seconds` (#2178) is the longer wait the scheduled writers
    (`paper run`, `paper kill`, `ingest`) use in place of `lock_retry_seconds`, so a
    reader that holds the store for a minute or two (a dashboard Data health render
    is ~86 s) is out-waited. The sells have ~85 min of window, but the buys start
    after `paper.sell_wait_seconds` (about 15 min before the window closes), so
    the wrapper re-checks the submit window before every submit and sends nothing
    once it has closed, however long a lock wait took.

    `memory_limit` and `threads` (#1418) cap DuckDB's memory and worker
    threads on every store connection (`store.db.configure_connection`).
    Both default to `None`, which sets nothing and keeps DuckDB's own
    default (80% of RAM, one thread per core), so behaviour is unchanged
    unless the owner sets them (`STORE__MEMORY_LIMIT`, `STORE__THREADS`).
    `memory_limit` is a DuckDB size string, a positive number and one of
    DuckDB's units (`KB`/`MB`/`GB`/`TB` or `KiB`/`MiB`/`GiB`/`TiB`, any
    case, e.g. `"6GB"`); `threads` is a positive integer.
    """

    path: str = "data/tradepartner.duckdb"
    lock_retry_seconds: int = Field(default=60, ge=0)
    writer_lock_retry_seconds: int = Field(default=300, ge=0)
    lock_retry_initial_delay_seconds: float = Field(default=0.05, gt=0)
    lock_retry_max_delay_seconds: float = Field(default=1.0, gt=0)
    memory_limit: str | None = None
    threads: int | None = Field(default=None, ge=1)
    pre_migration_backups_to_keep: int = Field(default=2, ge=1)
    backup_disk_margin_bytes: int = Field(default=1_000_000_000, ge=0)

    @field_validator("memory_limit")
    @classmethod
    def _validate_memory_limit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = _DUCKDB_SIZE_RE.fullmatch(value.strip())
        if match is None or float(match.group(1)) <= 0:
            raise ValueError(
                "memory_limit must be a positive DuckDB size: a number and one of "
                "KB, MB, GB, TB, KiB, MiB, GiB, TiB (e.g. '6GB')"
            )
        return f"{match.group(1)}{match.group(2)}"

    @model_validator(mode="after")
    def _validate_lock_retry_backoff_bounds(self) -> StoreConfig:
        if self.lock_retry_initial_delay_seconds > self.lock_retry_max_delay_seconds:
            raise ValueError(
                "lock_retry_initial_delay_seconds "
                f"({self.lock_retry_initial_delay_seconds}) must be <= "
                f"lock_retry_max_delay_seconds ({self.lock_retry_max_delay_seconds})"
            )
        return self


class IngestConfig(_ClosedConfig):
    """Daily ingest staleness and reference-symbol settings."""

    settle_delay_minutes: int = 60
    reference_symbol: str = "SPY"
    # #2196: search only this many prior XNYS sessions for EDGAR-gap bars.
    edgar_gap_lookback_sessions: int = Field(default=5, ge=0, le=20)
    # Spec default, unmeasured. On SIP history a listed name should lack a bar only on a
    # halt or suspension; measure over ~20 real sessions and tighten (T3, #86).
    max_missing_share: float = 0.05
    # #796 (owner, option i a): a cap on the names a chunk reports instead of counting
    # (dark plus snapshot-only, as a share of the chunk's listed names), so a gradual
    # dropout that keeps `max_missing_share` under its limit still ends in a stale run.
    # 1.0 is off: the default is set from the backfill's measured baseline after T45b.
    max_dark_share: float = Field(default=1.0, ge=0.0, le=1.0)
    # Not in the spec's key list; added in T16 (safety-reviewer): an ingest run's stored
    # failure message is server-supplied text, capped so a large error page cannot fill
    # `ingestion_runs.message` and the page that shows it.
    max_message_chars: int = Field(default=2000, gt=0)
    # Added for #573: a failed run's message gets ` | at: <frames>` appended (file:line
    # in function, innermost first, across the `raise ... from` chain). `max_where_frames`
    # bounds how many frames are kept; `max_where_chars` bounds the whole message
    # (error text plus frames) before `max_message_chars`'s own cut, so a long frame
    # trail is itself truncated rather than crowding out the error text.
    max_where_frames: int = Field(default=8, gt=0)
    max_where_chars: int = Field(default=1500, gt=0)
    # #2197 (owner, warn don't refuse): `paper status` and the operations page show an
    # owner-visible line once this many sessions have closed since the last `ok` daily
    # EDGAR ingest run. Display only: paper keeps trading on the stale master.
    edgar_max_age_sessions: int = Field(default=3, ge=1, le=20)
    # #2454: free GB the store's disk must have for an ingest to start; below it the
    # ingest refuses before any write (clear message, exit 1, a /fail heartbeat for the
    # scheduled shape). 0 turns the check off. Run-time, same default as the paper floor.
    min_free_disk_gb: float = Field(default=5.0, ge=0)


class RatesConfig(_ClosedConfig):
    """FRED series and source-quality limits (data-foundation requirement 18)."""

    series: list[str] = Field(default_factory=lambda: ["DTB3"], min_length=1)
    max_stale_sessions: int = Field(default=3, ge=0)
    refetch_days: int = Field(default=10, gt=0)
    plausible_range_percent: list[float] = Field(default_factory=lambda: [-1.0, 25.0])
    # #1883: a full-vintage backfill is slow. Generous request timeout, bounded retry on
    # timeouts and 5xx (never 4xx) with exponential backoff. FRED rejects a query spanning
    # more than 2000 vintage dates (HTTP 400), so the real-time range is fetched in windows
    # of this many days (~260 vintage dates a year).
    fred_timeout_seconds: float = Field(default=60.0, gt=0.0, allow_inf_nan=False)
    fred_max_attempts: int = Field(default=4, ge=1)
    fred_backoff_seconds: float = Field(default=2.0, ge=0.0, allow_inf_nan=False)
    fred_vintage_window_days: int = Field(default=365, ge=1, le=1000)

    @field_validator("series")
    @classmethod
    def _valid_series(cls, value: list[str]) -> list[str]:
        if any(not name.strip() for name in value) or len(set(value)) != len(value):
            raise ValueError("rates.series must contain unique nonblank ids")
        return value

    @field_validator("plausible_range_percent")
    @classmethod
    def _valid_range(cls, value: list[float]) -> list[float]:
        if len(value) != 2 or not all(math.isfinite(x) for x in value) or value[0] >= value[1]:
            raise ValueError("rates.plausible_range_percent must be finite increasing bounds")
        return value

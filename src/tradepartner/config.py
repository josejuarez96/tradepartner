"""Application configuration.

A single `pydantic-settings` `Settings` object, read from environment
variables and an optional `.env` file (never required; `uv run pytest` must
pass with no `.env` present). Every threshold named in the "Config keys"
lists of the data-foundation and backtest specs and in ADR 0006 is a field
here with the documented default — never hardcoded elsewhere (CLAUDE.md code
standards).

`universe.exclude_sic_ranges` defaults to `()`: no sector is excluded unless a
hypothesis file names one (ADR 0006 amendment 2026-10-11, #1540). It is frozen in
each registration (guard a), so only its shape is validated here (`start <= end`).

Secrets (`ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`, the
Phase 4 `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` (book `main`'s pair; every
other book's is `ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY`/`..._API_SECRET`, ADR 0017
B.1, plan T153), the `ALERT_SMTP_*`
credentials and the research vendor's `TYPESAFE_API_KEY`) are `SecretStr` so
their values never appear in `repr()`/`str()` of `Settings`, including
`SEC_EDGAR_USER_AGENT`, `ALERT_EMAIL_TO` and `ALERT_EMAIL_FROM`, which embed a
personal contact email.

`alpaca.paper` is guarded: the
order path reaches the paper endpoint only (Phase 4 spec req 2), and Phase 6
changes it by ADR, never by an environment variable.

The `.env` file is anchored to the project root
(`Path(__file__).resolve().parents[2] / ".env"`), not the process's current
working directory: a scheduled job (e.g. launchd, run from `$HOME`) must
still find real secrets. `TRADEPARTNER_ENV_FILE` overrides the path
explicitly, e.g. for an alternate environment or a test fixture.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    SerializerFunctionWrapHandler,
    StrictBool,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# `edgar.statement_tags`' default (spec amendment 2026-10-03, #660, decision
# (a)): canonical names, each an ordered list of fallbacks, every one the
# concept itself, never a near relative.
#
# #1556 adds a financial-issuer revenue fact and the two constituents of its
# one documented definition, under names that never touch `revenue` (and so
# never touch `gross_profit`). `revenue_financial` is a financial issuer's one
# documented revenue quantity from its own filing: it holds
# **`us-gaap:RevenuesNetOfInterestExpense`** (the issuer's total revenue net of
# interest expense, the concept itself) when the filing reports it, else the
# sum of **net interest income (`us-gaap:InterestIncomeExpenseNet`) plus
# noninterest income (`us-gaap:NoninterestIncome`)**. The sum is computed by the
# ingest's derived-candidate rule (`ingest._derived_candidates`) whenever both
# constituents are the stored vintages of **one filing and one period** (never
# mixing filings), whatever `revenue` tag that filing carries, and
# stored like any derived row: `basis = derived`, `xbrl_tag` the join of the
# two source tags by the operator (`+`). A reported
# `RevenuesNetOfInterestExpense` row is `basis = reported`. The constituents
# are parsed and stored as filed; they are named here so the derivation can
# read them from the same filing's records. Keeping this out of `revenue`
# leaves non-financial issuers' `revenue` and every `gross_profit` byte-identical
# to before and keeps every row point-in-time; a consumer wanting one revenue
# series coalesces `revenue` and `revenue_financial` at read time, taking the
# later `known_at` of the rows it reads. Gross profit
# stays N/A for financials (no `cost_of_revenue`). IFRS issuers are out of
# scope (#1556).
_STATEMENT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:SalesRevenueNet",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:SalesRevenueGoodsNet",
        "us-gaap:SalesRevenueServicesNet",
    ),
    # #1556: a financial issuer's own revenue fact (see the comment above).
    "revenue_financial": ("us-gaap:RevenuesNetOfInterestExpense",),
    "cost_of_revenue": (
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:CostOfGoodsSold",
        "us-gaap:CostOfServices",
    ),
    "gross_profit": ("us-gaap:GrossProfit",),
    "total_assets": ("us-gaap:Assets",),
    "operating_cash_flow": (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    # #1556: the constituents of the financial-revenue sum above.
    "net_interest_income": ("us-gaap:InterestIncomeExpenseNet",),
    "noninterest_income": ("us-gaap:NoninterestIncome",),
}
#: A CIK as the store keys it, and a lettered common-class member (#1169).
_CIK = re.compile(r"\d{10}")
_CLASS_MEMBER = re.compile(r"CommonClass[A-Z]")
_QUALIFIED_TAG = re.compile(r"[A-Za-z][A-Za-z0-9-]*:[A-Za-z_][A-Za-z0-9_]*")
#: An `edgar.filename_listings_refused` entry (#1597): the CIK, then the
#: ticker derived no more for it.
_REFUSED_FILENAME = re.compile(r"\d{10}:[A-Z0-9][A-Z0-9.\-/]*")

# Shared zero-padded 24h HH:MM pattern used by both the config validator and
# `backtest/quiet.py`'s runtime check.
_HHMM_RE = re.compile(r"\A([01]\d|2[0-3]):([0-5]\d)\Z")


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


def _default_alpaca_assets_dir() -> str:
    """`data/alpaca_assets` (the borrow snapshot's raw keep, #1506 spec open
    question 17), anchored to the project root like `edgar.cache_dir`, so a
    scheduled run from another working directory keeps its files beside the
    store; gitignored by `data/*`."""
    return str(Path(__file__).resolve().parents[2] / "data" / "alpaca_assets")


def _default_research_data_dir() -> str:
    """`data/research` (the research store, research-labeling spec Definitions and
    ADR 0013 point 3), anchored to the project root like `edgar.cache_dir`, so a
    run launched from another working directory still finds it; gitignored by
    `data/*`."""
    return str(Path(__file__).resolve().parents[2] / "data" / "research")


class _ClosedConfig(BaseModel):
    """Shared validation for early configuration sections."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


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
    lock_retry_initial_delay_seconds: float = Field(default=0.05, gt=0)
    lock_retry_max_delay_seconds: float = Field(default=1.0, gt=0)
    memory_limit: str | None = None
    threads: int | None = Field(default=None, ge=1)

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


class EdgarConfig(BaseModel):
    """SEC EDGAR access, incl. `edgartools`' local cache.

    `requests_per_second`/`retry_backoff_seconds`/`request_timeout_seconds`/
    `header_bytes` were added in T2 review round 2 (safety-reviewer SHOULD
    FIX): `adapters/edgar_raw.py`'s throttle, retry backoff, HTTP timeout
    and SGML-header slice size were hardcoded module constants; CLAUDE.md
    requires thresholds to come from config. `max_retry_after_seconds` was
    added in round 3 (safety-reviewer MUST FIX): an SEC response naming an
    unreasonably large (or non-finite) `Retry-After` must not make
    `edgar_raw` sleep for that long, or at all, on a NaN/infinite value.
    `retry_max_attempts`/`retry_backoff_cap_seconds`/`rate_limit_wait_seconds`
    were added in #554 (research #572 pitfalls P1/P2/P10): a single 1-second
    retry couldn't outlast a real outage or SEC's rate-limit block, and a
    truncated zip just failed the whole run. `retry_max_attempts` caps the
    capped-exponential-backoff loop on `429`/`503`/a transport error;
    `retry_backoff_cap_seconds` caps that backoff's own growth (separately
    from `max_retry_after_seconds`, which caps how far an SEC-sent
    `Retry-After` is trusted); `rate_limit_wait_seconds` is the one wait
    before `403` (SEC's rate-limit block) is retried once, then failed.
    Every field here is `gt=0`: a zero or negative throttle/timeout/backoff
    is nonsensical and would either hang or hot-loop `edgar_raw`.

    `requests_per_second` defaults to 9, not 10 (#656, research #572 E3):
    SEC's 10 req/s is a ceiling, not a target; secedgar users saw 429s at
    9.7 req/s and edgartools defaults to 9.

    An unknown key is refused when the model is validated (#1037), so a
    mistyped `EDGAR__...` override fails instead of being silently ignored
    (`model_copy(update=...)` does not validate). A non-finite float is
    refused too (#1093): `inf` passes `gt=0`, and `requests_per_second=inf`
    would turn `edgar_raw`'s throttle off. Errors never echo the input value.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    cache_dir: str = Field(default_factory=_default_edgar_cache_dir)
    # `le=10` (#1108): SEC's ceiling is policy, not a tunable; a larger value would
    # shrink `edgar_raw`'s minimum interval toward 0. Tests that need no throttle
    # raise it with `model_copy(update=...)`, which does not validate.
    requests_per_second: float = Field(default=9.0, gt=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    header_bytes: int = Field(default=4096, gt=0)
    max_retry_after_seconds: float = Field(default=120.0, gt=0)
    retry_max_attempts: int = Field(default=5, gt=0)
    retry_backoff_cap_seconds: float = Field(default=60.0, gt=0)
    rate_limit_wait_seconds: float = Field(default=600.0, gt=0)
    # T11b (#163): the EDGAR `FilingSource`. The full index starts in 1993; a
    # quarter's raw index is cached only once fetched this many days after its
    # Eastern-time end; above this many CIKs to stamp, stamping reads the nightly
    # `submissions.zip` instead of per-CIK submissions.
    index_first_year: int = Field(default=1993, ge=1993)
    index_settle_days: int = Field(default=3, ge=0)
    bulk_stamp_threshold_ciks: int = Field(default=500, gt=0)
    # #1382 guard: SEC's `submissions.zip` serves `acceptanceDateTime` moved by
    # New York's UTC offset (early for many CIKs, a look-ahead), so stamps never
    # come from it unless this is turned on; above the threshold, stamping goes
    # per CIK like below it. Turn on only once the zip is shown to match the
    # SGML header again (data-foundation plan, the #1382 repair task).
    bulk_stamps_enabled: bool = False
    # #1494 guard: since 2026-10 SEC's per-CIK submissions API has (on and off)
    # served `acceptanceDateTime` moved LATE by New York's UTC offset. With this
    # on, a CIK's new per-CIK stamps are written only after one of them matches
    # its SGML header's ACCEPTANCE-DATETIME (the master acceptance); on a
    # mismatch each new stamp is taken from its own SGML header instead.
    stamp_sgml_check: bool = True
    # #1686: after this many consecutive runs in which none of a CIK's three
    # newest SGML anchors can be read (404/410 or unparseable), the CIK's new
    # stamps are cached as the per-CIK API served them. That stamp is late by
    # New York's UTC offset (4 h EDT, 5 h EST) or exact, never early, so the
    # fallback delays a row and cannot look ahead. Counted per CIK on disk
    # (`sgml_unread/v1.json`), reset by any run whose anchor reads, and
    # reported on the run's message (`stamps from API fallback`).
    stamp_sgml_fallback_runs: int = Field(default=3, ge=1)
    # The longer range asked once for a stamp check's header that the first
    # `header_bytes` cut before `</SEC-HEADER>` (a Form 4 with many owners).
    stamp_header_bytes: int = Field(default=65536, gt=0)
    cover_page_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["10-K", "10-Q", "20-F", "40-F"], min_length=1
    )
    # T11g (#216, #231): the oldest year of the SEC Financial Statement and Notes
    # data sets to fetch. `2009` is FSN's own start; the default `2015` is
    # one year before the 2016 price start, so the latest SIC before any
    # T >= 2016 is already present.
    fsn_first_year: int = Field(default=2015, ge=2009)
    # T11d's keys, added in T11g so FSN extraction keeps these forms' SIC from
    # the first run (no FSN_VERSION bump later). 8-K is included so a de-SPAC's
    # new SIC arrives with its 8-K. `header_first_year` None means
    # `fsn_first_year`, so an override of one follows the other.
    header_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["S-1", "F-1", "10-12B", "8-K", "10-K", "10-Q", "20-F", "40-F"],
        min_length=1,
    )
    header_first_year: int | None = Field(default=None, ge=1993)
    # T11h: the failure policy (a filing that fails the same way on every
    # run). An accession failing identically this many consecutive counted
    # (Eastern) days is quarantined: no further request until its entry is
    # deleted or FAILURES_VERSION changes.
    max_filing_failures: int = Field(default=3, gt=0)
    # `check_failures()`'s per-group and cross-day thresholds: a group's (or
    # one error-class/base-form pair's) failures must clear both the count
    # floor and the share ceiling to fail the chunk, so neither a handful of
    # failures in a huge FSN period nor a tiny share of a huge run alone
    # trips it.
    min_failed_filings: int = Field(default=5, gt=0)
    max_failed_filing_share: float = Field(default=0.01, gt=0, le=1)
    # #578: how many of the fetch pass's parse failures the run message names
    # (the full list goes to a file under `cache_dir/validation/` whose path
    # the message gives), so a run with thousands stays readable. Also bounds
    # `check_failures`'s list of unaccepted filing failures (#578 part 3; the
    # full list is `failed_filings.json`, written on a non-dry run).
    max_validation_listed: int = Field(default=20, gt=0)
    # T77 (#660, spec amendment 2026-10-03): as-filed statement facts. T78
    # (#1127) turned the switch on after the real-store run passed; while off,
    # nothing reads the other three keys. `statement_tags` maps each canonical
    # fact name to its ordered `taxonomy:tag` fallbacks (the order is the
    # precedence within one filing); `statement_forms` filters on the
    # submissions record's form, never the companyfacts entry's; only
    # `statement_units` are read, any other unit is skipped and counted. None of
    # the three may be empty, nor any fact's fallback list (#1037): an empty one
    # would silently read no statement facts at all.
    statement_facts_enabled: bool = True
    statement_tags: dict[str, Annotated[list[str], Field(min_length=1)]] = Field(
        default_factory=lambda: {name: list(tags) for name, tags in _STATEMENT_TAGS.items()},
        min_length=1,
    )
    statement_forms: list[str] = Field(
        default_factory=lambda: ["10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"],
        min_length=1,
    )
    statement_units: list[str] = Field(default_factory=lambda: ["USD"], min_length=1)
    # T164e (#1358, spec amendment 2026-10-09): filing events. While the
    # switch is off the ingest never asks `filing_events`, so the adapter makes
    # no request and no cache write for it and no row is added. It stays off
    # until the real-store run has passed and the owner has closed #1382 (the
    # stamps clock: an early stamp is a look-ahead for an event read). Only a
    # form in `event_forms` is ever a row; never empty. Narrowing it later
    # fails `health --check` on the stored rows of a dropped form until a
    # rebuild (the amendment's open question 12).
    filing_events_enabled: bool = False
    # T179 (#1541, spec amendment 2026-10-11): the title fallback in
    # `store.classify` rule 6 (placeholder titles, whole-word type words,
    # issuer-name-first titles, class-letter shares). Off, every classification
    # is as before; the release's own PR (T181) flips it with the cache
    # version bumps, so a nightly ingest after a plain merge changes nothing.
    title_fallback: bool = True  # flipped by the release `cover-listings-1541` (T181)
    event_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["8-K", "8-K/A"], min_length=1
    )
    # #1541 (spec amendment 2026-10-11): both cover-page parsers read a
    # context dimensioned by `dei:EntityListingsExchangeAxis` as a listing
    # context. Off until the owner's release (T181) flips it together with
    # the `FSN_VERSION` and `COVER_VERSION` bumps: the caches are keyed by
    # those versions only, so a flip alone re-derives nothing already cached.
    exchange_axis_listings: bool = True  # flipped by the release `cover-listings-1541` (T181)
    # #1651, #1660: the cover-page parser and `parse_fsn` also read `CapitalUnitsByClassAxis` as a
    # class axis (Fifth Third) and merges a class's title, symbol and exchange
    # tagged on several contexts of one class member (Abbott 2020, P&G 2023).
    # Flipped on by the release `cover-listings-1541` (T181) at cache version 4,
    # then backed out by the owner in the same release (2026-10-14, cache
    # version 6): its class identities wait on the security-master review (#1682).
    cover_split_contexts: bool = False
    # #1169 (owner decision 2026-10-07, option 3): per CIK, the class member
    # (`CommonClass<letter>`) whose shares facts go to the CIK's one common
    # class although no title of that class names the letter. Owner-reviewed,
    # never a general rule: DICK'S (DKS), Tootsie Roll (TR) and Vimeo (VMEO)
    # title their one listed class "Common Stock" and tag its shares
    # `CommonClassA` (Class B is unlisted). Keys are 10-digit CIKs.
    class_member_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "0001089063": "CommonClassA",  # DKS
            "0000098677": "CommonClassA",  # TR
            "0001837686": "CommonClassA",  # VMEO
        }
    )
    # T190d (#1682 lever F): the per-class shares link at ingest. On, a
    # shares fact `fact_rows` leaves unmatched goes to the one common class
    # its member names by `universe.member_links_title` (BF.B, FOX, MKC), a
    # lettered member to the sole listed common when no title names a letter
    # and the filing's other classes are nominal (IBKR, SPG), and a common
    # class missing from the CIK's last `dead_class_filings` cover pages stops
    # being a candidate for a total (WBD's old series), all as of each fact's
    # acceptance.
    # Off, every `facts` row is as before. The release `rederive-1682` (T198)
    # flips it with the facts re-derive, never a nightly run before it.
    class_member_link: bool = False
    # Rule 3's page count: one fiscal year of periodic cover pages.
    dead_class_filings: int = Field(default=4, ge=1)
    # Rule 2's nominal class: every other member of the filing at most this
    # share of the largest (IBKR's 400 Class B shares beside 453M Class A).
    # The census on #1723: a looser rule put an unlisted Class B's count on
    # the listed common (Ford, Weber); DKS's 24M Class B is not nominal, so
    # `class_member_overrides` keeps its three entries.
    lettered_member_max_minor_share: float = Field(default=0.001, gt=0, lt=1)
    # T184 (#1597, spec amendment 2026-10-12): pre-2019 listings read from a
    # filing's own file names and its primary document's text. While the switch
    # is off the EDGAR `FilingSource` answers `filename_listings` as it does
    # today (no answer: T184b owns the fetch, T184c the confirmation rule,
    # T184e the write), makes no request and writes no cache entry, and the
    # store changes nothing. It stays off until the real-store run has passed
    # and #1597's open questions 22 to 24 are answered (T184h's gate).
    filename_listings: bool = False
    # The oldest year read: the amendment's rows are a 10-K's own, and the
    # window starts at FSN's 2015 so no reading is asked for a filing the
    # FSN data sets do not cover.
    filename_listings_since: date = date(2015, 1, 1)
    # The annual-report forms read (10-Qs are out of scope per the
    # amendment). Never empty: an empty one would silently read nothing.
    filename_listing_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["10-K", "10-KT"], min_length=1
    )
    # The primary document's read cap for a 10-K's Section 12(b) table: the
    # read stops at the table or here, whichever comes first (#1597), so a
    # filing whose table is never written costs one bounded read.
    filename_document_max_bytes: int = Field(default=4_000_000, gt=0)
    # (CIK, ticker) spans whose `filing_name` rows the run derives no more
    # and `master-retract --release` withdraws: the amendment's rule for a
    # collision the Alpaca backfill counted. Default empty. A malformed
    # entry (not `CIK:TICKER`) refuses the config, so a typo never leaves a
    # live name derived silently.
    filename_listings_refused: list[str] = Field(default_factory=list)

    @field_validator("filename_listings_refused")
    @classmethod
    def _check_filename_listings_refused(cls, value: list[str]) -> list[str]:
        """Every entry is `CIK:TICKER` (#1597: owner-listed, never inferred)."""
        for entry in value:
            if not _REFUSED_FILENAME.fullmatch(entry):
                raise ValueError(
                    "edgar.filename_listings_refused: an entry is not `CIK:TICKER` "
                    f"(got {len(entry)} characters)"
                )
        return value

    @field_validator("class_member_overrides")
    @classmethod
    def _class_member_overrides_are_well_formed(cls, value: dict[str, str]) -> dict[str, str]:
        """Each key is a 10-digit CIK and each value `CommonClass<A-Z>` (#1169)."""
        for cik, member in value.items():
            if not _CIK.fullmatch(cik):
                raise ValueError("edgar.class_member_overrides: a key is not a 10-digit CIK")
            if not _CLASS_MEMBER.fullmatch(member):
                raise ValueError(f"edgar.class_member_overrides[{cik!r}] is not `CommonClass<A-Z>`")
        return value

    @field_validator("statement_tags")
    @classmethod
    def _statement_tags_are_qualified(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """Every tag is `taxonomy:tag` (#660), so `us-gaap:Revenues` and a
        same-named concept in another taxonomy are never confused."""
        for name, tags in value.items():
            for tag in tags:
                if not _QUALIFIED_TAG.fullmatch(tag):
                    raise ValueError(
                        f"edgar.statement_tags[{name!r}]: {tag!r} is not `taxonomy:tag`"
                    )
        return value

    @property
    def header_start_year(self) -> int:
        """`header_first_year`, or `fsn_first_year` when it is unset."""
        return self.fsn_first_year if self.header_first_year is None else self.header_first_year


class MasterConfig(_ClosedConfig):
    """Security-master construction rules."""

    transfer_window_sessions: int = 5
    #: Sessions either side of a Form 25's filing session in which an 8-K12B
    #: (successor issuer) or a Form 15 (deregistration) belongs to it (#834):
    #: the real cases sit 6 sessions before to 9 after.
    reorganisation_window_sessions: int = 10
    #: Days after a Form 25's effective day before a companies snapshot still
    #: naming the ticker counts as listing evidence (#834), so a fetch before
    #: SEC drops a delisted ticker never relists it.
    snapshot_relisting_lag_days: int = 30
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
    #: Successor ids (`<cik>@<YYYY-MM-DD>`, `-<n>` for a second one that day,
    #: #820) the owner accepted although the current rules no longer derive
    #: them (#922: MTCH's July 2020 IAC/Match separation, #828).
    #: `master-retract` never proposes their rows and reports them as kept,
    #: and an EDGAR ingest's check does not record them, so health's
    #: `underived_master_rows` does not fail on them. A malformed id refuses
    #: the config.
    keep_successors: list[str] = Field(default_factory=list)
    #: Owner-reviewed 1:1 successor-CIK chains (#821: the amendment of
    #: 2026-10-12): `<predecessor>-><successor>@<accession>`, each side a
    #: `<cik>` or `<cik>:<class>` security id (never a `<cik>@<date>` or a
    #: `keep_successors` id), the two sides of different CIKs, and the
    #: accession the successor CIK's 8-K12B or 8-K12G3. One id may appear in
    #: only one entry (a chained successor's own later reorganisation waits
    #: for an amendment). A malformed entry refuses the config, never chains
    #: silently.
    chains: list[str] = Field(default_factory=list)
    #: Successor ids the owner reviewed and struck (#821): never counted as
    #: candidates on the EDGAR run row (T187c). A malformed id refuses the
    #: config.
    chains_dismissed: list[str] = Field(default_factory=list)
    #: Sessions before a candidate's listing row that another company's
    #: security last listed its ticker and exchange (#821): `chain_candidates`
    #: names a filer whose ticker and exchange another company held this many
    #: sessions or fewer before it. 65, the #821 evidence bound.
    chain_window_sessions: int = Field(default=65, ge=0)
    #: The close ratio across a chain's hand-over above which `health` warns
    #: `chain_price_break` (#821): the ratio or its inverse. 1.15, so a break
    #: over 15% either way.
    chain_max_price_ratio: float = Field(default=1.15, gt=0)

    @field_validator("keep_successors")
    @classmethod
    def _check_keep_successors(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_successor_id(entry)
        return value

    @field_validator("chains")
    @classmethod
    def _check_chains(cls, value: list[str]) -> list[str]:
        parse_chains(value)
        return value

    @field_validator("chains_dismissed")
    @classmethod
    def _check_chains_dismissed(cls, value: list[str]) -> list[str]:
        for entry in value:
            check_security_id(entry, "master.chains_dismissed")
        return value


#: A chain entry's accession: an SEC accession, `NNNNNNNNNN-YY-NNNNNN`.
_CHAIN_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")

#: A chain entry's security id: a ten-digit CIK, optionally a `:<class slug>`.
#: A `<cik>@<date>` successor (and so a `keep_successors` id) never matches.
_CHAIN_ID = re.compile(r"\d{10}(?::[a-z0-9]+(?:-[a-z0-9]+)*)?")


def parse_chains(entries: list[str]) -> list[tuple[str, str, str]]:
    """Parse `master.chains` entries (#821) into `(predecessor_id,
    successor_id, accession)` tuples, in order. Raises `ValueError` naming the
    key and entry on any other shape: not exactly one `->`, an id that is not
    a `<cik>` or `<cik>:<class>` id (a `<cik>@<date>` or `keep_successors` id
    is never chained), two ids of the same CIK, a malformed accession, or an
    id used in another entry."""
    parsed: list[tuple[str, str, str]] = []
    seen: dict[str, str] = {}
    for entry in entries:
        predecessor, arrow, rest = entry.partition("->")
        if not arrow or "->" in rest:
            raise ValueError(
                f"master.chains entry {entry!r} is not '<predecessor>-><successor>@<accession>'"
            )
        successor, at, accession = rest.rpartition("@")
        if not at:
            raise ValueError(
                f"master.chains entry {entry!r} is not '<predecessor>-><successor>@<accession>'"
            )
        for side, value in (("predecessor", predecessor), ("successor", successor)):
            if _CHAIN_ID.fullmatch(value) is None:
                raise ValueError(
                    f"master.chains entry {entry!r}: {side} {value!r} is not a "
                    "'<cik>' or '<cik>:<class>' security id (never a "
                    "'<cik>@<date>' or a keep_successors id)"
                )
        # #1838: the parsed CIK, not the id's first ten characters.
        if predecessor.partition(":")[0] == successor.partition(":")[0]:
            raise ValueError(
                f"master.chains entry {entry!r}: predecessor and successor are "
                "the same CIK, so there is nothing to chain"
            )
        if _CHAIN_ACCESSION.fullmatch(accession) is None:
            raise ValueError(
                f"master.chains entry {entry!r}: accession {accession!r} is not "
                "'NNNNNNNNNN-YY-NNNNNN'"
            )
        for value in (predecessor, successor):
            other = seen.get(value)
            if other is not None:
                raise ValueError(
                    f"master.chains entry {entry!r}: {value!r} already appears in "
                    f"entry {other!r}; an id may be in only one entry"
                )
            seen[value] = entry
        parsed.append((predecessor, successor, accession))
    return parsed


#: A successor `security_id` as `store.master._successor` writes it.
_SUCCESSOR_ID = re.compile(r"(?P<cik>\d{10})@(?P<day>\d{4}-\d{2}-\d{2})(?:-(?P<n>[2-9]|[1-9]\d+))?")


def parse_successor_id(entry: str) -> tuple[str, date]:
    """A `master.keep_successors` entry, `"<cik>@<YYYY-MM-DD>"` with an
    optional `-<n>` (n >= 2), as `(cik, valid_from)`. Raises `ValueError` on
    any other shape or an invalid day."""
    match = _SUCCESSOR_ID.fullmatch(entry)
    if match is None:
        raise ValueError(
            f"master.keep_successors entry {entry!r} is not a successor id "
            "'<10-digit cik>@<YYYY-MM-DD>' (optionally '-<n>')"
        )
    try:
        day = date.fromisoformat(match["day"])
    except ValueError:
        raise ValueError(f"master.keep_successors entry {entry!r} names no valid day") from None
    return match["cik"], day


#: A master `security_id`: a ten-digit CIK, then nothing, a `:<class slug>`
#: (`store.master._new_class`) or an `@<day>` successor (`-<n>`, #820).
_SECURITY_ID = re.compile(
    r"\d{10}(?::[a-z0-9]+(?:-[a-z0-9]+)*|@(?P<day>\d{4}-\d{2}-\d{2})(?:-(?:[2-9]|[1-9]\d+))?)?"
)


def check_security_id(entry: str, key: str) -> str:
    """`entry` if it is a master security id (`_SECURITY_ID`, with a valid
    day for a successor); else `ValueError` naming the config `key`."""
    match = _SECURITY_ID.fullmatch(entry)
    if match is None:
        raise ValueError(
            f"{key} entry {entry!r} is not a security id "
            "'<10-digit cik>', '<cik>:<class>' or '<cik>@<YYYY-MM-DD>'"
        )
    if match["day"] is not None:
        try:
            date.fromisoformat(match["day"])
        except ValueError:
            raise ValueError(f"{key} entry {entry!r} names no valid day") from None
    return entry


class AlpacaConfig(BaseModel):
    """Alpaca market-data choices resolved by the owner in T3 (#84).

    `historical_feed`: the free plan returns consolidated SIP history when the request
    `end` is at least 15 minutes in the past (confirmed 2026-09-25 with the owner's keys;
    docs/research/2026-09-25-free-data-terms.md). Real-time is IEX-only, so a caller
    asking for the current session inside that window must use `iex`.

    `actions_process_lag_days`: Alpaca's corporate-actions `start`/`end` filter on
    `process_date`, which can trail the ex-date by weeks (a GE dividend: ex 2020-12-18,
    processed 2021-01-25; #101 probe 1). `AlpacaPriceSource` asks for actions processed up
    to this many calendar days after the end of an ex-date window, then filters on ex-date.

    `registrant_quiet_days`: `ListingResolver` rule 6 (#793) takes a ticker's holder to
    have left it the day after its company's last cover-page share count once more than
    this many calendar days have passed by the run with none. Since 2022 the store's
    gaps between one company's share-count filings are 91 days at the median, 124 at the
    90th and 168 at the 99th percentile, so 180 marks a missed filing.

    `max_catchup_sessions`: how many XNYS sessions a daily ingest run may fetch that the
    store lacks before its expected session (#1572, from #1550), so a day a failed run
    left out is caught up by the next one instead of leaving a hole that empties the
    universe. Over it the run fails and names the `--backfill --since` command that
    clears the hole, which is the right tool for a lapse of more than a couple of days;
    `0` catches nothing up, so any missed session fails the run.

    Frozen and closed (T47, safety-reviewer): the `paper` guard below runs at
    construction, so an attribute assignment after the fact must be impossible too, and a
    misspelt broker fact (`quantity_decimals`) must fail rather than stay unset silently.
    `model_copy(update=...)` and `model_construct` still bypass any pydantic validator, so
    the trading client (T48) passes the literal `paper=True` and never forwards this field.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )

    historical_feed: Literal["sip", "iex"] = "sip"
    actions_process_lag_days: int = Field(default=90, ge=0)
    actions_backfill_floor: date = date(2020, 6, 1)
    registrant_quiet_days: int = Field(default=180, ge=1)
    # #843: how far before a rename's first cover-page row the new ticker may fill the
    # renamed company's bar hole (`ListingResolver.lead`). The 2016-2026 store's longest
    # liquid hole is GSX -> GOTU's 356 days; 0 turns the lead off.
    rename_lead_days: int = Field(default=400, ge=0)
    # #974: a security's first assigned equity span also resolves its ticker back to
    # the security's first session (`ListingResolver` `first_sessions`, built by
    # `repair.store_resolver`), bars and corporate actions. Owner decision on #979:
    # on by default; `false` is the kill switch and leaves every mapping as before.
    first_span_lead: bool = True
    # #1314: a security whose span of a ticker is not the ticker's latest open span is
    # asked for bars with Alpaca's `asof` this many days after that span starts, so a
    # reused symbol names the old company, not today's holder. 7 is the offset the
    # 2026-10-08 read-only check verified on all 511 reused-ticker segments (#1314).
    asof_offset_days: int = Field(default=7, ge=0)
    #: #1653: the collision probe of a `filing_name` span asks the sessions after the
    #: span's end through `master.transfer_window_sessions + 1` plus this many more, not
    #: on to the run's day: the first bar past the window shows a live other company, and
    #: the margin covers a thin symbol that trades only now and then.
    collision_probe_margin_sessions: int = Field(default=10, ge=0)
    #: #1618: an Alpaca `stock_dividends` row is stored as a `split` of its `rate` only
    #: when `1 < rate <` this. 25% is the exchange line above which a stock distribution
    #: is handled as a split; the 2016-2026 universe fetch's 140 stock rows run
    #: 1.001..1.5 and the five at or over 1.25 (CIG, DIT, MOVE, RJF, RUBI) are refused
    #: and named on the run row, never stored.
    stock_dividend_max_rate: float = Field(default=1.25, gt=1)
    #: #1618: a stock-dividend row whose security (or symbol) has a forward or reverse
    #: split within this many XNYS sessions of its ex-date is refused and named, so a
    #: split paid as a stock dividend (FBYD 2024-12-10, 1.2 in both categories) is
    #: adjusted once, by the split.
    stock_dividend_split_sessions: int = Field(default=5, ge=0)
    #: #1618: a same-date `cash_dividends` row whose amount is within this of a stock
    #: row's rate (and `1 < amount < stock_dividend_max_rate`) is that ratio repeated,
    #: not cash, and is skipped and counted. The 2016-2026 near echoes (CRESY, IRS, SBS)
    #: differ by at most 5e-6; #1617's 1.0062 vs 1.0063 stays cash.
    stock_dividend_echo_tolerance: float = Field(default=1e-5, ge=0)
    #: #1627: a stock dividend's whole-share growth and its `CIL` (cash in lieu of
    #: stock) activity can post on different sessions (Alpaca typically pays cash
    #: in lieu on the payable date), so reconciliation accepts a `CIL` dated from
    #: the ex-date through this many XNYS sessions after it, before the read goes
    #: inert and halts. Live (an `alpaca.` key), not frozen — the whole-share and
    #: cash checks still gate the booking.
    cash_in_lieu_max_lag_sessions: int = Field(default=10, ge=0)
    # Most symbols per bars or corporate-actions GET (#789). alpaca-py comma-joins the
    # list into the query string; an unbatched 9,500-symbol request got HTTP 414 from
    # Alpaca's nginx (2026-10-04 probe), while 2,956 symbols (~15,000 chars) worked.
    symbols_per_request: int = Field(default=1000, gt=0)
    # #1356: the free plan serves SIP only when a request's `end` is at least this many
    # minutes old ("the `end` parameter must be at least 15 minutes old to query SIP data
    # without a subscription", Alpaca FAQ, docs/research/2026-09-25-free-data-terms.md
    # claim 2b, S1). `alpaca_raw.daily_bars` caps a SIP request's end at now minus this;
    # 0 turns the cap off (a paid SIP subscription).
    sip_delay_minutes: int = Field(default=15, ge=0)
    #: #1572 (from #1550): how many XNYS sessions a daily ingest run may fetch that the
    #: store lacks before its expected session, so a day a failed run left out is caught
    #: up by the next one instead of leaving a `prices_daily` hole that empties the
    #: universe. Over it the daily run fails and names the `--backfill --since` command
    #: that clears the hole, which is the right tool for a longer lapse; `0` catches
    #: nothing up, so any missed session fails the run.
    max_catchup_sessions: int = Field(default=5, ge=0)
    #: Security ids (`<cik>`, `<cik>:<class>` or a #820 successor `<cik>@<day>`)
    #: the owner accepted as genuine long-gap relistings (#943: MiMedx, Nasdaq
    #: 2019-03 to OTC to Nasdaq 2020-11): `ListingResolver` does not apply rule 7's
    #: #847 stopped-line cut to their spans, which run on through a Form 25 to a
    #: later row of their ticker as before #905. Default empty. A malformed id
    #: refuses the config, so a typo never leaves a live name cut silently.
    accepted_relistings: list[str] = Field(default_factory=list)
    #: Master ticker -> Alpaca symbol for NYSE class shares whose cover page
    #: writes no dot (#1219: Brown-Forman, `BFB` is Alpaca's `BF.B`).
    #: Applied to NYSE listings only (`master_symbol`); the spelling is never
    #: guessed (CVNA, UAA, WLYB are real undotted symbols). Add a pair here
    #: when a NYSE class name has no bars; a value must be an Alpaca symbol.
    class_symbols: dict[str, str] = Field(
        default_factory=lambda: {
            "BFA": "BF.A",
            "BFB": "BF.B",
            "HVTA": "HVT.A",
            "WSOB": "WSO.B",
        }
    )
    # --- Phase 4 trading keys (docs/specs/paper-trading.md req 2, T47) ---
    # Guarded: the trading client is constructed with `paper=True` on every path
    # and a `false` here is refused, even from the environment (validator below).
    paper: bool = True
    # The EDGAR client pattern: pace, timeout and retries from config. The pace sits
    # under Alpaca's documented limit, which the recording task (T48b) confirms.
    trading_requests_per_minute: float = Field(default=150.0, gt=0)
    trading_request_timeout_seconds: float = Field(default=30.0, gt=0)
    trading_max_retries: int = Field(default=3, ge=0)
    #: Initial delay before retrying a failed trading request; doubles per retry.
    trading_retry_backoff_seconds: float = Field(default=1.0, gt=0)
    #: Maximum delay for each trading retry (default total: 1 + 2 + 4 = 7 seconds).
    trading_retry_backoff_max_seconds: float = Field(default=8.0, gt=0)
    # Broker facts set by the recording task (T48b, docs/research/
    # 2026-10-08-alpaca-paper-facts.md). Precision: paper accepted and filled
    # 9-decimal quantities (tests/fixtures/alpaca/paper/buy_fractional.json filled
    # 0.057436865, flatten.json sold 0.557436865), as Alpaca's fractional docs state.
    # Id length: Alpaca's documented 128-character limit; the recorded ids were 19
    # characters, so the limit itself is documented, not observed. `None` stays
    # allowed and the adapter (T48c) refuses to construct while either is `None`.
    quantity_decimals: int | None = Field(default=9, ge=0)
    client_order_id_max_length: int | None = Field(default=128, gt=0)

    @field_validator("accepted_relistings")
    @classmethod
    def _check_accepted_relistings(cls, value: list[str]) -> list[str]:
        for entry in value:
            check_security_id(entry, "alpaca.accepted_relistings")
        return value

    @field_validator("class_symbols")
    @classmethod
    def _check_class_symbols(cls, value: dict[str, str]) -> dict[str, str]:
        for ticker, symbol in value.items():
            if not re.fullmatch(r"[A-Z]+", ticker) or not re.fullmatch(r"[A-Z]+\.[A-Z]", symbol):
                raise ValueError(
                    f"alpaca.class_symbols: {ticker!r} -> {symbol!r} must map an undotted "
                    "upper-case ticker to a dotted class symbol (BFB -> BF.B)"
                )
        return value

    @field_validator("paper")
    @classmethod
    def _guard_paper(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("guarded setting; change only by ADR (Phase 6 live trading)")
        return value


class ExecutionConfig(_ClosedConfig):
    """Backtest/paper fill assumptions.

    `fill_price` stays `close` (T3): Alpaca's daily open is the first valid trade, not the
    official auction print, on every feed (documented), and fractional orders cannot use
    OPG and are priced off the NBBO, so they likely fill after the open (inference, #86).
    Neither live nor paper fills should be expected to match the bar open.
    """

    fill_price: Literal["close", "open"] = "close"


def _parse_id_at_day(entry: str, what: str) -> tuple[str, date]:
    """`"<security_id>@<YYYY-MM-DD>"` as `(security_id, day)`; `what` names the
    list in the error. Raises `ValueError` on any other shape."""
    security_id, sep, day = entry.rpartition("@")
    if not sep or not security_id:
        raise ValueError(f"{what} {entry!r} is not '<security_id>@<YYYY-MM-DD>'")
    return security_id, date.fromisoformat(day)


def parse_accepted_jump(entry: str) -> tuple[str, date]:
    """`"<security_id>@<YYYY-MM-DD>"` (an `universe.accepted_price_jumps` entry) as
    `(security_id, session)`. Raises `ValueError` on any other shape."""
    return _parse_id_at_day(entry, "accepted price jump")


def parse_accepted_shares_fact(entry: str) -> tuple[str, date]:
    """`"<security_id>@<YYYY-MM-DD>"` (an `universe.accepted_shares_facts` entry)
    as `(security_id, as_of_date)`. Raises `ValueError` on any other shape."""
    return _parse_id_at_day(entry, "accepted shares fact")


def parse_accepted_same_day_pair(entry: str) -> tuple[str, date]:
    """`"<security_id>@<YYYY-MM-DD>"` (an `universe.accepted_same_day_pairs`
    entry) as `(security_id, valid_from)`. Raises `ValueError` on any other
    shape."""
    return _parse_id_at_day(entry, "accepted same-day pair")


class UniverseConfig(_ClosedConfig):
    """ADR 0006 universe-construction thresholds, rules 1-8, in order."""

    security_types: list[str] = Field(default_factory=lambda: ["common"])
    exchanges: list[str] = Field(default_factory=lambda: ["NYSE", "NASDAQ", "NYSE_AMERICAN"])
    # Rule 3, a per-hypothesis choice; default none (ADR 0006 amendment 2026-10-11,
    # #1540). Frozen in each registration (guard a); shape-checked below.
    exclude_sic_ranges: tuple[tuple[int, int], ...] = ()
    min_price: float = 5
    # On since T3 (#84): historical bars come from the consolidated SIP feed, so the
    # 20-session median dollar volume is a real liquidity measure (IEX-only volume,
    # ~2.5% of the tape, would not have been).
    liquidity_rule_enabled: bool = True
    min_median_dollar_volume: float = 5_000_000
    liquidity_window: int = 20
    min_history_months: int = 12
    max_shares_age_days: int = 400
    # Shares plausibility (#845), part of rule 7: a shares fact whose value over the
    # security's last accepted earlier fact (moved by the splits known at T between
    # the two `as_of_date`s) is above `max_shares_ratio` or below its inverse is
    # rejected, unless the owner lists it in `accepted_shares_facts` as
    # "<security_id>@<YYYY-MM-DD>" (its `as_of_date`); rules 7 and 8 then use the last
    # accepted fact, still subject to `max_shares_age_days`. `health` lists them all.
    max_shares_ratio: float = Field(default=100, gt=1, allow_inf_nan=False)
    accepted_shares_facts: list[str] = Field(default_factory=list)
    top_n_by_cap: int = 1000
    # Rule 8's ranking (ADR 0023 D4, T212): `cap` is ADR 0006's market-cap rank (needs
    # rule 7's shares fact); `dollar_volume` ranks by median dollar volume from bars alone
    # and intersects with Alpaca's tradable assets as of T, for forward books. Frozen with
    # `universe.*`; a registered family keeps `cap` (`FROZEN_KEY_DEFAULTS`).
    rank_by: Literal["cap", "dollar_volume"] = "cap"
    # Price-quality gate (#787), part of rule 6: a close-to-close ratio between two
    # consecutive traded bars above `max_jump_ratio` or below `min_jump_ratio`, that no
    # split or dividend known at T explains, fails rule 6 while its session is in the
    # `min_history_months` window, unless the owner lists it in `accepted_price_jumps`
    # as "<security_id>@<YYYY-MM-DD>" (the jump's session). `health` lists every jump.
    max_jump_ratio: float = Field(default=2.5, gt=1, allow_inf_nan=False)
    min_jump_ratio: float = Field(default=0.4, gt=0, lt=1)
    accepted_price_jumps: list[str] = Field(default_factory=list)
    # Owner-accepted same-day listing pairs (#855), read only by `health`: a
    # same-start pair of different tickers that `non_overlapping_listings` would
    # fail (spec req 11, #822) passes when the owner lists it here as
    # "<security_id>@<YYYY-MM-DD>" (the pair's `valid_from`) after reviewing it.
    # `health` lists every accepted pair. Never changes universe membership.
    accepted_same_day_pairs: list[str] = Field(default_factory=list)

    @field_validator("accepted_price_jumps")
    @classmethod
    def _check_accepted_price_jumps(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_accepted_jump(entry)
        return value

    @field_validator("accepted_shares_facts")
    @classmethod
    def _check_accepted_shares_facts(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_accepted_shares_fact(entry)
        return value

    @field_validator("accepted_same_day_pairs")
    @classmethod
    def _check_accepted_same_day_pairs(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_accepted_same_day_pair(entry)
        return value

    @field_validator("exclude_sic_ranges")
    @classmethod
    def _check_exclude_sic_ranges(
        cls, value: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[int, int], ...]:
        for start, end in value:
            if start > end:
                raise ValueError(f"exclude_sic_ranges entry {start, end} has start > end")
        return value


class HealthConfig(_ClosedConfig):
    """Data-health thresholds (spec req 11), read only by `health`.

    `min_session_rows_share` (#1575) is the share of the previous session's
    `prices_daily` row count an XNYS session inside
    `universe.min_history_months` must hold, up to the latest stored session,
    for the `session_holes` rule to pass: a session with no row at all is
    always a hole, whatever this share says. The bar count moves with
    listings, delistings and ticker changes, so any positive value reports
    sessions that merely lost names; the default `0.0` reports only an empty
    session, which no listing change explains.
    """

    min_session_rows_share: float = Field(default=0.0, ge=0.0, le=1.0)


class BorrowConfig(_ClosedConfig):
    """The borrow-status snapshot (data-foundation spec req 19, amendment
    2026-10-11 #1506; ADR 0021): the daily `GET /v2/assets` ingest
    (`ingest --source alpaca_assets`) and its health block.

    `enabled` puts the snapshot last under `ingest --source all`; off, that
    run makes no assets request and writes nothing for it (an explicit
    `--source alpaca_assets` runs either way; T171d flips the default once the
    import and the first daily run have passed). `max_stale_sessions` is how
    many XNYS sessions the latest observation may trail the last completed
    session before `health` warns. `max_vanished_share` is the truncation
    guard: a snapshot that would mark more than this share of the stored
    present assets as vanished is refused with a failed run row and no table
    write. `raw_dir` (default `data/alpaca_assets` under the project root)
    keeps each daily payload gzipped in the recorder's file shape
    (`--import-dir` reads the same shape); empty turns the keep off.
    """

    enabled: bool = False
    max_stale_sessions: int = Field(default=2, ge=0)
    max_vanished_share: float = Field(default=0.05, ge=0.0, le=1.0)
    raw_dir: str = Field(default_factory=_default_alpaca_assets_dir)


_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class IndexCheckConfig(_ClosedConfig):
    """The known-answer index check (backtest spec amendment 2026-10-19, #1598).

    **Not frozen** and part of no hypothesis: the check is never a trial and no
    registered canonical set, fingerprint or hash includes these keys. `start_month`
    and `end_month` are the default window (the owner, 2026-10-12);
    `check_only_symbols` are benchmark series stored for the check alone, which no
    trial can read (`store.benchmarks.CHECK_ONLY_IDS`), so a symbol here may not also
    be in `benchmarks` (`Settings` refuses the overlap); `equal_benchmark` and
    `cap_benchmark` are the series the equal- and cap-weight books are compared with.
    `worst_months` months are listed per weighting, each with `names_per_month`
    names plus every name whose return is at least `extreme_name_return` (the first
    pass's 60 % screen).
    """

    start_month: str = Field(default="2019-08", pattern=_MONTH_PATTERN)
    end_month: str = Field(default="2026-09", pattern=_MONTH_PATTERN)
    check_only_symbols: list[str] = Field(default_factory=lambda: ["RSP"])
    equal_benchmark: str = Field(default="RSP", min_length=1)
    cap_benchmark: str = Field(default="SPY", min_length=1)
    worst_months: int = Field(default=10, ge=1)
    names_per_month: int = Field(default=5, ge=1)
    extreme_name_return: float = Field(default=0.60, gt=0.0)

    @model_validator(mode="after")
    def _validate_window(self) -> IndexCheckConfig:
        if self.end_month < self.start_month:
            raise ValueError(
                f"index_check.end_month ({self.end_month}) must not be before "
                f"start_month ({self.start_month})"
            )
        return self


class OpenfigiConfig(_ClosedConfig):
    """Current types check; unset limits use documented keyed/keyless limits."""

    enabled: bool = False
    requests_per_window: int | None = Field(default=None, ge=1)
    window_seconds: float | None = Field(default=None, gt=0)
    jobs_per_request: int | None = Field(default=None, ge=1)
    max_disagreements: int | None = Field(default=None, ge=0)

    def limits(self, *, keyed: bool) -> tuple[int, float, int]:
        """Mapping API's documented defaults, bounded even when configured."""
        return (
            min(self.requests_per_window or 25, 25),
            max(self.window_seconds or (6 if keyed else 60), 6 if keyed else 60),
            min(self.jobs_per_request or (100 if keyed else 5), 100 if keyed else 5),
        )


class AdjustConfig(_ClosedConfig):
    """Price-adjustment rules for `store.asof.adjusted_prices_as_of`.

    `max_prior_close_gap_sessions` is not in the spec's "Config keys" list;
    added for issue #72. A dividend's factor `1 - amount / prior_close`
    takes `prior_close` from the latest bar known at T before the ex-date.
    That bar counts only if it is among the N XNYS sessions immediately
    before the ex-date (1 = the prior session itself); otherwise the
    dividend is left unapplied and reported by `dropped_dividends_as_of`,
    rather than sized against a close from weeks or years earlier. Must be
    positive: zero would drop every dividend.

    `max_dividend_to_prior_close` (#841, not in the spec's list either) is a
    sanity bound on the amount: a dividend at or above this share of its
    prior close is bad source data (Alpaca attached Carlyle's $25 preferred
    issue price to CG's common on 2017-09-13, a $22 stock), so it is left
    unapplied and `dropped_dividends_as_of` reports it as
    `implausible_amount`. The default `1.0` drops exactly the amounts that
    would make the factor zero or negative; a lower value also drops
    implausibly large ones below the close. In `(0, 1]`: above `1` such an
    amount would reach `LN()` again.
    """

    max_prior_close_gap_sessions: int = Field(default=5, gt=0)
    max_dividend_to_prior_close: float = Field(default=1.0, gt=0, le=1.0)
    apply_spin_offs: bool = False


class GapConfig(_ClosedConfig):
    """Survivorship-gap reporting thresholds."""

    missing_tail_sessions: int = 5
    count_share_threshold: float = 0.05
    # ADR 0003 amendment #1199: a live listing dark longer than this many sessions,
    # its last bar before the window, counts as ended at that bar for the gap only.
    stale_listing_sessions: int = Field(default=63, ge=0)


# --- The coverage check against a named reference (spec amendment 2026-10-11, #1541) ---

#: `CIK:TICKER[:FROM[:TO]]`: a 10-digit CIK (the fixture store's `CIK`-prefixed
#: spelling is accepted too), an upper-case ticker, then optional ISO dates; an
#: empty `FROM` means "from the first sample".
_REFERENCE_CIK = re.compile(r"(?:CIK)?\d{10}")
_REFERENCE_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-/]*")


@dataclass(frozen=True)
class ReferenceName:
    """One `coverage.reference_names` entry: the sessions `first`..`last`
    (inclusive, `None` = unbounded) on which `cik` is expected in the universe
    under `ticker`."""

    cik: str
    ticker: str
    first: date | None
    last: date | None

    def covers(self, session: date) -> bool:
        """Whether the entry applies on `session`."""
        return (self.first is None or self.first <= session) and (
            self.last is None or session <= self.last
        )


def parse_reference_name(entry: str) -> ReferenceName:
    """`"CIK:TICKER[:FROM[:TO]]"` (a `coverage.reference_names` entry) as a
    `ReferenceName`. Raises `ValueError` on any other shape, on a `FROM` after
    its `TO`, and on an empty `TO` (leave it out instead)."""
    parts = entry.split(":")
    if not 2 <= len(parts) <= 4:
        raise ValueError(f"reference name {entry!r} is not 'CIK:TICKER[:FROM[:TO]]'")
    cik, ticker = parts[0], parts[1]
    if not _REFERENCE_CIK.fullmatch(cik):
        raise ValueError(f"reference name {entry!r}: CIK must be 10 digits")
    if not _REFERENCE_TICKER.fullmatch(ticker):
        raise ValueError(f"reference name {entry!r}: ticker must be upper-case")
    first = date.fromisoformat(parts[2]) if len(parts) > 2 and parts[2] else None
    if len(parts) == 4 and not parts[3]:
        raise ValueError(f"reference name {entry!r}: empty TO; leave it out")
    last = date.fromisoformat(parts[3]) if len(parts) == 4 else None
    if first is not None and last is not None and first > last:
        raise ValueError(f"reference name {entry!r}: FROM is after TO")
    return ReferenceName(cik=cik, ticker=ticker, first=first, last=last)


#: The default reference list (spec amendment 2026-10-11, #1541, item 3): large,
#: long-listed US common stocks, CIKs pinned from the SEC's
#: `company_tickers_exchange.json` fetched 2026-10-11, with the ticker and CIK
#: changes since `sample_from` split into windows from each filer's submissions:
#: Meta's `FB` until its 2022-06-09 rename to `META`; Exxon's holding-company
#: reorganisation (8-K12B 2026-07-01 under `0002115436`, the old registrant
#: `0000034088`'s 25-NSE 2026-07-02); P&G only until 2022-09-30 (spec open
#: question 4). The owner may edit it.
DEFAULT_REFERENCE_NAMES: tuple[str, ...] = (
    "0000320193:AAPL",
    "0000789019:MSFT",
    "0001018724:AMZN",
    "0001652044:GOOGL",
    "0001326801:FB::2022-06-08",
    "0001326801:META:2022-06-09",
    "0001067983:BRK.B",
    "0000200406:JNJ",
    "0000019617:JPM",
    "0000034088:XOM::2026-06-30",
    "0002115436:XOM:2026-07-01",
    "0000731766:UNH",
    "0001403161:V",
    "0000354950:HD",
    "0000093410:CVX",
    "0000310158:MRK",
    "0000021344:KO",
    "0000077476:PEP",
    "0000078003:PFE",
    "0000858877:CSCO",
    "0000104169:WMT",
    "0000070858:BAC",
    "0001744489:DIS",
    "0000097745:TMO",
    "0000051143:IBM",
    "0001551152:ABBV",
    "0000001800:ABT",
    "0000066740:MMM",
    "0000899051:ALL",
    "0000035527:FITB",
    "0000106640:WHR",
    "0000080424:PG::2022-09-30",
)


class CoverageConfig(_ClosedConfig):
    """`health`'s coverage check against a named reference (spec amendment
    2026-10-11, #1541, item 3; `health.coverage_reference`).

    `reference_names`: `CIK:TICKER[:FROM[:TO]]` entries, the sessions each
    applies on. `sample_months`: the calendar months (1-12) whose last XNYS
    session is sampled, from `sample_from` to the last completed session.
    `check_enabled`: off, the block is computed only on request (`health
    --coverage`, the page's button) and never fails `--check`; on, `health`
    computes it on every run and `--check` fails `coverage_reference_names`
    while a reference name is missing.

    `index_members` (#1682, T189b): the block also checks the S&P 500 answer
    key (plan T189's committed list, a check only, never a source) on each
    sample session. `index_max_missing`: unset (the default), the key's
    misses are reported and fail nothing; set, `health` computes the block on
    every run and `--check` fails `index_members_missing` while some sample
    session's misses exceed it (open question 30). Setting it with
    `index_members` off is refused: a gate that cannot run."""

    reference_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REFERENCE_NAMES), min_length=1
    )
    sample_months: list[int] = Field(default_factory=lambda: [6, 12], min_length=1)
    sample_from: date = date(2019, 12, 31)
    check_enabled: bool = False
    index_members: bool = True
    index_max_missing: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_index_gate(self) -> CoverageConfig:
        if self.index_max_missing is not None and not self.index_members:
            raise ValueError("coverage.index_max_missing needs coverage.index_members on")
        return self

    @property
    def computed_by_default(self) -> bool:
        """Whether `health` computes the block on every run: while
        `check_enabled` is on or `index_max_missing` is set."""
        return self.check_enabled or self.index_max_missing is not None

    @field_validator("reference_names")
    @classmethod
    def _check_reference_names(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_reference_name(entry)
        return value

    @field_validator("sample_months")
    @classmethod
    def _check_sample_months(cls, value: list[int]) -> list[int]:
        if any(not 1 <= month <= 12 for month in value):
            raise ValueError("sample_months must be calendar months 1-12")
        if len(set(value)) != len(value):
            raise ValueError("sample_months must not repeat a month")
        return value

    def references(self) -> tuple[ReferenceName, ...]:
        """`reference_names`, parsed."""
        return tuple(parse_reference_name(entry) for entry in self.reference_names)


# --- Phase 3: backtest engine and trial registry (docs/specs/backtest.md, T30) ---

# The enumerated hypothesis families (spec "Config keys"). Trials are counted per family
# for the deflated Sharpe, so a family cannot be invented by config: adding one is a
# reviewed code change here. `oracle` is refused on the real store (registry, T31b). A
# test pins `get_args(HypothesisFamily) == tuple(FAMILIES)` (ADR 0014 point 2).
HypothesisFamily = Literal["momentum", "oracle", "profitability", "combined"]

# Cadence and signal anchor (strategy-lab spec, "Config keys"; ADR 0012, superseding
# ADR 0006's Cadence section): frozen per hypothesis like the universe, beside
# `HypothesisFamily` since both are the enumerations a hypothesis file pins.
Cadence = Literal["month_end", "week_end", "daily"]
SignalAnchor = Literal["month_end", "offset"]

# Every Phase 3 section rejects unknown keys and non-finite floats. A hypothesis file pins
# `strategy.*` and `costs.*` (spec req 10), so a misspelt key must fail rather than fall back
# silently to the default, and a NaN or infinite value must fail rather than turn a
# result into NaN. Errors hide the input value by default (#1093), as `Settings` does:
# a caller that knows its input holds no secret (`execution/`'s frozen costs and risk)
# puts it back with `render_validation_errors`.
_PHASE3_MODEL_CONFIG = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class HypothesesConfig(BaseModel):
    """Hypothesis families, the unit for counting trials (spec domain rule 1).

    Non-empty and without duplicates, so the enumeration stays a plain list of names.
    """

    model_config = _PHASE3_MODEL_CONFIG

    families: list[HypothesisFamily] = Field(
        default_factory=lambda: list(_HYPOTHESIS_FAMILIES), min_length=1
    )

    @field_validator("families")
    @classmethod
    def _validate_families_unique(cls, value: list[HypothesisFamily]) -> list[HypothesisFamily]:
        if len(set(value)) != len(value):
            raise ValueError(f"families must not repeat, got {value}")
        return value


class StrategyConfig(BaseModel):
    """12-1 momentum signal and portfolio rules (spec req 3; handoff H1).

    `formation_months` must exceed `skip_months`, or the formation window is empty.
    `top_fraction` is a share of ranked names in (0, 1]. `turnover_top_fraction` is the
    share of members kept by B10's share-turnover screen before the rank, in (0, 1];
    the default 1.0 is no screen (backtest spec amendment #1358). It is a `momentum`
    rule and a post-registration own-section key (`backtest/frozen.py`
    `POST_REGISTRATION_OWN_KEYS`): a file that leaves it out freezes 1.0, never the live
    value.

    `weighting` is `equal` or `hold`; under `hold` an entry is sized at 1 /
    `hold_target_names` of equity and kept for `hold_sessions` sessions (backtest spec
    amendment #1731). `hold_sessions`, `hold_target_names` and `rebalance_band` are
    post-registration own-section keys too, inert under the default `equal` weighting.
    `hold_target_names` 0 means the count `top_fraction` selects; `rebalance_band` 0.0
    trades every non-zero drift, so every default reproduces today's behaviour.

    `signal_delay_sessions`, `placebo` and `placebo_seed` are the kill ladder's rung-4
    delay and rung-5 placebo (ADR 0023; backtest spec amendment #1820): post-registration
    own-section keys, inert at their defaults and read by the engine's plan step.

    `hold_top_fraction` is the `momentum` buy/hold buffer (backtest spec amendment #657):
    0.0 (the default) is no buffer; otherwise a name held at the last rebalance stays
    while it ranks inside this share, and `top_fraction <= value <= 1` is required. A
    value equal to `top_fraction` is normalised to 0.0, so the two spellings of "no
    buffer" are one hypothesis. It is a rule of `equal` weighting and a
    post-registration own-section key.
    """

    model_config = _PHASE3_MODEL_CONFIG

    formation_months: int = Field(default=12, gt=0)
    skip_months: int = Field(default=1, ge=0)
    top_fraction: float = Field(default=0.10, gt=0, le=1)
    weighting: Literal["equal", "hold"] = "equal"
    signal_total_return: bool = True
    turnover_top_fraction: float = Field(default=1.0, gt=0, le=1)
    hold_sessions: int = Field(default=1, gt=0)
    hold_target_names: int = Field(default=0, ge=0)
    rebalance_band: float = Field(default=0.0, ge=0, lt=1)
    signal_delay_sessions: int = Field(default=0, ge=0)
    placebo: Literal["none", "shuffle", "random_entry"] = "none"
    placebo_seed: int = Field(default=0, ge=0)
    hold_top_fraction: float = Field(default=0.0, ge=0, le=1)

    @field_validator("hold_top_fraction")
    @classmethod
    def _validate_hold_top_fraction(cls, value: float, info: ValidationInfo) -> float:
        top = info.data.get("top_fraction")
        if top is None or value == 0.0:
            return value
        if value == top:
            return 0.0
        if value < top:
            raise ValueError(
                f"strategy.hold_top_fraction ({value}) must be 0.0 or at least "
                f"strategy.top_fraction ({top})"
            )
        if info.data.get("weighting") == "hold":
            raise ValueError(
                f"strategy.hold_top_fraction ({value}) is a rule of equal weighting; "
                "strategy.weighting = 'hold' has its own exit rule"
            )
        return value

    @model_validator(mode="after")
    def _validate_formation_window(self) -> StrategyConfig:
        if self.formation_months <= self.skip_months:
            raise ValueError(
                f"formation_months ({self.formation_months}) must be greater than "
                f"skip_months ({self.skip_months})"
            )
        return self


class ProfitabilityConfig(BaseModel):
    """The `profitability` family's signal keys (backtest spec amendment #720, "Config
    keys"; hypothesis B3). Frozen per hypothesis and required in full by a
    `family = "profitability"` file; the momentum keys are not reused, so each family's
    section is complete on its own.

    `basis` is `gross` only until the pre-declared `cash` variant registers.
    `annual_period_days` is the inclusive day-length range an as-filed annual duration
    may have; `max_fact_age_days` bounds a fact's age at the rebalance; and
    `exclude_sic_ranges` is a signal-domain scope (inclusive ranges), separate from
    `universe.exclude_sic_ranges`. `top_fraction` is a share of scored names in (0, 1].
    """

    model_config = _PHASE3_MODEL_CONFIG

    basis: Literal["gross"] = "gross"
    annual_period_days: tuple[int, int] = (350, 380)
    max_fact_age_days: int = Field(default=548, gt=0)
    exclude_sic_ranges: tuple[tuple[int, int], ...] = ((6000, 6999),)
    include_derived: bool = True
    top_fraction: float = Field(default=0.10, gt=0, le=1)
    weighting: Literal["equal"] = "equal"

    @model_validator(mode="after")
    def _validate_ranges(self) -> ProfitabilityConfig:
        low, high = self.annual_period_days
        if not 0 < low <= high:
            raise ValueError(f"annual_period_days must be 0 < low <= high, got {low, high}")
        for start, end in self.exclude_sic_ranges:
            if start > end:
                raise ValueError(f"exclude_sic_ranges entry {start, end} has start > end")
        return self


class CombinedConfig(BaseModel):
    """The `combined` family's construction keys (hypothesis backlog B4; ADR 0014 point
    6, T130). The signal is a fixed equal-rank combination of momentum and profitability
    (never fitted on returns), so the family carries only the portfolio rules the engine
    reads for every family: `top_fraction`, a share of the combined scored names in
    (0, 1], and `weighting`. Nothing else; the sub-signals' own keys stay in `strategy`
    and `profitability`.
    """

    model_config = _PHASE3_MODEL_CONFIG

    top_fraction: float = Field(default=0.10, gt=0, le=1)
    weighting: Literal["equal"] = "equal"


class PortfolioConfig(_ClosedConfig):
    """The signed-book keys, the frozen section `portfolio` (backtest spec amendment
    #1872, item 1).

    `side` selects a long-only or a long/short book. `gross_exposure` is the sum of the
    absolute target weights, in `(0, 1.0]`: the upper bound is ADR 0024 item 4's
    placeholder, "so a backtest never tests what paper cannot hold". `net_exposure` is
    the sum of the signed target weights, in `[-gross, gross]` and equal to
    `gross_exposure` under `long_only`. `short_min_price` is the shortability proxy's
    price floor in dollars (item 3; Alpaca's own $5 maintenance line), and
    `short_cover_weight` the drift cover's short-value share of equity (item 7).

    The section is listed by no family and is a post-registration section under the
    2026-10-09 and #1792 rules, so every key is left out of the canonical set and the
    fingerprint at its default and no stored registration moves.
    """

    side: Literal["long_only", "long_short"] = "long_only"
    gross_exposure: float = Field(default=1.0, gt=0, le=1.0)
    net_exposure: float = Field(default=1.0, ge=-1.0, le=1.0)
    short_min_price: float = Field(default=5.0, gt=0)
    short_cover_weight: float = Field(default=0.10, gt=0, le=1.0)

    @model_validator(mode="after")
    def _validate_exposures(self) -> PortfolioConfig:
        """`net_exposure` in `[-gross, gross]`, equal to `gross_exposure` under
        `long_only` (spec item 1)."""
        if not (-self.gross_exposure <= self.net_exposure <= self.gross_exposure):
            raise ValueError(
                f"portfolio.net_exposure ({self.net_exposure}) must be in "
                f"[{-self.gross_exposure}, {self.gross_exposure}]"
            )
        if self.side == "long_only" and self.net_exposure != self.gross_exposure:
            raise ValueError(
                f"portfolio.net_exposure ({self.net_exposure}) must equal "
                f"portfolio.gross_exposure ({self.gross_exposure}) under 'long_only'"
            )
        return self


# --- The family registry (ADR 0014; T128) ------------------------------------
#
# One table from which every per-family table derives. Each entry pins:
#
# * `sections`: the family's own parameter section first, then any other family's
#   section its signal reads. Every listed section is frozen, required and hashed for
#   this family; sections it does not list are inert (never stored, overlaid or
#   fingerprinted) so that live changes to another family's keys do not move its
#   registration (`backtest/frozen.py`, `backtest/hypothesis.py`).
# * `params_model`: the pydantic model for the family's own section.
# * `parent`: `None` for a root family, else the family whose holdout gates this one.
# * `engine_ready` / `paper_ready`: the gates for `ENGINE_FAMILIES` and `PAPER_FAMILIES`.
# * `exclusion_reasons` / `count_names`: the reasons and count names the family's signal
#   may report, read by `backtest/strategies.py` and the generic `Plan` fields.
# * `benchmark`: an additive comparison benchmark (`backtest/results.py`); SPY and MTUM
#   stay mandatory for every family (charter, ADR 0005). `None` adds none.
# * `sweepable_keys`: the family's own `strategy.*`/`profitability.*` keys that may be
#   swept; `_DEFAULT_SWEEPABLE_KEYS` and `ALLOWED_AXIS_PREFIXES` derive from them.
#
# `oracle` is a test-only entry that reads momentum's `strategy` section (as today), so
# it is left out of `FAMILY_PARENTS` and `FAMILY_SIGNAL_SECTIONS` as it is today.


@dataclass(frozen=True)
class FamilySpec:
    """One hypothesis family's registration (ADR 0014 point 2)."""

    sections: tuple[str, ...]
    params_model: type[BaseModel]
    parent: HypothesisFamily | None
    engine_ready: bool
    paper_ready: bool
    exclusion_reasons: tuple[str, ...]
    count_names: tuple[str, ...]
    benchmark: str | None
    sweepable_keys: tuple[str, ...]


FAMILIES: dict[HypothesisFamily, FamilySpec] = {
    "momentum": FamilySpec(
        sections=("strategy",),
        params_model=StrategyConfig,
        parent=None,
        engine_ready=True,
        paper_ready=True,
        # `no_turnover` and its two counts are B10's screen, reported only when
        # `strategy.turnover_top_fraction` is below 1.0 (backtest spec amendment #1358).
        exclusion_reasons=("no_history", "no_turnover", "no_t0_bar"),
        # `n_retained` is the buffer's count, reported only when `strategy.hold_top_fraction`
        # is non-zero (backtest spec amendment #657).
        count_names=(
            "n_excluded_no_history",
            "n_screened",
            "n_excluded_no_turnover",
            "n_retained",
        ),
        benchmark="MTUM",
        sweepable_keys=(
            "strategy.formation_months",
            "strategy.skip_months",
            "strategy.top_fraction",
            "strategy.weighting",
            "strategy.signal_total_return",
            "strategy.turnover_top_fraction",
            "strategy.hold_top_fraction",
        ),
    ),
    "oracle": FamilySpec(
        sections=("strategy",),
        params_model=StrategyConfig,
        parent=None,
        engine_ready=True,
        paper_ready=True,
        exclusion_reasons=("no_history", "no_t0_bar"),
        count_names=("n_excluded_no_history",),
        benchmark="MTUM",
        sweepable_keys=(),
    ),
    "profitability": FamilySpec(
        sections=("profitability",),
        params_model=ProfitabilityConfig,
        parent=None,
        engine_ready=True,
        # Paper-ready by the owner's word in ADR 0017 part D (ADR 0014 point 5, T152).
        paper_ready=True,
        # The four reasons `gross_profitability` may report (`backtest/signals.py:225`).
        exclusion_reasons=("sector", "no_facts", "stale_facts", "malformed", "no_t0_bar"),
        # The six count names of spec amendment #720 (T85d).
        count_names=(
            "n_ranked",
            "n_excluded_no_facts",
            "n_excluded_stale_facts",
            "n_excluded_sector",
            "n_excluded_malformed",
            "n_derived",
        ),
        benchmark="MTUM",
        sweepable_keys=(),
    ),
    "combined": FamilySpec(
        # Its own construction section first, then both sub-signals' sections, so a B4
        # registration freezes, requires and hashes `strategy.*` and `profitability.*`
        # and reads neither live (ADR 0014 points 2 and 6).
        sections=("combined", "strategy", "profitability"),
        params_model=CombinedConfig,
        # Owner decision 2026-10-06 on #1074: `combined` is a child of `momentum` in
        # `FAMILY_PARENTS` (ADR 0014 open question 2).
        parent="momentum",
        engine_ready=True,
        # Paper-ready by the owner's word in ADR 0017 part D (ADR 0014 point 5, T152).
        paper_ready=True,
        # Momentum's reason, profitability's four, then the combination's own reason,
        # in the precedence `combined_rank` reports and pairwise disjoint (T130).
        exclusion_reasons=(
            "no_history",
            "sector",
            "no_facts",
            "stale_facts",
            "malformed",
            "one_signal_only",
            "no_t0_bar",
        ),
        # Both sub-signals' counts plus `n_combined` (T130).
        count_names=(
            "n_excluded_no_history",
            "n_ranked",
            "n_excluded_no_facts",
            "n_excluded_stale_facts",
            "n_excluded_sector",
            "n_excluded_malformed",
            "n_derived",
            "n_combined",
        ),
        benchmark="MTUM",
        sweepable_keys=(),
    ),
}


# The hypothesis family enumeration tuple derived from `FAMILIES`. The `HypothesisFamily`
# literal above must agree with it; a test pins the equality (mypy strict needs the
# literal explicit).
_HYPOTHESIS_FAMILIES: tuple[HypothesisFamily, ...] = tuple(FAMILIES)

# A family's lineage (strategy-lab spec open question 11): every non-`oracle` family has
# an entry here, either a parent family or `None` for a **root** family whose signal is
# unrelated to momentum's. `oracle` is test-only, no entry (as today). A child's holdout
# may only start after its parent's `holdout.end` and only once the parent has spent its
# holdout or reached its spend cap (`store/registry.py`).
FAMILY_PARENTS: dict[HypothesisFamily, HypothesisFamily | None] = {
    family: spec.parent for family, spec in FAMILIES.items() if family != "oracle"
}

# Each family's own signal section (first of its listed sections). `oracle` reads
# momentum's `strategy` section, so it is left out as today (`backtest/frozen.py`).
FAMILY_SIGNAL_SECTIONS: dict[HypothesisFamily, str] = {
    family: spec.sections[0] for family, spec in FAMILIES.items() if family != "oracle"
}

# The families the engine can run today (#1053, folded into T85 by owner decision
# 2026-10-06): `backtest run` enforces `ENGINE_FAMILIES`. Paper keeps its own gate,
# `PAPER_FAMILIES`, enforced by `paper start` and paper planning; a family joins it only
# by the owner's word (ADR 0014 point 5; `profitability` and `combined` by ADR 0017
# part D, T152).
ENGINE_FAMILIES: tuple[HypothesisFamily, ...] = tuple(
    family for family, spec in FAMILIES.items() if spec.engine_ready
)
PAPER_FAMILIES: tuple[HypothesisFamily, ...] = tuple(
    family for family, spec in FAMILIES.items() if spec.paper_ready
)


class CostsConfig(BaseModel):
    """Per-trade cost model (spec req 6).

    `per_side_bps` is a placeholder until Phase 4 paper fills recalibrate it (spec open
    question 3); the sensitivity ladder's 100 bp is about the per-dollar cost Novy-Marx &
    Velikov (2016) imply for 12-1 momentum, via G1. Commissions are zero at Alpaca; the
    keys exist for other brokers. Every value is non-negative: a negative cost would pay
    the strategy to trade.

    Amendment 2026-10-18 (#1731) "Cost terms": `spread_bps` is a per-side level added to
    `per_side_bps`, `impact_coeff` scales `sqrt(notional / ADV)` market impact, and
    `adv_sessions` is the window (in XNYS sessions) over which ADV is measured. Both new
    rate keys are `ge=0`; `adv_sessions` must hold at least one session.

    Amendment 2026-10-19 (#1872) item 6 (T221): `borrow_bps_per_year` is the declared
    headline borrow fee on a short book's value, in basis points a year (default 0.0, so
    a long-only book pays none), and `borrow_sensitivity_bps` lists further rates, each
    run as one more reported-only book. Both are non-negative; a `long_short` file is
    refused `refused_borrow_rate` at registration unless its headline is above 0 and
    {0, 30, 300} is inside the headline and the sensitivities together. The engine reads
    them at T222.
    """

    model_config = _PHASE3_MODEL_CONFIG

    per_side_bps: float = Field(default=15.0, ge=0)
    spread_bps: float = Field(default=0.0, ge=0)
    impact_coeff: float = Field(default=0.0, ge=0)
    commission_per_share: float = Field(default=0.0, ge=0)
    commission_per_order: float = Field(default=0.0, ge=0)
    adv_sessions: int = Field(default=20, ge=1)
    sensitivity_per_side_bps: list[Annotated[float, Field(ge=0)]] = Field(
        default_factory=lambda: [0.0, 30.0, 60.0, 100.0]
    )
    borrow_bps_per_year: float = Field(default=0.0, ge=0)
    borrow_sensitivity_bps: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)


class HoldoutConfig(BaseModel):
    """The locked holdout window. No defaults: every hypothesis file names both dates,
    and a run takes them from the frozen hypothesis, never from live `Settings` (spec
    req 10). Deliberately absent from `.env.example`.
    """

    model_config = _PHASE3_MODEL_CONFIG

    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _validate_window_order(self) -> HoldoutConfig:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"holdout end ({self.end}) is before its start ({self.start})")
        return self


class BacktestConfig(BaseModel):
    """Engine settings (spec reqs 4 and 5).

    `initial_capital` is scale-free with fractional shares but sizes per-order
    commissions. `delisting_exit=last_close` is the ADR 0004 oracle convention.
    `stale_exit_sessions` mirrors `gap.missing_tail_sessions` (spec open question 4).

    `cash_rate` is `0.0` or `store`: under `store` cash accrues the store's risk-free
    series per session (backtest spec amendment #1731), and a run pairs it with
    `metrics.risk_free_source = "store"`. A numeric value other than 0 is refused: a
    made-up rate is not a point-in-time fact.
    """

    model_config = _PHASE3_MODEL_CONFIG

    initial_capital: float = Field(default=100_000.0, gt=0)
    cash_rate: float | Literal["store"] = 0.0
    delisting_exit: Literal["last_close"] = "last_close"
    stale_exit_sessions: int = Field(default=5, gt=0)

    @field_validator("cash_rate")
    @classmethod
    def _validate_cash_rate(cls, value: float | Literal["store"]) -> float | Literal["store"]:
        if isinstance(value, str):
            return value
        if value != 0:
            raise ValueError(
                f"backtest.cash_rate must be 0.0 or 'store', got {value}; "
                "a made-up rate is not a point-in-time fact"
            )
        return value


class MetricsConfig(BaseModel):
    """Metric settings (spec reqs 7 and 15).

    No `periods_per_year`: `PERIODS_PER_YEAR` (12, 52, 252 by cadence; ADR 0012,
    `backtest/schedule.py`, T94) is a derived constant table, not config, and
    `MONTHS_PER_YEAR = 12` is its `month_end` entry. `red_flag_excess_cagr_pp` marks a
    trial for a look-ahead and cost audit; it is a reported flag, never a gate (spec
    open question 6).
    """

    model_config = _PHASE3_MODEL_CONFIG

    risk_free_rate: float = 0.0
    #: Where the per-period risk-free rate comes from (backtest spec amendment
    #: 2026-10-11, #1498): `constant` reads `risk_free_rate`; `store` reads the `rates`
    #: table. The live default flips to `store` with T170d; the frozen-key table entry
    #: stays `constant`.
    risk_free_source: Literal["constant", "store"] = "constant"
    #: The `rates.series` id a `store` trial reads.
    risk_free_series: str = "DTB3"
    #: Quote basis of the stored rate: `discount` (DTB3's) or `yield`.
    risk_free_basis: Literal["discount", "yield"] = "discount"
    red_flag_excess_cagr_pp: float = Field(default=3.0, ge=0)


# --- Strategy lab: schedule and lab sections (docs/specs/strategy-lab.md, T93) ---


def _settings_has_key(dotted_key: str) -> bool:
    """`True` iff `dotted_key` (e.g. `"strategy.formation_months"`) names a real field on
    `Settings`, walked through its nested section models. Used only to validate
    `lab.sweepable_keys` entries against typos: a key `Settings` lacks must be refused,
    never silently accepted as an axis that can never vary.

    Requires a `section.field` shape: a bare top-level name (no dot) is always
    rejected, whether or not it happens to name a `Settings` field, since every real
    sweepable key is one `strategy.*`/`schedule.*` field inside a section, never a
    whole section (`"universe"`) or a top-level scalar such as a secret
    (`"alpaca_api_key"`) (reviewer findings on #952, both reviewer passes)."""
    section, sep, rest = dotted_key.partition(".")
    if not sep or not rest:
        return False
    field = Settings.model_fields.get(section)
    if field is None:
        return False
    model = field.annotation
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return False
    return rest in model.model_fields


class ScheduleConfig(BaseModel):
    """Cadence and signal anchor (strategy-lab spec "Config keys"; ADR 0012, superseding
    ADR 0006's Cadence section). Frozen per hypothesis like the universe
    (`backtest/frozen.py`, T96): once a hypothesis registers, these never change for it.
    `paper start` refuses anything but `rebalance_cadence = month_end` (paper-trading
    spec req 14); only the backtest path exercises `week_end` and `daily`.
    """

    model_config = _PHASE3_MODEL_CONFIG

    rebalance_cadence: Cadence = "month_end"
    signal_anchor: SignalAnchor = "month_end"


# Forbidden `lab.sweepable_keys` prefixes (strategy-lab spec req 1, "Config keys"):
# costs are sensitivities inside a trial (backtest spec req 6), the universe is not
# tuned on P&L (ADR 0006 guard a), `execution.fill_price` is a convention (ADR 0006), and
# the rest are data or accounting rules a hypothesis never varies. Pinned by value in
# tests/test_config.py; the `Settings` validator below refuses any `lab.sweepable_keys`
# entry under one of these, kept as a second, belt-and-braces check alongside
# `ALLOWED_AXIS_PREFIXES` below (Amendment 2026-10-05 (#952, owner)): were a forbidden
# prefix ever also an allowed one, this still refuses it.
FORBIDDEN_AXIS_PREFIXES: tuple[str, ...] = (
    "costs.",
    "universe.",
    "holdout.",
    "gap.",
    "adjust.",
    "master.",
    "metrics.",
    "backtest.",
    "benchmarks",
    "alpaca.",
    "execution.",
)

# Amendment 2026-10-05 (#952, owner): sweepable keys are allow-listed to `strategy.*`
# and `schedule.*` rather than relying on `FORBIDDEN_AXIS_PREFIXES` alone (a deny-list
# that, unamended, let `risk.*`, `paper.*` and `alerts.*` through, #955). A new axis
# family needs a reviewed code change here, because `lab.sweepable_keys` itself comes
# from config, which is set from the unreviewed `.env`. Derived from `FAMILIES`
# (ADR 0014 point 2): schedule plus every family's own signal section with sweepable
# keys. Pinned by value in tests/test_config.py.
ALLOWED_AXIS_PREFIXES: tuple[str, ...] = (
    *sorted({f"{spec.sections[0]}." for spec in FAMILIES.values() if spec.sweepable_keys}),
    "schedule.",
)

_DEFAULT_SWEEPABLE_KEYS: tuple[str, ...] = (
    *(key for spec in FAMILIES.values() for key in spec.sweepable_keys),
    "schedule.rebalance_cadence",
    "schedule.signal_anchor",
)

# Placeholders (plan approach choice 4; open question 3 on #933, not yet answered): the
# measured seconds a single backtest variant takes at each cadence. Until T114's
# measurement run, these are scaled only by how many more rebalances a faster cadence
# runs per year relative to `month_end` (about 4 ISO weeks and 21 XNYS sessions per
# month), never measured; `backtest/quiet.py`'s `seconds_per_variant` (T106) falls back
# to this table when no measured figure exists.
_DEFAULT_SECONDS_PER_VARIANT: dict[Cadence, float] = {
    "month_end": 1.0,
    "week_end": 4.0,
    "daily": 20.0,
}


class LabConfig(BaseModel):
    """Strategy-lab sweep rules (strategy-lab spec "Config keys").

    `sweepable_keys` names every `Settings` key a sweep file's `[grid]` may vary
    (validated against `ALLOWED_AXIS_PREFIXES`, `FORBIDDEN_AXIS_PREFIXES` and against
    `Settings` itself on the `Settings` model, since that check needs the whole model);
    `axis_lattice` is a step
    per continuous sweepable key (an integer axis needs none) and its keys must be a
    subset of `sweepable_keys`. `promotion_min_dsr_excess` and `min_sharpe_variance_annual`
    are the promotion floor and the V floor (spec open question 4); `max_variants_per_sweep`
    bounds one sweep (open question 5); `max_family_holdout_spends` and
    `max_family_promotions` are copied into `family_rules` at a family's first
    registration (open question 10). `sweep_time_budget_minutes` and the
    `registry_size_*` guards are placeholders the measurement task (T114) replaces.
    `quiet_intervals`, `quiet_weekdays` and `quiet_timezone` describe the ingest plist's
    window and days; `paper_run_lead_minutes` is how long before the submit window the
    paper plist fires. `seconds_per_variant_default` is drafted here as config rather
    than a code constant (code standards; approach choice 4); it is not in the spec's
    "Config keys" list.
    """

    model_config = _PHASE3_MODEL_CONFIG

    sweepable_keys: list[str] = Field(default_factory=lambda: list(_DEFAULT_SWEEPABLE_KEYS))
    max_variants_per_sweep: int = Field(default=100, gt=0)
    max_promotions_per_sweep: int = Field(default=1, gt=0)
    max_family_promotions: int = Field(default=2, gt=0)
    promotion_min_dsr_excess: float = Field(default=0.5, ge=0)
    min_sharpe_variance_annual: float = Field(default=0.04, ge=0)
    max_failures_per_variant: int = Field(default=2, ge=0)
    max_family_holdout_spends: int = Field(default=3, gt=0)
    sweep_time_budget_minutes: float = Field(default=480.0, gt=0)
    sweep_detail_level: Literal["full", "summary"] = "summary"
    axis_lattice: dict[str, float] = Field(default_factory=lambda: {"strategy.top_fraction": 0.01})
    quiet_intervals: list[tuple[str, str]] = Field(default_factory=lambda: [("16:00", "21:00")])
    quiet_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    quiet_timezone: str = "America/New_York"
    paper_run_lead_minutes: int = Field(default=30, ge=0)
    registry_size_warn_gb: float = Field(default=20.0, gt=0)
    registry_size_refuse_gb: float = Field(default=50.0, gt=0)
    seconds_per_variant_default: dict[Cadence, float] = Field(
        default_factory=lambda: dict(_DEFAULT_SECONDS_PER_VARIANT)
    )

    @field_validator("quiet_timezone")
    @classmethod
    def _validate_quiet_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"lab.quiet_timezone {value!r} is not a valid IANA timezone") from exc
        return value

    @field_validator("quiet_weekdays")
    @classmethod
    def _validate_quiet_weekdays(cls, value: list[int]) -> list[int]:
        bad = [day for day in value if day < 0 or day > 6]
        if bad:
            raise ValueError(f"lab.quiet_weekdays entries must be 0-6 (Monday-Sunday), got {bad}")
        return value

    @field_validator("quiet_intervals")
    @classmethod
    def _validate_quiet_intervals(cls, value: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Refuse a pair whose times are not zero-padded `HH:MM` or whose end
        is not after its start."""
        for start_text, end_text in value:
            for text in (start_text, end_text):
                if not _HHMM_RE.match(text):
                    raise ValueError(
                        f"lab.quiet_intervals time {text!r} is not HH:MM (00:00 to 23:59)"
                    )
            if end_text <= start_text:
                raise ValueError(
                    f"lab.quiet_intervals pair [{start_text!r}, {end_text!r}] must end after it "
                    "starts on the same day"
                )
        return value

    @field_validator("seconds_per_variant_default")
    @classmethod
    def _validate_seconds_per_variant_default(
        cls, value: dict[Cadence, float]
    ) -> dict[Cadence, float]:
        missing = sorted(set(get_args(Cadence)) - set(value))
        if missing:
            raise ValueError(f"lab.seconds_per_variant_default is missing an entry for {missing}")
        return value

    @model_validator(mode="after")
    def _validate_axis_lattice_keys_are_sweepable(self) -> LabConfig:
        unsweepable = sorted(set(self.axis_lattice) - set(self.sweepable_keys))
        if unsweepable:
            raise ValueError(f"lab.axis_lattice key(s) {unsweepable} are not in lab.sweepable_keys")
        return self


# --- Phase 4: paper trading and journal (docs/specs/paper-trading.md, ADR 0010, T47) ---

# The `paper.*` keys req 14 freezes (the sixth, `max_stale_mark_sessions`, #1763, reads 0
# for a window frozen before it existed) into the window at `paper start`, beside the
# whole `risk.*` section; every other `paper.*` key is read at run time. `min_rebalances`
# is frozen as its window's cadence's entry of the per-cadence table (ADR 0017 part C).
FROZEN_PAPER_KEYS: tuple[str, ...] = (
    "tracking_k",
    "min_rebalances",
    "tracking_rule",
    "max_catch_up_sessions",
    "min_override_reason_chars",
    "max_stale_mark_sessions",
)

# The `execution.*` keys req 14 also freezes into the window at `paper start` (#366
# Q20, owner): the tracking trial's fill-price convention must be read from the
# window's `frozen_json`, never live `Settings`, since a config edit mid-window must
# not silently change what `paper report`'s fill-timing and residue terms compare
# paper fills against. One key today; `execution/window.py`'s `_frozen_params` writes
# it (#526).
FROZEN_EXECUTION_KEYS: tuple[str, ...] = ("fill_price",)

# The `costs.*` keys req 14 also freezes into the window at `paper start` (#534,
# owner): the wrapper sizes buys and checks the cash rule with the window's frozen
# costs, never live `Settings`, as #366 Q20 does for `execution.fill_price`. Exactly
# the keys `BuyCosts` is built from (`per_side_bps` and the two commissions
# `Commissions.from_config` reads); `sensitivity_per_side_bps` is a backtest key.
FROZEN_COSTS_KEYS: tuple[str, ...] = (
    "per_side_bps",
    "commission_per_share",
    "commission_per_order",
)

AlertChannel = Literal["store", "macos", "email"]
_DEFAULT_ALERT_CHANNELS: tuple[AlertChannel, ...] = ("store", "macos")
# The `email` channel's required `Settings` fields, paired with the env var name a
# missing one is reported as (#544): `Settings._validate_alert_channel_is_usable`
# names exactly these, never a value. `alert_email_from` is the optional sender
# (#404) and is not required.
_EMAIL_REQUIRED_SETTINGS: tuple[tuple[str, str], ...] = (
    ("alert_smtp_host", "ALERT_SMTP_HOST"),
    ("alert_smtp_user", "ALERT_SMTP_USER"),
    ("alert_smtp_password", "ALERT_SMTP_PASSWORD"),
    ("alert_email_to", "ALERT_EMAIL_TO"),
)


def _is_set(value: str | SecretStr | None) -> bool:
    """`True` iff `value` is a non-blank setting: a `SecretStr`'s stripped value,
    or a plain string's, never the secret itself."""
    if value is None:
        return False
    text = value.get_secret_value() if isinstance(value, SecretStr) else value
    return bool(text.strip())


#: The book whose paper key pair is `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET`
#: (ADR 0017 B.1): H1's book, `store.schema.DEFAULT_BOOK_ID` and `paper.book_id`'s default.
MAIN_BOOK_ID = "main"
#: The settings field holding every other book's pair, and so the head of its variables.
_BOOK_PAIRS_FIELD = "alpaca_paper_books"


class AlpacaPaperKeyPair(BaseModel):
    """One book's Alpaca paper key pair (ADR 0017 B.1, plan T153), read from
    `ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY` / `ALPACA_PAPER_BOOKS__<TOKEN>__API_SECRET`.
    Both halves are optional here so a half-set pair never stops `Settings` loading
    (and with it every other book's run); the adapter refuses such a book alone
    (`no_credentials`). A mistyped half is ignored the same way, never echoed."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None


def paper_key_variable_names(book_id: str) -> tuple[str, str]:
    """The environment variables holding `book_id`'s paper key pair (ADR 0017 B.1,
    plan T153): `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` for book `main`
    (unchanged), and `ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY`/`..._API_SECRET` for every
    other book, `<TOKEN>` the book token upper-cased. Names only, never a value."""
    if book_id == MAIN_BOOK_ID:
        return "ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET"
    head = f"{_BOOK_PAIRS_FIELD.upper()}__{book_id.upper()}__"
    return f"{head}API_KEY", f"{head}API_SECRET"


class SmallAccountConfig(BaseModel):
    """`risk.small_account` (spec amendment 2026-10-18 item 8; ADR 0023 D6; plan T206).

    `enabled` turns the small-account profile on: every buy is a fractional notional
    order rounded up to `risk.min_order_notional`, and the limits judge the rounded
    figure. ADR 0010's limits are unchanged. Off by default, so every window frozen
    before the section existed is unchanged. Unknown keys and non-booleans are refused.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    enabled: StrictBool = False

    @field_validator("enabled", mode="before")
    @classmethod
    def _bool_or_its_word(cls, value: object) -> object:
        """A real bool, or the strings "true"/"false" in any case (the env-var form);
        everything else ("yes", "1", "on", ints) is left for the strict check to refuse."""
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        return value


class ShortRiskConfig(BaseModel):
    """The three short-side `risk.*` keys (ADR 0010's amendment of 2026-10-19; paper
    spec amendment #1872 items 7 and 12; plan T231). `RiskConfig` carries them like
    `small_account`, outside its `model_fields`, so a window frozen before T231 still
    loads at these defaults. The fractions are shares of equity in [0, 1]; non-finite
    values and unknown keys are refused."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    max_short_weight: float = Field(default=0.05, ge=0, le=1)
    net_exposure_band: float = Field(default=0.10, ge=0, le=1)
    min_short_equity: float = Field(default=2000.0, ge=0)


class RiskConfig(BaseModel):
    """The Phase 4 risk rules, ADR 0010 point 1: every limit is a named key here, the
    whole section frozen into the paper window at `paper start` and read from the
    window afterwards (spec req 14), never from live `Settings`.

    Unknown keys are refused (a typo must fail, since the section is frozen by name) and
    non-finite floats too. Every fraction is a share in [0, 1]: above 1 would be leverage
    (`max_gross_exposure`, the charter's rule) or a meaningless threshold.
    `max_fill_lag_sessions` is at least 1 (spec req 8). Defaults are the spec's
    reasoning for H1 at paper scale, not measurements; `min_order_notional` and the two
    reconciliation tolerances were kept by the recording task (T48b, 2026-10-08,
    docs/research/2026-10-08-alpaca-paper-facts.md): a $5 notional buy and a $0.98
    whole-share sell were accepted (`min_order_notional`, Alpaca's documented $1);
    position quantities equalled the summed 9-decimal fills exactly
    (`reconcile_quantity_tolerance`); `cash` is reported in cents and moved within
    $0.0022 of the six fills' exact value. `reconcile_cash_tolerance` was widened from
    0.01 to 1.26 by ADR 0010's amendment of 2026-10-10 (#1290, owner's option (b));
    T183's amendment of 2026-10-12 (#1583) reverts it to 0.01 and turns it into the
    strict half-`cash` bound `reconcile.py` scales by the ledger's `fills_since_base`
    (`risk.reconcile_cash_tolerance` x (1 + fills / 2), in `Decimal`, inclusive): the
    per-fill cent rounding the #1290 band absorbed whole-dollar is instead allowed a
    cent per fill per side, so the #1466 relation to `max_orders_per_run` is removed.
    The value is a fixed default, not derived at run time; a window keeps the value it
    froze at `paper start`. After the first `paper start` any change to a default is a
    new ADR (ADR 0010 point 5).

    `small_account` (T206, `SmallAccountConfig`) is frozen with the section as the key
    `risk.small_account`, but it is deliberately not a pydantic field: a window frozen
    before T206 has no such key, and `reconcile_run.frozen_risk` refuses a `frozen_json`
    that lacks any of `RiskConfig.model_fields`. It is read from the input by a wrap
    validator, defaults to off when absent, and is written back by the serializer, so
    `model_dump()` (what `paper start` freezes) carries it.
    """

    model_config = _PHASE3_MODEL_CONFIG

    _small_account: SmallAccountConfig = PrivateAttr(default_factory=SmallAccountConfig)
    _short: ShortRiskConfig = PrivateAttr(default_factory=ShortRiskConfig)

    @model_validator(mode="wrap")
    @classmethod
    def _read_small_account(cls, data: Any, handler: Any) -> Any:
        """Split `small_account` and the three short keys (T231) off the input
        before the field validation sees them."""
        if not isinstance(data, dict):
            return handler(data)
        short_keys = {k: data[k] for k in ShortRiskConfig.model_fields if k in data}
        if "small_account" not in data and not short_keys:
            return handler(data)
        rest = {k: v for k, v in data.items() if k != "small_account" and k not in short_keys}
        section = (
            SmallAccountConfig.model_validate(data["small_account"])
            if "small_account" in data
            else SmallAccountConfig()
        )
        short = ShortRiskConfig.model_validate(short_keys)
        built = handler(rest)
        built._small_account = section
        built._short = short
        return built

    @model_serializer(mode="wrap")
    def _write_small_account(self, handler: SerializerFunctionWrapHandler) -> Any:
        """The dump carries `small_account` and the short keys, so `paper start`
        freezes them."""
        dumped = handler(self)
        dumped["small_account"] = self._small_account.model_dump()
        dumped.update(self._short.model_dump())
        return dumped

    @property
    def max_short_weight(self) -> float:
        """A short name's most held value over equity (ADR 0010 amendment 2026-10-19)."""
        return self._short.max_short_weight

    @property
    def net_exposure_band(self) -> float:
        """How far the net after a rebalance may sit from the window's `net_exposure`."""
        return self._short.net_exposure_band

    @property
    def min_short_equity(self) -> float:
        """The account equity below which a short open halts."""
        return self._short.min_short_equity

    @property
    def small_account(self) -> SmallAccountConfig:
        """The small-account profile (off unless the window froze it on)."""
        return self._small_account

    max_position_weight: float = Field(default=0.05, ge=0, le=1)
    max_order_notional_fraction: float = Field(default=0.05, ge=0, le=1)
    max_gross_exposure: float = Field(default=1.0, ge=0, le=1)
    max_orders_per_run: int = Field(default=250, gt=0)
    max_skips_per_run: int = Field(default=10, ge=0)
    # T202 (ADR 0023 D2): the skip cap is max(max_skips_per_run, floor(share x decisions)).
    max_skip_share: float = Field(default=0.10, ge=0, le=0.25)
    max_rejections_per_run: int = Field(default=5, ge=0)
    max_drawdown: float = Field(default=0.30, ge=0, le=1)
    min_order_notional: float = Field(default=1.0, ge=0)
    whole_share_price_buffer: float = Field(default=0.02, ge=0, le=1)
    max_unspent_cash_fraction: float = Field(default=0.05, ge=0, le=1)
    max_fill_lag_sessions: int = Field(default=1, ge=1)
    clock_max_sessions_late: int = Field(default=1, ge=0)
    max_broker_clock_skew_seconds: float = Field(default=60.0, ge=0)
    reconcile_quantity_tolerance: float = Field(default=1e-6, ge=0)
    reconcile_cash_tolerance: float = Field(default=0.01, ge=0)
    # #1627: the relative tolerance the reconciliation books a stock dividend's
    # `CIL` (cash in lieu of stock) cash activity against the expected cash
    # (fractional-remainder shares x the reference price). The broker values cash
    # in lieu on the ex-date, below the pre-ex close the reference price carries,
    # so a ~10% relative gap passes; a larger one halts the book, as any mismatch
    # does. A fraction of the expected cash, in [0, 1].
    cash_in_lieu_tolerance: float = Field(default=0.10, ge=0, le=1)
    # #1762 (paper spec amendment 2026-10-19 items 4 and 10; T249): the trading
    # sessions after a merger's `payable_date` (else its `effective_date`) that its
    # cash may still post; the first reconciliation after them checks the due once.
    # A window frozen before T249 lacks the key and `frozen_risk` reads it at 5.
    reconcile_merger_lag_sessions: int = Field(default=5, ge=0)


# `paper.min_rebalances` per cadence (ADR 0017 part C and open question 2, the paper
# spec's amendment of 2026-10-09): six months, a quarter of weeks, a quarter of sessions.
# `paper start` freezes the window's cadence's entry as the scalar `paper.min_rebalances`
# in `frozen_json`, so every reader of the frozen key reads one number.
_DEFAULT_MIN_REBALANCES: dict[Cadence, int] = {
    "month_end": 6,
    "week_end": 13,
    "daily": 63,
}


class PaperConfig(BaseModel):
    """Paper-window and tracking-run settings (spec reqs 3, 7, 8, 10, 14).

    `FROZEN_PAPER_KEYS` are frozen at `paper start`; the rest are run-time keys.
    `max_stale_mark_sessions` (#1763, owner decision) is how many consecutive sessions a
    tradable held name missing its S-1 bar is marked at its last close, with an alert,
    before the run fails; 0 is the old fail-at-once rule, which an already-open window
    keeps (its `frozen_json` lacks the key). The six
    timing keys (`submit_window_*`, `sell_wait_seconds`, `poll_interval_seconds`,
    `accept_wait_seconds`, `fill_read_overlap_seconds`) were checked against Probe 3's
    measured paper fill latency and all kept (#182, T70, the ADR 0006 amendment of
    2026-10-09), which also switched `tracking_rule` to `residual` (#247 Q4, the
    ADR 0005 amendment of the same date).
    `poll_interval_seconds` never exceeds `accept_wait_seconds` (req 3(f)), or
    the acknowledgement poll could never run before its own deadline. `order_id_prefix`
    is one token with no whitespace, since it heads every `client_order_id`.
    `book_id` names the book whose rows this window writes and whose token sits in
    every `client_order_id` (ADR 0015 seam 1, plan T133): `paper start` reads it
    once into `paper_windows.book_id`, the window's frozen copy, and every later
    row is written with the window's book, never this live key. It matches
    `^[A-Za-z0-9]+$` (no `-` or `:`, so the prefix and the book parse from the
    left of the id, whose `security_id` may contain a `:`), is not in
    `FROZEN_PAPER_KEYS`, and defaults to `store.schema.DEFAULT_BOOK_ID`'s `"main"`.
    `min_rebalances` is the per-cadence table (ADR 0017 part C): an entry for every
    `Cadence`, each above zero; `paper start` freezes the window's cadence's entry as
    the scalar `paper.min_rebalances` (`execution.window._frozen_params`).
    `min_universe_names` (at least 1) and `max_universe_drop_fraction` (in [0, 1]; 1.0
    turns the drop rule off) are the universe-collapse halt's two keys (spec req 7 step
    6, amendment 2026-10-12, #1550; `execution.planning.universe_collapse`): a plan with
    fewer members than the first, or fewer than `p x (1 - fraction)` against the
    window's last plan of `p` members, halts the run. Both are read at run time and are
    not in `FROZEN_PAPER_KEYS`, so windows opened before them are covered.
    `min_free_disk_gb` (#1758) is the free space, in GB, the store's disk must have
    for a run to start; below it the run refuses before any broker call, with a
    `run_failed` alert and no kill switch. 0 turns the check off. Run-time, not frozen.
    """

    model_config = _PHASE3_MODEL_CONFIG

    book_id: str = Field(default="main", min_length=1, pattern=r"^[A-Za-z0-9]+$")
    min_rebalances: dict[Cadence, int] = Field(
        default_factory=lambda: dict(_DEFAULT_MIN_REBALANCES)
    )
    tracking_k: float = Field(default=2.0, ge=0)
    tracking_rule: Literal["raw", "residual"] = "residual"
    max_catch_up_sessions: int = Field(default=5, ge=0)
    max_stale_mark_sessions: int = Field(default=3, ge=0)
    submit_window_before_open_minutes: int = Field(default=90, ge=0)
    submit_window_after_open_minutes: int = Field(default=30, ge=0)
    sell_wait_seconds: float = Field(default=900.0, ge=0)
    poll_interval_seconds: float = Field(default=15.0, gt=0)
    accept_wait_seconds: float = Field(default=30.0, gt=0)
    #: The longer acknowledgement poll's bound (T202b, spec amendment 2026-10-18
    #: item 4): an order unacknowledged by then is the run's one
    #: `pending_unresolved` order. Never below `accept_wait_seconds`; read at run
    #: time, not frozen.
    accept_wait_max_seconds: float = Field(default=300.0, gt=0)
    fill_read_overlap_seconds: float = Field(default=60.0, ge=0)
    order_id_prefix: str = Field(default="tp", min_length=1, pattern=r"^\S+$")
    live_capital_reference: float = Field(default=100.0, gt=0)
    min_override_reason_chars: int = Field(default=20, ge=1)
    min_universe_names: int = Field(default=1, ge=1)
    max_universe_drop_fraction: float = Field(default=0.5, ge=0, le=1)
    min_free_disk_gb: float = Field(default=5.0, ge=0)
    #: `paper status` reads the slippage line over only the newest
    #: `status_slippage_sessions` order sessions of the window (#1741): the line is
    #: a diagnostic, and one `prices_as_of` read per session would otherwise grow
    #: with the window's length. Read at run time, never frozen; `paper report`
    #: keeps the full window (its `paper_reports` row is the record).
    status_slippage_sessions: int = Field(default=20, gt=0)
    #: The fill-price sanity band (#1981): a fill journaled by a run whose price is
    #: further than this fraction from the order's reference price (the close of S-1,
    #: split-adjusted) halts the run with a `fill_price_outside_band` fault. 0.25 is
    #: conservative: an overnight gap past a quarter of the prior close is rare for a
    #: liquid US equity, so a normal opening gap never trips it, while a 100x or 0.01x
    #: fill (a broker-side bad print, a unit error) is far outside. Read at run time,
    #: never frozen, so the open windows see it.
    max_fill_price_deviation: float = Field(default=0.25, gt=0, lt=1)

    @field_validator("min_rebalances")
    @classmethod
    def _validate_min_rebalances(cls, value: dict[Cadence, int]) -> dict[Cadence, int]:
        missing = sorted(set(get_args(Cadence)) - set(value))
        if missing:
            raise ValueError(f"paper.min_rebalances is missing an entry for {missing}")
        not_positive = sorted(cadence for cadence, count in value.items() if count <= 0)
        if not_positive:
            raise ValueError(f"paper.min_rebalances must be above 0 for {not_positive}")
        return value

    @model_validator(mode="after")
    def _validate_poll_within_accept_wait(self) -> PaperConfig:
        if self.poll_interval_seconds > self.accept_wait_seconds:
            raise ValueError(
                f"poll_interval_seconds ({self.poll_interval_seconds}) must not exceed "
                f"accept_wait_seconds ({self.accept_wait_seconds})"
            )
        if self.accept_wait_max_seconds < self.accept_wait_seconds:
            raise ValueError(
                f"accept_wait_max_seconds ({self.accept_wait_max_seconds}) must not be below "
                f"accept_wait_seconds ({self.accept_wait_seconds})"
            )
        return self


class AlertsConfig(BaseModel):
    """Alert delivery channels (spec req 11; #247 Q2). `store` is always a channel, so
    the `alerts` table stays the source of truth; `email` works only when the four
    `ALERT_*` variables are set (`Settings.alert_*`). No channel repeats, and at
    least one non-store channel (`macos` or `email`) must be present (#366 Q22
    (iii), #416): `kill_switch_write_failed` is delivered by `deliver_without_store`,
    which never uses `store`, so a store-only config would reach no channel at all.
    `delivery_timeout_seconds` bounds one `osascript` call, and each SMTP socket
    operation, so a stuck channel cannot hold a run (the spec names no value; T57).

    Listing a channel is not enough for it to actually be *usable* (#544): `macos`
    needs nothing beyond this section, but `email` also needs its four `ALERT_*`
    variables, which live on `Settings`, not here, so `Settings` carries a second,
    cross-field check (`_validate_alert_channel_is_usable`) that refuses a config
    whose only non-store channel is an unconfigured `email` — naming the missing
    variable names, never their values.
    """

    model_config = _PHASE3_MODEL_CONFIG

    channels: list[AlertChannel] = Field(default_factory=lambda: list(_DEFAULT_ALERT_CHANNELS))
    delivery_timeout_seconds: float = Field(default=10.0, gt=0)
    # The off-Mac heartbeat ping (#2155, `heartbeat.py`): bounds the one HTTP request
    # `paper run` and `ingest` make on completion, so a dead network cannot hold a run.
    heartbeat_timeout_seconds: float = Field(default=5.0, gt=0)

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, value: list[AlertChannel]) -> list[AlertChannel]:
        if "store" not in value:
            raise ValueError("alerts.channels must include 'store' (the source of truth)")
        if len(set(value)) != len(value):
            raise ValueError(f"alerts.channels must not repeat, got {value}")
        if not any(c != "store" for c in value):
            raise ValueError(
                "alerts.channels must include at least one non-store channel "
                "('macos' or 'email'): 'kill_switch_write_failed' is delivered by "
                "deliver_without_store, which never uses 'store', so a store-only "
                "config would reach no channel (#366 Q22 (iii), #416)"
            )
        return value


class DashboardConfig(BaseModel):
    """Dashboard page settings (ADR 0011, #273; plan T66b).

    `page_row_limit` bounds every per-row read a page makes over the journal
    (the alerts list, the chain view, the fills table): a page's read
    connection is held for as long as its read takes, and that connection
    blocks the run's write connections (`store.db.open_for_write` retries for
    `store.lock_retry_seconds` and then fails), so the read must be bounded
    structurally rather than by how large the journal has grown.
    """

    model_config = _PHASE3_MODEL_CONFIG

    page_row_limit: int = Field(default=500, gt=0)


# The 8-K items a packet keeps, in priority order (research-labeling amendment
# 2026-10-06 C2 and E14; owner decision 2026-10-06 question 5).
_DEFAULT_EIGHTK_ITEMS = ("3.01", "2.01", "1.03", "5.01", "3.03", "1.01", "8.01")
_EIGHTK_ITEM = re.compile(r"\d\.\d\d")


class ResearchLabelingConfig(BaseModel):
    """Research labeling, pilot A (docs/specs/research-labeling.md req 7 and the
    amendment 2026-10-06's config table, which drops `estimate_margin` and
    `batch_max_usd`).

    `api_base_url` is the vendor's API root, read only by `research/models.py`
    (ADR 0013 point 3 (c)); it must be `https`. `price_usd_per_million_input_tokens`
    is the price snapshot (vendor Models page, 2026-10-05). `chars_per_token` is the
    pre-call token estimate's divisor (E9: 4 ran 1.5 to 1.7x low).
    `max_packet_tokens` is a refusal, never a truncation rule. The three `*_max_chars`
    are the per-document cuts of C2's packets; the three `*_days` windows are
    calendar days around the Form 25's filing date; `eightk_items` is the 8-K item
    priority order. `max_attempts` counts the first send: only HTTP 429, 529 and a
    connection error before any byte was sent are retried (req 7).
    """

    model_config = _PHASE3_MODEL_CONFIG

    api_base_url: str = "https://api.typesafe.ai/v1"
    price_usd_per_million_input_tokens: float = Field(default=0.042, gt=0)
    chars_per_token: float = Field(default=2.5, gt=0)
    max_packet_tokens: int = Field(default=8000, gt=0)
    exhibit_max_chars: int = Field(default=4000, gt=0)
    item_max_chars: int = Field(default=4000, gt=0)
    eightk_max_chars: int = Field(default=12000, gt=0)
    context_before_days: int = Field(default=45, ge=0)
    context_after_days: int = Field(default=20, ge=0)
    marker_after_days: int = Field(default=400, ge=0)
    eightk_items: list[str] = Field(default_factory=lambda: list(_DEFAULT_EIGHTK_ITEMS))
    requests_per_second: float = Field(default=2.0, gt=0)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)

    @field_validator("api_base_url")
    @classmethod
    def _validate_api_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("research.labeling.api_base_url must be an https URL")
        return value

    @field_validator("eightk_items")
    @classmethod
    def _validate_eightk_items(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("research.labeling.eightk_items must name at least one item")
        bad = [item for item in value if not _EIGHTK_ITEM.fullmatch(item)]
        if bad:
            raise ValueError(f"research.labeling.eightk_items entries must look like '3.01': {bad}")
        if len(set(value)) != len(value):
            raise ValueError(f"research.labeling.eightk_items must not repeat, got {value}")
        return value


class ResearchClaudeConfig(BaseModel):
    """Disabled-by-default Claude research comparison arm (#1504)."""

    model_config = _PHASE3_MODEL_CONFIG

    api_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    spend_ceiling_usd_total: float = Field(default=0.0, ge=0)
    price_usd_per_million_input_tokens: float = Field(default=2.0, gt=0)
    price_usd_per_million_output_tokens: float = Field(default=10.0, gt=0)
    chars_per_token: float = Field(default=3.5, gt=0)
    max_output_tokens: int = Field(default=1024, gt=0)
    thinking: Literal["between_tools", "adaptive"] = "between_tools"
    effort: Literal["low", "medium", "high", "max"] = "low"
    requests_per_second: float = Field(default=1.0, gt=0)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)

    @field_validator("api_base_url")
    @classmethod
    def _validate_api_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("research.claude.api_base_url must be an https URL")
        return value


class ResearchConfig(BaseModel):
    """Research-experiment registry (docs/specs/research-registry.md req 2, req 14).

    `experiments_dir` is the only directory `experiment register` accepts files
    from; its sibling `research/` directory (`experiments_dir.parent / "research"`)
    holds `claims.toml`, the claims register `research/experiment.py` checks
    registrations against. Relative to the project root, as every other path-shaped
    default in this module is.
    """

    model_config = _PHASE3_MODEL_CONFIG

    experiments_dir: str = "docs/experiments"
    # Research labeling (docs/specs/research-labeling.md, amendment 2026-10-06's
    # config table; plan T119). `data_dir` is resolved by `research/datafiles.py`
    # alone. The two ceilings are `0.0` in code and set only in the owner's `.env`
    # (`RESEARCH__SPEND_CEILING_USD_MONTH`, `RESEARCH__SPEND_CEILING_USD_TOTAL`):
    # at `0.0` the real model client cannot be built (ADR 0013 point 3 (e) and
    # point 7). A PR that changes either default is labelled `hold`.
    data_dir: str = Field(default_factory=_default_research_data_dir)
    spend_ceiling_usd_month: float = Field(default=0.0, ge=0)
    spend_ceiling_usd_total: float = Field(default=0.0, ge=0)
    labeling: ResearchLabelingConfig = Field(default_factory=ResearchLabelingConfig)
    claude: ResearchClaudeConfig = Field(default_factory=ResearchClaudeConfig)


class Settings(BaseSettings):
    """Root application settings, loaded from env vars and an optional `.env`."""

    # `hide_input_in_errors` (#1037): a nested model with `extra="forbid"` would
    # otherwise print a stray key's value, which may be a secret set under a
    # mistyped name; the error still names the key and the rule.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        hide_input_in_errors=True,
    )

    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    rates: RatesConfig = Field(default_factory=RatesConfig)
    edgar: EdgarConfig = Field(default_factory=EdgarConfig)
    master: MasterConfig = Field(default_factory=MasterConfig)
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY", "MTUM"])
    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    gap: GapConfig = Field(default_factory=GapConfig)
    coverage: CoverageConfig = Field(default_factory=CoverageConfig)
    adjust: AdjustConfig = Field(default_factory=AdjustConfig)
    hypotheses: HypothesesConfig = Field(default_factory=HypothesesConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    profitability: ProfitabilityConfig = Field(default_factory=ProfitabilityConfig)
    combined: CombinedConfig = Field(default_factory=CombinedConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    holdout: HoldoutConfig = Field(default_factory=HoldoutConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    lab: LabConfig = Field(default_factory=LabConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    # Phase 2 (spec req 11), #1575: the `health` command's own thresholds. Last
    # in the list so a new health key never shifts another section's position.
    health: HealthConfig = Field(default_factory=HealthConfig)
    # #1506 (T171c): the borrow-status snapshot, after `health` for the same reason.
    borrow: BorrowConfig = Field(default_factory=BorrowConfig)
    openfigi: OpenfigiConfig = Field(default_factory=OpenfigiConfig)
    # #1598 (T243): the known-answer index check; not frozen, part of no hypothesis.
    index_check: IndexCheckConfig = Field(default_factory=IndexCheckConfig)
    # T219 (backtest spec amendment #1872): the signed-book section, after the other
    # appended sections for the same reason.
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)

    alpaca_api_key: SecretStr | None = Field(default=None)
    alpaca_api_secret: SecretStr | None = Field(default=None)
    sec_edgar_user_agent: SecretStr | None = Field(default=None)
    fred_api_key: SecretStr | None = Field(default=None)
    # Phase 4 (spec req 2): the paper trading keys are their own variables and never
    # fall back to the data keys, so a live-capable key is never in the order path.
    alpaca_paper_api_key: SecretStr | None = Field(default=None)
    alpaca_paper_api_secret: SecretStr | None = Field(default=None)
    # ADR 0017 B.1 (plan T153): every book but `main` has its own pair, keyed by its
    # token (`paper_key_variable_names`). Never a fallback to `main`'s pair or the
    # data keys: a book with no entry has no pair. Environment names are
    # case-insensitive, so the keys arrive lower-cased.
    alpaca_paper_books: dict[str, AlpacaPaperKeyPair] = Field(default_factory=dict)
    # Phase 4 (spec req 11): the optional `email` alert channel. Top-level like the other
    # env-named secrets, because the nested delimiter is `__` and the spec names these
    # variables `ALERT_SMTP_HOST` etc.
    alert_smtp_host: str | None = Field(default=None)
    alert_smtp_user: SecretStr | None = Field(default=None)
    alert_smtp_password: SecretStr | None = Field(default=None)
    alert_email_to: SecretStr | None = Field(default=None)
    # Optional sender for the email channel; the SMTP login when unset (#404).
    alert_email_from: SecretStr | None = Field(default=None)
    # Optional dead-man's-switch URLs (#2155; healthchecks.io style), one check per job
    # because the schedules differ: `paper run` and `ingest` ping theirs on completion.
    # Secrets (the URL is the credential), so the type-driven `secret_values` scrub
    # covers them. Env `ALERT_HEARTBEAT_URL_PAPER` / `ALERT_HEARTBEAT_URL_INGEST`.
    alert_heartbeat_url_paper: SecretStr | None = Field(default=None)
    alert_heartbeat_url_ingest: SecretStr | None = Field(default=None)
    # Research labeling (docs/specs/research-labeling.md req 17): the vendor key, read
    # only by `research/models.py`, redacted by `secret_values`; with it absent the
    # real model client cannot be built (ADR 0013 point 3 (e)).
    typesafe_api_key: SecretStr | None = Field(default=None)
    research_claude_api_key: SecretStr | None = Field(default=None)

    def __init__(self, **kwargs: Any) -> None:
        # A per-instance default (not a class-level `model_config` value) so
        # `TRADEPARTNER_ENV_FILE` is re-read on every construction, e.g. after
        # `monkeypatch.setenv` in a test. An explicit `_env_file=...` kwarg
        # (used in tests to disable dotenv loading entirely) still wins.
        kwargs.setdefault("_env_file", _default_env_file())
        super().__init__(**kwargs)
        # Deliberately *not* a `@model_validator`: a whole-model pydantic validator
        # that raises is reported in a `ValidationError` whose `input_value` is the
        # raw constructor input to the *entire* model, including every other secret
        # passed alongside it (verified against pydantic 2.13's error rendering) —
        # an unconditional secret leak for any config this check could ever refuse.
        # Running the check here, after construction, as a plain attribute read and
        # a plain `raise`, means nothing but the already-validated `self` is ever
        # touched, so only the env var *names* below can appear in the message.
        self._validate_alert_channel_is_usable()
        # Also deliberately *not* a `@model_validator` (see the comment above): the
        # same `ValidationError`-embeds-the-whole-input-including-every-secret
        # problem applies here, since this method, like
        # `_validate_alert_channel_is_usable`, reads `self` after construction and
        # raises a plain `ValueError` whose message names only the bad key, never a
        # secret (code-review finding on #952).
        self._validate_lab_sweepable_keys()
        # Also a plain attribute read and raise, for the same reason (T243).
        self._validate_check_only_symbols()

    def _validate_check_only_symbols(self) -> None:
        """`index_check.check_only_symbols` may not overlap `benchmarks` (backtest
        spec amendment #1598, item 3): a check-only series must stay out of every
        trial's reach, and `benchmarks` is the trial list."""
        overlap = sorted(set(self.index_check.check_only_symbols) & set(self.benchmarks))
        if overlap:
            raise ValueError(
                f"index_check.check_only_symbols {overlap} also in benchmarks: a check-only "
                "series must not be a trial benchmark"
            )

    def _validate_lab_sweepable_keys(self) -> None:
        """`lab.sweepable_keys` (strategy-lab spec req 1; Amendment 2026-10-05 (#952,
        owner)) may name only a real `Settings` key under `ALLOWED_AXIS_PREFIXES`
        (`strategy.*`/`schedule.*`) and outside `FORBIDDEN_AXIS_PREFIXES` (kept as a
        second, belt-and-braces check); this needs the whole model (to resolve a
        dotted key), so it lives here rather than on `LabConfig` alone. The forbidden
        check runs first only so its message names the specific prefix; in today's
        two disjoint lists either order refuses the same keys."""
        for key in self.lab.sweepable_keys:
            if key.startswith(FORBIDDEN_AXIS_PREFIXES):
                raise ValueError(
                    f"lab.sweepable_keys entry {key!r} is under a forbidden prefix "
                    f"{FORBIDDEN_AXIS_PREFIXES}"
                )
            if not key.startswith(ALLOWED_AXIS_PREFIXES):
                raise ValueError(
                    f"lab.sweepable_keys entry {key!r} is not under an allowed prefix "
                    f"{ALLOWED_AXIS_PREFIXES}: a new axis family needs a reviewed code "
                    "change (strategy-lab spec, Amendment 2026-10-05 (#952))"
                )
            if not _settings_has_key(key):
                raise ValueError(f"lab.sweepable_keys entry {key!r} names a key Settings lacks")

    def _validate_alert_channel_is_usable(self) -> None:
        """#544: `AlertsConfig._validate_channels` only checks that a non-store
        channel is *listed*; `email`'s required `ALERT_*` variables live on
        `Settings`, not `AlertsConfig`, so whether it is actually *usable* can only
        be checked here. `macos` needs no configuration, so it already satisfies
        the owner's #366 Q22 (iii) "at least one non-store channel" rule by itself;
        only a config whose *only* non-store channel is `email` is refused when
        that channel is not fully configured. The missing variable names are
        named in the error; their values never are (CLAUDE.md non-negotiable 4)."""
        channels = self.alerts.channels
        if "email" in channels and "macos" not in channels:
            missing = [
                env_name
                for field, env_name in _EMAIL_REQUIRED_SETTINGS
                if not _is_set(getattr(self, field))
            ]
            if missing:
                raise ValueError(
                    "alerts.channels lists 'email' as the only non-store channel, "
                    f"but {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
                    "not set, so kill_switch_write_failed (which never touches the "
                    "store) would reach nobody (#366 Q22 (iii), #544). Set the "
                    "missing variable(s), add 'macos', or remove 'email'."
                )


def get_settings() -> Settings:
    """Load `Settings` fresh from the environment (no process-wide caching)."""
    return Settings()


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def render_validation_errors(exc: ValidationError, *, show_input: Callable[[str], bool]) -> str:
    """`exc`'s errors as `key = <input>: <rule>` (#1093). The config models hide input
    values in their own error text so a secret is never echoed; a caller whose input
    holds no secret (a frozen section, a hypothesis file's frozen keys) uses this to
    put the offending value back. `show_input(key)` decides per dotted location; a
    location it refuses is shown without a value. `show_input` has no default so every
    caller decides: a `Settings` error's input holds the secrets."""
    parts = []
    for error in exc.errors():
        key = ".".join(str(part) for part in error["loc"])
        shown = f"{key} = {error['input']!r}" if show_input(key) else key
        parts.append(f"{shown}: {error['msg']}")
    return "; ".join(parts)


def clean_message(message: str, settings: Settings) -> str:
    """`message` with every configured secret redacted, control characters
    replaced by a space, and cut to `ingest.max_message_chars`: the one
    cleaning for server- or filing-supplied text that is stored (the run row
    and the EDGAR adapter's `failed_filings.json` and FSN manifests, #629)."""
    for value in secret_values(settings):
        message = message.replace(value, "[redacted]")
    return _CONTROL_CHARS.sub(" ", message)[: settings.ingest.max_message_chars]


def secret_values(settings: Settings) -> list[str]:
    """The value of every `SecretStr` field on `settings`, found by type, so a
    secret added to `Settings` later is redacted without editing a list (#334,
    #342). Blank values are left out; values are stripped; longest first, so a
    secret that contains another is redacted whole. The per-book paper pairs
    (T153) sit one level down, in `alpaca_paper_books`, and are included."""
    values = []
    for name in type(settings).model_fields:
        field = getattr(settings, name)
        candidates = [field]
        if name == _BOOK_PAIRS_FIELD:
            candidates += [
                half for pair in field.values() for half in (pair.api_key, pair.api_secret)
            ]
        for secret in candidates:
            if isinstance(secret, SecretStr):
                value = secret.get_secret_value().strip()
                if value:
                    values.append(value)
    return sorted(set(values), key=len, reverse=True)

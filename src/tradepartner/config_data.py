"""Data-foundation config sections: calendar, store, ingest and rates (R1, issue #2512).

Moved verbatim from `tradepartner.config`, which re-exports every name here. This module
imports only `config_base`, never `tradepartner.config`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

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


def _default_alpaca_assets_dir() -> str:
    """`data/alpaca_assets` (the borrow snapshot's raw keep, #1506 spec open
    question 17), anchored to the project root like `edgar.cache_dir`, so a
    scheduled run from another working directory keeps its files beside the
    store; gitignored by `data/*`."""
    return str(Path(__file__).resolve().parents[2] / "data" / "alpaca_assets")


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
    # #2454: `health` warns once the store's disk has less free space than this many GB,
    # well above `ingest.min_free_disk_gb` and `paper.min_free_disk_gb` so there is time
    # to act. 0 turns the warning off.
    warn_free_disk_gb: float = Field(default=15.0, ge=0)


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


class EventsConfig(_ClosedConfig):
    """SEC ownership event forms (plan T305, spec amendment 2026-10-28, #2435).

    `sec_forms` is the list of 13D and Form 4 form names kept as events; it is NOT
    `master.issuer_forms` (an identity rule, in the frozen canonical set) and nothing
    here reaches master construction, the universe, prices, signals or any trial input.
    All three keys sit outside the frozen set (`tests/test_config.py` pins it).
    `sec_forms_enabled` gates collection (off: no event fetch, no row write);
    `sec_forms_read_enabled` gates the as-of reads and stays off until the owner records
    a clean week of `sec_forms_clock_suspect = 0` (#1382). Never empty.
    """

    sec_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: [
            "SC 13D",
            "SC 13D/A",
            "SCHEDULE 13D",
            "SCHEDULE 13D/A",
            "4",
            "4/A",
        ],
        min_length=1,
    )
    sec_forms_enabled: bool = False
    sec_forms_read_enabled: bool = False
    # The Form 4 bulk route (plan T305b, #2498 amendment). None of these is a frozen key.
    # `sec_forms_backfill_start`: the first filing date the backfill takes.
    # `form4_xml_price_below`: an accession with a bulk price under it goes to XML (the
    # set rounds prices to 2 decimals). `form4_zero_price_xml_codes`: a 0.00 price counts
    # as under it only for these transaction codes (an award or gift reports 0 truly).
    # `form4_bulk_order_sample`: accessions per quarter fetched as XML to check
    # `NONDERIV_TRANS_SK` order (at most 1,000). `sec_forms_max_requests`: SEC requests per
    # backfill run (at most 200,000, five times the #2498 estimate). `form4_bulk_max_member_bytes`:
    # the largest uncompressed table member the bulk reader takes (refused above it).
    sec_forms_backfill_start: date = date(2016, 1, 1)
    form4_xml_price_below: Decimal = Field(default=Decimal("1.00"), gt=0)
    form4_zero_price_xml_codes: list[Annotated[str, Field(min_length=1, pattern=r"^\S+$")]] = Field(
        default_factory=lambda: ["P", "S"]
    )
    form4_bulk_order_sample: int = Field(default=20, gt=0, le=1_000)
    sec_forms_max_requests: int = Field(default=40_000, gt=0, le=200_000)
    form4_bulk_max_member_bytes: int = Field(default=1024**3, gt=0, le=8 * 1024**3)

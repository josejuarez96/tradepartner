"""Application configuration.

A single `pydantic-settings` `Settings` object, read from environment
variables and an optional `.env` file (never required; `uv run pytest` must
pass with no `.env` present). Every threshold named in the "Config keys"
lists of the data-foundation and backtest specs and in ADR 0006 is a field
here with the documented default — never hardcoded elsewhere (CLAUDE.md code
standards).

`universe.exclude_sic_ranges` is **guarded**: it is a compliance exclusion
(ADR 0006), not a tunable parameter. A validator rejects any value other
than the charter default, including an environment override, so it cannot
be silently loosened; changing it is a charter amendment, not a config edit.

Secrets (`ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`, the
Phase 4 `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET`, the `ALERT_SMTP_*`
credentials and the research vendor's `TYPESAFE_API_KEY`) are `SecretStr` so
their values never appear in `repr()`/`str()` of `Settings`, including
`SEC_EDGAR_USER_AGENT`, `ALERT_EMAIL_TO` and `ALERT_EMAIL_FROM`, which embed a
personal contact email.

`alpaca.paper` is guarded the same way as `universe.exclude_sic_ranges`: the
order path reaches the paper endpoint only (Phase 4 spec req 2), and Phase 6
changes it by ADR, never by an environment variable.

The `.env` file is anchored to the project root
(`Path(__file__).resolve().parents[2] / ".env"`), not the process's current
working directory: a scheduled job (e.g. launchd, run from `$HOME`) must
still find real secrets. `TRADEPARTNER_ENV_FILE` overrides the path
explicitly, e.g. for an alternate environment or a test fixture.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
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


def _default_research_data_dir() -> str:
    """`data/research` (the research store, research-labeling spec Definitions and
    ADR 0013 point 3), anchored to the project root like `edgar.cache_dir`, so a
    run launched from another working directory still finds it; gitignored by
    `data/*`."""
    return str(Path(__file__).resolve().parents[2] / "data" / "research")


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
    """

    cache_dir: str = Field(default_factory=_default_edgar_cache_dir)
    requests_per_second: float = Field(default=9.0, gt=0)
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
    cover_page_forms: list[str] = Field(default_factory=lambda: ["10-K", "10-Q", "20-F", "40-F"])
    # T11g (#216, #231): the oldest year of the SEC Financial Statement and Notes
    # data sets to fetch. `2009` is FSN's own start; the default `2015` is
    # one year before the 2016 price start, so the latest SIC before any
    # T >= 2016 is already present.
    fsn_first_year: int = Field(default=2015, ge=2009)
    # T11d's keys, added in T11g so FSN extraction keeps these forms' SIC from
    # the first run (no FSN_VERSION bump later). 8-K is included so a de-SPAC's
    # new SIC arrives with its 8-K. `header_first_year` None means
    # `fsn_first_year`, so an override of one follows the other.
    header_forms: list[str] = Field(
        default_factory=lambda: ["S-1", "F-1", "10-12B", "8-K", "10-K", "10-Q", "20-F", "40-F"]
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

    @property
    def header_start_year(self) -> int:
        """`header_first_year`, or `fsn_first_year` when it is unset."""
        return self.fsn_first_year if self.header_first_year is None else self.header_first_year


class MasterConfig(BaseModel):
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

    @field_validator("keep_successors")
    @classmethod
    def _check_keep_successors(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_successor_id(entry)
        return value


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

    Frozen and closed (T47, safety-reviewer): the `paper` guard below runs at
    construction, so an attribute assignment after the fact must be impossible too, and a
    misspelt broker fact (`quantity_decimals`) must fail rather than stay unset silently.
    `model_copy(update=...)` and `model_construct` still bypass any pydantic validator, so
    the trading client (T48) passes the literal `paper=True` and never forwards this field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    historical_feed: Literal["sip", "iex"] = "sip"
    actions_process_lag_days: int = Field(default=90, ge=0)
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
    # Most symbols per bars or corporate-actions GET (#789). alpaca-py comma-joins the
    # list into the query string; an unbatched 9,500-symbol request got HTTP 414 from
    # Alpaca's nginx (2026-10-04 probe), while 2,956 symbols (~15,000 chars) worked.
    symbols_per_request: int = Field(default=1000, gt=0)
    #: Security ids (`<cik>`, `<cik>:<class>` or a #820 successor `<cik>@<day>`)
    #: the owner accepted as genuine long-gap relistings (#943: MiMedx, Nasdaq
    #: 2019-03 to OTC to Nasdaq 2020-11): `ListingResolver` does not apply rule 7's
    #: #847 stopped-line cut to their spans, which run on through a Form 25 to a
    #: later row of their ticker as before #905. Default empty. A malformed id
    #: refuses the config, so a typo never leaves a live name cut silently.
    accepted_relistings: list[str] = Field(default_factory=list)
    # --- Phase 4 trading keys (docs/specs/paper-trading.md req 2, T47) ---
    # Guarded: the trading client is constructed with `paper=True` on every path
    # and a `false` here is refused, even from the environment (validator below).
    paper: bool = True
    # The EDGAR client pattern: pace, timeout and retries from config. The pace sits
    # under Alpaca's documented limit, which the recording task (T48b) confirms.
    trading_requests_per_minute: float = Field(default=150.0, gt=0)
    trading_request_timeout_seconds: float = Field(default=30.0, gt=0)
    trading_max_retries: int = Field(default=3, ge=0)
    # Broker facts with no default, set by the recording task (T48b): the accepted
    # fractional quantity precision and the `client_order_id` length limit. The
    # adapter (T48c) refuses to construct while either is `None`.
    quantity_decimals: int | None = Field(default=None, ge=0)
    client_order_id_max_length: int | None = Field(default=None, gt=0)

    @field_validator("accepted_relistings")
    @classmethod
    def _check_accepted_relistings(cls, value: list[str]) -> list[str]:
        for entry in value:
            check_security_id(entry, "alpaca.accepted_relistings")
        return value

    @field_validator("paper")
    @classmethod
    def _guard_paper(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("guarded setting; change only by ADR (Phase 6 live trading)")
        return value


class ExecutionConfig(BaseModel):
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


class UniverseConfig(BaseModel):
    """ADR 0006 universe-construction thresholds, rules 1-8, in order."""

    security_types: list[str] = Field(default_factory=lambda: ["common"])
    exchanges: list[str] = Field(default_factory=lambda: ["NYSE", "NASDAQ", "NYSE_AMERICAN"])
    # Guarded (ADR 0006): see `_GUARDED_EXCLUDE_SIC_RANGES` and the validator
    # below. Not a tunable parameter; changes only through a charter amendment.
    exclude_sic_ranges: tuple[tuple[int, int], ...] = _GUARDED_EXCLUDE_SIC_RANGES
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
    def _guard_exclude_sic_ranges(
        cls, value: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[int, int], ...]:
        if value != _GUARDED_EXCLUDE_SIC_RANGES:
            raise ValueError("guarded setting; change only by charter amendment")
        return value


class AdjustConfig(BaseModel):
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


class GapConfig(BaseModel):
    """Survivorship-gap reporting thresholds."""

    missing_tail_sessions: int = 5
    count_share_threshold: float = 0.05


# --- Phase 3: backtest engine and trial registry (docs/specs/backtest.md, T30) ---

# The enumerated hypothesis families (spec "Config keys"). Trials are counted per family
# for the deflated Sharpe, so a family cannot be invented by config: adding one is a
# reviewed code change here. `oracle` is refused on the real store (registry, T31b).
HypothesisFamily = Literal["momentum", "oracle"]
_HYPOTHESIS_FAMILIES: tuple[HypothesisFamily, ...] = ("momentum", "oracle")

# Cadence and signal anchor (strategy-lab spec, "Config keys"; ADR 0012, superseding
# ADR 0006's Cadence section): frozen per hypothesis like the universe, beside
# `HypothesisFamily` since both are the enumerations a hypothesis file pins.
Cadence = Literal["month_end", "week_end", "daily"]
SignalAnchor = Literal["month_end", "offset"]

# A family's lineage (strategy-lab spec open question 11): every non-`oracle` family in
# `HypothesisFamily` has an entry here, either a parent family or `None` for a **root**
# family whose signal is unrelated to momentum's. Adding a family is one reviewed code
# change to the literal and this table together, whose PR states why it is a child or a
# root; a child's holdout may only start after its parent's `holdout.end` and only once
# the parent has spent its holdout or reached its spend cap (`store/registry.py`).
FAMILY_PARENTS: dict[HypothesisFamily, HypothesisFamily | None] = {"momentum": None}

# Every Phase 3 section rejects unknown keys and non-finite floats. A hypothesis file pins
# `strategy.*` and `costs.*` (spec req 10), so a misspelt key must fail rather than fall back
# silently to the default, and a NaN or infinite value must fail rather than turn a
# result into NaN.
_PHASE3_MODEL_CONFIG = ConfigDict(extra="forbid", allow_inf_nan=False)


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
    `top_fraction` is a share of ranked names in (0, 1].
    """

    model_config = _PHASE3_MODEL_CONFIG

    formation_months: int = Field(default=12, gt=0)
    skip_months: int = Field(default=1, ge=0)
    top_fraction: float = Field(default=0.10, gt=0, le=1)
    weighting: Literal["equal"] = "equal"
    signal_total_return: bool = True

    @model_validator(mode="after")
    def _validate_formation_window(self) -> StrategyConfig:
        if self.formation_months <= self.skip_months:
            raise ValueError(
                f"formation_months ({self.formation_months}) must be greater than "
                f"skip_months ({self.skip_months})"
            )
        return self


class CostsConfig(BaseModel):
    """Per-trade cost model (spec req 6).

    `per_side_bps` is a placeholder until Phase 4 paper fills recalibrate it (spec open
    question 3); the sensitivity ladder's 100 bp is about the per-dollar cost Novy-Marx &
    Velikov (2016) imply for 12-1 momentum, via G1. Commissions are zero at Alpaca; the
    keys exist for other brokers. Every value is non-negative: a negative cost would pay
    the strategy to trade.
    """

    model_config = _PHASE3_MODEL_CONFIG

    per_side_bps: float = Field(default=15.0, ge=0)
    commission_per_share: float = Field(default=0.0, ge=0)
    commission_per_order: float = Field(default=0.0, ge=0)
    sensitivity_per_side_bps: list[Annotated[float, Field(ge=0)]] = Field(
        default_factory=lambda: [0.0, 30.0, 60.0, 100.0]
    )


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
    """

    model_config = _PHASE3_MODEL_CONFIG

    initial_capital: float = Field(default=100_000.0, gt=0)
    cash_rate: float = 0.0
    delisting_exit: Literal["last_close"] = "last_close"
    stale_exit_sessions: int = Field(default=5, gt=0)


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
# from config, which is set from the unreviewed `.env`. Pinned by value in
# tests/test_config.py.
ALLOWED_AXIS_PREFIXES: tuple[str, ...] = ("strategy.", "schedule.")

_DEFAULT_SWEEPABLE_KEYS: tuple[str, ...] = (
    "strategy.formation_months",
    "strategy.skip_months",
    "strategy.top_fraction",
    "strategy.weighting",
    "strategy.signal_total_return",
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

# The five `paper.*` keys req 14 freezes into the window at `paper start`, beside the
# whole `risk.*` section; every other `paper.*` key is read at run time.
FROZEN_PAPER_KEYS: tuple[str, ...] = (
    "tracking_k",
    "min_rebalances",
    "tracking_rule",
    "max_catch_up_sessions",
    "min_override_reason_chars",
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


class RiskConfig(BaseModel):
    """The Phase 4 risk rules, ADR 0010 point 1: every limit is a named key here, the
    whole section frozen into the paper window at `paper start` and read from the
    window afterwards (spec req 14), never from live `Settings`.

    Unknown keys are refused (a typo must fail, since the section is frozen by name) and
    non-finite floats too. Every fraction is a share in [0, 1]: above 1 would be leverage
    (`max_gross_exposure`, the charter's rule) or a meaningless threshold.
    `max_fill_lag_sessions` is at least 1 (spec req 8). Defaults are the spec's
    reasoning for H1 at paper scale, not measurements; `min_order_notional` and the two
    reconciliation tolerances are confirmed by the recording task (T48b), and after the
    first `paper start` any change to a default is a new ADR (ADR 0010 point 5).
    """

    model_config = _PHASE3_MODEL_CONFIG

    max_position_weight: float = Field(default=0.05, ge=0, le=1)
    max_order_notional_fraction: float = Field(default=0.05, ge=0, le=1)
    max_gross_exposure: float = Field(default=1.0, ge=0, le=1)
    max_orders_per_run: int = Field(default=250, gt=0)
    max_skips_per_run: int = Field(default=10, ge=0)
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


class PaperConfig(BaseModel):
    """Paper-window and tracking-run settings (spec reqs 3, 7, 8, 10, 14).

    `FROZEN_PAPER_KEYS` are frozen at `paper start`; the rest are run-time keys. The six
    timing keys (`submit_window_*`, `sell_wait_seconds`, `poll_interval_seconds`,
    `accept_wait_seconds`, `fill_read_overlap_seconds`) are the spec's placeholders until
    Probe 3 (#182) sets them (T70), which also switches `tracking_rule` to `residual`
    (#247 Q4). `poll_interval_seconds` never exceeds `accept_wait_seconds` (req 3(f)), or
    the acknowledgement poll could never run before its own deadline. `order_id_prefix`
    is one token with no whitespace, since it heads every `client_order_id`.
    """

    model_config = _PHASE3_MODEL_CONFIG

    min_rebalances: int = Field(default=6, gt=0)
    tracking_k: float = Field(default=2.0, ge=0)
    tracking_rule: Literal["raw", "residual"] = "raw"
    max_catch_up_sessions: int = Field(default=5, ge=0)
    submit_window_before_open_minutes: int = Field(default=90, ge=0)
    submit_window_after_open_minutes: int = Field(default=30, ge=0)
    sell_wait_seconds: float = Field(default=900.0, ge=0)
    poll_interval_seconds: float = Field(default=15.0, gt=0)
    accept_wait_seconds: float = Field(default=30.0, gt=0)
    fill_read_overlap_seconds: float = Field(default=60.0, ge=0)
    order_id_prefix: str = Field(default="tp", min_length=1, pattern=r"^\S+$")
    live_capital_reference: float = Field(default=100.0, gt=0)
    min_override_reason_chars: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _validate_poll_within_accept_wait(self) -> PaperConfig:
        if self.poll_interval_seconds > self.accept_wait_seconds:
            raise ValueError(
                f"poll_interval_seconds ({self.poll_interval_seconds}) must not exceed "
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
    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    gap: GapConfig = Field(default_factory=GapConfig)
    adjust: AdjustConfig = Field(default_factory=AdjustConfig)
    hypotheses: HypothesesConfig = Field(default_factory=HypothesesConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
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

    alpaca_api_key: SecretStr | None = Field(default=None)
    alpaca_api_secret: SecretStr | None = Field(default=None)
    sec_edgar_user_agent: SecretStr | None = Field(default=None)
    # Phase 4 (spec req 2): the paper trading keys are their own variables and never
    # fall back to the data keys, so a live-capable key is never in the order path.
    alpaca_paper_api_key: SecretStr | None = Field(default=None)
    alpaca_paper_api_secret: SecretStr | None = Field(default=None)
    # Phase 4 (spec req 11): the optional `email` alert channel. Top-level like the other
    # env-named secrets, because the nested delimiter is `__` and the spec names these
    # variables `ALERT_SMTP_HOST` etc.
    alert_smtp_host: str | None = Field(default=None)
    alert_smtp_user: SecretStr | None = Field(default=None)
    alert_smtp_password: SecretStr | None = Field(default=None)
    alert_email_to: SecretStr | None = Field(default=None)
    # Optional sender for the email channel; the SMTP login when unset (#404).
    alert_email_from: SecretStr | None = Field(default=None)
    # Research labeling (docs/specs/research-labeling.md req 17): the vendor key, read
    # only by `research/models.py`, redacted by `secret_values`; with it absent the
    # real model client cannot be built (ADR 0013 point 3 (e)).
    typesafe_api_key: SecretStr | None = Field(default=None)

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
    secret that contains another is redacted whole."""
    values = []
    for name in type(settings).model_fields:
        secret = getattr(settings, name)
        if isinstance(secret, SecretStr):
            value = secret.get_secret_value().strip()
            if value:
                values.append(value)
    return sorted(set(values), key=len, reverse=True)

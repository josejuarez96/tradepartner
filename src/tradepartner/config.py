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

import os
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args
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
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from tradepartner.config_backtest import _HYPOTHESIS_FAMILIES as _HYPOTHESIS_FAMILIES
from tradepartner.config_backtest import ENGINE_FAMILIES as ENGINE_FAMILIES
from tradepartner.config_backtest import FAMILIES as FAMILIES
from tradepartner.config_backtest import FAMILY_PARENTS as FAMILY_PARENTS
from tradepartner.config_backtest import FAMILY_SIGNAL_SECTIONS as FAMILY_SIGNAL_SECTIONS
from tradepartner.config_backtest import BacktestConfig as BacktestConfig
from tradepartner.config_backtest import Cadence as Cadence
from tradepartner.config_backtest import CombinedConfig as CombinedConfig
from tradepartner.config_backtest import CostsConfig as CostsConfig
from tradepartner.config_backtest import FamilySpec as FamilySpec
from tradepartner.config_backtest import FilingDelayConfig as FilingDelayConfig
from tradepartner.config_backtest import HoldoutConfig as HoldoutConfig
from tradepartner.config_backtest import HypothesesConfig as HypothesesConfig
from tradepartner.config_backtest import HypothesisFamily as HypothesisFamily
from tradepartner.config_backtest import MetricsConfig as MetricsConfig
from tradepartner.config_backtest import PortfolioConfig as PortfolioConfig
from tradepartner.config_backtest import ProfitabilityConfig as ProfitabilityConfig
from tradepartner.config_backtest import ScheduleConfig as ScheduleConfig
from tradepartner.config_backtest import SignalAnchor as SignalAnchor
from tradepartner.config_backtest import SpinoffConfig as SpinoffConfig
from tradepartner.config_backtest import StrategyConfig as StrategyConfig
from tradepartner.config_base import _PHASE3_MODEL_CONFIG as _PHASE3_MODEL_CONFIG
from tradepartner.config_base import _ClosedConfig as _ClosedConfig
from tradepartner.config_data import _DUCKDB_SIZE_RE as _DUCKDB_SIZE_RE
from tradepartner.config_data import _MONTH_PATTERN as _MONTH_PATTERN
from tradepartner.config_data import _REFERENCE_CIK as _REFERENCE_CIK
from tradepartner.config_data import _REFERENCE_TICKER as _REFERENCE_TICKER
from tradepartner.config_data import DEFAULT_REFERENCE_NAMES as DEFAULT_REFERENCE_NAMES
from tradepartner.config_data import AdjustConfig as AdjustConfig
from tradepartner.config_data import BorrowConfig as BorrowConfig
from tradepartner.config_data import CalendarConfig as CalendarConfig
from tradepartner.config_data import CoverageConfig as CoverageConfig
from tradepartner.config_data import EventsConfig as EventsConfig
from tradepartner.config_data import GapConfig as GapConfig
from tradepartner.config_data import HealthConfig as HealthConfig
from tradepartner.config_data import IndexCheckConfig as IndexCheckConfig
from tradepartner.config_data import IngestConfig as IngestConfig
from tradepartner.config_data import OpenfigiConfig as OpenfigiConfig
from tradepartner.config_data import RatesConfig as RatesConfig
from tradepartner.config_data import ReferenceName as ReferenceName
from tradepartner.config_data import StoreConfig as StoreConfig
from tradepartner.config_data import UniverseConfig as UniverseConfig
from tradepartner.config_data import _default_alpaca_assets_dir as _default_alpaca_assets_dir
from tradepartner.config_data import _parse_id_at_day as _parse_id_at_day
from tradepartner.config_data import parse_accepted_jump as parse_accepted_jump
from tradepartner.config_data import parse_accepted_same_day_pair as parse_accepted_same_day_pair
from tradepartner.config_data import parse_accepted_shares_fact as parse_accepted_shares_fact
from tradepartner.config_data import parse_reference_name as parse_reference_name
from tradepartner.config_edgar import _CHAIN_ACCESSION as _CHAIN_ACCESSION
from tradepartner.config_edgar import _CHAIN_ID as _CHAIN_ID
from tradepartner.config_edgar import _CIK as _CIK
from tradepartner.config_edgar import _CLASS_MEMBER as _CLASS_MEMBER
from tradepartner.config_edgar import _QUALIFIED_TAG as _QUALIFIED_TAG
from tradepartner.config_edgar import _REFUSED_FILENAME as _REFUSED_FILENAME
from tradepartner.config_edgar import _SECURITY_ID as _SECURITY_ID
from tradepartner.config_edgar import _STATEMENT_TAGS as _STATEMENT_TAGS
from tradepartner.config_edgar import _SUCCESSOR_ID as _SUCCESSOR_ID
from tradepartner.config_edgar import EdgarConfig as EdgarConfig
from tradepartner.config_edgar import MasterConfig as MasterConfig
from tradepartner.config_edgar import _default_edgar_cache_dir as _default_edgar_cache_dir
from tradepartner.config_edgar import check_security_id as check_security_id
from tradepartner.config_edgar import parse_chains as parse_chains
from tradepartner.config_edgar import parse_successor_id as parse_successor_id

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


def _default_research_data_dir() -> str:
    """`data/research` (the research store, research-labeling spec Definitions and
    ADR 0013 point 3), anchored to the project root like `edgar.cache_dir`, so a
    run launched from another working directory still finds it; gitignored by
    `data/*`."""
    return str(Path(__file__).resolve().parents[2] / "data" / "research")


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


PAPER_FAMILIES: tuple[HypothesisFamily, ...] = tuple(
    family for family, spec in FAMILIES.items() if spec.paper_ready
)


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
#: The regression-test book (owner decision 2026-10-28): its own Alpaca paper account,
#: keys `ALPACA_PAPER_BOOKS__SANDBOX__API_KEY`/`..._SECRET`, run only on a temp COPY of
#: the store, never graded, never in the shakedown (docs/runbooks/sandbox.md).
SANDBOX_BOOK_ID = "sandbox"
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
    # #2183: cash may arrive after an ok reconciliation on the ex-date.
    reconcile_dividend_credit_sessions: int = Field(default=25, ge=1, le=55)
    # #2183: up to this many unmatched dividend dues are searched exhaustively (2^n
    # subsets); more need a proven-unique cent-sum fit or the book halts.
    reconcile_dividend_exhaustive_dues: int = Field(default=8, ge=1, le=12)
    # #2183: the cent-sum states that uniqueness proof may visit; exhausting them
    # fails closed (a cash mismatch), keeping a contrived book bounded.
    reconcile_dividend_subset_states: int = Field(default=10_000, ge=1)
    # #411: the most a delisted holding's late cash may exceed its last stored close x
    # quantity (a share of it, in [0, 1]): a cash deal trades below its price, so the
    # real credit can sit above the stale close; more is not this position's proceeds.
    # A window frozen before the key lacks it and `frozen_risk` reads it at 0.10.
    delisting_cash_max_premium: float = Field(default=0.10, ge=0, le=1)
    # #2266 (paper spec amendment 2026-10-26 item 5, ADR 0025; open questions 39 to
    # 41 decided as recommended): reconciliation breaks. A break is immaterial at or
    # below both the dollar figure and the basis points of ledger equity, summed with
    # the name's earlier auto-acceptances; an open break older than the age is
    # `aged`; more open material breaks than the count, or above the share of equity,
    # is the book-level `break_cap`; the cause lookback is item 14's window. 0 is
    # allowed for each (a materiality of 0 makes every non-zero break material, a cap of 0
    # makes every material break a halt). A
    # window frozen before them reads the defaults through `frozen_risk` (question 42).
    break_materiality_usd: float = Field(default=25.0, ge=0)
    break_materiality_bps: float = Field(default=5.0, ge=0, le=10_000)
    break_max_age_sessions: int = Field(default=5, ge=0)
    max_open_breaks: int = Field(default=3, ge=0)
    max_open_break_share: float = Field(default=0.02, ge=0, le=1)
    break_cause_lookback_sessions: int = Field(default=10, ge=0)


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


class UiConfig(_ClosedConfig):
    """What the Home and Book pages show (plan T273, #2170); display only, nothing
    here reaches a run.

    `next_run_time_et` is the clock time (HH:MM, America/New_York) the status line
    says the next run happens: the schedule itself lives in launchd
    (docs/runbooks/scheduling.md), so this is text to keep beside it. `holdings_rows`
    is how many names a book card lists.

    The Book page's "Stop this book now" (plan T277, #2268; ADR 0018 section 2 and
    its 2026-10-26 amendment): `max_reason_chars` caps the typed reason (as the
    web app's `webapp.max_reason_chars`); the page waits on the `paper kill`
    subprocess for `kill_wait_seconds`, which `None` makes
    `store.writer_lock_retry_seconds` plus `kill_wait_margin_seconds` (interpreter
    start plus one retry delay). A set value must exceed that sum, or a kill still
    waiting on the store would be reported as not confirmed (`Settings`, at load).

    The Lab's file listing (plan T289, #2319): `lab_message_chars` caps a parser
    message the page shows; `lab_git_timeout_seconds` bounds each read-only git
    call that decides a file's merged state.

    The Lab page (plan T291, #2304): `lab_orphan_after_hours` is how long an open
    `sweep_runs` row or an `unfinished` trial may stay open before the page lists it
    apart as an orphan (a run whose writer died) that no longer blocks a new run.
    `lab_poll_seconds` is the positive interval for the Lab status fragment.
    `lab_slot_grace_seconds` is the grace period for a stale Lab run slot.
    `lab_tail_bytes` is how many bytes of the child's log to show.
    `lab_ps_timeout_seconds` bounds the `ps` call that checks the child's start time.
    """

    next_run_time_et: str = "08:05"
    holdings_rows: int = Field(default=5, gt=0)
    max_reason_chars: int = Field(default=500, gt=0)
    kill_wait_seconds: int | None = Field(default=None, gt=0)
    kill_wait_margin_seconds: int = Field(default=30, gt=0)
    lab_message_chars: int = Field(default=500, gt=0)
    lab_git_timeout_seconds: int = Field(default=10, gt=0)
    lab_orphan_after_hours: int = Field(default=24, gt=0)
    lab_poll_seconds: int = Field(default=5, gt=0)
    lab_slot_grace_seconds: float = Field(default=30.0, gt=0)
    lab_tail_bytes: int = Field(default=4000, gt=0)
    lab_ps_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("next_run_time_et")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) is None:
            raise ValueError("ui.next_run_time_et must be HH:MM on a 24-hour clock")
        return value


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


class RawArchiveConfig(_ClosedConfig):
    """The local raw archive's disk floor (plan T345, #2744; ADR 0028, Upload and
    Disk; data-foundation spec amendment #2714 item 3).

    `min_free_disk_gb` is the free space, in GB, the archive's disk must keep after
    an archive write: the guard (`store.raw_archive_config.check_archive_write`)
    subtracts the file's size from the free space and refuses a result below it.
    It fails closed, so unlike `ingest.min_free_disk_gb` it cannot be 0 ("off").
    The archive's path is not here: it is the top-level `TRADEPARTNER_RAW_ARCHIVE`
    (`Settings.tradepartner_raw_archive`), held in `.env` only. Not frozen, part of
    no hypothesis.

    The iCloud waits (plan T346, #2751) bound how long `store.raw_archive` polls
    for a confirmed upload before an eviction and for local copies before a
    re-derive. Each wait is `max(<kind>_wait_min_s, transfer_wait_s_per_mb x MB)`
    over the bytes awaited. T354 measured 20 MB uploaded in 11 s
    (docs/research/2026-10-31-icloud-status-probe.md); the orchestrator's follow-up
    run of 2026-11-01 05:50 UTC (`~/tradepartner-probes/icloud/evict-space-out.txt`)
    measured 200 MB uploaded in 41 s, with free space back only after about 5 min
    of an eviction. On an upload timeout the file stays local, counted against the floor
    and reported pending; on a download timeout the re-derive refuses to start.
    `poll_interval_s` spaces the status polls and `command_timeout_s` caps each
    `osascript` or `brctl` call.
    """

    min_free_disk_gb: float = Field(default=10.0, gt=0)
    upload_wait_min_s: float = Field(default=300.0, gt=0)
    download_wait_min_s: float = Field(default=300.0, gt=0)
    transfer_wait_s_per_mb: float = Field(default=0.2, ge=0)
    poll_interval_s: float = Field(default=5.0, gt=0)
    command_timeout_s: float = Field(default=60.0, gt=0)


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
    # T273 (#2170): the Home page's display keys, last so no other section shifts.
    ui: UiConfig = Field(default_factory=UiConfig)
    # T305 (#2435): SEC ownership event forms; not frozen, last so no section shifts.
    events: EventsConfig = Field(default_factory=EventsConfig)
    # T345 (#2744): the raw archive's write floor; not frozen, last so no section shifts.
    raw_archive: RawArchiveConfig = Field(default_factory=RawArchiveConfig)

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
    # T345 (#2744; ADR 0028): the local raw archive's root, env `TRADEPARTNER_RAW_ARCHIVE`,
    # set only in `.env`. Unset or blank means the archive is off. A `SecretStr` because
    # the path names the macOS account (and may name an iCloud folder): `repr` hides it
    # and `secret_values` redacts it from every stored message. Read and checked only
    # by `store/raw_archive_config.py`, never validated here (#1093).
    tradepartner_raw_archive: SecretStr | None = Field(default=None)

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
        # And again (T277): the kill wait needs `store` and `ui` together.
        self._validate_kill_wait()

    def _validate_kill_wait(self) -> None:
        """A set `ui.kill_wait_seconds` must exceed `paper kill`'s worst-case store
        wait (`store.writer_lock_retry_seconds`) plus `ui.kill_wait_margin_seconds`
        (ADR 0018, amendment 2026-10-26)."""
        floor = self.store.writer_lock_retry_seconds + self.ui.kill_wait_margin_seconds
        if self.ui.kill_wait_seconds is not None and self.ui.kill_wait_seconds <= floor:
            raise ValueError(
                f"ui.kill_wait_seconds ({self.ui.kill_wait_seconds}) must be above "
                f"store.writer_lock_retry_seconds plus ui.kill_wait_margin_seconds ({floor}): "
                "a shorter bound reports a kill still waiting on the store as not confirmed"
            )

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


def kill_wait_seconds(settings: Settings) -> int:
    """How long the Book page waits on a `paper kill` subprocess (`UiConfig`):
    `ui.kill_wait_seconds`, or by default `store.writer_lock_retry_seconds` plus
    `ui.kill_wait_margin_seconds`."""
    if settings.ui.kill_wait_seconds is not None:
        return settings.ui.kill_wait_seconds
    return settings.store.writer_lock_retry_seconds + settings.ui.kill_wait_margin_seconds


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

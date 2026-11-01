"""Backtest config sections: families, strategy, portfolio, costs, schedule (R1, #2512).

Moved verbatim from `tradepartner.config`, which re-exports every name here. This module
imports only `config_base`, never `tradepartner.config`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from tradepartner.config_base import _PHASE3_MODEL_CONFIG, _ClosedConfig

# --- Phase 3: backtest engine and trial registry (docs/specs/backtest.md, T30) ---

# The enumerated hypothesis families (spec "Config keys"). Trials are counted per family
# for the deflated Sharpe, so a family cannot be invented by config: adding one is a
# reviewed code change here. `oracle` is refused on the real store (registry, T31b). A
# test pins `get_args(HypothesisFamily) == tuple(FAMILIES)` (ADR 0014 point 2).
HypothesisFamily = Literal[
    "momentum", "oracle", "profitability", "combined", "filing_delay", "spinoff"
]

# Cadence and signal anchor (strategy-lab spec, "Config keys"; ADR 0012, superseding
# ADR 0006's Cadence section): frozen per hypothesis like the universe, beside
# `HypothesisFamily` since both are the enumerations a hypothesis file pins.
Cadence = Literal["month_end", "week_end", "daily"]
SignalAnchor = Literal["month_end", "offset"]


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
# `filing_delay` and `spinoff` are experiment-only: `sections=()`, so they have no signal
# section and are left out of `FAMILY_SIGNAL_SECTIONS`, but keep their `FAMILY_PARENTS`
# entry (a root).


class FilingDelayConfig(BaseModel):
    """The `filing_delay` family's parameter section: empty on purpose (#2353).

    `filing_delay` is an experiment-only root family: the C1 filing-delay event study runs
    as an external script under the research registry (`docs/experiments/`), never through
    the backtest engine, so it has no `Settings` section and no frozen keys. The class
    exists because every `FamilySpec` names a params model.
    """

    model_config = _PHASE3_MODEL_CONFIG


class SpinoffConfig(BaseModel):
    """The `spinoff` family's parameter section: empty on purpose (#2399).

    `spinoff` is an experiment-only root family: the C2 spin-off-children event study runs as
    an external script under the research registry (`docs/experiments/`), never through the
    backtest engine, so it has no `Settings` section and no frozen keys. The class exists
    because every `FamilySpec` names a params model.
    """

    model_config = _PHASE3_MODEL_CONFIG


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
    "filing_delay": FamilySpec(
        # An experiment-only root family (#2353; owner decision 2026-10-27): it gives the C1
        # filing-delay experiment (`docs/experiments/c1-filing-delay.md`) a family of its own
        # so its configurations count in `filing_delay`'s N, not B4's (`combined`, a child of
        # `momentum`). It has no signal (`backtest/strategies.py`), so the engine and paper
        # refuse it (`engine_ready` / `paper_ready` False), and it has no sweepable keys.
        # No parameter section and no frozen keys: nothing for the engine to freeze.
        sections=(),
        params_model=FilingDelayConfig,
        parent=None,
        engine_ready=False,
        paper_ready=False,
        exclusion_reasons=(),
        count_names=(),
        benchmark=None,
        sweepable_keys=(),
    ),
    "spinoff": FamilySpec(
        # An experiment-only root family (#2399; owner 'start c2', 2026-10-27): it gives the
        # C2 spin-off-children experiment a family of its own so its configurations count in
        # `spinoff`'s N, not B4's. No signal, so the engine and paper refuse it, and no
        # sweepable keys, parameter section or frozen keys (as `filing_delay`, #2353).
        sections=(),
        params_model=SpinoffConfig,
        parent=None,
        engine_ready=False,
        paper_ready=False,
        exclusion_reasons=(),
        count_names=(),
        benchmark=None,
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
    family: spec.sections[0]
    for family, spec in FAMILIES.items()
    if family != "oracle" and spec.sections
}

# The families the engine can run today (#1053, folded into T85 by owner decision
# 2026-10-06): `backtest run` enforces `ENGINE_FAMILIES`. Paper keeps its own gate,
# `PAPER_FAMILIES`, enforced by `paper start` and paper planning; a family joins it only
# by the owner's word (ADR 0014 point 5; `profitability` and `combined` by ADR 0017
# part D, T152).
ENGINE_FAMILIES: tuple[HypothesisFamily, ...] = tuple(
    family for family, spec in FAMILIES.items() if spec.engine_ready
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


#: The frozen delisting failure-return policy versions (data-foundation spec amendment
#: #2670, req 2; plan T328, owner answer 2026-10-31 on #836). `none` is today's
#: last-close exit; a later change to a version's numbers takes a new version name.
DelistingFailureReturn = Literal["none", "failure_v1", "severe_v1"]


@dataclass(frozen=True)
class DelistingPolicy:
    """One version's frozen terms: the terminal return by listing exchange applied to a
    confirmed `performance_failure` or `bankruptcy` exit (an exchange outside the map
    refuses the policy, never 0%), whether an `unknown` non-merger, non-transfer exit is
    shocked too, and the merger match window in trading sessions after `end_session`.
    `merger_acquisition` takes no shock and `voluntary_other` is 0% in every version."""

    shocks: Mapping[str, float]
    shock_unknown: bool
    merger_window_sessions: int


#: Each version's terms, pinned by value in `tests/test_config.py`. Append-only.
DELISTING_POLICIES: Final[Mapping[DelistingFailureReturn, DelistingPolicy]] = MappingProxyType(
    {
        "none": DelistingPolicy(MappingProxyType({}), False, 0),
        "failure_v1": DelistingPolicy(
            MappingProxyType({"NYSE": -0.30, "NYSE_AMERICAN": -0.30, "NASDAQ": -0.55}), False, 10
        ),
        "severe_v1": DelistingPolicy(
            MappingProxyType({"NYSE": -0.55, "NYSE_AMERICAN": -0.55, "NASDAQ": -1.00}), True, 10
        ),
    }
)


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
    #: The frozen failure-return policy at delisting exits (`DELISTING_POLICIES`; plan
    #: T328). `none` reproduces the last-close exit; the engine applies no other value yet.
    delisting_failure_return: DelistingFailureReturn = "none"

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
    #: table. The live default flipped to `store` with T170d (#2011); the frozen-key
    #: table entry stays `constant` (a registration stored before it read `constant`).
    risk_free_source: Literal["constant", "store"] = "store"
    #: The `rates.series` id a `store` trial reads.
    risk_free_series: str = "DTB3"
    #: Quote basis of the stored rate: `discount` (DTB3's) or `yield`.
    risk_free_basis: Literal["discount", "yield"] = "discount"
    red_flag_excess_cagr_pp: float = Field(default=3.0, ge=0)


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

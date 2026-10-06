"""Family-specific, point-in-time signal reads and pure scoring at one dispatch seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, cast

import polars as pl

from tradepartner.backtest.signals import anchor_sessions, gross_profitability, momentum
from tradepartner.config import HypothesisFamily, Settings


@dataclass(frozen=True)
class SignalResult:
    """Scores, declared exclusions and counts produced by one family."""

    scores: dict[str, float]
    exclusions: Mapping[str, tuple[str, ...]]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class _MomentumRead:
    frame: pl.DataFrame


@dataclass(frozen=True)
class _ProfitabilityRead:
    facts: pl.DataFrame
    sics: Mapping[str, int | None]


_Read = _MomentumRead | _ProfitabilityRead


class _ProviderReads(Protocol):
    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame: ...

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame: ...

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]: ...


_Reader = Callable[[object, Settings, date, datetime, Sequence[str]], _Read]
_Signal = Callable[[_Read, Settings, date, datetime, Sequence[str]], SignalResult]


@dataclass(frozen=True)
class Strategy:
    """The reads, pure signal and construction metadata for one family."""

    reads: tuple[str, ...]
    reader: _Reader
    signal: _Signal
    exclusion_reasons: tuple[str, ...]
    count_names: tuple[str, ...]
    section: str


def _momentum_read(
    provider: object,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    strategy, schedule = params.strategy, params.schedule
    a_form, _ = anchor_sessions(
        session, strategy.formation_months, strategy.skip_months, schedule.signal_anchor
    )
    return _MomentumRead(
        cast(_ProviderReads, provider).adjusted_prices(
            t,
            members,
            strategy.signal_total_return,
            sessions_from=a_form,
        )
    )


def _momentum_signal(
    readings: _Read,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> SignalResult:
    frame = cast(_MomentumRead, readings).frame
    strategy, schedule = params.strategy, params.schedule
    result = momentum(
        frame,
        session,
        strategy.formation_months,
        strategy.skip_months,
        schedule.signal_anchor,
        schedule.rebalance_cadence,
        security_ids=members,
    )
    return SignalResult(
        result.scores,
        {"no_history": result.excluded},
        {"n_excluded_no_history": result.n_excluded},
    )


def _profitability_read(
    provider: object,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    facts = cast(_ProviderReads, provider)
    return _ProfitabilityRead(facts.statement_facts(t, members), facts.sics(t, members))


def _profitability_signal(
    readings: _Read,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> SignalResult:
    data = cast(_ProfitabilityRead, readings)
    config = params.profitability
    result = gross_profitability(
        data.facts,
        data.sics,
        t,
        security_ids=members,
        annual_period_days=config.annual_period_days,
        max_fact_age_days=config.max_fact_age_days,
        exclude_sic_ranges=config.exclude_sic_ranges,
        include_derived=config.include_derived,
        basis=config.basis,
    )
    return SignalResult(
        result.scores, {str(reason): ids for reason, ids in result.excluded.items()}, result.counts
    )


_PROFITABILITY_COUNTS = (
    "n_ranked",
    "n_excluded_no_facts",
    "n_excluded_stale_facts",
    "n_excluded_sector",
    "n_excluded_malformed",
    "n_derived",
)

_SIGNALS: Mapping[HypothesisFamily, Strategy] = {
    "momentum": Strategy(
        ("adjusted_prices",),
        _momentum_read,
        _momentum_signal,
        ("no_history",),
        ("n_excluded_no_history",),
        "strategy",
    ),
    "oracle": Strategy(
        ("adjusted_prices",),
        _momentum_read,
        _momentum_signal,
        ("no_history",),
        ("n_excluded_no_history",),
        "strategy",
    ),
    "profitability": Strategy(
        ("statement_facts", "sics"),
        _profitability_read,
        _profitability_signal,
        ("sector", "no_facts", "stale_facts", "malformed"),
        _PROFITABILITY_COUNTS,
        "profitability",
    ),
}


def signal_for(family: HypothesisFamily) -> Strategy:
    """Return the family's signal registration, or fail before any provider read."""
    return _SIGNALS[family]

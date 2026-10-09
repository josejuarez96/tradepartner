"""Family-specific, point-in-time signal reads and pure scoring at one dispatch seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

import polars as pl

from tradepartner.backtest.provider import DataProvider, TurnoverInputs
from tradepartner.backtest.signals import (
    anchor_sessions,
    formation_sessions,
    gross_profitability,
    momentum,
    turnover_screen,
)
from tradepartner.backtest.signals_combined import combined_rank
from tradepartner.config import FAMILIES, HypothesisFamily, Settings


@dataclass(frozen=True)
class SignalResult:
    """Scores, declared exclusions and counts produced by one family."""

    scores: dict[str, float]
    exclusions: Mapping[str, tuple[str, ...]]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class _MomentumRead:
    frame: pl.DataFrame
    turnover: TurnoverInputs | None = None
    formation: tuple[date, ...] = ()


@dataclass(frozen=True)
class _ProfitabilityRead:
    facts: pl.DataFrame
    sics: Mapping[str, int | None]


@dataclass(frozen=True)
class _CombinedRead:
    frame: pl.DataFrame
    facts: pl.DataFrame
    sics: Mapping[str, int | None]


_Read = _MomentumRead | _ProfitabilityRead | _CombinedRead


_Reader = Callable[[DataProvider, Settings, date, datetime, Sequence[str]], _Read]
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


def _screens(params: Settings) -> bool:
    """B10's turnover screen is on (spec amendment #1358): at the default 1.0 nothing
    new is read, screened or reported."""
    return params.strategy.turnover_top_fraction < 1.0


def _momentum_read(
    provider: DataProvider,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    strategy, schedule = params.strategy, params.schedule
    a_form, _ = anchor_sessions(
        session, strategy.formation_months, strategy.skip_months, schedule.signal_anchor
    )
    frame = provider.adjusted_prices(
        t,
        members,
        strategy.signal_total_return,
        sessions_from=a_form,
    )
    if not _screens(params):
        return _MomentumRead(frame)
    formation = formation_sessions(session, schedule.rebalance_cadence)
    return _MomentumRead(frame, provider.turnover_inputs(t, members, formation[0]), formation)


def _oracle_read(
    provider: DataProvider,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    """Momentum's price read without the screen: B10's key is `momentum`'s alone
    (registration refuses `oracle` off 1.0), so `oracle` never screens or reports it."""
    unscreened = params.model_copy(
        update={"strategy": params.strategy.model_copy(update={"turnover_top_fraction": 1.0})}
    )
    return _momentum_read(provider, unscreened, session, t, members)


def _momentum_signal(
    readings: _Read,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> SignalResult:
    read = cast(_MomentumRead, readings)
    strategy, schedule = params.strategy, params.schedule
    screen = None
    if read.turnover is not None:
        screen = turnover_screen(
            read.turnover,
            read.formation,
            session,
            strategy.turnover_top_fraction,
            security_ids=members,
        )
    result = momentum(
        read.frame,
        session,
        strategy.formation_months,
        strategy.skip_months,
        schedule.signal_anchor,
        schedule.rebalance_cadence,
        security_ids=members if screen is None else screen.kept,
    )
    if screen is None:
        return SignalResult(
            result.scores,
            {"no_history": result.excluded},
            {"n_excluded_no_history": result.n_excluded},
        )
    # The screen drops a name before the rank, so the two reasons are disjoint.
    return SignalResult(
        result.scores,
        {"no_history": result.excluded, "no_turnover": screen.excluded},
        {
            "n_excluded_no_history": result.n_excluded,
            "n_screened": screen.n_screened,
            "n_excluded_no_turnover": screen.n_excluded_no_turnover,
        },
    )


def _profitability_read(
    provider: DataProvider,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    return _ProfitabilityRead(provider.statement_facts(t, members), provider.sics(t, members))


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


def _combined_read(
    provider: DataProvider,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> _Read:
    """Momentum's price frame plus the profitability read: `statement_facts` and `sics`."""
    strategy, schedule = params.strategy, params.schedule
    a_form, _ = anchor_sessions(
        session, strategy.formation_months, strategy.skip_months, schedule.signal_anchor
    )
    return _CombinedRead(
        provider.adjusted_prices(
            t,
            members,
            strategy.signal_total_return,
            sessions_from=a_form,
        ),
        provider.statement_facts(t, members),
        provider.sics(t, members),
    )


def _combined_signal(
    readings: _Read,
    params: Settings,
    session: date,
    t: datetime,
    members: Sequence[str],
) -> SignalResult:
    data = cast(_CombinedRead, readings)
    strategy, schedule, profitability = params.strategy, params.schedule, params.profitability
    momentum_signal = momentum(
        data.frame,
        session,
        strategy.formation_months,
        strategy.skip_months,
        schedule.signal_anchor,
        schedule.rebalance_cadence,
        security_ids=members,
    )
    profitability_signal = gross_profitability(
        data.facts,
        data.sics,
        t,
        security_ids=members,
        annual_period_days=profitability.annual_period_days,
        max_fact_age_days=profitability.max_fact_age_days,
        exclude_sic_ranges=profitability.exclude_sic_ranges,
        include_derived=profitability.include_derived,
        basis=profitability.basis,
    )
    result = combined_rank(momentum_signal, profitability_signal)
    return SignalResult(
        result.scores, {str(reason): ids for reason, ids in result.excluded.items()}, result.counts
    )


# The reads and the (reader, signal) pair are family-specific code; `exclusion_reasons`,
# `count_names` and `section` derive from `config.FAMILIES` (ADR 0014 point 2, T128), so
# `Plan.exclusions` and `Plan.counts` carry exactly the names each family declares.
_FAMILY_IO: Mapping[HypothesisFamily, tuple[tuple[str, ...], _Reader, _Signal]] = {
    "momentum": (("adjusted_prices",), _momentum_read, _momentum_signal),
    "oracle": (("adjusted_prices",), _oracle_read, _momentum_signal),
    "profitability": (("statement_facts", "sics"), _profitability_read, _profitability_signal),
    "combined": (("adjusted_prices", "statement_facts", "sics"), _combined_read, _combined_signal),
}


def _build_signals() -> Mapping[HypothesisFamily, Strategy]:
    out: dict[HypothesisFamily, Strategy] = {}
    for family, (reads, reader, signal) in _FAMILY_IO.items():
        spec = FAMILIES[family]
        out[family] = Strategy(
            reads=reads,
            reader=reader,
            signal=signal,
            exclusion_reasons=spec.exclusion_reasons,
            count_names=spec.count_names,
            section=spec.sections[0],
        )
    return out


_SIGNALS: Mapping[HypothesisFamily, Strategy] = _build_signals()


def signal_for(family: HypothesisFamily) -> Strategy:
    """Return the family's signal registration, or fail before any provider read."""
    return _SIGNALS[family]

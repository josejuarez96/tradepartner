"""Results, metrics and deflated Sharpe writes (backtest spec reqs 7, 8, 15; plan T39b).

`write_results` turns the engine's per-level `BacktestResult`s into registry rows, in
the caller's write transaction and only through `store.registry`:

1. **Refuse bad input before any write**: `params` must be the trial's frozen
   `Settings` (its frozen keys hash to the trial's `params_sha256`), because the base
   level, the risk-free rate and the red-flag threshold come from it and
   `family_sharpes` reads the frozen base level; the base level (`costs.per_side_bps`)
   and a 0 bp level must both be present (`cost_drag` is gross minus net CAGR, and
   gross is the 0 bp run); every result's own level must match its key; and both
   benchmarks (`SPY`, `MTUM`), and the family's declared benchmark when it names a
   third series (`config.FAMILIES[family].benchmark`), must have equity rows.
2. **Metrics** (req 7; strategy-lab spec req 8): per series (`strategy`, `SPY`,
   `MTUM`, plus a family's declared third benchmark, `family_series`) and level, from
   period returns (equity at close(T_{i+1}) over equity at close(T_i), minus one, over
   the rebalance sessions of the run at the hypothesis's
   `schedule.rebalance_cadence`, read through `frozen_values`) annualised with that
   cadence's `periods_per_year`, the benchmarks at the same level, the 0 bp run of
   the same series as gross, every session's equity for drawdown, and the strategy's
   one-sided turnover per rebalance. "Gross" is gross of the per-side bps only: the
   0 bp run still pays `costs.commission_per_share` and `commission_per_order`, which
   are the same at every level (req 6; both 0 at Alpaca), so with nonzero commissions
   `cost_drag` leaves them out. A benchmark is bought once at F_0 and never
   rebalanced, so its turnover is 0; the strategy's first rebalance includes its
   initial buy.
3. **Detail rows** at the trial's detail level (strategy-lab spec req 12): metrics and
   rebalances per level at both. `full` (standalone hypotheses, the default): equity
   per level for every session, weights at the base level only (targets are the same
   at every level). `summary` (sweep variants): equity for every session at the base
   level and at the rebalance sessions only at the other levels, no weights. Metrics,
   `max_drawdown` per level included, come from the full in-memory results in step 2,
   before the rows are chosen, so a summary trial's metrics equal a full trial's.
4. **Result row** (req 8, 15; strategy-lab spec req 9): N from `family_n_split` (its
   research part stored as `n_research`, below) and the
   per-basis annualised pair Sharpes from `family_sharpes`, both with this trial as
   `pending`, so an `ok` in-sample, non-synthetic run counts itself and any other run
   (synthetic, holdout, tracking) is deflated against the family as it stands without
   changing it. Both bases (`raw`, `excess_spy`) from the base-level strategy metrics,
   each with its own inputs, V and SR* stored in annual units (`sharpe_unit =
   annual`); `n_trials` is N as counted (0 for an uncounted run in an empty family,
   where there is nothing to deflate and DSR is PSR(0)). The red flag from the
   base-level strategy `excess_cagr_spy`; the gap maxima over the base level's
   rebalances. `write_result` records `failed` instead when the store changed during
   the run.

**One N function** (strategy-lab plan, approach). `family_n(conn, family)` is the one
place the family's N is computed: `write_results` stores it, and the backtest page and
every lab module call it rather than counting trial rows themselves.

**Research runs raise N, never V** (research-registry spec req 9; backtest spec req 8 as
amended 2026-10-07, #901, T83b). N = the family's counted backtest trials plus
`store.research.family_run_count` (over its non-synthetic, non-holdout-spending research
runs that touch returns: Σ `n_configurations` of the `ok` runs plus, per `failed` run, the
larger of its declared and evaluated configurations, #1408); `family_n_split` returns the
two parts, `write_results` stores the research part in `trial_results.n_research` and
the sum in `n_trials`. V and the pair Sharpes stay the backtest pairs' alone (a
regression has no Sharpe). A store without the research tables (one a read-only
connection opened before version 12) reads the research part as None and N as the
backtest count; `write_results` refuses such a store before any write, since every
writing command migrates first.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise
from typing import cast

import duckdb
import polars as pl

from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult
from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.hypothesis import frozen_hash_matches
from tradepartner.backtest.metrics import (
    DeflatedSharpe,
    LongShortBook,
    Series,
    deflated_sharpe,
    red_flag,
    series_metrics,
)
from tradepartner.backtest.riskfree import constant_period_returns, period_risk_free
from tradepartner.backtest.schedule import periods_per_year, rebalance_sessions
from tradepartner.calendar import session_close
from tradepartner.config import FAMILIES, Cadence, HypothesisFamily, Settings
from tradepartner.store import registry, research, schema
from tradepartner.store.registry import (
    EquityRow,
    FamilySharpes,
    MetricRow,
    RebalanceRow,
    ResultStatistics,
    TrialHandle,
)

SPY_SERIES: Series = "SPY"
MTUM_SERIES: Series = "MTUM"
SERIES: tuple[Series, ...] = ("strategy", SPY_SERIES, MTUM_SERIES)


def family_benchmark(family: str) -> str | None:
    """The registered family's declared benchmark when it is a third series, else None.
    SPY and MTUM are mandatory for every family (charter, ADR 0005, ADR 0014 point 4),
    so a spec naming either adds nothing. Raises `KeyError` for an unregistered family."""
    name = FAMILIES[cast(HypothesisFamily, family)].benchmark
    return None if name in (None, SPY_SERIES, MTUM_SERIES) else name


def family_series(family: str) -> tuple[Series, ...]:
    """The series a `family` trial stores: `SERIES` plus its third benchmark, if any."""
    extra = family_benchmark(family)
    return SERIES if extra is None else (*SERIES, extra)


#: The level whose run is the gross series for `cost_drag`.
GROSS_LEVEL = 0.0

FamilySharpesFn = Callable[..., FamilySharpes]

#: What a trial stores beyond metrics and rebalance rows (strategy-lab spec,
#: "Detail level"; `lab.sweep_detail_level` for sweep variants). One list, the
#: registry's, so `open_trial` and `write_results` accept the same levels.
DetailLevel = registry.DetailLevel
DETAIL_LEVELS: tuple[DetailLevel, ...] = registry.DETAIL_LEVELS

#: The frozen key the period ends are read from (strategy-lab spec, "Cadence").
CADENCE_KEY = "schedule.rebalance_cadence"


@dataclass(frozen=True)
class FamilyN:
    """A family's N in its two parts: `trials`, the counted backtest trials, and
    `research`, `family_run_count` (None on a store without the research registry)."""

    trials: int
    research: int | None

    @property
    def total(self) -> int:
        """N: the backtest trials plus the research runs' configurations."""
        return self.trials + (self.research or 0)


@dataclass(frozen=True)
class _StatisticsWithResearch(ResultStatistics):
    """`ResultStatistics` plus `trial_results.n_research` (research-registry spec req
    9): `registry.write_result` writes every field of the statistics it is given."""

    n_research: int | None = None


def family_n_split(
    conn: duckdb.DuckDBPyConnection, family: str, *, pending: TrialHandle | None = None
) -> FamilyN:
    """`family`'s N in its two parts (module docstring, "Research runs raise N"):
    its `ok`, non-synthetic, in-sample trials, reruns and every variant's included
    (backtest spec req 8; strategy-lab spec req 3), with `pending` counted as
    `registry.family_sharpes` does; and `research.family_run_count`, None when the
    store has no research tables."""
    trials = registry.count_counted_trials(conn, family, pending)
    try:
        runs: int | None = research.family_run_count(conn, family)
    except schema.ResearchNotInitialised:
        runs = None
    return FamilyN(trials=trials, research=runs)


def family_n(
    conn: duckdb.DuckDBPyConnection, family: str, *, pending: TrialHandle | None = None
) -> int:
    """N of `family`: `family_n_split(...).total`. The one place N is computed (module
    docstring, "One N function")."""
    return family_n_split(conn, family, pending=pending).total


def hypothesis_cadence(conn: duckdb.DuckDBPyConnection, handle: TrialHandle) -> Cadence:
    """The trial's frozen `schedule.rebalance_cadence`, read through `frozen_values`
    (a registration without the key reads its `month_end` default)."""
    record = registry.get_hypothesis_by_id(conn, handle.hypothesis_id)
    cadence: Cadence = frozen_values(record)[CADENCE_KEY]
    return cadence


def _check_frozen(handle: TrialHandle, params: Settings) -> None:
    """Refuse `params` that are not the trial's frozen `Settings`."""
    if not frozen_hash_matches(params, handle.params_sha256, family=handle.family):
        raise ValueError(
            f"params are not trial {handle.trial_id}'s frozen settings (their frozen keys "
            "hash differently); pass the Settings the trial was opened with"
        )


def _check(results: Mapping[float, BacktestResult], params: Settings, family: str) -> None:
    """Every refusal on the results, before any row is written: `SPY`, `MTUM` and the
    family's declared benchmark, when it is a third series, must have equity rows."""
    series_needed = family_series(family)
    for level, result in results.items():
        if result.cost_per_side_bps != level:
            raise ValueError(
                f"result under level {level} bp holds the {result.cost_per_side_bps} bp run"
            )
    base = params.costs.per_side_bps
    for level, name in ((base, "base"), (GROSS_LEVEL, "gross (0 bp)")):
        if level not in results:
            raise ValueError(
                f"no {name} level {level} bp among {sorted(results)} bp; "
                "the base level feeds N, V, DSR and the red flag, and 0 bp is gross for cost_drag"
            )
    for level, result in results.items():
        present = {row.series for row in result.equity}
        missing = [series for series in series_needed if series not in present]
        if missing:
            raise ValueError(f"no equity rows for {missing} at {level} bp")


#: The name prefix of a `long_short` trial's borrow-sensitivity series (spec amendment
#: #1872, item 6; the engine's `strategy_borrow_<n>bp`). They are reported only.
BORROW_SERIES_PREFIX = f"{STRATEGY_SERIES}_borrow_"


def borrow_series(result: BacktestResult) -> list[str]:
    """The borrow-sensitivity series among `result`'s equity rows, in name order."""
    return sorted(
        {row.series for row in result.equity if row.series.startswith(BORROW_SERIES_PREFIX)}
    )


def _series_equity(result: BacktestResult, series: str) -> list[EquityRow]:
    return sorted((row for row in result.equity if row.series == series), key=lambda r: r.session)


def _period_ends(result: BacktestResult, cadence: Cadence) -> list[date]:
    """The run's rebalance sessions T_0..T_n at `cadence`, from its first and last
    equity session."""
    sessions = [row.session for row in result.equity]
    return rebalance_sessions(min(sessions), max(sessions), cadence)


def _period_returns(rows: Sequence[EquityRow], ends: Sequence[date]) -> list[float]:
    """Period i return: equity at close(T_{i+1}) / equity at close(T_i) - 1."""
    equity = {row.session: row.equity for row in rows}
    missing = [session for session in ends if session not in equity]
    if missing:
        raise ValueError(f"no equity at rebalance sessions {missing}")
    return [equity[b] / equity[a] - 1 for a, b in pairwise(ends)]


#: `rates.value` is the published quote in percent (`5.25` is 5.25%); the conversion takes
#: a fraction.
_PERCENT = 100.0


def _rates_by_session(
    frame: pl.DataFrame | None, series: str, sessions: Sequence[date]
) -> dict[date, float]:
    """`rf(s)` for every session after the first, as the published fraction: the value of
    the latest observation (by `observation_date`) among the frame's rows with
    `known_at <= close(s)`, carried across the sessions with none newer. A session with
    nothing known at its close is left out, which `period_risk_free` turns into
    `RiskFreeMissing` (a series the store lacks, or sessions before its first stamp).

    The frame is the engine's one read at the data cutoff: the latest revision of each
    observation known then, so a revision first known after `close(s)` and before the
    cutoff hides that observation from `s` (the earlier carry applies) rather than
    serving the earlier revision."""
    if frame is None:
        raise ValueError(
            "metrics.risk_free_source is 'store' but the result carries no rates frame"
        )
    rows = sorted(
        (
            (known_at, observed, value)
            for known_at, observed, value in frame.filter(pl.col("series") == series)
            .select("known_at", "observation_date", "value")
            .iter_rows()
        ),
        key=lambda row: row[0],
    )
    out: dict[date, float] = {}
    latest: tuple[date, float] | None = None
    taken = 0
    for session in sessions[1:]:
        close = session_close(session)
        while taken < len(rows) and rows[taken][0] <= close:
            _, observed, value = rows[taken]
            if latest is None or observed >= latest[0]:
                latest = (observed, value)
            taken += 1
        if latest is not None:
            out[session] = latest[1] / _PERCENT
    return out


def _risk_free_returns(
    result: BacktestResult, params: Settings, ends: Sequence[date], ppy: int, n_periods: int
) -> list[float]:
    """The risk-free return of each period: `constant`, the compounded
    `metrics.risk_free_rate`, or `store`, the compounded T-bill accrual over the sessions
    of the period (`riskfree.period_risk_free`). Raises `RiskFreeMissing` (before any
    row is written) when a session has no rate known at its close."""
    metrics = params.metrics
    if metrics.risk_free_source == "constant":
        return constant_period_returns(metrics.risk_free_rate, ppy, n_periods)
    series = metrics.risk_free_series
    sessions = sorted({row.session for row in result.equity})
    published = _rates_by_session(result.rates, series, sessions)
    return period_risk_free(sessions, ends, published, basis=metrics.risk_free_basis, series=series)


def _cost_level_kind(level: float, base: float, ladder: set[float]) -> str:
    """The `trial_metrics.cost_level_kind` of `level` (spec amendment 2026-10-18,
    #1792, item 2): `base` for the trial's `costs.per_side_bps`, `ladder` for one
    of `costs.sensitivity_per_side_bps`, else `measured` — the one extra level
    `--measured-cost-bps` adds, refused when it collides with either, so it is
    the only level that reaches the last branch."""
    if level == base:
        return "base"
    return "ladder" if level in ladder else "measured"


def metric_rows(
    results: Mapping[float, BacktestResult],
    params: Settings,
    *,
    cadence: Cadence,
    family: str,
) -> list[MetricRow]:
    """Every req 8 metric per series and level at `cadence`, as `trial_metrics`
    rows; a `family` benchmark beyond SPY and MTUM adds a series and two keys per
    series. Each row carries its level's `cost_level_kind`: `base`, `ladder` or
    `measured` (spec amendment 2026-10-18, #1792, item 2), so a measured column
    is never read as a ladder rung."""
    _check(results, params, family)
    third = family_benchmark(family)
    all_series = family_series(family)
    gross = results[GROSS_LEVEL]
    ppy = periods_per_year(cadence)
    base = params.costs.per_side_bps
    ladder = set(params.costs.sensitivity_per_side_bps)
    rows: list[MetricRow] = []
    base_result = results[base]
    for level in sorted(results):
        result = results[level]
        kind = _cost_level_kind(level, base, ladder)
        ends = _period_ends(result, cadence)
        returns = {s: _period_returns(_series_equity(result, s), ends) for s in all_series}
        risk_free = _risk_free_returns(result, params, ends, ppy, len(ends) - 1)
        for series in all_series:
            values = series_metrics(
                series,
                period_returns=returns[series],
                gross_period_returns=_period_returns(_series_equity(gross, series), ends),
                daily_equity=[row.equity for row in _series_equity(result, series)],
                turnover=(
                    [r.turnover for r in result.rebalances] if series == STRATEGY_SERIES else []
                ),
                spy_period_returns=returns[SPY_SERIES],
                mtum_period_returns=returns[MTUM_SERIES],
                periods_per_year=ppy,
                risk_free_period_returns=risk_free,
                family_benchmark=None if third is None else (third, returns[third]),
                long_short=_long_short_book(result) if series == STRATEGY_SERIES else None,
            )
            rows.extend(MetricRow(series, level, key, value, kind) for key, value in values.items())
        if level == base:
            rows.extend(
                _borrow_metric_rows(base_result, returns, params, ends, risk_free, ppy, level, kind)
            )
    return rows


def _long_short_book(result: BacktestResult) -> LongShortBook | None:
    """The result's signed-book summary as `series_metrics` takes it (None for a
    `long_only` run, so it writes no new row)."""
    summary = result.exposure_summary
    if summary is None:
        return None
    return LongShortBook(
        gross_mean=summary.gross_mean,
        gross_max=summary.gross_max,
        net_mean=summary.net_mean,
        net_min=summary.net_min,
        net_max=summary.net_max,
        borrow_paid=summary.borrow_paid,
    )


def _borrow_metric_rows(
    result: BacktestResult,
    returns: Mapping[str, Sequence[float]],
    params: Settings,
    ends: Sequence[date],
    risk_free: Sequence[float],
    ppy: int,
    level: float,
    kind: str,
) -> list[MetricRow]:
    """The req 8 keys of each `strategy_borrow_<n>bp` series at the base level, where the
    engine writes them. Reported only (spec amendment #1872, item 6): `write_results`
    reads V, N, DSR and the red flag from the `strategy` series alone, and no selection
    module reads these rows. The book has no 0 bp run of its own, so its gross is its net
    (`cost_drag` 0) and its turnover is the strategy's, the same trades."""
    rows: list[MetricRow] = []
    for series in borrow_series(result):
        own = _period_returns(_series_equity(result, series), ends)
        values = series_metrics(
            series,
            period_returns=own,
            gross_period_returns=own,
            daily_equity=[row.equity for row in _series_equity(result, series)],
            turnover=[r.turnover for r in result.rebalances],
            spy_period_returns=returns[SPY_SERIES],
            mtum_period_returns=returns[MTUM_SERIES],
            periods_per_year=ppy,
            risk_free_period_returns=risk_free,
        )
        rows.extend(MetricRow(series, level, key, value, kind) for key, value in values.items())
    return rows


def _gap_max(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def result_statistics(
    base_metrics: Mapping[str, float | None],
    base_rebalances: Sequence[RebalanceRow],
    family: FamilySharpes,
    params: Settings,
    *,
    n_trials: int,
    cadence: Cadence,
) -> ResultStatistics:
    """The `ok` result row's statistics from the base-level strategy metrics, the
    family's N (`n_trials`, from `family_n`) and annualised pair Sharpes (this trial
    included when it counts), the trial's `cadence` and the base level's rebalances
    (module docstring, step 4)."""
    n = n_trials
    ppy = periods_per_year(cadence)
    raw = deflated_sharpe(
        base_metrics, "raw", n_trials=max(n, 1), pair_sharpes=family.raw, periods_per_year=ppy
    )
    excess = deflated_sharpe(
        base_metrics,
        "excess_spy",
        n_trials=max(n, 1),
        pair_sharpes=family.excess_spy,
        periods_per_year=ppy,
    )
    return ResultStatistics(
        n_trials=n,
        sharpe_variance=raw.sharpe_variance,
        sr_star=raw.sr_star,
        psr_zero=raw.psr_zero,
        dsr=raw.dsr,
        sharpe_variance_excess=excess.sharpe_variance,
        sr_star_excess=excess.sr_star,
        psr_zero_excess=excess.psr_zero,
        dsr_excess=excess.dsr,
        dsr_basis=_basis_label(raw, excess),
        red_flag=red_flag(base_metrics, params),
        gap_max_count_share=_gap_max(r.gap_count_share for r in base_rebalances),
        gap_max_size_share=_gap_max(r.gap_size_share for r in base_rebalances),
    )


def _basis_label(raw: DeflatedSharpe, excess: DeflatedSharpe) -> str:
    """`dsr` or `psr`; both bases take V over the same pairs, so they agree."""
    if raw.dsr_basis != excess.dsr_basis:
        raise AssertionError(f"bases disagree: raw {raw.dsr_basis}, excess {excess.dsr_basis}")
    return raw.dsr_basis


def detail_equity(
    results: Mapping[float, BacktestResult],
    base_level: float,
    detail_level: DetailLevel,
    *,
    cadence: Cadence,
) -> list[EquityRow]:
    """The `trial_equity` rows a trial at `detail_level` stores (module docstring, step
    3): every row at `full`; at `summary`, every row at `base_level` and, at the other
    levels, the rows at the run's rebalance sessions at `cadence`."""
    if detail_level not in DETAIL_LEVELS:
        raise ValueError(f"detail level must be one of {DETAIL_LEVELS}, got {detail_level!r}")
    rows: list[EquityRow] = []
    for level in sorted(results):
        result = results[level]
        if detail_level == "full" or level == base_level:
            rows.extend(result.equity)
        else:
            ends = set(_period_ends(result, cadence))
            rows.extend(row for row in result.equity if row.session in ends)
    return rows


def write_results(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    results: Mapping[float, BacktestResult],
    params: Settings,
    family_sharpes: FamilySharpesFn = registry.family_sharpes,
    *,
    detail_level: DetailLevel = "full",
) -> str:
    """Write one run's rows for `handle` at `detail_level` and its result row; return
    the recorded status, `"ok"`, or `"failed"` when the store changed during the run
    (module docstring).

    `results` is the engine's output keyed by per-side bps; `params` is the trial's
    frozen `Settings`; `detail_level` is `full` for a standalone hypothesis (the
    default) and the sweep's level for a sweep variant (strategy-lab spec req 12).
    A measured level (`--measured-cost-bps`) arrives as one more key of `results`
    and is written as any other non-base level: its metrics labelled `measured`
    by `metric_rows`, its equity and rebalance rows as a ladder level's. N, V,
    DSR and the red flag stay the base level's (module docstring, step 4), so a
    measured level is never in them.
    Raises `ValueError` before any write when `params` are not those frozen settings,
    a level or benchmark is missing or the detail level is unknown, and
    `ResearchNotInitialised` when the store has no research tables (module docstring,
    "Research runs raise N"). Runs in the caller's write transaction.
    """
    if detail_level not in DETAIL_LEVELS:
        raise ValueError(f"detail level must be one of {DETAIL_LEVELS}, got {detail_level!r}")
    if detail_level != handle.detail_level:
        raise ValueError(
            f"trial {handle.trial_id} was opened at detail level {handle.detail_level!r}; "
            f"writing it at {detail_level!r} would leave its trials row wrong (#1197)"
        )
    _check_frozen(handle, params)
    schema.require_research(conn)
    cadence = hypothesis_cadence(conn, handle)
    rows = metric_rows(results, params, cadence=cadence, family=handle.family)
    base_level = params.costs.per_side_bps
    base = results[base_level]
    equity = detail_equity(results, base_level, detail_level, cadence=cadence)
    registry.write_metrics(conn, handle, rows)
    registry.write_equity(conn, handle, equity)
    for level in sorted(results):
        registry.write_rebalances(conn, handle, results[level].rebalances)
    if detail_level == "full":
        registry.write_weights(conn, handle, base.weights)
    base_metrics = {
        row.metric: row.value
        for row in rows
        if row.series == STRATEGY_SERIES and row.cost_per_side_bps == base_level
    }
    family = family_sharpes(conn, handle.family, pending=handle)
    n = family_n_split(conn, handle.family, pending=handle)
    stats = result_statistics(
        base_metrics, base.rebalances, family, params, n_trials=n.total, cadence=cadence
    )
    return registry.write_result(
        conn, handle, _StatisticsWithResearch(**asdict(stats), n_research=n.research)
    )

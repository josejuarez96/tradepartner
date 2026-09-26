"""Results, metrics and deflated Sharpe writes (backtest spec reqs 7, 8, 15; plan T39b).

`write_results` turns the engine's per-level `BacktestResult`s into registry rows, in
the caller's write transaction and only through `store.registry`:

1. **Refuse bad input before any write**: `params` must be the trial's frozen
   `Settings` (its frozen keys hash to the trial's `params_sha256`), because the base
   level, the risk-free rate and the red-flag threshold come from it and
   `family_sharpes` reads the frozen base level; the base level (`costs.per_side_bps`)
   and a 0 bp level must both be present (`cost_drag` is gross minus net CAGR, and
   gross is the 0 bp run); every result's own level must match its key; and both
   benchmarks (`SPY`, `MTUM`) must have equity rows.
2. **Metrics** (req 7): per series (`strategy`, `SPY`, `MTUM`) and level, from month
   returns (equity at close(T_{i+1}) over equity at close(T_i), minus one, over the
   rebalance sessions of the run), the benchmarks at the same level, the 0 bp run of
   the same series as gross, every session's equity for drawdown, and the strategy's
   one-sided turnover per rebalance. "Gross" is gross of the per-side bps only: the
   0 bp run still pays `costs.commission_per_share` and `commission_per_order`, which
   are the same at every level (req 6; both 0 at Alpaca), so with nonzero commissions
   `cost_drag` leaves them out. A benchmark is bought once at F_0 and never
   rebalanced, so its turnover is 0; the strategy's first rebalance includes its
   initial buy.
3. **Detail rows**: metrics, equity per level, weights at the base level only (targets
   are the same at every level), rebalances per level.
4. **Result row** (req 8, 15): N and the per-basis pair Sharpes from `family_sharpes`
   with this trial as `pending`, so an `ok` in-sample, non-synthetic run counts itself
   and any other run (synthetic, holdout, tracking) is deflated against the family as
   it stands without changing it. Both bases (`raw`, `excess_spy`) from the base-level
   strategy metrics, each with its own inputs; `n_trials` is N as counted (0 for an
   uncounted run in an empty family, where there is nothing to deflate and DSR is
   PSR(0)). The red flag from the base-level strategy `excess_cagr_spy`; the gap
   maxima over the base level's rebalances. `write_result` records `failed` instead
   when the store changed during the run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from itertools import pairwise

import duckdb

from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.metrics import (
    DeflatedSharpe,
    Series,
    deflated_sharpe,
    red_flag,
    series_metrics,
)
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.config import Settings
from tradepartner.store import registry
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

#: The level whose run is the gross series for `cost_drag`.
GROSS_LEVEL = 0.0

FamilySharpesFn = Callable[..., FamilySharpes]


def _check_frozen(handle: TrialHandle, params: Settings) -> None:
    """Refuse `params` that are not the trial's frozen `Settings`."""
    if registry.params_sha256(frozen_params_of(params)) != handle.params_sha256:
        raise ValueError(
            f"params are not trial {handle.trial_id}'s frozen settings (their frozen keys "
            "hash differently); pass the Settings the trial was opened with"
        )


def _check(results: Mapping[float, BacktestResult], params: Settings) -> None:
    """Every refusal on the results, before any row is written."""
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
        missing = [series for series in SERIES if series not in present]
        if missing:
            raise ValueError(f"no equity rows for {missing} at {level} bp")


def _series_equity(result: BacktestResult, series: str) -> list[EquityRow]:
    return sorted((row for row in result.equity if row.series == series), key=lambda r: r.session)


def _month_ends(result: BacktestResult) -> list[date]:
    """The run's rebalance sessions T_0..T_n, from its first and last equity session."""
    sessions = [row.session for row in result.equity]
    return rebalance_sessions(min(sessions), max(sessions))


def _monthly(rows: Sequence[EquityRow], ends: Sequence[date]) -> list[float]:
    """Month i return: equity at close(T_{i+1}) / equity at close(T_i) - 1."""
    equity = {row.session: row.equity for row in rows}
    missing = [session for session in ends if session not in equity]
    if missing:
        raise ValueError(f"no equity at rebalance sessions {missing}")
    return [equity[b] / equity[a] - 1 for a, b in pairwise(ends)]


def metric_rows(results: Mapping[float, BacktestResult], params: Settings) -> list[MetricRow]:
    """Every req 7 metric per series and level, as `trial_metrics` rows."""
    _check(results, params)
    gross = results[GROSS_LEVEL]
    rows: list[MetricRow] = []
    for level in sorted(results):
        result = results[level]
        ends = _month_ends(result)
        monthly = {s: _monthly(_series_equity(result, s), ends) for s in SERIES}
        for series in SERIES:
            values = series_metrics(
                series,
                monthly=monthly[series],
                gross_monthly=_monthly(_series_equity(gross, series), ends),
                daily_equity=[row.equity for row in _series_equity(result, series)],
                turnover=(
                    [r.turnover for r in result.rebalances] if series == STRATEGY_SERIES else []
                ),
                spy_monthly=monthly[SPY_SERIES],
                mtum_monthly=monthly[MTUM_SERIES],
                risk_free_rate=params.metrics.risk_free_rate,
            )
            rows.extend(MetricRow(series, level, key, value) for key, value in values.items())
    return rows


def _gap_max(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def result_statistics(
    base_metrics: Mapping[str, float | None],
    base_rebalances: Sequence[RebalanceRow],
    family: FamilySharpes,
    params: Settings,
) -> ResultStatistics:
    """The `ok` result row's statistics from the base-level strategy metrics, the
    family's N and pair Sharpes (this trial included when it counts) and the base
    level's rebalances (module docstring, step 4)."""
    n = family.n_trials
    raw = deflated_sharpe(base_metrics, "raw", n_trials=max(n, 1), pair_sharpes=family.raw)
    excess = deflated_sharpe(
        base_metrics, "excess_spy", n_trials=max(n, 1), pair_sharpes=family.excess_spy
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


def write_results(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    results: Mapping[float, BacktestResult],
    params: Settings,
    family_sharpes: FamilySharpesFn = registry.family_sharpes,
) -> str:
    """Write one run's rows for `handle` and its result row; return the recorded status,
    `"ok"`, or `"failed"` when the store changed during the run (module docstring).

    `results` is the engine's output keyed by per-side bps; `params` is the trial's
    frozen `Settings`. Raises `ValueError` before any write when `params` are not those
    frozen settings, or a level or benchmark is missing. Runs in the caller's write
    transaction.
    """
    _check_frozen(handle, params)
    rows = metric_rows(results, params)
    base_level = params.costs.per_side_bps
    base = results[base_level]
    registry.write_metrics(conn, handle, rows)
    for level in sorted(results):
        registry.write_equity(conn, handle, results[level].equity)
        registry.write_rebalances(conn, handle, results[level].rebalances)
    registry.write_weights(conn, handle, base.weights)
    base_metrics = {
        row.metric: row.value
        for row in rows
        if row.series == STRATEGY_SERIES and row.cost_per_side_bps == base_level
    }
    family = family_sharpes(conn, handle.family, pending=handle)
    stats = result_statistics(base_metrics, base.rebalances, family, params)
    return registry.write_result(conn, handle, stats)

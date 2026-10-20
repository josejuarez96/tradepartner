"""The engine applies the buy/hold buffer (backtest spec amendment #657, items 2 to 5;
plan T239).

Locked tests (split trial, phase 1): written before the build, against the plan line's
Tests clause. The first section is the pin: on `main`'s code, before any buffer wiring,
the momentum fixture twin's run digests, rebalance rows and reference metrics are
literals, and the runs with `strategy.hold_top_fraction` absent and with it equal to
`strategy.top_fraction` (normalised to 0.0) are byte-identical to them. The later
sections are the new behaviour: a hand fixture over three rebalances, the public
`plan` guard, and the successor-chain case.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import polars as pl

from backtest import test_engine, test_engine_hold
from backtest.fake_provider import FakeProvider
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.results import metric_rows
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.config import Settings

# --- the fixture twin's pins, taken on `main`'s code (plan T239's first commit) --------


def _with(settings: Settings, section: str, **updates: Any) -> Settings:
    """`settings` with `section`'s fields replaced, validated as any construction is."""
    values = settings.model_dump()
    values[section] = {**values[section], **updates}
    return Settings(_env_file=None, **values)


#: SHA-256 over the twin's rebalance rows (each row's `columns()` plus its counts
#: mapping, floats rounded to nine places), taken on `main` before the buffer landed.
TWIN_REBALANCE_DIGESTS = {
    0.0: "3d07ef6b4fda8ffc57e8810f4aa7828c42859dceea6c617e694f26af7533d502",
    30.0: "669469ced894a1c17312c6c780b78aa963ac076e21d87b0ddedf65f199935b22",
    60.0: "9f1933fcf739b90f34da472a14d4a28c0eb2ccd20eedd78e38ea206f4bdb3c84",
    100.0: "54d4cb26ac2bc509a14092919460cc7740420e8477c6b5c7d86edf2573dacb88",
}


def _twin_results(settings: Settings) -> dict[float, BacktestResult]:
    levels = tuple(settings.costs.sensitivity_per_side_bps)
    return run(
        settings,
        test_engine._provider(),
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )


def _rebalances_digest(result: BacktestResult) -> str:
    payload = [
        test_engine._rounded({**row.columns(), "counts": row.counts}) for row in result.rebalances
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _twin_metrics_digest(settings: Settings) -> str:
    """`test_engine_hold`'s reference-metrics digest (the twin's benchmark provider)."""
    base = test_engine._price_rows()
    extra = test_engine_hold._prices(
        test_engine_hold._closes(test_engine_hold._BENCHMARK_GROWTH, {}, test_engine.SESSIONS)
    )
    provider = FakeProvider(
        prices=pl.concat([base, extra]),
        members={
            session: sorted(test_engine.GROWTH)
            for session in rebalance_sessions(test_engine.T0, test_engine.HISTORY_END)
        },
        benchmarks=test_engine_hold._BENCHMARKS,
    )
    levels = sorted({settings.costs.per_side_bps, *settings.costs.sensitivity_per_side_bps})
    results = run(
        settings,
        provider,
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )
    rows = metric_rows(results, settings, cadence="month_end", family="momentum")
    payload = sorted(
        (row.series, row.cost_per_side_bps, row.metric, test_engine._rounded(row.value))
        for row in rows
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _twin_settings_variants() -> Sequence[Settings]:
    """The twin with `strategy.hold_top_fraction` absent, and named equal to
    `strategy.top_fraction` (normalised to 0.0 by the validator): one hypothesis."""
    default = test_engine._twin_settings()
    assert default.strategy.hold_top_fraction == 0.0
    at_top = _with(default, "strategy", hold_top_fraction=default.strategy.top_fraction)
    assert at_top.strategy.hold_top_fraction == 0.0
    return (default, at_top)


def test_twin_run_digests_and_rebalance_rows_pinned_at_default_and_hold_top() -> None:
    """The twin's run digests and rebalance rows stay byte-identical with the key
    absent and with it equal to `top_fraction` (the plan line's pin)."""
    for settings in _twin_settings_variants():
        results = _twin_results(settings)
        digests = {level: test_engine._run_digest(result) for level, result in results.items()}
        assert digests == test_engine.TWIN_RUN_DIGESTS
        rebalances = {level: _rebalances_digest(result) for level, result in results.items()}
        assert rebalances == TWIN_REBALANCE_DIGESTS
        assert len(results[0.0].rebalances) == 4


def test_twin_reference_metrics_pinned_at_default_and_hold_top() -> None:
    for settings in _twin_settings_variants():
        assert _twin_metrics_digest(settings) == test_engine_hold.TWIN_REFERENCE_METRICS_SHA256

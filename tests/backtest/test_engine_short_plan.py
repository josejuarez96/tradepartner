"""T223's shortability, drift cover and reported borrow books."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from backtest import test_engine, test_engine_short
from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.engine import plan, run, run_many
from tradepartner.backtest.strategies import SignalResult, Strategy
from tradepartner.calendar import session_close
from tradepartner.config import Settings


def _settings(**portfolio: Any) -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.25},
        portfolio={
            "side": "long_short",
            "gross_exposure": 1.0,
            "net_exposure": 0.0,
            **portfolio,
        },
        costs={"per_side_bps": 0.0, "sensitivity_per_side_bps": [0.0]},
    )


def _scores(monkeypatch: pytest.MonkeyPatch) -> None:
    def score(*args: Any) -> SignalResult:
        return SignalResult({"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}, {}, {})

    strategy = Strategy((), lambda *args: None, score, (), (), "strategy")  # type: ignore[arg-type]
    monkeypatch.setattr(engine, "signal_for", lambda family: strategy)


def _plan_provider(raw: pl.DataFrame) -> FakeProvider:
    return FakeProvider(
        prices=raw,
        raw=raw,
        members={test_engine_short.T0: ["A", "B", "C", "D"]},
        benchmarks={},
    )


def test_shortability_uses_raw_close_of_scored_names_and_counts_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scores(monkeypatch)
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 10.0)],
            "C": [(test_engine_short.HISTORY_START, 4.99)],
            "D": [(test_engine_short.HISTORY_START, 5.0)],
        }
    )
    provider = _plan_provider(bars)
    got = plan(provider, _settings(), test_engine_short.T0, "momentum")
    assert got.targets == {"A": 0.5, "D": -0.5}
    assert got.counts["n_short_below_price"] == 1
    raw_calls = [c for c in provider.calls if c.method == "raw_prices"]
    assert len(raw_calls) == 1
    assert raw_calls[0].t == session_close(test_engine_short.T0)
    assert raw_calls[0].ids == ("A", "B", "C", "D")


def test_unpriced_scored_name_is_ineligible_but_not_counted_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scores(monkeypatch)
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 10.0)],
            "C": [(test_engine_short.HISTORY_START, 4.0)],
            "D": [(test_engine_short.HISTORY_START, 5.0)],
        }
    ).filter(~((pl.col("security_id") == "C") & (pl.col("session") == test_engine_short.T0)))
    got = plan(_plan_provider(bars), _settings(), test_engine_short.T0, "momentum")
    assert got.targets == {"A": 0.5, "D": -0.5}
    assert got.counts["n_short_below_price"] == 0


def _borrow_params(headline: float, sensitivity: list[float]) -> Settings:
    base = test_engine_short._signed(
        costs={
            "per_side_bps": 0.0,
            "sensitivity_per_side_bps": [0.0],
            "borrow_bps_per_year": headline,
            "borrow_sensitivity_bps": sensitivity,
        }
    )
    return base


def _flat_provider() -> FakeProvider:
    return test_engine_short._provider(
        test_engine_short._bars(
            {
                "A": [(test_engine_short.HISTORY_START, 100.0)],
                "B": [(test_engine_short.HISTORY_START, 100.0)],
            }
        )
    )


def _strategy_rows(
    result: engine.BacktestResult, series: str
) -> tuple[tuple[date, float, float | None], ...]:
    return tuple(
        (row.session, row.equity, row.cash) for row in result.equity if row.series == series
    )


def test_borrow_books_match_separate_headline_runs_and_drop_duplicate_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    settings = _borrow_params(30.0, [0.0, 30.0, 2.5, 0.0, 300.0, 2.5])
    base = run(
        settings,
        _flat_provider(),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    for rate in (0.0, 30.0, 2.5, 300.0):
        alone = run(
            _borrow_params(rate, []),
            _flat_provider(),
            test_engine_short.T0,
            test_engine_short.T1,
            test_engine._handle(),
            [0.0],
            family="momentum",
        )[0.0]
        name = f"strategy_borrow_{rate:g}bp"
        assert _strategy_rows(base, name) == _strategy_rows(alone, engine.STRATEGY_SERIES)
        assert len(_strategy_rows(base, name)) == len(_strategy_rows(base, engine.STRATEGY_SERIES))
    assert [r.series for r in base.equity if r.session == test_engine_short.F0].count(
        "strategy_borrow_0bp"
    ) == 1


def test_run_many_signed_books_equal_separate_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    first = _borrow_params(30.0, [0.0, 300.0])
    second = _borrow_params(300.0, [0.0, 30.0])
    shared = run_many(
        [(first, test_engine._handle(1)), (second, test_engine._handle(2))],
        _flat_provider(),
        test_engine_short.T0,
        test_engine_short.T1,
        [0.0],
        family="momentum",
    )
    assert not shared.failures
    for trial_id, params in ((1, first), (2, second)):
        separate = run(
            params,
            _flat_provider(),
            test_engine_short.T0,
            test_engine_short.T1,
            test_engine._handle(trial_id),
            [0.0],
            family="momentum",
        )[0.0]
        got = shared[trial_id][0.0]
        assert got.equity == separate.equity
        assert got.rebalances == separate.rebalances
        assert got.targets == separate.targets
        assert got.position_values.equals(separate.position_values)


def test_run_many_with_real_proxy_plans_equals_separate_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scores(monkeypatch)
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 10.0)],
            "C": [(test_engine_short.HISTORY_START, 4.0)],
            "D": [(test_engine_short.HISTORY_START, 5.0)],
        }
    )
    first = _settings(short_cover_weight=1.0)
    second = _settings(short_cover_weight=1.0, short_min_price=6.0)
    shared = run_many(
        [(first, test_engine._handle(1)), (second, test_engine._handle(2))],
        _plan_provider(bars),
        test_engine_short.T0,
        test_engine_short.T1,
        [0.0],
        family="momentum",
    )
    assert not shared.failures
    for trial_id, params in ((1, first), (2, second)):
        separate = run(
            params,
            _plan_provider(bars),
            test_engine_short.T0,
            test_engine_short.T1,
            test_engine._handle(trial_id),
            [0.0],
            family="momentum",
        )[0.0]
        got = shared[trial_id][0.0]
        assert got.equity == separate.equity
        assert got.rebalances == separate.rebalances
        assert got.targets == separate.targets
        assert got.position_values.equals(separate.position_values)
    assert shared[1][0.0].targets != shared[2][0.0].targets


def test_long_only_defaults_preserve_the_existing_twin_digest() -> None:
    settings = test_engine._twin_settings()
    result = run(
        settings,
        test_engine._provider(),
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        tuple(settings.costs.sensitivity_per_side_bps),
        family="momentum",
    )
    assert {
        level: test_engine._run_digest(book) for level, book in result.items()
    } == test_engine.TWIN_RUN_DIGESTS


SPIKE = date(2024, 2, 2)
COVER = date(2024, 2, 5)


def _spike_provider(*, revision: bool = False) -> FakeProvider:
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 100.0), (SPIKE, 120.0), (COVER, 140.0)],
        }
    )
    bars = bars.with_columns(
        pl.when((pl.col("security_id") == "B") & (pl.col("session") == COVER))
        .then(130.0)
        .otherwise(pl.col("open"))
        .alias("open")
    )
    if revision:
        revised = bars.filter((pl.col("security_id") == "B") & (pl.col("session") == SPIKE))
        revised = revised.with_columns(
            pl.lit(100.0).alias("close"),
            pl.lit(session_close(SPIKE) + timedelta(days=1))
            .cast(pl.Datetime("us", "UTC"))
            .alias("known_at"),
        )
        bars = pl.concat([bars, revised])
    return test_engine_short._provider(bars)


def test_spike_covers_at_next_open_with_hand_equity_and_one_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    base = _borrow_params(0.0, [])
    params = base.model_copy(
        update={
            "portfolio": base.portfolio.model_copy(update={"short_cover_weight": 0.6}),
            "execution": base.execution.model_copy(update={"fill_price": "open"}),
        }
    )
    result = run(
        params,
        _spike_provider(),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    equity = test_engine_short._equity(result)
    assert equity[SPIKE] == pytest.approx(90_000.0, abs=1e-9)
    # 500 shares short at $100 are bought at the next $130 open, not its $140 close.
    assert equity[COVER] == pytest.approx(85_000.0, abs=1e-9)
    assert test_engine_short._cash(result)[COVER] == pytest.approx(35_000.0, abs=1e-9)
    assert test_engine_short._value(result, "B", COVER) == 0.0
    assert result.rebalances[0].counts["n_drift_covers"] == 1
    assert result.exposure_summary is not None
    assert result.exposure_summary.gross_max == pytest.approx(110_000 / 90_000)
    assert result.exposure_summary.net_min == pytest.approx(-10_000 / 90_000)
    assert result.exposure_summary.borrow_paid == 0.0


def test_drift_cover_charges_the_cover_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    params = _borrow_params(0.0, []).model_copy(
        update={
            "portfolio": test_engine_short._signed().portfolio.model_copy(
                update={"short_cover_weight": 0.6}
            ),
            "execution": test_engine_short._signed().execution.model_copy(
                update={"fill_price": "open"}
            ),
        }
    )
    params = params.model_copy(
        update={"costs": params.costs.model_copy(update={"per_side_bps": 10.0})}
    )
    result = run(
        params,
        _spike_provider(),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [10.0],
        family="momentum",
    )[10.0]
    assert result.rebalances[0].counts["n_drift_covers"] == 1
    # Short open $50,000 costs $50; the cash-capped long costs $50/1.001;
    # 500 short shares covered at $130 cost $65.
    assert result.rebalances[0].cost_paid == pytest.approx(50 + 50 / 1.001 + 65, abs=1e-9)


def test_held_short_halted_across_rebalance_keeps_last_mark_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The as-of decision read needs the held bar before the rebalance session."""
    test_engine_short._install_plan(
        monkeypatch,
        {
            test_engine_short.T0: {"A": 0.5, "B": -0.5},
            test_engine_short.T1: {"A": 0.5, "B": -0.5},
        },
    )
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 100.0)],
        }
    ).filter(
        ~(
            (pl.col("security_id") == "B")
            & pl.col("session").is_between(date(2024, 2, 27), test_engine_short.F1)
        )
    )
    result = run(
        _borrow_params(0.0, []),
        test_engine_short._provider(bars),
        test_engine_short.T0,
        test_engine_short.T2,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert test_engine_short._value(result, "B", test_engine_short.F1) == -50_000.0
    assert result.rebalances[1].counts["n_drift_covers"] == 0


def test_drift_cover_waits_for_next_bar_inside_step(monkeypatch: pytest.MonkeyPatch) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    resume = date(2024, 2, 7)
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [
                (test_engine_short.HISTORY_START, 100.0),
                (SPIKE, 150.0),
                (resume, 130.0),
            ],
        }
    ).filter(
        ~(
            (pl.col("security_id") == "B")
            & pl.col("session").is_between(date(2024, 2, 5), date(2024, 2, 6))
        )
    )
    base = _borrow_params(0.0, [])
    params = base.model_copy(
        update={"portfolio": base.portfolio.model_copy(update={"short_cover_weight": 0.6})}
    )
    result = run(
        params,
        test_engine_short._provider(bars),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert test_engine_short._value(result, "B", date(2024, 2, 5)) == -75_000.0
    assert test_engine_short._value(result, "B", date(2024, 2, 6)) == -75_000.0
    assert test_engine_short._value(result, "B", resume) == 0.0
    assert test_engine_short._cash(result)[resume] == pytest.approx(35_000.0, abs=1e-9)
    assert result.rebalances[0].counts["n_drift_covers"] == 1


def test_drift_cover_waits_for_bar_after_rebalance(monkeypatch: pytest.MonkeyPatch) -> None:
    test_engine_short._install_plan(
        monkeypatch,
        {test_engine_short.T0: {"A": 0.5, "B": -0.5}, test_engine_short.T1: {}},
    )
    resume = date(2024, 3, 4)
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [
                (test_engine_short.HISTORY_START, 100.0),
                (test_engine_short.T1, 150.0),
                (resume, 130.0),
            ],
        }
    ).filter(~((pl.col("security_id") == "B") & (pl.col("session") == test_engine_short.F1)))
    base = _borrow_params(0.0, [])
    params = base.model_copy(
        update={"portfolio": base.portfolio.model_copy(update={"short_cover_weight": 0.6})}
    )
    result = run(
        params,
        test_engine_short._provider(bars),
        test_engine_short.T0,
        test_engine_short.T2,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert test_engine_short._value(result, "B", test_engine_short.F1) == -75_000.0
    assert test_engine_short._value(result, "B", resume) == 0.0
    # The T1 plan also sells A for $50,000; covering B at $130 then leaves $85,000.
    assert test_engine_short._cash(result)[resume] == pytest.approx(85_000.0, abs=1e-9)
    assert result.rebalances[1].counts["n_drift_covers"] == 1


def test_stale_exit_clears_unfilled_drift_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    bars = test_engine_short._bars(
        {
            "A": [(test_engine_short.HISTORY_START, 100.0)],
            "B": [(test_engine_short.HISTORY_START, 100.0), (SPIKE, 150.0)],
        }
    ).filter(~((pl.col("security_id") == "B") & (pl.col("session") > SPIKE)))
    base = _borrow_params(0.0, [])
    params = base.model_copy(
        update={"portfolio": base.portfolio.model_copy(update={"short_cover_weight": 0.6})}
    )
    result = run(
        params,
        test_engine_short._provider(bars),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert result.rebalances[0].n_stale_exits == 1
    assert result.rebalances[0].counts["n_drift_covers"] == 0
    assert test_engine_short._value(result, "B", test_engine_short.T1) == 0.0

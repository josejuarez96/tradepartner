"""T223 shortability and drift decisions use only information known at each decision close."""

from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest
from backtest import test_engine, test_engine_short
from backtest.test_engine_short_plan import (
    COVER,
    _plan_provider,
    _scores,
    _settings,
    _spike_provider,
)

from tradepartner.backtest import engine
from tradepartner.calendar import session_close


def test_late_raw_bar_revision_does_not_change_proxy_or_targets(
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
    row = bars.filter((pl.col("security_id") == "C") & (pl.col("session") == test_engine_short.T0))
    late = row.with_columns(
        pl.lit(40.0).alias("close"),
        pl.lit(session_close(test_engine_short.T0) + timedelta(days=1))
        .cast(pl.Datetime("us", "UTC"))
        .alias("known_at"),
    )
    clean = engine.plan(_plan_provider(bars), _settings(), test_engine_short.T0, "momentum")
    revised = engine.plan(
        _plan_provider(pl.concat([bars, late])), _settings(), test_engine_short.T0, "momentum"
    )
    assert clean.targets == revised.targets == {"A": 0.5, "D": -0.5}
    assert clean.counts["n_short_below_price"] == revised.counts["n_short_below_price"] == 1


def test_revision_after_spike_close_cannot_cancel_next_session_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_engine_short._install_plan(monkeypatch, {test_engine_short.T0: {"A": 0.5, "B": -0.5}})
    from backtest.test_engine_short_plan import _borrow_params

    base = _borrow_params(0.0, [])
    params = base.model_copy(
        update={
            "portfolio": base.portfolio.model_copy(update={"short_cover_weight": 0.6}),
            "execution": base.execution.model_copy(update={"fill_price": "open"}),
        }
    )
    clean = engine.run(
        params,
        _spike_provider(),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    revised = engine.run(
        params,
        _spike_provider(revision=True),
        test_engine_short.T0,
        test_engine_short.T1,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert (
        clean.rebalances[0].counts["n_drift_covers"]
        == revised.rebalances[0].counts["n_drift_covers"]
        == 1
    )
    assert (
        test_engine_short._cash(clean)[COVER]
        == test_engine_short._cash(revised)[COVER]
        == pytest.approx(35_000.0)
    )
    assert test_engine_short._value(revised, "B", COVER) == 0.0

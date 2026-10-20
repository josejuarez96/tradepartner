"""Hand-value tests for the pure performance arithmetic (plan T250, #2006)."""

from __future__ import annotations

from datetime import date

import pytest

from tradepartner.execution import performance as perf

D1, D2, D3 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)
PAPER = {D1: 100_000.00, D2: 101_500.00, D3: 99_200.00}
SPY = {D1: 5_000.0, D2: 5_150.0, D3: 5_050.0}


def _vals(rows: list[tuple[date, float]]) -> list[float]:
    return [v for _, v in rows]


def test_base_value_is_the_value_at_the_base_session() -> None:
    assert perf.base_value(PAPER, D1) == 100_000.00
    assert perf.base_value(PAPER, D2) == 101_500.00


def test_base_value_missing_session_raises_naming_it() -> None:
    with pytest.raises(ValueError, match="2026-10-20"):
        perf.base_value(PAPER, date(2026, 10, 20))


def test_rebase_to_100() -> None:
    rows = perf.rebase(PAPER, 100_000.00)
    assert [s for s, _ in rows] == [D1, D2, D3]
    assert _vals(rows) == pytest.approx([100.00, 101.50, 99.20], abs=1e-9)


def test_rebase_sorts_by_session() -> None:
    rows = perf.rebase({D3: 99_200.0, D1: 100_000.0}, 100_000.0)
    assert [s for s, _ in rows] == [D1, D3]


def test_rebase_zero_base_raises() -> None:
    with pytest.raises(ValueError, match="positive"):
        perf.rebase(PAPER, 0.0)


def test_trial_series_over_shorter_span_rebases_on_its_own_base() -> None:
    trial = {D2: 2_000.0, D3: 2_100.0}
    rows = perf.rebase(trial, perf.base_value(trial, D2))
    assert _vals(rows) == pytest.approx([100.0, 105.0], abs=1e-9)


def test_daily_pnl() -> None:
    rows = perf.daily_pnl(PAPER)
    assert [s for s, _ in rows] == [D2, D3]
    assert _vals(rows) == pytest.approx([1_500.00, -2_300.00], abs=1e-9)


def test_cumulative_return_pct() -> None:
    assert perf.cumulative_return_pct(PAPER) == pytest.approx(-0.8, abs=1e-9)


def test_cumulative_return_against_explicit_base() -> None:
    # base value 99,000 (starting equity before the first mark)
    assert perf.cumulative_return_pct(PAPER, 99_000.0) == pytest.approx(
        100 * (99_200 / 99_000 - 1), abs=1e-9
    )


def test_excess_over_benchmark_hand_values() -> None:
    rows = perf.excess_over_benchmark(PAPER, SPY)
    assert [s for s, _ in rows] == [D1, D2, D3]
    assert _vals(rows) == pytest.approx([0.00, -1.50, -1.80], abs=1e-9)
    # the benchmark rebased with the paper series to one base
    assert _vals(perf.rebase(SPY, 5_000.0)) == pytest.approx([100.0, 103.0, 101.0], abs=1e-9)


def test_excess_sign_paper_ahead_is_positive() -> None:
    rows = perf.excess_over_benchmark({D1: 100.0, D2: 110.0}, {D1: 100.0, D2: 105.0})
    assert _vals(rows)[1] == pytest.approx(5.0, abs=1e-9)
    assert _vals(rows)[1] > 0


def test_excess_ignores_extra_benchmark_sessions() -> None:
    bench = {**SPY, date(2026, 10, 19): 9_999.0, date(2026, 10, 13): 1.0}
    rows = perf.excess_over_benchmark(PAPER, bench)
    assert [s for s, _ in rows] == [D1, D2, D3]
    assert _vals(rows) == pytest.approx([0.00, -1.50, -1.80], abs=1e-9)


def test_excess_missing_benchmark_session_raises_naming_it() -> None:
    bench = {D1: 5_000.0, D3: 5_050.0}
    with pytest.raises(ValueError, match="2026-10-15"):
        perf.excess_over_benchmark(PAPER, bench)


def test_one_session_series_is_flat_with_zero_pnl() -> None:
    one = {D1: 100_000.0}
    assert _vals(perf.rebase(one, 100_000.0)) == [100.0]
    assert perf.daily_pnl(one) == []
    assert perf.cumulative_return_pct(one) == 0.0
    assert _vals(perf.excess_over_benchmark(one, {D1: 5_000.0})) == [0.0]


def test_empty_series_returns_empty_never_raises() -> None:
    assert perf.rebase({}, 100.0) == []
    assert perf.daily_pnl({}) == []
    assert perf.cumulative_return_pct({}) is None
    assert perf.excess_over_benchmark({}, SPY) == []
    assert perf.excess_over_benchmark({}, {}) == []


def test_one_explicit_base_alone_raises() -> None:
    with pytest.raises(ValueError, match="both bases"):
        perf.excess_over_benchmark(PAPER, SPY, paper_base=99_000.0)
    with pytest.raises(ValueError, match="both bases"):
        perf.excess_over_benchmark(PAPER, SPY, benchmark_base=5_000.0)


def test_nan_and_negative_base_raise() -> None:
    for bad in (float("nan"), -1.0):
        with pytest.raises(ValueError, match="positive"):
            perf.rebase(PAPER, bad)

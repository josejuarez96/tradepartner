"""Tests for `tradepartner.backtest.portfolio` (backtest spec reqs 3 and 4, plan T34)."""

from __future__ import annotations

import math

import pytest

from tradepartner.backtest.portfolio import drifted_weights, target_weights, trades_from


def _scores(n: int) -> dict[str, float]:
    return {f"S{i:04d}": float(i) for i in range(n)}


def test_top_fraction_picks_the_highest_scores_equal_weight() -> None:
    w = target_weights({"A": 0.1, "B": 0.5, "C": -0.2, "D": 0.3}, 0.5, "equal")
    assert w == {"B": 0.5, "D": 0.5}


@pytest.mark.parametrize(
    ("n", "fraction", "k"),
    [(1000, 0.10, 100), (30, 0.10, 3), (15, 0.10, 2), (5, 0.10, 1), (10, 1.0, 10), (7, 0.3, 3)],
)
def test_number_selected_is_the_ceiling_without_float_error(
    n: int, fraction: float, k: int
) -> None:
    """0.1 * 30 is 3.0000000000000004 in floating point; that must still select 3."""
    assert len(target_weights(_scores(n), fraction, "equal")) == k


@pytest.mark.parametrize("n", [1, 2, 3, 7, 10, 49, 99, 100, 101, 333, 997, 1000])
def test_weights_non_negative_equal_and_sum_at_most_one(n: int) -> None:
    w = target_weights(_scores(n), 1.0, "equal")
    values = list(w.values())
    assert all(v >= 0 for v in values)
    assert len(set(values)) == 1
    assert sum(values) <= 1.0
    assert math.fsum(values) <= 1.0
    assert math.fsum(values) == pytest.approx(1.0, abs=1e-12)


def test_ties_rank_by_security_id_ascending() -> None:
    scores = {"D": 0.2, "B": 0.2, "C": 0.2, "A": 0.1}
    assert set(target_weights(scores, 0.5, "equal")) == {"B", "C"}


def test_tie_order_does_not_depend_on_input_order() -> None:
    a = {"X": 1.0, "M": 1.0, "B": 1.0, "Q": 0.0}
    b = dict(reversed(list(a.items())))
    assert target_weights(a, 0.5, "equal") == target_weights(b, 0.5, "equal")
    assert set(target_weights(a, 0.5, "equal")) == {"B", "M"}


def test_empty_score_set_gives_all_cash() -> None:
    assert target_weights({}, 0.1, "equal") == {}


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5, float("nan")])
def test_top_fraction_out_of_range_refused(fraction: float) -> None:
    with pytest.raises(ValueError):
        target_weights(_scores(10), fraction, "equal")


def test_unknown_weighting_refused() -> None:
    with pytest.raises(ValueError):
        target_weights(_scores(10), 0.1, "cap")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_score_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        target_weights({"A": 0.1, "B": bad}, 0.5, "equal")


# --- drifted weights and trades ---------------------------------------------------


def test_drifted_weights_are_values_over_equity() -> None:
    w = drifted_weights({"A": 60.0, "B": 30.0}, cash=10.0)
    assert w == pytest.approx({"A": 0.6, "B": 0.3})


def test_drifted_weights_all_cash() -> None:
    assert drifted_weights({}, cash=100.0) == {}


@pytest.mark.parametrize(
    ("values", "cash"),
    [({"A": -1.0}, 10.0), ({"A": 1.0}, -0.5), ({}, 0.0), ({"A": float("nan")}, 1.0)],
)
def test_drifted_weights_refuses_bad_inputs(values: dict[str, float], cash: float) -> None:
    with pytest.raises(ValueError):
        drifted_weights(values, cash=cash)


def test_trades_are_target_minus_drifted_over_the_union() -> None:
    trades = trades_from({"A": 0.5, "C": 0.5}, {"A": 0.6, "B": 0.3})
    assert trades == pytest.approx({"A": -0.1, "B": -0.3, "C": 0.5})


def test_trades_drop_exact_zeros() -> None:
    assert trades_from({"A": 0.5}, {"A": 0.5}) == {}


def test_trades_ordered_by_security_id() -> None:
    assert list(trades_from({"C": 0.5, "A": 0.5}, {"B": 1.0})) == ["A", "B", "C"]

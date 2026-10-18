"""Tests for `tradepartner.backtest.portfolio` (backtest spec reqs 3 and 4, plan T34)."""

from __future__ import annotations

import math

import pytest

from tradepartner.backtest.portfolio import (
    _selected_count,
    apply_rebalance_band,
    drifted_weights,
    hold_membership,
    target_weights,
    trades_from,
)


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


@pytest.mark.parametrize("n", [1, 2, 30, 1000])
@pytest.mark.parametrize("fraction", [1e-7, 5e-7, 1e-12, 5e-324])
def test_a_tiny_top_fraction_still_selects_one_name(n: int, fraction: float) -> None:
    """#1446: a valid but tiny fraction used to round to 0 and divide by zero."""
    assert target_weights(_scores(n), fraction, "equal") == {f"S{n - 1:04d}": 1.0}


@pytest.mark.parametrize("fraction", [1e-7, 0.1, 1.0])
def test_an_empty_set_selects_none(fraction: float) -> None:
    assert _selected_count(0, fraction) == 0


@pytest.mark.parametrize(
    ("n", "fraction", "k"),
    [(30, 0.05, 2), (30, 0.10, 3), (30, 0.20, 6), (2000, 0.05, 100), (2000, 0.20, 400)],
)
def test_registered_fractions_keep_their_counts(n: int, fraction: float, k: int) -> None:
    """The registered values (0.05, 0.10, 0.20) select what they selected before #1446."""
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


# --- the no-trade band (backtest spec amendment #1731; T209a) -----------------------


def test_band_of_zero_returns_the_targets_unchanged() -> None:
    target = {"A": 0.5, "B": 0.3, "C": 0.2}
    current = {"A": 0.52, "B": 0.28, "D": 0.05}
    assert apply_rebalance_band(target, current, 0.0) == target


def test_a_band_wider_than_every_drift_returns_the_current_weights() -> None:
    target = {"A": 0.5, "B": 0.3}
    current = {"A": 0.5, "B": 0.28}
    assert apply_rebalance_band(target, current, 0.99) == current


def test_band_suppresses_only_the_drifts_inside_it() -> None:
    # A 0.05 band on a 0.5 target is 0.025: A's 0.01 drift is inside, B's 0.1 is outside.
    # A keeps 0.51; the traded B fills the remaining 0.49 of the budget.
    target = {"A": 0.5, "B": 0.5}
    current = {"A": 0.51, "B": 0.4}
    assert apply_rebalance_band(target, current, 0.05) == {"A": 0.51, "B": 0.49}


def test_band_never_leaves_weights_summing_above_one() -> None:
    """A suppressed held name can over-fill the target; the traded names are scaled into
    what is left of the budget (reviewer's repro on #1788)."""
    effective = apply_rebalance_band({"A": 0.5, "B": 0.5}, {"A": 0.52, "B": 0.47}, 0.05)
    assert effective == {"A": 0.52, "B": 0.48}
    assert math.fsum(effective.values()) <= 1.0


def test_band_never_scales_a_traded_name_above_its_target() -> None:
    """Scaling only shrinks: a partly invested book keeps the rest in cash (reviewer's
    pass-2 repro on #1788)."""
    assert apply_rebalance_band({"A": 0.4, "B": 0.4}, {}, 0.05) == {"A": 0.4, "B": 0.4}
    assert apply_rebalance_band({"A": 0.5, "B": 0.3}, {"A": 0.49, "B": 0.1}, 0.05) == {
        "A": 0.49,
        "B": 0.3,
    }


def test_band_always_exits_a_name_absent_from_target() -> None:
    """A held name with no target has a zero band, so it is always sold."""
    assert apply_rebalance_band({"A": 1.0}, {"A": 0.9, "B": 0.1}, 0.99) == {"A": 0.9}


def test_a_band_of_zero_reproduces_todays_trades() -> None:
    target = {"A": 0.5, "C": 0.5}
    current = {"A": 0.6, "B": 0.3}
    assert trades_from(apply_rebalance_band(target, current, 0.0), current) == trades_from(
        target, current
    )


@pytest.mark.parametrize("band", [-0.1, 1.0, 1.5, float("nan"), float("inf")])
def test_band_out_of_range_refused(band: float) -> None:
    with pytest.raises(ValueError, match="band"):
        apply_rebalance_band({"A": 1.0}, {"A": 1.0}, band)


# --- hold-mode membership (backtest spec amendment #1731; T209a) --------------------


def test_hold_fills_the_target_from_the_rank_order() -> None:
    step = hold_membership(
        ("X", "Y", "Z"), {}, session=0, hold_sessions=3, hold_target_names=2, top_fraction=0.5
    )
    assert step.entering == ("X", "Y")
    assert step.exiting == ()
    assert step.held == ("X", "Y")
    assert step.entries == {"X": 0, "Y": 0}


def test_hold_exits_a_name_on_session_n_and_no_earlier() -> None:
    ranked, entries = ("Z",), {"X": 0}
    second = hold_membership(ranked, entries, 2, 3, 1, 1.0)
    assert second.exiting == ()
    assert second.held == ("X",)
    third = hold_membership(ranked, entries, 3, 3, 1, 1.0)
    assert third.exiting == ("X",)
    assert third.held == ("Z",)
    assert third.entries == {"Z": 3}


def test_hold_keeps_live_names_beyond_the_target_in_cash() -> None:
    """A book with more live names than its target keeps them all; it never force-exits."""
    step = hold_membership(("Z",), {"A": 0, "B": 0, "C": 0}, 1, 3, 1, 1.0)
    assert step.entering == ()
    assert step.exiting == ()
    assert step.held == ("A", "B", "C")


def test_a_name_re_entering_on_its_exit_restarts_its_hold() -> None:
    step = hold_membership(("X",), {"X": 0}, 3, 3, 1, 1.0)
    assert step.exiting == ("X",)
    assert step.entering == ("X",)
    assert step.held == ("X",)
    assert step.entries == {"X": 3}


def test_hold_target_names_zero_means_the_top_fraction_count() -> None:
    step = hold_membership(
        ("A", "B", "C", "D"), {}, session=0, hold_sessions=5, hold_target_names=0, top_fraction=0.5
    )
    assert step.entering == ("A", "B")
    assert step.held == ("A", "B")


@pytest.mark.parametrize(("hold_sessions", "session"), [(0, 0), (-1, 0), (3, -1)])
def test_hold_membership_refuses_bad_inputs(hold_sessions: int, session: int) -> None:
    with pytest.raises(ValueError):
        hold_membership(("X",), {}, session, hold_sessions, 1, 1.0)

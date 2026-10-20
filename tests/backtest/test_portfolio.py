"""Tests for `tradepartner.backtest.portfolio` (backtest spec reqs 3 and 4, plan T34)."""

from __future__ import annotations

import math

import pytest

from tradepartner.backtest.portfolio import (
    _selected_count,
    apply_rebalance_band,
    buffered_targets,
    drifted_weights,
    hold_membership,
    signed_target_weights,
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


# --- the buy/hold buffer's retention rule (backtest spec amendment #657, item 2) ---------


def _ranked(order: str) -> dict[str, float]:
    """Scores that rank `order`'s letters first to last (the first scores highest)."""
    return {name: float(len(order) - i) for i, name in enumerate(order)}


def test_buffered_targets_first_plan_is_the_unbuffered_top() -> None:
    """Ten names A to J, top 0.2 and hold 0.4, P = empty: the top 2, 1/2 each."""
    targets, n_retained = buffered_targets(_ranked("ABCDEFGHIJ"), set(), 0.2, 0.4)
    assert targets == {"A": 0.5, "B": 0.5}
    assert n_retained == 0


def test_buffered_targets_keeps_between_top_and_hold_and_sells_below_hold() -> None:
    """After a re-rank to C, D, A, E, B: A (3rd, inside the hold cut of 4) is kept, B
    (5th, below it) is sold, so the book is {A, C, D} at 1/3 with one retained."""
    scores = _ranked("CDAEBFGHIJ")
    targets, n_retained = buffered_targets(scores, {"A", "B"}, 0.2, 0.4)
    assert targets == {"A": 1 / 3, "C": 1 / 3, "D": 1 / 3}
    assert n_retained == 1


def test_buffered_targets_does_not_retain_a_previous_name_absent_from_the_scores() -> None:
    targets, n_retained = buffered_targets(_ranked("CDAEBFGHIJ"), {"A", "ZZ"}, 0.2, 0.4)
    assert targets == {"A": 1 / 3, "C": 1 / 3, "D": 1 / 3}
    assert n_retained == 1
    assert "ZZ" not in targets


def test_buffered_targets_does_not_count_a_name_already_in_the_top_as_retained() -> None:
    targets, n_retained = buffered_targets(_ranked("ABCDEFGHIJ"), {"A", "B"}, 0.2, 0.4)
    assert targets == {"A": 0.5, "B": 0.5}
    assert n_retained == 0


def test_buffered_targets_ties_break_by_security_id() -> None:
    scores = {"B": 1.0, "A": 1.0, "C": 1.0, "D": 1.0}
    targets, _ = buffered_targets(scores, set(), 0.25, 0.5)
    assert targets == {"A": 1.0}
    # Hold cut is the top 2 by (score desc, id asc): A, B. B was held, so it stays.
    targets, n_retained = buffered_targets(scores, {"B"}, 0.25, 0.5)
    assert targets == {"A": 0.5, "B": 0.5}
    assert n_retained == 1


@pytest.mark.parametrize("hold", [0.0])
@pytest.mark.parametrize("previous", [set(), {"A"}, {"A", "Z"}])
@pytest.mark.parametrize(
    "scores",
    [{}, {"A": 1.0}, {"A": 0.1, "B": 0.5, "C": -0.2, "D": 0.3}, _scores(37)],
)
def test_buffered_targets_at_zero_hold_is_target_weights(
    scores: dict[str, float], previous: set[str], hold: float
) -> None:
    assert buffered_targets(scores, previous, 0.1, hold) == (
        target_weights(scores, 0.1, "equal"),
        0,
    )


def test_buffered_targets_hold_equal_to_top_is_the_unbuffered_book() -> None:
    scores = _ranked("CDAEBFGHIJ")
    targets, n_retained = buffered_targets(scores, {"A", "B"}, 0.2, 0.2)
    assert targets == target_weights(scores, 0.2, "equal")
    assert n_retained == 0


def test_buffered_targets_full_hold_keeps_every_ranked_previous_name() -> None:
    targets, n_retained = buffered_targets(_ranked("CDAEBFGHIJ"), {"A", "B", "J"}, 0.2, 1.0)
    assert set(targets) == {"A", "B", "C", "D", "J"}
    assert n_retained == 3


def test_buffered_targets_weights_sum_to_at_most_one() -> None:
    targets, _ = buffered_targets(_scores(30), {f"S{i:04d}" for i in range(24, 30)}, 0.1, 0.5)
    assert math.fsum(targets.values()) <= 1.0
    assert list(targets) == sorted(targets)


@pytest.mark.parametrize(
    ("top", "hold"), [(0.2, 0.1), (0.2, 1.5), (0.2, -0.1), (0.2, float("nan")), (0.0, 0.5)]
)
def test_buffered_targets_refuses_bad_fractions(top: float, hold: float) -> None:
    with pytest.raises(ValueError):
        buffered_targets({"A": 1.0}, set(), top, hold)


def test_buffered_targets_refuses_a_non_finite_score() -> None:
    with pytest.raises(ValueError, match="finite"):
        buffered_targets({"A": float("nan")}, set(), 0.2, 0.4)


# --- signed target weights (T219; backtest spec amendment #1872, item 2) --------------
#
# `_scores(n)` above scores S0000..S{n-1} ascending, so the rank order is
# S{n-1}, S{n-2}, ..., S0000. Every value below is computed by hand.


def test_signed_target_weights_50_50_by_hand() -> None:
    """gross 1.0, net 0.0, ten names, top_fraction 0.2: the top two are long at +0.25
    each and the bottom two short at -0.25 each, so each leg sums to 0.5."""
    scores = _scores(10)
    targets = signed_target_weights(scores, 0.2, 1.0, 0.0, eligible=scores)
    assert targets == {"S0009": 0.25, "S0008": 0.25, "S0000": -0.25, "S0001": -0.25}


def test_signed_target_weights_gross_1_net_0_4_by_hand() -> None:
    """gross 1.0 with net 0.4: the long leg is (1.0 + 0.4)/2 = 0.7 over two names, so
    +0.35 each; the short leg -(1.0 - 0.4)/2 = -0.3, so -0.15 each. Σ |w| = 1.0 and
    Σ w = 0.4."""
    scores = _scores(10)
    targets = signed_target_weights(scores, 0.2, 1.0, 0.4, eligible=scores)
    assert targets == pytest.approx({"S0009": 0.35, "S0008": 0.35, "S0000": -0.15, "S0001": -0.15})


def test_signed_target_weights_skips_an_ineligible_low_scorer() -> None:
    """Eight names, top_fraction 0.25: the long leg is S0007 and S0006 (k_L = 2), and
    the short leg's two lowest eligible names would be S0000 and S0001, but S0001 is
    ineligible (under the price floor), so S0002 takes its place."""
    scores = _scores(8)
    eligible = [sid for sid in scores if sid != "S0001"]
    targets = signed_target_weights(scores, 0.25, 1.0, 0.0, eligible=eligible)
    assert targets == {"S0007": 0.25, "S0006": 0.25, "S0000": -0.25, "S0002": -0.25}
    assert "S0001" not in targets


def test_signed_target_weights_short_leg_takes_what_remains() -> None:
    """Four names, top_fraction 0.5: k_L = 2 (S0003, S0002) and k_S = 2, but only
    S0001 is eligible outside the long leg (S0000 is under the floor), so the short
    leg is S0001 alone at -(gross - net)/2/1 = -0.5."""
    scores = _scores(4)
    targets = signed_target_weights(scores, 0.5, 1.0, 0.0, eligible=["S0003", "S0002", "S0001"])
    assert targets == {"S0003": 0.25, "S0002": 0.25, "S0001": -0.5}


def test_signed_target_weights_with_no_eligible_names_is_long_only() -> None:
    """No eligible name: the short leg is empty and the long leg keeps its
    (gross + net)/2 share."""
    targets = signed_target_weights(_scores(4), 0.5, 1.0, 0.4, eligible=[])
    assert targets == pytest.approx({"S0003": 0.35, "S0002": 0.35})


def test_signed_target_weights_ties_break_by_security_id() -> None:
    """All four scores equal: the rank is by `security_id` ascending, so the one long
    name is A and the one lowest-scored (short) name is D."""
    targets = signed_target_weights(
        {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}, 0.25, 1.0, 0.0, eligible=["A", "B", "C", "D"]
    )
    assert targets == {"A": 0.5, "D": -0.5}


@pytest.mark.parametrize(("gross", "net"), [(1.0, 0.0), (1.0, 0.4), (0.8, -0.2)])
def test_signed_target_weights_legs_are_disjoint_and_sums_hold(gross: float, net: float) -> None:
    """Σ |w| = gross, Σ w = net, the legs are disjoint, and every short name is
    eligible."""
    scores = _scores(12)
    eligible = [sid for sid in scores if sid != "S0005"]
    targets = signed_target_weights(scores, 0.25, gross, net, eligible=eligible)
    long_leg = {sid for sid, weight in targets.items() if weight > 0}
    short_leg = {sid for sid, weight in targets.items() if weight < 0}
    assert not (long_leg & short_leg)
    assert short_leg <= set(eligible)
    assert math.fsum(targets.values()) == pytest.approx(net)
    assert math.fsum(abs(weight) for weight in targets.values()) == pytest.approx(gross)
    assert list(targets) == sorted(targets)


@pytest.mark.parametrize(
    "scores",
    [{}, _scores(3), _scores(7), {"X": 1.0, "Y": 1.0, "Z": 1.0}],
)
@pytest.mark.parametrize("fraction", [0.1, 0.5, 1.0])
def test_signed_target_weights_long_only_is_target_weights_exactly(
    scores: dict[str, float], fraction: float
) -> None:
    """gross == net is the `long_only` book (spec item 1), so the result is
    `target_weights`' output exactly and `eligible` is not read."""
    assert signed_target_weights(scores, fraction, 1.0, 1.0, eligible=[]) == target_weights(
        scores, fraction, "equal"
    )


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_signed_target_weights_rejects_a_bad_fraction(bad: float) -> None:
    with pytest.raises(ValueError, match="top_fraction"):
        signed_target_weights({"A": 1.0}, bad, 1.0, 0.0, eligible=["A"])


def test_signed_target_weights_rejects_non_finite_inputs() -> None:
    with pytest.raises(ValueError, match="score"):
        signed_target_weights({"A": float("nan")}, 0.5, 1.0, 0.0, eligible=["A"])
    with pytest.raises(ValueError, match="gross"):
        signed_target_weights({"A": 1.0}, 0.5, float("inf"), 0.0, eligible=["A"])
    with pytest.raises(ValueError, match="net"):
        signed_target_weights({"A": 1.0}, 0.5, 1.0, float("nan"), eligible=["A"])

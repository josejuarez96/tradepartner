"""Look-ahead for the `momentum` buy/hold buffer (backtest spec amendment #657, item 5;
plan T239).

Item 5's cases, on the hand fixture of `test_engine_buffer` (four names, `k = 2`,
`h = 3`), where the buffer retains A at T1 and T2 and every anchor precedes the run:

- **The plan-read check**: a buffered run reads nothing later than its own rebalance, so
  `run(end=T_i)` issues every provider read at a time at or before `close(T_i)`.
- **Truncation invariance**: a fact known after `close(T_i)` -- a later revision of a
  held name's bar -- leaves `run(end=T_i)` byte-identical.
- **Prefix invariance**: `run(end=T_i)` equals the prefix of `run(end=T_n)`.
- **A revision case with teeth**: a revision of the anchor bar shared by plans at T1 and
  T2 (Jan 2023), known an hour after `close(T1)`, reaches no plan in `run(end=T1)` or
  `run(end=T2)` (both unchanged) and moves plan 2's retention in `run(end=T3)`.
- **The carried state**: a revision of the anchor bar only plan 1 reads (Dec 2022), known
  an hour after `close(T1)`, would move a name across the top-two cut at T1, yet every run
  to T1, T2 and T3 is byte-identical: plan 2's retained set is built from plan 1's
  targets, which step 1 carried, never from a re-plan of T1 on the revised data. The
  teeth show the difference a re-plan would make.

Each new-behaviour case fails on the merge-base code, where the engine applies no buffer
(the retained names, `n_retained` and the hand targets do not appear). The fixture is the
`FakeProvider`, so the checks cost no store.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

import polars as pl
import pytest
from backtest import test_engine_buffer as hand

from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.portfolio import buffered_targets
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.calendar import last_session_of_month

T0, T1, T2, T3 = hand.T0, hand.T1, hand.T2, hand.T3
JAN_2023 = last_session_of_month(2023, 1)
DEC_2022 = last_session_of_month(2022, 12)
#: The revision time: an hour after close(T1), before close(T2).
REVISED_AT = read_time(T1, "month_end") + timedelta(hours=1)


def _run(
    revisions: Mapping[tuple[str, date], tuple[float, datetime]] | None,
    end: date,
    hold: float = 0.75,
) -> dict[float, BacktestResult]:
    return hand.hand_run(hold, hand.hand_provider(revisions), end)


def _assert_same(got: BacktestResult, want: BacktestResult, context: str) -> None:
    assert got.equity == want.equity, f"equity differs: {context}"
    assert got.rebalances == want.rebalances, f"rebalances differ: {context}"
    assert got.weights == want.weights, f"weights differ: {context}"
    assert got.position_values.equals(want.position_values), context
    assert got.targets == want.targets, f"targets differ: {context}"


def _assert_same_runs(
    got: Mapping[float, BacktestResult], want: Mapping[float, BacktestResult], context: str
) -> None:
    assert got.keys() == want.keys(), context
    for level, result in want.items():
        _assert_same(got[level], result, f"{context} at {level} bp")


def test_the_buffered_run_reads_nothing_after_its_rebalance() -> None:
    """Every read of `run(end=T_i)` is at or before `close(T_i)`: the buffer adds no read
    (it is built from the plan's own scores), and the carried state is a plan's, not a
    fresh read. The run is not vacuous: the buffer retains a name at two rebalances."""
    for end in (T1, T2, T3):
        provider = hand.hand_provider()
        result = run(
            hand.hand_settings(0.75),
            provider,
            T0,
            end,
            hand.test_engine._handle(),
            (0.0, hand.PER_SIDE),
            family="momentum",
        )
        allowed = {read_time(t, "month_end") for t in rebalance_sessions(T0, end, "month_end")}
        assert provider.read_times() <= allowed
    result = _run(None, T3)[0.0]
    assert [row.counts["n_retained"] for row in result.rebalances] == [0, 1, 1]


def test_a_fact_known_after_close_t_i_leaves_the_run_to_t_i_unchanged() -> None:
    """Truncation invariance: a later revision of a held name's bar (A's Dec 2022 close,
    known after `close(T_i)`) is invisible to every read `run(end=T_i)` makes."""
    for end in (T1, T2, T3):
        late = read_time(end, "month_end") + timedelta(hours=1)
        revisions = {("A", DEC_2022): (1.0, late)}
        _assert_same_runs(
            _run(revisions, end),
            _run(None, end),
            f"a revision known after close({end}) changed run(end={end})",
        )


def test_prefix_invariance() -> None:
    """`run(end=T_i)` is the prefix of `run(end=T3)`: no later rebalance reaches an
    earlier run."""
    longest = _run(None, T3)
    for end in (T1, T2):
        shorter = _run(None, end)
        for level in shorter:
            whole = longest[level]
            prefix = BacktestResult(
                cost_per_side_bps=whole.cost_per_side_bps,
                equity=tuple(row for row in whole.equity if row.session <= end),
                rebalances=tuple(row for row in whole.rebalances if row.session < end),
                weights=tuple(row for row in whole.weights if row.fill_session <= end),
                position_values=whole.position_values.filter(pl.col("session") <= end),
                marking_frames=tuple(f for f in whole.marking_frames if f.end <= end),
                targets={fill: t for fill, t in whole.targets.items() if fill <= end},
            )
            _assert_same(shorter[level], prefix, f"run(end={end}) at {level} bp")


def test_a_revision_of_the_shared_anchor_reaches_only_the_later_plan() -> None:
    """C's Jan 2023 close (the skip anchor of T1 and the formation anchor of T2) is
    revised to 400 an hour after close(T1). `run(end=T1)` and `run(end=T2)` are
    unchanged (neither builds a plan at or after close(T1)); plan 2, read at close(T2),
    sees it in `run(end=T3)`, where C drops out and the T2 target set changes from
    {A, C, D} to {A, D}."""
    revisions = {("C", JAN_2023): (400.0, REVISED_AT)}
    base3 = _run(None, T3)[0.0]
    assert _run(revisions, T1)[0.0].targets == _run(None, T1)[0.0].targets
    assert _run(revisions, T2)[0.0].targets == _run(None, T2)[0.0].targets
    revised3 = _run(revisions, T3)[0.0]
    fill2 = fill_session(T2, "month_end")
    assert sorted(base3.targets[fill2]) == ["A", "C", "D"]
    assert sorted(revised3.targets[fill2]) == ["A", "D"]


def test_the_buffer_carries_plan_targets_and_never_replans_them() -> None:
    """A revision of B's Dec 2022 close -- read by plan 1 alone -- to 70 an hour after
    close(T1) would move B into T1's top two (a re-planned T1 targets {B, C}), so a
    carried state that used it would keep B (targets {C, D}) at T2. The run carries plan
    1's targets instead: every run to T1, T2 and T3 is byte-identical, and plan 2's
    retained set is `buffered_targets(scores_2, P = plan 1's targets)`."""
    revisions = {("B", DEC_2022): (70.0, REVISED_AT)}
    base = _run(None, T3)
    for end in (T1, T2, T3):
        _assert_same_runs(
            _run(revisions, end),
            _run(None, end),
            f"a revision only plan 1 reads changed run(end={end})",
        )
    # Teeth: the two anchors' scores, and what a re-plan of T1 (top two {B, C}) would give
    # plan 2 as against the carried P = plan 1's targets ({A, C, D}).
    scores_1 = {"A": 132 / 110 - 1, "B": 110 / 70 - 1, "C": 204 / 102 - 1, "D": 141 / 101 - 1}
    replanned_1, _ = buffered_targets(scores_1, ("A", "B"), 0.5, 0.75)
    assert sorted(replanned_1) == ["B", "C"]
    scores_2 = {
        "A": 145 / 132 - 1,
        "B": 112 / 110 - 1,
        "C": 306 / 204 - 1,
        "D": 170 / 141 - 1,
    }
    carried, n_retained = buffered_targets(scores_2, ("A", "C", "D"), 0.5, 0.75)
    buggy, buggy_retained = buffered_targets(scores_2, tuple(replanned_1), 0.5, 0.75)
    assert sorted(carried) == ["A", "C", "D"] and n_retained == 1
    assert sorted(buggy) == ["C", "D"] and buggy_retained == 0
    fill2 = fill_session(T2, "month_end")
    assert sorted(base[0.0].targets[fill2]) == sorted(carried)  # the run used the carry


def test_a_held_name_renamed_by_a_chain_and_between_top_and_hold_is_retained() -> None:
    """The chain case, at the plan level: A is renamed to E by the live chains, and E
    ranks third at T1 (inside `hold`, outside `top`), so the T1 target set is C, D and E
    with one retained name. Before the buffer this plan targeted C and D alone."""
    members = {
        session: (["A", "B", "C", "D"] if session == T0 else ["E", "B", "C", "D"])
        for session in rebalance_sessions(T0, hand.HISTORY_END, "month_end")
    }
    provider = hand.hand_provider(members=members, chain_pairs=(("A", "E"),))
    result = hand.hand_run(0.75, provider)[hand.PER_SIDE]
    assert sorted(result.targets[fill_session(T1, "month_end")]) == ["C", "D", "E"]
    assert result.rebalances[1].counts["n_retained"] == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])

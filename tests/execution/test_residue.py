"""Residues and rebalance state (Phase 4 spec req 14 and Definitions > Rebalance
state; plan T52b).

Hand-computed cases: a residue of each origin with its split adjustment, the
`origin = untradable` rule, the latest-decision rule, the cap, and the derived
rebalance state. The literal check on `execution/plan.py` is T50's.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import (
    DecisionState,
    RebalanceState,
    State,
    rebalance_state,
    residue,
)
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
)

A = "SEC_A"
B = "SEC_B"
START = date(2026, 10, 1)  # the window's start session (carried residues are stated for it)
S = date(2026, 11, 20)  # the session quantities are stated for
T_OCT = date(2026, 10, 30)  # rebalance session; F = 2026-11-02
T_NOV = date(2026, 11, 30)  # F = 2026-12-01
WINDOW = 7
_IDS = itertools.count(100)


def _utc(day: date, hour: int = 21) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _no_actions() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
        }
    )


def _split(security_id: str, ex_date: date, ratio: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [security_id],
            "action_type": ["split"],
            "ex_date": [ex_date],
            "ratio_or_amount": [ratio],
        },
        schema=_no_actions().schema,
    )


def _paper_run(run_id: int, day: date, *, window_id: int = WINDOW) -> PaperRunRow:
    return PaperRunRow(
        run_id=run_id,
        window_id=window_id,
        session=day,
        kind="rebalance",
        started_at=_utc(day, 13),
        invoked_by="scheduler",
        code_version="abc",
        known_at=_utc(day, 13),
        ingested_at=_utc(day, 13),
    )


#: Run 1 is this window's; run 50 is the previous window's.
RESIDUE_RUNS = (_paper_run(1, date(2026, 11, 2)), _paper_run(50, START, window_id=WINDOW - 1))


def _ledger(**positions: float) -> Ledger:
    return Ledger(positions=positions, cash=1000.0, through=S)


def _carried(
    quantity: float, *, origin: str | None = "dust", security_id: str = A, window_id: int = WINDOW
) -> AdjustmentRow:
    return AdjustmentRow(
        adjustment_id=next(_IDS),
        window_id=window_id,
        session=START,
        kind="carried_residue",
        origin=origin,
        security_id=security_id,
        quantity=quantity,
        known_at=_utc(START, 14),
        ingested_at=_utc(START, 14),
    )


def _decision(
    day: date,
    *,
    decision: str = "forced_exit",
    reason: str | None = "window_stop",
    security_id: str = A,
    rebalance_session: date | None = None,
    side: str | None = "sell",
    run_id: int = 1,
) -> DecisionRow:
    return DecisionRow(
        decision_id=next(_IDS),
        run_id=run_id,
        rebalance_session=rebalance_session,
        security_id=security_id,
        side=side,
        planned_quantity=10.0 if side == "sell" else None,
        planned_notional=100.0 if side == "buy" else None,
        target_notional=100.0 if side == "buy" else None,
        whole_share=False,
        decision=decision,
        reason=reason,
        known_at=_utc(day, 13),
        ingested_at=_utc(day, 13),
    )


def _skipped(decision: DecisionRow, reason: str, day: date | None = None) -> DecisionEventRow:
    assert decision.decision_id is not None
    at = _utc(day, 14) if day is not None else decision.known_at + timedelta(minutes=5)
    return DecisionEventRow(
        decision_id=decision.decision_id,
        run_id=decision.run_id,
        status="skipped",
        reason=reason,
        known_at=at,
        ingested_at=at,
    )


def _mark(
    day: date, tradable: bool | None, *, security_id: str = A, run_id: int = 1
) -> PositionDailyRow:
    return PositionDailyRow(
        run_id=run_id,
        session=day,
        security_id=security_id,
        quantity=10.0,
        tradable=tradable,
        known_at=_utc(day),
        ingested_at=_utc(day),
    )


def _residue(
    *,
    adjustments: Sequence[AdjustmentRow] = (),
    decisions: Sequence[DecisionRow] = (),
    events: Sequence[DecisionEventRow] = (),
    marks: Sequence[PositionDailyRow] = (),
    ledger: Ledger | None = None,
    actions: pl.DataFrame | None = None,
    security_id: str = A,
    runs: Sequence[PaperRunRow] = RESIDUE_RUNS,
) -> float:
    return residue(
        security_id,
        adjustments,
        decisions,
        events,
        marks,
        ledger if ledger is not None else _ledger(**{A: 10.0}),
        actions if actions is not None else _no_actions(),
        window_id=WINDOW,
        runs=runs,
    )


# --- residue: each origin ---------------------------------------------------------


def test_no_residue_rows_mean_no_residue() -> None:
    assert _residue() == 0.0


def test_a_carried_dust_residue_counts_up_to_the_holding() -> None:
    assert _residue(adjustments=[_carried(0.4)]) == pytest.approx(0.4)


def test_a_carried_residue_is_split_adjusted_through_the_session() -> None:
    """A 2:1 split between the start and S doubles the carried 0.4 shares."""
    actions = _split(A, date(2026, 10, 15), 2.0)
    assert _residue(adjustments=[_carried(0.4)], actions=actions) == pytest.approx(0.8)


def test_a_split_after_the_session_or_on_the_start_does_not_apply() -> None:
    later = _split(A, S + timedelta(days=3), 2.0)
    on_start = _split(A, START, 2.0)
    assert _residue(adjustments=[_carried(0.4)], actions=later) == pytest.approx(0.4)
    assert _residue(adjustments=[_carried(0.4)], actions=on_start) == pytest.approx(0.4)


def test_a_carried_residue_shrinks_with_the_holding() -> None:
    """Sold down to 0.25 shares (a cash merger, a forced exit), the residue is 0.25."""
    assert _residue(adjustments=[_carried(0.4)], ledger=_ledger(**{A: 0.25})) == 0.25
    assert _residue(adjustments=[_carried(0.4)], ledger=_ledger()) == 0.0


def test_a_carried_untradable_residue_counts_only_while_the_latest_flag_is_false() -> None:
    carried = [_carried(3.0, origin="untradable")]
    untradable = [_mark(date(2026, 11, 18), True), _mark(date(2026, 11, 19), False)]
    tradable_again = [_mark(date(2026, 11, 18), False), _mark(date(2026, 11, 19), True)]
    assert _residue(adjustments=carried, marks=untradable) == pytest.approx(3.0)
    assert _residue(adjustments=carried, marks=tradable_again) == 0.0
    assert _residue(adjustments=carried, marks=[]) == 0.0  # no false flag: not counted


def test_the_latest_flag_is_the_latest_known_row_of_that_name() -> None:
    """Order of the input does not matter; another name's flag is ignored; a
    row with no flag (a cash row, say) is not a flag."""
    carried = [_carried(3.0, origin="untradable")]
    marks = [
        _mark(date(2026, 11, 19), False),
        _mark(date(2026, 11, 18), True),
        _mark(date(2026, 11, 20), True, security_id=B),
        _mark(date(2026, 11, 20), None),
    ]
    assert _residue(adjustments=carried, marks=marks) == pytest.approx(3.0)


def test_a_window_stop_exit_closed_as_dust_is_a_dust_residue() -> None:
    exit_ = _decision(date(2026, 11, 19))
    assert _residue(
        decisions=[exit_], events=[_skipped(exit_, "dust")], ledger=_ledger(**{A: 0.3})
    ) == pytest.approx(0.3)


def test_dust_counts_only_for_a_window_stop_exit() -> None:
    """A rebalance-time `dust` decision, or a `delisted` exit closed as dust,
    is not a dust residue."""
    rebalance_dust = _decision(
        date(2026, 11, 2), decision="dust", reason=None, rebalance_session=T_OCT
    )
    delisted = _decision(date(2026, 11, 19), reason="delisted")
    held = _ledger(**{A: 0.3})
    assert _residue(decisions=[rebalance_dust], ledger=held) == 0.0
    assert _residue(decisions=[delisted], events=[_skipped(delisted, "dust")], ledger=held) == 0.0


def test_an_old_dust_on_a_name_traded_again_counts_for_nothing() -> None:
    """The latest decision rules: a later trade on the name replaces the dust."""
    exit_ = _decision(date(2026, 11, 3))
    again = _decision(
        date(2026, 11, 13), decision="trade", reason=None, side="buy", rebalance_session=T_OCT
    )
    assert _residue(decisions=[again, exit_], events=[_skipped(exit_, "dust")]) == 0.0


@pytest.mark.parametrize("reason", ["window_stop", "delisted", "untargeted_receipt"])
def test_an_untradable_forced_exit_is_a_residue_while_the_flag_is_false(reason: str) -> None:
    exit_ = _decision(date(2026, 11, 19), reason=reason)
    events = [_skipped(exit_, "untradable")]
    assert _residue(
        decisions=[exit_], events=events, marks=[_mark(date(2026, 11, 19), False)]
    ) == pytest.approx(10.0)
    assert (
        _residue(decisions=[exit_], events=events, marks=[_mark(date(2026, 11, 20), True)]) == 0.0
    )


def test_an_exit_that_is_not_closed_is_no_residue() -> None:
    """An open or in-flight exit (no `skipped` event), or one whose latest
    event is another reason, leaves nothing."""
    exit_ = _decision(date(2026, 11, 19))
    marks = [_mark(date(2026, 11, 19), False)]
    assert _residue(decisions=[exit_], marks=marks) == 0.0
    written = DecisionEventRow(
        decision_id=exit_.decision_id or 0,
        run_id=1,
        status="written_off",
        reason="unfunded",
        known_at=_utc(date(2026, 11, 19), 15),
        ingested_at=_utc(date(2026, 11, 19), 15),
    )
    assert _residue(decisions=[exit_], events=[written], marks=marks) == 0.0


def test_the_latest_event_of_the_exit_decides() -> None:
    exit_ = _decision(date(2026, 11, 19))
    early_dust = _skipped(exit_, "dust", date(2026, 11, 19))
    later_untradable = DecisionEventRow(
        decision_id=exit_.decision_id or 0,
        run_id=1,
        status="skipped",
        reason="untradable",
        known_at=_utc(date(2026, 11, 20), 14),
        ingested_at=_utc(date(2026, 11, 20), 14),
    )
    events = [later_untradable, early_dust]
    assert _residue(decisions=[exit_], events=events, marks=[_mark(S, True)]) == 0.0
    assert _residue(decisions=[exit_], events=events, marks=[_mark(S, False)]) == 10.0


def test_the_residue_is_the_sum_capped_at_the_holding() -> None:
    """A carried 4 plus an untradable exit's held 6: the sum is 10, capped at 6."""
    exit_ = _decision(date(2026, 11, 19))
    parts = {
        "adjustments": [_carried(4.0, origin="dust")],
        "decisions": [exit_],
        "events": [_skipped(exit_, "untradable")],
        "marks": [_mark(date(2026, 11, 19), False)],
    }
    assert _residue(**parts, ledger=_ledger(**{A: 6.0})) == pytest.approx(6.0)  # type: ignore[arg-type]


def test_rows_of_other_names_and_kinds_are_ignored() -> None:
    other = _carried(5.0, security_id=B)
    dividend = AdjustmentRow(
        adjustment_id=next(_IDS),
        window_id=WINDOW,
        session=START,
        kind="dividend_cash",
        security_id=A,
        cash=3.0,
        known_at=_utc(START),
        ingested_at=_utc(START),
    )
    b_exit = _decision(date(2026, 11, 19), security_id=B)
    assert (
        _residue(
            adjustments=[other, dividend], decisions=[b_exit], events=[_skipped(b_exit, "dust")]
        )
        == 0.0
    )


def test_a_negative_holding_raises() -> None:
    with pytest.raises(ValueError, match="short"):
        _residue(adjustments=[_carried(0.4)], ledger=_ledger(**{A: -1.0}))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5])
def test_a_bad_carried_quantity_raises(bad: float) -> None:
    with pytest.raises(ValueError):
        _residue(adjustments=[_carried(bad)])


def test_rows_of_another_window_are_ignored() -> None:
    """A carried row of another window, and the previous window's `window_stop`
    exit skipped as dust, are not this window's residue."""
    assert _residue(adjustments=[_carried(0.2, window_id=WINDOW + 1)]) == 0.0
    old_exit = _decision(date(2026, 9, 29), run_id=50)
    assert _residue(decisions=[old_exit], events=[_skipped(old_exit, "dust")]) == 0.0
    old_flag = _mark(date(2026, 9, 29), False, run_id=50)
    assert _residue(adjustments=[_carried(3.0, origin="untradable")], marks=[old_flag]) == 0.0


@pytest.mark.parametrize("what", ["decision", "event", "mark"])
def test_a_row_of_an_unknown_run_raises(what: str) -> None:
    exit_ = _decision(date(2026, 11, 19))
    stray = _decision(date(2026, 11, 19), run_id=77)
    rows: dict[str, object] = {
        "decision": {"decisions": [stray]},
        "event": {
            "decisions": [exit_],
            "events": [DecisionEventRow(**{**_skipped(exit_, "dust").__dict__, "run_id": 77})],
        },
        "mark": {"marks": [_mark(S, False, run_id=77)]},
    }
    with pytest.raises(ValueError, match="run 77"):
        _residue(**rows[what])  # type: ignore[arg-type]


@pytest.mark.parametrize("origin", [None, "carried", ""])
def test_a_carried_row_without_a_residue_origin_raises(origin: str | None) -> None:
    with pytest.raises(ValueError, match="origin"):
        _residue(adjustments=[_carried(0.4, origin=origin)])


def test_rows_dated_after_the_session_change_nothing() -> None:
    """No look-ahead: a mark or a carried row dated after S (a later run's rows
    in the journal) leaves the residue on S unchanged."""
    carried = _carried(3.0, origin="untradable")
    base = [_mark(date(2026, 11, 19), False)]
    later_mark = _mark(S + timedelta(days=3), True)
    assert _residue(adjustments=[carried], marks=base) == pytest.approx(3.0)
    assert _residue(adjustments=[carried], marks=[*base, later_mark]) == pytest.approx(3.0)
    later_carried = AdjustmentRow(**{**_carried(2.0).__dict__, "session": S + timedelta(days=3)})
    assert _residue(adjustments=[later_carried]) == 0.0


def test_the_same_inputs_give_the_same_quantity() -> None:
    exit_ = _decision(date(2026, 11, 19))
    kwargs = {
        "adjustments": [_carried(0.4, origin="untradable"), _carried(0.1)],
        "decisions": [exit_],
        "events": [_skipped(exit_, "untradable")],
        "marks": [_mark(date(2026, 11, 19), False)],
        "actions": _split(A, date(2026, 10, 15), 2.0),
        "ledger": _ledger(**{A: 20.0}),
    }
    first = _residue(**kwargs)  # type: ignore[arg-type]
    # carried 0.8 (untradable, flag false) + 0.2 (dust), plus the untradable exit's
    # held 20: the sum is 21, capped at the holding
    assert first == pytest.approx(20.0)
    assert all(_residue(**kwargs) == first for _ in range(3))  # type: ignore[arg-type]


# --- rebalance state ----------------------------------------------------------------


def _window(first: date = T_OCT) -> PaperWindowRow:
    return PaperWindowRow(
        window_id=WINDOW,
        hypothesis_id=1,
        first_rebalance_session=first,
        account_id="paper",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="abc",
        started_at=_utc(date(2026, 10, 1)),
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=_utc(date(2026, 10, 1)),
        ingested_at=_utc(date(2026, 10, 1)),
    )


_run = _paper_run


RUNS = (_run(1, date(2026, 11, 2)), _run(2, date(2026, 11, 3)))


def _event(
    status: str, *, run_id: int = 1, t: date = T_OCT, reason: str | None = None
) -> RebalanceEventRow:
    return RebalanceEventRow(
        rebalance_session=t,
        run_id=run_id,
        status=status,
        reason=reason,
        known_at=_utc(date(2026, 11, 3)),
        ingested_at=_utc(date(2026, 11, 3)),
    )


def _state_of(
    state: State, *, decision: str = "trade", rebalance_session: date | None = T_OCT
) -> tuple[DecisionRow, DecisionState]:
    row = _decision(
        date(2026, 11, 2),
        decision=decision,
        reason=None,
        rebalance_session=rebalance_session,
        side="buy",
    )
    return row, DecisionState(state)


def _rebalance(
    decision_states: Sequence[tuple[DecisionRow, DecisionState]] = (),
    events: Sequence[RebalanceEventRow] = (),
    *,
    t: date = T_OCT,
    runs: Sequence[PaperRunRow] = RUNS,
    session: date = date(2026, 11, 3),
) -> RebalanceState:
    return rebalance_state(
        t, _window(), runs, events, decision_states, session=session, cadence="month_end"
    )


def test_a_rebalance_with_no_plan_at_all_is_pending() -> None:
    assert _rebalance() is RebalanceState.PENDING
    assert _rebalance(runs=()) is RebalanceState.PENDING  # no run on F_i either


def test_executed_only_when_no_decision_is_open_or_in_flight() -> None:
    done = [_state_of(State.CLOSED), _state_of(State.SETTLED)]
    assert _rebalance(done) is RebalanceState.EXECUTED
    for blocking in (State.OPEN, State.IN_FLIGHT):
        assert _rebalance([*done, _state_of(blocking)]) is RebalanceState.PENDING


def test_forced_exit_decisions_never_enter_the_test() -> None:
    """An open forced exit (no rebalance session) does not hold the rebalance
    pending, and a closed one does not make an unplanned rebalance executed."""
    open_exit = _state_of(State.OPEN, decision="forced_exit", rebalance_session=None)
    closed_exit = _state_of(State.CLOSED, decision="forced_exit", rebalance_session=None)
    assert _rebalance([_state_of(State.SETTLED), open_exit]) is RebalanceState.EXECUTED
    assert _rebalance([closed_exit]) is RebalanceState.PENDING


def test_decisions_of_another_rebalance_or_window_are_ignored() -> None:
    other_month = _state_of(State.OPEN, rebalance_session=T_NOV)
    assert _rebalance([_state_of(State.SETTLED), other_month]) is RebalanceState.EXECUTED
    stray_row, stray = _state_of(State.OPEN)
    foreign = DecisionRow(**{**stray_row.__dict__, "run_id": 9})
    runs = (*RUNS, _run(9, date(2026, 11, 2), window_id=WINDOW - 1))
    assert (
        _rebalance([_state_of(State.SETTLED), (foreign, stray)], runs=runs)
        is RebalanceState.EXECUTED
    )


def test_a_journaled_event_is_the_state() -> None:
    """Once written, `executed` or `missed` stands, whatever the decisions say."""
    open_ = [_state_of(State.OPEN)]
    assert _rebalance(open_, [_event("executed")]) is RebalanceState.EXECUTED
    assert _rebalance(open_, [_event("missed", reason="catch_up_lapsed")]) is (
        RebalanceState.MISSED
    )


def test_events_of_another_window_or_rebalance_are_ignored() -> None:
    foreign_run = _run(9, date(2026, 11, 2), window_id=WINDOW - 1)
    runs = (*RUNS, foreign_run)
    assert _rebalance([], [_event("missed", run_id=9)], runs=runs) is RebalanceState.PENDING
    assert _rebalance([], [_event("missed", t=T_NOV)], runs=runs) is RebalanceState.PENDING


def test_a_row_of_a_run_not_given_raises() -> None:
    """An incomplete run list can never hide a `missed` event or an open decision."""
    with pytest.raises(ValueError, match="run 77"):
        _rebalance([], [_event("missed", run_id=77, reason="skip_cap")])
    stray_row, stray = _state_of(State.OPEN)
    foreign = DecisionRow(**{**stray_row.__dict__, "run_id": 77})
    with pytest.raises(ValueError, match="run 77"):
        _rebalance([_state_of(State.SETTLED), (foreign, stray)])


def test_a_plan_decision_without_a_rebalance_session_raises() -> None:
    with pytest.raises(ValueError, match="no rebalance session"):
        _rebalance([_state_of(State.SETTLED), _state_of(State.OPEN, rebalance_session=None)])


def test_conflicting_events_raise() -> None:
    with pytest.raises(ValueError, match="both"):
        _rebalance([], [_event("executed"), _event("missed", run_id=2, reason="skip_cap")])


def test_a_rebalance_before_the_window_or_not_yet_due_raises() -> None:
    with pytest.raises(ValueError, match="before the window"):
        _rebalance(t=date(2026, 9, 30))
    with pytest.raises(ValueError, match="not due"):
        _rebalance(t=T_NOV, session=date(2026, 11, 30))  # F = 2026-12-01 > S
    with pytest.raises(ValueError):
        _rebalance(t=date(2026, 10, 29))  # not a rebalance session


def test_the_same_inputs_give_the_same_state() -> None:
    states = [_state_of(State.SETTLED), _state_of(State.CLOSED)]
    assert len({_rebalance(states) for _ in range(3)}) == 1

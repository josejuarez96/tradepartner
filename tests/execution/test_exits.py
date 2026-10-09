"""Forced and stop exits, pure (plan T63e; spec reqs 7 step 7 and 14).

Every case is on hand-built rows: decisions with their derived states, the
holdings, the listings at close(S-1), the `assets` read and the window's
adjustments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from tradepartner.calendar import next_session
from tradepartner.config import RiskConfig
from tradepartner.execution.exits import (
    ExitDecision,
    MissingAssetRefused,
    forced_exits,
    reattempt_exits,
    stop_exits,
)
from tradepartner.execution.plan import (
    DecisionState,
    Remainder,
    State,
    decision_state,
    remainder,
)
from tradepartner.execution.risk import OpenSell
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
)

S = date(2026, 10, 14)  # a Wednesday session
PREVIOUS = date(2026, 10, 13)
NOW = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
WINDOW = 1
PENDING = date(2026, 9, 30)  # the pending rebalance T_i


@dataclass(frozen=True)
class Flags:
    """An `assets` answer for one name."""

    tradable: bool = True
    fractionable: bool = True


def _assets(*names: str, **overrides: Flags) -> dict[str, Flags]:
    flags = {name: Flags() for name in names}
    flags.update(overrides)
    return flags


def _decision(
    decision_id: int,
    security_id: str,
    *,
    decision: str = "forced_exit",
    reason: str | None = "delisted",
    rebalance_session: date | None = None,
    side: str | None = "sell",
    known_at: datetime = NOW - timedelta(days=1),
    planned_quantity: float | None = 10.0,
    planned_notional: float | None = None,
) -> DecisionRow:
    return DecisionRow(
        decision_id=decision_id,
        run_id=1,
        rebalance_session=rebalance_session,
        security_id=security_id,
        side=side,
        planned_quantity=planned_quantity,
        planned_notional=planned_notional,
        whole_share=False,
        decision=decision,
        reason=reason,
        known_at=known_at,
        ingested_at=known_at,
    )


def _receipt(
    security_id: str,
    quantity: float = 3.0,
    *,
    session: date = PREVIOUS,
    known_at: datetime = NOW - timedelta(days=1),
    window_id: int = WINDOW,
) -> AdjustmentRow:
    return AdjustmentRow(
        adjustment_id=1,
        window_id=window_id,
        run_id=1,
        session=session,
        kind="spinoff_receipt",
        security_id=security_id,
        quantity=quantity,
        known_at=known_at,
        ingested_at=known_at,
    )


OPEN = DecisionState(State.OPEN, remainder=Remainder(quantity=10.0, notional=1000.0))
IN_FLIGHT = DecisionState(State.IN_FLIGHT)
SETTLED = DecisionState(State.SETTLED, remainder=Remainder(quantity=0.0, notional=0.0))
CLOSED_UNTRADABLE = DecisionState(State.CLOSED, "skipped")


def _forced(
    held: Mapping[str, float],
    *,
    listings_at: Mapping[str, date | None] | None = None,
    assets: Mapping[str, Flags] | None = None,
    decisions: tuple[DecisionRow, ...] = (),
    states: Mapping[int, DecisionState] | None = None,
    open_sells: tuple[OpenSell, ...] = (),
    adjustments: tuple[AdjustmentRow, ...] = (),
) -> list[ExitDecision]:
    return forced_exits(
        held,
        listings_at or {},
        assets if assets is not None else _assets(*held),
        decisions,
        states or {},
        open_sells,
        adjustments,
        session=S,
        pending_rebalance=PENDING,
    )


# --- forced_exits ------------------------------------------------------------------


def test_delisted_name_is_sold_whole() -> None:
    exits = _forced({"AAA": 12.5, "BBB": 4.0}, listings_at={"AAA": PREVIOUS})
    assert exits == [
        ExitDecision(
            security_id="AAA",
            reason="delisted",
            planned_quantity=12.5,
            whole_share=False,
            session=S,
        )
    ]


def test_unknown_listing_end_counts_as_ended() -> None:
    exits = _forced({"AAA": 2.0}, listings_at={"AAA": None})
    assert [(e.security_id, e.reason) for e in exits] == [("AAA", "delisted")]


def test_listing_ending_on_s_is_not_ended_at_close_s_minus_1() -> None:
    """No look-ahead: a listing whose last session is S itself has not ended
    at close(S-1), so nothing is sold on S."""
    assert _forced({"AAA": 2.0}, listings_at={"AAA": S}) == []
    later = date(2026, 10, 20)
    assert _forced({"AAA": 2.0}, listings_at={"AAA": later}) == []


def test_ended_listing_of_a_name_not_held_makes_no_exit() -> None:
    assert _forced({}, listings_at={"AAA": PREVIOUS}, assets={}) == []
    assert _forced({"AAA": 0.0}, listings_at={"AAA": PREVIOUS}) == []


def test_untradable_delisted_name_is_returned_closed_with_its_event() -> None:
    exits = _forced(
        {"AAA": 5.0},
        listings_at={"AAA": PREVIOUS},
        assets=_assets(AAA=Flags(tradable=False)),
    )
    assert len(exits) == 1
    (exit_,) = exits
    assert exit_.reason == "delisted"
    assert exit_.skipped_reason == "untradable"
    row = exit_.row(run_id=7, known_at=NOW, ingested_at=NOW, book_id="main")
    assert row.decision == "forced_exit"
    assert row.rebalance_session is None
    event = exit_.event(decision_id=42, run_id=7, known_at=NOW, ingested_at=NOW)
    assert event is not None
    assert (event.decision_id, event.status, event.reason) == (42, "skipped", "untradable")
    # The journaled pair reads as closed: nothing is ordered for it this run.
    journaled = replace(row, decision_id=42)
    state = decision_state(
        journaled,
        [event],
        [],
        [],
        [],
        pl.DataFrame(),
        lambda _sid: 100.0,
        RiskConfig(),
        session=S,
    )
    assert state.state is State.CLOSED


def test_tradable_exit_has_no_event() -> None:
    (exit_,) = _forced({"AAA": 5.0}, listings_at={"AAA": PREVIOUS})
    assert exit_.skipped_reason is None
    assert exit_.event(decision_id=1, run_id=1, known_at=NOW, ingested_at=NOW) is None


def test_untradable_name_is_re_evaluated_next_session() -> None:
    """The closed `untradable` exit of an earlier session blocks nothing: the
    name gets a new decision once the next session's read says it trades."""
    earlier = _decision(1, "AAA", known_at=NOW - timedelta(days=1))
    exits = _forced(
        {"AAA": 5.0},
        listings_at={"AAA": PREVIOUS},
        decisions=(earlier,),
        states={1: CLOSED_UNTRADABLE},
    )
    assert [(e.security_id, e.skipped_reason) for e in exits] == [("AAA", None)]


def test_spinoff_receipt_is_sold_whole() -> None:
    exits = _forced({"KID": 3.0, "PAR": 10.0}, adjustments=(_receipt("KID", 3.0),))
    assert exits == [
        ExitDecision(
            security_id="KID",
            reason="untargeted_receipt",
            planned_quantity=3.0,
            whole_share=False,
            session=S,
        )
    ]


def test_whole_share_flag_comes_from_assets() -> None:
    exits = _forced(
        {"KID": 2.4},
        assets=_assets(KID=Flags(fractionable=False)),
        adjustments=(_receipt("KID", 2.4),),
    )
    (exit_,) = exits
    assert exit_.whole_share is True
    # A forced exit is sized to the whole holding; the phase applies the floor.
    assert exit_.planned_quantity == 2.4
    assert exit_.row(run_id=1, known_at=NOW, ingested_at=NOW, book_id="main").whole_share is True


def test_receipt_journaled_after_s_is_not_seen() -> None:
    """No look-ahead: an adjustment dated after S is cut."""
    late = _receipt("KID", session=date(2026, 10, 15), known_at=NOW + timedelta(days=1))
    assert _forced({"KID": 3.0}, adjustments=(late,)) == []


def test_receipt_already_exited_is_not_sold_again() -> None:
    """Once an `untargeted_receipt` exit was decided after the receipt, the
    receipt is spent: shares the plan buys later are the plan's."""
    receipt = _receipt("KID", known_at=NOW - timedelta(days=20), session=date(2026, 9, 23))
    sold = _decision(1, "KID", reason="untargeted_receipt", known_at=NOW - timedelta(days=19))
    assert (
        _forced({"KID": 4.0}, adjustments=(receipt,), decisions=(sold,), states={1: SETTLED}) == []
    )


def test_receipt_exit_closed_untradable_is_re_evaluated() -> None:
    receipt = _receipt("KID", known_at=NOW - timedelta(days=3))
    skipped = _decision(1, "KID", reason="untargeted_receipt", known_at=NOW - timedelta(days=2))
    exits = _forced(
        {"KID": 3.0}, adjustments=(receipt,), decisions=(skipped,), states={1: CLOSED_UNTRADABLE}
    )
    assert [(e.security_id, e.reason) for e in exits] == [("KID", "untargeted_receipt")]


def test_receipt_rows_of_two_windows_raise() -> None:
    with pytest.raises(ValueError, match="window"):
        _forced(
            {"KID": 3.0},
            adjustments=(_receipt("KID"), _receipt("OLD", window_id=WINDOW + 1)),
        )


def test_months_old_settled_exit_does_not_block_a_new_one() -> None:
    old = _decision(1, "AAA", reason="untargeted_receipt", known_at=NOW - timedelta(days=120))
    exits = _forced(
        {"AAA": 6.0}, listings_at={"AAA": PREVIOUS}, decisions=(old,), states={1: SETTLED}
    )
    assert [(e.security_id, e.reason) for e in exits] == [("AAA", "delisted")]


@pytest.mark.parametrize("state", [OPEN, IN_FLIGHT], ids=["open", "in_flight"])
def test_open_or_in_flight_forced_exit_blocks(state: DecisionState) -> None:
    existing = _decision(1, "AAA")
    exits = _forced(
        {"AAA": 6.0}, listings_at={"AAA": PREVIOUS}, decisions=(existing,), states={1: state}
    )
    assert exits == []


@pytest.mark.parametrize("state", [OPEN, IN_FLIGHT], ids=["open", "in_flight"])
def test_open_or_in_flight_plan_decision_blocks(state: DecisionState) -> None:
    plan = _decision(
        1,
        "AAA",
        decision="trade",
        reason="left_universe",
        rebalance_session=date(2026, 9, 30),
    )
    exits = _forced(
        {"AAA": 6.0}, listings_at={"AAA": PREVIOUS}, decisions=(plan,), states={1: state}
    )
    assert exits == []


def test_non_terminal_own_sell_blocks() -> None:
    exits = _forced(
        {"AAA": 6.0},
        listings_at={"AAA": PREVIOUS},
        open_sells=(OpenSell("AAA", 6.0),),
    )
    assert exits == []


def test_no_second_forced_exit_while_a_sell_is_accepted() -> None:
    """A second run on the same session: the first run's forced-exit sell is
    still `accepted` (in flight, and a non-terminal own sell)."""
    first = _decision(1, "AAA", known_at=NOW - timedelta(minutes=5))
    exits = _forced(
        {"AAA": 6.0},
        listings_at={"AAA": PREVIOUS},
        decisions=(first,),
        states={1: IN_FLIGHT},
        open_sells=(OpenSell("AAA", 6.0),),
    )
    assert exits == []


def test_spinoff_child_held_by_the_plan_gets_one_decision() -> None:
    """Plan first: the child is in the plan's held set (an open `left_universe`
    sell), so the receipt makes no second decision."""
    plan = _decision(
        1,
        "KID",
        decision="trade",
        reason="left_universe",
        rebalance_session=date(2026, 9, 30),
        known_at=NOW - timedelta(minutes=1),
    )
    exits = _forced(
        {"KID": 3.0},
        adjustments=(_receipt("KID", known_at=NOW - timedelta(minutes=2)),),
        decisions=(plan,),
        states={1: OPEN},
    )
    assert exits == []


def test_spinoff_child_with_an_open_forced_exit_is_reattempted_not_redecided() -> None:
    """Forced exit first: an expired mid-month receipt sale is open; no new
    decision, and `reattempt_exits` hands it back for its remainder."""
    receipt = _receipt("KID", known_at=NOW - timedelta(days=10))
    expired = _decision(1, "KID", reason="untargeted_receipt", known_at=NOW - timedelta(days=9))
    assert (
        _forced({"KID": 3.0}, adjustments=(receipt,), decisions=(expired,), states={1: OPEN}) == []
    )
    assert reattempt_exits((expired,), {1: OPEN}) == [expired]


def test_delisted_receipt_gets_one_decision() -> None:
    exits = _forced({"KID": 3.0}, listings_at={"KID": PREVIOUS}, adjustments=(_receipt("KID"),))
    assert [(e.security_id, e.reason) for e in exits] == [("KID", "delisted")]


def test_missing_asset_for_a_candidate_raises() -> None:
    with pytest.raises(ValueError, match="assets"):
        _forced({"AAA": 1.0}, listings_at={"AAA": PREVIOUS}, assets={})


def test_missing_asset_for_a_candidate_is_a_named_refusal() -> None:
    """A held name missing from the `assets` read raises `MissingAssetRefused`
    (a `ValueError`), naming it (owner decision on #648 item 2, #733)."""
    with pytest.raises(MissingAssetRefused, match="AAA is missing from the assets read"):
        _forced({"AAA": 1.0}, listings_at={"AAA": PREVIOUS}, assets={})


def test_decision_without_state_raises() -> None:
    with pytest.raises(ValueError, match="state"):
        _forced({"AAA": 1.0}, listings_at={"AAA": PREVIOUS}, decisions=(_decision(1, "AAA"),))


def test_negative_or_non_finite_holding_raises() -> None:
    with pytest.raises(ValueError):
        _forced({"AAA": -1.0}, listings_at={"AAA": PREVIOUS})
    with pytest.raises(ValueError):
        _forced({"AAA": float("nan")}, listings_at={"AAA": PREVIOUS})


def test_session_must_be_a_date() -> None:
    with pytest.raises(ValueError, match="session"):
        forced_exits({}, {}, {}, (), {}, (), (), session=NOW, pending_rebalance=None)  # type: ignore[arg-type]


# --- dust-blocked forced exits (#505) -----------------------------------------------


def test_whole_share_dust_exit_blocks_for_the_next_three_sessions() -> None:
    """A delisted, non-fractionable name left holding 0.4 shares: session 1
    makes exactly one exit; the sells phase would close it `skipped`/`dust`
    (phases._sell_skip); from then on, `forced_exits` makes nothing new for
    it, forever, not just the next session."""
    held = {"AAA": 0.4}
    assets = _assets(AAA=Flags(fractionable=False))
    exits = _forced(held, listings_at={"AAA": PREVIOUS}, assets=assets)
    assert len(exits) == 1
    exit_ = exits[0]
    assert (exit_.security_id, exit_.planned_quantity, exit_.whole_share) == ("AAA", 0.4, True)

    decision = replace(
        exit_.row(run_id=1, known_at=NOW, ingested_at=NOW, book_id="main"), decision_id=1
    )
    dust_event = DecisionEventRow(
        decision_id=1, run_id=1, status="skipped", reason="dust", known_at=NOW, ingested_at=NOW
    )
    state = decision_state(
        decision,
        [dust_event],
        [],
        [],
        [],
        pl.DataFrame(),
        lambda _sid: 10.0,
        RiskConfig(),
        session=S,
    )
    assert (state.state, state.event_reason) == (State.CLOSED, "dust")

    session = S
    for _ in range(3):
        session = next_session(session)
        later_exits = forced_exits(
            held,
            {"AAA": PREVIOUS},
            assets,
            (decision,),
            {1: state},
            (),
            (),
            session=session,
            pending_rebalance=PENDING,
        )
        assert later_exits == []


def test_fractionable_dusted_remainder_blocks_the_next_session() -> None:
    """A fractionable delisted name whose exit dusted below the minimum: the
    closing `dust` event still blocks a new exit."""
    held = {"AAA": 0.004}
    decision = _decision(1, "AAA", reason="delisted", planned_quantity=0.004)
    dust_event = DecisionEventRow(
        decision_id=1, run_id=1, status="skipped", reason="dust", known_at=NOW, ingested_at=NOW
    )
    state = decision_state(
        decision,
        [dust_event],
        [],
        [],
        [],
        pl.DataFrame(),
        lambda _sid: 10.0,
        RiskConfig(),
        session=S,
    )
    assert (state.state, state.event_reason) == (State.CLOSED, "dust")
    tomorrow = next_session(S)
    assert (
        forced_exits(
            held,
            {"AAA": PREVIOUS},
            _assets("AAA"),
            (decision,),
            {1: state},
            (),
            (),
            session=tomorrow,
            pending_rebalance=PENDING,
        )
        == []
    )


def test_settled_whole_share_exit_leaves_one_more_decision_then_dust_blocks() -> None:
    """2.4 shares, an order for the floor of 2 filled: the decision settles with
    a remainder of 0 (measured against that order), so the 0.4 left gets one
    more exit, which the phase closes `dust`; after that no new exit is made."""
    settled = DecisionState(State.SETTLED, remainder=Remainder(quantity=0.0, notional=0.0))
    first = _decision(1, "AAA", reason="delisted", planned_quantity=2.4)
    exits = _forced(
        {"AAA": 0.4},
        listings_at={"AAA": PREVIOUS},
        decisions=(first,),
        states={1: settled},
        assets=_assets(AAA=Flags(fractionable=False)),
    )
    assert [(e.security_id, e.planned_quantity) for e in exits] == [("AAA", 0.4)]
    dusted = _decision(2, "AAA", reason="delisted", planned_quantity=0.4, known_at=NOW)
    assert (
        _forced(
            {"AAA": 0.4},
            listings_at={"AAA": PREVIOUS},
            decisions=(first, dusted),
            states={
                1: settled,
                2: DecisionState(State.CLOSED, "skipped", event_reason="dust"),
            },
        )
        == []
    )


def test_settled_remainder_not_below_the_holding_blocks_a_new_exit() -> None:
    """A settled exit whose remainder covers the holding blocks a new one."""
    settled_at_dust = DecisionState(State.SETTLED, remainder=Remainder(quantity=0.4, notional=4.0))
    old = _decision(1, "AAA", reason="delisted", planned_quantity=2.4)
    assert (
        _forced(
            {"AAA": 0.4},
            listings_at={"AAA": PREVIOUS},
            decisions=(old,),
            states={1: settled_at_dust},
        )
        == []
    )


def test_holding_grown_past_the_dust_quantity_gets_a_new_exit() -> None:
    """A split or a new receipt that grows the holding past the dust-closed
    (or settled) decision's quantity is fail-safe: a new exit is made."""
    dust_closed = DecisionState(State.CLOSED, "skipped", event_reason="dust")
    old = _decision(1, "AAA", reason="delisted", planned_quantity=0.4)
    exits = _forced(
        {"AAA": 1.4},
        listings_at={"AAA": PREVIOUS},
        decisions=(old,),
        states={1: dust_closed},
    )
    assert [(e.security_id, e.reason) for e in exits] == [("AAA", "delisted")]


def test_closed_untradable_delisted_exit_is_still_re_evaluated() -> None:
    """A forced exit closed `untradable` (not `dust`) blocks nothing: the
    dust block is specific to the `dust` event reason."""
    exits = _forced(
        {"AAA": 0.4},
        listings_at={"AAA": PREVIOUS},
        decisions=(_decision(1, "AAA", reason="delisted"),),
        states={1: CLOSED_UNTRADABLE},
    )
    assert [(e.security_id, e.reason) for e in exits] == [("AAA", "delisted")]


def test_stop_exits_still_sells_the_dust_residue_of_a_dust_closed_name() -> None:
    """The block is `forced_exits` only: a `stop` run still makes the
    `window_stop` exit for a dust-closed delisted name, floored to 0 when
    whole-share, so the residue is counted."""
    dust_closed = DecisionState(State.CLOSED, "skipped", event_reason="dust")
    decision = _decision(1, "AAA", reason="delisted", planned_quantity=0.4)
    exits = _stop(
        {"AAA": 0.4},
        assets=_assets(AAA=Flags(fractionable=False)),
        decisions=(decision,),
        states={1: dust_closed},
    )
    assert exits == [ExitDecision("AAA", "window_stop", 0.0, whole_share=True, session=S)]


# --- reattempt_exits ---------------------------------------------------------------


def test_reattempt_returns_open_forced_exits_of_any_reason() -> None:
    expired = _decision(1, "AAA", reason="delisted")
    halted = _decision(2, "BBB", reason="window_stop")
    receipt = _decision(3, "CCC", reason="untargeted_receipt")
    settled = _decision(4, "DDD", reason="delisted")
    in_flight = _decision(5, "EEE", reason="delisted")
    plan = _decision(
        6, "FFF", decision="trade", reason="left_targets", rebalance_session=date(2026, 9, 30)
    )
    states = {1: OPEN, 2: OPEN, 3: OPEN, 4: SETTLED, 5: IN_FLIGHT, 6: OPEN}
    got = reattempt_exits((settled, receipt, halted, expired, in_flight, plan), states)
    assert got == [expired, halted, receipt]


def test_reattempt_halted_exit_through_decision_state() -> None:
    """The expired and the halted exit are open by `decision_state`; the
    settled one is not."""

    def order(decision_id: int, sid: str) -> OrderRow:
        return OrderRow(
            client_order_id=f"tp-{decision_id}",
            decision_id=decision_id,
            run_id=1,
            session=PREVIOUS,
            attempt=1,
            phase="exit",
            security_id=sid,
            symbol=sid,
            side="sell",
            quantity=10.0,
            sells_in_flight_at_submit=False,
            known_at=NOW - timedelta(days=1),
            ingested_at=NOW - timedelta(days=1),
        )

    def event(decision_id: int, status: str, reason: str | None = None) -> OrderEventRow:
        return OrderEventRow(
            client_order_id=f"tp-{decision_id}",
            status=status,
            reason=reason,
            known_at=NOW - timedelta(hours=20),
            ingested_at=NOW - timedelta(hours=20),
        )

    expired = _decision(1, "AAA")
    halted = _decision(2, "BBB", reason="window_stop")
    settled = _decision(3, "CCC", reason="untargeted_receipt")
    orders = [order(1, "AAA"), order(2, "BBB"), order(3, "CCC")]
    events = [
        event(1, "expired"),
        event(2, "cancel_requested", "halt"),
        event(2, "cancelled"),
        event(3, "filled"),
    ]
    fills = [
        OrderedFill(
            fill=FillRow(
                fill_id=1,
                client_order_id="tp-3",
                filled_at=NOW - timedelta(hours=20),
                quantity=10.0,
                price=100.0,
                price_implied=False,
                broker_fill_id="b1",
                source="broker_feed",
                known_at=NOW - timedelta(hours=20),
                ingested_at=NOW - timedelta(hours=20),
            ),
            side="sell",
            security_id="CCC",
            symbol="CCC",
            run_id=1,
            window_id=WINDOW,
        )
    ]
    decisions = (expired, halted, settled)
    states = {
        d.decision_id or 0: decision_state(
            d,
            [],
            orders,
            events,
            fills,
            pl.DataFrame(),
            lambda _sid: 100.0,
            RiskConfig(),
            session=S,
        )
        for d in decisions
    }
    assert reattempt_exits(decisions, states) == [expired, halted]


def test_reattempt_decision_without_state_raises() -> None:
    with pytest.raises(ValueError, match="state"):
        reattempt_exits((_decision(1, "AAA"),), {})


# --- stop_exits --------------------------------------------------------------------


def _stop(
    held: Mapping[str, float],
    *,
    residues: Mapping[str, float] | None = None,
    assets: Mapping[str, Flags] | None = None,
    decisions: tuple[DecisionRow, ...] = (),
    states: Mapping[int, DecisionState] | None = None,
    open_sells: tuple[OpenSell, ...] = (),
    untradable_this_run: frozenset[str] = frozenset(),
) -> list[ExitDecision]:
    return stop_exits(
        held,
        residues or {},
        assets if assets is not None else _assets(*held),
        decisions,
        states or {},
        open_sells,
        untradable_this_run,
        session=S,
        quantity_decimals=9,
    )


def test_stop_exits_every_held_name() -> None:
    exits = _stop({"BBB": 4.0, "AAA": 2.5})
    assert exits == [
        ExitDecision("AAA", "window_stop", 2.5, whole_share=False, session=S),
        ExitDecision("BBB", "window_stop", 4.0, whole_share=False, session=S),
    ]
    row = exits[0].row(run_id=9, known_at=NOW, ingested_at=NOW, book_id="main")
    assert (row.decision, row.reason, row.side, row.planned_quantity) == (
        "forced_exit",
        "window_stop",
        "sell",
        2.5,
    )


def test_stop_carried_residue_stops_only_the_excess() -> None:
    (exit_,) = _stop({"AAA": 10.0}, residues={"AAA": 3.0})
    assert exit_.planned_quantity == pytest.approx(7.0)


def test_stop_listed_residue_gets_no_exit() -> None:
    assert _stop({"AAA": 3.0}, residues={"AAA": 3.0}) == []


def test_stop_untradable_this_run_gets_no_exit() -> None:
    assert _stop({"AAA": 3.0, "BBB": 1.0}, untradable_this_run=frozenset({"AAA"})) == [
        ExitDecision("BBB", "window_stop", 1.0, whole_share=False, session=S)
    ]


def test_stop_whole_share_floor_on_a_flagged_name() -> None:
    exits = _stop(
        {"AAA": 7.6, "BBB": 7.6},
        residues={"AAA": 2.2},
        assets=_assets("BBB", AAA=Flags(fractionable=False)),
    )
    assert exits == [
        ExitDecision("AAA", "window_stop", 5.0, whole_share=True, session=S),
        ExitDecision("BBB", "window_stop", 7.6, whole_share=False, session=S),
    ]


def test_stop_whole_share_floor_of_zero_is_returned_for_the_phase() -> None:
    """A flagged excess below one share still gets its exit (quantity 0), so
    the sells phase journals its `dust` skip and the residue reads it."""
    (exit_,) = _stop({"AAA": 0.4}, assets=_assets(AAA=Flags(fractionable=False)))
    assert (exit_.planned_quantity, exit_.whole_share) == (0.0, True)


@pytest.mark.parametrize("state", [OPEN, IN_FLIGHT], ids=["open", "in_flight"])
def test_stop_open_or_in_flight_forced_exit_blocks(state: DecisionState) -> None:
    existing = _decision(1, "AAA", reason="delisted")
    assert _stop({"AAA": 3.0}, decisions=(existing,), states={1: state}) == []


def test_stop_settled_or_closed_forced_exit_does_not_block() -> None:
    old = _decision(1, "AAA", reason="window_stop")
    assert len(_stop({"AAA": 3.0}, decisions=(old,), states={1: SETTLED})) == 1
    assert len(_stop({"AAA": 3.0}, decisions=(old,), states={1: CLOSED_UNTRADABLE})) == 1


def test_stop_non_terminal_own_sell_blocks() -> None:
    assert _stop({"AAA": 3.0}, open_sells=(OpenSell("AAA", 1.0),)) == []


def test_stop_missing_asset_raises() -> None:
    with pytest.raises(ValueError, match="assets"):
        _stop({"AAA": 3.0}, assets={})


def test_stop_residue_above_holding_or_negative_raises() -> None:
    with pytest.raises(ValueError, match="residue"):
        _stop({"AAA": 3.0}, residues={"AAA": 4.0})
    with pytest.raises(ValueError, match="residue"):
        _stop({"AAA": 3.0}, residues={"AAA": -1.0})


# --- review fixes (PR #493) --------------------------------------------------------


def test_stop_whole_share_floor_survives_float_error() -> None:
    """1.4 - 0.4 is 0.9999999999999999 in floats: still one whole share."""
    (exit_,) = _stop(
        {"AAA": 1.4}, residues={"AAA": 0.4}, assets=_assets(AAA=Flags(fractionable=False))
    )
    assert exit_.planned_quantity == 1.0


def test_stop_untradable_this_run_must_not_be_a_str() -> None:
    with pytest.raises(ValueError, match="untradable_this_run"):
        stop_exits(
            {"AAA": 1.0}, {}, _assets("AAA"), (), {}, (), "AAA", session=S, quantity_decimals=9
        )


def test_row_refuses_known_at_on_another_new_york_date() -> None:
    (exit_,) = _forced({"AAA": 5.0}, listings_at={"AAA": PREVIOUS})
    # 02:00 UTC on S is still S-1 in New York.
    with pytest.raises(ValueError, match="New York date"):
        exit_.row(
            run_id=1,
            known_at=datetime(2026, 10, 14, 2, 0, tzinfo=UTC),
            ingested_at=NOW,
            book_id="main",
        )


def test_exit_on_a_split_ex_date_round_trips_through_remainder() -> None:
    """A 2:1 split with ex-date S: the holding on S is post-split, and the
    journaled exit's remainder before any order is exactly that holding."""
    (exit_,) = _forced({"AAA": 20.0}, listings_at={"AAA": PREVIOUS})
    row = replace(exit_.row(run_id=1, known_at=NOW, ingested_at=NOW, book_id="main"), decision_id=1)
    actions = pl.DataFrame(
        {
            "security_id": ["AAA"],
            "action_type": ["split"],
            "ex_date": [S],
            "ratio_or_amount": [2.0],
        }
    )
    left = remainder(row, [], [], [], actions, lambda _sid: 50.0, session=S)
    assert left.quantity == 20.0


def test_plan_decision_of_another_rebalance_does_not_block() -> None:
    lapsed = _decision(
        1, "AAA", decision="trade", reason="left_universe", rebalance_session=date(2026, 8, 31)
    )
    exits = _forced(
        {"AAA": 6.0}, listings_at={"AAA": PREVIOUS}, decisions=(lapsed,), states={1: OPEN}
    )
    assert [(e.security_id, e.reason) for e in exits] == [("AAA", "delisted")]


def test_settled_plan_decision_spends_the_receipt() -> None:
    """The plan sold the child at T_i; shares it buys later are the plan's."""
    receipt = _receipt("KID", known_at=NOW - timedelta(days=20), session=date(2026, 9, 23))
    plan = _decision(
        1,
        "KID",
        decision="trade",
        reason="left_universe",
        rebalance_session=date(2026, 9, 30),
        known_at=NOW - timedelta(days=14),
    )
    assert (
        _forced({"KID": 4.0}, adjustments=(receipt,), decisions=(plan,), states={1: SETTLED}) == []
    )


def test_plan_decision_before_the_receipt_does_not_spend_it() -> None:
    plan = _decision(
        1,
        "KID",
        decision="skip_zero",
        side=None,
        rebalance_session=date(2026, 8, 31),
        known_at=NOW - timedelta(days=40),
    )
    exits = _forced(
        {"KID": 3.0},
        adjustments=(_receipt("KID"),),
        decisions=(plan,),
        states={1: DecisionState(State.CLOSED, "skip_zero")},
    )
    assert [(e.security_id, e.reason) for e in exits] == [("KID", "untargeted_receipt")]


def test_reattempt_and_new_exits_never_name_the_same_security() -> None:
    """One hand-off per exit: over the same inputs, a name with an open forced
    exit is re-attempted and gets no new decision."""
    open_exit = _decision(1, "AAA")
    decisions = (open_exit,)
    states = {1: OPEN}
    new = _forced(
        {"AAA": 5.0, "BBB": 2.0},
        listings_at={"AAA": PREVIOUS, "BBB": PREVIOUS},
        decisions=decisions,
        states=states,
    )
    again = reattempt_exits(decisions, states)
    assert [e.security_id for e in new] == ["BBB"]
    assert [d.security_id for d in again] == ["AAA"]


def test_reattempt_refuses_repeated_ids_and_two_open_exits_for_one_name() -> None:
    with pytest.raises(ValueError, match="twice"):
        reattempt_exits((_decision(1, "AAA"), _decision(1, "AAA")), {1: OPEN})
    with pytest.raises(ValueError, match="two open forced exits"):
        reattempt_exits((_decision(1, "AAA"), _decision(2, "AAA")), {1: OPEN, 2: OPEN})

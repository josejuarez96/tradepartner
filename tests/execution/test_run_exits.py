"""The run-level forced exits (Phase 4 spec req 7 step 7, the "Forced exits"
criterion; plan T63d).

On the rebalance window of `test_run_trade.py` (its `Env`, through
`tracking_run` with the scripted fake and a settable clock): the F_0 run buys
DUALB, SPFT and TRNS, then a Form 25 for TRNS, accepted before close(S-1),
ends its listing at close(S-1) for a later session S. A spin-off receipt is an
`adjustments` row with the shares put in the fake's book, which has no
corporate actions of its own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from execution.test_run_trade import (
    F_0,
    F_0_PLUS_1,
    T_0,
    Env,
    at,
    bought,
    delist,
    env,
    split,
    window,
)
from tradepartner.adapters.broker import Asset, OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.planning import PlanOutcome
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
)

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests

MAY_3, MAY_6, MAY_7 = date(2019, 5, 3), date(2019, 5, 6), date(2019, 5, 7)
T_1, F_1 = date(2019, 5, 31), date(2019, 6, 3)
UNTRADABLE = Asset(tradable=False, fractionable=True, status="active", cusip=None)
TRADABLE = Asset(tradable=True, fractionable=True, status="active", cusip=None)


def exits(env: Env, security_id: str) -> list[tuple[int, str, str, int]]:
    """(decision_id, reason, side, run_id) of the name's forced exits."""
    return [
        (r[0], r[1], r[2], r[3])
        for r in env.query(
            "SELECT decision_id, reason, side, run_id FROM decisions "
            "WHERE decision = 'forced_exit' AND security_id = ? ORDER BY decision_id",
            [security_id],
        )
    ]


def exit_orders(env: Env, security_id: str) -> list[tuple[str, int, str, float | None, date]]:
    """(client_order_id, decision_id, phase, quantity, session) of the name's
    orders made for a forced exit."""
    return [
        (r[0], r[1], r[2], r[3], r[4])
        for r in env.query(
            "SELECT o.client_order_id, o.decision_id, o.phase, o.quantity, o.session "
            "FROM orders o JOIN decisions d USING (decision_id) "
            "WHERE d.decision = 'forced_exit' AND o.security_id = ? ORDER BY o.known_at",
            [security_id],
        )
    ]


def ended_before(env: Env, session: date, security_id: str = "SEC_TRANSFER") -> None:
    """The name's Form 25, accepted an hour before close(S-1)."""
    delist(env, security_id, session_close(previous_session(session)) - timedelta(hours=1))


def test_a_delisted_name_is_sold_whole_by_the_next_in_window_run(
    env: Env, window: PaperWindowRow
) -> None:
    """A run outside the submit window makes no exit; the next in-window run on
    a non-fill session sells the whole holding as `forced_exit` (`delisted`),
    a batch of its own (phase `exit`), and the name leaves the book."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    early = env.run(at(MAY_3, 11, 0))
    assert early.status == "ok"
    assert early.kind == "mark"
    assert exits(env, "SEC_TRANSFER") == []
    assert any("outside the submit window" in n for n in early.notes)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side, run_id) == ("delisted", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "exit", MAY_3)
    assert quantity == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6
    # The batch held no plan decision: no rebalance event is written for it.
    assert [e for e in env.rebalance_events() if e[3] == outcome.run_id] == []


def test_an_untradable_delisted_name_is_closed_marked_and_re_evaluated(
    env: Env, window: PaperWindowRow
) -> None:
    """Untradable at the run: the `forced_exit` decision is closed at once by a
    `skipped` row with event reason `untradable`, nothing is ordered, the name
    is marked at its last close with `tradable` false, and the next session
    makes a new decision; once tradable again it is sold whole."""
    bought(env)
    ended_before(env, MAY_3)
    env.fake.set_asset("TRNS", UNTRADABLE)
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((closed, reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert reason == "delisted"
    assert env.query(
        "SELECT status, reason, run_id FROM decision_events WHERE decision_id = ?", [closed]
    ) == [("skipped", "untradable", first.run_id)]
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.alerts("skip_cap") == []

    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[3] for e in exits(env, "SEC_TRANSFER")] == [first.run_id, second.run_id]
    (close, mark, tradable) = env.query(
        "SELECT p.close, m.mark_price, m.tradable FROM positions_daily m "
        "JOIN prices_daily p ON p.security_id = m.security_id AND p.session = m.session "
        "WHERE m.security_id = 'SEC_TRANSFER' AND m.session = ?",
        [MAY_3],
    )[0]
    assert mark == pytest.approx(close)
    assert tradable is False
    assert exit_orders(env, "SEC_TRANSFER") == []

    env.fake.set_asset("TRNS", TRADABLE, from_session=MAY_7)
    third = env.run(at(MAY_7))
    assert third.status == "ok", env.result(env.latest_run())
    assert len(exits(env, "SEC_TRANSFER")) == 3
    ((_coid, decision_id, _phase, _quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (decision_id, session) == (exits(env, "SEC_TRANSFER")[-1][0], MAY_7)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_an_expired_exit_is_re_attempted_for_its_remainder(
    env: Env, window: PaperWindowRow
) -> None:
    """The exit's order expires unfilled: the decision stays open, the next
    in-window run re-attempts it (a new order on its own session, the same
    decision) and makes no second forced exit."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    env.fake.script(Expire())
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert env.held()["TRNS"] == pytest.approx(held)
    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [decision_id]
    orders = exit_orders(env, "SEC_TRANSFER")
    assert [(o[1], o[4]) for o in orders] == [(decision_id, MAY_3), (decision_id, MAY_6)]
    assert orders[1][3] == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_spinoff_receipt_is_sold_whole_by_the_next_in_window_run(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    assert window.window_id is not None
    received = at(date(2019, 5, 2), 22, 0)
    env.append(
        AdjustmentRow(
            window_id=window.window_id,
            session=date(2019, 5, 2),
            kind="spinoff_receipt",
            security_id="SEC_SPY",
            quantity=10.0,
            known_at=received,
            ingested_at=received,
        )
    )
    env.fake._net_quantity["SPY"] = env.fake._net_quantity.get("SPY", 0) + 10  # the spin-off
    early = env.run(at(MAY_3, 14, 30))
    assert early.status == "ok", env.result(env.latest_run())
    assert exits(env, "SEC_SPY") == []
    assert env.held()["SPY"] == pytest.approx(10.0)
    outcome = env.run(at(MAY_6))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_SPY")
    assert (reason, side, run_id) == ("untargeted_receipt", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, _session),) = exit_orders(env, "SEC_SPY")
    assert (ordered, phase, quantity) == (decision_id, "exit", pytest.approx(10.0))
    assert env.held().get("SPY", 0.0) < 1e-6


def test_a_plan_target_whose_listing_ended_is_journaled_skip_delisted(
    env: Env, window: PaperWindowRow
) -> None:
    """A target (not held) whose listing ended between close(T_0) and
    close(S-1) is `skip_delisted` on the catch-up: closed, never ordered, and
    no forced exit is made for it."""
    delist(env, "SEC_DUAL_B", at(F_0, 19, 0))
    outcome = env.run(at(F_0_PLUS_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.query(
        "SELECT decision, rebalance_session FROM decisions WHERE security_id = 'SEC_DUAL_B'"
    ) == [("skip_delisted", T_0)]
    assert exits(env, "SEC_DUAL_B") == []
    assert [o for o in env.orders() if o[1] == "SEC_DUAL_B"] == []


def test_on_a_fill_session_exits_join_the_sells_phase(env: Env, window: PaperWindowRow) -> None:
    """A held name whose listing ended at close(F_1 - 1): the F_1 run's forced
    exit is ordered in the rebalance's sells phase (phase `sell`), before its
    buys, in the same batch."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, F_1)
    outcome = env.run(at(F_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "rebalance"
    ((decision_id, reason, _side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, run_id) == ("delisted", outcome.run_id)
    ((coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "sell", F_1)
    assert quantity == pytest.approx(held)
    accepted = dict(
        env.query("SELECT client_order_id, known_at FROM order_events WHERE status = 'accepted'")
    )
    buys = [o[0] for o in env.orders() if o[5] == outcome.run_id and o[2] == "buy"]
    assert buys
    assert all(accepted[coid] <= accepted[buy] for buy in buys)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_no_look_ahead_a_filing_after_close_s_minus_1_waits_a_session(
    env: Env, window: PaperWindowRow
) -> None:
    """A Form 25 accepted after close(S-1), before the run on S, is not known
    to S: no forced exit on S; the next session sells the name whole. A 2:1
    split with ex-date S known only after close(S-1) is not applied either, so
    the exit sells the holding the broker reports, not twice it."""
    bought(env)
    held = env.held()["TRNS"]
    after_cut = session_close(previous_session(MAY_3)) + timedelta(hours=1)
    delist(env, "SEC_TRANSFER", after_cut)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert exits(env, "SEC_TRANSFER") == []
    assert exit_orders(env, "SEC_TRANSFER") == []
    late_split = session_close(previous_session(MAY_6)) + timedelta(hours=1)
    split(env, "SEC_TRANSFER", MAY_6, 2.0, late_split)
    nxt = env.run(at(MAY_6))
    assert nxt.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, _side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, run_id) == ("delisted", nxt.run_id)
    ((_coid, ordered, _phase, quantity, _session),) = exit_orders(env, "SEC_TRANSFER")
    assert ordered == decision_id
    assert quantity == pytest.approx(held)


def test_an_exit_journaled_as_the_window_closes_waits_for_the_next_run(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window is checked again just before `execute`: a run whose clock
    leaves the window while it reads and journals the exits submits nothing,
    and the next in-window run re-attempts the open exit."""
    bought(env)
    ended_before(env, MAY_3)
    original = run_module._forced_exits

    def slow(context: run_module.StepContext, **kwargs: object) -> object:
        handed = original(context, **kwargs)  # type: ignore[arg-type]
        _start, end = run_module.submit_window(env.settings, MAY_3)
        env.clock.now = end + timedelta(seconds=1)
        return handed

    monkeypatch.setattr(run_module, "_forced_exits", slow)
    first = env.run(at(MAY_3, 13, 55))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert any("outside the submit window" in n for n in first.notes)
    monkeypatch.setattr(run_module, "_forced_exits", original)
    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [decision_id]
    assert [o[1] for o in exit_orders(env, "SEC_TRANSFER")] == [decision_id]


def test_a_second_run_on_the_session_leaves_an_accepted_exit_sell_alone(
    env: Env, window: PaperWindowRow
) -> None:
    """The forced-exit criterion at run level: the first in-window run's exit
    sell is still `accepted` (unfilled) at step 7b's deadline; a second run on
    the same session collects it at step 3, makes no new forced exit and
    submits nothing for the name (#549)."""
    bought(env)
    ended_before(env, MAY_3)
    env.fill_on_sleep = False
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    ((coid, _decision, _phase, _quantity, _session),) = exit_orders(env, "SEC_TRANSFER")
    assert [o.client_order_id for o in env.fake.open_orders()] == [coid]
    submitted = len(env.submits())
    second = env.run(at(MAY_3, 12, 45))
    assert second.status == "ok", env.result(env.latest_run())
    assert not any("outside the submit window" in n for n in second.notes)
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [decision_id]
    assert [o[0] for o in exit_orders(env, "SEC_TRANSFER")] == [coid]
    assert len(env.submits()) == submitted
    assert [o.client_order_id for o in env.fake.open_orders()] == [coid]


def test_a_second_run_with_a_reused_ticker_leaves_an_accepted_exit_sell_alone(
    env: Env, window: PaperWindowRow
) -> None:
    """Same shape as `test_a_second_run_on_the_session_leaves_an_accepted_exit_sell_alone`,
    but a later issuer picks up TRNS between the two runs. The second run's
    sell phase has no attempt left for SEC_TRANSFER (its exit sell is
    already `accepted`), so the reused-ticker check (#568 item 2), now
    scoped in `_phase_assets` to the phase's own attempt scope, never looks
    at it: the run still ends `ok` with the open order untouched, not the
    halt the check would have raised had it still run in `_read_book` for
    every name of the batch's rows regardless of whether this phase
    attempts it (the regression this PR's review caught)."""
    bought(env)
    ended_before(env, MAY_3)
    env.fill_on_sleep = False
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    ((coid, _decision, _phase, _quantity, _session),) = exit_orders(env, "SEC_TRANSFER")
    assert [o.client_order_id for o in env.fake.open_orders()] == [coid]
    submitted = len(env.submits())

    reused_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_TRANSFER_NEW_ISSUER",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": F_0_PLUS_1,
            "known_at": reused_known_at,
            "ingested_at": reused_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    second = env.run(at(MAY_3, 12, 45))

    assert second.status == "ok", env.result(env.latest_run())
    assert not any("outside the submit window" in n for n in second.notes)
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [decision_id]
    assert [o[0] for o in exit_orders(env, "SEC_TRANSFER")] == [coid]
    assert len(env.submits()) == submitted
    assert [o.client_order_id for o in env.fake.open_orders()] == [coid]


def test_a_reused_ticker_under_a_kill_engaged_after_the_run_level_check_skips(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reused-ticker check (#568 item 2) must not run before the
    wrapper's own kill-switch read (`_engaged`, inside `execute`): a kill
    written after step 6's run-level check (e.g. during step 4's
    `positions` read, mirroring
    `test_a_kill_written_during_step_4_is_caught_before_any_submit` in
    test_run_trade.py) but before the sells phase still ends
    `skipped_kill_switch`, not a halt, even with a reused ticker present
    (the regression this PR's review caught: the check used to run in
    `_read_book`, before `_engaged` was ever read)."""
    assert window.window_id is not None
    window_id = window.window_id
    bought(env)
    held = env.held()["TRNS"]
    submitted_before = len(env.submits())
    ended_before(env, MAY_3)
    reused_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_TRANSFER_NEW_ISSUER",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": F_0_PLUS_1,
            "known_at": reused_known_at,
            "ingested_at": reused_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    original = env.fake.positions
    killed: list[int | switch.WriteFailed] = []

    def kill_at_step_4() -> object:
        if not killed:
            killed.append(
                switch.engage(
                    env.settings,
                    env.clock,
                    window_id=window_id,
                    source="owner",
                    reason="the owner kills the run before the sells phase",
                )
            )
        return original()

    monkeypatch.setattr(env.fake, "positions", kill_at_step_4)

    outcome = env.run(at(MAY_3))

    assert [isinstance(k, int) for k in killed] == [True]
    assert outcome.status == "skipped_kill_switch", env.result(env.latest_run())
    assert len(env.submits()) == submitted_before
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.held()["TRNS"] == pytest.approx(held)


def test_an_unanswered_delisted_name_halts_the_whole_sells_phase(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's decision on #410 (2026-10-01) keeps this fail-closed until
    T48c: a forced-exit name the broker's `assets` read does not answer for
    (`wrapper._phase_assets`) halts the whole sells phase, the rebalance's
    sells included, before anything is submitted (#568 item 1)."""
    bought(env)
    held = env.held()["TRNS"]
    submitted_before = len(env.submits())
    ended_before(env, MAY_3)
    original_assets = env.fake.assets

    def unanswering(symbols: list[str]) -> dict[str, Asset]:
        # The run's own pre-read (`run._Run.assets_read`, for every held
        # position) must still answer, so only the wrapper's own sells-phase
        # read (`wrapper._phase_assets`, asked for just the forced-exit
        # name) goes unanswered.
        answer = original_assets(symbols)
        if list(symbols) == ["TRNS"]:
            answer.pop("TRNS", None)
        return answer

    monkeypatch.setattr(env.fake, "assets", unanswering)

    with pytest.raises(ValueError, match=r"the assets read did not answer for \['TRNS'\]"):
        env.run(at(MAY_3))

    assert len(env.submits()) == submitted_before
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.held()["TRNS"] == pytest.approx(held)
    ((_decision_id, reason, side, _run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side) == ("delisted", "sell")
    status, fault_type, _message = env.result(env.latest_run())
    assert (status, fault_type) == ("halted", "ValueError")


def test_a_reused_ticker_never_resolves_to_the_new_issuers_asset(
    env: Env, window: PaperWindowRow
) -> None:
    """SEC_TRANSFER's ticker (TRNS) is picked up by a new issuer, a listing
    known at close(S-1): the forced exit for the delisted SEC_TRANSFER must
    never resolve its `assets` read against that reused symbol, since the
    broker would answer for the new issuer, not the one this run means. It
    fails closed instead of submitting anything (#568 item 2)."""
    bought(env)
    held = env.held()["TRNS"]
    submitted_before = len(env.submits())
    ended_before(env, MAY_3)
    reused_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_TRANSFER_NEW_ISSUER",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": F_0_PLUS_1,
            "known_at": reused_known_at,
            "ingested_at": reused_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    with pytest.raises(ValueError, match="TRNS"):
        env.run(at(MAY_3))

    assert len(env.submits()) == submitted_before
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.held()["TRNS"] == pytest.approx(held)
    status, fault_type, _message = env.result(env.latest_run())
    assert (status, fault_type) == ("halted", "ValueError")


def test_a_reusing_listing_known_after_close_s_minus_1_is_ignored(
    env: Env, window: PaperWindowRow
) -> None:
    """The reused-ticker check (#568 item 2) is itself point-in-time: a new
    issuer's listing known only after close(S-1) (`cut`) is not in the
    universe this run reads (`listings_as_of`'s own `known_at` filter), so
    it is no collision and the forced exit still submits normally."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    cut = session_close(previous_session(MAY_3))
    reused_known_at = cut + timedelta(minutes=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_TRANSFER_NEW_ISSUER",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": F_0_PLUS_1,
            "known_at": reused_known_at,
            "ingested_at": reused_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    outcome = env.run(at(MAY_3))

    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side, run_id) == ("delisted", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "exit", MAY_3)
    assert quantity == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_reusing_listings_valid_from_after_s_is_ignored(env: Env, window: PaperWindowRow) -> None:
    """Same shape, but the reusing listing is known well before close(S-1)
    while its own `valid_from` does not take effect until after S:
    `current_listings` (as of day=S) excludes it on that filter alone, so
    it is no collision either, and the exit still submits."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    future_valid_from = MAY_3 + timedelta(days=1)
    ghost_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_TRANSFER_NEW_ISSUER",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": future_valid_from,
            "known_at": ghost_known_at,
            "ingested_at": ghost_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    outcome = env.run(at(MAY_3))

    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side, run_id) == ("delisted", "sell", outcome.run_id)
    ((_coid, ordered, phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (ordered, phase, session) == (decision_id, "exit", MAY_3)
    assert quantity == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_an_older_owner_of_a_now_delisted_names_ticker_never_blocks_its_exit(
    env: Env, window: PaperWindowRow
) -> None:
    """The reused-ticker check (#568 item 2) fires only for a *later* issuer:
    a now-dead security that held SEC_TRANSFER's ticker before SEC_TRANSFER
    itself did (SEC_TRANSFER took the ticker over, not the other way round)
    never blocks SEC_TRANSFER's own forced exit."""
    bought(env)
    ended_before(env, MAY_3)
    ((own_valid_from,),) = env.query(
        "SELECT valid_from FROM listings WHERE security_id = 'SEC_TRANSFER' "
        "ORDER BY valid_from DESC LIMIT 1"
    )
    ghost_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=2)
    env.insert(
        "listings",
        {
            "security_id": "SEC_GHOST_HELD_TRNS_FIRST",
            "ticker": "TRNS",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": own_valid_from - timedelta(days=1),
            "known_at": ghost_known_at,
            "ingested_at": ghost_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    outcome = env.run(at(MAY_3))

    assert outcome.status == "ok", env.result(env.latest_run())
    ((_decision_id, reason, side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, side, run_id) == ("delisted", "sell", outcome.run_id)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_stale_delisted_securitys_shared_ticker_never_blocks_a_live_name(
    env: Env, window: PaperWindowRow
) -> None:
    """The reused-ticker check (#568 item 2) only ever looks at a name whose
    own listing has ended: a dormant, unrelated security that happens to
    share a currently-held, still-listed name's ticker (SPFT, never
    delisted in this test) never blocks that name's own run, even though its
    `valid_from` has the same shape a real reuse would."""
    bought(env)
    held = env.held()["SPFT"]
    ghost_known_at = session_close(previous_session(MAY_3)) - timedelta(hours=1)
    env.insert(
        "listings",
        {
            "security_id": "SEC_GHOST_SHARES_SPFT",
            "ticker": "SPFT",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": F_0_PLUS_1,
            "known_at": ghost_known_at,
            "ingested_at": ghost_known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )

    outcome = env.run(at(MAY_3))

    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.held()["SPFT"] == pytest.approx(held)


class _NoBroker:
    """A gate or broker that fails the test on any use."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"{name} used while fills are lagging")


def _lagging_context(env: Env, plan: object) -> run_module.StepContext:
    assert env.window is not None
    run = PaperRunRow(
        run_id=1,
        window_id=env.window.window_id,  # type: ignore[arg-type]
        session=MAY_3,
        kind="mark",
        started_at=at(MAY_3),
        invoked_by="tty",
        code_version="test",
        known_at=at(MAY_3),
        ingested_at=at(MAY_3),
    )
    return run_module.StepContext(
        settings=env.settings,
        connect=env.connect,
        gate=_NoBroker(),  # type: ignore[arg-type]
        window=env.window,
        frozen=RiskConfig(),
        run=run,
        session=MAY_3,
        assets={},
        assets_read=lambda _symbols: {},
        plan=plan,  # type: ignore[arg-type]
        lagging=True,
    )


@pytest.fixture
def no_exit_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forced exits read while fills are lagging")

    monkeypatch.setattr(run_module, "_forced_exits", refused)


def test_no_forced_exit_while_fills_lag_on_a_non_fill_session(
    env: Env, window: PaperWindowRow, no_exit_read: None
) -> None:
    """Under `fills_lagging` the exits step makes no forced exit: no read, no
    clock, no batch, a note on the run."""
    context = _lagging_context(env, None)
    assert run_module.exits_step(context) is None
    assert context.notes == ["fills_lagging: no forced exit made"]


def test_nothing_traded_while_fills_lag_on_a_fill_session(
    env: Env, window: PaperWindowRow, no_exit_read: None
) -> None:
    """A `lagging` plan outcome: no forced exit, no batch, the rebalance left
    pending, a note on the run."""
    context = _lagging_context(env, PlanOutcome(T_0, "lagging"))
    assert run_module.trade_step(context) is None
    assert context.notes == [
        "fills_lagging: nothing planned or traded; the rebalance stays pending"
    ]


# --- an open order's name with no bar (#685, #569 owner option (b)) ---------------------

NO_BAR = "SEC_NO_BAR"  # no `prices_daily` row at all: no reference price on any S
NO_BAR_SYMBOL = "NOBR"


def stale_order(
    env: Env,
    made: datetime,
    *,
    side: str = "buy",
    quantity: float | None = 2.0,
    notional: float | None = None,
) -> str:
    """An earlier run's non-terminal order of `NO_BAR` (a quantity buy unless
    told otherwise), accepted by the broker and never filled: the stale order
    #569 says must not stop the run. Its name is neither held nor in a
    pending rebalance."""
    run_id = env.latest_run()
    (decision_id,) = env.append(
        DecisionRow(
            run_id=run_id,
            rebalance_session=None,
            security_id=NO_BAR,
            side=side,
            planned_quantity=quantity,
            planned_notional=notional,
            whole_share=False,
            decision="forced_exit",
            reason="delisted",
            known_at=made,
            ingested_at=made,
        )
    )
    coid = f"tp-stale-no-bar-{side}"
    env.prices[NO_BAR_SYMBOL] = 10.0
    placed = env.fake.submit(
        OrderRequest(
            coid,
            NO_BAR_SYMBOL,
            Side.BUY if side == "buy" else Side.SELL,
            quantity=quantity,
            notional=notional,
        )
    )
    env.append(
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,  # type: ignore[arg-type]
            run_id=run_id,
            session=made.date(),
            attempt=1,
            phase="exit",
            security_id=NO_BAR,
            symbol=NO_BAR_SYMBOL,
            side=side,
            quantity=quantity,
            notional=notional,
            sells_in_flight_at_submit=False,
            known_at=made,
            ingested_at=made,
        ),
        OrderEventRow(client_order_id=coid, status="pending", known_at=made, ingested_at=made),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=placed.broker_order_id,
            known_at=made,
            ingested_at=made,
        ),
    )
    return coid


def fill_all_but(env: Env, coid: str) -> None:
    """Fill every open order but `coid` when the run sleeps."""
    env.fill_on_sleep = False

    def fill(_now: datetime) -> None:
        for order in env.fake.open_orders():
            if order.client_order_id != coid:
                env.fake.simulate_fill(order.client_order_id)

    env.on_sleep.append(fill)


@pytest.mark.parametrize(
    ("side", "quantity", "notional"),
    [
        pytest.param("buy", 2.0, None, id="quantity-buy"),
        # `unfilled_sells` would price a notional sell; step 7's open sells
        # are the held names' only, so this one is never priced (#685).
        pytest.param("sell", None, 30.0, id="notional-sell"),
    ],
)
def test_an_unrelated_stale_order_with_no_bar_does_not_stop_a_mark_run(
    env: Env,
    window: PaperWindowRow,
    side: str,
    quantity: float | None,
    notional: float | None,
) -> None:
    """Step 7's read prices the held names strictly; a name only a stale open
    order (and its in-flight decision) brings in, with no bar at close(S-1),
    is priced only if a number reads it, and none does here (#685)."""
    bought(env)
    stale_order(env, at(F_0_PLUS_1, 15, 0), side=side, quantity=quantity, notional=notional)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "mark"


def test_an_unrelated_stale_order_with_no_bar_does_not_block_a_forced_exit(
    env: Env, window: PaperWindowRow
) -> None:
    """End to end (#685, #569 (b)): the stale order neither halts step 7 nor the
    wrapper's sells-only exit batch; the delisted name is sold whole."""
    bought(env)
    coid = stale_order(env, at(F_0_PLUS_1, 15, 0))
    fill_all_but(env, coid)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, reason, _side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, run_id) == ("delisted", outcome.run_id)
    ((_coid, ordered, _phase, quantity, _session),) = exit_orders(env, "SEC_TRANSFER")
    assert ordered == decision_id
    assert quantity == pytest.approx(held)
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_held_name_with_no_bar_still_halts_step_7(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The held names stay strict: a held name whose reference price cannot be
    read still stops the run at step 7's read, before any exit is journaled."""
    bought(env)
    ended_before(env, MAY_3)
    real = run_module.planning.reference_prices

    def no_trns(conn: object, session: date, names: object, actions: object) -> object:
        if "SEC_TRANSFER" in set(names):  # type: ignore[call-overload]
            raise ValueError("no reference price for SEC_TRANSFER")
        return real(conn, session, names, actions)  # type: ignore[arg-type]

    monkeypatch.setattr(run_module.planning, "reference_prices", no_trns)
    with pytest.raises(ValueError, match="SEC_TRANSFER"):
        env.run(at(MAY_3))
    assert exits(env, "SEC_TRANSFER") == []


def test_an_open_decision_of_a_name_with_no_bar_raises_its_reason_when_read(
    env: Env, window: PaperWindowRow
) -> None:
    """Deferred, not dropped (#685): an open decision of a name with no bar,
    whose remainder step 7 must price, still stops the run, with the reason
    the price read failed, before any exit is journaled."""
    bought(env)
    made = at(F_0_PLUS_1, 15, 0)
    env.append(
        DecisionRow(
            run_id=env.latest_run(),
            rebalance_session=None,
            security_id=NO_BAR,
            side="sell",
            planned_notional=30.0,
            whole_share=False,
            decision="forced_exit",
            reason="delisted",
            known_at=made,
            ingested_at=made,
        )
    )
    ended_before(env, MAY_3)
    with pytest.raises(ValueError, match=r"no reference price for SEC_NO_BAR: no bar on or before"):
        env.run(at(MAY_3))
    assert exits(env, "SEC_TRANSFER") == []

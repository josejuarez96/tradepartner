"""The `stop` run (Phase 4 spec req 14, the run-side `paper stop` criteria;
plan T63f).

On the rebalance window of `test_run_trade.py` (its `Env`, through
`tracking_run` with the scripted fake and a settable clock): the F_0 run buys
DUALB, SPFT and TRNS a third each, then `paper stop` (`window.stop`) appends
the `requested` row and every later `paper run` is of kind `stop`. The fake
fills every open order when the run sleeps, so a `stop` run inside the submit
window flattens what it sells.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from execution.test_run_exits import (
    UNTRADABLE,
    _lagging_context,
    ended_before,
    exit_orders,
    exits,
)
from execution.test_run_trade import (
    F_0,
    T_0,
    Env,
    at,
    bought,
    env,
    window,
)
from tradepartner.adapters.broker import Asset
from tradepartner.adapters.fake_broker import Expire
from tradepartner.config import Settings
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.exits import MissingAssetRefused
from tradepartner.execution.plan import stop_session
from tradepartner.execution.window import stop
from tradepartner.store.db import open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    PaperWindowRow,
    kill_switch_events_for,
    runs_for,
)

__all__ = ["env", "window"]  # the fixtures, re-exported for this module's tests

MAY_2, MAY_3, MAY_6, MAY_7 = date(2019, 5, 2), date(2019, 5, 3), date(2019, 5, 6), date(2019, 5, 7)
T_1, F_1 = date(2019, 5, 31), date(2019, 6, 3)
TRADABLE = Asset(tradable=True, fractionable=True, status="active", cusip=None)
PLAN_TABLES = ("trials", "signals", "paper_plans")


class Crash(BaseException):
    """A process death: not an `Exception`, so no result row is written."""


def request_stop(env: Env, when: datetime) -> None:
    """`paper stop --reason` at `when`: the `requested` row."""
    env.clock.now = when
    result = stop(env.settings, env.connect, env.fake, env.clock, "end of the test window")
    assert result.state == "requested"


def run_kind(env: Env, run_id: int | None) -> str:
    return str(env.query("SELECT kind FROM paper_runs WHERE run_id = ?", [run_id])[0][0])


def stop_exits(env: Env, security_id: str) -> list[tuple[int, str, str, int]]:
    return [e for e in exits(env, security_id) if e[1] == "window_stop"]


def outcomes(env: Env, security_id: str) -> list[tuple[str, date, str, float | None]]:
    """(client_order_id, through_session, kind, mark_price) of the name's outcomes."""
    return [
        (r[0], r[1], r[2], r[3])
        for r in env.query(
            "SELECT o.client_order_id, o.through_session, o.kind, o.mark_price "
            "FROM outcomes o JOIN orders r USING (client_order_id) "
            "WHERE r.security_id = ? ORDER BY r.known_at, o.kind",
            [security_id],
        )
    ]


def buy_id(env: Env, session: date, security_id: str, attempt: int = 1) -> str:
    prefix = env.settings.paper.order_id_prefix
    return f"{prefix}-{session:%Y%m%d}-{security_id}-buy-{attempt}"


def sell_id(env: Env, session: date, security_id: str, attempt: int = 1) -> str:
    prefix = env.settings.paper.order_id_prefix
    return f"{prefix}-{session:%Y%m%d}-{security_id}-sell-{attempt}"


def carry(env: Env, window: PaperWindowRow, security_id: str, symbol: str, quantity: float) -> None:
    """A residue carried from a previous window: the `carried_residue` row
    `paper start` journals, and the shares on the fake's book."""
    assert window.window_id is not None
    env.append(
        AdjustmentRow(
            window_id=window.window_id,
            session=T_0,
            kind="carried_residue",
            origin="dust",
            security_id=security_id,
            quantity=quantity,
            known_at=window.started_at,
            ingested_at=window.started_at,
        )
    )
    env.fake._net_quantity[symbol] = env.fake._net_quantity.get(symbol, Decimal(0)) + Decimal(
        str(quantity)
    )


# --- the stop session -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        pytest.param(datetime(2019, 5, 2, 12, 0, tzinfo=UTC), MAY_2, id="before-the-open"),
        pytest.param(datetime(2019, 5, 2, 22, 0, tzinfo=UTC), MAY_2, id="after-the-close"),
        pytest.param(datetime(2019, 5, 4, 15, 0, tzinfo=UTC), MAY_6, id="saturday"),
        pytest.param(datetime(2019, 5, 7, 2, 0, tzinfo=UTC), MAY_6, id="monday-night-in-new-york"),
    ],
)
def test_the_stop_session_contains_the_request_or_is_the_next_session(
    requested: datetime, expected: date
) -> None:
    assert stop_session(requested) == expected


def test_run_stop_session_is_the_one_rule_in_plan() -> None:
    """`run.stop_session` is a re-export of `plan.stop_session` (#592): one rule
    for `run`, `report` and `check`, not a copy that can drift."""
    assert run_module.stop_session is stop_session


# --- the stop run -------------------------------------------------------------------------


def test_a_stop_run_plans_nothing_and_misses_the_pending_rebalance(
    env: Env, window: PaperWindowRow
) -> None:
    """A stop requested before F_0: the F_0 run is kind `stop`, opens no plan
    trial, writes `missed` (`window_stop`) for T_0, and with nothing held
    submits nothing."""
    request_stop(env, at(T_0, 22, 0))
    before = {table: env.count(table) for table in PLAN_TABLES}
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "stop"
    assert {table: env.count(table) for table in PLAN_TABLES} == before
    assert env.count("decisions") == 0
    assert env.rebalance_events() == [(T_0, "missed", "window_stop", outcome.run_id)]
    assert env.submits() == []
    # A later stop run writes no second row for it.
    later = env.run(at(MAY_2))
    assert later.status == "ok", env.result(env.latest_run())
    assert env.rebalance_events() == [(T_0, "missed", "window_stop", outcome.run_id)]


def test_a_stop_run_writes_missed_window_stop_rows_even_while_engaged(
    env: Env, window: PaperWindowRow
) -> None:
    """Step 3's `_stop_missed` runs before step 6's engaged-switch check: a
    stop run with the switch already engaged still writes `missed`
    (`window_stop`) for the pending rebalance, before it ends
    `skipped_kill_switch` (#598)."""
    assert window.window_id is not None
    request_stop(env, at(T_0, 22, 0))
    engaged = switch.engage(
        env.settings,
        lambda: at(T_0, 23, 0),
        window_id=window.window_id,
        source="owner",
        reason="seeded: already engaged",
    )
    assert isinstance(engaged, int)
    outcome = env.run(at(F_0))
    assert outcome.status == "skipped_kill_switch", env.result(env.latest_run())
    assert outcome.kind == "stop"
    assert env.rebalance_events() == [(T_0, "missed", "window_stop", outcome.run_id)]


def test_a_stop_run_sells_every_holding_through_the_wrapper_in_the_window(
    env: Env, window: PaperWindowRow
) -> None:
    """Outside the submit window a stop run makes no exit; the next in-window
    one makes a `window_stop` forced exit per held name for its whole holding,
    sold in the sells phase (phase `exit`), and the account is flat."""
    bought(env)
    held = env.held()
    request_stop(env, at(F_0, 22, 0))
    late = env.run(at(MAY_2, 15, 0))
    assert late.status == "ok", env.result(env.latest_run())
    assert late.kind == "stop"
    assert env.count("decisions") == 3  # the plan's three buys only
    assert any("outside the submit window" in n for n in late.notes)
    outcome = env.run(at(MAY_3))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "stop"
    for security_id, symbol in (
        ("SEC_DUAL_B", "DUALB"),
        ("SEC_SPLIT_FUTURE", "SPFT"),
        ("SEC_TRANSFER", "TRNS"),
    ):
        ((decision_id, _reason, side, run_id),) = stop_exits(env, security_id)
        assert (side, run_id) == ("sell", outcome.run_id)
        ((_coid, ordered, phase, quantity, session),) = exit_orders(env, security_id)
        assert (ordered, phase, session) == (decision_id, "exit", MAY_3)
        assert quantity == pytest.approx(held[symbol])
    assert env.held() == {} or max(env.held().values()) < 1e-6
    assert [s.side for s in env.submits()][-3:] == ["sell", "sell", "sell"]


def test_a_stop_run_skips_step_7bs_unspent_cash_check(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop run sells to cash, so step 7b's broker-cash read there would be
    a false positive (T63f): the step-7b `unspent_cash` check is skipped in a
    `stop` run (step 3's own check, before any stop sell, still runs) (#563).
    `_Run._executed` is stubbed to find nothing at step 3 and a rebalance at
    step 7b, as a plain run's own fall-back test does; here the run is a
    `stop`, so no alert is written either way."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    calls = iter([[], [T_0]])
    monkeypatch.setattr(run_module._Run, "_executed", lambda self, actions, prices: next(calls))
    outcome = env.run(at(MAY_2))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "stop"
    assert env.alerts("unspent_cash") == []


def test_an_open_delisted_exit_is_re_attempted_without_a_second_sell(
    env: Env, window: PaperWindowRow
) -> None:
    """TRNS's `delisted` exit expires on a mark run; the stop run re-attempts
    it (same decision, one sell for the name), makes no `window_stop` exit for
    TRNS, and exits the other two names."""
    bought(env)
    ended_before(env, MAY_3)
    env.fake.script(Expire())
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((delisted, reason, _side, _run),) = exits(env, "SEC_TRANSFER")
    assert reason == "delisted"
    request_stop(env, at(MAY_3, 22, 0))
    outcome = env.run(at(MAY_6))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in exits(env, "SEC_TRANSFER")] == [delisted]
    orders = exit_orders(env, "SEC_TRANSFER")
    assert [(o[1], o[4]) for o in orders] == [(delisted, MAY_3), (delisted, MAY_6)]
    assert len(stop_exits(env, "SEC_DUAL_B")) == len(stop_exits(env, "SEC_SPLIT_FUTURE")) == 1
    may_6_sells = [o for o in env.orders() if o[4] == MAY_6 and o[2] == "sell"]
    assert sorted(o[1] for o in may_6_sells) == ["SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER"]
    assert env.held() == {} or max(env.held().values()) < 1e-6


def test_an_expired_window_stop_exit_is_re_attempted_by_the_next_stop_run(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    held = env.held()["DUALB"]
    request_stop(env, at(F_0, 22, 0))
    env.fake.script(Expire(), client_order_id=sell_id(env, MAY_2, "SEC_DUAL_B"))
    first = env.run(at(MAY_2))
    assert first.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = stop_exits(env, "SEC_DUAL_B")
    assert env.held()["DUALB"] == pytest.approx(held)
    second = env.run(at(MAY_3))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in stop_exits(env, "SEC_DUAL_B")] == [decision_id]
    orders = exit_orders(env, "SEC_DUAL_B")
    assert [(o[1], o[4]) for o in orders] == [(decision_id, MAY_2), (decision_id, MAY_3)]
    assert orders[1][3] == pytest.approx(held)
    assert env.held().get("DUALB", 0.0) < 1e-6


def test_a_name_untradable_at_a_window_stop_phase_is_a_residue_until_it_trades(
    env: Env, window: PaperWindowRow
) -> None:
    """DUALB untradable at the stop run: its `window_stop` exit is closed with
    event reason `untradable` (no order, no cap count) and reads as a residue,
    so the next stop run makes no exit; once tradable, the stop run on that
    session exits it and the residue falls to zero."""
    bought(env)
    held = env.held()["DUALB"]
    request_stop(env, at(F_0, 22, 0))
    env.fake.set_asset("DUALB", UNTRADABLE)
    first = env.run(at(MAY_2))
    assert first.status == "ok", env.result(env.latest_run())
    ((closed, _reason, _side, _run),) = stop_exits(env, "SEC_DUAL_B")
    assert env.query(
        "SELECT status, reason FROM decision_events WHERE decision_id = ?", [closed]
    ) == [("skipped", "untradable")]
    assert exit_orders(env, "SEC_DUAL_B") == []
    assert env.alerts("skip_cap") == []
    assert env.held()["DUALB"] == pytest.approx(held)

    second = env.run(at(MAY_3))
    assert second.status == "ok", env.result(env.latest_run())
    assert [e[0] for e in stop_exits(env, "SEC_DUAL_B")] == [closed]
    assert exit_orders(env, "SEC_DUAL_B") == []

    env.fake.set_asset("DUALB", TRADABLE, from_session=MAY_6)
    third = env.run(at(MAY_6))
    assert third.status == "ok", env.result(env.latest_run())
    assert [e[3] for e in stop_exits(env, "SEC_DUAL_B")] == [first.run_id, third.run_id]
    ((_coid, decision_id, _phase, quantity, session),) = exit_orders(env, "SEC_DUAL_B")
    assert (decision_id, session) == (stop_exits(env, "SEC_DUAL_B")[-1][0], MAY_6)
    assert quantity == pytest.approx(held)
    assert env.held().get("DUALB", 0.0) < 1e-6


def test_an_untradable_delisted_name_is_closed_by_the_stop_run_and_re_exited_when_it_trades(
    env: Env, window: PaperWindowRow
) -> None:
    """TRNS delisted and untradable: the stop run journals its `delisted` exit
    closed with event reason `untradable` and makes no `window_stop` exit for
    it; once tradable, that session's stop run sells it whole as `delisted`."""
    bought(env)
    held = env.held()["TRNS"]
    ended_before(env, MAY_3)
    request_stop(env, at(MAY_2, 22, 0))
    env.fake.set_asset("TRNS", UNTRADABLE)
    first = env.run(at(MAY_3))
    assert first.status == "ok", env.result(env.latest_run())
    ((closed, reason, _side, run_id),) = exits(env, "SEC_TRANSFER")
    assert (reason, run_id) == ("delisted", first.run_id)
    assert env.query(
        "SELECT status, reason, run_id FROM decision_events WHERE decision_id = ?", [closed]
    ) == [("skipped", "untradable", first.run_id)]
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert len(stop_exits(env, "SEC_DUAL_B")) == 1
    assert env.held().get("DUALB", 0.0) < 1e-6

    second = env.run(at(MAY_6))
    assert second.status == "ok", env.result(env.latest_run())
    assert stop_exits(env, "SEC_TRANSFER") == []
    assert exit_orders(env, "SEC_TRANSFER") == []
    assert env.held()["TRNS"] == pytest.approx(held)

    env.fake.set_asset("TRNS", TRADABLE, from_session=MAY_7)
    third = env.run(at(MAY_7))
    assert third.status == "ok", env.result(env.latest_run())
    assert stop_exits(env, "SEC_TRANSFER") == []
    last = exits(env, "SEC_TRANSFER")[-1]
    assert (last[1], last[3]) == ("delisted", third.run_id)
    ((_coid, decision_id, _phase, quantity, session),) = exit_orders(env, "SEC_TRANSFER")
    assert (decision_id, session, quantity) == (last[0], MAY_7, pytest.approx(held))
    assert env.held().get("TRNS", 0.0) < 1e-6


def test_a_carried_residue_sells_only_the_excess_and_a_listed_residue_gets_no_exit(
    env: Env, window: PaperWindowRow
) -> None:
    """DUALB carries 2 shares from a previous window and the window buys more:
    the stop run sells the holding minus 2. SPY's whole holding is a carried
    residue: no `window_stop` exit is made for it."""
    carry(env, window, "SEC_DUAL_B", "DUALB", 2.0)
    carry(env, window, "SEC_SPY", "SPY", 3.0)
    traded = env.run(at(F_0))
    assert traded.status == "ok", env.result(env.latest_run())
    assert env.held()["SPY"] == pytest.approx(3.0)  # never sold by the plan
    held = env.held()["DUALB"]
    assert held > 2.0
    request_stop(env, at(F_0, 22, 0))
    outcome = env.run(at(MAY_2))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((decision_id, _reason, _side, _run),) = stop_exits(env, "SEC_DUAL_B")
    ((_coid, ordered, _phase, quantity, _session),) = exit_orders(env, "SEC_DUAL_B")
    assert ordered == decision_id
    assert quantity == pytest.approx(held - 2.0)
    assert env.held()["DUALB"] == pytest.approx(2.0)
    assert stop_exits(env, "SEC_SPY") == []
    assert env.held()["SPY"] == pytest.approx(3.0)


# --- outcome horizons ----------------------------------------------------------------------


def test_the_flattening_fill_ends_the_horizon_of_the_window_s_orders(
    env: Env, window: PaperWindowRow
) -> None:
    """The stop run on May 2 flattens every name: the F_0 buys' outcomes end at
    May 2 at the exit's fill price, written by the next run, not at close(T_1)."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    flattened = env.run(at(MAY_2))
    assert flattened.status == "ok", env.result(env.latest_run())
    (exit_coid,) = (o[0] for o in exit_orders(env, "SEC_DUAL_B"))
    ((fill_price,),) = env.query("SELECT price FROM fills WHERE client_order_id = ?", [exit_coid])
    nxt = env.run(at(MAY_3))
    assert nxt.status == "ok", env.result(env.latest_run())
    rows = {r[0]: r for r in outcomes(env, "SEC_DUAL_B")}
    buy = rows[buy_id(env, F_0, "SEC_DUAL_B")]
    assert (buy[1], buy[2]) == (MAY_2, "position_return")
    assert buy[3] == pytest.approx(fill_price)


def test_a_second_stop_run_on_the_flattening_session_leaves_the_horizon_at_the_fill(
    env: Env, window: PaperWindowRow
) -> None:
    """A second stop run on May 2, after the first flattened DUALB that
    session, writes no outcome ending at close(May 1); the May 3 run writes the
    F_0 buy's outcome through May 2 at the exit's fill price."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    first = env.run(at(MAY_2))
    assert first.status == "ok", env.result(env.latest_run())
    (exit_coid,) = (o[0] for o in exit_orders(env, "SEC_DUAL_B"))
    ((fill_price,),) = env.query("SELECT price FROM fills WHERE client_order_id = ?", [exit_coid])
    again = env.run(at(MAY_2, 13, 0))
    assert again.status == "ok", env.result(env.latest_run())
    assert again.kind == "stop"
    assert buy_id(env, F_0, "SEC_DUAL_B") not in {r[0] for r in outcomes(env, "SEC_DUAL_B")}
    nxt = env.run(at(MAY_3))
    assert nxt.status == "ok", env.result(env.latest_run())
    buy = {r[0]: r for r in outcomes(env, "SEC_DUAL_B")}[buy_id(env, F_0, "SEC_DUAL_B")]
    assert (buy[1], buy[2]) == (MAY_2, "position_return")
    assert buy[3] == pytest.approx(fill_price)


def test_close_of_the_next_rebalance_ends_the_horizon_when_the_exit_fills_later(
    env: Env, window: PaperWindowRow
) -> None:
    """A stop requested on T_1 whose exits expire that day: the F_0 buys'
    outcomes end at close(T_1), written by the June 3 stop run before its
    exits fill; that run also misses T_1 with reason `window_stop`."""
    bought(env)
    request_stop(env, at(T_1, 11, 0))
    for security_id in ("SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER"):
        env.fake.script(Expire(), client_order_id=sell_id(env, T_1, security_id))
    on_t1 = env.run(at(T_1))
    assert on_t1.status == "ok", env.result(env.latest_run())
    assert outcomes(env, "SEC_DUAL_B") == []
    june = env.run(at(F_1))
    assert june.status == "ok", env.result(env.latest_run())
    rows = {r[0]: r for r in outcomes(env, "SEC_DUAL_B")}
    assert rows[buy_id(env, F_0, "SEC_DUAL_B")][1:3] == (T_1, "position_return")
    assert (T_1, "missed", "window_stop", june.run_id) in env.rebalance_events()
    assert env.held().get("DUALB", 0.0) < 1e-6


def test_a_not_executed_buy_on_a_name_never_held_is_written_by_the_first_stop_run(
    env: Env, window: PaperWindowRow
) -> None:
    """TRNS's F_0 buy expires unfilled and is written off, so the name is never
    held: the first stop run finds it terminal and flat, and writes its
    `not_executed` outcome through close(S-1), long before close(T_1)."""
    env.fake.script(Expire(), client_order_id=buy_id(env, F_0, "SEC_TRANSFER"))
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert "TRNS" not in env.held()
    request_stop(env, at(F_0, 22, 0))
    assert outcomes(env, "SEC_TRANSFER") == []
    first = env.run(at(MAY_2))
    assert first.status == "ok", env.result(env.latest_run())
    ((_coid, through, kind, mark_price),) = outcomes(env, "SEC_TRANSFER")
    assert (through, kind) == (F_0, "not_executed")
    (close_s_minus_1,) = (
        r[0]
        for r in env.query(
            "SELECT close FROM prices_daily WHERE security_id = ? AND session = ?",
            ["SEC_TRANSFER", F_0],
        )
    )
    (close_s,) = (
        r[0]
        for r in env.query(
            "SELECT close FROM prices_daily WHERE security_id = ? AND session = ?",
            ["SEC_TRANSFER", MAY_2],
        )
    )
    assert close_s_minus_1 != close_s  # a different close(S) is present
    assert mark_price == pytest.approx(close_s_minus_1)  # the mark is close(S-1)
    # DUALB is still held at that run's step 3 (its exit fills at step 7b): no outcome yet.
    assert outcomes(env, "SEC_DUAL_B") == []


# --- a crash -------------------------------------------------------------------------------


def test_a_crash_inside_a_stop_run_leaves_the_derived_switch_engaged(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process dies at the first submit of the stop run: the run row is
    `stop` with no result, its orders `pending`, and the derived switch is
    engaged, so the next run submits nothing and closes it `crashed`."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    submitted = len(env.submits())

    def die(*_args: object, **_kwargs: object) -> None:
        raise Crash

    monkeypatch.setattr(env.fake, "submit", die)
    with pytest.raises(Crash):
        env.run(at(MAY_2))
    crashed = env.latest_run()
    assert run_kind(env, crashed) == "stop"
    assert env.query("SELECT count(*) FROM paper_run_results WHERE run_id = ?", [crashed]) == [(0,)]
    assert (
        env.query(
            "SELECT count(*) FROM orders o WHERE run_id = ? AND NOT EXISTS (SELECT 1 FROM "
            "order_events e WHERE e.client_order_id = o.client_order_id AND e.status <> 'pending')",
            [crashed],
        )[0][0]
        > 0
    )
    assert window.window_id is not None
    with open_read_only(env.settings) as conn:
        runs = runs_for(conn, window.window_id)
        rows = kill_switch_events_for(conn, window.window_id)
    state = switch.derive(
        window,
        rows,
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    assert state.engaged

    monkeypatch.undo()
    nxt = env.run(at(MAY_3))
    assert nxt.status == "skipped_kill_switch"
    assert nxt.kind == "stop"
    assert env.result(crashed)[0] == "crashed"
    assert len(env.submits()) == submitted


# --- the stop step's guards ----------------------------------------------------------------


def test_no_stop_exit_while_fills_lag(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under `fills_lagging` the stop step reads nothing, journals nothing and
    makes no batch: a note on the run."""

    def refused(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("exit book read while fills are lagging")

    monkeypatch.setattr(run_module, "_exit_book", refused)
    context = _lagging_context(env, None)
    assert run_module.stop_step(context) is None
    assert context.notes == ["fills_lagging: no stop exit made"]
    assert env.count("decisions") == 0


def test_an_unset_quantity_precision_fails_the_stop_run_before_any_exit(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    submitted = len(env.submits())
    env.settings = Settings(
        _env_file=None,
        store={"path": env.settings.store.path},
        alpaca={"client_order_id_max_length": 48},
    )
    assert env.settings.alpaca.quantity_decimals is None
    with pytest.raises(ValueError, match="quantity_decimals"):
        env.run(at(MAY_2))
    assert env.result(env.latest_run())[0] == "failed"
    assert env.query("SELECT count(*) FROM decisions WHERE decision = 'forced_exit'") == [(0,)]
    assert len(env.submits()) == submitted


def test_a_held_name_missing_from_the_assets_read_fails_the_stop_run_closed(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker's `assets` answer lacks DUALB: no `window_stop` exit is
    journaled or submitted for any name, the run ends `failed`."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    submitted = len(env.submits())
    answer = env.fake.assets

    def without_dualb(symbols: list[str]) -> dict[str, Asset]:
        return {k: v for k, v in answer(symbols).items() if k != "DUALB"}

    monkeypatch.setattr(env.fake, "assets", without_dualb)
    with pytest.raises(MissingAssetRefused, match="SEC_DUAL_B is missing from the assets read"):
        env.run(at(MAY_2))
    assert env.result(env.latest_run())[0] == "failed"
    assert env.query("SELECT count(*) FROM decisions WHERE decision = 'forced_exit'") == [(0,)]
    assert len(env.submits()) == submitted

"""`window.settle_order`'s broker gate and case (ii) end to end (Phase 4 spec
req 17, #571; plan T84b). The fixtures are `test_window_settle.py`'s."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from execution.test_run_stop import buy_id
from execution.test_run_trade import F_0, FROZEN, Env, at, env
from execution.test_window_settle import (
    NOTE,
    READS,
    Settle,
    _no_env_file,
    s,
    settle_window,
    ticking,
)
from tradepartner.adapters.broker import Order, OrderStatus
from tradepartner.adapters.fake_broker import (
    Expire,
    FakeBroker,
    PartialFill,
    Reject,
    SetPosition,
    Vanish,
)
from tradepartner.execution.resume import RELEASED, resume
from tradepartner.execution.window import (
    ACCOUNT_MISMATCH,
    ALREADY_TERMINAL,
    BROKER_OPEN,
    OTHER_OPEN_ORDER,
    UNEXPLAINED_POSITION,
    UNJOURNALED_FILL,
    settle_order,
)
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import OrderEventRow, append

__all__ = ["_no_env_file", "env", "s"]  # the fixtures, re-exported

MAY_2, MAY_3, MAY_6 = date(2019, 5, 2), date(2019, 5, 3), date(2019, 5, 6)


def calls(s: Settle, mark: int) -> set[str]:
    return {c.method for c in s.fake.calls[mark:]}


# --- (1) no open order -----------------------------------------------------------------


def test_an_order_the_broker_reports_open_is_refused_broker_open(s: Settle) -> None:
    s.place("tp-a")
    s.engage()
    mark = len(s.fake.calls)
    s.refused(BROKER_OPEN, "tp-a")
    assert calls(s, mark) <= READS


class ListsTerminalAsOpen(FakeBroker):
    """`open_orders()` lists every order, terminal ones included."""

    def open_orders(self) -> list[Order]:
        self._log("open_orders")
        return list(self._orders.values())


def test_an_order_open_orders_lists_is_refused_though_get_order_says_terminal(
    s: Settle,
) -> None:
    s.fake = ListsTerminalAsOpen(
        clock=s.clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1"
    )
    s.place("tp-a")
    s.fake.apply("tp-a", Expire())
    s.engage()
    assert s.fake.get_order("tp-a").status is OrderStatus.EXPIRED
    s.refused(BROKER_OPEN, "tp-a")


# --- (2) no fill the journal lacks --------------------------------------------------------


def test_a_fill_of_the_order_the_journal_lacks_is_refused_unjournaled_fill(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", PartialFill(4, 100.0))
    s.fake.apply("tp-a", Expire())
    s.fake.apply_account(SetPosition("SPY", None))  # so only (2) can refuse
    s.engage()
    message = s.refused(UNJOURNALED_FILL, "tp-a")
    assert "fake-fill-1" in message


# --- (3) no unexplained position -------------------------------------------------------------


def test_a_buys_name_held_above_the_ledger_is_refused(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", Expire())
    s.fake.apply_account(SetPosition("SPY", 0.5))
    s.engage()
    s.refused(UNEXPLAINED_POSITION, "tp-a")


def test_a_buys_name_within_the_tolerance_is_accepted(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", Expire())
    s.fake.apply_account(SetPosition("SPY", 5e-7))  # under the default 1e-6
    s.engage()
    assert not s.settle("tp-a").reset


def test_a_buys_name_held_below_the_ledger_is_accepted(s: Settle) -> None:
    """Not the order's doing: left to reconciliation."""
    s.held("tp-held", 10.0)
    s.place("tp-a")
    s.fake.apply("tp-a", Expire())
    s.fake.apply_account(SetPosition("SPY", 4.0))
    s.engage()
    s.settle("tp-a")


def test_a_sells_name_held_below_the_ledger_is_refused(s: Settle) -> None:
    s.held("tp-held", 10.0)
    s.place("tp-sell", side="sell", quantity=6.0)
    s.fake.apply("tp-sell", Expire())
    s.fake.apply_account(SetPosition("SPY", 4.0))
    s.engage()
    s.refused(UNEXPLAINED_POSITION, "tp-sell")


def test_a_sells_name_held_above_the_ledger_is_accepted(s: Settle) -> None:
    s.held("tp-held", 10.0)
    s.place("tp-sell", side="sell", quantity=6.0)
    s.fake.apply("tp-sell", Expire())
    s.fake.apply_account(SetPosition("SPY", 12.0))
    s.engage()
    s.settle("tp-sell")


def test_another_open_order_on_the_name_the_broker_knows_is_refused(s: Settle) -> None:
    s.held("tp-held", 10.0)
    s.place("tp-a")
    s.place("tp-b")
    s.fake.apply("tp-a", Vanish())
    s.engage()
    message = s.refused(OTHER_OPEN_ORDER, "tp-a")
    assert "tp-b" in message


def test_another_open_order_the_broker_forgot_too_does_not_refuse(s: Settle) -> None:
    """Two orders a reset wiped on one name settle one after the other."""
    s.held("tp-held", 10.0)
    s.place("tp-a")
    s.place("tp-b")
    s.fake.apply("tp-a", Vanish())
    s.fake.apply("tp-b", Vanish())
    s.engage()
    first = s.settle("tp-a")
    second = s.settle("tp-b")
    assert not first.reset and not second.reset
    _, event = s.settled_rows("tp-a")
    assert json.loads(event[5])["other_orders"] == [
        {"client_order_id": "tp-b", "get_order": "unknown"}
    ]


def test_another_known_open_order_does_not_refuse_under_the_reset_exception(
    s: Settle,
) -> None:
    s.place("tp-a")
    s.place("tp-b")
    s.fake.apply("tp-a", Vanish())  # tp-b open at the broker, nothing held
    s.engage()
    assert s.settle("tp-a").reset


# --- the account, broker errors and the write's re-check ------------------------------------


def test_another_account_is_refused_account_mismatch(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", Vanish())
    s.engage()
    other = FakeBroker(clock=s.clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA2")
    s.refused(ACCOUNT_MISMATCH, "tp-a", broker=other)
    assert [c.method for c in other.calls] == ["account"]


class FailingGetOrder(FakeBroker):
    def get_order(self, client_order_id: str) -> Order:
        self._log("get_order", client_order_id)
        raise ConnectionError("transport timeout")


def test_a_get_order_transport_error_propagates_with_nothing_written(s: Settle) -> None:
    s.place("tp-a")
    s.engage()
    broken = FailingGetOrder(
        clock=s.clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1"
    )
    before = s.counts()
    with pytest.raises(ConnectionError):
        s.settle("tp-a", broker=broken)
    assert s.counts() == before


def test_a_terminal_event_written_after_the_gate_aborts_the_write(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", Vanish())
    s.engage()
    settings, clock = s.settings, s.clock

    class LateTerminal(FakeBroker):
        def positions(self) -> Any:
            stamp = clock()
            with open_for_write(settings) as conn:
                append(
                    conn,
                    OrderEventRow(
                        client_order_id="tp-a",
                        status="expired",
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
            return super().positions()

    late = LateTerminal(clock=clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1")
    overrides = s.count("overrides")
    with pytest.raises(Exception) as raised:
        s.settle("tp-a", broker=late)
    assert getattr(raised.value, "reason", None) == ALREADY_TERMINAL
    assert s.count("overrides") == overrides
    assert s.query(
        "SELECT count(*) FROM order_events WHERE client_order_id = 'tp-a' AND status = 'cancelled'"
    ) == [(0,)]


# --- case (ii) end to end --------------------------------------------------------------------


def lagging_window(env: Env, tmp_path: Path, *, hold: bool) -> tuple[str, str]:
    """F_0 buys three names: SPFT's buy fills at the broker but its fill never
    reaches the stream (and, unless `hold`, the broker's book does not show
    it either); TRNS's stays open. MAY_2 lists the lag; TRNS is rejected
    after MAY_3's run halts on the lag bound, so its `rejected` event is
    journaled by `paper resume`'s collection, after the last run cursor."""
    settle_window(env, tmp_path)
    with open_for_write(env.settings) as conn:
        frozen = json.loads(
            conn.execute(
                "SELECT frozen_json FROM paper_windows WHERE window_id = ?",
                [env.window.window_id],  # type: ignore[union-attr]
            ).fetchone()[0]  # type: ignore[index]
        )
        frozen["risk.max_rejections_per_run"] = 0
        frozen["risk.max_fill_lag_sessions"] = 2
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [json.dumps(frozen, sort_keys=True), env.window.window_id],  # type: ignore[union-attr]
        )
    assert FROZEN.max_fill_lag_sessions == 1  # the test overrides it to 2 above
    spft, trns = buy_id(env, F_0, "SEC_SPLIT_FUTURE"), buy_id(env, F_0, "SEC_TRANSFER")
    env.fill_on_sleep = False

    def fill(now: datetime) -> None:
        for order in env.fake.open_orders():
            if order.client_order_id == trns:
                continue
            env.fake.lag_fills(None if order.client_order_id == spft else 0)
            env.fake.simulate_fill(order.client_order_id)
            env.fake.lag_fills(0)

    env.on_sleep.append(fill)
    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    if not hold:
        # The fill does not exist: neither the position nor the cash it spent
        # (the positions hook leaves cash alone, so the test puts it back).
        reading = env.fake.get_order(spft)
        assert reading.filled_quantity is not None and reading.filled_avg_price is not None
        env.fake.apply_account(SetPosition("SPFT", None))
        env.fake._cash += Decimal(repr(reading.filled_quantity)) * Decimal(
            repr(reading.filled_avg_price)
        )
    lagging = env.run(at(MAY_2))
    assert lagging.status == "ok", env.result(env.latest_run())
    with pytest.raises(Exception, match=r"ReconciliationError|fills_lagging"):
        env.run(at(MAY_3))
    assert env.result(env.latest_run())[:2] == ("halted", "ReconciliationError")
    env.fake.apply(trns, Reject())
    return spft, trns


def resumed(env: Env, *, accept_broker_fills: bool, accept_rejections: bool) -> Any:
    return resume(
        env.settings,
        env.connect,
        env.fake,
        ticking(env),
        "the owner resumes after the lag",
        accept_broker_fills,
        accept_rejections=accept_rejections,
    )


def synthetic(env: Env, coid: str) -> int:
    return int(
        env.query(
            "SELECT count(*) FROM fills WHERE client_order_id = ? AND source = 'broker_status'",
            [coid],
        )[0][0]
    )


def test_case_ii_a_lagging_fill_the_book_does_not_show_is_settled(env: Env, tmp_path: Path) -> None:
    spft, trns = lagging_window(env, tmp_path, hold=False)

    for _ in range(2):  # refused on the verdict, twice, with no synthetic fill
        refused = resumed(env, accept_broker_fills=True, accept_rejections=True)
        assert refused.status != RELEASED
        assert synthetic(env, spft) == 0

    result = settle_order(env.settings, env.connect, env.fake, ticking(env), spft, NOTE)
    assert not result.reset
    (raw,) = env.query(
        "SELECT raw_json FROM order_events WHERE client_order_id = ? AND status = 'cancelled'",
        [spft],
    )[0]
    reading = json.loads(raw)["get_order"]
    assert reading["status"] == "filled" and reading["filled_quantity"] > 0

    with pytest.raises(Exception, match=r"RejectionCap|rejection"):
        env.run(at(MAY_6))  # judges TRNS's rejection and commits its cursor
    assert env.result(env.latest_run())[:2] == ("halted", "RejectionCapError")

    released = resumed(env, accept_broker_fills=False, accept_rejections=True)
    assert released.status == RELEASED, released.reasons
    assert not any(spft in r or trns in r for r in released.reasons)
    assert synthetic(env, spft) == 0


def test_case_ii_with_the_book_holding_the_fill_is_refused_and_resume_completes_it(
    env: Env, tmp_path: Path
) -> None:
    spft, _trns = lagging_window(env, tmp_path, hold=True)
    with pytest.raises(Exception) as raised:
        settle_order(env.settings, env.connect, env.fake, ticking(env), spft, NOTE)
    assert getattr(raised.value, "reason", None) == UNEXPLAINED_POSITION

    with pytest.raises(Exception, match=r"RejectionCap|rejection"):
        env.run(at(MAY_6))
    released = resumed(env, accept_broker_fills=True, accept_rejections=True)
    assert released.status == RELEASED, released.reasons
    assert synthetic(env, spft) == 1

"""`window.settle_order`, the owner settlement writer (Phase 4 spec req 17,
#571; plan T84b).

This file holds the shared fixtures (`Settle`: the store, the window, the
journal rows and the recording scripted fake), the journal refusals of req
17's gate in order (each with nothing written and no broker call), what a
settlement writes, the reset exception, and case (i) end to end through
`paper run`, `paper resume`, `paper settle` and `paper abandon`.
`test_window_settle_gate.py` holds the broker gate's refusals and case (ii).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from execution.test_run_stop import buy_id
from execution.test_run_trade import (
    F_0,
    Env,
    at,
    env,
    frozen_json,
)
from execution.test_run_trade import (
    FROZEN as TRADE_FROZEN,
)
from tradepartner.adapters.broker import OrderRequest, Side, UnknownOrderError
from tradepartner.adapters.fake_broker import Expire, FakeBroker, Reset, SetPosition, Vanish
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import switch
from tradepartner.execution.collect import collect
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.resume import resume
from tradepartner.execution.window import (
    ABANDONED,
    ALREADY_TERMINAL,
    NO_WINDOW,
    NOT_ENGAGED,
    OPEN_ORDERS,
    OWNER_SETTLED_UNKNOWN,
    PENDING_ORDER,
    REASON,
    UNEXPLAINED_POSITION,
    UNKNOWN_ORDER,
    SettleResult,
    WindowCommandRefused,
    abandon,
    settle_order,
)
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    append,
)

__all__ = ["env"]  # the fixture, re-exported for the end-to-end case

FROZEN = RiskConfig()
MIN_REASON = 20
PRICE = 100.0
SPY = "SEC_SPY"  # a fixture name listed through 2026
DAY1 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # the conftest clock: S = 2026-10-01
REBALANCE = date(2026, 9, 30)
NOTE = "the broker reset the paper account and forgot the order"
#: Every table a settlement must leave alone.
UNTOUCHED = (
    "fills",
    "reconciliations",
    "kill_switch",
    "paper_window_stops",
    "decision_events",
    "decisions",
    "orders",
)
#: The broker methods the gate may call.
READS = {"account", "get_order", "open_orders", "fills", "positions"}


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`test_run_trade.py`'s autouse fixture, repeated: importing its `env`
    does not import its autouse ones."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


def settle_frozen_json(frozen: RiskConfig = FROZEN) -> str:
    values: dict[str, Any] = {f"risk.{k}": v for k, v in frozen.model_dump(mode="json").items()}
    values["paper.min_override_reason_chars"] = MIN_REASON
    return json.dumps(values, sort_keys=True)


class Ticking:
    """The fake's clock moved by a microsecond on every reading, so the
    settlement's stamp is later than its gate's reading (a real clock does
    this on its own). `readings` records each one."""

    def __init__(self, base: Any) -> None:
        self.base = base
        self.readings: list[datetime] = []

    def __call__(self) -> datetime:
        self.base.now += timedelta(microseconds=1)
        self.readings.append(self.base.now)
        return self.base.now


@dataclass
class Settle:
    """The store, the clock, the recording fake and the open window."""

    settings: Settings
    clock: Any
    fake: FakeBroker
    window: PaperWindowRow

    @property
    def window_id(self) -> int:
        assert self.window.window_id is not None
        return self.window.window_id

    def connect(self) -> Any:
        return open_for_write(self.settings)

    def append(self, *rows: object) -> list[int | None]:
        with open_for_write(self.settings) as conn:
            return [append(conn, row) for row in rows]  # type: ignore[arg-type]

    def query(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(sql, params or []).fetchall()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT count(*) FROM {table}")[0][0])

    def counts(self) -> dict[str, int]:
        return {t: self.count(t) for t in ("overrides", "order_events", *UNTOUCHED)}

    def run_id(self) -> int:
        at_ = DAY1 - timedelta(hours=2)
        (run_id,) = self.append(
            PaperRunRow(
                window_id=self.window_id,
                session=at_.date(),
                kind="rebalance",
                started_at=at_,
                invoked_by="scheduler",
                code_version="test",
                known_at=at_,
                ingested_at=at_,
            )
        )
        assert run_id is not None
        self.append(
            PaperRunResultRow(
                run_id=run_id,
                finished_at=at_,
                status="ok",
                clock_fault=False,
                known_at=at_,
                ingested_at=at_,
            )
        )
        return run_id

    def place(
        self,
        coid: str,
        side: str = "buy",
        quantity: float = 10.0,
        *,
        acknowledged: bool = True,
        security_id: str = SPY,
        symbol: str = "SPY",
        forced: bool = False,
    ) -> OrderRow:
        """A decision, its order, the `pending` event and, when
        `acknowledged`, the fake's submit (it stays `ACCEPTED`) and the
        `accepted` event with the broker's id."""
        run_id = self.run_id()
        at_ = DAY1 - timedelta(hours=1)
        (decision_id,) = self.append(
            DecisionRow(
                run_id=run_id,
                rebalance_session=None if forced else REBALANCE,
                security_id=security_id,
                side=side,
                planned_quantity=quantity,
                whole_share=False,
                decision="forced_exit" if forced else "trade",
                reason="delisted" if forced else None,
                known_at=at_,
                ingested_at=at_,
            )
        )
        assert decision_id is not None
        order = OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=at_.date(),
            attempt=1,
            phase=side,
            security_id=security_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            sells_in_flight_at_submit=False,
            known_at=at_,
            ingested_at=at_,
        )
        self.append(
            order,
            OrderEventRow(client_order_id=coid, status="pending", known_at=at_, ingested_at=at_),
        )
        if acknowledged:
            placed = self.fake.submit(OrderRequest(coid, symbol, Side(side), quantity=quantity))
            self.append(
                OrderEventRow(
                    client_order_id=coid,
                    status="accepted",
                    broker_order_id=placed.broker_order_id,
                    known_at=at_,
                    ingested_at=at_,
                )
            )
        return order

    def collect(self, *orders: OrderRow) -> None:
        """The journal collects `orders` from the fake, as a run does."""
        collect(self.fake, self.connect, list(orders), self.clock, "run", 1, FROZEN, self.settings)

    def held(self, coid: str = "tp-held", quantity: float = 10.0) -> OrderRow:
        """A filled, collected buy: the ledger and the fake hold `quantity`."""
        order = self.place(coid, quantity=quantity)
        self.fake.simulate_fill(coid)
        self.collect(order)
        return order

    def engage(self) -> None:
        engaged = switch.engage(
            self.settings, self.clock, window_id=self.window_id, source="owner", reason="test"
        )
        assert isinstance(engaged, int)

    def settle(self, coid: str, reason: str = NOTE, broker: Any = None) -> SettleResult:
        return settle_order(
            self.settings,
            self.connect,
            self.fake if broker is None else broker,
            Ticking(self.clock),
            coid,
            reason,
        )

    def refused(self, code: str, coid: str, *, broker: Any = None, reason: str = NOTE) -> str:
        """`settle` refused with `code`, nothing written; its message."""
        before = self.counts()
        with pytest.raises(WindowCommandRefused) as raised:
            self.settle(coid, reason, broker)
        assert raised.value.reason == code, str(raised.value)
        assert self.counts() == before
        return str(raised.value)

    def calls_since(self, mark: int) -> list[str]:
        return [c.method for c in self.fake.calls[mark:]]

    def settled_rows(self, coid: str) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        (override,) = self.query(
            "SELECT override_id, kind, client_order_id, security_id, rebalance_session, reason, "
            "known_at, ingested_at FROM overrides WHERE client_order_id = ?",
            [coid],
        )
        (event,) = self.query(
            "SELECT status, reason, broker_order_id, filled_quantity, filled_avg_price, "
            "raw_json, known_at, ingested_at FROM order_events "
            "WHERE client_order_id = ? AND reason = ?",
            [coid, OWNER_SETTLED_UNKNOWN],
        )
        return override, event


def new_window(settings: Settings, frozen: str | None = None) -> PaperWindowRow:
    started = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=REBALANCE,
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=frozen or settle_frozen_json(),
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )
    with open_for_write(settings) as conn:
        window_id = append(conn, row)
    return replace(row, window_id=window_id)


@pytest.fixture
def s(journal_settings: Settings, fixed_clock: Any) -> Settle:
    fake = FakeBroker(
        clock=fixed_clock, price_of=lambda _s: PRICE, auto_fill=False, account_id="PA1"
    )
    return Settle(journal_settings, fixed_clock, fake, new_window(journal_settings))


# --- the journal refusals, in req 17's order ------------------------------------------


def test_no_window_is_refused_with_no_broker_call(s: Settle) -> None:
    s.place("tp-a")
    s.engage()
    s.append(
        PaperWindowStopRow(
            window_id=s.window_id,
            at=DAY1,
            state=ABANDONED,
            reason="closed for the test",
            known_at=DAY1,
            ingested_at=DAY1,
        )
    )
    mark = len(s.fake.calls)
    s.refused(NO_WINDOW, "tp-a")
    assert s.calls_since(mark) == []


@pytest.mark.parametrize("coid", ["tp-elsewhere", "tp-none"])
def test_an_order_not_of_the_open_window_is_refused_unknown_order(s: Settle, coid: str) -> None:
    """An id of a closed window, or of no order at all."""
    s.place("tp-elsewhere")
    s.append(
        PaperWindowStopRow(
            window_id=s.window_id,
            at=DAY1 - timedelta(minutes=30),
            state=ABANDONED,
            reason="closed for the test",
            known_at=DAY1 - timedelta(minutes=30),
            ingested_at=DAY1 - timedelta(minutes=30),
        )
    )
    s.window = new_window(s.settings)
    s.engage()
    mark = len(s.fake.calls)
    s.refused(UNKNOWN_ORDER, coid)
    assert s.calls_since(mark) == []


def test_an_order_with_a_terminal_event_is_refused_already_terminal(s: Settle) -> None:
    s.held("tp-a")
    s.engage()
    mark = len(s.fake.calls)
    s.refused(ALREADY_TERMINAL, "tp-a")
    assert s.calls_since(mark) == []


def test_a_pending_order_is_refused_pending_order(s: Settle) -> None:
    """Req 4's settlement belongs to `paper resume`."""
    s.place("tp-a", acknowledged=False)
    s.engage()
    s.refused(PENDING_ORDER, "tp-a")
    assert s.fake.calls == ()


@pytest.mark.parametrize("reason", ["   ", "x" * (MIN_REASON - 1) + "   "])
def test_a_blank_or_short_reason_is_refused(s: Settle, reason: str) -> None:
    s.place("tp-a")
    s.engage()
    mark = len(s.fake.calls)
    s.refused(REASON, "tp-a", reason=reason)
    assert s.calls_since(mark) == []


def test_a_window_whose_switch_is_not_engaged_is_refused_not_engaged(s: Settle) -> None:
    s.place("tp-a")
    mark = len(s.fake.calls)
    s.refused(NOT_ENGAGED, "tp-a")
    assert s.calls_since(mark) == []


def test_the_journal_refusals_come_in_req_17s_order(s: Settle) -> None:
    """A pending order with a blank reason on a switch not engaged is
    refused `pending_order`; an acknowledged one `reason` before
    `not_engaged`."""
    s.place("tp-p", acknowledged=False)
    s.place("tp-a")
    s.refused(PENDING_ORDER, "tp-p", reason=" ")
    s.refused(REASON, "tp-a", reason=" ")


def test_settle_is_refused_while_another_process_holds_the_run_lock(s: Settle) -> None:
    s.place("tp-a")
    s.engage()
    s.fake.apply("tp-a", Expire())
    mark = len(s.fake.calls)
    before = s.counts()
    with run_lock(s.settings), pytest.raises(LockHeld):
        s.settle("tp-a")
    assert s.counts() == before
    assert s.calls_since(mark) == []


# --- what it writes ---------------------------------------------------------------------


def test_a_settlement_writes_the_two_rows_on_one_stamp_after_the_reads(s: Settle) -> None:
    """Case (i) on the unit store: the broker forgot the order (`Vanish`)
    and holds nothing; exactly one `overrides` row and one `order_events`
    row, stamped alike and later than the gate's reading, and nothing else;
    the fake sees only the five reads."""
    order = s.place("tp-a")
    s.fake.apply("tp-a", Vanish())
    s.engage()
    before = s.counts()
    mark = len(s.fake.calls)
    clock = Ticking(s.clock)

    result = settle_order(s.settings, s.connect, s.fake, clock, "tp-a", f"  {NOTE}  ")

    assert set(s.calls_since(mark)) == READS
    assert [c.method for c in s.fake.calls[mark:]] == [
        "account",
        "get_order",
        "open_orders",
        "fills",
        "positions",
    ]
    override, event = s.settled_rows("tp-a")
    override_id, kind, coid, security_id, session, reason, o_known, o_ingested = override
    assert (kind, coid, security_id, session, reason) == (
        "settle_order",
        "tp-a",
        SPY,
        REBALANCE,
        NOTE,
    )
    status, why, broker_order_id, filled_q, filled_p, raw, e_known, e_ingested = event
    accepted = s.query(
        "SELECT broker_order_id FROM order_events WHERE client_order_id = 'tp-a' "
        "AND status = 'accepted'"
    )[0][0]
    assert (status, why, broker_order_id, filled_q, filled_p) == (
        "cancelled",
        OWNER_SETTLED_UNKNOWN,
        accepted,
        None,
        None,
    )
    assert o_known == o_ingested == e_known == e_ingested == result.known_at
    assert result.known_at > clock.readings[0]  # later than the gate's reading
    assert result.known_at == clock.readings[-1]
    evidence = json.loads(raw)
    assert evidence["override_id"] == override_id == result.override_id
    assert evidence["get_order"] == "unknown"
    assert evidence["account_id"] == "PA1"
    assert evidence["open_order_ids"] == []
    assert evidence["fill_ids"] == []
    assert (evidence["broker_quantity"], evidence["ledger_quantity"]) == (0.0, 0.0)
    assert result.reset and evidence["reset"]
    after = s.counts()
    assert after["overrides"] == before["overrides"] + 1
    assert after["order_events"] == before["order_events"] + 1
    assert {t: after[t] for t in UNTOUCHED} == {t: before[t] for t in UNTOUCHED}
    assert order.client_order_id == result.client_order_id


def test_a_forced_exits_settlement_carries_no_rebalance_session(s: Settle) -> None:
    s.held("tp-held")
    s.place("tp-exit", side="sell", forced=True)
    s.fake.apply("tp-exit", Expire())
    s.engage()
    s.settle("tp-exit")
    override, _ = s.settled_rows("tp-exit")
    assert override[4] is None


def test_a_second_settlement_of_the_order_is_refused_already_terminal(s: Settle) -> None:
    s.place("tp-a")
    s.fake.apply("tp-a", Vanish())
    s.engage()
    s.settle("tp-a")
    s.refused(ALREADY_TERMINAL, "tp-a")


def test_a_settlement_never_calls_submit_or_cancel(s: Settle) -> None:
    s.held("tp-held")
    s.place("tp-a")
    s.place("tp-b")
    s.fake.apply("tp-a", Expire())
    s.fake.apply("tp-b", Vanish())
    s.engage()
    mark = len(s.fake.calls)
    s.settle("tp-a")
    assert set(s.calls_since(mark)) <= READS


# --- the reset exception ----------------------------------------------------------------


def test_a_sell_the_ledger_holds_settles_on_an_empty_book(s: Settle) -> None:
    """The reset exception: `get_order` unknown and no position in any name,
    so the ledger's 10 shares against the broker's none do not refuse."""
    s.held("tp-held")
    s.place("tp-sell", side="sell")
    s.fake.apply_account(Reset())
    s.engage()
    result = s.settle("tp-sell")
    assert result.reset
    _, event = s.settled_rows("tp-sell")
    evidence = json.loads(event[5])
    assert (evidence["broker_quantity"], evidence["ledger_quantity"]) == (0.0, 10.0)


def test_the_reset_exception_does_not_apply_while_another_name_is_held(s: Settle) -> None:
    s.held("tp-held")
    s.place("tp-sell", side="sell")
    s.fake.apply_account(Reset())
    s.fake.apply_account(SetPosition("QQQ", 1.0))
    s.engage()
    s.refused(UNEXPLAINED_POSITION, "tp-sell")


def test_the_reset_exception_needs_get_order_to_raise(s: Settle) -> None:
    """A terminal reading with an empty book is no reset: the sell's
    missing shares refuse."""
    s.held("tp-held")
    s.place("tp-sell", side="sell")
    s.fake.apply("tp-sell", Expire())
    s.fake.apply_account(SetPosition("SPY", None))
    s.engage()
    s.refused(UNEXPLAINED_POSITION, "tp-sell")


# --- case (i) end to end -------------------------------------------------------------------


def settle_window(env: Env, tmp_path: Path) -> PaperWindowRow:
    """`test_run_trade.py`'s window, with the frozen minimum reason length
    `paper start` freezes."""
    window = env.open_window(tmp_path=tmp_path)
    frozen = json.loads(frozen_json(TRADE_FROZEN))
    frozen["paper.min_override_reason_chars"] = MIN_REASON
    with open_for_write(env.settings) as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [json.dumps(frozen, sort_keys=True), window.window_id],
        )
    env.window = replace(window, frozen_json=json.dumps(frozen, sort_keys=True))
    return env.window


def ticking(env: Env) -> Ticking:
    return Ticking(env.clock)


def test_case_i_a_reset_order_is_settled_and_the_window_abandoned(env: Env, tmp_path: Path) -> None:
    """TRNS's F_0 buy is still open when the paper account is reset: the next
    run halts at its collection, `paper resume` raises the same way and
    `paper abandon` is refused `open_orders`; `paper settle` journals it
    terminal, a second settle is refused `already_terminal`, and `paper
    abandon` then lists the reset's differences."""
    settle_window(env, tmp_path)
    trns = buy_id(env, F_0, "SEC_TRANSFER")
    env.fill_on_sleep = False

    def fill_but_trns(now: datetime) -> None:
        for order in env.fake.open_orders():
            if order.client_order_id != trns:
                env.fake.simulate_fill(order.client_order_id)

    env.on_sleep.append(fill_but_trns)
    first = env.run(at(F_0))
    assert first.status == "ok", env.result(env.latest_run())
    assert [o.client_order_id for o in env.fake.open_orders()] == [trns]
    env.fake.apply_account(Reset())

    with pytest.raises(UnknownOrderError):
        env.run(at(date(2019, 5, 2)))
    status, fault, _ = env.result(env.latest_run())
    assert (status, fault) == ("halted", "UnknownOrderError")

    with pytest.raises(UnknownOrderError):
        resume(
            env.settings,
            env.connect,
            env.fake,
            ticking(env),
            "the owner tries a resume",
            False,
            accept_rejections=False,
        )
    with pytest.raises(WindowCommandRefused) as refused:
        abandon(env.settings, env.connect, env.fake, ticking(env), "the owner gives up")
    assert refused.value.reason == OPEN_ORDERS

    tables = ("fills", "reconciliations", "kill_switch", "decision_events", "paper_window_stops")
    before = {t: env.count(t) for t in tables}
    result = settle_order(env.settings, env.connect, env.fake, ticking(env), trns, NOTE)
    assert result.reset
    assert {t: env.count(t) for t in tables} == before
    (override,) = env.query(
        "SELECT kind, security_id, rebalance_session, reason FROM overrides "
        "WHERE client_order_id = ?",
        [trns],
    )
    assert override == ("settle_order", "SEC_TRANSFER", date(2019, 4, 30), NOTE)
    (event,) = env.query(
        "SELECT status, reason, filled_quantity, raw_json FROM order_events "
        "WHERE client_order_id = ? AND status = 'cancelled'",
        [trns],
    )
    assert event[:3] == ("cancelled", OWNER_SETTLED_UNKNOWN, None)
    assert json.loads(event[3])["get_order"] == "unknown"

    with pytest.raises(WindowCommandRefused) as again:
        settle_order(env.settings, env.connect, env.fake, ticking(env), trns, NOTE)
    assert again.value.reason == ALREADY_TERMINAL

    ended = abandon(
        env.settings, env.connect, env.fake, ticking(env), "the paper account was reset"
    )
    listed = json.loads(ended.residues_json)
    assert listed["positions"] == {}
    assert listed["mismatches"]  # the ledger's DUALB and SPFT the reset wiped
    (state,) = env.query("SELECT state FROM paper_window_stops")
    assert state == (ABANDONED,)

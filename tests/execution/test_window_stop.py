"""Window stop, abandon, kill and the override writer (Phase 4 plan T64b; spec
req 14 "Entry gate, start and stop", req 5's owner engagement, req 9's
override writer, open question 13's `paper abandon`)."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import polars as pl
import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import switch
from tradepartner.execution import window as window_module
from tradepartner.execution.collect import collect
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile_run import reconcile_now
from tradepartner.execution.resume import resume
from tradepartner.execution.window import (
    KILL_SWITCH,
    MULTIPLE_OPEN_WINDOWS,
    NO_WINDOW,
    NOT_FLAT,
    NOT_READY,
    OPEN_ORDERS,
    OVERRIDE,
    REASON,
    RECONCILIATION,
    KillWriteFailed,
    WindowCommandRefused,
    _check_flat,
    _not_ready,
    _parse_residues,
    abandon,
    kill,
    override,
    stop,
)
from tradepartner.store import schema
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    PositionDailyRow,
    append,
    kill_switch_events_for,
    open_window,
    order_events_for,
    overrides_for,
    reconciliations_for,
    runs_for,
    unconsumed_kill_switch_overrides,
    window_stops_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
MIN_REASON = 20
PRICE = 100.0
SPY = "SEC_SPY"  # a fixture name listed through 2026
DAY1 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # the conftest clock: S = 2026-10-01
NOTE = "the owner stops the window after the check"
OVERRIDE_REASON = "the owner excludes this name for a known reason"


@pytest.fixture
def fake(fixed_clock: FixedClock) -> FakeBroker:
    return FakeBroker(
        clock=fixed_clock, price_of=lambda _s: PRICE, auto_fill=False, account_id="PA1"
    )


def _frozen_json() -> str:
    frozen: dict[str, Any] = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
    frozen["paper.min_override_reason_chars"] = MIN_REASON
    return json.dumps(frozen, sort_keys=True)


def _new_window(
    settings: Settings, started: datetime, *, starting_cash: float = 100_000.0
) -> PaperWindowRow:
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=starting_cash,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=_frozen_json(),
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )
    (window_id,) = _append(settings, row)
    return replace(row, window_id=window_id)


@pytest.fixture
def window(journal_settings: Settings) -> PaperWindowRow:
    """An open window with its frozen values as `paper start` writes them."""
    return _new_window(journal_settings, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))


# --- helpers ------------------------------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _connect(settings: Settings) -> Any:
    return lambda: open_for_write(settings)


def _run(
    settings: Settings, window: PaperWindowRow, at: datetime, *, status: str | None = "ok"
) -> int:
    """A run of the window; `status=None` leaves it without a result row."""
    (run_id,) = _append(
        settings,
        PaperRunRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=at.date(),
            kind="stop",
            started_at=at,
            invoked_by="scheduler",
            code_version="test",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert run_id is not None
    if status is not None:
        _append(
            settings,
            PaperRunResultRow(
                run_id=run_id,
                finished_at=at,
                status=status,
                clock_fault=False,
                known_at=at,
                ingested_at=at,
            ),
        )
    return run_id


def _buy(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    run_id: int,
    coid: str,
    quantity: float,
    *,
    fill: bool = True,
    side: str = "buy",
) -> OrderRow:
    """A buy's decision, order, `pending` and `accepted` events, the fake's
    submit and, with `fill`, its fill collected into the journal."""
    at = DAY1 - timedelta(hours=1)
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 30),
            security_id=SPY,
            side=side,
            planned_quantity=quantity,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    order = OrderRow(
        client_order_id=coid,
        decision_id=decision_id,
        run_id=run_id,
        session=at.date(),
        attempt=1,
        phase=side,
        security_id=SPY,
        symbol="SPY",
        side=side,
        quantity=quantity,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    placed = fake.submit(OrderRequest(coid, "SPY", Side(side), quantity=quantity))
    _append(
        settings,
        order,
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=placed.broker_order_id,
            known_at=at,
            ingested_at=at,
        ),
    )
    if fill:
        fake.simulate_fill(coid)
        _collect(settings, fake, clock, [order])
    return order


def _collect(
    settings: Settings, fake: FakeBroker, clock: FixedClock, orders: list[OrderRow]
) -> Any:
    return collect(fake, _connect(settings), orders, clock, "run", 1, FROZEN, settings)


def _outcome(settings: Settings, coid: str, kind: str) -> None:
    _append(
        settings,
        OutcomeRow(
            client_order_id=coid,
            through_session=date(2026, 9, 30),
            kind=kind,
            value=0.0,
            known_at=DAY1,
            ingested_at=DAY1,
        ),
    )


def _forced_exit_skipped(settings: Settings, run_id: int, reason: str) -> None:
    """A `window_stop` forced exit of SPY closed by a `skipped` event."""
    at = DAY1 - timedelta(minutes=30)
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            security_id=SPY,
            side="sell",
            whole_share=False,
            decision="forced_exit",
            reason="window_stop",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    _append(
        settings,
        DecisionEventRow(
            decision_id=decision_id,
            run_id=run_id,
            status="skipped",
            reason=reason,
            known_at=at,
            ingested_at=at,
        ),
    )


def _requested(settings: Settings, window: PaperWindowRow, at: datetime) -> None:
    _append(
        settings,
        PaperWindowStopRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            at=at,
            state="requested",
            reason=NOTE,
            known_at=at,
            ingested_at=at,
        ),
    )


def _stop(settings: Settings, fake: FakeBroker, clock: FixedClock, reason: str = NOTE) -> Any:
    return stop(settings, _connect(settings), fake, clock, reason)


def _stops(settings: Settings, window: PaperWindowRow) -> list[Any]:
    with open_read_only(settings) as conn:
        return window_stops_for(conn, window.window_id)  # type: ignore[arg-type]


def _engaged(settings: Settings, window: PaperWindowRow) -> bool:
    with open_read_only(settings) as conn:
        runs = runs_for(conn, window.window_id)  # type: ignore[arg-type]
        state = switch.derive(
            window,
            kill_switch_events_for(conn, window.window_id),  # type: ignore[arg-type]
            [r.run for r in runs],
            [r.result for r in runs if r.result is not None],
            reading_run=None,
            lock_free=True,
        )
    return state.engaged


def _count(settings: Settings, table: str) -> int:
    with open_read_only(settings) as conn:
        (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
    return int(n)


def _refusal(excinfo: pytest.ExceptionInfo[WindowCommandRefused]) -> tuple[str, str]:
    return excinfo.value.reason, str(excinfo.value)


# --- the stop state machine -----------------------------------------------------


def test_a_first_stop_requests_and_a_second_closes_a_flat_window(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    first = _stop(journal_settings, fake, fixed_clock, f"  {NOTE}  ")

    assert first.state == "requested"
    (requested,) = _stops(journal_settings, window)
    assert (requested.state, requested.reason) == ("requested", NOTE)
    assert fake.calls == ()  # the request reads nothing at the broker

    fixed_clock.advance(minutes=1)
    second = _stop(journal_settings, fake, fixed_clock)

    assert second.state == "closed"
    _, closed = _stops(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (reconciliation,) = reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]
        assert open_window(conn) is None
    assert closed.state == "closed"
    assert closed.reconciliation_id == reconciliation.reconciliation_id == second.reconciliation_id
    assert reconciliation.status == "ok"
    assert closed.at >= reconciliation.at
    assert json.loads(closed.residues_json) == {}


def test_stop_and_abandon_are_refused_at_once_while_a_run_holds_the_lock(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    with run_lock(journal_settings):
        with pytest.raises(LockHeld):
            _stop(journal_settings, fake, fixed_clock)
        with pytest.raises(LockHeld):
            abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    assert _stops(journal_settings, window) == []
    assert fake.calls == ()


@pytest.mark.parametrize("command", ["stop", "abandon", "kill"])
def test_a_blank_reason_is_refused_before_any_broker_call_or_write(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    command: str,
) -> None:
    connect = _connect(journal_settings)
    with pytest.raises(WindowCommandRefused) as excinfo:
        if command == "stop":
            stop(journal_settings, connect, fake, fixed_clock, "   ")
        elif command == "abandon":
            abandon(journal_settings, connect, fake, fixed_clock, "")
        else:
            kill(journal_settings, connect, fixed_clock, " \t")

    assert excinfo.value.reason == REASON
    assert fake.calls == ()
    assert _stops(journal_settings, window) == []
    assert _count(journal_settings, "kill_switch") == 0


def test_every_command_is_refused_with_no_window_and_writes_nothing(
    journal_settings: Settings, fake: FakeBroker, fixed_clock: FixedClock
) -> None:
    connect = _connect(journal_settings)
    calls = [
        lambda: stop(journal_settings, connect, fake, fixed_clock, NOTE),
        lambda: abandon(journal_settings, connect, fake, fixed_clock, NOTE),
        lambda: kill(journal_settings, connect, fixed_clock, NOTE),
        lambda: override(
            journal_settings, fixed_clock, "engage_kill_switch", None, None, OVERRIDE_REASON
        ),
    ]
    for call in calls:
        with pytest.raises(WindowCommandRefused) as excinfo:
            call()
        assert excinfo.value.reason == NO_WINDOW

    assert fake.calls == ()
    for table in ("paper_window_stops", "kill_switch", "overrides", "reconciliations"):
        assert _count(journal_settings, table) == 0


def test_stop_refuses_more_than_one_open_window(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Only one window may be open (spec req 14); `stop` must refuse the same
    way `kill` does (#540), before any request is written."""
    _new_window(journal_settings, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == MULTIPLE_OPEN_WINDOWS
    assert "more than one open paper window" in message
    assert fake.calls == ()
    assert _stops(journal_settings, window) == []


def test_stop_is_refused_while_the_switch_is_engaged(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    kill(journal_settings, _connect(journal_settings), fixed_clock, "the owner engages it")

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == KILL_SWITCH
    assert "engaged (owner)" in message
    assert _stops(journal_settings, window) == []


def test_stop_is_refused_under_an_unconsumed_kill_switch_override(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    override_id = override(
        journal_settings, fixed_clock, "engage_kill_switch", None, None, OVERRIDE_REASON
    )
    assert not _engaged(journal_settings, window)  # derive alone ignores it

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == KILL_SWITCH
    assert f"override {override_id}" in message
    assert _stops(journal_settings, window) == []


def test_a_halted_run_after_the_request_refuses_the_closing_stop(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    _stop(journal_settings, fake, fixed_clock)
    run_id = _run(journal_settings, window, DAY1 + timedelta(minutes=5), status="halted")
    fixed_clock.advance(minutes=10)

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == KILL_SWITCH
    assert f"run {run_id} halted" in message
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]
    assert _count(journal_settings, "reconciliations") == 0


def test_the_closing_stop_names_every_open_order_and_missing_outcome(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-open", 1.0, fill=False)
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-filled", 2.0)
    expired = _buy(journal_settings, fake, fixed_clock, run_id, "tp-expired", 1.0, fill=False)
    fake.apply("tp-expired", Expire())
    _collect(journal_settings, fake, fixed_clock, [expired])
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == NOT_READY
    assert "tp-open" in message and "not terminal" in message
    assert "tp-filled" in message and "position_return" in message
    assert "tp-expired" in message and "not_executed" in message
    assert _count(journal_settings, "reconciliations") == 0  # refused before reconciling


def _settled(settings: Settings, fake: FakeBroker, clock: FixedClock, run_id: int) -> OrderRow:
    """A buy `paper settle` closed: `cancelled` / `owner_settled_unknown`, no fill,
    and its `not_executed` outcome."""
    order = _buy(settings, fake, clock, run_id, "tp-settled", 1.0, fill=False)
    at = DAY1 - timedelta(minutes=40)
    _append(
        settings,
        OrderEventRow(
            client_order_id="tp-settled",
            status="cancelled",
            reason="owner_settled_unknown",
            known_at=at,
            ingested_at=at,
        ),
    )
    _outcome(settings, "tp-settled", "not_executed")
    return order


def _fill_row(
    coid: str,
    broker_fill_id: str,
    at: datetime,
    *,
    source: str = "broker_feed",
    superseded_by: int | None = None,
) -> FillRow:
    return FillRow(
        client_order_id=coid,
        filled_at=at,
        quantity=1.0,
        price=PRICE,
        price_implied=source == "broker_status",
        broker_fill_id=broker_fill_id,
        source=source,
        superseded_by=superseded_by,
        known_at=at,
        ingested_at=at,
    )


def _not_ready_now(settings: Settings, window: PaperWindowRow) -> list[str]:
    with open_read_only(settings) as conn:
        return _not_ready(conn, window.window_id)  # type: ignore[arg-type]


def test_the_closing_stop_names_a_live_fill_journaled_after_the_terminal_event(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Spec req 17 / req 15 (3): a fill the feed delivers for a settled order is
    journaled like any fill, and `paper stop`'s readiness read then refuses
    `not_ready`, whatever the order's outcome rows."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _settled(journal_settings, fake, fixed_clock, run_id)
    assert _not_ready_now(journal_settings, window) == []  # the chain is complete

    late = DAY1 - timedelta(minutes=30)
    _append(journal_settings, _fill_row("tp-settled", "late-1", late))
    _outcome(journal_settings, "tp-settled", "position_return")
    _requested(journal_settings, window, DAY1 - timedelta(minutes=20))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == NOT_READY
    assert "tp-settled" in message
    assert "live fill journaled after its terminal event" in message
    assert _count(journal_settings, "reconciliations") == 0


def test_a_superseded_feed_fill_after_a_synthetic_one_is_not_named_by_stop(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Req 8's synthetic fill completes the order; the later feed fill is journaled
    `superseded_by` it, is no live fill, and leaves the chain complete."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-synthetic", 1.0, fill=False)
    at = DAY1 - timedelta(minutes=40)
    (synthetic,) = _append(
        journal_settings, _fill_row("tp-synthetic", "synthetic-1", at, source="broker_status")
    )
    _append(
        journal_settings,
        OrderEventRow(client_order_id="tp-synthetic", status="filled", known_at=at, ingested_at=at),
    )
    _outcome(journal_settings, "tp-synthetic", "position_return")
    _append(
        journal_settings,
        _fill_row("tp-synthetic", "feed-1", DAY1 - timedelta(minutes=30), superseded_by=synthetic),
    )
    assert _not_ready_now(journal_settings, window) == []


def test_the_closing_stop_is_refused_when_a_holding_exceeds_its_residue(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-b1", 3.0)
    _outcome(journal_settings, "tp-b1", "position_return")
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == NOT_FLAT
    assert f"{SPY} holds 3 against a residue of 0" in message
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]


@pytest.mark.parametrize("origin", ["dust", "untradable"])
def test_the_closed_row_lists_each_residue_with_its_quantity_and_origin(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    origin: str,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-b1", 3.0)
    _outcome(journal_settings, "tp-b1", "position_return")
    _forced_exit_skipped(journal_settings, run_id, origin)
    if origin == "untradable":
        at = DAY1 - timedelta(minutes=20)
        _append(
            journal_settings,
            PositionDailyRow(
                run_id=run_id,
                session=date(2026, 9, 30),
                security_id=SPY,
                quantity=3.0,
                mark_price=PRICE,
                value=3 * PRICE,
                tradable=False,
                known_at=at,
                ingested_at=at,
            ),
        )
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))

    result = _stop(journal_settings, fake, fixed_clock)

    assert result.state == "closed"
    (_, closed) = _stops(journal_settings, window)
    assert json.loads(closed.residues_json) == {SPY: {"quantity": 3.0, "origin": origin}}
    # `paper start` (T64) parses this exact shape.
    (parsed,) = _parse_residues(closed.residues_json).values()
    assert (parsed.security_id, parsed.quantity, parsed.origin) == (SPY, 3.0, origin)


def test_parse_residues_refuses_a_null_origin() -> None:
    """A residue's origin must be `dust` or `untradable` (spec req 14, Data
    section); `null` (and anything else) is refused rather than carried
    forward silently (#522 item 4)."""
    residues_json = json.dumps({SPY: {"quantity": 3.0, "origin": None}})

    with pytest.raises(ValueError, match="origin"):
        _parse_residues(residues_json)


def test_an_untradable_skip_on_a_name_that_trades_again_is_not_a_residue(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-b1", 3.0)
    _outcome(journal_settings, "tp-b1", "position_return")
    _forced_exit_skipped(journal_settings, run_id, "untradable")  # no tradable=False mark
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    assert excinfo.value.reason == NOT_FLAT


def test_a_reconciliation_mismatch_refuses_the_closing_stop_and_engages_the_switch(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")  # a position the ledger lacks

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == RECONCILIATION
    assert "broker_only_position" in message
    assert _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (event,) = kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
    assert (event.source, event.fault_type) == ("fault", "ReconciliationError")
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]


def test_a_closed_windows_halted_run_does_not_engage_the_next_window(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    _run(journal_settings, window, DAY1 - timedelta(hours=2), status="halted")
    abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)
    fixed_clock.advance(minutes=1)
    _new_window(journal_settings, fixed_clock.now)
    fixed_clock.advance(minutes=1)

    result = _stop(journal_settings, fake, fixed_clock)

    assert result.state == "requested"


# --- abandon --------------------------------------------------------------------


def test_abandon_under_an_engaged_switch_records_the_note_positions_and_mismatches(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    kill(journal_settings, _connect(journal_settings), fixed_clock, "the owner engages it")
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")
    fixed_clock.advance(minutes=1)

    result = abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, f" {NOTE} ")

    (row,) = _stops(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (reconciliation,) = reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]
        assert open_window(conn) is None
    assert (row.state, row.reason) == ("abandoned", NOTE)
    assert row.reconciliation_id == reconciliation.reconciliation_id == result.reconciliation_id
    assert reconciliation.status == "mismatch"
    residues = json.loads(row.residues_json)
    assert residues["positions"] == {"SPY": 1.0}
    assert "broker_only_position" in {m["kind"] for m in residues["mismatches"]}
    assert _engaged(journal_settings, window)  # abandon releases nothing


def test_abandon_refuses_more_than_one_open_window(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Only one window may be open (spec req 14); `abandon` must refuse the
    same way `kill` does (#540), before any reconciliation or write."""
    _new_window(journal_settings, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))

    with pytest.raises(WindowCommandRefused) as excinfo:
        abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    reason, message = _refusal(excinfo)
    assert reason == MULTIPLE_OPEN_WINDOWS
    assert "more than one open paper window" in message
    assert fake.calls == ()
    assert _stops(journal_settings, window) == []
    assert _count(journal_settings, "reconciliations") == 0


def _assert_abandon_refused_open(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    window: PaperWindowRow,
    coid: str,
) -> None:
    """`abandon` refuses `open_orders` naming `coid`, with no broker call and
    no row of any kind written (#542)."""
    calls = len(fake.calls)
    counts = {t: _count(settings, t) for t in ("reconciliations", "kill_switch", "order_events")}

    with pytest.raises(WindowCommandRefused) as excinfo:
        abandon(settings, _connect(settings), fake, clock, NOTE)

    reason, message = _refusal(excinfo)
    assert reason == OPEN_ORDERS
    assert coid in message and "not terminal" in message
    assert len(fake.calls) == calls  # refused before the final reconciliation
    assert _stops(settings, window) == []
    assert {t: _count(settings, t) for t in counts} == counts
    with open_read_only(settings) as conn:
        assert open_window(conn) is not None


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_abandon_is_refused_while_an_own_order_is_open_at_the_broker(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    side: str,
) -> None:
    """The owner's decision on #542: `abandon` never leaves an own order
    working at the broker and never cancels one; it refuses, writing nothing."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, f"tp-{side}", 1.0, fill=False, side=side)
    assert [o.client_order_id for o in fake.open_orders()] == [f"tp-{side}"]

    _assert_abandon_refused_open(journal_settings, fake, fixed_clock, window, f"tp-{side}")
    assert [o.client_order_id for o in fake.open_orders()] == [f"tp-{side}"]  # not cancelled


def test_abandon_is_refused_while_the_journal_shows_an_order_the_broker_filled(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """`stop`'s rule (`not_ready`): an order is open until the journal holds its
    terminal event, whatever the broker says; collecting it (a run or `paper
    resume`) is what lets the window end."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    order = _buy(journal_settings, fake, fixed_clock, run_id, "tp-late", 1.0, fill=False)
    fake.simulate_fill("tp-late")
    assert fake.open_orders() == []

    _assert_abandon_refused_open(journal_settings, fake, fixed_clock, window, "tp-late")

    _collect(journal_settings, fake, fixed_clock, [order])
    fixed_clock.advance(minutes=1)
    abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)
    assert [s.state for s in _stops(journal_settings, window)] == ["abandoned"]


def test_abandon_is_refused_while_an_order_the_broker_never_received_is_pending(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """A crash between journaling and submitting leaves only a `pending` event:
    open until `paper resume` settles it (`cancelled`, `not_received`)."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    at = DAY1 - timedelta(hours=1)
    (decision_id,) = _append(
        journal_settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 30),
            security_id=SPY,
            side="buy",
            planned_quantity=1.0,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    _append(
        journal_settings,
        OrderRow(
            client_order_id="tp-unsent",
            decision_id=decision_id,
            run_id=run_id,
            session=at.date(),
            attempt=1,
            phase="buy",
            security_id=SPY,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id="tp-unsent", status="pending", known_at=at, ingested_at=at),
    )

    _assert_abandon_refused_open(journal_settings, fake, fixed_clock, window, "tp-unsent")

    ticking = lambda: fixed_clock.advance(microseconds=1)  # noqa: E731
    resume(
        journal_settings,
        _connect(journal_settings),
        fake,
        ticking,
        NOTE,
        False,
        accept_rejections=False,
    )
    with open_read_only(journal_settings) as conn:
        events = order_events_for(conn, window_id=None, client_order_ids=["tp-unsent"])
    latest = events[-1]  # #571 item 3: the reader's (known_at, ingested_at, rowid) order
    assert (latest.status, latest.reason) == ("cancelled", "not_received")
    fixed_clock.advance(minutes=1)
    abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)
    assert [s.state for s in _stops(journal_settings, window)] == ["abandoned"]


def test_an_open_order_of_another_window_does_not_refuse_abandon(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """The read is per window, as `stop`'s is (#542's PR, open question 2)."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-old", 1.0, fill=False)
    _append(
        journal_settings,
        PaperWindowStopRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            at=DAY1 - timedelta(minutes=30),
            state="abandoned",
            reason=NOTE,
            known_at=DAY1 - timedelta(minutes=30),
            ingested_at=DAY1 - timedelta(minutes=30),
        ),
    )
    later = _new_window(journal_settings, DAY1 - timedelta(minutes=20))
    fixed_clock.advance(minutes=1)

    abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    assert [s.state for s in _stops(journal_settings, later)] == ["abandoned"]


def test_abandon_is_allowed_once_every_order_is_terminal(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Terminal orders do not refuse, even without their outcome rows (those
    are `stop`'s `not_ready`, not open orders)."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-filled", 2.0)
    expired = _buy(journal_settings, fake, fixed_clock, run_id, "tp-expired", 1.0, fill=False)
    fake.apply("tp-expired", Expire())
    _collect(journal_settings, fake, fixed_clock, [expired])
    fixed_clock.advance(minutes=1)

    result = abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    (row,) = _stops(journal_settings, window)
    assert row.state == "abandoned"
    assert row.reconciliation_id == result.reconciliation_id
    assert json.loads(row.residues_json)["positions"] == {"SPY": 2.0}


# --- kill -------------------------------------------------------------------------


def test_kill_appends_an_owner_engagement_even_while_a_run_holds_the_lock(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    with run_lock(journal_settings):
        event_id = kill(journal_settings, _connect(journal_settings), fixed_clock, f" {NOTE} ")

    with open_read_only(journal_settings) as conn:
        (event,) = kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
    assert event.event_id == event_id
    assert (event.state, event.source, event.reason) == ("engaged", "owner", NOTE)
    assert _engaged(journal_settings, window)


def test_kill_refuses_more_than_one_open_window(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Only one window may be open (spec req 14); a second open window (which
    nothing below `kill` prevents being written) must not let the owner
    engage the switch against an ambiguous target (#522 item 2)."""
    _new_window(journal_settings, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))

    with pytest.raises(WindowCommandRefused) as excinfo:
        kill(journal_settings, _connect(journal_settings), fixed_clock, NOTE)

    reason, message = _refusal(excinfo)
    assert reason == MULTIPLE_OPEN_WINDOWS
    assert "more than one open paper window" in message
    assert _count(journal_settings, "kill_switch") == 0


# --- the override writer ------------------------------------------------------------


def test_the_override_writer_stores_the_trimmed_reason(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    override_id = override(
        journal_settings,
        fixed_clock,
        "exclude_name",
        date(2026, 10, 30),
        SPY,
        f"   {OVERRIDE_REASON}\n",
    )

    with open_read_only(journal_settings) as conn:
        (stored,) = overrides_for(conn, window.window_id)  # type: ignore[arg-type]
        assert unconsumed_kill_switch_overrides(conn, window.window_id) == []  # type: ignore[arg-type]
    row = stored.override
    assert row.override_id == override_id
    assert (row.kind, row.rebalance_session, row.security_id) == (
        "exclude_name",
        date(2026, 10, 30),
        SPY,
    )
    assert row.reason == OVERRIDE_REASON
    assert row.made_at == row.known_at == DAY1


def test_the_override_writer_refuses_a_reason_below_the_frozen_minimum(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    short = "x" * (MIN_REASON - 1)
    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, fixed_clock, "engage_kill_switch", None, None, f"  {short}  ")

    reason, message = _refusal(excinfo)
    assert reason == REASON
    assert str(MIN_REASON) in message
    assert _count(journal_settings, "overrides") == 0


@pytest.mark.parametrize(
    ("kind", "session", "security_id"),
    [
        ("pause_name", date(2026, 10, 30), SPY),
        ("engage_kill_switch", date(2026, 10, 30), None),
        ("engage_kill_switch", None, SPY),
        ("exclude_name", None, SPY),
        ("keep_name", date(2026, 10, 30), None),
        ("keep_name", date(2026, 10, 29), SPY),  # not a rebalance session
        ("exclude_name", date(2026, 8, 31), SPY),  # before the window's T_0
    ],
)
def test_the_override_writer_refuses_fields_its_kind_does_not_take(
    journal_settings: Settings,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    kind: str,
    session: date | None,
    security_id: str | None,
) -> None:
    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, fixed_clock, kind, session, security_id, OVERRIDE_REASON)

    assert excinfo.value.reason == OVERRIDE
    assert _count(journal_settings, "overrides") == 0


@pytest.mark.parametrize(
    ("session", "security_id"), [(None, None), (date(2026, 10, 30), SPY), (None, SPY)]
)
def test_the_override_writer_refuses_settle_order_first(
    journal_settings: Settings,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    session: date | None,
    security_id: str | None,
) -> None:
    """Spec req 17 (#571): `settle_order` is `paper settle`'s alone (it needs a broker
    read); the writer the page and `paper override` share refuses it as its first
    branch, before the clock or the store is read."""

    def no_clock() -> datetime:
        raise AssertionError("the clock is read before the settle_order refusal")

    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, no_clock, "settle_order", session, security_id, OVERRIDE_REASON)

    reason, message = _refusal(excinfo)
    assert reason == OVERRIDE
    assert "settle_order" in message and "paper settle" in message
    assert _count(journal_settings, "overrides") == 0


def test_the_override_writer_refuses_settle_order_with_no_window(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    """The refusal is the kind's, not `no_window`: the kind is never the writer's."""
    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, fixed_clock, "settle_order", None, None, OVERRIDE_REASON)
    assert excinfo.value.reason == OVERRIDE


def test_override_refuses_more_than_one_open_window(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """Only one window may be open (spec req 14); the override writer must
    refuse the same way `kill` does (#540), before any row is written."""
    _new_window(journal_settings, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))

    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, fixed_clock, "engage_kill_switch", None, None, OVERRIDE_REASON)

    reason, message = _refusal(excinfo)
    assert reason == MULTIPLE_OPEN_WINDOWS
    assert "more than one open paper window" in message
    assert _count(journal_settings, "overrides") == 0


# --- review pass 1: shorts, carried residues, races, non-ok reconciliations -------


def _carried(settings: Settings, window: PaperWindowRow, quantity: float, origin: str) -> None:
    """A `carried_residue` adjustment as `paper start` journals it."""
    at = window.started_at
    _append(
        settings,
        AdjustmentRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=date(2026, 9, 29),
            kind="carried_residue",
            origin=origin,
            security_id=SPY,
            quantity=quantity,
            known_at=at,
            ingested_at=at,
        ),
    )


def _broker_holds(fake: FakeBroker, quantity: float) -> None:
    """The broker books `quantity` SPY outside the window's orders (a residue
    carried in from the previous window)."""
    fake.submit(OrderRequest("prev-1", "SPY", Side.BUY, quantity=quantity))
    fake.simulate_fill("prev-1")


def _untradable_mark(
    settings: Settings, run_id: int, quantity: float, *, tradable: bool = False
) -> None:
    """A run's mark for SPY, `tradable=False` unless the name trades again."""
    at = DAY1 - timedelta(minutes=20)
    _append(
        settings,
        PositionDailyRow(
            run_id=run_id,
            session=date(2026, 9, 30),
            security_id=SPY,
            quantity=quantity,
            mark_price=PRICE,
            value=quantity * PRICE,
            tradable=tradable,
            known_at=at,
            ingested_at=at,
        ),
    )


@pytest.fixture
def carried_window(journal_settings: Settings) -> PaperWindowRow:
    """A window whose starting cash already paid for a 4-share residue the
    broker holds (`_broker_holds(fake, 4.0)`)."""
    return _new_window(
        journal_settings,
        datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
        starting_cash=100_000.0 - 4 * PRICE,
    )


def test_a_short_holding_the_broker_agrees_with_is_never_flat(
    journal_settings: Settings, fake: FakeBroker, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1))
    _buy(journal_settings, fake, fixed_clock, run_id, "tp-s1", 1.0, side="sell")
    _outcome(journal_settings, "tp-s1", "realised_pnl")
    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == NOT_FLAT
    assert f"{SPY} holds -1, a short position" in message
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]


@pytest.mark.parametrize(
    ("carried_origin", "tradable_mark", "expected"),
    [
        ("dust", None, "dust"),
        ("untradable", False, "untradable"),
        ("untradable", None, None),  # no false mark: no residue, not flat
        ("untradable", True, None),  # trades again: no residue, not flat
    ],
)
def test_a_carried_residue_keeps_its_origin_while_it_counts(
    journal_settings: Settings,
    fake: FakeBroker,
    carried_window: PaperWindowRow,
    fixed_clock: FixedClock,
    carried_origin: str,
    tradable_mark: bool | None,
    expected: str | None,
) -> None:
    _carried(journal_settings, carried_window, 4.0, carried_origin)
    _broker_holds(fake, 4.0)
    run_id = _run(journal_settings, carried_window, DAY1 - timedelta(hours=1))
    if tradable_mark is not None:
        _untradable_mark(journal_settings, run_id, 4.0, tradable=tradable_mark)
    _requested(journal_settings, carried_window, DAY1 - timedelta(minutes=50))

    if expected is None:
        with pytest.raises(WindowCommandRefused) as excinfo:
            _stop(journal_settings, fake, fixed_clock)
        assert excinfo.value.reason == NOT_FLAT
        return
    _stop(journal_settings, fake, fixed_clock)

    (_, closed) = _stops(journal_settings, carried_window)
    assert json.loads(closed.residues_json) == {SPY: {"quantity": 4.0, "origin": expected}}


def test_untradable_wins_when_a_carried_dust_residue_mixes_with_an_untradable_skip(
    journal_settings: Settings,
    fake: FakeBroker,
    carried_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _carried(journal_settings, carried_window, 1.0, "dust")
    _broker_holds(fake, 4.0)
    run_id = _run(journal_settings, carried_window, DAY1 - timedelta(hours=1))
    _forced_exit_skipped(journal_settings, run_id, "untradable")
    _untradable_mark(journal_settings, run_id, 4.0)
    # The ledger holds 4: the carried 1 (dust) plus 3 journaled as a receipt,
    # all held under the untradable skip, so the parts mix.
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=carried_window.window_id,  # type: ignore[arg-type]
            session=date(2026, 9, 29),
            kind="spinoff_receipt",
            security_id=SPY,
            quantity=3.0,
            known_at=carried_window.started_at,
            ingested_at=carried_window.started_at,
        ),
    )
    _requested(journal_settings, carried_window, DAY1 - timedelta(minutes=50))

    _stop(journal_settings, fake, fixed_clock)

    (_, closed) = _stops(journal_settings, carried_window)
    assert json.loads(closed.residues_json) == {SPY: {"quantity": 4.0, "origin": "untradable"}}


def test_a_carried_residue_is_split_adjusted_through_the_stop_session(
    journal_settings: Settings,
    fake: FakeBroker,
    carried_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    with open_for_write(journal_settings) as conn:
        conn.execute(
            "INSERT INTO corporate_actions "
            "(security_id, action_type, ex_date, ratio_or_amount, source_action_id, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, 'split', ?, 2.0, '', ?, ?, 'test', 'action')",
            [SPY, date(2026, 9, 30), carried_window.started_at, carried_window.started_at],
        )
    _carried(journal_settings, carried_window, 2.0, "dust")  # 4 after the split
    _broker_holds(fake, 4.0)
    _requested(journal_settings, carried_window, DAY1 - timedelta(minutes=50))

    _stop(journal_settings, fake, fixed_clock)

    (_, closed) = _stops(journal_settings, carried_window)
    assert json.loads(closed.residues_json) == {SPY: {"quantity": 4.0, "origin": "dust"}}


def test_a_saturday_stop_states_the_ledger_for_friday_and_closes(
    journal_settings: Settings,
    fake: FakeBroker,
    carried_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _carried(journal_settings, carried_window, 4.0, "dust")
    _broker_holds(fake, 4.0)
    _requested(journal_settings, carried_window, DAY1 - timedelta(minutes=50))
    saturday = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    fixed_clock.now = saturday

    result = _stop(journal_settings, fake, fixed_clock)

    assert result.state == "closed"
    (_, closed) = _stops(journal_settings, carried_window)
    assert json.loads(closed.residues_json) == {SPY: {"quantity": 4.0, "origin": "dust"}}
    # The next `paper start` accepts the listed residue against the same broker.
    with open_read_only(journal_settings) as conn:
        listings = listing_ends_as_of(conn, saturday, journal_settings)
        actions = live_actions_as_of(conn, saturday)
    flat = _check_flat(
        open_orders=fake.open_orders(),
        positions=fake.positions(),
        previous_stop=closed,
        listings=listings,
        actions=actions,
        tolerance=FROZEN.reconcile_quantity_tolerance,
        now=saturday.date(),
    )
    assert [(r.security_id, r.quantity, r.origin) for r in flat.carried] == [(SPY, 4.0, "dust")]


def test_the_flatness_check_reads_a_listing_with_no_valid_from_as_the_shared_rule_does(
    journal_settings: Settings,
    fake: FakeBroker,
    carried_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A listing row with no `valid_from` is read by `planning.current_listings`'
    rule (#705): it never beats a dated row, so the residue still matches its
    dated ticker instead of the check raising `TypeError`."""
    _carried(journal_settings, carried_window, 4.0, "dust")
    _broker_holds(fake, 4.0)
    _requested(journal_settings, carried_window, DAY1 - timedelta(minutes=50))
    saturday = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    fixed_clock.now = saturday
    _stop(journal_settings, fake, fixed_clock)
    (_, closed) = _stops(journal_settings, carried_window)
    with open_read_only(journal_settings) as conn:
        listings = listing_ends_as_of(conn, saturday, journal_settings)
        actions = live_actions_as_of(conn, saturday)
    undated = listings.filter(pl.col("security_id") == SPY).with_columns(
        pl.lit("SPY_UNDATED").alias("ticker"), pl.lit(None, dtype=pl.Date).alias("valid_from")
    )

    flat = _check_flat(
        open_orders=fake.open_orders(),
        positions=fake.positions(),
        previous_stop=closed,
        listings=pl.concat([undated, listings]),
        actions=actions,
        tolerance=FROZEN.reconcile_quantity_tolerance,
        now=saturday.date(),
    )

    assert [(r.security_id, r.quantity, r.origin) for r in flat.carried] == [(SPY, 4.0, "dust")]


@pytest.mark.parametrize("engagement", ["kill", "override"])
def test_an_engagement_written_after_the_first_checks_refuses_the_request(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    engagement: str,
) -> None:
    import tradepartner.execution.window as window_module

    original = window_module._command_clock
    fired: list[bool] = []

    def engage_then_read(clock: Any) -> datetime:
        if fired:
            return original(clock)
        fired.append(True)
        if engagement == "kill":
            kill(journal_settings, _connect(journal_settings), clock, "the owner engages it")
        else:
            override(journal_settings, clock, "engage_kill_switch", None, None, OVERRIDE_REASON)
        return original(clock)

    monkeypatch.setattr(window_module, "_command_clock", engage_then_read)

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    assert excinfo.value.reason == KILL_SWITCH
    assert _stops(journal_settings, window) == []


@pytest.mark.parametrize("engagement", ["kill", "override"])
def test_an_engagement_written_during_the_closing_stop_refuses_the_close(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    engagement: str,
) -> None:
    import tradepartner.execution.window as window_module

    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))
    original = window_module._residues

    def residues_then_engage(*args: Any) -> Any:
        listed = original(*args)
        if engagement == "kill":
            kill(journal_settings, _connect(journal_settings), fixed_clock, "the owner engages")
        else:
            override(
                journal_settings, fixed_clock, "engage_kill_switch", None, None, OVERRIDE_REASON
            )
        return listed

    monkeypatch.setattr(window_module, "_residues", residues_then_engage)

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    assert excinfo.value.reason == KILL_SWITCH
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]


def test_a_reconciliation_written_during_the_closing_stop_refuses_the_close(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradepartner.execution.window as window_module

    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))
    original = window_module._residues

    def residues_then_reconcile(*args: Any) -> Any:
        listed = original(*args)
        reconcile_now(
            journal_settings,
            _connect(journal_settings),
            fake,
            window,
            DAY1.date(),
            fixed_clock,
            _connect(journal_settings),
            frozen=FROZEN,
            as_of=fixed_clock(),
        )
        return listed

    monkeypatch.setattr(window_module, "_residues", residues_then_reconcile)

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == RECONCILIATION
    assert "was written after" in message
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]


@pytest.mark.parametrize("status", ["pending_unresolved", "fills_lagging"])
def test_a_reconciliation_that_is_not_ok_refuses_the_close(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    import tradepartner.execution.window as window_module

    _requested(journal_settings, window, DAY1 - timedelta(minutes=50))
    original = window_module.reconcile_now

    def not_ok(*args: Any, **kwargs: Any) -> Any:
        return replace(original(*args, **kwargs), status=status, pending_ids=("tp-x",))

    monkeypatch.setattr(window_module, "reconcile_now", not_ok)

    with pytest.raises(WindowCommandRefused) as excinfo:
        _stop(journal_settings, fake, fixed_clock)

    reason, message = _refusal(excinfo)
    assert reason == RECONCILIATION
    assert status in message and "tp-x" in message
    assert [s.state for s in _stops(journal_settings, window)] == ["requested"]
    assert _count(journal_settings, "kill_switch") == 0
    assert not _engaged(journal_settings, window)


def test_kill_raises_when_its_row_cannot_be_written(
    journal_settings: Settings,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(switch, "engage", lambda *a, **k: switch.WriteFailed("store locked"))
    with pytest.raises(KillWriteFailed, match="NOT engaged: store locked"):
        kill(journal_settings, _connect(journal_settings), fixed_clock, NOTE)


def test_kill_raises_kill_write_failed_for_a_bad_clock(
    journal_settings: Settings, window: PaperWindowRow
) -> None:
    def broken() -> datetime:
        raise OSError("no clock")

    with pytest.raises(KillWriteFailed, match="NOT engaged"):
        kill(journal_settings, _connect(journal_settings), broken, NOTE)
    assert _count(journal_settings, "kill_switch") == 0


def test_abandon_engages_the_switch_when_it_fails_after_a_mismatch(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")
    calls = {"n": 0}
    original = fake.positions

    def positions_fail_after_reconcile() -> Any:
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("broker down")
        return original()

    monkeypatch.setattr(fake, "positions", positions_fail_after_reconcile)

    with pytest.raises(ConnectionError):
        abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    assert _stops(journal_settings, window) == []
    assert _engaged(journal_settings, window)


def test_abandon_says_the_switch_is_not_engaged_when_its_fault_row_fails(
    journal_settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")
    calls = {"n": 0}
    original = fake.positions

    def positions_fail_after_reconcile() -> Any:
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("broker down")
        return original()

    monkeypatch.setattr(fake, "positions", positions_fail_after_reconcile)
    monkeypatch.setattr(switch, "engage", lambda *a, **k: switch.WriteFailed("store locked"))

    with pytest.raises(ConnectionError) as excinfo:
        abandon(journal_settings, _connect(journal_settings), fake, fixed_clock, NOTE)

    assert any(
        "kill switch row could not be written, so it is NOT engaged: store locked" in note
        for note in getattr(excinfo.value, "__notes__", [])
    )
    assert _stops(journal_settings, window) == []


def test_the_engage_kind_is_the_shared_schema_constant() -> None:
    """#691: the window's `engage_kill_switch` checks read `schema.ENGAGE_KILL_SWITCH_KIND`,
    never a quoted copy of it, so a renamed kind cannot leave the window matching a
    stale string."""
    assert window_module._ENGAGE_KILL_SWITCH is schema.ENGAGE_KILL_SWITCH_KIND
    code = re.sub(r'"""[\s\S]*?"""', "", Path(window_module.__file__).read_text())
    assert f'"{schema.ENGAGE_KILL_SWITCH_KIND}"' not in code


# --- a version-16 journal (#1261, T132) -------------------------------------------


def _downgrade_to_version_16(settings: Settings) -> None:
    """Make the seeded store a version-16 one: no `book_id` on `orders`, so
    `require_journal` raises `SchemaVersionError`."""
    with open_for_write(settings) as conn:
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")


def test_kill_refuses_schema_version_on_a_version_16_store(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1261: with an open window in an unmigrated version-16 store, `kill`
    refuses `schema_version` naming the fix, not a false `no_window`, and writes
    nothing."""
    _downgrade_to_version_16(journal_settings)

    with pytest.raises(WindowCommandRefused) as excinfo:
        kill(journal_settings, _connect(journal_settings), fixed_clock, NOTE)

    reason, message = _refusal(excinfo)
    assert reason == window_module.SCHEMA_VERSION
    assert "open it for writing once" in message
    assert _count(journal_settings, "kill_switch") == 0


def test_override_refuses_schema_version_on_a_version_16_store(
    journal_settings: Settings, window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1261: the same for `override` with `engage_kill_switch`; nothing is
    written."""
    _downgrade_to_version_16(journal_settings)

    with pytest.raises(WindowCommandRefused) as excinfo:
        override(journal_settings, fixed_clock, "engage_kill_switch", None, None, OVERRIDE_REASON)

    reason, message = _refusal(excinfo)
    assert reason == window_module.SCHEMA_VERSION
    assert "open it for writing once" in message
    assert _count(journal_settings, "overrides") == 0

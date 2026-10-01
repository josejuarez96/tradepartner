"""`paper resume`: settle, collect, reconcile, release (Phase 4 spec req 5;
reqs 4 and 8 for the settlement and the lag bound; plan T61b).

The only way the kill switch is released. In the req 5 order:

1. Take the run lock (T59). `LockHeld` propagates. With no open window the
   outcome is `no_window`: no broker call, no write.
2. Journal the `resume_invocations` row first, so a refused resume is still
   on record and its fill cursor has a writer id.
3. Close every unfinished run of the window `crashed`.
4. Settle every `pending` order of the window (req 4). `get_order` found: an
   `accepted` event with the broker's order id, which acknowledges it, and
   step 5's collection then journals its fills **before** any terminal event
   (T58 writes the terminal event only once the journaled fills reach the
   broker's `filled_quantity`). `UnknownOrderError`: a `cancelled` event with
   reason `not_received`. Any other broker error propagates.
5. Collect every non-terminal order of the window (T58, writer `resume`).
   That journals this invocation's `fill_cursors` row, whatever follows.
   - A rejection-cap verdict from the collection (an all-rejected or
     over-cap submitting run whose rejection this resume journaled) refuses
     the release (#374, T58's handoff).
   - An order still `fills_lagging` past the frozen
     `risk.max_fill_lag_sessions` (`collect.lag_verdict`, anchored on the first
     reconciliation that listed it) refuses without `accept_broker_fills`.
     With it, each such order `get_order` reports finished gets a synthetic
     residual fill and its terminal event; one the broker still holds open
     refuses, since it may yet fill.
6. `reconcile_now` (T61) for the clock's session. A mismatch refuses (the
   mismatch row is written, the switch stays engaged, and is engaged with
   source `fault` if nothing had engaged it). Any other status but
   `ok` refuses too: `switch.release` takes only an `ok` reconciliation, so
   a lag inside the bound (`fills_lagging`) waits for the feed. That is
   stricter than req 8's "a lag inside the bound passes"; the PR raises it.
7. Release (`switch.release`) with the `resume_id`, that reconciliation's id
   and the drawdown peak: the ledger equity at the window's last mark (the
   cash row plus every name's value), or the current peak when nothing is
   marked yet; a peak that is not positive refuses. The reconciliation cited
   is the window's highest id, the one this resume wrote. A switch that is
   not engaged has nothing to release: the outcome is `not_engaged`, after
   the same settlement and reconciliation. After the release the state is
   derived again, and a switch that still derives engaged (a clock that did
   not move past the crashed close) is reported refused, not released.

**The synthetic residual fill** (req 8): quantity = `filled_quantity` minus
the journaled quantity; price = (`filled_quantity` x `filled_avg_price` minus
the journaled notional) / that quantity, stored as computed even below zero,
with `price_implied`; `filled_at` = the broker's `filled_at`, else the close
of the order's session; `source = broker_status`, `broker_fill_id =
synthetic:<client_order_id>`. It and the order's terminal event share one
write chunk. A real fill delivered later is journaled `superseded_by` it by
the collector (T58), so every reader counts the position once.

A resume writes no `decision_events` or `rebalance_events` row: the
collection runs without the write-off back-fill.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from tradepartner.adapters.broker import TERMINAL_STATUSES, Broker, Order, UnknownOrderError
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import switch
from tradepartner.execution.collect import Connect, OrderReading, collect, lag_verdict
from tradepartner.execution.lock import run_lock
from tradepartner.execution.reconcile import OK
from tradepartner.execution.reconcile_run import command_session, frozen_risk, reconcile_now
from tradepartner.store.journal import (
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperWindowRow,
    PositionDailyRow,
    ResumeInvocationRow,
    append,
    kill_switch_events_for,
    non_terminal_orders,
    open_window,
    pending_orders,
    positions_daily_for,
    reconciliations_for,
    runs_for,
)
from tradepartner.timeutil import ensure_tz_aware_utc

RELEASED = "released"
REFUSED = "refused"
NO_WINDOW = "no_window"
NOT_ENGAGED = "not_engaged"
NOT_RECEIVED = "not_received"
ACKNOWLEDGED = "acknowledged"

_WRITER = "resume"
_CRASHED = "crashed"
_ACCEPTED = "accepted"
_CANCELLED = "cancelled"
_BROKER_STATUS = "broker_status"


@dataclass(frozen=True)
class ResumeOutcome:
    """What one `paper resume` did. `reasons` says why it was refused;
    `settled` pairs each `pending` order with `acknowledged` or
    `not_received`."""

    status: str
    resume_id: int | None
    reasons: tuple[str, ...] = ()
    crashed_runs: tuple[int, ...] = ()
    settled: tuple[tuple[str, str], ...] = ()
    synthetic_fills: tuple[str, ...] = ()
    reconciliation_id: int | None = None
    released_event_id: int | None = None


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    """One clock reading, tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
    try:
        reading = clock()
        if not isinstance(reading, datetime):
            raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
        return ensure_tz_aware_utc(reading, field_name="clock")
    except Exception as exc:
        raise ClockError(f"clock failed: {type(exc).__name__}") from exc


def _window_id(window: PaperWindowRow) -> int:
    if window.window_id is None:
        raise ValueError("a paper_windows row without a window_id")
    return window.window_id


def _start(
    connect: Connect, window_id: int, now: datetime, reason: str, accept_broker_fills: bool
) -> tuple[int, tuple[int, ...], list[OrderRow]]:
    """The invocation row, the crashed closes and the `pending` orders, in one chunk."""
    with connect() as conn:
        resume_id = append(
            conn,
            ResumeInvocationRow(
                at=now,
                reason=reason,
                accept_broker_fills=accept_broker_fills,
                known_at=now,
                ingested_at=now,
            ),
        )
        assert resume_id is not None
        crashed: list[int] = []
        for run in runs_for(conn, window_id):
            if run.result is None and run.run.run_id is not None:
                append(
                    conn,
                    PaperRunResultRow(
                        run_id=run.run.run_id,
                        finished_at=now,
                        status=_CRASHED,
                        message=f"closed by paper resume {resume_id}",
                        clock_fault=False,
                        known_at=now,
                        ingested_at=now,
                    ),
                )
                crashed.append(run.run.run_id)
        pending = pending_orders(conn, window_id=window_id)
    return resume_id, tuple(crashed), pending


def _settle(
    broker: Broker, connect: Connect, pending: Sequence[OrderRow], clock: Callable[[], datetime]
) -> tuple[tuple[str, str], ...]:
    """Acknowledge each `pending` order the broker knows, or cancel it
    `not_received` (module docstring, step 4)."""
    if not pending:
        return ()
    events: list[OrderEventRow] = []
    settled: list[tuple[str, str]] = []
    readings: dict[str, Order | None] = {}
    for order in pending:
        try:
            readings[order.client_order_id] = broker.get_order(order.client_order_id)
        except UnknownOrderError:
            readings[order.client_order_id] = None
    stamp = _read_clock(clock)
    for order in pending:
        reading = readings[order.client_order_id]
        if reading is None:
            events.append(
                OrderEventRow(
                    client_order_id=order.client_order_id,
                    status=_CANCELLED,
                    reason=NOT_RECEIVED,
                    known_at=stamp,
                    ingested_at=stamp,
                )
            )
            settled.append((order.client_order_id, NOT_RECEIVED))
        else:
            events.append(
                OrderEventRow(
                    client_order_id=order.client_order_id,
                    status=_ACCEPTED,
                    broker_order_id=reading.broker_order_id,
                    known_at=stamp,
                    ingested_at=stamp,
                )
            )
            settled.append((order.client_order_id, ACKNOWLEDGED))
    with connect() as conn:
        for event in events:
            append(conn, event)
    return tuple(settled)


def _synthetic(reading: OrderReading, tolerance: float) -> FillRow | str:
    """The residual fill that completes a finished order, or why there is none."""
    order, broker = reading.order, reading.reading
    coid = order.client_order_id
    filled, average = broker.filled_quantity, broker.filled_avg_price
    if filled is None or average is None or not (math.isfinite(filled) and math.isfinite(average)):
        return f"{coid}: the broker gives no usable filled quantity and average price"
    residual = filled - reading.journaled_quantity
    if not residual > tolerance:
        return f"{coid}: no residual left to fill"
    price = (filled * average - reading.journaled_notional) / residual
    if not math.isfinite(price):
        return f"{coid}: the implied residual price is not finite"
    filled_at = broker.filled_at or session_close(order.session)
    return FillRow(
        client_order_id=coid,
        filled_at=filled_at,
        quantity=residual,
        price=price,
        price_implied=True,
        broker_fill_id=f"synthetic:{coid}",
        source=_BROKER_STATUS,
        known_at=filled_at,  # stamped with the write's clock reading in _write_synthetic
        ingested_at=filled_at,
    )


def _write_synthetic(
    connect: Connect, fills: Sequence[tuple[FillRow, OrderReading]], clock: Callable[[], datetime]
) -> None:
    stamp = _read_clock(clock)
    with connect() as conn:
        for fill, reading in fills:
            append(conn, replace(fill, known_at=stamp, ingested_at=stamp))
            broker = reading.reading
            append(
                conn,
                OrderEventRow(
                    client_order_id=reading.order.client_order_id,
                    event_at=broker.filled_at,
                    status=broker.status.value,
                    broker_order_id=broker.broker_order_id,
                    filled_quantity=broker.filled_quantity,
                    filled_avg_price=broker.filled_avg_price,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )


def _lag(
    connect: Connect,
    window_id: int,
    lagging: Sequence[OrderReading],
    now: datetime,
    frozen: RiskConfig,
    accept_broker_fills: bool,
) -> tuple[list[str], list[tuple[FillRow, OrderReading]]]:
    """Refusal reasons and synthetic fills for the orders past the lag bound."""
    if not lagging:
        return [], []
    with connect() as conn:
        reconciliations = reconciliations_for(conn, window_id)
    session = command_session(now)
    reasons: list[str] = []
    fills: list[tuple[FillRow, OrderReading]] = []
    for reading in lagging:
        verdict = lag_verdict(reading.order, reconciliations, session, frozen)
        if not verdict.breached:
            continue
        coid = reading.order.client_order_id
        if not accept_broker_fills:
            reasons.append(
                f"{coid} is past the lag bound ({verdict.sessions_past} sessions): "
                "resume with --accept-broker-fills to journal its residual"
            )
        elif reading.reading.status not in TERMINAL_STATUSES:
            reasons.append(f"{coid} is past the lag bound but the broker still holds it open")
        else:
            made = _synthetic(reading, frozen.reconcile_quantity_tolerance)
            if isinstance(made, str):
                reasons.append(made)
            else:
                fills.append((made, reading))
    return reasons, fills


def _mark_equity(marks: Sequence[PositionDailyRow]) -> float | None:
    """Ledger equity at the last marked session: its cash row plus every
    name's value, or None when the session's rows cannot give it."""
    if not marks:
        return None
    last = max(m.session for m in marks)
    rows = [m for m in marks if m.session == last]
    cash = [m.cash for m in rows if m.security_id is None]
    values = [m.value for m in rows if m.security_id is not None]
    if len(cash) != 1 or cash[0] is None or any(v is None for v in values):
        return None
    return cash[0] + math.fsum(v for v in values if v is not None)


def _switch(connect: Connect, window: PaperWindowRow) -> switch.SwitchState:
    """The window's derived kill-switch state, read by a lock holder."""
    window_id = _window_id(window)
    with connect() as conn:
        runs = runs_for(conn, window_id)
        events = kill_switch_events_for(conn, window_id)
    return switch.derive(
        window,
        events,
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )


def resume(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    reason: str,
    accept_broker_fills: bool,
) -> ResumeOutcome:
    """`paper resume --reason` (module docstring). `connect` opens a write chunk
    (`lambda: store.db.open_for_write(settings)`). Raises `ValueError` for a
    blank `reason` before anything, `LockHeld` while another process holds the
    run lock, `ClockError` for a bad clock reading, and whatever the broker or
    the store raises; a refusal is an outcome, never an exception."""
    if not reason.strip():
        raise ValueError("paper resume needs a non-blank --reason")
    with run_lock(settings):
        with connect() as conn:
            window = open_window(conn)
        if window is None:
            return ResumeOutcome(NO_WINDOW, None, ("no_window: no paper window is open",))
        window_id = _window_id(window)
        frozen = frozen_risk(window)
        now = _read_clock(clock)

        resume_id, crashed, pending = _start(connect, window_id, now, reason, accept_broker_fills)
        settled = _settle(broker, connect, pending, clock)
        with connect() as conn:
            open_orders = non_terminal_orders(conn, window_id=window_id)
        collected = collect(
            broker, connect, open_orders, clock, _WRITER, resume_id, frozen, settings
        )

        def outcome(
            status: str,
            *reasons: str,
            reconciliation_id: int | None = None,
            released_event_id: int | None = None,
        ) -> ResumeOutcome:
            return ResumeOutcome(
                status=status,
                resume_id=resume_id,
                reasons=tuple(reasons),
                crashed_runs=crashed,
                settled=settled,
                synthetic_fills=tuple(f.client_order_id for f, _ in synthetic),
                reconciliation_id=reconciliation_id,
                released_event_id=released_event_id,
            )

        synthetic: list[tuple[FillRow, OrderReading]] = []
        reasons = [breach.message for breach in collected.rejections]
        lag_reasons, synthetic_fills = _lag(
            connect, window_id, collected.lagging, now, frozen, accept_broker_fills
        )
        reasons += lag_reasons
        if reasons:
            return outcome(REFUSED, *reasons)
        if synthetic_fills:
            _write_synthetic(connect, synthetic_fills, clock)
            synthetic = synthetic_fills

        session = command_session(now)
        try:
            result = reconcile_now(
                settings, connect, broker, window, session, clock, connect, frozen=frozen
            )
        except ReconciliationError as exc:
            # A fault found here engages the switch when nothing has (a resume
            # with no engagement to release), as `paper reconcile` does.
            refusal = [f"reconciliation failed: {exc}"]
            if not _switch(connect, window).engaged:
                try:
                    engaged = switch.engage(
                        settings,
                        clock,
                        window_id=window_id,
                        source="fault",
                        fault_type=ReconciliationError.__name__,
                        reason=str(exc),
                    )
                except Exception as engage_error:  # a bad clock reading, say
                    engaged = switch.WriteFailed(f"{type(engage_error).__name__}: {engage_error}")
                if isinstance(engaged, switch.WriteFailed):
                    refusal.append(
                        f"the kill switch row could not be written, so it is NOT engaged: "
                        f"{engaged.error}"
                    )
            return outcome(REFUSED, *refusal)
        with connect() as conn:
            reconciliation_id = max(
                r.reconciliation_id or 0 for r in reconciliations_for(conn, window_id)
            )
            events = kill_switch_events_for(conn, window_id)
            marks = positions_daily_for(conn, window_id)
        if result.status != OK:
            return outcome(
                REFUSED,
                f"reconciliation {reconciliation_id} is {result.status}; a release needs ok",
                reconciliation_id=reconciliation_id,
            )
        if not _switch(connect, window).engaged:
            return outcome(NOT_ENGAGED, reconciliation_id=reconciliation_id)
        peak = _mark_equity(marks) if marks else switch.drawdown_peak(window, events)
        if peak is None or not (math.isfinite(peak) and peak > 0):
            return outcome(
                REFUSED,
                f"the last mark gives no positive equity for the drawdown peak: {peak!r}",
                reconciliation_id=reconciliation_id,
            )
        event_id = switch.release(
            settings,
            clock,
            window_id=window_id,
            resume_id=resume_id,
            reconciliation_id=reconciliation_id,
            peak_equity=peak,
        )
        after = _switch(connect, window)
        if after.engaged:
            # A clock that did not move past the crashed close (switch.derive
            # clears a crashed run only for a release stamped after it).
            return outcome(
                REFUSED,
                f"released (event {event_id}) but the switch still derives engaged: "
                + "; ".join(after.causes),
                reconciliation_id=reconciliation_id,
                released_event_id=event_id,
            )
        return outcome(RELEASED, reconciliation_id=reconciliation_id, released_event_id=event_id)

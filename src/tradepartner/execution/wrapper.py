"""The risk-gated wrapper's core: the shared clock, the rejection allowlist, the
halt path and the replay (Phase 4 spec req 4; ADR 0007 points 1, 3, 4, 5 and 7;
ADR 0010 points 2 and 4; plan T60).

**The allowlist.** One frozen constant per `Broker` method, never config, pinned
by a test (ADR 0010 point 4): `submit` empty (a "not tradable" or "not
fractionable" rejection at submit means state changed mid-run, "insufficient
buying power" that the ledger and the broker disagree, "market closed" a
scheduling fault); `cancel` `{OrderNotOpenError}` (the order finished first:
journaled `cancel_noop`, not a fault); every other method empty. `classify`
leaves a `BaseException` that is not an `Exception` (an interrupt) untouched,
checks `SystemFaultError` first, so no subclass can ever pass as a rejection,
then the method's constant, and halts on everything else, a type nobody
classified included.

**The shared clock.** The wrapper and the adapter hold one clock object (ADR
0007 point 5, checked by identity on the adapter's `clock` attribute; the
`Broker` ABC exposes none). `read_clock` turns an exception, a non-`datetime`
or a naive value into `ClockError`, and so does a reading earlier than the
wrapper's previous one. `clock_precheck` runs before each phase's first submit
(ADR 0010 point 2): the identity, then a well-formed reading not earlier than
the last `ok` ingestion run's `finished_at` and not later than the close of the
session `risk.clock_max_sessions_late` sessions after S.

**The halt path** (`halt`, also the run's halt entry point, T63), in the req 4
order:

1. the `kill_switch` `engaged` row (source `fault`, the fault's type, the
   message as its reason) through T59's `switch.engage`, which retries the
   lock; when the write still fails, the `kill_switch_write_failed` alert goes
   through every non-store channel and the process exits non-zero
   (`SystemExit(WRITE_FAILED_EXIT_CODE)`) with nothing else done. The
   `engaged` row is where the fault is journaled with its type; the result row
   (step 5) repeats the type with the full message. `StaleDataError` writes no
   `engaged` row: it is a data fault the next ingest cures;
2. best-effort cancels of the run's **acknowledged** non-terminal orders,
   oldest first. A `pending` order (no broker-acknowledged event) gets neither
   a cancel nor a read: `paper resume` settles it. Before every cancel call a
   non-terminal `cancel_requested` event with reason `halt`; after it,
   `cancel_noop` (`OrderNotOpenError`, the allowlist) or `cancel_failed` (any
   other exception: the halt stands), both also with reason `halt`, so the
   halt is visible on every event of the cancel and `plan.decision_state`'s
   test (a `cancel_requested` **or** `cancel_failed` with reason `halt`) holds
   whichever row a reader meets. A successful call writes nothing more: the
   outcome is read back;
3. one T58 `collect` per order a cancel was requested for, so its fills are
   journaled and its terminal event only once it is already done; an order
   still `pending_cancel` stays in flight for the next run's step 3 or `paper
   resume`. A failed read is journaled `cancel_failed` (reason `halt`) and the
   halt stands. **A `ClockError` from the read** (the broker-clock skew check,
   which every read of the account-wide feed meets again) is not a new fault:
   the halt stands, the switch stays engaged, and the alert and the result row
   name it (#375). A rejection verdict a read returns is named in the alert
   too (T58's handoff: a halt-path caller never drops one). With
   `write_offs`, each read back-fills the `written_off` row of a terminal buy
   of a pending rebalance that lacks one (T58);
4. the alert (`halted`, or `stale_data` for `StaleDataError`), run-scoped. A
   store error writing it is named in the result row and the halt continues;
5. the run's `paper_run_results` row, `halted` (or `stale`), with the fault's
   type, the message and `clock_fault`;
6. re-raises the fault.

**Clock faults on the halt path** (Definitions). After a `ClockError`, the
fault itself or any met on the way (a reading that raises, is malformed or
goes back, or a skew in a read), every row the halt path writes is stamped by
`store.db.utc_now()`, the clock is never read again, and the result row's
`clock_fault` is set. So a clock that keeps raising still gets the `engaged`
row first.

**The replay** (ADR 0007 point 3, ADR 0010 point 3). A
`DuplicateClientOrderIdError` from `submit` can only mean the journal and the
broker disagree. `replay` fetches the order: identical fields (id, symbol,
side, notional, quantity) adopt it, journaled as a non-terminal `replay` event
carrying the broker's order id; any difference, or a failed fetch, raises
`SystemFaultError`, which halts.

Every message journaled or sent is scrubbed of the configured secrets first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn, Protocol
from zoneinfo import ZoneInfo

from tradepartner.adapters.broker import (
    Broker,
    DuplicateClientOrderIdError,
    Order,
    OrderNotOpenError,
    OrderRequest,
)
from tradepartner.config import RiskConfig, Settings, secret_values
from tradepartner.errors import ClockError, StaleDataError, SystemFaultError
from tradepartner.execution import switch
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.collect import Connect, WriteOffContext, collect
from tradepartner.store.db import utc_now
from tradepartner.store.journal import (
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    append,
    non_terminal_orders,
    pending_orders,
)
from tradepartner.store.schema import HALT_REASON
from tradepartner.timeutil import ensure_tz_aware_utc

__all__ = [
    "ACCOUNT_ALLOWLIST",
    "ALLOWLISTS",
    "ASSETS_ALLOWLIST",
    "CANCEL_ALLOWLIST",
    "FILLS_ALLOWLIST",
    "GET_ORDER_ALLOWLIST",
    "OPEN_ORDERS_ALLOWLIST",
    "POSITIONS_ALLOWLIST",
    "SUBMIT_ALLOWLIST",
    "WRITE_FAILED_EXIT_CODE",
    "RiskGatedBroker",
    "TradingCalendar",
    "Verdict",
    "classify",
]

#: The rejection allowlist, one frozen constant per `Broker` method (module
#: docstring). Changing one is a safety-reviewed change (ADR 0007, ADR 0010).
SUBMIT_ALLOWLIST: tuple[type[Exception], ...] = ()
CANCEL_ALLOWLIST: tuple[type[Exception], ...] = (OrderNotOpenError,)
GET_ORDER_ALLOWLIST: tuple[type[Exception], ...] = ()
OPEN_ORDERS_ALLOWLIST: tuple[type[Exception], ...] = ()
FILLS_ALLOWLIST: tuple[type[Exception], ...] = ()
POSITIONS_ALLOWLIST: tuple[type[Exception], ...] = ()
ACCOUNT_ALLOWLIST: tuple[type[Exception], ...] = ()
ASSETS_ALLOWLIST: tuple[type[Exception], ...] = ()

#: Each `Broker` method's constant, keyed by the method's own name (taken from the
#: ABC, so a renamed method fails here, and the `fills` key is not a SQL literal).
ALLOWLISTS: Mapping[str, tuple[type[Exception], ...]] = MappingProxyType(
    {
        Broker.submit.__name__: SUBMIT_ALLOWLIST,
        Broker.cancel.__name__: CANCEL_ALLOWLIST,
        Broker.get_order.__name__: GET_ORDER_ALLOWLIST,
        Broker.open_orders.__name__: OPEN_ORDERS_ALLOWLIST,
        Broker.fills.__name__: FILLS_ALLOWLIST,
        Broker.positions.__name__: POSITIONS_ALLOWLIST,
        Broker.account.__name__: ACCOUNT_ALLOWLIST,
        Broker.assets.__name__: ASSETS_ALLOWLIST,
    }
)

#: The exit status when the `engaged` row cannot be written (spec req 4).
WRITE_FAILED_EXIT_CODE = 1

_NEW_YORK = ZoneInfo("America/New_York")
_MASK = "***"
_FAULT = "fault"
_RUN = "run"
_HALTED = "halted"
_STALE = "stale"
_STALE_DATA = "stale_data"
_CANCEL_REQUESTED = "cancel_requested"
_CANCEL_NOOP = "cancel_noop"
_CANCEL_FAILED = "cancel_failed"
_REPLAY = "replay"
_READ_CLOCK_NOTE = "halt read stands on ClockError"
#: The `Order` fields a replay must match exactly.
_REPLAY_FIELDS = ("client_order_id", "symbol", "side", "notional", "quantity")


class Verdict(StrEnum):
    """What the wrapper does with an exception from a broker call."""

    HALT = "halt"
    ALLOWED = "allowed"
    PROPAGATE = "propagate"


class TradingCalendar(Protocol):
    """The calendar the pre-check needs (`tradepartner.calendar` satisfies it)."""

    def next_session(self, day: date) -> date: ...

    def session_close(self, day: date) -> datetime: ...


def classify(method: str, exc: BaseException) -> Verdict:
    """The verdict for `exc` raised by `Broker.<method>` (module docstring).
    Raises `ValueError` for a name that is not a `Broker` method."""
    if method not in ALLOWLISTS:
        raise ValueError(f"not a Broker method: {method!r}")
    if not isinstance(exc, Exception):
        return Verdict.PROPAGATE
    if isinstance(exc, SystemFaultError):
        return Verdict.HALT
    if isinstance(exc, ALLOWLISTS[method]):
        return Verdict.ALLOWED
    return Verdict.HALT


def _checked(reading: object) -> datetime:
    """A clock reading as tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
    try:
        if not isinstance(reading, datetime):
            raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
        return ensure_tz_aware_utc(reading, field_name="clock")
    except Exception as exc:
        raise ClockError(f"clock reading rejected: {exc}") from exc


class _HaltClock:
    """The halt path's stamps: the wrapper's checked clock until a `ClockError`
    is met, then `utc_now()` for good, with `fault` set (module docstring)."""

    def __init__(self, wrapper: RiskGatedBroker, fault: bool) -> None:
        self._wrapper = wrapper
        self.fault = fault

    def __call__(self) -> datetime:
        if not self.fault:
            try:
                return self._wrapper.read_clock()
            except ClockError:
                self.fault = True
        return utc_now()

    def trip(self) -> None:
        self.fault = True


class RiskGatedBroker:
    """The only caller of `Broker.submit` and `Broker.cancel` (module docstring).

    `journal` opens a write chunk (`lambda: store.db.open_for_write(settings)`),
    `frozen` is the window's frozen `risk.*` section, `alerter` writes the halt's
    alert, and `calendar` gives session closes for the pre-check."""

    def __init__(
        self,
        broker: Broker,
        clock: Callable[[], datetime],
        frozen: RiskConfig,
        settings: Settings,
        journal: Connect,
        calendar: TradingCalendar,
        alerter: Alerter,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._frozen = frozen
        self._settings = settings
        self._journal = journal
        self._calendar = calendar
        self._alerter = alerter
        self._last: datetime | None = None

    def __repr__(self) -> str:
        return f"RiskGatedBroker(broker={type(self._broker).__name__})"

    # --- the clock -----------------------------------------------------------------

    def read_clock(self) -> datetime:
        """One reading of the shared clock, checked: well-formed and never earlier
        than the previous reading, else `ClockError`."""
        try:
            raw = self._clock()
        except Exception as exc:
            raise ClockError(f"clock raised {type(exc).__name__}") from exc
        reading = _checked(raw)
        if self._last is not None and reading < self._last:
            raise ClockError(
                f"clock went back from {self._last.isoformat()} to {reading.isoformat()}"
            )
        self._last = reading
        return reading

    def clock_precheck(self, session: date, last_ok_ingest: datetime | None) -> datetime:
        """The pre-check before a phase's first submit (module docstring); return
        the reading or raise `ClockError`. `last_ok_ingest` is the latest `ok`
        ingestion run's `finished_at` (None when there is none)."""
        if getattr(self._broker, "clock", None) is not self._clock:
            raise ClockError("the wrapper and the adapter do not hold one clock object")
        reading = self.read_clock()
        if last_ok_ingest is not None and reading < last_ok_ingest:
            raise ClockError(
                f"clock {reading.isoformat()} is earlier than the last ok ingestion "
                f"run's finish {last_ok_ingest.isoformat()}"
            )
        latest = session
        for _ in range(self._frozen.clock_max_sessions_late):
            latest = self._calendar.next_session(latest)
        bound = self._calendar.session_close(latest)
        if reading > bound:
            raise ClockError(
                f"clock {reading.isoformat()} is later than {bound.isoformat()}, "
                f"{self._frozen.clock_max_sessions_late} session(s) past close({session})"
            )
        return reading

    # --- the replay ----------------------------------------------------------------

    def replay(self, request: OrderRequest, duplicate: DuplicateClientOrderIdError) -> Order:
        """Adopt the broker's order for `request` as a replay, or raise
        `SystemFaultError` (module docstring)."""
        coid = request.client_order_id
        try:
            order = self._broker.get_order(coid)
        except Exception as exc:
            raise SystemFaultError(
                f"duplicate client_order_id {coid}: get_order failed ({type(exc).__name__})"
            ) from exc
        differing = [
            name for name in _REPLAY_FIELDS if getattr(order, name) != getattr(request, name)
        ]
        if differing:
            raise SystemFaultError(
                f"duplicate client_order_id {coid} differs from the broker's order in "
                f"{', '.join(differing)}"
            ) from duplicate
        stamp = self.read_clock()
        with self._journal() as conn:
            append(
                conn,
                OrderEventRow(
                    client_order_id=coid,
                    event_at=order.submitted_at,
                    status=_REPLAY,
                    broker_order_id=order.broker_order_id,
                    raw_json=json.dumps(asdict(order), default=str, sort_keys=True),
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
        return order

    # --- the halt path -------------------------------------------------------------

    def halt(
        self,
        fault: Exception,
        run: PaperRunRow,
        *,
        write_offs: WriteOffContext | None = None,
    ) -> NoReturn:
        """Take the halt path for `fault` raised in `run` and re-raise it (module
        docstring). `write_offs` is the run's write-off context (T58)."""
        if run.run_id is None:
            raise ValueError("halt needs a journaled run (run_id is None)")
        stale = isinstance(fault, StaleDataError)
        stamp = _HaltClock(self, fault=isinstance(fault, ClockError))
        fault_type = type(fault).__name__
        headline = self._scrub(f"{fault_type}: {fault}")
        notes: list[str] = []

        if not stale:
            engaged = switch.engage(
                self._settings,
                stamp,
                window_id=run.window_id,
                source=_FAULT,
                fault_type=fault_type,
                reason=headline,
                run_id=run.run_id,
            )
            if isinstance(engaged, switch.WriteFailed):
                self._alerter.deliver_without_store(
                    "kill_switch_write_failed",
                    f"run {run.run_id} halted on {headline}; the engaged row could not be "
                    f"written ({engaged.error})",
                )
                raise SystemExit(WRITE_FAILED_EXIT_CODE) from fault

        requested = self._acknowledged_open(run)
        for order in requested:
            self._cancel(order, stamp, notes)
        for order in requested:
            self._read(order, run, stamp, notes, write_offs)

        message = self._scrub("; ".join([headline, *dict.fromkeys(notes)]))
        try:
            self._alerter.write(
                _STALE_DATA if stale else _HALTED,
                run.run_id,
                self._session(run, stamp),
                message,
                clock_fault=stamp.fault,
            )
        except Exception as exc:
            message = f"{message}; alert not written ({type(exc).__name__})"
        finished = stamp()
        with self._journal() as conn:
            append(
                conn,
                PaperRunResultRow(
                    run_id=run.run_id,
                    finished_at=finished,
                    status=_STALE if stale else _HALTED,
                    fault_type=fault_type,
                    message=message,
                    clock_fault=stamp.fault,
                    known_at=finished,
                    ingested_at=finished,
                ),
            )
        raise fault

    def _acknowledged_open(self, run: PaperRunRow) -> list[OrderRow]:
        """The run's non-terminal orders with a broker-acknowledged event."""
        with self._journal() as conn:
            open_ = non_terminal_orders(conn, window_id=run.window_id)
            pending = {o.client_order_id for o in pending_orders(conn, window_id=run.window_id)}
        return [o for o in open_ if o.run_id == run.run_id and o.client_order_id not in pending]

    def _cancel(self, order: OrderRow, stamp: _HaltClock, notes: list[str]) -> None:
        coid = order.client_order_id
        self._event(coid, _CANCEL_REQUESTED, stamp)
        try:
            self._broker.cancel(coid)
        except Exception as exc:
            if classify(Broker.cancel.__name__, exc) is Verdict.ALLOWED:
                self._event(coid, _CANCEL_NOOP, stamp)
            else:
                self._event(coid, _CANCEL_FAILED, stamp)
                notes.append(f"cancel of {coid} failed ({type(exc).__name__})")

    def _read(
        self,
        order: OrderRow,
        run: PaperRunRow,
        stamp: _HaltClock,
        notes: list[str],
        write_offs: WriteOffContext | None,
    ) -> None:
        coid = order.client_order_id
        assert run.run_id is not None
        try:
            collected = collect(
                self._broker,
                self._journal,
                [order],
                stamp,
                _RUN,
                run.run_id,
                self._frozen,
                self._settings,
                write_offs=write_offs,
            )
        except ClockError as exc:
            # Every later read of the account-wide feed meets the same skew:
            # name the first one, then the halt simply stands (#375).
            if not any(note.startswith(_READ_CLOCK_NOTE) for note in notes):
                notes.append(f"{_READ_CLOCK_NOTE}: {exc}")
            stamp.trip()
            return
        except Exception as exc:
            self._event(coid, _CANCEL_FAILED, stamp)
            notes.append(f"halt read of {coid} failed ({type(exc).__name__})")
            return
        notes.extend(breach.message for breach in collected.rejections)

    def _event(self, coid: str, status: str, stamp: Callable[[], datetime]) -> None:
        now = stamp()
        with self._journal() as conn:
            append(
                conn,
                OrderEventRow(
                    client_order_id=coid,
                    status=status,
                    reason=HALT_REASON,
                    known_at=now,
                    ingested_at=now,
                ),
            )

    def _session(self, run: PaperRunRow, stamp: Callable[[], datetime]) -> date:
        return run.session if run.session is not None else stamp().astimezone(_NEW_YORK).date()

    def _scrub(self, text: str) -> str:
        """`text` with every configured secret masked, in any case and in its
        repr-escaped form (as `Alerter` does)."""
        secrets = secret_values(self._settings)
        forms = {form for secret in secrets for form in (secret, repr(secret)[1:-1])}
        for form in sorted(forms, key=len, reverse=True):
            text = re.sub(re.escape(form), _MASK, text, flags=re.IGNORECASE)
        return text

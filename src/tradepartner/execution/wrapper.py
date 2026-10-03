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
   through every non-store channel and the process exits non-zero with a code
   distinct from a crash's (`SystemExit(WRITE_FAILED_EXIT_CODE)`, `!=
   CRASH_EXIT_CODE`, #515) with nothing else done. The
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
4. the alert (`halted`, or `stale_data` for `StaleDataError`), run-scoped,
   after one checked clock reading (so a clock gone bad sets `clock_fault`);
5. the run's `paper_run_results` row, `halted` (or `stale`), with the fault's
   type, the message and `clock_fault`;
6. re-raises the fault.

**A store fault never cuts the halt short.** After the `engaged` row, every
write is best-effort: a failed `cancel_requested` row skips that order's cancel
call (no call without its journal row), any other failed write (an outcome
event, the alert, the result row) is noted and the halt goes on, and the
original fault is always the one re-raised, carrying every such failure as an
exception note (`add_note`), which the run's crash output (T63's `run_failed`)
reports. The switch is already engaged, so the next run halts whatever is lost.

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

**The two-phase driver** (`execute`, spec req 3, plan T60b) trades one batch:
the pending rebalance's journaled decisions, both sides, and the forced exits
handed to it, each once. Every fact is read once per phase from the journal and
from the store at close(S-1) (`live_actions_as_of`), so the ledger, the
residues, the decision states and the reference prices share one frame (#518).
`price_of` is the close(S-1) close over the split ratios with ex-date in
(S-1, S] known at close(S-1) (`planning.reference_prices`). The costs that size
the buys and the cash rule are the window's frozen `costs.*` keys
(`config.FROZEN_COSTS_KEYS`, #534), read with each phase's book; a window whose
`frozen_json` lacks them raises `ValueError` before any broker call, never
falling back to `settings.costs`. Each phase, in order:

1. its open decisions (`reattempts.attempt_scope`); a phase with none stops here;
2. the derived switch, after `switch.engage_from_overrides`: engaged ends the
   batch `skipped_kill_switch`, nothing written, nothing submitted;
3. the clock pre-check; a name with no listing at close(S-1) refused
   (`ValueError`) before any broker call; the phase's `assets` read;
4. the orders: `phases.sell_orders`, or, for buys, `phases.buy_orders` over the
   **one cash read**, max(0, `account().cash` - `reserve.open_buy_reserve`),
   never `buying_power`. The declared stub (plan T60b, development-process.md
   "Plan shape" rule 4): if T48b finds paper `cash` lags same-session proceeds,
   its amendments change this one read (`_buys_cash`), a size-S issue here;
5. every `OrderRequest` built and validated (`phases.requests_for`): a
   `ValueError` halts the phase before any request reaches the broker;
6. `risk.check_phase` against that same cash: a violation raises
   `LimitBreachError` (any `limit_breach`) or `SkipCapError` with zero submits,
   after a `missed` `rebalance_events` row with that reason when the **batch**
   held the rebalance's decisions, whichever phase breached (a
   forced-exits-only batch leaves it pending); a skip-cap halt writes no
   `decision_events` row;
7. one write chunk: the phase-time `skipped` rows, then the `orders` rows
   (`sells_in_flight_at_submit` true on a sell, `not last` on a buy) and their
   `pending` events, committed before the first submit;
8. each submit, then `get_order` every `paper.poll_interval_seconds` until the
   broker knows the order (`UnknownOrderError` meanwhile is "not yet") or
   `paper.accept_wait_seconds` passes (`AcknowledgementTimeoutError`, the
   order left `pending`); the `accepted` event carries the broker's order id.
   A duplicate id goes through `replay`.

Between the phases the sells are polled until all are terminal or
`paper.sell_wait_seconds` has passed since the open, then collected (T58); a
rejection verdict raises `RejectionCapError`. A batch with no buys (forced
exits only, or sells only) returns right after its sells' submits without
polling or collecting them, so its rejection cap relies on the run's step 7b
collection (T63d), not on this module (#534). The buys phase reads its facts
after that collection, so its scope's `last` and every buy's
`sells_in_flight_at_submit` agree. It ends by collecting its buys with a
`WriteOffContext` (T58), so a submitted buy that expires unfilled or is
rejected is `written_off` as soon as that collection returns, then appending
`reattempts.write_offs`' rows for every buy its sizing deferred (`completed`
only when it sized and submitted; a switch read engaged before its first
submit writes off no deferred buy). Every exception `execute` raises is a
fault for the caller's halt path (`halt`); `execute` writes no result row:
`BatchOutcome.status` is the run's to journal.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn, Protocol
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from pydantic import ValidationError

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    Account,
    Asset,
    Broker,
    DuplicateClientOrderIdError,
    Order,
    OrderNotOpenError,
    OrderRequest,
    UnknownOrderError,
    canonical_symbol,
)
from tradepartner.backtest.costs import Commissions
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import (
    FROZEN_COSTS_KEYS,
    CostsConfig,
    RiskConfig,
    Settings,
    secret_values,
)
from tradepartner.errors import (
    AcknowledgementTimeoutError,
    ClockError,
    LimitBreachError,
    RejectionCapError,
    SkipCapError,
    StaleDataError,
    SystemFaultError,
)
from tradepartner.execution import phases, switch
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.collect import Connect, WriteOffContext, collect
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.plan import BuyCosts, DecisionState, decision_state, residue
from tradepartner.execution.planning import current_listings, reference_prices
from tradepartner.execution.reattempts import attempt_scope, write_offs
from tradepartner.execution.reserve import open_buy_reserve
from tradepartner.execution.risk import Skip, Violations, _buy_cash, check_phase, unfilled_sells
from tradepartner.store.asof import listings_as_of, live_actions_as_of
from tradepartner.store.db import utc_now
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    RebalanceEventRow,
    adjustments_for,
    append,
    decisions_for,
    fills_for,
    kill_switch_events_for,
    non_terminal_orders,
    open_window,
    order_events_for,
    orders_for,
    pending_orders,
    positions_daily_for,
    reconciliations_for,
    runs_for,
)
from tradepartner.store.schema import HALT_REASON
from tradepartner.timeutil import ensure_tz_aware_utc

__all__ = [
    "ACCOUNT_ALLOWLIST",
    "ALLOWLISTS",
    "ASSETS_ALLOWLIST",
    "CANCEL_ALLOWLIST",
    "CRASH_EXIT_CODE",
    "FILLS_ALLOWLIST",
    "GET_ORDER_ALLOWLIST",
    "OPEN_ORDERS_ALLOWLIST",
    "POSITIONS_ALLOWLIST",
    "SUBMIT_ALLOWLIST",
    "WRITE_FAILED_EXIT_CODE",
    "BatchOutcome",
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

#: An uncaught exception's exit status (Python/typer's default). Named here so the
#: two codes are defined side by side and nothing hardcodes `1` to mean "crashed".
CRASH_EXIT_CODE = 1

#: The exit status when the `engaged` row itself cannot be written (spec req 4):
#: distinct from `CRASH_EXIT_CODE` so the launchd plist and the runbook can tell a
#: failed halt write apart from an ordinary crash (#366 Q22 (ii), #515). Also
#: distinct from `cli.USAGE_ERROR` (2), so a bad CLI flag never looks like one.
WRITE_FAILED_EXIT_CODE = 3

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
_OK = "ok"
_SKIPPED_KILL_SWITCH = "skipped_kill_switch"
_SELL = "sell"
_BUY = "buy"
_EXIT = "exit"
_PENDING = "pending"
_ACCEPTED = "accepted"
_SKIPPED = "skipped"
_WRITTEN_OFF = "written_off"
_UNFUNDED = "unfunded"
_MISSED = "missed"
_LIMIT_BREACH = "limit_breach"
_SKIP_CAP = "skip_cap"
_COSTS_PREFIX = "costs."


class Verdict(StrEnum):
    """What the wrapper does with an exception from a broker call."""

    HALT = "halt"
    ALLOWED = "allowed"
    PROPAGATE = "propagate"


class TradingCalendar(Protocol):
    """The calendar the pre-check needs (`tradepartner.calendar` satisfies it)."""

    def next_session(self, day: date) -> date: ...

    def session_open(self, day: date) -> datetime: ...

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


@dataclass(frozen=True)
class BatchOutcome:
    """What `execute` did (module docstring). `status` is `ok` or
    `skipped_kill_switch`, the run's result to journal; `submitted` the client
    order ids submitted, sells first; `skips` the phase-time skips journaled;
    `deferred` the buys the sizing deferred; `written_off` the buys written off
    at the phase's end. `cash` is the buys phase's cash (`account().cash` less
    the reserve, at least 0) and `cash_left` that cash less the modelled cost
    of every buy submitted, after `risk.check_phase`'s skips (#518), for the
    run's `unspent_cash` test; both None when no buys phase read the cash."""

    status: str
    submitted: tuple[str, ...] = ()
    skips: tuple[Skip, ...] = ()
    deferred: tuple[int, ...] = ()
    written_off: tuple[int, ...] = ()
    cash: float | None = None
    cash_left: float | None = None


@dataclass(frozen=True)
class _Book:
    """One phase's read of the journal and the store, every store fact as of
    close(S-1): the window and its frozen costs, the splits, the ledger stated
    for S, the reference prices, each decision's state and each sold name's
    residue, the tickers and ended listings of the batch's names, every own
    order, event and live fill of any window, and the last `ok` ingestion run's
    `finished_at`."""

    window: PaperWindowRow
    costs: BuyCosts
    actions: pl.DataFrame
    ledger: Ledger
    prices: Mapping[str, float]
    states: Mapping[int, DecisionState]
    residues: Mapping[str, float]
    tickers: Mapping[str, str | None]
    ended: frozenset[str]
    orders: Sequence[OrderRow]
    events: Sequence[OrderEventRow]
    fills: Sequence[OrderedFill]
    last_ok_ingest: datetime | None

    def price_of(self, security_id: str) -> float:
        """The reference price on S, or `ValueError` for a name not read."""
        return _price_lookup(self.prices)(security_id)


@dataclass(frozen=True)
class _Gated:
    """A phase past its risk check: the rows and requests to submit, in order,
    the skips journaled, the cap-counted skips so far and the orders left."""

    to_submit: tuple[tuple[OrderRow, OrderRequest], ...]
    skips: tuple[Skip, ...]
    counted: int
    left: tuple[phases.PhaseOrder, ...]


class RiskGatedBroker:
    """The only caller of `Broker.submit` and `Broker.cancel` (module docstring).

    `journal` opens a write chunk (`lambda: store.db.open_for_write(settings)`),
    `frozen` is the window's frozen `risk.*` section (the only limits it reads),
    the costs are the window's frozen `costs.*` keys (`_frozen_costs`, read with
    each phase's book, #534), and every `paper.*` and `alpaca.*` key comes from
    `settings`,
    `alerter` writes the halt's alert, `calendar` gives session opens and
    closes, and `sleep` waits between polls (tests advance a fake clock)."""

    def __init__(
        self,
        broker: Broker,
        clock: Callable[[], datetime],
        frozen: RiskConfig,
        settings: Settings,
        journal: Connect,
        calendar: TradingCalendar,
        alerter: Alerter,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._frozen = frozen
        self._settings = settings
        self._journal = journal
        self._calendar = calendar
        self._alerter = alerter
        self._sleep = sleep
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
        if last_ok_ingest is not None:
            last_ok_ingest = ensure_tz_aware_utc(last_ok_ingest, field_name="last_ok_ingest")
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
        except ClockError:
            raise  # keep the type: the halt path stamps by utc_now() after it
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

    # --- the two-phase driver -------------------------------------------------------

    def execute(
        self,
        run: PaperRunRow,
        decisions: Sequence[DecisionRow],
        forced_exits: Sequence[DecisionRow],
    ) -> BatchOutcome:
        """Trade one batch in two phases on the run's session (module docstring).

        `decisions` are the pending rebalance's journaled decisions, both sides
        (empty for a forced-exits-only batch), `forced_exits` the journaled
        forced exits handed to this batch; each decision once. Every exception
        is a fault for the caller's halt path."""
        if run.run_id is None or run.session is None:
            raise ValueError("execute needs a journaled run with its session")
        rows = [*decisions, *forced_exits]
        # The batch holds these rebalances: a halt in either phase marks them
        # `missed` (ADR 0010 point 2), whichever decisions that phase held.
        rebalance = sorted({d.rebalance_session for d in decisions if d.rebalance_session})
        book = self._read_book(run, rows)
        sold = phases.PhaseOrders((), ())
        submitted: list[str] = []
        skips: tuple[Skip, ...] = ()
        counted = 0
        if attempt_scope(rows, book.states, phase=_SELL).attempts:
            if self._engaged(run, book.window):
                return BatchOutcome(_SKIPPED_KILL_SWITCH)
            assets = self._phase_assets(run, book, rows, _SELL)
            sold = phases.sell_orders(
                decisions,
                book.states,
                book.ledger,
                book.residues,
                forced_exits,
                book.actions,
                book.price_of,
                assets,
                self._frozen,
                session=run.session,
                quantity_decimals=self._decimals(),
            )
            nobody = Account(book.window.account_id, 0.0, 0.0, 0.0, self.read_clock())
            gated = self._gate(
                run, book, sold, assets, nobody, rebalance, _SELL if decisions else _EXIT, 0, 0
            )
            # The skips ride along: `buy_orders` never buys a name this phase
            # skipped (#518 item 3).
            sold = phases.PhaseOrders(gated.left, gated.skips, session=run.session)
            skips, counted = gated.skips, gated.counted
            submitted = self._submit_all(gated.to_submit)
            if any(d.side == _BUY for d in decisions) and gated.to_submit:
                self._await_sells(run, [row for row, _ in gated.to_submit])
        if not any(d.side == _BUY for d in decisions):
            return BatchOutcome(_OK, tuple(submitted), skips)
        bought = self._buys(run, rows, rebalance, sold, len(submitted), counted)
        return BatchOutcome(
            bought.status,
            (*submitted, *bought.submitted),
            (*skips, *bought.skips),
            bought.deferred,
            bought.written_off,
            bought.cash,
            bought.cash_left,
        )

    def _buys(
        self,
        run: PaperRunRow,
        rows: Sequence[DecisionRow],
        rebalance: Sequence[date],
        sold: phases.PhaseOrders,
        prior_orders: int,
        prior_skips: int,
    ) -> BatchOutcome:
        """The buys phase, read after the sells' collection (module docstring)."""
        assert run.session is not None
        book = self._read_book(run, rows)
        scope = attempt_scope(rows, book.states, phase=_BUY)
        if not scope.attempts:
            return BatchOutcome(_OK, written_off=self._write_offs(run, rows, book, scope.last))
        if self._engaged(run, book.window):
            written = self._write_offs(run, rows, book, scope.last)
            return BatchOutcome(_SKIPPED_KILL_SWITCH, written_off=written)
        assets = self._phase_assets(run, book, rows, _BUY)
        account = self._broker.account()
        cash = self._buys_cash(account, book, run.session)
        costs = book.costs
        built = phases.buy_orders(
            rows,
            book.states,
            cash,
            sold,
            book.price_of,
            costs,
            assets,
            self._frozen,
            session=run.session,
        )
        gated = self._gate(
            run,
            book,
            built,
            assets,
            Account(account.account_id, cash, cash, account.equity, account.as_of),
            rebalance,
            _BUY,
            prior_orders,
            prior_skips,
            in_flight=not scope.last,
        )
        submitted = self._submit_all(gated.to_submit)
        collected_written_off = self._collect(
            run,
            [row for row, _ in gated.to_submit],
            write_offs=WriteOffContext(
                window_id=run.window_id,
                actions_as_of=book.actions,
                price_of=book.price_of,
                session=run.session,
            ),
        )
        after = self._read_book(run, rows)
        written = tuple(
            sorted(
                {
                    *collected_written_off,
                    *self._write_offs(
                        run, rows, after, scope.last, completed=True, deferred=built.deferred
                    ),
                }
            )
        )
        spent = sum(
            (
                _buy_cash(o.notional, o.quantity, o.price, o.whole_share, self._frozen, costs)
                for o in gated.left
            ),
            Decimal(0),
        )
        left = float(max(Decimal(repr(cash)) - spent, Decimal(0)))
        return BatchOutcome(_OK, tuple(submitted), gated.skips, built.deferred, written, cash, left)

    def _buys_cash(self, account: Account, book: _Book, session: date) -> float:
        """**The one cash read** spec req 2 may amend (the declared stub, module
        docstring): max(0, `account().cash` - the open-buy reserve), never
        `buying_power`. A `ValueError` from the reserve halts the buys phase;
        there is no fallback to a zero reserve or the full cash."""
        reserve = open_buy_reserve(
            book.orders,
            book.events,
            book.fills,
            book.actions,
            book.price_of,
            self._frozen,
            session=session,
        )
        return float(max(Decimal(repr(float(account.cash))) - reserve, Decimal(0)))

    def _gate(
        self,
        run: PaperRunRow,
        book: _Book,
        built: phases.PhaseOrders,
        assets: Mapping[str, Asset],
        account: Account,
        rebalance: Sequence[date],
        phase: str,
        prior_orders: int,
        prior_skips: int,
        *,
        in_flight: bool = True,
    ) -> _Gated:
        """Validate every request, check the batch and journal it (steps 5 to 7
        of the module docstring); raise the batch's violation after its
        `missed` row."""
        assert run.session is not None and run.run_id is not None
        session = run.session
        self._requests(built, book, session)
        candidates = [
            o.to_risk(str(book.tickers[o.security_id]), listing_ended=o.security_id in book.ended)
            for o in built.orders
        ]
        counted = prior_skips + sum(s.counts_toward_cap for s in built.skips)
        verdict = check_phase(
            candidates,
            book.ledger,
            account,
            assets,
            self._frozen,
            self._decimals(),
            price_of=book.price_of,
            costs=book.costs,
            open_sells=unfilled_sells(
                book.orders, book.events, book.fills, book.price_of, book.actions, session=session
            ),
            prior_orders=prior_orders,
            prior_skips=counted,
        )
        if isinstance(verdict, Violations):
            self._refuse(run, verdict, rebalance)
        # The verdict's orders, in its order, each with its basis: `check_phase`
        # may set `whole_share` on a buy or trim whose name is not `fractionable`
        # (#534); sizes are the built ones, which the check never changes.
        built_by_id = {o.decision_id: o for o in built.orders}
        final = phases.PhaseOrders(
            tuple(
                replace(built_by_id[o.decision_id], whole_share=o.whole_share)
                for o in verdict.orders
            ),
            (),
            session=built.session,
        )
        requests = self._requests(final, book, session)
        skips = (*built.skips, *verdict.skips)
        stamp = self.read_clock()
        pairs = []
        with self._journal() as conn:
            for skip in skips:
                append(
                    conn,
                    DecisionEventRow(
                        decision_id=skip.decision_id,
                        run_id=run.run_id,
                        status=_SKIPPED,
                        reason=skip.reason,
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
            for order, request in zip(final.orders, requests, strict=True):
                row = OrderRow(
                    client_order_id=request.client_order_id,
                    decision_id=order.decision_id,
                    run_id=run.run_id,
                    session=session,
                    attempt=int(request.client_order_id.rsplit("-", 1)[1]),
                    phase=phase,
                    security_id=order.security_id,
                    symbol=request.symbol,
                    side=order.side,
                    notional=request.notional,
                    quantity=request.quantity,
                    sells_in_flight_at_submit=True if order.side == _SELL else in_flight,
                    known_at=stamp,
                    ingested_at=stamp,
                )
                append(conn, row)
                append(
                    conn,
                    OrderEventRow(
                        client_order_id=row.client_order_id,
                        status=_PENDING,
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
                pairs.append((row, request))
        counted += sum(s.counts_toward_cap for s in verdict.skips)
        return _Gated(tuple(pairs), skips, counted, final.orders)

    def _refuse(self, run: PaperRunRow, verdict: Violations, missed: Sequence[date]) -> NoReturn:
        """Raise the batch's violation: `LimitBreachError` when any rule is a
        `limit_breach`, else `SkipCapError`, after a `missed` row for each
        rebalance in `missed` (those whose decisions the batch held, in either
        phase; a forced-exits-only batch holds none)."""
        breaches = [v for v in verdict.violations if v.kind == _LIMIT_BREACH]
        reason = _LIMIT_BREACH if breaches else _SKIP_CAP
        message = "; ".join(f"{v.rule}: {v.detail}" for v in verdict.violations)
        if missed:
            assert run.run_id is not None
            stamp = self.read_clock()
            with self._journal() as conn:
                for rebalance_session in missed:
                    append(
                        conn,
                        RebalanceEventRow(
                            rebalance_session=rebalance_session,
                            run_id=run.run_id,
                            status=_MISSED,
                            reason=reason,
                            known_at=stamp,
                            ingested_at=stamp,
                        ),
                    )
        raise (LimitBreachError if breaches else SkipCapError)(message)

    def _read_book(self, run: PaperRunRow, rows: Sequence[DecisionRow]) -> _Book:
        """One read of the journal and the store for a phase (`_Book`)."""
        assert run.session is not None
        session = run.session
        cut = session_close(previous_session(session))
        with self._journal() as conn:
            window = open_window(conn)
            if window is None or window.window_id != run.window_id:
                raise ValueError(f"run {run.run_id}'s window {run.window_id} is not the open one")
            costs = _frozen_costs(window)
            window_id = run.window_id
            actions = live_actions_as_of(conn, cut)
            ok = [
                r
                for r in reconciliations_for(conn, window_id)
                if r.status == _OK and r.at.astimezone(_NEW_YORK).date() <= session
            ]
            ledger = from_journal(
                fills_for(conn, window_id=window_id),
                orders_for(conn, window_id=window_id),
                adjustments_for(conn, window_id),
                actions,
                ok[-1] if ok else None,
                window.starting_cash,
                session,
                window_id=window_id,
                quantity_tolerance=self._frozen.reconcile_quantity_tolerance,
            )
            orders = orders_for(conn, window_id=None)
            events = order_events_for(conn, window_id=None)
            fills = fills_for(conn)
            journaled = decisions_for(conn, window_id)
            terminal = {e.client_order_id for e in events if e.status in TERMINAL_ORDER_STATUSES}
            names = (
                {d.security_id for d in rows}
                | set(ledger.positions)
                | {o.security_id for o in orders if o.client_order_id not in terminal}
            )
            prices = reference_prices(conn, session, names, actions)
            tickers, ended = _listings(
                conn, session, sorted({d.security_id for d in rows}), self._settings
            )
            residue_rows = (
                adjustments_for(conn, window_id),
                positions_daily_for(conn, window_id),
                [r.run for r in runs_for(conn, window_id)],
            )
            last_ok_ingest = _last_ok_ingest(conn)
        events_of = {d.decision.decision_id: d.events for d in journaled}
        price_of = _price_lookup(prices)
        states: dict[int, DecisionState] = {}
        for row in rows:
            if row.decision_id is None or row.decision_id not in events_of:
                raise ValueError(
                    f"decision {row.decision_id} is not journaled in window {window_id}"
                )
            states[row.decision_id] = decision_state(
                row,
                events_of[row.decision_id],
                orders,
                events,
                fills,
                actions,
                price_of,
                self._frozen,
                session=session,
            )
        adjustments, marks, runs = residue_rows
        all_events = [e for d in journaled for e in d.events]
        residues = {
            sid: residue(
                sid,
                adjustments,
                [d.decision for d in journaled],
                all_events,
                marks,
                ledger,
                actions,
                window_id=window_id,
                runs=runs,
            )
            for sid in sorted({d.security_id for d in rows if d.side == _SELL})
        }
        return _Book(
            window,
            costs,
            actions,
            ledger,
            prices,
            states,
            residues,
            tickers,
            ended,
            orders,
            events,
            fills,
            last_ok_ingest,
        )

    def _engaged(self, run: PaperRunRow, window: PaperWindowRow) -> bool:
        """Engage any unconsumed `engage_kill_switch` override, then read the
        derived switch (T59) as the run reading it."""
        engaged = switch.engage_from_overrides(
            self._settings, self.read_clock, window_id=run.window_id, run_id=run.run_id
        )
        if isinstance(engaged, switch.WriteFailed):
            self._alerter.deliver_without_store(
                "kill_switch_write_failed",
                self._scrub(
                    f"run {run.run_id}: an engage_kill_switch override could not be engaged "
                    f"({engaged.error})"
                ),
            )
            raise SystemExit(WRITE_FAILED_EXIT_CODE)
        with self._journal() as conn:
            rows = kill_switch_events_for(conn, run.window_id)
            runs = runs_for(conn, run.window_id)
        state = switch.derive(
            window,
            rows,
            [r.run for r in runs],
            [r.result for r in runs if r.result is not None],
            reading_run=run.run_id,
            lock_free=True,
        )
        return state.engaged

    def _phase_assets(
        self, run: PaperRunRow, book: _Book, rows: Sequence[DecisionRow], side: str
    ) -> dict[str, Asset]:
        """The clock pre-check, the refusal of a name with no listing at
        close(S-1) (before any broker call), then the phase's `assets` read
        for its open decisions, keyed by `security_id`."""
        assert run.session is not None
        self.clock_precheck(run.session, book.last_ok_ingest)
        names = sorted(
            {a.decision.security_id for a in attempt_scope(rows, book.states, phase=side).attempts}
        )
        unknown = [sid for sid in names if not book.tickers.get(sid)]
        if unknown:
            raise ValueError(f"no listing known at close(S-1) for {unknown}")
        symbols = {sid: canonical_symbol(str(book.tickers[sid])) for sid in names}
        answer = self._broker.assets(sorted(symbols.values()))
        missing = sorted(sym for sym in symbols.values() if sym not in answer)
        if missing:
            raise ValueError(f"the assets read did not answer for {missing}")
        return {sid: answer[symbol] for sid, symbol in symbols.items()}

    def _decimals(self) -> int:
        decimals = self._settings.alpaca.quantity_decimals
        if decimals is None:
            raise ValueError("alpaca.quantity_decimals is unset (T48b records it)")
        return decimals

    def _requests(
        self, built: phases.PhaseOrders, book: _Book, session: date
    ) -> list[OrderRequest]:
        return phases.requests_for(
            built,
            book.tickers,
            self._settings.paper.order_id_prefix,
            [o for o in book.orders if o.session == session],
            self._settings.alpaca.client_order_id_max_length,
            session=session,
        )

    def _submit_all(self, pairs: Sequence[tuple[OrderRow, OrderRequest]]) -> list[str]:
        """Submit each journaled request in order and await its acknowledgement."""
        submitted = []
        for _, request in pairs:
            try:
                self._broker.submit(request)
            except DuplicateClientOrderIdError as duplicate:
                self.replay(request, duplicate)
            else:
                self._acknowledge(request.client_order_id)
            submitted.append(request.client_order_id)
        return submitted

    def _acknowledge(self, coid: str) -> None:
        """Poll `get_order` until the broker knows the order, then journal its
        `accepted` event; `AcknowledgementTimeoutError` at the deadline."""
        paper = self._settings.paper
        deadline = self.read_clock() + timedelta(seconds=paper.accept_wait_seconds)
        while True:
            try:
                order = self._broker.get_order(coid)
            except UnknownOrderError:
                now = self.read_clock()
                if now >= deadline:
                    raise AcknowledgementTimeoutError(
                        f"order {coid} not acknowledged within {paper.accept_wait_seconds}s"
                    ) from None
                self._sleep(min(paper.poll_interval_seconds, (deadline - now).total_seconds()))
                continue
            stamp = self.read_clock()
            with self._journal() as conn:
                append(
                    conn,
                    OrderEventRow(
                        client_order_id=coid,
                        event_at=order.submitted_at,
                        status=_ACCEPTED,
                        broker_order_id=order.broker_order_id,
                        raw_json=json.dumps(asdict(order), default=str, sort_keys=True),
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
            return

    def _await_sells(self, run: PaperRunRow, sells: Sequence[OrderRow]) -> None:
        """Poll this run's sells until each is terminal or
        `paper.sell_wait_seconds` has passed since the open, then collect them."""
        assert run.session is not None
        paper = self._settings.paper
        deadline = self._calendar.session_open(run.session) + timedelta(
            seconds=paper.sell_wait_seconds
        )
        waiting = [row.client_order_id for row in sells]
        while waiting:
            waiting = [
                c for c in waiting if self._broker.get_order(c).status not in TERMINAL_STATUSES
            ]
            now = self.read_clock()
            if not waiting or now >= deadline:
                break
            self._sleep(min(paper.poll_interval_seconds, (deadline - now).total_seconds()))
        self._collect(run, sells)

    def _collect(
        self,
        run: PaperRunRow,
        orders: Sequence[OrderRow],
        *,
        write_offs: WriteOffContext | None = None,
    ) -> tuple[int, ...]:
        """Collect this phase's orders (T58); a rejection verdict raises
        `RejectionCapError` naming the submitting run. `write_offs`, when given,
        back-fills the `written_off` row of a terminal buy of a pending
        rebalance that lacks one, visible as soon as this collection returns
        (spec "Two phases": a submitted buy that expires unfilled or is
        rejected is written off at collection, not at the phase's end); the
        decision ids written off that way are returned."""
        if not orders:
            return ()
        assert run.run_id is not None
        collected = collect(
            self._broker,
            self._journal,
            orders,
            self.read_clock,
            _RUN,
            run.run_id,
            self._frozen,
            self._settings,
            write_offs=write_offs,
        )
        if collected.rejections:
            raise RejectionCapError("; ".join(b.message for b in collected.rejections))
        return collected.written_off

    def _write_offs(
        self,
        run: PaperRunRow,
        rows: Sequence[DecisionRow],
        book: _Book,
        last: bool,
        *,
        completed: bool = False,
        deferred: Sequence[int] = (),
    ) -> tuple[int, ...]:
        """Append the buys phase's `written_off` rows (`reattempts.write_offs`)."""
        assert run.run_id is not None
        written = write_offs(
            rows, book.states, phase=_BUY, last=last, completed=completed, deferred=deferred
        )
        if not written:
            return ()
        stamp = self.read_clock()
        with self._journal() as conn:
            for row in written:
                append(
                    conn,
                    DecisionEventRow(
                        decision_id=row.decision_id,
                        run_id=run.run_id,
                        status=_WRITTEN_OFF,
                        reason=_UNFUNDED,
                        unfunded_notional=row.unfunded_notional,
                        known_at=stamp,
                        ingested_at=stamp,
                    ),
                )
        return tuple(row.decision_id for row in written)

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

        try:
            requested = self._acknowledged_open(run)
        except Exception as exc:
            requested = []
            notes.append(f"open orders not read, no cancel attempted ({type(exc).__name__})")
        cancelled = [order for order in requested if self._cancel(order, stamp, notes)]
        for order in cancelled:
            self._read(order, run, stamp, notes, write_offs)

        stamp()  # one checked reading first, so a clock gone bad since sets clock_fault
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
            notes.append(f"alert not written ({type(exc).__name__})")
            message = self._scrub("; ".join([headline, *dict.fromkeys(notes)]))
        finished = stamp()
        try:
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
        except Exception as exc:
            notes.append(f"result row not written ({type(exc).__name__})")
        if notes:
            fault.add_note(self._scrub("halt path: " + "; ".join(dict.fromkeys(notes))))
        raise fault

    def _acknowledged_open(self, run: PaperRunRow) -> list[OrderRow]:
        """The run's non-terminal orders with a broker-acknowledged event."""
        with self._journal() as conn:
            open_ = non_terminal_orders(conn, window_id=run.window_id)
            pending = {o.client_order_id for o in pending_orders(conn, window_id=run.window_id)}
        return [o for o in open_ if o.run_id == run.run_id and o.client_order_id not in pending]

    def _cancel(self, order: OrderRow, stamp: _HaltClock, notes: list[str]) -> bool:
        """Journal `cancel_requested`, call `cancel`, journal its outcome; return
        whether the cancel was requested. No call without its journal row."""
        coid = order.client_order_id
        if not self._event(coid, _CANCEL_REQUESTED, stamp, notes):
            notes.append(f"cancel of {coid} not attempted: its cancel_requested row failed")
            return False
        try:
            self._broker.cancel(coid)
        except Exception as exc:
            if classify(Broker.cancel.__name__, exc) is Verdict.ALLOWED:
                self._event(coid, _CANCEL_NOOP, stamp, notes)
            else:
                self._event(coid, _CANCEL_FAILED, stamp, notes)
                notes.append(f"cancel of {coid} failed ({type(exc).__name__})")
        return True

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
            self._event(coid, _CANCEL_FAILED, stamp, notes)
            notes.append(f"halt read of {coid} failed ({type(exc).__name__})")
            return
        notes.extend(breach.message for breach in collected.rejections)

    def _event(
        self, coid: str, status: str, stamp: Callable[[], datetime], notes: list[str]
    ) -> bool:
        """Journal a halt cancel event; on a store error note it and return False
        (the halt goes on: a store fault never cuts the halt path short)."""
        now = stamp()
        try:
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
        except Exception as exc:
            notes.append(f"{status} of {coid} not journaled ({type(exc).__name__})")
            return False
        return True

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


def _frozen_costs(window: PaperWindowRow) -> BuyCosts:
    """The window's frozen costs: `FROZEN_COSTS_KEYS` under `costs.` in
    `frozen_json` (spec req 14, #534, owner), never live `Settings`. A window
    whose `frozen_json` lacks one of them (started before #534), or holds a
    value `CostsConfig` refuses, raises `ValueError`: there is no fallback to
    `settings.costs`."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    keys = [f"{_COSTS_PREFIX}{k}" for k in FROZEN_COSTS_KEYS]
    missing = [k for k in keys if k not in parsed]
    if missing:
        raise ValueError(
            f"window {window.window_id} frozen_json lacks cost keys {missing} (a window "
            "started before #534 froze no costs; stop it and start a new one)"
        )
    try:
        costs = CostsConfig.model_validate(
            {k: parsed[f"{_COSTS_PREFIX}{k}"] for k in FROZEN_COSTS_KEYS}
        )
    except ValidationError as exc:
        raise ValueError(f"window {window.window_id} frozen costs: {exc}") from exc
    return BuyCosts(costs.per_side_bps, Commissions.from_config(costs))


def _price_lookup(prices: Mapping[str, float]) -> Callable[[str], float]:
    def price_of(security_id: str) -> float:
        if security_id not in prices:
            raise ValueError(f"no reference price read for {security_id}")
        return prices[security_id]

    return price_of


def _listings(
    conn: duckdb.DuckDBPyConnection, session: date, names: Sequence[str], settings: Settings
) -> tuple[dict[str, str | None], frozenset[str]]:
    """Each name's ticker (its current listing at S among rows known at
    close(S-1); None without one) and the names whose listing ended
    (`delisted`) at close(S-1)."""
    if not names:
        return {}, frozenset()
    cut = session_close(previous_session(session))
    current = current_listings(listings_as_of(conn, cut, list(names)), session)
    tickers = {sid: (str(current[sid]["ticker"]) if sid in current else None) for sid in names}
    ends = current_listings(
        listing_ends_as_of(conn, cut, settings, list(names)), previous_session(session)
    )
    return tickers, frozenset(sid for sid, row in ends.items() if row["status"] == DELISTED)


def _last_ok_ingest(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The latest `ok` ingestion run's `finished_at` (the clock pre-check's floor)."""
    row = conn.execute(
        "SELECT max(finished_at) FROM ingestion_runs WHERE status = ?", [_OK]
    ).fetchone()
    return None if row is None else row[0]

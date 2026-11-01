"""The owner true-up of one reconciliation break (paper spec amendment 2026-10-26,
#2266, item 9; ADR 0025; plan T283b).

`paper true-up --book <b> --break <id> --reason "<text>"` adopts the broker's
figures for one open break, on the owner's word, modelled on `paper settle`
(req 17). It runs whether or not the derived kill switch is engaged, so a
`break_cap` halt can be cleared before `paper resume`.

**Gate**, in order, each refusal a `WindowCommandRefused` that writes nothing:

1. `reason`: `--reason` missing or blank (there is no default);
2. the run lock (`LockHeld` propagates: `locked`);
3. `no_window`: no window is open for the book;
4. `not_open`: the break is not open in that window (closed, or never written);
5. one clock reading, then the fresh broker read: `account()`, `positions()` and
   `cash_activities` (since the day before the window's New York start date, as
   reconciliation reads them). `account_mismatch` when the account is not the
   window's;
6. `open_order`: a non-terminal order of the window trades the name, so an
   in-flight fill is never adopted;
7. `short_name`: the name is held short on either book. The ledger's true-up
   replay states a long position (`execution.ledger`), so a signed name waits
   for its own rule rather than being adopted wrongly (fail closed);
8. `moved`: the name's broker or ledger quantity has moved from the break's
   latest event by more than `risk.reconcile_quantity_tolerance`, its cash
   activities' total by more than `risk.reconcile_cash_tolerance`, an activity
   of the break is gone, or a new unseen activity is tagged to the name. Run
   `paper reconcile` first, so the size accepted is the size written.

**What it writes.** One write chunk, stamped by one clock reading taken after
the broker read (`known_at = ingested_at`, later than every reading and every
journal row of the window), holding the `owner_true_up` adjustments and one
`trued_up` event naming them and the reason. Every adjustment's `session` is the
command's session, never back-dated, and its `explanation_json` holds the break
id, the reason, and the broker and ledger figures read:

- a quantity part: one row whose `quantity` is the delta (broker minus ledger)
  and whose `explanation_json` carries the broker's absolute `broker_quantity`,
  which the ledger states the name at from then on;
- a cash part: one row per activity id, its `cash` the activity's net amount and
  its id in the `UNIQUE` `broker_activity_id`, so each activity is booked once;
- a finding part (`unknown_symbol`, `merger_conflict`,
  `unexplained_dividend_group`): acknowledged, not adopted. Its row carries no
  quantity and no cash and records the `finding_key`; reconciliation does not
  reopen a break while `compare` raises the same key
  (`reconcile_run.acknowledged_keys`).

No LLM output reaches any of it (ADR 0008): the reason is journaled and never
parsed. There is no `--all`, pattern, `--force` or `--size` flag, and nothing
skips the broker read. The reversal (`--reverse`) is not built here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb

from tradepartner.adapters.broker import Broker, CashActivity
from tradepartner.config import Settings
from tradepartner.errors import ClockError
from tradepartner.execution import breaks as break_rules
from tradepartner.execution.lock import resolve_book, run_lock
from tradepartner.execution.reconcile_run import (
    _NEW_YORK,
    _cut,
    _journal_state,
    _ledger,
    _read_clock,
    _seen_activities,
    _stated_through,
    _window_id,
    command_session,
    frozen_risk,
)
from tradepartner.execution.window import (
    ACCOUNT_MISMATCH,
    NO_WINDOW,
    REASON,
    WindowCommandRefused,
)
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.journal import (
    AdjustmentRow,
    PaperWindowRow,
    ReconcileBreakRow,
    adjustments_for,
    append,
    break_events_for,
    fills_for,
    non_terminal_orders,
    open_breaks,
    open_window,
    orders_for,
    reconciliations_for,
)
from tradepartner.store.schema import OWNER_TRUE_UP_KIND

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

#: Refusal codes (module docstring); `reason`, `no_window` and `account_mismatch`
#: are `execution.window`'s, so the CLI maps every refusal the same way.
NOT_OPEN = "not_open"
OPEN_ORDER = "open_order"
SHORT_NAME = "short_name"
MOVED = "moved"
#: The parts a true-up adopts a quantity for (item 2's quantity part).
_QUANTITY_KINDS = frozenset(
    {
        "quantity",
        "ledger_only_position",
        "broker_only_position",
        "unknown_security",
        break_rules.SHORT_EXTERNAL_CHANGE,
    }
)
_EXECUTED = "executed"


@dataclass(frozen=True)
class TrueUpResult:
    """What `true_up` wrote: the break, its `trued_up` event, the adjustments in
    write order, and the one stamp they share."""

    break_id: str
    event_id: int
    adjustment_ids: tuple[int, ...]
    known_at: datetime


@dataclass(frozen=True)
class _Reading:
    """The figures the gate read for the break's name."""

    ledger_quantity: float
    broker_quantity: float
    cash_parts: tuple[tuple[str, float], ...]


def _refuse(code: str, message: str) -> WindowCommandRefused:
    return WindowCommandRefused(code, message)


def _gate_journal(
    conn: duckdb.DuckDBPyConnection, book: str, break_id: str
) -> tuple[PaperWindowRow, ReconcileBreakRow]:
    window = open_window(conn, book)
    if window is None:
        raise _refuse(NO_WINDOW, f"no paper window is open for book {book!r}")
    latest = {e.break_id: e for e in open_breaks(conn, _window_id(window))}.get(break_id)
    if latest is None:
        raise _refuse(
            NOT_OPEN,
            f"break {break_id!r} is not open in window {window.window_id} "
            "(closed, or never written): `paper status --book` lists the open breaks",
        )
    return window, latest


def _latest_stamp(conn: duckdb.DuckDBPyConnection, window_id: int) -> datetime | None:
    """The latest `known_at` of the window's journal rows a true-up must follow."""
    stamps = (
        [f.fill.known_at for f in fills_for(conn, window_id=window_id)]
        + [o.known_at for o in orders_for(conn, window_id=window_id)]
        + [a.known_at for a in adjustments_for(conn, window_id)]
        + [r.known_at for r in reconciliations_for(conn, window_id)]
        + [e.known_at for e in break_events_for(conn, window_id)]
    )
    return max(stamps, default=None)


def _activity_ids(event: ReconcileBreakRow) -> list[str]:
    return sorted(
        {
            str(activity_id)
            for part in json.loads(event.parts_json)
            if part["kind"] == break_rules.NAME_CASH
            for activity_id in part.get("activity_ids", ())
        }
    )


def _new_activities(
    activities: Sequence[CashActivity],
    *,
    symbol: str | None,
    listed: set[str],
    seen: frozenset[str],
    seen_before: date | None,
) -> list[str]:
    """Activities tagged to the name that reconciliation would attribute and the
    break does not list: the broker's cash on the name moved since the event."""
    if symbol is None:
        return []
    return sorted(
        a.activity_id
        for a in activities
        if a.symbol == symbol
        and a.status == _EXECUTED
        and a.net_amount is not None
        and a.net_amount != 0
        and a.activity_id not in listed
        and a.activity_id not in seen
        and (seen_before is None or a.date >= seen_before)
    )


def _read(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    event: ReconcileBreakRow,
    *,
    now: datetime,
    positions: Mapping[str, float],
    activities: Sequence[CashActivity],
) -> _Reading:
    """Run the journal and broker gates (module docstring, 6 to 8) and return the
    figures to write. Pure over the reads but for the journal reads on `conn`."""
    window_id = _window_id(window)
    frozen = frozen_risk(window)
    name = event.name
    traded = sorted(
        o.client_order_id
        for o in non_terminal_orders(conn, window_id=window_id)
        if (event.security_id is not None and o.security_id == event.security_id)
        or (event.symbol is not None and o.symbol == event.symbol)
    )
    if traded:
        raise _refuse(
            OPEN_ORDER,
            f"non-terminal order(s) {', '.join(traded)} trade {name}: an in-flight fill "
            "is never adopted; wait for the next run to collect them",
        )
    latest = _latest_stamp(conn, window_id)
    if latest is not None and latest > now:
        raise ClockError(
            f"the clock reading {now.isoformat()} is before a journal row stamped "
            f"{latest.isoformat()}"
        )
    session = command_session(now)
    through = _stated_through(session, now)
    state = _journal_state(conn, window_id, now)
    ledger = _ledger(
        state,
        live_actions_as_of(conn, _cut(session)),
        window,
        through,
        frozen.reconcile_quantity_tolerance,
    )
    ledger_quantity = 0.0
    if event.security_id is not None:
        ledger_quantity = ledger.positions.get(event.security_id, 0.0) - (
            ledger.short_holdings.get(event.security_id, 0.0)
        )
    broker_quantity = positions.get(event.symbol, 0.0) if event.symbol is not None else 0.0
    if (
        ledger_quantity < 0
        or broker_quantity < 0
        or (event.security_id is not None and event.security_id in ledger.short_holdings)
    ):
        raise _refuse(
            SHORT_NAME,
            f"{name} is held short (ledger {ledger_quantity:g}, broker {broker_quantity:g}): "
            "a short true-up is not built; `paper abandon --reason` closes the window",
        )
    tolerance = frozen.reconcile_quantity_tolerance
    for what, fresh, recorded in (
        ("broker", broker_quantity, event.broker_quantity),
        ("ledger", ledger_quantity, event.ledger_quantity),
    ):
        if abs(fresh - (recorded or 0.0)) > tolerance:
            raise _refuse(
                MOVED,
                f"the {what} quantity of {name} is {fresh:g}, not the break's "
                f"{recorded or 0.0:g}: run `paper reconcile` first",
            )
    by_id = {a.activity_id: a for a in activities}
    ids = _activity_ids(event)
    gone = [i for i in ids if i not in by_id or by_id[i].net_amount is None]
    if gone:
        raise _refuse(
            MOVED, f"the broker no longer lists activity {', '.join(gone)}: run `paper reconcile`"
        )
    cash_parts = tuple((i, float(by_id[i].net_amount or 0.0)) for i in ids)
    total = math.fsum(amount for _, amount in cash_parts)
    if abs(total - (event.cash_amount or 0.0)) > frozen.reconcile_cash_tolerance:
        raise _refuse(
            MOVED,
            f"the cash of {name}'s activities is {total:.2f}, not the break's "
            f"{event.cash_amount or 0.0:.2f}: run `paper reconcile` first",
        )
    seen, seen_before = _seen_activities(state.adjustments, state.base(through), window)
    added = _new_activities(
        activities, symbol=event.symbol, listed=set(ids), seen=seen, seen_before=seen_before
    )
    if added:
        raise _refuse(
            MOVED,
            f"new broker activity {', '.join(added)} on {name} is not in the break: "
            "run `paper reconcile` first",
        )
    return _Reading(ledger_quantity, broker_quantity, cash_parts)


def _rows(
    window: PaperWindowRow,
    event: ReconcileBreakRow,
    reading: _Reading,
    *,
    account_id: str,
    reason: str,
    session: date,
    stamp: datetime,
) -> list[AdjustmentRow]:
    """The `owner_true_up` rows (module docstring), quantity part first."""
    parts = json.loads(event.parts_json)
    terms: dict[str, Any] = {
        "break_id": event.break_id,
        "reason": reason,
        "account_id": account_id,
        "symbol": event.symbol,
        "broker_quantity": reading.broker_quantity,
        "ledger_quantity": reading.ledger_quantity,
    }

    def row(
        text: str,
        extra: Mapping[str, Any],
        *,
        quantity: float | None = None,
        cash: float | None = None,
        activity_id: str | None = None,
    ) -> AdjustmentRow:
        return AdjustmentRow(
            window_id=_window_id(window),
            session=session,
            kind=OWNER_TRUE_UP_KIND,
            security_id=event.security_id,
            quantity=quantity,
            cash=cash,
            explanation_json=json.dumps(
                {"explanation": text, **terms, **extra}, sort_keys=True, allow_nan=False
            ),
            book_id=window.book_id,
            broker_activity_id=activity_id,
            known_at=stamp,
            ingested_at=stamp,
        )

    rows: list[AdjustmentRow] = []
    if event.security_id is not None and any(p["kind"] in _QUANTITY_KINDS for p in parts):
        delta = Decimal(repr(reading.broker_quantity)) - Decimal(repr(reading.ledger_quantity))
        rows.append(row("owner true-up of a quantity break", {}, quantity=float(delta)))
    for activity_id, amount in reading.cash_parts:
        rows.append(
            row(
                "owner true-up of a name cash break",
                {"activity_id": activity_id},
                cash=amount,
                activity_id=activity_id,
            )
        )
    for key in sorted(
        str(p["finding_key"]) for p in parts if p["kind"] in break_rules.FINDING_KINDS
    ):
        rows.append(row("owner acknowledgement of a finding", {"finding_key": key}))
    return rows


def true_up(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    break_id: str,
    reason: str,
    *,
    book_id: str | None = None,
) -> TrueUpResult:
    """`paper true-up --book <b> --break <id> --reason` (module docstring).

    Raises `WindowCommandRefused` for each gate (nothing written), `LockHeld`
    while another process holds the book's lock, `ClockError` for a bad clock
    reading, a stamp not after the gate's reading, or a journal row stamped after
    either, and whatever the broker raises (nothing written). The only caller is
    the owner's CLI; no flag skips the broker read."""
    if reason is None or not reason.strip():
        raise _refuse(REASON, "--reason is required and must be non-blank: say why")
    note = reason.strip()
    book = resolve_book(settings, book_id)
    with run_lock(settings, book):
        with connect() as conn:
            window, event = _gate_journal(conn, book, break_id)
        now = _read_clock(clock)
        account = broker.account()
        if account.account_id != window.account_id:
            raise _refuse(
                ACCOUNT_MISMATCH,
                f"the broker's account {account.account_id!r} is not window "
                f"{window.window_id}'s {window.account_id!r}",
            )
        positions = {symbol: p.quantity for symbol, p in broker.positions().items()}
        activities = broker.cash_activities(
            window.started_at.astimezone(_NEW_YORK).date() - timedelta(days=1)
        )
        with connect() as conn:
            reading = _read(
                conn, window, event, now=now, positions=positions, activities=activities
            )

        stamp = _read_clock(clock)
        if stamp <= now:
            raise ClockError(
                f"the true-up stamp {stamp.isoformat()} is not after the gate's reading "
                f"{now.isoformat()}"
            )
        session = command_session(stamp)
        with connect() as conn:
            # Nothing else writes under the lock, but re-read: the event must still
            # be the break's latest, and no journal row may be stamped after us.
            _, again = _gate_journal(conn, book, break_id)
            if again.event_id != event.event_id:
                raise _refuse(
                    MOVED, f"break {break_id} has a newer event: run `paper true-up` again"
                )
            latest = _latest_stamp(conn, _window_id(window))
            if latest is not None and stamp <= latest:
                raise ClockError(
                    f"the true-up stamp {stamp.isoformat()} is not after the journal row "
                    f"stamped {latest.isoformat()}"
                )
            ids: list[int] = []
            for adjustment in _rows(
                window,
                event,
                reading,
                account_id=account.account_id,
                reason=note,
                session=session,
                stamp=stamp,
            ):
                adjustment_id = append(conn, adjustment)
                if adjustment_id is None:
                    raise RuntimeError("owner_true_up append returned no id")
                ids.append(adjustment_id)
            event_id = append(
                conn,
                replace(
                    event,
                    event_id=None,
                    event="trued_up",
                    ledger_quantity=reading.ledger_quantity,
                    broker_quantity=reading.broker_quantity,
                    reconciliation_id=None,
                    adjustment_ids_json=json.dumps(ids),
                    reason=note,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
            if event_id is None:
                raise RuntimeError("trued_up append returned no id")
    return TrueUpResult(break_id, event_id, tuple(ids), stamp)

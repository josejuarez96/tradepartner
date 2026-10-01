"""Reconciliation against the store and the broker (Phase 4 spec req 6; plan T61).

`reconcile.compare` (T55) is pure. This module gathers its inputs, writes what
it decides and raises on a mismatch. A run's steps 4 and 8, `paper reconcile`,
`paper stop` and `paper resume` all use the same `reconcile_now`.

**`reconcile_now`**, in order:

1. Read the window's non-terminal orders from the journal, and which of them
   are `pending` (never acknowledged), with the live fills of each.
2. Read the broker: `positions()`, `open_orders()`, `account()`, and one
   `get_order` per acknowledged non-terminal order, the `lagging` input. A
   `pending` order is never read: only `paper resume` settles it (req 4).
3. Build the ledger stated through S, or through the clock's New York date
   while no session has opened since S (a weekend `paper reconcile`, so its
   own earlier `ok` row stays the cash base), from the journal rows known at
   the caller's `as_of` (below) with splits from
   `live_actions_as_of(close(S-1))`, and the explanations
   (`explanations_as_of`, at the same `as_of`), then call `compare`.
4. In one write chunk, append the `reconciliations` row and the `adjustments`
   rows an `ok` result's explanations imply. Each row is stamped with the
   same clock reading, so every adjustment is inside the reconciliation that
   re-bases the ledger's cash (`execution.ledger`: a row whose `known_at`
   equals the base's is counted in it, never twice).
5. On `mismatch`, raise `ReconciliationError` after the row is committed.
   Engaging the switch and alerting belong to the caller: the run's halt
   path (T60) for steps 4 and 8, `reconcile_command` for `paper reconcile`.

**The journal cut (#488).** Every fill, order, adjustment and `ok`
reconciliation the ledger and the explanations read is cut at one explicit
`as_of` instant: a row counts only when its `known_at <= as_of`. Store facts
(listings, delistings, actions, bars) are always cut at close(S-1), whatever
`as_of` is. So the result depends on (S, `as_of`) and on nothing journaled
after `as_of`, whenever it is called. The caller chooses `as_of` and always
passes it: `paper reconcile` and `paper resume` pass a clock reading taken
just before the call (every row they and earlier runs journaled), and a
run's post-trade read (step 8) passes its own clock reading, so its own
fills on S are explained. `as_of` may not be later than `reconcile_now`'s
clock reading. The open-orders and `lagging` inputs (step 1) are the
journal's current state, which the broker's current open orders are compared
with.

**`explanations_as_of`** is the read-only half (T63g's no-look-ahead suite
calls it on a truncated store). Every store fact it reads is from rows with
`known_at <= close(S-1)`, and every journal row from rows with `known_at <=
as_of`:

- `symbols`: each name the window's journal holds (fills, orders,
  adjustments) maps to the ticker of its current listing at S, meaning the
  listing with the latest `valid_from <= S`. A name with no listing falls
  back to its latest order's symbol. Each broker symbol no journal name
  claims maps to the one security whose current listing has that ticker and
  is not delisted, and stays unmapped (compare's `unknown_symbol`) when
  there is none or more than one. When two journal names share a ticker,
  only the one not delisted keeps it; when that does not settle it, neither
  does, and a held one becomes compare's `unknown_security`.
- `ended`: journal names whose current listing is `delisted` at close(S-1)
  (`store.delistings.listing_ends_as_of`; a transfer is not an end).
- `dividends`: per held name, the cash due from `dividend` actions with an
  ex-date after the cash base's session and on or before S. The base is the
  window's latest `ok` reconciliation stated on or before S, or the window's
  start. The cash due is the amount per share times the ledger's holding at
  the session before the ex-date. A dividend the journal already holds as a
  `dividend_cash` row dated on or after its ex-date is never offered again.
- `spinoffs`: always empty. The store keeps only splits and cash dividends
  (`adapters.alpaca_prices` reports spin-offs and does not store them), so
  no row can name a spin-off child. A child the broker books is an
  `unknown_symbol` or `broker_only_position` mismatch until the store can
  hold one.
- `reference_prices`: the raw close of the latest bar at or before S-1 per
  name, which bounds an ended name's proceeds and a pending order's reach.

**`reconcile_command`** is `paper reconcile`. It takes the run lock (T59),
refuses with `NoWindowError` when no window is open (no broker call, no
write), and runs one `reconcile_now` for the clock's session (the latest
XNYS session on or before the clock's New York date), with the window's
frozen `risk` section. A mismatch is a system fault (spec Definitions), so it
appends the `engaged` row (source `fault`, fault type
`ReconciliationError`) before re-raising. It writes no alert: a
`reconciliation` alert is run-scoped (`execution.alerts`) and this command
has no run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from pydantic import ValidationError

from tradepartner.adapters.broker import Account, Broker, Order, Position
from tradepartner.calendar import is_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import switch
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.lock import run_lock
from tradepartner.execution.reconcile import (
    MISMATCH,
    OK,
    Explanations,
    JournalOpenOrder,
    Reconciliation,
    compare,
)
from tradepartner.store.asof import live_actions_as_of, prices_as_of
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.journal import (
    AdjustmentRow,
    OrderedFill,
    OrderRow,
    PaperWindowRow,
    ReconciliationRow,
    adjustments_for,
    append,
    fills_for,
    non_terminal_orders,
    open_window,
    orders_for,
    pending_orders,
    reconciliations_for,
)
from tradepartner.timeutil import ensure_tz_aware_utc

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_NEW_YORK = ZoneInfo("America/New_York")
_DIVIDEND = "dividend"
_DIVIDEND_CASH = "dividend_cash"
_RISK_PREFIX = "risk."
_FAULT_TYPE = ReconciliationError.__name__


class NoWindowError(RuntimeError):
    """`paper reconcile` with no open window (spec acceptance "`paper run`
    before any `paper start`"): refused, nothing written."""


@dataclass(frozen=True)
class _JournalState:
    """The window's journal rows a reconciliation reads."""

    fills: list[OrderedFill]
    orders: list[OrderRow]
    adjustments: list[AdjustmentRow]
    ok_rows: list[ReconciliationRow]

    def base(self, through: date) -> ReconciliationRow | None:
        """The latest `ok` reconciliation stated on or before `through`: the
        ledger's cash base for that session. A later `ok` row (a weekend
        `paper reconcile` stated for Friday) cannot state `through`."""
        stated = [r for r in self.ok_rows if r.at.astimezone(_NEW_YORK).date() <= through]
        return stated[-1] if stated else None


def frozen_risk(window: PaperWindowRow) -> RiskConfig:
    """The window's frozen `risk` section from `frozen_json`.

    The spec has `paper start` canonicalise the frozen values "as the registry
    does for hypothesis parameters": one flat JSON object with dotted keys
    (`"risk.max_drawdown"`, `"paper.tracking_k"`, ...). Every `RiskConfig` field
    must be present under its `risk.` key, because the whole section is frozen.
    An unknown `risk.` key, a value the model refuses, or anything other than
    that object raises `ValueError`. Other keys are not read here."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    risk = {k[len(_RISK_PREFIX) :]: v for k, v in parsed.items() if k.startswith(_RISK_PREFIX)}
    missing = sorted(set(RiskConfig.model_fields) - set(risk))
    if missing:
        raise ValueError(f"window {window.window_id} frozen_json lacks risk keys {missing}")
    try:
        return RiskConfig.model_validate(risk)
    except ValidationError as exc:
        raise ValueError(f"window {window.window_id} frozen risk section: {exc}") from exc


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
        raise ValueError("a paper_windows row without a window_id: pass a row read from the store")
    return window.window_id


def _cut(session: date) -> datetime:
    """close(S-1): the latest instant a fact may be known at to explain S."""
    if isinstance(session, datetime) or not is_session(session):
        raise ValueError(f"session must be an XNYS session date, got {session!r}")
    return session_close(previous_session(session))


def _stated_through(session: date, now: datetime) -> date:
    """The day the ledger is stated for: the clock's New York date while no
    session has opened since S (a weekend `paper reconcile` for Friday), else
    S. No fill, ex-date or split falls between them, so positions are the
    same; the difference is that an `ok` row written that weekend can still be
    the cash base of the next one."""
    day = now.astimezone(_NEW_YORK).date()
    return day if day > session and command_session(now) == session else session


def _journal_state(
    conn: duckdb.DuckDBPyConnection, window_id: int, as_of: datetime
) -> _JournalState:
    """The window's journal rows known at `as_of` (`known_at <= as_of`): the one
    place the ledger's and the explanations' journal input is cut (#488)."""
    return _JournalState(
        fills=[f for f in fills_for(conn, window_id=window_id) if f.fill.known_at <= as_of],
        orders=[o for o in orders_for(conn, window_id=window_id) if o.known_at <= as_of],
        adjustments=[a for a in adjustments_for(conn, window_id) if a.known_at <= as_of],
        ok_rows=[
            r
            for r in reconciliations_for(conn, window_id)
            if r.status == OK and r.known_at <= as_of
        ],
    )


def _ledger(
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    through: date,
    tolerance: float,
) -> Ledger:
    return from_journal(
        state.fills,
        state.orders,
        state.adjustments,
        actions,
        state.base(through),
        window.starting_cash,
        through,
        window_id=_window_id(window),
        quantity_tolerance=tolerance,
    )


def _current_listings(listings: pl.DataFrame, session: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from <= session`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        held = current.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            current[row["security_id"]] = row
    return current


def _symbols(
    names: Iterable[str],
    order_symbols: Mapping[str, str],
    current: Mapping[str, Mapping[str, Any]],
    broker_symbols: Collection[str],
) -> dict[str, str]:
    """`security_id` to broker symbol, one name per symbol (module docstring)."""
    claims: dict[str, list[str]] = defaultdict(list)
    for name in sorted(set(names)):
        listing = current.get(name)
        symbol = listing["ticker"] if listing is not None else order_symbols.get(name)
        if symbol is not None:
            claims[symbol].append(name)
    symbols: dict[str, str] = {}
    for symbol, claimants in claims.items():
        live = [n for n in claimants if current.get(n, {}).get("status") != DELISTED]
        chosen = claimants if len(claimants) == 1 else live
        if len(chosen) == 1:
            symbols[chosen[0]] = symbol
    claimed = set(claims)
    for symbol in sorted(set(broker_symbols) - claimed):
        matches = [
            name
            for name, row in current.items()
            if row["ticker"] == symbol and row["status"] != DELISTED
        ]
        if len(matches) == 1:
            symbols[matches[0]] = symbol
    return symbols


def _reference_prices(
    conn: duckdb.DuckDBPyConnection, cut: datetime, names: Sequence[str], before: date
) -> dict[str, float]:
    """The raw close of each name's latest bar on or before `before`, known at `cut`."""
    if not names:
        return {}
    bars = prices_as_of(conn, cut, list(names)).filter(pl.col("session") <= before)
    latest = bars.sort("session").group_by("security_id").last()
    return {row["security_id"]: float(row["close"]) for row in latest.iter_rows(named=True)}


def _dividends(
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    session: date,
    tolerance: float,
    through: date,
) -> dict[str, float]:
    """Cash due per name for dividends with ex-date in (base session, S], less
    any the journal already holds as `dividend_cash` on or after the ex-date."""
    base = state.base(through)
    stated = base.at if base is not None else window.started_at
    after = stated.astimezone(_NEW_YORK).date()
    journaled: dict[str, list[date]] = defaultdict(list)
    for adjustment in state.adjustments:
        if adjustment.kind == _DIVIDEND_CASH and adjustment.security_id is not None:
            journaled[adjustment.security_id].append(adjustment.session)
    rows = actions.filter(
        (pl.col("action_type") == _DIVIDEND)
        & (pl.col("ex_date") > after)
        & (pl.col("ex_date") <= session)
    )
    due: dict[str, float] = defaultdict(float)
    holdings: dict[date, Ledger] = {}
    for row in rows.iter_rows(named=True):
        if any(day >= row["ex_date"] for day in journaled.get(row["security_id"], ())):
            continue
        before = previous_session(row["ex_date"])
        if before not in holdings:
            holdings[before] = _ledger(state, actions, window, before, tolerance)
        held = holdings[before].positions.get(row["security_id"], 0.0)
        if held > tolerance:
            due[row["security_id"]] += held * float(row["ratio_or_amount"])
    return {name: amount for name, amount in sorted(due.items()) if amount > 0}


def _explanations(
    conn: duckdb.DuckDBPyConnection,
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    session: date,
    settings: Settings,
    broker_symbols: Collection[str],
    tolerance: float,
    through: date,
) -> Explanations:
    cut = _cut(session)
    names = (
        {f.security_id for f in state.fills}
        | {o.security_id for o in state.orders}
        | {a.security_id for a in state.adjustments if a.security_id is not None}
    )
    order_symbols = {o.security_id: o.symbol for o in state.orders}
    current = _current_listings(listing_ends_as_of(conn, cut, settings), session)
    symbols = _symbols(names, order_symbols, current, broker_symbols)
    ended = frozenset(n for n in names if current.get(n, {}).get("status") == DELISTED)
    priced = sorted(names | set(symbols))
    return Explanations(
        symbols=symbols,
        ended=ended,
        spinoffs={},
        dividends=_dividends(state, actions, window, session, tolerance, through),
        reference_prices=_reference_prices(conn, cut, priced, previous_session(session)),
    )


def explanations_as_of(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    session: date,
    *,
    as_of: datetime,
    settings: Settings,
    broker_symbols: Collection[str] = (),
    quantity_tolerance: float,
) -> Explanations:
    """What the store knew at close(S-1), and the journal at `as_of`, to explain
    the broker on S (module docstring). Read-only. `as_of` is the journal cut
    (tz-aware). `broker_symbols` are `positions()`' keys, so a symbol
    the journal never held can still be matched. `settings` gives
    `master.transfer_window_sessions` for the listing ends, and
    `quantity_tolerance` is the frozen `risk.reconcile_quantity_tolerance`.
    Raises `ValueError` for a non-session `session` or a naive `as_of`."""
    cut = _cut(session)
    as_of = ensure_tz_aware_utc(as_of, field_name="as_of")
    state = _journal_state(conn, _window_id(window), as_of)
    actions = live_actions_as_of(conn, cut)
    return _explanations(
        conn, state, actions, window, session, settings, broker_symbols, quantity_tolerance, session
    )


def _open_orders(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> tuple[list[OrderRow], set[str], dict[str, tuple[float, float]]]:
    """The window's non-terminal orders, the `pending` ids among them, and each
    order's live journaled (quantity, notional)."""
    orders = non_terminal_orders(conn, window_id=window_id)
    pending = {o.client_order_id for o in pending_orders(conn, window_id=window_id)}
    sums: dict[str, tuple[float, float]] = {}
    ids = [o.client_order_id for o in orders]
    if ids:
        for fill in fills_for(conn, client_order_ids=ids):
            quantity, notional = sums.get(fill.fill.client_order_id, (0.0, 0.0))
            sums[fill.fill.client_order_id] = (
                quantity + fill.fill.quantity,
                notional + fill.fill.quantity * fill.fill.price,
            )
    return orders, pending, sums


def _explanation_json(text: str, reconciliation_id: int | None) -> str:
    return json.dumps(
        {"explanation": text, "reconciliation_id": reconciliation_id},
        sort_keys=True,
        allow_nan=False,
    )


def reconcile_now(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    window: PaperWindowRow,
    session: date,
    clock: Callable[[], datetime],
    journal: Connect,
    run_id: int | None = None,
    *,
    frozen: RiskConfig,
    as_of: datetime,
) -> Reconciliation:
    """Reconcile window `window` on session `session` (module docstring) and
    return the compare result once its rows are committed.

    `connect` opens a connection for the reads, `journal` a write chunk for the
    `reconciliations` and `adjustments` rows (both `lambda:
    store.db.open_for_write(settings)` in a run). `frozen` is the window's
    frozen `risk` section and `settings` gives the run-time
    `paper.order_id_prefix` and `master.transfer_window_sessions`. `run_id`
    names the run, None outside one. `as_of` is the journal cut the ledger and
    the explanations read at (module docstring), never after the clock reading.

    Raises `ReconciliationError` on a mismatch (the row is written first),
    `ClockError` for a bad clock reading, `ValueError` for a non-session
    `session`, a window without an id, or a naive `as_of` or one after the clock
    reading, and whatever the broker raises (every
    allowlist this reaches is empty, req 4)."""
    window_id = _window_id(window)
    cut = _cut(session)
    now = _read_clock(clock)
    as_of = ensure_tz_aware_utc(as_of, field_name="as_of")
    if as_of > now:
        raise ValueError(f"as_of {as_of.isoformat()} is after the clock reading {now.isoformat()}")
    through = _stated_through(session, now)
    tolerance = frozen.reconcile_quantity_tolerance

    with connect() as conn:
        orders, pending, sums = _open_orders(conn, window_id)

    positions: dict[str, Position] = broker.positions()
    open_orders: list[Order] = broker.open_orders()
    account: Account = broker.account()
    lagging = [
        JournalOpenOrder(
            order=order,
            pending=order.client_order_id in pending,
            journaled_quantity=sums.get(order.client_order_id, (0.0, 0.0))[0],
            journaled_notional=sums.get(order.client_order_id, (0.0, 0.0))[1],
            reading=(
                None
                if order.client_order_id in pending
                else broker.get_order(order.client_order_id)
            ),
        )
        for order in orders
    ]

    with connect() as conn:
        state = _journal_state(conn, window_id, as_of)
        actions = live_actions_as_of(conn, cut)
        explanations = _explanations(
            conn, state, actions, window, session, settings, positions.keys(), tolerance, through
        )
    ledger = _ledger(state, actions, window, through, tolerance)
    result = compare(
        ledger,
        positions,
        open_orders,
        lagging,
        account,
        explanations,
        window,
        frozen,
        order_id_prefix=settings.paper.order_id_prefix,
    )

    with journal() as conn:
        stamp = _read_clock(clock)
        if stamp < now:
            raise ClockError(f"clock went back from {now.isoformat()} to {stamp.isoformat()}")
        reconciliation_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=run_id,
                at=stamp,
                status=result.status,
                broker_cash=result.broker_cash,
                mismatches_json=result.mismatches_json,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
        for adjustment in result.adjustments:
            append(
                conn,
                AdjustmentRow(
                    window_id=window_id,
                    run_id=run_id,
                    session=session,
                    kind=adjustment.kind,
                    security_id=adjustment.security_id,
                    quantity=adjustment.quantity,
                    cash=adjustment.cash,
                    explanation_json=_explanation_json(adjustment.explanation, reconciliation_id),
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )

    if result.status == MISMATCH:
        kinds = ", ".join(sorted({m.kind for m in result.mismatches}))
        raise ReconciliationError(
            f"reconciliation {reconciliation_id} of window {window_id} on "
            f"{session.isoformat()}: {kinds}"
        )
    return result


def command_session(now: datetime) -> date:
    """The session `paper reconcile` states the ledger for: the clock's New
    York date when it is a session, else the latest session before it."""
    day = ensure_tz_aware_utc(now, field_name="clock").astimezone(_NEW_YORK).date()
    return day if is_session(day) else previous_session(day)


def reconcile_command(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
) -> Reconciliation:
    """`paper reconcile` (module docstring). Raises `LockHeld` while another
    process holds the run lock and `NoWindowError` with no open window, both
    before any broker call or write. On a mismatch, appends the `engaged` row
    and re-raises `ReconciliationError`; if that row cannot be written (the
    store, or the clock it is stamped with, failing), the error says so and
    still names the mismatch."""
    with run_lock(settings):
        with connect() as conn:
            window = open_window(conn)
        if window is None:
            raise NoWindowError("no_window: no paper window is open")
        frozen = frozen_risk(window)
        now = _read_clock(clock)
        session = command_session(now)
        try:
            return reconcile_now(
                settings, connect, broker, window, session, clock, connect, frozen=frozen, as_of=now
            )
        except ReconciliationError as exc:
            try:
                engaged = switch.engage(
                    settings,
                    clock,
                    window_id=_window_id(window),
                    source="fault",
                    fault_type=_FAULT_TYPE,
                    reason=str(exc),
                )
            except Exception as engage_error:  # a bad clock reading, say
                engaged = switch.WriteFailed(f"{type(engage_error).__name__}: {engage_error}")
            if isinstance(engaged, switch.WriteFailed):
                raise ReconciliationError(
                    f"{exc}; the kill switch row could not be written: {engaged.error}"
                ) from exc
            raise

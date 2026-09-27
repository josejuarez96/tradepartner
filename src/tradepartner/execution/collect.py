"""The collectors: fills, terminal events, fill cursors, the lag bound and the
rejection cap (Phase 4 spec reqs 4 and 8; plan T58).

`collect` is what every collector runs (a run's step 3 and step 7b, the halt
path's read, `paper resume`'s settlement). In order:

1. **Read the journal** (one short chunk): the fill cursors, every
   non-terminal own order and its `pending` event, and which orders are still
   `pending`. An order passed in that is `pending` (no broker-acknowledged
   event) is refused with `ValueError` before any broker call: only `paper
   resume` settles those, through `get_order` in its own code. An order the
   journal already holds terminal is skipped.
2. **Read the broker**: `fills(since)` once, account-wide, with `since` =
   min(the latest `collected_through` across every `fill_cursors` row, the
   earliest `known_at` of the `pending` event of any non-terminal own order)
   - `paper.fill_read_overlap_seconds`, inclusive at equal timestamps
   (`Broker.fills` returns `filled_at >= since`); `None` (everything) when
   there is neither. A `broker_feed` fill whose `filled_at` is ahead of the
   clock by more than the frozen `risk.max_broker_clock_skew_seconds` is a
   `ClockError`, raised before anything is written. Then `get_order` once per
   order to collect. Broker errors propagate: every method's allowlist but
   `cancel`'s is empty (req 4), so the caller halts.
3. **Write** (one chunk, so the journal never holds half a collection):
   - each fill of an own order whose `broker_fill_id` no row has, superseded
     rows included (insert-or-ignore), stamped with the collection time as
     `known_at` and the broker's `filled_at`; when its order already has a
     live `broker_status` (synthetic) fill, the new row is journaled with
     `superseded_by` pointing at it, so every reader still counts the
     position once. A fill of an order the journal does not hold is not
     journaled (`fills_for` would refuse it); it is reported, and
     reconciliation flags the foreign order;
   - each collected order's terminal event, **only** once its journaled
     fills reach the broker's `filled_quantity` within the frozen
     `risk.reconcile_quantity_tolerance`. Until then the order stays in
     flight whatever `get_order` says, and it is **`fills_lagging`** when
     the broker's `filled_quantity` exceeds its journaled fills by more than
     that tolerance;
   - the rejection verdicts (below) for the runs whose orders this
     collection saw `rejected`;
   - with `write_offs` given (a run only; `paper resume` writes no
     `decision_events`), a `written_off` row (reason `unfunded`, the
     remainder's notional as `unfunded_notional`) for every decision of the
     window that `plan.decision_state` reports written off and that lacks
     one: the back-fill for a buy that went terminal in a resume or an
     earlier collection;
   - one `fill_cursors` row for this invocation, whatever it found. Its
     `collected_through` is the latest `filled_at` seen, never earlier than
     the previous cursor; with no fill and no cursor yet, the earliest open
     order's `pending` time, else the clock's reading (the next read's
     window still reaches back to any open order's `pending` event).

**The lag bound** (`lag_verdict`) is pure. It is anchored on the `known_at`
of the first reconciliation row whose `mismatches_json` lists the order under
`"lagging"` (T55's format). It counts the XNYS sessions after the anchor's New
York date, through the collection's session. It is breached at or after the
frozen `risk.max_fill_lag_sessions`, so a missed run exactly N sessions later
does not reset it. An order settled late by a resume is anchored on its first
lagging reconciliation, not on its submit session. Only a `paper run`
collection judges it (the caller's rule).

**The rejection cap** (`rejection_breaches`) is pure. An asynchronous
`rejected` status counts against the **submitting** run. A verdict when that
run's `rejected` orders exceed the frozen `risk.max_rejections_per_run`, or
when every one of its orders is `rejected`, even below the cap. The caller
raises `RejectionCapError` with the verdict's message. A run's rejections are
judged when a collection sees a new one, not again on every later read of its
other orders.
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.adapters.broker import TERMINAL_STATUSES, Broker, Fill, Order
from tradepartner.calendar import all_sessions
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError
from tradepartner.execution.plan import decision_state
from tradepartner.store.journal import (
    DecisionEventRow,
    FillCursorRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    ReconciliationRow,
    all_fill_ids,
    append,
    decisions_for,
    fill_cursors,
    fills_for,
    non_terminal_orders,
    order_events_for,
    orders_for,
    pending_orders,
)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

WRITER_KINDS = ("run", "resume")
_RUN = WRITER_KINDS[0]
_PENDING = "pending"
_REJECTED = "rejected"
_BUY = "buy"
_WRITTEN_OFF = "written_off"
_UNFUNDED = "unfunded"
_BROKER_FEED = "broker_feed"
_BROKER_STATUS = "broker_status"
_LAGGING_KEY = "lagging"
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OrderReading:
    """One collected order: the broker's reading, the live journaled fills
    after this collection, whether it is `fills_lagging`, and whether this
    collection wrote its terminal event."""

    order: OrderRow
    reading: Order
    journaled_quantity: float
    journaled_notional: float
    lagging: bool
    terminal_written: bool


@dataclass(frozen=True)
class RejectionBreach:
    """A submitting run over the rejection cap, or with every order rejected."""

    run_id: int
    rejected: int
    orders: int
    all_rejected: bool

    @property
    def message(self) -> str:
        """The `RejectionCapError` text, naming the submitting run."""
        if self.all_rejected:
            return f"every order of run {self.run_id} was rejected ({self.rejected})"
        return (
            f"run {self.run_id} had {self.rejected} of {self.orders} orders rejected, "
            "over risk.max_rejections_per_run"
        )


@dataclass(frozen=True)
class WriteOffContext:
    """What `plan.decision_state` needs for the write-off back-fill: the window,
    the splits known at close(S-1), the reference price and the session S."""

    window_id: int
    actions_as_of: pl.DataFrame
    price_of: Callable[[str], float]
    session: date


@dataclass(frozen=True)
class Collected:
    """What one collection saw and wrote."""

    since: datetime | None
    collected_through: datetime
    fills_journaled: tuple[str, ...]
    superseded: tuple[str, ...]
    foreign_fill_ids: tuple[str, ...]
    readings: tuple[OrderReading, ...]
    rejections: tuple[RejectionBreach, ...]
    written_off: tuple[int, ...]

    @property
    def lagging(self) -> tuple[OrderReading, ...]:
        """The collected orders that are `fills_lagging`."""
        return tuple(r for r in self.readings if r.lagging)


@dataclass(frozen=True)
class LagVerdict:
    """An order's lag bound: its anchor (None when no reconciliation listed it),
    the sessions past it, and whether the bound is breached."""

    anchor: datetime | None
    sessions_past: int
    breached: bool


def collect(
    broker: Broker,
    connect: Connect,
    orders: Sequence[OrderRow],
    clock: Callable[[], datetime],
    writer_kind: str,
    writer_id: int,
    frozen: RiskConfig,
    settings: Settings,
    *,
    write_offs: WriteOffContext | None = None,
) -> Collected:
    """Collect fills and the terminal events of `orders` (module docstring).

    `connect` opens a write chunk (`lambda: store.db.open_for_write(settings)`),
    `frozen` is the window's frozen `risk.*` section, and `settings` gives the
    run-time `paper.fill_read_overlap_seconds`. `writer_kind` is `run` or
    `resume` and `writer_id` its run or resume id. Raises `ValueError` for a
    `pending` order, an unknown writer kind or `write_offs` outside a run, and
    `ClockError` for a fill beyond the broker-clock skew, all before any
    write."""
    if writer_kind not in WRITER_KINDS:
        raise ValueError(f"writer_kind must be one of {WRITER_KINDS}, got {writer_kind!r}")
    if write_offs is not None and writer_kind != _RUN:
        raise ValueError("only a run writes decision_events (the write-off back-fill)")

    with connect() as conn:
        cursor = _latest_cursor(conn)
        open_since = _earliest_open_pending(conn)
        pending = {o.client_order_id for o in pending_orders(conn, window_id=None)}
        open_ids = {o.client_order_id for o in non_terminal_orders(conn, window_id=None)}
    refused = sorted(o.client_order_id for o in orders if o.client_order_id in pending)
    if refused:
        raise ValueError(f"pending orders are settled by paper resume, not collected: {refused}")
    to_read = [o for o in orders if o.client_order_id in open_ids]

    bounds = [t for t in (cursor, open_since) if t is not None]
    overlap = timedelta(seconds=settings.paper.fill_read_overlap_seconds)
    since = min(bounds) - overlap if bounds else None
    now = clock()
    seen = broker.fills(since)
    _check_skew(seen, now, frozen.max_broker_clock_skew_seconds)
    readings = {o.client_order_id: broker.get_order(o.client_order_id) for o in to_read}

    with connect() as conn:
        stamp = clock()
        journaled, superseded, foreign = _journal_fills(conn, seen, stamp)
        collected = _journal_terminals(conn, to_read, readings, frozen, stamp)
        newly_rejected = sorted(
            {
                r.order.run_id
                for r in collected
                if r.terminal_written and r.reading.status == _REJECTED
            }
        )
        rejections = rejection_breaches(
            newly_rejected,
            orders_for(conn, window_id=None),
            order_events_for(conn, window_id=None),
            max_rejections=frozen.max_rejections_per_run,
        )
        written_off = (
            _back_fill_write_offs(conn, write_offs, frozen, writer_id, stamp)
            if write_offs is not None
            else ()
        )
        latest_seen = max((f.filled_at for f in seen), default=None)
        through = max(
            (t for t in (latest_seen, cursor) if t is not None), default=open_since or stamp
        )
        _write(
            conn,
            FillCursorRow(
                writer_kind=writer_kind,
                writer_id=writer_id,
                collected_through=through,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )

    return Collected(
        since=since,
        collected_through=through,
        fills_journaled=journaled,
        superseded=superseded,
        foreign_fill_ids=foreign,
        readings=tuple(collected),
        rejections=rejections,
        written_off=written_off,
    )


def _write(conn: duckdb.DuckDBPyConnection, row: object) -> int | None:
    # `journal.JournalRow` declares `known_at`/`ingested_at` as settable members,
    # which no frozen row type satisfies under mypy; `append` only reads them.
    return append(conn, row)  # type: ignore[arg-type]


def _latest_cursor(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    return max((c.collected_through for c in fill_cursors(conn)), default=None)


def _earliest_open_pending(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The earliest `known_at` of the `pending` event of any non-terminal order."""
    open_ids = {o.client_order_id for o in non_terminal_orders(conn, window_id=None)}
    return min(
        (
            e.known_at
            for e in order_events_for(conn, window_id=None)
            if e.status == _PENDING and e.client_order_id in open_ids
        ),
        default=None,
    )


def _check_skew(fills: Iterable[Fill], now: datetime, skew_seconds: float) -> None:
    limit = now + timedelta(seconds=skew_seconds)
    ahead = [f for f in fills if f.filled_at > limit]
    if ahead:
        worst = max(ahead, key=lambda f: f.filled_at)
        raise ClockError(
            f"broker fill {worst.broker_fill_id} is {worst.filled_at.isoformat()}, ahead of "
            f"the clock {now.isoformat()} by more than {skew_seconds}s"
        )


def _journal_fills(
    conn: duckdb.DuckDBPyConnection, seen: Sequence[Fill], stamp: datetime
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Insert-or-ignore each own fill; (journaled, superseded, foreign) ids."""
    known = set(all_fill_ids(conn))
    own = {o.client_order_id for o in orders_for(conn, window_id=None)}
    journaled: list[str] = []
    superseded: list[str] = []
    foreign: list[str] = []
    for fill in seen:
        if fill.broker_fill_id in known:
            continue
        if fill.client_order_id not in own:
            foreign.append(fill.broker_fill_id)
            continue
        synthetic = [
            f.fill.fill_id
            for f in fills_for(conn, client_order_ids=[fill.client_order_id])
            if f.fill.source == _BROKER_STATUS
        ]
        _write(
            conn,
            FillRow(
                client_order_id=fill.client_order_id,
                filled_at=fill.filled_at,
                quantity=fill.quantity,
                price=fill.price,
                price_implied=False,
                broker_fill_id=fill.broker_fill_id,
                source=_BROKER_FEED,
                superseded_by=synthetic[0] if synthetic else None,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
        known.add(fill.broker_fill_id)
        journaled.append(fill.broker_fill_id)
        if synthetic:
            superseded.append(fill.broker_fill_id)
    return tuple(journaled), tuple(superseded), tuple(foreign)


def _journal_terminals(
    conn: duckdb.DuckDBPyConnection,
    orders: Sequence[OrderRow],
    readings: dict[str, Order],
    frozen: RiskConfig,
    stamp: datetime,
) -> list[OrderReading]:
    """Each order's reading after the fills above, writing its terminal event
    when the broker says terminal and the journaled fills have caught up."""
    tolerance = frozen.reconcile_quantity_tolerance
    live = fills_for(conn, client_order_ids=[o.client_order_id for o in orders])
    result = []
    for order in orders:
        reading = readings[order.client_order_id]
        mine = [f.fill for f in live if f.fill.client_order_id == order.client_order_id]
        quantity = math.fsum(f.quantity for f in mine)
        notional = math.fsum(f.quantity * f.price for f in mine)
        lagging = (reading.filled_quantity or 0.0) - quantity > tolerance
        terminal = reading.status in TERMINAL_STATUSES and not lagging
        if terminal:
            _write(
                conn,
                OrderEventRow(
                    client_order_id=order.client_order_id,
                    event_at=reading.filled_at,
                    status=reading.status.value,
                    broker_order_id=reading.broker_order_id,
                    filled_quantity=reading.filled_quantity,
                    filled_avg_price=reading.filled_avg_price,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
        result.append(OrderReading(order, reading, quantity, notional, lagging, terminal))
    return result


def _back_fill_write_offs(
    conn: duckdb.DuckDBPyConnection,
    context: WriteOffContext,
    frozen: RiskConfig,
    run_id: int,
    stamp: datetime,
) -> tuple[int, ...]:
    """Append the `written_off` row each written-off buy of the window lacks."""
    orders = orders_for(conn, window_id=context.window_id)
    events = order_events_for(conn, window_id=context.window_id)
    fills = fills_for(conn, window_id=context.window_id)
    written: list[int] = []
    for item in decisions_for(conn, context.window_id):
        decision = item.decision
        if decision.side != _BUY or any(e.status == _WRITTEN_OFF for e in item.events):
            continue
        state = decision_state(
            decision,
            list(item.events),
            orders,
            events,
            fills,
            context.actions_as_of,
            context.price_of,
            frozen,
            session=context.session,
        )
        if not state.written_off:
            continue
        assert decision.decision_id is not None and state.remainder is not None
        _write(
            conn,
            DecisionEventRow(
                decision_id=decision.decision_id,
                run_id=run_id,
                status=_WRITTEN_OFF,
                reason=_UNFUNDED,
                unfunded_notional=state.remainder.notional,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
        written.append(decision.decision_id)
    return tuple(written)


def rejection_breaches(
    run_ids: Iterable[int],
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    *,
    max_rejections: int,
) -> tuple[RejectionBreach, ...]:
    """The rejection verdicts for the submitting runs `run_ids`, in run order:
    `rejected` orders above `max_rejections` (the frozen
    `risk.max_rejections_per_run`), or every order of the run rejected."""
    rejected_ids = {e.client_order_id for e in order_events if e.status == _REJECTED}
    breaches = []
    for run_id in sorted(set(run_ids)):
        mine = [o for o in orders if o.run_id == run_id]
        rejected = sum(o.client_order_id in rejected_ids for o in mine)
        all_rejected = bool(mine) and rejected == len(mine)
        if rejected > max_rejections or all_rejected:
            breaches.append(RejectionBreach(run_id, rejected, len(mine), all_rejected))
    return tuple(breaches)


def _lagging_ids(row: ReconciliationRow) -> list[str]:
    if row.mismatches_json is None:
        return []
    try:
        listed = json.loads(row.mismatches_json).get(_LAGGING_KEY, [])
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"reconciliation {row.reconciliation_id} has an unreadable mismatches_json"
        ) from exc
    if not isinstance(listed, list):
        raise ValueError(
            f"reconciliation {row.reconciliation_id} mismatches_json lagging is not a list"
        )
    return [str(i) for i in listed]


def lag_verdict(
    order: OrderRow,
    reconciliations: Sequence[ReconciliationRow],
    session: date,
    frozen: RiskConfig,
) -> LagVerdict:
    """The order's lag bound at a `paper run` collection on `session` (module
    docstring). Raises `ValueError` for a reconciliation row whose
    `mismatches_json` cannot be read, rather than miss an anchor."""
    listing = [
        r
        for r in sorted(reconciliations, key=lambda r: (r.known_at, r.reconciliation_id or 0))
        if order.client_order_id in _lagging_ids(r)
    ]
    if not listing:
        return LagVerdict(anchor=None, sessions_past=0, breached=False)
    anchor = listing[0].known_at
    anchor_day = anchor.astimezone(_NEW_YORK).date()
    sessions = all_sessions()
    past = bisect.bisect_right(sessions, session) - bisect.bisect_right(sessions, anchor_day)
    past = max(past, 0)
    return LagVerdict(
        anchor=anchor, sessions_past=past, breached=past >= frozen.max_fill_lag_sessions
    )

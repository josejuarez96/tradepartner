"""Outcomes and the lot-ledger write (Phase 4 spec req 8 "Chain" and req 13;
plan T62).

Every order ends in exactly one outcome per kind it earns, **due** at the first
run after the end of its horizon:

- **Horizon.** An order of rebalance i (its session is F_i, or a catch-up
  session, so T_i is the last rebalance session before it) runs to
  close(T_{i+1}); a forced exit (`phase = exit`) to its own session. In a
  window with a stop requested, the horizon ends at the earliest of that, the
  name's flattening fill (the first filled `exit` sell of the name on or after
  the stop request) and the first `stop` run's session on or after the request.
  An outcome is due at a run on a session after the horizon's end, or on a
  `stop` run whose own mark ends it (the first `stop` run writes it).
- **Kinds.** A buy with fills: `position_return`, the mark at the horizon over
  the order's average fill price (over the accessor's live fills, so an implied
  residual enters at the order's average and a superseded fill never), less 1,
  with its contribution (the dollar gain over the equity of the last mark before
  the order's session). A sell with fills: `realised_pnl`, the dollars realised
  on the FIFO lots it closed (`execution.lots`), with its contribution. An
  order that did not fill in full (expired, rejected or cancelled): also
  `not_executed`, the name's return from close of the session before the order
  to the horizon (the cost of the miss), no contribution. A partial fill then
  expiry earns both.
- **Prices.** The horizon's mark is the `positions_daily` mark of the name on
  that session, else `prices(security_id, session)` (the raw close). A missing
  price leaves the value and mark None; a missing equity leaves the
  contribution None.

`due_outcomes` is pure. `write_outcomes_and_lots` is its I/O edge: it reads the
window's rows through `store.journal`, rebuilds the lot ledger over the
window's live fills, appends the due outcomes, and appends the rebuilt
`lots`, `disposals` and `wash_sale_flags` as one new set, stamped with one
clock reading, only when the set differs from the latest one journaled (so a
rebuild that changes nothing appends nothing, and the current ledger is the
latest set). A `LotLedgerError` (a sale beyond the fill-built holding: a split,
spin-off receipt or stock merger, which lots do not take yet) never fails the
run: the writer passes its message to `on_lot_error` (the caller alerts), writes
no lot rows and holds `realised_pnl` back until the ledger rebuilds.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.adapters.broker import Asset
from tradepartner.calendar import last_session_of_month, previous_session
from tradepartner.execution.lots import (
    Disposal,
    LedgerAccount,
    Lot,
    LotLedgerError,
    WashSaleFlag,
    rebuild,
)
from tradepartner.store import journal
from tradepartner.store.journal import (
    DisposalRow,
    LotRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PositionDailyRow,
    WashSaleFlagRow,
)

PriceOf = Callable[[str, date], float | None]
LotLedger = tuple[Sequence[Lot], Sequence[Disposal], Sequence[WashSaleFlag]]

POSITION_RETURN = "position_return"
REALISED_PNL = "realised_pnl"
NOT_EXECUTED = "not_executed"
_TERMINAL = frozenset({"filled", "expired", "rejected", "cancelled"})
_FILLED = "filled"
_EXIT = "exit"
_BUY = "buy"
_SELL = "sell"
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OutcomeWindow:
    """The window as outcomes need it: its id, the session of its stop
    request and of its `stop` runs (if any), and the (client order id, kind)
    pairs already journaled."""

    window_id: int
    stop_requested: date | None = None
    stop_run_sessions: tuple[date, ...] = ()
    written: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class Outcome:
    """One `outcomes` row without its journal timestamps."""

    client_order_id: str
    through_session: date
    kind: str
    value: float | None
    contribution: float | None
    mark_price: float | None


@dataclass(frozen=True)
class WriteResult:
    """What `write_outcomes_and_lots` appended."""

    outcomes: int
    lot_rows: int
    lot_error: str | None


def _rebalance_before(session: date) -> date:
    """T_i: the last rebalance session (last session of a month) before `session`."""
    candidate = last_session_of_month(session.year, session.month)
    if candidate < session:
        return candidate
    year, month = (session.year, session.month - 1) if session.month > 1 else (session.year - 1, 12)
    return last_session_of_month(year, month)


def _rebalance_after(rebalance: date) -> date:
    """T_{i+1}: the last session of the month after `rebalance`'s."""
    year, month = (
        (rebalance.year, rebalance.month + 1) if rebalance.month < 12 else (rebalance.year + 1, 1)
    )
    return last_session_of_month(year, month)


def _local(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _horizon(
    order: OrderRow,
    window: OutcomeWindow,
    flattening: Mapping[str, date],
) -> tuple[date, bool]:
    """(the horizon's last session, whether a `stop` run's own mark ends it)."""
    if order.phase == _EXIT:
        through = order.session
    else:
        through = _rebalance_after(_rebalance_before(order.session))
    if window.stop_requested is None:
        return through, False
    candidates = [through]
    flat = flattening.get(order.security_id)
    if flat is not None:
        candidates.append(flat)
    stop_runs = sorted(s for s in window.stop_run_sessions if s >= window.stop_requested)
    if stop_runs:
        candidates.append(stop_runs[0])
    end = min(candidates)
    return end, bool(stop_runs) and end == stop_runs[0] and end not in candidates[:-1]


def _flattening(
    orders: Sequence[OrderRow],
    terminal: Mapping[str, str],
    fills: Sequence[OrderedFill],
    stop_requested: date | None,
) -> dict[str, date]:
    """Per name, the session of the last fill of its first filled `exit` sell
    on or after the stop request."""
    if stop_requested is None:
        return {}
    last_fill: dict[str, date] = {}
    for item in fills:
        day = _local(item.fill.filled_at)
        coid = item.fill.client_order_id
        last_fill[coid] = max(day, last_fill.get(coid, day))
    found: dict[str, date] = {}
    for order in sorted(orders, key=lambda o: (o.session, o.client_order_id)):
        if (
            order.phase == _EXIT
            and order.side == _SELL
            and order.session >= stop_requested
            and terminal.get(order.client_order_id) == _FILLED
            and order.client_order_id in last_fill
            and order.security_id not in found
        ):
            found[order.security_id] = last_fill[order.client_order_id]
    return found


def _equity_before(marks: Sequence[PositionDailyRow], session: date) -> float | None:
    """Equity (position values plus cash) at the last mark before `session`."""
    earlier = [m.session for m in marks if m.session < session]
    if not earlier:
        return None
    day = max(earlier)
    rows = [m for m in marks if m.session == day]
    return sum((m.value or 0.0) + (m.cash or 0.0) for m in rows)


def _mark(
    marks: Sequence[PositionDailyRow], prices: PriceOf, security_id: str, session: date
) -> float | None:
    for row in marks:
        if row.session == session and row.security_id == security_id and row.mark_price:
            return row.mark_price
    return prices(security_id, session)


def _terminal_status(events: Iterable[OrderEventRow]) -> dict[str, str]:
    status: dict[str, str] = {}
    for event in sorted(events, key=lambda e: (e.known_at, e.ingested_at)):
        if event.status in _TERMINAL:
            status.setdefault(event.client_order_id, event.status)
    return status


def due_outcomes(
    window: OutcomeWindow,
    orders: Sequence[OrderRow],
    events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    marks: Sequence[PositionDailyRow],
    lots: LotLedger | None,
    prices: PriceOf,
    session: date,
) -> list[Outcome]:
    """The outcomes due at a run on `session` and not yet written (module
    docstring). `fills` are `fills_for`'s live fills, `marks` the window's
    `positions_daily`, `lots` the `lots.rebuild` result over the same fills
    (None when it failed: `realised_pnl` then waits) and `prices` the raw close
    of a name on a session (None when unknown)."""
    terminal = _terminal_status(events)
    flattening = _flattening(orders, terminal, fills, window.stop_requested)
    by_order: dict[str, list[OrderedFill]] = {}
    for item in fills:
        by_order.setdefault(item.fill.client_order_id, []).append(item)
    realised: dict[str, float] = {}
    if lots is not None:
        for disposal in lots[1]:
            realised[disposal.client_order_id] = (
                realised.get(disposal.client_order_id, 0.0) + disposal.realised_pnl
            )

    due: list[Outcome] = []
    for order in sorted(orders, key=lambda o: o.client_order_id):
        status = terminal.get(order.client_order_id)
        if status is None:
            continue
        through, on_stop_run = _horizon(order, window, flattening)
        if not (session > through or (on_stop_run and session >= through)):
            continue
        equity = _equity_before(marks, order.session)
        own = by_order.get(order.client_order_id, [])
        quantity = sum(item.fill.quantity for item in own)
        mark = _mark(marks, prices, order.security_id, through)
        candidates: list[Outcome] = []
        if quantity > 0 and order.side == _BUY:
            average = sum(i.fill.quantity * i.fill.price for i in own) / quantity
            value = None if mark is None or average <= 0 else mark / average - 1
            gain = None if mark is None else (mark - average) * quantity
            candidates.append(
                Outcome(
                    order.client_order_id,
                    through,
                    POSITION_RETURN,
                    value,
                    _share(gain, equity),
                    mark,
                )
            )
        if quantity > 0 and order.side == _SELL and lots is not None:
            pnl = realised.get(order.client_order_id)
            candidates.append(
                Outcome(
                    order.client_order_id, through, REALISED_PNL, pnl, _share(pnl, equity), mark
                )
            )
        if status != _FILLED:
            start = prices(order.security_id, previous_session(order.session))
            value = None if mark is None or not start else mark / start - 1
            candidates.append(
                Outcome(order.client_order_id, through, NOT_EXECUTED, value, None, mark)
            )
        due += [o for o in candidates if (o.client_order_id, o.kind) not in window.written]
    return due


def _share(amount: float | None, equity: float | None) -> float | None:
    if amount is None or equity is None or equity <= 0:
        return None
    return amount / equity


# --- the I/O edge ----------------------------------------------------------------


def write_outcomes_and_lots(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    account: LedgerAccount,
    assets: Mapping[str, Asset],
    prices: PriceOf,
    session: date,
    clock: Callable[[], datetime],
    *,
    on_lot_error: Callable[[str], None],
    stop_requested: date | None = None,
    stop_run_sessions: tuple[date, ...] = (),
) -> WriteResult:
    """Rebuild the lot ledger over the window's live fills, append the new set
    when it changed, and append the outcomes due at `session` (module
    docstring). `on_lot_error` receives a `LotLedgerError`'s message; the caller
    alerts. Every row is stamped with one reading of `clock`."""
    now = clock()
    fills = journal.fills_for(conn, window_id=window_id)
    orders = journal.orders_for(conn, window_id=window_id)
    events = journal.order_events_for(conn, window_id=window_id)
    marks = journal.positions_daily_for(conn, window_id)
    written = frozenset((o.client_order_id, o.kind) for o in journal.outcomes_for(conn, window_id))

    ledger: LotLedger | None
    lot_error: str | None = None
    try:
        ledger = rebuild(fills, orders, assets, account)
    except LotLedgerError as exc:
        ledger, lot_error = None, str(exc)
        on_lot_error(lot_error)
    lot_rows = 0 if ledger is None else _append_ledger(conn, ledger, now)

    window = OutcomeWindow(window_id, stop_requested, stop_run_sessions, written)
    outcomes = due_outcomes(window, orders, events, fills, marks, ledger, prices, session)
    for outcome in outcomes:
        journal.append(
            conn,
            OutcomeRow(
                client_order_id=outcome.client_order_id,
                through_session=outcome.through_session,
                kind=outcome.kind,
                value=outcome.value,
                contribution=outcome.contribution,
                mark_price=outcome.mark_price,
                known_at=now,
                ingested_at=now,
            ),
        )
    return WriteResult(outcomes=len(outcomes), lot_rows=lot_rows, lot_error=lot_error)


_LOT_COLUMNS = (
    "account_id, account_type, account_owner, security_id, symbol, cusip, trade_at, "
    "trade_date_local, quantity, cost_basis, fill_id"
)
_DISPOSAL_COLUMNS = (
    "account_id, trade_at, trade_date_local, quantity, proceeds, realised_pnl, tax_year, fill_id"
)


def _latest_set(conn: duckdb.DuckDBPyConnection) -> tuple[list[tuple[object, ...]], ...]:
    """The content of the latest journaled lot-ledger set, ids replaced by
    positions within the set, for comparison."""
    lot_rows = conn.execute(
        f"SELECT lot_id, {_LOT_COLUMNS} FROM lots "
        "WHERE known_at = (SELECT max(known_at) FROM lots) ORDER BY lot_id"
    ).fetchall()
    lot_pos = {row[0]: n for n, row in enumerate(lot_rows)}
    disposal_rows = conn.execute(
        f"SELECT disposal_id, lot_id, {_DISPOSAL_COLUMNS} FROM disposals "
        "WHERE known_at = (SELECT max(known_at) FROM lots) ORDER BY disposal_id"
    ).fetchall()
    disposal_pos = {row[0]: n for n, row in enumerate(disposal_rows)}
    flag_rows = conn.execute(
        "SELECT disposal_id, replacement_lot_id, matched_quantity, disallowed_amount "
        "FROM wash_sale_flags WHERE known_at = (SELECT max(known_at) FROM lots) ORDER BY flag_id"
    ).fetchall()
    return (
        [_normal(row[1:]) for row in lot_rows],
        [(lot_pos.get(row[1]), *_normal(row[2:])) for row in disposal_rows],
        [(disposal_pos.get(r[0]), lot_pos.get(r[1]), *_normal(r[2:])) for r in flag_rows],
    )


def _normal(values: Sequence[object]) -> tuple[object, ...]:
    """Values as the store returns them, comparable with a rebuilt row's."""
    return tuple(v.astimezone(_NEW_YORK) if isinstance(v, datetime) else v for v in values)


def _rebuilt_set(ledger: LotLedger) -> tuple[list[tuple[object, ...]], ...]:
    lots, disposals, flags = ledger
    lot_pos = {lot.lot_id: n for n, lot in enumerate(lots)}
    disposal_pos = {d.disposal_id: n for n, d in enumerate(disposals)}
    return (
        [
            _normal(
                (
                    lot.account_id,
                    lot.account_type,
                    lot.account_owner,
                    lot.security_id,
                    lot.symbol,
                    lot.cusip,
                    lot.trade_at,
                    lot.trade_date_local,
                    lot.quantity,
                    lot.cost_basis,
                    lot.fill_id,
                )
            )
            for lot in lots
        ],
        [
            (
                lot_pos[d.lot_id],
                *_normal(
                    (
                        d.account_id,
                        d.trade_at,
                        d.trade_date_local,
                        d.quantity,
                        d.proceeds,
                        d.realised_pnl,
                        d.tax_year,
                        d.fill_id,
                    )
                ),
            )
            for d in disposals
        ],
        [
            (
                disposal_pos[f.disposal_id],
                lot_pos[f.replacement_lot_id],
                f.matched_quantity,
                f.disallowed_amount,
            )
            for f in flags
        ],
    )


def _append_ledger(conn: duckdb.DuckDBPyConnection, ledger: LotLedger, now: datetime) -> int:
    """Append the rebuilt set when it differs from the latest one; return the
    number of rows appended."""
    lots, disposals, flags = ledger
    if not lots or _rebuilt_set(ledger) == _latest_set(conn):
        return 0
    lot_ids: dict[int, int] = {}
    for lot in lots:
        new_id = journal.append(
            conn,
            LotRow(
                account_id=lot.account_id,
                account_type=lot.account_type,
                account_owner=lot.account_owner,
                security_id=lot.security_id,
                symbol=lot.symbol,
                cusip=lot.cusip,
                trade_at=lot.trade_at,
                trade_date_local=lot.trade_date_local,
                quantity=lot.quantity,
                cost_basis=lot.cost_basis,
                fill_id=lot.fill_id,
                known_at=now,
                ingested_at=now,
            ),
        )
        assert new_id is not None
        lot_ids[lot.lot_id] = new_id
    disposal_ids: dict[int, int] = {}
    for d in disposals:
        new_id = journal.append(
            conn,
            DisposalRow(
                lot_id=lot_ids[d.lot_id],
                account_id=d.account_id,
                trade_at=d.trade_at,
                trade_date_local=d.trade_date_local,
                quantity=d.quantity,
                proceeds=d.proceeds,
                realised_pnl=d.realised_pnl,
                tax_year=d.tax_year,
                fill_id=d.fill_id,
                known_at=now,
                ingested_at=now,
            ),
        )
        assert new_id is not None
        disposal_ids[d.disposal_id] = new_id
    for f in flags:
        journal.append(
            conn,
            WashSaleFlagRow(
                disposal_id=disposal_ids[f.disposal_id],
                replacement_lot_id=lot_ids[f.replacement_lot_id],
                matched_quantity=f.matched_quantity,
                disallowed_amount=f.disallowed_amount,
                scanned_at=now,
                known_at=now,
                ingested_at=now,
            ),
        )
    return len(lots) + len(disposals) + len(flags)

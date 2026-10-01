"""Outcomes and the lot-ledger write (Phase 4 spec req 8 "Chain" and req 13;
plan T62).

Every order ends in exactly one outcome per kind it earns, **due** at the first
run on a session after its horizon's last session:

- **Horizon.** An order of rebalance i runs to close(T_{i+1}), T_i being its
  decision's `rebalance_session` (the last month-end session before the order's
  session when the decision has none or is not given); a forced exit
  (`phase = exit`) to its own session. In a window with a stop requested it ends
  at the **earliest** of that, the fill that makes the name flat (the first
  filled `exit` sell of the name on or after the request, marked at that order's
  average fill price), and close(S-1) for the first `stop` run on S whose step
  3 found the order terminal and the name flat apart from its residue
  (`OutcomeWindow.stop_flat`, per name, computed by the caller; a name never
  held is flat). That run on S writes the row, from marks known at close(S-1):
  a run never reads close(S), which is not known before the open.
- **Kinds.** A buy with fills: `position_return`, the horizon mark over the
  order's split-adjusted average fill price (over the accessor's live fills, so
  an implied residual enters at the order's average and a superseded fill never)
  less 1, with its contribution (the dollar gain over the equity of the last mark
  before the order's session). A sell with fills: `realised_pnl`, the dollars
  realised on the FIFO lots it closed (`execution.lots`), with its contribution.
  An order that did not fill in full (expired, rejected or cancelled): also
  `not_executed`, the name's return from close of the session before the order
  to the horizon (the cost of the miss), no contribution. A partial fill then
  expiry earns both. Returns are **price returns**: splits with an ex-date after
  the fill (or the start close) and on or before the horizon, from the caller's
  `live_actions_as_of(close(S-1))` frame, adjust the start; dividends are not
  added.
- **Prices.** The horizon's mark is the flattening fill's price, else the
  `positions_daily` mark of the name on that session, else `prices(security_id,
  session)` (the raw close). A missing price leaves the value and mark None.
  Equity is the `positions_daily` rows of one session: the cash of its row
  without a `security_id` plus every name's value; a missing cash row or value
  leaves the contribution None.

`due_outcomes` is pure. `write_outcomes_and_lots` is its I/O edge: it reads the
window's rows through `store.journal`, rebuilds the lot ledger over the
window's live fills, appends the due outcomes, and appends the rebuilt
`lots`, `disposals` and `wash_sale_flags` as one new set, stamped with one
clock reading, only when the set differs from the latest one journaled (so a
rebuild that changes nothing appends nothing, and the current ledger is the
set with the latest `known_at`). A changed set whose clock reading does not
advance past the latest set's is not appended (it would merge with it), is
reported like an error, and holds `realised_pnl` back like one. A
`LotLedgerError` (a sale beyond the fill-built holding: a split, spin-off
receipt, stock merger or carried residue, which lots do not take yet) never
fails the run: the writer passes its message to `on_lot_error` (the caller
alerts and dedupes), writes no lot rows and holds `realised_pnl` back until
the ledger rebuilds.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

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
    DecisionRow,
    DisposalRow,
    LotRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PositionDailyRow,
    WashSaleFlagRow,
)
from tradepartner.store.schema import atomic

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
_SPLIT = "split"
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OutcomeWindow:
    """The window as outcomes need it: its id; the session of its stop
    request; per name, the session S of the first `stop` run whose step 3 found
    the name flat apart from its residue (every name the caller knows is flat,
    including names never held); and the (client order id, kind) pairs already
    journaled."""

    window_id: int
    stop_requested: date | None = None
    stop_flat: Mapping[str, date] = field(default_factory=dict)
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
    """The last rebalance session (last session of a month) before `session`."""
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


def _split_factor(
    actions: pl.DataFrame | None, security_id: str, after: date, through: date
) -> float:
    """The product of the split ratios with `after < ex_date <= through`."""
    factor = 1.0
    if actions is None or actions.is_empty():
        return factor
    for row in actions.iter_rows(named=True):
        if (
            row["security_id"] == security_id
            and row["action_type"] == _SPLIT
            and after < row["ex_date"] <= through
        ):
            ratio = float(row["ratio_or_amount"])
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError(f"split of {security_id} has ratio {ratio}")
            factor *= ratio
    return factor


def _flattening(
    orders: Sequence[OrderRow],
    terminal: Mapping[str, str],
    fills: Sequence[OrderedFill],
    stop_requested: date | None,
) -> dict[str, tuple[date, float]]:
    """Per name, (session of the last fill, average fill price) of its first
    filled `exit` sell on or after the stop request."""
    if stop_requested is None:
        return {}
    by_order: dict[str, list[OrderedFill]] = {}
    for item in fills:
        by_order.setdefault(item.fill.client_order_id, []).append(item)
    found: dict[str, tuple[date, float]] = {}
    for order in sorted(orders, key=lambda o: (o.session, o.client_order_id)):
        own = by_order.get(order.client_order_id, [])
        quantity = sum(i.fill.quantity for i in own)
        if (
            order.phase == _EXIT
            and order.side == _SELL
            and order.session >= stop_requested
            and terminal.get(order.client_order_id) == _FILLED
            and quantity > 0
            and order.security_id not in found
        ):
            day = max(_local(i.fill.filled_at) for i in own)
            average = sum(i.fill.quantity * i.fill.price for i in own) / quantity
            found[order.security_id] = (day, average)
    return found


def _horizon(
    order: OrderRow,
    window: OutcomeWindow,
    flattening: Mapping[str, tuple[date, float]],
    rebalance_of: Mapping[int, date],
) -> tuple[date, float | None]:
    """(the horizon's last session, the flattening fill's price when that fill
    ends it)."""
    if order.phase == _EXIT:
        base = order.session
    else:
        rebalance = rebalance_of.get(order.decision_id) or _rebalance_before(order.session)
        base = _rebalance_after(rebalance)
    if window.stop_requested is None:
        return base, None
    # Ties go to the earlier entry: the flattening fill, then close(T_{i+1}).
    candidates: list[tuple[date, float | None]] = []
    flat = flattening.get(order.security_id)
    if flat is not None:
        candidates.append(flat)
    candidates.append((base, None))
    stop_run = window.stop_flat.get(order.security_id)
    if stop_run is not None and stop_run >= window.stop_requested:
        candidates.append((previous_session(stop_run), None))
    end = min(candidates, key=lambda c: c[0])
    # Never before the order's own session, whatever the caller's map says.
    return end if end[0] >= order.session else (order.session, None)


def _equity_before(marks: Sequence[PositionDailyRow], session: date) -> float | None:
    """Equity at the last mark before `session`: the cash of its row without a
    `security_id` plus every name's value, or None when either is missing."""
    earlier = [m.session for m in marks if m.session < session]
    if not earlier:
        return None
    rows = [m for m in marks if m.session == max(earlier)]
    cash = [m.cash for m in rows if m.security_id is None]
    values = [m.value for m in rows if m.security_id is not None]
    if len(cash) != 1 or cash[0] is None or any(v is None for v in values):
        return None
    return cash[0] + sum(v for v in values if v is not None)


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
    *,
    decisions: Sequence[DecisionRow] = (),
    actions: pl.DataFrame | None = None,
) -> list[Outcome]:
    """The outcomes due at a run on `session` and not yet written (module
    docstring). `fills` are `fills_for`'s live fills, `marks` the window's
    `positions_daily`, `lots` the `lots.rebuild` result over the same fills
    (None when it failed: `realised_pnl` then waits), `prices` the raw close of
    a name on a session (None when unknown), `decisions` the window's decisions
    (for T_i) and `actions` a `live_actions_as_of(close(S-1))` frame (for
    splits)."""
    terminal = _terminal_status(events)
    flattening = _flattening(orders, terminal, fills, window.stop_requested)
    rebalance_of = {
        d.decision_id: d.rebalance_session
        for d in decisions
        if d.decision_id is not None and d.rebalance_session is not None
    }
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
        through, fill_mark = _horizon(order, window, flattening, rebalance_of)
        if not session > through:
            continue
        security = order.security_id
        equity = _equity_before(marks, order.session)
        own = by_order.get(order.client_order_id, [])
        quantity = sum(item.fill.quantity for item in own)
        mark = fill_mark if fill_mark is not None else _mark(marks, prices, security, through)
        candidates: list[Outcome] = []
        if quantity > 0 and order.side == _BUY:
            cost = sum(i.fill.quantity * i.fill.price for i in own)
            shares = sum(
                i.fill.quantity
                * _split_factor(actions, security, _local(i.fill.filled_at), through)
                for i in own
            )
            average = cost / shares
            value = None if mark is None or average <= 0 else mark / average - 1
            gain = None if mark is None else mark * shares - cost
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
            before = previous_session(order.session)
            start = prices(security, before)
            if start:
                start /= _split_factor(actions, security, before, through)
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
    actions: pl.DataFrame | None = None,
    stop_requested: date | None = None,
    stop_flat: Mapping[str, date] | None = None,
) -> WriteResult:
    """Rebuild the lot ledger over the window's live fills, append the new set
    when it changed, and append the outcomes due at `session` (module
    docstring). `on_lot_error` receives a `LotLedgerError`'s message; the caller
    alerts. `actions` is the `live_actions_as_of(close(S-1))` frame and
    `stop_flat` the per-name first flat `stop` run session (`OutcomeWindow`).
    Every row is stamped with one reading of `clock`. The reads and every
    append run in one transaction (the caller's when one is open, which it
    then commits or rolls back), so a failure part-way leaves the previous
    lot set current and no outcome of this call; `on_lot_error` is called
    once the block has finished, so a lot error found before such a failure
    is not reported by this call (the next run finds it again)."""
    now = clock()
    with atomic(conn):
        fills = journal.fills_for(conn, window_id=window_id)
        orders = journal.orders_for(conn, window_id=window_id)
        events = journal.order_events_for(conn, window_id=window_id)
        marks = journal.positions_daily_for(conn, window_id)
        written = frozenset(
            (o.client_order_id, o.kind) for o in journal.outcomes_for(conn, window_id)
        )
        decisions = [d.decision for d in journal.decisions_for(conn, window_id)]

        ledger: LotLedger | None
        lot_error: str | None = None
        try:
            ledger = rebuild(fills, orders, assets, account)
        except LotLedgerError as exc:
            ledger, lot_error = None, str(exc)
        lot_rows = 0
        if ledger is not None:
            appended = _append_ledger(conn, ledger, now)
            if appended is None:
                lot_error = f"lot ledger changed but the clock ({now.isoformat()}) did not advance"
                ledger = None  # unsaved: realised P&L waits for a run that saves it
            else:
                lot_rows = appended

        window = OutcomeWindow(window_id, stop_requested, dict(stop_flat or {}), written)
        outcomes = due_outcomes(
            window,
            orders,
            events,
            fills,
            marks,
            ledger,
            prices,
            session,
            decisions=decisions,
            actions=actions,
        )
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
    if lot_error is not None:
        on_lot_error(lot_error)
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


def _append_ledger(conn: duckdb.DuckDBPyConnection, ledger: LotLedger, now: datetime) -> int | None:
    """Append the rebuilt set when it differs from the latest one; return the
    number of rows appended, or None when it differs but `now` is not after
    the latest set's stamp (appending would merge the two sets)."""
    lots, disposals, flags = ledger
    if not lots or _rebuilt_set(ledger) == _latest_set(conn):
        return 0
    latest = conn.execute("SELECT max(known_at) FROM lots").fetchone()
    if latest is not None and latest[0] is not None and now <= latest[0]:
        return None
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

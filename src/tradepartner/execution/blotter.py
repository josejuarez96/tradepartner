"""Read-only per-session order blotter with point-in-time open slippage (T258)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import session_close
from tradepartner.execution import slippage
from tradepartner.execution.plan import is_full_exit
from tradepartner.store import journal
from tradepartner.store.journal import DecisionRow, OrderedFill, OrderEventRow, OrderRow

_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class BlotterRow:
    """One journaled order and its live execution and plan details."""

    client_order_id: str
    symbol: str
    side: str
    quantity: float | None
    notional: float | None
    filled_quantity: float
    fill_price: float | None
    status: str
    decision: str
    reason: str | None
    signal_rank: int | None
    slippage_bp: float | None
    slippage_note: str | None


@dataclass(frozen=True)
class SessionBlotter:
    """Displayed rows and the full session's slippage summary."""

    rows: tuple[BlotterRow, ...]
    summary: slippage.Slippage
    capped: bool


def sessions(conn: duckdb.DuckDBPyConnection, window_id: int) -> tuple[date, ...]:
    """Order sessions of a window, newest first, including unfilled orders."""
    journal.require_journal(conn)
    rows = conn.execute(
        "SELECT DISTINCT o.session FROM orders o JOIN paper_runs r ON r.run_id = o.run_id "
        "WHERE r.window_id = ? ORDER BY o.session DESC",
        [window_id],
    ).fetchall()
    return tuple(row[0] for row in rows)


def _status(events: list[OrderEventRow]) -> str:
    """The first terminal state by journal knowledge time, else open."""
    terminal = sorted(
        (e for e in events if e.status in journal.TERMINAL_ORDER_STATUSES),
        key=lambda e: e.known_at,
    )
    return terminal[0].status if terminal else "open"


def _row(
    order: OrderRow,
    fills: list[OrderedFill],
    events: list[OrderEventRow],
    decision: tuple[str, str | None],
    rank: int | None,
    opens: dict[tuple[str, date], float],
    now: datetime,
) -> BlotterRow:
    quantity = sum(item.fill.quantity for item in fills)
    notional = sum(item.fill.quantity * item.fill.price for item in fills)
    price = notional / quantity if quantity > 0 else None
    slip: float | None = None
    note: str | None = None
    if session_close(order.session) > now:
        note = "pending"
    elif not fills:
        note = "no fill"
    elif any(item.fill.filled_at.astimezone(_NEW_YORK).date() != order.session for item in fills):
        note = "off session"
    elif price is None:
        # summarise counts a present order with non-positive volume as no_bar.
        note = "no bar"
    else:
        open_price = opens.get((order.security_id, order.session))
        if open_price is None or open_price <= 0:
            note = "no bar"
        else:
            slip = slippage.slippage_bp(order.side, price, open_price)
    return BlotterRow(
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        notional=order.notional,
        filled_quantity=quantity,
        fill_price=price,
        status=_status(events),
        decision=decision[0],
        reason=decision[1],
        signal_rank=rank,
        slippage_bp=slip,
        slippage_note=note,
    )


def session_blotter(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    session: date,
    now: datetime,
    limit: int,
) -> SessionBlotter:
    """Read one window's order session, capped to `limit` displayed rows.

    The summary covers every live fill of that session even when rows are capped.
    Open prices are read at the session's close, excluding later revisions.
    """
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("now must be timezone-aware UTC")
    if limit <= 0:
        raise ValueError("limit must be positive")
    session_close(session)  # Require a trading session even for an empty read.
    journal._require_orders_have_runs(conn)
    where = "t.session = ? AND t.run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?)"
    orders = journal._select(
        conn,
        OrderRow,
        where,
        [session, window_id],
        order="t.known_at DESC, t.client_order_id DESC",
        limit=limit + 1,
    )
    shown = orders[:limit]
    ids = [o.client_order_id for o in shown]
    # The summary covers the full session. Fetch its IDs by session in SQL,
    # then let the journal's sole fill reader validate and load only those IDs.
    session_ids = [
        row[0]
        for row in conn.execute(
            "SELECT o.client_order_id FROM orders o JOIN paper_runs r ON r.run_id = o.run_id "
            "WHERE r.window_id = ? AND o.session = ? ORDER BY o.client_order_id",
            [window_id, session],
        ).fetchall()
    ]
    fills = (
        journal.fills_for(conn, window_id=window_id, client_order_ids=session_ids)
        if session_ids
        else []
    )
    opens = slippage.read_opens(conn, fills) if fills else {}
    summary = slippage.summarise(fills, opens, now)
    events_by_id: dict[str, list[OrderEventRow]] = {}
    for event in journal.order_events_for(conn, window_id=window_id, client_order_ids=ids):
        events_by_id.setdefault(event.client_order_id, []).append(event)
    fills_by_id: dict[str, list[OrderedFill]] = {}
    for item in fills:
        fills_by_id.setdefault(item.fill.client_order_id, []).append(item)
    decision_ids = [o.decision_id for o in shown]
    decisions = {
        d.decision_id: d
        for d in journal._select(
            conn, DecisionRow, "list_contains(?, t.decision_id)", [decision_ids]
        )
    }
    for order in shown:
        if order.decision_id not in decisions:
            raise journal.JournalIntegrityError(
                f"order {order.client_order_id!r} has no decision {order.decision_id}"
            )
    # A catch-up order's run is not necessarily its plan's run. The decision
    # names that run; require its matching paper_plan before using its signal.
    rank_rows = conn.execute(
        "SELECT d.decision_id, s.rank FROM decisions d "
        "JOIN paper_plans p ON p.run_id = d.run_id "
        "AND p.rebalance_session = d.rebalance_session "
        "JOIN signals s ON s.run_id = p.run_id "
        "AND s.rebalance_session = p.rebalance_session "
        "AND s.security_id = d.security_id "
        "WHERE list_contains(?, d.decision_id) "
        "AND p.run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?)",
        [decision_ids, window_id],
    ).fetchall()
    ranks = {decision_id: rank for decision_id, rank in rank_rows}
    rows = tuple(
        _row(
            order,
            fills_by_id.get(order.client_order_id, []),
            events_by_id.get(order.client_order_id, []),
            (decisions[order.decision_id].decision, decisions[order.decision_id].reason),
            None if is_full_exit(decisions[order.decision_id]) else ranks.get(order.decision_id),
            opens,
            now,
        )
        for order in shown
    )
    return SessionBlotter(rows=rows, summary=summary, capped=len(orders) > limit)

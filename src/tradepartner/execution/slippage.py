"""Per-order implementation slippage against the session's SIP open (#1733).

This is implementation cost against arrival (the session's open bar). It is NOT
the backtest fill-timing term of `report.py` (`_fill_timing_term`, the gap between
the frozen `execution.fill_price` convention and the paper fill), and the two are
never folded together.

Definition. The unit is the ORDER, not the fill. Live fills are grouped by
`client_order_id`; an order's price is its volume-weighted average
`VWAP = sum(q * p) / sum(q)` (the broker's average, as `lots.py` merges an order).
This matters because a `price_implied` fill (the synthetic residual `paper resume`
writes, price `(filled * avg - journaled_notional) / residual`, possibly negative)
is not an execution price on its own; only the order-level VWAP is. The order's
slippage in basis points is `sign * (VWAP - open) / open * 10_000` with `sign` +1
for a buy and -1 for a sell, so adverse slippage (a buy above the open, a sell
below it) is positive. `open` is the raw (unadjusted) `prices_daily.open` of the
order's security on its journaled `orders.session` (never the NY date of
`filled_at`), read as of that session's close through `store.asof.prices_as_of`.
Point-in-time: a bar revised after the session close is never used.

Aggregation, per side and overall: the notional-weighted mean
`sum(notional * bp) / sum(notional)` (notional = executed `sum(q * p)`, the order's
dollar weight) is meant as the input to the measured cost level (T210) and kill-ladder
rung 1 (nothing consumes it yet);
the median and p90 (linear interpolation) are diagnostics. Excluded from the
statistics and counted apart, never a crash: `no_bar` (the session is complete but
has no bar, or the order has no positive volume), `pending` (the order's session
has not closed yet, so its bar may simply not be ingested) and `off_session` (a
fill whose NY date differs from its order's session, so the open is not its
arrival). Read-only: fills come only through `store.journal.fills_for`.

The measured cost (`paper cost`, T383; paper spec amendment 2026-11-07 #3118 item 3).
`measured_cost` is the same per-order statistic over one window, as the paper exam's
`costs.per_side_bps` is measured: the pooled notional-weighted mean of both sides,
rounded to the nearest 5 bp (`round_to_5bp`). Left out and counted: orders of a
`stop`-kind run (`paper_runs.kind`), and every other order on or after the window's
stop session (`stop_session_of` its earliest `paper_window_stops` stamp), because a
stop's liquidation is not a rebalance. Beside the pool, never instead of it: the
orders on sessions where a `month_end` book also had orders (#2886, its buys then wait
for `daily`'s). Point-in-time at `cutoff`: a fill, an order or a stop known after it is
not read, and each open is read as of its own session's close, which is at or before it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.backtest.frozen import frozen_values
from tradepartner.calendar import is_session, next_session, session_close
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.asof import prices_as_of
from tradepartner.store.journal import OrderedFill

_NEW_YORK = ZoneInfo("America/New_York")
_BP = 10_000.0
_P90 = 0.9


@dataclass(frozen=True)
class SideSlippage:
    """One group of orders: count, notional-weighted mean, median and p90 (bp);
    the statistics are None when the group is empty."""

    count: int
    wmean_bp: float | None
    median_bp: float | None
    p90_bp: float | None


@dataclass(frozen=True)
class Slippage:
    """A window's per-order slippage summary and its exclusion counts."""

    buy: SideSlippage
    sell: SideSlippage
    overall: SideSlippage
    no_bar: int
    off_session: int = 0
    pending: int = 0


@dataclass(frozen=True)
class _Order:
    client_order_id: str
    side: str
    security_id: str
    session: date
    quantity: float
    notional: float
    off_session: bool

    @property
    def vwap(self) -> float:
        return self.notional / self.quantity


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile of `values` (None when empty)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def slippage_bp(side: str, price: float, open_price: float) -> float:
    """Signed adverse-positive slippage in bp of one price against its open."""
    sign = 1.0 if side == "buy" else -1.0
    return sign * (price - open_price) / open_price * _BP


def _session_of(item: OrderedFill) -> date:
    """The order's journaled session; a fill that lacks it is refused, never guessed
    from the fill's own date."""
    if item.order_session is None:
        raise ValueError(
            f"fill {item.fill.fill_id} ({item.fill.broker_fill_id}) has no order session"
        )
    return item.order_session


def _orders(fills: Sequence[OrderedFill]) -> list[_Order]:
    """Group live fills into orders (VWAP inputs), in first-fill order."""
    grouped: dict[str, list[OrderedFill]] = {}
    for item in fills:
        grouped.setdefault(item.fill.client_order_id, []).append(item)
    orders: list[_Order] = []
    for order_id, items in grouped.items():
        first = items[0]
        session = _session_of(first)
        orders.append(
            _Order(
                client_order_id=order_id,
                side=first.side,
                security_id=first.security_id,
                session=session,
                quantity=sum(i.fill.quantity for i in items),
                notional=sum(i.fill.quantity * i.fill.price for i in items),
                off_session=any(
                    i.fill.filled_at.astimezone(_NEW_YORK).date() != session for i in items
                ),
            )
        )
    return orders


def _group(points: Sequence[tuple[float, float]]) -> SideSlippage:
    """`points` are `(bp, notional)`."""
    bps = [bp for bp, _ in points]
    total = sum(w for _, w in points)
    wmean = sum(bp * w for bp, w in points) / total if total > 0 else None
    return SideSlippage(len(points), wmean, quantile(bps, 0.5), quantile(bps, _P90))


def summarise(
    fills: Sequence[OrderedFill], opens: dict[tuple[str, date], float], now: datetime
) -> Slippage:
    """Pure: the per-order summary of `fills` against `opens` (keyed
    `(security_id, session)`) as of `now`."""
    by_side: dict[str, list[tuple[float, float]]] = {"buy": [], "sell": []}
    no_bar = off_session = pending = 0
    for order in _orders(fills):
        if session_close(order.session) > now:
            pending += 1
            continue
        if order.off_session:
            off_session += 1
            continue
        open_price = opens.get((order.security_id, order.session))
        if open_price is None or open_price <= 0.0 or order.quantity <= 0.0:
            no_bar += 1
            continue
        bp = slippage_bp(order.side, order.vwap, open_price)
        by_side.setdefault(order.side, []).append((bp, abs(order.notional)))
    return Slippage(
        buy=_group(by_side["buy"]),
        sell=_group(by_side["sell"]),
        overall=_group([p for pts in by_side.values() for p in pts]),
        no_bar=no_bar,
        off_session=off_session,
        pending=pending,
    )


def read_opens(
    conn: duckdb.DuckDBPyConnection, fills: Sequence[OrderedFill]
) -> dict[tuple[str, date], float]:
    """Raw open per `(security_id, session)` of `fills`' orders, each as of its
    session close."""
    wanted: dict[date, set[str]] = {}
    for item in fills:
        wanted.setdefault(_session_of(item), set()).add(item.security_id)
    opens: dict[tuple[str, date], float] = {}
    for session, securities in wanted.items():
        frame = prices_as_of(
            conn, session_close(session), sorted(securities), sessions_from=session
        ).filter(pl.col("session") == session)
        for row in frame.iter_rows(named=True):
            opens[(row["security_id"], session)] = float(row["open"])
    return opens


def _newest_sessions(fills: Sequence[OrderedFill], count: int) -> list[OrderedFill]:
    """`fills` restricted to those whose order session is among the newest `count`
    sessions present, in `fills`' order. The session set comes from the fills
    themselves, so no separate session read is made."""
    newest = set(sorted({_session_of(item) for item in fills}, reverse=True)[:count])
    return [item for item in fills if _session_of(item) in newest]


def window_slippage(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    now: datetime,
    *,
    sessions: int | None = None,
) -> Slippage:
    """The slippage summary of `window_id`'s live fills (read-only).

    `sessions` bounds the read to the newest `sessions` order sessions (#1741):
    the line is a diagnostic, and one `prices_as_of` read per session would grow
    with the window's length. `paper status` passes `paper.status_slippage_sessions`;
    `paper report` passes None and keeps the whole window.
    """
    fills = store_journal.fills_for(conn, window_id=window_id)
    if sessions is not None:
        fills = _newest_sessions(fills, sessions)
    return summarise(fills, read_opens(conn, fills), now)


def slippage_line(s: Slippage) -> str:
    """The one output line shared by `paper report` and `paper status`."""

    def part(name: str, g: SideSlippage) -> str:
        if g.wmean_bp is None or g.median_bp is None or g.p90_bp is None:
            return f"{name} n=0"
        return (
            f"{name} n={g.count} wmean {g.wmean_bp:.2f} median {g.median_bp:.2f} p90 {g.p90_bp:.2f}"
        )

    return (
        "slippage vs open (bp, adverse positive, per order): "
        f"{part('buy', s.buy)}; {part('sell', s.sell)}; {part('all', s.overall)}; "
        f"no bar {s.no_bar}; off session {s.off_session}; pending {s.pending}"
    )


def slippage_report_lines(s: Slippage | None, unavailable: str | None = None) -> list[str]:
    """The slippage diagnostic for `paper report`/`paper status` (#1741): the one
    summary line when it was computed, `slippage n/a (<reason>)` when the
    computation raised and the caller kept its row, or nothing when there is no
    summary and no reason to give."""
    if s is not None:
        return [slippage_line(s)]
    return [] if unavailable is None else [f"slippage n/a ({unavailable})"]


# --- the measured cost (`paper cost`, T383) --------------------------------------------

#: The step the figure to freeze is rounded to (#3118 item 3).
COST_ROUNDING_BP = 5.0
_STOP_KIND = "stop"
_MONTH_END = "month_end"
_CADENCE_KEY = "schedule.rebalance_cadence"


@dataclass(frozen=True)
class MeasuredCost:
    """One window's measured per-side cost: the pool (`slippage`: buys, sells, pooled
    and the counts apart), the orders on month-end sessions figured beside it, the
    orders left out, and the pooled figure rounded to 5 bp (None with no order)."""

    slippage: Slippage
    month_end: SideSlippage
    stop_run: int
    after_stop: int
    stop_session: date | None
    figure_bp: float | None


def round_to_5bp(value: float) -> float:
    """`value` to the nearest 5 bp; a tie rounds up, to the higher cost."""
    return math.floor(value / COST_ROUNDING_BP + 0.5) * COST_ROUNDING_BP + 0.0


def stop_session_of(stamp: datetime) -> date:
    """The first session a stop stamped at `stamp` reaches, read in New York: the
    session it falls in, or the next one when that session's close has passed (a stop
    requested in the evening first acts the next morning)."""
    day = stamp.astimezone(_NEW_YORK).date()
    if is_session(day) and stamp <= session_close(day):
        return day
    return next_session(day)


def _known_by(
    fills: Sequence[OrderedFill], cutoff: datetime, orders_known_at: Mapping[str, datetime]
) -> list[OrderedFill]:
    """`fills` known at or before `cutoff`, whose order was too."""
    return [
        item
        for item in fills
        if item.fill.known_at <= cutoff
        and orders_known_at.get(item.fill.client_order_id, item.fill.known_at) <= cutoff
    ]


def _cost_scope(
    fills: Sequence[OrderedFill],
    cutoff: datetime,
    *,
    stop_run_ids: frozenset[int],
    stop_session: date | None,
    orders_known_at: Mapping[str, datetime],
) -> tuple[list[OrderedFill], int, int]:
    """The fills measured, and the counts of orders left out by a stop run and by the
    stop session."""
    stop_run: set[str] = set()
    after_stop: set[str] = set()
    kept: list[OrderedFill] = []
    for item in _known_by(fills, cutoff, orders_known_at):
        order = item.fill.client_order_id
        if item.run_id in stop_run_ids:
            stop_run.add(order)
        elif stop_session is not None and _session_of(item) >= stop_session:
            after_stop.add(order)
        else:
            kept.append(item)
    return kept, len(stop_run), len(after_stop)


def measured_cost(
    fills: Sequence[OrderedFill],
    opens: dict[tuple[str, date], float],
    cutoff: datetime,
    *,
    stop_run_ids: frozenset[int],
    stop_at: datetime | None,
    month_end_sessions: frozenset[date],
    orders_known_at: Mapping[str, datetime],
) -> MeasuredCost:
    """Pure: the measured per-side cost of one window's `fills` as of `cutoff`
    (module docstring). `stop_at` is the window's earliest stop stamp known by then."""
    stop_session = None if stop_at is None else stop_session_of(stop_at)
    kept, stop_run, after_stop = _cost_scope(
        fills,
        cutoff,
        stop_run_ids=stop_run_ids,
        stop_session=stop_session,
        orders_known_at=orders_known_at,
    )
    pool = summarise(kept, opens, cutoff)
    month_end = summarise(
        [item for item in kept if _session_of(item) in month_end_sessions], opens, cutoff
    ).overall
    wmean = pool.overall.wmean_bp
    return MeasuredCost(
        slippage=pool,
        month_end=month_end,
        stop_run=stop_run,
        after_stop=after_stop,
        stop_session=stop_session,
        figure_bp=None if wmean is None else round_to_5bp(wmean),
    )


def _hypothesis_cadence(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> object:
    """A hypothesis's frozen `schedule.rebalance_cadence` (`frozen_values` reads a
    pre-lab registration as `month_end`)."""
    return frozen_values(registry.get_hypothesis_by_id(conn, hypothesis_id)).get(_CADENCE_KEY)


def _month_end_sessions(
    conn: duckdb.DuckDBPyConnection, window_id: int, cutoff: datetime
) -> frozenset[date]:
    """The sessions on which another window, of a `month_end` hypothesis, had an order
    known by `cutoff`."""
    others = conn.execute(
        "SELECT window_id, hypothesis_id FROM paper_windows WHERE window_id <> ? "
        "ORDER BY window_id",
        [window_id],
    ).fetchall()
    sessions: set[date] = set()
    for other, hypothesis_id in others:
        if _hypothesis_cadence(conn, int(hypothesis_id)) != _MONTH_END:
            continue
        sessions.update(
            o.session
            for o in store_journal.orders_for(conn, window_id=int(other))
            if o.known_at <= cutoff
        )
    return frozenset(sessions)


def window_book(conn: duckdb.DuckDBPyConnection, window_id: int) -> str | None:
    """The book of window `window_id`, or None when there is no such window."""
    row = conn.execute(
        "SELECT book_id FROM paper_windows WHERE window_id = ?", [window_id]
    ).fetchone()
    return None if row is None else str(row[0])


def window_cost(conn: duckdb.DuckDBPyConnection, window_id: int, cutoff: datetime) -> MeasuredCost:
    """The measured cost of `window_id` from the journal and bars known by `cutoff`
    (read-only; `paper cost`)."""
    fills = store_journal.fills_for(conn, window_id=window_id)
    stop_run_ids = frozenset(
        r.run.run_id
        for r in store_journal.runs_for(conn, window_id)
        if r.run.kind == _STOP_KIND and r.run.run_id is not None
    )
    stamps = [s.at for s in store_journal.window_stops_for(conn, window_id) if s.known_at <= cutoff]
    orders_known_at = {
        o.client_order_id: o.known_at for o in store_journal.orders_for(conn, window_id=window_id)
    }
    stop_at = min(stamps) if stamps else None
    stop_session = None if stop_at is None else stop_session_of(stop_at)
    kept, _, _ = _cost_scope(
        fills,
        cutoff,
        stop_run_ids=stop_run_ids,
        stop_session=stop_session,
        orders_known_at=orders_known_at,
    )
    closed = [item for item in kept if session_close(_session_of(item)) <= cutoff]
    return measured_cost(
        fills,
        read_opens(conn, closed),
        cutoff,
        stop_run_ids=stop_run_ids,
        stop_at=stop_at,
        month_end_sessions=_month_end_sessions(conn, window_id, cutoff),
        orders_known_at=orders_known_at,
    )

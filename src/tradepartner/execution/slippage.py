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
dollar weight) is THE input to the measured cost level and kill-ladder rung 1;
the median and p90 (linear interpolation) are diagnostics. Excluded from the
statistics and counted apart, never a crash: `no_bar` (the session is complete but
has no bar, or the order has no positive volume), `pending` (the order's session
has not closed yet, so its bar may simply not be ingested) and `off_session` (a
fill whose NY date differs from its order's session, so the open is not its
arrival). Read-only: fills come only through `store.journal.fills_for`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.calendar import session_close
from tradepartner.store import journal as store_journal
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
    return item.order_session or item.fill.filled_at.astimezone(_NEW_YORK).date()


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


def window_slippage(conn: duckdb.DuckDBPyConnection, window_id: int, now: datetime) -> Slippage:
    """The slippage summary of every live fill of `window_id` (read-only)."""
    fills = store_journal.fills_for(conn, window_id=window_id)
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

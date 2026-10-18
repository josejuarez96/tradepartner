"""Per-fill slippage against the session's SIP open bar (#1733; spec req 10/12).

Definition. For each live fill of a book's window, the signed slippage in basis
points is `sign * (fill.price - open) / open * 10_000`, where `open` is the raw
(unadjusted) `prices_daily.open` of the fill's security on the fill's session
(the New York date of `filled_at`), read as of that session's close
(`store.asof.prices_as_of`; fills are in raw prices, so the bar must be too,
exactly as `report._price_of` reads it) and `sign` is +1 for a buy and -1 for a
sell, so adverse slippage (a buy above the open, a sell below it) is positive.
Median and p90 (linear interpolation) are per side and overall, over fills that
have a bar; a fill with no (or a non-positive) open is counted in `no_bar`, never
a crash. Read-only: fills come through `store.journal.fills_for` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
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
    """Median and p90 (bp) of one group of fills; None when the group is empty."""

    count: int
    median_bp: float | None
    p90_bp: float | None


@dataclass(frozen=True)
class Slippage:
    """A window's slippage summary: per side, overall, and fills with no bar."""

    buy: SideSlippage
    sell: SideSlippage
    overall: SideSlippage
    no_bar: int


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
    """Signed adverse-positive slippage in bp of one fill against its open."""
    sign = 1.0 if side == "buy" else -1.0
    return sign * (price - open_price) / open_price * _BP


def _group(values: Sequence[float]) -> SideSlippage:
    return SideSlippage(len(values), quantile(values, 0.5), quantile(values, _P90))


def summarise(fills: Sequence[OrderedFill], opens: dict[tuple[str, date], float]) -> Slippage:
    """Pure: the summary of `fills` against `opens`, keyed `(security_id, session)`."""
    by_side: dict[str, list[float]] = {"buy": [], "sell": []}
    no_bar = 0
    for item in fills:
        open_price = opens.get((item.security_id, fill_session_of(item)))
        if open_price is None or open_price <= 0.0:
            no_bar += 1
            continue
        by_side.setdefault(item.side, []).append(
            slippage_bp(item.side, item.fill.price, open_price)
        )
    return Slippage(
        buy=_group(by_side["buy"]),
        sell=_group(by_side["sell"]),
        overall=_group([v for vs in by_side.values() for v in vs]),
        no_bar=no_bar,
    )


def fill_session_of(item: OrderedFill) -> date:
    """The New York session date of the fill."""
    return item.fill.filled_at.astimezone(_NEW_YORK).date()


def read_opens(
    conn: duckdb.DuckDBPyConnection, fills: Sequence[OrderedFill]
) -> dict[tuple[str, date], float]:
    """Raw open per `(security_id, session)` of `fills`, each as of its session close."""
    wanted: dict[date, set[str]] = {}
    for item in fills:
        wanted.setdefault(fill_session_of(item), set()).add(item.security_id)
    opens: dict[tuple[str, date], float] = {}
    for session, securities in wanted.items():
        frame = prices_as_of(
            conn, session_close(session), sorted(securities), sessions_from=session
        ).filter(pl.col("session") == session)
        for row in frame.iter_rows(named=True):
            opens[(row["security_id"], session)] = float(row["open"])
    return opens


def window_slippage(conn: duckdb.DuckDBPyConnection, window_id: int) -> Slippage:
    """The slippage summary of every live fill of `window_id` (read-only)."""
    fills = store_journal.fills_for(conn, window_id=window_id)
    return summarise(fills, read_opens(conn, fills))


def slippage_line(s: Slippage) -> str:
    """The one output line shared by `paper report` and `paper status`."""

    def part(name: str, g: SideSlippage) -> str:
        if g.median_bp is None or g.p90_bp is None:
            return f"{name} n=0"
        return f"{name} n={g.count} median {g.median_bp:.2f} p90 {g.p90_bp:.2f}"

    return (
        "slippage vs open (bp, adverse positive): "
        f"{part('buy', s.buy)}; {part('sell', s.sell)}; {part('all', s.overall)}; "
        f"no bar {s.no_bar}"
    )

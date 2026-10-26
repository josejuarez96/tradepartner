"""A book's holdings and what each is up or down since bought (plan T272, #2170).

Pure view-model functions, no Streamlit and no clock. `build_holdings` takes the
window's rows (fills, orders, adjustments and `positions_daily` marks, each read
once); `read_holdings` is the one function that reads the journal, four reads
for the whole window, never one per name.

- **Value** and **quantity** are the latest marked session's `positions_daily`
  row (the ledger's split-adjusted holding at that session's close).
- **Cost basis** is the sum over the name's open lots of what `lots.rebuild`
  holds, first in, first out: `lots.open_lots` reports each lot's open basis as
  the ledger holds it (#2247). The rebuild is stated `through` the marked
  session and over the fills filled by that session's close only, so a lot
  opened after the session is never counted, and it reads splits and the other
  adjustments, so a name that split between the buy and the mark is read in
  today's share units, not the raw fill's.
- **Gain** is value minus basis in dollars and over basis in percent; a short
  position is labelled `short`, its basis is the proceeds of the open short lots
  and its gain is basis minus value.
- **Change today** is the mark against the prior marked session's mark of the
  same name and side (price change times today's quantity; None for a name with
  no prior mark, such as one bought today). It applies the move to today's
  whole quantity, shares bought today included, and divides the prior price by
  a split between the two sessions.
- A name whose open lots hold a different quantity than the mark (beyond
  `risk.reconcile_quantity_tolerance`) shows `QUANTITY_NOTE` with its value and
  no gain or change, and is left out of the rankings.
- A mark with no price (`mark_price` None) is shown as "no price yet" with no
  value, gain or change, and is left out of the up and down counts and the
  rankings, never counted as zero.

Tickers are the order's broker symbol, else a listing's through
`execution.symbols.broker_symbol`; the security id is never shown.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.lots import SHORT, LedgerAccount, open_lots
from tradepartner.execution.symbols import broker_symbol
from tradepartner.store import journal
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.journal import AdjustmentRow, OrderedFill, OrderRow, PositionDailyRow
from tradepartner.store.schema import LONG

#: What a priced-less holding shows in place of a number.
NO_PRICE_NOTE = "no price yet"
#: Shown when the open lots and the mark disagree on the quantity held.
QUANTITY_NOTE = "lots do not match the holding"
#: Shown for a name whose ticker is unknown; the security id is never shown.
UNKNOWN_TICKER = "unknown"
_ACCOUNT = LedgerAccount(account_id="book", account_type="paper", owner="self")


@dataclass(frozen=True)
class HoldingRow:
    """One held name. `side` is `long` or `short`. Every number after `quantity`
    is None for a name with no price (`note` is then `NO_PRICE_NOTE`); the
    percents are percents (9.43 is 9.43 percent), None when the base is zero."""

    ticker: str
    side: str
    quantity: float
    cost_basis: float
    value: float | None
    gain: float | None
    gain_percent: float | None
    change_today: float | None
    change_today_percent: float | None
    note: str | None = None


@dataclass(frozen=True)
class HoldingsSummary:
    """The book's totals over its priced holdings. `count` is every holding,
    priced or not; `cash` is None when the book has no mark. The rankings hold
    at most `top_n` rows: `largest` by value, `best` and `worst` by dollar gain
    (only the names actually up, only the names actually down)."""

    count: int
    cash: float | None
    up_today: int
    down_today: int
    largest: tuple[HoldingRow, ...]
    best: tuple[HoldingRow, ...]
    worst: tuple[HoldingRow, ...]


@dataclass(frozen=True)
class Holdings:
    """The holdings as of the marked `session` (None when the book has no mark),
    largest value first, unpriced names last."""

    session: date | None
    rows: tuple[HoldingRow, ...]
    summary: HoldingsSummary


def ticker_map(
    orders: Sequence[OrderRow],
    listings: Mapping[str, Mapping[str, Any]] | None = None,
    class_symbols: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """`security_id` to ticker: a name's order symbol (already the broker form),
    else its listing row (`ticker`, `exchange`) through `broker_symbol`, else its
    raw ticker."""
    tickers: dict[str, str] = {}
    for security_id, row in (listings or {}).items():
        tickers[security_id] = broker_symbol(row, class_symbols) or str(row["ticker"])
    for order in orders:
        tickers[order.security_id] = order.symbol
    return tickers


def build_holdings(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    adjustments: Sequence[AdjustmentRow],
    marks: Sequence[PositionDailyRow],
    as_of: date,
    *,
    actions: pl.DataFrame | None = None,
    tickers: Mapping[str, str] | None = None,
    top_n: int = 5,
    quantity_tolerance: float | None = None,
) -> Holdings:
    """The holdings at the latest marked session on or before `as_of` (module
    docstring). `actions` is the `live_actions_as_of(close(S-1))` frame, the
    splits the lot ledger applies. Raises `LotLedgerError` when the fills cannot
    form a lot ledger, and `ValueError` for a `top_n` below 1."""
    if top_n < 1:
        raise ValueError(f"top_n is {top_n}, must be at least 1")
    marked = sorted({m.session for m in marks if m.session <= as_of})
    if not marked:
        return Holdings(None, (), HoldingsSummary(0, None, 0, 0, (), (), ()))
    session = marked[-1]
    prior_session = marked[-2] if len(marked) > 1 else None
    names = {**ticker_map(orders), **(tickers or {})}

    cutoff = session_close(session)
    seen = [f for f in fills if f.fill.filled_at <= cutoff]
    held = open_lots(
        seen,
        orders,
        {},
        _ACCOUNT,
        adjustments=[a for a in adjustments if a.session <= session],
        actions=actions,
        through=session,
        symbols=names,
    )
    open_basis: dict[tuple[str, str], float] = defaultdict(float)
    open_quantity: dict[tuple[str, str], float] = defaultdict(float)
    for lot in held:
        open_basis[(lot.security_id, lot.position_side)] += lot.basis
        open_quantity[(lot.security_id, lot.position_side)] += lot.quantity
    tolerance = (
        RiskConfig().reconcile_quantity_tolerance
        if quantity_tolerance is None
        else quantity_tolerance
    )

    prior_price: dict[tuple[str, str], float | None] = {
        (m.security_id, m.position_side): _in_todays_units(
            m.mark_price, m.security_id, prior_session, session, actions
        )
        for m in marks
        if m.session == prior_session and m.security_id is not None
    }
    today = [m for m in marks if m.session == session and m.security_id is not None]
    rows = [
        _row(m, names, open_basis, open_quantity, tolerance, prior_price)
        for m in today
        if m.quantity != 0
    ]
    rows.sort(key=lambda r: (r.value is None, -(r.value or 0.0), r.ticker))
    cash = next((m.cash for m in marks if m.session == session and m.cash is not None), None)
    return Holdings(session, tuple(rows), _summary(rows, cash, top_n))


def _in_todays_units(
    price: float | None,
    security_id: str,
    prior_session: date | None,
    session: date,
    actions: pl.DataFrame | None,
) -> float | None:
    """The prior session's `price` per share in `session`'s units: divided by
    every split ratio of the name with `prior_session < ex_date <= session`, so
    an ex-date never shows as a crash."""
    if price is None or prior_session is None or actions is None or actions.is_empty():
        return price
    splits = actions.filter(
        (pl.col("security_id") == security_id)
        & (pl.col("action_type") == "split")
        & (pl.col("ex_date") > prior_session)
        & (pl.col("ex_date") <= session)
    )
    for ratio in splits["ratio_or_amount"].to_list():
        price /= float(ratio)
    return price


def read_holdings(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    as_of: date,
    *,
    actions: pl.DataFrame | None = None,
    listings: Mapping[str, Mapping[str, Any]] | None = None,
    class_symbols: Mapping[str, str] | None = None,
    top_n: int = 5,
) -> Holdings:
    """`build_holdings` over the window's journal: the fills, orders,
    adjustments and marks, each read once (no per-name query), nothing written.
    `actions` defaults to the splits known at close(S-1) of the marked session S
    (`live_actions_as_of`), the cut the mark was written under. Fills and marks
    are the live rows now, not cut by `known_at`, so a past `as_of` shows a
    later correction in its corrected form."""
    fills = journal.fills_for(conn, window_id=window_id)
    orders = journal.orders_for(conn, window_id=window_id)
    adjustments = journal.adjustments_for(conn, window_id, through_session=as_of)
    marks = journal.positions_daily_for(conn, window_id)
    marked = sorted({m.session for m in marks if m.session <= as_of})
    if actions is None and marked:
        # The splits known at close(S-1), the cut the mark itself was written under.
        actions = live_actions_as_of(conn, session_close(previous_session(marked[-1])))
    return build_holdings(
        fills,
        orders,
        adjustments,
        marks,
        as_of,
        actions=actions,
        tickers=ticker_map(orders, listings, class_symbols),
        top_n=top_n,
    )


def _row(
    mark: PositionDailyRow,
    names: Mapping[str, str],
    open_basis: Mapping[tuple[str, str], float],
    open_quantity: Mapping[tuple[str, str], float],
    tolerance: float,
    prior_price: Mapping[tuple[str, str], float | None],
) -> HoldingRow:
    assert mark.security_id is not None
    short = mark.position_side == SHORT
    side = SHORT if short else LONG
    key = (mark.security_id, side)
    raw = open_basis.get(key, 0.0)
    basis = -raw if short else raw  # a short lot's basis is minus its sale's value
    quantity = abs(mark.quantity)
    ticker = names.get(mark.security_id, UNKNOWN_TICKER)
    price = mark.mark_price
    if price is None or mark.value is None:
        return HoldingRow(
            ticker, side, quantity, basis, None, None, None, None, None, NO_PRICE_NOTE
        )
    value = abs(mark.value)
    if abs(open_quantity.get(key, 0.0) - quantity) > tolerance:
        # The lots and the mark disagree on the shares held: no gain is stated.
        return HoldingRow(
            ticker, side, quantity, basis, value, None, None, None, None, QUANTITY_NOTE
        )
    gain = basis - value if short else value - basis
    gain_percent = gain / basis * 100.0 if basis != 0 else None
    before = prior_price.get((mark.security_id, mark.position_side))
    change: float | None = None
    change_percent: float | None = None
    if before is not None and before != 0:
        change = quantity * (price - before) * (-1.0 if short else 1.0)
        change_percent = (price - before) / before * 100.0 * (-1.0 if short else 1.0)
    return HoldingRow(
        ticker, side, quantity, basis, value, gain, gain_percent, change, change_percent
    )


def _summary(rows: Sequence[HoldingRow], cash: float | None, top_n: int) -> HoldingsSummary:
    priced = [r for r in rows if r.value is not None and r.gain is not None]
    up = sum(1 for r in priced if r.change_today is not None and r.change_today > 0)
    down = sum(1 for r in priced if r.change_today is not None and r.change_today < 0)
    best = sorted(
        (r for r in priced if (r.gain or 0.0) > 0), key=lambda r: (-(r.gain or 0.0), r.ticker)
    )
    worst = sorted(
        (r for r in priced if (r.gain or 0.0) < 0), key=lambda r: ((r.gain or 0.0), r.ticker)
    )
    largest = sorted(priced, key=lambda r: (-(r.value or 0.0), r.ticker))
    return HoldingsSummary(
        count=len(rows),
        cash=cash,
        up_today=up,
        down_today=down,
        largest=tuple(largest[:top_n]),
        best=tuple(best[:top_n]),
        worst=tuple(worst[:top_n]),
    )

"""Today's trading activity in plain sentences (T271, amendment #2170).

`summarise` is pure over `blotter.BlotterRow`s: per side the names that filled,
the dollars, an Eastern-time range, the full exits and the new names, a
sentence per order that did not fill, and the one-sentence digest Home shows.
`today` is the only reader: it asks `blotter.session_blotter` for the session
as the journal knew it at `as_of`, the held names before the session and the
plan row, then calls `summarise`. Tickers only, never an order or decision id.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.execution import blotter
from tradepartner.store import journal
from tradepartner.store.journal import PaperPlanRow

EASTERN = ZoneInfo("America/New_York")
NO_TRADES = "No trades today"
NEEDS_A_LOOK = "needs a look"

# Order status -> the owner's words for why the order is not a fill. Every
# status the journal can hold (`order_events.status`, plus the blotter's "open").
STATUS_SENTENCES: Mapping[str, str] = {
    "rejected": "the broker turned it down",
    "cancelled": "it was cancelled",
    "expired": "it expired without filling",
    "open": "it is still open",
    "pending": "it has not reached the broker yet",
    "accepted": "the broker has it but it has not filled",
    "replay": "the broker has it but it has not filled",
    "cancel_requested": "a cancel was asked for and has not settled",
    "cancel_noop": "a cancel was asked for but changed nothing",
    "cancel_failed": "a cancel was asked for and failed",
    "pending_unresolved": "the broker has not said what happened to it",
}
_SIDE_WORDS = {"sell": ("Sold", "sell"), "buy": ("Bought", "buy")}


@dataclass(frozen=True)
class SideActivity:
    """One side's filled orders: names, dollars, ET range, exits, new names."""

    side: str
    count: int
    dollars: float
    time_range: str | None
    full_exits: tuple[str, ...]
    new_names: tuple[str, ...]
    sentence: str | None


@dataclass(frozen=True)
class OrderProblem:
    """One order that did not fill, in a sentence; `detail` keeps the raw status."""

    symbol: str
    side: str
    sentence: str
    detail: str
    part_filled: bool = False


@dataclass(frozen=True)
class TodayActivity:
    """The session's activity: the digest, both sides and each problem order."""

    digest: str
    sells: SideActivity
    buys: SideActivity
    problems: tuple[OrderProblem, ...]


def _clock(moment: datetime, tz: ZoneInfo) -> tuple[str, str]:
    local = moment.astimezone(tz)
    return f"{local.hour % 12 or 12}:{local.minute:02d}", "AM" if local.hour < 12 else "PM"


def time_range(first: datetime, last: datetime, tz: ZoneInfo = EASTERN) -> str:
    """`"9:31 to 9:34 AM ET"` (or `"11:58 AM to 12:03 PM ET"`), in `tz`, never UTC."""
    if first.tzinfo is None or last.tzinfo is None:
        raise ValueError("fill times must be timezone-aware")
    (start, start_half), (end, end_half) = _clock(first, tz), _clock(last, tz)
    label = tz.key.rsplit("/", 1)[-1] if tz is not EASTERN else "ET"
    if (start, start_half) == (end, end_half):
        return f"{start} {start_half} {label}"
    if start_half == end_half:
        return f"{start} to {end} {end_half} {label}"
    return f"{start} {start_half} to {end} {end_half} {label}"


def _dollars(amount: float) -> str:
    return f"${amount:,.0f}"


def _names(count: int) -> str:
    return f"{count} name" if count == 1 else f"{count} names"


def _side(
    side: str,
    rows: list[blotter.BlotterRow],
    held_before: Collection[str] | None,
    tz: ZoneInfo,
) -> SideActivity:
    filled = [
        r for r in rows if r.side == side and r.filled_quantity > 0 and r.fill_price is not None
    ]
    symbols = sorted({r.symbol for r in filled})
    dollars = sum(r.filled_quantity * (r.fill_price or 0.0) for r in filled)
    firsts = [r.first_fill_at for r in filled if r.first_fill_at is not None]
    lasts = [r.last_fill_at for r in filled if r.last_fill_at is not None]
    span = time_range(min(firsts), max(lasts), tz) if firsts and lasts else None
    held = set(held_before or ())
    exits = sorted({r.symbol for r in filled if r.full_exit})
    new = (
        sorted(
            {
                r.symbol
                for r in filled
                if r.side == "buy" and not r.full_exit and (r.security_id or r.symbol) not in held
            }
        )
        if held_before is not None
        else []
    )
    verb = _SIDE_WORDS[side][0]
    sentence = None
    if symbols:
        sentence = f"{verb} {_names(len(symbols))} for {_dollars(dollars)}"
        if span:
            sentence += f", {span}"
        if exits:
            sentence += f"; fully out of {', '.join(exits)}"
        if new:
            sentence += f"; new: {', '.join(new)}"
        sentence += "."
    return SideActivity(
        side=side,
        count=len(symbols),
        dollars=dollars,
        time_range=span,
        full_exits=tuple(exits),
        new_names=tuple(new),
        sentence=sentence,
    )


def _problem(row: blotter.BlotterRow) -> OrderProblem | None:
    if row.status == "filled" and row.filled_quantity > 0:
        return None
    if row.filled_quantity > 0 and row.status == "open":
        return None  # part filled and still working: its fills are counted
    reason = STATUS_SENTENCES.get(row.status, NEEDS_A_LOOK)
    word = _SIDE_WORDS.get(row.side, (row.side, row.side))[1]
    if row.filled_quantity > 0:
        return OrderProblem(
            symbol=row.symbol,
            side=row.side,
            sentence=f"The {word} of {row.symbol} part filled, then {reason}.",
            detail=row.status,
            part_filled=True,
        )
    return OrderProblem(
        symbol=row.symbol,
        side=row.side,
        sentence=f"The {word} of {row.symbol} did not fill: {reason}.",
        detail=row.status,
    )


def _digest(sells: SideActivity, buys: SideActivity, problems: int) -> str:
    parts = [
        f"{_SIDE_WORDS[s.side][0]} {_names(s.count)} for {_dollars(s.dollars)}"
        for s in (sells, buys)
        if s.count
    ]
    # "Sold 3 names for $12,400, bought 4 for $11,900": the second clause drops "names".
    if len(parts) == 2:
        parts[1] = f"bought {buys.count} for {_dollars(buys.dollars)}"
    text = ", ".join(parts) if parts else "Nothing filled"
    if problems:
        noun = "order" if problems == 1 else "orders"
        text += f"; {problems} {noun} did not fill"
    return text


def _no_trades(plan: PaperPlanRow | None) -> str:
    if plan is not None and plan.n_targets == 0:
        return f"{NO_TRADES}: the plan holds no names."
    if plan is not None and plan.n_orders_below_min_at_live_capital > 0:
        return f"{NO_TRADES}: every planned order was below the minimum size."
    return NO_TRADES


def summarise(
    rows: Collection[blotter.BlotterRow],
    tz: ZoneInfo = EASTERN,
    *,
    held_before: Collection[str] | None = None,
    plan: PaperPlanRow | None = None,
) -> TodayActivity:
    """Plain-sentence activity of one session's blotter rows.

    `held_before` is the security ids held before the session (None: unknown, so no
    name is called new). `plan` is the session's plan row, used only to say why
    a session with no orders had none.
    """
    ordered = sorted(rows, key=lambda r: (r.symbol, r.client_order_id))
    sells = _side("sell", ordered, held_before, tz)
    buys = _side("buy", ordered, held_before, tz)
    problems = tuple(p for p in map(_problem, ordered) if p is not None)
    digest = (
        _digest(sells, buys, sum(not p.part_filled for p in problems))
        if ordered
        else _no_trades(plan)
    )
    return TodayActivity(digest=digest, sells=sells, buys=buys, problems=problems)


def _held_before(
    conn: duckdb.DuckDBPyConnection, window_id: int, session: date, as_of: datetime
) -> set[str]:
    """Security ids held at the last mark before `session` known by `as_of`."""
    marks = [
        m
        for m in journal.positions_daily_for(conn, window_id)
        if m.session < session and m.known_at <= as_of
    ]
    if not marks:
        return set()
    last = max(m.session for m in marks)
    latest: dict[str, journal.PositionDailyRow] = {}
    for mark in sorted((m for m in marks if m.session == last), key=lambda m: m.known_at):
        if mark.security_id is not None:
            latest[mark.security_id] = mark
    return {sid for sid, m in latest.items() if m.quantity != 0}


def today(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    session: date,
    as_of: datetime,
    *,
    limit: int,
    tz: ZoneInfo = EASTERN,
) -> TodayActivity:
    """Read `session`'s activity as the journal knew it at `as_of` (UTC).

    Orders, events and fills learned of after `as_of` are left out. Read-only.
    """
    if as_of.tzinfo is None or as_of.utcoffset() != UTC.utcoffset(as_of):
        raise ValueError("as_of must be timezone-aware UTC")
    book = blotter.session_blotter(conn, window_id, session, as_of, limit, known_by=as_of)
    if book.capped:
        raise ValueError(f"session {session} has more than {limit} orders; raise limit")
    held_ids = _held_before(conn, window_id, session, as_of)
    plans = [
        p
        for p in journal._select(
            conn,
            PaperPlanRow,
            "t.rebalance_session = ? AND t.run_id IN "
            "(SELECT run_id FROM paper_runs WHERE window_id = ?) AND t.known_at <= ?",
            [session, window_id, as_of],
            order="t.known_at DESC",
        )
    ]
    return summarise(book.rows, tz, held_before=held_ids, plan=plans[0] if plans else None)

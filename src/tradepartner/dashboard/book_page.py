"""The Book page (T276, #2170): one book's header, chart, Today and holdings.

It draws `book_view.build(conn, settings, book, now)` (T279) and `home_view
.strategy_sentence` (T273) and computes nothing: every number, sentence and
series comes from those two. It reads through the shell's one read-only
connection, opens no other and writes nothing; `app.py` routes it unchanged.

**Which book.** A `book` query parameter (the Home card's link, `?book=<token>`)
wins when the store has that book and the link is new this session; otherwise the
picker's choice stands; with neither, the one open book, else the paper default.
The picker appears only when the store holds more than one book.

**Plain text.** The rendered text carries no window or trial id, no raw status
word and no UTC time: the status is a pill (Trading, Halted, Closed), times are
the view model's Eastern sentences, and dates are weekday and day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from tradepartner.config import Settings
from tradepartner.dashboard import theme
from tradepartner.execution import book_holdings, book_stats, book_view, home_view, ops
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.store.journal import JournalNotInitialised
from tradepartner.store.schema import SchemaVersionError

BOOK_KEY = "book_page_book"
RANGE_KEY = "book_page_range"
_LINK_SEEN_KEY = "book_page_link_seen"
#: The Trades page's own book picker key; the "see all" link sets it, then opens that page.
TRADES_BOOK_KEY = "trades_book"
TRADES_PAGE = "trades"

_RANGES: dict[str, str] = {"Since start": "since_start", "1M": "1M", "3M": "3M"}
_DEFAULT_RANGE = "Since start"
_PILL: dict[str, tuple[str, theme.Status]] = {
    "running": ("Trading", "good"),
    "stopped": ("Halted", "critical"),
    "closed": ("Closed", "warning"),
}
_BOOK_LINE = "Book"
_SPY_LINE = "SPY"
_TERM_HELP = {
    "best": "The day the book rose the most from the day before.",
    "worst": "The day the book fell the most from the day before.",
    "drawdown": "The biggest fall from a high.",
    "early": "Too few rebalances have happened to say whether the strategy works.",
}


def _now() -> datetime:
    """The as-of time of one render (a seam for tests)."""
    return datetime.now(UTC)


def _day(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def _short_day(day: date) -> str:
    return f"{day:%b} {day.day}"


def _money(amount: float) -> str:
    return f"{'-' if amount < 0 else ''}${abs(amount):,.2f}"


def _signed_money(amount: float) -> str:
    return f"{'-' if amount < 0 else '+'}${abs(amount):,.2f}"


def _pct(value: float | None) -> str:
    return "not yet" if value is None else f"{value:+.2f}%"


def _choose_book(
    summaries: tuple[ops.BookSummary, ...], settings: Settings
) -> ops.BookSummary | None:
    """The shown book: a new link's book, the picker's, else the one open book."""
    if not summaries:
        return None
    tokens = [s.book_id for s in summaries]
    linked = st.query_params.get("book")
    if linked in tokens and st.session_state.get(_LINK_SEEN_KEY) != linked:
        st.session_state[BOOK_KEY] = linked
        st.session_state[_LINK_SEEN_KEY] = linked
    open_books = [s.book_id for s in summaries if s.is_open]
    if len(open_books) == 1:
        default = open_books[0]
    elif settings.paper.book_id in tokens:
        default = settings.paper.book_id
    else:
        default = tokens[0]
    if len(tokens) == 1:
        chosen = tokens[0]
    else:
        if st.session_state.get(BOOK_KEY) not in tokens:
            st.session_state[BOOK_KEY] = default
        chosen = str(st.selectbox("Book", tokens, key=BOOK_KEY))
    return next(s for s in summaries if s.book_id == chosen)


def _header(
    conn: duckdb.DuckDBPyConnection, summary: ops.BookSummary, view: book_view.BookView
) -> None:
    head = view.header
    assert head is not None
    st.header(summary.book_id)
    label, status = _PILL[head.status]
    theme.status_badge(label, status)
    st.write(home_view.strategy_sentence(conn, summary))
    if head.as_of_session is None or head.value is None:
        st.caption("No day has closed for this book yet, so there is no value to show.")
        return
    st.caption(f"As of the close on {_day(head.as_of_session)}.")
    tiles = st.columns(3)
    tiles[0].metric("Value", _money(head.value))
    tiles[1].metric("Return since start", _pct(head.since_start_return_pct))
    tiles[2].metric(
        "SPY over the same days",
        _pct(head.spy_return_pct),
        help=None if head.spy_return_pct is not None else "SPY prices are not available.",
    )


def _chart(piece: book_stats.RangeSlice) -> None:
    palette = theme.palette()
    frame = pd.DataFrame(
        [{"session": d, "series": _BOOK_LINE, "index": v} for d, v in piece.book]
        + [{"session": d, "series": _SPY_LINE, "index": v} for d, v in piece.spy]
    )
    names = [_BOOK_LINE, *([_SPY_LINE] if piece.spy else [])]
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("session:T", title=None),
            y=alt.Y("index:Q", title="start = 100", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("session:T"),
                alt.Tooltip("series:N"),
                alt.Tooltip("index:Q", format=".2f"),
            ],
            **theme.series_encodings(palette, names, _BOOK_LINE),
        )
    )
    st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _stat(label: str, value: str, explanation: str) -> None:
    st.metric(label, value)
    st.caption(explanation)


def _summary(perf: book_view.Performance) -> None:
    if perf.best_day is not None:
        day = perf.best_day
        _stat(
            "Best day",
            f"{_signed_money(day.dollars)} ({day.pct:+.2f}%)",
            f"{_short_day(day.session)}. {_TERM_HELP['best']}",
        )
    if perf.worst_day is not None:
        day = perf.worst_day
        _stat(
            "Worst day",
            f"{_signed_money(day.dollars)} ({day.pct:+.2f}%)",
            f"{_short_day(day.session)}. {_TERM_HELP['worst']}",
        )
    if perf.max_drawdown is not None:
        fall = perf.max_drawdown
        _stat(
            "Maximum drawdown",
            f"{_signed_money(fall.dollars)} ({fall.pct:+.2f}%)",
            f"{_short_day(fall.peak_session)} to {_short_day(fall.trough_session)}. "
            f"{_TERM_HELP['drawdown']}",
        )
    if perf.tracking is not None:
        track = perf.tracking
        value = (
            "enough to judge"
            if track.enough
            else f"too early to judge: {track.n if track.n is not None else 'unknown'}"
            f" of {track.m} rebalances"
        )
        _stat("Track record", value, _TERM_HELP["early"])


def _performance(perf: book_view.Performance | None) -> None:
    st.subheader("How it has done")
    if perf is None:
        st.caption("Nothing to draw until the book has a closed day.")
        return
    choice = st.segmented_control(
        "Range", list(_RANGES), default=_DEFAULT_RANGE, key=RANGE_KEY, label_visibility="collapsed"
    )
    key = _RANGES[choice or _DEFAULT_RANGE]
    piece = next(r for r in perf.ranges if r.range_key == key)
    left, right = st.columns([3, 1])
    with left:
        if piece.note is not None:
            st.caption(
                "This range is longer than the book's history, so it shows the whole history."
            )
        if len(piece.book) < 2:
            st.caption("Not enough days yet to draw a line.")
        else:
            _chart(piece)
    with right:
        _summary(perf)


def _today(view: book_view.BookView) -> None:
    st.subheader("Today")
    today = view.today
    if today is None:
        return
    if view.today_session is not None:
        st.caption(_day(view.today_session))
    sentences = [s for s in (today.sells.sentence, today.buys.sentence) if s]
    if not sentences and not today.problems:
        st.write(today.digest)
        return
    for sentence in sentences:
        st.write(sentence)
    for problem in today.problems:
        st.warning(problem.sentence)


def _table(rows: tuple[book_holdings.HoldingRow, ...]) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Ticker": r.ticker,
                    "Value": None if r.value is None else _money(r.value),
                    "Gain": None if r.gain is None else _signed_money(r.gain),
                    "Since bought": None if r.gain_percent is None else f"{r.gain_percent:+.2f}%",
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _open_trades(book: str) -> None:
    st.session_state[TRADES_BOOK_KEY] = book
    st.switch_page(TRADES_PAGE)


def _holdings(view: book_view.BookView, book: str) -> None:
    st.subheader("Holdings")
    held = view.holdings
    if held is None:
        st.caption("Nothing is held yet.")
        return
    summary = held.summary
    tiles = st.columns(4)
    tiles[0].metric("Names held", summary.count)
    tiles[1].metric("Cash", "not yet" if summary.cash is None else _money(summary.cash))
    tiles[2].metric("Up today", summary.up_today)
    tiles[3].metric("Down today", summary.down_today)
    gains, losses = st.columns(2)
    with gains:
        st.caption("Biggest gains")
        _table(summary.best)
    with losses:
        st.caption("Biggest losses")
        _table(summary.worst)
    if st.button("See all trades", key=f"book_page_trades_{book}"):
        _open_trades(book)


def render(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Draw the Book page from the shell's one read-only connection."""
    try:
        summaries = ops.book_summaries(conn, settings)
    except (JournalNotInitialised, SchemaVersionError):
        st.info("No book has started yet, so there is nothing to show.")
        return
    summary = _choose_book(summaries, settings)
    if summary is None:
        st.info("No book has started yet, so there is nothing to show.")
        return
    try:
        view = book_view.build(
            conn, settings, summary.book_id, _now(), top_n=settings.ui.holdings_rows
        )
    except UnreadableMarkError:
        st.error("A day's value for this book could not be read, so the page cannot be drawn.")
        return
    if view.header is None:
        st.info("This book has no window yet, so there is nothing to show.")
        return
    _header(conn, summary, view)
    _performance(view.performance)
    _today(view)
    _holdings(view, summary.book_id)

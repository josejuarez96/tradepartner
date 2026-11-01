"""The Home page (plan T275, amendment 2026-10-26 #2170).

Draws `execution.home_view.build` top to bottom in the approved mockup's order: a
one-line status, "Needs you", one card per book, the Data status line and the
Research strip. The view model chose every word and computed every number; this
module only lays them out. Two small display sums live here because the view model
carries percents only: the dollar change since start (from the card's equity and
percent) and the book-against-SPY line (`book_stats.range_slice` over
`ops.performance_data`, cut at the card's session). It opens no connection, writes
nothing and runs nothing: an action is text to copy.

Colour follows `docs/design/dashboard.md`: the orange `needs_you` role marks an item
that needs the owner and appears nowhere else, so a page with no orange is one where
nothing needs him. Every time is Eastern; ids, raw statuses and UTC never appear.
The book's link opens the Books tab with `?book=<book>`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.config import Settings
from tradepartner.dashboard import header, ops_page, theme
from tradepartner.execution import book_stats, home_view, ops
from tradepartner.execution.home_view import BookCard, NeedsYou
from tradepartner.store.schema import SchemaVersionError

#: The Books tab's route (`app` derives it from the title) and the query key it reads.
BOOKS_URL_PATH: Final = "books"
BOOK_QUERY_KEY: Final = "book"

_ET: Final = ZoneInfo("America/New_York")
_BOOK_LINE: Final = "This book"
_BENCHMARK_LINE: Final = "SPY"
_CHART_HEIGHT: Final = 160
_NO_WINDOW: Final = (
    "No book is trading yet. To start one, run `tradepartner paper start --hypothesis <name>`."
)
_LEVEL_STATUS: Final[dict[str, theme.Status]] = {
    "ok": "good",
    "amber": "warning",
    "red": "critical",
}
_PILL_COLOR: Final = {"Trading": "green", "Halted": "red", "Closed": "gray"}
_LINE_SCHEMA: Final[dict[str, pl.DataType]] = {
    "session": pl.Date(),
    "series": pl.Utf8(),
    "index": pl.Float64(),
}


def _day(session: date) -> str:
    return f"{session:%a %b} {session.day}"


def _et(moment: datetime) -> str:
    local = moment.astimezone(_ET)
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} ET"


def _money(value: float) -> str:
    return f"${abs(value):,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _signed_money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"


def _change(equity: float, pct: float, other_pct: float | None) -> tuple[str, str | None]:
    """The book's and SPY's change since start as "+$1,000.00 (+1.00%)", dollars first.

    SPY's dollars are what the book's starting value would have made in SPY."""
    start = equity / (1 + pct / 100)
    book = f"{_signed_money(equity - start)} ({pct:+.2f}%)"
    if other_pct is None:
        return book, None
    return book, f"{_signed_money(start * other_pct / 100)} ({other_pct:+.2f}%)"


def _status_line(view: home_view.HomeView) -> None:
    line = view.status
    theme.status_badge(line.text, _LEVEL_STATUS[line.level])
    parts = [line.next_run]
    if line.next_rebalance is not None:
        parts.append(f"next rebalance {_day(line.next_rebalance)}")
    st.caption(" · ".join(parts))


def _needs_you(items: tuple[NeedsYou, ...]) -> None:
    st.subheader("Needs you")
    if not items:
        st.markdown("Nothing needs you.")
        return
    for item in items:
        with st.container(border=True):
            st.markdown(f":orange[:material/priority_high: **{item.title}**]")
            st.write(item.sentence)
            st.code(item.action, language=None)


def _chart_rows(
    conn: duckdb.DuckDBPyConnection, settings: Settings, card: BookCard
) -> pl.DataFrame | None:
    """The book and SPY since the first mark, both rebased to 100 and cut at the
    card's session; None when there is nothing to draw."""
    if card.session is None:
        return None
    try:
        perf = ops.performance_data(conn, settings, card.book_id, now=header.now())
    except ValueError:
        return None
    if perf is None or not perf.equity:
        return None
    equity = {d: v for d, v in perf.equity if d <= card.session}
    bench = {d: v for d, v in perf.benchmark_index if d <= card.session}
    piece = book_stats.range_slice(equity, bench or None, "since_start", card.session)
    rows = [{"session": d, "series": _BOOK_LINE, "index": v} for d, v in piece.book]
    rows += [{"session": d, "series": _BENCHMARK_LINE, "index": v} for d, v in piece.spy]
    if len(piece.book) < 2:
        return None
    return pl.DataFrame(rows, schema=_LINE_SCHEMA)


def _line_chart(frame: pl.DataFrame) -> None:
    palette = theme.palette()
    names = sorted(set(frame["series"].to_list()))
    chart = (
        alt.Chart(frame.to_pandas(), height=_CHART_HEIGHT)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("session:T", title=None),
            y=alt.Y("index:Q", title=None, scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("session:T"),
                alt.Tooltip("series:N"),
                alt.Tooltip("index:Q", format=".2f"),
            ],
            **theme.series_encodings(palette, names, _BOOK_LINE),
        )
    )
    st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")
    st.caption("Both start at 100.")


def _pill(label: str) -> None:
    color = _PILL_COLOR.get(label, "blue")
    st.badge(label, color=color)  # type: ignore[arg-type]


def _card(conn: duckdb.DuckDBPyConnection, settings: Settings, card: BookCard) -> None:
    with st.container(border=True):
        top = st.columns([3, 1])
        top[0].markdown(f"#### {card.book_id}")
        with top[1]:
            _pill(card.pill)
        st.caption(card.strategy)
        if card.last_run_message:
            st.caption(f"Last run: {card.last_run_message}")
        if card.unreadable:
            st.caption("Some records for this book could not be read; its numbers may be missing.")
        if card.session is None or card.equity is None:
            st.write("No value yet: the first day's run sets the starting point.")
            return
        left, right = st.columns(2)
        with left:
            st.metric(f"Value as of {_day(card.session)}", _money(card.equity))
            if card.since_start_pct is not None:
                book, spy = _change(card.equity, card.since_start_pct, card.spy_since_start_pct)
                st.markdown(f"**Since start:** {book}")
                if spy is not None:
                    st.markdown(f"**SPY, same days:** {spy}")
        with right:
            frame = _chart_rows(conn, settings, card)
            if frame is not None:
                _line_chart(frame)
        st.write(card.digest or "No trades today.")
        facts = []
        if card.holdings_count is not None:
            noun = "holding" if card.holdings_count == 1 else "holdings"
            facts.append(f"{card.holdings_count} {noun}")
        if card.cash is not None:
            facts.append(f"{_money(card.cash)} cash")
        if facts:
            st.write(" · ".join(facts))
        if card.top_names:
            st.write(f"Largest: {', '.join(card.top_names)}")
        st.page_link(
            st.Page(lambda: None, title="Books", url_path=BOOKS_URL_PATH),
            label=f"Open {card.book_id}",
            query_params={BOOK_QUERY_KEY: card.book_id},
        )


def _data_and_research(view: home_view.HomeView) -> None:
    st.subheader("Data")
    st.write(view.data.text)
    st.subheader("Research")
    research = view.research
    text = research.text if research.title is None else f"{research.text}: {research.title}"
    when = f" ({_et(research.at)})" if research.at is not None else ""
    st.write(f"{text}{when}")
    st.page_link(
        st.Page(lambda: None, title="Research", url_path=research.link),
        label="Open Research",
    )


def render(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Draw the Home page from the shell's one read-only connection."""
    try:
        view = home_view.build(conn, settings, header.now().astimezone(UTC))
    except SchemaVersionError as exc:
        ops_page._render_journal_outdated(str(exc))
        return
    if not view.books:
        st.write(_NO_WINDOW)
        _data_and_research(view)
        return
    _status_line(view)
    _needs_you(view.needs_you)
    st.subheader("Books")
    for card in view.books:
        _card(conn, settings, card)
    _data_and_research(view)

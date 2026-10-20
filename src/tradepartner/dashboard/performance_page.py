"""Performance page (plan T252, paper spec amendment 2026-10-20, issue 2006 item 2).

Draws `execution.ops.performance_data` (T251) for one book: a read-only view over
the shell's one connection per render. It writes nothing, opens no trial and never
touches the broker or the run lock.

Layout, top to bottom, per the design standard (docs/design/dashboard.md): header
("as of", "last updated", a stale chip); KPI row (equity, cumulative return, daily
P&L, versus SPY); the hero equity curve, every line rebased to 100 at the base
session B (the book in the accent, SPY muted and dashed, the tracking trial's line
in its own slot over that trial's own span); a daily-P&L bar card; and the tracking
state line, one of three (`not yet`, `report due`, the trial line). The only
non-chart states are the shell's empty, busy and unreadable panels, the journal and
no-window panels the operations page shows, and `benchmark unavailable`.

The book selector picks the book as the operations page's does (`paper.book_id`
by default, shown only when another book has a window).
"""

from __future__ import annotations

from datetime import date

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, ops_page, theme
from tradepartner.execution import ops
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.execution.ops import PerformanceData
from tradepartner.store.schema import SchemaVersionError

#: The book selector's widget key (session state).
BOOK_KEY = "performance_book"

_BOOK = "Book"
_BENCHMARK = "SPY"
_TRIAL = "Tracking trial"
_LINE_SCHEMA: dict[str, pl.DataType] = {
    "session": pl.Date(),
    "series": pl.Utf8(),
    "index": pl.Float64(),
}
_PNL_SCHEMA: dict[str, pl.DataType] = {
    "session": pl.Date(),
    "pnl": pl.Float64(),
    "latest": pl.Boolean(),
}


def _selected_book(conn: duckdb.DuckDBPyConnection, settings: Settings) -> str:
    """The selector's pick, as `ops_page`'s: shown only when a book other than
    `paper.book_id` has a window."""
    try:
        summaries = ops.book_summaries(conn, settings)
    except SchemaVersionError:
        return settings.paper.book_id
    default = settings.paper.book_id
    books = [row.book_id for row in summaries]
    if books in ([], [default]):
        return default
    index = books.index(default) if default in books else 0
    return str(st.selectbox("Book", books, index=index, key=BOOK_KEY))


def _signed(value: float, fmt: str) -> str:
    return f"{value:+{fmt}}"


def _kpis(data: PerformanceData) -> None:
    assert data.equity
    tiles = st.columns(4)
    tiles[0].metric("Equity", f"{data.equity[-1][1]:,.2f}")
    tiles[1].metric(
        "Cumulative return",
        "n/a"
        if data.cumulative_return_pct is None
        else _signed(data.cumulative_return_pct, ".2f") + "%",
    )
    tiles[2].metric(
        "Daily P&L",
        _signed(data.daily_pnl[-1][1], ",.2f") if data.daily_pnl else "n/a",
        help="no day yet beyond the base session" if not data.daily_pnl else None,
    )
    tiles[3].metric(
        f"Versus {_BENCHMARK}",
        _signed(data.excess_pp[-1][1], ".2f") + " pp" if data.excess_pp else "n/a",
        help="return minus SPY total return since the base session",
    )


def _benchmark_panel(error: str) -> None:
    label = (
        error if error.startswith("benchmark unavailable") else f"benchmark unavailable: {error}"
    )
    theme.status_badge(label, "warning")


def _lines_frame(data: PerformanceData) -> pl.DataFrame:
    rows: list[tuple[date, str, float]] = [(d, _BOOK, v) for d, v in data.equity_index]
    rows += [(d, _BENCHMARK, v) for d, v in data.benchmark_index]
    rows += [(d, _TRIAL, v) for d, v in data.trial_index]
    return pl.DataFrame(
        [{"session": d, "series": s, "index": v} for d, s, v in rows], schema=_LINE_SCHEMA
    )


def _equity_hero(data: PerformanceData, palette: theme.Palette) -> None:
    with st.container(border=True):
        st.subheader("Equity, rebased to 100")
        frame = _lines_frame(data)
        names = [_BOOK, _BENCHMARK, *([_TRIAL] if data.trial_index else [])]
        if data.benchmark_error is not None:
            names.remove(_BENCHMARK)
        solid: list[int] = []
        ranges = {
            _BOOK: (palette.accent, solid, 1.0),
            _BENCHMARK: (palette.text_secondary, [4, 4], 0.6),
            _TRIAL: (palette.series[1], solid, 1.0),
        }
        chart = (
            alt.Chart(frame.to_pandas())
            .mark_line(strokeWidth=2)
            .encode(
                x=alt.X("session:T", title=None),
                y=alt.Y("index:Q", title="index", scale=alt.Scale(zero=False)),
                color=alt.Color(
                    "series:N",
                    scale=alt.Scale(domain=names, range=[ranges[n][0] for n in names]),
                    legend=alt.Legend(title=None, orient="top"),
                ),
                strokeDash=alt.StrokeDash(
                    "series:N",
                    scale=alt.Scale(domain=names, range=[ranges[n][1] for n in names]),
                    legend=None,
                ),
                opacity=alt.Opacity(
                    "series:N",
                    scale=alt.Scale(domain=names, range=[ranges[n][2] for n in names]),
                    legend=None,
                ),
                tooltip=[
                    alt.Tooltip("session:T"),
                    alt.Tooltip("series:N"),
                    alt.Tooltip("index:Q", format=".2f"),
                ],
            )
        )
        st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")
        if data.trial_span is not None:
            first, last = data.trial_span
            st.caption(
                f"The tracking trial is drawn over its own span, {first} to {last}, "
                "rebased to the book's equity at its first session."
            )


def _pnl_card(data: PerformanceData, palette: theme.Palette) -> None:
    with st.container(border=True):
        st.subheader("Daily P&L")
        if not data.daily_pnl:
            st.caption("No day beyond the base session yet.")
            return
        last = data.daily_pnl[-1][0]
        frame = pl.DataFrame(
            [{"session": d, "pnl": v, "latest": d == last} for d, v in data.daily_pnl],
            schema=_PNL_SCHEMA,
        )
        chart = (
            alt.Chart(frame.to_pandas())
            .mark_bar(cornerRadiusEnd=4)
            .encode(
                x=alt.X("session:T", title=None),
                y=alt.Y("pnl:Q", title="P&L"),
                color=alt.Color(
                    "latest:N",
                    scale=alt.Scale(
                        domain=[True, False], range=[palette.accent, palette.text_secondary]
                    ),
                    legend=None,
                ),
                tooltip=[alt.Tooltip("session:T"), alt.Tooltip("pnl:Q", format="+,.2f")],
            )
        )
        st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _tracking_line(data: PerformanceData) -> None:
    """The amendment's three tracking states (the trial line itself is the hero's)."""
    if data.tracking_state == "drawn":
        if data.trial_error is not None:
            st.warning(f"Tracking trial {data.tracking_trial_id} not drawn: {data.trial_error}")
        else:
            st.caption(f"Tracking trial {data.tracking_trial_id} drawn from the latest report.")
    elif data.tracking_state == "not_yet":
        st.caption(
            "not yet — a report needs two rebalances "
            f"({data.tracking_n} of {ops._REPORT_MIN_REBALANCES})"
        )
    elif data.tracking_state == "report_due":
        st.caption(f"two rebalances done — run paper report --book {data.book_id}")
    else:
        st.caption("tracking unavailable: the rebalance cadence could not be read.")


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the performance page from the shell's read-only connection."""
    if settings is None:
        settings = get_settings()

    st.header("Performance")
    book = _selected_book(conn, settings)
    try:
        data = ops.performance_data(conn, settings, book)
    except UnreadableMarkError as exc:
        st.error(f"A position mark could not be read: {exc}")
        return

    if data is None:
        ops_page._render_no_window()
        return
    if data.journal_not_initialised:
        ops_page._render_journal_not_initialised()
        return
    if data.journal_outdated is not None:
        ops_page._render_journal_outdated(data.journal_outdated)
        return

    header.render_freshness(header.Freshness(data.as_of, data.last_updated))
    if data.stale:
        theme.status_badge("stale: no run yet for the required session", "warning")
    if not data.equity:
        st.caption("No marks yet in this window; the first run's mark starts the curve.")
        _tracking_line(data)
        return

    _kpis(data)
    if data.benchmark_error is not None:
        _benchmark_panel(data.benchmark_error)
    palette = theme.palette()
    _equity_hero(data, palette)
    _pnl_card(data, palette)
    _tracking_line(data)

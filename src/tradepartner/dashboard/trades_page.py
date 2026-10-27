"""Read-only, per-session Trades page over the T258 blotter."""

from __future__ import annotations

from datetime import date

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, ops_page, theme
from tradepartner.execution import blotter, ops
from tradepartner.store import journal
from tradepartner.store.schema import SchemaVersionError

BOOK_KEY = "trades_book"
SESSION_KEY = "trades_session"
_ROW_LIMIT = 500


def _book(conn: duckdb.DuckDBPyConnection, settings: Settings) -> str:
    """Select a book using the Operations page's default and token order."""
    books = ops.book_ids(conn)
    default = settings.paper.book_id
    if books in ((), (default,)):
        return default
    index = books.index(default) if default in books else 0
    return str(st.selectbox("Book", books, index=index, key=BOOK_KEY))


def _session(sessions: tuple[date, ...]) -> date:
    """Choose an order session, newest first on the first render."""
    return st.selectbox("Session", sessions, index=0, key=SESSION_KEY)


def _slippage_text(row: blotter.BlotterRow) -> str:
    """Keep unavailable slippage's reason visible instead of inventing zero."""
    if row.slippage_bp is not None:
        return f"{row.slippage_bp:+.2f}"
    return row.slippage_note or "unavailable"


def _table(data: blotter.SessionBlotter) -> pd.DataFrame:
    """Eight columns, with rank present even for orders lacking slippage."""
    return pd.DataFrame(
        [
            {
                "ticker": row.symbol,
                "side": row.side,
                "shares": row.filled_quantity,
                "fill price": row.fill_price,
                "status": row.status,
                "slippage (bp)": _slippage_text(row),
                "reason": row.reason or row.decision,
                "signal rank": "—" if row.signal_rank is None else str(row.signal_rank),
            }
            for row in data.rows
        ]
    )


def _kpis(data: blotter.SessionBlotter) -> None:
    """Four comparable session measures; summary includes any capped rows."""
    tiles = st.columns(4)
    suffix = " shown" if data.capped else ""
    tiles[0].metric(f"Orders{suffix}", len(data.rows))
    tiles[1].metric(f"Filled{suffix}", sum(row.filled_quantity > 0 for row in data.rows))
    notional = sum(
        row.filled_quantity * row.fill_price for row in data.rows if row.fill_price is not None
    )
    tiles[2].metric(f"Notional traded{suffix}", f"{notional:,.2f}")
    weighted = data.summary.overall.wmean_bp
    tiles[3].metric("Slippage (bp)", f"{weighted:+.2f}" if weighted is not None else "n/a")


def _chart_frame(data: blotter.SessionBlotter) -> pd.DataFrame:
    """One category per order, even when several orders share a ticker."""
    numeric = [row for row in data.rows if row.slippage_bp is not None]
    largest = max(numeric, key=lambda row: abs(row.slippage_bp or 0.0)).client_order_id
    return pd.DataFrame(
        [
            {
                "order": row.client_order_id,
                "order label": f"{row.symbol} · {row.side} · …{row.client_order_id[-6:]}",
                "ticker": row.symbol,
                "side": row.side,
                "slippage": row.slippage_bp,
                "signal rank": row.signal_rank,
                "highlight": row.client_order_id == largest,
            }
            for row in numeric
        ]
    )


def _hero(data: blotter.SessionBlotter) -> None:
    """Per-order slippage bars; the greatest absolute move receives the accent."""
    if not any(row.slippage_bp is not None for row in data.rows):
        return
    frame = _chart_frame(data)
    palette = theme.palette()
    with st.container(border=True):
        st.subheader("Slippage vs session open (bp)")
        chart = (
            alt.Chart(frame)
            .mark_bar(cornerRadiusEnd=4)
            .encode(
                x=alt.X("order label:N", sort=None, title=None),
                y=alt.Y("slippage:Q", title="bp · adverse positive"),
                color=alt.Color(
                    "highlight:N",
                    scale=alt.Scale(
                        domain=[True, False], range=[palette.accent, palette.text_secondary]
                    ),
                    legend=None,
                ),
                tooltip=[
                    alt.Tooltip("order:N", title="Order"),
                    alt.Tooltip("ticker:N"),
                    alt.Tooltip("side:N"),
                    alt.Tooltip("slippage:Q", format="+.2f", title="Slippage (bp)"),
                    alt.Tooltip("signal rank:Q", title="Signal rank"),
                ],
            )
        )
        st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw one book and order session through the shell's read-only connection."""
    if settings is None:
        settings = get_settings()
    st.header("Trades")
    try:
        now = header.now()
        header.render_freshness(header.store_freshness(conn, now))
        book_column, session_column = st.columns(2)
        with book_column:
            book = _book(conn, settings)
        window = journal.latest_window(conn, book)
        if window is None or window.window_id is None:
            ops_page._render_no_window()
            return
        sessions = blotter.sessions(conn, window.window_id)
        if not sessions:
            st.info("No orders in this book yet.")
            return
        with session_column:
            session = _session(sessions)
        data = blotter.session_blotter(conn, window.window_id, session, now, _ROW_LIMIT)
    except journal.JournalNotInitialised:
        ops_page._render_journal_not_initialised()
        return
    except SchemaVersionError as exc:
        ops_page._render_journal_outdated(str(exc))
        return
    except (ValueError, journal.JournalIntegrityError, duckdb.Error) as exc:
        st.error(f"The trades for this book could not be read: {exc}")
        return
    if not data.rows:
        st.info("No orders in this session.")
        return
    _kpis(data)
    _hero(data)
    with st.container(border=True):
        st.subheader("Orders")
        st.dataframe(
            _table(data),
            hide_index=True,
            column_config={
                "shares": st.column_config.NumberColumn("shares", format="%.4f"),
                "fill price": st.column_config.NumberColumn("fill price", format="%.2f"),
            },
        )
        if data.capped:
            st.caption(
                f"Showing the newest {_ROW_LIMIT} orders; slippage includes the full session."
            )

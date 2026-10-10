"""Operations page (Phase 4 T69, spec req 12; plan T69; ADR 0011).

Draws `execution.ops.page_data` (T66b) exactly as `paper status` (T67) reads
it: a read-only view over the shell's one connection per render, never a
second computation of the same numbers (ADR 0011 point 1). This page writes
nothing and never touches the broker or the run lock; `execution.ops.
page_data` itself only asks `execution.lock.is_held`, a non-blocking check.

Layout, top to bottom, per the design standard's Phase 4 row
(docs/design/dashboard.md): header ("as of", "last updated", a stale chip);
KPI row (positions, open orders, today's signals, the kill-switch state as a
status chip); the ranking hero (the latest plan's targets in the accent,
everything else muted, in/out reasons on hover and in the table below);
fills; the chain detail view, one order at a time; alerts; reconciliation
status. **Books** (ADR 0017 B.7, plan T156; spec req 12 as amended
2026-10-09): above the per-book sections, a one-row-per-book summary
(`ops.book_summaries`: window, open or closed, positions, open orders, switch
state, last run status, next rebalance session at the book's cadence) and,
when a book other than `paper.book_id` has a window, a book selector (`BOOK_KEY`, default
`paper.book_id`) that picks the book every section below is drawn for
(`ops.page_data(conn, settings, book)`). Two states short-circuit the
rest, each its own panel rather than a traceback (the T43/T44 "registry not
initialised" pattern): a store with no paper-trading journal yet
(`journal_not_initialised`), and a migrated store with no `paper start` yet (`window is None`).
"""

from __future__ import annotations

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, theme
from tradepartner.execution import ops
from tradepartner.execution.ops import OpsData, OrderChain, RankedSignal
from tradepartner.execution.switch import SwitchState
from tradepartner.store.journal import AlertRow, ReconciliationRow
from tradepartner.store.schema import SchemaVersionError

#: The book selector's widget key (session state).
BOOK_KEY = "ops_book"

#: Severity per `execution.reconcile` status (module docstring there, "Status"):
#: `mismatch` is the only real error (the caller raises `ReconciliationError` on
#: it); `pending_unresolved` and `fills_lagging` are expected, allowance-governed
#: transient states, not errors, so they read as a warning, never critical.
_RECONCILIATION_STATUS: dict[str, theme.Status] = {
    "ok": "good",
    "pending_unresolved": "warning",
    "fills_lagging": "warning",
    "mismatch": "critical",
}

_SUMMARY_SCHEMA: dict[str, pl.DataType] = {
    "book": pl.Utf8(),
    "window": pl.Int64(),
    "open": pl.Boolean(),
    "positions": pl.Int64(),
    "open_orders": pl.Int64(),
    "kill_switch": pl.Utf8(),
    "last_run": pl.Utf8(),
    "next_rebalance": pl.Date(),
}
_RANKING_SCHEMA: dict[str, pl.DataType] = {
    "security_id": pl.Utf8(),
    "rank": pl.Int64(),
    "score": pl.Float64(),
    "signal_reason": pl.Utf8(),
    "decision": pl.Utf8(),
    "decision_reason": pl.Utf8(),
    "selected": pl.Boolean(),
}
_FILLS_SCHEMA: dict[str, pl.DataType] = {
    "security_id": pl.Utf8(),
    "symbol": pl.Utf8(),
    "side": pl.Utf8(),
    "quantity": pl.Float64(),
    "price": pl.Float64(),
    "broker_fill_id": pl.Utf8(),
    "known_at": pl.Datetime("us", "UTC"),
}
_ALERTS_SCHEMA: dict[str, pl.DataType] = {
    "session": pl.Date(),
    "kind": pl.Utf8(),
    "message": pl.Utf8(),
    "at": pl.Datetime("us", "UTC"),
}
_CHAIN_SCHEMA: dict[str, pl.DataType] = {
    "at": pl.Datetime("us", "UTC"),
    "kind": pl.Utf8(),
    "detail": pl.Utf8(),
}


def _render_journal_not_initialised() -> None:
    st.info(
        "Journal not initialised: this store has no paper-trading journal yet; "
        "any writing command (e.g. `tradepartner paper start`) migrates it to "
        "the current version."
    )


def _render_journal_outdated(message: str) -> None:
    st.warning(f"Journal outdated: {message}")


def _render_no_window() -> None:
    st.info("No paper window yet. Run `tradepartner paper start` to open one.")


def _switch_badge(state: SwitchState) -> None:
    if state.engaged:
        theme.status_badge(f"kill switch: engaged ({'; '.join(state.causes)})", "critical")
    elif state.run_in_progress:
        theme.status_badge("kill switch: run in progress", "warning")
    else:
        theme.status_badge("kill switch: ok", "good")


def _switch_text(state: SwitchState) -> str:
    if state.engaged:
        return "engaged"
    return "run in progress" if state.run_in_progress else "released"


def _summary_table(summaries: tuple[ops.BookSummary, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "book": row.book_id,
                "window": row.window.window_id,
                "open": row.is_open,
                "positions": row.positions_count,
                "open_orders": row.open_orders_count,
                "kill_switch": _switch_text(row.switch_state),
                "last_run": row.last_run_status,
                "next_rebalance": row.next_rebalance_session,
            }
            for row in summaries
        ],
        schema=_SUMMARY_SCHEMA,
    )


def _book_summaries(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> tuple[ops.BookSummary, ...]:
    """The summary rows, or none on a journal too old to read (the outdated
    panel below names it)."""
    try:
        return ops.book_summaries(conn, settings)
    except SchemaVersionError:
        return ()


def _selected_book(summaries: tuple[ops.BookSummary, ...], default: str) -> str:
    """The book the sections are drawn for: the selector's pick when any book
    other than `default` (`paper.book_id`) has a window, else `default`, as
    `paper status` and the override page read it."""
    books = [row.book_id for row in summaries]
    if books in ([], [default]):
        return default
    index = books.index(default) if default in books else 0
    picked = st.selectbox("Book", books, index=index, key=BOOK_KEY)
    return str(picked)


def _summary_card(summaries: tuple[ops.BookSummary, ...]) -> None:
    with st.container(border=True):
        st.subheader("Books")
        st.dataframe(_summary_table(summaries), hide_index=True)


def _kpis(data: OpsData) -> None:
    assert data.switch_state is not None
    tiles = st.columns(4)
    tiles[0].metric("Positions", data.positions_count, help=f"value {data.positions_value:,.2f}")
    tiles[1].metric("Open orders", data.open_orders_count)
    tiles[2].metric("Today's signals", data.targets_count)
    with tiles[3]:
        st.caption("Kill switch")
        _switch_badge(data.switch_state)


def _ranking_table(ranking: tuple[RankedSignal, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "security_id": r.security_id,
                "rank": r.rank,
                "score": r.score,
                "signal_reason": r.signal_reason,
                "decision": r.decision,
                "decision_reason": r.decision_reason,
                "selected": r.selected,
            }
            for r in ranking
        ],
        schema=_RANKING_SCHEMA,
    )


def _ranking_hero(data: OpsData, palette: theme.Palette) -> None:
    with st.container(border=True):
        st.subheader("Signal ranking")
        if not data.ranking:
            st.caption("No ranking yet for this window.")
            return
        table = _ranking_table(data.ranking)
        scored = table.filter(pl.col("score").is_not_null())
        if scored.is_empty():
            st.caption("No scored names in the latest plan.")
        else:
            chart = (
                alt.Chart(scored.to_pandas())
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X("security_id:N", title=None, sort="-y"),
                    y=alt.Y("score:Q", title="score"),
                    color=alt.Color(
                        "selected:N",
                        scale=alt.Scale(
                            domain=[True, False], range=[palette.accent, palette.text_secondary]
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        alt.Tooltip("security_id:N"),
                        alt.Tooltip("score:Q"),
                        alt.Tooltip("signal_reason:N"),
                        alt.Tooltip("decision:N"),
                        alt.Tooltip("decision_reason:N"),
                    ],
                )
            )
            st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")
            st.caption("Accent bars are the target set (in); the rest are out.")
        st.dataframe(table, hide_index=True)


def _fills_table(data: OpsData) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "security_id": f.security_id,
                "symbol": f.symbol,
                "side": f.side,
                "quantity": f.fill.quantity,
                "price": f.fill.price,
                "broker_fill_id": f.fill.broker_fill_id,
                "known_at": f.fill.known_at,
            }
            for f in data.fills
        ],
        schema=_FILLS_SCHEMA,
    )


def _fills_card(data: OpsData) -> None:
    with st.container(border=True):
        st.subheader("Fills")
        if not data.fills:
            st.caption("No fills yet in this window.")
            return
        st.dataframe(_fills_table(data), hide_index=True)
        if data.fills_capped:
            st.caption("Showing the newest fills only; the window has more.")


def _chain_table(chain: OrderChain) -> pl.DataFrame:
    return pl.DataFrame(
        [{"at": s.at, "kind": s.kind, "detail": s.detail} for s in chain.steps],
        schema=_CHAIN_SCHEMA,
    )


def _chains_card(data: OpsData) -> None:
    with st.container(border=True):
        st.subheader("Order chain")
        if not data.chains:
            st.caption("No orders yet in this window.")
            return
        ids = [c.client_order_id for c in data.chains]
        picked = st.selectbox("Order", ids, key="ops_chain_order")
        chain = next(c for c in data.chains if c.client_order_id == picked)
        st.dataframe(_chain_table(chain), hide_index=True)
        if data.chains_capped:
            st.caption("Some steps are not shown; the window has more than the row limit.")


def _alerts_table(alerts: tuple[AlertRow, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"session": a.session, "kind": a.kind, "message": a.message, "at": a.at} for a in alerts],
        schema=_ALERTS_SCHEMA,
    )


def _alerts_card(data: OpsData) -> None:
    with st.container(border=True):
        st.subheader("Alerts")
        if not data.alerts:
            st.caption("No alerts in this window.")
            return
        st.dataframe(_alerts_table(data.alerts), hide_index=True)
        if data.alerts_capped:
            st.caption("Showing the newest alerts only; the window has more.")


def _reconciliation_card(reconciliation: ReconciliationRow | None) -> None:
    with st.container(border=True):
        st.subheader("Reconciliation")
        if reconciliation is None:
            theme.status_badge("no reconciliation yet", "warning")
            return
        status = _RECONCILIATION_STATUS.get(reconciliation.status, "critical")
        theme.status_badge(f"{reconciliation.status} ({header.when(reconciliation.at)})", status)
        if reconciliation.broker_cash is not None:
            st.caption(f"broker cash: {reconciliation.broker_cash:,.2f}")
        if reconciliation.mismatches_json:
            st.caption(reconciliation.mismatches_json)


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the operations page from the shell's read-only connection."""
    if settings is None:
        settings = get_settings()

    st.header("Operations")
    summaries = _book_summaries(conn, settings)
    if summaries:
        _summary_card(summaries)
    book = _selected_book(summaries, settings.paper.book_id)
    data = ops.page_data(conn, settings, book)

    if data.journal_not_initialised:
        _render_journal_not_initialised()
        return
    if data.journal_outdated is not None:
        _render_journal_outdated(data.journal_outdated)
        return
    if data.window is None:
        _render_no_window()
        return

    header.render_freshness(header.Freshness(data.as_of, data.last_updated))
    if data.stale:
        theme.status_badge("stale: no run yet for the required session", "warning")

    _kpis(data)
    palette = theme.palette()
    _ranking_hero(data, palette)
    left, right = st.columns(2)
    with left:
        _fills_card(data)
        _chains_card(data)
    with right:
        _alerts_card(data)
        _reconciliation_card(data.reconciliation)

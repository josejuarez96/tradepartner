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

import json

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, theme
from tradepartner.execution import headroom, ops, waiting
from tradepartner.execution.ops import OpsData, OrderChain, RankedSignal
from tradepartner.execution.switch import SwitchState
from tradepartner.store import journal
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import utc_now
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
    "next_rebalance": pl.Utf8(),
}
_RANKING_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.Utf8(),
    "rank": pl.Int64(),
    "score": pl.Float64(),
    "signal_reason": pl.Utf8(),
    "decision": pl.Utf8(),
    "decision_reason": pl.Utf8(),
    "selected": pl.Boolean(),
}
_FILLS_SCHEMA: dict[str, pl.DataType] = {
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


def _rebalance_result(row: ops.BookSummary) -> str:
    return "traded today" if row.last_run_has_orders else "ran today with no orders"


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
                "next_rebalance": (
                    str(row.next_rebalance_session or "-")
                    + (
                        f"; rebalance {traded} {_rebalance_result(row)}"
                        if (traded := ops.rebalance_traded_today(row)) is not None
                        else ""
                    )
                ),
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


def _waiting_card(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Show every open book's owner queue, independent of the selected book."""
    try:
        items: tuple[waiting.WaitingItem, ...] = ops.waiting_on_owner(conn, settings, utc_now())
    except SchemaVersionError:
        return  # The outdated-journal panel below explains this store.
    with st.container(border=True):
        st.subheader("Waiting on you")
        if not items:
            theme.status_badge("Nothing is waiting on you", "good")
            return
        for item in items:
            status: theme.Status = "critical" if item.kind == "kill_switch_engaged" else "warning"
            theme.status_badge(f"{item.book_id}: {item.kind}", status)
            st.caption(f"{item.detail} · since {item.since or '-'}")
            if item.command is not None:
                st.code(item.command, language="bash")
            else:
                st.caption("No command; follow the detail above.")


def _kpis(data: OpsData) -> None:
    assert data.switch_state is not None
    tiles = st.columns(4)
    tiles[0].metric("Positions", data.positions_count, help=f"value {data.positions_value:,.2f}")
    tiles[1].metric("Open orders", data.open_orders_count)
    tiles[2].metric("Today's signals", data.targets_count)
    with tiles[3]:
        st.caption("Kill switch")
        _switch_badge(data.switch_state)


def _symbols(conn: duckdb.DuckDBPyConnection, data: OpsData) -> dict[str, str]:
    """Ranking tickers known at the plan, with submitted order symbols as fallback."""
    ids = {r.security_id for r in data.ranking}
    symbols: dict[str, str] = {}
    if data.plan_known_at is not None and ids:
        listings = listings_as_of(conn, data.plan_known_at, sorted(ids))
        for row in (
            listings.filter(pl.col("valid_from") <= data.plan_known_at.date())
            .sort("valid_from")
            .iter_rows(named=True)
        ):
            symbols[str(row["security_id"])] = str(row["ticker"])
    if data.window is not None and data.window.window_id is not None:
        limit = max(len(data.chains), len(data.ranking), len(data.fills), 1)
        for order in journal.orders_for(conn, window_id=data.window.window_id, limit=limit):
            if data.plan_known_at is not None and order.known_at <= data.plan_known_at:
                symbols.setdefault(order.security_id, order.symbol)
            symbols[order.client_order_id] = order.symbol
    return symbols


def _ranking_table(ranking: tuple[RankedSignal, ...], symbols: dict[str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "ticker": symbols.get(r.security_id, f"Ticker unavailable ({r.security_id})"),
                "rank": r.rank,
                "score": r.score,
                "signal_reason": r.signal_reason,
                "decision": r.decision,
                "decision_reason": r.decision_reason
                or "No separate reason recorded; see signal reason",
                "selected": r.selected,
            }
            for r in ranking
        ],
        schema=_RANKING_SCHEMA,
    )


def _ranking_hero(data: OpsData, palette: theme.Palette, symbols: dict[str, str]) -> None:
    with st.container(border=True):
        st.subheader("Signal ranking")
        if not data.ranking:
            st.caption("No ranking yet for this window.")
            return
        table = _ranking_table(data.ranking, symbols)
        scored = table.filter(pl.col("score").is_not_null())
        if scored.is_empty():
            st.caption("No scored names in the latest plan.")
        else:
            chart = (
                alt.Chart(scored.sort("rank").head(20).to_pandas())
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X("ticker:N", title=None, sort="-y", axis=alt.Axis(labelAngle=-45)),
                    y=alt.Y("score:Q", title="score"),
                    color=alt.Color(
                        "selected:N",
                        scale=alt.Scale(
                            domain=[True, False], range=[palette.accent, palette.text_secondary]
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        alt.Tooltip("ticker:N"),
                        alt.Tooltip("score:Q"),
                        alt.Tooltip("signal_reason:N"),
                        alt.Tooltip("decision:N"),
                        alt.Tooltip("decision_reason:N"),
                    ],
                )
            )
            st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")
            st.caption(
                "Top 20 scored names shown; the table has every name. Accent bars are targets."
            )
        st.dataframe(table, hide_index=True)


def _fills_table(data: OpsData) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
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


def _chains_card(data: OpsData, symbols: dict[str, str]) -> None:
    with st.container(border=True):
        st.subheader("Order chain")
        if not data.chains:
            st.caption("No orders yet in this window.")
            return
        labels = [
            f"{symbols.get(c.client_order_id, 'Ticker unavailable')} #{i + 1} ({c.client_order_id})"
            for i, c in enumerate(data.chains)
        ]
        picked = st.selectbox(
            "Order", range(len(data.chains)), format_func=lambda i: labels[i], key="ops_chain_order"
        )
        chain = data.chains[int(picked)]
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


_EVENT_LABEL = {
    "dividend_cash": "Dividend",
    "corporate_action_cash": "Corporate action cash",
    "fee_cash": "Fee",
    "cash_in_lieu": "Cash in lieu of a fractional share",
    "split": "Split",
    "stock_dividend": "Stock dividend",
}


def _reconciliation_events(mismatches_json: str) -> list[str]:
    """Plain lines for the events a reconciliation recorded on holdings: what it
    explained (dividends, splits, fees), ticker changes, and what it could not."""
    try:
        payload = json.loads(mismatches_json)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    lines: list[str] = []
    for entry in payload.get("explained") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "event"))
        label = _EVENT_LABEL.get(kind, kind.replace("_", " ").capitalize())
        name = entry.get("security_id") or "account"
        cash = entry.get("cash")
        amount = f", cash {cash:+,.2f}" if isinstance(cash, int | float) else ""
        why = f": {entry['explanation']}" if entry.get("explanation") else ""
        lines.append(f"{label} on {name}{amount}{why}")
    for entry in payload.get("symbol_changes") or []:
        if isinstance(entry, dict):
            lines.append(
                f"Ticker change on {entry.get('security_id')}: "
                f"{entry.get('old_symbol')} is now {entry.get('new_symbol')}"
            )
    for entry in payload.get("mismatches") or []:
        if isinstance(entry, dict):
            name = entry.get("security_id") or entry.get("symbol") or "account"
            lines.append(f"Needs a look, {entry.get('kind')} on {name}: {entry.get('detail')}")
    return lines


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
            events = _reconciliation_events(reconciliation.mismatches_json)
            if events:
                st.caption("Events on your holdings (dividends, splits, fees, name changes):")
                for event in events:
                    st.markdown(f"- {event}")
            else:
                st.caption("No events on your holdings in this check.")
            with st.expander("Raw reconciliation record"):
                st.code(reconciliation.mismatches_json, language="json")


_RISK_STATUS_TEXT = {"within": "within", "over": "at or over", "engaged": "engaged"}
_RISK_SCHEMA: dict[str, pl.DataType] = {
    "limit": pl.Utf8(),
    "value": pl.Utf8(),
    "frozen limit": pl.Utf8(),
    "headroom": pl.Utf8(),
    "status": pl.Utf8(),
    "what the limit does": pl.Utf8(),
}


def _risk_chip_status(row: headroom.LimitRow) -> theme.Status:
    """Within is good; at or over is a warning, except the drawdown, whose
    crossing engages the kill switch (critical, as is an engaged trigger)."""
    if row.status == "within":
        return "good"
    return "critical" if row.key == "drawdown" else "warning"


def _risk_number(row: headroom.LimitRow, number: float) -> str:
    """Cash and drawdown read as a percentage; gross and a position as a weight."""
    return f"{number:.2%}" if row.key in ("cash", "drawdown") else f"{number:.2f}"


def _risk_value(card: headroom.Headroom, row: headroom.LimitRow) -> str:
    if row.key != "position":
        return _risk_number(row, row.value)
    if card.largest_security_id is None:
        return "none"
    name = card.largest_ticker or f"Ticker unavailable ({card.largest_security_id})"
    return f"{name} {row.value:.2f}"


def _risk_headroom(row: headroom.LimitRow) -> str:
    if row.headroom_pp > 0 and row.status == "within":
        return f"{row.headroom_pp:.2f} pp left"
    return f"over by {-row.headroom_pp:.2f} pp" if row.headroom_pp < 0 else "at the limit"


def _risk_table(card: headroom.Headroom) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "limit": row.label,
                "value": _risk_value(card, row),
                "frozen limit": _risk_number(row, row.limit),
                "headroom": _risk_headroom(row),
                "status": _RISK_STATUS_TEXT[row.status],
                "what the limit does": row.does,
            }
            for row in card.rows
        ],
        schema=_RISK_SCHEMA,
    )


def _risk_card(conn: duckdb.DuckDBPyConnection, window: journal.PaperWindowRow) -> None:
    """The Risk card (plan T260): each limit the run enforces, the window's
    frozen value, the headroom left and what crossing it does
    (`execution.headroom`). An unreadable mark or `frozen_json` shows its
    message here, not a crash."""
    with st.container(border=True):
        st.subheader("Risk")
        try:
            card = headroom.read(conn, window)
        except ValueError as exc:  # UnreadableMarkError is a ValueError
            theme.status_badge("Risk limits unreadable", "critical")
            st.caption(str(exc))
            return
        if card.session is None:
            st.caption(
                f"Before the first mark: the window's starting cash {card.cash:,.2f} and "
                f"equity {card.equity:,.2f}, no positions."
            )
        else:
            st.caption(f"Ledger equity {card.equity:,.2f}, as of the close of {card.session}.")
        if card.long_short:
            theme.status_badge("short limits not shown yet", "warning")
        for row in card.rows:
            theme.status_badge(
                f"{row.label}: {_RISK_STATUS_TEXT[row.status]}", _risk_chip_status(row)
            )
        st.dataframe(_risk_table(card), hide_index=True)
        if card.drawdown_engaged:
            st.caption("The drawdown trigger has engaged the kill switch; it rearms at a release.")
        elif card.dollars_before_switch > 0:
            st.caption(
                f"Equity can fall ${card.dollars_before_switch:,.2f} before the kill switch "
                "engages."
            )
        else:
            st.caption(
                f"Equity is ${-card.dollars_before_switch:,.2f} at or past the kill switch's "
                "trigger level, peak x (1 - limit)."
            )
        st.caption(
            f"Drawdown is measured from the rule's peak, ${card.peak:,.2f}: the last "
            "release's peak equity, else the starting equity. Cash is the ledger's cash "
            "less held shorts; the unspent_cash alert reads the broker's."
        )


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the operations page from the shell's read-only connection."""
    if settings is None:
        settings = get_settings()

    st.header("Operations")
    summaries = _book_summaries(conn, settings)
    _waiting_card(conn, settings)
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
        st.caption(
            "Trust this over any 'last run ok' above: that run is for an earlier session. "
            "Normal before today's morning run; if it is still here after the run's usual "
            "time, see 'Waiting for you' at the top."
        )

    _kpis(data)
    _risk_card(conn, data.window)
    if data.open_orders_count and data.last_run_kind == "rebalance":
        st.info("Fills are recorded at the next scheduled run")
    symbols = _symbols(conn, data)
    palette = theme.palette()
    _ranking_hero(data, palette, symbols)
    left, right = st.columns(2)
    with left:
        _fills_card(data)
        _chains_card(data, symbols)
    with right:
        _alerts_card(data)
        _reconciliation_card(data.reconciliation)

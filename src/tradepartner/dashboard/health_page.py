"""Data-health page (Phase 2 T21, spec reqs 11 and 12).

Renders `health.health_report` at the current time, laid out per the
dashboard standard (docs/design/dashboard.md): a header with "as of", "last
updated" and a stale chip; a KPI row (coverage, interior gaps, delisted
names, last update); the hero chart of each session's missing share against
`ingest.max_missing_share`; supporting cards (bars per session, integrity
checks as status chips, per-source ingest status, survivorship gap,
unclassifiable and `snapshot_static` reliance, settings); and the gap,
delisted and missing-name tables. Read-only.

Two layers, like the other pages: `load_health_view` reads everything
through the connection it is given (the shell's single read-only
connection) and needs no Streamlit; `render` draws it.

**Per-session series.** For each session s in the chosen window, from rows
known at `t`: the bars dated s (`rows`, revisions included), and the coverage
population then (`live`): common and benchmark names whose current listing at
s (latest `valid_from` on or before s) was live at s, i.e. listed, or ended
(delisted or transferred) with its `end_session` on or after s (a transfer
with no end yet counts as live, as in `health`); how many of them have no bar
at s (`missing`), and that share. This is ingest's staleness check (spec req
10) replayed over the window with what the store knows now. Unlike `health`'s
coverage at the latest session, a name delisted *later* is live on the
sessions before its end, so a bar it lacked then shows as missing. One bars
query and one sweep over the listings serve the whole window.

**As of / last updated / stale.** "As of" is the latest `known_at` in any
fact table at or before `t`; "last updated" the latest finish of an `ok`
ingest run of any source; "stale" the number of sessions after the last
bar session up to the last completed session, shown as a warning chip when
positive.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.calendar import all_sessions, last_completed_session
from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, theme
from tradepartner.health import HealthReport, health_report
from tradepartner.store.classify import COMMON, classifications_as_of
from tradepartner.store.delistings import LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import securities_as_of

#: Sessions the window control starts with (a view default, not a threshold).
DEFAULT_WINDOW_SESSIONS: Final = 60

_SERIES_SCHEMA: Final[dict[str, Any]] = {
    "session": pl.Date,
    "rows": pl.Int64,
    "live": pl.Int64,
    "missing": pl.Int64,
    "missing_share": pl.Float64,
}


@dataclass(frozen=True, eq=False)
class HealthView:
    """Everything the page shows (module docstring)."""

    report: HealthReport
    series: pl.DataFrame
    window: tuple[date, date] | None
    threshold: float
    as_of: datetime | None
    last_updated: datetime | None
    stale_sessions: int | None


def _now() -> datetime:
    """The page's clock (tests pin it)."""
    return datetime.now(UTC)


def _window_sessions(window: tuple[date, date], last: date) -> list[date]:
    sessions = all_sessions()
    first, end = window[0], min(window[1], last)
    return list(sessions[bisect.bisect_left(sessions, first) : bisect.bisect_right(sessions, end)])


def _live_at(listing: dict[str, Any], session: date) -> bool:
    if listing["status"] == LISTED:
        return True
    end = listing["end_session"]
    if end is None:
        return bool(listing["status"] == TRANSFERRED)
    return bool(end >= session)


def _series(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, sessions: list[date]
) -> pl.DataFrame:
    if not sessions:
        return pl.DataFrame(schema=_SERIES_SCHEMA)
    rows_on: dict[date, int] = {}
    with_bar: dict[date, set[str]] = {}
    for session, sid in conn.execute(
        """
        SELECT session, security_id FROM prices_daily
        WHERE known_at <= ? AND session BETWEEN ? AND ?
        """,
        [t, sessions[0], sessions[-1]],
    ).fetchall():
        rows_on[session] = rows_on.get(session, 0) + 1
        with_bar.setdefault(session, set()).add(sid)
    securities, classes = securities_as_of(conn, t), classifications_as_of(conn, t)
    eligible = set(securities.filter(pl.col("benchmark"))["security_id"].to_list())
    eligible |= set(classes.filter(pl.col("security_type") == COMMON)["security_id"].to_list())
    listings = (
        listing_ends_as_of(conn, t, settings)
        .filter(pl.col("security_id").is_in(sorted(eligible)))
        .sort("valid_from", "security_id")
        .to_dicts()
    )
    current: dict[str, dict[str, Any]] = {}
    next_listing = 0
    out = []
    for session in sessions:
        while next_listing < len(listings) and listings[next_listing]["valid_from"] <= session:
            current[listings[next_listing]["security_id"]] = listings[next_listing]
            next_listing += 1
        live = {sid for sid, listing in current.items() if _live_at(listing, session)}
        missing = len(live - with_bar.get(session, set()))
        share = missing / len(live) if live else 0.0
        out.append((session, rows_on.get(session, 0), len(live), missing, share))
    return pl.DataFrame(out, schema=_SERIES_SCHEMA, orient="row")


def _stale_sessions(report: HealthReport) -> int | None:
    last_bar = report.coverage.last_bar
    if last_bar is None:
        return None
    sessions = all_sessions()
    return bisect.bisect_right(sessions, report.session) - bisect.bisect_right(sessions, last_bar)


def load_health_view(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    window: tuple[date, date] | None,
) -> HealthView:
    """The health report at `t` and the per-session series over `window` (clipped to
    the last completed session; no series when None), read through `conn` only."""
    report = health_report(conn, t, settings)
    finished = [i.last_ok_finished_at for i in report.ingests if i.last_ok_finished_at]
    return HealthView(
        report=report,
        series=_series(
            conn, t, settings, [] if window is None else _window_sessions(window, report.session)
        ),
        window=window,
        threshold=settings.ingest.max_missing_share,
        as_of=header.store_freshness(conn, t).as_of,
        last_updated=max(finished, default=None),
        stale_sessions=_stale_sessions(report),
    )


def _default_window(session: date) -> tuple[date, date]:
    sessions = all_sessions()
    index = bisect.bisect_right(sessions, session)
    return sessions[max(index - DEFAULT_WINDOW_SESSIONS, 0)], session


def _header(view: HealthView) -> None:
    header.render_freshness(header.Freshness(view.as_of, view.last_updated))
    if view.stale_sessions is None:
        theme.status_badge("no bars yet", "warning")
    elif view.stale_sessions:
        plural = "" if view.stale_sessions == 1 else "s"
        theme.status_badge(f"stale: {view.stale_sessions} session{plural}", "warning")


def _kpis(view: HealthView) -> None:
    report = view.report
    cov = report.coverage
    tiles = st.columns(4)
    tiles[0].metric(
        "Coverage",
        f"{cov.share:.1%}" if cov.live else "n/a",
        help=f"{len(cov.live) - len(cov.missing)} of {len(cov.live)} live names "
        f"have a bar on {cov.session}",
    )
    tiles[1].metric(
        "Interior gaps",
        report.gaps.names_with_gaps,
        help=f"{report.gaps.missing_sessions} missing sessions in total",
    )
    tiles[2].metric("Delisted names", report.delisted.count)
    tiles[3].metric("Last update", header.when(view.last_updated))


def _hero(view: HealthView, palette: theme.Palette) -> None:
    with st.container(border=True):
        st.subheader("Missing share per session")
        st.caption(f"Dashed line: ingest.max_missing_share = {view.threshold:.1%}")
        if _no_chart(view):
            return
        base = alt.Chart(view.series.to_pandas())
        bars = base.mark_bar(**theme.bar_mark(palette)).encode(
            x=alt.X("session:T", title=None),
            y=alt.Y("missing_share:Q", title="missing share", axis=alt.Axis(format="%")),
            tooltip=[
                alt.Tooltip("session:T"),
                alt.Tooltip("missing_share:Q", format=".1%"),
                alt.Tooltip("missing:Q"),
                alt.Tooltip("live:Q"),
                alt.Tooltip("rows:Q"),
            ],
        )
        rule = (
            alt.Chart(pl.DataFrame({"threshold": [view.threshold]}).to_pandas())
            .mark_rule(**theme.threshold_mark(palette))
            .encode(y="threshold:Q")
        )
        st.altair_chart(theme.style(bars + rule, palette), theme=None, width="stretch")


def _no_chart(view: HealthView) -> bool:
    """Say why there is nothing to plot, if so: a page never draws an empty frame."""
    if view.window is None:
        st.caption("Pick an end date for the window.")
    elif view.series.is_empty():
        st.caption("No sessions in the chosen window.")
    elif not (view.series["rows"].sum() or view.series["live"].sum()):
        st.caption("No bars or live names in the chosen window.")
    else:
        return False
    return True


def _bars_card(view: HealthView, palette: theme.Palette) -> None:
    with st.container(border=True):
        st.subheader("Bars per session")
        if _no_chart(view):
            return
        chart = (
            alt.Chart(view.series.to_pandas())
            .mark_bar(**theme.bar_mark(palette))
            .encode(
                x=alt.X("session:T", title=None),
                y=alt.Y("rows:Q", title="bars"),
                tooltip=[alt.Tooltip("session:T"), alt.Tooltip("rows:Q")],
            )
        )
        st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _integrity_card(report: HealthReport) -> None:
    with st.container(border=True):
        st.subheader("Integrity checks")
        for check in report.integrity:
            if check.passed:
                theme.status_badge(f"{check.rule}: pass", "good")
            else:
                theme.status_badge(f"{check.rule}: fail ({check.violations.height})", "critical")


def _sources_card(report: HealthReport) -> None:
    with st.container(border=True):
        st.subheader("Sources")
        for ingest in report.ingests:
            st.markdown(
                f"**{ingest.source}**: last ok {header.when(ingest.last_ok_finished_at)}"
                f" (cursor {ingest.last_ok_cursor or '-'})"
            )
            if ingest.latest_status is None:
                theme.status_badge("never run", "warning")
            elif ingest.latest_status == "ok":
                theme.status_badge("latest run: ok", "good")
            else:
                theme.status_badge(f"latest run: {ingest.latest_status}", "critical")
            if ingest.latest_message:
                st.caption(ingest.latest_message)


def _survivorship_card(report: HealthReport) -> None:
    gap = report.survivorship
    with st.container(border=True):
        st.subheader("Survivorship gap")
        st.markdown(
            f"Count share {gap.count_share:.2%} · size share {gap.size_share:.2%} · "
            f"{gap.missing.height} of {len(gap.listed)} listed names missing "
            f"(window after {gap.previous_rebalance})"
        )
        st.markdown(
            f"Side categories: unclassifiable {len(gap.unclassifiable)} · "
            f"truncated history {len(gap.truncated_history)} · "
            f"stale shares {len(gap.stale_shares)}"
        )


def _data_card(report: HealthReport) -> None:
    unclassified, static = report.unclassifiable, report.static_reliance
    with st.container(border=True):
        st.subheader("Classification and settings")
        st.markdown(
            f"Unclassifiable: {unclassified.count} "
            f"({len(unclassified.unclassifiable)} unclassifiable, "
            f"{len(unclassified.unclassified)} with no classification)"
        )
        tables = ", ".join(f"{k} {v}" for k, v in sorted(static.by_table.items())) or "none"
        st.markdown(f"snapshot_static reliance: {static.count} ({tables})")
        rule = "on" if report.settings["liquidity_rule_enabled"] else "off"
        st.markdown(f"Liquidity rule: {rule} · Fill price: {report.settings['fill_price']}")


def _tables(report: HealthReport) -> None:
    st.subheader("Gap report")
    st.dataframe(report.gaps.rows, hide_index=True)
    st.subheader(f"Delisted names ({report.delisted.count})")
    st.dataframe(report.delisted.frame, hide_index=True)
    missing = report.coverage.missing
    st.subheader(f"Live names with no bar on {report.coverage.session} ({len(missing)})")
    st.dataframe(pl.DataFrame({"security_id": list(missing)}), hide_index=True)
    st.subheader(f"Survivorship missing ({report.survivorship.missing.height})")
    st.dataframe(report.survivorship.missing, hide_index=True)


def render(conn: duckdb.DuckDBPyConnection) -> None:
    """Draw the data-health page from the shell's read-only connection."""
    settings = get_settings()
    t = _now()
    head, controls = st.columns([3, 2])
    head.subheader("Data health")
    picked = controls.date_input(
        "Window", value=_default_window(last_completed_session(t)), key="health_window"
    )
    picked_dates = list(picked) if isinstance(picked, tuple | list) else [picked]
    window = (picked_dates[0], picked_dates[1]) if len(picked_dates) == 2 else None
    view = load_health_view(conn, t, settings, window)
    with controls:
        _header(view)
    palette = theme.palette()
    _kpis(view)
    _hero(view, palette)
    left, right = st.columns(2)
    with left:
        _bars_card(view, palette)
        _integrity_card(view.report)
        _data_card(view.report)
    with right:
        _sources_card(view.report)
        _survivorship_card(view.report)
    _tables(view.report)

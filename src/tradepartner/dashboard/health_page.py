"""Data-health page (Phase 2 T21, spec reqs 11 and 12).

Renders `health.health_report` at the current time, laid out per the
dashboard standard (docs/design/dashboard.md): a header with "as of", "last
updated" and a stale chip; a KPI row (coverage, interior gaps, delisted
names, last update); the hero chart of each session's missing share against
`ingest.max_missing_share`; supporting cards (bars per session, integrity
checks as status chips, per-source ingest status, survivorship gap,
unclassifiable and `snapshot_static` reliance, settings, statement facts
(issue 660, T77c: the switch's state, coverage and derived share while it
is on, the last EDGAR run's `statement_*` counts and a warning badge while
`statement_vintage_late > 0`), the borrow-status block (issue 1506,
T171e: the switch, rows, the latest observation, the shortable and ETB
counts at it, the last run and the stale and switch warnings), the mergers
block (issue 1762, T248e: rows by kind, the latest `known_at`, the last
run's counts, and the report-only conflict and bar-after-effective-date
lists), the coverage
check against the named
reference (issue 1541, T180: "not computed" until the viewer presses its
button, since it costs one universe read per sample session; never computed
on a refresh, whatever `coverage.check_enabled` says)); and the gap,
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

**Sources.** The "latest run" chip is green for `ok`, orange (a caution,
not a failure) when the source has never run, and red for anything else. A
maintenance or repair run (`repair-resolution`'s `repaired`,
`ingest --fill-holes`'s `filled`, a `master-retract` or an `action-retract`
row) is not an ingest: `health.last_ingests` leaves it out, so it never shows
as the source's latest run and never hides a real ingest's status (issue 833,
issue 2125).
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
from tradepartner.health import (
    COVERAGE_REFERENCE_NAMES,
    INDEX_MEMBERS_MISSING,
    INGEST_TIME_ET,
    CoverageReference,
    HealthReport,
    IndexMembersCheck,
    health_report,
    health_verdict,
    stale_gap_expected,
    stale_sessions,
)
from tradepartner.store.classify import COMMON, classifications_as_of
from tradepartner.store.delistings import LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import securities_as_of

#: Sessions the window control starts with (a view default, not a threshold).
DEFAULT_WINDOW_SESSIONS: Final = 60

#: The coverage card's button: true only on the rerun its press triggers.
COVERAGE_BUTTON_KEY: Final = "health_coverage_reference"

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
    verdict: str
    gap_expected: bool


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


def load_health_view(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    window: tuple[date, date] | None,
    *,
    with_coverage_reference: bool = False,
) -> HealthView:
    """The health report at `t` and the per-session series over `window` (clipped to
    the last completed session; no series when None), read through `conn` only. The
    coverage-reference block is computed only when `with_coverage_reference` is true
    (the card's button), never on a plain refresh."""
    report = health_report(conn, t, settings, with_coverage_reference=with_coverage_reference)
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
        stale_sessions=stale_sessions(report),
        verdict=health_verdict(report),
        gap_expected=stale_gap_expected(report),
    )


def _default_window(session: date) -> tuple[date, date]:
    sessions = all_sessions()
    index = bisect.bisect_right(sessions, session)
    return sessions[max(index - DEFAULT_WINDOW_SESSIONS, 0)], session


def _header(view: HealthView) -> None:
    st.markdown(f"**{view.verdict}**")
    header.render_freshness(header.Freshness(view.as_of, view.last_updated))
    if view.stale_sessions is None:
        theme.status_badge("no bars yet", "warning")
    elif view.stale_sessions:
        plural = "" if view.stale_sessions == 1 else "s"
        note = (
            f" (expected until the {INGEST_TIME_ET:%H:%M} ET ingest)" if view.gap_expected else ""
        )
        theme.status_badge(f"stale: {view.stale_sessions} session{plural}{note}", "warning")


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
                pending = len(report.duplicate_dividends.pending)
                if check.rule == "duplicate_dividends" and pending:
                    theme.status_badge(
                        f"duplicate_dividends: pass ({pending} pending repair; "
                        "each payment is still counted twice)",
                        "warning",
                    )
                else:
                    theme.status_badge(f"{check.rule}: pass", "good")
            else:
                theme.status_badge(f"{check.rule}: fail ({check.violations.height})", "critical")
        label, tone = coverage_badge(report.coverage_reference)
        theme.status_badge(label, tone)


def coverage_badge(block: CoverageReference | None) -> tuple[str, theme.Status]:
    """`coverage_reference_names`' status chip: "not computed", a caution (never a
    pass it did not check), until the block is computed; "nothing checked" a
    caution too; a missing reference name is critical only while
    `coverage.check_enabled` is on, else a caution."""
    if block is None:
        return f"{COVERAGE_REFERENCE_NAMES}: not computed", "warning"
    label = f"{COVERAGE_REFERENCE_NAMES}: {block.verdict}"
    if block.failed and block.check_enabled:
        return label, "critical"
    return label, "good" if block.checked and not block.failed else "warning"


def _coverage_card(report: HealthReport, check_enabled: bool, index_gated: bool = False) -> None:
    """The coverage-reference card. Its button reruns the page, which then reads
    the button's state before loading the view (`render`)."""
    block = report.coverage_reference
    with st.container(border=True):
        st.subheader("Coverage against the reference list")
        st.button("Run coverage check", key=COVERAGE_BUTTON_KEY)
        if block is None:
            st.caption(
                "Not computed: it reads the universe once per sample session "
                "(`coverage.sample_months`)."
            )
            if check_enabled:
                theme.status_badge(
                    "coverage.check_enabled is on: `health --check` enforces this check",
                    "warning",
                )
            if index_gated:
                theme.status_badge(
                    "coverage.index_max_missing is set: `health --check` enforces "
                    f"{INDEX_MEMBERS_MISSING}",
                    "warning",
                )
            return
        mode = "check" if block.check_enabled else "report-only"
        st.markdown(f"Mode: **{mode}** (`coverage.check_enabled`)")
        st.dataframe(block.samples, hide_index=True)
        if not block.reference_missing.is_empty():
            st.markdown(f"Missing reference names ({block.reference_missing.height})")
            st.dataframe(block.reference_missing, hide_index=True)
        ratio = "n/a" if block.member_ratio is None else f"{block.member_ratio:.2f}"
        st.caption(
            f"Snapshot CIKs on universe exchanges: {block.snapshot_ciks}; "
            f"latest members per snapshot CIK: {ratio}"
        )
        if not block.late_known.is_empty():
            st.markdown(f"Late-known listings ({block.late_known.height})")
            st.dataframe(block.late_known, hide_index=True)
        _index_members_table(block.index_members)


def _index_members_table(index: IndexMembersCheck | None) -> None:
    """The card's second table (issue 1682, T189b): the S&P 500 answer key per
    sample session, from the report (the key's SHA included), then its misses.
    A check only: the key is never a universe or a signal."""
    if index is None:
        st.caption("S&P 500 answer key: off (`coverage.index_members`)")
        return
    label, tone = index_badge(index)
    st.markdown("S&P 500 answer key (a check only, never a source)")
    theme.status_badge(label, tone)
    if index.error is not None:
        return
    st.caption(f"Key commit `{index.commit}`, last dated row {index.through}")
    st.dataframe(index.samples, hide_index=True)
    if not index.misses.is_empty():
        st.markdown(f"Answer-key misses ({index.misses.height})")
        st.dataframe(index.misses, hide_index=True)


def index_badge(index: IndexMembersCheck) -> tuple[str, theme.Status]:
    """`index_members_missing`' chip: critical only when the block fails
    (`coverage.index_max_missing` set and exceeded); good when checked and
    within the threshold; else a caution (report-only misses, nothing checked,
    or the key unavailable)."""
    label = f"{INDEX_MEMBERS_MISSING}: {index.verdict}"
    if index.failed:
        return label, "critical"
    if index.max_missing is not None and index.checked:
        return label, "good"
    return label, "good" if index.checked and index.misses.is_empty() else "warning"


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
            f"stale shares {len(gap.stale_shares)} · "
            f"stale listings {gap.stale_listings.height}"
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
        filename = report.filename_listings
        st.markdown(
            f"Filing-name listings: {filename.rows} rows · "
            f"{filename.only_listing} securities rely only on them · "
            f"{filename.no_bars.height} spans without traded bars"
        )
        if not filename.no_bars.is_empty():
            st.dataframe(filename.no_bars, hide_index=True)
        rule = "on" if report.settings["liquidity_rule_enabled"] else "off"
        st.markdown(f"Liquidity rule: {rule} · Fill price: {report.settings['fill_price']}")


def _statement_card(report: HealthReport) -> None:
    statement = report.statement
    with st.container(border=True):
        st.subheader("Statement facts")
        state = "on" if statement.enabled else "off"
        st.markdown(f"Switch (`edgar.statement_facts_enabled`): **{state}**")
        if statement.coverage is None:
            st.caption("Switch is off: nothing to report.")
        else:
            cov = statement.coverage
            st.markdown(
                f"Coverage: {cov.fresh} of {cov.total} universe names "
                f"({cov.share:.1%}) have a fresh revenue and total assets row "
                "(rule 7's `universe.max_shares_age_days` bound)"
            )
            gross_profit = cov.derived + cov.reported
            st.markdown(
                f"Derived gross profit: {cov.derived} of {gross_profit} ({cov.derived_share:.1%})"
            )
        if statement.counts:
            counts = ", ".join(f"{k} {v}" for k, v in sorted(statement.counts.items()))
            st.caption(f"Last EDGAR run: {counts}")
            if {"non_usd", "malformed", "none"} & statement.counts.keys():
                st.caption("non_usd, malformed, none: parsed CIKs only (a cache hit adds zero)")
        if statement.vintage_late > 0:
            theme.status_badge(f"statement_vintage_late: {statement.vintage_late}", "warning")


def _rates_card(report: HealthReport) -> None:
    """Show each rate series' coverage, recent ingest and quality warnings."""
    with st.container(border=True):
        st.subheader("Rates")
        for item in report.rates.series:
            st.markdown(f"**{item.series}**: {item.rows} rows; {item.revised_keys} revised keys")
            st.caption(
                f"Latest observation {item.latest_observation_date or '-'} · "
                f"vintage {item.latest_vintage_date or '-'} · "
                f"known at {header.when(item.latest_known_at)} · "
                f"longest gap {item.longest_gap_sessions} XNYS sessions"
            )
            counts = ", ".join(f"{name} {value}" for name, value in sorted(item.run_counts.items()))
            stale = "n/a" if item.stale_sessions is None else str(item.stale_sessions)
            st.caption(f"Last FRED run: {counts or 'none'}; stale sessions {stale}")
            for warning in item.warnings:
                theme.status_badge(f"{item.series}: {warning}", "warning")
        st.caption(
            "This product uses the FRED® API but is not endorsed or certified "
            "by the Federal Reserve Bank of St. Louis."
        )


def _borrow_card(report: HealthReport) -> None:
    """The borrow-status block (issue 1506, T171e): rows, assets, the latest
    observation (the run cursor) and `known_at`, the shortable and ETB counts
    at it, the last run's counts, and each warning as a badge."""
    borrow = report.borrow
    with st.container(border=True):
        st.subheader("Borrow status")
        state = "on" if borrow.enabled else "off"
        st.markdown(f"Switch (`borrow.enabled`): **{state}**")
        st.markdown(f"{borrow.rows} rows · {borrow.assets} assets")
        st.caption(
            f"Latest observation {header.when(borrow.latest_observation)} · "
            f"latest known at {header.when(borrow.latest_known_at)}"
        )
        if borrow.latest_observation is not None:
            st.markdown(
                f"At the latest observation: {borrow.shortable} shortable, "
                f"{borrow.easy_to_borrow} easy to borrow (active assets)"
            )
        counts = ", ".join(f"{k} {v}" for k, v in sorted(borrow.run_counts.items()))
        st.caption(f"Last snapshot run: {borrow.last_run_status or 'never'}; {counts or 'none'}")
        for warning in borrow.warnings:
            theme.status_badge(warning, "warning")


def _mergers_card(report: HealthReport) -> None:
    """The mergers block (issue 1762, T248e): rows by kind, the latest
    `known_at`, the last Alpaca run's counts and the two report-only lists."""
    mergers = report.mergers
    with st.container(border=True):
        st.subheader("Mergers")
        st.markdown(" · ".join(f"{kind} {n}" for kind, n in mergers.rows_by_kind.items()))
        st.caption(f"Latest known at {header.when(mergers.latest_known_at)}")
        counts = ", ".join(f"{k} {v}" for k, v in sorted(mergers.run_counts.items()))
        st.caption(f"Last run: {counts or 'never'}")
        st.markdown(f"Merger conflicts ({mergers.conflicts.height})")
        if mergers.conflicts.height:
            st.dataframe(mergers.conflicts, hide_index=True)
        st.markdown(
            f"Acquirees with a bar after their effective date ({mergers.after_effective.height})"
        )
        if mergers.after_effective.height:
            st.dataframe(mergers.after_effective, hide_index=True)


def _tables(report: HealthReport) -> None:
    st.subheader("Gap report")
    st.dataframe(report.gaps.rows, hide_index=True)
    st.subheader(f"Delisted names ({report.delisted.count})")
    st.dataframe(report.delisted.frame, hide_index=True)
    reopened = report.listed_without_bars
    st.subheader(f"Listed with no bars after a Form 25 ({reopened.count})")
    st.dataframe(reopened.frame, hide_index=True)
    missing = report.coverage.missing
    st.subheader(f"Live names with no bar on {report.coverage.session} ({len(missing)})")
    st.dataframe(pl.DataFrame({"security_id": list(missing)}), hide_index=True)
    st.subheader(f"Survivorship missing ({report.survivorship.missing.height})")
    st.dataframe(report.survivorship.missing, hide_index=True)
    st.subheader(f"Stale listings, out of the gap ({report.survivorship.stale_listings.height})")
    st.dataframe(report.survivorship.stale_listings, hide_index=True)


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
    asked = bool(st.session_state.get(COVERAGE_BUTTON_KEY, False))
    view = load_health_view(conn, t, settings, window, with_coverage_reference=asked)
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
        _statement_card(view.report)
        _rates_card(view.report)
        _borrow_card(view.report)
        _mergers_card(view.report)
        _coverage_card(
            view.report,
            settings.coverage.check_enabled,
            index_gated=settings.coverage.index_max_missing is not None,
        )
    _tables(view.report)

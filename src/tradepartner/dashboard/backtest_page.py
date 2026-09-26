"""Backtest page (Phase 3 T43, spec req 17).

One trial at a time, picked from every trial in the registry: equity of the
strategy and both benchmarks on a log axis, drawdowns, monthly turnover and
cost bars, metrics per cost level, both deflated-Sharpe bases stored and
recomputed, per-rebalance gap, universe size, static reliance and late
dividends, the trial's kind and flag states, and the family's holdout
spends. Read-only, no run button: runs are CLI only.

Two layers, like the shell: `load_trial_view` reads everything the page
shows through the connection it is given (the shell's single read-only
connection) and computes the derived numbers (drawdowns, today's DSR); it
needs no Streamlit. `render` draws that view.

**Cost level.** Equity, drawdowns and per-rebalance rows are shown at the
trial's base cost level (`costs.per_side_bps` of its hypothesis), the level
N, V and DSR use (spec req 6). Metrics are shown at every stored level.

**Stored vs today's DSR** (spec req 8). The stored statistics used N and V
at run time and go stale as the family grows; the page recomputes both
bases with today's N and V (a fresh `registry.family_sharpes` read) from
the trial's own base-level `trial_metrics`, via `metrics.deflated_sharpe`,
and shows the two side by side, the stored one labelled "N at run time".
Only an `ok` trial has statistics; any other trial shows its state and
message and no results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.backtest.engine import STRATEGY_SERIES
from tradepartner.backtest.metrics import METRIC_KEYS, Basis, DeflatedSharpe, deflated_sharpe
from tradepartner.dashboard import header, theme
from tradepartner.store import registry, schema

_BASES: Final[tuple[Basis, ...]] = ("raw", "excess_spy")

#: `trial_results` columns holding each basis's stored (V, SR*, PSR(0), DSR).
_STORED_COLUMNS: Final[dict[Basis, tuple[str, str, str, str]]] = {
    "raw": ("sharpe_variance", "sr_star", "psr_zero", "dsr"),
    "excess_spy": ("sharpe_variance_excess", "sr_star_excess", "psr_zero_excess", "dsr_excess"),
}

_REBALANCE_COLUMNS: Final = (
    "session",
    "fill_session",
    "n_universe",
    "gap_count_share",
    "gap_size_share",
    "n_static_listings",
    "n_late_dividends",
    "n_dropped_dividends",
    "n_targets",
    "turnover",
    "cost_paid",
    "n_missing_fill",
    "n_delisting_exits",
    "n_stale_exits",
    "n_excluded_no_history",
)

Row = dict[str, Any]


@dataclass(frozen=True)
class DsrRow:
    """One DSR basis: the stored statistics and today's recomputation.
    `today` is None when it cannot be recomputed; `today_error` says why."""

    basis: Basis
    stored_basis: str | None
    stored_n: int | None
    stored_v: float | None
    stored_sr_star: float | None
    stored_psr_zero: float | None
    stored_dsr: float | None
    today: DeflatedSharpe | None
    today_error: str | None


@dataclass(frozen=True)
class TrialView:
    """Everything the page shows for one trial, read at render time."""

    trial: registry.TrialSummary
    hypothesis: registry.HypothesisRecord
    code_version: str
    code_dirty: bool | None
    data_cutoff: datetime | None
    holdout_reason: str | None
    gap_override_reason: str | None
    result: Row | None
    base_cost: float
    equity: list[Row]
    drawdowns: list[Row]
    rebalances: list[Row]
    metrics: list[Row]
    dsr_rows: tuple[DsrRow, ...]
    holdout_spends: list[registry.HoldoutSpend]


def drawdowns(equity: Sequence[Mapping[str, Any]]) -> list[Row]:
    """Per series, equity over its running peak minus one, in session order."""
    peaks: dict[str, float] = {}
    out: list[Row] = []
    for row in sorted(equity, key=lambda r: (r["series"], r["session"])):
        peak = max(peaks.get(row["series"], row["equity"]), row["equity"])
        peaks[row["series"]] = peak
        out.append(
            {
                "series": row["series"],
                "session": row["session"],
                "drawdown": row["equity"] / peak - 1,
            }
        )
    return out


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[object]) -> list[Row]:
    cursor = conn.execute(sql, list(params))
    names = [d[0] for d in cursor.description or ()]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _dsr_rows(
    conn: duckdb.DuckDBPyConnection,
    family: str,
    result: Row,
    base_metrics: Mapping[str, float | None],
) -> tuple[DsrRow, ...]:
    today: registry.FamilySharpes | None = None
    family_error: str | None = None
    try:
        today = registry.family_sharpes(conn, family)
    except registry.RegistryError as exc:
        family_error = str(exc)
    out = []
    for basis in _BASES:
        v, sr_star, psr_zero, dsr = (result[c] for c in _STORED_COLUMNS[basis])
        recomputed: DeflatedSharpe | None = None
        error = family_error
        if today is not None:
            pairs = today.raw if basis == "raw" else today.excess_spy
            try:
                recomputed = deflated_sharpe(
                    base_metrics, basis, n_trials=today.n_trials, pair_sharpes=pairs
                )
            except (KeyError, ValueError) as exc:
                error = str(exc)
        out.append(
            DsrRow(
                basis=basis,
                stored_basis=result["dsr_basis"],
                stored_n=result["n_trials"],
                stored_v=v,
                stored_sr_star=sr_star,
                stored_psr_zero=psr_zero,
                stored_dsr=dsr,
                today=recomputed,
                today_error=error,
            )
        )
    return tuple(out)


def load_trial_view(conn: duckdb.DuckDBPyConnection, trial_id: int) -> TrialView:
    """Read trial `trial_id` and everything the page shows for it."""
    [trial] = [
        t for t in registry.list_trials(conn, include_synthetic=True) if t.trial_id == trial_id
    ]
    hypothesis = registry.get_hypothesis_by_id(conn, trial.hypothesis_id)
    [extra] = _rows(
        conn,
        "SELECT code_version, code_dirty, data_cutoff, holdout_reason, gap_override_reason "
        "FROM trials WHERE trial_id = ?",
        [trial_id],
    )
    results = _rows(conn, "SELECT * FROM trial_results WHERE trial_id = ?", [trial_id])
    result = results[0] if results else None
    base = float(hypothesis.params[registry.BASE_COST_KEY])
    equity = _rows(
        conn,
        "SELECT series, session, equity FROM trial_equity "
        "WHERE trial_id = ? AND cost_per_side_bps = ? ORDER BY series, session",
        [trial_id, base],
    )
    rebalances = _rows(
        conn,
        f"SELECT {', '.join(_REBALANCE_COLUMNS)} FROM trial_rebalances "
        "WHERE trial_id = ? AND cost_per_side_bps = ? ORDER BY session",
        [trial_id, base],
    )
    metrics = _rows(
        conn,
        "SELECT series, cost_per_side_bps, metric, value FROM trial_metrics "
        "WHERE trial_id = ? ORDER BY series, cost_per_side_bps, metric",
        [trial_id],
    )
    base_metrics = {
        r["metric"]: r["value"]
        for r in metrics
        if r["series"] == "strategy" and r["cost_per_side_bps"] == base
    }
    dsr = (
        _dsr_rows(conn, hypothesis.family, result, base_metrics)
        if result is not None and result["status"] == "ok"
        else ()
    )
    return TrialView(
        trial=trial,
        hypothesis=hypothesis,
        code_version=extra["code_version"],
        code_dirty=extra["code_dirty"],
        data_cutoff=extra["data_cutoff"],
        holdout_reason=extra["holdout_reason"],
        gap_override_reason=extra["gap_override_reason"],
        result=result,
        base_cost=base,
        equity=equity,
        drawdowns=drawdowns(equity),
        rebalances=rebalances,
        metrics=metrics,
        dsr_rows=dsr,
        holdout_spends=registry.family_holdout_spends(conn, hypothesis.family),
    )


# --- rendering ---------------------------------------------------------------


def _trial_label(t: registry.TrialSummary) -> str:
    flags = " [synthetic]" if t.synthetic else ""
    return (
        f"#{t.trial_id} {t.slug} · {t.kind} · {t.start_session}→{t.end_session} · {t.status}{flags}"
    )


def _line_chart(
    rows: list[Row], y: str, title: str, palette: theme.Palette, *, log: bool = False, fmt: str = ""
) -> None:
    """The examined strategy in the accent, benchmarks muted and dashed, one y-axis."""
    if not rows:
        st.caption("No rows to plot.")
        return
    names = sorted({str(r["series"]) for r in rows})
    chart = (
        alt.Chart(pl.DataFrame(rows).to_pandas())
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("session:T", title="session"),
            y=alt.Y(
                f"{y}:Q",
                title=title,
                scale=alt.Scale(type="log") if log else alt.Undefined,
                axis=alt.Axis(format=fmt) if fmt else alt.Undefined,
            ),
            tooltip=[alt.Tooltip("session:T"), alt.Tooltip("series:N"), alt.Tooltip(f"{y}:Q")],
            **theme.series_encodings(palette, names, STRATEGY_SERIES),
        )
    )
    st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _bar_chart(rows: list[Row], y: str, title: str, palette: theme.Palette, fmt: str = "") -> None:
    if not rows:
        st.caption("No rows to plot.")
        return
    chart = (
        alt.Chart(pl.DataFrame([{"session": r["session"], y: r[y]} for r in rows]).to_pandas())
        .mark_bar(**theme.bar_mark(palette))
        .encode(
            x=alt.X("session:T", title="session"),
            y=alt.Y(f"{y}:Q", title=title, axis=alt.Axis(format=fmt) if fmt else alt.Undefined),
            tooltip=[alt.Tooltip("session:T"), alt.Tooltip(f"{y}:Q")],
        )
    )
    st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _metrics_table(view: TrialView) -> pl.DataFrame:
    columns = sorted({(r["series"], r["cost_per_side_bps"]) for r in view.metrics})
    values = {(r["series"], r["cost_per_side_bps"], r["metric"]): r["value"] for r in view.metrics}
    keys = [k for k in METRIC_KEYS if any(m == k for _, _, m in values)]
    return pl.DataFrame(
        [
            {"metric": key} | {f"{s} @ {c} bps": values.get((s, c, key)) for s, c in columns}
            for key in keys
        ]
    )


def _dsr_table(rows: Sequence[DsrRow]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "basis": r.basis,
                "stored label": r.stored_basis,
                "stored N": r.stored_n,
                "stored V": r.stored_v,
                "stored SR*": r.stored_sr_star,
                "stored PSR(0)": r.stored_psr_zero,
                "stored DSR (N at run time)": r.stored_dsr,
                "today N": r.today.n_trials if r.today else None,
                "today V": r.today.sharpe_variance if r.today else None,
                "today SR*": r.today.sr_star if r.today else None,
                "today label": r.today.dsr_basis if r.today else None,
                "today DSR": r.today.dsr if r.today else None,
            }
            for r in rows
        ],
        schema_overrides={"today N": pl.Int64, "stored N": pl.Int64},
    )


def _render_states(view: TrialView) -> None:
    trial, status = view.trial, view.trial.status
    st.subheader(f"Trial #{trial.trial_id}: {view.hypothesis.title}")
    dirty = " (dirty)" if view.code_dirty else ""
    st.markdown(
        f"**{trial.slug}** · family `{trial.family}` · kind `{trial.kind}` · "
        f"window {trial.start_session} → {trial.end_session} · status `{status}`"
    )
    st.caption(
        f"run by {trial.run_by} at {trial.started_at:%Y-%m-%d %H:%M} UTC · "
        f"code {view.code_version}{dirty} · data cutoff {view.data_cutoff} · "
        f"base cost {view.base_cost} bps per side"
        + (f" · note: {trial.note}" if trial.note else "")
    )
    if trial.synthetic:
        st.info("Synthetic trial: test data, not counted in N or V.")
    if trial.kind == "holdout":
        st.warning(f"Holdout run. Reason: {view.holdout_reason or '(none recorded)'}")
    if trial.holdout_repeat:
        st.warning("Holdout repeat: this family had already spent the holdout (domain rule 3).")
    if view.gap_override_reason:
        st.warning(f"Gap gate overridden by the owner: {view.gap_override_reason}")
    if view.result is not None and view.result["red_flag"]:
        st.warning(
            "Red flag: base-level excess CAGR over SPY is above "
            "`metrics.red_flag_excess_cagr_pp`. Audit for look-ahead and costs."
        )
    if status == registry.UNFINISHED:
        st.info("Unfinished: this trial has no result row (still running, or it crashed).")
    elif status != "ok":
        st.error(f"{status}: {trial.message or '(no message)'}")


def _render_results(view: TrialView) -> None:
    palette = theme.palette()
    st.subheader("Equity")
    st.caption(f"Strategy, SPY and MTUM at {view.base_cost} bps per side, log axis.")
    _line_chart(view.equity, "equity", "equity (log)", palette, log=True)

    st.subheader("Drawdowns")
    _line_chart(view.drawdowns, "drawdown", "drawdown", palette, fmt="%")

    st.subheader("Turnover and costs")
    st.caption("Per rebalance at the base cost level.")
    _bar_chart(view.rebalances, "turnover", "turnover (one-sided)", palette, fmt="%")
    _bar_chart(view.rebalances, "cost_paid", "cost paid ($)", palette)

    st.subheader("Metrics per cost level")
    st.dataframe(_metrics_table(view), hide_index=True)

    st.subheader("Deflated Sharpe")
    st.caption(
        "Stored: N at run time (N and V as they were when the trial ran; they go stale). "
        "Today: recomputed with the family's current N and V. Monthly, non-annualized Sharpe."
    )
    st.dataframe(_dsr_table(view.dsr_rows), hide_index=True)
    for row in view.dsr_rows:
        if row.today_error:
            st.warning(f"Today's {row.basis} DSR not recomputed: {row.today_error}")

    st.subheader("Per rebalance")
    st.caption("Survivorship gap, universe size, static-listing reliance and late dividends.")
    st.dataframe(pl.DataFrame(view.rebalances), hide_index=True)


def _render_holdout_spends(view: TrialView) -> None:
    st.subheader("Holdout spends")
    if not view.holdout_spends:
        st.markdown(f"Holdout not spent in family `{view.hypothesis.family}`.")
        return
    st.dataframe(
        pl.DataFrame(
            [
                {
                    "trial_id": s.trial_id,
                    "slug": s.slug,
                    "started_at": s.started_at,
                    "status": s.status,
                    "synthetic": s.synthetic,
                    "holdout_reason": s.holdout_reason,
                }
                for s in view.holdout_spends
            ]
        ),
        hide_index=True,
    )


def render(conn: duckdb.DuckDBPyConnection) -> None:
    """Draw the backtest page from the shell's read-only connection."""
    st.header("Backtest")
    header.render_freshness(header.store_freshness(conn, header.now()))
    try:
        schema.init_schema(conn)
    except schema.RegistryNotInitialised as exc:
        st.info(f"Trial registry not initialised: {exc}")
        return
    except schema.SchemaVersionError as exc:
        st.error(str(exc))
        return

    trials = registry.list_trials(conn, include_synthetic=True)
    if not trials:
        st.info("No trials yet. Run `tradepartner backtest <hypothesis>` to record one.")
        return
    # Options are ids under a fixed key, so a trial recorded or finished on the
    # CLI while the page is open changes the labels but not the pick.
    by_id = {t.trial_id: t for t in trials}
    picked = st.selectbox(
        "Trial", list(by_id), format_func=lambda i: _trial_label(by_id[i]), key="backtest_trial"
    )
    view = load_trial_view(conn, picked if picked is not None else trials[0].trial_id)

    _render_states(view)
    if view.trial.status == "ok":
        _render_results(view)
    else:
        st.markdown(f"No results for a trial with status `{view.trial.status}`.")
    _render_holdout_spends(view)

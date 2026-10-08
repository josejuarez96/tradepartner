"""Backtest page (Phase 3 T43, spec req 17).

One trial at a time, picked from every trial in the registry: equity of the
strategy and both benchmarks on a log axis, drawdowns, per-period turnover
and cost bars, metrics per cost level, both deflated-Sharpe bases stored and
recomputed, per-rebalance gap, universe size, static reliance and late
dividends, the trial's kind and flag states, and the family's holdout
spends. Read-only, no run button: runs are CLI only.

Two layers, like the shell: `load_trial_view` reads everything the page
shows through the connection it is given (the shell's single read-only
connection) and computes the derived numbers (drawdowns, today's DSR); it
needs no Streamlit. `render` draws that view.

**Cost level.** Equity, drawdowns and per-rebalance rows are shown at the
trial's base cost level (`costs.per_side_bps` of its hypothesis, read
through `frozen_values`), the level N, V and DSR use (spec req 6). Metrics
are shown at every stored level.

**Stored vs today's DSR** (spec req 8). The stored statistics used N and V
at run time and go stale as the family grows; the page recomputes both
bases with today's N (`results.family_n`) and V (a fresh
`registry.family_sharpes` read, annualised Sharpes) from the trial's own
base-level `trial_metrics` period keys at its cadence's periods per year, via
`metrics.deflated_sharpe`, and shows the two side by side, the stored one
labelled "N at run time". V and SR* are shown in annual units (strategy-lab
spec req 9): a result row whose `sharpe_unit` is NULL or `monthly` (written
before schema version 15) has its stored V times 12 and SR* times sqrt(12).
Only an `ok` trial has statistics; any other trial shows its state and
message and no results.

**Today's N split** (research-registry spec req 9; backtest spec req 8 as
amended 2026-10-07, issue 901). Today's N is `results.family_n_split`'s total and the
table shows its two parts beside it, backtest trials and research runs, with the
stored `trial_results.n_research` beside the stored N. On a store a read-only
connection opened before the research registry (no research tables, no
`n_research` column) both research cells are blank and today's N is the
backtest count.

**Detail level and the spend cap** (strategy-lab spec req 12 and amendment 12 to the
backtest spec's req 17; plan T112). A `full` trial shows its target weights at the
last rebalance; a `summary` trial (a sweep variant's) stores no weights, so the page
states its detail level beside its base-level equity, which it stores daily like a
`full` trial, and shows no weights table. A row without `detail_level` (a store
before schema version 15) is `full`. The holdout spends show the family cap
`holdout.decide` applies: the family rules' `max_family_holdout_spends`, or the live
`lab.max_family_holdout_spends` for a family without a rules row; on a store without
the lab tables there is no cap (the Phase 3 rules).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import altair as alt
import duckdb
import polars as pl
import streamlit as st

from tradepartner.backtest.engine import STRATEGY_SERIES
from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.metrics import METRIC_KEYS, Basis, DeflatedSharpe, deflated_sharpe
from tradepartner.backtest.results import CADENCE_KEY, FamilyN, family_n_split
from tradepartner.backtest.schedule import MONTHS_PER_YEAR, periods_per_year
from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header, theme
from tradepartner.store import lab_registry, lab_schema, registry, schema

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

#: `trial_results.sharpe_unit` values; NULL (a pre-version-15 row) reads as monthly.
_ANNUAL: Final = "annual"

#: `trials.detail_level` values (strategy-lab spec, Definitions "Detail level"); a
#: row without the column (before schema version 15) is `full`.
_FULL: Final = "full"
_SUMMARY: Final = "summary"

_WEIGHT_COLUMNS: Final = ("fill_session", "security_id", "target_weight", "fill_price", "shares")


def annual_stored(
    v: float | None, sr_star: float | None, unit: str | None
) -> tuple[float | None, float | None]:
    """A stored (V, SR*) in annual units: unchanged when `unit` is `annual`, else (a
    pre-lab monthly row) V times 12 and SR* times sqrt(12) (strategy-lab spec req 9)."""
    if unit == _ANNUAL:
        return v, sr_star
    return (
        None if v is None else v * MONTHS_PER_YEAR,
        None if sr_star is None else sr_star * math.sqrt(MONTHS_PER_YEAR),
    )


@dataclass(frozen=True)
class DsrRow:
    """One DSR basis: the stored statistics and today's recomputation.
    `today` is None when it cannot be recomputed; `today_error` says why. The N
    split: `stored_n_research` (None on a row written before the research
    registry), and today's `today_n_backtest` and `today_n_research` (None when
    the store has no research registry, or the family read failed)."""

    basis: Basis
    stored_basis: str | None
    stored_n: int | None
    stored_n_research: int | None
    stored_v: float | None
    stored_sr_star: float | None
    stored_psr_zero: float | None
    stored_dsr: float | None
    today: DeflatedSharpe | None
    today_error: str | None
    today_n_backtest: int | None = None
    today_n_research: int | None = None


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
    detail_level: str = _FULL
    weights: list[Row] | None = None
    max_holdout_spends: int | None = None


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
    ppy: int,
) -> tuple[DsrRow, ...]:
    today: registry.FamilySharpes | None = None
    split: FamilyN | None = None
    family_error: str | None = None
    try:
        today = registry.family_sharpes(conn, family)
        split = family_n_split(conn, family)
    except registry.RegistryError as exc:
        family_error = str(exc)
    out = []
    for basis in _BASES:
        stored_v, stored_sr_star, psr_zero, dsr = (result[c] for c in _STORED_COLUMNS[basis])
        v, sr_star = annual_stored(stored_v, stored_sr_star, result.get("sharpe_unit"))
        recomputed: DeflatedSharpe | None = None
        error = family_error
        if today is not None and split is not None:
            pairs = today.raw if basis == "raw" else today.excess_spy
            try:
                recomputed = deflated_sharpe(
                    base_metrics,
                    basis,
                    n_trials=split.total,
                    pair_sharpes=pairs,
                    periods_per_year=ppy,
                )
            except (KeyError, ValueError) as exc:
                error = str(exc)
        out.append(
            DsrRow(
                basis=basis,
                stored_basis=result["dsr_basis"],
                stored_n=result["n_trials"],
                stored_n_research=result.get("n_research"),
                stored_v=v,
                stored_sr_star=sr_star,
                stored_psr_zero=psr_zero,
                stored_dsr=dsr,
                today=recomputed,
                today_error=error,
                today_n_backtest=split.trials if split else None,
                today_n_research=split.research if split else None,
            )
        )
    return tuple(out)


def family_holdout_cap(
    conn: duckdb.DuckDBPyConnection, family: str, settings: Settings | None = None
) -> int | None:
    """The family's holdout spend cap (module docstring): None on a store without the
    lab tables; else the rules' cap, or the live `lab.max_family_holdout_spends`
    (`settings`, default `get_settings()`) for a family without a rules row."""
    if not lab_schema.is_lab_initialised(conn):
        return None
    rules = lab_registry.family_rules(conn, family)
    if rules is not None:
        return rules.max_family_holdout_spends
    live = settings if settings is not None else get_settings()
    return live.lab.max_family_holdout_spends


def _last_weights(conn: duckdb.DuckDBPyConnection, trial_id: int) -> list[Row]:
    """The trial's target weights at its last fill session, largest first."""
    return _rows(
        conn,
        f"SELECT {', '.join(_WEIGHT_COLUMNS)} FROM trial_weights WHERE trial_id = ? "
        "AND fill_session = (SELECT MAX(fill_session) FROM trial_weights WHERE trial_id = ?) "
        "ORDER BY target_weight DESC, security_id",
        [trial_id, trial_id],
    )


def load_trial_view(
    conn: duckdb.DuckDBPyConnection, trial_id: int, settings: Settings | None = None
) -> TrialView:
    """Read trial `trial_id` and everything the page shows for it; `settings` is
    read only for the live spend cap of a family without a rules row."""
    [trial] = [
        t for t in registry.list_trials(conn, include_synthetic=True) if t.trial_id == trial_id
    ]
    hypothesis = registry.get_hypothesis_by_id(conn, trial.hypothesis_id)
    # Every column, so a store before schema version 15 (no `detail_level`) still reads.
    [extra] = _rows(conn, "SELECT * FROM trials WHERE trial_id = ?", [trial_id])
    detail_level = str(extra.get("detail_level") or _FULL)
    results = _rows(conn, "SELECT * FROM trial_results WHERE trial_id = ?", [trial_id])
    result = results[0] if results else None
    frozen = frozen_values(hypothesis)
    base = float(frozen[registry.BASE_COST_KEY])
    ppy = periods_per_year(frozen[CADENCE_KEY])
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
        _dsr_rows(conn, hypothesis.family, result, base_metrics, ppy)
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
        detail_level=detail_level,
        weights=None if detail_level == _SUMMARY else _last_weights(conn, trial_id),
        max_holdout_spends=family_holdout_cap(conn, hypothesis.family, settings),
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
                "stored N research": r.stored_n_research,
                "stored V": r.stored_v,
                "stored SR*": r.stored_sr_star,
                "stored PSR(0)": r.stored_psr_zero,
                "stored DSR (N at run time)": r.stored_dsr,
                "today N": r.today.n_trials if r.today else None,
                "today N backtest": r.today_n_backtest,
                "today N research": r.today_n_research,
                "today V": r.today.sharpe_variance if r.today else None,
                "today SR*": r.today.sr_star if r.today else None,
                "today label": r.today.dsr_basis if r.today else None,
                "today DSR": r.today.dsr if r.today else None,
            }
            for r in rows
        ],
        schema_overrides={
            column: pl.Int64
            for column in (
                "stored N",
                "stored N research",
                "today N",
                "today N backtest",
                "today N research",
            )
        },
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
    st.caption(
        f"Strategy, SPY and MTUM at {view.base_cost} bps per side, log axis. "
        f"Detail level `{view.detail_level}`"
        + (
            ": daily equity at the base level only, other cost levels at rebalance "
            "sessions, no weights stored (strategy-lab spec req 12)."
            if view.detail_level == _SUMMARY
            else "."
        )
    )
    _line_chart(view.equity, "equity", "equity (log)", palette, log=True)

    st.subheader("Drawdowns")
    _line_chart(view.drawdowns, "drawdown", "drawdown", palette, fmt="%")

    st.subheader("Turnover and costs")
    st.caption("Per rebalance period at the base cost level.")
    _bar_chart(view.rebalances, "turnover", "turnover per period (one-sided)", palette, fmt="%")
    _bar_chart(view.rebalances, "cost_paid", "cost paid per period ($)", palette)

    st.subheader("Metrics per cost level")
    st.dataframe(_metrics_table(view), hide_index=True)

    st.subheader("Deflated Sharpe")
    st.caption(
        "Stored: N at run time (N and V as they were when the trial ran; they go stale). "
        "Today: recomputed with the family's current N and V. N is the family's backtest "
        "trials plus its research runs' configurations (research-registry spec req 9; "
        "blank research cells: the store has no research registry); V is the backtest "
        "trials' alone. V and SR* in annual units "
        "(rows stored in monthly units before the lab converted); DSR at the trial's own "
        "period."
    )
    st.dataframe(_dsr_table(view.dsr_rows), hide_index=True)
    for row in view.dsr_rows:
        if row.today_error:
            st.warning(f"Today's {row.basis} DSR not recomputed: {row.today_error}")

    st.subheader("Per rebalance")
    st.caption("Survivorship gap, universe size, static-listing reliance and late dividends.")
    st.dataframe(pl.DataFrame(view.rebalances), hide_index=True)

    if view.weights is not None:
        st.subheader("Weights")
        if view.weights:
            last = view.weights[0]["fill_session"]
            st.caption(f"Target weights at the last fill session, {last}.")
            st.dataframe(pl.DataFrame(view.weights), hide_index=True)
        else:
            st.caption("No weights stored for this trial.")


def _render_holdout_spends(view: TrialView) -> None:
    st.subheader("Holdout spends")
    family = view.hypothesis.family
    count = len(view.holdout_spends)
    no_cap = "no family cap (strategy lab not initialised: the Phase 3 rules)"
    if not view.holdout_spends:
        cap = (
            f"the family cap is {view.max_holdout_spends}"
            if view.max_holdout_spends is not None
            else no_cap
        )
        st.markdown(f"Holdout not spent in family `{family}`; {cap}.")
        return
    st.caption(
        f"Family `{family}`: {count} of {view.max_holdout_spends} holdout spends (the family cap)."
        if view.max_holdout_spends is not None
        else f"Family `{family}`: {count} holdout spends; {no_cap}."
    )
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


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the backtest page from the shell's read-only connection; `settings`
    defaults to `get_settings()` when the live spend cap is needed."""
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
    view = load_trial_view(conn, picked if picked is not None else trials[0].trial_id, settings)

    _render_states(view)
    if view.trial.status == "ok":
        _render_results(view)
    else:
        st.markdown(f"No results for a trial with status `{view.trial.status}`.")
    _render_holdout_spends(view)

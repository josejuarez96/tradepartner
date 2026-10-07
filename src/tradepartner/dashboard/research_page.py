"""Research view (research-registry spec req 15; plan T83c).

A read-only dashboard page for the research registry, drawn inside the shell's
single read-only connection (spec req 15): registrations with their block fields
and the budgets used along each amendment chain, per hypothesis family the
research run and configuration sums beside the family's backtest N, runs newest
first with every state req 15 names (outcome, verdict, confirmatory and its
basis, provenance, split, holdout spent and repeat; synthetic hidden by default;
failed, refused, abandoned and unfinished rows visible with their message),
datasets with their sealed splits and periods and the spends against them, and
the registry's decisions. A store before the migration shows "research registry
not initialised" (from `ResearchNotInitialised`).

Two layers, like the other pages: `load_research_view` reads everything through
the connection it is given (the shell's single read-only connection) and needs
no Streamlit; `render` draws that view. Read-only, no run button: runs are CLI
only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import duckdb
import polars as pl
import streamlit as st

from tradepartner.backtest.results import family_n
from tradepartner.dashboard import header
from tradepartner.store import research, schema

_INSTANT: Final = pl.Datetime("us", "UTC")

_REGISTRATIONS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "registration": pl.Int64(),
    "slug": pl.Utf8(),
    "kind": pl.Utf8(),
    "stage": pl.Int64(),
    "title": pl.Utf8(),
    "confirmatory": pl.Boolean(),
    "touches returns": pl.Boolean(),
    "provenance": pl.Utf8(),
    "family": pl.Utf8(),
    "dataset": pl.Utf8(),
    "window": pl.Utf8(),
    "splits": pl.Utf8(),
    "primary metric": pl.Utf8(),
    "direction": pl.Utf8(),
    "threshold": pl.Float64(),
    "CI level": pl.Float64(),
    "min clusters": pl.Int64(),
    "stop rule": pl.Utf8(),
    "budget runs": pl.Utf8(),
    "budget configurations": pl.Utf8(),
    "amends": pl.Int64(),
    "registered_by": pl.Utf8(),
    "known_at": _INSTANT,
}
_FAMILIES_SCHEMA: Final[dict[str, pl.DataType]] = {
    "family": pl.Utf8(),
    "research runs": pl.Int64(),
    "research configurations": pl.Int64(),
    "backtest N": pl.Int64(),
}
_RUNS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "run": pl.Int64(),
    "experiment": pl.Utf8(),
    "kind": pl.Utf8(),
    "family": pl.Utf8(),
    "split": pl.Utf8(),
    "outcome": pl.Utf8(),
    "verdict": pl.Utf8(),
    "confirmatory": pl.Boolean(),
    "basis": pl.Utf8(),
    "provenance": pl.Utf8(),
    "holdout spent": pl.Boolean(),
    "holdout repeat": pl.Boolean(),
    "configurations": pl.Int64(),
    "run_by": pl.Utf8(),
    "known_at": _INSTANT,
    "message": pl.Utf8(),
}
_DATASETS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "dataset": pl.Int64(),
    "name": pl.Utf8(),
    "version": pl.Utf8(),
    "rows": pl.Int64(),
    "events": pl.Utf8(),
    "splits (span)": pl.Utf8(),
    "sealed splits (spent)": pl.Utf8(),
    "sealed periods (spent)": pl.Utf8(),
    "locked": pl.Boolean(),
    "seed": pl.Int64(),
    "known_at": _INSTANT,
}
_DECISIONS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "decision": pl.Int64(),
    "kind": pl.Utf8(),
    "registration": pl.Int64(),
    "run": pl.Int64(),
    "made_by": pl.Utf8(),
    "reason": pl.Utf8(),
    "values": pl.Utf8(),
    "known_at": _INSTANT,
}


@dataclass(frozen=True)
class RegistrationRow:
    """One `research_registrations` row, with its chain's usage beside it."""

    registration_id: int
    slug: str
    kind: str
    stage: int
    title: str
    confirmatory: bool
    provenance: str
    touches_returns: bool
    family: str | None
    dataset_name: str
    window_start: date
    window_end: date
    splits: tuple[str, ...]
    primary_metric: str
    primary_direction: str
    primary_threshold: float | None
    primary_ci_level: float
    primary_min_clusters: int
    stop_rule: str
    budget_runs: int
    budget_configurations: int
    amends_registration_id: int | None
    registered_by: str
    known_at: datetime
    chain_runs: int
    chain_configurations: int


@dataclass(frozen=True)
class FamilySummary:
    """One hypothesis family's research sums beside its backtest N (req 6)."""

    family: str
    runs: int
    configurations: int
    backtest_n: int


@dataclass(frozen=True)
class DatasetRow:
    """One `research_datasets` row, with the sealed splits and periods a run
    has spent (req 11) marked."""

    dataset_id: int
    name: str
    version: str
    n_rows: int | None
    event_start: date
    event_end: date
    split_spans: Mapping[str, tuple[date, date]]
    sealed_splits: tuple[str, ...]
    sealed_periods: tuple[tuple[date, date], ...]
    spent_splits: tuple[str, ...]
    spent_periods: tuple[tuple[date, date], ...]
    locked: bool
    seed: int | None
    known_at: datetime


@dataclass(frozen=True)
class DecisionRow:
    """One `research_decisions` row (a holdout spend or a budget amendment)."""

    decision_id: int
    kind: str
    registration_id: int | None
    run_id: int | None
    values_json: str
    reason: str
    made_by: str
    known_at: datetime


@dataclass(frozen=True)
class ResearchView:
    """Everything the page shows, read at render time."""

    registrations: tuple[RegistrationRow, ...]
    families: tuple[FamilySummary, ...]
    runs: tuple[research.RunSummary, ...]
    datasets: tuple[DatasetRow, ...]
    decisions: tuple[DecisionRow, ...]


# --- reading ------------------------------------------------------------------


def _rows(
    conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[object] = ()
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, list(params))
    names = [d[0] for d in cursor.description or ()]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _chain_usage(runs: Sequence[research.RunSummary]) -> dict[str, tuple[int, int]]:
    """Per slug, the run count and the configurations declared over every
    non-synthetic run of its amendment chain, whatever the outcome (req 6)."""
    counts: dict[str, int] = {}
    configurations: dict[str, int] = {}
    for run in runs:
        if run.synthetic:
            continue
        counts[run.slug] = counts.get(run.slug, 0) + 1
        configurations[run.slug] = configurations.get(run.slug, 0) + run.n_configurations_declared
    return {slug: (count, configurations.get(slug, 0)) for slug, count in counts.items()}


def _registrations(
    conn: duckdb.DuckDBPyConnection, usage: Mapping[str, tuple[int, int]]
) -> tuple[RegistrationRow, ...]:
    rows = _rows(
        conn,
        "SELECT registration_id, slug, kind, stage, title, confirmatory, provenance, "
        "touches_returns, family, dataset_name, window_start, window_end, splits_json, "
        "primary_metric, primary_direction, primary_threshold, primary_ci_level, "
        "primary_min_clusters, stop_rule, budget_runs, budget_configurations, "
        "amends_registration_id, registered_by, known_at "
        "FROM research_registrations ORDER BY registration_id DESC",
    )
    out: list[RegistrationRow] = []
    for row in rows:
        chain_runs, chain_configurations = usage.get(row["slug"], (0, 0))
        out.append(
            RegistrationRow(
                registration_id=row["registration_id"],
                slug=row["slug"],
                kind=row["kind"],
                stage=row["stage"],
                title=row["title"],
                confirmatory=row["confirmatory"],
                provenance=row["provenance"],
                touches_returns=row["touches_returns"],
                family=row["family"],
                dataset_name=row["dataset_name"],
                window_start=row["window_start"],
                window_end=row["window_end"],
                splits=tuple(json.loads(row["splits_json"])),
                primary_metric=row["primary_metric"],
                primary_direction=row["primary_direction"],
                primary_threshold=row["primary_threshold"],
                primary_ci_level=row["primary_ci_level"],
                primary_min_clusters=row["primary_min_clusters"],
                stop_rule=row["stop_rule"],
                budget_runs=row["budget_runs"],
                budget_configurations=row["budget_configurations"],
                amends_registration_id=row["amends_registration_id"],
                registered_by=row["registered_by"],
                known_at=row["known_at"],
                chain_runs=chain_runs,
                chain_configurations=chain_configurations,
            )
        )
    return tuple(out)


def _families(
    conn: duckdb.DuckDBPyConnection,
    registrations: Sequence[RegistrationRow],
    runs: Sequence[research.RunSummary],
) -> tuple[FamilySummary, ...]:
    """Per family named by a registration, its non-synthetic research run and
    configuration sums beside the family's backtest N (req 6). The backtest N is
    `backtest.results.family_n`, the one N function (never recomputed here)."""
    families = sorted({r.family for r in registrations if r.family is not None})
    out: list[FamilySummary] = []
    for family in families:
        counted = [run for run in runs if run.family == family and not run.synthetic]
        out.append(
            FamilySummary(
                family=family,
                runs=len(counted),
                configurations=sum(run.n_configurations_declared for run in counted),
                backtest_n=family_n(conn, family),
            )
        )
    return tuple(out)


def _dataset_spends(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, set[str]], dict[str, set[tuple[date, date]]]]:
    """The sealed splits and periods each dataset name has spent, from the
    `holdout_spend` decisions (req 5). Keyed by dataset name across versions,
    like the gate, and synthetic runs are excluded, as the gate excludes them."""
    split_spends: dict[str, set[str]] = {}
    period_spends: dict[str, set[tuple[date, date]]] = {}
    rows = conn.execute(
        "SELECT ds.name, x.values_json FROM research_decisions x "
        "JOIN research_runs r ON r.run_id = x.run_id "
        "JOIN research_datasets ds ON ds.dataset_id = r.dataset_id "
        "WHERE x.kind = 'holdout_spend' AND NOT r.synthetic"
    ).fetchall()
    for name, raw in rows:
        values = json.loads(raw)
        if values.get("sealed_split") is not None:
            split_spends.setdefault(name, set()).add(values["sealed_split"])
        for start, end in values.get("sealed_periods") or []:
            period_spends.setdefault(name, set()).add(
                (date.fromisoformat(start), date.fromisoformat(end))
            )
    return split_spends, period_spends


def _datasets(conn: duckdb.DuckDBPyConnection) -> tuple[DatasetRow, ...]:
    split_spends, period_spends = _dataset_spends(conn)
    rows = _rows(
        conn,
        "SELECT dataset_id, name, version, n_rows, event_start, event_end, split_spans_json, "
        "sealed_splits_json, sealed_periods_json, locked, seed, known_at "
        "FROM research_datasets ORDER BY dataset_id DESC",
    )
    out: list[DatasetRow] = []
    for row in rows:
        spans = {
            name: (date.fromisoformat(start), date.fromisoformat(end))
            for name, (start, end) in json.loads(row["split_spans_json"]).items()
        }
        dataset_id = row["dataset_id"]
        name = row["name"]
        out.append(
            DatasetRow(
                dataset_id=dataset_id,
                name=name,
                version=row["version"],
                n_rows=row["n_rows"],
                event_start=row["event_start"],
                event_end=row["event_end"],
                split_spans=spans,
                sealed_splits=tuple(json.loads(row["sealed_splits_json"])),
                sealed_periods=tuple(
                    (date.fromisoformat(start), date.fromisoformat(end))
                    for start, end in json.loads(row["sealed_periods_json"])
                ),
                spent_splits=tuple(sorted(split_spends.get(name, set()))),
                spent_periods=tuple(sorted(period_spends.get(name, set()))),
                locked=row["locked"],
                seed=row["seed"],
                known_at=row["known_at"],
            )
        )
    return tuple(out)


def _decisions(conn: duckdb.DuckDBPyConnection) -> tuple[DecisionRow, ...]:
    rows = _rows(
        conn,
        "SELECT decision_id, kind, registration_id, run_id, values_json, reason, made_by, "
        "known_at FROM research_decisions ORDER BY decision_id DESC",
    )
    return tuple(DecisionRow(**row) for row in rows)


def load_research_view(
    conn: duckdb.DuckDBPyConnection, include_synthetic: bool = False
) -> ResearchView:
    """Read the whole research view through `conn` (the shell's single
    read-only connection); synthetic runs are hidden unless asked for."""
    schema.require_research(conn)
    runs = tuple(research.list_runs(conn, include_synthetic=include_synthetic))
    registrations = _registrations(conn, _chain_usage(runs))
    return ResearchView(
        registrations=registrations,
        families=_families(conn, registrations, runs),
        runs=runs,
        datasets=_datasets(conn),
        decisions=_decisions(conn),
    )


# --- rendering ----------------------------------------------------------------


def _window(start: date, end: date) -> str:
    return f"{start.isoformat()} to {end.isoformat()}"


def _periods(spans: Sequence[tuple[date, date]]) -> str:
    return "; ".join(_window(start, end) for start, end in spans) or "-"


def _split_spans(spans: Mapping[str, tuple[date, date]]) -> str:
    return ", ".join(
        f"{name}: {_window(start, end)}" for name, (start, end) in sorted(spans.items())
    )


def _marked(values: Sequence[str], spent: Sequence[str]) -> str:
    if not values:
        return "-"
    spent_set = set(spent)
    return ", ".join(f"{value} (spent)" if value in spent_set else value for value in values)


def _marked_periods(
    periods: Sequence[tuple[date, date]], spent: Sequence[tuple[date, date]]
) -> str:
    if not periods:
        return "-"
    spent_set = set(spent)
    return "; ".join(
        f"{_window(start, end)} (spent)" if (start, end) in spent_set else _window(start, end)
        for start, end in periods
    )


def _registrations_table(rows: Sequence[RegistrationRow]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "registration": row.registration_id,
                "slug": row.slug,
                "kind": row.kind,
                "stage": row.stage,
                "title": row.title,
                "confirmatory": row.confirmatory,
                "touches returns": row.touches_returns,
                "provenance": row.provenance,
                "family": row.family,
                "dataset": row.dataset_name,
                "window": _window(row.window_start, row.window_end),
                "splits": ", ".join(row.splits) or "-",
                "primary metric": row.primary_metric,
                "direction": row.primary_direction,
                "threshold": row.primary_threshold,
                "CI level": row.primary_ci_level,
                "min clusters": row.primary_min_clusters,
                "stop rule": row.stop_rule,
                "budget runs": f"{row.chain_runs}/{row.budget_runs}",
                "budget configurations": (
                    f"{row.chain_configurations}/{row.budget_configurations}"
                ),
                "amends": row.amends_registration_id,
                "registered_by": row.registered_by,
                "known_at": row.known_at,
            }
            for row in rows
        ],
        schema=_REGISTRATIONS_SCHEMA,
    )


def _families_table(rows: Sequence[FamilySummary]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "family": row.family,
                "research runs": row.runs,
                "research configurations": row.configurations,
                "backtest N": row.backtest_n,
            }
            for row in rows
        ],
        schema=_FAMILIES_SCHEMA,
    )


def _runs_table(rows: Sequence[research.RunSummary]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "run": row.run_id,
                "experiment": row.slug,
                "kind": row.kind,
                "family": row.family,
                "split": row.split,
                "outcome": row.outcome,
                "verdict": row.verdict,
                "confirmatory": row.confirmatory,
                "basis": row.confirmatory_basis,
                "provenance": row.provenance,
                "holdout spent": row.holdout_spent,
                "holdout repeat": row.holdout_repeat,
                "configurations": row.n_configurations_declared,
                "run_by": row.run_by,
                "known_at": row.known_at,
                "message": row.message,
            }
            for row in rows
        ],
        schema=_RUNS_SCHEMA,
    )


def _datasets_table(rows: Sequence[DatasetRow]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "dataset": row.dataset_id,
                "name": row.name,
                "version": row.version,
                "rows": row.n_rows,
                "events": _window(row.event_start, row.event_end),
                "splits (span)": _split_spans(row.split_spans),
                "sealed splits (spent)": _marked(row.sealed_splits, row.spent_splits),
                "sealed periods (spent)": _marked_periods(row.sealed_periods, row.spent_periods),
                "locked": row.locked,
                "seed": row.seed,
                "known_at": row.known_at,
            }
            for row in rows
        ],
        schema=_DATASETS_SCHEMA,
    )


def _decisions_table(rows: Sequence[DecisionRow]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "decision": row.decision_id,
                "kind": row.kind,
                "registration": row.registration_id,
                "run": row.run_id,
                "made_by": row.made_by,
                "reason": row.reason,
                "values": row.values_json,
                "known_at": row.known_at,
            }
            for row in rows
        ],
        schema=_DECISIONS_SCHEMA,
    )


def render(conn: duckdb.DuckDBPyConnection) -> None:
    """Draw the research view from the shell's read-only connection."""
    st.header("Research")
    header.render_freshness(header.store_freshness(conn, header.now()))
    try:
        schema.init_schema(conn)
        schema.require_research(conn)
    except schema.ResearchNotInitialised as exc:
        st.info(f"Research registry not initialised: {exc}")
        return
    except schema.RegistryNotInitialised as exc:
        st.info(f"Trial registry not initialised: {exc}")
        return
    except schema.SchemaVersionError as exc:
        st.error(str(exc))
        return

    show_synthetic = st.checkbox("Show synthetic runs", value=False, key="research_synthetic")
    view = load_research_view(conn, include_synthetic=show_synthetic)
    if not view.registrations and not view.datasets and not view.decisions:
        st.info(
            "No research registrations yet. Run `tradepartner experiment register <file>` "
            "to record one."
        )
        return

    st.subheader("Registrations")
    if view.registrations:
        st.caption(
            "Budgets are used/declared along each slug's amendment chain; a non-synthetic run "
            "counts whatever its outcome."
        )
        st.dataframe(_registrations_table(view.registrations), hide_index=True)
    else:
        st.markdown("No registrations.")

    st.subheader("Families")
    if view.families:
        st.caption(
            "Research runs and configurations over every slug naming the family, "
            "beside the family's backtest N."
        )
        st.dataframe(_families_table(view.families), hide_index=True)
    else:
        st.markdown("No hypothesis family is named by a registration.")

    st.subheader("Runs")
    st.caption("Newest first. Synthetic runs are hidden unless asked for.")
    if view.runs:
        st.dataframe(_runs_table(view.runs), hide_index=True)
    else:
        st.markdown("No runs.")

    st.subheader("Datasets")
    if view.datasets:
        st.caption("Sealed splits and periods; a spent one is marked.")
        st.dataframe(_datasets_table(view.datasets), hide_index=True)
    else:
        st.markdown("No datasets.")

    st.subheader("Decisions")
    if view.decisions:
        st.dataframe(_decisions_table(view.decisions), hide_index=True)
    else:
        st.markdown("No decisions.")

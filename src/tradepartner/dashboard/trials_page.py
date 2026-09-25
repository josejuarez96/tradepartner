"""Trial-registry view (Phase 3 T44, spec req 17).

Every trial newest first, with a hypothesis filter and synthetic trials
hidden unless asked for; failed, refused and unfinished trials stay in the
list with their message, so nothing that was run disappears from view
(spec "Definitions" > Trial: every run is a trial). Below them, the owner's
decisions (gap sign-offs, gap overrides, holdout spends), newest first.
Read-only, no run button: runs are CLI only.

Two layers, like the backtest page: `load_registry_view` reads everything
through the connection it is given (the shell's single read-only
connection) and needs no Streamlit; `render` draws it.

**Decisions under a filter.** A decision belongs to a hypothesis when it
names that hypothesis, or names a trial of it. A decision naming neither is
shown only unfiltered.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import duckdb
import polars as pl
import streamlit as st

from tradepartner.store import registry, schema

_ALL: Final = "All hypotheses"


@dataclass(frozen=True)
class DecisionRow:
    """One `owner_decisions` row, with the hypothesis it belongs to."""

    decision_id: int
    made_at: datetime
    kind: str
    slug: str | None
    trial_id: int | None
    values_json: str
    reason: str


@dataclass(frozen=True)
class RegistryView:
    """What the page shows for one filter setting."""

    trials: tuple[registry.TrialSummary, ...]
    slugs: tuple[str, ...]
    decisions: tuple[DecisionRow, ...]

    @property
    def status_counts(self) -> dict[str, int]:
        """Trials per status among those listed."""
        return dict(Counter(t.status for t in self.trials))


def _decisions(conn: duckdb.DuckDBPyConnection, slug: str | None) -> tuple[DecisionRow, ...]:
    rows = conn.execute(
        "SELECT d.decision_id, d.made_at, d.kind, COALESCE(h.slug, th.slug), d.trial_id, "
        "d.values_json, d.reason "
        "FROM owner_decisions d "
        "LEFT JOIN hypotheses h ON h.hypothesis_id = d.hypothesis_id "
        "LEFT JOIN trials t ON t.trial_id = d.trial_id "
        "LEFT JOIN hypotheses th ON th.hypothesis_id = t.hypothesis_id "
        "WHERE ? IS NULL OR h.slug = ? OR th.slug = ? "
        "ORDER BY d.decision_id DESC",
        [slug, slug, slug],
    ).fetchall()
    return tuple(DecisionRow(*row) for row in rows)


def load_registry_view(
    conn: duckdb.DuckDBPyConnection, slug: str | None = None, include_synthetic: bool = False
) -> RegistryView:
    """Trials (newest first) and decisions (newest first) for `slug`, or for
    every hypothesis when `slug` is None; synthetic trials only when
    `include_synthetic`. `slugs` is every registered hypothesis, for the
    filter."""
    trials = registry.list_trials(conn, slug=slug, include_synthetic=include_synthetic)
    slugs = tuple(
        row[0] for row in conn.execute("SELECT slug FROM hypotheses ORDER BY slug").fetchall()
    )
    return RegistryView(tuple(trials), slugs, _decisions(conn, slug))


def _trials_table(trials: tuple[registry.TrialSummary, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "trial": t.trial_id,
                "hypothesis": t.slug,
                "kind": t.kind,
                "status": t.status,
                "message": t.message,
                "window": f"{t.start_session.isoformat()} to {t.end_session.isoformat()}",
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "synthetic": t.synthetic,
                "holdout_repeat": t.holdout_repeat,
                "run_by": t.run_by,
                "note": t.note,
            }
            for t in trials
        ]
    )


def _decisions_table(decisions: tuple[DecisionRow, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "decision": d.decision_id,
                "made_at": d.made_at,
                "kind": d.kind,
                "hypothesis": d.slug,
                "trial": d.trial_id,
                "reason": d.reason,
                "values": d.values_json,
            }
            for d in decisions
        ]
    )


def render(conn: duckdb.DuckDBPyConnection) -> None:
    """Draw the trial-registry view from the shell's read-only connection."""
    st.header("Trial registry")
    try:
        schema.init_schema(conn)
    except schema.RegistryNotInitialised as exc:
        st.info(f"Trial registry not initialised: {exc}")
        return
    except schema.SchemaVersionError as exc:
        st.error(str(exc))
        return

    everything = load_registry_view(conn, include_synthetic=True)
    if not everything.trials and not everything.decisions:
        st.info("No trials yet. Run `tradepartner backtest <hypothesis>` to record one.")
        return

    picked = st.selectbox("Hypothesis", [_ALL, *everything.slugs], key="trials_hypothesis")
    show_synthetic = st.checkbox("Show synthetic trials", value=False, key="trials_synthetic")
    view = load_registry_view(
        conn, slug=None if picked in (None, _ALL) else picked, include_synthetic=show_synthetic
    )

    st.subheader("Trials")
    counts = view.status_counts
    st.caption(
        f"{len(view.trials)} trials, newest first: "
        + ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        if view.trials
        else "No trials match."
    )
    if view.trials:
        st.dataframe(_trials_table(view.trials), hide_index=True)

    st.subheader("Owner decisions")
    if view.decisions:
        st.dataframe(_decisions_table(view.decisions), hide_index=True)
    else:
        st.markdown("No owner decisions recorded.")

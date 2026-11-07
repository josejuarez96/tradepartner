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

**Families and sweeps** (strategy-lab spec req 14; plan T112). Above the trial
table, `load_lab_view` builds one card per family and one row per sweep (the
latest registration of each slug) from `sweep_report.lab_status` (the family's N
from `results.family_n`, V per basis from one `registry.family_sharpes` read per
family, each one set-based `trial_metrics` query per basis, the declared
count and the rules) and `sweep_report.sweep_report` per sweep (the counts, the
state, the selection-statistic quartiles, the recomputed `dsr_excess` share, the
red flags and, only for a complete sweep, the argmax and its verdicts), plus the
card's parent N (`results.family_n`), holdout spends (`registry.family_holdout_spends`)
with the cap, promotions with the cap and the SR\\* high-water mark
(`lab_registry.family_sr_star_high_water_mark`). A family without a rules row
(a Phase 3 family) shows the live `lab.*` caps, which is what `backtest` applies
to it. "SR\\* at declared count" is the family's sweep reports' figure; a family
with no sweep has none declared, so it equals SR\\*. A sweep's promotion and
retirement rows are the `owner_decisions` of kind `promotion` or `sweep_retired`
whose `values_json` names its registration as `sweep_id` (spec req 4: a
promotion's values carry the sweep id). Every read goes through the shell's one
read-only connection; nothing here writes. A store without the lab tables shows
"strategy lab not initialised" (`LabNotInitialised`) and the trial table as
before.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import duckdb
import polars as pl
import streamlit as st

from tradepartner.backtest import results, sweep_report
from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import header
from tradepartner.store import lab_registry, registry, schema
from tradepartner.store.lab_registry import FamilyRules
from tradepartner.store.lab_schema import LabNotInitialised, require_lab

_ALL: Final = "All hypotheses"
_INSTANT: Final = pl.Datetime("us", "UTC")

#: Explicit schemas: polars would infer each column's type from the first
#: 100 rows, so a nullable text column (a message, a note) that is empty in
#: the newest 100 rows would be typed null and reject an older value.
_TRIALS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "trial": pl.Int64(),
    "hypothesis": pl.Utf8(),
    "kind": pl.Utf8(),
    "status": pl.Utf8(),
    "message": pl.Utf8(),
    "window": pl.Utf8(),
    "started_at": _INSTANT,
    "finished_at": _INSTANT,
    "synthetic": pl.Boolean(),
    "holdout_repeat": pl.Boolean(),
    "run_by": pl.Utf8(),
    "note": pl.Utf8(),
}
_DECISIONS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "decision": pl.Int64(),
    "made_at": _INSTANT,
    "kind": pl.Utf8(),
    "hypothesis": pl.Utf8(),
    "trial": pl.Int64(),
    "reason": pl.Utf8(),
    "values": pl.Utf8(),
}


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
        ],
        schema=_TRIALS_SCHEMA,
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
        ],
        schema=_DECISIONS_SCHEMA,
    )


# --- families and sweeps (strategy-lab spec req 14) ----------------------------------

#: The `owner_decisions` kinds a sweep row lists (spec req 4).
_SWEEP_DECISION_KINDS: Final = ("promotion", "sweep_retired")


@dataclass(frozen=True)
class FamilyCard:
    """One family's card (spec req 14). V and SR\\* are annual; `rules` is None for
    a family with no `family_rules` row, whose caps are then the live `lab.*` keys
    and which has no SR\\* high-water mark."""

    family: str
    n: int
    sharpe_variance_annual_raw: float | None
    sharpe_variance_annual_excess: float | None
    sr_star_annual: float | None
    declared_count: int
    n_at_declared_count: int
    sr_star_annual_at_declared_count: float | None
    rules: FamilyRules | None
    parent_n: int | None
    holdout_spends: tuple[registry.HoldoutSpend, ...]
    max_holdout_spends: int
    promotions: tuple[DecisionRow, ...]
    max_promotions: int
    sr_star_high_water_annual: float | None


@dataclass(frozen=True)
class SweepLine:
    """One sweep (its latest registration): the report and its promotion and
    retirement decisions."""

    report: sweep_report.SweepReport
    decisions: tuple[DecisionRow, ...]


@dataclass(frozen=True)
class LabView:
    """The families cards and the sweeps table."""

    families: tuple[FamilyCard, ...]
    sweeps: tuple[SweepLine, ...]
    errors: tuple[str, ...] = ()


def _sr_star(n: int, variance: float | None) -> float | None:
    """SR\\*_annual as the sweep report computes it: None below one trial or two pairs."""
    return expected_max_sharpe(n, variance) if n >= 1 and variance is not None else None


def _lab_decisions(conn: duckdb.DuckDBPyConnection) -> list[tuple[DecisionRow, str, int | None]]:
    """Every `promotion` and `sweep_retired` decision, oldest first, with the family
    of the hypothesis it names (directly or through its trial, as `_decisions` reads
    it) and the `sweep_id` its values name (None when absent)."""
    rows = conn.execute(
        "SELECT d.decision_id, d.made_at, d.kind, COALESCE(h.slug, th.slug), d.trial_id, "
        "d.values_json, d.reason, COALESCE(h.family, th.family), "
        "TRY_CAST(json_extract_string(d.values_json, '$.sweep_id') AS BIGINT) "
        "FROM owner_decisions d "
        "LEFT JOIN hypotheses h ON h.hypothesis_id = d.hypothesis_id "
        "LEFT JOIN trials t ON t.trial_id = d.trial_id "
        "LEFT JOIN hypotheses th ON th.hypothesis_id = t.hypothesis_id "
        "WHERE list_contains($kinds::VARCHAR[], d.kind) ORDER BY d.decision_id",
        {"kinds": list(_SWEEP_DECISION_KINDS)},
    ).fetchall()
    return [(DecisionRow(*row[:7]), row[7], row[8]) for row in rows]


def load_lab_view(
    conn: duckdb.DuckDBPyConnection, settings: Settings, *, code_vintage: str | None = None
) -> LabView:
    """The families cards and sweeps table (module docstring) through `conn`.
    `code_vintage` is the checkout's `registry.code_tree_sha256()` unless given (a
    test passes one). Raises `LabNotInitialised` on a store without the lab tables,
    and `RegistryError` when a family's V cannot be read; a sweep whose report fails
    (a counted trial lacking a metric) is left out and named in `errors`."""
    require_lab(conn)
    vintage = code_vintage if code_vintage is not None else registry.code_tree_sha256()
    status = sweep_report.lab_status(conn, settings, code_vintage=vintage)
    slugs = [
        str(slug)
        for (slug,) in conn.execute(
            "SELECT slug FROM sweeps GROUP BY slug ORDER BY MAX(sweep_id) DESC"
        ).fetchall()
    ]
    # One unreadable sweep (a counted trial missing a metric) loses its row only.
    reports: list[sweep_report.SweepReport] = []
    errors: list[str] = []
    for slug in slugs:
        try:
            reports.append(sweep_report.sweep_report(conn, slug, code_vintage=vintage))
        except (KeyError, ValueError, registry.RegistryError) as exc:
            errors.append(f"sweep {slug}: {exc}")
    decisions = _lab_decisions(conn)
    declared: dict[str, tuple[int, float | None]] = {}
    for report in reports:
        declared.setdefault(
            report.family, (report.n_at_declared_count, report.sr_star_annual_at_declared_count)
        )
    cards = []
    for family in status.families:
        rules = family.rules
        variance = family.sharpe_variance_annual_excess
        sr_star = _sr_star(family.n, variance)
        n_declared, sr_star_declared = declared.get(family.family, (family.n, sr_star))
        cards.append(
            FamilyCard(
                family=family.family,
                n=family.n,
                sharpe_variance_annual_raw=family.sharpe_variance_annual_raw,
                sharpe_variance_annual_excess=variance,
                sr_star_annual=sr_star,
                declared_count=family.declared_count,
                n_at_declared_count=n_declared,
                sr_star_annual_at_declared_count=sr_star_declared,
                rules=rules,
                parent_n=(
                    results.family_n(conn, rules.parent_family)
                    if rules is not None and rules.parent_family is not None
                    else None
                ),
                holdout_spends=tuple(registry.family_holdout_spends(conn, family.family)),
                max_holdout_spends=(
                    rules.max_family_holdout_spends
                    if rules is not None
                    else settings.lab.max_family_holdout_spends
                ),
                promotions=tuple(
                    d for d, f, _ in decisions if d.kind == "promotion" and f == family.family
                ),
                max_promotions=(
                    rules.max_family_promotions
                    if rules is not None
                    else settings.lab.max_family_promotions
                ),
                sr_star_high_water_annual=_high_water(conn, family.family, family.n, variance)
                if rules is not None
                else None,
            )
        )
    sweeps = tuple(
        SweepLine(r, tuple(d for d, _, sweep_id in decisions if sweep_id == r.sweep_id))
        for r in reports
    )
    return LabView(tuple(cards), sweeps, tuple(errors))


def _high_water(
    conn: duckdb.DuckDBPyConnection, family: str, n: int, variance: float | None
) -> float | None:
    """The family's SR\\* high-water mark, or None when it cannot be read (the card
    still shows the rest)."""
    try:
        return lab_registry.family_sr_star_high_water_mark(
            conn, family, n_trials_today=n, sharpe_variance_annual_today=variance
        )
    except registry.RegistryError:
        return None


def _num(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _rules_line(rules: FamilyRules | None) -> str:
    if rules is None:
        return "Family rules: none recorded (a Phase 3 family: the live caps apply)."
    fixed = ", ".join(f"{key}={value}" for key, value in sorted(rules.fixed_params.items()))
    return (
        f"Family rules: holdout {rules.holdout_start} to {rules.holdout_end}; in-sample from "
        f"{rules.in_sample_start}; V floor {rules.min_sharpe_variance_annual}; fixed {fixed}."
    )


def _spends_line(card: FamilyCard) -> str:
    listed = "; ".join(
        f"{s.slug} ({'research run' if s.source == 'research_run' else 'trial'} "
        f"{s.trial_id}, {s.status})"
        for s in card.holdout_spends
    )
    return (
        f"Holdout spends: {len(card.holdout_spends)} of {card.max_holdout_spends}"
        + (f": {listed}" if listed else "")
        + "."
    )


def _promotions_line(card: FamilyCard) -> str:
    listed = "; ".join(f"{d.slug} (decision {d.decision_id})" for d in card.promotions)
    return (
        f"Promotions: {len(card.promotions)} of {card.max_promotions}"
        + (f": {listed}" if listed else "")
        + "."
    )


def _render_card(card: FamilyCard) -> None:
    with st.container(border=True):
        st.markdown(f"**Family `{card.family}`**")
        tiles = st.columns(4)
        tiles[0].metric("N today", card.n)
        tiles[1].metric("V excess (annual)", _num(card.sharpe_variance_annual_excess, 4))
        tiles[2].metric("SR*_annual", _num(card.sr_star_annual))
        tiles[3].metric("SR* high-water mark", _num(card.sr_star_high_water_annual))
        parent = (
            f"parent family `{card.rules.parent_family}`, N {card.parent_n}"
            if card.rules is not None and card.rules.parent_family is not None
            else "parent family: none (root)"
        )
        st.caption(
            f"V raw (annual) {_num(card.sharpe_variance_annual_raw, 4)}; declared count "
            f"{card.declared_count}; SR* at declared count "
            f"{_num(card.sr_star_annual_at_declared_count)} (N {card.n_at_declared_count}); "
            f"{parent}."
        )
        st.caption(_rules_line(card.rules))
        st.caption(_spends_line(card))
        st.caption(_promotions_line(card))


def _state_text(report: sweep_report.SweepReport) -> str:
    if report.complete:
        return f"complete; {len(report.terminal_failed)} terminal-failed"
    return (
        f"{report.state}; {len(report.stale)} stale, {len(report.terminal_failed)} terminal-failed"
    )


def _quartiles_text(report: sweep_report.SweepReport) -> str:
    d = report.distribution
    if d is None:
        return f"{report.selection_statistic}: none counted"
    return f"{report.selection_statistic}: {_num(d.q1, 4)} / {_num(d.median, 4)} / {_num(d.q3, 4)}"


def _argmax_text(report: sweep_report.SweepReport) -> str:
    v = report.verdicts
    if v is None:
        return "-"
    promote = "met" if v.promote_at_least_met else "not met"
    retire = "met" if v.retire_below_met else "not met"
    return (
        f"{v.argmax.slug} (v{v.argmax.variant_index}); promote_at_least {promote}; "
        f"retire_below {retire}"
    )


def _decisions_text(decisions: tuple[DecisionRow, ...]) -> str:
    if not decisions:
        return "-"
    return "; ".join(
        f"promotion {d.decision_id}: {d.slug}"
        if d.kind == "promotion"
        else f"retired {d.decision_id}: {d.reason}"
        for d in decisions
    )


_DSR_SHARE_COLUMN: Final = f"dsr_excess > {sweep_report.DSR_SHARE_THRESHOLD}"

_SWEEPS_SCHEMA: Final[dict[str, pl.DataType]] = {
    "sweep": pl.Utf8(),
    "declared / run": pl.Utf8(),
    "state": pl.Utf8(),
    "q1 / median / q3": pl.Utf8(),
    _DSR_SHARE_COLUMN: pl.Float64(),
    "red flags": pl.Int64(),
    "argmax": pl.Utf8(),
    "promotion / retirement": pl.Utf8(),
}


def _sweeps_table(sweeps: tuple[SweepLine, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "sweep": f"{s.report.slug} (r{s.report.sweep_id})",
                "declared / run": f"{s.report.n_declared} / {s.report.n_run}",
                "state": _state_text(s.report),
                "q1 / median / q3": _quartiles_text(s.report),
                _DSR_SHARE_COLUMN: s.report.dsr_excess_share_above,
                "red flags": s.report.red_flag_count,
                "argmax": _argmax_text(s.report),
                "promotion / retirement": _decisions_text(s.decisions),
            }
            for s in sweeps
        ],
        schema=_SWEEPS_SCHEMA,
    )


def _render_lab(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    st.subheader("Families")
    try:
        view = load_lab_view(conn, settings)
    except LabNotInitialised as exc:
        st.info(f"Strategy lab not initialised: {exc}")
        return
    except (KeyError, ValueError, registry.RegistryError, duckdb.Error) as exc:
        # The trial table below still renders.
        st.error(f"Families and sweeps could not be read: {exc}")
        return
    for error in view.errors:
        st.error(f"Not shown: {error}")
    if not view.families:
        st.markdown("No families registered.")
    for card in view.families:
        _render_card(card)
    st.subheader("Sweeps")
    if view.sweeps:
        st.caption(
            "Latest registration of each sweep. The argmax and its verdicts appear only "
            "for a complete sweep; dsr_excess is recomputed with today's N and V."
        )
        st.dataframe(_sweeps_table(view.sweeps), hide_index=True)
    else:
        st.markdown("No sweeps registered.")


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the trial-registry view from the shell's read-only connection;
    `settings` defaults to `get_settings()` (the live `lab.*` caps)."""
    st.header("Trial registry")
    header.render_freshness(header.store_freshness(conn, header.now()))
    try:
        schema.init_schema(conn)
    except schema.RegistryNotInitialised as exc:
        st.info(f"Trial registry not initialised: {exc}")
        return
    except schema.SchemaVersionError as exc:
        st.error(str(exc))
        return

    _render_lab(conn, settings if settings is not None else get_settings())

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

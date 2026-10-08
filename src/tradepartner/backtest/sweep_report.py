"""The sweep report and `lab status` (strategy-lab spec req 3 and the `lab status`
criterion; plan task T108).

`sweep_report(conn, slug)` reads a sweep's latest registration and its family in a
number of reads that grows with the variants, never with the trials: the `sweeps`
row and the family rules (`lab_registry`), every variant of every sweep in the
family (one `sweep_variants` read), their registrations (one `hypotheses` read),
their in-sample trials with results (one `trials` read), the variant states
(`lab_queries.variant_states`, a few reads per variant), the counted trials'
base-level metrics (one `trial_metrics` read), **one** `registry.family_sharpes`
call (itself one `trial_metrics` query per basis) and **one** `results.family_n`
call (plus the parent family's N when the rules name a parent). Everything after
the reads is pure over those rows.

**Variant states** (spec, Definitions "Selection statistic", "Complete sweep"; req 2;
#1221). There is one classifier: `store.lab_queries.variant_states`, which the sweep
runner's plan (T107) and `sweep_state` read too, so the report can never call a
sweep complete that the runner would rerun, or the reverse. Its counted trial is the
latest current `ok`, non-synthetic, in-sample trial over the variant's default window
(from the sweep registration's copied window); terminal failure counts only this
registration's runs (`sweep_trials`) since its rerun epoch, over that window, clean,
at the current code vintage, excluding `store changed during run` and `shared read
failed`. The report maps its states one to one (`current` reads `counted`) and adds
one display state: an `unrun` variant with failed trials reads `failed`. The report's
own trials read (one query) supplies only the shown trial's `red_flag` and that
display state. The classifier's reads are per variant, never per trial.

**Recomputed, never read back.** Each variant's `dsr_excess` is
`metrics.deflated_sharpe` on its counted trial's base-level metrics with today's N
(`results.family_n`) and today's excess-basis pair Sharpes (`family_sharpes`), never
the value stored at the trial's own N. "SR\\* at declared count" is
`expected_max_sharpe(N + the declared fingerprints with no counted trial, V)`. The
argmax (ties by canonical index) and the `promote_at_least` / `retire_below` verdicts
exist **only for a complete sweep**; `promote_at_least` is read as req 4 does, against
the argmax's `dsr_excess` computed with the family's SR\\* high-water mark
(`lab_registry.family_sr_star_high_water_mark`), and `retire_below` is met when the
argmax's selection statistic is below it. The report holds and prints no holdout
value: no variant has one, and the window dates are not part of it.

`lab_status(conn, settings)` lists the store size, every registry table's row count,
each family's N, V per basis, declared count and rules, the grandfathered pairs and
members, every open (incomplete) sweep with its completion and stale counts, the last
ten `sweep_runs` with seconds per variant, and a warning when `lab.quiet_timezone`
is not the system time zone. Both raise `LabNotInitialised` on a store without the
lab tables. Reads only: nothing here writes, deletes, drops, truncates or vacuums.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any, Final, Literal

import duckdb
import numpy as np

from tradepartner.backtest import results
from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
)
from tradepartner.backtest.quiet import system_timezone_matches
from tradepartner.config import Settings
from tradepartner.store import lab_queries, lab_registry, registry
from tradepartner.store.db import utc_now
from tradepartner.store.lab_registry import (
    FamilyRules,
    GrandfatheredMember,
    SweepRecord,
    SweepVariant,
)
from tradepartner.store.lab_schema import require_lab
from tradepartner.store.registry import (
    BASE_COST_KEY,
    STORE_CHANGED_MESSAGE,
    HypothesisRecord,
)
from tradepartner.store.schema import REGISTRY_TABLE_NAMES

__all__ = [
    "DSR_SHARE_THRESHOLD",
    "EXCLUDED_FAILURE_MESSAGES",
    "LabStatus",
    "SweepReport",
    "VariantRow",
    "format_lab_status",
    "format_report",
    "lab_status",
    "sweep_report",
]

VariantStatus = Literal["counted", "terminal_failed", "stale", "failed", "unrun"]
SweepState = Literal["complete", "incomplete (stale)", "incomplete (unrun)"]

CADENCE_KEY: Final = "schedule.rebalance_cadence"
ANCHOR_KEY: Final = "schedule.signal_anchor"
FILL_KEY: Final = "execution.fill_price"

#: The `engine.run_many` shared-read failure message (strategy-lab spec req 2), as
#: the one classifier (`lab_queries`) excludes it.
SHARED_READ_FAILED: Final = lab_queries.SHARED_READ_FAILED

#: Failure messages that never make a variant terminal-failed (spec req 2): both are
#: failures of the group or the store, not of the variant.
EXCLUDED_FAILURE_MESSAGES: Final = frozenset({STORE_CHANGED_MESSAGE, SHARED_READ_FAILED})

#: The report's "share of variants with recomputed `dsr_excess` > 0.5" (spec req 3):
#: a fixed reading of the spec's own text, "more likely skilled than not", not a gate.
DSR_SHARE_THRESHOLD: Final = 0.5

#: How many `sweep_runs` rows `lab status` lists, newest first (plan T108).
LAST_SWEEP_RUNS: Final = 10

#: The base-level `strategy` metrics the report reads per counted trial: the six it
#: prints and the inputs the recomputed excess-basis DSR needs (spec req 9).
_REPORT_METRICS: Final = (
    "excess_cagr_spy",
    "sharpe_annual_excess_spy",
    "cost_drag",
    "turnover_annual",
    "max_drawdown",
    "sharpe_period_excess_spy",
    "skew_period_excess_spy",
    "kurtosis_period_excess_spy",
    "n_periods",
    "periods_per_year",
)

_PERCENT_POINTS: Final = 100.0


# --- rows ------------------------------------------------------------------------


@dataclass(frozen=True)
class _TrialRow:
    """One `in_sample`, non-synthetic trial of a variant with an `ok` or `failed`
    result: what the variant states read."""

    trial_id: int
    hypothesis_id: int
    start_session: date
    end_session: date
    data_cutoff: datetime | None
    data_vintage: datetime | None
    code_tree_sha256: str | None
    code_dirty: bool | None
    status: str
    message: str | None
    red_flag: bool | None


@dataclass(frozen=True)
class _VariantState:
    """A variant's state and the trial its row shows: the counted trial, or the latest
    `ok` one when it is stale; `message` is the terminal failure's."""

    variant: SweepVariant
    record: HypothesisRecord
    status: VariantStatus
    trial: _TrialRow | None
    message: str | None


@dataclass(frozen=True)
class VariantRow:
    """One report row, in canonical order (spec req 3). The metric fields are the
    base-level values of the counted trial (or, for a stale variant, its latest `ok`
    trial), None when the variant has no such trial; `dsr_excess` is recomputed with
    today's N and V."""

    variant_index: int
    slug: str
    varied: dict[str, Any]
    cadence: str
    signal_anchor: str
    status: VariantStatus
    trial_id: int | None
    excess_cagr_spy: float | None
    sharpe_annual_excess_spy: float | None
    dsr_excess: float | None
    cost_drag: float | None
    turnover_annual: float | None
    max_drawdown: float | None
    red_flag: bool | None
    message: str | None


@dataclass(frozen=True)
class Distribution:
    """Minimum, quartiles, median and maximum of the selection statistic over the
    counted variants (linear interpolation between order statistics)."""

    minimum: float
    q1: float
    median: float
    q3: float
    maximum: float


@dataclass(frozen=True)
class Verdicts:
    """The complete sweep's argmax and its two pre-registered verdicts (spec reqs 3, 4).
    `dsr_excess_at_high_water` is the argmax's excess-basis DSR against the family's
    SR\\* high-water mark, the value `promote_at_least` is read against."""

    argmax: VariantRow
    sr_star_high_water_annual: float
    dsr_excess_at_high_water: float
    promote_at_least_met: bool
    retire_below_met: bool


@dataclass(frozen=True)
class SweepReport:
    """`sweep report <slug>` (spec req 3) for the sweep's latest registration."""

    slug: str
    sweep_id: int
    family: str
    title: str
    fill_price: str | None
    selection_statistic: str
    expected_range_pp: tuple[float, float]
    promote_at_least: float
    retire_below: float
    n_declared: int
    n_run: int
    n_counted: int
    terminal_failed: tuple[str, ...]
    stale: tuple[str, ...]
    state: SweepState
    family_n: int
    sharpe_variance_annual_excess: float | None
    sharpe_variance_annual_raw: float | None
    sr_star_annual: float | None
    declared_count: int
    n_at_declared_count: int
    sr_star_annual_at_declared_count: float | None
    parent_family: str | None
    parent_n: int | None
    rows: tuple[VariantRow, ...]
    distribution: Distribution | None
    dsr_excess_share_above: float | None
    expected_range_share: float | None
    red_flag_count: int
    verdicts: Verdicts | None

    @property
    def complete(self) -> bool:
        """Whether every variant is counted or terminal-failed."""
        return self.state == "complete"


@dataclass(frozen=True)
class FamilyStatus:
    """One family's line in `lab status`."""

    family: str
    n: int
    sharpe_variance_annual_raw: float | None
    sharpe_variance_annual_excess: float | None
    declared_count: int
    rules: FamilyRules | None


@dataclass(frozen=True)
class OpenSweep:
    """An incomplete sweep (latest registration of its slug) in `lab status`."""

    slug: str
    sweep_id: int
    family: str
    n_declared: int
    n_counted: int
    n_terminal_failed: int
    n_stale: int
    state: SweepState


@dataclass(frozen=True)
class SweepRunLine:
    """One `sweep_runs` row in `lab status`; `seconds_per_variant` is the run's seconds
    over its `ok` and failed trials, None for an open run or one that ran none."""

    sweep_run_id: int
    slug: str
    started_at: datetime
    finished_at: datetime | None
    n_planned: int
    n_ok: int | None
    n_failed: int | None
    seconds: float | None
    seconds_per_variant: float | None
    completed: bool | None


@dataclass(frozen=True)
class LabStatus:
    """`lab status` (spec req 15 and the `lab status` criterion)."""

    store_size_bytes: int
    table_rows: dict[str, int]
    families: tuple[FamilyStatus, ...]
    grandfathered_pairs: dict[str, tuple[int, ...]]
    grandfathered_members: tuple[GrandfatheredMember, ...]
    open_sweeps: tuple[OpenSweep, ...]
    last_runs: tuple[SweepRunLine, ...]
    timezone_warning: str | None


# --- reads -----------------------------------------------------------------------


def _family_variants(conn: duckdb.DuckDBPyConnection, family: str) -> list[SweepVariant]:
    """Every variant of every sweep registration in `family`, by sweep then index."""
    rows = conn.execute(
        "SELECT v.sweep_id, v.variant_index, v.hypothesis_id, v.fingerprint, "
        "v.variant_params_json FROM sweep_variants v JOIN sweeps s USING (sweep_id) "
        "WHERE s.family = ? ORDER BY v.sweep_id, v.variant_index",
        [family],
    ).fetchall()
    return [
        SweepVariant(sweep_id, index, hypothesis_id, fingerprint, json.loads(params))
        for sweep_id, index, hypothesis_id, fingerprint, params in rows
    ]


_RECORD_COLUMNS: Final = (
    "hypothesis_id",
    "slug",
    "family",
    "title",
    "doc_path",
    "doc_sha256",
    "params_json",
    "params_sha256",
    "in_sample_start",
    "holdout_start",
    "holdout_end",
    "registered_at",
    "registered_by",
)


def _records(conn: duckdb.DuckDBPyConnection, ids: Iterable[int]) -> dict[int, HypothesisRecord]:
    """The registrations with `ids`, in one read."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = conn.execute(
        f"SELECT {', '.join(_RECORD_COLUMNS)} FROM hypotheses "
        "WHERE list_contains($ids::BIGINT[], hypothesis_id)",
        {"ids": wanted},
    ).fetchall()
    found: dict[int, HypothesisRecord] = {}
    for row in rows:
        values = dict(zip(_RECORD_COLUMNS, row, strict=True))
        params = json.loads(values.pop("params_json"))
        record = HypothesisRecord(params=params, **values)
        found[record.hypothesis_id] = record
    return found


def _trials(conn: duckdb.DuckDBPyConnection, ids: Iterable[int]) -> dict[int, list[_TrialRow]]:
    """The `in_sample`, non-synthetic `ok` and `failed` trials of `ids`, oldest first
    per hypothesis, in one read."""
    wanted = sorted(set(ids))
    by_hypothesis: dict[int, list[_TrialRow]] = {i: [] for i in wanted}
    if not wanted:
        return by_hypothesis
    rows = conn.execute(
        "SELECT t.trial_id, t.hypothesis_id, t.start_session, t.end_session, t.data_cutoff, "
        "t.data_vintage, t.code_tree_sha256, t.code_dirty, r.status, r.message, r.red_flag "
        "FROM trials t JOIN trial_results r USING (trial_id) "
        "WHERE list_contains($ids::BIGINT[], t.hypothesis_id) AND t.kind = 'in_sample' "
        "AND NOT t.synthetic AND r.status IN ('ok', 'failed') ORDER BY t.trial_id",
        {"ids": wanted},
    ).fetchall()
    for row in rows:
        trial = _TrialRow(*row)
        by_hypothesis[trial.hypothesis_id].append(trial)
    return by_hypothesis


def _metrics(
    conn: duckdb.DuckDBPyConnection, trials: Sequence[tuple[int, float]]
) -> dict[int, dict[str, float | None]]:
    """The base-level `strategy` `_REPORT_METRICS` of each (trial id, base level), in
    one `trial_metrics` read."""
    found: dict[int, dict[str, float | None]] = {trial_id: {} for trial_id, _ in trials}
    if not trials:
        return found
    rows = conn.execute(
        "SELECT c.trial_id, m.metric, m.value "
        "FROM (SELECT UNNEST($ids::BIGINT[]) AS trial_id, UNNEST($bases::DOUBLE[]) AS base) c "
        "JOIN trial_metrics m ON m.trial_id = c.trial_id AND m.series = 'strategy' "
        "AND m.cost_per_side_bps = c.base AND list_contains($metrics::VARCHAR[], m.metric)",
        {
            "ids": [trial_id for trial_id, _ in trials],
            "bases": [base for _, base in trials],
            "metrics": list(_REPORT_METRICS),
        },
    ).fetchall()
    for trial_id, metric, value in rows:
        found[trial_id][metric] = None if value is None else float(value)
    return found


# --- variant states (pure) ---------------------------------------------------------


#: The report's status for each `lab_queries` variant state; a variant that is
#: `unrun` there but has failed trials reads `failed` here.
_STATUS: Final[dict[str, VariantStatus]] = {
    "current": "counted",
    "terminal_failed": "terminal_failed",
    "stale": "stale",
    "unrun": "unrun",
}


def _state(
    classified: lab_queries.VariantState,
    record: HypothesisRecord,
    trials: Sequence[_TrialRow],
) -> _VariantState:
    """One variant's report state from the one classifier
    (`lab_queries.variant_states`, module docstring "Variant states"), with the
    report's own trial row (its `red_flag`) for the trial it shows."""
    by_id = {t.trial_id: t for t in trials}
    shown = by_id.get(classified.trial.trial_id) if classified.trial is not None else None
    status = _STATUS[classified.state]
    if status == "unrun" and any(t.status == "failed" for t in trials):
        status = "failed"
    return _VariantState(classified.variant, record, status, shown, classified.terminal_message)


def _sweep_state(states: Sequence[_VariantState]) -> SweepState:
    if all(s.status in ("counted", "terminal_failed") for s in states):
        return "complete"
    if any(s.status == "stale" for s in states):
        return "incomplete (stale)"
    return "incomplete (unrun)"


@dataclass(frozen=True)
class _FamilyStates:
    """Every variant state of a family, by sweep id, and its declared fingerprints."""

    by_sweep: dict[int, list[_VariantState]]
    declared: frozenset[str]

    def uncounted(self) -> int:
        """Declared fingerprints with no counted trial in any sweep (Definitions,
        Declared count)."""
        counted = {
            s.variant.fingerprint
            for states in self.by_sweep.values()
            for s in states
            if s.status == "counted"
        }
        return len(self.declared - counted)


def _family_states(
    conn: duckdb.DuckDBPyConnection, family: str, code_vintage: str | None
) -> _FamilyStates:
    """Read and classify every variant of `family`, sweep by sweep, through
    `lab_queries.variant_states` (the one classifier)."""
    variants = _family_variants(conn, family)
    ids = [v.hypothesis_id for v in variants]
    records = _records(conn, ids)
    trials = _trials(conn, ids)
    by_sweep: dict[int, list[_VariantState]] = {}
    for sweep_id in sorted({v.sweep_id for v in variants}):
        by_sweep[sweep_id] = [
            _state(c, records[c.variant.hypothesis_id], trials[c.variant.hypothesis_id])
            for c in lab_queries.variant_states(conn, sweep_id, code_vintage=code_vintage)
        ]
    return _FamilyStates(by_sweep, frozenset(v.fingerprint for v in variants))


# --- the report ----------------------------------------------------------------------


def _number(metrics: Mapping[str, float | None], key: str, trial_id: int) -> float:
    value = metrics.get(key)
    if value is None or not math.isfinite(value):
        raise ValueError(f"trial {trial_id} has no finite base-level {key} for strategy")
    return value


def _row(
    state: _VariantState,
    metrics: Mapping[str, float | None] | None,
    *,
    family_n: int,
    pair_sharpes: Sequence[float],
) -> VariantRow:
    values = frozen_values(state.record)
    trial = state.trial
    dsr: float | None = None
    if trial is not None and metrics is not None:
        ppy = int(_number(metrics, "periods_per_year", trial.trial_id))
        dsr = deflated_sharpe(
            metrics,
            "excess_spy",
            n_trials=family_n,
            pair_sharpes=pair_sharpes,
            periods_per_year=ppy,
        ).dsr
    shown = metrics if metrics is not None else {}
    return VariantRow(
        variant_index=state.variant.variant_index,
        slug=state.record.slug,
        varied=dict(state.variant.variant_params),
        cadence=str(values[CADENCE_KEY]),
        signal_anchor=str(values[ANCHOR_KEY]),
        status=state.status,
        trial_id=trial.trial_id if trial is not None else None,
        excess_cagr_spy=shown.get("excess_cagr_spy"),
        sharpe_annual_excess_spy=shown.get("sharpe_annual_excess_spy"),
        dsr_excess=dsr,
        cost_drag=shown.get("cost_drag"),
        turnover_annual=shown.get("turnover_annual"),
        max_drawdown=shown.get("max_drawdown"),
        red_flag=trial.red_flag if trial is not None else None,
        message=state.message,
    )


def _selection(row: VariantRow, statistic: str) -> float:
    value = {
        "dsr_excess": row.dsr_excess,
        "sharpe_annual_excess_spy": row.sharpe_annual_excess_spy,
        "excess_cagr_spy": row.excess_cagr_spy,
    }[statistic]
    if value is None or not math.isfinite(value):
        raise ValueError(f"variant {row.slug} has no finite {statistic}")
    return value


def _distribution(values: Sequence[float]) -> Distribution | None:
    if not values:
        return None
    q = np.percentile(np.asarray(values, dtype=np.float64), [0, 25, 50, 75, 100])
    return Distribution(*(float(x) for x in q))


def _argmax(rows: Sequence[VariantRow], statistic: str) -> VariantRow | None:
    """The counted row with the largest statistic; ties to the lowest canonical index."""
    best: VariantRow | None = None
    for row in sorted(rows, key=lambda r: r.variant_index):
        if best is None or _selection(row, statistic) > _selection(best, statistic):
            best = row
    return best


def _verdicts(
    conn: duckdb.DuckDBPyConnection,
    sweep: SweepRecord,
    argmax: VariantRow,
    metrics: Mapping[str, float | None],
    *,
    family_n: int,
    variance: float | None,
) -> Verdicts:
    """Req 4's reading of `promote_at_least` (the argmax's excess DSR against the
    family's SR\\* high-water mark) and `retire_below` (its statistic below it)."""
    assert argmax.trial_id is not None
    trial_id = argmax.trial_id
    mark = lab_registry.family_sr_star_high_water_mark(
        conn, sweep.family, n_trials_today=family_n, sharpe_variance_annual_today=variance
    )
    ppy = _number(metrics, "periods_per_year", trial_id)
    dsr = probabilistic_sharpe(
        _number(metrics, "sharpe_period_excess_spy", trial_id),
        mark / math.sqrt(ppy),
        int(_number(metrics, "n_periods", trial_id)),
        _number(metrics, "skew_period_excess_spy", trial_id),
        _number(metrics, "kurtosis_period_excess_spy", trial_id),
    )
    return Verdicts(
        argmax=argmax,
        sr_star_high_water_annual=mark,
        dsr_excess_at_high_water=dsr,
        promote_at_least_met=dsr >= sweep.promote_at_least,
        retire_below_met=_selection(argmax, sweep.selection_statistic) < sweep.retire_below,
    )


def _sr_star(n: int, variance: float | None) -> float | None:
    return expected_max_sharpe(n, variance) if n >= 1 and variance is not None else None


def sweep_report(
    conn: duckdb.DuckDBPyConnection, slug: str, *, code_vintage: str | None = None
) -> SweepReport:
    """The report of `slug`'s latest registration (module docstring). `code_vintage`
    is the checkout's `registry.code_tree_sha256()` unless given (a test passes one).
    Raises `LabNotInitialised` on a store without the lab tables and `ValueError` for
    an unknown slug or a counted trial missing a base-level metric the report needs."""
    require_lab(conn)
    sweep = lab_registry.sweep_by_slug(conn, slug)
    if sweep is None:
        raise ValueError(f"no sweep is registered as {slug!r}")
    rules = lab_registry.family_rules(conn, sweep.family)
    vintage = code_vintage if code_vintage is not None else registry.code_tree_sha256()
    family = _family_states(conn, sweep.family, vintage)
    states = family.by_sweep.get(sweep.sweep_id, [])
    shown = [
        (s.trial.trial_id, float(frozen_values(s.record)[BASE_COST_KEY]))
        for s in states
        if s.trial is not None
    ]
    metrics = _metrics(conn, shown)
    sharpes = registry.family_sharpes(conn, sweep.family)
    family_n = results.family_n(conn, sweep.family)
    rows = tuple(
        _row(
            s,
            metrics[s.trial.trial_id] if s.trial is not None else None,
            family_n=family_n,
            pair_sharpes=sharpes.excess_spy,
        )
        for s in states
    )
    counted = [r for r in rows if r.status == "counted"]
    statistic = sweep.selection_statistic
    variance = sharpes.variance("excess_spy")
    state = _sweep_state(states)
    n_at_declared = family_n + family.uncounted()
    lo, hi = sweep.expected_range_lo_pp, sweep.expected_range_hi_pp
    verdicts = None
    if state == "complete":
        best = _argmax(counted, statistic)
        if best is not None and best.trial_id is not None:
            verdicts = _verdicts(
                conn,
                sweep,
                best,
                metrics[best.trial_id],
                family_n=family_n,
                variance=variance,
            )
    parent = rules.parent_family if rules is not None else None
    return SweepReport(
        slug=sweep.slug,
        sweep_id=sweep.sweep_id,
        family=sweep.family,
        title=sweep.title,
        fill_price=_fill_price(rules, states),
        selection_statistic=statistic,
        expected_range_pp=(lo, hi),
        promote_at_least=sweep.promote_at_least,
        retire_below=sweep.retire_below,
        n_declared=len(states),
        n_run=sum(1 for s in states if s.status != "unrun"),
        n_counted=len(counted),
        terminal_failed=tuple(s.record.slug for s in states if s.status == "terminal_failed"),
        stale=tuple(s.record.slug for s in states if s.status == "stale"),
        state=state,
        family_n=family_n,
        sharpe_variance_annual_excess=variance,
        sharpe_variance_annual_raw=sharpes.variance("raw"),
        sr_star_annual=_sr_star(family_n, variance),
        declared_count=len(family.declared),
        n_at_declared_count=n_at_declared,
        sr_star_annual_at_declared_count=_sr_star(n_at_declared, variance),
        parent_family=parent,
        parent_n=results.family_n(conn, parent) if parent is not None else None,
        rows=rows,
        distribution=_distribution([_selection(r, statistic) for r in counted]),
        dsr_excess_share_above=_share(
            counted, lambda r: r.dsr_excess is not None and r.dsr_excess > DSR_SHARE_THRESHOLD
        ),
        expected_range_share=_share(
            counted,
            lambda r: (
                r.excess_cagr_spy is not None
                and lo / _PERCENT_POINTS <= r.excess_cagr_spy <= hi / _PERCENT_POINTS
            ),
        ),
        red_flag_count=sum(1 for r in counted if r.red_flag),
        verdicts=verdicts,
    )


def _share(rows: Sequence[VariantRow], test: Callable[[VariantRow], bool]) -> float | None:
    return sum(1 for r in rows if test(r)) / len(rows) if rows else None


def _fill_price(rules: FamilyRules | None, states: Sequence[_VariantState]) -> str | None:
    """The family's fill convention: its rules' frozen value, else the variants'."""
    if rules is not None and FILL_KEY in rules.fixed_params:
        return str(rules.fixed_params[FILL_KEY])
    for state in states:
        value = frozen_values(state.record).get(FILL_KEY)
        if value is not None:
            return str(value)
    return None


# --- text --------------------------------------------------------------------------


def _fmt(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * _PERCENT_POINTS:.2f}%"


def _varied(values: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={json.dumps(value)}" for key, value in sorted(values.items()))


def format_report(report: SweepReport) -> str:
    """The report as text (spec req 3): header, counts, family numbers, one row per
    variant in canonical order, the distribution and shares, and the argmax and
    verdicts only for a complete sweep. Prints no holdout value."""
    lines = [
        f"sweep {report.slug} (registration {report.sweep_id}): {report.title}",
        f"family {report.family}; fill {report.fill_price or '-'}; "
        f"selection statistic {report.selection_statistic} at the base cost level",
        f"variants: {report.n_declared} declared, {report.n_run} run, "
        f"{report.n_counted} counted; state: {report.state}",
        f"terminal-failed: {', '.join(report.terminal_failed) or 'none'}",
        f"stale (awaiting a rerun): {', '.join(report.stale) or 'none'}",
        f"family N {report.family_n}; V (annual, excess) "
        f"{_fmt(report.sharpe_variance_annual_excess)}; V (annual, raw) "
        f"{_fmt(report.sharpe_variance_annual_raw)}; SR*_annual {_fmt(report.sr_star_annual)}",
        f"declared count n {report.declared_count}; N at declared count "
        f"{report.n_at_declared_count}; SR* at declared count "
        f"{_fmt(report.sr_star_annual_at_declared_count)}",
    ]
    if report.parent_family is not None:
        lines.append(f"parent family {report.parent_family}: N {report.parent_n}")
    lines.append(
        "idx  slug  varied  cadence  anchor  status  excess_cagr_spy  "
        "sharpe_annual_excess_spy  dsr_excess  cost_drag  turnover_annual  "
        "max_drawdown  red_flag"
    )
    for row in report.rows:
        lines.append(
            f"{row.variant_index}  {row.slug}  {_varied(row.varied)}  {row.cadence}  "
            f"{row.signal_anchor}  {row.status}"
            + (f" ({row.message})" if row.message else "")
            + f"  {_pct(row.excess_cagr_spy)}  {_fmt(row.sharpe_annual_excess_spy)}  "
            f"{_fmt(row.dsr_excess)}  {_pct(row.cost_drag)}  {_fmt(row.turnover_annual)}  "
            f"{_pct(row.max_drawdown)}  {'-' if row.red_flag is None else row.red_flag}"
        )
    d = report.distribution
    lines.append(
        f"{report.selection_statistic} over counted variants: "
        + (
            "none"
            if d is None
            else f"min {_fmt(d.minimum)}, q1 {_fmt(d.q1)}, median {_fmt(d.median)}, "
            f"q3 {_fmt(d.q3)}, max {_fmt(d.maximum)}"
        )
    )
    lines.append(
        f"share with dsr_excess > {DSR_SHARE_THRESHOLD}: {_fmt(report.dsr_excess_share_above, 2)}"
        f"; share with excess_cagr_spy inside expected_range_pp "
        f"[{report.expected_range_pp[0]}, {report.expected_range_pp[1]}]: "
        f"{_fmt(report.expected_range_share, 2)}; red flags: {report.red_flag_count}"
    )
    v = report.verdicts
    if v is not None:
        best = _selection(v.argmax, report.selection_statistic)
        lines += [
            f"argmax: {v.argmax.slug} (index {v.argmax.variant_index}, "
            f"{report.selection_statistic} {_fmt(best)})",
            f"promote_at_least {report.promote_at_least}: "
            f"{'met' if v.promote_at_least_met else 'not met'} (dsr_excess "
            f"{_fmt(v.dsr_excess_at_high_water)} at the SR* high-water mark "
            f"{_fmt(v.sr_star_high_water_annual)})",
            f"retire_below {report.retire_below}: {'met' if v.retire_below_met else 'not met'}",
        ]
    return "\n".join(lines)


# --- lab status ------------------------------------------------------------------------


def _table_rows(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    union = " UNION ALL ".join(
        f"SELECT '{table}' AS t, COUNT(*) AS n FROM {table}" for table in REGISTRY_TABLE_NAMES
    )
    counts = dict(conn.execute(union).fetchall())
    return {table: int(counts[table]) for table in REGISTRY_TABLE_NAMES}


def _last_runs(conn: duckdb.DuckDBPyConnection) -> tuple[SweepRunLine, ...]:
    # Seconds per variant as `lab_queries.seconds_per_variant_by_cadence` measures
    # them: the mean of the run's `ok` trials' `sweep_trials.seconds`.
    rows = conn.execute(
        "SELECT r.sweep_run_id, s.slug, r.started_at, r.finished_at, r.n_planned, r.n_ok, "
        "r.n_failed, r.seconds, r.completed, "
        "(SELECT AVG(st.seconds) FROM sweep_trials st JOIN trial_results tr USING (trial_id) "
        "WHERE st.sweep_run_id = r.sweep_run_id AND tr.status = 'ok') "
        "FROM sweep_runs r JOIN sweeps s USING (sweep_id) "
        "ORDER BY r.sweep_run_id DESC LIMIT ?",
        [LAST_SWEEP_RUNS],
    ).fetchall()
    lines = []
    for run_id, slug, started, finished, planned, ok, failed, seconds, completed, mean in rows:
        per = float(mean) if mean is not None else None
        lines.append(
            SweepRunLine(
                run_id, slug, started, finished, planned, ok, failed, seconds, per, completed
            )
        )
    return tuple(lines)


def lab_status(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    now: datetime | None = None,
    system_tz: tzinfo | None = None,
    code_vintage: str | None = None,
) -> LabStatus:
    """`lab status` (module docstring). `now`, `system_tz` and `code_vintage` stand in
    for the clock, the machine's zone and the checkout's code vintage (a test passes
    them). Raises `LabNotInitialised` on a store without the lab tables."""
    require_lab(conn)
    vintage = code_vintage if code_vintage is not None else registry.code_tree_sha256()
    families = [
        str(f)
        for (f,) in conn.execute(
            "SELECT DISTINCT family FROM hypotheses UNION SELECT family FROM family_rules "
            "ORDER BY 1"
        ).fetchall()
    ]
    statuses: list[FamilyStatus] = []
    open_sweeps: list[OpenSweep] = []
    latest = {
        int(sweep_id): str(slug)
        for sweep_id, slug in conn.execute(
            "SELECT MAX(sweep_id), slug FROM sweeps GROUP BY slug"
        ).fetchall()
    }
    for family in families:
        sharpes = registry.family_sharpes(conn, family)
        states = _family_states(conn, family, vintage)
        statuses.append(
            FamilyStatus(
                family=family,
                n=results.family_n(conn, family),
                sharpe_variance_annual_raw=sharpes.variance("raw"),
                sharpe_variance_annual_excess=sharpes.variance("excess_spy"),
                declared_count=len(states.declared),
                rules=lab_registry.family_rules(conn, family),
            )
        )
        for sweep_id, variant_states in sorted(states.by_sweep.items()):
            state = _sweep_state(variant_states)
            if sweep_id not in latest or state == "complete":
                continue
            open_sweeps.append(
                OpenSweep(
                    slug=latest[sweep_id],
                    sweep_id=sweep_id,
                    family=family,
                    n_declared=len(variant_states),
                    n_counted=sum(1 for s in variant_states if s.status == "counted"),
                    n_terminal_failed=sum(
                        1 for s in variant_states if s.status == "terminal_failed"
                    ),
                    n_stale=sum(1 for s in variant_states if s.status == "stale"),
                    state=state,
                )
            )
    zone = settings.lab.quiet_timezone
    matches = system_timezone_matches(
        settings, around=now if now is not None else utc_now(), system_tz=system_tz
    )
    return LabStatus(
        store_size_bytes=lab_registry.registry_size_bytes(conn),
        table_rows=_table_rows(conn),
        families=tuple(statuses),
        grandfathered_pairs=lab_registry.grandfathered_fingerprints(conn),
        grandfathered_members=tuple(lab_registry.grandfathered_members(conn)),
        open_sweeps=tuple(open_sweeps),
        last_runs=_last_runs(conn),
        timezone_warning=None
        if matches
        else (
            f"lab.quiet_timezone {zone} is not the system time zone: launchd fires the "
            "plists in the system zone, so the quiet intervals are off"
        ),
    )


def _rules_text(rules: FamilyRules) -> str:
    return (
        f"rules: parent {rules.parent_family or 'none (root)'}; in_sample_start "
        f"{rules.in_sample_start}; holdout {rules.holdout_start}..{rules.holdout_end}; "
        f"fixed {json.dumps(rules.fixed_params, sort_keys=True)}; max holdout spends "
        f"{rules.max_family_holdout_spends}; max promotions {rules.max_family_promotions}; "
        f"V floor {rules.min_sharpe_variance_annual}; lattice "
        f"{json.dumps(rules.axis_lattice, sort_keys=True)}"
    )


def format_lab_status(status: LabStatus) -> str:
    """`lab status` as text."""
    lines = [f"store size: {status.store_size_bytes / 1e9:.3f} GB"]
    lines += [f"  {table}: {n} rows" for table, n in status.table_rows.items()]
    lines.append("families:")
    for f in status.families:
        lines.append(
            f"  {f.family}: N {f.n}; V raw {_fmt(f.sharpe_variance_annual_raw)}; V excess "
            f"{_fmt(f.sharpe_variance_annual_excess)}; declared count {f.declared_count}"
        )
        lines.append("    " + (_rules_text(f.rules) if f.rules is not None else "rules: none"))
    lines.append("grandfathered pairs:")
    lines += [
        f"  {fp[:12]}: hypotheses {', '.join(map(str, ids))}"
        for fp, ids in status.grandfathered_pairs.items()
    ] or ["  none"]
    lines.append("grandfathered members:")
    lines += [
        f"  {m.slug} ({m.family}, id {m.hypothesis_id}): {', '.join(m.differences)}"
        for m in status.grandfathered_members
    ] or ["  none"]
    lines.append("open sweeps:")
    lines += [
        f"  {s.slug} (registration {s.sweep_id}, {s.family}): {s.n_counted} of "
        f"{s.n_declared} counted, {s.n_terminal_failed} terminal-failed, {s.n_stale} stale; "
        f"{s.state}"
        for s in status.open_sweeps
    ] or ["  none"]
    lines.append(f"last {LAST_SWEEP_RUNS} sweep runs:")
    lines += [
        f"  run {r.sweep_run_id} {r.slug} started {r.started_at.isoformat()}: planned "
        f"{r.n_planned}, ok {r.n_ok if r.n_ok is not None else '-'}, failed "
        f"{r.n_failed if r.n_failed is not None else '-'}, seconds {_fmt(r.seconds, 1)}, "
        f"seconds per variant {_fmt(r.seconds_per_variant, 1)}, completed "
        f"{'-' if r.completed is None else r.completed}"
        for r in status.last_runs
    ] or ["  none"]
    if status.timezone_warning is not None:
        lines.append(f"warning: {status.timezone_warning}")
    return "\n".join(lines)

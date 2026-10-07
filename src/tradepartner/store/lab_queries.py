"""Run-planning reads for the strategy lab (strategy-lab plan T103b; spec req 2).

**Reads only.** This module writes no row, holds no state and owns no table: it
answers, from the lab tables `lab_schema.apply_lab_schema` creates and the
Phase 3 registry tables, the questions the sweep runner (T107) and the sweep
report (T108) ask before they write anything:

- `counted_trial`: a variant's latest `ok`, non-synthetic, `in_sample` trial
  over the variant's default in-sample window under its sweep registration.
- `is_current` / `is_stale`: the trial's data vintage at its own cutoff and its
  code vintage against the checkout's (spec, Definitions "Vintage").
- `terminal_failed`: req 2's counting rule, the sweep row's copied
  `max_failures_per_variant` identical messages under the current registration,
  clean checkout, current code vintage, excluding `store changed during run`
  and `shared read failed`, cleared by a current `ok` (a stale `ok` neither
  counts nor blocks).
- `seconds_per_variant_by_cadence`: the measured seconds per variant at each
  cadence, from `sweep_trials` joined to the variants' cadence, for T106's
  quiet-interval prediction.
- `plan_run`: the variants a run opens, in read-group order and canonical order
  within a group, and the `--rerun` refusal on an incomplete sweep.
- `sweep_state`: complete, incomplete (stale) or incomplete (unrun), with the
  stale and terminal-failed lists.

Every public function calls `lab_schema.require_lab` first (like
`lab_registry`), so on a store without the lab tables it raises
`LabNotInitialised` and the Phase 3 rules stay in force. Every read of frozen
values goes through `frozen.frozen_values` (spec, Definitions "Frozen-key
defaults"), never `params` directly, and read-group membership reuses
`backtest.sweep.READ_GROUP_FREE_KEYS`, the one definition of the keys that do
not change a variant's provider reads.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final, Literal

import duckdb

from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.holdout import Frozen, default_in_sample_window
from tradepartner.backtest.sweep import READ_GROUP_FREE_KEYS
from tradepartner.config import Cadence
from tradepartner.store import lab_registry
from tradepartner.store.lab_registry import (
    CADENCE_KEY,
    LabRegistryError,
    SweepRecord,
    SweepVariant,
)
from tradepartner.store.lab_schema import require_lab
from tradepartner.store.registry import (
    STORE_CHANGED_MESSAGE,
    canonical_params_json,
    code_tree_sha256,
    data_vintage,
    get_hypothesis_by_id,
)

#: The message a `failed` trial carries when the group's shared read failed
#: (spec req 2; the engine, T105). Excluded from the terminal-failure count.
SHARED_READ_FAILED: Final = "shared read failed"

#: `sweep_state`'s overall value (spec, Definitions "Complete sweep").
SweepStateName = Literal["complete", "incomplete (stale)", "incomplete (unrun)"]


class SweepNotCompleteError(LabRegistryError):
    """`plan_run(..., rerun=True)` on a sweep that is not complete (req 2)."""


@dataclass(frozen=True)
class TrialRow:
    """One `trials` row with its `trial_results` outcome, the fields the
    vintage and terminal-failure rules read."""

    trial_id: int
    hypothesis_id: int
    kind: str
    synthetic: bool
    start_session: date
    end_session: date
    data_cutoff: datetime | None
    data_vintage: datetime | None
    code_version: str
    code_dirty: bool | None
    code_tree_sha256: str | None
    status: str
    message: str | None


@dataclass(frozen=True)
class PlannedVariant:
    """One variant `plan_run` schedules: its read group (1-based, ordered by the
    group's lowest canonical index) and the cadence it runs at."""

    read_group_index: int
    cadence: Cadence
    variant: SweepVariant


@dataclass(frozen=True)
class RunPlan:
    """The variants a plain run (or a `--rerun`) opens, in read-group order and
    canonical order within a group (spec req 2)."""

    sweep_id: int
    rerun: bool
    variants: tuple[PlannedVariant, ...]

    def read_groups(self) -> tuple[tuple[PlannedVariant, ...], ...]:
        """`variants` grouped by `read_group_index`, groups in that order and
        members in canonical order within each (the runner's unit)."""
        groups: dict[int, list[PlannedVariant]] = {}
        for planned in self.variants:
            groups.setdefault(planned.read_group_index, []).append(planned)
        return tuple(tuple(groups[index]) for index in sorted(groups))


@dataclass(frozen=True)
class SweepState:
    """A sweep's completeness (spec, Definitions "Complete sweep"): every
    variant has a current counted trial or is terminal-failed; otherwise the
    stale variants awaiting a rerun and the unrun variants are listed."""

    sweep_id: int
    state: SweepStateName
    stale: tuple[SweepVariant, ...]
    terminal_failed: tuple[SweepVariant, ...]
    unrun: tuple[SweepVariant, ...]

    @property
    def complete(self) -> bool:
        """Whether every variant has a current counted trial or is terminal-failed."""
        return self.state == "complete"


_TRIAL_SELECT: Final = (
    "SELECT t.trial_id, t.hypothesis_id, t.kind, t.synthetic, t.start_session, "
    "t.end_session, t.data_cutoff, t.data_vintage, t.code_version, t.code_dirty, "
    "t.code_tree_sha256, r.status, r.message "
    "FROM trials t JOIN trial_results r USING (trial_id) "
)


def _sweep_window(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> tuple[date, date, date]:
    """The sweep registration's copied `(in_sample_start, holdout_start,
    holdout_end)`; raises `LabRegistryError` for an unknown sweep."""
    row = conn.execute(
        "SELECT in_sample_start, holdout_start, holdout_end FROM sweeps WHERE sweep_id = ?",
        [sweep_id],
    ).fetchone()
    if row is None:
        raise LabRegistryError(f"no sweep has id {sweep_id}")
    return row[0], row[1], row[2]


def _max_failures_per_variant(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> int:
    """The sweep row's copied `max_failures_per_variant`; raises
    `LabRegistryError` for an unknown sweep."""
    row = conn.execute(
        "SELECT max_failures_per_variant FROM sweeps WHERE sweep_id = ?", [sweep_id]
    ).fetchone()
    if row is None:
        raise LabRegistryError(f"no sweep has id {sweep_id}")
    return int(row[0])


def counted_trial(
    conn: duckdb.DuckDBPyConnection, hypothesis_id: int, sweep_id: int
) -> TrialRow | None:
    """A variant's latest `ok`, non-synthetic, `in_sample` trial over its default
    in-sample window under the sweep registration, or None.

    "Latest" is the highest `trial_id`; the window is
    `holdout.default_in_sample_window` at the variant's own frozen cadence over
    the sweep's copied window, so a cadence-axis sweep's variants each key the
    window their own rebalances resolve to. The returned trial may be **stale**:
    call `is_current` to test the vintage (spec, Definitions "Selection
    statistic"). Raises `LabRegistryError` for an unknown sweep.
    """
    require_lab(conn)
    record = get_hypothesis_by_id(conn, hypothesis_id)
    in_sample_start, holdout_start, holdout_end = _sweep_window(conn, sweep_id)
    frozen = replace(
        Frozen.from_hypothesis(record),
        in_sample_start=in_sample_start,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
    )
    cadence = frozen_values(record)[CADENCE_KEY]
    window = default_in_sample_window(frozen, cadence)
    row = conn.execute(
        _TRIAL_SELECT + "WHERE t.hypothesis_id = ? AND t.kind = 'in_sample' AND NOT t.synthetic "
        "AND r.status = 'ok' AND t.start_session = ? AND t.end_session = ? "
        "ORDER BY t.trial_id DESC LIMIT 1",
        [hypothesis_id, window.start, window.end],
    ).fetchone()
    return TrialRow(*row) if row is not None else None


def is_current(conn: duckdb.DuckDBPyConnection, trial: TrialRow) -> bool:
    """Whether `trial` is current (spec, Definitions "Vintage"): its data vintage
    equals the data vintage computed now at its own cutoff, and its code vintage
    equals the checkout's `code_tree_sha256`. A trial with no cutoff, no recorded
    data vintage or no recorded code vintage is never current."""
    require_lab(conn)
    return _is_current(conn, trial, code_tree_sha256())


def _is_current(conn: duckdb.DuckDBPyConnection, trial: TrialRow, code_vintage: str | None) -> bool:
    """`is_current` with a pre-computed checkout code vintage (the runner and
    the report read it once per call, never once per trial)."""
    if trial.data_cutoff is None or trial.data_vintage is None:
        return False
    if trial.code_tree_sha256 is None or trial.code_tree_sha256 != code_vintage:
        return False
    return data_vintage(conn, trial.data_cutoff) == trial.data_vintage


def is_stale(conn: duckdb.DuckDBPyConnection, trial: TrialRow) -> bool:
    """Whether `trial` is a stale `ok`: an `ok` trial that is not current. A
    non-`ok` trial is never stale (spec, Definitions "Vintage": only an `ok`
    trial that is no longer current is stale)."""
    require_lab(conn)
    return trial.status == "ok" and not is_current(conn, trial)


def terminal_failed(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    sweep: SweepRecord,
    code_vintage: str,
) -> bool:
    """Whether the variant is terminal-failed under req 2's counting rule: the
    sweep row's copied `max_failures_per_variant` `failed` trials carrying the
    same message, written with `code_dirty = false` at `code_vintage`, excluding
    `store changed during run` and `shared read failed`. A current `ok` trial
    clears it; a stale `ok` neither counts nor blocks."""
    require_lab(conn)
    counted = counted_trial(conn, hypothesis_id, sweep.sweep_id)
    if counted is not None and _is_current(conn, counted, code_vintage):
        return False
    return _has_terminal_failures(conn, hypothesis_id, sweep.max_failures_per_variant, code_vintage)


def _has_terminal_failures(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    max_failures: int,
    code_vintage: str | None,
) -> bool:
    """The failure count alone (the caller has already applied the current-`ok`
    clear): any single message reaching `max_failures`. A cap below 1 disables
    the state rather than making every variant terminal at zero failures; a
    `None` checkout vintage (outside a checkout) matches no failure."""
    if max_failures < 1:
        return False
    row = conn.execute(
        "SELECT 1 FROM trials t JOIN trial_results r USING (trial_id) "
        "WHERE t.hypothesis_id = ? AND r.status = 'failed' AND t.code_dirty = false "
        "AND t.code_tree_sha256 = ? AND COALESCE(r.message, '') NOT IN (?, ?) "
        "GROUP BY COALESCE(r.message, '') HAVING COUNT(*) >= ? LIMIT 1",
        [hypothesis_id, code_vintage, STORE_CHANGED_MESSAGE, SHARED_READ_FAILED, max_failures],
    ).fetchone()
    return row is not None


def _require_sweep(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> None:
    if conn.execute("SELECT 1 FROM sweeps WHERE sweep_id = ?", [sweep_id]).fetchone() is None:
        raise LabRegistryError(f"no sweep has id {sweep_id}")


def seconds_per_variant_by_cadence(
    conn: duckdb.DuckDBPyConnection, sweep_id: int
) -> dict[Cadence, float]:
    """The measured seconds one variant takes, per cadence, over the sweep's
    `sweep_trials` rows joined to their variants' frozen cadence (spec req 2,
    quiet intervals): the mean of every trial's recorded `seconds` at that
    cadence. A cadence with no trial is absent, so `quiet.seconds_per_variant`
    falls back to `lab.seconds_per_variant_default`. Raises `LabRegistryError`
    for an unknown sweep."""
    require_lab(conn)
    _require_sweep(conn, sweep_id)
    cadence_of: dict[int, Cadence] = {}
    for variant in lab_registry.sweep_variants(conn, sweep_id):
        record = get_hypothesis_by_id(conn, variant.hypothesis_id)
        cadence_of[variant.hypothesis_id] = frozen_values(record)[CADENCE_KEY]
    totals: dict[Cadence, list[float]] = {}
    rows = conn.execute(
        "SELECT t.hypothesis_id, st.seconds "
        "FROM sweep_trials st JOIN sweep_runs sr USING (sweep_run_id) "
        "JOIN trials t USING (trial_id) WHERE sr.sweep_id = ?",
        [sweep_id],
    ).fetchall()
    for hypothesis_id, seconds in rows:
        cadence = cadence_of.get(hypothesis_id)
        if cadence is None:
            continue
        totals.setdefault(cadence, []).append(float(seconds))
    return {cadence: statistics.fmean(values) for cadence, values in totals.items()}


def _read_groups(
    conn: duckdb.DuckDBPyConnection, variants: Sequence[SweepVariant]
) -> list[list[SweepVariant]]:
    """`variants` (canonical order) split into read groups: those agreeing on
    every frozen key outside `backtest.sweep.READ_GROUP_FREE_KEYS`, groups in
    first-index order and members in canonical order."""
    groups: dict[str, list[SweepVariant]] = {}
    for variant in variants:
        values = frozen_values(get_hypothesis_by_id(conn, variant.hypothesis_id))
        shared = {key: value for key, value in values.items() if key not in READ_GROUP_FREE_KEYS}
        groups.setdefault(canonical_params_json(shared), []).append(variant)
    return list(groups.values())


def plan_run(conn: duckdb.DuckDBPyConnection, sweep_id: int, rerun: bool = False) -> RunPlan:
    """The variants a run opens (spec req 2), in read-group order and canonical
    order within a group.

    A plain run lists every variant with no current counted trial that is not
    terminal-failed (a stale `ok` is rerun, an unrun variant is run). `--rerun`
    on a complete sweep lists every variant, terminal-failed ones included;
    `--rerun` on an incomplete sweep raises `SweepNotCompleteError`. Raises
    `LabRegistryError` for an unknown sweep.
    """
    require_lab(conn)
    max_failures = _max_failures_per_variant(conn, sweep_id)
    code_vintage = code_tree_sha256()
    variants = lab_registry.sweep_variants(conn, sweep_id)
    if rerun and not sweep_state(conn, sweep_id).complete:
        raise SweepNotCompleteError(
            f"sweep {sweep_id} is not complete; --rerun reruns only a complete current sweep"
        )
    planned: list[PlannedVariant] = []
    for group_index, group in enumerate(_read_groups(conn, variants), start=1):
        for variant in group:
            if not rerun:
                counted = counted_trial(conn, variant.hypothesis_id, sweep_id)
                if counted is not None and _is_current(conn, counted, code_vintage):
                    continue
                if _has_terminal_failures(conn, variant.hypothesis_id, max_failures, code_vintage):
                    continue
            cadence = frozen_values(get_hypothesis_by_id(conn, variant.hypothesis_id))[CADENCE_KEY]
            planned.append(PlannedVariant(group_index, cadence, variant))
    return RunPlan(sweep_id=sweep_id, rerun=rerun, variants=tuple(planned))


def sweep_state(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> SweepState:
    """The sweep's completeness and its stale, terminal-failed and unrun
    variants (spec, Definitions "Complete sweep"; req 2).

    A variant is complete when it has a current counted trial or is
    terminal-failed; otherwise it is **stale** (an `ok` that is not current and
    no current one) or **unrun** (no `ok` trial at all). The state is
    `incomplete (stale)` when any variant is stale, else `incomplete (unrun)`
    when any is unrun, else `complete`. Raises `LabRegistryError` for an unknown
    sweep.
    """
    require_lab(conn)
    max_failures = _max_failures_per_variant(conn, sweep_id)
    code_vintage = code_tree_sha256()
    stale: list[SweepVariant] = []
    terminal: list[SweepVariant] = []
    unrun: list[SweepVariant] = []
    for variant in lab_registry.sweep_variants(conn, sweep_id):
        counted = counted_trial(conn, variant.hypothesis_id, sweep_id)
        if counted is not None and _is_current(conn, counted, code_vintage):
            continue
        if _has_terminal_failures(conn, variant.hypothesis_id, max_failures, code_vintage):
            terminal.append(variant)
        elif counted is not None:
            stale.append(variant)
        else:
            unrun.append(variant)
    state: SweepStateName
    if stale:
        state = "incomplete (stale)"
    elif unrun:
        state = "incomplete (unrun)"
    else:
        state = "complete"
    return SweepState(sweep_id, state, tuple(stale), tuple(terminal), tuple(unrun))

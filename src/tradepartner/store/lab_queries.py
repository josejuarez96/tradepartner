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

**The rerun epoch** (#1218; T107 writes the state it reads). A `--rerun` is the
one run that plans every variant of a sweep that has already been complete, so
its `sweep_runs` row is the latest one with `n_planned = n_declared` opened
after a closed run with `completed = true` (`rerun_epoch`). From that row on,
(1) a variant with no trial in a run of the epoch is **awaiting the rerun**: it
is listed as stale and planned by the next plain run even though its earlier
trial is still current, so a `--rerun` the budget stopped is finished by plain
runs (req 2); (2) the terminal-failure count reads only the epoch's runs, so a
rerun gives every variant "a fresh count under the new run" (req 2). The
schema has no rerun flag, so a plain run after a completed run that happens to
plan every variant (a code- or data-vintage change made them all stale) also
opens an epoch. That costs nothing for (1), since such a run plans every
variant anyway; for (2) it restarts a count that a code-vintage change had
already restarted, and after a data-vintage change it can take one more
identical failure before a variant reads terminal-failed: never fewer, so no
variant leaves the argmax earlier than req 2 allows, and failures never enter N.

**Synthetic runs** (#1218). `run_sweep(..., store_path=...)` opens every trial
`synthetic=True` on a marked fixture store. The planning reads take
`synthetic`: with it true they read only synthetic trials, with it false (the
default, the real store) only non-synthetic ones, so a fixture sweep completes
and is not rerun by its next plain run, while N, V and the selection statistic
(`registry.family_sharpes`, `results.family_n`) never see a synthetic trial.

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
from tradepartner.backtest.holdout import Frozen, Window, default_in_sample_window
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


def default_window(conn: duckdb.DuckDBPyConnection, hypothesis_id: int, sweep_id: int) -> Window:
    """The variant's default in-sample window: `holdout.default_in_sample_window`
    at its own frozen cadence over the sweep registration's copied window (spec
    req 2), so a cadence-axis sweep's variants each key the window their own
    rebalances resolve to. The window `counted_trial` reads and the runner (T107)
    opens every variant's trial over. Raises `LabRegistryError` for an unknown
    sweep."""
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
    return default_in_sample_window(frozen, cadence)


def _counted_trial(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    window: Window,
    code_vintage: str | None,
    vintages: dict[datetime, datetime | None],
    synthetic: bool = False,
) -> TrialRow | None:
    """The counted trial over `window`: the latest **current** `ok`,
    `in_sample` trial (non-synthetic, or synthetic with `synthetic`; module
    docstring, "Synthetic runs"), or, when none is current, the latest `ok`
    trial (stale), or None. The spec defines the counted trial as "the latest
    `ok` ... that is current" (Definitions "Selection statistic"); the stale
    latest is still returned so a caller can tell stale from unrun. Currentness
    is read against the caller's `code_vintage` and `vintages` memo, so a caller
    that already holds them computes nothing twice."""
    rows = conn.execute(
        _TRIAL_SELECT + "WHERE t.hypothesis_id = ? AND t.kind = 'in_sample' AND t.synthetic = ? "
        "AND r.status = 'ok' AND t.start_session = ? AND t.end_session = ? "
        "ORDER BY t.trial_id DESC",
        [hypothesis_id, synthetic, window.start, window.end],
    ).fetchall()
    if not rows:
        return None
    for row in rows:
        trial = TrialRow(*row)
        if _is_current(conn, trial, code_vintage, vintages):
            return trial
    return TrialRow(*rows[0])


def counted_trial(
    conn: duckdb.DuckDBPyConnection, hypothesis_id: int, sweep_id: int
) -> TrialRow | None:
    """A variant's counted trial (spec, Definitions "Selection statistic"): its
    latest current `ok`, non-synthetic, `in_sample` trial over its default
    in-sample window under the sweep registration, or the latest such `ok` trial
    when none is current (stale), or None. Call `is_current`/`is_stale` on the
    result. Raises `LabRegistryError` for an unknown sweep."""
    require_lab(conn)
    window = default_window(conn, hypothesis_id, sweep_id)
    return _counted_trial(conn, hypothesis_id, window, code_tree_sha256(), {})


def is_current(conn: duckdb.DuckDBPyConnection, trial: TrialRow) -> bool:
    """Whether `trial` is current (spec, Definitions "Vintage"): its data vintage
    equals the data vintage computed now at its own cutoff, and its code vintage
    equals the checkout's `code_tree_sha256`. A trial with no cutoff, no recorded
    data vintage or no recorded code vintage is never current."""
    require_lab(conn)
    return _is_current(conn, trial, code_tree_sha256(), {})


def _is_current(
    conn: duckdb.DuckDBPyConnection,
    trial: TrialRow,
    code_vintage: str | None,
    vintages: dict[datetime, datetime | None],
) -> bool:
    """`is_current` with a pre-computed checkout code vintage and a memo of the
    data vintage per cutoff (`vintages`), so a caller walking many trials at one
    cutoff computes the store's vintage once."""
    if trial.data_cutoff is None or trial.data_vintage is None:
        return False
    if trial.code_tree_sha256 is None or trial.code_tree_sha256 != code_vintage:
        return False
    if trial.data_cutoff not in vintages:
        vintages[trial.data_cutoff] = data_vintage(conn, trial.data_cutoff)
    return vintages[trial.data_cutoff] == trial.data_vintage


def is_stale(conn: duckdb.DuckDBPyConnection, trial: TrialRow) -> bool:
    """Whether `trial` is a stale `ok`: an `ok` trial that is not current. A
    non-`ok` trial is never stale (spec, Definitions "Vintage": only an `ok`
    trial that is no longer current is stale)."""
    require_lab(conn)
    return trial.status == "ok" and not is_current(conn, trial)


def rerun_epoch(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> int | None:
    """The `sweep_run_id` the sweep's current rerun epoch starts at, or None
    (module docstring, "The rerun epoch"): its latest `sweep_runs` row that
    planned every declared variant (`n_planned = n_declared > 0`) and was opened
    after a closed run that left the sweep complete. Raises `LabRegistryError`
    for an unknown sweep."""
    require_lab(conn)
    _require_sweep(conn, sweep_id)
    row = conn.execute(
        "SELECT MAX(r.sweep_run_id) FROM sweep_runs r "
        "WHERE r.sweep_id = ? AND r.n_declared > 0 AND r.n_planned = r.n_declared "
        "AND EXISTS (SELECT 1 FROM sweep_runs c WHERE c.sweep_id = r.sweep_id "
        "AND c.sweep_run_id < r.sweep_run_id AND c.completed)",
        [sweep_id],
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _awaiting_rerun(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    sweep_id: int,
    epoch: int | None,
    synthetic: bool,
) -> bool:
    """Whether the variant has no trial (of the run's kind of store, any status)
    in a run of the rerun epoch `epoch`; False without an epoch."""
    if epoch is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sweep_trials st JOIN sweep_runs sr USING (sweep_run_id) "
        "JOIN trials t USING (trial_id) "
        "WHERE sr.sweep_id = ? AND sr.sweep_run_id >= ? AND t.hypothesis_id = ? "
        "AND t.synthetic = ? LIMIT 1",
        [sweep_id, epoch, hypothesis_id, synthetic],
    ).fetchone()
    return row is None


def terminal_failed(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    sweep: SweepRecord,
    code_vintage: str,
    *,
    synthetic: bool = False,
) -> bool:
    """Whether the variant is terminal-failed under req 2's counting rule: under
    this sweep registration and since its rerun epoch (module docstring), the
    sweep row's copied `max_failures_per_variant` in-sample `failed` trials
    (non-synthetic, or synthetic with `synthetic`) over the variant's default
    window carrying the same message, written with `code_dirty = false` at
    `code_vintage`, excluding `store changed during run` and `shared read
    failed`. A current `ok` trial clears it; a stale `ok` neither counts nor
    blocks; a variant awaiting a stopped `--rerun` is not terminal-failed (the
    same reading as `sweep_state` and `plan_run`)."""
    require_lab(conn)
    state = _variant_state(
        conn,
        hypothesis_id,
        sweep.sweep_id,
        sweep.max_failures_per_variant,
        code_vintage,
        {},
        rerun_epoch(conn, sweep.sweep_id),
        synthetic,
    )
    return state.state == "terminal_failed"


def _terminal_message(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    sweep_id: int,
    window: Window,
    max_failures: int,
    code_vintage: str | None,
    *,
    epoch: int | None = None,
    synthetic: bool = False,
) -> str | None:
    """The failure count alone (the caller has already applied the current-`ok`
    clear): this sweep registration's `sweep_trials` rows for the variant from
    the rerun epoch `epoch` on (every run without one), whose trials are
    in-sample, of the run's kind of store (`synthetic`) and over `window`, any
    single message reaching `max_failures`. Scoping to the sweep's runs keeps a
    hypothesis that is a variant of two sweep registrations from counting the
    other registration's failures. A cap below 1 disables the state rather than
    making every variant terminal at zero failures; a `None` checkout vintage
    (outside a checkout) matches no failure. Returns the message that reached
    the cap (a NULL message reads as the empty string), or None."""
    if max_failures < 1:
        return None
    row = conn.execute(
        "SELECT COALESCE(r.message, '') FROM trials t JOIN trial_results r USING (trial_id) "
        "JOIN sweep_trials st USING (trial_id) JOIN sweep_runs sr USING (sweep_run_id) "
        "WHERE t.hypothesis_id = ? AND sr.sweep_id = ? AND sr.sweep_run_id >= ? "
        "AND r.status = 'failed' AND t.kind = 'in_sample' AND t.synthetic = ? "
        "AND t.start_session = ? AND t.end_session = ? "
        "AND t.code_dirty = false AND t.code_tree_sha256 = ? "
        "AND COALESCE(r.message, '') NOT IN (?, ?) "
        "GROUP BY COALESCE(r.message, '') HAVING COUNT(*) >= ? "
        "ORDER BY MIN(t.trial_id) LIMIT 1",
        [
            hypothesis_id,
            sweep_id,
            epoch if epoch is not None else 0,
            synthetic,
            window.start,
            window.end,
            code_vintage,
            STORE_CHANGED_MESSAGE,
            SHARED_READ_FAILED,
            max_failures,
        ],
    ).fetchone()
    return None if row is None else str(row[0])


def _require_sweep(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> None:
    if conn.execute("SELECT 1 FROM sweeps WHERE sweep_id = ?", [sweep_id]).fetchone() is None:
        raise LabRegistryError(f"no sweep has id {sweep_id}")


def seconds_per_variant_by_cadence(
    conn: duckdb.DuckDBPyConnection, sweep_id: int
) -> dict[Cadence, float]:
    """The measured seconds one variant takes, per cadence, over the sweep's
    `sweep_trials` rows joined to their variants' frozen cadence (spec req 2,
    quiet intervals): the mean of every `ok` trial's recorded `seconds` at that
    cadence (a failed or refused trial's seconds are not a run time and are
    ignored). A cadence with no `ok` trial is absent, so
    `quiet.seconds_per_variant` falls back to `lab.seconds_per_variant_default`.
    Raises `LabRegistryError` for an unknown sweep."""
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
        "JOIN trials t USING (trial_id) JOIN trial_results r USING (trial_id) "
        "WHERE sr.sweep_id = ? AND r.status = 'ok'",
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


def plan_run(
    conn: duckdb.DuckDBPyConnection,
    sweep_id: int,
    rerun: bool = False,
    *,
    synthetic: bool = False,
) -> RunPlan:
    """The variants a run opens (spec req 2), in read-group order and canonical
    order within a group.

    A plain run lists every variant with no current counted trial that is not
    terminal-failed (a stale `ok` is rerun, an unrun variant is run), and every
    variant still awaiting a stopped `--rerun` (module docstring, "The rerun
    epoch"). `--rerun` on a complete sweep lists every variant, terminal-failed
    ones included; `--rerun` on an incomplete sweep raises
    `SweepNotCompleteError`. `synthetic` reads the synthetic trials of a
    `store_path` run instead of the non-synthetic ones (module docstring).
    Raises `LabRegistryError` for an unknown sweep.
    """
    require_lab(conn)
    max_failures = _max_failures_per_variant(conn, sweep_id)
    code_vintage = code_tree_sha256()
    variants = lab_registry.sweep_variants(conn, sweep_id)
    if rerun and not sweep_state(conn, sweep_id, synthetic=synthetic).complete:
        raise SweepNotCompleteError(
            f"sweep {sweep_id} is not complete; --rerun reruns only a complete current sweep"
        )
    epoch = rerun_epoch(conn, sweep_id)
    vintages: dict[datetime, datetime | None] = {}
    planned: list[PlannedVariant] = []
    for group_index, group in enumerate(_read_groups(conn, variants), start=1):
        for variant in group:
            if not rerun and _variant_done(
                conn,
                variant,
                sweep_id,
                max_failures,
                code_vintage,
                vintages,
                epoch,
                synthetic,
            ):
                continue
            cadence = frozen_values(get_hypothesis_by_id(conn, variant.hypothesis_id))[CADENCE_KEY]
            planned.append(PlannedVariant(group_index, cadence, variant))
    return RunPlan(sweep_id=sweep_id, rerun=rerun, variants=tuple(planned))


VariantStateName = Literal["current", "terminal_failed", "stale", "unrun"]


@dataclass(frozen=True)
class VariantState:
    """One variant's state under req 2, the one classifier the runner's plan,
    `sweep_state` and the sweep report (`backtest.sweep_report`) all read.
    `trial` is the counted trial when `current`, the latest `ok` one when
    `stale` (None for a variant awaiting a rerun that never had one), else
    None; `terminal_message` is the message that reached the cap when
    `terminal_failed`."""

    variant: SweepVariant
    state: VariantStateName
    trial: TrialRow | None
    terminal_message: str | None


def _variant_state(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    sweep_id: int,
    max_failures: int,
    code_vintage: str | None,
    vintages: dict[datetime, datetime | None],
    epoch: int | None,
    synthetic: bool,
    variant: SweepVariant | None = None,
) -> VariantState:
    """One variant's state under req 2 (`sweep_state`'s docstring): a variant
    awaiting a stopped `--rerun` is stale whatever its earlier trial."""
    if variant is None:
        variant = next(
            v
            for v in lab_registry.sweep_variants(conn, sweep_id)
            if v.hypothesis_id == hypothesis_id
        )
    window = default_window(conn, hypothesis_id, sweep_id)
    counted = _counted_trial(conn, hypothesis_id, window, code_vintage, vintages, synthetic)
    awaiting = _awaiting_rerun(conn, hypothesis_id, sweep_id, epoch, synthetic)
    if not awaiting and counted is not None and _is_current(conn, counted, code_vintage, vintages):
        return VariantState(variant, "current", counted, None)
    message = (
        None
        if awaiting
        else _terminal_message(
            conn,
            hypothesis_id,
            sweep_id,
            window,
            max_failures,
            code_vintage,
            epoch=epoch,
            synthetic=synthetic,
        )
    )
    if message is not None:
        return VariantState(variant, "terminal_failed", None, message)
    if counted is not None:
        return VariantState(variant, "stale", counted, None)
    return VariantState(variant, "unrun", None, None)


def _variant_done(
    conn: duckdb.DuckDBPyConnection,
    variant: SweepVariant,
    sweep_id: int,
    max_failures: int,
    code_vintage: str | None,
    vintages: dict[datetime, datetime | None],
    epoch: int | None,
    synthetic: bool,
) -> bool:
    """Whether a plain run skips the variant: current or terminal-failed."""
    state = _variant_state(
        conn,
        variant.hypothesis_id,
        sweep_id,
        max_failures,
        code_vintage,
        vintages,
        epoch,
        synthetic,
        variant,
    )
    return state.state in ("current", "terminal_failed")


def variant_states(
    conn: duckdb.DuckDBPyConnection,
    sweep_id: int,
    *,
    synthetic: bool = False,
    code_vintage: str | None = None,
) -> tuple[VariantState, ...]:
    """Every variant's `VariantState`, in canonical order (the one classifier,
    `sweep_state`'s docstring). `code_vintage` is the checkout's
    `code_tree_sha256` unless given (the report's tests pass one). Raises
    `LabRegistryError` for an unknown sweep."""
    require_lab(conn)
    max_failures = _max_failures_per_variant(conn, sweep_id)
    vintage = code_vintage if code_vintage is not None else code_tree_sha256()
    epoch = rerun_epoch(conn, sweep_id)
    vintages: dict[datetime, datetime | None] = {}
    return tuple(
        _variant_state(
            conn,
            variant.hypothesis_id,
            sweep_id,
            max_failures,
            vintage,
            vintages,
            epoch,
            synthetic,
            variant,
        )
        for variant in lab_registry.sweep_variants(conn, sweep_id)
    )


def overall_state(states: Sequence[VariantState]) -> SweepStateName:
    """The sweep's state from its variants' (`sweep_state`'s docstring)."""
    if any(s.state == "stale" for s in states):
        return "incomplete (stale)"
    if any(s.state == "unrun" for s in states):
        return "incomplete (unrun)"
    return "complete"


def sweep_state(
    conn: duckdb.DuckDBPyConnection,
    sweep_id: int,
    *,
    synthetic: bool = False,
    code_vintage: str | None = None,
) -> SweepState:
    """The sweep's completeness and its stale, terminal-failed and unrun
    variants (spec, Definitions "Complete sweep"; req 2).

    A variant is complete when it has a current counted trial or is
    terminal-failed; otherwise it is **stale** (an `ok` that is not current and
    no current one, or a variant awaiting a stopped `--rerun`: module
    docstring, "The rerun epoch") or **unrun** (no `ok` trial at all). The
    state is `incomplete (stale)` when any variant is stale, else `incomplete
    (unrun)` when any is unrun, else `complete`. `synthetic` reads a
    `store_path` run's synthetic trials (module docstring). Raises
    `LabRegistryError` for an unknown sweep.
    """
    require_lab(conn)
    states = variant_states(conn, sweep_id, synthetic=synthetic, code_vintage=code_vintage)
    return SweepState(
        sweep_id,
        overall_state(states),
        tuple(s.variant for s in states if s.state == "stale"),
        tuple(s.variant for s in states if s.state == "terminal_failed"),
        tuple(s.variant for s in states if s.state == "unrun"),
    )

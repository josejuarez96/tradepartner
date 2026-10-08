"""The sweep runner (strategy-lab spec req 2; plan task T107).

`run_sweep` runs one sweep registration's planned variants, read group by read
group, and records every variant as a trial. It composes what other modules
own: the plan of the run and the counting rules (`store.lab_queries`, T103b),
the quiet-interval arithmetic (`backtest.quiet`, T106), the trial window and
kind (`holdout.decide`), one read set per step (`engine.run_many`, T105), the
result rows (`results.write_results`) and the run rows (`store.lab_registry`).
In order:

1. **Refusals, before any row is written.** `LabNotInitialised` on a store
   without the lab tables; with `store_path`, `registry.UnmarkedStoreRefused`
   for a store without the `store_markers` `fixture` row (a copy of the real
   store, even the real store's own path: req 2 and the fixture-marker
   definition exempt only `run_hypothesis`), and every trial on a marked store
   is opened `synthetic=True`; `RegistryTooLarge` when the store file exceeds
   `lab.registry_size_refuse_gb` (a warning above `lab.registry_size_warn_gb`);
   `NoCodeVintage` outside a git checkout, where no trial could ever be current;
   an unknown slug; `--rerun` on an incomplete sweep
   (`lab_queries.SweepNotCompleteError`). The CLI never passes `store_path`.
2. **Plan and open the run.** `lab_queries.plan_run` lists the variants (the
   synthetic trials' plan under `store_path`, #1218), and the run's
   `sweep_runs` row is opened with the declared and planned counts.
3. **Per read group**, in the plan's order: stop cleanly at the group boundary
   once the time budget (`--time-budget-minutes`, default
   `lab.sweep_time_budget_minutes`) is spent; otherwise ask
   `quiet.start_decision` with the group's predicted duration (its variant
   count times the measured seconds per variant at its cadence from
   `lab_queries.seconds_per_variant_by_cadence`, or
   `lab.seconds_per_variant_default`), sleeping through quiet intervals until
   it says `start` (a wait the budget cannot cover stops the run instead).
   Then, in one write chunk, every variant's trial is opened through
   `holdout.decide` as `kind=in_sample` over its default in-sample window
   (`lab_queries.default_window`, `holdout.default_in_sample_window` at its
   cadence), so every variant has its `TrialHandle` before the group's first
   provider call. The group runs through `engine.run_many` over a
   `StoreProvider` that, between steps, waits out any quiet interval with its
   step connection closed. Each variant is then closed in its own write chunk
   with one `sweep_trials` row: `ok` through `results.write_results` at
   `lab.sweep_detail_level`; `failed` with its own error's message when its own
   computation failed; `failed` with `shared read failed` on every variant
   still open when a shared read failed; `failed` with `store changed during
   run` when the data vintage at its own cutoff differs between the trial's
   open and the write.
4. **Close the run**: the counts, the seconds, `n_trials_at_end`
   (`results.family_n`, the one N function), `sr_star_annual_at_end`
   (`expected_max_sharpe` at that N over the family's excess-basis
   `family_sharpes` variance floored at the family rules'
   `min_sharpe_variance_annual`), and `completed` from `lab_queries.sweep_state`.

**Quiet intervals** (spec, Definitions). The runner holds no connection
between groups. Inside an interval it opens no trial, holds no connection and
writes nothing: a group starts only outside one, a running group pauses
between steps (the provider's step connection is closed before the pause and a
new one opened after it), and the result writes and the run's close wait for
the interval to end. The paper interval is always applied, as if a paper window
were open: nothing under `backtest/` may read the paper journal (the execution
boundary, `tests/execution/test_boundaries.py`), so the runner cannot tell, and
it takes the side that never contends with `paper run`; the paper interval is
about an hour on each quiet weekday.

**A shared read that keeps failing.** A provider read the group shares can
fail for one variant's names alone (#1200's finding: bad data on a name only
one variant holds fails the union read). Req 2 names that `shared read
failed` and excludes it from the terminal-failure count, so a deterministic
data defect leaves the group's variants unrun or stale and every plain run
tries the group once more, fails it the same way and moves on: no run loops,
and the sweep stays incomplete (no argmax, no promotion) until the data is
fixed, which changes the data vintage. The message is exactly `shared read
failed`, the excluded one; the provider's error is in
`SweepRunOutcome.errors`.

**The store-changed rule** is req 2's: by data vintage at each variant's own
cutoff, the same rule `registry.write_result` applies (#1232), so a row
ingested during a group for a session after the cutoff fails nothing.

`clock` (default the system clock) is the one source of time: the budget,
the quiet intervals and the seconds recorded per variant read it, and the
runner sleeps only through it, so a test drives every wait with a fake one.
"""

from __future__ import annotations

import logging
import math
import time
import traceback
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Final, Literal, Protocol, cast

import duckdb

from tradepartner import calendar
from tradepartner.backtest import engine, quiet
from tradepartner.backtest.engine import BacktestResult, SharedReadFailed
from tradepartner.backtest.holdout import Flags, Frozen, Reasons, Window, decide, gap_sessions
from tradepartner.backtest.hypothesis import load_frozen
from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.backtest.results import family_n, write_results
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import Connect, StoreProvider
from tradepartner.config import Cadence, HypothesisFamily, Settings, clean_message, get_settings
from tradepartner.store import lab_queries, lab_registry, lab_schema, registry, schema
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only
from tradepartner.store.lab_queries import SHARED_READ_FAILED, PlannedVariant
from tradepartner.store.lab_registry import LabRegistryError, SweepRecord
from tradepartner.store.registry import STORE_CHANGED_MESSAGE, TrialHandle

_log = logging.getLogger(__name__)

#: The owner, unless a caller (`backtest-runner`, a test) names itself.
DEFAULT_RUN_BY: Final = "owner"

#: Bytes per gigabyte for the `lab.registry_size_*` guards (decimal, as `lab status`
#: prints the store size).
_BYTES_PER_GB: Final = 1e9

TrialStatus = Literal[
    "ok", "failed", "refused_window", "refused_holdout", "refused_gap", "refused_variant"
]


class RegistryTooLarge(LabRegistryError):
    """The store file exceeds `lab.registry_size_refuse_gb`: `sweep run` refuses to
    start (req 13). Nothing in the lab deletes or compacts the registry."""


class NoCodeVintage(LabRegistryError):
    """`registry.code_tree_sha256` is None (not a git checkout): no trial could ever
    be current, so every plain run would rerun every variant."""


class Clock(Protocol):
    """The runner's one source of time: `now()` is tz-aware UTC, `sleep` waits."""

    def now(self) -> datetime:
        """The current instant, tz-aware UTC."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait `seconds` (non-negative)."""
        ...


class SystemClock:
    """The wall clock."""

    def now(self) -> datetime:
        """`datetime.now(UTC)`."""
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        """`time.sleep`."""
        time.sleep(max(seconds, 0.0))


@dataclass(frozen=True)
class VariantOutcome:
    """One trial the run opened: its variant, read group and recorded outcome."""

    trial_id: int
    hypothesis_id: int
    variant_index: int
    read_group_index: int
    status: TrialStatus
    message: str | None


@dataclass(frozen=True)
class SweepRunOutcome:
    """What one `run_sweep` call did. `errors` maps a failed trial to the formatted
    error behind it (a shared read's cause included), for the caller to print;
    `stopped_by_budget` is true when the budget ended the run at a group boundary
    with groups left."""

    sweep_id: int
    sweep_run_id: int
    rerun: bool
    synthetic: bool
    n_declared: int
    n_planned: int
    n_ok: int
    n_failed: int
    n_terminal_failed: int
    completed: bool
    stopped_by_budget: bool
    seconds: float
    trials: tuple[VariantOutcome, ...]
    warnings: tuple[str, ...]
    errors: dict[int, str] = field(default_factory=dict)


@dataclass
class _Timing:
    """The quiet-interval inputs every wait reads, and the seconds spent paused."""

    clock: Clock
    settings: Settings
    paper_window_open: bool
    paused_seconds: float = 0.0

    def intervals(self, now: datetime) -> list[quiet.QuietInterval]:
        return quiet.quiet_intervals_around(
            now,
            self.settings,
            paper_window_open=self.paper_window_open,
            session_open=_session_open,
        )

    def wait_out_interval(self) -> None:
        """Sleep through the quiet interval holding now, if any, until none does."""
        while True:
            now = self.clock.now()
            holding = next((i for i in self.intervals(now) if i.contains(now)), None)
            if holding is None:
                return
            pause = (holding.end - now).total_seconds()
            self.clock.sleep(pause)
            self.paused_seconds += pause


def _session_open(day: date) -> datetime | None:
    return calendar.session_open(day) if calendar.is_session(day) else None


class _PausingProvider(StoreProvider):
    """A `StoreProvider` that waits out a quiet interval at each step boundary, with
    its step connection closed (module docstring, "Quiet intervals"). A step is one
    read time: the first read at a new `t` is where `StoreProvider` closes the
    previous step's connection and opens the next one."""

    def __init__(
        self,
        connect: Connect,
        handle: TrialHandle,
        frozen_settings: Settings,
        timing: _Timing,
    ) -> None:
        self._timing = timing
        super().__init__(connect, handle, frozen_settings)

    def _at(self, t: datetime) -> duckdb.DuckDBPyConnection:
        if self._conn is None or self._step_t != t:
            self.end_step()
            self._timing.wait_out_interval()
        return super()._at(t)


@dataclass
class _Opened:
    """A variant whose trial is open: what its run and its write need."""

    planned: PlannedVariant
    handle: TrialHandle
    window: Window
    cutoff: datetime
    vintage: datetime | None
    frozen_settings: Settings | None = None
    status: TrialStatus | None = None
    message: str | None = None
    error: str | None = None
    results: dict[float, BacktestResult] | None = None
    ran: bool = False


def _on_store(live: Settings, store_path: Path | str | None) -> Settings:
    if store_path is None:
        return live
    store = live.store.model_copy(update={"path": str(store_path)})
    return live.model_copy(update={"store": store})


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _refuse_size(conn: duckdb.DuckDBPyConnection, live: Settings) -> list[str]:
    """Req 13's guard: `RegistryTooLarge` above the refuse threshold, a warning
    above the warn threshold."""
    size = lab_registry.registry_size_bytes(conn)
    gigabytes = size / _BYTES_PER_GB
    if gigabytes > live.lab.registry_size_refuse_gb:
        raise RegistryTooLarge(
            f"the store is {gigabytes:.3f} GB, above lab.registry_size_refuse_gb "
            f"({live.lab.registry_size_refuse_gb} GB): sweep run refuses to start"
        )
    if gigabytes > live.lab.registry_size_warn_gb:
        return [
            f"the store is {gigabytes:.3f} GB, above lab.registry_size_warn_gb "
            f"({live.lab.registry_size_warn_gb} GB)"
        ]
    return []


def _measured(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> dict[Cadence, float | None]:
    """The measured seconds per variant by cadence, a non-positive mean (a run
    faster than the clock's resolution) read as no measurement."""
    measured = lab_queries.seconds_per_variant_by_cadence(conn, sweep_id)
    return {cadence: value if value > 0 else None for cadence, value in measured.items()}


def run_sweep(
    slug: str,
    *,
    time_budget_minutes: float | None = None,
    rerun: bool = False,
    note: str | None = None,
    store_path: Path | str | None = None,
    clock: Clock | None = None,
    run_by: str = DEFAULT_RUN_BY,
) -> SweepRunOutcome:
    """Run the planned variants of sweep `slug`'s latest registration (module
    docstring) and return what the run did.

    Raises, before any row is written: `LabNotInitialised`,
    `registry.UnmarkedStoreRefused` (a `store_path` without the fixture marker),
    `RegistryTooLarge`, `NoCodeVintage`, `LabRegistryError` for an unknown slug,
    `lab_queries.SweepNotCompleteError` for `rerun` on an incomplete sweep, and
    `ValueError` for a non-positive budget.
    """
    clock = clock if clock is not None else SystemClock()
    live = get_settings()
    budget = (
        time_budget_minutes
        if time_budget_minutes is not None
        else live.lab.sweep_time_budget_minutes
    )
    if not budget > 0:
        raise ValueError(f"the time budget must be positive, got {budget} minutes")
    store = _on_store(live, store_path)
    synthetic = store_path is not None

    # The quiet intervals are waited out before the store is opened at all; the
    # refusals read only, and only then is anything written.
    timing = _Timing(clock, live, paper_window_open=True)
    timing.wait_out_interval()
    with open_read_only(store) as conn:
        lab_schema.require_lab(conn)
        if synthetic and not lab_schema.has_fixture_marker(conn):
            raise registry.UnmarkedStoreRefused(
                "store_path names a store without the store_markers fixture row: "
                "run_sweep refuses every unmarked store"
            )
        warnings = _refuse_size(conn, live)
        if lab_registry.sweep_by_slug(conn, slug) is None:
            raise LabRegistryError(f"no sweep is registered as {slug!r}")
    code_vintage = registry.code_tree_sha256()
    if code_vintage is None:
        raise NoCodeVintage("no code vintage outside a git checkout; run from the repo")
    for warning in warnings:
        _log.warning(warning)
    timing.wait_out_interval()
    # The budget counts from here: a quiet interval the run started inside is
    # waited out first and spends none of it.
    started = clock.now()
    deadline = started + timedelta(minutes=budget)

    with open_for_write(store) as conn:
        schema.init_schema(conn)
        sweep = lab_registry.sweep_by_slug(conn, slug)
        assert sweep is not None
        plan = lab_queries.plan_run(conn, sweep.sweep_id, rerun, synthetic=synthetic)
        measured = _measured(conn, sweep.sweep_id)
        sweep_run_id = lab_registry.open_sweep_run(
            conn,
            sweep_id=sweep.sweep_id,
            time_budget_minutes=math.ceil(budget),
            n_declared=sweep.n_variants,
            n_planned=len(plan.variants),
            code_tree_sha256=code_vintage,
            run_by=run_by,
            note=note,
        )

    outcomes: list[_Opened] = []
    stopped = False
    groups = plan.read_groups()
    try:
        for group in groups:
            if not _ready_to_start(group, measured, timing, deadline):
                stopped = True
                break
            outcomes.extend(
                _run_group(group, sweep, sweep_run_id, store, live, synthetic, run_by, timing)
            )
    except BaseException:
        # An interrupt or an error mid-group: the run's row is still closed (not
        # complete); trials of the group in flight stay unfinished (no result
        # row), so they count nowhere and the next plain run reruns them.
        with suppress(Exception):
            _close_run(store, sweep, sweep_run_id, outcomes, timing, started, synthetic)
        raise
    n_terminal, completed = _close_run(
        store, sweep, sweep_run_id, outcomes, timing, started, synthetic
    )
    return SweepRunOutcome(
        sweep_id=sweep.sweep_id,
        sweep_run_id=sweep_run_id,
        rerun=rerun,
        synthetic=synthetic,
        n_declared=sweep.n_variants,
        n_planned=len(plan.variants),
        n_ok=sum(1 for o in outcomes if o.status == "ok"),
        n_failed=sum(1 for o in outcomes if o.status != "ok"),
        n_terminal_failed=n_terminal,
        completed=completed,
        stopped_by_budget=stopped,
        seconds=(clock.now() - started).total_seconds(),
        trials=tuple(
            VariantOutcome(
                trial_id=o.handle.trial_id,
                hypothesis_id=o.handle.hypothesis_id,
                variant_index=o.planned.variant.variant_index,
                read_group_index=o.planned.read_group_index,
                status=o.status if o.status is not None else "failed",
                message=o.message,
            )
            for o in outcomes
        ),
        warnings=tuple(warnings),
        errors={o.handle.trial_id: o.error for o in outcomes if o.error is not None},
    )


def _ready_to_start(
    group: Sequence[PlannedVariant],
    measured: dict[Cadence, float | None],
    timing: _Timing,
    deadline: datetime,
) -> bool:
    """Whether the group starts (after any quiet wait), or the run stops here: the
    budget is spent, or the wait `quiet.start_decision` asks for ends past it."""
    cadence = group[0].cadence
    per_variant = quiet.seconds_per_variant(measured, cadence, timing.settings)
    predicted = quiet.predicted_group_seconds(len(group), per_variant)
    while True:
        now = timing.clock.now()
        if now >= deadline:
            return False
        intervals = timing.intervals(now)
        if quiet.start_decision(now, predicted, intervals) == "start":
            return True
        upcoming = next((i for i in intervals if now < i.end), None)
        if upcoming is None or upcoming.end >= deadline:
            return False
        pause = (upcoming.end - now).total_seconds()
        timing.clock.sleep(pause)
        timing.paused_seconds += pause


def _open_group(
    conn: duckdb.DuckDBPyConnection,
    group: Sequence[PlannedVariant],
    sweep: SweepRecord,
    store: Settings,
    live: Settings,
    synthetic: bool,
    run_by: str,
) -> list[_Opened]:
    """Open every variant's trial (one write chunk, before any provider call):
    `holdout.decide` over its default window as an in-sample run, the data cutoff
    at its last rebalance session's close. A refusal (none is expected over the
    default window) or a variant whose frozen settings cannot load is closed at
    once, in the same chunk, and never runs."""
    opened: list[_Opened] = []
    for planned in group:
        record = registry.get_hypothesis_by_id(conn, planned.variant.hypothesis_id)
        window = lab_queries.default_window(conn, record.hypothesis_id, sweep.sweep_id)
        cadence = planned.cadence
        # `lab=None`: the variant refusal is the `backtest` path's (req 5(a)); this
        # is the variant path, and with no flags no spend rule can apply.
        decision = decide(
            window, Frozen.from_hypothesis(record), Flags(), Reasons(), None, (), cadence=cadence
        )
        sessions = gap_sessions(window, cadence)
        cutoff = read_time(sessions[-1], cadence)
        handle = registry.open_trial(
            conn,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            start_session=window.start,
            end_session=window.end,
            data_cutoff=cutoff,
            synthetic=synthetic,
            run_by=run_by,
            settings=live,
        )
        (vintage,) = conn.execute(  # type: ignore[misc]
            "SELECT data_vintage FROM trials WHERE trial_id = ?", [handle.trial_id]
        ).fetchone()
        entry = _Opened(planned, handle, window, cutoff, vintage)
        if decision.outcome != "run" or decision.kind != "in_sample":
            entry.status, entry.message = _refusal(decision.outcome), decision.message
        else:
            try:
                entry.frozen_settings = load_frozen(conn, record.slug, settings=live)
            except Exception as exc:
                entry.status, entry.message = "failed", _describe(exc)
                entry.error = "".join(traceback.format_exception(exc))
        if entry.status is not None:
            entry.message = clean_message(entry.message or "", store)
            registry.close_trial(conn, handle, entry.status, entry.message)
        opened.append(entry)
    return opened


def _refusal(outcome: str) -> TrialStatus:
    if outcome in ("refused_window", "refused_holdout", "refused_gap", "refused_variant"):
        return outcome  # type: ignore[return-value]
    return "failed"


def _run_group(
    group: Sequence[PlannedVariant],
    sweep: SweepRecord,
    sweep_run_id: int,
    store: Settings,
    live: Settings,
    synthetic: bool,
    run_by: str,
    timing: _Timing,
) -> list[_Opened]:
    """Open, run and close one read group (module docstring, step 3)."""
    began, paused_before = timing.clock.now(), timing.paused_seconds
    with open_for_write(store) as conn:
        opened = _open_group(conn, group, sweep, store, live, synthetic, run_by)
    running = [o for o in opened if o.status is None]
    if running:
        _run_engine(running, sweep, store, timing)
    elapsed = (timing.clock.now() - began).total_seconds()
    seconds = max(elapsed - (timing.paused_seconds - paused_before), 0.0) / max(len(running), 1)
    timing.wait_out_interval()
    for entry in opened:
        # A variant closed at its open never ran: its seconds are not a run time.
        _close_variant(entry, sweep_run_id, store, live, seconds if entry.ran else 0.0)
    return opened


def _run_engine(
    running: list[_Opened], sweep: SweepRecord, store: Settings, timing: _Timing
) -> None:
    """`engine.run_many` over the group, recording each variant's results or error."""
    first = running[0]
    for entry in running:
        entry.ran = True
    try:
        windows = {o.window for o in running}
        if len(windows) != 1:
            raise ValueError(f"a read group runs over one window, got {sorted(map(str, windows))}")
        assert first.frozen_settings is not None
        costs = first.frozen_settings.costs
        levels = sorted({costs.per_side_bps, *costs.sensitivity_per_side_bps})
        variants = [(o.frozen_settings, o.handle) for o in running if o.frozen_settings is not None]
        connect: Connect = partial(open_read_only, store)
        with _PausingProvider(connect, first.handle, first.frozen_settings, timing) as provider:
            results = engine.run_many(
                variants,
                provider,
                first.window.start,
                first.window.end,
                levels,
                family=cast(HypothesisFamily, sweep.family),
            )
    except SharedReadFailed as exc:
        error = "".join(traceback.format_exception(exc))
        for entry in running:
            entry.status, entry.message, entry.error = "failed", SHARED_READ_FAILED, error
        return
    except Exception as exc:
        error = "".join(traceback.format_exception(exc))
        for entry in running:
            entry.status, entry.message, entry.error = "failed", _describe(exc), error
        return
    for entry in running:
        trial_id = entry.handle.trial_id
        if trial_id in results.failures:
            failure = results.failures[trial_id]
            entry.status, entry.message = "failed", _describe(failure)
            entry.error = "".join(traceback.format_exception(failure))
        else:
            entry.results = results[trial_id]


def _close_variant(
    entry: _Opened, sweep_run_id: int, store: Settings, live: Settings, seconds: float
) -> None:
    """Write the variant's outcome and its `sweep_trials` row in one write chunk.
    A variant that entered the engine fails `store changed during run` when the
    data vintage at its cutoff moved since the open, whatever its own outcome
    (req 2: every open variant of the group; its own error would otherwise count
    toward terminal failure on data that changed under it); else its result rows,
    or its own failure. A write that raises is closed `failed` in a chunk of its
    own, except a lock timeout, which is an infrastructure state and not the
    variant's: that trial is left unfinished (no result row, so it neither counts
    nor moves the terminal-failure count) and the next plain run reruns it."""
    try:
        with open_for_write(store) as conn:
            changed = entry.ran and registry.data_vintage(conn, entry.cutoff) != entry.vintage
            if changed:
                entry.status, entry.message = "failed", STORE_CHANGED_MESSAGE
                registry.close_trial(conn, entry.handle, "failed", STORE_CHANGED_MESSAGE)
            elif entry.results is not None and entry.status is None:
                assert entry.frozen_settings is not None
                status = write_results(
                    conn,
                    entry.handle,
                    entry.results,
                    entry.frozen_settings,
                    detail_level=live.lab.sweep_detail_level,
                )
                entry.status = "ok" if status == "ok" else "failed"
                entry.message = None if status == "ok" else STORE_CHANGED_MESSAGE
            elif _has_no_result(conn, entry.handle):
                entry.status = entry.status if entry.status is not None else "failed"
                entry.message = clean_message(entry.message or "", store)
                registry.close_trial(conn, entry.handle, entry.status, entry.message)
            _write_sweep_trial(conn, entry, sweep_run_id, seconds)
    except StoreLockedError as exc:
        entry.status, entry.message = "failed", clean_message(_describe(exc), store)
        entry.error = "".join(traceback.format_exception(exc))
    except Exception as exc:
        entry.status, entry.message = "failed", clean_message(_describe(exc), store)
        entry.error = "".join(traceback.format_exception(exc))
        with open_for_write(store) as conn:
            if _has_no_result(conn, entry.handle):
                registry.close_trial(conn, entry.handle, "failed", entry.message)
            _write_sweep_trial(conn, entry, sweep_run_id, seconds)
    entry.results = None


def _has_no_result(conn: duckdb.DuckDBPyConnection, handle: TrialHandle) -> bool:
    row = conn.execute(
        "SELECT 1 FROM trial_results WHERE trial_id = ?", [handle.trial_id]
    ).fetchone()
    return row is None


def _write_sweep_trial(
    conn: duckdb.DuckDBPyConnection, entry: _Opened, sweep_run_id: int, seconds: float
) -> None:
    lab_registry.write_sweep_trial(
        conn,
        sweep_run_id=sweep_run_id,
        trial_id=entry.handle.trial_id,
        read_group_index=entry.planned.read_group_index,
        seconds=seconds,
    )


def _close_run(
    store: Settings,
    sweep: SweepRecord,
    sweep_run_id: int,
    outcomes: Sequence[_Opened],
    timing: _Timing,
    started: datetime,
    synthetic: bool,
) -> tuple[int, bool]:
    """Fill the run's `sweep_runs` row (module docstring, step 4); return its
    terminal-failed count and whether the run leaves the sweep complete."""
    timing.wait_out_interval()
    with open_for_write(store) as conn:
        state = lab_queries.sweep_state(conn, sweep.sweep_id, synthetic=synthetic)
        n_trials = family_n(conn, sweep.family)
        variance = registry.family_sharpes(conn, sweep.family).variance("excess_spy")
        rules = lab_registry.family_rules(conn, sweep.family)
        floor = rules.min_sharpe_variance_annual if rules is not None else 0.0
        floored = max(variance if variance is not None else 0.0, floor)
        sr_star = expected_max_sharpe(n_trials, floored) if n_trials >= 1 else 0.0
        lab_registry.close_sweep_run(
            conn,
            sweep_run_id,
            n_ok=sum(1 for o in outcomes if o.status == "ok"),
            n_failed=sum(1 for o in outcomes if o.status != "ok"),
            n_terminal_failed=len(state.terminal_failed),
            seconds=(timing.clock.now() - started).total_seconds(),
            n_trials_at_end=n_trials,
            sr_star_annual_at_end=sr_star,
            completed=state.complete,
        )
    return len(state.terminal_failed), state.complete

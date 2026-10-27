"""The verdict of a Lab run and the runs in flight, read from the registry
(plan T291, amendment 2026-10-27 #2304; ADR 0018 amendment A).

**The registry decides, never the exit code.** `verdict(...)` takes the run's
kind and slug, the baseline the page recorded before it started the child (the
highest `trial_id` for a `backtest`, the highest `sweep_run_id` of the sweep for
a `sweep_run`), the child's exit code (or None when the dashboard lost it), the
child's already-scrubbed text and `now`. It returns what the registry says
happened after that baseline:

- `backtest`: the first non-synthetic trial of the slug above the baseline
  (`registry.list_trials`), its `status` and `message` word for word. A desk run
  of the same slug that started earlier has a lower id and is never taken for
  this one. A `refused_window` trial whose child exited 2 reads
  `refused_window` with the stored message, not "usage error".
- `sweep_run`: the first `sweep_runs` row above the baseline: `completed`, `ok`
  or `failed` (every planned variant ran), `ended_early` ("ended before every
  planned variant ran"; the log tail says why), or `running` (the row is open
  and no exit code is known); each variant's stored outcome; and, for a complete
  sweep, `sweep_report`'s two pre-registered verdicts.
- With no new row, and only then, the exit code speaks: 2 is `refused` with the
  text, 1 with text starting `store busy:` is `store_busy`, any other code is
  `failed`, and None is "ended with no trial recorded".

Nothing here writes: every read is a registry reader, and no function opens a
connection (the caller's short read-only one is passed in).

`in_flight(conn, now, settings)` lists what is open in the registry: a
`sweep_runs` row with no `finished_at` or an `unfinished` non-synthetic trial,
started within `ui.lab_orphan_after_hours`, is `running` (it blocks a new Run);
an older one is listed apart as an `orphan` with its remedy and does not block.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import duckdb

from tradepartner.backtest import sweep_report
from tradepartner.config import Settings
from tradepartner.store import lab_registry, registry
from tradepartner.store.lab_schema import LabNotInitialised, require_lab

RunKind = Literal["backtest", "sweep_run"]

#: The outcome words that are not a stored `trial_results.status`.
NO_ROW_REFUSED: Final = "refused"
NO_ROW_STORE_BUSY: Final = "store_busy"
NO_ROW_FAILED: Final = "failed"
NO_TRIAL: Final = "no_trial"
RUNNING: Final = "running"
ENDED_EARLY: Final = "ended_early"
COMPLETED: Final = "completed"

NO_TRIAL_TEXT: Final = "ended with no trial recorded"
ENDED_EARLY_TEXT: Final = "ended before every planned variant ran"
_STORE_BUSY_PREFIX: Final = "store busy:"

ORPHAN_REMEDY: Final = (
    "Nothing here is running it: the registry never recorded its end, so its writer "
    "died. It does not block a new run. Close it at the desk if it should not stay open."
)


@dataclass(frozen=True)
class VariantOutcome:
    """One variant a sweep run opened, with the status and message as stored."""

    variant_index: int
    slug: str
    status: str
    message: str | None


@dataclass(frozen=True)
class SweepVerdicts:
    """A complete sweep's two pre-registered verdicts, from `sweep_report`."""

    argmax_slug: str
    promote_at_least: float
    promote_at_least_met: bool
    retire_below: float
    retire_below_met: bool


@dataclass(frozen=True)
class LabVerdict:
    """What a Lab run came to. `outcome` is a stored trial status (`ok`, `failed`,
    `refused_window`, ...) or one of this module's words; `message` is the stored
    message (or, with no row, the child's scrubbed text); `from_registry` is false
    only when no new row exists and the exit code decided."""

    kind: RunKind
    slug: str
    outcome: str
    text: str
    message: str | None
    from_registry: bool
    trial_id: int | None = None
    sweep_run_id: int | None = None
    n_planned: int | None = None
    n_ok: int | None = None
    n_failed: int | None = None
    variants: tuple[VariantOutcome, ...] = ()
    verdicts: SweepVerdicts | None = None
    log_tail: str = ""


def _no_row(kind: RunKind, slug: str, exit_code: int | None, output: str) -> LabVerdict:
    """The verdict when the registry has no row after the baseline."""
    if exit_code is None:
        return LabVerdict(kind, slug, NO_TRIAL, NO_TRIAL_TEXT, None, False, log_tail=output)
    if exit_code == 2:
        return LabVerdict(kind, slug, NO_ROW_REFUSED, output, output, False, log_tail=output)
    if exit_code == 1 and output.lstrip().startswith(_STORE_BUSY_PREFIX):
        return LabVerdict(kind, slug, NO_ROW_STORE_BUSY, output, output, False, log_tail=output)
    text = output or f"exited {exit_code} and recorded nothing"
    return LabVerdict(kind, slug, NO_ROW_FAILED, text, output or None, False, log_tail=output)


def _trial_verdict(
    conn: duckdb.DuckDBPyConnection,
    slug: str,
    baseline_id: int,
    exit_code: int | None,
    output: str,
) -> LabVerdict:
    above = [t for t in registry.list_trials(conn, slug) if t.trial_id > baseline_id]
    if not above:
        return _no_row("backtest", slug, exit_code, output)
    trial = min(above, key=lambda t: t.trial_id)
    outcome = trial.status
    message = trial.message
    if trial.status == registry.UNFINISHED:
        outcome = RUNNING if exit_code is None else registry.UNFINISHED
        message = None
    text = f"{outcome}: {message}" if message else outcome
    return LabVerdict(
        "backtest",
        slug,
        outcome,
        text,
        message,
        True,
        trial_id=trial.trial_id,
        log_tail=output,
    )


def _sweep_verdicts(conn: duckdb.DuckDBPyConnection, slug: str) -> SweepVerdicts | None:
    try:
        report = sweep_report.sweep_report(conn, slug)
    except (ValueError, LabNotInitialised):
        return None
    if report.state != "complete" or report.verdicts is None:
        return None
    v = report.verdicts
    return SweepVerdicts(
        argmax_slug=v.argmax.slug,
        promote_at_least=report.promote_at_least,
        promote_at_least_met=v.promote_at_least_met,
        retire_below=report.retire_below,
        retire_below_met=v.retire_below_met,
    )


def _run_outcome(row: lab_registry.SweepRunRow, exit_code: int | None) -> str:
    if row.finished_at is None:
        return RUNNING if exit_code is None else ENDED_EARLY
    if row.completed:
        return COMPLETED
    ran = (row.n_ok or 0) + (row.n_failed or 0)
    if ran < row.n_planned:
        return ENDED_EARLY
    return NO_ROW_FAILED if (row.n_failed or 0) > 0 else "ok"


def _sweep_run_verdict(
    conn: duckdb.DuckDBPyConnection,
    slug: str,
    baseline_id: int,
    exit_code: int | None,
    output: str,
) -> LabVerdict:
    sweep = lab_registry.sweep_by_slug(conn, slug)
    rows = (
        lab_registry.sweep_runs_after(conn, sweep.sweep_id, baseline_id)
        if sweep is not None
        else []
    )
    if sweep is None or not rows:
        return _no_row("sweep_run", slug, exit_code, output)
    row = rows[0]
    outcome = _run_outcome(row, exit_code)
    variants = tuple(
        VariantOutcome(t.variant_index, t.slug, t.status, t.message)
        for t in lab_registry.sweep_run_trials(conn, row.sweep_run_id)
    )
    verdicts = _sweep_verdicts(conn, slug) if outcome == COMPLETED else None
    counts = f"{row.n_ok or 0} ok, {row.n_failed or 0} failed of {row.n_planned} planned"
    text = ENDED_EARLY_TEXT if outcome == ENDED_EARLY else outcome
    return LabVerdict(
        "sweep_run",
        slug,
        outcome,
        f"{text} ({counts})",
        output if outcome == ENDED_EARLY and output else None,
        True,
        sweep_run_id=row.sweep_run_id,
        n_planned=row.n_planned,
        n_ok=row.n_ok,
        n_failed=row.n_failed,
        variants=variants,
        verdicts=verdicts,
        log_tail=output,
    )


def verdict(
    conn: duckdb.DuckDBPyConnection,
    *,
    kind: RunKind,
    slug: str,
    baseline_id: int,
    exit_code: int | None,
    output: str,
    now: datetime,
) -> LabVerdict:
    """The verdict of one Lab run (module docstring). `output` is the child's text
    after the page's own scrub; `now` is the caller's clock (kept for the callers
    that show an age; the verdict itself reads no clock). Read-only."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    if kind == "backtest":
        return _trial_verdict(conn, slug, baseline_id, exit_code, output)
    return _sweep_run_verdict(conn, slug, baseline_id, exit_code, output)


@dataclass(frozen=True)
class InFlightItem:
    """One open registry row: a sweep run or an unfinished trial."""

    kind: Literal["sweep_run", "trial"]
    row_id: int
    slug: str
    started_at: datetime
    state: Literal["running", "orphan"]
    remedy: str | None


@dataclass(frozen=True)
class InFlight:
    """What is open in the registry: `running` blocks a new Run, `orphans` do not."""

    running: tuple[InFlightItem, ...]
    orphans: tuple[InFlightItem, ...]

    @property
    def busy(self) -> bool:
        """Whether something live holds the Run slot."""
        return bool(self.running)


def in_flight(conn: duckdb.DuckDBPyConnection, now: datetime, settings: Settings) -> InFlight:
    """The open rows of the registry as of `now` (module docstring). A row started
    at or after `now - ui.lab_orphan_after_hours` is `running`; an older one is an
    `orphan` with its remedy. Read-only."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    require_lab(conn)
    cutoff = now - timedelta(hours=settings.ui.lab_orphan_after_hours)
    items: list[InFlightItem] = []
    slugs = {
        int(sweep_id): str(slug)
        for sweep_id, slug in conn.execute("SELECT sweep_id, slug FROM sweeps").fetchall()
    }
    for run in lab_registry.open_sweep_runs(conn):
        items.append(
            _item(
                "sweep_run", run.sweep_run_id, slugs.get(run.sweep_id, "?"), run.started_at, cutoff
            )
        )
    for trial in registry.list_trials(conn):
        if trial.status == registry.UNFINISHED:
            items.append(_item("trial", trial.trial_id, trial.slug, trial.started_at, cutoff))
    return InFlight(
        running=tuple(i for i in items if i.state == "running"),
        orphans=tuple(i for i in items if i.state == "orphan"),
    )


def _item(
    kind: Literal["sweep_run", "trial"],
    row_id: int,
    slug: str,
    started_at: datetime,
    cutoff: datetime,
) -> InFlightItem:
    live = started_at >= cutoff
    return InFlightItem(
        kind,
        row_id,
        slug,
        started_at,
        "running" if live else "orphan",
        None if live else ORPHAN_REMEDY,
    )

"""The kill switch: its derived state, its writers and the drawdown trigger
(Phase 4 spec req 5; ADR 0010 points 1 and 4; plan T59).

**Derived, never stored.** `derive` is pure over one window's rows: the
switch is engaged when the window's latest `kill_switch` row is `engaged`, or
when any run of the window other than the reading one has no result row, or
ended `halted`, `crashed` or `failed` with no `released` row after both its
`started_at` and its `finished_at`. So a lost write or a crash cannot leave the
switch open. A run with no result row is never cleared by a release, whatever
the timestamps say: `paper resume` closes it as `crashed` before it releases,
and a release stamped by a clock that runs ahead of the crashed run's must not
clear a crash it never saw. Rows and
runs of another window are ignored: a closed window's faulted run never engages
the next one. "Latest" is write order (`event_id`), not `known_at`, because a
row written on the halt path after a `ClockError` carries a real-time stamp
(`store.journal.kill_switch_events_for`). No setting reaches the derivation,
so no config can open an engaged switch.

A reader that does not hold the run lock (`execution.lock`) passes
`lock_free=is_held(...) is False`. While the lock is held, the window's latest
unfinished run is the one in progress: the state reports `run_in_progress`
for it instead of engaging. An older unfinished run still engages, because
only one process can hold the lock. A process that holds the lock itself
(`paper run`, `resume`, `reconcile`, `stop`, `abandon`) passes `lock_free=True`
and never asks `is_held`, which reports its own lock as held and would hide a
real crash; a run also passes its own `run_id` as `reading_run`.

An `engage_kill_switch` override that no `engaged` row cites yet does not
engage the derived state: it takes effect when the next run or phase boundary
calls `engage_from_overrides`, which writes that row (spec req 5).

**Writers.** Each opens its own write chunk (`store.db.open_for_write`, which
retries the store lock for `store.lock_retry_seconds`) and stamps `at`,
`known_at` and `ingested_at` from one reading of the caller's clock. After a
`ClockError` the halt path passes `store.db.utc_now`. The clock is read outside
the write: an exception it raises propagates rather than becoming a
`WriteFailed`. A caller must close its own connections to the store before
calling a writer; an open one in the same process makes the write fail at once.

- `engage` appends one `engaged` row. It returns `WriteFailed` when the store
  cannot be written, rather than raising, so the halt path can deliver
  `kill_switch_write_failed` through the non-store channels and exit
  non-zero. The next run then derives `engaged` from the unfinished run.
- `engage_from_overrides` appends one `engaged` row (source `owner`) per
  `engage_kill_switch` override that no `engaged` row cites yet, citing its
  `override_id`. The read and the writes share one transaction, so an
  override engages exactly once, and a later release does not re-arm it.
- `release` appends the `released` row that ends an engagement. It carries
  the `resume_id`, the reconciliation that passed and the equity the drawdown
  peak resets to. It is the only gate in code before a `released` row, so it
  checks, in one transaction, that: the window is open; the reconciliation is
  this window's latest and `ok`, and not older than the resume; the resume is
  the latest `resume_invocations` row and no `released` row cites it yet; no
  run of the window is unfinished; no `engaged` row of the window was
  written after the resume (its `event_id` above the caller's
  `seen_event_id`: an engagement the owner did not see when resuming needs a
  new resume); the switch is engaged (a release with
  nothing engaged would only reset the drawdown peak) and the new row would
  clear it (a release stamped at or before a faulted run's `finished_at`
  would not); and the peak is a positive finite number. "No `released` row
  cites the resume" spans every window, because resumes carry none. Only
  `paper resume` calls it (T60e), after its own checks.

**Drawdown** (ADR 0010 point 1): at the mark step, ledger equity below the
window's peak by more than the frozen `risk.max_drawdown`, strictly, engages
the switch with source `drawdown`. The peak is `paper_windows.starting_equity`,
reset at each release to the `peak_equity` on the release row; it is not a
running maximum. The trigger fires once per crossing. It is disarmed from its
own `engaged` row until the next release, so a mark that stays below the line
while the switch is engaged adds no second row. A non-finite equity or peak, or
a peak that is not positive, raises `ValueError` rather than reading as "no
drawdown", so the caller's halt path engages the switch instead.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import duckdb

from tradepartner.config import Settings
from tradepartner.store.db import StoreLockedError, open_for_write
from tradepartner.store.journal import (
    JournalNotInitialised,
    KillSwitchRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    append,
    kill_switch_events_for,
    open_window,
    reconciliations_for,
    resume_invocations,
    runs_for,
    unconsumed_kill_switch_overrides,
)

ENGAGED = "engaged"
RELEASED = "released"
SOURCES = ("owner", "fault", "drawdown")
#: Run results that engage the switch; a run with no result row engages too.
FAULTED_RUN_STATUSES = frozenset({"halted", "crashed", "failed"})
_OWNER, _FAULT, _DRAWDOWN = SOURCES
_RECONCILIATION_OK = "ok"
#: What the writers treat as "the store could not be written".
_WRITE_ERRORS = (StoreLockedError, JournalNotInitialised, duckdb.Error, OSError)


@dataclass(frozen=True)
class SwitchState:
    """The derived kill-switch state of one window.

    `causes` names every reason it is engaged, in order: the latest row when it
    is `engaged`, then each faulted or unfinished run. `run_in_progress` is only
    ever set for a reader without the lock. `engaged_row` is that latest row
    (write order) when it is `engaged`, else None: the one definition of "the
    latest `engaged` row" a reader such as the run's `kill_switch` alert uses
    (#699)."""

    engaged: bool
    run_in_progress: bool
    causes: tuple[str, ...]
    engaged_row: KillSwitchRow | None = None


class ReleaseRefused(ValueError):
    """`release` refused: one of its checks failed and nothing was written, so
    the switch stays engaged. A `ValueError`, so callers that catch that keep
    working; `paper resume` catches this type alone and reports a refusal."""


@dataclass(frozen=True)
class WriteFailed:
    """A writer could not reach the store; `error` names the exception."""

    error: str


def derive(
    window: PaperWindowRow,
    kill_switch_rows: Sequence[KillSwitchRow],
    runs: Sequence[PaperRunRow],
    results: Sequence[PaperRunResultRow],
    reading_run: int | None,
    lock_free: bool,
) -> SwitchState:
    """The kill-switch state of `window` (module docstring). `reading_run` is the
    run doing the reading, never counted against itself; `lock_free` is False
    only for a reader that found the run lock held by someone else."""
    window_id = window.window_id
    rows = sorted((r for r in kill_switch_rows if r.window_id == window_id), key=_event_order)
    own_runs = sorted((r for r in runs if r.window_id == window_id), key=_run_order)
    finished = {r.run_id: r for r in results}
    releases = [r.at for r in rows if r.state == RELEASED]

    causes: list[str] = []
    engaged_row = rows[-1] if rows and rows[-1].state == ENGAGED else None
    if engaged_row is not None:
        causes.append(f"kill_switch event {engaged_row.event_id} {ENGAGED} ({engaged_row.source})")

    in_progress = None
    if not lock_free:
        unfinished = [r for r in own_runs if r.run_id not in finished]
        if unfinished and unfinished[-1] is own_runs[-1]:
            in_progress = own_runs[-1].run_id

    for run in own_runs:
        run_id = _run_order(run)
        if run_id in (reading_run, in_progress):
            continue
        result = finished.get(run_id)
        if result is None:
            causes.append(f"run {run_id} unfinished")
        elif _faulted_uncleared(run, result, releases):
            causes.append(f"run {run_id} {result.status}")

    return SwitchState(
        engaged=bool(causes),
        run_in_progress=in_progress is not None,
        causes=tuple(causes),
        engaged_row=engaged_row,
    )


def _faulted_uncleared(
    run: PaperRunRow, result: PaperRunResultRow, releases: Sequence[datetime]
) -> bool:
    """A run that ended faulted with no `released` row after both its
    `started_at` and its `finished_at` (the rule `derive` engages on)."""
    return result.status in FAULTED_RUN_STATUSES and not any(
        at > run.started_at and at > result.finished_at for at in releases
    )


def faulted_runs(
    window: PaperWindowRow,
    kill_switch_rows: Sequence[KillSwitchRow],
    runs: Sequence[PaperRunRow],
    results: Sequence[PaperRunResultRow],
) -> tuple[int, ...]:
    """The window's runs that ended `halted`, `crashed` or `failed` and that no
    release has cleared, by `derive`'s own rule: the runs a release would clear.
    For a lock holder (resume): it applies none of `derive`'s `reading_run` or
    run-in-progress exclusions, and leaves out unfinished runs, which resume
    closes `crashed` first."""
    window_id = window.window_id
    releases = [r.at for r in kill_switch_rows if r.window_id == window_id and r.state == RELEASED]
    finished = {r.run_id: r for r in results}
    return tuple(
        _run_order(run)
        for run in sorted((r for r in runs if r.window_id == window_id), key=_run_order)
        if (result := finished.get(_run_order(run))) is not None
        and _faulted_uncleared(run, result, releases)
    )


def _append(conn: duckdb.DuckDBPyConnection, row: KillSwitchRow) -> int:
    event_id = append(conn, row)
    assert event_id is not None
    return event_id


def _event_order(row: KillSwitchRow) -> int:
    if row.event_id is None:
        raise ValueError("a kill_switch row without an event_id: pass rows read from the journal")
    return row.event_id


def _run_order(run: PaperRunRow) -> int:
    if run.run_id is None:
        raise ValueError("a paper_runs row without a run_id: pass rows read from the journal")
    return run.run_id


def engage(
    settings: Settings,
    clock: Callable[[], datetime],
    *,
    window_id: int,
    source: str,
    fault_type: str | None = None,
    reason: str | None = None,
    run_id: int | None = None,
    override_id: int | None = None,
) -> int | WriteFailed:
    """Append one `engaged` row and return its `event_id`, or `WriteFailed` when
    the store stays locked past `store.lock_retry_seconds` or cannot be written.
    Raises `ValueError`, before any write, for a source outside `SOURCES` or a
    `fault` without its `fault_type`."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    if source == _FAULT and not fault_type:
        raise ValueError("a fault engagement needs its fault_type")
    now = clock()
    row = KillSwitchRow(
        window_id=window_id,
        at=now,
        state=ENGAGED,
        source=source,
        fault_type=fault_type,
        reason=reason,
        run_id=run_id,
        override_id=override_id,
        known_at=now,
        ingested_at=now,
    )
    try:
        with open_for_write(settings) as conn:
            return _append(conn, row)
    except _WRITE_ERRORS as exc:
        return WriteFailed(f"{type(exc).__name__}: {exc}")


def engage_from_overrides(
    settings: Settings, clock: Callable[[], datetime], *, window_id: int, run_id: int | None
) -> list[int] | WriteFailed:
    """Append one `engaged` row per unconsumed `engage_kill_switch` override of
    the window, citing it, and return their `event_id`s (empty when there is
    none), or `WriteFailed` when the store cannot be written."""
    now = clock()
    try:
        with open_for_write(settings) as conn:
            event_ids = []
            for override in unconsumed_kill_switch_overrides(conn, window_id):
                event_ids.append(
                    _append(
                        conn,
                        KillSwitchRow(
                            window_id=window_id,
                            at=now,
                            state=ENGAGED,
                            source=_OWNER,
                            reason=override.reason,
                            run_id=run_id,
                            override_id=override.override_id,
                            known_at=now,
                            ingested_at=now,
                        ),
                    )
                )
    except _WRITE_ERRORS as exc:
        return WriteFailed(f"{type(exc).__name__}: {exc}")
    return event_ids


def release(
    settings: Settings,
    clock: Callable[[], datetime],
    *,
    window_id: int,
    resume_id: int,
    reconciliation_id: int,
    peak_equity: float,
    seen_event_id: int,
) -> int:
    """Append the `released` row (source `owner`) and return its `event_id`.
    `seen_event_id` is the highest `kill_switch` `event_id` of the window when
    the resume row was written, in the same transaction (0 when there was none).
    Raises `ReleaseRefused` (a `ValueError`), writing nothing, when any check in
    the module docstring fails. Store errors propagate: the owner's `paper resume` reports them."""
    if not (math.isfinite(peak_equity) and peak_equity > 0):
        raise ReleaseRefused(f"peak_equity must be positive and finite, got {peak_equity!r}")
    if seen_event_id < 0:
        raise ReleaseRefused(f"seen_event_id must not be negative, got {seen_event_id!r}")
    seen = seen_event_id
    now = clock()
    with open_for_write(settings) as conn:
        window = open_window_of(conn, window_id)
        if window is None:
            raise ReleaseRefused(f"window {window_id} is not the open window")
        reconciliations = reconciliations_for(conn, window_id)
        reconciliation = next(
            (r for r in reconciliations if r.reconciliation_id == reconciliation_id),
            None,
        )
        if reconciliation is None:
            raise ReleaseRefused(f"reconciliation {reconciliation_id} is not in window {window_id}")
        if reconciliation.status != _RECONCILIATION_OK:
            raise ReleaseRefused(
                f"reconciliation {reconciliation_id} is {reconciliation.status!r}, not ok"
            )
        latest = max(r.reconciliation_id or 0 for r in reconciliations)
        if reconciliation_id != latest:
            raise ReleaseRefused(
                f"reconciliation {reconciliation_id} is not the window's latest ({latest})"
            )
        resumes = resume_invocations(conn)
        resume = next((r for r in resumes if r.resume_id == resume_id), None)
        if resume is None:
            raise ReleaseRefused(f"no resume_invocations row {resume_id}")
        if resume_id != max(r.resume_id or 0 for r in resumes):
            raise ReleaseRefused(f"resume {resume_id} is not the latest resume")
        # Resumes carry no window, so a release in any window consumes one.
        cited = conn.execute(
            "SELECT window_id FROM kill_switch WHERE state = ? AND resume_id = ? LIMIT 1",
            [RELEASED, resume_id],
        ).fetchone()
        if cited is not None:
            raise ReleaseRefused(
                f"resume {resume_id} has already released the switch (window {cited[0]})"
            )
        rows = kill_switch_events_for(conn, window_id)
        if reconciliation.at < resume.at:
            raise ReleaseRefused(
                f"reconciliation {reconciliation_id} is older than resume {resume_id}"
            )
        # An engagement written after the resume row is one the owner did not
        # see when resuming: only a new resume may release it. Write order
        # (`event_id`), not stamps: a halt row after a `ClockError` carries
        # `utc_now`, and two clocks can disagree.
        later = [str(r.event_id) for r in rows if r.state == ENGAGED and _event_order(r) > seen]
        if later:
            raise ReleaseRefused(
                f"kill_switch event(s) {', '.join(later)} engaged after resume "
                f"{resume_id}: resume again to release them"
            )
        runs = runs_for(conn, window_id)
        unfinished = [str(r.run.run_id) for r in runs if r.result is None]
        if unfinished:
            raise ReleaseRefused(
                f"run(s) {', '.join(unfinished)} unfinished: close them as crashed first"
            )
        run_rows = [r.run for r in runs]
        results = [r.result for r in runs if r.result is not None]
        state = derive(window, rows, run_rows, results, reading_run=None, lock_free=True)
        if not state.engaged:
            raise ReleaseRefused(f"the kill switch of window {window_id} is not engaged")
        row = KillSwitchRow(
            window_id=window_id,
            at=now,
            state=RELEASED,
            source=_OWNER,
            resume_id=resume_id,
            reconciliation_id=reconciliation_id,
            peak_equity=peak_equity,
            known_at=now,
            ingested_at=now,
        )
        last = max((_event_order(r) for r in rows), default=0)
        after = derive(
            window,
            [*rows, replace(row, event_id=last + 1)],
            run_rows,
            results,
            reading_run=None,
            lock_free=True,
        )
        if after.engaged:
            raise ReleaseRefused(
                f"a release at {now.isoformat()} would not clear the switch: "
                f"{'; '.join(after.causes)}"
            )
        return _append(conn, row)


def open_window_of(conn: duckdb.DuckDBPyConnection, window_id: int) -> PaperWindowRow | None:
    """Window `window_id` when it is its book's open window, else None (ADR 0017
    B.2 and B.5: another book's windows are never read, so another book's open
    window neither hides nor stands in for this one). `JournalIntegrityError`
    when the book has more than one open window, as `open_window`."""
    row = conn.execute(
        "SELECT book_id FROM paper_windows WHERE window_id = ?", [window_id]
    ).fetchone()
    if row is None:
        return None
    window = open_window(conn, str(row[0]))
    return window if window is not None and window.window_id == window_id else None


def drawdown_peak(window: PaperWindowRow, kill_switch_rows: Sequence[KillSwitchRow]) -> float:
    """The drawdown peak: the `peak_equity` of the window's last `released` row,
    else its `starting_equity`. Raises `ValueError` when that value is missing,
    not finite or not positive, rather than fall back to another peak."""
    releases = [
        r
        for r in sorted(kill_switch_rows, key=_event_order)
        if r.window_id == window.window_id and r.state == RELEASED
    ]
    peak = releases[-1].peak_equity if releases else window.starting_equity
    if peak is None or not (math.isfinite(peak) and peak > 0):
        raise ValueError(f"window {window.window_id} has no usable drawdown peak: {peak!r}")
    return peak


def drawdown_armed(window_id: int, kill_switch_rows: Sequence[KillSwitchRow]) -> bool:
    """False from the window's `drawdown` engagement until the next release, so
    the trigger fires once per crossing."""
    armed = True
    for row in sorted(kill_switch_rows, key=_event_order):
        if row.window_id != window_id:
            continue
        if row.state == RELEASED:
            armed = True
        elif row.source == _DRAWDOWN:
            armed = False
    return armed


def drawdown_check(equity: float, peak: float, max_drawdown: float, *, armed: bool) -> bool:
    """True when the trigger fires: `armed` and `equity` below `peak` by more
    than `max_drawdown` (the window's frozen `risk.max_drawdown`, a fraction of
    the peak), strictly. A drop equal to the threshold up to float rounding
    (`math.isclose`'s default relative tolerance) is at the threshold, so an
    equity of exactly `peak * (1 - max_drawdown)` never fires however it was
    computed. Raises `ValueError` for a non-finite `equity` or `peak`, or a peak
    that is not positive."""
    if not (math.isfinite(equity) and math.isfinite(peak) and peak > 0):
        raise ValueError(f"drawdown needs finite equity and a positive peak: {equity!r}, {peak!r}")
    drop, threshold = peak - equity, peak * max_drawdown
    return armed and drop > threshold and not math.isclose(drop, threshold)

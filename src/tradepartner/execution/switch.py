"""The kill switch: its derived state, its writers and the drawdown trigger
(Phase 4 spec req 5; ADR 0010 points 1 and 4; plan T59).

**Derived, never stored.** `derive` is pure over one window's rows: the
switch is engaged when the window's latest `kill_switch` row is `engaged`, or
when any run of the window other than the reading one ended `halted`, `crashed`
or `failed`, or has no result row, and no `released` row came after that run's
`started_at`. So a lost write or a crash cannot leave the switch open. Rows and
runs of another window are ignored: a closed window's faulted run never engages
the next one. "Latest" is write order (`event_id`), not `known_at`, because a
row written on the halt path after a `ClockError` carries a real-time stamp
(`store.journal.kill_switch_events_for`). No setting reaches the derivation,
so no config can open an engaged switch.

A reader that does not hold the run lock (`execution.lock`) passes
`lock_free=is_held(...) is False`. While the lock is held, the window's latest
unfinished run is the one in progress: the state reports `run_in_progress`
for it instead of engaging. An older unfinished run still engages, because
only one process can hold the lock. A run that holds the lock itself passes
`lock_free=True` and its own `run_id` as `reading_run`.

**Writers.** Each opens its own write chunk (`store.db.open_for_write`, which
retries the store lock for `store.lock_retry_seconds`) and stamps `at`,
`known_at` and `ingested_at` from one reading of the caller's clock. After a
`ClockError` the halt path passes `store.db.utc_now`.

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
  peak resets to. It refuses a reconciliation that is not `ok` or not this
  window's, and a `resume_id` with no `resume_invocations` row. Only `paper
  resume` calls it (T60e), after its own checks.

**Drawdown** (ADR 0010 point 1): at the mark step, ledger equity below the
window's peak by more than the frozen `risk.max_drawdown`, strictly, engages
the switch with source `drawdown`. The peak is `paper_windows.starting_equity`,
reset at each release to the `peak_equity` on the release row; it is not a
running maximum. The trigger fires once per crossing. It is disarmed from its
own `engaged` row until the next release, so a mark that stays below the line
while the switch is engaged adds no second row.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
    reconciliations_for,
    resume_invocations,
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
    ever set for a reader without the lock."""

    engaged: bool
    run_in_progress: bool
    causes: tuple[str, ...]


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
    if rows and rows[-1].state == ENGAGED:
        causes.append(f"kill_switch event {rows[-1].event_id} {ENGAGED} ({rows[-1].source})")

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
            fault = "unfinished"
        elif result.status in FAULTED_RUN_STATUSES:
            fault = result.status
        else:
            continue
        if not any(at > run.started_at for at in releases):
            causes.append(f"run {run_id} {fault}")

    return SwitchState(
        engaged=bool(causes), run_in_progress=in_progress is not None, causes=tuple(causes)
    )


def _append(conn: duckdb.DuckDBPyConnection, row: KillSwitchRow) -> int:
    # `journal.JournalRow` declares `known_at`/`ingested_at` as settable members,
    # which no frozen row type satisfies under mypy; `append` itself only reads them.
    event_id = append(conn, row)  # type: ignore[arg-type]
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
    try:
        with open_for_write(settings) as conn:
            now = clock()
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
) -> int:
    """Append the `released` row (source `owner`) and return its `event_id`.
    Raises `ValueError`, writing nothing, unless `reconciliation_id` is an `ok`
    reconciliation of this window and `resume_id` a journaled resume. Store
    errors propagate: the owner's `paper resume` reports them."""
    with open_for_write(settings) as conn:
        reconciliation = next(
            (
                r
                for r in reconciliations_for(conn, window_id)
                if r.reconciliation_id == reconciliation_id
            ),
            None,
        )
        if reconciliation is None:
            raise ValueError(f"reconciliation {reconciliation_id} is not in window {window_id}")
        if reconciliation.status != _RECONCILIATION_OK:
            raise ValueError(
                f"reconciliation {reconciliation_id} is {reconciliation.status!r}, not ok"
            )
        if all(r.resume_id != resume_id for r in resume_invocations(conn)):
            raise ValueError(f"no resume_invocations row {resume_id}")
        now = clock()
        return _append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=now,
                state=RELEASED,
                source=_OWNER,
                resume_id=resume_id,
                reconciliation_id=reconciliation_id,
                peak_equity=peak_equity,
                known_at=now,
                ingested_at=now,
            ),
        )


def drawdown_peak(window: PaperWindowRow, kill_switch_rows: Sequence[KillSwitchRow]) -> float:
    """The drawdown peak: the `peak_equity` of the window's last `released` row,
    else its `starting_equity`."""
    releases = [
        r
        for r in sorted(kill_switch_rows, key=_event_order)
        if r.window_id == window.window_id and r.state == RELEASED
    ]
    if releases and releases[-1].peak_equity is not None:
        return releases[-1].peak_equity
    return window.starting_equity


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
    computed."""
    drop, threshold = peak - equity, peak * max_drawdown
    return armed and drop > threshold and not math.isclose(drop, threshold)

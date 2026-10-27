"""`tradepartner action-retract`: withdraw one stored corporate action the
source has withdrawn, or reinstate it (#2092; data-foundation spec amendment
2026-10-20 (#2092), plan T262).

Alpaca sends no cancel for an announcement it withdraws, and an id absent on
a refetch is not a withdrawal (`ingest._add_actions`), so nothing else ever
cancels one: a split stored for ex-date S and then withdrawn halts every
reconciliation from S on. The owner decides it was withdrawn and runs this.

- **Point-in-time.** The withdrawal is req 5's `cancelled` revision: the
  identity's latest revision with `cancelled = TRUE`, `known_at = ingested_at
  =` the clock, every other value carried forward. A read at any `t` before
  the clock returns exactly what it returned before the run, so no trial goes
  stale and no read an open paper window made moves. It is never back-dated,
  so session S, whose facts are cut at close(S-1), stays halted on a re-run;
  the first session whose previous close is after the clock reconciles.
- **The identity** is `(security_id, source_action_id)`, req 5's id identity
  (a spin-off's id is its parent). An id-less identity is not taken.
- **Only what a dry run showed.** A real run takes the dry run's latest
  `known_at` and refuses, writing nothing, when the latest revision is another
  one, is already in the asked state, is a cancel this command did not write
  (for a reinstatement), or is known at or after the clock.
- **Recorded.** One `ingestion_runs` row in the same transaction: the action's
  source, mode `ingest.ACTION_RETRACT_MODE`, status `repaired` (never `ok`, so
  never a fresh ingest), `started_at` the new row's `ingested_at`, and a message
  with the identity, the verb and the reason. `ingest._add_actions` reads these
  rows to keep the withdrawal when the source still serves the same values.
- **`reinstate`** writes the same row with `cancelled = FALSE`: the only way
  back for a withdrawal whose values the source serves again.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb

from tradepartner.config import Settings
from tradepartner.ingest import ACTION_RETRACT_MODE, SourceRun, _read, _write_run
from tradepartner.repair import REPAIRED, RepairRefused
from tradepartner.store.db import insert_row, open_for_write, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc


@dataclass(frozen=True)
class ActionRetraction:
    """What one `retract_action` call found and wrote (or would write)."""

    security_id: str
    source_action_id: str
    #: True for a reinstatement, False for a withdrawal.
    reinstate: bool
    #: The latest revision's stored `cancelled` before the run.
    cancelled: bool
    action_type: str
    ex_date: date
    ratio_or_amount: float
    #: The `known_at` of the identity's latest revision before the run.
    latest_known_at: datetime
    #: The clock: the new row's `known_at` and `ingested_at`.
    at: datetime
    dry_run: bool

    def summary(self) -> str:
        """One line for the terminal and the run row."""
        verbs = (
            ("would reinstate", "reinstated") if self.reinstate else ("would withdraw", "withdrew")
        )
        verb = verbs[0] if self.dry_run else verbs[1]
        return (
            f"{verb} {self.security_id} {self.action_type} {self.source_action_id} "
            f"ex-date {self.ex_date.isoformat()} ratio {self.ratio_or_amount!r} "
            f"(latest revision known at {self.latest_known_at.isoformat()}, "
            f"cancelled {self.cancelled}) "
            f"from {self.at.isoformat()}"
        )


def _revisions(
    conn: duckdb.DuckDBPyConnection, security_id: str, source_action_id: str
) -> list[dict[str, Any]]:
    cursor = conn.execute(
        "SELECT * FROM corporate_actions WHERE security_id = ? AND source_action_id = ? "
        "ORDER BY known_at",
        [security_id, source_action_id],
    )
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _written_by_command(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> bool:
    found = conn.execute(
        "SELECT count(*) FROM ingestion_runs WHERE mode = ? AND started_at = ?",
        [ACTION_RETRACT_MODE, row["ingested_at"]],
    ).fetchone()
    return found is not None and int(found[0]) > 0


def _refusal(
    conn: duckdb.DuckDBPyConnection,
    latest: dict[str, Any],
    *,
    reinstate: bool,
    at: datetime,
    expect_known_at: datetime | None,
) -> str | None:
    known = latest["known_at"]
    if expect_known_at is not None and known != expect_known_at:
        return (
            f"the latest revision is known at {known.isoformat()}, not the dry run's "
            f"{expect_known_at.isoformat()}; run --dry-run again"
        )
    if not reinstate and latest["cancelled"]:
        return "the action is already withdrawn"
    if reinstate and not latest["cancelled"]:
        return "the action is live; there is nothing to reinstate"
    if reinstate and not _written_by_command(conn, latest):
        return "its cancel was not written by action-retract; ingest owns it"
    if at <= known:
        return f"a revision at {at.isoformat()} would be back-dated to {known.isoformat()}"
    return None


def retract_action(
    settings: Settings,
    *,
    security_id: str,
    source_action_id: str,
    reason: str,
    reinstate: bool = False,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect_known_at: datetime | None = None,
) -> ActionRetraction:
    """Withdraw (or, with `reinstate`, reinstate) the stored corporate action
    `(security_id, source_action_id)` from the clock on, recording the run
    (module docstring); with `dry_run`, read on a read-only connection and
    write nothing.

    Raises `ValueError` for a blank identity or reason, or a real run without
    `expect_known_at` (the dry run's `latest_known_at`); `RepairRefused`,
    writing nothing, when no row has the identity or the module docstring's
    refusals apply; `StoreLockedError` like ingest when the store stays locked.
    """
    if not security_id.strip():
        raise ValueError("pass the security id")
    if not source_action_id.strip():
        raise ValueError("pass the source action id (an id-less action is not taken)")
    if not reason.strip():
        raise ValueError("pass a reason: why the action is withdrawn or reinstated")
    if not dry_run and expect_known_at is None:
        raise ValueError("a real run writes only what a dry run showed: pass expect_known_at")
    expected = (
        None
        if expect_known_at is None
        else ensure_tz_aware_utc(expect_known_at, field_name="expect_known_at")
    )
    at = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        rows = _revisions(conn, security_id, source_action_id)
        if not rows:
            raise RepairRefused(
                f"no stored corporate action {security_id} {source_action_id}; nothing written"
            )
        latest = rows[-1]
        why = _refusal(conn, latest, reinstate=reinstate, at=at, expect_known_at=expected)
        if why is not None:
            raise RepairRefused(f"{why}; nothing written")
        result = ActionRetraction(
            security_id=security_id,
            source_action_id=source_action_id,
            reinstate=reinstate,
            cancelled=bool(latest["cancelled"]),
            action_type=str(latest["action_type"]),
            ex_date=latest["ex_date"],
            ratio_or_amount=float(latest["ratio_or_amount"]),
            latest_known_at=latest["known_at"],
            at=at,
            dry_run=dry_run,
        )
        if not dry_run:
            insert_row(
                conn,
                "corporate_actions",
                {**latest, "cancelled": not reinstate, "known_at": at, "ingested_at": at},
            )
            message = f"{result.summary()}; reason: {reason.strip()}"
            run = SourceRun(str(latest["source"]), REPAIRED, 1, "", message)
            _write_run(conn, uuid.uuid4().hex, at, clock(), run, ACTION_RETRACT_MODE)
    return result

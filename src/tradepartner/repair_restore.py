"""`tradepartner repair-restore`: put retracted rows back, row by row or by release
(ADR 0029, #3275, plan T391).

A repair that retracts rows moves them into `retracted_rows` (`store.retracted_rows.
retract`) instead of deleting them; this is the owner's way back when the judgment
that moved them is overturned. It is a thin, guarded wrapper over
`store.retracted_rows.restore`, which does the move and the key checks.

- **Select** one release's unrestored rows (`from_release`), or one logical key
  (`table` and `key`, as `retract` takes it), or the key within a release.
- **Its own release.** A restore changes what a backtest reads, so it is a
  row-changing repair like any other (runbook `data-releases.md`, rule 1): it runs
  only inside the open data release `release` (`repair.release_refusal`), and that
  release is not the one that retracted the rows (a restore is not the retraction
  undone inside its own record). `tradepartner decision data-release close` then
  names the restored sessions, which `RestoreRepaired.close_command` prints, so the
  trials that read them go stale.
- **Dry run first.** `dry_run` checks the selected rows on a read-only connection:
  it lists the rows and writes nothing. Insert casts and table constraints are
  checked by the real run. A real run writes only the
  dry run's row count (`expect`); any other count rolls back.
- **Refusals** (`RepairRefused`, nothing written): no open release of that name; a
  logical key still held by any revision, or a ticker-session held by another id
  (`RestoreRefused`, which names the offender); nothing to restore; an open paper
  window (`repair.refuse_inside_open_window`: a window's runs read the store, so
  any non-empty restore waits for it to close). The release check applies to a
  real run; row and paper-window refusals apply to the dry run too.
- **No look-ahead.** Each row returns with its original `known_at`, so a read at any
  `t` sees a restored row only if it saw the row before it was retracted.
- **Recorded.** `retracted_rows.restored_at` is the clock, and one `ingestion_runs`
  row (mode `repair`, status `repaired`) names the release, the count and the
  sessions.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import is_session, next_session, previous_session
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _write_run
from tradepartner.repair import (
    REPAIR,
    REPAIRED,
    RepairRefused,
    refuse_inside_open_window,
    release_refusal,
)
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.store.retracted_rows import (
    RestoreRefused,
    preview_restore,
    restore,
    select_restore_ids,
)
from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")

#: The `row_values` field that dates each source table's row for the close.
_SESSION_FIELD = {
    "prices_daily": "session",
    "corporate_actions": "ex_date",
    "listings": "valid_from",
    "delistings": "effective_on",
}


@dataclass(frozen=True)
class RestoredRow:
    """One row `restore` put back (or would)."""

    table: str
    #: The row's logical key as the table's own types spell it.
    key: tuple[str, ...]
    #: The release that retracted it.
    retracted_in: str
    #: The row's own `known_at`, unchanged.
    known_at: str
    #: The session the row is dated on: a bar's session, an action's ex-date, a
    #: listing's `valid_from`, a delisting's `effective_on`.
    session: date
    #: The New York date of `known_at`: a row can be known before its session
    #: (a delisting filed days before `effective_on`), and trials read it from then.
    known_date: date


@dataclass(frozen=True)
class RestoreRepaired:
    """What one `repair_restore` call found and, unless `dry_run`, wrote."""

    release: str
    rows: tuple[RestoredRow, ...]
    dry_run: bool

    @property
    def sessions_from(self) -> date:
        """The first trading session a restored row can be read from: the earlier
        of its session and the date it was known, snapped back to a session."""
        day = min(min(r.session, r.known_date) for r in self.rows)
        return day if is_session(day) else previous_session(day)

    @property
    def sessions_to(self) -> date:
        """The last restored session, snapped forward to a trading session."""
        day = max(r.session for r in self.rows)
        return day if is_session(day) else next_session(day)

    def summary(self) -> str:
        """One line for the terminal and the run row."""
        verb = "would restore" if self.dry_run else "restored"
        sources = ", ".join(sorted({r.retracted_in for r in self.rows}))
        tables = ", ".join(f"{t} {n}" for t, n in self._by_table())
        return (
            f"{verb} {len(self.rows)} retracted rows of release {sources} into release "
            f"{self.release} ({tables}); sessions {self.sessions_from} to {self.sessions_to}"
        )

    def _by_table(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.table] = counts.get(r.table, 0) + 1
        return sorted(counts.items())

    def lines(self) -> list[str]:
        """One line per row, for the dry run's review."""
        return [
            f"  {r.table} {', '.join(r.key)} on {r.session} known {r.known_at} "
            f"(retracted in {r.retracted_in})"
            for r in self.rows
        ]

    def close_command(self) -> str:
        """The command that closes the release naming the restored sessions, so
        trials that read them go stale."""
        return (
            f"tradepartner decision data-release close --name {self.release} "
            f"--from {self.sessions_from} --to {self.sessions_to}"
        )


def _restored_rows(conn: duckdb.DuckDBPyConnection, ids: Sequence[int]) -> list[RestoredRow]:
    """The selected retractions, read in their original id order."""
    out: list[RestoredRow] = []
    rows = conn.execute(
        "SELECT table_name, row_key, release, row_values FROM retracted_rows "
        "WHERE retraction_id IN (SELECT unnest(?::BIGINT[])) ORDER BY retraction_id",
        [list(ids)],
    ).fetchall()
    for table, key, retracted_in, values in rows:
        out.append(
            RestoredRow(
                table=str(table),
                key=tuple(str(k) for k in key),
                retracted_in=str(retracted_in),
                known_at=str(values["known_at"]),
                session=date.fromisoformat(str(values[_SESSION_FIELD[str(table)]])),
                known_date=ensure_tz_aware_utc(
                    datetime.fromisoformat(str(values["known_at"])), field_name="known_at"
                )
                .astimezone(_NEW_YORK)
                .date(),
            )
        )
    return out


def repair_restore(
    settings: Settings,
    *,
    release: str,
    from_release: str | None = None,
    table: str | None = None,
    key: Sequence[Any] | None = None,
    expect: int | None = None,
    dry_run: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> RestoreRepaired:
    """Restore retracted rows in one transaction inside the open data release
    `release` and record the run; with `dry_run`, preview on a read-only connection
    (module docstring).

    Raises `ValueError` for a bad call (no `release`, no selection, a real run
    without `expect`) and `RepairRefused`, with nothing written, for everything the
    module docstring lists."""
    if not release or not release.strip():
        raise ValueError("a restore names its data release: pass release")
    if not dry_run and expect is None:
        raise ValueError("a restore writes only the count of a dry run: pass expect")
    if from_release is None and (table is None or key is None):
        raise ValueError("name a release (from_release), or a table and a key, to restore")
    if from_release is not None and from_release == release:
        raise RepairRefused(
            f"release {release!r} retracted these rows: a restore runs in its own release, "
            "not the one that retracted them; nothing restored"
        )
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    if dry_run:
        with open_read_only(settings) as conn:
            try:
                ids = preview_restore(conn, release=from_release, table=table, key=key)
            except RestoreRefused as exc:
                raise RepairRefused(str(exc)) from None
            rows = _restored_rows(conn, ids)
            _refuse_own_release(rows, release)
            refuse_inside_open_window(conn, [min(r.session, r.known_date) for r in rows])
            return RestoreRepaired(release=release, rows=tuple(rows), dry_run=True)
    with open_for_write(settings) as conn:
        if why := release_refusal(conn, release):
            raise RepairRefused(f"{why}; nothing restored")
        try:
            ids = select_restore_ids(conn, from_release, table, key)
            restore(conn, release=from_release, table=table, key=key, at=started)
        except RestoreRefused as exc:
            raise RepairRefused(str(exc)) from None
        rows = _restored_rows(conn, ids)
        _refuse_own_release(rows, release)
        refuse_inside_open_window(conn, [min(r.session, r.known_date) for r in rows])
        result = RestoreRepaired(release=release, rows=tuple(rows), dry_run=False)
        if len(rows) != expect:
            raise RepairRefused(
                f"found {len(rows)} rows, not the expected {expect}; "
                "nothing restored, run --dry-run again"
            )
        run = SourceRun("edgar", REPAIRED, 0, "", result.summary())
        _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


def _refuse_own_release(rows: Sequence[RestoredRow], release: str) -> None:
    if any(r.retracted_in == release for r in rows):
        raise RepairRefused(
            f"release {release!r} retracted these rows: a restore runs in its own "
            "release, not the one that retracted them; nothing restored"
        )

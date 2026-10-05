"""`tradepartner master-retract`: withdraw the stored master rows the current
rules no longer derive (#859; `store.retraction`).

An EDGAR ingest adds what its build derives and never touches a key the
build stopped producing, so a row an earlier rule wrote by mistake stays
live (#826's WillScot successor `0001647088@2020-08-10`, which #835's rules
no longer make). This command builds the master exactly as an EDGAR ingest
would (the same fetch pass, `ingest._prefetch`, then `build_master` at the
run's clock) and lists every stored live EDGAR `securities` or `listings`
row whose key that build does not derive.

- **Dry run by default.** It reads on a read-only connection and changes
  nothing; the terminal lists every row found. A read-only connection
  never migrates, so on a store below schema version 11 it refuses and
  names the ingest that migrates it.
- **`--apply` writes only what a dry run showed.** It takes the dry run's
  row count and refuses, writing nothing, on any other count. Then, in one
  transaction: a retraction of each row (`store.retraction.retraction`:
  `retracted = TRUE`, known at the run), one `ingestion_runs` row (source
  `edgar`, mode `retract`, status `retracted`, never `ok`, so it never reads
  as a fresh ingest), and the rows it retracted in `master_underived` under
  that run, so health's `underived_master_rows` shows none still live.
- **Fails closed.** A fetch pass that fails (a validation failure, the
  failure policy's check) refuses the run: a build from incomplete answers
  would call valid rows underived. Failures are never recorded here (the
  ingest's job), so a dry run and an apply see the same source state. A
  build that derives no security (an empty answer) refuses too.
- **Point in time.** A retraction is a revision, not a delete: an as-of read
  at T before the run returns what it did; a trial logged before it
  reproduces. Bars filed under a retracted successor id are not touched:
  `repair-resolution` (#819) removes them once its resolver no longer
  assigns them, and `ingest --backfill --fill-holes` (#831) fetches the
  corrected security's missing bars.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

import duckdb

from tradepartner.adapters.filings import FilingSource
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _prefetch, _read, _Recorded, _write_run
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.master import build_master
from tradepartner.store.retraction import (
    EDGAR,
    RETRACT,
    RETRACTED,
    Underived,
    record_underived,
    stored_underived,
    write_retractions,
)
from tradepartner.store.schema import CURRENT_SCHEMA_VERSION, _max_version, init_schema
from tradepartner.timeutil import ensure_tz_aware_utc


class RetractRefused(RuntimeError):
    """Nothing was written: the fetch pass failed, the store is below version
    11 (dry run), or the apply found another count than the dry run's."""


@dataclass(frozen=True)
class RetractResult:
    """What `master_retract` found and, unless `dry_run`, retracted."""

    found: tuple[Underived, ...]
    at: datetime
    dry_run: bool

    @property
    def rows(self) -> int:
        """How many stored rows the build no longer derives."""
        return len(self.found)

    def summary(self) -> str:
        """One line: the count by table and the run's instant."""
        by_table = {
            table: sum(1 for u in self.found if u.table == table)
            for table in ("securities", "listings")
        }
        verb = "would retract" if self.dry_run else "retracted"
        return (
            f"{verb} {self.rows} rows ({by_table['securities']} securities, "
            f"{by_table['listings']} listings) known at {self.at.isoformat()}"
        )

    def lines(self) -> tuple[str, ...]:
        """One line per row found."""
        return tuple(item.describe() for item in self.found)


def master_retract(
    settings: Settings,
    *,
    filings: FilingSource,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = True,
    expect: int | None = None,
) -> RetractResult:
    """Find the stored master rows the build as of `clock()` no longer
    derives and, unless `dry_run`, retract them at that instant and record
    the run (module docstring). A real run requires `expect`, the dry run's
    row count, and raises `RetractRefused` on any other count before writing.
    Raises `StoreLockedError` like ingest when the store stays locked."""
    if not dry_run and expect is None:
        raise ValueError("an apply retracts only the count of a dry run: pass expect")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    recorded = _Recorded(filings)
    try:
        _prefetch(recorded, settings, dry_run=True)
    except Exception as exc:  # any fetch or validation failure: nothing is judged
        raise RetractRefused(
            f"the EDGAR fetch pass failed, nothing judged: {type(exc).__name__}: {exc}"
        ) from exc
    at = ensure_tz_aware_utc(clock(), field_name="clock()")
    build = build_master(recorded, settings, ingested_at=at)
    if not any(not row["benchmark"] for row in build.securities):
        raise RetractRefused(
            "the EDGAR build derives no security: an empty answer would call every "
            "stored row underived; nothing judged"
        )
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        if dry_run and _max_version(conn) != CURRENT_SCHEMA_VERSION:
            raise RetractRefused(
                f"the store is not at schema version {CURRENT_SCHEMA_VERSION} yet: run "
                "`tradepartner ingest --source edgar` once to migrate it, then the dry run"
            )
        if not dry_run:
            init_schema(conn)
        found = stored_underived(conn, build, at)
        result = RetractResult(found=found, at=at, dry_run=dry_run)
        if not dry_run:
            if result.rows != expect:
                raise RetractRefused(
                    f"found {result.rows} rows, not the expected {expect}; nothing "
                    "retracted, run the dry run again"
                )
            write_retractions(conn, found, at)
            run_id = uuid.uuid4().hex
            record_underived(conn, run_id, at, found)
            run = SourceRun(EDGAR, RETRACTED, result.rows, "", result.summary())
            _write_run(conn, run_id, started, clock(), run, RETRACT)
    return result

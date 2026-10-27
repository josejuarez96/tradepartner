"""`tradepartner master-retract`: withdraw the stored master rows the current
rules no longer derive (#859; `store.retraction`).

An EDGAR ingest adds what its build derives and never touches a key the
build stopped producing, so a row an earlier rule wrote by mistake stays
live (#826's WillScot successor `0001647088@2020-08-10`, which #835's rules
no longer make). This command builds the master exactly as an EDGAR ingest
would (the same fetch pass, `ingest._prefetch`, then `build_master`) and
lists every stored live EDGAR `filing` row of `securities` or `listings`
whose key that build does not derive (`store.retraction.underived`), and
every live `successor_chains` row no entry derives at this run
(`store.retraction.underived_chains`, #821): `build_chains` runs on the same
build, so an entry that is refused (`spac` predecessor, lost evidence, a
successor listing that no longer matches) leaves its row underived, as the
ingest's chain writer skips it. A chain row is `[traded]` when the journal
names its predecessor or successor.

- **Dry run by default.** It reads on a read-only connection and changes
  nothing. The terminal lists every row found, marks a row of a security
  the journal names in an order, adjustment, daily position or lot, in any
  window (`[traded]`), names the CIKs not judged, and
  ends with a digest of the set. A read-only connection never migrates, so
  on a store below schema version 11 it refuses and names the ingest that
  migrates it.
- **`--apply` writes only the set a dry run showed.** It takes the dry
  run's row count and digest (a hash of the sorted keys and stored
  `known_at`s) and refuses, writing nothing, on any other. It refuses a
  `[traded]` row unless the owner passes `allow_traded` (`--allow-traded`):
  retracting a held name's listing would leave its window unable to price
  or exit it, or fall back to an older ticker. Then, in one transaction: a
  retraction of each row (`store.retraction.retraction`: `retracted =
  TRUE`), one `ingestion_runs` row (source `edgar`, mode `retract`, status
  `retracted`, never `ok`, so it never reads as a fresh ingest), and the
  rows it retracted in `master_underived` under that run, so health's
  `underived_master_rows` shows none still live.
- **Inside a named release** (#1319, data-foundation plan T140c; runbook
  `docs/runbooks/data-releases.md`). `--apply` takes the name of the open
  data release (`--release`) and refuses, writing nothing, when no release
  of that name is open (`repair.release_refusal`); the run row's message
  names it.
- **Owner-kept successors (#922).** A row of a security on
  `master.keep_successors` (an owner-accepted successor the rules no longer
  derive, MTCH's `0000891103@2020-08-10` on #828) is never proposed: it is
  outside the row count, the digest and the `[traded]` check, and the dry
  run lists it as kept, with the reason (`store.retraction.split_kept`).
  The summary names every other keep-list id: one the build derives again
  (the keep no longer applies, so its stale rows are proposed), one whose
  CIK is not judged this run, and one with no underived row (a typo).
- **Stamped under the lock.** The retraction's `known_at = ingested_at` is
  the clock read after the write lock is held, and the set is found at that
  instant, so no stored revision (an ingest that committed while the
  command waited) can carry a later `known_at`.
- **Fails closed.** A fetch pass that fails (a validation failure, the
  failure policy's check) refuses the run, and so does a build that derives
  no security (an empty answer), or a pass with a failure no CIK can be
  found for (an FSN extraction failure). A CIK with a filing that failed or
  was quarantined this run is not judged at all (`ingest._unjudged_ciks`):
  the build may lack its rows for that reason alone. Failures are never
  recorded here (the ingest's job).
- **Point in time.** A retraction is a revision, not a delete: an as-of read
  of the master at T before the run returns what it did. Bars filed under a
  retracted successor id are not touched here: `repair-resolution` (#819)
  deletes them once its resolver no longer assigns them (a trial that
  priced them reproduces from its own export only), and `ingest --backfill
  --fill-holes` (#831) fetches the corrected security's missing bars.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

import duckdb

from tradepartner.adapters.filings import FilingSource
from tradepartner.config import Settings, clean_message
from tradepartner.ingest import (
    SourceRun,
    _prefetch,
    _read,
    _Recorded,
    _unjudged_ciks,
    _write_run,
)
from tradepartner.repair import release_refusal
from tradepartner.store.classify import build_classifications
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.master import build_chains, build_master
from tradepartner.store.retraction import (
    EDGAR,
    RETRACT,
    RETRACTED,
    Underived,
    cik_of,
    live_chains_as_of,
    record_underived,
    split_kept,
    stored_underived,
    underived_chains,
    write_retractions,
)
from tradepartner.store.schema import has_retracted, init_schema
from tradepartner.timeutil import ensure_tz_aware_utc

#: CIKs not judged that the summary names; the rest are counted.
_NAMED_CIKS = 20
#: The config key of the owner's kept successors (#922), named in the output.
_KEEP_KEY = "master.keep_successors"


class RetractRefused(RuntimeError):
    """Nothing was written (module docstring's refusals)."""


@dataclass(frozen=True)
class RetractResult:
    """What `master_retract` found and, unless `dry_run`, retracted."""

    found: tuple[Underived, ...]
    at: datetime
    dry_run: bool
    traded: frozenset[str] = frozenset()
    unjudged: frozenset[str] = frozenset()
    #: Underived rows of a `master.keep_successors` security: never proposed.
    kept: tuple[Underived, ...] = ()
    #: `master.keep_successors` ids the build derives again (not kept).
    keep_derived: tuple[str, ...] = ()
    #: `master.keep_successors` ids whose CIK is not judged this run.
    keep_unjudged: tuple[str, ...] = ()
    #: Other `master.keep_successors` ids with no underived row this run.
    keep_unmatched: tuple[str, ...] = ()

    @property
    def rows(self) -> int:
        """How many stored rows the build no longer derives."""
        return len(self.found)

    @property
    def digest(self) -> str:
        """12 hex characters of SHA-256 over the sorted rows (table, key,
        stored `known_at`): what `--apply` must match."""
        lines = sorted(
            "|".join([u.table, *map(str, u.key), u.row["known_at"].isoformat()]) for u in self.found
        )
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]

    def summary(self) -> str:
        """One line: the count by table, the digest, the run's instant, the
        rows kept on `master.keep_successors`, and the CIKs not judged."""
        by_table = {
            table: sum(1 for u in self.found if u.table == table)
            for table in ("securities", "listings", "successor_chains")
        }
        verb = "would retract" if self.dry_run else "retracted"
        line = (
            f"{verb} {self.rows} rows ({by_table['securities']} securities, "
            f"{by_table['listings']} listings, {by_table['successor_chains']} chains), "
            f"digest {self.digest}, at {self.at.isoformat()}"
        )
        if self.kept:
            held = sorted({u.row["security_id"] for u in self.kept})
            line += f"; kept {len(self.kept)} rows on {_KEEP_KEY} (owner-accepted): " + ", ".join(
                held
            )
        for ids, what in (
            (self.keep_derived, "derived again, not kept"),
            (self.keep_unjudged, "not judged this run"),
            (self.keep_unmatched, "with no underived row"),
        ):
            if ids:
                line += f"; {_KEEP_KEY} {what}: " + ", ".join(ids)
        if self.unjudged:
            named = sorted(self.unjudged)
            more = len(named) - _NAMED_CIKS
            line += f"; not judged (filings failed or quarantined): {len(named)} CIKs: " + (
                ", ".join(named[:_NAMED_CIKS]) + (f" and {more} more" if more > 0 else "")
            )
        return line

    def lines(self) -> tuple[str, ...]:
        """One line per row found, `[traded]` on a security the journal has
        an order for, then one per kept row, `[kept: master.keep_successors]`."""
        return tuple(
            item.describe()
            + (" [traded]" if any(sid in self.traded for sid in _row_ids(item)) else "")
            for item in self.found
        ) + tuple(f"{item.describe()} [kept: {_KEEP_KEY}]" for item in self.kept)


def _row_ids(item: Underived) -> tuple[str, ...]:
    """The security ids an underived row names: a listing or securities row
    its own id, a chain row its predecessor and successor (#821)."""
    if item.table == "successor_chains":
        return (item.row["predecessor_id"], item.row["successor_id"])
    return (item.row["security_id"],)


#: Every journal table that names a security a window traded or holds:
#: orders, adjustments (a spin-off child arrives as a `spinoff_receipt` with
#: no order), daily positions and lots (safety review of #872).
_TRADED_SQL = """
    SELECT security_id FROM orders WHERE list_contains(?, security_id)
    UNION SELECT security_id FROM adjustments WHERE list_contains(?, security_id)
    UNION SELECT security_id FROM positions_daily WHERE list_contains(?, security_id)
    UNION SELECT security_id FROM lots WHERE list_contains(?, security_id)
"""


def _traded(conn: duckdb.DuckDBPyConnection, found: tuple[Underived, ...]) -> frozenset[str]:
    """The securities among `found` that any journal order, adjustment, daily
    position or lot names, in any window, open or closed: a superset of the
    ones a window holds. A chain row's ids are its two sides (#821)."""
    ids = sorted({sid for item in found for sid in _row_ids(item)})
    if not ids:
        return frozenset()
    rows = conn.execute(_TRADED_SQL, [ids, ids, ids, ids]).fetchall()
    return frozenset(sid for (sid,) in rows)


def master_retract(
    settings: Settings,
    *,
    filings: FilingSource,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = True,
    expect: tuple[int, str] | None = None,
    allow_traded: bool = False,
    release: str | None = None,
) -> RetractResult:
    """Find the stored master rows the current build no longer derives and,
    unless `dry_run`, retract them and record the run (module docstring). A
    real run requires `expect`, the dry run's `(rows, digest)`, and raises
    `RetractRefused` before writing on any other, or on a traded row without
    `allow_traded`, or outside the open data release `release`
    (`repair.release_refusal`). Raises `StoreLockedError` like ingest when
    the store stays locked."""
    if not dry_run and expect is None:
        raise ValueError("an apply retracts only the set of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("an apply writes only inside a named data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    recorded = _Recorded(filings)
    try:
        _prefetch(recorded, settings, dry_run=True)
    except Exception as exc:  # any fetch or validation failure: nothing is judged
        raise RetractRefused(
            f"the EDGAR fetch pass failed, nothing judged: {type(exc).__name__}: {exc}"
        ) from exc
    recorded.frozen = True
    unjudged = _unjudged_ciks(recorded)
    if unjudged.unmapped:
        raise RetractRefused(
            f"{unjudged.unmapped} filing failures this run name no CIK, so no row can be "
            "judged safely; nothing judged, run again once they clear"
        )
    built_at = ensure_tz_aware_utc(clock(), field_name="clock()")
    build = build_master(recorded, settings, ingested_at=built_at)
    if not any(not row["benchmark"] for row in build.securities):
        raise RetractRefused(
            "the EDGAR build derives no security: an empty answer would call every "
            "stored row underived; nothing judged"
        )
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        # The dry run reads `retracted` and `master_underived` (version 11, #859),
        # never what a later version adds, so it asks for the column, not the
        # current version (PR #940: version 12 must not refuse a version-11 store).
        if dry_run and not has_retracted(conn, "listings"):
            raise RetractRefused(
                "the store is not at schema version 11 yet: run "
                "`tradepartner ingest --source edgar` once to migrate it, then the dry run"
            )
        if not dry_run:
            init_schema(conn)
            if release is not None and (why := release_refusal(conn, release)):
                raise RetractRefused(f"{why}; nothing retracted")
        at = ensure_tz_aware_utc(clock(), field_name="clock()")  # under the lock
        if at < built_at:
            raise RetractRefused(f"the clock went back from {built_at} to {at}; nothing judged")
        keep = frozenset(settings.master.keep_successors)
        derived = frozenset(row["security_id"] for row in build.securities)
        found, kept = split_kept(stored_underived(conn, build, at, unjudged.ciks), keep, derived)
        # #821, T187c: a chain is derived only when `build_chains` honours its
        # entry at this run, not merely when the entry names the successor. A
        # refused entry (`spac` predecessor, lost evidence, a successor
        # listing that no longer matches) leaves its live row underived, as
        # `store/master.py`'s chain writer skips it.
        derived_successors: frozenset[str] = frozenset()
        if settings.master.chains:
            types = {
                row["security_id"]: row["security_type"]
                for row in build_classifications(
                    recorded, build, settings, ingested_at=at
                ).classifications
            }
            chain_build = build_chains(recorded, settings, build, types, ingested_at=at)
            derived_successors = frozenset(chain.successor_id for chain in chain_build.chains)
        chains = underived_chains(live_chains_as_of(conn, at), derived_successors, unjudged.ciks)
        found = found + chains
        rest = keep - derived - {u.row["security_id"] for u in kept}
        unjudged_ids = {sid for sid in rest if cik_of(sid) in unjudged.ciks}
        result = RetractResult(
            found=found,
            at=at,
            dry_run=dry_run,
            traded=_traded(conn, found),
            unjudged=unjudged.ciks,
            kept=kept,
            keep_derived=tuple(sorted(keep & derived)),
            keep_unjudged=tuple(sorted(unjudged_ids)),
            keep_unmatched=tuple(sorted(rest - unjudged_ids)),
        )
        if not dry_run:
            if (result.rows, result.digest) != expect:
                raise RetractRefused(
                    f"found {result.rows} rows (digest {result.digest}), not the expected "
                    f"{expect}; nothing retracted, run the dry run again"
                )
            if result.traded and not allow_traded:
                raise RetractRefused(
                    f"rows of traded securities ({', '.join(sorted(result.traded))}): a "
                    "window may hold them; nothing retracted (pass --allow-traded once "
                    "you have checked)"
                )
            write_retractions(conn, found, at)
            run_id = uuid.uuid4().hex
            record_underived(conn, run_id, at, [u for u in found if u.table != "successor_chains"])
            message = clean_message(f"{result.summary()}; release {release}", settings)
            run = SourceRun(EDGAR, RETRACTED, result.rows, "", message)
            _write_run(conn, run_id, started, clock(), run, RETRACT)
    return result

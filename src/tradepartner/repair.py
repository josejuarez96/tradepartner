"""Remove the Alpaca rows the resolver no longer assigns to their security (#819).

A bar or corporate action from Alpaca reaches the store keyed by the
`security_id` that `ListingResolver` mapped its symbol to at ingest. When a
resolver rule is corrected (#819: a span ends at its own delisting, so a
reused ticker never prices a delisted security), rows written under the old
rule can sit on the wrong security: Eagle Bulk carrying another equity's
EGLE bars from 2025. Re-running the backfill cannot remove them, since
ingest only ever adds rows.

`repair_resolution` builds the resolver exactly as an ingest run would
(`store_resolver`: the listings, share counts and listing ends known at
the run) and keeps a stored row only if that resolver assigns its key: a
bar `(security_id, session)` when some ticker resolves to the security on
that session (`ListingResolver.holds`), an action `(security_id, ex_date)`
when one does on the session before the ex-date (where
`alpaca_prices.parse_corporate_actions` resolves it). Every row of any
other key, every revision of it, is deleted: such a row was never this
security's fact, so it has no `known_at` at which it was right, and a
revision row (spec Definitions) would leave it visible to every as-of read
before the repair. Rows of other sources (a fixture store) are never read
or touched.

What the repair is and is not:

- **One transaction.** The key reads, the verdicts and the deletes run on
  one write connection, so no ingest can add a row in between, and a
  failure deletes nothing.
- **Only what a dry run showed.** A real run takes the dry run's bar and
  action row counts and refuses, deleting nothing, on any other count; the
  terminal lists every security found with its counts.
- **Recorded.** It writes one `ingestion_runs` row (source `alpaca`, mode
  `repair`, status `repaired`) whose message gives the counts and the first
  securities. Status `repaired` is never `ok`, so it never counts as a
  fresh ingest for health or execution.
- **Not a fetch.** Bars the corrected resolver now assigns that the store
  never held (#819's same-day typos: FutureFuel's FF from 2024-05-10) come
  only from fetching again: a backfill with a new `--since`, whose months
  are not resumed (`backfill` module docstring).
- **Changes past reads.** As with the `statement_facts` rebuild (spec
  amendment #660), an as-of read at a T before the repair returns fewer
  rows after it; a trial logged before it is reproducible only from its
  own export. `dry_run` reports the same counts and deletes nothing.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime

import duckdb

from tradepartner.adapters.alpaca_prices import (
    ACTIONS_SOURCE,
    BAR_SOURCES,
    ListingResolver,
    registrant_evidence,
)
from tradepartner.calendar import previous_session
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _read, _write_run
from tradepartner.store.asof import facts_as_of, listings_as_of
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.timeutil import ensure_tz_aware_utc

REPAIR = "repair"
REPAIRED = "repaired"
#: Securities named in the run message, the rest counted.
_NAMED = 20
_BATCH = 200_000

Key = tuple[str, date]


def store_resolver(
    conn: duckdb.DuckDBPyConnection, at: datetime, settings: Settings
) -> ListingResolver:
    """The `ListingResolver` an ingest run at `at` builds: the listings
    known at `at`, with the `registrant_evidence` of the facts and listing
    ends known then (#793) and `at`'s day as the run's day, and
    `alpaca.rename_lead_days` (#843)."""
    at = ensure_tz_aware_utc(at, field_name="at")
    evidence = registrant_evidence(
        facts_as_of(conn, at).iter_rows(named=True),
        listing_ends_as_of(conn, at, settings).iter_rows(named=True),
        as_of=at.date(),
        quiet_after_days=settings.alpaca.registrant_quiet_days,
    )
    return ListingResolver(
        listings_as_of(conn, at).iter_rows(named=True),
        evidence,
        rename_lead_days=settings.alpaca.rename_lead_days,
    )


@dataclass(frozen=True)
class Misattributed:
    """Stored keys the resolver does not assign, with how many rows (every
    revision) each holds: bars by `(security_id, session)`, actions by
    `(security_id, ex_date)`."""

    bars: Mapping[Key, int]
    actions: Mapping[Key, int]

    @property
    def securities(self) -> tuple[str, ...]:
        """Every security with such a key, sorted."""
        return tuple(sorted({sid for sid, _ in (*self.bars, *self.actions)}))


def misattributed(
    resolver: ListingResolver,
    bar_keys: Iterable[tuple[str, date, int]],
    action_keys: Iterable[tuple[str, date, int]],
) -> Misattributed:
    """The keys, each given as `(security_id, day, rows)`, that `resolver`
    does not assign to their security (module docstring): a bar on its
    session, an action on the session before its ex-date. Pure."""
    bars = {(sid, day): rows for sid, day, rows in bar_keys if not resolver.holds(sid, day)}
    actions = {
        (sid, day): rows
        for sid, day, rows in action_keys
        if not resolver.holds(sid, previous_session(day), lead=False)  # #843: no lead
    }
    return Misattributed(bars, actions)


@dataclass(frozen=True)
class RepairResult:
    """What `repair_resolution` found and, unless `dry_run`, deleted."""

    found: Misattributed
    bar_keys_checked: int
    action_keys_checked: int
    dry_run: bool

    @property
    def bar_rows(self) -> int:
        """Bar rows found (deleted unless `dry_run`)."""
        return sum(self.found.bars.values())

    @property
    def action_rows(self) -> int:
        """Action rows found (deleted unless `dry_run`)."""
        return sum(self.found.actions.values())

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        named = self.found.securities
        more = f" and {len(named) - _NAMED} more" if len(named) > _NAMED else ""
        shown = f": {', '.join(named[:_NAMED])}{more}" if named else ""
        verb = "would delete" if self.dry_run else "deleted"
        return (
            f"{verb} {self.bar_rows} bar rows ({len(self.found.bars)} of "
            f"{self.bar_keys_checked} keys) and {self.action_rows} action rows "
            f"({len(self.found.actions)} of {self.action_keys_checked} keys) that the "
            f"resolver no longer assigns to their security, on {len(named)} securities{shown}"
        )

    def lines(self) -> list[str]:
        """One line per security found, for the terminal: its bar rows and
        their first and last session, and its action rows."""
        bars: dict[str, list[date]] = defaultdict(list)
        rows: dict[str, int] = defaultdict(int)
        for (sid, day), n in self.found.bars.items():
            bars[sid].append(day)
            rows[sid] += n
        actions: dict[str, int] = defaultdict(int)
        for (sid, _), n in self.found.actions.items():
            actions[sid] += n
        out = []
        for sid in self.found.securities:
            days = bars.get(sid, [])
            span = f" {min(days)}..{max(days)}" if days else ""
            out.append(f"  {sid}: {rows[sid]} bar rows{span}, {actions[sid]} action rows")
        return out


class RepairRefused(ValueError):
    """The repair found other counts than the dry run the owner approved;
    nothing was deleted."""


class _Counted:
    """`(security_id, day, rows)` per stored key of one table and source
    set, streamed; `seen` counts them once iterated."""

    def __init__(
        self, conn: duckdb.DuckDBPyConnection, table: str, day: str, sources: list[str]
    ) -> None:
        self._cursor = conn.execute(
            f"SELECT security_id, {day}, count(*) FROM {table} "
            "WHERE list_contains(?, source) GROUP BY ALL",
            [sources],
        )
        self.seen = 0

    def __iter__(self) -> Iterator[tuple[str, date, int]]:
        while batch := self._cursor.fetchmany(_BATCH):
            for sid, day, rows in batch:
                self.seen += 1
                yield str(sid), day, int(rows)


def _delete(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    sources: list[str],
    keys: Iterable[Key],
) -> int:
    """Delete every row of `table` from `sources` whose `(security_id,
    <day>)` is in `keys`; the number deleted."""
    rows = [[sid, when] for sid, when in keys]
    if not rows:
        return 0
    conn.execute("CREATE OR REPLACE TEMP TABLE _repair_keys (security_id VARCHAR, day DATE)")
    conn.executemany("INSERT INTO _repair_keys VALUES (?, ?)", rows)
    before = _rows(conn, table)
    conn.execute(
        f"DELETE FROM {table} WHERE list_contains(?, source) AND (security_id, {day}) IN "
        "(SELECT (security_id, day) FROM _repair_keys)",
        [sources],
    )
    conn.execute("DROP TABLE _repair_keys")
    return before - _rows(conn, table)


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return 0 if row is None else int(row[0])


def repair_resolution(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: tuple[int, int] | None = None,
) -> RepairResult:
    """Delete the Alpaca bars and actions that the resolver built as of
    `clock()` does not assign to their security, and record the run (module
    docstring); with `dry_run`, find and count them on a read-only
    connection and change nothing.

    A real run deletes only what a dry run showed: `expect` is that dry
    run's `(bar_rows, action_rows)`, and any other count raises
    `RepairRefused` before anything is deleted. Raises `StoreLockedError`
    like ingest when the store stays locked."""
    if not dry_run and expect is None:
        raise ValueError("a repair deletes only the counts of a dry run: pass expect")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    bar_sources = sorted(BAR_SOURCES)
    with opened as conn:
        resolver = store_resolver(conn, started, settings)
        bars = _Counted(conn, "prices_daily", "session", bar_sources)
        found_bars = misattributed(resolver, bars, ()).bars
        actions = _Counted(conn, "corporate_actions", "ex_date", [ACTIONS_SOURCE])
        found = Misattributed(found_bars, misattributed(resolver, (), actions).actions)
        result = RepairResult(
            found=found,
            bar_keys_checked=bars.seen,
            action_keys_checked=actions.seen,
            dry_run=dry_run,
        )
        if not dry_run:
            if (result.bar_rows, result.action_rows) != expect:
                raise RepairRefused(
                    f"found {result.bar_rows} bar rows and {result.action_rows} action rows, "
                    f"not the expected {expect}; nothing deleted, run --dry-run again"
                )
            deleted = (
                _delete(conn, "prices_daily", "session", bar_sources, found.bars),
                _delete(conn, "corporate_actions", "ex_date", [ACTIONS_SOURCE], found.actions),
            )
            if deleted != (result.bar_rows, result.action_rows):  # never a partial repair
                raise RuntimeError(
                    f"repair deleted {deleted} rows where it counted "
                    f"{(result.bar_rows, result.action_rows)}; rolled back"
                )
            run = SourceRun("alpaca", REPAIRED, 0, "", result.summary())
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result

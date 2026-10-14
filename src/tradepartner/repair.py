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
- **Inside a named release** (#1319, data-foundation plan T140c; runbook
  `docs/runbooks/data-releases.md`). A real run takes the name of the open
  release (`store.registry.open_release`) and refuses, deleting nothing, when
  no release of that name is open; the run row's message names it.
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

`delete_bars` is the targeted form (`tradepartner repair-bars`, T140c): it
deletes one security's Alpaca bars and corporate actions, every revision, on
the sessions `[sessions_from, sessions_to]` (an action by its ex-date), under
the same rules: one transaction, the dry run's counts, an open release, one
run row. It is how a contaminated stretch the resolver alone cannot judge
(#1314: another issuer's bars under a reused ticker, rule 8) leaves the
store before `ingest --source alpaca --fill-holes` fetches the security's own
bars: a contaminated revision left in place would stay visible to every
as-of read before the refetch. A delete is not a revision: no row is
written to `prices_daily` or `corporate_actions`, so an as-of read at any T
afterwards simply lacks the rows (the look-ahead rules are unchanged).

`retract_dividends` (`tradepartner repair-dividends`, #1613, T186) deletes
the second record of one cash dividend stored twice: for every `duplicate`
entry of the reviewed list (`store.dividend_reviews`) with an ex-date on or
before `--to`, every row of the identity `(security_id,
retract_source_action_id)`, an Alpaca dividend, and nothing else. A
duplicate has no `known_at` at which it was right, and a `cancelled`
revision would leave every earlier as-of read counting the payment twice,
so it is a delete under the same rules as `delete_bars`; it refuses the
whole run on any stale entry (the vendor touched it, or the kept row
differs or is gone), so the list is never half-applied.

`delete_master_rows` (`tradepartner delete-master-rows`, #1682 lever B,
T191) deletes stored `listings` and `delistings` rows that were never right
at any instant (a note's listing or its Form 25 on the common's primary id)
from a reviewed manifest, each with the filing, accepted no later than the
row's `known_at`, whose text shows the instrument was never the common (spec
open question 35 (a)). It runs on the caller's connection and transaction,
refuses a row whose filing is not the security's CIK's or was accepted after
the row's `known_at`, and refuses every row an open paper window read.
"""

from __future__ import annotations

import csv
import functools
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.adapters.alpaca_prices import (
    ACTIONS_SOURCE,
    BAR_SOURCES,
    ListingResolver,
    registrant_evidence,
)
from tradepartner.adapters.filings import FilingHeader
from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _read, _write_run
from tradepartner.store.asof import facts_as_of, listings_as_of, live_actions_as_of
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.dividend_reviews import DUPLICATE, ReviewedGroup, reviewed_groups
from tradepartner.store.master import first_session, session_of
from tradepartner.store.registry import open_release
from tradepartner.store.restamp_precheck import (
    crossed_instants,
    read_instants,
    window_first_session,
)
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
    `alpaca.rename_lead_days` (#843) and `alpaca.accepted_relistings`
    (#943); and, while `alpaca.first_span_lead` is on (#974), each
    security's first session (`first_sessions`)."""
    at = ensure_tz_aware_utc(at, field_name="at")
    evidence = registrant_evidence(
        facts_as_of(conn, at).iter_rows(named=True),
        listing_ends_as_of(conn, at, settings).iter_rows(named=True),
        as_of=at.date(),
        quiet_after_days=settings.alpaca.registrant_quiet_days,
        transfer_window_sessions=settings.master.transfer_window_sessions,
        class_symbols=settings.alpaca.class_symbols,
    )
    return ListingResolver(
        listings_as_of(conn, at).iter_rows(named=True),
        evidence,
        rename_lead_days=settings.alpaca.rename_lead_days,
        accepted_relistings=settings.alpaca.accepted_relistings,
        class_symbols=settings.alpaca.class_symbols,
        first_sessions=first_sessions(conn, at, settings)
        if settings.alpaca.first_span_lead
        else None,
        last_bar=functools.partial(last_bar, conn, at),
        handover_sessions=settings.master.transfer_window_sessions,
    )


def last_bar(
    conn: duckdb.DuckDBPyConnection, at: datetime, security_id: str, start: date, before: date
) -> date | None:
    """The session of `security_id`'s last traded Alpaca bar in `[start,
    before)` known at `at` (rule 8, #1314: L): per session the latest
    revision known at `at`, from an Alpaca source, with volume above zero."""
    row = conn.execute(
        "SELECT max(session) FROM ("
        "  SELECT session, volume FROM prices_daily"
        "  WHERE security_id = ? AND session >= ? AND session < ? AND known_at <= ?"
        "  AND list_contains(?, source)"
        "  QUALIFY row_number() OVER (PARTITION BY session ORDER BY known_at DESC) = 1"
        ") WHERE volume > 0",
        [security_id, start, before, at, sorted(BAR_SOURCES)],
    ).fetchone()
    return None if row is None else row[0]


def first_sessions(
    conn: duckdb.DuckDBPyConnection, at: datetime, settings: Settings
) -> dict[str, date]:
    """Per security id, the session of the **earliest** `known_at` among
    its `securities` rows known at `at` (#974): its first filing's
    acceptance, mapped to a session as `store.master` maps one (the session
    of its New York day, else the next). A direct read of the table, never
    `securities_as_of`, whose latest revision is stamped at an ingest clock
    (a renamed or restored row would move the floor past
    the first span and take the lead away). A `known_at` before the
    calendar's first session maps to that session (the calendar raises
    before it, `store.master.first_session`)."""
    at = ensure_tz_aware_utc(at, field_name="at")
    floor = datetime.combine(first_session(settings), time(12), tzinfo=UTC)
    rows = conn.execute(
        "SELECT security_id, min(known_at) FROM securities "
        "WHERE known_at <= ? AND NOT retracted GROUP BY ALL",
        [at],
    ).fetchall()
    return {
        str(sid): session_of(max(ensure_tz_aware_utc(known, field_name="known_at"), floor))
        for sid, known in rows
    }


@dataclass(frozen=True)
class Misattributed:
    """Stored keys the resolver does not assign, with how many rows (every
    revision) each holds: bars by `(security_id, session)`, actions by
    `(security_id, ex_date)`."""

    bars: Mapping[Key, int]
    actions: Mapping[Key, int]
    spinoffs: Mapping[Key, int] = field(default_factory=dict)

    @property
    def securities(self) -> tuple[str, ...]:
        """Every security with such a key, sorted."""
        return tuple(sorted({sid for sid, _ in (*self.bars, *self.actions, *self.spinoffs)}))


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
        if not resolver.holds(sid, previous_session(day), actions=True)  # #843, #974
    }
    return Misattributed(bars, actions)


@dataclass(frozen=True)
class RepairResult:
    """What `repair_resolution` found and, unless `dry_run`, deleted."""

    found: Misattributed
    bar_keys_checked: int
    action_keys_checked: int
    dry_run: bool
    unjudged_symbol_changes: int = 0

    @property
    def bar_rows(self) -> int:
        """Bar rows found (deleted unless `dry_run`)."""
        return sum(self.found.bars.values())

    @property
    def action_rows(self) -> int:
        """Action rows found (deleted unless `dry_run`)."""
        return sum(self.found.actions.values()) + sum(self.found.spinoffs.values())

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        named = self.found.securities
        more = f" and {len(named) - _NAMED} more" if len(named) > _NAMED else ""
        shown = f": {', '.join(named[:_NAMED])}{more}" if named else ""
        verb = "would delete" if self.dry_run else "deleted"
        return (
            f"{verb} {self.bar_rows} bar rows ({len(self.found.bars)} of "
            f"{self.bar_keys_checked} keys) and {self.action_rows} action rows "
            f"({len(self.found.actions) + len(self.found.spinoffs)} of "
            f"{self.action_keys_checked} keys) that the "
            f"resolver no longer assigns to their security, on {len(named)} securities{shown}; "
            f"{self.unjudged_symbol_changes} symbol_changes rows unjudged"
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
        for found in (self.found.actions, self.found.spinoffs):
            for (sid, _), n in found.items():
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
        self,
        conn: duckdb.DuckDBPyConnection,
        table: str,
        day: str,
        sources: list[str],
        action_types: Sequence[str] | None = None,
    ) -> None:
        extra = " AND list_contains(?, action_type)" if action_types is not None else ""
        self._cursor = conn.execute(
            f"SELECT security_id, {day}, count(*) FROM {table} "
            f"WHERE list_contains(?, source){extra} GROUP BY ALL",
            [sources, *([list(action_types)] if action_types is not None else [])],
        )
        self.seen = 0

    def __iter__(self) -> Iterator[tuple[str, date, int]]:
        while batch := self._cursor.fetchmany(_BATCH):
            for sid, day, rows in batch:
                self.seen += 1
                yield str(sid), day, int(rows)


def _delete_spinoffs(conn: duckdb.DuckDBPyConnection, keys: Sequence[tuple[str, date, str]]) -> int:
    """Delete the `spinoff` rows from `ACTIONS_SOURCE` whose `(child, ex_date,
    parent)` is in `keys`, never another parent's row of the same child and
    day; the number deleted."""
    if not keys:
        return 0
    before = _rows(conn, "corporate_actions")
    conn.executemany(
        "DELETE FROM corporate_actions WHERE source = ? AND action_type = 'spinoff' "
        "AND security_id = ? AND ex_date = ? AND source_action_id = ?",
        [[ACTIONS_SOURCE, *key] for key in keys],
    )
    return before - _rows(conn, "corporate_actions")


def _delete(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    sources: list[str],
    keys: Iterable[Key],
    action_types: Sequence[str] | None = None,
) -> int:
    """Delete every row of `table` from `sources` whose `(security_id,
    <day>)` is in `keys`; the number deleted."""
    rows = [[sid, when] for sid, when in keys]
    if not rows:
        return 0
    conn.execute("CREATE OR REPLACE TEMP TABLE _repair_keys (security_id VARCHAR, day DATE)")
    conn.executemany("INSERT INTO _repair_keys VALUES (?, ?)", rows)
    before = _rows(conn, table)
    extra = " AND list_contains(?, action_type)" if action_types is not None else ""
    conn.execute(
        f"DELETE FROM {table} WHERE list_contains(?, source){extra} "
        f"AND (security_id, {day}) IN (SELECT (security_id, day) FROM _repair_keys)",
        [sources, *([list(action_types)] if action_types is not None else [])],
    )
    conn.execute("DROP TABLE _repair_keys")
    return before - _rows(conn, table)


def release_refusal(conn: duckdb.DuckDBPyConnection, release: str) -> str | None:
    """Why a row-changing repair may not write under `release` on `conn`, or
    None when `release` is the open data release (`store.registry.open_release`;
    runbook `docs/runbooks/data-releases.md`, rule 1). Read on the write
    connection, under its lock, so no release can close in between."""
    current = open_release(conn)
    if current is not None and current.name == release:
        return None
    found = "no release is open" if current is None else f"release {current.name!r} is open"
    return (
        f"no open data release named {release!r} ({found}); open it first with "
        "`tradepartner decision data-release open`"
    )


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return 0 if row is None else int(row[0])


def repair_resolution(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: tuple[int, int] | None = None,
    release: str | None = None,
) -> RepairResult:
    """Delete the Alpaca bars and actions that the resolver built as of
    `clock()` does not assign to their security, and record the run (module
    docstring); with `dry_run`, find and count them on a read-only
    connection and change nothing.

    A real run deletes only what a dry run showed: `expect` is that dry
    run's `(bar_rows, action_rows)`, and any other count raises
    `RepairRefused` before anything is deleted. It runs only inside the open
    data release `release` (`release_refusal`), else `RepairRefused`. Raises
    `StoreLockedError` like ingest when the store stays locked."""
    if not dry_run and expect is None:
        raise ValueError("a repair deletes only the counts of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("a repair writes only inside a named data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    bar_sources = sorted(BAR_SOURCES)
    with opened as conn:
        if release is not None and not dry_run and (why := release_refusal(conn, release)):
            raise RepairRefused(f"{why}; nothing deleted")
        resolver = store_resolver(conn, started, settings)
        bars = _Counted(conn, "prices_daily", "session", bar_sources)
        found_bars = misattributed(resolver, bars, ()).bars
        actions = _Counted(
            conn,
            "corporate_actions",
            "ex_date",
            [ACTIONS_SOURCE],
            action_types=("split", "dividend"),
        )
        ordinary = misattributed(resolver, (), actions).actions
        spin_rows = conn.execute(
            "SELECT security_id, ex_date, source_action_id, count(*) "
            "FROM corporate_actions WHERE source = ? AND action_type = 'spinoff' GROUP BY ALL",
            [ACTIONS_SOURCE],
        ).fetchall()
        spinoffs: dict[Key, int] = defaultdict(int)
        bad_spins: list[tuple[str, date, str]] = []  # (child, ex_date, parent)
        for child, ex_date, parent, count in spin_rows:
            if not (
                resolver.holds(parent, previous_session(ex_date), actions=True)
                and resolver.holds(child, ex_date, actions=True)
            ):
                spinoffs[(child, ex_date)] += int(count)
                bad_spins.append((child, ex_date, parent))
        spinoffs = dict(spinoffs)
        found = Misattributed(found_bars, ordinary, spinoffs)
        unjudged_row = conn.execute("SELECT count(*) FROM symbol_changes").fetchone()
        assert unjudged_row is not None
        unjudged = int(unjudged_row[0])
        result = RepairResult(
            found=found,
            bar_keys_checked=bars.seen,
            action_keys_checked=actions.seen + len(spin_rows),
            dry_run=dry_run,
            unjudged_symbol_changes=unjudged,
        )
        if not dry_run:
            if (result.bar_rows, result.action_rows) != expect:
                raise RepairRefused(
                    f"found {result.bar_rows} bar rows and {result.action_rows} action rows, "
                    f"not the expected {expect}; nothing deleted, run --dry-run again"
                )
            deleted = (
                _delete(conn, "prices_daily", "session", bar_sources, found.bars),
                _delete(
                    conn,
                    "corporate_actions",
                    "ex_date",
                    [ACTIONS_SOURCE],
                    found.actions,
                    action_types=("split", "dividend"),
                )
                + _delete_spinoffs(conn, bad_spins),
            )
            if deleted != (result.bar_rows, result.action_rows):  # never a partial repair
                raise RuntimeError(
                    f"repair deleted {deleted} rows where it counted "
                    f"{(result.bar_rows, result.action_rows)}; rolled back"
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("alpaca", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


@dataclass(frozen=True)
class BarsDeleted:
    """What `delete_bars` found on one security's sessions and, unless
    `dry_run`, deleted: every revision, from an Alpaca source."""

    security_id: str
    sessions_from: date
    sessions_to: date
    bar_rows: int
    action_rows: int
    #: The distinct sessions with a bar row, and the distinct ex-dates.
    bar_sessions: tuple[date, ...]
    ex_dates: tuple[date, ...]
    dry_run: bool

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would delete" if self.dry_run else "deleted"
        span = (
            f" on {len(self.bar_sessions)} sessions {self.bar_sessions[0]}..{self.bar_sessions[-1]}"
            if self.bar_sessions
            else ""
        )
        dates = f" (ex-dates {', '.join(map(str, self.ex_dates))})" if self.ex_dates else ""
        return (
            f"{verb} {self.bar_rows} bar rows{span} and {self.action_rows} action rows{dates} "
            f"of {self.security_id} in {self.sessions_from}..{self.sessions_to}"
        )


def _bars_in_range(
    conn: duckdb.DuckDBPyConnection, security_id: str, first: date, last: date
) -> tuple[int, tuple[date, ...], int, tuple[date, ...]]:
    bars = conn.execute(
        "SELECT session, count(*) FROM prices_daily WHERE security_id = ? "
        "AND session BETWEEN ? AND ? AND list_contains(?, source) GROUP BY ALL ORDER BY 1",
        [security_id, first, last, sorted(BAR_SOURCES)],
    ).fetchall()
    actions = conn.execute(
        "SELECT ex_date, count(*) FROM corporate_actions WHERE security_id = ? "
        "AND ex_date BETWEEN ? AND ? AND source = ? GROUP BY ALL ORDER BY 1",
        [security_id, first, last, ACTIONS_SOURCE],
    ).fetchall()
    return (
        sum(int(n) for _, n in bars),
        tuple(day for day, _ in bars),
        sum(int(n) for _, n in actions),
        tuple(day for day, _ in actions),
    )


def delete_bars(
    settings: Settings,
    *,
    security_id: str,
    sessions_from: date,
    sessions_to: date,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: tuple[int, int] | None = None,
    release: str | None = None,
) -> BarsDeleted:
    """Delete `security_id`'s Alpaca bars on the sessions `[sessions_from,
    sessions_to]` and its Alpaca corporate actions with an ex-date there,
    every revision, in one transaction, and record the run (module
    docstring); with `dry_run`, count them on a read-only connection and
    change nothing.

    A real run deletes only what a dry run showed (`expect`, its
    `(bar_rows, action_rows)`) and only inside the open data release
    `release`; anything else raises `RepairRefused` before anything is
    deleted, as does a security with no `securities` or `listings` row. A day that is not
    an XNYS session or a reversed range raises `ValueError`. Raises
    `StoreLockedError` like ingest when the store stays locked."""
    for flag, day in (("--from", sessions_from), ("--to", sessions_to)):
        if not is_session(day):
            raise ValueError(f"{flag} {day.isoformat()} is not an XNYS session")
    if sessions_from > sessions_to:
        raise ValueError(f"--from {sessions_from} is after --to {sessions_to}")
    if not dry_run and expect is None:
        raise ValueError("a repair deletes only the counts of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("a repair writes only inside a named data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        if release is not None and not dry_run and (why := release_refusal(conn, release)):
            raise RepairRefused(f"{why}; nothing deleted")
        known = conn.execute(  # a typo'd id is refused, not a quiet 0-row run
            "SELECT 1 FROM securities WHERE security_id = $id "
            "UNION ALL SELECT 1 FROM listings WHERE security_id = $id LIMIT 1",
            {"id": security_id},
        ).fetchone()
        if known is None:
            raise RepairRefused(f"the store holds no security {security_id!r}; nothing deleted")
        bar_rows, sessions, action_rows, ex_dates = _bars_in_range(
            conn, security_id, sessions_from, sessions_to
        )
        result = BarsDeleted(
            security_id=security_id,
            sessions_from=sessions_from,
            sessions_to=sessions_to,
            bar_rows=bar_rows,
            action_rows=action_rows,
            bar_sessions=sessions,
            ex_dates=ex_dates,
            dry_run=dry_run,
        )
        if not dry_run:
            if (bar_rows, action_rows) != expect:
                raise RepairRefused(
                    f"found {bar_rows} bar rows and {action_rows} action rows, not the "
                    f"expected {expect}; nothing deleted, run --dry-run again"
                )
            deleted = (
                _delete(
                    conn,
                    "prices_daily",
                    "session",
                    sorted(BAR_SOURCES),
                    ((security_id, day) for day in sessions),
                ),
                _delete(
                    conn,
                    "corporate_actions",
                    "ex_date",
                    [ACTIONS_SOURCE],
                    ((security_id, day) for day in ex_dates),
                ),
            )
            if deleted != (bar_rows, action_rows):  # never a partial repair
                raise RuntimeError(
                    f"repair deleted {deleted} rows where it counted "
                    f"{(bar_rows, action_rows)}; rolled back"
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("alpaca", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


@dataclass(frozen=True)
class DividendsRetracted:
    """What `retract_dividends` found for the reviewed list's `duplicate`
    entries and, unless `dry_run`, deleted (`BarsDeleted`'s shape)."""

    #: The `--to` bound (None: every entry).
    to: date | None
    #: The list's `duplicate` entries.
    entries: int
    #: Entries on or before `to` whose retract identity still has its row.
    identities: int
    #: The rows those identities hold (one each, or the entry is stale).
    action_rows: int
    #: Entries after `to`: counted, untouched.
    deferred: int
    #: Entries whose retract identity has no row at all.
    already_repaired: int
    #: One line per stale entry (refuses a real run).
    stale: tuple[str, ...]
    #: The distinct ex-dates of the rows found.
    ex_dates: tuple[date, ...]
    dry_run: bool

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would delete" if self.dry_run else "deleted"
        span = (
            f" on {len(self.ex_dates)} ex-dates {self.ex_dates[0]}..{self.ex_dates[-1]}"
            if self.ex_dates
            else ""
        )
        bound = f" through {self.to}" if self.to is not None else ""
        return (
            f"{verb} {self.action_rows} duplicate dividend rows of {self.identities} identities"
            f"{span}; entries {self.entries}{bound}, deferred {self.deferred}, "
            f"already repaired {self.already_repaired}, stale {len(self.stale)}"
        )


def _stale_reason(
    retract_rows: list[tuple[Any, ...]],
    partners: list[tuple[Any, ...]],
    entry: ReviewedGroup,
) -> str | None:
    """Why one entry may not be retracted, or None (spec amendment
    2026-10-12, "The repair"). `retract_rows` are every row of the retract
    identity as `(source, action_type, ex_date, ratio_or_amount, known_at,
    cancelled)`; `partners` the other live dividend identities of the
    security on the entry's ex-date and amount as `(source_action_id,
    known_at)`."""
    if len(retract_rows) > 1:
        return f"the retract identity has {len(retract_rows)} revisions"
    source, action_type, ex_date, amount, known_at, cancelled = retract_rows[0]
    if source != ACTIONS_SOURCE or action_type != "dividend" or cancelled:
        return f"the retract row is a {source} {action_type} (cancelled {cancelled})"
    if ex_date != entry.ex_date or amount != entry.amount:
        return f"the retract row is {ex_date} {amount!r}, not the listed key"
    if not partners:
        return "no other live dividend row shares its ex-date and amount"
    if len(partners) > 1:
        return f"{len(partners)} other live identities share its key, not the one reviewed"
    [(kept, kept_known)] = partners
    if kept_known != known_at:
        return f"the retract row is known at {known_at}, the kept {kept} at {kept_known}"
    return None


def retract_dividends(
    settings: Settings,
    *,
    to: date | None = None,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: int | None = None,
    release: str | None = None,
    reviewed: Sequence[ReviewedGroup] | None = None,
) -> DividendsRetracted:
    """Delete the retract identity of every `duplicate` entry of the
    reviewed list (`reviewed`, default `store.dividend_reviews.
    reviewed_groups()`) with an ex-date on or before `to` (default: every
    entry; a later one is counted `deferred`), every row of `(security_id,
    retract_source_action_id)` with `source = ACTIONS_SOURCE` and
    `action_type = dividend` and nothing else, in one transaction, and record
    the run (spec amendment 2026-10-12, #1613); with `dry_run`, count them on
    a read-only connection and change nothing.

    An entry whose retract identity has no row is `already_repaired` when
    exactly one other live dividend identity holds its key, and stale
    otherwise (a mistyped id or security, or a reissued record). An
    entry is stale (`_stale_reason`) when that identity has more than one
    revision, when its row's ex-date or amount differs from the entry's key
    or its `known_at` from the kept identity's live row, or when no single
    other live dividend row of the security shares its ex-date and amount; a
    dry run lists the stale entries, a real run refuses on any. A real run
    deletes only the dry run's row count (`expect`) and only inside the open
    data release `release`; anything else raises `RepairRefused` before
    anything is deleted. `to` that is not an XNYS session raises
    `ValueError`. Raises `StoreLockedError` like ingest when the store stays
    locked."""
    if to is not None and not is_session(to):
        raise ValueError(f"--to {to.isoformat()} is not an XNYS session")
    if not dry_run and expect is None:
        raise ValueError("a repair deletes only the counts of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("a repair writes only inside a named data release: pass release")
    entries = [
        g for g in (reviewed_groups() if reviewed is None else reviewed) if g.verdict == DUPLICATE
    ]
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        if release is not None and not dry_run and (why := release_refusal(conn, release)):
            raise RepairRefused(f"{why}; nothing deleted")
        live = live_actions_as_of(conn, started, sorted({e.security_id for e in entries}))
        live = live.filter(pl.col("action_type") == "dividend")
        deferred = already = 0
        found: list[ReviewedGroup] = []
        stale: list[str] = []
        for entry in entries:
            if to is not None and entry.ex_date > to:
                deferred += 1
                continue
            retract_rows = conn.execute(
                "SELECT source, action_type, ex_date, ratio_or_amount, known_at, cancelled "
                "FROM corporate_actions WHERE security_id = ? AND source_action_id = ?",
                [entry.security_id, entry.retract_source_action_id],
            ).fetchall()
            partners = (
                live.filter(
                    (pl.col("security_id") == entry.security_id)
                    & (pl.col("ex_date") == entry.ex_date)
                    & (pl.col("ratio_or_amount") == entry.amount)
                    & (pl.col("source_action_id") != entry.retract_source_action_id)
                )
                .select("source_action_id", "known_at")
                .rows()
            )
            stale_why: str | None
            if not retract_rows:
                if len(partners) == 1:
                    already += 1
                    continue
                stale_why = (
                    f"the retract identity has no row and {len(partners)} live identities "
                    "hold the key, not the one kept"
                )
            else:
                stale_why = _stale_reason(retract_rows, partners, entry)
            if stale_why is not None:
                stale.append(
                    f"  {entry.security_id} {entry.ex_date} {entry.amount!r} "
                    f"{entry.retract_source_action_id} ({entry.ticker or 'no ticker'}): {stale_why}"
                )
                continue
            found.append(entry)
        result = DividendsRetracted(
            to=to,
            entries=len(entries),
            identities=len(found),
            action_rows=len(found),
            deferred=deferred,
            already_repaired=already,
            stale=tuple(stale),
            ex_dates=tuple(sorted({e.ex_date for e in found})),
            dry_run=dry_run,
        )
        if not dry_run:
            if stale:
                raise RepairRefused(
                    f"{len(stale)} stale list entries; nothing deleted, re-review them on "
                    "#1613:\n" + "\n".join(stale)
                )
            if result.action_rows != expect:
                raise RepairRefused(
                    f"found {result.action_rows} action rows, not the expected {expect}; "
                    "nothing deleted, run --dry-run again"
                )
            deleted = _delete_identities(
                conn, [(e.security_id, e.retract_source_action_id) for e in found]
            )
            if deleted != result.action_rows:  # never a partial repair
                raise RuntimeError(
                    f"repair deleted {deleted} rows where it counted {result.action_rows}; "
                    "rolled back"
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("alpaca", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


def _delete_identities(
    conn: duckdb.DuckDBPyConnection, identities: Sequence[tuple[str, str]]
) -> int:
    """Delete every `ACTIONS_SOURCE` dividend row whose `(security_id,
    source_action_id)` is in `identities`; the number deleted."""
    if not identities:
        return 0
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE _retract_ids (security_id VARCHAR, source_action_id VARCHAR)"
    )
    conn.executemany("INSERT INTO _retract_ids VALUES (?, ?)", [list(i) for i in identities])
    before = _rows(conn, "corporate_actions")
    conn.execute(
        "DELETE FROM corporate_actions WHERE source = ? AND action_type = 'dividend' "
        "AND (security_id, source_action_id) IN (SELECT (security_id, source_action_id) "
        "FROM _retract_ids)",
        [ACTIONS_SOURCE],
    )
    conn.execute("DROP TABLE _retract_ids")
    return before - _rows(conn, "corporate_actions")


# --- T191 (#1682 lever B): the reviewed delete of never-true master rows ---

#: The tables `delete_master_rows` may delete from.
MASTER_DELETE_TABLES: tuple[str, ...] = ("listings", "delistings")

#: The manifest's columns, in order (`read_master_manifest`). A `listings`
#: row is keyed by `ticker, exchange, valid_from` (and its title, checked),
#: a `delistings` row by `form, class_title, exchange, filed_at`; both by
#: `security_id` and `known_at`, the table's UNIQUE key. `accession` names
#: the filing whose text shows the instrument was never the common.
MASTER_MANIFEST_COLUMNS: tuple[str, ...] = (
    "table",
    "security_id",
    "ticker",
    "exchange",
    "valid_from",
    "form",
    "class_title",
    "filed_at",
    "known_at",
    "accession",
)

#: A delete moves a row's stamp to never: every read at or after its
#: `known_at` reads it differently afterwards (`ingest._crosses`, #1522).
_NEVER = datetime.max.replace(tzinfo=UTC)

_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MasterRowKey:
    """One reviewed manifest row: the stored row's key and the evidence."""

    table: str
    security_id: str
    exchange: str
    class_title: str | None
    known_at: datetime
    accession: str
    #: `listings` only.
    ticker: str | None = None
    valid_from: date | None = None
    #: `delistings` only.
    form: str | None = None
    filed_at: datetime | None = None

    def __post_init__(self) -> None:
        """Check the table's key columns and make the instants UTC."""
        if self.table not in MASTER_DELETE_TABLES:
            raise ValueError(f"table {self.table!r} is not one of {MASTER_DELETE_TABLES}")
        for name in ("security_id", "exchange", "accession"):
            if not getattr(self, name):
                raise ValueError(f"{self.table} row: {name} is empty")
        object.__setattr__(
            self, "known_at", ensure_tz_aware_utc(self.known_at, field_name="known_at")
        )
        if self.table == "listings":
            if not self.ticker or self.valid_from is None:
                raise ValueError("a listings row needs ticker and valid_from")
            if self.form is not None or self.filed_at is not None:
                raise ValueError("a listings row has no form or filed_at")
        else:
            if not self.form or not self.class_title or self.filed_at is None:
                raise ValueError("a delistings row needs form, class_title and filed_at")
            if self.ticker is not None or self.valid_from is not None:
                raise ValueError("a delistings row has no ticker or valid_from")
            object.__setattr__(
                self, "filed_at", ensure_tz_aware_utc(self.filed_at, field_name="filed_at")
            )

    def label(self) -> str:
        """The row as one line: table, id, key, stamp, evidence."""
        if self.table == "listings":
            key = f"{self.ticker} {self.exchange} {self.valid_from} {self.class_title!r}"
        else:
            assert self.filed_at is not None
            key = f"{self.form} {self.class_title!r} {self.exchange} {self.filed_at.isoformat()}"
        return (
            f"{self.table} {self.security_id} {key} known_at {self.known_at.isoformat()} "
            f"({self.accession})"
        )


def _optional(record: Mapping[str, str | None], name: str) -> str | None:
    value = (record.get(name) or "").strip()
    return value or None


def read_master_manifest(path: Path) -> list[MasterRowKey]:
    """The reviewed manifest at `path`: a CSV with `MASTER_MANIFEST_COLUMNS`
    as its header, one stored row per line (empty cells for the other
    table's key columns), instants as ISO 8601 with an offset. Raises
    `ValueError` for another header, a malformed or naive value, or a row
    listed twice."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != MASTER_MANIFEST_COLUMNS:
            raise ValueError(
                f"{path.name}: the header must be {','.join(MASTER_MANIFEST_COLUMNS)}, "
                f"not {','.join(reader.fieldnames or ())}"
            )
        rows: list[MasterRowKey] = []
        for line, record in enumerate(reader, start=2):
            try:
                valid_from = _optional(record, "valid_from")
                filed_at = _optional(record, "filed_at")
                row = MasterRowKey(
                    table=(record["table"] or "").strip(),
                    security_id=(record["security_id"] or "").strip(),
                    exchange=(record["exchange"] or "").strip(),
                    class_title=_optional(record, "class_title"),
                    known_at=datetime.fromisoformat((record["known_at"] or "").strip()),
                    accession=(record["accession"] or "").strip(),
                    ticker=_optional(record, "ticker"),
                    valid_from=None if valid_from is None else date.fromisoformat(valid_from),
                    form=_optional(record, "form"),
                    filed_at=None if filed_at is None else datetime.fromisoformat(filed_at),
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path.name} line {line}: {error}") from None
            if row in rows:
                raise ValueError(f"{path.name} line {line}: listed twice: {row.label()}")
            rows.append(row)
    return rows


@dataclass(frozen=True)
class MasterRowsDeleted:
    """What `delete_master_rows` found for a manifest and, unless `dry_run`,
    deleted (`BarsDeleted`'s shape)."""

    release: str
    #: The manifest's rows.
    rows: int
    #: Rows that pass every check, by table.
    listings: int
    delistings: int
    #: One line per row that passes, and per refused row with the reason.
    found: tuple[str, ...]
    refused: tuple[str, ...]
    dry_run: bool

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would delete" if self.dry_run else "deleted"
        return (
            f"{verb} {self.listings} listings and {self.delistings} delistings rows of "
            f"{self.rows} manifest rows; refused {len(self.refused)}; release {self.release}"
        )


#: `(cik, accession)` -> that filing's SGML header read for that CIK
#: (`adapters.edgar.parse_sgml_header(text, cik)`); raises `ValueError` when
#: the filing cannot be read or names no such filer or subject company.
HeaderLookup = Callable[[str, str], FilingHeader]


def _stored_master_row(conn: duckdb.DuckDBPyConnection, row: MasterRowKey) -> list[tuple[str, str]]:
    """`(source, provenance)` of each stored row with `row`'s key and stamp."""
    if row.table == "listings":
        return conn.execute(
            "SELECT source, provenance FROM listings WHERE security_id = ? AND ticker = ? "
            "AND exchange = ? AND valid_from = ? AND known_at = ? "
            "AND class_title IS NOT DISTINCT FROM ?",
            [
                row.security_id,
                row.ticker,
                row.exchange,
                row.valid_from,
                row.known_at,
                row.class_title,
            ],
        ).fetchall()
    return conn.execute(
        "SELECT source, provenance FROM delistings WHERE security_id = ? AND form = ? "
        "AND class_title = ? AND exchange = ? AND filed_at = ? AND known_at = ?",
        [row.security_id, row.form, row.class_title, row.exchange, row.filed_at, row.known_at],
    ).fetchall()


def _revisions(conn: duckdb.DuckDBPyConnection, row: MasterRowKey) -> set[datetime]:
    """The `known_at` of every stored revision of `row`'s key."""
    if row.table == "listings":
        found = conn.execute(
            "SELECT known_at FROM listings WHERE security_id = ? AND ticker = ? "
            "AND exchange = ? AND valid_from = ?",
            [row.security_id, row.ticker, row.exchange, row.valid_from],
        ).fetchall()
    else:
        found = conn.execute(
            "SELECT known_at FROM delistings WHERE security_id = ? AND form = ? "
            "AND class_title = ? AND exchange = ? AND filed_at = ?",
            [row.security_id, row.form, row.class_title, row.exchange, row.filed_at],
        ).fetchall()
    return {ensure_tz_aware_utc(k, field_name="known_at") for (k,) in found}


def _master_row_refusal(
    conn: duckdb.DuckDBPyConnection,
    row: MasterRowKey,
    manifest: Sequence[MasterRowKey],
    headers: HeaderLookup,
    window: tuple[date, Sequence[datetime]] | None,
) -> str | None:
    """Why `row` may not be deleted, or None (`delete_master_rows`)."""
    stored = _stored_master_row(conn, row)
    if not stored:
        return "no stored row has this key and known_at"
    if stored != [("edgar", "filing")]:
        return f"the stored row is {stored[0][0]} {stored[0][1]}, not an EDGAR filing row"
    listed = {r.known_at for r in manifest if r.table == row.table and _same_key(r, row)}
    if missing := sorted(_revisions(conn, row) - listed):
        return "another revision of this key is not in the manifest: " + ", ".join(
            k.isoformat() for k in missing
        )
    ciks = conn.execute(
        "SELECT DISTINCT cik FROM securities WHERE security_id = ?", [row.security_id]
    ).fetchall()
    if len(ciks) != 1:
        return f"the security has {len(ciks)} CIKs in securities, not one"
    cik = str(ciks[0][0])
    try:
        header = headers(cik, row.accession)
    except ValueError as error:
        return f"{row.accession} is not CIK {cik}'s filing: {error}"
    if header.cik != cik or header.accession != row.accession:
        return f"{row.accession}'s header names {header.cik} {header.accession}, not CIK {cik}"
    if header.accepted_at > row.known_at:
        return (
            f"{row.accession} was accepted at {header.accepted_at.isoformat()}, after the "
            f"row's known_at"
        )
    if window is not None:
        first, instants = window
        if crossed := crossed_instants(_NEVER, row.known_at, instants):
            return (
                f"an open paper window (first session {first}) read it: {len(crossed)} read "
                f"instants from {crossed[0].isoformat()}"
            )
        if row.known_at.astimezone(_NEW_YORK).date() >= first:
            return f"known inside the open paper window (first session {first})"
    return None


def _same_key(a: MasterRowKey, b: MasterRowKey) -> bool:
    """True when `a` and `b` are revisions of one stored key."""
    if a.table == "listings":
        return (a.security_id, a.ticker, a.exchange, a.valid_from) == (
            b.security_id,
            b.ticker,
            b.exchange,
            b.valid_from,
        )
    return (a.security_id, a.form, a.class_title, a.exchange, a.filed_at) == (
        b.security_id,
        b.form,
        b.class_title,
        b.exchange,
        b.filed_at,
    )


def _delete_master_row(conn: duckdb.DuckDBPyConnection, row: MasterRowKey) -> int:
    """Delete the one stored row `row` names; the number deleted."""
    before = _rows(conn, row.table)
    if row.table == "listings":
        conn.execute(
            "DELETE FROM listings WHERE security_id = ? AND ticker = ? AND exchange = ? "
            "AND valid_from = ? AND known_at = ? AND class_title IS NOT DISTINCT FROM ?",
            [
                row.security_id,
                row.ticker,
                row.exchange,
                row.valid_from,
                row.known_at,
                row.class_title,
            ],
        )
    else:
        conn.execute(
            "DELETE FROM delistings WHERE security_id = ? AND form = ? AND class_title = ? "
            "AND exchange = ? AND filed_at = ? AND known_at = ?",
            [row.security_id, row.form, row.class_title, row.exchange, row.filed_at, row.known_at],
        )
    return before - _rows(conn, row.table)


def delete_master_rows(
    conn: duckdb.DuckDBPyConnection,
    manifest: Sequence[MasterRowKey],
    release: str,
    *,
    dry_run: bool,
    headers: HeaderLookup,
    clock: Callable[[], datetime] = utc_now,
) -> MasterRowsDeleted:
    """Delete the never-true master rows of a reviewed manifest (spec
    amendment 2026-10-14, item 7; #1682 lever B; data-foundation plan T191),
    on `conn`, inside the caller's transaction (`open_for_write`, so a
    refusal or failure rolls everything back); with `dry_run`, check and
    count them and change nothing.

    A stored `listings` or `delistings` row that was never right at any
    instant (a note's listing, or its Form 25, on the common's id) has no
    `known_at` at which a retraction could be stored (the #1613 rule): it is
    deleted, the release's backup being the history. Each manifest row is
    **refused** unless:

    - exactly one stored row has its key and `known_at`, an EDGAR `filing`
      row (never a snapshot row, which no filing produced);
    - every stored revision of its key is in the manifest (a key leaves
      whole, as `delete_bars` deletes every revision);
    - its `accession` is a filing of the security's CIK (the CIK's own, or
      one naming it as the subject company, a 25-NSE) and was accepted no
      later than the row's `known_at`, read from the filing's own SGML
      header (`headers`), so the delete corrects a misreading of what was
      public and never applies knowledge learned later;
    - no open paper window read it: with a window open, the row's
      `known_at` is before every read instant (`restamp_precheck.
      read_instants`; a delete moves the stamp to never, the `ingest.
      _crosses` rule of #1522) and before the window's first session.

    A dry run lists every row found and refused. A real run deletes only a
    manifest with no refused row, only inside the open data release
    `release` (`release_refusal`), and raises `RepairRefused` before
    anything is deleted otherwise; it writes one `ingestion_runs` row
    (source `edgar`, mode `repair`, status `repaired`) with the counts. A
    delete that misses its count raises `RuntimeError`, so the caller's
    transaction rolls back. A delete is not a revision: an as-of read at a
    `t` before a row's `known_at` is unchanged, and one after it no longer
    sees the row (the delete-only rerun rule of
    `docs/runbooks/data-releases.md` applies to the sweeps that read it)."""
    if not release or not release.strip():
        raise ValueError("a master-row delete names its data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    if not dry_run and (why := release_refusal(conn, release)):
        raise RepairRefused(f"{why}; nothing deleted")
    first = window_first_session(conn)
    window = None if first is None else (first, read_instants(conn))
    found: list[str] = []
    refused: list[str] = []
    passing: list[MasterRowKey] = []
    for row in manifest:
        why_not = _master_row_refusal(conn, row, manifest, headers, window)
        if why_not is None:
            passing.append(row)
            found.append(f"  {row.label()}")
        else:
            refused.append(f"  {row.label()}: {why_not}")
    result = MasterRowsDeleted(
        release=release,
        rows=len(manifest),
        listings=sum(r.table == "listings" for r in passing),
        delistings=sum(r.table == "delistings" for r in passing),
        found=tuple(found),
        refused=tuple(refused),
        dry_run=dry_run,
    )
    if dry_run:
        return result
    if refused:
        raise RepairRefused(
            f"{len(refused)} of {len(manifest)} manifest rows refused; nothing deleted:\n"
            + "\n".join(refused)
        )
    deleted = sum(_delete_master_row(conn, row) for row in passing)
    if deleted != len(passing):  # never a partial delete
        raise RuntimeError(
            f"delete removed {deleted} rows where the manifest names {len(passing)}; rolled back"
        )
    run = SourceRun("edgar", REPAIRED, 0, "", result.summary())
    _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result

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
"""

from __future__ import annotations

import functools
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import duckdb

from tradepartner.adapters.alpaca_prices import (
    ACTIONS_SOURCE,
    BAR_SOURCES,
    ListingResolver,
    registrant_evidence,
)
from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _read, _write_run
from tradepartner.store.asof import facts_as_of, listings_as_of
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.master import _first_session, _session_of
from tradepartner.store.registry import open_release
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
    (a renamed, or retracted and restored, row would move the floor past
    the first span and take the lead away). A `known_at` before the
    calendar's first session maps to that session (the calendar raises
    before it, `store.master._first_session`)."""
    at = ensure_tz_aware_utc(at, field_name="at")
    floor = datetime.combine(_first_session(settings), time(12), tzinfo=UTC)
    rows = conn.execute(
        "SELECT security_id, min(known_at) FROM securities WHERE known_at <= ? GROUP BY ALL",
        [at],
    ).fetchall()
    return {
        str(sid): _session_of(max(ensure_tz_aware_utc(known, field_name="known_at"), floor))
        for sid, known in rows
    }


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

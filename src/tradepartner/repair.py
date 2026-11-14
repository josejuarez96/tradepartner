"""Remove the Alpaca rows the resolver no longer assigns to their security (#819).

**Retracted, not deleted (T390, #3275, ADR 0029).** `repair_resolution`,
`delete_bars`, `retract_dividends` and `retract_spinoff_splits` move their rows
out of `prices_daily` and `corporate_actions` into `retracted_rows`
(`store.retracted_rows.retract`) with the command's reason, the open release and
the rule that judged them (for `repair_resolution`, the resolver's listing ends
with their Form 25 and the chains it read; for the others, the range or the
reviewed entry), and none issues a `DELETE` on those tables. The fact tables hold
exactly what a delete would have left, so no reader changes; wherever the text
below says "delete", read "retract". `retract` moves every revision of a key from
any source, so a key that holds another source's revision makes the count differ
from the dry run's and the run rolls back (`RuntimeError`).

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
- **No orphans without the owner's count** (#3265). A bar the resolver does
  not assign to its security is either *moved* (some other security now owns
  one of the symbols the security held that session, so a fill-holes refetch
  puts the bar there) or an *orphan* (no id receives the symbol on that
  session: the deleted bar is lost, as when a one-exchange Form 25 cut Apache
  or Marriott while another line stayed listed). The dry run lists every
  orphan security with its first and last session and its key count. A real
  run refuses, deleting nothing, on any orphan unless `allow_orphans` equals
  the dry run's orphan key count exactly. Classified as the resolver at the
  run (listings, delistings and chains known then); no look-ahead is added.
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
from collections import Counter, defaultdict
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
    alpaca_symbol,
    registrant_evidence,
)
from tradepartner.adapters.filings import FilingHeader
from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, _read, _write_run
from tradepartner.store.asof import (
    facts_as_of,
    listings_as_of,
    live_actions_as_of,
    live_chains,
    symbol_changes_as_of,
)
from tradepartner.store.db import insert_row, open_for_write, utc_now
from tradepartner.store.delistings import delistings_as_of, listing_ends_as_of
from tradepartner.store.dividend_reviews import DUPLICATE, ReviewedGroup, reviewed_groups
from tradepartner.store.master import (
    RenameRefusal,
    derive_rename_listings,
    first_session,
    session_of,
    stored_rename_changes,
    stored_snapshot_history,
)
from tradepartner.store.registry import open_release
from tradepartner.store.restamp_precheck import (
    crossed_instants,
    read_instants,
    window_first_session,
)
from tradepartner.store.retracted_rows import retract
from tradepartner.store.spinoff_splits import SpinoffSplit, spinoff_splits
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
    (#943); while `alpaca.first_span_lead` is on (#974), each security's
    first session (`first_sessions`); the `symbol_changes` known at
    `at` (#1696, `symbol_changes_as_of`), followed where the listings lag
    them; and the live successor chains known at `at` (#821, T187b,
    `asof.live_chains`), whose rule 9 gives a successor the sessions between
    its predecessor's span end and its first listing row."""
    at = ensure_tz_aware_utc(at, field_name="at")
    evidence = registrant_evidence(
        facts_as_of(conn, at).iter_rows(named=True),
        listing_ends_as_of(conn, at, settings).iter_rows(named=True),
        as_of=at.date(),
        quiet_after_days=settings.alpaca.registrant_quiet_days,
        transfer_window_sessions=settings.master.transfer_window_sessions,
        class_symbols=settings.alpaca.class_symbols,
        filed_bars=filed_bars(conn, at),
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
        symbol_changes=_symbol_changes(conn, at),
        chains=live_chains(conn, at),
    )


def filed_bars(conn: duckdb.DuckDBPyConnection, at: datetime) -> dict[tuple[str, date], date]:
    """Per `(security_id, New York day of a Form 25 acceptance)` of the
    `delistings` known at `at`, the session of that security's last traded
    Alpaca bar on or before that day known at `at` (#3056, rule 7's
    stopped-before-filing cut): per session the latest revision known at
    `at`, from an Alpaca source, with volume above zero, as `last_bar`
    reads one. One query for every Form 25; a security with no such bar
    is left out."""
    at = ensure_tz_aware_utc(at, field_name="at")
    filed = delistings_as_of(conn, at)
    if filed.is_empty():
        return {}
    days = (
        filed.select(
            pl.col("security_id"),
            pl.col("filed_at").dt.convert_time_zone("America/New_York").dt.date().alias("filed_on"),
        )
        .unique()
        .sort("security_id", "filed_on")
    )
    conn.register("_filed_days", days)
    try:
        rows = conn.execute(
            "SELECT d.security_id, d.filed_on, max(b.session) FROM _filed_days d"
            " JOIN ("
            "  SELECT security_id, session, volume FROM prices_daily"
            "  WHERE known_at <= ? AND list_contains(?, source)"
            "  AND security_id IN (SELECT security_id FROM _filed_days)"
            "  QUALIFY row_number() OVER"
            "    (PARTITION BY security_id, session ORDER BY known_at DESC) = 1"
            " ) b ON b.security_id = d.security_id AND b.session <= d.filed_on"
            " WHERE b.volume > 0 GROUP BY 1, 2",
            [at, sorted(BAR_SOURCES)],
        ).fetchall()
    finally:
        conn.unregister("_filed_days")
    return {(str(sid), day): last for sid, day, last in rows}


def _symbol_changes(conn: duckdb.DuckDBPyConnection, at: datetime) -> list[dict[str, Any]]:
    """The `symbol_changes` rows known at `at` (#1696); none on a store
    before schema 28, which has no such table (a read-only connection
    cannot migrate it)."""
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    if "symbol_changes" not in tables:
        return []
    return list(symbol_changes_as_of(conn, at).iter_rows(named=True))


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


def _base_id(security_id: str) -> str:
    """The CIK of a `<cik>:<class>` or `<cik>@<date>` security id."""
    return security_id.split(":")[0].split("@")[0]


def orphan_bars(
    resolver: ListingResolver,
    bar_keys: Iterable[Key],
    tickers: Callable[[str, date], Sequence[str]],
) -> dict[Key, int]:
    """The bar keys among `bar_keys` (each one the resolver does not assign
    to its security) that no id receives (#3265): no ticker the security held
    on the session resolves, for a fill or a bar, to another security. A
    *moved* key has such an owner. Returns `{key: 1}` per orphan key. Pure."""
    out: dict[Key, int] = {}
    for sid, day in bar_keys:
        owners = {resolver.fill_resolve(t, day) for t in tickers(sid, day)}
        owners |= {resolver.lead(t, day) for t in tickers(sid, day)}
        if not (owners - {None, sid}):
            out[(sid, day)] = 1
    return out


def _norm(ticker: str) -> str:
    """A listing ticker in the form the resolver reads (upper case, Alpaca's
    class suffix), else the raw ticker."""
    return alpaca_symbol(ticker) or ticker


def _held_tickers(
    conn: duckdb.DuckDBPyConnection, at: datetime
) -> Callable[[str, date], Sequence[str]]:
    """`(security_id, session)` to the tickers the security's listings known
    at `at` carry on that session: those of its latest listing starting on or
    before it, else all of its listings (a bar before its first row)."""
    listed: dict[str, list[tuple[date, str]]] = defaultdict(list)
    live_classes: dict[str, list[tuple[date, str, Any]]] = defaultdict(list)
    for row in listings_as_of(conn, at).iter_rows(named=True):
        listed[row["security_id"]].append((row["valid_from"], _norm(row["ticker"])))
        live_classes[row["security_id"]].append(
            (row["valid_from"], _norm(row["ticker"]), row["class_title"])
        )

    retracted: dict[str, set[str]] = defaultdict(set)  # an id's own rows, retracted too
    for sid, ticker in conn.execute(
        "SELECT security_id, ticker FROM listings WHERE known_at <= ?", [at]
    ).fetchall():
        retracted[str(sid)].add(_norm(str(ticker)))

    # The id's own withdrawn rows (#3290): master-retract moves an id's common
    # ticker to a class or successor id, and a leftover warrant row or a raw
    # lowercase ticker is then all that is live. The bars' receiver sits under
    # the withdrawn ticker.
    withdrawn: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for sid, ticker, valid_from, class_title in conn.execute(
        "SELECT security_id, ticker, valid_from, class_title FROM listings WHERE known_at <= ? "
        "QUALIFY row_number() OVER (PARTITION BY security_id, ticker, exchange, valid_from "
        "ORDER BY known_at DESC, ingested_at DESC) = 1 AND retracted",
        [at],
    ).fetchall():
        norm = _norm(str(ticker))
        # A ticker its own class later replaced is a rename, not a withdrawn
        # common ticker: another id reusing it must not receive this id's bars.
        if any(
            d >= valid_from and t != norm and c == class_title
            for d, t, c in live_classes.get(str(sid), ())
        ):
            continue
        withdrawn[str(sid)].append((valid_from, norm))

    def held(security_id: str, day: date) -> Sequence[str]:
        rows = listed.get(security_id, [])
        if not rows:  # a retracted id: its own retracted listing rows (#3265 review)
            return sorted(retracted.get(security_id, ()))
        started = [r for r in rows if r[0] <= day]
        if not started:
            return sorted({t for _, t in rows})
        latest = max(r[0] for r in started)
        out = {t for d, t in started if d == latest}
        own = [r for r in withdrawn.get(security_id, ()) if r[0] <= day]
        if own:
            last = max(r[0] for r in own)
            out |= {t for d, t in own if d == last}
        return sorted(out)

    return held


@dataclass(frozen=True)
class ChainLine:
    """A live successor chain the run applied (#821, plan T187b): the
    predecessor, its successor, and the continuation ratio of the successor's
    first close to the predecessor's last close (the hand-over's one-share-for-
    one-share ratio; `None` when either has no bar known at the run). The dry
    run lists it so the owner sees why the pair's hole bars are no longer
    proposed for deletion."""

    predecessor_id: str
    successor_id: str
    ratio: float | None


@dataclass(frozen=True)
class RepairResult:
    """What `repair_resolution` found and, unless `dry_run`, deleted."""

    found: Misattributed
    bar_keys_checked: int
    action_keys_checked: int
    dry_run: bool
    unjudged_symbol_changes: int = 0
    unjudged_mergers: int = 0
    chains: tuple[ChainLine, ...] = ()
    #: The bar keys no id receives (#3265): `(security_id, session)` to rows.
    orphans: Mapping[Key, int] = field(default_factory=dict)
    #: Display names by base id, for the orphan lines only.
    names: Mapping[str, str] = field(default_factory=dict)
    #: Rows beyond the counted ones that retracting these keys would also move
    #: (`_wider`); a real run refuses on any.
    wider: tuple[str, ...] = ()

    @property
    def orphan_keys(self) -> int:
        """Bar keys no id receives; a real run needs this exact count passed."""
        return len(self.orphans)

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
        verb = "would retract" if self.dry_run else "retracted"
        return (
            f"{verb} {self.bar_rows} bar rows ({len(self.found.bars)} of "
            f"{self.bar_keys_checked} keys) and {self.action_rows} action rows "
            f"({len(self.found.actions) + len(self.found.spinoffs)} of "
            f"{self.action_keys_checked} keys) that the "
            f"resolver no longer assigns to their security, on {len(named)} securities{shown}; "
            f"{self.unjudged_symbol_changes} symbol_changes rows unjudged; "
            f"{self.unjudged_mergers} mergers rows unjudged; "
            f"{self.orphan_keys} orphan bar keys no id receives on "
            f"{len({sid for sid, _ in self.orphans})} securities"
            + (f"; {len(self.wider)} extra rows held by the same keys" if self.wider else "")
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
        by_sid: dict[str, list[date]] = defaultdict(list)
        for sid, day in self.orphans:
            by_sid[sid].append(day)
        for sid in sorted(by_sid):
            days = by_sid[sid]
            name = self.names.get(_base_id(sid), "?")
            out.append(
                f"  ORPHAN {sid} ({name}): {len(days)} bar keys {min(days)}..{max(days)} "
                "that no id receives"
            )
        out += [f"  WIDER {line}" for line in self.wider]
        for chain in self.chains:
            ratio = "-" if chain.ratio is None else f"{chain.ratio:.4f}"
            out.append(
                f"  chain {chain.predecessor_id}->{chain.successor_id}: "
                f"ratio {ratio} (the successor continues the predecessor)"
            )
        return out


def _chain_lines(conn: duckdb.DuckDBPyConnection, at: datetime) -> tuple[ChainLine, ...]:
    """The live successor chains known at `at` and their hand-over ratio (#821,
    T187b): the successor's first close over the predecessor's last close, read
    from `prices_daily` known at `at` (`None` when either has no bar)."""
    lines = []
    for predecessor, successor in live_chains(conn, at):

        def edge(security_id: str, order: str) -> float | None:
            row = conn.execute(
                f"SELECT close FROM prices_daily WHERE security_id = ? AND known_at <= ? "
                f"ORDER BY session {order} LIMIT 1",
                [security_id, at],
            ).fetchone()
            return None if row is None else float(row[0])

        first, last = edge(successor, "ASC"), edge(predecessor, "DESC")
        ratio = None if first is None or not last else first / last
        lines.append(ChainLine(predecessor, successor, ratio))
    return tuple(lines)


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


def _span(days: Iterable[date]) -> str:
    """`first..last` of a non-empty set of days."""
    ordered = sorted(days)
    return f"{ordered[0]}..{ordered[-1]}"


#: The columns `_select_*` return per row and `_wider` compares. The logical key
#: (`retracted_rows.KEY_COLUMNS`) is the first 2 (bars) or first 4 (actions).
_BAR_COLS = "security_id, session, source, known_at, ingested_at"
_ACTION_COLS = "security_id, source_action_id, action_type, ex_date, source, known_at, ingested_at"


def _cols(table: str) -> tuple[str, int]:
    return (_BAR_COLS, 2) if table == "prices_daily" else (_ACTION_COLS, 4)


def _day_of(table: str, row: tuple[Any, ...]) -> date:
    return row[1] if table == "prices_daily" else row[3]  # type: ignore[no-any-return]


def _select_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    sources: list[str],
    keys: Iterable[Key],
    action_types: Sequence[str] | None = None,
) -> list[tuple[Any, ...]]:
    """Every row of `table` from `sources` (and `action_types`) whose
    `(security_id, <day>)` is in `keys`: what the old delete matched."""
    rows = [[sid, when] for sid, when in keys]
    if not rows:
        return []
    conn.execute("CREATE OR REPLACE TEMP TABLE _repair_keys (security_id VARCHAR, day DATE)")
    conn.executemany("INSERT INTO _repair_keys VALUES (?, ?)", rows)
    extra = " AND list_contains(?, action_type)" if action_types is not None else ""
    try:
        found = conn.execute(
            f"SELECT {_cols(table)[0]} FROM {table} WHERE list_contains(?, source){extra} "
            f"AND (security_id, {day}) IN (SELECT (security_id, day) FROM _repair_keys)",
            [sources, *([list(action_types)] if action_types is not None else [])],
        ).fetchall()
    finally:
        conn.execute("DROP TABLE _repair_keys")
    return [tuple(r) for r in found]


def _select_actions(
    conn: duckdb.DuckDBPyConnection, action_type: str, identities: Iterable[Sequence[Any]]
) -> list[tuple[Any, ...]]:
    """Every `ACTIONS_SOURCE` row of `action_type` whose `(security_id,
    source_action_id)` is an identity, plus (for a spin-off) its ex-date as a
    third entry of the identity, when given."""
    out: list[tuple[Any, ...]] = []
    for identity in identities:
        sid, action_id, *ex = identity
        extra = " AND ex_date = ?" if ex else ""
        out += [
            tuple(r)
            for r in conn.execute(
                f"SELECT {_ACTION_COLS} FROM corporate_actions WHERE source = ? "
                f"AND action_type = ? AND security_id = ? AND source_action_id = ?{extra}",
                [ACTIONS_SOURCE, action_type, sid, action_id, *ex],
            ).fetchall()
        ]
    return out


def _wider(
    conn: duckdb.DuckDBPyConnection, table: str, selected: Sequence[tuple[Any, ...]]
) -> list[str]:
    """Why a retraction would move more than the old delete did (ADR 0029):
    `retract` moves every revision of a logical key whatever its source, and an
    action with a `source_action_id` across ex-dates. One line per extra row, key
    first; empty when the selected rows are everything those keys hold."""
    if not selected:
        return []
    cols, width = _cols(table)
    names = cols.split(", ")[:width]
    keys = list(dict.fromkeys(tuple(r[:width]) for r in selected))
    conn.execute(
        f"CREATE OR REPLACE TEMP TABLE _wider_keys AS SELECT {', '.join(names)} "
        f"FROM {table} LIMIT 0"
    )
    try:
        conn.executemany(f"INSERT INTO _wider_keys VALUES ({', '.join('?' for _ in names)})", keys)
        if table == "prices_daily":
            cond = "t.security_id = k.security_id AND t.session = k.session"
        else:
            cond = (
                "t.security_id = k.security_id AND t.source_action_id = k.source_action_id "
                "AND (k.source_action_id <> '' OR (t.action_type = k.action_type "
                "AND t.ex_date = k.ex_date))"
            )
        matched = conn.execute(
            f"SELECT {', '.join('t.' + c for c in cols.split(', '))} FROM {table} t "
            f"WHERE EXISTS (SELECT 1 FROM _wider_keys k WHERE {cond})"
        ).fetchall()
    finally:
        conn.execute("DROP TABLE IF EXISTS _wider_keys")
    extra = sorted({tuple(r) for r in matched} - {tuple(r) for r in selected}, key=str)
    return [
        f"{table} key {tuple(map(str, r[:width]))} also holds a {r[width]} revision "
        f"known {r[width + 1]} (ex-date {_day_of(table, r)})"
        if table != "prices_daily"
        else f"{table} key {tuple(map(str, r[:width]))} also holds a {r[width]} revision "
        f"known {r[width + 1]}"
        for r in extra
    ]


def _refuse_wider(wider: Sequence[str]) -> None:
    """Raise `RepairRefused`, naming the keys, when a retraction would take more
    than the dry run counted."""
    if wider:
        listed = "\n".join(f"  {line}" for line in wider[:20])
        more = f"\n  and {len(wider) - 20} more" if len(wider) > 20 else ""
        raise RepairRefused(
            f"{len(wider)} extra rows would be retracted with the counted keys (a retraction "
            f"moves every revision of a key, whatever its source or ex-date); nothing "
            f"retracted, review them:\n{listed}{more}"
        )


def _retract_grouped(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    selected: Sequence[tuple[Any, ...]],
    rules: Callable[[tuple[Any, ...]], str],
    *,
    reason: str,
    release: str,
) -> int:
    """Retract (ADR 0029, `store.retracted_rows.retract`) the logical keys of the
    `selected` rows, one call per distinct rule `rules(key)` gives, so every moved
    row records the rule that judged it; the rows moved."""
    width = _cols(table)[1]
    by_rule: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for key in dict.fromkeys(tuple(r[:width]) for r in selected):
        by_rule[rules(key)].append(key)
    return sum(
        retract(conn, table, group, reason, release, rule)
        for rule, group in sorted(by_rule.items())
    )


def _wider_spinoffs(
    conn: duckdb.DuckDBPyConnection, keys: Sequence[tuple[str, date, str]]
) -> list[str]:
    """`_wider` for `_delete_spinoffs`'s keys."""
    return _wider(
        conn, "corporate_actions", _select_actions(conn, "spinoff", [(c, p, e) for c, e, p in keys])
    )


def _delete_spinoffs(
    conn: duckdb.DuckDBPyConnection,
    keys: Sequence[tuple[str, date, str]],
    *,
    reason: str,
    release: str,
    rule: Callable[[str, date, str], str],
) -> int:
    """Retract the `spinoff` rows from `ACTIONS_SOURCE` whose `(child, ex_date,
    parent)` is in `keys`, never another parent's row of the same child and
    day, with `rule(child, ex_date, parent)` as the rule that judged them (the
    rules of one identity's ex-dates joined: its revisions move together); the
    number retracted."""
    selected = _select_actions(conn, "spinoff", [(c, p, e) for c, e, p in keys])
    if not selected:
        return 0
    by_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    for child, ex_date, parent in sorted(keys):
        by_identity[(child, parent)].append(rule(child, ex_date, parent))
    return _retract_grouped(
        conn,
        "corporate_actions",
        selected,
        lambda key: " | ".join(by_identity[(str(key[0]), str(key[1]))]),
        reason=reason,
        release=release,
    )


def _wider_delete(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    sources: list[str],
    keys: Iterable[Key],
    action_types: Sequence[str] | None = None,
) -> list[str]:
    """`_wider` for `_delete`'s keys."""
    return _wider(conn, table, _select_rows(conn, table, day, sources, keys, action_types))


def _delete(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    sources: list[str],
    keys: Iterable[Key],
    action_types: Sequence[str] | None = None,
    *,
    reason: str,
    release: str,
    rule: Callable[[str, Sequence[date]], str],
) -> int:
    """Retract every row of `table` from `sources` whose `(security_id,
    <day>)` is in `keys` (ADR 0029: moved to `retracted_rows`, not deleted),
    with `rule(security_id, days)` as the rule that judged each security's
    keys; the number retracted."""
    selected = _select_rows(conn, table, day, sources, keys, action_types)
    days_of: dict[str, set[date]] = defaultdict(set)
    for row in selected:
        days_of[str(row[0])].add(_day_of(table, row))
    return _retract_grouped(
        conn,
        table,
        selected,
        lambda key: rule(str(key[0]), sorted(days_of[str(key[0])])),
        reason=reason,
        release=release,
    )


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


def refuse_inside_open_window(
    conn: duckdb.DuckDBPyConnection, affected_sessions: Iterable[date]
) -> None:
    """Raise `RepairRefused` when an open paper window's first session
    (`restamp_precheck.window_first_session`) is on or before any of
    `affected_sessions`, the dates of the rows a repair or backfill is about
    to rewrite, retract or add back-dated (#3330). The window's runs read the
    store from that session on, so a past read must not move under it (the
    rule `delete_master_rows` applies per row). No window open, or no affected
    session, passes. Call it on the write connection before any write."""
    first = window_first_session(conn)
    if first is None:
        return
    inside = sorted({d for d in affected_sessions if d >= first})
    if inside:
        raise RepairRefused(
            f"an open paper window (first session {first}) reads the store: {len(inside)} "
            f"affected dates from {inside[0]} are inside it; nothing written, wait for the "
            "window to close"
        )


def known_dates(instants: Iterable[datetime]) -> list[date]:
    """The New York dates of `instants`, for `refuse_inside_open_window`."""
    return [
        ensure_tz_aware_utc(i, field_name="known_at").astimezone(_NEW_YORK).date() for i in instants
    ]


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return 0 if row is None else int(row[0])


def _resolution_rules(
    conn: duckdb.DuckDBPyConnection, at: datetime, settings: Settings
) -> Callable[[str, str, Sequence[date]], str]:
    """`rule(what, security_id, days)`: the text `retracted_rows.rule` keeps for a
    key the resolver at `at` does not assign (ADR 0029): the tickers the id held
    on those days, and the listing ends (with their Form 25) and successor chains
    the resolver read for the id, which judged the key outside its span."""
    ends: dict[str, list[str]] = defaultdict(list)
    for row in listing_ends_as_of(conn, at, settings).iter_rows(named=True):
        form = f" ({row['delisting_form']})" if row.get("delisting_form") else ""
        ends[str(row["security_id"])].append(
            f"{row['ticker']} {row['exchange']} from {row['valid_from']} "
            f"{row.get('status')} end {row.get('end_session')}{form}"
        )
    chains: dict[str, list[str]] = defaultdict(list)
    for predecessor, successor in live_chains(conn, at):
        for sid in (predecessor, successor):
            chains[sid].append(f"{predecessor}->{successor}")
    held = _held_tickers(conn, at)

    def rule(what: str, security_id: str, days: Sequence[date]) -> str:
        tickers = sorted({t for day in days for t in held(security_id, day)})
        return (
            f"resolver at {at.isoformat()}: no ticker of {security_id} "
            f"({', '.join(tickers) or 'none'}) resolves to it on {what} {_span(days)}; "
            f"listing ends: {'; '.join(ends.get(security_id, [])) or 'none'}; "
            f"chains: {', '.join(chains.get(security_id, [])) or 'none'}"
        )

    return rule


def repair_resolution(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: tuple[int, int] | None = None,
    release: str | None = None,
    allow_orphans: int | None = None,
) -> RepairResult:
    """Delete the Alpaca bars and actions that the resolver built as of
    `clock()` does not assign to their security, and record the run (module
    docstring); with `dry_run`, find and count them on a read-only
    connection and change nothing.

    A real run deletes only what a dry run showed: `expect` is that dry
    run's `(bar_rows, action_rows)`, and any other count raises
    `RepairRefused` before anything is deleted. It runs only inside the open
    data release `release` (`release_refusal`), else `RepairRefused`. A bar
    key no id receives (`orphan_bars`, #3265) is deleted only when
    `allow_orphans` equals the orphan key count exactly, else `RepairRefused`
    before anything is deleted. Raises `StoreLockedError` like ingest when
    the store stays locked."""
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
            raise RepairRefused(f"{why}; nothing retracted")
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
        wider = tuple(
            _wider_delete(conn, "prices_daily", "session", bar_sources, found_bars)
            + _wider_delete(
                conn,
                "corporate_actions",
                "ex_date",
                [ACTIONS_SOURCE],
                ordinary,
                ("split", "dividend"),
            )
            + _wider_spinoffs(conn, bad_spins)
        )
        orphans = orphan_bars(resolver, found_bars, _held_tickers(conn, started))
        names = {
            _base_id(str(sid)): str(name)
            for sid, name in conn.execute(
                "SELECT security_id, name FROM securities QUALIFY "
                "row_number() OVER (PARTITION BY security_id ORDER BY known_at DESC) = 1"
            ).fetchall()
            if name is not None
        }
        unjudged_row = conn.execute("SELECT count(*) FROM symbol_changes").fetchone()
        assert unjudged_row is not None
        unjudged = int(unjudged_row[0])
        mergers_row = conn.execute("SELECT count(*) FROM mergers").fetchone()
        assert mergers_row is not None
        result = RepairResult(
            found=found,
            bar_keys_checked=bars.seen,
            action_keys_checked=actions.seen + len(spin_rows),
            dry_run=dry_run,
            unjudged_symbol_changes=unjudged,
            unjudged_mergers=int(mergers_row[0]),
            chains=_chain_lines(conn, started),
            orphans=orphans,
            names=names,
            wider=wider,
        )
        if not dry_run:
            if result.orphan_keys != (allow_orphans or 0):
                raise RepairRefused(
                    f"{result.orphan_keys} bar keys on "
                    f"{len({sid for sid, _ in orphans})} securities are received by no id, "
                    f"and --allow-orphans is {allow_orphans}; the owner's exact count is "
                    "required to retract them; nothing retracted, run --dry-run and read the "
                    "ORPHAN lines"
                )
            if (result.bar_rows, result.action_rows) != expect:
                raise RepairRefused(
                    f"found {result.bar_rows} bar rows and {result.action_rows} action rows, "
                    f"not the expected {expect}; nothing retracted, run --dry-run again"
                )
            _refuse_wider(wider)
            refuse_inside_open_window(
                conn,
                [d for _, d in (*found.bars, *found.actions, *found.spinoffs)],
            )
            assert release is not None  # checked above
            why_rule = _resolution_rules(conn, started, settings)
            reason = f"repair-resolution at {started.isoformat()}: the resolver no longer assigns"
            moved = (
                _delete(
                    conn,
                    "prices_daily",
                    "session",
                    bar_sources,
                    found.bars,
                    reason=reason,
                    release=release,
                    rule=lambda sid, days: why_rule("session", sid, days),
                ),
                _delete(
                    conn,
                    "corporate_actions",
                    "ex_date",
                    [ACTIONS_SOURCE],
                    found.actions,
                    action_types=("split", "dividend"),
                    reason=reason,
                    release=release,
                    rule=lambda sid, days: why_rule(
                        "the session before ex-date", sid, [previous_session(d) for d in days]
                    ),
                )
                + _delete_spinoffs(
                    conn,
                    bad_spins,
                    reason=reason,
                    release=release,
                    rule=lambda child, ex_date, parent: (
                        why_rule("ex-date", child, [ex_date])
                        + f"; parent {parent}: "
                        + why_rule(
                            "the session before ex-date", parent, [previous_session(ex_date)]
                        )
                    ),
                ),
            )
            if moved != (result.bar_rows, result.action_rows):  # never a partial repair
                raise RuntimeError(
                    f"repair retracted {moved} rows where it counted "
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
    #: Rows beyond the counted ones that retracting these keys would also move
    #: (`_wider`); a real run refuses on any.
    wider: tuple[str, ...] = ()

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would retract" if self.dry_run else "retracted"
        span = (
            f" on {len(self.bar_sessions)} sessions {self.bar_sessions[0]}..{self.bar_sessions[-1]}"
            if self.bar_sessions
            else ""
        )
        dates = f" (ex-dates {', '.join(map(str, self.ex_dates))})" if self.ex_dates else ""
        return (
            f"{verb} {self.bar_rows} bar rows{span} and {self.action_rows} action rows{dates} "
            f"of {self.security_id} in {self.sessions_from}..{self.sessions_to}"
            + (f"; {len(self.wider)} extra rows held by the same keys" if self.wider else "")
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
            raise RepairRefused(f"{why}; nothing retracted")
        known = conn.execute(  # a typo'd id is refused, not a quiet 0-row run
            "SELECT 1 FROM securities WHERE security_id = $id "
            "UNION ALL SELECT 1 FROM listings WHERE security_id = $id LIMIT 1",
            {"id": security_id},
        ).fetchone()
        if known is None:
            raise RepairRefused(f"the store holds no security {security_id!r}; nothing retracted")
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
            wider=tuple(
                _wider_delete(
                    conn,
                    "prices_daily",
                    "session",
                    sorted(BAR_SOURCES),
                    ((security_id, day) for day in sessions),
                )
                + _wider_delete(
                    conn,
                    "corporate_actions",
                    "ex_date",
                    [ACTIONS_SOURCE],
                    ((security_id, day) for day in ex_dates),
                )
            ),
        )
        if not dry_run:
            if (bar_rows, action_rows) != expect:
                raise RepairRefused(
                    f"found {bar_rows} bar rows and {action_rows} action rows, not the "
                    f"expected {expect}; nothing retracted, run --dry-run again"
                )
            _refuse_wider(result.wider)
            refuse_inside_open_window(conn, (*sessions, *ex_dates))
            assert release is not None  # checked above
            reason = f"repair-bars {security_id} {sessions_from}..{sessions_to}"
            rule = (
                f"repair-bars --security-id {security_id} --from {sessions_from} "
                f"--to {sessions_to}: the owner's reviewed range (contaminated, rule 8)"
            )
            moved = (
                _delete(
                    conn,
                    "prices_daily",
                    "session",
                    sorted(BAR_SOURCES),
                    ((security_id, day) for day in sessions),
                    reason=reason,
                    release=release,
                    rule=lambda sid, days: rule,
                ),
                _delete(
                    conn,
                    "corporate_actions",
                    "ex_date",
                    [ACTIONS_SOURCE],
                    ((security_id, day) for day in ex_dates),
                    reason=reason,
                    release=release,
                    rule=lambda sid, days: rule,
                ),
            )
            if moved != (bar_rows, action_rows):  # never a partial repair
                raise RuntimeError(
                    f"repair retracted {moved} rows where it counted "
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
    #: Rows beyond the counted ones that retracting these keys would also move
    #: (`_wider`); a real run refuses on any.
    wider: tuple[str, ...] = ()

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would retract" if self.dry_run else "retracted"
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
            + (f"; {len(self.wider)} extra rows held by the same keys" if self.wider else "")
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
            raise RepairRefused(f"{why}; nothing retracted")
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
            wider=tuple(
                _wider_identities(
                    conn, [(e.security_id, e.retract_source_action_id) for e in found]
                )
            ),
        )
        if not dry_run:
            if stale:
                raise RepairRefused(
                    f"{len(stale)} stale list entries; nothing retracted, re-review them on "
                    "#1613:\n" + "\n".join(stale)
                )
            if result.action_rows != expect:
                raise RepairRefused(
                    f"found {result.action_rows} action rows, not the expected {expect}; "
                    "nothing retracted, run --dry-run again"
                )
            _refuse_wider(result.wider)
            refuse_inside_open_window(conn, result.ex_dates)
            assert release is not None  # checked above
            deleted = _delete_identities(
                conn,
                [(e.security_id, e.retract_source_action_id) for e in found],
                reason="repair-dividends: duplicate cash dividend",
                release=release,
                rules={
                    (e.security_id, e.retract_source_action_id): (
                        f"reviewed duplicate {e.security_id} {e.ex_date} amount {e.amount!r} "
                        f"({e.ticker or 'no ticker'}): retract {e.retract_source_action_id}"
                    )
                    for e in found
                },
            )
            if deleted != result.action_rows:  # never a partial repair
                raise RuntimeError(
                    f"repair retracted {deleted} rows where it counted {result.action_rows}; "
                    "rolled back"
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("alpaca", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


def _wider_identities(
    conn: duckdb.DuckDBPyConnection, identities: Sequence[tuple[str, str]]
) -> list[str]:
    """`_wider` for `_delete_identities`'s identities."""
    return _wider(conn, "corporate_actions", _select_actions(conn, "dividend", identities))


def _delete_identities(
    conn: duckdb.DuckDBPyConnection,
    identities: Sequence[tuple[str, str]],
    *,
    reason: str,
    release: str,
    rules: Mapping[tuple[str, str], str],
) -> int:
    """Retract every `ACTIONS_SOURCE` dividend row whose `(security_id,
    source_action_id)` is in `identities` (ADR 0029: moved to `retracted_rows`,
    not deleted), each with `rules[identity]` as the entry that judged it; the
    number retracted."""
    selected = _select_actions(conn, "dividend", identities)
    if not selected:
        return 0
    return _retract_grouped(
        conn,
        "corporate_actions",
        selected,
        lambda key: rules[(str(key[0]), str(key[1]))],
        reason=reason,
        release=release,
    )


# --- #3058: the reviewed delete of split rows that are spin-offs ---


@dataclass(frozen=True)
class SpinoffSplitsRetracted:
    """What `retract_spinoff_splits` found for the reviewed list and, unless
    `dry_run`, deleted (`DividendsRetracted`'s shape)."""

    #: The list's entries.
    entries: int
    #: Entries whose identity still has rows, none stale.
    identities: int
    #: The rows (every revision) those identities hold.
    action_rows: int
    #: Entries whose identity has no row at all.
    already_repaired: int
    #: One line per stale entry (refuses a real run).
    stale: tuple[str, ...]
    #: The distinct ex-dates of the identities found.
    ex_dates: tuple[date, ...]
    dry_run: bool
    #: Rows beyond the counted ones that retracting these keys would also move
    #: (`_wider`); a real run refuses on any.
    wider: tuple[str, ...] = ()

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would retract" if self.dry_run else "retracted"
        span = (
            f" on {len(self.ex_dates)} ex-dates {self.ex_dates[0]}..{self.ex_dates[-1]}"
            if self.ex_dates
            else ""
        )
        return (
            f"{verb} {self.action_rows} spin-off split rows of {self.identities} identities"
            f"{span}; entries {self.entries}, already repaired {self.already_repaired}, "
            f"stale {len(self.stale)}"
            + (f"; {len(self.wider)} extra rows held by the same keys" if self.wider else "")
        )


def _spinoff_split_stale(rows: list[tuple[Any, ...]], entry: SpinoffSplit) -> str | None:
    """Why one entry may not be deleted, or None. `rows` are every row of the
    identity, oldest first, as `(source, action_type, ex_date,
    ratio_or_amount, cancelled)`. Every row must be an `ACTIONS_SOURCE`
    split, and the latest must hold the reviewed ex-date and ratio (exact
    `DOUBLE` equality): a source that changed the event since the review
    needs a new review, never a silent delete."""
    for source, action_type, *_ in rows:
        if source != ACTIONS_SOURCE or action_type != "split":
            return f"the identity holds a {source} {action_type} row"
    _, _, ex_date, ratio, _ = rows[-1]
    if ex_date != entry.ex_date or ratio != entry.ratio:
        return (
            f"the latest row is {ex_date} {ratio!r}, "
            f"not the reviewed {entry.ex_date} {entry.ratio!r}"
        )
    return None


def retract_spinoff_splits(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: int | None = None,
    release: str | None = None,
    reviewed: Sequence[SpinoffSplit] | None = None,
) -> SpinoffSplitsRetracted:
    """Delete every row of each reviewed spin-off split identity (`reviewed`,
    default `store.spinoff_splits.spinoff_splits()`): every revision of
    `(security_id, source_action_id)`, in one transaction, and record the run
    (#3058); with `dry_run`, count them on a read-only connection and change
    nothing.

    The rows are deleted, not cancelled: the split never happened, so every
    read at every `t` must stop applying it, and only a back-dated change does
    that (a cancel from the clock on, `action-retract`, leaves every past read
    as it was). That moves the data vintage, so it runs only inside a named
    data release, like `retract_dividends`. Ingest refuses a listed identity
    with its reviewed values from then on
    (`AlpacaPriceSource.corporate_actions`).

    An entry whose identity has no row is `already_repaired`. An entry is
    stale (`_spinoff_split_stale`) when a row of the identity is not an
    `ACTIONS_SOURCE` split or the latest row's ex-date or ratio differs from
    the reviewed one; a dry run lists the stale entries, a real run refuses on
    any. A real run deletes only the dry run's row count (`expect`) and only
    inside the open data release `release`; anything else raises
    `RepairRefused` before anything is deleted. Raises `StoreLockedError` like
    ingest when the store stays locked."""
    if not dry_run and expect is None:
        raise ValueError("a repair deletes only the counts of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("a repair writes only inside a named data release: pass release")
    entries = list(spinoff_splits() if reviewed is None else reviewed)
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        if release is not None and not dry_run and (why := release_refusal(conn, release)):
            raise RepairRefused(f"{why}; nothing retracted")
        already = rows_found = 0
        found: list[SpinoffSplit] = []
        stale: list[str] = []
        for entry in entries:
            rows = conn.execute(
                "SELECT source, action_type, ex_date, ratio_or_amount, cancelled "
                "FROM corporate_actions WHERE security_id = ? AND source_action_id = ? "
                "ORDER BY known_at, ingested_at",
                [entry.security_id, entry.source_action_id],
            ).fetchall()
            if not rows:
                already += 1
                continue
            if (why := _spinoff_split_stale(rows, entry)) is not None:
                stale.append(
                    f"  {entry.security_id} {entry.ex_date} {entry.ratio!r} "
                    f"{entry.source_action_id} ({entry.ticker or 'no ticker'}): {why}"
                )
                continue
            found.append(entry)
            rows_found += len(rows)
        result = SpinoffSplitsRetracted(
            entries=len(entries),
            identities=len(found),
            action_rows=rows_found,
            already_repaired=already,
            stale=tuple(stale),
            ex_dates=tuple(sorted({e.ex_date for e in found})),
            dry_run=dry_run,
            wider=tuple(_wider_split_identities(conn, [e.identity for e in found])),
        )
        if not dry_run:
            if stale:
                raise RepairRefused(
                    f"{len(stale)} stale list entries; nothing retracted, re-review them on "
                    "#3058:\n" + "\n".join(stale)
                )
            if result.action_rows != expect:
                raise RepairRefused(
                    f"found {result.action_rows} action rows, not the expected {expect}; "
                    "nothing retracted, run --dry-run again"
                )
            _refuse_wider(result.wider)
            refuse_inside_open_window(conn, result.ex_dates)
            assert release is not None  # checked above
            deleted = _delete_split_identities(
                conn,
                [e.identity for e in found],
                reason="repair-spinoff-splits: a spin-off recorded as a split",
                release=release,
                rules={
                    e.identity: (
                        f"reviewed spin-off split {e.security_id} {e.ex_date} ratio "
                        f"{e.ratio!r} ({e.ticker or 'no ticker'}): {e.source_action_id}"
                    )
                    for e in found
                },
            )
            if deleted != result.action_rows:  # never a partial repair
                raise RuntimeError(
                    f"repair retracted {deleted} rows where it counted {result.action_rows}; "
                    "rolled back"
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("alpaca", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


def _wider_split_identities(
    conn: duckdb.DuckDBPyConnection, identities: Sequence[tuple[str, str]]
) -> list[str]:
    """`_wider` for `_delete_split_identities`'s identities."""
    return _wider(conn, "corporate_actions", _select_actions(conn, "split", identities))


def _delete_split_identities(
    conn: duckdb.DuckDBPyConnection,
    identities: Sequence[tuple[str, str]],
    *,
    reason: str,
    release: str,
    rules: Mapping[tuple[str, str], str],
) -> int:
    """Retract every `ACTIONS_SOURCE` split row whose `(security_id,
    source_action_id)` is in `identities` (ADR 0029: moved to `retracted_rows`,
    not deleted), each with `rules[identity]` as the entry that judged it; the
    number retracted."""
    selected = _select_actions(conn, "split", identities)
    if not selected:
        return 0
    return _retract_grouped(
        conn,
        "corporate_actions",
        selected,
        lambda key: rules[(str(key[0]), str(key[1]))],
        reason=reason,
        release=release,
    )


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
        seen: set[tuple[object, ...]] = set()
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
            key = (row.table, *_key(row), row.known_at)
            if key in seen:
                raise ValueError(f"{path.name} line {line}: listed twice: {row.label()}")
            seen.add(key)
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
        verb = "would retract" if self.dry_run else "retracted"
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
        return f"{row.accession} does not read as CIK {cik}'s filing: {error}"
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


def _key(row: MasterRowKey) -> tuple[object, ...]:
    """`row`'s stored key without `known_at`: the table's UNIQUE columns."""
    if row.table == "listings":
        return (row.security_id, row.ticker, row.exchange, row.valid_from)
    return (row.security_id, row.form, row.class_title, row.exchange, row.filed_at)


def _same_key(a: MasterRowKey, b: MasterRowKey) -> bool:
    """True when `a` and `b` are revisions of one stored key."""
    return a.table == b.table and _key(a) == _key(b)


def _retract_master_rows(
    conn: duckdb.DuckDBPyConnection, rows: Sequence[MasterRowKey], *, reason: str, release: str
) -> int:
    """Retract (ADR 0029, `store.retracted_rows.retract`) the logical keys of
    `rows`, each key once with every revision, its manifest lines (the revisions'
    `label()`s, joined) as the rule that judged it; the rows moved."""
    by_key: dict[tuple[str, tuple[object, ...]], list[str]] = defaultdict(list)
    for row in rows:
        by_key[(row.table, _key(row))].append(row.label())
    moved = 0
    for table in MASTER_DELETE_TABLES:
        by_rule: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        for (name, key), labels in by_key.items():
            if name == table:
                by_rule[" | ".join(sorted(labels))].append(key)
        for rule, keys in sorted(by_rule.items()):
            moved += retract(conn, table, keys, reason, release, rule)
    return moved


def delete_master_rows(
    conn: duckdb.DuckDBPyConnection,
    manifest: Sequence[MasterRowKey],
    release: str,
    *,
    dry_run: bool,
    headers: HeaderLookup,
    clock: Callable[[], datetime] = utc_now,
) -> MasterRowsDeleted:
    """Retract the never-true master rows of a reviewed manifest (spec
    amendment 2026-10-14, item 7; #1682 lever B; data-foundation plan T191),
    on `conn`, inside the caller's transaction (`open_for_write`, so a
    refusal or failure rolls everything back); with `dry_run`, check and
    count them and change nothing. The rows move into `retracted_rows`
    (ADR 0029, #3275, T392) with the reason, the release and the key's
    manifest lines as the rule, and `restore` puts them back; the tables
    hold what a delete would have left.

    A stored `listings` or `delistings` row that was never right at any
    instant (a note's listing, or its Form 25, on the common's id) has no
    `known_at` at which a `retracted` revision could be stored (the #1613
    rule): it is retracted into `retracted_rows`, every revision of its key
    with it. Each manifest row is
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
    anything is retracted otherwise; it writes one `ingestion_runs` row
    (source `edgar`, mode `repair`, status `repaired`) with the counts. A
    retraction that misses its count raises `RuntimeError`, so the caller's
    transaction rolls back. A retraction is not a revision: an as-of read at a
    `t` before a row's `known_at` is unchanged, and one after it no longer
    sees the row (the delete-only rerun rule of
    `docs/runbooks/data-releases.md` applies to the sweeps that read it)."""
    if not release or not release.strip():
        raise ValueError("a master-row delete names its data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    if not dry_run and (why := release_refusal(conn, release)):
        raise RepairRefused(f"{why}; nothing retracted")
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
            f"{len(refused)} of {len(manifest)} manifest rows refused; nothing retracted:\n"
            + "\n".join(refused)
        )
    moved = _retract_master_rows(
        conn,
        passing,
        reason="delete-master-rows: a master row never right at any instant",
        release=release,
    )
    if moved != len(passing):  # never a partial retraction
        raise RuntimeError(
            f"retraction moved {moved} rows where the manifest names {len(passing)}; rolled back"
        )
    run = SourceRun("edgar", REPAIRED, 0, "", result.summary())
    _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result


@dataclass(frozen=True)
class RenameListingsRepaired:
    """What `repair_rename_listings` found and, unless `dry_run`, wrote (#3057)."""

    #: Stored name changes judged.
    changes: int
    #: Listing rows added: `(security_id, ticker, exchange, valid_from, known_at)`.
    rows: tuple[tuple[str, str, str, date, datetime], ...]
    refusals: tuple[RenameRefusal, ...]
    dry_run: bool

    def summary(self) -> str:
        """One line for the run row and the terminal."""
        verb = "would add" if self.dry_run else "added"
        reasons = Counter(r.reason for r in self.refusals)
        refused = ", ".join(f"{why} {n}" for why, n in sorted(reasons.items())) or "none"
        return (
            f"{verb} {len(self.rows)} new-ticker listing rows of {self.changes} name changes; "
            f"refused: {refused}"
        )

    def lines(self) -> list[str]:
        """One line per row and per refusal, for the dry run's review."""
        out = [
            f"  + {sid} {ticker} {exchange} from {valid_from} known {known_at.isoformat()}"
            for sid, ticker, exchange, valid_from, known_at in self.rows
        ]
        out += [
            f"  - {r.security_id} {r.old_symbol}->{r.new_symbol}: {r.reason}" for r in self.refusals
        ]
        return out


def repair_rename_listings(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    expect: int | None = None,
    release: str | None = None,
) -> RenameListingsRepaired:
    """Add the new-ticker `listings` rows the stored `symbol_changes` imply
    (`store.master.derive_rename_listings`, #3057: PKI -> RVTY, CHK -> EXE),
    one `snapshot` row each, in one transaction, and record the run; with
    `dry_run`, derive them on a read-only connection and change nothing.

    A real run writes only the dry run's row count (`expect`) and only inside
    the open data release `release`; anything else raises `RepairRefused`
    before a row is written. The rows are those the nightly EDGAR master
    derives itself, so a later run adds nothing to them."""
    if not dry_run and expect is None:
        raise ValueError("a repair writes only the count of a dry run: pass expect")
    if not dry_run and release is None:
        raise ValueError("a repair writes only inside a named data release: pass release")
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    opened: AbstractContextManager[duckdb.DuckDBPyConnection] = (
        _read(settings) if dry_run else open_for_write(settings)
    )
    with opened as conn:
        if release is not None and not dry_run and (why := release_refusal(conn, release)):
            raise RepairRefused(f"{why}; nothing written")
        changes = stored_rename_changes(conn, started)
        live = conn.execute(
            "SELECT security_id, ticker, exchange, valid_from, known_at FROM listings "
            "QUALIFY NOT retracted AND row_number() OVER (PARTITION BY security_id, ticker, "
            "exchange, valid_from ORDER BY known_at DESC) = 1"
        ).fetchall()
        stored = [
            {
                "security_id": sid,
                "ticker": ticker,
                "exchange": exchange,
                "valid_from": valid_from,
                "known_at": known_at,
            }
            for sid, ticker, exchange, valid_from, known_at in live
        ]
        derived, refusals = derive_rename_listings(
            stored, stored_snapshot_history(conn, started), changes, ingested_at=started
        )
        result = RenameListingsRepaired(
            changes=len(changes),
            rows=tuple(
                (r.security_id, r.ticker, r.exchange, r.valid_from, r.known_at) for r in derived
            ),
            refusals=refusals,
            dry_run=dry_run,
        )
        if not dry_run:
            if len(derived) != expect:
                raise RepairRefused(
                    f"found {len(derived)} rows, not the expected {expect}; "
                    "nothing written, run --dry-run again"
                )
            for rename in derived:
                insert_row(
                    conn,
                    "listings",
                    {
                        "security_id": rename.security_id,
                        "ticker": rename.ticker,
                        "exchange": rename.exchange,
                        "class_title": None,
                        "valid_from": rename.valid_from,
                        "known_at": rename.known_at,
                        "ingested_at": started,
                        "source": "edgar",
                        "provenance": "snapshot",
                    },
                )
            message = f"{result.summary()}; release {release}"
            run = SourceRun("edgar", REPAIRED, 0, "", message)
            _write_run(conn, uuid.uuid4().hex, started, clock(), run, REPAIR)
    return result

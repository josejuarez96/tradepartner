"""Backfill and resume (spec req 9, open question 2; plan T17).

`backfill(settings, prices=..., filings=..., since=...)` fills the store
from `since` to the expected session (`ingest.expected_session`), source by
source, `edgar` then `alpaca`, halting at the first chunk that is not `ok`:

- **`edgar`** is one chunk over **full history** whatever `since` is: the
  filing index and Forms 25/25-NSE are never limited (spec req 3 and open
  question 2), and the builders need every filing to stamp `known_at`
  right. Shares facts are not limited either: a fact filed before `since`
  can be the latest one known at a T inside the window (universe rule 7).
  It reuses the single-session chunk (`ingest._ingest_filings`).
- **`alpaca`** is one chunk per **calendar month** (`month_windows`), each
  fetching bars and actions for its window. The store is read through a
  short-lived read-only connection, the source is called with no
  connection open, and the write lock is taken only to commit the month:
  another process can use the store between chunks and while a month is
  being fetched. The store is read as of the clock when the month starts,
  and rows are stamped with the clock once its fetch returns. A month is
  stale if the reference symbol lacks a bar on any of its sessions, or if
  more than `ingest.max_missing_share` of the listed benchmark names and
  common names on one of `universe.exchanges` live through the month have
  no bar in it. A common name whose every listing live in the month is
  `snapshot_static` and that has no bar in it is named in the run message
  with its own count, not counted (#784); so is any other name with no bar
  in this month or the previous one (bars already committed), unless it is
  a benchmark or first listed this month, or the store has no bar at all in
  the previous month.

The EDGAR chunk is fetched with no store connection open (a recording
pass) and committed in one short write transaction.

Each chunk writes one `ingestion_runs` row with mode `backfill`. A month's
`chunk_cursor` is `since=<since>;through=<last day of the window>`, so a
re-run with the same `since` **resumes** the day after the latest `ok`
chunk: a failed month is fetched again, committed months are not. Rows are
written by `ingest`'s rules (only what changes an as-of read), so a
repeated window, or a daily run over a backfilled session, adds nothing.
Resume freezes committed months: a listing a later EDGAR run adds for an
earlier month (a better snapshot match, #35) gets its bars only from a
backfill with a new, earlier `since`, or from `fill_holes`.

**Holes** (#831). `fill_holes(settings, prices=..., since=...)` refetches
the committed months of the backfill for `since` (before `_resume_from`)
where a security the code now counts as fetched in the month
(`_window_names` at the clock, so a corrected listing end or a new listing
row counts) has no bar known at the clock: a stale listing end once kept
the backfill from fetching it (KKR 2018-08..2019-07). A dry run lists
those (security, month) holes and fetches nothing. A real run takes the
months in order, re-reading the holes as of the clock when each starts,
and fetches only that month's holes plus the reference symbol, through the
same chunk as the backfill: the store read before the fetch with no
connection open, the reference-gap and missing-share staleness rules
(over the stored bars and the fetched ones together), rows written by
`ingest`'s rules (a first-seen bar keeps the timing rule's `known_at`, the
fetch's clock is `ingested_at`, an unchanged bar adds nothing, absence is
never a withdrawal) and the month's rows committed in one transaction. Each
month it fetches writes its own `ingestion_runs` row: mode `holes`, cursor
`holes;since=<since>;through=<last day>` (never a resume point for
`backfill`), status `filled` when committed. `filled` is never `ok`, so a
hole fill never counts as a fresh ingest for health, the cockpit or
execution. It halts at the first month that is not `filled`. A re-run reads
the holes again: filled months drop out, a hole the source still cannot
fill is fetched again.

A hole counts only if a fetched bar could land on it (#876): the store's
resolver (`repair.store_resolver` at the clock the holes are read, the
resolver an ingest run builds) holds the id on some session of the month
under a ticker that is an Alpaca symbol (`alpaca_symbol`). The others are
not fetched, and are counted per reason (`UNASSIGNED`, `NOT_ALPACA`): on
the dry run's summary and in each month's run message. `securities`
limits the holes to the named ids (a targeted refetch, the cursor, status
and halt rules unchanged); a named id with no hole to fetch is listed with
why (`NamedSecurity`), never an error.

**One benchmark** (#840): `backfill_benchmark(settings, prices=...,
symbol=..., since=...)` is the owner's one-off for a configured benchmark
the store lacks (MTUM is in no EDGAR snapshot, so the master never seeds
it). It refuses a symbol outside `benchmarks`. If no benchmark security
lists the symbol it seeds one first, from the owner's `BenchmarkSeed`
(cik, name, exchange): `BENCH:<symbol>`, `benchmark = TRUE`, source
`config`, a `snapshot_static` listing from the calendar's first session
and an `etf` classification (rule `benchmark_config`), all stamped at the
clock, like the master's own benchmark rows (`store.classify` builds only
from the master, which never holds this one). It refuses a `since` that
leaves no session to fetch, before writing anything, and it refuses
when another security holds the ticker or `BENCH:<symbol>` is taken.
Then it fetches that one security's bars and actions month by month
from `since` to the expected session, written by `ingest`'s rules (bars
keep their session-close `known_at`; a repeat adds nothing). A month with
any session lacking a bar is stale and halts the run with nothing written
for it. It writes **no** `ingestion_runs` row: an `ok` row is read as a
fresh store by the paper-run and dashboard freshness checks, and this run
refreshes one series only; its outcome is printed instead.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from tradepartner.adapters.alpaca_prices import ListingResolver, alpaca_symbol
from tradepartner.adapters.filings import FilingSource
from tradepartner.adapters.prices import PriceSource
from tradepartner.calendar import is_session, next_session
from tradepartner.config import Settings
from tradepartner.ingest import (
    FAILED,
    LOCKED,
    OK,
    SOURCES,
    STALE,
    STATIC,
    IngestResult,
    SourceRun,
    _add_actions,
    _add_rows,
    _bar_row,
    _clean,
    _counted,
    _fetched,
    _ingest_filings,
    _may_count,
    _prefetch,
    _read,
    _record_only,
    _Recorded,
    _replay_actions,
    _replay_plan,
    _reported_note,
    _run_source,
    _Stale,
    _staleness,
    _types_known,
    _unwrap,
    _with_frames,
    _write_run,
    expected_session,
)
from tradepartner.repair import store_resolver
from tradepartner.store.asof import listings_as_of
from tradepartner.store.benchmarks import (
    BenchmarkIdentityError,
    benchmark_candidates,
    ticker_holders,
)
from tradepartner.store.classify import EQUITY, classifications_as_of, listing_kind
from tradepartner.store.db import StoreLockedError, insert_row, open_for_write, utc_now
from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import _first_session, securities_as_of
from tradepartner.store.schema import init_schema
from tradepartner.timeutil import ensure_tz_aware_utc

BACKFILL = "backfill"
#: `fill_holes`' run mode, and the status of a month it committed (never `ok`).
HOLES = "holes"
FILLED = "filled"
#: Why a hole is not fetched (#876): no session of the month on which the
#: resolver assigns the id anything, or only tickers with no Alpaca symbol.
UNASSIGNED = "the resolver assigns it no session"
NOT_ALPACA = "held only under a ticker that is not an Alpaca symbol"
#: Why a named security (`securities`) has no hole to fetch.
UNKNOWN = "unknown id: no listing known"
NO_HOLE = "no hole in a committed month"


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """`[start, end]` cut at calendar-month boundaries, dropping windows
    with no XNYS session."""
    windows: list[tuple[date, date]] = []
    first = start
    while first <= end:
        after = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        last = min(after - timedelta(days=1), end)
        if is_session(first) or next_session(first) <= last:
            windows.append((first, last))
        first = after
    return windows


def backfill(
    settings: Settings,
    *,
    prices: PriceSource,
    filings: FilingSource,
    since: date,
    source: str = "all",
    clock: Callable[[], datetime] = utc_now,
) -> IngestResult:
    """Backfill from `since` (see the module docstring); resumes a previous
    backfill with the same `since` after its last committed month."""
    if source not in ("all", *SOURCES):
        raise ValueError(f"source must be 'all' or one of {SOURCES}, got {source!r}")
    if isinstance(since, datetime) or not isinstance(since, date):
        raise TypeError(f"since must be a date, got {since!r}")
    now = ensure_tz_aware_utc(clock(), field_name="clock()")
    runs: list[SourceRun] = []
    if source in ("all", "edgar"):
        recorded = _Recorded(filings)
        run = _run_source(
            "edgar",
            lambda conn: _ingest_filings(conn, settings, recorded, clock),
            settings,
            now,
            clock,
            f"since={since.isoformat()}",
            dry_run=False,
            mode=BACKFILL,
            prepare=lambda: _prefetch(recorded, settings, dry_run=False),
            after_commit=getattr(_unwrap(filings), "record_failures", None),
        )
        runs.append(run)
        if run.status != OK:
            return IngestResult(tuple(runs))
    if source in ("all", "alpaca"):
        try:
            start = _resume_from(settings, since)
        except StoreLockedError as exc:
            run = SourceRun(
                "alpaca", LOCKED, 0, f"since={since.isoformat()}", _clean(str(exc), settings)
            )
            return IngestResult((*runs, run))
        for window in month_windows(start, expected_session(now, settings)):
            chunk = _price_chunk(settings, prices, since, window, clock)
            if chunk is None:  # only a hole fill skips a month
                continue
            runs.append(chunk)
            if chunk.status != OK:
                break
    return IngestResult(tuple(runs))


@dataclass(frozen=True)
class Hole:
    """A month in which `security_id` is fetched but the store has no bar.

    `ticker` is its listing's on the month's last day (else its first);
    `between` is whether it has a stored bar before and after the month."""

    security_id: str
    ticker: str | None
    window: tuple[date, date]
    between: bool


@dataclass(frozen=True)
class NamedSecurity:
    """A security named to `fill_holes` that it fetched no hole of, and why
    (`UNKNOWN`, `NO_HOLE`, `HALTED`, or its holes not fetched per reason)."""

    security_id: str
    reason: str


@dataclass(frozen=True)
class HoleFill:
    """`fill_holes`' outcome: the holes a dry run found, or the runs of a
    real one (one per month fetched, halted after one not `filled`); the
    holes not fetched, per reason (`UNASSIGNED`, `NOT_ALPACA`); and the
    named securities with no hole fetched."""

    holes: tuple[Hole, ...]
    runs: tuple[SourceRun, ...]
    dry_run: bool
    dropped: tuple[tuple[str, int], ...] = ()
    named: tuple[NamedSecurity, ...] = ()

    @property
    def exit_code(self) -> int:
        """0 when every month fetched was `filled` (or none was), else 1."""
        return 0 if all(run.status == FILLED for run in self.runs) else 1

    def summary(self) -> str:
        """One line: how many holes, over how many securities and months."""
        securities = {hole.security_id for hole in self.holes}
        months = {hole.window for hole in self.holes}
        between = [hole for hole in self.holes if hole.between]
        return (
            f"{len(self.holes)} holes (security, month) over {len(securities)} securities "
            f"in {len(months)} months; {len(between)} of them between a security's stored "
            f"bars ({len({hole.security_id for hole in between})} securities)"
            f"{self.dropped_note()}"
        )

    def dropped_note(self) -> str:
        """`"; N holes the resolver cannot assign, not fetched (...)"`, the
        count per reason, or `""` when none was dropped."""
        return _dropped_note(dict(self.dropped))

    def lines(self) -> list[str]:
        """One line per security, months grouped into runs of consecutive
        months; those with holes between stored bars first."""
        by_security: dict[str, list[Hole]] = defaultdict(list)
        for hole in self.holes:
            by_security[hole.security_id].append(hole)

        def order(sid: str) -> tuple[int, str]:
            return (-sum(hole.between for hole in by_security[sid]), sid)

        out = []
        for sid in sorted(by_security, key=order):
            holes = sorted(by_security[sid], key=lambda hole: hole.window)
            spans: list[list[date]] = []
            for hole in holes:
                month = hole.window[0].replace(day=1)
                if spans and _next_month(spans[-1][-1]) == month:
                    spans[-1].append(month)
                else:
                    spans.append([month])
            shown = ", ".join(
                _month(span[0]) if len(span) == 1 else f"{_month(span[0])}..{_month(span[-1])}"
                for span in spans
            )
            between = sum(hole.between for hole in holes)
            out.append(
                f"  {sid} {holes[0].ticker or '-'}: {shown} ({len(holes)} months, "
                f"{between} between its stored bars)"
            )
        out.extend(f"  {named.security_id}: not fetched, {named.reason}" for named in self.named)
        return out


HALTED = "not filled: the fill halted first"


def _dropped_note(dropped: dict[str, int]) -> str:
    if not dropped:
        return ""
    reasons = ", ".join(f"{n} {reason}" for reason, n in sorted(dropped.items()))
    return f"; {sum(dropped.values())} holes the resolver cannot assign, not fetched ({reasons})"


@dataclass
class _Tally:
    """The securities whose holes a fill listed (dry run) or committed (a
    `filled` month), and the holes it dropped per security and reason."""

    fetched: set[str] = field(default_factory=set)
    dropped: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, fetched: Collection[str], dropped: dict[str, str]) -> None:
        self.fetched.update(fetched)
        for sid, reason in dropped.items():
            self.dropped[sid][reason] += 1

    def totals(self) -> tuple[tuple[str, int], ...]:
        total: Counter[str] = Counter()
        for reasons in self.dropped.values():
            total.update(reasons)
        return tuple(sorted(total.items()))


def _named(
    only: frozenset[str] | None, known: set[str], tally: _Tally, *, halted: bool
) -> tuple[NamedSecurity, ...]:
    """The named ids `tally` fetched no hole of, each with why."""
    out = []
    for sid in sorted(only or ()):
        if sid in tally.fetched:
            continue
        if sid not in known:
            reason = UNKNOWN
        elif reasons := tally.dropped.get(sid):
            shown = ", ".join(f"{n} {reason}" for reason, n in sorted(reasons.items()))
            reason = f"{sum(reasons.values())} holes not fetched ({shown})"
        else:
            reason = HALTED if halted else NO_HOLE
        out.append(NamedSecurity(sid, reason))
    return tuple(out)


def _next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def _month(month: date) -> str:
    return month.strftime("%Y-%m")


def fill_holes(
    settings: Settings,
    *,
    prices: PriceSource,
    since: date,
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
    securities: Collection[str] | None = None,
) -> HoleFill:
    """Refetch the holes in the committed months of the backfill for
    `since` (the module docstring's **Holes**), only those of `securities`
    when given; with `dry_run`, list them and fetch nothing."""
    if isinstance(since, datetime) or not isinstance(since, date):
        raise TypeError(f"since must be a date, got {since!r}")
    if isinstance(securities, str):
        raise TypeError("securities must be a collection of ids, not one string")
    only = None if securities is None else frozenset(securities)
    if only is not None and not only:
        raise ValueError("securities, when given, must name at least one id")
    cursor = f"{HOLES};since={since.isoformat()}"
    try:
        end = _resume_from(settings, since)
    except StoreLockedError as exc:
        run = SourceRun("alpaca", LOCKED, 0, cursor, _clean(str(exc), settings))
        return HoleFill((), (run,), dry_run)
    windows = month_windows(since, end - timedelta(days=1)) if end > since else []
    tally = _Tally()
    if dry_run:
        try:
            t = ensure_tz_aware_utc(clock(), field_name="clock()")
            holes, known = _holes(settings, windows, t, only, tally)
        except StoreLockedError as exc:
            run = SourceRun("alpaca", LOCKED, 0, cursor, _clean(str(exc), settings))
            return HoleFill((), (run,), dry_run)
        tally.fetched.update(hole.security_id for hole in holes)
        named = _named(only, known, tally, halted=False)
        return HoleFill(tuple(holes), (), dry_run, tally.totals(), named)
    known = set()
    if only is not None:
        try:
            with _read(settings) as conn:
                at = ensure_tz_aware_utc(clock(), field_name="clock()")
                known = set(listings_as_of(conn, at)["security_id"].to_list())
        except StoreLockedError as exc:
            run = SourceRun("alpaca", LOCKED, 0, cursor, _clean(str(exc), settings))
            return HoleFill((), (run,), dry_run)
    runs: list[SourceRun] = []
    halted = False
    for window in windows:
        chunk = _price_chunk(
            settings, prices, since, window, clock, fill=True, only=only, tally=tally
        )
        if chunk is None:
            continue
        runs.append(chunk)
        if chunk.status != FILLED:
            halted = True
            break
    named = _named(only, known, tally, halted=halted)
    return HoleFill((), tuple(runs), dry_run, tally.totals(), named)


def _sessions_in(window: tuple[date, date]) -> list[date]:
    first, last = window
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return [day for day in days if is_session(day)]


def _assignable(
    resolver: ListingResolver, sids: list[str], window: tuple[date, date]
) -> tuple[list[str], dict[str, str]]:
    """`sids` split into those a fetched bar could land on in `window` and
    the rest with why: kept when on some session the resolver resolves one
    of the id's tickers to it (`ListingResolver.holds`) and that ticker is
    an Alpaca symbol (else the fetch never sends it, `alpaca_symbol`)."""
    sessions = _sessions_in(window)
    keep: list[str] = []
    dropped: dict[str, str] = {}
    for sid in sids:
        reason: str | None = UNASSIGNED
        if resolver.symbols(sid, window[0], window[1]):
            for day in sessions:
                held = [
                    t for t in resolver.symbols(sid, day, day) if resolver.resolve(t, day) == sid
                ]
                if any(alpaca_symbol(t) is not None for t in held):
                    reason = None
                    break
                if held:
                    reason = NOT_ALPACA
        if reason is None:
            keep.append(sid)
        else:
            dropped[sid] = reason
    return keep, dropped


def _holes(
    settings: Settings,
    windows: list[tuple[date, date]],
    t: datetime,
    only: frozenset[str] | None = None,
    tally: _Tally | None = None,
) -> tuple[list[Hole], set[str]]:
    """Every hole in `windows` as of `t` that a fetched bar could land on
    (of the `only` ids, when given), a short read per month; the dropped
    ones are added to `tally`. Also the ids with a listing known at `t`."""
    if not windows and only is None:
        return [], set()
    with _read(settings) as conn:
        tickers: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for row in listings_as_of(conn, t).iter_rows(named=True):
            tickers[row["security_id"]].append((row["valid_from"], row["ticker"]))
        if not windows:
            return [], set(tickers)
        resolver = store_resolver(conn, t, settings)
        spans = {
            sid: (lo, hi)
            for sid, lo, hi in conn.execute(
                "SELECT security_id, min(session), max(session) FROM prices_daily "
                "WHERE known_at <= ? GROUP BY ALL",
                [t],
            ).fetchall()
        }
    holes: list[Hole] = []
    for window in windows:
        first, last = window
        with _read(settings) as conn:
            ids, *_ = _window_names(conn, t, window, settings)
            stored = _with_bars(conn, t, window)
        missing = [sid for sid in ids if sid not in stored and (only is None or sid in only)]
        kept, dropped = _assignable(resolver, missing, window)
        if tally is not None:
            tally.add((), dropped)
        for sid in kept:
            rows = sorted(tickers.get(sid, []))
            live = [ticker for start, ticker in rows if start <= last]
            ticker = live[-1] if live else (rows[0][1] if rows else None)
            lo, hi = spans.get(sid, (None, None))
            between = lo is not None and hi is not None and lo < first and hi > last
            holes.append(Hole(sid, ticker, window, between))
    return holes, set(tickers)


def _with_bars(conn: duckdb.DuckDBPyConnection, t: datetime, window: tuple[date, date]) -> set[str]:
    """Securities with a bar known at `t` on a session in `window`."""
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT security_id FROM prices_daily "
            "WHERE session BETWEEN ? AND ? AND known_at <= ?",
            [*window, t],
        ).fetchall()
    }


def _cursor(since: date, through: date) -> str:
    return f"since={since.isoformat()};through={through.isoformat()}"


def _resume_from(settings: Settings, since: date) -> date:
    """The day after the latest `ok` backfill month for `since`, else `since`."""
    if not Path(settings.store.path).exists():
        return since
    prefix = f"since={since.isoformat()};through="
    with _read(settings) as conn:
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        if "ingestion_runs" not in tables:
            return since
        row = conn.execute(
            "SELECT max(chunk_cursor) FROM ingestion_runs WHERE source = 'alpaca' "
            "AND mode = ? AND status = ? AND starts_with(chunk_cursor, ?)",
            [BACKFILL, OK, prefix],
        ).fetchone()
    if row is None or row[0] is None:
        return since
    return date.fromisoformat(row[0].removeprefix(prefix)) + timedelta(days=1)


def _price_chunk(
    settings: Settings,
    prices: PriceSource,
    since: date,
    window: tuple[date, date],
    clock: Callable[[], datetime],
    *,
    fill: bool = False,
    only: frozenset[str] | None = None,
    tally: _Tally | None = None,
) -> SourceRun | None:
    """One month. The store is read as of the clock when the month starts
    (after the EDGAR chunk committed) and rows are stamped with the clock
    after the fetch returns, so a revision is never dated before it was
    fetched. With `fill` (a hole fill), only the names with no bar known
    then (of the `only` ids, when given) that a fetched bar could land on
    (`_assignable`, the store's resolver then) are fetched, with the
    reference; the dropped ones are counted in the message and `tally`; the
    stored bars count toward the staleness share; and a month with no hole
    to fetch is skipped (`None`)."""
    first, last = window
    mode, done = (HOLES, FILLED) if fill else (BACKFILL, OK)
    cursor = f"{HOLES};{_cursor(since, last)}" if fill else _cursor(since, last)
    run_id = uuid.uuid4().hex
    started = ensure_tz_aware_utc(clock(), field_name="clock()")

    def outcome(status: str, rows: int, message: str) -> SourceRun:
        return SourceRun("alpaca", status, rows, cursor, _clean(message, settings))

    try:
        with _read(settings) as conn:
            ids, listed, static_only, may_count, reference = _window_names(
                conn, started, window, settings
            )
            stored = _with_bars(conn, started, window) if fill else set()
            holes = [sid for sid in ids if sid not in stored and (only is None or sid in only)]
            dropped: dict[str, str] = {}
            if fill and holes:
                holes, dropped = _assignable(store_resolver(conn, started, settings), holes, window)
        if fill:
            if tally is not None:
                tally.add((), dropped)
            if not holes:
                return None
            ids = sorted({*holes, *([reference] if reference is not None else [])})
        symbol = settings.ingest.reference_symbol
        if reference is None:
            raise LookupError(f"reference symbol {symbol} has no listing in {first}..{last}")
        bars = [b for b in prices.bars(ids, first, last) if first <= b.session <= last]
        actions = prices.corporate_actions(ids, first, last)
        with _read(settings) as conn:
            plan = _replay_plan(conn, actions, window)
        # A re-dated id's replay, fetched with no store connection open.
        actions, covered = _replay_actions(prices, actions, window, plan)
        ingested_at = ensure_tz_aware_utc(clock(), field_name="clock()")
        have = {bar.session for bar in bars if bar.security_id == reference}
        days = (first + timedelta(days=n) for n in range((last - first).days + 1))
        gaps = [day for day in days if is_session(day) and day not in have]
        if gaps:
            shown = ", ".join(day.isoformat() for day in gaps[:10])
            raise _Stale(f"reference symbol {symbol} ({reference}) has no bar for {shown}")
        with_bars = {bar.security_id for bar in bars} | stored
        counted, missing, reported = _staleness(listed, with_bars, static_only, may_count)
        share = len(missing) / len(counted) if counted else 0.0
        limit = settings.ingest.max_missing_share
        if share > limit:
            raise _Stale(
                f"{len(missing)} of {len(counted)} listed names ({share:.1%}, over {limit:.1%}) "
                f"have no bar in {first}..{last}: {', '.join(missing[:10])}"
                f"{_reported_note(reported)}"
            )
        with open_for_write(settings) as conn:
            init_schema(conn)
            added = _add_rows(
                conn,
                "prices_daily",
                [_bar_row(bar, ingested_at) for bar in bars],
                ingested_at=ingested_at,
                current=True,
                where="AND session BETWEEN ? AND ?",
                params=[first, last],
            )
            added += _add_actions(conn, actions, window, ingested_at=ingested_at, covered=covered)
            filled = (
                f"holes of {len(holes)} names with no stored bar"
                f"{_dropped_note(dict(Counter(dropped.values())))}: "
                if fill
                else ""
            )
            message = (
                f"{filled}{len(bars)} bars and {len(actions)} actions for {len(ids)} names; "
                f"{len(missing)} of {len(counted)} listed names without a bar"
                f"{_reported_note(reported)}"
            )
            if resolution := prices.resolution_summary():
                message += f"; {resolution}"
            run = outcome(done, added, message)
            _write_run(conn, run_id, started, clock(), run, mode)
        if fill and tally is not None:
            tally.fetched.update(holes)  # committed: a halted month's names stay listed
        return run
    except StoreLockedError as exc:
        return outcome(LOCKED, 0, str(exc))
    except _Stale as exc:
        run = outcome(STALE, 0, str(exc))
    except Exception as exc:  # any source or parse failure halts with a run row
        run = outcome(FAILED, 0, _with_frames(f"{type(exc).__name__}: {exc}", exc, settings))
    return _record_only(settings, run_id, started, clock, run, mode)


def _window_names(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    window: tuple[date, date],
    settings: Settings,
) -> tuple[list[str], list[str], set[str], set[str] | None, str | None]:
    """From listings known at `t`: securities with a `_fetched` listing
    live at some point in `window` that a fetched bar could land on (#875:
    an equity row by `listing_kind`, not superseded before the window by a
    later row of its security; benchmarks exempt), and the reference (to
    fetch), the
    benchmark names and the common names
    on one of `universe.exchanges` listed through the whole window,
    including any delisted only later (the staleness denominator), those
    of them (benchmarks never) whose every listing live in the window is
    `snapshot_static`, `_may_count` over the previous calendar month, and
    the reference symbol's `security_id`.

    A listing counts from its `valid_from`; a delisted or transferred one
    still counts while its end session (last bar known) or `effective_on`
    is inside or after the window, or while no end is known yet. For the
    fetch, as for the price resolver, a row also ends where the security's
    next later row starts: a row superseded on or before the window's first
    day fetches nothing, whatever its own status (an older listing on
    another exchange with no end, #875). The staleness denominator is not
    changed by either rule.
    """
    first, last = window
    kinds = {
        row["security_id"]: row["security_type"]
        for row in classifications_as_of(conn, t).iter_rows(named=True)
    }
    benchmarks = {
        row["security_id"]
        for row in securities_as_of(conn, t).iter_rows(named=True)
        if row["benchmark"]
    }
    types = _types_known(conn, t)
    ids: set[str] = set()
    listed: set[str] = set()
    filed: set[str] = set()  # with a filing-based listing live in the window
    reference = None
    earliest: dict[str, date] = {}
    rows = list(listing_ends_as_of(conn, t, settings).iter_rows(named=True))
    starts: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        starts[row["security_id"]].add(row["valid_from"])
    for row in rows:
        sid, status = row["security_id"], row["status"]
        earliest[sid] = min(row["valid_from"], earliest.get(sid, row["valid_from"]))
        if row["valid_from"] > last:
            continue
        end, effective = row["end_session"], row["effective_on"]
        ended = status in (DELISTED, TRANSFERRED) and (
            end is not None and end < first and (effective is None or effective < first)
        )
        if ended or status not in (LISTED, DELISTED, TRANSFERRED):
            continue
        superseded = any(row["valid_from"] < start <= first for start in starts[sid])
        assignable = sid in benchmarks or (
            listing_kind(row["ticker"], row["class_title"]) == EQUITY and not superseded
        )
        if assignable and _fetched(sid, row, benchmarks, types, settings):
            ids.add(sid)
        if row["provenance"] != STATIC:
            filed.add(sid)
        live = status == LISTED or (status == TRANSFERRED and (end is None or end >= last))
        through = live or (status == DELISTED and effective is not None and effective > last)
        if (
            through
            and row["valid_from"] <= first
            and _counted(sid, row, benchmarks, kinds, settings)
        ):
            listed.add(sid)  # today's status must not drop a name delisted later
        if live and row["ticker"] == settings.ingest.reference_symbol:
            reference = sid
    if reference is not None:
        ids.add(reference)
    static_only = listed - filed - benchmarks
    before = first - timedelta(days=1)
    may_count = _may_count(conn, t, (before.replace(day=1), before), earliest, benchmarks)
    return sorted(ids), sorted(listed), static_only, may_count, reference


# --- one benchmark (#840) -----------------------------------------------------

_CIK = re.compile(r"\d{10}")
#: `store.classify`'s rule for a config-seeded benchmark.
_BENCH_RULE = "benchmark_config"


@dataclass(frozen=True)
class BenchmarkSeed:
    """The master facts for a benchmark the store lacks, given by the owner."""

    cik: str
    name: str
    exchange: str

    def __post_init__(self) -> None:
        if not _CIK.fullmatch(self.cik):
            raise ValueError(f"cik must be a 10-digit zero-padded string, got {self.cik!r}")
        for field_name in ("name", "exchange"):
            value = getattr(self, field_name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} must be non-blank and trimmed, got {value!r}")


@dataclass(frozen=True)
class BenchmarkBackfill:
    """`backfill_benchmark`'s outcome: the security, whether it was seeded, and one
    `SourceRun` per month fetched (none is written to `ingestion_runs`)."""

    symbol: str
    security_id: str
    seeded: bool
    runs: tuple[SourceRun, ...]

    @property
    def ok(self) -> bool:
        return all(run.status == OK for run in self.runs)

    @property
    def exit_code(self) -> int:
        """0 when every month is `ok`, else 1."""
        return 0 if self.ok else 1


def backfill_benchmark(
    settings: Settings,
    *,
    prices: PriceSource,
    symbol: str,
    since: date,
    seed: BenchmarkSeed | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> BenchmarkBackfill:
    """Seed (if missing) and backfill one configured benchmark; see the module
    docstring. Raises `ValueError` (before any fetch) for a symbol outside
    `benchmarks`, a missing benchmark with no `seed`, or a taken identity."""
    if symbol not in settings.benchmarks:
        raise ValueError(
            f"{symbol!r} is not a configured benchmark ({', '.join(settings.benchmarks)})"
        )
    if isinstance(since, datetime) or not isinstance(since, date):
        raise TypeError(f"since must be a date, got {since!r}")
    now = ensure_tz_aware_utc(clock(), field_name="clock()")
    if since < _first_session(settings):
        raise ValueError(f"since {since} is before the calendar's first session")
    windows = month_windows(since, expected_session(now, settings))
    if not windows:
        raise ValueError(f"since {since} leaves no session to fetch")
    with open_for_write(settings) as conn:
        init_schema(conn)
        security_id, seeded = _seed_benchmark(conn, settings, symbol, seed, now)
    runs: list[SourceRun] = []
    for window in windows:
        run = _benchmark_chunk(settings, prices, security_id, since, window, clock)
        runs.append(run)
        if run.status != OK:
            break
    return BenchmarkBackfill(symbol, security_id, seeded, tuple(runs))


def _seed_benchmark(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    symbol: str,
    seed: BenchmarkSeed | None,
    now: datetime,
) -> tuple[str, bool]:
    """The one benchmark security listing `symbol`, seeded from `seed` if none does."""
    found = benchmark_candidates(conn, symbol)
    if len(found) > 1:
        raise BenchmarkIdentityError(
            f"benchmark {symbol} is ambiguous: securities {', '.join(found)} all list it"
        )
    if found:
        return found[0], False
    if seed is None:
        raise ValueError(
            f"benchmark {symbol} is not in the store; give its cik, name and exchange to seed it"
        )
    security_id = f"BENCH:{symbol}"
    start = _first_session(settings)
    holders = ticker_holders(conn, symbol, start=start, through=None)
    taken = conn.execute(
        "SELECT count(*) FROM securities WHERE security_id = ?", [security_id]
    ).fetchone()
    if holders or (taken is not None and taken[0]):
        raise BenchmarkIdentityError(
            f"benchmark {symbol}: cannot seed {security_id}: "
            + (f"ticker held by {', '.join(holders)}" if holders else "the id is taken")
        )
    common = {"known_at": now, "ingested_at": now, "source": "config", "provenance": STATIC}
    insert_row(
        conn,
        "securities",
        {"security_id": security_id, "cik": seed.cik, "name": seed.name, "benchmark": True}
        | common,
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": security_id,
            "ticker": symbol,
            "exchange": seed.exchange,
            "class_title": None,
            "valid_from": start,
        }
        | common,
    )
    # As `store.classify` does for a seeded benchmark: an `etf` by config.
    insert_row(
        conn,
        "classifications",
        {"security_id": security_id, "sic": None, "security_type": "etf", "rule": _BENCH_RULE}
        | common,
    )
    return security_id, True


def _benchmark_chunk(
    settings: Settings,
    prices: PriceSource,
    security_id: str,
    since: date,
    window: tuple[date, date],
    clock: Callable[[], datetime],
) -> SourceRun:
    """One month of one benchmark: stale (nothing written) unless every session
    in `window` has a bar."""
    first, last = window
    cursor = f"benchmark={security_id};{_cursor(since, last)}"

    def outcome(status: str, rows: int, message: str) -> SourceRun:
        return SourceRun("alpaca", status, rows, cursor, _clean(message, settings))

    try:
        ids = [security_id]
        bars = [
            b
            for b in prices.bars(ids, first, last)
            if b.security_id == security_id and first <= b.session <= last
        ]
        actions = [a for a in prices.corporate_actions(ids, first, last) if a.security_id in ids]
        with _read(settings) as conn:
            plan = _replay_plan(conn, actions, window)
        actions, covered = _replay_actions(prices, actions, window, plan)
        ingested_at = ensure_tz_aware_utc(clock(), field_name="clock()")
        have = {bar.session for bar in bars}
        days = (first + timedelta(days=n) for n in range((last - first).days + 1))
        gaps = [day for day in days if is_session(day) and day not in have]
        if gaps:
            shown = ", ".join(day.isoformat() for day in gaps[:10])
            return outcome(STALE, 0, f"{security_id} has no bar for {len(gaps)} sessions: {shown}")
        with open_for_write(settings) as conn:
            added = _add_rows(
                conn,
                "prices_daily",
                [_bar_row(bar, ingested_at) for bar in bars],
                ingested_at=ingested_at,
                current=True,
                where="AND session BETWEEN ? AND ?",
                params=[first, last],
            )
            added += _add_actions(conn, actions, window, ingested_at=ingested_at, covered=covered)
        return outcome(OK, added, f"{len(bars)} bars and {len(actions)} actions")
    except StoreLockedError as exc:
        return outcome(LOCKED, 0, str(exc))
    except Exception as exc:  # any source or parse failure halts the run
        return outcome(FAILED, 0, _with_frames(f"{type(exc).__name__}: {exc}", exc, settings))

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
  the previous month. The names so reported (dark plus snapshot-only) make
  the month stale when they are more than `ingest.max_dark_share` of the
  listed names (#796).

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
the dry run's summary and in each month's run message. A ticker that only
leads to the id (a rename lead, #843) counts, as it does for the bars
(#891).

**Lead gaps** (#891). A month in which a security has stored bars is also
a hole when a rename lead (or, #974, a first-span lead;
`ListingResolver.lead`, under an Alpaca symbol) assigns the security a
session of it with no bar known at the clock and none later on a session
the lead assigns it: the gap from the old symbol's last bar to the first
cover page naming the new one starts or ends inside a month (FB -> META,
June and July 2022). A missing session the old symbol traded after (a halt
inside the lead window) is no rename gap: the new symbol has no bar there
to fill it. The whole month is refetched like any hole, with the old and
the new symbol (`symbols`); `parse_bars` takes a new-symbol row only where
the security has no row of its own that session, and `ingest`'s rules add
nothing for an unchanged stored bar, so no session is stored twice and
each bar keeps its session-close `known_at`. A gap with no lead (a session
after the cover page) is no hole, as before. `securities` limits the holes
to the named ids (a targeted refetch, the cursor, status and halt rules
unchanged); a named id with no hole to fetch is listed with why
(`NamedSecurity`), never an error.

**First-span lead months** (#974). Every month (a backfill or a fill)
reads the store's resolver (`repair.store_resolver`) and fetches, besides
the listed names, every security whose first-span lead assigns it a
session of the month under an Alpaca symbol, before its first
ticker-bearing listing row, when that row passes `_fetched` (an OTC or
skipped-type first span is not fetched). `since` bounds the fetch as for
every month, so the lead reaches the backfill start at most. Such a
security is never in the staleness denominator there: no listing of it
is known in the month. `fill_holes` lists its empty months as holes and
the partial month at the lead's end as a lead gap, either kind of lead
counting (`Hole.rename_gap`). With `alpaca.first_span_lead` off nothing
changes.

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
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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
    _dark_stale,
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
            lambda conn, run_id: _ingest_filings(conn, settings, recorded, clock, run_id),
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
    """A month in which `security_id` is fetched but the store has no bar,
    or (`rename_gap`, #891) has bars but none on a session a lead assigns
    it: a lead gap, of either kind (a rename lead, #843, or a first-span
    lead, #974, whose partial month at the window's end is one).

    `ticker` is its listing's on the month's last day (else its first);
    `between` is whether it has a stored bar before and after the month;
    `handover` (#1314, rule 8) is a month meeting a rule-8 window of it
    (`ListingResolver.handovers`), refetched with its own `asof` request
    and landed on it (`ListingResolver.fill_resolve`)."""

    security_id: str
    ticker: str | None
    window: tuple[date, date]
    between: bool
    rename_gap: bool = False
    handover: bool = False  # #1314: a month meeting a rule-8 window of it


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
        gaps = sum(hole.rename_gap for hole in self.holes)
        renamed = f"; {gaps} of them lead gaps in a month with stored bars" if gaps else ""
        return (
            f"{len(self.holes)} holes (security, month) over {len(securities)} securities "
            f"in {len(months)} months; {len(between)} of them between a security's stored "
            f"bars ({len({hole.security_id for hole in between})} securities)"
            f"{renamed}{self.dropped_note()}"
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
            gaps = sum(hole.rename_gap for hole in holes)
            renamed = f", {gaps} lead gaps" if gaps else ""
            out.append(
                f"  {sid} {holes[0].ticker or '-'}: {shown} ({len(holes)} months, "
                f"{between} between its stored bars{renamed})"
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
    the rest with why: kept when on some session one of the id's tickers
    resolves to it or leads to it (a rename lead, #843/#891: bars take the
    lead, `ListingResolver.holds`) and that ticker is an Alpaca symbol (else
    the fetch never sends it, `alpaca_symbol`)."""
    sessions = _sessions_in(window)
    keep: list[str] = []
    dropped: dict[str, str] = {}
    for sid in sids:
        reason: str | None = UNASSIGNED
        if resolver.symbols(sid, window[0], window[1]):
            for day in sessions:
                held = [t for t in resolver.symbols(sid, day, day) if _lands(resolver, t, sid, day)]
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


def _lands(resolver: ListingResolver, ticker: str, sid: str, day: date) -> bool:
    """True when a bar of `ticker` on `day` is assigned `sid`: it resolves
    to it, or it leads to it (#843)."""
    return resolver.resolve(ticker, day) == sid or resolver.lead(ticker, day) == sid


def _handed_over(
    resolver: ListingResolver, sids: Collection[str], window: tuple[date, date]
) -> set[str]:
    """Those of `sids` with a rule-8 window (#1314) meeting `window` under
    a ticker that is an Alpaca symbol: a hole of the security, as if rule 8
    had not cut its span (spec rule 8, "Store holes"). By construction it
    has no bar there."""
    first, last = window
    return {
        sid
        for sid in sids
        if any(
            h.start <= last and first < h.end and alpaca_symbol(h.ticker) is not None
            for h in resolver.handovers(sid)
        )
    }


def _rename_gaps(
    conn: duckdb.DuckDBPyConnection,
    resolver: ListingResolver,
    t: datetime,
    window: tuple[date, date],
    sids: Collection[str],
    horizon: int,
) -> set[str]:
    """Those of `sids` (each with a bar known at `t` in `window`) with a
    rename gap in `window` (#891): a session on which a rename lead (#843)
    assigns it a bar of a ticker that is an Alpaca symbol, with no bar of
    it known at `t` and none later on a session the lead assigns it -- the
    run from the old symbol's last bar to the first cover page naming the
    new one. A missing session the old symbol traded after (a halt, a
    no-trade day) is none: the new symbol has no bar there to fill it.
    `horizon` (`alpaca.rename_lead_days`, the longest a lead runs) bounds
    the search for the next bar."""
    sessions = _sessions_in(window)
    led: dict[str, tuple[list[str], set[date]]] = {}
    for sid in sids:
        tickers = [tk for tk in resolver.symbols(sid, *window) if alpaca_symbol(tk) is not None]
        days = {day for day in sessions if any(resolver.lead(tk, day) == sid for tk in tickers)}
        if days:
            led[sid] = (tickers, days)
    if not led:
        return set()
    stored: dict[str, list[date]] = defaultdict(list)
    for sid, session in conn.execute(
        "SELECT DISTINCT security_id, session FROM prices_daily "
        "WHERE session BETWEEN ? AND ? AND known_at <= ? AND list_contains(?, security_id) "
        "ORDER BY session",
        [window[0], window[1] + timedelta(days=horizon), t, sorted(led)],
    ).fetchall():
        stored[sid].append(session)
    gaps: set[str] = set()
    for sid, (tickers, days) in led.items():
        have = stored[sid]
        for day in sorted(days - set(have)):
            later = next((session for session in have if session > day), None)
            if later is None or not any(resolver.lead(tk, later) == sid for tk in tickers):
                gaps.add(sid)
                break
    return gaps


def _month_holes(
    conn: duckdb.DuckDBPyConnection,
    resolver: ListingResolver,
    t: datetime,
    window: tuple[date, date],
    ids: Collection[str],
    only: frozenset[str] | None,
    horizon: int,
) -> tuple[list[str], set[str], set[str], dict[str, str], set[str]]:
    """The holes of `window` as of `t` among the fetched `ids` (of `only`,
    when given): the ids to fetch, sorted; those of them that are rename
    gaps (`_rename_gaps`); the ids with a bar known at `t` in `window`; the
    ids with no bar that no fetched bar could land on, with why
    (`_assignable`); and those with a rule-8 window in it (#1314,
    `_handed_over`), holes whatever they store."""
    stored = _with_bars(conn, t, window)
    named = [sid for sid in ids if only is None or sid in only]
    handed = _handed_over(resolver, named, window)
    empty, dropped = _assignable(resolver, [sid for sid in named if sid not in stored], window)
    dropped = {sid: why for sid, why in dropped.items() if sid not in handed}
    with_bars = [sid for sid in named if sid in stored]
    gaps = _rename_gaps(conn, resolver, t, window, with_bars, horizon)
    return sorted({*empty, *gaps, *handed}), gaps, stored, dropped, handed


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
            ids, *_ = _window_names(conn, t, window, settings, resolver)
            kept, gaps, _, dropped, handed = _month_holes(
                conn, resolver, t, window, ids, only, settings.alpaca.rename_lead_days
            )
        if tally is not None:
            tally.add((), dropped)
        for sid in kept:
            rows = sorted(tickers.get(sid, []))
            live = [ticker for start, ticker in rows if start <= last]
            ticker = live[-1] if live else (rows[0][1] if rows else None)
            lo, hi = spans.get(sid, (None, None))
            between = lo is not None and hi is not None and lo < first and hi > last
            holes.append(Hole(sid, ticker, window, between, sid in gaps, sid in handed))
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
    fetched. With `fill` (a hole fill), only the holes then (of the `only`
    ids, when given: `_month_holes`, the store's resolver then) are
    fetched, with the reference; the dropped ones are counted in the message and `tally`; the
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
            resolver = store_resolver(conn, started, settings)
            ids, listed, static_only, may_count, reference = _window_names(
                conn, started, window, settings, resolver
            )
            holes: list[str] = []
            renames: set[str] = set()
            stored: set[str] = set()
            dropped: dict[str, str] = {}
            handed: set[str] = set()
            windows8: dict[str, list[tuple[date, date]]] = {}
            if fill:
                holes, renames, stored, dropped, handed = _month_holes(
                    conn, resolver, started, window, ids, only, settings.alpaca.rename_lead_days
                )
                windows8 = {
                    sid: [(h.start, h.end) for h in resolver.handovers(sid)] for sid in handed
                }
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
        if stale := _dark_stale(listed, reported, settings, f"in {first}..{last}"):
            raise _Stale(stale)
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
            renamed = f" and {len(renames)} with a lead gap" if renames else ""
            # #1314 rule 8: a rule-8 hole whose refetch landed nothing on its window.
            landed = {
                bar.security_id
                for bar in bars
                if any(lo <= bar.session < hi for lo, hi in windows8.get(bar.security_id, []))
            }
            empty8 = len(handed - landed)
            renamed += f"; {empty8} rule8_window holes with nothing stored" if empty8 else ""
            filled = (
                f"holes of {len(holes) - len(renames)} names with no stored bar{renamed}"
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
    resolver: ListingResolver,
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

    (#974) The fetch also takes every security whose first-span lead
    (`resolver`, the store's at `t`) assigns it a session of `window`
    under an Alpaca symbol, before its first ticker-bearing listing row,
    when that row passes `_fetched` (an OTC or skipped-type first span is
    not fetched); never the staleness denominator, since no listing of it
    is known in the window.
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
    ids.update(_led(resolver, rows, ids, window, benchmarks, types, settings))
    if reference is not None:
        ids.add(reference)
    static_only = listed - filed - benchmarks
    before = first - timedelta(days=1)
    may_count = _may_count(conn, t, (before.replace(day=1), before), earliest, benchmarks)
    return sorted(ids), sorted(listed), static_only, may_count, reference


def _led(
    resolver: ListingResolver,
    rows: list[dict[str, Any]],
    ids: Collection[str],
    window: tuple[date, date],
    benchmarks: set[str],
    types: Mapping[str, set[str]],
    settings: Settings,
) -> set[str]:
    """The securities not in `ids` that a first-span lead (#974) assigns a
    session of `window` under an Alpaca symbol, before the start of their
    first span (`resolver.first_span_lead`, #1122: the resolver's own
    span, never a raw listing row recomputed here -- an unreadable ticker
    read as another string (#844) or a same-day typo dropped (#819) can
    move which row the resolver treats as the first, so recomputing it
    from `rows` directly could disagree with the resolver and either miss
    or wrongly grant a lead), when that ticker is an Alpaca symbol (else
    the fetch never sends it, as `_assignable` checks for an ordinary
    span) and the listing row at that start passes `_fetched`. A rename
    lead (#843) runs only after a security's first span, so a session
    before its start is the first-span lead's."""
    by_security: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for listing_row in rows:
        by_security[listing_row["security_id"]].append(listing_row)
    sessions = _sessions_in(window)
    out: set[str] = set()
    for sid, sid_rows in by_security.items():
        if sid in ids:
            continue
        lead = resolver.first_span_lead(sid)
        if lead is None or alpaca_symbol(lead.ticker) is None:
            continue
        row: dict[str, Any] | None = next(
            (r for r in sid_rows if r["valid_from"] == lead.end), None
        )
        if row is None or not _fetched(sid, row, benchmarks, types, settings):
            continue
        before = [day for day in sessions if day < lead.end]
        if any(resolver.lead(lead.ticker, day) == sid for day in before):
            out.add(sid)
    return out


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

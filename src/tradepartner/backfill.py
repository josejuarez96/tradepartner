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
  more than `ingest.max_missing_share` of the listed common and benchmark
  names live through the month have no bar in it.

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
backfill with a new, earlier `since`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from tradepartner.adapters.filings import FilingSource
from tradepartner.adapters.prices import PriceSource
from tradepartner.calendar import is_session, next_session
from tradepartner.config import Settings
from tradepartner.ingest import (
    _FETCH_PASS,
    FAILED,
    LOCKED,
    OK,
    SOURCES,
    STALE,
    IngestResult,
    SourceRun,
    _action_row,
    _add_rows,
    _bar_row,
    _build_filings,
    _clean,
    _ingest_filings,
    _record_only,
    _Recorded,
    _run_source,
    _Stale,
    _write_run,
    expected_session,
)
from tradepartner.store.classify import classifications_as_of
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only, utc_now
from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import securities_as_of
from tradepartner.store.schema import init_schema
from tradepartner.timeutil import ensure_tz_aware_utc

BACKFILL = "backfill"


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
            prepare=lambda: _build_filings(recorded, settings, _FETCH_PASS),
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
            run = _price_chunk(settings, prices, since, window, clock)
            runs.append(run)
            if run.status != OK:
                break
    return IngestResult(tuple(runs))


@contextmanager
def _read(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A short-lived read-only connection, retrying a writer's lock for
    `store.lock_retry_seconds` with `open_for_write`'s backoff (spec req 9),
    then `StoreLockedError`."""
    cfg = settings.store
    deadline = time.monotonic() + cfg.lock_retry_seconds
    delay = cfg.lock_retry_initial_delay_seconds
    while True:
        try:
            reader = open_read_only(settings)
            conn = reader.__enter__()
        except StoreLockedError as exc:
            if isinstance(exc.__cause__, duckdb.ConnectionException):
                raise  # this process holds the file: waiting never helps
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, cfg.lock_retry_max_delay_seconds)
            continue
        try:
            yield conn
        finally:
            reader.__exit__(None, None, None)
        return


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
) -> SourceRun:
    """One month. The store is read as of the clock when the month starts
    (after the EDGAR chunk committed) and rows are stamped with the clock
    after the fetch returns, so a revision is never dated before it was
    fetched."""
    first, last = window
    run_id, cursor = uuid.uuid4().hex, _cursor(since, last)
    started = ensure_tz_aware_utc(clock(), field_name="clock()")

    def outcome(status: str, rows: int, message: str) -> SourceRun:
        return SourceRun("alpaca", status, rows, cursor, _clean(message, settings))

    try:
        with _read(settings) as conn:
            ids, listed, reference = _window_names(conn, started, window, settings)
        symbol = settings.ingest.reference_symbol
        if reference is None:
            raise LookupError(f"reference symbol {symbol} has no listing in {first}..{last}")
        bars = [b for b in prices.bars(ids, first, last) if first <= b.session <= last]
        actions = prices.corporate_actions(ids, first, last)
        ingested_at = ensure_tz_aware_utc(clock(), field_name="clock()")
        have = {bar.session for bar in bars if bar.security_id == reference}
        days = (first + timedelta(days=n) for n in range((last - first).days + 1))
        gaps = [day for day in days if is_session(day) and day not in have]
        if gaps:
            shown = ", ".join(day.isoformat() for day in gaps[:10])
            raise _Stale(f"reference symbol {symbol} ({reference}) has no bar for {shown}")
        with_bars = {bar.security_id for bar in bars}
        missing = sorted(set(listed) - with_bars)
        share = len(missing) / len(listed) if listed else 0.0
        limit = settings.ingest.max_missing_share
        if share > limit:
            raise _Stale(
                f"{len(missing)} of {len(listed)} listed names ({share:.1%}, over {limit:.1%}) "
                f"have no bar in {first}..{last}: {', '.join(missing[:10])}"
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
            added += _add_rows(
                conn,
                "corporate_actions",
                [_action_row(action, ingested_at) for action in actions],
                ingested_at=ingested_at,
                current=True,
                where="AND ex_date BETWEEN ? AND ?",
                params=[first, last],
            )
            message = (
                f"{len(bars)} bars and {len(actions)} actions for {len(ids)} names; "
                f"{len(missing)} of {len(listed)} listed names without a bar"
            )
            run = outcome(OK, added, message)
            _write_run(conn, run_id, started, clock(), run, BACKFILL)
        return run
    except StoreLockedError as exc:
        return outcome(LOCKED, 0, str(exc))
    except _Stale as exc:
        run = outcome(STALE, 0, str(exc))
    except Exception as exc:  # any source or parse failure halts with a run row
        run = outcome(FAILED, 0, f"{type(exc).__name__}: {exc}")
    return _record_only(settings, run_id, started, clock, run, BACKFILL)


def _window_names(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    window: tuple[date, date],
    settings: Settings,
) -> tuple[list[str], list[str], str | None]:
    """From listings known at `t`: securities with a listing live at some
    point in `window` (to fetch), the common and benchmark names listed
    through the whole window, including any delisted only later (the
    staleness denominator), and the reference
    symbol's `security_id`.

    A listing counts from its `valid_from`; a delisted or transferred one
    still counts while its end session (last bar known) or `effective_on`
    is inside or after the window, or while no end is known yet.
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
    ids: set[str] = set()
    listed: set[str] = set()
    reference = None
    for row in listing_ends_as_of(conn, t, settings).iter_rows(named=True):
        if row["valid_from"] > last:
            continue
        sid, status = row["security_id"], row["status"]
        end, effective = row["end_session"], row["effective_on"]
        ended = status in (DELISTED, TRANSFERRED) and (
            end is not None and end < first and (effective is None or effective < first)
        )
        if ended or status not in (LISTED, DELISTED, TRANSFERRED):
            continue
        ids.add(sid)
        live = status == LISTED or (status == TRANSFERRED and (end is None or end >= last))
        through = live or (status == DELISTED and effective is not None and effective > last)
        counted = sid in benchmarks or kinds.get(sid) == "common"
        if through and row["valid_from"] <= first and counted:
            listed.add(sid)  # today's status must not drop a name delisted later
        if live and row["ticker"] == settings.ingest.reference_symbol:
            reference = sid
    return sorted(ids), sorted(listed), reference

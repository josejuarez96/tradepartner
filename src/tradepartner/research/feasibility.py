"""The feasibility counts reader (T454, #3702): counts on an already-open store copy.

`count_feasibility(conn, design)` fills a `FeasibilityCounts` (see `feasibility_block`)
from a connection the caller opened read-only on a COPY of the store. It never opens a
store and never decides whether a path is the owner's: that is the command's (T455).

What it reads, and what it provably does not:

* The event source's own table (`design.spec.table`): issuer column and `known_at`.
* `securities` (`security_id`, `cik`, `known_at`) to map a CIK to a security.
* `delistings` (`security_id`, `effective_on`, `known_at`) for issuer-level coverage.
* `prices_daily`: the five columns `security_id`, `session`, `close`, `volume`,
  `known_at`, and nothing else. `close * volume` is a LEVEL, taken per session and
  medianed over the lookback; no return, price change or ratio of two closes is ever
  formed, and `open`, `high` and `low` are never named.
* `registry.boundary_date`: the one registry read. Nothing is written, no research
  table is named, and no file is created.

Every read is cut at `known_at <=` the close of the window's last session (a row known
later is not read, so nothing known later can move a count); a band reads only bars
known at the event's own `known_at`. A window past the development boundary, or a copy
with no boundary, is refused before any event or price read.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from statistics import median

import duckdb

from tradepartner.calendar import (
    all_sessions,
    is_session,
    last_completed_session,
    previous_session,
    session_close,
)
from tradepartner.research.event_study.run import ET, entry_session
from tradepartner.research.feasibility_block import (
    BandTotal,
    CountCell,
    DelisterCoverage,
    FeasibilityCounts,
    StoreFacts,
    TimestampCounts,
)
from tradepartner.research.feasibility_design import FeasibilityDesign
from tradepartner.research.shape import effective_count
from tradepartner.store import schema
from tradepartner.store.registry import boundary_date

__all__ = ["FeasibilityRefused", "count_feasibility", "read_store_facts"]

_BARS_SQL = (
    "SELECT security_id, session, close, volume, known_at FROM prices_daily "
    "WHERE list_contains(?, security_id) AND session >= ? AND session <= ? AND known_at <= ?"
)
_HAS_BARS_SQL = (
    "SELECT DISTINCT security_id FROM prices_daily "
    "WHERE list_contains(?, security_id) AND session >= ? AND session <= ? AND known_at <= ?"
)
_SECURITIES_SQL = "SELECT security_id, cik FROM securities WHERE known_at <= ?"
_DELISTINGS_SQL = (
    "SELECT DISTINCT security_id FROM ("
    "SELECT security_id, effective_on, row_number() OVER "
    "(PARTITION BY security_id, form, class_title, exchange, filed_at ORDER BY known_at DESC) AS n "
    "FROM delistings WHERE known_at <= ?) WHERE n = 1 AND effective_on >= ? AND effective_on <= ?"
)
_INGEST_SQL = (
    "SELECT run_id, COALESCE(finished_at, started_at) AS ended FROM ingestion_runs "
    "ORDER BY ended DESC, run_id DESC LIMIT 1"
)
_DB_PATH_SQL = "SELECT path FROM duckdb_databases() WHERE database_name = current_database()"


class FeasibilityRefused(ValueError):
    """The window or the copy breaks a hard rule; raised before any event or price read."""


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def read_store_facts(conn: duckdb.DuckDBPyConnection) -> StoreFacts:
    """The header facts of the counted file: path, size, mtime, schema version, newest ingest."""
    row = conn.execute(_DB_PATH_SQL).fetchone()
    if row is None or not row[0]:
        raise FeasibilityRefused("the connection is not backed by a file")
    path = str(row[0])
    info = os.stat(path)
    ingest = conn.execute(_INGEST_SQL).fetchone()
    return StoreFacts(
        store_path=path,
        size_bytes=info.st_size,
        modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
        schema_version=schema.stored_version(conn) or 0,
        newest_ingest_run_id=None if ingest is None else str(ingest[0]),
        newest_ingest_at=None if ingest is None else _utc(ingest[1]),
    )


#: A fact's identity: the table's own natural key without `known_at`, as `store/schema.py`
#: states it (form4 UNIQUE (accession, filer_cik, line_no); statement_facts UNIQUE (cik,
#: fact_name, period_end, period_days); delistings UNIQUE (security_id, form, class_title,
#: exchange, filed_at, known_at); corporate_actions the `corporate_actions_identity` index,
#: which is `(security_id, source_action_id)` when the source gives an id and else the
#: type and ex-date). Each key with `known_at` is unique, so ordering by `known_at` has no
#: tie and no result depends on insert order. T453's `SourceSpec` is where these belong.
_IDENTITY: dict[str, str] = {
    "form4": "accession, filer_cik, line_no",
    "statement_fact": "cik, fact_name, period_end, period_days",
    "form25": "security_id, form, class_title, exchange, filed_at",
    "corporate_action": (
        "security_id, source_action_id, "
        "CASE WHEN source_action_id = '' THEN action_type END, "
        "CASE WHEN source_action_id = '' THEN ex_date END"
    ),
}


def _event_rows(
    conn: duckdb.DuckDBPyConnection, design: FeasibilityDesign, start: datetime, cut: datetime
) -> list[tuple[str, datetime]]:
    """`(issuer, known_at)` of each fact's FIRST-known row, if it matches and is known in range.

    A later revision, cancellation or restatement never removes or re-dates an event
    (`SourceSpec`'s rule), so the filter and the window test the first-known row.
    """
    spec = design.spec
    identity = _IDENTITY[design.source]
    clauses = [f"{spec.issuer_column} IS NOT NULL", "known_at >= ?"]
    params: list[object] = [cut, start]
    for column, values in design.filters:
        if column not in spec.filter_columns:
            raise FeasibilityRefused(f"filter.{column}: not a filterable column of {spec.table}")
        clauses.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    cols = ", ".join(dict.fromkeys([spec.issuer_column, *spec.filter_columns, "known_at"]))
    sql = (
        f"SELECT {spec.issuer_column}, known_at FROM ("
        f"SELECT {cols}, row_number() OVER (PARTITION BY {identity} ORDER BY known_at) AS n "
        f"FROM {spec.table} WHERE known_at <= ?) WHERE n = 1 AND {' AND '.join(clauses)}"
    )
    return [(str(r[0]), _utc(r[1])) for r in conn.execute(sql, params).fetchall()]


def _lookback_median(
    bars: dict[date, list[tuple[datetime, float]]],
    sessions: Sequence[date],
    decided_at: datetime,
) -> float | None:
    """Median close * volume over `sessions`, from the bars known at `decided_at`; None if short."""
    levels: list[float] = []
    for session in sessions:
        known = [level for known_at, level in bars.get(session, ()) if known_at <= decided_at]
        if not known:
            return None
        levels.append(known[-1])
    return float(median(levels))


def _band(edges: tuple[float, ...], level: float) -> int:
    for index, edge in enumerate(edges):
        if level >= edge:
            return index
    return len(edges)


def count_feasibility(
    conn: duckdb.DuckDBPyConnection,
    design: FeasibilityDesign,
    *,
    store: StoreFacts | None = None,
) -> FeasibilityCounts:
    """Fill the block's counts from `conn`, an open read-only connection to a store copy.

    Raises `FeasibilityRefused` before any event or price read when the copy has no
    development boundary or the window ends after it. `store` carries the header facts
    (read from the copy by `read_store_facts` when omitted).
    """
    boundary = boundary_date(conn)
    if boundary is None:
        raise FeasibilityRefused("the copy has no development boundary")
    if design.window_end > boundary:
        raise FeasibilityRefused(
            f"window.end {design.window_end} is after the development boundary {boundary}"
        )
    last = (
        design.window_end if is_session(design.window_end) else previous_session(design.window_end)
    )
    if last < design.window_start:
        raise FeasibilityRefused("the window holds no session of the trading calendar")
    cut = session_close(last)
    start = datetime.combine(design.window_start, time(0), tzinfo=ET)
    calendar = all_sessions()
    index = {session: i for i, session in enumerate(calendar)}
    first_index = next(i for i, s in enumerate(calendar) if s >= design.window_start)
    last_index = index[last]

    # Events: rows of one issuer that share an entry session are one event.
    rows = _event_rows(conn, design, start, cut)
    events: dict[tuple[str, int], datetime] = {}
    outside: set[tuple[str, date]] = set()
    for issuer, known_at in rows:
        entry_day = entry_session(known_at, design.entry)
        entry = index.get(entry_day)
        if entry is None or entry > last_index:
            outside.add((issuer, entry_day))
            continue
        key = (issuer, entry)
        events[key] = min(known_at, events.get(key, known_at))
    no_entry = len(outside)

    securities: defaultdict[str, list[str]] = defaultdict(list)
    cik_of: dict[str, str] = {}
    if design.spec.issuer_column in ("issuer_cik", "cik"):
        for security_id, cik in conn.execute(_SECURITIES_SQL, [cut]).fetchall():
            securities[str(cik)].append(str(security_id))
            cik_of[str(security_id)] = str(cik)
        for ids in securities.values():
            ids.sort()

    def securities_of(issuer: str) -> list[str]:
        return securities.get(issuer, []) if cik_of else [issuer]

    # Bars: the five columns, known by the cut, over the sessions any lookback can touch.
    needed = sorted({s for issuer, _ in events for s in securities_of(issuer)})
    bars: defaultdict[str, defaultdict[date, list[tuple[datetime, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    if events and needed:
        first_entry = min(entry for _, entry in events)
        floor = calendar[max(0, first_entry - design.lookback_sessions - 2)]
        for sid, session, close, volume, known_at in conn.execute(
            _BARS_SQL, [needed, floor, last, cut]
        ).fetchall():
            bars[str(sid)][session].append((_utc(known_at), float(close) * float(volume)))
        for per_session in bars.values():
            for versions in per_session.values():
                versions.sort(key=lambda v: v[0])

    # Band: median close * volume over the lookback sessions complete at the event's own time.
    banded: dict[int | None, list[tuple[str, int]]] = defaultdict(list)
    for (issuer, entry), known_at in sorted(events.items()):
        done = index[last_completed_session(known_at)]
        window = calendar[max(0, done - design.lookback_sessions + 1) : done + 1]
        band: int | None = None
        if len(window) == design.lookback_sessions:
            for sid in securities_of(issuer):
                level = _lookback_median(bars[sid], window, known_at)
                if level is not None:
                    band = _band(design.edges, level)
                    break
        banded[band].append((issuer, entry))

    cells: list[CountCell] = []
    totals: list[BandTotal] = []
    for band in (*range(design.n_bands), None):
        members = banded.get(band, [])
        by_year: defaultdict[int, list[tuple[str, int]]] = defaultdict(list)
        for issuer, entry in members:
            by_year[calendar[entry].year].append((issuer, entry))
        for year, group in sorted(by_year.items()):
            cells.append(
                CountCell(
                    year, band, len(group), len({e for _, e in group}), len({i for i, _ in group})
                )
            )
        eff = (
            effective_count(members, design.hold_sessions, rho=design.rho)
            if members and band is not None
            else None
        )
        totals.append(
            BandTotal(
                band,
                len(members),
                len({e for _, e in members}),
                len({i for i, _ in members}),
                eff,
            )
        )

    return FeasibilityCounts(
        store=store if store is not None else read_store_facts(conn),
        boundary=boundary,
        timestamp=TimestampCounts(rows_matched=len(rows), events_without_entry=no_entry),
        cells=tuple(cells),
        totals=tuple(totals),
        events_hold_past_window=sum(
            1 for _, entry in events if entry + design.hold_sessions - 1 > last_index
        ),
        window_sessions=last_index - first_index + 1,
        coverage=_coverage(conn, cut, design, last, {i for i, _ in events}, cik_of),
    )


def _coverage(
    conn: duckdb.DuckDBPyConnection,
    cut: datetime,
    design: FeasibilityDesign,
    last: date,
    event_issuers: set[str],
    cik_of: dict[str, str],
) -> DelisterCoverage:
    """(f): issuers whose listing ended in the window; issuer level only, never per event."""
    ended = [
        str(r[0])
        for r in conn.execute(
            _DELISTINGS_SQL, [cut, design.window_start, design.window_end]
        ).fetchall()
    ]
    with_bars = (
        {
            str(r[0])
            for r in conn.execute(_HAS_BARS_SQL, [ended, design.window_start, last, cut]).fetchall()
        }
        if ended
        else set()
    )
    issuers: defaultdict[str, list[str]] = defaultdict(list)
    for sid in ended:
        issuers[cik_of.get(sid, sid)].append(sid)
    has_event = {i for i in issuers if i in event_issuers}
    has_bars = {i for i, ids in issuers.items() if with_bars.intersection(ids)}
    return DelisterCoverage(
        delisted=len(issuers),
        delisted_with_bars=len(has_bars),
        delisted_with_events=len(has_event),
        delisted_with_events_and_bars=len(has_event & has_bars),
    )

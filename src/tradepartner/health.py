"""Data health as code (spec req 11; plan T18), shared by `tradepartner health`
(T19) and the data-health page (T21).

`health_report(conn, t, settings)` reads the store through the connection it is
given and computes nothing but what the rows say. Every metric that describes
the data (coverage, gaps, survivorship gap, unclassifiable, `snapshot_static`
reliance, delisted names) reads only rows with `known_at <= t`, like any as-of
function; the CLI and the page pass the current time. `t` must be a tz-aware
`datetime` (a bare date raises `TypeError`, a naive datetime `ValueError`), and
`session` is the last session completed at `t`.

**Metrics.** The spec names them; where it does not define one, the definition
here is this module's and is stated once:

- **Last ingest per source** (`last_ingests`): from `ingestion_runs` started at
  or before `t`, per source (`ingest.SOURCES` first, then any other source in
  the table, alphabetically): the finish time and cursor of the last `ok` run,
  and the status, start and message of the latest run of any status. A source
  that never ran shows `None`s.
- **Coverage** (`coverage`): the securities whose current listing (latest
  `valid_from` on or before `session`, as universe rule 2 reads it) is live at
  `session`, i.e. listed, or transferred with its end on or after `session`;
  how many of them have a bar for `session` known at `t`, the ones that do not,
  and the share (0.0 when no name is live). Also the first and last bar session
  in the store and how many securities have any bar, all known at `t`.
- **Gaps** (`bar_gaps`): interior holes in each security's bar history known at
  `t` up to `session`: XNYS sessions between its first and last bar with no bar.
  One row per security with at least one hole: first and last bar, bar count,
  missing sessions and the longest run of consecutive missing sessions. A
  trailing absence (no bar since some session) is not a gap here; coverage and
  the survivorship gap report it.
- **Survivorship gap** with its three side categories: `gap.survivorship_gap`
  at `t`, unchanged.
- **Unclassifiable** (`unclassifiable`): securities known at `t` whose latest
  classification is `unclassifiable`, and those with no classification row at
  all (`unclassified`), the two missing-data reasons of universe rule 1.
- **`snapshot_static` reliance** (`static_reliance`): securities known at `t`
  whose `securities` row, current listing or classification as of `t` rests on
  a `snapshot_static` row, with the count per table. Such a row counts only
  from its own `known_at` (#35).
- **Delisted names** (`delisted_names`): securities whose current listing is
  delisted at `t` (Forms 25 and 25-NSE; a transfer counts as delisted until its
  new listing is known, spec req 4), with ticker, exchange, class, form, filing
  time, end session and effective date.
- **Settings**: `universe.liquidity_rule_enabled` and `execution.fill_price`.

**Integrity rules** (`integrity_checks`), each a named `IntegrityCheck` whose
`violations` frame lists the offending rows or keys, empty when it passes.
`HealthReport.ok` is true when every rule passes; `health --check` exits
non-zero otherwise, naming `HealthReport.failures`. The row-level rules read
every row in the store, whatever its `known_at`; the two listing rules are
derived at `t`, as the data is read.

- `known_at_not_null`, `known_at_le_ingested_at`, `source_not_null` and
  `provenance_allowed` (the table's own set, `schema.TABLE_PROVENANCE_VALUES`)
  on every fact table. The schema's constraints already block these; the rules
  make a store written by other code, or a changed schema, show it.
- `bars_on_sessions`: every `prices_daily.session` is an XNYS session in the
  configured calendar range.
- `no_duplicate_bars`: no two bars share `(security_id, session, known_at)`.
- `non_overlapping_listings`: per security, ordered by `valid_from`, no two
  listings start on the same session and no delisted or transferred listing
  ends on or after the next listing's `valid_from`. A listing with no Form 25
  is superseded by the next one (a ticker change), as the as-of reads treat it.
- `no_bars_after_delisting`: no bar known at `t` for a delisted listing's
  security dated after the delisting's `effective_on` and before the
  security's next listing, if any. The derived end of a delisted listing is its
  last bar (spec req 4), so the bound that can be broken is the date the
  delisting takes effect; trading between the filing and that date is normal.
  A bar past it means a wrong delisting or a bar resolved to the wrong security
  (a reused ticker).
- `guarded_sic_default`: `universe.exclude_sic_ranges` equals the charter
  value (ADR 0006). `Settings` refuses any other value, so this fails only on
  settings built around the guard.

This module holds no threshold; the only numbers in it are 0 and 1.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import all_sessions, last_completed_session
from tradepartner.config import _GUARDED_EXCLUDE_SIC_RANGES, Settings, get_settings
from tradepartner.gap import SurvivorshipGap, survivorship_gap
from tradepartner.ingest import OK, SOURCES
from tradepartner.store.asof import _validate_t
from tradepartner.store.classify import UNCLASSIFIABLE, classifications_as_of
from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import securities_as_of
from tradepartner.store.schema import TABLE_PROVENANCE_VALUES
from tradepartner.universe import _current_listings

STATIC = "snapshot_static"

KNOWN_AT_NOT_NULL = "known_at_not_null"
KNOWN_AT_NOT_AFTER_INGESTED_AT = "known_at_le_ingested_at"
SOURCE_NOT_NULL = "source_not_null"
PROVENANCE_ALLOWED = "provenance_allowed"
BARS_ON_SESSIONS = "bars_on_sessions"
NO_DUPLICATE_BARS = "no_duplicate_bars"
NON_OVERLAPPING_LISTINGS = "non_overlapping_listings"
NO_BARS_AFTER_DELISTING = "no_bars_after_delisting"
GUARDED_SIC_DEFAULT = "guarded_sic_default"

#: Every integrity rule, in the order `integrity_checks` reports them.
INTEGRITY_RULES: tuple[str, ...] = (
    KNOWN_AT_NOT_NULL,
    KNOWN_AT_NOT_AFTER_INGESTED_AT,
    SOURCE_NOT_NULL,
    PROVENANCE_ALLOWED,
    BARS_ON_SESSIONS,
    NO_DUPLICATE_BARS,
    NON_OVERLAPPING_LISTINGS,
    NO_BARS_AFTER_DELISTING,
    GUARDED_SIC_DEFAULT,
)

_FACT_TABLES: tuple[str, ...] = tuple(TABLE_PROVENANCE_VALUES)

_TABLE_COUNT_SCHEMA: dict[str, Any] = {"table": pl.Utf8, "rows": pl.Int64}
_PROVENANCE_SCHEMA: dict[str, Any] = {"table": pl.Utf8, "provenance": pl.Utf8, "rows": pl.Int64}
_SESSION_SCHEMA: dict[str, Any] = {"session": pl.Date, "rows": pl.Int64}
_DUPLICATE_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
    "rows": pl.Int64,
}
_OVERLAP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "valid_from": pl.Date,
    "status": pl.Utf8,
    "end_session": pl.Date,
    "next_ticker": pl.Utf8,
    "next_exchange": pl.Utf8,
    "next_valid_from": pl.Date,
}
_AFTER_DELISTING_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "effective_on": pl.Date,
}
_GUARD_SCHEMA: dict[str, Any] = {"setting": pl.Utf8, "value": pl.Utf8, "expected": pl.Utf8}
_GAP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "first_bar": pl.Date,
    "last_bar": pl.Date,
    "bars": pl.Int64,
    "missing_sessions": pl.Int64,
    "longest_run": pl.Int64,
}
_DELISTED_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "class_title": pl.Utf8,
    "form": pl.Utf8,
    "filed_at": pl.Datetime("us", "UTC"),
    "end_session": pl.Date,
    "effective_on": pl.Date,
}


@dataclass(frozen=True)
class IngestStatus:
    """One source's ingest record at `t` (see the module docstring)."""

    source: str
    last_ok_finished_at: datetime | None
    last_ok_cursor: str | None
    latest_status: str | None
    latest_started_at: datetime | None
    latest_message: str | None


@dataclass(frozen=True)
class Coverage:
    """Bars at `session` for the names live then, and the store's bar range."""

    session: date
    live: tuple[str, ...]
    missing: tuple[str, ...]
    share: float
    first_bar: date | None
    last_bar: date | None
    names_with_bars: int


@dataclass(frozen=True, eq=False)
class BarGaps:
    """Interior bar holes, one row per security that has any."""

    rows: pl.DataFrame

    @property
    def names_with_gaps(self) -> int:
        """How many securities have at least one hole."""
        return self.rows.height

    @property
    def missing_sessions(self) -> int:
        """Missing sessions summed over every security."""
        return int(self.rows["missing_sessions"].sum())


@dataclass(frozen=True)
class Unclassifiable:
    """Universe rule 1's missing-data names known at `t`."""

    unclassifiable: tuple[str, ...]
    unclassified: tuple[str, ...]

    @property
    def count(self) -> int:
        """Both groups together."""
        return len(self.unclassifiable) + len(self.unclassified)


@dataclass(frozen=True)
class StaticReliance:
    """Securities resting on `snapshot_static` rows at `t`."""

    ids: tuple[str, ...]
    by_table: dict[str, int]

    @property
    def count(self) -> int:
        """How many securities rely on at least one static row."""
        return len(self.ids)


@dataclass(frozen=True, eq=False)
class DelistedNames:
    """Securities whose current listing is delisted at `t`."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many securities are delisted."""
        return self.frame.height


@dataclass(frozen=True, eq=False)
class IntegrityCheck:
    """One integrity rule: passed when `violations` is empty."""

    rule: str
    violations: pl.DataFrame

    @property
    def passed(self) -> bool:
        """True when nothing violates the rule."""
        return self.violations.is_empty()


@dataclass(frozen=True, eq=False)
class HealthReport:
    """Everything the health command and page show (module docstring)."""

    t: datetime
    session: date
    ingests: tuple[IngestStatus, ...]
    coverage: Coverage
    gaps: BarGaps
    survivorship: SurvivorshipGap
    unclassifiable: Unclassifiable
    static_reliance: StaticReliance
    delisted: DelistedNames
    settings: dict[str, Any]
    integrity: tuple[IntegrityCheck, ...]

    @property
    def failures(self) -> tuple[str, ...]:
        """The failing integrity rules, in `INTEGRITY_RULES` order."""
        return tuple(check.rule for check in self.integrity if not check.passed)

    @property
    def ok(self) -> bool:
        """True when every integrity rule passes."""
        return not self.failures


def health_report(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings | None = None
) -> HealthReport:
    """Every metric and integrity rule at `t` (module docstring). `settings`
    defaults to `get_settings()` and is passed to every derived read."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    return HealthReport(
        t=t,
        session=last_completed_session(t),
        ingests=last_ingests(conn, t),
        coverage=coverage(conn, t, settings),
        gaps=bar_gaps(conn, t),
        survivorship=survivorship_gap(conn, t, settings),
        unclassifiable=unclassifiable(conn, t),
        static_reliance=static_reliance(conn, t, settings),
        delisted=delisted_names(conn, t, settings),
        settings={
            "liquidity_rule_enabled": settings.universe.liquidity_rule_enabled,
            "fill_price": settings.execution.fill_price,
        },
        integrity=integrity_checks(conn, t, settings),
    )


def last_ingests(conn: duckdb.DuckDBPyConnection, t: datetime) -> tuple[IngestStatus, ...]:
    """Per source, the last `ok` run and the latest run started at or
    before `t`; `ingest.SOURCES` first, then any other source."""
    t = _validate_t(t)
    runs = conn.execute(
        """
        SELECT source, status, started_at, finished_at, chunk_cursor, message
        FROM ingestion_runs WHERE started_at <= ?
        ORDER BY started_at, run_id
        """,
        [t],
    ).fetchall()
    by_source: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for run in runs:
        by_source[run[0]].append(run)
    extra = sorted(set(by_source) - set(SOURCES))
    out: list[IngestStatus] = []
    for source in (*SOURCES, *extra):
        rows = by_source.get(source, [])
        ok_rows = [r for r in rows if r[1] == OK and r[3] is not None]
        last_ok = max(ok_rows, key=lambda r: r[3], default=None)
        latest = rows[-1] if rows else None
        out.append(
            IngestStatus(
                source=source,
                last_ok_finished_at=None if last_ok is None else last_ok[3],
                last_ok_cursor=None if last_ok is None else last_ok[4],
                latest_status=None if latest is None else latest[1],
                latest_started_at=None if latest is None else latest[2],
                latest_message=None if latest is None else latest[5],
            )
        )
    return tuple(out)


def _is_live(listing: dict[str, Any], session: date) -> bool:
    status, end = listing["status"], listing["end_session"]
    if status == LISTED:
        return True
    return status == TRANSFERRED and end is not None and end >= session


def coverage(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> Coverage:
    """Bars at the last completed session for the names live then, from rows
    known at `t` (module docstring)."""
    t = _validate_t(t)
    session = last_completed_session(t)
    current = _current_listings(conn, t, session, settings)
    live = tuple(sorted(sid for sid, row in current.items() if _is_live(row, session)))
    with_bar = {
        sid
        for (sid,) in conn.execute(
            "SELECT DISTINCT security_id FROM prices_daily WHERE known_at <= ? AND session = ?",
            [t, session],
        ).fetchall()
    }
    missing = tuple(sid for sid in live if sid not in with_bar)
    row = conn.execute(
        """
        SELECT min(session), max(session), count(DISTINCT security_id)
        FROM prices_daily WHERE known_at <= ? AND session <= ?
        """,
        [t, session],
    ).fetchone()
    first_bar, last_bar, names = row if row is not None else (None, None, 0)
    return Coverage(
        session=session,
        live=live,
        missing=missing,
        share=(len(live) - len(missing)) / len(live) if live else 0.0,
        first_bar=first_bar,
        last_bar=last_bar,
        names_with_bars=int(names),
    )


def bar_gaps(conn: duckdb.DuckDBPyConnection, t: datetime) -> BarGaps:
    """Interior bar holes per security, from bars known at `t` up to the last
    completed session (module docstring). Bars on a non-session are left to
    the `bars_on_sessions` rule."""
    t = _validate_t(t)
    session = last_completed_session(t)
    bars = conn.execute(
        """
        SELECT DISTINCT security_id, session FROM prices_daily
        WHERE known_at <= ? AND session <= ?
        """,
        [t, session],
    ).pl()
    sessions = all_sessions()
    index = pl.DataFrame(
        {"session": sessions, "_idx": range(len(sessions))},
        schema={"session": pl.Date, "_idx": pl.Int64},
    )
    holes = (
        bars.join(index, on="session", how="inner")
        .sort("security_id", "_idx")
        .with_columns(
            _hole=(pl.col("_idx").diff().over("security_id") - 1).fill_null(0).cast(pl.Int64)
        )
    )
    rows = (
        holes.group_by("security_id")
        .agg(
            first_bar=pl.col("session").min(),
            last_bar=pl.col("session").max(),
            bars=pl.len().cast(pl.Int64),
            missing_sessions=pl.col("_hole").sum(),
            longest_run=pl.col("_hole").max(),
        )
        .filter(pl.col("missing_sessions") > 0)
        .sort("security_id")
    )
    return BarGaps(rows=pl.DataFrame(rows, schema=_GAP_SCHEMA))


def unclassifiable(conn: duckdb.DuckDBPyConnection, t: datetime) -> Unclassifiable:
    """Securities known at `t` classified `unclassifiable`, or with no
    classification row known at `t`."""
    t = _validate_t(t)
    known = set(securities_as_of(conn, t)["security_id"].to_list())
    classes = {
        r["security_id"]: r["security_type"]
        for r in classifications_as_of(conn, t).iter_rows(named=True)
    }
    return Unclassifiable(
        unclassifiable=tuple(sorted(sid for sid in known if classes.get(sid) == UNCLASSIFIABLE)),
        unclassified=tuple(sorted(known - set(classes))),
    )


def static_reliance(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> StaticReliance:
    """Securities whose `securities` row, current listing or classification
    as of `t` has `provenance = snapshot_static` (module docstring)."""
    t = _validate_t(t)
    session = last_completed_session(t)
    securities = securities_as_of(conn, t)
    known = set(securities["security_id"].to_list())
    static: dict[str, set[str]] = {
        "securities": {
            r["security_id"] for r in securities.iter_rows(named=True) if r["provenance"] == STATIC
        },
        "listings": {
            sid
            for sid, row in _current_listings(conn, t, session, settings).items()
            if sid in known and row["provenance"] == STATIC
        },
        "classifications": {
            r["security_id"]
            for r in classifications_as_of(conn, t).iter_rows(named=True)
            if r["security_id"] in known and r["provenance"] == STATIC
        },
    }
    ids = set().union(*static.values())
    return StaticReliance(
        ids=tuple(sorted(ids)),
        by_table={table: len(names) for table, names in static.items()},
    )


def delisted_names(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> DelistedNames:
    """Securities whose current listing is delisted at `t`, sorted by id."""
    t = _validate_t(t)
    session = last_completed_session(t)
    rows = [
        {
            "security_id": sid,
            "ticker": row["ticker"],
            "exchange": row["exchange"],
            "class_title": row["class_title"],
            "form": row["delisting_form"],
            "filed_at": row["delisting_filed_at"],
            "end_session": row["end_session"],
            "effective_on": row["effective_on"],
        }
        for sid, row in sorted(_current_listings(conn, t, session, settings).items())
        if row["status"] == DELISTED
    ]
    return DelistedNames(frame=pl.DataFrame(rows, schema=_DELISTED_SCHEMA))


def integrity_checks(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> tuple[IntegrityCheck, ...]:
    """Every rule in `INTEGRITY_RULES`, in that order (module docstring)."""
    t = _validate_t(t)
    listings = listing_ends_as_of(conn, t, settings)
    violations: dict[str, pl.DataFrame] = {
        KNOWN_AT_NOT_NULL: _count_per_table(conn, "known_at IS NULL"),
        KNOWN_AT_NOT_AFTER_INGESTED_AT: _count_per_table(conn, "known_at > ingested_at"),
        SOURCE_NOT_NULL: _count_per_table(conn, "source IS NULL"),
        PROVENANCE_ALLOWED: _bad_provenance(conn),
        BARS_ON_SESSIONS: _bars_off_sessions(conn),
        NO_DUPLICATE_BARS: _duplicate_bars(conn),
        NON_OVERLAPPING_LISTINGS: _overlapping_listings(listings),
        NO_BARS_AFTER_DELISTING: _bars_after_delisting(conn, t, listings),
        GUARDED_SIC_DEFAULT: _guarded_sic(settings),
    }
    return tuple(IntegrityCheck(rule=rule, violations=violations[rule]) for rule in INTEGRITY_RULES)


def _count_per_table(conn: duckdb.DuckDBPyConnection, condition: str) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        row = conn.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()
        count = int(row[0]) if row is not None else 0
        if count:
            rows.append({"table": table, "rows": count})
    return pl.DataFrame(rows, schema=_TABLE_COUNT_SCHEMA)


def _bad_provenance(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        allowed = TABLE_PROVENANCE_VALUES[table]
        marks = ", ".join("?" for _ in allowed)
        found = conn.execute(
            f"""
            SELECT provenance, count(*) FROM {table}
            WHERE provenance IS NULL OR provenance NOT IN ({marks})
            GROUP BY provenance ORDER BY provenance NULLS FIRST
            """,
            list(allowed),
        ).fetchall()
        rows.extend({"table": table, "provenance": p, "rows": int(n)} for p, n in found)
    return pl.DataFrame(rows, schema=_PROVENANCE_SCHEMA)


def _bars_off_sessions(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    sessions = set(all_sessions())
    found = conn.execute(
        "SELECT session, count(*) FROM prices_daily GROUP BY session ORDER BY session"
    ).fetchall()
    rows = [{"session": s, "rows": int(n)} for s, n in found if s not in sessions]
    return pl.DataFrame(rows, schema=_SESSION_SCHEMA)


def _duplicate_bars(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    found = conn.execute(
        """
        SELECT security_id, session, known_at, count(*) FROM prices_daily
        GROUP BY security_id, session, known_at HAVING count(*) > 1
        ORDER BY security_id, session, known_at
        """
    ).fetchall()
    rows = [
        {"security_id": sid, "session": s, "known_at": k, "rows": int(n)} for sid, s, k, n in found
    ]
    return pl.DataFrame(rows, schema=_DUPLICATE_SCHEMA)


def _by_security(listings: pl.DataFrame) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in listings.iter_rows(named=True):
        out[row["security_id"]].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r["valid_from"], r["exchange"], r["ticker"]))
    return out


def _overlapping_listings(listings: pl.DataFrame) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for sid, ordered in sorted(_by_security(listings).items()):
        for current, following in pairwise(ordered):
            end = current["end_session"]
            same_start = current["valid_from"] == following["valid_from"]
            ends_late = current["status"] != LISTED and end is not None
            if same_start or (ends_late and end >= following["valid_from"]):
                rows.append(
                    {
                        "security_id": sid,
                        "ticker": current["ticker"],
                        "exchange": current["exchange"],
                        "valid_from": current["valid_from"],
                        "status": current["status"],
                        "end_session": end,
                        "next_ticker": following["ticker"],
                        "next_exchange": following["exchange"],
                        "next_valid_from": following["valid_from"],
                    }
                )
    return pl.DataFrame(rows, schema=_OVERLAP_SCHEMA)


def _bars_after_delisting(
    conn: duckdb.DuckDBPyConnection, t: datetime, listings: pl.DataFrame
) -> pl.DataFrame:
    bounds: list[dict[str, Any]] = []
    for ordered in _by_security(listings).values():
        for row in ordered:
            if row["status"] != DELISTED:
                continue
            later = [o["valid_from"] for o in ordered if o["valid_from"] > row["valid_from"]]
            bounds.append({**row, "_next": min(later, default=None)})
    if not bounds:
        return pl.DataFrame([], schema=_AFTER_DELISTING_SCHEMA)
    ids = sorted({b["security_id"] for b in bounds})
    marks = ", ".join("?" for _ in ids)
    sessions: dict[str, list[date]] = defaultdict(list)
    for sid, session in conn.execute(
        f"""
        SELECT DISTINCT security_id, session FROM prices_daily
        WHERE known_at <= ? AND security_id IN ({marks})
        ORDER BY security_id, session
        """,
        [t, *ids],
    ).fetchall():
        sessions[sid].append(session)
    rows = [
        {
            "security_id": b["security_id"],
            "session": s,
            "ticker": b["ticker"],
            "exchange": b["exchange"],
            "effective_on": b["effective_on"],
        }
        for b in bounds
        for s in sessions[b["security_id"]]
        if s > b["effective_on"] and (b["_next"] is None or s < b["_next"])
    ]
    return pl.DataFrame(rows, schema=_AFTER_DELISTING_SCHEMA).sort("security_id", "session")


def _guarded_sic(settings: Settings) -> pl.DataFrame:
    actual = tuple(tuple(r) for r in settings.universe.exclude_sic_ranges)
    rows: list[dict[str, Any]] = []
    if actual != _GUARDED_EXCLUDE_SIC_RANGES:
        rows.append(
            {
                "setting": "universe.exclude_sic_ranges",
                "value": repr(actual),
                "expected": repr(_GUARDED_EXCLUDE_SIC_RANGES),
            }
        )
    return pl.DataFrame(rows, schema=_GUARD_SCHEMA)

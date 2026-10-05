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

- **Last ingest per source** (`last_ingests`): from `ingestion_runs` rows
  known at `t`, i.e. finished at or before `t` (ingest writes a run's row when
  the run ends), per source (`ingest.SOURCES` first, then any other source in
  the table, alphabetically): the finish time and cursor of the last `ok` run,
  and the status, start and message of the latest run of any status. A source
  that never ran shows `None`s.
- **Coverage** (`coverage`): the population of ingest's staleness check
  (spec req 10): securities whose current listing (latest `valid_from` on or
  before `session`) is live at `session`, i.e. listed, or transferred with its
  end on or after `session`, and that are classified `common` or are a
  benchmark; how many of them have a bar for `session` known at `t`, the ones
  that do not, and the share (0.0 when no name is live). Also the first and
  last bar session in the store and how many securities have any bar, all
  known at `t`.
- **Gaps** (`bar_gaps`): interior holes in each security's bar history known at
  `t` up to `session`: XNYS sessions between its first and last bar with no bar.
  One row per security with at least one hole: first and last bar, bar count,
  missing sessions and the longest run of consecutive missing sessions. A
  trailing absence (no bar since some session) is not a gap here; coverage and
  the survivorship gap report it. The history is not split at listing
  boundaries, so a security delisted and later relisted shows the break between
  its two listings as one gap.
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
- **Price jumps** (`price_jumps`): the owner's review list (#787),
  `store.asof.price_jumps_as_of` at `t` over every security: each one-day
  close move between traded bars outside the `universe` jump bounds that no
  split or dividend known at `t` explains, with `accepted` from
  `universe.accepted_price_jumps`. An unaccepted jump fails universe rule 6
  while it is in the history window; accepting one is a config change.
  `jumps_before`, when given, keeps only jumps on sessions before it (a
  hypothesis's `holdout.start`), so the list never shows the holdout period.
- **Shares outliers** (`shares_outliers`): the owner's review list (#845),
  `universe.shares_as_of` at `t` over every security: each shares fact out
  of line with the security's last accepted earlier fact (ratio after the
  splits known at `t` outside `universe.max_shares_ratio`), with `accepted`
  from `universe.accepted_shares_facts`. Universe rules 7 and 8 use the last
  accepted fact instead of an unaccepted one. `jumps_before` keeps only
  facts with an `as_of_date` before it.
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
  listings start on the same session, and no listing ended by a Form 25 was
  filed on more than `master.transfer_window_sessions` sessions after the next
  exchange line started (both lines live at once: a dual listing, or a filing
  or listing resolved to the wrong security). The filing session is the raw
  fact to test: the derived end of a delisted or transferred listing is
  clipped before the next listing's start by construction (spec req 4). A
  listing with no Form 25 is superseded by the next one (a ticker change), as
  the as-of reads treat it. Two cases are not a second line (#822): rows of
  the same ticker starting the same day, which differ only in the exchange
  their filers tagged (or the class title's wording), are one line; and a
  row on `OFF_EXCHANGE` (NONE or OTC) is no exchange line at all (the normal
  suspension, OTC quote, late Form 25 sequence), so a late filing is tested
  against the next row that is neither.
- `no_bars_after_delisting`: no bar known at `t` for a delisted listing's
  security that *resumes* after the delisting's `effective_on` and before the
  security's next listing on an exchange, if any (a NONE or OTC row, often
  started before `effective_on` in the suspension sequence, does not end the
  check: the feed has no OTC bars, spec req 10). A listing's end is its last bar (spec req
  4), so a line that keeps trading past `effective_on` without a break (a
  holding-company or redomicile Form 25 on the same line, CMPR) is its own
  tail and passes. A bar that comes after more than
  `master.transfer_window_sessions` missing sessions fails, with every bar
  after it up to that listing. Missing sessions are XNYS sessions strictly
  between two bars; the first gap is counted from the last bar on or before
  `effective_on`, or from `effective_on` when there is none, so a delisting
  that took effect long before the store's first bar fails its line's bars:
  the store cannot show the line kept trading.
  Such a bar is another equity's resolved to this one (a reused ticker,
  EGLE), a relisting with no listing row, or bars on both sides of a hole in
  the store.
- `guarded_sic_default`: `universe.exclude_sic_ranges` equals the charter
  value (ADR 0006). `Settings` refuses any other value, so this fails only on
  settings built around the guard.

This module holds no threshold; the only numbers in it are 0 and 1.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb
import polars as pl

from tradepartner.adapters.alpaca_prices import (
    alpaca_symbol,
    is_placeholder_ticker,
    is_same_day_typo,
    same_alpaca_symbol,
)
from tradepartner.calendar import all_sessions, last_completed_session
from tradepartner.config import _GUARDED_EXCLUDE_SIC_RANGES, Settings, get_settings
from tradepartner.gap import SurvivorshipGap, survivorship_gap
from tradepartner.ingest import OK, SOURCES
from tradepartner.store.asof import _validate_t, price_jumps_as_of
from tradepartner.store.classify import EQUITY, UNCLASSIFIABLE, classifications_as_of, listing_kind
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    TRANSFERRED,
    _filing_session,
    _window,
    listing_ends_as_of,
)
from tradepartner.store.master import securities_as_of
from tradepartner.store.schema import TABLE_PROVENANCE_VALUES
from tradepartner.universe import shares_as_of

STATIC = "snapshot_static"
#: The classification ingest's staleness check counts, with benchmarks.
_COMMON = "common"
#: The exchange codes `adapters.edgar.normalize_exchange` gives a class
#: registered with no exchange ("None") or quoted over the counter: a row on
#: one of them is never a second exchange line (#822).
OFF_EXCHANGE: frozenset[str] = frozenset({"NONE", "OTC"})
#: `_row_kind`'s result for a placeholder-ticker row (#846): never `EQUITY`,
#: matching `ListingResolver`'s own `_PLACEHOLDER` kind.
_PLACEHOLDER_KIND = "placeholder"

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
    "filing_session": pl.Date,
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
class PriceJumps:
    """The price-jump review list at `t` (#787), one row per jump, only sessions
    before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The jumps the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


@dataclass(frozen=True, eq=False)
class SharesOutliers:
    """The shares-outlier review list at `t` (#845), one row per out-of-line
    fact, only `as_of_date`s before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The out-of-line facts the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


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
    price_jumps: PriceJumps
    shares_outliers: SharesOutliers
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
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings | None = None,
    *,
    jumps_before: date | None = None,
) -> HealthReport:
    """Every metric and integrity rule at `t` (module docstring). `settings`
    defaults to `get_settings()` and is passed to every derived read. Listing
    ends, securities and classifications are read once and shared.
    `jumps_before` limits the price-jump list to sessions before it, and the
    shares-outlier list to `as_of_date`s before it."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    session = last_completed_session(t)
    listings = listing_ends_as_of(conn, t, settings)
    current = _current_from(listings, session)
    securities = securities_as_of(conn, t)
    classes = classifications_as_of(conn, t)
    return HealthReport(
        t=t,
        session=session,
        ingests=last_ingests(conn, t),
        coverage=_coverage(conn, t, session, current, securities, classes),
        gaps=bar_gaps(conn, t),
        survivorship=survivorship_gap(conn, t, settings),
        unclassifiable=_unclassifiable(securities, classes),
        static_reliance=_static_reliance(securities, classes, current),
        delisted=_delisted_names(current),
        price_jumps=_price_jumps(conn, t, settings, jumps_before),
        shares_outliers=_shares_outliers(conn, t, settings, jumps_before),
        settings={
            "liquidity_rule_enabled": settings.universe.liquidity_rule_enabled,
            "fill_price": settings.execution.fill_price,
        },
        integrity=_integrity_checks(conn, t, settings, listings),
    )


def _price_jumps(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, before: date | None
) -> PriceJumps:
    frame = price_jumps_as_of(conn, t, settings=settings)
    if before is not None:
        frame = frame.filter(pl.col("session") < before)
    return PriceJumps(frame, before)


def _shares_outliers(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, before: date | None
) -> SharesOutliers:
    frame = shares_as_of(conn, t, None, settings).outliers
    if before is not None:
        frame = frame.filter(pl.col("as_of_date") < before)
    return SharesOutliers(frame, before)


def last_ingests(conn: duckdb.DuckDBPyConnection, t: datetime) -> tuple[IngestStatus, ...]:
    """Per source, the last `ok` run and the latest run whose row is known at
    `t` (finished at or before `t`); `ingest.SOURCES` first, then any other
    source."""
    t = _validate_t(t)
    runs = conn.execute(
        """
        SELECT source, status, started_at, finished_at, chunk_cursor, message
        FROM ingestion_runs WHERE coalesce(finished_at, started_at) <= ?
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


def _current_from(listings: pl.DataFrame, session: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing with the latest `valid_from` on or before
    `session` (universe rule 2's current listing), from `listing_ends_as_of`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        held = current.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            current[row["security_id"]] = row
    return current


def _is_live(listing: dict[str, Any], session: date) -> bool:
    status, end = listing["status"], listing["end_session"]
    if status == LISTED:
        return True
    return status == TRANSFERRED and (end is None or end >= session)


def coverage(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> Coverage:
    """Bars at the last completed session for the live common and benchmark
    names then, from rows known at `t` (module docstring)."""
    t = _validate_t(t)
    session = last_completed_session(t)
    current = _current_from(listing_ends_as_of(conn, t, settings), session)
    return _coverage(
        conn, t, session, current, securities_as_of(conn, t), classifications_as_of(conn, t)
    )


def _coverage(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    current: dict[str, dict[str, Any]],
    securities: pl.DataFrame,
    classes: pl.DataFrame,
) -> Coverage:
    benchmarks = {r["security_id"] for r in securities.iter_rows(named=True) if r["benchmark"]}
    common = {
        r["security_id"] for r in classes.iter_rows(named=True) if r["security_type"] == _COMMON
    }
    live = tuple(
        sorted(
            sid
            for sid, row in current.items()
            if _is_live(row, session) and (sid in benchmarks or sid in common)
        )
    )
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


_SESSIONS_VIEW = "_health_sessions"


def bar_gaps(conn: duckdb.DuckDBPyConnection, t: datetime) -> BarGaps:
    """Interior bar holes per security, from bars known at `t` up to the last
    completed session (module docstring), computed in DuckDB against the
    session index. Bars on a non-session are left to `bars_on_sessions`."""
    t = _validate_t(t)
    session = last_completed_session(t)
    sessions = all_sessions()
    index = pl.DataFrame(
        {"session": sessions, "idx": range(len(sessions))},
        schema={"session": pl.Date, "idx": pl.Int64},
    )
    conn.register(_SESSIONS_VIEW, index.to_arrow())
    try:
        found = conn.execute(
            f"""
            WITH bars AS (
                SELECT DISTINCT p.security_id, p.session, s.idx
                FROM prices_daily p JOIN {_SESSIONS_VIEW} s ON p.session = s.session
                WHERE p.known_at <= ? AND p.session <= ?
            ), holes AS (
                SELECT security_id, session, coalesce(
                    idx - lag(idx) OVER (PARTITION BY security_id ORDER BY idx) - 1, 0
                ) AS hole
                FROM bars
            )
            SELECT security_id, min(session), max(session), count(*), sum(hole), max(hole)
            FROM holes GROUP BY security_id HAVING sum(hole) > 0 ORDER BY security_id
            """,
            [t, session],
        ).fetchall()
    finally:
        conn.unregister(_SESSIONS_VIEW)
    rows = [
        {
            "security_id": sid,
            "first_bar": first,
            "last_bar": last,
            "bars": int(bars),
            "missing_sessions": int(missing),
            "longest_run": int(longest),
        }
        for sid, first, last, bars, missing, longest in found
    ]
    return BarGaps(rows=pl.DataFrame(rows, schema=_GAP_SCHEMA))


def unclassifiable(conn: duckdb.DuckDBPyConnection, t: datetime) -> Unclassifiable:
    """Securities known at `t` classified `unclassifiable`, or with no
    classification row known at `t`."""
    t = _validate_t(t)
    return _unclassifiable(securities_as_of(conn, t), classifications_as_of(conn, t))


def _unclassifiable(securities: pl.DataFrame, classes: pl.DataFrame) -> Unclassifiable:
    known = set(securities["security_id"].to_list())
    kinds = {r["security_id"]: r["security_type"] for r in classes.iter_rows(named=True)}
    return Unclassifiable(
        unclassifiable=tuple(sorted(sid for sid in known if kinds.get(sid) == UNCLASSIFIABLE)),
        unclassified=tuple(sorted(known - set(kinds))),
    )


def static_reliance(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> StaticReliance:
    """Securities whose `securities` row, current listing or classification
    as of `t` has `provenance = snapshot_static` (module docstring)."""
    t = _validate_t(t)
    current = _current_from(listing_ends_as_of(conn, t, settings), last_completed_session(t))
    return _static_reliance(securities_as_of(conn, t), classifications_as_of(conn, t), current)


def _static_reliance(
    securities: pl.DataFrame, classes: pl.DataFrame, current: dict[str, dict[str, Any]]
) -> StaticReliance:
    known = set(securities["security_id"].to_list())
    static: dict[str, set[str]] = {
        "securities": {
            r["security_id"] for r in securities.iter_rows(named=True) if r["provenance"] == STATIC
        },
        "listings": {
            sid for sid, row in current.items() if sid in known and row["provenance"] == STATIC
        },
        "classifications": {
            r["security_id"]
            for r in classes.iter_rows(named=True)
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
    return _delisted_names(
        _current_from(listing_ends_as_of(conn, t, settings), last_completed_session(t))
    )


def _delisted_names(current: dict[str, dict[str, Any]]) -> DelistedNames:
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
        for sid, row in sorted(current.items())
        if row["status"] == DELISTED
    ]
    return DelistedNames(frame=pl.DataFrame(rows, schema=_DELISTED_SCHEMA))


def integrity_checks(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> tuple[IntegrityCheck, ...]:
    """Every rule in `INTEGRITY_RULES`, in that order (module docstring)."""
    t = _validate_t(t)
    return _integrity_checks(conn, t, settings, listing_ends_as_of(conn, t, settings))


def _integrity_checks(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, listings: pl.DataFrame
) -> tuple[IntegrityCheck, ...]:
    window = settings.master.transfer_window_sessions
    violations: dict[str, pl.DataFrame] = {
        KNOWN_AT_NOT_NULL: _count_per_table(conn, "known_at IS NULL"),
        KNOWN_AT_NOT_AFTER_INGESTED_AT: _count_per_table(conn, "known_at > ingested_at"),
        SOURCE_NOT_NULL: _count_per_table(conn, "source IS NULL"),
        PROVENANCE_ALLOWED: _bad_provenance(conn),
        BARS_ON_SESSIONS: _bars_off_sessions(conn),
        NO_DUPLICATE_BARS: _duplicate_bars(conn),
        NON_OVERLAPPING_LISTINGS: _overlapping_listings(listings, window),
        NO_BARS_AFTER_DELISTING: _bars_after_delisting(conn, t, listings, window),
        GUARDED_SIC_DEFAULT: _guarded_sic(settings),
    }
    return tuple(IntegrityCheck(rule=rule, violations=violations[rule]) for rule in INTEGRITY_RULES)


def _present_tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Table names `conn` actually has, per `dashboard.header.store_freshness`'s
    pattern: a read-only connection never migrates (`store.schema.init_schema`),
    so a store opened before a later `_FACT_TABLES` addition (e.g.
    `statement_facts`, version 9, #660) is missing it until the next writable
    open -- querying it directly would raise `duckdb.CatalogException` instead
    of the loud-but-graceful "run `tradepartner ingest`" health already gives
    for an uninitialised store."""
    rows = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    return {name for (name,) in rows}


def _count_per_table(conn: duckdb.DuckDBPyConnection, condition: str) -> pl.DataFrame:
    present = _present_tables(conn)
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        if table not in present:
            continue
        row = conn.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()
        count = int(row[0]) if row is not None else 0
        if count:
            rows.append({"table": table, "rows": count})
    return pl.DataFrame(rows, schema=_TABLE_COUNT_SCHEMA)


def _bad_provenance(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    present = _present_tables(conn)
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        if table not in present:
            continue
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


def _same_line(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two rows of one ticker from one day: one line tagged with two
    exchanges by its filers (#822), never two lines."""
    return bool(a["valid_from"] == b["valid_from"] and a["ticker"] == b["ticker"])


def _row_kind(row: dict[str, Any]) -> str:
    """`row`'s kind by the resolver's own classification (`EQUITY` or one
    of `NON_EQUITY_KINDS`, or placeholder for no ticker), shared, never
    copied, so a row health reads the same way the resolver would."""
    ticker = str(row["ticker"])
    if is_placeholder_ticker(ticker):
        return _PLACEHOLDER_KIND
    return listing_kind(ticker, row.get("class_title"))


def _resolver_ticker(ticker: str) -> str:
    """`ticker` as `ListingResolver` keys it (#846): `alpaca_symbol`'s
    spelling fold (`BF-A` -> `BF.A`), or the raw ticker when it has none,
    so health compares the same strings the resolver's own same-day-typo
    rule does."""
    return alpaca_symbol(ticker) or ticker


def _held_ticker(
    ordered: list[dict[str, Any]], index: int, day: date, pair_kind: str
) -> str | None:
    """The ticker (resolver-folded) `ordered[index]`'s security held just
    before `day`, by the resolver's own rule: the row immediately before
    `day`, and only when that row is itself `EQUITY` too while
    `pair_kind` is `EQUITY` (either of the pair's own rows; a pair reads
    a non-equity row's ticker no more than the resolver's
    `_same_day_pair` does, #846) -- except a pair with neither row
    `EQUITY`, which the resolver's EQUITY-only path never examines in the
    first place, so there is no resolver opinion for health to drift
    from; its own last ticker, of whatever kind, decides."""
    previous = next((r for r in reversed(ordered[:index]) if r["valid_from"] < day), None)
    if previous is None:
        return None
    if pair_kind == EQUITY and _row_kind(previous) != EQUITY:
        return None
    return _resolver_ticker(str(previous["ticker"]))


def _same_day_typo_pair(
    ordered: list[dict[str, Any]], index: int, current: dict[str, Any], following: dict[str, Any]
) -> bool:
    """True when `current`/`following`'s same-start pair is a cover
    page's filer noise, not a genuine overlap (#846): the ticker the
    security held just before is one of the tied tickers (the resolver's
    own rule, `is_same_day_typo`, shared here, never copied, over the
    same resolver-folded ticker spellings), or the two tickers are one
    Alpaca symbol under different filer spellings (`same_alpaca_symbol`;
    `MOTV U`/`MOTV.U`)."""
    day = current["valid_from"]
    tickers = {
        _resolver_ticker(str(current["ticker"])),
        _resolver_ticker(str(following["ticker"])),
    }
    # Either row being equity puts the pair on the resolver's EQUITY-only
    # path (quant-auditor pass 2 on #846): gating on `current` alone let a
    # mixed equity/non-equity pair's gate depend on which ticker happened
    # to sort first in `ordered`, not on kind.
    current_kind, following_kind = _row_kind(current), _row_kind(following)
    pair_kind = EQUITY if EQUITY in (current_kind, following_kind) else current_kind
    held = _held_ticker(ordered, index, day, pair_kind)
    return is_same_day_typo(held, tickers) or same_alpaca_symbol(
        str(current["ticker"]), str(following["ticker"])
    )


def _overlapping_listings(listings: pl.DataFrame, window_sessions: int) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for sid, ordered in sorted(_by_security(listings).items()):
        for i, current in enumerate(ordered[:-1]):
            following = ordered[i + 1]
            same_start = current["valid_from"] == following["valid_from"]
            flagged = (
                following
                if same_start
                and not _same_line(current, following)
                and not _same_day_typo_pair(ordered, i, current, following)
                else None
            )
            filed = current["delisting_filed_at"]
            filing_session = None if filed is None else _filing_session(filed)
            if flagged is None and filing_session is not None:
                exchange_line = next(
                    (
                        r
                        for r in ordered[i + 1 :]
                        if r["exchange"] not in OFF_EXCHANGE and not _same_line(current, r)
                    ),
                    None,
                )
                if (
                    exchange_line is not None
                    and exchange_line["valid_from"] < _window(filing_session, window_sessions)[0]
                ):
                    flagged = exchange_line
            if flagged is not None:
                rows.append(
                    {
                        "security_id": sid,
                        "ticker": current["ticker"],
                        "exchange": current["exchange"],
                        "valid_from": current["valid_from"],
                        "status": current["status"],
                        "filing_session": filing_session,
                        "next_ticker": flagged["ticker"],
                        "next_exchange": flagged["exchange"],
                        "next_valid_from": flagged["valid_from"],
                    }
                )
    return pl.DataFrame(rows, schema=_OVERLAP_SCHEMA)


def _missing_sessions(sessions: tuple[date, ...], after: date, before: date) -> int:
    """XNYS sessions strictly between `after` and `before`."""
    return max(0, bisect_left(sessions, before) - bisect_right(sessions, after))


def _resumed_bars(
    bars: list[date],
    effective_on: date,
    next_start: date | None,
    window_sessions: int,
    sessions: tuple[date, ...],
) -> list[date]:
    """The bars of `bars` (sorted) after `effective_on` and before
    `next_start` that come back after more than `window_sessions` missing
    sessions, and every bar after the first of them; the bars before it are
    the listing's own tail. The first gap is counted from the last bar on or
    before `effective_on`, or from `effective_on` when there is none."""
    start = bisect_right(bars, effective_on)
    previous = bars[start - 1] if start else effective_on
    resumed: list[date] = []
    for session in bars[start:]:
        if next_start is not None and session >= next_start:
            break
        if resumed or _missing_sessions(sessions, previous, session) > window_sessions:
            resumed.append(session)
        previous = session
    return resumed


def _bars_after_delisting(
    conn: duckdb.DuckDBPyConnection, t: datetime, listings: pl.DataFrame, window_sessions: int
) -> pl.DataFrame:
    bounds: list[dict[str, Any]] = []
    for ordered in _by_security(listings).values():
        for row in ordered:
            if row["status"] != DELISTED:
                continue
            later = [
                o["valid_from"]
                for o in ordered
                if o["valid_from"] > row["valid_from"] and o["exchange"] not in OFF_EXCHANGE
            ]
            bounds.append({**row, "_next": min(later, default=None)})
    if not bounds:
        return pl.DataFrame([], schema=_AFTER_DELISTING_SCHEMA)
    ids = sorted({b["security_id"] for b in bounds})
    marks = ", ".join("?" for _ in ids)
    bars: dict[str, list[date]] = defaultdict(list)
    for sid, session in conn.execute(
        f"""
        SELECT DISTINCT security_id, session FROM prices_daily
        WHERE known_at <= ? AND security_id IN ({marks})
        ORDER BY security_id, session
        """,
        [t, *ids],
    ).fetchall():
        bars[sid].append(session)
    sessions = all_sessions()
    rows = [
        {
            "security_id": b["security_id"],
            "session": s,
            "ticker": b["ticker"],
            "exchange": b["exchange"],
            "effective_on": b["effective_on"],
        }
        for b in bounds
        for s in _resumed_bars(
            bars[b["security_id"]], b["effective_on"], b["_next"], window_sessions, sessions
        )
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

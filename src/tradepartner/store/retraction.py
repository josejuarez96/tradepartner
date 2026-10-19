"""Stored master rows the current rules no longer derive, and their
retraction (#859; spec req 3, amendment 2026-10-04 (#859)).

`securities` and `listings` are append-only, and ingest writes only rows
that change an as-of read (`ingest` module docstring): a key the builder
stops producing is never touched. So when a master rule is corrected (#835
undid #826's false WillScot successor `0001647088@2020-08-10`), the row the
old rule wrote stays live for ever. Deleting it would rewrite what every
as-of read before the correction returned; this module withdraws it as a
revision instead.

- **Underived.** `underived(build, securities, listings, skip_ciks)` is
  pure: each stored live row (the latest revision per key as of the check,
  not retracted) with `source = 'edgar'` and `provenance = 'filing'`
  (or `filing_name` for listings, #1597) whose
  key (`security_id` for `securities`; `security_id, ticker, exchange,
  valid_from` for `listings`) is not among `build`'s rows, and whose CIK is
  not in `skip_ciks`. Only EDGAR rows: the master builder derives nothing
  else, and a benchmark seeded from config (`backfill-benchmark`, #840) is
  never its row to judge. Filing-derived rows come from full-history
  inputs (the filing index, cover pages, Forms 25 and filename evidence),
  so a build that lacks one has changed its rules. A `snapshot` or
  `snapshot_static` row comes
  from the companies snapshot, which lists only names trading today: a
  delisted or renamed name's snapshot row is legitimately absent from
  today's build, and retracting it would erase delisted history (quant
  audit of #872). Such rows are never judged; a false one needs another
  remedy. Nor is a successor id (`<cik>@<valid_from>`, #820) with a
  stored non-filing-derived listing, or any of its rows: one relisted on
  snapshot evidence has a `filing` securities row, yet exists only while
  the snapshot names it. `skip_ciks` are CIKs whose filings failed or were skipped this
  run (`ingest._unjudged_ciks`): their rows are not judged either. A key
  the build still derives with other values (a reworded class title) is a
  revision ingest writes, not an underived row.
- **Kept (#922).** `split_kept(found, keep, derived)` sets aside every row
  of a security on the owner's `master.keep_successors` (a successor the
  owner accepted, MTCH's `0000891103@2020-08-10` on #828, that the rules no
  longer derive) while the build derives no `securities` row for it. An
  EDGAR ingest's check does not record them, and `master-retract` reports
  them as kept and never proposes them. Once the build derives the
  successor again, the keep no longer applies, so a stale listing of it is
  judged like any other (quant audit of #923).
- **Chains (#821, T187c).** `underived_chains(live, derived_successors,
  skip_ciks)` is the `successor_chains` counterpart of `underived`: each row
  `live_chains_as_of` returns whose   successor id is not in
  `derived_successors` (the successors `build_chains` honours at this run),
  source `edgar` and provenance `filing`, and neither side's CIK in
  `skip_ciks` (an unjudged predecessor has no listings, so `build_chains`
  would refuse `NO_LISTING` and propose a valid chain). `master-retract` runs
  the same `build_chains`, so a refused entry (a `spac` predecessor, lost
  evidence, a successor listing that no longer matches) leaves its row
  underived, as the ingest's chain writer skips it. Their keys are
  `successor_id`. They are never recorded in `master_underived` (health's
  `underived_master_rows` is the `securities` and `listings` rule), and a
  retraction keeps the withdrawn row's `withdrawn_known_at` so
  `registry.data_vintage` counts it.
- **Retraction.** `retraction(row, at)` is the stored row with `retracted
  = TRUE`, `known_at = ingested_at = at`: a revision of its key stamped at
  the correcting run, never back-dated (a row known after `at` raises).
  As-of reads (`store.asof._latest_as_of`) choose the latest revision first
  and then drop a retracted one, so a read at T before `at` returns the old
  row and a read after it does not. If a later build derives the key again,
  ingest's revision rule writes it back as live (`retracted` is one of the
  key's value columns), stamped at that run.
- **Recorded.** `record_underived` writes one `master_underived` row per
  underived row of a check (an EDGAR ingest chunk, a `master-retract` run),
  under the check's `ingestion_runs` id and clock. `underived_as_of` reads
  the latest check finished by T (the last EDGAR run row with status `ok`
  or `retracted`) and keeps the rows still live at T: health's
  `underived_master_rows`. `master_underived` is not a fact table and no
  as-of read of the master reads it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb
import polars as pl

from tradepartner.store.asof import _validate_t, listings_as_of
from tradepartner.store.db import insert_row
from tradepartner.store.master import MasterBuild, securities_as_of, security_cik
from tradepartner.timeutil import ensure_tz_aware_utc

#: The source of every row the master builder derives.
EDGAR = "edgar"
#: Provenances derived from full-history filing inputs (#1597).
FILING = "filing"
FILING_NAME = "filing_name"
#: The status of a `master-retract --apply` run row: a master check, never a
#: fresh ingest (health and execution read only `ok` runs as fresh).
RETRACTED = "retracted"
#: `ingestion_runs.mode` of a `master-retract --apply` run.
RETRACT = "retract"
#: Run statuses whose `master_underived` rows are a complete check.
CHECK_STATUSES: tuple[str, ...] = ("ok", RETRACTED)

#: Per retractable table, the natural key an as-of read collapses on.
KEYS: dict[str, tuple[str, ...]] = {
    "securities": ("security_id",),
    "listings": ("security_id", "ticker", "exchange", "valid_from"),
    "successor_chains": ("successor_id",),
}

Row = dict[str, Any]

#: Columns of `underived_as_of`'s frame (and health's violations).
UNDERIVED_SCHEMA: dict[str, Any] = {
    "table": pl.Utf8,
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "valid_from": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
    "run_id": pl.Utf8,
}


@dataclass(frozen=True)
class Underived:
    """A stored live row of `table` that the current build does not derive."""

    table: str
    row: Mapping[str, Any]

    @property
    def key(self) -> tuple[Any, ...]:
        """The row's natural key in its table."""
        return tuple(self.row[c] for c in KEYS[self.table])

    def describe(self) -> str:
        """One line for the terminal: the table, key and stored `known_at`."""
        key = " ".join(str(part) for part in self.key)
        return f"{self.table}: {key} (known {self.row['known_at'].isoformat()})"


def cik_of(security_id: str) -> str:
    """The CIK an EDGAR `security_id` belongs to: the id before its `:`
    class or `@` successor separator, not its first ten characters (#1838)."""
    return security_cik(security_id)


def underived(
    build: MasterBuild,
    securities: Iterable[Mapping[str, Any]],
    listings: Iterable[Mapping[str, Any]],
    skip_ciks: frozenset[str] = frozenset(),
) -> tuple[Underived, ...]:
    """The stored live EDGAR filing-derived rows the build does not derive,
    outside `skip_ciks` (module docstring), securities first, each table
    sorted by key. Pure."""
    stored_rows = {"securities": list(securities), "listings": list(listings)}
    # A successor (`<cik>@<valid_from>`, #820) relisted on snapshot evidence
    # has a `filing` securities row but exists only while the snapshot names
    # it (quant audit of #872): its own non-filing listing marks it.
    snapshot_evidenced = {
        row["security_id"]
        for row in stored_rows["listings"]
        if "@" in row["security_id"] and row["provenance"] not in {FILING, FILING_NAME}
    }
    # #1662: with `edgar.filename_listings` off the build reads no file names,
    # so a stored `filing_name` row is not "no longer derived", only unread.
    judged = {
        "securities": {FILING},
        "listings": {FILING, FILING_NAME} if build.filename_enabled else {FILING},
    }
    found: list[Underived] = []
    for table, built in (("securities", build.securities), ("listings", build.listings)):
        columns = KEYS[table]
        derived = {tuple(row[c] for c in columns) for row in built}
        rows = [
            Underived(table, dict(row))
            for row in stored_rows[table]
            if row["source"] == EDGAR
            and row["provenance"] in judged[table]
            and cik_of(row["security_id"]) not in skip_ciks
            and row["security_id"] not in snapshot_evidenced
            and tuple(row[c] for c in columns) not in derived
        ]
        found.extend(sorted(rows, key=lambda u: tuple(str(part) for part in u.key)))
    return tuple(found)


def split_kept(
    found: tuple[Underived, ...], keep: frozenset[str], derived: frozenset[str]
) -> tuple[tuple[Underived, ...], tuple[Underived, ...]]:
    """`found` split into the rows not kept and the rows of a security in
    `keep` that is not in `derived` (the build's `securities` ids; module
    docstring), each in `found`'s order. Pure."""
    held = keep - derived
    proposed = tuple(u for u in found if u.row["security_id"] not in held)
    kept = tuple(u for u in found if u.row["security_id"] in held)
    return proposed, kept


def stored_underived(
    conn: duckdb.DuckDBPyConnection,
    build: MasterBuild,
    at: datetime,
    skip_ciks: frozenset[str] = frozenset(),
) -> tuple[Underived, ...]:
    """`underived` over the master rows live at `at`."""
    return underived(
        build,
        securities_as_of(conn, at).iter_rows(named=True),
        listings_as_of(conn, at).iter_rows(named=True),
        skip_ciks,
    )


#: The live chain of a successor at `at` (#821): its revision known by `at`,
#: unretracted, that no later revision supersedes -- the same rule as
#: `store.master._LIVE_CHAIN_SQL`, over stored rows.
_LIVE_CHAIN_SQL = """
    SELECT * FROM successor_chains c
    WHERE known_at <= ? AND NOT c.retracted
      AND NOT EXISTS (
          SELECT 1 FROM successor_chains n
          WHERE n.successor_id = c.successor_id AND n.ingested_at > c.ingested_at
      )
"""


def live_chains_as_of(
    conn: duckdb.DuckDBPyConnection, at: datetime
) -> tuple[Mapping[str, Any], ...]:
    """The `successor_chains` rows live at `at` (module docstring): one per
    successor, its latest revision known by `at`, never a retracted or
    superseded one. Empty on a store without the table (a read-only
    connection below version 31)."""
    at = ensure_tz_aware_utc(at, field_name="at")
    present = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'successor_chains'"
    ).fetchone()
    if present is None or not present[0]:
        return ()
    cursor = conn.execute(_LIVE_CHAIN_SQL, [at])
    names = [column[0] for column in cursor.description]
    return tuple(dict(zip(names, values, strict=True)) for values in cursor.fetchall())


def underived_chains(
    chains: Iterable[Mapping[str, Any]],
    derived_successors: frozenset[str],
    skip_ciks: frozenset[str] = frozenset(),
) -> tuple[Underived, ...]:
    """The live `successor_chains` rows no entry derives at this run (#821,
    T187c): source `edgar`, provenance `filing`, a successor id not in
    `derived_successors` (`build_chains`'s honoured successors), and neither
    side's CIK in `skip_ciks` (an unjudged predecessor has no listings, so
    `build_chains` would refuse `NO_LISTING` and propose a valid chain).
    `master-retract` proposes and retracts them; a refused entry (a `spac`
    predecessor, lost evidence, a successor listing that no longer matches) is
    therefore underived too, matching the ingest's chain writer, which skips
    it. Pure."""
    found = [
        Underived("successor_chains", dict(row))
        for row in chains
        if row["source"] == EDGAR
        and row["provenance"] == FILING
        and cik_of(row["successor_id"]) not in skip_ciks
        and cik_of(row["predecessor_id"]) not in skip_ciks
        and row["successor_id"] not in derived_successors
    ]
    return tuple(sorted(found, key=lambda u: tuple(str(part) for part in u.key)))


def retraction(row: Mapping[str, Any], at: datetime) -> Row:
    """The revision that withdraws `row`'s key from `at` on (module
    docstring). Raises `ValueError` if `row` is known after `at`."""
    at = ensure_tz_aware_utc(at, field_name="at")
    if row["known_at"] >= at:
        raise ValueError(
            f"retraction at {at.isoformat()} would be back-dated: the row is known at "
            f"{row['known_at'].isoformat()}"
        )
    return {**row, "retracted": True, "known_at": at, "ingested_at": at}


def write_retractions(
    conn: duckdb.DuckDBPyConnection, found: Iterable[Underived], at: datetime
) -> int:
    """Insert a `retraction` of each row in `found`; return how many. A
    `successor_chains` row also keeps its own `known_at` as `withdrawn_known_at`
    (#821): the vintage counts a retraction from there."""
    written = 0
    for item in found:
        row = retraction(item.row, at)
        if item.table == "successor_chains":
            row["withdrawn_known_at"] = item.row["known_at"]
        insert_row(conn, item.table, row)
        written += 1
    return written


def record_underived(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    recorded_at: datetime,
    found: Iterable[Underived],
) -> None:
    """Write one `master_underived` row per item of `found` for the check
    `run_id`, recorded at `recorded_at` (tz-aware)."""
    recorded_at = ensure_tz_aware_utc(recorded_at, field_name="recorded_at")
    for item in found:
        listing = item.table == "listings"
        insert_row(
            conn,
            "master_underived",
            {
                "run_id": run_id,
                "recorded_at": recorded_at,
                "table_name": item.table,
                "security_id": item.row["security_id"],
                "ticker": item.row["ticker"] if listing else None,
                "exchange": item.row["exchange"] if listing else None,
                "valid_from": item.row["valid_from"] if listing else None,
                "known_at": item.row["known_at"],
            },
        )


def underived_as_of(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
    """The latest master check finished by `t` (module docstring): its
    `master_underived` rows whose key is still live at `t`, sorted by table
    and key. Empty when no check has run, or on a store without the table
    (a read-only connection to a version-10 store)."""
    t = _validate_t(t)
    present = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'master_underived'"
    ).fetchone()
    if present is None or not present[0]:
        return pl.DataFrame(schema=UNDERIVED_SCHEMA)
    marks = ", ".join("?" for _ in CHECK_STATUSES)
    latest = conn.execute(
        f"""
        SELECT run_id FROM ingestion_runs
        WHERE source = ? AND status IN ({marks}) AND finished_at <= ?
        ORDER BY finished_at DESC, started_at DESC LIMIT 1
        """,
        [EDGAR, *CHECK_STATUSES, t],
    ).fetchone()
    if latest is None:
        return pl.DataFrame(schema=UNDERIVED_SCHEMA)
    rows = conn.execute(
        """
        SELECT table_name, security_id, ticker, exchange, valid_from, known_at, run_id
        FROM master_underived WHERE run_id = ?
        """,
        [latest[0]],
    ).fetchall()
    live = {
        "securities": {
            (r["security_id"],) for r in securities_as_of(conn, t).iter_rows(named=True)
        },
        "listings": {
            tuple(r[c] for c in KEYS["listings"])
            for r in listings_as_of(conn, t).iter_rows(named=True)
        },
    }
    kept = [
        dict(zip(UNDERIVED_SCHEMA, row, strict=True))
        for row in rows
        if ((row[1],) if row[0] == "securities" else (row[1], row[2], row[3], row[4]))
        in live[row[0]]
    ]
    kept.sort(key=lambda r: (r["table"], r["security_id"], str(r["ticker"]), str(r["valid_from"])))
    return pl.DataFrame(kept, schema=UNDERIVED_SCHEMA)

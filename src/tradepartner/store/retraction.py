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
  not retracted) with `source = 'edgar'` and `provenance = 'filing'` whose
  key (`security_id` for `securities`; `security_id, ticker, exchange,
  valid_from` for `listings`) is not among `build`'s rows, and whose CIK is
  not in `skip_ciks`. Only EDGAR rows: the master builder derives nothing
  else, and a benchmark seeded from config (`backfill-benchmark`, #840) is
  never its row to judge. Only `filing` rows: they come from full-history
  inputs (the filing index, cover pages, Forms 25), so a build that lacks
  one has changed its rules. A `snapshot` or `snapshot_static` row comes
  from the companies snapshot, which lists only names trading today: a
  delisted or renamed name's snapshot row is legitimately absent from
  today's build, and retracting it would erase delisted history (quant
  audit of #872). Such rows are never judged; a false one needs another
  remedy. `skip_ciks` are CIKs whose filings failed or were skipped this
  run (`ingest._unjudged_ciks`): their rows are not judged either. A key
  the build still derives with other values (a reworded class title) is a
  revision ingest writes, not an underived row.
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
from tradepartner.store.master import MasterBuild, securities_as_of
from tradepartner.timeutil import ensure_tz_aware_utc

#: The source of every row the master builder derives.
EDGAR = "edgar"
#: The one provenance judged (module docstring).
FILING = "filing"
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
    """The CIK an EDGAR `security_id` belongs to: its first ten characters
    (`<cik>`, `<cik>:<class>`, `<cik>@<valid_from>`)."""
    return security_id[:10]


def underived(
    build: MasterBuild,
    securities: Iterable[Mapping[str, Any]],
    listings: Iterable[Mapping[str, Any]],
    skip_ciks: frozenset[str] = frozenset(),
) -> tuple[Underived, ...]:
    """The stored live EDGAR `filing` rows whose key `build` does not derive,
    outside `skip_ciks` (module docstring), securities first, each table
    sorted by key. Pure."""
    found: list[Underived] = []
    for table, stored, built in (
        ("securities", securities, build.securities),
        ("listings", listings, build.listings),
    ):
        columns = KEYS[table]
        derived = {tuple(row[c] for c in columns) for row in built}
        rows = [
            Underived(table, dict(row))
            for row in stored
            if row["source"] == EDGAR
            and row["provenance"] == FILING
            and cik_of(row["security_id"]) not in skip_ciks
            and tuple(row[c] for c in columns) not in derived
        ]
        found.extend(sorted(rows, key=lambda u: tuple(str(part) for part in u.key)))
    return tuple(found)


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
    """Insert a `retraction` of each row in `found`; return how many."""
    written = 0
    for item in found:
        insert_row(conn, item.table, retraction(item.row, at))
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

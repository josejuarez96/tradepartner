"""Truncation-invariance harness (spec "Look-ahead" > truncation invariance):

    f(T) on the full fixture store == f(T) on a store truncated to
    ``known_at <= T``, for probe timestamps placed just before and just
    after every distinct ``known_at`` in the fixture.

Two pieces:

- `TruncatedStore` builds, once, a connection where every fact table (the
  tables listed in `tradepartner.store.schema.TABLE_NAMES` that carry a
  `known_at` column; `ingestion_runs` and `schema_version` are
  bookkeeping, not point-in-time facts, and no as-of function reads them,
  so they are not copied) holds only rows with `known_at <= T` -- and its
  `.at(t)` method re-truncates it to a new `T` cheaply, for repeated use
  across every probe of a truncation-invariance test.
- `probe_timestamps` generates one probe just before and one just after
  every distinct `known_at` present in a store, so a truncation-invariance
  test can walk every boundary where a fact could wrongly appear or
  disappear.

Probe epsilon: **1 microsecond** (`PROBE_EPSILON`), DuckDB `TIMESTAMPTZ`'s
finest resolution. A probe at `known_at - PROBE_EPSILON` is guaranteed to
exclude that `known_at` (`known_at <= T` is false) and a probe at
`known_at + PROBE_EPSILON` is guaranteed to include it, without the probe
also landing on a *different* distinct `known_at` in the fixture (the
generator fixture's `known_at` values are never within 1 microsecond of
each other by construction).

**Mechanism (why `.at(t)` is cheap across thousands of probes).**
`TruncatedStore.__init__` copies every fact table **once**, unfiltered,
into a `truncation_source` schema on a fresh scratch connection
(`truncation_source.prices_daily`, `truncation_source.corporate_actions`,
...), then creates each `main.<table>` view **once**, filtered against a
one-row `probe_t` table rather than a literal `T`:
`CREATE VIEW main.<table> AS SELECT * FROM truncation_source.<table>
WHERE known_at <= (SELECT t FROM probe_t)`. `.at(t)` then only ever runs
`UPDATE probe_t SET t = ?`: the view's SQL text never changes, so DuckDB
never has to re-plan or re-catalog it. An earlier version of this harness
called `CREATE OR REPLACE VIEW` with a literal `T` baked into the SQL text
on every probe; that measured **superlinear** (compounding) across a few
thousand calls on the same connection -- each replacement left the
connection's query planning measurably slower for the next one, even
though building the view in isolation was fast. The `probe_t` indirection
was adopted specifically because that compounding did not reproduce with
it: the view's catalog entry is created exactly once. `main.<table>` --
the name every as-of function queries -- resolves to the view, not a real
table, so a function sees a genuinely truncated store: this is not the
same thing as calling an as-of function on the untruncated store twice,
because a function that forgot to filter a *joined* table by
`known_at <= T` would see rows through the view that a correct
implementation would not.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

import duckdb

from tradepartner.store import schema
from tradepartner.store.db import configure_connection

#: The gap used on each side of a `known_at` to build a probe timestamp.
PROBE_EPSILON: Final[timedelta] = timedelta(microseconds=1)

#: Fact tables in the store: every table `known_at <= T` filtering applies
#: to. Excludes `ingestion_runs` (job status, no `known_at`) and
#: `schema_version` (schema bookkeeping, no `known_at`) -- see
#: `store/schema.py`'s module docstring for why those two carry none of the
#: four common fact-table columns.
_FACT_TABLES: Final[tuple[str, ...]] = tuple(
    name for name in schema.TABLE_NAMES if name not in ("ingestion_runs", "schema_version")
)

_SOURCE_SCHEMA = "truncation_source"


class TruncatedStore:
    """A reusable "store truncated to some T" connection, built once from a
    source store and re-truncated cheaply via `.at(t)` (see this module's
    docstring for the mechanism).

    `tables` (default: every fact table) selects which tables get a
    `main.<table>` view; the rest are simply absent from `main`, so an
    as-of function that queries one of them fails loudly rather than
    silently seeing untruncated data.
    """

    def __init__(
        self, source: duckdb.DuckDBPyConnection, tables: Sequence[str] | None = None
    ) -> None:
        self.tables: tuple[str, ...] = tuple(tables) if tables is not None else _FACT_TABLES
        self._conn = duckdb.connect(":memory:")
        configure_connection(self._conn)
        self._conn.execute(f"CREATE SCHEMA {_SOURCE_SCHEMA}")
        for table in _FACT_TABLES:
            # A vectorized Arrow hand-off (rather than `fetchall` +
            # `executemany` row by row): this happens once per
            # `TruncatedStore`, not once per probe.
            arrow_table = source.execute(f"SELECT * FROM {table}").to_arrow_table()
            self._conn.register("_truncate_source", arrow_table)
            try:
                self._conn.execute(
                    f"CREATE TABLE {_SOURCE_SCHEMA}.{table} AS SELECT * FROM _truncate_source"
                )
            finally:
                self._conn.unregister("_truncate_source")

        self._conn.execute("CREATE TABLE probe_t (t TIMESTAMPTZ)")
        self._conn.execute("INSERT INTO probe_t VALUES (NULL)")
        for table in self.tables:
            self._conn.execute(
                f"CREATE VIEW main.{table} AS "
                f"SELECT * FROM {_SOURCE_SCHEMA}.{table} "
                "WHERE known_at <= (SELECT t FROM probe_t)"
            )

    def at(self, t: datetime) -> duckdb.DuckDBPyConnection:
        """Re-point every `main.<table>` view at `t` and return the
        connection (the same connection object on every call -- callers
        should query it immediately, before the next `.at()` call)."""
        self._conn.execute("UPDATE probe_t SET t = ?", [t])
        return self._conn

    def close(self) -> None:
        self._conn.close()


def distinct_known_ats(
    conn: duckdb.DuckDBPyConnection, tables: Sequence[str] | None = None
) -> list[datetime]:
    """Every distinct `known_at` across `tables` (default: every fact table)
    of `conn`, sorted."""
    values: set[datetime] = set()
    for table in tables if tables is not None else _FACT_TABLES:
        rows = conn.execute(f"SELECT DISTINCT known_at FROM {table}").fetchall()
        values.update(row[0] for row in rows)
    return sorted(values)


def probe_timestamps(
    conn: duckdb.DuckDBPyConnection, tables: Sequence[str] | None = None
) -> list[datetime]:
    """A probe `PROBE_EPSILON` before and after every distinct `known_at`
    among `tables` (default: every fact table, i.e. every distinct
    `known_at` in the whole store) in `conn`, deduplicated and sorted."""
    probes: set[datetime] = set()
    for known_at in distinct_known_ats(conn, tables):
        probes.add(known_at - PROBE_EPSILON)
        probes.add(known_at + PROBE_EPSILON)
    return sorted(probes)

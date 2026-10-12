"""The freshness line every page shows in its header (docs/design/dashboard.md,
principle 5; #185).

"As of" is the latest `known_at` in any fact table at or before `t`; "last
updated" the latest finish of an `ok` ingest run at or before `t`. Both are
read through the page's connection, and both are None on an empty store (or
one missing those tables).

The borrow snapshot's table and source are left out of both (#1506, T171b:
`borrow_status` is fetched after every other source and is read by nothing
these two numbers describe, so a morning snapshot must not show the store
fresher than its bars).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import duckdb
import streamlit as st

from tradepartner.ingest import OK
from tradepartner.store.asof import ASSETS_SOURCE
from tradepartner.store.schema import TABLE_PROVENANCE_VALUES

#: Fact tables the freshness reads skip (#1506, T171b). A `borrow_status`
#: snapshot runs after the bars and is read by nothing the header describes,
# so a morning snapshot must not show the store fresher than its bars: the
# "as of" reads the latest `known_at` of every other fact table.
_FRESHNESS_SKIPPED_TABLES: Final[frozenset[str]] = frozenset({"borrow_status"})

#: Ingest sources the freshness reads skip, beside `ASSETS_SOURCE`'s own
#: reader in `store.asof`: the borrow snapshot's run row has nothing to do
#: with when the bars arrived.
_FRESHNESS_SKIPPED_SOURCES: Final[frozenset[str]] = frozenset({ASSETS_SOURCE})


@dataclass(frozen=True)
class Freshness:
    """When the store's data and its last good ingest are from."""

    as_of: datetime | None
    last_updated: datetime | None


def now() -> datetime:
    """The pages' clock (tests pin it)."""
    return datetime.now(UTC)


def store_freshness(conn: duckdb.DuckDBPyConnection, t: datetime) -> Freshness:
    """The freshness at `t`: as of and last updated (module docstring).

    Both leave out the borrow snapshot (#1506, T171b): `_FRESHNESS_SKIPPED_TABLES`
    from the `known_at` reads and `_FRESHNESS_SKIPPED_SOURCES` from the run
    reads, so neither a table nothing here describes nor an ingest that runs
    after every other can make the store look fresher than its bars.
    """
    tables = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    present = {name for (name,) in tables}
    latest = [
        conn.execute(f"SELECT max(known_at) FROM {table} WHERE known_at <= ?", [t]).fetchone()
        for table in TABLE_PROVENANCE_VALUES
        if table in present and table not in _FRESHNESS_SKIPPED_TABLES
    ]
    known = [row[0] for row in latest if row is not None and row[0] is not None]
    run = (
        conn.execute(
            "SELECT max(finished_at) FROM ingestion_runs WHERE status = ? AND finished_at <= ? "
            "AND source NOT IN (SELECT unnest(?::VARCHAR[]))",
            [OK, t, sorted(_FRESHNESS_SKIPPED_SOURCES)],
        ).fetchone()
        if "ingestion_runs" in present
        else None
    )
    return Freshness(as_of=max(known, default=None), last_updated=None if run is None else run[0])


def when(value: datetime | None) -> str:
    """A timestamp as `YYYY-MM-DD HH:MM UTC`, or "never"."""
    return "never" if value is None else value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def render_freshness(freshness: Freshness) -> None:
    """The header caption: "as of … · last updated …"."""
    st.caption(f"as of {when(freshness.as_of)} · last updated {when(freshness.last_updated)}")

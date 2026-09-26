"""The freshness line every page shows in its header (docs/design/dashboard.md,
principle 5; #185).

"As of" is the latest `known_at` in any fact table at or before `t`; "last
updated" the latest finish of an `ok` ingest run at or before `t`. Both are
read through the page's connection, and both are None on an empty store (or
one missing those tables).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb
import streamlit as st

from tradepartner.ingest import OK
from tradepartner.store.schema import TABLE_PROVENANCE_VALUES


@dataclass(frozen=True)
class Freshness:
    """When the store's data and its last good ingest are from."""

    as_of: datetime | None
    last_updated: datetime | None


def now() -> datetime:
    """The pages' clock (tests pin it)."""
    return datetime.now(UTC)


def store_freshness(conn: duckdb.DuckDBPyConnection, t: datetime) -> Freshness:
    """The freshness at `t`: as of and last updated (module docstring)."""
    tables = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    present = {name for (name,) in tables}
    latest = [
        conn.execute(f"SELECT max(known_at) FROM {table} WHERE known_at <= ?", [t]).fetchone()
        for table in TABLE_PROVENANCE_VALUES
        if table in present
    ]
    known = [row[0] for row in latest if row is not None and row[0] is not None]
    run = (
        conn.execute(
            "SELECT max(finished_at) FROM ingestion_runs WHERE status = ? AND finished_at <= ?",
            [OK, t],
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

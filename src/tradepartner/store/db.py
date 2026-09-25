"""DuckDB connection management for the point-in-time store.

Spec req 1: "Ingest holds the file read-write only while committing a
chunk and releases it between chunks; every other process uses short-lived
`read_only=True` connections and shows a 'store busy' state when locked."
This module is where both halves of that rule live:

- `open_read_only` opens a short-lived, read-only connection.
- `open_for_write` is a context manager scoped to one committed chunk: it
  retries acquiring the file's write lock for `store.lock_retry_seconds`
  (ADR 0003 rule 1, spec req 9), then raises `StoreLockedError`; it commits
  on normal exit, rolls back on any exception, and always closes the
  connection afterwards so the lock is released.

DuckDB accepts a naive `datetime` as a `TIMESTAMPTZ` parameter without
complaint, silently treating it as a UTC instant — CLAUDE.md's "datetimes
are always timezone-aware UTC" rule has no enforcement from DuckDB itself.
`utc_now`, `ensure_tz_aware` and `insert_row` are how this module enforces
it in Python before a value ever reaches a table.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import duckdb

from tradepartner.config import Settings

# Exponential backoff between lock-acquisition attempts, capped so a long
# `lock_retry_seconds` doesn't mean a long final wait past the deadline.
_RETRY_INITIAL_DELAY_SECONDS = 0.05
_RETRY_MAX_DELAY_SECONDS = 1.0


class StoreLockedError(RuntimeError):
    """The store's DuckDB file could not be locked for writing within
    `store.lock_retry_seconds` (spec req 9)."""


def utc_now() -> datetime:
    """The current instant, tz-aware UTC — the only clock the store uses
    for `ingested_at` (CLAUDE.md: datetimes are always timezone-aware UTC)."""
    return datetime.now(UTC)


def ensure_tz_aware(value: datetime, *, field: str) -> datetime:
    """Return `value` unchanged, or raise `ValueError` if it is naive.

    DuckDB itself does not reject a naive datetime bound to a `TIMESTAMPTZ`
    parameter (verified: it silently stores it as a UTC instant), so this
    is the enforcement point for "datetimes are always timezone-aware UTC".
    """
    if value.tzinfo is None:
        raise ValueError(f"{field} must be tz-aware, got a naive datetime: {value!r}")
    return value


def configure_connection(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Pin the session timezone to UTC so `TIMESTAMPTZ` values round-trip
    as UTC regardless of the host machine's local timezone."""
    conn.execute("SET TimeZone='UTC'")
    return conn


def insert_row(conn: duckdb.DuckDBPyConnection, table: str, row: Mapping[str, Any]) -> None:
    """Insert one row into `table`, validating every `datetime` value in
    `row` is tz-aware before it reaches DuckDB (see module docstring).

    `table` is always a name from `tradepartner.store.schema.TABLE_NAMES`
    supplied by our own code, never external input.
    """
    for field, value in row.items():
        if isinstance(value, datetime):
            ensure_tz_aware(value, field=field)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(row.values()))


def open_read_only(settings: Settings) -> duckdb.DuckDBPyConnection:
    """A short-lived, read-only connection to the store.

    Callers hold this only as long as needed for one read; DuckDB itself
    rejects any write attempted on it (spec req 1).
    """
    conn = duckdb.connect(database=settings.store.path, read_only=True)
    return configure_connection(conn)


@contextmanager
def open_for_write(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A write connection to the store, scoped to one committed chunk.

    Retries acquiring the file's write lock for up to
    `store.lock_retry_seconds`, then raises `StoreLockedError`. On normal
    exit the transaction commits; on any exception it rolls back; the
    connection is always closed afterwards so the lock is released between
    chunks (spec req 1, req 9).
    """
    deadline = time.monotonic() + settings.store.lock_retry_seconds
    delay = _RETRY_INITIAL_DELAY_SECONDS
    conn: duckdb.DuckDBPyConnection | None = None
    while conn is None:
        try:
            conn = duckdb.connect(database=settings.store.path, read_only=False)
        except duckdb.IOException as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StoreLockedError(
                    f"could not acquire write lock on {settings.store.path!r} "
                    f"within {settings.store.lock_retry_seconds}s"
                ) from exc
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _RETRY_MAX_DELAY_SECONDS)

    configure_connection(conn)
    conn.begin()
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()

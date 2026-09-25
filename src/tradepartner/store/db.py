"""DuckDB connection management for the point-in-time store.

Spec req 1: "Ingest holds the file read-write only while committing a
chunk and releases it between chunks; every other process uses short-lived
`read_only=True` connections and shows a 'store busy' state when locked."
This module is where both halves of that rule live:

- `open_read_only` is a context manager for a single short-lived, read-only
  connection.
- `open_for_write` is a context manager scoped to one committed chunk: it
  retries acquiring the file's write lock for `store.lock_retry_seconds`
  (ADR 0003 rule 1, spec req 9), then raises `StoreLockedError`; it commits
  on normal exit, rolls back on any exception, and always closes the
  connection afterwards so the lock is released.

Two DuckDB behaviors, confirmed by hand against a real DuckDB file rather
than assumed, shape both functions:

- DuckDB accepts a naive `datetime` as a `TIMESTAMPTZ` parameter without
  complaint, interpreting it in the connection's configured session
  `TimeZone` — it is stored as UTC here only because `configure_connection`
  pins that session `TimeZone` to UTC, not because DuckDB itself treats a
  naive value as a UTC instant. `utc_now`, `ensure_tz_aware` and
  `insert_row` are how this module enforces "datetimes are always
  timezone-aware UTC" (CLAUDE.md) in Python before a value ever reaches a
  table. `ensure_tz_aware` delegates to the shared
  `tradepartner.timeutil.ensure_tz_aware_utc` (issue #30) so this module and
  `adapters.broker` cannot drift out of sync on what counts as valid.
- DuckDB's Python client shares **one database instance per file path per
  process**: a second `duckdb.connect()` call to the same path from the
  *same* process, opened in a different `read_only` mode than an already-open
  connection, raises `duckdb.ConnectionException` ("different
  configuration") rather than a lock error — retrying that connect call
  would never succeed, since the blocker is this same process, not another
  one. Both `open_read_only` and `open_for_write` treat that the same way
  they treat a genuine cross-process file lock: `StoreLockedError`.
- `information_schema.columns` (which `insert_row`'s per-column-type
  validation queries) is not scoped to the connection's current database
  by default: an `ATTACH`ed database with a same-named table contributes
  rows too, and an unfiltered query can silently pick up the wrong
  catalog's column type for a same-named column (verified by hand).
  `_column_types` filters on `current_database()`/`current_schema()` to
  rule that out, and never caches an empty result (a table that does not
  exist yet), so a lookup before `schema.init_schema` fails loudly instead
  of silently skipping every validation forever.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from tradepartner.config import Settings
from tradepartner.timeutil import ensure_tz_aware_utc

# Backoff bounds between lock-acquisition attempts (`open_for_write` reads
# these from config: `store.lock_retry_initial_delay_seconds` and
# `.lock_retry_max_delay_seconds`), capped so a long `lock_retry_seconds`
# doesn't mean a long final wait past the deadline.

_TIMESTAMPTZ_TYPE = "TIMESTAMP WITH TIME ZONE"
_DATE_TYPE = "DATE"

# Column-name -> DuckDB type-name cache, one entry per open connection, so
# `insert_row` introspects `information_schema` once per (connection,
# table) rather than on every single insert. Keyed weakly so a closed
# connection's entry is dropped instead of leaking.
_ColumnTypes = dict[str, dict[str, str]]
_column_types_cache: weakref.WeakKeyDictionary[duckdb.DuckDBPyConnection, _ColumnTypes] = (
    weakref.WeakKeyDictionary()
)


class StoreLockedError(RuntimeError):
    """The store's DuckDB file could not be locked for writing (or read
    from) within `store.lock_retry_seconds`, or is already open in this
    same process with a different mode (spec req 9)."""


def utc_now() -> datetime:
    """The current instant, tz-aware UTC — the only clock the store uses
    for `ingested_at` (CLAUDE.md: datetimes are always timezone-aware UTC)."""
    return datetime.now(UTC)


def ensure_tz_aware(value: datetime, *, field: str) -> datetime:
    """Return `value` normalized to UTC, or raise `ValueError` if it is
    naive.

    DuckDB itself does not reject a naive datetime bound to a `TIMESTAMPTZ`
    parameter (verified: it silently stores it as an instant in the
    connection's session `TimeZone`), so this is the enforcement point for
    "datetimes are always timezone-aware UTC". Delegates to the shared
    `tradepartner.timeutil.ensure_tz_aware_utc` (issue #30); this name and
    signature are kept as a thin wrapper since other in-flight code imports
    them.
    """
    return ensure_tz_aware_utc(value, field_name=field)


def configure_connection(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Pin the session timezone to UTC and disable extension auto-install
    and auto-load.

    `TimeZone='UTC'` is what makes `TIMESTAMPTZ` values round-trip as UTC
    regardless of the host machine's local timezone. The two
    `autoinstall`/`autoload` settings exist because DuckDB's own C++ HTTP
    client (used to fetch extensions on demand) does not go through
    Python's `socket` module — the tests' autouse no-network fixture,
    which patches `socket.socket.connect`/`.connect_ex`, cannot see or
    block it, so this is disabled explicitly instead.
    """
    conn.execute("SET TimeZone='UTC'")
    conn.execute("SET autoinstall_known_extensions=false")
    conn.execute("SET autoload_known_extensions=false")
    return conn


def _column_types(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """`{column_name: duckdb_type_name}` for `table`, cached per connection.

    Filters on `table_catalog`/`table_schema` matching the connection's
    *current* database and schema: without that filter, an `ATTACH`ed
    database containing a same-named table (e.g. another `prices_daily`)
    contributes rows to `information_schema.columns` too, and a same-named
    column from the wrong catalog can silently overwrite the real one when
    the rows collapse into a `{column: type}` dict (verified by hand: an
    attached `:memory:` db's `prices_daily.known_at VARCHAR` silently
    replaced the real `TIMESTAMPTZ` type without this filter).

    Raises `ValueError` if `table` has no columns at all — i.e. it does
    not exist yet — rather than caching an empty result, which would
    silently skip every type check for every row inserted before
    `schema.init_schema` runs.
    """
    per_table = _column_types_cache.setdefault(conn, {})
    if table not in per_table:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? AND table_catalog = current_database() "
            "AND table_schema = current_schema()",
            [table],
        ).fetchall()
        if not rows:
            raise ValueError(f"table {table!r} has no columns; run init_schema first")
        per_table[table] = dict(rows)
    return per_table[table]


def forget_column_types(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop any cached column-type info for `conn`.

    `schema.init_schema` calls this after (re-)creating every table, so a
    cache populated (or, before the "never cache empty" fix above, a cache
    that could never be populated) from before the tables existed is never
    reused once they do.
    """
    _column_types_cache.pop(conn, None)


def insert_row(conn: duckdb.DuckDBPyConnection, table: str, row: Mapping[str, Any]) -> None:
    """Insert one row into `table`, validating each value against its
    column's actual DuckDB type before it reaches DuckDB.

    DuckDB does not itself reject a naive `datetime` on a `TIMESTAMPTZ`
    column, nor a `datetime` (as opposed to a `date`) on a `DATE` column —
    both are silently accepted (verified by hand). This checks, by
    querying `information_schema` for `table`'s column types:

    - a `TIMESTAMPTZ` column's value must be a tz-aware `datetime`;
    - a `DATE` column's value must be a `date` that is *not* a `datetime`
      (`datetime` is a `date` subclass, so `isinstance` alone would accept
      a datetime — e.g. a session close in another timezone — where only a
      calendar date belongs).

    Other column types are passed through unchecked; DuckDB's own
    conversion errors cover those.

    `TIMESTAMPTZ` values are normalized to UTC (via `ensure_tz_aware`)
    before being bound, so a value's original tzinfo (whatever it was)
    never reaches DuckDB — a stored instant always round-trips from one
    canonical form, not the caller's original offset (issue #43). The
    caller's `row` Mapping itself is never mutated; a new list of values is
    built for the bind.

    `table` is always a name from `tradepartner.store.schema.TABLE_NAMES`
    supplied by our own code, never external input.
    """
    column_types = _column_types(conn, table)
    bound_values: list[Any] = []
    for field, value in row.items():
        col_type = column_types.get(field)
        if col_type == _TIMESTAMPTZ_TYPE:
            if not isinstance(value, datetime):
                raise TypeError(
                    f"{table}.{field} is TIMESTAMPTZ; expected a tz-aware datetime, "
                    f"got {type(value).__name__}: {value!r}"
                )
            value = ensure_tz_aware(value, field=f"{table}.{field}")
        elif col_type == _DATE_TYPE and (
            not isinstance(value, date) or isinstance(value, datetime)
        ):
            raise TypeError(
                f"{table}.{field} is DATE; expected a date (not a datetime), "
                f"got {type(value).__name__}: {value!r}"
            )
        bound_values.append(value)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", bound_values)


def _is_lock_error(exc: duckdb.IOException) -> bool:
    """True if `exc` is DuckDB's cross-process file-lock conflict, as
    opposed to some other I/O failure (e.g. a missing parent directory)
    that retrying will never fix."""
    message = str(exc)
    return "Could not set lock" in message or "Conflicting lock" in message


def _same_process_locked_error(settings: Settings, *, mode: str) -> StoreLockedError:
    return StoreLockedError(
        f"{settings.store.path!r} is already open in this process in a different "
        f"mode than the {mode} connection just requested — DuckDB shares one "
        "database instance per file path per process, so this is not a "
        "cross-process lock and retrying would not help; close the other "
        "connection in this process first"
    )


@contextmanager
def open_read_only(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A single short-lived, read-only connection to the store.

    Callers hold this only as long as needed for one read; DuckDB itself
    rejects any write attempted on it (spec req 1). Makes exactly one
    connection attempt (no retry): a caller that wants "busy" behavior
    (spec req 1's "store busy" state) catches `StoreLockedError` itself.
    """
    try:
        conn = duckdb.connect(database=settings.store.path, read_only=True)
    except duckdb.ConnectionException as exc:
        raise _same_process_locked_error(settings, mode="read-only") from exc
    except duckdb.IOException as exc:
        if _is_lock_error(exc):
            raise StoreLockedError(
                f"could not open a read-only connection to {settings.store.path!r}: "
                "the file is locked for writing by another process"
            ) from exc
        raise
    try:
        configure_connection(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def open_for_write(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A write connection to the store, scoped to one committed chunk.

    Creates the store file's parent directory if it does not exist, then
    retries acquiring the file's write lock for up to
    `store.lock_retry_seconds` (only on a genuine lock conflict — any other
    `IOException`, e.g. a still-missing parent directory, propagates
    immediately rather than spinning out the full retry window), then
    raises `StoreLockedError`. On normal exit the transaction commits; on
    any exception it rolls back; the connection is always closed
    afterwards so the lock is released between chunks (spec req 1, req 9).
    """
    store_path = Path(settings.store.path)
    if store_path.parent != Path() and not store_path.parent.exists():
        store_path.parent.mkdir(parents=True, exist_ok=True)

    max_delay = settings.store.lock_retry_max_delay_seconds

    deadline = time.monotonic() + settings.store.lock_retry_seconds
    delay = settings.store.lock_retry_initial_delay_seconds
    conn: duckdb.DuckDBPyConnection | None = None
    while conn is None:
        try:
            conn = duckdb.connect(database=settings.store.path, read_only=False)
        except duckdb.ConnectionException as exc:
            raise _same_process_locked_error(settings, mode="write") from exc
        except duckdb.IOException as exc:
            if not _is_lock_error(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StoreLockedError(
                    f"could not acquire write lock on {settings.store.path!r} "
                    f"within {settings.store.lock_retry_seconds}s"
                ) from exc
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, max_delay)

    try:
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

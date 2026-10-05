"""#953: the test process opens every DuckDB database single-threaded.

DuckDB's default starts `threads - 1` native worker threads per database
instance and tears them down on close. On Linux that teardown path can
segfault inside DuckDB's bundled jemalloc, which killed xdist workers on
CI. `tests/conftest.py:_single_threaded_duckdb` defaults `threads` to 1.
These tests pin that default, so removing the fixture fails loudly here
rather than as an intermittent worker crash.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from tradepartner.config import Settings
from tradepartner.store.db import open_for_write, open_read_only


def _threads(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT current_setting('threads')").fetchone()
    assert row is not None
    return int(row[0])


def test_a_bare_connect_is_single_threaded(tmp_path: Path) -> None:
    for database in (":memory:", str(tmp_path / "bare.duckdb")):
        conn = duckdb.connect(database)
        try:
            assert _threads(conn) == 1
        finally:
            conn.close()


def test_the_store_helpers_open_single_threaded(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        assert _threads(conn) == 1
    with open_read_only(settings) as conn:
        assert _threads(conn) == 1


def test_a_callers_explicit_config_is_kept() -> None:
    conn = duckdb.connect(":memory:", config={"threads": 2, "TimeZone": "UTC"})
    try:
        assert _threads(conn) == 2
        row = conn.execute("SELECT current_setting('TimeZone')").fetchone()
        assert row == ("UTC",)
    finally:
        conn.close()


def test_positional_read_only_still_works(tmp_path: Path) -> None:
    path = str(tmp_path / "ro.duckdb")
    duckdb.connect(path).close()
    conn = duckdb.connect(path, True)
    try:
        assert _threads(conn) == 1
    finally:
        conn.close()

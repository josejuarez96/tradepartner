"""#1418: optional DuckDB `memory_limit`/`threads` caps on store connections.

Unset (the default) leaves DuckDB's own setting; set, the value reaches
every connection `open_read_only`/`open_for_write` hands out; a value
DuckDB would not take, or a non-positive one, is refused at config load.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from tradepartner.config import Settings, StoreConfig
from tradepartner.store.db import configure_connection, open_for_write, open_read_only


def _setting(conn: duckdb.DuckDBPyConnection, name: str) -> str:
    row = conn.execute("SELECT current_setting(?)", [name]).fetchone()
    assert row is not None
    return str(row[0])


def _bare_defaults() -> tuple[str, str]:
    conn = duckdb.connect(":memory:")
    try:
        return _setting(conn, "memory_limit"), _setting(conn, "threads")
    finally:
        conn.close()


def _settings(tmp_path: Path, **store: object) -> Settings:
    return Settings(_env_file=None, store={"path": str(tmp_path / "s.duckdb"), **store})


def test_unset_keys_leave_duckdbs_defaults(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.store.memory_limit is None
    assert settings.store.threads is None
    expected = _bare_defaults()
    with open_for_write(settings) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        assert (_setting(conn, "memory_limit"), _setting(conn, "threads")) == expected
    with open_read_only(settings) as conn:
        assert (_setting(conn, "memory_limit"), _setting(conn, "threads")) == expected


def test_set_keys_reach_write_and_read_only_connections(tmp_path: Path) -> None:
    settings = _settings(tmp_path, memory_limit="512MiB", threads=3)
    with open_for_write(settings) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        assert _setting(conn, "memory_limit") == "512.0 MiB"
        assert _setting(conn, "threads") == "3"
    with open_read_only(settings) as conn:
        assert _setting(conn, "memory_limit") == "512.0 MiB"
        assert _setting(conn, "threads") == "3"


def test_configure_connection_without_store_sets_no_limits() -> None:
    expected = _bare_defaults()
    conn = duckdb.connect(":memory:")
    try:
        configure_connection(conn)
        assert (_setting(conn, "memory_limit"), _setting(conn, "threads")) == expected
        configure_connection(conn, StoreConfig(memory_limit="1GiB"))
        assert _setting(conn, "memory_limit") == "1.0 GiB"
        assert _setting(conn, "threads") == expected[1]
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("6GB", "6GB"), ("6 gb", "6gb"), (" 1.5GiB ", "1.5GiB"), ("800MB", "800MB")],
)
def test_memory_limit_accepts_duckdb_sizes(raw: str, normalized: str) -> None:
    assert StoreConfig(memory_limit=raw).memory_limit == normalized


@pytest.mark.parametrize(
    "bad", ["", "6", "10%", "abc", "0GB", "0.0MB", "-1GB", "6GB'; SET threads=1; --", "6 PB"]
)
def test_memory_limit_refuses_bad_values(bad: str) -> None:
    with pytest.raises(ValidationError, match="memory_limit"):
        StoreConfig(memory_limit=bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_threads_refuses_non_positive(bad: int) -> None:
    with pytest.raises(ValidationError, match="threads"):
        StoreConfig(threads=bad)


def test_keys_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORE__MEMORY_LIMIT", "4GB")
    monkeypatch.setenv("STORE__THREADS", "2")
    store = Settings(_env_file=None).store
    assert store.memory_limit == "4GB"
    assert store.threads == 2

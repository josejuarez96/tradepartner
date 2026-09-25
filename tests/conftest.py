"""Shared test fixtures (T4): no-network enforcement and the fixture store.

- `_no_network` (autouse) makes every test fail loudly instead of silently
  reaching the internet: `tests, on every PR, run against the fixture
  universe with the network disabled` (spec "Users & usage"). Tests marked
  `network` (T2's client smoke tests) are skipped in CI, not exempted
  here — this fixture blocks the socket layer regardless of markers, so a
  network test must mock instead of relying on being unmarked.
- `fixture_store` builds a fresh in-memory DuckDB store, initializes the
  schema, and loads any CSVs under `tests/fixtures/universe/` by table
  name — a no-op until T5 populates that directory.
- `settings` returns a `Settings` pointed at a tmp-path store file, with no
  `.env` loaded, for tests that need `Settings` rather than a live
  connection (e.g. `store.db` lock/retry tests).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import configure_connection

_FIXTURES_UNIVERSE_DIR = Path(__file__).parent / "fixtures" / "universe"


def _blocked_connect(*_args: object, **_kwargs: object) -> None:
    raise OSError(
        "network access is disabled in tests (tests/conftest.py:_no_network); "
        "use a fixture adapter or mock the client instead"
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every socket connection attempt for the duration of each test.

    `socket.socket.connect` is the choke point both `httpx` and `alpaca-py`
    (and anything else) ultimately call through, so patching it here is
    sufficient without special-casing per-library transports.
    """
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """`Settings` with `store.path` pointed at a fresh tmp file and no
    `.env` loaded, so tests are isolated from both the real store and the
    real environment."""
    return Settings(_env_file=None, store={"path": str(tmp_path / "test_store.duckdb")})


@pytest.fixture
def fixture_store() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh in-memory DuckDB connection with the schema applied and, if
    `tests/fixtures/universe/` exists, every `<table>.csv` in it loaded
    into the table of the same name.

    Yields the open connection; closes it on teardown. Silently a no-op on
    the CSV-loading step until T5 adds fixtures.
    """
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)

    if _FIXTURES_UNIVERSE_DIR.is_dir():
        for csv_path in sorted(_FIXTURES_UNIVERSE_DIR.glob("*.csv")):
            table = csv_path.stem
            if table not in schema.TABLE_NAMES:
                raise ValueError(
                    f"fixture CSV {csv_path.name!r} does not match any store "
                    f"table name (known tables: {schema.TABLE_NAMES})"
                )
            conn.execute(
                f"INSERT INTO {table} SELECT * FROM read_csv_auto(?, header=True)",
                [str(csv_path)],
            )

    try:
        yield conn
    finally:
        conn.close()

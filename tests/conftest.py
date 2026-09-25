"""Shared test fixtures (T4): no-network enforcement and the fixture store.

- `_no_network` (autouse) makes every test fail loudly instead of silently
  reaching the internet: `tests, on every PR, run against the fixture
  universe with the network disabled` (spec "Users & usage"). Tests marked
  `network` (T2's client smoke tests) are skipped in CI, not exempted
  here — this fixture blocks the socket layer regardless of markers, so a
  network test must mock instead of relying on being unmarked. Both
  `socket.socket.connect` (raises) and `.connect_ex` (returns a nonzero
  errno instead of raising — its normal contract) are patched, since a
  library may use either.
- `load_universe_fixtures` (used by `fixture_store`, also called directly
  by `tests/test_fixture_loader.py`) loads every `<table>.csv` under a
  fixtures directory into the store table of the same name.
- `fixture_store` builds a fresh in-memory DuckDB store, initializes the
  schema, and loads `tests/fixtures/universe/` this way — a no-op until T5
  populates that directory.
- `settings` returns a `Settings` pointed at a tmp-path store file, with no
  `.env` loaded, for tests that need `Settings` rather than a live
  connection (e.g. `store.db` lock/retry tests).
"""

from __future__ import annotations

import csv
import errno
import re
import socket
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import configure_connection

_FIXTURES_UNIVERSE_DIR = Path(__file__).parent / "fixtures" / "universe"

# An ISO-8601 UTC offset ("+00:00", "+0000", "-05:00") or a literal "Z"
# suffix. Deliberately strict: a fixture author who forgets the offset
# gets a loud failure here, not a silently-wrong instant (DuckDB itself
# accepts an offset-less timestamp string without complaint — see
# store.db's module docstring).
_TZ_OFFSET_PATTERN = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def _blocked_connect(*_args: object, **_kwargs: object) -> None:
    raise OSError(
        "network access is disabled in tests (tests/conftest.py:_no_network); "
        "use a fixture adapter or mock the client instead"
    )


def _blocked_connect_ex(*_args: object, **_kwargs: object) -> int:
    # connect_ex()'s contract is to return an errno instead of raising;
    # honor that contract rather than raising, so a caller that branches
    # on the return value (rather than catching an exception) still sees
    # network access refused.
    return errno.ECONNREFUSED


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every socket connection attempt for the duration of each test.

    `socket.socket.connect`/`.connect_ex` are the choke points both
    `httpx` and `alpaca-py` (and anything else built on the stdlib socket
    layer) ultimately call through, so patching them here is sufficient
    without special-casing per-library transports. DuckDB's own extension
    auto-install/auto-load HTTP client bypasses this entirely (it isn't
    built on Python's socket module); `store.db.configure_connection`
    disables that separately.
    """
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """`Settings` with `store.path` pointed at a fresh tmp file and no
    `.env` loaded, so tests are isolated from both the real store and the
    real environment."""
    return Settings(_env_file=None, store={"path": str(tmp_path / "test_store.duckdb")})


def _timestamptz_columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND data_type = 'TIMESTAMP WITH TIME ZONE'",
        [table],
    ).fetchall()
    return {row[0] for row in rows}


def _assert_timestamptz_cells_carry_offset(csv_path: Path, columns: set[str]) -> None:
    """Fail loudly if any `columns` cell in `csv_path` lacks an explicit
    UTC offset or `Z` suffix (empty cells, i.e. NULL, are exempt)."""
    if not columns:
        return
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for line_no, record in enumerate(reader, start=2):  # header is line 1
            for column in columns:
                value = record.get(column) or ""
                if not value:
                    continue
                if not _TZ_OFFSET_PATTERN.search(value):
                    raise ValueError(
                        f"{csv_path.name}:{line_no} column {column!r} = {value!r} has "
                        "no explicit UTC offset or 'Z' suffix; DuckDB would silently "
                        "interpret it in the session TimeZone rather than reject it"
                    )


def load_universe_fixtures(conn: duckdb.DuckDBPyConnection, fixtures_dir: Path) -> None:
    """Load every `<table>.csv` in `fixtures_dir` into the store table of
    the same name. A no-op if `fixtures_dir` does not exist.

    Columns bind **by CSV header name** (`INSERT ... BY NAME`), so a
    fixture's column order need not match the table's; every cell is read
    as text (`all_varchar=true`) and DuckDB casts it to the target
    column's real type on insert. Every value destined for a `TIMESTAMPTZ`
    column is checked for an explicit offset first (see
    `_assert_timestamptz_cells_carry_offset`).
    """
    if not fixtures_dir.is_dir():
        return
    for csv_path in sorted(fixtures_dir.glob("*.csv")):
        table = csv_path.stem
        if table not in schema.TABLE_NAMES:
            raise ValueError(
                f"fixture CSV {csv_path.name!r} does not match any store "
                f"table name (known tables: {schema.TABLE_NAMES})"
            )
        tz_columns = _timestamptz_columns(conn, table)
        _assert_timestamptz_cells_carry_offset(csv_path, tz_columns)
        conn.execute(
            f"INSERT INTO {table} BY NAME SELECT * FROM read_csv(?, header=true, all_varchar=true)",
            [str(csv_path)],
        )


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
    load_universe_fixtures(conn, _FIXTURES_UNIVERSE_DIR)
    try:
        yield conn
    finally:
        conn.close()

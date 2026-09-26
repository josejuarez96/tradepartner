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

# A bare calendar date, nothing else. DuckDB casts a datetime-looking
# string ("2020-01-02 23:30:00") to DATE without complaint, silently
# discarding the time-of-day — a session is a calendar day, not an
# instant (see calendar.py), so a fixture author who pastes a timestamp
# into a DATE column gets a loud failure here instead.
_BARE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TIMESTAMPTZ_TYPE = "TIMESTAMP WITH TIME ZONE"
_DATE_TYPE = "DATE"


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


def _table_column_types(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """`{column_name: duckdb_type_name}` for `table`, freshly queried (no
    caching here — unlike `store.db._column_types`, this runs once per
    fixture CSV, not once per row)."""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return dict(rows)


def _not_null_varchar_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """The `VARCHAR` columns of `table` that are `NOT NULL`.

    Passed as `read_csv`'s `force_not_null` so a CSV cell destined for one
    of these columns round-trips as an empty string rather than `NULL`
    (verified by hand: DuckDB's `read_csv` treats *both* an unquoted and a
    quoted-empty field as `NULL` by default, regardless of `all_varchar`,
    which would otherwise trip the column's `NOT NULL` constraint even
    though the schema itself uses `''` — never `NULL` — as its sentinel
    for e.g. `facts.class_member`'s undimensioned case; see
    `store/schema.py`'s comment on that column and issue #28).
    """
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND data_type = 'VARCHAR' AND is_nullable = 'NO'",
        [table],
    ).fetchall()
    return [row[0] for row in rows]


def _assert_header_matches_columns(
    csv_path: Path, table: str, column_types: dict[str, str]
) -> None:
    """Fail loudly if any CSV header cell is not an exact, case-sensitive
    match for one of `table`'s real column names.

    `INSERT ... BY NAME` binds SQL identifiers case-*insensitively*, so a
    typo'd or wrong-case header (e.g. `KNOWN_AT` for `known_at`) would
    otherwise bind silently instead of surfacing the mistake.
    """
    with csv_path.open(newline="") as fh:
        header = next(csv.reader(fh), [])
    unknown = [column for column in header if column not in column_types]
    if unknown:
        raise ValueError(
            f"{csv_path.name}: header column(s) {unknown!r} do not exactly match "
            f"(case-sensitive) any column of table {table!r}; known columns: "
            f"{sorted(column_types)}"
        )


def _assert_cell_formats(csv_path: Path, column_types: dict[str, str]) -> None:
    """Fail loudly if any cell destined for a `TIMESTAMPTZ` column lacks an
    explicit UTC offset/`Z` suffix, or any cell destined for a `DATE`
    column is not a bare `YYYY-MM-DD` (empty cells, i.e. NULL, are exempt
    from both)."""
    tz_columns = {name for name, kind in column_types.items() if kind == _TIMESTAMPTZ_TYPE}
    date_columns = {name for name, kind in column_types.items() if kind == _DATE_TYPE}
    if not tz_columns and not date_columns:
        return
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for line_no, record in enumerate(reader, start=2):  # header is line 1
            for column in tz_columns:
                value = record.get(column) or ""
                if value and not _TZ_OFFSET_PATTERN.search(value):
                    raise ValueError(
                        f"{csv_path.name}:{line_no} column {column!r} = {value!r} has "
                        "no explicit UTC offset or 'Z' suffix; DuckDB would silently "
                        "interpret it in the session TimeZone rather than reject it"
                    )
            for column in date_columns:
                value = record.get(column) or ""
                if value and not _BARE_DATE_PATTERN.fullmatch(value):
                    raise ValueError(
                        f"{csv_path.name}:{line_no} column {column!r} = {value!r} is not "
                        "a bare YYYY-MM-DD date; DuckDB would silently accept a "
                        "datetime-looking value on a DATE column too, discarding the "
                        "time-of-day"
                    )


def load_universe_fixtures(conn: duckdb.DuckDBPyConnection, fixtures_dir: Path) -> None:
    """Load every `<table>.csv` in `fixtures_dir` into the store table of
    the same name. A no-op if `fixtures_dir` does not exist.

    Columns bind **by CSV header name** (`INSERT ... BY NAME`), so a
    fixture's column order need not match the table's; every cell is read
    as text (`all_varchar=true`) and DuckDB casts it to the target
    column's real type on insert. Before that, every CSV header cell must
    exactly match a real column name (`_assert_header_matches_columns`),
    and every `TIMESTAMPTZ`/`DATE` cell must be in the expected format
    (`_assert_cell_formats`). Every `VARCHAR NOT NULL` column is passed to
    `read_csv`'s `force_not_null` (`_not_null_varchar_columns`) so an
    empty cell for one of those columns loads as `''`, matching the
    schema's own sentinel convention, instead of `NULL`.
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
        column_types = _table_column_types(conn, table)
        _assert_header_matches_columns(csv_path, table, column_types)
        _assert_cell_formats(csv_path, column_types)
        force_not_null = _not_null_varchar_columns(conn, table)
        conn.execute(
            f"INSERT INTO {table} BY NAME SELECT * FROM "
            "read_csv(?, header=true, all_varchar=true, force_not_null=?)",
            [str(csv_path), force_not_null],
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


@pytest.fixture
def fixture_store_path(tmp_path: Path) -> Path:
    """A temp-file store with the schema applied and the fixture universe
    loaded, closed before it is returned, so a test can open its own write
    and read-only connections to it (backtest plan T39b; shared by
    `tests/backtest/` and `tests/oracle/`). The file is never
    `settings.store.path`, so trials on it may be synthetic."""
    path = tmp_path / "fixture_store.duckdb"
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, _FIXTURES_UNIVERSE_DIR)
    finally:
        conn.close()
    return path

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
  fixtures directory into the store table of the same name — generic over
  `schema.TABLE_NAMES`, so `statement_facts.csv` (#660, T76) loads the
  same way as every other fixture CSV, with no code change here. It also
  creates `store_markers` and writes the `fixture` marker row
  (`lab_schema.write_fixture_marker`; strategy-lab spec, Definitions, Fixture
  marker), so every fixture store and every temp-file copy built through it
  (`fixture_store_path`, the `backtest-runner`'s) carries the marker the real
  store never has.
- `lab_store` is `fixture_store` with `lab_schema.apply_lab_schema` applied
  (strategy-lab plan T101); `mark_pre_lab` is the one writer of
  `pre_lab_hypotheses` outside the lab migration.
- `fixture_store` builds a fresh in-memory DuckDB store, initializes the
  schema, and loads `tests/fixtures/universe/` this way — a no-op until T5
  populates that directory.
- `_reset_edgar_rate_limit_block` (autouse) clears `edgar_raw`'s
  process-wide rate-limit block (#761), so a test that ends inside a
  persistent `403` cannot make a later test's first `403` fail fast.
- `settings` returns a `Settings` pointed at a tmp-path store file, with no
  `.env` loaded, for tests that need `Settings` rather than a live
  connection (e.g. `store.db` lock/retry tests).
- `_single_threaded_duckdb` (autouse, session) makes every `duckdb.connect`
  in the test process default to `threads=1` (#953): see its docstring.
"""

from __future__ import annotations

import csv
import errno
import os
import re
import socket
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.config import Settings
from tradepartner.store import lab_schema, schema
from tradepartner.store.db import configure_connection, utc_now

_FIXTURES_UNIVERSE_DIR = Path(__file__).parent / "fixtures" / "universe"

# CI shards pytest across N parallel jobs (#1112); this keeps the same test run as
# one process, just split into N invocations with no new dependency. Weight is each
# unit's own total seconds (`call` + `setup` + `teardown`, summed when a unit covers
# several items) read off the CI shards' `--durations=0 --durations-min=1.0` tables (ci.yml
# prints them), averaged over runs 37679158243 and 37681698737 (2026-10-07, #1192; a unit
# varies by up to a third between runs. The 2026-10-06 table came from a local run and
# left new or CI-slow units at the default, so shards ran 3.7 to 11.8 min); units at 3 s
# or more are listed, everything else defaults to `_DEFAULT_TEST_FILE_WEIGHT`.
# A stale weight still balances fine — it only shifts which shard a unit lands on, never which tests
# run — so this table does not need to be kept in lockstep with the suite; re-measure
# and update it only if a shard's wall clock drifts noticeably from the others.
#
# A unit is normally a whole file (see `_shard_unit`): a file's module/session-scoped
# fixtures then build at most once per shard. tests/lookahead/test_backtest_invariance.py
# is the one exception (`_SPLIT_BY_TEST` below): a real CI run at file-granularity put it
# alone in its own shard and it still took 17-18 min (3-5x its next-heaviest sibling),
# because its few parametrized cases don't share fixtures across cadences anyway (each
# `[month_end]`/`[daily]`/`[week_end]` variant pays its own ~60-200s setup — confirmed
# from this same profiling run) and xdist's scheduler happened to stack several of them
# on one worker. Splitting it by test, keyed by nodeid below, loses nothing a whole-file
# bucket would have saved and lets those cases land on different shards.
_HEAVY_TEST_FILE_WEIGHTS: dict[str, float] = {
    "tests/lookahead/test_backtest_invariance.py::"
    "test_truncation_invariance_at_every_rebalance[week_end]": 425.0,
    "tests/lookahead/test_asof_invariance.py": 398.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_truncation_invariance_at_every_rebalance[daily]": 383.0,
    "tests/lookahead/test_gap_invariance.py": 335.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_truncation_invariance_at_every_rebalance[month_end]": 307.0,
    "tests/lookahead/test_universe_invariance.py": 267.0,
    "tests/lookahead/test_backtest_plan_timing.py": 250.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_prefix_invariance_at_every_rebalance[daily]": 225.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_prefix_invariance_at_every_rebalance[month_end]": 221.0,
    "tests/execution/test_run_exits.py": 209.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_truncation_invariance_at_every_rebalance[profitability]": 204.0,
    "tests/execution/test_crash_resume.py": 178.0,
    "tests/execution/test_run_stop.py": 169.0,
    "tests/execution/test_run_trade.py": 154.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_prefix_invariance_at_every_rebalance[week_end]": 153.0,
    "tests/backtest/test_results.py": 132.0,
    "tests/oracle/test_bt_oracle.py": 121.0,
    "tests/test_backfill.py": 102.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_prefix_invariance_at_every_rebalance[profitability]": 94.0,
    "tests/test_health.py": 85.0,
    "tests/execution/test_resume.py": 75.0,
    "tests/test_cli_backtest.py": 74.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_revisions_known_after_t_i_leave_run_to_t_i_unchanged[month_end]": 67.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_revisions_known_after_t_i_leave_run_to_t_i_unchanged[week_end]": 65.0,
    "tests/execution/test_run_marks.py": 63.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_revisions_known_after_t_i_leave_run_to_t_i_unchanged[daily]": 62.0,
    "tests/execution/test_run_core.py": 62.0,
    "tests/test_llm_boundary.py": 61.0,
    "tests/execution/test_window_settle_gate.py": 58.0,
    "tests/backtest/test_run.py": 45.0,
    "tests/test_fixture_universe.py": 42.0,
    "tests/adapters/test_fixture_prices.py": 39.0,
    "tests/execution/test_window_settle.py": 36.0,
    "tests/backtest/test_engine.py": 36.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_revisions_known_after_t_i_leave_run_to_t_i_unchanged[profitability]": 25.0,
    "tests/execution/test_run_plan.py": 24.0,
    "tests/store/test_delistings.py": 22.0,
    "tests/execution/test_window_stop.py": 20.0,
    "tests/lookahead/test_backtest_invariance.py::test_the_run_is_not_vacuous[month_end]": 19.0,
    "tests/lookahead/test_backtest_invariance.py::"
    "test_a_10k_accepted_after_close_t_i_reaches_only_runs_planning_after_t_i[profitability]": 16.0,
    "tests/lookahead/test_backtest_invariance.py::test_the_run_is_not_vacuous[daily]": 16.0,
    "tests/dashboard/test_health_page.py": 14.0,
    "tests/store/test_master.py": 13.0,
    "tests/execution/test_ops.py": 13.0,
    "tests/execution/test_wrapper_phases.py": 12.0,
    "tests/lookahead/test_backtest_invariance.py::test_the_run_is_not_vacuous[week_end]": 11.0,
    "tests/lookahead/test_backtest_invariance.py::test_the_run_is_not_vacuous[profitability]": 11.0,
    "tests/test_backfill_fetch_set.py": 10.0,
    "tests/test_cli.py": 9.0,
    "tests/execution/test_planning.py": 8.0,
    "tests/test_ingest.py": 8.0,
    "tests/execution/test_wrapper_reattempts.py": 8.0,
    "tests/dashboard/test_backtest_page.py": 7.0,
    "tests/test_no_forbidden_imports.py": 7.0,
    "tests/execution/test_collect.py": 6.0,
    "tests/dashboard/test_override_page.py": 6.0,
    "tests/execution/test_sdk_boundary.py": 5.0,
    "tests/execution/test_reconcile_run.py": 5.0,
    "tests/execution/test_boundaries.py": 5.0,
    "tests/backtest/test_engine_exits.py": 5.0,
    "tests/test_calendar.py": 4.0,
    "tests/test_shares_plausibility.py": 4.0,
    "tests/execution/test_accept_rejections_boundary.py": 4.0,
    "tests/store/test_classify.py": 4.0,
    "tests/store/test_asof.py": 4.0,
}
_DEFAULT_TEST_FILE_WEIGHT = 0.4

# Files bucketed by individual test (nodeid) rather than as a whole file — see the
# comment on `_HEAVY_TEST_FILE_WEIGHTS` above. Keep this list short: splitting a file
# that *does* share an expensive module/session fixture across its tests would make
# that fixture rebuild once per shard its tests land in, instead of once overall.
_SPLIT_BY_TEST: frozenset[str] = frozenset({"tests/lookahead/test_backtest_invariance.py"})

_SHARD_INDEX_ENV = "PYTEST_SHARD_INDEX"
_SHARD_COUNT_ENV = "PYTEST_SHARD_COUNT"

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


def shard_assignment(units: Iterable[str], shard_count: int) -> dict[str, int]:
    """Deterministically bucket shard units into ``shard_count`` shards (#1112).

    A unit is normally a whole file path; for the few files in `_SPLIT_BY_TEST` it's
    one test's nodeid instead (see `_shard_unit`) — this function doesn't care which,
    it just balances whatever strings it's given by weight.

    Greedy longest-processing-time bin packing: units are sorted heaviest-first (ties
    broken by the unit string itself, for a result that doesn't depend on set/hash
    iteration order) from `_HEAVY_TEST_FILE_WEIGHTS` (default weight for everything
    else) and each goes to the shard currently holding the least weight, lowest index
    breaking ties.

    Pure and total: every unit gets exactly one shard index in ``[0, shard_count)``,
    and the mapping depends only on the input set, not on the order items were
    collected in.
    """
    if shard_count < 1:
        raise ValueError(f"shard_count must be >= 1, got {shard_count}")
    loads = [0.0] * shard_count
    assignment: dict[str, int] = {}
    ordered = sorted(
        set(units),
        key=lambda u: (-_HEAVY_TEST_FILE_WEIGHTS.get(u, _DEFAULT_TEST_FILE_WEIGHT), u),
    )
    for unit in ordered:
        weight = _HEAVY_TEST_FILE_WEIGHTS.get(unit, _DEFAULT_TEST_FILE_WEIGHT)
        shard = min(range(shard_count), key=lambda i: (loads[i], i))
        assignment[unit] = shard
        loads[shard] += weight
    return assignment


def _shard_unit(item: pytest.Item, rootdir: Path) -> str:
    """This item's bucketing key: its file, or its own nodeid for `_SPLIT_BY_TEST`."""
    rel = item.path.relative_to(rootdir).as_posix()
    return item.nodeid if rel in _SPLIT_BY_TEST else rel


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only this shard's items, when CI sets `PYTEST_SHARD_INDEX`/`_COUNT` (#1112).

    No-op when either is unset, so a local `uv run pytest` and `ready_pr.py`'s
    targeted runs are unaffected. Bucketing is by test file (`shard_assignment`) for
    all but a short, explicit list of files (`_SPLIT_BY_TEST`) confirmed not to share
    expensive fixtures across their own tests; every other file's fixtures still build
    at most once per shard.
    """
    count_raw = os.environ.get(_SHARD_COUNT_ENV)
    index_raw = os.environ.get(_SHARD_INDEX_ENV)
    if not count_raw or not index_raw:
        return
    shard_count = int(count_raw)
    shard_index = int(index_raw)
    if shard_count <= 1:
        return
    if not 0 <= shard_index < shard_count:
        raise ValueError(
            f"{_SHARD_INDEX_ENV}={shard_index} out of range for {_SHARD_COUNT_ENV}={shard_count}"
        )
    rootdir = config.rootpath
    units = {_shard_unit(item, rootdir) for item in items}
    assignment = shard_assignment(units, shard_count)
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        unit = _shard_unit(item, rootdir)
        (kept if assignment[unit] == shard_index else deselected).append(item)
    items[:] = kept
    if deselected:
        config.hook.pytest_deselected(items=deselected)


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


_DUCKDB_CONNECT = duckdb.connect


def _single_threaded_connect(
    database: str | Path = ":memory:",
    read_only: bool = False,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> duckdb.DuckDBPyConnection:
    """`duckdb.connect` with `threads=1` unless the caller set `threads`."""
    return _DUCKDB_CONNECT(database, read_only, {"threads": 1, **(config or {})}, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _single_threaded_duckdb() -> Iterator[None]:
    """Open every DuckDB database in the test process with `threads=1` (#953).

    With the default (`threads` = CPU count), each database instance starts
    `threads - 1` native worker threads and joins them when its last
    connection closes. The tests open and close thousands of short-lived
    instances per xdist worker (every `open_for_write`/`open_read_only` is
    one), so that is tens of thousands of native thread exits. On Linux,
    DuckDB 1.5.5 allocates through its bundled jemalloc
    (5.3.0-196-ga25b9b8), whose thread-teardown path (`tsd_add_nominal`) can
    segfault. jemalloc fixed this in 5.4.0 (jemalloc#2981), and DuckDB does
    not ship that fix yet. On CI that killed a random xdist worker inside
    `conn.close()`, with no Python thread marked current in the dump. With
    `threads=1`, DuckDB starts no worker threads, so that teardown path
    never runs. Query results are unchanged; only DuckDB's intra-query
    parallelism goes. Production connections are untouched.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(duckdb, "connect", _single_threaded_connect)
        yield


@pytest.fixture(autouse=True)
def _reset_edgar_rate_limit_block() -> None:
    """Start every test with `edgar_raw`'s process-wide block cleared (#761)."""
    edgar_raw._RATE_LIMIT_BLOCK.clear()


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

    Then creates `store_markers` and writes the `fixture` marker row
    (strategy-lab spec, Definitions, Fixture marker; plan T101), the store's
    proof that it is a fixture store and not a copy of the real one. A missing
    `fixtures_dir` writes nothing, marker included.
    """
    if not fixtures_dir.is_dir():
        return
    lab_schema.create_store_markers(conn)
    lab_schema.write_fixture_marker(conn, "tests/conftest.py:load_universe_fixtures")
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
def lab_store(
    fixture_store: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyConnection:
    """`fixture_store` with the strategy-lab tables applied
    (`lab_schema.apply_lab_schema`; plan T101, choice 2): the store every lab
    task builds and tests on until the lab migration (T113) lands them in
    `init_schema`. Carries the fixture marker like `fixture_store`."""
    lab_schema.apply_lab_schema(fixture_store)
    return fixture_store


def mark_pre_lab(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> None:
    """Mark `hypothesis_id` pre-lab on a fixture store (spec, Definitions,
    Frozen-key defaults: "a fixture store ... gets its pre-lab twins from a test
    helper that inserts the marker row"). The only writer of
    `pre_lab_hypotheses` outside the lab migration (T113). Needs the lab tables
    (`lab_store`)."""
    lab_schema.require_lab(conn)
    conn.execute(
        "INSERT INTO pre_lab_hypotheses (hypothesis_id, marked_at) VALUES (?, ?)",
        [hypothesis_id, utc_now()],
    )


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


def version_4_store(path: Path) -> Path:
    """Write a store file at `path` shaped as `init_schema` left it at schema
    version 4 (fact and registry tables, no journal; Phase 4 plan T49) with
    the fixture universe loaded and one `schema_version` row, and return
    `path`. Built from the current fact and registry DDL, which version 5 left
    unchanged (pinned by hash in `tests/store/test_journal_schema.py`), and
    never through `init_schema`, which would migrate it. For journal code
    that must refuse, and fact or registry reads that must still work, on a
    store no write has touched since T49.

    Also includes `schema._STATEMENT_FACTS_TABLE_DDL` (version 9, #660):
    anachronistic for a true version-4 store, but `load_universe_fixtures`
    below loads every fixture CSV generically, `statement_facts.csv`
    included, so the table must exist for that call to succeed — the same
    simplification this function already makes for every other fact
    table's shape. `schema._FILING_EVENTS_TABLE_DDL` (version 21, #1358) is
    there for the same reason (`filing_events.csv`)."""
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        for ddl in (
            schema._TABLE_DDL
            + schema._REGISTRY_TABLE_DDL
            + schema._STATEMENT_FACTS_TABLE_DDL
            + schema._FILING_EVENTS_TABLE_DDL
        ):
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (4, TIMESTAMPTZ "
            "'2026-09-26 12:00:00+00')"
        )
        load_universe_fixtures(conn, _FIXTURES_UNIVERSE_DIR)
    finally:
        conn.close()
    return path

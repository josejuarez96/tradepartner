"""Strategy-lab DDL as functions, before any schema version takes it (strategy-lab
plan T101, choice 2; spec "Data / interfaces" > Tables).

The lab's tables arrive on the owner's store only with the lab migration (T113,
schema version L), which calls `apply_lab_schema` from `init_schema`. Until then
nothing in `schema.py` calls this module: no version bump, no edit there. Every
lab task builds and tests on the `lab_store` test fixture (a fixture store with
`apply_lab_schema` applied), and every lab read and write calls `require_lab`
first, so a store without the lab tables (the owner's store before T113, a plain
fixture store) raises `LabNotInitialised` and keeps the Phase 3 rules.

- `apply_lab_schema(conn)` creates the seven lab registry tables
  (`family_rules`, `sweeps`, `sweep_variants`, `hypothesis_fingerprints`,
  `pre_lab_hypotheses`, `sweep_runs`, `sweep_trials`) and `store_markers`, and
  widens two `CHECK` enumerations by the staging rebuild of schema version 2
  (`corporate_actions`: create the new table, copy every row in insertion
  order, drop, rename): `trial_results.status` gains `refused_variant` and
  `owner_decisions.kind` gains `promotion` and `sweep_retired`. The rebuild
  starts from the live table's own DDL (`duckdb_tables().sql`), so every column
  a later version added by `ALTER TABLE` (`trial_results.n_research`, version
  12; T97's `sharpe_unit`) is kept with its rows, and only the enumeration's
  text changes. Idempotent: a table that exists is left alone and an
  enumeration that already allows the new values is not rebuilt. One
  transaction (the caller's if open).
- `is_lab_initialised(conn)` is true when every `LAB_TABLE_NAMES` table exists,
  **by table presence, never by a `schema_version` row**; `require_lab(conn)`
  raises `LabNotInitialised` when it is false.
- `store_markers` is the one lab table a plain fixture store has:
  `tests/conftest.py`'s `load_universe_fixtures` calls `create_store_markers`
  and `write_fixture_marker`, and the real store never gets the `fixture` row
  (spec, Definitions, Fixture marker). `has_fixture_marker(conn)` is false when
  the table or the row is absent; it is read by the `store_path` refusals of
  `run_hypothesis` and `run_sweep` (T110, T107).

The lab tables are registry tables (no `known_at`, like the Phase 3 registry
tables), append-only by contract, with no foreign keys and no sequences (ids
are `MAX + 1` inside the write transaction, as for the trial registry). They
stay out of `schema.TABLE_NAMES`, so the look-ahead harness never sees them;
the lab migration adds them to `REGISTRY_TABLE_NAMES` (spec), not this module.
Columns that a sweep run knows only at its end (`sweep_runs.finished_at` and
the counts and statistics after it) are nullable, so the lab registry (T103)
may open the row before the run and close it after.
"""

from __future__ import annotations

import re

import duckdb

from tradepartner.store.db import forget_column_types, utc_now
from tradepartner.store.schema import SchemaVersionError, atomic


class LabNotInitialised(RuntimeError):
    """The store has no strategy-lab tables (`is_lab_initialised` is false): a
    store before the lab migration (T113) or a plain fixture store. Raised by
    `require_lab`, which every lab read and write calls first, never by
    `init_schema`, so every Phase 3 read and write on such a store keeps
    working and keeps the Phase 3 rules (strategy-lab plan, choice 2). The
    sibling of `schema.RegistryNotInitialised` and
    `schema.ResearchNotInitialised`."""


#: `sweeps.selection_statistic` (spec, Definitions, Selection statistic);
#: equal to `backtest.sweep.SelectionStatistic`'s values (pinned by a test).
SELECTION_STATISTICS: tuple[str, ...] = (
    "dsr_excess",
    "sharpe_annual_excess_spy",
    "excess_cagr_spy",
)

#: `store_markers.kind` (spec, Definitions, Fixture marker: `fixture` only).
STORE_MARKER_KINDS: tuple[str, ...] = ("fixture",)

#: The one marker kind the fixture loader writes.
FIXTURE_MARKER_KIND = "fixture"

#: `trial_results.status` values the lab adds (spec, Data / interfaces).
LAB_TRIAL_STATUSES: tuple[str, ...] = ("refused_variant",)

#: `owner_decisions.kind` values the lab adds (spec, Data / interfaces).
LAB_DECISION_KINDS: tuple[str, ...] = ("promotion", "sweep_retired")


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"CHECK ({column} IN ({allowed}))"


# One row per family, written at its first registration (the migration writes
# one per existing family). `parent_family` NULL for a root family;
# `sr_star_seed_annual` NULL for a root family and for every migrated family.
_CREATE_FAMILY_RULES = """
CREATE TABLE IF NOT EXISTS family_rules (
    family VARCHAR NOT NULL PRIMARY KEY,
    first_hypothesis_id BIGINT NOT NULL,
    parent_family VARCHAR,
    holdout_start DATE NOT NULL,
    holdout_end DATE NOT NULL,
    in_sample_start DATE NOT NULL,
    fixed_params_json VARCHAR NOT NULL,
    fixed_params_sha256 VARCHAR NOT NULL,
    max_family_holdout_spends INTEGER NOT NULL,
    max_family_promotions INTEGER NOT NULL,
    min_sharpe_variance_annual DOUBLE NOT NULL,
    axis_lattice_json VARCHAR NOT NULL,
    sr_star_seed_annual DOUBLE,
    registered_at TIMESTAMPTZ NOT NULL
)
"""

# One row per sweep registration; a changed file is a new row.
_CREATE_SWEEPS = f"""
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id BIGINT NOT NULL PRIMARY KEY,
    slug VARCHAR NOT NULL,
    family VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    doc_path VARCHAR NOT NULL,
    doc_sha256 VARCHAR NOT NULL,
    grid_json VARCHAR NOT NULL,
    grid_sha256 VARCHAR NOT NULL,
    n_variants INTEGER NOT NULL,
    selection_statistic VARCHAR NOT NULL,
    expected_excess_cagr_spy_pp DOUBLE NOT NULL,
    expected_range_lo_pp DOUBLE NOT NULL,
    expected_range_hi_pp DOUBLE NOT NULL,
    promote_at_least DOUBLE NOT NULL,
    retire_below DOUBLE NOT NULL,
    max_promotions INTEGER NOT NULL,
    min_dsr_floor DOUBLE NOT NULL,
    max_failures_per_variant INTEGER NOT NULL,
    axis_lattice_json VARCHAR NOT NULL,
    in_sample_start DATE NOT NULL,
    holdout_start DATE NOT NULL,
    holdout_end DATE NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    registered_by VARCHAR NOT NULL,
    CHECK (n_variants >= 1),
    {_enum_check("selection_statistic", SELECTION_STATISTICS)}
)
"""

# `variant_index` is the 1-based canonical index; `variant_params_json` holds
# the varied keys and values only.
_CREATE_SWEEP_VARIANTS = """
CREATE TABLE IF NOT EXISTS sweep_variants (
    sweep_id BIGINT NOT NULL,
    variant_index INTEGER NOT NULL,
    hypothesis_id BIGINT NOT NULL,
    fingerprint VARCHAR NOT NULL,
    variant_params_json VARCHAR NOT NULL,
    PRIMARY KEY (sweep_id, variant_index),
    CHECK (variant_index >= 1)
)
"""

# One row per registration, standalone or variant. No UNIQUE on `fingerprint`:
# a promotion registers a second hypothesis with its variant's fingerprint
# (spec, Definitions, Fingerprint; req 4).
_CREATE_HYPOTHESIS_FINGERPRINTS = """
CREATE TABLE IF NOT EXISTS hypothesis_fingerprints (
    hypothesis_id BIGINT NOT NULL PRIMARY KEY,
    fingerprint VARCHAR NOT NULL
)
"""

# Written by the lab migration (T113) for every `hypotheses` row then present,
# and on a fixture store only by the conftest's `mark_pre_lab` helper.
_CREATE_PRE_LAB_HYPOTHESES = """
CREATE TABLE IF NOT EXISTS pre_lab_hypotheses (
    hypothesis_id BIGINT NOT NULL PRIMARY KEY,
    marked_at TIMESTAMPTZ NOT NULL
)
"""

# `finished_at` and every column after `n_planned` but the code vintage,
# `run_by` and `note` are known at the run's end and NULL until then;
# `code_dirty` is NULL when `code_version` is 'unknown', as on `trials`.
_CREATE_SWEEP_RUNS = """
CREATE TABLE IF NOT EXISTS sweep_runs (
    sweep_run_id BIGINT NOT NULL PRIMARY KEY,
    sweep_id BIGINT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    time_budget_minutes INTEGER NOT NULL,
    n_declared INTEGER NOT NULL,
    n_planned INTEGER NOT NULL,
    n_ok INTEGER,
    n_failed INTEGER,
    n_terminal_failed INTEGER,
    seconds DOUBLE,
    code_tree_sha256 VARCHAR NOT NULL,
    code_version VARCHAR NOT NULL,
    code_dirty BOOLEAN,
    n_trials_at_end INTEGER,
    sr_star_annual_at_end DOUBLE,
    completed BOOLEAN,
    run_by VARCHAR NOT NULL,
    note VARCHAR
)
"""

# A trial is opened by exactly one sweep run.
_CREATE_SWEEP_TRIALS = """
CREATE TABLE IF NOT EXISTS sweep_trials (
    sweep_run_id BIGINT NOT NULL,
    trial_id BIGINT NOT NULL PRIMARY KEY,
    read_group_index INTEGER NOT NULL,
    seconds DOUBLE NOT NULL
)
"""

# One row per kind: the fixture marker is written once per store.
_CREATE_STORE_MARKERS = f"""
CREATE TABLE IF NOT EXISTS store_markers (
    kind VARCHAR NOT NULL PRIMARY KEY,
    written_at TIMESTAMPTZ NOT NULL,
    written_by VARCHAR NOT NULL,
    {_enum_check("kind", STORE_MARKER_KINDS)}
)
"""

#: Every table `apply_lab_schema` creates; `is_lab_initialised` is true when
#: all of them exist. Disjoint from `schema.TABLE_NAMES`,
#: `REGISTRY_TABLE_NAMES`, the journal tables and `RESEARCH_TABLE_NAMES`.
LAB_TABLE_NAMES: tuple[str, ...] = (
    "family_rules",
    "sweeps",
    "sweep_variants",
    "hypothesis_fingerprints",
    "pre_lab_hypotheses",
    "sweep_runs",
    "sweep_trials",
    "store_markers",
)

_LAB_TABLE_DDL: tuple[str, ...] = (
    _CREATE_FAMILY_RULES,
    _CREATE_SWEEPS,
    _CREATE_SWEEP_VARIANTS,
    _CREATE_HYPOTHESIS_FINGERPRINTS,
    _CREATE_PRE_LAB_HYPOTHESES,
    _CREATE_SWEEP_RUNS,
    _CREATE_SWEEP_TRIALS,
    _CREATE_STORE_MARKERS,
)

#: The two registry enumerations the lab widens: (table, column) -> the values
#: it adds. Rebuilt by `_widen_enum`, never altered in place (DuckDB cannot).
_WIDENED_ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("trial_results", "status"): LAB_TRIAL_STATUSES,
    ("owner_decisions", "kind"): LAB_DECISION_KINDS,
}


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    (count,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = ?",
        [table],
    ).fetchone()
    return bool(count)


def create_store_markers(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the `store_markers` table if it does not exist (idempotent). The
    fixture loader calls it on every fixture store; `apply_lab_schema` creates
    it with the other lab tables."""
    conn.execute(_CREATE_STORE_MARKERS)
    forget_column_types(conn)


def write_fixture_marker(conn: duckdb.DuckDBPyConnection, written_by: str) -> None:
    """Write the `fixture` row into `store_markers` (spec, Definitions, Fixture
    marker), unless the store already has one. The only writer of
    `store_markers`; called only by `tests/conftest.py`'s
    `load_universe_fixtures`, never on the real store. The table must exist
    (`create_store_markers`)."""
    conn.execute(
        "INSERT INTO store_markers (kind, written_at, written_by) VALUES (?, ?, ?) "
        "ON CONFLICT (kind) DO NOTHING",
        [FIXTURE_MARKER_KIND, utc_now(), written_by],
    )


def has_fixture_marker(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether the store carries the `fixture` marker row: false when
    `store_markers` or the row is absent (the real store, a copy of it)."""
    if not _table_exists(conn, "store_markers"):
        return False
    (count,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM store_markers WHERE kind = ?", [FIXTURE_MARKER_KIND]
    ).fetchone()
    return bool(count)


def is_lab_initialised(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether every `LAB_TABLE_NAMES` table exists. Read by table presence,
    never by a `schema_version` row: true on the `lab_store` fixture, false on
    a plain fixture store (which has only `store_markers`) and on the owner's
    store until the lab migration (T113)."""
    (present,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name IN "
        f"({', '.join('?' for _ in LAB_TABLE_NAMES)})",
        list(LAB_TABLE_NAMES),
    ).fetchone()
    return bool(present == len(LAB_TABLE_NAMES))


def require_lab(conn: duckdb.DuckDBPyConnection) -> None:
    """Raise `LabNotInitialised` unless `is_lab_initialised(conn)`. Every lab
    read and write (T103 onwards) calls it before its first query."""
    if not is_lab_initialised(conn):
        raise LabNotInitialised(
            "strategy lab not initialised (a lab table is missing); the lab "
            "migration creates the tables, and until then this store keeps the "
            "Phase 3 rules"
        )


_IN_LIST = re.compile(r"^\((?P<column>\w+) IN \((?P<values>'[^']*'(?:, '[^']*')*)\)\)$")


def _current_enum(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> tuple[str, str]:
    """The `CHECK` on `table.column` as DuckDB prints it in the table's DDL, and
    its quoted value list (`'a', 'b'`). Raises `SchemaVersionError` unless there is exactly one
    `CHECK` on the table and it is a plain `column IN (...)` list: any other
    shape is a store this module does not know and will not rebuild."""
    rows = conn.execute(
        "SELECT constraint_text, expression FROM duckdb_constraints() "
        "WHERE database_name = current_database() AND schema_name = current_schema() "
        "AND table_name = ? AND constraint_type = 'CHECK'",
        [table],
    ).fetchall()
    if len(rows) != 1:
        raise SchemaVersionError(
            f"{table} has {len(rows)} CHECK constraints, expected one on {column}; "
            "the lab schema does not rebuild a table it does not know"
        )
    constraint_text, expression = rows[0]
    match = _IN_LIST.fullmatch(expression)
    if match is None or match["column"] != column:
        raise SchemaVersionError(
            f"{table}'s CHECK is {expression!r}, expected a {column} IN (...) list; "
            "the lab schema does not rebuild a table it does not know"
        )
    return str(constraint_text), match["values"]


def _widen_enum(
    conn: duckdb.DuckDBPyConnection, table: str, column: str, added: tuple[str, ...]
) -> None:
    """Rebuild `table` with `added` appended to its `column IN (...)` `CHECK`,
    by the staging pattern of schema version 2, every row copied in insertion
    order and every column kept as the live DDL has it. A no-op when every
    value in `added` is already allowed. Runs inside the caller's transaction."""
    constraint_text, values = _current_enum(conn, table, column)
    current = tuple(re.findall(r"'([^']*)'", values))
    missing = tuple(value for value in added if value not in current)
    if not missing:
        return
    (live_ddl,) = conn.execute(  # type: ignore[misc]
        "SELECT sql FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = ?",
        [table],
    ).fetchone()
    staging = f"{table}_lab"
    head = f"CREATE TABLE {table}("
    if not live_ddl.startswith(head) or live_ddl.count(constraint_text) != 1:
        raise SchemaVersionError(
            f"{table}'s DDL is not in the shape the lab schema rebuilds: {live_ddl!r}"
        )
    appended = ", ".join(f"'{value}'" for value in missing)
    widened = f"CHECK(({column} IN ({values}, {appended})))"
    staging_ddl = f"CREATE TABLE {staging}(" + live_ddl[len(head) :].replace(
        constraint_text, widened, 1
    )
    conn.execute(staging_ddl)
    conn.execute(f"INSERT INTO {staging} BY NAME SELECT * FROM {table} ORDER BY rowid")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")


def apply_lab_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every `LAB_TABLE_NAMES` table and widen `trial_results.status`
    (`refused_variant`) and `owner_decisions.kind` (`promotion`,
    `sweep_retired`) by the staging rebuild, every row kept byte-identical
    (module docstring). Needs a store `init_schema` has created (the registry
    tables must exist). Idempotent: a second call changes nothing. One
    transaction (the caller's if open). Writes no `schema_version` row and is
    called by nothing in `schema.py` until the lab migration (T113)."""
    with atomic(conn):
        for ddl in _LAB_TABLE_DDL:
            conn.execute(ddl)
        for (table, column), added in _WIDENED_ENUMS.items():
            _widen_enum(conn, table, column, added)
    forget_column_types(conn)

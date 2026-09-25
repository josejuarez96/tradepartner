"""DuckDB DDL for the point-in-time store (spec "Data / interfaces" > Tables).

Every **fact table** carries the four common columns from ADR 0003 rule 1
and the spec's "Definitions": `known_at`, `ingested_at`, `source`,
`provenance`, plus two `CHECK` constraints (`known_at <= ingested_at`, and
`provenance` restricted to a set of allowed values). That allowed set is
**per table**, not the same five values everywhere: `prices_daily` only
ever gets `bar` rows; `corporate_actions` only `action`; `facts` and
`delistings` only `filing` (spec "Data / interfaces" > Master column
sources: shares outstanding and delistings both name a filing as the only
source); `securities`, `listings` and `classifications` allow `filing`,
`snapshot` and `snapshot_static` (the master table sources different
columns of these three from filings vs. snapshots). `ingestion_runs` is
not a fact table — it records ingest job status, not a point-in-time fact
— so it has none of the four common columns and no provenance concept.

DuckDB accepts a naive (tz-unaware) `datetime` as a `TIMESTAMPTZ`
parameter without raising, interpreting it in the connection's configured
session `TimeZone` (UTC here only because `tradepartner.store.db
.configure_connection` pins it) rather than rejecting it — so the
`CHECK`/`NOT NULL` constraints here cannot themselves enforce
tz-awareness. `tradepartner.store.db.insert_row` validates that in Python,
by column type, before any row reaches these tables.

`init_schema(conn)` is idempotent: every statement is `CREATE ... IF NOT
EXISTS`, and each `schema_version` bookkeeping row is inserted only once.
If a store records a version this code has no path from (anything other
than 1 or `CURRENT_SCHEMA_VERSION`), `init_schema` raises
`SchemaVersionError` rather than silently operating against a shape it
does not know about.

Schema versions (Phase 3 spec req 9):

- **Version 1**: the fact tables, `ingestion_runs` and `schema_version`
  (`TABLE_NAMES`), exactly as Phase 2 shipped them.
- **Version 2**: adds the eight trial-registry tables
  (`REGISTRY_TABLE_NAMES`). The migration from version 1 is additive:
  it creates the registry tables and appends a version-2 row to
  `schema_version`; no fact table, `ingestion_runs` row or existing
  `schema_version` row changes. It runs on any writable connection that
  calls `init_schema`. A read-only connection never migrates: on a
  version-1 (or empty) store it raises `RegistryNotInitialised`, which the
  read-only dashboard pages turn into a "registry not initialised" state.
- **A later fact-table DDL change goes to version 3**, with its own
  migration and a note here, never a silent edit of the version-1 or
  version-2 DDL below.

Registry tables are not fact tables: like `ingestion_runs` they carry no
`known_at`/`ingested_at`/`source`/`provenance` columns, and they are kept
out of `TABLE_NAMES` so the look-ahead harness (which truncates every
`TABLE_NAMES` fact table by `known_at`) is untouched. They are
append-only by contract (`store.registry` exposes inserts and reads
only); the only schema-level backstops are the primary keys (one
`trial_results` row per trial) and `CHECK`s on the spec's enumerated
columns (`trials.kind`, `trial_results.status`, `owner_decisions.kind`).
No foreign keys are declared, as for the fact tables; ids are assigned by
`store.registry`, not by a sequence.

Design decisions (not pinned by the spec text, recorded here because
they shape this DDL):

- **`security_id` is one per listed share class**, not one per company: a
  dual-class company (spec req 13's dual-class fixture case) has two
  `securities` rows sharing one `cik`. The security master (T8) creates
  the *first* `security_id` for a CIK from the earliest issuer filing
  (spec req 3, "earliest-filing rule"); further classes of the same
  issuer get their own `security_id` only once their own listing is
  discovered. Nothing in this module enforces "at least one row per CIK"
  or "classes share a CIK" — that invariant belongs to the master's
  write path, not the schema.
- **`securities.name` is sourced from the EDGAR filing index's company
  name at filing time** (`provenance = 'filing'`), not from the companies
  snapshot endpoint — unlike the master column-sources table's general
  "name: companies snapshot, snapshot_static" note, which still applies
  to other uses of a snapshot company name (e.g. a `listings`-level
  display name). `name` stays `NOT NULL`: every `securities` row is
  created *from* a filing, so a name is always available at insert time.
"""

from __future__ import annotations

import duckdb

from tradepartner.store.db import configure_connection, forget_column_types, utc_now

#: Every provenance value used anywhere in the store (spec "Definitions").
#: No single table allows all of these — see `TABLE_PROVENANCE_VALUES`.
PROVENANCE_VALUES: tuple[str, ...] = (
    "filing",
    "bar",
    "action",
    "snapshot",
    "snapshot_static",
)

#: The provenance values each fact table's CHECK constraint allows, per
#: the "Data / interfaces" master column-sources table. Exposed (not just
#: baked into the DDL strings) so tests can probe "this table's rows may
#: never carry a provenance from outside its own set" without duplicating
#: the mapping.
TABLE_PROVENANCE_VALUES: dict[str, tuple[str, ...]] = {
    "securities": ("filing", "snapshot", "snapshot_static"),
    "listings": ("filing", "snapshot", "snapshot_static"),
    "classifications": ("filing", "snapshot", "snapshot_static"),
    "delistings": ("filing",),
    "prices_daily": ("bar",),
    "corporate_actions": ("action",),
    "facts": ("filing",),
}

#: The schema version `init_schema` records on a fresh store and migrates
#: a version-1 store to. Bump and add a migration note to the module
#: docstring (not silent DDL edits) if the shape of a table changes after
#: data has been loaded: a fact-table change is version 3.
CURRENT_SCHEMA_VERSION = 2

#: The Phase 2 schema version, the only one `init_schema` migrates from.
_VERSION_1 = 1


class SchemaVersionError(RuntimeError):
    """The store's `schema_version` table records a version this code has
    no migration from — it does not know that shape and refuses to
    operate on it rather than guessing."""


class RegistryNotInitialised(RuntimeError):
    """A read-only connection opened a store without the version-2 trial
    registry (a version-1 store, or one never initialised). Read-only
    connections never migrate; any writing command's `init_schema` call
    does."""


def _common_fact_columns(provenance_values: tuple[str, ...]) -> str:
    """The four columns and two `CHECK` constraints common to every fact
    table (ADR 0003 rule 1; spec "Definitions"), with `provenance`
    restricted to `provenance_values` for this particular table."""
    provenance_list = ", ".join(f"'{value}'" for value in provenance_values)
    return f"""
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    source VARCHAR NOT NULL,
    provenance VARCHAR NOT NULL,
    CHECK (provenance IN ({provenance_list})),
    CHECK (known_at <= ingested_at)
"""


_CREATE_SECURITIES = f"""
CREATE TABLE IF NOT EXISTS securities (
    security_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    benchmark BOOLEAN NOT NULL DEFAULT FALSE,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["securities"])},
    UNIQUE (security_id, known_at)
)
"""

_CREATE_LISTINGS = f"""
CREATE TABLE IF NOT EXISTS listings (
    security_id VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    class_title VARCHAR,
    valid_from DATE NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["listings"])},
    UNIQUE (security_id, ticker, exchange, valid_from, known_at)
)
"""

_CREATE_CLASSIFICATIONS = f"""
CREATE TABLE IF NOT EXISTS classifications (
    security_id VARCHAR NOT NULL,
    sic INTEGER,
    security_type VARCHAR NOT NULL,
    rule VARCHAR NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["classifications"])},
    UNIQUE (security_id, rule, known_at)
)
"""

# filed_at is TIMESTAMPTZ (the filing's acceptance instant), so
# CAST(filed_at AS DATE) yields the UTC calendar date, which is not
# necessarily the exchange (America/New_York) trading session containing
# that instant. Deriving "the last session before this filing" (spec req
# 4) must go through the XNYS calendar (T8b's `delistings.py`), never a
# bare date cast of `filed_at`.
_CREATE_DELISTINGS = f"""
CREATE TABLE IF NOT EXISTS delistings (
    security_id VARCHAR NOT NULL,
    form VARCHAR NOT NULL,
    class_title VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    filed_at TIMESTAMPTZ NOT NULL,
    effective_on DATE NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["delistings"])},
    UNIQUE (security_id, form, class_title, exchange, filed_at, known_at)
)
"""

_CREATE_PRICES_DAILY = f"""
CREATE TABLE IF NOT EXISTS prices_daily (
    security_id VARCHAR NOT NULL,
    session DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["prices_daily"])},
    UNIQUE (security_id, session, known_at)
)
"""

_CREATE_CORPORATE_ACTIONS = f"""
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["corporate_actions"])},
    UNIQUE (security_id, action_type, ex_date, known_at)
)
"""

# class_member defaults to '' (not NULL) for an undimensioned fact:
# DuckDB's UNIQUE constraint treats NULL as distinct from every other
# NULL (confirmed by hand), so a nullable class_member would let the same
# undimensioned fact be inserted twice without tripping the UNIQUE
# constraint below. '' is not a legal XBRL class-member value, so it
# cannot collide with a real dimension.
_CREATE_FACTS = f"""
CREATE TABLE IF NOT EXISTS facts (
    security_id VARCHAR NOT NULL,
    fact_name VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    class_member VARCHAR NOT NULL DEFAULT '',
    value DOUBLE NOT NULL,
    filing_accession VARCHAR,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["facts"])},
    UNIQUE (security_id, fact_name, as_of_date, class_member, known_at)
)
"""

# Not a fact table (spec "Data / interfaces" > Tables): no known_at,
# ingested_at, source or provenance columns, and no per-fact provenance
# concept applies to an ingest job's own status row.
_CREATE_INGESTION_RUNS = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id VARCHAR NOT NULL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    rows_added INTEGER,
    chunk_cursor VARCHAR,
    message VARCHAR
)
"""

_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
)
"""

#: Every table this module owns, in dependency-free creation order (no
#: foreign keys are declared, so order only needs schema_version last for
#: readability). Used by `init_schema` and exposed for the test loader.
TABLE_NAMES: tuple[str, ...] = (
    "securities",
    "listings",
    "classifications",
    "delistings",
    "prices_daily",
    "corporate_actions",
    "facts",
    "ingestion_runs",
    "schema_version",
)

_TABLE_DDL: tuple[str, ...] = (
    _CREATE_SECURITIES,
    _CREATE_LISTINGS,
    _CREATE_CLASSIFICATIONS,
    _CREATE_DELISTINGS,
    _CREATE_PRICES_DAILY,
    _CREATE_CORPORATE_ACTIONS,
    _CREATE_FACTS,
    _CREATE_INGESTION_RUNS,
    _CREATE_SCHEMA_VERSION,
)


# Trial registry (schema version 2; Phase 3 spec "Data / interfaces" >
# Tables). Not fact tables: no known_at, like ingestion_runs. Every JSON
# payload is VARCHAR because extension auto-load is disabled
# (`configure_connection`), so the json extension is never available.
_CREATE_HYPOTHESES = """
CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id BIGINT NOT NULL PRIMARY KEY,
    slug VARCHAR NOT NULL,
    family VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    doc_path VARCHAR NOT NULL,
    doc_sha256 VARCHAR NOT NULL,
    params_json VARCHAR NOT NULL,
    params_sha256 VARCHAR NOT NULL,
    in_sample_start DATE NOT NULL,
    holdout_start DATE NOT NULL,
    holdout_end DATE NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    registered_by VARCHAR NOT NULL
)
"""

# No CHECK on the window: a refused window (start after end, or outside
# the in-sample range) is still a trial and must be recorded. code_dirty
# is NULL when code_version is 'unknown' (outside a git checkout);
# store_max_ingested_at is NULL on a store with no fact rows.
_CREATE_TRIALS = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id BIGINT NOT NULL PRIMARY KEY,
    hypothesis_id BIGINT NOT NULL,
    kind VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    start_session DATE NOT NULL,
    end_session DATE NOT NULL,
    data_cutoff TIMESTAMPTZ NOT NULL,
    store_max_ingested_at TIMESTAMPTZ,
    code_version VARCHAR NOT NULL,
    code_dirty BOOLEAN,
    synthetic BOOLEAN NOT NULL,
    holdout_repeat BOOLEAN NOT NULL DEFAULT FALSE,
    holdout_reason VARCHAR,
    gap_override_reason VARCHAR,
    run_by VARCHAR NOT NULL,
    note VARCHAR,
    CHECK (kind IN ('in_sample', 'holdout', 'tracking'))
)
"""

# One row per trial: a trial's outcome is this row, never an update of
# `trials`. The statistics are NULL on any status other than ok.
_CREATE_TRIAL_RESULTS = """
CREATE TABLE IF NOT EXISTS trial_results (
    trial_id BIGINT NOT NULL PRIMARY KEY,
    finished_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    message VARCHAR,
    n_trials INTEGER,
    sharpe_variance DOUBLE,
    sr_star DOUBLE,
    psr_zero DOUBLE,
    dsr DOUBLE,
    sharpe_variance_excess DOUBLE,
    sr_star_excess DOUBLE,
    psr_zero_excess DOUBLE,
    dsr_excess DOUBLE,
    dsr_basis VARCHAR,
    red_flag BOOLEAN,
    gap_max_count_share DOUBLE,
    gap_max_size_share DOUBLE,
    CHECK (status IN ('ok', 'failed', 'refused_window', 'refused_holdout', 'refused_gap'))
)
"""

# value is NULL where a metric does not apply (`*_excess_spy` for SPY).
_CREATE_TRIAL_METRICS = """
CREATE TABLE IF NOT EXISTS trial_metrics (
    trial_id BIGINT NOT NULL,
    series VARCHAR NOT NULL,
    cost_per_side_bps DOUBLE NOT NULL,
    metric VARCHAR NOT NULL,
    value DOUBLE,
    UNIQUE (trial_id, series, cost_per_side_bps, metric)
)
"""

_CREATE_TRIAL_REBALANCES = """
CREATE TABLE IF NOT EXISTS trial_rebalances (
    trial_id BIGINT NOT NULL,
    cost_per_side_bps DOUBLE NOT NULL,
    session DATE NOT NULL,
    fill_session DATE NOT NULL,
    n_universe INTEGER NOT NULL,
    n_static_listings INTEGER NOT NULL,
    n_targets INTEGER NOT NULL,
    turnover DOUBLE NOT NULL,
    cost_paid DOUBLE NOT NULL,
    gap_count_share DOUBLE,
    gap_size_share DOUBLE,
    n_missing_fill INTEGER NOT NULL,
    n_delisting_exits INTEGER NOT NULL,
    n_stale_exits INTEGER NOT NULL,
    n_excluded_no_history INTEGER NOT NULL,
    n_dropped_dividends INTEGER NOT NULL,
    n_late_dividends INTEGER NOT NULL,
    UNIQUE (trial_id, cost_per_side_bps, session)
)
"""

# cash is NULL for a benchmark series, which holds no cash account.
_CREATE_TRIAL_EQUITY = """
CREATE TABLE IF NOT EXISTS trial_equity (
    trial_id BIGINT NOT NULL,
    series VARCHAR NOT NULL,
    cost_per_side_bps DOUBLE NOT NULL,
    session DATE NOT NULL,
    equity DOUBLE NOT NULL,
    cash DOUBLE,
    UNIQUE (trial_id, series, cost_per_side_bps, session)
)
"""

# Base cost level only (targets are identical across levels). fill_price
# and shares are NULL for a missing fill.
_CREATE_TRIAL_WEIGHTS = """
CREATE TABLE IF NOT EXISTS trial_weights (
    trial_id BIGINT NOT NULL,
    fill_session DATE NOT NULL,
    security_id VARCHAR NOT NULL,
    target_weight DOUBLE NOT NULL,
    fill_price DOUBLE,
    shares DOUBLE,
    UNIQUE (trial_id, fill_session, security_id)
)
"""

_CREATE_OWNER_DECISIONS = """
CREATE TABLE IF NOT EXISTS owner_decisions (
    decision_id BIGINT NOT NULL PRIMARY KEY,
    made_at TIMESTAMPTZ NOT NULL,
    kind VARCHAR NOT NULL,
    hypothesis_id BIGINT,
    trial_id BIGINT,
    values_json VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    CHECK (kind IN ('gap_signoff', 'gap_override', 'holdout_spend'))
)
"""

#: The trial-registry tables added at schema version 2, disjoint from
#: `TABLE_NAMES` so the look-ahead harness never sees them.
REGISTRY_TABLE_NAMES: tuple[str, ...] = (
    "hypotheses",
    "trials",
    "trial_results",
    "trial_metrics",
    "trial_rebalances",
    "trial_equity",
    "trial_weights",
    "owner_decisions",
)

_REGISTRY_TABLE_DDL: tuple[str, ...] = (
    _CREATE_HYPOTHESES,
    _CREATE_TRIALS,
    _CREATE_TRIAL_RESULTS,
    _CREATE_TRIAL_METRICS,
    _CREATE_TRIAL_REBALANCES,
    _CREATE_TRIAL_EQUITY,
    _CREATE_TRIAL_WEIGHTS,
    _CREATE_OWNER_DECISIONS,
)


def _is_read_only(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether `conn`'s current database is attached read-only."""
    result = conn.execute(
        "SELECT readonly FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    return bool(result[0]) if result is not None else False


def _max_version(conn: duckdb.DuckDBPyConnection) -> int | None:
    """The highest recorded schema version, or None if there is none (no
    `schema_version` table, or an empty one)."""
    exists = conn.execute(
        "SELECT COUNT(*) FROM duckdb_tables() "
        "WHERE database_name = current_database() AND schema_name = current_schema() "
        "AND table_name = 'schema_version'"
    ).fetchone()
    if exists is None or exists[0] == 0:
        return None
    result = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return result[0] if result is not None else None


def _check_read_only(conn: duckdb.DuckDBPyConnection) -> None:
    """The read-only path of `init_schema`: no DDL, only a version check."""
    max_version = _max_version(conn)
    if max_version == CURRENT_SCHEMA_VERSION:
        return
    if max_version is None or max_version == _VERSION_1:
        found = "no schema" if max_version is None else f"schema version {max_version}"
        raise RegistryNotInitialised(
            f"store has {found}; the trial registry needs version "
            f"{CURRENT_SCHEMA_VERSION}, and a read-only connection does not migrate "
            "(run any writing command to migrate the store)"
        )
    raise SchemaVersionError(
        f"store schema_version is {max_version}, this code expects {CURRENT_SCHEMA_VERSION}"
    )


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every store table if it does not already exist, migrating a
    version-1 store to version 2.

    Idempotent: safe to call on every process start and every test. Also
    pins the connection's session timezone to UTC and disables extension
    auto-install/auto-load (`configure_connection`) so a caller that built
    its own raw connection still gets correct `TIMESTAMPTZ` round-tripping
    and no surprise network access, and drops any cached column-type info
    for `conn` (`store.db.forget_column_types`) so `store.db.insert_row`
    never reuses type info cached before these tables existed.

    On a writable connection: a fresh store gets every table and one
    `schema_version` row for `CURRENT_SCHEMA_VERSION`; a version-1 store
    gets the registry tables and an appended version-2 row, and nothing
    else changes (module docstring, "Schema versions").

    On a read-only connection no DDL runs: a version-2 store passes, and a
    version-1 or uninitialised store raises `RegistryNotInitialised`.

    Raises `SchemaVersionError` if the store records any other version:
    this module has no migration from it, so operating on a store shaped
    for a different version would silently corrupt or misinterpret it.
    """
    configure_connection(conn)
    if _is_read_only(conn):
        _check_read_only(conn)
        forget_column_types(conn)
        return
    max_version = _max_version(conn)
    if max_version not in (None, _VERSION_1, CURRENT_SCHEMA_VERSION):
        raise SchemaVersionError(
            f"store schema_version is {max_version}, this code expects {CURRENT_SCHEMA_VERSION}"
        )
    for ddl in _TABLE_DDL + _REGISTRY_TABLE_DDL:
        conn.execute(ddl)
    forget_column_types(conn)
    if max_version != CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            [CURRENT_SCHEMA_VERSION, utc_now()],
        )

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
EXISTS`, and the `schema_version` bookkeeping row is inserted only once.
If a store already has a `schema_version` row that disagrees with
`CURRENT_SCHEMA_VERSION`, `init_schema` raises `SchemaVersionError` rather
than silently operating against a shape it does not know about.

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

#: The schema version `init_schema` records on first run. Bump and add a
#: migration note here (not silent DDL edits) if the shape of a table
#: changes after data has been loaded.
#:
#: Migration notes:
#: - 2 (#108): `corporate_actions` gains `source_action_id` and `cancelled`,
#:   and its UNIQUE key gains `source_action_id`. No data had been ingested
#:   at v1, so there is no migration code; a v1 store raises
#:   `SchemaVersionError`.
CURRENT_SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    """The store's `schema_version` table records a version other than
    `CURRENT_SCHEMA_VERSION` — this code does not know that shape and
    refuses to operate on it rather than guessing."""


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

# An action's identity (#108) is `(security_id, source_action_id)` when the
# source gives a stable id, else `(security_id, action_type, ex_date)`, so a
# re-dated action (same id, new ex_date) is a revision of one event, not a
# second event. source_action_id uses '' for "the source gave no id", not
# NULL, for the same UNIQUE reason as facts.class_member below. A revision
# with cancelled = TRUE withdraws the event from its known_at on; that also
# retires the old key of an id-less re-date.
_CREATE_CORPORATE_ACTIONS = f"""
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    source_action_id VARCHAR NOT NULL DEFAULT '',
    cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["corporate_actions"])},
    UNIQUE (security_id, action_type, ex_date, source_action_id, known_at)
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


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every store table if it does not already exist.

    Idempotent: safe to call on every process start and every test. Also
    pins the connection's session timezone to UTC and disables extension
    auto-install/auto-load (`configure_connection`) so a caller that built
    its own raw connection still gets correct `TIMESTAMPTZ` round-tripping
    and no surprise network access, and drops any cached column-type info
    for `conn` (`store.db.forget_column_types`) so `store.db.insert_row`
    never reuses type info cached before these tables existed.

    Raises `SchemaVersionError` if the store already has a `schema_version`
    row whose version differs from `CURRENT_SCHEMA_VERSION`: this module
    has no migration logic, so operating on a store shaped for a different
    version would silently corrupt or misinterpret it.
    """
    configure_connection(conn)
    for ddl in _TABLE_DDL:
        conn.execute(ddl)
    forget_column_types(conn)
    result = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    max_version = result[0] if result is not None else None
    if max_version is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            [CURRENT_SCHEMA_VERSION, utc_now()],
        )
    elif max_version != CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"store schema_version is {max_version}, this code expects {CURRENT_SCHEMA_VERSION}"
        )

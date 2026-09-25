"""DuckDB DDL for the point-in-time store (spec "Data / interfaces" > Tables).

Every **fact table** carries the four common columns from ADR 0003 rule 1
and the spec's "Definitions": `known_at`, `ingested_at`, `source`,
`provenance`, plus two `CHECK` constraints (`provenance` restricted to the
allowed set; `known_at <= ingested_at`). `ingestion_runs` is not a fact
table — it records ingest job status, not a point-in-time fact — so it has
none of the four common columns.

DuckDB accepts a naive (tz-unaware) `datetime` as a `TIMESTAMPTZ` parameter
silently (it does not raise), so the `CHECK`/`NOT NULL` constraints here
cannot themselves enforce tz-awareness. `tradepartner.store.db` validates
that in Python before any row reaches these tables.

`init_schema(conn)` is idempotent: every statement is `CREATE ... IF NOT
EXISTS`, and the `schema_version` bookkeeping row is inserted only once.
"""

from __future__ import annotations

import duckdb

from tradepartner.store.db import configure_connection, utc_now

#: Provenance values allowed on every fact-table row (spec "Definitions").
PROVENANCE_VALUES: tuple[str, ...] = (
    "filing",
    "bar",
    "action",
    "snapshot",
    "snapshot_static",
)

#: The schema version `init_schema` records on first run. Bump and add a
#: migration note here (not silent DDL edits) if the shape of a table
#: changes after data has been loaded.
CURRENT_SCHEMA_VERSION = 1

# The four columns and two CHECK constraints common to every fact table
# (ADR 0003 rule 1; spec "Definitions"). Interpolated into each fact
# table's CREATE TABLE below so the constraint text lives in exactly one
# place.
_PROVENANCE_LIST = ", ".join(f"'{value}'" for value in PROVENANCE_VALUES)
_COMMON_FACT_COLUMNS = f"""
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    source VARCHAR NOT NULL,
    provenance VARCHAR NOT NULL,
    CHECK (provenance IN ({_PROVENANCE_LIST})),
    CHECK (known_at <= ingested_at)
"""

_CREATE_SECURITIES = f"""
CREATE TABLE IF NOT EXISTS securities (
    security_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    benchmark BOOLEAN NOT NULL DEFAULT FALSE,
    {_COMMON_FACT_COLUMNS}
)
"""

_CREATE_LISTINGS = f"""
CREATE TABLE IF NOT EXISTS listings (
    security_id VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    class_title VARCHAR,
    valid_from DATE NOT NULL,
    {_COMMON_FACT_COLUMNS},
    UNIQUE (security_id, ticker, exchange, valid_from, known_at)
)
"""

_CREATE_CLASSIFICATIONS = f"""
CREATE TABLE IF NOT EXISTS classifications (
    security_id VARCHAR NOT NULL,
    sic INTEGER,
    security_type VARCHAR NOT NULL,
    rule VARCHAR NOT NULL,
    {_COMMON_FACT_COLUMNS}
)
"""

_CREATE_DELISTINGS = f"""
CREATE TABLE IF NOT EXISTS delistings (
    security_id VARCHAR NOT NULL,
    form VARCHAR NOT NULL,
    class_title VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    filed_at TIMESTAMPTZ NOT NULL,
    effective_on DATE NOT NULL,
    {_COMMON_FACT_COLUMNS}
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
    {_COMMON_FACT_COLUMNS},
    UNIQUE (security_id, session, known_at)
)
"""

_CREATE_CORPORATE_ACTIONS = f"""
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    {_COMMON_FACT_COLUMNS},
    UNIQUE (security_id, action_type, ex_date, known_at)
)
"""

_CREATE_FACTS = f"""
CREATE TABLE IF NOT EXISTS facts (
    security_id VARCHAR NOT NULL,
    fact_name VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    class_member VARCHAR,
    value DOUBLE NOT NULL,
    filing_accession VARCHAR,
    {_COMMON_FACT_COLUMNS},
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
    pins the connection's session timezone to UTC (`configure_connection`)
    so a caller that built its own raw connection still gets correct
    `TIMESTAMPTZ` round-tripping.
    """
    configure_connection(conn)
    for ddl in _TABLE_DDL:
        conn.execute(ddl)
    result = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    row_count = result[0] if result is not None else 0
    if row_count == 0:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            [CURRENT_SCHEMA_VERSION, utc_now()],
        )

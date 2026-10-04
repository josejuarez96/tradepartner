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
than 2, 3, 4, 5, 6, 7 or `CURRENT_SCHEMA_VERSION`), `init_schema` raises
`SchemaVersionError` rather than silently operating against a shape it
does not know about.

Schema versions (the Phase 3 spec req 9 says "version 2" for the
registry; #83 took version 2 first, so the registry is version 3):

- **Version 1**: the Phase 2 fact tables, `ingestion_runs` and
  `schema_version` (`TABLE_NAMES`). No migration: a version-1 store raises
  `SchemaVersionError` and is rebuilt (#83).
- **Version 2** (#83): adds `corporate_actions.announced_at`.
- **Version 3** (#117): adds the eight trial-registry tables
  (`REGISTRY_TABLE_NAMES`). The migration from version 2 is additive: it
  creates the registry tables and appends a version-3 row to
  `schema_version`; no fact table, `ingestion_runs` row or existing
  `schema_version` row changes. It runs on any writable connection that
  calls `init_schema`. A read-only connection never migrates: on a
  version-2 (or empty) store it raises `RegistryNotInitialised`, which the
  read-only dashboard pages turn into a "registry not initialised" state.
- **Version 4** (#108): `corporate_actions` gains `source_action_id` and
  `cancelled` and the unique index `corporate_actions_identity`. DuckDB
  cannot add a NOT NULL column or change a UNIQUE constraint in place, so
  the migration from version 2 or 3 rebuilds the table: a new table with
  the version-4 DDL, every existing row copied with `source_action_id =
  ''` and `cancelled = FALSE` (an id-less, live row: its identity is still
  `(security_id, action_type, ex_date)`, so as-of reads see exactly what
  they saw before), the old table dropped and the new one renamed. A
  version-2 store gets the registry tables too, and both version rows.
  Every other table is untouched. The whole of `init_schema` runs in one
  transaction (the caller's, if one is open, as in `open_for_write`), so a
  failed migration leaves the store as it was. A read-only connection
  never migrates: a version-3 store raises `SchemaVersionError` naming the
  fix (open it for writing once).
- **Version 5** (Phase 4 T49): adds the paper-trading journal tables
  (`JOURNAL_TABLE_NAMES`). The migration from version 2, 3 or 4 is
  additive for the journal: it creates the journal tables and appends a
  version-5 row (after the version-3 and version-4 steps when those are
  due); no fact or registry table changes. **Any** write connection
  migrates, `ingest` included, so the owner's store reaches version 5 on
  the first nightly run after this version is pulled: the owner copies the
  store file before that pull (the Phase 4 runbook repeats this). A
  read-only connection never migrates, and it accepts a version-4 store as
  a pre-journal version, so fact and registry reads keep working there; a
  journal read on such a store is `store.journal`'s
  `JournalNotInitialised`, raised when the tables are absent.
- **Version 6** (#332): `order_events.reason` becomes a nullable closed set
  (`ORDER_EVENT_REASONS`), so a misspelt `halt` or `not_received` can no
  longer turn off `plan.decision_state`'s protection of a halted or
  never-received buy. DuckDB cannot add a `CHECK` in place, so the
  migration from version 5 rebuilds `order_events` with the version-6 DDL,
  every row kept; it first refuses with `SchemaVersionError`, changing
  nothing, if a stored reason is outside the set. A store at version 4 or
  earlier gets the version-6 journal directly and both version rows. A
  read-only connection accepts a version-5 store (reads do not depend on
  the `CHECK`).
- **Version 7** (#377): `decisions.reason` becomes a nullable closed set
  (`DECISION_REASONS`), so a misspelt `left_targets` or `window_stop` can
  no longer turn a full exit into a trim or hide a forced exit's residue
  from `plan`. The migration from version 6 rebuilds `decisions` as
  version 6 rebuilt `order_events`, every row kept in insertion order; it
  first refuses with `SchemaVersionError`, changing nothing, if a stored
  reason is outside the set. A version-5 store gets both rebuilds and both
  version rows, in the one transaction. A read-only connection accepts a
  version-5 or version-6 store (reads do not depend on either `CHECK`).
- **Version 8** (#472): `resume_invocations` gains `accept_rejections`
  (`paper resume --accept-rejections`, the owner's flag), and the new
  `resume_acceptances` table records, for a resume given the flag, the
  rejection-cap verdicts it accepted (`accepted_json`, `[]` for none).
  DuckDB cannot add a NOT NULL column in place, so the migration from
  version 5, 6 or 7 rebuilds `resume_invocations` with the version-8 DDL,
  every row kept in insertion order with `accept_rejections = FALSE` (no
  earlier resume could be given the flag), and the DDL pass creates
  `resume_acceptances`. A store at version 4 or earlier gets the version-8
  journal directly. A read-only connection accepts a version-7 store, so
  every other read keeps working (`store.journal.require_journal` does not
  ask for `LATER_JOURNAL_TABLE_NAMES`); a `resume_invocations` or
  `resume_acceptances` read there fails on the missing column or table
  (only `paper resume`'s write path reads them).
- **A later DDL change goes to version 9**, with its own migration and a
  note here, never a silent edit of the DDL below.

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

Journal tables (Phase 4 spec "Data / interfaces" > Tables) are not fact
tables either: they carry `known_at` (the shared clock's reading when the
system learned or decided the fact; a broker timestamp is never a
`known_at`) and `ingested_at`, both NOT NULL with `known_at <=
ingested_at`, but no `source` or `provenance`, and they stay out of
`TABLE_NAMES`. Append-only by contract (`store.journal` exposes inserts and
reads only), so no column is filled in later. Schema-level backstops: a
primary key on each table's own id, `fills.broker_fill_id` UNIQUE, a
`CHECK` on every column the spec enumerates as a closed set (an open set,
written with "…" in the spec, gets none), `overrides.reason` non-blank
after trimming, and a positive `price` on `broker_feed` fills. A column is
NOT NULL only where the spec's rules say it always has a value; anything
conditional (a reason, a fault type, a broker id, a JSON detail) is
nullable. No foreign keys, as for the other tables; ids come from
`store.journal`.

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
  created *from* a filing, so a name is always available at insert time,
  although it may be the empty string for an index row whose name column
  is blank (#358).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
#: a version-2, 3, 4, 5, 6 or 7 store to. Bump and add a migration note (not
#: silent DDL edits) if the shape of a table changes after data has been
#: loaded.
#:
#: Migration notes:
#: - 2 (issue #83): `corporate_actions.announced_at TIMESTAMPTZ` (nullable),
#:   the source's announcement time, so a first-seen `known_at` earlier than
#:   the proxy is checkable. No store had ingested data at version 1, so
#:   there is no migration code: a version-1 store raises
#:   `SchemaVersionError`; rebuild it.
#: - 3 (issue #117, Phase 3 T31): the eight trial-registry tables
#:   (`REGISTRY_TABLE_NAMES`). Additive migration from version 2: the
#:   registry tables are created and a version-3 row appended; nothing else
#:   changes (module docstring, "Schema versions").
#: - 4 (issue #108): `corporate_actions` gains `source_action_id` and
#:   `cancelled`, its UNIQUE key gains `source_action_id`, and the unique
#:   index `corporate_actions_identity` holds one row per identity per
#:   `known_at`. Migration from version 2 or 3: the table is rebuilt in one
#:   transaction with every existing row kept and given `source_action_id =
#:   ''`, `cancelled = FALSE`, which is exactly its identity before (#108).
#: - 5 (Phase 4 T49): the paper-trading journal tables
#:   (`JOURNAL_TABLE_NAMES`). Additive migration from version 2, 3 or 4: the
#:   journal tables are created and a version-5 row appended; no fact or
#:   registry table changes (module docstring, "Schema versions").
#: - 6 (#332): `order_events.reason` gets a nullable closed `CHECK`
#:   (`ORDER_EVENT_REASONS`). Migration from version 5: `order_events` is
#:   rebuilt in one transaction with every row kept, refused first if a stored
#:   reason is outside the set (module docstring, "Schema versions").
#: - 7 (#377): `decisions.reason` gets a nullable closed `CHECK`
#:   (`DECISION_REASONS`). Migration from version 6 (or 5, after the version-6
#:   step): `decisions` is rebuilt in one transaction with every row kept,
#:   refused first if a stored reason is outside the set.
#: - 8 (#472): `resume_invocations.accept_rejections BOOLEAN NOT NULL` and
#:   the `resume_acceptances` table. Migration from version 7 (or 5 or 6,
#:   after their steps): `resume_invocations` is rebuilt in one transaction
#:   with every row kept and given `accept_rejections = FALSE`.
CURRENT_SCHEMA_VERSION = 8

#: The last version without the registry (fact tables as of #83).
_PRE_REGISTRY_VERSION = 2

#: The last version without action identity (#108): the registry, and
#: `corporate_actions` still keyed by `(security_id, action_type, ex_date)`.
_PRE_ACTION_IDENTITY_VERSION = 3

#: The last version without the journal (#108's action identity and the
#: registry): read-only connections still serve fact and registry reads.
_PRE_JOURNAL_VERSION = 4

#: The last version without the `order_events.reason` `CHECK` (#332):
#: read-only connections serve every read.
_PRE_ORDER_EVENT_REASON_VERSION = 5

#: The last version without the `decisions.reason` `CHECK` (#377): read-only
#: connections serve every read.
_PRE_DECISION_REASON_VERSION = 6

#: The last version without `resume_invocations.accept_rejections` and
#: `resume_acceptances` (#472): read-only connections serve every other read.
_PRE_ACCEPT_REJECTIONS_VERSION = 7


class SchemaVersionError(RuntimeError):
    """The store's `schema_version` table records a version this code has
    no migration from — it does not know that shape and refuses to
    operate on it rather than guessing."""


class RegistryNotInitialised(RuntimeError):
    """A read-only connection opened a store without the trial registry
    (a version-2 store, or one never initialised). Read-only
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

# announced_at is the source's announcement time, NULL when the source
# gives none (issue #83). A first-seen row's known_at equals announced_at
# capped at the close of the session before ex_date, else that close
# (spec req 5); revisions carry it forward unchanged. The adapters enforce
# that, the table only stores it. It is evidence for the stamp, never a
# time to filter on: as-of reads use known_at only.
#
# An action's identity (#108) is `(security_id, source_action_id)` when the
# source gives a stable id, else `(security_id, action_type, ex_date)`, so a
# re-dated action (same id, new ex_date) is a revision of one event, not a
# second event. source_action_id uses '' for "the source gave no id", not
# NULL, for the same UNIQUE reason as facts.class_member below. A revision
# with cancelled = TRUE withdraws the event from its known_at on; that also
# retires the old key of an id-less re-date, whose replacement row is
# stamped at its ingested_at (spec req 5).
_CREATE_CORPORATE_ACTIONS = f"""
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    announced_at TIMESTAMPTZ,
    source_action_id VARCHAR NOT NULL DEFAULT '',
    cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["corporate_actions"])},
    UNIQUE (security_id, action_type, ex_date, source_action_id, known_at)
)
"""

# One row per identity per known_at (#108), so "the latest revision" is never
# a tie. The table's UNIQUE cannot say this for an id identity, where two
# rows with one id and one known_at could differ in ex_date; DuckDB has no
# partial index, so the id-less parts collapse to '' when an id is present.
_CREATE_CORPORATE_ACTIONS_IDENTITY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS corporate_actions_identity ON corporate_actions (
    security_id,
    source_action_id,
    (CASE WHEN source_action_id = '' THEN action_type ELSE '' END),
    (CASE WHEN source_action_id = '' THEN CAST(ex_date AS VARCHAR) ELSE '' END),
    known_at
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
    _CREATE_CORPORATE_ACTIONS_IDENTITY_INDEX,
    _CREATE_FACTS,
    _CREATE_INGESTION_RUNS,
    _CREATE_SCHEMA_VERSION,
)


# Trial registry (schema version 3; Phase 3 spec "Data / interfaces" >
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
# store_max_ingested_at is NULL on a store with no fact rows. The two
# session columns hold the requested window dates as given; data_cutoff
# (the close of the end session) is NULL when the requested end cannot
# be resolved on the trading calendar, so that refusal still leaves a
# trials row.
_CREATE_TRIALS = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id BIGINT NOT NULL PRIMARY KEY,
    hypothesis_id BIGINT NOT NULL,
    kind VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    start_session DATE NOT NULL,
    end_session DATE NOT NULL,
    data_cutoff TIMESTAMPTZ,
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

#: The trial-registry tables added at schema version 3, disjoint from
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


# Paper-trading journal (schema version 5; Phase 4 spec "Data / interfaces"
# > Tables and module docstring). Every JSON payload is VARCHAR, as in the
# registry. Money and quantities are DOUBLE (fractional shares).
_JOURNAL_TIMESTAMPS = """
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    CHECK (known_at <= ingested_at)
"""


SIDES: tuple[str, ...] = ("buy", "sell")

#: `order_events.reason` of the `cancel_requested` (and `cancel_failed`) event the
#: halt path journals before each cancel call.
HALT_REASON = "halt"
#: `order_events.reason` of the terminal `cancelled` event `paper resume` journals
#: for a `pending` order the broker never received.
NOT_RECEIVED_REASON = "not_received"
#: Every `order_events.reason` the spec names (#332). `plan.decision_state` keeps a
#: buy open on these exact spellings, so the column is a closed set.
ORDER_EVENT_REASONS: tuple[str, ...] = (HALT_REASON, NOT_RECEIVED_REASON)

#: `decisions.reason` of a plan full exit: a held name that left the targets.
LEFT_TARGETS_REASON = "left_targets"
#: `decisions.reason` of a plan full exit: a held name that left the universe.
LEFT_UNIVERSE_REASON = "left_universe"
#: `decisions.reason` (and `overrides.kind`) of an `exclude_name` override decision.
EXCLUDE_NAME_REASON = "exclude_name"
#: `decisions.reason` (and `overrides.kind`) of a `keep_name` override decision.
KEEP_NAME_REASON = "keep_name"
#: `overrides.kind` of an owner kill-switch engagement.
ENGAGE_KILL_SWITCH_KIND = "engage_kill_switch"
#: `decisions.reason` of a forced exit of a delisted holding.
DELISTED_REASON = "delisted"
#: `decisions.reason` of a forced exit of a holding received but never targeted.
UNTARGETED_RECEIPT_REASON = "untargeted_receipt"
#: `decisions.reason` of a window stop's forced exit (also a `rebalance_events`
#: reason).
WINDOW_STOP_REASON = "window_stop"
#: Every `decisions.reason` the spec names (#377). A full exit is defined from
#: (decision, reason) and `plan` matches on these exact spellings, so the column is
#: a closed set.
DECISION_REASONS: tuple[str, ...] = (
    LEFT_TARGETS_REASON,
    LEFT_UNIVERSE_REASON,
    EXCLUDE_NAME_REASON,
    KEEP_NAME_REASON,
    DELISTED_REASON,
    UNTARGETED_RECEIPT_REASON,
    WINDOW_STOP_REASON,
)

#: Every journal column the spec enumerates as a closed set, with its allowed
#: values; the DDL turns each into a `CHECK`. Columns in `NULLABLE_JOURNAL_ENUMS`
#: may also be NULL: the spec lists `null` among their values, or the row can
#: lack the thing (a skip decision has no side, a non-session run no kind).
JOURNAL_ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("paper_window_stops", "state"): ("requested", "closed", "abandoned"),
    ("paper_runs", "kind"): ("rebalance", "catch_up", "mark", "stop"),
    ("paper_runs", "invoked_by"): ("scheduler", "tty"),
    ("paper_run_results", "status"): (
        "ok",
        "halted",
        "stale",
        "skipped_kill_switch",
        "crashed",
        "failed",
        "no_session",
    ),
    ("rebalance_events", "status"): ("executed", "missed"),
    ("rebalance_events", "reason"): (
        "catch_up_lapsed",
        "kill_switch",
        "limit_breach",
        "skip_cap",
        WINDOW_STOP_REASON,
    ),
    ("signals", "reason"): ("selected", "below_cut", "excluded_no_history"),
    ("decisions", "side"): SIDES,
    ("decisions", "decision"): (
        "trade",
        "skip_below_minimum",
        "skip_untradable",
        "skip_below_one_share",
        "skip_delisted",
        "skip_zero",
        "dust",
        "override",
        "forced_exit",
    ),
    ("decisions", "reason"): DECISION_REASONS,
    ("decision_events", "status"): ("skipped", "written_off"),
    ("decision_events", "reason"): (
        "skip_below_one_share",
        "skip_untradable",
        "skip_below_minimum",
        "skip_delisted",
        "dust",
        "untradable",
        "unfunded",
    ),
    ("orders", "phase"): ("sell", "buy", "exit"),
    ("orders", "side"): SIDES,
    ("order_events", "status"): (
        "pending",
        "accepted",
        "replay",
        "cancel_requested",
        "cancel_noop",
        "cancel_failed",
        "filled",
        "expired",
        "rejected",
        "cancelled",
    ),
    ("order_events", "reason"): ORDER_EVENT_REASONS,
    ("fills", "source"): ("broker_feed", "broker_status"),
    ("fill_cursors", "writer_kind"): ("run", "resume"),
    ("outcomes", "kind"): ("position_return", "realised_pnl", "not_executed"),
    ("adjustments", "kind"): (
        "corporate_action_cash",
        "dividend_cash",
        "spinoff_receipt",
        "carried_residue",
    ),
    ("adjustments", "origin"): ("dust", "untradable"),
    ("reconciliations", "status"): ("ok", "mismatch", "pending_unresolved", "fills_lagging"),
    ("kill_switch", "state"): ("engaged", "released"),
    ("kill_switch", "source"): ("owner", "fault", "drawdown"),
    ("overrides", "kind"): (EXCLUDE_NAME_REASON, KEEP_NAME_REASON, ENGAGE_KILL_SWITCH_KIND),
}

NULLABLE_JOURNAL_ENUMS: frozenset[tuple[str, str]] = frozenset(
    {
        ("rebalance_events", "reason"),
        ("decisions", "side"),
        ("paper_runs", "kind"),
        ("decision_events", "reason"),
        ("adjustments", "origin"),
        ("order_events", "reason"),
        ("decisions", "reason"),
    }
)


def _check(table: str, column: str) -> str:
    """The `CHECK` restricting `table.column` to its `JOURNAL_ENUMS` values (and
    NULL when it is in `NULLABLE_JOURNAL_ENUMS`)."""
    allowed = ", ".join(f"'{value}'" for value in JOURNAL_ENUMS[table, column])
    check = f"{column} IN ({allowed})"
    if (table, column) in NULLABLE_JOURNAL_ENUMS:
        return f"CHECK ({column} IS NULL OR {check})"
    return f"CHECK ({check})"


_CREATE_PAPER_WINDOWS = f"""
CREATE TABLE IF NOT EXISTS paper_windows (
    window_id BIGINT NOT NULL PRIMARY KEY,
    hypothesis_id BIGINT NOT NULL,
    first_rebalance_session DATE NOT NULL,
    account_id VARCHAR NOT NULL,
    starting_cash DOUBLE NOT NULL,
    starting_equity DOUBLE NOT NULL,
    code_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    frozen_json VARCHAR NOT NULL,
    frozen_sha256 VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

# `abandoned` from the start (#247 Q13; T64b amends the spec to match).
_CREATE_PAPER_WINDOW_STOPS = f"""
CREATE TABLE IF NOT EXISTS paper_window_stops (
    window_id BIGINT NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    state VARCHAR NOT NULL,
    reason VARCHAR,
    reconciliation_id BIGINT,
    residues_json VARCHAR,
    {_JOURNAL_TIMESTAMPS},
    {_check("paper_window_stops", "state")}
)
"""

# code_dirty is NULL outside a git checkout, as `trials.code_dirty`. On a
# non-session day the run has no S and no kind (it exits with a `no_session`
# result), so session and kind are NULL together.
_CREATE_PAPER_RUNS = f"""
CREATE TABLE IF NOT EXISTS paper_runs (
    run_id BIGINT NOT NULL PRIMARY KEY,
    window_id BIGINT NOT NULL,
    session DATE,
    kind VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    invoked_by VARCHAR NOT NULL,
    code_version VARCHAR NOT NULL,
    code_dirty BOOLEAN,
    {_JOURNAL_TIMESTAMPS},
    {_check("paper_runs", "kind")},
    {_check("paper_runs", "invoked_by")},
    CHECK ((session IS NULL) = (kind IS NULL))
)
"""

# One result per run: a run's outcome is this row, never an update; a run
# without one is `unfinished`.
_CREATE_PAPER_RUN_RESULTS = f"""
CREATE TABLE IF NOT EXISTS paper_run_results (
    run_id BIGINT NOT NULL PRIMARY KEY,
    finished_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    fault_type VARCHAR,
    message VARCHAR,
    clock_fault BOOLEAN NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("paper_run_results", "status")}
)
"""

# One plan per planning run (a catch-up re-uses the decisions, never re-plans).
# store_max_ingested_at is NULL on a store with no fact rows, as in `trials`.
_CREATE_PAPER_PLANS = f"""
CREATE TABLE IF NOT EXISTS paper_plans (
    run_id BIGINT NOT NULL PRIMARY KEY,
    plan_trial_id BIGINT NOT NULL,
    rebalance_session DATE NOT NULL,
    store_max_ingested_at TIMESTAMPTZ,
    n_universe INTEGER NOT NULL,
    n_targets INTEGER NOT NULL,
    n_orders_below_min_at_live_capital INTEGER NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

# `pending` is derived (F_i <= S and no row), never stored.
_CREATE_REBALANCE_EVENTS = f"""
CREATE TABLE IF NOT EXISTS rebalance_events (
    rebalance_session DATE NOT NULL,
    run_id BIGINT NOT NULL,
    status VARCHAR NOT NULL,
    reason VARCHAR,
    {_JOURNAL_TIMESTAMPS},
    {_check("rebalance_events", "status")},
    {_check("rebalance_events", "reason")}
)
"""

_CREATE_PAPER_REPORTS = f"""
CREATE TABLE IF NOT EXISTS paper_reports (
    window_id BIGINT NOT NULL,
    trial_id BIGINT NOT NULL,
    through_session DATE NOT NULL,
    run_at TIMESTAMPTZ NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

# score and rank are NULL for a name excluded for lack of history.
_CREATE_SIGNALS = f"""
CREATE TABLE IF NOT EXISTS signals (
    run_id BIGINT NOT NULL,
    rebalance_session DATE NOT NULL,
    security_id VARCHAR NOT NULL,
    score DOUBLE,
    rank INTEGER,
    reason VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("signals", "reason")}
)
"""

# rebalance_session is NULL for a decision outside a rebalance (a forced
# exit); side is NULL on a decision that trades nothing (a skip, dust).
# `reason` is a closed set since version 7 (#377).
# whole_share is the fractionable flag at decision time, read from this row
# and never from the live asset.
_CREATE_DECISIONS = f"""
CREATE TABLE IF NOT EXISTS decisions (
    decision_id BIGINT NOT NULL PRIMARY KEY,
    run_id BIGINT NOT NULL,
    rebalance_session DATE,
    security_id VARCHAR NOT NULL,
    target_weight DOUBLE,
    drifted_weight DOUBLE,
    side VARCHAR,
    planned_notional DOUBLE,
    planned_quantity DOUBLE,
    target_notional DOUBLE,
    whole_share BOOLEAN NOT NULL,
    decision VARCHAR NOT NULL,
    reason VARCHAR,
    override_id BIGINT,
    {_JOURNAL_TIMESTAMPS},
    {_check("decisions", "side")},
    {_check("decisions", "decision")},
    {_check("decisions", "reason")}
)
"""

_CREATE_DECISION_EVENTS = f"""
CREATE TABLE IF NOT EXISTS decision_events (
    decision_id BIGINT NOT NULL,
    run_id BIGINT NOT NULL,
    status VARCHAR NOT NULL,
    reason VARCHAR,
    unfunded_notional DOUBLE,
    {_JOURNAL_TIMESTAMPS},
    {_check("decision_events", "status")},
    {_check("decision_events", "reason")}
)
"""

# An order is sized by notional or by quantity, so each may be NULL.
_CREATE_ORDERS = f"""
CREATE TABLE IF NOT EXISTS orders (
    client_order_id VARCHAR NOT NULL PRIMARY KEY,
    decision_id BIGINT NOT NULL,
    run_id BIGINT NOT NULL,
    session DATE NOT NULL,
    attempt INTEGER NOT NULL,
    phase VARCHAR NOT NULL,
    security_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    notional DOUBLE,
    quantity DOUBLE,
    sells_in_flight_at_submit BOOLEAN NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("orders", "phase")},
    {_check("orders", "side")}
)
"""

# event_at is the broker's instant (NULL before the broker has one, e.g. a
# `pending` row written before submit); `reason` is a closed set since version 6.
_CREATE_ORDER_EVENTS = f"""
CREATE TABLE IF NOT EXISTS order_events (
    client_order_id VARCHAR NOT NULL,
    event_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    reason VARCHAR,
    broker_order_id VARCHAR,
    filled_quantity DOUBLE,
    filled_avg_price DOUBLE,
    raw_json VARCHAR,
    {_JOURNAL_TIMESTAMPS},
    {_check("order_events", "status")},
    {_check("order_events", "reason")}
)
"""

# quantity is unsigned (side comes from the order). A `broker_status` row is
# the implied residual that completes an order (`broker_fill_id =
# synthetic:<client_order_id>`): its price is stored as computed and flagged
# price_implied, which is exactly `source = 'broker_status'`; only
# `broker_feed` rows must have a positive price. A
# later real fill supersedes it through superseded_by; every reader goes
# through `store.journal`'s accessor, which hides superseded rows.
_CREATE_FILLS = f"""
CREATE TABLE IF NOT EXISTS fills (
    fill_id BIGINT NOT NULL PRIMARY KEY,
    client_order_id VARCHAR NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL,
    quantity DOUBLE NOT NULL,
    price DOUBLE NOT NULL,
    price_implied BOOLEAN NOT NULL,
    broker_fill_id VARCHAR NOT NULL UNIQUE,
    source VARCHAR NOT NULL,
    superseded_by BIGINT,
    {_JOURNAL_TIMESTAMPS},
    {_check("fills", "source")},
    CHECK (quantity > 0),
    CHECK (price_implied = (source = 'broker_status')),
    CHECK (source <> 'broker_feed' OR price > 0)
)
"""

_CREATE_FILL_CURSORS = f"""
CREATE TABLE IF NOT EXISTS fill_cursors (
    writer_kind VARCHAR NOT NULL,
    writer_id BIGINT NOT NULL,
    collected_through TIMESTAMPTZ NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("fill_cursors", "writer_kind")}
)
"""

# Written first; the outcome is the `kill_switch` `released` row carrying
# this resume_id, or its absence.
_CREATE_RESUME_INVOCATIONS = f"""
CREATE TABLE IF NOT EXISTS resume_invocations (
    resume_id BIGINT NOT NULL PRIMARY KEY,
    "at" TIMESTAMPTZ NOT NULL,
    reason VARCHAR NOT NULL,
    accept_broker_fills BOOLEAN NOT NULL,
    accept_rejections BOOLEAN NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

# One row per resume given `--accept-rejections` (#472), written once its
# rejection-cap verdicts are judged and before its reconciliation and release:
# a JSON list of the verdicts the flag accepted, `[]` when there was none. It is
# not a release: that is the `kill_switch` `released` row citing the resume_id.
_CREATE_RESUME_ACCEPTANCES = f"""
CREATE TABLE IF NOT EXISTS resume_acceptances (
    resume_id BIGINT NOT NULL PRIMARY KEY,
    accepted_json VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

_CREATE_OUTCOMES = f"""
CREATE TABLE IF NOT EXISTS outcomes (
    client_order_id VARCHAR NOT NULL,
    through_session DATE NOT NULL,
    kind VARCHAR NOT NULL,
    value DOUBLE,
    contribution DOUBLE,
    mark_price DOUBLE,
    {_JOURNAL_TIMESTAMPS},
    {_check("outcomes", "kind")}
)
"""

# A session with no position (cash only, as at close(T_0) or after a full
# exit) is one row with security_id NULL and quantity 0, so its equity (cash)
# is still marked.
_CREATE_POSITIONS_DAILY = f"""
CREATE TABLE IF NOT EXISTS positions_daily (
    run_id BIGINT NOT NULL,
    session DATE NOT NULL,
    security_id VARCHAR,
    quantity DOUBLE NOT NULL,
    mark_price DOUBLE,
    value DOUBLE,
    cash DOUBLE,
    tradable BOOLEAN,
    {_JOURNAL_TIMESTAMPS},
    CHECK (security_id IS NOT NULL OR quantity = 0)
)
"""

_CREATE_ADJUSTMENTS = f"""
CREATE TABLE IF NOT EXISTS adjustments (
    adjustment_id BIGINT NOT NULL PRIMARY KEY,
    window_id BIGINT NOT NULL,
    run_id BIGINT,
    session DATE NOT NULL,
    kind VARCHAR NOT NULL,
    origin VARCHAR,
    security_id VARCHAR,
    quantity DOUBLE,
    cash DOUBLE,
    explanation_json VARCHAR,
    {_JOURNAL_TIMESTAMPS},
    {_check("adjustments", "kind")},
    {_check("adjustments", "origin")}
)
"""

_CREATE_RECONCILIATIONS = f"""
CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id BIGINT NOT NULL PRIMARY KEY,
    window_id BIGINT NOT NULL,
    run_id BIGINT,
    "at" TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    broker_cash DOUBLE,
    mismatches_json VARCHAR,
    {_JOURNAL_TIMESTAMPS},
    {_check("reconciliations", "status")}
)
"""

_CREATE_KILL_SWITCH = f"""
CREATE TABLE IF NOT EXISTS kill_switch (
    event_id BIGINT NOT NULL PRIMARY KEY,
    window_id BIGINT NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    state VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    fault_type VARCHAR,
    reason VARCHAR,
    run_id BIGINT,
    override_id BIGINT,
    resume_id BIGINT,
    reconciliation_id BIGINT,
    peak_equity DOUBLE,
    {_JOURNAL_TIMESTAMPS},
    {_check("kill_switch", "state")},
    {_check("kill_switch", "source")}
)
"""

# The frozen `paper.min_override_reason_chars` is checked by the writer; the
# schema refuses a blank reason whatever the config says.
_CREATE_OVERRIDES = f"""
CREATE TABLE IF NOT EXISTS overrides (
    override_id BIGINT NOT NULL PRIMARY KEY,
    window_id BIGINT NOT NULL,
    made_at TIMESTAMPTZ NOT NULL,
    rebalance_session DATE,
    security_id VARCHAR,
    kind VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("overrides", "kind")},
    CHECK (length(trim(reason)) >= 1)
)
"""

# run_id is NULL for an alert raised outside a run (`locked`, `no_window`).
# session is the run's S, or the calendar session containing `at` (the next
# one on a non-session day): always set, since alerts dedupe on it.
_CREATE_ALERTS = f"""
CREATE TABLE IF NOT EXISTS alerts (
    alert_id BIGINT NOT NULL PRIMARY KEY,
    run_id BIGINT,
    session DATE NOT NULL,
    kind VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

_CREATE_ALERT_DELIVERIES = f"""
CREATE TABLE IF NOT EXISTS alert_deliveries (
    alert_id BIGINT NOT NULL,
    channel VARCHAR NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    ok BOOLEAN NOT NULL,
    error VARCHAR,
    {_JOURNAL_TIMESTAMPS}
)
"""

# cusip is NULL when the broker's asset has none; fill_id is NULL for a lot
# merged from an order's fills at its average price (a `price_implied` fill).
_CREATE_LOTS = f"""
CREATE TABLE IF NOT EXISTS lots (
    lot_id BIGINT NOT NULL PRIMARY KEY,
    account_id VARCHAR NOT NULL,
    account_type VARCHAR NOT NULL,
    account_owner VARCHAR NOT NULL,
    security_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    cusip VARCHAR,
    trade_at TIMESTAMPTZ NOT NULL,
    trade_date_local DATE NOT NULL,
    quantity DOUBLE NOT NULL,
    cost_basis DOUBLE NOT NULL,
    fill_id BIGINT,
    {_JOURNAL_TIMESTAMPS}
)
"""

_CREATE_DISPOSALS = f"""
CREATE TABLE IF NOT EXISTS disposals (
    disposal_id BIGINT NOT NULL PRIMARY KEY,
    lot_id BIGINT NOT NULL,
    account_id VARCHAR NOT NULL,
    trade_at TIMESTAMPTZ NOT NULL,
    trade_date_local DATE NOT NULL,
    quantity DOUBLE NOT NULL,
    proceeds DOUBLE NOT NULL,
    realised_pnl DOUBLE NOT NULL,
    tax_year INTEGER NOT NULL,
    fill_id BIGINT,
    {_JOURNAL_TIMESTAMPS}
)
"""

_CREATE_WASH_SALE_FLAGS = f"""
CREATE TABLE IF NOT EXISTS wash_sale_flags (
    flag_id BIGINT NOT NULL PRIMARY KEY,
    disposal_id BIGINT NOT NULL,
    replacement_lot_id BIGINT NOT NULL,
    matched_quantity DOUBLE NOT NULL,
    disallowed_amount DOUBLE NOT NULL,
    scanned_at TIMESTAMPTZ NOT NULL,
    {_JOURNAL_TIMESTAMPS}
)
"""

#: The paper-trading journal tables added at schema version 5, disjoint
#: from `TABLE_NAMES` and `REGISTRY_TABLE_NAMES`, so the look-ahead harness
#: never sees them.
JOURNAL_TABLE_NAMES: tuple[str, ...] = (
    "paper_windows",
    "paper_window_stops",
    "paper_runs",
    "paper_run_results",
    "paper_plans",
    "rebalance_events",
    "paper_reports",
    "signals",
    "decisions",
    "decision_events",
    "orders",
    "order_events",
    "fills",
    "fill_cursors",
    "resume_invocations",
    "resume_acceptances",
    "outcomes",
    "positions_daily",
    "adjustments",
    "reconciliations",
    "kill_switch",
    "overrides",
    "alerts",
    "alert_deliveries",
    "lots",
    "disposals",
    "wash_sale_flags",
)

#: Journal tables added after version 5 (#472), which a read-only connection to
#: an older journal store lacks: `store.journal.require_journal` does not ask for
#: them, so every other journal read keeps working there.
LATER_JOURNAL_TABLE_NAMES: tuple[str, ...] = ("resume_acceptances",)

_JOURNAL_TABLE_DDL: tuple[str, ...] = (
    _CREATE_PAPER_WINDOWS,
    _CREATE_PAPER_WINDOW_STOPS,
    _CREATE_PAPER_RUNS,
    _CREATE_PAPER_RUN_RESULTS,
    _CREATE_PAPER_PLANS,
    _CREATE_REBALANCE_EVENTS,
    _CREATE_PAPER_REPORTS,
    _CREATE_SIGNALS,
    _CREATE_DECISIONS,
    _CREATE_DECISION_EVENTS,
    _CREATE_ORDERS,
    _CREATE_ORDER_EVENTS,
    _CREATE_FILLS,
    _CREATE_FILL_CURSORS,
    _CREATE_RESUME_INVOCATIONS,
    _CREATE_RESUME_ACCEPTANCES,
    _CREATE_OUTCOMES,
    _CREATE_POSITIONS_DAILY,
    _CREATE_ADJUSTMENTS,
    _CREATE_RECONCILIATIONS,
    _CREATE_KILL_SWITCH,
    _CREATE_OVERRIDES,
    _CREATE_ALERTS,
    _CREATE_ALERT_DELIVERIES,
    _CREATE_LOTS,
    _CREATE_DISPOSALS,
    _CREATE_WASH_SALE_FLAGS,
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
    if max_version in (
        _PRE_JOURNAL_VERSION,
        _PRE_ORDER_EVENT_REASON_VERSION,
        _PRE_DECISION_REASON_VERSION,
        _PRE_ACCEPT_REJECTIONS_VERSION,
        CURRENT_SCHEMA_VERSION,
    ):
        # Version 4 serves fact and registry reads; versions 5 and 6 every read;
        # version 7 every read but `resume_invocations` and `resume_acceptances`.
        return
    if max_version == _PRE_ACTION_IDENTITY_VERSION:
        raise SchemaVersionError(
            f"store has schema version {max_version}, this code expects "
            f"{CURRENT_SCHEMA_VERSION}, and a read-only connection does not migrate "
            "(run any writing command to migrate the store)"
        )
    if max_version is None or max_version == _PRE_REGISTRY_VERSION:
        found = "no schema" if max_version is None else f"schema version {max_version}"
        raise RegistryNotInitialised(
            f"store has {found}; the trial registry needs version "
            f"{CURRENT_SCHEMA_VERSION}, and a read-only connection does not migrate "
            "(run any writing command to migrate the store)"
        )
    raise SchemaVersionError(
        f"store schema_version is {max_version}, this code expects {CURRENT_SCHEMA_VERSION}"
    )


def in_transaction(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether `conn` has an explicit transaction open. In autocommit mode
    every statement runs in a transaction of its own, so two statements in
    a row see different ids; inside an open transaction they share one.
    (Probing with `BEGIN` is not an option: a failed `BEGIN` aborts the
    open transaction.)"""
    query = "SELECT current_transaction_id()"
    first = conn.execute(query).fetchone()
    second = conn.execute(query).fetchone()
    return first == second


@contextmanager
def atomic(conn: duckdb.DuckDBPyConnection) -> Iterator[None]:
    """Run the block in one transaction: the caller's if one is open (it
    commits or rolls back), else a new one committed on success and rolled
    back on any exception."""
    if in_transaction(conn):
        yield
        return
    conn.execute("BEGIN TRANSACTION")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


#: Where `_migrate_action_identity` builds the version-4 table before it
#: takes the name `corporate_actions`.
_ACTIONS_STAGING_TABLE = "corporate_actions_v4"

#: The `corporate_actions` columns at versions 2 and 3, all kept by the
#: version-4 migration.
_PRE_IDENTITY_ACTION_COLUMNS = (
    "security_id",
    "action_type",
    "ex_date",
    "ratio_or_amount",
    "announced_at",
    "known_at",
    "ingested_at",
    "source",
    "provenance",
)


def _migrate_action_identity(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-2/3 `corporate_actions` with the version-4 DDL
    (module docstring, "Schema versions"). Every row is kept; the new
    columns take their defaults (`''`, `FALSE`). The identity index is
    created afterwards by `init_schema`'s DDL pass. Runs inside
    `init_schema`'s transaction."""
    staging_ddl = _CREATE_CORPORATE_ACTIONS.replace(
        "CREATE TABLE IF NOT EXISTS corporate_actions (",
        f"CREATE TABLE {_ACTIONS_STAGING_TABLE} (",
        1,
    )
    columns = ", ".join(_PRE_IDENTITY_ACTION_COLUMNS)
    conn.execute(staging_ddl)
    conn.execute(
        f"INSERT INTO {_ACTIONS_STAGING_TABLE} ({columns}) SELECT {columns} FROM corporate_actions"
    )
    conn.execute("DROP TABLE corporate_actions")
    conn.execute(f"ALTER TABLE {_ACTIONS_STAGING_TABLE} RENAME TO corporate_actions")


#: Where `_migrate_order_event_reasons` builds the version-6 table before it
#: takes the name `order_events`.
_ORDER_EVENTS_STAGING_TABLE = "order_events_v6"


def _migrate_order_event_reasons(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-5 `order_events` with the version-6 DDL (module
    docstring, "Schema versions"), every row kept. Raises `SchemaVersionError`
    before any change if a stored reason is outside `ORDER_EVENT_REASONS`: that
    row needs the owner's look, not a silent rewrite. Runs inside
    `init_schema`'s transaction."""
    allowed = ", ".join("?" for _ in ORDER_EVENT_REASONS)
    stray = conn.execute(
        "SELECT DISTINCT reason FROM order_events "
        f"WHERE reason IS NOT NULL AND reason NOT IN ({allowed}) ORDER BY reason",
        list(ORDER_EVENT_REASONS),
    ).fetchall()
    if stray:
        found = ", ".join(repr(reason) for (reason,) in stray)
        raise SchemaVersionError(
            f"order_events holds reasons outside {ORDER_EVENT_REASONS}: {found}; "
            f"the store stays at version {_PRE_ORDER_EVENT_REASON_VERSION} until "
            "they are resolved"
        )
    staging_ddl = _CREATE_ORDER_EVENTS.replace(
        "CREATE TABLE IF NOT EXISTS order_events (",
        f"CREATE TABLE {_ORDER_EVENTS_STAGING_TABLE} (",
        1,
    )
    conn.execute(staging_ddl)
    # Readers break `known_at` ties on rowid (the halt path's `cancel_requested`
    # and `cancel_noop` share a stamp), so the copy keeps the insertion order.
    conn.execute(
        f"INSERT INTO {_ORDER_EVENTS_STAGING_TABLE} BY NAME "
        "SELECT * FROM order_events ORDER BY rowid"
    )
    conn.execute("DROP TABLE order_events")
    conn.execute(f"ALTER TABLE {_ORDER_EVENTS_STAGING_TABLE} RENAME TO order_events")


#: Where `_migrate_decision_reasons` builds the version-7 table before it takes
#: the name `decisions`.
_DECISIONS_STAGING_TABLE = "decisions_v7"


def _migrate_decision_reasons(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-6 `decisions` (or a version-5 one: the table did not
    change at version 6) with the version-7 DDL (module docstring, "Schema
    versions"), every row kept. Raises `SchemaVersionError` before any change of
    its own if a stored reason is outside `DECISION_REASONS`; `init_schema`'s
    transaction rolls back any earlier step. Runs inside that transaction."""
    allowed = ", ".join("?" for _ in DECISION_REASONS)
    stray = conn.execute(
        "SELECT DISTINCT reason FROM decisions "
        f"WHERE reason IS NOT NULL AND reason NOT IN ({allowed}) ORDER BY reason",
        list(DECISION_REASONS),
    ).fetchall()
    if stray:
        found = ", ".join(repr(reason) for (reason,) in stray)
        raise SchemaVersionError(
            f"decisions holds reasons outside {DECISION_REASONS}: {found}; the store "
            "stays at its version until they are resolved"
        )
    staging_ddl = _CREATE_DECISIONS.replace(
        "CREATE TABLE IF NOT EXISTS decisions (",
        f"CREATE TABLE {_DECISIONS_STAGING_TABLE} (",
        1,
    )
    conn.execute(staging_ddl)
    # Keep the insertion order, as `_migrate_order_event_reasons` does.
    conn.execute(
        f"INSERT INTO {_DECISIONS_STAGING_TABLE} BY NAME SELECT * FROM decisions ORDER BY rowid"
    )
    conn.execute("DROP TABLE decisions")
    conn.execute(f"ALTER TABLE {_DECISIONS_STAGING_TABLE} RENAME TO decisions")


#: Where `_migrate_resume_flags` builds the version-8 table before it takes the
#: name `resume_invocations`.
_RESUME_INVOCATIONS_STAGING_TABLE = "resume_invocations_v8"


def _migrate_resume_flags(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-5, 6 or 7 `resume_invocations` (the table did not change
    from version 5 to 7) with the version-8 DDL (module docstring, "Schema
    versions"), every row kept in insertion order with `accept_rejections =
    FALSE`: no resume before version 8 could be given the flag. Runs inside
    `init_schema`'s transaction."""
    staging_ddl = _CREATE_RESUME_INVOCATIONS.replace(
        "CREATE TABLE IF NOT EXISTS resume_invocations (",
        f"CREATE TABLE {_RESUME_INVOCATIONS_STAGING_TABLE} (",
        1,
    )
    conn.execute(staging_ddl)
    conn.execute(
        f"INSERT INTO {_RESUME_INVOCATIONS_STAGING_TABLE} BY NAME "
        "SELECT *, FALSE AS accept_rejections FROM resume_invocations ORDER BY rowid"
    )
    conn.execute("DROP TABLE resume_invocations")
    conn.execute(f"ALTER TABLE {_RESUME_INVOCATIONS_STAGING_TABLE} RENAME TO resume_invocations")


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every store table if it does not already exist, migrating a
    version-2, 3, 4, 5, 6 or 7 store to version 8.

    Idempotent: safe to call on every process start and every test. Also
    pins the connection's session timezone to UTC and disables extension
    auto-install/auto-load (`configure_connection`) so a caller that built
    its own raw connection still gets correct `TIMESTAMPTZ` round-tripping
    and no surprise network access, and drops any cached column-type info
    for `conn` (`store.db.forget_column_types`) so `store.db.insert_row`
    never reuses type info cached before these tables existed.

    On a writable connection, in one transaction (the caller's if open): a
    fresh store gets every table and one `schema_version` row for
    `CURRENT_SCHEMA_VERSION`; a version-7 store gets `resume_invocations`
    rebuilt with `accept_rejections` (every row kept, `FALSE`), the
    `resume_acceptances` table and a version-8 row; a version-6 store gets
    `decisions` rebuilt with the reason `CHECK` (every row kept) first, and
    version-7 and version-8 rows; a version-5 store gets `order_events` rebuilt
    likewise before that, and version-6 to version-8 rows; a version-4 store
    gets the journal tables and version-5 to version-8 rows; a version-3 store gets that plus
    `corporate_actions` rebuilt with the version-4 columns (every row kept)
    and a version-4 row; a version-2 store gets all of that plus the
    registry tables and a version-3 row. Nothing else changes (module
    docstring, "Schema versions").

    On a read-only connection no DDL runs: a version-8, 7, 6 or 5 store
    passes, and so does a version-4 store (fact and registry reads work; the journal
    tables are absent, which `store.journal` reports); a version-2 or
    uninitialised store raises `RegistryNotInitialised`, and a version-3
    store raises `SchemaVersionError`.

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
    pre_identity = (_PRE_REGISTRY_VERSION, _PRE_ACTION_IDENTITY_VERSION)
    migratable = (
        *pre_identity,
        _PRE_JOURNAL_VERSION,
        _PRE_ORDER_EVENT_REASON_VERSION,
        _PRE_DECISION_REASON_VERSION,
        _PRE_ACCEPT_REJECTIONS_VERSION,
    )
    if max_version not in (None, *migratable, CURRENT_SCHEMA_VERSION):
        raise SchemaVersionError(
            f"store schema_version is {max_version}, this code expects {CURRENT_SCHEMA_VERSION}"
        )
    with atomic(conn):
        if max_version in pre_identity:
            _migrate_action_identity(conn)
        if max_version == _PRE_ORDER_EVENT_REASON_VERSION:
            _migrate_order_event_reasons(conn)
        if max_version in (_PRE_ORDER_EVENT_REASON_VERSION, _PRE_DECISION_REASON_VERSION):
            _migrate_decision_reasons(conn)
        if max_version in (
            _PRE_ORDER_EVENT_REASON_VERSION,
            _PRE_DECISION_REASON_VERSION,
            _PRE_ACCEPT_REJECTIONS_VERSION,
        ):
            _migrate_resume_flags(conn)
        for ddl in _TABLE_DDL + _REGISTRY_TABLE_DDL + _JOURNAL_TABLE_DDL:
            conn.execute(ddl)
        forget_column_types(conn)
        if max_version != CURRENT_SCHEMA_VERSION:
            first_new = CURRENT_SCHEMA_VERSION if max_version is None else max_version + 1
            applied_at = utc_now()
            for version in range(first_new, CURRENT_SCHEMA_VERSION + 1):
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    [version, applied_at],
                )

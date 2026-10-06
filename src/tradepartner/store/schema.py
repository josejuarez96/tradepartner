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
than 2 to 11 or `CURRENT_SCHEMA_VERSION`), `init_schema` raises
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
- **Version 9** (#571, spec req 17, plan T84): the owner settlement of an
  order the journal cannot close. `owner_settled_unknown` joins
  `ORDER_EVENT_REASONS` and `settle_order` joins `overrides.kind`;
  `overrides` gains a nullable `client_order_id`, set exactly for a
  `settle_order` row (`CHECK ((kind = 'settle_order') = (client_order_id IS
  NOT NULL))`). Additive: both `CHECK` sets only grow, so no stored row can
  fall outside them. DuckDB cannot change a `CHECK` or add a column with a
  table `CHECK` in place, so the migration from version 5, 6, 7 or 8 rebuilds
  `order_events` and `overrides` as version 6 rebuilt `order_events`, every
  row kept in insertion order, each `overrides` row given `client_order_id =
  NULL` (no earlier row could be a `settle_order`). A store at version 4 or
  earlier gets the version-9 journal directly. A read-only connection accepts
  a version-8 store, so every other read keeps working. Only a command that
  calls `init_schema` on its write connection migrates (`ingest`, `backfill`,
  `paper start`, a backtest run, `hypothesis register`, `decision gap-signoff`); `store.db
  .open_for_write` alone does not. Until one has run, every `overrides` read
  (`store.journal.overrides_for`: `paper check`, `paper run`'s planning and
  kill-switch read, `paper stop`, `paper override`, the override
  page) and write fails loudly on the missing column, never silently: the
  nightly ingest, which `paper run`'s freshness check requires anyway,
  migrates the store, and after pulling this version the owner runs one
  ingest (copying the store file first, as for version 5) before any `paper`
  command.
- **Version 10** (#660, T76; renumbered from 9 at ready time — T84/#714
  landed version 9 first, per the plan line's stated rule that whichever
  of the two PRs lands second renumbers): adds `statement_facts` (as-filed
  revenue, cost of revenue, gross profit, total assets and operating cash
  flow; the 2026-10-03 amendment, "Amendment 2026-10-03 (#660)" in the
  spec). Purely additive, like version 3 and version 5: the migration
  from version 5, 6, 7, 8 or 9 just creates the table and appends a
  version-10 row; no existing table changes. A store at version 4 or
  earlier gets it directly alongside the journal tables. Unlike every
  other fact table here, its UNIQUE key (`cik, fact_name, period_end,
  period_days`) excludes `known_at`: the table is the one deliberate
  exception to the "Definitions" revision rule — it holds the
  first-accepted vintage of each key for ever, and a later filing
  carrying the same key (an identical comparative or a differing
  restatement) is never stored, so there is no revision to key on. A
  read-only connection accepts a version-9 store without migrating it; a
  direct `statement_facts` read there fails on the missing table, as does
  anything that loops over every `TABLE_NAMES` table expecting them all
  to exist (`health.py`, `registry.store_max_ingested_at`) — loudly, and
  the next writable `init_schema` call migrates the store.
- **Version 11** (#859): retraction in the append-only master.
  `securities` and `listings` gain `retracted BOOLEAN NOT NULL DEFAULT
  FALSE`: a row with it set is a revision of its key, stamped at the
  correcting run, that withdraws the key from its own `known_at` on, as
  `corporate_actions.cancelled` withdraws an action (#108); an as-of read
  before it still sees the old row. Also the non-fact `master_underived`
  table (each master check's stored rows the current rules no longer
  derive; `health`'s `underived_master_rows`). `_TABLE_DDL` stays pinned
  at its version-4 shape, so after the DDL pass `init_schema` rebuilds the
  two tables with the version-11 DDL (`_migrate_retracted`) on every
  store without the column, a fresh one included, every row kept in
  insertion order with `retracted = FALSE`. A read-only connection accepts
  a version-10 (or older) store: it has no retraction, so the master reads
  (`has_retracted`) take every row as live, as before; `paper start` reads
  the master before its write connection migrates.
- **Version 12** (#926, research-registry plan T80; spec req 1): the five
  research-registry tables (`RESEARCH_TABLE_NAMES`: registrations, dataset
  versions, runs, results, decisions) and `trial_results.n_research
  INTEGER` (nullable, req 9). Purely additive, like version 3 and version
  10: the migration from version 11 (or any earlier migratable version,
  after its own steps) creates the tables, adds the column by `ALTER TABLE`
  so the pinned `_REGISTRY_TABLE_DDL` never changes (NULL on every existing
  row; a fresh store gets the column the same way), and appends a
  version-12 row; no fact, journal or other registry table changes. The
  research tables are neither fact nor registry nor journal tables (see
  below) and stay out of `TABLE_NAMES`, so the look-ahead harness never
  sees them. Any writing `init_schema` migrates, so the owner's store takes
  version 12 at its first writing job after this version is pulled (the
  research-registry plan, "Schema version"). A read-only connection accepts
  a version-11 store without migrating it: every non-research read keeps
  working, a research read raises `ResearchNotInitialised`
  (`require_research`, which the research readers call first) and a
  `trial_results.n_research` read fails on the missing column.
- **Version 13** (#720, #1033, T85d; profitability amendment #1033 B3): the
  six `PROFITABILITY_REBALANCE_COLUMNS` on `trial_rebalances` — `n_ranked`,
  `n_excluded_no_facts`, `n_excluded_stale_facts`, `n_excluded_sector`,
  `n_excluded_malformed` and `n_derived` (each nullable `INTEGER`), the
  `profitability` family's per-rebalance ranking and exclusion counts.
  Purely additive, like version 12: the migration from version 12 (or any
  earlier migratable version, after its own steps) adds the six columns by
  `ALTER TABLE` so the pinned `_REGISTRY_TABLE_DDL` never changes (NULL on
  every existing row; a fresh store gets the columns the same way), and
  appends a version-13 row; no fact, journal, research or other registry
  table changes. `store.registry.write_rebalances` can write the six counts
  for a `profitability` trial's rows and leaves them NULL for a `momentum`
  one (`store.registry.RebalanceRow`'s six new fields are optional,
  defaulting to `None`; the engine itself does not fill them yet — T85e).
  Any writing `init_schema` migrates, so the owner's
  store takes version 13 at its first writing job after this version is
  pulled. A read-only connection accepts a version-12 store without
  migrating it: every other read keeps working; a read of the six columns
  there fails on the missing columns, same as `n_research` on a
  version-11 store.
- **A later DDL change goes to version 14**, with its own migration and a
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

Research-registry tables (research-registry spec "Data / interfaces" >
Tables; version 12) are not fact tables either: every row carries
`known_at TIMESTAMPTZ NOT NULL` (the write's clock: the registration, the
run's open, the result's close, the decision) and no `ingested_at`,
`source` or `provenance`, and they stay out of `TABLE_NAMES`,
`REGISTRY_TABLE_NAMES` and `JOURNAL_TABLE_NAMES`. Append-only by contract
(`store.research` exposes inserts and reads only). Schema-level backstops:
a primary key on each table's own id (on `research_results`, the run id,
so a run has one result), a `CHECK` on every column the spec enumerates
(`RESEARCH_ENUMS` and `RESEARCH_STAGES`), `verdict` and a positive
`n_configurations` on exactly the `ok` results, a positive
`n_configurations_declared`, and req 2's `return` implies `touches_returns`
implies a `family`. No foreign keys; ids come from `store.research`.

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
    "statement_facts": ("filing",),
}

#: `statement_facts.basis` (#660): `reported` for an as-filed value,
#: `derived` for a `gross_profit` row the ingest computed from the stored
#: `revenue` and `cost_of_revenue` rows because the filing carried no
#: `GrossProfit` tag. A closed set, like `ORDER_EVENT_REASONS` and
#: `DECISION_REASONS` below, enforced by this table's own `CHECK` rather
#: than deferred to `health --check` (T77c).
STATEMENT_FACT_BASIS_VALUES: tuple[str, ...] = ("reported", "derived")

#: The schema version `init_schema` records on a fresh store and migrates
#: a version-2 to 11 store to. Bump and add a migration note (not
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
#: - 9 (#571, spec req 17): `owner_settled_unknown` joins `ORDER_EVENT_REASONS`,
#:   `settle_order` joins `overrides.kind`, and `overrides.client_order_id`
#:   (nullable, set exactly for `settle_order`). Migration from version 8 (or 5,
#:   6 or 7, after their steps): `order_events` and `overrides` are rebuilt in
#:   one transaction with every row kept, `client_order_id = NULL`.
#: - 10 (#660, T76; renumbered from 9 at ready time, T84/#714 landed version 9
#:   first): the `statement_facts` table (as-filed revenue, cost of revenue,
#:   gross profit, total assets and operating cash flow; the 2026-10-03
#:   amendment). Purely additive: the table is new, so the migration from
#:   version 5, 6, 7, 8 or 9 is just creating it and appending a version-10
#:   row; no existing table changes. Unlike every other fact table, its
#:   UNIQUE key (`cik, fact_name, period_end, period_days`) excludes
#:   `known_at`: the table holds one vintage per period for ever, never a
#:   revision (module docstring's "Schema versions" exception to the
#:   "Definitions" revision rule).
#: - 11 (#859): `securities` and `listings` gain `retracted BOOLEAN NOT NULL
#:   DEFAULT FALSE` (a retraction is a revision with it set, as
#:   `corporate_actions.cancelled`), and the non-fact `master_underived`
#:   table. `_TABLE_DDL` keeps its version-4 pin, so every store, a fresh one
#:   included, has the two tables rebuilt after the DDL pass with every row
#:   kept and `retracted = FALSE`.
#: - 12 (#926, research-registry plan T80): the five research-registry tables
#:   (`RESEARCH_TABLE_NAMES`) and `trial_results.n_research INTEGER`
#:   (nullable). Additive: the migration from version 11 (or any earlier
#:   migratable version, after its steps) creates the tables, adds the column
#:   by `ALTER TABLE` (NULL on every existing row) and appends a version-12
#:   row; no fact, journal or other registry table changes.
#: - 13 (#720, #1033, T85d): `trial_rebalances` gains the six
#:   `PROFITABILITY_REBALANCE_COLUMNS` (nullable `INTEGER`): the
#:   `profitability` family's per-rebalance `n_ranked` and its five
#:   `n_excluded_*`/`n_derived` exclusion counts. Additive, like version 12:
#:   the migration from version 12 (or any earlier migratable version, after
#:   its steps) adds the six columns by `ALTER TABLE` (NULL on every existing
#:   row, and on every `momentum` row going forward) and appends a
#:   version-13 row; no fact, journal, research or other registry table
#:   changes.
#: - A later DDL change goes to version 14, with its own migration and a
#:   note here, never a silent edit of the DDL below.
CURRENT_SCHEMA_VERSION = 13

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

#: The last version without `owner_settled_unknown`, `settle_order` and
#: `overrides.client_order_id` (#571): read-only connections serve every read
#: but `overrides`.
_PRE_SETTLE_ORDER_VERSION = 8

#: The last version without `statement_facts` (#660, T76; renumbered from 8,
#: T84/#714 landed version 9 first): read-only connections serve every
#: other read; a `statement_facts` read there fails on the missing table,
#: same as a journal table on a pre-journal store.
_PRE_STATEMENT_FACTS_VERSION = 9

#: The last version without `retracted` and `master_underived` (#859):
#: read-only connections serve every read; the master reads find no
#: `retracted` column (`has_retracted`) and take every row as live.
_PRE_RETRACTION_VERSION = 10

#: The last version without the research registry and `trial_results
#: .n_research` (#926, T80): read-only connections serve every other read; a
#: research read there raises `ResearchNotInitialised` (`require_research`).
_PRE_RESEARCH_VERSION = 11

#: The last version without `PROFITABILITY_REBALANCE_COLUMNS` on
#: `trial_rebalances` (#720, #1033, T85d): read-only connections serve every
#: other read; a read of the six columns there fails on the missing columns.
_PRE_PROFITABILITY_VERSION = 12


class SchemaVersionError(RuntimeError):
    """The store's `schema_version` table records a version this code has
    no migration from — it does not know that shape and refuses to
    operate on it rather than guessing."""


class RegistryNotInitialised(RuntimeError):
    """A read-only connection opened a store without the trial registry
    (a version-2 store, or one never initialised). Read-only
    connections never migrate; any writing command's `init_schema` call
    does."""


class ResearchNotInitialised(RuntimeError):
    """The store has no research-registry tables: a store before version 12
    that only a read-only connection has opened (read-only connections never
    migrate).
    Raised by the research readers (`require_research`), never by
    `init_schema`, so every other read on such a store keeps working; any
    writing command's `init_schema` migrates it."""


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

# As-filed statement facts (amendment 2026-10-03, #660; spec "Data /
# interfaces" > Amendment 2026-10-03). Keyed by **cik**, never
# security_id: a statement is the issuer's, not a share class's (ADR 0003
# rule 3 is kept -- the key is the master's own cik column, never a
# ticker). The UNIQUE constraint deliberately excludes known_at (unlike
# every other fact table above): the table holds the first-accepted
# vintage of each (cik, fact_name, period_end, period_days) key for
# ever, and a later filing carrying the same key -- an identical
# comparative column or a differing restatement -- is never stored, so
# there is no revision to key on. period_start is NULL exactly when
# period_days = 0 (an instant fact, e.g. total_assets); the CHECK below
# enforces that at the schema level rather than deferring it to `health
# --check` (T77c also restates it there for a store's own confidence).
# basis is restricted to STATEMENT_FACT_BASIS_VALUES. filing_accession is
# NOT NULL (unlike facts.filing_accession above): a statement fact always
# comes from a filing, never a snapshot.
_CREATE_STATEMENT_FACTS = f"""
CREATE TABLE IF NOT EXISTS statement_facts (
    cik VARCHAR NOT NULL,
    fact_name VARCHAR NOT NULL,
    xbrl_tag VARCHAR NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    period_days INTEGER NOT NULL,
    value DOUBLE NOT NULL,
    unit VARCHAR NOT NULL,
    form VARCHAR NOT NULL,
    filing_accession VARCHAR NOT NULL,
    basis VARCHAR NOT NULL,
    comparative BOOLEAN NOT NULL,
    {_common_fact_columns(TABLE_PROVENANCE_VALUES["statement_facts"])},
    CHECK (basis IN ({", ".join(f"'{value}'" for value in STATEMENT_FACT_BASIS_VALUES)})),
    CHECK (
        (period_days = 0 AND period_start IS NULL)
        OR (period_days != 0 AND period_start IS NOT NULL)
    ),
    UNIQUE (cik, fact_name, period_end, period_days)
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
    "statement_facts",
    "ingestion_runs",
    "schema_version",
)

#: Frozen at version 4 (#108's `corporate_actions` on top of #83's fact
#: tables) and pinned by hash in `tests/store/test_journal_schema.py` and
#: `tests/store/test_registry_schema.py` -- quant-auditor review of #660/T76
#: (PR #729): a *new* fact table must never be folded into this tuple, since
#: the pin exists precisely so this blob never has to change again; it gets
#: its own tuple and its own pin instead (`_STATEMENT_FACTS_TABLE_DDL` below,
#: added to version 10).
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

#: `statement_facts` (#660, T76), added at version 10: its own tuple rather
#: than folded into `_TABLE_DDL` above, so that tuple's version-4 pin never
#: has to move again (quant-auditor review of PR #729). Pinned by hash in
#: `tests/store/test_schema.py`; a later edit of this table's DDL goes to
#: the next schema version with its own migration, exactly as `_TABLE_DDL`
#: itself is guarded.
_STATEMENT_FACTS_TABLE_DDL: tuple[str, ...] = (_CREATE_STATEMENT_FACTS,)

#: The master tables a retraction can withdraw a row from (#859): each gains
#: `retracted` at version 11.
RETRACTABLE_TABLES: tuple[str, ...] = ("securities", "listings")

#: The version-11 `securities` and `listings` (#859): the version-4 shape plus
#: `retracted`, as #108 gave `corporate_actions` its `cancelled`. A row with
#: `retracted = TRUE` is a revision that withdraws its key from its own
#: `known_at` on. `_TABLE_DDL` keeps the version-4 shape (its pin), so
#: `init_schema` rebuilds the two tables into these after its DDL pass.
_RETRACTION_TABLE_DDL: dict[str, str] = {
    table: ddl.replace(
        "    UNIQUE (",
        "    retracted BOOLEAN NOT NULL DEFAULT FALSE,\n    UNIQUE (",
        1,
    )
    for table, ddl in (("securities", _CREATE_SECURITIES), ("listings", _CREATE_LISTINGS))
}


def has_retracted(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Whether `table` has the version-11 `retracted` column. A store a
    read-only connection opened below version 11 lacks it, and holds no
    retraction, so its reads take every row as live (#859)."""
    found = conn.execute(
        "SELECT count(*) FROM duckdb_columns() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = ? AND column_name = 'retracted'",
        [table],
    ).fetchone()
    return found is not None and found[0] > 0


#: `master_underived` (#859, version 11): per master check (an EDGAR ingest
#: chunk or a `master-retract` run), each stored `securities` or `listings`
#: row the current rules no longer derive. Not a fact table: like
#: `ingestion_runs` it records a job's finding, has no `known_at` of its own,
#: and no as-of read path touches it; `health` reads it for
#: `underived_master_rows`. `run_id` is the check's `ingestion_runs` row,
#: `recorded_at` the check's clock (tz-aware UTC), `known_at` the stored
#: row's. `ticker`, `exchange` and `valid_from` are NULL for a `securities`
#: row.
_CREATE_MASTER_UNDERIVED = """
CREATE TABLE IF NOT EXISTS master_underived (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    table_name VARCHAR NOT NULL,
    security_id VARCHAR NOT NULL,
    ticker VARCHAR,
    exchange VARCHAR,
    valid_from DATE,
    known_at TIMESTAMPTZ NOT NULL,
    CHECK (table_name IN ('securities', 'listings')),
    CHECK ((table_name = 'listings') = (valid_from IS NOT NULL))
)
"""

#: Tables new at version 11 (#859), created by `init_schema`'s DDL pass. Kept
#: out of `TABLE_NAMES` (not a fact table; the look-ahead harness truncates
#: only those).
MASTER_CHECK_TABLE_NAMES: tuple[str, ...] = ("master_underived",)
_MASTER_UNDERIVED_TABLE_DDL: tuple[str, ...] = (_CREATE_MASTER_UNDERIVED,)


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
#: `order_events.reason` of the terminal `cancelled` event `paper settle` journals
#: for an order the owner settles without a fill (spec req 17, #571).
OWNER_SETTLED_UNKNOWN_REASON = "owner_settled_unknown"
#: Every `order_events.reason` the spec names (#332, #571). `plan.decision_state`
#: keeps a buy open on these exact spellings, so the column is a closed set.
ORDER_EVENT_REASONS: tuple[str, ...] = (
    HALT_REASON,
    NOT_RECEIVED_REASON,
    OWNER_SETTLED_UNKNOWN_REASON,
)

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
#: `overrides.kind` of an owner settlement of one order (`paper settle`, spec req
#: 17, #571): the only kind that carries a `client_order_id`.
SETTLE_ORDER_KIND = "settle_order"
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
    ("overrides", "kind"): (
        EXCLUDE_NAME_REASON,
        KEEP_NAME_REASON,
        ENGAGE_KILL_SWITCH_KIND,
        SETTLE_ORDER_KIND,
    ),
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
    client_order_id VARCHAR,
    kind VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("overrides", "kind")},
    CHECK (length(trim(reason)) >= 1),
    CHECK ((kind = '{SETTLE_ORDER_KIND}') = (client_order_id IS NOT NULL))
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


# Research-experiment registry (schema version 12; research-registry spec req 1
# and "Data / interfaces" > Tables, plan T80). Not fact tables: every row
# carries `known_at` (`db.utc_now()` at the write, tz-aware UTC, NOT NULL) and
# none of the other three common columns, and the tables stay out of
# `TABLE_NAMES` so the look-ahead harness never sees them. Append-only by
# contract (`store.research`, T81, exposes inserts and reads only); a run's
# outcome is its one `research_results` row (the primary key), never an update
# of `research_runs`. No foreign keys and no sequences, as for the trial
# registry: ids are `MAX + 1` inside the write transaction. Every JSON payload
# is VARCHAR (no json extension, `configure_connection`). The schema-level
# backstops are the primary keys, a `CHECK` on every column the spec
# enumerates (each set a code constant below, pinned by a test: a new value is
# a spec amendment), `verdict` and a positive `n_configurations` set on an `ok`
# result (req 8, req 9), and the two req 2 rules N depends on (`return` implies
# `touches_returns` implies a `family`). The other req 2 and req 11 refusals
# belong to the parser and `store.research`: a refused registration or dataset
# never reaches these tables.

#: `research_registrations.kind` (spec "Definitions", Kind).
RESEARCH_KINDS: tuple[str, ...] = ("agreement", "benchmark", "robustness", "economic", "return")

#: `research_registrations.stage`: the brief §2 gates 2 to 7 (stage 1 has no runs).
RESEARCH_STAGES: tuple[int, ...] = (2, 3, 4, 5, 6, 7)

#: `research_registrations.provenance` (spec "Definitions", Provenance).
RESEARCH_PROVENANCES: tuple[str, ...] = (
    "human",
    "deterministic",
    "classical",
    "model_historical",
    "model_prospective",
    "pit_model",
)

#: `research_registrations.primary_direction`.
RESEARCH_DIRECTIONS: tuple[str, ...] = ("greater", "less")

#: `research_registrations.multiplicity_method`.
RESEARCH_MULTIPLICITY_METHODS: tuple[str, ...] = ("holm", "fixed_sequence", "bh", "none")

#: `research_runs.split` (spec "Definitions", Split).
RESEARCH_SPLITS: tuple[str, ...] = ("dev", "cal", "test", "prospective", "pilot", "full", "none")

#: `research_runs.confirmatory_basis` (req 7); `none` on an exploratory run.
RESEARCH_CONFIRMATORY_BASES: tuple[str, ...] = ("predates_dataset", "sealed_split", "none")

#: `research_results.outcome` (req 8).
RESEARCH_OUTCOMES: tuple[str, ...] = (
    "ok",
    "failed",
    "refused_window",
    "refused_holdout",
    "refused_split",
    "refused_budget",
    "abandoned",
)

#: `research_results.verdict` (req 8), set exactly on an `ok` result.
RESEARCH_VERDICTS: tuple[str, ...] = ("pass", "fail", "underpowered", "n/a")

#: `research_decisions.kind` (req 2's `budget_amend`, req 5's `holdout_spend`;
#: the earlier draft's `import` was dropped with req 12, #810).
RESEARCH_DECISION_KINDS: tuple[str, ...] = ("holdout_spend", "budget_amend")

#: Every enumerated research column and its allowed values, one `CHECK` each
#: (`stage`, an integer, has its own `CHECK` over `RESEARCH_STAGES`).
RESEARCH_ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("research_registrations", "kind"): RESEARCH_KINDS,
    ("research_registrations", "provenance"): RESEARCH_PROVENANCES,
    ("research_registrations", "primary_direction"): RESEARCH_DIRECTIONS,
    ("research_registrations", "multiplicity_method"): RESEARCH_MULTIPLICITY_METHODS,
    ("research_runs", "split"): RESEARCH_SPLITS,
    ("research_runs", "confirmatory_basis"): RESEARCH_CONFIRMATORY_BASES,
    ("research_results", "outcome"): RESEARCH_OUTCOMES,
    ("research_results", "verdict"): RESEARCH_VERDICTS,
    ("research_decisions", "kind"): RESEARCH_DECISION_KINDS,
}


def _research_check(table: str, column: str, *, nullable: bool = False) -> str:
    """The `CHECK` restricting `table.column` to its `RESEARCH_ENUMS` values
    (and NULL when `nullable`)."""
    allowed = ", ".join(f"'{value}'" for value in RESEARCH_ENUMS[table, column])
    check = f"{column} IN ({allowed})"
    return f"CHECK ({column} IS NULL OR {check})" if nullable else f"CHECK ({check})"


_RESEARCH_STAGE_CHECK = f"CHECK (stage IN ({', '.join(str(s) for s in RESEARCH_STAGES)}))"

# One row per registration; an amendment is a new row pointing at the one it
# supersedes (`amends_registration_id`, NULL on the first of a chain). Two of
# req 2's refusals are backstopped here too, since N reads them: a `return`
# registration touches returns, and one that touches returns names a family. `family`
# is NULL unless the file names one (required when `touches_returns`, req 2);
# `hypothesis_ref`, `dataset_sha256_pin`, `primary_threshold` and the
# multiplicity family's id and size are optional in the file (req 2).
_CREATE_RESEARCH_REGISTRATIONS = f"""
CREATE TABLE IF NOT EXISTS research_registrations (
    registration_id BIGINT NOT NULL PRIMARY KEY,
    slug VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    stage INTEGER NOT NULL,
    title VARCHAR NOT NULL,
    confirmatory BOOLEAN NOT NULL,
    provenance VARCHAR NOT NULL,
    touches_returns BOOLEAN NOT NULL,
    family VARCHAR,
    claims_json VARCHAR NOT NULL,
    hypothesis_ref VARCHAR,
    dataset_name VARCHAR NOT NULL,
    dataset_sha256_pin VARCHAR,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    splits_json VARCHAR NOT NULL,
    primary_metric VARCHAR NOT NULL,
    primary_direction VARCHAR NOT NULL,
    primary_threshold DOUBLE,
    primary_ci_level DOUBLE NOT NULL,
    primary_min_clusters INTEGER NOT NULL,
    primary_inference VARCHAR NOT NULL,
    secondary_json VARCHAR NOT NULL,
    comparison_set VARCHAR NOT NULL,
    multiplicity_method VARCHAR NOT NULL,
    multiplicity_family_id VARCHAR,
    multiplicity_family_size INTEGER,
    budget_runs INTEGER NOT NULL,
    budget_configurations INTEGER NOT NULL,
    stop_rule VARCHAR NOT NULL,
    expected_effect VARCHAR NOT NULL,
    seed BIGINT NOT NULL,
    doc_path VARCHAR NOT NULL,
    doc_sha256 VARCHAR NOT NULL,
    params_json VARCHAR NOT NULL,
    params_sha256 VARCHAR NOT NULL,
    amends_registration_id BIGINT,
    registered_by VARCHAR NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    {_research_check("research_registrations", "kind")},
    {_RESEARCH_STAGE_CHECK},
    {_research_check("research_registrations", "provenance")},
    {_research_check("research_registrations", "primary_direction")},
    {_research_check("research_registrations", "multiplicity_method")},
    CHECK (kind <> 'return' OR touches_returns),
    CHECK (NOT touches_returns OR family IS NOT NULL)
)
"""

# One row per dataset version (req 11). `n_rows` is NULL for a non-tabular
# (directory) export; `event_column`, `split_path`, `split_sha256` and `seed`
# are NULL when not given; `split_spans_json` always holds the per-split event
# spans (`full` alone without a split file). `code_dirty` is NULL when
# `code_version` is 'unknown', as in `trials`.
_CREATE_RESEARCH_DATASETS = """
CREATE TABLE IF NOT EXISTS research_datasets (
    dataset_id BIGINT NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    n_rows BIGINT,
    event_start DATE NOT NULL,
    event_end DATE NOT NULL,
    event_column VARCHAR,
    split_path VARCHAR,
    split_sha256 VARCHAR,
    split_spans_json VARCHAR NOT NULL,
    sealed_splits_json VARCHAR NOT NULL,
    sealed_periods_json VARCHAR NOT NULL,
    locked BOOLEAN NOT NULL,
    seed BIGINT,
    code_version VARCHAR NOT NULL,
    code_dirty BOOLEAN,
    note VARCHAR,
    known_at TIMESTAMPTZ NOT NULL
)
"""

# One row per run, written at open and never updated (req 3); a refusal at
# open is a run row too, its outcome the result row. `store_max_ingested_at`
# is NULL for a run that reads no runtime-store data (or an empty store);
# `holdout_reason` is NULL unless `holdout_spent`. At least one configuration
# is declared (req 6, `--configurations` default 1).
_CREATE_RESEARCH_RUNS = f"""
CREATE TABLE IF NOT EXISTS research_runs (
    run_id BIGINT NOT NULL PRIMARY KEY,
    registration_id BIGINT NOT NULL,
    dataset_id BIGINT NOT NULL,
    dataset_sha256 VARCHAR NOT NULL,
    split VARCHAR NOT NULL,
    config_json VARCHAR NOT NULL,
    config_sha256 VARCHAR NOT NULL,
    n_configurations_declared INTEGER NOT NULL,
    confirmatory BOOLEAN NOT NULL,
    confirmatory_basis VARCHAR NOT NULL,
    code_version VARCHAR NOT NULL,
    code_dirty BOOLEAN,
    store_max_ingested_at TIMESTAMPTZ,
    synthetic BOOLEAN NOT NULL,
    holdout_spent BOOLEAN NOT NULL,
    holdout_repeat BOOLEAN NOT NULL,
    holdout_reason VARCHAR,
    run_by VARCHAR NOT NULL,
    note VARCHAR,
    known_at TIMESTAMPTZ NOT NULL,
    {_research_check("research_runs", "split")},
    {_research_check("research_runs", "confirmatory_basis")},
    CHECK (n_configurations_declared >= 1)
)
"""

# One row per run, the primary key, so a second result for a run raises
# (req 8). The statistics are NULL unless `ok`; `verdict` is set exactly on an
# `ok` row, computed by code from the interval, and an `ok` row reports at least
# one evaluated configuration (`family_run_count` sums them into N, req 9).
_CREATE_RESEARCH_RESULTS = f"""
CREATE TABLE IF NOT EXISTS research_results (
    run_id BIGINT NOT NULL PRIMARY KEY,
    outcome VARCHAR NOT NULL,
    message VARCHAR,
    primary_value DOUBLE,
    primary_ci_low DOUBLE,
    primary_ci_high DOUBLE,
    n_observations BIGINT,
    n_clusters BIGINT,
    n_configurations INTEGER,
    secondary_json VARCHAR,
    exploratory_json VARCHAR,
    verdict VARCHAR,
    artifact_sha256 VARCHAR,
    artifact_path VARCHAR,
    known_at TIMESTAMPTZ NOT NULL,
    {_research_check("research_results", "outcome")},
    {_research_check("research_results", "verdict", nullable=True)},
    CHECK ((outcome = 'ok') = (verdict IS NOT NULL)),
    CHECK (outcome <> 'ok' OR (n_configurations IS NOT NULL AND n_configurations >= 1))
)
"""

# `registration_id` and `run_id` are NULL where the decision has none (a
# `budget_amend` names a registration, a `holdout_spend` a run).
_CREATE_RESEARCH_DECISIONS = f"""
CREATE TABLE IF NOT EXISTS research_decisions (
    decision_id BIGINT NOT NULL PRIMARY KEY,
    kind VARCHAR NOT NULL,
    registration_id BIGINT,
    run_id BIGINT,
    values_json VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    made_by VARCHAR NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    {_research_check("research_decisions", "kind")}
)
"""

#: The research-registry tables added at schema version 12, disjoint from
#: `TABLE_NAMES` (the look-ahead harness never sees them), `REGISTRY_TABLE_NAMES`
#: and `JOURNAL_TABLE_NAMES`.
RESEARCH_TABLE_NAMES: tuple[str, ...] = (
    "research_registrations",
    "research_datasets",
    "research_runs",
    "research_results",
    "research_decisions",
)

_RESEARCH_TABLE_DDL: tuple[str, ...] = (
    _CREATE_RESEARCH_REGISTRATIONS,
    _CREATE_RESEARCH_DATASETS,
    _CREATE_RESEARCH_RUNS,
    _CREATE_RESEARCH_RESULTS,
    _CREATE_RESEARCH_DECISIONS,
)

#: The column version 12 adds to `trial_results` (req 9): the research share of
#: a backtest's N, NULL on every row written before the migration. Added by
#: `ALTER TABLE` after the DDL pass (`_migrate_n_research`), so the pinned
#: `_REGISTRY_TABLE_DDL` never changes.
N_RESEARCH_COLUMN = "n_research"

#: The six columns version 13 adds to `trial_rebalances` (#720, #1033, T85d):
#: the `profitability` family's per-rebalance ranking and exclusion counts,
#: NULL on every row written before the migration and on every `momentum`
#: row (that family never reads `statement_facts` or `sics`, so it never has
#: these counts). Added by `ALTER TABLE` after the DDL pass
#: (`_migrate_profitability_rebalance_counts`), so the pinned
#: `_REGISTRY_TABLE_DDL` never changes.
PROFITABILITY_REBALANCE_COLUMNS: tuple[str, ...] = (
    "n_ranked",
    "n_excluded_no_facts",
    "n_excluded_stale_facts",
    "n_excluded_sector",
    "n_excluded_malformed",
    "n_derived",
)


def require_research(conn: duckdb.DuckDBPyConnection) -> None:
    """Raise `ResearchNotInitialised` unless every `RESEARCH_TABLE_NAMES` table
    exists. The research readers (T81's `store.research`, T83c's page) call it
    before their first query."""
    (present,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name IN "
        f"({', '.join('?' for _ in RESEARCH_TABLE_NAMES)})",
        list(RESEARCH_TABLE_NAMES),
    ).fetchone()
    if present != len(RESEARCH_TABLE_NAMES):
        raise ResearchNotInitialised(
            "research registry not initialised (a research table is missing); "
            "any writing command migrates the store to the current version"
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
        _PRE_SETTLE_ORDER_VERSION,
        _PRE_STATEMENT_FACTS_VERSION,
        _PRE_RETRACTION_VERSION,
        _PRE_RESEARCH_VERSION,
        _PRE_PROFITABILITY_VERSION,
        CURRENT_SCHEMA_VERSION,
    ):
        # Version 4 serves fact and registry reads; versions 5 to 8 every journal
        # read but `overrides` (no `client_order_id` before 9), and versions 5 to 7
        # none of `resume_invocations` and `resume_acceptances` either; version 9
        # every read but `statement_facts`; version 10 every read (no
        # `retracted` column: no retraction, see `has_retracted`); version 11
        # every read but the research tables (`require_research` raises
        # `ResearchNotInitialised`) and `trial_results.n_research`; version 12
        # every read but `trial_rebalances`'s six `PROFITABILITY_REBALANCE_COLUMNS`.

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


#: Where `_migrate_settle_order` builds the version-9 tables before they take
#: their names.
_ORDER_EVENTS_V9_STAGING_TABLE = "order_events_v9"
_OVERRIDES_STAGING_TABLE = "overrides_v9"

#: The `overrides` columns before version 9 (#571), copied by name.
_PRE_SETTLE_OVERRIDE_COLUMNS: tuple[str, ...] = (
    "override_id",
    "window_id",
    "made_at",
    "rebalance_session",
    "security_id",
    "kind",
    "reason",
    "known_at",
    "ingested_at",
)


def _migrate_settle_order(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-5 to 8 `order_events` and `overrides` with the version-9
    DDL (module docstring, "Schema versions"), every row kept in insertion order,
    each `overrides` row with `client_order_id = NULL`. Both `CHECK` sets only
    grow, so no stored row can be refused. Runs inside `init_schema`'s
    transaction."""
    conn.execute(
        _CREATE_ORDER_EVENTS.replace(
            "CREATE TABLE IF NOT EXISTS order_events (",
            f"CREATE TABLE {_ORDER_EVENTS_V9_STAGING_TABLE} (",
            1,
        )
    )
    # Keep the insertion order, as `_migrate_order_event_reasons` does.
    conn.execute(
        f"INSERT INTO {_ORDER_EVENTS_V9_STAGING_TABLE} BY NAME "
        "SELECT * FROM order_events ORDER BY rowid"
    )
    conn.execute("DROP TABLE order_events")
    conn.execute(f"ALTER TABLE {_ORDER_EVENTS_V9_STAGING_TABLE} RENAME TO order_events")
    conn.execute(
        _CREATE_OVERRIDES.replace(
            "CREATE TABLE IF NOT EXISTS overrides (",
            f"CREATE TABLE {_OVERRIDES_STAGING_TABLE} (",
            1,
        )
    )
    columns = ", ".join(_PRE_SETTLE_OVERRIDE_COLUMNS)
    conn.execute(
        f"INSERT INTO {_OVERRIDES_STAGING_TABLE} ({columns}) "
        f"SELECT {columns} FROM overrides ORDER BY rowid"
    )
    conn.execute("DROP TABLE overrides")
    conn.execute(f"ALTER TABLE {_OVERRIDES_STAGING_TABLE} RENAME TO overrides")


def _migrate_retracted(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild each `RETRACTABLE_TABLES` table that lacks `retracted` with its
    version-11 DDL (module docstring, "Schema versions"), every row kept in
    insertion order with `retracted = FALSE` (no row before version 11 could
    retract anything), so every as-of read returns what it did before. A
    fresh store's tables come from `_TABLE_DDL` (the version-4 shape) and are
    rebuilt empty. Idempotent: a table that has the column is left alone.
    Runs inside `init_schema`'s transaction, after its DDL pass."""
    for table in RETRACTABLE_TABLES:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if "retracted" in columns:
            continue
        staging = f"{table}_v11"
        conn.execute(
            _RETRACTION_TABLE_DDL[table].replace(
                f"CREATE TABLE IF NOT EXISTS {table} (", f"CREATE TABLE {staging} (", 1
            )
        )
        conn.execute(
            f"INSERT INTO {staging} BY NAME "
            f"SELECT *, FALSE AS retracted FROM {table} ORDER BY rowid"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")


def _add_nullable_int_columns(
    conn: duckdb.DuckDBPyConnection, table: str, columns: tuple[str, ...]
) -> None:
    """Add each name in `columns` to `table` as a nullable `INTEGER` where
    missing, by `ALTER TABLE` (NULL on every existing row). Idempotent: a
    column already present is left alone. Shared by `_migrate_n_research`
    and `_migrate_profitability_rebalance_counts`, whose own docstrings carry
    each column's schema-version story; this helper has none of its own.
    Runs inside `init_schema`'s transaction, after its DDL pass."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
    for column in columns:
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER")


def _migrate_n_research(conn: duckdb.DuckDBPyConnection) -> None:
    """Add `trial_results.n_research INTEGER` (nullable, NULL on every existing
    row) where it is missing (module docstring, "Schema versions", version 12).
    `_REGISTRY_TABLE_DDL` keeps its version-4 pin, so a fresh store gets the
    column here too. Runs inside `init_schema`'s transaction, after its DDL
    pass."""
    _add_nullable_int_columns(conn, "trial_results", (N_RESEARCH_COLUMN,))


def _migrate_profitability_rebalance_counts(conn: duckdb.DuckDBPyConnection) -> None:
    """Add each of `PROFITABILITY_REBALANCE_COLUMNS` to `trial_rebalances`
    (nullable `INTEGER`, NULL on every existing row) where missing (module
    docstring, "Schema versions", version 13). `_REGISTRY_TABLE_DDL` keeps its
    version-4 pin, so a fresh store gets the columns here too. Runs inside
    `init_schema`'s transaction, after its DDL pass."""
    _add_nullable_int_columns(conn, "trial_rebalances", PROFITABILITY_REBALANCE_COLUMNS)


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every store table if it does not already exist, migrating a
    version-2 to 12 store to version 13.

    Idempotent: safe to call on every process start and every test. Also
    pins the connection's session timezone to UTC and disables extension
    auto-install/auto-load (`configure_connection`) so a caller that built
    its own raw connection still gets correct `TIMESTAMPTZ` round-tripping
    and no surprise network access, and drops any cached column-type info
    for `conn` (`store.db.forget_column_types`) so `store.db.insert_row`
    never reuses type info cached before these tables existed.

    On a writable connection, in one transaction (the caller's if open): a
    fresh store gets every table and one `schema_version` row for
    `CURRENT_SCHEMA_VERSION`; every store gets `trial_rebalances`'s six
    `PROFITABILITY_REBALANCE_COLUMNS` added (NULL on every existing row),
    and a version-12 store a version-13 row (#720, #1033, T85d); every store
    gets the research-registry tables created and `trial_results.n_research`
    added (NULL on every existing row), and a version-11 store a version-12
    row (#926, T80); every store gets `securities` and `listings` rebuilt
    with `retracted` (every row kept, `FALSE`) and `master_underived`
    created, and a version-10 store a version-11 row (#859); a version-9
    store gets `statement_facts` created and a version-10 row (purely
    additive, #660); a version-8 store gets that after `order_events` and
    `overrides` are rebuilt with the version-9 sets and column (every row
    kept) and a version-9 row; a version-7 store gets that after
    `resume_invocations` is rebuilt with `accept_rejections` (every row
    kept, `FALSE`) and the `resume_acceptances` table created, and
    version-8 to version-10 rows; a version-6 store gets `decisions`
    rebuilt with the reason `CHECK` (every row kept) before that, and
    version-7 to version-10 rows; a version-5 store gets `order_events`
    rebuilt likewise before that, and version-6 to version-10 rows; a
    version-4 store gets the journal tables and version-5 to version-10
    rows; a version-3 store gets that plus `corporate_actions` rebuilt with
    the version-4 columns (every row kept) and a version-4 row; a
    version-2 store gets all of that plus the registry tables and a
    version-3 row. Nothing else changes (module docstring, "Schema
    versions").

    On a read-only connection no DDL runs: a version-13, 12, 11, 10, 9, 8, 7,
    6 or 5 store passes (a version-12 store serves every read but
    `trial_rebalances`'s six `PROFITABILITY_REBALANCE_COLUMNS`; a version-11
    store serves every read but the research tables, which `require_research`
    reports as `ResearchNotInitialised`, and `trial_results.n_research`; a
    version-9 store serves every read but `statement_facts`, missing there
    as a pre-journal store's journal tables are; a version-8 store serves
    every read but `overrides`, which needs `client_order_id`), and so does
    a version-4 store (fact and registry reads work; the journal tables are
    absent, which `store.journal` reports); a version-2 or uninitialised
    store raises `RegistryNotInitialised`, and a version-3 store raises
    `SchemaVersionError`.

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
        _PRE_SETTLE_ORDER_VERSION,
        _PRE_STATEMENT_FACTS_VERSION,
        _PRE_RETRACTION_VERSION,
        _PRE_RESEARCH_VERSION,
        _PRE_PROFITABILITY_VERSION,
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
        if max_version in (
            _PRE_ORDER_EVENT_REASON_VERSION,
            _PRE_DECISION_REASON_VERSION,
            _PRE_ACCEPT_REJECTIONS_VERSION,
            _PRE_SETTLE_ORDER_VERSION,
        ):
            _migrate_settle_order(conn)
        for ddl in (
            _TABLE_DDL
            + _REGISTRY_TABLE_DDL
            + _JOURNAL_TABLE_DDL
            + _STATEMENT_FACTS_TABLE_DDL
            + _MASTER_UNDERIVED_TABLE_DDL
            + _RESEARCH_TABLE_DDL
        ):
            conn.execute(ddl)
        _migrate_retracted(conn)
        _migrate_n_research(conn)
        _migrate_profitability_rebalance_counts(conn)
        forget_column_types(conn)
        if max_version != CURRENT_SCHEMA_VERSION:
            first_new = CURRENT_SCHEMA_VERSION if max_version is None else max_version + 1
            applied_at = utc_now()
            for version in range(first_new, CURRENT_SCHEMA_VERSION + 1):
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    [version, applied_at],
                )

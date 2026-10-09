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
- **Version 14** (#1074, #1153, strategy-interface plan T127; ADR 0014
  point 3): generic rebalance counts and exclusion reasons. The
  `trial_rebalance_counts(trial_id, session, name, value)` table
  (`REBALANCE_COUNTS_TABLE_NAME`, `UNIQUE (trial_id, session, name)`), one
  row per count a rebalance's plan reports, written once per rebalance at
  the trial's base cost level, as `trial_weights` is; `trial_rebalances
  .n_excluded_no_history` becomes nullable (NULL for a family that does not
  report it, as the six `PROFITABILITY_REBALANCE_COLUMNS` are for
  `momentum`); and `signals.reason` leaves `JOURNAL_ENUMS` for its own
  `CHECK`, `selected`, `below_cut` or any `excluded_<reason>`
  (`SIGNAL_REASONS`, `EXCLUDED_REASON_PREFIX`). The migration from version
  13 (or any earlier migratable version, after its steps) is additive: it
  rebuilds `signals` with the wider `CHECK` and every row kept in insertion
  order (a journal store, versions 5 to 13; DuckDB cannot alter a `CHECK`
  in place, as for `order_events` at version 6), creates the counts table
  and fills it from each `(trial_id, session)`'s lowest-cost
  `trial_rebalances` row, one row per `REBALANCE_COUNT_COLUMNS` column that
  is not NULL, drops the NOT NULL by `ALTER TABLE` (so the pinned
  `_REGISTRY_TABLE_DDL` never changes; a fresh store loses it the same
  way), and appends a version-14 row, all in `init_schema`'s one
  transaction, so a failed step leaves the store at its previous version.
  The fixed count columns are still written, from the same counts by name
  (`store.registry.write_rebalances`), until a later issue retires them. A
  read-only connection accepts a version-13 store without migrating it:
  every read but `trial_rebalance_counts` works.
- **Version 15** (#1179, strategy-lab plan T97, version "P"; strategy-lab
  spec reqs 8 and 9, Definitions "Vintage" and "Detail level"): the
  period keys. `trials` gains `detail_level VARCHAR` (`full` on every
  existing row), `data_vintage TIMESTAMPTZ` and `code_tree_sha256 VARCHAR`
  (NULL on every existing row), and `trial_results` gains `sharpe_unit
  VARCHAR` (NULL on every existing row, read as `monthly`; `annual` on
  every row written from now on), all by `ALTER TABLE` after the DDL pass,
  so the pinned `_REGISTRY_TABLE_DDL` never changes and a fresh store gets
  them the same way. The migration from version 14 (or any earlier
  migratable version, after its steps) also inserts into `trial_metrics`,
  for every existing trial's rows, each `*_monthly` row copied under its
  `*_period` name, `n_periods` from `n_months`, `periods_per_year = 12`,
  `turnover_annual = 12 * turnover_monthly` and `sharpe_annual_excess_spy =
  sqrt(12) * sharpe_monthly_excess_spy` (NULL stays NULL, as for SPY): every
  pre-version-15 trial is `month_end`. The `*_monthly` and `n_months` rows
  stay (append-only) and nothing reads them afterwards. No other table
  changes. A read-only connection accepts a version-14 store without
  migrating it: every read but the four columns and the period rows works.
- **Version 16** (#1195, strategy-lab plan T113, version "L"; strategy-lab
  spec "Data / interfaces" > Tables, Definitions "Frozen-key defaults" and
  "Family rules"): the lab migration. A store migrating from version 15 (or
  any earlier migratable version, after its steps) gets
  `lab_schema.apply_lab_schema` (the eight `LAB_TABLE_NAMES` tables, and
  `trial_results.status` and `owner_decisions.kind` rebuilt with the lab's
  values, every row kept byte-identical), then, from the `hypotheses` rows
  that exist at that moment: one `pre_lab_hypotheses` row for **every** one
  (whatever keys its `params` hold), one `hypothesis_fingerprints` row for
  every one (`frozen.fingerprint` over `frozen.frozen_values`; duplicates
  kept, `lab_registry.fingerprint_registered` returns the earliest), and one
  `family_rules` row per family from its earliest (lowest id) hypothesis:
  its window, every frozen value under `FORBIDDEN_AXIS_PREFIXES`, the live
  caps, `min_sharpe_variance_annual` and `axis_lattice` from `Settings`,
  `FAMILY_PARENTS`' parent and a NULL `sr_star_seed_annual`. No hypothesis
  is registered and no other table changes. A **fresh** store is created at
  version 16 **without** the lab tables (strategy-lab plan choice 2: a
  store without them keeps the Phase 3 rules; the `lab_store` test fixture
  applies them), so `is_lab_initialised` stays the test, never the version.
  `REGISTRY_TABLE_NAMES` lists the lab tables after the eight Phase 3 ones.
  A read-only connection accepts a version-15 store without migrating it:
  every read but the lab tables works (`lab_schema.require_lab` raises
  `LabNotInitialised`).
- **Version 17** (#1258, ADR 0015 seams 1 to 3, plan T132): the expansion
  seams. `book_id VARCHAR NOT NULL DEFAULT 'main'` (`DEFAULT_BOOK_ID`, the
  one book every row written before T133 belongs to) on `paper_windows`,
  `decisions`, `orders`, `positions_daily`, `lots`, `disposals`,
  `adjustments` and `reconciliations`; `position_side VARCHAR NOT NULL
  DEFAULT 'long'` (`POSITION_SIDES`, a `CHECK` like every other closed set)
  on `decisions`, `orders`, `positions_daily`, `lots` and `disposals`; and,
  on `orders`, the read-side shape seam 3 reserves (`order_type`,
  `time_in_force`, `limit_price`, `stop_price`, `asset_class`, `order_class`,
  `multiplier`, `parent_order_id`; the five defaults in `ORDER_SHAPE_DEFAULTS`
  the DDL is generated from, and no `CHECK` on them: T135b's refusal is the
  guard). DuckDB cannot add a NOT NULL column in place, so the migration
  `_migrate_expansion_seams`, run by `init_schema` after `_migrate_lab`,
  rebuilds each of the eight tables that lacks `book_id` with the version-17
  DDL, every row kept in insertion order with the new columns at their
  defaults; a table another migration already rebuilt from the current
  `_CREATE_*` DDL (a version-6 `decisions`) is left alone, as
  `_migrate_retracted` tests. A read-only connection accepts a version-16
  store for fact, registry and lab reads, but a journal read raises
  `SchemaVersionError` (`store.journal.require_journal`, naming the fix:
  open it for writing once): `journal._select` selects every row-type field,
  so every read of the eight tables, the ops page and `open_window` included,
  would otherwise fail with a binder error.
- **Version 18** (#1319, data-foundation plan T140b; ADR 0016 point 6): named
  data releases and the development boundary. `owner_decisions.kind` gains
  `data_release` and `development_boundary` (`lab_schema.RELEASE_DECISION_KINDS`,
  also the tail of `LAB_DECISION_KINDS`), the `CHECK` rebuilt by
  `lab_schema.widen_enum` as the lab migration rebuilt it (every row kept in
  insertion order, byte-identical, every kind kept), and `trials` gains a
  nullable `development_boundary DATE` by `ALTER TABLE` (NULL on every existing
  row). `_migrate_release_kinds` runs on every fresh or migrating writable
  store (a fresh one too, since `_REGISTRY_TABLE_DDL` keeps its version-4
  pin), never on one already at 18; it is idempotent. No fact, journal,
  research or lab table changes, and nothing writes the `development_boundary`
  kind or column until T142b. A read-only connection accepts a version-17
  store: every read works but the new column, which nothing reads before
  T142b.
- **Version 19** (#1370, paper-trading plan T154; ADR 0017 B.6): `alerts`
  gains `book_id VARCHAR NOT NULL DEFAULT 'main'` (`DEFAULT_BOOK_ID`), so the
  session-scoped kinds (`no_window`, `locked`) dedupe per (book, kind,
  session) and a page can filter by book. Additive: DuckDB cannot add a NOT
  NULL column in place, so `_migrate_alert_books`, run by `init_schema` after
  `_migrate_release_kinds` on a migrating writable store, rebuilds `alerts`
  with the version-19 DDL where it lacks `book_id`, every row kept in
  insertion order with `book_id = 'main'` (every alert before version 19 is
  book `main`'s, the one book there was) and every other column unchanged.
  No other table changes; `paper_windows` and the seven other expanded tables
  already carry `book_id` (version 17). A read-only connection accepts a
  version-18 store: every read works but `store.journal.alerts_for`, which
  only the writing `execution.alerts.Alerter` calls, and a writer migrates
  first. T157 takes version 20, chained after this.
- **Version 20** (#1388, paper-trading plan T157; ADR 0017 part E): the two
  shakedown decision kinds. `owner_decisions.kind` gains `shakedown_span` and
  `shakedown_note` (`lab_schema.SHAKEDOWN_DECISION_KINDS`, also the tail of
  `LAB_DECISION_KINDS`), the `CHECK` rebuilt by `lab_schema.widen_enum` as
  version 18 rebuilt it (every row kept in insertion order, byte-identical,
  every kind kept). `_migrate_shakedown_kinds` runs on every fresh or
  migrating writable store, never on one already at 20; it is idempotent. No
  other table changes: no journal row (H1's open window on book `main`
  included) is touched. A read-only connection accepts a version-19 store:
  every read works (a shakedown row cannot exist there, so
  `registry.shakedown_span` reads None).
- **A later DDL change goes to version 21**, with its own migration and a
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
from typing import Final, TypedDict

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
#: - 14 (#1074, #1153, T127): the `trial_rebalance_counts` table,
#:   `trial_rebalances.n_excluded_no_history` nullable, and `signals.reason`'s
#:   own prefix `CHECK`. Additive: the migration from version 13 (or any
#:   earlier migratable version, after its steps) rebuilds `signals` with
#:   every row kept, fills the counts table from the fixed count columns and
#:   appends a version-14 row (module docstring, "Schema versions").
#: - 15 (#1179, strategy-lab plan T97, version "P"): `trials.detail_level`,
#:   `trials.data_vintage`, `trials.code_tree_sha256` and
#:   `trial_results.sharpe_unit`, and the period-key rows inserted for every
#:   existing trial (module docstring, "Schema versions").
#: - 16 (#1195, strategy-lab plan T113, version "L"): the lab migration:
#:   the lab tables and the two widened enumerations
#:   (`lab_schema.apply_lab_schema`) and the `pre_lab_hypotheses`,
#:   `hypothesis_fingerprints` and `family_rules` rows for every existing
#:   hypothesis and family, on a migrating store only (module docstring,
#:   "Schema versions").
#: - 17 (#1258, ADR 0015 seams 1 to 3, plan T132): `book_id` on the eight
#:   journal tables, `position_side` on five of them and the `orders` shape
#:   columns, all defaulted. Migration from version 16 (or any earlier
#:   migratable version, after its steps): every one of the eight tables
#:   that lacks `book_id` is rebuilt with the version-17 DDL, every row kept
#:   in insertion order with the new columns at their defaults
#:   (`_migrate_expansion_seams`), after `_migrate_lab` (module docstring,
#:   "Schema versions").
#: - 18 (#1319, data-foundation plan T140b): `owner_decisions.kind` widened with
#:   `data_release` and `development_boundary`, every row kept, and the nullable
#:   `trials.development_boundary` (`_migrate_release_kinds`, on every store;
#:   module docstring, "Schema versions").
#: - 19 (#1370, paper-trading plan T154; ADR 0017 B.6): `alerts.book_id`,
#:   defaulted `main`; `alerts` rebuilt with every row kept where it lacks the
#:   column (`_migrate_alert_books`; module docstring, "Schema versions").
#: - 20 (#1388, paper-trading plan T157; ADR 0017 part E): `owner_decisions.kind`
#:   widened with `shakedown_span` and `shakedown_note`, every row kept
#:   (`_migrate_shakedown_kinds`, on every store; module docstring, "Schema
#:   versions").
#: - A later DDL change goes to version 21, with its own migration and a
#:   note here, never a silent edit of the DDL below.
CURRENT_SCHEMA_VERSION = 20

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

#: The last version without `trial_rebalance_counts`, with a NOT NULL
#: `trial_rebalances.n_excluded_no_history` and with `signals.reason` in
#: `JOURNAL_ENUMS` (#1153, T127): read-only connections serve every read but
#: `trial_rebalance_counts`.
_PRE_REBALANCE_COUNTS_VERSION = 13

#: The last version without the period keys, the vintage columns and
#: `trial_results.sharpe_unit` (#1179, T97): read-only connections serve every
#: read but those columns and the `*_period` metric rows.
_PRE_PERIOD_KEYS_VERSION = 14

#: The last version without the strategy-lab tables (#1195, T113; version
#: "P"): read-only connections serve every read but the lab tables.
_PRE_LAB_VERSION = 15

#: The last version without the ADR 0015 expansion-seam columns (#1258, T132,
#: version 17): read-only connections serve fact, registry and lab reads, but
#: a journal read raises `SchemaVersionError` (`store.journal.require_journal`),
#: since the eight expanded tables lack `book_id`.
_PRE_EXPANSION_SEAMS_VERSION = 16

#: The last version without the release decision kinds and
#: `trials.development_boundary` (#1319, T140b, version 18): read-only
#: connections serve every read (nothing reads the new column before T142b).
_PRE_RELEASE_VERSION = 17

#: The last version without `alerts.book_id` (#1370, T154, version 19):
#: read-only connections serve every read but `store.journal.alerts_for`
#: (which only the writing `Alerter` calls; a writer migrates first).
_PRE_ALERT_BOOKS_VERSION = 18

#: The last version without the shakedown decision kinds (#1388, T157, version
#: 20): read-only connections serve every read (no shakedown row can exist).
_PRE_SHAKEDOWN_VERSION = 19


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

#: The strategy-lab tables (`lab_schema.LAB_TABLE_NAMES`, which imports this
#: module, so the names are repeated here; a test pins the equality). Created
#: by the version-16 migration on a migrating store only, never on a fresh one
#: (module docstring, "Schema versions").
_LAB_TABLE_NAMES: tuple[str, ...] = (
    "family_rules",
    "sweeps",
    "sweep_variants",
    "hypothesis_fingerprints",
    "pre_lab_hypotheses",
    "sweep_runs",
    "sweep_trials",
    "store_markers",
)

#: The trial-registry tables: the eight added at schema version 3, then the
#: lab tables (version 16, strategy-lab spec "Data / interfaces" > Tables),
#: disjoint from `TABLE_NAMES` so the look-ahead harness never sees them. A
#: fresh store has the first eight only (no lab tables, plan choice 2).
REGISTRY_TABLE_NAMES: tuple[str, ...] = (
    "hypotheses",
    "trials",
    "trial_results",
    "trial_metrics",
    "trial_rebalances",
    "trial_equity",
    "trial_weights",
    "owner_decisions",
    *_LAB_TABLE_NAMES,
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

#: The generic per-rebalance counts table (version 14, #1153, T127; ADR 0014
#: point 3): one row per count name a rebalance's plan reports, written once
#: per rebalance at the trial's base cost level (a plan's counts are the same
#: at every level, as `trial_weights` assumes). A registry table by role, kept
#: out of the version-3 `REGISTRY_TABLE_NAMES` and `_REGISTRY_TABLE_DDL`,
#: whose pin never moves, as `statement_facts` is kept out of `_TABLE_DDL`.
REBALANCE_COUNTS_TABLE_NAME = "trial_rebalance_counts"

_CREATE_TRIAL_REBALANCE_COUNTS = """
CREATE TABLE IF NOT EXISTS trial_rebalance_counts (
    trial_id BIGINT NOT NULL,
    session DATE NOT NULL,
    name VARCHAR NOT NULL,
    value INTEGER NOT NULL,
    UNIQUE (trial_id, session, name)
)
"""

_REBALANCE_COUNTS_TABLE_DDL: tuple[str, ...] = (_CREATE_TRIAL_REBALANCE_COUNTS,)


# Paper-trading journal (schema version 5; Phase 4 spec "Data / interfaces"
# > Tables and module docstring). Every JSON payload is VARCHAR, as in the
# registry. Money and quantities are DOUBLE (fractional shares).
_JOURNAL_TIMESTAMPS = """
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    CHECK (known_at <= ingested_at)
"""


SIDES: tuple[str, ...] = ("buy", "sell")

#: The one book every journal row written before T133 belongs to (ADR 0015
#: seam 1, plan T132): the `book_id` columns' default and the migration's
#: backfill. T133 makes every writer pass the window's own book, so the
#: default is dead after it.
DEFAULT_BOOK_ID = "main"

#: The position a lot, disposal, position mark, decision or order closes or
#: opens (ADR 0015 seam 2, plan T132). `SIDES` stays `buy`/`sell`, so an
#: order's intent is the pair (`side`, `position_side`); every row written
#: before the shorting ADR is `LONG`.
POSITION_SIDES: tuple[str, ...] = ("long", "short")
LONG = "long"


class OrderShapeDefaults(TypedDict):
    """The five `orders` shape columns ADR 0015 seam 3 reserves, each with the
    default its DDL column and its journal row-type field take (plan T132). The
    DDL is generated from this mapping, so the two can never drift."""

    order_type: str
    time_in_force: str
    asset_class: str
    order_class: str
    multiplier: int


#: `orders`' five defaulted shape columns (ADR 0015 seam 3, plan T132):
#: `limit_price`, `stop_price` and `parent_order_id` are nullable and take
#: no default, and none of the eight carries a `CHECK` (T135b's refusal is
#: the guard; a `CHECK` would cost a rebuild when the first other kind is
#: allowed).
ORDER_SHAPE_DEFAULTS: OrderShapeDefaults = {
    "order_type": "market",
    "time_in_force": "day",
    "asset_class": "us_equity",
    "order_class": "simple",
    "multiplier": 1,
}

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
    ("decisions", "side"): SIDES,
    ("decisions", "position_side"): POSITION_SIDES,
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
    ("orders", "position_side"): POSITION_SIDES,
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
    ("positions_daily", "position_side"): POSITION_SIDES,
    ("lots", "position_side"): POSITION_SIDES,
    ("disposals", "position_side"): POSITION_SIDES,
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
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
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

#: `signals.reason`'s closed part (version 14, #1153, T127). Any other reason
#: is an exclusion, `EXCLUDED_REASON_PREFIX` plus a reason the family's signal
#: declares (`excluded_no_history` for `momentum`): the database enforces the
#: prefix only, and the paper planner checks the suffix against the family
#: (ADR 0014 point 3), so a new family's reason needs no `store/` edit. Not in
#: `JOURNAL_ENUMS`, which holds closed sets only.
SIGNAL_REASONS: tuple[str, ...] = ("selected", "below_cut")
EXCLUDED_REASON_PREFIX = "excluded_"

_SIGNAL_REASONS_SQL = ", ".join(f"'{reason}'" for reason in SIGNAL_REASONS)
_SIGNALS_REASON_CHECK = (
    f"CHECK (reason IN ({_SIGNAL_REASONS_SQL}) OR starts_with(reason, '{EXCLUDED_REASON_PREFIX}'))"
)

# score and rank are NULL for an excluded name.
_CREATE_SIGNALS = f"""
CREATE TABLE IF NOT EXISTS signals (
    run_id BIGINT NOT NULL,
    rebalance_session DATE NOT NULL,
    security_id VARCHAR NOT NULL,
    score DOUBLE,
    rank INTEGER,
    reason VARCHAR NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_SIGNALS_REASON_CHECK}
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
    position_side VARCHAR NOT NULL DEFAULT '{LONG}',
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
    {_JOURNAL_TIMESTAMPS},
    {_check("decisions", "side")},
    {_check("decisions", "position_side")},
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
    position_side VARCHAR NOT NULL DEFAULT '{LONG}',
    order_type VARCHAR NOT NULL DEFAULT '{ORDER_SHAPE_DEFAULTS["order_type"]}',
    time_in_force VARCHAR NOT NULL DEFAULT '{ORDER_SHAPE_DEFAULTS["time_in_force"]}',
    limit_price DOUBLE,
    stop_price DOUBLE,
    asset_class VARCHAR NOT NULL DEFAULT '{ORDER_SHAPE_DEFAULTS["asset_class"]}',
    order_class VARCHAR NOT NULL DEFAULT '{ORDER_SHAPE_DEFAULTS["order_class"]}',
    multiplier DOUBLE NOT NULL DEFAULT {ORDER_SHAPE_DEFAULTS["multiplier"]},
    parent_order_id VARCHAR,
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
    sells_in_flight_at_submit BOOLEAN NOT NULL,
    {_JOURNAL_TIMESTAMPS},
    {_check("orders", "phase")},
    {_check("orders", "side")},
    {_check("orders", "position_side")}
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
    position_side VARCHAR NOT NULL DEFAULT '{LONG}',
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
    {_JOURNAL_TIMESTAMPS},
    {_check("positions_daily", "position_side")},
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
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
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
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
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
# one on a non-session day): always set, since alerts dedupe on it. book_id
# (version 19, ADR 0017 B.6): the book whose run or entry raised it, so the
# session-scoped kinds dedupe per (book, kind, session).
_CREATE_ALERTS = f"""
CREATE TABLE IF NOT EXISTS alerts (
    alert_id BIGINT NOT NULL PRIMARY KEY,
    run_id BIGINT,
    session DATE NOT NULL,
    kind VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
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
    position_side VARCHAR NOT NULL DEFAULT '{LONG}',
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
    {_JOURNAL_TIMESTAMPS},
    {_check("lots", "position_side")}
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
    position_side VARCHAR NOT NULL DEFAULT '{LONG}',
    book_id VARCHAR NOT NULL DEFAULT '{DEFAULT_BOOK_ID}',
    {_JOURNAL_TIMESTAMPS},
    {_check("disposals", "position_side")}
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

#: Every fixed `trial_rebalances` count column (version 14, #1153, T127): each
#: is written from the rebalance's counts by name, NULL when the family does
#: not report it, and the version-14 migration copies each non-NULL value into
#: `trial_rebalance_counts` under the column's name.
REBALANCE_COUNT_COLUMNS: tuple[str, ...] = (
    "n_excluded_no_history",
    *PROFITABILITY_REBALANCE_COLUMNS,
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
        _PRE_REBALANCE_COUNTS_VERSION,
        _PRE_PERIOD_KEYS_VERSION,
        _PRE_LAB_VERSION,
        _PRE_EXPANSION_SEAMS_VERSION,
        _PRE_RELEASE_VERSION,
        _PRE_ALERT_BOOKS_VERSION,
        _PRE_SHAKEDOWN_VERSION,
        CURRENT_SCHEMA_VERSION,
    ):
        # Version 4 serves fact and registry reads; versions 5 to 8 every journal
        # read but `overrides` (no `client_order_id` before 9), and versions 5 to 7
        # none of `resume_invocations` and `resume_acceptances` either; version 9
        # every read but `statement_facts`; version 10 every read (no
        # `retracted` column: no retraction, see `has_retracted`); version 11
        # every read but the research tables (`require_research` raises
        # `ResearchNotInitialised`) and `trial_results.n_research`; version 12
        # every read but `trial_rebalances`'s six `PROFITABILITY_REBALANCE_COLUMNS`;
        # version 13 every read but `trial_rebalance_counts`; version 14 every
        # read but the version-15 columns and the `*_period` metric rows;
        # version 15 every read but the lab tables (`lab_schema.require_lab`
        # raises `LabNotInitialised`); version 16 serves fact, registry and lab
        # reads, but every journal read raises `SchemaVersionError`
        # (`store.journal.require_journal`, naming the fix): the eight expanded
        # tables lack `book_id`; version 17 serves every read (a data-release
        # row cannot exist there, and nothing reads `trials.development_boundary`
        # before T142b); version 18 every read but `journal.alerts_for` (no
        # `alerts.book_id`), which only the writing `Alerter` calls; version 19
        # every read (no shakedown row can exist there).

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


#: Where `_migrate_signal_reasons` builds the version-14 table before it takes
#: the name `signals`.
_SIGNALS_STAGING_TABLE = "signals_v14"


def _migrate_signal_reasons(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild a version-5 to 13 `signals` with the version-14 reason `CHECK`
    (module docstring, "Schema versions"), every row kept in insertion order.
    The `CHECK` only widens, so no stored row can be refused. Runs inside
    `init_schema`'s transaction, before its DDL pass."""
    conn.execute(
        _CREATE_SIGNALS.replace(
            "CREATE TABLE IF NOT EXISTS signals (",
            f"CREATE TABLE {_SIGNALS_STAGING_TABLE} (",
            1,
        )
    )
    # Keep the insertion order, as `_migrate_order_event_reasons` does.
    conn.execute(
        f"INSERT INTO {_SIGNALS_STAGING_TABLE} BY NAME SELECT * FROM signals ORDER BY rowid"
    )
    conn.execute("DROP TABLE signals")
    conn.execute(f"ALTER TABLE {_SIGNALS_STAGING_TABLE} RENAME TO signals")


def _relax_no_history_count(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop the NOT NULL on `trial_rebalances.n_excluded_no_history` where it
    is still there (version 14): the column is NULL for a family that does not
    report the count. `_REGISTRY_TABLE_DDL` keeps its version-4 pin, so a fresh
    store loses it here too. Idempotent. Runs inside `init_schema`'s
    transaction, after its DDL pass."""
    notnull = {
        row[1]: bool(row[3])
        for row in conn.execute("PRAGMA table_info('trial_rebalances')").fetchall()
    }
    if notnull["n_excluded_no_history"]:
        conn.execute(
            "ALTER TABLE trial_rebalances ALTER COLUMN n_excluded_no_history DROP NOT NULL"
        )


def _migrate_rebalance_counts(conn: duckdb.DuckDBPyConnection) -> None:
    """Fill `trial_rebalance_counts` from a pre-version-14 store's fixed count
    columns (module docstring, "Schema versions", version 14): for each
    `(trial_id, session)`, its lowest-cost `trial_rebalances` row gives one row
    per `REBALANCE_COUNT_COLUMNS` column that is not NULL (a plan's counts are
    the same at every level). Runs inside `init_schema`'s transaction, after
    `_migrate_profitability_rebalance_counts` (so every column exists) and
    only on a store migrating from an earlier version."""
    selects = " UNION ALL ".join(
        f"SELECT trial_id, session, '{column}' AS name, {column} AS value "
        f"FROM base WHERE {column} IS NOT NULL"
        for column in REBALANCE_COUNT_COLUMNS
    )
    conn.execute(
        f"INSERT INTO {REBALANCE_COUNTS_TABLE_NAME} (trial_id, session, name, value) "
        "WITH base AS (SELECT * FROM trial_rebalances QUALIFY row_number() OVER "
        "(PARTITION BY trial_id, session ORDER BY cost_per_side_bps) = 1) "
        f"{selects}"
    )


#: Every `trial_metrics` key version 15 renames, Phase 3 name to period name.
PERIOD_KEY_RENAMES: Final[dict[str, str]] = {
    "sharpe_monthly": "sharpe_period",
    "sharpe_monthly_excess_spy": "sharpe_period_excess_spy",
    "turnover_monthly": "turnover_period",
    "skew_monthly": "skew_period",
    "kurtosis_monthly": "kurtosis_period",
    "skew_monthly_excess_spy": "skew_period_excess_spy",
    "kurtosis_monthly_excess_spy": "kurtosis_period_excess_spy",
    "n_months": "n_periods",
}

#: Every trial before version 15 rebalanced at `month_end`: its periods per year
#: (`backtest.schedule.MONTHS_PER_YEAR`, which this leaf module does not import).
_PHASE3_PERIODS_PER_YEAR: Final = 12

#: The columns version 15 adds, by table: (name, type, value on existing rows).
_PERIOD_COLUMNS: Final[dict[str, tuple[tuple[str, str, str | None], ...]]] = {
    "trials": (
        ("detail_level", "VARCHAR", "full"),
        ("data_vintage", "TIMESTAMPTZ", None),
        ("code_tree_sha256", "VARCHAR", None),
    ),
    "trial_results": (("sharpe_unit", "VARCHAR", None),),
}


def _migrate_period_columns(conn: duckdb.DuckDBPyConnection) -> None:
    """Add each version-15 column where missing (module docstring, "Schema
    versions", version 15): by `ALTER TABLE`, `detail_level` with a `'full'`
    default dropped straight after (so every existing row reads `full` without an
    `UPDATE`, and no later insert inherits it), the rest NULL. Idempotent.
    `_REGISTRY_TABLE_DDL` keeps its version-4 pin, so a fresh store gets the
    columns here too. Runs inside `init_schema`'s transaction, after its DDL
    pass."""
    for table, columns in _PERIOD_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        for name, type_, existing_value in columns:
            if name in existing:
                continue
            if existing_value is None:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_}")
                continue
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {type_} DEFAULT '{existing_value}'"
            )
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN {name} DROP DEFAULT")


def _migrate_period_metrics(conn: duckdb.DuckDBPyConnection) -> None:
    """Insert the period-key rows for every existing trial (module docstring,
    "Schema versions", version 15): each `PERIOD_KEY_RENAMES` row copied under
    its new name, `periods_per_year = 12` beside every `n_months` row,
    `turnover_annual = 12 * turnover_monthly` and `sharpe_annual_excess_spy =
    sqrt(12) * sharpe_monthly_excess_spy`. The old rows stay. Runs inside
    `init_schema`'s transaction, only on a store migrating from version 14 or
    earlier."""
    renames = ", ".join(f"('{old}', '{new}')" for old, new in PERIOD_KEY_RENAMES.items())
    ppy = _PHASE3_PERIODS_PER_YEAR
    conn.execute(
        "INSERT INTO trial_metrics (trial_id, series, cost_per_side_bps, metric, value) "
        f"WITH renames(old, new) AS (VALUES {renames}) "
        "SELECT m.trial_id, m.series, m.cost_per_side_bps, r.new, m.value "
        "FROM trial_metrics m JOIN renames r ON m.metric = r.old "
        f"UNION ALL SELECT trial_id, series, cost_per_side_bps, 'periods_per_year', {ppy}.0 "
        "FROM trial_metrics WHERE metric = 'n_months' "
        f"UNION ALL SELECT trial_id, series, cost_per_side_bps, 'turnover_annual', {ppy} * value "
        "FROM trial_metrics WHERE metric = 'turnover_monthly' "
        "UNION ALL SELECT trial_id, series, cost_per_side_bps, 'sharpe_annual_excess_spy', "
        f"sqrt({ppy}) * value FROM trial_metrics WHERE metric = 'sharpe_monthly_excess_spy'"
    )


def _migrate_lab(conn: duckdb.DuckDBPyConnection) -> None:
    """The lab migration (module docstring, "Schema versions", version 16):
    `lab_schema.apply_lab_schema`, then, from the `hypotheses` rows that exist
    now, one `pre_lab_hypotheses` row per hypothesis, one
    `hypothesis_fingerprints` row per hypothesis (`frozen.fingerprint` over
    `frozen.frozen_values`, duplicates kept) and one `family_rules` row per
    family from its earliest hypothesis, with the live `Settings` caps
    (`config.get_settings`, read only when a hypothesis exists), the
    `FAMILY_PARENTS` parent and a NULL SR* seed. Registers no hypothesis.
    Runs inside `init_schema`'s transaction, only on a store migrating from
    version 15 or earlier, so a failure leaves the store at its old version.

    The lab modules import this one, so they are imported here, at call
    time."""
    from tradepartner import config
    from tradepartner.backtest import frozen
    from tradepartner.store import lab_registry, lab_schema, registry

    lab_schema.apply_lab_schema(conn)
    ids = [
        hypothesis_id
        for (hypothesis_id,) in conn.execute(
            "SELECT hypothesis_id FROM hypotheses ORDER BY hypothesis_id"
        ).fetchall()
    ]
    if not ids:
        return
    marked_at = utc_now()
    conn.executemany(
        "INSERT INTO pre_lab_hypotheses (hypothesis_id, marked_at) VALUES (?, ?)",
        [[hypothesis_id, marked_at] for hypothesis_id in ids],
    )
    earliest: dict[str, registry.HypothesisRecord] = {}
    for hypothesis_id in ids:
        record = registry.get_hypothesis_by_id(conn, hypothesis_id)
        fingerprint = frozen.fingerprint(
            record.family, frozen.frozen_values(record), record.in_sample_start
        )
        lab_registry.write_fingerprint(conn, hypothesis_id, fingerprint)
        earliest.setdefault(record.family, record)
    settings = config.get_settings()
    parents: dict[str, str | None] = {
        str(family): parent for family, parent in config.FAMILY_PARENTS.items()
    }
    for family, record in earliest.items():
        lab_registry.write_family_rules(
            conn,
            family=family,
            first_hypothesis_id=record.hypothesis_id,
            parent_family=parents.get(family),
            holdout_start=record.holdout_start,
            holdout_end=record.holdout_end,
            in_sample_start=record.in_sample_start,
            fixed_params={
                key: value
                for key, value in frozen.frozen_values(record).items()
                if key.startswith(config.FORBIDDEN_AXIS_PREFIXES)
            },
            sr_star_seed_annual=None,
            settings=settings,
        )


#: The eight journal tables ADR 0015 seams 1 to 3 expand at schema version 17,
#: each with the version-17 DDL `_migrate_expansion_seams` rebuilds it from.
_EXPANSION_SEAM_TABLE_DDL: Final[dict[str, str]] = {
    "paper_windows": _CREATE_PAPER_WINDOWS,
    "decisions": _CREATE_DECISIONS,
    "orders": _CREATE_ORDERS,
    "positions_daily": _CREATE_POSITIONS_DAILY,
    "lots": _CREATE_LOTS,
    "disposals": _CREATE_DISPOSALS,
    "adjustments": _CREATE_ADJUSTMENTS,
    "reconciliations": _CREATE_RECONCILIATIONS,
}


def _migrate_expansion_seams(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild each of `_EXPANSION_SEAM_TABLE_DDL`'s eight tables that lacks
    `book_id` with its version-17 DDL (module docstring, "Schema versions",
    version 17), every row kept in insertion order with the new columns at
    their defaults (`book_id = DEFAULT_BOOK_ID`, `position_side = LONG`, the
    `orders` shape from `ORDER_SHAPE_DEFAULTS`; no row before version 17 could
    hold anything else). Idempotent: a table that already has `book_id` is left
    alone, as `_migrate_retracted` tests, because an older migration rebuilding
    from the current `_CREATE_*` DDL may already have given a table the
    version-17 shape (a version-6 `decisions`). Runs inside `init_schema`'s
    transaction, after `_migrate_lab`."""
    for table, ddl in _EXPANSION_SEAM_TABLE_DDL.items():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if "book_id" in columns:
            continue
        staging = f"{table}_v17"
        conn.execute(
            ddl.replace(f"CREATE TABLE IF NOT EXISTS {table} (", f"CREATE TABLE {staging} (", 1)
        )
        conn.execute(f"INSERT INTO {staging} BY NAME SELECT * FROM {table} ORDER BY rowid")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")


#: The column version 18 adds to `trials` (ADR 0016 point 6).
_DEVELOPMENT_BOUNDARY_COLUMN: Final = "development_boundary"


def _migrate_release_kinds(conn: duckdb.DuckDBPyConnection) -> None:
    """Version 18 (module docstring, "Schema versions"): widen
    `owner_decisions.kind` with `lab_schema.RELEASE_DECISION_KINDS` by
    `lab_schema.widen_enum` (a no-op once present; every row kept in insertion
    order) and add the nullable `trials.development_boundary DATE` where missing
    (NULL on every existing row). Idempotent; runs on a fresh or migrating
    writable store (never one already at 18), inside `init_schema`'s transaction, after
    `_migrate_expansion_seams`. `lab_schema` imports this module, so it is
    imported here, at call time."""
    from tradepartner.store import lab_schema

    lab_schema.widen_enum(conn, "owner_decisions", "kind", lab_schema.RELEASE_DECISION_KINDS)
    columns = {row[1] for row in conn.execute("PRAGMA table_info('trials')").fetchall()}
    if _DEVELOPMENT_BOUNDARY_COLUMN not in columns:
        conn.execute(f"ALTER TABLE trials ADD COLUMN {_DEVELOPMENT_BOUNDARY_COLUMN} DATE")
    forget_column_types(conn)


def _migrate_alert_books(conn: duckdb.DuckDBPyConnection) -> None:
    """Version 19 (module docstring, "Schema versions"): rebuild `alerts` with
    its version-19 DDL where it lacks `book_id`, every row kept in insertion
    order with `book_id = DEFAULT_BOOK_ID` (no alert before version 19 can
    belong to another book) and every other column, `known_at` and
    `ingested_at` included, unchanged. Idempotent: an `alerts` that already
    has `book_id` (a version-4 store's, created from the current DDL in the
    same open) is left alone. Runs inside `init_schema`'s transaction, after
    `_migrate_release_kinds`."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info('alerts')").fetchall()}
    if "book_id" in columns:
        return
    conn.execute(
        _CREATE_ALERTS.replace(
            "CREATE TABLE IF NOT EXISTS alerts (", "CREATE TABLE alerts_v19 (", 1
        )
    )
    conn.execute("INSERT INTO alerts_v19 BY NAME SELECT * FROM alerts ORDER BY rowid")
    conn.execute("DROP TABLE alerts")
    conn.execute("ALTER TABLE alerts_v19 RENAME TO alerts")
    forget_column_types(conn)


def _migrate_shakedown_kinds(conn: duckdb.DuckDBPyConnection) -> None:
    """Version 20 (module docstring, "Schema versions"): widen
    `owner_decisions.kind` with `lab_schema.SHAKEDOWN_DECISION_KINDS` by
    `lab_schema.widen_enum` (a no-op once present; every row kept in insertion
    order, byte-identical). Idempotent; runs on a fresh or migrating writable
    store (never one already at 20), inside `init_schema`'s transaction, after
    `_migrate_alert_books`. `lab_schema` imports this module, so it is imported
    here, at call time."""
    from tradepartner.store import lab_schema

    lab_schema.widen_enum(conn, "owner_decisions", "kind", lab_schema.SHAKEDOWN_DECISION_KINDS)
    forget_column_types(conn)


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every store table if it does not already exist, migrating a
    version-2 to 19 store to version 20.

    Idempotent: safe to call on every process start and every test. Also
    pins the connection's session timezone to UTC and disables extension
    auto-install/auto-load (`configure_connection`) so a caller that built
    its own raw connection still gets correct `TIMESTAMPTZ` round-tripping
    and no surprise network access, and drops any cached column-type info
    for `conn` (`store.db.forget_column_types`) so `store.db.insert_row`
    never reuses type info cached before these tables existed.

    On a writable connection, in one transaction (the caller's if open): a
    fresh store gets every table and one `schema_version` row for
    `CURRENT_SCHEMA_VERSION` and no lab table; every store gets
    `owner_decisions.kind` widened with `shakedown_span` and `shakedown_note`
    (every row kept), and a version-19 store a version-20 row (#1388, T157);
    a version-18 or earlier store
    gets `alerts` rebuilt with `book_id` where it lacks it (every row kept in
    insertion order, `book_id = 'main'`; #1370, T154) and a version-19 row;
    every store gets
    `owner_decisions.kind` widened with `data_release` and
    `development_boundary` (every row kept) and the nullable
    `trials.development_boundary`, and a version-17 store a version-18 row
    (#1319, T140b); a version-16 or earlier store
    gets each of the eight journal tables ADR 0015 seams 1 to 3 expand
    rebuilt with the version-17 DDL where it lacks `book_id` (every row kept
    in insertion order, the new columns at their defaults; #1258, T132);
    a version-2 to 15 store gets, last, the lab tables, the two widened
    enumerations and the
    `pre_lab_hypotheses`, `hypothesis_fingerprints` and `family_rules` rows
    for its existing hypotheses and families (#1195, T113); every store gets
    the version-15 columns
    (`trials.detail_level`, `full` on existing rows, `data_vintage`,
    `code_tree_sha256`; `trial_results.sharpe_unit`), and a version-2 to 14
    store the period-key `trial_metrics` rows for every existing trial and a
    version-15 row (#1179, T97); every store gets `trial_rebalance_counts`
    created and `trial_rebalances.n_excluded_no_history` made nullable, and a
    version-5 to 13 store `signals` rebuilt with the version-14 reason `CHECK`
    (every row kept), the counts table filled from its fixed count columns
    and a version-14 row (#1153, T127); every store gets `trial_rebalances`'s six
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

    On a read-only connection no DDL runs: a version-20, 19, 18 (every read but
    `journal.alerts_for`, which only a writer calls) or 17 store passes, and a
    version-16 store serves fact, registry and lab reads while every journal
    read raises `SchemaVersionError` (`store.journal.require_journal`, naming
    the fix); 15 (every read but
    the lab tables, which `lab_schema.require_lab` reports as
    `LabNotInitialised`), 14 (every read but
    the version-15 columns and period rows), 13 (every read but
    `trial_rebalance_counts`), 12, 11, 10, 9, 8, 7,
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
        _PRE_REBALANCE_COUNTS_VERSION,
        _PRE_PERIOD_KEYS_VERSION,
        _PRE_LAB_VERSION,
        _PRE_EXPANSION_SEAMS_VERSION,
        _PRE_RELEASE_VERSION,
        _PRE_ALERT_BOOKS_VERSION,
        _PRE_SHAKEDOWN_VERSION,
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
        journal_before_14 = range(_PRE_JOURNAL_VERSION + 1, _PRE_REBALANCE_COUNTS_VERSION + 1)
        if max_version in journal_before_14:
            _migrate_signal_reasons(conn)
        for ddl in (
            _TABLE_DDL
            + _REGISTRY_TABLE_DDL
            + _JOURNAL_TABLE_DDL
            + _STATEMENT_FACTS_TABLE_DDL
            + _MASTER_UNDERIVED_TABLE_DDL
            + _RESEARCH_TABLE_DDL
            + _REBALANCE_COUNTS_TABLE_DDL
        ):
            conn.execute(ddl)
        _migrate_retracted(conn)
        _migrate_n_research(conn)
        _migrate_profitability_rebalance_counts(conn)
        _relax_no_history_count(conn)
        if max_version is not None and max_version <= _PRE_REBALANCE_COUNTS_VERSION:
            _migrate_rebalance_counts(conn)
        _migrate_period_columns(conn)
        if max_version is not None and max_version <= _PRE_PERIOD_KEYS_VERSION:
            _migrate_period_metrics(conn)
        forget_column_types(conn)
        if max_version is not None and max_version <= _PRE_LAB_VERSION:
            _migrate_lab(conn)
        if max_version is not None and max_version <= _PRE_EXPANSION_SEAMS_VERSION:
            _migrate_expansion_seams(conn)
        if max_version is None or max_version <= _PRE_RELEASE_VERSION:
            _migrate_release_kinds(conn)
        if max_version is not None and max_version <= _PRE_ALERT_BOOKS_VERSION:
            _migrate_alert_books(conn)
        if max_version is None or max_version <= _PRE_SHAKEDOWN_VERSION:
            _migrate_shakedown_kinds(conn)
        if max_version != CURRENT_SCHEMA_VERSION:
            first_new = CURRENT_SCHEMA_VERSION if max_version is None else max_version + 1
            applied_at = utc_now()
            for version in range(first_new, CURRENT_SCHEMA_VERSION + 1):
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    [version, applied_at],
                )

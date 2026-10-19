"""Data health as code (spec req 11; plan T18), shared by `tradepartner health`
(T19) and the data-health page (T21).

`health_report(conn, t, settings)` reads the store through the connection it is
given and computes nothing but what the rows say. Every metric that describes
the data (coverage, gaps, survivorship gap, unclassifiable, `snapshot_static`
reliance, delisted names) reads only rows with `known_at <= t`, like any as-of
function; the CLI and the page pass the current time. `t` must be a tz-aware
`datetime` (a bare date raises `TypeError`, a naive datetime `ValueError`), and
`session` is the last session completed at `t`.

**Metrics.** The spec names them; where it does not define one, the definition
here is this module's and is stated once:

- **Last ingest per source** (`last_ingests`): from `ingestion_runs` rows
  known at `t`, i.e. finished at or before `t` (ingest writes a run's row when
  the run ends), other than `master-retract`'s (#859), per source
  (`ingest.SOURCES` first, then any other source in the table,
  alphabetically): the finish time and cursor of the last `ok` run,
  and the status, start and message of the latest run of any status. A source
  that never ran shows `None`s.
- **Coverage** (`coverage`): the population of ingest's staleness check
  (spec req 10): securities whose current listing (latest `valid_from` on or
  before `session`) is live at `session`, i.e. listed, or transferred with its
  end on or after `session`, and that are classified `common` or are a
  benchmark; how many of them have a bar for `session` known at `t`, the ones
  that do not, and the share (0.0 when no name is live). Also the first and
  last bar session in the store and how many securities have any bar, all
  known at `t`.
- **Gaps** (`bar_gaps`): interior holes in each security's bar history known at
  `t` up to `session`: XNYS sessions between its first and last bar with no bar.
  One row per security with at least one hole: first and last bar, bar count,
  missing sessions and the longest run of consecutive missing sessions. A
  trailing absence (no bar since some session) is not a gap here; coverage and
  the survivorship gap report it. The history is not split at listing
  boundaries, so a security delisted and later relisted shows the break between
  its two listings as one gap.
- **Survivorship gap** with its four side categories (the stale listings,
  #1199, among them): `gap.survivorship_gap` at `t`, unchanged.
- **Unclassifiable** (`unclassifiable`): securities known at `t` whose latest
  classification is `unclassifiable`, and those with no classification row at
  all (`unclassified`), the two missing-data reasons of universe rule 1.
- **`snapshot_static` reliance** (`static_reliance`): securities known at `t`
  whose `securities` row, current listing or classification as of `t` rests on
  a `snapshot_static` row, with the count per table. Such a row counts only
  from its own `known_at` (#35).
- **Delisted names** (`delisted_names`): securities whose current listing is
  delisted at `t` (Forms 25 and 25-NSE; a transfer counts as delisted until its
  new listing is known, spec req 4), with ticker, exchange, class, form, filing
  time, end session and effective date.
- **Listed with no bars after a Form 25** (`listed_without_bars`, #1460,
  #828 item 5): securities whose current listing at `session` (as
  `delisted_names` picks it) is listed, on an exchange (not `NONE` or `OTC`,
  which the feed has no bars for), and starts after an earlier listing of
  the same security that a Form 25 or 25-NSE ended, with no bar known at
  `t` from that listing's `valid_from` through `session`. It is the known
  limit of the master's 8-A12B/cover-page rules: a stale 10-K or 10-Q cover
  page that still names the exchange after a real delisting reopens a
  listed row with nothing behind it. One row per security, with the
  current listing and the latest earlier delisting (form, filing time,
  effective date). Report-only, like `delisted_names`: no rule reads it,
  `health --check` does not fail on it, and it has no grace period, so a
  genuine relisting whose first bars are not in yet shows until they are.
- **Price jumps** (`price_jumps`): the owner's review list (#787),
  `store.asof.price_jumps_as_of` at `t` over every security: each one-day
  close move between traded bars outside the `universe` jump bounds that no
  split or dividend known at `t` explains, with `accepted` from
  `universe.accepted_price_jumps`. An unaccepted jump fails universe rule 6
  while it is in the history window; accepting one is a config change.
  `jumps_before`, when given, keeps only jumps on sessions before it (a
  hypothesis's `holdout.start`), so the list never shows the holdout period.
- **Shares outliers** (`shares_outliers`): the owner's review list (#845),
  `universe.shares_as_of` at `t` over every security: each shares fact out
  of line with the security's last accepted earlier fact (ratio after the
  splits known at `t` outside `universe.max_shares_ratio`), with `accepted`
  from `universe.accepted_shares_facts`. Universe rules 7 and 8 use the last
  accepted fact instead of an unaccepted one. `jumps_before` keeps only
  facts with an `as_of_date` before it.
- **Accepted same-day pairs** (`accepted_same_day_pairs`, #855): every
  same-start pair of different tickers that `non_overlapping_listings` would
  fail but the owner accepted in `universe.accepted_same_day_pairs`
  (`"<security_id>@<valid_from>"`), in that rule's row shape, so an accepted
  pair is listed, never silently hidden.
- **Settings**: `universe.liquidity_rule_enabled` and `execution.fill_price`.
- **Statement facts** (`statement`, #660, T77c): `edgar.statement_facts_enabled`'s
  state; while it is on, `statement_coverage`'s share of `universe_as_of(T)`
  names whose issuer has a `revenue` and a `total_assets` row known at `t`
  with `period_end` within `universe.max_shares_age_days` of the last
  completed session at `t` (rule 7's own bound, measured the same way;
  not any backtest family's own `max_fact_age_days`,
  which can be wider; report-only, no rule reads it), and the derived share
  of every known `gross_profit` row (`basis = derived`, over every CIK with
  a `securities` row known at `t`, not only `universe_as_of(T)`'s members,
  one row per `(cik, period_end, period_days)`: a parse-quality count, not
  a portfolio one); while it is off, `coverage` is `None` -- nothing to
  report on, and `universe_as_of` is not paid for. `counts` is
  `statement_counts` of the latest EDGAR run's message, empty when it
  names none. The data-health page's own card (dashboard T77c) shows a
  warning badge while `vintage_late` is positive; `health --check` does
  not, since this is a reported figure, not one of the integrity rules
  below. A CLI warning, like the quarantined-count one `cli._quarantined`
  already prints, is T77b's to add alongside the run message it reads.

- **Filing events** (`filing_events`, #1358, T164e): `edgar.filing_events_enabled`'s
  state; the row count and latest `accepted_at` per form (every row in the
  table, whatever its `known_at`: a count of the store, not a read at `t`);
  `counts` is `filing_event_counts` of the latest EDGAR run's message, with a
  warning while `items_missing` or `clock_suspect` is positive. Coverage is
  reported, not thresholded. `clock_suspect` is the clock detector's rule
  (a health finding, never a repair): a stamp that fits only a moved clock.

- **Rates** (`rates`, #1498, T169e): per configured or stored series, the
  stored row count, latest observation, vintage and stamp, revised-key count,
  the longest missing XNYS-session span between observations, and the last
  run's counts and staleness. A revised key, a late row in the last daily run,
  a gap beyond `rates.max_stale_sessions`, or a value outside
  `rates.plausible_range_percent` warns on the page; none fails `--check`.
  The stamp, unique key and finite value are integrity rules below.

- **Coverage against a named reference** (`coverage_reference`, #1541,
  T180): the one check that compares the store with a list outside its own
  derivation, `coverage.reference_names` (`CIK:TICKER[:FROM[:TO]]`, the
  sessions each entry applies on, inclusive). The sample sessions
  (`sample_sessions`) are the last XNYS session of each
  `coverage.sample_months` month from `coverage.sample_from` to the last
  session completed at `t`. On each sample session S, at `t`: (a)
  `reference_missing`, every entry covering S whose CIK is not a member of
  `universe_as_of(close(S))` under its ticker (compared as Alpaca symbols,
  so `BRK.B` is `BRK-B`), with the universe's exclusion of the CIK's
  security that carries the ticker (else, when the CIK is a member under
  another ticker, `ticker:<that ticker>`; else the primary id's exclusion,
  the CIK itself; else the CIK's first security's; else `no_security`); (b)
  `late_known`, the securities whose current listing at S, from the rows
  known at `t` (`listing_ends_as_of` at `t`), covers S (listed; or ended on
  or after S; or a transfer with no end yet) while none of their rows
  covering S is visible to a read at `close(S)` (its latest revision known
  by then exists and is not a retraction, #859): names the
  store lists on S that no read at S could see, with their share of the
  names listed on S (every exchange code, `NONE` and `OTC` included); (c)
  the member count on S and, on the latest S only, the count of CIKs known
  at `t` with a `snapshot` or `snapshot_static` listing on a
  `universe.exchanges` exchange (the SEC's current list as the store holds
  it: a cross-check, not an independent source) and the ratio of the two,
  with no threshold. The rule `coverage_reference_names` is not in
  `INTEGRITY_RULES` (each of those is emitted on every run, with no
  skipped state): `HealthReport.failures` names it only when the block was
  computed with `coverage.check_enabled` on and (a) is non-empty, so
  `health --check` fails on it only while `check_enabled`; a block computed
  on request with the switch off reports, and fails nothing. The block costs
  one `universe_as_of` per sample session, so `health_report` computes it
  only when asked (`health --coverage`, the page's button) or while
  `check_enabled` is on; otherwise `coverage_reference` is `None`, "not
  computed", never a pass.

  The block's `index_members` (#1682, T189b; `IndexMembersCheck`) is the
  S&P 500 answer key (`store.index_members`, T189) as a second reference, on
  the same sample sessions, while `coverage.index_members` is on (the
  default). **A check only**: S&P membership is a committee's after-the-fact
  selection, so this module is the key's one reader outside its loader, and
  no universe, signal, backtest, paper or research read touches it
  (`tests/lookahead/test_check_only_inputs.py`). On each sample session S up
  to the key's last dated row, every key interval covering S is resolved to
  a security through the listing rows known at `t` (`index_members.resolve`)
  and is a hit when that security is a member of `universe_as_of(close(S))`,
  by `security_id`, so a second class's own miss counts; a miss carries the
  universe's exclusion (`rule_name:reason`), `unresolved` (no listing row
  carries the ticker) or `not_considered`. A session after the key's last
  dated row is flagged `beyond_key` and counts nothing. The block reports the
  key's commit SHA, the hit share per session, and the misses with their
  names. The rule `index_members_missing` is in `HealthReport.failures` only
  while `coverage.index_max_missing` is set and some session's misses exceed
  it, or the key cannot be loaded; unset (the default), it reports and fails
  nothing. Setting the threshold also makes `health` compute the block on
  every run.

**Integrity rules** (`integrity_checks`), each a named `IntegrityCheck` whose
`violations` frame lists the offending rows or keys, empty when it passes.
`HealthReport.ok` is true when every rule passes; `health --check` exits
non-zero otherwise, naming `HealthReport.failures`. The row-level rules read
every row in the store, whatever its `known_at`; the two listing rules are
derived at `t`, as the data is read.

- `known_at_not_null`, `known_at_le_ingested_at`, `source_not_null` and
  `provenance_allowed` (the table's own set, `schema.TABLE_PROVENANCE_VALUES`)
  on every fact table. The schema's constraints already block these; the rules
  make a store written by other code, or a changed schema, show it.
- `bars_on_sessions`: every `prices_daily.session` is an XNYS session in the
  configured calendar range.
- `no_duplicate_bars`: no two bars share `(security_id, session, known_at)`.
- `non_overlapping_listings`: per security, ordered by `valid_from`, no two
  listings start on the same session, and no listing ended by a Form 25 was
  filed on more than `master.transfer_window_sessions` sessions after the next
  exchange line started (both lines live at once: a dual listing, or a filing
  or listing resolved to the wrong security). The filing session is the raw
  fact to test: the derived end of a delisted or transferred listing is
  clipped before the next listing's start by construction (spec req 4). A
  listing with no Form 25 is superseded by the next one (a ticker change), as
  the as-of reads treat it. Three cases are not a second line (#822,
  #1720): rows of the same ticker starting the same day, which differ only in
  the exchange their filers tagged (or the class title's wording), are one
  line; a row on `OFF_EXCHANGE` (NONE or OTC) is no exchange line at all (the
  normal suspension, OTC quote, late Form 25 sequence), so a late filing is
  tested against the next row that is neither; and a same-start OTC pair
  where one ticker is the other plus FINRA's fifth-letter `D` temporary
  symbol (`JFIL`/`JFILD`) is one line, the new quote a reverse split opens.
  Every other same-start pair of different tickers fails unless the owner
  lists it in
  `universe.accepted_same_day_pairs` (#855). An accepted pair counts as one
  line: a late Form 25 on its rows is still tested, against the next
  exchange line after that day, and the report lists the pair.
- `no_bars_after_delisting`: no bar known at `t` for a delisted listing's
  security that *resumes* after the delisting's `effective_on` and before the
  security's next listing on an exchange, if any (a NONE or OTC row, often
  started before `effective_on` in the suspension sequence, does not end the
  check: the feed has no OTC bars, spec req 10). A listing's end is its last bar (spec req
  4), so a line that keeps trading past `effective_on` without a break (a
  holding-company or redomicile Form 25 on the same line, CMPR) is its own
  tail and passes. A bar that comes after more than
  `master.transfer_window_sessions` missing sessions fails, with every bar
  after it up to that listing. Missing sessions are XNYS sessions strictly
  between two bars; the first gap is counted from the last bar on or before
  `effective_on`, or from `effective_on` when there is none, so a delisting
  that took effect long before the store's first bar fails its line's bars:
  the store cannot show the line kept trading.
  Such a bar is another equity's resolved to this one (a reused ticker,
  EGLE), a relisting with no listing row, or bars on both sides of a hole in
  the store. A delisting whose next *equity* listing of the security
  carries the same ticker (compared as `alpaca_symbol` keys it) raises no
  violation at all (#829): the resolver's own rule 7 (`docs/specs/data-foundation.md`,
  "Resolver rules"; #819) keeps the span running under that ticker through
  the delisting, so the bars between are the security's own, never a
  resumption to flag, however long the gap. A later row that shares the
  ticker but is not EQUITY (a note, a right, a unit) does not count: the
  resolver's rule only runs a span on when both rows are EQUITY. **Known
  limit (#827):** when there is no bar at all on or before `effective_on`
  (the line's own history is unknown, not merely absent), `effective_on`
  itself stands in for "the last bar on or before it" when counting the
  first gap; a bar that is some other security's reused ticker, not this
  line's own, still passes if it starts within
  `master.transfer_window_sessions` of `effective_on`, the same grace a
  genuine resumption gets. Zero rows hit this on the 2026-10-04 backfilled
  store.
- `underived_master_rows` (#859): the stored `securities` and `listings` rows
  the latest master check finished by `t` (an EDGAR ingest chunk or a
  `master-retract --apply`) found the current rules no longer derive, and
  that are still live at `t` (`store.retraction.underived_as_of`): table,
  key, stored `known_at` and the check's run id. `tradepartner
  master-retract` lists the same set from a fresh build and retracts it.
- `statement_period_days` (#660, T77c): every `statement_facts` row has
  `period_start` NULL exactly when `period_days = 0`, else `period_days =
  period_end - period_start` in days. The schema's own `CHECK` already
  blocks the first half; this restates both for a store's own confidence
  (the schema comment's words), the shape a store built by other code, or
  a future schema, could still break.
- `statement_derived_matches_components`: every `basis = derived` row equals
  the same-filing components it was derived from, per the ingest's
  `DERIVED_FACTS` table: `gross_profit` the difference of the two stored
  `revenue` and `cost_of_revenue` rows, and `revenue_financial` (#1556) the
  sum of the two stored `net_interest_income` and `noninterest_income` rows,
  each sharing the derived row's `cik`, `period_end`, `period_days` and
  `filing_accession`, both present -- a missing component row fails it too.
  Cannot fail on correct data (spec "Health").
- `statement_basis_allowed`: `statement_facts.basis` is in
  `schema.STATEMENT_FACT_BASIS_VALUES` (`{reported, derived}`). The
  schema's own `CHECK` already blocks this too.

- `filing_events_known_at_is_accepted_at`, `filing_events_form_allowed`
  (`form` in `edgar.event_forms`), `filing_events_items_not_null` and
  `filing_events_unique` (`(cik, accession)`), #1358, T164e. The schema's
  `CHECK`, `NOT NULL` and `UNIQUE` already block three of them; the rules
  restate them for a store built by other code. Narrowing `edgar.event_forms`
  fails `filing_events_form_allowed` on the stored rows of a dropped form
  until a `rebuild_filing_events`.
- `rates_known_at_from_vintage`, `rates_unique` and `rates_value_finite`
  (#1498): every rate stamp equals `rate_known_at(vintage_date)`, no two
  rows share `(series, observation_date, known_at)`, and every value is
  finite. The four common-column rules above also cover `rates`.
- `session_holes` (#1575, #1550 part c): one row per XNYS session from the
  first session of the `universe.min_history_months` window at `t`
  (`calendar.sessions_in_month_window`, as the universe's own rule 6 reads
  it) through the latest session the store holds rows for, whose
  `prices_daily` rows known at `t` are below
  `health.min_session_rows_share` of the previous session's row count. A
  session with no row at all is a hole whatever the share says; the default
  share `0.0` therefore reports an empty session and nothing else, since the
  bar count moves with listings and delistings and only an empty session has
  no such explanation. The previous session is the previous calendar
  session, and a session before the store's first bar is not judged (nothing
  was ever ingested for it). Sessions after the latest stored one are
  staleness -- coverage and the ingest checks already report it -- not
  holes, so a store whose last ingest missed a day passes here and fails
  there. This is the check that catches a one-day ingest outage the universe
  would otherwise turn into an empty universe: rule 6 requires a bar on
  every session of that window, so one missing session fails every name.
- `duplicate_dividends` (#1613, T186b): per `(security_id, ex_date,
  ratio_or_amount)` of the `dividend` rows live at `t`
  (`live_actions_as_of`), the identities on it, classed against the
  reviewed list (`store.dividend_reviews`, read at the edge so tests inject
  one) by `equal_amount_groups`: a key with more than one identity that the
  list does not hold, or a listed key with another identity count than the
  two reviewed (a third record; a lost leg; a duplicate whose ids changed),
  fails, one row per identity with its `known_at`. `known_at` and the id
  are never part of the key, and the rule has no date bound. The
  `duplicate_dividends` block lists the accepted (`distinct`) and
  unresolved keys, never hidden, and the pending ones (a listed `duplicate`
  not repaired yet): a CLI warning, like the quarantined count, and not a
  failure, so a known, listed repair waiting for its release never blocks
  the paper shakedown's E.7.
- `borrow_status_unique` (#1506, T171e): no two `borrow_status` rows share
  `(asset_id, known_at)`. The schema's `UNIQUE` already blocks it; the rule
  restates it for a store built by other code. The four common-column rules
  above also cover `borrow_status`, `provenance_allowed` holding it to
  `snapshot`.
- The successor-chain rules (#821, T187c), over raw rows and never through
  the chained reads, one per live `successor_chains` row at `t`
  (`live_chains_as_of`): `chain_ids_distinct_ciks` (the two ids name
  different CIKs), `chain_known_at_le_ingested_at`, and
  `chain_known_at_ge_successor_rows` (the row's stamp is at or after the
  successor's `securities` row's and its first listing row's, so a lagging
  stamp is named until the next EDGAR run moves it). The row's
  `chain_successor_listed_under_predecessor_ticker` and
  `chain_predecessor_no_bar_after_handover` (no predecessor bar on or after
  the successor's first bar) fail a row over the stored listings and bars.
  A `chain_price_break` is a warning, never a failure: the close ratio
  across the hand-over (the successor's first stored close over the
  predecessor's last) is above `master.chain_max_price_ratio` or below its
  inverse. The `successor_chains` block lists each live chain with its
  hand-over day (the successor's first listing row's `valid_from`), the
  ratio, the predecessor facts dated after the hand-over, and the latest
  EDGAR run's `chain_candidates` beside them.

**Borrow status** (`BorrowHealth`, #1506, T171e; ADR 0021): the table's row
count and distinct assets known at `t`, the latest observation (the newest
`ok` `alpaca_assets` run cursor at or before `t`, never the table's latest
`known_at`, which lags it on a quiet day) and that latest `known_at`, the
last run's `borrow_*` counts, and at the latest observation the `shortable`
and `easy_to_borrow` counts among `active` assets (the table holds only
`us_equity` ones). Two report-only warnings: the latest observation is more
than `borrow.max_stale_sessions` sessions before `session` (the sessions
after the one completed at the observation, through `session`), or none was
recorded while `borrow.enabled` is on; and `borrow.enabled` is off.

**OpenFIGI types** (`figi_types`, #1713, T189c): a check only, like the
index answer key, and never a source. The latest `openfigi_types` row per
security known at `t` against the security's latest classification and its
current listing's title at `t` (`listing_ends_as_of`): a security the store
classifies `common` that OpenFIGI calls a note, bond, preferred, warrant,
unit or depositary receipt, and the converse (ours `depositary`,
`unclassifiable` or `debt` on a class OpenFIGI calls "Common Stock" or
"REIT"), each named with both types in `rows`. Only a security whose
current listing covers the session is counted, as the ingest maps live
names alone: a delisted name's last observation does not weigh on the gate
forever. A security whose latest observation is `mapped = FALSE` is not
named, only counted in `unmapped`; a mapped row that agrees is not
reported. The rule
`figi_types_disagreements` joins `HealthReport.failures` only while
`openfigi.max_disagreements` is set: the disagreements plus the unmapped
count over it, or the table holding no observation at `t` (a gate never
passes on a check it did not make), fails; unset (the default), the block
reports and fails nothing.

This module holds no threshold; the only numbers in it are 0 and 1, and
EDGAR's own filing-date cutoff (`EDGAR_FILING_DATE_CUTOFF`).
"""

from __future__ import annotations

import csv
import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.adapters.alpaca_prices import (
    alpaca_symbol,
    is_placeholder_ticker,
    is_same_day_typo,
    same_alpaca_symbol,
)
from tradepartner.adapters.prices import announcement_date_known_at, spinoff_known_at_floor
from tradepartner.adapters.rates import rate_known_at
from tradepartner.calendar import (
    all_sessions,
    is_session,
    last_completed_session,
    last_session_of_month,
    previous_session,
    session_close,
    sessions_in_month_window,
)
from tradepartner.config import (
    ReferenceName,
    Settings,
    get_settings,
    parse_accepted_same_day_pair,
)
from tradepartner.gap import SurvivorshipGap, survivorship_gap
from tradepartner.ingest import DERIVED_FACTS, MODE, OK, SOURCES
from tradepartner.store.asof import (
    _FACT_KEY,
    _PRICE_KEY,
    ASSETS_SOURCE,
    _latest_as_of,
    _traded,
    _validate_t,
    borrow_status_as_of,
    listings_as_of,
    live_actions_as_of,
    price_jumps_as_of,
    prices_as_of,
    statement_facts_as_of,
)
from tradepartner.store.classify import EQUITY, UNCLASSIFIABLE, classifications_as_of, listing_kind
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    TRANSFERRED,
    _filing_session,
    _window,
    listing_ends_as_of,
)
from tradepartner.store.dividend_reviews import (
    EqualAmountGroup,
    ReviewedGroup,
    equal_amount_groups,
    reviewed_groups,
)
from tradepartner.store.index_members import (
    UNRESOLVED,
    IndexMembers,
    load_index_members,
    normalize_ticker,
    resolve,
)
from tradepartner.store.master import first_session, securities_as_of, security_cik, session_of
from tradepartner.store.retraction import EDGAR, RETRACT, live_chains_as_of, underived_as_of
from tradepartner.store.schema import (
    STATEMENT_FACT_BASIS_VALUES,
    TABLE_PROVENANCE_VALUES,
    has_retracted,
)
from tradepartner.timeutil import ensure_tz_aware_utc
from tradepartner.universe import shares_as_of, universe_as_of

STATIC = "snapshot_static"
#: The classification ingest's staleness check counts, with benchmarks.
_COMMON = "common"
#: The exchange codes `adapters.edgar.normalize_exchange` gives a class
#: registered with no exchange ("None") or quoted over the counter: a row on
#: one of them is never a second exchange line (#822).
OFF_EXCHANGE: frozenset[str] = frozenset({"NONE", "OTC"})
#: FINRA's fifth-letter temporary-symbol suffix (#1720): the new quote a
#: reverse split opens appends this letter to the old symbol (`JFIL` ->
#: `JFILD`). A rule of the source, not a tunable threshold.
FINRA_TEMPORARY_SUFFIX = "D"
#: The length of the base a FINRA temporary symbol is built on: the suffix
#: is the base's *fifth* character, so the base itself is four letters.
FINRA_TEMPORARY_BASE_LENGTH = 4
#: `_row_kind`'s result for a placeholder-ticker row (#846): never `EQUITY`,
#: matching `ListingResolver`'s own `_PLACEHOLDER` kind.
_PLACEHOLDER_KIND = "placeholder"

KNOWN_AT_NOT_NULL = "known_at_not_null"
KNOWN_AT_NOT_AFTER_INGESTED_AT = "known_at_le_ingested_at"
SOURCE_NOT_NULL = "source_not_null"
PROVENANCE_ALLOWED = "provenance_allowed"
FILENAME_LISTING_VALID_FROM = "filename_listing_valid_from"
BARS_ON_SESSIONS = "bars_on_sessions"
NO_DUPLICATE_BARS = "no_duplicate_bars"
NON_OVERLAPPING_LISTINGS = "non_overlapping_listings"
NO_BARS_AFTER_DELISTING = "no_bars_after_delisting"
UNDERIVED_MASTER_ROWS = "underived_master_rows"
STATEMENT_PERIOD_DAYS = "statement_period_days"
STATEMENT_DERIVED_MATCHES_COMPONENTS = "statement_derived_matches_components"
STATEMENT_BASIS_ALLOWED = "statement_basis_allowed"
FILING_EVENTS_KNOWN_AT_IS_ACCEPTED_AT = "filing_events_known_at_is_accepted_at"
FILING_EVENTS_FORM_ALLOWED = "filing_events_form_allowed"
FILING_EVENTS_ITEMS_NOT_NULL = "filing_events_items_not_null"
FILING_EVENTS_UNIQUE = "filing_events_unique"
#: The coverage check's rule (#1541): reported from the `coverage_reference`
#: block, never from `INTEGRITY_RULES` (module docstring).
COVERAGE_REFERENCE_NAMES = "coverage_reference_names"
#: The index answer key's rule (#1682, T189b): reported from the
#: `coverage_reference` block's `index_members`, never from `INTEGRITY_RULES`.
INDEX_MEMBERS_MISSING = "index_members_missing"
#: The session-hole rule (#1575): an XNYS session in the universe history
#: window whose `prices_daily` row count is below
#: `health.min_session_rows_share` of the previous session's.
SESSION_HOLES = "session_holes"
RATE_KNOWN_AT = "rates_known_at_from_vintage"
RATE_UNIQUE = "rates_unique"
RATE_VALUE_FINITE = "rates_value_finite"
SPINOFF_KNOWN_AT_FLOOR = "spinoff_known_at_floor"
SYMBOL_CHANGE_KNOWN_AT = "symbol_changes_known_at_from_process_date"
#: The duplicate-dividends rule (#1613, T186b): `duplicate_dividends`.
DUPLICATE_DIVIDENDS = "duplicate_dividends"
#: The borrow-status key rule (#1506, T171e): `borrow_status_unique`.
BORROW_STATUS_UNIQUE = "borrow_status_unique"
#: The OpenFIGI types rule (#1713, T189c): reported from the `figi_types`
#: block, never from `INTEGRITY_RULES`, and only while
#: `openfigi.max_disagreements` is set.
FIGI_TYPES_DISAGREEMENTS = "figi_types_disagreements"
#: The successor-chain rules (#821, T187c), over raw rows: distinct CIKs,
#: the row's own stamp order, its stamp against its successor's rows, the
#: successor listing under the predecessor's ticker, and no predecessor bar
#: after the hand-over.
CHAIN_IDS_DISTINCT_CIKS = "chain_ids_distinct_ciks"
CHAIN_KNOWN_AT_LE_INGESTED_AT = "chain_known_at_le_ingested_at"
CHAIN_KNOWN_AT_GE_SUCCESSOR_ROWS = "chain_known_at_ge_successor_rows"
CHAIN_SUCCESSOR_LISTED_UNDER_PREDECESSOR_TICKER = "chain_successor_listed_under_predecessor_ticker"
CHAIN_PREDECESSOR_NO_BAR_AFTER_HANDOVER = "chain_predecessor_no_bar_after_handover"
#: The successor-chain price-break warning (#821, T187c): a block warning,
#: never a failure, so it is not in `INTEGRITY_RULES`.
CHAIN_PRICE_BREAK = "chain_price_break"

#: Every integrity rule, in the order `integrity_checks` reports them.
INTEGRITY_RULES: tuple[str, ...] = (
    KNOWN_AT_NOT_NULL,
    KNOWN_AT_NOT_AFTER_INGESTED_AT,
    SOURCE_NOT_NULL,
    PROVENANCE_ALLOWED,
    FILENAME_LISTING_VALID_FROM,
    BARS_ON_SESSIONS,
    NO_DUPLICATE_BARS,
    NON_OVERLAPPING_LISTINGS,
    NO_BARS_AFTER_DELISTING,
    UNDERIVED_MASTER_ROWS,
    STATEMENT_PERIOD_DAYS,
    STATEMENT_DERIVED_MATCHES_COMPONENTS,
    STATEMENT_BASIS_ALLOWED,
    FILING_EVENTS_KNOWN_AT_IS_ACCEPTED_AT,
    FILING_EVENTS_FORM_ALLOWED,
    FILING_EVENTS_ITEMS_NOT_NULL,
    FILING_EVENTS_UNIQUE,
    SESSION_HOLES,
    RATE_KNOWN_AT,
    RATE_UNIQUE,
    RATE_VALUE_FINITE,
    SPINOFF_KNOWN_AT_FLOOR,
    SYMBOL_CHANGE_KNOWN_AT,
    DUPLICATE_DIVIDENDS,
    BORROW_STATUS_UNIQUE,
    CHAIN_IDS_DISTINCT_CIKS,
    CHAIN_KNOWN_AT_LE_INGESTED_AT,
    CHAIN_KNOWN_AT_GE_SUCCESSOR_ROWS,
    CHAIN_SUCCESSOR_LISTED_UNDER_PREDECESSOR_TICKER,
    CHAIN_PREDECESSOR_NO_BAR_AFTER_HANDOVER,
)

#: EDGAR's filing-date rule: a submission accepted on a business day after
#: 17:30 New York is dated the next business day, one before it that day
#: (#1358, "The clock"; T164's count found the cutoff holds for 8-Ks). A
#: rule of the source, not a tunable threshold.
EDGAR_FILING_DATE_CUTOFF = time(17, 30)
_NEW_YORK = ZoneInfo("America/New_York")

_FACT_TABLES: tuple[str, ...] = tuple(TABLE_PROVENANCE_VALUES)

_TABLE_COUNT_SCHEMA: dict[str, Any] = {"table": pl.Utf8, "rows": pl.Int64}
_PROVENANCE_SCHEMA: dict[str, Any] = {"table": pl.Utf8, "provenance": pl.Utf8, "rows": pl.Int64}
_SESSION_SCHEMA: dict[str, Any] = {"session": pl.Date, "rows": pl.Int64}
#: `session_holes` (#1575): the hole, the row count that makes it one, and the
#: session it is measured against with its count.
_SESSION_HOLE_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "rows": pl.Int64,
    "previous_session": pl.Date,
    "previous_rows": pl.Int64,
}
_DUPLICATE_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
    "rows": pl.Int64,
}
_OVERLAP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "valid_from": pl.Date,
    "status": pl.Utf8,
    "filing_session": pl.Date,
    "next_ticker": pl.Utf8,
    "next_exchange": pl.Utf8,
    "next_valid_from": pl.Date,
}
_AFTER_DELISTING_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "effective_on": pl.Date,
}
_STATEMENT_PERIOD_SCHEMA: dict[str, Any] = {
    "cik": pl.Utf8,
    "fact_name": pl.Utf8,
    "period_end": pl.Date,
    "period_days": pl.Int64,
    "period_start": pl.Date,
    "filing_accession": pl.Utf8,
}
_STATEMENT_DERIVED_SCHEMA: dict[str, Any] = {
    "cik": pl.Utf8,
    "period_end": pl.Date,
    "period_days": pl.Int64,
    "filing_accession": pl.Utf8,
    "fact_name": pl.Utf8,
    "value": pl.Float64,
    "left_value": pl.Float64,
    "right_value": pl.Float64,
}
_STATEMENT_BASIS_SCHEMA: dict[str, Any] = {"basis": pl.Utf8, "rows": pl.Int64}
_GAP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "first_bar": pl.Date,
    "last_bar": pl.Date,
    "bars": pl.Int64,
    "missing_sessions": pl.Int64,
    "longest_run": pl.Int64,
}
_LISTED_WITHOUT_BARS_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "class_title": pl.Utf8,
    "valid_from": pl.Date,
    "delisting_form": pl.Utf8,
    "delisting_filed_at": pl.Datetime("us", "UTC"),
    "delisting_effective_on": pl.Date,
}
_DELISTED_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "class_title": pl.Utf8,
    "form": pl.Utf8,
    "filed_at": pl.Datetime("us", "UTC"),
    "end_session": pl.Date,
    "effective_on": pl.Date,
}


@dataclass(frozen=True)
class IngestStatus:
    """One source's ingest record at `t` (see the module docstring)."""

    source: str
    last_ok_finished_at: datetime | None
    last_ok_cursor: str | None
    latest_status: str | None
    latest_started_at: datetime | None
    latest_message: str | None


@dataclass(frozen=True)
class Coverage:
    """Bars at `session` for the names live then, and the store's bar range."""

    session: date
    live: tuple[str, ...]
    missing: tuple[str, ...]
    share: float
    first_bar: date | None
    last_bar: date | None
    names_with_bars: int


@dataclass(frozen=True, eq=False)
class BarGaps:
    """Interior bar holes, one row per security that has any."""

    rows: pl.DataFrame

    @property
    def names_with_gaps(self) -> int:
        """How many securities have at least one hole."""
        return self.rows.height

    @property
    def missing_sessions(self) -> int:
        """Missing sessions summed over every security."""
        return int(self.rows["missing_sessions"].sum())


@dataclass(frozen=True)
class Unclassifiable:
    """Universe rule 1's missing-data names known at `t`."""

    unclassifiable: tuple[str, ...]
    unclassified: tuple[str, ...]

    @property
    def count(self) -> int:
        """Both groups together."""
        return len(self.unclassifiable) + len(self.unclassified)


@dataclass(frozen=True)
class StaticReliance:
    """Securities resting on `snapshot_static` rows at `t`."""

    ids: tuple[str, ...]
    by_table: dict[str, int]

    @property
    def count(self) -> int:
        """How many securities rely on at least one static row."""
        return len(self.ids)


@dataclass(frozen=True, eq=False)
class DelistedNames:
    """Securities whose current listing is delisted at `t`."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many securities are delisted."""
        return self.frame.height


@dataclass(frozen=True, eq=False)
class ListedWithoutBars:
    """Securities listed at `t` on a row that starts after a Form 25 and has
    no bar known at `t` (#1460; module docstring), one row per security."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many securities are listed with no bars after a Form 25."""
        return self.frame.height


@dataclass(frozen=True, eq=False)
class PriceJumps:
    """The price-jump review list at `t` (#787), one row per jump, only sessions
    before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The jumps the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


@dataclass(frozen=True, eq=False)
class SharesOutliers:
    """The shares-outlier review list at `t` (#845), one row per out-of-line
    fact, only `as_of_date`s before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The out-of-line facts the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


@dataclass(frozen=True, eq=False)
class AcceptedSameDayPairs:
    """Same-start listing pairs the owner accepted (#855), one row per pair in
    `non_overlapping_listings`' violation shape; each would fail that rule
    without its `universe.accepted_same_day_pairs` entry."""

    frame: pl.DataFrame


@dataclass(frozen=True)
class StatementCoverage:
    """Statement-facts freshness and derived share at `t` (module
    docstring, #660, T77c). `fresh`/`total` are over `universe_as_of(T)`
    names (`total`); `derived`/`reported` count every known `gross_profit`
    row by `basis`, one per `(cik, period_end, period_days)`, over every
    CIK with a `securities` row known at `t` -- not only `universe_as_of`'s
    members, so a name excluded from today's universe (delisted, stale
    shares, ...) still counts here."""

    fresh: int
    total: int
    derived: int
    reported: int

    @property
    def share(self) -> float:
        """Coverage share, 0.0 when `total` is 0 (this module's usual
        empty-population convention, as `Coverage.share`)."""
        return self.fresh / self.total if self.total else 0.0

    @property
    def derived_share(self) -> float:
        """Derived share of known `gross_profit` rows, 0.0 when none."""
        n = self.derived + self.reported
        return self.derived / n if n else 0.0


@dataclass(frozen=True)
class StatementFacts:
    """Statement-facts health at `t` (#660, T77c). `enabled` is
    `edgar.statement_facts_enabled`; `coverage` is `None` while it is off
    (nothing to report on, and `universe_as_of` is not run for nothing).
    `counts` is `statement_counts` of the latest EDGAR run's message,
    empty when it names none (the switch has always been off, or the run
    predates T77b)."""

    enabled: bool
    coverage: StatementCoverage | None
    counts: dict[str, int]

    @property
    def vintage_late(self) -> int:
        """`statement_vintage_late` from the latest run's counts, 0 if
        unnamed."""
        return self.counts.get("vintage_late", 0)


@dataclass(frozen=True, eq=False)
class FilingEvents:
    """Filing-events health (#1358, T164e; module docstring). `enabled` is
    `edgar.filing_events_enabled`; `per_form` has one row per stored form
    (`form`, `rows`, `latest_accepted_at`), empty when the table is; `counts`
    is `filing_event_counts` of the latest EDGAR run's message, empty when it
    names none."""

    enabled: bool
    per_form: pl.DataFrame
    counts: dict[str, int]

    @property
    def warnings(self) -> tuple[str, ...]:
        """The two warnings: `items_missing` and `clock_suspect`, each
        while its count in the latest run is positive."""
        return tuple(
            f"filing_events_{name}: {self.counts[name]}"
            for name in ("items_missing", "clock_suspect")
            if self.counts.get(name, 0) > 0
        )


_REFERENCE_MISSING_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "cik": pl.Utf8,
    "ticker": pl.Utf8,
    "security_id": pl.Utf8,
    "rule": pl.Int64,
    "rule_name": pl.Utf8,
    "reason": pl.Utf8,
}
_COVERAGE_SAMPLE_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "members": pl.Int64,
    "references": pl.Int64,
    "reference_missing": pl.Int64,
    "listed": pl.Int64,
    "late_known": pl.Int64,
    "late_share": pl.Float64,
}
_LATE_KNOWN_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "valid_from": pl.Date,
    "provenance": pl.Utf8,
    "first_known_at": pl.Datetime("us", "UTC"),
}
#: The companies-snapshot provenances, the (c) cross-check's rows.
_SNAPSHOT_PROVENANCES: frozenset[str] = frozenset({"snapshot", STATIC})


@dataclass(frozen=True)
class RateSeriesHealth:
    """One stored series, its latest FRED run and report-only warnings."""

    series: str
    rows: int
    latest_observation_date: date | None
    latest_vintage_date: date | None
    latest_known_at: datetime | None
    revised_keys: int
    longest_gap_sessions: int
    run_counts: dict[str, int]
    stale_sessions: int | None
    last_daily_late_rows: int
    implausible_rows: int
    max_stale_sessions: int

    @property
    def warnings(self) -> tuple[str, ...]:
        """Quality warnings; none is an integrity failure."""
        out: list[str] = []
        if self.revised_keys:
            out.append(f"revised keys: {self.revised_keys}")
        if self.last_daily_late_rows:
            out.append(f"last daily late rows: {self.last_daily_late_rows}")
        if self.longest_gap_sessions > self.max_stale_sessions:
            out.append(f"longest gap: {self.longest_gap_sessions} sessions")
        if self.implausible_rows:
            out.append(f"outside plausible range: {self.implausible_rows}")
        return tuple(out)


@dataclass(frozen=True)
class RatesHealth:
    """Rate summaries for configured or stored series."""

    series: tuple[RateSeriesHealth, ...]


@dataclass(frozen=True)
class BorrowHealth:
    """The borrow-status block (module docstring, "Borrow status").
    `shortable` and `easy_to_borrow` are `None` with no observation;
    `stale_sessions` too."""

    enabled: bool
    rows: int
    assets: int
    latest_observation: datetime | None
    latest_known_at: datetime | None
    last_run_status: str | None
    run_counts: dict[str, int]
    shortable: int | None
    easy_to_borrow: int | None
    stale_sessions: int | None
    max_stale_sessions: int

    @property
    def warnings(self) -> tuple[str, ...]:
        """Report-only warnings; none is an integrity failure."""
        out: list[str] = []
        if not self.enabled:
            out.append("borrow.enabled is off: `ingest --source all` takes no snapshot")
        if self.latest_observation is None:
            if self.enabled:
                out.append("no borrow snapshot observed")
        elif self.stale_sessions is not None and self.stale_sessions > self.max_stale_sessions:
            out.append(
                f"latest observation {self.latest_observation.isoformat()} is "
                f"{self.stale_sessions} sessions old; maximum {self.max_stale_sessions}"
            )
        return tuple(out)


_FIGI_DISAGREEMENT_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "class_title": pl.Utf8,
    "ours": pl.Utf8,
    "figi_type": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
}
#: The OpenFIGI `securityType`/`securityType2` words that name a common
#: equity (`Common Stock`, `REIT`), matched as whole words, case-insensitively.
_FIGI_COMMON_WORDS: tuple[str, ...] = ("common stock", "reit")
#: ... and the other instruments the spec names, a disagreement with `common`.
_FIGI_OTHER_WORDS: tuple[str, ...] = (
    "note",
    "bond",
    "preferred",
    "warrant",
    "unit",
    "depositary receipt",
)
#: Our classifications the converse applies to (the spec's list).
_FIGI_MAY_BE_COMMON: frozenset[str] = frozenset({UNCLASSIFIABLE, "depositary", "debt"})


@dataclass(frozen=True, eq=False)
class FigiTypes:
    """The `figi_types` block at `t` (module docstring, "OpenFIGI types").

    `rows` names every type disagreement, one row per security whose latest
    observation disagrees with its latest classification: both types, the
    current listing's title and the observation's `known_at`. `observations`
    is how many securities have a stored row known at `t` (mapped or not);
    `unmapped` how many of their latest rows are `mapped = FALSE`. `worst` is
    the gated number, disagreements plus unmapped; `max_disagreements` is
    `openfigi.max_disagreements` as the block was computed -- only while it
    is set does the block fail."""

    max_disagreements: int | None
    observations: int
    unmapped: int
    rows: pl.DataFrame

    @property
    def disagreements(self) -> int:
        """How many securities' latest type disagrees with their classification."""
        return self.rows.height

    @property
    def worst(self) -> int:
        """The gated count: named disagreements plus unmapped securities."""
        return self.disagreements + self.unmapped

    @property
    def checked(self) -> bool:
        """Whether any observation is known at `t`: with none, the block
        checked nothing and never passes a set gate."""
        return self.observations > 0

    @property
    def failed(self) -> bool:
        """While `max_disagreements` is set: nothing observed at `t`, or
        `worst` over it. Never while it is unset."""
        if self.max_disagreements is None:
            return False
        return not self.checked or self.worst > self.max_disagreements

    @property
    def verdict(self) -> str:
        """`nothing checked`, `<n> disagreements, <n> unmapped (report-only)`,
        then `pass (max <m>)` or `fail (max <m>)` while a threshold is set."""
        if not self.checked:
            return "nothing checked: fail" if self.failed else "nothing checked"
        head = f"{self.disagreements} disagreements, {self.unmapped} unmapped"
        if self.max_disagreements is None:
            return f"{head} (report-only)"
        return f"{head}: {'fail' if self.failed else 'pass'} (max {self.max_disagreements})"


def _figi_word(text: str, words: tuple[str, ...]) -> bool:
    """Whether `text` (lower-cased) holds one of `words` as a whole word."""
    return any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) for word in words)


def _figi_is_common(security_type: str, security_type2: str) -> bool | None:
    """Whether an OpenFIGI type names a common equity (`Common Stock`,
    `REIT`); False for the other instruments the spec names (a note, bond,
    preferred, warrant, unit or depositary receipt), even beside a common
    word, since the instrument is the more specific reading; None when it
    names neither (module docstring). `securityType2` is read beside
    `securityType`, since the response spells a short form there."""
    text = f"{security_type} {security_type2}".lower()
    if _figi_word(text, _FIGI_OTHER_WORDS):
        return False
    if _figi_word(text, _FIGI_COMMON_WORDS):
        return True
    return None


def figi_types(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings | None = None
) -> FigiTypes:
    """The `figi_types` block at `t` (module docstring)."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    session = last_completed_session(t)
    current = _current_from(listing_ends_as_of(conn, t, settings), session)
    return _figi_types_report(conn, t, session, settings, current, classifications_as_of(conn, t))


def _figi_types_report(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    settings: Settings,
    current: dict[str, dict[str, Any]],
    classes: pl.DataFrame,
) -> FigiTypes:
    """`health_report`'s own entry to the block: `current` is its current
    listing per security and `classes` its latest classification per
    security, already read. As the ingest maps live names alone, only a
    security whose current listing covers `session` is counted (a delisted
    name's last observation must not weigh on the gate forever)."""
    max_disagreements = settings.openfigi.max_disagreements
    if "openfigi_types" not in _present_tables(conn):
        return FigiTypes(
            max_disagreements, 0, 0, pl.DataFrame([], schema=_FIGI_DISAGREEMENT_SCHEMA)
        )
    latest: dict[str, tuple[str, str, str, bool, datetime]] = {}
    for sid, ticker, stype, stype2, mapped, known_at in conn.execute(
        "SELECT security_id, ticker, security_type, security_type2, mapped, known_at "
        "FROM openfigi_types WHERE known_at <= ? ORDER BY known_at, security_id",
        [t],
    ).fetchall():
        latest[sid] = (ticker, stype, stype2, bool(mapped), known_at)
    ours_by_sid = {r["security_id"]: r["security_type"] for r in classes.iter_rows(named=True)}
    rows: list[dict[str, Any]] = []
    observations = 0
    unmapped = 0
    for sid in sorted(latest):
        listing = current.get(sid)
        if listing is None or not _covers(listing, session):
            continue
        observations += 1
        ticker, figi_type, figi_type2, mapped, known_at = latest[sid]
        if not mapped:
            unmapped += 1
            continue
        ours = ours_by_sid.get(sid)
        theirs = _figi_is_common(figi_type, figi_type2)
        disagrees = (ours == _COMMON and theirs is False) or (
            ours in _FIGI_MAY_BE_COMMON and theirs is True
        )
        if not disagrees:
            continue
        rows.append(
            {
                "security_id": sid,
                "ticker": ticker,
                "class_title": listing["class_title"],
                "ours": ours,
                "figi_type": figi_type,
                "known_at": ensure_tz_aware_utc(known_at, field_name="known_at"),
            }
        )
    return FigiTypes(
        max_disagreements,
        observations,
        unmapped,
        pl.DataFrame(rows, schema=_FIGI_DISAGREEMENT_SCHEMA),
    )


_INDEX_SAMPLE_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "beyond_key": pl.Boolean,
    "members": pl.Int64,
    "hits": pl.Int64,
    "misses": pl.Int64,
    "unresolved": pl.Int64,
    "hit_share": pl.Float64,
}
_INDEX_HIT_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "ticker": pl.Utf8,
    "security_id": pl.Utf8,
}
_INDEX_MISS_SCHEMA: dict[str, Any] = {
    "session": pl.Date,
    "ticker": pl.Utf8,
    "security_id": pl.Utf8,
    "outcome": pl.Utf8,
    "rule_name": pl.Utf8,
    "reason": pl.Utf8,
    "bucket": pl.Utf8,
}
#: A resolved security the universe neither admits nor excludes (no
#: `securities` row at the session's close, say).
NOT_CONSIDERED = "not_considered"


@dataclass(frozen=True, eq=False)
class IndexMembersCheck:
    """The S&P 500 answer key against the universe on each sample session
    (#1682, T189b; module docstring). A check only: the key is never a
    universe, signal, filter or feature.

    `commit` and `through` are the key's pinned source SHA and last dated
    row (None when it could not be loaded, `error` saying why). `samples`:
    one row per sample session, `beyond_key` true after `through` (nothing
    resolved or counted there), `members` the key's intervals covering it,
    `hits` the ones whose resolved security is a universe member, `misses`
    the rest, `unresolved` the misses no listing row carries, `hit_share`
    hits over members (None beyond the key or with no members). `hits`: one
    row per hit `(session, ticker, security_id)`. `misses`: one row per miss
    with the resolution outcome, the universe's exclusion for the resolved
    security and its `bucket` (`rule_name:reason`, `unresolved` or
    `not_considered`). `max_missing` is `coverage.index_max_missing` as the
    block was computed: only while it is set does the block fail."""

    commit: str | None
    through: date | None
    error: str | None
    max_missing: int | None
    samples: pl.DataFrame
    hits: pl.DataFrame
    misses: pl.DataFrame

    @property
    def worst(self) -> int:
        """The most misses on one sample session inside the key (0 for none)."""
        counted = self.samples.filter(~pl.col("beyond_key"))
        return 0 if counted.is_empty() else int(counted["misses"].max())  # type: ignore[arg-type]

    @property
    def checked(self) -> bool:
        """Whether some key interval covered some sample session inside the key."""
        return self.error is None and int(self.samples["members"].sum()) > 0

    @property
    def failed(self) -> bool:
        """While `max_missing` is set: the key could not be loaded, it
        checked nothing (every sample session beyond it, or none covered), or
        some sample session's misses exceed it; a gate never passes on a check
        it did not make. Never while it is unset."""
        if self.max_missing is None:
            return False
        return not self.checked or self.worst > self.max_missing

    @property
    def verdict(self) -> str:
        """The one wording the CLI and page share: `key unavailable (...)`,
        `nothing checked`, then `<n> max missing per session` with
        `report-only`, `pass (max <m>)` or `fail (max <m>)`."""
        if self.error is not None:
            gate = "fail" if self.failed else "report-only"
            return f"key unavailable ({self.error}): {gate}"
        if not self.checked:
            return "nothing checked: fail" if self.failed else "nothing checked"
        head = f"{self.worst} max missing per session"
        if self.max_missing is None:
            return f"{head} (report-only)"
        return f"{head}: {'fail' if self.failed else 'pass'} (max {self.max_missing})"


@dataclass(frozen=True, eq=False)
class CoverageReference:
    """The coverage check against a named reference at `t` (#1541; module
    docstring). `samples`: one row per sample session (member count,
    reference names checked and missing, names listed and late-known, the
    late share). `reference_missing`: one row per reference name missing on
    a sample session, with the universe's exclusion for it. `late_known`: one
    row per late-known security per sample session, with its current listing
    at that session. `snapshot_ciks` and `member_ratio`: the latest sample's
    cross-check against the companies snapshot (`member_ratio` is `None`
    with no sample or no snapshot CIK). `check_enabled` is the setting the
    block was computed under: only then does it fail `HealthReport.ok`."""

    check_enabled: bool
    samples: pl.DataFrame
    reference_missing: pl.DataFrame
    late_known: pl.DataFrame
    snapshot_ciks: int
    member_ratio: float | None
    #: The answer-key check; None while `coverage.index_members` is off.
    index_members: IndexMembersCheck | None = None

    @property
    def failed(self) -> bool:
        """Whether a reference name is missing on some sample session."""
        return not self.reference_missing.is_empty()

    @property
    def checked(self) -> bool:
        """Whether some entry covered some sample session: with none, a block
        with nothing missing checked nothing and is never a pass."""
        return not self.samples.is_empty() and int(self.samples["references"].sum()) > 0

    @property
    def verdict(self) -> str:
        """`pass`, `nothing checked`, `fail (<n>)` while `check_enabled`, or
        `<n> missing (report-only)`: the one wording the CLI and page share."""
        missing = self.reference_missing.height
        if self.failed:
            return f"fail ({missing})" if self.check_enabled else f"{missing} missing (report-only)"
        return "pass" if self.checked else "nothing checked"


@dataclass(frozen=True, eq=False)
class IntegrityCheck:
    """One integrity rule: passed when `violations` is empty."""

    rule: str
    violations: pl.DataFrame

    @property
    def passed(self) -> bool:
        """True when nothing violates the rule."""
        return self.violations.is_empty()


_FILENAME_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "valid_from": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}


@dataclass(frozen=True)
class FilenameListings:
    """Filing-name rows known at T and the names without a traded bar in span."""

    rows: int
    only_listing: int
    no_bars: pl.DataFrame


_DUPLICATE_DIVIDENDS_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ex_date": pl.Date,
    "amount": pl.Float64,
    "source_action_id": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
    "verdict": pl.Utf8,
}


@dataclass(frozen=True)
class DuplicateDividends:
    """Every equal-amount cash dividend key live at `t` and its class under
    the reviewed list (`store.dividend_reviews.equal_amount_groups`; module
    docstring, `duplicate_dividends`). Repaired keys are not kept."""

    groups: tuple[EqualAmountGroup, ...]

    def _of(self, group_class: str) -> tuple[EqualAmountGroup, ...]:
        return tuple(g for g in self.groups if g.group_class == group_class)

    @property
    def accepted(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `distinct`: two declared payments of one amount."""
        return self._of("accepted")

    @property
    def unresolved(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `unresolved`: the known gap, reported, never hidden."""
        return self._of("unresolved")

    @property
    def pending(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `duplicate` still holding both ids: a repair not run yet
        (or deferred by its `--to`). A CLI warning, not a failure."""
        return self._of("pending")

    @property
    def violations(self) -> pl.DataFrame:
        """One row per identity of every `violation` key: the key, the id,
        its `known_at` and the listed verdict (null for an unlisted key)."""
        rows = [
            {
                "security_id": g.security_id,
                "ex_date": g.ex_date,
                "amount": g.amount,
                "source_action_id": i.source_action_id,
                "known_at": ensure_tz_aware_utc(i.known_at, field_name="known_at"),
                "verdict": None if g.reviewed is None else g.reviewed.verdict,
            }
            for g in self._of("violation")
            for i in g.identities
        ]
        return pl.DataFrame(rows, schema=_DUPLICATE_DIVIDENDS_SCHEMA)


def duplicate_dividends(
    conn: duckdb.DuckDBPyConnection, t: datetime, reviewed: Sequence[ReviewedGroup]
) -> DuplicateDividends:
    """The `duplicate_dividends` block at `t`: `equal_amount_groups` over the
    `dividend` rows `live_actions_as_of(conn, t)` returns and `reviewed`
    (the packaged list is read at the edge, `health_report`)."""
    t = _validate_t(t)
    groups = equal_amount_groups(live_actions_as_of(conn, t), reviewed)
    return DuplicateDividends(tuple(g for g in groups if g.group_class != "repaired"))


_SUCCESSOR_CHAIN_SCHEMA: dict[str, Any] = {
    "predecessor_id": pl.Utf8,
    "successor_id": pl.Utf8,
    "evidence_accession": pl.Utf8,
    "evidence_form": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
    "handover": pl.Date,
    "ratio": pl.Float64,
    "price_break": pl.Boolean,
}

#: One row per predecessor fact dated after the hand-over a read returns under
#: the successor (#821; the subsidiary case).
_PREDECESSOR_FACT_SCHEMA: dict[str, Any] = {
    "predecessor_id": pl.Utf8,
    "fact_name": pl.Utf8,
    "as_of_date": pl.Date,
    "value": pl.Float64,
}

#: A successor-chain rule's violations: the two ids, the row's stamp and a
#: one-line detail (module docstring, `successor_chains`).
_CHAIN_RULE_SCHEMA: dict[str, Any] = {
    "predecessor_id": pl.Utf8,
    "successor_id": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
    "detail": pl.Utf8,
}

#: `chain_candidates` on the run row: `none`, or `N: id, id`.
_CHAIN_CANDIDATES = re.compile(r"\bchain_candidates: (none|\d+:(?: [^;]*)?)")


@dataclass(frozen=True, eq=False)
class SuccessorChains:
    """The live `successor_chains` rows at `t` (#821, T187c), each with the
    hand-over day and the close ratio across it from the stored bars, the
    predecessor facts dated after the hand-over, and the latest EDGAR run's
    candidates beside them. A price break is a warning, never a failure."""

    frame: pl.DataFrame
    predecessor_facts: pl.DataFrame
    candidates: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        """How many live chains there are."""
        return self.frame.height

    @property
    def breaks(self) -> tuple[str, ...]:
        """The successors whose close ratio is out of bounds."""
        return tuple(self.frame.filter(pl.col("price_break"))["successor_id"].to_list())


def chain_candidates_from_message(message: str | None) -> tuple[str, ...]:
    """The successor ids the latest EDGAR run named under `chain_candidates`
    (ingest's clause), empty when it names none or the message is `None`."""
    if not message:
        return ()
    match = _CHAIN_CANDIDATES.search(message)
    if match is None or match.group(1) == "none":
        return ()
    _, _, ids = match.group(1).partition(":")
    return tuple(part.strip() for part in ids.split(",") if part.strip())


def _chain_amount(
    settings: Settings, predecessor_last: float | None, successor_first: float | None
) -> tuple[float | None, bool]:
    """The close ratio across a hand-over and whether it breaks
    `master.chain_max_price_ratio` (the ratio or its inverse)."""
    if predecessor_last is None or successor_first is None or predecessor_last == 0:
        return None, False
    ratio = successor_first / predecessor_last
    maximum = settings.master.chain_max_price_ratio
    return ratio, ratio > maximum or ratio < 1 / maximum


def _listing_rows(listings: pl.DataFrame, security_ids: Sequence[str]) -> list[dict[str, Any]]:
    """`listings`' rows whose `security_id` is in `security_ids` (an as-of
    frame, one row per security and revision)."""
    wanted = set(security_ids)
    return [row for row in listings.iter_rows(named=True) if row["security_id"] in wanted]


def _raw_prices(
    conn: duckdb.DuckDBPyConnection, t: datetime, security_ids: Sequence[str]
) -> pl.DataFrame:
    """Raw `prices_daily` rows known at `t`, the latest revision per bar and
    traded only: the successor-chain block's own read (#821, T187c). It never
    uses `prices_as_of`, which T187b re-keys by chain, or the predecessor's
    last close vanishes and `chain_price_break` and
    `chain_predecessor_no_bar_after_handover` go blind."""
    return _traded(_latest_as_of(conn, "prices_daily", _PRICE_KEY, t, security_ids))


def _raw_facts(
    conn: duckdb.DuckDBPyConnection, t: datetime, security_ids: Sequence[str]
) -> pl.DataFrame:
    """Raw `facts` rows known at `t`, the latest revision per key: the
    successor-chain block's own read (#821, T187c), never `facts_as_of`, which
    T187b re-keys to the successor, so the predecessor's post-hand-over facts
    stay visible next to the chain."""
    return _latest_as_of(conn, "facts", _FACT_KEY, t, security_ids)


def successor_chains(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    candidates: Sequence[str] = (),
) -> SuccessorChains:
    """The `successor_chains` block at `t` (#821, T187c), store-only and over
    raw rows (never through the chained reads): each live chain, its
    hand-over day and close ratio, the predecessor facts after it, and the
    latest EDGAR run's candidates. A bare date raises `TypeError`, a naive
    datetime `ValueError`."""
    t = _validate_t(t)
    live = live_chains_as_of(conn, t)
    if not live:
        return SuccessorChains(
            pl.DataFrame([], schema=_SUCCESSOR_CHAIN_SCHEMA),
            pl.DataFrame([], schema=_PREDECESSOR_FACT_SCHEMA),
            tuple(candidates),
        )
    ids = sorted({row["predecessor_id"] for row in live} | {row["successor_id"] for row in live})
    listings = listings_as_of(conn, t)
    bars = _raw_prices(conn, t, ids)
    facts = _raw_facts(conn, t, sorted({row["predecessor_id"] for row in live}))
    first: dict[str, tuple[date, float]] = {}
    last: dict[str, tuple[date, float]] = {}
    for bar in bars.iter_rows(named=True):
        sid = bar["security_id"]
        if sid not in first or bar["session"] < first[sid][0]:
            first[sid] = (bar["session"], bar["close"])
        if sid not in last or bar["session"] > last[sid][0]:
            last[sid] = (bar["session"], bar["close"])
    rows: list[dict[str, Any]] = []
    fact_rows: list[dict[str, Any]] = []
    for chain in live:
        predecessor, successor = chain["predecessor_id"], chain["successor_id"]
        succ_listings = _listing_rows(listings, [successor])
        succ_listings.sort(key=lambda r: (r["valid_from"], r["known_at"]))
        handover = succ_listings[0]["valid_from"] if succ_listings else None
        pred_close = last.get(predecessor, (None, None))[1]
        succ_close = first.get(successor, (None, None))[1]
        ratio, price_break = _chain_amount(settings, pred_close, succ_close)
        rows.append(
            {
                "predecessor_id": predecessor,
                "successor_id": successor,
                "evidence_accession": chain["evidence_accession"],
                "evidence_form": chain["evidence_form"],
                "known_at": chain["known_at"],
                "handover": handover,
                "ratio": ratio,
                "price_break": price_break,
            }
        )
        if handover is not None:
            for fact in facts.iter_rows(named=True):
                if fact["security_id"] == predecessor and fact["as_of_date"] > handover:
                    fact_rows.append(
                        {
                            "predecessor_id": predecessor,
                            "fact_name": fact["fact_name"],
                            "as_of_date": fact["as_of_date"],
                            "value": fact["value"],
                        }
                    )
    rows.sort(key=lambda r: r["successor_id"])
    fact_rows.sort(key=lambda r: (r["predecessor_id"], r["as_of_date"], r["fact_name"]))
    return SuccessorChains(
        pl.DataFrame(rows, schema=_SUCCESSOR_CHAIN_SCHEMA),
        pl.DataFrame(fact_rows, schema=_PREDECESSOR_FACT_SCHEMA),
        tuple(candidates),
    )


def _chain_rule_rows(conn: duckdb.DuckDBPyConnection, t: datetime) -> dict[str, pl.DataFrame]:
    """Every successor-chain rule's violations at `t` (#821, T187c), over raw
    rows: live chain rows against the successor's `securities` and listing
    rows, the predecessor's last listing row, and the stored bars."""
    live = live_chains_as_of(conn, t)
    empty = pl.DataFrame([], schema=_CHAIN_RULE_SCHEMA)
    violations: dict[str, list[dict[str, Any]]] = {
        CHAIN_IDS_DISTINCT_CIKS: [],
        CHAIN_KNOWN_AT_LE_INGESTED_AT: [],
        CHAIN_KNOWN_AT_GE_SUCCESSOR_ROWS: [],
        CHAIN_SUCCESSOR_LISTED_UNDER_PREDECESSOR_TICKER: [],
        CHAIN_PREDECESSOR_NO_BAR_AFTER_HANDOVER: [],
    }
    if not live:
        return {rule: empty for rule in violations}
    listings = listings_as_of(conn, t)
    securities = securities_as_of(conn, t)
    security_known = {
        row["security_id"]: row["known_at"] for row in securities.iter_rows(named=True)
    }

    def violation(rule: str, chain: Mapping[str, Any], detail: str) -> None:
        violations[rule].append(
            {
                "predecessor_id": chain["predecessor_id"],
                "successor_id": chain["successor_id"],
                "known_at": chain["known_at"],
                "detail": detail,
            }
        )

    predecessor_bars: dict[str, list[date]] = defaultdict(list)
    successor_first_bar: dict[str, date] = {}
    wanted_bars = sorted(
        {row["predecessor_id"] for row in live} | {row["successor_id"] for row in live}
    )
    for bar in _raw_prices(conn, t, wanted_bars).iter_rows(named=True) if wanted_bars else ():
        sid = bar["security_id"]
        predecessor_bars[sid].append(bar["session"])
        if sid not in successor_first_bar or bar["session"] < successor_first_bar[sid]:
            successor_first_bar[sid] = bar["session"]
    for chain in live:
        predecessor, successor = chain["predecessor_id"], chain["successor_id"]
        if security_cik(predecessor) == security_cik(successor):
            violation(CHAIN_IDS_DISTINCT_CIKS, chain, f"{predecessor} and {successor}")
        if chain["known_at"] > chain["ingested_at"]:
            violation(
                CHAIN_KNOWN_AT_LE_INGESTED_AT,
                chain,
                f"known_at {chain['known_at'].isoformat()} > "
                f"ingested_at {chain['ingested_at'].isoformat()}",
            )
        succ_rows = _listing_rows(listings, [successor])
        succ_rows.sort(key=lambda r: (r["valid_from"], r["known_at"]))
        succ_security = security_known.get(successor)
        succ_first_listing = succ_rows[0] if succ_rows else None
        for label, stamp in (
            ("securities", succ_security),
            ("listing", succ_first_listing["known_at"] if succ_first_listing else None),
        ):
            if stamp is not None and chain["known_at"] < stamp:
                violation(
                    CHAIN_KNOWN_AT_GE_SUCCESSOR_ROWS,
                    chain,
                    f"known_at {chain['known_at'].isoformat()} < successor {label} "
                    f"{stamp.isoformat()}",
                )
        pred_rows = _listing_rows(listings, [predecessor])
        pred_rows.sort(key=lambda r: (r["valid_from"], r["known_at"]))
        if pred_rows:
            last_pred = pred_rows[-1]
            if not any(
                row["ticker"] == last_pred["ticker"] and row["exchange"] == last_pred["exchange"]
                for row in succ_rows
            ):
                violation(
                    CHAIN_SUCCESSOR_LISTED_UNDER_PREDECESSOR_TICKER,
                    chain,
                    f"{successor} is not listed {last_pred['ticker']}/{last_pred['exchange']}",
                )
        succ_bar = successor_first_bar.get(successor)
        if succ_bar is not None:
            after = [
                session for session in predecessor_bars.get(predecessor, []) if session >= succ_bar
            ]
            if after:
                violation(
                    CHAIN_PREDECESSOR_NO_BAR_AFTER_HANDOVER,
                    chain,
                    f"{predecessor} has a bar on {min(after).isoformat()} "
                    f"at/after {succ_bar.isoformat()}",
                )
    return {
        rule: pl.DataFrame(rows, schema=_CHAIN_RULE_SCHEMA) for rule, rows in violations.items()
    }


@dataclass(frozen=True, eq=False)
class HealthReport:
    """Everything the health command and page show (module docstring)."""

    t: datetime
    session: date
    ingests: tuple[IngestStatus, ...]
    coverage: Coverage
    gaps: BarGaps
    survivorship: SurvivorshipGap
    unclassifiable: Unclassifiable
    static_reliance: StaticReliance
    delisted: DelistedNames
    listed_without_bars: ListedWithoutBars
    price_jumps: PriceJumps
    shares_outliers: SharesOutliers
    accepted_same_day_pairs: AcceptedSameDayPairs
    settings: dict[str, Any]
    statement: StatementFacts
    filing_events: FilingEvents
    rates: RatesHealth
    borrow: BorrowHealth
    figi_types: FigiTypes
    duplicate_dividends: DuplicateDividends
    filename_listings: FilenameListings
    successor_chains: SuccessorChains
    integrity: tuple[IntegrityCheck, ...]
    #: `None` when the block was not computed ("not computed", never a pass).
    coverage_reference: CoverageReference | None = None

    @property
    def failures(self) -> tuple[str, ...]:
        """The failing integrity rules, in `INTEGRITY_RULES` order, then
        `coverage_reference_names` when the block was computed with
        `coverage.check_enabled` on and a reference name is missing, then
        `index_members_missing` when the block's answer-key check failed
        (`IndexMembersCheck.failed`: only while `coverage.index_max_missing`
        is set), then `figi_types_disagreements` when the `figi_types` block
        failed (`FigiTypes.failed`: only while `openfigi.max_disagreements`
        is set)."""
        failed = tuple(check.rule for check in self.integrity if not check.passed)
        block = self.coverage_reference
        if block is not None and block.check_enabled and block.failed:
            failed += (COVERAGE_REFERENCE_NAMES,)
        if block is not None and block.index_members is not None and block.index_members.failed:
            failed += (INDEX_MEMBERS_MISSING,)
        if self.figi_types.failed:
            failed += (FIGI_TYPES_DISAGREEMENTS,)
        return failed

    @property
    def ok(self) -> bool:
        """True when nothing in `failures` fails."""
        return not self.failures


def health_report(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings | None = None,
    *,
    jumps_before: date | None = None,
    with_coverage_reference: bool | None = None,
    reviewed: Sequence[ReviewedGroup] | None = None,
    index_key: IndexMembers | None = None,
) -> HealthReport:
    """Every metric and integrity rule at `t` (module docstring). `settings`
    defaults to `get_settings()` and is passed to every derived read. Listing
    ends, securities and classifications are read once and shared.
    `jumps_before` limits the price-jump list to sessions before it, and the
    shares-outlier list to `as_of_date`s before it. `with_coverage_reference`
    computes the `coverage_reference` block when true, never when false, and
    when `None` (the default) only while `coverage.check_enabled` is on or
    `coverage.index_max_missing` is set. `reviewed` is the duplicate-dividend
    list, by default the packaged one (`store.dividend_reviews.reviewed_groups`).
    `index_key` is the answer key, by default the packaged one
    (`store.index_members.load_index_members`)."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    session = last_completed_session(t)
    listings = listing_ends_as_of(conn, t, settings)
    current = _current_from(listings, session)
    securities = securities_as_of(conn, t)
    classes = classifications_as_of(conn, t)
    overlaps, accepted_pairs = _overlapping_listings(listings, settings)
    ingests = last_ingests(conn, t)
    dividends = duplicate_dividends(conn, t, reviewed_groups() if reviewed is None else reviewed)
    chain_candidates = next(
        (
            chain_candidates_from_message(status.latest_message)
            for status in ingests
            if status.source == EDGAR
        ),
        (),
    )
    return HealthReport(
        t=t,
        session=session,
        ingests=ingests,
        coverage=_coverage(conn, t, session, current, securities, classes),
        gaps=bar_gaps(conn, t),
        survivorship=survivorship_gap(conn, t, settings),
        unclassifiable=_unclassifiable(securities, classes),
        static_reliance=_static_reliance(securities, classes, current),
        delisted=_delisted_names(current),
        listed_without_bars=_listed_without_bars(conn, t, session, listings, current),
        price_jumps=_price_jumps(conn, t, settings, jumps_before),
        shares_outliers=_shares_outliers(conn, t, settings, jumps_before),
        accepted_same_day_pairs=AcceptedSameDayPairs(accepted_pairs),
        settings={
            "liquidity_rule_enabled": settings.universe.liquidity_rule_enabled,
            "fill_price": settings.execution.fill_price,
        },
        statement=_statement_facts_report(conn, t, settings, ingests),
        filing_events=_filing_events_report(conn, settings, ingests),
        rates=_rates_report(conn, t, settings),
        borrow=_borrow_report(conn, t, session, settings),
        figi_types=_figi_types_report(conn, t, session, settings, current, classes),
        duplicate_dividends=dividends,
        filename_listings=_filename_listings(conn, t, listings),
        successor_chains=successor_chains(conn, t, settings, chain_candidates),
        integrity=_integrity_checks(conn, t, settings, listings, overlaps, dividends),
        coverage_reference=(
            _coverage_reference(conn, t, settings, listings, index_key)
            if (
                settings.coverage.computed_by_default
                if with_coverage_reference is None
                else with_coverage_reference
            )
            else None
        ),
    )


def _filename_listings(
    conn: duckdb.DuckDBPyConnection, t: datetime, listings: pl.DataFrame
) -> FilenameListings:
    """Count securities whose listing live at T is a filing-name row, and spans
    without any traded bar at T."""
    all_rows = list(listings.iter_rows(named=True))
    named = [row for row in all_rows if row["provenance"] == "filing_name"]
    live = _current_from(listings, last_completed_session(t)).values()  # #1662: live at T
    only = sum(row["provenance"] == "filing_name" for row in live)
    if not named:
        return FilenameListings(0, only, pl.DataFrame([], schema=_FILENAME_SCHEMA))
    bars = prices_as_of(conn, t, sorted({row["security_id"] for row in named}), traded_only=True)
    sessions: dict[str, list[date]] = defaultdict(list)
    for bar in bars.iter_rows(named=True):
        sessions[bar["security_id"]].append(bar["session"])
    no_bars = []
    for row in named:
        end = row["end_session"]
        if not any(
            row["valid_from"] <= day and (end is None or day <= end)
            for day in sessions[row["security_id"]]
        ):
            no_bars.append({key: row[key] for key in _FILENAME_SCHEMA})
    return FilenameListings(len(named), only, pl.DataFrame(no_bars, schema=_FILENAME_SCHEMA))


def _filename_listing_valid_from(listings: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Check 10-K dates and Form 25's first-session last-resort dates."""
    rows = listings.filter(pl.col("provenance") == "filing_name")
    if rows.is_empty():
        return pl.DataFrame([], schema=_FILENAME_SCHEMA)
    first = first_session(settings)
    bad = []
    for row in rows.iter_rows(named=True):
        expected = session_of(row["known_at"])
        is_form25 = row["valid_from"] == first
        if (is_form25 and row["valid_from"] > expected) or (
            not is_form25 and row["valid_from"] != expected
        ):
            bad.append({key: row[key] for key in _FILENAME_SCHEMA})
    return pl.DataFrame(bad, schema=_FILENAME_SCHEMA)


def _price_jumps(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, before: date | None
) -> PriceJumps:
    frame = price_jumps_as_of(conn, t, settings=settings)
    if before is not None:
        frame = frame.filter(pl.col("session") < before)
    return PriceJumps(frame, before)


def _shares_outliers(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings, before: date | None
) -> SharesOutliers:
    frame = shares_as_of(conn, t, None, settings).outliers
    if before is not None:
        frame = frame.filter(pl.col("as_of_date") < before)
    return SharesOutliers(frame, before)


#: `statement_<name>: <N>` (T77b's run-message convention, not yet built:
#: `ingest._source_counts`'s `"label: N"` shape and `cli._QUARANTINED`'s
#: pattern, generalised over every `statement_*` run-row count the spec
#: names -- `held`, `unstampable`, `vintage_late`, `conflicts`, `restated`,
#: `non_usd`, `malformed`, `derived`, `none` -- so this parser needs no
#: change whichever of them, or in whatever order, a message carries.
_STATEMENT_COUNT = re.compile(r"\bstatement_(\w+): (\d+)\b")


def statement_counts(message: str | None) -> dict[str, int]:
    """Every `statement_<name>: <N>` figure in an EDGAR run's message
    (T77b, #660), keyed by `name` with the `statement_` prefix stripped
    (e.g. `{"held": 3, "vintage_late": 0, "derived": 2}`). Empty when
    `message` is `None` or names none (the switch has always been off, or
    the run predates T77b)."""
    if not message:
        return {}
    return {name: int(n) for name, n in _STATEMENT_COUNT.findall(message)}


def statement_coverage(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings | None = None
) -> StatementCoverage:
    """`StatementCoverage` at `t` (module docstring), computed whether or
    not `edgar.statement_facts_enabled` is on. `health_report` skips this
    while the switch is off: there is nothing in the table to report on,
    and no reason to pay for `universe_as_of`."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    return _statement_coverage(conn, t, settings)


def _statement_coverage(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> StatementCoverage:
    if "statement_facts" not in _present_tables(conn):
        return StatementCoverage(fresh=0, total=0, derived=0, reported=0)
    frame = statement_facts_as_of(conn, t)
    members = universe_as_of(conn, t, settings).members["security_id"].to_list()
    member_frame = frame.filter(pl.col("security_id").is_in(members)) if members else frame.clear()
    session = last_completed_session(t)
    fresh = _fresh_count(member_frame, members, session, settings.universe.max_shares_age_days)
    derived, reported = _gross_profit_basis_counts(frame)
    return StatementCoverage(fresh=fresh, total=len(members), derived=derived, reported=reported)


def _fresh_count(frame: pl.DataFrame, members: Sequence[str], session: date, bound: int) -> int:
    """How many of `members` have both a `revenue` and a `total_assets`
    row in `frame` (already filtered to `members`) whose latest known
    `period_end` is within `bound` days of `session` (module docstring:
    rule 7's own freshness bound, measured the same way rule 7 measures
    it -- from the last completed session, not `t` itself)."""
    if not members or frame.is_empty():
        return 0
    latest = (
        frame.filter(pl.col("fact_name").is_in(["revenue", "total_assets"]))
        .group_by(["security_id", "fact_name"])
        .agg(pl.col("period_end").max())
    )
    by_sid: dict[str, dict[str, date]] = defaultdict(dict)
    for row in latest.iter_rows(named=True):
        by_sid[row["security_id"]][row["fact_name"]] = row["period_end"]
    fresh = 0
    for sid in members:
        facts = by_sid.get(sid)
        if (
            facts
            and "revenue" in facts
            and "total_assets" in facts
            and all((session - pe).days <= bound for pe in facts.values())
        ):
            fresh += 1
    return fresh


def _gross_profit_basis_counts(frame: pl.DataFrame) -> tuple[int, int]:
    """`(derived, reported)` counts of every known `gross_profit` row in
    `frame` (every CIK with a `securities` row known at `t`, per
    `_statement_coverage`'s caller), deduplicated to one per `(cik,
    period_end, period_days)` (`frame` may repeat a dual-class issuer's
    row once per class)."""
    if frame.is_empty():
        return 0, 0
    gross_profit = frame.filter(pl.col("fact_name") == "gross_profit").unique(
        ["cik", "period_end", "period_days"]
    )
    if gross_profit.is_empty():
        return 0, 0
    derived = int((gross_profit["basis"] == "derived").sum())
    reported = int((gross_profit["basis"] == "reported").sum())
    return derived, reported


def _statement_facts_report(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    ingests: tuple[IngestStatus, ...],
) -> StatementFacts:
    message = next((i.latest_message for i in ingests if i.source == "edgar"), None)
    enabled = settings.edgar.statement_facts_enabled
    return StatementFacts(
        enabled=enabled,
        coverage=_statement_coverage(conn, t, settings) if enabled else None,
        counts=statement_counts(message),
    )


#: `filing_events_<name>: <N>` (T164e's run-message convention, #1358).
_EVENT_COUNT = re.compile(r"\bfiling_events_(\w+): (\d+)\b")

_EVENT_FORM_SCHEMA: dict[str, Any] = {
    "form": pl.Utf8,
    "rows": pl.Int64,
    "latest_accepted_at": pl.Datetime("us", "UTC"),
}


def filing_event_counts(message: str | None) -> dict[str, int]:
    """Every `filing_events_<name>: <N>` figure in an EDGAR run's message
    (T164e, #1358), keyed by `name` with the prefix stripped. Empty when
    `message` is `None` or names none (the switch has always been off)."""
    if not message:
        return {}
    return {name: int(n) for name, n in _EVENT_COUNT.findall(message)}


def clock_suspect(accepted_at: datetime, filed: date) -> bool:
    """The clock detector (#1358, "The clock"): whether a stamp fits only a
    moved clock, given the submissions payload's filing date. Early: accepted
    on a session before `EDGAR_FILING_DATE_CUTOFF` New York and dated a later
    day. Late: accepted on a New York date after its filing date. A health
    finding, never a repair; it catches only the moves that cross the
    filing-date rule."""
    local = ensure_tz_aware_utc(accepted_at, field_name="accepted_at").astimezone(_NEW_YORK)
    day = local.date()
    if day > filed:
        return True
    return is_session(day) and local.time() < EDGAR_FILING_DATE_CUTOFF and filed > day


def _filing_events_report(
    conn: duckdb.DuckDBPyConnection, settings: Settings, ingests: tuple[IngestStatus, ...]
) -> FilingEvents:
    message = next((i.latest_message for i in ingests if i.source == "edgar"), None)
    if "filing_events" in _present_tables(conn):
        found = conn.execute(
            "SELECT form, count(*), max(accepted_at) FROM filing_events GROUP BY form ORDER BY form"
        ).fetchall()
    else:
        found = []
    rows = [{"form": f, "rows": int(n), "latest_accepted_at": at} for f, n, at in found]
    return FilingEvents(
        enabled=settings.edgar.filing_events_enabled,
        per_form=pl.DataFrame(rows, schema=_EVENT_FORM_SCHEMA),
        counts=filing_event_counts(message),
    )


_RATE_COUNT = re.compile(r"\brates_(\w+)=(\d+)\b")
_RATE_STALE = re.compile(r"\bstale_sessions=(\d+)\b|\bis (\d+) sessions behind\b")


def _rates_report(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> RatesHealth:
    """Per-series stored coverage and the latest run visible at `t`."""
    if "rates" not in _present_tables(conn):
        return RatesHealth(())
    stored = conn.execute(
        "SELECT series, observation_date, value, vintage_date, known_at FROM rates "
        "ORDER BY series, observation_date, known_at"
    ).fetchall()
    runs = conn.execute(
        "SELECT mode, chunk_cursor, message, rows_added FROM ingestion_runs "
        "WHERE source = 'fred' AND coalesce(finished_at, started_at) <= ? "
        "ORDER BY coalesce(finished_at, started_at), run_id",
        [t],
    ).fetchall()
    by_series: dict[str, list[tuple[date, float, date, datetime]]] = defaultdict(list)
    for series, day, value, vintage, stamp in stored:
        by_series[series].append((day, value, vintage, stamp))
    result: list[RateSeriesHealth] = []
    lower, upper = settings.rates.plausible_range_percent
    sessions = all_sessions()
    for series in sorted(set(settings.rates.series) | set(by_series)):
        rows = by_series.get(series, [])
        date_counts = Counter(row[0] for row in rows)
        days = sorted(date_counts)
        gaps = [
            bisect_left(sessions, right) - bisect_right(sessions, left)
            for left, right in pairwise(days)
        ]
        series_runs = [
            (mode, message or "", rows_added)
            for mode, cursor, message, rows_added in runs
            if cursor is not None and cursor.startswith(f"rates;series={series};")
        ]
        counts = (
            {name: int(value) for name, value in _RATE_COUNT.findall(series_runs[-1][1])}
            if series_runs
            else {}
        )
        if series_runs:
            counts["rows_added"] = int(series_runs[-1][2])
        stale_match = _RATE_STALE.search(series_runs[-1][1]) if series_runs else None
        last_daily = next((msg for mode, msg, _ in reversed(series_runs) if mode == MODE), "")
        daily_counts = {name: int(value) for name, value in _RATE_COUNT.findall(last_daily)}
        result.append(
            RateSeriesHealth(
                series=series,
                rows=len(rows),
                latest_observation_date=max(days, default=None),
                latest_vintage_date=max(
                    (row[2] for row in rows if row[2] is not None), default=None
                ),
                latest_known_at=max((row[3] for row in rows if row[3] is not None), default=None),
                revised_keys=sum(count > 1 for count in date_counts.values()),
                longest_gap_sessions=max(gaps, default=0),
                run_counts=counts,
                stale_sessions=int(stale_match[1] or stale_match[2]) if stale_match else None,
                last_daily_late_rows=daily_counts.get("late_rows", 0),
                implausible_rows=sum(
                    row[1] is None or not lower <= row[1] <= upper for row in rows
                ),
                max_stale_sessions=settings.rates.max_stale_sessions,
            )
        )
    return RatesHealth(tuple(result))


_BORROW_COUNT = re.compile(r"\bborrow_(\w+)=(\d+)\b")


def _borrow_report(
    conn: duckdb.DuckDBPyConnection, t: datetime, session: date, settings: Settings
) -> BorrowHealth:
    """The borrow block at `t` (module docstring, "Borrow status")."""
    present = _present_tables(conn)
    rows = assets = 0
    latest_known_at: datetime | None = None
    if "borrow_status" in present:
        found = conn.execute(
            "SELECT count(*), count(DISTINCT asset_id), max(known_at) FROM borrow_status "
            "WHERE known_at <= ?",
            [t],
        ).fetchone()
        if found is not None:
            rows, assets, latest_known_at = int(found[0]), int(found[1]), found[2]
    observation: datetime | None = None
    last: tuple[Any, ...] | None = None
    if "ingestion_runs" in present:
        observed = conn.execute(
            "SELECT max(TRY_CAST(chunk_cursor AS TIMESTAMPTZ)) FROM ingestion_runs "
            "WHERE source = ? AND status = ? AND TRY_CAST(chunk_cursor AS TIMESTAMPTZ) <= ? "
            "AND coalesce(finished_at, started_at) <= ?",
            [ASSETS_SOURCE, OK, t, t],
        ).fetchone()
        observation = None if observed is None else observed[0]
        last = conn.execute(
            "SELECT status, rows_added, message FROM ingestion_runs "
            "WHERE source = ? AND coalesce(finished_at, started_at) <= ? "
            "ORDER BY coalesce(finished_at, started_at) DESC, run_id DESC LIMIT 1",
            [ASSETS_SOURCE, t],
        ).fetchone()
    counts: dict[str, int] = {}
    if last is not None:
        counts = {name: int(value) for name, value in _BORROW_COUNT.findall(last[2] or "")}
        counts["rows_added"] = int(last[1] or 0)
    shortable = easy = stale = None
    if observation is not None:
        observation = ensure_tz_aware_utc(observation, field_name="observation")
        if "borrow_status" in present:
            frame = borrow_status_as_of(conn, observation).filter(pl.col("status") == "active")
            shortable = int(frame["shortable"].sum())
            easy = int(frame["easy_to_borrow"].sum())
        sessions = all_sessions()
        stale = max(
            0,
            bisect_right(sessions, session)
            - bisect_right(sessions, last_completed_session(observation)),
        )
    return BorrowHealth(
        enabled=settings.borrow.enabled,
        rows=rows,
        assets=assets,
        latest_observation=observation,
        latest_known_at=latest_known_at,
        last_run_status=None if last is None else str(last[0]),
        run_counts=counts,
        shortable=shortable,
        easy_to_borrow=easy,
        stale_sessions=stale,
        max_stale_sessions=settings.borrow.max_stale_sessions,
    )


def sample_sessions(t: datetime, settings: Settings | None = None) -> tuple[date, ...]:
    """The coverage check's sample sessions at `t` (#1541): the last XNYS
    session of each `coverage.sample_months` month, from
    `coverage.sample_from` (or the calendar's first session, if later) to the
    last session completed at `t`, ascending."""
    t = _validate_t(t)
    cfg = (settings if settings is not None else get_settings()).coverage
    last = last_completed_session(t)
    out: list[date] = []
    first = max(cfg.sample_from, all_sessions()[0])  # never before the calendar
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        if month in cfg.sample_months:
            session = last_session_of_month(year, month)
            if first <= session <= last:
                out.append(session)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(out)


def coverage_reference(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings | None = None,
    index_key: IndexMembers | None = None,
) -> CoverageReference:
    """The coverage check against the named reference at `t` (#1541; module
    docstring), computed whatever `coverage.check_enabled` says: one
    `universe_as_of` per sample session, so callers ask for it on demand.
    `index_key` is the answer key (#1682), by default the packaged one."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    return _coverage_reference(conn, t, settings, listing_ends_as_of(conn, t, settings), index_key)


def _covers(row: dict[str, Any], session: date) -> bool:
    """Whether a `listing_ends_as_of` row covers `session`: it starts on or
    before it and is not ended before it (listed; a transfer with no end
    yet; or an end on or after `session`)."""
    if row["valid_from"] > session:
        return False
    if row["status"] == LISTED:
        return True
    end = row["end_session"]
    if end is None:
        return bool(row["status"] == TRANSFERRED)
    return bool(end >= session)


_ListingKey = tuple[str, str, str, date]


def _revisions(
    conn: duckdb.DuckDBPyConnection, t: datetime
) -> dict[_ListingKey, list[tuple[datetime, bool]]]:
    """Per listing key, every revision known at `t` as `(known_at, live)`,
    oldest first (`live` false for a retraction, #859)."""
    live = "NOT retracted" if has_retracted(conn, "listings") else "TRUE"
    rows = conn.execute(
        f"""
        SELECT security_id, ticker, exchange, valid_from, known_at, {live}
        FROM listings WHERE known_at <= ?
        ORDER BY known_at
        """,
        [t],
    ).fetchall()
    out: dict[_ListingKey, list[tuple[datetime, bool]]] = defaultdict(list)
    for sid, ticker, exchange, start, at, is_live in rows:
        out[(sid, ticker, exchange, start)].append((at, bool(is_live)))
    return out


def _visible_at(revisions: list[tuple[datetime, bool]], close: datetime) -> bool:
    """Whether a read at `close` sees the key: its latest revision known by
    then exists and is not a retraction (`_latest_as_of`'s rule)."""
    index = bisect_right([at for at, _ in revisions], close)
    return index > 0 and revisions[index - 1][1]


def _same_ticker(stored: str, reference: str) -> bool:
    """A stored ticker is a reference entry's: equal, or one Alpaca symbol
    under two filer spellings (`BRK-B`, `BRK.B`)."""
    return stored.strip().upper() == reference or same_alpaca_symbol(stored, reference)


def _reference_exclusion(
    ref: ReferenceName,
    session: date,
    members: pl.DataFrame,
    exclusions: dict[str, dict[str, Any]],
    cik_of: dict[str, str],
    rows_by_sid: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """`None` when `ref` is a member on `session` (its CIK under its ticker,
    compared as Alpaca symbols), else its `reference_missing` row: the
    exclusion of the CIK's security whose listing rows (known at `t`, started
    by `session`) carry the ticker; else, when the CIK is a member under
    another ticker, that ticker (rule null); else the exclusion of the
    primary id (the CIK itself); else of the CIK's first security; else
    `no_security` (no `securities` row for the CIK at the session's close)."""
    tickers = members.filter(pl.col("cik") == ref.cik)["ticker"].to_list()
    if any(_same_ticker(ticker, ref.ticker) for ticker in tickers):
        return None
    candidates = sorted(sid for sid in exclusions if cik_of.get(sid) == ref.cik)
    matched = [
        sid
        for sid in candidates
        if any(
            row["valid_from"] <= session and _same_ticker(row["ticker"], ref.ticker)
            for row in rows_by_sid.get(sid, [])
        )
    ]
    base = {"session": session, "cik": ref.cik, "ticker": ref.ticker}
    excluded = matched[0] if matched else None
    if excluded is None and tickers:
        return {
            **base,
            "security_id": None,
            "rule": None,
            "rule_name": None,
            "reason": "ticker:" + ",".join(sorted(tickers)),
        }
    if excluded is None and ref.cik in candidates:
        excluded = ref.cik
    if excluded is None and candidates:
        excluded = candidates[0]
    if excluded is None:
        return {
            **base,
            "security_id": None,
            "rule": None,
            "rule_name": None,
            "reason": "no_security",
        }
    found = exclusions[excluded]
    return {
        **base,
        "security_id": excluded,
        "rule": found["rule"],
        "rule_name": found["rule_name"],
        "reason": found["reason"],
    }


def _load_key(index_key: IndexMembers | None) -> tuple[IndexMembers | None, str | None]:
    """The answer key and None, or None and why it could not be loaded (an
    absent, emptied or malformed packaged file is reported, never raised)."""
    if index_key is not None:
        return index_key, None
    try:
        return load_index_members(), None
    except FileNotFoundError:
        return None, "file absent"
    except (OSError, ValueError, csv.Error) as exc:
        # IndexMembersError and UnicodeDecodeError are ValueErrors.
        return None, f"{type(exc).__name__}: {exc}"


def _key_listings(listings: pl.DataFrame, key: IndexMembers) -> pl.DataFrame:
    """Every listing row of the securities with some row carrying a key
    ticker (`normalize_ticker`): all `resolve` needs, every row of each kept
    so a row's span still ends at its security's next row. Taken once per
    block, not per sample session."""
    wanted = {normalize_ticker(i.ticker) for i in key.intervals}
    carried = {
        ticker
        for ticker in listings["ticker"].drop_nulls().unique().to_list()
        if normalize_ticker(ticker) in wanted
    }
    sids = listings.filter(pl.col("ticker").is_in(sorted(carried)))["security_id"].unique()
    return listings.filter(pl.col("security_id").is_in(sids.to_list()))


def _index_session(
    key: IndexMembers,
    session: date,
    listings: pl.DataFrame,
    member_ids: set[str],
    exclusions: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """One sample session of the answer-key check: its `samples` row, hits
    and misses (`IndexMembersCheck`). A hit is the resolved security being a
    member, by `security_id`, so a second class's own miss counts."""
    if session > key.through:
        row = {
            "session": session,
            "beyond_key": True,
            "members": 0,
            "hits": 0,
            "misses": 0,
            "unresolved": 0,
            "hit_share": None,
        }
        return row, [], []
    hits: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    for res in resolve(key.intervals, session, listings):
        sid = res.security_id
        if sid is not None and sid in member_ids:
            hits.append({"session": session, "ticker": res.ticker, "security_id": sid})
            continue
        found = exclusions.get(sid) if sid is not None else None
        if sid is None:
            rule_name, reason, bucket = None, None, UNRESOLVED
        elif found is None:
            rule_name, reason, bucket = None, None, NOT_CONSIDERED
        else:
            rule_name, reason = found["rule_name"], found["reason"]
            bucket = f"{rule_name}:{reason}"
        misses.append(
            {
                "session": session,
                "ticker": res.ticker,
                "security_id": sid,
                "outcome": res.outcome,
                "rule_name": rule_name,
                "reason": reason,
                "bucket": bucket,
            }
        )
    members = len(hits) + len(misses)
    row = {
        "session": session,
        "beyond_key": False,
        "members": members,
        "hits": len(hits),
        "misses": len(misses),
        "unresolved": sum(m["bucket"] == UNRESOLVED for m in misses),
        "hit_share": len(hits) / members if members else None,
    }
    return row, hits, misses


def _coverage_reference(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    listings: pl.DataFrame,
    index_key: IndexMembers | None = None,
) -> CoverageReference:
    refs = settings.coverage.references()
    key, key_error = _load_key(index_key) if settings.coverage.index_members else (None, None)
    key_listings = _key_listings(listings, key) if key is not None else listings
    index_samples: list[dict[str, Any]] = []
    index_hits: list[dict[str, Any]] = []
    index_misses: list[dict[str, Any]] = []
    cik_of = {
        r["security_id"]: r["cik"]
        for r in securities_as_of(conn, t).select("security_id", "cik").iter_rows(named=True)
    }
    rows_by_sid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in listings.iter_rows(named=True):
        rows_by_sid[row["security_id"]].append(row)
    revisions = _revisions(conn, t)

    def history(row: dict[str, Any]) -> list[tuple[datetime, bool]]:
        return revisions.get(
            (row["security_id"], row["ticker"], row["exchange"], row["valid_from"]), []
        )

    def known_by(row: dict[str, Any]) -> datetime | None:
        return next((at for at, is_live in history(row) if is_live), None)

    samples: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    late: list[dict[str, Any]] = []
    for session in sample_sessions(t, settings):
        close = session_close(session)
        universe = universe_as_of(conn, close, settings)
        exclusions = {r["security_id"]: r for r in universe.exclusions.iter_rows(named=True)}
        checked = [ref for ref in refs if ref.covers(session)]
        found = [
            entry
            for ref in checked
            if (
                entry := _reference_exclusion(
                    ref, session, universe.members, exclusions, cik_of, rows_by_sid
                )
            )
            is not None
        ]
        missing.extend(found)
        if key is not None:
            sample, hit_rows, miss_rows = _index_session(
                key, session, key_listings, set(universe.members["security_id"]), exclusions
            )
            index_samples.append(sample)
            index_hits.extend(hit_rows)
            index_misses.extend(miss_rows)
        current = _current_from(listings, session)
        listed = sorted(sid for sid, row in current.items() if _covers(row, session))
        late_here = 0
        for sid in listed:
            seen = (
                _visible_at(history(row), close)
                for row in rows_by_sid[sid]
                if _covers(row, session)
            )
            if not any(seen):
                head = current[sid]
                late_here += 1
                late.append(
                    {
                        "session": session,
                        "security_id": sid,
                        "ticker": head["ticker"],
                        "exchange": head["exchange"],
                        "valid_from": head["valid_from"],
                        "provenance": head["provenance"],
                        "first_known_at": known_by(head),
                    }
                )
        samples.append(
            {
                "session": session,
                "members": universe.members.height,
                "references": len(checked),
                "reference_missing": len(found),
                "listed": len(listed),
                "late_known": late_here,
                "late_share": late_here / len(listed) if listed else 0.0,
            }
        )
    exchanges = set(settings.universe.exchanges)
    snapshot_ciks = len(
        {
            cik_of[row["security_id"]]
            for row in listings.iter_rows(named=True)
            if row["provenance"] in _SNAPSHOT_PROVENANCES
            and row["exchange"] in exchanges
            and row["security_id"] in cik_of
        }
    )
    latest_members = samples[-1]["members"] if samples else None
    return CoverageReference(
        check_enabled=settings.coverage.check_enabled,
        samples=pl.DataFrame(samples, schema=_COVERAGE_SAMPLE_SCHEMA),
        reference_missing=pl.DataFrame(missing, schema=_REFERENCE_MISSING_SCHEMA),
        late_known=pl.DataFrame(late, schema=_LATE_KNOWN_SCHEMA),
        snapshot_ciks=snapshot_ciks,
        member_ratio=(
            latest_members / snapshot_ciks if latest_members is not None and snapshot_ciks else None
        ),
        index_members=(
            IndexMembersCheck(
                commit=None if key is None else key.commit,
                through=None if key is None else key.through,
                error=key_error,
                max_missing=settings.coverage.index_max_missing,
                samples=pl.DataFrame(index_samples, schema=_INDEX_SAMPLE_SCHEMA),
                hits=pl.DataFrame(index_hits, schema=_INDEX_HIT_SCHEMA),
                misses=pl.DataFrame(index_misses, schema=_INDEX_MISS_SCHEMA),
            )
            if settings.coverage.index_members
            else None
        ),
    )


def last_ingests(conn: duckdb.DuckDBPyConnection, t: datetime) -> tuple[IngestStatus, ...]:
    """Per source, the last `ok` run and the latest run whose row is known at
    `t` (finished at or before `t`); `ingest.SOURCES` first, then any other
    source. A `master-retract` run row (mode `retract`, #859) is not an
    ingest and is left out, so it never hides the latest EDGAR run's status
    or quarantine count."""
    t = _validate_t(t)
    runs = conn.execute(
        """
        SELECT source, status, started_at, finished_at, chunk_cursor, message
        FROM ingestion_runs WHERE coalesce(finished_at, started_at) <= ? AND mode <> ?
        ORDER BY started_at, run_id
        """,
        [t, RETRACT],
    ).fetchall()
    by_source: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for run in runs:
        by_source[run[0]].append(run)
    extra = sorted(set(by_source) - set(SOURCES))
    out: list[IngestStatus] = []
    for source in (*SOURCES, *extra):
        rows = by_source.get(source, [])
        ok_rows = [r for r in rows if r[1] == OK and r[3] is not None]
        last_ok = max(ok_rows, key=lambda r: r[3], default=None)
        latest = rows[-1] if rows else None
        out.append(
            IngestStatus(
                source=source,
                last_ok_finished_at=None if last_ok is None else last_ok[3],
                last_ok_cursor=None if last_ok is None else last_ok[4],
                latest_status=None if latest is None else latest[1],
                latest_started_at=None if latest is None else latest[2],
                latest_message=None if latest is None else latest[5],
            )
        )
    return tuple(out)


def _current_from(listings: pl.DataFrame, session: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing with the latest `valid_from` on or before
    `session` (universe rule 2's current listing), from `listing_ends_as_of`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        held = current.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            current[row["security_id"]] = row
    return current


def _is_live(listing: dict[str, Any], session: date) -> bool:
    status, end = listing["status"], listing["end_session"]
    if status == LISTED:
        return True
    return status == TRANSFERRED and (end is None or end >= session)


def coverage(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> Coverage:
    """Bars at the last completed session for the live common and benchmark
    names then, from rows known at `t` (module docstring)."""
    t = _validate_t(t)
    session = last_completed_session(t)
    current = _current_from(listing_ends_as_of(conn, t, settings), session)
    return _coverage(
        conn, t, session, current, securities_as_of(conn, t), classifications_as_of(conn, t)
    )


def _coverage(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    current: dict[str, dict[str, Any]],
    securities: pl.DataFrame,
    classes: pl.DataFrame,
) -> Coverage:
    benchmarks = {r["security_id"] for r in securities.iter_rows(named=True) if r["benchmark"]}
    common = {
        r["security_id"] for r in classes.iter_rows(named=True) if r["security_type"] == _COMMON
    }
    live = tuple(
        sorted(
            sid
            for sid, row in current.items()
            if _is_live(row, session) and (sid in benchmarks or sid in common)
        )
    )
    with_bar = {
        sid
        for (sid,) in conn.execute(
            "SELECT DISTINCT security_id FROM prices_daily WHERE known_at <= ? AND session = ?",
            [t, session],
        ).fetchall()
    }
    missing = tuple(sid for sid in live if sid not in with_bar)
    row = conn.execute(
        """
        SELECT min(session), max(session), count(DISTINCT security_id)
        FROM prices_daily WHERE known_at <= ? AND session <= ?
        """,
        [t, session],
    ).fetchone()
    first_bar, last_bar, names = row if row is not None else (None, None, 0)
    return Coverage(
        session=session,
        live=live,
        missing=missing,
        share=(len(live) - len(missing)) / len(live) if live else 0.0,
        first_bar=first_bar,
        last_bar=last_bar,
        names_with_bars=int(names),
    )


_SESSIONS_VIEW = "_health_sessions"


def bar_gaps(conn: duckdb.DuckDBPyConnection, t: datetime) -> BarGaps:
    """Interior bar holes per security, from bars known at `t` up to the last
    completed session (module docstring), computed in DuckDB against the
    session index. Bars on a non-session are left to `bars_on_sessions`."""
    t = _validate_t(t)
    session = last_completed_session(t)
    sessions = all_sessions()
    index = pl.DataFrame(
        {"session": sessions, "idx": range(len(sessions))},
        schema={"session": pl.Date, "idx": pl.Int64},
    )
    conn.register(_SESSIONS_VIEW, index.to_arrow())
    try:
        found = conn.execute(
            f"""
            WITH bars AS (
                SELECT DISTINCT p.security_id, p.session, s.idx
                FROM prices_daily p JOIN {_SESSIONS_VIEW} s ON p.session = s.session
                WHERE p.known_at <= ? AND p.session <= ?
            ), holes AS (
                SELECT security_id, session, coalesce(
                    idx - lag(idx) OVER (PARTITION BY security_id ORDER BY idx) - 1, 0
                ) AS hole
                FROM bars
            )
            SELECT security_id, min(session), max(session), count(*), sum(hole), max(hole)
            FROM holes GROUP BY security_id HAVING sum(hole) > 0 ORDER BY security_id
            """,
            [t, session],
        ).fetchall()
    finally:
        conn.unregister(_SESSIONS_VIEW)
    rows = [
        {
            "security_id": sid,
            "first_bar": first,
            "last_bar": last,
            "bars": int(bars),
            "missing_sessions": int(missing),
            "longest_run": int(longest),
        }
        for sid, first, last, bars, missing, longest in found
    ]
    return BarGaps(rows=pl.DataFrame(rows, schema=_GAP_SCHEMA))


def unclassifiable(conn: duckdb.DuckDBPyConnection, t: datetime) -> Unclassifiable:
    """Securities known at `t` classified `unclassifiable`, or with no
    classification row known at `t`."""
    t = _validate_t(t)
    return _unclassifiable(securities_as_of(conn, t), classifications_as_of(conn, t))


def _unclassifiable(securities: pl.DataFrame, classes: pl.DataFrame) -> Unclassifiable:
    known = set(securities["security_id"].to_list())
    kinds = {r["security_id"]: r["security_type"] for r in classes.iter_rows(named=True)}
    return Unclassifiable(
        unclassifiable=tuple(sorted(sid for sid in known if kinds.get(sid) == UNCLASSIFIABLE)),
        unclassified=tuple(sorted(known - set(kinds))),
    )


def static_reliance(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> StaticReliance:
    """Securities whose `securities` row, current listing or classification
    as of `t` has `provenance = snapshot_static` (module docstring)."""
    t = _validate_t(t)
    current = _current_from(listing_ends_as_of(conn, t, settings), last_completed_session(t))
    return _static_reliance(securities_as_of(conn, t), classifications_as_of(conn, t), current)


def _static_reliance(
    securities: pl.DataFrame, classes: pl.DataFrame, current: dict[str, dict[str, Any]]
) -> StaticReliance:
    known = set(securities["security_id"].to_list())
    static: dict[str, set[str]] = {
        "securities": {
            r["security_id"] for r in securities.iter_rows(named=True) if r["provenance"] == STATIC
        },
        "listings": {
            sid for sid, row in current.items() if sid in known and row["provenance"] == STATIC
        },
        "classifications": {
            r["security_id"]
            for r in classes.iter_rows(named=True)
            if r["security_id"] in known and r["provenance"] == STATIC
        },
    }
    ids = set().union(*static.values())
    return StaticReliance(
        ids=tuple(sorted(ids)),
        by_table={table: len(names) for table, names in static.items()},
    )


def delisted_names(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> DelistedNames:
    """Securities whose current listing is delisted at `t`, sorted by id."""
    t = _validate_t(t)
    return _delisted_names(
        _current_from(listing_ends_as_of(conn, t, settings), last_completed_session(t))
    )


def _delisted_names(current: dict[str, dict[str, Any]]) -> DelistedNames:
    rows = [
        {
            "security_id": sid,
            "ticker": row["ticker"],
            "exchange": row["exchange"],
            "class_title": row["class_title"],
            "form": row["delisting_form"],
            "filed_at": row["delisting_filed_at"],
            "end_session": row["end_session"],
            "effective_on": row["effective_on"],
        }
        for sid, row in sorted(current.items())
        if row["status"] == DELISTED
    ]
    return DelistedNames(frame=pl.DataFrame(rows, schema=_DELISTED_SCHEMA))


def listed_without_bars(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> ListedWithoutBars:
    """Securities listed at `t` on an exchange row that starts after a
    Form 25 and has no bar known at `t` since (#1460; module docstring),
    sorted by id. A bare date raises `TypeError`, a naive datetime
    `ValueError`."""
    t = _validate_t(t)
    session = last_completed_session(t)
    listings = listing_ends_as_of(conn, t, settings)
    return _listed_without_bars(conn, t, session, listings, _current_from(listings, session))


def _listed_without_bars(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    listings: pl.DataFrame,
    current: dict[str, dict[str, Any]],
) -> ListedWithoutBars:
    delisted_before: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        now = current.get(row["security_id"])
        if (
            now is None
            or now["status"] != LISTED
            or now["exchange"] in OFF_EXCHANGE
            or row["status"] != DELISTED
            or row["valid_from"] >= now["valid_from"]
        ):
            continue
        held = delisted_before.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            delisted_before[row["security_id"]] = row
    if not delisted_before:
        return ListedWithoutBars(frame=pl.DataFrame([], schema=_LISTED_WITHOUT_BARS_SCHEMA))
    ids = sorted(delisted_before)
    marks = ", ".join("?" for _ in ids)
    last_bar: dict[str, date] = {}
    for sid, latest in conn.execute(
        f"""
        SELECT security_id, max(session) FROM prices_daily
        WHERE known_at <= ? AND session <= ? AND security_id IN ({marks})
        GROUP BY security_id
        """,
        [t, session, *ids],
    ).fetchall():
        last_bar[sid] = latest
    rows = [
        {
            "security_id": sid,
            "ticker": current[sid]["ticker"],
            "exchange": current[sid]["exchange"],
            "class_title": current[sid]["class_title"],
            "valid_from": current[sid]["valid_from"],
            "delisting_form": delisted_before[sid]["delisting_form"],
            "delisting_filed_at": delisted_before[sid]["delisting_filed_at"],
            "delisting_effective_on": delisted_before[sid]["effective_on"],
        }
        for sid in ids
        if sid not in last_bar or last_bar[sid] < current[sid]["valid_from"]
    ]
    return ListedWithoutBars(frame=pl.DataFrame(rows, schema=_LISTED_WITHOUT_BARS_SCHEMA))


def integrity_checks(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    reviewed: Sequence[ReviewedGroup] | None = None,
) -> tuple[IntegrityCheck, ...]:
    """Every rule in `INTEGRITY_RULES`, in that order (module docstring);
    `reviewed` as in `health_report`."""
    t = _validate_t(t)
    listings = listing_ends_as_of(conn, t, settings)
    overlaps, _ = _overlapping_listings(listings, settings)
    dividends = duplicate_dividends(conn, t, reviewed_groups() if reviewed is None else reviewed)
    return _integrity_checks(conn, t, settings, listings, overlaps, dividends)


def _integrity_checks(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    listings: pl.DataFrame,
    overlaps: pl.DataFrame,
    dividends: DuplicateDividends,
) -> tuple[IntegrityCheck, ...]:
    """`overlaps` is `_overlapping_listings`' violation frame for `listings`,
    computed once by the caller, which also needs its accepted pairs;
    `dividends` the `duplicate_dividends` block, likewise."""
    window = settings.master.transfer_window_sessions
    violations: dict[str, pl.DataFrame] = {
        KNOWN_AT_NOT_NULL: _count_per_table(conn, "known_at IS NULL"),
        KNOWN_AT_NOT_AFTER_INGESTED_AT: _count_per_table(conn, "known_at > ingested_at"),
        SOURCE_NOT_NULL: _count_per_table(conn, "source IS NULL"),
        PROVENANCE_ALLOWED: _bad_provenance(conn),
        FILENAME_LISTING_VALID_FROM: _filename_listing_valid_from(listings, settings),
        BARS_ON_SESSIONS: _bars_off_sessions(conn),
        NO_DUPLICATE_BARS: _duplicate_bars(conn),
        NON_OVERLAPPING_LISTINGS: overlaps,
        NO_BARS_AFTER_DELISTING: _bars_after_delisting(conn, t, listings, window),
        UNDERIVED_MASTER_ROWS: underived_as_of(conn, t),
        STATEMENT_PERIOD_DAYS: _statement_period_days(conn),
        STATEMENT_DERIVED_MATCHES_COMPONENTS: _statement_derived_matches_components(conn),
        STATEMENT_BASIS_ALLOWED: _statement_basis_allowed(conn),
        **_filing_event_rules(conn, settings),
        SESSION_HOLES: _session_holes(conn, t, settings),
        **_rate_rules(conn),
        **_new_action_stamp_rules(conn),
        DUPLICATE_DIVIDENDS: dividends.violations,
        BORROW_STATUS_UNIQUE: _borrow_duplicates(conn),
        **_chain_rule_rows(conn, t),
    }
    return tuple(IntegrityCheck(rule=rule, violations=violations[rule]) for rule in INTEGRITY_RULES)


_RATE_RULE_SCHEMA: dict[str, Any] = {
    "series": pl.Utf8,
    "observation_date": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}


def _rate_rules(conn: duckdb.DuckDBPyConnection) -> dict[str, pl.DataFrame]:
    """Check every stored rate stamp, unique key and finite value."""
    rows = (
        conn.execute(
            "SELECT series, observation_date, value, vintage_date, known_at FROM rates"
        ).fetchall()
        if "rates" in _present_tables(conn)
        else []
    )
    bad_stamp: list[dict[str, Any]] = []
    bad_value: list[dict[str, Any]] = []
    seen: set[tuple[str, date, datetime]] = set()
    duplicates: list[dict[str, Any]] = []
    # `rate_known_at` is a pure function of the vintage date and walks the
    # calendar; many rows share a vintage, so compute each once per call.
    stamps: dict[date | None, datetime | None] = {}
    for series, day, value, vintage, stamp in rows:
        key = {"series": series, "observation_date": day, "known_at": stamp}
        if vintage not in stamps:
            try:
                stamps[vintage] = rate_known_at(vintage) if vintage is not None else None
            except (ValueError, IndexError):
                stamps[vintage] = None
        correct = stamps[vintage]
        if stamp != correct:
            bad_stamp.append(key)
        if value is None or not math.isfinite(value):
            bad_value.append(key)
        identity = (series, day, stamp)
        if identity in seen:
            duplicates.append(key)
        seen.add(identity)
    return {
        RATE_KNOWN_AT: pl.DataFrame(bad_stamp, schema=_RATE_RULE_SCHEMA),
        RATE_UNIQUE: pl.DataFrame(duplicates, schema=_RATE_RULE_SCHEMA),
        RATE_VALUE_FINITE: pl.DataFrame(bad_value, schema=_RATE_RULE_SCHEMA),
    }


_ACTION_STAMP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
}


def _new_action_stamp_rules(conn: duckdb.DuckDBPyConnection) -> dict[str, pl.DataFrame]:
    """Reject spin-offs before their ex-date proxy and mis-stamped renames."""
    tables = _present_tables(conn)
    bad_spin: list[dict[str, Any]] = []
    if "corporate_actions" in tables:
        rows = conn.execute(
            "SELECT security_id, ex_date, known_at FROM corporate_actions "
            "WHERE action_type = 'spinoff'"
        ).fetchall()
        for security_id, ex_date, stamp in rows:
            floor = spinoff_known_at_floor(ex_date)
            if floor is None or stamp < floor:
                bad_spin.append({"security_id": security_id, "known_at": stamp})
    bad_change: list[dict[str, Any]] = []
    if "symbol_changes" in tables:
        # The first row of an identity (the table's identity index) is stamped
        # by the date-only rule; a later row is a revision, stamped at its own
        # `ingested_at` after the first (`ingest._add_symbol_changes`).
        rows = conn.execute(
            """
            SELECT security_id, process_date, known_at, ingested_at,
                ROW_NUMBER() OVER w AS rn, FIRST_VALUE(known_at) OVER w AS first_known
            FROM symbol_changes
            WINDOW w AS (
                PARTITION BY security_id, source_action_id,
                    CASE WHEN source_action_id = '' THEN old_symbol ELSE '' END,
                    CASE WHEN source_action_id = '' THEN new_symbol ELSE '' END,
                    CASE WHEN source_action_id = '' THEN process_date ELSE NULL END
                ORDER BY known_at
            )
            """
        ).fetchall()
        for security_id, process_date, stamp, ingested, rn, first_known in rows:
            if rn == 1:
                try:
                    expected = announcement_date_known_at(process_date)
                except (ValueError, IndexError):
                    expected = None
                ok = stamp == expected
            else:
                ok = stamp == ingested and stamp > first_known
            if not ok:
                bad_change.append({"security_id": security_id, "known_at": stamp})
    return {
        SPINOFF_KNOWN_AT_FLOOR: pl.DataFrame(bad_spin, schema=_ACTION_STAMP_SCHEMA),
        SYMBOL_CHANGE_KNOWN_AT: pl.DataFrame(bad_change, schema=_ACTION_STAMP_SCHEMA),
    }


_BORROW_KEY_SCHEMA: dict[str, Any] = {
    "asset_id": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
    "rows": pl.Int64,
}


def _borrow_duplicates(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per `(asset_id, known_at)` held by more than one
    `borrow_status` row, with how many."""
    if "borrow_status" not in _present_tables(conn):
        return pl.DataFrame(schema=_BORROW_KEY_SCHEMA)
    found = conn.execute(
        "SELECT asset_id, known_at, count(*) FROM borrow_status "
        "GROUP BY asset_id, known_at HAVING count(*) > 1 ORDER BY asset_id, known_at"
    ).fetchall()
    return pl.DataFrame(
        [{"asset_id": a, "known_at": k, "rows": int(n)} for a, k, n in found],
        schema=_BORROW_KEY_SCHEMA,
    )


def _present_tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Table names `conn` actually has, per `dashboard.header.store_freshness`'s
    pattern: a read-only connection never migrates (`store.schema.init_schema`),
    so a store opened before a later `_FACT_TABLES` addition (e.g.
    `statement_facts`, version 9, #660) is missing it until the next writable
    open -- querying it directly would raise `duckdb.CatalogException` instead
    of the loud-but-graceful "run `tradepartner ingest`" health already gives
    for an uninitialised store."""
    rows = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    return {name for (name,) in rows}


def _count_per_table(conn: duckdb.DuckDBPyConnection, condition: str) -> pl.DataFrame:
    present = _present_tables(conn)
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        if table not in present:
            continue
        row = conn.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()
        count = int(row[0]) if row is not None else 0
        if count:
            rows.append({"table": table, "rows": count})
    return pl.DataFrame(rows, schema=_TABLE_COUNT_SCHEMA)


def _bad_provenance(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    present = _present_tables(conn)
    rows: list[dict[str, Any]] = []
    for table in _FACT_TABLES:
        if table not in present:
            continue
        allowed = TABLE_PROVENANCE_VALUES[table]
        marks = ", ".join("?" for _ in allowed)
        found = conn.execute(
            f"""
            SELECT provenance, count(*) FROM {table}
            WHERE provenance IS NULL OR provenance NOT IN ({marks})
            GROUP BY provenance ORDER BY provenance NULLS FIRST
            """,
            list(allowed),
        ).fetchall()
        rows.extend({"table": table, "provenance": p, "rows": int(n)} for p, n in found)
    return pl.DataFrame(rows, schema=_PROVENANCE_SCHEMA)


def _bars_off_sessions(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    sessions = set(all_sessions())
    found = conn.execute(
        "SELECT session, count(*) FROM prices_daily GROUP BY session ORDER BY session"
    ).fetchall()
    rows = [{"session": s, "rows": int(n)} for s, n in found if s not in sessions]
    return pl.DataFrame(rows, schema=_SESSION_SCHEMA)


def _duplicate_bars(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    found = conn.execute(
        """
        SELECT security_id, session, known_at, count(*) FROM prices_daily
        GROUP BY security_id, session, known_at HAVING count(*) > 1
        ORDER BY security_id, session, known_at
        """
    ).fetchall()
    rows = [
        {"security_id": sid, "session": s, "known_at": k, "rows": int(n)} for sid, s, k, n in found
    ]
    return pl.DataFrame(rows, schema=_DUPLICATE_SCHEMA)


def _session_holes(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> pl.DataFrame:
    """#1575, #1550 part c: one row per session hole, ascending (module
    docstring). Rows are counted per session from `prices_daily` known at
    `t` over the history window's own range, then compared with the
    previous calendar session's count in Python, where the threshold and
    the window bounds are visible."""
    if "prices_daily" not in _present_tables(conn):
        return pl.DataFrame([], schema=_SESSION_HOLE_SCHEMA)
    session = last_completed_session(t)
    window = sessions_in_month_window(session, settings.universe.min_history_months)
    if not window:
        return pl.DataFrame([], schema=_SESSION_HOLE_SCHEMA)
    # The window's own first session is judged against its predecessor when
    # the store holds it, so one row before the window starts is read too.
    start = previous_session(window[0])
    found = conn.execute(
        """
        SELECT session, count(*) FROM prices_daily
        WHERE known_at <= ? AND session <= ? AND session >= ?
        GROUP BY session ORDER BY session
        """,
        [t, session, start],
    ).fetchall()
    if not found:
        return pl.DataFrame([], schema=_SESSION_HOLE_SCHEMA)
    counts = {day: int(n) for day, n in found}
    # The store's own first bar known at `t`, read on its own: the window's
    # bound would move `first` past empty sessions at the window's start.
    stored = conn.execute("SELECT min(session) FROM prices_daily WHERE known_at <= ?", [t])
    first = (stored.fetchone() or (min(counts),))[0]
    latest = max(counts)
    share = settings.health.min_session_rows_share
    holes: list[dict[str, Any]] = []
    for day in window:
        if day < first or day > latest:
            # Before the store's first bar (nothing was ingested for it), or
            # after the latest stored session: staleness, not a hole.
            continue
        rows = counts.get(day, 0)
        before = previous_session(day)
        earlier = counts.get(before, 0)
        if rows and rows >= share * earlier:
            continue
        holes.append(
            {
                "session": day,
                "rows": rows,
                "previous_session": before,
                "previous_rows": earlier,
            }
        )
    return pl.DataFrame(holes, schema=_SESSION_HOLE_SCHEMA)


def _by_security(listings: pl.DataFrame) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in listings.iter_rows(named=True):
        out[row["security_id"]].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r["valid_from"], r["exchange"], r["ticker"]))
    return out


def _same_line(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two rows of one ticker from one day: one line tagged with two
    exchanges by its filers (#822), never two lines."""
    return bool(a["valid_from"] == b["valid_from"] and a["ticker"] == b["ticker"])


def _row_kind(row: dict[str, Any]) -> str:
    """`row`'s kind by the resolver's own classification (`EQUITY` or one
    of `NON_EQUITY_KINDS`, or placeholder for no ticker), shared, never
    copied, so a row health reads the same way the resolver would."""
    ticker = str(row["ticker"])
    if is_placeholder_ticker(ticker):
        return _PLACEHOLDER_KIND
    return listing_kind(ticker, row.get("class_title"))


def _resolver_ticker(ticker: str) -> str:
    """`ticker` as `ListingResolver` keys it (#846): `alpaca_symbol`'s
    spelling fold (`BF-A` -> `BF.A`), or the raw ticker when it has none,
    so health compares the same strings the resolver's own same-day-typo
    rule does."""
    return alpaca_symbol(ticker) or ticker


def _held_ticker(
    ordered: list[dict[str, Any]], index: int, day: date, pair_kind: str
) -> str | None:
    """The ticker (resolver-folded) `ordered[index]`'s security held just
    before `day`, by the resolver's own rule: the row immediately before
    `day`, and only when that row is itself `EQUITY` too while
    `pair_kind` is `EQUITY` (either of the pair's own rows; a pair reads
    a non-equity row's ticker no more than the resolver's
    `_same_day_pair` does, #846) -- except a pair with neither row
    `EQUITY`, which the resolver's EQUITY-only path never examines in the
    first place, so there is no resolver opinion for health to drift
    from; its own last ticker, of whatever kind, decides."""
    previous = next((r for r in reversed(ordered[:index]) if r["valid_from"] < day), None)
    if previous is None:
        return None
    if pair_kind == EQUITY and _row_kind(previous) != EQUITY:
        return None
    return _resolver_ticker(str(previous["ticker"]))


def _same_day_typo_pair(
    ordered: list[dict[str, Any]], index: int, current: dict[str, Any], following: dict[str, Any]
) -> bool:
    """True when `current`/`following`'s same-start pair is a cover
    page's filer noise, not a genuine overlap (#846): the ticker the
    security held just before is one of the tied tickers (the resolver's
    own rule, `is_same_day_typo`, shared here, never copied, over the
    same resolver-folded ticker spellings), or the two tickers are one
    Alpaca symbol under different filer spellings (`same_alpaca_symbol`;
    `MOTV U`/`MOTV.U`)."""
    day = current["valid_from"]
    tickers = {
        _resolver_ticker(str(current["ticker"])),
        _resolver_ticker(str(following["ticker"])),
    }
    # Either row being equity puts the pair on the resolver's EQUITY-only
    # path (quant-auditor pass 2 on #846): gating on `current` alone let a
    # mixed equity/non-equity pair's gate depend on which ticker happened
    # to sort first in `ordered`, not on kind.
    current_kind, following_kind = _row_kind(current), _row_kind(following)
    pair_kind = EQUITY if EQUITY in (current_kind, following_kind) else current_kind
    held = _held_ticker(ordered, index, day, pair_kind)
    return is_same_day_typo(held, tickers) or same_alpaca_symbol(
        str(current["ticker"]), str(following["ticker"])
    )


def _finra_temporary_pair(current: dict[str, Any], following: dict[str, Any]) -> bool:
    """True when a same-start pair is one OTC quote under FINRA's
    fifth-letter `D` temporary symbol and its own base symbol (#1720):
    `JFIL` and `JFILD` on one day, both off-exchange, the base a four-letter
    symbol the temporary symbol's own prefix. A reverse split gives the new
    quote the temporary symbol and the OTC feed carries both from the same
    day, so the pair is one line, not two listings. An exchange line never
    carries the temporary symbol, so the rule stays inside `OFF_EXCHANGE`
    (#822)."""
    if current["exchange"] not in OFF_EXCHANGE or following["exchange"] not in OFF_EXCHANGE:
        return False
    shorter, longer = sorted(
        (_resolver_ticker(str(current["ticker"])), _resolver_ticker(str(following["ticker"]))),
        key=len,
    )
    return (
        len(shorter) == FINRA_TEMPORARY_BASE_LENGTH and longer == shorter + FINRA_TEMPORARY_SUFFIX
    )


def _overlap_row(
    current: dict[str, Any], flagged: dict[str, Any], filing_session: date | None
) -> dict[str, Any]:
    return {
        "security_id": current["security_id"],
        "ticker": current["ticker"],
        "exchange": current["exchange"],
        "valid_from": current["valid_from"],
        "status": current["status"],
        "filing_session": filing_session,
        "next_ticker": flagged["ticker"],
        "next_exchange": flagged["exchange"],
        "next_valid_from": flagged["valid_from"],
    }


def _overlapping_listings(
    listings: pl.DataFrame, settings: Settings
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """`non_overlapping_listings`' violations, and the same-start pairs the
    owner accepted in `universe.accepted_same_day_pairs` (#855), both in
    `_OVERLAP_SCHEMA`. An accepted pair is excused from the same-start test
    only, and counts as one line: the late-Form-25 test still runs on its
    rows, against the next exchange line after that day."""
    window_sessions = settings.master.transfer_window_sessions
    accepted_days = {
        parse_accepted_same_day_pair(entry) for entry in settings.universe.accepted_same_day_pairs
    }
    rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for sid, ordered in sorted(_by_security(listings).items()):
        for i, current in enumerate(ordered[:-1]):
            following = ordered[i + 1]
            same_start = current["valid_from"] == following["valid_from"]
            flagged = (
                following
                if same_start
                and not _same_line(current, following)
                and not _same_day_typo_pair(ordered, i, current, following)
                and not _finra_temporary_pair(current, following)
                else None
            )
            filed = current["delisting_filed_at"]
            filing_session = None if filed is None else _filing_session(filed)
            # An accepted day is one line, as the owner reviewed it: for every
            # row of that day (also a ticker's second exchange tag, #822), no
            # same-day row is a second line for the late-Form-25 test.
            accepted_day = (sid, current["valid_from"]) in accepted_days
            one_line_day = current["valid_from"] if accepted_day else None
            if flagged is not None and accepted_day:
                accepted.append(_overlap_row(current, flagged, filing_session))
                flagged = None
            if flagged is None and filing_session is not None:
                exchange_line = next(
                    (
                        r
                        for r in ordered[i + 1 :]
                        if r["exchange"] not in OFF_EXCHANGE
                        and not _same_line(current, r)
                        and r["valid_from"] != one_line_day
                    ),
                    None,
                )
                if (
                    exchange_line is not None
                    and exchange_line["valid_from"] < _window(filing_session, window_sessions)[0]
                ):
                    flagged = exchange_line
            if flagged is not None:
                rows.append(_overlap_row(current, flagged, filing_session))
    return (
        pl.DataFrame(rows, schema=_OVERLAP_SCHEMA),
        pl.DataFrame(accepted, schema=_OVERLAP_SCHEMA),
    )


def _missing_sessions(sessions: tuple[date, ...], after: date, before: date) -> int:
    """XNYS sessions strictly between `after` and `before`."""
    return max(0, bisect_left(sessions, before) - bisect_right(sessions, after))


def _resumed_bars(
    bars: list[date],
    effective_on: date,
    next_start: date | None,
    window_sessions: int,
    sessions: tuple[date, ...],
) -> list[date]:
    """The bars of `bars` (sorted) after `effective_on` and before
    `next_start` that come back after more than `window_sessions` missing
    sessions, and every bar after the first of them; the bars before it are
    the listing's own tail. The first gap is counted from the last bar on or
    before `effective_on`, or from `effective_on` when there is none."""
    start = bisect_right(bars, effective_on)
    previous = bars[start - 1] if start else effective_on
    resumed: list[date] = []
    for session in bars[start:]:
        if next_start is not None and session >= next_start:
            break
        if resumed or _missing_sessions(sessions, previous, session) > window_sessions:
            resumed.append(session)
        previous = session
    return resumed


def _bars_after_delisting(
    conn: duckdb.DuckDBPyConnection, t: datetime, listings: pl.DataFrame, window_sessions: int
) -> pl.DataFrame:
    bounds: list[dict[str, Any]] = []
    for ordered in _by_security(listings).values():
        for row in ordered:
            if row["status"] != DELISTED:
                continue
            later = sorted(
                (
                    o
                    for o in ordered
                    if o["valid_from"] > row["valid_from"] and o["exchange"] not in OFF_EXCHANGE
                ),
                key=lambda o: o["valid_from"],
            )
            next_valid_from = later[0]["valid_from"] if later else None
            if next_valid_from is not None and _row_kind(row) == EQUITY:
                # The resolver's own rule 7 (spec data-foundation.md,
                # "Resolver rules"; #819) only keeps a span running under a
                # later row of the same ticker when both rows are EQUITY,
                # and only from the day that row actually holds the ticker:
                # a same-day filer-typo pair (#846) is tied on `valid_from`,
                # so every row of that day, not just the one `later` sorts
                # first, is checked (#829).
                own_ticker = _resolver_ticker(str(row["ticker"]))
                same_day = [o for o in later if o["valid_from"] == next_valid_from]
                if any(
                    _row_kind(o) == EQUITY and _resolver_ticker(str(o["ticker"])) == own_ticker
                    for o in same_day
                ):
                    continue
            bounds.append({**row, "_next": next_valid_from})
    if not bounds:
        return pl.DataFrame([], schema=_AFTER_DELISTING_SCHEMA)
    ids = sorted({b["security_id"] for b in bounds})
    marks = ", ".join("?" for _ in ids)
    bars: dict[str, list[date]] = defaultdict(list)
    for sid, session in conn.execute(
        f"""
        SELECT DISTINCT security_id, session FROM prices_daily
        WHERE known_at <= ? AND security_id IN ({marks})
        ORDER BY security_id, session
        """,
        [t, *ids],
    ).fetchall():
        bars[sid].append(session)
    sessions = all_sessions()
    rows = [
        {
            "security_id": b["security_id"],
            "session": s,
            "ticker": b["ticker"],
            "exchange": b["exchange"],
            "effective_on": b["effective_on"],
        }
        for b in bounds
        for s in _resumed_bars(
            bars[b["security_id"]], b["effective_on"], b["_next"], window_sessions, sessions
        )
    ]
    return pl.DataFrame(rows, schema=_AFTER_DELISTING_SCHEMA).sort("security_id", "session")


def _statement_period_days(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """`statement_period_days` (module docstring): `period_start` NULL
    exactly when `period_days = 0`, else `period_days = period_end -
    period_start` in days."""
    if "statement_facts" not in _present_tables(conn):
        return pl.DataFrame(schema=_STATEMENT_PERIOD_SCHEMA)
    found = conn.execute(
        """
        SELECT cik, fact_name, period_end, period_days, period_start, filing_accession
        FROM statement_facts
        WHERE (period_days = 0) != (period_start IS NULL)
           OR (period_days != 0 AND period_days != date_diff('day', period_start, period_end))
        ORDER BY cik, fact_name, period_end, filing_accession
        """
    ).fetchall()
    rows = [
        {
            "cik": cik,
            "fact_name": fact_name,
            "period_end": period_end,
            "period_days": int(period_days),
            "period_start": period_start,
            "filing_accession": accession,
        }
        for cik, fact_name, period_end, period_days, period_start, accession in found
    ]
    return pl.DataFrame(rows, schema=_STATEMENT_PERIOD_SCHEMA)


def _statement_derived_matches_components(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """`statement_derived_matches_components` (module docstring): every
    `basis = derived` row must equal the same-filing combination of the two
    stored constituent rows in `ingest.DERIVED_FACTS` (`gross_profit` =
    `revenue - cost_of_revenue`; `revenue_financial` (#1556) =
    `net_interest_income + noninterest_income`), both present (a missing
    component row fails it too). Exact `DOUBLE` equality, no tolerance: the
    ingest derives the row by this same operation at insert time (spec
    decision (e)), so a correct row's stored value and this recomputation are
    bit-identical -- no config threshold is needed, or would mean anything, for
    a check that cannot legitimately differ by any amount on correct data."""
    if "statement_facts" not in _present_tables(conn):
        return pl.DataFrame(schema=_STATEMENT_DERIVED_SCHEMA)
    rows: list[dict[str, Any]] = []
    for fact_name, left_name, right_name, operator in DERIVED_FACTS:
        if operator not in ("+", "-"):
            raise ValueError(f"DERIVED_FACTS {fact_name}: unknown operator {operator!r}")
        found = conn.execute(
            f"""
            SELECT d.cik, d.period_end, d.period_days, d.filing_accession, d.value,
                   l.value, r.value
            FROM statement_facts d
            LEFT JOIN statement_facts l
              ON l.cik = d.cik AND l.period_end = d.period_end AND l.period_days = d.period_days
             AND l.filing_accession = d.filing_accession AND l.fact_name = ?
            LEFT JOIN statement_facts r
              ON r.cik = d.cik AND r.period_end = d.period_end AND r.period_days = d.period_days
             AND r.filing_accession = d.filing_accession AND r.fact_name = ?
            WHERE d.fact_name = ? AND d.basis = 'derived'
              AND (l.value IS NULL OR r.value IS NULL
                   OR d.value != (l.value {operator} r.value))
            ORDER BY d.cik, d.period_end, d.filing_accession
            """,
            [left_name, right_name, fact_name],
        ).fetchall()
        rows.extend(
            {
                "cik": cik,
                "period_end": period_end,
                "period_days": int(period_days),
                "filing_accession": accession,
                "fact_name": fact_name,
                "value": value,
                "left_value": left,
                "right_value": right,
            }
            for cik, period_end, period_days, accession, value, left, right in found
        )
    return pl.DataFrame(rows, schema=_STATEMENT_DERIVED_SCHEMA)


def _statement_basis_allowed(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """`statement_basis_allowed` (module docstring): `basis` outside
    `schema.STATEMENT_FACT_BASIS_VALUES` (the schema's own `CHECK` already
    blocks this, as `_statement_period_days`'s docstring notes)."""
    if "statement_facts" not in _present_tables(conn):
        return pl.DataFrame(schema=_STATEMENT_BASIS_SCHEMA)
    allowed = STATEMENT_FACT_BASIS_VALUES
    marks = ", ".join("?" for _ in allowed)
    found = conn.execute(
        f"""
        SELECT basis, count(*) FROM statement_facts
        WHERE basis IS NULL OR basis NOT IN ({marks})
        GROUP BY basis ORDER BY basis NULLS FIRST
        """,
        list(allowed),
    ).fetchall()
    rows = [{"basis": basis, "rows": int(n)} for basis, n in found]
    return pl.DataFrame(rows, schema=_STATEMENT_BASIS_SCHEMA)


_EVENT_KEY_SCHEMA: dict[str, Any] = {"cik": pl.Utf8, "accession": pl.Utf8, "rows": pl.Int64}
_EVENT_FORM_RULE_SCHEMA: dict[str, Any] = {"form": pl.Utf8, "rows": pl.Int64}


def _filing_event_rules(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> dict[str, pl.DataFrame]:
    """The four `filing_events` rules (module docstring, #1358): offending
    keys (`cik`, `accession`, rows) or, for the form rule, forms and their
    row counts. A store without the table passes all four."""
    if "filing_events" not in _present_tables(conn):
        return {
            FILING_EVENTS_KNOWN_AT_IS_ACCEPTED_AT: pl.DataFrame(schema=_EVENT_KEY_SCHEMA),
            FILING_EVENTS_FORM_ALLOWED: pl.DataFrame(schema=_EVENT_FORM_RULE_SCHEMA),
            FILING_EVENTS_ITEMS_NOT_NULL: pl.DataFrame(schema=_EVENT_KEY_SCHEMA),
            FILING_EVENTS_UNIQUE: pl.DataFrame(schema=_EVENT_KEY_SCHEMA),
        }

    def keys(where: str, group: str = "") -> pl.DataFrame:
        found = conn.execute(
            f"SELECT cik, accession, count(*) FROM filing_events {where} "
            f"GROUP BY cik, accession {group} ORDER BY cik, accession"
        ).fetchall()
        rows = [{"cik": c, "accession": a, "rows": int(n)} for c, a, n in found]
        return pl.DataFrame(rows, schema=_EVENT_KEY_SCHEMA)

    forms = list(settings.edgar.event_forms)
    marks = ", ".join("?" for _ in forms)
    found = conn.execute(
        f"""
        SELECT form, count(*) FROM filing_events
        WHERE form IS NULL OR form NOT IN ({marks})
        GROUP BY form ORDER BY form NULLS FIRST
        """,
        forms,
    ).fetchall()
    bad_forms = pl.DataFrame(
        [{"form": f, "rows": int(n)} for f, n in found], schema=_EVENT_FORM_RULE_SCHEMA
    )
    return {
        FILING_EVENTS_KNOWN_AT_IS_ACCEPTED_AT: keys("WHERE known_at IS DISTINCT FROM accepted_at"),
        FILING_EVENTS_FORM_ALLOWED: bad_forms,
        FILING_EVENTS_ITEMS_NOT_NULL: keys("WHERE items IS NULL"),
        FILING_EVENTS_UNIQUE: keys("", "HAVING count(*) > 1"),
    }

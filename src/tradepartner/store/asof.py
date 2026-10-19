"""As-of read primitives over the point-in-time store (spec req 8).

Every function here takes a tz-aware UTC `t` ("T" in the spec's
"Definitions": *what was knowable at time T?*) and returns only rows with
`known_at <= t`, the **latest revision** per natural key (spec
"Definitions" > Revision: "the row with the greatest `known_at <= T`").
Rows are never updated in place, so "latest revision" is always a query,
never a stored flag. In `securities` and `listings` the latest revision may
be a retraction (#859, `retracted = TRUE`, `store.retraction`): the key then
has no row from that revision's `known_at` on, and the old row still answers
every T before it.

Five of the eight as-of functions the spec lists live here: `prices_as_of`,
`adjusted_prices_as_of`, `facts_as_of`, `listings_as_of` and
`statement_facts_as_of` (#660, T76b), plus `filing_events_as_of` (#1358,
T164d; req 8 lists it after the eight) and `dropped_dividends_as_of`, which
reports the dividends `adjusted_prices_as_of` leaves unapplied (#72) and is
not itself one of the spec's eight. The other three live elsewhere, each
owned by the module that writes its own table: `securities_as_of` in
`store.master` (T8; the security master owns `securities`/`listings`
writes, and `statement_facts_as_of` below calls it rather than duplicating
its latest-revision query), `universe_as_of` in `universe.py` (T13) and
`survivorship_gap` in `gap.py` (T15) -- all three shipped; out of scope
for this module, not later tasks.

**`statement_facts_as_of` has no revision to pick** (spec "Definitions" >
Revision, exception): `statement_facts`'s `UNIQUE (cik, fact_name,
period_end, period_days)` excludes `known_at`, so each key has at most one
row ever -- "latest known at T" and "known at T" coincide, and the read is
a plain `known_at <= t` filter, no `ROW_NUMBER()` needed. The join through
`securities_as_of(t)` on `cik` is what makes this read point-in-time
overall: a CIK with no `securities` row known at `t` contributes no rows
(invisible at `t`, same as everywhere else in this module), and a
multi-class CIK's one statement row is repeated once per `security_id`
`securities_as_of(t)` lists for it at `t` -- so a class added after `t`
does not yet pull the CIK's statement facts in under its own id. A class
`securities_as_of` ever stops listing at some T (a delisted class stays
listed today; #859's retraction is a later task) would drop out here too,
the same way every other function in this module defers to whatever that
one function decides a security's existence is.

**Successor chains (amendment 2026-10-12, #821; plan T187b).** One
`successor_chains` row per chained successor makes one continuous security
out of a 1:1 successor-CIK reorganisation. `live_chains(conn, t)` returns
the `(predecessor_id, successor_id)` pairs whose live, unretracted chain row
is known at `t` (`schema.successor_chains`'s rule: the successor's
unretracted row with the greatest `ingested_at`, unless a retraction was
written after it). The six as-of reads keyed by security
(`prices_as_of`, `adjusted_prices_as_of`, `live_actions_as_of`,
`price_jumps_as_of`, `dropped_dividends_as_of`, `facts_as_of`) apply the
chains **known at `t`** with one shared helper: the predecessor's rows are
re-keyed to the successor and dropped from the predecessor, so a read for
the predecessor returns nothing and one for the successor returns both
histories; a key the two share is the successor's own row. The
`security_ids` filter and the id-keyed `universe.accepted_price_jumps`
entries are mapped the same way, so an entry naming a predecessor still
excuses its re-keyed row under the successor. A chained successor's **own**
bar on a session before its first listing row's `valid_from` reads absent
until a live chain naming that successor is known at `t`: the shares a fill
writes into the rule-9 hole are its history only once the chain explains
them, so a read before the chain's `known_at` must not serve them (the
hiding needs no chain, only that the store names the id as a chained
successor, so a store with no `successor_chains` rows is unaffected).
`listings_as_of`, `securities_as_of`, `statement_facts_as_of`, `filing_events_as_of` and the
delisting/classification reads are **unchanged**: the predecessor stays a
security that rule 2 excludes as delisted. A predecessor fact dated after
the hand-over (the successor's first listing row's `valid_from`) reads under
the successor only while no fact of the successor's own is known at `t` (the
cut-off is the successor fact's `known_at`, not its date), so a subsidiary
that keeps filing a nominal count is cut off by the successor's first own
fact. When no chain is known at `t` the reads build exactly the SQL they
built before this amendment, so a store with no chains is byte-identical.

**Return type: `polars.DataFrame`.** ADR 0004 adopts polars for
dataframes, and it is already a T1 runtime dependency. Every function
builds its frame straight from DuckDB (`conn.execute(sql, params).pl()`),
with a fixed column order (the table's own schema order, or an explicit
`SELECT` list for `adjusted_prices_as_of`) and `ORDER BY` the table's
natural key, so two calls against equivalent data return row-for-row
identical frames -- no Python-side sort needed to compare them (see
`tests/lookahead/test_asof_invariance.py`'s use of `DataFrame.equals`).
Building a Python `list[dict]` per row was measured (by the T6 PR's
reviewer, profiling the truncation-invariance suite) to be the dominant
cost of an earlier version of this module: `.pl()` converts DuckDB's
result directly into Arrow-backed polars columns without ever
materializing per-row Python objects.

**A bare `date` passed as `t` raises `TypeError`; a naive `datetime`
raises `ValueError`.** T is always an instant, never a calendar day on its
own (spec "Definitions": "Never a bare date."): `_validate_t` rejects
anything that is not a `datetime` instance at all (a `date` is not a
`datetime` -- `datetime` is a *subclass* of `date`, not the reverse, so a
plain `date` fails `isinstance(t, datetime)`) with `TypeError`, then hands
a genuine `datetime` to `tradepartner.timeutil.ensure_tz_aware_utc`, which
raises `ValueError` if it is naive and otherwise normalizes it to UTC.

**`borrow_status_as_of` is keyed by the asset, not the symbol, and its
observation is a run row.** `borrow_status` (amendment 2026-10-11, #1506,
ADR 0021) has no `security_id`: a symbol may belong to several Alpaca
`us_equity` assets, so the latest row is picked per `asset_id` and only then
filtered by `symbols` -- filtering first would let a renamed asset return
its stale pre-rename row under its old symbol. The status's *observation*
is read from `ingestion_runs` (`ASSETS_SOURCE`), because rows record change
only: on a day nothing changed no row is written, yet the day was observed.
`present = FALSE` on an asset's latest row means the snapshot at `t` lacked
it, so it reads as absent rather than as its last known flags.

**`listings_as_of` and issue #35 (owner decision: strict `known_at`).**
Spec req 8 says, literally, "All [as-of functions] take tz-aware T and
return only rows with `known_at <= T`" -- with no carve-out for any
particular provenance. `listings_as_of` applies that literally: a
`snapshot_static` listing (fixture ticker `PRE9`, `known_at` 2020-01-15,
`valid_from` 2017-01-03) is invisible before its own `known_at`, exactly
like a `filing` or `snapshot` row would be, even at a T inside its valid
range. The owner chose this (option (b) on
[issue #35](https://github.com/josejuarez96/tradepartner/issues/35)) over
a config-gated exemption that would apply such rows from `valid_from`: a
current snapshot lists survivors only, so the exemption would add
survivorship bias, and a row's `valid_from` can predate the IPO. The
consequence is accepted: pre-2019 `snapshot_static` listings are unknown
before their snapshot fetch, so early backfill coverage is thin. The spec's
`snapshot_static` exception governs which *columns* a consumer may treat
as valid before `known_at` once it already sees the row; it never makes a
row visible early, and no consumer may read `listings` directly to get
around this. `tests/lookahead/harness.py`'s `TruncatedStore` truncates
`listings` uniformly with every other table (no exemption), so the
truncation-invariance tests exercise this directly.

**Adjustment methodology (`adjusted_prices_as_of`).** A split or dividend
adjusts a bar at `session` only when it is **both** known
(`known_at <= t`, latest revision per action identity among rows known at
`t`, not cancelled) **and already effective** (`ex_date <= t`) --
per spec req 8: "Level rules ... adjust shares only for splits with
ex-date <= T, regardless of when the split became known" and the
acceptance line "Split known before T with ex-date after T: ... rule 4
uses the raw close." At `t`, a split whose `ex_date` is still in the
future has not happened yet, so adjusting historical bars for it would
shift price levels for an event that has not occurred; `adjusted_prices_as_of`
must therefore return the raw close for every bar as long as `ex_date > t`,
exactly like the raw-close level rules T13 will apply to `universe_as_of`.
The spec's backfilled-split acceptance criterion still holds under this
combined rule: "Backfilled 2018 split in 2026:
`adjusted_prices_as_of(2019-01-31 close)` adjusts 2017 prices" -- fixture
`SEC_SPLIT_BACKFILLED`'s split has `known_at` 2018-03-14 and `ex_date`
2018-03-15, both `<= ` the probe T (2019-01-31 close), even though it was
only `ingested_at` in a 2026 backfill run; `ingested_at` never gates this
rule.

**Spin-offs (`include_spin_offs`, #1894).** Opt-in and total-return only
(it requires `include_dividends=True`). A `spinoff` row (child in
`security_id`, ratio r in `ratio_or_amount`, parent in `source_action_id`)
is selected by its parent and adjusts the parent as a dividend of D = r x
the child's raw close on E, under the dividend's prior-close rules; it
applies only once the row, the child's bar on E and the parent's prior bar
are all known at `t`. `unvalued_spin_offs_as_of` lists the ones left out.
At the default every reader is byte-identical with or without the rows.

**Action identity, re-dates and cancellations (#108).** An action's
identity is `(security_id, source_action_id)` when the source gave an id,
else `(security_id, action_type, ex_date)` (`source_action_id = ''`). A
re-dated action is a later revision of the same identity with a new
`ex_date`, so the old ex-date applies until the re-date's `known_at` and
the new one after, never both. A latest revision with `cancelled` set
removes the event. Both filters, and `ex_date <= t`, run on the latest
revision as of `t`: filtering first would let an older revision stand in
for a newer one that re-dated the event past `t` or cancelled it.

**"Effective" is exchange-local, not UTC (`_EXCHANGE_TZ`).** `ex_date` is a
calendar `DATE`; `t` is a UTC instant. A split's `ex_date` is the XNYS
session it takes effect on, which is an America/New_York-local concept:
comparing `ex_date <= t`'s *UTC* calendar date is wrong near the UTC/ET
day boundary. For example `t = 2019-02-14T01:00Z` is `2019-02-13T20:00`
America/New_York -- still the *day before* a `2019-02-14` ex-date, even
though `t`'s UTC date already reads `2019-02-14`. `t_session` is therefore
computed as `t.astimezone(_EXCHANGE_TZ).date()`, not `t.date()`.

For each raw bar at `session`, every split with `ex_date > session` and
`ex_date <= t_session` contributes a `1 / ratio` price factor (a split
makes the pre-split share price look artificially high on the old scale,
so historical closes are divided down to the post-split scale); every
dividend (only when `include_dividends=True`) with `ex_date > session` and
`ex_date <= t_session` contributes a `1 - amount / prior_close` factor.
`prior_close` is the raw close of the **latest bar known at `t` with
`session < ex_date`** for that security, found with a DuckDB `ASOF JOIN`
(a range join on the nearest `session` below `ex_date`) rather than a
lookup of the exact XNYS session immediately before `ex_date`: an earlier
version of this function skipped a dividend's factor entirely whenever
that one specific session's bar was not itself known at `t` (e.g. a real
ingest gap), even though an earlier bar was known and would have been the
obviously-correct fallback. The `ASOF JOIN` picks that earlier bar
instead of dropping the dividend's factor. The fallback is bounded
(issue #72): the prior bar counts only if it is among the
`adjust.max_prior_close_gap_sessions` XNYS sessions immediately before the
ex-date (the gap is counted over `calendar.all_sessions`, never
weekdays). Past that, after a long ingest gap or a relisting, a close
weeks or years old would silently mis-size the factor, or fail the whole
query if it is at or below the amount, so the dividend is left unapplied
(`NULL` factor) instead and `dropped_dividends_as_of` reports it, along
with any dividend that has no prior bar at all or whose prior bar or
ex-date lies outside the configured calendar, for `health` to count.
If a split and a dividend share the same `ex_date`, the dividend's
`prior_close` is still the **raw**, pre-split close of the prior session
(the `ASOF JOIN` reads from
`latest_bars`, never from an already-adjusted series) -- this matches the
standard convention that a dividend amount is quoted against the
pre-split price level on its own ex-date, and is simplest to reason
about, since `adjusted_prices_as_of` never adjusts a series and then reads
back from its own output.

Only `open`/`high`/`low`/`close` are adjusted; `volume` is left raw (out
of scope here -- no acceptance criterion in this task requires an
adjusted-volume convention, and the spec's "Data / interfaces" table
documents `prices_daily` volume as raw only).

**Implausible dividend amounts are dropped, not applied (#841).** A
dividend `amount >= prior_close` would make `1 - amount / prior_close`
zero or negative, and DuckDB's `LN()` (used by the cumulative-factor
window function below) *raises* on a non-positive input rather than
returning `-inf`/`NaN`. Such a row is bad source data on one name (Alpaca
attached Carlyle's $25 preferred issue price to CG's common on
2017-09-13, a $22 stock), and raising failed every read that included
the name, so a whole backtest trial. It is left unapplied (`NULL`
factor) like a stale-prior-bar dividend and `dropped_dividends_as_of`
reports it as `implausible_amount`: an amount at or above
`adjust.max_dividend_to_prior_close` (default `1.0`, at most `1.0`)
times the prior close. The bound is checked only on a usable prior close
(the gap reasons come first), and only for a finite amount on a positive,
finite close: a NaN or infinite amount, or a zero or NaN prior close, is
corrupt data of another kind and still raises below.

**Every other event's factor must be positive and finite, or this raises
`ValueError`.** A split `ratio_or_amount` of `0` divides by zero (DuckDB
returns `inf`, not an error, for `1.0 / 0.0`), and so does a NaN or
infinite dividend amount or a zero or NaN prior close. That is bad store
data, not "no factor" (`NULL`, which a dropped dividend produces and which
this function treats as "no adjustment from this event", not an error).
Before ever computing `LN()`, a dedicated query checks every non-`NULL`
event factor for `factor > 0 AND isfinite(factor)`, **and separately**
rejects any
dividend with a negative `ratio_or_amount` even though `1 - (negative) /
prior_close` is itself a perfectly positive, finite number greater than
`1` (a dividend that *raises* the price is not a validation failure the
factor-sign check alone can see -- a negative dividend amount is simply
bad data). The first violation found raises `ValueError` naming the
`security_id`, `ex_date` and the offending `ratio_or_amount`/`factor`,
rather than letting a raw DuckDB `OutOfRangeException` (for the
`LN(0)`/`LN(negative)` case) or a silently wrong `inf`-adjusted price
series (for the zero-ratio case) or a silently wrong price-inflating
series (for the negative-dividend case) reach the caller.

**Why this runs as one SQL statement, not a Python loop per bar.** Every
step -- the latest-revision-as-of-`t` filter for both `prices_daily` and
`corporate_actions`, each event's own factor, one log factor per
`(security_id, ex_date)` (the events sharing an ex-date summed as
`SUM(LN(factor) ORDER BY action_type, ratio_or_amount)`, so a split and a
dividend on one day sum in a fixed order whatever the scan order, #1099),
the **cumulative** factor per security (a running product of an ex-date's
factor and every later ex-date's, expressed as `EXP(SUM(log_factor) OVER
(PARTITION BY security_id ORDER BY ex_date DESC))` so it is a plain window
aggregate, not a recursive query),
and attaching that cumulative factor to each bar -- happens inside one
query (plus the small validation query above, which shares the same CTEs).
Attaching uses DuckDB's `ASOF LEFT JOIN` (the same range-join operator used
for the dividend `prior_close` lookup: `ON b.security_id = c.security_id
AND b.session < c.ex_date` finds, per bar, the nearest event with a later
`ex_date` -- and because that event's own factor already IS the cumulative
product of itself and everything after it, this single match is the bar's
correct total factor, with no correlated subquery). The final `SELECT`
multiplies `open`/`high`/`low`/`close` by that factor directly in SQL.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.calendar import all_sessions
from tradepartner.config import Settings, get_settings, parse_accepted_jump
from tradepartner.store.schema import RETRACTABLE_TABLES, has_retracted
from tradepartner.timeutil import ensure_tz_aware_utc

#: Natural key (excluding `known_at`) each table's rows are keyed by for
#: "latest revision as of T" purposes, and the `ORDER BY` each function
#: sorts its result by -- the columns the schema's own
#: `UNIQUE (..., known_at)` constraint names minus `known_at` itself.
#: `corporate_actions`'s identity (#108) is not a plain column list, so it
#: is not listed here: it is `_ACTION_IDENTITY_PARTITION`, used by
#: `live_actions_as_of` and by the latest-revision CTE that
#: `adjusted_prices_as_of` inlines (see this module's docstring on why it
#: runs as one SQL statement).
_PRICE_KEY: tuple[str, ...] = ("security_id", "session")
_FACT_KEY: tuple[str, ...] = ("security_id", "fact_name", "as_of_date", "class_member")
_LISTING_KEY: tuple[str, ...] = ("security_id", "ticker", "exchange", "valid_from")
#: `statement_facts_as_of`'s result key (after the `cik` -> `security_id`
#: join, spec "Data / interfaces" > Amendment 2026-10-03): not
#: `statement_facts`'s own natural key (`cik, fact_name, period_end,
#: period_days`, its `UNIQUE` constraint) -- a multi-class CIK's one row
#: becomes one row per `security_id`, so `security_id` replaces `cik` here.
_STATEMENT_FACT_KEY: tuple[str, ...] = ("security_id", "fact_name", "period_end", "period_days")

#: View name `statement_facts_as_of` registers the `securities_as_of(t)`
#: frame under, to join `statement_facts` against it by `cik` in SQL.
_STATEMENT_SECURITIES_VIEW = "_asof_statement_securities"

#: `filing_events_as_of`'s sort order (amendment 2026-10-09, #1358): by
#: acceptance, then accession, then class -- the spec's order, not the
#: table's `UNIQUE (cik, accession)`, so events read in the order they became
#: public.
_FILING_EVENT_ORDER: tuple[str, ...] = ("known_at", "accession", "security_id")

#: View name `filing_events_as_of` registers the `securities_as_of(t)` frame
#: under, to join `filing_events` against it by `cik` in SQL.
_FILING_EVENT_SECURITIES_VIEW = "_asof_filing_event_securities"

#: `PARTITION BY` for an action's identity (#108): the source's id when it
#: gave one, else `(action_type, ex_date)`. Every reader of
#: `corporate_actions` picks the latest revision over this partition, and
#: only then filters on type, ex-date and `cancelled`.
_ACTION_IDENTITY_PARTITION = """
    security_id,
    source_action_id,
    CASE WHEN source_action_id = '' THEN action_type END,
    CASE WHEN source_action_id = '' THEN ex_date END
"""

#: The exchange's local timezone -- see this module's docstring ("Effective
#: is exchange-local, not UTC") for why `ex_date <= t` is compared in this
#: timezone's calendar date, not `t`'s UTC calendar date.
_EXCHANGE_TZ = ZoneInfo("America/New_York")

#: Name of the temporary view `_sessions_registered` exposes the XNYS
#: sessions under, for a dividend's prior-close gap (#72).
_SESSIONS_VIEW = "_asof_xnys_sessions"

#: Name of the temporary view `_chains_registered` exposes the live successor
#: chains under, for the six chain-aware reads to LEFT JOIN (#821, T187b).
_CHAINS_VIEW = "_asof_chains"

#: Name of the temporary view `_chains_registered` exposes the chained
#: successors whose own bars must stay hidden until their chain is known under
#: (#821, T187b): their id and the first session their own later listings allow.
_PRE_LISTING_VIEW = "_asof_pre_listing"

#: The successor-chain master table (#821, T187). A store below schema 31 has
#: no such table and no chain; `live_chains` returns nothing there.
_CHAINS_TABLE = "successor_chains"

#: The ingest source whose `ingestion_runs` rows record a borrow snapshot's
#: observation (spec Amendment 2026-10-11, #1506, ADR 0021 point 3: "rows
#: record change; the run row records the observation"). T171c writes those
#: rows; this module is their only reader. `dashboard/header.py`'s freshness
#: reads exclude the same source so a snapshot never shows the store fresher
#: than its bars.
ASSETS_SOURCE: Final[str] = "alpaca_assets"

#: The columns `borrow_status_as_of` returns, in order (spec Amendment
#: 2026-10-11, #1506, "As-of read (T171b)"): every payload column the table
#: stores plus `known_at` (the fetch that wrote the row) and `observed_at`
#: (the last snapshot run at or before T). The bookkeeping columns
#: `present`, `ingested_at`, `source` and `provenance` are left out: a row
#: returned has already been checked as present, and the other three say how
#: it got there, not what it says.
_BORROW_STATUS_COLUMNS: tuple[str, ...] = (
    "asset_id",
    "symbol",
    "exchange",
    "status",
    "tradable",
    "marginable",
    "shortable",
    "easy_to_borrow",
    "borrow_status",
    "fractionable",
    "known_at",
)


def _validate_t(t: datetime) -> datetime:
    """Reject anything that is not a tz-aware `datetime` (spec: "a bare
    date passed as T raises"). `datetime` is a *subclass* of `date`, so
    `isinstance(t, datetime)` correctly rejects a plain `date` (which is
    not a `datetime`) while still accepting every `datetime` -- checked
    explicitly here rather than assumed. A naive `datetime` is rejected by
    `ensure_tz_aware_utc` with `ValueError`; a tz-aware one is normalized
    to UTC.
    """
    if not isinstance(t, datetime):
        raise TypeError(f"T must be a tz-aware datetime, not {type(t).__name__}: {t!r}")
    return ensure_tz_aware_utc(t, field_name="T")


def _security_filter(
    security_ids: Sequence[str] | None, params: list[Any], *, column: str = "security_id"
) -> str:
    """`"AND <column> IN (?, ?, ...)"` (appending placeholders' values to
    `params` in the order they'll be bound), `"AND FALSE"` if
    `security_ids` is an empty sequence (an empty `IN ()` is a DuckDB
    syntax error, and the correct answer to "restrict to no securities" is
    "no rows", not "no filter"), or `""` if `security_ids` is `None`.
    Callers that need the filter more than once in the same query call
    this once per occurrence, appending to the same `params` list in the
    order the clauses appear in the SQL text (DuckDB binds `?`
    positionally). `column` is the expression the filter applies to: the
    chain-aware reads pass the mapped `security_id` (#821)."""
    if security_ids is None:
        return ""
    if not security_ids:
        return "AND FALSE"
    params.extend(security_ids)
    placeholders = ", ".join("?" for _ in security_ids)
    return f"AND {column} IN ({placeholders})"


def _has_table(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Whether `conn`'s store has `table` (a read-only connection to a store
    below the table's schema version serves the rest of the reads)."""
    rows = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    return table in {name for (name,) in rows}


def live_chains(conn: duckdb.DuckDBPyConnection, t: datetime) -> tuple[tuple[str, str], ...]:
    """The `(predecessor_id, successor_id)` pairs whose live chain is known
    at `t` (#821, T187b): sorted by successor. The live chain is the
    successor's unretracted `successor_chains` row with the greatest
    `ingested_at`, unless a later row (a retraction) was written after it
    (`schema.successor_chains`); only rows with `known_at <= t` are read, so
    a chain reads absent at every `t` before its evidence stamp. A store
    below schema 31 has no `successor_chains` table and no chain.

    `t` must be tz-aware (a bare date raises `TypeError`)."""
    t = _validate_t(t)
    if not _has_table(conn, _CHAINS_TABLE):
        return ()
    rows = conn.execute(
        f"""
        SELECT predecessor_id, successor_id FROM {_CHAINS_TABLE} c
        WHERE NOT retracted AND known_at <= ?
          AND NOT EXISTS (
              SELECT 1 FROM {_CHAINS_TABLE} n
              WHERE n.successor_id = c.successor_id AND n.ingested_at > c.ingested_at
          )
        ORDER BY successor_id
        """,
        [t],
    ).fetchall()
    return tuple((str(predecessor), str(successor)) for predecessor, successor in rows)


@dataclass(frozen=True)
class LiveChains:
    """The chains a single chain-aware read applies (#821, T187b): `pairs`
    maps `predecessor_id` to `successor_id`, `handovers` maps a successor to
    the `valid_from` of its first listing row under the predecessor's ticker
    (the hand-over day a predecessor fact is judged against; a successor with
    no such row known at `t` is absent, so every predecessor fact is its
    own). `pre_listing` maps a chained successor with **no** live chain at `t`
    to the `valid_from` of its first own listing row known at `t`, so the
    price reads hide its own bars before that session (the rule-9 hole bars a
    fill wrote, which are its history only once its chain is known). Empty
    (`bool(LiveChains())` false) when no chain is known at `t`, in which case
    the reads build their pre-#821 SQL, except that `pre_listing` may still be
    non-empty when the store has chain rows that are not live at `t`."""

    pairs: Mapping[str, str] = field(default_factory=dict)
    handovers: Mapping[str, date] = field(default_factory=dict)
    pre_listing: Mapping[str, date] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def map_id(self, security_id: str) -> str:
        """`security_id` as the chained read keys it: the successor's id when
        it is a chained predecessor, else itself."""
        return self.pairs.get(security_id, security_id)

    def hide_sql(self, alias: str = "t") -> str:
        """SQL `AND` clause dropping `alias`'s own bars on a session before the
        successor's first own listing row (`pre_listing`); empty when nothing
        is hidden. Applied to both the chained and the unchained SQL, since a
        chained successor's hole bars must stay hidden until its chain is
        known (#821)."""
        if not self.pre_listing:
            return ""
        return (
            f"AND NOT EXISTS (SELECT 1 FROM {_PRE_LISTING_VIEW} pl "
            f"WHERE pl.successor_id = {alias}.security_id "
            f"AND {alias}.session < pl.first_session)"
        )

    def mapped_entry(self, entry: str, parse: Any) -> tuple[str, date]:
        """A `<security_id>@<date>` config entry (`universe.accepted_price_jumps`)
        with its id mapped through the chain, so an entry naming a predecessor
        applies to the successor's re-keyed row (#821)."""
        security_id, day = parse(entry)
        return self.map_id(security_id), day


#: A read with no chain known at its `t` (skip the chain SQL entirely).
_NO_CHAINS = LiveChains()


def _chain_id(alias: str = "t") -> str:
    """SQL: `alias`'s `security_id` mapped through the live chain view."""
    return f"COALESCE({_CHAINS_VIEW}.successor_id, {alias}.security_id)"


def _chain_join(alias: str = "t") -> str:
    """SQL: the `LEFT JOIN` that attaches the live chain to a row's
    `security_id`."""
    return f"LEFT JOIN {_CHAINS_VIEW} ON {_CHAINS_VIEW}.predecessor_id = {alias}.security_id"


def _chain_own(alias: str = "t") -> str:
    """SQL: 0 for a row of the successor itself, 1 for a re-keyed predecessor
    row, so a key the two share is the successor's own row (#821)."""
    return f"CASE WHEN {_CHAINS_VIEW}.successor_id IS NULL THEN 0 ELSE 1 END"


def _chain_mapped_key(key_columns: Sequence[str], alias: str = "t") -> str:
    """`key_columns` with `security_id` replaced by the mapped id (#821)."""
    return ", ".join(
        _chain_id(alias) if column == "security_id" else column for column in key_columns
    )


@contextmanager
def _chains_registered(conn: duckdb.DuckDBPyConnection, t: datetime) -> Iterator[LiveChains]:
    """Expose the live chains known at `t` to SQL as `_CHAINS_VIEW` (its
    `predecessor_id`, `successor_id` and `handover`) and the chained
    successors whose own bars stay hidden *and* have no live chain at `t` as
    `_PRE_LISTING_VIEW` (`successor_id`, `first_session`), for the block, then
    drop both. Yields the `LiveChains`; when neither is needed nothing is
    registered and the reads build their pre-#821 SQL. `live_chains` is read
    once here, and the hand-over day is derived from the listings known at `t`
    (the successor's first listing row under the predecessor's ticker and
    exchange)."""
    chains = _live_chains_at(conn, t)
    pre_listing = _pre_listing_at(conn, t, chains)
    if not chains.pairs and not pre_listing:
        yield _NO_CHAINS
        return
    chains_frame = pl.DataFrame(
        {
            "predecessor_id": [pred for pred in chains.pairs],
            "successor_id": [chains.pairs[pred] for pred in chains.pairs],
            "handover": [chains.handovers.get(chains.pairs[pred]) for pred in chains.pairs],
        },
        schema={
            "predecessor_id": pl.Utf8,
            "successor_id": pl.Utf8,
            "handover": pl.Date,
        },
    )
    pre_frame = pl.DataFrame(
        {
            "successor_id": list(pre_listing),
            "first_session": list(pre_listing.values()),
        },
        schema={"successor_id": pl.Utf8, "first_session": pl.Date},
    )
    conn.register(_CHAINS_VIEW, chains_frame)
    conn.register(_PRE_LISTING_VIEW, pre_frame)
    try:
        yield LiveChains(chains.pairs, chains.handovers, pre_listing)
    finally:
        conn.unregister(_CHAINS_VIEW)
        conn.unregister(_PRE_LISTING_VIEW)


def _pre_listing_at(
    conn: duckdb.DuckDBPyConnection, t: datetime, chains: LiveChains
) -> dict[str, date]:
    """The chained successors whose own bars a price read must hide at `t`:
    every `successor_id` named by a `successor_chains` row (live or not, less
    those with a live chain at `t`), mapped to the earliest `valid_from` its
    `listings` rows give. Read without a `known_at` filter on purpose: the rule
    only ever **hides** a bar, never shows one, so a listing row known after
    `t` cannot leak a later bar into a read; it is what keeps a filled rule-9
    hole bar hidden before its chain is known. See `LiveChains.pre_listing`."""
    if not _has_table(conn, _CHAINS_TABLE) or not _has_table(conn, "listings"):
        return {}
    rows = conn.execute(f"SELECT DISTINCT successor_id FROM {_CHAINS_TABLE}").fetchall()
    successors = sorted({str(successor) for (successor,) in rows} - set(chains.pairs.values()))
    if not successors:
        return {}
    first = conn.execute(
        "SELECT security_id, MIN(valid_from) FROM listings "
        "WHERE security_id IN (SELECT unnest(?)) GROUP BY security_id",
        [successors],
    ).fetchall()
    return {str(successor): valid_from for successor, valid_from in first}


def _live_chains_at(conn: duckdb.DuckDBPyConnection, t: datetime) -> LiveChains:
    """`live_chains(t)` with each successor's hand-over day, read from the
    listings known at `t`: the predecessor's last listing row's ticker and
    exchange matched by the successor's earliest such row (as
    `store.master.build_chains`)."""
    pairs = live_chains(conn, t)
    if not pairs:
        return _NO_CHAINS
    listings = listings_as_of(conn, t).select("security_id", "ticker", "exchange", "valid_from")
    by_security: dict[str, list[tuple[str, str, date]]] = {}
    for security_id, ticker, exchange, valid_from in listings.iter_rows():
        by_security.setdefault(security_id, []).append((ticker, exchange, valid_from))
    handovers: dict[str, date] = {}
    for predecessor, successor in pairs:
        predecessor_rows = by_security.get(predecessor, [])
        successor_rows = by_security.get(successor, [])
        if not predecessor_rows or not successor_rows:
            continue
        last = max(predecessor_rows, key=lambda row: row[2])
        matches = [row[2] for row in successor_rows if row[0] == last[0] and row[1] == last[1]]
        if matches:
            handovers[successor] = min(matches)
    return LiveChains(dict(pairs), handovers)


def _spin_off_actions_filter(
    security_ids: Sequence[str] | None, params: list[Any], *, mapped: bool = False
) -> str:
    """`_security_filter` for `latest_actions` when it also reads `spinoff`
    rows: a split or dividend is selected by its `security_id`, a spin-off
    by its parent in `source_action_id` (its `security_id` is the child).
    Same `None` / empty-sequence rules. With `mapped` (a live chain at `t`,
    #821) both keys go through `_CHAINS_VIEW`, so a chained predecessor's
    parent id matches its successor."""
    if security_ids is None:
        return ""
    if not security_ids:
        return "AND FALSE"
    params.extend(security_ids)
    placeholders = ", ".join("?" for _ in security_ids)

    def key(column: str) -> str:
        return f"COALESCE({_CHAINS_VIEW}.successor_id, {column})" if mapped else column

    return (
        f"AND (CASE WHEN action_type = 'spinoff' THEN {key('source_action_id')} "
        f"ELSE {key('security_id')} END) IN ({placeholders})"
    )


def _latest_as_of(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    key_columns: Sequence[str],
    t: datetime,
    security_ids: Sequence[str] | None,
    *,
    sessions_from: date | None = None,
    chains: LiveChains | None = None,
) -> pl.DataFrame:
    """The latest-revision-as-of-`t` rows of `table`, as a `polars.
    DataFrame` sorted by `key_columns`: one row per distinct `key_columns`
    tuple among rows with `known_at <= t`, the one with the greatest
    `known_at` (spec "Definitions" > Revision). For `securities` and
    `listings` a key whose latest revision is a retraction (#859) returns
    nothing, and the `retracted` column is left out (every row returned is
    live), so a frame reads as it did before version 11.

    `security_ids`, when given, restricts to those `security_id` values
    (an empty sequence restricts to none, returning an empty frame with
    the right schema -- see `_security_filter`); every table this is
    called for has a `security_id` column.

    `sessions_from`, when given, keeps only keys with `session >=
    sessions_from`. It is applied before choosing the latest revision, which
    gives the same rows as filtering after it because `session` must be a
    key column: every revision of a key shares its session (#1305).

    `chains`, when a non-empty `LiveChains`, re-keys the predecessor's rows
    to the successor (and drops them from the predecessor) before the latest
    revision is chosen, and the `security_ids` filter applies to the mapped
    id; a key the two share is the successor's own row (#821)."""
    chained = bool(chains)
    params: list[Any] = [t]
    session_filter = ""
    if sessions_from is not None:
        if "session" not in key_columns:
            raise ValueError(f"sessions_from needs `session` in the key of {table}")
        if isinstance(sessions_from, datetime):
            raise TypeError(f"sessions_from must be a session date, not {sessions_from!r}")
    security_filter = _security_filter(
        security_ids, params, column=_chain_id() if chained else "security_id"
    )
    if sessions_from is not None:
        params.append(sessions_from)
        session_filter = "AND session >= ?"
    # A retraction (#859) is the latest revision of its key, so the filter
    # applies after choosing it, as `cancelled` does for actions: the key is
    # withdrawn from the retraction's `known_at` on, never before.
    # A store below version 11 (a read-only connection never migrates) has
    # no `retracted` column and no retraction: every row is live there.
    retractable = table in RETRACTABLE_TABLES and has_retracted(conn, table)
    excluded = "_rn, retracted" if retractable else "_rn"
    live = "AND NOT retracted" if retractable else ""
    hide = chains.hide_sql("t") if chains is not None and "session" in key_columns else ""
    if chained:
        assert chains is not None  # bool(chains) is True
        # The join and the id filter live in the inner query (they read the
        # chain view); the row after it already carries the mapped id, so the
        # partition and order use `key_columns` as written.
        partition = ", ".join(key_columns)
        sql = f"""
            SELECT * EXCLUDE ({excluded}, _own) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {partition}
                    ORDER BY _own, known_at DESC
                ) AS _rn
                FROM (
                    SELECT * REPLACE ({_chain_id()} AS security_id), {_chain_own()} AS _own
                    FROM {table} t {_chain_join()}
                    WHERE t.known_at <= ?
                    {security_filter}
                    {session_filter}
                    {hide}
                )
            )
            WHERE _rn = 1 {live}
            ORDER BY {partition}
        """
        return conn.execute(sql, params).pl()
    sql = f"""
        SELECT * EXCLUDE ({excluded}) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {", ".join(key_columns)} ORDER BY known_at DESC
            ) AS _rn
            FROM {table} t
            WHERE t.known_at <= ?
            {security_filter}
            {session_filter}
            {hide}
        )
        WHERE _rn = 1 {live}
        ORDER BY {", ".join(key_columns)}
    """
    return conn.execute(sql, params).pl()


def live_actions_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The `corporate_actions` events known at `t`: per identity (#108) the
    latest revision with `known_at <= t`, dropped if that revision is
    cancelled, sorted by `(security_id, action_type, ex_date)`. A re-dated
    event appears once, at its latest ex-date; no ex-date filter is applied.

    `t` must be tz-aware (a bare date raises `TypeError`); `security_ids`
    as in `_latest_as_of`. A live chain known at `t` (#821) re-keys the
    predecessor's actions to the successor; the successor's own action wins
    a shared identity.
    """
    t = _validate_t(t)
    with _chains_registered(conn, t) as chains:
        params: list[Any] = [t]
        if chains:
            security_filter = _security_filter(security_ids, params, column=_chain_id())
            sql = f"""
                SELECT * EXCLUDE (_rn, _own) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY {_ACTION_IDENTITY_PARTITION} ORDER BY _own, known_at DESC
                    ) AS _rn
                    FROM (
                        SELECT * REPLACE ({_chain_id()} AS security_id), {_chain_own()} AS _own
                        FROM corporate_actions t {_chain_join()}
                        WHERE t.known_at <= ?
                        {security_filter}
                    )
                )
                WHERE _rn = 1 AND NOT cancelled
                ORDER BY security_id, action_type, ex_date, source_action_id
            """
        else:
            security_filter = _security_filter(security_ids, params)
            sql = f"""
                SELECT * EXCLUDE (_rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY {_ACTION_IDENTITY_PARTITION}
                        ORDER BY known_at DESC
                    ) AS _rn
                    FROM corporate_actions
                    WHERE known_at <= ?
                    {security_filter}
                )
                WHERE _rn = 1 AND NOT cancelled
                ORDER BY security_id, action_type, ex_date, source_action_id
            """
        return conn.execute(sql, params).pl()


def prices_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    traded_only: bool = False,
    sessions_from: date | None = None,
) -> pl.DataFrame:
    """Raw `prices_daily` rows known by `t`: one per `(security_id,
    session)`, the latest revision as of `t` (spec acceptance: "A bar
    re-fetched with a different close becomes a second row ...
    `prices_as_of` returns each in its interval" -- i.e. calling this with a
    `t` before the revision's `known_at` returns the original row, and with
    a `t` at or after it returns the revision).

    `traded_only=True` drops bars whose latest revision has zero volume
    (#787): a session nobody traded is missing, not a price. The universe and
    the backtest provider read with it; a later revision with volume brings
    the bar back.

    `sessions_from` leaves out bars before that session, inside the query
    (`_latest_as_of`), so a reader that needs only a recent window does not
    read and convert the whole history (#1305). The bars it keeps are exactly
    the unbounded read's bars from that session on.

    A live chain known at `t` (#821) re-keys the predecessor's bars to the
    successor and drops them from the predecessor; the successor's own bar
    wins a shared session.
    """
    t = _validate_t(t)
    with _chains_registered(conn, t) as chains:
        frame = _latest_as_of(
            conn,
            "prices_daily",
            _PRICE_KEY,
            t,
            security_ids,
            sessions_from=sessions_from,
            chains=chains,
        )
    return _traded(frame) if traded_only else frame


def marking_changed_ids_as_of(
    conn: duckdb.DuckDBPyConnection,
    previous_t: datetime,
    t: datetime,
    security_ids: Sequence[str],
) -> tuple[str, ...]:
    """Names whose cached adjusted history may differ at `t`.

    A newly known old bar can change a dividend's prior close even when the
    bar is outside the marking window. A new action revision can cancel or
    re-date an event. An already known action can become effective when its
    ex-date arrives. A spin-off names its parent, not its child, when the row
    or the child's bar on the ex-date becomes known or effective (#1894). The
    broad probe deliberately includes harmless changes.
    """
    previous_t, t = _validate_t(previous_t), _validate_t(t)
    if not security_ids or t <= previous_t:
        return ()
    previous_session = previous_t.astimezone(_EXCHANGE_TZ).date()
    current_session = t.astimezone(_EXCHANGE_TZ).date()
    rows = conn.execute(
        """
        SELECT DISTINCT security_id FROM (
            SELECT security_id FROM prices_daily
            WHERE known_at > ? AND known_at <= ? AND session <= ?
            UNION ALL
            SELECT security_id FROM corporate_actions
            WHERE known_at > ? AND known_at <= ?
            UNION ALL
            SELECT security_id FROM corporate_actions
            WHERE known_at <= ? AND ex_date > ? AND ex_date <= ?
            UNION ALL
            -- A spin-off adjusts its parent (`source_action_id`), not the child
            -- in `security_id`, when the row, or the child's bar on E, becomes
            -- known or effective (#1894). Broad whatever the opt-in key says.
            SELECT source_action_id FROM corporate_actions
            WHERE action_type = 'spinoff' AND source_action_id <> ''
              AND ((known_at > ? AND known_at <= ?)
                   OR (known_at <= ? AND ex_date > ? AND ex_date <= ?))
            UNION ALL
            SELECT ca.source_action_id FROM corporate_actions ca
            JOIN prices_daily cb
              ON cb.security_id = ca.security_id AND cb.session = ca.ex_date
            WHERE ca.action_type = 'spinoff' AND ca.source_action_id <> ''
              AND ca.known_at <= ? AND cb.known_at > ? AND cb.known_at <= ?
        ) WHERE security_id IN (SELECT unnest(?))
        ORDER BY security_id
        """,
        [
            previous_t,
            t,
            previous_session,
            previous_t,
            t,
            t,
            previous_session,
            current_session,
            previous_t,
            t,
            t,
            previous_session,
            current_session,
            t,
            previous_t,
            t,
            list(security_ids),
        ],
    ).fetchall()
    return tuple(row[0] for row in rows)


def _traded(frame: pl.DataFrame) -> pl.DataFrame:
    """`frame` without its zero-volume bars (#787)."""
    return frame.filter(pl.col("volume") > 0)


#: `price_jumps_as_of`'s columns, in order.
_JUMP_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "prev_session": pl.Date,
    "session": pl.Date,
    "prev_close": pl.Float64,
    "close": pl.Float64,
    "ratio": pl.Float64,
    "accepted": pl.Boolean,
}


def price_jumps_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> pl.DataFrame:
    """Unexplained one-day price jumps known at `t` (#787), sorted by
    `(security_id, session)`.

    Over each security's traded bars known at `t` (latest revision, volume
    above zero, as `prices_as_of(traded_only=True)`), a bar whose raw close
    over the previous traded bar's close (`ratio`) is above
    `universe.max_jump_ratio` or below `universe.min_jump_ratio` is a jump
    unless the splits and dividends known at `t` with an ex-date in
    `(prev_session, session]` explain it: `(close + dividends) * split
    ratios / prev_close` is back inside the bounds. A dividend reported as
    `implausible_amount` by `dropped_dividends_as_of` does not explain a
    jump; that bound is judged against the dividend's as-of prior close
    (the latest bar before the ex-date, traded or not), while the jump
    check uses the previous traded close. `accepted` is true when
    `universe.accepted_price_jumps` names `<security_id>@<session>`.
    `settings` defaults to `get_settings()`.

    Raises `ValueError` naming the security when a jump candidate carries
    an action `adjusted_prices_as_of` refuses (a negative dividend amount,
    a non-positive or non-finite factor; `dropped_dividends_as_of`'s
    validation). Fail loud on purpose: bad action data stops
    `universe_as_of` (rule 6) and `health` rather than being read as an
    explanation, or not, of a jump. Only candidates' actions are checked.

    Only rows known at `t` are read, so the list at `t` never depends on a
    later bar or a later-known action: a split first known after `t` leaves
    its jump on the list at `t`.
    """
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    cfg = settings.universe
    with _chains_registered(conn, t) as chains:
        params: list[Any] = [t]
        hide = chains.hide_sql("b")
        if chains:
            security_filter = _security_filter(security_ids, params, column=_chain_id("b"))
            latest = f"""
                SELECT security_id, session, close, volume, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY _own, known_at DESC
                ) AS _rn
                FROM (
                    SELECT * REPLACE ({_chain_id("b")} AS security_id), {_chain_own("b")} AS _own
                    FROM prices_daily b {_chain_join("b")}
                    WHERE b.known_at <= ?
                    {security_filter}
                    {hide}
                )
            """
        else:
            security_filter = _security_filter(security_ids, params)
            latest = f"""
                SELECT security_id, session, close, volume, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY known_at DESC
                ) AS _rn
                FROM prices_daily b
                WHERE b.known_at <= ?
                {security_filter}
                {hide}
            """
        params += [cfg.max_jump_ratio, cfg.min_jump_ratio]
        sql = f"""
            WITH latest AS (
                {latest}
            ), traded AS (
                SELECT security_id, session, close,
                    LAG(session) OVER w AS prev_session,
                    LAG(close) OVER w AS prev_close
                FROM latest
                WHERE _rn = 1 AND volume > 0
                WINDOW w AS (PARTITION BY security_id ORDER BY session)
            )
            SELECT security_id, prev_session, session, prev_close, close,
                close / prev_close AS ratio
            FROM traded
            WHERE prev_close > 0 AND (close / prev_close > ? OR close / prev_close < ?)
            ORDER BY security_id, session
        """
        candidates = conn.execute(sql, params).pl()
    if candidates.is_empty():
        return pl.DataFrame(schema=_JUMP_SCHEMA)
    candidate_ids = candidates["security_id"].unique().sort().to_list()
    actions = live_actions_as_of(conn, t, candidate_ids)
    implausible = {
        (sid, ex_date, amount)
        for sid, ex_date, amount, reason in dropped_dividends_as_of(
            conn, t, candidate_ids, settings=settings
        )
        .select("security_id", "ex_date", "ratio_or_amount", "reason")
        .iter_rows()
        if reason == "implausible_amount"
    }
    by_security: dict[str, list[tuple[str, date, float]]] = {}
    for sid, kind, ex_date, amount in actions.select(
        "security_id", "action_type", "ex_date", "ratio_or_amount"
    ).iter_rows():
        by_security.setdefault(sid, []).append((kind, ex_date, amount))
    accepted = {
        chains.mapped_entry(entry, parse_accepted_jump) for entry in cfg.accepted_price_jumps
    }
    rows: list[dict[str, Any]] = []
    for row in candidates.iter_rows(named=True):
        splits, dividends = 1.0, 0.0
        for kind, ex_date, amount in by_security.get(row["security_id"], []):
            if not row["prev_session"] < ex_date <= row["session"]:
                continue
            if kind == "split":
                splits *= amount
            elif kind == "dividend" and (row["security_id"], ex_date, amount) not in implausible:
                dividends += amount
        explained = (row["close"] + dividends) * splits / row["prev_close"]
        if cfg.min_jump_ratio <= explained <= cfg.max_jump_ratio:
            continue
        rows.append(row | {"accepted": (row["security_id"], row["session"]) in accepted})
    return pl.DataFrame(rows, schema=_JUMP_SCHEMA)


def symbol_changes_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Latest name-change revision per action identity known at ``t``.

    Identity follows the table's unique index: Alpaca's id when supplied,
    otherwise security, old and new symbols, and process date. The filter
    applies to security ids and the result includes every table column.
    """
    t = _validate_t(t)
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    return conn.execute(
        f"""
        SELECT * EXCLUDE (_rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY security_id, source_action_id,
                    CASE WHEN source_action_id = '' THEN old_symbol ELSE '' END,
                    CASE WHEN source_action_id = '' THEN new_symbol ELSE '' END,
                    CASE WHEN source_action_id = '' THEN process_date ELSE NULL END
                ORDER BY known_at DESC
            ) AS _rn
            FROM symbol_changes
            WHERE known_at <= ? {security_filter}
        ) WHERE _rn = 1
        ORDER BY security_id, process_date, old_symbol
        """,
        params,
    ).pl()


def rates_as_of(
    conn: duckdb.DuckDBPyConnection, t: datetime, series: str | None = None
) -> pl.DataFrame:
    """Published rates known by `t`, latest revision per series and observation date.

    The observation date describes the rate; only `known_at` controls when a
    row becomes visible. A named `series` limits the result; None reads all
    series. A store before schema 23 has no `rates` table, so this read raises.
    """
    t = _validate_t(t)
    params: list[Any] = [t]
    series_filter = ""
    if series is not None:
        series_filter = "AND series = ?"
        params.append(series)
    return conn.execute(
        f"""
        SELECT series, observation_date, value, vintage_date, known_at FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY series, observation_date ORDER BY known_at DESC
            ) AS _rn
            FROM rates
            WHERE known_at <= ?
            {series_filter}
        )
        WHERE _rn = 1
        ORDER BY series, observation_date
        """,
        params,
    ).pl()


def facts_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`facts` rows known by `t`: one per `(security_id, fact_name,
    as_of_date, class_member)`, the latest revision as of `t` (spec
    acceptance: "Restated shares fact: `facts_as_of(T)` returns the earlier
    value between the two `known_at`, the later after").

    **Accession rule first** (plan T11e): among the rows known by `t` that
    carry a `filing_accession`, only those from the latest ingest of their
    `(security_id, fact_name, class_member, filing_accession)` count, every
    row of that ingest (a filing may legitimately carry two dates); the
    EDGAR adapter re-dates a filing's shares when company facts arrive after
    the FSN month end or when the accession switches source, the store keeps
    both dates as rows, and only the newest ingest's dates are the filing's.
    Rows with no accession are untouched. The per-date collapse runs on what
    is left, so a stale re-dated row can never shadow another filing's row
    on the same date.

    **A live chain at `t` (#821)** re-keys the predecessor's facts to the
    successor. A predecessor fact dated on or before the hand-over (the
    successor's first listing row's `valid_from`) is always the successor's; a
    later one only while no fact of the successor's own is known at `t` (the
    cut-off is that fact's `known_at`, not its date), so a subsidiary that
    keeps filing a nominal count is cut off by the successor's first own fact.
    """
    t = _validate_t(t)
    partition = ", ".join(_FACT_KEY)
    with _chains_registered(conn, t) as chains:
        if chains:
            assert chains is not None
            params: list[Any] = [t]
            security_filter = _security_filter(security_ids, params, column=_chain_id("f"))
            params.append(t)
            sql = f"""
                WITH mapped AS (
                    SELECT * REPLACE ({_chain_id("f")} AS security_id),
                        {_chain_own("f")} AS _own,
                        {_CHAINS_VIEW}.successor_id AS _successor,
                        {_CHAINS_VIEW}.handover AS _handover
                    FROM facts f {_chain_join("f")}
                    WHERE f.known_at <= ?
                    {security_filter}
                ),
                successor_first AS (
                    SELECT security_id AS successor, MIN(known_at) AS cutoff
                    FROM facts
                    WHERE known_at <= ?
                      AND security_id IN (SELECT DISTINCT successor_id FROM {_CHAINS_VIEW})
                    GROUP BY security_id
                ),
                kept AS (
                    SELECT m.* EXCLUDE (_successor, _handover) FROM mapped m
                    LEFT JOIN successor_first s ON s.successor = m._successor
                    WHERE m._own = 0 OR m._handover IS NULL OR m.as_of_date <= m._handover
                        OR s.cutoff IS NULL
                ),
                known AS (
                    SELECT *, MAX(ingested_at) OVER (
                        PARTITION BY security_id, fact_name, class_member, filing_accession
                    ) AS _latest_ingest
                    FROM kept
                ),
                current AS (
                    SELECT * EXCLUDE (_latest_ingest) FROM known
                    WHERE filing_accession IS NULL OR ingested_at = _latest_ingest
                )
                SELECT * EXCLUDE (_rn, _own) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY {partition} ORDER BY _own, known_at DESC
                    ) AS _rn
                    FROM current
                )
                WHERE _rn = 1
                ORDER BY {partition}
            """
            return conn.execute(sql, params).pl()
        params = [t]
        security_filter = _security_filter(security_ids, params)
        sql = f"""
            WITH known AS (
                SELECT *, MAX(ingested_at) OVER (
                    PARTITION BY security_id, fact_name, class_member, filing_accession
                ) AS _latest_ingest
                FROM facts
                WHERE known_at <= ?
                {security_filter}
            ),
            current AS (
                SELECT * EXCLUDE (_latest_ingest) FROM known
                WHERE filing_accession IS NULL OR ingested_at = _latest_ingest
            )
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {partition} ORDER BY known_at DESC
                ) AS _rn
                FROM current
            )
            WHERE _rn = 1
            ORDER BY {partition}
        """
        return conn.execute(sql, params).pl()


def listings_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`listings` rows known by `t`: one per `(security_id, ticker,
    exchange, valid_from)`, the latest revision as of `t`.

    Applies `known_at <= t` literally to every row regardless of
    provenance, including `snapshot_static` (owner decision on issue #35)
    -- see this module's docstring.
    """
    t = _validate_t(t)
    return _latest_as_of(conn, "listings", _LISTING_KEY, t, security_ids)


def statement_facts_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`statement_facts` rows known by `t` (amendment 2026-10-03, #660; plan
    T76b), joined through `securities_as_of(t)` on `cik`: one row per
    `(security_id, fact_name, period_end, period_days)`, sorted by that key.
    Columns: `security_id` then every `statement_facts` column in schema
    order (`cik` included).

    `statement_facts` holds only the first-accepted vintage of each key
    (its `UNIQUE` excludes `known_at` -- see this module's docstring), so
    this is a plain `known_at <= t` filter, not a latest-revision query.

    The join is what makes this point-in-time: a CIK with no `securities`
    row known at `t` contributes nothing, and a dual- (or multi-) class
    CIK's one row is repeated once per `security_id` that
    `tradepartner.store.master.securities_as_of` lists for it at `t` --
    this function calls that one rather than re-deriving which `security_id`s
    a `cik` maps to.

    `security_ids`, when given, is passed straight to `securities_as_of`
    (an empty sequence therefore joins against no securities, returning no
    rows -- `_security_filter`'s usual meaning); every row this returns
    already carries a `security_id` from that call, so no second filter is
    needed here.

    `t` must be tz-aware (a bare date raises `TypeError`, a naive
    `datetime` raises `ValueError` -- same rules as every other function in
    this module).
    """
    t = _validate_t(t)
    # Imported here, not at module level: `store.master` imports
    # `_latest_as_of` from this module, so a top-level import the other way
    # would be circular. `securities_as_of` already applies every rule
    # (including any retraction, #859) for "which securities exist at t" --
    # this function must not re-implement that query.
    from tradepartner.store.master import securities_as_of

    securities = securities_as_of(conn, t, security_ids).select("security_id", "cik")
    conn.register(_STATEMENT_SECURITIES_VIEW, securities)
    try:
        sql = f"""
            SELECT s.security_id, f.*
            FROM statement_facts f
            JOIN {_STATEMENT_SECURITIES_VIEW} s ON s.cik = f.cik
            WHERE f.known_at <= ?
            ORDER BY {", ".join(_STATEMENT_FACT_KEY)}
        """
        return conn.execute(sql, [t]).pl()
    finally:
        conn.unregister(_STATEMENT_SECURITIES_VIEW)


def _string_list(value: Sequence[str] | None, *, name: str) -> list[str] | None:
    """`value` as a list, or `None` for "no filter". A bare `str` is a
    `Sequence[str]` too, and filtering on its characters would silently
    match nothing, so it raises `TypeError` instead."""
    if value is None:
        return None
    if isinstance(value, str):
        raise TypeError(f"{name} must be a sequence of strings, not a str: {value!r}")
    return list(value)


def filing_events_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    forms: Sequence[str] | None = None,
    items: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`filing_events` rows known by `t` (spec "Data / interfaces" >
    Amendment 2026-10-09, #1358; plan T164d), joined through
    `securities_as_of(t)` on `cik` as `statement_facts_as_of` is: one row
    per `(security_id, accession)`, so a dual-class issuer's event appears
    once per listed class and a CIK with no `securities` row at `t` is
    invisible at `t`. Sorted by `(known_at, accession, security_id)`.
    Columns: `security_id` then every `filing_events` column in schema order.

    `known_at` is the filing's stamped acceptance (the table's `CHECK`), never
    the index's `filed` date, so an 8-K accepted after the close but before
    EDGAR's 17:30 filing-date cut is invisible at that session's close and
    first visible at the next session. `filing_events` has no revisions (its
    `UNIQUE (cik, accession)` excludes `known_at`), so this is a plain
    `known_at <= t` filter.

    `forms` keeps the rows whose `form` equals one of its entries exactly
    (`8-K` never matches `8-K/A`). `items` keeps the rows whose verbatim
    `items` string, split on commas, contains any of its codes as a whole
    code (`2.02` never matches `12.02`); the store parses no code, so this
    is the one place a consumer's split happens. `None` means no filter on
    that column; an empty sequence keeps nothing; a bare `str` raises
    `TypeError`.

    `t` must be tz-aware (a bare date raises `TypeError`, a naive `datetime`
    raises `ValueError`). On a store older than schema version 21 the table
    does not exist and the read raises (`store.schema`'s version-21 note).
    """
    t = _validate_t(t)
    form_list = _string_list(forms, name="forms")
    item_list = _string_list(items, name="items")
    # Imported here for the same reason as in `statement_facts_as_of`:
    # `store.master` imports from this module.
    from tradepartner.store.master import securities_as_of

    securities = securities_as_of(conn, t).select("security_id", "cik")
    params: list[Any] = [t]
    filters = ""
    if form_list is not None:
        filters += " AND list_contains(?::VARCHAR[], f.form)"
        params.append(form_list)
    if item_list is not None:
        filters += (
            " AND list_has_any("
            "list_transform(string_split(f.items, ','), code -> trim(code)), ?::VARCHAR[])"
        )
        params.append(item_list)
    conn.register(_FILING_EVENT_SECURITIES_VIEW, securities)
    try:
        sql = f"""
            SELECT s.security_id, f.*
            FROM filing_events f
            JOIN {_FILING_EVENT_SECURITIES_VIEW} s ON s.cik = f.cik
            WHERE f.known_at <= ?{filters}
            ORDER BY {", ".join(_FILING_EVENT_ORDER)}
        """
        return conn.execute(sql, params).pl()
    finally:
        conn.unregister(_FILING_EVENT_SECURITIES_VIEW)


def _latest_assets_observation(conn: duckdb.DuckDBPyConnection, t: datetime) -> datetime | None:
    """The latest `ok` `ASSETS_SOURCE` run cursor at or before `t`, or None
    when there is none (spec Amendment 2026-10-11, #1506, ADR 0021 point 3:
    the observation is the run row, never the table's `MAX(known_at)`,
    which on a quiet day lags the last snapshot).

    The cursor is compared as a timestamp, not as a string: the writer
    stores ISO-8601 UTC, and a lexical comparison of a differently-padded
    value would silently order wrong. `TRY_CAST` rather than `CAST` so a
    row with an unusable cursor is skipped rather than failing every read
    -- its cursor could not have been written by this code path. A store
    whose `ingestion_runs` table is absent (the look-ahead harness copies
    fact tables only, `tests/lookahead/harness.py`) has no observation to
    report and reads None.
    """
    tables = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    if "ingestion_runs" not in {name for (name,) in tables}:
        return None
    row = conn.execute(
        "SELECT max(TRY_CAST(chunk_cursor AS TIMESTAMPTZ)) FROM ingestion_runs "
        "WHERE source = ? AND status = 'ok' "
        "AND TRY_CAST(chunk_cursor AS TIMESTAMPTZ) <= ?",
        [ASSETS_SOURCE, t],
    ).fetchone()
    return None if row is None or row[0] is None else row[0]


def borrow_status_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    symbols: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The borrow status known at `t` (spec Amendment 2026-10-11, #1506,
    ADR 0021; plan T171b), in `_BORROW_STATUS_COLUMNS` order, sorted by
    `(symbol, asset_id)`.

    Per `asset_id` the latest row with `known_at <= t` is chosen **first**,
    dropped when that row has `present = FALSE` (the asset was missing from
    the snapshot at `t`), and only then filtered by `symbols`: a renamed
    asset's old symbol therefore returns nothing rather than its stale
    pre-rename row, and a symbol several assets share returns several rows.
    `symbols` matches the row's `symbol` exactly -- the symbol on its fetch
    day, which is all the table stores -- with `None` for no filter and an
    empty sequence for no rows. A bare `str` raises `TypeError`.

    `observed_at` is the latest `ok` `ASSETS_SOURCE` run cursor at or before
    `t` (`_latest_assets_observation`), the same value on every row, and
    NULL when there is none: it says when the returned status was last
    confirmed, which on a quiet day is later than any row's `known_at`.

    `t` must be tz-aware (a bare date raises `TypeError`, a naive `datetime`
    raises `ValueError`). On a store below schema version 24 the table does
    not exist and the read raises (schema's version-24 note), as
    `filing_events_as_of` does below its version 21.
    """
    t = _validate_t(t)
    symbol_list = _string_list(symbols, name="symbols")
    params: list[Any] = [t]
    symbol_filter = ""
    if symbol_list is not None:
        # `list_contains` over an empty list is false for every row, which
        # is the right answer for "restrict to no symbols"; a bare IN ()
        # would be a syntax error.
        symbol_filter = " AND list_contains(?::VARCHAR[], l.symbol)"
        params.append(symbol_list)
    columns = ", ".join(f"l.{column}" for column in _BORROW_STATUS_COLUMNS)
    sql = f"""
        SELECT {columns} FROM (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY asset_id ORDER BY known_at DESC
                ) AS _rn
                FROM borrow_status
                WHERE known_at <= ?
            )
            WHERE _rn = 1 AND present
        ) AS l
        WHERE TRUE{symbol_filter}
        ORDER BY l.symbol, l.asset_id
    """
    frame = conn.execute(sql, params).pl()
    return frame.with_columns(
        pl.lit(
            _latest_assets_observation(conn, t),
            dtype=pl.Datetime(time_unit="us", time_zone="UTC"),
        ).alias("observed_at")
    )


def _adjusted_ctes(
    action_types: str,
    bars_filter: str,
    actions_filter: str,
    *,
    chained: bool = False,
    hide: str = "",
    spin_offs: bool = False,
) -> str:
    """The CTEs `adjusted_prices_as_of` shares between its validation query
    (checks `event_factor` before anything calls `LN()` on it) and its main
    query (adds the cumulative-factor window and the `ASOF JOIN` to bars).

    `action_types` is `"'split'"`, `"'split', 'dividend'"` or `"'split',
    'dividend', 'spinoff'"` (always a literal, never user input);
    `bars_filter`/`actions_filter` are each `_security_filter`'s output for
    that CTE's own `?` placeholders. Build all three, and the bind
    parameters in placeholder order, with `_adjusted_params`; run the
    queries inside `_sessions_registered`, which provides the
    `_SESSIONS_VIEW` the gap CTE reads.

    With `chained` the bars and actions are re-keyed through `_CHAINS_VIEW`
    first (#821), so a predecessor's splits and dividends adjust the
    successor's bars; `hide` is the bar-hiding clause of a chained successor
    whose chain is not known yet.

    `spin_offs=True` (the opt-in read, #1894) adds two CTEs and one `?`
    (`t`): `child_close`, the child's latest bar on E known at `t`, read
    whether or not the child is in the read's ids, and `keyed_actions`,
    which re-keys a `spinoff` row to its parent (`source_action_id`) and
    turns its ratio into the distributed value D = r x C, so the rest of
    the chain treats it as a parent dividend of amount D. A `spinoff` row
    with no child close has `ratio_or_amount` NULL and `drop_reason`
    `no_child_close`. `spin_ratio` and `child_close` carry r and C for
    `_raise_on_invalid_factor`.
    """
    if spin_offs:
        spin_ctes = """
        child_close AS (
            SELECT security_id, session, close FROM (
                SELECT security_id, session, close, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY known_at DESC
                ) AS _rn
                FROM prices_daily
                WHERE known_at <= ?
                  AND (security_id, session) IN (
                      SELECT security_id, ex_date FROM latest_actions
                      WHERE action_type = 'spinoff'
                  )
            )
            WHERE _rn = 1
        ),
        -- A spin-off is keyed by its parent; D = r x C is its amount.
        keyed_actions AS (
            SELECT
                CASE WHEN la.action_type = 'spinoff'
                    THEN la.source_action_id ELSE la.security_id END AS security_id,
                la.security_id AS child_id,
                la.ex_date,
                la.action_type,
                CASE WHEN la.action_type = 'spinoff'
                    THEN la.ratio_or_amount * cc.close
                    ELSE la.ratio_or_amount END AS ratio_or_amount,
                la.ratio_or_amount AS spin_ratio,
                cc.close AS child_close
            FROM latest_actions la
            LEFT JOIN child_close cc
                ON la.action_type = 'spinoff'
                AND cc.security_id = la.security_id AND cc.session = la.ex_date
        ),"""
        action_source = "keyed_actions"
        extra_cols = (
            "\n                la.child_id,\n                la.spin_ratio,\n"
            "                la.child_close,"
        )
        no_child = "WHEN action_type = 'spinoff' AND child_close IS NULL THEN 'no_child_close'"
    else:
        spin_ctes, action_source, extra_cols, no_child = "", "latest_actions", "", ""

    if chained:
        latest_bars = f"""
        latest_bars AS (
            SELECT * EXCLUDE (_rn, _own) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY _own, known_at DESC
                ) AS _rn
                FROM (
                    SELECT * REPLACE ({_chain_id("b")} AS security_id), {_chain_own("b")} AS _own
                    FROM prices_daily b {_chain_join("b")}
                    WHERE b.known_at <= ?
                    {bars_filter}
                    {hide}
                )
            )
            WHERE _rn = 1
        ),"""
        latest_actions = f"""
        latest_actions AS (
            SELECT * EXCLUDE (_rn, _own) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {_ACTION_IDENTITY_PARTITION} ORDER BY _own, known_at DESC
                ) AS _rn
                FROM (
                    SELECT * REPLACE ({_chain_id("a")} AS security_id), {_chain_own("a")} AS _own
                    FROM corporate_actions a {_chain_join("a")}
                    WHERE a.known_at <= ?
                    {actions_filter}
                )
            )
            WHERE _rn = 1 AND NOT cancelled AND ex_date <= ?
              AND action_type IN ({action_types})
        ),"""
    else:
        latest_bars = f"""
        latest_bars AS (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY known_at DESC
                ) AS _rn
                FROM prices_daily b
                WHERE b.known_at <= ?
                {bars_filter}
                {hide}
            )
            WHERE _rn = 1
        ),"""
        latest_actions = f"""
        latest_actions AS (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {_ACTION_IDENTITY_PARTITION}
                    ORDER BY known_at DESC
                ) AS _rn
                FROM corporate_actions
                WHERE known_at <= ?
                {actions_filter}
            )
            WHERE _rn = 1 AND NOT cancelled AND ex_date <= ?
              AND action_type IN ({action_types})
        ),"""
    return f"""
        {latest_bars}
        -- Latest revision per action identity (#108): the source's id when
        -- it gave one, else (action_type, ex_date). The ex-date, type and
        -- cancelled filters apply to that latest revision, never before
        -- choosing it: a re-date into the future, or a cancel, must retire
        -- an older revision rather than leave it standing.
        {latest_actions}{spin_ctes}
        -- `pb` (the ASOF-joined nearest bar with session < ex_date) is
        -- only read by the dividend branch; for a split row it is
        -- harmlessly present but unused. See this module's docstring on
        -- why this is an ASOF JOIN rather than an exact lookup of "the"
        -- prior XNYS session, and on the staleness bound (#72).
        event_prior AS (
            SELECT
                la.security_id,
                la.ex_date,
                la.action_type,
                la.ratio_or_amount,{extra_cols}
                pb.session AS prior_session,
                pb.close AS prior_close
            FROM {action_source} la
            ASOF LEFT JOIN latest_bars pb
                ON pb.security_id = la.security_id AND pb.session < la.ex_date
        ),
        calendar_bounds AS (
            SELECT MIN(session) AS first_session, MAX(session) AS last_session
            FROM {_SESSIONS_VIEW}
        ),
        -- A dividend's gap: XNYS sessions in [prior_session, ex_date),
        -- i.e. the index of the last session before ex_date minus that
        -- of the last session before prior_session (-1 if none), so the
        -- prior session itself is a gap of 1. NULL when either date is
        -- outside the configured calendar, where there is no index to
        -- count with (a pre-calendar bar would otherwise count as if it
        -- were on the first session).
        event_gap AS (
            SELECT
                ep.*,
                CASE WHEN ep.action_type IN ('dividend', 'spinoff')
                    AND ep.prior_session >= cb.first_session
                    AND ep.ex_date <= cb.last_session
                    THEN xe.idx - COALESCE(xp.idx, -1)
                END AS gap_sessions
            FROM event_prior ep
            CROSS JOIN calendar_bounds cb
            ASOF LEFT JOIN {_SESSIONS_VIEW} xe ON xe.session < ep.ex_date
            ASOF LEFT JOIN {_SESSIONS_VIEW} xp ON xp.session < ep.prior_session
        ),
        -- Why a dividend is left unapplied, in this order; NULL when it
        -- applies, and always for a split. `dropped_dividends_as_of`
        -- reports this column as `reason`.
        event_drop AS (
            SELECT
                *,
                CASE WHEN action_type IN ('dividend', 'spinoff') THEN
                    CASE
                        {no_child}
                        WHEN prior_session IS NULL THEN 'no_prior_bar'
                        WHEN gap_sessions IS NULL THEN 'outside_calendar_range'
                        WHEN gap_sessions > ? THEN 'stale_prior_bar'
                        -- Only a finite amount on a positive, finite close: a
                        -- NaN or infinite amount, or a zero or NaN close, is
                        -- corrupt data `_raise_on_invalid_factor` names.
                        WHEN isfinite(ratio_or_amount) AND isfinite(prior_close)
                            AND prior_close > 0
                            AND ratio_or_amount >= ? * prior_close
                            THEN 'implausible_amount'
                    END
                END AS drop_reason
            FROM event_gap
        ),
        -- Each event's own factor; NULL for a dividend with a drop reason:
        -- no prior bar, one more than `max_prior_close_gap_sessions`
        -- sessions back, one outside the calendar (gap NULL), or an amount
        -- at or above `max_dividend_to_prior_close` of the prior close.
        event_factor AS (
            SELECT
                *,
                CASE
                    WHEN action_type = 'split' THEN 1.0 / ratio_or_amount
                    WHEN drop_reason IS NULL THEN 1.0 - ratio_or_amount / prior_close
                END AS factor
            FROM event_drop
        )
    """


def _adjusted_params(
    t: datetime,
    security_ids: Sequence[str] | None,
    settings: Settings | None,
    *,
    include_dividends: bool,
    include_spin_offs: bool = False,
    chains: LiveChains | None = None,
) -> tuple[str, list[Any]]:
    """`_adjusted_ctes`' SQL and its bind parameters, in placeholder order:
    `t` and the bars filter (`latest_bars`), `t`, the actions filter and
    `t_session` (`latest_actions`), `t` again when `include_spin_offs`
    (`child_close`), then `max_prior_close_gap_sessions` and
    `max_dividend_to_prior_close` (`event_drop`). `t` must already be
    validated. `include_spin_offs` requires `include_dividends` (the
    caller checks); it selects `spinoff` rows by their parent.

    Both limits only matter for a dividend, so a splits-only query binds
    `0` and `1.0` and never loads settings (`get_settings()` rereads the
    environment on every call). With a non-empty `chains` the bars and
    actions are re-keyed through the live chain view (#821) and the id
    filters use the mapped column.
    """
    t_session = t.astimezone(_EXCHANGE_TZ).date()
    action_types = "'split', 'dividend'" if include_dividends else "'split'"
    if include_spin_offs:
        action_types += ", 'spinoff'"
    max_gap, max_share = 0, 1.0
    if include_dividends:
        adjust = (settings or get_settings()).adjust
        max_gap, max_share = adjust.max_prior_close_gap_sessions, adjust.max_dividend_to_prior_close

    chained = bool(chains)
    hide = chains.hide_sql("b") if chains is not None else ""
    bar_column = _chain_id("b") if chained else "security_id"
    action_column = _chain_id("a") if chained else "security_id"
    params: list[Any] = [t]
    bars_filter = _security_filter(security_ids, params, column=bar_column)
    params.append(t)
    actions_filter = (
        _spin_off_actions_filter(security_ids, params, mapped=chained)
        if include_spin_offs
        else _security_filter(security_ids, params, column=action_column)
    )
    params.append(t_session)
    if include_spin_offs:
        params.append(t)
    params += [max_gap, max_share]
    return (
        _adjusted_ctes(
            action_types,
            bars_filter,
            actions_filter,
            chained=chained,
            hide=hide,
            spin_offs=include_spin_offs,
        ),
        params,
    )


#: `(sessions, frame)` for the last `calendar.all_sessions()` tuple seen.
#: Checked by identity: that tuple is itself cached per calendar range, and
#: hashing ~11k dates on every call (an `lru_cache` key) costs ~0.5 ms.
_sessions_frame_cache: tuple[tuple[date, ...], pl.DataFrame] | None = None


def _sessions_frame(sessions: tuple[date, ...]) -> pl.DataFrame:
    """`sessions` as an `(idx, session)` frame, `idx` 0-based ascending,
    built once per `calendar.all_sessions()` tuple."""
    global _sessions_frame_cache
    if _sessions_frame_cache is None or _sessions_frame_cache[0] is not sessions:
        frame = (
            pl.DataFrame({"session": sessions}, schema={"session": pl.Date})
            .with_row_index("idx")
            .with_columns(pl.col("idx").cast(pl.Int64))
        )
        _sessions_frame_cache = (sessions, frame)
    return _sessions_frame_cache[1]


_NO_SESSIONS = pl.DataFrame(schema={"idx": pl.Int64, "session": pl.Date})


@contextmanager
def _sessions_registered(
    conn: duckdb.DuckDBPyConnection, *, include_dividends: bool
) -> Iterator[None]:
    """Expose the XNYS sessions to SQL as `_SESSIONS_VIEW` for the
    duration of the block, then drop the view. Registering a polars frame
    costs well under a millisecond, where binding the ~11k sessions as a
    list parameter cost ~5 ms per statement. Only a dividend's gap reads
    it, so a splits-only query registers an empty frame. Works on a
    read-only connection (a registered frame is a temporary view)."""
    frame = _sessions_frame(all_sessions()) if include_dividends else _NO_SESSIONS
    conn.register(_SESSIONS_VIEW, frame)
    try:
        yield
    finally:
        conn.unregister(_SESSIONS_VIEW)


def adjusted_prices_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    include_dividends: bool = False,
    include_spin_offs: bool = False,
    settings: Settings | None = None,
    traded_only: bool = False,
    sessions_from: date | None = None,
) -> pl.DataFrame:
    """`prices_as_of(conn, t, security_ids)`, with `open`/`high`/`low`/
    `close` adjusted for every split known by `t` with `ex_date <= t`
    (exchange-local date, `_EXCHANGE_TZ`) and, when `include_dividends=
    True`, every such dividend -- see this module's docstring for the
    adjustment methodology, the validation this performs before computing
    any cumulative factor, and why this issues SQL statements sharing one
    set of CTEs rather than a Python loop per bar. `volume` is left raw.
    Column order matches `prices_as_of`; sorted by `(security_id,
    session)`.

    A dividend whose prior bar is more than `settings.adjust.
    max_prior_close_gap_sessions` XNYS sessions before its ex-date (or
    that has no prior bar at all) is left unapplied; `dropped_dividends_
    as_of` lists those. `settings` defaults to `get_settings()`.

    A dividend whose amount is at or above `settings.adjust.
    max_dividend_to_prior_close` times its prior close is left unapplied
    too, and reported as `implausible_amount` (#841).

    Raises `ValueError` if any known, effective event's own factor is
    non-positive or non-finite (a split `ratio_or_amount` of `0`), or a
    dividend amount is negative.

    `include_spin_offs=True` (spec amendment 2026-10-19, #1894; requires
    `include_dividends=True`, else `ValueError`) also applies every
    `spinoff` row of a parent in `security_ids` as a dividend in kind of
    D = ratio x the child's raw close on the ex-date E, under the dividend's
    prior-close rules, valued only from inputs known at `t`. A spin-off it
    cannot value is left unapplied and listed by `unvalued_spin_offs_as_of`.
    The default reads are byte-identical with or without `spinoff` rows.

    `traded_only=True` drops zero-volume bars after adjusting, as
    `prices_as_of(traded_only=True)` (#787); the factors themselves are
    computed over every stored bar, so a dividend's prior close is the one
    the unfiltered read would use.

    `sessions_from` (strategy-lab T99) leaves bars with `session <
    sessions_from` out of the returned frame, **after** the as-of selection:
    the `known_at` cut, the latest revision per bar and every factor are
    computed exactly as the unbounded read computes them (a dividend whose
    prior close falls before the bound still adjusts), so the result is the
    unbounded frame's rows at or after the bound, row for row. `None` (the
    default) bounds nothing. Raises `TypeError` for a `datetime`: the bound is
    a session date, not a read time.
    """
    t = _validate_t(t)
    if include_spin_offs and not include_dividends:
        raise ValueError(
            "include_spin_offs=True requires include_dividends=True: "
            "a spin-off is a distribution, so it belongs in the total-return series only"
        )
    if isinstance(sessions_from, datetime):
        raise TypeError(f"sessions_from must be a session date, not a datetime: {sessions_from!r}")
    with _chains_registered(conn, t) as chains:
        common_ctes, params = _adjusted_params(
            t,
            security_ids,
            settings,
            include_dividends=include_dividends,
            include_spin_offs=include_spin_offs,
            chains=chains,
        )
        with _sessions_registered(conn, include_dividends=include_dividends):
            _raise_on_invalid_factor(conn, common_ctes, params, spin_offs=include_spin_offs)
            frame = conn.execute(
                _adjusted_select(common_ctes, bounded=sessions_from is not None),
                [*params, sessions_from] if sessions_from is not None else params,
            ).pl()
    return _traded(frame) if traded_only else frame


def _raise_on_invalid_factor(
    conn: duckdb.DuckDBPyConnection,
    common_ctes: str,
    params: list[Any],
    *,
    spin_offs: bool = False,
) -> None:
    """Raise `ValueError` naming the first event whose own factor is
    non-positive or non-finite, or the first negative dividend amount
    (see this module's docstring), before anything calls `LN()`. With
    `spin_offs`, also a `spinoff` whose ratio, or whose child close when
    known, is non-positive or non-finite (corrupt data, as a split ratio
    of `0` is), whatever drop reason the row would otherwise get."""
    spin_check = (
        """
           OR (action_type = 'spinoff' AND (
                NOT (spin_ratio > 0 AND isfinite(spin_ratio))
                OR (child_close IS NOT NULL
                    AND NOT (child_close > 0 AND isfinite(child_close)))))"""
        if spin_offs
        else ""
    )
    bad_rows = conn.execute(
        f"""
        WITH {common_ctes}
        SELECT security_id, ex_date, ratio_or_amount, factor
        FROM event_factor
        WHERE (factor IS NOT NULL AND NOT (factor > 0 AND isfinite(factor)))
           OR (action_type = 'dividend' AND ratio_or_amount < 0){spin_check}
        ORDER BY security_id, ex_date
        LIMIT 1
        """,
        params,
    ).fetchall()
    if bad_rows:
        security_id, ex_date, ratio_or_amount, factor = bad_rows[0]
        raise ValueError(
            "corporate action produces a non-positive or non-finite adjustment factor: "
            f"security_id={security_id!r} ex_date={ex_date!r} "
            f"ratio_or_amount={ratio_or_amount!r} factor={factor!r}"
        )


def _adjusted_select(common_ctes: str, *, bounded: bool = False) -> str:
    """`adjusted_prices_as_of`'s main query over `common_ctes`."""
    session_filter = "WHERE b.session >= ?" if bounded else ""
    return f"""
        WITH {common_ctes},
        -- One factor per ex-date before the cumulative window: a split and
        -- dividend on the same day must sum in a stable order, independent
        -- of the table or truncated-view scan order (#1099).
        event_day AS (
            SELECT security_id, ex_date,
                SUM(LN(factor) ORDER BY action_type, ratio_or_amount) AS log_factor
            FROM event_factor
            WHERE factor IS NOT NULL
            GROUP BY security_id, ex_date
        ),
        -- Accumulate from the latest ex-date backward, so each date's factor
        -- includes itself and every later date (ASC would be wrong).
        cum AS (
            SELECT security_id, ex_date,
                EXP(SUM(log_factor) OVER (
                    PARTITION BY security_id ORDER BY ex_date DESC
                )) AS cum_factor
            FROM event_day
        )
        SELECT
            b.security_id,
            b.session,
            b.open * COALESCE(c.cum_factor, 1.0) AS open,
            b.high * COALESCE(c.cum_factor, 1.0) AS high,
            b.low * COALESCE(c.cum_factor, 1.0) AS low,
            b.close * COALESCE(c.cum_factor, 1.0) AS close,
            b.volume,
            b.known_at,
            b.ingested_at,
            b.source,
            b.provenance
        FROM latest_bars b
        ASOF LEFT JOIN cum c
            ON b.security_id = c.security_id AND b.session < c.ex_date
        {session_filter}
        ORDER BY b.security_id, b.session
    """


def dropped_dividends_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> pl.DataFrame:
    """Dividends known by `t` with `ex_date <= t` that `adjusted_prices_as_of(
    ..., include_dividends=True)` leaves unapplied because it has no usable
    prior close (#72) or an implausible amount (#841): one row per
    `(security_id, ex_date)`, sorted by both, with `ratio_or_amount`,
    `prior_session`, `gap_sessions` and `reason`, one of:

    - `"no_prior_bar"`: no bar before the ex-date is known at `t`
      (`prior_session` and `gap_sessions` are `NULL`);
    - `"outside_calendar_range"`: the prior bar or the ex-date falls
      outside `calendar.start`..`calendar.end`, so the gap cannot be
      counted (`gap_sessions` is `NULL`);
    - `"stale_prior_bar"`: more than `settings.adjust.
      max_prior_close_gap_sessions` XNYS sessions before the ex-date;
    - `"implausible_amount"` (#841): the amount is at or above
      `settings.adjust.max_dividend_to_prior_close` times the prior close
      (bad source data, e.g. a preferred issue price on the common).

    For `health` to count; `settings` defaults to `get_settings()`.
    Raises `ValueError` on the same invalid data `adjusted_prices_as_of`
    does (e.g. a negative dividend amount), so the two never disagree on
    what is a drop and what is bad data.
    """
    t = _validate_t(t)
    with _chains_registered(conn, t) as chains:
        common_ctes, params = _adjusted_params(
            t, security_ids, settings, include_dividends=True, chains=chains
        )
        sql = f"""
            WITH {common_ctes}
            SELECT
                security_id,
                ex_date,
                ratio_or_amount,
                prior_session,
                gap_sessions,
                drop_reason AS reason
            FROM event_factor
            WHERE drop_reason IS NOT NULL
            ORDER BY security_id, ex_date
        """
        with _sessions_registered(conn, include_dividends=True):
            _raise_on_invalid_factor(conn, common_ctes, params)
            return conn.execute(sql, params).pl()


def unvalued_spin_offs_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> pl.DataFrame:
    """Spin-offs of the parents in `security_ids` (`None`: all) known by `t`
    with `ex_date <= t` that `adjusted_prices_as_of(..., include_dividends=
    True, include_spin_offs=True)` leaves unapplied (spec amendment
    2026-10-19, #1894, item 2): one row per `(parent, child, ex_date)`,
    sorted by those, with `reason`, one of `"no_child_close"` (no bar of the
    child on the ex-date is known at `t`) or the dividend's four, applied to
    the parent's prior bar and to D = ratio x the child's close:
    `"no_prior_bar"`, `"outside_calendar_range"`, `"stale_prior_bar"`,
    `"implausible_amount"`. `settings` defaults to `get_settings()`.
    Raises `ValueError` on the same corrupt data `adjusted_prices_as_of`
    does (a non-positive or non-finite ratio or child close).
    """
    t = _validate_t(t)
    common_ctes, params = _adjusted_params(
        t, security_ids, settings, include_dividends=True, include_spin_offs=True
    )
    sql = f"""
        WITH {common_ctes}
        SELECT
            security_id AS parent,
            child_id AS child,
            ex_date,
            drop_reason AS reason
        FROM event_factor
        WHERE action_type = 'spinoff' AND drop_reason IS NOT NULL
        ORDER BY parent, ex_date, child
    """
    with _sessions_registered(conn, include_dividends=True):
        _raise_on_invalid_factor(conn, common_ctes, params, spin_offs=True)
        return conn.execute(sql, params).pl()

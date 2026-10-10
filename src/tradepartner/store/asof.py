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

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
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


def _security_filter(security_ids: Sequence[str] | None, params: list[Any]) -> str:
    """`"AND security_id IN (?, ?, ...)"` (appending placeholders' values to
    `params` in the order they'll be bound), `"AND FALSE"` if
    `security_ids` is an empty sequence (an empty `IN ()` is a DuckDB
    syntax error, and the correct answer to "restrict to no securities" is
    "no rows", not "no filter"), or `""` if `security_ids` is `None`.
    Callers that need the filter more than once in the same query call
    this once per occurrence, appending to the same `params` list in the
    order the clauses appear in the SQL text (DuckDB binds `?`
    positionally)."""
    if security_ids is None:
        return ""
    if not security_ids:
        return "AND FALSE"
    params.extend(security_ids)
    placeholders = ", ".join("?" for _ in security_ids)
    return f"AND security_id IN ({placeholders})"


def _latest_as_of(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    key_columns: Sequence[str],
    t: datetime,
    security_ids: Sequence[str] | None,
    *,
    sessions_from: date | None = None,
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
    """
    partition = ", ".join(key_columns)
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    session_filter = ""
    if sessions_from is not None:
        if "session" not in key_columns:
            raise ValueError(f"sessions_from needs `session` in the key of {table}")
        if isinstance(sessions_from, datetime):
            raise TypeError(f"sessions_from must be a session date, not {sessions_from!r}")
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
    sql = f"""
        SELECT * EXCLUDE ({excluded}) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {partition} ORDER BY known_at DESC
            ) AS _rn
            FROM {table}
            WHERE known_at <= ?
            {security_filter}
            {session_filter}
        )
        WHERE _rn = 1 {live}
        ORDER BY {partition}
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
    as in `_latest_as_of`.
    """
    t = _validate_t(t)
    params: list[Any] = [t]
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
    """
    t = _validate_t(t)
    frame = _latest_as_of(
        conn, "prices_daily", _PRICE_KEY, t, security_ids, sessions_from=sessions_from
    )
    return _traded(frame) if traded_only else frame


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
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    params += [cfg.max_jump_ratio, cfg.min_jump_ratio]
    sql = f"""
        WITH latest AS (
            SELECT security_id, session, close, volume, ROW_NUMBER() OVER (
                PARTITION BY security_id, session ORDER BY known_at DESC
            ) AS _rn
            FROM prices_daily
            WHERE known_at <= ?
            {security_filter}
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
    accepted = {parse_accepted_jump(entry) for entry in cfg.accepted_price_jumps}
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
    """
    t = _validate_t(t)
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    partition = ", ".join(_FACT_KEY)
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


def _adjusted_ctes(action_types: str, bars_filter: str, actions_filter: str) -> str:
    """The CTEs `adjusted_prices_as_of` shares between its validation query
    (checks `event_factor` before anything calls `LN()` on it) and its main
    query (adds the cumulative-factor window and the `ASOF JOIN` to bars).

    `action_types` is `"'split'"` or `"'split', 'dividend'"` (always a
    literal, never user input); `bars_filter`/`actions_filter` are each
    `_security_filter`'s output for that CTE's own `?` placeholders. Build
    all three, and the bind parameters in placeholder order, with
    `_adjusted_params`; run the queries inside `_sessions_registered`,
    which provides the `_SESSIONS_VIEW` the gap CTE reads.
    """
    return f"""
        latest_bars AS (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY security_id, session ORDER BY known_at DESC
                ) AS _rn
                FROM prices_daily
                WHERE known_at <= ?
                {bars_filter}
            )
            WHERE _rn = 1
        ),
        -- Latest revision per action identity (#108): the source's id when
        -- it gave one, else (action_type, ex_date). The ex-date, type and
        -- cancelled filters apply to that latest revision, never before
        -- choosing it: a re-date into the future, or a cancel, must retire
        -- an older revision rather than leave it standing.
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
        ),
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
                la.ratio_or_amount,
                pb.session AS prior_session,
                pb.close AS prior_close
            FROM latest_actions la
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
                CASE WHEN ep.action_type = 'dividend'
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
                CASE WHEN action_type = 'dividend' THEN
                    CASE
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
) -> tuple[str, list[Any]]:
    """`_adjusted_ctes`' SQL and its bind parameters, in placeholder order:
    `t` and the bars filter (`latest_bars`), `t`, the actions filter and
    `t_session` (`latest_actions`), then `max_prior_close_gap_sessions`
    and `max_dividend_to_prior_close` (`event_drop`). `t` must already be
    validated.

    Both limits only matter for a dividend, so a splits-only query binds
    `0` and `1.0` and never loads settings (`get_settings()` rereads the
    environment on every call).
    """
    t_session = t.astimezone(_EXCHANGE_TZ).date()
    action_types = "'split', 'dividend'" if include_dividends else "'split'"
    max_gap, max_share = 0, 1.0
    if include_dividends:
        adjust = (settings or get_settings()).adjust
        max_gap, max_share = adjust.max_prior_close_gap_sessions, adjust.max_dividend_to_prior_close

    params: list[Any] = [t]
    bars_filter = _security_filter(security_ids, params)
    params.append(t)
    actions_filter = _security_filter(security_ids, params)
    params += [t_session, max_gap, max_share]
    return _adjusted_ctes(action_types, bars_filter, actions_filter), params


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
    if isinstance(sessions_from, datetime):
        raise TypeError(f"sessions_from must be a session date, not a datetime: {sessions_from!r}")
    common_ctes, params = _adjusted_params(
        t, security_ids, settings, include_dividends=include_dividends
    )
    with _sessions_registered(conn, include_dividends=include_dividends):
        _raise_on_invalid_factor(conn, common_ctes, params)
        frame = conn.execute(_adjusted_select(common_ctes), params).pl()
    if sessions_from is not None:
        frame = frame.filter(pl.col("session") >= sessions_from)
    return _traded(frame) if traded_only else frame


def _raise_on_invalid_factor(
    conn: duckdb.DuckDBPyConnection, common_ctes: str, params: list[Any]
) -> None:
    """Raise `ValueError` naming the first event whose own factor is
    non-positive or non-finite, or the first negative dividend amount
    (see this module's docstring), before anything calls `LN()`."""
    bad_rows = conn.execute(
        f"""
        WITH {common_ctes}
        SELECT security_id, ex_date, ratio_or_amount, factor
        FROM event_factor
        WHERE (factor IS NOT NULL AND NOT (factor > 0 AND isfinite(factor)))
           OR (action_type = 'dividend' AND ratio_or_amount < 0)
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


def _adjusted_select(common_ctes: str) -> str:
    """`adjusted_prices_as_of`'s main query over `common_ctes`."""
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
    common_ctes, params = _adjusted_params(t, security_ids, settings, include_dividends=True)
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

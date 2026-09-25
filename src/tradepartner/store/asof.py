"""As-of read primitives over the point-in-time store (spec req 8).

Every function here takes a tz-aware UTC `t` ("T" in the spec's
"Definitions": *what was knowable at time T?*) and returns only rows with
`known_at <= t`, the **latest revision** per natural key (spec
"Definitions" > Revision: "the row with the greatest `known_at <= T`").
Rows are never updated in place, so "latest revision" is always a query,
never a stored flag.

Only four of the six as-of functions the spec lists live here:
`prices_as_of`, `adjusted_prices_as_of`, `facts_as_of`, `listings_as_of`.
`securities_as_of`, `universe_as_of` and `survivorship_gap` are later plan
tasks (T8, T13, T15) and are out of scope for this module.

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

**`listings_as_of` and issue #35.** Spec req 8 says, literally, "All [as-of
functions] take tz-aware T and return only rows with `known_at <= T`" --
with no carve-out for any particular provenance. `listings_as_of` applies
that literally: a `snapshot_static` listing (fixture ticker `PRE9`,
`known_at` 2020-01-15, `valid_from` 2017-01-03) is invisible before its own
`known_at`, exactly like a `filing` or `snapshot` row would be, even though
its `valid_from` is years earlier. The spec's own definitions list
`snapshot_static` as "a snapshot attribute the config explicitly allows to
apply before its fetch time" -- but that exception is about which
*columns* a later consumer (T8's security master, T8's
`master.static_columns` config) is allowed to treat as valid before their
`known_at`, once it already knows about the row. It is not a claim that the
row itself becomes visible to an as-of read before its own `known_at`; a
consumer that has not yet been told a fact cannot apply it early. See
[issue #35](https://github.com/josejuarez96/tradepartner/issues/35) for the
tracked follow-up this raises for T8's `securities_as_of`/master code,
which will need to decide, separately, whether *its* read of a
`snapshot_static` row's `valid_from` may predate that row's own
`known_at` once the row is already visible. `tests/lookahead/harness.py`'s
`TruncatedStore` also truncates `listings` uniformly with every other
table, so the truncation-invariance test on `listings_as_of` exercises this
directly.

**Adjustment methodology (`adjusted_prices_as_of`).** A split or dividend
adjusts a bar at `session` only when it is **both** known
(`known_at <= t`, latest revision per `(security_id, action_type, ex_date)`
among rows known at `t`) **and already effective** (`ex_date <= t`) --
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
instead of dropping the dividend's factor. If a split and a dividend
share the same `ex_date`, the dividend's `prior_close` is still the
**raw**, pre-split close of the prior session (the `ASOF JOIN` reads from
`latest_bars`, never from an already-adjusted series) -- this matches the
standard convention that a dividend amount is quoted against the
pre-split price level on its own ex-date, and is simplest to reason
about, since `adjusted_prices_as_of` never adjusts a series and then reads
back from its own output.

Only `open`/`high`/`low`/`close` are adjusted; `volume` is left raw (out
of scope here -- no acceptance criterion in this task requires an
adjusted-volume convention, and the spec's "Data / interfaces" table
documents `prices_daily` volume as raw only).

**Every event's factor must be positive and finite, or this raises
`ValueError`.** A split `ratio_or_amount` of `0` divides by zero (DuckDB
returns `inf`, not an error, for `1.0 / 0.0`); a dividend `amount >=
prior_close` makes `1 - amount / prior_close` zero or negative, and
DuckDB's `LN()` (used by the cumulative-factor window function below)
*raises* on a non-positive input rather than returning `-inf`/`NaN`. Both
are bad store data, not "no factor" (`NULL`, which a dividend with no
known prior bar can legitimately produce and which this function treats
as "no adjustment from this event", not an error). Before ever computing
`LN()`, a dedicated query checks every non-`NULL` event factor for
`factor > 0 AND isfinite(factor)`; the first violation found raises
`ValueError` naming the `security_id`, `ex_date` and the offending
`ratio_or_amount`/`factor`, rather than letting a raw DuckDB
`OutOfRangeException` (for the `LN(0)`/`LN(negative)` case) or a silently
wrong `inf`-adjusted price series (for the zero-ratio case) reach the
caller.

**Why this runs as one SQL statement, not a Python loop per bar.** Every
step -- the latest-revision-as-of-`t` filter for both `prices_daily` and
`corporate_actions`, each event's own factor, the **cumulative** factor per
security (a running product of an event's factor and every later event's,
expressed as `EXP(SUM(LN(factor)) OVER (PARTITION BY security_id ORDER BY
ex_date DESC))` so it is a plain window aggregate, not a recursive query),
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

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.timeutil import ensure_tz_aware_utc

#: Natural key (excluding `known_at`) each table's rows are keyed by for
#: "latest revision as of T" purposes, and the `ORDER BY` each function
#: sorts its result by -- the columns the schema's own
#: `UNIQUE (..., known_at)` constraint names minus `known_at` itself.
#: `corporate_actions`'s key (`security_id, action_type, ex_date`) is not
#: listed here: `adjusted_prices_as_of` inlines its own latest-revision CTE
#: for that table rather than calling `_latest_as_of` (see this module's
#: docstring on why it runs as one SQL statement).
_PRICE_KEY: tuple[str, ...] = ("security_id", "session")
_FACT_KEY: tuple[str, ...] = ("security_id", "fact_name", "as_of_date", "class_member")
_LISTING_KEY: tuple[str, ...] = ("security_id", "ticker", "exchange", "valid_from")

#: The exchange's local timezone -- see this module's docstring ("Effective
#: is exchange-local, not UTC") for why `ex_date <= t` is compared in this
#: timezone's calendar date, not `t`'s UTC calendar date.
_EXCHANGE_TZ = ZoneInfo("America/New_York")


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
) -> pl.DataFrame:
    """The latest-revision-as-of-`t` rows of `table`, as a `polars.
    DataFrame` sorted by `key_columns`: one row per distinct `key_columns`
    tuple among rows with `known_at <= t`, the one with the greatest
    `known_at` (spec "Definitions" > Revision).

    `security_ids`, when given, restricts to those `security_id` values
    (an empty sequence restricts to none, returning an empty frame with
    the right schema -- see `_security_filter`); every table this is
    called for has a `security_id` column.
    """
    partition = ", ".join(key_columns)
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    sql = f"""
        SELECT * EXCLUDE (_rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {partition} ORDER BY known_at DESC
            ) AS _rn
            FROM {table}
            WHERE known_at <= ?
            {security_filter}
        )
        WHERE _rn = 1
        ORDER BY {partition}
    """
    return conn.execute(sql, params).pl()


def prices_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Raw `prices_daily` rows known by `t`: one per `(security_id,
    session)`, the latest revision as of `t` (spec acceptance: "A bar
    re-fetched with a different close becomes a second row ...
    `prices_as_of` returns each in its interval" -- i.e. calling this with a
    `t` before the revision's `known_at` returns the original row, and with
    a `t` at or after it returns the revision).
    """
    t = _validate_t(t)
    return _latest_as_of(conn, "prices_daily", _PRICE_KEY, t, security_ids)


def facts_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`facts` rows known by `t`: one per `(security_id, fact_name,
    as_of_date, class_member)`, the latest revision as of `t` (spec
    acceptance: "Restated shares fact: `facts_as_of(T)` returns the earlier
    value between the two `known_at`, the later after")."""
    t = _validate_t(t)
    return _latest_as_of(conn, "facts", _FACT_KEY, t, security_ids)


def listings_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`listings` rows known by `t`: one per `(security_id, ticker,
    exchange, valid_from)`, the latest revision as of `t`.

    Applies `known_at <= t` literally to every row regardless of
    provenance, including `snapshot_static` -- see this module's docstring
    and issue #35.
    """
    t = _validate_t(t)
    return _latest_as_of(conn, "listings", _LISTING_KEY, t, security_ids)


def _adjusted_ctes(action_types: str, bars_filter: str, actions_filter: str) -> str:
    """The CTEs `adjusted_prices_as_of` shares between its validation query
    (checks `event_factor` before anything calls `LN()` on it) and its main
    query (adds the cumulative-factor window and the `ASOF JOIN` to bars).

    `action_types` is `"'split'"` or `"'split', 'dividend'"` (always a
    literal, never user input); `bars_filter`/`actions_filter` are each
    `_security_filter`'s output for that CTE's own `?` placeholders.
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
        latest_actions AS (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY security_id, action_type, ex_date ORDER BY known_at DESC
                ) AS _rn
                FROM corporate_actions
                WHERE known_at <= ? AND ex_date <= ?
                  AND action_type IN ({action_types})
                {actions_filter}
            )
            WHERE _rn = 1
        ),
        -- Each event's own factor. `pb` (the ASOF-joined nearest bar with
        -- session < ex_date) is only read by the dividend branch; for a
        -- split row it is harmlessly present but unused. See this
        -- module's docstring on why this is an ASOF JOIN rather than an
        -- exact lookup of "the" prior XNYS session.
        event_factor AS (
            SELECT
                la.security_id,
                la.ex_date,
                la.action_type,
                la.ratio_or_amount,
                CASE la.action_type
                    WHEN 'split' THEN 1.0 / la.ratio_or_amount
                    ELSE 1.0 - la.ratio_or_amount / pb.close
                END AS factor
            FROM latest_actions la
            ASOF LEFT JOIN latest_bars pb
                ON pb.security_id = la.security_id AND pb.session < la.ex_date
        )
    """


def adjusted_prices_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
    *,
    include_dividends: bool = False,
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

    Raises `ValueError` if any known, effective event's own factor is
    non-positive or non-finite (a split `ratio_or_amount` of `0`, or a
    dividend `amount >= prior_close`).
    """
    t = _validate_t(t)
    t_session = t.astimezone(_EXCHANGE_TZ).date()

    action_types = "'split', 'dividend'" if include_dividends else "'split'"

    params: list[Any] = [t]
    bars_filter = _security_filter(security_ids, params)
    params.append(t)
    params.append(t_session)
    actions_filter = _security_filter(security_ids, params)

    common_ctes = _adjusted_ctes(action_types, bars_filter, actions_filter)

    bad_rows = conn.execute(
        f"""
        WITH {common_ctes}
        SELECT security_id, ex_date, ratio_or_amount, factor
        FROM event_factor
        WHERE factor IS NOT NULL AND NOT (factor > 0 AND isfinite(factor))
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

    sql = f"""
        WITH {common_ctes},
        -- Cumulative factor per security: the product of this event's own
        -- factor and every later event's (spec: a split or dividend
        -- adjusts every bar before its ex-date, and multiple later events
        -- compound). Expressed as EXP(SUM(LN(...))) so it is a plain
        -- window aggregate, not a recursive query. ORDER BY ex_date DESC
        -- is load-bearing: it accumulates from the latest event backward,
        -- so each event's cum_factor is "itself and everything after it";
        -- ASC would accumulate the wrong direction (see
        -- tests/store/test_asof.py's compounding test, which asserts
        -- distinct expected factors per date range and fails under ASC).
        cum AS (
            SELECT security_id, ex_date,
                EXP(SUM(LN(factor)) OVER (
                    PARTITION BY security_id ORDER BY ex_date DESC
                )) AS cum_factor
            FROM event_factor
            WHERE factor IS NOT NULL
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
    return conn.execute(sql, params).pl()

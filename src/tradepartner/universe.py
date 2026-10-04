"""The tradable universe as of T (ADR 0006 rules 1-8; spec req 8; plan T13).

`universe_as_of(conn, t, settings)` rebuilds the universe from rows known at
`t` only, applying the ADR's rules **in order**; each excluded security is
reported once, under the first rule it fails:

1. `security_type`: the latest classification known at `t` is in
   `universe.security_types`. No classification row is `unclassified`.
2. `exchange`: the security's current listing at T's session is live and
   on one of `universe.exchanges`. The current listing is the one with the
   latest `valid_from` on or before the session (a ticker change or a
   transfer supersedes the older row). It is live while `listed`, or while
   `transferred` up to its end session; a `delisted` listing (a Form 25
   known at `t`) is out from the filing's `known_at`, even while bars
   continue, so nothing enters a name on its way out.
3. `sector`: the classification's SIC is outside every guarded
   `universe.exclude_sic_ranges` range. A missing SIC passes.
4. `price`: the raw close at the session is at least `universe.min_price`
   (spec "Level rules": raw close, never adjusted). A name with no bar at
   the session passes here and fails rule 6, so the gap report counts it
   as missing data rather than as a price exclusion.
5. `liquidity`: the median raw close x raw volume over the bars in the
   last `universe.liquidity_window` sessions is at least
   `universe.min_median_dollar_volume`. Skipped, and recorded as disabled,
   when `universe.liquidity_rule_enabled` is false.
6. `history`: every XNYS session in the `universe.min_history_months`
   calendar months up to and including the session has a bar
   (`missing_bars`), and no session in them carries a price jump
   (`price_jump`, #787): `store.asof.price_jumps_as_of` at `t`, not in
   `universe.accepted_price_jumps`. `price_jump` is not missing data.
7. `shares`: the latest `shares_outstanding` fact known at `t` is at most
   `universe.max_shares_age_days` old at the session. Rows sharing that
   `as_of_date` are never summed: the one row with a class member wins
   over an undimensioned total (`''`); two class-member rows on one
   security are `ambiguous_shares`. A fact out of line with the
   security's last accepted earlier fact (ratio, after the splits known
   at `t` between them, above `universe.max_shares_ratio` or below its
   inverse) is rejected unless `universe.accepted_shares_facts` names it,
   and the last accepted fact is used instead, its age judged the same
   way (#845, `shares_as_of`; `Universe.shares_fallbacks` lists them).
8. `size`: companies (one `cik`) with a class that passed rules 1-7,
   ranked by market cap; the top `universe.top_n_by_cap` kept, and each of
   their classes that passed rules 1-7 admitted. A class's cap is its
   shares, adjusted for every split known at `t` with
   `as_of_date < ex_date <= session`, times its raw close at the session.
   A company's cap sums **every** class that passed rules 1-3, has a bar
   at the session and passes rule 7: size is the company's, tradability
   (rules 4-6) the class's, so a class failing liquidity or history does
   not shrink its company (ADR 0006: "summed over all classes"). Ties rank
   by `cik`.

Every rule reads bars with `traded_only=True` (#787): a zero-volume bar is
missing, not a price, so it fails rule 6 and is no close for rules 4 and 8
and no dollar volume for rule 5.

Rules 1, 6 and 7 are the missing-data exclusions the survivorship-gap
report (T15) counts: their reasons are `MISSING_DATA_REASONS`. Rule 1's
other reasons (`etf`, `preferred`, ...) are not missing data.
Every threshold comes from `settings.universe`; this module holds no
numeric literal but 0, 1 and -1 (spec; tested by AST).

The store's shares fact is named `shares_outstanding`; ingest (T16) maps
EDGAR's `EntityCommonStockSharesOutstanding` to it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import last_completed_session, previous_session, sessions_in_month_window
from tradepartner.config import Settings, get_settings, parse_accepted_shares_fact
from tradepartner.store.asof import (
    _validate_t,
    facts_as_of,
    live_actions_as_of,
    price_jumps_as_of,
    prices_as_of,
)
from tradepartner.store.classify import classifications_as_of
from tradepartner.store.delistings import DELISTED, LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.master import securities_as_of

#: ADR 0006 rules in order; a rule's number is its position plus one.
RULES: tuple[str, ...] = (
    "security_type",
    "exchange",
    "sector",
    "price",
    "liquidity",
    "history",
    "shares",
    "size",
)

#: The store's `fact_name` for shares outstanding (see the module docstring).
SHARES_FACT = "shares_outstanding"

#: Exclusion reasons that mean missing data (rules 1, 6, 7), for T15.
MISSING_DATA_REASONS: frozenset[str] = frozenset(
    {
        "unclassified",
        "unclassifiable",
        "missing_bars",
        "no_shares",
        "stale_shares",
        "ambiguous_shares",
    }
)


_MEMBER_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "cik": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "close": pl.Float64,
    "shares": pl.Float64,
    "market_cap": pl.Float64,
    "company_cap": pl.Float64,
    "company_rank": pl.Int64,
}
#: `SharesPick.outliers`: a fact out of line with the last accepted earlier
#: fact (`baseline_*`, raw), `ratio` = value over the baseline moved by splits;
#: a zero or negative value has a null `ratio` (and null `baseline_*` when it
#: comes first).
SHARES_OUTLIER_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "as_of_date": pl.Date,
    "value": pl.Float64,
    "baseline_as_of": pl.Date,
    "baseline_value": pl.Float64,
    "ratio": pl.Float64,
    "accepted": pl.Boolean,
}
#: `SharesPick.fallbacks` and `Universe.shares_fallbacks`: the rejected latest
#: fact (`as_of_date`, `value`, `ratio`) and the accepted one used instead.
SHARES_FALLBACK_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "as_of_date": pl.Date,
    "value": pl.Float64,
    "used_as_of": pl.Date,
    "used_value": pl.Float64,
    "ratio": pl.Float64,
}
_EXCLUSION_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "rule": pl.Int64,
    "rule_name": pl.Utf8,
    "reason": pl.Utf8,
}


@dataclass(frozen=True)
class Universe:
    """`universe_as_of`'s result.

    `members`: one row per admitted class, sorted by company rank then
    `security_id`. `exclusions`: one row per excluded security, with the
    first rule it failed (`rule` 1-8, `rule_name`) and a `reason`.
    `rules_enabled`: every rule by name. `settings`: the `universe` config
    and `execution.fill_price` the result was built with. `shares_fallbacks`:
    the classes that passed rules 1-3 whose latest shares fact was rejected
    as out of line (#845), with the fact used instead
    (`SHARES_FALLBACK_SCHEMA`); such a class may still fail rule 7 as
    `stale_shares` when that fact is too old.
    """

    t: datetime
    session: date
    members: pl.DataFrame
    exclusions: pl.DataFrame
    rules_enabled: dict[str, bool]
    settings: dict[str, Any]
    shares_fallbacks: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=SHARES_FALLBACK_SCHEMA)
    )


@cache
def _last_sessions(session: date, count: int) -> frozenset[date]:
    """`session` and the `count - 1` XNYS sessions before it."""
    out = [session]
    for _ in range(count - 1):
        out.append(previous_session(out[-1]))
    return frozenset(out)


def _current_listings(
    conn: duckdb.DuckDBPyConnection, t: datetime, session: date, settings: Settings
) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from` on or
    before `session`, with `status` and `end_session` at `t`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listing_ends_as_of(conn, t, settings).iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        held = current.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            current[row["security_id"]] = row
    return current


def _listing_reason(listing: dict[str, Any] | None, session: date, exchanges: list[str]) -> str:
    """Why a listing fails rule 2, or `""` if it passes."""
    if listing is None:
        return "not_listed"
    status, end = listing["status"], listing["end_session"]
    if status == DELISTED:
        return "delisted"
    if status == TRANSFERRED and (end is None or end < session):
        return "not_listed"
    if status not in (LISTED, TRANSFERRED):
        return f"status:{status}"
    if listing["exchange"] not in exchanges:
        return f"exchange:{listing['exchange']}"
    return ""


def _split_factors(
    conn: duckdb.DuckDBPyConnection, t: datetime, ids: list[str], session: date
) -> dict[str, list[tuple[date, float]]]:
    """Per security, `(ex_date, ratio)` of every split known at `t` with
    `ex_date <= session`: the latest revision per action identity, so a
    re-dated split counts once at its latest ex-date and a cancelled one
    not at all (#108)."""
    actions = live_actions_as_of(conn, t, ids)
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in actions.iter_rows(named=True):
        if row["action_type"] == "split" and row["ex_date"] <= session:
            out[row["security_id"]].append((row["ex_date"], row["ratio_or_amount"]))
    return out


@dataclass(frozen=True)
class SharesPick:
    """`shares_as_of`'s result.

    `shares`: per security, `(as_of_date, value)` of the fact rules 7 and 8
    use, raw (not split-moved). `ambiguous`: the ids whose latest
    `as_of_date` holds two class-member rows. `outliers`: every fact known at
    `t` out of line with its last accepted earlier fact, accepted by the
    owner or not (`SHARES_OUTLIER_SCHEMA`). `fallbacks`: the securities whose
    latest fact was rejected, with the fact used instead
    (`SHARES_FALLBACK_SCHEMA`).
    """

    shares: dict[str, tuple[date, float]]
    ambiguous: set[str]
    outliers: pl.DataFrame
    fallbacks: pl.DataFrame


def _date_picks(rows: list[dict[str, Any]]) -> dict[date, float | None]:
    """Per `as_of_date`, the one value rule 7 reads (a class row wins over an
    undimensioned total), or `None` when two class rows make it ambiguous."""
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["as_of_date"]].append(row)
    picks: dict[date, float | None] = {}
    for as_of in sorted(by_date):
        classed = [r for r in by_date[as_of] if r["class_member"]]
        chosen = classed or by_date[as_of]
        picks[as_of] = chosen[0]["value"] if len(chosen) == 1 else None
    return picks


def shares_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    ids: Sequence[str] | None,
    settings: Settings,
) -> SharesPick:
    """Rule 7's shares selection at `t` with the plausibility check (#845).

    Per security (every one when `ids` is `None`), its `shares_outstanding`
    facts known at `t` are walked in `as_of_date` order. The first is
    accepted, unless it is zero or negative: such a value is always
    rejected and never a baseline. Each later one is compared with the last accepted fact moved by
    every split known at `t` with `accepted_as_of < ex_date <= as_of_date`:
    a ratio above `universe.max_shares_ratio` or below its inverse is out of
    line and rejected, unless `universe.accepted_shares_facts` names it. A
    date with two class rows is skipped by the walk. The latest date decides:
    ambiguous, its own value when accepted, else the last accepted fact.
    Only facts and splits known at `t` are read, never a later filing.
    """
    cfg = settings.universe
    accepted_list = {parse_accepted_shares_fact(e) for e in cfg.accepted_shares_facts}
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in facts_as_of(conn, t, ids).iter_rows(named=True):
        if row["fact_name"] == SHARES_FACT:
            rows[row["security_id"]].append(row)
    splits: dict[str, list[tuple[date, float]]] = defaultdict(list)
    split_ids = None if ids is None else sorted(rows)
    for action in live_actions_as_of(conn, t, split_ids).iter_rows(named=True):
        if action["action_type"] == "split":
            splits[action["security_id"]].append((action["ex_date"], action["ratio_or_amount"]))

    shares: dict[str, tuple[date, float]] = {}
    ambiguous: set[str] = set()
    outliers: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for sid in sorted(rows):
        picks = _date_picks(rows[sid])
        base: tuple[date, float] | None = None
        latest_ok = False
        for as_of, value in picks.items():
            latest_ok = False
            if value is None:
                continue
            if value <= 0:
                # Not a share count: always rejected, never a baseline, and no
                # owner entry accepts it.
                outliers.append(
                    {
                        "security_id": sid,
                        "as_of_date": as_of,
                        "value": value,
                        "baseline_as_of": base[0] if base else None,
                        "baseline_value": base[1] if base else None,
                        "ratio": None,
                        "accepted": False,
                    }
                )
                continue
            if base is None:
                base, latest_ok = (as_of, value), True
                continue
            moved = base[1]
            for ex_date, ratio in splits.get(sid, []):
                if base[0] < ex_date <= as_of:
                    moved *= ratio
            change = value / moved if moved > 0 else float("inf")
            out_of_line = not (1 / cfg.max_shares_ratio <= change <= cfg.max_shares_ratio)
            owner_ok = (sid, as_of) in accepted_list
            if out_of_line:
                outliers.append(
                    {
                        "security_id": sid,
                        "as_of_date": as_of,
                        "value": value,
                        "baseline_as_of": base[0],
                        "baseline_value": base[1],
                        "ratio": change,
                        "accepted": owner_ok,
                    }
                )
            if not out_of_line or owner_ok:
                base, latest_ok = (as_of, value), True
        latest_as_of = max(picks)
        latest_value = picks[latest_as_of]
        if latest_value is None:
            ambiguous.add(sid)
            continue
        if base is None:
            continue
        shares[sid] = base
        if not latest_ok:
            fallbacks.append(
                {
                    "security_id": sid,
                    "as_of_date": latest_as_of,
                    "value": latest_value,
                    "used_as_of": base[0],
                    "used_value": base[1],
                    "ratio": outliers[-1]["ratio"],
                }
            )
    return SharesPick(
        shares=shares,
        ambiguous=ambiguous,
        outliers=pl.DataFrame(outliers, schema=SHARES_OUTLIER_SCHEMA).sort(
            "security_id", "as_of_date"
        ),
        fallbacks=pl.DataFrame(fallbacks, schema=SHARES_FALLBACK_SCHEMA).sort("security_id"),
    )


def latest_shares_as_of(
    conn: duckdb.DuckDBPyConnection, t: datetime, ids: Sequence[str], settings: Settings
) -> tuple[dict[str, tuple[date, float]], set[str]]:
    """Per security, `(as_of_date, value)` of the shares fact rule 7 uses at
    `t` (`shares_as_of`: the latest accepted fact), and the ids whose latest
    `as_of_date` is ambiguous (two class-member rows)."""
    pick = shares_as_of(conn, t, ids, settings)
    return pick.shares, pick.ambiguous


def universe_as_of(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings | None = None
) -> Universe:
    """The universe at `t` from rows known at `t` (see the module docstring
    for the rules). `settings` defaults to `get_settings()` and is the only
    source of every `universe.*` value, also passed to `listing_ends_as_of`.
    A bare date raises `TypeError`, a naive datetime `ValueError`."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    cfg = settings.universe
    session = last_completed_session(t)

    securities = {r["security_id"]: r for r in securities_as_of(conn, t).iter_rows(named=True)}
    alive = sorted(securities)
    exclusions: list[dict[str, Any]] = []

    def apply(rule: str, reasons: dict[str, str]) -> None:
        nonlocal alive
        for sid in alive:
            if reasons.get(sid):
                number = RULES.index(rule) + 1
                exclusions.append(
                    {"security_id": sid, "rule": number, "rule_name": rule, "reason": reasons[sid]}
                )
        alive = [sid for sid in alive if not reasons.get(sid)]

    classes = {r["security_id"]: r for r in classifications_as_of(conn, t).iter_rows(named=True)}
    apply(
        "security_type",
        {
            sid: (
                "unclassified"
                if sid not in classes
                else ""
                if classes[sid]["security_type"] in cfg.security_types
                else classes[sid]["security_type"]
            )
            for sid in alive
        },
    )

    listings = _current_listings(conn, t, session, settings)
    apply(
        "exchange",
        {sid: _listing_reason(listings.get(sid), session, cfg.exchanges) for sid in alive},
    )

    def utility(sid: str) -> str:
        sic = classes[sid]["sic"]
        hit = sic is not None and any(lo <= sic <= hi for lo, hi in cfg.exclude_sic_ranges)
        return f"sic:{sic}" if hit else ""

    apply("sector", {sid: utility(sid) for sid in alive})

    bars: dict[str, dict[date, tuple[float, int]]] = defaultdict(dict)
    for row in prices_as_of(conn, t, alive, traded_only=True).iter_rows(named=True):
        if row["session"] <= session:
            bars[row["security_id"]][row["session"]] = (row["close"], row["volume"])
    sized = list(alive)  # passed rules 1-3: the classes a company's cap may sum
    close = {sid: bars[sid][session][0] for sid in alive if session in bars[sid]}
    apply(
        "price",
        {
            sid: "min_price" if close.get(sid, cfg.min_price) < cfg.min_price else ""
            for sid in alive
        },
    )

    if cfg.liquidity_rule_enabled:
        window = _last_sessions(session, cfg.liquidity_window)

        def illiquid(sid: str) -> str:
            dollars = [c * v for s, (c, v) in bars[sid].items() if s in window]
            ok = dollars and statistics.median(dollars) >= cfg.min_median_dollar_volume
            return "" if ok else "min_median_dollar_volume"

        apply("liquidity", {sid: illiquid(sid) for sid in alive})

    history = sessions_in_month_window(session, cfg.min_history_months)
    apply(
        "history",
        {sid: "" if all(s in bars[sid] for s in history) else "missing_bars" for sid in alive},
    )
    jumps = price_jumps_as_of(conn, t, alive, settings=settings).filter(
        ~pl.col("accepted") & pl.col("session").is_in(list(history))
    )
    apply("history", dict.fromkeys(jumps["security_id"].to_list(), "price_jump"))

    pick = shares_as_of(conn, t, sized, settings)
    latest_shares, ambiguous = pick.shares, pick.ambiguous

    def shares_reason(sid: str) -> str:
        if sid in ambiguous:
            return "ambiguous_shares"
        if sid not in latest_shares:
            return "no_shares"
        age = (session - latest_shares[sid][0]).days
        return "stale_shares" if age > cfg.max_shares_age_days else ""

    apply("shares", {sid: shares_reason(sid) for sid in alive})

    splits = _split_factors(conn, t, sized, session)
    capped = [sid for sid in sized if sid in close and not shares_reason(sid)]
    shares: dict[str, float] = {}
    for sid in capped:
        as_of, value = latest_shares[sid]
        for ex_date, ratio in splits.get(sid, []):
            if ex_date > as_of:
                value *= ratio
        shares[sid] = value
    company_cap: dict[str, float] = defaultdict(float)
    for sid in capped:
        company_cap[securities[sid]["cik"]] += shares[sid] * close[sid]
    ranked = sorted(
        {securities[sid]["cik"] for sid in alive}, key=lambda cik: (-company_cap[cik], cik)
    )
    rank = {cik: position + 1 for position, cik in enumerate(ranked)}
    kept = set(ranked[: cfg.top_n_by_cap])
    apply("size", {sid: "" if securities[sid]["cik"] in kept else "top_n_by_cap" for sid in alive})

    members = [
        {
            "security_id": sid,
            "cik": securities[sid]["cik"],
            "ticker": listings[sid]["ticker"],
            "exchange": listings[sid]["exchange"],
            "close": close[sid],
            "shares": shares[sid],
            "market_cap": shares[sid] * close[sid],
            "company_cap": company_cap[securities[sid]["cik"]],
            "company_rank": rank[securities[sid]["cik"]],
        }
        for sid in alive
    ]
    return Universe(
        t=t,
        session=session,
        members=pl.DataFrame(members, schema=_MEMBER_SCHEMA).sort("company_rank", "security_id"),
        exclusions=pl.DataFrame(exclusions, schema=_EXCLUSION_SCHEMA).sort("security_id"),
        rules_enabled={rule: rule != "liquidity" or cfg.liquidity_rule_enabled for rule in RULES},
        settings={
            "universe": cfg.model_dump(mode="json"),
            "fill_price": settings.execution.fill_price,
        },
        shares_fallbacks=pick.fallbacks,
    )

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
4. `price`: the raw close of the latest bar on or before the session is at
   least `universe.min_price` (spec "Level rules": raw close, never
   adjusted).
5. `liquidity`: the median raw close x raw volume over the bars in the
   last `universe.liquidity_window` sessions is at least
   `universe.min_median_dollar_volume`. Skipped, and recorded as disabled,
   when `universe.liquidity_rule_enabled` is false.
6. `history`: every XNYS session in the `universe.min_history_months`
   calendar months up to and including the session has a bar.
7. `shares`: the latest `shares_outstanding` fact known at `t` is at most
   `universe.max_shares_age_days` old at the session.
8. `size`: companies (one `cik`) ranked by market cap, the top
   `universe.top_n_by_cap` kept, and every class of a kept company that
   passed rules 1-7 admitted. A class's cap is its shares, adjusted for
   every split known at `t` with `as_of_date < ex_date <= session`, times
   its raw close; a company's cap sums its classes that passed rules 1-7.
   Ties rank by `cik`.

Rules 1, 6 and 7 are the missing-data exclusions the survivorship-gap
report (T15) counts; each exclusion's `reason` says which kind it was.
Every threshold comes from `settings.universe`; this module holds no
numeric literal but 0, 1 and -1 (spec; tested by AST).

The store's shares fact is named `shares_outstanding`; ingest (T16) maps
EDGAR's `EntityCommonStockSharesOutstanding` to it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import last_completed_session, previous_session, sessions_in_month_window
from tradepartner.config import Settings, get_settings
from tradepartner.store.asof import _latest_as_of, _validate_t, facts_as_of, prices_as_of
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

_ACTION_KEY = ("security_id", "action_type", "ex_date")

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
    and `execution.fill_price` the result was built with.
    """

    t: datetime
    session: date
    members: pl.DataFrame
    exclusions: pl.DataFrame
    rules_enabled: dict[str, bool]
    settings: dict[str, Any]


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
    `ex_date <= session`."""
    actions = _latest_as_of(conn, "corporate_actions", _ACTION_KEY, t, ids)
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in actions.iter_rows(named=True):
        if row["action_type"] == "split" and row["ex_date"] <= session:
            out[row["security_id"]].append((row["ex_date"], row["ratio_or_amount"]))
    return out


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
    for row in prices_as_of(conn, t, alive).iter_rows(named=True):
        if row["session"] <= session:
            bars[row["security_id"]][row["session"]] = (row["close"], row["volume"])
    close = {sid: bars[sid][max(bars[sid])][0] for sid in alive if bars[sid]}
    apply(
        "price",
        {
            sid: "no_bar" if sid not in close else "min_price" if close[sid] < cfg.min_price else ""
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

    latest_shares: dict[str, tuple[date, float]] = {}
    for row in facts_as_of(conn, t, alive).iter_rows(named=True):
        if row["fact_name"] != SHARES_FACT:
            continue
        held = latest_shares.get(row["security_id"])
        if held is None or row["as_of_date"] > held[0]:
            latest_shares[row["security_id"]] = (row["as_of_date"], row["value"])
        elif row["as_of_date"] == held[0]:
            latest_shares[row["security_id"]] = (held[0], held[1] + row["value"])

    def shares_reason(sid: str) -> str:
        if sid not in latest_shares:
            return "no_shares"
        age = (session - latest_shares[sid][0]).days
        return "stale_shares" if age > cfg.max_shares_age_days else ""

    apply("shares", {sid: shares_reason(sid) for sid in alive})

    splits = _split_factors(conn, t, alive, session)
    shares: dict[str, float] = {}
    for sid in alive:
        as_of, value = latest_shares[sid]
        for ex_date, ratio in splits.get(sid, []):
            if ex_date > as_of:
                value *= ratio
        shares[sid] = value
    company_cap: dict[str, float] = defaultdict(float)
    for sid in alive:
        company_cap[securities[sid]["cik"]] += shares[sid] * close[sid]
    ranked = sorted(company_cap, key=lambda cik: (-company_cap[cik], cik))
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
    )

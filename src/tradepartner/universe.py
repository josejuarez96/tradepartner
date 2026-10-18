"""The tradable universe as of T (ADR 0006 rules 1-8; spec req 8; plan T13).

`universe_as_of(conn, t, settings)` rebuilds the universe from rows known at
`t` only, applying the ADR's rules **in order**; each excluded security is
reported once, under the first rule it fails:

1. `security_type`: the latest classification known at `t` is in
   `universe.security_types`. No classification row is `unclassified`.
2. `exchange`: the security's current listing at T's session is live and
   on one of `universe.exchanges`. The current listing is the one with the
   latest `valid_from` on or before the session (a ticker change or a
   transfer supersedes the older row). When the rows of one class share
   that latest `valid_from` (filed on two exchanges, #1574), a row on a
   venue in `universe.exchanges` wins, whatever order the names sort in.
   A newer row on a real venue that is not on the list (`NYSE_CHICAGO`,
   `NYSETX`: a secondary quote of the same class, #1682 lever A) does not
   hide a listing that is still live on a venue in `universe.exchanges`:
   the security passes while any of its rows at or before the session is
   live on that list. A newer row on `OTC`, `NONE`, no name or any value
   outside `_SECONDARY_VENUES` is a move off the exchange and still fails,
   and a listing ended by its own Form 25 is not live. It is live while `listed`, or while
   `transferred` up to its end session; a `delisted` listing (a Form 25
   known at `t`) is out from the filing's `known_at`, even while bars
   continue, so nothing enters a name on its way out.
3. `sector`: the classification's SIC is outside every
   `universe.exclude_sic_ranges` range. A missing SIC passes. A per-hypothesis
   choice, default none (ADR 0006 amendment 2026-10-11, #1540): under the
   default no row carries this reason.
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
   A jump candidate with an invalid corporate action (a negative dividend,
   a non-positive or non-finite factor) raises `ValueError` naming the
   security, from `price_jumps_as_of` (#1119): bad action data fails
   loud instead of being read as a jump's explanation.
7. `shares`: the latest `shares_outstanding` fact known at `t` is at most
   `universe.max_shares_age_days` old at the session. Rows sharing that
   `as_of_date` are never summed: the one row with a class member wins
   over an undimensioned total (`''`); two class-member rows on one
   security are `ambiguous_shares`, unless the class-member link settles
   them (#1682, T190): a member *links* to the security's class when it
   names the class letter its stored title names (or neither names one)
   and every other qualifying word of the member ("Nonvoting",
   "Exchangeable", "Preferred", ...) is in the title; titles are the
   security's `listings` rows known at `t` and the slug in its id. The
   linked rows decide the date when there are any; rows whose values are
   all equal collapse to that value; only different values with no single
   linked value stay `ambiguous_shares`. A fact out of line with the
   security's last accepted earlier fact (ratio, after the splits known
   at `t` between them, above `universe.max_shares_ratio` or below its
   inverse) is rejected unless `universe.accepted_shares_facts` names it,
   and the last accepted fact is used instead, its age judged the same
   way (#845, `shares_as_of`; `Universe.shares_fallbacks` lists them).
   A run of rejected facts, each in line with the one before it, that
   spans more than `universe.max_shares_age_days` re-anchors the baseline
   on its latest fact (#853), so a mis-scaled first fact cannot keep a
   name out for good. A fact dated before the security's first stored bar
   session known at `t` is a *pre-trading seed* -- and is no baseline --
   when the first fact on or after that session is more than
   `universe.max_shares_ratio` times it (#1682, T190b): every such seed is
   skipped and that first post-bar fact anchors, so an issuer's one to
   several tiny pre-trading seeds do not out-rank every later true fact.
   Everything else keeps today's behaviour, a normal pre-listing fact
   included. An out-of-line fact is
   *explained*, and accepted as the new baseline, when an event known at
   `t` lies between its baseline's `as_of_date` and its own (#1682, T190):
   a `spinoff` action whose child is this security.
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

   `universe.rank_by` (ADR 0023 D4, T212; default `cap`, which is everything
   above, byte for byte) switches rules 7 and 8 for a forward book. Under
   `dollar_volume` rule 7 is skipped (no shares fact is read: `rules_enabled`
   says so and `shares_fallbacks` is empty) and rule 8 ranks companies by the
   sum of their classes' median raw close x raw volume over the
   `universe.min_history_months` window (every class that passed rules 1-6
   has a bar on each of its sessions), from bars known at `t` alone; ties
   rank by `cik`. The top `universe.top_n_by_cap` are kept, then each kept
   class is intersected with Alpaca's tradable assets as of `t`
   (`borrow_status_as_of`: the latest row per asset with `known_at <= t`,
   `status = 'active'` and `tradable`, matched on the class's Alpaca symbol:
   `master_symbol` with `alpaca.class_symbols`, as the broker names it); a kept class with no such
   asset is excluded under rule 8 with reason `untradable_asset`. **Before the
   first assets snapshot with `known_at <= t` the intersection is skipped**, never today's list read
   into the past: `Universe.assets_intersection_skipped` is true and
   `n_assets_intersected` 0.

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

import math
import re
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
from tradepartner.config import Settings, UniverseConfig, get_settings, parse_accepted_shares_fact
from tradepartner.store.asof import (
    _validate_t,
    borrow_status_as_of,
    facts_as_of,
    listings_as_of,
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

#: `borrow_status.status` of an asset Alpaca lists as trading.
_ASSET_ACTIVE = "active"

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
#: comes first). `reanchored` is true on the fact that re-anchored the
#: baseline (#853); it keeps `accepted` false (the owner did not accept it), so
#: health's review list still shows it. `explained` is true on a fact accepted
#: as the new baseline because a spin-off known at `t` (this security its
#: child) lies between it and its baseline (T190); it also keeps `accepted`
#: false, so health's review list still shows it.
SHARES_OUTLIER_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "as_of_date": pl.Date,
    "value": pl.Float64,
    "baseline_as_of": pl.Date,
    "baseline_value": pl.Float64,
    "ratio": pl.Float64,
    "accepted": pl.Boolean,
    "reanchored": pl.Boolean,
    "explained": pl.Boolean,
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
#: The one extra member column under `rank_by = dollar_volume`: the company's
#: ranking value (its classes' summed median dollar volume). `shares`,
#: `market_cap` and `company_cap` are null there: no shares fact is read.
_DOLLAR_VOLUME_MEMBER_SCHEMA: dict[str, Any] = {
    **_MEMBER_SCHEMA,
    "company_dollar_volume": pl.Float64,
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

    `n_assets_intersected` and `assets_intersection_skipped` record the
    `rank_by = dollar_volume` intersection (module docstring): the kept
    classes checked against the assets snapshot, and whether the
    intersection was skipped because no snapshot was known at `t`. Both are
    0 and false under `cap`.
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
    n_assets_intersected: int = 0
    assets_intersection_skipped: bool = False


@cache
def _last_sessions(session: date, count: int) -> frozenset[date]:
    """`session` and the `count - 1` XNYS sessions before it."""
    out = [session]
    for _ in range(count - 1):
        out.append(previous_session(out[-1]))
    return frozenset(out)


#: Real venues off `universe.exchanges` whose newer row is a secondary quote
#: of a class listed on the list (T196). An allow-list: any other value
#: (`OTC`, `NONE`, blank, an OTC tier's slug, an unknown name) is not a
#: venue the rule vouches for and still fails.
_SECONDARY_VENUES = frozenset(
    {
        "NYSE_CHICAGO",
        "NYSETX",
        "NYSE_NATIONAL",
        "NASDAQ_BX",
        "NASDAQ_PHLX",
        "NYSE_ARCA",
        "CBOE_BZX",
        "CBOE",
    }
)


def _is_live(row: dict[str, Any], session: date) -> bool:
    """Whether a listing row (with `status` and `end_session`) is live at `session`."""
    if row["status"] == LISTED:
        return True
    return (
        row["status"] == TRANSFERRED
        and row["end_session"] is not None
        and (row["end_session"] >= session)
    )


def _current_listings(
    conn: duckdb.DuckDBPyConnection, t: datetime, session: date, settings: Settings
) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from` on or
    before `session`, with `status` and `end_session` at `t`.

    When the rows of one security share that latest `valid_from` (one class
    filed on two exchanges, #1574), the tie goes to a row whose `exchange` is
    in `universe.exchanges`, whatever order the names sort in; with no such
    row, `listing_ends_as_of`'s frame order still decides the tie.

    Each listing also carries `on_listed_exchange` (T196, #1682 lever A):
    whether any row of the security at or before `session`, whatever its
    `valid_from`, is live at `session` on a venue in `universe.exchanges`."""
    exchanges = frozenset(settings.universe.exchanges)
    current: dict[str, dict[str, Any]] = {}
    on_list: set[str] = set()
    for row in listing_ends_as_of(conn, t, settings).iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        if row["exchange"] in exchanges and _is_live(row, session):
            on_list.add(row["security_id"])
        held = current.get(row["security_id"])
        newer = held is None or row["valid_from"] > held["valid_from"]
        # A same-day tie (#1574): prefer a venue on the universe's list over
        # one off it, so the tie does not turn on how the names sort.
        tie_to_universe = (
            held is not None
            and row["valid_from"] == held["valid_from"]
            and row["exchange"] in exchanges
            and held["exchange"] not in exchanges
        )
        if newer or tie_to_universe:
            current[row["security_id"]] = row
    return {sid: {**row, "on_listed_exchange": sid in on_list} for sid, row in current.items()}


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
        # A secondary quote on a real venue does not hide a live listing on the list.
        if listing["on_listed_exchange"] and listing["exchange"] in _SECONDARY_VENUES:
            return ""
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
    owner or not, re-anchored or not (`SHARES_OUTLIER_SCHEMA`). `fallbacks`: the securities whose
    latest fact was rejected, with the fact used instead
    (`SHARES_FALLBACK_SCHEMA`).
    """

    shares: dict[str, tuple[date, float]]
    ambiguous: set[str]
    outliers: pl.DataFrame
    fallbacks: pl.DataFrame


#: A class member naming its class by letter (`CommonClassB`,
#: `ClassBExchangeableShares`, `SeriesBCommonStock`); `ClassUndefined` names
#: none. Ingest's `fact_rows` reads `Class<letter>` only (#1169).
_MEMBER_LETTER = re.compile(r"(?:Class|Series)([A-Z])(?![a-z])")
#: A title naming its class by letter ("Class B Common Stock", "Series C
#: common stock"; ingest's `_TITLE_CLASS`, #1166), on the lower-cased title.
_TITLE_LETTER = re.compile(r"\bclass ([a-z])\b|^\s*series ([a-z])\b")
#: Member words that qualify no class: the generic nouns of an equity member.
_NEUTRAL_MEMBER_WORDS = frozenset(
    {"common", "stock", "class", "share", "shares", "ordinary", "undefined", "member"}
)


def _member_letter(member: str) -> str | None:
    match = _MEMBER_LETTER.search(member)
    return match.group(1) if match else None


def _title_letter(title: str) -> str | None:
    match = _TITLE_LETTER.search(title.lower())
    return next(g for g in match.groups() if g).upper() if match else None


def _title_words(title: str) -> set[str]:
    """The title's lower-case words, "non-voting" and "non voting" read as
    one word "nonvoting" (any `non` prefix joins its word)."""
    folded = re.sub(r"\bnon[\s-]+", "non", title.lower())
    return set(re.findall(r"[a-z]+", folded))


def _member_words(member: str) -> set[str]:
    """The member's camel-case words, lower case, a `Non` joined to the word
    after it (`NonVotingCommonStock` -> nonvoting, common, stock)."""
    words = [w.lower() for w in re.findall(r"[A-Z][a-z]+|[a-z]+", member)]
    out: list[str] = []
    for word in words:
        if out and out[-1] == "non":
            out[-1] = "non" + word
        else:
            out.append(word)
    return set(out)


def member_links_title(member: str, title: str) -> bool:
    """Whether the XBRL class member `member` names the class titled `title`
    (#1682, T190). Every qualifying word of the member (camel-case split,
    less the generic nouns `_NEUTRAL_MEMBER_WORDS` and single letters) is a
    word of the title, with "non-voting" read as "nonvoting"; and a member
    naming a class letter matches the letter the title names, while a member
    naming none links a lettered title only through such a qualifier. So
    `CommonStock` links "Common Stock" and `NonvotingCommonStock` does not;
    `CommonClassB` links "Class B Common Stock" and `ClassBExchangeableShares`
    does not; `NonvotingCommonStock` links "Class B Common Stock (nonvoting)"
    and `CommonStock` does not. An empty member (an undimensioned total)
    links nothing. Pure: no per-name list."""
    if not member:
        return False
    qualifiers = {w for w in _member_words(member) if w not in _NEUTRAL_MEMBER_WORDS | {"series"}}
    qualifiers = {w for w in qualifiers if len(w) > 1}
    if not qualifiers <= _title_words(title):
        return False
    letter, title_letter = _member_letter(member), _title_letter(title)
    if letter is not None or title_letter is None:
        return letter == title_letter
    # A member naming no letter links a lettered title only by a qualifier
    # the title states ("Class B Common Stock (nonvoting)").
    return bool(qualifiers)


def _date_picks(rows: list[dict[str, Any]], titles: Sequence[str]) -> dict[date, float | None]:
    """Per `as_of_date`, the one value rule 7 reads, or `None` when it is
    ambiguous. A class row wins over an undimensioned total; among several
    class rows the ones whose member links one of `titles`
    (`member_links_title`) win; the rows left collapse when their values are
    equal and are ambiguous when they differ."""
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["as_of_date"]].append(row)
    picks: dict[date, float | None] = {}
    for as_of in sorted(by_date):
        classed = [r for r in by_date[as_of] if r["class_member"]]
        chosen = classed or by_date[as_of]
        if len(chosen) > 1:
            linked = [
                r for r in chosen if any(member_links_title(r["class_member"], t) for t in titles)
            ]
            chosen = linked or chosen
        values = {r["value"] for r in chosen}
        picks[as_of] = chosen[0]["value"] if len(values) == 1 else None
    return picks


def _class_titles(
    conn: duckdb.DuckDBPyConnection, t: datetime, ids: Sequence[str]
) -> dict[str, list[str]]:
    """Per security, the class titles known at `t`: its live `listings`
    rows' `class_title`s (`listings_as_of`, retractions applied) and the slug
    of a class id (`<cik>:<slug>`, hyphens read as spaces) whose `securities`
    row is live at `t`. No later cover page or listing names a class at `t`."""
    out: dict[str, list[str]] = defaultdict(list)
    for row in listings_as_of(conn, t, ids).iter_rows(named=True):
        if row["class_title"]:
            out[row["security_id"]].append(row["class_title"])
    for row in securities_as_of(conn, t, ids).iter_rows(named=True):
        sid = row["security_id"]
        if ":" in sid:
            out[sid].append(sid.split(":", 1)[1].replace("-", " "))
    return out


def _first_bar_sessions(
    conn: duckdb.DuckDBPyConnection, t: datetime, ids: Sequence[str]
) -> dict[str, date]:
    """Per security, its first stored bar session known at `t` (#1682, T190b):
    the earliest `session` whose latest revision as of `t` has positive
    volume, the same rows `prices_as_of(..., traded_only=True)` keeps. A
    security with no traded bar known at `t` has no entry. A bar known only
    after `t` is not read."""
    if not ids:
        return {}
    placeholders = ", ".join("?" * len(ids))
    rows = conn.execute(
        "SELECT security_id, min(session) FROM ("
        "  SELECT security_id, session, volume, row_number() OVER ("
        "    PARTITION BY security_id, session ORDER BY known_at DESC"
        "  ) AS _rn FROM prices_daily WHERE known_at <= ?"
        f"    AND security_id IN ({placeholders})"
        ") WHERE _rn = 1 AND volume > 0 GROUP BY security_id",
        [t, *ids],
    ).fetchall()
    return {str(sid): session for sid, session in rows}


def shares_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    ids: Sequence[str] | None,
    settings: Settings,
) -> SharesPick:
    """Rule 7's shares selection at `t` with the plausibility check (#845).

    Per security (every one when `ids` is `None`), its `shares_outstanding`
    facts known at `t` are walked in `as_of_date` order. The first is
    accepted, unless it is zero, negative, NaN or infinite: such a value is
    always rejected and never a baseline. Each later one is compared with the
    last accepted fact moved by every split known at `t` with
    `accepted_as_of < ex_date <= as_of_date`: a ratio above
    `universe.max_shares_ratio` or below its inverse is out of line and
    rejected, unless `universe.accepted_shares_facts` names it.

    Re-anchor (#853): the rejected facts since the last accepted one form
    runs, each fact in line (the same ratio test, split-moved) with the one
    before it; an out-of-line one starts a new run and an accepted fact ends
    it. A rejected fact dated more than `universe.max_shares_age_days` after
    its run's first fact is accepted as the new baseline and flagged
    `reanchored`. A date with two class rows, and a value that is not a share
    count, neither extend nor break a run.

    Class rows (#1682, T190): on one `as_of_date`, the class rows whose
    member links the security's class titles known at `t` decide
    (`_date_picks`, `member_links_title`); equal values collapse; a date
    still holding two different values is ambiguous and skipped by the walk.

    Explained jump (T190): an out-of-line fact that no owner entry accepts is
    accepted as the new baseline, flagged `explained`, when a `spinoff`
    action known at `t` whose child is this security has its `ex_date` after
    the baseline's `as_of_date` and on or before the fact's own (a child's
    count moves from its pre-distribution seed to the distributed count). A
    spin-off known after `t` explains nothing at `t`. A security row is not
    read as an explanation: the master dates a class by its first cover page,
    not by a reorganisation, so it would explain mis-scaled facts (#1703).

    Pre-trading seed (T190b): a fact dated before the security's first stored
    bar session known at `t` (`_first_bar_sessions`) is no baseline, and is
    skipped, when the first fact on or after that session is more than
    `universe.max_shares_ratio` times it (split-moved); every such seed is
    skipped and that first post-bar fact anchors. A pre-bar fact the first
    post-bar fact keeps in line stays the baseline, and a security with no
    traded bar known at `t` is untouched -- so an issuer's one to several tiny
    pre-trading registration counts no longer out-rank every later true fact,
    without moving a normal pre-listing fact at a historical `t`.

    The latest date decides: ambiguous, its own value when accepted, else
    the last accepted fact. Only facts, splits, spin-offs, listings and
    securities known at `t` are read, never a later filing.
    """
    cfg = settings.universe
    accepted_list = {parse_accepted_shares_fact(e) for e in cfg.accepted_shares_facts}
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in facts_as_of(conn, t, ids).iter_rows(named=True):
        if row["fact_name"] == SHARES_FACT:
            rows[row["security_id"]].append(row)
    splits: dict[str, list[tuple[date, float]]] = defaultdict(list)
    spinoffs: dict[str, list[date]] = defaultdict(list)
    split_ids = None if ids is None else sorted(rows)
    for action in live_actions_as_of(conn, t, split_ids).iter_rows(named=True):
        if action["action_type"] == "split":
            splits[action["security_id"]].append((action["ex_date"], action["ratio_or_amount"]))
        elif action["action_type"] == "spinoff":
            spinoffs[action["security_id"]].append(action["ex_date"])
    titles = _class_titles(conn, t, sorted(rows))
    first_bars = _first_bar_sessions(conn, t, sorted(rows))

    def explained(sid: str, after: date, through: date) -> bool:
        """A spin-off known at `t` with `sid` as its child, its `ex_date` in
        `(after, through]`, explains a jump."""
        return any(after < ex_date <= through for ex_date in spinoffs.get(sid, []))

    shares: dict[str, tuple[date, float]] = {}
    ambiguous: set[str] = set()
    outliers: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []

    def in_line(sid: str, earlier: tuple[date, float], as_of: date, value: float) -> float | None:
        """The ratio of `value` over `earlier` moved by the splits known at
        `t` between the two dates, or `None` when it is within the limit."""
        moved = earlier[1]
        for ex_date, ratio in splits.get(sid, []):
            if earlier[0] < ex_date <= as_of:
                moved *= ratio
        change = value / moved if moved > 0 else float("inf")
        return None if 1 / cfg.max_shares_ratio <= change <= cfg.max_shares_ratio else change

    def seed_dates(sid: str, picks: dict[date, float | None], first_bar: date | None) -> set[date]:
        """The dates of the pre-trading seeds to skip (T190b): every share
        count dated before the security's first stored bar session known at
        `t` that the first count on or after that session exceeds by more than
        `universe.max_shares_ratio` (split-moved). Empty otherwise, so every
        other shape keeps today's behaviour. An issuer has one to several
        pre-bar seeds (LIN, PSKY, VTRS), so all of them are skipped, not just
        the first, and the first post-bar fact anchors."""
        if first_bar is None:
            return set()
        counts = [
            (as_of, value)
            for as_of, value in picks.items()
            if value is not None and value > 0 and math.isfinite(value)
        ]
        nxt = next(((as_of, value) for as_of, value in counts if as_of >= first_bar), None)
        if nxt is None:
            return set()
        seeds = set()
        for as_of, value in counts:
            if as_of >= first_bar:
                continue
            change = in_line(sid, (as_of, value), nxt[0], nxt[1])
            if change is not None and change > cfg.max_shares_ratio:
                seeds.add(as_of)
        return seeds

    for sid in sorted(rows):
        picks = _date_picks(rows[sid], titles.get(sid, []))
        seeds = seed_dates(sid, picks, first_bars.get(sid))
        base: tuple[date, float] | None = None
        run: list[tuple[date, float]] = []  # rejected facts in line with each other
        latest_ok = False
        for as_of, value in picks.items():
            latest_ok = False
            if value is None:
                continue
            if as_of in seeds:
                # A pre-trading seed: skipped as a baseline (T190b); the first
                # fact on or after the first bar session anchors.
                continue
            if not (value > 0 and math.isfinite(value)):  # zero, negative, NaN, inf
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
                        "reanchored": False,
                        "explained": False,
                    }
                )
                continue
            if base is None:
                base, latest_ok = (as_of, value), True
                continue
            change = in_line(sid, base, as_of, value)
            owner_ok = (sid, as_of) in accepted_list
            reanchored = False
            explains = change is not None and not owner_ok and explained(sid, base[0], as_of)
            if change is not None and not owner_ok and not explains:
                if not run or in_line(sid, run[-1], as_of, value) is not None:
                    run = []
                run.append((as_of, value))
                reanchored = (as_of - run[0][0]).days > cfg.max_shares_age_days
            if change is not None:
                outliers.append(
                    {
                        "security_id": sid,
                        "as_of_date": as_of,
                        "value": value,
                        "baseline_as_of": base[0],
                        "baseline_value": base[1],
                        "ratio": change,
                        "accepted": owner_ok,
                        "reanchored": reanchored,
                        "explained": explains,
                    }
                )
            if change is None or owner_ok or reanchored or explains:
                base, latest_ok, run = (as_of, value), True, []
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


def _assets_snapshot_known(conn: duckdb.DuckDBPyConnection, t: datetime) -> bool:
    """True when an assets snapshot row with `known_at <= t` exists. A store
    without the `borrow_status` table (below schema version 24) has none."""
    tables = {
        name
        for (name,) in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
            " AND table_catalog = current_database()"
        ).fetchall()
    }
    if "borrow_status" not in tables:
        return False
    row = conn.execute("SELECT count(*) FROM borrow_status WHERE known_at <= ?", [t]).fetchone()
    return row is not None and row[0] > 0


def _tradable_symbols(conn: duckdb.DuckDBPyConnection, t: datetime) -> set[str]:
    """The Alpaca symbols of the assets active and tradable at `t`."""
    from tradepartner.adapters.alpaca_prices import alpaca_symbol  # cycle: it imports this module

    frame = borrow_status_as_of(conn, t)
    keys = {
        alpaca_symbol(symbol)
        for symbol, status, tradable in frame.select("symbol", "status", "tradable").iter_rows()
        if tradable and status == _ASSET_ACTIVE
    }
    return {key for key in keys if key is not None}


def _dollar_volume_universe(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    cfg: UniverseConfig,
    securities: dict[str, dict[str, Any]],
    listings: dict[str, dict[str, Any]],
    alive: list[str],
    close: dict[str, float],
    bars: dict[str, dict[date, tuple[float, int]]],
    history: Sequence[date],
    exclusions: list[dict[str, Any]],
    settings: Settings,
) -> Universe:
    """Rules 7 and 8 under `rank_by = dollar_volume` (module docstring): `alive`
    passed rules 1-6, so every class has a bar on each `history` session."""
    from tradepartner.adapters.alpaca_prices import master_symbol  # cycle: it imports this module

    size_rule = RULES.index("size") + 1
    window = frozenset(history)
    median = {
        sid: statistics.median(c * v for s, (c, v) in bars[sid].items() if s in window)
        for sid in alive
    }
    company_dv: dict[str, float] = defaultdict(float)
    for sid in alive:
        company_dv[securities[sid]["cik"]] += median[sid]
    ranked = sorted(company_dv, key=lambda cik: (-company_dv[cik], cik))
    rank = {cik: position + 1 for position, cik in enumerate(ranked)}
    kept = set(ranked[: cfg.top_n_by_cap])

    def exclude(sid: str, reason: str) -> None:
        exclusions.append(
            {"security_id": sid, "rule": size_rule, "rule_name": "size", "reason": reason}
        )

    for sid in alive:
        if securities[sid]["cik"] not in kept:
            exclude(sid, "top_n_by_cap")
    admitted = [sid for sid in alive if securities[sid]["cik"] in kept]
    skipped = not _assets_snapshot_known(conn, t)
    checked = len(admitted)
    if not skipped:
        tradable = _tradable_symbols(conn, t)
        for sid in admitted:
            if master_symbol(listings[sid], settings.alpaca.class_symbols) not in tradable:
                exclude(sid, "untradable_asset")
        admitted = [
            sid
            for sid in admitted
            if master_symbol(listings[sid], settings.alpaca.class_symbols) in tradable
        ]
    members = [
        {
            "security_id": sid,
            "cik": securities[sid]["cik"],
            "ticker": listings[sid]["ticker"],
            "exchange": listings[sid]["exchange"],
            "close": close[sid],
            "shares": None,
            "market_cap": None,
            "company_cap": None,
            "company_rank": rank[securities[sid]["cik"]],
            "company_dollar_volume": company_dv[securities[sid]["cik"]],
        }
        for sid in admitted
    ]
    return Universe(
        t=t,
        session=session,
        members=pl.DataFrame(members, schema=_DOLLAR_VOLUME_MEMBER_SCHEMA).sort(
            "company_rank", "security_id"
        ),
        exclusions=pl.DataFrame(exclusions, schema=_EXCLUSION_SCHEMA).sort("security_id"),
        rules_enabled={
            rule: (rule != "liquidity" or cfg.liquidity_rule_enabled) and rule != "shares"
            for rule in RULES
        },
        settings={
            "universe": cfg.model_dump(mode="json"),
            "fill_price": settings.execution.fill_price,
        },
        n_assets_intersected=0 if skipped else checked,
        assets_intersection_skipped=skipped,
    )


def universe_as_of(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings | None = None
) -> Universe:
    """The universe at `t` from rows known at `t` (see the module docstring
    for the rules). `settings` defaults to `get_settings()` and is the only
    source of every `universe.*` value, also passed to `listing_ends_as_of`.
    A bare date raises `TypeError`, a naive datetime `ValueError`; rule 6
    raises `ValueError` when a jump candidate carries an invalid corporate
    action (`price_jumps_as_of`)."""
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

    # Rules 4-6 look at the session, the liquidity window and the history window
    # only, so the bar read starts at the earliest of them: its cost stays flat
    # however much history the store holds before `t` (#1305).
    window = (
        _last_sessions(session, cfg.liquidity_window) if cfg.liquidity_rule_enabled else frozenset()
    )
    history = sessions_in_month_window(session, cfg.min_history_months)
    first = min(history[0], min(window, default=session))
    bars: dict[str, dict[date, tuple[float, int]]] = defaultdict(dict)
    frame = prices_as_of(conn, t, alive, traded_only=True, sessions_from=first)
    for sid, day, close_, volume in frame.select(
        "security_id", "session", "close", "volume"
    ).iter_rows():
        if day <= session:
            bars[sid][day] = (close_, volume)
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

        def illiquid(sid: str) -> str:
            dollars = [c * v for s, (c, v) in bars[sid].items() if s in window]
            ok = dollars and statistics.median(dollars) >= cfg.min_median_dollar_volume
            return "" if ok else "min_median_dollar_volume"

        apply("liquidity", {sid: illiquid(sid) for sid in alive})

    apply(
        "history",
        {sid: "" if all(s in bars[sid] for s in history) else "missing_bars" for sid in alive},
    )
    jumps = price_jumps_as_of(conn, t, alive, settings=settings).filter(
        ~pl.col("accepted") & pl.col("session").is_in(list(history))
    )
    apply("history", dict.fromkeys(jumps["security_id"].to_list(), "price_jump"))

    if cfg.rank_by == "dollar_volume":
        return _dollar_volume_universe(
            conn,
            t,
            session,
            cfg,
            securities,
            listings,
            alive,
            close,
            bars,
            history,
            exclusions,
            settings,
        )

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

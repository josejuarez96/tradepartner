"""The survivorship gap as of T (ADR 0003 rule 5; spec "Survivorship gap"; plan T15).

`survivorship_gap(conn, t, settings)` measures, from rows known at `t`
only, how much of the listed universe the store is missing prices for. It
is a **lower bound** (ADR 0003): a name the master never learned of is
not counted.

- **W** is the sessions in `(previous_rebalance, session]`, where
  `session` is the last completed session at `t` and `previous_rebalance`
  defaults to the last session of the calendar month before `session`'s
  (the ADR 0006 monthly cadence; a mid-month T on the health page still
  looks back to the last month-end).
- **L** (`listed`): securities whose classification at `t` is in
  `universe.security_types`, not a benchmark, with a listing known at `t`
  on one of `universe.exchanges` active at any session in W (an OTC or
  other off-universe listing is not the population the gap bounds). A
  listing is active from its `valid_from`; a delisted one through its
  Form 25's filing session, a transferred one through its end session, a
  listed one open-ended.
- **Stale listings** (ADR 0003 amendment #1199): a security whose current
  (latest) listing is still live at `session` but whose last traded bar
  known at `t` is before W's first session and more than
  `gap.stale_listing_sessions` sessions before `session` counts, for the
  gap only, as ended at that bar: it is not in L on that listing's account
  and never in M. Only another of its listings that ended inside W (a
  Form 25 filed in W, a transfer) keeps it in L, judged on that listing.
  A listing with no traded bar at all is never stale. The securities
  removed are reported in `stale_listings` with their last bar and the
  sessions dark, in no other side category.
- **M** (`missing`): names in L whose current listing is still live at
  `session` with no bar at `session` (`no_bar_at_t`), plus names in L
  delisted (not transferred) by a Form 25 filed inside W whose last bar is
  more than `gap.missing_tail_sessions` sessions before the last session
  before the filing session, or who have no bar at all
  (`truncated_tail`). Only the latest revision of a bar known at `t`
  with positive volume counts; halt markers are not trades. A clean
  merger (last traded bar before the filing) is not missing.
- **Count share** = |M| / |L|. **Size share** = sum of value over M /
  sum over L, value = the latest `shares_outstanding` fact known at `t`
  (universe rule 7's selection, so an out-of-line fact falls back to the
  last accepted one, #845) x the last traded raw close known at `t`, the
  shares moved to the close's session by every split known at `t` between
  the two dates. A name with no close or no single shares value is worth
  zero and still counted. Both shares are 0.0 when L is empty.
- **Side categories**, reported separately and never in M: the stale
  listings above, and universe rule 1's `unclassified`/`unclassifiable`,
  rule 6 (`truncated_history`) and rule 7 (`stale_shares`, any rule 7
  reason) exclusions from `universe_as_of(conn, t, settings)`, limited to
  names with a non-benchmark listing on `universe.exchanges` active in W
  (so filers never listed, delisted long before, or stale, are not
  counted) and less the names already in M (a listed name with no bar at
  T also fails rule 6), so each name is counted once.

Every threshold comes from `settings`; this module holds no numeric
literal but 0, 1 and -1 (spec; tested by AST in T14).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import (
    all_sessions,
    is_session,
    last_completed_session,
    last_session_of_month,
    next_session,
    previous_session,
)
from tradepartner.config import Settings, get_settings
from tradepartner.store.asof import _security_filter, _validate_t
from tradepartner.store.classify import UNCLASSIFIABLE, classifications_as_of
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    TRANSFERRED,
    _filing_session,
    listing_ends_as_of,
)
from tradepartner.store.master import securities_as_of
from tradepartner.universe import (
    MISSING_DATA_REASONS,
    _split_factors,
    latest_shares_as_of,
    universe_as_of,
)

NO_BAR_AT_T = "no_bar_at_t"
TRUNCATED_TAIL = "truncated_tail"

_MISSING_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "reason": pl.Utf8,
    "last_bar": pl.Date,
    "reference_session": pl.Date,
    "tail_sessions": pl.Int64,
    "value": pl.Float64,
}


_STALE_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "last_bar": pl.Date,
    "dark_sessions": pl.Int64,
}


@dataclass(frozen=True)
class SurvivorshipGap:
    """`survivorship_gap`'s result (see the module docstring).

    `listed`: L's ids, sorted. `missing`: one row per name in M with its
    `reason`, `last_bar` (null if none), the `reference_session` and
    `tail_sessions` for a `truncated_tail`, and its `value`. `listed_value`
    is the size share's denominator. `stale_listings`: one row per stale
    security (`security_id`, `last_bar`, `dark_sessions`), in neither L nor
    M nor another side category. `settings`: the `gap` and `universe`
    config the result was built with.
    """

    t: datetime
    session: date
    previous_rebalance: date
    listed: tuple[str, ...]
    missing: pl.DataFrame
    count_share: float
    size_share: float
    listed_value: float
    unclassifiable: tuple[str, ...]
    truncated_history: tuple[str, ...]
    stale_shares: tuple[str, ...]
    stale_listings: pl.DataFrame
    settings: dict[str, Any]


def _default_previous_rebalance(session: date) -> date:
    before = session.replace(day=1) - timedelta(days=1)
    return last_session_of_month(before.year, before.month)


def _active_end(listing: dict[str, Any]) -> date | None:
    """The last session `listing` is active, or `None` while listed."""
    if listing["status"] == DELISTED:
        return _filing_session(listing["delisting_filed_at"])
    if listing["status"] == TRANSFERRED:
        end: date | None = listing["end_session"]
        return end
    return None


def _active_in(listing: dict[str, Any], low: date) -> bool:
    """True when `listing` is active at some session from `low` on (W's first)."""
    end = _active_end(listing)
    return end is None or end >= low


def _ended_in(listing: dict[str, Any], low: date) -> bool:
    """True when `listing` has an end and it is on or after `low`."""
    end = _active_end(listing)
    return end is not None and end >= low


def _live(listing: dict[str, Any], session: date) -> bool:
    """True when `listing` is still live at `session`: listed, or transferred
    with its end session on or after `session`."""
    status, end = listing["status"], listing["end_session"]
    return bool(status == LISTED or (status == TRANSFERRED and end is not None and end >= session))


def _last_bars(
    conn: duckdb.DuckDBPyConnection, t: datetime, session: date, ids: list[str]
) -> dict[str, tuple[date, float]]:
    """Per security, `(session, close)` of its latest traded bar on or before
    `session` known at `t` (the latest revision of each bar must have volume)."""
    params: list[Any] = [t, session]
    security_filter = _security_filter(ids, params)
    sql = f"""
        WITH latest AS (
            SELECT security_id, session, close, volume, ROW_NUMBER() OVER (
                PARTITION BY security_id, session ORDER BY known_at DESC
            ) AS _revision
            FROM prices_daily
            WHERE known_at <= ? AND session <= ?
            {security_filter}
        ), traded AS (
            SELECT security_id, session, close, ROW_NUMBER() OVER (
                PARTITION BY security_id ORDER BY session DESC
            ) AS _rn
            FROM latest
            WHERE _revision = 1 AND volume > 0
        )
        SELECT security_id, session, close FROM traded WHERE _rn = 1
    """
    return {sid: (day, close) for sid, day, close in conn.execute(sql, params).fetchall()}


def _sessions_after(last_bar: date, reference: date) -> int:
    """The number of sessions `s` with `last_bar < s <= reference`."""
    sessions = all_sessions()
    return bisect_right(sessions, reference) - bisect_right(sessions, last_bar)


def stale_dark_sessions(last_bar: date | None, session: date, settings: Settings) -> int | None:
    """The stale-listing test (ADR 0003 amendment #1199): the sessions after
    `last_bar` up to `session` when they are more than
    `gap.stale_listing_sessions`, else None. With no traded bar (`None`) a
    listing is never stale. Which listings it applies to is the caller's
    rule (the gap adds "last bar before W"; the ingest, #1234, none)."""
    if last_bar is None:
        return None
    dark = _sessions_after(last_bar, session)
    return dark if dark > settings.gap.stale_listing_sessions else None


def stale_listings_at(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    session: date,
    ids: Iterable[str],
    settings: Settings,
) -> dict[str, tuple[date, int]]:
    """Of `ids`, those whose latest traded bar on or before `session` known
    at `t` (as `survivorship_gap` reads bars) is stale by
    `stale_dark_sessions`: id -> (last bar, sessions dark). An id with no
    traded bar is never in it. The ingest's missing-share check (#1234)
    reads it. A bare date `t` raises `TypeError`, a naive one `ValueError`."""
    t = _validate_t(t)
    out: dict[str, tuple[date, int]] = {}
    for sid, (last_bar, _) in _last_bars(conn, t, session, sorted(set(ids))).items():
        dark = stale_dark_sessions(last_bar, session, settings)
        if dark is not None:
            out[sid] = (last_bar, dark)
    return out


def survivorship_gap(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings | None = None,
    *,
    previous_rebalance: date | None = None,
) -> SurvivorshipGap:
    """The survivorship gap at `t` from rows known at `t` (see the module
    docstring). `settings` defaults to `get_settings()` and is the only
    source of every `gap.*` and `universe.*` value, also passed to
    `listing_ends_as_of` and `universe_as_of`. `previous_rebalance`, when
    given, must be a session before `t`'s. A bare date raises
    `TypeError`, a naive datetime `ValueError`."""
    t = _validate_t(t)
    settings = settings if settings is not None else get_settings()
    session = last_completed_session(t)
    if previous_rebalance is None:
        previous_rebalance = _default_previous_rebalance(session)
    elif not is_session(previous_rebalance) or previous_rebalance >= session:
        raise ValueError(
            f"previous_rebalance must be a session before {session}, got {previous_rebalance}"
        )
    low = next_session(previous_rebalance)

    securities = securities_as_of(conn, t).iter_rows(named=True)
    candidates = {r["security_id"] for r in securities if not r["benchmark"]}
    types = settings.universe.security_types
    kinds = {
        r["security_id"]: r["security_type"]
        for r in classifications_as_of(conn, t).iter_rows(named=True)
    }
    common = {sid for sid, kind in kinds.items() if kind in types}
    # The names L or the `unclassifiable` side category can count: the stale rule
    # applies to all of them, so a stale name is in no side category either.
    counted = {
        sid
        for sid in candidates
        if sid in common or kinds.get(sid, UNCLASSIFIABLE) == UNCLASSIFIABLE
    }
    exchanges = settings.universe.exchanges
    by_security: dict[str, list[dict[str, Any]]] = {}
    for listing in listing_ends_as_of(conn, t, settings).iter_rows(named=True):
        sid = listing["security_id"]
        if sid not in candidates or listing["exchange"] not in exchanges:
            continue
        if listing["valid_from"] > session:
            continue
        by_security.setdefault(sid, []).append(listing)

    bars = _last_bars(conn, t, session, sorted(by_security.keys() & counted))
    active: set[str] = set()
    current: dict[str, dict[str, Any]] = {}
    stale: list[dict[str, Any]] = []
    for sid, listings in by_security.items():
        latest = max(listings, key=lambda listing: listing["valid_from"])
        last_bar = bars[sid][0] if sid in bars else None  # only `counted` ids have bars
        if last_bar is not None and last_bar < low and _live(latest, session):
            dark = stale_dark_sessions(last_bar, session, settings)
            if dark is not None:
                # Stale (ADR 0003 amendment #1199): ended at its last bar for
                # the gap. An older open-ended row is the same live line under
                # an earlier cover page, so only a listing that ended inside W
                # (a Form 25 filed in W, a transfer) keeps the security in L.
                listings = [listing for listing in listings if _ended_in(listing, low)]
                if not listings:
                    stale.append({"security_id": sid, "last_bar": last_bar, "dark_sessions": dark})
                    continue
        if any(_active_in(listing, low) for listing in listings):
            active.add(sid)
            current[sid] = max(listings, key=lambda listing: listing["valid_from"])
    ids = sorted(active & common)

    shares, _ = latest_shares_as_of(conn, t, ids, settings)
    splits = _split_factors(conn, t, ids, session)

    def worth(sid: str) -> float:
        if sid not in bars or sid not in shares:
            return 0.0
        close_session, close = bars[sid]
        as_of, count = shares[sid]
        for ex_date, ratio in splits.get(sid, []):
            if as_of < ex_date <= close_session:
                count *= ratio
            elif close_session < ex_date <= as_of:
                count /= ratio
        return count * close

    values = {sid: worth(sid) for sid in ids}
    tail = settings.gap.missing_tail_sessions
    missing: list[dict[str, Any]] = []
    for sid in ids:
        listing = current[sid]
        last_bar = bars[sid][0] if sid in bars else None
        row: dict[str, Any] = {
            "security_id": sid,
            "last_bar": last_bar,
            "reference_session": None,
            "tail_sessions": None,
            "value": values[sid],
        }
        status = listing["status"]
        if _live(listing, session) and last_bar != session:
            missing.append({**row, "reason": NO_BAR_AT_T})
            continue
        if status != DELISTED:
            continue
        filed = _filing_session(listing["delisting_filed_at"])
        if filed >= low:
            reference = previous_session(filed)
            gap = None if last_bar is None else _sessions_after(last_bar, reference)
            if gap is None or gap > tail:
                row |= {"reference_session": reference, "tail_sessions": gap}
                missing.append({**row, "reason": TRUNCATED_TAIL})

    listed_value = sum(values.values())
    missing_value = sum(r["value"] for r in missing)
    exclusions = universe_as_of(conn, t, settings).exclusions.iter_rows(named=True)
    side: dict[str, list[str]] = {"unclassifiable": [], "history": [], "shares": []}
    in_m = {r["security_id"] for r in missing}
    for r in exclusions:
        if r["security_id"] in in_m or r["security_id"] not in active:
            continue
        if r["rule_name"] == "security_type" and r["reason"] in MISSING_DATA_REASONS:
            side["unclassifiable"].append(r["security_id"])
        elif r["rule_name"] in side and r["reason"] in MISSING_DATA_REASONS:
            side[r["rule_name"]].append(r["security_id"])

    return SurvivorshipGap(
        t=t,
        session=session,
        previous_rebalance=previous_rebalance,
        listed=tuple(ids),
        missing=pl.DataFrame(missing, schema=_MISSING_SCHEMA).sort("security_id"),
        count_share=len(missing) / len(ids) if ids else 0.0,
        size_share=missing_value / listed_value if listed_value else 0.0,
        listed_value=listed_value,
        unclassifiable=tuple(sorted(side["unclassifiable"])),
        truncated_history=tuple(sorted(side["history"])),
        stale_shares=tuple(sorted(side["shares"])),
        stale_listings=pl.DataFrame(stale, schema=_STALE_SCHEMA).sort("security_id"),
        settings={
            "gap": settings.gap.model_dump(mode="json"),
            "universe": settings.universe.model_dump(mode="json"),
        },
    )

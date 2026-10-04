"""Delistings and transfers, derived at read time (spec req 4, plan T8b).

`build_delistings` is pure (filings and a `MasterBuild` in, rows out);
`write_delistings` inserts them. `delistings_as_of` reads them back, and
`listing_ends_as_of` derives each listing's status and end at T.

**Rows.** Forms 25 and 25-NSE are both stored as `delistings` rows,
`known_at` = `filed_at` = the acceptance, `provenance = filing`, keeping
the filing's class title and exchange. `effective_on` is the filing's
stated date, else the acceptance's New York date plus 10 days (Rule
12d2-2); it never sets an end, and only the re-tag rule below reads it.

**One class per filing.** A filing names a class, not a company, so it is
resolved to the `security_id` of its CIK whose listing on the filing's
exchange has the same title up to the first comma (master's
`_norm_title`). Failing that, a filing whose title is plain common equity
(master's `_is_common`, and no warrant, right, unit, preferred or
depositary word) takes the one class on that exchange whose
listings are all common-titled or untitled (snapshot rows), so "Common
Stock" still finds a class whose cover page says "Common Shares". Anything
else, including a warrant or preferred title that matches no class, or
two common classes, is returned in `unmatched`, never guessed: guessing
would end the common listing on a warrant or preferred filing.

Resolution is a key mapping and may use master rows known after the
filing (the master is built from full history). That never makes anything
visible early: the row's `known_at` stays the acceptance, and at read time
a filing ends only a listing already known at T.

**Nothing derived is stored.** At T, from rows with `known_at <= T` only:

- A delisting ends the latest listing of its security on its exchange with
  `valid_from` on or before the filing session (so after a ticker change
  only the current ticker ends). The earliest such filing wins.
- **Re-tagged exchange** (#818). If a later listing of that security with
  the same ticker carries another exchange tag (the filer's own cover page
  flipping NYSE_AMERICAN to NYSE) and started before the transfer window,
  the filing ends the latest such listing instead: where the tags
  disagree, the Form 25's exchange is the fact. Never re-tagged: a
  listing that starts inside the window (a transfer's destination,
  below); a tag that names no exchange (`NONE`, `OTC`, empty: a move off
  the exchange); a security with a bar known at T after the filing's
  `effective_on` (it still trades, so the filing withdrew one line of a
  dual listing; until such a bar is known the conservative re-tag
  holds); and a filing whose own-exchange listing an earlier filing
  already ended (a 25/A, or the issuer's Form 25 after the exchange's,
  never moves an end).
- **Other class on an untitled listing** (#818). A filing whose title is
  not plain common (a class or series letter, Special, (Old), T-DECS, or
  any title `_is_plain_title` refuses) never ends an untitled
  (`snapshot_static`) listing: that listing cannot show it is the class
  the filing names.
- It is a **transfer** at T if another listing of the security on another
  exchange is known at T with `valid_from` within
  `master.transfer_window_sessions` sessions of the filing session. The
  old listing's end is then the session before the new `valid_from`.
- Otherwise it is **delisted** at T (conservative: the state between the
  filing and a new listing becoming known). Its end is the last bar
  session known at T from its `valid_from` up to, not including, any later
  listing of the same security; with no such bar, `end_session` is null.

The filing session is the acceptance's New York date if that is a
session, else the next one (the same rule as a listing's `valid_from`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from typing import Any

import duckdb
import polars as pl

from tradepartner.adapters.filings import DelistingFiling
from tradepartner.calendar import is_session, next_session, previous_session
from tradepartner.config import Settings
from tradepartner.store.asof import (
    _EXCHANGE_TZ,
    _latest_as_of,
    _security_filter,
    _validate_t,
    listings_as_of,
)
from tradepartner.store.db import insert_row
from tradepartner.store.master import MasterBuild, _is_common, _norm_title
from tradepartner.timeutil import ensure_tz_aware_utc

# Amendments too (owner decision 2026-09-26, #262): EDGAR's history has them,
# and `derive_listing_ends`' earliest-filing rule means an amendment never
# moves a listing's end, while a 25/A whose original is missing still ends it.
DELISTING_FORMS = frozenset({"25", "25-NSE", "25/A", "25-NSE/A"})

#: Days from filing to effect when the filing states none (Rule 12d2-2).
_DEFAULT_EFFECTIVE_DAYS = 10

LISTED = "listed"
DELISTED = "delisted"
TRANSFERRED = "transferred"

_DELISTING_KEY: tuple[str, ...] = ("security_id", "form", "class_title", "exchange", "filed_at")

Row = dict[str, Any]


@dataclass(frozen=True)
class DelistingBuild:
    """`delistings` rows, plus filings no class could be resolved for."""

    delistings: tuple[Row, ...]
    unmatched: tuple[DelistingFiling, ...]


def _filing_day(filed_at: datetime) -> date:
    return filed_at.astimezone(_EXCHANGE_TZ).date()


def _filing_session(filed_at: datetime) -> date:
    day = _filing_day(filed_at)
    return day if is_session(day) else next_session(day)


#: Words that make a title mentioning common stock something else
#: ("Warrants to purchase Common Stock", "Units, each of one share ...").
_NOT_COMMON_WORDS = ("warrant", "right", "unit", "preferred", "depositary", "note", "debenture")


def _is_plain_common(title: str) -> bool:
    norm = _norm_title(title)
    return _is_common(title) and not any(word in norm for word in _NOT_COMMON_WORDS)


#: A class or series letter ("Class B Common Stock", "Series B ...").
_CLASS_DESIGNATION = re.compile(r"\b(?:class|series) [a-z0-9]\b")

#: Words that make a common title name one class of several: "Special
#: Common Shares", "Common Stock (Old)", "T-DECS", "Common Equivalent
#: Securities", "Non-Voting Common Stock", a tracking, restricted,
#: redeemable, exchangeable or when-issued stock.
_QUALIFIER_WORDS = frozenset(
    {
        "special",
        "old",
        "new",
        "tracking",
        "tangible",
        "decs",
        "equivalent",
        "voting",
        "restricted",
        "redeemable",
        "exchangeable",
        "issued",
    }
)


def _is_plain_title(title: str) -> bool:
    """Whether `title` names a company's plain common equity: common, no
    class or series letter, no qualifier word (#818). Stricter than
    `_is_plain_common`: only such a filing may end an untitled listing."""
    norm = _norm_title(title)
    return (
        _is_plain_common(title)
        and _CLASS_DESIGNATION.search(norm) is None
        and _QUALIFIER_WORDS.isdisjoint(norm.split())
    )


#: Exchange tags that name no exchange: a cover page showing one is a move
#: off the exchange, never a re-tag of the listing a Form 25 ends.
_OFF_EXCHANGE = frozenset({"", "NONE", "OTC"})


def _resolve(filing: DelistingFiling, master: MasterBuild) -> str | None:
    """The `security_id` `filing` delists, or `None` if it is not certain."""
    candidates: set[str] = {
        row["security_id"]
        for row in master.securities
        if row["cik"] == filing.cik and not row.get("benchmark", False)
    }
    on_exchange = [
        row
        for row in master.listings
        if row["security_id"] in candidates and row["exchange"] == filing.exchange
    ]
    title = _norm_title(filing.class_title)
    by_title: set[str] = {
        row["security_id"]
        for row in on_exchange
        if row["class_title"] is not None and _norm_title(row["class_title"]) == title
    }
    if len(by_title) == 1:
        return by_title.pop()
    if by_title or not _is_plain_common(filing.class_title):
        return None
    titles: dict[str, list[str | None]] = defaultdict(list)
    for row in on_exchange:
        titles[row["security_id"]].append(row["class_title"])
    common = {
        security_id
        for security_id, seen in titles.items()
        if all(t is None or _is_plain_common(t) for t in seen)
    }
    return common.pop() if len(common) == 1 else None


def build_delistings(
    filings: Iterable[DelistingFiling], master: MasterBuild, *, ingested_at: datetime
) -> DelistingBuild:
    """`delistings` rows for every Form 25 and 25-NSE in `filings` that
    resolves to one class of `master`.

    Raises `ValueError` for any other form, or if a filing's acceptance is
    later than `ingested_at` (spec: `known_at <= ingested_at`).
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    rows: list[Row] = []
    unmatched: list[DelistingFiling] = []
    for filing in sorted(filings, key=lambda f: (f.accepted_at, f.accession)):
        if filing.form not in DELISTING_FORMS:
            raise ValueError(f"not a delisting form: {filing.form!r} ({filing.accession})")
        if filing.accepted_at > ingested_at:
            raise ValueError(
                f"{filing.accession}: accepted_at {filing.accepted_at.isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        security_id = _resolve(filing, master)
        if security_id is None:
            unmatched.append(filing)
            continue
        effective_on = filing.effective_on or _filing_day(filing.accepted_at) + timedelta(
            days=_DEFAULT_EFFECTIVE_DAYS
        )
        rows.append(
            {
                "security_id": security_id,
                "form": filing.form,
                "class_title": filing.class_title,
                "exchange": filing.exchange,
                "filed_at": filing.accepted_at,
                "effective_on": effective_on,
                "known_at": filing.accepted_at,
                "ingested_at": ingested_at,
                "source": "edgar",
                "provenance": "filing",
            }
        )
    return DelistingBuild(delistings=tuple(rows), unmatched=tuple(unmatched))


def write_delistings(conn: duckdb.DuckDBPyConnection, build: DelistingBuild) -> int:
    """Insert every row of `build`; return the number inserted."""
    for row in build.delistings:
        insert_row(conn, "delistings", row)
    return len(build.delistings)


def delistings_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`delistings` rows known by `t`, latest revision per `(security_id,
    form, class_title, exchange, filed_at)`, sorted by that key."""
    return _latest_as_of(conn, "delistings", _DELISTING_KEY, _validate_t(t), security_ids)


@cache
def _window(filing_session: date, sessions: int) -> tuple[date, date]:
    low = high = filing_session
    for _ in range(sessions):
        low = previous_session(low)
        high = next_session(high)
    return low, high


def _ended_listing(
    siblings: list[Row],
    delisting: Row,
    window_sessions: int,
    last_bar: date | None,
    ended: dict[int, Row],
) -> Row | None:
    """The listing among `siblings` (one security's, whose last bar known at
    T is `last_bar`) that `delisting` ends, or `None`. `ended` holds the
    listings earlier filings ended. Module docstring: latest on its
    exchange, a re-tag, an other-class filing on an untitled listing."""
    session = _filing_session(delisting["filed_at"])
    exchange = delisting["exchange"]
    on_exchange = [r for r in siblings if r["exchange"] == exchange and r["valid_from"] <= session]
    if not on_exchange:
        return None
    target = max(on_exchange, key=lambda r: r["valid_from"])
    if target["_index"] in ended:
        return None
    if last_bar is None or last_bar <= delisting["effective_on"]:
        low, _ = _window(session, window_sessions)
        retagged = [
            r
            for r in siblings
            if r["ticker"] == target["ticker"]
            and r["exchange"] != exchange
            and r["exchange"] not in _OFF_EXCHANGE
            and target["valid_from"] < r["valid_from"] < low
        ]
        if retagged:
            target = max(retagged, key=lambda r: r["valid_from"])
    if target["class_title"] is None and not _is_plain_title(delisting["class_title"]):
        return None
    return target


def derive_listing_ends(
    listings: pl.DataFrame,
    delistings: pl.DataFrame,
    bar_sessions: pl.DataFrame,
    *,
    transfer_window_sessions: int,
) -> pl.DataFrame:
    """`listings` with `status`, `end_session`, `delisting_form`,
    `delisting_filed_at` and `effective_on` added (see the module
    docstring for the rules).

    Pure: every input must already be restricted to rows known at the same
    T (`listings_as_of`, `delistings_as_of`, and `(security_id, session)`
    of bars known at T). Row order follows `listings`.
    """
    by_security: dict[str, list[Row]] = defaultdict(list)
    listing_rows = listings.to_dicts()
    for index, row in enumerate(listing_rows):
        by_security[row["security_id"]].append({**row, "_index": index})

    bars: dict[str, list[date]] = defaultdict(list)
    for security_id, session in bar_sessions.select("security_id", "session").iter_rows():
        bars[security_id].append(session)

    ended: dict[int, Row] = {}  # listing index -> the filing that ends it
    for delisting in sorted(delistings.to_dicts(), key=lambda r: (r["filed_at"], r["form"])):
        security_id = delisting["security_id"]
        target = _ended_listing(
            by_security.get(security_id, []),
            delisting,
            transfer_window_sessions,
            max(bars.get(security_id, []), default=None),
            ended,
        )
        if target is not None:
            ended.setdefault(target["_index"], delisting)

    out: list[Row] = []
    for index, row in enumerate(listing_rows):
        filing = ended.get(index)
        if filing is None:
            out.append(
                {
                    **row,
                    "status": LISTED,
                    "end_session": None,
                    "delisting_form": None,
                    "delisting_filed_at": None,
                    "effective_on": None,
                }
            )
            continue
        siblings = by_security[row["security_id"]]
        low, high = _window(_filing_session(filing["filed_at"]), transfer_window_sessions)
        successors = sorted(
            other["valid_from"]
            for other in siblings
            if other["exchange"] != filing["exchange"] and low <= other["valid_from"] <= high
        )
        end: date | None
        if successors:
            status, end = TRANSFERRED, previous_session(successors[0])
        else:
            later = [o["valid_from"] for o in siblings if o["valid_from"] > row["valid_from"]]
            stop = min(later, default=date.max)
            within = [s for s in bars.get(row["security_id"], []) if row["valid_from"] <= s < stop]
            status, end = DELISTED, max(within, default=None)
        out.append(
            {
                **row,
                "status": status,
                "end_session": end,
                "delisting_form": filing["form"],
                "delisting_filed_at": filing["filed_at"],
                "effective_on": filing["effective_on"],
            }
        )

    schema: dict[str, pl.DataType] = {
        **listings.schema,
        "status": pl.Utf8(),
        "end_session": pl.Date(),
        "delisting_form": pl.Utf8(),
        "delisting_filed_at": delistings.schema["filed_at"],
        "effective_on": pl.Date(),
    }
    return pl.DataFrame(out, schema=schema)


def _bar_sessions(
    conn: duckdb.DuckDBPyConnection, t: datetime, security_ids: Sequence[str]
) -> pl.DataFrame:
    params: list[Any] = [t]
    security_filter = _security_filter(security_ids, params)
    sql = f"""
        SELECT DISTINCT security_id, session FROM prices_daily
        WHERE known_at <= ?
        {security_filter}
        ORDER BY security_id, session
    """
    return conn.execute(sql, params).pl()


def listing_ends_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`listings_as_of(t)` with each listing's delisting status and end at
    `t` (`derive_listing_ends`), derived only from rows known at `t`.

    A bare date raises `TypeError`, a naive datetime `ValueError`.
    """
    t = _validate_t(t)
    listings = listings_as_of(conn, t, security_ids)
    delistings = delistings_as_of(conn, t, security_ids)
    delisted_ids = sorted(set(delistings["security_id"].to_list()))
    bar_sessions = _bar_sessions(conn, t, delisted_ids)
    return derive_listing_ends(
        listings,
        delistings,
        bar_sessions,
        transfer_window_sessions=settings.master.transfer_window_sessions,
    )

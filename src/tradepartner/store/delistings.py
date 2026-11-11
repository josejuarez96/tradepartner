"""Delistings and transfers, derived at read time (spec req 4, plan T8b).

`build_delistings` is pure (filings and a `MasterBuild` in, rows out);
`write_delistings` inserts them. `delistings_as_of` reads them back, and
`listing_ends_as_of` derives each listing's status and end at T.

**Rows.** Forms 25 and 25-NSE are both stored as `delistings` rows,
`known_at` = `filed_at` = the acceptance, `provenance = filing`, keeping
the filing's class title and exchange. `effective_on` is the filing's
stated date, else the acceptance's New York date plus 10 days (Rule
12d2-2); it never sets an end, and only the re-tag rule below reads it.

**Classes per filing.** A filing names classes, not a company, so it is
resolved (master's `_form25_classes`, which the #820 relisting rule
shares) among the classes of its CIK (known by the filing's acceptance,
and not yet succeeded by new equity, #820) listed on the filing's
exchange: the one whose listing has the same title up to the first comma
(master's `_norm_title`). Failing that, a filing whose title is plain
common equity (master's `_is_common`, and no warrant, right, unit,
preferred or depositary word) takes the one class on that exchange whose
listings are all common-titled or untitled (snapshot rows), so "Common
Stock" still finds a class whose cover page says "Common Shares".
**Compound titles** (#1163) list several classes: "Common stock and
warrants", a SPAC's "Class A Common Stock; Units, each consisting of one
share of Class A common stock and one-half of one warrant". The parts
between `;`, `,`, "and" or "&" that are plain common and not a
description of another class ("each consisting of one share of ...") are
the common classes it names: one such part resolves as a plain-common
title would; two or more ("Class A Common Stock and Class B Common
Stock") each need an exact title match and give one row per class.
Anything else, including a warrant or preferred title that matches no
class, two common classes for one common name, or a named class that
matches none, is returned in `unmatched`, never guessed: guessing would
end the common listing on a warrant or preferred filing.

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
- **Debt class** (#2937). A filing whose title names a debt class (a note,
  debenture or bond, no common word: Verizon's "2.375% Notes due 2022") never
  ends a common or untitled listing, only a listing of its own class: once the
  notes' listing row is retracted, the filing would otherwise land on the
  common row, the latest on the exchange.
- **Form 25 close** (#1163). A listing that starts in a session after the
  filing session of a Form 25 that ended an earlier listing of the
  security on the same exchange (a cover page re-opened it: an acquired
  company's last 10-K naming the pair, Maxim, Rudolph, Nielsen) is ended
  by the latest such filing, delisted, unless a bar known at T falls
  after that filing's `effective_on`. Trading after the delisting took
  effect is what tells a real relisting (#820: CMPR, CG, WELL) from a
  stale cover page; until such a bar is known the conservative close
  holds, as for the re-tag. A filing that ended no listing closes nothing.
- It is a **transfer** at T if another listing of the security on another
  exchange is known at T with `valid_from` within
  `master.transfer_window_sessions` sessions of the filing session. The
  old listing's end is then the session before the new `valid_from`.
- It is also a **transfer** at T when the security **keeps trading** after
  the filing's `effective_on` (#3242; owner, 2026-11-10): a bar known at T
  within `master.transfer_window_sessions` of `effective_on` when the next
  listing on another exchange started before it (a late Form 25), or one
  within that many sessions before a next listing that starts after it (a
  cover page starts that listing late). Tail bars and an uplisting years
  later are two lines. Only bars in sessions a listing row on an exchange
  covers count (#3252; owner, 2026-11-10): OTC bars leave it a delisting.
  The old listing's end is then the session before that next listing; with
  no next listing known it stays a delisting. Only bars known at T are read, so the same
  filing is a delisting at an earlier T. A security with no bar after
  `effective_on` stays delisted below. A filing also ends the other rows of
  its exchange that start the same day as the listing it ends (one line,
  two cover-page tickers).
- It is a **line withdrawal** at T, also a transfer, when the filing ends one
  line of a security whose other line carries on (#3269: Apache's NYSE and
  NYSE Chicago Forms 25 of June 2020 with Nasdaq concurrent from the same day,
  Marriott's NYSE Chicago Form 25 of September 2020): a sibling row of the
  same security and class (`class_title` equal, or either `None`) on another
  exchange, not `_OFF_EXCHANGE`, started between the line's own start and
  `effective_on` (an older row was replaced by the line) and not
  ended by any Form 25 known at T with an `effective_on` on or before this
  filing's (so the reading does not depend on filing order, and two lines
  ended the same day with no third row are both delistings), and a bar known
  at T within `master.transfer_window_sessions` after `effective_on` in a
  session a listing row on an exchange covers (the evidence #3242 asks for;
  a stale sibling row with no such bar leaves a delisting). The line's end is
  the session before `effective_on` and the security stays listed. A sibling
  starting the same day as the line outranks the transfer-to-a-later-listing
  rules; otherwise they come first and keep their end.
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
from tradepartner.store.master import (
    MasterBuild,
    _form25_classes,
    _is_common,
    _is_plain_common,
    _norm_title,
)
from tradepartner.timeutil import ensure_tz_aware_utc

# Amendments too (owner decision 2026-09-26, #262): EDGAR's history has them,
# and `derive_listing_ends`' earliest-filing rule means an amendment never
# moves a listing's end, while a 25/A whose original is missing still ends it.
# An amendment after a filing already counted for that security and exchange
# is ignored at read time, so it never ends a relisting row opened after the
# original (#820). Limit: a later delisting known only by its /A is missed.
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


#: Words that make a Form 25's class a debt security ("2.375% Notes due 2022").
_DEBT_WORDS = frozenset({"notes", "note", "debentures", "debenture", "bonds", "bond"})


def _is_debt_title(title: str) -> bool:
    """Whether a Form 25's `title` names a debt class: a note, debenture or
    bond word in its head and no common-equity word (#2937)."""
    norm = _norm_title(title)
    return not _is_common(title) and not _DEBT_WORDS.isdisjoint(norm.split())


#: Exchange tags that name no exchange: a cover page showing one is a move
#: off the exchange, never a re-tag of the listing a Form 25 ends.
_OFF_EXCHANGE = frozenset({"", "NONE", "OTC"})


@dataclass(frozen=True)
class _MasterRows:
    """`master`'s securities by CIK and listings by security, each in
    `master` order (listings with their position), grouped once per build
    (#2927) so each filing reads its CIK's rows, not every row."""

    securities_by_cik: dict[str, list[Row]]
    listings_by_id: dict[str, list[tuple[int, Row]]]

    @classmethod
    def of(cls, master: MasterBuild) -> _MasterRows:
        securities: dict[str, list[Row]] = defaultdict(list)
        for row in master.securities:
            securities[row["cik"]].append(row)
        listings: dict[str, list[tuple[int, Row]]] = defaultdict(list)
        for position, row in enumerate(master.listings):
            listings[row["security_id"]].append((position, row))
        return cls(dict(securities), dict(listings))


def _resolve(
    filing: DelistingFiling, master: MasterBuild, rows: _MasterRows | None = None
) -> tuple[str, ...]:
    """The `security_id`s `filing` delists, or `()` if that is not certain."""
    if rows is None:
        rows = _MasterRows.of(master)
    # #820: never a successor known later. An original Form 25 after a
    # succession names the successor; an amendment amends the old class's.
    known = [s for s in master.successions if s.known_at <= filing.accepted_at]
    if filing.form.endswith("/A"):
        excluded = {s.security_id for s in known}
    else:
        excluded = {s.predecessor_id for s in known}
    candidates: set[str] = {
        row["security_id"]
        for row in rows.securities_by_cik.get(filing.cik, ())
        if not row.get("benchmark", False)
        and row["known_at"] <= filing.accepted_at
        and row["security_id"] not in excluded
    }
    # Each candidate's titles on the filing's exchange, keyed in the order a
    # walk of `master.listings` meets them (`_form25_classes` reads key order).
    found: list[tuple[int, str, list[str | None]]] = []
    for security_id in candidates:
        hits = [
            (position, row)
            for position, row in rows.listings_by_id.get(security_id, ())
            if row["exchange"] == filing.exchange
        ]
        if hits:
            found.append((hits[0][0], security_id, [row["class_title"] for _, row in hits]))
    titles = {security_id: named for _, security_id, named in sorted(found)}
    return _form25_classes(filing.class_title, titles)


def build_delistings(
    filings: Iterable[DelistingFiling], master: MasterBuild, *, ingested_at: datetime
) -> DelistingBuild:
    """`delistings` rows for every Form 25 and 25-NSE in `filings` that
    resolves to classes of `master`, one row per class.

    Raises `ValueError` for any other form, or if a filing's acceptance is
    later than `ingested_at` (spec: `known_at <= ingested_at`).
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    rows: list[Row] = []
    unmatched: list[DelistingFiling] = []
    master_rows = _MasterRows.of(master)  # #2927: once per build, not once per filing
    for filing in sorted(filings, key=lambda f: (f.accepted_at, f.accession)):
        if filing.form not in DELISTING_FORMS:
            raise ValueError(f"not a delisting form: {filing.form!r} ({filing.accession})")
        if filing.accepted_at > ingested_at:
            raise ValueError(
                f"{filing.accession}: accepted_at {filing.accepted_at.isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        security_ids = _resolve(filing, master, master_rows)
        if not security_ids:
            unmatched.append(filing)
            continue
        effective_on = filing.effective_on or _filing_day(filing.accepted_at) + timedelta(
            days=_DEFAULT_EFFECTIVE_DAYS
        )
        rows.extend(
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
            for security_id in security_ids
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


def delistings_with_reasons_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Return known Form 25 episodes with their latest reason, or `unknown`."""
    from tradepartner.store.delisting_reasons import delisting_reasons_as_of

    t = _validate_t(t)
    episodes = delistings_as_of(conn, t, security_ids)
    reasons = delisting_reasons_as_of(conn, t, security_ids).select(
        "security_id",
        "delisting_form",
        "delisting_class_title",
        "delisting_exchange",
        "delisting_filed_at",
        "reason",
        "reason_source",
        "reason_evidence_form",
        "reason_evidence_accession",
        "reason_known_at",
    )
    return episodes.join(
        reasons,
        left_on=_DELISTING_KEY,
        right_on=(
            "security_id",
            "delisting_form",
            "delisting_class_title",
            "delisting_exchange",
            "delisting_filed_at",
        ),
        how="left",
    ).with_columns(
        pl.col("reason").fill_null("unknown"),
        pl.col("reason_source").fill_null("unknown"),
    )


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
    if _spares(delisting, target):
        return None
    return target


def _on_exchange(siblings: list[Row], session: date) -> bool:
    """Whether `session` is covered by a listing row on an exchange (#3252).

    The row that covers a session is the latest of the security's rows (known
    at T: the caller passes nothing later) starting on or before it; a row on
    `_OFF_EXCHANGE` (OTC, NONE) covers it off the exchange. A session no row
    covers is not on an exchange either."""
    started = [o for o in siblings if o["valid_from"] <= session]
    if not started:
        return False
    latest = max(o["valid_from"] for o in started)
    return any(o["exchange"] not in _OFF_EXCHANGE for o in started if o["valid_from"] == latest)


def _keeps_trading(
    bars: list[date],
    siblings: list[Row],
    row: Row,
    filing: Row,
    window_sessions: int,
) -> tuple[date] | None:
    """`(end,)` when `filing` ends `row` as a transfer because the security
    keeps trading after the filing's `effective_on`, else `None` (#3242).

    The owner's reading (2026-11-10): a Form 25 for a security that trades on
    is a move to its next listing, never a terminal delisting. Judged on
    `bars`, the security's sessions with a bar known at T only (the caller
    passes nothing later), so the reading is the one a run at T would make.
    The security trades on when a bar falls within `window_sessions` of
    `effective_on` and the next listing started before it (a late Form 25),
    or a bar falls within `window_sessions` before a next listing on another
    exchange that starts after it (it traded into it; a cover page may start
    that listing months late, and a hole in the store can sit between).
    The end is the session before that next listing. A security with no next
    listing known stays a delisting (a reused ticker's bars after a real
    delisting look the same), and so does one with no bar after
    `effective_on` (VLD, stopped for 13 months). Only bars in sessions a
    listing row on an exchange covers count (#3252; owner, 2026-11-10): an OTC
    tail before an uplisting leaves the line a delisting."""
    effective_on = filing["effective_on"]
    after = sorted(s for s in bars if s > effective_on and _on_exchange(siblings, s))
    if not after:
        return None
    first = after[0]
    later = sorted(
        o["valid_from"]
        for o in siblings
        if o["valid_from"] > row["valid_from"]
        and o["exchange"] != filing["exchange"]
        and o["exchange"] not in _OFF_EXCHANGE
    )
    if not later:
        return None  # nothing to move to: bars on a line with no next listing are no proof
    next_start = later[0]
    low = _window(next_start, window_sessions)[0]
    runs_in = any(effective_on < s < next_start and s >= low for s in after)
    # A next listing that starts after the effective day must be run into:
    # a few tail bars and an uplisting years later are two lines, not one.
    # One that started before it (a late Form 25) has no gap to bridge.
    adjacent = next_start <= effective_on and first <= _window(effective_on, window_sessions)[1]
    if not (adjacent or runs_in):
        return None
    return (previous_session(next_start),)


def _withdrawn(
    bars: list[date],
    siblings: list[Row],
    row: Row,
    filing: Row,
    ended: dict[int, Row],
    window_sessions: int,
    *,
    concurrent: bool = False,
) -> date | None:
    """The line's last session when `filing` only withdraws `row`'s line while
    another listing of the security carries on, else `None` (#3269).

    A sibling carries on when it is another row of the same security and
    class (`class_title` equal, or either `None`, as `_spares` reads it) on
    another exchange that is not `_OFF_EXCHANGE`, started on or before
    `effective_on`, and no Form 25 known at T with an `effective_on` on or
    before this filing's ended it (read from `ended`, whole, so the reading
    does not depend on filing order: two lines ended the same day with no
    third row are both delistings). And the security must still trade after
    `effective_on`: a bar known at T falls within `window_sessions` after it,
    in a session a listing row on an exchange covers (`_on_exchange`, #3252).
    Without that bar the sibling is stale, and the Form 25 is the delisting
    it reads as.

    A sibling must not be older than the line (`valid_from` at or after
    `row`'s): an older row on another exchange was replaced by it (a move
    years ago, no Form 25 for the old line), not carried alongside. With
    `concurrent`, only a sibling starting the same day counts (one class filed
    on two exchanges): that pair is one security's two lines, so the withdrawal
    outranks `_keeps_trading`'s reading of a later row as the next listing."""
    effective_on = filing["effective_on"]
    title = row["class_title"]
    live = any(
        other["exchange"] != filing["exchange"]
        and other["valid_from"] >= row["valid_from"]
        and (not concurrent or other["valid_from"] == row["valid_from"])
        and other["exchange"] not in _OFF_EXCHANGE
        and (title is None or other["class_title"] is None or other["class_title"] == title)
        and other["valid_from"] <= effective_on
        and not (
            other["_index"] in ended and ended[other["_index"]]["effective_on"] <= effective_on
        )
        for other in siblings
    )
    if not live:
        return None
    high = _window(effective_on, window_sessions)[1]
    if not any(effective_on < s <= high and _on_exchange(siblings, s) for s in bars):
        return None
    return previous_session(effective_on)


def _spares(delisting: Row, listing: Row) -> bool:
    """Whether `delisting` names a debt class and `listing` is a common or
    untitled one: a notes filing never ends the common listing (#2937), whether
    it lands on it directly or through a later row it would re-open."""
    title = listing["class_title"]
    return _is_debt_title(delisting["class_title"]) and (title is None or _is_common(title))


def _reopened(
    by_security: dict[str, list[Row]], ended: dict[int, Row], bars: dict[str, list[date]]
) -> dict[int, Row]:
    """Listings a later row re-opened after a Form 25 ended an earlier one
    (#1163), each mapped to that Form 25: the latest filing that ended a
    listing of the security on the row's exchange in a session before its
    `valid_from`, when no bar known at T falls after that filing's
    `effective_on`. Module docstring: the Form 25 close."""
    out: dict[int, Row] = {}
    for security_id, siblings in by_security.items():
        closers = [(row, ended[row["_index"]]) for row in siblings if row["_index"] in ended]
        if not closers:
            continue
        last_bar = max(bars.get(security_id, []), default=None)
        for row in siblings:
            if row["_index"] in ended:
                continue
            before = [
                filing
                for closed, filing in closers
                if closed["exchange"] == row["exchange"]
                and _filing_session(filing["filed_at"]) < row["valid_from"]
                and not _spares(filing, row)
            ]
            if not before:
                continue
            filing = max(before, key=lambda f: f["filed_at"])
            if last_bar is None or last_bar <= filing["effective_on"]:
                out[row["_index"]] = filing
    return out


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
    counted: set[tuple[str, str]] = set()  # (security_id, exchange) with a filing
    for delisting in sorted(delistings.to_dicts(), key=lambda r: (r["filed_at"], r["form"])):
        security_id = delisting["security_id"]
        key = (security_id, delisting["exchange"])
        if delisting["form"].endswith("/A") and key in counted:
            # Amends a filing already counted. `_ended_listing`'s already-ended
            # skip misses a late /A after a relisting (#820): its target is
            # then the new row, which no filing ended yet.
            continue
        counted.add(key)
        target = _ended_listing(
            by_security.get(security_id, []),
            delisting,
            transfer_window_sessions,
            max(bars.get(security_id, []), default=None),
            ended,
        )
        if target is not None:
            ended.setdefault(target["_index"], delisting)
            # The filing names a line, not a ticker: rows of the same class
            # on its exchange from the same day are that one line (#3242:
            # AEVA and IPV, two cover-page tickers of one NYSE line).
            for tied in by_security.get(security_id, []):
                if (
                    tied["_index"] != target["_index"]
                    and tied["exchange"] == target["exchange"]
                    and tied["valid_from"] == target["valid_from"]
                    and (tied["class_title"] is None) == (target["class_title"] is None)
                ):
                    ended.setdefault(tied["_index"], delisting)
    reopened = _reopened(by_security, ended, bars)
    ended.update(reopened)

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
        keeps = (
            None
            if index in reopened
            else _keeps_trading(
                bars.get(row["security_id"], []), siblings, row, filing, transfer_window_sessions
            )
        )
        args = (
            bars.get(row["security_id"], []),
            siblings,
            row,
            filing,
            ended,
            transfer_window_sessions,
        )
        side = None if index in reopened else _withdrawn(*args, concurrent=True)
        if successors and index not in reopened:
            status, end = TRANSFERRED, previous_session(successors[0])
        elif side is not None:
            status, end = TRANSFERRED, side
        elif keeps is not None:
            status, end = TRANSFERRED, keeps[0]
        elif index not in reopened and (withdrawn := _withdrawn(*args)) is not None:
            status, end = TRANSFERRED, withdrawn
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

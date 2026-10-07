"""Security master core (spec req 3): `securities` and `listings` rows from a
`FilingSource`, and `securities_as_of`.

`build_master` is pure (records in, rows out); `write_master` inserts them.
Delisting rows and their read-time derivation are T8b; classification is T9.

**Existence.** A CIK gets a `securities` row from its **earliest issuer
filing** in the full-history filing index (`master.issuer_forms`; a Form 4
reporting owner never qualifies), `known_at` = that acceptance, `name` = the
index's company name at that filing, `provenance = filing`. The index is
always read with no `since`, so delisted names exist at every historical T.

**One `security_id` per listed class** (see `store/schema.py`). The CIK's
first security is `primary_security_id(cik)` (the CIK itself). Cover pages
(`dei:Security12bTitle`, `TradingSymbol`, `SecurityExchangeName`) are walked
in acceptance order; each listed class matches an existing class by its
title up to the first comma (so a reworded par-value clause is the same
class), else by a ticker it currently trades under, and no class takes two
items of one page unless it is the same ticker on a second exchange. Title
first means two classes that swap tickers keep their own ids. The primary
is the first common-equity class (`_COMMON_WORDS`) on the earliest cover
page, else its first class; any later unmatched class becomes
`<cik>:<title-slug>`, with a `securities` row known at the cover page that
first names it. A class whose ticker and title both change on one cover
page is read as a new class: a documented limit, not a guess.

**Listings.** A `filing` listing row is written when a class shows a
(ticker, exchange) pair it did not show on its previous cover page, so a
ticker change or an exchange transfer adds a row and a repeated cover page
does not. `valid_from` is the XNYS session on or after the acceptance's
New York date; `known_at` is the acceptance. A class shows a pair once per
page: when a page lists one class's pair under two titles (a filer's
duplicate, e.g. an ADS and its underlying shares, or two notes given one
ticker), the row takes the first item's title.

**Relistings** (#820, spec req 4). A Form 25 (`source.delistings()`, any
of `DELISTING_FORMS`) is matched to a class known before it, as
`store.delistings` resolves it: the one class listed on its exchange whose
title matches up to the first comma, else, for a plain common-equity
title, the one plain-common class on that exchange. The next cover page
accepted after it that lists that class on that exchange opens a new row
even for an unchanged pair (a holding-company reorganisation, a change of
domicile, an LP or REIT conversion: CMPR, CG, WELL, FCFS, KIM), with
`known_at` that cover page's acceptance and `valid_from` no earlier than
the session after the filing session. A cover page before the delisting
takes effect (the effective day: the filing's, else filing day + 10) only
shows shares still trading and relists nothing (a Form 25 ends the latest listing
starting on or before that session). An amendment (`/A`) does not re-arm
a class and exchange that already had one, so a late 25-NSE/A never
splits the listing it amends. A cover page that shows the class only on
another exchange (and not on the Form 25's) settles it as a transfer: a later move back is
an ordinary new row, never a relisting.

**New equity after a Form 25 is a new security** (owner decision on
#820): when the CIK filed an 8-A12B (a new 12(b) registration) between
`master.transfer_window_sessions` sessions before the filing session and
that cover page, the relisted shares are a successor, not the old class:
post-bankruptcy equity (CRC, OAS, DBD, GPOR, MNK, WW, WOLF all filed one,
none of the five reorganisations above did). The successor gets the id
`<cik>@<valid_from>`, its own `securities` row known at the cover page,
the old class's titles, and a row starting no earlier than the session
after the Form 25's effective day (that cover page may precede it).
The old class takes no later cover-page item, so no listing joins the two
and no return spans the gap. A Form 15 (15-12B or 15-12G) within
`master.reorganisation_window_sessions` sessions of the Form 25 vetoes
it: the old class was exchanged in a reorganisation or merger, so the
8-A12B registers the same holders' shares (WSC's merger, #834; every
reorganisation above filed one, no bankruptcy did). `MasterBuild.successions` records each pair.
The id has no `<cik>:` prefix on purpose: the price resolver treats it as
its own company, so its listing takes the ticker from the old holder,
which has left by then. Limit: an 8-A12B for another class (new notes) in
that window also makes a successor; the error is a split history, never
a return across a bankruptcy.

**Listing evidence without a cover page** (#834). Two other records
relist a class a Form 25 left pending, for its last pairs on that
exchange, from the session after the Form 25 took effect: an 8-K12B (a
successor issuer's 12(b) registration under Rule 12g-3) accepted after
the class's last cover page, with no 8-A12B near the Form 25 (that
registers another exchange or new equity, which a cover page settles:
CTO's transfer), known at the later of it and the Form 25
(FRT, whose later cover pages parse with no listing; OKE before its next
cover page); else the earliest companies snapshot fetched after the Form
25 that still names the ticker on that exchange, `provenance =
snapshot`, known at the fetch (SA). The 8-K12B must fall inside
`master.reorganisation_window_sessions` sessions of the Form 25's filing
session (so an old Form 25 is never revived years later), and the fetch
must come `master.snapshot_relisting_lag_days` after the effective day
with no Form 15 from that window's start to the fetch (so a fetch before
SEC drops a delisted or acquired name's ticker never relists it). Fetches are judged in time
order with the cover pages, against what was known at the fetch. The same new-equity test applies. A
company acquired into another CIK (GORO, STRR) has neither, and stays
delisted: its ticker's later bars belong to the other CIK.

**Snapshot listings** (pre-~2019 names have no cover page). A companies
snapshot entry attaches to the class trading under its ticker on the cover
pages known at its `fetched_at` (never later ones), or to the primary of a
CIK with no cover page yet and one ticker in that fetch; anything else is
returned in `unmatched_snapshot` rather than guessed. When
`master.static_columns` includes both `ticker` and `exchange`, the row is
`snapshot_static` and `valid_from` is the class's first session, but only
where no cover page covers that span and the class's earliest cover-page
listing has the snapshot's ticker and exchange (a changed ticker leaves the
earlier one unknown: reported, not written). Otherwise the row is
`snapshot`, valid from the fetch session, and only for a class with no
cover page. `known_at` is always the fetch time. Only the earliest fetch
writes a class's static span. A name delisted before the snapshot and
before cover pages has no listing at all: it is returned in
`unlisted_securities` so health and the survivorship gap can count it.

**Issue #35 is open.** Whether a `snapshot_static` row may be read before
its own `known_at` is an owner decision. This module writes the honest
`known_at` and `provenance`; `securities_as_of`, like T6's
`listings_as_of`, shows no row before its `known_at`.

**Benchmarks** (`benchmarks` config) are seeded, not derived from EDGAR:
`BENCH:<ticker>`, `benchmark = TRUE`, `source = config`, cik and name from
the snapshot entry with that ticker, `known_at` its fetch time. Their
static listing starts at `calendar.start` (nominal; bars decide what
exists). A benchmark absent from the snapshot is returned in
`missing_benchmarks`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingIndexEntry,
    FilingSource,
)
from tradepartner.calendar import is_session, next_session, previous_session
from tradepartner.config import Settings
from tradepartner.store.asof import _EXCHANGE_TZ, _latest_as_of, _validate_t
from tradepartner.store.db import insert_row
from tradepartner.timeutil import ensure_tz_aware_utc

_EDGAR = "edgar"
_CONFIG = "config"
_STATIC_LISTING_COLUMNS = frozenset({"ticker", "exchange"})
#: A new 12(b) registration: near a Form 25, it marks the relisted shares as new equity.
_NEW_REGISTRATION_FORMS = frozenset({"8-A12B"})
#: A deregistration: near a Form 25, the old class was exchanged in a reorganisation
#: or merger, so an 8-A12B there registers the same holders' shares, not new equity (#834).
_DEREGISTRATION_FORMS = frozenset({"15-12B", "15-12G"})
#: A successor issuer's 12(b) registration (Rule 12g-3): the class is listed again (#834).
_SUCCESSOR_ISSUER_FORMS = frozenset({"8-K12B"})
_MARKER_FORMS = _NEW_REGISTRATION_FORMS | _DEREGISTRATION_FORMS | _SUCCESSOR_ISSUER_FORMS
#: Days from filing to effect when a Form 25 states none (Rule 12d2-2), as in `store.delistings`.
_DEFAULT_EFFECTIVE_DAYS = 10

Row = dict[str, Any]


@dataclass(frozen=True)
class MasterBuild:
    """Rows for `securities` and `listings`, plus what could not be attached."""

    securities: tuple[Row, ...]
    listings: tuple[Row, ...]
    unmatched_snapshot: tuple[CompanySnapshotEntry, ...]
    missing_benchmarks: tuple[str, ...]
    unlisted_securities: tuple[str, ...] = ()
    successions: tuple[Succession, ...] = ()
    class_titles: tuple[ClassTitle, ...] = ()


@dataclass(frozen=True)
class ClassTitle:
    """A title a class showed on a cover page, known from `known_at` (that
    page's acceptance), once per class and title. A listing row keeps only
    the title of the page that first showed its pair; a later retitling
    ("Common Stock" to "Class A Common Stock" for one pair) is here (#1166)."""

    security_id: str
    title: str
    known_at: datetime


@dataclass(frozen=True)
class Succession:
    """New equity listed after a Form 25 (#820): `security_id` succeeds
    `predecessor_id`, known from `known_at` (the relisting cover page)."""

    predecessor_id: str
    security_id: str
    known_at: datetime


@dataclass(frozen=True)
class _Stop:
    """A Form 25 that ended a class's listing on one exchange."""

    after: date  # the first session a later row of the class may start
    effective_after: date  # the first session after the delisting took effect
    window_start: date  # the earliest session of an 8-A12B that marks new equity
    filed_at: datetime  # the Form 25's acceptance
    since: datetime | None  # the last cover page showing the class before it
    effective_on: date  # the day the delisting took effect
    reorg_start: date  # sessions of an 8-K12B or Form 15 that belong to it
    reorg_end: date


#: Acceptances per marker form (`_MARKER_FORMS`) of one CIK.
_Marks = Mapping[str, Sequence[datetime]]


def _registered(stop: _Stop, until: datetime, marks: _Marks) -> bool:
    """An 8-A12B accepted from `stop.window_start` to `until`."""
    return any(
        stop.window_start <= _session_of(at) and at <= until
        for form in _NEW_REGISTRATION_FORMS
        for at in marks.get(form, ())
    )


def _deregistered(stop: _Stop, until: datetime, marks: _Marks, *, windowed: bool = True) -> bool:
    """A Form 15 accepted by `until` inside the stop's reorganisation window
    (or, with `windowed=False`, any time from the window's start)."""
    return any(
        stop.reorg_start <= _session_of(at)
        and (not windowed or _session_of(at) <= stop.reorg_end)
        and at <= until
        for form in _DEREGISTRATION_FORMS
        for at in marks.get(form, ())
    )


def _new_equity(stop: _Stop, until: datetime, marks: _Marks) -> bool:
    """True when the shares listed again after `stop` (evidence accepted at
    `until`) are new equity: an 8-A12B from `stop.window_start` to `until`
    and no Form 15 in the reorganisation window (#820, #834)."""
    return _registered(stop, until, marks) and not _deregistered(stop, until, marks)


_Pairs = frozenset[tuple[str, str]]


@dataclass
class _Class:
    security_id: str
    known_at: datetime
    titles: set[str] = field(default_factory=set)
    pairs: _Pairs = frozenset()  # (ticker, exchange) on the latest cover page showing it
    history: list[tuple[datetime, _Pairs]] = field(default_factory=list)
    first_listing: tuple[str, str, date, datetime] | None = None  # + the row's known_at
    static_ticker: str | None = None  # ticker of the static span, once written
    exchanges: set[str] = field(default_factory=set)  # exchanges of its cover-page rows
    pair_titles: dict[tuple[str, str], str] = field(default_factory=dict)  # latest title per pair
    delisted_on: set[str] = field(default_factory=set)  # exchanges a Form 25 named
    ended: dict[str, _Stop] = field(default_factory=dict)  # Form 25s no cover page followed yet
    retired: bool = False  # succeeded by new equity: takes no later cover-page item

    def pairs_at(self, t: datetime) -> _Pairs:
        """The pairs this class showed on the latest cover page known at `t`."""
        shown: _Pairs = frozenset()
        for known_at, pairs in self.history:
            if known_at <= t:
                shown = pairs
        return shown

    def first_listing_at(self, t: datetime) -> tuple[str, str, date] | None:
        if self.first_listing is None or self.first_listing[3] > t:
            return None
        return self.first_listing[:3]


def primary_security_id(cik: str) -> str:
    """The `security_id` of a CIK's first class."""
    return cik


#: Title words that mark a common-equity class, preferred as a CIK's primary.
_COMMON_WORDS = ("common", "ordinary", "capital stock")


def _norm_title(title: str) -> str:
    head = title.lower().split(",", 1)[0]
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", head).split())


def _is_common(title: str) -> bool:
    norm = _norm_title(title)
    return any(word in norm for word in _COMMON_WORDS)


def _session_on_or_after(day: date) -> date:
    return day if is_session(day) else next_session(day)


def _first_session(settings: Settings) -> date:
    """The calendar's first session. `calendar.start` need not be one, and
    the calendar raises (`DateOutOfBounds`, a `ValueError`) for any day
    before its first session, so step forward from it."""
    day = settings.calendar.start
    while day <= settings.calendar.end:
        try:
            if is_session(day):
                return day
        except ValueError:
            pass
        day += timedelta(days=1)
    raise ValueError("calendar.start..calendar.end holds no session")


def _session_of(instant: datetime) -> date:
    return _session_on_or_after(instant.astimezone(_EXCHANGE_TZ).date())


#: Words that make a title mentioning common stock something else; mirrors
#: `store.delistings` (which imports this module, so it cannot be imported here).
_NOT_COMMON_WORDS = ("warrant", "right", "unit", "preferred", "depositary", "note", "debenture")


def _is_plain_common(title: str) -> bool:
    norm = _norm_title(title)
    return _is_common(title) and not any(word in norm for word in _NOT_COMMON_WORDS)


def _delisted_class(classes: Sequence[_Class], filing: DelistingFiling) -> _Class | None:
    """The live class `filing` delists, resolved like `store.delistings`
    but only among classes already listed, or `None` if not certain."""
    on_exchange = [c for c in classes if not c.retired and filing.exchange in c.exchanges]
    title = _norm_title(filing.class_title)
    by_title = [c for c in on_exchange if title in c.titles]
    if len(by_title) == 1:
        return by_title[0]
    if by_title or not _is_plain_common(filing.class_title):
        return None
    common = [c for c in on_exchange if all(_is_plain_common(t) for t in c.titles)]
    return common[0] if len(common) == 1 else None


def _sessions_around(session: date, sessions: int) -> tuple[date, date]:
    low = high = session
    for _ in range(sessions):
        low, high = previous_session(low), next_session(high)
    return low, high


def _stop(filing: DelistingFiling, settings: Settings, since: datetime | None) -> _Stop:
    session = _session_of(filing.accepted_at)
    filed_on = filing.accepted_at.astimezone(_EXCHANGE_TZ).date()
    effective = filing.effective_on or filed_on + timedelta(days=_DEFAULT_EFFECTIVE_DAYS)
    window_start, _ = _sessions_around(session, settings.master.transfer_window_sessions)
    reorg_start, reorg_end = _sessions_around(
        session, settings.master.reorganisation_window_sessions
    )
    return _Stop(
        next_session(session),
        next_session(effective),
        window_start,
        filing.accepted_at,
        since,
        effective,
        reorg_start,
        reorg_end,
    )


#: One dated record in a CIK's timeline: a Form 25, an 8-K12B, a snapshot fetch.
_Event = tuple[datetime, int, str, "DelistingFiling | CompanySnapshotEntry | None"]


def _row(known_at: datetime, ingested_at: datetime, source: str, provenance: str) -> Row:
    if known_at > ingested_at:
        raise ValueError(f"known_at {known_at.isoformat()} is after ingested_at")
    return {
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": source,
        "provenance": provenance,
    }


class _Builder:
    def __init__(self, settings: Settings, ingested_at: datetime) -> None:
        self.settings = settings
        self.ingested_at = ingested_at
        self.static = set(settings.master.static_columns) >= _STATIC_LISTING_COLUMNS
        self.securities: list[Row] = []
        self.listings: list[Row] = []
        self.unmatched: list[CompanySnapshotEntry] = []
        self.successions: list[Succession] = []
        self.class_titles: dict[tuple[str, str], ClassTitle] = {}  # first per (id, title)

    def security(
        self,
        security_id: str,
        cik: str,
        name: str,
        known_at: datetime,
        *,
        source: str = _EDGAR,
        provenance: str = "filing",
        benchmark: bool = False,
    ) -> None:
        self.securities.append(
            {"security_id": security_id, "cik": cik, "name": name, "benchmark": benchmark}
            | _row(known_at, self.ingested_at, source, provenance)
        )

    def listing(
        self,
        security_id: str,
        ticker: str,
        exchange: str,
        class_title: str | None,
        valid_from: date,
        known_at: datetime,
        *,
        source: str = _EDGAR,
        provenance: str = "filing",
    ) -> None:
        self.listings.append(
            {
                "security_id": security_id,
                "ticker": ticker,
                "exchange": exchange,
                "class_title": class_title,
                "valid_from": valid_from,
            }
            | _row(known_at, self.ingested_at, source, provenance)
        )

    def cover_pages(
        self,
        first: FilingIndexEntry,
        pages: Sequence[CoverPage],
        delistings: Sequence[DelistingFiling] = (),
        marks: _Marks | None = None,
        fetches: Sequence[CompanySnapshotEntry] = (),
    ) -> list[_Class]:
        """Classes and their cover-page listings for one CIK; `delistings`
        are its Form 25s, `marks` its marker filings (`_MARKER_FORMS`) and
        `fetches` its companies-snapshot entries (every fetch)."""
        cik = first.cik
        marks = marks or {}
        classes: list[_Class] = []
        # Form 25s, 8-K12Bs and snapshot fetches in time order (in that order on a tie).
        events: list[_Event] = [(f.accepted_at, 0, f.accession, f) for f in delistings]
        events += [
            (at, 1, "", None) for form in _SUCCESSOR_ISSUER_FORMS for at in marks.get(form, ())
        ]
        events += [(e.fetched_at, 2, e.ticker, e) for e in fetches]
        events.sort(key=lambda e: e[:3])
        for page in sorted(pages, key=lambda p: (p.accepted_at, p.accession)):
            while events and events[0][0] < page.accepted_at:
                self._event(first, classes, events.pop(0), marks)
            known_at = max(page.accepted_at, first.accepted_at)
            valid_from = _session_of(page.accepted_at)
            shown: dict[str, set[tuple[str, str]]] = defaultdict(set)
            claimed: dict[str, str] = {}  # security_id -> ticker it took on this page
            starts: dict[tuple[str, str], date] = {}  # (security_id, exchange) -> row start
            items = list(page.listings)
            if not classes:  # the primary is the first common class, else the first
                items.sort(key=lambda item: not _is_common(item.title))
            for item in items:
                live = [c for c in classes if not c.retired]
                cls = _match(live, item, claimed)
                if cls is None:
                    cls = self._new_class(first, item, classes, known_at)
                relisted = False
                stop = cls.ended.get(item.exchange)
                if stop is not None and (cls.security_id, item.exchange) not in starts:
                    new_equity = _new_equity(stop, page.accepted_at, marks)
                    # Before the delisting takes effect the old shares still
                    # trade, so the page relists only new equity (whose row
                    # starts after the effective day anyway).
                    if new_equity or valid_from >= stop.effective_after:
                        relisted = True
                        del cls.ended[item.exchange]
                        start = max(valid_from, stop.after)
                        if new_equity:
                            start = max(start, stop.effective_after)
                            cls = self._successor(first, cls, start, classes, known_at)
                        starts[(cls.security_id, item.exchange)] = start
                start = starts.get((cls.security_id, item.exchange), valid_from)
                claimed[cls.security_id] = item.ticker
                cls.titles.add(_norm_title(item.title))
                key = (cls.security_id, item.title)
                if key not in self.class_titles:
                    self.class_titles[key] = ClassTitle(cls.security_id, item.title, known_at)
                pair = (item.ticker, item.exchange)
                shown_already = pair in shown[cls.security_id]
                shown[cls.security_id].add(pair)
                if not shown_already:
                    cls.pair_titles[pair] = item.title
                if (relisted or pair not in cls.pairs) and not shown_already:
                    self.listing(cls.security_id, *pair, item.title, start, known_at)
                    cls.exchanges.add(item.exchange)
                    if cls.first_listing is None:
                        cls.first_listing = (item.ticker, item.exchange, start, known_at)
            for cls in classes:
                if cls.security_id in shown:
                    cls.pairs = frozenset(shown[cls.security_id])
                    cls.history.append((known_at, cls.pairs))
                    # A Form 25 on an exchange the page no longer lists for the
                    # class was a move (a transfer), not a pause before a relisting.
                    listed_on = {exchange for _, exchange in cls.pairs}
                    for exchange in [e for e in cls.ended if e not in listed_on]:
                        del cls.ended[exchange]
        for event in events:  # after the last cover page
            self._event(first, classes, event, marks)
        if not classes:
            classes.append(_Class(primary_security_id(cik), first.accepted_at))
        return classes

    def _event(
        self, first: FilingIndexEntry, classes: list[_Class], event: _Event, marks: _Marks
    ) -> None:
        """A Form 25 (recorded, and relisted at once if an 8-K12B since the
        class's last cover page and inside its reorganisation window precedes
        it), an 8-K12B inside a pending Form 25's window (relists it), or a
        snapshot fetch (relists a pending Form 25's ticker; see the module
        docstring). Never an 8-K12B with an 8-A12B near the Form 25: that
        registers another exchange (a transfer) or new equity, which only a
        cover page settles (CTO, #834)."""
        at, _, _, record = event
        if isinstance(record, DelistingFiling):
            stopped = self._delisting(classes, record)
            if stopped is None:
                return
            cls, stop = stopped
            successor_issuer = any(
                (stop.since is None or a > stop.since)
                and a <= at
                and stop.reorg_start <= _session_of(a)
                for form in _SUCCESSOR_ISSUER_FORMS
                for a in marks.get(form, ())
            )
            if successor_issuer and not _registered(stop, at, marks):
                self._relist(first, classes, cls, record.exchange, at, "filing", marks)
            return
        lag = timedelta(days=self.settings.master.snapshot_relisting_lag_days)
        for cls in [c for c in classes if not c.retired]:
            for exchange, stop in list(cls.ended.items()):
                if stop.filed_at >= at:
                    continue
                if record is None:  # an 8-K12B
                    if _session_of(at) <= stop.reorg_end and not _registered(stop, at, marks):
                        self._relist(first, classes, cls, exchange, at, "filing", marks)
                    continue
                tickers = {ticker for ticker, ex in cls.pairs if ex == exchange}
                if (
                    record.exchange == exchange
                    and record.ticker in tickers
                    and at.astimezone(_EXCHANGE_TZ).date() >= stop.effective_on + lag
                    and not _deregistered(stop, at, marks, windowed=False)
                ):
                    self._relist(first, classes, cls, exchange, at, "snapshot", marks)

    def _relist(
        self,
        first: FilingIndexEntry,
        classes: list[_Class],
        cls: _Class,
        exchange: str,
        known_at: datetime,
        provenance: str,
        marks: _Marks,
    ) -> None:
        """List `cls`'s last pairs on `exchange` again, known at `known_at`,
        from the session after its Form 25 took effect (#834): evidence
        other than a cover page (an 8-K12B, a companies snapshot)."""
        stop = cls.ended.pop(exchange)
        pairs = sorted(pair for pair in cls.pairs if pair[1] == exchange)
        if not pairs:
            return
        start = max(stop.after, stop.effective_after)
        target = cls
        if _new_equity(stop, known_at, marks):
            target = self._successor(first, cls, start, classes, known_at)
            target.pairs = frozenset(pairs)
            target.history.append((known_at, target.pairs))
            target.first_listing = (*pairs[0], start, known_at)
        for pair in pairs:
            title = None if provenance == "snapshot" else cls.pair_titles.get(pair)
            self.listing(target.security_id, *pair, title, start, known_at, provenance=provenance)
            target.exchanges.add(exchange)

    def _delisting(
        self, classes: list[_Class], filing: DelistingFiling
    ) -> tuple[_Class, _Stop] | None:
        """Record `filing` on the class it delists, if one is certain."""
        cls = _delisted_class(classes, filing)
        if cls is None:
            return None
        if filing.form.endswith("/A") and filing.exchange in cls.delisted_on:
            return None  # amends a Form 25 already counted
        cls.delisted_on.add(filing.exchange)
        since = cls.history[-1][0] if cls.history else None
        stop = _stop(filing, self.settings, since)
        cls.ended[filing.exchange] = stop
        return cls, stop

    def _successor(
        self,
        first: FilingIndexEntry,
        old: _Class,
        start: date,
        classes: list[_Class],
        known_at: datetime,
    ) -> _Class:
        """New equity succeeding `old` from `start` (its own security)."""
        base = f"{first.cik}@{start.isoformat()}"
        taken = {c.security_id for c in classes}
        security_id, n = base, 1
        while security_id in taken:
            n += 1
            security_id = f"{base}-{n}"
        cls = _Class(security_id, known_at, titles=set(old.titles))
        old.retired = True
        self.security(security_id, first.cik, first.company_name, known_at)
        self.successions.append(Succession(old.security_id, security_id, known_at))
        classes.append(cls)
        return cls

    def _new_class(
        self, first: FilingIndexEntry, item: CoverListing, classes: list[_Class], known_at: datetime
    ) -> _Class:
        if not classes:
            cls = _Class(primary_security_id(first.cik), first.accepted_at)
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", _norm_title(item.title).replace("%", "pct"))
            base = f"{first.cik}:{slug.strip('-')}"
            taken = {c.security_id for c in classes}
            security_id, n = base, 1
            while security_id in taken:
                n += 1
                security_id = f"{base}-{n}"
            cls = _Class(security_id, known_at)
            self.security(security_id, first.cik, first.company_name, known_at)
        classes.append(cls)
        return cls

    def snapshot(self, classes: list[_Class], entries: list[CompanySnapshotEntry]) -> None:
        """Attach snapshot entries (sorted by fetch time) using only what the
        cover pages showed by each entry's `fetched_at`, so a row's presence
        never depends on a filing accepted after its own `known_at`."""
        for entry in entries:
            t = entry.fetched_at
            listed = [c for c in classes if c.pairs_at(t)]
            cls = next((c for c in listed if entry.ticker in {k for k, _ in c.pairs_at(t)}), None)
            same_fetch = sum(1 for e in entries if e.fetched_at == t)
            if cls is None and not listed and same_fetch == 1:
                cls = classes[0]  # a CIK with no cover page yet and one ticker
            if cls is None:
                self.unmatched.append(entry)
                continue
            if self.static and cls.static_ticker is not None:
                # The earliest fetch already covered the span before cover
                # pages; a later fetch showing another ticker is reported.
                if entry.ticker != cls.static_ticker:
                    self.unmatched.append(entry)
                continue
            first_listing = cls.first_listing_at(t)
            if first_listing is None:
                if self.static:
                    start, provenance = _session_of(cls.known_at), "snapshot_static"
                else:
                    start, provenance = _session_of(t), "snapshot"
                self._snapshot_listing(cls, entry, start, provenance)
            elif self.static:
                ticker, exchange, first_from = first_listing
                if (ticker, exchange) != (entry.ticker, entry.exchange):
                    self.unmatched.append(entry)
                elif _session_of(cls.known_at) < first_from:
                    self._snapshot_listing(cls, entry, _session_of(cls.known_at), "snapshot_static")

    def _snapshot_listing(
        self, cls: _Class, entry: CompanySnapshotEntry, start: date, provenance: str
    ) -> None:
        if provenance == "snapshot_static" and cls.static_ticker is None:
            cls.static_ticker = entry.ticker
        self.listing(
            cls.security_id,
            entry.ticker,
            entry.exchange,
            None,
            start,
            entry.fetched_at,
            provenance=provenance,
        )

    def benchmark(self, entry: CompanySnapshotEntry) -> None:
        security_id = f"BENCH:{entry.ticker}"
        static_name = "name" in self.settings.master.static_columns
        name_prov = "snapshot_static" if static_name else "snapshot"
        self.security(
            security_id,
            entry.cik,
            entry.name,
            entry.fetched_at,
            benchmark=True,
            source=_CONFIG,
            provenance=name_prov,
        )
        if self.static:
            start, provenance = _first_session(self.settings), "snapshot_static"
        else:
            start, provenance = _session_of(entry.fetched_at), "snapshot"
        self.listing(
            security_id,
            entry.ticker,
            entry.exchange,
            None,
            start,
            entry.fetched_at,
            source=_CONFIG,
            provenance=provenance,
        )


def _match(classes: list[_Class], item: CoverListing, claimed: dict[str, str]) -> _Class | None:
    """The existing class `item` belongs to, among classes not yet claimed on
    this page: the class already holding this ticker and title on this page
    (a second exchange); else one matching both title and current ticker;
    else the only class with this title; else one with this current ticker.
    Title before ticker keeps two classes that swap tickers apart; the
    uniqueness rule keeps reordered same-titled series apart."""
    title = _norm_title(item.title)
    for cls in classes:
        if claimed.get(cls.security_id) == item.ticker and title in cls.titles:
            return cls
    free = [cls for cls in classes if cls.security_id not in claimed]
    by_ticker = [c for c in free if item.ticker in {ticker for ticker, _ in c.pairs}]
    exact = next((c for c in by_ticker if title in c.titles), None)
    if exact is not None:
        return exact
    # A title shared by several classes (preferred series that differ only
    # after the first comma) cannot decide on its own.
    by_title = [c for c in classes if title in c.titles]
    if len(by_title) == 1 and by_title[0] in free:
        return by_title[0]
    return by_ticker[0] if by_ticker else None


def build_master(source: FilingSource, settings: Settings, *, ingested_at: datetime) -> MasterBuild:
    """`securities` and `listings` rows for every issuer CIK and benchmark.

    Pure apart from calling `source`. Raises `ValueError` if any record's
    `known_at` is later than `ingested_at` (spec: `known_at <= ingested_at`).
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    issuer_forms = set(settings.master.issuer_forms)
    first: dict[str, FilingIndexEntry] = {}
    marks: dict[str, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    for entry in source.filing_index():  # full history, never `since` (spec req 3)
        if entry.form in _MARKER_FORMS:
            marks[entry.cik][entry.form].append(entry.accepted_at)
        if entry.form not in issuer_forms:
            continue
        seen = first.get(entry.cik)
        order = (entry.accepted_at, entry.accession)
        if seen is None or order < (seen.accepted_at, seen.accession):
            first[entry.cik] = entry

    snapshot: dict[tuple[str, str, str], CompanySnapshotEntry] = {}
    fetches: dict[str, list[CompanySnapshotEntry]] = defaultdict(list)  # every fetch, per CIK
    for snap in source.companies_snapshot():  # earliest fetch wins per (cik, ticker, exchange)
        fetches[snap.cik].append(snap)
        key = (snap.cik, snap.ticker, snap.exchange)
        if key not in snapshot or snap.fetched_at < snapshot[key].fetched_at:
            snapshot[key] = snap
    benchmarks = set(settings.benchmarks)
    by_cik: dict[str, list[CompanySnapshotEntry]] = defaultdict(list)
    for snap in sorted(snapshot.values(), key=lambda e: (e.cik, e.fetched_at, e.ticker)):
        if snap.ticker not in benchmarks:
            by_cik[snap.cik].append(snap)

    delistings: dict[str, list[DelistingFiling]] = defaultdict(list)
    for filing in source.delistings():  # full history, like the index
        delistings[filing.cik].append(filing)

    builder = _Builder(settings, ingested_at)
    for cik in sorted(first):
        entry = first[cik]
        builder.security(primary_security_id(cik), cik, entry.company_name, entry.accepted_at)
        cik_marks = marks.get(cik, {})
        classes = builder.cover_pages(
            entry, source.cover_pages(cik), delistings.get(cik, ()), cik_marks, fetches.get(cik, ())
        )
        builder.snapshot(classes, by_cik.pop(cik, []))
    for entries in by_cik.values():  # snapshot names with no issuer filing
        builder.unmatched.extend(entries)

    missing: list[str] = []
    for ticker in settings.benchmarks:
        found = sorted(
            (e for e in snapshot.values() if e.ticker == ticker), key=lambda e: e.fetched_at
        )
        if found:
            builder.benchmark(found[0])
        else:
            missing.append(ticker)

    listed = {row["security_id"] for row in builder.listings}
    return MasterBuild(
        securities=tuple(builder.securities),
        listings=tuple(builder.listings),
        unmatched_snapshot=tuple(builder.unmatched),
        missing_benchmarks=tuple(missing),
        unlisted_securities=tuple(
            row["security_id"] for row in builder.securities if row["security_id"] not in listed
        ),
        successions=tuple(builder.successions),
        class_titles=tuple(builder.class_titles.values()),
    )


def write_master(conn: duckdb.DuckDBPyConnection, build: MasterBuild) -> int:
    """Insert every row of `build`; return the number inserted.

    Dedupe of an unchanged re-run belongs to ingest (T16); a duplicate row
    here trips the table's `UNIQUE` constraint.
    """
    for row in build.securities:
        insert_row(conn, "securities", row)
    for row in build.listings:
        insert_row(conn, "listings", row)
    return len(build.securities) + len(build.listings)


def securities_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """`securities` rows known by `t`, latest revision per `security_id`,
    sorted by `security_id`. A bare date raises `TypeError`, a naive
    datetime `ValueError` (same rules as `store.asof`)."""
    return _latest_as_of(conn, "securities", ("security_id",), _validate_t(t), security_ids)

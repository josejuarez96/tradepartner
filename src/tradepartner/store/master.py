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
New York date; `known_at` is the acceptance.

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FilingIndexEntry,
    FilingSource,
)
from tradepartner.calendar import is_session, next_session
from tradepartner.config import Settings
from tradepartner.store.asof import _EXCHANGE_TZ, _latest_as_of, _validate_t
from tradepartner.store.db import insert_row
from tradepartner.timeutil import ensure_tz_aware_utc

_EDGAR = "edgar"
_CONFIG = "config"
_STATIC_LISTING_COLUMNS = frozenset({"ticker", "exchange"})

Row = dict[str, Any]


@dataclass(frozen=True)
class MasterBuild:
    """Rows for `securities` and `listings`, plus what could not be attached."""

    securities: tuple[Row, ...]
    listings: tuple[Row, ...]
    unmatched_snapshot: tuple[CompanySnapshotEntry, ...]
    missing_benchmarks: tuple[str, ...]
    unlisted_securities: tuple[str, ...] = ()


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

    def cover_pages(self, first: FilingIndexEntry, pages: Sequence[CoverPage]) -> list[_Class]:
        cik = first.cik
        classes: list[_Class] = []
        for page in sorted(pages, key=lambda p: (p.accepted_at, p.accession)):
            known_at = max(page.accepted_at, first.accepted_at)
            valid_from = _session_of(page.accepted_at)
            shown: dict[str, set[tuple[str, str]]] = defaultdict(set)
            claimed: dict[str, str] = {}  # security_id -> ticker it took on this page
            items = list(page.listings)
            if not classes:  # the primary is the first common class, else the first
                items.sort(key=lambda item: not _is_common(item.title))
            for item in items:
                cls = _match(classes, item, claimed)
                if cls is None:
                    cls = self._new_class(first, item, classes, known_at)
                claimed[cls.security_id] = item.ticker
                cls.titles.add(_norm_title(item.title))
                pair = (item.ticker, item.exchange)
                shown[cls.security_id].add(pair)
                if pair not in cls.pairs:
                    self.listing(cls.security_id, *pair, item.title, valid_from, known_at)
                    if cls.first_listing is None:
                        cls.first_listing = (item.ticker, item.exchange, valid_from, known_at)
            for cls in classes:
                if cls.security_id in shown:
                    cls.pairs = frozenset(shown[cls.security_id])
                    cls.history.append((known_at, cls.pairs))
        if not classes:
            classes.append(_Class(primary_security_id(cik), first.accepted_at))
        return classes

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
    for entry in source.filing_index():  # full history, never `since` (spec req 3)
        if entry.form not in issuer_forms:
            continue
        seen = first.get(entry.cik)
        order = (entry.accepted_at, entry.accession)
        if seen is None or order < (seen.accepted_at, seen.accession):
            first[entry.cik] = entry

    snapshot: dict[tuple[str, str, str], CompanySnapshotEntry] = {}
    for snap in source.companies_snapshot():  # earliest fetch wins per (cik, ticker, exchange)
        key = (snap.cik, snap.ticker, snap.exchange)
        if key not in snapshot or snap.fetched_at < snapshot[key].fetched_at:
            snapshot[key] = snap
    benchmarks = set(settings.benchmarks)
    by_cik: dict[str, list[CompanySnapshotEntry]] = defaultdict(list)
    for snap in sorted(snapshot.values(), key=lambda e: (e.cik, e.fetched_at, e.ticker)):
        if snap.ticker not in benchmarks:
            by_cik[snap.cik].append(snap)

    builder = _Builder(settings, ingested_at)
    for cik in sorted(first):
        entry = first[cik]
        builder.security(primary_security_id(cik), cik, entry.company_name, entry.accepted_at)
        classes = builder.cover_pages(entry, source.cover_pages(cik))
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

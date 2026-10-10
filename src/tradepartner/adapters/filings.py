"""`FilingSource`: the interface every filing adapter implements (spec req 6).

A filing adapter returns **parsed records**, never raw payloads: the real
EDGAR adapter (T11) is a thin raw-fetch client (`edgar_raw`) plus pure
parsers, and the fixture adapter (`fixture_filings`) serves records built
in memory. Consumers (the security master, ingest) depend only on this
module.

Every record carries the instant it became knowable: `accepted_at` (SEC
acceptance timestamp, `provenance = filing`) or `fetched_at` (the fetch
time of a current-only endpoint, `provenance = snapshot` or
`snapshot_static`). Both must be tz-aware and are normalized to UTC on
construction (CLAUDE.md: datetimes are always tz-aware UTC). A `cik` is the
10-digit zero-padded form EDGAR uses in URLs (`0000320193`), so two
spellings of one issuer can never become two securities. Exchange names are
whatever the parser normalized them to; this module does not map them.
"""

from __future__ import annotations

import abc
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from tradepartner.timeutil import ensure_tz_aware_utc

_CIK_PATTERN = re.compile(r"\d{10}")


def _check_cik(cik: str) -> None:
    if not isinstance(cik, str) or not _CIK_PATTERN.fullmatch(cik):
        raise ValueError(f"cik must be a 10-digit zero-padded string, got {cik!r}")


def _utc(record: object, field_name: str) -> None:
    """Normalize a frozen record's datetime field to UTC in place, or raise."""
    value = getattr(record, field_name)
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a tz-aware datetime, got {value!r}")
    object.__setattr__(record, field_name, ensure_tz_aware_utc(value, field_name=field_name))


def _utc_optional(record: object, field_name: str) -> None:
    """As `_utc`, but `None` passes through unchanged (#660:
    `StatementFactRecord.accepted_at` is `None` for an accession with no
    stamp record yet; anything else must still be a tz-aware datetime)."""
    value = getattr(record, field_name)
    if value is None:
        return
    _utc(record, field_name)


@dataclass(frozen=True)
class FilingIndexEntry:
    """One row of EDGAR's full-history filing index."""

    cik: str
    company_name: str
    form: str
    accession: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "accepted_at")


@dataclass(frozen=True)
class CompanySnapshotEntry:
    """One row of the current-only companies snapshot (ticker, exchange)."""

    cik: str
    name: str
    ticker: str
    exchange: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "fetched_at")


@dataclass(frozen=True)
class FilingHeader:
    """The SGML header of one filing: its form and the issuer's SIC code."""

    cik: str
    accession: str
    form: str
    sic: int | None
    accepted_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "accepted_at")


@dataclass(frozen=True)
class CoverListing:
    """One registered class on a cover page (`dei:Security12bTitle`,
    `dei:TradingSymbol`, `dei:SecurityExchangeName`)."""

    title: str
    ticker: str
    exchange: str


@dataclass(frozen=True)
class CoverPage:
    """The cover page of one periodic filing, listing every registered class."""

    cik: str
    accession: str
    accepted_at: datetime
    listings: tuple[CoverListing, ...]

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "accepted_at")


@dataclass(frozen=True)
class FactRecord:
    """One XBRL fact (e.g. shares outstanding) with its cover `as_of_date`
    and class dimension (`""` when undimensioned, matching the store)."""

    cik: str
    fact_name: str
    as_of_date: date
    class_member: str
    value: float
    accession: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "accepted_at")


@dataclass(frozen=True)
class DelistingFiling:
    """A Form 25 or 25-NSE naming the class and exchange it removes."""

    cik: str
    form: str
    class_title: str
    exchange: str
    accession: str
    accepted_at: datetime
    effective_on: date | None = None

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc(self, "accepted_at")


@dataclass(frozen=True)
class StatementFactRecord:
    """One as-filed statement-fact entry (amendment 2026-10-03, #660):
    revenue, cost of revenue, gross profit, total assets or operating
    cash flow, as the companyfacts payload carries it for one tag and
    period. **Reported values only** — the parser never derives a
    missing `gross_profit`; that is the ingest's job (T77b), working
    from the stored `revenue` and `cost_of_revenue` rows, so this record
    carries no `basis`.

    `period_start` is `None` for an instant fact (`total_assets`;
    `period_days` is then `0`); for a duration it is the entry's own
    period start, never `None`, and `period_end` must be strictly after
    it (never equal — an instant is `period_start=None`, not a one-day
    duration). `period_days` is computed, not stored, since the spec's
    `StatementFactRecord` field list has no such field — the table
    column of the same name is `(period_end - period_start).days`, or
    `0` for an instant.

    `accepted_at` is the filing's SEC acceptance instant (submissions
    UTC) — `None` when the accession has no stamp record yet (the
    ingest's hold rule, T77b, holds the key rather than writing it).
    Unlike every other record in this module, a `None` is valid, so
    `accepted_at` is tz-checked **only when set** (`_utc_optional`).

    `filed` is the companyfacts entry's own `filed` date — read only by
    the ingest's hold rule to compare against an unstamped carrier's
    date, and **never stored**: `statement_facts` has no `filed` column
    (spec "Data / interfaces" > Amendment 2026-10-03, "Interfaces").
    """

    cik: str
    fact_name: str
    xbrl_tag: str
    period_start: date | None
    period_end: date
    value: float
    unit: str
    form: str
    accession: str
    accepted_at: datetime | None
    filed: date
    comparative: bool

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _utc_optional(self, "accepted_at")
        if self.period_start is not None and self.period_end <= self.period_start:
            raise ValueError(
                "period_end must be strictly after period_start for a duration fact "
                f"(got period_start={self.period_start!r}, period_end={self.period_end!r}); "
                "an instant fact (period_days = 0) uses period_start=None instead"
            )

    @property
    def period_days(self) -> int:
        """`0` for an instant fact (`period_start is None`), else the
        duration in days — the `statement_facts.period_days` column's
        value, computed here rather than stored on the record."""
        if self.period_start is None:
            return 0
        return (self.period_end - self.period_start).days


@dataclass(frozen=True)
class FilingEvent:
    """One filing-event record (amendment 2026-10-09, #1358): a filing whose
    form is in `edgar.event_forms` (`8-K`, `8-K/A` by default), with the
    submissions payload's `items` string kept **verbatim** (`"2.02,9.01"`;
    `""` when the payload lists none, never `None`) and the filing's stamped
    SEC acceptance. Every field is set: an accession with no stamp, a
    settled-unstampable one or one whose `items` is unknown is never a
    record (the adapter counts it instead). The store row's `known_at` is
    this `accepted_at`, never the index's filing date."""

    cik: str
    accession: str
    form: str
    items: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        if not isinstance(self.items, str):
            raise TypeError(
                f"items must be the payload's verbatim string ('' for none), got {self.items!r}"
            )
        _utc(self, "accepted_at")


class FilingSource(abc.ABC):
    """Filing data, as parsed records (spec "Interfaces").

    `since`, where accepted, keeps records with `accepted_at >= since`;
    `None` means full history. The security master always calls
    `filing_index()` with no `since` (spec req 3: the index is scanned over
    full history regardless of `--since`). Every method returns records
    sorted by their timestamp, then accession or ticker, so callers see a
    deterministic order.
    """

    @abc.abstractmethod
    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        """Every filing of every issuer CIK in the index, all their forms.

        An issuer CIK has at least one `master.issuer_forms` filing other
        than a Form 25 or 25-NSE; other filers (insiders, funds, exchanges)
        are dropped. A 25-NSE sits under its subject company only."""

    @abc.abstractmethod
    def companies_snapshot(self) -> list[CompanySnapshotEntry]:
        """The current companies/tickers/exchanges snapshot."""

    @abc.abstractmethod
    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        """XBRL facts named in `names` for `cik`."""

    @abc.abstractmethod
    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        """SGML headers of `cik`'s filings whose form is in `forms`."""

    @abc.abstractmethod
    def cover_pages(self, cik: str) -> list[CoverPage]:
        """Cover pages of `cik`'s periodic filings (iXBRL, ~2019 onward)."""

    @abc.abstractmethod
    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        """Every Form 25 and 25-NSE."""

    @abc.abstractmethod
    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        """As-filed statement facts (amendment 2026-10-03, #660) for
        `cik`: one entry per tag occurrence the companyfacts payload
        carries for the configured statement names, reported values
        only. The ingest (T77b) keeps the first vintage of each key,
        applies the hold rule on an unstamped carrier, and derives a
        missing `gross_profit` from the stored `revenue` and
        `cost_of_revenue` rows; none of that lives here."""

    def filing_events(self, cik: str) -> list[FilingEvent]:
        """Filing events (amendment 2026-10-09, #1358) for `cik`: one
        `FilingEvent` per stamped accession whose form is in
        `edgar.event_forms` and whose `items` is known, sorted by
        `accepted_at`, then accession.

        Not abstract, unlike every question above: the EDGAR adapter's
        answer is T164c's and the ingest's recording proxy is T164e's, and
        the ingest asks it only while `edgar.filing_events_enabled` is on
        (default off), so an adapter without an answer refuses loudly here
        rather than answering an empty list."""
        raise NotImplementedError(
            f"{type(self).__name__} does not answer filing_events yet (#1358: the EDGAR "
            "answer is T164c, the ingest proxy T164e)"
        )

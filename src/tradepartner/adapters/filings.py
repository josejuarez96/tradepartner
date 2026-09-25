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

"""The EDGAR `FilingSource` (spec req 6, plan T11b; T11c adds the per-CIK methods).

Every fetch goes through `edgar_raw` (throttle, retry, `User-Agent`) and
every payload through the `edgar` parsers; this module decides what to
fetch, what to keep and what to cache under `edgar.cache_dir`.

**Scale.** The full index lists about a million filers over 1993 onward, so
`filing_index` keeps only issuer CIKs: those with at least one
`master.issuer_forms` row other than a Form 25 or 25-NSE. It keeps all their
rows (classification reads the other forms) and counts the other filers on
`.skipped_filers`. The quarterly indexes are read twice (issuers first, then
their rows), so no more than one quarter's full text is parsed at a time.

**Caches.** A quarter's raw `form.idx` is cached, gzipped, only when fetched
`edgar.index_settle_days` or more after its Eastern-time end; younger
quarters are re-fetched every call. Acceptance times are cached per CIK,
one reduced record per accession, under a directory named by
`PARSER_VERSION`, so a parser fix re-stamps. A cache file that does not
decode (truncated, older version) is ignored and re-fetched. Every write
goes through a temp file and `os.replace`.

**Stamping.** A row's `known_at` is its SEC acceptance instant from the
submissions payloads, never its index filing date. Accessions not in the
cache are stamped from one `submissions.zip` download when they belong to
more than `edgar.bulk_stamp_threshold_ciks` CIKs, and per CIK otherwise
(older pages fetched only while accessions remain unstamped); the zip is
rebuilt about 03:00 ET, so after a bulk pass the CIKs still unstamped get
the per-CIK fetch. A row still unstamped is excluded and reported on
`.unstamped_filings`; it is cached as unstampable (and not re-fetched) only
when its quarter had settled before the per-CIK fetch that lacked it.
"""

from __future__ import annotations

import gzip
import json
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import (
    UnstampedFiling,
    acceptance_times,
    parse_company_tickers,
    parse_filing_index,
)
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
)
from tradepartner.config import Settings
from tradepartner.timeutil import ensure_tz_aware_utc

#: Bumped when a parser change must re-stamp every cached accession.
PARSER_VERSION = 1

_EASTERN = ZoneInfo("America/New_York")
_DELISTING_FORMS = frozenset({"25", "25/A", "25-NSE", "25-NSE/A"})

Quarter = tuple[int, int]


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    """One filing reduced from a submissions payload. `accepted_at` is `None`
    only for an accession cached as unstampable."""

    accession: str
    form: str
    primary_document: str
    inline_xbrl: bool
    accepted_at: datetime | None


@dataclass
class _Submissions:
    """A CIK's submissions fetched so far in this instance (memoised for T11c)."""

    records: dict[str, SubmissionRecord]
    pages: list[str]
    fetched_at: datetime


def reduce_submissions(payload: Mapping[str, Any]) -> tuple[dict[str, SubmissionRecord], list[str]]:
    """Accession -> reduced record for a submissions payload or an older page,
    and the names of the older pages it lists. A malformed payload raises
    `ValueError`."""
    try:
        filings = payload.get("filings")
        columns = filings["recent"] if isinstance(filings, Mapping) else payload
        pages = [str(f["name"]) for f in filings.get("files", [])] if filings else []
        times = acceptance_times(payload)
        records = {
            accession: SubmissionRecord(accession, form, str(doc), bool(ixbrl), times[accession])
            for accession, form, doc, ixbrl in zip(
                columns["accessionNumber"],
                columns["form"],
                columns["primaryDocument"],
                columns["isInlineXBRL"],
                strict=True,
            )
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"submissions payload: malformed: {error!r}") from error
    return records, pages


def quarter_of(at: datetime) -> Quarter:
    """The calendar quarter of `at` in Eastern time (EDGAR's index quarters)."""
    eastern = at.astimezone(_EASTERN)
    return eastern.year, (eastern.month - 1) // 3 + 1


class EdgarFilingSource(FilingSource):
    """Filing records from SEC EDGAR. `client` and `clock` are injectable for
    tests; `.requests` counts the HTTP requests made through the client."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.edgar.request_timeout_seconds)
        hooks = self._client.event_hooks
        hooks["request"] = [*hooks.get("request", []), self._count]
        self._client.event_hooks = hooks
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache = Path(settings.edgar.cache_dir)
        self._submissions: dict[str, _Submissions] = {}
        self._open_quarters: dict[Quarter, str] = {}
        self.requests = 0
        self.skipped_filers = 0
        self.unstamped_filings: list[UnstampedFiling] = []

    def _count(self, request: httpx.Request) -> None:
        self.requests += 1

    def _now(self) -> datetime:
        return ensure_tz_aware_utc(self._clock(), field_name="clock()")

    # --- the filing index --------------------------------------------------

    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        """Rows of issuer CIKs (all their forms), stamped with acceptance
        times; unstamped rows go to `.unstamped_filings`, never the result."""
        if since is not None:
            since = ensure_tz_aware_utc(since, field_name="since")
        self.unstamped_filings, self._open_quarters = [], {}
        start = (self._settings.edgar.index_first_year, 1)
        last = quarter_of(self._now())
        quarters = [(y, q) for y in range(start[0], last[0] + 1) for q in range(1, 5)]
        quarters = [q for q in quarters if start <= q <= last]
        issuer_forms = set(self._settings.master.issuer_forms) - _DELISTING_FORMS
        filers: set[str] = set()
        issuers: set[str] = set()
        delisting_ciks: dict[str, set[str]] = {}
        for _, row in self._rows(quarters, last):
            filers.add(row.cik)
            if row.form in issuer_forms:
                issuers.add(row.cik)
            if row.form in _DELISTING_FORMS:
                delisting_ciks.setdefault(row.accession, set()).add(row.cik)
        self.skipped_filers = len(filers - issuers)

        kept: dict[str, dict[str, tuple[UnstampedFiling, Quarter]]] = {}
        for quarter, row in self._rows(quarters, last):
            if row.cik not in issuers:
                continue
            if (
                row.form in _DELISTING_FORMS
                and int(row.accession[:10]) == int(row.cik)
                and len(delisting_ciks[row.accession]) > 1
            ):
                continue  # the exchange's copy of a 25-NSE; the subject company keeps it
            kept.setdefault(row.cik, {}).setdefault(row.accession, (row, quarter))

        stamps = self._stamp(kept)
        entries: list[FilingIndexEntry] = []
        for cik, rows in kept.items():
            for accession, (row, _) in rows.items():
                record = stamps[cik].get(accession)
                if record is None or record.accepted_at is None:
                    self.unstamped_filings.append(row)
                elif since is None or record.accepted_at >= since:
                    at = record.accepted_at
                    entries.append(FilingIndexEntry(cik, row.company_name, row.form, accession, at))
        entries.sort(key=lambda e: (e.accepted_at, e.accession, e.cik))
        self.unstamped_filings.sort(key=lambda r: (r.filed_on, r.accession, r.cik))
        return entries

    def _rows(
        self, quarters: Sequence[Quarter], open_quarter: Quarter
    ) -> Iterator[tuple[Quarter, UnstampedFiling]]:
        """Every row of every quarter's index, undated (the parser is given no
        acceptance times, so it returns each row with its filing date only)."""
        for quarter in quarters:
            for row in parse_filing_index(self._index_text(quarter, open_quarter), {}).unstamped:
                yield quarter, row

    def _index_text(self, quarter: Quarter, open_quarter: Quarter) -> str:
        if quarter in self._open_quarters:
            return self._open_quarters[quarter]
        path = self._cache / "index" / f"{quarter[0]}-QTR{quarter[1]}.idx.gz"
        try:
            return gzip.decompress(path.read_bytes()).decode("utf-8")
        except (OSError, EOFError, UnicodeDecodeError, zlib.error):
            pass  # absent, truncated or corrupt: fetch it again
        try:
            text = edgar_raw.filing_index_quarter(
                *quarter, settings=self._settings, client=self._client
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404 or quarter != open_quarter:
                raise
            text = ""  # the open quarter's index may not exist yet
        if self._settled(quarter, self._now()):
            edgar_raw.write_atomic(path, gzip.compress(text.encode("utf-8"), mtime=0))
        else:
            self._open_quarters[quarter] = text
        return text

    def _settled(self, quarter: Quarter, at: datetime) -> bool:
        """Whether `at` is `edgar.index_settle_days` past the quarter's Eastern end."""
        year, qtr = quarter
        end = datetime(year + qtr // 4, qtr % 4 * 3 + 1, 1, tzinfo=_EASTERN)
        return at >= end + timedelta(days=self._settings.edgar.index_settle_days)

    # --- stamping -----------------------------------------------------------

    def _stamp(
        self, kept: Mapping[str, Mapping[str, tuple[UnstampedFiling, Quarter]]]
    ) -> dict[str, dict[str, SubmissionRecord]]:
        stamps = {cik: self._load_stamps(cik) for cik in kept}
        pending = {cik: {a for a in rows if a not in stamps[cik]} for cik, rows in kept.items()}
        pending = {cik: wanted for cik, wanted in pending.items() if wanted}
        if len(pending) > self._settings.edgar.bulk_stamp_threshold_ciks:
            self._stamp_bulk(pending, stamps)
        for cik, wanted in pending.items():
            wanted -= stamps[cik].keys()
            if not wanted:
                continue
            submissions = self._fetch_submissions(cik, wanted)
            new = {a: submissions.records[a] for a in wanted if a in submissions.records}
            for accession in wanted - new.keys():
                row, quarter = kept[cik][accession]
                if self._settled(quarter, submissions.fetched_at):
                    new[accession] = SubmissionRecord(accession, row.form, "", False, None)
            if new:
                stamps[cik].update(new)
                self._save_stamps(cik, stamps[cik])
        return stamps

    def _fetch_submissions(self, cik: str, wanted: set[str]) -> _Submissions:
        """`cik`'s submissions, memoised; older pages fetched while any of
        `wanted` is still missing."""
        memo = self._submissions.get(cik)
        if memo is None:
            payload = edgar_raw.submissions(cik, settings=self._settings, client=self._client)
            records, pages = reduce_submissions(payload)
            memo = self._submissions[cik] = _Submissions(records, pages, self._now())
        while memo.pages and not wanted <= memo.records.keys():
            page = edgar_raw.submissions_page(
                memo.pages.pop(0), settings=self._settings, client=self._client
            )
            memo.records.update(reduce_submissions(page)[0])
        return memo

    def _stamp_bulk(
        self, pending: Mapping[str, set[str]], stamps: dict[str, dict[str, SubmissionRecord]]
    ) -> None:
        """Stamp from `submissions.zip`; never marks anything unstampable (the
        zip trails the day's filings)."""
        path = edgar_raw.bulk_submissions(settings=self._settings, client=self._client)
        with zipfile.ZipFile(path) as bulk:
            names = set(bulk.namelist())
            for cik, wanted in pending.items():
                if f"CIK{cik}.json" not in names:
                    continue
                records, pages = reduce_submissions(json.loads(bulk.read(f"CIK{cik}.json")))
                for page in pages:
                    if wanted <= records.keys() or page not in names:
                        break
                    records.update(reduce_submissions(json.loads(bulk.read(page)))[0])
                new = {a: records[a] for a in wanted if a in records}
                if new:
                    stamps[cik].update(new)
                    self._save_stamps(cik, stamps[cik])

    def _stamps_path(self, cik: str) -> Path:
        return self._cache / "stamps" / f"v{PARSER_VERSION}" / f"{cik}.json"

    def _load_stamps(self, cik: str) -> dict[str, SubmissionRecord]:
        try:
            data = json.loads(self._stamps_path(cik).read_bytes())
            if data["version"] != PARSER_VERSION or data["cik"] != cik:
                return {}
            return {
                accession: SubmissionRecord(
                    accession,
                    form,
                    doc,
                    ixbrl,
                    ensure_tz_aware_utc(datetime.fromisoformat(at), field_name="stamp")
                    if at
                    else None,
                )
                for accession, (form, doc, ixbrl, at) in data["records"].items()
            }
        except (OSError, ValueError, KeyError, TypeError):
            return {}  # absent, truncated or another layout: stamp again

    def _save_stamps(self, cik: str, records: Mapping[str, SubmissionRecord]) -> None:
        data = {
            "version": PARSER_VERSION,
            "cik": cik,
            "records": {
                r.accession: [
                    r.form,
                    r.primary_document,
                    r.inline_xbrl,
                    r.accepted_at.isoformat() if r.accepted_at else None,
                ]
                for r in records.values()
            },
        }
        edgar_raw.write_atomic(self._stamps_path(cik), json.dumps(data).encode("utf-8"))

    # --- the companies snapshot ---------------------------------------------

    def companies_snapshot(self) -> list[CompanySnapshotEntry]:
        """`company_tickers_exchange.json`, known at the clock read after the
        response arrived."""
        payload = edgar_raw.company_tickers(settings=self._settings, client=self._client)
        entries = parse_company_tickers(payload, self._now())
        return sorted(entries, key=lambda e: (e.fetched_at, e.ticker, e.cik))

    # --- T11c ----------------------------------------------------------------

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        _t11c()

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        _t11c()

    def cover_pages(self, cik: str) -> list[CoverPage]:
        _t11c()

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        _t11c()


def _t11c() -> NoReturn:
    raise NotImplementedError("T11c")

"""The EDGAR `FilingSource` (spec req 6, plan T11b; T11c adds FSN fetch and
parse; T11d serves `cover_pages`/`filing_headers` from it).

Every fetch goes through `edgar_raw` (throttle, retry, `User-Agent`) and
every payload through the `edgar` parsers; this module decides what to
fetch, what to keep and what to cache under `edgar.cache_dir`.

**Scale.** The full index lists about a million filers over 1993 onward, so
`filing_index` keeps only issuer CIKs: those with at least one
`master.issuer_forms` row other than a Form 25 or 25-NSE, or named as the
subject (not the filer) of a Form 25 or 25-NSE, so a company seen only
through its delisting is not lost. It keeps all their rows (classification
reads the other forms) and counts the other filers on `.skipped_filers`.
The exchange's own copy of a 25-NSE is dropped; a Form 25, which the issuer
files itself, stays under every CIK that lists it. The quarterly indexes
are read twice (issuers first, then their rows), so no more than one
quarter's full text is parsed at a time.

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

**Cover pages and headers (T11d).** `cover_pages` and `filing_headers` serve
the FSN caches T11c built, each row re-stamped at read time from
`_load_stamps(cik)`, never from FSN's own dates or a document's own
`ACCEPTANCE-DATETIME`. What FSN does not (yet) hold is covered per
document: a cover-page accession gets one iXBRL parse when it is inline
XBRL, its base form is in `edgar.cover_page_forms` and it was accepted on
or after the lag window's start (the first day of the newest cached FSN
period); an older such accession absent from FSN is not fetched and is
counted on `.fsn_missing`, unless its FSN extraction itself failed (that
is T11f's `.failed_filings` to count). A header gets a ranged
`filing_sgml_header` fetch when its accession is absent from FSN and
either its base form is a registration form (`S-1`, `F-1`, `10-12B`)
accepted on or after `edgar.header_start_year`, or it is accepted inside
the lag window (periodic forms and 8-K); an FSN header with a blank SIC
inside the lag window also gets one, so a de-SPAC's new SIC still
arrives with its 8-K. Each per-document result is cached by accession,
its stamp stripped, under its own version constant (`COVER_VERSION`,
`HEADER_VERSION`): a cached entry always wins over FSN for its accession,
until the version bumps or the cache is cleared.

**Facts (T11e).** `facts` joins two sources, both stamped at read time from
`_load_stamps(cik)`: company facts (`companyfacts.zip` above the stamping
threshold, the per-CIK API below it; parsed facts cached per CIK under
`PARSER_VERSION` and the CIK's latest stamped cover-form accession, so a
10-K/A invalidates and an 8-K does not) and the per-class shares of the
CIK's cover pages, walked with exactly `cover_pages`'s precedence. An FSN
share is dated by the company-facts `end` of its accession when there is
one, else `min(ddate, acceptance date in New York)`, because FSN's `ddate`
is a rounded month end (owner decision 2026-09-26, #242); a per-document
record keeps its cover date. Records are de-duplicated across sources on
(accession, fact name, class member), the winner keeping its own dates; two
sources agreeing on no value raise `ValueError` until T11f's policy. The
store's `facts_as_of` serves one row per (security, fact name, class,
accession), the latest ingested, so a re-dated share never appears twice.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import re
import shutil
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import duckdb
import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import (
    CoverPageParse,
    FsnFiling,
    FsnShare,
    UnstampedFiling,
    acceptance_times,
    parse_company_facts,
    parse_company_tickers,
    parse_cover_page,
    parse_filing_index,
    parse_fsn,
    parse_sgml_header,
)
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
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
#: Bumped when a parser change must re-extract every cached FSN period.
#: Separate from `PARSER_VERSION`, which stays the per-CIK stamps' key: a
#: bump here re-downloads every period's zip (the PR states that cost).
FSN_VERSION = 1
#: Bumped when `parse_cover_page` changes and every per-document cover-page
#: parse (T11d) must be re-fetched and re-parsed. Deleting `edgar.cache_dir`
#: or bumping this switches a per-document accession back to its FSN row.
COVER_VERSION = 1
#: As `COVER_VERSION`, for per-document `parse_sgml_header` results (T11d).
HEADER_VERSION = 1

#: Registration forms (T11d): a ranged header is requested from
#: `edgar.header_start_year`, unlike periodic forms and 8-K, which only get
#: one inside the lag window.
_REGISTRATION_FORMS = frozenset({"S-1", "F-1", "10-12B"})

#: The four `dei` cover-page concepts FSN's `num.tsv`/`txt.tsv` are filtered
#: to while streaming each zip member (module docstring "Caches").
_FSN_SHARES_TAG = "EntityCommonStockSharesOutstanding"
_FSN_LISTING_TAGS = ("Security12bTitle", "TradingSymbol", "SecurityExchangeName")
_FSN_MEMBERS = ("sub.tsv", "num.tsv", "txt.tsv", "dim.tsv")

_EASTERN = ZoneInfo("America/New_York")
_DELISTING_FORMS = frozenset({"25", "25/A", "25-NSE", "25-NSE/A"})
#: Filed by the exchange, so the filer's own copy is not a delisting of the filer.
_EXCHANGE_FORMS = frozenset({"25-NSE", "25-NSE/A"})

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
        self.fsn_duplicates = 0
        self.fsn_reissued = 0
        self.fsn_reissue_undetected = 0
        self.fsn_incomplete_listings = 0
        self.fsn_missing = 0
        self._filing_index_ran = False
        self._fsn_ready = False
        self._fsn_failed_accessions: frozenset[str] = frozenset()
        # Every accession FSN extracted, under whichever CIK it keys the filing
        # to, and the in-range periods whose manifests load (the lag window).
        self._fsn_extracted_accessions: frozenset[str] = frozenset()
        self._fsn_loaded_periods: tuple[str, ...] = ()
        # T11e: `companyfacts.zip` path once downloaded, False once per-CIK was chosen.
        self._facts_bulk: Path | bool | None = None

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
                if not _filed_by(row):
                    issuers.add(row.cik)  # the subject of a delisting
        self.skipped_filers = len(filers - issuers)

        kept: dict[str, dict[str, tuple[UnstampedFiling, Quarter]]] = {}
        for quarter, row in self._rows(quarters, last):
            if row.cik not in issuers:
                continue
            if (
                row.form in _EXCHANGE_FORMS
                and _filed_by(row)
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
        self._filing_index_ran = True
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

    # --- FSN data sets (T11c) -------------------------------------------------

    def _ensure_fsn(self) -> None:
        """Runs once per instance, on the first `cover_pages`, `facts` or
        `filing_headers` call (T11d/T11e wire those in); raises if
        `filing_index` has not run yet.

        Extracts every FSN period from `edgar.fsn_first_year` with no
        extraction cache yet, oldest first, keeping every accession whose
        base form is in `edgar.cover_page_forms` or `edgar.header_forms`. The
        issuer filter is never applied here, so a CIK that becomes an
        issuer later keeps its earlier rows.

        Kept rows are regrouped into per-CIK cache files
        (`edgar.cache_dir/fsn/v{FSN_VERSION}/{cik}.json`), like the stamps
        cache. Each extracted period gets a manifest recording the zip's
        content hash, its validators, the accessions extracted and the
        accessions that failed extraction (error class, base form,
        `accepted: False`), and `committed: False` until T11f's
        `record_failures()` sets it. An accession seen in two periods keeps
        the first extracted; the duplicate is counted on `.fsn_duplicates`.
        A period is re-extracted only when `FSN_VERSION` changes.

        For periods already extracted (a manifest exists) and still listed
        on the data-set page, one `HEAD` compares their validators with the
        manifest: a change is a re-issue, a cached period no longer listed
        is a roll-up; both are counted on `.fsn_reissued` and never
        re-extracted. A period with no usable validator (the `HEAD` fails, or
        neither run has an `ETag` or `Last-Modified`) is counted on
        `.fsn_reissue_undetected`, so the run message says its re-issues went
        unchecked; it is never counted as a re-issue.
        """
        if self._fsn_ready:
            return
        if not self._filing_index_ran:
            raise RuntimeError("_ensure_fsn: filing_index() must run first")
        self.fsn_duplicates = 0
        self.fsn_reissued = 0
        self.fsn_reissue_undetected = 0
        self.fsn_incomplete_listings = 0
        self.fsn_missing = 0

        all_periods = edgar_raw.fsn_periods(settings=self._settings, client=self._client)
        listed = set(all_periods)
        wanted = [
            p
            for p in all_periods
            if edgar_raw.fsn_period_year(p) >= self._settings.edgar.fsn_first_year
        ]

        known_accessions: set[str] = set()
        to_extract: list[str] = []
        for period in wanted:
            manifest = self._load_fsn_manifest(period)
            if manifest is None:
                to_extract.append(period)
            else:
                known_accessions.update(manifest["accessions_extracted"])

        kept_forms = {*self._settings.edgar.cover_page_forms, *self._settings.edgar.header_forms}
        for period in to_extract:
            self._extract_fsn_period(period, kept_forms, known_accessions)

        for period in self._cached_fsn_periods():
            if edgar_raw.fsn_period_year(period) < self._settings.edgar.fsn_first_year:
                continue
            if period in to_extract:
                continue
            if period not in listed:
                self.fsn_reissued += 1  # rolled up: cached but no longer listed
                continue
            manifest = self._load_fsn_manifest(period)
            if manifest is None:
                continue
            try:
                headers = edgar_raw.fsn_validators(
                    period, settings=self._settings, client=self._client
                )
            except (httpx.HTTPError, edgar_raw.RetryAfterTooLargeError):
                # HTTP status or network failure: unchecked this run, never
                # fatal. A missing User-Agent (EdgarCredentialsError) still raises.
                self.fsn_reissue_undetected += 1
                continue
            now, then = _fsn_validators_dict(headers), manifest.get("validators") or {}
            if not _usable_validators(now) or not _usable_validators(then):
                self.fsn_reissue_undetected += 1
            elif now != then:
                self.fsn_reissued += 1  # re-issued: same period, new content

        failed: set[str] = set()
        extracted: set[str] = set()
        loaded: list[str] = []
        for period in self._cached_fsn_periods():
            if edgar_raw.fsn_period_year(period) < self._settings.edgar.fsn_first_year:
                continue
            manifest = self._load_fsn_manifest(period)
            if manifest is not None:
                failed.update(f["accession"] for f in manifest.get("accessions_failed", []))
                extracted.update(manifest.get("accessions_extracted", []))
                loaded.append(period)
        self._fsn_failed_accessions = frozenset(failed)
        self._fsn_extracted_accessions = frozenset(extracted)
        self._fsn_loaded_periods = tuple(loaded)

        self._fsn_ready = True

    def _extract_fsn_period(
        self, period: str, kept_forms: set[str], known_accessions: set[str]
    ) -> None:
        zip_path, headers = edgar_raw.fsn_zip(period, settings=self._settings, client=self._client)
        with zip_path.open("rb") as zip_file:  # streamed: FSN zips reach hundreds of MB
            content_hash = hashlib.file_digest(zip_file, "sha256").hexdigest()
        extract_dir = self._cache / "fsn" / "_extract" / period
        try:
            members = _fsn_extract(zip_path, extract_dir)
            sub_rows = _fsn_rows(members["sub.tsv"], ("adsh", "cik", "sic", "form"))
            num_rows = _fsn_rows(
                members["num.tsv"],
                ("adsh", "tag", "ddate", "dimh", "dimn", "coreg", "value"),
                where=f"tag = '{_FSN_SHARES_TAG}'",
            )
            listing_tags = ", ".join(f"'{tag}'" for tag in _FSN_LISTING_TAGS)
            txt_rows = _fsn_rows(
                members["txt.tsv"],
                ("adsh", "tag", "dimh", "dimn", "coreg", "value"),
                where=f"tag IN ({listing_tags})",
            )
            # Only the dimensions a kept row uses: a quarterly dim.tsv is
            # millions of rows, the largest part of peak memory otherwise.
            dimhashes = sorted(
                {str(row["dimh"]) for row in (*num_rows, *txt_rows) if row.get("dimh")}
            )
            dim_rows = (
                _fsn_rows(
                    members["dim.tsv"],
                    ("dimhash", "segments"),
                    where="list_contains(?, dimhash)",
                    params=[dimhashes],
                )
                if dimhashes
                else []
            )
            parsed = parse_fsn(sub_rows, num_rows, txt_rows, dim_rows)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)  # the zip is deleted after extraction

        form_by_accession = {str(row["adsh"]): str(row["form"]) for row in sub_rows}
        per_cik_new: dict[str, dict[str, FsnFiling]] = {}
        served: list[str] = []
        incomplete = 0
        for record in parsed.records:
            if record.accession in known_accessions:
                self.fsn_duplicates += 1
                continue
            known_accessions.add(record.accession)
            if record.form.removesuffix("/A") in kept_forms:
                per_cik_new.setdefault(record.cik, {})[record.accession] = record
                served.append(record.accession)
                incomplete += record.incomplete_listings
        self.fsn_incomplete_listings += incomplete
        for cik, records in per_cik_new.items():
            existing = self._load_fsn_cache(cik, strict=True)
            existing.update(records)
            self._save_fsn_cache(cik, existing)

        manifest = {
            "version": FSN_VERSION,
            "period": period,
            "content_hash": content_hash,
            "validators": _fsn_validators_dict(headers),
            "accessions_extracted": sorted({r.accession for r in parsed.records}),
            # T11f: served-form accessions (the failure-share denominator with
            # `accessions_failed`) and the period's skipped incomplete listings.
            "accessions_served": sorted(served),
            "incomplete_listings": incomplete,
            "accessions_failed": [
                {
                    "accession": f.accession,
                    "error_class": f.error_class,
                    "base_form": form_by_accession.get(f.accession, "").removesuffix("/A"),
                    "accepted": False,
                }
                for f in parsed.failures
                # only forms this adapter serves count toward T11f's failure policy
                if form_by_accession.get(f.accession, "").removesuffix("/A") in kept_forms
            ],
            "committed": False,
        }
        self._save_fsn_manifest(period, manifest)

    def _fsn_root(self) -> Path:
        return self._cache / "fsn" / f"v{FSN_VERSION}"

    def _fsn_cache_path(self, cik: str) -> Path:
        return self._fsn_root() / f"{cik}.json"

    def _load_fsn_cache(self, cik: str, *, strict: bool = False) -> dict[str, FsnFiling]:
        """The CIK's cached FSN records; `{}` when there is no file. A file
        that exists but does not load reads as `{}` too, unless `strict`: the
        extraction path must not overwrite it, since its periods have
        manifests and would never be extracted again."""
        path = self._fsn_cache_path(cik)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict):
                raise TypeError("not an object")
            if data.get("version") != FSN_VERSION or data.get("cik") != cik:
                raise ValueError("another version or CIK")
            return {
                accession: _fsn_filing_from_json(accession, cik, record)
                for accession, record in data["records"].items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if strict:
                raise ValueError(
                    f"FSN cache {path} is unreadable ({type(exc).__name__}); delete it and "
                    f"every manifest under {self._fsn_root() / 'manifests'} to re-extract"
                ) from exc
            return {}

    def _save_fsn_cache(self, cik: str, records: Mapping[str, FsnFiling]) -> None:
        data = {
            "version": FSN_VERSION,
            "cik": cik,
            "records": {accession: _fsn_filing_to_json(r) for accession, r in records.items()},
        }
        edgar_raw.write_atomic(self._fsn_cache_path(cik), json.dumps(data).encode("utf-8"))

    def _fsn_manifest_path(self, period: str) -> Path:
        return self._fsn_root() / "manifests" / f"{period}.json"

    def _load_fsn_manifest(self, period: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._fsn_manifest_path(period).read_bytes())
            if not isinstance(data, dict):
                return None
            if data.get("version") != FSN_VERSION or data.get("period") != period:
                return None
            return data
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # absent, truncated or another layout: extract again

    def _save_fsn_manifest(self, period: str, manifest: dict[str, Any]) -> None:
        edgar_raw.write_atomic(
            self._fsn_manifest_path(period), json.dumps(manifest).encode("utf-8")
        )

    def _cached_fsn_periods(self) -> list[str]:
        try:
            stems = [path.stem for path in (self._fsn_root() / "manifests").glob("*.json")]
            return [stem for stem in stems if edgar_raw.is_fsn_period(stem)]
        except OSError:
            return []

    def _lag_window_start(self) -> datetime:
        """The first day (Eastern midnight, as UTC) of the newest cached FSN
        period at or after `edgar.fsn_first_year`. With no such period
        (e.g. `fsn_first_year` past the newest listed period) it raises
        rather than treating all history as the lag window."""
        periods = self._fsn_loaded_periods  # in range, and the manifest loads
        if not periods:
            # Fail closed: "everything is inside the lag window" would request
            # a header for every 8-K and 10-Q since 1993 (#249 safety review).
            raise RuntimeError(
                "no cached FSN period at or after edgar.fsn_first_year="
                f"{self._settings.edgar.fsn_first_year}; is it later than the newest period?"
            )
        newest = max(periods, key=_fsn_period_sort_key)
        start = _fsn_period_start(newest)
        return datetime(start.year, start.month, start.day, tzinfo=_EASTERN).astimezone(UTC)

    # --- cover pages and headers from FSN (T11d) -----------------------------

    def cover_pages(self, cik: str) -> list[CoverPage]:
        """FSN cover pages of `cik` (stamped at read time), plus a per-document
        parse for lag-window accessions FSN does not (yet) hold. See the
        module docstring's "Cover pages and headers" section."""
        pages = [
            CoverPage(cik, record.accession, record.accepted_at, payload.listings)
            for record, payload in self._cover_sources(cik, self._load_stamps(cik))
            if record.accepted_at is not None
        ]
        pages.sort(key=lambda p: (p.accepted_at, p.accession))
        return pages

    def _cover_sources(
        self, cik: str, stamps: Mapping[str, SubmissionRecord], *, count_missing: bool = True
    ) -> Iterator[tuple[SubmissionRecord, _CachedCoverPage | FsnFiling]]:
        """The one precedence walk behind `cover_pages` and `facts`: for each
        stamped cover-form accession of `cik`, its per-document cache entry
        when present (a co-registrant's copy of another entity's page is
        skipped), else its FSN row, else a lag-window per-document parse; an
        older accession absent from FSN is counted on `.fsn_missing` once,
        by `cover_pages` (`count_missing=False` for `facts`)."""
        self._ensure_fsn()
        fsn_cache = self._load_fsn_cache(cik)
        cover_forms = set(self._settings.edgar.cover_page_forms)
        lag_start = self._lag_window_start()
        for accession, record in stamps.items():
            if record.accepted_at is None:
                continue  # unstampable: already reported on .unstamped_filings
            if record.form.removesuffix("/A") not in cover_forms:
                continue
            cached = self._load_cover_cache(accession)
            if cached is not None:
                if cached.entity_cik == cik:  # else a co-registrant's copy: not its page
                    yield record, cached
                continue
            fsn_filing = fsn_cache.get(accession)
            if fsn_filing is not None:
                yield record, fsn_filing
                continue
            if accession in self._fsn_extracted_accessions:
                continue  # FSN holds it under the filer's CIK: this CIK is a co-registrant
            if not record.inline_xbrl:
                continue
            if record.accepted_at >= lag_start:
                parsed = self._fetch_cover_page(
                    cik, accession, record.primary_document, record.accepted_at
                )
                if parsed.cover.cik == cik:  # a combined filing names one entity
                    yield record, _CachedCoverPage(cik, parsed.cover.listings, _strip(parsed.facts))
            elif count_missing and accession not in self._fsn_failed_accessions:
                self.fsn_missing += 1

    def _fetch_cover_page(
        self, cik: str, accession: str, primary_document: str, accepted_at: datetime
    ) -> CoverPageParse:
        # The root copy, never an `xsl.../` rendering of it (plan T11d).
        root_document = re.sub(r"^xsl[^/]*/", "", primary_document)
        path = edgar_raw.download_filing_file(
            cik, accession, root_document, settings=self._settings, client=self._client
        )
        try:
            document = path.read_bytes()
            parsed = parse_cover_page(document, accession=accession, accepted_at=accepted_at)
        finally:
            path.unlink(missing_ok=True)  # the document is deleted after parsing
        self._save_cover_cache(accession, cik, parsed)
        return parsed

    def _cover_cache_path(self, accession: str) -> Path:
        # One entry per accession: `parse_cover_page` does not depend on the
        # CIK asking. The entry records the entity it names, and only that CIK
        # is served its listings (co-registrants of a combined filing are not).
        edgar_raw.validate_accession(accession)
        return self._cache / "cover" / f"v{COVER_VERSION}" / f"{accession}.json"

    def _load_cover_cache(self, accession: str) -> _CachedCoverPage | None:
        try:
            data = json.loads(self._cover_cache_path(accession).read_bytes())
            if data.get("version") != COVER_VERSION or data.get("accession") != accession:
                return None
            return _CachedCoverPage(
                entity_cik=str(data["entity_cik"]),
                listings=tuple(CoverListing(*item) for item in data["listings"]),
                facts=tuple(_fact_from_json(f) for f in data["facts"]),
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # absent, truncated or another version: fetch again

    def _save_cover_cache(self, accession: str, cik: str, parsed: CoverPageParse) -> None:
        data = {
            "version": COVER_VERSION,
            "accession": accession,
            "cik": cik,
            "entity_cik": parsed.cover.cik,
            "listings": [
                [item.title, item.ticker, item.exchange] for item in parsed.cover.listings
            ],
            "facts": [_fact_to_json(f) for f in parsed.facts],
        }
        edgar_raw.write_atomic(self._cover_cache_path(accession), json.dumps(data).encode("utf-8"))

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        """SIC headers of `cik`'s filings whose base form is in `forms`: from
        FSN with no request, plus a ranged SGML header where the module
        docstring's "Cover pages and headers" section says one is owed."""
        self._ensure_fsn()
        stamps = self._load_stamps(cik)
        fsn_cache = self._load_fsn_cache(cik)
        wanted = set(forms)
        lag_start = self._lag_window_start()
        header_start = datetime(
            self._settings.edgar.header_start_year, 1, 1, tzinfo=_EASTERN
        ).astimezone(UTC)

        headers: list[FilingHeader] = []
        for accession, record in stamps.items():
            if record.accepted_at is None:
                continue
            base_form = record.form.removesuffix("/A")
            if base_form not in wanted:
                continue
            cached = self._load_header_cache(cik, accession)
            if cached is not None:
                headers.append(
                    FilingHeader(cik, accession, record.form, cached.sic, record.accepted_at)
                )
                continue
            fsn_filing = fsn_cache.get(accession)
            if fsn_filing is not None and fsn_filing.sic is not None:
                headers.append(
                    FilingHeader(cik, accession, record.form, fsn_filing.sic, record.accepted_at)
                )
                continue
            if fsn_filing is not None:
                # a blank FSN SIC (#174: FSN's blanks are 8-Ks): a lag-window
                # ranged header still brings a de-SPAC's new SIC.
                if record.accepted_at >= lag_start:
                    headers.append(
                        self._ranged_header(cik, accession, record.form, record.accepted_at)
                    )
                continue
            # absent from FSN entirely
            if base_form in _REGISTRATION_FORMS:
                if record.accepted_at >= header_start:
                    headers.append(
                        self._ranged_header(cik, accession, record.form, record.accepted_at)
                    )
            elif record.accepted_at >= lag_start:
                headers.append(self._ranged_header(cik, accession, record.form, record.accepted_at))
        headers.sort(key=lambda h: (h.accepted_at, h.accession))
        return headers

    def _ranged_header(
        self, cik: str, accession: str, form: str, accepted_at: datetime
    ) -> FilingHeader:
        cached = self._load_header_cache(cik, accession)
        if cached is None:
            text = edgar_raw.filing_sgml_header(
                cik, accession, settings=self._settings, client=self._client
            )
            parsed = parse_sgml_header(text, cik=cik)
            if parsed.accession != accession:
                raise ValueError(
                    f"SGML header requested for {accession} names accession {parsed.accession!r}"
                )
            self._save_header_cache(accession, cik, parsed.sic)
            sic = parsed.sic
        else:
            sic = cached.sic
        return FilingHeader(cik, accession, form, sic, accepted_at)

    def _header_cache_path(self, cik: str, accession: str) -> Path:
        # Keyed per CIK: a combined filing's header gives each co-registrant
        # its own FILER block's SIC (#249 safety review).
        edgar_raw.validate_accession(accession)
        return self._cache / "header" / f"v{HEADER_VERSION}" / f"{int(cik)}" / f"{accession}.json"

    def _load_header_cache(self, cik: str, accession: str) -> _CachedHeader | None:
        try:
            data = json.loads(self._header_cache_path(cik, accession).read_bytes())
            if (
                data.get("version") != HEADER_VERSION
                or data.get("accession") != accession
                or data.get("cik") != cik
            ):
                return None
            return _CachedHeader(sic=data["sic"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # absent, truncated or another version: fetch again

    def _save_header_cache(self, accession: str, cik: str, sic: int | None) -> None:
        data = {"version": HEADER_VERSION, "accession": accession, "cik": cik, "sic": sic}
        edgar_raw.write_atomic(
            self._header_cache_path(cik, accession), json.dumps(data).encode("utf-8")
        )

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        _t11f()

    # --- facts (T11e) ---------------------------------------------------------

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        """Facts named in `names` for `cik`: company facts (undimensioned, from
        `companyfacts.zip` or the per-CIK API, cached by `_facts_cache_key`)
        plus the per-class shares of the CIK's cover pages (FSN rows and
        lag-window per-document parses, the `_cover_sources` walk), every
        record stamped at read time from `_load_stamps(cik)`.

        **Dating** (owner decision 2026-09-26, #242), per accession: a
        per-document record keeps its cover date; an FSN share takes the
        company-facts `end` of the same accession and fact name when there is
        one (a cover has one date context), else
        `min(ddate, accepted_at in New York)`, so a date never falls after
        the fact became known (FSN's `ddate` is a rounded month end).

        **De-duplication across sources** on (accession, fact name, class
        member): a per-document parse wins over company facts, which win over
        FSN; every source's records keep their own `as_of_date` keys within
        the winner; the same key with different values in two sources raises
        `ValueError` (the chunk fails until T11f's policy) and serves neither.
        """
        wanted = set(names)
        stamps = self._load_stamps(cik)
        company = self._company_facts(cik, stamps, wanted)  # accession -> unstamped facts
        by_key: dict[tuple[str, str, str], dict[str, list[_CachedFact]]] = {}

        def put(accession: str, source: str, fact: _CachedFact) -> None:
            key = (accession, fact.fact_name, fact.class_member)
            by_key.setdefault(key, {}).setdefault(source, []).append(fact)

        for accession, facts in company.items():
            for fact in facts:
                put(accession, "company", fact)
        for record, payload in self._cover_sources(cik, stamps, count_missing=False):
            if isinstance(payload, _CachedCoverPage):
                for fact in payload.facts:
                    if fact.fact_name in wanted:
                        put(record.accession, "document", fact)
                continue
            if _FSN_SHARES_TAG not in wanted or record.accepted_at is None:
                continue
            ends = {
                f.as_of_date
                for f in company.get(record.accession, ())
                if f.fact_name == _FSN_SHARES_TAG
            }
            eastern = record.accepted_at.astimezone(_EASTERN).date()
            for share in payload.shares:
                dated = max(ends) if ends else min(share.as_of_date, eastern)
                put(
                    record.accession,
                    "fsn",
                    _CachedFact(_FSN_SHARES_TAG, dated, share.class_member, share.value),
                )

        out: list[FactRecord] = []
        for (accession, fact_name, member), sources in by_key.items():
            values = {source: {f.value for f in facts} for source, facts in sources.items()}
            for a, b in itertools.combinations(sorted(values), 2):
                if values[a].isdisjoint(values[b]):  # the sources agree on no value
                    raise ValueError(
                        f"{accession}: {fact_name} {member or 'undimensioned'} differs between "
                        f"{a} {sorted(values[a])} and {b} {sorted(values[b])}"
                    )
            winner = next(s for s in ("document", "company", "fsn") if s in sources)
            accepted_at = stamps[accession].accepted_at
            assert accepted_at is not None  # every source above is stamped
            for fact in {f.as_of_date: f for f in sources[winner]}.values():
                out.append(
                    FactRecord(
                        cik, fact_name, fact.as_of_date, member, fact.value, accession, accepted_at
                    )
                )
        out.sort(
            key=lambda f: (f.accepted_at, f.accession, f.fact_name, f.class_member, f.as_of_date)
        )
        return out

    def _facts_cache_key(
        self, stamps: Mapping[str, SubmissionRecord], cover_forms: set[str]
    ) -> str | None:
        """The CIK's latest stamped accession whose base form is a cover form:
        company facts are re-fetched only when it changes (a new 10-K/A
        invalidates the cache; an 8-K does not)."""
        latest = [
            r
            for r in stamps.values()
            if r.accepted_at is not None and r.form.removesuffix("/A") in cover_forms
        ]
        if not latest:
            return None
        return max(latest, key=lambda r: (r.accepted_at, r.accession)).accession

    def _company_facts(
        self, cik: str, stamps: Mapping[str, SubmissionRecord], wanted: set[str]
    ) -> dict[str, list[_CachedFact]]:
        """`cik`'s company facts named in `wanted`, per accession, stamped
        only when the accession has a stamp (an unstamped one is absent, then
        present once its stamp arrives). Cached under `PARSER_VERSION` by
        `_facts_cache_key`; a CIK with no cover-form accession makes no request."""
        key = self._facts_cache_key(stamps, set(self._settings.edgar.cover_page_forms))
        if key is None:
            return {}
        path = self._cache / "facts" / f"v{PARSER_VERSION}" / f"{cik}.json"
        cached = _load_facts_cache(path, cik, key)
        if cached is None:
            payload = self._company_facts_payload(cik)
            cached = [
                (f.fact_name, f.accession, f.as_of_date, f.value)
                for f in parse_company_facts(payload, wanted, _EveryAccession()).facts
            ]
            data = {"version": PARSER_VERSION, "cik": cik, "key": key, "facts": cached}
            edgar_raw.write_atomic(path, json.dumps(data, default=str).encode("utf-8"))
        out: dict[str, list[_CachedFact]] = {}
        for name, accession, as_of, value in cached:
            record = stamps.get(accession)
            if record is None or record.accepted_at is None or name not in wanted:
                continue
            out.setdefault(accession, []).append(_CachedFact(name, as_of, "", value))
        return out

    def _company_facts_payload(self, cik: str) -> Any:
        """The raw company-facts payload: from one `companyfacts.zip` when
        more than `edgar.bulk_stamp_threshold_ciks` stamped CIKs need a
        fetch (the stamping rule), else the per-CIK API; a CIK absent from
        the zip falls back to the API."""
        if self._facts_bulk is None:
            stale = 0
            for stamps_path in self._stamps_path("0").parent.glob("*.json"):
                other = stamps_path.stem
                key = self._facts_cache_key(
                    self._load_stamps(other), set(self._settings.edgar.cover_page_forms)
                )
                cache = self._cache / "facts" / f"v{PARSER_VERSION}" / f"{other}.json"
                if key is not None and _load_facts_cache(cache, other, key) is None:
                    stale += 1
            self._facts_bulk = (
                edgar_raw.bulk_company_facts(settings=self._settings, client=self._client)
                if stale > self._settings.edgar.bulk_stamp_threshold_ciks
                else False
            )
        if isinstance(self._facts_bulk, Path):
            with zipfile.ZipFile(self._facts_bulk) as bulk:
                if f"CIK{cik}.json" in bulk.namelist():
                    return json.loads(bulk.read(f"CIK{cik}.json"))
        return edgar_raw.company_facts(cik, settings=self._settings, client=self._client)


def _filed_by(row: UnstampedFiling) -> bool:
    """Whether `row` is listed under the CIK that submitted it (the accession prefix)."""
    return int(row.accession[:10]) == int(row.cik)


class _EveryAccession(dict[str, datetime]):
    """An `acceptance` mapping that stamps every company-facts entry with a
    placeholder, so `parse_company_facts` yields every entry for caching;
    the real stamp is applied at read time from `_load_stamps`."""

    _PLACEHOLDER = datetime(2000, 1, 1, tzinfo=UTC)

    def get(self, key: str, default: datetime | None = None) -> datetime:  # type: ignore[override]
        return self._PLACEHOLDER


_FactsCache = list[tuple[str, str, date, float]]


def _load_facts_cache(path: Path, cik: str, key: str) -> _FactsCache | None:
    try:
        data = json.loads(path.read_bytes())
        if data["version"] != PARSER_VERSION or data["cik"] != cik or data["key"] != key:
            return None
        return [
            (str(name), str(accession), date.fromisoformat(str(as_of)), float(value))
            for name, accession, as_of, value in data["facts"]
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None  # absent, truncated, another version or a new latest accession


def _strip(facts: Sequence[FactRecord]) -> tuple[_CachedFact, ...]:
    return tuple(_fact_from_json(_fact_to_json(f)) for f in facts)


def _t11f() -> NoReturn:
    raise NotImplementedError("T11f")


@dataclass(frozen=True, slots=True)
class _CachedFact:
    """One cached cover-page share fact, unstamped (T11e re-stamps it, as
    an FSN share is, never from this cache)."""

    fact_name: str
    as_of_date: date
    class_member: str
    value: float


@dataclass(frozen=True, slots=True)
class _CachedCoverPage:
    """A per-document cover-page parse cached under `COVER_VERSION`, its
    stamp stripped (T11d re-stamps from `_load_stamps` at read time).
    `entity_cik` is the CIK the cover page names."""

    entity_cik: str
    listings: tuple[CoverListing, ...]
    facts: tuple[_CachedFact, ...]


@dataclass(frozen=True, slots=True)
class _CachedHeader:
    """A per-document SGML-header SIC cached under `HEADER_VERSION`. `sic`
    itself may legitimately be `None`; the cache file's absence (not this
    type) means "fetch it"."""

    sic: int | None


def _fact_to_json(fact: FactRecord) -> list[object]:
    return [fact.fact_name, fact.as_of_date.isoformat(), fact.class_member, fact.value]


def _fact_from_json(item: Sequence[object]) -> _CachedFact:
    fact_name, as_of_date, class_member, value = item
    return _CachedFact(
        fact_name=str(fact_name),
        as_of_date=date.fromisoformat(str(as_of_date)),
        class_member=str(class_member),
        value=float(value),  # type: ignore[arg-type]
    )


def _fsn_period_start(period: str) -> date:
    """The first calendar day of an FSN period spelled `YYYYqN` or `YYYY_MM`
    (a quarter's first month)."""
    if "q" in period:
        year, qtr = period.split("q")
        return date(int(year), (int(qtr) - 1) * 3 + 1, 1)
    year, month = period.split("_")
    return date(int(year), int(month), 1)


def _fsn_period_sort_key(period: str) -> tuple[int, int, int]:
    """As `edgar_raw._fsn_period_sort_key`: a quarter ranks ahead of the
    months it may later split into, at the same first month."""
    start = _fsn_period_start(period)
    return (start.year, start.month, 0 if "q" in period else 1)


# --- FSN data sets: zip extraction and DuckDB reads (T11c) ------------------


def _fsn_extract(zip_path: Path, extract_dir: Path) -> dict[str, Path]:
    """Extract `sub.tsv`, `num.tsv`, `txt.tsv` and `dim.tsv` from `zip_path`
    into `extract_dir`; raises `ValueError` if the zip is missing one."""
    with zipfile.ZipFile(zip_path) as archive:
        names = {name.lower(): name for name in archive.namelist()}
        paths: dict[str, Path] = {}
        for member in _FSN_MEMBERS:
            real_name = names.get(member)
            if real_name is None:
                raise ValueError(f"FSN zip {zip_path.name} is missing {member}")
            paths[member] = Path(archive.extract(real_name, path=extract_dir))
    return paths


def _fsn_rows(
    path: Path,
    columns: Sequence[str],
    *,
    where: str | None = None,
    params: Sequence[object] = (),
) -> list[dict[str, str]]:
    """`columns` of `path` (a tab-separated FSN member) as plain string
    dicts, via DuckDB `read_csv` with every column read as `varchar` (FSN's
    own convention: numeric-looking columns like `cik` can have leading
    zeros truncated otherwise) and no quoting (FSN's fields are never
    quoted, and a bare `"` inside a `txt.tsv` value must not start one).
    A zero-byte member (no header row at all -- a real FSN file never is
    one, but a test fixture may be) has no columns to select and yields no
    rows rather than a DuckDB binder error."""
    if path.stat().st_size == 0:
        return []
    column_list = ", ".join(columns)
    sql = (
        f"SELECT {column_list} FROM read_csv(?, delim='\t', header=true, "
        "all_varchar=true, quote='')"
    )
    if where:
        sql += f" WHERE {where}"
    connection = duckdb.connect()
    try:
        cursor = connection.execute(sql, [str(path), *params])
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _fsn_validators_dict(headers: httpx.Headers) -> dict[str, str | None]:
    """The subset of `headers` a period's manifest compares run to run."""
    return {
        "last_modified": headers.get("Last-Modified"),
        "etag": headers.get("ETag"),
        "content_length": headers.get("Content-Length"),
    }


def _usable_validators(validators: Mapping[str, str | None]) -> bool:
    """An `ETag` or `Last-Modified`: `Content-Length` alone cannot tell a
    re-issue of the same size from the original."""
    return bool(validators.get("etag") or validators.get("last_modified"))


def _fsn_filing_to_json(filing: FsnFiling) -> dict[str, Any]:
    return {
        "form": filing.form,
        "sic": filing.sic,
        "listings": [[item.title, item.ticker, item.exchange] for item in filing.listings],
        "shares": [[s.class_member, s.value, s.as_of_date.isoformat()] for s in filing.shares],
    }


def _fsn_filing_from_json(accession: str, cik: str, data: Mapping[str, Any]) -> FsnFiling:
    return FsnFiling(
        accession=accession,
        cik=cik,
        form=data["form"],
        sic=data["sic"],
        listings=tuple(CoverListing(*listing) for listing in data["listings"]),
        shares=tuple(
            FsnShare(member, value, date.fromisoformat(as_of))
            for member, value, as_of in data["shares"]
        ),
    )

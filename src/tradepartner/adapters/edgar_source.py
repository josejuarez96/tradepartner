"""The EDGAR `FilingSource` (spec req 6, plan T11b; T11c adds the per-CIK methods).

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
"""

from __future__ import annotations

import gzip
import hashlib
import json
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
    FsnFiling,
    FsnShare,
    UnstampedFiling,
    acceptance_times,
    parse_company_tickers,
    parse_filing_index,
    parse_fsn,
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
        self._filing_index_ran = False
        self._fsn_ready = False

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
        base form is in `edgar.cover_page_forms`. T11c note: the amendment's
        full filter also names `edgar.header_forms`, a T11d config key that
        does not exist yet; T11d widens this filter once it lands. The
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
        for record in parsed.records:
            if record.accession in known_accessions:
                self.fsn_duplicates += 1
                continue
            known_accessions.add(record.accession)
            if record.form.removesuffix("/A") in kept_forms:
                per_cik_new.setdefault(record.cik, {})[record.accession] = record
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
            "accessions_failed": [
                {
                    "accession": f.accession,
                    "error_class": f.error_class,
                    "base_form": form_by_accession.get(f.accession, "").removesuffix("/A"),
                    "accepted": False,
                }
                for f in parsed.failures
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

    # --- T11c ----------------------------------------------------------------

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        _t11c()

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        _t11c()

    def cover_pages(self, cik: str) -> list[CoverPage]:
        _t11c()

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        _t11c()


def _filed_by(row: UnstampedFiling) -> bool:
    """Whether `row` is listed under the CIK that submitted it (the accession prefix)."""
    return int(row.accession[:10]) == int(row.cik)


def _t11c() -> NoReturn:
    raise NotImplementedError("T11c")


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

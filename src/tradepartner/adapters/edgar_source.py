"""The EDGAR `FilingSource` (spec req 6, plan T11b; T11c adds FSN fetch and
parse; T11d serves `cover_pages`/`filing_headers` from it; T11e adds
`facts`; T11f adds `delistings`; T11h adds the failure policy).

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

**Stamps layout and `items` (T164c, #1358).** A stamps record carries the
payload's 8-K `items` string as a fifth element under `STAMPS_LAYOUT`, a
constant separate from `PARSER_VERSION` (which also keys the facts and
statement caches, so a bump there would re-stamp every CIK). A file in the
old four-element layout is read as it is, `items` unknown (`None`) for its
accessions, and rewritten in the new layout only when the CIK is next
stamped, every existing record (`form`, document, iXBRL flag,
`accepted_at`, every `None` sentinel) written back byte-identical. A new
stamp carries `items` whatever `edgar.filing_events_enabled` says. The only
thing that fills an old accession's `items` is the **refresh**, run by
`filing_events(cik)` (asked only while the switch is on): it re-reads the
CIK's submissions once (the cached `submissions.zip` under `reuse_cached`,
otherwise the per-CIK API), writes `items` for accessions already stamped
and touches nothing else, stamps nothing, and sets the file's
`items_refreshed` flag, the once-per-CIK marker (a file migrated by the
stamping path keeps it `false`, so it is still refreshed once; a file
written for a CIK with no earlier file starts `true`). An accession still
without `items` after the refresh is terminal and counted.

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
is T11h's `.failed_filings` to count). A header gets a ranged
`filing_sgml_header` fetch when its accession is absent from FSN and
either its base form is a registration form (`S-1`, `F-1`, `10-12B`)
accepted on or after `edgar.header_start_year`, or it is accepted inside
the lag window (periodic forms and 8-K); an FSN header with a blank SIC
inside the lag window also gets one, so a de-SPAC's new SIC still
arrives with its 8-K. Each per-document result is cached by accession,
its stamp stripped, under its own version constant (`COVER_VERSION`,
`HEADER_VERSION`): a cached entry always wins over FSN for its accession,
until the version bumps or the cache is cleared. A cover parse's skipped
listings (no title or exchange, #609) are written to its cache entry and
counted on `.cover_incomplete_listings` when it is parsed, never on a cache
hit (#612), as FSN's are on `.fsn_incomplete_listings` when extracted.

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
sources disagreeing on a value, or a NaN or infinite value from any source
(#749), is a collision (T11h's policy, below). FSN
holds 4 decimal places, so a value agrees with FSN's when it is within half
a unit of the 4th place (#610 X1). A company-facts date after acceptance is
capped at the Eastern acceptance date; when that cap makes it collide with a
different value the filing reports for that date, the after-acceptance value
is dropped and counted on `.facts_capped_dropped` (#610 X2, owner
2026-10-02); with no value dated on or before acceptance it stays a
collision. The
store's `facts_as_of` serves one row per (security, fact name, class,
accession), the latest ingested, so a re-dated share never appears twice.
A company-facts 404, or a CIK the bulk zip has no file for, is not a filing
failure (many issuers have no XBRL facts): counted on `.facts_missing`, its
empty result cached the same way as a real payload. A bulk member that is an
empty object (SEC ships `{}` members, #566) is treated as absent from the
zip, counted on `.facts_bulk_empty` (`.submissions_bulk_empty` for
`submissions.zip`, where an empty older page is likewise absent). A
per-CIK companyfacts API answer of 200 `{}` (SEC's answer for those same
CIKs, #576) is SEC's "no facts", handled exactly like a 404 and also counted
on `.facts_api_empty`. A payload that carries `facts` but no `cik` (#599:
SEC serves this shape for a handful of CIKs, identically from the zip and
the API) is identified by the CIK it was requested under (the zip member
name, or the API URL), counted on `.facts_bulk_keyless`/`.facts_api_keyless`,
and its facts carry that CIK; a `cik` that is present but differs still
raises from the API and is recorded for the input-validation gate from the
zip (#578, below). A per-CIK submissions API answer of 200 `{}` (a CIK payload or an
older page) lists nothing: the rows it would stamp stay unstamped this run
and are never cached as unstampable for it, counted on
`.submissions_api_empty`. Any other malformed payload still fails the source
(the gate's list, for a zip member).

**Statement facts (T77a, #660).** Behind `edgar.statement_facts_enabled`
(off: no call, no request, no cache). `statement_facts(cik)` reads the same
company-facts payload as `facts`: a `facts` call that reads the payload
fills the statement cache from it too, while a `statement_facts` call that
reads it fills only its own (it does not know the share names asked for),
so the payload is read once per CIK per run when `facts` is asked first,
as the ingest's fetch pass asks. A payload `parse_company_facts` refuses is
recorded once and is absent for both caches. The parsed cache
`statement_facts/v{STATEMENT_VERSION}/<cik>.json` holds the entries
`parse_statement_facts` yields with no stamps (unfiltered, unstamped) and
the conflicts, keyed by the CIK's latest stamped cover-form accession (the
accession a trailing payload reached, so the next run re-parses it) and a
digest of `edgar.statement_tags` and `statement_units`. Every read stamps
from `_load_stamps`: an accession with no record is emitted with
`accepted_at = None`, form `""`; one whose form is outside
`edgar.statement_forms` is dropped; one settled unstampable is dropped and
its entries counted on `.statement_unstampable`; a stamped entry ending
after the acceptance date (New York) is dropped (the parser's malformed
rule, applied once the stamp is known); `comparative` is recomputed over
what is kept; the co-registrant filters of `_company_facts` apply. The
first call per CIK per instance counts `.statement_conflicts` and
`.statement_unstampable` and lists the conflicts once on
`.statement_conflict_keys`; a parse counts `.statement_non_usd`,
`.statement_malformed` and `.statement_none` (a payload carrying none of
the configured tags), so a cross-run cache hit adds zero to those three.
Later calls in the run are cache-file reads, never requests, and add
nothing to the counts. Conflicts are never `_record_failure`d. With
`reuse_cached` the bulk path is forced from the cached `companyfacts.zip`
with no request for it; stale statement caches count toward
`edgar.bulk_stamp_threshold_ciks` only while the switch is on.

**Failure policy (T11h, owner decision (2); #610).** A per-document
fetch/parse that raises `ValueError` (a malformed document, a fact
collision) or meets a 404/410 for the document or header itself is skipped,
not raised: `_guarded` records it in `failed_filings.json` (keyed by
`FAILURES_VERSION`) with a count of consecutive Eastern days it has failed
identically and its message (redacted, capped at `ingest.max_message_chars`;
#610 P4). The count advances at most once per Eastern day, by
`record_failures()` (the `after_commit` hook `ingest.py`/`backfill.py` call
after a committed `ok`, non-dry-run EDGAR chunk) or by
`record_failed_check()` (which `ingest._prefetch` calls when
`check_failures()` raised on a non-dry run; #610 policy 2), so failures are
recorded, quarantined and accepted even when no run can commit. An
accession failing identically `edgar.max_filing_failures` days running is
quarantined: no further request until its entry is deleted or
`FAILURES_VERSION` changes. An FSN accession whose rows failed extraction is
recorded, with its message, in its period's manifest instead
(`fsn/v{FSN_VERSION}/manifests/<period>.json`, `accessions_failed`), counted
on `.failed_filings`, never quarantined, and retried only when
`FSN_VERSION` changes. Messages are stored from #616 on: an entry recorded
before it carries none until the accession fails again (a quarantined one
never does until un-quarantined), and an FSN manifest extracted before it
none until `FSN_VERSION` changes; nothing backfills them (#629).

`check_failures()` (called by `ingest.py`'s `_prefetch`, before the lock)
raises `FilingFailuresError` when (1) the uncommitted FSN periods' failure
share, or (2) the run's per-document failure share, clears both
`edgar.min_failed_filings` and `edgar.max_failed_filing_share`, or (3) one
(error class, base form) pair has at least `edgar.min_failed_filings`
distinct non-`accepted` accessions across `failed_filings.json` and this
run's failures. The per-document denominator is every per-document
accession the run fetched or read from its per-document cache (#610 policy
1; a co-registrant's cached copy included) plus the quarantined accessions
it skipped, each of which counts as a failure unless `accepted`: quarantine
alone never excuses a failure. FSN failures are judged by rule (1)
only, never pooled into rule (3) (#610 policy 3). A fact collision withholds
only the collided (accession, fact name, class member) key, recorded under
the accession's base form, counting toward rule (3) only, never rule (2).

**Accepting a failure (the owner, by hand).** Set `"accepted": true` on its
entry and leave every other field as it is:

- a per-document failure: the accession's entry under `"entries"` in
  `edgar.cache_dir/failed_filings.json`. `accepted` holds only while the
  accession keeps failing with the same `error_class` and `message_hash`; a
  different failure resets it to `false` and is judged again. An accepted
  entry leaves the numerators of rules (2) and (3); it still counts toward
  quarantine, and a quarantined accession is excused only by `accepted`.
- an FSN failure: the accession's object in `accessions_failed` of its
  period's manifest. It leaves rule (1); it is re-judged only when
  `FSN_VERSION` changes (the period is re-extracted).

The file must stay valid JSON: one that does not load fails the run loudly,
never silently lifting an acceptance (#275).

**Input validation (#578).** `.validation_failures` collects the fetch pass's
parse failures (`edgar_validation`) for `ingest._prefetch`'s gate, which
fails the run before any store write and lists them all in
`edgar.cache_dir/validation/`. Recorded (part 2), each then absent
for the rest of the pass: a quarter's `form.idx` (its rows skipped), a
`submissions.zip` member or older page (the CIK left unstamped, no per-CIK
top-up), a `companyfacts.zip` member (no company facts for the CIK, nothing
cached, no API call) and an FSN period whose extraction or parse fails
whole (no manifest, so the next run extracts it again). Part 3: a per-CIK
submissions API payload or older page (lists nothing, like SEC's `{}`) and
a per-CIK companyfacts API payload (no company facts, nothing cached). A
per-accession FSN failure and every per-document failure (cover page, SGML
header, delisting notice) stay with the failure policy above, one policy
(owner option (a) on #808): `check_failures` fails the run only where its
rules fire, and its message lists the run's unaccepted per-document
failures, bounded, naming a file under `edgar.cache_dir/validation/` that
holds the full list whenever the check fails with them, dry run or not
(#884).
"""

from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import itertools
import json
import math
import re
import shutil
import zipfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import duckdb
import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import (
    CoverPageParse,
    FsnFiling,
    FsnShare,
    StatementConflict,
    StatementFactsParse,
    UnstampedFiling,
    acceptance_times,
    parse_company_facts,
    parse_company_tickers,
    parse_cover_page,
    parse_delisting,
    parse_filing_index,
    parse_fsn,
    parse_sgml_header,
    parse_statement_facts,
)
from tradepartner.adapters.edgar_validation import PARSE_ERRORS, ValidationFailures
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingEvent,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
    StatementFactRecord,
)
from tradepartner.config import Settings, clean_message
from tradepartner.timeutil import ensure_tz_aware_utc

#: The cache versions below each name a directory under `edgar.cache_dir`;
#: a bump never deletes the superseded tree (the runbook's "After an EDGAR
#: cache version bump" says how to remove it, #615).
#: Bumped when a parser change must re-stamp every cached accession.
PARSER_VERSION = 1
#: The per-CIK stamps file's record layout (T164c, #1358): 2 adds `items` as
#: a fifth element and the file's `items_refreshed` flag. Separate from
#: `PARSER_VERSION`, which also keys the facts and statement caches: a file in
#: the old layout is read as it is, never re-stamped (module docstring).
STAMPS_LAYOUT = 2
_OLD_STAMPS_LAYOUT = 1
#: Bumped when the layout of the per-CIK statement-facts cache (T77a, #660)
#: or `parse_statement_facts` changes and every CIK must be re-parsed.
STATEMENT_VERSION = 1
#: Bumped when a parser change must re-extract every cached FSN period.
#: Separate from `PARSER_VERSION`, which stays the per-CIK stamps' key: a
#: bump here re-downloads every period's zip (the PR states that cost).
FSN_VERSION = 2  # 2: #609 (latest ddate per member, NULL shares, title whitespace)
#: Bumped when `parse_cover_page` changes and every per-document cover-page
#: parse (T11d) must be re-fetched and re-parsed. Deleting `edgar.cache_dir`
#: or bumping this switches a per-document accession back to its FSN row.
#: #615's nil-with-text refusal is no bump: a cached parse that skipped such
#: a fact keeps it until the next bump re-parses it.
COVER_VERSION = 2  # 2: #609 (nil facts skipped, incomplete listings skipped and counted)
#: As `COVER_VERSION`, for per-document `parse_sgml_header` results (T11d).
HEADER_VERSION = 1
#: As `COVER_VERSION`, for per-document `parse_delisting` results (T11f).
DELISTING_VERSION = 1
#: Bumped when the layout of `check_failures`'s full-list file changes (#884).
FILING_FAILURES_LIST_VERSION = 1

#: Bumped to retry every accession in `failed_filings.json` (T11h): deleting
#: an entry by hand un-quarantines one accession; bumping this un-quarantines
#: every one and resets every count.
FAILURES_VERSION = 1

#: Registration forms (T11d): a ranged header is requested from
#: `edgar.header_start_year`, unlike periodic forms and 8-K, which only get
#: one inside the lag window.
_REGISTRATION_FORMS = frozenset({"S-1", "F-1", "10-12B"})

#: The four `dei` cover-page concepts FSN's `num.tsv`/`txt.tsv` are filtered
#: to while streaming each zip member (module docstring "Caches").
_FSN_SHARES_TAG = "EntityCommonStockSharesOutstanding"
_FSN_LISTING_TAGS = ("Security12bTitle", "TradingSymbol", "SecurityExchangeName")
_FSN_MEMBERS = ("sub.tsv", "num.tsv", "txt.tsv", "dim.tsv")
#: FSN's `num.tsv` `value` column is DECIMAL(28,4) (SEC's FSN data-set
#: readme): a share count FSN holds agrees with another source's value when
#: that value is within half a unit of the 4th decimal place (#610 X1). A
#: property of the data format, not a tunable threshold.
_FSN_DECIMALS = 4
_FSN_HALF_UNIT = Decimal(1).scaleb(-_FSN_DECIMALS) / 2

_EASTERN = ZoneInfo("America/New_York")
_DELISTING_FORMS = frozenset({"25", "25/A", "25-NSE", "25-NSE/A"})
#: Filed by the exchange, so the filer's own copy is not a delisting of the filer.
_EXCHANGE_FORMS = frozenset({"25-NSE", "25-NSE/A"})

Quarter = tuple[int, int]

_T = TypeVar("_T")


class FilingFailuresError(RuntimeError):
    """Raised by `check_failures()` (T11h) when the failure policy's
    thresholds are crossed; the chunk fails with no row written."""


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    """One filing reduced from a submissions payload. `accepted_at` is `None`
    only for an accession cached as unstampable."""

    accession: str
    form: str
    primary_document: str
    inline_xbrl: bool
    accepted_at: datetime | None
    #: The payload's 8-K `items` string verbatim (`""` when it lists none);
    #: `None` when unknown: a payload or page without the column, or a record
    #: from the old stamps layout not yet refreshed (T164c, #1358).
    items: str | None = None


@dataclass
class _Submissions:
    """A CIK's submissions fetched so far in this instance (memoised for T11c)."""

    records: dict[str, SubmissionRecord]
    pages: list[str]
    fetched_at: datetime
    # #576: the API answered 200 `{}` for the payload or a page, so a missing
    # accession is not evidence the filing is unstampable.
    empty: bool = False


def reduce_submissions(payload: Mapping[str, Any]) -> tuple[dict[str, SubmissionRecord], list[str]]:
    """Accession -> reduced record for a submissions payload or an older page,
    and the names of the older pages it lists. A malformed payload raises
    `ValueError`."""
    try:
        filings = payload.get("filings")
        columns = filings["recent"] if isinstance(filings, Mapping) else payload
        pages = [str(f["name"]) for f in filings.get("files", [])] if filings else []
        times = acceptance_times(payload)
        # T164c (#1358): `items` is optional; without the column it is
        # unknown (`None`), which is not the empty string.
        accessions = columns["accessionNumber"]
        items = columns.get("items")
        if items is None:
            items = [None] * len(accessions)
        records = {
            accession: SubmissionRecord(
                accession,
                form,
                str(doc),
                bool(ixbrl),
                times[accession],
                None if item is None else str(item),
            )
            for accession, form, doc, ixbrl, item in zip(
                accessions,
                columns["form"],
                columns["primaryDocument"],
                columns["isInlineXBRL"],
                items,
                strict=True,
            )
            # #1138: a blank `acceptanceDateTime` leaves its accession out of
            # `times` (#1055); skip it here too rather than aborting the
            # whole payload's reduce on `KeyError`. The caller treats a
            # missing accession as unstamped, same as one it never asked for.
            if accession in times
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
        *,
        reuse_cached: bool = False,
        event_forms: Sequence[str] = ("8-K", "8-K/A"),
    ) -> None:
        if not event_forms:
            raise ValueError("event_forms must name at least one form (spec: never empty)")
        self._settings = settings
        # #660 (`ingest --bulk-from-cache`): company facts always from the
        # cached `companyfacts.zip`, with no request for it.
        self._reuse_cached = reuse_cached
        self._client = client or httpx.Client(timeout=settings.edgar.request_timeout_seconds)
        hooks = self._client.event_hooks
        hooks["request"] = [*hooks.get("request", []), self._count]
        self._client.event_hooks = hooks
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache = Path(settings.edgar.cache_dir)
        # #578: the fetch pass's parse failures, for `ingest._prefetch`'s gate.
        self.validation_failures = ValidationFailures(self._cache / "validation", self._clock)
        self._submissions: dict[str, _Submissions] = {}
        # T164c (#1358): CIK -> its stamps file's `items_refreshed` flag, as
        # last read or written; the forms `filing_events` answers (T164e
        # passes `edgar.event_forms`); the counts, each CIK counted once.
        self._items_refreshed: dict[str, bool] = {}
        self._event_forms = frozenset(event_forms)
        self._events_counted: set[str] = set()
        self.filing_events_unstamped = 0
        self.filing_events_items_missing = 0
        self._open_quarters: dict[Quarter, str] = {}
        # #578: quarters whose form.idx did not parse, recorded once per instance.
        self._unparsed_quarters: set[Quarter] = set()
        self.requests = 0
        self.skipped_filers = 0
        self.unstamped_filings: list[UnstampedFiling] = []
        self.fsn_duplicates = 0
        self.fsn_reissued = 0
        self.fsn_reissue_undetected = 0
        self.fsn_incomplete_listings = 0
        # #612: listings skipped (no title or exchange, #609) by this run's
        # per-document cover parses; as FSN's count, cache hits add nothing.
        self.cover_incomplete_listings = 0
        self.fsn_missing = 0
        self._filing_index_ran = False
        self._fsn_ready = False
        self._fsn_failed_accessions: frozenset[str] = frozenset()
        # Every accession FSN extracted, under whichever CIK it keys the filing
        # to, and the in-range periods whose manifests load (the lag window).
        self._fsn_extracted_accessions: frozenset[str] = frozenset()
        self._fsn_loaded_periods: tuple[str, ...] = ()
        # #868: periods parsed cleanly after an earlier period failed whole,
        # so not written; they still set where the lag window starts.
        self._fsn_unwritten_periods: list[str] = []
        # T11f: Form 25/25-NSE primary documents skipped pre-fetch (not XML).
        self.pre_xml_delistings = 0
        self.unstamped_delistings = 0
        # T11e: the `companyfacts.zip` path and member names once downloaded,
        # False once the per-CIK API was chosen, None until decided.
        self._facts_bulk: tuple[Path, frozenset[str]] | bool | None = None
        self._facts_memo: dict[tuple[str, str], _FactsCache] = {}
        # T77a (#660): plain counts set by the fetch pass (module docstring),
        # and the conflicts withheld, each listed once.
        self.statement_conflict_keys: list[StatementConflict] = []
        self.statement_conflicts = 0
        self.statement_non_usd = 0
        self.statement_malformed = 0
        self.statement_unstampable = 0
        self.statement_none = 0
        # CIK -> the key its statement cache was written or found under this
        # run (never the records), and the CIKs already counted.
        self._statement_filled: dict[str, str] = {}
        self._statement_counted: set[str] = set()
        # T11h: the failure policy.
        self.failed_filings = 0
        self.quarantined = 0
        self.facts_missing = 0
        # #566: bulk zip members that are an empty object or carry no `cik`
        # (SEC ships `{}` members), treated as absent from the zip and asked
        # of the per-CIK API instead; counted for the run's summary.
        self.facts_bulk_empty = 0
        self.submissions_bulk_empty = 0
        # #576: per-CIK API answers of 200 `{}` (SEC's "nothing here").
        self.facts_api_empty = 0
        self.submissions_api_empty = 0
        # #599: a payload with `facts` but no `cik` (SEC ships this shape for
        # a few CIKs, identically from the zip and the API): identified by
        # the CIK it was requested under, not treated as absent.
        self.facts_bulk_keyless = 0
        self.facts_api_keyless = 0
        # #610 X2: (accession, fact name, capped date) keys where a company
        # value dated after acceptance was dropped because capping its date
        # made it collide with the value the filing reports for that date.
        self.facts_capped_dropped: set[tuple[str, str, date]] = set()
        # accession -> (error_class, base_form, message), skipped this run,
        # never yet written to failed_filings.json (that is `record_failures`'s
        # job, after an `ok` commit).
        self._pending_failures: dict[str, tuple[str, str, str]] = {}
        # Pending failures recorded from a fact collision (facts(): withholds
        # one key, not a fetch): counted toward `_check_cross_day_pairs` only,
        # never `_check_per_document_group`'s numerator.
        self._collision_failures: set[str] = set()
        # Accessions whose guarded fetch succeeded this run: their stale
        # failed_filings.json entries are pruned by `record_failures()`.
        self._succeeded_this_run: set[str] = set()
        # Per-document accessions this run fetched or read from their
        # per-document cache (#610 policy 1: cache hits count; quarantined
        # accessions do not): `check_failures`'s per-document denominator.
        self._per_document_attempted: set[str] = set()
        # Quarantined per-document accessions this run skipped: they stay in
        # the per-document share (numerator unless `accepted`, denominator
        # always), since a failed check can now quarantine (#610 policy 2)
        # and quarantine must never excuse a failure nobody accepted.
        self._quarantined_this_run: set[str] = set()
        self._failed_filings_cache: dict[str, dict[str, Any]] | None = None
        # FSN accessions newly recorded as failed by this run's own
        # `_extract_fsn_period` calls, folded into `.failed_filings`.
        self._fsn_extraction_failures_this_run = 0

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
        acceptance times, so it returns each row with its filing date only).

        A quarter whose `form.idx` does not parse is recorded on
        `.validation_failures` (#578) and skipped whole, as if absent, by
        this walk and every later one on this instance (`filing_index` walks
        twice, `delistings` twice more), so it is recorded once. The cached
        file is kept as it is."""
        for quarter in quarters:
            if quarter in self._unparsed_quarters:
                continue
            text = self._index_text(quarter, open_quarter)
            parsed = self.validation_failures.collect(
                "form.idx", f"{quarter[0]}-QTR{quarter[1]}", partial(parse_filing_index, text, {})
            )
            if parsed is None:
                self._unparsed_quarters.add(quarter)
                continue
            for row in parsed.unstamped:
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
        unparsed: set[str] = set()
        if len(pending) > self._settings.edgar.bulk_stamp_threshold_ciks:
            unparsed = self._stamp_bulk(pending, stamps)
        for cik, wanted in pending.items():
            wanted -= stamps[cik].keys()
            if not wanted or cik in unparsed:
                continue  # #578: a recorded bad zip member is never topped up per CIK
            submissions = self._fetch_submissions(cik, wanted)
            new = {a: submissions.records[a] for a in wanted if a in submissions.records}
            for accession in wanted - new.keys():
                if submissions.empty:
                    break  # #576: an empty answer proves nothing unstampable
                row, quarter = kept[cik][accession]
                if self._settled(quarter, submissions.fetched_at):
                    new[accession] = SubmissionRecord(accession, row.form, "", False, None)
            if new:
                stamps[cik].update(new)
                self._save_stamps(cik, stamps[cik])
        return stamps

    def _fetch_submissions(self, cik: str, wanted: set[str]) -> _Submissions:
        """`cik`'s submissions, memoised; older pages fetched while any of
        `wanted` is still missing.

        A payload or older page that does not parse is recorded on
        `.validation_failures` (#578 part 3) and lists nothing, like an
        empty answer (#576): paging stops and the memo is marked `empty`, so
        nothing it would stamp is cached as unstampable this run."""
        memo = self._submissions.get(cik)
        if memo is None:
            try:
                payload = edgar_raw.submissions(cik, settings=self._settings, client=self._client)
            except _UNDECODABLE as error:  # #578: a body that is not JSON
                self.validation_failures.record(_SUBMISSIONS_API, f"CIK{cik}.json", error)
                payload = _NOT_JSON
            if payload is _NOT_JSON:
                memo = self._submissions[cik] = _Submissions({}, [], self._now(), empty=True)
            elif _empty_object(payload):  # #576: SEC's "nothing here"; lists nothing
                self.submissions_api_empty += 1
                memo = self._submissions[cik] = _Submissions({}, [], self._now(), empty=True)
            else:
                parsed = self.validation_failures.collect(
                    _SUBMISSIONS_API, f"CIK{cik}.json", partial(reduce_submissions, payload)
                )
                records, pages = parsed if parsed is not None else ({}, [])
                memo = _Submissions(records, pages, self._now(), empty=parsed is None)
                self._submissions[cik] = memo
        while memo.pages and not wanted <= memo.records.keys():
            name = memo.pages.pop(0)
            try:
                page = edgar_raw.submissions_page(
                    name, settings=self._settings, client=self._client
                )
            except _UNDECODABLE as error:  # #578: recorded; lists nothing, as below
                self.validation_failures.record(_SUBMISSIONS_API, name, error)
                memo.pages.clear()
                memo.empty = True
                break
            if _empty_object(page):  # #576: lists nothing; stop paging
                self.submissions_api_empty += 1
                memo.pages.clear()
                memo.empty = True
                break
            older = self.validation_failures.collect(
                _SUBMISSIONS_API, name, partial(reduce_submissions, page)
            )
            if older is None:  # recorded: lists nothing, as above
                memo.pages.clear()
                memo.empty = True
                break
            memo.records.update(older[0])
        return memo

    def _stamp_bulk(
        self, pending: Mapping[str, set[str]], stamps: dict[str, dict[str, SubmissionRecord]]
    ) -> set[str]:
        """Stamp from `submissions.zip`; never marks anything unstampable (the
        zip trails the day's filings).

        A member (CIK payload or older page) that does not parse is recorded
        on `.validation_failures` (#578) and its CIK is returned: the CIK
        keeps no stamp from this zip and `_stamp` asks nothing of the per-CIK
        API for it, whose answer would likely share the shape (#599), so its
        rows stay unstamped this run and nothing is cached for it."""
        path = edgar_raw.bulk_submissions(settings=self._settings, client=self._client)
        unparsed: set[str] = set()
        with zipfile.ZipFile(path) as bulk:
            names = set(bulk.namelist())

            def member(
                name: str, absent: Callable[[Any], bool]
            ) -> tuple[dict[str, SubmissionRecord], list[str]] | bool:
                """`name`'s records and pages; `True` when `absent(payload)`
                (#566), `False` once recorded as unparsed."""

                def parse() -> tuple[dict[str, SubmissionRecord], list[str]] | bool:
                    payload = json.loads(bulk.read(name))
                    return True if absent(payload) else reduce_submissions(payload)

                parsed = self.validation_failures.collect("submissions.zip member", name, parse)
                return False if parsed is None else parsed

            for cik, wanted in pending.items():
                if f"CIK{cik}.json" not in names:
                    continue
                main = member(f"CIK{cik}.json", _keyless_member)
                if main is False:
                    unparsed.add(cik)
                    continue
                if main is True:  # #566: as if absent; stamped per CIK
                    self.submissions_bulk_empty += 1
                    continue
                records, pages = main
                for page in pages:
                    if wanted <= records.keys() or page not in names:
                        break
                    older = member(page, _empty_object)
                    if older is False:
                        unparsed.add(cik)
                        break
                    if older is True:
                        # #566: an empty page is absent; the per-CIK top-up
                        # in `_stamp` fetches it for what is still wanted.
                        self.submissions_bulk_empty += 1
                        break
                    records.update(older[0])
                if cik in unparsed:
                    continue
                new = {a: records[a] for a in wanted if a in records}
                if new:
                    stamps[cik].update(new)
                    self._save_stamps(cik, stamps[cik])
        return unparsed

    def _stamps_path(self, cik: str) -> Path:
        return self._cache / "stamps" / f"v{PARSER_VERSION}" / f"{cik}.json"

    def _load_stamps(self, cik: str) -> dict[str, SubmissionRecord]:
        """`cik`'s stamps in either layout (module docstring, "Stamps layout");
        `{}` for an absent, truncated or unknown file. Records the file's
        `items_refreshed` flag for `_save_stamps` (`True` with no file)."""
        try:
            data = json.loads(self._stamps_path(cik).read_bytes())
            if data["version"] != PARSER_VERSION or data["cik"] != cik:
                raise ValueError("another version or CIK")
            layout = data.get("layout", _OLD_STAMPS_LAYOUT)
            if layout == _OLD_STAMPS_LAYOUT:
                rows = [(*row, None) for row in data["records"].values()]
                refreshed = False
            elif layout == STAMPS_LAYOUT:
                rows = list(data["records"].values())
                refreshed = data["items_refreshed"]
                if not isinstance(refreshed, bool):
                    raise TypeError("items_refreshed")
            else:
                raise ValueError(f"stamps layout {layout!r}")
            records = {}
            for accession, (form, doc, ixbrl, at, items) in zip(data["records"], rows, strict=True):
                if items is not None and not isinstance(items, str):
                    raise TypeError("items")
                records[accession] = SubmissionRecord(
                    accession,
                    form,
                    doc,
                    ixbrl,
                    ensure_tz_aware_utc(datetime.fromisoformat(at), field_name="stamp")
                    if at
                    else None,
                    items,
                )
        except (OSError, ValueError, KeyError, TypeError):
            self._items_refreshed.pop(cik, None)
            return {}  # absent, truncated or another layout: stamp again
        self._items_refreshed[cik] = refreshed
        return records

    def _save_stamps(
        self,
        cik: str,
        records: Mapping[str, SubmissionRecord],
        *,
        items_refreshed: bool | None = None,
    ) -> None:
        """Write `cik`'s stamps in `STAMPS_LAYOUT`. `items_refreshed` defaults
        to the flag `_load_stamps` last read for the CIK (`True` if none), so
        a stamping pass never marks an old-layout file refreshed."""
        if items_refreshed is None:
            items_refreshed = self._items_refreshed.get(cik, True)
        data = {
            "version": PARSER_VERSION,
            "layout": STAMPS_LAYOUT,
            "cik": cik,
            "items_refreshed": items_refreshed,
            "records": {
                r.accession: [
                    r.form,
                    r.primary_document,
                    r.inline_xbrl,
                    r.accepted_at.isoformat() if r.accepted_at else None,
                    r.items,
                ]
                for r in records.values()
            },
        }
        edgar_raw.write_atomic(self._stamps_path(cik), json.dumps(data).encode("utf-8"))
        self._items_refreshed[cik] = items_refreshed

    # --- filing events (T164c, #1358) ----------------------------------------

    def filing_events(self, cik: str) -> list[FilingEvent]:
        """One `FilingEvent` per stamped accession of `cik` whose form is in
        `event_forms` and whose `items` is known, sorted by `accepted_at`,
        then accession (module docstring, "Stamps layout and `items`").

        The ingest asks this only while `edgar.filing_events_enabled` is on
        (T164e). The first ask for a CIK whose stamps are not yet refreshed
        re-reads its submissions once and writes `items` only; a CIK with no
        stamps file is never fetched here (only the stamping paths stamp).
        Counts: `.filing_events_unstamped` (event-form sentinels with no
        acceptance) and `.filing_events_items_missing` (stamped event-form
        accessions whose `items` is still unknown after the refresh, a
        terminal state), each CIK counted once per instance."""
        _validate_cik(cik)
        stamps = self._load_stamps(cik)
        if stamps and not self._items_refreshed.get(cik, True):
            stamps = self._refresh_items(cik, stamps)
        refreshed = self._items_refreshed.get(cik, True)
        events: list[FilingEvent] = []
        unstamped = missing = 0
        for record in stamps.values():
            if record.form not in self._event_forms:
                continue
            if record.accepted_at is None:
                unstamped += 1
            elif record.items is None:
                missing += 1 if refreshed else 0
            else:
                events.append(
                    FilingEvent(
                        cik, record.accession, record.form, record.items, record.accepted_at
                    )
                )
        if cik not in self._events_counted:
            self._events_counted.add(cik)
            self.filing_events_unstamped += unstamped
            self.filing_events_items_missing += missing
        events.sort(key=lambda e: (e.accepted_at, e.accession))
        return events

    def _refresh_items(
        self, cik: str, stamps: dict[str, SubmissionRecord]
    ) -> dict[str, SubmissionRecord]:
        """Fill `items` for `cik`'s stamped accessions from one re-read of its
        submissions, touching nothing else, and mark the file refreshed. A
        read that fails or answers nothing leaves the file as it is (the
        refresh is tried again on a later ask)."""
        payload = self._items_source(cik, set(stamps))
        if payload is None:
            return stamps
        filled = dict(stamps)
        for accession, record in stamps.items():
            if record.items is not None:
                continue
            fresh = payload.get(accession)
            if fresh is not None and fresh.items is not None:
                filled[accession] = replace(record, items=fresh.items)
        self._save_stamps(cik, filled, items_refreshed=True)
        return filled

    def _items_source(self, cik: str, wanted: set[str]) -> dict[str, SubmissionRecord] | None:
        """`cik`'s submission records for the refresh, or `None` when nothing
        usable was read: the cached `submissions.zip` member and its pages
        under `reuse_cached` (no request), otherwise the per-CIK API."""
        if not self._reuse_cached:
            submissions = self._fetch_submissions(cik, wanted)
            return None if submissions.empty else submissions.records
        path = self._cache / "bulk" / "submissions.zip"
        try:
            bulk = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as error:
            raise zipfile.BadZipFile(
                f"{path}: reuse_cached needs a submissions.zip that opens as a zip: {error}"
            ) from error
        with bulk:
            names = set(bulk.namelist())

            def member(name: str) -> tuple[dict[str, SubmissionRecord], list[str]] | None:
                def parse() -> tuple[dict[str, SubmissionRecord], list[str]] | None:
                    payload = json.loads(bulk.read(name))
                    return None if _keyless_member(payload) else reduce_submissions(payload)

                return self.validation_failures.collect("submissions.zip member", name, parse)

            name = f"CIK{cik}.json"
            main = member(name) if name in names else None
            if main is None:
                return None
            records, pages = main
            for page in pages:
                if wanted <= records.keys() or page not in names:
                    break
                older = member(page)
                if older is None:
                    break
                records.update(older[0])
            return records

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
        A period is re-extracted only when `FSN_VERSION` changes. Once a
        period fails whole (recorded on `.validation_failures`, #578), the
        later periods of the pass are extracted but write no manifest or
        cache (#868): otherwise a later period would claim an accession the
        failed one shares, and keep it after the failed one is repaired.

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
        self._fsn_extraction_failures_this_run = 0
        self._fsn_unwritten_periods = []

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
        persist = True
        for period in to_extract:
            # #868: after a period fails whole, the later periods of this pass
            # are still extracted (every failure shows in one pass, #578) but
            # write nothing, so none claims an accession the failed period
            # shares; the gate fails the run, and the next run extracts them.
            persist = self._extract_fsn_period(
                period, kept_forms, known_accessions, persist=persist
            )

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
        self, period: str, kept_forms: set[str], known_accessions: set[str], *, persist: bool
    ) -> bool:
        """Extract one FSN period. When `persist` is `False` (an earlier
        period of the pass failed whole, #868), the period is only downloaded
        and parsed, so a whole-period failure is still recorded, and nothing
        else happens: no accession is claimed, no counter moves, no cache row
        or manifest is written (the period only counts toward where the lag
        window starts, `_lag_window_start`). Returns `False` once a period has failed
        whole (this one, or an earlier one), else `True`."""
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
                free_text="value",
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
                    free_text="segments",
                )
                if dimhashes
                else []
            )
            parsed = parse_fsn(sub_rows, num_rows, txt_rows, dim_rows)
        except _FSN_PARSE_ERRORS as error:
            # #578: the period is absent for this run (no manifest, so the
            # next run downloads and extracts it again); `record` re-raises
            # a tripped path-safety guard.
            self.validation_failures.record("FSN period", period, error)
            return False
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)  # the zip is deleted after extraction

        if not persist:
            # parsed only to record a whole-period failure (#868)
            self._fsn_unwritten_periods.append(period)
            return False
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

        accessions_failed = [
            {
                "accession": f.accession,
                "error_class": f.error_class,
                "base_form": form_by_accession.get(f.accession, "").removesuffix("/A"),
                # P4 (#610): the message, so a failed run is diagnosed from disk
                "message": self._stored_message(f.error),
                "accepted": False,
            }
            for f in parsed.failures
            # only forms this adapter serves count toward T11h's failure policy
            if form_by_accession.get(f.accession, "").removesuffix("/A") in kept_forms
        ]
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
            "accessions_failed": accessions_failed,
            "committed": False,
        }
        self._save_fsn_manifest(period, manifest)
        # T11h: an FSN accession whose rows failed extraction counts on
        # `.failed_filings`, never quarantined, retried only when FSN_VERSION
        # changes (the zip is gone).
        self._fsn_extraction_failures_this_run += len(accessions_failed)
        self._update_failed_filings_count()
        return True

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
        period at or after `edgar.fsn_first_year`, or of a newer one this
        pass parsed but did not write after an earlier period failed whole
        (#868: otherwise every filing since the last written period would be
        fetched one by one on a run the gate fails anyway). With no such
        period (e.g. `fsn_first_year` past the newest listed period) it
        raises rather than treating all history as the lag window."""
        # in range, and the manifest loads; or parsed but unwritten (#868)
        periods = (*self._fsn_loaded_periods, *self._fsn_unwritten_periods)
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
        by `cover_pages` (`count_missing=False` for `facts`). This method
        runs for both `cover_pages` and `facts` (T11h): an accession already
        failed earlier this run is not fetched twice, and quarantine is
        counted only once, on the `cover_pages` pass."""
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
                self._per_document_attempted.add(accession)  # #610 policy 1: a cache hit
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
                if accession in self._pending_failures:
                    continue  # failed earlier this run (cover_pages, then facts): never twice
                if self._is_quarantined(accession):
                    self._quarantined_this_run.add(accession)
                    if count_missing:  # count once per run, on the cover_pages pass
                        self.quarantined += 1
                    continue
                base_form = record.form.removesuffix("/A")
                fetch: Callable[[], CoverPageParse] = partial(
                    self._fetch_cover_page,
                    cik,
                    accession,
                    record.primary_document,
                    record.accepted_at,
                )
                parsed = self._guarded(accession, base_form, fetch)
                if parsed is not None and parsed.cover.cik == cik:  # a combined filing names one
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
        self.cover_incomplete_listings += parsed.incomplete_listings
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
            # #612: the listings the parse skipped, so they are diagnosable
            # from disk as an FSN manifest's are. Not read back: an entry
            # written before #612 lacks it and still loads (no version bump).
            "incomplete_listings": parsed.incomplete_listings,
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
                self._per_document_attempted.add(accession)  # #610 policy 1: a cache hit
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
                    header = self._maybe_ranged_header(
                        cik, accession, record.form, base_form, record.accepted_at
                    )
                    if header is not None:
                        headers.append(header)
                continue
            # absent from FSN entirely
            if base_form in _REGISTRATION_FORMS:
                if record.accepted_at >= header_start:
                    header = self._maybe_ranged_header(
                        cik, accession, record.form, base_form, record.accepted_at
                    )
                    if header is not None:
                        headers.append(header)
            elif record.accepted_at >= lag_start:
                header = self._maybe_ranged_header(
                    cik, accession, record.form, base_form, record.accepted_at
                )
                if header is not None:
                    headers.append(header)
        headers.sort(key=lambda h: (h.accepted_at, h.accession))
        return headers

    def _maybe_ranged_header(
        self, cik: str, accession: str, form: str, base_form: str, accepted_at: datetime
    ) -> FilingHeader | None:
        """`_ranged_header`, gated by the quarantine check and wrapped in
        `_guarded` (T11h): a mismatched accession, or a 404/410, is skipped."""
        if self._is_quarantined(accession):
            self._quarantined_this_run.add(accession)
            self.quarantined += 1
            return None
        return self._guarded(
            accession, base_form, lambda: self._ranged_header(cik, accession, form, accepted_at)
        )

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
        """Every Form 25, 25/A, 25-NSE and 25-NSE/A over full history
        (`since` filters the result, never the scan), de-duplicated by
        accession as `filing_index` de-duplicates its own rows (the
        exchange's own copy of a 25-NSE dropped). A 25 or 25-NSE whose
        `primaryDocument` is not an `.xml` file is not downloaded: counted
        on `.pre_xml_delistings` (`parse_delisting` reads XML only, and the
        index runs from 1993, long before EDGAR's XML forms). A cached
        parse, or a quarantined accession (T11h), makes no request."""
        if since is not None:
            since = ensure_tz_aware_utc(since, field_name="since")
        self.pre_xml_delistings = 0
        self.unstamped_delistings = 0
        start = (self._settings.edgar.index_first_year, 1)
        last = quarter_of(self._now())
        quarters = [(y, q) for y in range(start[0], last[0] + 1) for q in range(1, 5)]
        quarters = [q for q in quarters if start <= q <= last]

        delisting_ciks: dict[str, set[str]] = {}
        for _, row in self._rows(quarters, last):
            if row.form in _DELISTING_FORMS:
                delisting_ciks.setdefault(row.accession, set()).add(row.cik)

        kept: dict[str, dict[str, tuple[UnstampedFiling, Quarter]]] = {}
        for quarter, row in self._rows(quarters, last):
            if row.form not in _DELISTING_FORMS:
                continue
            if (
                row.form in _EXCHANGE_FORMS
                and _filed_by(row)
                and len(delisting_ciks[row.accession]) > 1
            ):
                continue  # the exchange's copy of a 25-NSE; the subject company keeps it
            kept.setdefault(row.cik, {}).setdefault(row.accession, (row, quarter))

        stamps = self._stamp(kept)
        # One result per accession (plan T11f): a Form 25 kept under several
        # CIKs is served once, from the first CIK (index order) that stamps it.
        candidates: dict[str, list[tuple[str, SubmissionRecord | None]]] = {}
        for cik, rows in kept.items():
            for accession in rows:
                candidates.setdefault(accession, []).append((cik, stamps[cik].get(accession)))
        results: list[DelistingFiling] = []
        for accession, options in candidates.items():
            stamped = [
                (c, r, r.accepted_at)
                for c, r in options
                if r is not None and r.accepted_at is not None
            ]
            if not stamped:
                # Every unstamped delisting accession, so an exchange-only 25-NSE
                # (a row `filing_index` never keeps) is visible too. It overlaps
                # `.unstamped_filings` for subject-company rows: never add the two.
                self.unstamped_delistings += 1
                continue
            cik, record, accepted_at = stamped[0]
            cached = self._load_delisting_cache(accession)
            if cached is not None:
                self._per_document_attempted.add(accession)  # #610 policy 1: a cache hit
                results.append(
                    DelistingFiling(
                        cik=cached.cik,
                        form=cached.form,
                        class_title=cached.class_title,
                        exchange=cached.exchange,
                        accession=accession,
                        accepted_at=accepted_at,
                        effective_on=cached.effective_on,
                    )
                )
                continue
            if not record.primary_document.lower().endswith(".xml"):
                self.pre_xml_delistings += 1
                continue
            if self._is_quarantined(accession):
                self._quarantined_this_run.add(accession)
                self.quarantined += 1
                continue
            delisting_fetch: Callable[[], DelistingFiling] = partial(
                self._fetch_delisting,
                cik,
                accession,
                record.form,
                record.primary_document,
                accepted_at,
            )
            parsed = self._guarded(accession, record.form.removesuffix("/A"), delisting_fetch)
            if parsed is not None:
                results.append(parsed)
        if since is not None:
            results = [d for d in results if d.accepted_at >= since]
        results.sort(key=lambda d: (d.accepted_at, d.accession))
        return results

    def _fetch_delisting(
        self, cik: str, accession: str, form: str, primary_document: str, accepted_at: datetime
    ) -> DelistingFiling:
        # The root copy, never an `xsl.../` rendering of it (as `_fetch_cover_page`, T11d).
        root_document = re.sub(r"^xsl[^/]*/", "", primary_document)
        path = edgar_raw.download_filing_file(
            cik, accession, root_document, settings=self._settings, client=self._client
        )
        try:
            text = path.read_text(encoding="utf-8")
            parsed = parse_delisting(text, form=form, accession=accession, accepted_at=accepted_at)
        finally:
            path.unlink(missing_ok=True)  # the document is deleted after parsing
        self._save_delisting_cache(accession, parsed)
        return parsed

    def _delisting_cache_path(self, accession: str) -> Path:
        edgar_raw.validate_accession(accession)
        return self._cache / "delisting" / f"v{DELISTING_VERSION}" / f"{accession}.json"

    def _load_delisting_cache(self, accession: str) -> _CachedDelisting | None:
        path = self._delisting_cache_path(accession)  # validates first, outside the try
        try:
            data = json.loads(path.read_bytes())
            if data.get("version") != DELISTING_VERSION or data.get("accession") != accession:
                return None
            effective = data.get("effective_on")
            return _CachedDelisting(
                cik=str(data["cik"]),
                form=str(data["form"]),
                class_title=str(data["class_title"]),
                exchange=str(data["exchange"]),
                effective_on=date.fromisoformat(effective) if effective else None,
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # absent, truncated or another version: fetch again

    def _save_delisting_cache(self, accession: str, parsed: DelistingFiling) -> None:
        data = {
            "version": DELISTING_VERSION,
            "accession": accession,
            "cik": parsed.cik,
            "form": parsed.form,
            "class_title": parsed.class_title,
            "exchange": parsed.exchange,
            "effective_on": parsed.effective_on.isoformat() if parsed.effective_on else None,
        }
        edgar_raw.write_atomic(
            self._delisting_cache_path(accession), json.dumps(data).encode("utf-8")
        )

    # --- failure policy (T11h) -----------------------------------------------

    def _is_quarantined(self, accession: str) -> bool:
        """Whether `accession` failed identically on `edgar.max_filing_failures`
        consecutive counted days: no further request until its
        `failed_filings.json` entry is deleted by hand or `FAILURES_VERSION`
        changes. `accepted` does not lift a quarantine (plan T11h: "accepted
        per-document entries stay quarantined"), only `check_failures`'s
        thresholds."""
        entry = self._failure_store().get(accession)
        if entry is None or entry.get("kind") == "collision":
            return False  # a collision counts toward the pair rule only, never quarantine
        return bool(entry["count"] >= self._settings.edgar.max_filing_failures)

    def _guarded(self, accession: str, base_form: str, fn: Callable[[], _T]) -> _T | None:
        """Run one per-document fetch/parse, skipping (never raising) the
        four failure kinds plan T11h names: a `ValueError` from a parser (a
        malformed document, a forged fact collision, or `_ranged_header`'s
        own accession-mismatch check), or a 404/410 for the document or
        header itself. Any other `httpx.HTTPStatusError` (a 500, say) still
        propagates and fails the chunk. Records `accession` as attempted
        this run either way (`check_failures`'s per-document denominator)."""
        self._per_document_attempted.add(accession)
        try:
            result = fn()
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (404, 410):
                raise
            self._record_failure(accession, type(error).__name__, base_form, str(error))
            return None
        except edgar_raw.InvalidFilingReferenceError:
            raise  # a tripped path-safety guard is not a filing failure (#275)
        except ValueError as error:
            self._record_failure(accession, type(error).__name__, base_form, str(error))
            return None
        self._succeeded_this_run.add(accession)
        return result

    def _record_failure(
        self,
        accession: str,
        error_class: str,
        base_form: str,
        message: str,
        *,
        collision: bool = False,
    ) -> None:
        """Record one skipped failure for `record_failures()` to write later.
        `collision=True` (a `facts()` key withheld, not a fetch skipped) is
        excluded from `_check_per_document_group`'s numerator (plan T11h:
        "counts toward the (error class, base form) rule only")."""
        self._pending_failures[accession] = (error_class, base_form, message)
        if collision:
            self._collision_failures.add(accession)
        self._update_failed_filings_count()

    def _update_failed_filings_count(self) -> None:
        self.failed_filings = len(self._pending_failures) + self._fsn_extraction_failures_this_run

    def _failure_store(self) -> dict[str, dict[str, Any]]:
        if self._failed_filings_cache is None:
            self._failed_filings_cache = self._load_failed_filings()
        return self._failed_filings_cache

    def _failed_filings_path(self) -> Path:
        return self._cache / "failed_filings.json"

    def _load_failed_filings(self) -> dict[str, dict[str, Any]]:
        """The failure store. Absent, or another `FAILURES_VERSION` (the
        documented way to retry everything): empty. Anything else that does
        not load raises, naming the file: the owner edits it by hand, and a
        typo must never silently lift every quarantine and `accepted` flag,
        nor be overwritten by the next commit (#275)."""
        path = self._failed_filings_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"{path} does not load ({type(exc).__name__}); fix it by hand"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; fix it by hand")
        if data.get("version") != FAILURES_VERSION:
            return {}
        entries = data.get("entries")
        if not isinstance(entries, dict) or not all(isinstance(v, dict) for v in entries.values()):
            raise ValueError(f"{path} has malformed entries; fix it by hand")
        return entries

    def _save_failed_filings(self, entries: Mapping[str, dict[str, Any]]) -> None:
        data = {"version": FAILURES_VERSION, "entries": dict(entries)}
        edgar_raw.write_atomic(self._failed_filings_path(), json.dumps(data).encode("utf-8"))

    def record_failures(self) -> None:
        """The `after_commit` hook (T11h): advances `failed_filings.json`'s
        consecutive-counted-day counts for this run's per-document failures
        (`_pending_failures`, messages included), and marks every
        still-uncommitted FSN manifest `committed: true`.

        Counts advance at most once per Eastern calendar day, the day read
        from the clock now (commit time). A second writer the same day is
        idempotent: an entry already advanced today is left alone. A
        different error message (or class) than the stored entry resets the
        count to 1 instead of incrementing it; `accepted` (set by hand) is
        preserved across an advance.
        """
        self._write_failure_store()
        for period in self._cached_fsn_periods():
            manifest = self._load_fsn_manifest(period)
            if manifest is not None and not manifest.get("committed", False):
                manifest["committed"] = True
                self._save_fsn_manifest(period, manifest)
        # The run is recorded: its in-memory failures and attempts end with it.
        self._pending_failures.clear()
        self._collision_failures.clear()
        self._per_document_attempted.clear()
        self._quarantined_this_run.clear()
        self._succeeded_this_run.clear()

    def record_failed_check(self) -> None:
        """Called by `ingest._prefetch` when `check_failures()` raised, on a
        non-dry run (#610 policy 2): writes this run's failures to
        `failed_filings.json` by the same counted-day rule as
        `record_failures()` (messages included, P4), so they can be
        quarantined and `accepted` although the run never commits. FSN
        manifests stay uncommitted (they keep being judged), and the
        in-memory state is kept: the run is not recorded as a success."""
        if self._pending_failures or self._succeeded_this_run:
            self._write_failure_store()

    def _write_failure_store(self) -> None:
        """Merge this run's failures into `failed_filings.json` under the
        lock, pruning accessions that parse now; counts advance at most once
        per Eastern day read from the clock now."""
        today = self._now().astimezone(_EASTERN).date().isoformat()
        with self._failed_filings_lock():
            # Re-read under the lock, never the copy loaded at the fetch pass:
            # an owner's hand edit (or another writer) since then is kept (#275).
            store = dict(self._load_failed_filings())
            for accession in self._succeeded_this_run - self._pending_failures.keys():
                store.pop(accession, None)  # it parses now: the old failure is resolved
            self._merge_pending_failures(store, today)
            self._save_failed_filings(store)
        self._failed_filings_cache = store

    def _stored_message(self, message: str) -> str:
        """A failure message as written to disk (P4, #610): every configured
        secret redacted, control characters replaced, cut to
        `ingest.max_message_chars` (it is server- or filing-supplied text):
        `config.clean_message`, the run row's own cleaning (#629)."""
        return clean_message(message, self._settings)

    def _accepted(self, accession: str, error_class: str, message: str) -> bool:
        """Whether this run's failure of `accession` is `accepted`: only when
        its stored entry is accepted for this same error class and message
        hash (fail closed: a different failure is judged again)."""
        entry = self._failure_store().get(accession)
        return (
            entry is not None
            and bool(entry.get("accepted", False))
            and entry.get("error_class") == error_class
            and entry.get("message_hash") == _message_hash(message)
        )

    @contextlib.contextmanager
    def _failed_filings_lock(self) -> Iterator[None]:
        """An advisory lock around `failed_filings.json`'s read-merge-write."""
        lock_path = self._failed_filings_path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _merge_pending_failures(self, store: dict[str, dict[str, Any]], today: str) -> None:
        """This run's failures into `store`, by the counted-day rule."""
        for accession, (error_class, base_form, message) in self._pending_failures.items():
            message_hash = _message_hash(message)
            entry = store.get(accession)
            if entry is not None and entry.get("last_counted_day") == today:
                continue  # already advanced today
            same = (
                entry is not None
                and entry.get("error_class") == error_class
                and entry.get("message_hash") == message_hash
            )
            store[accession] = {
                "error_class": error_class,
                "base_form": base_form,
                "message_hash": message_hash,
                "message": self._stored_message(message),  # P4 (#610)
                "count": (entry["count"] + 1) if same and entry is not None else 1,
                "last_counted_day": today,
                # `accepted` was given for one error: a different one is reviewed again.
                "accepted": bool(entry.get("accepted", False)) if same and entry else False,
                "kind": "collision" if accession in self._collision_failures else "fetch",
            }

    def check_failures(self) -> None:
        """Called by `_prefetch` after the fetch pass, before the lock.
        Raises `FilingFailuresError` (the chunk fails, nothing written) when
        any of plan T11h's three rules fires; see the module docstring.

        The message then lists this run's per-document failures (and
        `facts()` collisions) that no `accepted` entry excuses (#578 part 3,
        owner option (a) on #808):
        their count, then the first `edgar.max_validation_listed` as
        `accession error_class/base_form: message`, each message cleaned
        (`_stored_message`). Whenever the check fails with such failures, dry
        run or not, the full list is written to a JSON file under
        `edgar.cache_dir/validation/`, named at the very start of the
        message, so the run row's `ingest.max_message_chars` cut keeps it
        whatever the reasons' length (#884); `failed_filings.json` (written
        on a non-dry run by `record_failed_check`) holds them too, and
        accepting one stays the hand edit there."""
        reasons: list[str] = []
        self._check_fsn_group(reasons)
        self._check_per_document_group(reasons)
        self._check_cross_day_pairs(reasons)
        if reasons:
            full_list, listing = self._unaccepted_listing()
            raise FilingFailuresError(
                full_list
                + "; ".join(reasons)
                + f"; failure messages in {self._failed_filings_path()} (not on a dry run)"
                + f" and the FSN manifests under {self._fsn_root() / 'manifests'}"
                + listing
            )

    def _unaccepted_listing(self) -> tuple[str, str]:
        """`check_failures`'s message parts for this run's unaccepted
        per-document failures: where their full list was written (the
        message's first part) and the bounded list (its last); both "" when
        there are none."""
        unaccepted = [
            (accession, error_class, base_form, message)
            for accession, (error_class, base_form, message) in sorted(
                self._pending_failures.items()
            )
            if not self._accepted(accession, error_class, message)
        ]
        if not unaccepted:
            return "", ""
        try:
            where = str(self._write_unaccepted_listing(unaccepted))
        except OSError as error:  # the check still fails, unlisted on disk
            where = self._stored_message(f"not written ({type(error).__name__}: {error})")
        limit = self._settings.edgar.max_validation_listed
        listed = "; ".join(
            self._stored_message(f"{accession} {error_class}/{base_form}: {message}")
            for accession, error_class, base_form, message in unaccepted[:limit]
        )
        return (
            f"full list of this run's {len(unaccepted)} unaccepted filing failures: {where}; ",
            f"; this run's unaccepted filing failures (per-document and fact collisions): "
            f"{len(unaccepted)}, first {min(limit, len(unaccepted))}: {listed}",
        )

    def _write_unaccepted_listing(self, unaccepted: Sequence[tuple[str, str, str, str]]) -> Path:
        """Write every one of `check_failures`'s unaccepted failures to a new
        JSON file under `edgar.cache_dir/validation/` (beside the input
        validation gate's lists) and return its path (#884): a dry run
        writes no `failed_filings.json`, and the message lists only the
        first `edgar.max_validation_listed`. Messages are cleaned as on
        disk elsewhere (`_stored_message`)."""
        now = self._now()
        path = (
            self._cache / "validation" / f"filing-failures-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        payload = {
            "version": FILING_FAILURES_LIST_VERSION,
            "written_at": now.isoformat(),
            "failures": [
                {
                    "accession": accession,
                    "error_class": error_class,
                    "base_form": base_form,
                    "message": self._stored_message(message),
                }
                for accession, error_class, base_form, message in unaccepted
            ],
        }
        edgar_raw.write_atomic(path, json.dumps(payload, indent=1).encode("utf-8"))
        return path.resolve()

    def _threshold_reason(self, label: str, failures: int, denominator: int) -> str | None:
        min_n = self._settings.edgar.min_failed_filings
        max_share = self._settings.edgar.max_failed_filing_share
        if failures >= min_n and denominator > 0 and failures / denominator > max_share:
            share = failures / denominator
            return (
                f"{label}: {failures} failures of {denominator} attempted "
                f"({share:.1%}, over {max_share:.1%})"
            )
        return None

    def _check_fsn_group(self, reasons: list[str]) -> None:
        """FSN's denominator is every served-form accession (`accessions_served`
        plus `accessions_failed`) of every period whose manifest is still
        uncommitted, not only those extracted this run."""
        failures = denominator = 0
        for period in self._cached_fsn_periods():
            manifest = self._load_fsn_manifest(period)
            if manifest is None or manifest.get("committed", False):
                continue
            served = manifest.get("accessions_served", [])
            failed = manifest.get("accessions_failed", [])
            denominator += len(served) + len(failed)
            failures += sum(1 for f in failed if not f.get("accepted", False))
        reason = self._threshold_reason("FSN extraction", failures, denominator)
        if reason:
            reasons.append(reason)

    def _check_per_document_group(self, reasons: list[str]) -> None:
        """Per-document's denominator is the accessions fetched or read from
        the per-document cache this run (#610 policy 1; a co-registrant's
        cached copy, `entity_cik != cik`, counts too), plus the quarantined
        accessions it skipped; a quarantined accession counts as a failure
        unless its entry is `accepted` (a failed check can quarantine, #610
        policy 2, and quarantine never excuses on its own). A `facts()`
        collision counts toward `_check_cross_day_pairs` only, never here. A
        pending failure is excused only by an entry accepted for its same
        error class and message."""
        failures = sum(
            1
            for accession, (error_class, _form, message) in self._pending_failures.items()
            if accession not in self._collision_failures
            and not self._accepted(accession, error_class, message)
        )
        # A quarantined accession is a failure no request re-checked this run:
        # it counts unless its stored entry is accepted (#610 review), even when
        # another of its documents (a cached header) put it in the attempted
        # set; one this run already failed is counted above, never twice.
        store = self._failure_store()
        failures += sum(
            1
            for accession in self._quarantined_this_run - self._pending_failures.keys()
            if not store.get(accession, {}).get("accepted", False)
        )
        denominator = len(self._per_document_attempted | self._quarantined_this_run)
        reason = self._threshold_reason("per-document", failures, denominator)
        if reason:
            reasons.append(reason)

    def _check_cross_day_pairs(self, reasons: list[str]) -> None:
        """One (error class, base form) pair with at least
        `edgar.min_failed_filings` distinct accessions, `accepted: true`
        entries excluded, across `failed_filings.json` and this run's
        not-yet-recorded failures. FSN manifest failures are not pooled here
        (#610 policy 3): `_check_fsn_group`'s share rule alone judges them."""
        pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
        store = self._failure_store()
        for accession, entry in store.items():
            if accession in self._pending_failures:
                continue  # judged below, by this run's own error
            if not entry.get("accepted", False):
                pairs[(entry["error_class"], entry["base_form"])].add(accession)
        for accession, (error_class, base_form, message) in self._pending_failures.items():
            if not self._accepted(accession, error_class, message):
                pairs[(error_class, base_form)].add(accession)
        min_n = self._settings.edgar.min_failed_filings
        for (error_class, base_form), accessions in pairs.items():
            if len(accessions) >= min_n:
                reasons.append(
                    f"{error_class}/{base_form}: {len(accessions)} accessions across days"
                )

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
        the winner; the same key with different values in two sources (FSN
        compared at its 4 decimal places, #610 X1), or a NaN or infinite
        value in any source, even the only one (#749), is a collision (T11h):
        only that (accession, fact name, class member) key
        is withheld, recorded under the accession's base form, and it counts
        toward `check_failures`'s cross-day (error class, base form) rule
        only; the accession's other facts are still served.
        """
        _validate_cik(cik)
        self._ensure_fsn()
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
                as_of = max(ends) if ends else min(share.as_of_date, eastern)
                put(
                    record.accession,
                    "fsn",
                    _CachedFact(_FSN_SHARES_TAG, as_of, share.class_member, share.value),
                )

        out: list[FactRecord] = []
        for (accession, fact_name, member), sources in by_key.items():
            label = f"{accession}: {fact_name} {member or 'undimensioned'}"
            base_form = stamps[accession].form.removesuffix("/A")
            try:
                dated: dict[str, dict[date, float]] = {}
                for source, facts in sources.items():
                    for fact in facts:
                        if not math.isfinite(fact.value):
                            # #749: a key only one source supplies is never
                            # compared by `_same_value`, so check it here.
                            raise ValueError(
                                f"{label} {source} value on {fact.as_of_date} is not finite"
                            )
                        held = dated.setdefault(source, {}).setdefault(fact.as_of_date, fact.value)
                        if held != fact.value:  # one source, one date, two values
                            raise ValueError(
                                f"{label} has two {source} values on {fact.as_of_date}"
                            )
                for a, b in itertools.combinations(sorted(dated), 2):
                    common = dated[a].keys() & dated[b].keys()
                    if common:  # comparable dates must agree, value for value
                        clash = [
                            d for d in common if not _same_value(a, dated[a][d], b, dated[b][d])
                        ]
                        if clash:
                            days = ", ".join(d.isoformat() for d in sorted(clash))
                            raise ValueError(f"{label} differs between {a} and {b} on {days}")
                    elif not any(
                        _same_value(a, x, b, y)
                        for x in dated[a].values()
                        for y in dated[b].values()
                    ):
                        raise ValueError(f"{label} differs between {a} and {b}: no value in common")
            except ValueError as error:
                # T11h: a collision withholds only this key, never the whole
                # accession; recorded under its base form for the cross-day
                # rule only, never `_check_per_document_group`'s numerator.
                self._record_failure(
                    accession, type(error).__name__, base_form, str(error), collision=True
                )
                continue
            winner = next(s for s in ("document", "company", "fsn") if s in sources)
            accepted_at = stamps[accession].accepted_at
            if accepted_at is None:  # every source above is stamped; a bug otherwise
                raise RuntimeError(f"{label} reached the output without a stamp")
            for as_of, value in dated[winner].items():
                out.append(FactRecord(cik, fact_name, as_of, member, value, accession, accepted_at))
        out.sort(
            key=lambda f: (f.accepted_at, f.accession, f.fact_name, f.class_member, f.as_of_date)
        )
        return out

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        """As-filed statement facts (amendment 2026-10-03, #660; module
        docstring "Statement facts"): `[]` with no I/O while
        `edgar.statement_facts_enabled` is off; otherwise the CIK's cached
        entries stamped at read time, in the parser's order."""
        _validate_cik(cik)
        if not self._settings.edgar.statement_facts_enabled:
            return []
        self._ensure_fsn()
        stamps = self._load_stamps(cik)
        cached = self._statement_cache(cik, stamps)
        if cached is None:
            return []  # no stamped cover-form accession: nothing asked, as `facts`
        records, conflicts, unstampable = self._stamp_statement(cik, cached, stamps)
        if cik not in self._statement_counted:
            self._statement_counted.add(cik)
            self.statement_conflicts += len(conflicts)
            self.statement_unstampable += unstampable
        listed = set(self.statement_conflict_keys)
        self.statement_conflict_keys.extend(c for c in conflicts if c not in listed)
        return records

    def _statement_cache_path(self, cik: str) -> Path:
        return self._cache / "statement_facts" / f"v{STATEMENT_VERSION}" / f"{cik}.json"

    def _statement_key(self, accession: str) -> str:
        """The statement cache key: `accession` and a digest of the
        configured tags (with their order, the precedence) and units."""
        edgar = self._settings.edgar
        config = json.dumps([edgar.statement_tags, edgar.statement_units])
        return f"{accession}|{hashlib.sha256(config.encode('utf-8')).hexdigest()[:16]}"

    def _statement_cache(
        self, cik: str, stamps: Mapping[str, SubmissionRecord]
    ) -> StatementFactsParse | None:
        """`cik`'s unstamped statement entries: the cache file when it was
        filled this run (never a request) or its key is current, else
        parsed from the company-facts payload; `None` with no stamped
        cover-form accession."""
        path = self._statement_cache_path(cik)
        written = self._statement_filled.get(cik)
        if written is not None:
            cached = _load_statement_cache(path, cik, written)
            if cached is None:
                raise RuntimeError(f"{path}: filled this run but no longer reads")
            return cached
        latest = self._facts_cache_key(stamps, set(self._settings.edgar.cover_page_forms))
        if latest is None:
            return None
        key = self._statement_key(latest)
        cached = _load_statement_cache(path, cik, key)
        if cached is not None:
            self._statement_filled[cik] = key
            return cached
        payload, complete, origin = self._company_facts_payload(cik, latest)
        return self._fill_statement_cache(cik, stamps, latest, payload, complete, origin)

    def _fill_statement_cache(
        self,
        cik: str,
        stamps: Mapping[str, SubmissionRecord],
        latest: str,
        payload: Any | None,
        complete: bool,
        origin: str | None,
    ) -> StatementFactsParse:
        """Parse `payload` into `cik`'s statement cache and write it, keyed
        by `latest` when the payload holds it, else by the latest stamped
        cover-form accession it reached; count the parse-time skips. A
        payload that does not parse is recorded under `origin` and cached
        empty under a key no run matches."""
        edgar = self._settings.edgar
        tags, forms, units = edgar.statement_tags, edgar.statement_forms, edgar.statement_units

        def parse() -> tuple[StatementFactsParse, StatementFactsParse, bool] | None:
            if payload is None:  # no XBRL facts at all, or recorded upstream
                return None
            return (
                parse_statement_facts(payload, tags, forms, units, {}),
                parse_statement_facts(payload, tags, forms, units, stamps),
                _carries_a_tag(payload, tags),
            )

        empty = StatementFactsParse((), (), 0, 0)
        if origin is None:
            parsed = parse()
        else:  # #578: a payload `parse_statement_facts` refuses is recorded, absent
            parsed = self.validation_failures.collect(origin, f"CIK{cik}.json", parse)
        if parsed is None:
            entries = empty
            reached = latest if complete and payload is None else ""
        else:
            entries, stamped, carries = parsed
            self.statement_non_usd += stamped.non_unit
            self.statement_malformed += stamped.malformed
            if not carries:
                self.statement_none += 1
            reached = latest if complete else _reached(payload, stamps, edgar.cover_page_forms)
        key = self._statement_key(reached)
        data = {
            "version": STATEMENT_VERSION,
            "cik": cik,
            "key": key,
            "records": [_statement_record_to_json(r) for r in entries.records],
            "conflicts": [_statement_conflict_to_json(c) for c in entries.conflicts],
        }
        edgar_raw.write_atomic(self._statement_cache_path(cik), json.dumps(data).encode("utf-8"))
        self._statement_filled[cik] = key
        return entries

    def _stamp_statement(
        self, cik: str, cached: StatementFactsParse, stamps: Mapping[str, SubmissionRecord]
    ) -> tuple[list[StatementFactRecord], list[StatementConflict], int]:
        """Stamp cached entries from `stamps` (module docstring): the kept
        records, the kept conflicts, and how many records an accession
        settled as unstampable dropped."""
        forms = frozenset(self._settings.edgar.statement_forms)
        fsn_cache = self._load_fsn_cache(cik)
        verdicts: dict[str, bool] = {}
        unstampable = 0

        def foreign(accession: str) -> bool:
            """The co-registrant filters of `_company_facts`."""
            if accession not in verdicts:
                cover = self._load_cover_cache(accession)
                verdicts[accession] = (
                    accession in self._fsn_extracted_accessions and accession not in fsn_cache
                ) or (cover is not None and cover.entity_cik != cik)
            return verdicts[accession]

        def kept(accession: str, end: date) -> bool:
            record = stamps.get(accession)
            if foreign(accession):
                return False
            if record is None:
                return True  # no stamp record yet: emitted unstamped, for the hold rule
            if record.form not in forms or record.accepted_at is None:
                return False
            return end <= record.accepted_at.astimezone(_EASTERN).date()

        records: list[StatementFactRecord] = []
        for r in cached.records:
            if kept(r.accession, r.period_end):
                records.append(r)
                continue
            stamp = stamps.get(r.accession)
            settled = stamp is not None and stamp.accepted_at is None and stamp.form in forms
            if settled and not foreign(r.accession):
                unstampable += 1
        conflicts = [c for c in cached.conflicts if kept(c.accession, c.period_end)]
        latest: dict[tuple[str, str, int], date] = {}
        items: list[StatementFactRecord | StatementConflict] = [*records, *conflicts]
        for item in items:
            group = (item.accession, item.fact_name, item.period_days)
            latest[group] = max(latest.get(group, item.period_end), item.period_end)
        out: list[StatementFactRecord] = []
        for r in records:
            stamp = stamps.get(r.accession)
            out.append(
                replace(
                    r,
                    form="" if stamp is None else stamp.form,
                    accepted_at=None if stamp is None else stamp.accepted_at,
                    comparative=r.period_end < latest[(r.accession, r.fact_name, r.period_days)],
                )
            )
        out.sort(
            key=lambda r: (
                r.accepted_at is None,
                r.accepted_at or datetime.min.replace(tzinfo=UTC),
                r.accession,
                r.fact_name,
                r.period_end,
                r.period_days,
            )
        )
        return out, conflicts, unstampable

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
        latest = self._facts_cache_key(stamps, set(self._settings.edgar.cover_page_forms))
        if latest is None:
            return {}
        key = _facts_key(latest, wanted)
        path = self._cache / "facts" / f"v{PARSER_VERSION}" / f"{cik}.json"
        cached = self._facts_memo.get((cik, key))
        if cached is None:  # an empty memo is an answer too: asked once per run (#578)
            cached = _load_facts_cache(path, cik, key)
        if cached is None:
            payload, complete, origin = self._company_facts_payload(cik, latest)

            def parse() -> _FactsCache:
                if payload is None:  # T11h/T11e: no XBRL facts at all; not a filing failure
                    return []
                return [
                    (f.fact_name, f.accession, f.as_of_date, f.value)
                    for f in parse_company_facts(payload, wanted, _EveryAccession()).facts
                ]

            recorded = False
            if origin is not None:  # #578: a payload that does not parse is recorded, absent
                parsed = self.validation_failures.collect(origin, f"CIK{cik}.json", parse)
                cached, complete = ([], False) if parsed is None else (parsed, complete)
                recorded = parsed is None
            else:
                cached = parse()
            self._facts_memo[cik, key] = cached  # one fetch per CIK per run, cached or not
            statement_path = self._statement_cache_path(cik)
            if (
                self._settings.edgar.statement_facts_enabled
                and cik not in self._statement_filled
                and _cached_key(statement_path, cik, STATEMENT_VERSION)
                != self._statement_key(latest)
            ):  # T77a: the one payload read serves the statement cache too
                if recorded:  # absent for both caches, and recorded once only
                    self._fill_statement_cache(cik, stamps, latest, None, False, None)
                else:
                    self._fill_statement_cache(cik, stamps, latest, payload, complete, origin)
            if complete:  # else the payload trails the latest filing: fetch again next run
                rows = [[n, a, d.isoformat(), v] for n, a, d, v in cached]
                data = {"version": PARSER_VERSION, "cik": cik, "key": key, "facts": rows}
                edgar_raw.write_atomic(path, json.dumps(data).encode("utf-8"))
        fsn_cache = self._load_fsn_cache(cik)
        # (accession, name, capped date) -> [(value, dated after acceptance)]
        kept: dict[tuple[str, str, date], list[tuple[float, bool]]] = {}
        for name, accession, as_of, value in cached:
            record = stamps.get(accession)
            if record is None or record.accepted_at is None or name not in wanted:
                continue
            if accession in self._fsn_extracted_accessions and accession not in fsn_cache:
                continue  # FSN holds it under another CIK: a co-registrant's combined filing
            cover = self._load_cover_cache(accession)
            if cover is not None and cover.entity_cik != cik:
                continue  # the per-document parse names another entity
            eastern = record.accepted_at.astimezone(_EASTERN).date()
            capped = min(as_of, eastern)  # an XBRL date typo never dates a fact after its stamp
            kept.setdefault((accession, name, capped), []).append((value, as_of > eastern))
        out: dict[str, list[_CachedFact]] = {}
        for (accession, name, capped), values in kept.items():
            reported = {value for value, after in values if not after}
            if reported and any(after and v not in reported for v, after in values):
                # #610 X2 (owner, 2026-10-02): capping made a value dated after
                # acceptance collide with one the filing reports on that date;
                # drop the later-dated one, keep what the filing reports.
                self.facts_capped_dropped.add((accession, name, capped))
                values = [(v, after) for v, after in values if not after or v in reported]
            for value, _after in values:
                out.setdefault(accession, []).append(_CachedFact(name, capped, "", value))
        return out

    def _stale_fact_caches(self) -> int:
        """Stamped CIKs whose facts cache, or (only while the switch is on,
        #660) statement cache, is not keyed by their latest cover-form
        accession: the bulk decision's count."""
        statements = self._settings.edgar.statement_facts_enabled
        cover_forms = set(self._settings.edgar.cover_page_forms)
        stale = 0
        for stamps_path in self._stamps_path("0").parent.glob("*.json"):
            other = stamps_path.stem
            if not _CIK_PATTERN.fullmatch(other):
                continue  # not a stamps file
            other_latest = self._facts_cache_key(self._load_stamps(other), cover_forms)
            if other_latest is None:
                continue
            cache = self._cache / "facts" / f"v{PARSER_VERSION}" / f"{other}.json"
            if _cached_latest(cache, other) != other_latest or (
                statements
                and _cached_key(self._statement_cache_path(other), other, STATEMENT_VERSION)
                != self._statement_key(other_latest)
            ):
                stale += 1
        return stale

    def _company_facts_payload(self, cik: str, latest: str) -> tuple[Any | None, bool, str | None]:
        """The raw company-facts payload, whether it already holds
        `latest` (the filing the cache is keyed by) and the validation input
        it came from (`_FACTS_MEMBER`, `_FACTS_API`, or `None` with no
        payload): from one
        `companyfacts.zip` when more than `edgar.bulk_stamp_threshold_ciks`
        stamped CIKs need a fetch (the stamping rule), else the per-CIK API;
        a CIK the zip lacks, or whose zip payload trails `latest` (the zip is
        rebuilt nightly), falls back to the API. A payload for another CIK
        is recorded (below). A 404 from the per-CIK API (T11h, T11e's leftover: many
        issuers have no XBRL facts at all) is **not** a filing failure: the
        payload is `None`, complete `True` (an empty result is cached). An
        API 200 whose payload is an empty object (#576: SEC's answer for the
        CIKs whose zip member is `{}`) is handled exactly like that 404 and
        counted on `facts_api_empty`; any other payload without `cik` or
        `facts` is recorded (below). A payload that has `facts` but no `cik` (#599:
        SEC ships this shape for a few CIKs, identically from the zip and the
        API) is identified by the CIK it was requested under (the zip member
        name, or the API URL) rather than treated as absent, and counted on
        `facts_bulk_keyless`/`facts_api_keyless`; a `cik` that is present but
        differs is recorded.

        A zip member or API payload that does not parse (not JSON, or
        another CIK's facts, or missing the fields `_holds_accession` reads)
        is recorded on `.validation_failures` (#578 parts 2 and 3) and is
        absent for this run: no facts from company facts and nothing cached;
        for a zip member the per-CIK API is not asked, since its answer would
        likely share the shape (#599). The caller records a payload that
        `parse_company_facts` refuses the same way, under its origin."""
        if self._facts_bulk is None:
            if (
                self._reuse_cached
                or self._stale_fact_caches() > self._settings.edgar.bulk_stamp_threshold_ciks
            ):
                path = edgar_raw.bulk_company_facts(
                    settings=self._settings, client=self._client, reuse_cached=self._reuse_cached
                )
                with zipfile.ZipFile(path) as bulk:
                    self._facts_bulk = (path, frozenset(bulk.namelist()))
            else:
                self._facts_bulk = False
        bulk_payload: Any | None = None
        if isinstance(self._facts_bulk, tuple) and f"CIK{cik}.json" in self._facts_bulk[1]:
            bulk_path = self._facts_bulk[0]

            def member() -> tuple[Any | None, str, bool]:
                """The member's payload (`None` when `{}`), its shape
                ("empty", "keyless" or "keyed") and whether it holds `latest`."""
                with zipfile.ZipFile(bulk_path) as bulk:
                    payload = json.loads(bulk.read(f"CIK{cik}.json"))
                if not _keyless_member(payload):
                    return payload, "keyed", _holds_accession(payload, cik, latest)
                if "facts" not in payload:
                    return None, "empty", False
                payload = _identified(payload, cik)
                return payload, "keyless", _holds_accession(payload, cik, latest)

            read = self.validation_failures.collect(_FACTS_MEMBER, f"CIK{cik}.json", member)
            if read is None:
                return None, False, None
            bulk_payload, shape, holds = read
            if shape == "keyless":
                # #599: has facts despite missing `cik`; identified by the
                # zip member name it was read under.
                self.facts_bulk_keyless += 1
            elif shape == "empty":
                # #566: SEC's zip has `{}` members. As if absent from the
                # zip: the API is asked, never "no facts" assumed.
                self.facts_bulk_empty += 1
            if holds:
                return bulk_payload, True, _FACTS_MEMBER
        try:
            payload = edgar_raw.company_facts(cik, settings=self._settings, client=self._client)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
            absent = True
        except _UNDECODABLE as error:  # #578: a body that is not JSON; absent, nothing cached
            self.validation_failures.record(_FACTS_API, f"CIK{cik}.json", error)
            return None, False, None
        else:
            absent = _empty_object(payload)
            if absent:
                self.facts_api_empty += 1  # #576: SEC's 200 `{}` is its 404
        if absent:
            if bulk_payload is not None:
                # The zip has this CIK's facts, only trailing `latest`: serve
                # them, marked incomplete so the next run asks again (#275).
                return bulk_payload, False, _FACTS_MEMBER
            self.facts_missing += 1
            return None, True, None  # no XBRL facts at all: an empty result is cached
        # #599: as above, identified by the API URL it was requested under.
        keyless = _keyless_member(payload) and "facts" in payload
        if keyless:
            payload = _identified(payload, cik)
        held = self.validation_failures.collect(
            _FACTS_API, f"CIK{cik}.json", partial(_holds_accession, payload, cik, latest)
        )
        if held is None:
            return None, False, None
        if keyless:
            self.facts_api_keyless += 1
        return payload, held, _FACTS_API


#: What fails an FSN period whole (#578): a parser refusal, or DuckDB failing
#: a member's read on a malformed line (#455, #498). DuckDB's I/O errors
#: still propagate.
_FSN_PARSE_ERRORS: tuple[type[Exception], ...] = (*PARSE_ERRORS, duckdb.InvalidInputException)

#: The validation collector's input names for company facts and per-CIK
#: submissions (#578 parts 2 and 3).
_FACTS_MEMBER = "companyfacts.zip member"
_FACTS_API = "companyfacts API"
_SUBMISSIONS_API = "submissions API"
#: What `response.json()` raises on a per-CIK API body that is not JSON
#: (#578 part 3): recorded like any other parse failure of that input.
_UNDECODABLE: tuple[type[Exception], ...] = (json.JSONDecodeError, UnicodeDecodeError)
#: `_fetch_submissions`'s marker for a recorded undecodable body.
_NOT_JSON = object()


def _message_hash(message: str) -> str:
    """The `failed_filings.json` key `accepted` is bound to (with the error
    class): the SHA-256 of the raw, unredacted message."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _same_value(a_source: str, a: float, b_source: str, b: float) -> bool:
    """Two sources' values for one key and date agree: exactly, or, when one
    side is FSN, within half a unit of FSN's 4th decimal place (#610 X1:
    105.1597 from FSN agrees with company facts' 105.159666). A NaN or an
    infinity never agrees (#629)."""
    if not (math.isfinite(a) and math.isfinite(b)):
        # #629: a NaN or an infinity never agrees, so the key is withheld;
        # `Decimal` would raise `InvalidOperation` and fail the whole source.
        return False
    if "fsn" not in (a_source, b_source):
        return a == b
    return abs(Decimal(repr(a)) - Decimal(repr(b))) <= _FSN_HALF_UNIT


def _empty_object(payload: Any) -> bool:
    """Whether a per-CIK API payload is an empty JSON object (#576: SEC
    answers 200 `{}` for some CIKs)."""
    return isinstance(payload, dict) and not payload


def _keyless_member(payload: Any) -> bool:
    """Whether a bulk zip's `CIK##########.json` member, or a per-CIK API
    payload, is an object with no `cik` (SEC ships empty `{}` members, #566,
    and a handful of non-empty payloads that carry `facts` but no `cik`,
    #599). Anything that is not an object is left to fail downstream; the
    caller tells the two keyless shapes apart by `"facts" in payload`."""
    return isinstance(payload, dict) and "cik" not in payload


def _identified(payload: Mapping[str, Any], cik: str) -> dict[str, Any]:
    """`payload` with its requested CIK substituted for `cik` (#599): when SEC
    omits `cik` from a payload that otherwise carries `facts`, the zip member
    name or API URL it was requested under is its only identity. Downstream
    readers (`_holds_accession`, `parse_company_facts`) then index
    `payload["cik"]` as usual, so the facts parsed from it carry `cik`."""
    return {**payload, "cik": cik}


def _holds_accession(payload: Any, cik: str, accession: str) -> bool:
    """Whether the company-facts `payload` (checked to be `cik`'s) carries any
    entry, of any concept, for `accession`."""
    if str(payload["cik"]).zfill(10) != cik:
        raise ValueError(f"company facts for CIK {payload['cik']!r} served for {cik}")
    try:
        return any(
            entry["accn"] == accession
            for concepts in payload["facts"].values()
            for concept in concepts.values()
            for entries in concept["units"].values()
            for entry in entries
        )
    except AttributeError as error:  # a list or string where an object belongs (#578)
        raise ValueError(f"company facts: malformed: {error!r}") from error


def _facts_key(latest: str, wanted: set[str]) -> str:
    """The facts cache key: the latest cover-form accession and the names
    asked for, so a new filing or a new name re-fetches."""
    return f"{latest}|{','.join(sorted(wanted))}"


_CIK_PATTERN = re.compile(r"\d{10}")


def _validate_cik(cik: str) -> None:
    if not isinstance(cik, str) or not _CIK_PATTERN.fullmatch(cik):
        raise ValueError(f"cik must be a 10-digit zero-padded string, got {cik!r}")


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


def _cached_latest(path: Path, cik: str) -> str | None:
    """The latest cover-form accession a facts cache file was keyed by, or
    None when there is no usable cache (the bulk decision's staleness probe,
    which cannot know the names a later `facts` call will ask for)."""
    try:
        data = json.loads(path.read_bytes())
        if data["version"] != PARSER_VERSION or data["cik"] != cik:
            return None
        return str(data["key"]).split("|", 1)[0]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cached_key(path: Path, cik: str, version: int) -> str | None:
    """The key a per-CIK cache file was written under, or None when there is
    no usable file (absent, truncated, another version or CIK)."""
    try:
        data = json.loads(path.read_bytes())
        if data["version"] != version or data["cik"] != cik:
            return None
        return str(data["key"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _carries_a_tag(payload: Mapping[str, Any], tags: Mapping[str, Sequence[str]]) -> bool:
    """Whether the payload carries any configured `taxonomy:tag` at all
    (`statement_none` counts the CIKs that carry none)."""
    facts = payload["facts"]
    return any(
        tag.partition(":")[2] in facts.get(tag.partition(":")[0], {})
        for fallbacks in tags.values()
        for tag in fallbacks
    )


def _reached(
    payload: Any | None, stamps: Mapping[str, SubmissionRecord], cover_forms: Sequence[str]
) -> str:
    """The latest stamped cover-form accession a trailing payload holds, or
    `""`: the key a trailing member's statement cache is written under."""
    if payload is None:
        return ""
    held = {
        entry["accn"]
        for concepts in payload["facts"].values()
        for concept in concepts.values()
        for entries in concept["units"].values()
        for entry in entries
    }
    bases = set(cover_forms)
    candidates = [
        r
        for r in stamps.values()
        if r.accession in held and r.accepted_at is not None and r.form.removesuffix("/A") in bases
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda r: (r.accepted_at, r.accession)).accession


def _statement_record_to_json(record: StatementFactRecord) -> list[object]:
    """An unstamped cached entry: its form, stamp and `comparative` are
    the read's (module docstring "Statement facts")."""
    start = None if record.period_start is None else record.period_start.isoformat()
    return [
        record.fact_name,
        record.xbrl_tag,
        start,
        record.period_end.isoformat(),
        record.value,
        record.unit,
        record.accession,
        record.filed.isoformat(),
    ]


def _statement_conflict_to_json(conflict: StatementConflict) -> list[object]:
    start = None if conflict.period_start is None else conflict.period_start.isoformat()
    return [
        conflict.accession,
        conflict.fact_name,
        start,
        conflict.period_end.isoformat(),
        list(conflict.values),
    ]


def _optional_date(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def _load_statement_cache(path: Path, cik: str, key: str) -> StatementFactsParse | None:
    """The cached unstamped entries and conflicts, or None when the file is
    unusable or keyed otherwise."""
    try:
        data = json.loads(path.read_bytes())
        if data["version"] != STATEMENT_VERSION or data["cik"] != cik or data["key"] != key:
            return None
        records = tuple(
            StatementFactRecord(
                cik=cik,
                fact_name=str(name),
                xbrl_tag=str(tag),
                period_start=_optional_date(start),
                period_end=date.fromisoformat(str(end)),
                value=float(value),
                unit=str(unit),
                form="",
                accession=str(accession),
                accepted_at=None,
                filed=date.fromisoformat(str(filed)),
                comparative=False,
            )
            for name, tag, start, end, value, unit, accession, filed in data["records"]
        )
        conflicts = tuple(
            StatementConflict(
                str(accession),
                str(name),
                _optional_date(start),
                date.fromisoformat(str(end)),
                tuple(float(v) for v in values),
            )
            for accession, name, start, end, values in data["conflicts"]
        )
        return StatementFactsParse(records, conflicts, 0, 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None


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


@dataclass(frozen=True, slots=True)
class _CachedDelisting:
    """A per-document `parse_delisting` result cached under
    `DELISTING_VERSION`, its stamp stripped (re-applied from `_stamp` at
    read time, as cover pages and headers are)."""

    cik: str
    form: str
    class_title: str
    exchange: str
    effective_on: date | None


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
    free_text: str | None = None,
) -> list[dict[str, str]]:
    """`columns` of `path` (a tab-separated FSN member) as plain string
    dicts, via DuckDB `read_csv` with every column read as `varchar` (FSN's
    own convention: numeric-looking columns like `cik` can have leading
    zeros truncated otherwise) and no quoting (FSN's fields are never
    quoted, and a bare `"` inside a `txt.tsv` value must not start one).
    A zero-byte member (no header row at all -- a real FSN file never is
    one, but a test fixture may be) has no columns to select and yields no
    rows rather than a DuckDB binder error.

    DuckDB fails the whole read on one malformed line, so the member is
    first rewritten in place by `_fsn_normalize_member` (#455, #498);
    `free_text` names the member's free-text column whose literal tabs may
    be folded (`txt.tsv`'s `value`, `dim.tsv`'s `segments`), and is `None`
    for every other member, whose surplus tabs still fail the read."""
    if path.stat().st_size == 0:
        return []
    _fsn_normalize_member(path, free_text=free_text)
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


def _fsn_normalize_member(path: Path, *, free_text: str | None = None) -> None:
    """Rewrite `path`, streamed (an FSN member reaches hundreds of MB), so
    DuckDB can read it; anything else is kept byte for byte.

    - An invalid UTF-8 byte becomes U+FFFD (FSN members are not always valid
      UTF-8; #455).
    - With `free_text` (which must name a header column), a line with more
      fields than the header keeps its surplus in that column, each surplus
      tab replaced by U+FFFD: a 2015 `txt.tsv` note carries unquoted tabs in
      `value`, its last column, and a 2025 `dim.tsv` InvestmentIdentifier
      in `segments`, its middle one (#498). The U+FFFD makes `parse_fsn`
      fail an accession whose kept listing value or class member had one.
      A line with fewer fields, or any surplus without `free_text`, is left
      for DuckDB to fail loudly: folding a column that is not free text
      would shift fields silently.
    """
    cleaned = path.with_name(path.name + ".normalized")
    with (
        path.open(encoding="utf-8", errors="replace", newline="") as source,
        cleaned.open("w", encoding="utf-8", newline="") as target,
    ):
        header = source.readline()
        target.write(header)
        names = header.rstrip("\r\n").split("\t")
        if free_text is not None and free_text not in names:
            raise ValueError(f"{path.name}: no {free_text!r} column to fold tabs into")
        column = names.index(free_text) if free_text is not None else -1
        tabs = len(names) - 1
        for line in source:
            if column >= 0 and line.count("\t") > tabs:
                body = line.rstrip("\r\n")
                fields = body.split("\t")
                surplus = len(fields) - len(names)
                folded = "\ufffd".join(fields[column : column + surplus + 1])
                fields[column : column + surplus + 1] = [folded]
                line = "\t".join(fields) + line[len(body) :]
            target.write(line)
    cleaned.replace(path)


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

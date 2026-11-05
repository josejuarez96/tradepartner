"""Drive the Form 4 bulk route over a selected issuer accession queue.

The caller supplies the SEC operations and the atomic accession writer. This keeps
the route testable without network access; production operations use EdgarFilingSource,
whose requests all pass through edgar_raw's shared limiter.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import SEC_FORMS_PARSER_VERSION, Form4Transaction
from tradepartner.adapters.edgar_insider_bulk import (
    BulkAccession,
    InsiderBulk,
    InsiderBulkUnreadableError,
    read_insider_bulk,
)
from tradepartner.adapters.filings import SecFormFiling
from tradepartner.adapters.form4_compare import (
    Declaration,
    Disagreement,
    RunTally,
    compare_rows,
)
from tradepartner.adapters.form4_owner_route import refusal as owner_refusal
from tradepartner.adapters.form4_route import (
    IDENTITY_BAD,
    AccessionRoute,
    Quarter,
    QuarterState,
    Route,
    order_sample,
    quarter_of,
    quarter_state,
    route_accession,
    rows_agree,
)
from tradepartner.config import Settings

Form4IndexRow = tuple[str, str, str, date]  # accession, issuer CIK, form, filing date
BULK_READER_VERSION = 4  # Recheck order samples after multi-owner exclusion (#2711).


#: The manifest entry kind of a rule 5 review marker (plan T351, #2714 item 6). It is
#: not an XML or bulk row source, so `find_entry`, `prior_xml_hashes` and
#: `rechecked_against` pass over it.
MARKER_SOURCE = "form4_compare_marker"


@dataclass
class Rule5:
    """The run's rule 5 comparison (plan T351): the declaration, the run's tally and the
    latest disagreements of each compared accession. Shared by every quarter's route
    call, so the share is over the whole run. An unset threshold reports only: the order
    sample keeps its refusal and nothing is written differently."""

    declaration: Declaration
    tally: RunTally = field(default_factory=RunTally)
    found: dict[str, tuple[Disagreement, ...]] = field(default_factory=dict)

    @property
    def enforcing(self) -> bool:
        """Whether the threshold key is set."""
        return self.declaration.threshold is not None

    def record(
        self,
        accession: str,
        bulk_rows: tuple[Form4Transaction, ...],
        xml_rows: tuple[Form4Transaction, ...],
    ) -> tuple[Disagreement, ...]:
        """Compare one XML-fetched accession, count it once and keep its latest result."""
        disagreements = compare_rows(bulk_rows, xml_rows, self.declaration)
        self.tally.add(accession, disagreements)
        if disagreements:
            self.found[accession] = disagreements
        else:
            self.found.pop(accession, None)
        return disagreements


class Form4CompareThresholdError(RuntimeError):
    """The share of disagreeing XML-fetched accessions reached the threshold: the source
    run fails (plan T351, #2714 item 6)."""


def comparable(record: BulkAccession | None, cik: str, multi_owner_xml: bool) -> bool:
    """Whether an accession's bulk rows are a fair side of the rule 5 comparison: the bulk
    set names this issuer, its identity and order checks passed, and (with multi-owner XML
    routing on) it has one owner, since a multi-owner accession's bulk owner join is
    SEC's known defect (#2706) and its rows never reach the store from bulk."""
    return (
        record is not None
        and record.issuer_cik == cik
        and not record.identity_bad
        and not record.order_bad
        and not (multi_owner_xml and len(set(record.filer_ciks)) > 1)
    )


def trusted_filing(
    filing: SecFormFiling, bulk_rows: tuple[Form4Transaction, ...], decl: Declaration
) -> SecFormFiling:
    """The XML `filing` with each field the declaration trusts to the bulk set taken from
    the bulk row of the same `(filer_cik, line_no)`. When the key sets differ (an ordering
    or ownership difference) the rows are wholly the `line_no` rule's trusted source's.
    The default declaration trusts the XML everywhere, so the filing comes back as is."""
    bulk_by_key = {(r.filer_cik, r.line_no): r for r in bulk_rows}
    xml_keys = {(r.filer_cik, r.line_no) for r in filing.transactions}
    if xml_keys != bulk_by_key.keys() or len(xml_keys) != len(filing.transactions):
        if decl.rules["line_no"].trusted == "bulk":
            return replace(filing, transactions=bulk_rows)
        return filing
    names = [name for name, rule in decl.rules.items() if rule.trusted == "bulk"]
    if not names:
        return filing
    rows = tuple(
        replace(row, **{n: getattr(bulk_by_key[row.filer_cik, row.line_no], n) for n in names})
        for row in filing.transactions
    )
    return replace(filing, transactions=rows)


def marker_entries(
    accession: str,
    quarter: str | None,
    zip_sha256: str | None,
    disagreements: tuple[Disagreement, ...],
) -> list[dict[str, object]]:
    """Manifest entries for one compared accession: one unresolved marker per disagreeing
    field (both values, the trusted choice, the checked bulk-zip SHA-256), or one
    resolved marker when the sources now agree."""
    base: dict[str, object] = {
        "row_source": MARKER_SOURCE,
        "accession": accession,
        "quarter": quarter,
        "zip_sha256": zip_sha256,
    }
    if not disagreements:
        return [{**base, "status": "resolved"}]
    return [
        {
            **base,
            "status": "unresolved",
            "field": d.field,
            "row_key": None if d.row_key is None else list(d.row_key),
            "bulk": None if d.bulk is None else str(d.bulk),
            "xml": None if d.xml is None else str(d.xml),
            "trusted": d.trusted,
        }
        for d in disagreements
    ]


def unresolved_markers(manifest: Mapping[str, object] | None) -> list[dict[str, object]]:
    """The unresolved marker entries of one parsed manifest (none for `None`)."""
    if manifest is None:
        return []
    entries = manifest.get("entries", ())
    return [
        dict(e)
        for e in entries  # type: ignore[attr-defined]
        if e.get("row_source") == MARKER_SOURCE and e.get("status") == "unresolved"
    ]


def offending(rule5: Rule5, limit: int = 20) -> str:
    """The disagreeing accessions with their fields, for a refusal message."""
    names = sorted(rule5.found)
    shown = "; ".join(
        f"{a}: {','.join(sorted({d.field for d in rule5.found[a]}))}" for a in names[:limit]
    )
    return shown + (f"; and {len(names) - limit} more" if len(names) > limit else "")


class RequestBudgetStopped(Exception):
    """The next SEC request would exceed the run's configured limit."""


@contextmanager
def request_budget(client: httpx.Client, count: Callable[[], int], limit: int) -> Iterator[None]:
    """Guard every request at the client hook, before edgar_raw sends it."""
    original = client.event_hooks

    def check(_request: httpx.Request) -> None:
        if count() >= limit:
            raise RequestBudgetStopped

    client.event_hooks = {
        **original,
        "request": [check, *original.get("request", [])],
    }
    try:
        yield
    finally:
        client.event_hooks = original


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _validators(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: headers[key] for key in ("last-modified", "etag", "content-length") if key in headers
    }


def insider_zip_path(settings: Settings, quarter: Quarter) -> Path:
    """The cached insider zip of `quarter` under `edgar.cache_dir`."""
    return (
        Path(settings.edgar.cache_dir) / "insider" / f"{quarter.year}q{quarter.number}_form345.zip"
    )


def insider_zip_fetch(
    settings: Settings, quarter: Quarter, sha256: str
) -> tuple[str | None, datetime | None]:
    """The URL and UTC fetch time `edgar_raw.insider_bulk_zip` recorded for the
    cached zip whose SHA-256 is `sha256` (plan T348), or `(None, None)` for a zip
    cached before that, an unreadable record, or a record of other bytes."""
    path = edgar_raw.insider_fetch_path(insider_zip_path(settings, quarter))
    try:
        record = json.loads(path.read_text())
        url, at = record["url"], datetime.fromisoformat(record["fetched_at"])
        recorded = record["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    if not isinstance(url, str) or at.utcoffset() is None or recorded != sha256:
        return None, None
    return url, at


def load_bulk_quarters(
    quarters: Iterable[Quarter],
    *,
    settings: Settings,
    client: httpx.Client,
    revalidate: bool = True,
) -> tuple[dict[Quarter, InsiderBulk | None], dict[Quarter, str], Counter[str], frozenset[Quarter]]:
    """Download or validate each quarter's zip through edgar_raw's shared limiter.

    The validator sidecar controls cache reuse. A malformed zip fails closed for its
    quarter, and the order-check key changes when its bytes or a parser changes.
    `revalidate=False` reads a cached zip with no request: the backfill's second read
    of a quarter it validated earlier in the same run (#2624).
    """
    base = Path(settings.edgar.cache_dir) / "insider"
    result: dict[Quarter, InsiderBulk | None] = {}
    digests: dict[Quarter, str] = {}
    counts: Counter[str] = Counter()
    unreadable: set[Quarter] = set()
    for quarter in sorted(set(quarters)):
        label = f"{quarter.year}q{quarter.number}"
        path = insider_zip_path(settings, quarter)
        sidecar = base / f"{label}_form345.validators.json"
        saved: dict[str, str] = {}
        try:
            loaded = json.loads(sidecar.read_text())
            if isinstance(loaded, dict) and all(
                isinstance(key, str) and isinstance(value, str) for key, value in loaded.items()
            ):
                saved = loaded
        except (OSError, ValueError):
            pass
        if not revalidate and not path.exists():
            unreadable.add(quarter)
            counts["sec_forms_bulk_unreadable"] += 1
            result[quarter] = None
            continue
        use_cache = not revalidate and path.exists()
        if not use_cache and path.exists() and saved:
            headers = edgar_raw.insider_bulk_validators(label, settings=settings, client=client)
            if headers is None:
                result[quarter] = None
                continue
            use_cache = bool(_validators(headers)) and _validators(headers) == saved
        if not use_cache:
            fetched = edgar_raw.insider_bulk_zip(label, settings=settings, client=client)
            if fetched is None:
                result[quarter] = None
                continue
            path, headers = fetched
            edgar_raw.write_atomic(sidecar, json.dumps(_validators(headers)).encode())
        try:
            result[quarter] = read_insider_bulk(
                path, max_member_bytes=settings.events.form4_bulk_max_member_bytes
            )
        except InsiderBulkUnreadableError:
            counts["sec_forms_bulk_unreadable"] += 1
            unreadable.add(quarter)
            result[quarter] = None
            continue
        digests[quarter] = _digest(path)
    return result, digests, counts, frozenset(unreadable)


def order_check_path(settings: Settings, quarter: Quarter, digest: str) -> Path:
    """Cache marker keyed by zip bytes and every parser version affecting parity.

    A marker from an older version (`BULK_READER_VERSION`, the parsers, the multi-owner
    switch) no longer matches, so the quarter's order is checked again on purpose
    (#2728): the reader that wrote the marker is not the one about to write bulk rows.
    The re-check is bounded to the leftovers: the sample is drawn only from the
    quarter's queued accessions (stored ones are skipped before the route, and a
    quarter with none queued is not read at all), at most `form4_bulk_order_sample`
    XML requests per quarter, and a passing sample writes the new marker. The sample
    is deterministic, so a quarter whose leftovers mismatch stays held each run.
    """
    from tradepartner.adapters.edgar_source import PARSER_VERSION

    label = f"{quarter.year}q{quarter.number}"
    version = (
        f"b{BULK_READER_VERSION}-x{SEC_FORMS_PARSER_VERSION}-s{PARSER_VERSION}"
        f"-m{int(settings.events.form4_multi_owner_xml)}"
    )
    return (
        Path(settings.edgar.cache_dir)
        / "insider"
        / "order_checks"
        / f"{label}-{digest}-{version}.ok"
    )


@dataclass(frozen=True)
class BulkRouteResult:
    """Counts and progress from one backfill pass."""

    counts: Counter[str]
    requests: int
    remaining: int

    def message(self) -> str:
        """Plain progress suitable for the ingestion run and the owner's terminal."""
        figures = ", ".join(f"{key}: {value}" for key, value in sorted(self.counts.items()))
        return f"Form 4 bulk: {self.requests} requests, {self.remaining} remaining; {figures}"


def run_form4_bulk_route(
    accessions: Iterable[Form4IndexRow],
    *,
    quarters: Mapping[Quarter, InsiderBulk | None],
    stamp: Callable[[str, str, date], datetime | tuple[datetime, str, date] | None],
    xml: Callable[[str, str, date], SecFormFiling | None],
    write: Callable[[SecFormFiling], object],
    write_bulk: Callable[[SecFormFiling, Quarter], object] | None = None,
    now: datetime,
    price_below: Decimal,
    zero_price_codes: tuple[str, ...],
    order_sample_size: int,
    budget: int,
    multi_owner_xml: bool = True,
    request_count: Callable[[], int] | None = None,
    checked: Callable[[Quarter], bool] | None = None,
    remember_check: Callable[[Quarter], None] | None = None,
    skip_accessions: frozenset[str] = frozenset(),
    blocked_quarters: frozenset[Quarter] = frozenset(),
    source_counts_failures: bool = False,
    accession_quarters: Mapping[str, Quarter] | None = None,
    rule5: Rule5 | None = None,
) -> BulkRouteResult:
    """Route selected Form 4 accessions, never substituting a bulk row after XML fails.

    A missing older quarter and an order mismatch stop bulk writes for that quarter.
    The acceptance stamp alone supplies `known_at`; the bulk filing date is only
    checked against the issuer submission record by `route_accession`.

    `rule5` (plan T351, #2714 item 6) compares each XML-fetched accession with its bulk
    rows (the order sample and the XML-routed filings, never a multi-owner accession's
    bulk owner join). With its threshold unset it only counts and the order sample keeps
    its refusal. With it set, a disagreement never blocks the quarter: the XML is stored
    (or, for a sampled accession later routed to bulk, its trusted row) and the driver
    writes the marker; the driver fails the run if the share reaches the threshold.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    # Issuer by issuer within each quarter, so the stamp seam reads each
    # issuer's stamps file once per quarter, not once per accession (#2632).
    queue = sorted(
        (row for row in set(accessions) if row[0] not in skip_accessions),
        key=lambda row: (row[1], row[3], row[0]),
    )
    grouped: dict[Quarter, list[Form4IndexRow]] = defaultdict(list)
    for row in queue:
        quarter = (
            accession_quarters[row[0]] if accession_quarters is not None else quarter_of(row[3])
        )
        grouped[quarter].append(row)
    counts: Counter[str] = Counter()
    requests = 0
    remaining = 0
    enforcing = rule5 is not None and rule5.enforcing
    sampled: dict[str, SecFormFiling] = {}  # sampled XML with a disagreement, for the write

    def spent() -> int:
        return request_count() if request_count is not None else requests

    for quarter, rows in sorted(grouped.items()):
        future = sum(len(items) for later, items in grouped.items() if later > quarter)

        def stopped(
            unprocessed: int, prior_remaining: int = remaining, later_count: int = future
        ) -> BulkRouteResult:
            counts["sec_forms_budget_stopped"] += 1
            return BulkRouteResult(counts, spent(), prior_remaining + unprocessed + later_count)

        if quarter in blocked_quarters:
            remaining += len(rows)
            continue
        bulk = quarters.get(quarter)
        state = quarter_state(quarter, downloaded=bulk is not None, run_date=now.date())
        if state is QuarterState.MISSING:
            counts["sec_forms_bulk_quarter_missing"] += 1
            remaining += len(rows)
            continue
        if bulk is not None and (checked is None or not checked(quarter)):
            candidates = {
                accession: len(bulk.accessions[accession].rows)
                for accession, cik, _form, _day in rows
                if accession in bulk.accessions
                and bulk.accessions[accession].issuer_cik == cik
                and not bulk.accessions[accession].identity_bad
                and not bulk.accessions[accession].order_bad
                and (not multi_owner_xml or len(set(bulk.accessions[accession].filer_ciks)) == 1)
            }
            sample = order_sample(candidates, order_sample_size)
            if not sample:
                counts["sec_forms_bulk_order_unchecked"] += 1
            mismatch = False
            clean = True
            for accession in sample:
                if spent() >= budget:
                    return stopped(len(rows))
                selected = next(row for row in rows if row[0] == accession)
                try:
                    parsed = xml(selected[1], accession, selected[3])
                except RequestBudgetStopped:
                    return stopped(len(rows))
                requests += 1
                sample_rows = bulk.accessions[accession].transactions
                if rule5 is not None and parsed is not None:
                    found = rule5.record(accession, sample_rows, parsed.transactions)
                    if enforcing and found:
                        sampled[accession] = parsed
                if enforcing:  # a mismatch is counted and marked, never a refusal
                    clean = clean and parsed is not None and accession not in sampled
                    if parsed is None:
                        counts["sec_forms_bulk_order_sample_unfetched"] += 1
                    continue
                if parsed is None or not rows_agree(sample_rows, parsed.transactions):
                    mismatch = True
                    break
            if mismatch:
                counts["sec_forms_bulk_order_mismatch"] += 1
                remaining += len(rows)
                continue
            if sample and clean and remember_check is not None:
                remember_check(quarter)
        for position, (accession, cik, form, filed) in enumerate(rows):
            if spent() >= budget:
                return stopped(len(rows) - position)
            try:
                stamped = stamp(cik, accession, filed)
            except RequestBudgetStopped:
                return stopped(len(rows) - position)
            requests += 1
            if stamped is None:
                if not source_counts_failures:
                    counts["sec_forms_unstamped"] += 1
                remaining += 1
                continue
            accepted, submissions_form, submissions_filed = (
                (stamped, form, filed) if isinstance(stamped, datetime) else stamped
            )
            if accepted > now:
                counts["sec_forms_not_yet_known"] += 1
                remaining += 1
                continue
            accession_bulk = bulk.accessions.get(accession) if bulk is not None else None
            decision = (
                AccessionRoute(Route.XML, "sec_forms_bulk_multi_owner_xml")
                if (
                    multi_owner_xml
                    and accession_bulk is not None
                    and len(set(accession_bulk.filer_ciks)) > 1
                )
                else AccessionRoute(Route.XML, IDENTITY_BAD)
                if accession_bulk is not None and accession_bulk.issuer_cik != cik
                else route_accession(
                    accession_bulk,
                    state=state,
                    submissions_form=submissions_form,
                    submissions_filing_date=submissions_filed,
                    price_below=price_below,
                    zero_price_codes=zero_price_codes,
                )
            )
            if decision.reason is not None:
                counts[decision.reason] += 1
            if decision.route is Route.NONE:
                remaining += 1
                continue
            if decision.route is Route.XML:
                if spent() >= budget:
                    return stopped(len(rows) - position)
                try:
                    filing = xml(cik, accession, filed)
                except RequestBudgetStopped:
                    return stopped(len(rows) - position)
                requests += 1
                if filing is None:
                    if not source_counts_failures:
                        counts["sec_forms_fetch_failed"] += 1
                    remaining += 1
                    continue
                reason = (
                    owner_refusal(
                        filing, accession_bulk.filer_ciks if accession_bulk is not None else None
                    )
                    if multi_owner_xml
                    else None
                )
                if reason is not None:  # #2711: no owner is guessed or duplicated
                    counts[reason] += 1
                    remaining += 1
                    continue
                if (
                    rule5 is not None
                    and accession_bulk is not None
                    and comparable(accession_bulk, cik, multi_owner_xml)
                ):
                    found = rule5.record(
                        accession, accession_bulk.transactions, filing.transactions
                    )
                    if found and enforcing:
                        filing = trusted_filing(
                            filing, accession_bulk.transactions, rule5.declaration
                        )
                        counts["sec_forms_compare_trusted_row"] += 1
                filing = replace(filing, form=submissions_form, filed_date=submissions_filed)
            elif accession in sampled:
                # The sample's XML disagreed below the threshold: store its trusted
                # row (the XML, unless a field is declared bulk), not the bulk rows.
                assert rule5 is not None and accession_bulk is not None
                trusted = trusted_filing(
                    sampled[accession], accession_bulk.transactions, rule5.declaration
                )
                filing = replace(
                    trusted,
                    form=submissions_form,
                    accepted_at=accepted,
                    filed_date=submissions_filed,
                )
                counts["sec_forms_compare_trusted_row"] += 1
            else:
                assert accession_bulk is not None
                assert accession_bulk.issuer_cik is not None
                filing = SecFormFiling(
                    accession,
                    submissions_form,
                    accepted,
                    submissions_filed,
                    (accession_bulk.issuer_cik,),
                    accession_bulk.filer_ciks,
                    accession_bulk.transactions,
                )
                counts["sec_forms_form4_row_skipped"] += accession_bulk.skipped
            if decision.route is Route.BULK and write_bulk is not None and accession not in sampled:
                written = write_bulk(filing, quarter)
            else:
                written = write(filing)
            if getattr(written, "changed", 0):
                counts["sec_forms_changed"] += 1
            elif getattr(written, "added", 1):
                counts["sec_forms_added_accessions"] += 1
    return BulkRouteResult(counts, spent(), remaining)

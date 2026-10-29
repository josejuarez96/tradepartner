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
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar import SEC_FORMS_PARSER_VERSION
from tradepartner.adapters.edgar_insider_bulk import (
    InsiderBulk,
    InsiderBulkUnreadableError,
    read_insider_bulk,
)
from tradepartner.adapters.filings import SecFormFiling
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
BULK_READER_VERSION = 1


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


def load_bulk_quarters(
    quarters: Iterable[Quarter], *, settings: Settings, client: httpx.Client
) -> tuple[dict[Quarter, InsiderBulk | None], dict[Quarter, str], Counter[str], frozenset[Quarter]]:
    """Download or validate each quarter's zip through edgar_raw's shared limiter.

    The validator sidecar controls cache reuse. A malformed zip fails closed for its
    quarter, and the order-check key changes when its bytes or a parser changes.
    """
    base = Path(settings.edgar.cache_dir) / "insider"
    result: dict[Quarter, InsiderBulk | None] = {}
    digests: dict[Quarter, str] = {}
    counts: Counter[str] = Counter()
    unreadable: set[Quarter] = set()
    for quarter in sorted(set(quarters)):
        label = f"{quarter.year}q{quarter.number}"
        path = base / f"{label}_form345.zip"
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
        use_cache = False
        if path.exists() and saved:
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
    """Cache marker keyed by zip bytes and every parser version affecting parity."""
    from tradepartner.adapters.edgar_source import PARSER_VERSION

    label = f"{quarter.year}q{quarter.number}"
    version = f"b{BULK_READER_VERSION}-x{SEC_FORMS_PARSER_VERSION}-s{PARSER_VERSION}"
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
    request_count: Callable[[], int] | None = None,
    checked: Callable[[Quarter], bool] | None = None,
    remember_check: Callable[[Quarter], None] | None = None,
    skip_accessions: frozenset[str] = frozenset(),
    blocked_quarters: frozenset[Quarter] = frozenset(),
    source_counts_failures: bool = False,
) -> BulkRouteResult:
    """Route selected Form 4 accessions, never substituting a bulk row after XML fails.

    A missing older quarter and an order mismatch stop bulk writes for that quarter.
    The acceptance stamp alone supplies `known_at`; the bulk filing date is only
    checked against the issuer submission record by `route_accession`.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    queue = sorted(
        (row for row in set(accessions) if row[0] not in skip_accessions),
        key=lambda row: (row[3], row[0]),
    )
    grouped: dict[Quarter, list[Form4IndexRow]] = defaultdict(list)
    for row in queue:
        grouped[quarter_of(row[3])].append(row)
    counts: Counter[str] = Counter()
    requests = 0
    remaining = 0

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
            }
            sample = order_sample(candidates, order_sample_size)
            if not sample:
                counts["sec_forms_bulk_order_unchecked"] += 1
            mismatch = False
            for accession in sample:
                if spent() >= budget:
                    return stopped(len(rows))
                selected = next(row for row in rows if row[0] == accession)
                try:
                    parsed = xml(selected[1], accession, selected[3])
                except RequestBudgetStopped:
                    return stopped(len(rows))
                requests += 1
                if parsed is None or not rows_agree(
                    bulk.accessions[accession].transactions, parsed.transactions
                ):
                    mismatch = True
                    break
            if mismatch:
                counts["sec_forms_bulk_order_mismatch"] += 1
                remaining += len(rows)
                continue
            if sample and remember_check is not None:
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
                AccessionRoute(Route.XML, IDENTITY_BAD)
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
                filing = replace(filing, form=submissions_form, filed_date=submissions_filed)
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
            if decision.route is Route.BULK and write_bulk is not None:
                written = write_bulk(filing, quarter)
            else:
                written = write(filing)
            if getattr(written, "changed", 0):
                counts["sec_forms_changed"] += 1
            elif getattr(written, "added", 1):
                counts["sec_forms_added_accessions"] += 1
    return BulkRouteResult(counts, spent(), remaining)

"""Pure route rules of the Form 4 bulk backfill (plan T305c, #2498).

T305b's reader (`edgar_insider_bulk`) returns the raw facts of each accession in a quarter's
insider data set. This module decides, from explicit inputs only, what to do with them:
which route an accession takes (bulk, XML, or nothing), which accessions form the per-quarter
order sample, whether two row lists agree, and what the run's request plan is against the
per-run budget. It touches no store, makes no request and reads no clock: the run date, the
quarter's download outcome, the submissions record and every threshold are arguments.
T307d is the driver that composes these with the fetches and the writes.

Reason strings are the counter names the amendment gives (`sec_forms_bulk_*`); the driver
counts them. A quarter is **published** when its zip downloaded. A 404 means *not yet
published* only for the run date's quarter and the one before it; for an older quarter it
writes nothing from bulk (`sec_forms_bulk_quarter_missing`) and the quarter waits for the
owner (#2498 open question 6), rather than sending a whole quarter to XML.

Row comparison matches rows by `(filer_cik, line_no)`, never by owner position: the data
set orders rows by `(line_no, filer_cik)` and does not keep the XML's `reportingOwner`
order (T305b). Price, shares and shares-owned-after are compared at 2 decimals, as the data
set rounds them (open questions 2 and 5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from tradepartner.adapters.edgar import Form4Transaction
from tradepartner.adapters.edgar_insider_bulk import BulkAccession

QUARTER_MISSING = "sec_forms_bulk_quarter_missing"
BULK_MISSING = "sec_forms_bulk_missing"
HEADER_DISAGREE = "sec_forms_bulk_header_disagree"
IDENTITY_BAD = "sec_forms_bulk_identity_bad"
ORDER_BAD = "sec_forms_bulk_order_bad"
UNDER_THRESHOLD = "sec_forms_bulk_under_threshold"
BUDGET_REFUSED = "sec_forms_budget_refused"

_CENT = Decimal("0.01")
_ROUNDED_FIELDS = frozenset({"price", "shares", "shares_owned_after"})


class Route(Enum):
    """Where an accession's rows come from. `NONE` writes nothing (an older quarter's 404)."""

    BULK = "bulk"
    XML = "xml"
    NONE = "none"


class QuarterState(Enum):
    """What the zip download says about a filing quarter."""

    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"  # 404 for the run date's quarter or the one before it
    MISSING = "missing"  # 404 for an older quarter


@dataclass(frozen=True, order=True)
class Quarter:
    """A calendar quarter, the unit the data sets are cut by."""

    year: int
    number: int  # 1 to 4

    def previous(self) -> Quarter:
        """The quarter before this one."""
        return (
            Quarter(self.year - 1, 4) if self.number == 1 else Quarter(self.year, self.number - 1)
        )


def quarter_of(day: date) -> Quarter:
    """The calendar quarter of a date (the filing quarter, from the submissions `filingDate`)."""
    return Quarter(day.year, (day.month - 1) // 3 + 1)


def quarter_state(quarter: Quarter, *, downloaded: bool, run_date: date) -> QuarterState:
    """Published when the zip downloaded; a 404 is *unpublished* only for the run date's
    quarter and the one before it, *missing* for any older quarter."""
    if downloaded:
        return QuarterState.PUBLISHED
    current = quarter_of(run_date)
    if quarter in (current, current.previous()):
        return QuarterState.UNPUBLISHED
    return QuarterState.MISSING


@dataclass(frozen=True)
class AccessionRoute:
    """The route fixed for one accession and the counter that names why it is not plain bulk
    (`None` for plain bulk and for the unpublished-quarter XML route, which has no counter)."""

    route: Route
    reason: str | None


def under_threshold(
    price: Decimal | None, code: str, *, below: Decimal, zero_price_codes: Collection[str]
) -> bool:
    """Whether one bulk price sends its accession to XML. A null price is never below. A
    price of exactly 0.00 is below only for a code in `zero_price_codes` (for an award,
    gift, exercise or withholding it is the reported zero and stays in bulk). Read on the
    2-decimal bulk price, so a true 0.995 to 0.999 reads as 1.00 and stays."""
    if price is None:
        return False
    if price == 0:
        return code in zero_price_codes
    return price < below


def route_accession(
    bulk: BulkAccession | None,
    *,
    state: QuarterState,
    submissions_form: str,
    submissions_filing_date: date,
    price_below: Decimal,
    zero_price_codes: Collection[str],
) -> AccessionRoute:
    """The route of one accession, fixed before any of its rows is written.

    `bulk` is the accession's bulk facts (`None` when absent from the quarter's set);
    `submissions_form` and `submissions_filing_date` are the issuer's submissions record,
    which the bulk header is only checked against. Checks run in the order: quarter state,
    missing, identity, order, header, price threshold."""
    if state is QuarterState.MISSING:
        return AccessionRoute(Route.NONE, QUARTER_MISSING)
    if state is QuarterState.UNPUBLISHED:
        return AccessionRoute(Route.XML, None)
    if bulk is None:
        return AccessionRoute(Route.XML, BULK_MISSING)
    if bulk.identity_bad:
        return AccessionRoute(Route.XML, IDENTITY_BAD)
    if bulk.order_bad:
        return AccessionRoute(Route.XML, ORDER_BAD)
    if bulk.document_type != submissions_form or bulk.filing_date != submissions_filing_date:
        return AccessionRoute(Route.XML, HEADER_DISAGREE)
    if any(
        under_threshold(
            row.price, row.transaction_code, below=price_below, zero_price_codes=zero_price_codes
        )
        for row in bulk.rows
    ):
        return AccessionRoute(Route.XML, UNDER_THRESHOLD)
    return AccessionRoute(Route.BULK, None)


def order_sample(transaction_counts: Mapping[str, int], size: int) -> tuple[str, ...]:
    """The accessions whose XML checks the quarter's `NONDERIV_TRANS_SK` order: those with
    two or more non-derivative transactions, the first `size` by ascending SHA-256 of the
    accession number. `transaction_counts` maps each subject-issuer accession of the
    quarter to its count; fewer eligible than `size` gives all of them, none gives `()`."""
    if size < 0:
        raise ValueError("the sample size cannot be negative")
    eligible = [a for a, count in transaction_counts.items() if count >= 2]
    eligible.sort(key=lambda a: (hashlib.sha256(a.encode("utf-8")).hexdigest(), a))
    return tuple(eligible[:size])


def _q2(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _comparable(row: Form4Transaction) -> tuple[object, ...]:
    return tuple(
        _q2(getattr(row, f.name)) if f.name in _ROUNDED_FIELDS else getattr(row, f.name)
        for f in fields(Form4Transaction)
    )


def rows_agree(bulk_rows: Sequence[Form4Transaction], xml_rows: Sequence[Form4Transaction]) -> bool:
    """Whether the bulk and XML rows of one accession are the same rows. Rows are matched
    by `(filer_cik, line_no)`, never by owner position; the keys must be the same set with
    none repeated, and every contract field must be equal, price, shares and shares owned
    after at 2 decimals. A reordered pair of transactions differs at its keys and fails."""
    bulk_by_key = {(r.filer_cik, r.line_no): r for r in bulk_rows}
    xml_by_key = {(r.filer_cik, r.line_no): r for r in xml_rows}
    if len(bulk_by_key) != len(bulk_rows) or len(xml_by_key) != len(xml_rows):
        return False
    if bulk_by_key.keys() != xml_by_key.keys():
        return False
    return all(_comparable(bulk_by_key[k]) == _comparable(xml_by_key[k]) for k in bulk_by_key)


@dataclass(frozen=True)
class RequestCounts:
    """The remaining work the run knows after the zips and the filing index: stored
    identities, cached stamps and remembered order checks are already left out."""

    submissions_first_pages: int
    anchor_headers: int
    xml_fetches: int
    order_samples: int

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"{f.name} cannot be negative")

    @property
    def total(self) -> int:
        """Requests the remaining work needs."""
        return (
            self.submissions_first_pages
            + self.anchor_headers
            + self.xml_fetches
            + self.order_samples
        )


@dataclass(frozen=True)
class RequestPlan:
    """The run's planned requests against the per-run budget. `refused` means the
    per-filing fetches must not start (`sec_forms_budget_refused`); older submissions pages
    are known only as fetched and are not in the plan."""

    already_spent: int
    counts: RequestCounts
    budget: int

    @property
    def planned(self) -> int:
        """Requests already made this run plus the remaining work."""
        return self.already_spent + self.counts.total

    @property
    def refused(self) -> bool:
        return self.planned > self.budget

    @property
    def reason(self) -> str | None:
        return BUDGET_REFUSED if self.refused else None

    def message(self) -> str:
        """The plan line for `ingestion_runs.message`."""
        c = self.counts
        return (
            f"plan {self.planned} of {self.budget} requests: {self.already_spent} spent, "
            f"{c.submissions_first_pages} submissions pages, {c.anchor_headers} anchor headers, "
            f"{c.xml_fetches} XML, {c.order_samples} order samples"
            + (f" ({BUDGET_REFUSED})" if self.refused else "")
        )


def plan_requests(counts: RequestCounts, *, budget: int, already_spent: int = 0) -> RequestPlan:
    """Plan the per-filing fetches against `events.sec_forms_max_requests`. `already_spent`
    is the run's requests so far (the zips and the filing index); `counts` is only the work
    still to do, so a resumed run plans against what remains."""
    if budget <= 0 or already_spent < 0:
        raise ValueError("the budget must be positive and the spent count not negative")
    return RequestPlan(already_spent=already_spent, counts=counts, budget=budget)


def budget_reached(requests_made: int, budget: int) -> bool:
    """Whether the next request must not be made (`sec_forms_budget_stopped`)."""
    return requests_made >= budget

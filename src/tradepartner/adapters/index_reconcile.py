"""The settled-quarter reconcile of the incremental EDGAR ingest, pure (T319b, #2629, #2703).

A closed quarter's `form.idx` is compared with the stored `filing_index` rows. This
module decides; the driver (T323) reads the file and the table, then writes what is
returned. It reads no clock, no store and no network: every input is an argument.

What is compared (spec amendment 2026-10-30, *Late corrections*): only the rows the
table keeps, which are issuer CIKs, CIKs that an issuer-form row in the file makes
issuers (these take the new-issuer walk instead), and the 25 and `events.sec_forms`
rows. A row is matched on `(cik, accession)` over the whole table, wherever its
latest revision's `filed_on` places it, so a correction that crossed a quarter end is
a revision, never an insert plus an absent row.

- A row only in the file is returned to add.
- A stored row of this quarter that the file lacks is counted and kept.
- A form or company-name difference is refused and counted.
- A `filed_on` difference becomes one revision, only when the file's `listed_on`
  is after the latest revision's. A quarterly file never wins a tie (a daily file
  does), so a file that predates a daily re-listing writes nothing back (#2703); the
  count is `index_stale_listing`.

`select_quarters` works out which settled quarters to reconcile on a run. A quarter is
selected when it settled since the last `ok` run (so a key-off gap that spans a settle
is caught), when its HEAD ETag changed, and on every Saturday (US/Eastern) for the last
`edgar.reconcile_recent_quarters` settled quarters, beside the ETag test (owner,
2026-10-31, #2659). T317 found SEC rewrites every quarterly file nightly, so
Last-Modified is no test; the ETag is the MD5 of the body.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tradepartner.adapters.form4_route import Quarter, quarter_of
from tradepartner.timeutil import ensure_tz_aware_utc

_EASTERN = ZoneInfo("America/New_York")
_SATURDAY = 5

#: Why a quarter was selected, in this order.
REASON_SETTLED = "settled_since_last_ok"
REASON_ETAG = "etag_changed"
REASON_SATURDAY = "saturday"


@dataclass(frozen=True, slots=True)
class FileRow:
    """One row of a quarterly `form.idx`: what the file says, with no acceptance time."""

    cik: str
    accession: str
    form: str
    company_name: str
    filed_on: date


@dataclass(frozen=True, slots=True)
class StoredRow:
    """The latest stored revision of one `(cik, accession)` (its `filed_on` and `listed_on`)."""

    cik: str
    accession: str
    form: str
    company_name: str
    filed_on: date
    listed_on: date


@dataclass(frozen=True, slots=True)
class Revision:
    """A `filed_on` correction: the file's row, to be written as a new dated revision."""

    cik: str
    accession: str
    form: str
    company_name: str
    filed_on: date
    listed_on: date


@dataclass(frozen=True)
class ReconcileResult:
    """What one quarterly file asks of the table."""

    to_add: tuple[FileRow, ...]
    revisions: tuple[Revision, ...]
    refused: tuple[FileRow, ...]
    absent: int
    stale: int
    new_issuer_ciks: tuple[str, ...]

    @property
    def refused_count(self) -> int:
        """Rows refused for a differing form or name (or conflicting duplicate lines)."""
        return len(self.refused)


def quarterly_listed_on(last_modified: datetime) -> date:
    """A quarterly file's `listed_on`: the day before the US/Eastern date of Last-Modified.

    SEC rewrites the files around 07:30 UTC, so a file covers the daily files up to the
    day before at most (T317, 2026-10-31).
    """
    stamp = ensure_tz_aware_utc(last_modified, field_name="last_modified")
    return stamp.astimezone(_EASTERN).date() - timedelta(days=1)


def _quarter_range(quarter: Quarter) -> tuple[date, date]:
    start = date(quarter.year, (quarter.number - 1) * 3 + 1, 1)
    end = date(quarter.year + quarter.number // 4, quarter.number % 4 * 3 + 1, 1)
    return start, end


def _dedupe(rows: Iterable[FileRow]) -> tuple[list[FileRow], list[FileRow]]:
    """One row per `(cik, accession)`. Exact duplicate lines collapse; a key listed with
    different values is conflicting and goes to the second list whole (never guessed)."""
    first: dict[tuple[str, str], FileRow] = {}
    conflicting: dict[tuple[str, str], FileRow] = {}
    for row in rows:
        key = (row.cik, row.accession)
        seen = first.get(key)
        if seen is None:
            first[key] = row
        elif seen != row:
            conflicting.setdefault(key, seen)
    return (
        [row for key, row in first.items() if key not in conflicting],
        list(conflicting.values()),
    )


def reconcile_quarter(
    file_rows: Iterable[FileRow],
    *,
    quarter: Quarter,
    listed_on: date,
    stored: Iterable[StoredRow],
    issuer_ciks: Collection[str],
    issuer_forms: Collection[str],
    kept_forms: Collection[str],
) -> ReconcileResult:
    """Compare a settled quarter's `form.idx` with the table.

    `stored` is the latest revision of every `(cik, accession)` the table holds that the
    file lists or whose `filed_on` is in `quarter`. `listed_on` is `quarterly_listed_on`
    of the file's Last-Modified. `kept_forms` are the 25 and `events.sec_forms` forms.
    """
    by_key = {(row.cik, row.accession): row for row in stored}
    rows, conflicting = _dedupe(file_rows)
    issuers = frozenset(issuer_ciks)
    new_issuers = {
        row.cik for row in rows if row.cik not in issuers and row.form in set(issuer_forms)
    }
    kept = frozenset(kept_forms)
    to_add: list[FileRow] = []
    revisions: list[Revision] = []
    refused: list[FileRow] = [row for row in conflicting if row.cik not in new_issuers]
    stale = 0
    listed: set[tuple[str, str]] = {(row.cik, row.accession) for row in rows}
    listed.update((row.cik, row.accession) for row in conflicting)
    for row in rows:
        if row.cik in new_issuers:
            continue  # the new-issuer walk takes the CIK's whole history
        have = by_key.get((row.cik, row.accession))
        if have is None:
            if row.cik in issuers or row.form in kept:
                to_add.append(row)
        elif (row.form, row.company_name) != (have.form, have.company_name):
            refused.append(row)
        elif row.filed_on != have.filed_on:
            if listed_on > have.listed_on:
                revisions.append(
                    Revision(
                        row.cik, row.accession, row.form, row.company_name, row.filed_on, listed_on
                    )
                )
            else:
                stale += 1
    start, end = _quarter_range(quarter)
    absent = sum(
        1 for key, have in by_key.items() if start <= have.filed_on < end and key not in listed
    )
    return ReconcileResult(
        tuple(to_add),
        tuple(revisions),
        tuple(refused),
        absent,
        stale,
        tuple(sorted(new_issuers)),
    )


def _normalise_etag(value: str) -> str:
    """An ETag without its quotes, weak prefix or `-gzip` suffix (the MD5 of the body)."""
    tag = value.strip().removeprefix("W/").strip('"')
    return tag.removesuffix("-gzip").strip('"')


def etag_changed_quarters(
    stored: Mapping[Quarter, str], head: Mapping[Quarter, str]
) -> frozenset[Quarter]:
    """Quarters whose HEAD ETag differs from the stored one, or has none stored."""
    return frozenset(
        quarter
        for quarter, tag in head.items()
        if quarter not in stored or _normalise_etag(stored[quarter]) != _normalise_etag(tag)
    )


@dataclass(frozen=True)
class QuarterSelection:
    """The quarters to reconcile, oldest first, and why each was picked."""

    quarters: tuple[Quarter, ...]
    reasons: Mapping[Quarter, tuple[str, ...]]


def _settled_at(quarter: Quarter, settle_days: int) -> datetime:
    _, end = _quarter_range(quarter)
    return datetime(end.year, end.month, end.day, tzinfo=_EASTERN) + timedelta(days=settle_days)


def select_quarters(
    *,
    now: datetime,
    last_ok_run_at: datetime | None,
    changed: Collection[Quarter],
    settle_days: int,
    first_year: int,
    recent_quarters: int,
) -> QuarterSelection:
    """Which settled quarters to reconcile on this run.

    - every quarter that settled after `last_ok_run_at` and by `now` (none when there is
      no earlier `ok` run: the seed reads everything);
    - every settled quarter in `changed` (HEAD ETag differs from the stored one);
    - on a Saturday (US/Eastern date of `now`), the last `recent_quarters` settled
      quarters, whatever the HEAD tests say (owner, 2026-10-31, #2659).
    """
    clock = ensure_tz_aware_utc(now, field_name="now")
    last_ok = (
        None
        if last_ok_run_at is None
        else ensure_tz_aware_utc(last_ok_run_at, field_name="last_ok_run_at")
    )
    quarter = Quarter(first_year, 1)
    last = quarter_of(clock.astimezone(_EASTERN).date())
    settled: list[Quarter] = []
    while quarter <= last:
        if clock >= _settled_at(quarter, settle_days):
            settled.append(quarter)
        quarter = (
            Quarter(quarter.year + 1, 1)
            if quarter.number == 4
            else Quarter(quarter.year, quarter.number + 1)
        )
    reasons: dict[Quarter, list[str]] = {}
    if last_ok is not None:
        for q in settled:
            if _settled_at(q, settle_days) > last_ok:
                reasons.setdefault(q, []).append(REASON_SETTLED)
    for q in settled:
        if q in changed:
            reasons.setdefault(q, []).append(REASON_ETAG)
    if clock.astimezone(_EASTERN).weekday() == _SATURDAY and recent_quarters > 0:
        for q in settled[-recent_quarters:]:
            reasons.setdefault(q, []).append(REASON_SATURDAY)
    ordered = tuple(sorted(reasons))
    return QuarterSelection(ordered, {q: tuple(reasons[q]) for q in ordered})

"""Reviewed listing rows the filings state but the cover-page rules cannot derive (#3293).

A cover page names a listing; an exchange's CERT or a Form 8-A12B states a
move or a relisting that no later cover page has yet repeated (the Texas Stock
Exchange and Nasdaq transfers of 2026, Interlink's 2021 relisting). Without a
row for the new venue the old line's Form 25 reads as a terminal delisting and
the security's bars are orphaned (#3288 groups G2 and G3).

`listing_reviews.csv` holds one reviewed row per such filing. `build_master`
(`store.master`) writes each as a `listings` row with `source = edgar` and
`provenance = filing`, `known_at` the cited filing's acceptance time, so it
is point in time: a build whose `ingested_at` precedes the acceptance leaves
the row out. A row whose security the build does not hold is left out and
counted, never invented. A key the build already derives is not written twice.

Columns: `security_id`, `ticker`, `exchange` (a `normalize_exchange` code),
`class_title` (may be empty: untitled, as a snapshot row), `valid_from`
(a session), `known_at` (UTC, the filing's acceptance), `accession` (the
filing, EDGAR form `##########-##-######`), `reason`, `issue` (`#n`).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import resources

from tradepartner.adapters.edgar import normalize_exchange
from tradepartner.calendar import is_session

_FILE = "listing_reviews.csv"
_COLUMNS = (
    "security_id",
    "ticker",
    "exchange",
    "class_title",
    "valid_from",
    "known_at",
    "accession",
    "reason",
    "issue",
)
_SECURITY_ID = re.compile(r"\d{10}(?::[a-z0-9-]+)?")
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


@dataclass(frozen=True)
class ReviewedListing:
    """One reviewed `listings` row, cited to the filing that states it."""

    security_id: str
    ticker: str
    exchange: str
    class_title: str | None
    valid_from: date
    known_at: datetime
    accession: str
    reason: str
    issue: str

    @property
    def key(self) -> tuple[str, str, str, date]:
        """The `listings` key: security, ticker, exchange, start."""
        return (self.security_id, self.ticker, self.exchange, self.valid_from)

    @property
    def entry(self) -> str:
        """A stable one-line label of the row, for the per-name fingerprint
        (it names the security's ten-digit CIK, which the incremental build
        then touches)."""
        return (
            f"{self.security_id}|{self.ticker}|{self.exchange}|{self.valid_from.isoformat()}"
            f"|{self.accession}"
        )


def parse_reviews(text: str) -> tuple[ReviewedListing, ...]:
    """Parse the reviewed CSV, refusing malformed, duplicate or undocumented rows."""
    reader = csv.reader(io.StringIO(text))
    if tuple(next(reader, ())) != _COLUMNS:
        raise ValueError(f"{_FILE} line 1: expected {_COLUMNS}")
    rows: list[ReviewedListing] = []
    seen: set[tuple[str, str, str, date]] = set()
    for raw in reader:
        if not raw:
            continue
        line = reader.line_num
        if len(raw) != len(_COLUMNS):
            raise ValueError(f"{_FILE} line {line}: {len(_COLUMNS)} fields required")
        values = [value.strip() for value in raw]
        security_id, ticker, exchange, title, start, stamp, accession, reason, issue = values
        if any(not value for value in (security_id, ticker, exchange, reason)):
            raise ValueError(f"{_FILE} line {line}: a required field is empty")
        if _SECURITY_ID.fullmatch(security_id) is None:
            raise ValueError(f"{_FILE} line {line}: invalid security_id {security_id!r}")
        if normalize_exchange(exchange) != exchange:
            raise ValueError(f"{_FILE} line {line}: {exchange!r} is not an exchange code")
        if _ACCESSION.fullmatch(accession) is None or not issue.startswith("#"):
            raise ValueError(f"{_FILE} line {line}: invalid accession or issue")
        try:
            valid_from = date.fromisoformat(start)
            known_at = datetime.fromisoformat(stamp)
        except ValueError:
            raise ValueError(f"{_FILE} line {line}: invalid valid_from or known_at") from None
        if not is_session(valid_from):
            raise ValueError(f"{_FILE} line {line}: valid_from {start} is not a session")
        if known_at.utcoffset() != timedelta(0):
            raise ValueError(f"{_FILE} line {line}: known_at must be tz-aware UTC")
        row = ReviewedListing(
            security_id,
            ticker,
            exchange,
            title or None,
            valid_from,
            known_at,
            accession,
            reason,
            issue,
        )
        if row.key in seen:
            raise ValueError(f"{_FILE} line {line}: duplicate row {row.key}")
        seen.add(row.key)
        rows.append(row)
    return tuple(rows)


def reviewed_listings() -> tuple[ReviewedListing, ...]:
    """Load the packaged reviewed rows; a missing file fails closed."""
    return parse_reviews(resources.files(__package__).joinpath(_FILE).read_text(encoding="utf-8"))

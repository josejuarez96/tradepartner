"""Reviewed spin-off actions the price feed does not carry (#3392).

The Alpaca actions feed is the only source of `spinoff` corporate actions, and
it can miss one: MSG Rangers (MSGR), distributed on 2026-10-27, was not in the
real store because the parent was named by a ticker the master did not yet
list. `reviewed_spinoffs.csv` holds one row per distribution read from the
filings, with the exact values a feed row would carry:

- `child_security_id` and `parent_security_id` (ten-digit CIKs): the stored
  action is the child's `spinoff`, `source_action_id` the parent's id (the T64
  convention of `parse_corporate_actions`);
- `ex_date` (an XNYS session) and `ratio` (child shares per parent share);
- `known_at`: the acceptance time (UTC) of the cited 8-K, so the action is
  point in time;
- `accession` (EDGAR form) and `evidence`; `issue` (`#n`).

`AlpacaPriceSource.corporate_actions` adds a row for a requested child or
parent whose ex-date is in the window, unless the feed returned the same
(child, parent) identity, which then wins. Nothing else reads the file.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Final

from tradepartner.adapters.prices import ActionType, CorporateAction
from tradepartner.calendar import is_session

_FILE: Final = "reviewed_spinoffs.csv"
SOURCE: Final = "edgar"
COLUMNS: Final = (
    "child_security_id",
    "parent_security_id",
    "ex_date",
    "ratio",
    "known_at",
    "accession",
    "evidence",
    "issue",
)
_SECURITY_ID = re.compile(r"\d{10}")
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


@dataclass(frozen=True)
class ReviewedSpinoff:
    """One reviewed distribution, cited to the filing that states it."""

    child_security_id: str
    parent_security_id: str
    ex_date: date
    ratio: float
    known_at: datetime
    accession: str
    evidence: str
    issue: str

    @property
    def identity(self) -> tuple[str, str]:
        """`(child, parent)`: the identity of a spin-off in the store."""
        return (self.child_security_id, self.parent_security_id)

    @property
    def action(self) -> CorporateAction:
        """The `spinoff` action this row stands for."""
        return CorporateAction(
            security_id=self.child_security_id,
            action_type=ActionType.SPINOFF,
            ex_date=self.ex_date,
            ratio_or_amount=self.ratio,
            known_at=self.known_at,
            source=SOURCE,
            source_action_id=self.parent_security_id,
        )


def parse_spinoffs(text: str) -> tuple[ReviewedSpinoff, ...]:
    """The rows in the CSV `text`, in file order. Raises `ValueError`, naming
    the line, on another header, a short or long row, an id that is not a
    ten-digit CIK, a child that is its own parent, an `ex_date` that is not an
    XNYS session, a ratio that is not positive and finite, a `known_at` that
    is not tz-aware UTC, a bad accession or issue, empty evidence, or an
    identity listed twice."""
    reader = csv.reader(io.StringIO(text))
    if tuple(next(reader, ())) != COLUMNS:
        raise ValueError(f"{_FILE} line 1: expected {COLUMNS}")
    rows: list[ReviewedSpinoff] = []
    seen: set[tuple[str, str]] = set()
    for raw in reader:
        if not raw:
            continue
        line = reader.line_num

        def fail(why: str, line: int = line) -> ValueError:
            return ValueError(f"{_FILE} line {line}: {why}")

        if len(raw) != len(COLUMNS):
            raise fail(f"{len(COLUMNS)} fields required")
        child, parent, ex, ratio_text, stamp, accession, evidence, issue = (v.strip() for v in raw)
        for sid in (child, parent):
            if _SECURITY_ID.fullmatch(sid) is None:
                raise fail(f"invalid security id {sid!r}")
        if child == parent:
            raise fail("a spin-off's child is not its parent")
        if _ACCESSION.fullmatch(accession) is None or not issue.startswith("#"):
            raise fail("invalid accession or issue")
        if not evidence:
            raise fail("empty evidence")
        try:
            ex_date = date.fromisoformat(ex)
            known_at = datetime.fromisoformat(stamp)
            ratio = float(ratio_text)
        except ValueError:
            raise fail("invalid ex_date, ratio or known_at") from None
        if not is_session(ex_date):
            raise fail(f"ex_date {ex} is not an XNYS session")
        if not math.isfinite(ratio) or ratio <= 0:
            raise fail(f"ratio {ratio_text!r} is not positive")
        if known_at.utcoffset() != timedelta(0):
            raise fail("known_at must be tz-aware UTC")
        row = ReviewedSpinoff(child, parent, ex_date, ratio, known_at, accession, evidence, issue)
        if row.identity in seen:
            raise fail(f"identity {row.identity} is listed again")
        seen.add(row.identity)
        rows.append(row)
    return tuple(rows)


def reviewed_spinoffs() -> tuple[ReviewedSpinoff, ...]:
    """The packaged reviewed rows; a missing or malformed file fails closed."""
    return parse_spinoffs(resources.files(__package__).joinpath(_FILE).read_text(encoding="utf-8"))

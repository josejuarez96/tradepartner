"""The reviewed list of equal-amount cash dividend groups (#1613).

Alpaca sometimes carries one declared cash dividend as two records under two
ids, and ingest stores both (spec req 5's first-seen rule): two live
`dividend` rows of one security with one `ex_date` and one
`ratio_or_amount`. Not every such pair is a duplicate (Ford's 2025-02-18
$0.15 is a regular and a supplemental dividend, both paid), and no stored or
raw field separates the classes, so ingest never dedupes. Each group is
judged from evidence outside the feed and recorded here, one row per group,
in the packaged file `dividend_reviews.csv` (spec amendment 2026-10-12,
"The reviewed list"):

- `duplicate`: one entitlement stored twice; `retract_source_action_id`
  names the greater of the two ids in string order, which
  `repair.retract_dividends` deletes under a named data release;
- `distinct`: two declared payments of equal amount, both kept;
- `unresolved`: the evidence cannot settle it, kept and reported.

Every listed group was reviewed as exactly two identities. `reviewed_groups`
reads the file (refusing a malformed one, naming the row) and
`equal_amount_groups` is the pure rule the health check and the paper
reconciliation apply to the live rows: per `(security_id, ex_date,
ratio_or_amount)` the identities and their class. Amounts compare by exact
equality with the stored `DOUBLE`, never a tolerance.
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from typing import Final, Literal, cast

import polars as pl

from tradepartner.calendar import is_session

Verdict = Literal["duplicate", "distinct", "unresolved"]
GroupClass = Literal["violation", "accepted", "unresolved", "pending", "repaired"]

DUPLICATE: Final = "duplicate"
DISTINCT: Final = "distinct"
UNRESOLVED: Final = "unresolved"
VERDICTS: Final[frozenset[str]] = frozenset({DUPLICATE, DISTINCT, UNRESOLVED})

#: The file's columns, in order.
COLUMNS: Final = (
    "security_id",
    "ex_date",
    "amount",
    "verdict",
    "retract_source_action_id",
    "ticker",
    "evidence",
)
#: The identities every listed group was reviewed as.
REVIEWED_IDENTITIES: Final = 2
_FILE: Final = "dividend_reviews.csv"

#: `(security_id, ex_date, amount)`.
GroupKey = tuple[str, date, float]


class ReviewsError(ValueError):
    """The reviewed list is malformed; the message names the row."""


@dataclass(frozen=True)
class ReviewedGroup:
    """One adjudicated equal-amount group of the reviewed list."""

    security_id: str
    ex_date: date
    amount: float
    verdict: Verdict
    #: The id `repair.retract_dividends` deletes; empty unless `duplicate`.
    retract_source_action_id: str
    #: The listing at the ex-date, for the reader (empty when none resolves).
    ticker: str
    evidence: str

    @property
    def key(self) -> GroupKey:
        """`(security_id, ex_date, amount)`."""
        return (self.security_id, self.ex_date, self.amount)


def _row(line: int, raw: dict[str, str]) -> ReviewedGroup:
    def fail(why: str) -> ReviewsError:
        return ReviewsError(f"{_FILE} line {line}: {why}")

    sid = raw["security_id"].strip()
    if not sid:
        raise fail("empty security_id")
    try:
        ex_date = date.fromisoformat(raw["ex_date"].strip())
    except ValueError:
        raise fail(f"ex_date {raw['ex_date']!r} is not a date") from None
    if not is_session(ex_date):
        raise fail(f"ex_date {ex_date} is not an XNYS session")
    try:
        amount = float(raw["amount"])
    except ValueError:
        raise fail(f"amount {raw['amount']!r} is not a number") from None
    if not math.isfinite(amount) or amount <= 0:
        raise fail(f"amount {raw['amount']!r} is not positive")
    verdict = raw["verdict"].strip()
    if verdict not in VERDICTS:
        raise fail(f"verdict {verdict!r} is not one of {sorted(VERDICTS)}")
    retract = raw["retract_source_action_id"].strip()
    if verdict == DUPLICATE and not retract:
        raise fail("a duplicate row needs retract_source_action_id")
    if verdict != DUPLICATE and retract:
        raise fail(f"a {verdict} row has a retract_source_action_id")
    return ReviewedGroup(
        security_id=sid,
        ex_date=ex_date,
        amount=amount,
        verdict=cast(Verdict, verdict),
        retract_source_action_id=retract,
        ticker=raw["ticker"].strip(),
        evidence=raw["evidence"].strip(),
    )


def parse_reviews(text: str) -> tuple[ReviewedGroup, ...]:
    """The reviewed groups in the CSV `text` (the packaged file's format),
    in file order. Raises `ReviewsError`, naming the line, on another header,
    a short or long row, a verdict outside `VERDICTS`, an `ex_date` that is
    not an XNYS session, a non-positive amount, a retract id on a row that
    is not `duplicate` or none on one that is, or a key listed twice."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or tuple(header) != COLUMNS:
        raise ReviewsError(f"{_FILE} line 1: header {header!r} is not {list(COLUMNS)}")
    groups: list[ReviewedGroup] = []
    seen: dict[GroupKey, int] = {}
    for values in reader:
        line = reader.line_num
        if not values:
            continue
        if len(values) != len(COLUMNS):
            raise ReviewsError(f"{_FILE} line {line}: {len(values)} fields, not {len(COLUMNS)}")
        group = _row(line, dict(zip(COLUMNS, values, strict=True)))
        if (first := seen.get(group.key)) is not None:
            raise ReviewsError(
                f"{_FILE} line {line}: key {group.key} is listed again (line {first})"
            )
        seen[group.key] = line
        groups.append(group)
    return tuple(groups)


def reviewed_groups() -> tuple[ReviewedGroup, ...]:
    """The packaged reviewed list (`parse_reviews` over
    `tradepartner/store/dividend_reviews.csv`)."""
    text = resources.files("tradepartner.store").joinpath(_FILE).read_text(encoding="utf-8")
    return parse_reviews(text)


@dataclass(frozen=True)
class Identity:
    """One live vendor record of a group: its id and when it became known."""

    source_action_id: str
    known_at: datetime


@dataclass(frozen=True)
class EqualAmountGroup:
    """One `(security_id, ex_date, amount)` key and its class under the
    reviewed list (`equal_amount_groups`)."""

    security_id: str
    ex_date: date
    amount: float
    #: The live identities on the key, sorted by id.
    identities: tuple[Identity, ...]
    group_class: GroupClass
    #: The reviewed row, or None for an unlisted key.
    reviewed: ReviewedGroup | None

    @property
    def key(self) -> GroupKey:
        """`(security_id, ex_date, amount)`."""
        return (self.security_id, self.ex_date, self.amount)


_LISTED_CLASS: Final[dict[str, GroupClass]] = {
    DISTINCT: "accepted",
    UNRESOLVED: "unresolved",
    DUPLICATE: "pending",
}


def equal_amount_groups(
    live_dividend_rows: pl.DataFrame, reviewed: Iterable[ReviewedGroup]
) -> tuple[EqualAmountGroup, ...]:
    """Classify every equal-amount cash dividend key of the live rows
    against the reviewed list (spec amendment 2026-10-12, "The health rule").

    `live_dividend_rows` is `store.asof.live_actions_as_of`'s frame (one row
    per identity, the latest revision, not cancelled); rows whose
    `action_type` is not `dividend` are ignored. An identity is `(security_id,
    source_action_id)` (the action type and ex-date stand in for an empty
    id, as in the store). The key is `(security_id, ex_date,
    ratio_or_amount)`, compared exactly; `known_at` and `source_action_id`
    are never part of it, so two identities known at different times are one
    key. Per key:

    - more than one identity and not listed: `violation`;
    - listed with exactly two identities: `accepted` (`distinct`),
      `unresolved`, or `pending` (`duplicate`: not repaired yet);
    - a listed `duplicate` with one identity: `repaired`;
    - listed with any other count of at least one: `violation` (a third
      record, or one leg of a real pair gone).

    A key with one identity that is not listed, and a listed key with none
    (a store that never held it), are not returned. Sorted by key."""
    identities: dict[GroupKey, dict[str, datetime]] = defaultdict(dict)
    dividends = live_dividend_rows.filter(pl.col("action_type") == "dividend")
    for row in dividends.select(
        "security_id", "ex_date", "ratio_or_amount", "source_action_id", "known_at"
    ).iter_rows():
        sid, ex_date, amount, action_id, known_at = row
        key: GroupKey = (str(sid), ex_date, float(amount))
        identities[key][str(action_id)] = known_at
    listed: dict[GroupKey, ReviewedGroup] = {group.key: group for group in reviewed}
    out: list[EqualAmountGroup] = []
    for key in sorted(identities.keys() | listed.keys()):
        found = identities.get(key, {})
        group = listed.get(key)
        count = len(found)
        group_class: GroupClass
        if group is None:
            if count <= 1:
                continue
            group_class = "violation"
        elif count == 0:
            continue
        elif count == REVIEWED_IDENTITIES:
            group_class = _LISTED_CLASS[group.verdict]
        elif count == 1 and group.verdict == DUPLICATE:
            group_class = "repaired"
        else:
            group_class = "violation"
        out.append(
            EqualAmountGroup(
                security_id=key[0],
                ex_date=key[1],
                amount=key[2],
                identities=tuple(Identity(i, k) for i, k in sorted(found.items())),
                group_class=group_class,
                reviewed=group,
            )
        )
    return tuple(out)

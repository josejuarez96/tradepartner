"""The reviewed list of Alpaca split rows that are spin-offs (#3058).

Alpaca serves some spin-offs as a `forward_splits`, `reverse_splits` or
`stock_dividends` row of the parent (the 2026-11-03 store measurement, #3053
item 2: 33 such rows, 30 of them before 2020-11). Stored as a `split`, the row
rescales the parent's whole history before the ex-date by a share ratio that
never happened: HWM's 2020-04-01 Arconic Corp distribution (one child share
per four) is a 0.25 "reverse split" whose raw close fell 2.3x, an about -89%
fake return, and HON's 2018-10-29 Resideo distribution a 1.032 "split" that
also scales the share counts.

Ingest cannot tell such a row from a split by its own fields, so each one is
judged from evidence outside the feed and recorded here, one row per Alpaca
identity `(security_id, source_action_id)`, in the packaged file
`spinoff_splits.csv`:

- the stored row's `ex_date` and `ratio` (the exact stored `DOUBLE`), so a
  repair deletes only the row that was reviewed;
- `ticker` (the parent's listing on the ex-date) and `child` (the company
  distributed) for the reader;
- `evidence`: why it is a spin-off and not a split.

`AlpacaPriceSource.corporate_actions` refuses a listed identity, so a
backfill never stores it again, and `repair.retract_spinoff_splits` deletes
it from the store under a named data release. Nothing else reads the list.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Final

from tradepartner.calendar import is_session

#: The file's columns, in order.
COLUMNS: Final = (
    "security_id",
    "source_action_id",
    "ex_date",
    "ratio",
    "ticker",
    "child",
    "evidence",
)
_FILE: Final = "spinoff_splits.csv"

#: `(security_id, source_action_id)`: the store's identity of an Alpaca action.
Identity = tuple[str, str]


class SpinoffSplitsError(ValueError):
    """The reviewed list is malformed; the message names the line."""


@dataclass(frozen=True)
class SpinoffSplit:
    """One reviewed Alpaca split row that is a spin-off."""

    security_id: str
    source_action_id: str
    ex_date: date
    #: The stored `ratio_or_amount`, compared exactly.
    ratio: float
    ticker: str
    child: str
    evidence: str

    @property
    def identity(self) -> Identity:
        """`(security_id, source_action_id)`."""
        return (self.security_id, self.source_action_id)


def _row(line: int, raw: dict[str, str]) -> SpinoffSplit:
    def fail(why: str) -> SpinoffSplitsError:
        return SpinoffSplitsError(f"{_FILE} line {line}: {why}")

    sid = raw["security_id"].strip()
    if not sid:
        raise fail("empty security_id")
    action_id = raw["source_action_id"].strip()
    if not action_id:
        raise fail("empty source_action_id (an id-less action is not taken)")
    try:
        ex_date = date.fromisoformat(raw["ex_date"].strip())
    except ValueError:
        raise fail(f"ex_date {raw['ex_date']!r} is not a date") from None
    if not is_session(ex_date):
        raise fail(f"ex_date {ex_date} is not an XNYS session")
    try:
        ratio = float(raw["ratio"])
    except ValueError:
        raise fail(f"ratio {raw['ratio']!r} is not a number") from None
    if not math.isfinite(ratio) or ratio <= 0:
        raise fail(f"ratio {raw['ratio']!r} is not positive")
    evidence = raw["evidence"].strip()
    if not evidence:
        raise fail("empty evidence")
    return SpinoffSplit(
        security_id=sid,
        source_action_id=action_id,
        ex_date=ex_date,
        ratio=ratio,
        ticker=raw["ticker"].strip(),
        child=raw["child"].strip(),
        evidence=evidence,
    )


def parse_spinoff_splits(text: str) -> tuple[SpinoffSplit, ...]:
    """The reviewed rows in the CSV `text` (the packaged file's format), in
    file order. Raises `SpinoffSplitsError`, naming the line, on another
    header, a short or long row, an empty id or evidence, an `ex_date` that
    is not an XNYS session, a non-positive ratio, or an identity listed
    twice."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or tuple(header) != COLUMNS:
        raise SpinoffSplitsError(f"{_FILE} line 1: header {header!r} is not {list(COLUMNS)}")
    rows: list[SpinoffSplit] = []
    seen: dict[Identity, int] = {}
    for values in reader:
        line = reader.line_num
        if not values:
            continue
        if len(values) != len(COLUMNS):
            raise SpinoffSplitsError(
                f"{_FILE} line {line}: {len(values)} fields, not {len(COLUMNS)}"
            )
        row = _row(line, dict(zip(COLUMNS, values, strict=True)))
        if (first := seen.get(row.identity)) is not None:
            raise SpinoffSplitsError(
                f"{_FILE} line {line}: identity {row.identity} is listed again (line {first})"
            )
        seen[row.identity] = line
        rows.append(row)
    return tuple(rows)


def spinoff_splits() -> tuple[SpinoffSplit, ...]:
    """The packaged reviewed list (`parse_spinoff_splits` over
    `tradepartner/store/spinoff_splits.csv`)."""
    text = resources.files("tradepartner.store").joinpath(_FILE).read_text(encoding="utf-8")
    return parse_spinoff_splits(text)

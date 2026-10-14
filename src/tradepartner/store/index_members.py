"""The index answer key: S&P 500 membership intervals, a check only (#1682, T189).

**This list is never a source.** S&P 500 membership is a selection a
committee made after the fact, with look-ahead and survivorship in it, so it
is never a universe, a signal, a filter, a feature or a trial input. Its one
reader is `health`'s `coverage_reference` block (T189b), which compares the
store's universe with it and reports the misses; no other module under
`src/` imports this loader or names its file (a source scan in
`tests/lookahead/test_check_only_inputs.py` pins that), and every universe,
backtest, paper and research read is the same with the file present, absent
or emptied (spec amendment 2026-10-14, "The check").

The list is the packaged file `index_members.csv`: `#` comment lines first
(the source, the pinned commit, the last dated row of the source, the
derivation date, the upstream provenance and the dataset's MIT notice, which
the licence requires to be kept with every copy), then the header
`ticker,from,to` and one membership interval per row. `from` and `to` are
calendar dates, both inclusive; an empty `to` is an interval still open at
the source's last row. `derive_intervals` builds the rows from the dataset's
historical-components file (one row per change date, the comma-joined
tickers in the index from that date until the next row's date), so a ticker
is a member on day `d` exactly when the last row dated on or before `d` lists
it. The derivation runs once by hand at the pinned commit; a refresh is a
size-S PR that moves the commit and regenerates the file.

`resolve` maps the intervals covering one session to stored securities
through the listing rows (`store.delistings.listing_ends_as_of`'s frame) by
`normalize_ticker`, the normalisation the 2026-10-14 answer-key check used.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, Literal

import polars as pl

from tradepartner.store.delistings import LISTED, TRANSFERRED

#: The file's column header, after the `#` comment lines.
COLUMNS: Final = ("ticker", "from", "to")
#: The comment keys the loader requires (`# key: value`).
REQUIRED_KEYS: Final = ("source", "commit", "through", "derived")
_FILE: Final = "index_members.csv"
_COMMENT: Final = "#"
_KEY_LINE: Final = re.compile(r"^#\s*([a-z_]+):\s*(.*?)\s*$")
_NOT_ALNUM: Final = re.compile(r"[^A-Z0-9]")
_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
#: A class or instrument id is `<cik>:<slug>`; the primary id is the CIK alone.
CLASS_SEPARATOR: Final = ":"

#: How an interval resolved: a listing row of the ticker covering the
#: session, a row of the ticker live elsewhere inside the interval, or none.
Outcome = Literal["covering", "in_interval", "unresolved"]
COVERING: Final = "covering"
IN_INTERVAL: Final = "in_interval"
UNRESOLVED: Final = "unresolved"


class IndexMembersError(ValueError):
    """The index list is malformed; the message names the line."""


@dataclass(frozen=True)
class IndexInterval:
    """One membership interval of one ticker, both ends inclusive."""

    ticker: str
    start: date
    #: The last calendar day in the index; None while still a member at the
    #: source's last row.
    end: date | None

    def covers(self, session: date) -> bool:
        """Whether `session` falls inside the interval."""
        return self.start <= session and (self.end is None or session <= self.end)


@dataclass(frozen=True)
class IndexMembers:
    """The loaded list: its provenance and its intervals, in file order."""

    #: The source repository URL and file.
    source: str
    #: The pinned commit SHA of the source the file was derived from.
    commit: str
    #: The source file's last dated row (an open interval is open from it on).
    through: date
    #: The day the file was derived.
    derived: date
    intervals: tuple[IndexInterval, ...]


@dataclass(frozen=True)
class IndexResolution:
    """One interval covering a session, mapped to a stored security."""

    session: date
    #: The key's ticker, as the file spells it.
    ticker: str
    interval_start: date
    interval_end: date | None
    outcome: Outcome
    #: The resolved security; None when `unresolved`.
    security_id: str | None
    #: The chosen listing row's ticker and `valid_from`; None when `unresolved`.
    listing_ticker: str | None
    listing_valid_from: date | None
    #: The chosen security, then every other security with a row of the
    #: ticker in the same outcome's pool, sorted (a note or class filed under
    #: the name's ticker shows here); empty when `unresolved`.
    candidates: tuple[str, ...]


def normalize_ticker(ticker: str) -> str:
    """The ticker in upper case with every character outside `A-Z0-9`
    dropped (case, dots, slashes, dashes, spaces), as the 2026-10-14 check
    compared them: `BRK.B`, `brk-b`, `BRK/B` and `BRK B` are all `BRKB`."""
    return _NOT_ALNUM.sub("", ticker.upper())


def derive_intervals(
    historical_components_rows: Iterable[tuple[date, str]],
) -> tuple[IndexInterval, ...]:
    """The membership intervals of the dataset's historical-components rows.

    Each row is `(date, tickers)`, `tickers` comma-joined: the index from that
    date until the next row's date. A ticker's interval starts at the first
    row of a run of rows listing it and ends the calendar day before the
    first later row without it; a run reaching the last row is open (`end`
    None). Tickers are stripped and kept as spelled; an empty one is skipped.
    Raises `IndexMembersError` on rows out of date order or a repeated date.
    Sorted by `(ticker, start)`."""
    rows = list(historical_components_rows)
    for (before, _), (after, _) in pairwise(rows):
        if after <= before:
            raise IndexMembersError(f"row dated {after} follows {before}: not in date order")
    open_since: dict[str, date] = {}
    out: list[IndexInterval] = []
    for row_date, joined in rows:
        present = {t.strip() for t in joined.split(",") if t.strip()}
        for ticker in sorted(set(open_since) - present):
            out.append(IndexInterval(ticker, open_since.pop(ticker), row_date - timedelta(days=1)))
        for ticker in present:
            open_since.setdefault(ticker, row_date)
    out.extend(IndexInterval(ticker, start, None) for ticker, start in open_since.items())
    return tuple(sorted(out, key=lambda i: (i.ticker, i.start)))


def format_index_members(intervals: Iterable[IndexInterval], comments: Sequence[str]) -> str:
    """The file text: each of `comments` as a `# ` line, the header, then one
    row per interval (an open end as an empty field)."""
    buffer = io.StringIO()
    for line in comments:
        buffer.write(f"{_COMMENT} {line}".rstrip() + "\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for interval in intervals:
        end = "" if interval.end is None else interval.end.isoformat()
        writer.writerow((interval.ticker, interval.start.isoformat(), end))
    return buffer.getvalue()


def _date(line: int, field: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise IndexMembersError(f"{_FILE} line {line}: {field} {value!r} is not a date") from None


def parse_index_members(text: str) -> IndexMembers:
    """The list in `text` (the packaged file's format). Raises
    `IndexMembersError`, naming the line, on a missing `# key:` among
    `REQUIRED_KEYS`, a `commit` that is not a 40-hex SHA, a header other than
    `COLUMNS`, a short or long row, an empty ticker, a bad date, an end
    before its start, or two intervals of one ticker that overlap."""
    keys: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines) and lines[index].startswith(_COMMENT):
        if (match := _KEY_LINE.match(lines[index])) is not None:
            keys.setdefault(match.group(1), match.group(2))
        index += 1
    for key in REQUIRED_KEYS:
        if not keys.get(key):
            raise IndexMembersError(f"{_FILE}: the comment line '# {key}:' is missing")
    commit = keys["commit"].split()[0]
    if not _SHA.match(commit):
        raise IndexMembersError(f"{_FILE}: commit {commit!r} is not a 40-hex SHA")
    reader = csv.reader(lines[index:])
    header = next(reader, None)
    first = index + 1
    if header is None or tuple(header) != COLUMNS:
        raise IndexMembersError(f"{_FILE} line {first}: header {header!r} is not {list(COLUMNS)}")
    intervals: list[IndexInterval] = []
    last: dict[str, IndexInterval] = {}
    for offset, values in enumerate(reader, start=1):
        line = first + offset
        if not values:
            continue
        if len(values) != len(COLUMNS):
            raise IndexMembersError(f"{_FILE} line {line}: {len(values)} fields, not 3")
        ticker = values[0].strip()
        if not ticker:
            raise IndexMembersError(f"{_FILE} line {line}: empty ticker")
        start = _date(line, "from", values[1].strip())
        end = _date(line, "to", values[2].strip()) if values[2].strip() else None
        if end is not None and end < start:
            raise IndexMembersError(f"{_FILE} line {line}: to {end} is before from {start}")
        held = last.get(ticker)
        if held is not None and (held.end is None or start <= held.end):
            raise IndexMembersError(f"{_FILE} line {line}: {ticker} overlaps its earlier interval")
        interval = IndexInterval(ticker, start, end)
        last[ticker] = interval
        intervals.append(interval)
    return IndexMembers(
        source=keys["source"],
        commit=commit,
        through=_date(1, "through", keys["through"]),
        derived=_date(1, "derived", keys["derived"]),
        intervals=tuple(intervals),
    )


def load_index_members(path: Path | None = None) -> IndexMembers:
    """The list at `path`, or the packaged `tradepartner/store/index_members.csv`
    when `path` is None (`parse_index_members`). A missing file raises
    `FileNotFoundError`; the caller decides what an absent key means."""
    if path is None:
        text = resources.files("tradepartner.store").joinpath(_FILE).read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return parse_index_members(text)


@dataclass(frozen=True)
class _Row:
    security_id: str
    ticker: str
    valid_from: date
    #: The row's live span ends here (inclusive); None while open. The span
    #: is empty when `live_end < valid_from` (a delisted row with no bar).
    live_end: date | None


def _live_end(row: Mapping[str, Any], next_from: date | None) -> date | None:
    """The last day `row` is its security's current listing and live: its
    own end (`health._covers`'s rule: listed, or a transfer with no end yet,
    is open; else its end session) capped at the day before its security's
    next listing row starts (universe rule 2's current listing)."""
    status, end = row["status"], row["end_session"]
    own: date | None
    if status == LISTED or (status == TRANSFERRED and end is None):
        own = None
    elif end is None:
        own = row["valid_from"] - timedelta(days=1)
    else:
        own = end
    cap = None if next_from is None else next_from - timedelta(days=1)
    if own is None:
        return cap
    return own if cap is None else min(own, cap)


def _rows_by_ticker(listings_frame: pl.DataFrame, wanted: set[str]) -> dict[str, list[_Row]]:
    starts: dict[str, list[date]] = defaultdict(list)
    for sid, start in listings_frame.select("security_id", "valid_from").unique().iter_rows():
        starts[sid].append(start)
    for values in starts.values():
        values.sort()
    out: dict[str, list[_Row]] = defaultdict(list)
    for row in listings_frame.select(
        "security_id", "ticker", "valid_from", "status", "end_session"
    ).iter_rows(named=True):
        key = normalize_ticker(row["ticker"] or "")
        if key not in wanted:
            continue
        later = [s for s in starts[row["security_id"]] if s > row["valid_from"]]
        out[key].append(
            _Row(
                security_id=row["security_id"],
                ticker=row["ticker"],
                valid_from=row["valid_from"],
                live_end=_live_end(row, later[0] if later else None),
            )
        )
    return out


def _span_end(row: _Row) -> date | None:
    """The end of the row's live span; its `valid_from` when the span is
    empty, so a row with no live day still marks the day it started."""
    if row.live_end is not None and row.live_end < row.valid_from:
        return row.valid_from
    return row.live_end


def _distance(row: _Row, session: date) -> int:
    """Days from `session` to the row's span (`_span_end`)."""
    end = _span_end(row)
    if session < row.valid_from:
        return (row.valid_from - session).days
    return 0 if end is None or session <= end else (session - end).days


def _resolve_one(interval: IndexInterval, session: date, rows: list[_Row]) -> IndexResolution:
    def covering(row: _Row) -> bool:
        return row.valid_from <= session and (row.live_end is None or session <= row.live_end)

    def inside(row: _Row) -> bool:
        end = _span_end(row)
        return (interval.end is None or row.valid_from <= interval.end) and (
            end is None or end >= interval.start
        )

    exact = interval.ticker.strip().upper()
    outcome: Outcome = COVERING
    pool = [r for r in rows if covering(r)]
    if not pool:
        outcome = IN_INTERVAL
        pool = [r for r in rows if inside(r)]
    if not pool:
        return IndexResolution(
            session=session,
            ticker=interval.ticker,
            interval_start=interval.start,
            interval_end=interval.end,
            outcome=UNRESOLVED,
            security_id=None,
            listing_ticker=None,
            listing_valid_from=None,
            candidates=(),
        )

    def rank(row: _Row) -> tuple[bool, int, bool, str]:
        # An exact spelling before a normalised one (ZBH, not Zions' ZB/H),
        # then the row nearest the session, then an issuer's primary id
        # before its class and instrument ids (MSFT, not a note filed under
        # the common's ticker), then the id.
        return (
            row.ticker.strip().upper() != exact,
            _distance(row, session),
            CLASS_SEPARATOR in row.security_id,
            row.security_id,
        )

    pool.sort(key=rank)
    chosen = pool[0]
    others = sorted({r.security_id for r in pool} - {chosen.security_id})
    return IndexResolution(
        session=session,
        ticker=interval.ticker,
        interval_start=interval.start,
        interval_end=interval.end,
        outcome=outcome,
        security_id=chosen.security_id,
        listing_ticker=chosen.ticker,
        listing_valid_from=chosen.valid_from,
        candidates=(chosen.security_id, *others),
    )


def resolve(
    intervals: Iterable[IndexInterval], session: date, listings_frame: pl.DataFrame
) -> list[IndexResolution]:
    """Each interval covering `session`, mapped to a stored security.

    `listings_frame` is `store.delistings.listing_ends_as_of`'s frame at the
    caller's `t` (non-retracted rows known at `t`, with `status` and
    `end_session`). A listing row's live span is the days it is its
    security's current listing (universe rule 2: until the day before the
    security's next row starts) and not ended (`listed`, or a transfer with
    no end yet, is open; else it ends at its `end_session`; a delisted row
    with no bar has no span). Rows are matched by `normalize_ticker`. The
    interval resolves to, in order:

    - `covering`: a row of the ticker whose live span holds `session`;
    - `in_interval`: else a row of the ticker whose live span (or, when that
      is empty, its `valid_from`) meets the interval, the fallback for a name
      whose rows start late or end early; never a row live only outside the
      interval, so a reused ticker's other holder is not the key's name;
    - `unresolved`: else none (a ticker no stored row carries).

    Among several rows: an exact spelling (upper-cased) before a normalised
    match, then the nearest live span to `session`, then an issuer's primary
    id (no `:`) before its class and instrument ids, then `security_id`.
    Pure. Sorted by the key's ticker."""
    covering = sorted(
        (i for i in intervals if i.covers(session)), key=lambda i: (i.ticker, i.start)
    )
    wanted = {normalize_ticker(i.ticker) for i in covering}
    rows = _rows_by_ticker(listings_frame, wanted)
    return [_resolve_one(i, session, rows.get(normalize_ticker(i.ticker), [])) for i in covering]

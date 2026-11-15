"""The identity answer key against a store (#3275, ADR 0029, T396): check only.

`check` runs each history of `identity_answer_key` against a store through the
resolver and the listing ends, and returns one `Miss` per expectation the store
does not meet, each naming the fact's citation. It reads through a connection
the caller opens read-only, writes nothing, and nothing on a universe, signal,
backtest or research path imports it or the key (the source scan in
`tests/lookahead/test_check_only_inputs.py`).

How the store's answer becomes the key's `Observed` (outside identifiers):

- A store id is mapped to its CIK by its `securities` row (latest revision known
  at `at`); a security with none is not read.
- `holds`: for each ticker the history names, each calendar day from the
  history's first fact to `at`'s day takes the holder `repair.store_resolver`
  gives that ticker on the day's session (a non-session day takes the session
  before it, so a holder keeps a weekend). A holder on `at`'s day is open.
- `bars`: the sessions with a traded Alpaca bar (volume above zero, latest
  revision known at `at`) for the CIK's securities, joined where consecutive
  sessions follow each other, for every (CIK, ticker) the history names.
- `line_end`: from `listing_ends_as_of`, the common-class listing rows of the
  CIK's securities on the exchange (exchange names compare upper-cased with
  spaces as underscores: "NYSE Chicago" is `NYSE_CHICAGO`). A line is open if
  any such security's latest row there is listed, else it ended on the last of
  their `end_session`s; a line whose end the store does not know is unknown.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from tradepartner.adapters.alpaca_prices import BAR_SOURCES, ListingResolver
from tradepartner.calendar import is_session, last_completed_session, next_session, previous_session
from tradepartner.config import Settings
from tradepartner.repair import store_resolver
from tradepartner.store.asof import _validate_t
from tradepartner.store.delistings import LISTED, listing_ends_as_of
from tradepartner.store.identity_answer_key import (
    AnswerKeyError,
    History,
    Miss,
    Observed,
    Span,
    load_answer_key,
    match,
)
from tradepartner.store.master import _is_common, securities_as_of


@dataclass(frozen=True)
class CheckReport:
    """The result of a run: how many histories, and every miss."""

    at: datetime
    history_ids: tuple[str, ...]
    misses: tuple[Miss, ...]

    @property
    def histories(self) -> int:
        """How many histories were run."""
        return len(self.history_ids)

    def misses_of(self, history_id: str) -> tuple[Miss, ...]:
        """The misses of one history."""
        return tuple(m for m in self.misses if m.history == history_id)


def exchange_key(name: str) -> str:
    """The store's spelling of a key exchange name: "NYSE Chicago" -> NYSE_CHICAGO."""
    return "_".join(name.upper().split())


def _ciks(conn: duckdb.DuckDBPyConnection, at: datetime) -> dict[str, str]:
    """`security_id -> cik` from the latest `securities` row known at `at`."""
    frame = securities_as_of(conn, at)
    rows = frame.select("security_id", "cik").iter_rows()
    return {str(sid): str(cik) for sid, cik in rows}


def _pairs(history: History) -> set[tuple[str, str]]:
    """Every (cik, ticker) the history names, in inputs, bars and expectations."""
    pairs = {(i.cik, i.ticker) for i in history.inputs}
    pairs |= {(b.cik, b.ticker) for b in history.bars}
    pairs |= {(e.cik, e.ticker) for e in history.expected if e.ticker is not None}
    return pairs


def _earliest(history: History) -> date:
    days = [i.date for i in history.inputs] + [b.first for b in history.bars]
    days += [e.first for e in history.expected if e.first is not None]
    days += [e.last for e in history.expected if e.last is not None]
    return min(days)


def _session_on_or_before(day: date) -> date:
    return day if is_session(day) else previous_session(day)


def _holds(
    resolver: ListingResolver,
    ciks: dict[str, str],
    tickers: Iterable[str],
    first: date,
    end: date,
) -> list[Span]:
    """Calendar-day holders per ticker from `first` to `end`, as run-length spans."""
    spans: list[Span] = []
    for ticker in sorted(set(tickers)):
        run: tuple[str, date] | None = None
        day = first
        while day <= end:
            sid = resolver.resolve(ticker, _session_on_or_before(day))
            holder = ciks.get(sid) if sid is not None else None
            if run is not None and holder != run[0]:
                spans.append(Span(run[0], ticker, run[1], day - timedelta(days=1)))
                run = None
            if run is None and holder is not None:
                run = (holder, day)
            day += timedelta(days=1)
        if run is not None:
            spans.append(Span(run[0], ticker, run[1], None))
    return spans


def _bar_sessions(
    conn: duckdb.DuckDBPyConnection, at: datetime, security_ids: Sequence[str]
) -> list[date]:
    if not security_ids:
        return []
    rows = conn.execute(
        "SELECT DISTINCT session FROM ("
        "  SELECT security_id, session, volume, source FROM prices_daily"
        "  WHERE known_at <= ? AND list_contains(?, security_id)"
        "  QUALIFY row_number() OVER"
        "    (PARTITION BY security_id, session ORDER BY known_at DESC) = 1"
        ") WHERE volume > 0 AND list_contains(?, source) ORDER BY session",
        [at, list(security_ids), sorted(BAR_SOURCES)],
    ).fetchall()
    return [r[0] for r in rows]


def _bar_spans(cik: str, ticker: str, sessions: Sequence[date], latest: date) -> list[Span]:
    """Runs of consecutive trading sessions; a run reaching the last completed
    session is open."""
    spans: list[Span] = []
    start: date | None = None
    prev: date | None = None
    for session in sessions:
        if prev is not None and start is not None and next_session(prev) != session:
            spans.append(Span(cik, ticker, start, prev))
            start = None
        if start is None:
            start = session
        prev = session
    if start is not None and prev is not None:
        spans.append(Span(cik, ticker, start, None if prev >= latest else prev))
    return spans


def _line_ends(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    at: datetime,
    ciks: dict[str, str],
) -> dict[tuple[str, str], date | None]:
    """`(cik, store exchange) -> the line's last session`, None for a line still
    listed; a line whose end is not known is left out."""
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in listing_ends_as_of(conn, at, settings).iter_rows(named=True):
        cik = ciks.get(row["security_id"])
        title = row["class_title"]
        if cik is None or (title is not None and not _is_common(title)):
            continue
        key = (cik, row["exchange"], row["security_id"])
        held = latest.get(key)
        if held is None or row["valid_from"] > held["valid_from"]:
            latest[key] = row
    lines: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (cik, exchange, _), row in latest.items():
        lines[(cik, exchange)].append(row)
    out: dict[tuple[str, str], date | None] = {}
    for line, rows in lines.items():
        if any(r["status"] == LISTED for r in rows):
            out[line] = None
        elif all(r["end_session"] is not None for r in rows):
            out[line] = max(r["end_session"] for r in rows)
    return out


def observe(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    history: History,
    at: datetime,
    *,
    resolver: ListingResolver | None = None,
    ciks: dict[str, str] | None = None,
    line_ends: dict[tuple[str, str], date | None] | None = None,
) -> Observed:
    """What the store says about `history`, in the key's terms (module docstring).
    The resolver, CIK map and line ends are the whole store's: pass them to share
    them across histories."""
    at = _validate_t(at)
    ciks = _ciks(conn, at) if ciks is None else ciks
    resolver = store_resolver(conn, at, settings) if resolver is None else resolver
    line_ends = _line_ends(conn, settings, at, ciks) if line_ends is None else line_ends
    pairs = _pairs(history)
    holds = _holds(resolver, ciks, {t for _, t in pairs}, _earliest(history), at.date())
    latest = last_completed_session(at)
    by_cik: dict[str, list[str]] = defaultdict(list)
    for sid, cik in ciks.items():
        by_cik[cik].append(sid)
    bars: list[Span] = []
    for cik, ticker in sorted(pairs):
        sessions = _bar_sessions(conn, at, by_cik.get(cik, []))
        bars.extend(_bar_spans(cik, ticker, sessions, latest))
    named = {(e.cik, e.exchange) for e in history.expected if e.exchange is not None}
    observed_ends: dict[tuple[str, str], date | None] = {}
    for cik, exchange in named:
        key = (cik, exchange_key(exchange))
        if key in line_ends:
            observed_ends[(cik, exchange)] = line_ends[key]
    return Observed(holds=tuple(holds), bars=tuple(bars), line_ends=observed_ends)


def check(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    histories: Sequence[History],
    at: datetime,
) -> CheckReport:
    """Run every history against the store as known at `at`; read-only."""
    at = _validate_t(at)
    ciks = _ciks(conn, at)
    resolver = store_resolver(conn, at, settings)
    line_ends = _line_ends(conn, settings, at, ciks)
    misses: list[Miss] = []
    for history in histories:
        observed = observe(
            conn, settings, history, at, resolver=resolver, ciks=ciks, line_ends=line_ends
        )
        misses.extend(match(history, observed))
    return CheckReport(at=at, history_ids=tuple(h.id for h in histories), misses=tuple(misses))


def report_lines(report: CheckReport, store_path: str) -> list[str]:
    """The printed report: a line per history, then a line per miss naming its
    citation, then the totals."""
    lines = [
        f"identity-check at {report.at.isoformat()} (read-only, nothing written)",
        f"store: {store_path}",
    ]
    for hid in report.history_ids:
        missed = report.misses_of(hid)
        lines.append(f"{hid}: " + (f"FAIL ({len(missed)})" if missed else "ok"))
        lines.extend(f"  miss {miss}" for miss in missed)
    lines.append(f"identity-check: {report.histories} histories, {len(report.misses)} misses")
    return lines


__all__ = [
    "AnswerKeyError",
    "CheckReport",
    "check",
    "exchange_key",
    "load_answer_key",
    "observe",
    "report_lines",
]

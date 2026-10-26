"""The Home page's view model (plan T273, amendment 2026-10-26 #2170): the status
line, "Needs you", one card per book, Data status and the Research strip.

`build(conn, settings, now)` makes one read pass through the shell's read-only
connection and returns plain frozen dataclasses: no write, no broker, no run, no
Streamlit object. Every number comes from a reader that already exists
(`ops.book_summaries`, `ops.waiting_on_owner`, `ops.performance_data`,
`book_stats`, `book_activity`, `book_holdings`, `health`, the research page's
view); this module only chooses words. Nothing here executes a command: an
action is the text the owner types or a pointer to where to look.

**As of.** A card is cut at the last session completed at `now`; a mark of a
later session, or a blotter row known after `now`, moves nothing. The readers
beneath (`ops.performance_data`, `ops.book_summaries`) take the real clock for
their own header and next-rebalance reads; `now` is theirs only where they take it.

**The status line** is red for a halted book or a missed run, amber for any other
item waiting on the owner, and "Everything is running normally" otherwise.

**Data status.** The records counted are the open quarantined filings on the
latest EDGAR ingest row (`health.last_ingests`, the count `paper`'s health
warning reads) plus the securities with a hole in their bars (`health.bar_gaps`).
They are "trading affected" only when an open book's latest run was `halted`
(a failed data check stops a run that way); otherwise "trading not affected".
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Literal

import duckdb

from tradepartner import health
from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings
from tradepartner.dashboard import header, research_page
from tradepartner.execution import book_activity, book_holdings, book_stats, ops, waiting
from tradepartner.store import registry, schema
from tradepartner.timeutil import ensure_tz_aware_utc

__all__ = [
    "BLURBS_PATH",
    "RESEARCH_PAGE",
    "BookCard",
    "DataStatus",
    "HomeView",
    "NeedsYou",
    "ResearchStrip",
    "StatusLine",
    "build",
    "strategy_sentence",
]

_LOG = logging.getLogger(__name__)

#: The checked-in one-liners, keyed by hypothesis slug (open question 3).
BLURBS_PATH: Final = Path(__file__).resolve().parents[3] / "docs" / "ui" / "book_blurbs.toml"
#: The Research page the strip links to (the page's route name).
RESEARCH_PAGE: Final = "research"
#: The ingest line's count, as `cli._QUARANTINED` reads it (`ingest._source_counts`).
_QUARANTINED = re.compile(r"\bquarantined: (\d+)\b")

Level = Literal["ok", "amber", "red"]

#: Kind -> (title, one sentence with the recommendation, action when the item has no
#: command). `overdue_delisting` is `ops.OverdueDelisting`; the rest are `WaitingKind`s.
_WORDS: Final[dict[str, tuple[str, str, str | None]]] = {
    "kill_switch_engaged": (
        "Trading is stopped",
        "The kill switch is on: read the reason, fix what caused it, then resume.",
        None,
    ),
    "order_lagging": (
        "An order has not settled",
        "Check the order in Alpaca first; settle it here only if Alpaca no longer knows it.",
        None,
    ),
    "overdue_delisting": (
        "Cash for a delisted name has not arrived",
        "Check Alpaca's activity for the payout; nothing here needs doing until it shows.",
        "Check Alpaca's activity",
    ),
    "missed_run": (
        "A daily run did not happen",
        "Check the Mac was awake and the job's last exit code, then let tomorrow's run go.",
        "Check docs/runbooks/scheduling.md",
    ),
    "shakedown_note": (
        "A shakedown alert needs a note",
        "Write a line saying what happened, so the shakedown can count the day.",
        None,
    ),
    "report_due": (
        "A tracking report is due",
        "Run the report once; it counts as one test.",
        None,
    ),
    "unreadable": (
        "Some records could not be read",
        "Look at the book's status before trusting its numbers.",
        "tradepartner paper status --book {book}",
    ),
}
#: Most urgent first; `waiting.KIND_ORDER` with delisting cash after the lagging order.
_URGENCY: Final[tuple[str, ...]] = (
    "kill_switch_engaged",
    "order_lagging",
    "overdue_delisting",
    "missed_run",
    "shakedown_note",
    "report_due",
    "unreadable",
)
_RED_KINDS: Final = frozenset({"kill_switch_engaged", "missed_run"})


@dataclass(frozen=True)
class NeedsYou:
    """One "Needs you" item: `action` is text to show (a command to copy or where
    to look); nothing runs it."""

    book_id: str
    kind: str
    title: str
    sentence: str
    action: str


@dataclass(frozen=True)
class StatusLine:
    """The page's first line. `next_run` is display text from `ui.next_run_time_et`;
    `next_rebalance` the earliest open book's `next_rebalance_session`."""

    level: Level
    text: str
    next_run: str
    next_rebalance: date | None


@dataclass(frozen=True)
class BookCard:
    """One book's card. A number is None when its reader had nothing or could not
    read the rows; `unreadable` then says so (never the raw error)."""

    book_id: str
    pill: str
    strategy: str
    session: date | None
    equity: float | None
    since_start_pct: float | None
    spy_since_start_pct: float | None
    digest: str | None
    holdings_count: int | None
    cash: float | None
    top_names: tuple[str, ...]
    unreadable: bool


@dataclass(frozen=True)
class DataStatus:
    """The data line; `records` is the count that need tidying."""

    text: str
    records: int
    trading_affected: bool


@dataclass(frozen=True)
class ResearchStrip:
    """The latest research decision or registration; `link` is the page's route."""

    text: str
    title: str | None
    at: datetime | None
    link: str


@dataclass(frozen=True)
class HomeView:
    """Everything the Home page draws."""

    status: StatusLine
    needs_you: tuple[NeedsYou, ...]
    books: tuple[BookCard, ...]
    data: DataStatus
    research: ResearchStrip


def _blurbs(path: Path = BLURBS_PATH) -> dict[str, str]:
    """The slug -> sentence table; a missing or unreadable file is an empty table
    (the card then shows the registry title)."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8")).get("blurbs", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _LOG.warning("book blurbs not read from %s: %s", path, type(exc).__name__)
        return {}
    return {str(k): str(v) for k, v in table.items()}


def strategy_sentence(
    conn: duckdb.DuckDBPyConnection, book: ops.BookSummary, blurbs_path: Path = BLURBS_PATH
) -> str:
    """The book's one plain sentence: its hypothesis slug's entry in
    `docs/ui/book_blurbs.toml`, else the registry title, else the book's token."""
    try:
        hypothesis = registry.get_hypothesis_by_id(conn, book.window.hypothesis_id)
    except registry.UnknownHypothesis:
        return f"Book {book.book_id}"
    return _blurbs(blurbs_path).get(hypothesis.slug, hypothesis.title)


def _needs_you(
    items: tuple[waiting.WaitingItem, ...], overdue: tuple[ops.OverdueDelisting, ...]
) -> tuple[NeedsYou, ...]:
    out: list[NeedsYou] = []
    for item in items:
        title, sentence, fallback = _WORDS[item.kind]
        action = item.command or (fallback or "").format(book=item.book_id)
        out.append(NeedsYou(item.book_id, item.kind, f"{title} ({item.book_id})", sentence, action))
    for late in overdue:
        title, sentence, fallback = _WORDS["overdue_delisting"]
        out.append(
            NeedsYou(
                late.book_id,
                "overdue_delisting",
                f"{title} ({late.book_id}, {late.symbol})",
                sentence,
                fallback or "",
            )
        )
    return tuple(sorted(out, key=lambda n: (_URGENCY.index(n.kind), n.book_id)))


def _status(
    settings: Settings,
    summaries: tuple[ops.BookSummary, ...],
    needs: tuple[NeedsYou, ...],
    unreadable: tuple[str, ...] = (),
) -> StatusLine:
    clock = f"Next run at {settings.ui.next_run_time_et} ET"
    upcoming = [s.next_rebalance_session for s in summaries if s.next_rebalance_session]
    nxt = min(upcoming, default=None)
    halted = [s.book_id for s in summaries if s.is_open and s.switch_state.engaged]
    if halted or any(n.kind in _RED_KINDS for n in needs):
        if halted:
            text = f"{', '.join(halted)} is halted"
        elif any(n.kind == "kill_switch_engaged" for n in needs):
            text = "A book is halted"
        else:
            text = "A daily run was missed"
        return StatusLine("red", text, clock, nxt)
    if unreadable:
        text = f"Some records could not be read ({', '.join(unreadable)}); trading is not affected"
        return StatusLine("amber", text, clock, nxt)
    if needs:
        word = "item needs" if len(needs) == 1 else "items need"
        text = f"{len(needs)} {word} a look; trading is not affected"
        return StatusLine("amber", text, clock, nxt)
    return StatusLine("ok", "Everything is running normally", clock, nxt)


def _pill(summary: ops.BookSummary) -> str:
    if summary.switch_state.engaged:
        return "Halted"
    if not summary.is_open:
        return "Closed"
    if summary.last_run_session is None:
        return f"Starts {summary.window.first_rebalance_session:%A}"
    return "Trading"


def _card(
    conn: duckdb.DuckDBPyConnection, settings: Settings, summary: ops.BookSummary, now: datetime
) -> BookCard:
    unreadable = False
    session: date | None = None
    equity = since = spy = None
    digest: str | None = None
    count: int | None = None
    cash: float | None = None
    top: tuple[str, ...] = ()
    window_id = summary.window.window_id
    cut = last_completed_session(now)
    try:
        perf = ops.performance_data(conn, settings, summary.book_id)
        if perf is not None and perf.base_session is not None:
            series = {d: v for d, v in perf.equity if d <= cut}
            # The base point alone (every mark is after the cut) is no mark at all.
            if any(d > perf.base_session for d in series):
                session = max(series)
                equity = series[session]
                bench = {d: v for d, v in perf.benchmark_index if d <= cut}
                piece = book_stats.range_slice(series, bench or None, "since_start", session)
                since, spy = piece.book_return_pct, piece.spy_return_pct
    except ValueError:
        unreadable = True
    if session is not None and window_id is not None:
        try:
            limit = settings.dashboard.page_row_limit
            digest = book_activity.today(conn, window_id, session, now, limit=limit).digest
            held = book_holdings.read_holdings(
                conn, window_id, session, top_n=settings.ui.holdings_rows
            )
            count, cash = held.summary.count, held.summary.cash
            top = tuple(row.ticker for row in held.summary.largest)
        except ValueError:  # includes the lot ledger's and the marks' errors
            unreadable = True
    return BookCard(
        summary.book_id, _pill(summary), strategy_sentence(conn, summary), session, equity,
        since, spy, digest, count, cash, top, unreadable,
    )  # fmt: skip


def _data_status(
    conn: duckdb.DuckDBPyConnection, summaries: tuple[ops.BookSummary, ...], now: datetime
) -> DataStatus:
    fresh = header.store_freshness(conn, now).as_of
    quarantined = 0
    for status in health.last_ingests(conn, now):
        if status.source == "edgar" and status.latest_message:
            found = _QUARANTINED.findall(status.latest_message)
            quarantined = int(found[-1]) if found else 0
    holes = health.bar_gaps(conn, now).names_with_gaps
    records = quarantined + holes
    affected = any(s.is_open and s.last_run_status == "halted" for s in summaries)
    if records:
        tail = "trading affected" if affected else "trading not affected"
        return DataStatus(f"{records} records need tidying; {tail}", records, affected)
    if fresh is None:
        return DataStatus("No data yet", 0, False)
    through = last_completed_session(fresh)
    return DataStatus(f"Data is up to date through {through.isoformat()}", 0, False)


def _research_strip(conn: duckdb.DuckDBPyConnection, now: datetime) -> ResearchStrip:
    try:
        view = research_page.load_research_view(conn)
    except schema.ResearchNotInitialised:
        return ResearchStrip("Nothing new yet", None, None, RESEARCH_PAGE)
    titles = {r.slug: r.title for r in view.registrations}
    found: list[tuple[datetime, str, str]] = [
        (d.known_at, f"Decision: {' '.join(d.reason.split())}", "A research decision was made")
        for d in view.decisions
    ] + [
        (r.known_at, titles.get(r.slug, r.slug), "A research run was registered") for r in view.runs
    ]
    found = [f for f in found if f[0] <= now]
    if not found:
        return ResearchStrip("Nothing new yet", None, None, RESEARCH_PAGE)
    at, title, text = max(found, key=lambda f: f[0])
    return ResearchStrip(text, title, at, RESEARCH_PAGE)


def build(conn: duckdb.DuckDBPyConnection, settings: Settings, now: datetime) -> HomeView:
    """The Home page at `now` (module docstring); read-only through `conn`."""
    now = ensure_tz_aware_utc(now, field_name="now")
    summaries = ops.book_summaries(conn, settings)
    needs = _needs_you(ops.waiting_on_owner(conn, settings, now), ops.overdue_delisting_cash(conn))
    cards = tuple(_card(conn, settings, s, now) for s in summaries)
    return HomeView(
        status=_status(settings, summaries, needs, tuple(c.book_id for c in cards if c.unreadable)),
        needs_you=needs,
        books=cards,
        data=_data_status(conn, summaries, now),
        research=_research_strip(conn, now),
    )

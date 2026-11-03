"""Per-book buy timing: the pure arm function (paper spec amendment 2026-11-01, T369)
and the per-book submit window (amendment 2026-11-03 item 1, T377).

The arm of a session is a function of the configured timing, the start and the
calendar, never of anything the run observes (item 1). A book's submit window is a
function of its settings and its session's open only; each caller passes the open from
its own calendar, and the sandbox branch (a filesystem read) stays in the callers.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from tradepartner.calendar import is_session
from tradepartner.config import (
    _BOOK_TOKEN,
    MIDSESSION_LATEST_START_MARGIN_MINUTES,
    BookExecution,
)

if TYPE_CHECKING:
    from tradepartner.config import Settings


def execution_for(settings: Settings, book_id: str) -> BookExecution:
    """The book's `paper.execution` entry, or the defaults (`after_sells`, c = 0,
    `none`) for a book absent from the map. Refuses a malformed book token and a non-session
    `alternate_start`."""
    if _BOOK_TOKEN.fullmatch(book_id) is None:
        raise ValueError(f"book_id must match ^[A-Za-z0-9]+$, got {book_id!r}")
    ex = settings.paper.execution.get(book_id, BookExecution())
    if ex.alternate_start is not None and not is_session(ex.alternate_start):
        raise ValueError(f"alternate_start {ex.alternate_start} is not an XNYS session")
    return ex


def arm_for(
    execution: BookExecution, session: date, sessions: Sequence[date]
) -> Literal["V0", "V1"]:
    """The arm of `session`: V1 when buys go at the open, V0 otherwise.

    `sessions` is the ascending XNYS session list. Under `alternate`, V1 when the
    session's ordinal counted from `alternate_start` (ordinal 0) is even; V0 when it is
    odd or the session is before the start.
    """
    if execution.buy_timing == "at_open":
        return "V1"
    if execution.buy_timing == "after_sells":
        return "V0"
    start = execution.alternate_start
    if start is None:
        raise ValueError("buy_timing 'alternate' requires alternate_start")
    if session < start:
        return "V0"
    i = bisect_left(sessions, session)
    if i >= len(sessions) or sessions[i] != session:
        raise ValueError(f"{session} is not in the session list")
    j = bisect_left(sessions, start)
    if j >= len(sessions) or sessions[j] != start:
        raise ValueError(f"alternate_start {start} is not in the session list")
    return "V1" if (i - j) % 2 == 0 else "V0"


def allowed_time_in_force(execution: BookExecution) -> frozenset[str]:
    """The time-in-force values the book's orders may carry: `{day}`, plus `opg` when
    its `auction` is `opening` (order-shape check input, item 4)."""
    if execution.auction == "opening":
        return frozenset({"day", "opg"})
    return frozenset({"day"})


def submit_window_for(
    settings: Settings, book_id: str, opening: datetime
) -> tuple[datetime, datetime]:
    """The book's submit window on the session whose open is `opening` (amendment
    2026-11-03 item 1), both ends inclusive.

    A book absent from `paper.execution` or at `open` gets today's [open -
    `paper.submit_window_before_open_minutes`, open +
    `paper.submit_window_after_open_minutes`]; a `midsession` book gets [open +
    `midsession_start_minutes_after_open`, that + `midsession_window_minutes`].
    """
    if opening.tzinfo is None:
        raise ValueError(f"opening must be tz-aware, got {opening!r}")
    ex = execution_for(settings, book_id)
    if ex.submit_at == "midsession":
        start = opening + timedelta(minutes=ex.midsession_start_minutes_after_open)
        return start, start + timedelta(minutes=ex.midsession_window_minutes)
    paper = settings.paper
    return (
        opening - timedelta(minutes=paper.submit_window_before_open_minutes),
        opening + timedelta(minutes=paper.submit_window_after_open_minutes),
    )


def latest_batch_start(settings: Settings, book_id: str, opening: datetime) -> datetime | None:
    """The latest instant a `midsession` book's batch may start (inclusive): its window's
    end less `paper.sell_wait_seconds` less 10 minutes (item 1), so a batch that starts
    has room for the sells' wait and the buys inside the window. None for a book at
    `open`, which has no such rule."""
    if execution_for(settings, book_id).submit_at != "midsession":
        return None
    _start, end = submit_window_for(settings, book_id, opening)
    return end - timedelta(
        seconds=settings.paper.sell_wait_seconds,
        minutes=MIDSESSION_LATEST_START_MARGIN_MINUTES,
    )


def is_midsession(settings: Settings, book_id: str) -> bool:
    """Whether the book's `paper.execution` entry submits mid-session."""
    return execution_for(settings, book_id).submit_at == "midsession"

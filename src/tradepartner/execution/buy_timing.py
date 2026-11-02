"""Per-book buy timing: the pure arm function (paper spec amendment 2026-11-01, T369).

The arm of a session is a function of the configured timing, the start and the
calendar, never of anything the run observes (item 1). Nothing on the order path reads
these until T370.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Literal

from tradepartner.config import BookExecution

if TYPE_CHECKING:
    from tradepartner.config import Settings

_BOOK_TOKEN = re.compile(r"^[A-Za-z0-9]+$")


def execution_for(settings: Settings, book_id: str) -> BookExecution:
    """The book's `paper.execution` entry, or the defaults (`after_sells`, c = 0,
    `none`) for a book absent from the map. Refuses a malformed book token."""
    if _BOOK_TOKEN.fullmatch(book_id) is None:
        raise ValueError(f"book_id must match ^[A-Za-z0-9]+$, got {book_id!r}")
    return settings.paper.execution.get(book_id, BookExecution())


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

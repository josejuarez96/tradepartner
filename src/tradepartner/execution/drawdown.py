"""The drawdown check shared by `run._drawdown` and `paper resume` (#648).

`check(window, marks_rows, kill_switch_rows, max_drawdown, marked, session)` is
pure over rows already read from the journal: it returns the first session (in
session order) whose ledger equity crosses the drawdown bound, or `None`.

**Selection.** There is nothing to check when `marks_rows` is empty (before
the peak is even computed). Otherwise the sessions checked are: every marked
session whose mark row's `known_at` is after the window's last `released`
kill-switch event (every marked session when there is none), plus `marked`
and the window's last marked session. `switch.drawdown_peak` is constant
between releases (the last `released` row's `peak_equity`, else
`starting_equity`) and `switch.drawdown_armed` is disarmed from a `drawdown`
engagement until the next release, so the trigger fires once per crossing
however many times a session already checked is checked again: re-checking it
is idempotent.

**The back-filled label.** A crossing is labeled back-filled, and names the
release's time, only when its session is strictly before `previous_session(S)`
(so an ordinary S-1 mark, read the morning after a release, is never
mislabeled) and that session's own close (`calendar.session_close`) is at or
before the last release's time (so a session the release itself would have
seen, or one later than it, is not labeled either). It is still returned as a
crossing either way (erring toward safety: a release elsewhere in the window
does not excuse a drawdown the owner has not seen) -- the label only says
whether it predates the release.

A caller engages the switch and writes its own alert on the crossing this
returns; this module writes nothing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from tradepartner import calendar
from tradepartner.calendar import previous_session
from tradepartner.execution import switch
from tradepartner.store.journal import KillSwitchRow, PaperWindowRow, PositionDailyRow


@dataclass(frozen=True)
class Crossing:
    """The first session `check` finds with ledger equity below the bound."""

    session: date
    equity: float
    peak: float
    reason: str


def _window_id(window: PaperWindowRow) -> int:
    if window.window_id is None:
        raise ValueError("a paper_windows row without a window_id")
    return window.window_id


def mark_equity(rows: Sequence[PositionDailyRow], day: date) -> float | None:
    """Ledger equity at the marked session `day`: its cash row plus every
    name's value, or None when that session's rows cannot give it."""
    today = [r for r in rows if r.session == day]
    cash = {r.cash for r in today if r.cash is not None}
    values = [r.value for r in today if r.security_id is not None]
    if len(cash) != 1 or any(v is None for v in values):
        return None
    equity = cash.pop() + math.fsum(v for v in values if v is not None)
    return equity if math.isfinite(equity) else None


def check(
    window: PaperWindowRow,
    marks_rows: Sequence[PositionDailyRow],
    kill_switch_rows: Sequence[KillSwitchRow],
    max_drawdown: float,
    marked: set[date],
    session: date,
) -> Crossing | None:
    """The first drawdown crossing among the sessions selected (module
    docstring), or `None`. `session` is the caller's run or resume command's
    session S, used only for the back-filled label's `previous_session(S)`
    boundary."""
    if not marks_rows:
        return None
    peak = switch.drawdown_peak(window, kill_switch_rows)
    armed = switch.drawdown_armed(_window_id(window), kill_switch_rows)
    # The last release in write order (`event_id`), not by `at`: a halt row
    # after a `ClockError` carries a real-time stamp (switch.py), so `at`
    # does not always agree with write order.
    released = sorted(
        (r for r in kill_switch_rows if r.state == switch.RELEASED),
        key=lambda r: r.event_id or 0,
    )
    last_release = released[-1].at if released else None
    known_at_of: dict[date, datetime] = {}
    for row in marks_rows:
        known_at_of[row.session] = min(row.known_at, known_at_of.get(row.session, row.known_at))
    to_check = {
        day
        for day, known_at in known_at_of.items()
        if last_release is None or known_at > last_release
    }
    to_check |= marked | {max(r.session for r in marks_rows)}
    crossed: tuple[date, float] | None = None
    for day in sorted(to_check):
        equity = mark_equity(marks_rows, day)
        if equity is not None and switch.drawdown_check(equity, peak, max_drawdown, armed=armed):
            crossed = day, equity
            break
    if crossed is None:
        return None
    day, equity = crossed
    reason = (
        f"ledger equity {equity:.2f} at {day.isoformat()} is below the peak {peak:.2f} "
        f"by more than risk.max_drawdown {max_drawdown}"
    )
    if (
        last_release is not None
        and day < previous_session(session)
        and calendar.session_close(day) <= last_release
    ):
        reason += f" (back-filled session, before the release at {last_release.isoformat()})"
    return Crossing(session=day, equity=equity, peak=peak, reason=reason)

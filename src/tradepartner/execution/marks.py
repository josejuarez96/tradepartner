"""Marks and lapses, pure (Phase 4 spec req 7 step 5, req 5; plan T63b).

Re-sliced out of `run.py` on 2026-09-30 (#388) so the two computations below
are testable without a broker, a clock or a write connection; the run (T63)
owns the writes and the alerts this task's outputs feed.

- `marks_for` backs a `mark` step's `positions_daily` rows: for every session
  since the window's last mark through S-1, the ledger's split-adjusted
  holding at that session's close, priced by `store.asof.prices_as_of` at the
  same close, with `tradable` carried from the run's own `assets` read (the
  one broker read a mark needs, taken once by the caller and reused for
  every back-filled session).
- `lapses` finds the rebalance sessions a window's catch-up window has run
  out on as of `session`: `catch_up_lapsed` when the frozen
  `paper.max_catch_up_sessions` elapsed with the switch clear throughout,
  `kill_switch` when the switch was engaged on any session of the catch-up
  period -- the rebalance session itself through the boundary session,
  inclusive -- not only on the session checked (#366 Q19(b): a switch
  released one session before the lapse still names the cause; spec req 5:
  "traded after the release if the catch-up window still allows, else
  becomes missed with reason kill_switch").
- `missed_run` is one lookup: whether S-1 has no `paper_runs` row at all, the
  trigger for a `missed_run` alert (req 7).

Nothing here writes a row, delivers an alert or reads a clock.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import duckdb

from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.switch import ENGAGED
from tradepartner.store.asof import prices_as_of
from tradepartner.store.journal import (
    KillSwitchRow,
    PaperRunRow,
    PaperWindowRow,
    RebalanceEventRow,
)

_PAPER_MAX_CATCH_UP = "paper.max_catch_up_sessions"
_EXECUTED = "executed"
_MISSED = "missed"
_CATCH_UP_LAPSED = "catch_up_lapsed"
_KILL_SWITCH = "kill_switch"
_KNOWN_STATUSES = frozenset({_EXECUTED, _MISSED})


@dataclass(frozen=True)
class Mark:
    """One `positions_daily` row's worth of data for `session`: a held
    security's split-adjusted quantity, its close(`session`) mark price and
    value, the ledger's cash at that session, and `tradable` from the run's
    `assets` read. `security_id` is None with `quantity = 0.0` only for a
    session the ledger holds nothing on (the schema's
    `CHECK (security_id IS NOT NULL OR quantity = 0)`), so a flat window
    still gets one row per session."""

    session: date
    security_id: str | None
    quantity: float
    mark_price: float | None
    value: float | None
    cash: float
    tradable: bool | None


@dataclass(frozen=True)
class Missed:
    """One rebalance whose catch-up window has run out as of `lapses`'
    `session` argument, with the reason it is being reported `missed`."""

    rebalance_session: date
    reason: str  # "catch_up_lapsed" | "kill_switch"


LedgerFor = Callable[[date], Ledger]
"""Builds the window's `Ledger` stated `through` one session. The caller
binds `execution.ledger.from_journal` over the window's fills, orders,
adjustments, reconciliation base, `window_id` and the frozen
`risk.reconcile_quantity_tolerance` once (none of those are risk config
`marks_for` itself reads), so each mark session costs one call."""


def _frozen_int(frozen: Mapping[str, object], key: str) -> int:
    if key not in frozen:
        raise ValueError(f"frozen window values lack {key!r}")
    value = frozen[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"frozen {key} is {value!r}, not an int")
    if value < 0:
        raise ValueError(f"frozen {key} is {value}, must be at least 0")
    return value


def marks_for(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    ledger: LedgerFor,
    sessions: Sequence[date],
    tradable_flags: Mapping[str, bool],
) -> list[Mark]:
    """`Mark` rows for every session in `sessions` (ascending; the caller
    derives the range from the window's last mark, exclusive, through S-1,
    `store.journal.last_marked_session`), read-only.

    For each session: `ledger(session)` gives the window's holding
    split-adjusted through that session's close (Definitions > Ledger); each
    held name is priced by `prices_as_of` at the session's raw close -- not
    split-adjusted, because the quantity is stated on the same pre-split
    basis before a split's ex-date, so neither jumps on the session the split
    lands (spec acceptance: "a 2:1 split with ex_date = S ... marks at
    close(S-1) with the pre-split quantity and raw close, no jump"). `tradable`
    is `tradable_flags.get(security_id)`, the same flags for every session
    back-filled in one call, since a mark step reads `assets` once. A session
    with nothing held gets one `Mark(security_id=None, quantity=0.0, ...)` row
    carrying the ledger's cash. Raises `ValueError` if `prices_as_of` has no
    price for a held name at that close.
    """
    marks: list[Mark] = []
    for session in sessions:
        book = ledger(session)
        if not book.positions:
            marks.append(
                Mark(
                    session=session,
                    security_id=None,
                    quantity=0.0,
                    mark_price=None,
                    value=None,
                    cash=book.cash,
                    tradable=None,
                )
            )
            continue
        held = sorted(book.positions)
        prices = prices_as_of(conn, session_close(session), held)
        # `prices_as_of` returns the latest-known revision of *every* session's
        # bar on or before this close, one row per (security_id, session), not
        # only `session`'s own: a security missing a bar for `session` would
        # otherwise be silently priced off an older session's close (a stale
        # mark). Filtering to `session` here turns that into the same
        # "no price" `ValueError` as a name with no bar at all.
        closes = {
            row["security_id"]: row["close"]
            for row in prices.iter_rows(named=True)
            if row["session"] == session
        }
        for security_id in held:
            if security_id not in closes:
                raise ValueError(f"no price for {security_id} at close({session})")
            quantity = book.positions[security_id]
            price = closes[security_id]
            marks.append(
                Mark(
                    session=session,
                    security_id=security_id,
                    quantity=quantity,
                    mark_price=price,
                    value=quantity * price,
                    cash=book.cash,
                    tradable=tradable_flags.get(security_id),
                )
            )
    return marks


def missed_run(runs: Sequence[PaperRunRow], session: date) -> bool:
    """True when `runs` holds no row for S-1 (req 7's `missed_run` alert
    trigger): `session` is S, so the boundary checked is the previous
    session, whatever `runs` itself is filtered to."""
    boundary = previous_session(session)
    return not any(run.session == boundary for run in runs)


def _run_windows(runs: Iterable[PaperRunRow]) -> dict[int, int]:
    windows: dict[int, int] = {}
    for run in runs:
        if run.run_id is None:
            raise ValueError("a paper run has no run_id")
        windows[run.run_id] = run.window_id
    return windows


def _in_window(run_id: int | None, runs: Mapping[int, int], window_id: int, what: str) -> bool:
    """Whether a row of run `run_id` belongs to `window_id`; a run not among
    `runs` at all raises, since its row cannot be placed (mirrors
    `plan.rebalance_state`'s own check, so an incomplete run list can never
    hide a `missed` or `executed` event)."""
    if run_id is None or run_id not in runs:
        raise ValueError(f"{what} names run {run_id}, which is not among the runs given")
    return runs[run_id] == window_id


def _is_pending(
    rebalance_session: date,
    rebalance_events: Sequence[RebalanceEventRow],
    windows: Mapping[int, int],
    window_id: int,
) -> bool:
    statuses: set[str] = set()
    for event in rebalance_events:
        if event.rebalance_session != rebalance_session:
            continue
        if not _in_window(event.run_id, windows, window_id, f"rebalance event {event}"):
            continue
        if event.status not in _KNOWN_STATUSES:
            raise ValueError(
                f"rebalance {rebalance_session} has an unknown status {event.status!r}"
            )
        statuses.add(event.status)
    if len(statuses) > 1:
        raise ValueError(f"rebalance {rebalance_session} is journaled both {sorted(statuses)}")
    return not statuses


def _catch_up_boundary(rebalance_session: date, max_catch_up_sessions: int) -> date:
    """F_i advanced `max_catch_up_sessions` sessions: the last session a
    `catch_up` run may still trade T_i on (spec req 7: "S <= F_i + the frozen
    `paper.max_catch_up_sessions`")."""
    boundary = fill_session(rebalance_session)
    for _ in range(max_catch_up_sessions):
        boundary = next_session(boundary)
    return boundary


def _period_sessions(first: date, last: date) -> list[date]:
    """Every trading session from `first` through `last`, inclusive,
    ascending (`first <= last`)."""
    sessions = [first]
    session = first
    while session < last:
        session = next_session(session)
        sessions.append(session)
    return sessions


def _event_order(row: KillSwitchRow) -> int:
    if row.event_id is None:
        raise ValueError("a kill_switch row without an event_id: pass rows read from the journal")
    return row.event_id


def _engaged_during(
    kill_switch_rows: Sequence[KillSwitchRow], window_id: int, sessions: Sequence[date]
) -> bool:
    """True when the window's kill-switch state (the latest `engaged` or
    `released` row by write order, `event_id`, with `at <= close(session)`)
    was `engaged` as of the close of any session in `sessions` (ascending).
    A state from before `sessions[0]` carries forward, so an engagement that
    started earlier and was never released still counts."""
    rows = sorted((r for r in kill_switch_rows if r.window_id == window_id), key=_event_order)
    index = 0
    engaged = False
    for session in sessions:
        boundary = session_close(session)
        while index < len(rows) and rows[index].at <= boundary:
            engaged = rows[index].state == ENGAGED
            index += 1
        if engaged:
            return True
    return False


def lapses(
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    rebalance_events: Sequence[RebalanceEventRow],
    kill_switch_rows: Sequence[KillSwitchRow],
    session: date,
    frozen: Mapping[str, object],
) -> list[Missed]:
    """Every rebalance of `window` whose catch-up window has run out as of
    `session` = S, with no `rebalance_events` row of its own yet (Definitions
    > Rebalance state; spec req 7, req 5).

    A rebalance T_i is due once `fill_session(T_i) <= session`. It has
    lapsed once `session` is past T_i's catch-up boundary (F_i advanced by
    the frozen `paper.max_catch_up_sessions` sessions): `session <= boundary`
    is still within the catch-up window and is never reported here ("each
    lapse computed at its boundary session and not one earlier"). The reason
    is `kill_switch` when the window's kill-switch state was engaged as of
    the close of any session from T_i's fill session `F_i` through the
    boundary, inclusive (`#366 Q19(b)`: naming the cause even when the switch
    was released again before `session`), else `catch_up_lapsed`. `runs`
    places `rebalance_events` rows in the window (`run_id` ->
    `paper_runs.window_id`, as `plan.rebalance_state` does); a row of a run
    not among `runs` raises, so an incomplete run list can never hide an
    already-resolved rebalance. A T_i already journaled `executed` or
    `missed` is not reported again.
    """
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    if session < window.first_rebalance_session:
        return []  # a mark-only session before T_0: no rebalance is due yet
    max_catch_up_sessions = _frozen_int(frozen, _PAPER_MAX_CATCH_UP)
    windows = _run_windows(runs)
    missed: list[Missed] = []
    for rebalance_session in rebalance_sessions(window.first_rebalance_session, session):
        fill = fill_session(rebalance_session)
        if fill > session:
            continue
        if not _is_pending(rebalance_session, rebalance_events, windows, window.window_id):
            continue
        boundary = _catch_up_boundary(rebalance_session, max_catch_up_sessions)
        if session <= boundary:
            continue
        period = _period_sessions(fill, boundary)
        engaged = _engaged_during(kill_switch_rows, window.window_id, period)
        reason = _KILL_SWITCH if engaged else _CATCH_UP_LAPSED
        missed.append(Missed(rebalance_session=rebalance_session, reason=reason))
    return missed

"""Marks and lapses, pure (Phase 4 spec req 7 step 5, req 5; plan T63b).

Re-sliced out of `run.py` on 2026-09-30 (#388) so the two computations below
are testable without a broker, a clock or a write connection; the run (T63)
owns the writes and the alerts this task's outputs feed.

- `marks_for` backs a `mark` step's `positions_daily` rows: for every session
  since the window's last mark through S-1, the ledger's split-adjusted
  holding at that session's close, priced by `store.asof.prices_as_of` at the
  same close, with `tradable` carried from the run's own `assets` read (the
  one broker read a mark needs, taken once by the caller and reused for
  every back-filled session). An untradable held name past its last bar is
  marked at its last close (spec req 7, #678); any other missing bar raises.
- `lapses` finds the rebalance sessions a window's catch-up window has run
  out on as of `session`: `catch_up_lapsed` when the frozen
  `paper.max_catch_up_sessions` elapsed with the switch clear throughout,
  `kill_switch` when the switch was engaged at any point overlapping the
  catch-up period -- the fill session F_i through the boundary session,
  inclusive -- not only on the session checked (#366 Q19(b): a switch
  released one session before the lapse, or within the same session, still
  names the cause; spec req 5: "traded after the release if the catch-up
  window still allows, else becomes missed with reason kill_switch"). A
  run that crashed or was halted in that span with no `kill_switch` row
  (a halt-path write failure, or a hard crash) counts too, the same rule
  `execution.switch.derive` uses for a run with no result row.
- `equity_at` is the one read of ledger equity from `positions_daily` rows,
  shared by the run, `paper resume`, the drawdown check, the outcomes and the
  monthly report (#1116): a session's cash read from any of its rows (every
  row carries it; the `security_id IS NULL` row exists only on a flat
  session) plus every held name's value, refused (`UnreadableMarkError`) when a
  row cannot give it rather than skipped.
- `missed_run` is one lookup: whether S-1 has no `paper_runs` row at all, the
  trigger for a `missed_run` alert (req 7).

Nothing here writes a row, delivers an alert or reads a clock.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import duckdb
import polars as pl

from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.config import Cadence
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.switch import ENGAGED, FAULTED_RUN_STATUSES, RELEASED
from tradepartner.store.asof import prices_as_of
from tradepartner.store.journal import (
    KillSwitchRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
)

_logger = logging.getLogger(__name__)

_PAPER_MAX_CATCH_UP = "paper.max_catch_up_sessions"
_EXECUTED = "executed"
_MISSED = "missed"
_CATCH_UP_LAPSED = "catch_up_lapsed"
_KILL_SWITCH = "kill_switch"
_SPLIT = "split"
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


class UnreadableMarkError(ValueError):
    """A session's `positions_daily` rows cannot state its ledger equity:
    no row at all, a row with no cash or a cash that disagrees with another
    row's, a held name with no value, or a non-finite number. A check that
    cannot read its input does not pass (#713 (ii)(a)), so callers refuse or
    fault on it, never skip."""


def equity_at(rows: Sequence[PositionDailyRow], session: date) -> float:
    """Ledger equity at the marked `session`: its cash plus every held name's
    value, from the rows of that exact session (#1116).

    `marks_for`, the only writer of `positions_daily`, carries the ledger's
    cash on every row, and writes the `security_id IS NULL` row only when the
    window holds nothing, so cash is read from every row of the session: each
    must carry the same finite cash. Raises `UnreadableMarkError` when the
    session has no row, any row has no cash or a non-finite one, two rows
    disagree on cash, or a held name's value is missing or non-finite. Each
    number is checked before it is summed (`math.fsum` raises on +inf with
    -inf rather than returning a value), and the total is checked too."""
    today = [r for r in rows if r.session == session]
    if not today:
        raise UnreadableMarkError(f"no mark rows for {session.isoformat()}")
    cash: set[float] = set()
    for row in today:
        if row.cash is None:
            raise UnreadableMarkError(f"a mark row for {session.isoformat()} has no cash")
        cash.add(row.cash)
    if len(cash) != 1:
        raise UnreadableMarkError(
            f"the mark rows for {session.isoformat()} disagree on cash: {sorted(cash)}"
        )
    (cash_value,) = cash
    if not math.isfinite(cash_value):
        raise UnreadableMarkError(f"the cash marked for {session.isoformat()} is {cash_value!r}")
    values: list[float] = []
    for row in today:
        if row.security_id is None:
            continue
        if row.value is None or not math.isfinite(row.value):
            raise UnreadableMarkError(
                f"the mark of {row.security_id} for {session.isoformat()} has value {row.value!r}"
            )
        values.append(row.value)
    equity = cash_value + math.fsum(values)
    if not math.isfinite(equity):
        raise UnreadableMarkError(f"equity marked for {session.isoformat()} is {equity!r}")
    return equity


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


def _last_bars(
    conn: duckdb.DuckDBPyConnection, books: Mapping[date, Ledger], sessions: Sequence[date]
) -> dict[str, date]:
    """Each name held on any of `sessions`: its latest bar's session on or
    before the last of them, as known at that close (never after the run's
    own cut, since `sessions` ends at S-1). One read over every such name, so
    a name sold before the last session (which the run's `assets` read never
    flags) still shows a bar after an ingest gap. It only decides whether a
    missing bar raises; it never prices a mark."""
    held = sorted({name for book in books.values() for name in book.positions})
    if not held:
        return {}
    last: dict[str, date] = {}
    for row in prices_as_of(conn, session_close(sessions[-1]), held).iter_rows(named=True):
        name, bar_session = row["security_id"], row["session"]
        if name not in last or bar_session > last[name]:
            last[name] = bar_session
    return last


def _split_factor(actions: pl.DataFrame, security_id: str, after: date, through: date) -> float:
    """The product of `security_id`'s split ratios in `actions` with
    `after < ex_date <= through`: the splits the ledger's quantity at
    `through` counts but a raw close of `after` predates. A non-finite or
    non-positive ratio raises, as `ledger.from_journal` does."""
    factor = 1.0
    if actions.is_empty():
        return factor
    for row in actions.iter_rows(named=True):
        if (
            row["security_id"] == security_id
            and row["action_type"] == _SPLIT
            and after < row["ex_date"] <= through
        ):
            ratio = float(row["ratio_or_amount"])
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError(f"split of {security_id} has ratio {ratio!r}")
            factor *= ratio
    return factor


def marks_for(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    ledger: LedgerFor,
    sessions: Sequence[date],
    tradable_flags: Mapping[str, bool],
    *,
    actions: pl.DataFrame,
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
    carrying the ledger's cash.

    A held name with no bar for a session is marked at its last close (spec
    req 7, #678) only when its flag is false or absent (untradable, no
    current ticker, or not in the broker's assets read) and it has no bar
    after the session through the last of `sessions` (`_last_bars`, read
    for every name held on any of them), so the gap runs from the session
    after its last bar: that close is the latest bar on or before the session as
    known at close(session), never a bar after it, and each such mark logs a
    `marks` warning naming the stale close's date. A split of the name with
    its ex-date after the stale close's session and on or before the marked
    one (from `actions`, the same frame the caller's `ledger` applies, so the
    ledger's quantity already counts it) divides the carried close by its
    ratio, so the carried value stays on the quantity's own basis (#1116). Every other missing bar
    (a tradable name, a gap before the name's last bar, or a name with no
    bar at all) raises `ValueError`, so a stale mark is never carried
    silently.
    """
    books = {session: ledger(session) for session in sessions}
    own: dict[date, dict[str, float]] = {}
    last_known: dict[date, dict[str, tuple[date, float]]] = {}
    for session in sessions:
        held = sorted(books[session].positions)
        if not held:
            continue
        prices = prices_as_of(conn, session_close(session), held)
        # `prices_as_of` returns the latest-known revision of *every* session's
        # bar on or before this close, one row per (security_id, session), not
        # only `session`'s own: a security missing a bar for `session` would
        # otherwise be silently priced off an older session's close (a stale
        # mark). `own` keeps `session`'s bars only; `last_known` keeps each
        # name's latest bar, which prices a mark only under the rule above.
        own[session] = {}
        last_known[session] = {}
        for row in prices.sort("session").iter_rows(named=True):
            security_id, bar_session, close = row["security_id"], row["session"], row["close"]
            last_known[session][security_id] = (bar_session, close)
            if bar_session == session:
                own[session][security_id] = close
    last_bar = _last_bars(conn, books, sessions)
    marks: list[Mark] = []
    for session in sessions:
        book = books[session]
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
        for security_id in sorted(book.positions):
            flag = tradable_flags.get(security_id)
            if security_id in own[session]:
                price = own[session][security_id]
            else:
                stale = last_known[session].get(security_id)
                later = last_bar.get(security_id)
                if flag is True or stale is None or (later is not None and later > session):
                    raise ValueError(f"no price for {security_id} at close({session})")
                stale_session, stale_close = stale
                price = stale_close / _split_factor(actions, security_id, stale_session, session)
                _logger.warning(
                    "marks: %s has no bar at close(%s); marked at its last close, %s's, "
                    "as untradable (tradable flag %s)",
                    security_id,
                    session.isoformat(),
                    stale_session.isoformat(),
                    flag,
                )
            quantity = book.positions[security_id]
            marks.append(
                Mark(
                    session=session,
                    security_id=security_id,
                    quantity=quantity,
                    mark_price=price,
                    value=quantity * price,
                    cash=book.cash,
                    tradable=flag,
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


def _catch_up_boundary(
    rebalance_session: date, max_catch_up_sessions: int, cadence: Cadence
) -> date:
    """F_i advanced `max_catch_up_sessions` sessions: the last session a
    `catch_up` run may still trade T_i on (spec req 7: "S <= F_i + the frozen
    `paper.max_catch_up_sessions`")."""
    boundary = fill_session(rebalance_session, cadence)
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
    """True when the window's kill-switch state, replayed in write order
    (`event_id`, matching `execution.switch.derive`'s own "latest is write
    order" rule), was `engaged` at any point overlapping the period spanned
    by `sessions` (ascending): from the close of the session before
    `sessions[0]` (exclusive) through the close of `sessions[-1]`
    (inclusive). Sampling only at each session's own close would miss an
    engagement opened and released within one session of the period (the
    run that trades starts before the open, so a same-day release before a
    boundary session's close can still have blocked that session's run);
    checking overlap instead of a per-close snapshot catches it. A state
    already engaged at the period's start carries forward and counts, even
    with no `engaged` row inside the period itself."""
    rows = sorted((r for r in kill_switch_rows if r.window_id == window_id), key=_event_order)
    period_start = session_close(previous_session(sessions[0]))
    period_end = session_close(sessions[-1])
    engaged_before = False
    for row in rows:
        if row.at <= period_start:
            engaged_before = row.state == ENGAGED
        elif row.at <= period_end and row.state == ENGAGED:
            return True
    return engaged_before


def _uncleared_by(
    run: PaperRunRow,
    result: PaperRunResultRow | None,
    releases: Sequence[datetime],
    cutoff: datetime,
) -> bool:
    """`execution.switch.derive`'s own clearing rule (`_faulted_uncleared`),
    bounded to releases at or before `cutoff`: a faulted run stays
    "uncleared as of `cutoff`" unless some release's `at` is after both its
    `started_at` and its `finished_at`, and at or before `cutoff` itself. An
    unfinished run (`result is None`) has no `finished_at` to compare
    against and is never cleared this way -- only `paper resume` closing it
    `crashed` first, then a release, can end it, so until a result row
    exists it carries forward through every later `cutoff`."""
    if result is None:
        return True
    return not any(
        at > run.started_at and at > result.finished_at and at <= cutoff for at in releases
    )


def _faulted_run_in_period(
    kill_switch_rows: Sequence[KillSwitchRow],
    runs: Sequence[PaperRunRow],
    results: Sequence[PaperRunResultRow],
    window_id: int,
    sessions: Sequence[date],
) -> bool:
    """True when the switch was engaged at any point in the period spanned
    by `sessions` (ascending) because of a run of `window_id` that ended
    without a result row (still unfinished -- a crash) or with a result in
    `execution.switch.FAULTED_RUN_STATUSES` (`halted`, `crashed`, `failed`),
    whether or not it ever wrote a `kill_switch` row (a write failure on the
    halt path, or a hard crash before it got that far). `execution.switch.
    derive` engages the switch for exactly such a run until a release clears
    it; `_engaged_during` alone would miss this (no row at all), so it is
    checked in addition, not instead. A run whose own session falls inside
    the period counts outright (the fault happened at some point in the
    period, whatever became of it after); a run from before the period
    counts only while `_uncleared_by` says it still carried forward into the
    period's start, mirroring `derive`'s own rule rather than resetting at
    each session like a per-close snapshot would. `session` is nullable
    (`paper_runs`: a run invoked on a non-session day ends `no_session` with
    no `session` or `kind`), so such a run is placed by `started_at` against
    the same period boundaries instead of raising; its result is routinely
    `no_session`, never in `FAULTED_RUN_STATUSES`, so it drops out at the
    fault check like any other unfaulted run, and only a genuinely faulted
    or unfinished null-session row (`derive` counts those too, whatever
    their session) reaches the `started_at` placement."""
    finished = {r.run_id: r for r in results}
    period = frozenset(sessions)
    period_start = session_close(previous_session(sessions[0]))
    period_end = session_close(sessions[-1])
    releases = [r.at for r in kill_switch_rows if r.window_id == window_id and r.state == RELEASED]
    for run in runs:
        if run.window_id != window_id:
            continue
        if run.run_id is None:
            raise ValueError("a paper run has no run_id")
        result = finished.get(run.run_id)
        faulted = result is None or result.status in FAULTED_RUN_STATUSES
        if not faulted:
            continue
        if run.session is not None:
            if run.session > sessions[-1]:
                continue
            if run.session in period:
                return True
        else:
            if run.started_at > period_end:
                continue
            if run.started_at > period_start:
                return True
        if _uncleared_by(run, result, releases, period_start):
            return True
    return False


def lapses(
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    rebalance_events: Sequence[RebalanceEventRow],
    kill_switch_rows: Sequence[KillSwitchRow],
    results: Sequence[PaperRunResultRow],
    session: date,
    frozen: Mapping[str, object],
    *,
    cadence: Cadence,
) -> list[Missed]:
    """Every rebalance of `window` whose catch-up window has run out as of
    `session` = S, with no `rebalance_events` row of its own yet (Definitions
    > Rebalance state; spec req 7, req 5). `cadence` is the window's hypothesis's
    frozen `schedule.rebalance_cadence` (ADR 0015 seam 4).

    A rebalance T_i is due once `fill_session(T_i) <= session`. It has
    lapsed once `session` is past T_i's catch-up boundary (F_i advanced by
    the frozen `paper.max_catch_up_sessions` sessions): `session <= boundary`
    is still within the catch-up window and is never reported here ("each
    lapse computed at its boundary session and not one earlier"). The
    boundary is never later than the session before the next rebalance's
    fill session F_{i+1}, because from F_{i+1} on `planning.due_rebalance`
    plans T_{i+1}, never T_i (ADR 0017 section 2): at `daily` a T_i left
    pending lapses on F_{i+1}, in the run that plans T_{i+1}. At `month_end`
    F_{i+1} is about twenty sessions after F_i, beyond any catch-up window
    the frozen key allows in practice, so the boundary there is F_i plus the
    key as before. The reason
    is `kill_switch` when the switch was engaged at any point overlapping
    T_i's fill session `F_i` through the boundary, inclusive (`#366 Q19(b)`:
    naming the cause even when the switch was released again before
    `session`) -- either a `kill_switch` row says so (`_engaged_during`) or a
    run of the window started in that span ended unfinished or faulted
    (`_faulted_run_in_period`: `execution.switch.derive`'s own rule for a
    crash or a halt-path write failure that left no row) -- else
    `catch_up_lapsed`. `runs` places `rebalance_events` rows in the window
    (`run_id` -> `paper_runs.window_id`, as `plan.rebalance_state` does); a
    row of a run not among `runs` raises, so an incomplete run list can
    never hide an already-resolved rebalance. A T_i already journaled
    `executed` or `missed` is not reported again.
    """
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    if session < window.first_rebalance_session:
        return []  # a mark-only session before T_0: no rebalance is due yet
    max_catch_up_sessions = _frozen_int(frozen, _PAPER_MAX_CATCH_UP)
    windows = _run_windows(runs)
    missed: list[Missed] = []
    due = rebalance_sessions(window.first_rebalance_session, session, cadence)
    for index, rebalance_session in enumerate(due):
        fill = fill_session(rebalance_session, cadence)
        if fill > session:
            continue
        if not _is_pending(rebalance_session, rebalance_events, windows, window.window_id):
            continue
        boundary = _catch_up_boundary(rebalance_session, max_catch_up_sessions, cadence)
        if index + 1 < len(due):
            next_fill = fill_session(due[index + 1], cadence)
            if next_fill <= session:
                # T_{i+1} is due on and after F_{i+1}: T_i is never planned again.
                boundary = min(boundary, previous_session(next_fill))
        if session <= boundary:
            continue
        period = _period_sessions(fill, boundary)
        engaged = _engaged_during(
            kill_switch_rows, window.window_id, period
        ) or _faulted_run_in_period(kill_switch_rows, runs, results, window.window_id, period)
        reason = _KILL_SWITCH if engaged else _CATCH_UP_LAPSED
        missed.append(Missed(rebalance_session=rebalance_session, reason=reason))
    return missed

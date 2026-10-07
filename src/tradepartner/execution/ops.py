"""Operations data: `page_data(conn, settings) -> OpsData` (Phase 4 spec req 12;
plan T66b; split out of T66 on 2026-09-30, #388, so the operations page and
`paper status` (T67) can start before the window and report chains finish).

Pure over a read-only connection: every number the operations page and
`paper status` show comes from here, never recomputed by the page itself
(ADR 0011). `page_data` never writes to the store, never takes the run lock
(`execution.lock.run_lock`) and never engages or releases the kill switch: it
only asks `execution.lock.is_held` (a non-blocking check) and
`execution.switch.derive` (pure over rows already read), exactly as those
modules document for readers that must never block a run.

**Journal not initialised.** A version-4 store no write has migrated yet
raises `store.journal.JournalNotInitialised` from the first read; this module
catches it and returns an `OpsData` with `journal_not_initialised=True` and
every other field at its empty default (the T43 "registry not initialised"
pattern: a page shows a state, not a traceback).

**No window yet.** Before the first `paper start`, the journal exists but
`journal.latest_window` returns `None`; `OpsData` then carries
`journal_not_initialised=False`, `window=None` and every other field empty.

**Staleness.** "As of" is the latest `known_at` among every row this function
reads for the window; "last updated" is the latest `paper_run_results.
finished_at`. `stale` is True when "S-1", the session this run-by-run check
asks for, has no run with that `session` in this window: when today (by the
New York calendar) is itself a session, S-1 is the session strictly before
it (today's own run may not have happened yet); on a non-session day
(a weekend, a holiday) there is no "today's run" to wait for, so S-1 is the
latest session at or before today instead — both cases are exactly
`calendar.previous_session(today)` (`_required_run_session`).

**Row limits.** The alerts list, the chain view and the fills table are each
capped at `dashboard.page_row_limit` rows, newest first (ADR 0011's
"Consequences": a render's read connection blocks the run's write
connections for as long as `page_data` takes). `OpsData` reports each cap
that bit (`alerts_capped`, `chains_capped`, `fills_capped`). Every one of
those reads is bounded in SQL, not only truncated in Python (#435): the alerts
query and `journal.orders_for` read at most `limit + 1` rows, newest first
(the extra row only tells the cap bit); the chain view's events, fills and
outcomes are read only for the at most `limit` orders it can keep
(`client_order_ids`), so they scale with the cap, not the window; and the fills
table reads the `limit + 1` newest fills by `fill_id` (`journal.fills_for`'s
`limit`). "As of" stays the latest `known_at` of the window's order chains
even past the cap: one aggregate over the window's `order_events` and
`outcomes` (a single row), the newest orders being in the capped read, and
the fills table's newest rows by `fill_id` standing in for the newest by
`known_at` (both come from the write that journals a fill: `journal.append`
hands out the next id and the writer stamps the clock; only a clock running
backwards between two collections could make them disagree).

Every other whole-window read page_data used to make is bounded too (#613):
marks are read only for the window's latest marked session
(`_last_marked_session`, one aggregate, then `journal.positions_daily_for`
with `after=last_session - timedelta(days=1)`, never `previous_session(...)`:
`after` is exclusive (`session > after`) and `last_session` is the maximum
session, so this reads exactly `session == last_session` whatever the
trading calendar does between the two, including a mark on a date the
calendar never scheduled as a session, #652); the kill-switch rows are at most two
(`_kill_switch_rows_for`): the window's last row by `event_id` and the
`released` row with the greatest `at`, the only two `switch.derive` can ever
draw a cause or a clearing timestamp from; the reconciliation shown is the
one row `_latest_reconciliation` picks in SQL, the same row Python's
`max(..., key=lambda r: r.at)` would; the non-terminal order count is one
`COUNT(*)` (`_open_orders_count`) over `journal._orders_where`'s predicate,
never `len(journal.non_terminal_orders(...))`; and `stale` and
`last_updated` are each one aggregate (`_stale_and_last_updated`). The run
set `_runs_for_switch` reads for `switch.derive` is **not** capped at a
fixed row count, because every run it could still show is a live cause of
the engaged switch for the *current* incident: every run with no result row
(unfinished -- closed by `paper resume` before a release), every faulted run
(`switch.FAULTED_RUN_STATUSES`) a release has not yet cleared (cleared by
`paper resume`'s release once the incident ends), and the window's latest
run (for the in-progress rule). A quiet window reads at most a handful of
rows there; a long-running window with no open incident reads none. "As of"
folds in one further aggregate, `_bounded_known_at`, over the window's whole
`paper_runs`, `paper_run_results`, `positions_daily`, `kill_switch` and
`reconciliations` rows, so it still reflects a row of any of those left out
of the bounded reads above.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import previous_session
from tradepartner.config import Settings
from tradepartner.execution import lock
from tradepartner.execution.switch import FAULTED_RUN_STATUSES, SwitchState, derive
from tradepartner.store import journal
from tradepartner.store.db import utc_now
from tradepartner.store.journal import (
    TERMINAL_ORDER_STATUSES,
    AlertRow,
    DecisionRow,
    JournalNotInitialised,
    KillSwitchRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PaperPlanRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    RunWithResult,
    SignalRow,
)

__all__ = ["ChainStep", "OpsData", "OrderChain", "RankedSignal", "page_data"]

_NEW_YORK = ZoneInfo("America/New_York")

#: Deterministic tie-break for steps sharing a `known_at` in one order's chain.
_STEP_ORDER: dict[str, int] = {"order": 0, "order_event": 1, "fill": 2, "outcome": 3}


def _required_run_session(now: datetime) -> date:
    """S-1: the session `stale` requires a run for. When today (the New York
    calendar date of `now`) is itself a session, that is the session strictly
    before it, because today's own run may not have happened yet. On a
    non-session day (a weekend, a holiday) there is no "today's run" to wait
    for, so it is the latest session at or before today instead. Both cases
    are `calendar.previous_session(today)`: on a session day that is the
    session before it; on a non-session day it is already the latest one at
    or before it (`previous_session` does not require its argument to be a
    session). Takes `now` so the rule can be tested without the wall clock."""
    today = now.astimezone(_NEW_YORK).date()
    return previous_session(today)


@dataclass(frozen=True)
class RankedSignal:
    """One security's place in the latest plan's ranking (spec req 12 hero):
    its `signals` reason (`selected`, `below_cut`, or an `excluded_<reason>` the
    plan's family declares -- `excluded_no_history` for momentum, T127b #1209),
    None for a held name the plan's universe pass never scored, and, when a
    `decisions` row names it, both the decision's own kind (`decision`, e.g.
    `trade`, `override`, `dust`, `skip_delisted`) and its `reason` (e.g.
    `left_universe`, `exclude_name`), kept separate so an override's kind and
    a dust exit's reason are never collapsed into one one string."""

    security_id: str
    score: float | None
    rank: int | None
    signal_reason: str | None
    decision: str | None
    decision_reason: str | None
    selected: bool


@dataclass(frozen=True)
class ChainStep:
    """One event in an order's journal chain, newest-last."""

    at: datetime
    kind: str
    detail: str


@dataclass(frozen=True)
class OrderChain:
    """One order's full journal chain (spec req 12 "journal-chain detail view")."""

    client_order_id: str
    steps: tuple[ChainStep, ...]


@dataclass(frozen=True)
class OpsData:
    """Everything the operations page and `paper status` (T67) show, computed
    once over a read-only connection."""

    journal_not_initialised: bool = False
    window: PaperWindowRow | None = None
    as_of: datetime | None = None
    last_updated: datetime | None = None
    stale: bool = False
    positions_count: int = 0
    positions_value: float = 0.0
    open_orders_count: int = 0
    targets_count: int = 0
    switch_state: SwitchState | None = None
    ranking: tuple[RankedSignal, ...] = field(default_factory=tuple)
    fills: tuple[OrderedFill, ...] = field(default_factory=tuple)
    fills_capped: bool = False
    chains: tuple[OrderChain, ...] = field(default_factory=tuple)
    chains_capped: bool = False
    alerts: tuple[AlertRow, ...] = field(default_factory=tuple)
    alerts_capped: bool = False
    reconciliation: ReconciliationRow | None = None


def _latest(plans: list[PaperPlanRow]) -> PaperPlanRow | None:
    if not plans:
        return None
    return max(plans, key=lambda p: (p.rebalance_session, p.run_id))


def _alerts_for_window(
    conn: duckdb.DuckDBPyConnection, window: PaperWindowRow, *, limit: int
) -> tuple[tuple[AlertRow, ...], bool]:
    """The window's alerts, newest first, capped at `limit` in SQL (not just in
    Python, so a journal with many alerts never pays for more than `limit + 1`
    rows). Most alert kinds are run-scoped (one per (kind, run)), so a row
    naming a run of this window belongs to it. `locked` has no run id
    (`execution.alerts`: emitted at run entry, before any run row exists, when
    a scheduled `paper run` found the run lock already held) and dedupes on
    (kind, session) instead; it is this window's when its session is on or
    after the window's first rebalance, since there is no run to join it to.
    `no_window` alone has no window to belong to: it fires before the first
    `paper start`, and this function is never called before one exists."""
    journal.require_journal(conn)
    rows = conn.execute(
        'SELECT alert_id, run_id, session, kind, message, "at", known_at, ingested_at '
        "FROM alerts WHERE run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?) "
        "OR (run_id IS NULL AND kind = 'locked' AND session >= ?) "
        "ORDER BY alert_id DESC LIMIT ?",
        [window.window_id, window.first_rebalance_session, limit + 1],
    ).fetchall()
    capped = len(rows) > limit
    kept = rows[:limit]
    alerts = tuple(
        AlertRow(
            alert_id=r[0],
            run_id=r[1],
            session=r[2],
            kind=r[3],
            message=r[4],
            at=r[5],
            known_at=r[6],
            ingested_at=r[7],
        )
        for r in kept
    )
    return alerts, capped


def _latest_chain_known_at(conn: duckdb.DuckDBPyConnection, window_id: int) -> datetime | None:
    """The latest `known_at` among the window's `order_events` and `outcomes`
    rows (through their order's run), one aggregate row, so "as of" covers the
    orders past the chain view's cap without reading their rows."""
    journal.require_journal(conn)
    row = conn.execute(
        "SELECT max(t.known_at) FROM (SELECT client_order_id, known_at FROM order_events "
        "UNION ALL SELECT client_order_id, known_at FROM outcomes) t "
        "WHERE t.client_order_id IN (SELECT o.client_order_id FROM orders o "
        "JOIN paper_runs r ON r.run_id = o.run_id WHERE r.window_id = ?)",
        [window_id],
    ).fetchone()
    assert row is not None
    latest: datetime | None = row[0]
    return latest


def _build_ranking(
    conn: duckdb.DuckDBPyConnection, window_id: int, plan: PaperPlanRow
) -> tuple[RankedSignal, ...]:
    """The latest plan's ranking: every `signals` row, in rank order, each
    joined to its `decisions` row when one names the same security. A held
    name the universe pass excludes before scoring (`skip_delisted`, or a
    `left_universe`/`left_targets` exit) has a `decisions` row but no
    `signals` row; it is appended after the ranked ones with no score or rank,
    so a full exit is never silently dropped from the hero (spec req 12 names
    `left_universe` and `override` as reasons the ranking must show)."""
    signals = journal.signals_for(conn, plan.run_id)
    decisions = journal.decisions_for(conn, window_id, rebalance_session=plan.rebalance_session)
    by_security: dict[str, DecisionRow] = {d.decision.security_id: d.decision for d in decisions}

    def _rank_key(s: SignalRow) -> tuple[bool, int]:
        return (s.rank is None, s.rank if s.rank is not None else 0)

    ranked: list[RankedSignal] = []
    for s in sorted(signals, key=_rank_key):
        d = by_security.get(s.security_id)
        ranked.append(
            RankedSignal(
                security_id=s.security_id,
                score=s.score,
                rank=s.rank,
                signal_reason=s.reason,
                decision=d.decision if d is not None else None,
                decision_reason=d.reason if d is not None else None,
                selected=s.reason == "selected",
            )
        )
    scored = {s.security_id for s in signals}
    for security_id in sorted(set(by_security) - scored):
        d = by_security[security_id]
        ranked.append(
            RankedSignal(
                security_id=security_id,
                score=None,
                rank=None,
                signal_reason=None,
                decision=d.decision,
                decision_reason=d.reason,
                selected=False,
            )
        )
    return tuple(ranked)


def _order_step(order: OrderRow) -> ChainStep:
    size = order.quantity if order.quantity is not None else order.notional
    return ChainStep(
        at=order.known_at,
        kind="order",
        detail=(
            f"order {order.side} {size} {order.symbol} session={order.session.isoformat()} "
            f"phase={order.phase} attempt={order.attempt}"
        ),
    )


def _build_chains(
    all_orders: Sequence[OrderRow],
    all_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    all_outcomes: Sequence[OutcomeRow],
    *,
    limit: int,
) -> tuple[tuple[OrderChain, ...], bool]:
    """Built from rows `page_data` already read for `_as_of` (never its own
    `store.journal` reads: with the window's orders, events and outcomes read
    once each, not once here and once there)."""
    # Newest order first, so a long window's cap drops its oldest orders, not
    # the current session's (the ones the operator is looking at).
    orders = sorted(all_orders, key=lambda o: (o.known_at, o.client_order_id), reverse=True)
    events_by_order: dict[str, list[OrderEventRow]] = {}
    for event in all_events:
        events_by_order.setdefault(event.client_order_id, []).append(event)
    fills_by_order: dict[str, list[OrderedFill]] = {}
    for ordered_fill in fills:
        fills_by_order.setdefault(ordered_fill.fill.client_order_id, []).append(ordered_fill)
    outcomes_by_order: dict[str, list[OutcomeRow]] = {}
    for outcome in all_outcomes:
        outcomes_by_order.setdefault(outcome.client_order_id, []).append(outcome)

    chains: list[OrderChain] = []
    total_steps = 0
    capped = False
    for order in orders:
        steps: list[ChainStep] = [_order_step(order)]
        for event in events_by_order.get(order.client_order_id, ()):
            steps.append(
                ChainStep(
                    at=event.known_at,
                    kind="order_event",
                    detail=f"{event.status}" + (f" ({event.reason})" if event.reason else ""),
                )
            )
        for ordered_fill in fills_by_order.get(order.client_order_id, ()):
            f = ordered_fill.fill
            steps.append(
                ChainStep(
                    at=f.known_at,
                    kind="fill",
                    detail=f"fill {f.quantity} @ {f.price} ({f.broker_fill_id})",
                )
            )
        for outcome in outcomes_by_order.get(order.client_order_id, ()):
            steps.append(
                ChainStep(
                    at=outcome.known_at,
                    kind="outcome",
                    detail=f"{outcome.kind}={outcome.value}",
                )
            )
        steps.sort(key=lambda s: (s.at, _STEP_ORDER.get(s.kind, 99)))

        if total_steps >= limit:
            capped = True
            break
        remaining = limit - total_steps
        if len(steps) > remaining:
            steps = steps[:remaining]
            capped = True
        total_steps += len(steps)
        chains.append(OrderChain(client_order_id=order.client_order_id, steps=tuple(steps)))

    return tuple(chains), capped


def _as_of(
    window: PaperWindowRow,
    *,
    bounded_known_at: datetime | None,
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    outcomes: Sequence[OutcomeRow],
    alerts: Sequence[AlertRow],
    chains_known_at: datetime | None,
) -> datetime | None:
    """The latest `known_at` among every row the page shows: everything
    `page_data` reads for this window, so "as of" can never predate what the
    page itself displays (e.g. a kill-switch row the KPI row already shows),
    `chains_known_at`, so it never predates an event or outcome of an order
    past the chain view's cap either, and `bounded_known_at`
    (`_bounded_known_at`'s single aggregate over the window's whole
    `paper_runs`, `paper_run_results`, `positions_daily`, `kill_switch` and
    `reconciliations` rows), so it never predates one of those left out of
    the bounded reads below (module docstring)."""
    candidates = (
        [window.known_at]
        + ([chains_known_at] if chains_known_at is not None else [])
        + ([bounded_known_at] if bounded_known_at is not None else [])
        + [o.known_at for o in orders]
        + [e.known_at for e in order_events]
        + [f.fill.known_at for f in fills]
        + [o.known_at for o in outcomes]
        + [a.known_at for a in alerts]
    )
    return max(candidates, default=None)


def _last_marked_session(conn: duckdb.DuckDBPyConnection, window_id: int) -> date | None:
    """The latest session the window has a `positions_daily` row for, one
    aggregate row, so the page never reads an older session's marks to find
    it (module docstring, item 1)."""
    journal.require_journal(conn)
    row = conn.execute(
        "SELECT max(session) FROM positions_daily WHERE run_id IN "
        "(SELECT run_id FROM paper_runs WHERE window_id = ?)",
        [window_id],
    ).fetchone()
    assert row is not None
    session: date | None = row[0]
    return session


_KILL_SWITCH_FIELDS: tuple[str, ...] = (
    "event_id",
    "window_id",
    "at",
    "state",
    "source",
    "fault_type",
    "reason",
    "run_id",
    "override_id",
    "resume_id",
    "reconciliation_id",
    "peak_equity",
    "known_at",
    "ingested_at",
)


def _kill_switch_rows_for(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> tuple[KillSwitchRow, ...]:
    """At most two of the window's `kill_switch` rows: the last one by
    `event_id`, whatever its state (`switch.derive`'s "latest row"
    `rows[-1]` rule), and the `released` row with the greatest `at`. `derive`
    only ever reads a release's `at` through `any(at > started_at and at >
    finished_at for at in releases)`, a comparison that is monotonic in
    `at`: if the greatest release clears a run, every smaller one would too,
    and if it does not, no smaller one could, so only the greatest matters
    (module docstring, item 2). The two rows can coincide (the last by
    `event_id` is itself the greatest release), in which case one is kept."""
    journal.require_journal(conn)
    columns = ", ".join(f'"{name}"' if name == "at" else name for name in _KILL_SWITCH_FIELDS)
    last = conn.execute(
        f"SELECT {columns} FROM kill_switch WHERE window_id = ? ORDER BY event_id DESC LIMIT 1",
        [window_id],
    ).fetchall()
    released = conn.execute(
        f"SELECT {columns} FROM kill_switch WHERE window_id = ? AND state = 'released' "
        'ORDER BY "at" DESC LIMIT 1',
        [window_id],
    ).fetchall()
    by_event_id: dict[int, KillSwitchRow] = {}
    for values in (*last, *released):
        row = KillSwitchRow(**dict(zip(_KILL_SWITCH_FIELDS, values, strict=True)))
        assert row.event_id is not None
        by_event_id[row.event_id] = row
    return tuple(by_event_id.values())


_RUN_FIELDS: tuple[str, ...] = (
    "run_id",
    "window_id",
    "session",
    "kind",
    "started_at",
    "invoked_by",
    "code_version",
    "code_dirty",
    "known_at",
    "ingested_at",
)
_RESULT_FIELDS: tuple[str, ...] = (
    "run_id",
    "finished_at",
    "status",
    "fault_type",
    "message",
    "clock_fault",
    "known_at",
    "ingested_at",
)


def _runs_for_switch(
    conn: duckdb.DuckDBPyConnection, window_id: int, *, released_at: datetime | None
) -> list[RunWithResult]:
    """The runs `switch.derive` can possibly draw a cause from (module
    docstring, item 3): every run with no result row (unfinished); every
    faulted run (`switch.FAULTED_RUN_STATUSES`) that `released_at` -- the
    greatest `released` row's `at`, from `_kill_switch_rows_for` -- has not
    cleared (`released_at` is None, or at or before the run's `started_at`,
    or at or before its `finished_at`: the negation of `switch.
    _faulted_uncleared`'s clearing rule); and the window's latest run by
    `run_id`, for `derive`'s in-progress rule (`unfinished[-1] is
    own_runs[-1]`), whichever of the first two groups it falls into or not.
    Every run left out is cleared or ordinary and contributes no cause, so
    `derive`'s output over this set is `derive`'s output over every run of
    the window. Not capped at a fixed count: each uncleared faulted or
    unfinished run is a live cause the page must show for the current
    incident (module docstring)."""
    journal.require_journal(conn)
    run_columns = ", ".join(f"r.{name}" for name in _RUN_FIELDS)
    result_columns = ", ".join(f"s.{name}" for name in _RESULT_FIELDS)
    rows = conn.execute(
        f"SELECT {run_columns}, {result_columns} FROM paper_runs r "
        "LEFT JOIN paper_run_results s ON s.run_id = r.run_id "
        "WHERE r.window_id = ? AND ("
        "s.run_id IS NULL "
        "OR (list_contains(?, s.status) "
        "AND (? IS NULL OR ? <= r.started_at OR ? <= s.finished_at)) "
        "OR r.run_id = (SELECT max(run_id) FROM paper_runs WHERE window_id = ?)"
        ") ORDER BY r.run_id",
        [
            window_id,
            list(FAULTED_RUN_STATUSES),
            released_at,
            released_at,
            released_at,
            window_id,
        ],
    ).fetchall()
    width = len(_RUN_FIELDS)
    out: list[RunWithResult] = []
    for row in rows:
        run = PaperRunRow(**dict(zip(_RUN_FIELDS, row[:width], strict=True)))
        result_values = row[width:]
        result = (
            None
            if result_values[0] is None
            else PaperRunResultRow(**dict(zip(_RESULT_FIELDS, result_values, strict=True)))
        )
        out.append(RunWithResult(run, result))
    return out


def _stale_and_last_updated(
    conn: duckdb.DuckDBPyConnection, window_id: int, s_minus_1: date
) -> tuple[bool, datetime | None]:
    """`stale` (no run of the window has `session == s_minus_1`) and
    `last_updated` (the max `paper_run_results.finished_at` over the
    window's runs), each one aggregate row, so neither reads the whole run
    set `_runs_for_switch` already bounds away (module docstring, item 3)."""
    journal.require_journal(conn)
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM paper_runs WHERE window_id = ? AND session = ?), "
        "(SELECT max(s.finished_at) FROM paper_run_results s "
        "JOIN paper_runs r ON r.run_id = s.run_id WHERE r.window_id = ?)",
        [window_id, s_minus_1, window_id],
    ).fetchone()
    assert row is not None
    has_run, last_updated = row
    return not bool(has_run), last_updated


_RECONCILIATION_FIELDS: tuple[str, ...] = (
    "reconciliation_id",
    "window_id",
    "run_id",
    "at",
    "status",
    "broker_cash",
    "mismatches_json",
    "known_at",
    "ingested_at",
)


def _latest_reconciliation(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> ReconciliationRow | None:
    """The one row `max(journal.reconciliations_for(conn, window_id), key=
    lambda r: r.at)` would pick. `reconciliations_for`'s order is `(known_at,
    ingested_at, rowid)` ascending, and Python's `max` keeps the *first*
    maximal element it scans, so among rows sharing the greatest `at` it is
    the one with the smallest `(known_at, ingested_at, rowid)` -- exactly
    `ORDER BY "at" DESC, known_at, ingested_at, rowid LIMIT 1` (module
    docstring, item 4)."""
    journal.require_journal(conn)
    columns = ", ".join(f'"{n}"' if n == "at" else n for n in _RECONCILIATION_FIELDS)
    row = conn.execute(
        f"SELECT {columns} FROM reconciliations WHERE window_id = ? "
        'ORDER BY "at" DESC, known_at, ingested_at, rowid LIMIT 1',
        [window_id],
    ).fetchone()
    if row is None:
        return None
    return ReconciliationRow(**dict(zip(_RECONCILIATION_FIELDS, row, strict=True)))


def _open_orders_count(conn: duckdb.DuckDBPyConnection, window_id: int) -> int:
    """`len(journal.non_terminal_orders(conn, window_id=window_id))`, as one
    `COUNT(*)`: the window's orders (through their run,
    `journal._ORDER_IN_WINDOW`'s join) with no terminal `order_events` row
    (`journal._orders_where`'s predicate, `journal.TERMINAL_ORDER_STATUSES`;
    module docstring, item 5). `journal.orders_for` is still called
    elsewhere in `page_data` for the chain view, so the orphan-order fail
    closed check (`journal._require_orders_have_runs`) still runs."""
    journal.require_journal(conn)
    row = conn.execute(
        "SELECT COUNT(*) FROM orders o JOIN paper_runs r ON r.run_id = o.run_id "
        "WHERE r.window_id = ? AND o.client_order_id NOT IN "
        "(SELECT client_order_id FROM order_events WHERE list_contains(?, status))",
        [window_id, list(TERMINAL_ORDER_STATUSES)],
    ).fetchone()
    assert row is not None
    return int(row[0])


def _bounded_known_at(conn: duckdb.DuckDBPyConnection, window_id: int) -> datetime | None:
    """The latest `known_at` among the window's *whole* `paper_runs`,
    `paper_run_results`, `positions_daily`, `kill_switch` and
    `reconciliations` rows, one aggregate, so "as of" still covers every row
    of those tables even though `_runs_for_switch`, `_last_marked_session`,
    `_kill_switch_rows_for` and `_latest_reconciliation` each read only a
    bounded slice of them (module docstring, item 6)."""
    journal.require_journal(conn)
    row = conn.execute(
        "SELECT max(known_at) FROM ("
        "SELECT known_at FROM paper_runs WHERE window_id = ? "
        "UNION ALL "
        "SELECT s.known_at FROM paper_run_results s JOIN paper_runs r ON r.run_id = s.run_id "
        "WHERE r.window_id = ? "
        "UNION ALL "
        "SELECT known_at FROM positions_daily WHERE run_id IN "
        "(SELECT run_id FROM paper_runs WHERE window_id = ?) "
        "UNION ALL "
        "SELECT known_at FROM kill_switch WHERE window_id = ? "
        "UNION ALL "
        "SELECT known_at FROM reconciliations WHERE window_id = ?"
        ") t",
        [window_id, window_id, window_id, window_id, window_id],
    ).fetchone()
    assert row is not None
    latest: datetime | None = row[0]
    return latest


def page_data(conn: duckdb.DuckDBPyConnection, settings: Settings) -> OpsData:
    """Everything the operations page and `paper status` (T67) show, read once
    through `conn` (the shell's read-only connection). Never writes to the
    store, never takes the run lock and never engages or releases the switch
    (module docstring)."""
    limit = settings.dashboard.page_row_limit
    try:
        window = journal.latest_window(conn)
    except JournalNotInitialised:
        return OpsData(journal_not_initialised=True)

    if window is None:
        return OpsData()

    window_id = window.window_id
    assert window_id is not None

    # Newest first, one past the cap: `_build_chains` can keep at most `limit`
    # orders (each chain has at least its order step), so only those orders'
    # events, fills and outcomes are read.
    orders = journal.orders_for(conn, window_id=window_id, limit=limit + 1)
    kept_ids = [o.client_order_id for o in orders[:limit]]
    order_events = journal.order_events_for(conn, window_id=window_id, client_order_ids=kept_ids)
    outcomes = journal.outcomes_for(conn, window_id, client_order_ids=kept_ids)
    chain_fills = journal.fills_for(conn, window_id=window_id, client_order_ids=kept_ids)
    newest_fills = journal.fills_for(conn, window_id=window_id, limit=limit + 1)
    fills, fills_capped = _capped_fills(newest_fills, limit=limit)
    chains_known_at = _latest_chain_known_at(conn, window_id)

    kill_switch_rows = _kill_switch_rows_for(conn, window_id)
    released_at = max((r.at for r in kill_switch_rows if r.state == "released"), default=None)
    runs_with_results = _runs_for_switch(conn, window_id, released_at=released_at)
    runs = [rw.run for rw in runs_with_results]
    results = [rw.result for rw in runs_with_results if rw.result is not None]

    s_minus_1 = _required_run_session(utc_now())
    stale, last_updated = _stale_and_last_updated(conn, window_id, s_minus_1)

    last_session = _last_marked_session(conn, window_id)
    positions_count = 0
    positions_value = 0.0
    if last_session is not None:
        # `after` is exclusive and `last_session` is the maximum session
        # (`_last_marked_session`), so `after=last_session - timedelta(days=1)`
        # reads exactly `session == last_session`, with no dependency on
        # which dates the trading calendar schedules as sessions (#652).
        marks = [
            m
            for m in journal.positions_daily_for(
                conn, window_id, after=last_session - timedelta(days=1)
            )
            if m.security_id is not None
        ]
        positions_count = sum(1 for m in marks if m.quantity != 0)
        positions_value = sum(m.value or 0.0 for m in marks if m.quantity != 0)

    open_orders_count = _open_orders_count(conn, window_id)

    plan = _latest(journal.plans_for(conn, window_id))
    targets_count = plan.n_targets if plan is not None else 0
    ranking = _build_ranking(conn, window_id, plan) if plan is not None else ()

    switch_state = derive(
        window,
        kill_switch_rows,
        runs,
        results,
        reading_run=None,
        lock_free=not lock.is_held(settings),
    )

    chains, chains_capped = _build_chains(orders, order_events, chain_fills, outcomes, limit=limit)
    alerts, alerts_capped = _alerts_for_window(conn, window, limit=limit)
    reconciliation = _latest_reconciliation(conn, window_id)
    bounded_known_at = _bounded_known_at(conn, window_id)

    as_of = _as_of(
        window,
        bounded_known_at=bounded_known_at,
        orders=orders,
        order_events=order_events,
        fills=[*newest_fills, *chain_fills],
        outcomes=outcomes,
        chains_known_at=chains_known_at,
        alerts=alerts,
    )

    return OpsData(
        journal_not_initialised=False,
        window=window,
        as_of=as_of,
        last_updated=last_updated,
        stale=stale,
        positions_count=positions_count,
        positions_value=positions_value,
        open_orders_count=open_orders_count,
        targets_count=targets_count,
        switch_state=switch_state,
        ranking=ranking,
        fills=fills,
        fills_capped=fills_capped,
        chains=chains,
        chains_capped=chains_capped,
        alerts=alerts,
        alerts_capped=alerts_capped,
        reconciliation=reconciliation,
    )


def _capped_fills(
    fills: Sequence[OrderedFill], *, limit: int
) -> tuple[tuple[OrderedFill, ...], bool]:
    ordered = sorted(fills, key=lambda f: f.fill.fill_id or 0, reverse=True)
    capped = len(ordered) > limit
    return tuple(ordered[:limit]), capped

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
that bit (`alerts_capped`, `chains_capped`, `fills_capped`). The alerts query
pushes its limit into SQL. `store.journal.fills_for` is documented as the
*single* reader of `fills`, and the window readers it and the chain view share
(`orders_for`, `order_events_for`, `outcomes_for`) take no row limit of their
own, so those three still read the whole window before this module truncates
in Python; a true per-row SQL bound on them needs a `store/journal.py` change
this task's file list does not include (left for a follow-up: #435).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import previous_session
from tradepartner.config import Settings
from tradepartner.execution import lock
from tradepartner.execution.switch import SwitchState, derive
from tradepartner.store import journal
from tradepartner.store.db import utc_now
from tradepartner.store.journal import (
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
    PositionDailyRow,
    ReconciliationRow,
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
    its `signals` reason (`selected`, `below_cut`, `excluded_no_history`), None
    for a held name the plan's universe pass never scored, and, when a
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
    runs: Sequence[PaperRunRow],
    results: Sequence[PaperRunResultRow],
    orders: Sequence[OrderRow],
    order_events: Sequence[OrderEventRow],
    fills: Sequence[OrderedFill],
    outcomes: Sequence[OutcomeRow],
    marks: Sequence[PositionDailyRow],
    kill_switch_rows: Sequence[KillSwitchRow],
    reconciliations: Sequence[ReconciliationRow],
    alerts: Sequence[AlertRow],
) -> datetime | None:
    """The latest `known_at` among every row the page shows: everything
    `page_data` reads for this window, so "as of" can never predate what the
    page itself displays (e.g. a kill-switch row the KPI row already shows)."""
    candidates = (
        [window.known_at]
        + [r.known_at for r in runs]
        + [r.known_at for r in results]
        + [o.known_at for o in orders]
        + [e.known_at for e in order_events]
        + [f.fill.known_at for f in fills]
        + [o.known_at for o in outcomes]
        + [m.known_at for m in marks]
        + [k.known_at for k in kill_switch_rows]
        + [r.known_at for r in reconciliations]
        + [a.known_at for a in alerts]
    )
    return max(candidates, default=None)


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

    runs_with_results = journal.runs_for(conn, window_id)
    runs = [rw.run for rw in runs_with_results]
    results = [rw.result for rw in runs_with_results if rw.result is not None]
    orders = journal.orders_for(conn, window_id=window_id)
    order_events = journal.order_events_for(conn, window_id=window_id)
    outcomes = journal.outcomes_for(conn, window_id)
    all_marks = journal.positions_daily_for(conn, window_id)
    kill_switch_rows = journal.kill_switch_events_for(conn, window_id)
    reconciliations = journal.reconciliations_for(conn, window_id)
    all_fills = journal.fills_for(conn, window_id=window_id)
    fills, fills_capped = _capped_fills(all_fills, limit=limit)

    last_updated = max((r.finished_at for r in results), default=None)
    s_minus_1 = _required_run_session(utc_now())
    stale = not any(r.session == s_minus_1 for r in runs if r.session is not None)

    # `all_marks` is already every `positions_daily` row of the window, in
    # session order (`journal.positions_daily_for`'s contract), so the latest
    # marked session is its last row's -- `journal.last_marked_session` would
    # only re-read the same rows.
    last_session = all_marks[-1].session if all_marks else None
    positions_count = 0
    positions_value = 0.0
    if last_session is not None:
        marks = [m for m in all_marks if m.session == last_session and m.security_id is not None]
        positions_count = sum(1 for m in marks if m.quantity != 0)
        positions_value = sum(m.value or 0.0 for m in marks if m.quantity != 0)

    open_orders_count = len(journal.non_terminal_orders(conn, window_id=window_id))

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

    chains, chains_capped = _build_chains(orders, order_events, all_fills, outcomes, limit=limit)
    alerts, alerts_capped = _alerts_for_window(conn, window, limit=limit)
    reconciliation = max(reconciliations, key=lambda r: r.at, default=None)

    as_of = _as_of(
        window,
        runs=runs,
        results=results,
        orders=orders,
        order_events=order_events,
        fills=all_fills,
        outcomes=outcomes,
        marks=all_marks,
        kill_switch_rows=kill_switch_rows,
        reconciliations=reconciliations,
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

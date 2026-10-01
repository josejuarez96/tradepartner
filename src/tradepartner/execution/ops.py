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

**Staleness.** "As of" is the latest `known_at` among the rows this function
reads for the window (never looked up independently, so it can never show a
time later than what the page itself displays); "last updated" is the latest
`paper_run_results.finished_at`. `stale` is True when the session before
today's (S-1, by the New York calendar, mirroring
`execution.reconcile_run.command_session`) has no run with that `session` in
this window.

**Row limits.** The alerts list, the chain view and the fills table are each
capped at `dashboard.page_row_limit` rows (ADR 0011's "Consequences": a
render's read connection blocks the run's write connections for as long as
`page_data` takes, so its reads are bounded structurally rather than by how
large the journal has grown). `OpsData` reports each cap that bit
(`alerts_capped`, `chains_capped`, `fills_capped`) so the page can say so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings
from tradepartner.execution import lock
from tradepartner.execution.switch import SwitchState, derive
from tradepartner.store import journal
from tradepartner.store.db import utc_now
from tradepartner.store.journal import (
    AlertRow,
    DecisionRow,
    JournalNotInitialised,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PaperPlanRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    SignalRow,
)

__all__ = ["ChainStep", "OpsData", "OrderChain", "RankedSignal", "page_data"]

_NEW_YORK = ZoneInfo("America/New_York")

#: Deterministic tie-break for steps sharing a `known_at` in one order's chain.
_STEP_ORDER: dict[str, int] = {"order": 0, "order_event": 1, "fill": 2, "outcome": 3}


def _today_session(now: datetime) -> date:
    """The New York calendar session `now` falls on, or the one before it when
    `now` is not a session (`execution.reconcile_run.command_session`'s rule,
    mirrored here rather than imported, so this read-only module does not pull
    in the write-side reconciliation module)."""
    day = now.astimezone(_NEW_YORK).date()
    return day if is_session(day) else previous_session(day)


@dataclass(frozen=True)
class RankedSignal:
    """One security's place in the latest plan's ranking (spec req 12 hero)."""

    security_id: str
    score: float | None
    rank: int | None
    signal_reason: str
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
    conn: duckdb.DuckDBPyConnection, window_id: int, *, limit: int
) -> tuple[tuple[AlertRow, ...], bool]:
    """The window's alerts (run-scoped kinds dedupe on (kind, run), so every
    alert in scope names a run of this window), newest first, capped at
    `limit`. `no_window` and `locked` carry no run id and precede any window,
    so they are never this window's to show."""
    journal.require_journal(conn)
    rows = conn.execute(
        'SELECT alert_id, run_id, session, kind, message, "at", known_at, ingested_at '
        "FROM alerts WHERE run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?) "
        "ORDER BY alert_id DESC",
        [window_id],
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


def _decision_reason(decision: DecisionRow) -> str | None:
    """`decision.reason` when set (`left_universe`, `left_targets`, ...), else
    `decision.decision` itself (`override`, `skip_delisted`, `trade`, ...)."""
    return decision.reason if decision.reason is not None else decision.decision


def _build_ranking(
    conn: duckdb.DuckDBPyConnection, window_id: int, plan: PaperPlanRow
) -> tuple[RankedSignal, ...]:
    signals = journal.signals_for(conn, plan.run_id)
    decisions = journal.decisions_for(conn, window_id, rebalance_session=plan.rebalance_session)
    decision_reason: dict[str, str | None] = {}
    for d in decisions:
        decision_reason[d.decision.security_id] = _decision_reason(d.decision)

    def _rank_key(s: SignalRow) -> tuple[bool, int]:
        return (s.rank is None, s.rank if s.rank is not None else 0)

    return tuple(
        RankedSignal(
            security_id=s.security_id,
            score=s.score,
            rank=s.rank,
            signal_reason=s.reason,
            decision_reason=decision_reason.get(s.security_id),
            selected=s.reason == "selected",
        )
        for s in sorted(signals, key=_rank_key)
    )


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
    conn: duckdb.DuckDBPyConnection, window_id: int, *, limit: int
) -> tuple[tuple[OrderChain, ...], bool]:
    orders = sorted(journal.orders_for(conn, window_id=window_id), key=lambda o: o.client_order_id)
    events_by_order: dict[str, list[OrderEventRow]] = {}
    for event in journal.order_events_for(conn, window_id=window_id):
        events_by_order.setdefault(event.client_order_id, []).append(event)
    fills_by_order: dict[str, list[OrderedFill]] = {}
    for ordered_fill in journal.fills_for(conn, window_id=window_id):
        fills_by_order.setdefault(ordered_fill.fill.client_order_id, []).append(ordered_fill)
    outcomes_by_order: dict[str, list[OutcomeRow]] = {}
    for outcome in journal.outcomes_for(conn, window_id):
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
    runs: Sequence[PaperRunRow],
    results: Sequence[PaperRunResultRow],
    orders: Sequence[OrderRow],
    fills: Sequence[OrderedFill],
) -> datetime | None:
    candidates = (
        [window.known_at]
        + [r.known_at for r in runs]
        + [r.known_at for r in results]
        + [o.known_at for o in orders]
        + [f.fill.known_at for f in fills]
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
    kill_switch_rows = journal.kill_switch_events_for(conn, window_id)
    fills, fills_capped = _capped_fills(conn, window_id, limit=limit)

    last_updated = max((r.finished_at for r in results), default=None)
    today_session = _today_session(utc_now())
    s_minus_1 = previous_session(today_session)
    stale = not any(r.session == s_minus_1 for r in runs if r.session is not None)

    last_session = journal.last_marked_session(conn, window_id)
    positions_count = 0
    positions_value = 0.0
    if last_session is not None:
        marks = [
            m
            for m in journal.positions_daily_for(conn, window_id)
            if m.session == last_session and m.security_id is not None
        ]
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

    chains, chains_capped = _build_chains(conn, window_id, limit=limit)
    alerts, alerts_capped = _alerts_for_window(conn, window_id, limit=limit)
    reconciliations = journal.reconciliations_for(conn, window_id)
    reconciliation = max(reconciliations, key=lambda r: r.at, default=None)

    as_of = _as_of(window, runs, results, orders, fills)

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
    conn: duckdb.DuckDBPyConnection, window_id: int, *, limit: int
) -> tuple[tuple[OrderedFill, ...], bool]:
    all_fills = journal.fills_for(conn, window_id=window_id)
    ordered = sorted(all_fills, key=lambda f: f.fill.fill_id or 0, reverse=True)
    capped = len(ordered) > limit
    return tuple(ordered[:limit]), capped

"""What each paper book is waiting on the owner for (paper-trading plan T256,
amendment 2026-10-20 #2096, item 1): the pure half of the "waiting on me" read.

Pure over explicit inputs: no I/O, no clock, no store. `execution.ops.
waiting_on_owner(conn, settings, now)` reads the rows and hands them here as
`BookFacts` and `SpanFacts`; `build` turns them into `WaitingItem`s. Each item
names the book, what is waiting, since when, a detail in words and, where one
clears it, the command the owner types (text only: nothing here runs it).

**The kinds**, in the fixed order `build` sorts by (then book token, then
`since`):

1. `kill_switch_engaged`: `switch.derive` says engaged. A switch that only has a
   run in progress is not engaged and is no item. `since` is the latest engaged
   row's `at`; with no `engaged` row (a crashed or faulted run, or an unfinished
   one, engages it alone) it is the `started_at` of the earliest run that
   engages it. Command: `paper resume`.
2. `order_lagging`: a non-terminal order whose `collect.lag_verdict` is
   `breached` under the window's frozen `RiskConfig`, judged at S-1
   (`ops._required_run_session(now)`): the session of the last run the owner
   can already expect to have happened, so the item shows exactly the breaches
   that run judged, with the same rule and anchor. The run itself judges at its
   own session S, so today's run (S = S-1's next session) may find one more
   breach before this read shows it; S-1 is kept so the read never claims a
   breach no run has reached (the spec-critic follow-up on the plan line).
   `since` is the verdict's anchor. Command: `paper settle`, only once Alpaca no
   longer knows the order.
3. `missed_run`: no run of the window on S-1 (`ops._stale_and_last_updated`'s
   `stale`), skipped for a window started after S-1's close. `since` is S-1.
   No command: the detail says what to check (scheduling runbook).
4. `shakedown_note`: a `stale_data` or `missed_run` alert at or after the open
   span's `start_at` (and at or before `now`) that no `shakedown_note` names.
   `since` is the alert's `at`. Command: `decision shakedown-note`.
5. `report_due`: at least `ReportFacts.minimum` tracked rebalances (`paper
   report`'s count) and no `paper_reports` row yet: only the first report is
   prompted, because every `paper report` opens a counted tracking trial.
   `since` is the second rebalance session. Command: `paper report`.
6. `unreadable`: one per book whose rows raised a `ValueError` (an
   `UnreadableMarkError`, `frozen_risk` on an unreadable `frozen_json`,
   `lag_verdict` on an unreadable reconciliation row), carrying every message.
   No command. The book's kinds that read fine still come out, and so do every
   other book's items.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Final, Literal
from zoneinfo import ZoneInfo

from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.collect import lag_verdict
from tradepartner.execution.plain import plain_causes
from tradepartner.execution.switch import SwitchState, faulted_runs
from tradepartner.store.journal import (
    KillSwitchRow,
    OrderRow,
    PaperWindowRow,
    ReconciliationRow,
    RunWithResult,
)

__all__ = [
    "KIND_ORDER",
    "NOTED_ALERT_KINDS",
    "BookFacts",
    "LagFacts",
    "ReportFacts",
    "RunFacts",
    "SpanAlert",
    "SpanFacts",
    "SwitchFacts",
    "WaitingItem",
    "WaitingKind",
    "build",
]

_NEW_YORK = ZoneInfo("America/New_York")

WaitingKind = Literal[
    "kill_switch_engaged",
    "order_lagging",
    "missed_run",
    "shakedown_note",
    "report_due",
    "unreadable",
]

#: The fixed order items come out in, kind first (module docstring).
KIND_ORDER: Final[tuple[WaitingKind, ...]] = (
    "kill_switch_engaged",
    "order_lagging",
    "missed_run",
    "shakedown_note",
    "report_due",
    "unreadable",
)

#: The alert kinds the shakedown needs noted (ADR 0017 E.1 and E.7).
NOTED_ALERT_KINDS: Final = ("stale_data", "missed_run")


@dataclass(frozen=True, kw_only=True)
class WaitingItem:
    """One thing a book is waiting on the owner for. `since` is a timestamp or a
    session (by kind, module docstring), None for `unreadable`; `command` is the
    text the owner types to clear it, None where no command does."""

    book_id: str
    kind: WaitingKind
    since: datetime | date | None
    detail: str
    command: str | None


@dataclass(frozen=True, kw_only=True)
class SwitchFacts:
    """`switch.derive`'s output and the rows it was derived from."""

    window: PaperWindowRow
    state: SwitchState
    kill_switch_rows: tuple[KillSwitchRow, ...]
    runs: tuple[RunWithResult, ...]


@dataclass(frozen=True, kw_only=True)
class LagFacts:
    """The window's non-terminal orders, its reconciliations, the session the
    bound is judged at (S-1) and the window's frozen risk section."""

    orders: tuple[OrderRow, ...]
    reconciliations: tuple[ReconciliationRow, ...]
    session: date
    frozen: RiskConfig


@dataclass(frozen=True, kw_only=True)
class RunFacts:
    """Whether S-1 has no run of the window, S-1 itself, and when the window
    started."""

    stale: bool
    session: date
    window_started_at: datetime


@dataclass(frozen=True, kw_only=True)
class ReportFacts:
    """`paper report`'s tracking count (None when the cadence cannot be read),
    whether the window has a `paper_reports` row, the count that makes a report
    due, and the window's second rebalance session (None when not read)."""

    tracking_count: int | None
    has_report: bool
    minimum: int
    second_rebalance: date | None


@dataclass(frozen=True, kw_only=True)
class BookFacts:
    """One open window's inputs. A section is None when it was not read (its
    read raised); `errors` carries the messages of the reads that raised."""

    book_id: str
    switch: SwitchFacts | None = None
    lag: LagFacts | None = None
    run: RunFacts | None = None
    report: ReportFacts | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SpanAlert:
    """An alert the open shakedown span may need noted."""

    alert_id: int
    book_id: str
    kind: str
    session: date
    at: datetime


@dataclass(frozen=True, kw_only=True)
class SpanFacts:
    """The open span's instant lower bound, the candidate alerts, the alert ids
    some `shakedown_note` names, and `now` (alerts after it are not read)."""

    start_at: datetime
    alerts: tuple[SpanAlert, ...]
    noted: frozenset[int]
    now: datetime


def _kill_switch_item(book_id: str, facts: SwitchFacts) -> WaitingItem | None:
    state = facts.state
    if not state.engaged:
        return None
    since: datetime | None
    if state.engaged_row is not None:
        since = state.engaged_row.at
    else:
        runs = [rw.run for rw in facts.runs]
        results = [rw.result for rw in facts.runs if rw.result is not None]
        faulted = set(faulted_runs(facts.window, facts.kill_switch_rows, runs, results))
        own = [rw for rw in facts.runs if rw.run.window_id == facts.window.window_id]
        latest = max((rw.run.run_id or 0 for rw in own), default=None)
        in_progress = latest if state.run_in_progress else None
        engaging = [
            rw.run.started_at
            for rw in own
            if rw.run.run_id != in_progress and (rw.result is None or rw.run.run_id in faulted)
        ]
        since = min(engaging, default=None)
    why = state.engaged_row.reason if state.engaged_row is not None else None
    return WaitingItem(
        book_id=book_id,
        kind="kill_switch_engaged",
        since=since,
        detail="kill switch engaged: "
        + plain_causes(state.causes)
        + (f"; reason given: {why}" if why else ""),
        command=f'tradepartner paper resume --book {book_id} --reason "<why>"',
    )


def _order_lagging_items(book_id: str, facts: LagFacts) -> list[WaitingItem]:
    items: list[WaitingItem] = []
    for order in facts.orders:
        verdict = lag_verdict(order, facts.reconciliations, facts.session, facts.frozen)
        if not verdict.breached:
            continue
        cid = order.client_order_id
        items.append(
            WaitingItem(
                book_id=book_id,
                kind="order_lagging",
                since=verdict.anchor,
                detail=(
                    f"order {cid} ({order.side} {order.symbol}) still lagging its fills "
                    f"{verdict.sessions_past} session(s) after it was first listed, at "
                    f"{facts.session.isoformat()} (bound {facts.frozen.max_fill_lag_sessions}): "
                    "check the order in Alpaca; settle it only if Alpaca no longer knows it"
                ),
                command=(
                    f'tradepartner paper settle --book {book_id} --order {cid} --reason "<why>"'
                ),
            )
        )
    return items


def _missed_run_item(book_id: str, facts: RunFacts) -> WaitingItem | None:
    if not facts.stale or facts.window_started_at > session_close(facts.session):
        return None
    return WaitingItem(
        book_id=book_id,
        kind="missed_run",
        since=facts.session,
        detail=(
            f"no run for session {facts.session.isoformat()}: check the Mac was awake and "
            "the job's last exit code (docs/runbooks/scheduling.md)"
        ),
        command=None,
    )


def _report_due_item(book_id: str, facts: ReportFacts) -> WaitingItem | None:
    if facts.has_report or facts.tracking_count is None or facts.tracking_count < facts.minimum:
        return None
    return WaitingItem(
        book_id=book_id,
        kind="report_due",
        since=facts.second_rebalance,
        detail=(
            f"{facts.tracking_count} rebalances tracked and no tracking report yet; "
            "`paper report` opens a counted tracking trial"
        ),
        command=f"tradepartner paper report --book {book_id}",
    )


def _shakedown_items(span: SpanFacts) -> list[WaitingItem]:
    return [
        WaitingItem(
            book_id=alert.book_id,
            kind="shakedown_note",
            since=alert.at,
            detail=(
                f"{alert.kind} alert {alert.alert_id} (session {alert.session.isoformat()}) "
                "is inside the shakedown span and has no shakedown note"
            ),
            command=(
                f'tradepartner decision shakedown-note --alert {alert.alert_id} --reason "<why>"'
            ),
        )
        for alert in span.alerts
        if alert.kind in NOTED_ALERT_KINDS
        and span.start_at <= alert.at <= span.now
        and alert.alert_id not in span.noted
    ]


def _book_items(book: BookFacts) -> list[WaitingItem]:
    items: list[WaitingItem] = []
    errors = list(book.errors)
    if book.switch is not None:
        item = _kill_switch_item(book.book_id, book.switch)
        if item is not None:
            items.append(item)
    if book.lag is not None:
        try:
            items.extend(_order_lagging_items(book.book_id, book.lag))
        except ValueError as exc:
            errors.append(str(exc))
    if book.run is not None:
        item = _missed_run_item(book.book_id, book.run)
        if item is not None:
            items.append(item)
    if book.report is not None:
        item = _report_due_item(book.book_id, book.report)
        if item is not None:
            items.append(item)
    if errors:
        items.append(
            WaitingItem(
                book_id=book.book_id,
                kind="unreadable",
                since=None,
                detail="; ".join(errors),
                command=None,
            )
        )
    return items


def _since_key(since: datetime | date | None) -> datetime:
    if since is None:
        return datetime.min.replace(tzinfo=UTC)
    if isinstance(since, datetime):
        return since
    return datetime.combine(since, time(0), tzinfo=_NEW_YORK)


def _ordered(items: Iterable[WaitingItem]) -> tuple[WaitingItem, ...]:
    return tuple(
        sorted(
            items,
            key=lambda i: (KIND_ORDER.index(i.kind), i.book_id, _since_key(i.since), i.detail),
        )
    )


def build(books: Sequence[BookFacts], span: SpanFacts | None) -> tuple[WaitingItem, ...]:
    """Every item the books and the open shakedown span (None when no span row
    exists) are waiting on, in the fixed order (module docstring): by kind, then
    book token, then `since`. Empty when nothing is waiting."""
    items: list[WaitingItem] = []
    for book in books:
        items.extend(_book_items(book))
    if span is not None:
        items.extend(_shakedown_items(span))
    return _ordered(items)

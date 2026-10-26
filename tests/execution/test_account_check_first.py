"""The broker's account_id is checked against the window's before any
broker-derived write in `paper resume`, `reconcile` and `abandon` (#2187)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import switch
from tradepartner.execution.reconcile_run import ReconciliationError, reconcile_command
from tradepartner.execution.resume import REFUSED, resume
from tradepartner.store import registry
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    append,
    order_events_for,
)


class FixedClock(Protocol):
    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
SPY = "SEC_SPY"
DAY1 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _window(settings: Settings, account_id: str) -> PaperWindowRow:
    frozen = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
    started = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id=account_id,
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=json.dumps(frozen, sort_keys=True),
        frozen_sha256=registry.params_sha256(frozen),
        known_at=started,
        ingested_at=started,
    )
    (window_id,) = _append(settings, row)
    return replace(row, window_id=window_id)


def _crashed_run(settings: Settings, window: PaperWindowRow, at: datetime) -> int:
    (run_id,) = _append(
        settings,
        PaperRunRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=at.date(),
            kind="rebalance",
            started_at=at,
            invoked_by="scheduler",
            code_version="test",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert run_id is not None
    return run_id


def _pending_order(
    settings: Settings, broker: FakeBroker, run_id: int, coid: str, at: datetime
) -> None:
    """A buy journaled `pending` (the crash came before its acknowledgement) that
    the book's own broker did receive."""
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 30),
            security_id=SPY,
            side="buy",
            planned_quantity=2.0,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    order = OrderRow(
        client_order_id=coid,
        decision_id=decision_id,
        run_id=run_id,
        session=at.date(),
        attempt=1,
        phase="buy",
        security_id=SPY,
        symbol="SPY",
        side="buy",
        quantity=2.0,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    _append(
        settings,
        order,
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
    )
    broker.submit(OrderRequest(coid, "SPY", Side.BUY, quantity=2.0))


def _fake(clock: FixedClock, account_id: str) -> FakeBroker:
    return FakeBroker(
        assets=FakeBroker.active_assets("SPY"),
        clock=clock,
        price_of=lambda _s: 100.0,
        auto_fill=False,
        account_id=account_id,
    )


def _resume(settings: Settings, broker: FakeBroker, clock: FixedClock) -> Any:
    ticking = lambda: clock.advance(microseconds=1)  # noqa: E731
    return resume(
        settings,
        lambda: open_for_write(settings),
        broker,
        ticking,
        "owner checked",
        False,
        accept_rejections=False,
    )


def test_resume_on_another_accounts_broker_writes_nothing_before_the_account_check(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    """F1: `paper resume` with the wrong account's keys (the owner pastes the
    `daily` pair into `ALPACA_PAPER_*`, say) settles the book's `pending` order
    against an account that never saw it: it journals `cancelled`
    `not_received` before the reconciliation's `account_id` check refuses.
    The real order lives on at the book's own account and may fill there."""
    window = _window(journal_settings, "PA_MAIN")
    own = _fake(fixed_clock, "PA_MAIN")
    other = _fake(fixed_clock, "PA_DAILY")
    run_id = _crashed_run(journal_settings, window, DAY1 - timedelta(hours=1))
    _pending_order(journal_settings, own, run_id, "tp-main-1", DAY1 - timedelta(hours=1))
    switch.engage(
        journal_settings,
        fixed_clock,
        window_id=window.window_id,  # type: ignore[arg-type]
        source="owner",
        reason="t",
    )

    outcome = _resume(journal_settings, other, fixed_clock)

    assert outcome.status == REFUSED  # the account_id mismatch refuses
    with open_read_only(journal_settings) as conn:
        statuses = [
            (e.status, e.reason)
            for e in order_events_for(conn, window_id=None)
            if e.client_order_id == "tp-main-1"
        ]
    # Nothing settled against another account (the order is still
    # pending, for a resume on the right account to settle).
    assert statuses == [("pending", None)], statuses


def test_after_a_wrong_account_resume_the_right_one_is_refused_while_the_order_is_open(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    """F1, the aftermath: the order the wrong-account resume wrote `cancelled`
    is still open at the book's own account. A resume on the right keys is
    refused `unexplained_open_order` (the journal holds it terminal) until the
    DAY order ends at the broker; if it fills, the fill is journaled under a
    `cancelled`/`not_received` order (see the report)."""
    window = _window(journal_settings, "PA_MAIN")
    own = _fake(fixed_clock, "PA_MAIN")
    other = _fake(fixed_clock, "PA_DAILY")
    run_id = _crashed_run(journal_settings, window, DAY1 - timedelta(hours=1))
    _pending_order(journal_settings, own, run_id, "tp-main-1", DAY1 - timedelta(hours=1))
    switch.engage(
        journal_settings,
        fixed_clock,
        window_id=window.window_id,  # type: ignore[arg-type]
        source="owner",
        reason="t",
    )
    _resume(journal_settings, other, fixed_clock)

    second = _resume(journal_settings, own, fixed_clock)

    # The right-keys resume settles the pending order and releases.
    assert second.status != REFUSED, second.reasons


def test_abandon_on_another_accounts_broker_is_refused(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    """F3: `paper abandon` goes on over a reconciliation mismatch by design, and
    an `account_id` mismatch is one: with the wrong keys it closes the window and
    records the OTHER account's positions as this window's residues."""
    from tradepartner.adapters.fake_broker import SetPosition
    from tradepartner.execution import window as window_module

    _window(journal_settings, "PA_MAIN")
    other = _fake(fixed_clock, "PA_DAILY")
    other.apply_account(SetPosition("SPY", 7.0))  # the daily book's holding

    try:
        result = window_module.abandon(
            journal_settings,
            lambda: open_for_write(journal_settings),
            other,
            fixed_clock,
            "owner abandons the window for the break test, long enough",
        )
    except window_module.WindowCommandRefused as exc:
        assert exc.reason == window_module.ACCOUNT_MISMATCH
        return
    raise AssertionError(f"abandoned over another account: {result}")


def test_reconcile_on_another_accounts_broker_writes_nothing_before_the_account_check(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    """`paper reconcile` with the wrong account's keys must not settle the
    book's `pending` order against an account that never saw it."""
    window = _window(journal_settings, "PA_MAIN")
    own = _fake(fixed_clock, "PA_MAIN")
    other = _fake(fixed_clock, "PA_DAILY")
    run_id = _crashed_run(journal_settings, window, DAY1 - timedelta(hours=1))
    _pending_order(journal_settings, own, run_id, "tp-main-1", DAY1 - timedelta(hours=1))

    with pytest.raises(ReconciliationError, match="PA_DAILY"):
        reconcile_command(
            journal_settings, lambda: open_for_write(journal_settings), other, fixed_clock
        )

    with open_read_only(journal_settings) as conn:
        statuses = [
            (e.status, e.reason)
            for e in order_events_for(conn, window_id=None)
            if e.client_order_id == "tp-main-1"
        ]
    assert statuses == [("pending", None)], statuses

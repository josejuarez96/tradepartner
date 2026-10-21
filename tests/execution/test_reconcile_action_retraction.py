"""Reconciliation on the fake broker after `action-retract` (#2092, plan T262).

10 `SPY` held since 2026-09-29 at 100, and a 2:1 split of `SPY` with ex-date
S = 2026-10-01 stored under Alpaca id `ca-spy-1` at the first-seen proxy
close(2026-09-30). The broker never booked it: Alpaca withdrew it. The ledger
expects 20 shares and the broker holds 10, so S halts on `quantity`. The owner
withdraws the split at 11:00 ET on S, before close(S). S still halts on a re-run
(its facts are cut at close(S-1), before the cancel); S+1's cut is close(S),
after it, so S+1 reconciles `ok`: 10 shares, cash 100,000 - 10 x 100 = 99,000.
The account's pre-window funding deposit (`FUNDING`) is the cash base.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from execution.test_reconcile_run import (
    CUT,
    FUNDING,
    PRICE,
    S2,
    SPY,
    BookedFake,
    FixedClock,
    S,
    _hold,
    _reconcile,
    _rows,
)
from tradepartner.action_retract import retract_action
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.calendar import next_session, session_close
from tradepartner.config import Settings
from tradepartner.errors import ReconciliationError
from tradepartner.execution.reconcile import MISMATCH, OK
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.journal import PaperWindowRow

AID = "ca-spy-1"
#: The owner's retraction: 11:00 ET on S, after the 08:05 halt, before close(S).
RETRACTED_AT = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
#: 10:00 ET on S+1.
NEXT_MORNING = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)


@pytest.fixture
def fake(fixed_clock: FixedClock) -> BookedFake:
    return BookedFake(
        assets=FakeBroker.active_assets("SPY", "MTUM"),
        clock=fixed_clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PA1",
    )


def _held_with_a_withdrawn_split(
    settings: Settings, fake: BookedFake, window: PaperWindowRow
) -> None:
    fake.activities = [FUNDING]
    _hold(settings, fake, window, {(SPY, "SPY"): 10.0})
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": SPY,
                "action_type": "split",
                "ex_date": S,
                "ratio_or_amount": 2.0,
                "source_action_id": AID,
                "known_at": CUT,
                "ingested_at": CUT + timedelta(hours=2, minutes=30),
                "source": "alpaca",
                "provenance": "action",
            },
        )


def test_a_withdrawn_split_halts_until_its_cancel_is_known_then_reconciles(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _held_with_a_withdrawn_split(journal_settings, fake, open_window)
    with pytest.raises(ReconciliationError, match="quantity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    retract_action(
        journal_settings,
        security_id=SPY,
        source_action_id=AID,
        reason="Alpaca withdrew the split",
        clock=lambda: RETRACTED_AT,
        expect_known_at=CUT,
    )
    fixed_clock.now = RETRACTED_AT + timedelta(minutes=30)
    with pytest.raises(ReconciliationError, match="quantity"):  # S is cut at close(S-1)
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    fixed_clock.now = NEXT_MORNING
    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert result.status == OK
    assert result.broker_cash == pytest.approx(100_000.0 - 10.0 * PRICE)
    assert [row.status for row in _rows(journal_settings, open_window)] == [
        MISMATCH,
        MISMATCH,
        OK,
    ]


def test_without_the_cancel_the_next_session_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _held_with_a_withdrawn_split(journal_settings, fake, open_window)
    fixed_clock.now = NEXT_MORNING

    with pytest.raises(ReconciliationError, match="quantity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)


def test_a_cancel_written_after_close_s_also_costs_s_plus_1(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The owner acts at 19:00 ET on S, after close(S): S+1's cut close(S) is
    before the cancel, so S+1 halts too; S+2's cut close(S+1) is after it."""
    _held_with_a_withdrawn_split(journal_settings, fake, open_window)
    late = session_close(S) + timedelta(hours=3)
    retract_action(
        journal_settings,
        security_id=SPY,
        source_action_id=AID,
        reason="Alpaca withdrew the split",
        clock=lambda: late,
        expect_known_at=CUT,
    )
    fixed_clock.now = NEXT_MORNING
    with pytest.raises(ReconciliationError, match="quantity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    s3 = next_session(S2)
    fixed_clock.now = datetime.combine(s3, NEXT_MORNING.timetz())
    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=s3)

    assert result.status == OK
    assert result.broker_cash == pytest.approx(100_000.0 - 10.0 * PRICE)

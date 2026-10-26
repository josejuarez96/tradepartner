"""A delisted holding removed by the broker on one session and paid on a later
one (#411, owner decision 2026-10-26).

The store cases run `reconcile_now` on the fake broker and the fixture store
with a `delistings` row and 10 `MTUM` bought at 100 (the reference price). The
removal session is S (2026-10-01); with `risk.reconcile_merger_lag_sessions` = 5
the cash window ends 2026-10-08 and 2026-10-09 is the first session after it.
The credit is an `MA` activity tagged to the symbol, as the merger rule's is.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from execution.test_lots_adjustments import D0303, D0307, D0312, Book
from execution.test_reconcile_run import (
    CUT,
    FUNDING,
    MTUM,
    SPY,
    BookedFake,
    FixedClock,
    S,
    _adjustments,
    _delisting,
    _hold,
    _reconcile,
    _reference,
    _rows,
    fake,  # noqa: F401  (fixture)
)
from tradepartner.adapters.broker import CashActivity
from tradepartner.calendar import next_session
from tradepartner.config import Settings
from tradepartner.errors import ReconciliationError
from tradepartner.execution.reconcile import OK, OVERDUE_DELISTING_CASH
from tradepartner.store.journal import PaperWindowRow

AFTER = date(2026, 10, 9)  # the first session after the cash window


def _later(session: date, count: int) -> date:
    for _ in range(count):
        session = next_session(session)
    return session


def _credit(symbol: str, amount: float, day: date, activity_type: str = "MA") -> CashActivity:
    return CashActivity(
        activity_id=f"{activity_type}-{symbol}-{day}",
        activity_type=activity_type,
        date=day,
        status="executed",
        net_amount=amount,
        symbol=symbol,
    )


def _at(clock: FixedClock, day: date) -> None:
    clock.now = datetime(day.year, day.month, day.day, 12, 5, tzinfo=UTC)


def _removed_on_s(
    settings: Settings,
    broker: BookedFake,
    window: PaperWindowRow,
    clock: FixedClock,
    holdings: dict[tuple[str, str], float] | None = None,
) -> None:
    """Hold, delist and remove the names with no cash; the S reconcile is `ok`."""
    held = holdings or {(MTUM, "MTUM"): 10.0}
    broker.activities.append(FUNDING)
    run_id = _hold(settings, broker, window, held)
    for (security_id, symbol), quantity in held.items():
        _delisting(settings, security_id, CUT - timedelta(days=1))
        broker.extra_quantity[symbol] = -quantity
    assert _reconcile(settings, broker, window, clock, run_id=run_id).status == OK


def _kinds(
    settings: Settings, window: PaperWindowRow
) -> list[tuple[str, float | None, float | None]]:
    return [(a.kind, a.quantity, a.cash) for a in _adjustments(settings, window)]


def _halts_on_cash(
    settings: Settings,
    broker: BookedFake,
    window: PaperWindowRow,
    clock: FixedClock,
    session: date,
) -> None:
    """The reconcile raises and its row's only mismatch is `cash`."""
    with pytest.raises(ReconciliationError):
        _reconcile(settings, broker, window, clock, session=session)
    payload = json.loads(_rows(settings, window)[-1].mismatches_json or "{}")
    assert [m["kind"] for m in payload["mismatches"]] == ["cash"]


def _last_allowed(settings: Settings, window: PaperWindowRow) -> list[str]:
    payload = json.loads(_rows(settings, window)[-1].mismatches_json or "{}")
    return [a["reason"] for a in payload["allowed"]]


def test_an_ended_listing_removed_then_paid_two_sessions_later_reconciles(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The live ACA shape: removed with no cash on S, the tagged credit two
    sessions later is booked as the position's proceeds (the probe of the break
    pass, with the credit tagged to its symbol)."""
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    proceeds = 10 * _reference(journal_settings, MTUM)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = proceeds
    fake.activities.append(_credit("MTUM", proceeds, later))

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=later).status == OK

    assert _kinds(journal_settings, open_window) == [
        ("corporate_action_cash", -10.0, 0.0),
        ("corporate_action_cash", None, proceeds),
    ]
    (credit,) = [a for a in _adjustments(journal_settings, open_window) if a.quantity is None]
    assert credit.broker_activity_id == f"MA-MTUM-{later}"
    assert credit.security_id == MTUM


def test_the_late_credit_is_booked_once_and_a_second_one_is_new_cash(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    proceeds = 10 * _reference(journal_settings, MTUM)
    later = _later(S, 1)
    _at(fixed_clock, later)
    fake.extra_cash = proceeds
    fake.activities.append(_credit("MTUM", proceeds, later))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=later).status == OK
    again = _later(S, 2)
    _at(fixed_clock, again)

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=again).status == OK

    assert len(_adjustments(journal_settings, open_window)) == 2


def test_cash_arriving_the_same_session_still_reconciles_as_one_removal_row(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    fake.activities.append(FUNDING)
    run_id = _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0})
    _delisting(journal_settings, MTUM, CUT - timedelta(days=1))
    proceeds = 10 * _reference(journal_settings, MTUM)
    fake.extra_quantity["MTUM"] = -10.0
    fake.extra_cash = proceeds
    fake.activities.append(_credit("MTUM", proceeds, S))

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)

    assert result.status == OK
    assert _kinds(journal_settings, open_window) == [("corporate_action_cash", -10.0, proceeds)]


def test_an_untagged_credit_after_the_removal_still_halts(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The cash with no activity naming the symbol is unexplained, as before."""
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = 10 * _reference(journal_settings, MTUM)

    _halts_on_cash(journal_settings, fake, open_window, fixed_clock, later)


def test_a_credit_tagged_to_another_symbol_still_halts(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    proceeds = 10 * _reference(journal_settings, MTUM)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = proceeds
    fake.activities.append(_credit("SPY", proceeds, later))

    _halts_on_cash(journal_settings, fake, open_window, fixed_clock, later)


def test_a_tagged_credit_after_the_window_halts(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    proceeds = 10 * _reference(journal_settings, MTUM)
    _at(fixed_clock, AFTER)
    fake.extra_cash = proceeds
    fake.activities.append(_credit("MTUM", proceeds, AFTER))

    _halts_on_cash(journal_settings, fake, open_window, fixed_clock, AFTER)


def test_a_tagged_credit_far_above_the_position_value_halts(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    big = 20 * _reference(journal_settings, MTUM)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = big
    fake.activities.append(_credit("MTUM", big, later))

    _halts_on_cash(journal_settings, fake, open_window, fixed_clock, later)


def test_no_cash_within_the_window_is_recorded_once_and_never_halts(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The window's last session is quiet; the first reconciliation after it is
    `ok` and records the overdue item on the row; the next one does not repeat it."""
    _removed_on_s(journal_settings, fake, open_window, fixed_clock)
    inside = date(2026, 10, 8)
    _at(fixed_clock, inside)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=inside).status == OK
    assert OVERDUE_DELISTING_CASH not in _last_allowed(journal_settings, open_window)

    _at(fixed_clock, AFTER)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=AFTER).status == OK
    assert _last_allowed(journal_settings, open_window) == [OVERDUE_DELISTING_CASH]

    later = date(2026, 10, 12)
    _at(fixed_clock, later)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=later).status == OK
    assert _last_allowed(journal_settings, open_window) == []


def test_a_second_delisted_name_does_not_borrow_the_first_ones_credit(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Both names are removed on S. Cash for MTUM is tagged to MTUM; the same
    amount arriving for SPY with no SPY tag is not explained by MTUM's item."""
    _removed_on_s(
        journal_settings,
        fake,
        open_window,
        fixed_clock,
        {(MTUM, "MTUM"): 10.0, (SPY, "SPY"): 10.0},
    )
    mtum = 10 * _reference(journal_settings, MTUM)
    spy = 10 * _reference(journal_settings, SPY)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = mtum + spy
    fake.activities.append(_credit("MTUM", mtum, later))

    _halts_on_cash(journal_settings, fake, open_window, fixed_clock, later)


def test_each_delisted_name_takes_its_own_tagged_credit(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _removed_on_s(
        journal_settings,
        fake,
        open_window,
        fixed_clock,
        {(MTUM, "MTUM"): 10.0, (SPY, "SPY"): 10.0},
    )
    mtum = 10 * _reference(journal_settings, MTUM)
    spy = 10 * _reference(journal_settings, SPY)
    later = _later(S, 2)
    _at(fixed_clock, later)
    fake.extra_cash = mtum + spy
    fake.activities += [_credit("MTUM", mtum, later), _credit("SPY", spy, later)]

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=later).status == OK

    credits = {
        a.security_id: a.cash
        for a in _adjustments(journal_settings, open_window)
        if a.quantity is None
    }
    assert credits == {MTUM: pytest.approx(mtum), SPY: pytest.approx(spy)}


# --- the lot ledger (lots.py, #2164 item 1) ----------------------------------

_DELISTING_CASH = {"delisting_cash": True}


def test_the_late_delisting_cash_is_the_removals_proceeds_in_the_lot_ledger() -> None:
    """10 @ 30 (basis 300), removed on 03-07 with no cash, paid 320 on 03-12:
    one disposal at 320, pnl +20, and no merger-removal raise."""
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    book.adjust(
        "corporate_action_cash",
        D0307,
        "SEC_T",
        quantity=-10.0,
        cash=0.0,
        explanation={"explanation": "listing ended"},
    )
    book.adjust("corporate_action_cash", D0312, "SEC_T", cash=320.0, explanation=_DELISTING_CASH)

    _lots, disposals, _flags = book.run(D0312)

    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [(10.0, 320.0, 20.0)]


def test_late_delisting_cash_with_no_removal_row_raises_in_the_lot_ledger() -> None:
    from tradepartner.execution.lots import LotLedgerError

    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    book.adjust("corporate_action_cash", D0312, "SEC_T", cash=320.0, explanation=_DELISTING_CASH)

    with pytest.raises(LotLedgerError, match="no removal row"):
        book.run(D0312)

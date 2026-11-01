"""The owner true-up of one break (paper spec amendment 2026-10-26 #2266, item 9;
plan T283b): item 16's **True-up** gates and writes, and its **Parts and the seen
set** cases, on the fake broker and the fixture store."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tradepartner.adapters.broker import CashActivity, PositionIntent, Side
from tradepartner.calendar import next_session
from tradepartner.config import Settings
from tradepartner.errors import ClockError
from tradepartner.execution import reconcile_run, true_up
from tradepartner.execution.reconcile import Explanations
from tradepartner.execution.window import WindowCommandRefused
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    PaperWindowRow,
    ReconcileBreakRow,
    adjustments_for,
    break_events_for,
)

from .test_reconcile_run import (  # noqa: F401  (fixtures used by name)
    FUNDING,
    SPY,
    BookedFake,
    FixedClock,
    S,
    _filled_flip_trade,
    _hold,
    _long_only_book,
    _order,
    _reconcile,
    _rows,
    _run,
    fake,
    flip_fake,
    frozen_window,
)

REASON = "checked the Alpaca statement: the broker's figure is right"


def _ticking(clock: FixedClock) -> Callable[[], datetime]:
    """A clock that moves one microsecond per reading, as a real one does."""
    return lambda: clock.advance(microseconds=1)


def _events(settings: Settings, window: PaperWindowRow) -> list[ReconcileBreakRow]:
    with open_read_only(settings) as conn:
        return break_events_for(conn, window.window_id or 0)


def _adjustments(settings: Settings, window: PaperWindowRow) -> list[AdjustmentRow]:
    with open_read_only(settings) as conn:
        return adjustments_for(conn, window.window_id or 0)


def _true_up(
    settings: Settings,
    broker: BookedFake,
    clock: FixedClock,
    break_id: str,
    reason: str = REASON,
) -> true_up.TrueUpResult:
    return true_up.true_up(
        settings, lambda: open_for_write(settings), broker, _ticking(clock), break_id, reason
    )


def _snapshot(settings: Settings, window: PaperWindowRow) -> tuple[Any, ...]:
    return (
        tuple(_adjustments(settings, window)),
        tuple(_events(settings, window)),
        tuple(_rows(settings, window)),
    )


def _open_quantity_and_cash_break(
    settings: Settings, broker: BookedFake, window: PaperWindowRow, clock: FixedClock
) -> ReconcileBreakRow:
    """SPY held 10; the broker holds 11 and credited an untagged-to-any-due `MA` of
    $30 on it: one material break with a quantity part and a cash part."""
    _hold(settings, broker, window, {(SPY, "SPY"): 10.0})
    broker.activities = [FUNDING, CashActivity("ma-1", "MA", S, "executed", 30.0, "SPY")]
    broker.extra_cash = 30.0
    broker.extra_quantity = {"SPY": 1.0}
    assert _reconcile(settings, broker, window, clock).status == "breaks"
    (opened,) = _events(settings, window)
    assert opened.material and opened.event == "opened"
    return opened


# --- the five no-write refusals --------------------------------------------------


def test_a_blank_reason_refuses_before_any_broker_read(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    before, calls = _snapshot(journal_settings, frozen_window), len(fake.calls)

    with pytest.raises(WindowCommandRefused) as refused:
        _true_up(journal_settings, fake, fixed_clock, opened.break_id, reason="   ")

    assert refused.value.reason == "reason"
    assert _snapshot(journal_settings, frozen_window) == before
    assert len(fake.calls) == calls


def test_a_closed_or_unknown_break_refuses_not_open(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    _true_up(journal_settings, fake, fixed_clock, opened.break_id)
    before = _snapshot(journal_settings, frozen_window)

    for break_id in (opened.break_id, "1:SEC_NONE:9"):
        with pytest.raises(WindowCommandRefused) as refused:
            _true_up(journal_settings, fake, fixed_clock, break_id)
        assert refused.value.reason == "not_open"
    assert _snapshot(journal_settings, frozen_window) == before


@pytest.mark.parametrize(
    "move",
    ["quantity", "cash", "new_activity", "gone_activity"],
)
def test_a_broker_that_moved_since_the_break_refuses_moved(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
    move: str,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    if move == "quantity":
        fake.extra_quantity = {"SPY": 2.0}
    elif move == "cash":
        fake.activities = [FUNDING, CashActivity("ma-1", "MA", S, "executed", 31.0, "SPY")]
    elif move == "new_activity":
        fake.activities = [*fake.activities, CashActivity("ma-2", "MA", S, "executed", 5.0, "SPY")]
    else:
        fake.activities = [FUNDING]
    before = _snapshot(journal_settings, frozen_window)

    with pytest.raises(WindowCommandRefused) as refused:
        _true_up(journal_settings, fake, fixed_clock, opened.break_id)

    assert refused.value.reason == "moved"
    assert "paper reconcile" in str(refused.value)
    assert _snapshot(journal_settings, frozen_window) == before


def test_a_non_terminal_order_on_the_name_refuses_open_order(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    run_id = _run(journal_settings, frozen_window)
    _order(
        journal_settings, run_id, "tp-inflight", SPY, "SPY", 1.0, events=("pending",), fill=False
    )
    before = _snapshot(journal_settings, frozen_window)

    with pytest.raises(WindowCommandRefused) as refused:
        _true_up(journal_settings, fake, fixed_clock, opened.break_id)

    assert refused.value.reason == "open_order"
    assert "tp-inflight" in str(refused.value)
    assert _snapshot(journal_settings, frozen_window) == before


def test_another_account_refuses_account_mismatch(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    fake._account_id = "PA-OTHER"
    before, calls = _snapshot(journal_settings, frozen_window), len(fake.calls)

    with pytest.raises(WindowCommandRefused) as refused:
        _true_up(journal_settings, fake, fixed_clock, opened.break_id)

    assert refused.value.reason == "account_mismatch"
    assert _snapshot(journal_settings, frozen_window) == before
    assert [call.method for call in fake.calls[calls:]] == ["account"]


def test_a_short_name_refuses_rather_than_adopting_a_long_figure(
    journal_settings: Settings,
    flip_fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    """The ledger's true-up replay states a long position; a signed name fails closed."""
    run_id = _hold(journal_settings, flip_fake, frozen_window, {(SPY, "SPY"): 10.0})
    flipped = next_session(S)
    traded_at = datetime.combine(flipped, datetime.min.time(), tzinfo=UTC) + timedelta(hours=18)
    fixed_clock.now = traded_at
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.SELL, PositionIntent.SELL_TO_CLOSE
    )
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.SELL, PositionIntent.SELL_TO_OPEN
    )
    flip_fake.extra_quantity = {"SPY": -1.0}
    fixed_clock.now = traded_at + timedelta(hours=2)
    _reconcile(journal_settings, flip_fake, frozen_window, fixed_clock, session=flipped)
    opened = _events(journal_settings, frozen_window)[-1]
    before = _snapshot(journal_settings, frozen_window)

    with pytest.raises(WindowCommandRefused) as refused:
        _true_up(journal_settings, flip_fake, fixed_clock, opened.break_id)

    assert refused.value.reason == "short_name"
    assert _snapshot(journal_settings, frozen_window) == before


def test_a_stamp_that_does_not_move_past_the_gate_is_a_clock_error(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    before = _snapshot(journal_settings, frozen_window)

    with pytest.raises(ClockError):
        true_up.true_up(
            journal_settings,
            lambda: open_for_write(journal_settings),
            fake,
            fixed_clock,  # never moves: the stamp ties the gate's reading
            opened.break_id,
            REASON,
        )
    assert _snapshot(journal_settings, frozen_window) == before


# --- what a true-up writes -------------------------------------------------------


def test_one_stamp_one_row_per_part_then_trued_up_and_the_next_reconcile_is_ok(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    opened = _open_quantity_and_cash_break(journal_settings, fake, frozen_window, fixed_clock)
    before = {a.adjustment_id for a in _adjustments(journal_settings, frozen_window)}

    result = _true_up(journal_settings, fake, fixed_clock, opened.break_id)

    rows = [
        a for a in _adjustments(journal_settings, frozen_window) if a.adjustment_id not in before
    ]
    assert [(a.kind, a.quantity, a.cash, a.broker_activity_id) for a in rows] == [
        ("owner_true_up", 1.0, None, None),
        ("owner_true_up", None, 30.0, "ma-1"),
    ]
    assert all(a.session == S for a in rows)
    explanation = json.loads(rows[0].explanation_json or "")
    assert explanation["broker_quantity"] == 11.0
    assert explanation["ledger_quantity"] == 10.0
    assert (explanation["break_id"], explanation["reason"]) == (opened.break_id, REASON)
    trued = _events(journal_settings, frozen_window)[-1]
    assert trued.event == "trued_up" and trued.reason == REASON
    assert json.loads(trued.adjustment_ids_json or "") == [a.adjustment_id for a in rows]
    assert list(result.adjustment_ids) == [a.adjustment_id for a in rows]
    # One clock reading stamps the chunk, later than every reading before it.
    stamps = {a.known_at for a in rows} | {a.ingested_at for a in rows}
    assert stamps == {trued.known_at} == {result.known_at}
    assert trued.known_at > opened.known_at

    fixed_clock.advance(seconds=1)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"
    assert _events(journal_settings, frozen_window)[-1].event == "trued_up"


def test_a_not_due_dividend_stays_open_across_two_breaks_rows_then_books_once_by_id(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.activities = [FUNDING, CashActivity("div-x", "DIV", S, "executed", 30.0, "SPY")]
    fake.extra_cash = 30.0
    for _ in range(2):
        assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"
        fixed_clock.advance(seconds=1)
    events = _events(journal_settings, frozen_window)
    assert [e.event for e in events] == ["opened", "observed"]

    _true_up(journal_settings, fake, fixed_clock, events[0].break_id)
    for _ in range(2):
        fixed_clock.advance(seconds=1)
        assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"

    booked = [a for a in _adjustments(journal_settings, frozen_window) if a.cash is not None]
    assert [(a.kind, a.cash, a.broker_activity_id) for a in booked] == [
        ("owner_true_up", 30.0, "div-x")
    ]
    assert [e.event for e in _events(journal_settings, frozen_window)] == [
        "opened",
        "observed",
        "trued_up",
    ]


def test_a_merger_conflict_stays_open_after_compare_stops_raising_it_until_trued_up(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.activities = [FUNDING]
    original = reconcile_run._explanations
    raised = {"once": True}

    def with_conflict(*args: Any, **kwargs: Any) -> Explanations:
        result = original(*args, **kwargs)
        if not raised["once"]:
            return result
        raised["once"] = False
        return replace(
            result,
            merger_conflicts={
                SPY: "merger ids ['deal-b', 'deal-a'] for SEC_SPY effective 2026-10-01"
            },
        )

    monkeypatch.setattr(reconcile_run, "_explanations", with_conflict)
    for _ in range(3):
        assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"
        fixed_clock.advance(seconds=1)
    events = _events(journal_settings, frozen_window)
    assert [e.event for e in events] == ["opened", "observed", "observed"]

    result = _true_up(journal_settings, fake, fixed_clock, events[0].break_id)

    (row,) = [a for a in _adjustments(journal_settings, frozen_window) if a.kind == "owner_true_up"]
    assert (row.quantity, row.cash, row.broker_activity_id) == (None, None, None)
    assert json.loads(row.explanation_json or "")["finding_key"] == (
        '["merger_conflict", ["deal-a", "deal-b"]]'
    )
    assert result.adjustment_ids == (row.adjustment_id,)
    fixed_clock.advance(seconds=1)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"


def test_a_trued_up_unknown_symbol_stays_closed_until_its_key_changes(
    journal_settings: Settings,
    fake: BookedFake,  # noqa: F811
    frozen_window: PaperWindowRow,  # noqa: F811
    fixed_clock: FixedClock,
) -> None:
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity = {"SPYX": 5.0}
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"
    (opened,) = _events(journal_settings, frozen_window)
    assert (opened.security_id, opened.symbol) == (None, "SPYX")

    _true_up(journal_settings, fake, fixed_clock, opened.break_id)
    (row,) = [a for a in _adjustments(journal_settings, frozen_window) if a.kind == "owner_true_up"]
    assert (row.security_id, row.quantity, row.cash) == (None, None, None)

    for _ in range(2):
        fixed_clock.advance(seconds=1)
        assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"
    assert [e.event for e in _events(journal_settings, frozen_window)] == ["opened", "trued_up"]

    fake.extra_quantity = {"SPYX": 6.0}
    fixed_clock.advance(seconds=1)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"
    events = _events(journal_settings, frozen_window)
    assert [e.event for e in events] == ["opened", "trued_up", "opened"]
    assert events[-1].break_id != opened.break_id


def test_acknowledged_keys_read_only_breaks_whose_latest_event_is_trued_up() -> None:
    at = datetime(2026, 10, 1, 15, tzinfo=UTC)
    key = '["unknown_symbol", "SPYX", 5.0]'
    part = json.dumps([{"kind": "unknown_symbol", "finding_key": key, "activity_ids": []}])

    def event(event_id: int, kind: str, break_id: str = "1:SPYX:1") -> ReconcileBreakRow:
        return ReconcileBreakRow(
            event_id=event_id,
            break_id=break_id,
            window_id=1,
            book_id="main",
            event=kind,
            parts_json=part,
            symbol="SPYX",
            material=True,
            first_seen_session=S,
            known_at=at,
            ingested_at=at,
        )

    assert reconcile_run.acknowledged_keys([event(1, "opened")]) == frozenset()
    assert reconcile_run.acknowledged_keys([event(2, "trued_up"), event(1, "opened")]) == {key}
    reopened = [event(1, "opened"), event(2, "trued_up"), event(3, "reopened")]
    assert reconcile_run.acknowledged_keys(reopened) == frozenset()

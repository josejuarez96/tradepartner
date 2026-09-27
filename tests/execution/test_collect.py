"""Collectors, fill cursors, the lag bound and the rejection cap (Phase 4 plan
T58; spec req 8 "Journal chain", req 4 rejections, and the "Fills are journaled
once" and "Rejections" acceptance cases that need no run)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import polars as pl
import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker, FillAt, PartialFill, Reject
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError
from tradepartner.execution.collect import (
    Collected,
    RejectionBreach,
    WriteOffContext,
    collect,
    lag_verdict,
    rejection_breaches,
)
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    FillCursorRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    append,
    decisions_for,
    fill_cursors,
    fills_for,
    order_events_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
TOL = FROZEN.reconcile_quantity_tolerance
NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
    }
)


# --- journal helpers --------------------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _run(settings: Settings, window: PaperWindowRow, at: datetime) -> int:
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


def _order(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    run_id: int,
    client_order_id: str,
    *,
    side: str = "buy",
    quantity: float | None = None,
    notional: float | None = None,
    symbol: str = "AAA",
    sells_in_flight: bool = False,
    acknowledge: bool = True,
) -> OrderRow:
    """A decision, its order row and `pending` event, the fake's submit, and
    (unless `acknowledge=False`) the `accepted` event: what the wrapper leaves."""
    at = clock()
    if quantity is None and notional is None:
        notional = 1000.0
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 30),
            security_id=f"SEC-{symbol}",
            side=side,
            planned_notional=notional,
            planned_quantity=quantity,
            target_notional=notional if side == "buy" else None,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    row = OrderRow(
        client_order_id=client_order_id,
        decision_id=decision_id,
        run_id=run_id,
        session=at.date(),
        attempt=1,
        phase=side,
        security_id=f"SEC-{symbol}",
        symbol=symbol,
        side=side,
        notional=notional,
        quantity=quantity,
        sells_in_flight_at_submit=sells_in_flight,
        known_at=at,
        ingested_at=at,
    )
    rows: list[object] = [
        row,
        OrderEventRow(
            client_order_id=client_order_id, status="pending", known_at=at, ingested_at=at
        ),
    ]
    order = fake.submit(
        OrderRequest(
            client_order_id=client_order_id,
            symbol=symbol,
            side=Side(side),
            notional=notional,
            quantity=quantity,
        )
    )
    if acknowledge:
        rows.append(
            OrderEventRow(
                client_order_id=client_order_id,
                status="accepted",
                broker_order_id=order.broker_order_id,
                known_at=at,
                ingested_at=at,
            )
        )
    _append(settings, *rows)
    return row


def _collect(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    orders: Sequence[OrderRow],
    *,
    writer_kind: str = "run",
    writer_id: int = 1,
    frozen: RiskConfig = FROZEN,
    write_offs: WriteOffContext | None = None,
) -> Collected:
    return collect(
        fake,
        lambda: open_for_write(settings),
        orders,
        clock,
        writer_kind,
        writer_id,
        frozen,
        settings,
        write_offs=write_offs,
    )


def _fills(settings: Settings) -> list[OrderedFill]:
    with open_read_only(settings) as conn:
        return fills_for(conn)


def _all_fill_rows(settings: Settings) -> list[tuple[str, int | None]]:
    with open_read_only(settings) as conn:
        return conn.execute(
            "SELECT broker_fill_id, superseded_by FROM fills ORDER BY fill_id"
        ).fetchall()


def _events(settings: Settings, client_order_id: str) -> list[OrderEventRow]:
    with open_read_only(settings) as conn:
        return [
            e
            for e in order_events_for(conn, window_id=None)
            if e.client_order_id == client_order_id
        ]


def _cursors(settings: Settings) -> list[FillCursorRow]:
    with open_read_only(settings) as conn:
        return fill_cursors(conn)


def _statuses(settings: Settings, client_order_id: str) -> list[str]:
    return [e.status for e in _events(settings, client_order_id)]


# --- fills are journaled once -----------------------------------------------


def test_an_overlapping_read_never_doubles_a_fill(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", FillAt(50.0))
    fixed_clock.advance(minutes=1)

    first = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    second = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)

    assert first.fills_journaled == ("fake-fill-1",)
    assert second.fills_journaled == ()
    assert [f.fill.broker_fill_id for f in _fills(journal_settings)] == ["fake-fill-1"]
    assert _statuses(journal_settings, "tp-a") == ["pending", "accepted", "filled"]


def test_a_fill_carries_the_brokers_time_and_the_collection_time(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    filled_at = fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", FillAt(50.0))
    collected_at = fixed_clock.advance(minutes=5)

    _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)

    (fill,) = _fills(journal_settings)
    assert (fill.fill.filled_at, fill.fill.known_at, fill.fill.ingested_at) == (
        filled_at,
        collected_at,
        collected_at,
    )
    assert (fill.fill.source, fill.fill.price_implied, fill.fill.superseded_by) == (
        "broker_feed",
        False,
        None,
    )
    terminal = _events(journal_settings, "tp-a")[-1]
    assert (terminal.status, terminal.event_at, terminal.known_at) == (
        "filled",
        filled_at,
        collected_at,
    )
    assert (terminal.filled_quantity, terminal.filled_avg_price) == (10.0, 50.0)


def test_a_real_fill_after_the_synthetic_one_is_superseded_and_never_reenters(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    scripted_fake.lag_fills(1)
    fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", FillAt(50.0))
    at = fixed_clock.advance(minutes=1)
    # What `paper resume --accept-broker-fills` leaves: a synthetic residual and
    # the terminal event.
    (synthetic_id,) = _append(
        journal_settings,
        FillRow(
            client_order_id="tp-a",
            filled_at=at,
            quantity=10.0,
            price=50.0,
            price_implied=True,
            broker_fill_id="synthetic:tp-a",
            source="broker_status",
            known_at=at,
            ingested_at=at,
        ),
    )
    _append(
        journal_settings,
        OrderEventRow(client_order_id="tp-a", status="filled", known_at=at, ingested_at=at),
    )
    scripted_fake.lag_fills(0)
    _collect(journal_settings, scripted_fake, fixed_clock, [])  # the lag hides it once
    fixed_clock.advance(minutes=1)
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [])
    assert collected.fills_journaled == ("fake-fill-1",)
    assert collected.superseded == ("fake-fill-1",)
    for _ in range(2):
        fixed_clock.advance(minutes=1)
        assert _collect(journal_settings, scripted_fake, fixed_clock, []).fills_journaled == ()

    assert _all_fill_rows(journal_settings) == [
        ("synthetic:tp-a", None),
        ("fake-fill-1", synthetic_id),
    ]
    assert [f.fill.broker_fill_id for f in _fills(journal_settings)] == ["synthetic:tp-a"]


def test_the_earlier_of_two_lagging_fills_is_picked_up_by_the_next_read(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    a = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    b = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-b", quantity=5.0)
    scripted_fake.lag_fills(1)
    fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", FillAt(50.0))  # earlier, lags one read
    scripted_fake.lag_fills(0)
    later = fixed_clock.advance(minutes=30)
    scripted_fake.apply("tp-b", FillAt(20.0))
    fixed_clock.advance(minutes=1)

    first = _collect(journal_settings, scripted_fake, fixed_clock, [a, b], writer_id=run_id)
    assert first.fills_journaled == ("fake-fill-2",)
    assert [r.order.client_order_id for r in first.lagging] == ["tp-a"]
    assert first.collected_through == later
    assert _statuses(journal_settings, "tp-a") == ["pending", "accepted"]

    fixed_clock.advance(hours=2)
    second = _collect(journal_settings, scripted_fake, fixed_clock, [a], writer_id=run_id)
    assert second.fills_journaled == ("fake-fill-1",)
    assert second.lagging == ()
    assert _statuses(journal_settings, "tp-a")[-1] == "filled"
    assert _statuses(journal_settings, "tp-b")[-1] == "filled"


def test_the_read_window_reaches_back_to_the_oldest_open_orders_pending_event(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    submitted = fixed_clock()
    a = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    fixed_clock.advance(hours=3)
    _append(
        journal_settings,
        FillCursorRow(
            writer_kind="run",
            writer_id=run_id,
            collected_through=fixed_clock(),
            known_at=fixed_clock(),
            ingested_at=fixed_clock(),
        ),
    )
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [a], writer_id=run_id)
    overlap = timedelta(seconds=journal_settings.paper.fill_read_overlap_seconds)
    assert collected.since == submitted - overlap
    assert [c.args for c in scripted_fake.calls if c.method == "fills"] == [(submitted - overlap,)]


def test_with_no_open_order_the_read_starts_at_the_latest_cursor(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    through = fixed_clock()
    _append(
        journal_settings,
        *(
            FillCursorRow(
                writer_kind="run",
                writer_id=run_id,
                collected_through=through - timedelta(hours=h),
                known_at=through,
                ingested_at=through,
            )
            for h in (5, 0, 2)
        ),
    )
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [], writer_id=run_id)
    overlap = timedelta(seconds=journal_settings.paper.fill_read_overlap_seconds)
    assert collected.since == through - overlap


def test_three_fills_one_ulp_short_complete_the_order(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    parts = (0.41, 0.772656, 2.285278)
    assert parts[0] + parts[1] + parts[2] < 3.467934  # one ulp short, added naively
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=3.467934)
    for part in parts:
        fixed_clock.advance(seconds=10)
        scripted_fake.apply("tp-a", PartialFill(part, 50.0))
    fixed_clock.advance(minutes=1)

    collected = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)

    assert len(collected.fills_journaled) == 3
    assert collected.lagging == ()
    assert _statuses(journal_settings, "tp-a")[-1] == "filled"


def test_a_terminal_status_before_its_fills_leaves_the_order_in_flight_and_lagging(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    scripted_fake.lag_fills(1)
    fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", FillAt(50.0))
    fixed_clock.advance(minutes=1)

    first = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    (reading,) = first.readings
    assert reading.lagging and reading.reading.status == "filled"
    assert reading.journaled_quantity == 0.0
    assert not reading.terminal_written
    assert _statuses(journal_settings, "tp-a") == ["pending", "accepted"]

    fixed_clock.advance(minutes=1)
    second = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert second.lagging == ()
    assert second.readings[0].terminal_written
    assert _statuses(journal_settings, "tp-a")[-1] == "filled"


def test_a_partly_filled_open_order_whose_fill_lags_is_lagging(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    scripted_fake.lag_fills(1)
    scripted_fake.apply("tp-a", PartialFill(4.0, 50.0))
    fixed_clock.advance(minutes=1)

    (reading,) = _collect(
        journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id
    ).readings
    assert reading.reading.status == "accepted"
    assert reading.lagging
    assert _statuses(journal_settings, "tp-a") == ["pending", "accepted"]


def test_an_order_that_ends_without_fills_gets_its_terminal_event(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    scripted_fake.apply("tp-a", Expire())
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert collected.readings[0].terminal_written
    terminal = _events(journal_settings, "tp-a")[-1]
    assert (terminal.status, terminal.filled_quantity, terminal.event_at) == ("expired", None, None)


def test_a_terminal_order_in_the_journal_is_not_read_again(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    scripted_fake.apply("tp-a", Expire())
    _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    calls = len(scripted_fake.calls)
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert collected.readings == ()
    assert [c.method for c in scripted_fake.calls[calls:]] == ["fills"]
    assert _statuses(journal_settings, "tp-a").count("expired") == 1


def test_a_pending_order_is_refused_before_any_broker_call(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(
        journal_settings,
        scripted_fake,
        fixed_clock,
        run_id,
        "tp-a",
        quantity=10.0,
        acknowledge=False,
    )
    calls = len(scripted_fake.calls)
    with pytest.raises(ValueError, match="pending"):
        _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert scripted_fake.calls[calls:] == ()
    assert _cursors(journal_settings) == []


def test_a_fill_of_an_order_the_journal_does_not_hold_is_not_journaled(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    scripted_fake.submit(
        OrderRequest(client_order_id="manual-1", symbol="ZZZ", side=Side.BUY, quantity=1.0)
    )
    scripted_fake.apply("manual-1", FillAt(10.0))
    fixed_clock.advance(minutes=1)
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [], writer_id=run_id)
    assert collected.foreign_fill_ids == ("fake-fill-1",)
    assert collected.fills_journaled == ()
    assert _all_fill_rows(journal_settings) == []


# --- cursors ------------------------------------------------------------------


def test_one_cursor_row_per_invocation(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    filled_at = fixed_clock.advance(minutes=1)
    scripted_fake.apply("tp-a", PartialFill(4.0, 50.0))
    fixed_clock.advance(minutes=1)
    _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    fixed_clock.advance(minutes=1)
    _collect(
        journal_settings, scripted_fake, fixed_clock, [order], writer_kind="resume", writer_id=7
    )
    fixed_clock.advance(minutes=1)
    _collect(journal_settings, scripted_fake, fixed_clock, [], writer_id=run_id)

    cursors = _cursors(journal_settings)
    assert [(c.writer_kind, c.writer_id) for c in cursors] == [
        ("run", run_id),
        ("resume", 7),
        ("run", run_id),
    ]
    # The latest `filled_at` seen; a read that sees nothing newer keeps it.
    assert [c.collected_through for c in cursors] == [filled_at] * 3


def test_the_first_read_with_nothing_seen_writes_the_clock(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    _collect(journal_settings, scripted_fake, fixed_clock, [], writer_kind="resume", writer_id=3)
    (cursor,) = _cursors(journal_settings)
    assert cursor.collected_through == fixed_clock()
    assert [c.args for c in scripted_fake.calls if c.method == "fills"] == [(None,)]


def test_an_unknown_writer_kind_is_refused(
    journal_settings: Settings, scripted_fake: FakeBroker, fixed_clock: FixedClock
) -> None:
    with pytest.raises(ValueError, match="writer_kind"):
        _collect(journal_settings, scripted_fake, fixed_clock, [], writer_kind="page")


# --- the broker-clock skew --------------------------------------------------


def _skewed_fill(fake: FakeBroker, clock: FixedClock, ahead: float) -> None:
    clock.advance(seconds=ahead)
    fake.apply("tp-a", FillAt(50.0))
    clock.advance(seconds=-ahead)


def test_a_fill_ahead_of_the_clock_beyond_the_skew_is_a_clock_error(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    _skewed_fill(scripted_fake, fixed_clock, FROZEN.max_broker_clock_skew_seconds + 1)
    with pytest.raises(ClockError, match="ahead"):
        _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert _all_fill_rows(journal_settings) == []
    assert _cursors(journal_settings) == []


def test_a_fill_ahead_by_exactly_the_skew_is_accepted(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", quantity=10.0)
    _skewed_fill(scripted_fake, fixed_clock, FROZEN.max_broker_clock_skew_seconds)
    collected = _collect(journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id)
    assert collected.fills_journaled == ("fake-fill-1",)


# --- the rejection cap --------------------------------------------------------


def _rejecting_run(
    settings: Settings,
    window: PaperWindowRow,
    fake: FakeBroker,
    clock: FixedClock,
    *,
    rejected: int,
    others: int = 0,
) -> tuple[int, list[OrderRow]]:
    run_id = _run(settings, window, clock())
    orders = []
    for i in range(rejected + others):
        order = _order(settings, fake, clock, run_id, f"tp-r{run_id}-{i}", quantity=1.0)
        if i < rejected:
            fake.apply(order.client_order_id, Reject())
        orders.append(order)
    return run_id, orders


def test_rejections_above_the_cap_are_a_verdict_naming_the_submitting_run(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    frozen = RiskConfig(max_rejections_per_run=2)
    submitting, orders = _rejecting_run(
        journal_settings, open_window, scripted_fake, fixed_clock, rejected=3, others=1
    )
    fixed_clock.advance(hours=20)
    observing = _run(journal_settings, open_window, fixed_clock())
    collected = _collect(
        journal_settings, scripted_fake, fixed_clock, orders, writer_id=observing, frozen=frozen
    )
    (breach,) = collected.rejections
    assert breach == RejectionBreach(run_id=submitting, rejected=3, orders=4, all_rejected=False)
    assert f"run {submitting}" in breach.message
    assert all(_statuses(journal_settings, o.client_order_id)[-1] == "rejected" for o in orders[:3])


def test_rejections_at_the_cap_are_not_a_verdict(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    frozen = RiskConfig(max_rejections_per_run=2)
    _, orders = _rejecting_run(
        journal_settings, open_window, scripted_fake, fixed_clock, rejected=2, others=1
    )
    collected = _collect(journal_settings, scripted_fake, fixed_clock, orders, frozen=frozen)
    assert collected.rejections == ()


def test_a_run_whose_every_order_is_rejected_is_a_verdict_below_the_cap(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    submitting, orders = _rejecting_run(
        journal_settings, open_window, scripted_fake, fixed_clock, rejected=2
    )
    collected = _collect(journal_settings, scripted_fake, fixed_clock, orders)
    (breach,) = collected.rejections
    assert breach == RejectionBreach(run_id=submitting, rejected=2, orders=2, all_rejected=True)
    assert "every order" in breach.message


def test_a_rejection_observed_earlier_is_not_judged_again(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    frozen = RiskConfig(max_rejections_per_run=0)
    _, orders = _rejecting_run(
        journal_settings, open_window, scripted_fake, fixed_clock, rejected=1, others=1
    )
    assert _collect(journal_settings, scripted_fake, fixed_clock, orders, frozen=frozen).rejections
    # The still-open order of that run is collected again later: no new rejection.
    later = _collect(journal_settings, scripted_fake, fixed_clock, [orders[1]], frozen=frozen)
    assert later.rejections == ()


def _event(client_order_id: str, status: str, minute: int = 0) -> OrderEventRow:
    at = datetime(2026, 10, 1, 14, minute, tzinfo=UTC)
    return OrderEventRow(
        client_order_id=client_order_id, status=status, known_at=at, ingested_at=at
    )


def _plain_order(client_order_id: str, run_id: int) -> OrderRow:
    at = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    return OrderRow(
        client_order_id=client_order_id,
        decision_id=1,
        run_id=run_id,
        session=at.date(),
        attempt=1,
        phase="buy",
        security_id="S",
        symbol="S",
        side="buy",
        notional=10.0,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )


def test_rejection_breaches_count_only_the_named_runs() -> None:
    orders = [_plain_order("a", 1), _plain_order("b", 1), _plain_order("c", 2)]
    events = [_event("a", "rejected"), _event("b", "rejected"), _event("c", "rejected")]
    assert rejection_breaches([2], orders, events, max_rejections=5) == (
        RejectionBreach(run_id=2, rejected=1, orders=1, all_rejected=True),
    )
    assert rejection_breaches([1, 2], orders, events, max_rejections=0) == (
        RejectionBreach(run_id=1, rejected=2, orders=2, all_rejected=True),
        RejectionBreach(run_id=2, rejected=1, orders=1, all_rejected=True),
    )


def test_a_cancelled_or_filled_order_is_not_a_rejection() -> None:
    orders = [_plain_order("a", 1), _plain_order("b", 1)]
    events = [_event("a", "cancelled"), _event("b", "rejected")]
    assert rejection_breaches([1], orders, events, max_rejections=5) == ()


# --- the lag bound ------------------------------------------------------------


def _reconciliation(at: datetime, lagging: Sequence[str], rid: int) -> ReconciliationRow:
    return ReconciliationRow(
        reconciliation_id=rid,
        window_id=1,
        at=at,
        status="fills_lagging" if lagging else "ok",
        mismatches_json=json.dumps({"lagging": list(lagging), "mismatches": []}),
        known_at=at,
        ingested_at=at,
    )


def _at(day: date, hour: int = 14) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


THU, FRI, MON, TUE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)


def test_the_lag_bound_is_anchored_on_the_first_reconciliation_that_listed_the_order() -> None:
    order = _plain_order("tp-a", 1)
    rows = [
        _reconciliation(_at(THU, 13), [], 1),
        _reconciliation(_at(THU, 15), ["tp-a"], 2),
        _reconciliation(_at(FRI), ["tp-a"], 3),
    ]
    thursday = lag_verdict(order, rows, THU, FROZEN)
    assert (thursday.anchor, thursday.sessions_past, thursday.breached) == (_at(THU, 15), 0, False)
    friday = lag_verdict(order, rows, FRI, FROZEN)
    assert (friday.sessions_past, friday.breached) == (1, True)


def test_a_missed_run_exactly_n_sessions_later_does_not_reset_the_bound() -> None:
    frozen = RiskConfig(max_fill_lag_sessions=2)
    order = _plain_order("tp-a", 1)
    rows = [_reconciliation(_at(THU), ["tp-a"], 1)]
    assert not lag_verdict(order, rows, FRI, frozen).breached
    # Monday is exactly two sessions on, and its run was missed; Tuesday's
    # collection is past the bound.
    tuesday = lag_verdict(order, rows, TUE, frozen)
    assert (tuesday.sessions_past, tuesday.breached) == (3, True)


def test_a_pending_order_settled_late_with_a_brief_lag_does_not_trip_the_bound() -> None:
    # Submitted weeks ago, settled by resume on Thursday, first listed lagging then.
    order = _plain_order("tp-a", 1)
    rows = [_reconciliation(_at(date(2026, 9, 1)), [], 1), _reconciliation(_at(THU), ["tp-a"], 2)]
    assert not lag_verdict(order, rows, THU, FROZEN).breached


def test_an_order_never_listed_has_no_anchor() -> None:
    order = _plain_order("tp-a", 1)
    verdict = lag_verdict(order, [_reconciliation(_at(THU), ["tp-b"], 1)], MON, FROZEN)
    assert (verdict.anchor, verdict.sessions_past, verdict.breached) == (None, 0, False)


def test_a_weekend_anchor_counts_from_the_next_session() -> None:
    order = _plain_order("tp-a", 1)
    rows = [_reconciliation(_at(date(2026, 10, 3)), ["tp-a"], 1)]  # Saturday
    assert lag_verdict(order, rows, MON, FROZEN).sessions_past == 1


def test_an_unreadable_mismatches_json_fails_closed() -> None:
    order = _plain_order("tp-a", 1)
    bad = ReconciliationRow(
        reconciliation_id=1,
        window_id=1,
        at=_at(THU),
        status="mismatch",
        mismatches_json="not json",
        known_at=_at(THU),
        ingested_at=_at(THU),
    )
    with pytest.raises(ValueError, match="mismatches_json"):
        lag_verdict(order, [bad], FRI, FROZEN)


# --- the write-off back-fill ------------------------------------------------------


def _write_off_context(window: PaperWindowRow, session: date = THU) -> WriteOffContext:
    return WriteOffContext(
        window_id=window.window_id,  # type: ignore[arg-type]
        actions_as_of=NO_ACTIONS,
        price_of=lambda _security_id: 50.0,
        session=session,
    )


def test_a_terminal_buy_short_of_its_target_is_written_off_once(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", notional=1000.0)
    scripted_fake.apply("tp-a", PartialFill(12.0, 50.0))
    scripted_fake.apply("tp-a", Expire())
    fixed_clock.advance(minutes=1)
    context = _write_off_context(open_window)

    first = _collect(
        journal_settings, scripted_fake, fixed_clock, [order], writer_id=run_id, write_offs=context
    )
    assert first.written_off == (order.decision_id,)
    with open_read_only(journal_settings) as conn:
        (decision,) = decisions_for(conn, open_window.window_id)  # type: ignore[arg-type]
    (event,) = decision.events
    assert (event.status, event.reason, event.run_id) == ("written_off", "unfunded", run_id)
    assert event.unfunded_notional == pytest.approx(400.0)

    again = _collect(
        journal_settings, scripted_fake, fixed_clock, [], writer_id=run_id, write_offs=context
    )
    assert again.written_off == ()


def test_a_buy_submitted_with_sells_in_flight_is_not_written_off(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(
        journal_settings,
        scripted_fake,
        fixed_clock,
        run_id,
        "tp-a",
        notional=1000.0,
        sells_in_flight=True,
    )
    scripted_fake.apply("tp-a", Expire())
    collected = _collect(
        journal_settings,
        scripted_fake,
        fixed_clock,
        [order],
        writer_id=run_id,
        write_offs=_write_off_context(open_window),
    )
    assert collected.written_off == ()


def test_a_resume_never_writes_a_write_off(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window, fixed_clock())
    order = _order(journal_settings, scripted_fake, fixed_clock, run_id, "tp-a", notional=1000.0)
    scripted_fake.apply("tp-a", Expire())
    with pytest.raises(ValueError, match="run"):
        _collect(
            journal_settings,
            scripted_fake,
            fixed_clock,
            [order],
            writer_kind="resume",
            writer_id=4,
            write_offs=_write_off_context(open_window),
        )
    collected = _collect(
        journal_settings, scripted_fake, fixed_clock, [order], writer_kind="resume", writer_id=4
    )
    assert collected.written_off == ()
    with open_read_only(journal_settings) as conn:
        assert all(not d.events for d in decisions_for(conn, open_window.window_id))  # type: ignore[arg-type]

"""`paper resume` (Phase 4 plan T61b; spec req 5's release order, req 4's
settlement of `pending` orders, req 8's lag bound and synthetic fills, and the
resume acceptance cases; #374's rejection-cap handoff from T58)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pytest

from tradepartner.adapters.broker import Order, OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker, PartialFill, Reject
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import resume as resume_module
from tradepartner.execution import switch
from tradepartner.execution.collect import collect
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile_run import reconcile_now
from tradepartner.execution.resume import NO_WINDOW, REFUSED, RELEASED, resume
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    append,
    fill_cursors,
    fills_for,
    kill_switch_events_for,
    order_events_for,
    reconciliations_for,
    resume_acceptances,
    resume_invocations,
    runs_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
PRICE = 100.0
SPY = "SEC_SPY"  # a fixture name listed through 2026
DAY1 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # the conftest clock: S = 2026-10-01
DAY2 = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)  # the next session


class SkewedFake(FakeBroker):
    """A fake whose `get_order` can report a rounded average price for one order,
    as a broker that rounds its average does."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.avg_override: dict[str, float] = {}

    def get_order(self, client_order_id: str) -> Order:
        order = super().get_order(client_order_id)
        if client_order_id in self.avg_override:
            order = replace(order, filled_avg_price=self.avg_override[client_order_id])
        return order


@pytest.fixture
def fake(fixed_clock: FixedClock) -> SkewedFake:
    return SkewedFake(
        clock=fixed_clock, price_of=lambda _s: PRICE, auto_fill=False, account_id="PA1"
    )


@pytest.fixture
def window(journal_settings: Settings) -> PaperWindowRow:
    """An open window with its frozen risk section as `paper start` writes it."""
    return _open_window(journal_settings, FROZEN)


def _open_window(settings: Settings, risk: RiskConfig) -> PaperWindowRow:
    frozen = {f"risk.{k}": v for k, v in risk.model_dump(mode="json").items()}
    started = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=json.dumps(frozen, sort_keys=True),
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )
    (window_id,) = _append(settings, row)
    return replace(row, window_id=window_id)


# --- helpers ------------------------------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _run(settings: Settings, window: PaperWindowRow, at: datetime, *, finished: bool) -> int:
    """A run of the window; `finished=False` leaves it without a result row (a crash)."""
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
    if finished:
        _append(
            settings,
            PaperRunResultRow(
                run_id=run_id,
                finished_at=at,
                status="ok",
                clock_fault=False,
                known_at=at,
                ingested_at=at,
            ),
        )
    return run_id


def _order(
    settings: Settings,
    fake: FakeBroker,
    run_id: int,
    coid: str,
    quantity: float,
    at: datetime,
    *,
    acknowledge: bool = True,
    submit: bool = True,
) -> OrderRow:
    """A buy's decision, order row and `pending` event, the fake's submit (unless
    `submit=False`: the broker never got it) and, unless `acknowledge=False`,
    the `accepted` event."""
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 30),
            security_id=SPY,
            side="buy",
            planned_quantity=quantity,
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
        quantity=quantity,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    rows: list[object] = [
        order,
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
    ]
    if submit:
        placed = fake.submit(OrderRequest(coid, "SPY", Side.BUY, quantity=quantity))
        if acknowledge:
            rows.append(
                OrderEventRow(
                    client_order_id=coid,
                    status="accepted",
                    broker_order_id=placed.broker_order_id,
                    known_at=at,
                    ingested_at=at,
                )
            )
    _append(settings, *rows)
    return order


def _engage(settings: Settings, window: PaperWindowRow, clock: FixedClock) -> None:
    switch.engage(settings, clock, window_id=window.window_id, source="owner", reason="test")  # type: ignore[arg-type]


def _resume(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    *,
    accept: bool = False,
    accept_rejections: bool = False,
) -> Any:
    # A real clock moves between reads; the release must be stamped after the
    # crashed close for `switch.derive` to clear the crashed run.
    ticking = lambda: clock.advance(microseconds=1)  # noqa: E731
    return resume(
        settings,
        lambda: open_for_write(settings),
        fake,
        ticking,
        "owner checked",
        accept,
        accept_rejections=accept_rejections,
    )


def _collect(
    settings: Settings, fake: FakeBroker, clock: FixedClock, orders: list[OrderRow]
) -> Any:
    return collect(
        fake, lambda: open_for_write(settings), orders, clock, "run", 1, FROZEN, settings
    )


def _engaged(settings: Settings, window: PaperWindowRow) -> bool:
    with open_read_only(settings) as conn:
        runs = runs_for(conn, window.window_id)  # type: ignore[arg-type]
        state = switch.derive(
            window,
            kill_switch_events_for(conn, window.window_id),  # type: ignore[arg-type]
            [r.run for r in runs],
            [r.result for r in runs if r.result is not None],
            reading_run=None,
            lock_free=True,
        )
    return state.engaged


def _events(settings: Settings, coid: str) -> list[OrderEventRow]:
    with open_read_only(settings) as conn:
        return [e for e in order_events_for(conn, window_id=None) if e.client_order_id == coid]


def _all_fills(settings: Settings, coid: str) -> list[Any]:
    with open_read_only(settings) as conn:
        rows = conn.execute(
            "SELECT fill_id, quantity, price, source, broker_fill_id, superseded_by, "
            "price_implied FROM fills WHERE client_order_id = ? ORDER BY fill_id",
            [coid],
        ).fetchall()
    return rows


def _count(settings: Settings, table: str) -> int:
    with open_read_only(settings) as conn:
        (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
    return int(n)


def _lagging_third_fill(
    settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    clock: FixedClock,
    coid: str,
    *,
    last: float = 1.0,
) -> OrderRow:
    """An order of three shares: two fills journaled, the third booked at the
    broker (FILLED) but never in `fills()`, listed lagging by a reconciliation
    on DAY1, and the clock moved to DAY2 so the lag is past the bound (1)."""
    run_id = _run(settings, window, DAY1 - timedelta(hours=1), finished=True)
    order = _order(settings, fake, run_id, coid, 2.0 + last, DAY1 - timedelta(hours=1))
    fake.apply(coid, PartialFill(1.0, 100.0))
    fake.apply(coid, PartialFill(1.0, 101.0))
    _collect(settings, fake, clock, [order])
    fake.lag_fills(None)
    fake.apply(coid, PartialFill(last, 102.0))
    first = reconcile_now(
        settings,
        lambda: open_for_write(settings),
        fake,
        window,
        DAY1.date(),
        clock,
        lambda: open_for_write(settings),
        frozen=FROZEN,
        as_of=clock(),
    )
    assert first.lagging_ids == (coid,)
    clock.now = DAY2
    _engage(settings, window, clock)
    return order


# --- settlement of pending orders --------------------------------------------


def test_a_pending_order_the_fake_filled_is_settled_with_its_fills_before_its_filled_event(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    crashed = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    _order(
        journal_settings,
        fake,
        crashed,
        "tp-p1",
        2.0,
        DAY1 - timedelta(hours=1),
        acknowledge=False,
    )
    fake.simulate_fill("tp-p1")

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    statuses = [e.status for e in _events(journal_settings, "tp-p1")]
    assert statuses == ["pending", "accepted", "filled"]
    with open_read_only(journal_settings) as conn:
        (fill,) = fills_for(conn, client_order_ids=["tp-p1"])
        filled_event = [e for e in order_events_for(conn, window_id=None) if e.status == "filled"]
        assert fill.fill.known_at <= filled_event[0].known_at
    assert fill.fill.quantity == 2.0
    assert not _engaged(journal_settings, window)


def test_a_pending_order_the_broker_never_received_is_cancelled_not_received(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    crashed = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    _order(
        journal_settings,
        fake,
        crashed,
        "tp-lost",
        2.0,
        DAY1 - timedelta(hours=1),
        acknowledge=False,
        submit=False,
    )

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    last = _events(journal_settings, "tp-lost")[-1]
    assert (last.status, last.reason) == ("cancelled", "not_received")
    assert outcome.settled == (("tp-lost", "not_received"),)


def test_resume_closes_an_unfinished_run_crashed_and_releases_with_its_ids(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    crashed = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    assert outcome.crashed_runs == (crashed,)
    with open_read_only(journal_settings) as conn:
        (run,) = runs_for(conn, window.window_id)  # type: ignore[arg-type]
        (invocation,) = resume_invocations(conn)
        (reconciliation,) = reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]
        released = [
            e
            for e in kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
            if e.state == "released"
        ]
    assert run.result is not None and run.result.status == "crashed"
    assert invocation.reason == "owner checked" and not invocation.accept_broker_fills
    assert not invocation.accept_rejections
    (row,) = released
    assert (row.resume_id, row.reconciliation_id) == (
        invocation.resume_id,
        reconciliation.reconciliation_id,
    )
    assert row.peak_equity == window.starting_equity  # no mark yet
    assert (outcome.resume_id, outcome.reconciliation_id, outcome.released_event_id) == (
        invocation.resume_id,
        reconciliation.reconciliation_id,
        row.event_id,
    )


def test_the_release_resets_the_peak_to_the_equity_at_the_last_mark(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    for session, cash in ((date(2026, 9, 29), 100_000.0), (date(2026, 9, 30), 93_000.0)):
        _append(
            journal_settings,
            PositionDailyRow(
                run_id=run_id, session=session, quantity=0.0, cash=cash, known_at=at, ingested_at=at
            ),
            PositionDailyRow(
                run_id=run_id,
                session=session,
                security_id=SPY,
                quantity=10.0,
                mark_price=50.0,
                value=500.0,
                known_at=at,
                ingested_at=at,
            ),
        )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    with open_read_only(journal_settings) as conn:
        released = [
            e
            for e in kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
            if e.state == "released"
        ]
    assert released[0].peak_equity == 93_500.0


def test_the_release_resets_the_peak_from_marks_fors_real_row_shape(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Regression for #653: `marks.marks_for`'s own docstring says a session
    with nothing held gets a `security_id=None` cash row, but once anything
    is held every row is a per-security row carrying the ledger's `cash`
    redundantly -- there is never a dedicated null-security row. Build the
    last marked session in exactly that shape (no cash-only row; every
    position row carries the same `cash`) and require `resume` to still read
    the peak from it, rather than refusing because no null-security row
    exists."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    session = date(2026, 9, 30)
    cash = 93_000.0
    _append(
        journal_settings,
        PositionDailyRow(
            run_id=run_id,
            session=session,
            security_id=SPY,
            quantity=10.0,
            mark_price=50.0,
            value=500.0,
            cash=cash,
            known_at=at,
            ingested_at=at,
        ),
        PositionDailyRow(
            run_id=run_id,
            session=session,
            security_id="SEC_OTHER",
            quantity=4.0,
            mark_price=25.0,
            value=100.0,
            cash=cash,
            known_at=at,
            ingested_at=at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    with open_read_only(journal_settings) as conn:
        released = [
            e
            for e in kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
            if e.state == "released"
        ]
    assert released[0].peak_equity == cash + 500.0 + 100.0


def test_only_the_max_marked_session_counts(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A broken earlier session (a position row with no value) must not sink
    the read: only the rows of the highest `session` count."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    _append(
        journal_settings,
        PositionDailyRow(
            run_id=run_id,
            session=date(2026, 9, 29),
            security_id=SPY,
            quantity=10.0,
            mark_price=None,
            value=None,
            cash=100_000.0,
            known_at=at,
            ingested_at=at,
        ),
        PositionDailyRow(
            run_id=run_id,
            session=date(2026, 9, 30),
            security_id=SPY,
            quantity=10.0,
            mark_price=50.0,
            value=500.0,
            cash=93_000.0,
            known_at=at,
            ingested_at=at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == RELEASED, outcome.reasons
    with open_read_only(journal_settings) as conn:
        released = [
            e
            for e in kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
            if e.state == "released"
        ]
    assert released[0].peak_equity == 93_500.0


# --- refusals -------------------------------------------------------------------


def test_resume_is_refused_while_reconciliation_fails_and_the_switch_stays_engaged(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _engage(journal_settings, window, fixed_clock)
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")  # a position the ledger lacks

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("broker_only_position" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (row,) = reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]
    assert row.status == "mismatch"


def test_resume_is_refused_after_an_all_rejected_run_and_the_switch_stays_engaged(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=True)
    _order(journal_settings, fake, run_id, "tp-r1", 1.0, DAY1 - timedelta(hours=1))
    _order(journal_settings, fake, run_id, "tp-r2", 1.0, DAY1 - timedelta(hours=1))
    fake.apply("tp-r1", Reject())
    fake.apply("tp-r2", Reject())
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any(f"every order of run {run_id} was rejected" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    assert _count(journal_settings, "reconciliations") == 0


def test_resume_judges_a_halted_runs_rejections_its_halt_read_journaled(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    # #397, owner answer (a): the halt read collects as its run, so no later
    # collection judges these rejections again; resume judges the halted run.
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    orders = [
        _order(journal_settings, fake, run_id, coid, 1.0, DAY1 - timedelta(hours=1))
        for coid in ("tp-h1", "tp-h2")
    ]
    for order in orders:
        fake.apply(order.client_order_id, Reject())
    halt_read = collect(
        fake,
        lambda: open_for_write(journal_settings),
        orders,
        fixed_clock,
        "run",
        run_id,
        FROZEN,
        journal_settings,
    )
    assert [b.run_id for b in halt_read.rejections] == [run_id]
    halted_at = fixed_clock.advance(minutes=1)
    _append(
        journal_settings,
        PaperRunResultRow(
            run_id=run_id,
            finished_at=halted_at,
            status="halted",
            clock_fault=False,
            known_at=halted_at,
            ingested_at=halted_at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any(f"every order of run {run_id} was rejected" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    assert _count(journal_settings, "reconciliations") == 0
    # Only a release clears the halted run, so every later resume refuses too.
    again = _resume(journal_settings, fake, fixed_clock)
    assert again.status == REFUSED
    assert any(f"every order of run {run_id} was rejected" in r for r in again.reasons)


def test_resume_names_a_crashed_runs_rejection_verdict_once(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    # The run is closed `crashed` by this resume; its collection and the
    # faulted-run check both find the verdict, and the refusal names it once.
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    for coid in ("tp-c1", "tp-c2"):
        _order(journal_settings, fake, run_id, coid, 1.0, DAY1 - timedelta(hours=1))
        fake.apply(coid, Reject())

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert outcome.crashed_runs == (run_id,)
    assert [r for r in outcome.reasons if f"run {run_id}" in r] == [
        f"every order of run {run_id} was rejected (2)"
    ]
    assert _engaged(journal_settings, window)
    assert _count(journal_settings, "reconciliations") == 0


def test_resume_without_the_flag_is_refused_past_the_lag_bound(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("tp-lag" in r and "lag bound" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    assert [row[3] for row in _all_fills(journal_settings, "tp-lag")] == [
        "broker_feed",
        "broker_feed",
    ]


def test_a_refused_resume_still_journals_its_fill_cursor(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    with open_read_only(journal_settings) as conn:
        resumes = [c for c in fill_cursors(conn) if c.writer_kind == "resume"]
    assert [c.writer_id for c in resumes] == [outcome.resume_id]


def test_with_the_flag_a_synthetic_third_fill_completes_two_journaled_ones(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")

    outcome = _resume(journal_settings, fake, fixed_clock, accept=True)

    assert outcome.status == RELEASED, outcome.reasons
    assert outcome.synthetic_fills == ("tp-lag",)
    rows = _all_fills(journal_settings, "tp-lag")
    assert [(q, p, src) for _, q, p, src, *_ in rows[:2]] == [
        (1.0, 100.0, "broker_feed"),
        (1.0, 101.0, "broker_feed"),
    ]
    _, quantity, price, source, broker_fill_id, superseded, implied = rows[2]
    assert (source, broker_fill_id, superseded, implied) == (
        "broker_status",
        "synthetic:tp-lag",
        None,
        True,
    )
    assert quantity == pytest.approx(1.0)
    assert price == pytest.approx(102.0)
    assert _events(journal_settings, "tp-lag")[-1].status == "filled"


def test_a_real_fill_after_the_synthetic_one_is_superseded_and_kept_once(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    order = _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")
    assert _resume(journal_settings, fake, fixed_clock, accept=True).status == RELEASED
    fake._fill_hidden_reads = [0 for _ in fake._fill_hidden_reads]  # the feed catches up

    for _ in range(3):
        fixed_clock.advance(minutes=1)
        _collect(journal_settings, fake, fixed_clock, [order])

    rows = _all_fills(journal_settings, "tp-lag")
    assert len(rows) == 4
    synthetic_id = rows[2][0]
    assert rows[3][5] == synthetic_id  # superseded_by the synthetic fill
    with open_read_only(journal_settings) as conn:
        live = fills_for(conn, client_order_ids=["tp-lag"])
    assert sum(f.fill.quantity for f in live) == pytest.approx(3.0)


def test_a_negative_implied_price_is_stored_with_price_implied(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")
    fake.avg_override["tp-lag"] = 60.0  # 3 x 60 < the 201 already journaled

    outcome = _resume(journal_settings, fake, fixed_clock, accept=True)

    assert outcome.synthetic_fills == ("tp-lag",)
    _, quantity, price, source, _, _, implied = _all_fills(journal_settings, "tp-lag")[2]
    assert (source, implied) == ("broker_status", True)
    assert quantity == pytest.approx(1.0)
    assert price == pytest.approx(3 * 60.0 - 201.0)


def test_with_the_flag_an_order_the_broker_still_holds_open_is_refused(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=True)
    order = _order(journal_settings, fake, run_id, "tp-open", 3.0, DAY1 - timedelta(hours=1))
    fake.lag_fills(None)
    fake.apply("tp-open", PartialFill(1.0, 100.0))  # booked, still ACCEPTED, never delivered
    reconcile_now(
        journal_settings,
        lambda: open_for_write(journal_settings),
        fake,
        window,
        DAY1.date(),
        fixed_clock,
        lambda: open_for_write(journal_settings),
        frozen=FROZEN,
        as_of=fixed_clock(),
    )
    fixed_clock.now = DAY2
    _engage(journal_settings, window, fixed_clock)
    assert order.client_order_id == "tp-open"

    outcome = _resume(journal_settings, fake, fixed_clock, accept=True)

    assert outcome.status == REFUSED
    assert any("tp-open" in r and "open" in r for r in outcome.reasons)
    assert outcome.synthetic_fills == ()


# --- the lock, no window, and what a resume never writes ----------------------


def test_no_window_is_refused_and_writes_nothing(
    journal_settings: Settings, fake: SkewedFake, fixed_clock: FixedClock
) -> None:
    outcome = _resume(journal_settings, fake, fixed_clock)
    assert outcome.status == NO_WINDOW
    assert fake.calls == ()
    assert _count(journal_settings, "resume_invocations") == 0


def test_resume_is_refused_while_another_process_holds_the_lock(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    with run_lock(journal_settings), pytest.raises(LockHeld):
        _resume(journal_settings, fake, fixed_clock)
    assert _count(journal_settings, "resume_invocations") == 0


def test_a_blank_reason_is_refused_before_anything(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    with pytest.raises(ValueError, match="reason"):
        resume(
            journal_settings,
            lambda: open_for_write(journal_settings),
            fake,
            fixed_clock,
            " ",
            False,
            accept_rejections=False,
        )
    assert _count(journal_settings, "resume_invocations") == 0


def test_a_resume_writes_no_decision_or_rebalance_event(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    _order(
        journal_settings,
        fake,
        run_id,
        "tp-p1",
        2.0,
        DAY1 - timedelta(hours=1),
        acknowledge=False,
    )
    fake.apply("tp-p1", Reject())  # a terminal buy a run would back-fill `written_off`
    before = (
        _count(journal_settings, "decision_events"),
        _count(journal_settings, "rebalance_events"),
    )

    _resume(journal_settings, fake, fixed_clock)

    after = (
        _count(journal_settings, "decision_events"),
        _count(journal_settings, "rebalance_events"),
    )
    assert after == before


# --- review follow-ups ---------------------------------------------------------


def _synthetic_row(settings: Settings, coid: str) -> Any:
    with open_read_only(settings) as conn:
        (fill,) = [
            f.fill
            for f in fills_for(conn, client_order_ids=[coid])
            if f.fill.source == "broker_status"
        ]
    return fill


def test_a_two_share_residual_is_priced_per_share_at_the_brokers_time(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag", last=2.0)
    broker_filled_at = fake.get_order("tp-lag").filled_at
    before = fixed_clock()

    outcome = _resume(journal_settings, fake, fixed_clock, accept=True)

    assert outcome.status == RELEASED, outcome.reasons
    fill = _synthetic_row(journal_settings, "tp-lag")
    assert fill.quantity == pytest.approx(2.0)
    assert fill.price == pytest.approx((4 * 101.25 - 201.0) / 2)  # 102, not 204
    assert fill.filled_at == broker_filled_at
    assert fill.known_at > before  # the write's own clock reading


def test_a_synthetic_fill_without_the_brokers_time_takes_the_orders_session_close(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    # A partly filled order that then expired: the broker gives no `filled_at`.
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=True)
    order = _order(journal_settings, fake, run_id, "tp-exp", 3.0, DAY1 - timedelta(hours=1))
    fake.apply("tp-exp", PartialFill(1.0, 100.0))
    _collect(journal_settings, fake, fixed_clock, [order])
    fake.lag_fills(None)
    fake.apply("tp-exp", PartialFill(0.5, 102.0))
    fake.apply("tp-exp", Expire())
    assert fake.get_order("tp-exp").filled_at is None
    reconcile_now(
        journal_settings,
        lambda: open_for_write(journal_settings),
        fake,
        window,
        DAY1.date(),
        fixed_clock,
        lambda: open_for_write(journal_settings),
        frozen=FROZEN,
        as_of=fixed_clock(),
    )
    fixed_clock.now = DAY2
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock, accept=True)

    assert outcome.status == RELEASED, outcome.reasons
    fill = _synthetic_row(journal_settings, "tp-exp")
    assert fill.filled_at == session_close(order.session)
    assert fill.quantity == pytest.approx(0.5)
    assert _events(journal_settings, "tp-exp")[-1].status == "expired"


def test_a_clock_that_does_not_move_reports_the_switch_still_engaged(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)

    outcome = resume(
        journal_settings,
        lambda: open_for_write(journal_settings),
        fake,
        fixed_clock,
        "x",
        False,
        accept_rejections=False,
    )

    assert outcome.status == REFUSED
    assert any("release refused" in r and "would not clear" in r for r in outcome.reasons)
    assert outcome.released_event_id is None
    assert _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        states = [e.state for e in kill_switch_events_for(conn, window.window_id)]  # type: ignore[arg-type]
    assert "released" not in states


def test_a_mismatch_found_with_nothing_engaged_engages_the_switch(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")
    assert not _engaged(journal_settings, window)

    assert _resume(journal_settings, fake, fixed_clock).status == REFUSED
    assert _engaged(journal_settings, window)


def test_a_last_mark_with_no_positive_equity_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    _append(
        journal_settings,
        PositionDailyRow(
            run_id=run_id,
            session=date(2026, 9, 30),
            quantity=0.0,
            cash=-50.0,
            known_at=at,
            ingested_at=at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("positive equity" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)


def test_two_distinct_cash_values_at_the_last_mark_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Two rows of the same session disagreeing on `cash` cannot be a
    single ledger reading: refuse rather than pick either one."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    session = date(2026, 9, 30)
    _append(
        journal_settings,
        PositionDailyRow(
            run_id=run_id,
            session=session,
            security_id=SPY,
            quantity=10.0,
            mark_price=50.0,
            value=500.0,
            cash=93_000.0,
            known_at=at,
            ingested_at=at,
        ),
        PositionDailyRow(
            run_id=run_id,
            session=session,
            security_id="SEC_OTHER",
            quantity=4.0,
            mark_price=25.0,
            value=100.0,
            cash=92_000.0,
            known_at=at,
            ingested_at=at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("positive equity" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)


def test_a_held_position_row_with_no_value_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, window, DAY1 - timedelta(days=1), finished=True)
    at = DAY1 - timedelta(days=1)
    _append(
        journal_settings,
        PositionDailyRow(
            run_id=run_id,
            session=date(2026, 9, 30),
            security_id=SPY,
            quantity=10.0,
            mark_price=None,
            value=None,
            cash=93_000.0,
            known_at=at,
            ingested_at=at,
        ),
    )
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("positive equity" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)


def test_a_failed_fault_engagement_is_named_in_the_refusal(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        switch, "engage", lambda *_a, **_k: switch.WriteFailed("IOException: store locked")
    )
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("NOT engaged" in r and "store locked" in r for r in outcome.reasons)


def test_an_engagement_written_during_the_resume_refuses_the_release(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engagement written after the resume row (a drawdown, say) is one the
    owner did not see: `release` refuses it, the resume reports REFUSED and the
    switch stays engaged (#447)."""
    _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)

    def reconcile_then_engage(*args: object, **kwargs: object) -> object:
        result = reconcile_now(*args, **kwargs)  # type: ignore[arg-type]
        assert isinstance(
            switch.engage(
                journal_settings,
                fixed_clock,
                window_id=window.window_id,  # type: ignore[arg-type]
                source="drawdown",
                reason="drawdown after the resume row",
            ),
            int,
        )
        return result

    monkeypatch.setattr(resume_module, "reconcile_now", reconcile_then_engage)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert any("release refused" in r and "after resume" in r for r in outcome.reasons)
    assert outcome.released_event_id is None
    assert _engaged(journal_settings, window)


# --- --accept-rejections (#472) ------------------------------------------------


def _halted_with_rejections(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    window: PaperWindowRow,
    coids: tuple[str, ...] = ("tp-h1", "tp-h2"),
    *,
    frozen: RiskConfig = FROZEN,
    others: tuple[OrderRow, ...] = (),
) -> int:
    """A run whose halt read journals the rejection of every order in `coids`
    (and collects `others`, orders of the same run), then ends `halted` with the
    switch engaged: #451's refusal, which only `--accept-rejections` gets past."""
    at = DAY1 - timedelta(hours=1)
    run_id = others[0].run_id if others else _run(settings, window, at, finished=False)
    orders = [_order(settings, fake, run_id, coid, 1.0, at) for coid in coids]
    for order in orders:
        fake.apply(order.client_order_id, Reject())
    halt_read = collect(
        fake,
        lambda: open_for_write(settings),
        [*others, *orders],
        clock,
        "run",
        run_id,
        frozen,
        settings,
    )
    assert [b.run_id for b in halt_read.rejections] == [run_id]
    halted_at = clock.advance(minutes=1)
    _append(
        settings,
        PaperRunResultRow(
            run_id=run_id,
            finished_at=halted_at,
            status="halted",
            clock_fault=False,
            known_at=halted_at,
            ingested_at=halted_at,
        ),
    )
    _engage(settings, window, clock)
    return run_id


def _acceptances(settings: Settings) -> list[tuple[int, list[dict[str, Any]], datetime]]:
    with open_read_only(settings) as conn:
        return [
            (row.resume_id, json.loads(row.accepted_json), row.known_at)
            for row in resume_acceptances(conn)
        ]


def _verdict(run_id: int, rejected: int, orders: int, *, all_rejected: bool) -> dict[str, Any]:
    message = (
        f"every order of run {run_id} was rejected ({rejected})"
        if all_rejected
        else (
            f"run {run_id} had {rejected} of {orders} orders rejected, "
            "over risk.max_rejections_per_run"
        )
    )
    return {
        "run_id": run_id,
        "rejected": rejected,
        "orders": orders,
        "all_rejected": all_rejected,
        "message": message,
    }


def test_without_the_flag_a_halted_runs_rejection_verdict_refuses_and_nothing_is_accepted(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _halted_with_rejections(journal_settings, fake, fixed_clock, window)

    outcome = _resume(journal_settings, fake, fixed_clock)

    assert outcome.status == REFUSED
    assert f"every order of run {run_id} was rejected (2)" in outcome.reasons
    assert outcome.accepted_rejections == ()
    assert _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (invocation,) = resume_invocations(conn)
    assert invocation.accept_rejections is False
    assert _count(journal_settings, "resume_acceptances") == 0


def test_with_the_flag_the_release_proceeds_and_the_journal_names_the_accepted_verdict(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _halted_with_rejections(journal_settings, fake, fixed_clock, window)

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == RELEASED, outcome.reasons
    assert outcome.accepted_rejections == (f"every order of run {run_id} was rejected (2)",)
    assert not _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (invocation,) = resume_invocations(conn)
        (released,) = [
            e
            for e in kill_switch_events_for(conn, window.window_id)  # type: ignore[arg-type]
            if e.state == "released"
        ]
    assert invocation.accept_rejections is True and not invocation.accept_broker_fills
    ((resume_id, accepted, known_at),) = _acceptances(journal_settings)
    assert resume_id == invocation.resume_id == outcome.resume_id == released.resume_id
    assert accepted == [_verdict(run_id, 2, 2, all_rejected=True)]
    # Journal-first: the acceptance is on record before the release.
    assert invocation.known_at <= known_at < released.known_at
    assert known_at.utcoffset() == timedelta(0)


def test_with_the_flag_an_over_cap_verdict_is_accepted_with_its_counts(
    journal_settings: Settings,
    fake: SkewedFake,
    fixed_clock: FixedClock,
) -> None:
    capped = RiskConfig(max_rejections_per_run=1)
    window = _open_window(journal_settings, capped)
    at = DAY1 - timedelta(hours=1)
    run_id = _run(journal_settings, window, at, finished=False)
    kept = _order(journal_settings, fake, run_id, "tp-ok", 1.0, at)
    fake.apply("tp-ok", Expire())
    _halted_with_rejections(
        journal_settings, fake, fixed_clock, window, frozen=capped, others=(kept,)
    )

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == RELEASED, outcome.reasons
    ((_, accepted, _),) = _acceptances(journal_settings)
    assert accepted == [_verdict(run_id, 2, 3, all_rejected=False)]


def test_with_the_flag_a_crashed_runs_verdict_is_accepted_once(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    # The resume closes the run `crashed`; its collection and the faulted-run
    # check both find the verdict, and the flag accepts it once.
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)
    for coid in ("tp-c1", "tp-c2"):
        _order(journal_settings, fake, run_id, coid, 1.0, DAY1 - timedelta(hours=1))
        fake.apply(coid, Reject())

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == RELEASED, outcome.reasons
    assert outcome.crashed_runs == (run_id,)
    ((_, accepted, _),) = _acceptances(journal_settings)
    assert accepted == [_verdict(run_id, 2, 2, all_rejected=True)]


def test_with_the_flag_a_reconciliation_mismatch_still_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _halted_with_rejections(journal_settings, fake, fixed_clock, window)
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    fake.simulate_fill("owner-1")  # a position the ledger lacks

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == REFUSED
    assert any("broker_only_position" in r for r in outcome.reasons)
    assert outcome.released_event_id is None
    assert _engaged(journal_settings, window)
    with open_read_only(journal_settings) as conn:
        (row,) = reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]
        states = [e.state for e in kill_switch_events_for(conn, window.window_id)]  # type: ignore[arg-type]
    assert row.status == "mismatch"
    assert "released" not in states
    # The acceptance is on record even though the release never came.
    ((_, accepted, _),) = _acceptances(journal_settings)
    assert accepted == [_verdict(run_id, 2, 2, all_rejected=True)]


def test_with_the_flag_the_lag_bound_still_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _halted_with_rejections(journal_settings, fake, fixed_clock, window)
    fixed_clock.now = DAY1
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == REFUSED
    assert any("tp-lag" in r and "lag bound" in r for r in outcome.reasons)
    assert not any(f"run {run_id}" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    assert _count(journal_settings, "reconciliations") == 1  # _lagging_third_fill's own


def test_with_the_flag_an_engagement_written_after_the_resume_still_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#447: an engagement written at or after the resume row is one the owner did
    not see; the flag accepts verdicts, never that."""
    _halted_with_rejections(journal_settings, fake, fixed_clock, window)

    def reconcile_then_engage(*args: object, **kwargs: object) -> object:
        result = reconcile_now(*args, **kwargs)  # type: ignore[arg-type]
        assert isinstance(
            switch.engage(
                journal_settings,
                fixed_clock,
                window_id=window.window_id,  # type: ignore[arg-type]
                source="drawdown",
                reason="drawdown after the resume row",
            ),
            int,
        )
        return result

    monkeypatch.setattr(resume_module, "reconcile_now", reconcile_then_engage)

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == REFUSED
    assert any("release refused" in r and "after resume" in r for r in outcome.reasons)
    assert outcome.released_event_id is None
    assert _engaged(journal_settings, window)


def test_with_the_flag_a_run_with_a_verdict_and_a_lagging_order_is_still_refused(
    journal_settings: Settings,
    fake: SkewedFake,
    fixed_clock: FixedClock,
) -> None:
    """One halted run carries both a rejection-cap verdict and an order past the
    lag bound: the flag accepts the verdict and the lag still refuses."""
    capped = RiskConfig(max_rejections_per_run=1)
    window = _open_window(journal_settings, capped)
    at = DAY1 - timedelta(hours=1)
    run_id = _run(journal_settings, window, at, finished=False)
    lagging = _order(journal_settings, fake, run_id, "tp-lag", 3.0, at)
    fake.apply("tp-lag", PartialFill(1.0, 100.0))
    fake.apply("tp-lag", PartialFill(1.0, 101.0))
    _halted_with_rejections(
        journal_settings, fake, fixed_clock, window, frozen=capped, others=(lagging,)
    )
    fake.lag_fills(None)
    fake.apply("tp-lag", PartialFill(1.0, 102.0))
    first = reconcile_now(
        journal_settings,
        lambda: open_for_write(journal_settings),
        fake,
        window,
        DAY1.date(),
        fixed_clock,
        lambda: open_for_write(journal_settings),
        frozen=capped,
        as_of=fixed_clock(),
    )
    assert first.lagging_ids == ("tp-lag",)
    fixed_clock.now = DAY2

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == REFUSED
    assert any("tp-lag" in r and "lag bound" in r for r in outcome.reasons)
    assert not any(f"run {run_id} had" in r for r in outcome.reasons)
    assert _engaged(journal_settings, window)
    ((_, accepted, _),) = _acceptances(journal_settings)
    assert accepted == [_verdict(run_id, 2, 3, all_rejected=False)]


def test_with_the_flag_a_collection_verdict_on_a_run_that_ended_ok_still_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#374's verdict on a run the release would not clear is not #451's refusal:
    the flag does not accept it."""
    run_id = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=True)
    for coid in ("tp-r1", "tp-r2"):
        _order(journal_settings, fake, run_id, coid, 1.0, DAY1 - timedelta(hours=1))
        fake.apply(coid, Reject())
    _engage(journal_settings, window, fixed_clock)

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == REFUSED
    assert f"every order of run {run_id} was rejected (2)" in outcome.reasons
    assert outcome.accepted_rejections == ()
    assert _engaged(journal_settings, window)
    assert _count(journal_settings, "reconciliations") == 0
    ((_, accepted, _),) = _acceptances(journal_settings)
    assert accepted == []


def test_the_flag_with_no_verdict_changes_nothing_and_records_nothing_accepted(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    crashed = _run(journal_settings, window, DAY1 - timedelta(hours=1), finished=False)

    outcome = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert outcome.status == RELEASED, outcome.reasons
    assert outcome.crashed_runs == (crashed,)
    assert outcome.accepted_rejections == ()
    with open_read_only(journal_settings) as conn:
        (invocation,) = resume_invocations(conn)
    assert invocation.accept_rejections is True
    ((resume_id, accepted, _),) = _acceptances(journal_settings)
    assert (resume_id, accepted) == (outcome.resume_id, [])


@pytest.mark.parametrize("flag", [1, "yes", None])
def test_a_flag_that_is_not_a_bool_is_refused_before_anything(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
    flag: object,
) -> None:
    with pytest.raises(TypeError, match="accept_rejections"):
        resume(
            journal_settings,
            lambda: open_for_write(journal_settings),
            fake,
            fixed_clock,
            "owner checked",
            False,
            accept_rejections=flag,  # type: ignore[arg-type]
        )
    assert _count(journal_settings, "resume_invocations") == 0


def test_the_flag_without_a_verdict_refuses_what_a_plain_resume_refuses(
    journal_settings: Settings,
    fake: SkewedFake,
    window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _lagging_third_fill(journal_settings, fake, window, fixed_clock, "tp-lag")

    plain = _resume(journal_settings, fake, fixed_clock)
    flagged = _resume(journal_settings, fake, fixed_clock, accept_rejections=True)

    assert plain.status == flagged.status == REFUSED
    assert plain.reasons == flagged.reasons
    assert _engaged(journal_settings, window)

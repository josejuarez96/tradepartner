"""Reconciliation with store explanations (Phase 4 plan T61; spec req 6 and the
"Reconciliation on the fake" acceptance cases that need no run)."""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

import polars as pl
import pytest

from tradepartner.adapters.broker import (
    Account,
    Asset,
    CashActivity,
    OrderRequest,
    Position,
    PositionIntent,
    Side,
)
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import book_view, reconcile_run, switch
from tradepartner.execution import breaks as break_rules
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile import (
    FILLS_LAGGING,
    MISMATCH,
    OK,
    PENDING_UNRESOLVED,
    Explanations,
    SymbolChangeRecord,
)
from tradepartner.execution.reconcile_run import (
    _NEW_YORK,
    NoWindowError,
    _journal_state,
    _ledger,
    command_session,
    explanations_as_of,
    frozen_risk,
    order_symbols,
    reconcile_command,
    reconcile_now,
)
from tradepartner.execution.risk import round_down
from tradepartner.store.asof import live_actions_as_of, prices_as_of
from tradepartner.store.db import insert_row, open_for_write, open_read_only
from tradepartner.store.dividend_reviews import ReviewedGroup
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    adjustments_for,
    append,
    break_events_for,
    fills_for,
    kill_switch_events_for,
    orders_for,
    reconciliations_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


#: A roomy cap, so a single quantity break on the fixture's $250 reference price is
#: not a `break_cap` here; the cap has its own tests below with explicit values.
FROZEN = RiskConfig()
ROOMY = FROZEN.model_copy(update={"max_open_break_share": 0.5})
S = date(2026, 10, 1)  # the conftest clock's session
CUT = session_close(date(2026, 9, 30))  # close(S-1)
BOUGHT_AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)  # before every ex-date tested
PRICE = 100.0  # the fake's fill price (conftest REFERENCE_PRICE)
SPY, MTUM = "SEC_SPY", "SEC_MTUM"  # fixture names listed through 2026
#: The account's own $100,000 funding `JNLC`, dated the day before `open_window`'s
#: New York start date (2026-09-29): inside the base under the fallback (#1641),
#: so a fake-broker reconcile fixture carries it and never mismatches on it.
FUNDING = CashActivity(
    activity_id="20260928000000000::fund0001",
    activity_type="JNLC",
    date=date(2026, 9, 28),
    status="executed",
    net_amount=100_000.0,
)


class BookedFake(FakeBroker):
    """A fake whose positions and cash a test can move the way a broker's
    corporate-action processing would (a split, a removal, a credit), which
    `FakeBroker` itself never does."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.extra_quantity: dict[str, float] = {}
        self.extra_cash = 0.0

    def positions(self) -> dict[str, Position]:
        held = {s: p.quantity for s, p in super().positions().items()}
        for symbol, delta in self.extra_quantity.items():
            held[symbol] = held.get(symbol, 0.0) + delta
        return {s: Position(symbol=s, quantity=q) for s, q in held.items() if q != 0}

    def account(self) -> Account:
        account = super().account()
        return Account(
            account_id=account.account_id,
            cash=account.cash + self.extra_cash,
            buying_power=account.buying_power,
            equity=account.equity,
            as_of=account.as_of,
        )


@pytest.fixture
def fake(fixed_clock: FixedClock) -> BookedFake:
    return BookedFake(
        assets=FakeBroker.active_assets("SPY", "MTUM"),
        clock=fixed_clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PA1",
    )


@pytest.fixture
def flip_fake(fixed_clock: FixedClock) -> BookedFake:
    """A paper broker whose SPY can be sold short between dividend ex-dates."""
    return BookedFake(
        assets={
            "SPY": Asset(
                tradable=True,
                fractionable=True,
                status="active",
                cusip=None,
                shortable=True,
                easy_to_borrow=True,
                marginable=True,
            )
        },
        clock=fixed_clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PA1",
        paper=True,
        activities=[FUNDING],
    )


# --- store and journal helpers -----------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _fact(settings: Settings, table: str, **values: object) -> None:
    with open_for_write(settings) as conn:
        insert_row(conn, table, values)


def _split(settings: Settings, security_id: str, ratio: float, known_at: datetime) -> None:
    _fact(
        settings,
        "corporate_actions",
        security_id=security_id,
        action_type="split",
        ex_date=S,
        ratio_or_amount=ratio,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="action",
    )


def _dividend(
    settings: Settings,
    security_id: str,
    amount: float,
    known_at: datetime,
    ex_date: date = S,
    source_action_id: str = "",
) -> None:
    _fact(
        settings,
        "corporate_actions",
        security_id=security_id,
        action_type="dividend",
        ex_date=ex_date,
        ratio_or_amount=amount,
        source_action_id=source_action_id,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="action",
    )


def _spinoff(
    settings: Settings,
    child: str,
    parent: str,
    ratio: float,
    known_at: datetime,
    ex_date: date = S,
) -> None:
    """A stored `spinoff` corporate action (child in `security_id`, parent in
    `source_action_id`, `ratio` shares received per parent share), the
    `execution.window` convention (#1894, #1896)."""
    _fact(
        settings,
        "corporate_actions",
        security_id=child,
        action_type="spinoff",
        ex_date=ex_date,
        ratio_or_amount=ratio,
        source_action_id=parent,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="action",
    )


def _delisting(settings: Settings, security_id: str, filed_at: datetime) -> None:
    _fact(
        settings,
        "delistings",
        security_id=security_id,
        form="25",
        class_title="Common Stock",
        exchange="NYSE",
        filed_at=filed_at,
        effective_on=filed_at.date() + timedelta(days=10),
        known_at=filed_at,
        ingested_at=filed_at,
        source="edgar",
        provenance="filing",
    )


def _symbol_change(
    settings: Settings,
    security_id: str,
    old: str,
    new: str,
    known_at: datetime,
    source_action_id: str = "rename-1",
) -> None:
    """A stored `symbol_changes` row: the name's ticker went `old` -> `new`, one
    `known_at` for both its process-date close and the read cut."""
    _fact(
        settings,
        "symbol_changes",
        security_id=security_id,
        old_symbol=old,
        new_symbol=new,
        old_cusip=f"OLD-{security_id}",
        new_cusip=f"NEW-{security_id}",
        process_date=known_at.date() - timedelta(days=1),
        source_action_id=source_action_id,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="action",
    )


def _run(settings: Settings, window: PaperWindowRow) -> int:
    (run_id,) = _append(
        settings,
        PaperRunRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=BOUGHT_AT.date(),
            kind="rebalance",
            started_at=BOUGHT_AT,
            invoked_by="scheduler",
            code_version="test",
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    assert run_id is not None
    return run_id


def _order(
    settings: Settings,
    run_id: int,
    client_order_id: str,
    security_id: str,
    symbol: str,
    quantity: float,
    *,
    events: tuple[str, ...] = ("pending", "accepted", "filled"),
    fill: bool = True,
    at: datetime = BOUGHT_AT,
) -> OrderRow:
    """A buy's decision, order row and events, and (with `fill`) its one fill
    at `PRICE`: what the wrapper and a collector leave in the journal."""
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 29),
            security_id=security_id,
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
        client_order_id=client_order_id,
        decision_id=decision_id,
        run_id=run_id,
        session=at.date(),
        attempt=1,
        phase="buy",
        security_id=security_id,
        symbol=symbol,
        side="buy",
        quantity=quantity,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    rows: list[object] = [order]
    rows += [
        OrderEventRow(client_order_id=client_order_id, status=s, known_at=at, ingested_at=at)
        for s in events
    ]
    if fill:
        rows.append(
            FillRow(
                client_order_id=client_order_id,
                filled_at=at,
                quantity=quantity,
                price=PRICE,
                price_implied=False,
                broker_fill_id=f"fill-{client_order_id}",
                source="broker_feed",
                known_at=at,
                ingested_at=at,
            )
        )
    _append(settings, *rows)
    return order


def _hold(
    settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    holdings: dict[tuple[str, str], float],
) -> int:
    """Journal a filled buy per (security, symbol) and fill the same buy at the fake."""
    run_id = _run(settings, window)
    for n, ((security_id, symbol), quantity) in enumerate(sorted(holdings.items()), start=1):
        coid = f"tp-hold-{n}"
        _order(settings, run_id, coid, security_id, symbol, quantity)
        fake.submit(OrderRequest(coid, symbol, Side.BUY, quantity=quantity))
        fake.simulate_fill(coid)
    return run_id


def _sell_spy(settings: Settings, run_id: int, at: datetime) -> None:
    """Journal a filled sale of the ten SPY shares held by `_hold`."""
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=at.date(),
            security_id=SPY,
            side="sell",
            planned_quantity=10.0,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    coid = f"tp-sell-{at.date().isoformat()}"
    _append(
        settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=at.date(),
            attempt=1,
            phase="sell",
            security_id=SPY,
            symbol="SPY",
            side="sell",
            quantity=10.0,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        ),
        *(
            OrderEventRow(client_order_id=coid, status=s, known_at=at, ingested_at=at)
            for s in ("pending", "accepted", "filled")
        ),
        FillRow(
            client_order_id=coid,
            filled_at=at,
            quantity=10.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{coid}",
            source="broker_feed",
            known_at=at,
            ingested_at=at,
        ),
    )


def _filled_flip_trade(
    settings: Settings,
    fake: BookedFake,
    run_id: int,
    at: datetime,
    side: Side,
    intent: PositionIntent,
) -> None:
    """Journal and fill ten SPY shares on one side of a position flip."""
    position_side = (
        "short" if intent in (PositionIntent.SELL_TO_OPEN, PositionIntent.BUY_TO_CLOSE) else "long"
    )
    coid = f"tp-flip-{intent.value}-{at.date()}"
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=at.date(),
            security_id=SPY,
            side=side.value,
            planned_quantity=10.0,
            whole_share=False,
            decision="trade",
            position_side=position_side,
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    _append(
        settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=at.date(),
            attempt=1,
            phase="buy" if side is Side.BUY else "sell",
            security_id=SPY,
            symbol="SPY",
            side=side.value,
            quantity=10.0,
            position_side=position_side,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        ),
        *(
            OrderEventRow(client_order_id=coid, status=status, known_at=at, ingested_at=at)
            for status in ("pending", "accepted", "filled")
        ),
        FillRow(
            client_order_id=coid,
            filled_at=at,
            quantity=10.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{coid}",
            source="broker_feed",
            known_at=at,
            ingested_at=at,
        ),
    )
    fake.submit(OrderRequest(coid, "SPY", side, quantity=10.0, position_intent=intent))
    fake.simulate_fill(coid)


def _reconcile(
    settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    clock: FixedClock,
    *,
    run_id: int | None = None,
    as_of: datetime | None = None,
    session: date = S,
    frozen: RiskConfig = ROOMY,
) -> Any:
    return reconcile_now(
        settings,
        lambda: open_for_write(settings),
        fake,
        window,
        session,
        clock,
        lambda: open_for_write(settings),
        run_id,
        frozen=frozen,
        as_of=clock() if as_of is None else as_of,
    )


_REAL_SIGNED_BOOK = reconcile_run._signed_book  # before the autouse patch below


@pytest.fixture(autouse=True)
def _long_only_book(monkeypatch: pytest.MonkeyPatch) -> None:
    """These windows name no registered hypothesis; read each as `long_only`. The
    `long_short` refusal has its own tests, which patch it the other way."""
    monkeypatch.setattr(reconcile_run, "_signed_book", lambda conn, window: False)


def _rows(settings: Settings, window: PaperWindowRow) -> list[ReconciliationRow]:
    with open_read_only(settings) as conn:
        return reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]


def _adjustments(settings: Settings, window: PaperWindowRow) -> list[AdjustmentRow]:
    with open_read_only(settings) as conn:
        return adjustments_for(conn, window.window_id)  # type: ignore[arg-type]


def test_named_cash_break_is_written_and_halts_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.activities = [CashActivity("name-credit", "MA", S, "executed", 30.0, "SPY")]
    fake.extra_cash = 30.0
    submits_before = sum(call.method == "submit" for call in fake.calls)

    reconcile_command(journal_settings, lambda: open_for_write(journal_settings), fake, fixed_clock)

    (row,) = _rows(journal_settings, frozen_window)
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, frozen_window.window_id or 0)
        switch_events = kill_switch_events_for(conn, frozen_window.window_id or 0)
    assert row.status == "breaks"
    assert [(event.event, event.security_id, event.cash_amount) for event in events] == [
        ("opened", SPY, 30.0)
    ]
    assert switch_events == []  # a `breaks` row engages no switch (#2266 item 7)
    assert sum(call.method == "submit" for call in fake.calls) == submits_before

    _reconcile(journal_settings, fake, frozen_window, fixed_clock)
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, frozen_window.window_id or 0)
    assert [event.event for event in events] == ["opened", "observed"]
    assert events[0].break_id == events[1].break_id


def test_name_cash_break_is_excluded_from_two_bases_and_remains_unseen(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.activities = [CashActivity("open-credit", "MA", S, "executed", 30.0, "SPY")]
    fake.extra_cash = 30.0
    for expected, session in (("opened", S), ("observed", next_session(S))):
        if session != S:
            fixed_clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
        _reconcile(journal_settings, fake, frozen_window, fixed_clock, session=session)
        row = _rows(journal_settings, frozen_window)[-1]
        payload = json.loads(row.mismatches_json or "")
        assert row.status == "breaks"
        assert row.broker_cash == pytest.approx(100_000.0 - 10 * PRICE)
        assert payload["raw_broker_cash"] == pytest.approx(100_030.0 - 10 * PRICE)
        assert "open-credit" not in {a["activity_id"] for a in payload["activities"]}
        with open_read_only(journal_settings) as conn:
            state = _journal_state(conn, frozen_window.window_id or 0, fixed_clock())
            ledger = _ledger(
                state,
                live_actions_as_of(conn, session_close(previous_session(session))),
                frozen_window,
                session,
                FROZEN.reconcile_quantity_tolerance,
            )
            events = break_events_for(conn, frozen_window.window_id or 0)
        assert ledger.cash == pytest.approx(100_000.0 - 10 * PRICE)
        assert events[-1].event == expected


def test_pre_t282b_break_without_cash_is_not_a_ledger_base(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    earlier = fixed_clock() - timedelta(minutes=10)
    _ok_base(journal_settings, open_window, at=earlier, broker_cash=90_000.0)
    broken_at = fixed_clock() - timedelta(minutes=5)
    _append(
        journal_settings,
        ReconciliationRow(
            window_id=open_window.window_id or 0,
            at=broken_at,
            status="breaks",
            broker_cash=None,
            known_at=broken_at,
            ingested_at=broken_at,
        ),
    )
    with open_read_only(journal_settings) as conn:
        state = _journal_state(conn, open_window.window_id or 0, fixed_clock())
        assert state.base(S) is not None and state.base(S).broker_cash == 90_000.0


@pytest.mark.parametrize(
    ("types", "cause"),
    [(("DIVNRA",), "corporate_action"), (("DIVNRA", "MA"), "unattributed")],
)
def test_a_mixed_name_cash_part_takes_the_least_attributed_cause(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
    types: tuple[str, ...],
    cause: str,
) -> None:
    """Item 14: a cause is set only by evidence that can produce the observed
    kind. A dividend row fits `DIVNRA` but not `MA`, so a part holding both is
    not `corporate_action`."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    _dividend(
        journal_settings,
        SPY,
        0.5,
        CUT - timedelta(days=5),
        ex_date=S - timedelta(days=2),
        source_action_id="div-old",
    )
    # A clean reconciliation first, so the held name has no own trading since the
    # last completed one (an own fill forces `unattributed`, item 14).
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"
    later = next_session(S)
    fixed_clock.now = session_close(S) + timedelta(hours=18)
    fake.activities = [
        *fake.activities,
        *(
            CashActivity(f"credit-{i}", kind, later, "executed", 30.0, "SPY")
            for i, kind in enumerate(types)
        ),
    ]
    fake.extra_cash = 30.0 * len(types)

    _reconcile(journal_settings, fake, frozen_window, fixed_clock, session=later)

    with open_read_only(journal_settings) as conn:
        opened = break_events_for(conn, frozen_window.window_id or 0)[0]
    assert opened.cause == cause


def _last_event(settings: Settings, window: PaperWindowRow) -> Any:
    with open_read_only(settings) as conn:
        return break_events_for(conn, window.window_id or 0)[-1]


def test_a_short_quantity_break_records_the_signed_ledger_short(
    journal_settings: Settings,
    flip_fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Greptile P1: a ledger short of 10 against broker -11 is a 1-share break,
    not a break of the whole -11 (the ledger keeps shorts in `short_holdings`)."""
    run_id = _hold(journal_settings, flip_fake, open_window, {(SPY, "SPY"): 10.0})
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
    fixed_clock.now = datetime.combine(flipped, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )

    _reconcile(journal_settings, flip_fake, open_window, fixed_clock, session=flipped)

    event = _last_event(journal_settings, open_window)
    assert (event.ledger_quantity, event.broker_quantity) == (-10.0, -11.0)


def test_a_changed_unknown_symbol_quantity_keeps_the_earlier_finding(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Greptile P1: findings are matched by `finding_key`, so 4 shares then 5
    shares of an unmapped symbol keeps both keys until a true-up."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    for quantity in (4.0, 5.0):
        fake.extra_quantity = {MTUM_WI: quantity}
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    parts = json.loads(_last_event(journal_settings, open_window).parts_json)
    keys = [p["finding_key"] for p in parts if p["kind"] == "unknown_symbol"]
    assert len(keys) == 2 and len(set(keys)) == 2


def _kinds(settings: Settings, window: PaperWindowRow) -> list[str]:
    (row,) = _rows(settings, window)
    return sorted(m["kind"] for m in json.loads(row.mismatches_json or "")["mismatches"])


def test_a_break_reconciliation_halts_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """T282d, #2266 item 7: a name-level residual is a `breaks` row, one `opened`
    event, no raise and no `kill_switch` row."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 10.0

    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"

    assert _kinds(journal_settings, open_window) == ["quantity"]
    with open_read_only(journal_settings) as conn:
        assert [e.event for e in break_events_for(conn, open_window.window_id or 0)] == ["opened"]
        assert kill_switch_events_for(conn, open_window.window_id or 0) == []


def test_breaks_past_the_cap_halt_as_a_book_level_break_cap(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Q3 of PR #2386: `break_cap` is a reconciliation `Mismatch`. The row is the
    book-level `mismatch`, written once; the break events are still recorded."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 10.0
    cap = ROOMY.model_copy(update={"max_open_breaks": 0})

    with pytest.raises(ReconciliationError, match="break_cap"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, frozen=cap)

    (row,) = _rows(journal_settings, open_window)
    assert row.status == "mismatch" and row.broker_cash is None
    assert _kinds(journal_settings, open_window) == ["break_cap", "quantity"]
    with open_read_only(journal_settings) as conn:
        assert [e.event for e in break_events_for(conn, open_window.window_id or 0)] == ["opened"]


def test_one_break_above_the_equity_share_halts_as_break_cap(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Spec item 16: one break above `risk.max_open_break_share` of the equity halts."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 10.0  # $2,500 at the fixture price, over 2% of $100,000

    with pytest.raises(ReconciliationError, match="break_cap"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, frozen=FROZEN)

    assert _kinds(journal_settings, open_window) == ["break_cap", "quantity"]


def test_an_unknown_symbol_break_halts_nothing_but_counts_toward_the_cap(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Q4 of PR #2386, option A: no `security_id`, so no name is restricted and the
    book keeps trading; the break still counts, and at the cap the book halts."""
    fake.extra_quantity = {"SPYX": 5.0}

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == "breaks"
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
        assert [(e.event, e.security_id, e.symbol, e.material) for e in events] == [
            ("opened", None, "SPYX", True)
        ]
        assert break_rules.restricted_names(events) == frozenset()
        assert kill_switch_events_for(conn, open_window.window_id or 0) == []

    cap = ROOMY.model_copy(update={"max_open_breaks": 0})
    with pytest.raises(ReconciliationError, match="break_cap"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, frozen=cap)
    assert _rows(journal_settings, open_window)[-1].status == "mismatch"


def test_a_restricted_name_in_a_long_short_book_halts_with_a_clear_message(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q2 of PR #2386, fallback: `kept_effective_weights` is long-only, so a signed
    book with a restricted name is refused instead of mis-planned (issue filed)."""
    monkeypatch.setattr(reconcile_run, "_signed_book", lambda conn, window: True)
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 10.0

    with pytest.raises(ReconciliationError, match="long_short book with restricted name"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert "restricted_long_short" in _kinds(journal_settings, open_window)


def test_an_unknown_symbol_in_a_long_short_book_restricts_no_name_so_it_trades(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile_run, "_signed_book", lambda conn, window: True)
    fake.extra_quantity = {"SPYX": 5.0}

    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"


def test_the_book_view_reads_a_breaks_row_as_a_match_except_its_breaks(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 10: a `breaks` row is "Matches Alpaca except N open breaks", never the
    "Does not match Alpaca" a `mismatch` shows; it is no checked match either."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 10.0
    _reconcile(journal_settings, fake, frozen_window, fixed_clock)
    (row,) = _rows(journal_settings, frozen_window)

    view = book_view._reconciliation(frozen_window, [row], fixed_clock() + timedelta(hours=1))

    assert row.status == "breaks"
    assert "Matches Alpaca as of" in view.text and "except 1 open break." in view.text
    assert "Does not match" not in view.text
    assert view.matches is False


def test_a_part_with_a_new_cause_is_observed_under_the_opening_cause(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Greptile P1: the journal refuses a cause that rewrites the opening one, so
    a later unattributed part is recorded in `parts_json` and the event keeps the
    opening `corporate_action`; the reconciliation is not rolled back."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    _dividend(
        journal_settings,
        SPY,
        0.5,
        CUT - timedelta(days=5),
        ex_date=S - timedelta(days=2),
        source_action_id="div-old",
    )
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "ok"
    later = next_session(S)
    fixed_clock.now = session_close(S) + timedelta(hours=18)
    fake.activities = [
        *fake.activities,
        CashActivity("c0", "DIVNRA", later, "executed", 30.0, "SPY"),
    ]
    fake.extra_cash = 30.0
    _reconcile(journal_settings, fake, frozen_window, fixed_clock, session=later)

    fake.extra_quantity = {"SPY": -1.0}
    _reconcile(journal_settings, fake, frozen_window, fixed_clock, session=later)

    event = _last_event(journal_settings, frozen_window)
    assert event.event == "observed" and event.cause == "corporate_action"
    assert "unattributed" in event.parts_json


def test_a_failed_closing_event_write_engages_the_switch(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greptile P1: a break that clears on an `ok` read still halts when its
    closing event cannot be written: `ReconciliationError`, switch engaged."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity = {"SPY": -1.0}
    _reconcile(journal_settings, fake, frozen_window, fixed_clock)
    fake.extra_quantity = {}

    def fail_events(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("event writer failed")

    monkeypatch.setattr(reconcile_run, "_write_break_events", fail_events)
    with pytest.raises(ReconciliationError, match="break events could not be written"):
        reconcile_command(
            journal_settings, lambda: open_for_write(journal_settings), fake, fixed_clock
        )

    with open_read_only(journal_settings) as conn:
        switch_events = kill_switch_events_for(conn, frozen_window.window_id or 0)
    assert switch_events[-1].state == "engaged"


def test_failed_break_event_write_engages_switch_and_submits_no_order(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.activities = [CashActivity("name-credit", "MA", S, "executed", 30.0, "SPY")]
    fake.extra_cash = 30.0
    submits_before = sum(call.method == "submit" for call in fake.calls)

    def fail_events(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("event writer failed")

    monkeypatch.setattr(reconcile_run, "_write_break_events", fail_events)
    with pytest.raises(ReconciliationError, match="break events could not be written"):
        reconcile_command(
            journal_settings, lambda: open_for_write(journal_settings), fake, fixed_clock
        )

    with open_read_only(journal_settings) as conn:
        switch_events = kill_switch_events_for(conn, frozen_window.window_id or 0)
        events = break_events_for(conn, frozen_window.window_id or 0)
    assert switch_events[-1].state == "engaged"
    assert events == []
    assert sum(call.method == "submit" for call in fake.calls) == submits_before


def test_pending_order_with_a_name_break_still_raises_after_journaling(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _order(
        journal_settings,
        run_id,
        "tp-still-pending",
        MTUM,
        "MTUM",
        1.0,
        events=("pending",),
        fill=False,
    )
    fake.extra_quantity["SPY"] = 1.0

    with pytest.raises(ReconciliationError, match=r"broker_only_position|quantity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert _rows(journal_settings, open_window)[-1].status == PENDING_UNRESOLVED
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [event.event for event in events] == ["opened"]


def test_due_rebalance_whose_break_cannot_be_written_never_reaches_submit(
    journal_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `breaks` result no longer halts, but one whose events cannot be recorded
    still does: the run fails closed before any order."""
    from execution import test_run_core as run_fixture

    clock = run_fixture.Clock(run_fixture._at(run_fixture.F_0))
    broker = BookedFake(
        assets=FakeBroker.active_assets("SPY"),
        clock=clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        cash=100_000.0,
        account_id="PA1",
    )
    env = run_fixture.Env(journal_settings, clock, broker)
    window = env.window(first=run_fixture.T_0, started=run_fixture._at(run_fixture.T_0, 12))
    env.ingest(run_fixture._at(run_fixture.T_0, 21))
    env.append(
        AdjustmentRow(
            window_id=window.window_id or 0,
            session=run_fixture.T_0,
            kind="spinoff_receipt",
            security_id=SPY,
            quantity=1.0,
            known_at=run_fixture._at(run_fixture.T_0, 13),
            ingested_at=run_fixture._at(run_fixture.T_0, 13),
        )
    )
    broker.extra_quantity["SPY"] = 2.0

    def fail_events(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("event writer failed")

    monkeypatch.setattr(reconcile_run, "_write_break_events", fail_events)

    with pytest.raises(ReconciliationError):
        env.run(run_fixture._at(run_fixture.F_0))

    assert env.engaged()
    assert "submit" not in env.broker_methods()
    assert env.query("SELECT kind FROM paper_runs") == [("rebalance",)]
    assert env.query("SELECT status FROM paper_run_results") == [("halted",)]
    assert env.query("SELECT status FROM reconciliations") == []


def test_two_store_copies_write_identical_break_events(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    tmp_path: Path,
) -> None:
    copy_path = tmp_path / "copy.duckdb"
    shutil.copyfile(journal_settings.store.path, copy_path)
    copy_settings = Settings(_env_file=None, store={"path": str(copy_path)})
    fake.extra_quantity["SPY"] = 10.0

    for settings in (journal_settings, copy_settings):
        _reconcile(settings, fake, open_window, fixed_clock)

    events = []
    for settings in (journal_settings, copy_settings):
        with open_read_only(settings) as conn:
            events.append(break_events_for(conn, open_window.window_id or 0))
    assert events[0] == events[1]
    assert events[0][0].break_id == f"{open_window.window_id}:{SPY}:1"


def test_next_session_merger_explains_an_open_quantity_break(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0})
    fake.extra_quantity["MTUM"] = -10.0
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _rows(journal_settings, open_window)[-1].status == "breaks"

    known = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
    _fact(
        journal_settings,
        "mergers",
        security_id=MTUM,
        kind="cash",
        acquirer_security_id=None,
        acquiree_symbol="MTUM",
        acquiree_cusip="SYNTH-MTUM",
        acquirer_symbol="",
        acquirer_cusip="",
        share_ratio=None,
        cash_per_share=20.0,
        effective_date=S,
        process_date=S,
        payable_date=S,
        source_action_id="merger-1",
        known_at=known,
        ingested_at=known,
        source="alpaca",
        provenance="action",
    )
    following = next_session(S)
    fixed_clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    fake.extra_cash = 200.0
    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=following)

    assert result.status == OK
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [event.event for event in events] == ["opened", "auto_explained"]
    assert any(
        a.kind == "corporate_action_cash" for a in _adjustments(journal_settings, open_window)
    )


def test_merger_closure_cites_only_its_own_adjustment(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0, (SPY, "SPY"): 10.0})
    fake.extra_quantity["MTUM"] = -10.0
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    known = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
    _fact(
        journal_settings,
        "mergers",
        security_id=MTUM,
        kind="cash",
        acquirer_security_id=None,
        acquiree_symbol="MTUM",
        acquiree_cusip="SYNTH-MTUM",
        acquirer_symbol="",
        acquirer_cusip="",
        share_ratio=None,
        cash_per_share=20.0,
        effective_date=S,
        process_date=S,
        payable_date=S,
        source_action_id="merger-cited",
        known_at=known,
        ingested_at=known,
        source="alpaca",
        provenance="action",
    )
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    fake.activities = [CashActivity("div-spy", "DIV", S, "executed", 5.0, "SPY")]
    fake.extra_cash = 205.0
    fixed_clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=next_session(S))
    assert result.status == OK
    with open_read_only(journal_settings) as conn:
        event = break_events_for(conn, open_window.window_id or 0)[-1]
    adjustments = _adjustments(journal_settings, open_window)
    merger_id = next(a.adjustment_id for a in adjustments if a.kind == "corporate_action_cash")
    assert event.event == "auto_explained"
    assert json.loads(event.adjustment_ids_json or "[]") == [merger_id]


def test_observed_break_retains_finding_parts_and_structured_keys(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 1.0
    original = reconcile_run._explanations
    finding = {"active": True}

    def with_findings(*args: Any, **kwargs: Any) -> Explanations:
        result = original(*args, **kwargs)
        if not finding["active"]:
            return result
        return replace(
            result,
            merger_conflicts={
                SPY: "merger ids ['deal-b', 'deal-a'] for SEC_SPY effective 2026-10-01"
            },
            dividend_findings={
                SPY: "2 live dividend records of 0.5 with ex-date 2026-10-01 "
                "(div-a known 2026-09-30) that the reviewed list does not explain"
            },
        )

    monkeypatch.setattr(reconcile_run, "_explanations", with_findings)
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    with open_read_only(journal_settings) as conn:
        opened = break_events_for(conn, open_window.window_id or 0)[-1]
    keys = {part["kind"]: part["finding_key"] for part in json.loads(opened.parts_json)}
    assert keys["merger_conflict"] == '["merger_conflict", ["deal-a", "deal-b"]]'
    assert keys["unexplained_dividend_group"] == (
        '["unexplained_dividend_group", "SEC_SPY", ["2026-10-01"]]'
    )

    finding["active"] = False
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    fake.extra_quantity["SPY"] = 0.0
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _rows(journal_settings, open_window)[-1].status == "breaks"
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [event.event for event in events] == ["opened", "observed", "observed"]
    assert {part["kind"] for part in json.loads(events[-1].parts_json)} == {
        "merger_conflict",
        "unexplained_dividend_group",
    }

    _order(
        journal_settings,
        run_id,
        "tp-finding-pending",
        MTUM,
        "MTUM",
        1.0,
        events=("pending",),
        fill=False,
    )
    with pytest.raises(ReconciliationError, match="open finding"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _rows(journal_settings, open_window)[-1].status == PENDING_UNRESOLVED


def test_dividend_due_beside_a_break_books_once_after_the_break_clears(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0, (MTUM, "MTUM"): 10.0})
    _dividend(journal_settings, MTUM, 0.5, CUT - timedelta(days=1))
    fake.activities = [CashActivity("div-mtum", "DIV", S, "executed", 5.0, "MTUM")]
    fake.extra_cash = 5.0
    fake.extra_quantity["SPY"] = 1.0

    _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _rows(journal_settings, open_window)[-1].status == "breaks"

    fake.extra_quantity["SPY"] = 0.0
    fixed_clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=next_session(S))
    assert result.status == OK
    assert [(a.kind, a.cash) for a in _adjustments(journal_settings, open_window)] == [
        ("dividend_cash", pytest.approx(5.0))
    ]


def test_retained_finding_keeps_a_cash_base_and_books_a_separate_dividend(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0, (MTUM, "MTUM"): 10.0})
    original = reconcile_run._explanations
    finding = {"active": True}

    def with_finding(*args: Any, **kwargs: Any) -> Explanations:
        offered = original(*args, **kwargs)
        if finding["active"]:
            return replace(
                offered,
                merger_conflicts={SPY: "merger ids ['deal-b', 'deal-a'] for SEC_SPY"},
            )
        return offered

    monkeypatch.setattr(reconcile_run, "_explanations", with_finding)
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    finding["active"] = False
    _dividend(journal_settings, MTUM, 0.5, CUT - timedelta(days=1))
    fake.activities = [CashActivity("div-mtum", "DIV", S, "executed", 5.0, "MTUM")]
    fake.extra_cash = 5.0
    fixed_clock.advance(minutes=5)
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    row = _rows(journal_settings, open_window)[-1]
    assert row.status == "breaks"
    assert row.broker_cash == pytest.approx(100_000.0 - 20 * PRICE + 5.0)
    assert [(a.kind, a.cash) for a in _adjustments(journal_settings, open_window)] == [
        ("dividend_cash", pytest.approx(5.0))
    ]
    with open_read_only(journal_settings) as conn:
        assert [event.event for event in break_events_for(conn, open_window.window_id or 0)] == [
            "opened",
            "observed",
        ]


def test_explained_name_closes_while_another_name_still_breaks(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0, (SPY, "SPY"): 10.0})
    fake.extra_quantity.update({"MTUM": -10.0, "SPY": 1.0})
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    known = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
    _fact(
        journal_settings,
        "mergers",
        security_id=MTUM,
        kind="cash",
        acquirer_security_id=None,
        acquiree_symbol="MTUM",
        acquiree_cusip="SYNTH-MTUM",
        acquirer_symbol="",
        acquirer_cusip="",
        share_ratio=None,
        cash_per_share=20.0,
        effective_date=S,
        process_date=S,
        payable_date=S,
        source_action_id="merger-cited",
        known_at=known,
        ingested_at=known,
        source="alpaca",
        provenance="action",
    )
    fake.extra_cash = 200.0
    fixed_clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    _reconcile(journal_settings, fake, open_window, fixed_clock, session=next_session(S))
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [(event.security_id, event.event) for event in events[-2:]] == [
        (MTUM, "auto_explained"),
        (SPY, "observed"),
    ]
    assert [(a.kind, a.cash) for a in _adjustments(journal_settings, open_window)] == [
        ("corporate_action_cash", pytest.approx(200.0))
    ]


def test_book_fault_keeps_an_open_break_observed_until_safe_closure(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = -10.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"
    fake.extra_quantity["SPY"] = 0.0
    fake.extra_cash = 10.0
    with pytest.raises(ReconciliationError):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [event.event for event in events] == ["opened", "observed"]


def test_old_name_residual_opens_a_break_without_rewriting_its_row(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    earlier = fixed_clock() - timedelta(minutes=1)
    old = ReconciliationRow(
        window_id=open_window.window_id or 0,
        at=earlier,
        status="mismatch",
        broker_cash=None,
        mismatches_json='{"mismatches": [{"kind": "quantity"}]}',
        known_at=earlier,
        ingested_at=earlier,
    )
    _append(journal_settings, old)
    before = _rows(journal_settings, open_window)[0]
    fake.extra_quantity["SPY"] = 1.0

    _reconcile(journal_settings, fake, open_window, fixed_clock)

    rows = _rows(journal_settings, open_window)
    assert rows[0] == before
    assert rows[-1].status == "breaks"
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, open_window.window_id or 0)
    assert [event.event for event in events] == ["opened"]


def _reference(settings: Settings, security_id: str) -> float:
    with open_read_only(settings) as conn:
        bars = prices_as_of(conn, CUT, [security_id]).sort("session")
    return float(bars["close"][-1])


# --- explanations: only rows known at close(S-1) ----------------------------


def test_explanations_read_only_rows_known_at_close_of_the_previous_session(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0, (MTUM, "MTUM"): 4.0})
    before, after = CUT - timedelta(minutes=1), CUT + timedelta(minutes=1)
    _dividend(journal_settings, SPY, 0.5, before)
    _dividend(journal_settings, MTUM, 0.7, after)
    _dividend(journal_settings, SPY, 9.0, before, ex_date=date(2026, 10, 2))  # ex after S
    _delisting(journal_settings, MTUM, before)
    _delisting(journal_settings, SPY, after)

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=CUT,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.ended == frozenset({MTUM})
    assert found.dividends == pytest.approx({SPY: 5.0})
    assert found.spinoffs == {}
    assert found.symbols == {SPY: "SPY", MTUM: "MTUM"}
    assert found.reference_prices == {
        SPY: _reference(journal_settings, SPY),
        MTUM: _reference(journal_settings, MTUM),
    }


# --- spin-off children in an open window (#1896) -----------------------------

#: The ex-date: its session-before is the session the parent was bought on
#: (`BOUGHT_AT`), so the ledger held it before the ex-date.
EX = previous_session(S)
#: The adapter's earliest stamp for `EX` (the date-only rule on a process date of
#: `EX`): the close of the session after it, as the fixture's own spin-off row
#: carries. It is `close(S)`, after `S`'s own cutoff, so a row for `EX` is first
#: knowable for the next session's reconcile.
STAMP = session_close(next_session(EX))
S2 = next_session(S)


def test_explanations_offer_a_spinoff_of_a_held_parent(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """#1896: a stored `spinoff` known at close(S2-1) whose parent the ledger held
    on the session before the ex-date is offered, keyed by child to
    `(parent, ratio)` (the `execution.window` convention)."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S2,
            as_of=STAMP,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.spinoffs == {MTUM: (SPY, 0.4)}


def test_explanations_leave_a_spinoff_of_a_parent_not_held_out(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """#1896: the ledger holds no `SPY`, so its stored spin-off is not offered and
    a broker-booked child stays a mismatch (never a guess)."""
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S2,
            as_of=STAMP,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.spinoffs == {}


def test_a_spinoff_whose_row_lands_after_its_ex_date_is_still_offered(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """#1896: a spin-off's row is stamped the close after Alpaca's date-only
    `process_date`, so an `ok` reconciliation can sit on the ex-date before the
    row lands. The read is bounded by the cutoff, not the base session, so the
    spin-off is still offered and the child is not lost for good."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _ok_base(journal_settings, open_window, at=session_close(EX), broker_cash=99_000.0)
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S2,
            as_of=STAMP,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.spinoffs == {MTUM: (SPY, 0.4)}


def test_a_broker_booked_spinoff_child_journals_a_spinoff_receipt(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1896: 10 `SPY` held, a 0.4 `SPY` -> `MTUM` spin-off known at close(S2-1),
    and the broker's 4.0 `MTUM` child journals the `spinoff_receipt` the ledger
    then holds (cash unchanged: a receipt is shares, not cash)."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=1)  # the next session, when the row is known

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert result.status == OK
    assert result.broker_cash == pytest.approx(100_000.0 - 10.0 * PRICE)
    (adjustment,) = _adjustments(journal_settings, open_window)
    assert (adjustment.kind, adjustment.security_id, adjustment.quantity, adjustment.cash) == (
        "spinoff_receipt",
        MTUM,
        4.0,
        None,
    )


def test_a_wrong_spinoff_ratio_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1896: 10 `SPY` at a stored 0.25 ratio is 2.5 children, but the broker
    books 4.0: the ratio is wrong, so the child is a mismatch, never accepted."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.25, STAMP, ex_date=EX)
    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_spinoff_child_of_a_parent_not_held_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1896: the ledger never held `SPY`, so its spin-off explains nothing and
    the broker's `MTUM` child halts as a `broker_only_position`."""
    fake.activities = [FUNDING]
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_spinoff_of_a_parent_moved_since_the_ex_date_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1896: the parent must be held, unchanged, on the session before the
    ex-date and at the stated session, so `compare`'s `parent held x ratio` bound
    uses the basis the distribution was earned in; a parent bought up since then
    is not offered and the broker's child halts."""
    fake.activities = [FUNDING]
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 5.0})
    later = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)  # after the ex-date's session-before
    _order(journal_settings, run_id, "tp-more", SPY, "SPY", 5.0, at=later)
    fake.submit(OrderRequest("tp-more", "SPY", Side.BUY, quantity=5.0))
    fake.simulate_fill("tp-more")
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_spinoff_known_only_after_the_cutoff_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1896 (no look-ahead): a `spinoff` known only at the next session's close
    is invisible at S2's cutoff, so the broker's child stays a mismatch."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, session_close(S2), ex_date=EX)
    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


#: The chained successor of `SPY` in the #2127 cases: the id the ledger holds
#: once the 1:1 chain (`SPY` -> `SUCC`) is known at the cutoff.
SUCC = "SEC_SUCC"


def _chain(settings: Settings, predecessor: str, successor: str, known_at: datetime) -> None:
    """A live `successor_chains` row (#821): `predecessor` -> `successor` known at
    `known_at`."""
    _fact(
        settings,
        "successor_chains",
        predecessor_id=predecessor,
        successor_id=successor,
        evidence_accession="SEC-24-000001",
        evidence_form="8-K12B",
        retracted=False,
        withdrawn_known_at=None,
        known_at=known_at,
        ingested_at=known_at,
        source="edgar",
        provenance="filing",
    )


def test_a_spinoff_of_a_chained_parent_resolves_to_the_held_successor(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """#2127: the ledger holds the chained successor `SUCC`, the id the chain's
    re-keyed read carries the predecessor `SPY`'s history under. A `spinoff` row
    whose parent is the predecessor `SPY` re-keys its parent to `SUCC`, so the
    child is offered as `(SUCC, ratio)`; without the re-key the held parent is
    missed and the child would halt."""
    _hold(journal_settings, fake, open_window, {(SUCC, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    _chain(journal_settings, SPY, SUCC, CUT)

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S2,
            as_of=STAMP,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.spinoffs == {MTUM: (SUCC, 0.4)}


def test_a_spinoff_of_a_chain_not_yet_known_keeps_the_stored_parent(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """No look-ahead (#2127): with the `SPY` -> `SUCC` chain known only after the
    cutoff (`close(S2)`), the read sees two securities and the spin-off's parent
    stays `SPY`; the ledger holds `SUCC`, so the child explains nothing and is
    left a mismatch, never re-keyed ahead of the chain."""
    _hold(journal_settings, fake, open_window, {(SUCC, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    _chain(journal_settings, SPY, SUCC, session_close(S2))

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S2,
            as_of=STAMP,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )

    assert found.spinoffs == {}


# --- a when-issued spin-off child at the broker (#2112) ------------------------

#: The broker form of a when-issued child (Alpaca's `spin_offs.new_symbol`).
MTUM_WI = "MTUM.WI"
S3 = next_session(S2)  # a Monday: S2 is Friday 2026-10-02


def test_explanations_map_an_offered_child_to_its_when_issued_broker_symbol(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2112: the offered child `MTUM` maps to `MTUM.WI` only while the broker
    holds the when-issued symbol and not the regular-way one."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)

    def symbols(broker: tuple[str, ...]) -> dict[str, str]:
        with open_read_only(journal_settings) as conn:
            return dict(
                explanations_as_of(
                    conn,
                    open_window,
                    S2,
                    as_of=fixed_clock.advance(),
                    settings=journal_settings,
                    broker_symbols=broker,
                    quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
                ).symbols
            )

    assert symbols(("SPY", MTUM_WI)) == {SPY: "SPY", MTUM: MTUM_WI}
    assert symbols(("SPY", "MTUM", MTUM_WI)) == {SPY: "SPY", MTUM: "MTUM"}
    assert symbols(("SPY",)) == {SPY: "SPY", MTUM: "MTUM"}


def test_a_when_issued_spinoff_child_is_allowed_then_journaled_once_regular_way(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2112: 10 `SPY` held and a 0.4 `MTUM`/`SPY` row known at close(S2-1), so
    10 x 0.4 = 4.0 children are due. While the broker holds them as `MTUM.WI`
    the reconciliation is `ok`, journals nothing and lists the 4.0 as allowed
    (no ledger position on a symbol no order can trade). Once the broker holds
    4.0 `MTUM` regular-way, one `spinoff_receipt` of 4.0 is journaled, no cash."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    fake.extra_quantity = {MTUM_WI: 4.0}
    fixed_clock.advance(days=1)

    first = _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert first.status == OK
    assert _adjustments(journal_settings, open_window) == []
    listed = json.loads(_rows(journal_settings, open_window)[-1].mismatches_json or "")
    assert listed["allowed"] == [
        {"reason": "when-issued spin-off child", "difference": 4.0, "security_id": MTUM}
    ]
    assert listed["explained"] == []

    fake.extra_quantity = {"MTUM": 4.0}
    fixed_clock.advance(days=3)  # Monday

    second = _reconcile(journal_settings, fake, open_window, fixed_clock, session=S3)

    assert second.status == OK
    assert second.broker_cash == pytest.approx(100_000.0 - 10.0 * PRICE)
    (adjustment,) = _adjustments(journal_settings, open_window)
    assert (adjustment.kind, adjustment.security_id, adjustment.quantity, adjustment.cash) == (
        "spinoff_receipt",
        MTUM,
        4.0,
        None,
    )


def test_a_when_issued_child_known_only_after_the_cutoff_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2112 (no look-ahead): a `spinoff` row known only at close(S2) is not
    read at S2's cutoff, close(S2-1), so nothing offers `MTUM` and the broker's
    `MTUM.WI` maps to no name: `unknown_symbol`, nothing journaled."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.4, session_close(S2), ex_date=EX)
    fake.extra_quantity = {MTUM_WI: 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_when_issued_child_of_a_parent_not_held_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2112: a row whose parent the ledger never held is not offered, so the
    broker's `MTUM.WI` maps to no name and halts as `unknown_symbol`."""
    fake.activities = [FUNDING]
    _spinoff(journal_settings, MTUM, SPY, 0.4, STAMP, ex_date=EX)
    fake.extra_quantity = {MTUM_WI: 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_when_issued_child_at_a_wrong_ratio_still_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2112: 10 `SPY` at a stored 0.25 is 2.5 children due, the broker's
    `MTUM.WI` is 4.0: the symbol maps, the quantity is outside the due band, so
    it halts as `broker_only_position`, never allowed."""
    fake.activities = [FUNDING]
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _spinoff(journal_settings, MTUM, SPY, 0.25, STAMP, ex_date=EX)
    fake.extra_quantity = {MTUM_WI: 4.0}
    fixed_clock.advance(days=1)

    _reconcile(journal_settings, fake, open_window, fixed_clock, session=S2)

    assert _adjustments(journal_settings, open_window) == []


def test_a_broker_symbol_the_journal_never_held_maps_through_its_listing(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 1.0})
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=CUT,
            settings=journal_settings,
            broker_symbols=["SPY", "MTUM", "NOPE"],
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.symbols == {SPY: "SPY", MTUM: "MTUM"}


def test_explanations_read_a_listing_with_no_valid_from_as_the_shared_rule_does(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listing row with no `valid_from` is read by `planning.current_listings`'
    rule (#705): it never beats a dated row, so the name keeps its dated
    ticker instead of the read raising `TypeError`."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 1.0})
    real = reconcile_run.listing_ends_as_of

    def with_undated_row(*args: Any, **kwargs: Any) -> pl.DataFrame:
        listings = real(*args, **kwargs)
        undated = listings.filter(pl.col("security_id") == SPY).with_columns(
            pl.lit("SPY_UNDATED").alias("ticker"), pl.lit(None, dtype=pl.Date).alias("valid_from")
        )
        return pl.concat([undated, listings])

    monkeypatch.setattr(reconcile_run, "listing_ends_as_of", with_undated_row)
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=CUT,
            settings=journal_settings,
            broker_symbols=["SPY"],
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.symbols == {SPY: "SPY"}


def test_a_ticker_change_known_at_close_s_minus_1_maps_the_broker_symbol(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761: a held name whose ticker changed (a stored `symbol_changes` row
    known at close(S-1)) reconciles -- the broker's new-symbol position matches
    the ledger's old-symbol one, and the `reconciliations` row records it."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 10.0}  # the broker renamed the position

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert result.symbol_changes == (SymbolChangeRecord(SPY, "SPY", "SPYX"),)
    assert _adjustments(journal_settings, open_window) == []  # nothing moved
    row = _rows(journal_settings, open_window)[-1]
    assert json.loads(row.mismatches_json or "")["symbol_changes"] == [
        {"security_id": SPY, "old_symbol": "SPY", "new_symbol": "SPYX"}
    ]


def test_a_broker_symbol_with_no_stored_change_is_a_mismatch(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761: the broker's new symbol with no stored change is the same unknown
    symbol and lone ledger position as before, and records no mapping."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 10.0}

    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"

    (row,) = _rows(journal_settings, open_window)
    listed = json.loads(row.mismatches_json or "")
    assert sorted(m["kind"] for m in listed["mismatches"]) == [
        "ledger_only_position",
        "unknown_symbol",
    ]
    assert listed["symbol_changes"] == []


def test_a_symbol_change_known_only_after_close_s_minus_1_is_not_applied(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761 (no look-ahead): the read is at close(S-1), so a change stamped a
    minute after it is invisible and the new broker symbol is a mismatch."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT + timedelta(minutes=1))
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 10.0}

    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=CUT,
            settings=journal_settings,
            broker_symbols=["SPY", "SPYX"],
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.symbols == {SPY: "SPY"}
    assert found.symbol_changes == {}

    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"
    (row,) = _rows(journal_settings, open_window)
    assert json.loads(row.mismatches_json or "")["symbol_changes"] == []


def test_a_quantity_mismatch_under_the_new_symbol_is_a_mismatch(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761: the change maps the symbol but not a size -- a broker quantity that
    differs under the new symbol is compare's `quantity` mismatch as ever."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 9.0}

    _reconcile(journal_settings, fake, open_window, fixed_clock)

    (row,) = _rows(journal_settings, open_window)
    listed = json.loads(row.mismatches_json or "")
    assert [m["kind"] for m in listed["mismatches"]] == ["quantity"]
    assert listed["symbol_changes"] == [
        {"security_id": SPY, "old_symbol": "SPY", "new_symbol": "SPYX"}
    ]
    assert _adjustments(journal_settings, open_window) == []


def test_a_ticker_change_is_not_recorded_when_the_broker_holds_both_symbols(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761 (review fix 1): the broker holding both the old and the new symbol
    leaves the name on its listing's symbol -- the unclaimed-symbol rule puts it
    back on the old one -- so the leftover new-symbol position is an unknown
    symbol and the row records no applied change (the map did not keep it)."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    fake.extra_quantity = {"SPYX": 10.0}  # the broker holds the old 10 and a new 10

    _reconcile(journal_settings, fake, open_window, fixed_clock)

    (row,) = _rows(journal_settings, open_window)
    listed = json.loads(row.mismatches_json or "")
    assert [m["kind"] for m in listed["mismatches"]] == ["unknown_symbol"]
    assert listed["symbol_changes"] == []


def test_two_candidate_ticker_changes_for_a_name_are_a_mismatch(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1761: two stored new symbols for one held name are two candidates, so the
    position stays a mismatch (fail closed, never a guess)."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1), "r1")
    _symbol_change(journal_settings, SPY, "SPY", "SPYZ", CUT - timedelta(minutes=1), "r2")
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 10.0}

    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == "breaks"

    (row,) = _rows(journal_settings, open_window)
    listed = json.loads(row.mismatches_json or "")
    assert sorted(m["kind"] for m in listed["mismatches"]) == [
        "ledger_only_position",
        "unknown_symbol",
    ]
    assert listed["symbol_changes"] == []


def _order_symbols(settings: Settings, window: PaperWindowRow, *names: str) -> Any:
    assert window.window_id is not None
    with open_read_only(settings) as conn:
        return order_symbols(conn, S, names, settings, window.window_id)


def test_the_order_path_reads_the_symbol_reconcile_mapped_after_a_ticker_change(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2185: the listings still show `SPY`, a change to `SPYX` is known at
    close(S-1) and the broker holds `SPYX`: once the reconciliation on S applied
    it, the order path's map sends `SPY` under `SPYX`, the one reconcile mapped."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    fake.extra_quantity = {"SPY": -10.0, "SPYX": 10.0}

    before = _order_symbols(journal_settings, open_window, SPY, MTUM)
    result = _reconcile(journal_settings, fake, open_window, fixed_clock)
    after = _order_symbols(journal_settings, open_window, SPY, MTUM)

    assert result.symbol_changes == (SymbolChangeRecord(SPY, "SPY", "SPYX"),)
    assert after.symbols == {SPY: "SPYX", MTUM: "MTUM"}
    assert after.ambiguous == {}
    # Before any reconciliation on S applied it, the name is never sent under SPY.
    assert before.symbols == {MTUM: "MTUM"}
    assert "SPY -> SPYX" in before.ambiguous[SPY]


def test_two_ticker_changes_in_force_leave_the_name_out_of_the_order_map(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2185: two candidate new symbols are ambiguous: dropped, with a note naming
    both, never sent under the old symbol."""
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1), "r1")
    _symbol_change(journal_settings, SPY, "SPY", "SPYZ", CUT - timedelta(minutes=1), "r2")

    found = _order_symbols(journal_settings, open_window, SPY)

    assert found.symbols == {}
    assert "SPY -> SPYX/SPYZ" in found.ambiguous[SPY]


def test_a_symbol_claimed_by_a_renamed_name_and_another_leaves_both_out(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2185: the old ticker reused by another issuer (MTUM's listing re-tickered
    to `SPY`) while SPY's change is not applied: both names claim `SPY`, so the
    order map drops both, each with a note, instead of halting the run."""
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    with open_for_write(journal_settings) as conn:
        conn.execute("UPDATE listings SET ticker = 'SPY' WHERE security_id = ?", [MTUM])

    found = _order_symbols(journal_settings, open_window, SPY, MTUM)

    assert found.symbols == {}
    assert sorted(found.ambiguous) == [MTUM, SPY]
    assert "claim SPY" in found.ambiguous[MTUM]


@pytest.mark.parametrize(
    ("at", "new"), [(CUT - timedelta(hours=1), "SPYX"), (None, "SPYZ")], ids=["s_minus_1", "other"]
)
def test_only_the_same_change_recorded_on_s_applies_in_the_order_map(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    at: datetime | None,
    new: str,
) -> None:
    """#2185: a reconciliation that recorded the change on S-1 only, or one on S
    that recorded another new symbol, is no evidence for S: the name stays out."""
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT - timedelta(minutes=1))
    stamp = fixed_clock() if at is None else at
    change = {"security_id": SPY, "old_symbol": "SPY", "new_symbol": new}
    _append(
        journal_settings,
        ReconciliationRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            at=stamp,
            status="ok",
            broker_cash=PRICE,
            mismatches_json=json.dumps({"symbol_changes": [change]}),
            known_at=stamp,
            ingested_at=stamp,
        ),
    )

    found = _order_symbols(journal_settings, open_window, SPY)

    assert found.symbols == {}
    assert SPY in found.ambiguous


def test_the_order_map_reads_no_ticker_change_known_after_close_s_minus_1(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2185 (no look-ahead): a change stamped a minute after close(S-1) is not
    read, so the name keeps its listing's symbol and is not ambiguous."""
    _symbol_change(journal_settings, SPY, "SPY", "SPYX", CUT + timedelta(minutes=1))

    found = _order_symbols(journal_settings, open_window, SPY)

    assert found.symbols == {SPY: "SPY"}
    assert found.ambiguous == {}


def test_a_split_known_at_close_of_the_previous_session_explains_the_new_quantity(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _split(journal_settings, SPY, 2.0, CUT - timedelta(minutes=1))
    fake.extra_quantity["SPY"] = 10.0  # the broker booked the 2:1 split

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert result.adjustments == ()  # the ledger applies a split itself
    assert _adjustments(journal_settings, open_window) == []


def test_a_split_known_only_after_close_of_the_previous_session_is_a_mismatch(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    _split(journal_settings, SPY, 2.0, CUT + timedelta(minutes=1))
    fake.extra_quantity["SPY"] = 10.0

    _reconcile(journal_settings, fake, open_window, fixed_clock)

    row = _rows(journal_settings, open_window)[-1]
    assert _rows(journal_settings, open_window)[0].status == OK
    assert row.status == "breaks"
    assert row.broker_cash == pytest.approx(100_000.0 - 10 * PRICE)
    assert [m["kind"] for m in json.loads(row.mismatches_json or "")["mismatches"]] == ["quantity"]
    with open_read_only(journal_settings) as conn:
        (event,) = break_events_for(conn, open_window.window_id or 0)
    assert event.cause == "unattributed"


# --- each explanation kind writes its adjustment -----------------------------


def test_an_ended_listing_writes_corporate_action_cash_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0})
    _delisting(journal_settings, MTUM, CUT - timedelta(days=1))
    proceeds = 0.5 * 10 * _reference(journal_settings, MTUM)
    fake.extra_quantity["MTUM"] = -10.0  # the broker removed the position ...
    fake.extra_cash = proceeds  # ... and paid the merger cash

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)

    assert result.status == OK
    (row,) = _rows(journal_settings, open_window)
    assert row.broker_cash == pytest.approx(100_000.0 - 10 * PRICE + proceeds)
    (adjustment,) = _adjustments(journal_settings, open_window)
    assert (adjustment.kind, adjustment.security_id, adjustment.session) == (
        "corporate_action_cash",
        MTUM,
        S,
    )
    assert adjustment.quantity == pytest.approx(-10.0)
    assert adjustment.cash == pytest.approx(proceeds)
    assert adjustment.run_id == run_id
    assert adjustment.known_at == row.known_at
    assert json.loads(adjustment.explanation_json or "")["reconciliation_id"] == (
        row.reconciliation_id
    )

    fixed_clock.advance(minutes=5)
    again = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert again.status == OK and again.adjustments == ()
    assert len(_adjustments(journal_settings, open_window)) == 1


def test_the_written_rows_carry_the_windows_book(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """ADR 0015 seam 1 (T133): `reconcile_now` writes its `reconciliations` row
    and every `adjustments` row with the window's book, never the DDL default."""
    fx_window = replace(open_window, book_id="fx")
    run_id = _hold(journal_settings, fake, fx_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    fake.extra_cash = 5.0

    result = _reconcile(journal_settings, fake, fx_window, fixed_clock, run_id=run_id)

    assert result.status == OK
    (row,) = _rows(journal_settings, fx_window)
    (adjustment,) = _adjustments(journal_settings, fx_window)
    assert row.book_id == "fx"
    assert adjustment.book_id == "fx"


def test_a_credited_dividend_writes_dividend_cash_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    fake.extra_cash = 5.0

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    (adjustment,) = _adjustments(journal_settings, open_window)
    assert (adjustment.kind, adjustment.security_id, adjustment.quantity) == (
        "dividend_cash",
        SPY,
        None,
    )
    assert adjustment.cash == pytest.approx(5.0)

    fixed_clock.advance(minutes=5)
    again = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert again.status == OK and again.adjustments == ()
    assert len(_adjustments(journal_settings, open_window)) == 1


def test_a_dividend_credited_on_its_pay_date_after_an_ok_reconcile_is_explained(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#2183: an ok cash base after the ex-date must not hide the later credit."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK

    pay_date = S
    for _ in range(10):
        pay_date = next_session(pay_date)
    fixed_clock.now = datetime.combine(pay_date, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    fake.activities = [CashActivity("div-pay", "DIV", pay_date, "executed", 5.0, "SPY")]
    fake.extra_cash = 5.0

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay_date)
    assert result.status == OK
    assert [(a.kind, a.cash) for a in _adjustments(journal_settings, open_window)] == [
        ("dividend_cash", pytest.approx(5.0))
    ]

    fixed_clock.advance(minutes=5)
    assert (
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay_date).status == OK
    )
    assert len(_adjustments(journal_settings, open_window)) == 1


@pytest.mark.parametrize(
    "activity_type", ["DIVNRA", "DIVFEE", "DIVTW", "DIVROC", "DIVCGL", "DIVCGS", "DIVTXEX"]
)
def test_dividend_fee_and_withholding_still_halt(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    activity_type: str,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    fake.activities = [CashActivity("other", activity_type, S, "executed", 5.0, "SPY")]
    fake.extra_cash = 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)


@pytest.mark.parametrize("case", ["expired", "other_symbol", "wrong_amount", "not_held"])
def test_late_dividend_cash_without_an_eligible_due_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    case: str,
) -> None:
    if case != "not_held":
        _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    pay_date = S
    count = FROZEN.reconcile_dividend_credit_sessions + 1 if case == "expired" else 10
    for _ in range(count):
        pay_date = next_session(pay_date)
    fixed_clock.now = datetime.combine(pay_date, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    symbol = "MTUM" if case == "other_symbol" else "SPY"
    amount = 5.02 if case == "wrong_amount" else 5.0
    fake.activities = [CashActivity("div-pay", "DIV", pay_date, "executed", amount, symbol)]
    fake.extra_cash = amount
    if case in ("expired", "wrong_amount"):
        # A held name's unexplained dividend cash is a name cash break (#2266 item 1).
        # $5 is immaterial by default and would be auto-accepted (item 6), so the
        # threshold is 0 here: this test is about the attribution, not the size.
        always_material = FROZEN.model_copy(update={"break_materiality_usd": 0.0})
        assert (
            _reconcile(
                journal_settings,
                fake,
                open_window,
                fixed_clock,
                session=pay_date,
                frozen=always_material,
            ).status
            == "breaks"
        )
        return
    # No symbol maps to a held name: still the book-level `cash`.
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay_date)


def test_monthly_payer_nets_old_credit_before_new_due(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """At session 26, ex 0 is expired; its session-24 credit cannot pay ex 21."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    ex21 = S
    credit24 = S
    check26 = S
    for index in range(26):
        check26 = next_session(check26)
        if index == 20:
            ex21 = check26
        if index == 23:
            credit24 = check26
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="first")
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=ex21, source_action_id="second")
    credited_at = datetime.combine(credit24, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=credit24,
            kind="dividend_cash",
            security_id=SPY,
            cash=5.0,
            known_at=credited_at,
            ingested_at=credited_at,
        ),
    )
    fixed_clock.now = datetime.combine(check26, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            check26,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == pytest.approx({SPY: 5.0})
    fake.extra_cash = 10.0
    assert (
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=check26).status == OK
    )
    assert [a.cash for a in _adjustments(journal_settings, open_window)] == [5.0, 5.0]


def test_expired_unpaid_dividend_cannot_take_newer_dividends_credit(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
) -> None:
    """A credit for ex 21 at session 26 must stay paid at session 27."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    ex21 = S
    credit26 = S
    for index in range(26):
        credit26 = next_session(credit26)
        if index == 20:
            ex21 = credit26
    check27 = next_session(credit26)
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="unpaid")
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=ex21, source_action_id="paid")
    credited_at = datetime.combine(credit26, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=credit26,
            kind="dividend_cash",
            security_id=SPY,
            cash=5.0,
            known_at=credited_at,
            ingested_at=credited_at,
        ),
    )
    checked_at = datetime.combine(check27, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            check27,
            as_of=checked_at,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == {}


def test_newer_credit_is_not_reopened_by_an_expired_unpaid_due(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """E1 remains unpaid; booking E2 at 27 must not reopen E2 at 28."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    ex26 = S
    for _ in range(26):
        ex26 = next_session(ex26)
    pay27 = next_session(ex26)
    check28 = next_session(pay27)
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="unpaid")
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=ex26, source_action_id="paid")
    fixed_clock.now = datetime.combine(pay27, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    fake.activities = [CashActivity("e2", "DIV", pay27, "executed", 5.0, "SPY")]
    fake.extra_cash = 5.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay27).status == OK
    fixed_clock.now = datetime.combine(check28, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            check28,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == {}


def test_an_old_div_tag_cannot_admit_another_symbols_new_credit(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    old = CashActivity("old-spy", "DIV", S, "executed", 0.0, "SPY")
    fake.activities = [old]
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    pay = S
    for _ in range(10):
        pay = next_session(pay)
    fixed_clock.now = datetime.combine(pay, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    fake.activities = [old, CashActivity("new-mtum", "DIV", pay, "executed", 5.0, "MTUM")]
    fake.extra_cash = 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay)


def test_untagged_cash_after_ok_base_is_not_a_late_dividend(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    pay = S
    for _ in range(10):
        pay = next_session(pay)
    fixed_clock.now = datetime.combine(pay, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    fake.extra_cash = 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay)


def test_read_only_and_live_explanations_agree_on_pre_base_untagged_dividend(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Read-only has no broker DIV feed, so an older due is not offered."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    later = next_session(S)
    fixed_clock.now = datetime.combine(later, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    with open_read_only(journal_settings) as conn:
        read_only = explanations_as_of(
            conn,
            open_window,
            later,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert read_only.dividends == {}
    live = _reconcile(journal_settings, fake, open_window, fixed_clock, session=later)
    assert live.status == OK and live.adjustments == ()


def test_untagged_pre_base_dividend_does_not_widen_deferred_cash_band(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    _order(
        journal_settings,
        run_id,
        "tp-pending-tag",
        MTUM,
        "MTUM",
        1.0,
        events=("pending",),
        fill=False,
    )
    fake.submit(OrderRequest("tp-pending-tag", "MTUM", Side.BUY, quantity=1.0))
    pay = next_session(S)
    fixed_clock.now = datetime.combine(pay, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    fake.extra_cash = 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=pay)


def test_dividend_before_window_start_is_not_an_active_due(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
) -> None:
    """A held name carried into a later window cannot reopen its earlier dividend."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1), ex_date=S)
    later_start = next_session(S)
    later_window = replace(
        open_window,
        started_at=datetime.combine(later_start, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=14),
    )
    checked_at = datetime.combine(later_start, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            later_window,
            later_start,
            as_of=checked_at,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == {}


def test_dividend_absorbed_as_ended_proceeds_cannot_explain_later_untagged_cash(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(MTUM, "MTUM"): 10.0})
    _dividend(journal_settings, MTUM, 0.5, CUT - timedelta(days=1))
    _delisting(journal_settings, MTUM, CUT - timedelta(days=1))
    proceeds = 0.5 * 10 * _reference(journal_settings, MTUM)
    fake.extra_quantity["MTUM"] = -10.0
    fake.extra_cash = proceeds
    assert _reconcile(journal_settings, fake, open_window, fixed_clock).status == OK
    assert all(a.kind != "dividend_cash" for a in _adjustments(journal_settings, open_window))
    later = next_session(S)
    fixed_clock.now = datetime.combine(later, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    fake.extra_cash += 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=later)


def test_dividend_credit_window_excludes_its_exact_far_edge(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    inside = S
    for _ in range(FROZEN.reconcile_dividend_credit_sessions - 1):
        inside = next_session(inside)
    outside = next_session(inside)
    with open_read_only(journal_settings) as conn:
        due_inside = explanations_as_of(
            conn,
            open_window,
            inside,
            as_of=datetime.combine(inside, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20),
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert due_inside.dividends == pytest.approx({SPY: 5.0})
    fixed_clock.now = datetime.combine(outside, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    fake.activities = [CashActivity("edge", "DIV", outside, "executed", 5.0, "SPY")]
    fake.extra_cash = 5.0
    _reconcile(journal_settings, fake, open_window, fixed_clock, session=outside)


def test_netting_discards_a_cash_rounding_residue(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.03, CUT - timedelta(days=1))
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=S,
            kind="dividend_cash",
            security_id=SPY,
            cash=0.3 - 1e-16,
            known_at=datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
            ingested_at=datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
        ),
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == {}


def test_two_dividends_on_one_name_remain_due_after_the_first_is_paid(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    second = next_session(S)
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="first")
    _dividend(journal_settings, SPY, 0.7, CUT, ex_date=second, source_action_id="second")
    fixed_clock.now = datetime.combine(second, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            second,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == pytest.approx({SPY: 12.0})
    assert found.dividend_dues == {SPY: (5.0, 7.0)}
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=second,
            kind="dividend_cash",
            security_id=SPY,
            cash=5.0,
            known_at=fixed_clock.now,
            ingested_at=fixed_clock.now,
        ),
    )
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            second,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == pytest.approx({SPY: 7.0})
    assert found.dividend_dues == {SPY: (7.0,)}


def test_older_of_two_same_name_dividends_books_one_cash_row(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    second = next_session(S)
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="first")
    _dividend(journal_settings, SPY, 0.7, CUT, ex_date=second, source_action_id="second")
    fixed_clock.now = datetime.combine(second, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    fake.activities = [CashActivity("older", "DIV", second, "executed", 5.0, "SPY")]
    fake.extra_cash = 5.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, session=second).status == OK
    assert [(a.kind, a.cash) for a in _adjustments(journal_settings, open_window)] == [
        ("dividend_cash", 5.0)
    ]


def test_long_to_short_flip_still_books_the_older_long_dividend(
    journal_settings: Settings,
    flip_fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _hold(journal_settings, flip_fake, open_window, {(SPY, "SPY"): 10.0})
    flipped = next_session(S)
    ex2 = next_session(flipped)
    traded_at = datetime.combine(flipped, datetime.min.time(), tzinfo=UTC) + timedelta(hours=18)
    fixed_clock.now = traded_at
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.SELL, PositionIntent.SELL_TO_CLOSE
    )
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.SELL, PositionIntent.SELL_TO_OPEN
    )
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="long")
    _dividend(journal_settings, SPY, 0.7, CUT, ex_date=ex2, source_action_id="short")
    fixed_clock.now = datetime.combine(ex2, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            ex2,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividend_dues == {SPY: (5.0, -7.0)}
    assert found.dividends == {SPY: -2.0}
    flip_fake.activities = [CashActivity("long-credit", "DIV", ex2, "executed", 5.0, "SPY")]
    flip_fake.extra_cash = 5.0
    assert (
        _reconcile(journal_settings, flip_fake, open_window, fixed_clock, session=ex2).status == OK
    )
    assert [(row.kind, row.cash) for row in _adjustments(journal_settings, open_window)] == [
        ("dividend_cash", 5.0)
    ]


def test_short_to_long_flip_never_raises_for_mixed_sign_dividend_dues(
    journal_settings: Settings,
    flip_fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window)
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, BOUGHT_AT, Side.SELL, PositionIntent.SELL_TO_OPEN
    )
    flipped = next_session(S)
    ex2 = next_session(flipped)
    traded_at = datetime.combine(flipped, datetime.min.time(), tzinfo=UTC) + timedelta(hours=18)
    fixed_clock.now = traded_at
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.BUY, PositionIntent.BUY_TO_CLOSE
    )
    _filled_flip_trade(
        journal_settings, flip_fake, run_id, traded_at, Side.BUY, PositionIntent.BUY_TO_OPEN
    )
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S, source_action_id="short")
    _dividend(journal_settings, SPY, 0.7, CUT, ex_date=ex2, source_action_id="long")
    fixed_clock.now = datetime.combine(ex2, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            ex2,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividend_dues == {SPY: (-5.0, 7.0)}
    assert found.dividends == {SPY: 2.0}
    flip_fake.activities = [CashActivity("long-credit", "DIV", ex2, "executed", 7.0, "SPY")]
    flip_fake.extra_cash = 7.0
    result = _reconcile(journal_settings, flip_fake, open_window, fixed_clock, session=ex2)
    assert result.status in (OK, MISMATCH)
    if result.status == MISMATCH:
        assert [m.kind for m in result.mismatches] == ["cash"]


def test_expired_dividend_does_not_widen_deferred_cash_band(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=S)
    _order(
        journal_settings, run_id, "tp-pend-div", MTUM, "MTUM", 1.0, events=("pending",), fill=False
    )
    fake.submit(OrderRequest("tp-pend-div", "MTUM", Side.BUY, quantity=1.0))
    expired = S
    for _ in range(FROZEN.reconcile_dividend_credit_sessions + 1):
        expired = next_session(expired)
    fixed_clock.now = datetime.combine(expired, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=20
    )
    fake.extra_cash = 5.0
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, session=expired)


@pytest.mark.parametrize(
    ("trade", "expected"),
    [("buy_on_ex", {}), ("sell_on_ex", {SPY: 5.0}), ("sell_before_ex", {})],
)
def test_dividend_uses_holding_at_the_previous_trading_session(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    trade: str,
    expected: dict[str, float],
) -> None:
    if trade == "buy_on_ex":
        run_id = _run(journal_settings, open_window)
        _order(
            journal_settings,
            run_id,
            "tp-buy-on-ex",
            SPY,
            "SPY",
            10.0,
            at=datetime(2026, 10, 1, 15, 0, tzinfo=UTC),
        )
    else:
        run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
        sold_at = (
            datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
            if trade == "sell_before_ex"
            else datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
        )
        _sell_spy(journal_settings, run_id, sold_at)
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(minutes=1))
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=fixed_clock.now,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == expected


def test_a_dividend_known_only_after_close_of_the_previous_session_explains_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT + timedelta(minutes=1))
    fake.extra_cash = 5.0

    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _adjustments(journal_settings, open_window) == []


# --- the lagging input, and a pending order never read -----------------------


def test_the_lagging_input_is_read_and_a_pending_order_is_not(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, open_window)
    at = fixed_clock() - timedelta(minutes=30)
    _order(
        journal_settings,
        run_id,
        "tp-lag",
        SPY,
        "SPY",
        5.0,
        events=("pending", "accepted"),
        fill=False,
        at=at,
    )
    fake.submit(OrderRequest("tp-lag", "SPY", Side.BUY, quantity=5.0))
    fake.apply("tp-lag", PartialFill(2.0, PRICE))  # booked, not yet collected
    _order(
        journal_settings,
        run_id,
        "tp-pend",
        MTUM,
        "MTUM",
        3.0,
        events=("pending",),
        fill=False,
        at=at,
    )
    fake.submit(OrderRequest("tp-pend", "MTUM", Side.BUY, quantity=3.0))

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    read = [c.args for c in fake.calls if c.method == "get_order"]
    assert read == [("tp-lag",)]
    assert result.status == PENDING_UNRESOLVED
    assert result.lagging_ids == ("tp-lag",)
    assert result.pending_ids == ("tp-pend",)
    (row,) = _rows(journal_settings, open_window)
    listed = json.loads(row.mismatches_json or "")
    assert (listed["lagging"], listed["pending"]) == (["tp-lag"], ["tp-pend"])


# --- paper reconcile -----------------------------------------------------------


@pytest.fixture
def frozen_window(journal_settings: Settings) -> PaperWindowRow:
    """An open window whose `frozen_json` holds the risk section as `paper start`
    writes it (dotted keys, the registry's canonical form)."""
    frozen = {f"risk.{k}": v for k, v in ROOMY.model_dump(mode="json").items()}
    frozen["paper.tracking_k"] = 20
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
    (window_id,) = _append(journal_settings, row)
    return PaperWindowRow(**{**row.__dict__, "window_id": window_id})


def _command(settings: Settings, fake: FakeBroker, clock: FixedClock) -> Any:
    return reconcile_command(settings, lambda: open_for_write(settings), fake, clock)


def test_paper_reconcile_during_a_lag_writes_fills_lagging_and_engages_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    run_id = _run(journal_settings, frozen_window)
    _order(
        journal_settings,
        run_id,
        "tp-lag",
        SPY,
        "SPY",
        5.0,
        events=("pending", "accepted"),
        fill=False,
        at=fixed_clock() - timedelta(minutes=30),
    )
    fake.submit(OrderRequest("tp-lag", "SPY", Side.BUY, quantity=5.0))
    fake.apply("tp-lag", PartialFill(2.0, PRICE))

    result = _command(journal_settings, fake, fixed_clock)

    assert result.status == FILLS_LAGGING
    (row,) = _rows(journal_settings, frozen_window)
    assert (row.status, row.run_id, row.broker_cash) == (FILLS_LAGGING, None, None)
    with open_read_only(journal_settings) as conn:
        assert kill_switch_events_for(conn, frozen_window.window_id) == []  # type: ignore[arg-type]


def test_paper_reconcile_with_a_foreign_order_writes_the_mismatch_and_engages(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))  # left open

    with pytest.raises(ReconciliationError, match="foreign_order"):
        _command(journal_settings, fake, fixed_clock)

    (row,) = _rows(journal_settings, frozen_window)
    assert row.status == MISMATCH
    kinds = [m["kind"] for m in json.loads(row.mismatches_json or "")["mismatches"]]
    assert kinds == ["foreign_order"]
    with open_read_only(journal_settings) as conn:
        (event,) = kill_switch_events_for(conn, frozen_window.window_id)  # type: ignore[arg-type]
    assert (event.state, event.source, event.fault_type) == (
        "engaged",
        "fault",
        "ReconciliationError",
    )


def test_paper_reconcile_is_refused_while_a_run_holds_the_lock(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    with run_lock(journal_settings), pytest.raises(LockHeld):
        _command(journal_settings, fake, fixed_clock)
    assert fake.calls == ()
    assert _rows(journal_settings, frozen_window) == []


def test_paper_reconcile_with_no_open_window_is_refused_and_writes_nothing(
    journal_settings: Settings, fake: BookedFake, fixed_clock: FixedClock
) -> None:
    with pytest.raises(NoWindowError, match="no_window"):
        _command(journal_settings, fake, fixed_clock)
    assert fake.calls == ()
    with open_read_only(journal_settings) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM reconciliations").fetchone()  # type: ignore[misc]
    assert count == 0


@pytest.mark.parametrize(
    ("now", "session"),
    [
        (datetime(2026, 10, 1, 14, 0, tzinfo=UTC), date(2026, 10, 1)),
        (datetime(2026, 10, 2, 1, 0, tzinfo=UTC), date(2026, 10, 1)),  # 21:00 ET Thursday
        (datetime(2026, 10, 3, 15, 0, tzinfo=UTC), date(2026, 10, 2)),  # Saturday
    ],
)
def test_the_command_session_is_the_latest_session_on_the_new_york_date(
    now: datetime, session: date
) -> None:
    assert command_session(now) == session


# --- the frozen risk section ---------------------------------------------------


def _window_with(frozen_json: str) -> PaperWindowRow:
    at = datetime(2026, 9, 29, tzinfo=UTC)
    return PaperWindowRow(
        window_id=7,
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=1.0,
        starting_equity=1.0,
        code_version="test",
        started_at=at,
        frozen_json=frozen_json,
        frozen_sha256="0" * 64,
        known_at=at,
        ingested_at=at,
    )


def test_frozen_risk_reads_the_dotted_risk_keys() -> None:
    values = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
    values["risk.max_drawdown"] = 0.2
    values["paper.tracking_k"] = 20
    assert frozen_risk(_window_with(json.dumps(values))) == FROZEN.model_copy(
        update={"max_drawdown": 0.2}
    )


@pytest.mark.parametrize(
    "frozen_json",
    [
        "{}",
        "[]",
        "not json",
        json.dumps({"risk": RiskConfig().model_dump(mode="json")}),
        json.dumps(
            {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()} | {"risk.typo": 1}
        ),
        json.dumps(
            {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
            | {"risk.max_drawdown": 2.0}
        ),
        # A NaN or negative drawdown limit would make the switch's drawdown
        # check fail open: frozen_risk builds only through model_validate.
        json.dumps(
            {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
            | {"risk.max_drawdown": float("nan")}
        ),
        json.dumps(
            {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
            | {"risk.max_drawdown": -0.1}
        ),
        json.dumps(
            {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
            | {"risk.reconcile_cash_tolerance": float("inf")}
        ),
    ],
)
def test_frozen_risk_refuses_anything_but_the_whole_dotted_section(frozen_json: str) -> None:
    with pytest.raises(ValueError, match="window 7"):
        frozen_risk(_window_with(frozen_json))


def _todays_risk_keys_without(*dropped: str) -> dict[str, object]:
    """Every `risk.*` key of `FROZEN` but `dropped`, dotted, as `paper start` freezes them."""
    return {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items() if k not in dropped}


def test_frozen_risk_reads_a_window_frozen_before_max_skip_share_as_zero() -> None:
    """Paper spec amendment 2026-10-18 item 3 (T202, #1808): the two open windows
    froze today's keys and no `risk.max_skip_share`; `frozen_risk` reads it as 0,
    so max(`max_skips_per_run`, 0) keeps their frozen absolute cap exactly, and
    every other value is the frozen one."""
    values = _todays_risk_keys_without("max_skip_share") | {
        "risk.max_skips_per_run": 7,
        "paper.tracking_k": 20,
    }
    frozen = frozen_risk(_window_with(json.dumps(values)))
    assert frozen.max_skip_share == 0
    assert frozen.max_skips_per_run == 7
    assert frozen == FROZEN.model_copy(update={"max_skip_share": 0.0, "max_skips_per_run": 7})


def test_frozen_risk_reads_a_frozen_max_skip_share_as_frozen() -> None:
    """A window started after T202 froze the key: its value is read, never 0."""
    values = _todays_risk_keys_without() | {"risk.max_skip_share": 0.2}
    assert frozen_risk(_window_with(json.dumps(values))).max_skip_share == pytest.approx(0.2)


def test_frozen_risk_reads_a_window_frozen_before_the_merger_lag_at_its_default() -> None:
    """Paper spec amendment #1762 item 10 (T249): a window frozen before T249 has
    no `risk.reconcile_merger_lag_sessions` and reads it at 5; a frozen value is
    read as frozen."""
    values = _todays_risk_keys_without("reconcile_merger_lag_sessions")
    assert frozen_risk(_window_with(json.dumps(values))).reconcile_merger_lag_sessions == 5
    frozen = _todays_risk_keys_without() | {"risk.reconcile_merger_lag_sessions": 2}
    assert frozen_risk(_window_with(json.dumps(frozen))).reconcile_merger_lag_sessions == 2


def test_frozen_risk_backfills_dividend_credit_window() -> None:
    values = _todays_risk_keys_without("reconcile_dividend_credit_sessions")
    assert frozen_risk(_window_with(json.dumps(values))).reconcile_dividend_credit_sessions == 25
    values["risk.reconcile_dividend_credit_sessions"] = 12
    assert frozen_risk(_window_with(json.dumps(values))).reconcile_dividend_credit_sessions == 12


def test_frozen_risk_reads_a_window_frozen_before_the_delisting_premium_at_its_default() -> None:
    """#411: a window frozen before `risk.delisting_cash_max_premium` reads it at 0.10."""
    values = _todays_risk_keys_without("delisting_cash_max_premium")
    assert frozen_risk(_window_with(json.dumps(values))).delisting_cash_max_premium == 0.10


#: Paper spec amendment 2026-10-26 (#2266) item 5, open question 42: the six
#: break keys a window frozen before T280b lacks, at their decided defaults.
_BREAK_DEFAULTS: dict[str, float] = {
    "break_materiality_usd": 25.0,
    "break_materiality_bps": 5.0,
    "break_max_age_sessions": 5,
    "max_open_breaks": 3,
    "max_open_break_share": 0.02,
    "break_cause_lookback_sessions": 10,
}


def test_frozen_risk_reads_a_window_frozen_before_the_break_keys_at_their_defaults() -> None:
    """#2266 item 5 (question 42, the #2183 precedent): a window frozen before
    the six break keys reads all six at their defaults instead of raising
    `lacks risk keys`, and every other value is the frozen one."""
    values = _todays_risk_keys_without(*_BREAK_DEFAULTS) | {"risk.max_skips_per_run": 7}
    frozen = frozen_risk(_window_with(json.dumps(values)))
    assert {k: getattr(frozen, k) for k in _BREAK_DEFAULTS} == _BREAK_DEFAULTS
    assert frozen == FROZEN.model_copy(update={"max_skips_per_run": 7})


def test_frozen_risk_keeps_a_later_windows_stored_break_keys() -> None:
    """A window frozen after T280b keeps its stored values, never the defaults."""
    stored = {
        "break_materiality_usd": 0.0,
        "break_materiality_bps": 1.5,
        "break_max_age_sessions": 2,
        "max_open_breaks": 0,
        "max_open_break_share": 0.5,
        "break_cause_lookback_sessions": 3,
    }
    values = _todays_risk_keys_without() | {f"risk.{k}": v for k, v in stored.items()}
    frozen = frozen_risk(_window_with(json.dumps(values)))
    assert {k: getattr(frozen, k) for k in stored} == stored


_BACK_FILLED = (
    "max_skip_share",
    "reconcile_merger_lag_sessions",
    "reconcile_dividend_credit_sessions",
    "reconcile_dividend_exhaustive_dues",
    "reconcile_dividend_subset_states",
    "delisting_cash_max_premium",
    *_BREAK_DEFAULTS,
)


@pytest.mark.parametrize(
    "missing", sorted(k for k in RiskConfig.model_fields if k not in _BACK_FILLED)
)
def test_frozen_risk_still_refuses_a_window_lacking_any_other_risk_key(missing: str) -> None:
    """Only the legacy back-fills allow a missing frozen risk key."""
    values = _todays_risk_keys_without(missing)
    with pytest.raises(ValueError, match=f"window 7 frozen_json lacks risk keys.*{missing}"):
        frozen_risk(_window_with(json.dumps(values)))


def test_no_adapter_is_imported() -> None:
    module = Path(__file__).resolve().parents[2] / "src/tradepartner/execution/reconcile_run.py"
    source = module.read_text(encoding="utf-8")
    assert "fake_broker" not in source and "alpaca_broker" not in source


def test_stock_dividends_picks_only_in_band_splits_before_the_session() -> None:
    """#1627: `_stock_dividends` seats a `CIL` beside a held stock dividend, so it
    must read a stock-only band: a `split` with `1 < ratio <= stock_dividend_max_rate`
    whose ex-date is at or before the session, and never a 2:1 split, a reverse
    split, a non-split row, or a split whose ex-date has not passed. The result is
    `(ex_date, last_acceptable_CIL_date, ratio)`; a zero lag keeps the window on
    the ex-date."""
    actions = pl.DataFrame(
        {
            "security_id": ["A", "B", "C", "D", "E", "F"],
            "action_type": ["split", "split", "split", "split", "dividend", "split"],
            "ex_date": [S, S, S, S, S, S + timedelta(days=1)],
            "ratio_or_amount": [1.005, 2.0, 1.20, 0.5, 1.1, 1.10],
        }
    )

    assert reconcile_run._stock_dividends(actions, S, 1.25, 0) == {
        "A": (S, S, 1.005),
        "C": (S, S, 1.20),
    }


# --- review follow-ups: the cash base, dedupe, the reference cut, failures ----

SATURDAY = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)  # paper reconcile states Friday


def test_a_second_weekend_reconcile_keeps_the_cash_base(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fixed_clock.now = SATURDAY
    fake.extra_cash = 0.009  # broker rounding, inside the cash tolerance
    assert _command(journal_settings, fake, fixed_clock).status == OK

    fixed_clock.advance(minutes=5)
    fake.extra_cash = 0.018  # 0.009 past the Saturday base, 0.018 past the start
    assert _command(journal_settings, fake, fixed_clock).status == OK


def test_a_journaled_dividend_is_never_offered_again(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    friday = date(2026, 10, 2)
    _dividend(journal_settings, SPY, 0.5, CUT, ex_date=friday)
    fixed_clock.now = SATURDAY
    fake.extra_cash = 5.0
    assert _command(journal_settings, fake, fixed_clock).status == OK

    fixed_clock.advance(minutes=5)
    fake.extra_cash = 10.0  # credited a second time
    with pytest.raises(ReconciliationError, match="cash"):
        _command(journal_settings, fake, fixed_clock)
    assert len(_adjustments(journal_settings, frozen_window)) == 1


def _bar(settings: Settings, security_id: str, close: float, known_at: datetime) -> None:
    _fact(
        settings,
        "prices_daily",
        security_id=security_id,
        session=date(2026, 9, 30),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="bar",
    )


def _bar_at(
    settings: Settings, security_id: str, session: date, close: float, known_at: datetime
) -> None:
    """A bar as of `known_at` (before `cut`), on any session."""
    _fact(
        settings,
        "prices_daily",
        security_id=security_id,
        session=session,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
        known_at=known_at,
        ingested_at=known_at,
        source="alpaca",
        provenance="bar",
    )


def test_reference_prices_are_bars_known_at_close_of_the_previous_session(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 1.0, (MTUM, "MTUM"): 1.0})
    stale = _reference(journal_settings, MTUM)
    _bar(journal_settings, SPY, 77.0, CUT)
    _bar(journal_settings, MTUM, 123.0, CUT + timedelta(minutes=1))
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=CUT,
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.reference_prices == {SPY: 77.0, MTUM: stale}


def test_a_dividend_is_owed_on_the_holding_before_its_ex_date(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    on_ex_date = datetime(2026, 10, 1, 13, 30, tzinfo=UTC)
    _order(journal_settings, run_id, "tp-late", MTUM, "MTUM", 4.0, at=on_ex_date)
    _order(journal_settings, run_id, "tp-more", SPY, "SPY", 6.0, at=on_ex_date)
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(minutes=1))
    _dividend(journal_settings, MTUM, 0.7, CUT - timedelta(minutes=1))
    with open_read_only(journal_settings) as conn:
        found = explanations_as_of(
            conn,
            open_window,
            S,
            as_of=on_ex_date,  # a post-trade read on S: the orders journaled then count
            settings=journal_settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )
    assert found.dividends == pytest.approx({SPY: 5.0})


@pytest.mark.parametrize(
    "engage",
    [
        lambda *_a, **_k: switch.WriteFailed("IOException: store locked"),
        lambda *_a, **_k: (_ for _ in ()).throw(ClockError("clock failed")),
    ],
)
def test_paper_reconcile_still_names_the_mismatch_when_the_switch_cannot_be_written(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    engage: Any,
) -> None:
    monkeypatch.setattr(switch, "engage", engage)
    fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))
    with pytest.raises(ReconciliationError, match=r"foreign_order.*could not be written"):
        _command(journal_settings, fake, fixed_clock)


class FailingFake(BookedFake):
    def account(self) -> Account:
        raise RuntimeError("transport")


def test_a_broker_error_propagates_and_writes_nothing(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    failing = FailingFake(clock=fixed_clock, price_of=lambda _s: PRICE, account_id="PA1")
    with pytest.raises(RuntimeError, match="transport"):
        _reconcile(journal_settings, failing, open_window, fixed_clock)
    assert _rows(journal_settings, open_window) == []


def test_a_clock_going_back_is_a_clock_error_and_writes_nothing(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    readings = iter(
        [datetime(2026, 10, 1, 14, 0, tzinfo=UTC), datetime(2026, 10, 1, 13, 59, tzinfo=UTC)]
    )
    with pytest.raises(ClockError, match="went back"):
        _reconcile(journal_settings, fake, open_window, lambda: next(readings), as_of=CUT)  # type: ignore[arg-type]
    assert _rows(journal_settings, open_window) == []


def test_the_row_and_its_adjustments_commit_together(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))
    fake.extra_cash = 5.0

    def refuse_adjustments(conn: Any, row: Any) -> int | None:
        if isinstance(row, AdjustmentRow):
            raise RuntimeError("disk full")
        return append(conn, row)

    monkeypatch.setattr(reconcile_run, "append", refuse_adjustments)
    with pytest.raises(RuntimeError, match="disk full"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert _rows(journal_settings, open_window) == []


# --- the journal cut (#488) ---------------------------------------------------------


def _later_run(settings: Settings, window: PaperWindowRow) -> None:
    """What the run on S journals after close(S-1): a MTUM buy filled at 15:00Z and an
    `ok` reconciliation at 21:00Z, both on S."""
    at = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    (run_id,) = _append(
        settings,
        PaperRunRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=S,
            kind="rebalance",
            started_at=at,
            invoked_by="scheduler",
            code_version="test",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert run_id is not None
    _order(settings, run_id, "tp-on-s", MTUM, "MTUM", 5.0, at=at)
    reconciled = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
    _append(
        settings,
        ReconciliationRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            run_id=run_id,
            at=reconciled,
            status=OK,
            broker_cash=98_500.0,
            known_at=reconciled,
            ingested_at=reconciled,
        ),
    )


def _explain(settings: Settings, window: PaperWindowRow, as_of: datetime) -> Explanations:
    with open_read_only(settings) as conn:
        return explanations_as_of(
            conn,
            window,
            S,
            as_of=as_of,
            settings=settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )


def test_journal_rows_known_after_as_of_change_no_explanation(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """#488's failing case: the run on S's own rows, known after close(S-1), neither add
    a name nor become the dividend base of the pre-trade read (`as_of` = close(S-1))."""
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    reconciled = BOUGHT_AT + timedelta(hours=6)
    _append(
        journal_settings,
        ReconciliationRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            run_id=run_id,
            at=reconciled,
            status=OK,
            broker_cash=99_000.0,
            known_at=reconciled,
            ingested_at=reconciled,
        ),
    )
    _dividend(journal_settings, SPY, 1.5, CUT - timedelta(hours=2))
    before = _explain(journal_settings, open_window, CUT)

    _later_run(journal_settings, open_window)

    assert _explain(journal_settings, open_window, CUT) == before
    assert before.symbols == {SPY: "SPY"}
    assert before.dividends == pytest.approx({SPY: 15.0})


def test_the_post_trade_read_sees_the_runs_own_rows(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """Step 8 reads at the run's clock, so the run's own S fills are explained."""
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _later_run(journal_settings, open_window)
    step_8 = datetime(2026, 10, 1, 20, 30, tzinfo=UTC)  # after the fill, before the 21:00Z row
    found = _explain(journal_settings, open_window, step_8)
    assert found.symbols == {SPY: "SPY", MTUM: "MTUM"}
    assert set(found.reference_prices) == {SPY, MTUM}


def _collected_late(
    settings: Settings,
    run_id: int,
    security_id: str,
    symbol: str,
    quantity: float,
    collected: datetime,
) -> None:
    """An order known before close(S-1), filled at 15:59 ET on S-1, and collected (fill
    and terminal event journaled) at `collected` by S's step 3."""
    placed = CUT - timedelta(hours=1)
    filled = CUT - timedelta(minutes=1)
    order = _order(
        settings,
        run_id,
        f"tp-late-{symbol}",
        security_id,
        symbol,
        quantity,
        events=("pending", "accepted"),
        fill=False,
        at=placed,
    )
    _append(
        settings,
        FillRow(
            client_order_id=order.client_order_id,
            filled_at=filled,
            quantity=quantity,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{order.client_order_id}",
            source="broker_feed",
            known_at=collected,
            ingested_at=collected,
        ),
        OrderEventRow(
            client_order_id=order.client_order_id,
            status="filled",
            known_at=collected,
            ingested_at=collected,
        ),
    )


def test_a_late_collected_fill_and_a_later_adjustment_change_no_explanation(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    """The fills and adjustments cuts: a fill filled on S-1 but collected on S, and a
    `dividend_cash` row journaled on S, are not read at `as_of` = close(S-1). Read at
    the run's clock, the fill becomes MTUM's dividend base and the row settles SPY's."""
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 1.5, CUT - timedelta(hours=2))
    _dividend(journal_settings, MTUM, 0.5, CUT - timedelta(hours=2))
    collected = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)  # S's step 3, before the open
    _collected_late(journal_settings, run_id, MTUM, "MTUM", 4.0, collected)
    before = _explain(journal_settings, open_window, CUT)
    assert before.dividends == pytest.approx({SPY: 15.0})  # MTUM not yet held at CUT

    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            run_id=run_id,
            session=S,
            kind="dividend_cash",
            security_id=SPY,
            cash=15.0,
            known_at=collected + timedelta(hours=8),
            ingested_at=collected + timedelta(hours=8),
        ),
    )

    assert _explain(journal_settings, open_window, CUT) == before
    later = _explain(journal_settings, open_window, collected + timedelta(hours=9))
    assert later.dividends == pytest.approx({MTUM: 2.0})


def test_reconcile_now_states_the_ledger_from_rows_known_at_as_of(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """`reconcile_now` passes its `as_of` to the ledger's journal read: a fill collected
    after it is not in the ledger, so the broker's position in that name is unexplained
    (why a run's pre-trade reconciliation cannot cut at close(S-1), #488)."""
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    collected = fixed_clock() - timedelta(minutes=10)
    _collected_late(journal_settings, run_id, MTUM, "MTUM", 4.0, collected)
    fake.submit(OrderRequest("tp-late-MTUM", "MTUM", Side.BUY, quantity=4.0))
    fake.simulate_fill("tp-late-MTUM")

    with pytest.raises(ReconciliationError, match=r"broker_only_position"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, as_of=CUT)
    fixed_clock.advance(minutes=5)
    assert (
        _reconcile(journal_settings, fake, open_window, fixed_clock, as_of=fixed_clock()).status
        == OK
    )


def test_an_as_of_after_the_clock_reading_is_refused_before_any_broker_call(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    later = fixed_clock() + timedelta(seconds=1)
    with pytest.raises(ClockError, match="after the clock reading"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, as_of=later)
    assert _rows(journal_settings, open_window) == []


# --- books (ADR 0017 B.4 and B.5, plan T155) ------------------------------------------


@pytest.fixture
def b_frozen_window(journal_settings: Settings, frozen_window: PaperWindowRow) -> PaperWindowRow:
    """Book `b`'s open window on its own account (PB1), beside `main`'s."""
    row = replace(frozen_window, window_id=None, book_id="b", account_id="PB1")
    (window_id,) = _append(journal_settings, row)
    return replace(row, window_id=window_id)


def _b_fake(clock: FixedClock) -> BookedFake:
    return BookedFake(
        assets=FakeBroker.active_assets("SPY", "MTUM"),
        clock=clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PB1",
    )


def test_a_reconciliation_fault_halts_only_its_book(
    journal_settings: Settings,
    frozen_window: PaperWindowRow,
    b_frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Spec acceptance (ADR 0017 B.5): a mismatch on `b`'s account writes `b`'s
    row and engages `b`'s switch; `main` gets no row and no engagement."""
    b_fake = _b_fake(fixed_clock)
    b_fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))  # foreign, open

    with pytest.raises(ReconciliationError, match="foreign_order"):
        reconcile_command(
            journal_settings,
            lambda: open_for_write(journal_settings),
            b_fake,
            fixed_clock,
            book_id="b",
        )

    (row,) = _rows(journal_settings, b_frozen_window)
    assert (row.status, row.book_id) == (MISMATCH, "b")
    assert _rows(journal_settings, frozen_window) == []
    with open_read_only(journal_settings) as conn:
        (event,) = kill_switch_events_for(conn, b_frozen_window.window_id)  # type: ignore[arg-type]
        assert kill_switch_events_for(conn, frozen_window.window_id) == []  # type: ignore[arg-type]
    assert (event.state, event.source) == ("engaged", "fault")


def test_main_s_reconcile_reads_main_s_window_beside_b_s(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    b_frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """With no book, `paper reconcile` is `main`'s (`paper.book_id`): `b`'s open
    window is not a second open window, and `main`'s row is `main`'s."""
    result = _command(journal_settings, fake, fixed_clock)
    assert result.status == "ok"
    (row,) = _rows(journal_settings, frozen_window)
    assert row.book_id == "main"
    assert _rows(journal_settings, b_frozen_window) == []


def test_b_s_reconcile_runs_while_main_s_lock_is_held(
    journal_settings: Settings,
    frozen_window: PaperWindowRow,
    b_frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    b_fake = _b_fake(fixed_clock)
    with run_lock(journal_settings, "main"):
        result = reconcile_command(
            journal_settings,
            lambda: open_for_write(journal_settings),
            b_fake,
            fixed_clock,
            book_id="b",
        )
        with run_lock(journal_settings, "b"), pytest.raises(LockHeld):
            reconcile_command(
                journal_settings,
                lambda: open_for_write(journal_settings),
                b_fake,
                fixed_clock,
                book_id="b",
            )
    assert result.status == "ok"


def test_reconcile_for_a_book_with_no_window_is_no_window(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    with pytest.raises(NoWindowError, match="no_window"):
        reconcile_command(
            journal_settings,
            lambda: open_for_write(journal_settings),
            fake,
            fixed_clock,
            book_id="b",
        )
    assert fake.calls == ()


# --- booked `FEE` cash activities by observation (2026-10-12, item 6; T183) ------


def _fee(activity_id: str, day: date, net: float, status: str = "executed") -> CashActivity:
    return CashActivity(
        activity_id=activity_id,
        activity_type="FEE",
        date=day,
        status=status,
        net_amount=net,
        symbol="SPY",
        description="SEC fee",
    )


def _CIL_ACTIVITY(activity_id: str, net: float) -> CashActivity:
    """A `CIL` (cash in lieu of stock) for SPY on the ex-date, `status` executed
    (#1627)."""
    return CashActivity(
        activity_id=activity_id,
        activity_type="CIL",
        date=S,
        status="executed",
        net_amount=net,
        symbol="SPY",
        description="cash in lieu of stock",
    )


def test_two_fee_rows_are_booked_once_by_their_id(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The window-1 shape: the broker charged two cents the ledger lacks, each a
    dated `FEE` row; they are booked once, keyed by id, on the fee's own date,
    and a second reconciliation books nothing more."""
    fake.activities = [_fee("f1", S, -0.01), _fee("f2", S, -0.01)]
    fake.extra_cash = -0.02  # the broker charged two cents the ledger lacks

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    booked = _adjustments(journal_settings, open_window)
    assert [(a.kind, a.cash, a.broker_activity_id, a.session) for a in booked] == [
        ("fee_cash", -0.01, "f1", S),
        ("fee_cash", -0.01, "f2", S),
    ]
    fixed_clock.advance(minutes=5)
    again = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert again.status == OK and again.adjustments == ()
    assert len(_adjustments(journal_settings, open_window)) == 2


def test_the_same_gap_with_no_activity_still_halts_as_cash(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A `FEE` is booked by observation, never inferred from the difference: the
    -0.02 with no activity read is still a `cash` mismatch."""
    fake.extra_cash = -0.02  # the broker charged two cents the ledger lacks
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)


def test_an_unknown_cash_activity_halts_the_window_as_unexplained_activity(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    fake.activities = [
        CashActivity(
            activity_id="i-1",
            activity_type="INT",
            date=S,
            status="executed",
            net_amount=-0.5,
        )
    ]
    with pytest.raises(ReconciliationError, match="unexplained_activity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)


def test_under_the_fallback_a_fee_dated_on_the_start_date_is_booked(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Null sets (no ok base, no `activities_seen_json`): a `FEE` dated on the
    window's New York start date is booked; an earlier one is inside the base."""
    fake.activities = [_fee("old", date(2026, 9, 28), -0.02), _fee("on", date(2026, 9, 29), -0.01)]
    fake.extra_cash = -0.01  # only the start-date fee is a real gap

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert [a.broker_activity_id for a in result.adjustments] == ["on"]


def test_a_fee_beside_a_pending_order_is_deferred_fee_not_journaled(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Beside an open allowance the `FEE` is subtracted and listed under
    `allowed` as `deferred fee`, but never journaled (#1583, T183)."""
    run_id = _run(journal_settings, open_window)
    _order(journal_settings, run_id, "tp-pend", MTUM, "MTUM", 3.0, events=("pending",), fill=False)
    fake.submit(OrderRequest("tp-pend", "MTUM", Side.BUY, quantity=3.0))
    fake.activities = [_fee("f1", S, -0.01)]
    fake.extra_cash = -0.01

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)

    assert result.status == PENDING_UNRESOLVED
    assert ("deferred fee", -0.01, None) in [
        (a.reason, a.difference, a.security_id) for a in result.allowed
    ]
    assert result.adjustments == ()
    assert _adjustments(journal_settings, open_window) == []


def test_reconcile_reads_account_before_cash_activities(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The read order is fixed (#1583): `account()` is read before
    `cash_activities(since)`, so the read race has one shape."""
    _reconcile(journal_settings, fake, open_window, fixed_clock)
    methods = [call.method for call in fake.calls]
    assert methods.index("account") < methods.index("cash_activities")


def test_a_fee_the_activity_read_lists_but_the_account_has_not_charged_halts_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The read race to the spec's letter (#1583): `account()` is read first, so a
    fee posting between the two reads is in the second activities read but not in
    `account().cash`; booking it leaves a +0.02 gap that halts as `cash`, and the
    next reconciliation (which reads the charged cash too) books it and passes."""
    fake.activities = [_fee("f1", S, -0.02)]  # the read sees it; `account()` does not
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    fake.extra_cash = -0.02  # now the broker cash reflects the fee too
    fixed_clock.advance(minutes=5)
    result = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert result.status == OK
    assert [a.broker_activity_id for a in result.adjustments] == ["f1"]


# --- the seen-set sources end to end (#1583, item 6; Q2-Q4, S2) -----------------


def _ok_base(
    settings: Settings,
    window: PaperWindowRow,
    *,
    at: datetime,
    broker_cash: float,
    activity_ids: tuple[str, ...] = (),
    recorded: bool = True,
) -> None:
    """Journal an `ok` reconciliation at `at` re-basing the cash to `broker_cash`;
    `recorded` False writes a pre-T183 row with no `activities` set (null)."""
    payload: dict[str, object] = {"mismatches": [], "lagging": [], "pending": []}
    if recorded:
        payload["activities"] = [
            {"activity_id": i, "activity_type": "FEE", "status": "executed"} for i in activity_ids
        ]
    _append(
        settings,
        ReconciliationRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            at=at,
            status="ok",
            broker_cash=broker_cash,
            mismatches_json=json.dumps(payload, sort_keys=True),
            known_at=at,
            ingested_at=at,
        ),
    )


def _day_before(day: date) -> date:
    return previous_session(day)


def test_a_fee_dated_before_an_ok_base_but_posted_after_it_is_booked(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(a) A `FEE` dated D that posts after an `ok` reconciliation dated D+1 is not
    in the base's recorded set (the base did not see it), so it is booked next
    even though its date precedes the base's: the base's set decides, not the date."""
    base_at = session_close(_day_before(S))
    _ok_base(
        journal_settings, open_window, at=base_at, broker_cash=100_000.0, activity_ids=("other",)
    )
    fake.activities = [_fee("f1", _day_before(S), -0.02)]  # dated before the base, posted after
    fake.extra_cash = -0.02

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert [a.broker_activity_id for a in result.adjustments] == ["f1"]


def test_a_fee_the_base_recorded_is_never_booked_again_whatever_its_date(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(b) An id in the base's recorded set was in its `broker_cash`: never booked,
    whatever the date attached to it in the live read."""
    base_at = session_close(_day_before(S))
    _ok_base(journal_settings, open_window, at=base_at, broker_cash=100_000.0, activity_ids=("f1",))
    fake.activities = [_fee("f1", S, -0.02)]  # a later date, the same id

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert result.adjustments == ()


def test_a_start_set_fee_is_not_booked_but_a_later_same_day_one_is(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(c, d) `paper start` records the ids it read into `activities_seen_json`;
    such a fee is never booked, but one of the same start date posted after
    `paper start` (not in the set) is booked."""
    start = open_window.started_at.astimezone(_NEW_YORK).date()
    window = replace(open_window, activities_seen_json=json.dumps(["f-prestart"]))
    fake.activities = [
        _fee("f-prestart", start, -0.01),  # recorded at paper start
        _fee("f-poststart", start, -0.02),  # posted after, same start date
    ]
    fake.extra_cash = -0.02  # only the post-start fee is a real gap

    result = _reconcile(journal_settings, fake, window, fixed_clock)

    assert result.status == OK
    assert [a.broker_activity_id for a in result.adjustments] == ["f-poststart"]


def test_under_the_fallback_a_fee_after_a_pre_t183_ok_row_is_booked(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(e) A pre-T183 `ok` row has no recorded set, so the date fallback governs: a
    `FEE` dated D (on or after the window's start) that posts after a pre-T183
    ok row dated D+1 is still booked."""
    start = open_window.started_at.astimezone(_NEW_YORK).date()
    later = _day_before(S)
    _ok_base(
        journal_settings,
        open_window,
        at=session_close(later),
        broker_cash=100_000.0,
        recorded=False,  # a pre-T183 row: null set
    )
    fake.activities = [_fee("f1", start, -0.02)]  # dated the start, posted after the ok row
    fake.extra_cash = -0.02

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert [a.broker_activity_id for a in result.adjustments] == ["f1"]


def test_a_booked_fee_is_not_rebooked_when_the_base_is_older_than_the_booking(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(f) A booked id is never re-booked even when `base(through)` picks an `ok`
    row older than the booking and the booking is the only guard: the base's
    recorded set names a different id, so the journaled `broker_activity_id`
    alone keeps the booked fee from counting twice (it is what mutation M3, dropping
    the booked-id set, would break)."""
    _ok_base(
        journal_settings,
        open_window,
        at=session_close(_day_before(S)),
        broker_cash=100_000.0,
        activity_ids=("other",),  # the base recorded a different id; only the booking knows f1
    )
    _journal_fee(
        journal_settings, open_window, "f1", S, -0.02, at=fixed_clock() - timedelta(minutes=5)
    )
    fake.activities = [_fee("f1", S, -0.02)]
    fake.extra_cash = -0.02  # the broker cash already reflects the booked fee

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert result.adjustments == ()  # already journaled, and the ledger already holds it


def _journal_fee(
    settings: Settings,
    window: PaperWindowRow,
    activity_id: str,
    session: date,
    net: float,
    *,
    at: datetime,
) -> None:
    _append(
        settings,
        AdjustmentRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=session,
            kind="fee_cash",
            cash=net,
            broker_activity_id=activity_id,
            known_at=at,
            ingested_at=at,
        ),
    )


def test_a_fee_dated_before_the_reconciliation_keeps_its_own_session(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(item 6, Q6) A booked `fee_cash` is stated on its own activity date, not the
    reconciliation session."""
    fee_day = _day_before(S)
    fake.activities = [_fee("f1", fee_day, -0.02)]
    fake.extra_cash = -0.02

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    (row,) = _adjustments(journal_settings, open_window)
    assert row.session == fee_day  # its own date, one or more days before S


def test_a_booked_fee_the_fake_then_cancels_is_unexplained_activity(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(item 6, S4) A fee the ledger booked, whose status the broker later corrects,
    halts as `unexplained_activity`, naming the row and its id."""
    fake.activities = [_fee("f1", S, -0.02)]
    fake.extra_cash = -0.02
    first = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert first.status == OK
    fixed_clock.advance(minutes=5)
    fake.activities = [_fee("f1", S, -0.02, status="canceled")]

    with pytest.raises(ReconciliationError, match="unexplained_activity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    (row,) = _rows(journal_settings, open_window)[-1:]
    detail = json.loads(row.mismatches_json)["mismatches"][0]
    assert detail["kind"] == "unexplained_activity"
    assert "f1" in detail["detail"]


def _journal_fill(
    settings: Settings, client_order_id: str, quantity: float, *, at: datetime, accept: bool = True
) -> None:
    """Journal the terminal event and fill for an order the test already journaled
    (a `pending` order is settled by an `accepted` event and its fill)."""
    rows: list[object] = []
    if accept:
        rows.append(
            OrderEventRow(
                client_order_id=client_order_id, status="accepted", known_at=at, ingested_at=at
            )
        )
    rows.append(
        FillRow(
            client_order_id=client_order_id,
            filled_at=at,
            quantity=quantity,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{client_order_id}",
            source="broker_feed",
            known_at=at,
            ingested_at=at,
        )
    )
    _append(settings, *rows)


def test_a_dividend_row_with_a_matching_corporate_action_is_dividend_cash_without_a_fee_id(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A `DIV` activity is read and recorded, never booked by id (#1583, Q7); the
    cash it stands for is explained by the store's dividend rule as a `dividend_cash`
    whose `broker_activity_id` is null."""
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _dividend(journal_settings, SPY, 0.5, CUT - timedelta(days=1))  # 0.5 x 10 = 5.0 due
    fake.activities = [
        CashActivity(
            activity_id="d-1", activity_type="DIV", date=S, status="executed", net_amount=5.0
        )
    ]
    fake.extra_cash = 5.0  # the broker credited the dividend

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)

    assert result.status == OK
    (booked,) = _adjustments(journal_settings, open_window)
    assert booked.kind == "dividend_cash"
    assert booked.broker_activity_id is None  # dividends are never keyed by an activity id
    assert [r.activity_type for r in result.activities] == ["DIV"]


def test_a_dividend_row_without_a_matching_corporate_action_is_a_cash_mismatch(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A `DIV` activity the store has no dividend for does not explain the cash: the
    broker paid it but the journal cannot, so it is a `cash` mismatch."""
    run_id = _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    fake.activities = [
        CashActivity(
            activity_id="d-1", activity_type="DIV", date=S, status="executed", net_amount=5.0
        )
    ]
    fake.extra_cash = 5.0  # credited, but no corporate_action backs it
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)


def test_a_deferred_fee_is_booked_by_the_next_ok_reconciliation_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(item 6, Q8) A `FEE` beside an open allowance is `deferred fee`, never
    journaled; the next reconciliation without the allowance books it exactly
    once."""
    run_id = _run(journal_settings, open_window)
    _order(journal_settings, run_id, "tp-pend", MTUM, "MTUM", 3.0, events=("pending",), fill=False)
    fake.submit(OrderRequest("tp-pend", "MTUM", Side.BUY, quantity=3.0))
    fake.activities = [_fee("f1", S, -0.02)]
    fake.extra_cash = -0.02

    deferred = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)
    assert deferred.status == PENDING_UNRESOLVED
    assert ("deferred fee", -0.02, None) in [
        (a.reason, a.difference, a.security_id) for a in deferred.allowed
    ]
    assert _adjustments(journal_settings, open_window) == []

    # settle the pending order (journal + fill it) and reconcile again: one fee_cash
    fixed_clock.advance(minutes=5)  # past the deferred reconciliation's stamp
    _journal_fill(journal_settings, "tp-pend", 3.0, at=fixed_clock())
    fake.apply("tp-pend", PartialFill(3.0, PRICE))
    fake.extra_cash = -0.02  # the settled buy is already in the fake's cash; only the fee extra
    settled = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)
    assert settled.status == OK
    booked = _adjustments(journal_settings, open_window)
    assert [a.broker_activity_id for a in booked if a.kind == "fee_cash"] == ["f1"]


def test_a_fee_beside_a_lagging_order_is_deferred_then_booked_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """(item 6) The lagging-order twin: a `FEE` beside a fill that lags is deferred,
    then booked once by the next `ok`."""
    run_id = _run(journal_settings, open_window)
    at = fixed_clock() - timedelta(minutes=30)
    _order(
        journal_settings,
        run_id,
        "tp-lag",
        SPY,
        "SPY",
        5.0,
        events=("pending", "accepted"),
        fill=False,
        at=at,
    )
    fake.submit(OrderRequest("tp-lag", "SPY", Side.BUY, quantity=5.0))
    fake.apply("tp-lag", PartialFill(2.0, PRICE))  # booked at the broker, not journaled
    fake.activities = [_fee("f1", S, -0.02)]
    fake.extra_cash = -0.02

    deferred = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)
    assert deferred.status is not None
    assert deferred.lagging_ids == ("tp-lag",)
    assert ("deferred fee", -0.02, None) in [
        (a.reason, a.difference, a.security_id) for a in deferred.allowed
    ]
    assert _adjustments(journal_settings, open_window) == []

    # close the lag: journal the fill past the deferred stamp and let the broker match
    fixed_clock.advance(minutes=5)
    _journal_fill(journal_settings, "tp-lag", 2.0, at=fixed_clock(), accept=False)
    fake.extra_cash = -0.02  # the 2-share buy is already in the fake's cash; only the fee extra
    settled = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)
    assert settled.status == OK
    assert [a.broker_activity_id for a in _adjustments(journal_settings, open_window)] == ["f1"]


def test_a_superseded_fill_is_hidden_and_not_counted_in_fills_since_base(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """(item 6, Q11/S-NIT4) A superseded fill is hidden by the accessor, so it never
    enters `fills_since_base`: the ledger is read from `fills_for`, which drops the
    superseded row and counts only the live one."""
    run_id = _run(journal_settings, open_window)
    at = fixed_clock() - timedelta(minutes=30)
    order = _order(
        journal_settings,
        run_id,
        "tp-b",
        SPY,
        "SPY",
        4.0,
        events=("pending", "accepted"),
        fill=False,
        at=at,
    )
    (survivor,) = _append(
        journal_settings,
        FillRow(
            client_order_id="tp-b",
            filled_at=at,
            quantity=4.0,
            price=PRICE,
            price_implied=True,
            broker_fill_id="bf-pristine",
            source="broker_status",
            superseded_by=None,
            known_at=at,
            ingested_at=at,
        ),
    )
    _append(
        journal_settings,
        FillRow(
            client_order_id="tp-b",
            filled_at=at,
            quantity=4.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id="bf-old",
            source="broker_feed",
            superseded_by=survivor,  # hidden by the accessor
            known_at=at,
            ingested_at=at,
        ),
    )
    with open_read_only(journal_settings) as conn:
        live = fills_for(conn, client_order_ids=["tp-b"])
        assert [f.fill.fill_id for f in live] == [survivor]
        ledger = from_journal(
            live,
            [order],
            (),
            live_actions_as_of(conn, fixed_clock()),
            None,
            open_window.starting_cash,
            S,
            window_id=open_window.window_id,
            quantity_tolerance=1e-6,
        )
    assert ledger.fills_since_base == 1  # the superseded row was never counted


def test_a_booked_stock_dividend_cash_in_lieu_lands_exactly_on_the_whole_share(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1627 (qa fix 1): the booked fraction must not leave the ledger a sub-ulp
    share below the whole. 4065 x 1.0085's #127 example generalised: 4065 shares x
    a 1.0077 split is exactly 4096.3005, so the `CIL` books -0.30050000000028376
    (the float remainder). Applying that float through `from_journal` gives
    4095.9999999999995 and `round_down` 4095, stranding a share; the `cash_in_lieu`
    snap floors the exact `Fraction` position to 4096, so `from_journal` gives
    exactly 4096.0 and `round_down(..., 0) == 4096`. A second `from_journal` still
    books nothing new (one `cash_in_lieu` row, idempotent)."""
    run_id = _run(journal_settings, open_window)
    at = BOUGHT_AT  # a whole session before the ex-date S, so the split applies
    order = _order(
        journal_settings, run_id, "tp-sd", "SEC_A", "AAA", 4065.0, events=(), fill=False, at=at
    )
    _append(
        journal_settings,
        FillRow(
            client_order_id="tp-sd",
            filled_at=at,
            quantity=4065.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id="bf-sd",
            source="broker_feed",
            superseded_by=None,
            known_at=at,
            ingested_at=at,
        ),
    )
    _split(journal_settings, "SEC_A", 1.0077, CUT - timedelta(hours=1))  # ex_date = S
    exact_fraction = Fraction(10077, 10000) * 4065
    removed = float(exact_fraction) - 4096.0
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=S,
            kind="cash_in_lieu",
            security_id="SEC_A",
            quantity=-removed,
            cash=30.05,
            broker_activity_id="act-cil",
            known_at=fixed_clock(),
            ingested_at=fixed_clock(),
        ),
    )

    with open_read_only(journal_settings) as conn:
        live = fills_for(conn, client_order_ids=["tp-sd"])
        ledgers = from_journal(
            live,
            [order],
            adjustments_for(conn, window_id=open_window.window_id),
            live_actions_as_of(conn, fixed_clock()),
            None,
            open_window.starting_cash,
            S,
            window_id=open_window.window_id,
            quantity_tolerance=1e-6,
        )

    assert ledgers.positions["SEC_A"] == 4096.0  # exactly, never 4095.9999999999995
    assert round_down(ledgers.positions["SEC_A"], 0) == 4096
    assert [
        (row.kind, row.broker_activity_id) for row in _adjustments(journal_settings, open_window)
    ] == [("cash_in_lieu", "act-cil")]  # booked once, never twice


def _stock_dividend_journal(
    settings: Settings,
    window: PaperWindowRow,
    quantity: float,
    at: datetime,
    extra_splits: Sequence[tuple[date, float]] = (),
    later_fill: tuple[date, float, float] | None = None,
) -> None:
    """A buy of `quantity` shares at `at`, a stored 1.0077 stock dividend with
    ex_date S (#1627), any `extra_splits` of `(ex_date, ratio)` after it, and an
    optional `later_fill` of `(quantity, price, filled_at)` after the ex-date."""
    run_id = _run(settings, window)
    _order(settings, run_id, "tp-sd", "SEC_A", "AAA", quantity, events=(), fill=False, at=at)
    _append(
        settings,
        FillRow(
            client_order_id="tp-sd",
            filled_at=at,
            quantity=quantity,
            price=PRICE,
            price_implied=False,
            broker_fill_id="bf-sd",
            source="broker_feed",
            superseded_by=None,
            known_at=at,
            ingested_at=at,
        ),
    )
    _split(settings, "SEC_A", 1.0077, CUT - timedelta(hours=1))
    for ex_date, ratio in extra_splits:
        _fact(
            settings,
            "corporate_actions",
            security_id="SEC_A",
            action_type="split",
            ex_date=ex_date,
            ratio_or_amount=ratio,
            known_at=CUT - timedelta(hours=1),
            ingested_at=CUT - timedelta(hours=1),
            source="alpaca",
            provenance="action",
        )
    if later_fill is not None:
        fill_quantity, fill_price, fill_at = later_fill
        _order(
            settings,
            run_id,
            "tp-late",
            "SEC_A",
            "AAA",
            fill_quantity,
            events=(),
            fill=False,
            at=fill_at,
        )
        _append(
            settings,
            FillRow(
                client_order_id="tp-late",
                filled_at=fill_at,
                quantity=fill_quantity,
                price=fill_price,
                price_implied=False,
                broker_fill_id="bf-late",
                source="broker_feed",
                superseded_by=None,
                known_at=fill_at,
                ingested_at=fill_at,
            ),
        )
    _append(
        settings,
        AdjustmentRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=S,
            kind="cash_in_lieu",
            security_id="SEC_A",
            quantity=-float(
                Fraction(10077, 10000) * quantity - math.floor(Fraction(10077, 10000) * quantity)
            ),
            cash=30.05,
            broker_activity_id="act-cil",
            known_at=at,
            ingested_at=at,
        ),
    )


def test_a_cash_in_lieu_rounds_down_before_a_later_split_on_the_name(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1627 (fix 1): the round-down lands at the `cash_in_lieu` row's own point,
    so a later split acts on the whole shares it left. 4065 x 1.0077 = 4096.3005
    floors to 4096, and a later 2:1 split leaves 8192, not 8192.601."""
    _stock_dividend_journal(
        journal_settings, open_window, 4065.0, BOUGHT_AT, extra_splits=[(date(2026, 10, 15), 2.0)]
    )

    with open_read_only(journal_settings) as conn:
        ledgers = from_journal(
            fills_for(conn),
            orders_for(conn, window_id=open_window.window_id),
            adjustments_for(conn, window_id=open_window.window_id),
            live_actions_as_of(conn, fixed_clock()),
            None,
            open_window.starting_cash,
            date(2026, 10, 15),
            window_id=open_window.window_id,
            quantity_tolerance=1e-6,
        )

    assert ledgers.positions["SEC_A"] == 8192.0
    assert round_down(ledgers.positions["SEC_A"], 0) == 8192


def test_a_second_stock_dividend_on_the_name_books_both(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1627 (fix 1, safety's 111.3): a second stock dividend on the same name books
    its own `cash_in_lieu` and both round-downs land, each at its own ex-date."""
    run_id = _run(journal_settings, open_window)
    _order(
        journal_settings,
        run_id,
        "tp-sd",
        "SEC_A",
        "AAA",
        4065.0,
        events=(),
        fill=False,
        at=BOUGHT_AT,
    )
    _append(
        journal_settings,
        FillRow(
            client_order_id="tp-sd",
            filled_at=BOUGHT_AT,
            quantity=4065.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id="bf-sd",
            source="broker_feed",
            superseded_by=None,
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    _split(journal_settings, "SEC_A", 1.0077, CUT - timedelta(hours=1))
    _fact(
        journal_settings,
        "corporate_actions",
        security_id="SEC_A",
        action_type="split",
        ex_date=date(2026, 10, 20),
        ratio_or_amount=1.0235,
        known_at=CUT - timedelta(hours=1),
        ingested_at=CUT - timedelta(hours=1),
        source="alpaca",
        provenance="action",
    )
    # the fraction the first dividend removes (0.3005), then the second: the
    # round-down's 4096 whole x 1.0235 = 4192.256, so 0.256.
    first = Fraction(10077, 10000) * 4065
    second = Fraction(math.floor(first)) * Fraction(10235, 10000)
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=S,
            kind="cash_in_lieu",
            security_id="SEC_A",
            quantity=-float(first - math.floor(first)),
            cash=30.05,
            broker_activity_id="act-1",
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            session=date(2026, 10, 20),
            kind="cash_in_lieu",
            security_id="SEC_A",
            quantity=-float(second - math.floor(second)),
            cash=90.5,
            broker_activity_id="act-2",
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )

    with open_read_only(journal_settings) as conn:
        ledgers = from_journal(
            fills_for(conn),
            orders_for(conn, window_id=open_window.window_id),
            adjustments_for(conn, window_id=open_window.window_id),
            live_actions_as_of(conn, fixed_clock()),
            None,
            open_window.starting_cash,
            date(2026, 10, 20),
            window_id=open_window.window_id,
            quantity_tolerance=1e-6,
        )

    assert ledgers.positions["SEC_A"] == float(math.floor(second))
    assert [
        (row.kind, row.broker_activity_id) for row in _adjustments(journal_settings, open_window)
    ] == [
        ("cash_in_lieu", "act-1"),
        ("cash_in_lieu", "act-2"),
    ]


def test_a_fractional_fill_on_the_cash_in_lieu_session_is_not_rounded_down(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> None:
    """#1627 (fix 1, the ordering): the `cash_in_lieu` row is applied after that
    session's split and **before** its fills, so a fractional fill on the CIL's
    own session (the run's session S: the CIL books at step 4 and the rebalance's
    fractional fill is stated later the same session, `alpaca.quantity_decimals=9`)
    keeps its fraction — the round-down rounds the whole shares held at its own
    point, not the total position at `through`. Ranking the CIL after the day's
    fills floors this 0.5 away (`removes 0.8005 shares, not 0.3005...`)."""
    _stock_dividend_journal(
        journal_settings,
        open_window,
        4065.0,
        BOUGHT_AT,
        later_fill=(0.5, PRICE, datetime(2026, 10, 1, 15, 0, tzinfo=UTC)),
    )

    with open_read_only(journal_settings) as conn:
        ledgers = from_journal(
            fills_for(conn),
            orders_for(conn, window_id=open_window.window_id),
            adjustments_for(conn, window_id=open_window.window_id),
            live_actions_as_of(conn, fixed_clock()),
            None,
            open_window.starting_cash,
            date(2026, 10, 5),
            window_id=open_window.window_id,
            quantity_tolerance=1e-6,
        )

    assert ledgers.positions["SEC_A"] == 4096.5  # 4096 whole + the same-session 0.5
    assert round_down(ledgers.positions["SEC_A"], 0) == 4096


def test_a_stock_dividend_cash_in_lieu_is_booked_once_across_two_reconciles(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1627 (fix 4): the `cash_in_lieu` books once by its broker id, like a
    `fee_cash`. 4065 x 1.0077 = 4096.3005, so the broker holds floor = 4096 and
    the `CIL` is worth 0.3005 x (the pre-ex close 100 / 1.0077). A second
    reconciliation reads the same id — now inside the recorded set — and books
    nothing more, so the journal holds exactly one row."""
    reference = _reference(journal_settings, SPY)
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 4065.0})
    _split(journal_settings, SPY, 1.0077, CUT - timedelta(hours=1))
    in_lieu = 0.3005 * (reference / 1.0077)
    fake.extra_quantity = {"SPY": 31.0}  # 4096 whole, the floor of 4096.3005
    fake.extra_cash = in_lieu
    fake.activities = [_CIL_ACTIVITY("act-cil", in_lieu)]

    first = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert first.status == OK
    assert [
        (a.kind, a.broker_activity_id) for a in _adjustments(journal_settings, open_window)
    ] == [("cash_in_lieu", "act-cil")]

    fixed_clock.advance(minutes=5)
    second = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert second.status == OK
    assert second.adjustments == ()  # never booked twice
    assert [
        (a.kind, a.broker_activity_id) for a in _adjustments(journal_settings, open_window)
    ] == [("cash_in_lieu", "act-cil")]


def test_a_correct_cash_in_lieu_posted_late_uses_the_pre_ex_reference(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1627 (fix 2): the reference read is the close of `previous_session(ex_date)`,
    not the close before the reconciliation's own session. The name is held from
    2026-09-22, the ex-date is 2026-09-23 (bar 120.00 at 2026-09-22) and the CIL
    posts at S, 2026-10-01 (bar 80.00 at 2026-09-30, the close before S): a
    correct CIL at ratio 1.15 values the 0.75-share remainder at 120 / 1.15 and
    reconciles. If `_stock_dividend_references` read `previous_session(S)`, it
    would divide 80 instead and this CIL would be off by half and halt."""
    run_id = _run(journal_settings, open_window)
    _order(
        journal_settings,
        run_id,
        "tp-hold-1",
        SPY,
        "SPY",
        4065.0,
        at=datetime(2026, 9, 22, 14, 0, tzinfo=UTC),
    )
    fake.submit(OrderRequest("tp-hold-1", "SPY", Side.BUY, quantity=4065.0))
    fake.simulate_fill("tp-hold-1")
    _fact(
        journal_settings,
        "corporate_actions",
        security_id=SPY,
        action_type="split",
        ex_date=date(2026, 9, 23),
        ratio_or_amount=1.15,
        known_at=CUT - timedelta(hours=1),
        ingested_at=CUT - timedelta(hours=1),
        source="alpaca",
        provenance="action",
    )
    _bar_at(journal_settings, SPY, date(2026, 9, 22), 120.0, CUT - timedelta(hours=2))
    _bar_at(journal_settings, SPY, date(2026, 9, 23), 91.0, CUT - timedelta(hours=2))
    _bar_at(journal_settings, SPY, date(2026, 9, 30), 80.0, CUT - timedelta(hours=1))
    expected = 0.75 * (120.0 / 1.15)
    fake.extra_quantity = {"SPY": 609.0}  # floor(4065 x 1.15) - 4065 = 4674 - 4065
    fake.extra_cash = expected
    fake.activities = [_CIL_ACTIVITY("act-cil", expected)]

    result = _reconcile(journal_settings, fake, open_window, fixed_clock)

    assert result.status == OK
    assert [
        (a.kind, a.broker_activity_id) for a in _adjustments(journal_settings, open_window)
    ] == [("cash_in_lieu", "act-cil")]


def test_an_underpaid_cash_in_lieu_posted_late_halts(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """#1627 (fix 2): the same pre-ex reference decides, so a `CIL` paid at 85% of
    the expected cash (0.75 x 120 / 1.15) at S is off beyond the relative
    tolerance and halts, booking nothing."""
    run_id = _run(journal_settings, open_window)
    _order(
        journal_settings,
        run_id,
        "tp-hold-1",
        SPY,
        "SPY",
        4065.0,
        at=datetime(2026, 9, 22, 14, 0, tzinfo=UTC),
    )
    fake.submit(OrderRequest("tp-hold-1", "SPY", Side.BUY, quantity=4065.0))
    fake.simulate_fill("tp-hold-1")
    _fact(
        journal_settings,
        "corporate_actions",
        security_id=SPY,
        action_type="split",
        ex_date=date(2026, 9, 23),
        ratio_or_amount=1.15,
        known_at=CUT - timedelta(hours=1),
        ingested_at=CUT - timedelta(hours=1),
        source="alpaca",
        provenance="action",
    )
    _bar_at(journal_settings, SPY, date(2026, 9, 22), 120.0, CUT - timedelta(hours=2))
    _bar_at(journal_settings, SPY, date(2026, 9, 23), 91.0, CUT - timedelta(hours=2))
    _bar_at(journal_settings, SPY, date(2026, 9, 30), 80.0, CUT - timedelta(hours=1))
    expected = 0.75 * (120.0 / 1.15)
    fake.extra_quantity = {"SPY": 609.0}
    fake.extra_cash = expected * 0.85
    fake.activities = [_CIL_ACTIVITY("act-cil", expected * 0.85)]

    with pytest.raises(ReconciliationError):
        _reconcile(journal_settings, fake, open_window, fixed_clock)


def test_cash_within_the_open_allowances_carries_the_decimal_difference(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """S-NIT1: the `cash within the open allowances` record carries the Decimal-derived
    difference; reverting to the pre-fee float gap leaves residue at a six-figure
    balance (100000.10 vs 100000.00 -> 0.1, never 0.09999999999404023)."""
    run_id = _run(journal_settings, open_window)
    _order(journal_settings, run_id, "tp-pend", MTUM, "MTUM", 3.0, events=("pending",), fill=False)
    fake.submit(OrderRequest("tp-pend", "MTUM", Side.BUY, quantity=3.0))
    fake.extra_cash = -0.1  # a $0.10 gap inside the open allowance, at a six-figure balance

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, run_id=run_id)

    by_reason = {a.reason: a.difference for a in result.allowed}
    assert "cash within the open allowances" in by_reason
    assert (
        by_reason["cash within the open allowances"] == -0.1
    )  # Decimal-derived, not the float residue


# --- the window-1 case to item 6's letter (Q5, S3) ----------------------------


def _window_1_row(window_id: int | None, started_at: datetime) -> PaperWindowRow:
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=100_008.90,
        starting_equity=100_008.90,
        code_version="t",
        started_at=started_at,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=started_at,
        ingested_at=started_at,
    )
    return replace(row, window_id=window_id)


def test_window_1_resume_books_its_two_start_date_fees_under_the_fallback(
    journal_settings: Settings,
    fake: BookedFake,
    fixed_clock: FixedClock,
) -> None:
    """Item 6's window-1 shape (Q5, S3): a window opened with `starting_cash`
    100008.90 and a null seen-set, a pre-T183 `ok` base (no recorded `activities`)
    that day, two `FEE` rows dated the start day, broker 100008.88, key 0.01, zero
    fills. Both are booked `fee_cash` once with the reconciliation's `known_at`.
    Dates are fitted to the fixture session S (2026-10-01); the balances, the key
    and the zero-fill ledger are item 6's exact figures."""
    started_at = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)
    window_id = _append(journal_settings, _window_1_row(None, started_at))[0]
    window = _window_1_row(window_id, started_at)
    start = date(2026, 9, 30)  # the window's New York start date
    # a pre-T183 ok base that day: broker 100008.90, no recorded activities
    _append(
        journal_settings,
        ReconciliationRow(
            window_id=window_id,  # type: ignore[arg-type]
            at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
            status="ok",
            broker_cash=100_008.90,
            mismatches_json=json.dumps({"mismatches": [], "lagging": [], "pending": []}),
            known_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
            ingested_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        ),
    )
    fake.activities = [_fee("f-1", start, -0.01), _fee("f-2", start, -0.01)]
    fake.extra_cash = 8.88  # the fake's baseline is 100000.00; the broker is at 100008.88

    result = _reconcile(journal_settings, fake, window, fixed_clock)

    assert result.status == OK
    booked = _adjustments(journal_settings, window)
    assert [(a.kind, a.cash, a.broker_activity_id, a.session) for a in booked] == [
        ("fee_cash", -0.01, "f-1", start),
        ("fee_cash", -0.01, "f-2", start),
    ]
    recon_known = _rows(journal_settings, window)[-1].known_at
    assert all(a.known_at == recon_known for a in booked)
    payload = json.loads(result.mismatches_json)
    assert {a["activity_id"] for a in payload["activities"]} == {"f-1", "f-2"}

    # a second read journals nothing and the ledger cash is now 100008.88
    fixed_clock.advance(minutes=5)
    again = _reconcile(journal_settings, fake, window, fixed_clock)
    assert again.status == OK and again.adjustments == ()
    assert len(_adjustments(journal_settings, window)) == 2
    # the second-read ledger is 100008.88 exactly: the reconcile re-based on the ok
    # row and the two fees it already holds (known at that base) are not re-applied
    with open_read_only(journal_settings) as conn:
        state = _journal_state(conn, window.window_id, fixed_clock())
        ledger = _ledger(
            state,
            live_actions_as_of(conn, fixed_clock()),
            window,
            S,
            FROZEN.reconcile_quantity_tolerance,
        )
    assert Decimal(repr(ledger.cash)) == Decimal("100008.88")


def test_window_1_no_activity_twin_still_halts(
    journal_settings: Settings, fake: BookedFake, fixed_clock: FixedClock
) -> None:
    """Item 6's window-1 twin (Q5): the same -0.02 with no activity read is still a
    `cash` mismatch, never inferred from the difference."""
    window_id = _append(
        journal_settings, _window_1_row(None, datetime(2026, 9, 30, 14, 0, tzinfo=UTC))
    )[0]
    window = _window_1_row(window_id, datetime(2026, 9, 30, 14, 0, tzinfo=UTC))
    fake.activities = []
    fake.extra_cash = 8.88  # the broker is 100008.88, the ledger 100008.90, no read row
    with pytest.raises(ReconciliationError, match="cash"):
        _reconcile(journal_settings, fake, window, fixed_clock)


# --- equal-amount dividend groups (#1625, #1613) -----------------------------


def _listed(security_id: str, verdict: str, amount: float = 0.5) -> ReviewedGroup:
    return ReviewedGroup(
        security_id=security_id,
        ex_date=S,
        amount=amount,
        verdict=verdict,  # type: ignore[arg-type]
        retract_source_action_id="dup-b" if verdict == "duplicate" else "",
        ticker="",
        evidence="test",
    )


def _pair(settings: Settings, security_id: str, amount: float = 0.5) -> None:
    for action_id in ("dup-a", "dup-b"):
        _dividend(
            settings, security_id, amount, CUT - timedelta(days=1), source_action_id=action_id
        )


def _explain_at_cut(settings: Settings, window: PaperWindowRow) -> Any:
    with open_read_only(settings) as conn:
        return explanations_as_of(
            conn,
            window,
            S,
            as_of=CUT,
            settings=settings,
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )


@pytest.mark.parametrize(
    ("reviewed", "due"),
    [
        ([], {MTUM: 2.0}),  # unlisted: flagged, never summed
        ([_listed(SPY, "duplicate")], {SPY: 5.0, MTUM: 2.0}),  # pending: counted once
        ([_listed(SPY, "distinct")], {SPY: 10.0, MTUM: 2.0}),  # two declared payments
    ],
)
def test_an_equal_amount_pair_is_counted_by_the_reviewed_list(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    monkeypatch: pytest.MonkeyPatch,
    reviewed: list[ReviewedGroup],
    due: dict[str, float],
) -> None:
    monkeypatch.setattr(reconcile_run, "reviewed_groups", lambda: reviewed)
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0, (MTUM, "MTUM"): 4.0})
    _pair(journal_settings, SPY)
    _dividend(journal_settings, MTUM, 0.5, CUT - timedelta(days=1))  # a plain dividend
    found = _explain_at_cut(journal_settings, open_window)
    assert found.dividends == pytest.approx(due)
    if reviewed:
        assert found.dividend_findings == {}
    else:
        [(name, detail)] = found.dividend_findings.items()
        assert name == SPY
        assert "dup-a" in detail and "dup-b" in detail and str(S) in detail


def test_an_unlisted_pair_on_a_held_name_is_a_data_mismatch_whatever_the_cash(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails closed: neither one payment nor two is assumed; the book halts on
    a data finding the owner settles in the reviewed list."""
    monkeypatch.setattr(reconcile_run, "reviewed_groups", lambda: [])
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _pair(journal_settings, SPY)
    fake.extra_cash = 5.0  # the broker paid one
    with pytest.raises(ReconciliationError, match="unexplained_dividend_group"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)
    (row,) = _rows(journal_settings, open_window)
    assert row.status == MISMATCH
    assert _adjustments(journal_settings, open_window) == []


def test_a_pending_duplicate_reconciles_on_one_payment(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile_run, "reviewed_groups", lambda: [_listed(SPY, "duplicate")])
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _pair(journal_settings, SPY)
    fake.extra_cash = 5.0
    result = _reconcile(journal_settings, fake, open_window, fixed_clock)
    assert result.status == OK
    (adjustment,) = _adjustments(journal_settings, open_window)
    assert (adjustment.kind, adjustment.cash) == ("dividend_cash", pytest.approx(5.0))


def test_an_unlisted_pair_on_a_name_not_held_is_no_finding(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile_run, "reviewed_groups", lambda: [])
    _hold(journal_settings, fake, open_window, {(SPY, "SPY"): 10.0})
    _pair(journal_settings, MTUM)
    found = _explain_at_cut(journal_settings, open_window)
    assert found.dividends == {}
    assert found.dividend_findings == {}


def test_signed_book_fails_closed_and_reads_a_registered_long_only_hypothesis(
    journal_settings: Settings, frozen_window: PaperWindowRow
) -> None:
    from execution.test_run_core import registered_hypothesis

    known = replace(frozen_window, hypothesis_id=registered_hypothesis(journal_settings))
    with open_for_write(journal_settings) as conn:
        assert _REAL_SIGNED_BOOK(conn, known) is False
        # No such hypothesis: unreadable reads as signed, so the book halts.
        assert _REAL_SIGNED_BOOK(conn, replace(frozen_window, hypothesis_id=9_999)) is True


def test_signed_book_reads_a_long_short_hypothesis_as_signed(
    journal_settings: Settings, frozen_window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unsafe direction: a hypothesis freezing `portfolio.side = long_short` reads
    True (the key and value `headroom._side` reads), so its restricted names halt."""
    from execution.test_run_core import registered_hypothesis

    known = replace(frozen_window, hypothesis_id=registered_hypothesis(journal_settings))
    monkeypatch.setattr(
        reconcile_run, "frozen_values", lambda _hypothesis: {"portfolio.side": "long_short"}
    )
    with open_for_write(journal_settings) as conn:
        assert _REAL_SIGNED_BOOK(conn, known) is True


# --- auto-acceptance and true-up semantics (T283a; #2266 items 5, 6, 8, 9) -----------


def _events(settings: Settings, window: PaperWindowRow) -> list[Any]:
    with open_read_only(settings) as conn:
        return break_events_for(conn, window.window_id or 0)


def _credit(fake: BookedFake, activity_id: str, amount: float, day: date = S) -> None:
    """A not-due `MA` credit tagged to SPY: a name cash break of `amount`."""
    fake.activities = [
        *fake.activities,
        CashActivity(activity_id, "MA", day, "executed", amount, "SPY"),
    ]
    fake.extra_cash += amount


def test_every_break_auto_accepted_is_ok_and_engages_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 16 Auto-acceptance: $10 on a $100,000 book writes `opened`, `auto_accepted`
    and one `auto_true_up`, with no restriction; the row is `ok` (T282d's rule for
    `breaks` never applies) and the command raises nothing and engages no switch."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    _credit(fake, "tiny-credit", 10.0)

    result = _command(journal_settings, fake, fixed_clock)

    assert result.status == OK
    (row,) = _rows(journal_settings, frozen_window)
    assert row.status == OK and row.broker_cash == pytest.approx(100_010.0 - 10 * PRICE)
    events = _events(journal_settings, frozen_window)
    assert [(e.event, e.material, e.dollar_size) for e in events] == [
        ("opened", False, 10.0),
        ("auto_accepted", False, 10.0),
    ]
    assert events[0].break_id == events[1].break_id
    (true_up,) = [
        a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"
    ]
    assert (true_up.security_id, true_up.cash, true_up.quantity) == (SPY, 10.0, None)
    assert true_up.broker_activity_id == "tiny-credit"
    assert json.loads(events[1].adjustment_ids_json or "") == [true_up.adjustment_id]
    terms = json.loads(true_up.explanation_json or "")
    assert (terms["size"], terms["prior_auto_accepted"], terms["cumulative_auto_accepted"]) == (
        10.0,
        0.0,
        10.0,
    )
    assert terms["break_materiality_usd"] == FROZEN.break_materiality_usd
    with open_read_only(journal_settings) as conn:
        assert kill_switch_events_for(conn, frozen_window.window_id or 0) == []
        assert (
            break_rules.restricted_names(break_events_for(conn, frozen_window.window_id or 0))
            == frozenset()
        )

    # The next reconciliation sees the activity as booked and the ledger as the broker.
    fixed_clock.advance(minutes=5)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == OK
    assert len(_events(journal_settings, frozen_window)) == 2


def test_a_quantity_break_is_adopted_at_the_brokers_absolute_quantity(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 0.04  # about $10 at the fixture reference price

    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == OK

    (true_up,) = [
        a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"
    ]
    assert (true_up.security_id, true_up.cash, true_up.broker_activity_id) == (SPY, None, None)
    assert true_up.quantity == pytest.approx(0.04)
    terms = json.loads(true_up.explanation_json or "")
    assert (terms["broker_quantity"], terms["ledger_quantity"]) == (10.04, 10.0)
    with open_read_only(journal_settings) as conn:
        state = _journal_state(conn, frozen_window.window_id or 0, fixed_clock())
        ledger = _ledger(
            state,
            live_actions_as_of(conn, CUT),
            frozen_window,
            S,
            FROZEN.reconcile_quantity_tolerance,
        )
    assert ledger.positions[SPY] == 10.04
    fixed_clock.advance(minutes=5)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == OK


def test_a_late_split_with_an_earlier_ex_date_does_not_change_an_adopted_quantity(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 9: a true-up sets the figure. A split row that lands later with an earlier
    `ex_date` is already in the broker's quantity and is never applied on top; a split
    with a later `ex_date` still is."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    fake.extra_quantity["SPY"] = 0.04
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == OK
    later = next_session(S)
    stamp = session_close(S) + timedelta(hours=2)
    for ex_date, ratio in ((S - timedelta(days=1), 2.0), (later, 3.0)):
        _fact(
            journal_settings,
            "corporate_actions",
            security_id=SPY,
            action_type="split",
            ex_date=ex_date,
            ratio_or_amount=ratio,
            known_at=stamp,
            ingested_at=stamp,
            source="alpaca",
            provenance="action",
        )

    def quantity(through: date) -> float:
        with open_read_only(journal_settings) as conn:
            state = _journal_state(conn, frozen_window.window_id or 0, session_close(through))
            actions = live_actions_as_of(conn, session_close(through))
            return _ledger(
                state, actions, frozen_window, through, FROZEN.reconcile_quantity_tolerance
            ).positions[SPY]

    assert quantity(S) == pytest.approx(10.04)  # the earlier split is not applied on top
    assert quantity(later) == pytest.approx(10.04 * 3.0)  # the later one is


def test_a_break_still_open_keeps_breaks_while_another_is_accepted(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """T283a's pinned pair: a material break keeps the `breaks` status (and, since
    T282d, restricts its name and engages nothing); a small one beside it is accepted
    and its cash stays inside the base, the open one's outside it."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0, (MTUM, "MTUM"): 10.0})
    _credit(fake, "tiny-credit", 10.0)
    fake.activities = [
        *fake.activities,
        CashActivity("big-credit", "MA", S, "executed", 30.0, "MTUM"),
    ]
    fake.extra_cash += 30.0

    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"

    (row,) = _rows(journal_settings, frozen_window)
    payload = json.loads(row.mismatches_json or "")
    assert row.broker_cash == pytest.approx(100_040.0 - 20 * PRICE - 30.0)
    assert payload["raw_broker_cash"] == pytest.approx(100_040.0 - 20 * PRICE)
    ids = {a["activity_id"] for a in payload["activities"]}
    assert "tiny-credit" in ids and "big-credit" not in ids
    assert [
        (e.security_id, e.event, e.material) for e in _events(journal_settings, frozen_window)
    ] == [
        (MTUM, "opened", True),
        (SPY, "opened", False),
        (SPY, "auto_accepted", False),
    ]
    assert [a.broker_activity_id for a in _adjustments(journal_settings, frozen_window)] == [
        "tiny-credit"
    ]


def test_the_third_ten_dollars_on_one_name_is_material_under_the_cumulative_bound(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 5: prior plus this one, so a leak of a few dollars a session cannot be
    accepted forever. $10, then $10 (cumulative $20) are accepted; the third makes
    $30 against the $25 bound and is opened material, with no adjustment."""
    _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    day = S
    statuses = []
    for n in range(3):
        if n:
            day = next_session(day)
            fixed_clock.now = session_close(day) + timedelta(hours=2)
        _credit(fake, f"leak-{n}", 10.0, day)
        statuses.append(
            _reconcile(journal_settings, fake, frozen_window, fixed_clock, session=day).status
        )

    assert statuses == [OK, OK, "breaks"]
    events = _events(journal_settings, frozen_window)
    assert [e.event for e in events] == ["opened", "auto_accepted"] * 2 + ["opened"]
    assert events[-1].material is True
    booked = [a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"]
    assert [a.broker_activity_id for a in booked] == ["leak-0", "leak-1"]
    assert json.loads(booked[1].explanation_json or "")["cumulative_auto_accepted"] == 20.0


def _lag(settings: Settings, fake: BookedFake, run_id: int, clock: FixedClock) -> None:
    """Leave one acknowledged order partly filled at the broker and unfilled in the
    journal: the next reconciliation is `fills_lagging`."""
    _order(
        settings,
        run_id,
        "tp-lag",
        MTUM,
        "MTUM",
        5.0,
        events=("pending", "accepted"),
        fill=False,
        at=clock() - timedelta(minutes=30),
    )
    fake.submit(OrderRequest("tp-lag", "MTUM", Side.BUY, quantity=5.0))
    fake.apply("tp-lag", PartialFill(2.0, PRICE))


def test_a_deferred_small_break_is_accepted_by_the_next_eligible_reconciliation(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 6: beside a lagging fill the immaterial break is written `opened`, books no
    adjustment and restricts nothing (the lag keeps T281's halt on the run). The next
    `ok` reconciliation re-tests it and writes `auto_accepted` with its `auto_true_up`."""
    run_id = _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    _lag(journal_settings, fake, run_id, fixed_clock)
    _credit(fake, "deferred", 10.0)

    with pytest.raises(ReconciliationError):
        _reconcile(journal_settings, fake, frozen_window, fixed_clock)

    assert _rows(journal_settings, frozen_window)[-1].status == FILLS_LAGGING
    assert [(e.event, e.material) for e in _events(journal_settings, frozen_window)] == [
        ("opened", False)
    ]
    assert [
        a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"
    ] == []
    with open_read_only(journal_settings) as conn:
        events = break_events_for(conn, frozen_window.window_id or 0)
        assert break_rules.restricted_names(events) == frozenset()

    # The lag clears: the fill is journaled, the order terminal at the broker.
    fixed_clock.advance(minutes=5)
    _journal_fill(journal_settings, "tp-lag", 2.0, at=fixed_clock(), accept=False)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == OK

    events = _events(journal_settings, frozen_window)
    assert [e.event for e in events] == ["opened", "auto_accepted"]
    assert events[0].break_id == events[1].break_id
    assert [
        a.broker_activity_id
        for a in _adjustments(journal_settings, frozen_window)
        if a.kind == "auto_true_up"
    ] == ["deferred"]


def test_a_deferred_small_break_that_grows_past_materiality_stays_open_and_material(
    journal_settings: Settings,
    fake: BookedFake,
    frozen_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """PR #2267 pass 6: re-tested at the eligible reconciliation, the break is $40: it is
    `observed` material, restricts its name and books no automatic adjustment."""
    run_id = _hold(journal_settings, fake, frozen_window, {(SPY, "SPY"): 10.0})
    _lag(journal_settings, fake, run_id, fixed_clock)
    _credit(fake, "deferred", 10.0)
    with pytest.raises(ReconciliationError):
        _reconcile(journal_settings, fake, frozen_window, fixed_clock)
    fixed_clock.advance(minutes=5)
    _journal_fill(journal_settings, "tp-lag", 2.0, at=fixed_clock(), accept=False)
    _credit(fake, "grown", 30.0)

    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"

    events = _events(journal_settings, frozen_window)
    assert [(e.event, e.material, e.dollar_size) for e in events] == [
        ("opened", False, 10.0),
        ("observed", True, 40.0),
    ]
    assert [
        a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"
    ] == []
    with open_read_only(journal_settings) as conn:
        assert break_rules.restricted_names(
            break_events_for(conn, frozen_window.window_id or 0)
        ) == {SPY}

    # And once material, a break that shrinks is not auto-accepted ("material from then on").
    fake.activities = [a for a in fake.activities if a.activity_id != "grown"]
    fake.extra_cash -= 30.0
    fixed_clock.advance(minutes=5)
    assert _reconcile(journal_settings, fake, frozen_window, fixed_clock).status == "breaks"
    assert [
        a for a in _adjustments(journal_settings, frozen_window) if a.kind == "auto_true_up"
    ] == []

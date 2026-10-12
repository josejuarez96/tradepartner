"""Reconciliation with store explanations (Phase 4 plan T61; spec req 6 and the
"Reconciliation on the fake" acceptance cases that need no run)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import polars as pl
import pytest

from tradepartner.adapters.broker import Account, CashActivity, OrderRequest, Position, Side
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import reconcile_run, switch
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile import (
    FILLS_LAGGING,
    MISMATCH,
    OK,
    PENDING_UNRESOLVED,
    Explanations,
)
from tradepartner.execution.reconcile_run import (
    _NEW_YORK,
    NoWindowError,
    command_session,
    explanations_as_of,
    frozen_risk,
    reconcile_command,
    reconcile_now,
)
from tradepartner.store.asof import live_actions_as_of, prices_as_of
from tradepartner.store.db import insert_row, open_for_write, open_read_only
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
    fills_for,
    kill_switch_events_for,
    reconciliations_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
S = date(2026, 10, 1)  # the conftest clock's session
CUT = session_close(date(2026, 9, 30))  # close(S-1)
BOUGHT_AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)  # before every ex-date tested
PRICE = 100.0  # the fake's fill price (conftest REFERENCE_PRICE)
SPY, MTUM = "SEC_SPY", "SEC_MTUM"  # fixture names listed through 2026


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
        clock=fixed_clock, price_of=lambda _s: PRICE, auto_fill=False, account_id="PA1"
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
    settings: Settings, security_id: str, amount: float, known_at: datetime, ex_date: date = S
) -> None:
    _fact(
        settings,
        "corporate_actions",
        security_id=security_id,
        action_type="dividend",
        ex_date=ex_date,
        ratio_or_amount=amount,
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


def _reconcile(
    settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    clock: FixedClock,
    *,
    run_id: int | None = None,
    as_of: datetime | None = None,
) -> Any:
    return reconcile_now(
        settings,
        lambda: open_for_write(settings),
        fake,
        window,
        S,
        clock,
        lambda: open_for_write(settings),
        run_id,
        frozen=FROZEN,
        as_of=clock() if as_of is None else as_of,
    )


def _rows(settings: Settings, window: PaperWindowRow) -> list[ReconciliationRow]:
    with open_read_only(settings) as conn:
        return reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]


def _adjustments(settings: Settings, window: PaperWindowRow) -> list[AdjustmentRow]:
    with open_read_only(settings) as conn:
        return adjustments_for(conn, window.window_id)  # type: ignore[arg-type]


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
    _split(journal_settings, SPY, 2.0, CUT + timedelta(minutes=1))
    fake.extra_quantity["SPY"] = 10.0

    with pytest.raises(ReconciliationError, match="quantity"):
        _reconcile(journal_settings, fake, open_window, fixed_clock)

    (row,) = _rows(journal_settings, open_window)
    assert row.status == MISMATCH
    assert row.broker_cash is None
    assert [m["kind"] for m in json.loads(row.mismatches_json or "")["mismatches"]] == ["quantity"]


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
    frozen = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
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


def test_no_adapter_is_imported() -> None:
    module = Path(__file__).resolve().parents[2] / "src/tradepartner/execution/reconcile_run.py"
    source = module.read_text(encoding="utf-8")
    assert "fake_broker" not in source and "alpaca_broker" not in source


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
    return BookedFake(clock=clock, price_of=lambda _s: PRICE, auto_fill=False, account_id="PB1")


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
    row older than the booking (the journaled `broker_activity_id` is seen)."""
    _ok_base(
        journal_settings,
        open_window,
        at=session_close(_day_before(S)),
        broker_cash=100_000.0,
        activity_ids=("f1",),
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

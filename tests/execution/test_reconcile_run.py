"""Reconciliation with store explanations (Phase 4 plan T61; spec req 6 and the
"Reconciliation on the fake" acceptance cases that need no run)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import polars as pl
import pytest

from tradepartner.adapters.broker import Account, OrderRequest, Position, Side
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import reconcile_run, switch
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile import (
    FILLS_LAGGING,
    MISMATCH,
    OK,
    PENDING_UNRESOLVED,
    Explanations,
)
from tradepartner.execution.reconcile_run import (
    NoWindowError,
    command_session,
    explanations_as_of,
    frozen_risk,
    reconcile_command,
    reconcile_now,
)
from tradepartner.store.asof import prices_as_of
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

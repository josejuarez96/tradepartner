"""Reconciliation of short positions (paper plan T233; paper spec amendment
2026-10-19, #1872, items 9 and 10). Locked tests: Phase 1 of the order-path
split (Opus locks, Sonnet builds).

The line's Tests clause, as pinned here:

- **An unexplained short change halts by kind.** A buy-in, a recall and an
  external add to a held short are each a `short_external_change` mismatch on
  that name (item 9), at `compare` and through `paper reconcile`, which
  engages the switch. A change our own lagging cover explains is not one.
- **A debited and an undebited short dividend both pass.** A short's due is
  negative (-quantity x amount) and the cash band is [due, 0]; a
  `dividend_cash` row is proposed only for what the broker debits (item 10).
  Cash outside the band is a `cash` mismatch.
- **A broker long against a ledger short is a mismatch** (item 10, "with the
  sign checked").
- **Every long case unedited.** `test_reconcile.py` and `test_reconcile_run.py`
  are not touched by this task and must stay green.

Every fixture carries the account's own funding deposit (a `JNLC` dated the
day before the window's New York start date), the #1641 incident: it is inside
the base and never a mismatch.

Hand values. A short of 10 `SEC_A` opened at 100 on a 100,000 window: ledger
cash 101,000 (the sale's proceeds credited, T232), broker `AAA` -10 (Alpaca
reads a short as a negative `qty`, the 2026-10-19 sandbox probe's
`short.position_while_short`) and broker cash 101,000 (`short.account_while_short`:
cash rises by the proceeds).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pytest

from tradepartner.adapters.broker import (
    Account,
    Asset,
    CashActivity,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    PositionIntent,
    Side,
)
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ReconciliationError
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.reconcile import (
    FILLS_LAGGING,
    MISMATCH,
    OK,
    Explanations,
    JournalOpenOrder,
    Reconciliation,
    compare,
)
from tradepartner.execution.reconcile_run import reconcile_command
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    append,
    kill_switch_events_for,
    reconciliations_for,
)

# --- compare-level fixtures -------------------------------------------------

S = date(2026, 10, 7)
AT = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
STARTED = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # 09:00 ET on 2026-10-05
START_DATE = date(2026, 10, 5)  # the window's New York start date
DEPOSIT_DATE = START_DATE - timedelta(days=1)  # the funding deposit, just before
FROZEN = RiskConfig()  # quantity tolerance 1e-6; cash 0.01 (x1 with no fills since base)
PREFIX = "tp"
SYMBOLS = {"SEC_A": "AAA", "SEC_B": "BBB"}
REFERENCE = {"SEC_A": 100.0, "SEC_B": 50.0}
WINDOW = PaperWindowRow(
    window_id=1,
    hypothesis_id=1,
    first_rebalance_session=date(2026, 10, 5),
    account_id="acct-1",
    starting_cash=100_000.0,
    starting_equity=100_000.0,
    code_version="test",
    started_at=STARTED,
    frozen_json="{}",
    frozen_sha256="0" * 64,
    known_at=STARTED,
    ingested_at=STARTED,
)
SHORT_CASH = 101_000.0  # 100,000 + 10 x 100 short-sale proceeds
DEPOSIT_ID = "20261004000000000::fund0001"


def _deposit(day: date = DEPOSIT_DATE) -> CashActivity:
    """The account's own $100,000 funding `JNLC`, dated before the window's
    start date: inside the base under the fallback (#1641)."""
    return CashActivity(
        activity_id=DEPOSIT_ID,
        activity_type="JNLC",
        date=day,
        status="executed",
        net_amount=100_000.0,
    )


def _explanations(**overrides: object) -> Explanations:
    values: dict[str, object] = {
        "symbols": SYMBOLS,
        "reference_prices": REFERENCE,
        "activities": (_deposit(),),
        "activity_seen_before": START_DATE,
    }
    values.update(overrides)
    return Explanations(**values)  # type: ignore[arg-type]


def _ledger(
    cash: float = SHORT_CASH,
    *,
    long: Mapping[str, float] | None = None,
    short: Mapping[str, float] | None = None,
) -> Ledger:
    return Ledger(
        positions=dict(long or {}),
        cash=cash,
        through=S,
        short_holdings=dict({"SEC_A": 10.0} if short is None else short),
    )


def _positions(**quantities: float) -> dict[str, Position]:
    return {symbol: Position(symbol=symbol, quantity=q) for symbol, q in quantities.items()}


def _account(cash: float) -> Account:
    return Account(account_id="acct-1", cash=cash, buying_power=cash, equity=cash, as_of=AT)


def _run(
    ledger: Ledger,
    positions: Mapping[str, Position],
    cash: float,
    *,
    journal_open: Sequence[JournalOpenOrder] = (),
    explanations: Explanations | None = None,
) -> Reconciliation:
    return compare(
        ledger,
        positions,
        (),
        journal_open,
        _account(cash),
        explanations or _explanations(),
        WINDOW,
        FROZEN,
        order_id_prefix=PREFIX,
    )


def _kinds(result: Reconciliation) -> list[str]:
    return [m.kind for m in result.mismatches]


def _on(result: Reconciliation, security_id: str) -> list[str]:
    """The kinds of the mismatches naming `security_id`."""
    return [m.kind for m in result.mismatches if m.security_id == security_id]


# --- a held short reconciles ------------------------------------------------


def test_a_held_short_against_the_brokers_negative_quantity_is_ok() -> None:
    """Item 10: |broker| (-10 -> 10) against the ledger's short holding (10),
    sign checked; cash 101,000 both sides. A long of 8 `SEC_B` beside it is
    compared as today. The funding deposit is inside the base: recorded, never
    a mismatch."""
    result = _run(
        _ledger(long={"SEC_B": 8.0}),
        _positions(AAA=-10.0, BBB=8.0),
        SHORT_CASH,
    )

    assert result.status == OK
    assert _kinds(result) == []
    assert result.adjustments == ()
    assert result.broker_cash == SHORT_CASH
    assert [a.activity_id for a in result.activities] == [DEPOSIT_ID]


# --- an unexplained short change halts by kind -------------------------------


@pytest.mark.parametrize(
    ("broker_quantity", "broker_cash"),
    [
        # A buy-in of 3 at 105: broker -7, cash 101,000 - 315.
        pytest.param(-7.0, 100_685.0, id="buy_in"),
        # A recall of the whole short at 105: no position, cash 101,000 - 1,050.
        pytest.param(None, 99_950.0, id="recall"),
        # Shares added to the short that no fill of ours explains: 2 at 100.
        pytest.param(-12.0, 101_200.0, id="external_add"),
    ],
)
def test_an_unexplained_short_change_is_short_external_change(
    broker_quantity: float | None, broker_cash: float
) -> None:
    """Item 9: any change to a short our own fills do not explain is a
    `short_external_change` on that name, never `quantity`,
    `ledger_only_position` or `broker_only_position`; nothing is adopted."""
    positions = {} if broker_quantity is None else _positions(AAA=broker_quantity)

    result = _run(_ledger(), positions, broker_cash)

    assert result.status == MISMATCH
    assert _on(result, "SEC_A") == ["short_external_change"]
    (found,) = [m for m in result.mismatches if m.kind == "short_external_change"]
    assert (found.security_id, found.symbol) == ("SEC_A", "AAA")
    assert result.adjustments == ()
    assert result.broker_cash is None


def test_a_change_our_own_lagging_cover_explains_is_not_short_external_change() -> None:
    """A `buy_to_close` of 3 the broker reports filled at 105 and the journal
    has not yet collected: broker -7 and cash 101,000 - 315 = 100,685 are inside
    the lag's allowance (a buy widens the quantity up by 3 and the cash down by
    315), so the status is `fills_lagging`, not a halt."""
    row = OrderRow(
        client_order_id="tp-20261007-SEC_A-cover-1",
        decision_id=1,
        run_id=1,
        session=S,
        attempt=1,
        phase="sell",
        security_id="SEC_A",
        symbol="AAA",
        side="buy",
        quantity=3.0,
        position_side="short",
        sells_in_flight_at_submit=False,
        known_at=AT,
        ingested_at=AT,
    )
    reading = Order(
        client_order_id=row.client_order_id,
        symbol="AAA",
        side=Side.BUY,
        notional=None,
        quantity=3.0,
        status=OrderStatus.FILLED,
        submitted_at=AT,
        broker_order_id="b-cover-1",
        filled_quantity=3.0,
        filled_avg_price=105.0,
        filled_at=AT,
    )

    result = _run(
        _ledger(),
        _positions(AAA=-7.0),
        100_685.0,
        journal_open=[JournalOpenOrder(row, False, 0.0, 0.0, reading)],
    )

    assert _kinds(result) == []
    assert result.status == FILLS_LAGGING
    assert result.lagging_ids == ("tp-20261007-SEC_A-cover-1",)


# --- a broker long against a ledger short ------------------------------------


def test_a_broker_long_against_a_ledger_short_is_a_mismatch() -> None:
    """Item 10's sign check: broker +10 has the short's |quantity| but the
    wrong sign. It is a mismatch on the held name: `short_external_change`
    (item 9) or `quantity`; never `broker_only_position`, since the ledger
    holds the name."""
    result = _run(_ledger(), _positions(AAA=10.0), SHORT_CASH)

    assert result.status == MISMATCH
    kinds = _on(result, "SEC_A")
    assert len(kinds) == 1
    assert kinds[0] in {"short_external_change", "quantity"}
    assert result.adjustments == ()


# --- dividends on a short ----------------------------------------------------

#: 10 shares short x a $0.50 dividend: the due is -5.00 (item 10).
SHORT_DUE = -5.0


def test_an_undebited_short_dividend_passes_and_journals_nothing() -> None:
    """Paper does not debit dividends (item 2): cash 101,000 on both sides is
    inside [due, 0] = [-5, 0], so `ok`, and no `dividend_cash` row is proposed
    for a debit the broker did not make."""
    result = _run(
        _ledger(),
        _positions(AAA=-10.0),
        SHORT_CASH,
        explanations=_explanations(dividends={"SEC_A": SHORT_DUE}),
    )

    assert result.status == OK
    assert _kinds(result) == []
    assert result.adjustments == ()


def test_a_debited_short_dividend_passes_and_journals_the_debit() -> None:
    """The broker debits the whole due: cash 101,000 - 5 = 100,995, at the
    band's low end. `ok`, with one `dividend_cash` row of -5.00 on `SEC_A`
    (cash only, no quantity, no broker activity id)."""
    result = _run(
        _ledger(),
        _positions(AAA=-10.0),
        SHORT_CASH + SHORT_DUE,
        explanations=_explanations(dividends={"SEC_A": SHORT_DUE}),
    )

    assert result.status == OK
    assert _kinds(result) == []
    (row,) = result.adjustments
    assert (row.kind, row.security_id, row.quantity, row.cash, row.broker_activity_id) == (
        "dividend_cash",
        "SEC_A",
        None,
        pytest.approx(SHORT_DUE),
        None,
    )
    assert result.broker_cash == SHORT_CASH + SHORT_DUE


@pytest.mark.parametrize(
    "broker_cash",
    [
        pytest.param(SHORT_CASH - 10.0, id="debit_beyond_the_due"),  # -10 < -5
        pytest.param(SHORT_CASH + 5.0, id="credit_on_a_short"),  # +5 > 0
    ],
)
def test_cash_outside_the_short_dividend_band_is_a_cash_mismatch(broker_cash: float) -> None:
    """[due, 0] bounds the band both ways: a debit beyond the due, or a credit
    on a short, is a `cash` mismatch (the position itself matches)."""
    result = _run(
        _ledger(),
        _positions(AAA=-10.0),
        broker_cash,
        explanations=_explanations(dividends={"SEC_A": SHORT_DUE}),
    )

    assert result.status == MISMATCH
    assert _kinds(result) == ["cash"]
    assert result.adjustments == ()


# --- through `paper reconcile` on the fake: the halt and its switch ----------


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...


RUN_STARTED = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)  # 08:00 ET, 2026-09-29
RUN_DEPOSIT_DATE = date(2026, 9, 28)  # the day before the window's start date
SHORT_AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
PRICE = 100.0
SPY = "SEC_SPY"  # a fixture-store name listed through 2026
ETB = Asset(
    tradable=True,
    fractionable=True,
    status="active",
    cusip=None,
    shortable=True,
    easy_to_borrow=True,
    marginable=True,
)
RUN_DEPOSIT_ID = "20260928000000000::fund0002"


class ShortFake(FakeBroker):
    """A paper fake whose position and cash a test can move the way a broker's
    buy-in or recall would, which `FakeBroker` itself never does."""

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
        return replace(super().account(), cash=super().account().cash + self.extra_cash)


@pytest.fixture
def short_fake(fixed_clock: FixedClock) -> ShortFake:
    """A paper fake (shorts fill only then) with SPY ETB, the window's $100,000
    funding deposit listed the day before its start date, as the real account's
    first `JNLC` is (#1641)."""
    return ShortFake(
        assets={"SPY": ETB},
        clock=fixed_clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PA1",
        paper=True,
        activities=[
            CashActivity(
                activity_id=RUN_DEPOSIT_ID,
                activity_type="JNLC",
                date=RUN_DEPOSIT_DATE,
                status="executed",
                net_amount=100_000.0,
            )
        ],
    )


@pytest.fixture
def short_window(journal_settings: Settings) -> PaperWindowRow:
    """An open window whose `frozen_json` holds the risk section as `paper
    start` writes it."""
    frozen = {f"risk.{k}": v for k, v in RiskConfig().model_dump(mode="json").items()}
    frozen["paper.tracking_k"] = 20
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=RUN_STARTED,
        frozen_json=json.dumps(frozen, sort_keys=True),
        frozen_sha256="0" * 64,
        known_at=RUN_STARTED,
        ingested_at=RUN_STARTED,
    )
    with open_for_write(journal_settings) as conn:
        window_id = append(conn, row)  # type: ignore[arg-type]
    return replace(row, window_id=window_id)


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _short_10_spy(settings: Settings, fake: ShortFake, window: PaperWindowRow) -> None:
    """Journal a filled `sell_to_open` of 10 SPY at 100 (decision, order,
    events, fill: what the wrapper and a collector leave) and fill the same
    short at the fake: broker SPY -10 and cash 101,000, ledger short 10 and
    cash 101,000."""
    (run_id,) = _append(
        settings,
        PaperRunRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=SHORT_AT.date(),
            kind="rebalance",
            started_at=SHORT_AT,
            invoked_by="scheduler",
            code_version="test",
            known_at=SHORT_AT,
            ingested_at=SHORT_AT,
        ),
    )
    assert run_id is not None
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 29),
            security_id=SPY,
            side="sell",
            planned_quantity=10.0,
            whole_share=True,
            decision="trade",
            position_side="short",
            known_at=SHORT_AT,
            ingested_at=SHORT_AT,
        ),
    )
    assert decision_id is not None
    coid = "tp-20260929-SEC_SPY-short-1"
    _append(
        settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=SHORT_AT.date(),
            attempt=1,
            phase="buy",
            security_id=SPY,
            symbol="SPY",
            side="sell",
            quantity=10.0,
            position_side="short",
            sells_in_flight_at_submit=False,
            known_at=SHORT_AT,
            ingested_at=SHORT_AT,
        ),
        *(
            OrderEventRow(client_order_id=coid, status=s, known_at=SHORT_AT, ingested_at=SHORT_AT)
            for s in ("pending", "accepted", "filled")
        ),
        FillRow(
            client_order_id=coid,
            filled_at=SHORT_AT,
            quantity=10.0,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{coid}",
            source="broker_feed",
            known_at=SHORT_AT,
            ingested_at=SHORT_AT,
        ),
    )
    fake.submit(
        OrderRequest(
            coid, "SPY", Side.SELL, quantity=10.0, position_intent=PositionIntent.SELL_TO_OPEN
        )
    )
    fake.simulate_fill(coid)
    assert fake.positions()["SPY"].quantity == -10.0
    assert fake.account().cash == pytest.approx(101_000.0)


def _rows(settings: Settings, window: PaperWindowRow) -> list[ReconciliationRow]:
    with open_read_only(settings) as conn:
        return reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]


def test_paper_reconcile_of_a_held_short_on_the_fake_is_ok(
    journal_settings: Settings,
    short_fake: ShortFake,
    short_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """The journal's short of 10 against the fake's -10, cash 101,000 both
    sides, the funding deposit inside the base: `ok`, nothing engaged."""
    _short_10_spy(journal_settings, short_fake, short_window)

    result = reconcile_command(
        journal_settings, lambda: open_for_write(journal_settings), short_fake, fixed_clock
    )

    assert result.status == OK
    assert _kinds(result) == []
    (row,) = _rows(journal_settings, short_window)
    assert (row.status, row.broker_cash) == (OK, pytest.approx(101_000.0))
    with open_read_only(journal_settings) as conn:
        assert kill_switch_events_for(conn, short_window.window_id) == []  # type: ignore[arg-type]


def test_a_buy_in_on_the_fake_halts_as_short_external_change_and_engages(
    journal_settings: Settings,
    short_fake: ShortFake,
    short_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Item 9: the broker buys in 3 of the 10 at 105 (SPY -7, cash -315). The
    reconciliation row lists `short_external_change` on `SEC_SPY`, the error
    names it, and the switch engages (source `fault`)."""
    _short_10_spy(journal_settings, short_fake, short_window)
    short_fake.extra_quantity["SPY"] = 3.0
    short_fake.extra_cash = -315.0

    with pytest.raises(ReconciliationError, match="short_external_change"):
        reconcile_command(
            journal_settings, lambda: open_for_write(journal_settings), short_fake, fixed_clock
        )

    (row,) = _rows(journal_settings, short_window)
    assert row.status == MISMATCH
    found = json.loads(row.mismatches_json or "")["mismatches"]
    assert [(m["kind"], m["security_id"]) for m in found if m["security_id"] == SPY] == [
        ("short_external_change", SPY)
    ]
    with open_read_only(journal_settings) as conn:
        (event,) = kill_switch_events_for(conn, short_window.window_id)  # type: ignore[arg-type]
    assert (event.state, event.source, event.fault_type) == (
        "engaged",
        "fault",
        "ReconciliationError",
    )

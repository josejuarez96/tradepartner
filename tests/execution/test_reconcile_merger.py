"""A cash merger on a held long, explained at the stored rate (paper spec
amendment #1762 items 1, 2, 4 and 11; plan T249, the long half).

The store cases run `reconcile_now` on the fake broker and the fixture store
with a stored `mergers` row; the pure cases call `compare`. Every number is
hand-computed: 10 `MTUM` bought at 100 from 100,000, a merger at 62.5 a share,
so the due is 625.0 and the ledger's cash 99,000.0. The merger is effective and
payable on S (2026-10-01, a Thursday); with `risk.reconcile_merger_lag_sessions`
= 5 its window ends on 2026-10-08 (Oct 2, 5, 6, 7, 8) and 2026-10-09 is the
first session after it. Every fake carries the account's own funding `JNLC`,
dated before the window (inside the base).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import pytest

from tradepartner.adapters.broker import Account, CashActivity, OrderRequest, Position, Side
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ReconciliationError
from tradepartner.execution import reconcile_run as reconcile_module
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.reconcile import (
    MERGER_CONFLICT,
    OK,
    Explanations,
    JournalOpenOrder,
    OpenMerger,
    Reconciliation,
    compare,
)
from tradepartner.execution.reconcile_run import explanations_as_of, reconcile_now
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
    reconciliations_for,
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...


FROZEN = RiskConfig()
S = date(2026, 10, 1)  # the conftest clock's session; effective and payable date
S2, S3, S4 = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
WINDOW_END = date(2026, 10, 8)  # payable + 5 sessions
AFTER = date(2026, 10, 9)  # the first session after the window
LATER = date(2026, 10, 12)
CUT = session_close(date(2026, 9, 30))  # close(S-1)
KNOWN = CUT - timedelta(days=1)  # a merger row known before close(S-1)
BOUGHT_AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
PRICE = 100.0
MTUM = "SEC_MTUM"
SPY = "SEC_SPY"
HELD = 10.0
RATE = 62.5
DUE = HELD * RATE  # 625.0
LEDGER_CASH = 100_000.0 - HELD * PRICE  # 99,000.0
M1, M2 = "merger-1", "merger-2"
FUNDING = CashActivity(
    activity_id="20260928000000000::fund0001",
    activity_type="JNLC",
    date=date(2026, 9, 28),
    status="executed",
    net_amount=100_000.0,
)


class BookedFake(FakeBroker):
    """A fake a test moves the way a broker's merger processing would: the
    position removed (`extra_quantity`) and the cash credited (`extra_cash`)."""

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
    broker = BookedFake(
        assets=FakeBroker.active_assets("SPY", "MTUM"),
        clock=fixed_clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        account_id="PA1",
    )
    broker.activities = [FUNDING]
    return broker


# --- helpers -----------------------------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _merger(
    settings: Settings,
    merger_id: str = M1,
    known_at: datetime = KNOWN,
    *,
    security_id: str = MTUM,
    effective: date = S,
    payable: date | None = S,
    kind: str = "cash",
    acquirer: str | None = None,
    share_ratio: float = 0.5,
) -> None:
    """A stored `mergers` row: a cash merger at `RATE` unless `kind` says stock."""
    stock = kind != "cash"
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "mergers",
            {
                "security_id": security_id,
                "kind": kind,
                "acquirer_security_id": acquirer,
                "acquiree_symbol": "MTUM",
                "acquiree_cusip": "SYNTH-MTUM",
                "acquirer_symbol": "SPY" if acquirer else "",
                "acquirer_cusip": "SYNTH-SPY" if acquirer else "",
                "share_ratio": share_ratio if stock else None,
                "cash_per_share": None if kind == "stock" else RATE,
                "effective_date": effective,
                "process_date": effective,
                "payable_date": payable,
                "source_action_id": merger_id,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "alpaca",
                "provenance": "action",
            },
        )


def _delisting(settings: Settings, security_id: str, filed_at: datetime) -> None:
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "delistings",
            {
                "security_id": security_id,
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": filed_at,
                "effective_on": filed_at.date() + timedelta(days=10),
                "known_at": filed_at,
                "ingested_at": filed_at,
                "source": "edgar",
                "provenance": "filing",
            },
        )


def _hold(settings: Settings, fake: FakeBroker, window: PaperWindowRow) -> None:
    """Journal a filled buy of 10 `MTUM` at 100 and fill the same buy at the fake."""
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
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run_id,
            rebalance_session=date(2026, 9, 29),
            security_id=MTUM,
            side="buy",
            planned_quantity=HELD,
            whole_share=False,
            decision="trade",
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    assert decision_id is not None
    coid = "tp-hold-1"
    _append(
        settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=BOUGHT_AT.date(),
            attempt=1,
            phase="buy",
            security_id=MTUM,
            symbol="MTUM",
            side="buy",
            quantity=HELD,
            sells_in_flight_at_submit=False,
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
        *(
            OrderEventRow(client_order_id=coid, status=s, known_at=BOUGHT_AT, ingested_at=BOUGHT_AT)
            for s in ("pending", "accepted", "filled")
        ),
        FillRow(
            client_order_id=coid,
            filled_at=BOUGHT_AT,
            quantity=HELD,
            price=PRICE,
            price_implied=False,
            broker_fill_id=f"fill-{coid}",
            source="broker_feed",
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    fake.submit(OrderRequest(coid, "MTUM", Side.BUY, quantity=HELD))
    fake.simulate_fill(coid)


def _on(clock: FixedClock, session: date, minute: int = 0) -> None:
    """Move the clock to 10:00 New York (14:00 UTC) of `session`, plus `minute`."""
    clock.now = datetime.combine(session, time(14, minute), tzinfo=UTC)


def _reconcile(
    settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    clock: FixedClock,
    session: date,
    minute: int = 0,
) -> Reconciliation:
    _on(clock, session, minute)
    return reconcile_now(
        settings,
        lambda: open_for_write(settings),
        fake,
        window,
        session,
        clock,
        lambda: open_for_write(settings),
        frozen=FROZEN,
        as_of=clock(),
    )


def _halts(
    settings: Settings,
    fake: FakeBroker,
    window: PaperWindowRow,
    clock: FixedClock,
    session: date,
    minute: int = 0,
) -> list[dict[str, Any]]:
    """Reconcile expecting a mismatch; return the row's recorded mismatches."""
    with pytest.raises(ReconciliationError):
        _reconcile(settings, fake, window, clock, session, minute)
    row = _rows(settings, window)[-1]
    mismatches: list[dict[str, Any]] = json.loads(row.mismatches_json or "")["mismatches"]
    return mismatches


def _rows(settings: Settings, window: PaperWindowRow) -> list[ReconciliationRow]:
    with open_read_only(settings) as conn:
        return reconciliations_for(conn, window.window_id)  # type: ignore[arg-type]


def _adjustments(settings: Settings, window: PaperWindowRow) -> list[AdjustmentRow]:
    with open_read_only(settings) as conn:
        return adjustments_for(conn, window.window_id)  # type: ignore[arg-type]


def _merger_id(adjustment: AdjustmentRow) -> str | None:
    value = json.loads(adjustment.explanation_json or "{}").get("merger_id")
    return value if isinstance(value, str) else None


def _open(settings: Settings, window: PaperWindowRow, session: date) -> Explanations:
    with open_read_only(settings) as conn:
        return explanations_as_of(
            conn,
            window,
            session,
            as_of=datetime.combine(session, time(14), tzinfo=UTC),
            settings=settings,
            broker_symbols=("SPY",),
            quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
        )


def _removed(fake: BookedFake, cash: float = 0.0) -> None:
    """The broker removed the 10 `MTUM` and credited `cash`."""
    fake.extra_quantity["MTUM"] = -HELD
    fake.extra_cash = cash


# --- item 2: the removal, explained at the stored rate -------------------------


def test_a_long_removed_with_the_due_reconciles_ok_journals_one_row_and_closes(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, DUE)

    result = _reconcile(journal_settings, fake, open_window, fixed_clock, S)

    assert result.status == OK
    assert result.broker_cash == pytest.approx(LEDGER_CASH + DUE)
    (row,) = _adjustments(journal_settings, open_window)
    assert (row.kind, row.security_id, row.session) == ("corporate_action_cash", MTUM, S)
    assert row.quantity == pytest.approx(-HELD)
    assert row.cash == pytest.approx(DUE)
    assert _merger_id(row) == M1
    # Closed: paid in full, so the next session opens nothing and journals nothing.
    assert _open(journal_settings, open_window, S2).mergers == {}
    again = _reconcile(journal_settings, fake, open_window, fixed_clock, S2)
    assert again.status == OK and again.adjustments == ()


def test_the_merger_rule_applies_instead_of_the_ended_listing_band(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A delisting known beside the row: the row's tighter rule names the id."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, DUE)

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    (row,) = _adjustments(journal_settings, open_window)
    assert _merger_id(row) == M1 and row.cash == pytest.approx(DUE)


def test_cash_above_the_due_halts_on_cash(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, DUE + 1.0)

    mismatches = _halts(journal_settings, fake, open_window, fixed_clock, S)

    assert [m["kind"] for m in mismatches] == ["cash"]
    assert _adjustments(journal_settings, open_window) == []


# --- item 4: cash that comes later --------------------------------------------


def test_the_removal_with_no_cash_then_the_due_two_ok_reconciliations_later(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 0.0)

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    (removal,) = _adjustments(journal_settings, open_window)
    assert removal.quantity == pytest.approx(-HELD) and removal.cash == pytest.approx(0.0)
    assert _merger_id(removal) == M1
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK
    assert len(_adjustments(journal_settings, open_window)) == 1
    opened = _open(journal_settings, open_window, S3).mergers[MTUM]
    assert (opened.removed_quantity, opened.journaled_cash) == (pytest.approx(HELD), 0.0)

    fake.extra_cash = DUE
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S3).status == OK

    _removal, paid = _adjustments(journal_settings, open_window)
    assert (paid.kind, paid.security_id, paid.quantity) == ("corporate_action_cash", MTUM, None)
    assert paid.cash == pytest.approx(DUE) and _merger_id(paid) == M1
    assert _open(journal_settings, open_window, S4).mergers == {}


def test_the_due_paid_in_two_parts_journals_two_cash_only_rows_and_closes(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK

    fake.extra_cash = 300.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK
    assert _open(journal_settings, open_window, S3).mergers[MTUM].journaled_cash == 300.0
    fake.extra_cash = DUE
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S3).status == OK

    _removal, first, second = _adjustments(journal_settings, open_window)
    assert [first.cash, second.cash] == [pytest.approx(300.0), pytest.approx(325.0)]
    assert {_merger_id(first), _merger_id(second)} == {M1}
    assert _open(journal_settings, open_window, S4).mergers == {}


def test_a_credit_above_the_rest_of_the_due_halts_on_cash(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """300 of 625 paid with the removal: a later 400 is above the 325 left."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 300.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK

    fake.extra_cash = 700.0
    assert [m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S2)] == [
        "cash"
    ]


def test_a_due_not_met_by_the_windows_end_halts_once_then_closes(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, WINDOW_END).status == OK
    assert not _open(journal_settings, open_window, WINDOW_END).mergers[MTUM].final

    assert _open(journal_settings, open_window, AFTER).mergers[MTUM].final
    mismatches = _halts(journal_settings, fake, open_window, fixed_clock, AFTER)

    (cash,) = mismatches
    assert cash["kind"] == "cash" and cash["security_id"] == MTUM
    assert repr(M1) in cash["detail"] and "0.0 of 625.0" in cash["detail"]
    # The owner's resume: the next reconciliation does not raise the merger again.
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, AFTER, 30).status == OK
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, LATER).status == OK
    assert len(_adjustments(journal_settings, open_window)) == 1
    assert _open(journal_settings, open_window, LATER).mergers == {}


def test_the_due_met_inside_the_window_never_halts_after_it(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    fake.extra_cash = DUE
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, WINDOW_END).status == OK

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, AFTER).status == OK


# --- item 1: a removal the store did not know yet -----------------------------


def test_with_no_row_and_no_delisting_a_removal_halts_as_today(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _removed(fake, DUE)

    mismatches = _halts(journal_settings, fake, open_window, fixed_clock, S)

    assert sorted(m["kind"] for m in mismatches) == ["cash", "ledger_only_position"]
    assert _open(journal_settings, open_window, S).mergers == {}


def test_a_row_known_only_after_close_s_minus_1_explains_nothing_at_s(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """No look-ahead: the read is `mergers_as_of(close(S-1))`. A row stamped a
    minute after close(S-1) is invisible at S and seen at S2; after the owner's
    resume the removal is journaled once."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, known_at=CUT + timedelta(minutes=1))
    _removed(fake, DUE)

    assert _open(journal_settings, open_window, S).mergers == {}
    kinds = sorted(m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S))
    assert kinds == ["cash", "ledger_only_position"]
    assert _open(journal_settings, open_window, S2).mergers[MTUM].merger_id == M1

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2, 30).status == OK
    (row,) = _adjustments(journal_settings, open_window)
    assert (row.cash, _merger_id(row)) == (pytest.approx(DUE), M1)


@pytest.mark.parametrize(("paid", "halts_once"), [(DUE, False), (0.0, True)])
def test_a_halted_removal_whose_row_is_stored_after_the_window(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    paid: float,
    halts_once: bool,
) -> None:
    """The removal halts at S; the row is first known at close(AFTER), after its
    window. The resumed run explains the held acquiree (open under (a)) and
    journals the removal once; a total short of the due halts on `cash` once."""
    _hold(journal_settings, fake, open_window)
    _removed(fake, paid)
    _halts(journal_settings, fake, open_window, fixed_clock, S)
    _merger(journal_settings, known_at=session_close(AFTER))

    if halts_once:
        (cash,) = _halts(journal_settings, fake, open_window, fixed_clock, LATER)
        assert cash["kind"] == "cash" and repr(M1) in cash["detail"]
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, LATER, 30).status == OK
    (row,) = _adjustments(journal_settings, open_window)
    assert row.quantity == pytest.approx(-HELD) and row.cash == pytest.approx(paid)
    assert _merger_id(row) == M1
    # Its window ended before it was known: the journaled removal opens nothing.
    assert _open(journal_settings, open_window, date(2026, 10, 13)).mergers == {}


# --- the ended-listing band first, then the row -------------------------------


def test_the_bands_proceeds_count_toward_a_row_known_later_inside_its_window(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, 300.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    (band,) = _adjustments(journal_settings, open_window)
    assert band.cash == pytest.approx(300.0) and _merger_id(band) is None
    _merger(journal_settings, known_at=session_close(S))

    opened = _open(journal_settings, open_window, S2).mergers[MTUM]
    assert (opened.removed_quantity, opened.journaled_cash) == (pytest.approx(HELD), 300.0)
    fake.extra_cash = DUE
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK

    _band, rest = _adjustments(journal_settings, open_window)
    assert (rest.quantity, rest.cash, _merger_id(rest)) == (None, pytest.approx(325.0), M1)
    assert _open(journal_settings, open_window, S3).mergers == {}


def test_stock_merger_known_after_band_removal_gets_no_unchecked_cil_allowance(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The band removed 10 target shares before any receipt was validated.
    Its later stock merger has zero cash due, so a $100 credit must halt
    even when no acquirer reference price exists."""
    _hold(journal_settings, fake, open_window)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    _merger(journal_settings, known_at=session_close(S), kind="stock", acquirer=SPY)
    monkeypatch.setattr(reconcile_module, "_reference_prices", lambda *_args, **_kwargs: {})

    assert _open(journal_settings, open_window, S2).mergers == {}
    fake.extra_cash = 100.0
    assert [m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S2)] == [
        "cash"
    ]


def test_a_row_stored_after_its_window_for_a_band_removal_opens_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    _merger(journal_settings, known_at=session_close(AFTER))

    assert _open(journal_settings, open_window, LATER).mergers == {}
    fake.extra_cash = DUE
    kinds = [m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, LATER)]
    assert kinds == ["cash"]


# --- item 1: conflicting rows -------------------------------------------------


@pytest.mark.parametrize(
    "stamps",
    [(KNOWN, KNOWN), (KNOWN - timedelta(days=1), KNOWN)],
    ids=["one-run", "two-runs"],
)
def test_two_rows_known_before_the_removal_halt_merger_conflict_once_then_fall_back(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    stamps: tuple[datetime, datetime],
) -> None:
    """The pair stored in one run or in two gives the same result: one
    `merger_conflict` naming both ids; after the resume the band explains the
    removal (a delisting is known) and neither id is read again."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, M1, stamps[0])
    _merger(journal_settings, M2, stamps[1])
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, DUE)

    (conflict,) = _halts(journal_settings, fake, open_window, fixed_clock, S)

    assert conflict["kind"] == MERGER_CONFLICT and conflict["security_id"] == MTUM
    assert conflict["detail"] == (f"merger ids [{M1!r}, {M2!r}] for {MTUM} effective 2026-10-01")
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S, 30).status == OK
    (band,) = _adjustments(journal_settings, open_window)
    assert _merger_id(band) is None and band.quantity == pytest.approx(-HELD)
    assert _open(journal_settings, open_window, S2).mergers == {}


def test_a_second_row_after_the_first_explained_the_removal_halts_once(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Row M1 explains the removal with 300 of its 625; row M2 for the same
    acquiree and date is known at close(S): S2 halts `merger_conflict` naming
    both, once; after the resume, later cash is not matched to M1's terms."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, M1)
    _removed(fake, 300.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    _merger(journal_settings, M2, session_close(S))

    (conflict,) = _halts(journal_settings, fake, open_window, fixed_clock, S2)
    assert conflict["kind"] == MERGER_CONFLICT
    assert repr(M1) in conflict["detail"] and repr(M2) in conflict["detail"]

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2, 30).status == OK
    fake.extra_cash = DUE
    kinds = [m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S3)]
    assert kinds == ["cash"]
    assert len(_adjustments(journal_settings, open_window)) == 1


def test_a_conflicting_pair_on_a_name_never_held_raises_nothing(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, M1, security_id=SPY)
    _merger(journal_settings, M2, security_id=SPY)

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK


# --- the acquirer among the mapped names; the stock kinds wait for T249b -------


def test_the_acquirer_joins_the_names_mapped_to_broker_symbols(
    journal_settings: Settings, fake: BookedFake, open_window: PaperWindowRow
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY)

    found = _open(journal_settings, open_window, S)

    assert found.symbols[SPY] == "SPY"
    assert found.mergers[MTUM].kind == "stock"


def test_a_stock_merger_without_a_receipt_halts_on_quantity(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A stored stock merger cannot explain a missing acquirer receipt."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY)
    _removed(fake, 0.0)

    kinds = [m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S)]
    assert kinds == ["quantity"]


def test_stock_merger_whole_receipt_then_later_cil(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """10 x .25 = 2.5 due: 2 shares arrive, then 50 cash for the half
    at the 100 reference. The upper cash bound is 51 with the 2% buffer."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.25)
    _removed(fake)
    fake.extra_quantity["SPY"] = 2.0

    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    removal, receipt = _adjustments(journal_settings, open_window)
    assert (removal.kind, removal.security_id, removal.quantity, removal.cash) == (
        "corporate_action_cash",
        MTUM,
        -10.0,
        0.0,
    )
    assert (receipt.kind, receipt.security_id, receipt.quantity, receipt.cash) == (
        "merger_receipt",
        SPY,
        2.0,
        None,
    )
    assert _merger_id(removal) == _merger_id(receipt) == M1
    assert _open(journal_settings, open_window, S2).mergers[MTUM].removed_quantity == 10.0

    fake.extra_cash = 50.0
    fake.activities.append(CashActivity("cil-stock-1", "CIL", S2, "executed", 50.0, "SPY"))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK
    cash_row = _adjustments(journal_settings, open_window)[-1]
    assert (cash_row.kind, cash_row.quantity, cash_row.cash, _merger_id(cash_row)) == (
        "corporate_action_cash",
        None,
        50.0,
        M1,
    )
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S3).status == OK
    assert len(_adjustments(journal_settings, open_window)) == 3


def test_stock_merger_receipt_below_band_halts_on_quantity(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.25)
    _removed(fake)
    fake.extra_quantity["SPY"] = 0.5  # one share below the band's 1.5 lower edge
    assert "quantity" in [
        m["kind"] for m in _halts(journal_settings, fake, open_window, fixed_clock, S)
    ]
    assert _adjustments(journal_settings, open_window) == []


def test_stock_merger_counts_a_preexisting_acquirer_holding(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.5)
    # A preexisting 3-share holding plus 5 receipt shares.
    _append(
        journal_settings,
        AdjustmentRow(
            window_id=open_window.window_id,
            session=BOUGHT_AT.date(),
            kind="carried_residue",
            security_id=SPY,
            quantity=3.0,
            known_at=BOUGHT_AT,
            ingested_at=BOUGHT_AT,
        ),
    )
    fake.extra_quantity["SPY"] = 8.0
    _removed(fake)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    assert _adjustments(journal_settings, open_window)[-1].quantity == 5.0


def test_short_stock_merger_still_halts_short_external_change() -> None:
    ledger = Ledger(positions={}, cash=LEDGER_CASH, through=S, short_holdings={MTUM: HELD})
    result = _compare(
        ledger,
        {"SPY": 5.0},
        LEDGER_CASH,
        {MTUM: _merger_open(kind="stock", acquirer=SPY, share_ratio=0.5, cash_per_share=None)},
    )
    assert "short_external_change" in [m.kind for m in result.mismatches]
    assert result.adjustments == ()


def test_stock_merger_receipt_is_deferred_beside_a_pending_order() -> None:
    """A pending order leaves the five-share merger receipt in the allowance;
    neither merger row is journaled until an ordinary reconciliation."""
    order = OrderRow(
        client_order_id="tp-pending-spy-1",
        decision_id=1,
        run_id=1,
        session=S,
        attempt=1,
        phase="buy",
        security_id=SPY,
        symbol="SPY",
        side="buy",
        notional=100.0,
        sells_in_flight_at_submit=False,
        known_at=BOUGHT_AT,
        ingested_at=BOUGHT_AT,
    )
    pending = JournalOpenOrder(order, True, 0.0, 0.0, None)
    result = _compare(
        Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S),
        {"SPY": 5.0},
        LEDGER_CASH,
        {MTUM: _merger_open(kind="stock", acquirer=SPY, share_ratio=0.5, cash_per_share=None)},
        lagging=(pending,),
    )

    assert result.status == "pending_unresolved"
    assert result.adjustments == ()
    assert ("deferred merger receipt", 5.0, SPY) in [
        (a.reason, a.difference, a.security_id) for a in result.allowed
    ]


def test_stock_and_cash_receipt_and_base_cash_due() -> None:
    """10 acquiree shares yield 2 acquirer shares and 625 base cash;
    another 51 is the buffered CIL upper bound for the missing half share."""
    ledger = Ledger(positions={MTUM: 10.0}, cash=LEDGER_CASH, through=S)
    result = _compare(
        ledger,
        {"SPY": 2.0},
        LEDGER_CASH + 625.0,
        {
            MTUM: _merger_open(
                kind="stock_and_cash",
                acquirer=SPY,
                share_ratio=0.25,
                cash_per_share=62.5,
            )
        },
    )
    assert result.status == OK
    assert [(a.kind, a.quantity, a.cash) for a in result.adjustments] == [
        ("corporate_action_cash", -10.0, 625.0),
        ("merger_receipt", 2.0, None),
    ]


def test_stock_and_cash_whole_receipt_without_price_halts_before_journaling() -> None:
    """Ten target shares give five whole shares and 625 cash; even with no
    fractional due the removal's sale fraction needs an acquirer price."""
    result = _compare(
        Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S),
        {"SPY": 5.0},
        LEDGER_CASH + DUE,
        {MTUM: _merger_open(kind="stock_and_cash", acquirer=SPY, share_ratio=0.5)},
        reference_prices={MTUM: 100.0},
    )
    assert result.status == "mismatch"
    assert "quantity" in [m.kind for m in result.mismatches]
    assert result.adjustments == ()


def test_same_session_stock_and_cash_cil_is_journaled_separately_from_boot() -> None:
    """The 675 cash difference is 625 base cash plus a 50 CIL activity;
    the lot rebuild needs the CIL portion named on the removal row."""
    cil = CashActivity("merger-cil", "CIL", S, "executed", 50.0, "SPY")
    result = _compare(
        Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S),
        {"SPY": 2.0},
        LEDGER_CASH + 675.0,
        {MTUM: _merger_open(kind="stock_and_cash", acquirer=SPY, share_ratio=0.25)},
        activities=(cil,),
    )
    assert result.status == OK
    assert result.adjustments[0].merger_terms is not None
    assert result.adjustments[0].merger_terms["same_session_cil_cash"] == 50.0


def test_unpaid_stock_cil_closes_quietly_after_window(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.25)
    _removed(fake)
    fake.extra_quantity["SPY"] = 2.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    assert _open(journal_settings, open_window, AFTER).mergers[MTUM].final
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, AFTER).status == OK
    assert _open(journal_settings, open_window, LATER).mergers == {}


# --- pure compare cases --------------------------------------------------------


def _merger_open(**overrides: Any) -> OpenMerger:
    values: dict[str, Any] = {
        "merger_id": M1,
        "kind": "cash",
        "acquirer": None,
        "share_ratio": None,
        "cash_per_share": RATE,
        "effective_date": S,
        "payable_date": S,
        "window_end": WINDOW_END,
    }
    values.update(overrides)
    return OpenMerger(**values)


def _compare(
    ledger: Ledger,
    positions: dict[str, float],
    cash: float,
    mergers: dict[str, OpenMerger],
    activities: tuple[CashActivity, ...] = (),
    dividends: dict[str, float] | None = None,
    lagging: tuple[JournalOpenOrder, ...] = (),
    reference_prices: dict[str, float] | None = None,
) -> Reconciliation:
    window = _window()
    at = datetime(2026, 10, 1, 14, tzinfo=UTC)
    return compare(
        ledger,
        {s: Position(symbol=s, quantity=q) for s, q in positions.items()},
        (),
        lagging,
        Account(account_id="PA1", cash=cash, buying_power=cash, equity=cash, as_of=at),
        Explanations(
            symbols={MTUM: "MTUM", SPY: "SPY"},
            reference_prices=reference_prices
            if reference_prices is not None
            else {MTUM: 100.0, SPY: 100.0},
            mergers=mergers,
            activities=activities,
            dividends=dividends or {},
            activity_seen_before=date(2026, 9, 29),
        ),
        window,
        FROZEN,
        order_id_prefix="tp",
    )


def _window() -> PaperWindowRow:
    return PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=S,
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=BOUGHT_AT,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=BOUGHT_AT,
        ingested_at=BOUGHT_AT,
    )


def test_a_held_short_acquiree_still_halts_short_external_change() -> None:
    """The short half is T249s: a short closed by a cash merger is not explained."""
    ledger = Ledger(positions={}, cash=LEDGER_CASH, through=S, short_holdings={MTUM: HELD})

    result = _compare(ledger, {}, LEDGER_CASH - DUE, {MTUM: _merger_open()})

    assert "short_external_change" in [m.kind for m in result.mismatches]
    assert result.adjustments == ()


def test_a_cil_on_the_acquiree_is_placed_against_the_merger() -> None:
    """A `CIL` on an open merger's acquiree is left in the cash difference the
    merger reads, never booked by the stock-dividend rule (#1762 item 4)."""
    ledger = Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S)
    cil = CashActivity(
        activity_id="cil-1",
        activity_type="CIL",
        date=S,
        status="executed",
        net_amount=DUE,
        symbol="MTUM",
    )

    result = _compare(ledger, {}, LEDGER_CASH + DUE, {MTUM: _merger_open()}, (cil,))

    assert result.status == OK
    (row,) = result.adjustments
    assert (row.kind, row.quantity, row.cash, row.merger_id) == (
        "corporate_action_cash",
        -HELD,
        DUE,
        M1,
    )


def test_a_dividend_credited_beside_the_due_is_journaled_as_a_dividend() -> None:
    ledger = Ledger(positions={MTUM: HELD, SPY: 1.0}, cash=LEDGER_CASH, through=S)

    result = _compare(
        ledger,
        {"SPY": 1.0},
        LEDGER_CASH + DUE + 5.0,
        {MTUM: _merger_open()},
        dividends={SPY: 5.0},
    )

    assert result.status == OK
    kinds = sorted((a.kind, a.cash) for a in result.adjustments)
    assert kinds == [("corporate_action_cash", DUE), ("dividend_cash", 5.0)]


def test_an_acquiree_the_broker_still_holds_takes_nothing() -> None:
    ledger = Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S)

    result = _compare(ledger, {"MTUM": HELD}, LEDGER_CASH, {MTUM: _merger_open(final=True)})

    assert result.status == OK and result.adjustments == ()


def test_a_merger_row_is_left_out_of_every_other_rows_json() -> None:
    """Only a merger's row carries `merger_id` in `mismatches_json`."""
    ledger = Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S)

    result = _compare(ledger, {}, LEDGER_CASH + DUE, {MTUM: _merger_open()})

    (entry,) = json.loads(result.mismatches_json)["explained"]
    assert entry["merger_id"] == M1
    plain = _compare(Ledger(positions={}, cash=LEDGER_CASH, through=S), {}, LEDGER_CASH, {})
    assert "merger_id" not in plain.mismatches_json


# --- pass-1 review cases ------------------------------------------------------


def test_a_dividend_beside_an_unpaid_due_is_booked_as_the_dividend_not_merger_cash() -> None:
    """The removal is journaled with nothing paid; a 5.00 dividend on `SPY` is the
    only credit. It is the dividend's, and the full due later is the merger's."""
    ledger = Ledger(positions={SPY: 1.0}, cash=LEDGER_CASH, through=S)
    removed = {MTUM: _merger_open(removed_quantity=HELD, journaled_cash=0.0)}

    result = _compare(ledger, {"SPY": 1.0}, LEDGER_CASH + 5.0, removed, dividends={SPY: 5.0})

    assert result.status == OK
    assert [(a.kind, a.cash, a.merger_id) for a in result.adjustments] == [
        ("dividend_cash", 5.0, None)
    ]
    paid = _compare(ledger, {"SPY": 1.0}, LEDGER_CASH + DUE, removed)
    assert [(a.cash, a.merger_id) for a in paid.adjustments] == [(DUE, M1)]


def test_an_ended_names_proceeds_beside_an_unpaid_removal_stay_the_ended_names() -> None:
    """`MTUM` removed by its merger with nothing paid, `SPY` (1 share at 100)
    ended in the same session crediting 90: the 90 is `SPY`'s proceeds."""
    ledger = Ledger(positions={MTUM: HELD, SPY: 1.0}, cash=LEDGER_CASH, through=S)
    explanations = Explanations(
        symbols={MTUM: "MTUM", SPY: "SPY"},
        ended=frozenset({SPY}),
        reference_prices={MTUM: 100.0, SPY: 100.0},
        mergers={MTUM: _merger_open()},
        activity_seen_before=date(2026, 9, 29),
    )
    at = datetime(2026, 10, 1, 14, tzinfo=UTC)
    result = compare(
        ledger,
        {},
        (),
        (),
        Account(account_id="PA1", cash=LEDGER_CASH + 90.0, buying_power=0.0, equity=0.0, as_of=at),
        explanations,
        replace(_window(), account_id="PA1"),
        FROZEN,
        order_id_prefix="tp",
    )

    assert result.status == OK
    rows = {(a.security_id, a.merger_id): a.cash for a in result.adjustments}
    assert rows == {(MTUM, M1): 0.0, (SPY, None): 90.0}


@pytest.mark.parametrize(("deferred", "final"), [(True, True), (False, False)])
def test_a_row_after_the_window_beside_a_lagging_order_is_not_the_due_check(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
    deferred: bool,
    final: bool,
) -> None:
    """`compare` runs no due check beside a lagging or pending order, so a
    mismatch row after the window that had one does not count as the check;
    one without does (the removal was journaled before it)."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    stamp = datetime.combine(AFTER, time(13), tzinfo=UTC)
    payload = {
        "mismatches": [{"kind": "unknown_symbol", "detail": "x", "security_id": None}],
        "lagging": ["tp-lag-1"] if deferred else [],
        "pending": [],
    }
    _append(
        journal_settings,
        ReconciliationRow(
            window_id=open_window.window_id,  # type: ignore[arg-type]
            at=stamp,
            status="mismatch",
            mismatches_json=json.dumps(payload),
            book_id=open_window.book_id,
            known_at=stamp,
            ingested_at=stamp,
        ),
    )

    assert _open(journal_settings, open_window, AFTER).mergers[MTUM].final is final


def test_a_later_revision_does_not_move_when_the_row_was_first_known(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """First known at close(S), inside the window; a revision stamped after the
    window does not make it "known only after its window": the band's removal
    still opens it under (b)."""
    _hold(journal_settings, fake, open_window)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    _merger(journal_settings, known_at=session_close(S))
    _merger(journal_settings, known_at=session_close(AFTER), payable=S2)

    opened = _open(journal_settings, open_window, LATER).mergers[MTUM]
    assert opened.payable_date == S2 and opened.removed_quantity == pytest.approx(HELD)


def test_a_band_removal_before_the_effective_date_is_not_the_mergers(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """A band removal on S links no merger effective on S3 (two sessions on)."""
    _hold(journal_settings, fake, open_window)
    _delisting(journal_settings, MTUM, KNOWN)
    _removed(fake, 0.0)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    _merger(journal_settings, known_at=session_close(S), effective=S3, payable=S3)

    assert _open(journal_settings, open_window, S4).mergers == {}


def _beside_an_ended_name(spy_held: float, credit: float) -> Reconciliation:
    """`MTUM` removed by its merger with 625 due, `SPY` (at 100) ended in the same
    session, and `credit` the only cash."""
    ledger = Ledger(positions={MTUM: HELD, SPY: spy_held}, cash=LEDGER_CASH, through=S)
    at = datetime(2026, 10, 1, 14, tzinfo=UTC)
    cash = LEDGER_CASH + credit
    return compare(
        ledger,
        {},
        (),
        (),
        Account(account_id="PA1", cash=cash, buying_power=cash, equity=cash, as_of=at),
        Explanations(
            symbols={MTUM: "MTUM", SPY: "SPY"},
            ended=frozenset({SPY}),
            reference_prices={MTUM: 100.0, SPY: 100.0},
            mergers={MTUM: _merger_open()},
            activity_seen_before=date(2026, 9, 29),
        ),
        _window(),
        FROZEN,
        order_id_prefix="tp",
    )


def test_an_ambiguous_split_between_a_merger_and_an_ended_name_halts_on_cash() -> None:
    """10 `SPY` ended (cap 1,020) and 700 credited: "the merger paid in full, 75
    proceeds" and "the merger paid nothing, 700 proceeds" both fit, so nothing is
    guessed: `cash`, nothing journaled."""
    result = _beside_an_ended_name(10.0, 700.0)

    assert [m.kind for m in result.mismatches] == ["cash"]
    assert result.adjustments == ()


def test_the_due_paid_in_full_beside_an_ended_name_is_journaled_when_unambiguous() -> None:
    """1 `SPY` ended (cap 102) and 625 + 50 credited: only "paid in full" fits."""
    result = _beside_an_ended_name(1.0, DUE + 50.0)

    assert result.status == OK
    rows = {(a.security_id, a.merger_id): a.cash for a in result.adjustments}
    assert rows == {(MTUM, M1): DUE, (SPY, None): pytest.approx(50.0)}


def test_a_payment_within_the_cash_bound_of_the_due_journals_the_cash_received() -> None:
    ledger = Ledger(positions={MTUM: HELD}, cash=LEDGER_CASH, through=S)

    result = _compare(ledger, {}, LEDGER_CASH + DUE - 0.005, {MTUM: _merger_open()})

    assert result.status == OK
    (row,) = result.adjustments
    assert row.cash == pytest.approx(DUE - 0.005, abs=1e-9)


def test_stock_receipt_journals_lot_terms_for_basis_carry(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Ten target shares at .5 yield five acquirer shares; the removal
    records that ratio and the 250.38 pre-ex price for the lot rebuild."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.5)
    _removed(fake)
    fake.extra_quantity["SPY"] = 5.0
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    removal = _adjustments(journal_settings, open_window)[0]
    terms = json.loads(removal.explanation_json or "{}")
    assert terms["merger_kind"] == "stock"
    assert terms["acquirer_security_id"] == SPY
    assert terms["share_ratio"] == 0.5
    assert terms["acquirer_price"] == 250.38
    assert terms["fractional_shares"] == 0.0


def test_all_cil_stock_merger_journals_no_zero_share_receipt(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """Ten target shares x .05 = .5 due, zero whole acquirer shares,
    125.19 CIL at the 250.38 reference: only the removal is journaled."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.05)
    _removed(fake, 125.19)
    fake.activities.append(CashActivity("all-cil-1", "CIL", S, "executed", 125.19, "SPY"))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    (removal,) = _adjustments(journal_settings, open_window)
    assert (removal.kind, removal.quantity, removal.cash) == (
        "corporate_action_cash",
        -10.0,
        125.19,
    )


def test_all_cil_stock_merger_rebuilds_due_for_later_payment(
    journal_settings: Settings,
    fake: BookedFake,
    open_window: PaperWindowRow,
    fixed_clock: FixedClock,
) -> None:
    """With no receipt row, .5 fractional share still leaves 125.19 due.
    The next session's CIL is journaled against that open merger."""
    _hold(journal_settings, fake, open_window)
    _merger(journal_settings, kind="stock", acquirer=SPY, share_ratio=0.05)
    _removed(fake)
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S).status == OK
    assert len(_adjustments(journal_settings, open_window)) == 1

    fake.extra_cash = 125.19
    fake.activities.append(CashActivity("all-cil-later", "CIL", S2, "executed", 125.19, "SPY"))
    assert _reconcile(journal_settings, fake, open_window, fixed_clock, S2).status == OK
    assert [(row.quantity, row.cash) for row in _adjustments(journal_settings, open_window)] == [
        (-10.0, 0.0),
        (None, 125.19),
    ]

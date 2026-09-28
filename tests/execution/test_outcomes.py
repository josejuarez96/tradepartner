"""Outcomes and the lot-ledger write (Phase 4 plan T62, #344).

Rebalance T_i = 2026-09-30 (the last session of September), fill session
F_i = 2026-10-01, T_{i+1} = 2026-10-30: an order of rebalance i is due at the
first run after close(2026-10-30), the run on 2026-11-02. Equity at the mark
of 2026-09-30 is 10,000, so a 100 dollar gain contributes 0.01.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from itertools import count

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import previous_session
from tradepartner.execution.lots import LedgerAccount, rebuild
from tradepartner.execution.outcomes import (
    Outcome,
    OutcomeWindow,
    due_outcomes,
    write_outcomes_and_lots,
)
from tradepartner.store import schema
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    append,
    outcomes_for,
)

T_I = date(2026, 9, 30)
F_I = date(2026, 10, 1)
T_NEXT = date(2026, 10, 30)
DUE = date(2026, 11, 2)
STAMP = datetime(2026, 9, 29, 12, tzinfo=UTC)
ACCOUNT = LedgerAccount("acct-1", "paper", "self")
_fill_ids = count(1)


def _utc(day: date, hour: int = 14) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


class Book:
    """Orders, their events and fills for one window, plus closes and marks."""

    def __init__(self) -> None:
        self.orders: list[OrderRow] = []
        self.events: list[OrderEventRow] = []
        self.fills: list[OrderedFill] = []
        self.closes: dict[tuple[str, date], float] = {}
        self.marks: list[PositionDailyRow] = [
            PositionDailyRow(
                run_id=1,
                session=T_I,
                quantity=0.0,
                cash=10_000.0,
                known_at=STAMP,
                ingested_at=STAMP,
            )
        ]

    def order(
        self,
        security_id: str,
        side: str,
        status: str,
        fills: Sequence[tuple[float, float]] = (),
        *,
        session: date = F_I,
        phase: str | None = None,
        implied_last: bool = False,
    ) -> str:
        coid = f"tp-{session:%Y%m%d}-{security_id}-{side}-{len(self.orders) + 1}"
        self.orders.append(
            OrderRow(
                client_order_id=coid,
                decision_id=1,
                run_id=1,
                session=session,
                attempt=1,
                phase=phase or side,
                security_id=security_id,
                symbol=security_id.removeprefix("SEC_"),
                side=side,
                quantity=10.0,
                sells_in_flight_at_submit=False,
                known_at=_utc(session, 13),
                ingested_at=_utc(session, 13),
            )
        )
        for n, (quantity, price) in enumerate(fills):
            fill_id = next(_fill_ids)
            implied = implied_last and n == len(fills) - 1
            row = FillRow(
                fill_id=fill_id,
                client_order_id=coid,
                filled_at=_utc(session, 14 + n),
                quantity=quantity,
                price=price,
                price_implied=implied,
                broker_fill_id=("synthetic:" if implied else "bf-") + str(fill_id),
                source="broker_status" if implied else "broker_feed",
                known_at=_utc(session, 20),
                ingested_at=_utc(session, 20),
            )
            self.fills.append(
                OrderedFill(row, side, security_id, security_id.removeprefix("SEC_"), 1, 1)
            )
        for event in ("pending", "accepted", status):
            self.events.append(
                OrderEventRow(
                    client_order_id=coid,
                    status=event,
                    known_at=_utc(session, 20),
                    ingested_at=_utc(session, 20),
                )
            )
        return coid

    def mark(self, security_id: str, session: date, price: float, quantity: float = 1.0) -> None:
        self.marks.append(
            PositionDailyRow(
                run_id=1,
                session=session,
                security_id=security_id,
                quantity=quantity,
                mark_price=price,
                value=quantity * price,
                known_at=STAMP,
                ingested_at=STAMP,
            )
        )

    def price_of(self, security_id: str, session: date) -> float | None:
        return self.closes.get((security_id, session))

    def due(
        self,
        session: date,
        window: OutcomeWindow | None = None,
        *,
        decisions: Sequence[DecisionRow] = (),
        actions: pl.DataFrame | None = None,
    ) -> list[Outcome]:
        ledger = rebuild(self.fills, self.orders, {}, ACCOUNT)
        return due_outcomes(
            window or OutcomeWindow(window_id=1),
            self.orders,
            self.events,
            self.fills,
            self.marks,
            ledger,
            self.price_of,
            session,
            decisions=decisions,
            actions=actions,
        )


def _by_kind(outcomes: Sequence[Outcome]) -> dict[tuple[str, str], Outcome]:
    return {(o.client_order_id, o.kind): o for o in outcomes}


def test_a_filled_buy_has_its_position_return_and_contribution() -> None:
    book = Book()
    coid = book.order("SEC_A", "buy", "filled", [(5.0, 98.0), (5.0, 102.0)])
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)

    [outcome] = book.due(DUE)

    assert (outcome.client_order_id, outcome.kind, outcome.through_session) == (
        coid,
        "position_return",
        T_NEXT,
    )
    assert outcome.value == pytest.approx(0.10)  # 110 / 100 - 1
    assert outcome.contribution == pytest.approx(0.01)  # 10 x 10 / 10,000
    assert outcome.mark_price == 110.0


def test_nothing_is_due_until_the_run_after_close_of_the_next_rebalance() -> None:
    book = Book()
    book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)
    assert book.due(T_NEXT) == []
    assert len(book.due(DUE)) == 1


def test_a_non_terminal_order_is_not_due() -> None:
    book = Book()
    book.order("SEC_A", "buy", "accepted", [(4.0, 100.0)])
    assert book.due(DUE) == []


def test_a_filled_sell_has_its_realised_pnl_on_the_fifo_lots() -> None:
    book = Book()
    book.order("SEC_B", "buy", "filled", [(10.0, 50.0)], session=date(2026, 9, 1))
    coid = book.order("SEC_B", "sell", "filled", [(10.0, 60.0)])
    book.closes[("SEC_B", T_NEXT)] = 58.0

    outcomes = _by_kind(book.due(DUE))

    sell = outcomes[(coid, "realised_pnl")]
    assert sell.value == pytest.approx(100.0)
    assert sell.contribution == pytest.approx(0.01)
    assert sell.through_session == T_NEXT


@pytest.mark.parametrize("status", ["expired", "rejected", "cancelled"])
def test_an_unexecuted_order_has_the_names_return_over_the_horizon(status: str) -> None:
    book = Book()
    coid = book.order("SEC_C", "buy", status)
    book.closes[("SEC_C", T_I)] = 20.0  # close(F_i - 1), the plan's reference
    book.closes[("SEC_C", T_NEXT)] = 22.0

    [outcome] = book.due(DUE)

    assert (outcome.client_order_id, outcome.kind) == (coid, "not_executed")
    assert outcome.value == pytest.approx(0.10)
    assert outcome.mark_price == 22.0
    assert outcome.contribution is None


def test_an_unexecuted_order_without_prices_has_no_value() -> None:
    book = Book()
    book.order("SEC_C", "sell", "expired")
    [outcome] = book.due(DUE)
    assert (outcome.value, outcome.mark_price) == (None, None)


def test_a_partial_fill_then_expiry_has_both_outcomes() -> None:
    book = Book()
    coid = book.order("SEC_D", "buy", "expired", [(3.0, 10.0)])
    book.mark("SEC_D", T_NEXT, 11.0, 3.0)
    book.closes[("SEC_D", T_I)] = 10.0
    book.closes[("SEC_D", T_NEXT)] = 11.0

    outcomes = _by_kind(book.due(DUE))

    assert set(outcomes) == {(coid, "position_return"), (coid, "not_executed")}
    assert outcomes[(coid, "position_return")].contribution == pytest.approx(
        0.0003
    )  # 1 x 3 / 10,000


def test_a_partial_sell_then_expiry_has_realised_pnl_and_not_executed() -> None:
    book = Book()
    book.order("SEC_B", "buy", "filled", [(10.0, 50.0)], session=date(2026, 9, 1))
    coid = book.order("SEC_B", "sell", "expired", [(4.0, 55.0)])
    outcomes = _by_kind(book.due(DUE))
    assert outcomes[(coid, "realised_pnl")].value == pytest.approx(20.0)
    assert (coid, "not_executed") in outcomes


def test_a_forced_exit_is_due_after_its_exit_session() -> None:
    exit_day = date(2026, 10, 15)
    book = Book()
    book.order("SEC_B", "buy", "filled", [(10.0, 50.0)], session=date(2026, 9, 1))
    coid = book.order("SEC_B", "sell", "filled", [(10.0, 45.0)], session=exit_day, phase="exit")

    assert [o for o in book.due(exit_day) if o.client_order_id == coid] == []
    outcomes = _by_kind(book.due(date(2026, 10, 16)))
    assert outcomes[(coid, "realised_pnl")].through_session == exit_day
    assert outcomes[(coid, "realised_pnl")].value == pytest.approx(-50.0)


def test_written_outcomes_are_not_due_again() -> None:
    book = Book()
    coid = book.order("SEC_D", "buy", "expired", [(3.0, 10.0)])
    book.mark("SEC_D", T_NEXT, 11.0, 3.0)
    window = OutcomeWindow(window_id=1, written=frozenset({(coid, "position_return")}))
    assert [o.kind for o in book.due(DUE, window)] == ["not_executed"]


def test_an_order_with_an_implied_fill_uses_its_average_price() -> None:
    book = Book()
    book.order("SEC_A", "buy", "filled", [(6.0, 100.0), (4.0, 85.0)], implied_last=True)
    book.mark("SEC_A", T_NEXT, 104.0, 10.0)
    [outcome] = book.due(DUE)
    assert outcome.value == pytest.approx(104.0 / 94.0 - 1)  # (600 + 340) / 10 = 94


def test_realised_pnl_waits_when_the_lot_ledger_is_unavailable() -> None:
    book = Book()
    book.order("SEC_B", "buy", "filled", [(10.0, 50.0)], session=date(2026, 9, 1))
    coid = book.order("SEC_B", "sell", "filled", [(10.0, 60.0)])
    outcomes = due_outcomes(
        OutcomeWindow(window_id=1),
        book.orders,
        book.events,
        book.fills,
        book.marks,
        None,
        book.price_of,
        DUE,
    )
    assert (coid, "realised_pnl") not in _by_kind(outcomes)


# --- stopped windows: the earliest of the flattening fill, close(T_{i+1}) and
# --- the first stop run's mark


def test_stopped_window_horizon_is_close_s_minus_1_of_the_first_flat_stop_run() -> None:
    """A buy on a name never held, expired: the first `stop` run on S finds it
    flat and writes the row from close(S-1); close(S) is never read."""
    stop_run = date(2026, 10, 21)
    book = Book()
    coid = book.order("SEC_C", "buy", "expired")
    book.closes[("SEC_C", T_I)] = 20.0
    book.closes[("SEC_C", previous_session(stop_run))] = 21.0
    book.closes[("SEC_C", stop_run)] = 99.0  # not known before the open on S
    window = OutcomeWindow(
        window_id=1, stop_requested=date(2026, 10, 20), stop_flat={"SEC_C": stop_run}
    )

    assert book.due(previous_session(stop_run), window) == []
    [outcome] = book.due(stop_run, window)  # written by the stop run itself
    assert (outcome.client_order_id, outcome.through_session) == (coid, previous_session(stop_run))
    assert outcome.value == pytest.approx(0.05)
    assert outcome.mark_price == 21.0


def test_a_name_still_held_at_the_first_stop_run_is_not_due_then() -> None:
    stop_run = date(2026, 10, 21)
    book = Book()
    book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    window = OutcomeWindow(window_id=1, stop_requested=date(2026, 10, 20), stop_flat={})
    assert book.due(stop_run, window) == []


def test_stopped_window_horizon_is_the_flattening_fill_when_earlier() -> None:
    flatten = date(2026, 10, 20)
    book = Book()
    coid = book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.order("SEC_A", "sell", "filled", [(10.0, 105.0)], session=flatten, phase="exit")
    book.closes[("SEC_A", flatten)] = 106.0  # the close after the exit is not the mark
    window = OutcomeWindow(
        window_id=1, stop_requested=date(2026, 10, 19), stop_flat={"SEC_A": date(2026, 10, 22)}
    )

    outcomes = _by_kind(book.due(date(2026, 10, 21), window))

    buy = outcomes[(coid, "position_return")]
    assert buy.through_session == flatten
    assert (buy.value, buy.mark_price) == (pytest.approx(0.05), 105.0)


def test_stopped_window_horizon_is_close_of_the_next_rebalance_when_earlier() -> None:
    book = Book()
    coid = book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)
    window = OutcomeWindow(
        window_id=1, stop_requested=date(2026, 11, 3), stop_flat={"SEC_A": date(2026, 11, 4)}
    )
    [outcome] = book.due(DUE, window)
    assert (outcome.client_order_id, outcome.through_session) == (coid, T_NEXT)


# --- splits, calendar, look-ahead, equity ------------------------------------------


def _split(security_id: str, ex_date: date, ratio: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [security_id],
            "action_type": ["split"],
            "ex_date": [ex_date],
            "ratio_or_amount": [ratio],
        }
    )


def test_a_split_inside_the_horizon_adjusts_the_start_not_the_return() -> None:
    """10 bought at 100, a 2:1 split on 2026-10-15, marked at 55: +10%, and a
    1,100 - 1,000 = 100 dollar gain."""
    book = Book()
    book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.mark("SEC_A", T_NEXT, 55.0, 20.0)
    [outcome] = book.due(DUE, actions=_split("SEC_A", date(2026, 10, 15), 2.0))
    assert outcome.value == pytest.approx(0.10)
    assert outcome.contribution == pytest.approx(0.01)


def test_a_split_adjusts_the_not_executed_start_price() -> None:
    book = Book()
    book.order("SEC_C", "buy", "expired")
    book.closes[("SEC_C", T_I)] = 20.0
    book.closes[("SEC_C", T_NEXT)] = 11.0
    [outcome] = book.due(DUE, actions=_split("SEC_C", date(2026, 10, 15), 2.0))
    assert outcome.value == pytest.approx(0.10)


def test_the_decisions_rebalance_session_sets_the_horizon() -> None:
    """A re-attempt on 2026-11-02 for rebalance 2026-09-30 still ends at
    close(2026-10-30), which the order's session alone would put at 2026-11-30."""
    late = date(2026, 11, 2)
    book = Book()
    coid = book.order("SEC_C", "buy", "expired", session=late)
    decision = DecisionRow(
        decision_id=1,
        run_id=1,
        rebalance_session=T_I,
        security_id="SEC_C",
        whole_share=False,
        decision="trade",
        known_at=STAMP,
        ingested_at=STAMP,
    )
    [outcome] = book.due(date(2026, 11, 3), decisions=[decision])
    assert (outcome.client_order_id, outcome.through_session) == (coid, T_NEXT)
    assert book.due(date(2026, 11, 3)) == []  # without it: due after 2026-11-30


def test_a_january_order_rolls_the_year() -> None:
    book = Book()
    coid = book.order("SEC_C", "buy", "expired", session=date(2027, 1, 4))
    [outcome] = book.due(date(2027, 2, 1))
    assert (outcome.client_order_id, outcome.through_session) == (coid, date(2027, 1, 29))
    assert book.due(date(2027, 1, 29)) == []


def test_prices_after_the_horizon_change_nothing() -> None:
    book = Book()
    book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.order("SEC_C", "sell", "expired")
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)
    book.closes.update({("SEC_C", T_I): 20.0, ("SEC_C", T_NEXT): 21.0})
    before = book.due(DUE)
    book.mark("SEC_A", DUE, 500.0, 10.0)
    book.closes.update({("SEC_C", DUE): 500.0, ("SEC_A", DUE): 500.0})
    assert book.due(DUE) == before


def test_equity_without_a_cash_row_leaves_the_contribution_empty() -> None:
    book = Book()
    book.marks = []
    book.mark("SEC_Z", T_I, 10.0)
    book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)
    [outcome] = book.due(DUE)
    assert outcome.value == pytest.approx(0.10)
    assert outcome.contribution is None


# --- the writer ------------------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _journal(conn: duckdb.DuckDBPyConnection, book: Book, *, superseded: bool = False) -> None:
    append(
        conn,
        PaperWindowRow(
            window_id=1,
            hypothesis_id=1,
            first_rebalance_session=T_I,
            account_id="acct-1",
            starting_cash=10_000.0,
            starting_equity=10_000.0,
            code_version="t",
            started_at=STAMP,
            frozen_json="{}",
            frozen_sha256="0" * 64,
            known_at=STAMP,
            ingested_at=STAMP,
        ),
    )
    append(
        conn,
        PaperRunRow(
            run_id=1,
            window_id=1,
            started_at=STAMP,
            invoked_by="tty",
            code_version="t",
            known_at=STAMP,
            ingested_at=STAMP,
        ),
    )
    for row in (*book.orders, *book.events, *(f.fill for f in book.fills), *book.marks):
        append(conn, row)
    if superseded:
        synthetic = book.fills[-1].fill
        late = FillRow(
            client_order_id=synthetic.client_order_id,
            filled_at=synthetic.filled_at,
            quantity=synthetic.quantity,
            price=90.0,
            price_implied=False,
            broker_fill_id="bf-late",
            source="broker_feed",
            superseded_by=synthetic.fill_id,
            known_at=_utc(DUE, 20),
            ingested_at=_utc(DUE, 20),
        )
        append(conn, late)


def _clock() -> datetime:
    return _utc(DUE, 13)


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_the_writer_appends_outcomes_and_lots_once(conn: duckdb.DuckDBPyConnection) -> None:
    book = Book()
    book.order("SEC_A", "buy", "filled", [(6.0, 100.0), (4.0, 85.0)], implied_last=True)
    book.mark("SEC_A", T_NEXT, 104.0, 10.0)
    _journal(conn, book, superseded=True)
    errors: list[str] = []

    first = write_outcomes_and_lots(
        conn, 1, ACCOUNT, {}, book.price_of, DUE, _clock, on_lot_error=errors.append
    )
    second = write_outcomes_and_lots(
        conn, 1, ACCOUNT, {}, book.price_of, DUE, _clock, on_lot_error=errors.append
    )

    assert (first.outcomes, first.lot_rows, second.outcomes, second.lot_rows) == (1, 1, 0, 0)
    assert errors == []
    [row] = outcomes_for(conn, 1)
    assert row.value == pytest.approx(104.0 / 94.0 - 1)  # the superseded 90 is not counted
    assert (_count(conn, "lots"), _count(conn, "disposals"), _count(conn, "wash_sale_flags")) == (
        1,
        0,
        0,
    )
    lot = conn.execute("SELECT quantity, cost_basis, fill_id FROM lots").fetchone()
    assert lot == (pytest.approx(10.0), pytest.approx(940.0), None)


def _changed_ledger(conn: duckdb.DuckDBPyConnection) -> Book:
    book = Book()
    book.order("SEC_B", "buy", "filled", [(10.0, 50.0)], session=date(2026, 9, 1))
    book.order("SEC_B", "sell", "filled", [(10.0, 40.0)])
    _journal(conn, book)
    write_outcomes_and_lots(conn, 1, ACCOUNT, {}, book.price_of, F_I, _clock, on_lot_error=print)
    assert (_count(conn, "lots"), _count(conn, "disposals")) == (1, 1)
    later = Book()
    later.order("SEC_B", "buy", "filled", [(5.0, 41.0)], session=date(2026, 10, 5))
    for row in (*later.orders, *later.events, later.fills[0].fill):
        append(conn, row)
    return book


def test_a_changed_ledger_is_appended_as_a_new_set(conn: duckdb.DuckDBPyConnection) -> None:
    book = _changed_ledger(conn)

    result = write_outcomes_and_lots(
        conn,
        1,
        ACCOUNT,
        {},
        book.price_of,
        date(2026, 10, 6),
        lambda: _utc(DUE, 15),
        on_lot_error=print,
    )

    assert result.lot_rows == 2 + 1 + 1  # two lots, one disposal, one wash-sale flag
    latest = "(SELECT max(known_at) FROM lots)"
    assert _count(conn, f"lots WHERE known_at = {latest}") == 2
    assert _count(conn, f"disposals WHERE known_at = {latest}") == 1
    flag = conn.execute(
        "SELECT matched_quantity, disallowed_amount FROM wash_sale_flags"
    ).fetchone()
    assert flag == (pytest.approx(5.0), pytest.approx(50.0))


def test_a_changed_ledger_with_the_same_stamp_is_not_merged(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    book = _changed_ledger(conn)
    errors: list[str] = []

    result = write_outcomes_and_lots(
        conn, 1, ACCOUNT, {}, book.price_of, date(2026, 10, 6), _clock, on_lot_error=errors.append
    )

    assert result.lot_rows == 0
    assert len(errors) == 1 and "did not advance" in errors[0]
    assert (_count(conn, "lots"), _count(conn, "disposals")) == (1, 1)


def test_a_lot_ledger_error_alerts_and_the_outcomes_still_run(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A sale beyond the fill-built holding (a spin-off receipt sold, say):
    the writer reports it, writes no lot rows, holds back realised P&L and
    still writes the other outcomes."""
    book = Book()
    sell = book.order("SEC_KID", "sell", "filled", [(2.0, 5.0)])
    buy = book.order("SEC_A", "buy", "filled", [(10.0, 100.0)])
    book.mark("SEC_A", T_NEXT, 110.0, 10.0)
    _journal(conn, book)
    errors: list[str] = []

    result = write_outcomes_and_lots(
        conn, 1, ACCOUNT, {}, book.price_of, DUE, _clock, on_lot_error=errors.append
    )

    assert len(errors) == 1 and "more than the ledger holds" in errors[0]
    assert result.lot_error == errors[0]
    assert _count(conn, "lots") == 0
    assert {(r.client_order_id, r.kind) for r in outcomes_for(conn, 1)} == {
        (buy, "position_return")
    }
    assert sell

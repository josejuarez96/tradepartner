"""Holdings and what each is up or down since bought (plan T272, #2170).

Hand values are in each test's docstring. The pure `build_holdings` cases use
`Book` (one order and one fill per trade) and hand-written marks; the journal
cases go through `read_holdings` on the fixture store.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from itertools import count

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.execution import book_holdings
from tradepartner.execution.book_holdings import (
    NO_PRICE_NOTE,
    UNKNOWN_TICKER,
    HoldingRow,
    Holdings,
    build_holdings,
    read_holdings,
    ticker_map,
)
from tradepartner.execution.lots import SHORT
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    PositionDailyRow,
    append,
)
from tradepartner.store.schema import LONG

_STAMP = datetime(2030, 1, 1, tzinfo=UTC)
_fill_ids = count(1)

D0303 = date(2025, 3, 3)
D0304 = date(2025, 3, 4)
D0305 = date(2025, 3, 5)
D0306 = date(2025, 3, 6)
D0307 = date(2025, 3, 7)
D0310 = date(2025, 3, 10)
D0311 = date(2025, 3, 11)
D0312 = date(2025, 3, 12)

SYMBOLS = {"SEC_X": "XYZ", "SEC_Y": "YYY", "SEC_Z": "ZZZ"}


def _at(day: date) -> datetime:
    """A fill instant on `day`: 19:00 UTC is 14:00 or 15:00 in New York."""
    return datetime(day.year, day.month, day.day, 19, tzinfo=UTC)


class Book:
    """Orders and fills for one window, one order per trade."""

    def __init__(self) -> None:
        self.orders: list[OrderRow] = []
        self.fills: list[OrderedFill] = []

    def trade(
        self,
        side: str,
        quantity: float,
        price: float,
        day: date,
        *,
        security_id: str = "SEC_X",
        position_side: str = LONG,
    ) -> None:
        symbol = SYMBOLS[security_id]
        coid = f"o{len(self.orders) + 1}"
        opening = side == ("sell" if position_side == SHORT else "buy")
        self.orders.append(
            OrderRow(
                client_order_id=coid,
                decision_id=1,
                run_id=1,
                session=day,
                attempt=1,
                phase="buy" if opening else "sell",
                security_id=security_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                position_side=position_side,
                sells_in_flight_at_submit=False,
                known_at=_STAMP,
                ingested_at=_STAMP,
            )
        )
        fill_id = next(_fill_ids)
        row = FillRow(
            fill_id=fill_id,
            client_order_id=coid,
            filled_at=_at(day),
            quantity=quantity,
            price=price,
            price_implied=False,
            broker_fill_id=f"bf-{fill_id}",
            source="broker_feed",
            known_at=_STAMP,
            ingested_at=_STAMP,
        )
        self.fills.append(OrderedFill(row, side, security_id, symbol, 1, 1))

    def holdings(self, marks: list[PositionDailyRow], as_of: date, **kwargs: object) -> Holdings:
        return build_holdings(self.fills, self.orders, [], marks, as_of, **kwargs)  # type: ignore[arg-type]


def _mark(
    session: date,
    security_id: str | None,
    quantity: float,
    price: float | None,
    *,
    cash: float = 1000.0,
    side: str = LONG,
) -> PositionDailyRow:
    return PositionDailyRow(
        run_id=1,
        session=session,
        security_id=security_id,
        quantity=quantity,
        mark_price=price,
        value=None if price is None else quantity * price,
        cash=cash,
        position_side=side,
        known_at=_STAMP,
        ingested_at=_STAMP,
    )


def _splits(*rows: tuple[str, date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "action_type": ["split"] * len(rows),
            "ex_date": [r[1] for r in rows],
            "ratio_or_amount": [r[2] for r in rows],
        },
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
        },
    )


def _one(holdings: Holdings, ticker: str) -> HoldingRow:
    (row,) = [r for r in holdings.rows if r.ticker == ticker]
    return row


def test_two_lots_at_different_prices_give_the_summed_basis_and_gain() -> None:
    """20 @ 100 (basis 2,000) and 30 @ 110 (basis 3,300): 50 shares, basis
    5,300. Marked at 116: value 5,800, gain 500.00, 500 / 5,300 = 9.43%."""
    book = Book()
    book.trade("buy", 20, 100.0, D0303)
    book.trade("buy", 30, 110.0, D0304)
    out = book.holdings([_mark(D0305, "SEC_X", 50, 116.0)], D0305)
    row = _one(out, "XYZ")
    assert (row.side, row.quantity, row.cost_basis, row.value) == (LONG, 50, 5300.0, 5800.0)
    assert row.gain == pytest.approx(500.0)
    assert row.gain_percent == pytest.approx(9.43, abs=0.005)
    assert out.session == D0305
    assert out.summary.cash == 1000.0
    assert out.summary.count == 1


def test_a_partial_sale_closes_the_oldest_lot_first() -> None:
    """20 @ 100, 30 @ 110, sell 25: FIFO closes lot 1 (2,000) and 5 of lot 2
    (basis 110 x 5 = 550). Open: 25 shares, basis 3,300 - 550 = 2,750."""
    book = Book()
    book.trade("buy", 20, 100.0, D0303)
    book.trade("buy", 30, 110.0, D0304)
    book.trade("sell", 25, 120.0, D0305)
    row = _one(book.holdings([_mark(D0306, "SEC_X", 25, 120.0)], D0306), "XYZ")
    assert row.cost_basis == pytest.approx(2750.0)
    assert row.gain == pytest.approx(3000.0 - 2750.0)


def test_a_split_between_the_buy_and_the_mark_reads_the_adjusted_lot() -> None:
    """10 @ 100 (basis 1,000) on 03-03, 2:1 split ex 03-07: 20 shares, basis
    still 1,000. Marked 03-12 at 55: value 1,100, gain 100, 10%. The raw fill
    (10 shares at 100) would give a value of 550 against 1,000."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    out = book.holdings(
        [_mark(D0312, "SEC_X", 20, 55.0)], D0312, actions=_splits(("SEC_X", D0307, 2.0))
    )
    row = _one(out, "XYZ")
    assert (row.quantity, row.cost_basis, row.value) == (20, 1000.0, 1100.0)
    assert row.gain == pytest.approx(100.0)
    assert row.gain_percent == pytest.approx(10.0)


def test_a_short_is_labelled_and_its_gain_is_basis_minus_value() -> None:
    """Short 10 @ 100: proceeds (basis) 1,000. Marked at 90: value 900, gain
    1,000 - 900 = +100, 10%. Today's change against a prior mark of 95: the
    price fell 5 on 10 shares, +50 for the short."""
    book = Book()
    book.trade("sell", 10, 100.0, D0303, position_side=SHORT)
    marks = [
        _mark(D0304, "SEC_X", 10, 95.0, side=SHORT),
        _mark(D0305, "SEC_X", 10, 90.0, side=SHORT),
    ]
    row = _one(book.holdings(marks, D0305), "XYZ")
    assert row.side == "short"
    assert (row.cost_basis, row.value) == (1000.0, 900.0)
    assert row.gain == pytest.approx(100.0)
    assert row.gain_percent == pytest.approx(10.0)
    assert row.change_today == pytest.approx(50.0)
    assert row.change_today_percent == pytest.approx(5 / 95 * 100)


def test_a_mark_with_no_price_is_flagged_and_left_out_of_totals_and_ranking() -> None:
    """XYZ is priced; YYY has no price. YYY shows "no price yet", has no
    value, gain or change (never 0), and is in no count or ranking, but is
    counted as a holding."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("buy", 5, 50.0, D0303, security_id="SEC_Y")
    marks = [
        _mark(D0304, "SEC_X", 10, 100.0),
        _mark(D0304, "SEC_Y", 5, 50.0),
        _mark(D0305, "SEC_X", 10, 110.0),
        _mark(D0305, "SEC_Y", 5, None),
    ]
    out = book.holdings(marks, D0305)
    bare = _one(out, "YYY")
    assert bare.note == NO_PRICE_NOTE
    assert (bare.value, bare.gain, bare.gain_percent, bare.change_today) == (None, None, None, None)
    assert bare.cost_basis == 250.0
    assert out.summary.count == 2
    assert out.summary.up_today == 1
    assert out.summary.down_today == 0
    assert [r.ticker for r in out.summary.largest] == ["XYZ"]
    assert [r.ticker for r in out.summary.best] == ["XYZ"]
    assert out.rows[-1].ticker == "YYY"


def test_a_lot_opened_after_the_as_of_session_is_not_counted() -> None:
    """10 @ 100 on 03-03; 5 @ 120 on 03-10. As of 03-07 the book holds 10 and
    its basis is 1,000, though the journal (and a later mark) already hold the
    03-10 buy. A fill on the as-of session itself, after its close, is out too."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("buy", 5, 120.0, D0310)
    marks = [_mark(D0307, "SEC_X", 10, 105.0), _mark(D0310, "SEC_X", 15, 110.0)]
    early = _one(book.holdings(marks, D0307), "XYZ")
    assert (early.quantity, early.cost_basis) == (10, 1000.0)
    late = _one(book.holdings(marks, D0310), "XYZ")
    assert (late.quantity, late.cost_basis) == (15, 1600.0)
    after_close = Book()
    after_close.trade("buy", 10, 100.0, D0303)
    after_close.trade("buy", 5, 120.0, D0307)
    row = after_close.fills[-1].fill
    object.__setattr__(row, "filled_at", session_close(D0307) + timedelta(minutes=1))
    assert (
        _one(after_close.holdings([_mark(D0307, "SEC_X", 10, 105.0)], D0307), "XYZ").cost_basis
        == 1000.0
    )


def test_change_today_up_down_counts_and_top_n() -> None:
    """Prior 03-04 then 03-05 marks: XYZ 100 -> 110 (+10 x 10 = +100, +10%),
    YYY 50 -> 45 (-5 x 10 = -50), ZZZ 20 -> 20 (flat, neither up nor down).
    Gains since bought: XYZ +200 (bought 90), YYY -100 (bought 55), ZZZ +0.
    With top_n=1 each ranking has one row."""
    book = Book()
    book.trade("buy", 10, 90.0, D0303)
    book.trade("buy", 10, 55.0, D0303, security_id="SEC_Y")
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_Z")
    marks = [
        _mark(D0304, "SEC_X", 10, 100.0),
        _mark(D0304, "SEC_Y", 10, 50.0),
        _mark(D0304, "SEC_Z", 10, 20.0),
        _mark(D0305, "SEC_X", 10, 110.0),
        _mark(D0305, "SEC_Y", 10, 45.0),
        _mark(D0305, "SEC_Z", 10, 20.0),
    ]
    out = book.holdings(marks, D0305, top_n=1)
    x = _one(out, "XYZ")
    assert x.change_today == pytest.approx(100.0)
    assert x.change_today_percent == pytest.approx(10.0)
    assert _one(out, "YYY").change_today == pytest.approx(-50.0)
    assert _one(out, "ZZZ").change_today == 0.0
    s = out.summary
    assert (s.count, s.up_today, s.down_today) == (3, 1, 1)
    assert [r.ticker for r in s.largest] == ["XYZ"]
    assert [r.ticker for r in s.best] == ["XYZ"]
    assert [r.ticker for r in s.worst] == ["YYY"]
    assert [r.ticker for r in book.holdings(marks, D0305).summary.largest] == ["XYZ", "YYY", "ZZZ"]
    assert [r.ticker for r in out.rows] == ["XYZ", "YYY", "ZZZ"]  # by value, 1100 > 450 > 200


def test_a_split_on_the_ex_date_is_not_a_crash_in_change_today() -> None:
    """10 @ 100, 2:1 split ex 03-07. Prior mark 03-06: 110 a share (10 shares);
    03-07: 55.5 (20 shares). The prior price in today's units is 55, so the
    change is 20 x 0.5 = +10 (+0.909%), not -1,090."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    marks = [_mark(D0306, "SEC_X", 10, 110.0), _mark(D0307, "SEC_X", 20, 55.5)]
    out = book.holdings(marks, D0307, actions=_splits(("SEC_X", D0307, 2.0)))
    row = _one(out, "XYZ")
    assert row.change_today == pytest.approx(10.0)
    assert row.change_today_percent == pytest.approx(0.5 / 55 * 100)
    assert (out.summary.up_today, out.summary.down_today) == (1, 0)


def test_a_name_bought_today_has_no_change_today() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, D0305)
    out = book.holdings([_mark(D0304, None, 0.0, None), _mark(D0305, "SEC_X", 10, 101.0)], D0305)
    row = _one(out, "XYZ")
    assert (row.change_today, row.change_today_percent) == (None, None)
    assert out.summary.up_today == out.summary.down_today == 0


def test_a_book_with_no_mark_is_empty_and_a_flat_book_has_no_rows() -> None:
    book = Book()
    assert book.holdings([], D0305).session is None
    flat = book.holdings([_mark(D0305, None, 0.0, None, cash=500.0)], D0305)
    assert flat.rows == ()
    assert (flat.summary.count, flat.summary.cash) == (0, 500.0)


def test_marks_after_the_as_of_session_are_ignored() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    marks = [_mark(D0304, "SEC_X", 10, 101.0), _mark(D0306, "SEC_X", 10, 150.0)]
    assert _one(book.holdings(marks, D0305), "XYZ").value == 1010.0


def test_the_security_id_is_never_shown() -> None:
    """A name with no order and no listing reads "unknown", never its id;
    a listing gives the broker form."""
    out = build_holdings([], [], [], [_mark(D0305, "SEC_Q", 1, 10.0)], D0305)
    assert out.rows[0].ticker == UNKNOWN_TICKER
    tickers = ticker_map([], {"SEC_B": {"ticker": "BFB", "exchange": "NYSE"}}, {"BFB": "BF.B"})
    assert tickers == {"SEC_B": "BF.B"}
    assert book_holdings.UNKNOWN_TICKER not in tickers.values()


def test_top_n_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="top_n"):
        build_holdings([], [], [], [], D0305, top_n=0)


# --- through the journal ------------------------------------------------------------


class _CountingConnection:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.queries = 0

    def execute(self, sql: str, params: object = None) -> duckdb.DuckDBPyConnection:
        self.queries += 1
        return self._conn.execute(sql) if params is None else self._conn.execute(sql, params)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def _seed(settings: Settings, window_id: int, names: range) -> None:
    """The numbered `names`, each bought twice (10 @ 100 and 10 @ 110) before 10-01
    and marked at 120 on 10-01 and 121 on 10-02, plus one cash-only mark."""
    first, second = date(2026, 9, 30), date(2026, 10, 1)
    start = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
    stamp = {"known_at": start, "ingested_at": start}
    with open_for_write(settings) as conn:
        run_id = append(
            conn,
            PaperRunRow(
                window_id=window_id,
                session=first,
                kind="rebalance",
                started_at=start,
                invoked_by="scheduler",
                code_version="t",
                **stamp,
            ),
        )
        assert run_id is not None
        decision_id = append(
            conn,
            DecisionRow(
                run_id=run_id,
                security_id="S0",
                whole_share=True,
                decision="trade",
                **stamp,
            ),
        )
        assert decision_id is not None
        for i in names:
            sid = f"SEC{i}"
            for k, price in enumerate((100.0, 110.0)):
                coid = f"c{i}-{k}"
                append(
                    conn,
                    OrderRow(
                        client_order_id=coid,
                        decision_id=decision_id,
                        run_id=run_id,
                        session=first,
                        attempt=1,
                        phase="buy",
                        security_id=sid,
                        symbol=f"T{i}",
                        side="buy",
                        quantity=10.0,
                        sells_in_flight_at_submit=False,
                        **stamp,
                    ),
                )
                append(
                    conn,
                    FillRow(
                        client_order_id=coid,
                        filled_at=start + timedelta(minutes=k),
                        quantity=10.0,
                        price=price,
                        price_implied=False,
                        broker_fill_id=f"bf-{coid}",
                        source="broker_feed",
                        **stamp,
                    ),
                )
            for session, price in ((first, 119.0), (second, 120.0)):
                append(
                    conn,
                    PositionDailyRow(
                        run_id=run_id,
                        session=session,
                        security_id=sid,
                        quantity=20.0,
                        mark_price=price,
                        value=20.0 * price,
                        cash=5000.0,
                        **stamp,
                    ),
                )


def test_read_holdings_reads_the_journal_once_for_any_number_of_names(
    journal_settings: Settings, open_window: object
) -> None:
    """Query count is the same for 2 names and for 60: no per-name query. Each
    name: basis 2,100, value 2,400, gain 300, 14.29%."""
    window_id = open_window.window_id  # type: ignore[attr-defined]
    assert window_id is not None
    counts = []
    for names in (range(2), range(2, 60)):
        _seed(journal_settings, window_id, names)
        with open_read_only(journal_settings) as conn:
            counting = _CountingConnection(conn)
            out = read_holdings(counting, window_id, date(2026, 10, 1))  # type: ignore[arg-type]
        counts.append(counting.queries)
        row = out.rows[0]
        assert (row.cost_basis, row.value) == (2100.0, 2400.0)
        assert row.gain_percent == pytest.approx(300 / 2100 * 100)
        assert out.summary.cash == 5000.0
        assert out.summary.count == names.stop
    assert counts[0] == counts[1]


def test_read_holdings_on_the_full_window_fixture_is_within_the_timing_bound(
    journal_settings: Settings, open_window: object
) -> None:
    window_id = open_window.window_id  # type: ignore[attr-defined]
    _seed(journal_settings, window_id, range(150))
    fast = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path, "lock_retry_seconds": 2},
    )
    with open_read_only(fast) as conn:
        started = time.monotonic()
        out = read_holdings(conn, window_id, date(2026, 10, 2))
        elapsed = time.monotonic() - started
    assert out.summary.count == 150
    assert elapsed < fast.store.lock_retry_seconds / 2
